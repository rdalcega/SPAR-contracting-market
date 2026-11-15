"""Blind tests of the public turns API. No providers or programs are invoked."""

import copy
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from market import models, turns


FORCED = {"type": "function", "function": {"name": "act"}}
NOT_COMPUTE = (
    'not run: compute takes one argument, "code", a string; '
    "send your actions with act"
)
NO_PROGRAMS = (
    "not run: no programs left this turn, and compute cannot be called "
    "again this turn; send your actions with act"
)
LAST = (
    "(That was your last program this turn: compute cannot be called "
    "again this turn. Send your actions with act.)"
)
FREE = (
    "(The program failed, so it does not count: "
    "1 more program allowed this turn.)"
)
ENDED = "not run: act ended the turn"
IN_LAST = "(the results of this reply are in the answer to its last act call)"
EMPTY = "(an empty reply)"
HEADING = "Results of your actions:"
NO_ACTIONS = "(no actions run)"
FAILED = "(note: your correction could not be sent; the results above stand)"
ONLY_CORRECTION = (
    "This is your only correction this turn, and compute cannot be called in it."
)
LOST_END = (
    ". That turn was lost: none of its actions ran. End every turn by calling "
    "the function act with a list of your actions (an empty list does nothing) "
    "and your memory."
)
MISSING = object()


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("These tests must not use the network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket.socket, "sendto", forbidden)


@pytest.fixture
def cfg():
    return {
        "credits_per_usd": 1,
        "message_channels": "off",
        "turns": {"max_actions": 6, "memory_chars": 2000},
        "compute": {
            "cpu_s": 5,
            "wall_s": 10,
            "memory_mb": 512,
            "output_kb": 64,
            "output_chars": 4000,
            "code_chars": 20000,
        },
    }


def call(name="act", arguments='{"actions": [], "memory": ""}', ident="id"):
    result = {"type": "function", "function": {"name": name}}
    if arguments is not MISSING:
        result["function"]["arguments"] = arguments
    if ident is not MISSING:
        result["id"] = ident
    return result


def reply(calls=(), text="", reasoning="", cost=0.0, finish="tool_calls"):
    # A reply protocol double: no dependency on an unspecified constructor.
    return SimpleNamespace(
        text=text,
        tool_calls=list(calls),
        reasoning=reasoning,
        cost=cost,
        finish_reason=finish,
        input_tokens=123456,
        output_tokens=987654,
        reasoning_tokens=765432,
        failed_cost=99.0,
        cost_known=True,
        cost_source="reported",
    )


def exception_of(cls, message, failed_cost=None):
    # The API fixes exception fields, but does not specify constructors.
    error = cls.__new__(cls)
    BaseException.__init__(error, message)
    error.error = message
    if failed_cost is not None:
        error.failed_cost = failed_cost
    return error


class Backend:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
        self.snapshots = []

    def call(self, messages, functions, tool_choice, /):
        request = {
            "messages": list(messages),
            "functions": functions,
            "tool_choice": tool_choice,
        }
        self.requests.append(request)
        self.snapshots.append(copy.deepcopy(request))
        assert self.outcomes, "Unexpected extra model call"
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Programs:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, code, compute_block):
        self.calls.append((code, compute_block))
        assert self.outcomes, "Unexpected program execution"
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def decide(cfg, replies, outputs=(), balance=100.0, manager=False,
           system="SYSTEM", view="VIEW"):
    backend = Backend(replies)
    programs = Programs(outputs)
    chain = turns.decide(
        system, view, backend, cfg, balance, manager, programs
    )
    assert not backend.outcomes
    assert not programs.outcomes
    return chain, backend, programs


def tool_contents(chain):
    return [
        message["content"] for message in chain.messages
        if message["role"] == "tool"
    ]


def assert_requests(chain, backend, choices, correction=False):
    assert len(backend.requests) == len(choices)
    for request, snapshot, choice in zip(
        backend.requests, backend.snapshots, choices
    ):
        assert request == snapshot
        assert request["tool_choice"] == choice
        assert [
            item["function"]["name"] for item in request["functions"]
        ] == (["act"] if correction else ["compute", "act"])
    for step, snapshot in zip(chain.steps, backend.snapshots):
        assert step["request"] == snapshot


@pytest.mark.parametrize(
    "name, expected",
    [
        ("NOT_COMPUTE", NOT_COMPUTE),
        ("NO_PROGRAMS_LEFT", NO_PROGRAMS),
        ("LAST_PROGRAM", LAST),
        ("FREE_RETRY", FREE),
        ("ACT_ENDED", ENDED),
        ("IN_LAST_ACT", IN_LAST),
        ("EMPTY_REPLY", EMPTY),
        ("RESULTS_HEADING", HEADING),
        ("NO_ACTIONS", NO_ACTIONS),
        ("CORRECTION_HEADING", "Correction:"),
        ("correction_failed", FAILED),
    ],
)
def test_exact_constants(name, expected):
    # turns.md 5.2, 5.4, 11.4, 11.6, 12.
    assert getattr(turns, name) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", ""),
        (" \t\r\nvisible ", "visible"),
        ("<think>hidden</think>Visible text", "Visible text"),
        ("a<think>x\ny</think>b<think>z</think>c", "abc"),
        ("<think>all\nhidden</think>", ""),
        ("old</think>middle</think>last", "last"),
        ("visible<think>unfinished", "visible"),
        ("a</think>b<think>c", "b"),
        ("a<think>x<think>y</think>z</think>end", "end"),
        ("a<think>x<think>y</think>z", "az"),
        ("<THINK>x</THINK>", "<THINK>x</THINK>"),
        ("<think >x</think >", "<think >x</think >"),
        ("x\u200by", "x\u200by"),
        ("\u001c\u0085\u3000x\u2028", "x"),
    ],
)
def test_strip_think(text, expected):
    # turns.md 2; README whitespace and exact, case-sensitive tags.
    assert turns.strip_think(text) == expected


