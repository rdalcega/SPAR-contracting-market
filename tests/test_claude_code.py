"""Tests of provider claude_code (market/claude_code.py, market/claude_tools.py) against a scripted fake `claude`
(tests/fake_claude.py): free, offline, no model."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from market import claude_code, config, models

FAKE = os.path.join(os.path.dirname(__file__), "fake_claude.py")
LLM = {"temperature": 0.8, "max_tokens": 64000, "timeout_s": 10, "max_retries": 2, "network_patience_s": 900}
SPEC = {"provider": "claude_code", "model": "claude-haiku-5-5", "price_in_per_m": 0.10, "price_out_per_m": 0.50,
        "temperature": None, "max_tokens": 8000, "extra_body": {"reasoning": {"effort": "low"}}}
COMPUTE = {"type": "function", "function": {"name": "compute", "description": "run", "parameters": {
    "type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}}
ACT = {"type": "function", "function": {"name": "act", "description": "end", "parameters": {
    "type": "object", "properties": {"memory": {"type": "string"}}}}}
SYSTEM, VIEW = {"role": "system", "content": "You are B-1."}, {"role": "user", "content": "Day 1, round 1."}


def text(t, stop="end_turn", usage=(10, 0, 0, 5)):
    return {"blocks": [{"type": "text", "text": t}], "stop": stop, "usage": list(usage)}


def calls(*named, thinking=None, usage=(10, 0, 0, 5)):
    blocks = [{"type": "thinking", "thinking": thinking}] if thinking else []
    blocks += [{"type": "tool_use", "name": n, "input": i} for n, i in named]
    return {"blocks": blocks, "stop": "tool_use", "usage": list(usage)}


class Fake:
    """A test's fake `claude`: its script, its log, and a backend that runs it."""

    def __init__(self, tmp_path, responses):
        self.script, self.log = tmp_path / "script.json", tmp_path / "log.jsonl"
        self.script.write_text(json.dumps({"next": 0, "responses": responses}))
        self.cli = tmp_path / "claude"
        self.cli.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}' '{self.script}' '{self.log}' \"$@\"\n")
        self.cli.chmod(0o755)
        self.slept = []
        self.tracker = models.CostTracker(None)

    def backend(self, spec=SPEC, llm=LLM, clock=time.time):
        return claude_code.backend(spec, llm, self.tracker, "tok-123", cli=str(self.cli),
                                   sleep=self.slept.append, clock=clock)

    def entries(self, kind):
        """What the fake logged of one kind, in order; waits briefly for entries still being written."""
        found = []
        for _ in range(100):
            lines = self.log.read_text().splitlines() if self.log.exists() else []
            found = [json.loads(line)[kind] for line in lines if kind in json.loads(line)]
            if found:
                return found
            time.sleep(0.05)
        return found


def answer(reply, *texts):
    """The tool messages answering a reply's calls, in order."""
    return [{"role": "tool", "tool_call_id": c["id"], "content": t} for c, t in zip(reply.tool_calls, texts)]


def assistant(reply):
    return {"role": "assistant", "content": reply.text, "tool_calls": reply.tool_calls}


