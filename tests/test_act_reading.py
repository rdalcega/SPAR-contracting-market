"""Acceptance check 6, first half: spec/vectors/act_reading.jsonl, compared as spec/vectors/README.md says.

Then tests of rules the vectors do not pin down (each names its rule).
"""

import json
import math
from pathlib import Path

import pytest

from market.actions import (
    COMPUTE_AS_ACTION, NO_NAME, OUT_OF_BUSINESS, action_list, can_send, name_refusal, result_line, shown,
)
from market.reading import (
    COMPUTE_BESIDE_ACT, CUT, NO_ACT, NO_MEMORY, NOT_A_CALL, UNREADABLE, act_arguments, call_name, nesting,
    read_items, read_json, read_reply, turn_memory,
)

VECTORS = Path(__file__).parent.parent / "spec" / "vectors" / "act_reading.jsonl"
CASES = [json.loads(line) for line in VECTORS.read_text(encoding="utf-8").splitlines()]
MAX_ACTIONS, MEMORY_CHARS = 6, 2000


def same(a, b):
    """Values by type and value: 1, 1.0 and true differ, NaN equals NaN, -0.0 differs from 0.0."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(map(same, a, b))
    if isinstance(a, float):
        return (math.isnan(a) and math.isnan(b)) or (a == b and math.copysign(1, a) == math.copysign(1, b))
    return a == b


def provider_calls(calls):
    """The vector's calls in the form a provider returns them."""
    return [{"id": f"call_{i}", "type": "function", "function": dict(call)} for i, call in enumerate(calls)]


def call(name="act", arguments=None, **extra):
    function = {"name": name} | ({} if arguments is None else {"arguments": arguments})
    return {"id": "call_0", "type": "function", "function": function, **extra}


def act(arguments=None):
    return call("act", arguments if arguments is None or isinstance(arguments, str) else json.dumps(arguments))


def test_the_file_has_every_case():
    assert [case["id"] for case in CASES] == [f"act-{n:04d}" for n in range(1, 1646)]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_vector(case):
    given, expected = case["input"], case["expected"]
    correction = given["correction"]
    limit = correction["limit"] if correction else MAX_ACTIONS
    reading = read_reply(provider_calls(given["calls"]), given["cut"], limit)

    # 1. the format error, as exact text, or that there is none
    assert reading["error"] == expected["format_error"]
    if expected["format_error"] is not None:
        assert (reading["notes"], reading["actions"], reading["dropped"], reading["memory"]) == ([], [], 0, None)
        return

    # 2. the kept actions, in order: name, shown name, arguments (without `tool`)
    assert [name for name, _ in reading["actions"]] == [action["name"] for action in expected["actions"]]
    for (name, arguments), action in zip(reading["actions"], expected["actions"]):
        assert shown(name) == action["shown"]
        if name is not None:
            assert same({k: v for k, v in arguments.items() if k != "tool"}, action["arguments"])
        # 3. the result line where the market's state cannot change it (the participant in business)
        refusal = name_refusal(name, given["role"], given["message_channels"], False)
        if refusal is None and name == "compute":
            refusal = COMPUTE_AS_ACTION
        assert (None if refusal is None else result_line(name, False, refusal)) == action["result"]

    # 4. the notes and the number of actions the cap dropped
    assert reading["notes"] == expected["notes"]
    assert reading["dropped"] == expected["capped"]

    # 5. the memory: the reply's, then the turn's end in the file's scenario
    memory = expected["memory"]
    assert reading["memory"] == memory["reply_memory"]
    decide, corrected = (None, reading["memory"]) if correction else (reading["memory"], None)
    end = turn_memory(given["memory_before"], decide, corrected, MEMORY_CHARS)
    assert (end["written"], end["text"], end["note"]) == (memory["written"], memory["after"], memory["note"])
    assert end["from_correction"] == (bool(correction) and memory["written"])


