"""What the providers that run an agent harness have in common (claude_code: market/claude_code.py; codex:
market/codex.py): each conversation runs in one live session of the harness, isolated from the user's own setup.

models.md, 3: "Another provider (an API that is not OpenAI-compatible) may be added behind the same interface." Such
an adapter takes the inputs of 2.1, returns the fields of 2.2 and ends in the ways of 2.4; its cost is the tokens at
the spec's prices (4.9, rule 2).

The market sends the whole conversation on every call; a harness keeps it itself. So each conversation runs in one
live session. Its first call starts the session: the system message becomes the system prompt, the view the first
user message. A later call whose messages extend the session's last request by the reply's assistant message
continues it:
- tool messages answering every call of the reply go back as those calls' results;
- a user message (turns.md 11.4, text path) goes back as the next user message when the reply called nothing, else
  as the result of the reply's last call, its earlier calls answered NOT_RUN. The model keeps its own reply in its
  history, not the market's rewritten one.
A call that neither starts nor continues a session fails. A session ends when its backend starts another one, after
a reply with no functions offered and no calls (the summarizer's), when a step fails, when its episode ends, or at
exit.

What this does differently from the OpenAI-compatible providers, whatever the harness (each transport lists its own):
- tool_choice is not sent, so "required" is not enforced;
- a session's functions are those of its first call, as a harness fixes its tools when the session starts. A call
  that allows fewer (a correction offers act only, turns.md 11.5; a forced act allows act only, 4.1) gets the notice
  "compute is not available now: call act" at the start of the text that carries the correction, else of the first
  text it delivers; the first response that calls only functions it withholds is answered with that notice and not
  returned: the harness asks the model again and the next response is read;
- no temperature (a spec must give temperature: null), and extra_body may hold only reasoning.effort;
- a failure within a chain is not retried, as a session cannot be replayed: a subscription limit reached there loses
  the turn (a first call waits it out);
- a response the market does not get (the harness's own continuation, its new request after a call of a function it
  does not serve, or one answered with the notice above) adds its tokens and its thinking to the next reply, so it is
  charged and logged with it; when the session ends first, its cost counts toward the cost cap only.
SPEC_QUESTIONS Q118 records these and the user's decision that runs on these providers are not the same market.
"""

import atexit
import json
import math
import os
import queue
import random
import signal
import subprocess
import threading
import weakref
from dataclasses import dataclass, field

from .fmt import G
from .models import _LONGEST, CallFailed, Reply

NOT_RUN = "not run"
_JITTER = random.Random()
_BACKENDS = weakref.WeakSet()


class Lost(Exception):
    """A step that ended without a reply; `until`: the reset time of a subscription limit that was hit."""

    def __init__(self, text, until=None):
        super().__init__(text)
        self.until = until


@dataclass
class Response:
    """One model response as a transport reads it. calls: {"id", "name" (the market's), "arguments" (a string),
    "served" (a function of the market's tool server)}; waits: its calls await answers."""

    calls: list = field(default_factory=list)
    text: str = ""
    thinking: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    reasoning_tokens: int = 0
    finish: object = None
    waits: bool = False


def count(value):
    """A reported token count; one that is not a finite number reads as 0."""
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return int(value) if ok else 0


def allowed(functions, tool_choice):
    """The names of the functions a call allows: those offered, none for "none", or only the one it forces."""
    names = [f["function"]["name"] for f in functions]
    if tool_choice == "none":
        return []
    forced = (tool_choice.get("function") or {}).get("name") if isinstance(tool_choice, dict) else None
    return [forced] if forced in names else names


def notice(withheld, allowed_names):
    """What the model is told of functions its session shows but the call withholds."""
    verb = "is" if len(withheld) == 1 else "are"
    then = f": call {' or '.join(allowed_names)}" if allowed_names else ""
    return f"{' and '.join(withheld)} {verb} not available now{then}"


def noticed(text_notice, text):
    return f"{text_notice}\n\n{text}" if text_notice else text


def joined(*texts):
    """Thinking texts, each set apart by an empty line."""
    return "\n\n".join(t for t in texts if t)


def text_of(message):
    content = message.get("content")
    return content if isinstance(content, str) else "" if content is None else json.dumps(content)


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


