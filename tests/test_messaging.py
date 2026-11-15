"""Messaging (market/messaging.py) against spec/messaging.md.

Every expected text is read from the spec itself (its fenced blocks, its tables, its examples), so a test fails when
the code and the spec differ by one byte. The summarizer's backend is a stub: no test makes a model call.
"""

import copy
import json
import re
from pathlib import Path

import pytest

from market import actions, config, messaging, models, state
from market.fmt import ONE_LINE as one_line
from market.messaging import conversations, is_new, request, section, send, shown, thread
from market.models import CallFailed, Reply
from market.state import Market, Message

SPEC = Path(__file__).resolve().parent.parent / "spec"
BUYERS, MANAGERS = ("B-c53", "B-d75"), ("M-14b", "M-849", "M-f8c")  # 2.3: seed 0 of the reference
IDS = "B-c53, B-d75, M-14b, M-849, M-f8c"
OTHER = {"buyer": "manager", "manager": "buyer"}
GROUPS = {
    "Conversation among all managers": "managers",
    "Conversation among all buyers": "buyers",
    "Conversation among all participants": "everyone",
}
LINE = re.compile(r"  \[day (\d+), round (\d+)\] (you|[BM]-\w{3}) -> "
                  r"(you|all managers|all buyers|everyone|[BM]-\w{3})( \(NEW\))?: (.*)")
SUMMARY = re.compile(r"  Summary of the (\d+) earlier messages? \(written automatically(?:, (\d+) of them new)?"
                     r"(?:; you are \S+ in it)?\): (.*)")


def spec(name="messaging.md"):
    return (SPEC / name).read_text(encoding="utf-8")


def fenced(after, n=0, name="messaging.md"):
    """The n-th fenced block of a spec file after a marker, without the fence's own indentation."""
    text = spec(name)
    found = re.findall(r"^( *)```\n(.*?)\n\1```$", text[text.index(after):], flags=re.M | re.S)
    indent, body = found[n]
    return "\n".join(line[len(indent):] for line in body.split("\n"))


def rows(after):
    """The cells of the rows of the table whose header row starts with a marker, without its two header rows."""
    lines = spec()[spec().index(after):].split("\n\n")[0].splitlines()
    return [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines[2:]]


def market(mode="all", buyers=BUYERS, managers=MANAGERS, **messaging_keys):
    """The participants of 2.3's examples on day 3, round 2, with a log that is a list."""
    m = Market(seed=1, cfg=config.resolve({"message_channels": mode, "messaging": messaging_keys}))
    for pid in buyers:
        state.add_participant(m, pid, "buyer")
    for pid in managers:
        state.add_participant(m, pid, "manager")
    m.day, m.round = 3, 2
    m.log = [].append
    return m


def log(m, kind):
    return [r for r in m.log.__self__ if r["type"] == kind]


def say(m, sender, to, text="hi", day=1, rnd=1, n=1):
    """n messages put in the market's sequence as a successful send_message records them."""
    m.messages += [Message(day, rnd, sender, to, text) for _ in range(n)]


def act(m, pid, **args):
    return actions.run(m, pid, "send_message", args)


class Stub:
    """A summarizer backend that gives its scripted outcomes in order, then `default`; it keeps every request."""

    def __init__(self, *outcomes, default=None):
        self.outcomes, self.default, self.requests = list(outcomes), default, []

    def call(self, messages, functions=None, tool_choice=None):
        assert functions is None and tool_choice is None  # 5.1: no functions
        self.requests.append(messages)
        out = self.outcomes.pop(0) if self.outcomes else self.default
        assert out is not None, "the summarizer was called"
        if isinstance(out, Exception):
            raise out
        return out


class Tracker:
    """A cost tracker that counts its checks and is reached from the `stop`-th check on."""

    def __init__(self, stop=None):
        self.checks, self.stop = 0, stop

    def check(self):
        self.checks += 1
        if self.stop is not None and self.checks >= self.stop:
            raise models.CostCapReached("cost cap $1.00 reached (spent $1.00)")


def text_of(sec):
    """A section as the view prints it: the title, then its items, one empty line apart (3.2; view.md, 2.3)."""
    title, items = sec
    return "\n\n".join([title] + items)


def view(m, pid, summarizer=None, tracker=None):
    return text_of(section(m, m.participants[pid], summarizer, tracker))


def lines_of(m, pid, summarizer=None, tracker=None):
    """The indented lines of a participant's Messages section."""
    return [line for line in view(m, pid, summarizer, tracker).splitlines() if line.startswith("  ")]


# 1 and 2.2: who has send_message


def test_send_message_is_registered():
    assert actions.ACTIONS["send_message"] is send


@pytest.mark.parametrize("mode", config.MODES)
@pytest.mark.parametrize("pid", ["B-c53", "M-14b"])
def test_roles_that_cannot_send(mode, pid):
    m = market(mode)
    role = m.participants[pid].role
    ok, message = act(m, pid, to="everyone", text="hi")
    if mode in ("within_roles", "all", role + "s"):
        assert (ok, message) == (mode == "all", message)
        assert conversations(m, m.participants[pid])
        return
    names = fenced(f"A {role} that cannot send:", name="actions-buyer.md")
    block = fenced("### 2.2 Before send_message's own checks", 0 if mode == "off" else 1)
    assert (ok, message) == (False, block.replace("{list}", names))
    assert conversations(m, m.participants[pid]) == [] and m.messages == []


def test_a_sender_out_of_business_gets_the_generic_refusal():
    m = market("managers")
    m.participants["M-14b"].out_of_business = True
    assert act(m, "M-14b", to="managers", text="hi") == (
        False, "you are out of business (balance at or below 0) and cannot act")
    assert act(m, "M-849", to="M-14b", text="hi") == (True, "message sent to M-14b")  # 1: it can still be sent to


# 2.3 and 2.4: the action's own checks


def allowed(mode, role):
    block = fenced("- `{allowed}`, by the mode", 1 if mode == "all" else 0)
    return block.replace("{role}", role)


def refusal(letter, mode, role, raw=None, them=None):
    """The error text of a row of 2.3's table, its placeholders filled as the section defines them."""
    template = next(row[2] for row in rows("| # | condition | error text |") if row[0] == letter).strip("`")
    return (template.replace("{allowed}", allowed(mode, role)).replace("{group}", str(raw))
            .replace("{raw}", str(raw)).replace("{ids}", IDS).replace("{id}", str(them))
            .replace("{their_role}", OTHER[role]).replace("{role}", role))


def test_the_rows_of_the_table_are_the_eight_of_the_spec():
    assert [row[0] for row in rows("| # | condition | error text |")] == list("abdefghi")
    assert refusal("a", "all", "buyer") == "missing argument 'to'"
    assert refusal("h", "all", "buyer") == "missing argument 'text'"


COLUMNS = [("managers", "M-14b"), ("buyers", "B-c53"), ("within_roles", "M-14b"), ("within_roles", "B-c53"),
           ("all", "M-14b"), ("all", "B-c53")]
