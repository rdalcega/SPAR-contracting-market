"""A turn as a model conversation (spec/turns.md 1, 2, 4, 5, 11, 12, 13) and the turn half of acceptance check 4."""

import copy
import json
from pathlib import Path

import pytest

from market import sandbox
from market.models import CallFailed, CostCapReached, Reply
from market.reading import COMPUTE_BESIDE_ACT, CUT, NO_ACT, NO_MEMORY, NOT_A_CALL, UNREADABLE, read_reply
from market.turns import (
    ACT_ENDED,
    ACT_FORCED,
    EMPTY_REPLY,
    FREE_RETRY,
    IN_LAST_ACT,
    LAST_PROGRAM,
    NO_PROGRAMS_LEFT,
    NOT_COMPUTE,
    Chain,
    assistant,
    correct,
    correction_body,
    correction_failed,
    correction_format_line,
    correction_messages,
    correction_note,
    decide,
    format_line,
    functions,
    lost_text,
    results,
    strip_think,
)

EXAMPLES = Path(__file__).parent.parent / "spec" / "examples"
CFG = {
    "compute": {"cpu_s": 5, "wall_s": 10, "memory_mb": 512, "output_kb": 64, "output_chars": 4000,
                "code_chars": 20000},
    "turns": {"max_actions": 6, "memory_chars": 2000},
    "credits_per_usd": 1.0,
    "message_channels": "off",
}
SYSTEM = {"role": "system", "content": "the system prompt"}
VIEW = {"role": "user", "content": "the view"}
FAILED = ("error", 1, 0.02, "Traceback (most recent call last):\nZeroDivisionError: division by zero\n(exit code 1)")


