"""Tests of provider codex (market/codex.py) against a scripted fake `codex` (tests/fake_codex.py): free, offline,
no model. What it shares with claude_code (market/live.py) is tested in tests/test_claude_code.py."""

import base64
import json
import os
import sys
import time

import pytest

from market import codex, config, live, models

FAKE = os.path.join(os.path.dirname(__file__), "fake_codex.py")
LLM = {"temperature": 0.8, "max_tokens": 64000, "timeout_s": 10, "max_retries": 2, "network_patience_s": 900}
SPEC = {"provider": "codex", "model": "gpt-test", "price_in_per_m": 0.25, "price_out_per_m": 2.0,
        "temperature": None, "max_tokens": 8000, "extra_body": {"reasoning": {"effort": "low"}}}
COMPUTE = {"type": "function", "function": {"name": "compute", "description": "run", "parameters": {
    "type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}}
ACT = {"type": "function", "function": {"name": "act", "description": "end", "parameters": {
    "type": "object", "properties": {"memory": {"type": "string"}}}}}
SYSTEM, VIEW = {"role": "system", "content": "You are B-1."}, {"role": "user", "content": "Day 1, round 1."}
NOTICE = "compute is not available now: call act\n\n"
FORCED = {"type": "function", "function": {"name": "act"}}


def token(expires):
    claims = base64.urlsafe_b64encode(json.dumps({"exp": expires}).encode()).decode().rstrip("=")
    return f"h.{claims}.s"


def text(t, usage=(10, 5)):
    return {"blocks": [{"type": "text", "text": t}], "usage": list(usage)}


def calls(*named, thinking=None, usage=(10, 5)):
    blocks = [{"type": "thinking", "thinking": thinking}] if thinking else []
    blocks += [{"type": "tool_use", "name": n, "input": i} for n, i in named]
    return {"blocks": blocks, "usage": list(usage)}


class Fake:
    """A test's fake `codex`, its script and log, a ChatGPT login, and a backend that runs them."""

    def __init__(self, tmp_path, responses, **extra):
        self.script, self.log = tmp_path / "script.json", tmp_path / "log.jsonl"
        self.script.write_text(json.dumps({"next": 0, "responses": responses, **extra}))
        self.cli = tmp_path / "codex"
        self.cli.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}' '{self.script}' '{self.log}' \"$@\"\n")
        self.cli.chmod(0o755)
        self.login = tmp_path / "login"
        self.login.mkdir()
        self.write_login(time.time() + 86400)
        self.slept = []
        self.tracker = models.CostTracker(None)

    def write_login(self, expires, access=None):
        (self.login / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
            "access_token": access or token(expires), "account_id": "acct-1", "refresh_token": "never-read"}}))

    def backend(self, spec=SPEC, llm=LLM, clock=time.time):
        return codex.backend(spec, llm, self.tracker, cli=str(self.cli), login_home=str(self.login),
                             sleep=self.slept.append, clock=clock)

    def entries(self, kind):
        found = []
        for _ in range(100):
            lines = self.log.read_text().splitlines() if self.log.exists() else []
            found = [json.loads(line)[kind] for line in lines if kind in json.loads(line)]
            if found:
                return found
            time.sleep(0.05)
        return found

    def requests(self, method):
        return [r["params"] for r in self.entries("request") if r["method"] == method]


def answer(reply, *texts):
    return [{"role": "tool", "tool_call_id": c["id"], "content": t} for c, t in zip(reply.tool_calls, texts)]


def assistant(reply):
    return {"role": "assistant", "content": reply.text, "tool_calls": reply.tool_calls}