@pytest.mark.parametrize("reasoning", ["", " \n", "private<think>raw</think>"])
def test_assistant_preserves_calls_and_only_sends_message_fields(reasoning):
    # turns.md 2; models.md 2.2-2.3.
    calls = [
        call("compute", '{"code":"x"}', {"odd": [1, None]}),
        {"function": None, "extra": {"provider": True}},
    ]
    r = reply(calls, " <think>hidden</think>visible ", reasoning, cost=3)
    before = copy.deepcopy(r.__dict__)
    expected = {
        "role": "assistant",
        "content": "visible",
        "tool_calls": r.tool_calls,
    }
    if reasoning:
        expected["reasoning"] = reasoning
    actual = turns.assistant(r)
    assert actual == expected
    assert list(actual) == list(expected)
    assert actual["tool_calls"] is r.tool_calls
    assert r.__dict__ == before


def test_chain_defaults_and_independent_conversations(cfg):
    # turns.md 1-2; public Chain defaults.
    messages, steps = [], []
    blank = turns.Chain(messages, steps)
    assert blank.messages is messages
    assert blank.steps is steps
    assert blank.reply is None
    assert blank.empty is False
    assert blank.error is None
    assert blank.request is None
    assert blank.failed_cost == 0.0

    for view in ["first", "second"]:
        r = reply([call()])
        chain, backend, programs = decide(cfg, [r], view=view)
        assert chain.messages == [
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": view},
        ]
        assert chain.reply is r
        assert chain.steps[0]["reply"] is r
        assert chain.steps[0]["programs"] == []
        assert not programs.calls
        assert_requests(chain, backend, ["required"])


@pytest.mark.parametrize("finish", [None, "stop", "length", "content_filter"])
@pytest.mark.parametrize(
    "calls",
    [
        [],
        [call("act", "not JSON")],
        [call("compute", '{"code":"x"}'), call()],
        [call(), call("compute", '{"code":"x"}')],
    ],
)
def test_act_or_no_calls_ends_even_when_charge_exhausts_manager(
    cfg, finish, calls
):
    # turns.md 4.2, 13.2: act/no-call precedence over the empty-act shortcut.
    r = reply(calls, cost=1, finish=finish)
    chain, backend, programs = decide(
        cfg, [r], balance=0.5, manager=True
    )
    assert chain.reply is r
    assert chain.empty is False
    assert len(chain.messages) == 2
    assert chain.steps[0]["programs"] == []
    assert chain.steps[0]["charge"] == 1.0
    assert not programs.calls
    assert_requests(chain, backend, ["required"])


def test_successful_compute_message_order_and_request_snapshots(cfg):
    # turns.md 2, 4, 5.1-5.3; models.md 2.3.
    first = reply(
        [call("compute", '{"code":"print(sum(range(10)))"}', "compute-id")],
        text=" <think>hidden</think> ",
        reasoning="hidden reasoning",
        cost=0.1,
    )
    last = reply([call()], cost=0.2)
    before = copy.deepcopy(first.__dict__)
    chain, backend, programs = decide(
        cfg, [first, last], [("ok", 0, 0.125, "45")]
    )
    assert chain.messages == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "VIEW"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": first.tool_calls,
            "reasoning": "hidden reasoning",
        },
        {
            "role": "tool",
            "tool_call_id": "compute-id",
            "content": "45\n\n" + LAST,
        },
    ]
    assert list(chain.messages[3]) == ["role", "tool_call_id", "content"]
    assert chain.messages[2]["tool_calls"] is first.tool_calls
    assert len(chain.steps[0]["request"]["messages"]) == 2
    assert len(chain.steps[1]["request"]["messages"]) == 4
    assert chain.steps[0]["request"]["messages"] is not chain.messages
    assert chain.steps[0]["request"]["messages"][0] is chain.messages[0]
    assert chain.reply is last
    assert [s["reply"] for s in chain.steps] == [first, last]
    assert [s["charge"] for s in chain.steps] == [0.1, 0.2]
    assert chain.steps[0]["programs"] == [{
        "code": "print(sum(range(10)))",
        "status": "ok",
        "exit_code": 0,
        "wall": 0.125,
        "output": "45",
    }]
    assert chain.steps[1]["programs"] == []
    assert programs.calls[0][1] is cfg["compute"]
    assert first.__dict__ == before
    assert_requests(chain, backend, ["required", FORCED])


@pytest.mark.parametrize(
    "arguments",
    [
        MISSING, None, False, 0, 1, [], ["code"], "",
        " ", "null", "false", "0", "[]", '"text"', "{}",
        {"code": None}, {"code": False}, {"code": 0}, {"code": []},
        '{"code":7}', '{"Code":"x"}', '{"code":"x"',
        '{"code":"x",}', '{"code":"x"} {}',
        '```json\n{"code":"x"}\n```',
        "{'code':'x'}", '{"code":"line\nbreak"}',
        # Adjudicated: arrived as a plain space, which 7.2's strict reading allows around the value (the
        # valid case ' \t\r\n{"code":"x"} \n' below); U+00A0 is not JSON whitespace, so this is refused.
        '\u00a0{"code":"x"}',
        '{"code":"x","code":null}',
    ],
)
def test_malformed_compute_is_answered_without_running(cfg, arguments):
    # turns.md 5.2; strict JSON is defined in 7.2.
    first = reply([call("compute", arguments)])
    chain, backend, programs = decide(cfg, [first, reply([call()])])
    assert tool_contents(chain) == [NOT_COMPUTE]
    assert chain.steps[0]["programs"] == []
    assert not programs.calls
    assert_requests(chain, backend, ["required", "required"])


