"""The view's market sections (market/view_contracts.py) against spec/view-contracts.md, on markets built by hand.

Every example the spec gives is read from the spec file itself (`spec(first, last)`: its lines, 1-based), so the
comparison is byte for byte; the other expected texts are written out from the section each test names. The last
tests rebuild the market sections of the four example views of spec/examples.
"""

import copy
import pathlib

import pytest

from market import config, state
from market import view_contracts as vc
from market.state import Bid, Contract, Delivery, Dispute, Market, Post, Result, Task

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = (ROOT / "spec" / "view-contracts.md").read_text(encoding="utf-8").split("\n")
PM = "\u00b1"
VALUES_60 = "worth 60 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 11.9, 18.5, 26.7, 36.3, 47.4, 60.0)"
MANAGER_MARK = " | CLOSED: takes no more runs, tests or submissions"
BUYER_MARK = " | CLOSED: settled; it cannot be approved, rejected or ended"


def spec(first, last=None):
    """Lines first to last of spec/view-contracts.md, as one text."""
    return "\n".join(SPEC[first - 1:last or first])


# Building a market by hand


def market(day=1, rnd=1, buyers=("B-c53", "B-d75"), managers=("M-14b", "M-849", "M-f8c"), **overrides):
    m = Market(seed=1, cfg=config.resolve(overrides or None))
    for pid in buyers:
        state.add_participant(m, pid, "buyer")
    for pid in managers:
        state.add_participant(m, pid, "manager")
    m.day, m.round = day, rnd
    return m


def task(m, n, worth=60, due=9, buyer="B-c53"):
    t = m.tasks[f"task-{n}"] = Task(f"task-{n}", buyer, worth, due)
    return t


def post(m, n, day=1, rnd=1, form=None, threshold=None, **kw):
    """An open post of task-n (made here unless it exists), posted in that round."""
    t = m.tasks.get(f"task-{n}") or task(m, n, **kw)
    t.state, t.post = "posted", state.numbered("post", t)
    p = m.posts[t.post] = Post(t.post, t.id, t.buyer, day, rnd, form, threshold)
    return p


def bid(m, n, on, manager, price, threshold=None, status="open"):
    b = m.bids[f"bid-{n}"] = Bid(f"bid-{n}", on.id, manager, price, threshold, status)
    return b


def contract(m, n, manager="M-14b", form="plain", price=20, signed=1, threshold=5.0, deadline=None, **kw):
    """A contract on task-n (made here unless it exists); its deadline is the spec's formula unless given."""
    t = m.tasks.get(f"task-{n}") or task(m, n, **kw)
    t.state, t.contract = "contracted", state.numbered("contract", t)
    c = m.contracts[t.contract] = Contract(
        t.contract, t.id, state.numbered("post", t), t.buyer, manager, form, float(price),
        threshold if form == "escrow" else None, signed, deadline or config.deadline(m.cfg, t.due, signed))
    return c


def run(c, label, start=1, arrival=2, worker="employee", parent=None, mine=(), theirs=()):
    r = Result(label, worker, "refine" if parent else "fresh", parent, start, arrival, 6.0, list(mine), list(theirs))
    c.results.append(r)
    return r


def give(c, label, day, message="", quality=None, **kw):
    """A delivery (plain) or a submission (escrow, under review unless told) of a result, run here unless it
    exists."""
    if state.result(c, label) is None:
        run(c, label, day - 1, day)
    kw.setdefault("outcome", "review" if c.form == "escrow" else None)
    d = Delivery(label, day, 1, message, quality, **kw)
    c.deliveries.append(d)
    return d


def reject(c, label, day, decided, rejection="", dispute=None, **kw):
    return give(c, label, day, outcome="rejected", decided=decided, rejection=rejection, dispute=dispute, **kw)


def approve(c, label, day, decided, **kw):
    d = give(c, label, day, outcome="approved", decided=decided, **kw)
    release(c, decided, "approved")
    return d


def release(c, day, how, paid=None, received=True):
    c.release_day, c.release_how, c.paid = day, how, c.price if paid is None else paid
    c.received = day if received else None


def end(m, c, day):
    t = m.tasks[c.task]
    c.ended, t.state, t.contract = day, "queued", None
    t.ended.append(c.id)


# Reading a view


def items(m, pid, section):
    return getattr(vc, section)(m, m.participants[pid])[1]


def title(m, pid, section):
    return getattr(vc, section)(m, m.participants[pid])[0]


def item(m, pid, cid, section="active_contracts"):
    found = [i for i in items(m, pid, section) if i.startswith(cid + " with ")]
    assert len(found) == 1, (cid, items(m, pid, section))
    return found[0]


def line(m, pid, cid, section="active_contracts"):
    return item(m, pid, cid, section).split("\n")[0]


def subs(m, pid, cid, section="active_contracts"):
    return item(m, pid, cid, section).split("\n")[1:]


def is_active(m, pid, c):
    """Section 10, and the two lists agree with it."""
    p = m.participants[pid]
    here = [i for i in vc.active_contracts(m, p)[1] if i.startswith(c.id + " with ")]
    there = [i for i in vc.finished_contracts(m, p)[1] if i.startswith(c.id + " with ")]
    assert len(here) + len(there) == 1
    assert bool(here) == vc.active(m, p, c)
    return bool(here)


def layout(sections):
    """view.md, 2.3: one empty line before each title, one after it when it has content, one between items."""
    out = []
    for name, content in sections:
        out += ["", name]
        if content:
            out += ["", "\n\n".join(content)]
    return "\n".join(out)


# 1, 2. The sections and the shared texts


def test_sections_order_form_and_no_change():
    m = finished_example()
    before = copy.deepcopy(m)
    assert [t for t, _ in vc.sections(m, m.participants["M-14b"])] == [
        "--- Open posts on the forum ---", "--- Your production ---", "--- Your active contracts ---",
        "--- Your finished contracts (latest 5 of 11) ---"]
    assert [t for t, _ in vc.sections(m, m.participants["B-c53"])] == [
        "--- Your tasks not yet contracted ---", "--- Your active contracts ---",
        "--- Your finished contracts (latest 5 of 10, and 1 older delivered and not paid in full) ---",
        "--- Other buyers' open posts (for reference) ---"]
    for p in m.participants.values():
        for _, content in vc.sections(m, p):
            for text in content:
                first, *rest = text.split("\n")
                assert first and not first[0].isspace()
                assert all(sub.startswith("    ") and sub[4] != " " for sub in rest)
    assert m == before


def test_empty_sections_are_titles_alone():
    m = market()
    assert vc.sections(m, m.participants["B-c53"]) == [
        ("--- Your tasks not yet contracted ---", []), ("--- Your active contracts ---", []),
        ("--- Your finished contracts ---", []), ("--- Other buyers' open posts (for reference) ---", [])]
    manager = vc.sections(m, m.participants["M-14b"])
    assert [(t, len(content)) for t, content in manager] == [
        ("--- Open posts on the forum ---", 0), ("--- Your production ---", 2), ("--- Your active contracts ---", 0),
        ("--- Your finished contracts ---", 0)]


def test_when_and_named():
    m = market(view={"overflow_ids": 2})
    assert [vc._when(m, g) for g in (1, 3, 4, 41)] == ["day 1, round 1", "day 1, round 3", "day 2, round 1",
                                                       "day 14, round 2"]
    assert vc._when(market(time={"rounds_per_day": 1}), 7) == "day 7, round 1"
    assert vc._named(m, ["a"]) == "a"
    assert vc._named(m, ["a", "b"]) == "a, b"
    assert vc._named(m, ["a", "b", "c"]) == "a, b and 1 more"
    assert vc._named(m, ["a", "b", "c", "d"]) == "a, b and 2 more"


def test_values_follow_the_value_parameters():
    m = market()
    assert vc._worths(m, 65) == "12.8, 20.1, 28.9, 39.3, 51.4, 65.0"  # 2.3's example
    m = market(value={"q0": 4, "exponent": 1})
    assert vc._worths(m, 60) == "10.0, 20.0, 30.0, 40.0, 50.0, 60.0"


def test_one_line_in_every_quoted_message():
    m = market(12, 1)
    c = contract(m, 1, form="escrow", due=20)
    reject(c, "R-1", 11, 11, "a\r\nb\n", message="x\n\ny", quality=None)
    give(c, "R-2", 12, "p\vq\fr\x1cs\x1dt\x1eu\x85v\u2028w\u2029z", 7.0)
    assert subs(m, "B-c53", c.id)[1].endswith('(not yours); your message: "a b" | your readings: none')
    assert subs(m, "B-c53", c.id)[-2] == '    message with delivery #1 (R-1, day 11): "x  y"'
    assert subs(m, "B-c53", c.id)[-1] == ('    with delivery #2 (R-2, day 12): stated quality 7; message '
                                          '"p q r s t u v w z"')
    assert subs(m, "M-14b", c.id)[1].endswith('; the buyer\'s message: "a b"')


# 3. Open posts


def test_post_line_examples():
    m = market(1, 2)
    post(m, 3, 1, 1, "escrow", 7.0, due=7, buyer="B-d75")
    assert vc.open_posts(m, m.participants["M-14b"]) == ("--- Open posts on the forum ---", [spec(221)])
    assert vc.open_posts(m, m.participants["B-c53"]) == ("--- Other buyers' open posts (for reference) ---",
                                                         [spec(224)])
    assert items(m, "B-d75", "open_posts") == []  # its own post is in its tasks section
    m.day, m.round = 4, 1
    assert items(m, "M-14b", "open_posts") == [spec(222)]
    m = market(10, 1)
    bid(m, 41, post(m, 12, 9, 1, "plain", due=15), "M-14b", 18.0, 5.0)
    assert items(m, "M-14b", "open_posts") == [spec(223)]
    assert items(m, "M-849", "open_posts") == [
        "post-12 by B-c53 | task-12 | due day 15 (5 days left) | prefers a plain contract | bids: sealed (only the "
        "buyer sees them); you have not bid"]


def test_post_terms_and_window():
    m = market(2, 3)  # now = 6
    post(m, 1, 2, 1, due=20)  # opens in round index 6
    post(m, 2, 2, 2, "escrow", due=20)  # opens in round index 7 = day 3, round 1
    post(m, 3, 2, 3, "plain", due=20)
    assert items(m, "B-d75", "open_posts") == [
        "post-1 by B-c53 | task-1 | due day 20 (18 days left) | no preferred form | bids: sealed (only the buyer "
        "sees them)",
        "post-2 by B-c53 | task-2 | due day 20 (18 days left) | prefers escrow | bids open; the buyer can accept one "
        "from day 3, round 1 | bids: sealed (only the buyer sees them)",
        "post-3 by B-c53 | task-3 | due day 20 (18 days left) | prefers a plain contract | bids open; the buyer can "
        "accept one from day 3, round 2 | bids: sealed (only the buyer sees them)"]


