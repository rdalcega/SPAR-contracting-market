"""The view's frame and general sections (market/view.py) against spec/view.md: every line form and every example
of the spec, from markets built by hand; what building a view changes and what a lost turn undoes.

The market sections and the Messages section are other modules' (view_contracts.py, messaging.py): the tests here
give the frame fixed stand-ins for them, except the tests that ask for `real`, which run the frame on those
modules.
"""

import copy
import pathlib
import re

import pytest

from market import config, state, turns, view
from market.state import Contract, Delivery, Dispute, Market, Memory, Message, Payment, Result, Task

SPEC = pathlib.Path(__file__).parent.parent / "spec"
IDS = ("B-001", "B-002", "M-001", "M-002")
RESULTS = "--- Results of your actions in your previous turn ---"
NOTIFICATIONS = "--- Notifications since your last turn ---"
PAYMENTS = "--- Payments (yours and to you; the latest 8 are shown) ---"
ACCOUNTS = "--- Your accounts so far (credits) ---"
RECORD = "--- Your record with each counterparty (all your contracts so far) ---"
MEMORY = "--- Your self-written memory ---"
FINAL = "--- Final instructions ---"
LAST = "Decide what to do now. compute can do the arithmetic for you. End your turn by calling act."
MARKET = {
    "buyer": ["--- Your tasks not yet contracted ---", "--- Your active contracts ---",
              "--- Your finished contracts ---", "--- Other buyers' open posts (for reference) ---"],
    "manager": ["--- Open posts on the forum ---", "--- Your production ---", "--- Your active contracts ---",
                "--- Your finished contracts ---"],
}
MESSAGES = {  # 2.2: mode -> the title in a buyer's view, in a manager's view
    "off": (None, None),
    "managers": (None, "--- Messages between managers ---"),
    "buyers": ("--- Messages between buyers ---", None),
    "within_roles": ("--- Messages between buyers ---", "--- Messages between managers ---"),
    "all": ("--- Messages ---", "--- Messages ---"),
}
HEAD = "Results of your actions:"
NO_MEMORY = "(note: your reply had no memory; your memory is unchanged)"


class Stand:
    """What the stand-ins give: the market sections (None: the role's titles alone) and the Messages section."""

    def __init__(self):
        self.market = None
        self.messages = ("--- Messages ---", ["Conversation among all participants: (no messages yet)"])
        self.calls = []


@pytest.fixture(autouse=True)
def stand(request, monkeypatch):
    s = Stand()
    if "real" in request.fixturenames:
        return s

    def section(m, p, summarizer, tracker):
        s.calls.append({"pid": p.id, "turns": p.turns, "pending": list(p.pending), "summarizer": summarizer,
                        "tracker": tracker})
        p.seen, p.unseen = len(m.messages), []
        return s.messages

    monkeypatch.setattr(view.view_contracts, "sections",
                        lambda m, p: s.market or [(title, []) for title in MARKET[p.role]])
    monkeypatch.setattr(view.messaging, "section", section)
    return s


@pytest.fixture
def real():
    """Asked for by a test that runs the frame on view_contracts.py and messaging.py themselves."""


def market(mode="off", day=3, rnd=2, ids=IDS, **overrides):
    """Buyers and managers at share 1 (balance 60), in seat order as given, at the start of a round."""
    m = Market(seed=7, cfg=config.resolve({"message_channels": mode, **overrides}))
    for pid in ids:
        state.add_participant(m, pid, "buyer" if pid.startswith("B") else "manager")
    m.day, m.round = day, rnd
    return m


def contract(m, cid, form="plain", price=12.0, buyer="B-001", manager="M-001", deadline=9, **fields):
    tid = cid.replace("contract", "task")
    m.tasks[tid] = Task(tid, buyer, 60, deadline, state="contracted", contract=cid)
    c = m.contracts[cid] = Contract(cid, tid, cid.replace("contract", "post"), buyer, manager, form, price,
                                    5.0 if form == "escrow" else None, 1, deadline)
    for name, value in fields.items():
        setattr(c, name, value)
    return c


def deliver(c, label, day, readings=(), **fields):
    """A delivery (plain) or a submission (escrow) of a new result, with the buyer's readings of it."""
    c.results.append(Result(label, "employee", "fresh", None, 1, 2, 6.0, buyer_readings=list(readings)))
    c.deliveries.append(Delivery(label, day, 1, **fields))
    return c.deliveries[-1]


def pay(m, day, rnd, payer, payee, amount, cid=None, to_price=None):
    """A payment; it counts in full toward the price of the contract it is on unless `to_price` says less."""
    part = (amount if to_price is None else to_price) if cid else 0.0
    m.payments.append(Payment(day, rnd, payer, payee, amount, cid, part, False))
    if cid:
        m.contracts[cid].paid = round(m.contracts[cid].paid + part, 4)


def parts(text):
    """A view as its two header lines and its sections `(title, content)`; a title alone has content ""."""
    head, *rest = re.split(r"\n\n(?=--- [^\n]* ---(?:\n|$))", text)
    return head, [tuple((s.split("\n\n", 1) + [""])[:2]) for s in rest]


def content(text, title):
    """The content of the section whose title starts so."""
    return next(body for name, body in parts(text)[1] if name.startswith(title))


def record(m, pid):
    """The record section's items in a participant's view."""
    body = content(view.render(m, pid, []), RECORD)
    return body.split("\n\n") if body else []


def blocks(name):
    """The fenced blocks of a spec file, each without its fence's indent."""
    found = re.findall(r"^( *)```\w*\n(.*?)\n\1```$", (SPEC / name).read_text(encoding="utf-8"), re.S | re.M)
    return ["\n".join(line[len(indent):] for line in text.split("\n")) for indent, text in found]


def example(name):
    return (SPEC / "examples" / name).read_text(encoding="utf-8")


def template(start, **fields):
    """The template block of view.md that starts so, with its placeholders filled."""
    text = next(b for b in blocks("view.md") if b.startswith(start))
    for name, value in fields.items():
        text = text.replace("{" + name.replace("_minus_", " - ") + "}", str(value))
    assert "{" not in text
    return text


def use_example(stand, text, messages):
    """The stand-ins give an example view's own market sections and, with `messages`, its Messages section."""
    sections = [(title, body.split("\n\n") if body else []) for title, body in parts(text)[1]]
    if messages:
        stand.messages = sections.pop(3)
    stand.market = sections[5:9]


# 2.1 The header


def header(m, pid):
    return view.render(m, pid, []).split("\n")[:3]


def test_header_lines():
    m = market()
    assert header(m, "B-001") == [
        "=== DAY 3, ROUND 2 of 3 today === You are B-001 (buyer). Your balance: 60.00 credits.",
        "Participants - buyers: B-001, B-002; managers: M-001, M-002.",
        "",
    ]
    assert header(m, "M-002")[0] == (
        "=== DAY 3, ROUND 2 of 3 today === You are M-002 (manager). Your balance: 60.00 credits.")


def test_header_is_the_template_of_2_1():
    m = market(day=12, rnd=3, ids=("B-44c", "B-204", "M-829", "M-3c5", "M-fda"))
    m.participants["M-3c5"].balance = 163.2
    assert header(m, "M-3c5")[:2] == template(
        "=== DAY {day}", day=12, round=3, rounds_per_day=3, id="M-3c5", role="manager", balance="163.20",
        buyer_ids="B-44c, B-204", manager_ids="M-829, M-3c5, M-fda").split("\n")


def test_header_participants_in_seat_order_not_sorted_with_those_out_of_business():
    m = market(ids=("B-44c", "B-204", "M-829", "M-3c5", "M-fda"))
    m.participants["M-3c5"].out_of_business = True
    for pid in m.participants:
        assert header(m, pid)[1] == "Participants - buyers: B-44c, B-204; managers: M-829, M-3c5, M-fda."


@pytest.mark.parametrize("balance, shown", [
    (163.2, "163.20"), (-3.5, "-3.50"), (-0.004, "-0.00"), (0.0, "0.00"), (-0.0, "-0.00"), (1.0, "1.00"),
    (0.125, "0.12"), (1234567.891, "1234567.89"), (141.484999, "141.48"),
])
def test_header_balance_keeps_its_sign_and_credits_is_plural(balance, shown):
    m = market()
    m.participants["M-001"].balance = balance
    assert header(m, "M-001")[0].endswith(f"You are M-001 (manager). Your balance: {shown} credits.")


def test_header_day_round_and_rounds_per_day():
    m = market(day=28, rnd=3)
    assert header(m, "B-001")[0].startswith("=== DAY 28, ROUND 3 of 3 today === ")
    m = market(day=1, rnd=1, time={"rounds_per_day": 1})
    assert header(m, "B-001")[0].startswith("=== DAY 1, ROUND 1 of 1 today === ")
    m = market(day=12, rnd=5, time={"rounds_per_day": 7})
    assert header(m, "B-001")[0].startswith("=== DAY 12, ROUND 5 of 7 today === ")


# 2.2, 2.3 The sections, their order and the empty lines

FIRST_BUYER = """\
=== DAY 1, ROUND 1 of 3 today === You are B-001 (buyer). Your balance: 60.00 credits.
Participants - buyers: B-001, B-002; managers: M-001, M-002.

--- Results of your actions in your previous turn ---

--- Notifications since your last turn ---

--- Payments (yours and to you; the latest 8 are shown) ---

--- Your accounts so far (credits) ---

Spent: nothing.

Received: nothing.

Held in escrow now: 0.00.

--- Your record with each counterparty (all your contracts so far) ---

--- Your tasks not yet contracted ---

--- Your active contracts ---

--- Your finished contracts ---

--- Other buyers' open posts (for reference) ---

--- Your self-written memory ---

--- Final instructions ---

Decide what to do now. compute can do the arithmetic for you. End your turn by calling act."""