@pytest.mark.parametrize(
    "arguments, expected_code",
    [
        ({"code": "  x\n", "extra": 1}, "  x\n"),
        (' \t\r\n{"code":"x"} \n', "x"),
        ('{"code":null,"code":"last"}', "last"),
        ('{"code":"x","extra":NaN}', "x"),
        ('{"code":"x","extra":Infinity}', "x"),
        ('{"code":"x","extra":-Infinity}', "x"),
        ('{"code":"\\ud800"}', "\ud800"),
        ('{"code":"\\ud83d\\ude00"}', "\U0001f600"),
    ],
)
def test_valid_compute_arguments_are_passed_unchanged(cfg, arguments, expected_code):
    # turns.md 5.2 and 7.2: decoded objects, duplicate keys, JSON extensions.
    first = reply([call("compute", arguments)])
    chain, backend, programs = decide(
        cfg, [first, reply([call()])], [("ok", 0, 1.25, " \noutput\n")]
    )
    assert programs.calls == [(expected_code, cfg["compute"])]
    assert tool_contents(chain) == [" \noutput\n\n\n" + LAST]
    assert chain.steps[0]["programs"][0]["output"] == " \noutput\n"
    assert_requests(chain, backend, ["required", FORCED])


@pytest.mark.parametrize(
    "bad_call",
    [
        {}, {"function": None}, {"function": []},
        {"function": {"arguments": '{"code":"x"}'}},
        call("Compute", '{"code":"x"}'),
        call(" compute", '{"code":"x"}'),
        call("compute ", '{"code":"x"}'),
        call("ACT", '{"code":"x"}'),
        call(None, '{"code":"x"}'),
        call(1, '{"code":"x"}'),
        call(["compute"], '{"code":"x"}'),
        call({"name": "compute"}, '{"code":"x"}'),
    ],
)
def test_names_are_exact_and_malformed_calls_still_get_answers(cfg, bad_call):
    # turns.md 2, 4.2, 5.1-5.2.
    chain, backend, programs = decide(
        cfg, [reply([bad_call]), reply([call()])]
    )
    assert tool_contents(chain) == [NOT_COMPUTE]
    assert not programs.calls
    assert_requests(chain, backend, ["required", "required"])


def test_every_call_gets_its_original_id_in_original_order(cfg):
    # turns.md 2 and 5.1: missing, repeated, and arbitrary JSON ids.
    ids = [MISSING, None, False, 0, "same", "same", [], {"x": [1]}]
    calls = [call("other", "{}", ident) for ident in ids]
    chain, _, _ = decide(cfg, [reply(calls), reply([call()])])
    answers = chain.messages[3:]
    assert len(answers) == len(calls)
    for answer, ident in zip(answers, ids):
        assert answer == {
            "role": "tool",
            "tool_call_id": None if ident is MISSING else ident,
            "content": NOT_COMPUTE,
        }
        assert list(answer) == ["role", "tool_call_id", "content"]


@pytest.mark.parametrize("status", ["error", "refused", "timeout", "memory", "OK"])
@pytest.mark.parametrize("code", ["1/0", "", " \t\n"])
def test_any_non_ok_status_grants_exactly_one_retry(cfg, status, code):
    # turns.md 5.2, 5.4: even a blank/refused program consumes an attempt.
    first = reply([call("compute", {"code": code})])
    second = reply([call("compute", {"code": "retry"})])
    final = reply([call()])
    chain, backend, programs = decide(
        cfg,
        [first, second, final],
        [(status, None, 0.0, "failed"), ("refused", None, 0.0, "")],
    )
    assert tool_contents(chain) == ["failed\n\n" + FREE, "\n\n" + LAST]
    assert [c[0] for c in programs.calls] == [code, "retry"]
    assert [len(s["programs"]) for s in chain.steps] == [1, 1, 0]
    assert_requests(chain, backend, ["required", "required", FORCED])


def test_free_retry_worked_traceback_example(cfg):
    # turns.md 5.4: traceback is supplied by the runner; only the tail is ours.
    output = (
        "Traceback (most recent call last):\n"
        '  File "<program>", line 1, in <module>\n'
        "ZeroDivisionError: division by zero\n"
        "(exit code 1)"
    )
    chain, backend, _ = decide(
        cfg,
        [reply([call("compute", {"code": "1/0"})]), reply([call()])],
        [("error", 1, 0.25, output)],
    )
    assert tool_contents(chain) == [output + "\n\n" + FREE]
    assert chain.steps[0]["programs"][0]["output"] == output
    assert_requests(chain, backend, ["required", "required"])