def test_bids_field_on_anothers_post():
    m = market(5, 1)
    p = post(m, 1, 1, 1, due=20)
    assert vc.bids_field(m, p, "B-d75") == "sealed (only the buyer sees them)"
    assert vc.bids_field(m, p, "M-14b") == "sealed (only the buyer sees them); you have not bid"
    mine = bid(m, 1, p, "M-14b", 22.25)
    bid(m, 2, p, "M-849", 18.0, 5.0)
    assert vc.bids_field(m, p, "M-14b") == ("sealed; yours: bid-1 at 22.25 (awaiting the buyer: not a contract "
                                            "unless accepted)")
    assert vc.bids_field(m, p, "M-849") == ("sealed; yours: bid-2 at 18 with threshold 5 (escrow) (awaiting the "
                                            "buyer: not a contract unless accepted)")
    assert vc.bids_field(m, p, "M-f8c") == "sealed (only the buyer sees them); you have not bid"
    assert vc.bids_field(m, p, "B-d75") == "sealed (only the buyer sees them)"
    for status in ("withdrawn", "lost"):
        mine.status = status
        assert vc.bids_field(m, p, "M-14b") == "sealed (only the buyer sees them); you have not bid"


def test_bids_field_on_the_buyers_own_post():
    m = market(1, 2)
    p = post(m, 5, 1, 1, due=20)  # made in round index 1: its bids open in round index 3
    bid(m, 1, p, "M-14b", 20.0, 6.0)
    assert vc.bids_field(m, p, "B-c53") == "sealed until day 1, round 3 (you see them then)"
    m.round = 3
    assert vc.bids_field(m, p, "B-c53") == "bid-1 by M-14b at 20 with threshold 6 (escrow)"
    bid(m, 2, p, "M-849", 18.5)
    bid(m, 3, p, "M-f8c", 18.5, 7.0)
    bid(m, 4, p, "M-14b", 1.0, status="withdrawn")
    assert vc.bids_field(m, p, "B-c53") == spec(367).rsplit(" | bids: ", 1)[1]
    m.bids["bid-1"].price = 18.5  # updated in place: it keeps its place among equal prices
    assert vc.bids_field(m, p, "B-c53").startswith("bid-1 by M-14b at 18.5 with threshold 6 (escrow); bid-2 by")
    m.participants["M-849"].out_of_business = True
    assert vc.bids_field(m, p, "B-c53") == ("bid-1 by M-14b at 18.5 with threshold 6 (escrow); bid-3 by M-f8c at "
                                            "18.5 with threshold 7 (escrow)")
    for b in m.bids.values():
        b.status = "lost"
    assert vc.bids_field(m, p, "B-c53") == "no bids yet"


def test_open_posts_order_and_limit():
    m = market(9, 1, view={"open_shown": 3, "overflow_ids": 2})
    for n in (4, 2, 7):  # created in this order
        post(m, n, 1, 1, due=30)
    names = lambda pid: [i.split(" ", 1)[0] for i in items(m, pid, "open_posts")]  # noqa: E731
    assert names("M-14b") == ["post-4", "post-2", "post-7"]  # at the limit: all listed, in creation order
    post(m, 1, 2, 1, due=30, buyer="B-d75")
    assert names("M-14b") == ["(1", "post-2", "post-7", "post-1"]
    assert items(m, "M-14b", "open_posts")[0] == spec(198).strip().replace("post-3", "post-4")
    assert names("B-c53") == ["post-1"]  # only another buyer's posts
    assert names("B-d75") == ["post-4", "post-2", "post-7"]
    bid(m, 1, m.posts["post-1"], "M-14b", 10.0)  # the newest, with its own open bid: hidden first
    bid(m, 2, m.posts["post-2"], "M-849", 10.0)
    assert names("M-14b") == ["(1", "post-4", "post-2", "post-7"]
    assert items(m, "M-14b", "open_posts")[0] == "(1 more open post not listed: post-1)"
    assert items(m, "M-849", "open_posts")[0] == "(1 more open post not listed: post-2)"
    assert names("M-f8c") == ["(1", "post-2", "post-7", "post-1"]
    bid(m, 3, m.posts["post-4"], "M-14b", 10.0)
    bid(m, 4, m.posts["post-7"], "M-14b", 10.0)
    assert names("M-14b") == ["(1", "post-2", "post-7", "post-1"]  # ranks 1, 0, 1, 1: the oldest of rank 1 hidden
    m.bids["bid-1"].status = "withdrawn"
    post(m, 3, 3, 1, due=30)
    post(m, 5, 3, 2, due=30)
    post(m, 6, 3, 3, due=1)  # past its due day: still open and listed
    for pid in ("M-14b", "M-f8c"):  # the three newest of rank 0, in creation order; the hidden, newest first
        assert names(pid) == ["(4", "post-3", "post-5", "post-6"]
        assert items(m, pid, "open_posts")[0] == "(4 more open posts not listed: post-1, post-7 and 2 more)"
    bid(m, 5, m.posts["post-5"], "M-14b", 10.0)
    bid(m, 6, m.posts["post-6"], "M-14b", 10.0)
    assert names("M-14b") == ["(4", "post-2", "post-1", "post-3"]
    assert items(m, "M-14b", "open_posts")[0] == "(4 more open posts not listed: post-6, post-5 and 2 more)"
    m.posts["post-6"].closed = "accepted"
    m.posts["post-5"].closed = "withdrawn"
    assert names("M-f8c") == ["(2", "post-7", "post-1", "post-3"]
    assert items(m, "M-f8c", "open_posts")[0] == "(2 more open posts not listed: post-2, post-4)"


def test_a_repost_is_listed_where_it_was_created():
    m = market(12, 1)
    c = contract(m, 1, signed=1, due=5)
    end(m, c, 10)
    post(m, 2, 10, 1, due=30)
    post(m, 1, 11, 1)
    assert [i.split(" ", 1)[0] for i in items(m, "M-14b", "open_posts")] == ["post-2", "post-1-2"]


@pytest.mark.parametrize("today, clause", [
    (4, ""),
    (5, ""),
    (6, " | a contract signed today has deadline day 10, 4 days after signing (the task's due day is 9: its value to "
        "{whose} falls from then)"),
    (8, " | a contract signed today has deadline day 12, 4 days after signing (the task's due day is 9: its value to "
        "{whose} falls from then)"),
])
def test_lateness_clause_before_the_due_day(today, clause):
    m = market(today, 1)
    t = task(m, 1, 52, 9)
    assert vc._late(m, t, "manager") == clause.format(whose="the buyer")
    assert vc._late(m, t, "buyer") == clause.format(whose="you")


def test_lateness_clause_examples():
    m = market(9, 1)
    assert vc._late(m, task(m, 1, 65, 9), "buyer") == spec(286)
    m = market(17, 1)
    assert vc._late(m, task(m, 1, 52, 13), "buyer") == spec(287)
    assert vc._late(m, task(m, 1, 52, 13), "manager") == spec(288)
    m = market(7, 1)
    assert vc._late(m, task(m, 1, 52, 7), "manager") == spec(289)


@pytest.mark.parametrize("today, buyer, manager", [
    (9, "DUE TODAY (the task's due day 9): a result delivered tomorrow, day 10, is 1 day late and worth 80% of its "
        "on-time value (at quality 5, 6, 7, 8, 9, 10: 9.5, 14.8, 21.3, 29.0, 37.9, 48.0), and nothing from day 14; "
        "a contract signed today has deadline day 13",
     "DUE TODAY (the task's due day 9): a contract signed today has deadline day 13; a result's value to the buyer "
     "will fall by 20% of its on-time value for each day after day 9, and it is worth nothing from day 14"),
    (10, "PAST DUE (the task's due day 9): a result delivered tomorrow, day 11, is 2 days late and worth 60% of its "
         "on-time value (at quality 5, 6, 7, 8, 9, 10: 7.1, 11.1, 16.0, 21.8, 28.4, 36.0), and nothing from day 14; "
         "a contract signed today has deadline day 13",
     "PAST DUE (the task's due day 9): a contract signed today has deadline day 13; a result's value to the buyer "
     "falls by 20% of its on-time value for each day after day 9, and it is worth nothing from day 14"),
    (12, "PAST DUE (the task's due day 9): a result delivered tomorrow, day 13, is 4 days late and worth 20% of its "
         "on-time value (at quality 5, 6, 7, 8, 9, 10: 2.4, 3.7, 5.3, 7.3, 9.5, 12.0), and nothing from day 14; "
         "a contract signed today has deadline day 13",
     None),
    (13, "PAST DUE (the task's due day 9): a result delivered tomorrow, day 14, is 5 days late and worth nothing at "
         "any quality; a contract signed today has deadline day 13",
     None),
    (30, "PAST DUE (the task's due day 9): a result delivered tomorrow, day 31, is 22 days late and worth nothing at "
         "any quality; a contract signed today has deadline day 13",
     "PAST DUE (the task's due day 9): a contract signed today has deadline day 13; a result's value to the buyer "
     "falls by 20% of its on-time value for each day after day 9, and it is worth nothing from day 14"),
])
def test_lateness_clause_from_the_due_day(today, buyer, manager):
    m = market(today, 1)
    post(m, 1, 1, 1, worth=60, due=9)
    assert items(m, "B-c53", "tasks")[0].split(" | ")[2] == buyer
    if manager:
        assert items(m, "M-14b", "open_posts")[0].split(" | ")[3] == manager


def test_lateness_clause_follows_the_parameters():
    m = market(4, 1, value={"late_decay": 0.5}, contracts={"min_contract_days": 1}, time={"close_after_due_days": 2})
    t = task(m, 1, 60, 3)  # escrow_close_days 2: the full-pay cap is day 4, worth nothing from day 5
    assert vc._late(m, t, "manager") == (
        " | PAST DUE (the task's due day 3): a contract signed today has deadline day 4; a result's value to the "
        "buyer falls by 50% of its on-time value for each day after day 3, and it is worth nothing from day 5")
    assert vc._late(m, t, "buyer") == (
        " | PAST DUE (the task's due day 3): a result delivered tomorrow, day 5, is 2 days late and worth nothing at "
        "any quality; a contract signed today has deadline day 4")