RECIPIENTS = {
    "another manager's ID": ["M-849"],
    "another buyer's ID": ["B-d75"],
    "the sender's own ID": [None],
    "`managers`": ["managers"],
    "`buyers`": ["buyers"],
    "`everyone`": ["everyone"],
    "any other text": ["m-849", " M-849 ", "all", "Managers", " managers ", "all managers", "M-000", "EVERYONE"],
    "not a text": [0, 5, 2.5, True, False, ["M-849"], {"id": "M-849"}],
    "not given": [None, ""],
}


def test_what_the_recipient_may_name_in_each_mode_and_role():
    table = rows("| recipient (exactly as sent) |")
    assert [row[0] for row in table] == list(RECIPIENTS)
    cases = 0
    for row in table:
        for (mode, pid), cell in zip(COLUMNS, row[1:], strict=True):
            for raw in RECIPIENTS[row[0]]:
                m = market(mode)
                role = m.participants[pid].role
                to = pid if row[0] == "the sender's own ID" else raw
                result = act(m, pid, to=to, text="hello")
                cases += 1
                if cell.startswith("sent"):
                    label = {"managers": "all managers", "buyers": "all buyers"}.get(to, to)
                    assert result == (True, f"message sent to {label}"), (mode, pid, to)
                    assert cell == "sent" or cell == f"sent ({label})"
                    assert [(msg.sender, msg.to) for msg in m.messages] == [(pid, to)]
                else:
                    assert result == (False, refusal(cell, mode, role, to, to)), (mode, pid, to)
                    assert m.messages == [] and log(m, "message") == []  # a failed send_message stores nothing
    assert cases == 6 * (6 + 8 + 7 + 2)


def test_the_exact_texts_of_the_spec():
    """The eight lines of 2.3's "Exact texts", each from the case that gives it."""
    lines = fenced("Exact texts (seed 0 of the reference").splitlines()
    cases = [("managers", "M-14b", 7), ("all", "B-c53", "managers"), ("within_roles", "M-14b", "buyers"),
             ("managers", "M-14b", "everyone"), ("all", "M-14b", "all"), ("buyers", "B-c53", " managers "),
             ("within_roles", "M-14b", "B-d75"), ("buyers", "B-c53", "M-849")]
    assert len(lines) == len(cases) == 8
    for line, (mode, pid, to) in zip(lines, cases):
        assert act(market(mode), pid, to=to, text="hello") == (False, line)


@pytest.mark.parametrize("args, letter", [
    ({}, "a"), ({"to": None, "text": None}, "a"), ({"to": "", "text": "hi"}, "a"),
    ({"recipient": "M-849", "message": "hi", "content": "hi"}, "a"),  # 2.1: arguments it does not read
    ({"to": 7}, "b"), ({"to": False, "text": " "}, "b"),
    ({"to": "buyers"}, "d"), ({"to": "everyone", "text": ""}, "d"),
    ({"to": "M-000"}, "e"), ({"to": "M-000", "text": "  "}, "e"),
    ({"to": "M-14b"}, "f"), ({"to": "M-14b", "text": " "}, "f"),
    ({"to": "B-d75"}, "g"), ({"to": "B-d75", "text": "\n"}, "g"),
    ({"to": "M-849"}, "h"), ({"to": "managers", "text": None}, "h"), ({"to": "M-849", "text": ""}, "h"),
    ({"to": "M-849", "message": "hi"}, "h"),
    ({"to": "M-849", "text": " "}, "i"), ({"to": "managers", "text": "\n\t \r"}, "i"),
    ({"to": "M-849", "text": "\u00a0\u2003\u3000\x1c\x85"}, "i"),
])
def test_the_first_check_that_applies_decides(args, letter):
    """2.3: the checks in order; when both arguments are bad, the recipient's error is reported."""
    m = market("within_roles")
    raw = args.get("to")
    assert act(m, "M-14b", **args) == (False, refusal(letter, "within_roles", "manager", raw, raw))
    assert m.messages == []


def test_the_blank_text():
    assert refusal("i", "all", "buyer") == "text is blank; no message was sent"


@pytest.mark.parametrize("value, recorded", [
    (0, "0"), (False, "False"), (3.0, "3.0"), ({"a": [1, None]}, "{'a': [1, None]}"), ([], "[]"),
    (" hi \n", " hi \n"), ("a\r\n\nb", "a\r\n\nb"),
])
def test_the_text_is_converted_and_kept_as_sent(value, recorded):
    """2.1: 0, false, lists and objects count as given; 2.5: TEXT, not stripped, line breaks kept."""
    m = market("all")
    assert act(m, "B-c53", to="M-849", text=value) == (True, "message sent to M-849")
    assert m.messages == [Message(3, 2, "B-c53", "M-849", recorded)]


# 2.5 and 2.6: a sent message


def test_a_sent_message_is_recorded_in_one_sequence():
    m = market("all")
    m.day, m.round = 4, 3
    before = copy.deepcopy(m.participants)
    assert act(m, "M-849", to="everyone", text="one", extra=1) == (True, "message sent to everyone")
    assert act(m, "B-d75", to="M-849", text="two") == (True, "message sent to M-849")
    assert act(m, "B-d75", to="buyers", text="three") == (True, "message sent to all buyers")
    assert act(m, "M-14b", to="managers", text="four") == (True, "message sent to all managers")
    assert m.messages == [Message(4, 3, "M-849", "everyone", "one"), Message(4, 3, "B-d75", "M-849", "two"),
                          Message(4, 3, "B-d75", "buyers", "three"), Message(4, 3, "M-14b", "managers", "four")]
    assert m.participants == before  # 2.6: no credits move, nobody is notified
    assert m.actor is None
    assert [(r["sender"], r["to"], r["sent"], r["text"], r["day"], r["round"]) for r in log(m, "message")] == [
        ("M-849", "everyone", "one", "one", 4, 3), ("B-d75", "M-849", "two", "two", 4, 3),
        ("B-d75", "buyers", "three", "three", 4, 3), ("M-14b", "managers", "four", "four", 4, 3)]
    assert len(m.log.__self__) == 4  # 1: no notification, no money record


def test_the_cut_at_message_chars():
    m = market("managers")
    note = fenced("### 2.5 A sent message", 1)
    assert note == " (cut to its first {message_chars} characters)"
    assert fenced("### 2.5 A sent message", 0) == "message sent to {label}{cut}"
    cut = "message sent to all managers (cut to its first 500 characters)"
    assert act(m, "M-14b", to="managers", text="x" * 500) == (True, "message sent to all managers")
    assert act(m, "M-14b", to="managers", text="x" * 501) == (True, cut)
    assert act(m, "M-14b", to="managers", text=" " * 600 + "hi") == (True, cut)  # blank is checked before the cut
    assert act(m, "M-14b", to="M-849", text="\n" + "y" * 498 + " z") == (
        True, "message sent to M-849 (cut to its first 500 characters)")
    assert [msg.text for msg in m.messages] == ["x" * 500, "x" * 500, " " * 500, "\n" + "y" * 498 + " "]
    assert actions.result_line("send_message", True, cut) == (
        "- send_message: OK: message sent to all managers (cut to its first 500 characters)")
    record = log(m, "message")[2]
    assert (record["sent"], record["text"]) == (" " * 600 + "hi", " " * 500)  # 7: as sent and as recorded


