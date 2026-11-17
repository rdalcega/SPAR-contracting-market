"""A scripted stand-in for `codex` (`debug models` and `app-server`), for tests/test_codex.py; no network, no model.

Usage: fake_codex.py SCRIPT LOG [codex's arguments]. SCRIPT is a JSON file {"next": i, "responses": [...]} shared by
every process of a test (each response taken advances "next"), with optional keys "catalog" (what `debug models`
prints), "sources" (thread/start's instructionSources), "refresh" (ask the client for a token before the first
response) and "child": "plain" or "stubborn" (a long-running child, one that ignores SIGTERM: closing must stop the
process group), or "deaf" (after the login, stop reading and wait on a child that inherits stdin and never reads).
A response is one of:
  {"blocks": [...], "usage": [input, output(, reasoning)]}   one model response; it waits for answers when it has
      served calls
      blocks: {"type": "text", "text"} | {"type": "thinking", "thinking"} | {"type": "tool_use", "name", "input"}
              | {"type": "raw", "item"} (an output item of another kind, which Codex handles itself and goes on)
      a tool_use with "unserved": true is a call of a function Codex does not have: answered with an error by Codex
  {"error": text, "limit": resets_at or null}   the turn fails (on a reached subscription limit)
  {"hang": true}                                 no output any more
LOG gets one JSON line per thing seen: the launch, each request's params, each answer to a tool call.
"""

import json
import os
import subprocess
import sys
import time

SCRIPT, LOG, ARGS = sys.argv[1], sys.argv[2], sys.argv[3:]


def log(**entry):
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def script():
    with open(SCRIPT) as f:
        return json.load(f)


def take():
    s = script()
    response = s["responses"][s["next"]]
    s["next"] += 1
    with open(SCRIPT, "w") as f:
        json.dump(s, f)
    return response


def out(**message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


CATALOG = {"models": [{"slug": "gpt-test", "tool_mode": "code_mode_only", "apply_patch_tool_type": "freeform",
                       "supports_search_tool": True, "multi_agent_version": "v1",
                       "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}]}]}


class Server:
    def __init__(self):
        self.n = 1000
        self.answers = {}
        self.thread = None
        self.tools = []

    def read(self):
        """The next client message; requests other than answers are handled, answers kept."""
        line = sys.stdin.readline()
        if not line:
            sys.exit(0)
        message = json.loads(line)
        if "method" not in message:  # an answer to one of our requests
            self.answers[message["id"]] = message
            return None
        return message

    def ask(self, method, params):
        self.n += 1
        out(id=self.n, method=method, params=params)
        while self.n not in self.answers:
            message = self.read()
            if message is not None:
                self.handle(message)
        return self.answers.pop(self.n)

    def handle(self, message):
        method, params, rid = message["method"], message.get("params") or {}, message.get("id")
        log(request={"method": method, "params": params})
        if method == "initialize":
            out(id=rid, result={"userAgent": "fake", "codexHome": os.environ.get("CODEX_HOME")})
        elif method == "initialized":
            pass
        elif method == "account/login/start":
            out(id=rid, result={"type": "chatgptAuthTokens"})
            if script().get("child") == "deaf":  # like the npm launcher: a child inherits stdin; no one reads it
                process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
                log(child=process.pid)
                process.wait()
        elif method == "thread/start":
            self.thread, self.tools = "thread-1", [t["name"] for t in params.get("dynamicTools") or []]
            out(id=rid, result={"thread": {"id": self.thread}, "instructionSources": script().get("sources", [])})
        elif method == "turn/start":
            out(id=rid, result={"turn": {"id": "turn"}})
            self.turn()
        else:
            out(id=rid, error={"code": -32601, "message": "unknown"})

    def turn(self):
        if script().get("refresh"):
            answer = self.ask("account/chatgptAuthTokens/refresh", {"reason": "unauthorized"})
            log(refreshed=answer.get("result"))
        n = 0
        while True:
            response = take()
            n += 1
            if response.get("hang"):
                time.sleep(3600)
            if "error" in response:
                if response.get("limit"):
                    out(method="account/rateLimits/updated", params={"rateLimits": {"primary": {
                        "usedPercent": 100, "resetsAt": response["limit"]}, "secondary": None}})
                info = "usageLimitExceeded" if response.get("limit") else "other"
                out(method="turn/completed", params={"turn": {"status": "failed", "error": {
                    "message": response["error"], "codexErrorInfo": info}}})
                return
            served = []
            for i, block in enumerate(response["blocks"]):
                kind = block["type"]
                if kind == "text":
                    item = {"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": block["text"]}]}
                elif kind == "thinking":
                    item = {"type": "reasoning", "summary": [{"type": "summary_text", "text": block["thinking"]}]}
                elif kind == "raw":  # another kind of output, which Codex handles itself and goes on
                    item = block["item"]
                else:
                    call_id = f"call_{n}_{i}"
                    item = {"type": "function_call", "name": block["name"], "call_id": call_id,
                            "arguments": json.dumps(block["input"])}
                    if not block.get("unserved"):
                        served.append((call_id, block))
                out(method="rawResponseItem/completed", params={"item": item})
            requests = []
            for call_id, block in served:
                self.n += 1
                requests.append((self.n, call_id))
                out(id=self.n, method="item/tool/call", params={"callId": call_id, "tool": block["name"],
                                                               "arguments": block["input"]})
            usage = response.get("usage", [10, 5]) + [0]
            out(method="rawResponse/completed", params={"usage": {
                "inputTokens": usage[0], "outputTokens": usage[1], "reasoningOutputTokens": usage[2]}})
            calls = [b for b in response["blocks"] if b["type"] in ("tool_use", "raw")]
            if not calls:
                out(method="turn/completed", params={"turn": {"status": "completed", "error": None}})
                return
            for rid, call_id in requests:
                while rid not in self.answers:
                    message = self.read()
                    if message is not None:
                        self.handle(message)
                answer = self.answers.pop(rid)
                log(tool_result={call_id: answer["result"]["contentItems"][0]["text"]})


def main():
    if ARGS[:2] == ["debug", "models"]:
        print(json.dumps(script().get("catalog", CATALOG)))
        return
    log(launch={"args": ARGS, "env": dict(os.environ), "cwd": os.getcwd(), "home": os.listdir(os.environ["HOME"])})
    child = script().get("child")
    if child and child != "deaf":  # a long-running child; "stubborn": one that ignores SIGTERM
        code = "import signal, time\nif %r: signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(3600)" % (
            child == "stubborn")
        log(child=subprocess.Popen([sys.executable, "-c", code]).pid)
    server = Server()
    out(method="remoteControl/status/changed", params={})
    while True:
        message = server.read()
        if message is not None:
            server.handle(message)


main()
