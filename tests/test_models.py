"""Tests of market/models.py against spec/models.md, with a scripted fake server on 127.0.0.1."""

import errno
import http.client
import http.server
import json
import math
import os
import random
import socket
import struct
import sys
import threading
import time
import types

import pytest

from market import models

LLM = {"temperature": 0.8, "max_tokens": 64000, "timeout_s": 5, "max_retries": 5, "network_patience_s": 900}


def resolved(spec):
    """A participant's spec as config.resolve returns it: with the temperature, max_tokens and extra_body of its
    calls (config.md 3.4.1, "when absent")."""
    return {"temperature": LLM["temperature"], "max_tokens": LLM["max_tokens"], "extra_body": {}, **spec}


BUYERS = resolved({"provider": "openrouter", "model": "qwen/qwen3.8-27b", "price_in_per_m": 0.15,
                   "price_out_per_m": 1.875,
                   "extra_body": {"provider": {"quantizations": ["bf16", "fp16", "fp8", "unknown"]}}})
SUMMARIZER = {"provider": "openrouter", "model": "anthropic/claude-sonnet-5.5", "price_in_per_m": 2.0,
              "price_out_per_m": 10.0, "temperature": None, "max_tokens": 8000,
              "extra_body": {"reasoning": {"effort": "high"}}}
COMPUTE = {"type": "function", "function": {"name": "compute", "description": "run", "parameters": {
    "type": "object", "properties": {"code": {"type": "string", "description": "the program"}},
    "required": ["code"]}}}
ACT = {"type": "function", "function": {"name": "act", "description": "end", "parameters": {
    "type": "object", "properties": {"actions": {"type": "array"}, "memory": {"type": "string"}}}}}
FORCED = {"type": "function", "function": {"name": "act"}}
MESSAGES = [{"role": "system", "content": "system prompt"}, {"role": "user", "content": "the view"}]
KEY = {"OPENROUTER_API_KEY": "sk-test"}
T = 0.15  # the time limit of the tests that really wait for one


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        fake = self.server.fake
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        with fake.lock:
            fake.requests.append(types.SimpleNamespace(path=self.path, headers=self.headers, raw=raw))
            step = fake.script.pop(0) if len(fake.script) > 1 else fake.script[0]
        if callable(step):
            return step(self)
        status, body, *headers = step if isinstance(step, tuple) else (200, step)
        send(self, status, body if isinstance(body, bytes) else json.dumps(body).encode(), *headers)


def send(handler, status, data, headers=None, length=None):
    handler.send_response(status)
    handler.send_header("Content-Length", str(len(data) if length is None else length))
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(data)


class Fake:
    """A server that plays `script` in order (its last step repeats) and records every request."""

    def __init__(self):
        self.script, self.requests, self.lock = [], [], threading.Lock()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.fake = self
        self.httpd.handle_error = lambda *args: None
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, args=(0.005,), daemon=True).start()

    def bodies(self):
        return [json.loads(r.raw) for r in self.requests]


@pytest.fixture
def server():
    fake = Fake()
    yield fake
    fake.httpd.shutdown()
    fake.httpd.server_close()


@pytest.fixture(autouse=True)
def hermetic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .env but a test's own


@pytest.fixture
def no_jitter(monkeypatch):
    monkeypatch.setattr(models, "_JITTER", types.SimpleNamespace(random=lambda: 0.0))


class Clock:
    def __init__(self):
        self.now, self.waits = 1000.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def make(server, spec=None, *, llm=None, env=None, clock=None, cap=None):
    """A backend on the fake server, the waits it asks for, and its tracker."""
    waits, tracker = [], models.CostTracker(cap)
    spec = resolved({"provider": "openai_compatible", "model": "m", "base_url": server.url, **(spec or {})})
    b = models.backend(spec, {**LLM, **(llm or {})}, tracker, env=env or {},
                       sleep=clock.sleep if clock else waits.append, clock=clock or time.time)
    return b, clock.waits if clock else waits, tracker


def ok(content="hi", **body):
    return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}], **body}


def message(**fields):
    return {"choices": [{"message": fields}]}


def once(server, body, spec=None, **kw):
    """The reply to one call answered by `body`."""
    server.script = [body]
    b, _, _ = make(server, spec, **kw)
    return b.call(MESSAGES)


def fails(server, script, spec=None, *, llm=None, **kw):
    """The failed call of a backend that never retries, with the number of requests it made."""
    server.script = script
    b, waits, tracker = make(server, spec, llm={"max_retries": 0, **(llm or {})}, **kw)
    with pytest.raises(models.CallFailed) as e:
        b.call(MESSAGES)
    return e.value, len(server.requests)


# --- 4.1, 4.2: the request ---------------------------------------------------------------------------------


def test_openrouter_first_call_of_a_turn(server):
    server.script = [ok()]
    b, _, _ = make(server, {**BUYERS, "base_url": server.url}, env=KEY)
    b.call(MESSAGES, [COMPUTE, ACT], "required")
    (r,) = server.requests
    body = json.loads(r.raw)
    assert r.path == "/v1/chat/completions"
    assert body == {"model": "qwen/qwen3.8-27b", "messages": MESSAGES, "max_tokens": 64000, "temperature": 0.8,
                    "usage": {"include": True}, "tools": [COMPUTE, ACT], "tool_choice": "required",
                    "provider": {"quantizations": ["bf16", "fp16", "fp8", "unknown"]}}
    assert list(body) == ["model", "messages", "max_tokens", "temperature", "usage", "tools", "tool_choice",
                          "provider"]
    assert json.dumps(body["tools"]) == json.dumps([COMPUTE, ACT])  # the definitions keep their key order
    assert r.headers["Content-Type"] == "application/json"
    assert r.headers["Authorization"] == "Bearer sk-test"
    assert r.headers["X-Title"]


def test_openrouter_summarizer_call(server):
    server.script = [ok()]
    b, _, _ = make(server, {**SUMMARIZER, "base_url": server.url}, env=KEY)
    b.call(MESSAGES)
    assert server.bodies() == [{"model": "anthropic/claude-sonnet-5.5", "messages": MESSAGES, "max_tokens": 8000,
                                "usage": {"include": True}, "reasoning": {"effort": "high"}}]


@pytest.mark.parametrize("provider", ["ollama", "openai_compatible"])
def test_other_providers_send_no_usage_no_title_no_key(server, provider):
    server.script = [ok()]
    b, _, _ = make(server, {"provider": provider}, env=KEY)
    b.call(MESSAGES, [ACT], FORCED)
    (r,) = server.requests
    assert json.loads(r.raw) == {"model": "m", "messages": MESSAGES, "max_tokens": 64000, "temperature": 0.8,
                                 "tools": [ACT], "tool_choice": FORCED}
    assert r.headers["Content-Type"] == "application/json"
    assert r.headers["Authorization"] is None and r.headers["X-Title"] is None


