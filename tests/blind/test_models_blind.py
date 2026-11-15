"""Blind tests for spec/models.md through the documented market.models API.

No implementation inspection, external network, or filesystem writes.
Caller-owned behavior and market.mock's implementation are outside this API.
Run with bytecode and pytest's cache disabled if execution must be read-only:
    PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider this_file.py
"""

import builtins
import copy
import errno
import http.client
import io
import json
import math
import pathlib
import queue
import random
import socket
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from market import models


LLM = {
    "temperature": 0.8,
    "max_tokens": 64000,
    "timeout_s": 1200,
    "max_retries": 5,
    "network_patience_s": 900,
}
MESSAGES = [
    {"role": "system", "content": "system"},
    {"role": "user", "content": "user"},
]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "compute",
            "description": "Compute.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "act",
            "description": "Act.",
            "parameters": {
                "type": "object",
                "properties": {
                    "actions": {"type": "array", "items": {"type": "object"}},
                    "memory": {"type": "string"},
                },
                "required": ["memory"],
            },
        },
    },
]
NAMED_ACT = {"type": "function", "function": {"name": "act"}}
EMPTY = [None, False, 0, 0.0, "", [], {}]
ABSENT = object()
# config.md 3.4.1, "when absent": what config.resolve stores in a participant's
# spec that names none of these at the default llm block.
WHEN_ABSENT = {"temperature": 0.8, "max_tokens": 64000, "extra_body": {}}


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.waits = []
        self.on_sleep = None

    def clock(self):
        return self.now

    def sleep(self, seconds):
        assert math.isfinite(seconds)
        assert seconds > 0
        assert len(self.waits) < 100, "Unbounded retry loop"
        self.waits.append(seconds)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep()


class Wire:
    def __init__(self):
        self.responses = queue.Queue()
        self.requests = []
        self.lock = threading.Lock()

    def put(self, body=None, status=200, headers=None, before=None,
            raw=None, wire=None, close=False):
        if raw is None:
            raw = json.dumps({} if body is None else body).encode("ascii")
        self.responses.put({
            "status": status,
            "headers": headers or {},
            "raw": raw,
            "wire": wire,
            "close": close,
            "before": before,
        })