def test_the_cut_follows_the_parameter_and_the_singular_rule():
    m = market("all", message_chars=1)
    assert act(m, "B-c53", to="everyone", text="ab") == (
        True, "message sent to everyone (cut to its first 1 character)")
    assert act(m, "B-c53", to="everyone", text="a") == (True, "message sent to everyone")
    m = market("all", message_chars=3)
    assert act(m, "B-c53", to="B-d75", text=12345) == (True, "message sent to B-d75 (cut to its first 3 characters)")
    assert m.messages[0].text == "123"


def test_a_trial_on_a_copy_gives_the_same_result_and_logs_nothing():
    """2.2, item 3 (R1): the checks read only the mode, the participants and the arguments."""
    m = market("buyers")
    shadow = copy.deepcopy(m)
    args = {"to": "B-d75", "text": "z" * 700}
    assert actions.run(shadow, "B-c53", "send_message", args) == actions.run(m, "B-c53", "send_message", args)
    assert shadow.messages == m.messages and len(m.log.__self__) == 1 and shadow.log is None


# 3.1: which conversations, in order


def test_conversations_by_mode_and_role():
    """The table of 3.1; within a role the ids are in code-point order, not seat order."""
    def keys(mode, pid):
        m = market(mode, buyers=("B-d75", "B-c53"), managers=("M-f8c", "M-14b", "M-849"))
        return conversations(m, m.participants[pid])

    for mode in ("off", "managers"):
        assert keys(mode, "B-c53") == []
    for mode in ("off", "buyers"):
        assert keys(mode, "M-849") == []
    for mode in ("buyers", "within_roles"):
        assert keys(mode, "B-d75") == [("B-c53", "B-d75"), "buyers"]
    for mode in ("managers", "within_roles"):
        assert keys(mode, "M-849") == [("M-14b", "M-849"), ("M-849", "M-f8c"), "managers"]
    assert keys("all", "B-d75") == [("B-d75", "M-14b"), ("B-d75", "M-849"), ("B-d75", "M-f8c"), ("B-c53", "B-d75"),
                                    "buyers", "everyone"]
    assert keys("all", "M-849") == [("M-14b", "M-849"), ("M-849", "M-f8c"), ("B-c53", "M-849"), ("B-d75", "M-849"),
                                    "managers", "everyone"]


def test_headings_and_empty_conversations():
    """3.1's headings; every conversation is listed in every view, even when empty (3.2)."""
    headings = {row[0]: row[1].strip("`") for row in rows("| conversation | heading |")}
    assert fenced("A conversation with no messages is one line:") == "{heading}: (no messages yet)"
    private = headings["private, with participant X"]
    expected = {
        ("all", "M-849"): ["--- Messages ---"] + [private.replace("{X}", x) for x in ("M-14b", "M-f8c", "B-c53",
                                                                                      "B-d75")]
        + [headings["all managers"], headings["everyone"]],
        ("all", "B-c53"): ["--- Messages ---"] + [private.replace("{X}", x) for x in ("M-14b", "M-849", "M-f8c",
                                                                                      "B-d75")]
        + [headings["all buyers"], headings["everyone"]],
        ("managers", "M-14b"): ["--- Messages between managers ---", private.replace("{X}", "M-849"),
                                private.replace("{X}", "M-f8c"), headings["all managers"]],
        ("within_roles", "M-14b"): ["--- Messages between managers ---", private.replace("{X}", "M-849"),
                                    private.replace("{X}", "M-f8c"), headings["all managers"]],
        ("buyers", "B-d75"): ["--- Messages between buyers ---", private.replace("{X}", "B-c53"),
                              headings["all buyers"]],
        ("within_roles", "B-d75"): ["--- Messages between buyers ---", private.replace("{X}", "B-c53"),
                                    headings["all buyers"]],
    }
    for (mode, pid), lines in expected.items():
        assert view(market(mode), pid) == "\n\n".join([lines[0]] + [f"{h}: (no messages yet)" for h in lines[1:]])


def test_a_conversation_holds_its_messages_in_the_order_sent():
    m = market("all")
    for sender, to in (("M-14b", "M-849"), ("B-c53", "everyone"), ("M-849", "M-14b"), ("M-849", "managers"),
                       ("B-c53", "M-849"), ("M-14b", "managers"), ("M-f8c", "M-14b"), ("B-d75", "buyers"),
                       ("M-849", "B-c53"), ("M-14b", "everyone")):
        say(m, sender, to)
    assert thread(m, ("M-14b", "M-849")) == [0, 2]
    assert thread(m, ("B-c53", "M-849")) == [4, 8]
    assert thread(m, ("M-14b", "M-f8c")) == [6]
    assert thread(m, ("B-c53", "B-d75")) == []
    assert thread(m, "managers") == [3, 5] and thread(m, "buyers") == [7] and thread(m, "everyone") == [1, 9]


def test_two_of_a_role_keep_both_conversations_and_an_out_of_business_one_is_listed():
    """1: with exactly 2 members the group and the private conversation are separate; an out-of-business
    participant can still be sent messages and its conversations stay in the others' views."""
    m = market("managers", managers=("M-14b", "M-849"))
    m.participants["M-849"].out_of_business = True
    assert act(m, "M-14b", to="M-849", text="private") == (True, "message sent to M-849")
    assert act(m, "M-14b", to="managers", text="group") == (True, "message sent to all managers")
    assert view(m, "M-14b") == ("--- Messages between managers ---\n\n"
                                "Your conversation with M-849:\n  [day 3, round 2] you -> M-849: private\n\n"
                                "Conversation among all managers:\n  [day 3, round 2] you -> all managers: group")


# 3.2: the section's layout


def rebuild(m, reader, text):
    """Fill a market with what a Messages section shows: each message line, as many earlier messages as each
    summary covers (the stated number of them new), the summary stored, and the reader's read state."""
    p, key, other, new = m.participants[reader], None, None, []
    for line in text.splitlines()[1:]:
        if not line.startswith("  "):
            if line:
                heading = line.split(":")[0]
                other = heading.removeprefix("Your conversation with ")
                key = GROUPS.get(heading) or tuple(sorted((reader, other)))
                if key in GROUPS.values():
                    other = next(q.id for q in m.participants.values() if q.id != reader and q.role + "s" == key
                                 or key == "everyone" and q.id != reader)
            continue
        group = key if isinstance(key, str) else None
        found = SUMMARY.fullmatch(line)
        if found:
            covers, n_new = int(found[1]), int(found[2] or 0)
            new += range(len(m.messages), len(m.messages) + n_new)
            say(m, other, group or reader, "earlier", n=covers)
            m.summaries[key] = (found[3], covers)
            continue
        day, rnd, src, dst, mark, body = LINE.fullmatch(line).groups()
        if mark:
            new.append(len(m.messages))
        say(m, reader if src == "you" else src, group or (reader if dst == "you" else dst), body, int(day), int(rnd))
    p.seen, p.unseen = len(m.messages), new