FIRST_MANAGER = """\
=== DAY 1, ROUND 1 of 3 today === You are M-001 (manager). Your balance: 60.00 credits.
Participants - buyers: B-001, B-002; managers: M-001, M-002.

--- Results of your actions in your previous turn ---

--- Notifications since your last turn ---

--- Payments (yours and to you; the latest 8 are shown) ---

--- Your accounts so far (credits) ---

Spent: nothing.

Received: nothing.

--- Your record with each counterparty (all your contracts so far) ---

--- Open posts on the forum ---

--- Your production ---

--- Your active contracts ---

--- Your finished contracts ---

--- Your self-written memory ---

--- Final instructions ---

Decide what to do now. compute can do the arithmetic for you. End your turn by calling act."""


def test_first_view_of_each_role_is_titles_alone():
    m = market(day=1, rnd=1)
    assert view.build(m, "B-001")[0] == FIRST_BUYER
    assert view.build(m, "M-001")[0] == FIRST_MANAGER


@pytest.mark.parametrize("mode", list(MESSAGES))
@pytest.mark.parametrize("role", ["buyer", "manager"])
def test_sections_in_order_with_the_messages_section_by_mode(stand, mode, role):
    m = market(mode)
    pid = "B-001" if role == "buyer" else "M-001"
    title = MESSAGES[mode][role == "manager"]
    stand.messages = (title, ["Your conversation with X-1: (no messages yet)", "Conversation: (no messages yet)"])
    text = view.build(m, pid, "the summarizer", "the tracker")[0]
    general = [RESULTS, NOTIFICATIONS, PAYMENTS, ACCOUNTS, RECORD]
    if title:
        general.insert(3, title)
    titles = [name for name, _ in parts(text)[1]]
    assert titles == general + MARKET[role] + [MEMORY, FINAL]
    assert len(titles) == (12 if title else 11)
    assert len(stand.calls) == (1 if title else 0)
    if title:
        assert stand.calls[0]["summarizer"] == "the summarizer" and stand.calls[0]["tracker"] == "the tracker"
        assert (f"\n\n{PAYMENTS}\n\n{title}\n\nYour conversation with X-1: (no messages yet)\n\n"
                f"Conversation: (no messages yet)\n\n{ACCOUNTS}\n\n") in text
    else:
        assert "Messages" not in text


def test_render_has_a_messages_section_only_when_given_one():
    m = market("all")
    assert [name for name, _ in parts(view.render(m, "B-001", []))[1]][2:4] == [PAYMENTS, ACCOUNTS]
    text = view.render(m, "B-001", [], ("--- Messages ---", ["a: (no messages yet)"]))
    assert f"\n\n{PAYMENTS}\n\n--- Messages ---\n\na: (no messages yet)\n\n{ACCOUNTS}\n\n" in text


def test_items_one_empty_line_apart_and_sub_lines_with_their_line(stand):
    m = market()
    p = m.participants["M-001"]
    p.results = [HEAD, "- bid: OK: placed bid-1", "- bid: ERROR: no"]
    p.turns, p.memory = 1, Memory("line one\n\n\nline five\n  indented", 3, 1, 0, False)
    pay(m, 2, 1, "B-001", "M-001", 5.0)
    pay(m, 2, 2, "M-001", "B-002", 1.0)
    stand.market = [
        ("--- Open posts on the forum ---", ["(1 more open post not listed: post-9)", "post-1 by B-001 | task-1"]),
        ("--- Your production ---", ["Expected run costs.", "You (run_myself): free."]),
        ("--- Your active contracts ---", ["contract-1 with B-001 | task-1\n    R-1 | you\n    R-2 | you",
                                           "contract-2 with B-002 | task-2"]),
        ("--- Your finished contracts (latest 1 of 1) ---", ["contract-3 with B-001 | task-3"]),
    ]
    assert view.render(m, "M-001", ["[day 3] one", "[day 3, round 1] two"]) == """\
=== DAY 3, ROUND 2 of 3 today === You are M-001 (manager). Your balance: 60.00 credits.
Participants - buyers: B-001, B-002; managers: M-001, M-002.

--- Results of your actions in your previous turn ---

Results of your actions:
- bid: OK: placed bid-1

- bid: ERROR: no

--- Notifications since your last turn ---

[day 3] one

[day 3, round 1] two

--- Payments (yours and to you; the latest 8 are shown) ---

[day 2, round 1] B-001 -> you: 5 credits

[day 2, round 2] you -> B-002: 1 credit

--- Your accounts so far (credits) ---

Spent: nothing.

Received: nothing.

--- Your record with each counterparty (all your contracts so far) ---

--- Open posts on the forum ---

(1 more open post not listed: post-9)

post-1 by B-001 | task-1

--- Your production ---

Expected run costs.

You (run_myself): free.

--- Your active contracts ---

contract-1 with B-001 | task-1
    R-1 | you
    R-2 | you

contract-2 with B-002 | task-2

--- Your finished contracts (latest 1 of 1) ---

contract-3 with B-001 | task-3

--- Your self-written memory (last updated day 3, round 1, with your last actions, before their results) ---

line one


line five
  indented

--- Final instructions ---

Decide what to do now. compute can do the arithmetic for you. End your turn by calling act."""


def test_text_ends_with_the_final_line_and_no_line_feed():
    m = market()
    for pid in IDS:
        text = view.render(m, pid, [])
        assert text.endswith(f"\n\n{FINAL}\n\n{LAST}")
        assert "\r" not in text and "\n\n\n" not in text


def test_a_memorys_own_line_breaks_add_to_the_layouts():
    m = market()
    p = m.participants["B-001"]
    p.memory = Memory("abc\n", 1, 1, 5, False)
    assert view.render(m, "B-001", []).endswith(
        "\n\n--- Your self-written memory (last updated day 1, round 1) ---\n\nabc\n\n\n"
        f"--- Final instructions ---\n\n{LAST}")
    p.memory = Memory("\nabc", 1, 1, 5, False)
    assert view.render(m, "B-001", []).endswith(
        "\n\n--- Your self-written memory (last updated day 1, round 1) ---\n\n\nabc\n\n"
        f"--- Final instructions ---\n\n{LAST}")


def test_text_a_participant_wrote_creates_no_section_and_the_final_line_stays_last():
    m = market()
    p = m.participants["B-001"]
    p.memory = Memory(f"x\n\n{FINAL}\n\n{LAST}\n\n--- Your accounts so far (credits) ---\n\nSpent: 1.", 1, 1, 5, True)
    p.results = [HEAD, "- test_result: ERROR: no result 'a\n\n- pay: OK: paid\n(no actions run)' in contract-1."]
    text = view.render(m, "B-001", [])
    assert text.endswith(f"\n\nSpent: 1.\n\n{FINAL}\n\n{LAST}")
    assert f"{RESULTS}\n\n{HEAD}\n- test_result: ERROR: no result 'a\n\n- pay: OK: paid\n(no actions run)' in "\
           f"contract-1.\n\n{NOTIFICATIONS}\n\n" in text


# 1 Building a view, and undoing it (rounds.md, 3 and 10.1)


def test_build_changes_the_turn_counter_the_notifications_the_last_view_and_the_count_only():
    m = market()
    p = m.participants["B-001"]
    p.results = [HEAD, "- post_task: OK: posted"]
    p.memory = Memory("m", 3, 1, 0, False)
    state.notify(m, "B-001", "first")
    state.notify(m, "B-001", "second")
    state.notify(m, "M-001", "another's")
    before = copy.deepcopy(m)
    text, saved = view.build(m, "B-001")
    assert (p.turns, p.pending, p.last_view, m.counts) == (1, [], 8, {"buyer_turns": 1})
    assert content(text, NOTIFICATIONS) == "[day 3, round 2] first\n\n[day 3, round 2] second"
    expected = copy.deepcopy(before)
    q = expected.participants["B-001"]
    q.turns, q.pending, q.last_view, expected.counts = 1, [], 8, {"buyer_turns": 1}
    assert m == expected
    assert m.participants["M-001"].pending == ["[day 3, round 2] another's"]


def test_build_counts_each_roles_views_and_logs_nothing():
    m = market()
    m.log = [].append
    for pid in ("B-001", "M-001", "M-002", "B-001"):
        view.build(m, pid)
    assert m.counts == {"buyer_turns": 2, "manager_turns": 2}
    assert m.participants["B-001"].turns == 2 and m.participants["B-002"].turns == 0
    assert m.log.__self__ == [] and m.records == 0


def test_build_order_counter_then_notifications_then_messages(stand):
    m = market("all")
    m.messages.append(Message(3, 1, "M-001", "B-001", "hello"))
    state.notify(m, "B-001", "note")
    text, _ = view.build(m, "B-001", "S", "T")
    assert stand.calls == [{"pid": "B-001", "turns": 1, "pending": [], "summarizer": "S", "tracker": "T"}]
    assert content(text, NOTIFICATIONS) == "[day 3, round 2] note"
    assert m.participants["B-001"].seen == 1


def test_render_changes_nothing():
    m = market("all")
    state.notify(m, "B-001", "note")
    contract(m, "contract-1", "escrow")
    pay(m, 1, 1, "B-001", "M-001", 3.0)
    before = copy.deepcopy(m)
    for pid in IDS:
        view.render(m, pid, ["[day 1] x"], ("--- Messages ---", ["c: (no messages yet)"]))
    assert m == before