# --- rules the vectors do not pin down ---


def test_nesting_beyond_400_levels_does_not_decode():
    # turns.md 7.2: nesting deeper than 400 levels may be treated as JSON that cannot be decoded (preferred)
    assert nesting(read_json("[" * 400 + "]" * 400)) == 400
    with pytest.raises(ValueError):
        read_json("[" * 401 + "]" * 401)
    at_401, at_400 = ('{"actions": [' + "[" * n + "]" * n + "]}" for n in (399, 398))
    assert read_reply([act(at_401)], False, 6)["error"] == UNREADABLE
    assert read_reply([act(at_400)], False, 6)["error"] is None


def test_nesting_beyond_400_levels_in_decoded_arguments_is_unreadable():
    # turns.md 7.2 and 7.3 step 1: the same answer when the provider sent the arguments already decoded
    value = []
    for _ in range(399):
        value = [value]
    assert read_reply([call("act", {"actions": value})], False, 6)["error"] == UNREADABLE
    assert read_reply([call("act", {"actions": value[0]})], False, 6)["error"] is None


def test_integer_of_more_than_4300_digits_does_not_decode():
    # turns.md 7.2: such a literal may be treated as JSON that cannot be decoded (preferred)
    assert read_json("1" * 4300) == int("1" * 4300)
    with pytest.raises(ValueError):
        read_json("1" * 4301)
    text = '{"actions": [{"action": "pay", "amount": %s}]}'
    assert read_reply([act(text % ("1" * 4301))], False, 6)["error"] == UNREADABLE
    assert read_reply([act(text % ("1" * 4300))], False, 6)["actions"] == [("pay", {"amount": int("1" * 4300)})]


def test_strict_reading():
    # turns.md 7.2: one value, JSON whitespace only, last value of a repeated key in its first position
    assert list(read_json('{"a": 1, "b": 2, "a": 3}').items()) == [("a", 3), ("b", 2)]
    assert read_json(' \t\r\n{"a": [NaN, Infinity, -Infinity]} \n')["a"][1:] == [math.inf, -math.inf]
    assert read_json('"\\ud800"') == "\ud800" and read_json('"\\ud83d\\ude00"') == "\U0001f600"
    assert type(read_json("1")) is int and type(read_json("1.0")) is float and type(read_json("1e0")) is float
    assert read_json("123456789012345678901234567890") == 123456789012345678901234567890
    for text in ("", "{} {}", " {}", '{"a": "\n"}', "{'a': 1}", '{"a": 1,}', "None", "﻿{}", "{}\x0c"):
        with pytest.raises(ValueError):
            read_json(text)


def test_a_call_without_function_or_name_is_no_act_call():
    # models.md 4.7: a call may lack id, type or function, and its name may be any JSON value
    for other in ({}, {"id": 1}, {"function": {}}, {"function": None}, call(None), call(5), call(["act"])):
        assert call_name(other) != "act"
        assert read_reply([other], False, 6)["error"] == NO_ACT
        assert read_reply([other, act({"memory": "m"})], False, 6)["memory"] == "m"
    assert read_reply([{"function": {"name": "act"}}], False, 6)["error"] is None


def test_extra_fields_of_a_call_are_ignored():
    # turns.md 2: the provider's extra fields ride along; the reading uses the name and the arguments only
    reading = read_reply([call("act", '{"actions": ["x"]}', index=0, extra={"a": 1})], False, 6)
    assert reading["actions"] == [(None, {})]


def test_reading_leaves_the_calls_as_returned():
    # turns.md 2: the calls go back into the conversation exactly as the provider returned them
    inner = {"actions": [{"name": "bid", "params": {"x": 1}}]}
    decoded = {"actions": [{"action": " ", "name": "act", "args": inner}]}
    calls = [call("act", decoded), act('{"actions": [{"action": "pay", "arguments": {"to": "B-001"}}]')]
    before = json.dumps(calls)
    assert read_reply(calls, False, 6)["actions"] == [("bid", {"x": 1}), ("pay", {"to": "B-001"})]
    assert json.dumps(calls) == before