def test_the_example_section_of_the_spec():
    """3.2's example, the whole section from its title to the next section's title."""
    block = fenced("Example (mode `managers`, M-14b's view, day 11 round 1")
    expected = block.removesuffix("\n\n--- Your accounts so far (credits) ---")
    assert expected != block and expected.count("(NEW)") == 1
    m = market("managers")
    rebuild(m, "M-14b", expected)
    assert [len(thread(m, key)) for key in conversations(m, m.participants["M-14b"])] == [14, 15, 22]
    assert view(m, "M-14b", Stub()) == expected
    assert (m.participants["M-14b"].seen, m.participants["M-14b"].unseen) == (51, [])
    assert view(m, "M-14b", Stub()) == expected.replace(" (NEW)", "")


@pytest.mark.parametrize("name, reader", [
    ("view_manager_all_M-7f8_day28_round3.txt", "M-7f8"), ("view_buyer_all_B-42c_day23_round2.txt", "B-42c")])
def test_the_messages_sections_of_the_complete_views(name, reader):
    """README.md, acceptance check 3: every line of the examples' Messages sections, in order and spacing."""
    whole = spec("examples/" + name)
    expected = whole[whole.index("--- Messages ---"):whole.index("\n\n--- Your accounts so far (credits) ---")]
    ids = sorted(set(re.findall(r"Your conversation with (\S+?):", expected)) | {reader})
    m = market("all", buyers=[i for i in ids if i[0] == "B"], managers=[i for i in ids if i[0] == "M"])
    rebuild(m, reader, expected)
    assert "Summary of the 10 earlier messages (written automatically; you are " + reader in expected
    assert view(m, reader, Stub()) == expected


def test_the_layout_inside_a_conversation():
    """3.2: with no summary the first message line follows the heading directly; with one, the summary line does
    and an empty line precedes the first message line; one empty line before every other message line."""
    m = market("buyers", recent=2)
    say(m, "B-d75", "B-c53", "one", 1, 1)
    say(m, "B-c53", "B-d75", "two", 1, 2)
    m.participants["B-c53"].seen = 2
    assert view(m, "B-c53") == ("--- Messages between buyers ---\n\n"
                                "Your conversation with B-d75:\n"
                                "  [day 1, round 1] B-d75 -> you: one\n\n"
                                "  [day 1, round 2] you -> B-d75: two\n\n"
                                "Conversation among all buyers: (no messages yet)")
    say(m, "B-d75", "B-c53", "three", 2, 1)
    title, items = section(m, m.participants["B-c53"], None, None)
    assert title == "--- Messages between buyers ---" and items == [
        "Your conversation with B-d75:\n"
        "  Summary of the 2 earlier messages (written automatically; you are B-c53 in it): "
        "(mock summary of 2 messages)\n\n"
        "  [day 2, round 1] B-d75 -> you (NEW): three",
        "Conversation among all buyers: (no messages yet)"]


# 3.3: the message line


def test_the_message_line():
    assert fenced("### 3.3 The message line") == "  [day {d}, round {r}] {src} -> {dst}{new}: {text}"
    m = market("all")
    say(m, "M-849", "B-c53", "to you", 2, 3)
    say(m, "B-c53", "M-849", "from you", 12, 1)
    say(m, "M-849", "managers", "to the managers", 3, 1)
    say(m, "B-c53", "buyers", "to the buyers", 3, 2)
    say(m, "B-d75", "buyers", "from a buyer", 3, 2)
    say(m, "M-f8c", "everyone", "to all", 3, 3)
    say(m, "B-c53", "everyone", "   ", 4, 1)
    assert lines_of(m, "B-c53") == [
        "  [day 2, round 3] M-849 -> you (NEW): to you",
        "  [day 12, round 1] you -> M-849: from you",
        "  [day 3, round 2] you -> all buyers: to the buyers",
        "  [day 3, round 2] B-d75 -> all buyers (NEW): from a buyer",
        "  [day 3, round 3] M-f8c -> everyone (NEW): to all",
        "  [day 4, round 1] you -> everyone:    ",  # 2.5: a whitespace-only text is shown as it is
    ]
    assert lines_of(m, "M-849") == [
        "  [day 2, round 3] you -> B-c53: to you",
        "  [day 12, round 1] B-c53 -> you (NEW): from you",
        "  [day 3, round 1] you -> all managers: to the managers",
        "  [day 3, round 3] M-f8c -> everyone (NEW): to all",
        "  [day 4, round 1] B-c53 -> everyone (NEW):    ",
    ]


@pytest.mark.parametrize("text, line", [
    ("a\n\nb", "a  b"), ("abc\n", "abc"), ("abc\n\n", "abc "), ("\nabc", " abc"), ("a\r\nb", "a b"),
    ("a\rb\x0bc\x0cd\x1ce\x1df\x1eg\x85h\u2028i\u2029j", "a b c d e f g h i j"), ("a\n\rb", "a  b"),
    (" a\tb  ", " a\tb  "), ("a\x1fb\u00a0", "a\x1fb\u00a0"),
])
def test_a_text_on_one_line(text, line):
    assert one_line(text) == line
    m = market("managers")
    say(m, "M-849", "M-14b", text)
    assert lines_of(m, "M-14b") == [f"  [day 1, round 1] M-849 -> you (NEW): {line}"]
    say(m, "M-849", "M-14b", "x", n=2)
    m.cfg["messaging"]["recent"] = 2
    assert request(m, ("M-14b", "M-849"), 0, 2)[1]["content"].endswith(
        f"\n[day 1, round 1] M-849 -> M-14b: {line}\n[day 1, round 1] M-849 -> M-14b: x")


# 3.4: new messages


def marks(m, pid, summarizer=None, tracker=None):
    """Each line of a participant's section cut to what the marks decide: `text` or `text (NEW)`, or the summary
    line's parenthesis."""
    out = []
    for line in lines_of(m, pid, summarizer, tracker):
        found = LINE.fullmatch(line)
        out.append(found[6] + (found[5] or "") if found else line[line.index("(written"):line.index("): ") + 1])
    return out