def test_undo_restores_what_build_changed_with_the_notifications_shown_ahead_of_those_since():
    m = market("all")
    p = m.participants["M-001"]
    p.results = [HEAD, "- bid: OK: placed"]
    p.memory = Memory("m", 3, 1, 4, False)
    p.turns, p.last_view, p.seen, p.unseen = 4, 7, 3, [1]
    m.messages += [Message(1, 1, "B-001", "M-001", f"m{i}") for i in range(5)]
    state.notify(m, "M-001", "shown one")
    state.notify(m, "M-001", "shown two")
    before = copy.deepcopy(m)
    text, saved = view.build(m, "M-001")
    assert (p.turns, p.pending, p.last_view, p.seen, p.unseen) == (5, [], 8, 5, [])
    state.notify(m, "M-001", "since")
    m.messages.append(Message(3, 2, "B-001", "M-001", "since"))
    view.undo(m, "M-001", saved)
    expected = copy.deepcopy(before)
    expected.participants["M-001"].pending.append("[day 3, round 2] since")
    expected.messages.append(Message(3, 2, "B-001", "M-001", "since"))
    expected.counts = {"manager_turns": 1}
    assert m == expected
    assert p.pending == ["[day 3, round 2] shown one", "[day 3, round 2] shown two", "[day 3, round 2] since"]


def test_the_view_after_a_lost_turn_shows_the_same_results_notifications_and_memory_title():
    m = market()
    p = m.participants["B-001"]
    p.results = [HEAD, "- post_task: OK: posted task-1 as post-1"]
    p.memory = Memory("m", 3, 1, 1, False)
    p.turns = 1
    state.notify(m, "B-001", "one")
    lost, saved = view.build(m, "B-001")
    view.undo(m, "B-001", saved)
    m.round = 3
    state.notify(m, "B-001", "two")
    text, _ = view.build(m, "B-001")
    assert content(text, RESULTS) == content(lost, RESULTS) == f"{HEAD}\n- post_task: OK: posted task-1 as post-1"
    assert content(text, NOTIFICATIONS) == "[day 3, round 2] one\n\n[day 3, round 3] two"
    title = "--- Your self-written memory (last updated day 3, round 1, with your last actions, before their results)"
    assert title in lost and title in text
    assert (p.turns, p.last_view, m.counts) == (2, 9, {"buyer_turns": 2})


def test_undo_twice_in_a_row_and_a_turn_lost_after_its_own_notices():
    m = market()
    p = m.participants["M-001"]
    state.notify(m, "M-001", "a")
    _, saved = view.build(m, "M-001")
    state.notify(m, "M-001", state.OUT_OF_BUSINESS_NOTICE)
    view.undo(m, "M-001", saved)
    _, saved = view.build(m, "M-001")
    view.undo(m, "M-001", saved)
    assert p.pending == ["[day 3, round 2] a", f"[day 3, round 2] {state.OUT_OF_BUSINESS_NOTICE}"]
    assert (p.turns, p.last_view) == (0, 0)


def test_undo_makes_the_new_messages_new_again(real):
    m = market("all")
    m.messages.append(Message(3, 1, "M-001", "B-001", "hello"))
    lost, saved = view.build(m, "B-001")
    line = "Your conversation with M-001:\n  [day 3, round 1] M-001 -> you (NEW): hello"
    assert line in lost
    assert view.build(m, "B-001")[0].count("(NEW)") == 0
    m.participants["B-001"].seen = 0
    _, saved = view.build(m, "B-001")
    view.undo(m, "B-001", saved)
    assert (m.participants["B-001"].seen, m.participants["B-001"].unseen) == (0, [])
    assert line in view.build(m, "B-001")[0]


def test_undo_keeps_a_summary_written_while_the_view_was_built(real):
    m = market("all")
    p = m.participants["B-001"]
    m.messages += [Message(1, 1, "M-001", "B-001", f"m{i}") for i in range(m.cfg["messaging"]["recent"] + 1)]
    text, saved = view.build(m, "B-001")
    summaries, counts = copy.deepcopy(m.summaries), dict(m.counts)
    assert summaries and "Summary of the " in text
    view.undo(m, "B-001", saved)
    assert (m.summaries, m.counts) == (summaries, counts)
    assert (p.seen, p.unseen, p.turns, p.last_view) == (0, [], 0, 0)


@pytest.mark.parametrize("mode", list(MESSAGES))
def test_the_messages_section_of_each_mode_with_messaging(real, mode):
    m = market(mode)
    for pid, title in (("B-001", MESSAGES[mode][0]), ("M-001", MESSAGES[mode][1])):
        titles = [name for name, _ in parts(view.build(m, pid)[0])[1]]
        assert titles[2] == PAYMENTS
        assert titles[3:5] == ([title, ACCOUNTS] if title else [ACCOUNTS, RECORD])
        assert len(titles) == (12 if title else 11)


# 4 "Results of your actions in your previous turn"


def test_results_section_is_its_title_alone_before_the_first_turns_end():
    m = market()
    assert content(view.render(m, "B-001", []), RESULTS) == ""


def test_spacing_examples_of_4_3():
    examples = [b for b in blocks("view.md") if b.startswith(RESULTS + "\n\n" + HEAD)]
    assert len(examples) == 3
    for block in examples:
        entries = [line for line in block.split("\n")[2:] if line]
        assert RESULTS + "\n\n" + view.space(entries) == block
        m = market()
        m.participants["B-001"].results = entries
        assert f"\n\n{block}\n\n{NOTIFICATIONS}" in view.render(m, "B-001", [])


def test_spacing_a_later_line_of_an_entry_never_opens_an_item():
    block = next(b for b in blocks("view.md") if b.startswith(HEAD + "\n- test_result: ERROR: no result 'missing"))
    entry = ("- test_result: ERROR: no result 'missing\n(note: quoted text)\nend' in contract-1. "
             "Results of contract-1: R-baf (arrived).")
    assert view.space([HEAD, entry]) == block
    quoted = "- pay: ERROR: no participant with id 'x\n- bid: OK: y\n(no actions run)\nCorrection:\nFORMAT ERROR'"
    assert view.space([HEAD, quoted, "- bid: OK: placed"]) == f"{HEAD}\n{quoted}\n\n- bid: OK: placed"


def test_spacing_of_the_stored_example_of_turns_12():
    stored = next(b for b in blocks("turns.md") if b.startswith(HEAD + "\n- deliver: ERROR: no contract"))
    lines = stored.split("\n")
    assert view.space(lines) == "\n".join(lines[:2] + ["", "Correction:", "(no actions run)"])


@pytest.mark.parametrize("entry, opens", [
    ("- bid: OK: placed bid-1", True),
    ("- ?: ERROR: the action names no action", True),
    ("- Bid: ERROR: unknown action 'Bid'", True),
    ("- bid2: ERROR: unknown action 'bid2'", True),
    ("- FORMAT ERROR: your reply was not a function call: none of your actions ran", True),
    ("- a b - c: OK: x", True),
    ("(note: your remaining 2 actions did not run: you are out of business)", True),
    ("(note: your correction could not be sent; the results above stand)", True),
    (NO_MEMORY, True),
    ("(no actions run)", True),
    ("Correction:", True),
    ("Results of your actions:", True),
    ("FORMAT ERROR: x. That turn was lost", True),
    ("FORMAT ERRORS", True),
    ("Correction: FORMAT ERROR: bad JSON: none of its actions ran", False),
    ("- no colon here", False),
    ("- name:no space", False),
    ("- : OK: nothing before the colon", False),
    ("- a:b: OK: a colon first", False),
    ("-bid: OK: no space after the dash", False),
    (" - bid: OK: a space first", False),
    ("(note)", False),
    ("(note:no space)", False),
    ("(no actions run)\nquoted", True),
    ("Correction:\nquoted", True),
    ("Results of your actions:\nquoted", True),
    ("(Note: capital)", False),
    ("(no actions run) ", False),
    ("Correction: ", False),
    ("Results of your actions", False),
    ("format error", False),
    ("plain text", False),
    ("", False),
    ("\n- bid: OK: after a line feed", False),
    ("- bid\n: OK: the name ends at a line feed", False),
])
def test_spacing_which_first_lines_open_an_item(entry, opens):
    gap = [""] if opens else []
    assert view.space([HEAD, "- pay: OK: paid", entry]) == "\n".join([HEAD, "- pay: OK: paid"] + gap + [entry])
    assert view.space([HEAD, entry]) == f"{HEAD}\n{entry}"
    assert view.space(["Correction:", entry]) == f"Correction:\n{entry}"
    assert view.space([entry, "x"]) == f"{entry}\nx"


def test_spacing_no_empty_line_only_after_the_exact_heading_or_correction_line():
    line = "- bid: OK: placed"
    for before in ("Correction: FORMAT ERROR: bad: none of its actions ran", "Results of your actions: ",
                   "(no actions run)", "Correction:\nmore"):
        assert view.space([before, line]) == f"{before}\n\n{line}"
    assert view.space([]) == ""
    assert view.space(["FORMAT ERROR: a\n\nb. That turn was lost"]) == "FORMAT ERROR: a\n\nb. That turn was lost"


def test_spacing_of_a_whole_turn_from_turns_results():
    cap = "(note: your act call listed 9 actions; only the first 8 were run)"
    out = "(note: your remaining 1 action did not run: you are out of business)"
    entries = turns.results([cap, "- bid: OK: placed bid-1", "- run_myself: ERROR: no\n(note: clash)", out],
                            ["- bid: ERROR: again"], NO_MEMORY)
    assert view.space(entries) == "\n".join([
        HEAD, cap, "", "- bid: OK: placed bid-1", "", "- run_myself: ERROR: no\n(note: clash)", "", out, "",
        "Correction:", "- bid: ERROR: again", "", NO_MEMORY])
    assert view.space(turns.results([], None, None)) == f"{HEAD}\n(no actions run)"
    assert view.space(turns.results([], [], NO_MEMORY)) == f"{HEAD}\nCorrection:\n(no actions run)\n\n{NO_MEMORY}"
    failed = "Correction: FORMAT ERROR: bad JSON: none of its actions ran"
    assert view.space(turns.results(["- FORMAT ERROR: x: none of your actions ran", failed], None, NO_MEMORY)) == (
        f"{HEAD}\n- FORMAT ERROR: x: none of your actions ran\n{failed}\n\n{NO_MEMORY}")


