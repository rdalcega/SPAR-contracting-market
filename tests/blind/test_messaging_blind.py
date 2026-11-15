"""Blind tests derived from spec/messaging.md and its referenced definitions.

No network, file writes, or implementation-derived expected values.
Run with bytecode and pytest's cache disabled if execution must be read-only:
    PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/blind/test_messaging_blind.py
"""

from market.fmt import ONE_LINE  # the one-line rule lives in market/fmt.py
import copy
import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

from market import actions, config, messaging, models, reading, state


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "spec"
IDS = ("B-c53", "B-d75", "M-14b", "M-849", "M-f8c")
MODES = ("off", "managers", "buyers", "within_roles", "all")
ENABLED = (
    ("managers", "manager"),
    ("buyers", "buyer"),
    ("within_roles", "manager"),
    ("within_roles", "buyer"),
    ("all", "manager"),
    ("all", "buyer"),
)
ACTION_LISTS = {
    "buyer": (
        "pay, post_task, accept_bid, test_delivery, approve_submission, "
        "reject_submission, pay_escrow_arbiter, end_contract, edit_post, "
        "withdraw_post, keep_delivery"
    ),
    "manager": (
        "pay, bid, delegate_run, run_myself, test_result, deliver, dispute, "
        "withdraw_bid"
    ),
}
GROUP_LABELS = {
    "managers": "all managers",
    "buyers": "all buyers",
    "everyone": "everyone",
}
GROUP_HEADINGS = {
    "managers": "Conversation among all managers",
    "buyers": "Conversation among all buyers",
    "everyone": "Conversation among all participants",
}
AUDIENCES = {
    "managers": (
        "This conversation is among all managers: every manager reads it, "
        "and no buyer does."
    ),
    "buyers": (
        "This conversation is among all buyers: every buyer reads it, "
        "and no manager does."
    ),
    "everyone": (
        "This conversation is among all participants: every buyer and "
        "manager reads it."
    ),
}
UNAVAILABLE = "(summary unavailable; the earlier messages are not shown)"


def market(mode="all", ids=IDS, **messaging_options):
    cfg = config.resolve({
        "message_channels": mode,
        "messaging": messaging_options,
    })
    m = state.Market(seed=0, cfg=cfg)
    m.day, m.round = 11, 1
    for pid in ids:
        state.add_participant(
            m, pid, "buyer" if pid.startswith("B-") else "manager", 1.0
        )
    return m


def actor(m, role):
    return m.participants["B-c53" if role == "buyer" else "M-14b"]


def financial_state(m):
    return copy.deepcopy([
        (p.id, p.balance, p.ledger, p.pending, p.out_of_business)
        for p in m.participants.values()
    ])


def send(m, p, args):
    return messaging.send(m, p, args, {})


def pair(a, b):
    return tuple(sorted((a, b)))


def add(m, sender="M-849", to="everyone", text="hello", day=1, round_=1):
    m.messages.append(state.Message(day, round_, sender, to, text))
    return len(m.messages) - 1


def fill(m, n, to="everyone", sender="M-849"):
    for i in range(n):
        add(m, sender, to, "message " + str(i))


def render(m, pid="M-14b", backend=None, tracker=None):
    title, items = messaging.section(m, m.participants[pid], backend, tracker)
    return title + "\n\n" + "\n\n".join(items)


def records_of(records, kind):
    return [record for record in records if record["type"] == kind]


class Tracker:
    def __init__(self, stop_at=None):
        self.checks = 0
        self.stop_at = stop_at

    def check(self):
        self.checks += 1
        if self.checks == self.stop_at:
            raise models.CostCapReached("test cap")


class Script:
    def __init__(self, *outcomes, tracker=None):
        self.outcomes = list(outcomes)
        self.calls = []
        self.tracker = tracker

    def call(self, messages, functions=None, tool_choice=None):
        assert not functions
        assert tool_choice is None
        assert len(messages) == 2
        assert [x["role"] for x in messages] == ["system", "user"]
        self.calls.append(copy.deepcopy(messages))
        assert self.outcomes, "Unexpected summarizer call"
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            if self.tracker is not None and isinstance(outcome, models.CallFailed):
                self.tracker.add(outcome.failed_cost)
            raise outcome
        if self.tracker is not None:
            self.tracker.add(outcome.failed_cost)
            self.tracker.add(outcome.cost, outcome.cost_known)
        return outcome


def fence_after(text, marker):
    tail = text.split(marker, 1)[1]
    return tail.split("```\n", 1)[1].split("\n```", 1)[0]


def prompt_templates():
    text = (SPEC / "messaging.md").read_text(encoding="utf-8")
    system = fence_after(text, "The system message is the same in every mode:")
    first = fence_after(text, "**First summary**")
    update = fence_after(text, "**Update**")
    return system, first, update