def write_within(proc, line, deadline, clock, timeout, ended):
    """A line to the stdin of a child that leads its process group, the whole group killed at the deadline (a child
    that stops reading, or a descendant that inherited its stdin and stops reading); Lost on failure."""
    late = threading.Timer(max(0.0, deadline - clock()), _kill_group, (proc,))
    late.start()
    try:
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
    except (OSError, ValueError):
        if late.finished.is_set():
            raise Lost(f"no complete response within {G(timeout)} s") from None
        raise Lost(ended()) from None
    finally:
        late.cancel()


def stop_process(proc):
    """Stop a child that leads its process group, and every process left in the group, then reap it: SIGTERM, up to
    5 s for the leader to exit, then SIGKILL to the group whatever the leader did (a descendant may ignore SIGTERM)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass
    _kill_group(proc)
    try:
        proc.stdin.close()  # after the group is gone: its flush may block on a reader that stopped
    except (OSError, ValueError):
        pass
    proc.wait()


class Backend:
    """A spec's backend: its live sessions, one per conversation. `session` is the transport's Session class."""

    provider = "a harness"  # the provider name, as configs give it
    harness = "the harness"  # its name in error texts
    session = None

    def __init__(self, spec, llm, tracker, sleep, clock):
        self.model, self.max_tokens = spec["model"], spec["max_tokens"]
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
        names = allowed(functions or [], tool_choice)
        session = self._take(messages)
        try:
            if session is None:
                session, reply = self._start(messages, functions or [], names)
            else:
                deadline = began + self.timeout  # 4.3: one time limit for the whole attempt
                withheld, said = session.withhold(names)
                try:
                    session.deliver(messages[len(session.seen) + 1:], deadline, said)
                    reply = session.read(deadline, withheld, said)
                except Lost as e:
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
            self._spend(s)
        return None

    def _drop(self, session):
        with self.lock:
            if session in self.sessions:
                self.sessions.remove(session)
        self._spend(session)

    def _spend(self, session):
        """Close a session; responses no reply took up count toward the cost cap."""
        session.close()
        carried, session.carried = session.carried, Response()
        if carried.tokens_in or carried.tokens_out:
            reply = self._priced(Reply(input_tokens=carried.tokens_in, output_tokens=carried.tokens_out))
            self.tracker.add(reply.cost, reply.cost_known)

    def _start(self, messages, functions, names):
        """A new session's first step; a session that ends without a reply is started again, as 4.4 to 4.6 retry."""
        roles = [m.get("role") for m in messages]
        if roles not in (["system", "user"], ["user"]):
            raise CallFailed(f"provider {self.provider} cannot start a conversation of {roles}: only a system "
                             "message and a user message, or a user message")
        system = text_of(messages[0]) if roles[0] == "system" else ""
        failures, since = 0, None
        while True:
            session, deadline = None, self.clock() + self.timeout
            try:
                session = self.session(self, system, functions, deadline)  # one that cannot start cleans up
                with self.lock:
                    self.sessions.append(session)
                withheld, said = session.withhold(names)
                session.say(noticed(said, text_of(messages[-1])), deadline)
                return session, session.read(deadline, withheld, said)
            except BaseException as e:
                if session is not None:
                    self._drop(session)
                if not isinstance(e, Lost):
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

    def close(self):
        with self.lock:
            sessions, self.sessions = self.sessions, []
        for s in sessions:
            self._spend(s)


