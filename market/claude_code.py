"""Provider claude_code: model calls answered by Claude Code (`claude -p`) on a Claude subscription. The key is a
Claude Code OAuth token (`claude setup-token`) in CLAUDE_CODE_OAUTH_TOKEN. How a conversation maps onto a session,
and what any harness provider does differently: market/live.py.

Each session is one `claude -p` process in stream-json mode. The market's functions are the session's only tools,
served by market/claude_tools.py, whose calls wait for the market's answers.

Isolation: each session has its own empty home, configuration and working folders, an environment holding only PATH,
the token and the switches of _SWITCHES, no built-in tools, no settings, CLAUDE.md, memory, hooks, plugins or MCP
servers but the market's, and no saved session. With an empty configuration Claude Code knows no account, so the
user's email is not sent.

What the model still sees differently (Claude Code adds it, with no switch that keeps subscription login):
- the system prompt starts with Claude Code's line "You are a Claude agent, built on Anthropic's Claude Agent SDK.";
- a reminder before the view carries today's date, and a system message after it the session's working folder, the
  platform, the model's name and Claude Code's note on untrusted files;
- the functions are named mcp__market__<name> (the replies give the market its own names back);
- reasoning is the summary of its thinking that Claude returns (display "summarized"), never the raw thinking;
- input tokens count cache reads and writes as plain input, at the spec's input price.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

from . import live
from .models import SetupError

CLI = "claude"
TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"  # the variable Claude Code reads its subscription token from
PREFIX = "mcp__market__"  # Claude Code's name for a function of the tool server "market"
NOT_RUN = live.NOT_RUN
_STUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_tools.py")
_STOPS = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length",
          "refusal": "content_filter"}  # models.md 2.2, "finish reason"
_SWITCHES = {
    "ENABLE_TOOL_SEARCH": "false",  # no deferred-tool placeholder among the tools
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1",
    "DISABLE_AUTOUPDATER": "1",
}


def backend(spec, llm, tracker, key, *, cli=None, sleep=time.sleep, clock=time.time):
    """models.md 3, "Setting up": the backend of a claude_code spec, or SetupError when Claude Code is missing."""
    cli = cli or CLI
    if shutil.which(cli) is None:
        raise SetupError(f"provider claude_code needs Claude Code's `{cli}` command on PATH")
    return _Backend(spec, llm, tracker, key, cli, sleep, clock)


def _tokens_in(usage):
    """The input tokens: cache writes and reads count as input (module docstring)."""
    return sum(live.count(usage.get(k)) for k in ("input_tokens", "cache_creation_input_tokens",
                                                   "cache_read_input_tokens"))


class _Session(live.Session):
    """One `claude -p` process."""

    def launch(self, system, functions, deadline):
        owner = self.owner
        self.proc, self.server, self.conn, self.connected = None, None, None, threading.Event()
        short = len(tempfile.gettempdir()) > 60 and os.path.isdir("/tmp")  # a socket path fits in 104 bytes
        self.dir = tempfile.mkdtemp(prefix="cc-", dir="/tmp" if short else None)
        home, config, work = (os.path.join(self.dir, d) for d in ("home", "config", "work"))
        for d in (home, config, work):
            os.mkdir(d)
        system_file = os.path.join(self.dir, "system.txt")
        with open(system_file, "w", encoding="utf-8") as f:
            f.write(system)
        args = [owner.cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--model", owner.model, "--system-prompt-file", system_file,
                "--tools", "", "--strict-mcp-config", "--restricted", "--no-session-persistence",
                "--thinking-display", "summarized"]  # a hidden flag: without it Claude 5 sends empty thinking
        if owner.effort:
            args += ["--effort", owner.effort]
        if functions:
            tools = [{"name": f["function"]["name"], "description": f["function"].get("description", ""),
                      "inputSchema": f["function"].get("parameters") or {"type": "object", "properties": {}}}
                     for f in functions]
            tools_file, address = os.path.join(self.dir, "tools.json"), os.path.join(self.dir, "s")
            with open(tools_file, "w", encoding="utf-8") as f:
                json.dump(tools, f)
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server.bind(address)
            self.server.listen(1)
            threading.Thread(target=self._accept, daemon=True).start()
            mcp = {"mcpServers": {"market": {"command": sys.executable, "args": ["-I", _STUB, tools_file, address]}}}
            args += ["--mcp-config", json.dumps(mcp), "--allowedTools", ",".join(PREFIX + t["name"] for t in tools)]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": home, "CLAUDE_CONFIG_DIR": config, TOKEN: owner.key,
               "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(owner.max_tokens), **_SWITCHES}
        self.stderr = os.path.join(self.dir, "stderr.txt")
        try:
            with open(self.stderr, "w") as err:
                self.proc = subprocess.Popen(args, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=err, text=True, encoding="utf-8", start_new_session=True)
        except OSError as e:
            raise live.Lost(f"Claude Code could not be started: {e}") from None
        threading.Thread(target=self.pump, daemon=True).start()

    def _accept(self):
        try:
            self.conn, _ = self.server.accept()
        except OSError:
            return
        self.connected.set()

    def _next(self, deadline):
        event = self.next_event(deadline)
        if event.get("type") == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            if info.get("status") == "rejected" and isinstance(info.get("resetsAt"), (int, float)):
                self.until = info["resetsAt"]
        return event

    def send_user(self, text, deadline):
        line = json.dumps({"type": "user", "message": {"role": "user", "content": text}})
        live.write_within(self.proc, line, deadline, self.clock, self.owner.timeout, self.ended_text)

    def send_answers(self, answers, deadline):
        """The market's answers by tool-use id, to the tool server."""
        if not self.connected.wait(max(0.0, deadline - self.clock())):
            raise live.Lost("the market's tool server did not connect")
        try:
            self.conn.settimeout(max(0.001, deadline - self.clock()))
            for use_id, text in answers.items():
                self.conn.sendall((json.dumps({"id": use_id, "text": text}) + "\n").encode("utf-8"))
        except OSError as e:
            raise live.Lost(f"the market's tool server is gone: {e}") from None

    def response(self, deadline):
        """The next model response Claude Code completes, from its stream events; (None, the result's text) when
        its turn ends first."""
        blocks, usage, stop = {}, {}, None
        while True:
            event = self._next(deadline)
            kind = event.get("type")
            if kind == "result":
                return None, event.get("result") or event.get("subtype")
            if kind != "stream_event":
                continue
            e = event.get("event") or {}
            k = e.get("type")
            if k == "message_start":
                blocks, usage, stop = {}, dict((e.get("message") or {}).get("usage") or {}), None
            elif k == "content_block_start":
                blocks[e.get("index")] = {"json": "", **(e.get("content_block") or {})}
            elif k == "content_block_delta" and e.get("index") in blocks:
                block, delta = blocks[e["index"]], e.get("delta") or {}
                for field, key in (("text", "text"), ("thinking", "thinking"), ("json", "partial_json")):
                    if isinstance(delta.get(key), str):
                        block[field] = block.get(field, "") + delta[key]
            elif k == "message_delta":
                stop = (e.get("delta") or {}).get("stop_reason")
                usage.update({key: v for key, v in (e.get("usage") or {}).items() if v is not None})
            elif k == "message_stop":
                order = [blocks[i] for i in sorted(blocks, key=lambda i: i if isinstance(i, int) else -1)]
                return self._response(order, usage, stop), None

    @staticmethod
    def _response(blocks, usage, stop):
        calls = [{"id": b.get("id"), "name": str(b.get("name", "")).removeprefix(PREFIX),
                  "arguments": b["json"] or json.dumps(b.get("input") or {}),
                  "served": str(b.get("name", "")).startswith(PREFIX)}
                 for b in blocks if b.get("type") == "tool_use"]
        return live.Response(
            calls=calls, text="".join(b.get("text", "") for b in blocks if b.get("type") == "text"),
            thinking="".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking"),
            tokens_in=_tokens_in(usage), tokens_out=live.count(usage.get("output_tokens")),
            finish=_STOPS.get(stop, stop), waits=stop == "tool_use")

    def close(self):
        if self.proc is not None:
            live.stop_process(self.proc)
        for s in (getattr(self, "conn", None), getattr(self, "server", None)):
            if s is not None:
                s.close()
        if getattr(self, "dir", None):
            shutil.rmtree(self.dir, ignore_errors=True)


class _Backend(live.Backend):
    provider = "claude_code"
    harness = "Claude Code"
    session = _Session

    def __init__(self, spec, llm, tracker, key, cli, sleep, clock):
        super().__init__(spec, llm, tracker, sleep, clock)
        self.key, self.cli = key, cli