def expected_request(key, messages, prior=None, covered=0):
    system, first, update = prompt_templates()
    if isinstance(key, tuple):
        ordered = sorted(key, key=lambda pid: (not pid.startswith("M-"), pid))
        a, b = ordered
        audience = f"This conversation is private to {a} and {b}: only they read it."
        label = f"Conversation between {a} and {b}"
    else:
        audience = AUDIENCES[key]
        label = GROUP_HEADINGS[key]
    lines = "\n".join(
        f"[day {msg.day}, round {msg.round}] {msg.sender} -> "
        f"{GROUP_LABELS.get(msg.to, msg.to)}: "
        + " ".join(msg.text.splitlines())
        for msg in messages
    )
    n = len(messages)
    if prior is None:
        user = first.format(
            audience=audience,
            conversation=label,
            N=n,
            s="" if n == 1 else "s",
            W=min(200, 40 + 15 * n),
            lines=lines,
        )
    else:
        user = update.format(
            audience=audience,
            conversation=label,
            P=covered,
            K=n,
            T=covered + n,
            sP="" if covered == 1 else "s",
            sK="" if n == 1 else "s",
            W=min(200, 40 + 15 * (covered + n)),
            prior=prior,
            lines=lines,
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("role", ("buyer", "manager"))
def test_role_availability_and_refusal_precedes_arguments(mode, role):
    # Messaging 1, 2.2; actions-buyer 1.1-1.2.
    enabled = mode in ("all", "within_roles", role + "s")
    assert actions.can_send(role, mode) is enabled
    expected_list = ("send_message, " if enabled else "") + ACTION_LISTS[role]
    assert actions.action_list(role, mode) == expected_list
    refusal = actions.name_refusal("send_message", role, mode, False)
    if enabled:
        assert refusal is None
    elif mode == "off":
        assert refusal == (
            "'send_message' is not available in this market. Your actions: "
            + ACTION_LISTS[role] + "."
        )
    else:
        assert refusal == (
            "unknown action 'send_message'. Your actions: "
            + ACTION_LISTS[role] + "."
        )


@pytest.mark.parametrize("mode", MODES)
def test_out_of_business_name_check_order(mode):
    # Messaging 2.2; actions-buyer 1.2: availability precedes insolvency.
    actual = actions.name_refusal("send_message", "manager", mode, True)
    if mode == "off":
        expected = (
            "'send_message' is not available in this market. Your actions: "
            + ACTION_LISTS["manager"] + "."
        )
    elif mode == "buyers":
        expected = (
            "unknown action 'send_message'. Your actions: "
            + ACTION_LISTS["manager"] + "."
        )
    else:
        expected = "you are out of business (balance at or below 0) and cannot act"
    assert actual == expected


@pytest.mark.parametrize("name", ("SEND_MESSAGE", " send_message ", "Send_Message"))
def test_action_names_are_exact(name):
    # Messaging 2.2; actions-buyer 1.2: no case folding or trimming.
    assert actions.name_refusal(name, "manager", "all", False) == (
        f"unknown action '{name}'. Your actions: send_message, "
        + ACTION_LISTS["manager"] + "."
    )


def test_key_only_send_is_nameless_even_when_off():
    # Messaging introduction, 2.2; turns 7.4-7.5.
    parsed = reading.read_items([
        {"send_message": {"to": "everyone", "text": "hi"}},
        {"action": "send_message", "to": "everyone", "text": "hi"},
    ])
    assert parsed == [
        (None, {"send_message": {"to": "everyone", "text": "hi"}}),
        ("send_message", {"to": "everyone", "text": "hi"}),
    ]
    assert actions.name_refusal(parsed[0][0], "buyer", "off", False) == (
        'the action names no action: give its name with "action", as '
        '{"action": "<action name>", "<argument name>": <value>}'
    )


@pytest.mark.parametrize("limit", (1, 2, 6))
def test_successful_and_failed_messages_share_action_cap(limit):
    # Messaging 2.6; turns 7.4, 8: every retained item counts.
    items = [
        {"action": "send_message", "to": "everyone", "text": "one"},
        {"action": "send_message"},
        {"action": "send_message", "to": "everyone", "text": "three"},
        {"action": "send_message", "to": "everyone", "text": "four"},
        {"action": "send_message", "to": "everyone", "text": "five"},
        {"action": "send_message", "to": "everyone", "text": "six"},
        {"action": "send_message", "to": "everyone", "text": "seven"},
    ]
    call = {"function": {"name": "act", "arguments": json.dumps({"actions": items})}}
    result = reading.read_reply([call], False, limit)
    assert result["error"] is None
    assert result["dropped"] == 7 - limit
    assert result["actions"] == [
        ("send_message", {k: v for k, v in item.items() if k != "action"})
        for item in items[:limit]
    ]
    unit = "action was" if limit == 1 else "actions were"
    assert result["notes"] == [
        f"(note: only your first {limit} {unit} run)"
    ]


@pytest.mark.parametrize("mode,role", ENABLED)
@pytest.mark.parametrize(
    "recipient",
    (
        "M-849", "B-d75", "self", "managers", "buyers", "everyone",
        "all", "Managers", " managers ", "all managers", "m-849",
        " M-849 ", "849", " ", "M-\uff18\uff14\uff19", 0, False,
        [], {}, 1.5, None, "",
    ),
)
def test_recipient_table_and_exact_error_precedence(mode, role, recipient):
    # Messaging 2.3-2.4: recipient errors precede missing message text.
    m = market(mode)
    p = actor(m, role)
    if recipient == "self":
        recipient = p.id
    allowed = (
        f'one participant ID, "{role}s" (all the other {role}s) or '
        '"everyone" (all the other participants)'
        if mode == "all"
        else f'one {role} ID or "{role}s" (all the other {role}s)'
    )
    expected = None
    if recipient is None or recipient == "":
        expected = "missing argument 'to'"
    elif not isinstance(recipient, str):
        expected = "to must be " + allowed
    elif recipient in GROUP_LABELS:
        if recipient != role + "s" and not (
            recipient == "everyone" and mode == "all"
        ):
            expected = f'you cannot send to "{recipient}": to must be {allowed}'
    elif recipient not in IDS:
        expected = (
            f"no participant with id '{recipient}' (participants: "
            "B-c53, B-d75, M-14b, M-849, M-f8c)"
        )
    elif recipient == p.id:
        expected = "you cannot message yourself"
    else:
        their_role = "buyer" if recipient.startswith("B-") else "manager"
        if mode != "all" and their_role != role:
            expected = (
                f"{recipient} is a {their_role}: messages go only to other "
                f'{role}s (to a {role} ID, or to "{role}s" for all of them)'
            )

    before = copy.deepcopy(m)
    assert send(m, p, {"to": recipient}) == (
        False, expected or "missing argument 'text'"
    )
    assert m == before

    if expected is None:
        assert send(m, p, {"to": recipient, "text": "hi"}) == (
            True, "message sent to " + GROUP_LABELS.get(recipient, recipient)
        )
        assert m.messages == [state.Message(11, 1, p.id, recipient, "hi")]


@pytest.mark.parametrize(
    "args,error",
    [
        ({}, "missing argument 'to'"),
        ({"recipient": "M-849", "text": "hi"}, "missing argument 'to'"),
        ({"to": "M-849", "message": "hi"}, "missing argument 'text'"),
        ({"to": "M-849", "content": "hi"}, "missing argument 'text'"),
        ({"to": "M-849", "text": None}, "missing argument 'text'"),
        ({"to": "M-849", "text": ""}, "missing argument 'text'"),
        ({"to": "M-14b", "text": " "}, "you cannot message yourself"),
    ],
)
def test_argument_names_missing_values_and_order(args, error):
    # Messaging 2.1, 2.3; actions-buyer 1.4.
    m = market()
    before = copy.deepcopy(m)
    assert send(m, actor(m, "manager"), args) == (False, error)
    assert m == before


@pytest.mark.parametrize(
    "value,text",
    [
        (0, "0"),
        (False, "False"),
        (True, "True"),
        ([], "[]"),
        ({}, "{}"),
        ([None, False, 3.0], "[None, False, 3.0]"),
        ({"x": [1, "a\nb"]}, "{'x': [1, 'a\\nb']}"),
        ({"a": "a'b"}, "{'a': \"a'b\"}"),
        ({"x": "\u00a0\u200b"}, "{'x': '\\xa0\\u200b'}"),
        (3.0, "3.0"),
        (-0.0, "-0.0"),
        (1e-5, "1e-05"),
        (1e16, "1e+16"),
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (float("-inf"), "-inf"),
        (123456789012345678901234567890, "123456789012345678901234567890"),
        ("\u200b", "\u200b"),
        ("\x00", "\x00"),
        ("\ud800", "\ud800"),
    ],
)
def test_text_conversion_is_text_not_json(value, text):
    # Messaging 2.5; README TEXT, PY and REPR.
    m = market()
    assert send(m, actor(m, "manager"), {
        "to": "M-849", "text": value, "unused": object(),
        "recipient": None, "content": "ignored", "message": "ignored",
    }) == (True, "message sent to M-849")
    assert m.messages[-1].text == text


@pytest.mark.parametrize(
    "text",
    [
        " ", "\t\n\r\v\f", "\x1c\x1d\x1e\x1f", "\x85",
        "\u00a0", "\u1680", "\u2000\u2001\u2002\u2003\u2004\u2005",
        "\u2006\u2007\u2008\u2009\u200a", "\u2028\u2029",
        "\u202f\u205f\u3000",
    ],
)
def test_whitespace_blank_check(text):
    # Messaging 2.3(i); README's complete whitespace definition.
    m = market()
    assert send(m, actor(m, "manager"), {"to": "everyone", "text": text}) == (
        False, "text is blank; no message was sent"
    )
    assert m.messages == []


@pytest.mark.parametrize(
    "cap,text,stored,suffix",
    [
        (1, "ab", "a", " (cut to its first 1 character)"),
        (2, "\U0001f600e\u0301", "\U0001f600e",
         " (cut to its first 2 characters)"),
        (2, "\r\nx", "\r\n", " (cut to its first 2 characters)"),
        (2, " a", " a", ""),
        (3, " a\n", " a\n", ""),
        (500, "x" * 500, "x" * 500, ""),
        (500, "x" * 501, "x" * 500, " (cut to its first 500 characters)"),
        (500, " " * 600 + "hi", " " * 500,
         " (cut to its first 500 characters)"),
    ],
)
def test_message_cut_preserves_code_points_whitespace_and_exact_result(
    cap, text, stored, suffix
):
    # Messaging 2.5: cut after blank check, with singular rule.
    m = market(message_chars=cap)
    records = []
    m.log = records.append
    before = financial_state(m)
    assert send(m, actor(m, "manager"), {"to": "managers", "text": text}) == (
        True, "message sent to all managers" + suffix
    )
    assert m.messages == [state.Message(11, 1, "M-14b", "managers", stored)]
    assert financial_state(m) == before
    assert len(records) == 1
    event = records[0]
    assert event["type"] == "message"
    assert event["day"] == 11 and event["round"] == 1
    assert event["sender"] == "M-14b"
    assert event["to"] == "managers"
    assert event["sent"] == text
    assert event["text"] == stored


def test_message_log_records_converted_uncut_text():
    # Messaging 2.5, 7; running 6.3(E).
    m = market(message_chars=3)
    records = []
    m.log = records.append
    send(m, actor(m, "manager"), {"to": "B-c53", "text": [1, False]})
    event = records_of(records, "message")[0]
    assert event["to"] == "B-c53"
    assert event["sent"] == "[1, False]"
    assert event["text"] == "[1,"


def test_messages_are_free_append_only_and_reach_out_of_business_recipient():
    # Messaging 1, 2.5-2.6: no separate message limit, money or news.
    m = market()
    m.participants["M-849"].out_of_business = True
    before = financial_state(m)
    for i in range(20):
        assert send(m, actor(m, "buyer"), {"to": "M-849", "text": i})[0]
    assert m.messages == [
        state.Message(11, 1, "B-c53", "M-849", str(i)) for i in range(20)
    ]
    assert financial_state(m) == before
    assert m.summaries == {}
    assert m.counts.get("summarizer_calls", 0) == 0
    assert pair("B-c53", "M-849") in messaging.conversations(
        m, actor(m, "buyer")
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", ""), ("\n", ""), ("\n\n", " "),
        ("a\n\nb", "a  b"), ("abc\n", "abc"),
        ("abc\n\n", "abc "), ("\nabc", " abc"),
        (" a\t b ", " a\t b "),
        ("a\r\nb", "a b"), ("a\r\n\r\n", "a "),
        ("a\x1fb", "a\x1fb"),
        ("a\u00a0b", "a\u00a0b"),
    ],
)
def test_one_line_examples_and_non_boundaries(text, expected):
    # Messaging 3.3: ONE_LINE preserves all non-boundary characters.
    assert ONE_LINE(text) == expected