def test_first_call_starts_an_isolated_session(tmp_path):
    fake = Fake(tmp_path, [calls(("compute", {"code": "print(1)"}), thinking="Price it at 5.",
                                 usage=(100, 20, 30, 7))])
    reply = fake.backend().call([SYSTEM, VIEW], [COMPUTE, ACT], "required")
    assert reply.tool_calls == [{"id": "toolu_1_1", "type": "function",
                                 "function": {"name": "compute", "arguments": '{"code": "print(1)"}'}}]
    assert reply.reasoning == "Price it at 5." and reply.text == ""
    assert (reply.input_tokens, reply.output_tokens, reply.finish_reason) == (150, 7, "tool_calls")
    assert reply.cost == pytest.approx(150 * 0.10 / 1e6 + 7 * 0.50 / 1e6) and reply.cost_source == "computed"
    assert fake.tracker.total == pytest.approx(reply.cost)
    launch = fake.entries("launch")[0]
    args, env = launch["args"], launch["env"]
    for flag in ("--tools", "--strict-mcp-config", "--restricted", "--no-session-persistence",
                 "--include-partial-messages"):
        assert flag in args
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--model") + 1] == "claude-haiku-5-5" and args[args.index("--effort") + 1] == "low"
    assert args[args.index("--allowedTools") + 1] == "mcp__market__compute,mcp__market__act"
    assert args[args.index("--thinking-display") + 1] == "summarized"
    # the environment holds nothing of the parent's but PATH; home, configuration and working folder are empty
    assert set(env) - {"PWD", "SHLVL", "_", "OLDPWD", "__CF_USER_TEXT_ENCODING", "LC_CTYPE"} == {
        "PATH", "HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "ENABLE_TOOL_SEARCH", "CLAUDE_CODE_DISABLE_CLAUDE_MDS", "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS",
        "DISABLE_AUTOUPDATER"}
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-123" and env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8000"
    assert launch["home"] == [] and launch["config"] == []
    assert os.path.basename(launch["cwd"]) == "work"
    assert fake.entries("user") == ["Day 1, round 1."]
    assert fake.entries("tools") == [[
        {"name": "compute", "description": "run", "inputSchema": COMPUTE["function"]["parameters"]},
        {"name": "act", "description": "end", "inputSchema": ACT["function"]["parameters"]}]]


def test_system_prompt_is_the_system_message(tmp_path, monkeypatch):
    seen = {}
    real = claude_code._Session.__init__

    def spy(self, owner, system, functions):
        real(self, owner, system, functions)
        with open(os.path.join(self.dir, "system.txt")) as f:
            seen["system"] = f.read()

    monkeypatch.setattr(claude_code._Session, "__init__", spy)
    Fake(tmp_path, [text("hi")]).backend().call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert seen["system"] == "You are B-1."


def test_a_chain_continues_one_session_with_native_tool_results(tmp_path):
    fake = Fake(tmp_path, [calls(("compute", {"code": "x"})), calls(("act", {"memory": "m"})),
                           calls(("act", {"memory": "fixed"})), text("done")])
    b = fake.backend()
    messages = [SYSTEM, VIEW]
    first = b.call(messages, [COMPUTE, ACT], "required")
    messages = messages + [assistant(first)] + answer(first, "out: 1")
    second = b.call(messages, [COMPUTE, ACT], {"type": "function", "function": {"name": "act"}})
    assert second.tool_calls[0]["function"]["name"] == "act"
    messages = messages + [assistant(second)] + answer(second, "your act call's actions could not be read")
    third = b.call(messages, [ACT], {"type": "function", "function": {"name": "act"}})
    assert third.tool_calls[0]["function"]["arguments"] == '{"memory": "fixed"}'
    assert len(fake.entries("launch")) == 1
    results = fake.entries("tool_result")
    assert results[:2] == [{"toolu_1_0": "out: 1"}, {"toolu_2_0": "your act call's actions could not be read"}]


def test_text_path_answers_pending_calls(tmp_path):
    fake = Fake(tmp_path, [calls(("compute", {"code": "a"}), ("compute", {"code": "b"})),
                           calls(("act", {"memory": "m"}))])
    b = fake.backend()
    first = b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    body = {"role": "user", "content": "your reply did not call act: ..."}
    b.call([SYSTEM, VIEW, {"role": "assistant", "content": "(an empty reply)"}, body], [ACT])
    ids = [c["id"] for c in first.tool_calls]
    results = {k: v for entry in fake.entries("tool_result") for k, v in entry.items()}
    assert results == {ids[0]: claude_code.NOT_RUN, ids[1]: "your reply did not call act: ..."}


def test_text_path_after_a_reply_without_calls_is_a_user_message(tmp_path):
    fake = Fake(tmp_path, [text("I will think."), calls(("act", {"memory": "m"}))])
    b = fake.backend()
    first = b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert first.tool_calls is None and first.finish_reason == "stop" and first.text == "I will think."
    body = {"role": "user", "content": "your reply was not a function call"}
    second = b.call([SYSTEM, VIEW, assistant(first), body], [ACT])
    assert second.tool_calls[0]["function"]["name"] == "act"
    assert fake.entries("user") == ["Day 1, round 1.", "your reply was not a function call"]
    assert len(fake.entries("launch")) == 1


