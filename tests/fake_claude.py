"""A scripted stand-in for `claude -p` in stream-json mode, for tests/test_claude_code.py; no network, no model.

Usage: fake_claude.py SCRIPT LOG [claude's arguments]. SCRIPT is a JSON file {"next": i, "responses": [...]}, shared
by every process of a test (each response taken advances "next"). A response is one of:
  {"blocks": [...], "stop": "tool_use" | "end_turn" | ..., "usage": [input, cache_write, cache_read, output]}
      blocks: {"type": "text", "text"} | {"type": "thinking", "thinking"} | {"type": "tool_use", "name", "input"}
      a tool_use with "unserved": true is named without the prefix and answered with an error, as Claude Code does;
      a response with "continue": true and a stop other than tool_use is followed by the next in the same turn
  {"error": text, "limit": resets_at or null}   the turn ends in an error result (with a rejected rate limit)
  {"hang": true}                                 no output any more
A SCRIPT with "deaf": true makes the process never read its input.
LOG gets one JSON line per thing seen: the launch, each user message, the tool server's tools, each tool result.
Like Claude Code, it starts the tool server of --mcp-config, sends a model's tool calls to it and waits for their
results before the next response.
"""

import json
import os
import subprocess
import sys
import time

SCRIPT, LOG, ARGS = sys.argv[1], sys.argv[2], sys.argv[3:]
PREFIX = "mcp__market__"


def log(**entry):
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def take():
    with open(SCRIPT) as f:
        script = json.load(f)
    response = script["responses"][script["next"]]
    script["next"] += 1
    with open(SCRIPT, "w") as f:
        json.dump(script, f)
    return response


def out(**event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def arg(name):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else None


class Server:
    def __init__(self, config):
        spec = json.loads(config)["mcpServers"]["market"]
        self.proc = subprocess.Popen([spec["command"], *spec["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True)
        self.n = 0
        self.request("initialize", {"protocolVersion": "2025-06-18"})
        log(tools=self.request("tools/list", {})["tools"])

    def request(self, method, params):
        self.n += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        while True:
            message = json.loads(self.proc.stdout.readline())
            if message.get("id") == self.n:
                return message["result"]


def respond(response, n):
    """Stream one response's events; its tool calls, with their ids."""
    usage = response.get("usage", [10, 0, 0, 5])
    out(type="stream_event", event={"type": "message_start", "message": {"id": f"msg_{n}", "usage": {
        "input_tokens": usage[0], "cache_creation_input_tokens": usage[1], "cache_read_input_tokens": usage[2],
        "output_tokens": 1}}})
    calls = []
    for i, block in enumerate(response["blocks"]):
        if block["type"] == "tool_use":
            use_id = f"toolu_{n}_{i}"
            calls.append((use_id, block))
            name = block["name"] if block.get("unserved") else PREFIX + block["name"]
            out(type="stream_event", event={"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": use_id, "name": name, "input": {}}})
            text = json.dumps(block["input"])
            for part in (text[:3], text[3:]):
                out(type="stream_event", event={"type": "content_block_delta", "index": i,
                                                "delta": {"type": "input_json_delta", "partial_json": part}})
        else:
            kind = block["type"]
            out(type="stream_event", event={"type": "content_block_start", "index": i,
                                            "content_block": {"type": kind, kind: ""}})
            delta = "text_delta" if kind == "text" else "thinking_delta"
            out(type="stream_event", event={"type": "content_block_delta", "index": i,
                                            "delta": {"type": delta, kind: block[kind]}})
        out(type="stream_event", event={"type": "content_block_stop", "index": i})
        out(type="assistant", message={"id": f"msg_{n}", "content": [block]})  # ignored by the adapter
    out(type="stream_event", event={"type": "message_delta", "delta": {"stop_reason": response["stop"]},
                                    "usage": {"output_tokens": usage[3]}})
    out(type="stream_event", event={"type": "message_stop"})
    return calls


def main():
    log(launch={"args": ARGS, "env": dict(os.environ), "cwd": os.getcwd(),
                "home": os.listdir(os.environ["HOME"]), "config": os.listdir(os.environ["CLAUDE_CONFIG_DIR"])})
    server = Server(arg("--mcp-config")) if "--mcp-config" in ARGS else None
    out(type="system", subtype="init")
    with open(SCRIPT) as f:
        if json.load(f).get("deaf"):  # a process that never reads its input
            time.sleep(3600)
    n = 0
    for line in sys.stdin:
        log(user=json.loads(line)["message"]["content"])
        while True:
            response = take()
            n += 1
            if response.get("hang"):
                time.sleep(3600)
            if "error" in response:
                if response.get("limit"):
                    out(type="rate_limit_event", rate_limit_info={"status": "rejected",
                                                                  "resetsAt": response["limit"]})
                out(type="assistant", message={"content": [{"type": "text", "text": response["error"]}]})
                out(type="result", subtype="success", is_error=True, result=response["error"])
                break
            calls = respond(response, n)
            if response["stop"] != "tool_use":
                if response.get("continue"):  # Claude Code goes on by itself (e.g. after an output limit)
                    continue
                out(type="result", subtype="success", is_error=False, result="")
                break
            for use_id, block in calls:
                if block.get("unserved"):  # Claude Code answers it with an error itself
                    log(tool_error=use_id)
                    continue
                result = server.request("tools/call", {"name": block["name"], "arguments": block["input"],
                                                       "_meta": {"claudecode/toolUseId": use_id}})
                log(tool_result={use_id: result["content"][0]["text"]})


main()