def test_first_call_starts_an_isolated_session(tmp_path):
    fake = Fake(tmp_path, [calls(("compute", {"code": "print(1)"}), thinking="Price it at 5.", usage=(150, 7))])
    reply = fake.backend().call([SYSTEM, VIEW], [COMPUTE, ACT], "required")
    assert reply.tool_calls == [{"id": "call_1_1", "type": "function",
                                 "function": {"name": "compute", "arguments": '{"code": "print(1)"}'}}]
    assert reply.reasoning == "Price it at 5." and reply.text == ""
    assert (reply.input_tokens, reply.output_tokens, reply.finish_reason) == (150, 7, "tool_calls")
    assert reply.cost == pytest.approx(150 * 0.25 / 1e6 + 7 * 2.0 / 1e6) and reply.cost_source == "computed"
    launch = fake.entries("launch")[0]
    args, env = launch["args"], launch["env"]
    assert args[0] == "app-server"
    assert [args[i + 1] for i, a in enumerate(args) if a == "--disable"] == list(codex._OFF)
    overrides = dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "-c")
    assert {k: json.loads(v) for k, v in overrides.items() if k != "model_catalog_json"} == codex._CONFIG
    with open(json.loads(overrides["model_catalog_json"])) as f:  # still there: the session is live
        model = json.load(f)["models"][0]
    assert {k: model[k] for k in codex._DIRECT} == codex._DIRECT
    assert set(env) - {"PWD", "SHLVL", "_", "OLDPWD", "__CF_USER_TEXT_ENCODING", "LC_CTYPE"} == {
        "PATH", "HOME", "CODEX_HOME"}
    assert env["HOME"] == env["CODEX_HOME"] and launch["home"] == [] and os.path.basename(launch["cwd"]) == "work"
    assert fake.requests("initialize")[0]["capabilities"] == {"experimentalApi": True}
    login = fake.requests("account/login/start")[0]
    assert login["type"] == "chatgptAuthTokens" and login["chatgptAccountId"] == "acct-1"
    assert "never-read" not in json.dumps(fake.entries("request"))
    thread = fake.requests("thread/start")[0]
    assert thread["baseInstructions"] == "You are B-1." and thread["model"] == "gpt-test"
    assert (thread["ephemeral"], thread["approvalPolicy"], thread["sandbox"], thread["experimentalRawEvents"]) == (
        True, "never", "read-only", True)
    assert thread["dynamicTools"] == [
        {"type": "function", "name": "compute", "description": "run", "inputSchema": COMPUTE["function"]["parameters"]},
        {"type": "function", "name": "act", "description": "end", "inputSchema": ACT["function"]["parameters"]}]
    turn = fake.requests("turn/start")[0]
    assert (turn["input"][0]["text"], turn["effort"], turn["summary"]) == ("Day 1, round 1.", "low", "detailed")


def test_a_chain_continues_one_thread_with_native_tool_results(tmp_path):
    fake = Fake(tmp_path, [calls(("compute", {"code": "x"})), calls(("act", {"memory": "m"})),
                           calls(("act", {"memory": "fixed"}))])
    b = fake.backend()
    messages = [SYSTEM, VIEW]
    first = b.call(messages, [COMPUTE, ACT], "required")
    messages += [assistant(first)] + answer(first, "out: 1")
    second = b.call(messages, [COMPUTE, ACT], FORCED)
    messages += [assistant(second)] + answer(second, "unreadable")
    third = b.call(messages, [ACT], FORCED)
    assert third.tool_calls[0]["function"]["arguments"] == '{"memory": "fixed"}'
    assert len(fake.entries("launch")) == 1 and len(fake.requests("turn/start")) == 1
    assert fake.entries("tool_result")[:2] == [{"call_1_0": NOTICE + "out: 1"}, {"call_2_0": NOTICE + "unreadable"}]


def test_text_path_after_a_reply_without_calls_is_a_new_turn(tmp_path):
    fake = Fake(tmp_path, [text("I will think."), calls(("act", {"memory": "m"}))])
    b = fake.backend()
    first = b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert first.tool_calls is None and first.finish_reason == "stop" and first.text == "I will think."
    body = {"role": "user", "content": "your reply was not a function call"}
    second = b.call([SYSTEM, VIEW, assistant(first), body], [ACT], FORCED)
    assert second.tool_calls[0]["function"]["name"] == "act"
    assert [t["input"][0]["text"] for t in fake.requests("turn/start")] == [
        "Day 1, round 1.", NOTICE + "your reply was not a function call"]


def test_text_path_answers_the_pending_call(tmp_path):
    fake = Fake(tmp_path, [calls(("compute", {"code": "a"})), calls(("act", {"memory": "m"}))])
    b = fake.backend()
    b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    body = {"role": "user", "content": "your reply did not call act: ..."}
    b.call([SYSTEM, VIEW, {"role": "assistant", "content": "(an empty reply)"}, body], [ACT], FORCED)
    assert fake.entries("tool_result") == [{"call_1_0": NOTICE + "your reply did not call act: ..."}]


def test_a_withheld_compute_is_pushed_back(tmp_path):
    fake = Fake(tmp_path, [calls(("act", {"memory": "bad"})), calls(("compute", {"code": "y"}), usage=(50, 9)),
                           calls(("act", {"memory": "fixed"}), usage=(10, 5))])
    b = fake.backend()
    first = b.call([SYSTEM, VIEW], [COMPUTE, ACT], "required")
    fixed = b.call([SYSTEM, VIEW, assistant(first)] + answer(first, "unreadable"), [ACT], FORCED)
    assert fixed.tool_calls[0]["function"]["arguments"] == '{"memory": "fixed"}'
    assert fake.entries("tool_result")[1] == {"call_2_0": "compute is not available now: call act"}
    assert (fixed.input_tokens, fixed.output_tokens) == (60, 14)