# 4. The buyer's tasks


def test_task_line_examples():
    m = market(1, 1)
    task(m, 1, 65, 9)
    assert vc.tasks(m, m.participants["B-c53"]) == ("--- Your tasks not yet contracted ---", [spec(365)])
    m = market(14, 1)
    post(m, 22, 13, 3, "escrow", 7.0, worth=57, due=17)
    assert items(m, "B-c53", "tasks") == [spec(366)]
    m = market(5, 1)
    p = post(m, 5, 4, 1, "escrow", 7.0, worth=50, due=5)
    bid(m, 1, p, "M-14b", 20.0, 6.0)
    bid(m, 2, p, "M-849", 18.5)
    bid(m, 3, p, "M-f8c", 18.5, 7.0)
    post(m, 9, 4, 1, "plain", worth=54, due=9)
    assert items(m, "B-c53", "tasks") == [spec(367), spec(368)]
    m = market(23, 2, buyers=("B-79d", "B-42c"), managers=("M-bd6", "M-f2b", "M-218"))
    end(m, contract(m, 27, "M-bd6", "escrow", 17, 16, 7.0, worth=42, due=19, buyer="B-42c"), 23)
    assert items(m, "B-42c", "tasks") == [spec(369)]
    assert items(m, "B-79d", "tasks") == []


def test_task_reposted_after_ended_contracts():
    m = market(11, 1)
    end(m, contract(m, 12, "M-f8c", price=13, signed=2, worth=47, due=8), 10)
    post(m, 12, 10, 1)
    head, tail = spec(370).split(" | ... | ")
    text = items(m, "B-c53", "tasks")[0]
    assert text.startswith(head.replace("(...)", "(on-time value to you at quality 5, 6, 7, 8, 9, 10: "))
    assert text.endswith(" | due day 8 (3 days ago) | " + tail)
    c = contract(m, 12, "M-14b", signed=11)
    m.day = 14
    end(m, c, 14)
    assert c.id == "contract-12-2"
    assert items(m, "B-c53", "tasks")[0].endswith(
        " | due day 8 (6 days ago) | QUEUED (not posted again since you ended contract-12 on day 10, contract-12-2 "
        "on day 14)")
    post(m, 12, 14, 1, "escrow")
    assert items(m, "B-c53", "tasks")[0].endswith(
        " | due day 8 (6 days ago) | POSTED as post-12-3 (you ended contract-12 on day 10, contract-12-2 on day 14), "
        "prefers escrow (edit_post changes its stated form and threshold) | bids open; you can accept one from day "
        "14, round 3, not before (accept_bid fails earlier) | bids: sealed until day 14, round 3 (you see them then)")


def test_tasks_order_and_limit():
    m = market(3, 1, view={"open_shown": 3, "overflow_ids": 1})  # now = 7
    names = lambda: [i.split(" ", 1)[0] for i in items(m, "B-c53", "tasks")]  # noqa: E731
    task(m, 1, due=30)  # queued: rank 1
    post(m, 2, 1, 1, due=30)  # posted, open, no bid: rank 2
    bid(m, 1, post(m, 3, 1, 1, due=30), "M-14b", 10.0)  # posted, open, a bid: rank 0
    task(m, 4, due=30, buyer="B-d75")
    assert names() == ["task-1", "task-2", "task-3"]  # at the limit, in arrival order; another buyer's is not listed
    bid(m, 2, post(m, 5, 2, 3, due=30), "M-14b", 10.0)  # posted, sealed (opens in round index 8): rank 2
    assert names() == ["(1", "task-1", "task-3", "task-5"]
    assert items(m, "B-c53", "tasks")[0] == "(1 more task not listed: task-2 (posted))"
    task(m, 6, due=30)
    assert names() == ["(2", "task-1", "task-3", "task-6"]
    assert items(m, "B-c53", "tasks")[0] == "(2 more tasks not listed: task-5 (posted) and 1 more)"
    m.round = 2  # now = 8: the bids on post-5 are open, so task-5 has rank 0
    assert names() == ["(2", "task-3", "task-5", "task-6"]
    assert items(m, "B-c53", "tasks")[0] == "(2 more tasks not listed: task-2 (posted) and 1 more)"
    m.participants["M-14b"].out_of_business = True  # its bids no longer count: ranks 1, 2, 2, 2, 1
    assert names() == ["(2", "task-1", "task-5", "task-6"]
    assert items(m, "B-c53", "tasks")[0] == "(2 more tasks not listed: task-3 (posted) and 1 more)"
    m.tasks["task-6"].state = "withdrawn"
    m.tasks["task-5"].state = "contracted"
    assert names() == ["task-1", "task-2", "task-3"]


# 5. The manager's production


def test_production_lines():
    m = market()
    name, (costs, capacity) = vc.production(m, m.participants["M-14b"])
    assert name == "--- Your production ---"
    assert costs == spec(392)
    assert capacity == spec(421) == vc.capacity_line(m, "M-14b")
    m = market(tests={"manager_cost": 0.5, "buyer_cost": 0.5}, production={"sig_c": 0.4, "cost_ref_worth": 100,
                                                        "employee": {"fee": 2.5}})
    assert vc.production(m, m.participants["M-14b"])[1][0] == (
        f"Expected run costs in credits, with how much each typically varies either way: employee fresh 6.25 "
        f"({PM}24%), employee refine 4.75 ({PM}19%), you fresh 10.00 ({PM}40%), you refine 14.00 ({PM}40%). "
        f"test_result: 0.5 per reading.")


def test_capacity_line_examples():
    m = market(5, 1)
    c9, c10 = contract(m, 9, due=20), contract(m, 10, due=20)
    other = contract(m, 11, "M-849", due=20)
    run(c9, "R-1e4", 4, 8, "manager")
    run(c9, "R-8a7", 4, 6)
    run(c10, "R-047", 4, 7)
    run(c10, "R-old", 3, 5)  # arrived today: not in flight
    run(other, "R-fff", 4, 9, "manager")
    assert vc.capacity_line(m, "M-14b") == spec(422)
    assert vc.capacity_line(m, "M-849") == (
        "You (run_myself): busy on R-fff (contract-11) until day 9: no run_myself before day 9. Employee "
        "(delegate_run): 0 of 2 slots busy: 2 delegate_run can start now on your contracts.")
    m = market(5, 1)
    c = contract(m, 17, due=20)
    run(c, "R-a01", 4, 8, "manager")
    run(c, "R-a02", 4, 6)
    end(m, c, 5)
    assert vc.capacity_line(m, "M-14b") == spec(423)
    c.ended, c.form, c.closed = None, "escrow", "late"  # a settled escrow reads the same
    assert vc.capacity_line(m, "M-14b") == spec(423)


def test_capacity_line_counts_and_singular():
    m = market(5, 1, production={"manager": {"capacity": 2}, "employee": {"capacity": 1}})
    c = contract(m, 1, due=20)
    assert vc.capacity_line(m, "M-14b") == (
        "You (run_myself): free: 2 run_myself can start now on your contracts. Employee (delegate_run): 0 of 1 slot "
        "busy: 1 delegate_run can start now on your contracts.")
    run(c, "R-001", 4, 8, "manager")
    run(c, "R-002", 5, 7)
    assert vc.capacity_line(m, "M-14b") == (
        "You (run_myself): busy on R-001 (contract-1) until day 8: 1 more run_myself can start now on your "
        "contracts. Employee (delegate_run): 1 of 1 slot busy (R-002 on contract-1, arrives day 7): no delegate_run "
        "before day 7.")
    run(contract(m, 2, due=20), "R-003", 5, 6, "manager")
    assert vc.capacity_line(m, "M-14b").startswith(
        "You (run_myself): busy on R-001 (contract-1) until day 8, R-003 (contract-2) until day 6: no run_myself "
        "before day 6. Employee")


# 6 to 8. The contract lines


def plain_six():
    """contract-6 of 8.5's first example, on day 6; its results are 7.3's last two examples."""
    m = market(6, 1)
    c = contract(m, 6, price=25.5, signed=3, worth=70, due=8)
    c.paid, c.kept = 10.0, "R-bbb"
    run(c, "R-bbb", 1, 2, mine=[5.0], theirs=[4.0, 6.0])
    run(c, "R-bcc", 4, 5, parent="R-bbb")
    give(c, "R-bbb", 4, "hi there", 6.5)
    give(c, "R-bcc", 5, quality=7.0)
    return m, c


def test_plain_contract_with_deliveries():
    m, c = plain_six()
    assert items(m, "B-c53", "active_contracts") == [spec(702, 706)]
    assert items(m, "M-14b", "active_contracts") == [
        "contract-6 with B-c53 | task-6 | price 25.5 (plain) | paid 10.00 of 25.50 | deadline day 8 (2 days left) | "
        "in use by B-c53: R-bbb (kept; day 4, on time)\n    " + spec(597) + "\n    " + spec(598)]
    c.kept = None
    assert line(m, "B-c53", c.id).endswith(" | in use: R-bcc (latest; day 5, on time) | cannot be ended (it has a "
                                           "delivery)")
    assert subs(m, "B-c53", c.id)[:2] == [
        f"    R-bbb delivered day 4 | your readings [4.0, 6.0] mean 5.00 (n=2, {PM}1.41 standard error)",
        "    R-bcc delivered day 5 (in use) | your readings: none"]
    assert subs(m, "M-14b", c.id) == [
        f"    R-bbb | employee | fresh | started day 1, arrived day 2 | your readings [5.0] mean 5.00 (n=1, {PM}0.50 "
        f"standard error) | delivered day 4 (not in use)",
        "    R-bcc | employee | refine of R-bbb | started day 4, arrived day 5 | your readings: none | DELIVERED day "
        "5 (in use)"]


@pytest.mark.parametrize("day, lateness", [(8, "on time"), (9, "1 day late"), (11, "3 days late")])
def test_in_use_lateness_counts_from_the_deadline(day, lateness):
    m = market(day, 1)
    c = contract(m, 1, signed=1, due=6, deadline=8)
    give(c, "R-001", day)
    assert line(m, "B-c53", c.id).split(" | ")[-2] == f"in use: R-001 (latest; day {day}, {lateness})"
    mine = line(m, "M-14b", c.id, "active_contracts" if day <= 8 else "finished_contracts")  # 10.1, rule 5
    assert f" | in use by B-c53: R-001 (latest; day {day}, {lateness})" in mine


