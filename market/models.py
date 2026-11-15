"""The model layer (spec/models.md): one call interface for every provider, the dollar cost and the cost cap.

A model spec is one that config.resolve returns: it carries the temperature, max_tokens and extra_body its calls
use (config.md 3.4.1, "when absent").
"""

import errno
import http.client
import json
import math
import os
import random
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import FREE_PROVIDERS
from .fmt import F, G, finite

_PROVIDERS = {  # section 3: (base URL, API key variable) when the spec gives none, of the providers that have one
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
    "claude_code": (None, "CLAUDE_CODE_OAUTH_TOKEN"),  # market/claude_code.py: Claude Code on a subscription
}
_TITLE = "contracting-market"
_RETRIED = (408, 409, 425, 429)
_LOST_ERRNO = (errno.ENETDOWN, errno.ENETUNREACH, errno.ENETRESET, errno.EHOSTDOWN, errno.EHOSTUNREACH,
               errno.ETIMEDOUT)
_JITTER = random.Random()  # 4.5: the waits' randomness, outside the market's keyed draws


@dataclass
class Reply:
    """2.2: what a call that succeeds returns."""

    text: str = ""
    tool_calls: list | None = None
    reasoning: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0
    cost_known: bool = True
    input_estimated: bool = False
    output_estimated: bool = False
    cost_source: str = "reported"  # reported | computed | free | unknown
    failed_cost: float = 0.0
    finish_reason: object = None
    served_by: object = None
    latency: float | None = None


class CallFailed(Exception):
    """2.4: a failed call, with its error text (4.10) and its failed attempts' reported cost."""

    def __init__(self, error, failed_cost=0.0):
        super().__init__(error)
        self.error = error
        self.failed_cost = failed_cost


class SetupError(Exception):
    """Section 3, "Setting up": a backend that cannot be set up."""


class CostCapReached(Exception):
    """2.4 and section 7: the stop a cost-cap check raises; not a failed call."""


class CostTracker:
    """Section 7: the running dollar total of one invocation, shared by all its episodes and threads."""

    def __init__(self, cap):
        self.cap = cap
        self.total = 0.0
        self.unknown = 0
        self._lock = threading.Lock()

    def add(self, dollars, known=True):
        """Add a reply's cost (4.9), or a failed attempt's reported cost (4.4); count a reply of unknown cost."""
        with self._lock:
            self.total += dollars
            self.unknown += not known

    def reached(self):
        return self.cap is not None and self.total >= self.cap

    def check(self):
        """Raise the stop when the total is at or above the cap."""
        if self.reached():
            raise CostCapReached(f"cost cap ${F(self.cap, 2)} reached (spent ${F(self.total, 2)})")


def charge(cost, credits_per_usd):
    """Section 5: a reply's token charge in credits; a charge of 0 or less is skipped by the caller."""
    return finite(round(cost * credits_per_usd, 6), "a token charge")


ENV_FILE = ".env"  # section 3: variables also read from this file, the real environment taking precedence
_LONGEST = 1e9  # seconds: the longest time limit a socket takes; no call lasts that long