def test_new_marks_across_views():
    m = market("within_roles")
    say(m, "M-849", "M-14b", "a")
    say(m, "M-14b", "M-849", "b")
    say(m, "M-f8c", "managers", "c")
    assert marks(m, "M-14b") == ["a (NEW)", "b", "c (NEW)"]
    assert marks(m, "M-14b") == ["a", "b", "c"]
    say(m, "M-14b", "managers", "d")
    say(m, "M-849", "managers", "e")
    say(m, "B-c53", "buyers", "not a manager's")
    assert marks(m, "M-14b") == ["a", "b", "c", "d", "e (NEW)"]
    assert marks(m, "M-849") == ["a", "b (NEW)", "c (NEW)", "d (NEW)", "e"]
    assert marks(m, "M-14b") == ["a", "b", "c", "d", "e"]
    p = m.participants["M-14b"]
    assert (p.seen, p.unseen) == (6, [])
    assert [is_new(m, p, i) for i in range(6)] == [False] * 6
    q = m.participants["M-f8c"]  # no view yet: everything someone else sent is new
    assert [is_new(m, q, i) for i in range(6)] == [True, True, False, True, True, True]


def test_a_lost_view_makes_its_new_messages_new_again():
    """3.4; rounds.md, 10.1: the read state goes back, the summary written for the lost view stays."""
    m = market("managers", recent=2)
    say(m, "M-849", "managers", "a")
    assert marks(m, "M-14b") == ["a (NEW)"]
    say(m, "M-849", "managers", "b")
    say(m, "M-f8c", "managers", "c")
    p = m.participants["M-14b"]
    before = (p.seen, list(p.unseen))
    stub = Stub(Reply(text="S", cost=0.5))
    lost = marks(m, "M-14b", stub, Tracker())
    assert lost == ["(written automatically, 1 of them new; you are M-14b in it)", "c (NEW)"]
    p.seen, p.unseen = before  # view.undo
    assert marks(m, "M-14b", stub, Tracker()) == lost  # no second call: the stub has no more replies
    assert m.summaries == {"managers": ("S", 2)} and m.counts["summarizer_dollars"] == 0.5
    assert marks(m, "M-14b", stub, Tracker()) == ["(written automatically; you are M-14b in it)", "c"]


def test_messages_behind_the_placeholder_stay_new_until_a_view_shows_them():
    """3.4, 4.4: the example text of 4.4, then the same reader's later views."""
    m = market("all")
    say(m, "M-849", "everyone", "old", n=12)
    reader = m.participants["B-c53"]
    reader.seen = 12
    say(m, "M-14b", "everyone", "more", n=8)
    say(m, "M-f8c", "everyone", "more 8")
    failing = Stub(default=CallFailed("HTTP 500 for model 'x': no"))
    items = section(m, reader, failing, Tracker())[1]
    assert items[-1] == fenced("### 4.4 Hidden messages after a failure")
    assert (reader.seen, reader.unseen) == (21, list(range(12, 20)))
    assert m.summaries == {} and len(failing.requests) == 4
    assert section(m, reader, failing, Tracker())[1][-1] == items[-1].replace(" (NEW)", "")
    assert (reader.seen, reader.unseen) == (21, list(range(12, 20))) and len(failing.requests) == 8
    say(m, "B-c53", "everyone", "mine")
    say(m, "B-d75", "everyone", "newest")
    shown_now = section(m, reader, Stub(Reply(text="All of it.")), Tracker())[1][-1]
    assert shown_now == (
        "Conversation among all participants:\n"
        "  Summary of the 20 earlier messages (written automatically, 8 of them new; you are B-c53 in it): "
        "All of it.\n\n"
        "  [day 1, round 1] M-f8c -> everyone: more 8\n\n"
        "  [day 1, round 1] you -> everyone: mine\n\n"
        "  [day 1, round 1] B-d75 -> everyone (NEW): newest")
    assert (reader.seen, reader.unseen) == (23, [])
    assert "(written automatically; you are B-c53 in it)" in section(m, reader, Stub(), None)[1][-1]


def test_the_summary_line():
    assert fenced("### 3.5 The summary line") == (
        "  Summary of the {E} earlier message{s} (written automatically{newcount}{you}): {summary}")
    assert fenced("### 3.5 The summary line", 1) == "(summary unavailable; the earlier messages are not shown)"
    examples = ["(written automatically; you are M-7f8 in it)",
                "(written automatically, 1 of them new; you are M-849 in it)",
                "(written automatically, 8 of them new)"]
    assert all(f"`{example}`" in " ".join(spec().split()) for example in examples)
    m = market("managers", managers=("M-7f8", "M-849"), recent=3)
    say(m, "M-7f8", "M-849", "x", n=2)
    view(m, "M-849")
    say(m, "M-7f8", "M-849", "x")
    say(m, "M-849", "M-7f8", "y")
    assert marks(m, "M-7f8") == [examples[0], "y (NEW)"]
    assert marks(m, "M-849") == [examples[1], "y"]
    say(m, "M-7f8", "M-849", "z", n=9)
    assert lines_of(m, "M-849", Stub(default=CallFailed("no")), Tracker())[0] == (
        f"  Summary of the 12 earlier messages {examples[2]}: (summary unavailable; the earlier messages are not "
        "shown)")
    assert lines_of(m, "M-7f8")[0] == ("  Summary of the 12 earlier messages (written automatically; you are M-7f8 "
                                       "in it): (mock summary of 12 messages)")


# 4.1 to 4.3: which messages are summarized, and when a summary is written


@pytest.mark.parametrize("recent, count, cut", [
    (10, 1, 0), (10, 10, 0), (10, 11, 10), (10, 20, 10), (10, 21, 20), (10, 25, 20), (10, 31, 30),
    (2, 2, 0), (2, 3, 2), (2, 4, 2), (2, 5, 4), (3, 100, 99)])
def test_which_messages_are_summarized(recent, count, cut):
    m = market("buyers", recent=recent)
    say(m, "B-d75", "buyers", n=count)
    lines = lines_of(m, "B-c53")
    if cut:
        assert lines[0].startswith(f"  Summary of the {cut} earlier messages (written automatically, {cut} of them "
                                   f"new; you are B-c53 in it): (mock summary of {cut} messages)")
        assert m.summaries == {"buyers": (f"(mock summary of {cut} messages)", cut)}
    else:
        assert m.summaries == {}
    assert len(lines) == count - cut + bool(cut) and 1 <= count - cut <= recent


def numbers(content):
    """The counts a request states: (P, K, T) of an update or (N,) of a first summary, and its word budget."""
    budget = int(re.search(r"At most (\d+) words\. Reply with the summary only\.", content)[1])
    update = re.search(r"its first (\d+) messages? and the (\d+) messages? that came after\. Write one summary of "
                       r"all (\d+) messages,", content)
    if update:
        return tuple(int(x) for x in update.groups()), budget
    return (int(re.search(r"\((\d+) messages?\):\n", content)[1]),), budget