def test_results_of_a_turn_lost_as_a_format_error_are_one_entry():
    m = market()
    lost = turns.lost_text("no act call", "bad JSON")
    m.participants["M-001"].results = [lost]
    assert lost.startswith("FORMAT ERROR: ")
    assert content(view.render(m, "M-001", []), RESULTS) == lost


# 5 "Notifications since your last turn"


def test_notifications_oldest_first_each_an_item_stamped_as_produced_then_cleared():
    m = market(day=4, rnd=None)
    state.notify(m, "B-001", "New task task-9 in your queue.")
    m.round = 1
    state.notify(m, "B-001", "The bids on post-1 are opened: 1 bid, which you can accept from now.")
    state.notify(m, "B-001", "M-001 paid you 5")
    text, _ = view.build(m, "B-001")
    assert content(text, NOTIFICATIONS) == (
        "[day 4] New task task-9 in your queue.\n\n"
        "[day 4, round 1] The bids on post-1 are opened: 1 bid, which you can accept from now.\n\n"
        "[day 4, round 1] M-001 paid you 5")
    m.round = 2
    assert content(view.build(m, "B-001")[0], NOTIFICATIONS) == ""


def test_notifications_of_a_days_last_round_come_before_the_next_days():
    m = market(day=4, rnd=3)
    view.build(m, "M-001")
    state.notify(m, "M-001", "B-001 kept R-1")
    m.day, m.round = 5, None
    state.notify(m, "M-001", "contract-1 is due today (deadline day 5).")
    m.round = 1
    assert content(view.build(m, "M-001")[0], NOTIFICATIONS) == (
        "[day 4, round 3] B-001 kept R-1\n\n[day 5] contract-1 is due today (deadline day 5).")


def test_notifications_wait_while_no_view_is_built():
    m = market()
    state.notify(m, "M-002", "kept")
    view.build(m, "M-001")
    assert m.participants["M-002"].pending == ["[day 3, round 2] kept"]


# 6 "Payments"


def test_payments_title_alone_and_only_the_participants_own():
    m = market()
    assert content(view.render(m, "B-001", []), PAYMENTS) == ""
    pay(m, 1, 1, "B-002", "M-002", 5.0)
    assert content(view.render(m, "B-001", []), PAYMENTS) == ""
    assert content(view.render(m, "M-001", []), PAYMENTS) == ""
    assert content(view.render(m, "B-002", []), PAYMENTS) == "[day 1, round 1] you -> M-002: 5 credits"
    assert content(view.render(m, "M-002", []), PAYMENTS) == "[day 1, round 1] B-002 -> you: 5 credits"


def test_payment_lines():
    m = market()
    contract(m, "contract-3")
    contract(m, "contract-17", price=14.0)
    pay(m, 7, 1, "B-001", "M-001", 1.0, "contract-3")
    pay(m, 12, 1, "B-001", "M-001", 14.0, "contract-17")
    pay(m, 12, 1, "B-001", "M-001", 14.0, "contract-17", to_price=0.0)
    pay(m, 12, 3, "B-001", "M-002", 12.5)
    pay(m, 16, 1, "M-001", "B-001", 18.0)
    pay(m, 16, 2, "M-001", "B-001", 0.5)
    pay(m, 17, 2, "B-002", "B-001", 1234567.0)
    pay(m, 19, 3, "M-001", "B-001", 1.01)
    assert content(view.render(m, "B-001", []), PAYMENTS).split("\n\n") == [
        "[day 7, round 1] you -> M-001: 1 credit for contract-3",
        "[day 12, round 1] you -> M-001: 14 credits for contract-17",
        "[day 12, round 1] you -> M-001: 14 credits for contract-17",
        "[day 12, round 3] you -> M-002: 12.50 credits",
        "[day 16, round 1] M-001 -> you: 18 credits",
        "[day 16, round 2] M-001 -> you: 0.50 credits",
        "[day 17, round 2] B-002 -> you: 1234567 credits",
        "[day 19, round 3] M-001 -> you: 1.01 credits",
    ]
    assert content(view.render(m, "M-001", []), PAYMENTS).split("\n\n") == [
        "[day 7, round 1] B-001 -> you: 1 credit for contract-3",
        "[day 12, round 1] B-001 -> you: 14 credits for contract-17",
        "[day 12, round 1] B-001 -> you: 14 credits for contract-17",
        "[day 16, round 1] you -> B-001: 18 credits",
        "[day 16, round 2] you -> B-001: 0.50 credits",
        "[day 19, round 3] you -> B-001: 1.01 credits",
    ]


def test_payments_only_the_latest_with_the_earlier_ones_counted():
    m = market()
    for i in range(8):
        pay(m, 1 + i, 1, "B-001", "M-001", 2.0 + i)
        pay(m, 1 + i, 2, "B-002", "M-002", 1.0)
    listed = [f"[day {1 + i}, round 1] you -> M-001: {2 + i} credits" for i in range(8)]
    assert content(view.render(m, "B-001", []), PAYMENTS).split("\n\n") == listed
    pay(m, 9, 1, "M-001", "B-001", 3.0)
    listed.append("[day 9, round 1] M-001 -> you: 3 credits")
    assert content(view.render(m, "B-001", []), PAYMENTS).split("\n\n") == ["(1 earlier not listed)"] + listed[1:]
    for i in range(5):
        pay(m, 10, 1, "B-001", "M-002", 1.0)
    items = content(view.render(m, "B-001", []), PAYMENTS).split("\n\n")
    assert items == ["(6 earlier not listed)"] + listed[6:] + ["[day 10, round 1] you -> M-002: 1 credit"] * 5


@pytest.mark.parametrize("k, title", [
    (1, "--- Payments (yours and to you; the latest 1 is shown) ---"),
    (2, "--- Payments (yours and to you; the latest 2 are shown) ---"),
    (30, "--- Payments (yours and to you; the latest 30 are shown) ---"),
])
def test_payments_title_and_count_follow_payments_shown(k, title):
    m = market(view={"payments_shown": k})
    for i in range(3):
        pay(m, 1, 1 + i, "B-001", "M-001", 4.0)
    lines = [f"[day 1, round {1 + i}] you -> M-001: 4 credits" for i in range(3)]
    expected = lines if k >= 3 else [f"({3 - k} earlier not listed)"] + lines[-k:]
    text = view.render(m, "B-001", [])
    assert f"\n\n{title}\n\n" + "\n\n".join(expected) + f"\n\n{ACCOUNTS}" in text


def test_payments_are_the_templates_of_6():
    m = market(view={"payments_shown": 2})
    contract(m, "contract-4-2")
    for amount in (3.0, 1.0, 12.5):
        pay(m, 5, 2, "B-001", "M-001", amount, "contract-4-2", to_price=0.0)
    line = "[day {d}, round {r}] {src} -> {dst}: {amount} {unit}{for}"
    fields = {"d": 5, "r": 2, "src": "you", "dst": "M-001", "for": " for contract-4-2"}
    assert view.render(m, "B-001", []).split("\n\n")[3:7] == [
        template("--- Payments (yours and to you; the latest {k}", k=2, verb="are"),
        template("({n - k}", n_minus_k=1),
        template(line, amount=1, unit="credit", **fields),
        template(line, amount="12.50", unit="credits", **fields),
    ]


def test_payment_lines_of_the_example_of_6():
    lines = next(b for b in blocks("view.md") if b.startswith(PAYMENTS)).split("\n\n")[2:]
    m = market(ids=("B-42c", "M-f2b", "M-bd6"))
    contract(m, "contract-17", price=14.0, buyer="B-42c", manager="M-f2b")
    pay(m, 12, 1, "B-42c", "M-f2b", 14.0, "contract-17")
    pay(m, 16, 1, "M-bd6", "B-42c", 18.0)
    pay(m, 19, 3, "M-f2b", "B-42c", 14.0)
    assert content(view.render(m, "B-42c", []), PAYMENTS).split("\n\n") == lines


# 8 "Your accounts so far (credits)"


def accounts(m, pid):
    return content(view.render(m, pid, []), ACCOUNTS).split("\n\n")


def test_accounts_examples_of_8():
    first, second = [b for b in blocks("view.md") if b.startswith("Spent: tokens")]
    m = market()
    b, mgr = m.participants["B-001"], m.participants["M-001"]
    b.ledger = [("per-task budgets", 214.2), ("escrow fees", 9.9), ("paid into escrow (total)", 99.0),
                ("tokens", 0.08), ("payments made", 100.0), ("tests", 20.0), ("payments received", 32.0),
                ("per-task budgets", 214.2), ("tests", 17.0), ("tokens", 0.08), ("escrow refunds", 17.0),
                ("payments made", 77.0), ("paid into escrow (total)", 100.0), ("escrow fees", 10.0)]
    for i, price in enumerate((18.0, 18.0, 18.0)):
        contract(m, f"contract-{i + 1}", "escrow", price)
    assert accounts(m, "B-001") == first.split("\n\n")
    mgr.ledger = [("dispute deposits", 6.0), ("your own runs", 31.05), ("payments received", 169.0),
                  ("tests", 30.0), ("employee runs", 83.29), ("escrow releases", 78.0), ("payments made", 12.0),
                  ("tokens", 0.18), ("dispute deposits returned", 6.0)]
    assert accounts(m, "M-001") == second.split("\n\n")


def test_accounts_are_the_template_of_8():
    m = market()
    m.participants["B-001"].ledger = [("tests", 2.0), ("per-task budgets", 30.0)]
    contract(m, "contract-1", "escrow", 20.25)
    expected = template(ACCOUNTS, spent="tests 2.00", received="per-task budgets 30.00", held="20.25")
    assert f"\n\n{expected}\n\n{RECORD}" in view.render(m, "B-001", [])