def test_a_named_key_is_sent_by_any_provider_and_null_sends_none(server):
    server.script = [ok()]
    make(server, {"api_key_env": "MY_KEY"}, env={"MY_KEY": "abc"})[0].call(MESSAGES)
    make(server, {**BUYERS, "base_url": server.url, "api_key_env": None})[0].call(MESSAGES)
    assert server.requests[0].headers["Authorization"] == "Bearer abc"
    assert server.requests[1].headers["Authorization"] is None
    assert server.requests[1].headers["X-Title"]


@pytest.mark.parametrize("spec", [{}, SUMMARIZER], ids=["participant", "summarizer"])
@pytest.mark.parametrize("temperature, llm", [
    (0.2, {}),
    (0, {}),
    (0.3, {"temperature": None}),                           # the spec's own, whatever llm's is
    (None, {}),                                             # a null value sends none, whatever llm's is
    (None, {"temperature": 1.5}),
])
def test_the_specs_temperature_is_sent(server, spec, temperature, llm):
    server.script = [ok()]
    make(server, {**spec, "base_url": server.url, "temperature": temperature}, llm=llm, env=KEY)[0].call(MESSAGES)
    (body,) = server.bodies()
    assert body.get("temperature") == temperature and ("temperature" in body) == (temperature is not None)


def test_the_specs_max_tokens_is_sent(server):
    server.script = [ok()]
    make(server, {"max_tokens": 77})[0].call(MESSAGES)
    make(server, {"max_tokens": 77}, llm={"max_tokens": 99})[0].call(MESSAGES)
    make(server, {**SUMMARIZER, "base_url": server.url}, llm={"max_tokens": 99}, env=KEY)[0].call(MESSAGES)
    assert [b["max_tokens"] for b in server.bodies()] == [77, 77, 8000]


def test_tools_and_tool_choice_only_with_functions(server):
    server.script = [ok()]
    b, _, _ = make(server)
    b.call(MESSAGES, None, "required")
    b.call(MESSAGES, [], "required")
    b.call(MESSAGES, [ACT])
    b.call(MESSAGES, [ACT], None)
    b.call(MESSAGES, [COMPUTE, ACT], FORCED)
    bodies = server.bodies()
    assert all("tools" not in b and "tool_choice" not in b for b in bodies[:2])
    assert all(b["tools"] == [ACT] and "tool_choice" not in b for b in bodies[2:4])
    assert bodies[4]["tools"] == [COMPUTE, ACT] and bodies[4]["tool_choice"] == FORCED


def test_extra_body_is_merged_last_and_shallow(server):
    server.script = [ok()]
    extra = {"tool_choice": "auto", "model": "other", "usage": {"x": 1}, "seed": 7, "stream": True,
             "max_tokens": 5, "temperature": 0.1}
    b, _, _ = make(server, {**BUYERS, "base_url": server.url, "extra_body": extra}, env=KEY)
    b.call(MESSAGES, [ACT], "required")
    assert server.bodies() == [{"model": "other", "messages": MESSAGES, "max_tokens": 5, "temperature": 0.1,
                                "usage": {"x": 1}, "tools": [ACT], "tool_choice": "auto", "seed": 7,
                                "stream": True}]


def test_extra_body_can_replace_messages_and_tools(server):
    server.script = [ok()]
    b, _, _ = make(server, {"extra_body": {"messages": [{"role": "user", "content": "x"}], "tools": [COMPUTE]}})
    b.call(MESSAGES, [ACT], "required")
    (body,) = server.bodies()
    assert body["messages"] == [{"role": "user", "content": "x"}] and body["tools"] == [COMPUTE]
    assert body["tool_choice"] == "required"


def test_a_deeply_nested_extra_body_is_sent(server):
    deep = None
    for _ in range(1100):  # deeper than Python's default recursion limit of 1000 frames
        deep = {"x": deep}
    server.script = [ok()]
    make(server, {"extra_body": {"deep": deep}})[0].call(MESSAGES)
    sent = json.loads(server.requests[0].raw)["deep"]
    for _ in range(1100):
        sent = sent["x"]
    assert sent is None


def test_nothing_else_is_sent(server):
    server.script = [ok()]
    make(server)[0].call(MESSAGES)
    assert list(server.bodies()[0]) == ["model", "messages", "max_tokens", "temperature"]


def test_messages_are_carried_as_given_and_the_body_is_ascii(server):
    calls = [{"id": "c1", "type": "function", "function": {"name": "compute", "arguments": "{\"code\": \"1\"}"}}]
    messages = [{"role": "system", "content": "caf\u00e9 \U0001f600"},
                {"role": "user", "content": "lone \ud800 surrogate"},
                {"role": "assistant", "content": "", "tool_calls": calls, "reasoning": "hidden"},
                {"role": "tool", "tool_call_id": "c1", "content": "45"}]
    server.script = [ok()]
    make(server)[0].call(messages, [COMPUTE, ACT], "required")
    (r,) = server.requests
    assert r.raw.isascii()
    assert b"caf\\u00e9 \\ud83d\\ude00" in r.raw and b"lone \\ud800 surrogate" in r.raw
    assert json.loads(r.raw)["messages"] == messages


@pytest.mark.parametrize("tail", ["", "/", "///"])
def test_trailing_slashes_of_the_base_url_are_removed(server, tail):
    assert once(server, ok(), {"base_url": server.url + tail}).text == "hi"
    assert server.requests[0].path == "/v1/chat/completions"


def test_default_base_urls():
    tracker = models.CostTracker(None)
    b = models.backend(BUYERS, LLM, tracker, env=KEY)
    assert (b.connection, b.host, b.port, b.path) == (
        http.client.HTTPSConnection, "openrouter.ai", None, "/api/v1/chat/completions")
    b = models.backend(resolved({"provider": "ollama", "model": "m"}), LLM, tracker, env={})
    assert (b.connection, b.host, b.port, b.path) == (
        http.client.HTTPConnection, "localhost", 11434, "/v1/chat/completions")
    assert "Authorization" not in b.headers


# --- 4.4: what each attempt ends in --------------------------------------------------------------------------