def test_the_summary_schedule():
    """4.3: no call while the stored summary covers the summarized part; a first summary, then updates that carry
    the stored text and only the messages after it; a jump of several batches is one summary."""
    m = market("managers")
    stub = Stub(default=Reply(text="S"))
    say(m, "M-849", "managers", "m", n=10)
    for pid in MANAGERS:
        view(m, pid, stub, Tracker())
    assert stub.requests == [] and m.summaries == {}
    say(m, "M-849", "managers", "m11")
    stub.default = Reply(text="first")
    for pid in MANAGERS:  # the first view makes the call, the round's later views reuse it
        assert lines_of(m, pid, stub, Tracker())[0].endswith(f"you are {pid} in it): first")
    assert len(stub.requests) == 1 and m.summaries == {"managers": ("first", 10)}
    assert numbers(stub.requests[0][1]["content"]) == ((10,), 190)
    say(m, "M-14b", "managers", "more", n=9)
    view(m, "M-f8c", stub, Tracker())
    assert len(stub.requests) == 1  # 20 messages: the first 10 are summarized still
    say(m, "M-14b", "managers", "m21")
    stub.default = Reply(text="second")
    view(m, "M-14b", stub, Tracker())
    content = stub.requests[1][1]["content"]
    assert len(stub.requests) == 2 and m.summaries == {"managers": ("second", 20)}
    assert numbers(content) == ((10, 10, 20), 200)
    assert "\n\nPrevious summary:\nfirst\n\nConversation among all managers, the new messages:\n" in content
    assert content.count("\n[day 1, round 1] ") == 10 and content.count("-> all managers: m11\n") == 1
    assert "m21" not in content and content.endswith("M-14b -> all managers: more")
    say(m, "M-f8c", "managers", "jump", n=25)  # 46 messages: the cut jumps from 20 to 40
    stub.default = Reply(text="third")
    assert lines_of(m, "M-849", stub, Tracker())[0].endswith("you are M-849 in it): third")
    assert numbers(stub.requests[2][1]["content"]) == ((20, 20, 40), 200)
    assert len(lines_of(m, "M-14b", stub, Tracker())) == 7 and len(stub.requests) == 3
    assert [(r["conversation"], r["covers"], r["text"]) for r in log(m, "summary")] == [
        ("managers", 10, "first"), ("managers", 20, "second"), ("managers", 40, "third")]
    assert m.counts["summaries_written"] == 3 and m.counts["summarizer_calls"] == 3


def test_a_first_summary_after_a_jump_covers_all_of_it():
    m = market("all")
    say(m, "B-c53", "M-849", "early", n=5)
    view(m, "M-849", Stub(), None)
    say(m, "M-849", "B-c53", "late", n=20)
    stub = Stub(Reply(text="  One\n\n summary.\t"))
    lines = lines_of(m, "B-c53", stub, Tracker())
    assert lines[0] == ("  Summary of the 20 earlier messages (written automatically, 15 of them new; you are B-c53 "
                        "in it): One summary.")
    assert len(lines) == 6 and numbers(stub.requests[0][1]["content"]) == ((20,), 200)
    assert m.summaries == {("B-c53", "M-849"): ("One summary.", 20)}  # 4.3 item 3: one space, trimmed


def test_the_conversations_of_one_view_are_summarized_in_their_order():
    """4.3: within one view, one conversation after another in 3.1's order; one summary per conversation (4.2)."""
    m = market("all", recent=2)
    for to in ("everyone", "buyers", "B-d75", "M-f8c", "M-14b"):
        say(m, "B-c53", to, n=3)
    stub = Stub(default=Reply(text="S"))
    view(m, "B-d75", stub, Tracker())
    assert [r["conversation"] for r in log(m, "summary")] == [("B-c53", "B-d75"), "buyers", "everyone"]
    view(m, "B-c53", stub, Tracker())
    assert [r["conversation"] for r in log(m, "summary")][3:] == [("B-c53", "M-14b"), ("B-c53", "M-f8c")]
    assert [r["conversation"] for r in log(m, "summarizer_call")] == [
        ("B-c53", "B-d75"), "buyers", "everyone", ("B-c53", "M-14b"), ("B-c53", "M-f8c")]
    assert len(stub.requests) == 5 and set(m.summaries.values()) == {("S", 2)}


# 4.3 item 4 and 5.3: failures


def test_an_attempt_fails_on_a_failed_call_a_cut_reply_or_an_empty_text():
    """5.3: each of the four ways an attempt fails, then the placeholder; nothing is stored."""
    m = market("managers", recent=2)
    say(m, "M-849", "M-14b", "x", n=3)
    stub = Stub(CallFailed("giving up on 'x' after 6 attempts: HTTP 500", failed_cost=0.25),
                Reply(text="A fine summary.", finish_reason="length", cost=0.1, input_tokens=7, output_tokens=3),
                Reply(text="Filtered.", finish_reason="content_filter", cost=0.2, cost_known=False),
                Reply(text=" <think>only thoughts</think>\n ", finish_reason="stop", cost=0.4))
    tracker = Tracker()
    assert lines_of(m, "M-14b", stub, tracker) == [
        "  Summary of the 2 earlier messages (written automatically, 2 of them new): "
        "(summary unavailable; the earlier messages are not shown)",
        "  [day 1, round 1] M-849 -> you (NEW): x"]
    assert len(stub.requests) == 4 and tracker.checks == 4  # the cap is checked before every attempt
    assert all(r == stub.requests[0] for r in stub.requests)  # the same request each time
    assert m.summaries == {} and log(m, "summary") == []
    assert [(r["conversation"], r["covers"], r["error"], r["cost"]) for r in log(m, "summary_failed")] == [
        (("M-14b", "M-849"), 2, "giving up on 'x' after 6 attempts: HTTP 500", 0.0),
        (("M-14b", "M-849"), 2, "finish reason length", 0.1),
        (("M-14b", "M-849"), 2, "finish reason content_filter", 0.2),
        (("M-14b", "M-849"), 2, "empty reply", 0.4)]
    calls = log(m, "summarizer_call")
    assert [r["messages"] for r in calls] == stub.requests
    assert [r["covers"] for r in calls] == [2] * 4 and calls[0]["spec"] == m.cfg["models"]["summarizer"]
    assert (calls[0]["error"], calls[0]["failed_cost"]) == ("giving up on 'x' after 6 attempts: HTTP 500", 0.25)
    assert calls[1]["reply"]["text"] == "A fine summary." and calls[1]["reply"]["finish_reason"] == "length"
    assert calls[1]["reply"]["input_tokens"] == 7 and calls[3]["reply"]["cost"] == 0.4
    assert m.counts == {"summarizer_calls": 3, "summarizer_input_tokens": 7, "summarizer_output_tokens": 3,
                        "summarizer_dollars": pytest.approx(0.7), "calls_unknown_cost": 1, "summaries_failed": 4}
    assert m.lost_turns == 0  # never counted toward the lost-turn abort


def test_a_failed_attempt_is_retried_at_once_up_to_four_in_all():
    m = market("managers", recent=2)
    say(m, "M-849", "managers", "x", n=3)
    stub = Stub(Reply(text=""), CallFailed("no"), Reply(text="x", finish_reason="length"),
                Reply(text="<think>plan</think> The   fourth\n- try. ", finish_reason="stop", cost=0.3))
    tracker = Tracker()
    assert lines_of(m, "M-f8c", stub, tracker)[0] == (
        "  Summary of the 2 earlier messages (written automatically, 2 of them new; you are M-f8c in it): "
        "The fourth - try.")
    assert m.summaries == {"managers": ("The fourth - try.", 2)} and tracker.checks == 4
    assert [(r["covers"], r["text"], r["cost"]) for r in log(m, "summary")] == [(2, "The fourth - try.", 0.3)]
    assert m.counts["summaries_failed"] == 3 and m.counts["summaries_written"] == 1
    assert [r["type"] for r in m.log.__self__] == ["summarizer_call", "summary_failed"] * 3 + [
        "summarizer_call", "summary"]