def test_running_and_arrived_results():
    m = market(2, 1)
    c = contract(m, 1, form="escrow", price=25, due=9)
    run(c, "R-dbe", 2, 3)
    assert subs(m, "M-14b", c.id)[1] == "    " + spec(594)
    m.day = 6
    c.results.clear()
    run(c, "R-aaa", 5, 8)
    run(c, "R-you", 5, 6, "manager", mine=[6.0, 7.0])
    assert subs(m, "M-14b", c.id)[1:] == [
        "    " + spec(595),
        f"    R-you | you | fresh | started day 5, arrived day 6 | your readings [6.0, 7.0] mean 6.50 (n=2, {PM}0.35 "
        f"standard error)"]
    assert item(m, "M-14b", c.id).startswith(
        "contract-1 with B-c53 | task-1 | price 25 (escrow, threshold 5) | deposit 25.00: 25.00 held in escrow, 0.00 "
        "released | deadline day 9 (3 days left) | price held; nothing submitted\n    escrow days: ")
    c.results.clear()
    assert subs(m, "M-14b", c.id)[1:] == ["    no results yet"]


def test_rejected_submission_on_the_managers_line():
    m = market(7, 1)
    c = contract(m, 1, form="escrow", due=8, signed=1)
    run(c, "R-b1e", 2, 3)
    run(c, "R-81c", 5, 6, parent="R-b1e", mine=[5.4])
    reject(c, "R-81c", 6, 7, "R-81c tests average ~4.8 (n=3), below the escrow threshold of 5. Please refine the "
                             "result and resubmit before the deadline on day 8.")
    assert subs(m, "M-14b", c.id)[2] == "    " + spec(596)


def test_readings_shown_limit():
    m = market(6, 1)
    c = contract(m, 1, due=20)
    r = run(c, "R-001", 1, 2, theirs=[5.9, 7.4, 4.3, 7.5, 6.4], mine=[1.0, 2.0, 3.0, 4.0, 5.0])
    give(c, "R-001", 3)
    assert subs(m, "B-c53", c.id)[0].endswith(
        f" | your readings [5.9, 7.4, 4.3, 7.5, 6.4] mean 6.30 (n=5, {PM}0.89 standard error)")
    r.buyer_readings.insert(0, 5.7)
    r.manager_readings.insert(0, 6.0)
    assert subs(m, "B-c53", c.id)[0].endswith(  # 2.3's example
        f" | your readings [..., 5.9, 7.4, 4.3, 7.5, 6.4] mean 6.20 (n=6, {PM}0.82 standard error)")
    assert f" | your readings [..., 1.0, 2.0, 3.0, 4.0, 5.0] mean 3.50 (n=6, {PM}0.20 standard error) | " in \
        subs(m, "M-14b", c.id)[0]


def test_escrow_examples_of_a_buyer():
    m = market(12, 1)
    c9 = contract(m, 9, "M-849", "escrow", 15, 10, 5.0, due=10)
    c6 = contract(m, 6, "M-14b", "escrow", 21, 8, 6.0, due=13)
    reject(c6, "R-d02", 12, 12)
    assert items(m, "B-c53", "active_contracts") == [spec(722, 723), spec(725, 727)]
    assert subs(m, "B-c53", c6.id)[0] == spec(789)
    assert c9.deadline == 14 and c6.deadline == 13

    m = market(14, 1)
    c8 = contract(m, 8, "M-14b", "escrow", 24, 5, 6.0, due=9)
    give(c8, "R-d04", 11, outcome="auto", decided=13)
    release(c8, 13, "automatic", 14.4)
    assert items(m, "B-c53", "active_contracts") == [spec(729, 730)]
    assert items(m, "M-14b", "active_contracts") == []
    assert items(m, "M-14b", "finished_contracts") == [spec(944)]

    m = market(15, 1)
    c5 = contract(m, 5, "M-14b", "escrow", 16, 6, 7.0, due=10)
    run(c5, "R-a01", 13, 14, theirs=[5.0])
    reject(c5, "R-a01", 14, 14, dispute=Dispute(14, 6.1, "buyer"), message="m", quality=7.0)
    c5.closed = "late"
    assert items(m, "B-c53", "active_contracts") == [spec(732, 735)]
    m.day = 16  # 10.2, rule 5: the latest submission day is no longer today - 1
    assert items(m, "B-c53", "active_contracts") == []
    assert items(m, "B-c53", "finished_contracts") == [
        "contract-5 with M-14b | task-5 | worth 60 | price 16 (escrow, threshold 7) | deposit 16.00: 0.00 held in "
        "escrow, 0.00 released, 16.00 refunded | deadline day 10 | CLOSED UNRELEASED (price refunded)"]


def test_money_field_examples():
    m = market(5, 1)
    c = contract(m, 1, form="escrow", price=25, due=9)
    assert " | price 25 (escrow, threshold 5) | deposit 25.00: 25.00 held in escrow, 0.00 released | " in \
        line(m, "B-c53", c.id)
    m = market(10, 1)
    c = contract(m, 1, form="escrow", price=24, threshold=10.0, due=9)
    give(c, "R-001", 10, outcome="approved", decided=10)
    release(c, 10, "approved", 19.2)
    assert " | price 24 (escrow, threshold 10) | deposit 24.00: 0.00 held in escrow, 19.20 released, 4.80 refunded " \
           "| " in line(m, "B-c53", c.id)
    assert line(m, "B-c53", c.id).endswith(" | RELEASED R-001 (day 10, approved; submitted 1 day late)" + BUYER_MARK)
    c = contract(m, 2, form="escrow", price=17, due=9)
    c.closed = "late"
    assert " | deposit 17.00: 0.00 held in escrow, 0.00 released, 17.00 refunded | " in line(m, "M-14b", c.id)
    c = contract(m, 3, price=22.25, due=20)
    c.paid = 3.0
    assert " | price 22.25 (plain) | paid 3.00 of 22.25 | " in line(m, "M-14b", c.id)
    end(m, c, 10)
    assert " | price 22.25 (plain) | paid 3.00 | " in line(m, "M-14b", c.id, "finished_contracts")


def test_message_lines_limit():
    m = market(14, 1)
    c = contract(m, 1, due=20)
    give(c, "R-a00", 11)  # neither a message nor a stated quality: no message line, but it has its number
    give(c, "R-b00", 12, "msg 0")
    give(c, "R-b02", 13, "msg 2")
    give(c, "R-b03", 14, "msg 3", 9.0)
    assert subs(m, "B-c53", c.id)[4:] == ['    message with delivery #2 (R-b00, day 12): "msg 0"', *SPEC[741:743]]
    give(c, "R-b04", 14, "msg 4 x")
    assert subs(m, "B-c53", c.id)[5:] == ["    (1 earlier delivery message not listed)", *SPEC[741:744]]
    c.deliveries[0].quality = 0.0  # a stated quality of 0 is one
    assert subs(m, "B-c53", c.id)[5:] == SPEC[740:744]
    assert len(subs(m, "B-c53", c.id)) == 9  # every delivery line, whatever the limit
    m.cfg["view"]["messages_shown"] = 5
    assert subs(m, "B-c53", c.id)[5:] == [
        "    with delivery #1 (R-a00, day 11): stated quality 0", '    message with delivery #2 (R-b00, day 12): '
        '"msg 0"', *SPEC[741:744]]


def outcomes(day=10):
    """An escrow with three earlier rejections; the tests add its latest submission."""
    m = market(day, 1)
    c = contract(m, 1, form="escrow", price=20, threshold=6.0, due=20)
    reject(c, "R-001", 3, 3)
    reject(c, "R-002", 4, 5, "no", dispute=Dispute(6, 4.25, "buyer"))
    reject(c, "R-003", 9, 10)
    return m, c


def both(m, c, n):
    """The outcome of submission n as the manager's result line and the buyer's submission line write it."""
    mine, theirs = subs(m, "M-14b", c.id), subs(m, "B-c53", c.id)
    offset = 1 if state.held(c) else 0
    label = c.deliveries[n - 1].label
    manager = mine[offset + n - 1].split(f" | SUBMITTED day {c.deliveries[n - 1].day}: ")[1]
    buyer = theirs[offset + n - 1].removeprefix(f"    {label} submitted day {c.deliveries[n - 1].day} (#{n}): ")
    return manager, buyer.removesuffix(" | your readings: none")


def test_outcomes_of_earlier_submissions():
    m, c = outcomes()
    give(c, "R-004", 10)
    assert both(m, c, 1) == ("REJECTED day 3 (not disputed; the dispute window closed after day 4)",
                             "REJECTED day 3 (not disputed; the dispute window closed after day 4) (not yours)")
    assert both(m, c, 2) == (
        "REJECTED day 5, disputed: the arbiter read 4.2 and ruled for the buyer; the buyer's message: \"no\"",
        "REJECTED day 5, disputed: the arbiter read 4.2 and ruled for the buyer (not yours); your message: \"no\"")
    assert both(m, c, 3) == ("REJECTED day 10", "REJECTED day 10 (not yours)")  # in its window, but not the latest
    assert both(m, c, 4) == ("UNDER REVIEW (released at the start of day 12 unless approved or rejected)",) * 2
    assert line(m, "B-c53", c.id).endswith(
        " | price held; R-004 UNDER REVIEW since day 10 (released at the start of day 12 unless approved or rejected "
        "before)")
    assert line(m, "M-14b", c.id).endswith(
        " | price held; R-004 UNDER REVIEW since day 10 (released at the start of day 12 unless approved or rejected "
        "before)")