@pytest.fixture(autouse=True)
def localhost_only(monkeypatch):
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def checked_address(address):
        assert isinstance(address, tuple), "Only localhost TCP is allowed"
        assert address[0] in ("127.0.0.1", "::1"), address

    def connect(sock, address):
        checked_address(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        checked_address(address)
        return original_connect_ex(sock, address)

    def getaddrinfo(host, port, *args, **kwargs):
        assert host in ("localhost", "127.0.0.1", "::1"), host
        if host == "localhost":
            host = "127.0.0.1"
        return original_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    for name in (
        "http_proxy", "https_proxy", "all_proxy",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")


@pytest.fixture(autouse=True)
def isolated_dotenv(monkeypatch):
    """Supply an absent .env without reading or writing one."""
    original_open = builtins.open
    original_io_open = io.open
    original_exists = pathlib.Path.exists
    original_is_file = pathlib.Path.is_file

    def is_dotenv(path):
        try:
            return pathlib.Path(path).name == ".env"
        except TypeError:
            return False

    def open_file(path, *args, **kwargs):
        if is_dotenv(path):
            raise FileNotFoundError(".env")
        return original_open(path, *args, **kwargs)

    def io_open_file(path, *args, **kwargs):
        if is_dotenv(path):
            raise FileNotFoundError(".env")
        return original_io_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_file)
    monkeypatch.setattr(io, "open", io_open_file)
    monkeypatch.setattr(
        pathlib.Path, "exists",
        lambda path: False if is_dotenv(path) else original_exists(path),
    )
    monkeypatch.setattr(
        pathlib.Path, "is_file",
        lambda path: False if is_dotenv(path) else original_is_file(path),
    )


@pytest.fixture
def wire():
    state = Wire()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = {
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "raw": raw,
                "body": json.loads(raw),
            }
            with state.lock:
                state.requests.append(request)
            try:
                response = state.responses.get_nowait()
            except queue.Empty:
                response = {
                    "status": 599,
                    "headers": {},
                    "raw": b'{"error": "test response queue exhausted"}',
                    "wire": None,
                    "close": False,
                    "before": None,
                }
            if response["before"] is not None:
                response["before"]()
            self.close_connection = True
            if response["close"]:
                return
            try:
                if response["wire"] is not None:
                    self.wfile.write(response["wire"])
                    self.wfile.flush()
                    return
                self.send_response(response["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response["raw"])))
                self.send_header("Connection", "close")
                for key, value in response["headers"].items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(response["raw"])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.base_url = "http://127.0.0.1:%d/v1" % server.server_port
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def make_backend(wire):
    def make(provider="openrouter", spec=None, llm=None, summarizer=False,
             tracker=None, timer=None, env=None):
        supplied = {
            "provider": provider,
            "model": "blind/model",
            "base_url": wire.base_url,
            "api_key_env": None,
        }
        supplied.update(spec or {})
        settings = dict(LLM)
        settings.update(llm or {})
        # config.md 3.4.1, "when absent": the model layer takes a spec as config.resolve
        # returns it, so the fixture fills these three keys the way resolve does.
        supplied.setdefault(
            "temperature", None if summarizer else settings["temperature"],
        )
        supplied.setdefault("max_tokens", settings["max_tokens"])
        supplied.setdefault("extra_body", {})
        if tracker is None:
            tracker = models.CostTracker(None)
        if timer is None:
            timer = FakeTime()
        backend = models.backend(
            supplied,
            settings,
            tracker,
            env={} if env is None else env,
            sleep=timer.sleep,
            clock=timer.clock,
        )
        return backend, tracker, timer
    return make


def response(message=None, usage=ABSENT, **choice_fields):
    choice = {"message": {} if message is None else message}
    choice.update(choice_fields)
    body = {"choices": [choice]}
    if usage is not ABSENT:
        body["usage"] = usage
    return body


def set_path(body, path, value):
    node = body
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return body


# models.md 4.8; README.md, Number formats and rounding.
@pytest.mark.parametrize("text, expected", [
    ("", 1),
    ("a", 1),
    ("abc", 1),
    ("abcd", 1),
    ("abcdefg", 1),
    ("abcdefgh", 2),
    ("a" * 399, 99),
    ("a" * 400, 100),
    ("\u00e9" * 8, 2),
    ("\U0001f600" * 8, 2),
    ("\ud800" * 8, 2),
    ("\n\t\x00\r" * 2, 2),
])
def test_estimate_counts_code_points_and_floors(text, expected):
    assert models.estimate(text) == expected


# models.md 4.8: message order, default JSON spelling, and excluded fields.
def test_request_text_exact_serialization():
    messages = [
        {"role": "system", "content": "A", "reasoning": "ignored"},
        {"role": "user"},
        {"role": "user", "content": None},
        {
            "role": "assistant",
            "content": "B",
            "reasoning": "also ignored",
            "tool_calls": [
                {"id": "\u00e9", "function": {"arguments": "\ud800"}}
            ],
        },
        {"role": "tool", "tool_call_id": "ignored", "content": "C"},
    ]
    body = {"messages": messages, "tools": [{"z": 1, "a": 2}]}
    expected = (
        'AB[{"id": "\\u00e9", "function": {"arguments": "\\ud800"}}]'
        'C[{"z": 1, "a": 2}]'
    )
    assert models.request_text(body) == expected


# models.md 4.8: absent or empty calls and tools contribute no JSON.
@pytest.mark.parametrize("empty", EMPTY)
def test_request_text_ignores_empty_calls(empty):
    body = {"messages": [{"content": "abcd", "tool_calls": empty}], "tools": []}
    assert models.request_text(body) == "abcd"


# models.md 4.8: JSON escapes, insertion order, and no counted metadata.
def test_request_text_unicode_and_controls():
    body = {
        "messages": [
            {"content": "\U0001f600\n"},
            {"tool_calls": [{"z": "\U0001f600", "a": "\n\\\""}]},
        ]
    }
    assert models.request_text(body) == (
        '\U0001f600\n[{"z": "\\ud83d\\ude00", "a": "\\n\\\\\\""}]'
    )


# models.md 5; config.md 3.4.4; README.md rounding.
@pytest.mark.parametrize("cost, rate, expected", [
    (0.0, 1.0, 0.0),
    (2.0625e-05, 1.0, 0.000021),
    (2.0625e-05, 1000.0, 0.020625),
    (0.0000003, 2.0, 0.000001),
    (0.0078125, 1.0, 0.007812),
    (0.0234375, 1.0, 0.023438),
    (-0.0078125, 1.0, -0.007812),
    (-0.0234375, 1.0, -0.023438),
    (1.23456789, 1.0, 1.234568),
    (1.0, 0.000001, 0.000001),
])
def test_charge_multiplies_before_ties_to_even_rounding(cost, rate, expected):
    assert models.charge(cost, rate) == expected


# models.md 5; README.md rounding preserves negative zero.
def test_charge_preserves_negative_zero():
    result = models.charge(-0.0000001, 1.0)
    assert result == 0.0
    assert math.copysign(1.0, result) == -1.0


# models.md 2.2 and the documented Reply construction interface.
def test_reply_defaults_and_equality():
    reply = models.Reply()
    assert reply.text == ""
    assert reply.tool_calls is None
    assert reply.reasoning == ""
    assert (reply.input_tokens, reply.output_tokens, reply.reasoning_tokens) == (0, 0, 0)
    assert reply.cost == 0.0
    assert reply.cost_known is True
    assert reply.input_estimated is False
    assert reply.output_estimated is False
    assert reply.cost_source == "reported"
    assert reply.failed_cost == 0.0
    assert reply.finish_reason is None
    assert reply.served_by is None
    assert reply.latency is None
    assert models.Reply(text="x") == models.Reply(text="x")
    assert models.Reply(text="x") != models.Reply(text="y")


# models.md 2.4: failed calls carry only their error and failed-attempt cost.
def test_call_failed_fields():
    failure = models.CallFailed("model failed", failed_cost=0.125)
    assert str(failure) == failure.error == "model failed"
    assert failure.failed_cost == 0.125
    assert models.CallFailed("model failed").failed_cost == 0.0


# models.md 3: setup checks all named keys, including mock keys.
@pytest.mark.parametrize("provider", [
    "openrouter", "ollama", "openai_compatible", "mock",
])
@pytest.mark.parametrize("env", [{}, {"BLIND_API_KEY": ""}])
def test_named_key_is_required_at_setup(provider, env):
    with pytest.raises(models.SetupError) as caught:
        models.backend(
            {
                "provider": provider,
                "model": "blind/model",
                "base_url": "http://localhost:1/v1",
                "api_key_env": "BLIND_API_KEY",
            },
            dict(LLM),
            models.CostTracker(None),
            env=env,
        )
    assert "BLIND_API_KEY" in str(caught.value)  # the text's wording is free; it names the variable


# models.md 3: OpenRouter's default key variable.
def test_openrouter_default_key_is_required():
    with pytest.raises(models.SetupError) as caught:
        models.backend(
            {"provider": "openrouter", "model": "blind/model"},
            dict(LLM), models.CostTracker(None), env={},
        )
    assert "OPENROUTER_API_KEY" in str(caught.value)  # the text's wording is free; it names the variable


# models.md 3; config.md 3.4.1: openai_compatible needs a base URL.
@pytest.mark.parametrize("base", [
    ABSENT, "", "localhost:1234", "ftp://localhost/v1", "http://", "https:///v1",
])
def test_invalid_base_url_is_setup_failure(base):
    spec = {"provider": "openai_compatible", "model": "blind/model"}
    if base is not ABSENT:
        spec["base_url"] = base
    with pytest.raises(models.SetupError) as caught:
        models.backend(spec, dict(LLM), models.CostTracker(None), env={})
    assert "base_url" in str(caught.value)


# models.md 3 and 4.1: default addresses, inspected before any socket is opened.
@pytest.mark.parametrize("provider, host, port, path, secure", [
    ("openrouter", "openrouter.ai", 443, "/api/v1/chat/completions", True),
    ("ollama", "localhost", 11434, "/v1/chat/completions", False),
])
def test_default_endpoint_without_network(monkeypatch, provider, host, port,
                                          path, secure):
    class CapturedRequest(BaseException):
        pass

    seen = []

    def capture(connection, method, url, body=None, headers=None, **kwargs):
        seen.append((
            connection.host, connection.port, method, url,
            isinstance(connection, http.client.HTTPSConnection),
        ))
        raise CapturedRequest()

    monkeypatch.setattr(http.client.HTTPConnection, "request", capture)
    # Adjudication: whether the layer connects before or inside request() is
    # the implementer's choice (models.md 1); no socket is opened either way.
    monkeypatch.setattr(http.client.HTTPConnection, "connect", lambda connection: None)
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", lambda connection: None)
    backend = models.backend(
        {"provider": provider, "model": "blind/model", "api_key_env": None,
         **WHEN_ABSENT},
        dict(LLM), models.CostTracker(None), env={},
    )
    assert seen == []
    with pytest.raises(CapturedRequest):
        backend.call(MESSAGES)
    assert seen == [(host, port, "POST", path, secure)]


# models.md 3 and 4.1: keys are read at setup, override defaults, and are optional.
@pytest.mark.parametrize("provider", [
    "openrouter", "ollama", "openai_compatible",
])
@pytest.mark.parametrize("key_name", [None, "BLIND_API_KEY"])
def test_authorization_and_provider_headers(wire, make_backend, provider, key_name):
    env = {"BLIND_API_KEY": "first", "OPENROUTER_API_KEY": "wrong"}
    backend, _, _ = make_backend(
        provider=provider, spec={"api_key_env": key_name}, env=env,
    )
    assert wire.requests == []
    env["BLIND_API_KEY"] = "second"
    wire.put()
    backend.call(MESSAGES)
    headers = wire.requests[0]["headers"]
    assert headers["content-type"].split(";")[0] == "application/json"
    if key_name is None:
        assert "authorization" not in headers
    else:
        assert headers["authorization"] == "Bearer first"
    if provider == "openrouter":
        assert headers["x-title"]
    else:
        assert "x-title" not in headers


# models.md 3: the real environment is the default environment mapping.
def test_default_environment_mapping(wire, monkeypatch):
    monkeypatch.setenv("BLIND_API_KEY", "from-process")
    backend = models.backend(
        {
            "provider": "openai_compatible",
            "model": "blind/model",
            "base_url": wire.base_url,
            "api_key_env": "BLIND_API_KEY",
            **WHEN_ABSENT,
        },
        dict(LLM), models.CostTracker(None),
    )
    wire.put()
    backend.call(MESSAGES)
    assert wire.requests[0]["headers"]["authorization"] == "Bearer from-process"


# models.md 1, 2.1, 4.1, 4.2; config.md 3.4.3.
@pytest.mark.parametrize("provider", [
    "openrouter", "ollama", "openai_compatible",
])
def test_minimal_request_exact_fields(wire, make_backend, provider):
    backend, _, _ = make_backend(
        provider=provider, spec={"base_url": wire.base_url + "///"},
    )
    wire.put()
    backend.call(MESSAGES)
    request = wire.requests[0]
    assert request["method"] == "POST"
    assert request["path"] == "/v1/chat/completions"
    expected = {
        "model": "blind/model",
        "messages": MESSAGES,
        "max_tokens": 64000,
        "temperature": 0.8,
    }
    if provider == "openrouter":
        expected["usage"] = {"include": True}
    assert request["body"] == expected


# models.md 2.1, 4.2, 6; config.md 3.4.1 and 3.4.3.
@pytest.mark.parametrize("summarizer", [False, True])
@pytest.mark.parametrize("temperature", [ABSENT, None, 0.0, 2.5])
@pytest.mark.parametrize("llm_temperature", [None, 0.8])
def test_temperature_presence(wire, make_backend, summarizer, temperature,
                              llm_temperature):
    spec = {}
    if temperature is not ABSENT:
        spec["temperature"] = temperature
    backend, _, _ = make_backend(
        spec=spec, summarizer=summarizer,
        llm={"temperature": llm_temperature},
    )
    wire.put()
    backend.call(MESSAGES)
    body = wire.requests[0]["body"]
    expected = temperature
    if temperature is ABSENT:
        expected = None if summarizer else llm_temperature
    if expected is None:
        assert "temperature" not in body
    else:
        assert body["temperature"] == expected


# models.md 2.1 and 6; config.md 3.4.1: no independent per-call token limit.
@pytest.mark.parametrize("summarizer", [False, True])
@pytest.mark.parametrize("limit", [1, 8000, 64000, 1000000])
def test_spec_token_limit_overrides_llm(wire, make_backend, summarizer, limit):
    backend, _, _ = make_backend(
        spec={"max_tokens": limit}, summarizer=summarizer,
        llm={"max_tokens": 123},
    )
    wire.put()
    backend.call(MESSAGES)
    assert wire.requests[0]["body"]["max_tokens"] == limit


# models.md 2.1, 4.2 and 6: absent summarizer limit inherits llm.
def test_summarizer_inherits_only_token_limit(wire, make_backend):
    backend, _, _ = make_backend(
        summarizer=True, llm={"max_tokens": 7, "temperature": 1.5},
    )
    wire.put()
    backend.call(MESSAGES)
    body = wire.requests[0]["body"]
    assert body["max_tokens"] == 7
    assert "temperature" not in body
    assert "tools" not in body
    assert "tool_choice" not in body


# models.md 2.1 and 4.2: functions and tool_choice are conditional.
@pytest.mark.parametrize("functions", [None, [], TOOLS, TOOLS[1:]])
@pytest.mark.parametrize("choice", [None, "required", NAMED_ACT])
def test_tools_and_tool_choice(wire, make_backend, functions, choice):
    backend, _, _ = make_backend()
    wire.put()
    backend.call(MESSAGES, functions=functions, tool_choice=choice)
    body = wire.requests[0]["body"]
    if functions:
        assert body["tools"] == functions
        if choice is not None:
            assert body["tool_choice"] == choice
        else:
            assert "tool_choice" not in body
    else:
        assert "tools" not in body
        assert "tool_choice" not in body


# models.md 2.3 and 4.1; turns.md 2 and 11.4: carry supplied messages unchanged.
def test_conversation_round_trip_and_surrogate_escape(wire, make_backend):
    calls = [{
        "id": {"odd": 1},
        "type": "function",
        "function": {"name": "act", "arguments": "{\"memory\":\"\\ud800\"}"},
        "extra": [1, None],
    }]
    messages = [
        {"role": "system", "content": "system \u00e9"},
        {"role": "user", "content": "view \U0001f600"},
        {
            "role": "assistant",
            "content": "  <think>unstripped input</think>\n\ud800",
            "tool_calls": calls,
            "reasoning": "private reasoning",
        },
        {"role": "tool", "tool_call_id": {"odd": 1}, "content": "answer"},
        {"role": "assistant", "content": "(an empty reply)"},
        {"role": "user", "content": "correction\n\x00"},
    ]
    before = copy.deepcopy(messages)
    backend, _, _ = make_backend()
    wire.put()
    backend.call(messages, TOOLS[1:], NAMED_ACT)
    request = wire.requests[0]
    assert request["body"]["messages"] == before
    assert messages == before
    assert b"\\ud800" in request["raw"]
    request["raw"].decode("ascii")


# models.md 4.2: extra_body is a last, shallow, unrestricted override.
def test_extra_body_overrides_all_fields(wire, make_backend):
    extras = {
        "model": "wire/model",
        "messages": [{"role": "user", "content": "wire"}],
        "max_tokens": 1,
        "temperature": None,
        "usage": {"custom": True},
        "tools": [],
        "tool_choice": "auto",
        "seed": 42,
        "top_p": 0.25,
        "reasoning": {"effort": "high"},
        "provider": {"order": ["local"]},
        "stream": False,
    }
    saved = copy.deepcopy(extras)
    backend, _, _ = make_backend(spec={"extra_body": extras})
    wire.put(response(usage={"prompt_tokens": 2, "completion_tokens": 3}))
    backend.call(MESSAGES, TOOLS, "required")
    assert wire.requests[0]["body"] == saved
    assert extras == saved
    # Sections 4.2 and 4.8 disagree about estimates after this replacement.


# models.md 4.2: extra_body can add tool_choice even without caller tools.
def test_extra_body_can_add_otherwise_omitted_keys(wire, make_backend):
    backend, _, _ = make_backend(
        summarizer=True,
        spec={"extra_body": {"temperature": 0.2, "tool_choice": "auto"}},
    )
    wire.put()
    backend.call(MESSAGES)
    body = wire.requests[0]["body"]
    assert body["temperature"] == 0.2
    assert body["tool_choice"] == "auto"


# models.md 4.2 worked requests; config.md 2 and 3.4.
@pytest.mark.parametrize("summarizer", [False, True])
def test_default_configuration_worked_request(wire, make_backend, summarizer):
    if summarizer:
        spec = {
            "model": "anthropic/claude-sonnet-5.5",
            "price_in_per_m": 2.0,
            "price_out_per_m": 10.0,
            "temperature": None,
            "max_tokens": 8000,
            "extra_body": {"reasoning": {"effort": "high"}},
        }
        expected = {
            "model": "anthropic/claude-sonnet-5.5",
            "messages": MESSAGES,
            "max_tokens": 8000,
            "usage": {"include": True},
            "reasoning": {"effort": "high"},
        }
        functions, choice = None, None
    else:
        routing = {"quantizations": ["bf16", "fp16", "fp8", "unknown"]}
        spec = {
            "model": "qwen/qwen3.8-27b",
            "price_in_per_m": 0.15,
            "price_out_per_m": 1.875,
            "extra_body": {"provider": routing},
        }
        expected = {
            "model": "qwen/qwen3.8-27b",
            "messages": MESSAGES,
            "max_tokens": 64000,
            "temperature": 0.8,
            "usage": {"include": True},
            "tools": TOOLS,
            "tool_choice": "required",
            "provider": routing,
        }
        functions, choice = TOOLS, "required"
    backend, _, _ = make_backend(spec=spec, summarizer=summarizer)
    wire.put()
    backend.call(MESSAGES, functions, choice)
    assert wire.requests[0]["body"] == expected


# models.md 2.4 and 4.7: all ordinary finish reasons are successful replies.
@pytest.mark.parametrize("finish", [
    None, "stop", "tool_calls", "length", "content_filter", "other",
])
def test_finish_reason_is_preserved_and_unusable_replies_are_charged(
        wire, make_backend, finish):
    backend, tracker, timer = make_backend()
    wire.put(response(
        {"content": ""}, {"cost": 0.125}, finish_reason=finish,
    ))
    reply = backend.call(MESSAGES)
    assert reply.finish_reason == finish
    assert reply.text == ""
    assert reply.cost == 0.125
    assert tracker.total == 0.125
    assert timer.waits == []


# models.md 4.7: only the first choice is read.
def test_first_choice_only(wire, make_backend):
    backend, _, timer = make_backend()
    wire.put({
        "choices": [
            {"message": {"content": "first"}, "finish_reason": "stop"},
            {"finish_reason": "error", "message": {"tool_calls": "bad"}},
        ],
    })
    reply = backend.call(MESSAGES)
    assert reply.text == "first"
    assert reply.finish_reason == "stop"
    assert timer.waits == []


# models.md 4.7: text is not stripped or interpreted by the model layer.
@pytest.mark.parametrize("content, expected", [
    (" \n<think>hidden</think>visible\t", " \n<think>hidden</think>visible\t"),
    ("\ud800\u00e9\U0001f600", "\ud800\u00e9\U0001f600"),
    ([{"text": "a"}, "skip", 7, None, {}, {"text": "b"}], "ab"),
    ([{"text": ""}, {"text": "\n"}, {"text": "\u00e9"}], "\n\u00e9"),
])
def test_visible_content(wire, make_backend, content, expected):
    backend, _, _ = make_backend()
    wire.put(response({"content": content}))
    assert backend.call(MESSAGES).text == expected


# models.md 4.4 and 4.7: empty structural values are never malformed.
@pytest.mark.parametrize("path", [
    ("choices",),
    ("choices", 0, "message"),
    ("choices", 0, "message", "content"),
    ("choices", 0, "message", "tool_calls"),
    ("usage",),
    ("usage", "completion_tokens_details"),
])
@pytest.mark.parametrize("empty", EMPTY)
def test_empty_structural_values_are_accepted(wire, make_backend, path, empty):
    body = response(
        {"content": "", "tool_calls": []},
        {"completion_tokens_details": {}},
    )
    set_path(body, path, empty)
    backend, _, timer = make_backend()
    wire.put(body)
    reply = backend.call(MESSAGES)
    assert reply.text == ""
    assert reply.tool_calls is None
    assert reply.reasoning_tokens == 0
    assert timer.waits == []


# models.md 4.7: empty function values remain as returned.
@pytest.mark.parametrize("empty", EMPTY)
def test_empty_function_is_preserved(wire, make_backend, empty):
    calls = [{"function": empty}]
    backend, _, _ = make_backend()
    wire.put(response({"tool_calls": calls}))
    assert backend.call(MESSAGES).tool_calls == calls


# models.md 4.7: shape validation does not validate function semantics.
def test_function_calls_are_preserved_verbatim(wire, make_backend):
    calls = [
        {},
        {"id": "same", "extra": {"x": [1, None]}},
        {"id": "same", "type": 7, "function": {"name": ["act"], "arguments": 9}},
        {"function": {"name": "act", "arguments": {"memory": "\ud800"}}},
        {"id": None, "function": {"name": None, "arguments": False}},
    ]
    backend, _, _ = make_backend()
    wire.put(response({"tool_calls": calls}))
    assert backend.call(MESSAGES).tool_calls == calls


# models.md 4.7 and 4.8: reasoning precedence and JSON representation.
@pytest.mark.parametrize("message, expected", [
    ({}, ""),
    ({"reasoning": "primary", "reasoning_content": "fallback"}, "primary"),
    ({"reasoning": "", "reasoning_content": "fallback"}, "fallback"),
    ({"reasoning": False, "reasoning_content": "fallback"}, "fallback"),
    ({"reasoning": {}, "reasoning_content": []}, ""),
    ({"reasoning": ["\u00e9", "\U0001f600"]},
     '["\\u00e9", "\\ud83d\\ude00"]'),
    ({"reasoning_content": {"b": 2, "a": 1}}, '{"b": 2, "a": 1}'),
    ({"reasoning": True}, "true"),
    ({"reasoning": 1.25}, "1.25"),
])
def test_reasoning(wire, make_backend, message, expected):
    backend, _, _ = make_backend()
    wire.put(response(message))
    assert backend.call(MESSAGES).reasoning == expected


# models.md 4.7: served-by is the top-level provider, absent when empty.
@pytest.mark.parametrize("provider", EMPTY + ["Together", {"name": "upstream"}])
def test_served_by(wire, make_backend, provider):
    backend, _, _ = make_backend()
    body = response()
    body["provider"] = provider
    wire.put(body)
    assert backend.call(MESSAGES).served_by == (provider if provider else None)


# models.md 4.7; turns.md 7.2: duplicate JSON keys keep the last value.
def test_duplicate_response_keys(wire, make_backend):
    backend, _, _ = make_backend()
    wire.put(raw=(
        b'{"choices":[{"message":{"content":"old","content":"new"}}],'
        b'"usage":{"prompt_tokens":99,"prompt_tokens":10.7,'
        b'"completion_tokens":3,"cost":5,"cost":0},'
        b'"provider":"old","provider":"new"}'
    ))
    reply = backend.call(MESSAGES)
    assert reply.text == "new"
    assert reply.input_tokens == 10
    assert reply.cost == 0.0
    assert reply.served_by == "new"


# models.md 4.8 and 4.9: worked example, including the two empty-string quotes.
def test_estimation_and_pricing_worked_example(wire, make_backend):
    backend, tracker, _ = make_backend(spec={
        "price_in_per_m": 0.15, "price_out_per_m": 1.875,
    })
    wire.put(response({"content": "hello world!!"}))
    reply = backend.call([{"role": "user", "content": "x" * 400}])
    assert reply.input_tokens == 100
    assert reply.output_tokens == 3
    assert reply.input_estimated is True
    assert reply.output_estimated is True
    assert reply.reasoning_tokens == 0
    assert reply.cost == 2.0625e-05
    assert reply.cost_known is True
    assert reply.cost_source == "computed"
    assert tracker.total == reply.cost
    assert tracker.unknown == 0


# models.md 4.8: reported zero is not replaced by an estimate.
def test_reported_zero_worked_example(wire, make_backend):
    backend, _, _ = make_backend()
    wire.put(response(
        {"content": "hi"},
        {"prompt_tokens": 0, "completion_tokens": 0},
    ))
    reply = backend.call([{"role": "user", "content": "x" * 400}])
    assert (reply.input_tokens, reply.output_tokens) == (0, 0)
    assert reply.input_estimated is False
    assert reply.output_estimated is False


# models.md 4.8: counts independently use missing/null estimates.
@pytest.mark.parametrize("prompt, completion, expected, flags", [
    (ABSENT, ABSENT, (100, 3), (True, True)),
    (None, None, (100, 3), (True, True)),
    (0, None, (0, 3), (False, True)),
    (None, 0, (100, 0), (True, False)),
    (10.7, 3.9, (10, 3), (False, False)),
    (-10.7, -3.9, (-10, -3), (False, False)),
])
def test_independent_counts(wire, make_backend, prompt, completion,
                            expected, flags):
    usage = {}
    if prompt is not ABSENT:
        usage["prompt_tokens"] = prompt
    if completion is not ABSENT:
        usage["completion_tokens"] = completion
    backend, _, _ = make_backend()
    wire.put(response({"content": "hello world!!"}, usage))
    reply = backend.call([{"content": "x" * 400}])
    assert (reply.input_tokens, reply.output_tokens) == expected
    assert (reply.input_estimated, reply.output_estimated) == flags


# models.md 4.8: reasoning is not estimated as visible text or added twice.
def test_reasoning_count_is_inside_output(wire, make_backend):
    backend, _, _ = make_backend(spec={
        "price_in_per_m": 1.0, "price_out_per_m": 2.0,
    })
    wire.put(response(
        {"content": "abcd", "reasoning": "r" * 4000},
        {
            "prompt_tokens": 10.7,
            "completion_tokens": 20.9,
            "completion_tokens_details": {"reasoning_tokens": 12.8},
        },
    ))
    reply = backend.call(MESSAGES)
    assert (reply.input_tokens, reply.output_tokens, reply.reasoning_tokens) == (
        10, 20, 12,
    )
    assert reply.cost == 10 * 1.0 / 1000000 + 20 * 2.0 / 1000000


# models.md 4.8: estimation includes calls but excludes hidden reasoning.
@pytest.mark.parametrize("message, output_tokens", [
    ({"content": "ab"}, 1),
    ({"content": "abcdef"}, 2),
    ({"content": "abcdefgh", "tool_calls": [{}]}, 3),
    ({"content": "", "reasoning": "x" * 4000}, 1),
    ({"content": "\U0001f600" * 6}, 2),
])
def test_output_estimate(wire, make_backend, message, output_tokens):
    backend, _, _ = make_backend()
    wire.put(response(message))
    reply = backend.call([{"content": ""}])
    assert reply.input_tokens == 1
    assert reply.output_tokens == output_tokens


# models.md 4.8: tools and assistant calls are included in input estimates.
def test_input_estimate_includes_tools_and_calls(wire, make_backend):
    messages = [
        {"content": "abcd", "reasoning": "x" * 1000, "tool_calls": [{}]},
        {"role": "tool", "tool_call_id": "x" * 1000, "content": None},
    ]
    backend, _, _ = make_backend()
    wire.put()
    reply = backend.call(messages, functions=[{}])
    # "abcd" + "[{}]" + "[{}]" is twelve characters.
    assert reply.input_tokens == 3


# models.md 4.9: reported cost wins, including zero and negative numbers.
@pytest.mark.parametrize("provider", [
    "openrouter", "ollama", "openai_compatible",
])
@pytest.mark.parametrize("cost", [0, 0.0, 0.000000123456789, 0.125, -0.125])
def test_reported_cost_precedence(wire, make_backend, provider, cost):
    backend, tracker, _ = make_backend(provider=provider, spec={
        "price_in_per_m": 99.0, "price_out_per_m": 101.0,
    })
    wire.put(response(usage={
        "prompt_tokens": 100, "completion_tokens": 3, "cost": cost,
    }))
    reply = backend.call(MESSAGES)
    assert reply.cost == cost
    assert reply.cost_known is True
    assert reply.cost_source == "reported"
    assert tracker.total == cost
    assert tracker.unknown == 0


# models.md 4.9: both prices are needed; ollama is otherwise known free.
@pytest.mark.parametrize("provider", [
    "openrouter", "ollama", "openai_compatible",
])
@pytest.mark.parametrize("prices", [
    {}, {"price_in_per_m": 2.0}, {"price_out_per_m": 3.0},
])
def test_missing_price_fallback(wire, make_backend, provider, prices):
    backend, tracker, _ = make_backend(
        provider=provider, spec=prices, summarizer=True,
    )
    wire.put(response(usage={"cost": None}))
    reply = backend.call(MESSAGES)
    free = provider == "ollama"
    assert reply.cost == 0.0
    assert reply.cost_known is free
    assert reply.cost_source == ("free" if free else "unknown")
    assert tracker.total == 0.0
    assert tracker.unknown == (0 if free else 1)


# models.md 4.9: zero prices still constitute two supplied prices.
def test_zero_prices_are_computed_not_free(wire, make_backend):
    backend, tracker, _ = make_backend(provider="ollama", spec={
        "price_in_per_m": 0.0, "price_out_per_m": 0.0,
    })
    wire.put()
    reply = backend.call(MESSAGES)
    assert reply.cost == 0.0
    assert reply.cost_source == "computed"
    assert reply.cost_known is True
    assert tracker.unknown == 0


# models.md 4.9: left-to-right double arithmetic without cost rounding.
@pytest.mark.parametrize("pt, ct, pin, pout", [
    (100, 3, 0.15, 1.875),
    (7, 11, 0.123456789, 0.987654321),
    (-10, -3, 0.15, 1.875),
    (1, 1, 1e-12, 1e-12),
])
def test_computed_cost_formula(wire, make_backend, pt, ct, pin, pout):
    backend, _, _ = make_backend(spec={
        "price_in_per_m": pin, "price_out_per_m": pout,
    })
    wire.put(response(usage={"prompt_tokens": pt, "completion_tokens": ct}))
    reply = backend.call(MESSAGES)
    expected = pt * pin / 1000000 + ct * pout / 1000000
    assert reply.cost == expected
    assert reply.cost_source == "computed"


MALFORMED_FIELDS = [
    (("choices",), {}),
    (("choices",), {"nonempty": True}),
    (("choices",), [None]),
    (("choices",), [[]]),
    (("choices", 0, "message"), "message"),
    (("choices", 0, "message"), [1]),
    (("choices", 0, "message", "tool_calls"), "calls"),
    (("choices", 0, "message", "tool_calls"), [None]),
    (("choices", 0, "message", "tool_calls"), [1]),
    (("choices", 0, "message", "tool_calls"), [{"function": "act"}]),
    (("choices", 0, "message", "tool_calls"), [{"function": [1]}]),
    (("choices", 0, "message", "content"), 1),
    (("choices", 0, "message", "content"), True),
    (("choices", 0, "message", "content"), {"text": "wrong shape"}),
    (("choices", 0, "message", "content"), [{"text": None}]),
    (("choices", 0, "message", "content"), [{"text": False}]),
    (("choices", 0, "message", "content"), [{"text": 0}]),
    (("choices", 0, "message", "content"), [{"text": []}]),
    (("usage",), "usage"),
    (("usage",), [1]),
    (("usage", "completion_tokens_details"), [1]),
    (("usage", "completion_tokens_details"), "details"),
]
# An empty object at choices is explicitly exempted by 4.7.
MALFORMED_FIELDS = MALFORMED_FIELDS[1:]


# models.md 2.4, 4.4 and 4.7: malformed responses retry, not layer defects.
@pytest.mark.parametrize("path, value", MALFORMED_FIELDS)
def test_malformed_shape_retries(wire, make_backend, path, value):
    body = response(
        {"content": "", "tool_calls": []},
        {"completion_tokens_details": {}},
    )
    set_path(body, path, value)
    wire.put(body)
    wire.put(response({"content": "recovered"}))
    backend, _, timer = make_backend(llm={"max_retries": 1})
    assert backend.call(MESSAGES).text == "recovered"
    assert len(wire.requests) == 2
    assert len(timer.waits) == 1
    assert wire.requests[0]["body"] == wire.requests[1]["body"]


# models.md 4.7: invalid numeric tokens are malformed, even if cost is reported.
@pytest.mark.parametrize("path", [
    ("usage", "prompt_tokens"),
    ("usage", "completion_tokens"),
    ("usage", "completion_tokens_details", "reasoning_tokens"),
])
@pytest.mark.parametrize("value", [
    True, "12", [1], {"x": 1}, float("nan"), float("inf"), -float("inf"),
])
def test_bad_token_counts_retry(wire, make_backend, path, value):
    body = response(usage={
        "cost": 0.125, "completion_tokens_details": {},
    })
    set_path(body, path, value)
    wire.put(body)
    wire.put(response(usage={"cost": 0.25}))
    backend, tracker, _ = make_backend(llm={"max_retries": 1})
    reply = backend.call(MESSAGES)
    assert reply.failed_cost == 0.125
    assert reply.cost == 0.25
    assert tracker.total == 0.375
    assert tracker.unknown == 0


# models.md 4.7: cost's empty-value exception is only null or absent.
@pytest.mark.parametrize("cost", [
    False, True, "", [], {}, "0", float("nan"), float("inf"), -float("inf"),
])
def test_bad_cost_retries_without_adding_invalid_cost(wire, make_backend, cost):
    wire.put(response(usage={"cost": cost}))
    wire.put(response(usage={"cost": 0.25}))
    backend, tracker, _ = make_backend(llm={"max_retries": 1})
    reply = backend.call(MESSAGES)
    assert reply.failed_cost == 0.0
    assert tracker.total == 0.25
    assert tracker.unknown == 0


# models.md 4.7 and 4.9: finite inputs can overflow computed cost.
def test_computed_cost_overflow_is_retried(wire, make_backend):
    wire.put(response(usage={"prompt_tokens": 2, "completion_tokens": 1}))
    wire.put(response(usage={"cost": 0.125}))
    backend, tracker, _ = make_backend(
        spec={"price_in_per_m": 1e308, "price_out_per_m": 1.0},
        llm={"max_retries": 1},
    )
    reply = backend.call(MESSAGES)
    assert reply.cost == 0.125
    assert reply.failed_cost == 0.0
    assert tracker.total == 0.125


# models.md 4.4: every 2xx JSON object without an error is a reply.
@pytest.mark.parametrize("status", [200, 201, 202, 206, 299])
def test_success_http_statuses(wire, make_backend, status):
    backend, _, timer = make_backend()
    wire.put({}, status=status)
    assert backend.call(MESSAGES).text == ""
    assert timer.waits == []


# models.md 4.4: provider errors require both nonempty error and empty choices.
@pytest.mark.parametrize("empty", EMPTY)
def test_provider_error_empty_choices(wire, make_backend, empty):
    wire.put({"error": {"message": "failed"}, "choices": empty})
    wire.put(response({"content": "recovered"}))
    backend, _, timer = make_backend(llm={"max_retries": 1})
    assert backend.call(MESSAGES).text == "recovered"
    assert len(timer.waits) == 1


# models.md 4.4: empty errors and errors beside usable choices are ignored.
@pytest.mark.parametrize("error", EMPTY + [{"message": "ignored"}])
def test_top_level_error_with_nonempty_choices_is_ignored(
        wire, make_backend, error):
    body = response({"content": "reply"})
    body["error"] = error
    wire.put(body)
    backend, _, timer = make_backend()
    assert backend.call(MESSAGES).text == "reply"
    assert timer.waits == []


# models.md 4.4: a missing choices field and an empty error give an empty reply.
@pytest.mark.parametrize("error", EMPTY)
def test_empty_error_without_choices(wire, make_backend, error):
    wire.put({"error": error})
    backend, _, timer = make_backend()
    assert backend.call(MESSAGES).text == ""
    assert timer.waits == []


# models.md 4.4: generation errors precede ordinary parsing and are retried.
@pytest.mark.parametrize("choice", [
    {"finish_reason": "error", "message": {"content": "partial"}},
    {"error": {"message": "upstream"}, "message": {"content": "partial"}},
    {"finish_reason": "error", "message": 7},
])
def test_generation_error_retries(wire, make_backend, choice):
    wire.put({"choices": [choice], "usage": {"cost": 0.125}})
    wire.put(response({"content": "complete"}, {"cost": 0.25}))
    backend, tracker, _ = make_backend(llm={"max_retries": 1})
    reply = backend.call(MESSAGES)
    assert reply.text == "complete"
    assert reply.failed_cost == 0.125
    assert tracker.total == 0.375


# models.md 4.4: counted HTTP errors.
@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 502, 503, 599, 600])
def test_retriable_http_status(wire, make_backend, status):
    wire.put({"usage": {"cost": 0.125}}, status=status)
    wire.put(response({"content": "complete"}, {"cost": 0.25}))
    backend, tracker, timer = make_backend(llm={"max_retries": 1})
    reply = backend.call(MESSAGES)
    assert reply.text == "complete"
    assert reply.failed_cost == 0.125
    assert tracker.total == 0.375
    assert len(timer.waits) == 1


# models.md 4.4 and 4.10: permanent HTTP failures, including all redirects.
@pytest.mark.parametrize("status", [
    300, 301, 302, 303, 304, 307, 308,
    400, 401, 402, 403, 404, 405, 410, 413, 422, 499,
])
def test_nonretriable_http_status(wire, make_backend, status):
    wire.put(
        {"usage": {"cost": 0.125}, "error": "denied"},
        status=status,
        headers={"Location": wire.base_url + "/redirected"},
    )
    backend, tracker, timer = make_backend()
    with pytest.raises(models.CallFailed) as caught:
        backend.call(MESSAGES)
    # Adjudication: a 304 response has no body in HTTP (RFC 9110, 15.4.5), so
    # the bytes sent after its headers are not a response body with a cost.
    reported = 0.0 if status == 304 else 0.125
    assert caught.value.failed_cost == reported
    assert "blind/model" in caught.value.error
    assert str(status) in caught.value.error
    assert str(caught.value) == caught.value.error
    assert tracker.total == reported
    assert tracker.unknown == 0
    assert len(wire.requests) == 1
    assert timer.waits == []


# models.md 4.4: invalid JSON and non-object JSON are counted failures.
@pytest.mark.parametrize("raw", [
    b"", b"not json", b"{", b"{} trailing", b"{}{}",
    b"[]", b"null", b"false", b"0", b'"text"',
    b'{"choices":[{"message":{"content":"bad\ncontrol"}}]}',
])
def test_bad_json_response_retries(wire, make_backend, raw):
    wire.put(raw=raw)
    wire.put(response({"content": "complete"}))
    backend, _, timer = make_backend(llm={"max_retries": 1})
    assert backend.call(MESSAGES).text == "complete"
    assert len(timer.waits) == 1


# models.md 4.4: invalid HTTP and truncated bodies are counted failures.
@pytest.mark.parametrize("raw_wire", [
    b"NOT HTTP\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\n{}",
])
def test_invalid_or_cut_short_http_is_counted(wire, make_backend, raw_wire):
    wire.put(wire=raw_wire)
    wire.put(response({"content": "complete"}))
    backend, _, timer = make_backend(llm={"max_retries": 1})
    assert backend.call(MESSAGES).text == "complete"
    assert len(wire.requests) == 2
    assert len(timer.waits) == 1


# models.md 4.2 and 4.4: streaming is sent as configured but cannot be parsed.
def test_streamed_body_exhausts_retries(wire, make_backend):
    wire.put(raw=b'data: {"choices":[]}\n\ndata: [DONE]\n\n')
    wire.put(raw=b'data: {"choices":[]}\n\ndata: [DONE]\n\n')
    backend, tracker, timer = make_backend(
        spec={"extra_body": {"stream": True}},
        llm={"max_retries": 1},
    )
    with pytest.raises(models.CallFailed):
        backend.call(MESSAGES)
    assert len(wire.requests) == 2
    assert all(item["body"]["stream"] is True for item in wire.requests)
    assert len(timer.waits) == 1
    assert tracker.total == 0.0
    assert tracker.unknown == 0


# models.md 4.4 and config.md 3.4.3: max_retries + 1 counted attempts.
@pytest.mark.parametrize("retries", [0, 1, 5, 7])
@pytest.mark.parametrize("summarizer", [False, True])
def test_retry_budget_and_failed_cost(wire, make_backend, retries, summarizer):
    for _ in range(retries + 1):
        wire.put({"usage": {"cost": 0.125}}, status=503)
    backend, tracker, timer = make_backend(
        summarizer=summarizer, llm={"max_retries": retries},
    )
    state = random.getstate()
    with pytest.raises(models.CallFailed) as caught:
        backend.call(MESSAGES, TOOLS, "required")
    assert random.getstate() == state
    assert len(wire.requests) == retries + 1
    assert len(timer.waits) == retries
    assert caught.value.failed_cost == (retries + 1) * 0.125
    assert tracker.total == (retries + 1) * 0.125
    assert tracker.unknown == 0
    assert "blind/model" in caught.value.error
    assert "503" in caught.value.error
    assert all(
        item["body"] == wire.requests[0]["body"] for item in wire.requests
    )


# models.md 4.4 and 7: failed costs are added immediately, before waiting.
def test_failed_attempt_accounting_precedes_retry_wait(wire, make_backend):
    wire.put({"error": "provider", "usage": {"cost": 0.125}})
    wire.put({"usage": {"cost": 0.25}}, status=503)
    wire.put(response({"content": ""}, {"cost": 0.5}))
    backend, tracker, timer = make_backend(llm={"max_retries": 2})
    observed = []
    timer.on_sleep = lambda: observed.append((tracker.total, tracker.unknown))
    reply = backend.call(MESSAGES)
    assert observed == [(0.125, 0), (0.375, 0)]
    assert reply.cost == 0.5
    assert reply.failed_cost == 0.375
    assert tracker.total == 0.875
    assert models.charge(reply.cost, 1) == 0.5


# models.md 4.4: failed attempts without a reported finite cost add nothing.
@pytest.mark.parametrize("body", [
    {},
    {"usage": None},
    {"usage": "bad"},
    {"usage": {}},
    {"usage": {"cost": None}},
    {"usage": {"cost": False}},
    {"usage": {"cost": "0.5"}},
    {"usage": {"cost": float("inf")}},
    {"usage": {"cost": float("nan")}},
])
def test_failed_attempt_does_not_invent_cost(wire, make_backend, body):
    wire.put(body, status=503)
    wire.put(response(usage={"cost": 0.25}))
    backend, tracker, _ = make_backend(
        spec={"price_in_per_m": 100, "price_out_per_m": 100},
        llm={"max_retries": 1},
    )
    reply = backend.call(MESSAGES)
    assert reply.failed_cost == 0.0
    assert tracker.total == 0.25
    assert tracker.unknown == 0


# models.md 4.4 and 4.9: negative finite failed-attempt costs are used as given.
def test_negative_failed_attempt_cost(wire, make_backend):
    wire.put({"usage": {"cost": -0.125}}, status=503)
    wire.put(response(usage={"cost": 0.25}))
    backend, tracker, _ = make_backend(llm={"max_retries": 1})
    reply = backend.call(MESSAGES)
    assert reply.failed_cost == -0.125
    assert tracker.total == 0.125


# models.md 4.3 and 4.4: a completed body noticed past the limit is a timeout.
def test_expired_attempt_is_retried_without_real_wait(wire, make_backend):
    timer = FakeTime()

    def expire():
        timer.now += 1201

    wire.put(response({"content": "late"}), before=expire)
    wire.put(response({"content": "on time"}, {"cost": 0.25}))
    backend, tracker, _ = make_backend(
        timer=timer, llm={"max_retries": 1},
    )
    reply = backend.call(MESSAGES)
    assert reply.text == "on time"
    assert len(wire.requests) == 2
    assert len(timer.waits) == 1
    assert tracker.total == 0.25


# models.md 4.4 and 4.6: connection failures do not consume retry budget.
@pytest.mark.parametrize("failure", [
    ConnectionRefusedError(errno.ECONNREFUSED, "refused"),
    socket.gaierror(socket.EAI_AGAIN, "DNS unavailable"),
    TimeoutError("connect timed out"),
    ConnectionResetError(errno.ECONNRESET, "reset"),
    BrokenPipeError(errno.EPIPE, "broken"),
])
def test_network_failure_does_not_consume_counted_attempt(
        wire, make_backend, monkeypatch, failure):
    original = socket.create_connection
    calls = []

    def connect(*args, **kwargs):
        calls.append(1)
        if len(calls) <= 2:
            raise failure
        return original(*args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", connect)
    wire.put(response({"content": "recovered"}, {"cost": 0.125}))
    backend, tracker, timer = make_backend(llm={"max_retries": 0})
    state = random.getstate()
    reply = backend.call(MESSAGES)
    assert random.getstate() == state
    assert reply.text == "recovered"
    assert len(calls) == 3
    assert len(timer.waits) == 2
    assert reply.failed_cost == 0.0
    assert tracker.total == 0.125


# models.md 4.4 and 4.6: closing before any response is a network failure.
def test_close_before_response_is_not_counted(wire, make_backend):
    wire.put(close=True)
    wire.put(response({"content": "recovered"}))
    backend, _, timer = make_backend(llm={"max_retries": 0})
    assert backend.call(MESSAGES).text == "recovered"
    assert len(wire.requests) == 2
    assert len(timer.waits) == 1


# models.md 4.6: patience expires strictly after, not at, the boundary.
@pytest.mark.parametrize("patience", [0, 900])
def test_network_patience_strict_boundary(make_backend, monkeypatch, patience):
    timer = FakeTime()
    attempts = []
    boundary_times = iter([float(patience), float(patience) + 1.0])

    def connect(*args, **kwargs):
        attempts.append(timer.now)
        raise ConnectionRefusedError(errno.ECONNREFUSED, "offline")

    def advance():
        timer.now = next(boundary_times)

    monkeypatch.setattr(socket, "create_connection", connect)
    timer.on_sleep = advance
    backend, tracker, _ = make_backend(
        timer=timer,
        llm={"network_patience_s": patience, "max_retries": 0},
    )
    with pytest.raises(models.CallFailed) as caught:
        backend.call(MESSAGES)
    assert attempts == [0.0, float(patience), float(patience) + 1.0]
    assert len(timer.waits) == 2
    assert caught.value.failed_cost == 0.0
    assert "blind/model" in caught.value.error
    assert any(word in caught.value.error.lower() for word in (
        "network", "offline", "refused", "connection",
    ))
    assert tracker.total == 0.0
    assert tracker.unknown == 0


# models.md 4.6: a counted failure ends the network streak.
def test_counted_failure_resets_network_patience(wire, make_backend, monkeypatch):
    timer = FakeTime()
    original = socket.create_connection
    count = 0

    def connect(*args, **kwargs):
        nonlocal count
        count += 1
        if count in (1, 3):
            raise ConnectionRefusedError(errno.ECONNREFUSED, "offline")
        return original(*args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", connect)
    wire.put({}, status=503)
    wire.put(response({"content": "recovered"}))
    backend, _, _ = make_backend(
        timer=timer, llm={"network_patience_s": 0, "max_retries": 1},
    )
    assert backend.call(MESSAGES).text == "recovered"
    assert count == 4
    assert len(timer.waits) == 3


# models.md 4.4 and 4.6: network failures do not reset the counted-failure budget.
def test_network_failure_preserves_counted_budget(wire, make_backend, monkeypatch):
    original = socket.create_connection
    count = 0

    def connect(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "offline")
        return original(*args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", connect)
    wire.put({"usage": {"cost": 0.125}}, status=503)
    wire.put({"usage": {"cost": 0.25}}, status=503)
    backend, tracker, timer = make_backend(llm={"max_retries": 1})
    with pytest.raises(models.CallFailed) as caught:
        backend.call(MESSAGES)
    assert count == 3
    assert len(timer.waits) == 2
    assert caught.value.failed_cost == 0.375
    assert tracker.total == 0.375


# models.md 2.4: internal defects are not converted into failed model calls.
def test_unexpected_internal_exception_propagates(wire, make_backend):
    class Defect(Exception):
        pass

    timer = FakeTime()

    def broken_sleep():
        raise Defect("injected defect")

    timer.on_sleep = broken_sleep
    wire.put({}, status=503)
    backend, _, _ = make_backend(timer=timer)
    with pytest.raises(Defect):
        backend.call(MESSAGES)


# models.md 7: tracker starts at zero, counts unknown replies, and has no cap.
def test_tracker_without_cap_and_fresh_invocation():
    tracker = models.CostTracker(None)
    assert tracker.cap is None
    assert tracker.total == 0.0
    assert tracker.unknown == 0
    assert tracker.reached() is False
    assert tracker.check() is None
    tracker.add(0.125)
    tracker.add(0.0, known=False)
    tracker.add(0.0, known=False)
    tracker.add(1000000.0)
    assert tracker.total == 1000000.125
    assert tracker.unknown == 2
    assert tracker.reached() is False
    assert tracker.check() is None
    fresh = models.CostTracker(None)
    assert fresh.total == 0.0
    assert fresh.unknown == 0


# models.md 7: cap comparison is >=, independent of rendered decimal places.
@pytest.mark.parametrize("spent, reached", [
    (0.0, False), (19.999, False), (20.0, True), (20.125, True),
])
def test_cap_boundary(spent, reached):
    tracker = models.CostTracker(20)
    tracker.add(spent)
    assert tracker.cap == 20
    assert tracker.reached() is reached
    if reached:
        with pytest.raises(models.CostCapReached):
            tracker.check()
    else:
        assert tracker.check() is None


# models.md 7; README.md F2: exact API text and ties-to-even formatting.
@pytest.mark.parametrize("cap, spent, expected", [
    (20, 20.12, "cost cap $20.00 reached (spent $20.12)"),
    (20, 20.125, "cost cap $20.00 reached (spent $20.12)"),
    (20, 20.375, "cost cap $20.00 reached (spent $20.38)"),
    (0.125, 0.375, "cost cap $0.12 reached (spent $0.38)"),
])
def test_cost_cap_stop_is_not_call_failure(cap, spent, expected):
    tracker = models.CostTracker(cap)
    tracker.add(spent)
    with pytest.raises(models.CostCapReached) as caught:
        tracker.check()
    assert str(caught.value) == expected
    assert not isinstance(caught.value, models.CallFailed)


# models.md 7: admitted calls and retries may overshoot; backend never checks cap.
@pytest.mark.parametrize("summarizer", [False, True])
def test_backend_does_not_check_cap(wire, make_backend, summarizer):
    tracker = models.CostTracker(0.125)
    tracker.add(0.125)
    wire.put({"usage": {"cost": 0.25}}, status=503)
    wire.put(response(usage={"cost": 0.5}))
    backend, _, _ = make_backend(
        tracker=tracker, summarizer=summarizer, llm={"max_retries": 1},
    )
    reply = backend.call(MESSAGES)
    assert reply.cost == 0.5
    assert reply.failed_cost == 0.25
    assert tracker.total == 0.875
    with pytest.raises(models.CostCapReached):
        tracker.check()


# models.md 6 and 7: participant and summary backends share real-dollar spending.
def test_shared_tracker_and_credit_rate_independence(wire, make_backend):
    tracker = models.CostTracker(1)
    participant, _, _ = make_backend(tracker=tracker)
    summarizer, _, _ = make_backend(tracker=tracker, summarizer=True)
    wire.put(response(usage={"cost": 0.125}))
    wire.put(response(
        {"content": ""}, {"cost": 0.25}, finish_reason="content_filter",
    ))
    first = participant.call(MESSAGES)
    second = summarizer.call(MESSAGES)
    assert models.charge(first.cost, 1000) == 125.0
    assert second.cost == 0.25
    assert tracker.total == 0.375
    assert tracker.reached() is False


# models.md 7 and the documented concurrent tracker interface.
def test_tracker_concurrent_adds():
    tracker = models.CostTracker(None)

    def add_many():
        for _ in range(250):
            tracker.add(0.125)
            tracker.add(0.0, known=False)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: add_many(), range(8)))
    assert tracker.total == 250.0
    assert tracker.unknown == 2000


# models.md 1, 2.2 and 7: one backend can serve simultaneous callers.
def test_backend_concurrent_calls(wire, make_backend):
    for _ in range(16):
        wire.put(response({"content": "ok"}, {"cost": 0.125}))
    backend, tracker, _ = make_backend()

    def call(index):
        return backend.call([{"role": "user", "content": str(index)}])

    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(call, range(16)))
    assert all(reply.text == "ok" for reply in replies)
    assert all(reply.cost == 0.125 for reply in replies)
    assert all(reply.failed_cost == 0.0 for reply in replies)
    assert tracker.total == 2.0
    assert tracker.unknown == 0
    assert sorted(
        int(item["body"]["messages"][0]["content"]) for item in wire.requests
    ) == list(range(16))


# models.md 3 and 7: mock dispatch is tested without importing its later module.
def test_mock_delegation_does_not_touch_tracker(monkeypatch):
    stub = types.ModuleType("market.mock")
    sentinel = object()
    received = []

    def mock_backend(spec):
        received.append(spec)
        return sentinel

    stub.backend = mock_backend
    monkeypatch.setitem(sys.modules, "market.mock", stub)
    import market
    monkeypatch.setattr(market, "mock", stub, raising=False)
    spec = {"provider": "mock", "api_key_env": None}
    tracker = models.CostTracker(20)
    result = models.backend(spec, dict(LLM), tracker, env={})
    assert result is sentinel
    assert received == [spec]
    assert tracker.total == 0.0
    assert tracker.unknown == 0