def test_after_four_failures_the_next_view_tries_again_and_the_stored_summary_stays():
    """4.3 item 4: the previous stored summary stays but is not shown; the next view of any reader retries, also
    another reader's in the same round, with an update of the summary that stayed."""
    m = market("managers", recent=2)
    say(m, "M-849", "managers", "x", n=3)
    view(m, "M-14b", Stub(Reply(text="old")), Tracker())
    say(m, "M-f8c", "managers", "y", n=2)
    failing = Stub(default=Reply(text=""))
    assert lines_of(m, "M-14b", failing, Tracker())[0] == (
        "  Summary of the 4 earlier messages (written automatically, 1 of them new): " + messaging.PLACEHOLDER)
    assert len(failing.requests) == 4 and m.summaries == {"managers": ("old", 2)}
    assert lines_of(m, "M-849", failing, Tracker())[0].endswith("1 of them new): " + messaging.PLACEHOLDER)
    assert len(failing.requests) == 8  # up to 4 attempts per reader view
    working = Stub(Reply(text="new"))
    assert lines_of(m, "M-f8c", working, Tracker())[0].endswith("3 of them new; you are M-f8c in it): new")
    assert numbers(working.requests[0][1]["content"]) == ((2, 2, 4), 100)
    assert "\nPrevious summary:\nold\n" in working.requests[0][1]["content"]
    assert working.requests[0] == failing.requests[0] and m.summaries == {"managers": ("new", 4)}
    assert lines_of(m, "M-14b", Stub(), None)[0].endswith("1 of them new; you are M-14b in it): new")


def test_the_cost_cap_stops_before_a_call_and_is_no_failed_attempt():
    """5.3: checked before each call, every attempt included; no placeholder, nothing counted as failed."""
    m = market("managers", recent=2)
    say(m, "M-849", "managers", "x", n=3)
    stub = Stub(default=Reply(text=""))
    with pytest.raises(models.CostCapReached):
        section(m, m.participants["M-14b"], stub, Tracker(stop=1))
    assert stub.requests == [] and m.log.__self__ == [] and m.counts == {}
    with pytest.raises(models.CostCapReached):
        section(m, m.participants["M-14b"], stub, Tracker(stop=3))
    assert len(stub.requests) == 2 and m.counts["summaries_failed"] == 2 and m.summaries == {}
    assert m.participants["M-14b"].seen == 0  # the view was not built


# 4.5: how a summary is shown


@pytest.mark.parametrize("text, length, tail", [
    ("A. " * 900, 2000, " A. A."), ("word " * 900, 2000, "word word\u2026"),
    ("x" * 1500 + "; " + "y" * 600, 1501, "xxx."), ("x" * 999 + ". " + "y" * 1500, 1000, "xxx."),
    ("x" * 998 + ". " + "y" * 1500, 2000, "yyy\u2026"),
    ("x" * 1500 + ';" ' + "y" * 600, 1502, 'xx."'), ("x" * 1500 + ";) " + "y" * 600, 1502, "xx.)"),
    ("x" * 1500 + "!\u201d " + "y" * 600, 1502, "x!\u201d"), ("x" * 1500 + "?\u2019 " + "y" * 600, 1502, "x?\u2019"),
    ("x" * 1500 + ".' " + "y" * 600, 1502, "xx.'"), ("x" * 1500 + ";\u2019 " + "y" * 600, 1502, "x.\u2019"),
    ("x" * 1999 + ". " + "y" * 9, 2000, "xxx."),  # the space is the last character of the cut
    ("x" * 1200 + ". " + "y" * 798 + ". z", 1201, "xxx."),  # a mark at the cut's end has no next character
    ("x" * 1200 + ". y" + ".y" * 500, 1201, "xxx."),  # a mark with no space after it ends no sentence
    ("x" * 1200 + ".] " + "y" * 900, 2000, "yyy\u2026"),  # `]` is no closing character
    ("a; b; " + "x" * 1500 + "? " + "y" * 600, 1507, "xxx?"),  # only the last end's `;` is replaced
    ("x" * 1990 + " " * 5 + "y" * 600, 2000, "yyy\u2026"),  # whitespace runs are one space before the cut
    ("x" * 1998 + " " + "y" * 600, 1999, "xxx\u2026"),  # the hard cut's trailing whitespace is removed
])
def test_a_long_summary_is_cut_at_a_sentence_end(text, length, tail):
    out = shown(text, 2000)
    assert (len(out), out[-len(tail):]) == (length, tail)


def test_a_summary_within_the_cap_is_shown_whole_on_one_line():
    bulleted = "  M-849:\n- Asked  for 20.\n\nB-c53:\n- Refused.\t"
    assert shown(bulleted, 2000) == "M-849: - Asked for 20. B-c53: - Refused."
    assert shown("x" * 2000, 2000) == "x" * 2000 and shown("ab. cd", 50) == "ab. cd"
    assert shown("x" * 30 + ". " + "y" * 30, 50) == "x" * 30 + "."
    assert shown("x" * 23 + ". " + "y" * 30, 50) == "x" * 23 + ". " + "y" * 24 + "\u2026"  # end 24 < floor(50 / 2)
    assert shown("x" * 24 + ". " + "y" * 30, 51) == "x" * 24 + "."  # end 25 = floor(51 / 2)


def test_the_view_cuts_the_summary_and_the_update_carries_it_uncut():
    """4.5, 5.1: `{prior}` is the stored text, not cut to messaging.summary_chars."""
    m = market("buyers", recent=2, summary_chars=50)
    say(m, "B-d75", "buyers", "x", n=3)
    long = "First point here. " * 5 + "And a last one that is cut"
    stub = Stub(Reply(text=long), Reply(text="short"))
    assert lines_of(m, "B-c53", stub, Tracker())[0].endswith(
        "you are B-c53 in it): First point here. First point here.")
    assert m.summaries == {"buyers": (long, 2)}
    say(m, "B-d75", "buyers", "x", n=2)
    view(m, "B-c53", stub, Tracker())
    assert f"\nPrevious summary:\n{long}\n\nConversation among all buyers, the new messages:\n" in (
        stub.requests[1][1]["content"])


# 5.1: the request