@pytest.mark.parametrize("first_status", ["ok", "refused"])
def test_multiple_calls_share_budget_and_retry_within_one_reply(cfg, first_status):
    # turns.md 5.2, 5.4: validation precedes the exhausted-budget check.
    calls = [
        call("compute", {"code": "first"}),
        call("other", {"code": "not a program"}),
        call("compute", {"code": "second"}),
        call("compute", {"code": None}),
        call("compute", {"code": "third"}),
    ]
    outputs = [(first_status, 0, 0.1, "one")]
    if first_status != "ok":
        outputs.append(("error", 1, 0.2, "two"))
    chain, backend, programs = decide(
        cfg, [reply(calls), reply([call()])], outputs
    )
    expected = (
        ["one\n\n" + LAST, NOT_COMPUTE, NO_PROGRAMS, NOT_COMPUTE, NO_PROGRAMS]
        if first_status == "ok"
        else ["one\n\n" + FREE, NOT_COMPUTE, "two\n\n" + LAST,
              NOT_COMPUTE, NO_PROGRAMS]
    )
    assert tool_contents(chain) == expected
    assert len(programs.calls) == (1 if first_status == "ok" else 2)
    assert_requests(chain, backend, ["required", FORCED])


@pytest.mark.parametrize("third_is_act", [False, True])
def test_two_no_program_steps_force_third_call_and_then_end(cfg, third_is_act):
    # turns.md 4.1-4.2: the no-program worked sequence and three-call bound.
    replies = [
        reply([call("compute", "{}")]),
        reply([call("compute", "{}")]),
        reply([call() if third_is_act else call("compute", {"code": "x"})]),
    ]
    chain, backend, programs = decide(cfg, replies)
    assert chain.reply is replies[-1]
    assert chain.empty is False
    assert tool_contents(chain) == [NOT_COMPUTE, NOT_COMPUTE]
    assert [s["programs"] for s in chain.steps] == [[], [], []]
    assert not programs.calls
    assert_requests(chain, backend, ["required", "required", FORCED])


def test_ignored_forced_act_does_not_run_a_third_step(cfg):
    # turns.md 4.2 worked sequence: ran compute, refused compute, terminal compute.
    replies = [
        reply([call("compute", {"code": str(i)})]) for i in range(3)
    ]
    chain, backend, programs = decide(
        cfg, replies, [("ok", 0, 0.1, "first")]
    )
    assert chain.reply is replies[2]
    assert chain.empty is False
    assert tool_contents(chain) == ["first\n\n" + LAST, NO_PROGRAMS]
    assert [c[0] for c in programs.calls] == ["0"]
    assert_requests(chain, backend, ["required", FORCED, FORCED])


def test_free_retry_allows_four_calls_but_not_a_fourth_compute_step(cfg):
    # turns.md 4.1-4.2, 5.4: free discounts both counts, only once.
    replies = [
        reply([call("compute", {"code": "fails"})]),
        reply([call("other", "{}")]),
        reply([call("compute", {"code": "retry"})]),
        reply([call("compute", {"code": "must not run"})]),
    ]
    chain, backend, programs = decide(
        cfg, replies, [("error", 1, 0.1, "bad"), ("ok", 0, 0.2, "good")]
    )
    assert chain.reply is replies[-1]
    assert chain.empty is False
    assert [c[0] for c in programs.calls] == ["fails", "retry"]
    assert tool_contents(chain) == [
        "bad\n\n" + FREE, NOT_COMPUTE, "good\n\n" + LAST
    ]
    assert_requests(chain, backend, ["required", "required", "required", FORCED])


def test_retry_granted_on_second_step_extends_chain(cfg):
    # turns.md 4.2, 5.4: use free as it stood before the reply.
    # Adjudicated: before the third call programs = 1, calls = 2 and free = 1, so by 4.1 neither
    # `programs - free >= 1` nor `calls - free >= 2` holds and the choice is "required" (5.4: "the next
    # call of the chain may run it"); the blind test expected act forced there.
    replies = [
        reply([call("other", "{}")]),
        reply([call("compute", {"code": "fails"})]),
        reply([call("compute", {"code": "retry"})]),
        reply([call()]),
    ]
    chain, backend, _ = decide(
        cfg, replies, [("error", 1, 0.1, "bad"), ("ok", 0, 0.2, "good")]
    )
    assert tool_contents(chain) == [
        NOT_COMPUTE, "bad\n\n" + FREE, "good\n\n" + LAST
    ]
    assert_requests(chain, backend, ["required", "required", "required", FORCED])


@pytest.mark.parametrize(
    "usd, rate, expected",
    [
        (0.0, 1, 0.0),
        (-1.25, 2, -2.5),
        (0.0000004, 1, 0.0),
        (-0.0000004, 1, -0.0),
        (0.0000004, 10, 0.000004),
        (0.0078125, 1, 0.007812),
        (0.0234375, 1, 0.023438),
        (-0.0078125, 1, -0.007812),
        (-0.0234375, 1, -0.023438),
        (0.00390625, 2, 0.007812),
        (1.23456789, 1, 1.234568),
    ],
)
@pytest.mark.parametrize("correction", [False, True])
def test_reply_charges_multiply_then_round_ties_to_even(
    cfg, usd, rate, expected, correction
):
    # turns.md 13.1; models.md 5; README double rounding.
    cfg["credits_per_usd"] = rate
    r = reply([], cost=usd, finish="length")
    if correction:
        backend = Backend([r])
        chain = turns.correct([{"role": "user", "content": "body"}], backend, cfg, "buyer")
    else:
        chain, _, _ = decide(cfg, [r])
    assert chain.steps[0]["charge"] == expected
    assert chain.steps[0]["reply"] is r
    assert chain.steps[0]["programs"] == []
    # Huge token counts and failed-attempt cost in the double do not add charges.