def test_accounts_with_nothing_and_a_manager_never_has_the_held_line():
    m = market()
    assert accounts(m, "B-001") == ["Spent: nothing.", "Received: nothing.", "Held in escrow now: 0.00."]
    assert accounts(m, "M-001") == ["Spent: nothing.", "Received: nothing."]
    contract(m, "contract-1", "escrow", 20.0)
    assert accounts(m, "M-001") == ["Spent: nothing.", "Received: nothing."]


def test_accounts_spent_in_the_fixed_order_received_in_the_order_of_first_entries():
    m = market()
    p = m.participants["B-001"]
    p.ledger = [(name, 1.0) for name in reversed(view.SPENT)]
    assert accounts(m, "B-001")[0] == (
        "Spent: tokens 1.00, employee runs 1.00, your own runs 1.00, tests 1.00, payments made 1.00, "
        "paid into escrow (total) 1.00, escrow fees 1.00, dispute deposits 1.00, arbiter's fees paid 1.00.")
    assert accounts(m, "B-001")[1] == "Received: nothing."
    p.ledger = [("dispute deposits returned", 6.0), ("escrow releases", 12.0), ("tests", 1.0),
                ("escrow refunds", 2.0), ("payments received", 3.0), ("per-task budgets", 30.0),
                ("escrow releases", 0.5), ("dispute deposits returned", 1.0)]
    assert accounts(m, "B-001")[:2] == [
        "Spent: tests 1.00.",
        "Received: dispute deposits returned 7.00, escrow releases 12.50, escrow refunds 2.00, "
        "payments received 3.00, per-task budgets 30.00."]
    p.ledger = [("per-task budgets", 30.0), ("payments received", 3.0), ("escrow releases", 12.0),
                ("escrow refunds", 2.0), ("dispute deposits returned", 6.0)]
    assert accounts(m, "B-001")[1] == (
        "Received: per-task budgets 30.00, payments received 3.00, escrow releases 12.00, escrow refunds 2.00, "
        "dispute deposits returned 6.00.")


def test_accounts_sums_are_plain_additions_in_the_order_of_the_events():
    m = market()
    p = m.participants["M-001"]
    charges = [0.0043, 0.000442, 0.007137, 0.003121]  # plain addition 0.015000000000000001, SUM 0.015
    p.ledger = [("tokens", x) for x in charges] + [("payments received", x) for x in charges]
    assert accounts(m, "M-001") == ["Spent: tokens 0.02.", "Received: payments received 0.02."]
    charges = [0.018505, 0.012931, 0.003564]  # 0.034999999999999996 in this order, 0.035 in the other
    p.ledger = [("tokens", x) for x in charges]
    assert accounts(m, "M-001")[0] == "Spent: tokens 0.03."
    p.ledger.reverse()
    assert accounts(m, "M-001")[0] == "Spent: tokens 0.04."


def test_accounts_a_category_sum_that_is_not_finite_is_never_shown():
    """config.md, 6, "Finiteness during an episode": two payments an accepted config allows can add up to inf."""
    m = market()
    p = m.participants["B-001"]
    p.ledger = [("payments made", 1e308), ("tests", 2.0), ("payments made", 7e307)]
    before = view.render(m, "B-001", [])
    assert accounts(m, "B-001")[0] == f"Spent: tests 2.00, payments made {1e308 + 7e307:.2f}."
    p.ledger.append(("payments made", 5e306))
    assert view.render(m, "B-001", []) == before.replace(f"{1e308 + 7e307:.2f}", f"{1e308 + 7e307 + 5e306:.2f}")
    p.ledger.append(("payments made", 5e307))
    with pytest.raises(ArithmeticError, match="^the payments made total in the accounts of B-001 is not finite: inf$"):
        view.render(m, "B-001", [])
    p.ledger = [("payments received", 1e308), ("escrow refunds", -1e308), ("payments received", 1e308)]
    with pytest.raises(ArithmeticError, match="^the payments received total in the accounts of B-001 is not finite"):
        view.render(m, "B-001", [])
    assert accounts(m, "M-001") == ["Spent: nothing.", "Received: nothing."]


def test_accounts_a_category_with_an_entry_is_listed_also_at_zero():
    m = market()
    m.participants["M-001"].ledger = [("tokens", 0.004), ("dispute deposits", 0.0),
                                      ("dispute deposits returned", 0.0)]
    assert accounts(m, "M-001") == ["Spent: tokens 0.00, dispute deposits 0.00.",
                                    "Received: dispute deposits returned 0.00."]
    m.participants["B-001"].ledger = [("per-task budgets", 0.0)]
    assert accounts(m, "B-001")[:2] == ["Spent: nothing.", "Received: per-task budgets 0.00."]


def test_accounts_entries_as_the_markets_money_movements_make_them():
    m = market()
    state.credit(m, "B-001", 30.0, "per-task budgets")
    assert state.charge(m, "B-001", 0.0123456789, "tokens") == 0.012346
    assert state.charge(m, "B-001", 0.0, "tests") == 0.0
    state.debit(m, "B-001", 20.0, "paid into escrow (total)")
    state.charge(m, "B-001", 2.0, "escrow fees")
    m.actor = "B-001"
    state.debit(m, "B-001", 5.0, "payments made")
    state.credit(m, "M-001", 5.0, "payments received")
    m.actor = None
    assert accounts(m, "B-001")[:2] == [
        "Spent: tokens 0.01, payments made 5.00, paid into escrow (total) 20.00, escrow fees 2.00.",
        "Received: per-task budgets 30.00."]
    assert accounts(m, "M-001") == ["Spent: nothing.", "Received: payments received 5.00."]
    assert header(m, "M-001")[0].endswith("Your balance: 60.00 credits.")


def test_accounts_held_is_the_sum_of_the_prices_the_buyers_escrows_hold_now():
    m = market()
    contract(m, "contract-1", "escrow", 6.2)
    contract(m, "contract-2", "escrow", 2.7, manager="M-002")
    contract(m, "contract-3", "escrow", 6.8)
    contract(m, "contract-4", "escrow", 4.6)
    contract(m, "contract-5", "plain", 100.0)
    contract(m, "contract-6", "escrow", 50.0, buyer="B-002")
    c = contract(m, "contract-7", "escrow", 1000.0, release_day=2, release_how="approved", paid=1000.0)
    deliver(c, "R-1", 2, outcome="approved", decided=2)
    contract(m, "contract-8", "escrow", 2000.0, closed="late")
    contract(m, "contract-9", "escrow", 4000.0, ended=2)
    deliver(contract(m, "contract-10", "escrow", 0.25), "R-1", 2, outcome="review")
    deliver(contract(m, "contract-11", "escrow", 0.5), "R-1", 2, outcome="rejected", decided=3,
            dispute=Dispute(3))
    assert accounts(m, "B-001")[2] == "Held in escrow now: 21.05."
    assert accounts(m, "B-002")[2] == "Held in escrow now: 50.00."


# 9 "Your record with each counterparty"


def test_record_is_its_title_alone_before_the_first_contract_and_names_only_counterparties():
    m = market()
    assert record(m, "B-001") == [] and record(m, "M-001") == []
    contract(m, "contract-1", buyer="B-002", manager="M-002")
    assert record(m, "B-001") == [] and record(m, "M-001") == []
    assert [line.split(":")[0] for line in record(m, "B-002")] == ["M-002"]
    assert [line.split(":")[0] for line in record(m, "M-002")] == ["B-002"]


def test_record_lines_sorted_by_id_in_code_point_order():
    ids = ("B-b79", "B-82c", "B-A00", "M-f2b", "M-bd6", "M-218")
    m = market(ids=ids)
    for i, (b, mgr) in enumerate([("B-b79", "M-f2b"), ("B-b79", "M-bd6"), ("B-b79", "M-218"), ("B-82c", "M-218"),
                                  ("B-A00", "M-218"), ("B-b79", "M-f2b")]):
        contract(m, f"contract-{i + 1}", buyer=b, manager=mgr)
    assert [line.split(":")[0] for line in record(m, "B-b79")] == ["M-218", "M-bd6", "M-f2b"]
    assert [line.split(":")[0] for line in record(m, "M-218")] == ["B-82c", "B-A00", "B-b79"]
    assert record(m, "B-b79")[2].startswith("M-f2b: 2 plain contracts, 0 escrow contracts; ")


def test_record_with_nothing_delivered_the_third_example_of_9_2():
    line = next(b for b in blocks("view.md") if b.startswith("M-f8c: 2 plain contracts"))
    m = market(ids=("B-c53", "M-f8c"))
    contract(m, "contract-1", buyer="B-c53", manager="M-f8c")
    contract(m, "contract-2", buyer="B-c53", manager="M-f8c", paid=5.0)
    assert record(m, "B-c53") == [line]
    assert record(m, "M-f8c") == [
        "B-c53: 2 plain contracts, 0 escrow contracts; plain: 0 delivered (0 late); they paid 0 in full, "
        "0 in part, 0 not at all (0.00 of 0.00 for delivered work)"]


def test_record_singular_counts_and_the_plain_part_with_no_plain_contract():
    m = market()
    contract(m, "contract-1", "escrow")
    escrow = ("; escrow: 0 released (0 submitted late; 0 with no decision), 0 submissions rejected, "
              "0 disputes ruled (0 for the manager), 0 closed unreleased and refunded")
    assert record(m, "B-001") == [
        "M-001: 0 plain contracts, 1 escrow contract; plain: 0 delivered (0 late); you paid 0 in full, 0 in part, "
        "0 not at all (0.00 of 0.00 for delivered work)" + escrow]
    contract(m, "contract-2")
    assert record(m, "M-001") == [
        "B-001: 1 plain contract, 1 escrow contract; plain: 0 delivered (0 late); they paid 0 in full, 0 in part, "
        "0 not at all (0.00 of 0.00 for delivered work)" + escrow]