def test_a_new_conversation_closes_the_previous_session(tmp_path):
    fake = Fake(tmp_path, [calls(("act", {"memory": "m"})), calls(("act", {"memory": "n"}))])
    b = fake.backend()
    b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    old = b.sessions[0]
    b.call([SYSTEM, {"role": "user", "content": "Day 1, round 2."}], [COMPUTE, ACT])
    assert len(fake.entries("launch")) == 2 and b.sessions != [old]
    assert old.proc.poll() is not None and not os.path.exists(old.dir)


def test_summarizer_call_has_no_tools_and_ends_its_session(tmp_path):
    fake = Fake(tmp_path, [text("summary", stop="max_tokens")])
    b = fake.backend()
    reply = b.call([SYSTEM, VIEW])
    assert reply.text == "summary" and reply.finish_reason == "length"
    args = fake.entries("launch")[0]["args"]
    assert "--mcp-config" not in args and "--allowedTools" not in args
    assert b.sessions == []


def test_a_failed_start_is_retried_then_fails(tmp_path):
    fake = Fake(tmp_path, [{"error": "API Error: 500"}] * 3)
    with pytest.raises(models.CallFailed, match="after 3 attempts: Claude Code's turn ended without a reply: "
                                                 "API Error: 500"):
        fake.backend().call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert len(fake.slept) == 2 and len(fake.entries("launch")) == 3


def test_a_subscription_limit_is_waited_out(tmp_path):
    now = 1_000_000.0
    fake = Fake(tmp_path, [{"error": "limit reached", "limit": now + 600}, text("ok")])
    reply = fake.backend(clock=lambda: now).call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert reply.text == "ok" and len(fake.slept) == 1 and 600 <= fake.slept[0] < 601


def test_a_limit_beyond_the_network_patience_fails(tmp_path):
    now = 1_000_000.0
    fake = Fake(tmp_path, [{"error": "limit reached", "limit": now + 5 * 3600}])
    with pytest.raises(models.CallFailed, match="beyond the network patience of 900 s"):
        fake.backend(clock=lambda: now).call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert fake.slept == []


def test_no_reply_within_the_time_limit(tmp_path):
    fake = Fake(tmp_path, [{"hang": True}])
    with pytest.raises(models.CallFailed, match="after 1 attempts: no complete response within 1 s"):
        fake.backend(llm={**LLM, "timeout_s": 1, "max_retries": 0}).call([SYSTEM, VIEW], [COMPUTE, ACT])


def test_a_process_that_does_not_read_its_input_does_not_block_the_call(tmp_path):
    fake = Fake(tmp_path, [])
    fake.script.write_text(json.dumps({"next": 0, "responses": [], "deaf": True}))
    view = {"role": "user", "content": "x" * 2_000_000}  # far beyond a pipe's buffer
    began = time.time()
    with pytest.raises(models.CallFailed, match="no complete response within 1 s"):
        fake.backend(llm={**LLM, "timeout_s": 1, "max_retries": 0}).call([SYSTEM, view], [COMPUTE, ACT])
    assert time.time() - began < 10


def test_a_session_that_cannot_start_leaves_nothing_behind(tmp_path, monkeypatch):
    fake = Fake(tmp_path, [])
    b = fake.backend()
    made = []
    real = claude_code._Session._launch

    def failing(self, system, functions):
        made.append(self.dir)
        real(self, system, functions)
        raise RuntimeError("no")

    monkeypatch.setattr(claude_code._Session, "_launch", failing)
    with pytest.raises(RuntimeError):
        b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert b.sessions == [] and made and not os.path.exists(made[0])


def test_an_unexpected_error_in_a_first_step_drops_its_session(tmp_path, monkeypatch):
    fake = Fake(tmp_path, [text("hi")])
    b = fake.backend()
    monkeypatch.setattr(claude_code._Session, "_reply", lambda *a: (_ for _ in ()).throw(ValueError("bad usage")))
    with pytest.raises(ValueError):
        b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert b.sessions == []