@pytest.mark.parametrize("failure, word", [
    ({"error": {"message": "overloaded"}}, "provider error"),
    ({"error": "x", "choices": []}, "provider error"),
    ({"error": "x", "choices": None}, "provider error"),
    ({"choices": [{"finish_reason": "error", "message": {"content": "half"}}]}, "generation error"),
    ({"choices": [{"error": {"code": 502}, "message": {"content": "half"}}]}, "generation error"),
    ({"choices": [{"message": "text"}]}, "malformed"),
    (b"not json", "not a JSON object"),
    (b"\xff\xfe\xff", "not a JSON object"),
    (b"", "not a JSON object"),
    ([1], "not a JSON object"),
    ("text", "not a JSON object"),
    (5, "not a JSON object"),
    (None, "not a JSON object"),
    (True, "not a JSON object"),
    (lambda h: h.wfile.write(b"this is not http\r\n\r\n"), "BadStatusLine"),
    (lambda h: send(h, 200, b'{"choices": []}', length=40), "IncompleteRead"),
    (lambda h: send_chunked(h, [b'{"choices": '], end=False), "IncompleteRead"),
])
def test_a_counted_failure_is_retried(server, failure, word):
    server.script = [failure, ok("after")]
    b, waits, _ = make(server)
    assert b.call(MESSAGES).text == "after"
    assert len(server.requests) == 2 and len(waits) == 1
    error, n = fails(server, [failure])
    assert word in error.error and "'m'" in error.error and "after 1 attempts" in error.error


def send_chunked(handler, chunks, end=True):
    handler.send_response(200)
    handler.send_header("Transfer-Encoding", "chunked")
    handler.end_headers()
    for chunk in chunks:
        handler.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
    if end:
        handler.wfile.write(b"0\r\n\r\n")


def test_a_chunked_response_is_read(server):
    data = json.dumps(ok("chunked")).encode()
    assert once(server, lambda h: send_chunked(h, [data[:9], data[9:]])).text == "chunked"


def test_an_error_beside_choices_is_ignored_and_a_body_with_neither_is_an_empty_reply(server):
    assert once(server, ok("fine", error={"message": "ignored"})).text == "fine"
    r = once(server, {})
    assert (r.text, r.tool_calls, r.reasoning, r.finish_reason, r.served_by) == ("", None, "", None, None)
    assert once(server, (201, ok("created"))).text == "created"


@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 502, 503, 529, 599])
def test_retried_statuses(server, status):
    server.script = [(status, b"busy"), ok("after")]
    b, waits, _ = make(server)
    assert b.call(MESSAGES).text == "after" and len(waits) == 1
    error, n = fails(server, [(status, b"busy")])
    assert f"HTTP {status}" in error.error and "busy" in error.error and "after 1 attempts" in error.error


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 413, 422, 301, 302, 307, 308])
def test_other_statuses_fail_the_call_at_once(server, status):
    server.script = [(status, b"x" * 600, {"Location": server.url + "/elsewhere"}), ok()]
    b, waits, tracker = make(server, {"model": "qwen/qwen3.8-27b"})
    with pytest.raises(models.CallFailed) as e:
        b.call(MESSAGES)
    assert len(server.requests) == 1 and waits == []  # no retry, and a redirect is not followed
    assert e.value.error == f"HTTP {status} for model 'qwen/qwen3.8-27b': " + "x" * 500
    assert str(e.value) == e.value.error and e.value.failed_cost == 0 and tracker.total == 0


def test_at_most_max_retries_plus_one_counted_attempts(server):
    server.script = [(500, b"down")]
    b, waits, _ = make(server)
    with pytest.raises(models.CallFailed) as e:
        b.call(MESSAGES)
    assert len(server.requests) == 6 and len(waits) == 5
    assert e.value.error.startswith("giving up on 'm' after 6 attempts: ") and "HTTP 500" in e.value.error
    server.requests.clear()
    server.script = [(500, b"down")] * 5 + [ok("sixth")]
    assert make(server)[0].call(MESSAGES).text == "sixth"
    server.requests.clear()
    server.script = [{"error": "a"}, (503, b""), b"junk", ok("mixed")]
    assert make(server, llm={"max_retries": 3})[0].call(MESSAGES).text == "mixed"
    error, n = fails(server, [{"error": "a"}, (503, b""), b"junk", ok()], llm={"max_retries": 2})
    assert "after 3 attempts" in error.error


# --- 4.4, 4.6: network failures ------------------------------------------------------------------------------


def drop(handler):
    """Close the connection before any response."""
    handler.close_connection = True


def reset(handler):
    """Close the connection with a TCP reset."""
    handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    os.close(handler.connection.detach())


def reset_in_body(handler):
    handler.send_response(200)
    handler.send_header("Content-Length", "100")
    handler.end_headers()
    handler.wfile.write(b'{"choices"')
    time.sleep(0.05)
    reset(handler)


def closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("failure", [drop, reset, reset_in_body])
def test_a_network_failure_is_waited_out_and_not_counted(server, failure, no_jitter):
    server.script = [failure] * 7 + [ok("back")]
    b, waits, _ = make(server, llm={"max_retries": 0})
    assert b.call(MESSAGES).text == "back"
    assert len(server.requests) == 8 and waits == [2, 4, 8, 16, 30, 30, 30]


def test_network_waits_have_jitter_from_outside_the_market(server):
    server.script = [drop] * 6 + [ok()]
    b, waits, _ = make(server)
    random.seed(7)
    state = random.getstate()
    b.call(MESSAGES)
    assert random.getstate() == state
    assert all(low <= w < low + 1 for w, low in zip(waits, [2, 4, 8, 16, 30, 30], strict=True))


def test_a_refused_connection_fails_once_the_patience_is_used_up(no_jitter):
    clock = Clock()
    spec = resolved({"provider": "openai_compatible", "model": "m",
                     "base_url": f"http://127.0.0.1:{closed_port()}/v1"})
    b = models.backend(spec, {**LLM, "network_patience_s": 30}, models.CostTracker(None), env={},
                       sleep=clock.sleep, clock=clock)
    with pytest.raises(models.CallFailed) as e:
        b.call(MESSAGES)
    # failures at 0, 2, 6, 14 and 30 s are within the patience (30 is not more than 30); the one at 60 s is not
    assert clock.waits == [2, 4, 8, 16, 30]
    assert "'m'" in e.value.error and "network" in e.value.error and "ConnectionRefusedError" in e.value.error
    assert e.value.failed_cost == 0


def test_patience_zero_allows_one_more_attempt(server, no_jitter):
    clock = Clock()
    server.script = [drop]
    b, waits, _ = make(server, llm={"network_patience_s": 0}, clock=clock)
    with pytest.raises(models.CallFailed):
        b.call(MESSAGES)
    assert len(server.requests) == 2 and waits == [2]


def test_a_counted_failure_ends_the_streak(server, no_jitter):
    clock = Clock()
    server.script = [drop, drop, (500, b""), drop, drop, ok("back")]
    b, waits, _ = make(server, llm={"network_patience_s": 5}, clock=clock)
    # one streak would be 10 s old at the fourth failure; the 500 ended it, so the patience starts again
    assert b.call(MESSAGES).text == "back"
    assert waits == [2, 4, 2, 2, 4]