@pytest.mark.parametrize(
    "boundary",
    ("\n", "\r", "\r\n", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e",
     "\x85", "\u2028", "\u2029"),
)
def test_every_line_boundary(boundary):
    # Messaging 3.3: a final boundary adds nothing; other boundaries add space.
    assert ONE_LINE("a" + boundary + "b") == "a b"
    assert ONE_LINE("a" + boundary) == "a"
    assert ONE_LINE("a" + boundary + boundary) == "a "
    assert ONE_LINE(boundary + "a") == " a"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("pid", ("B-c53", "M-849"))
def test_conversation_order_is_role_then_code_point_not_seat_order(mode, pid):
    # Messaging 1, 3.1, 4.2: private keys are unordered pairs.
    m = market(mode, ids=("B-d75", "B-c53", "M-f8c", "M-849", "M-14b"))
    m.participants["M-14b"].out_of_business = True
    p = m.participants[pid]
    if mode not in ("all", "within_roles", p.role + "s"):
        expected = []
    else:
        others = (
            ["M-14b", "M-f8c", "B-c53", "B-d75"]
            if pid == "M-849" and mode == "all"
            else ["M-14b", "M-849", "M-f8c", "B-d75"]
            if pid == "B-c53" and mode == "all"
            else ["M-14b", "M-f8c"]
            if pid == "M-849"
            else ["B-d75"]
        )
        expected = [pair(pid, other) for other in others] + [p.role + "s"]
        if mode == "all":
            expected.append("everyone")
    assert messaging.conversations(m, p) == expected


@pytest.mark.parametrize("mode,role", ENABLED)
def test_empty_section_has_every_heading(mode, role):
    # Messaging 3.1-3.2: empty conversations are present, in exact order.
    m = market(mode)
    p = actor(m, role)
    if role == "manager":
        others = ["M-849", "M-f8c"]
        if mode == "all":
            others += ["B-c53", "B-d75"]
    else:
        others = ["M-14b", "M-849", "M-f8c"] if mode == "all" else []
        others += ["B-d75"]
    expected_items = [
        f"Your conversation with {other}: (no messages yet)" for other in others
    ]
    expected_items.append(f"Conversation among all {role}s: (no messages yet)")
    if mode == "all":
        expected_items.append("Conversation among all participants: (no messages yet)")
    expected_title = (
        "--- Messages ---" if mode == "all"
        else f"--- Messages between {role}s ---"
    )
    backend = Script()
    tracker = Tracker()
    assert messaging.section(m, p, backend, tracker) == (
        expected_title, expected_items
    )
    assert backend.calls == []
    assert tracker.checks == 0


def test_groups_exist_with_one_member_and_stay_separate_with_two():
    # Messaging 1: a role group exists independently of its membership count.
    one = market("managers", ids=("B-c53", "M-14b"))
    assert messaging.conversations(one, one.participants["M-14b"]) == ["managers"]
    assert send(one, one.participants["M-14b"], {
        "to": "managers", "text": "self group"
    }) == (True, "message sent to all managers")
    two = market("managers", ids=("B-c53", "M-14b", "M-849"))
    add(two, "M-14b", "M-849", "private")
    add(two, "M-14b", "managers", "group")
    assert messaging.thread(two, pair("M-14b", "M-849")) == [0]
    assert messaging.thread(two, "managers") == [1]


def test_thread_filters_without_sorting_messages_by_timestamp():
    # Messaging 2.5-2.6, 3.1: sequence order, both private directions.
    m = market()
    add(m, "M-14b", "M-849", "first", day=9)
    add(m, "M-849", "managers", "group")
    add(m, "M-849", "M-14b", "second", day=1)
    add(m, "B-c53", "M-849", "unrelated")
    add(m, "M-f8c", "everyone", "public")
    add(m, "B-d75", "buyers", "buyers")
    assert messaging.thread(m, pair("M-14b", "M-849")) == [0, 2]
    assert messaging.thread(m, "managers") == [1]
    assert messaging.thread(m, "everyone") == [4]
    assert messaging.thread(m, "buyers") == [5]
    assert messaging.thread(m, pair("M-14b", "M-f8c")) == []


def test_message_lines_destinations_new_marks_spacing_and_read_progress():
    # Messaging 3.2-3.4: exact display and independent reader state.
    m = market()
    add(m, "M-849", "M-14b", "a\n\nb\n", day=1000000, round_=2)
    add(m, "M-14b", "M-849", " reply ", day=1000000, round_=2)
    add(m, "M-f8c", "managers", "group")
    add(m, "M-14b", "everyone", "own")
    add(m, "B-c53", "everyone", "public")
    add(m, "B-c53", "buyers", "not visible")
    title, items = messaging.section(m, m.participants["M-14b"], None, None)
    assert title == "--- Messages ---"
    assert items == [
        "Your conversation with M-849:\n"
        "  [day 1000000, round 2] M-849 -> you (NEW): a  b\n\n"
        "  [day 1000000, round 2] you -> M-849:  reply ",
        "Your conversation with M-f8c: (no messages yet)",
        "Your conversation with B-c53: (no messages yet)",
        "Your conversation with B-d75: (no messages yet)",
        "Conversation among all managers:\n"
        "  [day 1, round 1] M-f8c -> all managers (NEW): group",
        "Conversation among all participants:\n"
        "  [day 1, round 1] you -> everyone: own\n\n"
        "  [day 1, round 1] B-c53 -> everyone (NEW): public",
    ]
    assert m.participants["M-14b"].seen == 6
    assert m.participants["M-14b"].unseen == []
    assert "(NEW)" not in render(m)
    assert m.participants["M-849"].seen == 0
    assert "(NEW)" in render(m, "M-849")