def test_outcomes_of_the_latest_submission():
    m, c = outcomes()
    d = reject(c, "R-004", 10, 10)
    assert both(m, c, 4) == ("REJECTED day 10 (disputable through day 11)",
                             "REJECTED day 10 (disputable through day 11) (not yours)")
    assert line(m, "B-c53", c.id).endswith(
        " | price held; 4 submissions rejected; the rejection of R-004 can be disputed through day 11 | can be ended "
        "from day 12 if M-14b does not dispute")
    m.day = 11
    assert both(m, c, 4)[0] == "REJECTED day 10 (disputable through day 11)"
    assert both(m, c, 3)[0] == "REJECTED day 10"
    m.day = 12
    assert both(m, c, 4)[0] == "REJECTED day 10 (not disputed; the dispute window closed after day 11)"
    assert both(m, c, 3)[0] == "REJECTED day 10 (not disputed; the dispute window closed after day 11)"
    assert line(m, "B-c53", c.id).endswith(" | price held; 4 submissions rejected | can be ended now")
    assert line(m, "M-14b", c.id).endswith(" | price held; 4 submissions rejected")

    m.day, d.dispute = 11, Dispute(11)
    assert both(m, c, 4) == ("REJECTED day 10, IN DISPUTE (the arbiter rules at the start of day 12)",
                             "REJECTED day 10, IN DISPUTE (the arbiter rules at the start of day 12) (not yours)")
    for pid in ("B-c53", "M-14b"):
        assert line(m, pid, c.id).endswith(
            " | price held; the rejection of R-004 is IN DISPUTE (the arbiter rules at the start of day 12)")

    m.day, d.dispute = 12, Dispute(11, 5.96, "buyer")
    assert both(m, c, 4) == ("REJECTED day 10, disputed: the arbiter read 6.0 and ruled for the buyer",
                             "REJECTED day 10, disputed: the arbiter read 6.0 and ruled for the buyer (not yours)")
    assert line(m, "B-c53", c.id).endswith(" | price held; 4 submissions rejected | can be ended now")

    d.dispute = Dispute(11, 6.3, "manager")
    release(c, 12, "ruling", received=False)
    assert both(m, c, 4) == (
        "REJECTED day 10, disputed: the arbiter read 6.3 and ruled for the manager: RELEASED",
        "REJECTED day 10, disputed: the arbiter read 6.3 and ruled for the manager: RELEASED; 20.00 paid to M-14b; "
        "yours once you pay the arbiter's fee 10 to the arbiter, not to the manager (pay_escrow_arbiter)")
    assert both(m, c, 1)[1] == "REJECTED day 3 (not disputed; the dispute window closed after day 4) (not yours)"
    c.received = 12
    assert both(m, c, 4)[1] == ("REJECTED day 10, disputed: the arbiter read 6.3 and ruled for the manager: "
                                "RELEASED; 20.00 paid to M-14b")


def test_outcomes_that_release():
    m, c = outcomes()
    approve(c, "R-004", 10, 10)
    assert both(m, c, 4) == ("APPROVED day 10", "APPROVED day 10; 20.00 paid to M-14b")
    assert both(m, c, 3) == ("REJECTED day 10", "REJECTED day 10 (not yours)")  # in its window, the escrow settled
    m, c = outcomes(12)
    c.deadline = 9
    give(c, "R-004", 10, outcome="auto", decided=12)
    release(c, 12, "automatic", 16.0)
    assert both(m, c, 4) == ("RELEASED with no decision day 12",
                             "RELEASED with no decision day 12; 16.00 paid to M-14b and 4.00 back to you")
    assert line(m, "B-c53", c.id).endswith(
        " | RELEASED R-004 (day 12, released with no decision; submitted 1 day late)" + BUYER_MARK)


def test_a_rejection_in_its_window_on_a_settled_escrow():
    m, c = outcomes(10)
    c.deliveries[-1].decided = 9
    c.closed = "late"
    assert both(m, c, 3) == ("REJECTED day 9", "REJECTED day 9 (not yours)")
    assert subs(m, "B-c53", c.id)[3] == "    escrow closed unreleased: its price 20.00 came back to you"


# 6.4 to 6.6, 8.1, 12. The status catalogue


@pytest.mark.parametrize("today, late, deadline, end_clause", [
    (9, "", "deadline day 11 (2 days left; its task's due day is 9)",
     " | can be ended from day 12 if nothing is delivered by then"),
    (10, " | PAST DUE (the task's due day 9, not the contract's deadline day 11): a result delivered tomorrow, day "
         "11, is 2 days late and worth 60% of its on-time value (at quality 5, 6, 7, 8, 9, 10: 7.1, 11.1, 16.0, "
         "21.8, 28.4, 36.0), and nothing from day 14",
     "deadline day 11 (1 day left; its task's due day is 9)",
     " | can be ended from day 12 if nothing is delivered by then"),
    (11, " | PAST DUE (the task's due day 9, not the contract's deadline day 11): a result delivered tomorrow, day "
         "12, is 3 days late and worth 40% of its on-time value (at quality 5, 6, 7, 8, 9, 10: 4.7, 7.4, 10.7, "
         "14.5, 19.0, 24.0), and nothing from day 14",
     "deadline day 11 (today; its task's due day is 9)",
     " | can be ended from day 12 if nothing is delivered by then"),
    (12, " | PAST DUE (the task's due day 9 and the contract's deadline day 11): a result delivered tomorrow, day "
         "13, is 4 days late and worth 20% of its on-time value (at quality 5, 6, 7, 8, 9, 10: 2.4, 3.7, 5.3, 7.3, "
         "9.5, 12.0), and nothing from day 14",
     "deadline day 11 (1 day ago; its task's due day is 9)", " | can be ended now"),
    (13, " | PAST DUE (the task's due day 9 and the contract's deadline day 11): a result delivered tomorrow, day "
         "14, is 5 days late and worth nothing at any quality",
     "deadline day 11 (2 days ago; its task's due day is 9)", " | can be ended now"),
])
def test_plain_nothing_delivered(today, late, deadline, end_clause):
    m = market(today, 1)
    c = contract(m, 1, price=18.5, signed=7, due=9)
    assert item(m, "B-c53", c.id) == (
        f"contract-1 with M-14b | task-1 | {VALUES_60}{late} | price 18.5 (plain) | paid 0.00 of 18.50 | signed day "
        f"7 | {deadline} | NOT DELIVERED (nothing to test yet){end_clause}")
    assert item(m, "M-14b", c.id) == (
        f"contract-1 with B-c53 | task-1 | price 18.5 (plain) | paid 0.00 of 18.50 | {deadline} | NOT DELIVERED\n"
        f"    no results yet")


def test_plain_signed_after_its_deadline():
    m = market(15, 1)
    c = contract(m, 1, signed=15, due=10)
    assert c.deadline == 14
    assert line(m, "B-c53", c.id).endswith(
        " | signed day 15 | deadline day 14 (1 day ago; its task's due day is 10) | NOT DELIVERED (nothing to test "
        "yet) | signed after its deadline day: cannot be ended")
    give(c, "R-001", 15)
    assert line(m, "B-c53", c.id).endswith(" | in use: R-001 (latest; day 15, 1 day late) | cannot be ended (it has "
                                           "a delivery)")


def test_the_late_clause_only_while_nothing_is_delivered_and_not_settled():
    m = market(12, 1)
    c = contract(m, 1, signed=7, due=9)
    assert " | PAST DUE (" in line(m, "B-c53", c.id)
    give(c, "R-001", 12)
    assert " | PAST DUE (" not in line(m, "B-c53", c.id)
    m = market(12, 1, view={"overdue_days": 9})
    c = contract(m, 1, form="escrow", signed=1, due=5)
    assert " | PAST DUE (the task's due day 5 and the contract's deadline day 5): " in line(m, "B-c53", c.id)
    c.closed = "late"
    assert " | PAST DUE (" not in line(m, "B-c53", c.id)


def test_escrow_held_nothing_submitted():
    m = market(23, 1)
    early = contract(m, 36, "M-218", "escrow", 18, 17, 7.0, worth=63, due=25)
    over = contract(m, 35, "M-218", "escrow", 18, 17, 7.0, due=22)
    cap = contract(m, 9, "M-849", "escrow", 15, 21, 5.0, due=21)
    assert line(m, "B-c53", early.id).endswith(
        " | deadline day 25 (2 days left) | price held; nothing submitted (nothing to test yet) | can be ended from "
        "day 26 if nothing is released by then")
    assert item(m, "B-c53", over.id) == spec(719, 720)
    assert cap.deadline == 25
    assert line(m, "B-c53", cap.id).endswith(
        " | deadline day 25 (2 days left; its task's due day is 21) | price held; nothing submitted (nothing to test "
        "yet) | its escrow closes the day after its deadline day: can be ended only after a rejection that stands")
    assert line(m, "M-849", cap.id).endswith(" | price held; nothing submitted")


def test_escrow_in_dispute():
    m = market(13, 1)
    c = contract(m, 1, form="escrow", price=20, due=14)
    reject(c, "R-001", 12, 12, dispute=Dispute(13))
    assert line(m, "B-c53", c.id).endswith(
        " | deadline day 14 (1 day left) | price held; the rejection of R-001 is IN DISPUTE (the arbiter rules at "
        "the start of day 14)")
    assert subs(m, "B-c53", c.id)[0] == spec(787)
    assert subs(m, "M-14b", c.id)[0] == (
        "    escrow days: full price if submitted by day 14 (the deadline day) | with nothing released, closes at "
        "the start of day 19 (later if a decision or dispute is pending then), and takes no new submission from "
        "that day | dispute fee 10 credits")


def test_escrow_released_with_the_fee_owed_or_not():
    m = market(12, 1)
    c = contract(m, 14, form="escrow", price=30, signed=7, threshold=7.0, due=12)
    reject(c, "R-e01", 10, 10, dispute=Dispute(11, 7.5, "manager"))
    release(c, 12, "ruling", received=False)
    status = "RELEASED R-e01 (day 12, the arbiter ruled; submitted on time); B-c53 owes the arbiter's fee 15 to " \
             "the arbiter (not to {who}) before the result is its"
    assert line(m, "M-14b", c.id).endswith(" | deadline day 12 | " + status.format(who="you") + MANAGER_MARK)
    assert line(m, "B-c53", c.id).endswith(" | deadline day 12 | " + status.format(who="the manager"))
    assert subs(m, "M-14b", c.id)[0].startswith("    R-e01 | ")  # no escrow days line once released
    c.received = 12
    paid = "RELEASED R-e01 (day 12, the arbiter ruled; submitted on time)"
    assert line(m, "M-14b", c.id).endswith(" | " + paid + MANAGER_MARK)
    assert line(m, "B-c53", c.id).endswith(" | " + paid + BUYER_MARK)
    m.cfg["contracts"]["escrow"]["dispute_fee_rate"] = 0.0  # a fee of 0 is never owed
    c.received = None
    assert line(m, "B-c53", c.id).endswith(" | " + paid + BUYER_MARK)