def delivered(m):
    """Six plain contracts of B-001 with M-001 (deadline day 9): five delivered, in each state of payment."""
    c = contract(m, "contract-1", price=20.0, paid=20.0)
    deliver(c, "R-a", 8, [9.9], quality=9.0)
    deliver(c, "R-b", 10, [0.0, 0.1, 0.4, 7.7], quality=7.0)  # the latest is in use: late; exact mean 2.05...3
    c = contract(m, "contract-2", price=15.0, paid=7.5, kept="R-c")
    deliver(c, "R-c", 9, [7.0])  # kept, on the deadline day: not late; no stated quality
    deliver(c, "R-d", 11, [1.0], quality=10.0)
    c = contract(m, "contract-3", price=10.0)
    deliver(c, "R-e", 9, quality=8.2)
    c = contract(m, "contract-4", price=12.0, paid=12 - 1e-7, kept="R-f")
    deliver(c, "R-f", 10)  # kept, late
    deliver(c, "R-g", 9)
    c = contract(m, "contract-5", price=8.0, paid=1e-10)
    deliver(c, "R-h", 5)
    contract(m, "contract-6", price=30.0, paid=30.0)  # an advance: not delivered
    deliver(contract(m, "contract-7", price=99.0, paid=99.0, buyer="B-002"), "R-z", 20)
    return m


def test_record_delivered_late_and_paid_counts_and_sums_over_the_delivered():
    m = delivered(market())
    plain = ("6 plain contracts, 0 escrow contracts; plain: 5 delivered (2 late); {who} paid 2 in full, 1 in part, "
             "2 not at all (39.50 of 65.00 for delivered work)")
    assert record(m, "M-001") == ["B-001: " + plain.format(who="they"), record(m, "M-001")[1]]
    assert record(m, "M-001")[1] == (
        "B-002: 1 plain contract, 0 escrow contracts; plain: 1 delivered (1 late); they paid 1 in full, 0 in part, "
        "0 not at all (99.00 of 99.00 for delivered work)")
    assert record(m, "B-001") == [
        "M-001: " + plain.format(who="you") + "; your readings of their deliveries in use: mean 4.5 over 2 results"
        "; quality they stated with those deliveries: mean 7.6 over 2"]


def test_record_paid_in_full_and_not_at_all_thresholds():
    m = market()
    for i, paid in enumerate((10 - 1e-6, 10 - 1.1e-6, 1e-9, 1.1e-9, 10.0, 0.0)):
        deliver(contract(m, f"contract-{i + 1}", price=10.0, paid=paid), "R-1", 3)
    assert "; they paid 2 in full, 2 in part, 2 not at all (30.00 of 60.00 for delivered work)" in record(
        m, "M-001")[0]


def test_record_readings_are_the_mean_of_each_results_exact_mean():
    m = market()
    c = contract(m, "contract-1")
    deliver(c, "R-a", 3, [0.0, 0.1, 0.4, 7.7])  # the exact mean is 2.0500000000000003; one by one, 2.05
    assert record(m, "B-001")[0].endswith("; your readings of their deliveries in use: mean 2.1 over 1 result")
    c.results[0].buyer_readings = [6.8, 6.0, 6.2, 7.6, 0.0, 1.3]  # exact mean 4.65; SUM / 6 is 4.6499999999999995
    assert record(m, "B-001")[0].endswith("; your readings of their deliveries in use: mean 4.7 over 1 result")
    c.results[0].buyer_readings = [0.0, 0.1, 0.4, 7.7]
    deliver(contract(m, "contract-2"), "R-a", 3, [6.2, 2.7, 6.8, 4.6])
    deliver(contract(m, "contract-3"), "R-a", 3)
    assert record(m, "B-001")[0].endswith("; your readings of their deliveries in use: mean 3.6 over 2 results")
    assert "readings" not in record(m, "M-001")[0] and "quality" not in record(m, "M-001")[0]


def test_record_not_tested_and_nothing_delivered():
    m = market()
    c = contract(m, "contract-1")
    assert record(m, "B-001")[0].endswith("(0.00 of 0.00 for delivered work)")
    deliver(c, "R-a", 3, [5.0])
    deliver(c, "R-b", 4)
    assert record(m, "B-001")[0].endswith(
        "(0.00 of 12.00 for delivered work); you have not tested their deliveries in use")
    assert record(m, "M-001")[0].endswith("(0.00 of 12.00 for delivered work)")
    c.kept = "R-a"
    assert record(m, "B-001")[0].endswith("; your readings of their deliveries in use: mean 5.0 over 1 result")


def test_record_stated_quality_of_the_deliveries_in_use():
    m = market()
    c = contract(m, "contract-1")
    deliver(c, "R-a", 3, quality=9.0)
    deliver(c, "R-b", 4)
    tail = "; you have not tested their deliveries in use"
    assert record(m, "B-001")[0].endswith("for delivered work)" + tail)
    c.kept = "R-a"
    assert record(m, "B-001")[0].endswith(tail + "; quality they stated with those deliveries: mean 9.0 over 1")
    deliver(contract(m, "contract-2"), "R-a", 3, quality=0.0)
    deliver(contract(m, "contract-3"), "R-a", 3, quality=7.25)
    assert record(m, "B-001")[0].endswith(tail + "; quality they stated with those deliveries: mean 5.4 over 3")


def escrows(m, base=0):
    """Nine escrow contracts of B-001 with M-001 (deadline day 9), one per outcome, numbered from base + 1."""
    c = contract(m, f"contract-{base + 1}", "escrow", release_day=9, release_how="approved", paid=12.0, received=9)
    deliver(c, "R-a", 8, outcome="approved", decided=9)
    c = contract(m, f"contract-{base + 2}", "escrow", release_day=12, release_how="automatic", paid=9.6, received=12)
    deliver(c, "R-a", 10, outcome="auto", decided=12)
    c = contract(m, f"contract-{base + 3}", "escrow", release_day=9, release_how="ruling", paid=12.0)
    deliver(c, "R-a", 7, outcome="rejected", decided=8, dispute=Dispute(8, 6.7, "manager"))
    c = contract(m, f"contract-{base + 4}", "escrow")
    deliver(c, "R-a", 6, outcome="rejected", decided=7, dispute=Dispute(7, 3.0, "buyer"))
    deliver(c, "R-b", 9, outcome="rejected", decided=10)
    c = contract(m, f"contract-{base + 5}", "escrow")
    deliver(c, "R-a", 9, outcome="rejected", decided=10, dispute=Dispute(10))
    contract(m, f"contract-{base + 6}", "escrow", closed="late")
    deliver(contract(m, f"contract-{base + 7}", "escrow", ended=12), "R-a", 9, outcome="rejected", decided=10)
    contract(m, f"contract-{base + 8}", "escrow")
    deliver(contract(m, f"contract-{base + 9}", "escrow"), "R-a", 12, outcome="review")
    return m


def test_record_escrow_counts_for_each_outcome():
    m = escrows(market(day=13))
    line = ("{other}: 0 plain contracts, 9 escrow contracts; plain: 0 delivered (0 late); {who} paid 0 in full, "
            "0 in part, 0 not at all (0.00 of 0.00 for delivered work); escrow: 3 released (1 submitted late; "
            "1 with no decision), 5 submissions rejected, 2 disputes ruled (1 for the manager), 1 closed unreleased "
            "and refunded; 1 ended by {them}")
    assert record(m, "B-001") == [line.format(other="M-001", who="you", them="you")]
    assert record(m, "M-001") == [line.format(other="B-001", who="they", them="them")]


@pytest.mark.parametrize("build, counts", [
    (lambda c: deliver(c, "R-a", 9, outcome="review"),
     "0 released (0 submitted late; 0 with no decision), 0 submissions rejected, 0 disputes ruled (0 for the "
     "manager), 0 closed unreleased and refunded"),
    (lambda c: (deliver(c, "R-a", 9, outcome="approved", decided=9), setattr(c, "release_day", 9)),
     "1 released (0 submitted late; 0 with no decision), 0 submissions rejected, 0 disputes ruled (0 for the "
     "manager), 0 closed unreleased and refunded"),
    (lambda c: (deliver(c, "R-a", 9, outcome="rejected", decided=10),
                deliver(c, "R-b", 10, outcome="approved", decided=10), setattr(c, "release_day", 10)),
     "1 released (1 submitted late; 0 with no decision), 1 submission rejected, 0 disputes ruled (0 for the "
     "manager), 0 closed unreleased and refunded"),
    (lambda c: (deliver(c, "R-a", 11, outcome="auto", decided=13), setattr(c, "release_day", 13)),
     "1 released (1 submitted late; 1 with no decision), 0 submissions rejected, 0 disputes ruled (0 for the "
     "manager), 0 closed unreleased and refunded"),
    (lambda c: (deliver(c, "R-a", 9, outcome="rejected", decided=10, dispute=Dispute(10, 7.0, "manager")),
                setattr(c, "release_day", 11)),
     "1 released (0 submitted late; 0 with no decision), 1 submission rejected, 1 dispute ruled (1 for the "
     "manager), 0 closed unreleased and refunded"),
    (lambda c: deliver(c, "R-a", 9, outcome="rejected", decided=10, dispute=Dispute(10, 2.0, "buyer")),
     "0 released (0 submitted late; 0 with no decision), 1 submission rejected, 1 dispute ruled (0 for the "
     "manager), 0 closed unreleased and refunded"),
    (lambda c: deliver(c, "R-a", 9, outcome="rejected", decided=10, dispute=Dispute(10)),
     "0 released (0 submitted late; 0 with no decision), 1 submission rejected, 0 disputes ruled (0 for the "
     "manager), 0 closed unreleased and refunded"),
    (lambda c: setattr(c, "closed", "late"),
     "0 released (0 submitted late; 0 with no decision), 0 submissions rejected, 0 disputes ruled (0 for the "
     "manager), 1 closed unreleased and refunded"),
    (lambda c: setattr(c, "ended", 12),
     "0 released (0 submitted late; 0 with no decision), 0 submissions rejected, 0 disputes ruled (0 for the "
     "manager), 0 closed unreleased and refunded; 1 ended by you"),
])
def test_record_escrow_piece_of_one_contract(build, counts):
    m = market(day=13)
    build(contract(m, "contract-1", "escrow"))
    assert record(m, "B-001")[0].split("; escrow: ")[1] == counts