@pytest.mark.parametrize(
    "sender,index,seen,unseen,expected",
    [
        ("M-14b", 0, 0, [], False),
        ("M-14b", 0, 1, [0], False),
        ("M-849", 0, 0, [], True),
        ("M-849", 0, 1, [], False),
        ("M-849", 0, 1, [0], True),
        ("M-849", 1, 1, [], True),
        ("M-849", 1, 2, [0], False),
    ],
)
def test_new_state_predicate(sender, index, seen, unseen, expected):
    # Messaging 3.4; API's corrected signature is is_new(m, p, i).
    m = market()
    for _ in range(index + 1):
        add(m, sender, "everyone")
    p = m.participants["M-14b"]
    p.seen, p.unseen = seen, unseen
    assert messaging.is_new(m, p, index) is expected


@pytest.mark.parametrize(
    "recent,length,cut",
    [
        (2, 0, 0), (2, 1, 0), (2, 2, 0), (2, 3, 2),
        (2, 4, 2), (2, 5, 4),
        (10, 1, 0), (10, 9, 0), (10, 10, 0),
        (10, 11, 10), (10, 20, 10), (10, 21, 20),
        (10, 25, 20), (10, 30, 20), (10, 31, 30),
        (3, 10, 9), (100, 101, 100),
    ],
)
def test_batch_boundaries_and_mock_summary(recent, length, cut):
    # Messaging 4.1, 5.5: cut = k * floor((L - 1) / k), L >= 1.
    m = market(recent=recent)
    fill(m, length)
    tracker = Tracker(stop_at=1)
    text = render(m, tracker=tracker)
    assert tracker.checks == 0
    if cut:
        assert m.summaries == {
            "everyone": (f"(mock summary of {cut} messages)", cut)
        }
        assert (
            f"  Summary of the {cut} earlier messages "
            f"(written automatically, {cut} of them new; you are M-14b in it): "
            f"(mock summary of {cut} messages)"
        ) in text
        assert m.counts.get("summaries_written", 0) == 1
    else:
        assert m.summaries == {}
        assert "Summary of" not in text
        assert m.counts.get("summaries_written", 0) == 0
    assert text.count("  [day ") == length - cut
    assert m.counts.get("summarizer_calls", 0) == 0
    assert m.counts.get("summarizer_dollars", 0) == 0
    assert m.participants["M-14b"].seen == length


def test_jump_from_five_to_twenty_five_needs_one_summary_of_twenty():
    # Messaging 4.1's worked jump example; 4.3 lazy creation.
    m = market()
    fill(m, 5)
    backend = Script(models.Reply(text="twenty"))
    tracker = Tracker()
    render(m, backend=backend, tracker=tracker)
    assert backend.calls == []
    fill(m, 20)
    assert m.summaries == {}
    render(m, backend=backend, tracker=tracker)
    assert len(backend.calls) == 1
    assert backend.calls[0] == expected_request("everyone", m.messages[:20])
    assert m.summaries == {"everyone": ("twenty", 20)}


@pytest.mark.parametrize(
    "key,pid,sender,to",
    [
        ("everyone", "M-14b", "B-c53", "everyone"),
        ("managers", "M-14b", "M-849", "managers"),
        ("buyers", "B-c53", "B-d75", "buyers"),
        (("M-14b", "M-849"), "M-14b", "M-849", "M-14b"),
        (("B-c53", "B-d75"), "B-c53", "B-d75", "B-c53"),
        (("B-d75", "M-f8c"), "B-d75", "M-f8c", "B-d75"),
    ],
)
@pytest.mark.parametrize("n,budget", [(2, 70), (3, 85), (10, 190), (11, 200), (20, 200)])
def test_exact_first_prompt_all_audiences_and_word_budget(key, pid, sender, to, n, budget):
    # Messaging 5.1: byte-exact templates, ID order and fixed word formula.
    m = market(recent=n)
    for i in range(n + 1):
        add(m, sender, to, f"item {i}\nsecond\n", day=2, round_=3)
    backend = Script(models.Reply(text="summary"))
    render(m, pid, backend, Tracker())
    assert backend.calls == [expected_request(key, m.messages[:n])]
    assert f"At most {budget} words." in backend.calls[0][1]["content"]
    assert not backend.calls[0][0]["content"].endswith("\n")
    assert not backend.calls[0][1]["content"].endswith("\n")
    assert " -> you:" not in backend.calls[0][1]["content"]
    assert " (NEW)" not in backend.calls[0][1]["content"]


@pytest.mark.parametrize(
    "key,pid,sender,to",
    [
        ("everyone", "M-14b", "M-849", "everyone"),
        ("managers", "M-14b", "M-849", "managers"),
        ("buyers", "B-c53", "B-d75", "buyers"),
        (("B-c53", "M-849"), "B-c53", "M-849", "B-c53"),
    ],
)
def test_update_uses_uncut_prior_and_only_newly_summarized_messages(key, pid, sender, to):
    # Messaging 4.2-4.3, 5.1: one replacing summary, full prior retained.
    m = market(recent=2, summary_chars=50)
    fill(m, 3, to, sender)
    prior = "Prior " + "x" * 100 + " END"
    first = Script(models.Reply(text=prior))
    render(m, pid, first, Tracker())
    assert m.summaries[key] == (prior, 2)
    add(m, sender, to, "fourth")
    add(m, sender, to, "fifth")
    backend = Script(models.Reply(text="replacement"))
    render(m, pid, backend, Tracker())
    assert backend.calls == [expected_request(key, m.messages[2:4], prior, 2)]
    assert m.summaries[key] == ("replacement", 4)
    assert len(m.summaries) == 1
    assert "At most 100 words." in backend.calls[0][1]["content"]


def messages_from_request(example):
    result = []
    for line in example[1]["content"].splitlines():
        if not line.startswith("[day "):
            continue
        match = re.fullmatch(
            "\\[day ([0-9]+), round ([0-9]+)\\] (\\S+) -> (.*?): (.*)",
            line,
        )
        assert match is not None
        day, round_, sender, to, text = match.groups()
        recipient = {
            "all managers": "managers", "all buyers": "buyers"
        }.get(to, to)
        result.append(state.Message(int(day), int(round_), sender, recipient, text))
    return result