def test_escrow_closed_unreleased_with_nothing_submitted():
    m = market(15, 1, view={"overdue_days": 6})
    c = contract(m, 1, form="escrow", price=16, signed=6, threshold=7.0, due=10)
    c.closed = "late"
    assert item(m, "B-c53", c.id) == (
        f"contract-1 with M-14b | task-1 | {VALUES_60} | price 16 (escrow, threshold 7) | deposit 16.00: 0.00 held "
        f"in escrow, 0.00 released, 16.00 refunded | signed day 6 | deadline day 10 | CLOSED UNRELEASED (price "
        f"refunded) (nothing to test yet){BUYER_MARK}\n    escrow closed unreleased: its price 16.00 came back to "
        f"you")
    assert item(m, "M-14b", c.id) == (
        f"contract-1 with B-c53 | task-1 | price 16 (escrow, threshold 7) | deposit 16.00: 0.00 held in escrow, 0.00 "
        f"released, 16.00 refunded | deadline day 10 | CLOSED UNRELEASED (price refunded){MANAGER_MARK}\n"
        f"    no results yet")
    m.cfg["view"]["overdue_days"] = 5  # at the default it is finished from its close day
    for pid in ("B-c53", "M-14b"):
        assert not is_active(m, pid, c)


def test_ended_contracts_are_finished_lines():
    m = market(11, 1)
    c = contract(m, 12, "M-f8c", price=13, signed=2, worth=47, due=8)
    c.paid = 3.0
    run(c, "R-001", 10, 14)  # still running: an ended contract is finished all the same
    end(m, c, 10)
    assert items(m, "B-c53", "finished_contracts") == [spec(935)]
    assert items(m, "M-f8c", "finished_contracts") == [
        "contract-12 with B-c53 | task-12 | price 13 (plain) | paid 3.00 | deadline day 8 | ENDED by B-c53 on day 10 "
        "| 1 result"]
    e = contract(m, 3, "M-f8c", "escrow", 17, 2, 7.0, due=8)
    end(m, e, 11)
    assert line(m, "M-f8c", e.id, "finished_contracts") == (
        "contract-3 with B-c53 | task-3 | price 17 (escrow, threshold 7) | deposit 17.00: 0.00 held in escrow, 0.00 "
        "released, 17.00 refunded | deadline day 8 | ENDED by B-c53 on day 11 (price refunded) | 0 results")
    assert title(m, "M-f8c", "finished_contracts") == "--- Your finished contracts (latest 2 of 2) ---"
    assert items(m, "M-f8c", "active_contracts") == items(m, "B-c53", "active_contracts") == []


# 9. The escrow days line


def test_escrow_days_examples():
    m = market(7, 1)
    c = contract(m, 1, form="escrow", price=18, due=9)
    give(c, "R-cdd", 6)
    assert subs(m, "M-14b", c.id)[0] == spec(786)
    assert subs(m, "B-c53", c.id)[0] == (
        "    escrow days: full price if submitted by day 9 (the deadline day) | R-cdd is released at the start of day "
        "8 if you neither approve nor reject it (sooner if the market ends first) | with nothing released, closes at "
        "the start of day 14 (later if a decision or dispute is pending then) | dispute fee 9 credits")
    m = market(12, 1)
    c = contract(m, 1, form="escrow", price=16, due=7)
    reject(c, "R-001", 11, 11)
    assert subs(m, "M-14b", c.id)[0] == spec(788)
    assert subs(m, "B-c53", c.id)[0] == (
        "    escrow days: full price if submitted by day 7 (the deadline day, 5 days ago) | closes once no decision "
        "or dispute is pending (its close day 12 has come) | dispute fee 8 credits")


@pytest.mark.parametrize("today, p1, p2, p3", [
    (9, "full price if submitted by day 9 (the deadline day)",
     "a submission made today is released at the start of day 11 if the buyer neither approves nor rejects it "
     "(sooner if the market ends first)",
     "with nothing released, closes at the start of day 14 (later if a decision or dispute is pending then), and "
     "takes no new submission from that day"),
    (10, "full price if submitted by day 9 (the deadline day, 1 day ago)",
     "a submission made today is released at the start of day 12 if the buyer neither approves nor rejects it "
     "(sooner if the market ends first)",
     "with nothing released, closes at the start of day 14 (later if a decision or dispute is pending then), and "
     "takes no new submission from that day"),
    (13, "full price if submitted by day 9 (the deadline day, 4 days ago)",
     "a submission made today is released at the start of day 15 if the buyer neither approves nor rejects it "
     "(sooner if the market ends first)",
     "with nothing released, closes at the start of day 14 (later if a decision or dispute is pending then), and "
     "takes no new submission from that day"),
    (14, "full price if submitted by day 9 (the deadline day, 5 days ago)", None,
     "closes once no decision or dispute is pending (its close day 14 has come); no new submission"),
])
def test_escrow_days_around_the_deadline_and_the_close_day(today, p1, p2, p3):
    m = market(today, 1)
    c = contract(m, 1, form="escrow", price=2, due=9)
    reject(c, "R-001", today - 1, today)  # open to dispute: it holds the escrow open on its close day
    parts = [p for p in (p1, p2, p3, "dispute fee 1 credit") if p]
    assert subs(m, "M-14b", c.id)[0] == "    escrow days: " + " | ".join(parts)
    give(c, "R-002", today)  # under review: the release clause names it, also from the close day
    named = "R-002 is released at the start of day {} if you neither approve nor reject it (sooner if the market " \
            "ends first)".format(today + 2)
    assert subs(m, "B-c53", c.id)[0] == "    escrow days: " + " | ".join(
        [p1, named, p3.replace(", and takes no new submission from that day", "").replace("; no new submission", ""),
         "dispute fee 1 credit"])


@pytest.mark.parametrize("price, fee", [(2, "1 credit"), (15, "7.50 credits"), (20, "10 credits"),
                                        (0.02, "0.01 credits"), (4.01, "2 credits")])
def test_dispute_fee_on_the_escrow_days_line(price, fee):
    m = market(2, 1)
    c = contract(m, 1, form="escrow", price=price, due=9)
    assert subs(m, "B-c53", c.id)[0].endswith(" | dispute fee " + fee)


# 10. Active or finished


@pytest.mark.parametrize("role", ["B-c53", "M-14b"])
def test_overdue_boundary(role):
    m = market(12, 1)
    plain = contract(m, 1, signed=1, due=5, deadline=8)  # LD is its deadline
    assert is_active(m, role, plain)
    m.day = 13
    assert not is_active(m, role, plain)
    m.cfg["view"]["overdue_days"] = 6
    assert is_active(m, role, plain)
    escrow = contract(m, 2, form="escrow", signed=1, due=8, deadline=11)  # LD is its task's due day
    escrow.closed = "late"
    assert is_active(m, role, escrow)
    m.day = 14
    assert not is_active(m, role, escrow)
    escrow.closed = None  # held: active whatever the day
    assert is_active(m, role, escrow)
    end(m, escrow, 14)
    assert not is_active(m, role, escrow)


def test_manager_keeps_a_contract_with_a_running_or_recent_unused_result():
    m = market(20, 1)
    plain = contract(m, 1, signed=1, due=9)
    give(plain, "R-001", 5)
    released = contract(m, 2, form="escrow", signed=1, due=9)
    approve(released, "R-001", 5, 5)
    closed = contract(m, 3, form="escrow", signed=1, due=9)
    reject(closed, "R-001", 5, 5)
    closed.closed = "late"
    for c in (plain, released, closed):
        assert not is_active(m, "M-14b", c)
        r = run(c, "R-002", 15, 17)
        assert is_active(m, "M-14b", c)  # arrived on day today - recent_days
        r.arrival = 16
        assert not is_active(m, "M-14b", c)
        m.cfg["view"]["recent_days"] = 4
        assert is_active(m, "M-14b", c)
        m.cfg["view"]["recent_days"] = 3
        r.arrival = 21  # running: back to active, with the closed mark if settled
        assert is_active(m, "M-14b", c)
        assert line(m, "M-14b", c.id).endswith(MANAGER_MARK) == (c.form == "escrow")
        assert subs(m, "M-14b", c.id)[-1].endswith(
            "arrives day 21 (tomorrow: it cannot be tested, refined or delivered before then)")
        assert not is_active(m, "B-c53", c)  # the buyer's rules do not look at results
        r.arrival = 20
        assert is_active(m, "M-14b", c)
        give(c, "R-002", 20) if c.form == "plain" else c.deliveries.append(
            Delivery("R-002", 20, 1, outcome="rejected", decided=20))
        assert not is_active(m, "M-14b", c)  # delivered or submitted on it: no longer counted


def test_delivered_plain_contract_by_its_deadline_and_latest_delivery():
    m = market(8, 1)
    c = contract(m, 1, signed=1, due=6, deadline=8)
    give(c, "R-001", 3)
    assert is_active(m, "M-14b", c) and is_active(m, "B-c53", c)
    m.day = 9
    assert not is_active(m, "M-14b", c) and not is_active(m, "B-c53", c)
    give(c, "R-002", 9)
    m.day = 10  # the latest delivery was yesterday: the buyer keeps it, the manager does not
    assert not is_active(m, "M-14b", c) and is_active(m, "B-c53", c)
    m.day = 11
    assert not is_active(m, "B-c53", c)


def test_escrow_closed_after_submissions_by_its_due_day():
    m = market(9, 1)
    c = contract(m, 1, form="escrow", signed=7, due=9)  # deadline day 11: LD is the due day 9
    reject(c, "R-001", 8, 8)
    c.closed = "close"
    assert is_active(m, "M-14b", c) and is_active(m, "B-c53", c)
    m.day = 10
    assert not is_active(m, "M-14b", c) and not is_active(m, "B-c53", c)
    c.deliveries[0].day = 9
    assert not is_active(m, "M-14b", c) and is_active(m, "B-c53", c)


def test_released_escrow_in_the_buyers_view():
    m = market(9, 1)
    c = contract(m, 1, form="escrow", signed=1, due=9)
    approve(c, "R-001", 5, 5)
    assert is_active(m, "B-c53", c) and is_active(m, "M-14b", c)  # today <= due
    m.day = 10
    assert not is_active(m, "B-c53", c) and not is_active(m, "M-14b", c)
    c.release_day = 9  # released yesterday
    assert is_active(m, "B-c53", c) and not is_active(m, "M-14b", c)
    m.day = 11
    assert not is_active(m, "B-c53", c)
    m.day = 40
    c.release_how, c.received = "ruling", None  # the arbiter's fee is owed
    assert is_active(m, "B-c53", c) and not is_active(m, "M-14b", c)
    c.received = 40
    assert not is_active(m, "B-c53", c)