@pytest.mark.parametrize(
    "manager, balance, cost, empty",
    [
        (True, 1.0, 1.0, True),
        (True, 0.5, 1.0, True),
        (True, 0.0, 0.000001, True),
        (True, -1.0, 0.000001, True),
        (True, 1.0000001, 1.0, False),
        (True, 0.0, 0.0, False),
        (True, -1.0, -1.0, False),
        (True, -1.0, 0.0000004, False),
        (False, 0.0, 1.0, False),
        (False, -10.0, 100.0, False),
    ],
)
def test_out_of_business_requires_positive_charge_and_manager(
    cfg, manager, balance, cost, empty
):
    # turns.md 4.2, 13.2; episode.md 9.1: no epsilon around zero.
    first = reply([call("compute", {"code": "x"})], cost=cost)
    replies = [first] if empty else [first, reply([call()])]
    outputs = [] if empty else [("ok", 0, 0.1, "x")]
    chain, backend, programs = decide(
        cfg, replies, outputs, balance=balance, manager=manager
    )
    assert chain.empty is empty
    assert len(programs.calls) == (0 if empty else 1)
    if empty:
        assert chain.reply is first
        assert len(chain.messages) == 2
        assert chain.steps[0]["programs"] == []
    assert_requests(chain, backend, ["required"] if empty else ["required", FORCED])


def test_negative_charges_do_not_fund_later_programs(cfg):
    # turns.md 13.1-13.2: skip nonpositive charges in the balance projection.
    first = reply([call("other", "{}")], cost=-100)
    last = reply([call("compute", {"code": "x"})], cost=1)
    chain, backend, programs = decide(
        cfg, [first, last], balance=1, manager=True
    )
    assert chain.empty is True
    assert chain.reply is last
    assert [s["charge"] for s in chain.steps] == [-100, 1]
    assert tool_contents(chain) == [NOT_COMPUTE]
    assert not programs.calls
    assert_requests(chain, backend, ["required", "required"])


def test_prior_positive_charges_can_exhaust_manager(cfg):
    # turns.md 13.1-13.2: include every earlier reply's charge.
    first = reply([call("other", "{}")], cost=0.75)
    last = reply([call("compute", {"code": "x"})], cost=0.25)
    chain, _, programs = decide(cfg, [first, last], balance=1, manager=True)
    assert chain.empty is True
    assert chain.reply is last
    assert not programs.calls


def test_projection_subtracts_charges_one_by_one_not_as_a_sum(cfg):
    # turns.md 13.1; rounds.md 4: sequential double subtraction.
    # ((1.0 - 0.7) - 0.2) - 0.1 is positive, about 2.78e-17.
    replies = [
        reply([call("compute", {"code": "fails"})], cost=0.7),
        reply([call("other", "{}")], cost=0.2),
        reply([call("compute", {"code": "retry"})], cost=0.1),
        reply([call()]),
    ]
    chain, _, programs = decide(
        cfg, replies, [("error", 1, 0.1, ""), ("ok", 0, 0.1, "")],
        balance=1, manager=True,
    )
    assert chain.empty is False
    assert chain.reply is replies[-1]
    assert [c[0] for c in programs.calls] == ["fails", "retry"]


@pytest.mark.parametrize("prior_reply", [False, True])
def test_failed_model_call_keeps_prior_steps_and_failed_request(cfg, prior_reply):
    # turns.md 13.1; models.md 2.4-2.5, 5: failed cost is log-only.
    failure = exception_of(models.CallFailed, "provider failure", 0.875)
    first = reply([call("compute", {"code": "x"})], cost=0.125)
    replies = [first, failure] if prior_reply else [failure]
    outputs = [("ok", 0, 0.1, "x")] if prior_reply else []
    chain, backend, _ = decide(cfg, replies, outputs)
    assert chain.reply is None
    assert chain.error == "provider failure"
    assert chain.failed_cost == 0.875
    assert chain.empty is False
    assert chain.request == backend.snapshots[-1]
    assert chain.messages == backend.snapshots[-1]["messages"]
    assert len(chain.steps) == int(prior_reply)
    if prior_reply:
        assert chain.steps[0]["reply"] is first
        assert chain.steps[0]["charge"] == 0.125
        assert chain.steps[0]["programs"][0]["output"] == "x"


@pytest.mark.parametrize("correction", [False, True])
@pytest.mark.parametrize("kind", ["cap", "defect"])
def test_model_exceptions_other_than_callfailed_propagate(cfg, correction, kind):
    # turns.md 4; models.md 2.4: cap stops and defects are not provider failures.
    error = (
        exception_of(models.CostCapReached, "cap")
        if kind == "cap" else RuntimeError("defect")
    )
    backend = Backend([error])
    with pytest.raises(type(error)) as caught:
        if correction:
            turns.correct([], backend, cfg, "buyer")
        else:
            turns.decide("s", "v", backend, cfg, 100, False, Programs())
    assert caught.value is error


def test_program_runner_exception_propagates(cfg):
    # turns.md 5 and public API: inability to run isolated is not a model failure.
    error = RuntimeError("cannot isolate")
    backend = Backend([reply([call("compute", {"code": "x"})])])
    with pytest.raises(RuntimeError) as caught:
        turns.decide("s", "v", backend, cfg, 100, False, Programs([error]))
    assert caught.value is error


@pytest.mark.parametrize("limit, unit, pronoun", [(1, "action", "it"),
                                                  (2, "actions", "them"),
                                                  (6, "actions", "them"),
                                                  (1000000, "actions", "them")])
