"""Provider claude_code: model calls answered by Claude Code (`claude -p`) on a Claude subscription.

models.md, 3: "Another provider (an API that is not OpenAI-compatible) may be added behind the same interface." This
adapter takes the inputs of 2.1, returns the fields of 2.2 and ends in the ways of 2.4; its cost is the tokens at the
spec's prices (4.9, rule 2). The key is a Claude Code OAuth token (`claude setup-token`) in CLAUDE_CODE_OAUTH_TOKEN.

The market sends the whole conversation on every call; Claude Code keeps it itself. So each conversation runs in one
live session, one `claude -p` process. Its first call starts the session: the system message becomes the system
prompt, the view the first user message. A later call whose messages extend the session's last request by the
reply's assistant message continues it:
- tool messages answering every call of the reply go back as those calls' results, through market/claude_tools.py,
  the session's only tools;
- a user message (turns.md 11.4, text path) goes back as the next user message when the reply called nothing, else
  as the result of the reply's last call, its earlier calls answered "not run". The model keeps its own reply in its
  history, not the market's rewritten one.
A call that neither starts nor continues a session fails. A session ends when its backend starts another one, after
a reply with no functions offered and no calls (the summarizer's), when a step fails, or at exit.

Isolation: each session has its own empty home, configuration and working folders, an environment holding only PATH,
the token and the switches of _SWITCHES, no built-in tools, no settings, CLAUDE.md, memory, hooks, plugins or MCP
servers but the market's, and no saved session. With an empty configuration Claude Code knows no account, so the
user's email is not sent.

What the model still sees differently from the OpenAI-compatible providers (Claude Code adds it, with no switch that
keeps subscription login):
- the system prompt starts with Claude Code's line "You are a Claude agent, built on Anthropic's Claude Agent SDK.";
- a reminder before the view carries today's date, and a system message after it the session's working folder, the
  platform, the model's name and Claude Code's note on untrusted files;
- the functions are named mcp__market__<name> (the replies give the market its own names back);
- tool_choice is not sent: Claude Code has none, so neither "required" nor a forced act is enforced;
- a session's functions are those of its first call: Claude Code fixes its tools when the session starts, so a
  correction, which offers act only (turns.md 11.5), still shows compute;
- no temperature (a spec must give temperature: null), and extra_body may hold only reasoning.effort;
- reasoning is the summary of its thinking that Claude returns (display "summarized"), never the raw thinking;
- input tokens count cache reads and writes as plain input, at the spec's input price;
- a failure within a chain is not retried, as a session cannot be replayed: a subscription limit reached there loses
  the turn (a first call waits it out);
- a response the market does not get (Claude Code's own continuation, or its new request after a call of a function
  it does not serve) counts toward the cost cap, not toward a participant's charges.
"""

import atexit
import json
import math
import os
import queue
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import weakref

from .fmt import G
from .models import _LONGEST, CallFailed, Reply, SetupError

CLI = "claude"
TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"  # the variable Claude Code reads its subscription token from
PREFIX = "mcp__market__"  # Claude Code's name for a function of the tool server "market"
NOT_RUN = "not run"
_STUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_tools.py")
_STOPS = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length",
          "refusal": "content_filter"}  # models.md 2.2, "finish reason"
_SWITCHES = {
    "ENABLE_TOOL_SEARCH": "false",  # no deferred-tool placeholder among the tools
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1",
    "DISABLE_AUTOUPDATER": "1",
}
_JITTER = random.Random()
_BACKENDS = weakref.WeakSet()


def backend(spec, llm, tracker, key, *, cli=None, sleep=time.sleep, clock=time.time):
    """models.md 3, "Setting up": the backend of a claude_code spec, or SetupError when Claude Code is missing."""
    cli = cli or CLI
    if shutil.which(cli) is None:
        raise SetupError(f"provider claude_code needs Claude Code's `{cli}` command on PATH")
    return _Backend(spec, llm, tracker, key, cli, sleep, clock)


class _Lost(Exception):
    """A step that ended without a reply; `until`: the reset time of a subscription limit that was hit."""

    def __init__(self, text, until=None):
        super().__init__(text)
        self.until = until