def test_record_payments_beyond_the_prices():
    m = market()
    deliver(contract(m, "contract-1", price=20.0), "R-a", 3)
    contract(m, "contract-2", price=10.0)
    contract(m, "contract-3", price=10.0, buyer="B-002")
    pay(m, 1, 1, "B-001", "M-001", 15.0, "contract-1")
    assert "other payments" not in record(m, "B-001")[0]
    pay(m, 1, 2, "B-001", "M-001", 10.0, "contract-1", to_price=5.0)
    one = "; other payments on your contracts: {who} paid 5.00 on plain contracts beyond what was owed of their "\
          "prices (1 payment)"
    assert record(m, "B-001")[0].endswith("for delivered work); you have not tested their deliveries in use"
                                          + one.format(who="you"))
    assert record(m, "M-001")[0].endswith("for delivered work)" + one.format(who="they"))
    pay(m, 2, 1, "B-001", "M-001", 12.34, "contract-2", to_price=10.0)
    pay(m, 2, 2, "B-001", "M-001", 6.0, "contract-1", to_price=0.0)
    pay(m, 2, 3, "B-001", "M-001", 50.0)
    pay(m, 2, 3, "M-001", "B-001", 11.0)
    pay(m, 2, 3, "B-002", "M-001", 10.99, "contract-3", to_price=10.0)
    many = "; other payments on your contracts: {who} paid 13.34 on plain contracts beyond what was owed of their "\
           "prices (3 payments)"
    assert record(m, "B-001")[0].endswith(many.format(who="you"))
    assert record(m, "M-001")[0].endswith(many.format(who="they"))
    assert record(m, "M-001")[1].endswith(
        "; other payments on your contracts: they paid 0.99 on plain contracts beyond what was owed of their "
        "prices (1 payment)")
    assert "(20.00 of 20.00 for delivered work)" in record(m, "B-001")[0]


def test_record_contracts_ended_by_the_buyer():
    m = market()
    contract(m, "contract-1", ended=4)
    contract(m, "contract-2", "escrow", ended=5)
    contract(m, "contract-3")
    assert record(m, "B-001")[0].endswith("0 closed unreleased and refunded; 2 ended by you")
    assert record(m, "M-001")[0].endswith("0 closed unreleased and refunded; 2 ended by them")
    assert record(m, "B-001")[0].startswith("M-001: 2 plain contracts, 1 escrow contract; ")


def test_record_every_piece_in_order():
    m = escrows(delivered(market(day=13)), base=10)
    pay(m, 2, 2, "B-001", "M-001", 6.0, "contract-1", to_price=0.0)
    m.contracts["contract-6"].ended = 12
    assert record(m, "B-001") == [
        "M-001: 6 plain contracts, 9 escrow contracts; plain: 5 delivered (2 late); you paid 2 in full, 1 in part, "
        "2 not at all (39.50 of 65.00 for delivered work); your readings of their deliveries in use: mean 4.5 over "
        "2 results; quality they stated with those deliveries: mean 7.6 over 2; escrow: 3 released (1 submitted "
        "late; 1 with no decision), 5 submissions rejected, 2 disputes ruled (1 for the manager), 1 closed "
        "unreleased and refunded; other payments on your contracts: you paid 6.00 on plain contracts beyond what "
        "was owed of their prices (1 payment); 2 ended by you"]


# 10 "Your self-written memory"


def memory_title(m, pid):
    return parts(view.build(m, pid)[0])[1][-2]


def test_memory_never_written_is_the_title_alone():
    assert memory_title(market(), "B-001") == (MEMORY, "")


def test_memory_written_by_the_previous_turns_decide_reply():
    m = market()
    p = m.participants["M-001"]
    p.turns, p.memory = 6, Memory("a\n\n  b\tc\n- bid: OK: x", 2, 3, 6, False)
    assert memory_title(m, "M-001") == (
        "--- Your self-written memory (last updated day 2, round 3, with your last actions, before their results) "
        "---", "a\n\n  b\tc\n- bid: OK: x")


def test_memory_written_from_the_correction_or_in_an_earlier_turn():
    m = market()
    p = m.participants["M-001"]
    p.turns, p.memory = 6, Memory("kept", 12, 1, 6, True)
    assert memory_title(m, "M-001") == ("--- Your self-written memory (last updated day 12, round 1) ---", "kept")
    p.memory = Memory("kept", 12, 1, 6, False)
    assert memory_title(m, "M-001") == ("--- Your self-written memory (last updated day 12, round 1) ---", "kept")
    p.turns, p.memory = 9, Memory("old", 1, 2, 3, False)
    assert memory_title(m, "M-001") == ("--- Your self-written memory (last updated day 1, round 2) ---", "old")


def test_memory_sections_are_the_three_templates_of_10():
    never, last, dated = [b for b in blocks("view.md") if b.startswith("--- Your self-written memory")]
    m = market()
    p = m.participants["B-001"]
    assert f"\n\n{never}\n\n{FINAL}" in view.build(m, "B-001")[0]
    p.memory = Memory("two\nlines", 4, 2, 1, False)
    fields = {"d": 4, "r": 2, "memory": "two\nlines"}
    assert f"\n\n{template(last, **fields)}\n\n{FINAL}" in view.build(m, "B-001")[0]
    assert f"\n\n{template(dated, **fields)}\n\n{FINAL}" in view.build(m, "B-001")[0]


def test_memory_title_after_a_turn_that_wrote_none_and_after_a_lost_turn():
    m = market()
    p = m.participants["B-001"]
    p.turns, p.memory = 1, Memory("mine", 3, 1, 1, False)
    last = "--- Your self-written memory (last updated day 3, round 1, with your last actions, before their "\
           "results) ---"
    text, saved = view.build(m, "B-001")
    assert parts(text)[1][-2] == (last, "mine")
    view.undo(m, "B-001", saved)  # lost to a failed call: no turn
    assert memory_title(m, "B-001") == (last, "mine")
    # that turn wrote no memory (no memory in its reply, or lost as a format error): the plain dated form
    assert memory_title(m, "B-001") == ("--- Your self-written memory (last updated day 3, round 1) ---", "mine")


# 11 "Final instructions"


def test_final_instructions_whatever_the_view_holds(stand):
    block = next(b for b in blocks("view.md") if b.startswith(FINAL))
    m = escrows(delivered(market("all", day=13)), base=10)
    m.participants["B-001"].memory = Memory("x" * 2000, 1, 1, 0, False)
    for pid in IDS:
        assert view.build(m, pid)[0].endswith("\n\n" + block)


# 14 and examples/: complete views


def test_complete_example_14_1_a_buyer_on_day_1_round_2(stand):
    expected = next(b for b in blocks("view.md") if b.startswith("=== DAY 1, ROUND 2 of 3 today === You are B-c53"))
    m = market(day=1, rnd=2, ids=("B-c53", "B-d75", "M-14b", "M-849", "M-f8c"))
    p = m.participants["B-c53"]
    p.balance, p.turns = 163.2, 1
    p.ledger = [("per-task budgets", 40.8), ("per-task budgets", 40.8), ("tokens", 0.0012)]
    p.results = [HEAD, "- post_task: OK: posted task-1 as post-1; prefers escrow, threshold 7"]
    p.memory = Memory("mock memory", 1, 1, 1, False)
    use_example(stand, expected, messages=False)
    assert view.build(m, "B-c53")[0] == expected


def manager_day_28():
    """A state behind 14.2's general sections: M-7f8 on day 28, round 3."""
    m = market("all", 28, 3, ("B-82c", "B-b79", "M-0ed", "M-ee6", "M-7f8"))
    p = m.participants["M-7f8"]
    p.balance, p.turns = 141.48, 80
    p.results = [HEAD, "- send_message: OK: message sent to B-b79"]
    plain = [(1, "B-82c", 12.0), (2, "B-82c", 12.0), (5, "B-b79", 12.0), (8, "B-b79", 13.0), (9, "B-b79", 13.0),
             (17, "B-b79", 12.0), (20, "B-b79", 12.0), (21, "B-b79", 12.0), (26, "B-b79", 12.0),
             (27, "B-b79", 12.0), (29, "B-b79", 12.0), (30, "B-b79", 12.0), (31, "B-b79", 12.0),
             (32, "B-b79", 11.0), (41, "B-82c", 12.0)]
    escrow = [(40, "B-b79", 18.0), (43, "B-b79", 12.0), (46, "B-b79", 12.0), (47, "B-82c", 12.0),
              (48, "B-82c", 12.0), (49, "B-82c", 12.0)]
    for n, buyer, price in sorted(plain + escrow):
        form = "plain" if (n, buyer, price) in plain else "escrow"
        c = contract(m, f"contract-{n}", form, price, buyer, "M-7f8", deadline=n + 4)
        if form == "plain":
            deliver(c, "R-001", n + 2, quality=7.0)
        elif n == 48:
            deliver(c, "R-025", 23, outcome="rejected", decided=24, dispute=Dispute(24, 6.7, "manager"))
            c.release_day, c.release_how, c.paid = 26, "ruling", price
        else:
            deliver(c, "R-001", n + 2, outcome="approved", decided=n + 3)
            c.release_day, c.release_how, c.paid, c.received = n + 3, "approved", price, n + 3
    for day, rnd, payer, payee, amount, n, part in [
        (6, 1, "B-b79", "M-7f8", 12.0, 5, None), (7, 1, "B-b79", "M-7f8", 13.0, 8, None),
        (7, 2, "B-82c", "M-0ed", 9.0, None, None), (8, 2, "B-b79", "M-7f8", 13.0, 9, None),
        (10, 1, "B-82c", "M-7f8", 12.0, 1, None), (10, 1, "B-82c", "M-7f8", 12.0, 2, None),
        (11, 3, "B-b79", "M-7f8", 12.0, 21, None), (12, 1, "B-b79", "M-7f8", 12.0, 27, None),
        (12, 3, "B-b79", "M-7f8", 12.0, 26, None), (13, 2, "B-b79", "M-7f8", 12.0, 26, 0.0),
        (14, 1, "M-7f8", "B-b79", 12.0, None, None), (15, 3, "B-b79", "M-7f8", 12.0, 29, None),
        (15, 3, "B-b79", "M-7f8", 11.0, 32, None), (16, 2, "B-b79", "M-7f8", 12.0, 20, None),
        (18, 1, "M-ee6", "B-b79", 3.0, None, None), (20, 1, "B-82c", "M-7f8", 12.0, 41, None),
        (22, 3, "B-b79", "M-7f8", 12.0, 31, None),
    ]:
        pay(m, day, rnd, payer, payee, amount, f"contract-{n}" if n else None, part)
    p.ledger = [("your own runs", 31.05), ("tokens", 0.06), ("tests", 12.0), ("employee runs", 40.0)]
    p.ledger += [("payments received" if x.payee == "M-7f8" else "payments made", x.amount)
                 for x in m.payments if "M-7f8" in (x.payer, x.payee)]
    p.ledger += [("escrow releases", 18.0), ("escrow releases", 12.0), ("tokens", 0.06), ("employee runs", 43.29),
                 ("escrow releases", 12.0), ("dispute deposits", 6.0), ("tests", 18.0),
                 ("dispute deposits returned", 6.0), ("escrow releases", 12.0), ("escrow releases", 12.0),
                 ("escrow releases", 12.0), ("tokens", 0.06)]
    p.memory = Memory(
        "Day28 r2: No bids (all open posts past due; late results worth too little vs ~12 price). Sent B-b79 "
        "reminder for c30 (12) and c31 (12) unpaid. Paused new runs for B-b79 until paid. B-82c claims c1/c2 paid "
        "day 10 (not in my log; low priority). Balance ~141. Plan: wait for fresh on-time posts; bid only at prices "
        "well below expected value; prefer escrow; keep stated qualities honest; test before submitting. Employee "
        "and own runs free to start on new contracts.", 28, 2, 80, False)
    return m