@pytest.mark.parametrize("unreadable", [False, True])
def test_correction_note_exact_limit_and_singular_rule(
    limit, unit, pronoun, unreadable
):
    # turns.md 11.2; config.md 3.6: positive limits, no upper bound.
    if unreadable:
        expected = (
            "Your reply could not be read, so none of its actions ran (below). "
            f"You may call act again now, with up to {limit} {unit} and your "
            f"memory. {ONLY_CORRECTION}"
        )
    else:
        expected = (
            f"{limit} of your actions failed (the results of your reply are "
            "below; the actions that succeeded have run and stand). "
            f"You may send up to {limit} {unit} now to replace {pronoun}, "
            "in the same format as before, with your memory, which then "
            f"replaces the one you just sent. {ONLY_CORRECTION}"
        )
    assert turns.correction_note(limit, unreadable) == expected


@pytest.mark.parametrize("news", [None, [], ["n1\nembedded", "n2"]])
@pytest.mark.parametrize("facts", [None, [], ["post fact", "balance fact"]])
def test_correction_body_preserves_supplied_spacing_and_order(news, facts):
    # turns.md 11.3; view.md 4.3: result text is already spaced.
    result_text = "- one: ERROR: a\nquoted\n\n- two: OK: b"
    before = copy.deepcopy((news, facts))
    expected = "note\n\nResults of your reply:\n" + result_text
    if news:
        expected += (
            "\n\nWhat reached you from this round so far "
            "(your next view shows it too):\nn1\nembedded\nn2"
        )
    if facts:
        expected += "\n\npost fact\nbalance fact"
    assert turns.correction_body("note", result_text, news, facts) == expected
    assert (news, facts) == before


def test_correction_body_worked_bid_example():
    # turns.md 11.3: one failed bid out of three, with news.
    note = (
        "1 of your actions failed (the results of your reply are below; "
        "the actions that succeeded have run and stand). You may send up to "
        "1 action now to replace it, in the same format as before, with your "
        "memory, which then replaces the one you just sent. "
        + ONLY_CORRECTION
    )
    lines = (
        "- bid: ERROR: post-8 closed earlier this round, when its buyer "
        "accepted a bid (this round B-d75 accepted another bid on post-8 "
        "before your bid ran)\n\n"
        "- bid: OK: placed bid-21 on post-7 at 24 with threshold 6 (escrow)\n\n"
        "- bid: OK: placed bid-22 on post-6 at 24 with threshold 6 (escrow)"
    )
    news = (
        "[day 6, round 3] B-c53 approved R-815 on contract-1: you received "
        "25.00 of the price 25; contract-1 is closed."
    )
    assert turns.correction_body(note, lines, [news], []) == (
        note + "\n\nResults of your reply:\n" + lines
        + "\n\nWhat reached you from this round so far "
        "(your next view shows it too):\n" + news
    )


def test_correction_tool_path_answers_last_act_and_every_other_call(cfg):
    # turns.md 2, 11.4: last act wins; ids and full call objects are preserved.
    calls = [
        call("act", "{}", "same"),
        call("compute", '{"code":"x"}', "same"),
        call(" compute", "{}", MISSING),
        call("Compute", "{}", {"odd": True}),
        call("act", " \n{}\t", None),
        call("unknown", '{"x":NaN}', False),
    ]
    r = reply(calls, " <think>secret</think>visible ", "reasoning")
    prefix = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "v"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "tool_call_id": "old", "content": "old output"},
    ]
    chain = turns.Chain(prefix, [], reply=r)
    before = copy.deepcopy(prefix)
    result = turns.correction_messages(chain, {"error": None}, "BODY")
    assert result is not prefix
    assert prefix == before
    assert result[:4] == prefix
    assert all(a is b for a, b in zip(result, prefix))
    assert result[4] == {
        "role": "assistant", "content": "visible",
        "tool_calls": r.tool_calls, "reasoning": "reasoning",
    }
    assert result[4]["tool_calls"] is r.tool_calls
    expected_contents = [IN_LAST, ENDED, IN_LAST, IN_LAST, "BODY", IN_LAST]
    expected_ids = ["same", "same", None, {"odd": True}, None, False]
    for answer, ident, content in zip(result[5:], expected_ids, expected_contents):
        assert answer == {
            "role": "tool", "tool_call_id": ident, "content": content,
        }
        assert list(answer) == ["role", "tool_call_id", "content"]
    assert len(result) == 11


@pytest.mark.parametrize(
    "arguments",
    [
        MISSING, None, False, 0, [], {}, {"actions": []},
        "", " \t", "null", "[]", '"string"', '{"actions":[]',
        # Adjudicated: ' {}' with a plain space is a clean object string (7.2; the tool-path test above
        # uses " \n{}\t"); U+00A0 is not JSON whitespace, so this one is not.
        '{"x":1,}', '{} {}', '\u00a0{}', '{"x":"a\nb"}',
    ],
)
def test_any_unclean_call_forces_correction_text_path(arguments):
    # turns.md 11.4: every call must contain a strict JSON object string.
    r = reply([call(), call("unknown", arguments)], "visible", "private")
    chain = turns.Chain([], [], reply=r)
    assert turns.correction_messages(chain, {"error": None}, "BODY") == [
        {"role": "assistant", "content": "visible", "reasoning": "private"},
        {"role": "user", "content": "BODY"},
    ]


@pytest.mark.parametrize(
    "error, finish",
    [
        ("unreadable", "tool_calls"),
        (None, "length"),
        ("unreadable", "length"),
    ],
)
def test_unreadable_or_cut_reply_forces_text_path(error, finish):
    # turns.md 11.4: readable JSON alone does not select the tool path.
    r = reply([call()], " <think>x</think>visible ", finish=finish)
    chain = turns.Chain([], [], reply=r)
    assert turns.correction_messages(chain, {"error": error}, "BODY") == [
        {"role": "assistant", "content": "visible"},
        {"role": "user", "content": "BODY"},
    ]