def test_a_call_of_a_function_claude_code_does_not_serve_is_skipped(tmp_path):
    unserved = {"blocks": [{"type": "tool_use", "name": "act", "input": {"memory": "bare"}, "unserved": True},
                           {"type": "tool_use", "name": "compute", "input": {"code": "x"}}],
                "stop": "tool_use", "usage": [100, 0, 0, 10]}
    fake = Fake(tmp_path, [unserved, calls(("act", {"memory": "named"}), usage=(10, 0, 0, 5))])
    reply = fake.backend().call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert reply.tool_calls[0]["function"]["arguments"] == '{"memory": "named"}'
    assert fake.entries("tool_error") == ["toolu_1_0"]
    assert fake.entries("tool_result") == [{"toolu_1_1": claude_code.NOT_RUN}]
    # both responses count toward the cost cap; the reply's own cost is only its own
    assert reply.cost == pytest.approx(10 * 0.10 / 1e6 + 5 * 0.50 / 1e6)
    assert fake.tracker.total == pytest.approx(110 * 0.10 / 1e6 + 15 * 0.50 / 1e6)


def test_claude_codes_own_continuation_counts_toward_the_cost_cap(tmp_path):
    cut = {**text("half", stop="max_tokens", usage=(10, 0, 0, 5)), "continue": True}
    fake = Fake(tmp_path, [cut, text("rest", usage=(20, 0, 0, 7)), calls(("act", {"memory": "m"}))])
    b = fake.backend()
    first = b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert first.finish_reason == "length"
    body = {"role": "user", "content": "your reply hit the length limit"}
    second = b.call([SYSTEM, VIEW, assistant(first), body], [ACT])
    assert second.tool_calls[0]["function"]["name"] == "act"
    assert fake.tracker.total == pytest.approx((10 + 20 + 10) * 0.10 / 1e6 + (5 + 7 + 5) * 0.50 / 1e6)


def test_a_continuation_that_calls_a_function_is_answered_not_run(tmp_path):
    cut = {**text("half", stop="max_tokens"), "continue": True}
    fake = Fake(tmp_path, [cut, calls(("act", {"memory": "early"})), text("over"),
                           calls(("act", {"memory": "m"}))])
    b = fake.backend()
    first = b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    body = {"role": "user", "content": "your reply hit the length limit"}
    second = b.call([SYSTEM, VIEW, assistant(first), body], [ACT])
    assert second.tool_calls[0]["function"]["arguments"] == '{"memory": "m"}'
    assert fake.entries("tool_result") == [{"toolu_2_0": claude_code.NOT_RUN}]


def test_a_process_that_cannot_be_started_is_a_failed_attempt(tmp_path):
    fake = Fake(tmp_path, [])
    b = fake.backend(llm={**LLM, "max_retries": 1})
    fake.cli.chmod(0o644)  # found at setup, no longer runnable
    with pytest.raises(models.CallFailed, match="after 2 attempts: Claude Code could not be started"):
        b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert b.sessions == []


def test_a_time_limit_longer_than_a_wait_can_take(tmp_path):
    b = Fake(tmp_path, [text("ok")]).backend(llm={**LLM, "timeout_s": 1e300})
    assert b.timeout == models._LONGEST and b.call([SYSTEM, VIEW]).text == "ok"


def test_a_failure_within_a_chain_is_not_retried(tmp_path):
    fake = Fake(tmp_path, [calls(("compute", {"code": "x"})), {"error": "API Error: 500"}])
    b = fake.backend()
    first = b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    with pytest.raises(models.CallFailed, match="turn ended without a reply: API Error: 500"):
        b.call([SYSTEM, VIEW, assistant(first)] + answer(first, "out"), [COMPUTE, ACT])
    assert fake.slept == [] and b.sessions == []


def test_a_continuation_that_does_not_map_fails(tmp_path):
    fake = Fake(tmp_path, [calls(("compute", {"code": "x"}), ("act", {"memory": "m"}))])
    b = fake.backend()
    first = b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    with pytest.raises(models.CallFailed, match="cannot continue"):
        b.call([SYSTEM, VIEW, assistant(first)] + answer(first, "out")[:1], [COMPUTE, ACT])


def test_a_conversation_with_replies_cannot_start_a_session(tmp_path):
    fake = Fake(tmp_path, [])
    with pytest.raises(models.CallFailed, match="cannot start a conversation"):
        fake.backend().call([SYSTEM, VIEW, {"role": "assistant", "content": "x"}, VIEW], [COMPUTE, ACT])
    assert not fake.log.exists()