def test_the_patience_is_wall_clock_time_since_the_streaks_first_failure(server, no_jitter):
    clock = Clock()

    def asleep(handler):  # the machine sleeps for an hour during the attempt
        clock.now += 3600
        drop(handler)

    server.script = [drop, asleep]
    b, waits, _ = make(server, clock=clock)
    with pytest.raises(models.CallFailed):
        b.call(MESSAGES)
    assert len(server.requests) == 2 and waits == [2]


@pytest.mark.parametrize("error, lost", [
    (OSError(errno.ENETDOWN, "Network is down"), True),
    (TimeoutError(errno.ETIMEDOUT, "Operation timed out"), True),
    (BrokenPipeError(errno.EPIPE, "Broken pipe"), True),
    (http.client.RemoteDisconnected("closed"), True),
    (OSError(errno.EIO, "Input/output error"), False),      # any other input/output error is counted
    (TimeoutError("timed out"), False),                     # the socket's own time limit: a timeout
    (http.client.LineTooLong("header line"), False),
])
def test_errors_while_waiting_for_the_response(server, monkeypatch, error, lost):
    real, errors = http.client.HTTPConnection.getresponse, [error]
    monkeypatch.setattr(http.client.HTTPConnection, "getresponse",
                        lambda self: (_ for _ in ()).throw(errors.pop()) if errors else real(self))
    server.script = [ok("then")]
    b, waits, _ = make(server, llm={"max_retries": 0})
    if lost:
        assert b.call(MESSAGES).text == "then" and len(waits) == 1
    else:
        with pytest.raises(models.CallFailed):
            b.call(MESSAGES)


# --- 4.3: the time limit of one attempt ----------------------------------------------------------------------


def stall(handler):
    time.sleep(5 * T)


def stall_in_body(handler):
    handler.send_response(200)
    handler.send_header("Content-Length", "100")
    handler.end_headers()
    handler.wfile.write(b'{"choices"')
    time.sleep(5 * T)


def trickle_headers(handler):
    handler.wfile.write(b"HTTP/1.1 200 OK\r\nX-Padding: ")
    trickle(handler)


def trickle_body(handler):
    handler.send_response(200)
    handler.end_headers()
    trickle(handler)


def trickle(handler):
    try:
        for _ in range(100):
            handler.wfile.write(b" ")
            time.sleep(0.02)
    except OSError:
        pass


@pytest.mark.parametrize("slow", [stall, stall_in_body, trickle_headers, trickle_body])
def test_a_response_not_complete_in_time_is_a_timeout(server, slow):
    began = time.time()
    error, n = fails(server, [slow], llm={"timeout_s": T})
    assert n == 1 and time.time() - began < 3 * T + 0.5  # given up however the server sends
    assert "no complete response within 0.15 s" in error.error and "'m'" in error.error
    server.script = [slow, ok("second")]
    b, waits, _ = make(server, llm={"timeout_s": T})
    assert b.call(MESSAGES).text == "second" and len(waits) == 1


def test_a_body_complete_when_the_time_is_noticed_up_is_a_timeout(server):
    clock = Clock()

    def late(seconds):
        def step(handler):
            clock.now += seconds
            send(handler, 200, json.dumps(ok("late")).encode())
        return step

    assert once(server, late(5), clock=clock).text == "late"  # within timeout_s
    error, n = fails(server, [late(5.001)], clock=clock)
    assert n == 2 and "no complete response within 5 s" in error.error


def test_failing_to_connect_in_time_is_a_network_failure(monkeypatch, no_jitter):
    def connect(self):
        raise TimeoutError("timed out")

    monkeypatch.setattr(http.client.HTTPConnection, "connect", connect)
    clock = Clock()
    spec = resolved({"provider": "openai_compatible", "model": "m", "base_url": "http://127.0.0.1:9/v1"})
    b = models.backend(spec, {**LLM, "max_retries": 0, "network_patience_s": 10}, models.CostTracker(None),
                       env={}, sleep=clock.sleep, clock=clock)
    with pytest.raises(models.CallFailed) as e:
        b.call(MESSAGES)
    assert clock.waits == [2, 4, 8] and "network" in e.value.error


# --- 4.5: waits between attempts -----------------------------------------------------------------------------


def test_waits_between_counted_attempts(server, no_jitter):
    server.script = [(429, b"slow down")]
    b, waits, _ = make(server)
    with pytest.raises(models.CallFailed):
        b.call(MESSAGES)
    assert waits == [2, 4, 8, 16, 32]
    b, waits, _ = make(server, llm={"max_retries": 9})
    with pytest.raises(models.CallFailed):
        b.call(MESSAGES)
    assert waits == [2, 4, 8, 16, 32, 60, 60, 60, 60]


def test_counted_waits_have_jitter_from_outside_the_market(server):
    server.script = [(500, b"")]
    b, waits, _ = make(server, llm={"max_retries": 7})
    random.seed(3)
    state = random.getstate()
    with pytest.raises(models.CallFailed):
        b.call(MESSAGES)
    assert random.getstate() == state
    assert all(low <= w < low + 1 for w, low in zip(waits, [2, 4, 8, 16, 32, 60, 60], strict=True))
    assert len(set(waits)) == 7 and 60 <= sum(waits[:5]) <= 67  # about a minute at the default 5 retries


# --- 4.7: reading the reply ----------------------------------------------------------------------------------