def test_bids_note():
    m = market(5, 1)
    assert items(m, "M-14b", "active_contracts") == []
    for n, (number, k) in enumerate(((2, 1), (10, 1), (12, 1), (12, 2), (3, 1)), 1):
        t = m.tasks.get(f"task-{number}") or task(m, number, due=30)
        if k == 2:
            t.ended.append("contract-12")
        p = post(m, number, 1, 1)
        bid(m, n, p, "M-14b" if number != 3 else "M-849", 10.0)
    bid(m, 9, m.posts["post-2"], "M-14b", 5.0, status="withdrawn")
    assert items(m, "M-14b", "active_contracts") == [
        "(your bids on post-10, post-12, post-12-2, post-2 are not contracts until their buyers accept them)"]
    assert items(m, "M-849", "active_contracts") == [
        "(your bids on post-3 are not contracts until their buyers accept them)"]
    assert items(m, "M-f8c", "active_contracts") == []
    done = contract(m, 20, "M-849", signed=1, due=2, deadline=2)
    give(done, "R-001", 2)
    assert items(m, "M-849", "active_contracts") == [  # a finished contract does not remove the note
        "(your bids on post-3 are not contracts until their buyers accept them)"]
    contract(m, 21, "M-849", signed=4, due=20)
    assert [i.split(" ", 1)[0] for i in items(m, "M-849", "active_contracts")] == ["contract-21"]
    assert items(m, "B-c53", "active_contracts")[0].startswith("contract-21 with M-849 | ")


# 11. The finished contracts


def finished_example():
    """The contracts of 11.5's two examples, on day 20."""
    m = market(20, 1)
    for n, day in ((1, 2), (2, 3), (3, 4)):
        c = contract(m, n, price=10, signed=1, due=5)
        give(c, f"R-{n}00", day)
        c.paid = 10.0
    end(m, contract(m, 4, price=10, signed=1, due=5), 7)
    give(contract(m, 6, price=11, signed=3, worth=51, due=7), "R-1a0", 5)
    contract(m, 8, price=13, signed=5, worth=53, due=9)
    give(contract(m, 10, price=15, signed=7, worth=55, due=11), "R-5a0", 9)
    c = contract(m, 11, price=16, signed=8, worth=56, due=12)
    give(c, "R-6a0", 10)
    c.paid = 16.0
    give(contract(m, 12, price=17, signed=9, worth=57, due=13), "R-7a0", 11)
    c = contract(m, 14, form="escrow", price=30, signed=7, threshold=7.0, due=12)
    reject(c, "R-e01", 10, 10, dispute=Dispute(11, 7.5, "manager"))
    release(c, 12, "ruling", received=False)
    contract(m, 15, form="escrow", price=12, signed=6, threshold=5.0, worth=41, due=9).closed = "late"
    return m


def test_finished_examples():
    m = finished_example()
    assert layout([vc.finished_contracts(m, m.participants["M-14b"])])[1:] == spec(893, 905)
    assert layout([vc.finished_contracts(m, m.participants["B-c53"])])[1:] == spec(911, 928)
    assert [i.split(" ", 1)[0] for i in items(m, "B-c53", "active_contracts")] == ["contract-14"]
    assert items(m, "M-14b", "active_contracts") == []


def test_further_finished_lines():
    m = market(12, 1)
    c = contract(m, 2, form="escrow", price=25, signed=1, threshold=5.0, worth=59, due=9)
    run(c, "R-c0f", 2, 3, theirs=[6.1])
    approve(c, "R-c0f", 5, 5)
    assert vc.finished_contracts(m, m.participants["B-c53"]) == (
        "--- Your finished contracts (latest 1 of 1) ---", [spec(937)])
    assert items(m, "M-14b", "finished_contracts") == [
        "contract-2 with B-c53 | task-2 | price 25 (escrow, threshold 5) | deposit 25.00: 0.00 held in escrow, 25.00 "
        "released | deadline day 9 | RELEASED R-c0f (day 5, approved; submitted on time) | 1 result"]


def test_finished_tail_reads_the_label_in_use():
    m = market(30, 1)
    c = contract(m, 1, signed=1, due=5)
    run(c, "R-001", 1, 2, theirs=[4.0])
    run(c, "R-002", 2, 3, theirs=[8.0, 9.0])
    give(c, "R-001", 2)
    give(c, "R-002", 3)
    c.paid = 20.0
    assert line(m, "B-c53", c.id, "finished_contracts").endswith(
        f" | in use: R-002 (latest; day 3, on time) | your readings [8.0, 9.0] mean 8.50 (n=2, {PM}1.41 standard "
        f"error)")
    c.kept = "R-001"
    assert item(m, "B-c53", c.id, "finished_contracts").endswith(
        f" | in use: R-001 (kept; day 2, on time) | your readings [4.0] mean 4.00 (n=1, {PM}2.00 standard error)")
    c.paid = 20.0 - 1e-6  # paid in full within 1e-6
    assert len(subs(m, "B-c53", c.id, "finished_contracts")) == 0
    c.paid = 19.99  # owed: its detail lines in place of the tail
    assert item(m, "B-c53", c.id, "finished_contracts") == (
        "contract-1 with M-14b | task-1 | worth 60 | price 20 (plain) | paid 19.99 of 20.00 | deadline day 5 | in "
        "use: R-001 (kept; day 2, on time)\n"
        f"    R-001 delivered day 2 (in use) | your readings [4.0] mean 4.00 (n=1, {PM}2.00 standard error)\n"
        f"    R-002 delivered day 3 | your readings [8.0, 9.0] mean 8.50 (n=2, {PM}1.41 standard error)")
    c.deliveries[1].message = "two\nlines"
    assert subs(m, "B-c53", c.id, "finished_contracts")[-1] == (
        '    message with delivery #2 (R-002, day 3): "two lines"')
    assert "\n" not in item(m, "M-14b", c.id, "finished_contracts")  # the manager's line has no sub-lines
    e = contract(m, 2, form="escrow", signed=1, due=5)
    run(e, "R-001", 1, 2, theirs=[4.0])
    reject(e, "R-001", 3, 3)
    e.closed = "late"
    assert line(m, "B-c53", e.id, "finished_contracts").endswith(" | CLOSED UNRELEASED (price refunded)")
    end(m, contract(m, 3, form="escrow", signed=1, due=5), 9)
    assert line(m, "B-c53", "contract-3", "finished_contracts").endswith(" | ENDED by B-c53 on day 9 (price "
                                                                         "refunded)")


def owing(m, n, day, signed=1, paid=0.0, manager="M-14b"):
    c = contract(m, n, manager, price=10, signed=signed, due=5)
    give(c, "R-001", day)
    c.paid = paid
    return c


def test_finished_order():
    m = market(30, 1, view={"finished_shown": 6})
    owing(m, 1, 4)
    owing(m, 2, 3, signed=2)
    owing(m, 3, 3, signed=1)
    owing(m, 4, 3, signed=1)
    contract(m, 5, price=10, signed=1, due=2, deadline=3)  # no delivery: by its listing deadline, day 3
    e = contract(m, 6, form="escrow", price=10, signed=1, due=2, deadline=6)  # an escrow's is its due day, 2
    e.closed = "late"
    for pid in ("B-c53", "M-14b"):
        assert [i.split(" ", 1)[0] for i in items(m, pid, "finished_contracts")] == [
            "contract-6", "contract-3", "contract-4", "contract-5", "contract-2", "contract-1"]
        assert title(m, pid, "finished_contracts") == "--- Your finished contracts (latest 6 of 6) ---"


def test_finished_shown_limit_and_title():
    m = market(30, 1, view={"finished_shown": 2})
    owing(m, 1, 2, paid=10.0)
    for pid in ("B-c53", "M-14b"):
        assert title(m, pid, "finished_contracts") == "--- Your finished contracts (latest 1 of 1) ---"
        assert len(items(m, pid, "finished_contracts")) == 1
    owing(m, 2, 3, paid=10.0)
    for pid in ("B-c53", "M-14b"):
        assert title(m, pid, "finished_contracts") == "--- Your finished contracts (latest 2 of 2) ---"
        assert len(items(m, pid, "finished_contracts")) == 2
    owing(m, 3, 4, paid=10.0)
    for pid in ("B-c53", "M-14b"):
        assert title(m, pid, "finished_contracts") == "--- Your finished contracts (latest 2 of 3) ---"
        assert [i.split(" ", 1)[0] for i in items(m, pid, "finished_contracts")] == [
            "contract-2", "contract-3", "Older"]
        assert items(m, pid, "finished_contracts")[-1] == (
            "Older finished contracts not listed: 1 (0 escrow: 0 released, 0 refunded; of the 1 plain, 1 paid in "
            "full; 0 delivered and not paid in full, with 0.00 credits of their prices unpaid; 0 never delivered).")


def test_kept_contracts_and_the_owed_list():
    m = market(30, 1, view={"finished_shown": 1, "overflow_ids": 2})
    owing(m, 1, 2, paid=2.5)
    owing(m, 2, 3, manager="M-849")
    owing(m, 3, 4, paid=10.0)
    owing(m, 4, 5, paid=9.99)
    owing(m, 5, 6)
    assert vc.finished_contracts(m, m.participants["B-c53"])[0] == (
        "--- Your finished contracts (latest 1 of 5, and 2 older delivered and not paid in full) ---")
    listed = items(m, "B-c53", "finished_contracts")
    assert [i.split(" ", 1)[0] for i in listed] == ["contract-2", "contract-4", "contract-5", "Older"]
    assert listed[1] == ("contract-4 with M-14b | task-4 | worth 60 | price 10 (plain) | paid 9.99 of 10.00 | "
                         "deadline day 5 | in use: R-001 (latest; day 5, on time)\n    R-001 delivered day 5 (in "
                         "use) | your readings: none")
    assert listed[-1] == (
        "Older finished contracts not listed: 2 (0 escrow: 0 released, 0 refunded; of the 2 plain, 1 paid in full; 1 "
        "delivered and not paid in full, with 7.50 credits of their prices unpaid; 0 never delivered). Delivered and "
        "not paid in full: contract-1 (M-14b, 7.50 unpaid).")
    assert title(m, "M-14b", "finished_contracts") == "--- Your finished contracts (latest 1 of 4) ---"
    assert items(m, "M-14b", "finished_contracts")[-1] == (
        "Older finished contracts not listed: 3 (0 escrow: 0 released, 0 refunded; of the 3 plain, 1 paid in full; 2 "
        "delivered and not paid in full, with 7.51 credits of their prices unpaid; 0 never delivered). Delivered and "
        "not paid in full: contract-4 (B-c53, 0.01 unpaid), contract-1 (B-c53, 7.50 unpaid).")
    m.cfg["view"]["overflow_ids"] = 1
    assert title(m, "B-c53", "finished_contracts") == (
        "--- Your finished contracts (latest 1 of 5, and 1 older delivered and not paid in full) ---")
    assert items(m, "B-c53", "finished_contracts")[-1].endswith(
        "; 2 delivered and not paid in full, with 17.50 credits of their prices unpaid; 0 never delivered). Delivered "
        "and not paid in full: contract-2 (M-849, 10.00 unpaid) and 1 more.")