def test_cut_suffix_on_every_format_error():
    # turns.md 7.1: the suffix follows each of the three error texts
    assert read_reply([], True, 6)["error"] == NOT_A_CALL + CUT
    assert read_reply([call("compute", '{"code": "1"}')], True, 6)["error"] == NO_ACT + CUT
    assert read_reply([act('{"actions": [')], True, 6)["error"] == UNREADABLE + CUT


def test_closing_brackets_never_read_a_cut_reply_and_only_complete_an_object():
    # turns.md 7.2 and 7.3: }, ]}, }]} and ] in turn, after trimming; strict only when cut
    short = ("{", '{"actions": []', '{"actions": [{"action": "bid"}', '{"actions": [{"action": "bid"',
             '　{"actions": [] \x1c')
    for text in short:
        assert isinstance(act_arguments(act(text), False), dict)
        assert act_arguments(act(text), True) is None
    for text in ("[", '[{"actions": []}', '{"actions": [[', '{"actions": [{"a": [1', '　{"actions": []}'):
        assert act_arguments(act(text), False) is None


def test_empty_and_decoded_arguments():
    # turns.md 7.3 step 1: missing, null and "" are {}; a decoded object is used; any other decoded value fails
    assert act_arguments({"function": {"name": "act"}}, False) == {}
    assert act_arguments(call("act", ""), True) == {}
    assert act_arguments(call("act", {"memory": "m"}), False) == {"memory": "m"}
    for decoded in ([], [{}], 0, 1, 1.5, True, False):
        assert act_arguments(call("act", decoded), False) is None
    for text in (" ", "null", "[]", '"{}"', "0"):
        assert act_arguments(call("act", text), False) is None


def test_i3_merges_each_key_against_the_item_as_it_then_stands():
    # turns.md 7.4 I3: the two examples of the rule, and "unless the item already has it"
    assert read_items([{"args": {"params": {"x": 1}}}]) == [(None, {"x": 1})]
    assert read_items([{"params": {"args": {"x": 1}}}]) == [(None, {"args": {"x": 1}})]
    assert read_items([{"action": "bid", "x": 0, "args": {"x": 1, "y": 2}, "params": {"y": 3, "z": 4}}]) == [
        ("bid", {"x": 0, "y": 2, "z": 4})
    ]


def test_i5_reads_an_act_item_after_its_nested_arguments():
    # turns.md 7.4 I3 then I5: an `actions` list merged in from `args` replaces an item named act
    assert read_items([{"action": "act", "args": {"actions": [{"action": "bid"}, {}]}}]) == [("bid", {})]
    assert read_items([{"name": "act", "actions": "bid"}]) == [("act", {"actions": "bid"})]


def test_the_cap_note_follows_the_limit():
    # turns.md 8: {s} and {were} at a limit of 1 and above, after the compute note
    calls = [call("compute", '{"code": "1"}'), act({"actions": ["a", "b", "c"]})]
    assert read_reply(calls, False, 1)["notes"] == [COMPUTE_BESIDE_ACT, "(note: only your first 1 action was run)"]
    assert read_reply(calls, False, 2)["notes"] == [COMPUTE_BESIDE_ACT, "(note: only your first 2 actions were run)"]
    assert read_reply(calls, False, 3)["notes"] == [COMPUTE_BESIDE_ACT]
    assert read_reply(calls, False, 2)["dropped"] == 1


def test_an_unreadable_act_call_discards_a_later_calls_memory():
    # turns.md 10.1: an unreadable reply carries no memory, whatever any of its act calls holds
    reading = read_reply([act('{"actions": "bid"}'), act({"memory": "kept?"})], False, 6)
    assert (reading["error"], reading["memory"]) == (UNREADABLE, None)