def test_text_function_calls_and_the_other_fields(server):
    calls = [{"id": "c1", "type": "function", "function": {"name": "act", "arguments": "{\"actions\": [] "},
              "index": 0, "extra": {"kept": [1, None]}},
             {"function": {"name": 5, "arguments": {"already": "decoded"}}},
             {"id": "c1", "type": "function", "function": None},
             {"id": None, "function": ""},
             {}]
    body = {"id": "gen-1", "provider": "Together", "model": "served",
            "choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "  visible \n", "tool_calls": calls, "reasoning": "thinking"}},
                {"message": {"content": "second choice"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22, "cost": 0.0033,
                      "completion_tokens_details": {"reasoning_tokens": 7}}}
    r = once(server, body, {"price_in_per_m": 1.0, "price_out_per_m": 1.0})
    assert r == models.Reply(
        text="  visible \n", tool_calls=calls, reasoning="thinking", input_tokens=11, output_tokens=22,
        reasoning_tokens=7, cost=0.0033, cost_known=True, input_estimated=False, output_estimated=False,
        cost_source="reported", failed_cost=0.0, finish_reason="tool_calls", served_by="Together",
        latency=r.latency)
    assert r.latency >= 0


@pytest.mark.parametrize("content, text", [
    ("plain", "plain"),
    ([{"type": "text", "text": "a"}, "skipped", 7, None, [1], {"type": "image"}, {"text": "b"}, {"text": ""}], "ab"),
    (None, ""), ("", ""), ([], ""), (0, ""), (False, ""), ({}, ""),
])
def test_content(server, content, text):
    assert once(server, message(content=content)).text == text
    assert once(server, message()).text == ""


@pytest.mark.parametrize("fields, reasoning", [
    ({"reasoning": "r", "reasoning_content": "rc"}, "r"),
    ({"reasoning": "", "reasoning_content": "rc"}, "rc"),
    ({"reasoning": None, "reasoning_content": "rc"}, "rc"),
    ({"reasoning_content": "rc"}, "rc"),
    ({"reasoning": {"steps": [1, "caf\u00e9"]}}, '{"steps": [1, "caf\\u00e9"]}'),
    ({"reasoning": 3.5}, "3.5"),
    ({"reasoning": [], "reasoning_content": True}, "true"),
    ({"reasoning": None, "reasoning_content": 0}, ""),
    ({}, ""),
])
def test_reasoning(server, fields, reasoning):
    assert once(server, message(content="x", **fields)).reasoning == reasoning


@pytest.mark.parametrize("reason", ["stop", "tool_calls", "length", "content_filter", "", None, 7])
def test_finish_reason_as_given(server, reason):
    assert once(server, {"choices": [{"finish_reason": reason, "message": {"content": "x"}}]}).finish_reason == reason
    assert once(server, {"choices": [{"message": {"content": "x"}}]}).finish_reason is None


@pytest.mark.parametrize("usage, tokens", [
    ({"completion_tokens_details": {"reasoning_tokens": 12}}, 12),
    ({"completion_tokens_details": {"reasoning_tokens": 3.9}}, 3),
    ({"completion_tokens_details": {"reasoning_tokens": None}}, 0),
    ({"completion_tokens_details": {"reasoning_tokens": False}}, 0),
    ({"completion_tokens_details": {"reasoning_tokens": ""}}, 0),
    ({"completion_tokens_details": {}}, 0),
    ({"completion_tokens_details": None}, 0),
    ({"completion_tokens_details": ""}, 0),
    ({"completion_tokens_details": []}, 0),
    ({}, 0), (None, 0), ("", 0), (0, 0), (False, 0), ([], 0),
])
def test_reasoning_tokens(server, usage, tokens):
    assert once(server, ok(usage=usage)).reasoning_tokens == tokens


def test_a_repeated_key_keeps_its_last_value(server):
    raw = (b'{"choices": [{"message": {"content": "first", "content": "last"}}], '
           b'"usage": {"prompt_tokens": 1, "prompt_tokens": 2, "completion_tokens": 3}}')
    r = once(server, raw)
    assert (r.text, r.input_tokens, r.output_tokens) == ("last", 2, 3)


@pytest.mark.parametrize("body", [
    {"choices": []}, {"choices": None}, {"choices": 0}, {"choices": ""}, {"choices": {}}, {"choices": [{}]},
    {"choices": [{"message": None}]}, {"choices": [{"message": []}]}, {"choices": [{"message": ""}]},
    message(content=[], tool_calls=[]), message(tool_calls=None), message(tool_calls=""), message(tool_calls={}),
])
def test_empty_fields_are_never_malformed(server, body):
    r = once(server, body)
    assert (r.text, r.tool_calls, r.finish_reason) == ("", None, None)


nan, inf = math.nan, math.inf
PRICED = {"price_in_per_m": 0.15, "price_out_per_m": 1.875}
MALFORMED = [
    {"choices": "x"}, {"choices": {"a": 1}}, {"choices": [1]}, {"choices": [None]}, {"choices": [[]]},
    {"choices": ["message"]}, {"choices": 3},
    {"choices": [{"message": "m"}]}, {"choices": [{"message": [1]}]}, {"choices": [{"message": 5}]},
    message(tool_calls="x"), message(tool_calls={"a": 1}), message(tool_calls=[1]), message(tool_calls=[None]),
    message(tool_calls=[{"id": "a", "function": {}}, "b"]), message(tool_calls=[{"function": "f"}]),
    message(tool_calls=[{"function": [1]}]), message(tool_calls=[{"function": True}]), message(tool_calls=7),
    message(content=5), message(content=True), message(content={"text": "x"}),
    message(content=[{"text": None}]), message(content=[{"text": 5}]), message(content=["x", {"text": ["y"]}]),
    ok(usage="u"), ok(usage=[1]), ok(usage=7), ok(usage=True),
    ok(usage={"completion_tokens_details": "d"}), ok(usage={"completion_tokens_details": [1]}),
    ok(usage={"prompt_tokens": "12"}), ok(usage={"prompt_tokens": True}), ok(usage={"prompt_tokens": nan}),
    ok(usage={"prompt_tokens": inf}), ok(usage={"prompt_tokens": [1]}), ok(usage={"prompt_tokens": {"n": 1}}),
    ok(usage={"completion_tokens": "3"}), ok(usage={"completion_tokens": True}),
    ok(usage={"completion_tokens": -inf}), ok(usage={"completion_tokens": nan}),
    ok(usage={"completion_tokens_details": {"reasoning_tokens": True}}),
    ok(usage={"completion_tokens_details": {"reasoning_tokens": "3"}}),
    ok(usage={"completion_tokens_details": {"reasoning_tokens": nan}}),
    ok(usage={"cost": False}), ok(usage={"cost": ""}), ok(usage={"cost": "0.1"}), ok(usage={"cost": True}),
    ok(usage={"cost": nan}), ok(usage={"cost": inf}), ok(usage={"cost": [0]}), ok(usage={"cost": {}}),
    ok(usage={"cost": 10**400}),
    ok(usage={"prompt_tokens": 10**400}), ok(usage={"completion_tokens": 1.7e308}),  # rule 2's cost not finite
]


@pytest.mark.parametrize("body", MALFORMED)
def test_malformed_bodies_are_retried(server, body):
    server.script = [json.dumps(body).encode(), ok("after")]
    b, waits, tracker = make(server, PRICED)
    assert b.call(MESSAGES).text == "after" and len(server.requests) == 2 and len(waits) == 1
    error, n = fails(server, [json.dumps(body).encode()], PRICED)
    assert "malformed response" in error.error


def test_a_huge_whole_count_without_prices_is_a_count(server):
    assert once(server, ok(usage={"prompt_tokens": 10**400})).input_tokens == 10**400


# --- 4.8: token counts and the estimate ----------------------------------------------------------------------


def test_the_estimate():
    assert [models.estimate(s) for s in ["", "abc", "abcd", "abcdefg", "x" * 400, "\U0001f600" * 8]] == [
        1, 1, 1, 1, 100, 2]


def test_the_specs_examples(server):
    server.script = [ok("hello world!!")]
    b, _, tracker = make(server, PRICED)
    r = b.call([{"role": "user", "content": "x" * 400}])
    assert (r.input_tokens, r.output_tokens, r.input_estimated, r.output_estimated) == (100, 3, True, True)
    assert r.cost == 2.0625e-05 and r.cost_source == "computed" and r.cost_known
    assert tracker.total == 2.0625e-05 and tracker.unknown == 0
    r = once(server, ok("hi", usage={"prompt_tokens": 0, "completion_tokens": 0}), PRICED)
    assert (r.input_tokens, r.output_tokens, r.input_estimated, r.output_estimated) == (0, 0, False, False)
    assert r.cost == 0 and r.cost_source == "computed"


@pytest.mark.parametrize("usage, counts", [
    ({"prompt_tokens": 10.7, "completion_tokens": 3.99}, (10, 3, False, False)),
    ({"prompt_tokens": -2.5, "completion_tokens": 1e3}, (-2, 1000, False, False)),
    ({"prompt_tokens": 7}, (7, 1, False, True)),
    ({"completion_tokens": 9}, (3, 9, True, False)),
    ({"prompt_tokens": None, "completion_tokens": None}, (3, 1, True, True)),
    ({"prompt_tokens": False, "completion_tokens": ""}, (0, 0, False, False)),  # empty, not missing or null
    (None, (3, 1, True, True)),
])
def test_reported_and_estimated_counts(server, usage, counts):
    server.script = [ok("hi", usage=usage)]
    b, _, _ = make(server, {"price_in_per_m": 1.0, "price_out_per_m": 2.0})
    r = b.call([{"role": "user", "content": "x" * 12}])  # the reply `hi""` estimates to 1
    assert (r.input_tokens, r.output_tokens, r.input_estimated, r.output_estimated) == counts
    assert r.cost == counts[0] * 1.0 / 10**6 + counts[1] * 2.0 / 10**6  # rule 2 prices the truncated counts


def test_the_requests_text(server):
    calls = [{"id": "c\u00e9", "type": "function", "function": {"name": "compute", "arguments": "{}"}}]
    messages = [{"role": "system", "content": "s" * 10},
                {"role": "user"},
                {"role": "assistant", "content": None, "tool_calls": calls, "reasoning": "r" * 4000},
                {"role": "tool", "tool_call_id": "c" * 4000, "content": "t" * 7},
                {"role": "assistant", "content": "a", "tool_calls": []}]
    text = "s" * 10 + json.dumps(calls) + "t" * 7 + "a"
    assert '"c\\u00e9"' in text and ", " in text and ": " in text
    server.script = [ok()]
    b, _, _ = make(server)
    assert b.call(messages).input_tokens == len(text) // 4
    assert b.call(messages, [COMPUTE, ACT], "required").input_tokens == len(text + json.dumps([COMPUTE, ACT])) // 4
    assert b.call(messages, []).input_tokens == len(text) // 4
    assert b.call([{"role": "user", "content": ""}]).input_tokens == 1


def test_the_estimate_uses_the_body_as_sent(server):
    extra = {"messages": [{"role": "user", "content": "y" * 40}], "tools": [COMPUTE]}
    r = once(server, ok(), {"extra_body": extra})
    assert r.input_tokens == (40 + len(json.dumps([COMPUTE]))) // 4
    r = once(server, ok(), {"extra_body": {"messages": [{"role": "user", "content": [{"text": "part"}]}, "odd"]}})
    assert r.input_tokens == len('[{"text": "part"}]') // 4
    assert once(server, ok(), {"extra_body": {"messages": "text", "tools": "none"}}).input_tokens == 1


def test_request_text():
    calls = [{"id": "1"}]
    body = {"messages": [{"role": "user", "content": "ab"}, {"content": None, "tool_calls": calls}]}
    assert models.request_text(body) == 'ab[{"id": "1"}]'
    assert models.request_text({**body, "tools": [ACT]}) == 'ab[{"id": "1"}]' + json.dumps([ACT])
    assert models.request_text({**body, "tools": []}) == models.request_text(body)
    assert models.request_text({}) == ""


def test_a_large_body_is_read_whole(server):
    assert once(server, ok("x" * 300_000)).text == "x" * 300_000


def test_the_replys_text(server):
    calls = [{"id": "1", "type": "function", "function": {"name": "act", "arguments": "{\"memory\": \"\u00e9\"}"}}]
    r = once(server, message(content="some text", tool_calls=calls))
    assert r.output_tokens == len("some text" + json.dumps(calls)) // 4 and "\\u00e9" in json.dumps(calls)
    assert once(server, message(content="x" * 10)).output_tokens == 3  # 10 characters and `""`
    assert once(server, {}).output_tokens == 1


# --- 4.9: the dollar cost ------------------------------------------------------------------------------------

USAGE = {"prompt_tokens": 100, "completion_tokens": 3}
PROVIDERS = ["openrouter", "ollama", "openai_compatible"]


def reply_of(server, provider, usage, prices=None):
    server.script = [ok(usage=usage)]
    b, _, tracker = make(server, {"provider": provider, **(prices or {})}, env=KEY)
    return b.call(MESSAGES), tracker


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("prices", [PRICED, {}])
@pytest.mark.parametrize("cost", [0.0042, 0, 0.0, 1, 1e-9, -0.5])
def test_rule_1_a_reported_cost_is_used_as_reported(server, provider, prices, cost):
    r, tracker = reply_of(server, provider, {**USAGE, "cost": cost}, prices)
    assert (r.cost, r.cost_known, r.cost_source) == (cost, True, "reported") and type(r.cost) is float
    assert (tracker.total, tracker.unknown) == (cost, 0)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("usage", [USAGE, {**USAGE, "cost": None}])
def test_rule_2_the_tokens_at_the_specs_prices(server, provider, usage):
    r, tracker = reply_of(server, provider, usage, PRICED)
    assert (r.cost, r.cost_known, r.cost_source) == (2.0625e-05, True, "computed")
    assert (tracker.total, tracker.unknown) == (2.0625e-05, 0)
    r, _ = reply_of(server, provider, {"prompt_tokens": 123457, "completion_tokens": 7919},
                    {"price_in_per_m": 0.1, "price_out_per_m": 0.7})
    assert r.cost == 123457 * 0.1 / 10**6 + 7919 * 0.7 / 10**6
    r, _ = reply_of(server, provider, USAGE, {"price_in_per_m": 0, "price_out_per_m": 0})
    assert (r.cost, r.cost_known, r.cost_source) == (0, True, "computed")


@pytest.mark.parametrize("provider, known, source", [
    ("openrouter", False, "unknown"), ("openai_compatible", False, "unknown"), ("ollama", True, "free")])
@pytest.mark.parametrize("prices", [{}, {"price_in_per_m": 0.15}, {"price_out_per_m": 1.875},
                                    {"price_in_per_m": None, "price_out_per_m": 1.875}])
def test_rule_3_no_cost_and_not_both_prices(server, provider, known, source, prices):
    r, tracker = reply_of(server, provider, USAGE, prices)
    assert (r.cost, r.cost_known, r.cost_source) == (0, known, source)
    assert (tracker.total, tracker.unknown) == (0, 0 if known else 1)


# --- 4.4, 7: failed attempts' reported cost ------------------------------------------------------------------


def test_failed_attempts_costs_count_toward_the_cap_and_belong_to_no_reply(server):
    server.script = [{"error": {"message": "billed"}, "usage": {"cost": 0.25}},
                     {"choices": [{"finish_reason": "error", "message": {}}], "usage": {"cost": 0.5}},
                     (500, {"usage": {"cost": 1}}),
                     {"choices": [{"message": 5}], "usage": {"cost": 2.0}},
                     (503, {"usage": {"cost": "4"}}), (503, {"usage": [8]}), (503, [16]), (503, b"junk"),
                     {"error": "x", "usage": {"cost": True}}, {"error": "x", "usage": {"cost": math.nan}},
                     ok(usage={"cost": 0.125})]
    b, _, tracker = make(server, llm={"max_retries": 20})
    r = b.call(MESSAGES)
    assert (r.cost, r.failed_cost) == (0.125, 3.75)
    assert (tracker.total, tracker.unknown) == (3.875, 0)


def test_a_failed_call_carries_its_failed_attempts_cost(server):
    server.script = [{"error": "x", "usage": {"cost": 0.25}}, (402, {"error": "no credits", "usage": {"cost": 0.5}})]
    b, _, tracker = make(server)
    with pytest.raises(models.CallFailed) as e:
        b.call(MESSAGES)
    assert e.value.failed_cost == 0.75 and (tracker.total, tracker.unknown) == (0.75, 0)
    assert e.value.error.startswith("HTTP 402 for model 'm': ") and "no credits" in e.value.error
    server.script = [{"error": "x", "usage": {"cost": 0.25}}]
    b, _, tracker = make(server, llm={"max_retries": 2})
    with pytest.raises(models.CallFailed) as e:
        b.call(MESSAGES)
    assert e.value.failed_cost == 0.75 and tracker.total == 0.75 and "provider error" in e.value.error


def test_a_failed_attempts_cost_is_added_when_the_attempt_ends(server):
    seen = []
    server.script = [{"error": "x", "usage": {"cost": 0.25}}, ok()]
    tracker = models.CostTracker(None)
    spec = resolved({"provider": "openai_compatible", "model": "m", "base_url": server.url})
    b = models.backend(spec, LLM, tracker, env={}, sleep=lambda s: seen.append(tracker.total))
    b.call(MESSAGES)
    assert seen == [0.25]


def test_calls_from_several_threads_share_the_tracker(server):
    server.script = [ok(usage={"cost": 0.5})]
    b, _, tracker = make(server, cap=3.5)
    threads = [threading.Thread(target=b.call, args=(MESSAGES,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert tracker.total == 4.0 and len(server.requests) == 8 and tracker.reached()


def test_latency_covers_retries_and_waits(server, no_jitter):
    clock = Clock()
    server.script = [(500, b""), ok()]
    assert make(server, clock=clock)[0].call(MESSAGES).latency == 2


# --- 5: token charges in credits -----------------------------------------------------------------------------


@pytest.mark.parametrize("cost, rate, credits", [
    (2.0625e-05, 1, 2.1e-05),
    (0.0033, 1, 0.0033),
    (0.0000004, 1, 0.0),
    (0.0000004, 10, 0.000004),       # the multiplication comes before the rounding
    (1.23456749, 100, 123.456749),
    (0.0000005, 1, 0.0),             # ties to even on the double's exact value
    (0.0, 50, 0.0),
    (-0.5, 2, -1.0),                 # the caller skips a charge of 0 or less
])
def test_charge(cost, rate, credits):
    assert models.charge(cost, rate) == credits == round(cost * rate, 6)


@pytest.mark.parametrize("cost, rate", [(1e300, 1e10), (-1e300, 1e10), (math.inf, 1), (math.inf, 0), (math.nan, 1)])
def test_a_charge_that_is_not_finite_stops_where_it_is_computed(cost, rate):
    """config.md, 6, "Finiteness during an episode"."""
    with pytest.raises(ArithmeticError, match=r"^a token charge is not finite: (inf|-inf|nan)$"):
        models.charge(cost, rate)
    assert models.charge(1e300, 1e8) == 1e308


# --- 7: the cost tracker and the cap -------------------------------------------------------------------------


def test_the_cap_is_reached_at_or_above_it():
    tracker = models.CostTracker(20)
    tracker.add(19.99)
    assert not tracker.reached() and tracker.check() is None
    tracker.add(0.01)
    tracker.add(0.125, known=False)
    assert tracker.reached() and (tracker.total, tracker.unknown) == (20.125, 1)
    with pytest.raises(models.CostCapReached) as e:
        tracker.check()
    assert str(e.value) == "cost cap $20.00 reached (spent $20.12)"
    assert not isinstance(e.value, models.CallFailed)
    exact = models.CostTracker(0.5)
    exact.add(0.5)
    assert exact.reached()


def test_no_cap_is_never_reached():
    tracker = models.CostTracker(None)
    tracker.add(1e9)
    assert not tracker.reached() and tracker.check() is None


def test_the_tracker_under_threads():
    tracker = models.CostTracker(100)

    def work():
        for i in range(2000):
            tracker.add(0.25, known=i % 2 == 0)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert (tracker.total, tracker.unknown) == (4000.0, 8000)


# --- 3: setting up -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("spec, variable", [
    (BUYERS, "OPENROUTER_API_KEY"),
    ({**BUYERS, "api_key_env": "OTHER_KEY"}, "OTHER_KEY"),
    ({"provider": "ollama", "model": "m", "api_key_env": "OLLAMA_KEY"}, "OLLAMA_KEY"),
    ({"provider": "openai_compatible", "model": "m", "base_url": "http://127.0.0.1:1/v1", "api_key_env": "K"}, "K"),
    ({"provider": "mock", "api_key_env": "MOCK_KEY"}, "MOCK_KEY"),
])
@pytest.mark.parametrize("env", [{}, {"OPENROUTER_API_KEY": "", "OTHER_KEY": "", "OLLAMA_KEY": "", "K": "",
                                      "MOCK_KEY": ""}, {"UNRELATED": "x"}])
def test_an_unset_or_empty_key_variable_fails_the_setup(spec, variable, env):
    with pytest.raises(models.SetupError) as e:
        models.backend(resolved(spec), LLM, models.CostTracker(None), env=env)
    assert str(e.value) == f"set {variable} in your environment or in .env"


def test_the_key_comes_from_the_environment_or_the_env_file(server, tmp_path, monkeypatch):
    # models.md 3: a .env file may supply variables, the real environment taking precedence
    (tmp_path / ".env").write_text("# keys\nexport FILE_KEY='from file'\nBOTH_KEY=from file\n\nNOT A PAIR\n")
    server.script = [ok(), ok(), ok()]
    spec = {"provider": "openai_compatible", "model": "m", "base_url": server.url}
    models.backend(resolved({**spec, "api_key_env": "FILE_KEY"}), LLM, models.CostTracker(None)).call(MESSAGES)
    assert server.requests[-1].headers["Authorization"] == "Bearer from file"
    monkeypatch.setenv("BOTH_KEY", "from os.environ")
    models.backend(resolved({**spec, "api_key_env": "BOTH_KEY"}), LLM, models.CostTracker(None)).call(MESSAGES)
    assert server.requests[-1].headers["Authorization"] == "Bearer from os.environ"
    make(server, {"api_key_env": "FIRST_KEY"}, env={"FIRST_KEY": "from env"})[0].call(MESSAGES)
    assert server.requests[-1].headers["Authorization"] == "Bearer from env"
    with pytest.raises(models.SetupError, match="set FILE_KEY in your environment or in .env"):
        make(server, {"api_key_env": "FILE_KEY"}, env={})  # a given env is the only source


def test_reading_an_env_file(tmp_path):
    assert models.read_env_file(str(tmp_path / "none")) == {}
    assert models.read_env_file(str(tmp_path)) == {}  # a folder cannot be read: no variables
    (tmp_path / ".env").write_text('﻿A="x=1 # kept"\n  B = 2 # note\nC=\nD="unclosed\nexport\tE=e\n=f\n',
                                   encoding="utf-8")
    assert models.read_env_file(str(tmp_path / ".env")) == {"A": "x=1 # kept", "B": "2", "C": "", "D": '"unclosed',
                                                            "E": "e"}


def test_an_empty_environment_variable_falls_back_to_the_env_file(server, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("EMPTY_KEY=from file\n")
    monkeypatch.setenv("EMPTY_KEY", "")
    server.script = [ok()]
    spec = {"provider": "openai_compatible", "model": "m", "base_url": server.url, "api_key_env": "EMPTY_KEY"}
    models.backend(resolved(spec), LLM, models.CostTracker(None)).call(MESSAGES)
    assert server.requests[-1].headers["Authorization"] == "Bearer from file"


def test_the_key_is_read_when_the_backend_is_set_up(server):
    env = {"K": "first"}
    server.script = [ok()]
    b, _, _ = make(server, {"api_key_env": "K"}, env=env)
    env["K"] = "second"
    b.call(MESSAGES)
    assert server.requests[0].headers["Authorization"] == "Bearer first"


@pytest.mark.parametrize("spec", [
    {"provider": "openai_compatible", "model": "m"},
    {"provider": "openai_compatible", "model": "m", "base_url": "localhost:8000/v1"},
    {"provider": "ollama", "model": "m", "base_url": "ftp://127.0.0.1/v1"},
])
def test_a_base_url_that_is_not_http_fails_the_setup(spec):
    with pytest.raises(models.SetupError, match="base_url"):
        models.backend(resolved(spec), LLM, models.CostTracker(None), env={})


def test_the_mock_provider_is_market_mocks_backend(monkeypatch):
    seen, made = [], object()
    stub = types.ModuleType("market.mock")
    stub.backend = lambda spec: seen.append(spec) or made
    monkeypatch.setitem(sys.modules, "market.mock", stub)
    monkeypatch.setattr(sys.modules["market"], "mock", stub, raising=False)  # where an imported market.mock is found
    tracker = models.CostTracker(0.01)
    spec = resolved({"provider": "mock", "price_in_per_m": 0.15, "price_out_per_m": 1.875})
    assert models.backend(spec, LLM, tracker, env={}) is made and seen == [spec]
    keyed = {**spec, "api_key_env": "MOCK_KEY"}
    assert models.backend(keyed, LLM, tracker, env={"MOCK_KEY": "unused"}) is made
    with pytest.raises(models.SetupError, match="set MOCK_KEY"):
        models.backend(keyed, LLM, tracker, env={})
    assert len(seen) == 2 and tracker.total == 0


@pytest.mark.parametrize("timeout_s", [1e10, 1e300])
def test_a_time_limit_longer_than_a_socket_takes_is_no_defect(server, timeout_s):
    # config.md 3.4.3: llm.timeout_s is any number > 0; a huge one must not end the episode with an error
    server.script = [ok()]
    backend = make(server, llm={**LLM, "timeout_s": timeout_s})[0]
    assert backend.call(MESSAGES).text == "hi"


@pytest.mark.parametrize("base_url", [
    "http://127.0.0.1:99999/v1", "http://host:abc/v1", "http://[::1/v1", "http://host/my api/v1",
    "http://host/v\u00e9/v1", "http://a..b/v1", "ftp://host/v1", "localhost:8000/v1",
])
def test_a_base_url_that_cannot_be_requested_fails_the_setup(base_url):
    # models.md 3, "Setting up": a backend that cannot be set up ends the episode before any day is played
    spec = resolved({"provider": "openai_compatible", "model": "m", "base_url": base_url})
    with pytest.raises(models.SetupError, match="base_url"):
        models.backend(spec, LLM, models.CostTracker(None))


@pytest.mark.parametrize("key", ["sk-1\n", "sk-\u00e9", "sk\x00"])
def test_a_key_a_request_cannot_carry_fails_the_setup(key):
    spec = resolved({"provider": "openai_compatible", "model": "m", "base_url": "http://127.0.0.1:1/v1",
                     "api_key_env": "K"})
    with pytest.raises(models.SetupError, match="K"):
        models.backend(spec, LLM, models.CostTracker(None), env={"K": key})


def test_a_mock_seats_key_only_has_to_be_set(monkeypatch):
    # models.md 3: "a mock spec that names an `api_key_env` needs that variable set, though the mock never uses the key"
    stub = types.ModuleType("market.mock")
    stub.backend = lambda spec: "the mock"
    monkeypatch.setitem(sys.modules, "market.mock", stub)
    monkeypatch.setattr(sys.modules["market"], "mock", stub, raising=False)
    spec = resolved({"provider": "mock", "api_key_env": "MOCK_KEY"})
    assert models.backend(spec, LLM, models.CostTracker(None), env={"MOCK_KEY": "cl\u00e9\n"}) == "the mock"