@pytest.mark.parametrize("update", (False, True))
def test_complete_published_request_examples(update):
    # Messaging 5.1; examples/summarizer_request_{first,update}_all.json.
    name = "summarizer_request_update_all.json" if update else "summarizer_request_first_all.json"
    expected = json.loads((SPEC / "examples" / name).read_text(encoding="utf-8"))
    first = json.loads(
        (SPEC / "examples" / "summarizer_request_first_all.json").read_text(
            encoding="utf-8"
        )
    )
    m = market()
    m.messages = messages_from_request(first)
    if update:
        m.summaries["everyone"] = ("(summary of 10 messages)", 10)
        m.messages.extend(messages_from_request(expected))
    add(m, "M-849", "everyone", "not in the requested summary")
    backend = Script(models.Reply(text="done"))
    render(m, backend=backend, tracker=Tracker())
    assert backend.calls == [expected]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("  a\t b\nc d  ", "a b c d"),
        ("<think>secret\nstuff</think> visible ", "visible"),
        ("a<think>x</think>b<think>y</think>c", "abc"),
        ("discard</think>discard too</think>kept", "kept"),
        ("kept<think>unfinished", "kept"),
        ("junk</think> kept <think>unfinished", "kept"),
        ("<THINK>literal</THINK>", "<THINK>literal</THINK>"),
        ("a\x1fb\u3000c", "a b c"),
        ("a\u200bb", "a\u200bb"),
        ("(summary)", "(summary)"),
    ],
)
def test_reply_strip_think_then_whitespace_normalization(text, expected):
    # Messaging 4.3, 5.3; turns 2's ordered strip-think rules.
    m = market(recent=2)
    fill(m, 3)
    backend = Script(models.Reply(text=text, reasoning="never visible"))
    rendered = render(m, backend=backend, tracker=Tracker())
    assert m.summaries["everyone"] == (expected, 2)
    assert f"; you are M-14b in it): {expected}" in rendered
    assert "never visible" not in rendered


@pytest.mark.parametrize("reason", (None, "stop", "tool_calls", "other"))
def test_non_failure_finish_reasons_and_tool_calls_do_not_reject_text(reason):
    # Messaging 5.3: only length and content_filter invalidate nonempty text.
    m = market(recent=2)
    fill(m, 3)
    backend = Script(models.Reply(
        text="summary", finish_reason=reason,
        tool_calls=[{"id": "x", "function": {"name": "ignored", "arguments": "{}"}}],
    ))
    render(m, backend=backend, tracker=Tracker())
    assert m.summaries["everyone"] == ("summary", 2)
    assert m.counts.get("summaries_failed", 0) == 0


@pytest.mark.parametrize(
    "bad",
    [
        models.Reply(text="nonempty", finish_reason="length"),
        models.Reply(text="nonempty", finish_reason="content_filter"),
        models.Reply(text=""),
        models.Reply(text=" \t \n"),
        models.Reply(text="<think>hidden</think>"),
        models.Reply(text="<think>unfinished"),
        models.Reply(text="hidden</think>"),
        models.CallFailed("backend unavailable", failed_cost=0.125),
    ],
)
@pytest.mark.parametrize("success_on", (1, 2, 3, 4))
def test_up_to_four_attempts_identical_request_and_stop_on_success(bad, success_on):
    # Messaging 4.3, 5.3: immediate retries, unchanged request, per-attempt counts.
    m = market(recent=2)
    fill(m, 3)
    records = []
    m.log = records.append
    backend = Script(*([bad] * (success_on - 1)), models.Reply(text="ok"))
    tracker = Tracker()
    before = financial_state(m)
    output = render(m, backend=backend, tracker=tracker)
    assert len(backend.calls) == tracker.checks == success_on
    assert all(request == expected_request("everyone", m.messages[:2])
               for request in backend.calls)
    assert m.summaries == {"everyone": ("ok", 2)}
    assert UNAVAILABLE not in output
    assert m.counts.get("summaries_failed", 0) == success_on - 1
    assert m.counts.get("summaries_written", 0) == 1
    expected_replies = 1 if isinstance(bad, Exception) else success_on
    assert m.counts.get("summarizer_calls", 0) == expected_replies
    assert len(records_of(records, "summary_failed")) == success_on - 1
    assert len(records_of(records, "summarizer_call")) == success_on
    assert m.lost_turns == 0
    assert financial_state(m) == before


def test_four_failures_hide_history_preserve_previous_and_retry_next_reader():
    # Messaging 4.3-4.4: stale summary is retained but not displayed.
    m = market(recent=2)
    fill(m, 5)
    m.summaries["everyone"] = ("old", 2)
    bad = models.Reply(text="unusable", finish_reason="length")
    backend = Script(bad, bad, bad, bad, models.Reply(text="new"))
    tracker = Tracker()
    output = render(m, backend=backend, tracker=tracker)
    assert m.summaries["everyone"] == ("old", 2)
    assert (
        "  Summary of the 4 earlier messages "
        "(written automatically, 4 of them new): " + UNAVAILABLE
    ) in output
    assert "you are M-14b in it" not in output
    assert ": old" not in output
    assert "message 0" not in output
    assert "message 1" not in output
    assert "message 2" not in output
    assert "message 3" not in output
    assert "message 4" in output
    assert m.participants["M-14b"].seen == 5
    assert m.participants["M-14b"].unseen == [0, 1, 2, 3]
    assert len(backend.calls) == 4
    render(m, "B-c53", backend, tracker)
    assert len(backend.calls) == 5
    assert all(request == expected_request("everyone", m.messages[2:4], "old", 2)
               for request in backend.calls)
    assert m.summaries["everyone"] == ("new", 4)
    final = render(m, backend=Script(), tracker=Tracker(stop_at=1))
    assert "(written automatically, 4 of them new; you are M-14b in it)" in final
    assert m.participants["M-14b"].unseen == []
    assert "(written automatically; you are M-14b in it)" in render(m)


def test_first_summary_failure_retries_as_first_summary():
    # Messaging 4.3: failed first attempts store nothing.
    m = market(recent=2)
    fill(m, 3)
    bad = models.Reply(text="")
    backend = Script(bad, bad, bad, bad)
    render(m, backend=backend, tracker=Tracker())
    assert m.summaries == {}
    succeeding = Script(models.Reply(text="new"))
    render(m, backend=succeeding, tracker=Tracker())
    assert succeeding.calls == [expected_request("everyone", m.messages[:2])]


def test_hidden_messages_only_others_remain_new_and_visible_tail_is_seen():
    # Messaging 3.4-3.5, 4.4: placeholder does not consume hidden new messages.
    m = market(recent=2)
    add(m, "M-14b", "everyone", "own hidden")
    add(m, "M-849", "everyone", "other hidden")
    add(m, "M-f8c", "everyone", "visible")
    bad = models.Reply(text="")
    output = render(m, backend=Script(bad, bad, bad, bad), tracker=Tracker())
    assert "(written automatically, 1 of them new): " + UNAVAILABLE in output
    assert m.participants["M-14b"].unseen == [1]
    output = render(m, backend=Script(models.Reply(text="done")), tracker=Tracker())
    assert "(written automatically, 1 of them new; you are M-14b in it)" in output
    assert "M-f8c -> everyone (NEW): visible" not in output
    assert "M-f8c -> everyone: visible" in output


def test_hidden_failure_worked_example():
    # Messaging 4.4's exact 20-earlier / 8-new example.
    m = market()
    fill(m, 12, sender="M-849")
    fill(m, 8, sender="M-f8c")
    add(m, "M-f8c", "everyone", "more 8")
    m.participants["M-14b"].seen = 12
    bad = models.Reply(text="")
    _, items = messaging.section(
        m, m.participants["M-14b"], Script(bad, bad, bad, bad), Tracker()
    )
    assert items[-1] == (
        "Conversation among all participants:\n"
        "  Summary of the 20 earlier messages "
        "(written automatically, 8 of them new): "
        "(summary unavailable; the earlier messages are not shown)\n\n"
        "  [day 1, round 1] M-f8c -> everyone (NEW): more 8"
    )
    assert m.participants["M-14b"].unseen == list(range(12, 20))