@pytest.mark.parametrize("finish", [None, "stop", "tool_calls", "content_filter"])
def test_other_finish_reasons_do_not_force_text_path(finish):
    # turns.md 11.4; models.md 2.2: only "length" means cut here.
    r = reply([call("act", '{"x":Infinity}')], finish=finish)
    result = turns.correction_messages(
        turns.Chain([], [], reply=r), {"error": None}, "BODY"
    )
    assert result == [
        {"role": "assistant", "content": "", "tool_calls": r.tool_calls},
        {"role": "tool", "tool_call_id": "id", "content": "BODY"},
    ]


def test_text_path_fallback_omits_empty_values_and_uses_text_conversion():
    # turns.md 11.4; README TEXT/PY, including Unicode whitespace.
    omitted = [MISSING, None, False, 0, 0.0, -0.0, "", [], {},
               " \t\n \u3000"]
    included = [
        "  literal  ", True, 17, -23, 3.0, 1e-5, 1e16,
        [None, True, "é"], {"a": "x\n", "b": False},
        "\u200b", "0", "false", "[]", "{}",
    ]
    r = reply([call("act", value) for value in omitted + included],
              "<think>nothing visible</think>", "secret")
    expected = (
        "  literal  \nTrue\n17\n-23\n3.0\n1e-05\n1e+16\n"
        "[None, True, 'é']\n{'a': 'x\\n', 'b': False}\n"
        "\u200b\n0\nfalse\n[]\n{}"
    )
    result = turns.correction_messages(
        turns.Chain([], [], reply=r), {"error": "bad"}, "BODY"
    )
    assert result == [
        {"role": "assistant", "content": expected, "reasoning": "secret"},
        {"role": "user", "content": "BODY"},
    ]
    assert list(result[0]) == ["role", "content", "reasoning"]


@pytest.mark.parametrize("calls", [[], [call("act", MISSING)],
                                  [call("act", "  "), call("act", {})]])
def test_text_path_empty_reply_placeholder(calls):
    # turns.md 11.4.
    r = reply(calls, "<think>hidden</think>")
    result = turns.correction_messages(
        turns.Chain([], [], reply=r), {"error": "bad"}, "BODY"
    )
    assert result == [
        {"role": "assistant", "content": EMPTY},
        {"role": "user", "content": "BODY"},
    ]


def test_text_path_visible_text_has_priority_and_is_never_cut():
    # turns.md 11.4: the fixed argument cap does not cap visible text.
    visible = "\U0001f600" * 2100
    r = reply([call("act", "ARGUMENT")], "\t" + visible + "\n")
    result = turns.correction_messages(
        turns.Chain([], [], reply=r), {"error": "bad"}, "BODY"
    )
    assert result[0] == {"role": "assistant", "content": visible}


@pytest.mark.parametrize("length", [1999, 2000, 2001])
def test_text_fallback_caps_each_argument_at_2000_codepoints(length):
    # config.md 5 and API 11.4; conflicts with turns.md 11.4's "in full".
    first = "\U0001f600" * length
    second = "b" * 2001
    r = reply([call("act", first), call("act", second)])
    result = turns.correction_messages(
        turns.Chain([], [], reply=r), {"error": "bad"}, "BODY"
    )
    assert result[0]["content"] == (
        "\U0001f600" * min(length, 2000) + "\n" + "b" * 2000
    )


def test_text_fallback_cuts_after_decoded_value_conversion():
    # config.md 5; turns.md 11.4; README TEXT/PY.
    r = reply([call("act", ["x" * 2100])])
    result = turns.correction_messages(
        turns.Chain([], [], reply=r), {"error": "bad"}, "BODY"
    )
    assert result[0]["content"] == "['" + "x" * 1998


@pytest.mark.parametrize(
    "calls",
    [
        [],
        [call("compute", {"code": "never run"})],
        [call("compute", {"code": "never run"}), call()],
        [call("act", "not JSON")],
    ],
)
def test_correct_always_makes_exactly_one_forced_act_only_call(cfg, calls):
    # turns.md 4.3, 11.5, 13: no compute steps or further correction.
    messages = [{"role": "user", "content": "correction body"}]
    before = copy.deepcopy(messages)
    r = reply(calls, cost=0.125, finish="length")
    backend = Backend([r])
    chain = turns.correct(messages, backend, cfg, "buyer")
    assert chain.messages is messages
    assert messages == before
    assert chain.reply is r
    assert chain.empty is False
    assert chain.error is None
    assert chain.request is None
    assert chain.failed_cost == 0.0
    assert len(chain.steps) == 1
    assert chain.steps[0]["reply"] is r
    assert chain.steps[0]["programs"] == []
    assert chain.steps[0]["charge"] == 0.125
    assert chain.steps[0]["request"]["messages"] is not messages
    assert_requests(chain, backend, [FORCED], correction=True)


def test_correct_provider_failure_has_no_step_or_charge(cfg):
    # turns.md 11.6; models.md 5: failed calls are not charged.
    messages = [{"role": "user", "content": "body"}]
    failure = exception_of(models.CallFailed, "failed correction", 2.5)
    backend = Backend([failure])
    chain = turns.correct(messages, backend, cfg, "buyer")
    assert chain.messages is messages
    assert chain.steps == []
    assert chain.reply is None
    assert chain.empty is False
    assert chain.error == "failed correction"
    assert chain.failed_cost == 2.5
    assert chain.request == backend.snapshots[0]
    assert_requests(chain, backend, [FORCED], correction=True)