def test_a_call_of_a_function_codex_lacks_is_skipped(tmp_path):
    unserved = {"blocks": [{"type": "tool_use", "name": "shell", "input": {"cmd": "ls"}, "unserved": True}],
                "usage": [100, 10]}
    fake = Fake(tmp_path, [unserved, calls(("act", {"memory": "m"}))])
    reply = fake.backend().call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert reply.tool_calls[0]["function"]["name"] == "act" and reply.input_tokens == 110


def test_an_output_of_another_kind_is_skipped(tmp_path):
    other = {"blocks": [{"type": "raw", "item": {"type": "custom_tool_call", "call_id": "c9", "name": "exec",
                                                  "input": "ls"}}], "usage": [40, 4]}
    fake = Fake(tmp_path, [other, calls(("act", {"memory": "m"}), usage=(10, 5, 3))])
    reply = fake.backend().call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert reply.tool_calls[0]["function"]["name"] == "act"
    assert (reply.input_tokens, reply.output_tokens, reply.reasoning_tokens) == (50, 9, 3)


def test_summarizer_call_has_no_tools_and_ends_its_session(tmp_path):
    fake = Fake(tmp_path, [text("summary")])
    b = fake.backend()
    assert b.call([SYSTEM, VIEW]).text == "summary"
    assert fake.requests("thread/start")[0]["dynamicTools"] == [] and b.sessions == []


def gone(pid):
    """Whether a process has ended (waited up to 5 s); a zombie no one has reaped yet counts as ended."""
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        status = os.popen(f"ps -o stat= -p {pid}").read().strip()
        if status.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("child", ["plain", "stubborn"])
def test_closing_stops_the_whole_process_group(tmp_path, child):
    fake = Fake(tmp_path, [calls(("act", {"memory": "m"}))], child=child)
    b = fake.backend()
    b.call([SYSTEM, VIEW], [COMPUTE, ACT])
    pid = fake.entries("child")[0]
    b.close()
    assert gone(pid), "the app-server's child outlived the session"


def test_a_child_that_inherits_stdin_and_stops_reading_does_not_block_the_call(tmp_path):
    fake = Fake(tmp_path, [], child="deaf")
    began = time.time()
    with pytest.raises(models.CallFailed, match="no complete response within 1 s"):
        fake.backend(llm={**LLM, "timeout_s": 1, "max_retries": 0}).call(  # thread/start: far beyond a pipe's buffer
            [{"role": "system", "content": "x" * 2_000_000}, VIEW], [COMPUTE, ACT])
    assert time.time() - began < 10 and gone(fake.entries("child")[0])


def test_codex_asking_for_a_fresh_token_is_answered_from_the_login(tmp_path):
    fake = Fake(tmp_path, [text("ok")], refresh=True)
    fake.write_login(time.time() + 90000, access=token(time.time() + 90000))
    fresh = json.loads((fake.login / "auth.json").read_text())["tokens"]["access_token"]
    fake.backend().call([SYSTEM, VIEW])
    assert fake.entries("refreshed")[0] == {"accessToken": fresh, "chatgptAccountId": "acct-1",
                                            "chatgptPlanType": None}


def test_a_failed_turn_is_retried_then_fails(tmp_path):
    fake = Fake(tmp_path, [{"error": "stream disconnected"}] * 3)
    with pytest.raises(models.CallFailed, match="after 3 attempts: Codex's turn ended without a reply: stream"):
        fake.backend().call([SYSTEM, VIEW], [COMPUTE, ACT])
    assert len(fake.slept) == 2


def test_a_subscription_limit_is_waited_out(tmp_path):
    now = 1_000_000.0
    fake = Fake(tmp_path, [{"error": "usage limit", "limit": now + 600}, text("ok")])
    fake.write_login(time.time() + 86400)
    reply = fake.backend(clock=lambda: now if fake.slept else now).call([SYSTEM, VIEW])
    assert reply.text == "ok" and len(fake.slept) == 1 and 600 <= fake.slept[0] < 601