def test_summary_shared_by_both_private_readers_with_individual_you_clause():
    # Messaging 4.2-4.3: the private conversation has one shared summary.
    m = market(recent=2)
    add(m, "M-849", "B-c53", "one")
    add(m, "B-c53", "M-849", "two")
    add(m, "M-849", "B-c53", "three")
    backend = Script(models.Reply(text="shared"))
    tracker = Tracker()
    buyer = render(m, "B-c53", backend, tracker)
    manager = render(m, "M-849", backend, tracker)
    assert len(backend.calls) == tracker.checks == 1
    assert m.summaries == {("B-c53", "M-849"): ("shared", 2)}
    assert "(written automatically, 1 of them new; you are B-c53 in it): shared" in buyer
    assert "(written automatically, 1 of them new; you are M-849 in it): shared" in manager


def test_lost_view_read_state_can_be_restored_without_undoing_summary():
    # Messaging 3.4: caller restores cursors; stored summary and cost survive.
    m = market(recent=2)
    fill(m, 3)
    p = m.participants["M-14b"]
    previous = (p.seen, list(p.unseen))
    backend = Script(models.Reply(text="paid", cost=0.125))
    first = render(m, backend=backend, tracker=Tracker())
    p.seen, p.unseen = previous
    second = render(m, backend=Script(), tracker=Tracker(stop_at=1))
    assert first == second
    assert m.summaries == {"everyone": ("paid", 2)}
    assert m.counts["summarizer_dollars"] == 0.125
    assert m.counts["summaries_written"] == 1


def test_conversations_are_summarized_in_view_order():
    # Messaging 3.1, 4.3: call and log order follow conversation order.
    m = market(recent=2)
    destinations = ["M-849", "M-f8c", "B-c53", "B-d75", "managers", "everyone"]
    for to in reversed(destinations):
        fill(m, 3, to, "M-14b")
    backend = Script(*(models.Reply(text=str(i)) for i in range(6)))
    records = []
    m.log = records.append
    render(m, backend=backend, tracker=Tracker())
    expected_keys = [
        ("M-14b", "M-849"), ("M-14b", "M-f8c"),
        ("B-c53", "M-14b"), ("B-d75", "M-14b"),
        "managers", "everyone",
    ]
    assert [r["conversation"] for r in records_of(records, "summary")] == expected_keys
    assert [r["conversation"] for r in records_of(records, "summarizer_call")] == expected_keys
    for i, key in enumerate(expected_keys):
        assert m.summaries[key] == (str(i), 2)


@pytest.mark.parametrize("stop_at", (1, 2, 3, 4))
def test_cost_cap_checked_before_each_retry_and_never_a_failed_attempt(stop_at):
    # Messaging 5.3; models 6-7: stop propagates, no placeholder or lost turn.
    m = market(recent=2)
    fill(m, 3)
    p = m.participants["M-14b"]
    p.unseen = [0]
    previous = (p.seen, list(p.unseen))
    records = []
    m.log = records.append
    backend = Script(*(models.Reply(text="") for _ in range(stop_at - 1)))
    tracker = Tracker(stop_at=stop_at)
    with pytest.raises(models.CostCapReached):
        render(m, backend=backend, tracker=tracker)
    assert len(backend.calls) == stop_at - 1
    assert tracker.checks == stop_at
    assert (p.seen, p.unseen) == previous
    assert m.summaries == {}
    assert m.counts.get("summaries_failed", 0) == stop_at - 1
    assert len(records_of(records, "summary_failed")) == stop_at - 1
    assert m.lost_turns == 0


def test_cap_between_conversations_preserves_completed_summary():
    # Messaging 4.3, 5.3: earlier cost is known before the next conversation.
    m = market(recent=2)
    fill(m, 3, "M-14b", "M-849")
    fill(m, 3, "M-14b", "M-f8c")
    tracker = models.CostTracker(0.125)
    backend = Script(models.Reply(text="first", cost=0.125), tracker=tracker)
    with pytest.raises(models.CostCapReached):
        render(m, backend=backend, tracker=tracker)
    assert len(backend.calls) == 1
    assert tracker.total == 0.125
    assert m.summaries == {("M-14b", "M-849"): ("first", 2)}
    assert m.participants["M-14b"].seen == 0
    assert m.counts.get("summaries_failed", 0) == 0


def test_reply_cost_tokens_unknown_cost_and_full_logging():
    # Messaging 5.4, 7; models 6; running 6.3(D,F), 6.7.
    m = market(recent=2)
    fill(m, 3)
    records = []
    m.log = records.append
    replies = [
        models.Reply(
            text="unusable", finish_reason="length",
            input_tokens=11, output_tokens=7, reasoning_tokens=3,
            cost=0.125, failed_cost=0.25, cost_source="reported",
        ),
        models.Reply(
            text=" ", input_tokens=13, output_tokens=5, cost=0,
            cost_known=False, cost_source="unknown",
            input_estimated=True, output_estimated=True,
        ),
        models.Reply(
            text="  final\nsummary ", reasoning="hidden",
            input_tokens=17, output_tokens=9, reasoning_tokens=4,
            cost=0.0625, failed_cost=0.5, cost_source="computed",
            finish_reason="stop", served_by="test-provider",
            tool_calls=[{"id": "z", "function": {"name": "x", "arguments": "{}"}}],
        ),
    ]
    tracker = models.CostTracker(None)
    backend = Script(*replies, tracker=tracker)
    before = financial_state(m)
    output = render(m, backend=backend, tracker=tracker)
    assert financial_state(m) == before
    assert m.counts["summarizer_calls"] == 3
    assert m.counts["summarizer_input_tokens"] == 41
    assert m.counts["summarizer_output_tokens"] == 21
    assert m.counts["summarizer_dollars"] == 0.1875
    assert m.counts["calls_unknown_cost"] == 1
    assert m.counts["summaries_failed"] == 2
    assert m.counts["summaries_written"] == 1
    assert tracker.total == 0.9375
    assert tracker.unknown == 1
    assert m.lost_turns == 0
    assert m.summaries["everyone"] == ("final summary", 2)
    assert "hidden" not in output
    calls = records_of(records, "summarizer_call")
    assert len(calls) == 3
    for event, reply in zip(calls, replies):
        assert event["conversation"] == "everyone"
        assert event["covers"] == 2
        assert event["messages"] == expected_request("everyone", m.messages[:2])
        assert event["spec"] == m.cfg["models"]["summarizer"]
        for field, value in asdict(reply).items():
            if field != "latency":
                assert event["reply"][field] == value
    failures = records_of(records, "summary_failed")
    assert [event["cost"] for event in failures] == [0.125, 0]
    assert all(event["conversation"] == "everyone" for event in failures)
    assert all(event["covers"] == 2 and event["error"] for event in failures)
    written = records_of(records, "summary")
    assert len(written) == 1
    assert written[0]["conversation"] == "everyone"
    assert written[0]["covers"] == 2
    assert written[0]["text"] == "final summary"
    assert written[0]["cost"] == 0.0625


def test_failed_call_cost_is_cap_only_and_failure_is_logged():
    # Messaging 5.3-5.4, 7; models 4.4, 6-7.
    m = market(recent=2)
    fill(m, 3)
    records = []
    m.log = records.append
    tracker = models.CostTracker(None)
    backend = Script(
        models.CallFailed("provider failure", failed_cost=0.25),
        models.Reply(text="ok", cost=0.125),
        tracker=tracker,
    )
    render(m, backend=backend, tracker=tracker)
    assert tracker.total == 0.375
    assert m.counts["summarizer_dollars"] == 0.125
    assert m.counts["summarizer_calls"] == 1
    assert m.counts["summaries_failed"] == 1
    assert m.counts.get("calls_unknown_cost", 0) == 0
    call = records_of(records, "summarizer_call")[0]
    assert call["error"] == "provider failure"
    assert call["failed_cost"] == 0.25
    assert call["messages"] == expected_request("everyone", m.messages[:2])
    failure = records_of(records, "summary_failed")[0]
    assert failure["error"] == "provider failure"
    assert failure["covers"] == 2
    assert failure["conversation"] == "everyone"