def _count(usage, *keys):
    """Reported token counts, summed; a count that is not a finite number reads as 0."""
    values = (usage.get(key) for key in keys)
    return sum(int(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))


def _tokens_in(usage):
    """The input tokens: cache writes and reads count as input (module docstring)."""
    return _count(usage, "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _text(message):
    content = message.get("content")
    return content if isinstance(content, str) else "" if content is None else json.dumps(content)


class _Backend:
    def __init__(self, spec, llm, tracker, key, cli, sleep, clock):
        self.model, self.key, self.cli = spec["model"], key, cli
        self.max_tokens = spec["max_tokens"]
        self.effort = (spec["extra_body"].get("reasoning") or {}).get("effort")
        self.prices = (spec.get("price_in_per_m"), spec.get("price_out_per_m"))
        self.timeout = min(llm["timeout_s"], _LONGEST)  # Q115: no call lasts that long
        self.retries, self.patience = llm["max_retries"], llm["network_patience_s"]
        self.tracker, self.sleep, self.clock = tracker, sleep, clock
        self.sessions, self.lock = [], threading.Lock()
        _BACKENDS.add(self)

    def call(self, messages, functions=None, tool_choice=None):
        """2.1 to 2.4: one call, the Reply or CallFailed. tool_choice is not sent (module docstring)."""
        began = self.clock()
        session = self._take(messages)
        try:
            if session is None:
                session, reply = self._start(messages, functions or [])
            else:
                deadline = began + self.timeout  # 4.3: one time limit for the whole attempt
                try:
                    session.deliver(messages[len(session.seen) + 1:], deadline)
                    reply = session.read(deadline)
                except _Lost as e:
                    raise CallFailed(f"'{self.model}': {e}") from None
            reply = self._priced(reply)
        except BaseException:
            if session is not None:
                self._drop(session)
            raise
        session.seen = list(messages)
        if functions or reply.tool_calls:
            session.busy = False
        else:
            self._drop(session)
        reply.latency = self.clock() - began
        self.tracker.add(reply.cost, reply.cost_known)
        return reply

    def _take(self, messages):
        """The idle session these messages continue, now busy; else None, every idle session closed."""
        with self.lock:
            for s in self.sessions:
                n = len(s.seen)
                if (not s.busy and len(messages) > n + 1 and messages[:n] == s.seen
                        and messages[n].get("role") == "assistant"):
                    s.busy = True
                    return s
            idle = [s for s in self.sessions if not s.busy]
            self.sessions = [s for s in self.sessions if s.busy]
        for s in idle:
            s.close()
        return None

    def _drop(self, session):
        with self.lock:
            if session in self.sessions:
                self.sessions.remove(session)
        session.close()

    def _start(self, messages, functions):
        """A new session's first step; a session that ends without a reply is started again, as 4.4 to 4.6 retry."""
        roles = [m.get("role") for m in messages]
        if roles not in (["system", "user"], ["user"]):
            raise CallFailed(f"provider claude_code cannot start a conversation of {roles}: only a system message "
                             "and a user message, or a user message")
        system = _text(messages[0]) if roles[0] == "system" else ""
        failures, since = 0, None
        while True:
            session, deadline = None, self.clock() + self.timeout
            try:
                session = _Session(self, system, functions)  # one that cannot start cleans itself up
                with self.lock:
                    self.sessions.append(session)
                session.say(_text(messages[-1]), deadline)
                return session, session.read(deadline)
            except BaseException as e:
                if session is not None:
                    self._drop(session)
                if not isinstance(e, _Lost):
                    raise
                now = self.clock()
                if e.until is not None:  # a subscription limit: waited out, within the network patience
                    since = now if since is None else since
                    wait = max(1.0, e.until - now)
                    if now + wait - since > self.patience:
                        raise CallFailed(f"giving up on '{self.model}': the subscription limit lasts beyond the "
                                         f"network patience of {G(self.patience)} s: {e}") from None
                    self.sleep(wait + _JITTER.random())
                    continue
                failures += 1
                if failures > self.retries:
                    raise CallFailed(f"giving up on '{self.model}' after {failures} attempts: {e}") from None
                self.sleep(min(60, 2 ** failures) + _JITTER.random())

    def _priced(self, reply):
        """4.9: the reply's cost, from its tokens at the spec's prices; unknown when the spec has none."""
        price_in, price_out = self.prices
        if price_in is None or price_out is None:
            reply.cost, reply.cost_known, reply.cost_source = 0.0, False, "unknown"
            return reply
        cost = reply.input_tokens * float(price_in) / 10**6 + reply.output_tokens * float(price_out) / 10**6
        if not math.isfinite(cost):
            raise CallFailed(f"the cost of a reply of '{self.model}' at the spec's prices is not finite")
        reply.cost, reply.cost_source = cost, "computed"
        return reply

    def untold(self, usage):
        """A response the market does not get: its cost still counts toward the cost cap (module docstring)."""
        reply = self._priced(Reply(input_tokens=_tokens_in(usage), output_tokens=_count(usage, "output_tokens")))
        self.tracker.add(reply.cost, reply.cost_known)

    def close(self):
        with self.lock:
            sessions, self.sessions = self.sessions, []
        for s in sessions:
            s.close()


class _Session:
    """One live `claude -p` process and the state of its conversation."""

    def __init__(self, owner, system, functions):
        self.owner, self.clock = owner, owner.clock
        self.seen, self.busy = [], True
        self.pending = []  # the tool-use ids of the last reply's calls, awaiting the market's answers
        self.ended = False  # the last reply ended Claude Code's turn, whose result event is still to come
        self.until = None  # the reset time of a subscription limit Claude Code reported as hit
        self.events = queue.Queue()
        self.proc, self.server, self.conn, self.connected = None, None, None, threading.Event()
        short = len(tempfile.gettempdir()) > 60 and os.path.isdir("/tmp")  # a socket path fits in 104 bytes
        self.dir = tempfile.mkdtemp(prefix="cc-", dir="/tmp" if short else None)
        try:
            self._launch(system, functions)
        except BaseException:
            self.close()
            raise

    def _launch(self, system, functions):
        owner = self.owner
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
                                             stderr=err, text=True, encoding="utf-8")
        except OSError as e:
            raise _Lost(f"Claude Code could not be started: {e}") from None
        threading.Thread(target=self._pump, daemon=True).start()

    def _accept(self):
        try:
            self.conn, _ = self.server.accept()
        except OSError:
            return
        self.connected.set()

    def _pump(self):
        for line in self.proc.stdout:
            try:
                self.events.put(json.loads(line))
            except ValueError:
                pass
        self.events.put(None)

    def _next(self, deadline):
        """The next event of Claude Code's output; _Lost at the deadline or when the process has ended."""
        remaining = deadline - self.clock()
        try:
            event = self.events.get(timeout=max(0.0, remaining)) if remaining > 0 else self.events.get_nowait()
        except queue.Empty:
            raise _Lost(f"no complete response within {G(self.owner.timeout)} s") from None
        if event is None:
            self.events.put(None)
            raise _Lost("Claude Code ended: " + self._stderr_tail())
        if event.get("type") == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            if info.get("status") == "rejected" and isinstance(info.get("resetsAt"), (int, float)):
                self.until = info["resetsAt"]
        return event

    def _stderr_tail(self):
        try:
            with open(self.stderr, encoding="utf-8", errors="replace") as f:
                return f.read()[-500:].strip() or "(nothing on stderr)"
        except OSError:
            return "(no stderr)"

    def say(self, text, deadline):
        """A user message, once the previous turn's result has come."""
        while self.ended:
            response, _ = self._response(deadline)
            if response is None:
                self.ended = False
            else:  # Claude Code's own continuation of the previous turn; its served calls are not run
                blocks, usage, stop = response
                served = [b.get("id") for b in blocks
                          if b.get("type") == "tool_use" and str(b.get("name", "")).startswith(PREFIX)]
                if stop == "tool_use" and served:
                    self.answer(dict.fromkeys(served, NOT_RUN), deadline)
                self.owner.untold(usage)
        line = json.dumps({"type": "user", "message": {"role": "user", "content": text}})
        late = threading.Timer(max(0.0, deadline - self.clock()), self.proc.kill)  # a child that stops reading
        late.start()
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            if late.finished.is_set():
                raise _Lost(f"no complete response within {G(self.owner.timeout)} s") from None
            raise _Lost("Claude Code ended: " + self._stderr_tail()) from None
        finally:
            late.cancel()

    def answer(self, answers, deadline):
        """The market's answers to pending calls, by tool-use id, sent to the tool server."""
        if not self.connected.wait(max(0.0, deadline - self.clock())):
            raise _Lost("the market's tool server did not connect")
        try:
            self.conn.settimeout(max(0.001, deadline - self.clock()))
            for use_id, text in answers.items():
                self.conn.sendall((json.dumps({"id": use_id, "text": text}) + "\n").encode("utf-8"))
        except OSError as e:
            raise _Lost(f"the market's tool server is gone: {e}") from None
        self.pending = []

    def deliver(self, suffix, deadline):
        """The messages after the reply's assistant message, as the module docstring maps them."""
        roles = [m.get("role") for m in suffix]
        if self.pending and roles == ["tool"] * len(self.pending) and (
                sorted(m.get("tool_call_id") for m in suffix) == sorted(self.pending)):
            self.answer({m["tool_call_id"]: _text(m) for m in suffix}, deadline)
        elif roles == ["user"] and self.pending:
            answers = dict.fromkeys(self.pending[:-1], NOT_RUN)
            answers[self.pending[-1]] = _text(suffix[0])
            self.answer(answers, deadline)
        elif roles == ["user"]:
            self.say(_text(suffix[0]), deadline)
        else:
            raise CallFailed(f"provider claude_code cannot continue a conversation with {roles} after the reply "
                             f"(pending calls: {len(self.pending)})")

    def read(self, deadline):
        """The next reply. A response calling a function Claude Code does not serve (a name without PREFIX) is not
        one: Claude Code answers that call with an error and asks the model again itself, so it is skipped, its
        served calls answered NOT_RUN."""
        while True:
            response, ending = self._response(deadline)
            if response is None:
                raise _Lost(f"Claude Code's turn ended without a reply: {ending}", self.until)
            blocks, usage, stop = response
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            if stop == "tool_use" and any(not str(b.get("name", "")).startswith(PREFIX) for b in uses):
                served = [b.get("id") for b in uses if str(b.get("name", "")).startswith(PREFIX)]
                if served:
                    self.answer(dict.fromkeys(served, NOT_RUN), deadline)
                self.owner.untold(usage)
                continue
            return self._reply(blocks, usage, stop)

    def _response(self, deadline):
        """The next model response Claude Code completes, (blocks, usage, stop_reason), from its stream events;
        (None, the result's text) when its turn ends first."""
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
                order = sorted(blocks, key=lambda i: i if isinstance(i, int) else -1)
                return ([blocks[i] for i in order], usage, stop), None

    def _reply(self, blocks, usage, stop):
        """2.2: the fields of one response; its calls given the market's own function names."""
        calls = [{"id": b.get("id"), "type": "function",
                  "function": {"name": b.get("name", "").removeprefix(PREFIX),
                               "arguments": b["json"] or json.dumps(b.get("input") or {})}}
                 for b in blocks if b.get("type") == "tool_use"]
        self.pending = [c["id"] for c in calls] if stop == "tool_use" else []
        self.ended = stop != "tool_use"
        return Reply(
            text="".join(b.get("text", "") for b in blocks if b.get("type") == "text"),
            tool_calls=calls or None,
            reasoning="".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking"),
            input_tokens=_tokens_in(usage), output_tokens=_count(usage, "output_tokens"),
            finish_reason=_STOPS.get(stop, stop))

    def close(self):
        if self.proc is not None:
            for closing in (self.proc.terminate, self.proc.stdin.close):  # stdin last: its flush may block
                try:
                    closing()
                except (OSError, ValueError):
                    pass
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        for s in (self.conn, self.server):
            if s is not None:
                s.close()
        shutil.rmtree(self.dir, ignore_errors=True)


@atexit.register
def _close_all():
    for b in list(_BACKENDS):
        b.close()