def call(name, arguments, id="c1"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": arguments}}


def compute(code="print(1)", id="c1"):
    return call("compute", json.dumps({"code": code}), id)


def act(arguments='{"actions": [], "memory": "m"}', id="a1"):
    return call("act", arguments, id)


def reply(*calls, text="", **fields):
    return Reply(text=text, tool_calls=list(calls) or None, **fields)


class Scripted:
    """A model that returns the given replies in order; an exception among them is raised."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def call(self, messages, functions=None, tool_choice=None):
        self.requests.append(
            {"messages": copy.deepcopy(messages), "functions": functions, "tool_choice": tool_choice})
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Programs:
    """A run_program that gives each code its scripted result, else status ok with the output `<code>`."""

    def __init__(self, **by_code):
        self.by_code = by_code
        self.ran = []

    def __call__(self, code, compute):
        assert compute is CFG["compute"]
        self.ran.append(code)
        return self.by_code.get(code, ("ok", 0, 0.01, f"<{code}>"))


def run(*replies, programs=None, balance=100.0, manager=False, cfg=CFG):
    backend, programs = Scripted(*replies), programs or Programs()
    chain = decide(SYSTEM["content"], VIEW["content"], backend, cfg, balance, manager, programs)
    return chain, backend, programs


def choices(backend):
    return [request["tool_choice"] for request in backend.requests]


def answers(messages):
    return [message["content"] for message in messages if message["role"] == "tool"]


def last(output):
    return output + "\n\n" + LAST_PROGRAM


def retry(output):
    return output + "\n\n" + FREE_RETRY


# --- acceptance check 4: the two example files -----------------------------------------------------------------


def example(name):
    text = (EXAMPLES / name).read_text(encoding="utf-8")
    return text, json.loads(text)


def as_file(messages):
    return json.dumps(messages, indent=1, ensure_ascii=False)


def test_example_turn_with_compute():
    text, file = example("turn_with_compute_off_B-d75.json")
    first = Reply(text=file[1]["content"], tool_calls=file[1]["tool_calls"])
    backend = Scripted(first, reply(act()))
    programs = Programs(**{"print(sum(range(10)))": ("ok", 0, 0.03, "45")})
    chain = decide("the system prompt", file[0]["content"], backend, CFG, 135.60, False, programs)
    second = backend.requests[1]
    assert second["messages"][0] == SYSTEM
    assert second["messages"][1:] == file
    assert as_file(second["messages"][1:]) == text
    assert chain.steps[1]["request"]["messages"] == second["messages"]
    assert programs.ran == ["print(sum(range(10)))"]
    assert choices(backend) == ["required", ACT_FORCED]
    assert [request["functions"] for request in backend.requests] == [functions(CFG, "buyer")] * 2


@pytest.mark.skipif(sandbox.host_check() is not None, reason="programs cannot be run isolated on this host")
def test_example_turn_with_compute_really_run():
    text, file = example("turn_with_compute_off_B-d75.json")
    backend = Scripted(Reply(text="", tool_calls=file[1]["tool_calls"]), reply(act()))
    chain = decide("the system prompt", file[0]["content"], backend, CFG, 135.60, False, sandbox.run_program)
    assert as_file(backend.requests[1]["messages"][1:]) == text
    program = chain.steps[0]["programs"][0]
    assert (program["status"], program["exit_code"], program["output"]) == ("ok", 0, "45")


def test_example_turn_with_correction():
    text, file = example("turn_with_correction_off.json")
    backend = Scripted(
        Reply(text=file[1]["content"], tool_calls=file[1]["tool_calls"]),
        Reply(text=file[3]["content"], tool_calls=file[3]["tool_calls"]),
    )
    programs = Programs(**{"print(sum(range(10)))": ("ok", 0, 0.03, "45")})
    chain = decide("the system prompt", file[0]["content"], backend, CFG, 76.80, True, programs)
    reading = read_reply(chain.reply.tool_calls, False, 6)
    assert reading["actions"] == [("accept_bid", {"bid_id": "bid-1"})]
    line = "- accept_bid: ERROR: 'accept_bid' is not available to a manager: it is a buyer's action"
    messages = correction_messages(chain, reading, correction_body(correction_note(1, False), line, [], []))
    assert messages[0] == SYSTEM
    assert messages[1:] == file
    assert as_file(messages[1:]) == text


# --- section 2: strip-think and the assistant message ----------------------------------------------------------


@pytest.mark.parametrize("text, kept", [
    ("<think>hidden</think>Visible text", "Visible text"),
    ("a<think>one</think>b<think>two\nlines\n</think>c", "abc"),
    ("<think>a<think>b</think>c</think>d", "d"),
    ("<think>x</think>keep<think>y</think> this </think>", ""),
    ("lost</think>also lost</think>  kept  ", "kept"),
    ("kept\n<think>never closed", "kept"),
    ("a</think>b<think>c", "b"),
    ("a<think>b</think>c</think>d<think>e", "d"),
    ("<think>all</think>", ""),
    ("<think>open only", ""),
    (" \n\tplain" + chr(0xA0) + chr(0x2003), "plain"),
    ("<THINK>not a block</THINK>", "<THINK>not a block</THINK>"),
    ("", ""),
])
def test_strip_think(text, kept):
    assert strip_think(text) == kept


def test_assistant_message():
    calls = [{"id": "x", "type": "function", "function": {"name": "compute", "arguments": "{}"}, "index": 0}]
    plain = assistant(Reply(text="<think>h</think> said ", tool_calls=calls))
    assert plain == {"role": "assistant", "content": "said", "tool_calls": calls}
    assert list(plain) == ["role", "content", "tool_calls"]
    assert plain["tool_calls"] is calls
    reasoned = assistant(Reply(text="", tool_calls=calls, reasoning="because"))
    assert reasoned == {"role": "assistant", "content": "", "tool_calls": calls, "reasoning": "because"}
    assert list(reasoned) == ["role", "content", "tool_calls", "reasoning"]


# --- section 4: the sequences of the table in 4.2 --------------------------------------------------------------


def test_sequence_act():
    ending = reply(act())
    chain, backend, programs = run(ending)
    assert choices(backend) == ["required"]
    assert backend.requests[0]["messages"] == [SYSTEM, VIEW]
    assert chain.reply is ending and not chain.empty and chain.error is None
    assert chain.messages == [SYSTEM, VIEW]
    assert programs.ran == []


def test_sequence_compute_ran_then_act():
    first, ending = reply(compute("p")), reply(act())
    chain, backend, programs = run(first, ending)
    assert choices(backend) == ["required", ACT_FORCED]
    assert backend.requests[1]["messages"] == [
        SYSTEM, VIEW, {"role": "assistant", "content": "", "tool_calls": first.tool_calls},
        {"role": "tool", "tool_call_id": "c1", "content": last("<p>")},
    ]
    assert chain.messages == backend.requests[1]["messages"]
    assert chain.reply is ending
    assert programs.ran == ["p"]


@pytest.mark.parametrize("second", [("ok", 0, 0.01, "fine"), FAILED, ("timeout", -9, 10.1, "(no output)\nnote")])
def test_sequence_failed_program_free_retry_then_any_status(second):
    programs = Programs(bad=FAILED, again=second)
    chain, backend, programs = run(reply(compute("bad")), reply(compute("again")), reply(act()), programs=programs)
    assert choices(backend) == ["required", "required", ACT_FORCED]
    assert answers(chain.messages) == [retry(FAILED[3]), last(second[3])]
    assert programs.ran == ["bad", "again"]


def test_sequence_failed_and_compute_in_one_reply():
    programs = Programs(bad=FAILED)
    first = reply(compute("bad", "c1"), compute("good", "c2"))
    chain, backend, programs = run(first, reply(act()), programs=programs)
    assert choices(backend) == ["required", ACT_FORCED]
    assert chain.messages[3:] == [
        {"role": "tool", "tool_call_id": "c1", "content": retry(FAILED[3])},
        {"role": "tool", "tool_call_id": "c2", "content": last("<good>")},
    ]
    assert programs.ran == ["bad", "good"]


def test_sequence_malformed_twice_then_act():
    chain, backend, programs = run(reply(call("compute", "{}")), reply(call("compute", "{}")), reply(act()))
    assert choices(backend) == ["required", "required", ACT_FORCED]
    assert answers(chain.messages) == [NOT_COMPUTE, NOT_COMPUTE]
    assert programs.ran == []


def test_sequence_compute_despite_forced_act():
    third = reply(compute("three"))
    chain, backend, programs = run(reply(compute("one")), reply(compute("two")), third)
    assert choices(backend) == ["required", ACT_FORCED, ACT_FORCED]
    assert answers(backend.requests[2]["messages"]) == [last("<one>"), NO_PROGRAMS_LEFT]
    assert programs.ran == ["one"]
    assert chain.reply is third and not chain.empty
    assert chain.messages == backend.requests[2]["messages"]
    assert read_reply(chain.reply.tool_calls, False, 6)["error"] == NO_ACT
    assert [len(step["programs"]) for step in chain.steps] == [1, 0, 0]


def test_step_bound_after_the_free_retry():
    programs = Programs(a=FAILED, b=FAILED)
    fourth = reply(compute("d"))
    chain, backend, programs = run(
        reply(compute("a")), reply(compute("b")), reply(compute("c")), fourth, programs=programs)
    assert choices(backend) == ["required", "required", ACT_FORCED, ACT_FORCED]
    assert answers(chain.messages) == [retry(FAILED[3]), last(FAILED[3]), NO_PROGRAMS_LEFT]
    assert programs.ran == ["a", "b"]
    assert chain.reply is fourth


def test_malformed_then_failed_program_keeps_the_retry():
    programs = Programs(bad=FAILED)
    chain, backend, programs = run(
        reply(call("compute", "{}")), reply(compute("bad")), reply(compute("good")), reply(act()),
        programs=programs)
    assert choices(backend) == ["required", "required", "required", ACT_FORCED]
    assert answers(chain.messages) == [NOT_COMPUTE, retry(FAILED[3]), last("<good>")]


def test_reply_with_no_function_call_ends_the_chain():
    ending = reply(text="just text")
    chain, backend, programs = run(ending)
    assert len(backend.requests) == 1
    assert chain.reply is ending and not chain.empty
    assert read_reply(chain.reply.tool_calls, False, 6)["error"] == NOT_A_CALL


def test_compute_beside_act_never_runs():
    ending = reply(compute("p"), act())
    chain, backend, programs = run(ending)
    assert len(backend.requests) == 1 and programs.ran == []
    assert chain.reply is ending and chain.messages == [SYSTEM, VIEW]
    assert read_reply(chain.reply.tool_calls, False, 6)["notes"] == [COMPUTE_BESIDE_ACT]


def test_every_call_offers_compute_and_act():
    chain, backend, programs = run(reply(compute("one")), reply(compute("two")), reply(compute("three")))
    assert [request["functions"] for request in backend.requests] == [functions(CFG, "buyer")] * 3


# --- section 5: the answer to each call --------------------------------------------------------------------------


def test_answer_texts():
    assert NOT_COMPUTE == 'not run: compute takes one argument, "code", a string; send your actions with act'
    assert NO_PROGRAMS_LEFT == ("not run: no programs left this turn, and compute cannot be called again this "
                                "turn; send your actions with act")
    assert LAST_PROGRAM == ("(That was your last program this turn: compute cannot be called again this turn. "
                            "Send your actions with act.)")
    assert FREE_RETRY == "(The program failed, so it does not count: 1 more program allowed this turn.)"


def test_example_of_5_3():
    chain, backend, programs = run(reply(compute("p")), reply(act()), programs=Programs(p=("ok", 0, 0.01, "45")))
    assert chain.messages[3]["content"] == (
        "45\n\n(That was your last program this turn: compute cannot be called again this turn. Send your "
        "actions with act.)")


def test_example_of_5_4():
    output = ('Traceback (most recent call last):\n  File "<program>", line 1, in <module>\n'
              "ZeroDivisionError: division by zero\n(exit code 1)")
    programs = Programs(**{"1/0": ("error", 1, 0.02, output)})
    chain, backend, programs = run(reply(compute("1/0")), reply(compute("2")), reply(act()), programs=programs)
    assert chain.messages[3]["content"] == (
        "Traceback (most recent call last):\n"
        '  File "<program>", line 1, in <module>\n'
        "ZeroDivisionError: division by zero\n"
        "(exit code 1)\n"
        "\n"
        "(The program failed, so it does not count: 1 more program allowed this turn.)")
    assert choices(backend) == ["required", "required", ACT_FORCED]
    assert chain.messages[5]["content"] == last("<2>")


@pytest.mark.parametrize("malformed", [
    call("Compute", '{"code": "print(1)"}'),
    call(" compute", '{"code": "print(1)"}'),
    call("bid", '{"code": "print(1)"}'),
    call(None, '{"code": "print(1)"}'),
    {"id": "c1", "type": "function"},
    {"id": "c1", "type": "function", "function": None},
    {"id": "c1", "type": "function", "function": {}},
    {"id": "c1", "type": "function", "function": {"name": "compute"}},
    call("compute", None),
    call("compute", ""),
    call("compute", "   "),
    call("compute", "{}"),
    call("compute", '{"code": 5}'),
    call("compute", '{"code": null}'),
    call("compute", '{"code": ["print(1)"]}'),
    call("compute", '{"Code": "print(1)"}'),
    call("compute", '["print(1)"]'),
    call("compute", '"print(1)"'),
    call("compute", "print(1)"),
    call("compute", '{"code": "print(1)"'),
    call("compute", '{"code": "print(1)"} extra'),
    call("compute", '{"code": "print(1)",}'),
    call("compute", "{'code': 'print(1)'}"),
    call("compute", '{"code": "a\nb"}'),
    call("compute", ["print(1)"]),
    call("compute", 7),
    call("compute", True),
    call("compute", {"code": 5}),
    call("compute", {"program": "print(1)"}),
])
def test_malformed_compute_call_runs_nothing(malformed):
    chain, backend, programs = run(reply(malformed), reply(act()))
    assert answers(chain.messages) == [NOT_COMPUTE]
    assert programs.ran == [] and chain.steps[0]["programs"] == []
    assert choices(backend) == ["required", "required"]


@pytest.mark.parametrize("arguments, code", [
    ('{"code": "print(1)"}', "print(1)"),
    (' \n\t{"code": "print(1)"}\r\n ', "print(1)"),
    ({"code": "print(1)"}, "print(1)"),
    ('{"code": "print(1)", "note": 3}', "print(1)"),
    ('{"code": "a", "code": "b"}', "b"),
    ('{"code": "a\\nb"}', "a\nb"),
    ('{"code": "\\ud800"}', "\ud800"),
])
def test_compute_call_that_runs(arguments, code):
    chain, backend, programs = run(reply(call("compute", arguments)), reply(act()))
    assert programs.ran == [code]
    assert answers(chain.messages) == [last(f"<{code}>")]


@pytest.mark.parametrize("code", ["", "   ", " \n\t"])
def test_blank_code_runs_is_refused_and_counts_as_failed(code):
    refused = ("refused", None, 0.0, "(program not run: no program given)")
    programs = Programs(**{code: refused})
    chain, backend, programs = run(reply(compute(code)), reply(compute("p")), reply(act()), programs=programs)
    assert programs.ran == [code, "p"]
    assert answers(chain.messages) == [retry("(program not run: no program given)"), last("<p>")]
    assert choices(backend) == ["required", "required", ACT_FORCED]
    assert chain.steps[0]["programs"] == [
        {"code": code, "status": "refused", "exit_code": None, "wall": 0.0,
         "output": "(program not run: no program given)"}]


def test_refused_program_after_the_retry_gets_the_last_program_tail():
    too_long = "(program not run: program too long (20001 characters; the limit is 20000))"
    programs = Programs(bad=FAILED, long=("refused", None, 0.0, too_long))
    chain, backend, programs = run(reply(compute("bad")), reply(compute("long")), reply(act()), programs=programs)
    assert answers(chain.messages) == [retry(FAILED[3]), last(too_long)]
    assert choices(backend) == ["required", "required", ACT_FORCED]


@pytest.mark.parametrize(
    "status", ["error", "timeout", "cpu_limit", "memory_limit", "output_limit", "memory_unknown", "killed",
               "refused"])
def test_every_status_but_ok_is_a_failed_program(status):
    programs = Programs(p=(status, None, 0.5, "out"))
    chain, backend, programs = run(reply(compute("p")), reply(act()), programs=programs)
    assert answers(chain.messages) == [retry("out")]
    assert choices(backend) == ["required", "required"]


def test_several_calls_in_one_reply_after_a_program_that_ran():
    first = reply(compute("one", "a"), compute("two", "b"), call("bid", "{}", "c"), compute("three", "d"))
    chain, backend, programs = run(first, reply(act()))
    assert chain.messages[2] == {"role": "assistant", "content": "", "tool_calls": first.tool_calls}
    assert chain.messages[3:] == [
        {"role": "tool", "tool_call_id": "a", "content": last("<one>")},
        {"role": "tool", "tool_call_id": "b", "content": NO_PROGRAMS_LEFT},
        {"role": "tool", "tool_call_id": "c", "content": NOT_COMPUTE},
        {"role": "tool", "tool_call_id": "d", "content": NO_PROGRAMS_LEFT},
    ]
    assert programs.ran == ["one"]


def test_several_calls_in_one_reply_two_failures():
    programs = Programs(a=FAILED, b=FAILED)
    first = reply(call("compute", "{}", "m"), compute("a", "a"), compute("b", "b"), compute("c", "c"))
    chain, backend, programs = run(first, reply(act()), programs=programs)
    assert answers(chain.messages) == [NOT_COMPUTE, retry(FAILED[3]), last(FAILED[3]), NO_PROGRAMS_LEFT]
    assert programs.ran == ["a", "b"]
    assert choices(backend) == ["required", ACT_FORCED]


def test_step_after_the_program_ran_answers_each_call():
    chain, backend, programs = run(reply(compute("one")), reply(call("compute", "{}"), compute("two")), reply(act()))
    assert answers(chain.messages) == [last("<one>"), NOT_COMPUTE, NO_PROGRAMS_LEFT]


def test_free_retry_is_granted_once_a_turn():
    programs = Programs(a=FAILED, b=FAILED)
    chain, backend, programs = run(reply(compute("a")), reply(compute("b")), reply(act()), programs=programs)
    assert answers(chain.messages) == [retry(FAILED[3]), last(FAILED[3])]
    assert choices(backend) == ["required", "required", ACT_FORCED]


def test_compute_step_keeps_text_calls_and_reasoning():
    first = Reply(text="<think>plan</think>I compute.", tool_calls=[compute("p")], reasoning="hidden")
    chain, backend, programs = run(first, reply(act()))
    assert chain.messages[2] == {
        "role": "assistant", "content": "I compute.", "tool_calls": first.tool_calls, "reasoning": "hidden"}
    assert chain.messages[2]["tool_calls"] is first.tool_calls


# --- section 2: the tool message's id ---------------------------------------------------------------------------


def test_tool_messages_answer_ids_as_returned():
    calls = [
        {"type": "function", "function": {"name": "compute", "arguments": "{}"}},
        call("compute", "{}", "same"),
        call("compute", "{}", "same"),
        call("compute", "{}", 7),
        call("compute", "{}", None),
        call("compute", "{}", {"k": [1]}),
        call("compute", "{}", ""),
        call("compute", "{}", False),
    ]
    chain, backend, programs = run(reply(*calls), reply(act()))
    tools = chain.messages[3:]
    assert all(list(message) == ["role", "tool_call_id", "content"] for message in tools)
    ids = [message["tool_call_id"] for message in tools]
    assert ids == [None, "same", "same", 7, None, {"k": [1]}, "", False]
    assert type(ids[3]) is int and ids[7] is False
    assert json.loads(json.dumps(tools[0]))["tool_call_id"] is None


def test_shared_ids_each_get_their_own_answer():
    programs = Programs(bad=FAILED)
    chain, backend, programs = run(
        reply(compute("bad", "same"), compute("good", "same"), compute("more", "same")), reply(act()),
        programs=programs)
    assert chain.messages[3:] == [
        {"role": "tool", "tool_call_id": "same", "content": retry(FAILED[3])},
        {"role": "tool", "tool_call_id": "same", "content": last("<good>")},
        {"role": "tool", "tool_call_id": "same", "content": NO_PROGRAMS_LEFT},
    ]


# --- section 13: charges and out of business inside a chain ------------------------------------------------------


def test_each_reply_is_charged():
    cfg = CFG | {"credits_per_usd": 3.0}
    chain, backend, programs = run(
        reply(compute("p"), cost=0.0000004), reply(act(), cost=1.23456789), cfg=cfg)
    assert [step["charge"] for step in chain.steps] == [round(0.0000004 * 3.0, 6), round(1.23456789 * 3.0, 6)]
    assert [step["charge"] for step in chain.steps] == [0.000001, 3.703704]


def test_manager_out_of_business_ends_the_chain_as_an_empty_act():
    ending = reply(compute("p"), cost=1.0)
    chain, backend, programs = run(ending, reply(act()), balance=1.0, manager=True)
    assert chain.empty and chain.reply is ending and chain.error is None
    assert len(backend.requests) == 1 and programs.ran == []
    assert chain.steps[0]["programs"] == [] and chain.steps[0]["charge"] == 1.0
    assert chain.messages == [SYSTEM, VIEW]


def test_buyer_is_never_out_of_business():
    chain, backend, programs = run(reply(compute("p"), cost=5.0), reply(act(), cost=5.0), balance=1.0)
    assert not chain.empty and len(backend.requests) == 2 and programs.ran == ["p"]


def test_manager_above_zero_goes_on():
    chain, backend, programs = run(
        reply(compute("p"), cost=0.999999), reply(act()), balance=1.0, manager=True)
    assert not chain.empty and len(backend.requests) == 2 and programs.ran == ["p"]


def test_out_of_business_counts_the_chain_s_own_charges_so_far():
    second = reply(compute("p"), cost=0.6)
    chain, backend, programs = run(
        reply(call("compute", "{}"), cost=0.6), second, reply(act()), balance=1.0, manager=True)
    assert chain.empty and chain.reply is second
    assert len(backend.requests) == 2 and programs.ran == []
    assert answers(chain.messages) == [NOT_COMPUTE]


def test_out_of_business_at_a_reply_with_other_functions_only():
    ending = reply(call("bid", '{"post_id": "post-1"}'), cost=2.0)
    chain, backend, programs = run(ending, balance=1.0, manager=True)
    assert chain.empty and chain.reply is ending


def test_out_of_business_at_the_step_bound_is_an_empty_act_not_a_format_error():
    third = reply(compute("three"), cost=1.0)
    chain, backend, programs = run(
        reply(compute("one")), reply(compute("two")), third, balance=1.0, manager=True)
    assert chain.empty and chain.reply is third


@pytest.mark.parametrize("ending", [reply(act(), cost=9.0), reply(text="no call", cost=9.0),
                                    reply(compute("p"), act(), cost=9.0)])
def test_out_of_business_reply_with_act_or_no_call_is_not_an_empty_act(ending):
    chain, backend, programs = run(ending, balance=1.0, manager=True)
    assert not chain.empty and chain.reply is ending and len(backend.requests) == 1


@pytest.mark.parametrize("cost", [0.0, -1.0, 0.0000004])
def test_charge_of_zero_or_less_does_nothing(cost):
    chain, backend, programs = run(reply(compute("p"), cost=cost), reply(act()), balance=0.0, manager=True)
    assert chain.steps[0]["charge"] <= 0
    assert not chain.empty and len(backend.requests) == 2 and programs.ran == ["p"]


def test_negative_charge_is_not_added_to_the_balance():
    second = reply(compute("p"), cost=1.0)
    chain, backend, programs = run(
        reply(call("compute", "{}"), cost=-5.0), second, reply(act()), balance=1.0, manager=True)
    assert chain.steps[0]["charge"] == -5.0
    assert chain.empty and chain.reply is second


def test_manager_at_zero_goes_out_at_its_first_charge_above_zero():
    chain, backend, programs = run(reply(compute("p"), cost=0.000001), balance=0.0, manager=True)
    assert chain.empty


# --- failed calls, and what the chain records -------------------------------------------------------------------


def test_failed_first_call_loses_the_turn_to_the_provider():
    chain, backend, programs = run(CallFailed("HTTP 402 for model 'm': no credit", failed_cost=0.25))
    assert chain.error == "HTTP 402 for model 'm': no credit" and chain.failed_cost == 0.25
    assert chain.reply is None and not chain.empty and chain.steps == []
    assert chain.request == {"messages": [SYSTEM, VIEW], "functions": functions(CFG, "buyer"),
                             "tool_choice": "required"}


def test_failed_later_call_keeps_the_earlier_replies():
    first = reply(compute("p"), cost=0.5)
    chain, backend, programs = run(first, CallFailed("giving up on 'm' after 6 attempts: HTTP 500"))
    assert chain.error == "giving up on 'm' after 6 attempts: HTTP 500" and chain.failed_cost == 0.0
    assert chain.reply is None
    assert [(step["reply"], step["charge"]) for step in chain.steps] == [(first, 0.5)]
    assert [program["code"] for program in chain.steps[0]["programs"]] == ["p"]
    assert chain.request["tool_choice"] == ACT_FORCED
    assert chain.request["messages"] == chain.messages == backend.requests[1]["messages"]


def test_cost_cap_passes_through():
    with pytest.raises(CostCapReached):
        run(CostCapReached("cost cap $20.00 reached (spent $20.12)"))


def test_program_that_cannot_be_run_isolated_passes_through():
    def broken(code, compute):
        raise RuntimeError("no sandbox")

    with pytest.raises(RuntimeError):
        run(reply(compute("p")), programs=broken)


def test_chain_records_every_step():
    programs = Programs(bad=("error", 3, 0.25, "(no output)\n(exit code 3)"))
    first, second, third = reply(compute("bad"), cost=0.1), reply(compute("good"), cost=0.2), reply(act(), cost=0.3)
    chain, backend, programs = run(first, second, third, programs=programs)
    assert [step["reply"] for step in chain.steps] == [first, second, third]
    assert [step["charge"] for step in chain.steps] == [0.1, 0.2, 0.3]
    assert [step["request"] for step in chain.steps] == backend.requests
    assert [len(step["request"]["messages"]) for step in chain.steps] == [2, 4, 6]
    assert [step["programs"] for step in chain.steps] == [
        [{"code": "bad", "status": "error", "exit_code": 3, "wall": 0.25, "output": "(no output)\n(exit code 3)"}],
        [{"code": "good", "status": "ok", "exit_code": 0, "wall": 0.01, "output": "<good>"}],
        [],
    ]
    assert chain.reply is third and chain.messages == backend.requests[2]["messages"]


def test_chain_reads_nothing_but_what_it_is_given():
    chain, backend, programs = run(reply(act()))
    assert backend.requests[0]["messages"] == [
        {"role": "system", "content": "the system prompt"}, {"role": "user", "content": "the view"}]


# --- section 11.2: the correction's note --------------------------------------------------------------------------


@pytest.mark.parametrize("k, actions", [(1, "1 action"), (2, "2 actions"), (6, "6 actions"), (12, "12 actions")])
def test_note_for_an_unreadable_reply(k, actions):
    assert correction_note(k, True) == (
        "Your reply could not be read, so none of its actions ran (below). You may call act again now, with up "
        f"to {actions} and your memory. This is your only correction this turn, and compute cannot be called in "
        "it.")


@pytest.mark.parametrize("n, actions, they", [(1, "1 action", "it"), (2, "2 actions", "them"),
                                              (6, "6 actions", "them")])
def test_note_for_failed_actions(n, actions, they):
    assert correction_note(n, False) == (
        f"{n} of your actions failed (the results of your reply are below; the actions that succeeded have run "
        f"and stand). You may send up to {actions} now to replace {they}, in the same format as before, with "
        "your memory, which then replaces the one you just sent. This is your only correction this turn, and "
        "compute cannot be called in it.")


def test_notes_at_the_default_limit_in_full():
    assert correction_note(6, True) == (
        "Your reply could not be read, so none of its actions ran (below). You may call act again now, with up "
        "to 6 actions and your memory. This is your only correction this turn, and compute cannot be called in it.")
    assert correction_note(1, False) == (
        "1 of your actions failed (the results of your reply are below; the actions that succeeded have run and "
        "stand). You may send up to 1 action now to replace it, in the same format as before, with your memory, "
        "which then replaces the one you just sent. This is your only correction this turn, and compute cannot be "
        "called in it.")


# --- section 11.3: the correction's body --------------------------------------------------------------------------


def test_body_without_news_or_facts():
    assert correction_body("NOTE", "- a: ERROR: x\n\n- b: OK: y", [], []) == (
        "NOTE\n\nResults of your reply:\n- a: ERROR: x\n\n- b: OK: y")
    assert correction_body("NOTE", "- a: ERROR: x", None, None) == "NOTE\n\nResults of your reply:\n- a: ERROR: x"


def test_body_of_an_unreadable_reply():
    line = format_line(NOT_A_CALL + CUT)
    assert correction_body(correction_note(6, True), line, [], []) == (
        "Your reply could not be read, so none of its actions ran (below). You may call act again now, with up "
        "to 6 actions and your memory. This is your only correction this turn, and compute cannot be called in it."
        "\n\nResults of your reply:\n"
        "- FORMAT ERROR: your reply was not a function call: every reply must call compute or act - your reply "
        "hit the length limit before its act call was complete; reason more briefly: none of your actions ran")


def test_body_real_example_with_news():
    spaced = (
        "- bid: ERROR: post-8 closed earlier this round, when its buyer accepted a bid (this round B-d75 accepted "
        "another bid on post-8 before your bid ran)\n"
        "\n"
        "- bid: OK: placed bid-21 on post-7 at 24 with threshold 6 (escrow)\n"
        "\n"
        "- bid: OK: placed bid-22 on post-6 at 24 with threshold 6 (escrow)")
    news = ["[day 6, round 3] B-c53 approved R-815 on contract-1: you received 25.00 of the price 25; contract-1 "
            "is closed."]
    assert correction_body(correction_note(1, False), spaced, news, []) == (
        "1 of your actions failed (the results of your reply are below; the actions that succeeded have run and "
        "stand). You may send up to 1 action now to replace it, in the same format as before, with your memory, "
        "which then replaces the one you just sent. This is your only correction this turn, and compute cannot be "
        "called in it.\n"
        "\n"
        "Results of your reply:\n"
        "- bid: ERROR: post-8 closed earlier this round, when its buyer accepted a bid (this round B-d75 accepted "
        "another bid on post-8 before your bid ran)\n"
        "\n"
        "- bid: OK: placed bid-21 on post-7 at 24 with threshold 6 (escrow)\n"
        "\n"
        "- bid: OK: placed bid-22 on post-6 at 24 with threshold 6 (escrow)\n"
        "\n"
        "What reached you from this round so far (your next view shows it too):\n"
        "[day 6, round 3] B-c53 approved R-815 on contract-1: you received 25.00 of the price 25; contract-1 is "
        "closed.")


def test_body_with_news_and_facts():
    news = ["[day 17, round 1] first", "[day 17, round 1] second"]
    facts = ["Open bids on post-3 now: none.", "Your balance now: 86.08 credits (...). You (run_myself): free."]
    assert correction_body("NOTE", "- x: ERROR: y", news, facts) == (
        "NOTE\n\nResults of your reply:\n- x: ERROR: y\n\n"
        "What reached you from this round so far (your next view shows it too):\n"
        "[day 17, round 1] first\n[day 17, round 1] second\n\n"
        "Open bids on post-3 now: none.\nYour balance now: 86.08 credits (...). You (run_myself): free.")


def test_body_with_facts_only():
    assert correction_body("NOTE", "- x: ERROR: y", [], ["Open bids on post-3 now: none."]) == (
        "NOTE\n\nResults of your reply:\n- x: ERROR: y\n\nOpen bids on post-3 now: none.")


# --- section 11.4: how the body joins the conversation -------------------------------------------------------------


def ended(ending, *before):
    """A chain that ended with the reply `ending` after the messages `before`."""
    return Chain(messages=[SYSTEM, VIEW, *before], steps=[], reply=ending)


def joined(ending, *before, limit=6):
    chain = ended(ending, *before)
    reading = read_reply(ending.tool_calls, ending.finish_reason == "length", limit)
    return correction_messages(chain, reading, "BODY"), reading


def test_tool_path_answers_every_call():
    calls = [
        act('{"actions": [{"action": "bid"}], "memory": "first"}', "a1"),
        compute("p", "c1"),
        act('{"actions": [{"action": "pay"}]}', "a2"),
        call("Compute", '{"code": "p"}', "c2"),
        call(" compute", '{"code": "p"}', "c3"),
        call("bid", " {} ", "b1"),
    ]
    ending = Reply(text="<think>t</think>Done.", tool_calls=calls, reasoning="why", finish_reason="tool_calls")
    before = [{"role": "assistant", "content": "", "tool_calls": []},
              {"role": "tool", "tool_call_id": 1, "content": "x"}]
    messages, reading = joined(ending, *before)
    assert reading["error"] is None
    assert messages == [
        SYSTEM, VIEW, *before,
        {"role": "assistant", "content": "Done.", "tool_calls": calls, "reasoning": "why"},
        {"role": "tool", "tool_call_id": "a1", "content": IN_LAST_ACT},
        {"role": "tool", "tool_call_id": "c1", "content": ACT_ENDED},
        {"role": "tool", "tool_call_id": "a2", "content": "BODY"},
        {"role": "tool", "tool_call_id": "c2", "content": IN_LAST_ACT},
        {"role": "tool", "tool_call_id": "c3", "content": IN_LAST_ACT},
        {"role": "tool", "tool_call_id": "b1", "content": IN_LAST_ACT},
    ]
    assert messages[4]["tool_calls"] is calls
    assert ACT_ENDED == "not run: act ended the turn"
    assert IN_LAST_ACT == "(the results of this reply are in the answer to its last act call)"


def test_tool_path_ids_as_returned():
    calls = [
        {"type": "function", "function": {"name": "act", "arguments": "{}"}},
        call("act", "{}", 5),
        call("compute", "{}", 5),
        call("act", "{}", None),
    ]
    messages, reading = joined(Reply(tool_calls=calls))
    assert messages[3:] == [
        {"role": "tool", "tool_call_id": None, "content": IN_LAST_ACT},
        {"role": "tool", "tool_call_id": 5, "content": IN_LAST_ACT},
        {"role": "tool", "tool_call_id": 5, "content": ACT_ENDED},
        {"role": "tool", "tool_call_id": None, "content": "BODY"},
    ]


def test_tool_path_leaves_the_chain_s_conversation_alone():
    chain = ended(reply(act()))
    correction_messages(chain, read_reply(chain.reply.tool_calls, False, 6), "BODY")
    assert chain.messages == [SYSTEM, VIEW]


def text_path(content, **extra):
    return [SYSTEM, VIEW, {"role": "assistant", "content": content, **extra}, {"role": "user", "content": "BODY"}]


def test_text_path_for_an_unreadable_reply_with_text():
    messages, reading = joined(Reply(text="<think>hm</think> I would bid. ", reasoning="r"))
    assert reading["error"] == NOT_A_CALL
    assert messages == text_path("I would bid.", reasoning="r")
    assert list(messages[2]) == ["role", "content", "reasoning"]


def test_text_path_has_no_tool_calls_and_no_reasoning_key_without_reasoning():
    messages, reading = joined(Reply(text="words", tool_calls=[call("bid", '{"post_id": "post-1"}')]))
    assert reading["error"] == NO_ACT
    assert messages == text_path("words")
    assert list(messages[2]) == ["role", "content"]


def test_text_path_shows_the_arguments_when_the_text_is_empty():
    calls = [call("bid", '{"post_id": "post-1"}'), act('{"actions": "bid"}'), call("x", "  raw text  ")]
    messages, reading = joined(Reply(text="<think>only hidden</think>", tool_calls=calls))
    assert reading["error"] == UNREADABLE
    assert messages == text_path('{"post_id": "post-1"}\n{"actions": "bid"}\n  raw text  ')


def test_text_path_for_a_cut_reply_that_reads():
    ending = Reply(tool_calls=[act('{"actions": [], "memory": "m"}')], finish_reason="length")
    messages, reading = joined(ending)
    assert reading["error"] is None
    assert messages == text_path('{"actions": [], "memory": "m"}')


@pytest.mark.parametrize("arguments, shown", [
    ('{"actions": []', '{"actions": []'),
    (' {"actions": [{"action": "pay"}  ', ' {"actions": [{"action": "pay"}  '),
    ({"actions": [], "memory": "it's"}, "{'actions': [], 'memory': \"it's\"}"),
    ({"actions": [{"action": "pay", "amount": 2.0, "ok": True, "to": None}]},
     "{'actions': [{'action': 'pay', 'amount': 2.0, 'ok': True, 'to': None}]}"),
])
def test_text_path_for_readable_arguments_that_are_not_a_clean_object_string(arguments, shown):
    messages, reading = joined(Reply(tool_calls=[act(arguments)]))
    assert reading["error"] is None
    assert messages == text_path(shown)


def test_whitespace_around_a_clean_object_string_takes_the_tool_path():
    calls = [act('  {"actions": []}\n')]
    messages, reading = joined(Reply(tool_calls=calls))
    assert messages == [
        SYSTEM, VIEW, {"role": "assistant", "content": "", "tool_calls": calls},
        {"role": "tool", "tool_call_id": "a1", "content": "BODY"},
    ]


def test_any_call_with_empty_arguments_takes_the_text_path():
    calls = [act('{"actions": [], "memory": "m"}'), call("compute", "")]
    messages, reading = joined(Reply(tool_calls=calls))
    assert reading["error"] is None
    assert messages == text_path('{"actions": [], "memory": "m"}')


def test_a_call_that_is_not_an_object_string_beside_a_readable_act_takes_the_text_path():
    for other in (call("compute", "[1]"), call("bid", "nonsense"), {"id": "n", "function": {"name": "bid"}}):
        messages, reading = joined(Reply(text="said", tool_calls=[act(), other]))
        assert reading["error"] is None
        assert messages == text_path("said")


@pytest.mark.parametrize("empty", [None, False, 0, "", [], {}, "   ", " \n\t" + chr(0xA0)])
def test_text_path_leaves_out_empty_arguments(empty):
    calls = [call("bid", empty), {"id": "n", "function": {"name": "bid"}}, call("bid", "kept")]
    messages, reading = joined(Reply(tool_calls=calls))
    assert messages == text_path("kept")
    messages, reading = joined(Reply(tool_calls=calls[:2]))
    assert messages == text_path("(an empty reply)")


def test_text_path_of_an_empty_reply():
    assert EMPTY_REPLY == "(an empty reply)"
    for ending in (Reply(), Reply(text="  "), Reply(text="<think>x</think>"), Reply(tool_calls=[act("")]),
                   Reply(tool_calls=[{"id": "a", "function": {"name": "act"}}], finish_reason="length")):
        messages, reading = joined(ending)
        assert messages == text_path("(an empty reply)")


@pytest.mark.parametrize("value, shown", [(True, "True"), (7, "7"), (2.5, "2.5"), ([1, "a", None], "[1, 'a', None]")])
def test_text_path_shows_decoded_values_as_text(value, shown):
    messages, reading = joined(Reply(tool_calls=[call("act", value)]))
    assert reading["error"] == UNREADABLE
    assert messages == text_path(shown)


def test_text_path_cuts_each_call_s_arguments_at_2000_characters():
    long_act = '{"actions": "' + "a" * 2500 + '"}'
    long_dict = {"k": "b" * 2500}
    exact = "c" * 2000
    calls = [act(long_act), call("bid", long_dict), call("bid", exact), call("bid", "d" * 2001)]
    messages, reading = joined(Reply(tool_calls=calls))
    parts = messages[2]["content"].split("\n")
    assert parts == [long_act[:2000], ("{'k': '" + "b" * 2500)[:2000], exact, "d" * 2000]
    assert [len(part) for part in parts] == [2000] * 4


def test_text_path_does_not_cut_the_stripped_text():
    messages, reading = joined(Reply(text="t" * 2500))
    assert messages == text_path("t" * 2500)


# --- sections 4.3 and 11.5: the correction call -------------------------------------------------------------------


def test_correction_call_offers_act_only_forced():
    answer = reply(act(), cost=0.25)
    backend = Scripted(answer)
    messages = [SYSTEM, VIEW, {"role": "assistant", "content": "x"}, {"role": "user", "content": "BODY"}]
    chain = correct(messages, backend, CFG | {"credits_per_usd": 2.0}, "buyer")
    assert backend.requests == [{"messages": messages, "functions": functions(CFG, "buyer")[1:],
                                 "tool_choice": ACT_FORCED}]
    assert ACT_FORCED == {"type": "function", "function": {"name": "act"}}
    assert [f["function"]["name"] for f in backend.requests[0]["functions"]] == ["act"]
    assert chain.reply is answer and chain.error is None and not chain.empty
    assert chain.steps == [{"request": backend.requests[0], "reply": answer, "charge": 0.5, "programs": []}]
    assert chain.messages == messages


def test_correction_call_is_never_followed_by_another_call():
    backend = Scripted(reply(compute("p")), reply(act()))
    chain = correct([SYSTEM, VIEW], backend, CFG, "buyer")
    assert len(backend.requests) == 1 and len(backend.replies) == 1
    assert chain.reply.tool_calls[0]["function"]["name"] == "compute"
    assert chain.steps[0]["programs"] == []


def test_correction_call_that_fails():
    backend = Scripted(CallFailed("HTTP 500 for model 'm': down", failed_cost=0.5))
    chain = correct([SYSTEM, VIEW], backend, CFG, "buyer")
    assert chain.error == "HTTP 500 for model 'm': down" and chain.failed_cost == 0.5
    assert chain.reply is None and chain.steps == []
    assert chain.request == {"messages": [SYSTEM, VIEW], "functions": functions(CFG, "buyer")[1:],
                             "tool_choice": ACT_FORCED}


def test_correction_call_lets_the_cost_cap_through():
    with pytest.raises(CostCapReached):
        correct([SYSTEM, VIEW], Scripted(CostCapReached("cost cap $1.00 reached (spent $1.00)")), CFG, "buyer")


# --- sections 11.6 and 12: the outcome lines and the results text -------------------------------------------------

END = (" That turn was lost: none of its actions ran. End every turn by calling the function act with a list of "
       "your actions (an empty list does nothing) and your memory.")


def test_format_error_line():
    assert format_line(NO_ACT) == (
        "- FORMAT ERROR: your reply did not call act: your turn ends with act, with your actions and your memory: "
        "none of your actions ran")
    assert format_line(NOT_A_CALL + CUT) == (
        "- FORMAT ERROR: your reply was not a function call: every reply must call compute or act - your reply "
        "hit the length limit before its act call was complete; reason more briefly: none of your actions ran")


def test_correction_could_not_be_sent_note():
    assert correction_failed == "(note: your correction could not be sent; the results above stand)"


def test_correction_format_error_line():
    assert correction_format_line(UNREADABLE) == (
        'Correction: FORMAT ERROR: your act call\'s "actions" could not be read: it must be a list of action '
        'objects, each naming its action with "action" and giving its arguments beside it: none of its actions ran')
    assert correction_format_line("E") == "Correction: FORMAT ERROR: E: none of its actions ran"


def test_lost_turn_texts():
    assert lost_text(NOT_A_CALL, NO_ACT) == (
        "FORMAT ERROR: your reply was not a function call: every reply must call compute or act; and your "
        "correction: your reply did not call act: your turn ends with act, with your actions and your memory."
        + END)
    assert lost_text(NO_ACT + CUT) == (
        "FORMAT ERROR: your reply did not call act: your turn ends with act, with your actions and your memory - "
        "your reply hit the length limit before its act call was complete; reason more briefly." + END)
    assert lost_text("E", "E2") == (
        "FORMAT ERROR: E; and your correction: E2. That turn was lost: none of its actions ran. End every turn by "
        "calling the function act with a list of your actions (an empty list does nothing) and your memory.")
    assert lost_text("E") == (
        "FORMAT ERROR: E. That turn was lost: none of its actions ran. End every turn by calling the function act "
        "with a list of your actions (an empty list does nothing) and your memory.")


def test_results_normally():
    lines = ["(note: only your first 6 actions were run)", "- bid: OK: placed", "- pay: ERROR: no"]
    assert results(lines, None, None) == ["Results of your actions:", *lines]
    assert results(lines, None, NO_MEMORY) == ["Results of your actions:", *lines, NO_MEMORY]


def test_results_with_no_lines():
    assert results([], None, None) == ["Results of your actions:", "(no actions run)"]
    assert "\n".join(results([], None, None)) == "Results of your actions:\n(no actions run)"


def test_results_of_an_out_of_business_empty_act():
    assert results([], None, NO_MEMORY) == [
        "Results of your actions:", "(note: your reply had no memory; your memory is unchanged)"]


def test_results_example_of_section_12():
    line = ("- deliver: ERROR: no contract with id 'contract-1'. Your contracts: none. A bid becomes a contract "
            "only when the buyer accepts it.")
    assert "\n".join(results([line], [], None)) == (
        "Results of your actions:\n"
        "- deliver: ERROR: no contract with id 'contract-1'. Your contracts: none. A bid becomes a contract only "
        "when the buyer accepts it.\n"
        "Correction:\n"
        "(no actions run)")


def test_results_with_a_correction_block():
    first = ["- bid: ERROR: no"]
    cut = "(note: your memory was cut to its first 2000 of 2500 characters)"
    assert results(first, ["- bid: OK: placed", "- pay: ERROR: no"], cut) == [
        "Results of your actions:", "- bid: ERROR: no", "Correction:", "- bid: OK: placed", "- pay: ERROR: no", cut]
    assert results(first, [COMPUTE_BESIDE_ACT], None) == [
        "Results of your actions:", "- bid: ERROR: no", "Correction:", COMPUTE_BESIDE_ACT]
    assert results(first, [], NO_MEMORY) == [
        "Results of your actions:", "- bid: ERROR: no", "Correction:", "(no actions run)", NO_MEMORY]


def test_results_with_the_lines_of_11_6():
    unreadable = [format_line(NO_ACT)]
    assert results(unreadable, ["- bid: OK: placed"], None) == [
        "Results of your actions:", format_line(NO_ACT), "Correction:", "- bid: OK: placed"]
    stood = ["- bid: ERROR: no", correction_failed]
    assert results(stood, None, None) == ["Results of your actions:", *stood]
    discarded = ["- bid: ERROR: no", correction_format_line(NOT_A_CALL)]
    assert results(discarded, None, NO_MEMORY) == ["Results of your actions:", *discarded, NO_MEMORY]


def test_results_keeps_an_entry_with_line_breaks_whole_and_its_inputs_unchanged():
    first, correction = ["- test_result: ERROR: no result 'missing\n(note: quoted text)\nend'"], []
    entries = results(first, correction, None)
    assert entries[1] == first[0] and len(entries) == 4
    assert first == ["- test_result: ERROR: no result 'missing\n(note: quoted text)\nend'"] and correction == []