def test_mock_summary_logging_is_free_even_at_reached_cap():
    # Messaging 5.5, 7: no model call or cap check for the free summary.
    m = market(recent=2)
    fill(m, 3)
    records = []
    m.log = records.append
    tracker = models.CostTracker(1)
    tracker.add(1)
    before = financial_state(m)
    render(m, tracker=tracker)
    assert tracker.total == 1
    assert financial_state(m) == before
    assert records_of(records, "summarizer_call") == []
    assert records_of(records, "summary_failed") == []
    written = records_of(records, "summary")
    assert len(written) == 1
    assert written[0]["text"] == "(mock summary of 2 messages)"
    assert written[0]["covers"] == 2
    assert written[0]["cost"] == 0


@pytest.mark.parametrize(
    "text,cap,expected",
    [
        ("", 50, ""),
        (" \talpha\n\nbeta  ", 50, "alpha beta"),
        ("x" * 50, 50, "x" * 50),
        ("x" * 51, 50, "x" * 49 + "\u2026"),
        ("x" * 48 + " " + "y" * 10, 50, "x" * 48 + "\u2026"),
        ("x" * 23 + ". " + "y" * 40, 50,
         "x" * 23 + ". " + "y" * 24 + "\u2026"),
        ("x" * 24 + ". " + "y" * 40, 50, "x" * 24 + "."),
        ("x" * 48 + ". " + "y" * 40, 50, "x" * 48 + "."),
        ("x" * 49 + ". " + "y" * 40, 50, "x" * 49 + "."),
        ("x" * 50 + ". " + "y" * 40, 50, "x" * 49 + "\u2026"),
        ("x" * 25 + "." + "y" * 40, 50,
         "x" * 25 + "." + "y" * 23 + "\u2026"),
        ("x" * 24 + ": " + "y" * 40, 50,
         "x" * 24 + ": " + "y" * 23 + "\u2026"),
        ("x" * 24 + "; " + "y" * 40, 50, "x" * 24 + "."),
        ("x" * 24 + ". " + "z" * 10 + "! " + "y" * 40,
         50, "x" * 24 + ". " + "z" * 10 + "!"),
        ("x" * 24 + ". " + "y" * 40, 51, "x" * 24 + "."),
    ],
)
def test_summary_display_sentence_boundaries_and_hard_cut(text, cap, expected):
    # Messaging 4.5: cap+1 lookahead, floor(cap/2), final sentence end.
    assert messaging.shown(text, cap) == expected


@pytest.mark.parametrize("mark", (".", "!", "?", ";"))
@pytest.mark.parametrize("closing", ("", '"', "'", "\u201d", "\u2019", ")"))
def test_sentence_marks_and_optional_closing_character(mark, closing):
    # Messaging 4.5: semicolon changes to a period, preserving its closer.
    text = "x" * 30 + mark + closing + " " + "y" * 50
    expected = "x" * 30 + ("." if mark == ";" else mark) + closing
    assert messaging.shown(text, 50) == expected


@pytest.mark.parametrize("closing", ("]", "}", "\u201c", "\u2018", '")'))
def test_unlisted_or_multiple_closers_do_not_make_sentence_end(closing):
    # Messaging 4.5: exactly zero or one listed closer, followed by space.
    text = "x" * 30 + "." + closing + " " + "y" * 50
    expected = text[:49].rstrip() + "\u2026"
    assert messaging.shown(text, 50) == expected


def test_summary_display_worked_examples():
    # Messaging 4.5's five cap-2000 worked examples.
    assert messaging.shown("A. " * 900, 2000) == "A. " * 666 + "A."
    assert messaging.shown("word " * 900, 2000) == "word " * 399 + "word\u2026"
    assert messaging.shown("x" * 1500 + "; " + "y" * 600, 2000) == "x" * 1500 + "."
    assert messaging.shown("x" * 999 + ". " + "y" * 1500, 2000) == "x" * 999 + "."
    assert messaging.shown("x" * 998 + ". " + "y" * 1500, 2000) == (
        "x" * 998 + ". " + "y" * 999 + "\u2026"
    )


def test_display_cut_does_not_change_stored_summary():
    # Messaging 4.3, 4.5: stored text remains available uncut for updates.
    m = market(recent=2, summary_chars=50)
    fill(m, 3)
    text = "x" * 100
    render(m, backend=Script(models.Reply(text=text)), tracker=Tracker())
    assert m.summaries["everyone"] == (text, 2)
    output = render(m, backend=Script(), tracker=Tracker(stop_at=1))
    assert ": " + "x" * 49 + "\u2026" in output
    assert m.summaries["everyone"] == (text, 2)


def test_defaults_and_messaging_ranges():
    # Messaging 2.5, 4.1, 4.5; config 3.13.
    cfg = config.resolve({})
    assert cfg["message_channels"] == "off"
    assert cfg["messaging"] == {
        "message_chars": 500, "recent": 10, "summary_chars": 2000
    }
    for field, minimum in (("message_chars", 1), ("recent", 2), ("summary_chars", 50)):
        for valid in (minimum, minimum + 1, 1000000):
            assert config.resolve({"messaging": {field: valid}})["messaging"][field] == valid
        for invalid in (minimum - 1, -1, 0.5, True, "50"):
            with pytest.raises(config.ConfigError) as caught:
                config.resolve({"messaging": {field: invalid}})
            assert "messaging." + field in str(caught.value)


def reconstruct_section(expected, mode, ids, pid):
    """Build a fixture from published view text, not from implementation output."""
    m = market(mode, ids=ids)
    lines = expected.splitlines()
    key = None
    newly_seen = []
    for line in lines[1:]:
        if not line:
            continue
        if line.startswith("Your conversation with "):
            other = line.split("Your conversation with ", 1)[1].split(":", 1)[0]
            key = pair(pid, other)
            continue
        if line.startswith("Conversation among all "):
            heading = line.split(":", 1)[0]
            key = next(k for k, v in GROUP_HEADINGS.items() if v == heading)
            continue
        if line.startswith("  Summary of the "):
            count = int(line.split("  Summary of the ", 1)[1].split(" ", 1)[0])
            summary = line.split("): ", 1)[1]
            m.summaries[key] = (summary, count)
            if isinstance(key, tuple):
                to = key[0] if key[1] == pid else key[1]
            else:
                to = key
            for _ in range(count):
                add(m, pid, to, "earlier message already summarized")
            continue
        match = re.fullmatch(
            "  \\[day ([0-9]+), round ([0-9]+)\\] (\\S+) -> "
            "(.*?)( \\(NEW\\))?: (.*)", line,
        )
        assert match is not None, line
        day, round_, sender, destination, new, text = match.groups()
        sender = pid if sender == "you" else sender
        destination = pid if destination == "you" else {
            "all managers": "managers", "all buyers": "buyers"
        }.get(destination, destination)
        index = add(m, sender, destination, text, int(day), int(round_))
        if new:
            newly_seen.append(index)
    m.participants[pid].seen = len(m.messages)
    m.participants[pid].unseen = newly_seen
    return m