def test_cost_unknown_without_prices(tmp_path):
    spec = {k: v for k, v in SPEC.items() if not k.startswith("price")}
    reply = Fake(tmp_path, [text("s")]).backend(spec=spec).call([SYSTEM, VIEW])
    assert (reply.cost, reply.cost_known, reply.cost_source) == (0.0, False, "unknown")


def test_setup(tmp_path, monkeypatch):
    assert models.resolved(SPEC)["api_key_env"] == "CLAUDE_CODE_OAUTH_TOKEN"
    with pytest.raises(models.SetupError, match="set CLAUDE_CODE_OAUTH_TOKEN in your environment or in .env"):
        models.backend(SPEC, LLM, models.CostTracker(None), env={})
    monkeypatch.setattr(claude_code, "CLI", str(tmp_path / "no-such-claude"))
    with pytest.raises(models.SetupError, match="needs Claude Code's"):
        models.backend(SPEC, LLM, models.CostTracker(None), env={"CLAUDE_CODE_OAUTH_TOKEN": "t"})
    fake = Fake(tmp_path, [])
    monkeypatch.setattr(claude_code, "CLI", str(fake.cli))
    b = models.backend(SPEC, LLM, models.CostTracker(None), env={"CLAUDE_CODE_OAUTH_TOKEN": "t"})
    assert isinstance(b, claude_code._Backend) and b.key == "t"


def _cfg(**spec):
    return {"models": {"buyers": {**SPEC, **spec}}}


def test_config_accepts_a_claude_code_spec():
    cfg = config.resolve(_cfg())
    assert cfg["models"]["buyers"]["provider"] == "claude_code"


@pytest.mark.parametrize("spec, path", [
    ({"temperature": 0.5}, "models.buyers.temperature"),
    ({"base_url": "http://x"}, "models.buyers.base_url"),
    ({"extra_body": {"reasoning": {"effort": "huge"}}}, "models.buyers.extra_body"),
    ({"extra_body": {"reasoning": {"effort": "low"}, "provider": {}}}, "models.buyers.extra_body"),
    ({"api_key_env": None}, "models.buyers.api_key_env"),
])
def test_config_refuses_what_claude_code_would_drop(spec, path):
    with pytest.raises(config.ConfigError, match=path.replace(".", r"\.")):
        config.resolve(_cfg(**spec))


def test_config_refuses_the_inherited_temperature():
    raw = _cfg()
    del raw["models"]["buyers"]["temperature"]
    with pytest.raises(config.ConfigError, match=r"models\.buyers\.temperature"):
        config.resolve(raw)
    raw["llm"] = {"temperature": None}
    assert config.resolve(raw)["models"]["buyers"]["temperature"] is None


def test_tool_server_answers_in_either_order(tmp_path):
    """The tool server settles a call whether the market's answer comes before or after it."""
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps([{"name": "act", "description": "", "inputSchema": {"type": "object"}}]))
    address = os.path.join(tempfile.mkdtemp(dir="/tmp"), "s")  # within AF_UNIX's path length
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(address)
    server.listen(1)
    stub = subprocess.Popen([sys.executable, "-I", claude_code._STUB, str(tools), address],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    conn, _ = server.accept()

    def rpc(n, method, params):
        stub.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params}) + "\n")
        stub.stdin.flush()

    def read():
        return json.loads(stub.stdout.readline())

    rpc(1, "tools/list", {})
    assert read()["result"]["tools"][0]["name"] == "act"
    conn.sendall(b'{"id": "u1", "text": "early"}\n')
    time.sleep(0.2)
    rpc(2, "tools/call", {"name": "act", "arguments": {}, "_meta": {"claudecode/toolUseId": "u1"}})
    assert read() == {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "early"}]}}
    rpc(3, "tools/call", {"name": "act", "arguments": {}, "_meta": {"claudecode/toolUseId": "u2"}})
    threading.Timer(0.2, lambda: conn.sendall(b'{"id": "u2", "text": "late"}\n')).start()
    assert read()["result"]["content"][0]["text"] == "late"
    stub.stdin.close()
    stub.wait(5)
    conn.close()
    server.close()