def test_a_thread_that_loaded_instructions_is_not_used(tmp_path):
    fake = Fake(tmp_path, [], sources=["/somewhere/AGENTS.md"])
    with pytest.raises(models.CallFailed, match="loaded instruction files"):
        fake.backend(llm={**LLM, "max_retries": 0}).call([SYSTEM, VIEW], [COMPUTE, ACT])


def test_no_reply_within_the_time_limit(tmp_path):
    fake = Fake(tmp_path, [{"hang": True}])
    with pytest.raises(models.CallFailed, match="no complete response within 1 s"):
        fake.backend(llm={**LLM, "timeout_s": 1, "max_retries": 0}).call([SYSTEM, VIEW], [COMPUTE, ACT])


def test_setup(tmp_path, monkeypatch):
    fake = Fake(tmp_path, [])
    assert models.resolved(SPEC)["api_key_env"] is None
    with pytest.raises(models.SetupError, match="needs Codex's"):
        codex.backend(SPEC, LLM, fake.tracker, cli=str(tmp_path / "none"), login_home=str(fake.login))
    with pytest.raises(models.SetupError, match="needs a ChatGPT login"):
        codex.backend(SPEC, LLM, fake.tracker, cli=str(fake.cli), login_home=str(tmp_path / "nobody"))
    fake.write_login(time.time() + 3600)  # within SETUP_MARGIN
    with pytest.raises(models.SetupError, match="expires within 6 h"):
        fake.backend()
    (fake.login / "auth.json").write_text(json.dumps({"tokens": {"access_token": token(time.time() + 86400),
                                                                 "account_id": "a"}}))
    assert codex.tokens(str(fake.login))[1] == "a"  # no auth_mode: still a ChatGPT login
    (fake.login / "auth.json").write_text(json.dumps({"auth_mode": "apikey", "tokens": {
        "access_token": token(time.time() + 86400), "account_id": "a"}}))
    with pytest.raises(models.SetupError, match="needs a ChatGPT login"):
        fake.backend()
    fake.write_login(time.time() + 86400)
    with pytest.raises(models.SetupError, match="no model 'gpt-other' \\(it has: gpt-test\\)"):
        fake.backend(spec={**SPEC, "model": "gpt-other"})
    with pytest.raises(models.SetupError, match="takes no reasoning effort 'xhigh'"):
        fake.backend(spec={**SPEC, "extra_body": {"reasoning": {"effort": "xhigh"}}})
    monkeypatch.setattr(codex, "CLI", str(fake.cli))
    monkeypatch.setenv("CODEX_HOME", str(fake.login))
    b = models.backend(SPEC, LLM, models.CostTracker(None), env={})
    assert isinstance(b, codex._Backend) and b.login_home == str(fake.login)


def _cfg(**spec):
    return {"models": {"managers": {**SPEC, **spec}}}


def test_config_accepts_a_codex_spec():
    cfg = config.resolve(_cfg(extra_body={"reasoning": {"effort": "ultra"}}))
    assert cfg["models"]["managers"]["provider"] == "codex"


@pytest.mark.parametrize("spec, path", [
    ({"temperature": 0.5}, "models.managers.temperature"),
    ({"base_url": "http://x"}, "models.managers.base_url"),
    ({"api_key_env": "X"}, "models.managers.api_key_env"),
    ({"extra_body": {"reasoning": {"effort": "huge"}}}, "models.managers.extra_body"),
])
def test_config_refuses_what_codex_would_drop(spec, path):
    with pytest.raises(config.ConfigError, match=path.replace(".", r"\.")):
        config.resolve(_cfg(**spec))


def test_the_response_ignores_input_items_and_warmups():
    """Raw items that are not the model's (the user's message, a tool output) and a request with no output are not
    a response."""
    session = codex._Session.__new__(codex._Session)
    session.tools, session.limits = ["act"], None
    session.backlog = __import__("collections").deque([
        {"method": "rawResponse/completed", "params": {"usage": {"inputTokens": 50, "outputTokens": 0}}},
        {"method": "rawResponseItem/completed", "params": {"item": {"type": "message", "role": "user",
                                                                    "content": [{"type": "input_text", "text": "v"}]}}},
        {"method": "rawResponseItem/completed", "params": {"item": {"type": "function_call_output", "output": "o"}}},
        {"method": "rawResponseItem/completed", "params": {"item": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]}}},
        {"method": "rawResponse/completed", "params": {"usage": {"inputTokens": 60, "outputTokens": 3}}}])
    response, _ = session.response(time.time() + 5)
    assert (response.text, response.tokens_in, response.tokens_out, response.waits) == ("hi", 60, 3, False)
    assert isinstance(response, live.Response)