def test_exact_section_32_worked_example():
    # Messaging 3.2's full section, including spaces and blank lines.
    spec = (SPEC / "messaging.md").read_text(encoding="utf-8")
    sample = fence_after(spec, "Example (mode `managers`")
    ending = "\n\n--- Your accounts so far (credits) ---"
    assert sample.endswith(ending)
    expected = sample[:-len(ending)]
    m = reconstruct_section(expected, "managers", IDS, "M-14b")
    assert render(m, backend=Script(), tracker=Tracker(stop_at=1)) + ending == sample


@pytest.mark.parametrize(
    "filename,ids,pid",
    [
        (
            "view_manager_all_M-7f8_day28_round3.txt",
            ("B-82c", "B-b79", "M-0ed", "M-ee6", "M-7f8"),
            "M-7f8",
        ),
        (
            "view_buyer_all_B-42c_day23_round2.txt",
            ("B-79d", "B-42c", "M-bd6", "M-f2b", "M-218"),
            "B-42c",
        ),
    ],
)
def test_published_complete_view_message_sections(filename, ids, pid):
    # Messaging 3.2; both published complete-view examples.
    text = (SPEC / "examples" / filename).read_text(encoding="utf-8")
    start = text.index("--- Messages ---")
    end = text.index("\n\n--- Your accounts so far (credits) ---", start)
    expected = text[start:end]
    m = reconstruct_section(expected, "all", ids, pid)
    assert render(m, pid, Script(), Tracker(stop_at=1)) == expected


def fake_http(monkeypatch, bodies):
    """Exercise the public model backend without sockets, timers or sleeps."""
    pending = list(bodies)
    sent = []

    class Response:
        def __init__(self, status, body):
            self.status = status
            self.body = body

        def read(self):
            return json.dumps(self.body).encode("ascii")

    class Connection:
        def __init__(self, *args, **kwargs):
            self.sock = None

        def connect(self):
            pass

        def request(self, method, path, body, headers):
            sent.append((method, path, json.loads(body), headers))

        def getresponse(self):
            assert pending, "Unexpected HTTP attempt"
            status, body = pending.pop(0)
            return Response(status, body)

        def close(self):
            pass

    class Timer:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(models.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(models.http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(models.threading, "Timer", Timer)
    return sent


def response(text="summary", cost=0.125):
    return {
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 3, "cost": cost},
    }


def test_default_summarizer_wire_request_and_cost(monkeypatch):
    # Messaging 5.2; models 6 and 4.2's default summarizer example.
    m = market(recent=2)
    fill(m, 3)
    sent = fake_http(monkeypatch, [(200, response())])
    tracker = models.CostTracker(None)
    backend = models.backend(
        m.cfg["models"]["summarizer"], m.cfg["llm"], tracker,
        env={"OPENROUTER_API_KEY": "test-key"}, sleep=lambda _: None,
    )
    before = financial_state(m)
    render(m, backend=backend, tracker=tracker)
    assert len(sent) == 1
    method, path, body, headers = sent[0]
    assert method == "POST"
    assert path == "/api/v1/chat/completions"
    assert body == {
        "model": "anthropic/claude-sonnet-5.5",
        "messages": expected_request("everyone", m.messages[:2]),
        "max_tokens": 8000,
        "usage": {"include": True},
        "reasoning": {"effort": "high"},
    }
    assert headers["Authorization"] == "Bearer test-key"
    assert tracker.total == 0.125
    assert m.counts["summarizer_dollars"] == 0.125
    assert financial_state(m) == before


@pytest.mark.parametrize(
    "extra_spec,expected_temperature,expected_limit,extra",
    [
        ({}, None, 1234, {}),
        ({"temperature": None}, None, 1234, {}),
        ({"temperature": 0}, 0, 1234, {}),
        ({"temperature": 1.25, "max_tokens": 321}, 1.25, 321, {}),
        (
            {"temperature": 0.5, "max_tokens": 321,
             "extra_body": {"temperature": 0.25, "max_tokens": 99,
                            "reasoning": {"effort": "low"}}},
            0.25, 99, {"reasoning": {"effort": "low"}},
        ),
    ],
)
def test_summarizer_own_settings_and_extra_body_last(
    monkeypatch, extra_spec, expected_temperature, expected_limit, extra
):
    # Models 6; config 3.4.1: agent temperature does not leak into summarizer.
    cfg = config.resolve({
        "message_channels": "all",
        "llm": {"temperature": 1.75, "max_tokens": 1234},
        "models": {"summarizer": {
            "provider": "openrouter", "model": "test-model", **extra_spec
        }},
    })
    m = state.Market(seed=0, cfg=cfg)
    for pid in IDS:
        state.add_participant(m, pid, "buyer" if pid.startswith("B-") else "manager", 1)
    fill(m, 11)
    sent = fake_http(monkeypatch, [(200, response())])
    tracker = models.CostTracker(None)
    backend = models.backend(
        cfg["models"]["summarizer"], cfg["llm"], tracker,
        env={"OPENROUTER_API_KEY": "test-key"}, sleep=lambda _: None,
    )
    render(m, backend=backend, tracker=tracker)
    body = sent[0][2]
    assert body["max_tokens"] == expected_limit
    if expected_temperature is None:
        assert "temperature" not in body
    else:
        assert body["temperature"] == expected_temperature
    assert "tools" not in body and "tool_choice" not in body
    assert "seed" not in body and "top_p" not in body
    for key, value in extra.items():
        assert body[key] == value


def test_model_layer_retry_is_inside_one_summary_attempt(monkeypatch):
    # Models 6, 4.4; messaging 5.3: backend retries precede summary retries.
    m = market(recent=2)
    fill(m, 3)
    sent = fake_http(monkeypatch, [
        (429, {"error": "busy", "usage": {"cost": 0.25}}),
        (200, response(cost=0.125)),
    ])
    tracker = models.CostTracker(None)
    backend = models.backend(
        m.cfg["models"]["summarizer"], m.cfg["llm"], tracker,
        env={"OPENROUTER_API_KEY": "test-key"}, sleep=lambda _: None,
    )
    records = []
    m.log = records.append
    render(m, backend=backend, tracker=tracker)
    assert len(sent) == 2
    assert sent[0][2] == sent[1][2]
    assert m.counts["summarizer_calls"] == 1
    assert m.counts.get("summaries_failed", 0) == 0
    assert m.counts["summarizer_dollars"] == 0.125
    assert tracker.total == 0.375
    assert records_of(records, "summarizer_call")[0]["reply"]["failed_cost"] == 0.25


def test_summarizer_computed_cost_is_unrounded(monkeypatch):
    # Models 6, 4.9: 100 and 3 tokens at 0.15 and 1.875 => 2.0625e-05.
    cfg = config.resolve({
        "message_channels": "all",
        "models": {"summarizer": {
            "provider": "openrouter", "model": "test-model",
            "price_in_per_m": 0.15, "price_out_per_m": 1.875,
        }},
    })
    m = state.Market(seed=0, cfg=cfg)
    for pid in IDS:
        state.add_participant(m, pid, "buyer" if pid.startswith("B-") else "manager", 1)
    fill(m, 11)
    body = response()
    del body["usage"]["cost"]
    fake_http(monkeypatch, [(200, body)])
    tracker = models.CostTracker(None)
    backend = models.backend(
        cfg["models"]["summarizer"], cfg["llm"], tracker,
        env={"OPENROUTER_API_KEY": "test-key"}, sleep=lambda _: None,
    )
    before = financial_state(m)
    render(m, backend=backend, tracker=tracker)
    assert m.counts["summarizer_dollars"] == 2.0625e-05
    assert tracker.total == 2.0625e-05
    assert financial_state(m) == before