def test_older_summary_counts():
    m = market(30, 1, view={"finished_shown": 1})
    owing(m, 1, 2, paid=10.0)
    contract(m, 2, price=10, signed=1, due=2, deadline=2)  # never delivered
    end(m, contract(m, 3, price=10, signed=1, due=2, deadline=2), 4)
    end(m, contract(m, 4, form="escrow", price=10, signed=1, due=2, deadline=2), 4)
    approve(contract(m, 5, form="escrow", price=10, signed=1, due=2, deadline=2), "R-001", 2, 2)
    contract(m, 6, form="escrow", price=10, signed=1, due=2, deadline=2).closed = "late"
    c = contract(m, 7, form="escrow", price=10, signed=1, due=2, deadline=2)
    reject(c, "R-001", 2, 2)
    c.closed = "late"
    owing(m, 8, 9, paid=10.0)
    for pid in ("B-c53", "M-14b"):
        assert title(m, pid, "finished_contracts") == "--- Your finished contracts (latest 1 of 8) ---"
        assert items(m, pid, "finished_contracts")[-1] == (
            "Older finished contracts not listed: 7 (2 ended by the buyer; 3 escrow: 1 released, 2 refunded; of the 2 "
            "plain, 1 paid in full; 0 delivered and not paid in full, with 0.00 credits of their prices unpaid; 1 "
            "never delivered).")



def test_older_unpaid_total_must_be_finite():
    """config.md, 6, "Finiteness during an episode": a sum of amounts that is not finite is never shown; the
    episode stops at its computation, and the error names the value."""
    m = market(30, 1, view={"finished_shown": 1, "overflow_ids": 1})
    for n in (1, 2, 3):
        give(contract(m, n, price=8e307, signed=1, due=5), "R-001", 2)
    assert items(m, "M-14b", "finished_contracts")[-1] == (  # 8e307 + 8e307 is a double: the line is written
        "Older finished contracts not listed: 2 (0 escrow: 0 released, 0 refunded; of the 2 plain, 0 paid in full; 2 "
        f"delivered and not paid in full, with {1.6e308:.2f} credits of their prices unpaid; 0 never delivered). "
        f"Delivered and not paid in full: contract-2 (B-c53, {8e307:.2f} unpaid) and 1 more.")
    for c in m.contracts.values():
        c.price = 1e308
    with pytest.raises(ArithmeticError, match="unpaid total .* of M-14b is not finite: inf"):
        vc.finished_contracts(m, m.participants["M-14b"])
    assert items(m, "B-c53", "finished_contracts")[-1].endswith(  # the buyer keeps contract-2: one older price
        f"with {1e308:.2f} credits of their prices unpaid; 0 never delivered). Delivered and not paid in full: "
        f"contract-1 (M-14b, {1e308:.2f} unpaid).")
    give(contract(m, 4, price=1e308, signed=1, due=5), "R-001", 2)
    with pytest.raises(ArithmeticError, match="unpaid total .* of B-c53 is not finite: inf"):
        vc.finished_contracts(m, m.participants["B-c53"])


# The market sections of the four example views (spec/examples)


def example(name, first):
    text = (ROOT / "spec" / "examples" / name).read_text(encoding="utf-8")
    return text[text.index(f"\n--- {first}"):text.index("\n\n--- Your self-written memory")]


def test_day_1_examples():
    m = market(1, 1)
    task(m, 1, 65, 9)
    task(m, 2, 59, 9)
    task(m, 3, 50, 7, buyer="B-d75")
    assert layout(vc.sections(m, m.participants["B-c53"])) == example("view_buyer_day1_off_B-c53.txt", "Your tasks")
    assert layout(vc.sections(m, m.participants["M-14b"])) == example("view_manager_day1_off_M-14b.txt", "Open posts")


def test_buyer_example_of_day_23():
    m = market(23, 2, buyers=("B-79d", "B-42c"), managers=("M-bd6", "M-f2b", "M-218"))
    me = {"buyer": "B-42c"}
    for n in range(1, 9):
        c = contract(m, n, "M-f2b", price=14, signed=1, due=5, **me)
        give(c, "R-001", 2)
        c.paid = 14.0
    for n in (9, 10, 11):
        approve(contract(m, n, "M-218", "escrow", 18, 1, 7.0, due=5, **me), "R-001", 2, 2)
    c = contract(m, 25, "M-218", "escrow", 18, 13, 7.0, worth=62, due=20, **me)
    run(c, "R-4bf", 14, 15, theirs=[7.7])
    approve(c, "R-4bf", 16, 17)
    c = contract(m, 24, "M-218", "escrow", 18, 13, 7.0, worth=45, due=21, **me)
    run(c, "R-96c", 14, 15, theirs=[6.9, 9.6])
    approve(c, "R-96c", 17, 17)
    c = contract(m, 23, "M-f2b", price=14, signed=14, worth=70, due=21, **me)
    run(c, "R-c98", 15, 16, theirs=[9.5])
    give(c, "R-c98", 17)
    c.paid = 14.0
    c = contract(m, 28, "M-bd6", "escrow", 17, 14, 7.0, worth=78, due=19, **me)
    run(c, "R-017", 15, 16, theirs=[10.0])
    approve(c, "R-017", 19, 19)
    c = contract(m, 26, "M-bd6", "escrow", 17, 16, 7.0, worth=47, due=18, **me)
    run(c, "R-99e", 17, 18, theirs=[6.6, 5.1])
    reject(c, "R-99e", 20, 21, dispute=Dispute(21, 8.2, "manager"), quality=7.4,
           message="M-bd6: R-99e submitted for review. My mean reading is 7.4 over 3 readings; stated quality 7.4.")
    release(c, 22, "ruling", received=False)
    end(m, contract(m, 27, "M-bd6", "escrow", 17, 16, 7.0, worth=42, due=19, **me), 23)
    c = contract(m, 29, "M-218", "escrow", 18, 17, 7.0, worth=61, due=21, **me)
    run(c, "R-07d", 20, 22, theirs=[5.6, 4.6, 6.3])
    give(c, "R-07d", 22, "Submitting R-07d (employee refine, tested 7.8 on day 22). It is 1 day late, so I "
                         "understand the price is scaled to 14.40.", 7.8)
    contract(m, 35, "M-218", "escrow", 18, 17, 7.0, worth=60, due=22, **me)
    contract(m, 36, "M-218", "escrow", 18, 17, 7.0, worth=63, due=25, **me)
    contract(m, 39, "M-f2b", price=14, signed=20, worth=72, due=28, **me)

    assert layout(vc.sections(m, m.participants["B-42c"])) == example(
        "view_buyer_all_B-42c_day23_round2.txt", "Your tasks")
    active = items(m, "B-42c", "active_contracts")  # 8.5 gives four of them
    assert [active[0], active[1], active[2], active[4]] == [spec(710, 712), spec(714, 717), spec(719, 720), spec(708)]
    assert items(m, "B-42c", "finished_contracts")[-2:] == [spec(936), spec(938)]


def test_manager_example_of_day_28():
    m = market(28, 3, buyers=("B-82c", "B-b79"), managers=("M-0ed", "M-ee6", "M-7f8"))
    posts = ((11, 9, "plain", None), (18, 10, "plain", None), (19, 10, "plain", None), (22, 12, "plain", None),
             (24, 12, "plain", None), (23, 13, "plain", None), (36, 22, "plain", None), (39, 23, "escrow", 5.0),
             (40, 21, "escrow", 5.0), (50, 26, "escrow", 5.0))
    for n, due, form, threshold in posts:
        post(m, n, 1, 1, form, threshold, due=due, buyer="B-b79")
    me, b1, b2 = "M-7f8", {"buyer": "B-82c"}, {"buyer": "B-b79"}
    for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14):
        c = contract(m, n, me, price=12, signed=1, due=5, **b2)
        give(c, "R-001", 2)
        c.paid = 12.0
    approve(contract(m, 13, me, "escrow", 12, 1, 5.0, due=5, **b2), "R-001", 3, 3)
    give(contract(m, 17, me, price=12, signed=1, due=9, **b2), "R-001", 5)
    give(contract(m, 30, me, price=12, signed=6, due=14, **b2), "R-001", 10)
    c = contract(m, 41, me, price=12, signed=15, due=23, **b1)
    give(c, "R-55b", 19)
    c.paid, c.kept = 12.0, "R-55b"
    approve(contract(m, 43, me, "escrow", 12, 20, 5.0, due=27, **b2), "R-97c", 27, 27)
    approve(contract(m, 46, me, "escrow", 12, 20, 5.0, due=27, **b2), "R-417", 24, 24)
    c = contract(m, 47, me, "escrow", 12, 20, 6.0, due=26, **b1)
    run(c, "R-000", 20, 22)
    approve(c, "R-721", 25, 25)
    c = contract(m, 48, me, "escrow", 12, 20, 6.0, due=28, **b1)
    run(c, "R-025", 21, 23, "manager", mine=[7.5, 7.3, 6.6, 6.4])
    reject(c, "R-025", 23, 24, "B-82c: My 4 independent readings of R-025 average 5.15 (8.6, 3.1, 3.5, 5.4), well "
                               "below the stated 7.5 and the threshold of 6. Please refine the result and resubmit "
                               "with an honest stated quality.", Dispute(25, 6.7, "manager"))
    release(c, 26, "ruling")
    c = contract(m, 49, me, "escrow", 12, 21, 6.0, due=25, **b1)
    run(c, "R-000", 21, 22)
    approve(c, "R-97a", 25, 26)

    assert layout(vc.sections(m, m.participants["M-7f8"])) == example(
        "view_manager_all_M-7f8_day28_round3.txt", "Open posts")
    assert items(m, "M-7f8", "finished_contracts")[0] == spec(945)