class Session:
    """One live session of a harness and the state of its conversation. A transport implements launch (which sets
    `proc` and `stderr` and starts `pump`), response, send_user, send_answers and close, keeping `until` (the reset
    time of a subscription limit it saw hit)."""

    def __init__(self, owner, system, functions, deadline):
        self.owner, self.clock = owner, owner.clock
        self.seen, self.busy = [], True
        self.pending = []  # the ids of the last reply's calls, awaiting the market's answers
        self.ended = False  # the last reply ended the harness's turn, whose end is still to be read
        self.until = None
        self.events = queue.Queue()
        self.tools = [f["function"]["name"] for f in functions]  # the functions it shows, fixed at its start
        self.carried = Response()  # what responses no reply has taken up add up to
        self.proc, self.stderr = None, None
        try:
            self.launch(system, functions, deadline)
        except BaseException:
            self.close()
            raise

    def withhold(self, names):
        """The functions this session shows that a call does not allow, and the notice that says so (or None)."""
        withheld = [n for n in self.tools if n not in names]
        return withheld, notice(withheld, names) if withheld else None

    def next_event(self, deadline):
        """The next event the transport's reader queued; Lost at the deadline or when the harness has ended."""
        remaining = deadline - self.clock()
        try:
            event = self.events.get(timeout=remaining) if remaining > 0 else self.events.get_nowait()
        except queue.Empty:
            raise Lost(f"no complete response within {G(self.owner.timeout)} s") from None
        if event is None:
            self.events.put(None)
            raise Lost(self.ended_text())
        return event

    def _carry(self, response):
        """A response the market does not get: its tokens and thinking go with the next reply."""
        c = self.carried
        self.carried = Response(tokens_in=c.tokens_in + response.tokens_in,
                                tokens_out=c.tokens_out + response.tokens_out,
                                reasoning_tokens=c.reasoning_tokens + response.reasoning_tokens,
                                thinking=joined(c.thinking, response.thinking))

    def say(self, text, deadline):
        """A user message, once the previous turn has ended."""
        while self.ended:
            response, _ = self.response(deadline)
            if response is None:
                self.ended = False
            else:  # the harness's own continuation of the previous turn; its served calls are not run
                self._carry(response)
                served = [c["id"] for c in response.calls if c["served"]]
                if response.waits and served:
                    self.answer(dict.fromkeys(served, NOT_RUN), deadline)
        self.send_user(text, deadline)

    def answer(self, answers, deadline):
        """The market's answers to pending calls, by call id."""
        self.send_answers(answers, deadline)
        self.pending = []

    def deliver(self, suffix, deadline, said=None):
        """The messages after the reply's assistant message, as the module docstring maps them; a notice opens the
        text that carries the correction body (text path), else the first text delivered."""
        roles = [m.get("role") for m in suffix]
        if self.pending and roles == ["tool"] * len(self.pending) and (
                sorted(m.get("tool_call_id") for m in suffix) == sorted(self.pending)):
            texts = [text_of(m) for m in suffix]
            texts[0] = noticed(said, texts[0])
            self.answer({m["tool_call_id"]: t for m, t in zip(suffix, texts)}, deadline)
        elif roles == ["user"] and self.pending:
            answers = dict.fromkeys(self.pending[:-1], NOT_RUN)
            answers[self.pending[-1]] = noticed(said, text_of(suffix[0]))
            self.answer(answers, deadline)
        elif roles == ["user"]:
            self.say(noticed(said, text_of(suffix[0])), deadline)
        else:
            raise CallFailed(f"provider {self.owner.provider} cannot continue a conversation with {roles} after "
                             f"the reply (pending calls: {len(self.pending)})")

    def read(self, deadline, withheld=(), said=None):
        """The next reply. Two responses are not one, and are skipped (module docstring):
        - one calling a function the harness does not serve: the harness answers that call with an error and asks
          the model again itself; its served calls are answered NOT_RUN;
        - the first one whose calls are all of withheld functions: they are answered with the notice, and the model
          asked again (once: a second such response is the reply)."""
        pushed = False
        while True:
            response, ending = self.response(deadline)
            if response is None:
                raise Lost(f"{self.owner.harness}'s turn ended without a reply: {ending}", self.until)
            served = [c for c in response.calls if c["served"]]
            if response.waits and len(served) < len(response.calls):
                text = NOT_RUN
            elif (response.waits and response.calls and withheld and not pushed
                  and all(c["name"] in withheld for c in served)):
                text, pushed = said, True
            else:
                return self._reply(response)
            self._carry(response)
            if served:
                self.answer({c["id"]: text for c in served}, deadline)

    def _reply(self, response):
        """2.2: the fields of one response, with what was carried."""
        calls = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                 for c in response.calls]
        self.pending = [c["id"] for c in calls] if response.waits else []
        self.ended = not response.waits
        c, self.carried = self.carried, Response()
        return Reply(text=response.text, tool_calls=calls or None, reasoning=joined(c.thinking, response.thinking),
                     input_tokens=c.tokens_in + response.tokens_in, output_tokens=c.tokens_out + response.tokens_out,
                     reasoning_tokens=c.reasoning_tokens + response.reasoning_tokens, finish_reason=response.finish)

    def pump(self):
        """Read the harness's output lines into the event queue (JSON objects only); None at its end."""
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                self.events.put(event)
        self.events.put(None)

    def ended_text(self):
        try:
            with open(self.stderr, encoding="utf-8", errors="replace") as f:
                tail = f.read()[-500:].strip() or "(nothing on stderr)"
        except (OSError, TypeError):
            tail = "(no stderr)"
        return f"{self.owner.harness} ended: {tail}"


@atexit.register
def _close_all():
    for b in list(_BACKENDS):
        b.close()