def test_the_system_message_and_the_audience_sentences():
    system = fenced("The system message is the same in every mode:")
    assert messaging.SYSTEM == system and "\n" not in system
    audience = {row[0]: row[1].strip("`") for row in rows("| conversation | `{audience}` |")}
    m = market("all", recent=2)
    cases = {"managers": ("all managers", "Conversation among all managers"),
             "buyers": ("all buyers", "Conversation among all buyers"),
             "everyone": ("everyone", "Conversation among all participants")}
    for key, (name, label) in cases.items():
        say(m, "M-849" if key != "buyers" else "B-c53", key, n=2)
        system_message, user = request(m, key, 0, 2)
        assert system_message == {"role": "system", "content": system} and user["role"] == "user"
        assert user["content"].startswith(audience[name] + " Summarize these messages. ")
        assert f"\n\n{label} (2 messages):\n[day 1, round 1] " in user["content"]
    pairs = {("M-849", "M-f8c"): "M-849 and M-f8c", ("B-c53", "B-d75"): "B-c53 and B-d75",
             ("B-d75", "M-f8c"): "M-f8c and B-d75"}
    for key, names in pairs.items():
        say(m, key[0], key[1], "one", 2, 3)
        say(m, key[1], key[0], "two", 12, 1)
        content = request(m, key, 0, 2)[1]["content"]
        a, b = names.split(" and ")
        assert content.startswith(audience["private"].replace("{A}", a).replace("{B}", b) + " Summarize")
        assert content.endswith(f"\n\nConversation between {names} (2 messages):\n"
                                f"[day 2, round 3] {key[0]} -> {key[1]}: one\n"
                                f"[day 12, round 1] {key[1]} -> {key[0]}: two")
    for names in pairs.values():
        assert f"`{names}`" in spec()


@pytest.mark.parametrize("n, s, words", [(1, "", 55), (2, "s", 70), (10, "s", 190), (11, "s", 200), (30, "s", 200)])
def test_the_first_summary_request_is_the_template_of_the_spec(n, s, words):
    template = fenced("**First summary** (N messages")
    m = market("all")
    for i in range(n + 3):
        say(m, MANAGERS[i % 3], "everyone", f"text {i}\nline", 1 + i // 3, 1 + i % 3)
    lines = "\n".join(f"[day {1 + i // 3}, round {1 + i % 3}] {MANAGERS[i % 3]} -> everyone: text {i} line"
                      for i in range(n))
    expected = (template.replace("{audience}", "This conversation is among all participants: every buyer and "
                                 "manager reads it.")
                .replace("{W}", str(words)).replace("{conversation}", "Conversation among all participants")
                .replace("{N}", str(n)).replace("{s}", s).replace("{lines}", lines))
    assert "{" not in expected
    assert request(m, "everyone", 0, n) == [{"role": "system", "content": messaging.SYSTEM},
                                            {"role": "user", "content": expected}]


@pytest.mark.parametrize("p, k, sp, sk, words", [
    (1, 1, "", "", 70), (1, 3, "", "s", 100), (2, 1, "s", "", 85), (10, 10, "s", "s", 200), (4, 6, "s", "s", 190)])
def test_the_update_request_is_the_template_of_the_spec(p, k, sp, sk, words):
    template = fenced("**Update** (the stored summary covers P messages")
    m = market("within_roles")
    say(m, "M-14b", "M-849", "covered", n=p)
    for i in range(k + 2):
        say(m, "M-849", "M-14b", f"new {i}", 4, 2)
    prior = "M-849: - Offered {20}. " * 200  # uncut, braces and all
    m.summaries[("M-14b", "M-849")] = (prior, p)
    lines = "\n".join(f"[day 4, round 2] M-849 -> M-14b: new {i}" for i in range(k))
    expected = (template.replace("{lines}", lines).replace("{W}", str(words)).replace("{P}", str(p))
                .replace("{K}", str(k)).replace("{T}", str(p + k)).replace("{sP}", sp).replace("{sK}", sk)
                .replace("{audience}", "This conversation is private to M-14b and M-849: only they read it.")
                .replace("{conversation}", "Conversation between M-14b and M-849").replace("{prior}", prior))
    assert request(m, ("M-14b", "M-849"), p, p + k)[1]["content"] == expected


@pytest.mark.parametrize("name", ["summarizer_request_first_all.json", "summarizer_request_update_all.json"])
def test_the_example_requests(name):
    """README.md, acceptance check 4: each example request from the conversation it shows."""
    expected = json.loads(spec("examples/" + name))
    content = expected[1]["content"]
    m = market("all")
    prior = re.search(r"\nPrevious summary:\n(.*)\n\nConversation", content, flags=re.S)
    covers = 0
    if prior:
        covers = int(re.search(r"its first (\d+) messages", content)[1])
        say(m, "B-c53", "everyone", "earlier", n=covers)
        m.summaries["everyone"] = (prior[1], covers)
    sent = re.findall(r"^\[day (\d+), round (\d+)\] (\S+) -> everyone: (.*)$", content, flags=re.M)
    for day, rnd, sender, text in sent:
        say(m, sender, "everyone", text, int(day), int(rnd))
    say(m, "M-14b", "M-849", "another conversation", n=30)
    say(m, "M-14b", "everyone", "the one shown in full")
    assert len(sent) == 10 and len(thread(m, "everyone")) == covers + 11
    assert request(m, "everyone", covers, covers + 10) == expected
    stub = Stub(Reply(text="ok"))
    view(m, "B-d75", stub, Tracker())  # the view of a reader asks exactly this
    assert stub.requests == [expected]


# 5.5: the free mock summary


def test_the_mock_summary_makes_no_call_and_costs_nothing():
    assert fenced("### 5.5 Under the offline mock") == "(mock summary of {n} messages)"
    m = market("all")
    say(m, "M-849", "everyone", "x", n=25)
    assert lines_of(m, "B-c53", None, None)[0] == (  # no tracker: no cost-cap check is made
        "  Summary of the 20 earlier messages (written automatically, 20 of them new; you are B-c53 in it): "
        "(mock summary of 20 messages)")
    assert m.summaries == {"everyone": ("(mock summary of 20 messages)", 20)}
    assert [(r["conversation"], r["covers"], r["text"], r["cost"]) for r in log(m, "summary")] == [
        ("everyone", 20, "(mock summary of 20 messages)", 0.0)]
    assert log(m, "summarizer_call") == [] and m.counts == {"summaries_written": 1}
    view(m, "M-14b", None, None)
    assert len(log(m, "summary")) == 1  # one summary per conversation, shared by its readers


# The market object


def test_the_state_copies_and_pickles():
    """Summaries and read state are plain data of the market (rounds.md, 6.6; running.md, 5)."""
    import pickle

    m = market("all", recent=2)
    say(m, "M-849", "B-c53", "x", n=3)
    view(m, "B-c53")
    for other in (copy.deepcopy(m), pickle.loads(pickle.dumps(m))):
        assert other.summaries == m.summaries == {("B-c53", "M-849"): ("(mock summary of 2 messages)", 2)}
        assert other.messages == m.messages and other.participants == m.participants


def test_the_source_is_ascii():
    for name in ("market/messaging.py", "tests/test_messaging.py"):
        assert (SPEC.parent / name).read_text(encoding="utf-8").isascii()