def estimate(s):
    """4.8: the token estimate of a text."""
    return max(1, len(s) // 4)


def resolved(spec):
    """Section 3: a spec with its provider's base URL and key variable where it gives none, and a mock spec's
    missing prices as 0 (running.md, 7.1)."""
    base, variable = _PROVIDERS.get(spec["provider"], (None, None))
    mock = spec["provider"] == "mock"
    prices = {key: spec.get(key) or 0 for key in ("price_in_per_m", "price_out_per_m")} if mock else {}
    return {"api_key_env": variable, **spec, **prices, "base_url": spec.get("base_url") or base}


def read_env_file(path):
    """Section 3: the variables of a .env file: NAME=value lines, with blank lines, # comments and a leading
    `export` skipped, one pair of matching quotes around a value removed, else a ` #` comment after it; {} when the
    file is missing or cannot be read."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeError):
        return {}
    found = {}
    for line in lines:
        line = line.strip()
        if line.split(None, 1)[:1] == ["export"]:
            line = line[len("export"):].lstrip()
        name, equals, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not equals or not name or name.startswith("#"):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        found[name] = value
    return found


def backend(spec, llm, tracker, *, env=None, sleep=time.sleep, clock=time.time):
    """Section 3, "Setting up": the backend of one model spec, or SetupError naming an unset key variable. The
    key comes from `env` when given, else from the environment, else (unset or empty there) from ENV_FILE."""
    base, variable = resolved(spec)["base_url"], resolved(spec)["api_key_env"]
    key = None
    if variable is not None:
        if env is not None:
            key = env.get(variable)
        else:
            key = os.environ.get(variable) or read_env_file(ENV_FILE).get(variable)
        if not key:
            raise SetupError(f"set {variable} in your environment or in .env")
    if spec["provider"] == "mock":
        from . import mock
        return mock.backend(spec)
    if key and not (key.isascii() and key.isprintable()):
        raise SetupError(f"the value of {variable} holds characters a request cannot carry")
    if spec["provider"] == "claude_code":
        from . import claude_code
        return claude_code.backend(spec, llm, tracker, key, sleep=sleep, clock=clock)
    return _Backend(spec, llm, tracker, base, key, sleep, clock)


class _Lost(Exception):
    """4.4: a network failure, waited out (4.6)."""


class _Failed(Exception):
    """4.4: a failed attempt that is counted (retry) or fails the call at once, with its reported cost."""

    def __init__(self, text, cost=0.0, retry=True):
        super().__init__(text)
        self.cost = cost
        self.retry = retry


class _Malformed(Exception):
    """4.7: a field that cannot be read in the shape or value it has."""


def _why(e):
    return f"{type(e).__name__}: {e}"


def _shut(sock):
    """Shut the connection's socket itself: a TLS socket's own shutdown would unwrap it under the reader."""
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass


def _object(v):
    """4.7: an empty value reads as an empty object; any other must be an object."""
    if not v:
        return {}
    if not isinstance(v, dict):
        raise _Malformed("an object was expected")
    return v


def _count(v):
    """4.7 and 4.8: a reported token count, truncated toward zero; a boolean is not a number."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or isinstance(v, float) and not math.isfinite(v):
        raise _Malformed("a token count is not a finite number")
    return int(v)


def _dollars(v):
    """4.7: a reported cost as a double; it must be a finite number."""
    if not isinstance(v, bool) and isinstance(v, (int, float)):
        try:
            v = float(v)
        except OverflowError:
            v = math.inf
        if math.isfinite(v):
            return v
    raise _Malformed("the cost is not a finite number")


def _reported_cost(body):
    """4.4: the cost a failed attempt's response reported; 0 when it reported none."""
    try:
        return _dollars(body["usage"]["cost"])
    except (KeyError, TypeError, _Malformed):
        return 0.0


def request_text(body):
    """4.8: the request's text that the input estimate counts, from a body's messages and tools."""
    parts = []
    messages = body.get("messages")
    for message in messages if isinstance(messages, list) else []:
        if isinstance(message, dict):
            content = message.get("content")
            parts.append("" if content is None else content if isinstance(content, str) else json.dumps(content))
            if message.get("tool_calls"):
                parts.append(json.dumps(message["tool_calls"]))
    if body.get("tools") and isinstance(body["tools"], list):
        parts.append(json.dumps(body["tools"]))
    return "".join(parts)


class _Backend:
    """Section 4: the OpenAI-compatible backend of openrouter, ollama and openai_compatible."""

    def __init__(self, spec, llm, tracker, base, key, sleep, clock):
        base = (base or "").rstrip("/") + "/chat/completions"
        try:
            url = urlsplit(base)
            port = url.port
            usable = (url.scheme in ("http", "https") and bool(url.hostname) and base.isascii()
                      and base.isprintable() and " " not in base and bool(url.hostname.encode("idna")))
        except ValueError:  # a port that is no port, a broken address, a host name that cannot be sent
            usable = False
        if not usable:
            raise SetupError(f"the base_url of model '{spec.get('model')}' must be an http or https URL")
        https = url.scheme == "https"
        self.connection = http.client.HTTPSConnection if https else http.client.HTTPConnection
        self.host, self.port = url.hostname, port
        self.path = url.path + ("?" + url.query if url.query else "")
        self.headers = {"Content-Type": "application/json"}
        if key:
            self.headers["Authorization"] = f"Bearer {key}"
        self.openrouter = spec["provider"] == "openrouter"
        if self.openrouter:
            self.headers["X-Title"] = _TITLE
        self.free = spec["provider"] in FREE_PROVIDERS
        self.model = spec.get("model")
        self.max_tokens, self.temperature, self.extra = spec["max_tokens"], spec["temperature"], spec["extra_body"]
        self.prices = (spec.get("price_in_per_m"), spec.get("price_out_per_m"))
        self.timeout, self.retries = min(llm["timeout_s"], _LONGEST), llm["max_retries"]
        self.patience = llm["network_patience_s"]
        self.tracker, self.sleep, self.clock = tracker, sleep, clock

    def call(self, messages, functions=None, tool_choice=None):
        """2.1 to 2.4: one call: the Reply, or CallFailed; attempts, retries and waits as 4.3 to 4.6."""
        began = self.clock()
        body = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.openrouter:
            body["usage"] = {"include": True}
        if functions:
            body["tools"] = functions
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        body.update(self.extra)
        data = json.dumps(body).encode("ascii")
        failures, streak, since, failed_cost = 0, 0, None, 0.0
        while True:
            try:
                reply = self._end(*self._attempt(data), body)
            except _Lost as e:
                now = self.clock()
                since = now if since is None else since
                if now - since > self.patience:
                    raise CallFailed(f"giving up on '{self.model}': no network for more than "
                                     f"{G(self.patience)} s: {e}", failed_cost) from None
                streak += 1
                self.sleep(min(30, 2 ** streak) + _JITTER.random())
            except _Failed as e:
                streak, since = 0, None
                failed_cost += e.cost
                self.tracker.add(e.cost)
                failures += 1
                if not e.retry:
                    raise CallFailed(str(e), failed_cost) from None
                if failures > self.retries:
                    raise CallFailed(f"giving up on '{self.model}' after {failures} attempts: {e}",
                                     failed_cost) from None
                self.sleep(min(60, 2 ** failures) + _JITTER.random())
            else:
                reply.failed_cost = failed_cost
                reply.latency = self.clock() - began
                self.tracker.add(reply.cost, reply.cost_known)
                return reply

    def _attempt(self, data):
        """4.3: one request: its status and complete body, else _Lost (a network failure) or _Failed."""
        start = self.clock()
        conn = self.connection(self.host, self.port, timeout=self.timeout)
        late = threading.Event()
        timer = None
        try:
            try:
                conn.connect()
                sock = conn.sock
                timer = threading.Timer(self.timeout, lambda: (late.set(), _shut(sock)))
                timer.daemon = True
                timer.start()
                conn.request("POST", self.path, data, self.headers)
            except OSError as e:
                raise _Lost(_why(e)) from None
            try:
                response = conn.getresponse()
                raw = response.read()  # raises IncompleteRead on a body cut short
                if late.is_set() or self.clock() - start > self.timeout:
                    raise TimeoutError
            except (OSError, http.client.HTTPException) as e:
                code = getattr(e, "errno", None)
                if late.is_set() or isinstance(e, TimeoutError) and code is None:
                    raise _Failed(f"no complete response within {G(self.timeout)} s") from None
                if isinstance(e, ConnectionError) or code in _LOST_ERRNO:
                    raise _Lost(_why(e)) from None
                raise _Failed(_why(e)) from None
            return response.status, raw
        finally:
            if timer:
                timer.cancel()
            conn.close()

    def _end(self, status, raw, sent):
        """4.4: what an attempt whose response arrived ends in: the Reply, or _Failed."""
        try:
            body = json.loads(raw)
        except (ValueError, RecursionError):
            body = None
        cost = _reported_cost(body)
        if not 200 <= status < 300:
            text = raw.decode("utf-8", "replace")[:500]
            raise _Failed(f"HTTP {status} for model '{self.model}': {text}", cost,
                          retry=status in _RETRIED or status >= 500)
        if not isinstance(body, dict):
            raise _Failed("the response body is not a JSON object")
        choices = body.get("choices")
        if body.get("error") and not choices:
            raise _Failed(f"provider error: {json.dumps(body['error'])[:500]}", cost)
        first = choices[0] if choices and isinstance(choices, list) else None
        if isinstance(first, dict) and (first.get("finish_reason") == "error" or first.get("error")):
            raise _Failed(f"generation error: {json.dumps(first.get('error'))[:500]}", cost)
        try:
            return self._read(body, sent)
        except _Malformed as e:
            raise _Failed(f"malformed response: {e}", cost) from None

    def _read(self, body, sent):
        """4.7 to 4.9: the reply of a 2xx JSON object, or _Malformed."""
        choices = body.get("choices") or [{}]
        if not isinstance(choices, list) or not isinstance(choices[0], dict):
            raise _Malformed("choices is not a list whose first element is an object")
        choice = choices[0]
        message = _object(choice.get("message"))
        content = message.get("content") or ""
        if isinstance(content, list):
            parts = [part.get("text", "") for part in content if isinstance(part, dict)]
            if not all(isinstance(part, str) for part in parts):
                raise _Malformed("a content part's text is not a string")
            content = "".join(parts)
        elif not isinstance(content, str):
            raise _Malformed("content is neither a string nor a list")
        calls = message.get("tool_calls") or None
        if calls is not None and not (isinstance(calls, list) and all(
                isinstance(c, dict) and isinstance(c.get("function") or {}, dict) for c in calls)):
            raise _Malformed("tool_calls is not a list of objects whose function is an object")
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
        if not isinstance(reasoning, str):
            reasoning = json.dumps(reasoning)
        usage = _object(body.get("usage"))
        details = _object(usage.get("completion_tokens_details"))
        reported_in, reported_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
        tokens_in = estimate(request_text(sent)) if reported_in is None else _count(reported_in or 0)
        tokens_out = (estimate(content + json.dumps(calls or "")) if reported_out is None
                      else _count(reported_out or 0))
        price_in, price_out = self.prices
        if usage.get("cost") is not None:
            cost, source = _dollars(usage["cost"]), "reported"
        elif price_in is not None and price_out is not None:
            try:
                cost = tokens_in * float(price_in) / 10**6 + tokens_out * float(price_out) / 10**6
                source = "computed"
            except OverflowError:
                cost = math.inf
            if not math.isfinite(cost):
                raise _Malformed("the cost at the spec's prices is not finite")
        else:
            cost, source = 0.0, "free" if self.free else "unknown"
        return Reply(
            text=content, tool_calls=calls, reasoning=reasoning, input_tokens=tokens_in, output_tokens=tokens_out,
            reasoning_tokens=_count(details.get("reasoning_tokens") or 0), cost=cost,
            cost_known=source != "unknown", input_estimated=reported_in is None,
            output_estimated=reported_out is None, cost_source=source,
            finish_reason=choice.get("finish_reason"), served_by=body.get("provider") or None)