def test_turn_memory_takes_the_corrections_memory_first():
    # turns.md 10.3: the correction's memory if it was read and had one, else the decide reply's
    assert turn_memory("old", "decide", "fix", 2000) == {
        "written": True, "text": "fix", "from_correction": True, "note": None}
    assert turn_memory("old", "decide", None, 2000) == {
        "written": True, "text": "decide", "from_correction": False, "note": None}
    assert turn_memory(None, None, None, 2000) == {
        "written": False, "text": None, "from_correction": False, "note": NO_MEMORY}


def test_turn_memory_cut_follows_memory_chars():
    # turns.md 10.2 and 10.4: cut to turns.memory_chars code points, untrimmed; the note names both lengths
    end = turn_memory("old", None, " a\U0001f600\nb ", 3)
    assert end["text"] == " a\U0001f600"
    assert end["note"] == "(note: your memory was cut to its first 3 of 6 characters)"
    assert turn_memory(None, "ab", None, 1)["note"] == "(note: your memory was cut to its first 1 of 2 characters)"
    assert turn_memory(None, "ab", None, 2)["note"] is None


def test_out_of_business_comes_after_the_names_own_refusals():
    # actions-buyer.md 1.2 steps 2 to 6; turns.md 13.2: the name's refusal first, then out of business
    assert name_refusal("bid", "manager", "off", True) == OUT_OF_BUSINESS
    assert name_refusal("pay", "manager", "off", True) == OUT_OF_BUSINESS
    assert name_refusal("compute", "manager", "off", True) == OUT_OF_BUSINESS
    assert name_refusal("send_message", "manager", "all", True) == OUT_OF_BUSINESS
    assert name_refusal(None, "manager", "off", True) == NO_NAME
    assert name_refusal("note", "manager", "off", True).startswith("'note' is not available in this market.")
    assert name_refusal("end_turn", "manager", "off", True).startswith("unknown action 'end_turn'.")
    assert name_refusal("post_task", "manager", "off", True) == (
        "'post_task' is not available to a manager: it is a buyer's action")


def test_who_can_send_and_the_lists():
    # messaging.md 1 and actions-buyer.md 1.1: the four lists, by role and mode
    modes = ("off", "managers", "buyers", "within_roles", "all")
    sends = {(role, mode) for role in ("buyer", "manager") for mode in modes if can_send(role, mode)}
    assert sends == {("buyer", "buyers"), ("buyer", "within_roles"), ("buyer", "all"),
                     ("manager", "managers"), ("manager", "within_roles"), ("manager", "all")}
    assert action_list("buyer", "managers") == (
        "pay, post_task, accept_bid, test_delivery, approve_submission, reject_submission, pay_escrow_arbiter, "
        "end_contract, edit_post, withdraw_post, keep_delivery")
    assert action_list("buyer", "all") == "send_message, " + action_list("buyer", "off")
    assert action_list("manager", "buyers") == (
        "pay, bid, delegate_run, run_myself, test_result, deliver, dispute, withdraw_bid")
    assert action_list("manager", "managers") == "send_message, " + action_list("manager", "off")


def test_result_line_ok_and_error():
    # turns.md 9: `- {shown}: OK: {message}` and `- {shown}: ERROR: {message}`, the name trimmed, `?` with none
    assert result_line(" Bid ", True, "done") == "- Bid: OK: done"
    assert result_line("  ", False, NO_NAME) == "- ?: ERROR: " + NO_NAME
    assert result_line(5, False, NO_NAME) == "- ?: ERROR: " + NO_NAME


def test_a_reply_with_no_function_call_may_arrive_as_none():
    # models.md 2.2: the reply's function calls, "or none"; the model layer gives None for none
    assert read_reply(None, False, 6)["error"] == NOT_A_CALL
    assert read_reply(None, True, 6)["error"] == NOT_A_CALL + CUT