@pytest.mark.parametrize(
    "err",
    [
        "your reply did not call act: end your turn by calling act",
        "problem - your reply hit the length limit before its act call was "
        "complete; reason more briefly",
        "error with\nan embedded line",
    ],
)
def test_outcome_lines_preserve_entire_error(err):
    # turns.md 11.3, 11.6: error construction belongs to reading.
    assert turns.format_line(err) == (
        "- FORMAT ERROR: " + err + ": none of your actions ran"
    )
    assert turns.correction_format_line(err) == (
        "Correction: FORMAT ERROR: " + err + ": none of its actions ran"
    )


@pytest.mark.parametrize("err2", [None, "second error"])
def test_lost_text_exact_single_and_double_error_forms(err2):
    # turns.md 12: no results heading for a lost format-error turn.
    expected = "FORMAT ERROR: first error"
    if err2 is not None:
        expected += "; and your correction: second error"
    expected += LOST_END
    assert turns.lost_text("first error", err2) == expected


@pytest.mark.parametrize(
    "first, correction, memory, expected",
    [
        ([], None, None, [HEADING, NO_ACTIONS]),
        ([], None, "memory note", [HEADING, "memory note"]),
        (["first"], None, None, [HEADING, "first"]),
        ([], [], None, [HEADING, "Correction:", NO_ACTIONS]),
        (["first"], [], "memory", [
            HEADING, "first", "Correction:", NO_ACTIONS, "memory"
        ]),
        ([], ["(note: compute beside act)"], None, [
            HEADING, "Correction:", "(note: compute beside act)"
        ]),
        (["first", FAILED], None, "memory", [
            HEADING, "first", FAILED, "memory"
        ]),
        (["first", "Correction: FORMAT ERROR: bad: none of its actions ran"],
         None, None, [
             HEADING, "first",
             "Correction: FORMAT ERROR: bad: none of its actions ran"
         ]),
        (["- FORMAT ERROR: bad: none of your actions ran"],
         ["- pay: OK: paid"], "memory", [
             HEADING, "- FORMAT ERROR: bad: none of your actions ran",
             "Correction:", "- pay: OK: paid", "memory"
         ]),
        (["opening", "- action: first\n(note: quoted)\nlast", "out of business"],
         ["correction opening", "- action: correction"], "memory", [
             HEADING, "opening", "- action: first\n(note: quoted)\nlast",
             "out of business", "Correction:", "correction opening",
             "- action: correction", "memory"
         ]),
    ],
)
def test_results_entries_preserve_order_and_distinguish_no_correction(
    first, correction, memory, expected
):
    # turns.md 11.6, 12; view.md 4.3: never split a multiline entry.
    before = copy.deepcopy((first, correction))
    actual = turns.results(first, correction, memory)
    assert actual == expected
    assert actual is not first
    assert actual is not correction
    assert (first, correction) == before


def test_results_worked_deliver_example():
    # turns.md 12: stored text, before the view adds spacing.
    first = [
        "- deliver: ERROR: no contract with id 'contract-1'. "
        "Your contracts: none. A bid becomes a contract only when "
        "the buyer accepts it."
    ]
    actual = "\n".join(turns.results(first, [], None))
    assert actual == (
        "Results of your actions:\n"
        "- deliver: ERROR: no contract with id 'contract-1'. "
        "Your contracts: none. A bid becomes a contract only when "
        "the buyer accepts it.\n"
        "Correction:\n"
        "(no actions run)"
    )


def example_messages(filename):
    root = Path(__file__).resolve().parents[2]
    return json.loads(
        (root / "spec" / "examples" / filename).read_text(encoding="utf-8")
    )


def test_compute_acceptance_example(cfg):
    # README acceptance check 4; turns.md 5.3.
    expected = example_messages("turn_with_compute_off_B-d75.json")
    scripted = expected[1]
    r = reply(scripted["tool_calls"], scripted["content"])
    chain, backend, _ = decide(
        cfg,
        [r, reply([call()])],
        [("ok", 0, 0.25, "45")],
        balance=135.60,
        view=expected[0]["content"],
    )
    assert backend.snapshots[1]["messages"][1:] == expected
    assert_requests(chain, backend, ["required", FORCED])


def test_correction_acceptance_example(cfg):
    # README acceptance check 4; turns.md 5.3, 11.2-11.5.
    expected = example_messages("turn_with_correction_off.json")
    compute_message, act_message = expected[1], expected[3]
    first = reply(compute_message["tool_calls"], compute_message["content"])
    last = reply(act_message["tool_calls"], act_message["content"])
    chain, _, _ = decide(
        cfg,
        [first, last],
        [("ok", 0, 0.25, "45")],
        balance=76.80,
        manager=True,
        view=expected[0]["content"],
    )
    note = turns.correction_note(1, False)
    body = turns.correction_body(
        note,
        "- accept_bid: ERROR: 'accept_bid' is not available to a manager: "
        "it is a buyer's action",
        [],
        [],
    )
    messages = turns.correction_messages(chain, {"error": None}, body)
    assert messages[1:] == expected
    backend = Backend([reply([call()])])
    corrected = turns.correct(messages, backend, cfg, "buyer")
    assert backend.snapshots[0]["messages"][1:] == expected
    assert_requests(corrected, backend, [FORCED], correction=True)