def test_complete_example_14_2_a_manager_on_day_28_round_3(stand):
    expected = next(b for b in blocks("view.md") if b.startswith("=== DAY 28, ROUND 3 of 3 today === You are M-7f8"))
    assert expected == example("view_manager_all_M-7f8_day28_round3.txt")
    m = manager_day_28()
    use_example(stand, expected, messages=True)
    assert view.build(m, "M-7f8")[0] == expected


def buyer_day_23():
    """A state behind the general sections of examples/view_buyer_all_B-42c_day23_round2.txt."""
    m = market("all", 23, 2, ("B-79d", "B-42c", "M-bd6", "M-f2b", "M-218"))
    p = m.participants["B-42c"]
    p.balance, p.turns = 85.14, 60
    p.results = [
        HEAD,
        "- test_delivery: OK: R-07d reading: 4.6 (charged 1 credit). Your readings [5.6, 4.6] mean 5.10 (n=2, "
        "\u00b11.41 standard error)",
        "- test_delivery: OK: R-07d reading: 6.3 (charged 1 credit). Your readings [5.6, 4.6, 6.3] mean 5.50 (n=3, "
        "\u00b11.15 standard error)",
        "- end_contract: OK: ended contract-27 with M-bd6 (its rejection of R-1e2 stands); its price 17 came back "
        "to you (the fees are not refunded). task-27 is back in your queue, due day 19 (4 days ago), and can be "
        "posted again",
    ]
    p.memory = Memory(content(example("view_buyer_all_B-42c_day23_round2.txt"), MEMORY[:-4]), 23, 1, 60, False)
    # plain: (number, manager, price, the buyer's readings of the delivery in use, its stated quality)
    plain = [(3, "M-218", 18.0, [6.6], 9.0), (5, "M-218", 16.0, [5.1, 5.2], None), (6, "M-f2b", 15.0, [9.1], None),
             (8, "M-bd6", 20.0, [7.0], 7.2), (10, "M-bd6", 18.0, [7.4], 7.6), (14, "M-bd6", 16.0, [7.8], 8.0),
             (15, "M-f2b", 14.0, [9.0], None), (17, "M-f2b", 14.0, [9.2], 8.5), (23, "M-f2b", 14.0, [9.5], 9.1),
             (39, "M-f2b", 14.0, None, None)]
    # escrow: (number, manager, price, how it stands)
    escrow = [(12, "M-218", 20.0, "approved"), (13, "M-218", 18.0, "approved"), (18, "M-218", 18.0, "approved"),
              (24, "M-218", 18.0, "approved"), (25, "M-218", 18.0, "approved"), (26, "M-bd6", 17.0, "ruling"),
              (27, "M-bd6", 17.0, "ended"), (28, "M-bd6", 17.0, "approved"), (29, "M-218", 18.0, "review"),
              (35, "M-218", 18.0, "held"), (36, "M-218", 18.0, "held")]
    for n, manager, price, *rest in sorted(plain + escrow):
        if len(rest) == 2:
            c = contract(m, f"contract-{n}", "plain", price, "B-42c", manager, deadline=n + 4)
            if rest[0] is not None:
                deliver(c, "R-000", n + 1, [1.0])
                deliver(c, "R-001", n + 2, rest[0], quality=rest[1])
            continue
        c, how = contract(m, f"contract-{n}", "escrow", price, "B-42c", manager, deadline=n + 4), rest[0]
        if how == "approved":
            deliver(c, "R-001", n + 2, [7.7], outcome="approved", decided=n + 3)
            c.release_day, c.release_how, c.paid, c.received = n + 3, "approved", price, n + 3
        elif how == "ruling":
            deliver(c, "R-99e", 20, [6.6, 5.1], outcome="rejected", decided=21, dispute=Dispute(21, 8.2, "manager"))
            c.release_day, c.release_how, c.paid = 22, "ruling", price
        elif how == "ended":
            deliver(c, "R-1e2", 20, outcome="rejected", decided=21)
            c.ended = 23
        elif how == "review":
            deliver(c, "R-07d", 22, [5.6, 4.6, 6.3], outcome="review")
    for day, rnd, payer, payee, amount, n, part in [
        (6, 2, "B-42c", "M-218", 16.0, 5, None), (6, 2, "B-42c", "M-218", 14.0, 3, None),
        (7, 1, "B-42c", "M-218", 4.0, 3, None), (7, 2, "B-42c", "M-f2b", 15.0, 6, None),
        (8, 1, "B-79d", "M-f2b", 7.0, None, None), (9, 1, "B-42c", "M-bd6", 18.0, 10, None),
        (10, 1, "B-42c", "M-bd6", 20.0, 8, None), (12, 1, "B-42c", "M-f2b", 14.0, 17, None),
        (14, 1, "B-42c", "M-f2b", 14.0, 15, None), (14, 1, "B-42c", "M-bd6", 18.0, 10, 0.0),
        (15, 2, "B-42c", "M-bd6", 16.0, 14, None), (16, 1, "M-bd6", "B-42c", 18.0, None, None),
        (18, 1, "B-42c", "M-f2b", 14.0, 23, None), (19, 1, "B-42c", "M-f2b", 14.0, 23, 0.0),
        (19, 3, "M-f2b", "B-42c", 14.0, None, None),
    ]:
        pay(m, day, rnd, payer, payee, amount, f"contract-{n}" if n else None, part)
    p.ledger = [("per-task budgets", 214.2), ("tokens", 0.08), ("paid into escrow (total)", 199.0),
                ("escrow fees", 19.9), ("tests", 37.0), ("per-task budgets", 214.2)]
    p.ledger += [("payments made" if x.payer == "B-42c" else "payments received", x.amount)
                 for x in m.payments if "B-42c" in (x.payer, x.payee)]
    p.ledger += [("tokens", 0.08), ("escrow refunds", 17.0)]
    return m


def test_complete_example_a_busy_buyer_on_day_23_round_2(stand):
    expected = example("view_buyer_all_B-42c_day23_round2.txt")
    m = buyer_day_23()
    use_example(stand, expected, messages=True)
    assert view.build(m, "B-42c")[0] == expected


def day_1():
    """Seed 0's market as round 1 of day 1 starts, as far as the two day-1 examples show it."""
    m = market(day=1, rnd=None, ids=("B-c53", "B-d75", "M-14b", "M-849", "M-f8c"))
    b = m.participants["B-c53"]
    b.share = 1.36
    b.balance = config.start_balance(m.cfg, 1.36)
    m.participants["M-14b"].balance = config.start_balance(m.cfg, 1.35)
    for worth in (65, 59):
        task = Task(state.new_task_id(m), "B-c53", worth, 9)
        m.tasks[task.id] = task
        state.credit(m, "B-c53", config.per_task_budget(m.cfg, 1.36), "per-task budgets")
        state.notify(m, "B-c53", f"New task {task.id} in your queue, worth {worth} to you, due day 9 (8 days left). "
                                 "It brought 40.80 credits, added to your balance.")
    m.round = 1
    return m


def test_complete_examples_of_day_1_with_the_market_sections(real):
    m = day_1()
    assert view.build(m, "B-c53")[0] == example("view_buyer_day1_off_B-c53.txt")
    assert view.build(m, "M-14b")[0] == example("view_manager_day1_off_M-14b.txt")


def test_complete_examples_of_day_1_frame(stand):
    for pid, name in (("B-c53", "view_buyer_day1_off_B-c53.txt"), ("M-14b", "view_manager_day1_off_M-14b.txt")):
        m, expected = day_1(), example(name)
        use_example(stand, expected, messages=False)
        assert view.build(m, pid)[0] == expected


