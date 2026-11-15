"""The market's own mechanics (market/processes.py) against spec/processes.md: every worked number, every
notification byte for byte on markets built by hand, each escrow path, the end rule and the opening notices."""

import copy
import math
import pathlib
import re

import pytest

from market import config, processes, rng, state
from market.state import Bid, Contract, Credit, Delivery, Dispute, Market, Post, Result, Task

PM = "\u00b1"


def market(day=3, rnd=None, overrides=None):
    """Two buyers and two managers at share 1 (balance 60) at the start of a day, with a log that is a list."""
    m = Market(seed=7, cfg=config.resolve(overrides))
    for pid, role in (("B-001", "buyer"), ("B-002", "buyer"), ("M-001", "manager"), ("M-002", "manager")):
        state.add_participant(m, pid, role)
    m.day, m.round = day, rnd
    m.log = [].append
    return m


def log(m, kind):
    return [r for r in m.log.__self__ if r["type"] == kind]


def contract(m, cid="contract-1", form="escrow", price=20.0, due=9, signed=1, threshold=5.0, deadline=None,
             buyer="B-001", manager="M-001", worth=60):
    """A contract and its task; the deadline is section 3.1's unless given."""
    tid = "task-" + cid.split("-")[1]
    m.tasks.setdefault(tid, Task(tid, buyer, worth, due, state="contracted", contract=cid))
    if deadline is None:
        deadline = config.deadline(m.cfg, due, signed)
    c = m.contracts[cid] = Contract(cid, tid, "post" + cid.removeprefix("contract"), buyer, manager, form, price,
                                    threshold if form == "escrow" else None, signed, deadline)
    return c


def result(c, label, q=6.0, worker="employee", kind="fresh", parent=None, start=1, arrival=2):
    r = Result(label, worker, kind, parent, start, arrival, q)
    c.results.append(r)
    return r


def submit(c, label, day, outcome="review", decided=None, dispute=None, q=6.0):
    """A submission (escrow) or, with outcome None, a delivery (plain) of a new arrived result."""
    result(c, label, q)
    d = Delivery(label, day, 1, outcome=outcome, decided=decided, dispute=dispute)
    c.deliveries.append(d)
    return d


def pending(m, pid):
    return m.participants[pid].pending


def balances(m):
    return {p.id: p.balance for p in m.participants.values()}


def fixed_arbiter(monkeypatch, reading):
    monkeypatch.setattr(rng, "arbiter", lambda *a: {"z": 0.25, "reading": reading})


def start_of_day(m):
    """The order of episode.md 3.3 to 3.6."""
    m.day, m.round = m.day + 1, None
    for c in m.contracts.values():
        processes.arrivals(m, c)
        processes.due_notice(m, c)
    for step in (processes.rule, processes.auto_release, processes.lateness_close):
        for c in m.contracts.values():
            step(m, c)


# rel and origin


@pytest.mark.parametrize("d, text", [(12, "9 days left"), (5, "2 days left"), (4, "1 day left"), (3, "today"),
                                     (2, "1 day ago"), (1, "2 days ago"), (-7, "10 days ago")])
def test_rel(d, text):
    assert processes.rel(market(day=3), d) == text


@pytest.mark.parametrize("worker, kind, parent, text", [
    ("employee", "fresh", None, "your employee, fresh"),
    ("employee", "refine", "R-dbe", "your employee, refine of R-dbe"),
    ("manager", "fresh", None, "you, fresh"),
    ("manager", "refine", "R-0a1", "you, refine of R-0a1"),
])
def test_origin(worker, kind, parent, text):
    assert processes.origin(Result("R-5b6", worker, kind, parent, 1, 2, 5.0)) == text


# 1. Production


@pytest.mark.parametrize("worker, kind, q_parent, zs, q, days, cost", [
    ("employee", "fresh", None, (-0.0379, 1.0489, -0.3925), 5.7649, 2, 3.64),
    ("manager", "fresh", None, (1.8723, -1.0971, 0.4217), 9.1499, 2, 8.62),
    ("employee", "refine", 5.7648, (0.4241, -3.1075, -1.0166), 7.9737, 1, 2.35),
    ("manager", "refine", 3.9116, (-0.8212, 0.9382, -0.1958), 7.5100, 2, 12.92),
])
def test_the_laws_of_a_run_on_the_worked_examples(monkeypatch, worker, kind, q_parent, zs, q, days, cost):
    m = market()
    c = contract(m)
    parent = result(c, "R-dbe", q_parent) if kind == "refine" else None
    normals = iter(zs)
    monkeypatch.setattr(rng, "normal", lambda u: next(normals))
    run = processes.draw_run(m, c, worker, kind, parent)
    assert run["q"] == pytest.approx(q, abs=2e-3)
    assert (run["days"], run["cost"]) == (days, cost)
    assert (run["z_q"], run["z_t"], run["z_c"]) == zs


@pytest.mark.parametrize("worker, kind, cost, days", [("employee", "fresh", 4.0, 1), ("employee", "refine", 2.8, 1),
                                                      ("manager", "fresh", 8.0, 3), ("manager", "refine", 14.0, 1)])
def test_the_expected_costs_and_median_days(monkeypatch, worker, kind, cost, days):
    m = market()
    c = contract(m)
    result(c, "R-dbe", 5.0)
    normals = iter((0.0, 0.0, m.cfg["production"]["sig_c"] / 2))
    monkeypatch.setattr(rng, "normal", lambda u: next(normals))
    run = processes.draw_run(m, c, worker, kind, "R-dbe" if kind == "refine" else None)
    assert (run["cost"], run["days"]) == (cost, days)


def test_draw_run_keys_the_draw_by_contract_worker_kind_and_count(monkeypatch):
    m = market()
    c = contract(m, "contract-4-2")
    result(c, "R-001", 5.5, "employee", "fresh")
    result(c, "R-002", 6.5, "employee", "fresh")
    result(c, "R-003", 7.5, "manager", "refine", "R-001")
    calls = []
    real = rng.run
    monkeypatch.setattr(rng, "run", lambda *a: calls.append(a) or real(*a))
    before = copy.deepcopy(m)
    processes.draw_run(m, c, "employee", "fresh")
    processes.draw_run(m, c, "employee", "refine", "R-002")
    processes.draw_run(m, c, "manager", "refine", c.results[0])
    processes.draw_run(m, c, "manager", "fresh")
    production = m.cfg["production"]
    assert calls == [
        (7, "task-4-2", "employee", "fresh", 2, production, None),
        (7, "task-4-2", "employee", "refine", 0, production, 6.5),
        (7, "task-4-2", "manager", "refine", 1, production, 5.5),
        (7, "task-4-2", "manager", "fresh", 0, production, None),
    ]
    assert copy.deepcopy(m) == before and log(m, "run") == []


def test_a_refused_run_draws_the_same_again_and_an_accepted_one_moves_on():
    m = market()
    c = contract(m)
    first = processes.draw_run(m, c, "employee", "fresh")
    assert processes.draw_run(m, c, "employee", "fresh") == first
    processes.start_run(m, c, "employee", "fresh", None, first)
    assert processes.draw_run(m, c, "employee", "fresh") != first
    assert processes.draw_run(m, c, "manager", "fresh") == rng.run(7, "task-1", "manager", "fresh", 0,
                                                                  m.cfg["production"])


def test_a_run_cost_that_is_not_finite_stops_the_episode(monkeypatch):
    m = market()
    monkeypatch.setattr(rng, "run", lambda *a: {"z_q": 0, "z_t": 0, "z_c": 0, "q": 5.0, "days": 1,
                                                "cost": math.inf})
    with pytest.raises(ArithmeticError, match="cost"):
        processes.draw_run(m, contract(m), "manager", "fresh")


def test_start_run_creates_the_result_and_charges_and_notifies_nobody():
    m = market(day=4, rnd=2)
    c = contract(m, "contract-3")
    parent = result(c, "R-dbe", 5.0)
    run = processes.draw_run(m, c, "manager", "refine", "R-dbe")
    before = balances(m)
    r = processes.start_run(m, c, "manager", "refine", "R-dbe", run)
    assert r is c.results[-1]
    assert r == Result(rng.label(7, "task-3", 0, {"R-dbe"})["label"], "manager", "refine", "R-dbe", 4,
                       4 + run["days"], run["q"])
    assert c.label_draws == rng.label(7, "task-3", 0, {"R-dbe"})["n"]
    assert balances(m) == before and all(p.pending == [] and p.ledger == [] for p in m.participants.values())
    [record] = log(m, "run")
    assert record["day"] == 4 and record["round"] == 2
    assert {k: record[k] for k in ("contract", "label", "worker", "kind", "parent", "k", "quality", "days",
                                   "cost", "arrival", "z_q", "z_t", "z_c")} == {
        "contract": "contract-3", "label": r.label, "worker": "manager", "kind": "refine", "parent": "R-dbe",
        "k": 0, "quality": run["q"], "days": run["days"], "cost": run["cost"], "arrival": r.arrival,
        "z_q": run["z_q"], "z_t": run["z_t"], "z_c": run["z_c"]}
    assert state.in_flight(m, "M-001", "manager") == [(c, r)] and not state.arrived(m, r)
    assert processes.start_run(m, c, "employee", "refine", parent, run).parent == "R-dbe"
    assert processes.start_run(m, c, "employee", "fresh", None, run).parent is None


def test_a_label_already_used_on_the_contract_is_drawn_again_and_the_discarded_draw_counts():
    m = market()
    c, other = contract(m), contract(m, "contract-2")
    taken = rng.label(7, "task-1", 0, set())["label"]
    result(c, taken)
    run = processes.draw_run(m, c, "employee", "fresh")
    r = processes.start_run(m, c, "employee", "fresh", None, run)
    assert r.label != taken and r.label == rng.label(7, "task-1", 0, {taken})["label"]
    assert c.label_draws == rng.label(7, "task-1", 0, {taken})["n"] >= 2
    r2 = processes.start_run(m, c, "employee", "fresh", None, run)
    assert r2.label == rng.label(7, "task-1", c.label_draws - 1, {taken, r.label})["label"]
    result(other, taken)
    assert state.result(other, taken) is not state.result(c, taken)


def test_arrivals_notify_the_manager_in_the_order_the_runs_started():
    m = market(day=4)
    c3, c4 = contract(m, "contract-3"), contract(m, "contract-4", form="plain")
    result(c3, "R-dbe", arrival=3)
    result(c3, "R-5b6", 7.25, "employee", "refine", "R-dbe", start=3, arrival=4)
    result(c3, "R-111", start=3, arrival=5)
    result(c3, "R-222", worker="manager", start=2, arrival=4)
    result(c4, "R-713", worker="manager", start=2, arrival=4)
    for c in (c3, c4):
        processes.arrivals(m, c)
    assert pending(m, "M-001") == [
        "[day 4] R-5b6 (your employee, refine of R-dbe) for contract-3 has arrived.",
        "[day 4] R-222 (you, fresh) for contract-3 has arrived.",
        "[day 4] R-713 (you, fresh) for contract-4 has arrived.",
    ]
    assert all(pending(m, pid) == [] for pid in ("B-001", "B-002", "M-002"))
    first = log(m, "arrival")[0]
    assert {k: first[k] for k in ("contract", "label", "worker", "kind", "parent", "quality")} == {
        "contract": "contract-3", "label": "R-5b6", "worker": "employee", "kind": "refine", "parent": "R-dbe",
        "quality": 7.25}
    assert [r["label"] for r in log(m, "arrival")] == ["R-5b6", "R-222", "R-713"]


def test_arrivals_on_contracts_ended_released_or_closed_and_for_a_manager_out_of_business():
    m = market(day=6)
    ended = contract(m, "contract-1", form="plain")
    ended.ended = 5
    ended_escrow = contract(m, "contract-2")
    ended_escrow.ended = 4
    released = contract(m, "contract-3")
    submit(released, "R-aaa", 3, "approved", 4)
    released.release_day, released.release_how, released.paid, released.received = 4, "approved", 20.0, 4
    closed = contract(m, "contract-4")
    closed.closed = "late"
    for c, label in ((ended, "R-001"), (ended_escrow, "R-002"), (released, "R-003"), (closed, "R-004")):
        result(c, label, arrival=6)
    m.participants["M-001"].out_of_business = True
    for c in m.contracts.values():
        processes.arrivals(m, c)
    assert pending(m, "M-001") == [
        "[day 6] R-001 (your employee, fresh) for contract-1 has arrived. B-001 ended contract-1 on day 5, so it "
        "cannot be delivered.",
        "[day 6] R-002 (your employee, fresh) for contract-2 has arrived. B-001 ended contract-2 on day 4, so it "
        "cannot be delivered.",
        "[day 6] R-003 (your employee, fresh) for contract-3 has arrived.",
        "[day 6] R-004 (your employee, fresh) for contract-4 has arrived.",
    ]
    assert all(state.arrived(m, r) for c in m.contracts.values() for r in c.results)


# 2. Readings


def test_the_reading_law_on_the_worked_example_and_its_clipping(monkeypatch):
    m = market()
    c = contract(m)
    z = [-0.78218]
    monkeypatch.setattr(rng, "normal", lambda u: z[0])
    assert processes.read(m, c, result(c, "R-aaa", 9.1498), "manager") == 8.8
    z[0] = 0.1
    assert processes.read(m, c, result(c, "R-bbb", 5.0), "buyer") == 5.2
    z[0] = 3.0
    assert processes.read(m, c, result(c, "R-ccc", 9.9), "buyer") == 10.0
    z[0] = -3.0
    assert processes.read(m, c, result(c, "R-ddd", 0.2), "manager") == 0.0


def test_read_counts_each_side_apart_and_records_the_reading(monkeypatch):
    m = market(day=5, rnd=1)
    c = contract(m, "contract-2-3")
    r = result(c, "R-abc", 6.5)
    calls = []
    real = rng.reading
    monkeypatch.setattr(rng, "reading", lambda *a: calls.append(a) or real(*a))
    before = balances(m)
    values = [processes.read(m, c, r, side) for side in ("manager", "manager", "buyer", "manager")]
    key = (7, "task-2-3")
    assert calls == [key + ("manager", "R-abc", 0, 6.5, 0.5, 1), key + ("manager", "R-abc", 1, 6.5, 0.5, 1),
                     key + ("buyer", "R-abc", 0, 6.5, 2.0, 1), key + ("manager", "R-abc", 2, 6.5, 0.5, 1)]
    assert r.manager_readings == [values[0], values[1], values[3]] and r.buyer_readings == [values[2]]
    assert values[0] == rng.reading(7, "task-2-3", "manager", "R-abc", 0, 6.5, 0.5, 1)["reading"]
    assert [(x["side"], x["i"], x["reading"]) for x in log(m, "reading")] == [
        ("manager", 0, values[0]), ("manager", 1, values[1]), ("buyer", 0, values[2]), ("manager", 2, values[3])]
    assert log(m, "reading")[2]["z"] == rng.reading(7, "task-2-3", "buyer", "R-abc", 0, 6.5, 2.0, 1)["z"]
    assert balances(m) == before and all(p.pending == [] for p in m.participants.values())
    assert log(m, "notification") == [] and log(m, "money") == []


@pytest.mark.parametrize("readings, side, text", [
    ([], "manager", "readings: none"),
    ([], "buyer", "readings: none"),
    ([6.0], "manager", f"readings [6.0] mean 6.00 (n=1, {PM}0.50 standard error)"),
    ([2.3, 1.9, 2.3, 1.8, 2.6, 2.4], "manager",
     f"readings [..., 1.9, 2.3, 1.8, 2.6, 2.4] mean 2.22 (n=6, {PM}0.20 standard error)"),
    ([5.2], "buyer", f"readings [5.2] mean 5.20 (n=1, {PM}2.00 standard error)"),
    ([5.7, 5.9, 7.4, 4.3, 7.5, 6.4], "buyer",
     f"readings [..., 5.9, 7.4, 4.3, 7.5, 6.4] mean 6.20 (n=6, {PM}0.82 standard error)"),
    ([1.0, 2.6, 6.5, 1.2], "manager", f"readings [1.0, 2.6, 6.5, 1.2] mean 2.83 (n=4, {PM}0.25 standard error)"),
    ([5.1, 9.0, 2.6, 0.4], "manager", f"readings [5.1, 9.0, 2.6, 0.4] mean 4.28 (n=4, {PM}0.25 standard error)"),
    ([4.6, 0.0, 10.0, 3.0, 7.0], "buyer",
     f"readings [4.6, 0.0, 10.0, 3.0, 7.0] mean 4.92 (n=5, {PM}0.89 standard error)"),
])
def test_readings_text(readings, side, text):
    assert processes.readings_text(market(), readings, side) == text


def test_readings_text_follows_the_configured_decimals_and_listing_limit():
    m = market(overrides={"tests": {"decimals": 2, "manager_sd": 1.5}, "view": {"readings_shown": 2}})
    assert processes.readings_text(m, [6.25, 7.5, 8.0], "manager") == (
        f"readings [..., 7.50, 8.00] mean 7.25 (n=3, {PM}0.87 standard error)")
    assert processes.readings_text(m, [6.25, 7.5], "manager") == (
        f"readings [6.25, 7.50] mean 6.88 (n=2, {PM}1.06 standard error)")


# 3. Deadlines and lateness


@pytest.mark.parametrize("signed, deadline", [(1, 9), (5, 9), (6, 10), (8, 12), (9, 13), (11, 13), (20, 13)])
def test_the_deadline_examples(signed, deadline):
    m = market()
    assert config.escrow_close_days(m.cfg) == 5
    assert contract(m, due=9, signed=signed).deadline == deadline


def test_the_deadline_day_notice_goes_to_the_buyer_then_the_manager():
    m = market(day=9)
    records = m.log.__self__
    processes.due_notice(m, contract(m, form="plain"))
    text = "[day 9] contract-1 is due today (deadline day 9)."
    assert pending(m, "B-001") == [text] and pending(m, "M-001") == [text]
    assert [(r["to"], r["text"]) for r in records] == [("B-001", text), ("M-001", text)]


def test_the_due_day_notice_goes_to_the_buyer_only_and_the_deadline_notice_follows_on_its_day():
    m = market(day=9)
    c = contract(m, due=9, signed=6)
    processes.due_notice(m, c)
    assert pending(m, "B-001") == [
        "[day 9] The task of contract-1 is due today (day 9): its value to you falls for each day after it. The "
        "contract's deadline day is 10."]
    assert pending(m, "M-001") == []
    m.day = 10
    processes.due_notice(m, c)
    assert pending(m, "B-001")[1:] == pending(m, "M-001") == ["[day 10] contract-1 is due today (deadline day 10)."]
    m.day = 11
    processes.due_notice(m, c)
    assert len(pending(m, "B-001")) == 2 and len(pending(m, "M-001")) == 1


def test_no_due_notice_on_a_contract_ended_settled_or_plain_with_a_delivery():
    for day, signed in ((9, 1), (9, 6)):
        m = market(day=day)
        ended = contract(m, "contract-1", form="plain", signed=signed)
        ended.ended = 8
        ended_escrow = contract(m, "contract-2", signed=signed)
        ended_escrow.ended = 8
        released = contract(m, "contract-3", signed=signed)
        submit(released, "R-aaa", 7, "approved", 8)
        released.release_day, released.release_how = 8, "approved"
        closed = contract(m, "contract-4", signed=signed)
        closed.closed = "late"
        delivered = contract(m, "contract-5", form="plain", signed=signed)
        submit(delivered, "R-bbb", 8, None)
        for c in m.contracts.values():
            processes.due_notice(m, c)
        assert all(p.pending == [] for p in m.participants.values())


def test_an_escrow_with_a_submission_under_review_or_rejected_still_gets_its_notice():
    m = market(day=9)
    review, rejected = contract(m, "contract-1"), contract(m, "contract-2")
    submit(review, "R-aaa", 8)
    submit(rejected, "R-bbb", 6, "rejected", 7)
    for c in m.contracts.values():
        processes.due_notice(m, c)
    assert pending(m, "B-001") == pending(m, "M-001") == [
        "[day 9] contract-1 is due today (deadline day 9).", "[day 9] contract-2 is due today (deadline day 9)."]


# 4. The value of a result


def test_the_value_fraction_at_the_defaults():
    m = market()
    assert [processes.f(m, q) for q in (0.0, 1.0, 10.0)] == [0.0, 0.0, 1.0]
    assert processes.f(m, 5.5) == (4.5 / 9) ** 2
    assert [f"{65 * processes.f(m, q):.1f}" for q in range(5, 11)] == ["12.8", "20.1", "28.9", "39.3", "51.4", "65.0"]


def test_the_value_fraction_clips_before_the_exponent():
    m = market(overrides={"value": {"q0": 4, "exponent": 0.5}})
    assert processes.f(m, 3.0) == 0.0 and processes.f(m, 10.0) == 1.0
    assert processes.f(m, 5.5) == (1.5 / 6) ** 0.5 == 0.5


def test_the_value_counts_lateness_from_the_tasks_due_day():
    m = market()
    c = contract(m, due=9, signed=8, worth=65)
    assert c.deadline == 12
    on_time = 65 * (6 / 9) ** 2
    assert processes.value(m, c, 7.0, 4) == processes.value(m, c, 7.0, 9) == on_time
    assert processes.value(m, c, 7.0, 10) == on_time * (1 - 0.2 * 1)
    assert processes.value(m, c, 7.0, 12) == on_time * (1 - 0.2 * 3)
    assert processes.value(m, c, 7.0, 14) == processes.value(m, c, 7.0, 30) == 0
    assert processes.value(m, c, 1.0, 9) == processes.value(m, c, 0.3, 9) == 0


# 5. The plain contract


def test_a_payment_counts_toward_the_price_up_to_what_is_owed():
    m = market()
    c = contract(m, form="plain", price=30.0)
    assert processes.count_payment(m, c, 10.0) == (10.0, 0.0) and c.paid == 10.0
    assert state.owed(c) == 20.0 and not state.paid_in_full(c)
    assert processes.count_payment(m, c, 25.0) == (20.0, 5.0) and c.paid == 30.0
    assert state.owed(c) == 0.0 and state.paid_in_full(c)
    assert processes.count_payment(m, c, 6.0) == (0.0, 6.0) and c.paid == 30.0


def test_the_examples_of_pay():
    m = market()
    c = contract(m, form="plain", price=20.0)
    c.paid = 14.0
    assert processes.count_payment(m, c, 11.0) == (6.0, 5.0) and c.paid == 20.0


def test_paid_never_exceeds_the_price_and_is_kept_to_four_decimals():
    m = market()
    c = contract(m, form="plain", price=10.1)
    assert processes.count_payment(m, c, 3.37) == (3.37, 0.0)
    assert processes.count_payment(m, c, 3.37) == (3.37, 0.0) and c.paid == 6.74
    assert processes.count_payment(m, c, 3.37) == (3.36, 0.01) and c.paid == 10.1
    d = contract(m, "contract-2", form="plain", price=0.3)
    for _ in range(3):
        assert processes.count_payment(m, d, 0.1) == (0.1, 0.0)
    assert d.paid == 0.3 and state.paid_in_full(d)


def test_nothing_is_owed_on_an_ended_contract():
    m = market()
    c = contract(m, form="plain", price=30.0)
    c.paid, c.ended = 10.0, 3
    assert processes.count_payment(m, c, 8.0) == (0.0, 8.0) and c.paid == 10.0


# 6.8 The release and the pay cut


@pytest.mark.parametrize("signed, s, to_manager", [
    (1, 5, 20.0), (1, 9, 20.0), (1, 10, 16.0), (1, 11, 12.0), (1, 12, 8.0), (1, 13, 4.0), (1, 14, 0.0),
    (8, 12, 20.0), (8, 13, 16.0), (8, 14, 0.0),
    (6, 10, 20.0), (6, 11, 16.0), (6, 13, 8.0), (6, 14, 0.0),
    (9, 13, 20.0), (20, 13, 20.0), (20, 14, 0.0),
])
def test_the_release_factor(signed, s, to_manager):
    m = market(day=s + 1)
    c = contract(m, price=20.0, due=9, signed=signed)
    submit(c, "R-aaa", s)
    assert processes.release(m, c, "automatic") == (to_manager, 20 - to_manager)
    assert c.paid == to_manager


def test_a_release_at_the_start_of_a_day_moves_the_money_at_once():
    m = market(day=12)
    c = contract(m, price=33.33, due=9)
    submit(c, "R-aaa", 10)
    assert processes.release(m, c, "automatic") == (26.664, 33.33 - 26.664)
    assert (c.release_day, c.release_how, c.paid, c.received) == (12, "automatic", 26.664, None)
    assert not state.held(c) and state.refunded(c) == 33.33 - 26.664
    assert m.participants["M-001"].balance == 60 + 26.664
    assert m.participants["M-001"].ledger == [("escrow releases", 26.664)]
    assert m.participants["B-001"].balance == 60 + (33.33 - 26.664)
    assert m.participants["B-001"].ledger == [("escrow refunds", 33.33 - 26.664)]
    assert m.deferred == []
    [record] = log(m, "release")
    assert {k: record[k] for k in ("contract", "label", "how", "to_manager", "refunded", "days_late")} == {
        "contract": "contract-1", "label": "R-aaa", "how": "automatic", "to_manager": 26.664,
        "refunded": 33.33 - 26.664, "days_late": 1}
    assert [(r["participant"], r["kind"], r["amount"]) for r in log(m, "money")] == [
        ("M-001", "escrow releases", 26.664), ("B-001", "escrow refunds", 33.33 - 26.664)]


def test_a_release_inside_the_buyers_action_defers_the_managers_credit_only():
    m = market(day=10, rnd=2)
    c = contract(m, price=20.0, due=9)
    submit(c, "R-aaa", 10)
    m.actor = "B-001"
    assert processes.release(m, c, "approved") == (16.0, 4.0)
    assert (c.release_day, c.release_how, c.paid) == (10, "approved", 16.0)
    assert m.participants["M-001"].balance == 60 and m.participants["M-001"].ledger == [("escrow releases", 16.0)]
    assert m.participants["B-001"].balance == 64 and m.participants["B-001"].ledger == [("escrow refunds", 4.0)]
    assert m.deferred == [Credit("M-001", 16.0, "escrow_release", "B-001")]
    m.actor = None
    state.land_credits(m)
    assert m.participants["M-001"].balance == 76 and m.deferred == []


def test_a_release_in_full_refunds_nothing_and_one_of_nothing_pays_nothing():
    m = market(day=10)
    full = contract(m, "contract-1", price=20.0)
    submit(full, "R-aaa", 9)
    assert processes.release(m, full, "automatic") == (20.0, 0.0)
    assert m.participants["B-001"].ledger == [] and m.participants["M-001"].ledger == [("escrow releases", 20.0)]
    nothing = contract(m, "contract-2", price=20.0, signed=8, buyer="B-002", manager="M-002")
    submit(nothing, "R-bbb", 14)
    assert processes.release(m, nothing, "automatic") == (0.0, 20.0)
    assert nothing.paid == 0.0 and nothing.release_day == 10
    assert m.participants["M-002"].ledger == [] and m.participants["B-002"].ledger == [("escrow refunds", 20.0)]


def test_paid_text_and_back():
    m = market()
    c = contract(m, price=20.0, due=9)
    submit(c, "R-aaa", 10)
    c.paid = 16.0
    assert processes.paid_text(m, c) == "16.00 of the price 20 (submitted 1 day late)"
    c.deliveries[-1].day, c.paid = 12, 8.0
    assert processes.paid_text(m, c) == "8.00 of the price 20 (submitted 3 days late)"
    c.deliveries[-1].day, c.paid, c.price = 9, 22.5, 22.5
    assert processes.paid_text(m, c) == "22.50 of the price 22.5"
    assert processes.back(4.0) == ", and 4.00 came back to you"
    assert processes.back(33.33 - 26.664) == ", and 6.67 came back to you"
    assert processes.back(0.0) == processes.back(1e-9) == processes.back(-3.5e-15) == ""


# 6.5 Automatic release


def test_the_automatic_release_at_the_start_of_its_day():
    m = market(day=11)
    state.add_participant(m, "M-14b", "manager")
    c = contract(m, "contract-7", price=20.0, due=9, manager="M-14b")
    d = submit(c, "R-08c", 10)
    processes.auto_release(m, c)
    assert state.under_review(c) is d and c.release_day is None and log(m, "auto_release") == []
    m.day = 12
    processes.auto_release(m, c)
    assert (d.outcome, d.decided) == ("auto", 12)
    assert (c.release_day, c.release_how, c.received, c.paid) == (12, "automatic", 12, 16.0)
    assert not state.held(c) and not state.owes_fee(m, c)
    assert m.participants["M-14b"].balance == 76 and m.participants["M-14b"].ledger == [("escrow releases", 16.0)]
    assert m.participants["B-001"].balance == 64 and m.participants["B-001"].ledger == [("escrow refunds", 4.0)]
    assert pending(m, "B-001") == [
        "[day 12] R-08c on contract-7 was released: you neither approved nor rejected it by the start of day 12. "
        "It is yours; 16.00 of the price 20 went to M-14b, and 4.00 came back to you."]
    assert pending(m, "M-14b") == [
        "[day 12] R-08c on contract-7 was released: B-001 neither approved nor rejected it by the start of day 12. "
        "You received 16.00 of the price 20 (submitted 1 day late); contract-7 is closed."]
    assert [r["type"] for r in m.log.__self__] == ["auto_release", "release", "money", "money", "notification",
                                                   "notification"]
    assert [r["to"] for r in log(m, "notification")] == ["B-001", "M-14b"]
    assert {k: log(m, "auto_release")[0][k] for k in ("contract", "label", "submitted")} == {
        "contract": "contract-7", "label": "R-08c", "submitted": 10}
    processes.auto_release(m, c)
    assert len(log(m, "release")) == 1


def test_an_automatic_release_in_full_says_nothing_came_back_and_brings_a_manager_back_in_business():
    m = market(day=7)
    c = contract(m, price=20.0)
    submit(c, "R-aaa", 5)
    manager = m.participants["M-001"]
    manager.balance, manager.out_of_business = -1.5, True
    processes.auto_release(m, c)
    assert manager.balance == 18.5 and not manager.out_of_business
    assert pending(m, "B-001") == [
        "[day 7] R-aaa on contract-1 was released: you neither approved nor rejected it by the start of day 7. "
        "It is yours; 20.00 of the price 20 went to M-001."]
    assert pending(m, "M-001") == [
        "[day 7] Your balance is positive again: you are back in business.",
        "[day 7] R-aaa on contract-1 was released: B-001 neither approved nor rejected it by the start of day 7. "
        "You received 20.00 of the price 20; contract-1 is closed."]
    assert log(m, "back_in_business")[0]["cause"] == "escrow_release"


def test_the_automatic_release_follows_review_days():
    m = market(day=7, overrides={"contracts": {"escrow": {"review_days": 3}}})
    c = contract(m)
    submit(c, "R-aaa", 5)
    processes.auto_release(m, c)
    assert state.under_review(c)
    m.day = 8
    processes.auto_release(m, c)
    assert c.release_day == 8 and "by the start of day 8." in pending(m, "B-001")[0]


def test_the_automatic_release_touches_nothing_else():
    m = market(day=20)
    plain = contract(m, "contract-1", form="plain")
    submit(plain, "R-aaa", 3, None)
    rejected = contract(m, "contract-2")
    submit(rejected, "R-bbb", 3, "rejected", 4)
    empty = contract(m, "contract-3")
    before = copy.deepcopy(m)
    for c in (plain, rejected, empty):
        processes.auto_release(m, c)
        processes.auto_release(m, c, "close")
    assert copy.deepcopy(m) == before and m.log.__self__ == []


def test_at_the_close_a_submission_under_review_is_released_whatever_its_day():
    m = market(day=10)
    c = contract(m, price=20.0, due=9)
    d = submit(c, "R-aaa", 10)
    processes.auto_release(m, c, "close")
    assert (d.outcome, d.decided) == ("auto", 10)
    assert (c.release_day, c.release_how, c.received, c.paid) == (10, "close", 10, 16.0)
    assert m.participants["M-001"].balance == 76 and m.participants["B-001"].balance == 64
    assert log(m, "release")[0]["how"] == "close" and len(log(m, "money")) == 2
    assert "by the start of day 12." in pending(m, "B-001")[0]


# 6.6, 6.7 The ruling


def disputed(m, cid="contract-14", label="R-b8b", price=22.0, due=14, signed=1, submitted=12, rejected=13,
             dispute_day=14, q=6.0):
    """An escrow whose latest rejection is in dispute; the deposit has left the manager's balance."""
    c = contract(m, cid, price=price, due=due, signed=signed)
    submit(c, label, submitted, "rejected", rejected, Dispute(dispute_day), q)
    state.debit(m, c.manager, state.dispute_fee(m, c), "dispute deposits", check=True)
    m.log.__self__.clear()
    return c


def test_a_ruling_for_the_manager(monkeypatch):
    m = market(day=15)
    c = disputed(m)
    calls = []
    monkeypatch.setattr(rng, "arbiter", lambda *a: calls.append(a) or {"z": 0.2, "reading": 6.1})
    processes.rule(m, c)
    assert calls == [(7, "task-14", "R-b8b", 6.0, 0.5, 1)]
    d = c.deliveries[-1]
    assert d.dispute == Dispute(14, 6.1, "manager", False) and d.outcome == "rejected" and d.decided == 13
    assert (c.release_day, c.release_how, c.paid, c.received) == (15, "ruling", 22.0, None)
    assert state.owes_fee(m, c) and state.open_dispute(c) is None and not state.held(c)
    assert m.participants["M-001"].balance == 60 - 11 + 11 + 22
    assert m.participants["M-001"].ledger == [("dispute deposits", 11.0), ("dispute deposits returned", 11.0),
                                              ("escrow releases", 22.0)]
    assert m.participants["B-001"].balance == 60 and m.participants["B-001"].ledger == []
    head = "[day 15] The arbiter's reading of R-b8b on contract-14 is 6.1, against the threshold 5:"
    assert pending(m, "M-001") == [
        f"{head} you won the dispute. Your deposit of 11 came back, and you received 22.00 of the price 22; "
        "contract-14 is closed."]
    assert pending(m, "B-001") == [
        f"{head} the dispute went against you. The price was released to M-001. R-b8b is yours once you pay the "
        "arbiter's fee of 11 (pay_escrow_arbiter)."]
    assert [r["type"] for r in m.log.__self__] == ["money", "release", "money", "ruling", "notification",
                                                   "notification"]
    assert [r["to"] for r in log(m, "notification")] == ["M-001", "B-001"]
    ruling = log(m, "ruling")[0]
    assert {k: ruling[k] for k in ("contract", "label", "z", "reading", "threshold", "winner", "at_close",
                                   "deposit", "deposit_fate", "fee_owed", "fee_charged")} == {
        "contract": "contract-14", "label": "R-b8b", "z": 0.2, "reading": 6.1, "threshold": 5.0,
        "winner": "manager", "at_close": False, "deposit": 11.0, "deposit_fate": "returned", "fee_owed": 11.0,
        "fee_charged": 0.0}
    processes.rule(m, c)
    assert len(log(m, "ruling")) == 1


def test_a_ruling_for_the_manager_on_a_late_submission_cuts_the_pay_at_the_submission_day(monkeypatch):
    m = market(day=18)
    c = disputed(m, price=20.5, due=14, submitted=15, rejected=16, dispute_day=17)
    fixed_arbiter(monkeypatch, 5.0)
    processes.rule(m, c)
    assert c.deliveries[-1].dispute.winner == "manager"
    assert (c.paid, c.release_day) == (16.4, 18)
    assert m.participants["M-001"].balance == pytest.approx(60 + 16.4)
    assert m.participants["B-001"].ledger == [("escrow refunds", 20.5 - 16.4)]
    head = "[day 18] The arbiter's reading of R-b8b on contract-14 is 5.0, against the threshold 5:"
    assert pending(m, "M-001") == [
        f"{head} you won the dispute. Your deposit of 10.25 came back, and you received 16.40 of the price 20.5 "
        "(submitted 1 day late); contract-14 is closed."]
    assert pending(m, "B-001") == [
        f"{head} the dispute went against you. The price was released to M-001, and 4.10 came back to you. R-b8b "
        "is yours once you pay the arbiter's fee of 10.25 (pay_escrow_arbiter)."]


def test_a_ruling_for_the_manager_with_a_fee_of_zero_gives_the_result_at_once(monkeypatch):
    m = market(day=15, overrides={"contracts": {"escrow": {"dispute_fee_rate": 0.001}}})
    c = disputed(m, price=2.0)
    assert state.dispute_fee(m, c) == 0 and m.participants["M-001"].ledger == [("dispute deposits", 0.0)]
    fixed_arbiter(monkeypatch, 7.5)
    processes.rule(m, c)
    assert (c.release_day, c.release_how, c.received) == (15, "ruling", 15) and not state.owes_fee(m, c)
    assert m.participants["M-001"].ledger[1:] == [("dispute deposits returned", 0.0), ("escrow releases", 2.0)]
    assert m.participants["B-001"].ledger == []
    head = "[day 15] The arbiter's reading of R-b8b on contract-14 is 7.5, against the threshold 5:"
    assert pending(m, "M-001")[-1] == (
        f"{head} you won the dispute. Your deposit of 0 came back, and you received 2.00 of the price 2; "
        "contract-14 is closed.")
    assert pending(m, "B-001") == [
        f"{head} the dispute went against you. The price was released to M-001. R-b8b is yours once you pay the "
        "arbiter's fee of 0 (pay_escrow_arbiter)."]
    assert (log(m, "ruling")[0]["fee_owed"], log(m, "ruling")[0]["fee_charged"]) == (0.0, 0.0)


def test_a_ruling_for_the_buyer_before_the_close_day(monkeypatch):
    m = market(day=15)
    c = disputed(m)
    fixed_arbiter(monkeypatch, 4.9)
    before = balances(m)
    processes.rule(m, c)
    d = c.deliveries[-1]
    assert d.dispute == Dispute(14, 4.9, "buyer", False) and d.outcome == "rejected"
    assert state.held(c) and c.release_day is None and c.received is None and c.paid == 0.0
    assert not state.disputable(m, c) and not processes.pending(m, c) and processes.end_rule(m, c) == "stands"
    assert balances(m) == before and before["M-001"] == 49
    assert m.participants["M-001"].ledger == [("dispute deposits", 11.0)] and m.participants["B-001"].ledger == []
    head = "[day 15] The arbiter's reading of R-b8b on contract-14 is 4.9, against the threshold 5:"
    assert pending(m, "M-001") == [
        f"{head} you lost the dispute and your deposit of 11. The rejection stands; you can submit another result "
        "while the escrow holds the price."]
    assert pending(m, "B-001") == [f"{head} the dispute went your way. Your rejection stands and the price stays held."]
    assert [r["type"] for r in m.log.__self__] == ["money", "ruling", "notification", "notification"]
    assert [r["to"] for r in log(m, "notification")] == ["M-001", "B-001"]
    lost = log(m, "money")[0]
    assert (lost["participant"], lost["kind"], lost["amount"], lost["held"]) == (
        "M-001", "dispute deposit lost", 11.0, True)
    assert (log(m, "ruling")[0]["winner"], log(m, "ruling")[0]["deposit_fate"]) == ("buyer", "lost")


@pytest.mark.parametrize("day, short", [(18, False), (19, True), (20, True)])
def test_the_managers_losing_text_is_the_short_one_from_the_close_day(monkeypatch, day, short):
    m = market(day=day)
    c = disputed(m, due=14, submitted=17, rejected=day - 1, dispute_day=day - 1)
    assert state.close_day(m, c) == 19
    fixed_arbiter(monkeypatch, 0.0)
    processes.rule(m, c)
    head = f"[day {day}] The arbiter's reading of R-b8b on contract-14 is 0.0, against the threshold 5:"
    tail = "" if short else "; you can submit another result while the escrow holds the price"
    assert pending(m, "M-001") == [f"{head} you lost the dispute and your deposit of 11. The rejection stands{tail}."]
    assert pending(m, "B-001") == [f"{head} the dispute went your way. Your rejection stands and the price stays held."]


def test_a_ruling_for_the_buyer_with_a_deposit_of_zero(monkeypatch):
    m = market(day=15, overrides={"contracts": {"escrow": {"dispute_fee_rate": 0.001}}})
    c = disputed(m, price=2.0)
    fixed_arbiter(monkeypatch, 1.0)
    processes.rule(m, c)
    assert state.held(c) and balances(m)["M-001"] == 60
    assert pending(m, "M-001")[-1] == (
        "[day 15] The arbiter's reading of R-b8b on contract-14 is 1.0, against the threshold 5: you lost the "
        "dispute and your deposit of 0. The rejection stands; you can submit another result while the escrow holds "
        "the price.")


def test_the_arbiters_reading_law_and_a_tie_goes_to_the_manager(monkeypatch):
    monkeypatch.setattr(rng, "normal", lambda u: -1.15229)
    for threshold, winner in ((6.0, "manager"), (6.4, "manager"), (7.0, "buyer")):
        m = market(day=15)
        c = disputed(m, q=7.0219)
        c.threshold = threshold
        processes.rule(m, c)
        assert c.deliveries[-1].dispute.reading == 6.4 and c.deliveries[-1].dispute.winner == winner
        assert log(m, "ruling")[0]["z"] == -1.15229


def test_no_ruling_before_the_ruling_day_and_none_without_an_open_dispute(monkeypatch):
    fixed_arbiter(monkeypatch, 9.0)
    m = market(day=14)
    c = disputed(m)
    plain = contract(m, "contract-2", form="plain")
    submit(plain, "R-aaa", 3, None)
    ruled = contract(m, "contract-3")
    submit(ruled, "R-bbb", 3, "rejected", 4, Dispute(5, 4.0, "buyer"))
    before = copy.deepcopy(m)
    for x in (c, plain, ruled):
        processes.rule(m, x)
    assert copy.deepcopy(m) == before and m.log.__self__ == []
    m.day = 16
    processes.rule(m, c)
    assert c.release_day == 16 and c.deliveries[-1].dispute.winner == "manager"


def test_a_returned_deposit_brings_the_manager_back_before_the_rulings_notice(monkeypatch):
    m = market(day=15)
    manager = m.participants["M-001"]
    manager.balance = 11.0
    c = disputed(m)
    assert manager.balance == 0 and manager.out_of_business
    manager.pending.clear()
    fixed_arbiter(monkeypatch, 5.5)
    processes.rule(m, c)
    assert manager.balance == 33 and not manager.out_of_business
    assert manager.pending[0] == "[day 15] Your balance is positive again: you are back in business."
    assert manager.pending[1].endswith("you won the dispute. Your deposit of 11 came back, and you received 22.00 "
                                       "of the price 22; contract-14 is closed.")
    assert len(manager.pending) == 2 and log(m, "back_in_business")[0]["cause"] == "dispute deposits returned"


@pytest.mark.parametrize("balance, submitted, charged", [(60.0, 12, True), (11.0, 12, True), (10.99, 12, False),
                                                         (8.0, 15, True), (6.0, 15, False)])
def test_at_the_close_the_fee_of_a_ruling_for_the_manager_is_collected_if_the_balance_covers_it(
        monkeypatch, balance, submitted, charged):
    m = market(day=16)
    c = disputed(m, due=14, submitted=submitted, rejected=16, dispute_day=16)
    m.participants["B-001"].balance = balance
    fixed_arbiter(monkeypatch, 8.0)
    processes.rule(m, c)
    assert state.open_dispute(c) and m.log.__self__ == []
    processes.rule(m, c, at_close=True)
    refund = 22 - c.paid
    assert c.deliveries[-1].dispute == Dispute(16, 8.0, "manager", True)
    assert (c.release_day, c.release_how) == (16, "ruling") and c.paid == (22.0 if submitted == 12 else 17.6)
    fees = [e for e in m.participants["B-001"].ledger if e[0] == "arbiter's fees paid"]
    if charged:
        assert c.received == 16 and not state.owes_fee(m, c) and fees == [("arbiter's fees paid", 11.0)]
        assert m.participants["B-001"].balance == pytest.approx(balance + refund - 11)
    else:
        assert c.received is None and state.owes_fee(m, c) and fees == []
        assert m.participants["B-001"].balance == pytest.approx(balance + refund)
    ruling = log(m, "ruling")[0]
    assert (ruling["at_close"], ruling["fee_charged"], ruling["fee_owed"]) == (
        True, 11.0 if charged else 0.0, 0.0 if charged else 11.0)
    assert m.participants["M-001"].balance == pytest.approx(49 + 11 + c.paid)


def test_at_the_close_a_fee_of_zero_and_a_ruling_for_the_buyer(monkeypatch):
    m = market(day=16, overrides={"contracts": {"escrow": {"dispute_fee_rate": 0.001}}})
    free = disputed(m, price=2.0, dispute_day=16)
    fixed_arbiter(monkeypatch, 8.0)
    processes.rule(m, free, at_close=True)
    assert free.received == 16 and m.participants["B-001"].ledger == []
    m = market(day=16)
    lost = disputed(m, dispute_day=16)
    fixed_arbiter(monkeypatch, 2.0)
    before = balances(m)
    processes.rule(m, lost, at_close=True)
    assert lost.deliveries[-1].dispute == Dispute(16, 2.0, "buyer", True)
    assert state.held(lost) and lost.closed is None and balances(m) == before
    assert log(m, "money")[0]["kind"] == "dispute deposit lost"


# 6.9 The lateness close


def test_the_lateness_close_on_the_close_day():
    m = market(day=32)
    c = contract(m, "contract-53-2", price=7.0, due=28, signed=27)
    assert c.deadline == 31 and state.close_day(m, c) == 33
    m.tasks["task-53"].ended.append("contract-53")
    processes.lateness_close(m, c)
    assert state.held(c) and m.log.__self__ == []
    m.day = 33
    processes.lateness_close(m, c)
    assert c.closed == "late" and not state.held(c) and c.release_day is None and c.ended is None
    assert state.refunded(c) == 7.0 and processes.end_rule(m, c) == "closed"
    assert m.tasks["task-53"].state == "contracted" and m.tasks["task-53"].contract == "contract-53-2"
    assert m.participants["B-001"].balance == 67 and m.participants["B-001"].ledger == [("escrow refunds", 7.0)]
    assert m.participants["M-001"].balance == 60 and m.participants["M-001"].ledger == []
    assert pending(m, "B-001") == [
        "[day 33] The escrow of contract-53-2 closed: nothing was released by 5 days after its task's due day, so "
        "its price 7 came back to you."]
    assert pending(m, "M-001") == [
        "[day 33] The escrow of contract-53-2 closed unreleased (5 days after its task's due day): it takes no more "
        "submissions."]
    assert [r["type"] for r in m.log.__self__] == ["lateness_close", "money", "notification", "notification"]
    assert [r["to"] for r in log(m, "notification")] == ["B-001", "M-001"]
    assert (log(m, "lateness_close")[0]["contract"], log(m, "lateness_close")[0]["refunded"]) == ("contract-53-2", 7.0)
    processes.lateness_close(m, c)
    assert len(log(m, "lateness_close")) == 1 and m.participants["B-001"].balance == 67


def test_the_lateness_close_names_the_deadline_when_it_is_the_due_day_and_counts_from_the_due_day():
    m = market(day=17)
    c = contract(m, price=22.5, due=9)
    submit(c, "R-aaa", 12, "rejected", 13)
    processes.lateness_close(m, c)
    assert pending(m, "B-001") == [
        "[day 17] The escrow of contract-1 closed: nothing was released by 8 days after its deadline, so its price "
        "22.5 came back to you."]
    assert pending(m, "M-001") == [
        "[day 17] The escrow of contract-1 closed unreleased (8 days after its deadline): it takes no more "
        "submissions."]


def test_the_lateness_close_one_day_after_the_due_day():
    m = market(day=10, overrides={"value": {"late_decay": 1}})
    c = contract(m, price=7.0, due=9)
    assert config.escrow_close_days(m.cfg) == 1 and c.deadline == 9
    processes.lateness_close(m, c)
    assert pending(m, "B-001") == [
        "[day 10] The escrow of contract-1 closed: nothing was released by 1 day after its deadline, so its price "
        "7 came back to you."]
    assert pending(m, "M-001") == [
        "[day 10] The escrow of contract-1 closed unreleased (1 day after its deadline): it takes no more "
        "submissions."]


def test_something_pending_delays_the_lateness_close():
    m = market(day=14)
    review, dispute, window, stands = (contract(m, f"contract-{n}", due=9) for n in (1, 2, 3, 4))
    submit(review, "R-aaa", 13)
    submit(dispute, "R-bbb", 12, "rejected", 13, Dispute(13))
    submit(window, "R-ccc", 12, "rejected", 13)
    submit(stands, "R-ddd", 11, "rejected", 12)
    plain = contract(m, "contract-5", form="plain", due=9)
    assert [processes.pending(m, c) for c in m.contracts.values()] == [True, True, True, False, False]
    for c in m.contracts.values():
        processes.lateness_close(m, c)
    assert [c.closed for c in m.contracts.values()] == [None, None, None, "late", None]
    m.participants["M-001"].out_of_business = True
    m.day = 15
    assert not processes.pending(m, window)
    processes.lateness_close(m, window)
    assert window.closed == "late" and plain.closed is None
    for gone in ("ended", "release_day"):
        c = contract(m, f"contract-{gone}", due=9)
        setattr(c, gone, 12)
        processes.lateness_close(m, c)
        assert c.closed is None


def test_a_morning_in_order_rulings_then_releases_then_closes(monkeypatch):
    m = market(day=14)
    lost = disputed(m, "contract-1", "R-aaa", price=20.0, due=10, submitted=13, rejected=14, dispute_day=14)
    review = contract(m, "contract-2", price=20.0, due=10)
    submit(review, "R-bbb", 13)
    empty = contract(m, "contract-3", price=30.0, due=10, buyer="B-002", manager="M-002")
    for p in m.participants.values():
        p.ledger.clear()
    fixed_arbiter(monkeypatch, 3.0)
    start_of_day(m)
    assert m.day == 15 == state.close_day(m, lost)
    assert lost.deliveries[-1].dispute.winner == "buyer" and lost.closed == "late"
    assert (review.release_day, review.release_how, review.closed, review.paid) == (15, "automatic", None, 8.0)
    assert empty.closed == "late"
    assert pending(m, "M-001") == [
        "[day 15] The arbiter's reading of R-aaa on contract-1 is 3.0, against the threshold 5: you lost the "
        "dispute and your deposit of 10. The rejection stands.",
        "[day 15] R-bbb on contract-2 was released: B-001 neither approved nor rejected it by the start of day 15. "
        "You received 8.00 of the price 20 (submitted 3 days late); contract-2 is closed.",
        "[day 15] The escrow of contract-1 closed unreleased (5 days after its deadline): it takes no more "
        "submissions.",
    ]
    assert pending(m, "B-001") == [
        "[day 15] The arbiter's reading of R-aaa on contract-1 is 3.0, against the threshold 5: the dispute went "
        "your way. Your rejection stands and the price stays held.",
        "[day 15] R-bbb on contract-2 was released: you neither approved nor rejected it by the start of day 15. "
        "It is yours; 8.00 of the price 20 went to M-001, and 12.00 came back to you.",
        "[day 15] The escrow of contract-1 closed: nothing was released by 5 days after its deadline, so its price "
        "20 came back to you.",
    ]
    assert m.participants["B-001"].ledger == [("escrow refunds", 12.0), ("escrow refunds", 20.0)]
    assert m.participants["M-001"].ledger == [("escrow releases", 8.0)]
    assert m.participants["B-002"].ledger == [("escrow refunds", 30.0)] and m.deferred == []


def test_the_money_of_the_escrow_paths_is_conserved(monkeypatch):
    def total(m):
        held = sum(c.price for c in m.contracts.values() if state.held(c))
        deposits = sum(state.dispute_fee(m, c) for c in m.contracts.values() if state.open_dispute(c))
        return sum(balances(m).values()) + held + deposits

    m = market(day=14)
    won = disputed(m, "contract-1", "R-aaa", price=33.33, due=12, submitted=13, rejected=14, dispute_day=14)
    lost = disputed(m, "contract-2", "R-bbb", price=20.0, due=10, submitted=13, rejected=14, dispute_day=14)
    review = contract(m, "contract-3", price=17.77, due=10)
    submit(review, "R-ccc", 13)
    contract(m, "contract-4", price=30.0, due=10, buyer="B-002", manager="M-002")
    monkeypatch.setattr(rng, "arbiter",
                        lambda seed, stream, *a: {"z": 0.0, "reading": 9.0 if stream == "task-1" else 1.0})
    before = total(m)
    start_of_day(m)
    assert [c.closed or c.release_how for c in m.contracts.values()] == ["ruling", "late", "automatic", "late"]
    assert total(m) == pytest.approx(before - state.dispute_fee(m, lost), abs=1e-9)
    assert won.paid == 26.664 and m.deferred == []


# 7. When a contract can be ended


def test_end_rule_ended_comes_first():
    m = market(day=20)
    plain, escrow = contract(m, "contract-1", form="plain"), contract(m, "contract-2")
    plain.ended = escrow.ended = 12
    assert processes.end_rule(m, plain) == processes.end_rule(m, escrow) == "ended"


def test_end_rule_escrow_released_and_closed():
    m = market(day=20)
    released = contract(m, "contract-1")
    submit(released, "R-aaa", 8, "approved", 9)
    released.release_day, released.release_how = 9, "approved"
    ruled = contract(m, "contract-2")
    submit(ruled, "R-bbb", 8, "rejected", 9, Dispute(9, 7.0, "manager"))
    ruled.release_day, ruled.release_how = 10, "ruling"
    late, at_close = contract(m, "contract-3"), contract(m, "contract-4")
    late.closed, at_close.closed = "late", "close"
    assert [processes.end_rule(m, c) for c in m.contracts.values()] == ["released", "released", "closed", "closed"]


@pytest.mark.parametrize("signed", [1, 12])
def test_end_rule_escrow_with_a_submission(signed):
    m = market(day=11)
    review, dispute, window = (contract(m, f"contract-{n}", due=9, signed=signed) for n in (1, 2, 3))
    submit(review, "R-aaa", 10)
    submit(dispute, "R-bbb", 9, "rejected", 10, Dispute(11))
    submit(window, "R-ccc", 9, "rejected", 10)
    assert [processes.end_rule(m, c) for c in m.contracts.values()] == ["review", "dispute", "disputable"]
    m.day = 12
    assert processes.end_rule(m, window) == "stands"
    dispute.deliveries[-1].dispute = Dispute(11, 3.0, "buyer")
    assert processes.end_rule(m, dispute) == "stands"
    second = submit(window, "R-ddd", 12)
    assert processes.end_rule(m, window) == "review"
    second.outcome, second.decided = "rejected", 12
    assert processes.end_rule(m, window) == "disputable"


def test_end_rule_a_rejection_that_stands_can_be_ended_before_the_deadline():
    m = market(day=5)
    c = contract(m, due=9)
    submit(c, "R-aaa", 2, "rejected", 3)
    assert m.day <= c.deadline and processes.end_rule(m, c) == "stands"


def test_end_rule_escrow_with_nothing_submitted():
    m = market(day=9)
    c = contract(m, due=9, signed=1)
    assert processes.end_rule(m, c) == "early"
    m.day = 10
    assert processes.end_rule(m, c) == "overdue"
    later = contract(m, "contract-2", due=9, signed=8)
    assert later.deadline == 12 and processes.end_rule(m, later) == "early"
    m.day = 13
    assert processes.end_rule(m, later) == "overdue"
    for signed in (9, 11, 20):
        cap = contract(m, f"contract-{signed}", due=9, signed=signed)
        assert cap.deadline == 13
        for m.day in (signed, 13, 14, 30):
            assert processes.end_rule(m, cap) == "cap"


def test_end_rule_plain():
    m = market(day=9)
    c = contract(m, form="plain", due=9, signed=1)
    assert processes.end_rule(m, c) == "early"
    m.day = 10
    assert processes.end_rule(m, c) == "overdue"
    delivered = contract(m, "contract-2", form="plain", due=9, signed=20)
    submit(delivered, "R-aaa", 21, None)
    assert processes.end_rule(m, delivered) == "delivered"
    late_signed = contract(m, "contract-3", form="plain", due=9, signed=14)
    assert late_signed.deadline == 13
    for m.day in (14, 40):
        assert processes.end_rule(m, late_signed) == "late_signed"
    on_cap = contract(m, "contract-4", form="plain", due=9, signed=13)
    m.day = 13
    assert on_cap.deadline == 13 and processes.end_rule(m, on_cap) == "early"
    m.day = 14
    assert processes.end_rule(m, on_cap) == "overdue"


# 8. Sealed bids and their opening


def test_the_opening_rounds_of_the_examples():
    m = market()
    for (day, rnd), (open_day, open_round) in (((1, 1), (1, 3)), ((1, 2), (2, 1)), ((1, 3), (2, 2))):
        post = Post("post-1", "task-1", "B-001", day, rnd)
        m.day, m.round = open_day, open_round
        assert not state.sealed(m, post)
        m.day, m.round = (open_day, open_round - 1) if open_round > 1 else (open_day - 1, 3)
        assert state.sealed(m, post)


def test_the_opening_notices():
    m = market(day=2, rnd=3)
    state.add_participant(m, "M-003", "manager")
    m.participants["M-002"].out_of_business = True
    posts = [("post-3", "B-002", 2, 1, None), ("post-1", "B-001", 2, 1, None), ("post-2", "B-001", 2, 1, None),
             ("post-4", "B-001", 2, 2, None), ("post-5", "B-002", 2, 1, "withdrawn"), ("post-6", "B-002", 2, 1, None),
             ("post-7", "B-002", 1, 3, None), ("post-8", "B-002", 2, 1, None)]
    for pid, buyer, day, rnd, closed in posts:
        m.posts[pid] = Post(pid, "task-" + pid[5:], buyer, day, rnd, closed=closed)
    bids = [("post-1", "M-001", "open"), ("post-3", "M-003", "open"), ("post-3", "M-001", "open"),
            ("post-1", "M-002", "open"), ("post-1", "M-003", "withdrawn"), ("post-2", "M-002", "open"),
            ("post-4", "M-001", "open"), ("post-5", "M-001", "lost"), ("post-6", "M-003", "withdrawn"),
            ("post-7", "M-001", "open"), ("post-3", "M-002", "open")]
    for n, (post, manager, status) in enumerate(bids, 1):
        m.bids[f"bid-{n}"] = Bid(f"bid-{n}", post, manager, 20.0, status=status)
    processes.opening_notices(m)
    assert pending(m, "B-002") == [
        "[day 2, round 3] The bids on post-3 are opened: 2 bids, which you can accept from now."]
    assert pending(m, "B-001") == [
        "[day 2, round 3] The bids on post-1 are opened: 1 bid, which you can accept from now."]
    assert [r["to"] for r in log(m, "notification")] == ["B-002", "B-001"]
    assert all(pending(m, pid) == [] for pid in ("M-001", "M-002", "M-003"))
    m.day, m.round = 3, 1
    processes.opening_notices(m)
    assert pending(m, "B-001")[1:] == [
        "[day 3, round 1] The bids on post-4 are opened: 1 bid, which you can accept from now."]
    assert len(pending(m, "B-002")) == 1


def test_the_opening_round_follows_the_bid_window():
    m = market(day=1, rnd=3, overrides={"contracts": {"bid_window_rounds": 3}})
    m.posts["post-1"] = Post("post-1", "task-1", "B-001", 1, 2)
    m.bids["bid-1"] = Bid("bid-1", "post-1", "M-001", 20.0)
    for m.day, m.round in ((1, 3), (2, 1), (2, 2)):
        processes.opening_notices(m)
        assert pending(m, "B-001") == []
    m.round = 3
    processes.opening_notices(m)
    assert pending(m, "B-001") == [
        "[day 2, round 3] The bids on post-1 are opened: 1 bid, which you can accept from now."]


# 9. The notifications, against the spec's own templates


def test_every_notification_is_the_specs_template_filled_in(monkeypatch):
    spec = (pathlib.Path(__file__).parent.parent / "spec" / "processes.md").read_text()
    lines = [line.strip() for line in spec.splitlines()]
    arrived, deadline_day, due_day, auto_b, auto_m, won_m, won_b, lost_long, lost_short, lost_b, close_b, close_m, \
        one_bid, bids = [line for line in lines if line.startswith("[day {d}")]
    head = re.search(r"`\{head\}` = `([^`]+)`", spec)[1]
    gone = re.search(r"`\{gone\}`: `([^`]+)`", spec)[1]
    back = re.search(r"`\{back\}` = `([^`]+)`", spec)[1]
    late_1, late_n = re.search(r"`\{late\}` = `([^`]+)` if k = 1, `([^`]+)`", spec).groups()
    [paid_text] = [line for line in lines if line.startswith("{to_manager} of the price")]

    m = market(day=6)
    c = contract(m, form="plain", due=6, signed=4)
    c.ended = 5
    result(c, "R-001", worker="manager", kind="refine", parent="R-000", arrival=6)
    processes.arrivals(m, c)
    c.ended = None
    processes.due_notice(m, c)
    m.day = 8
    processes.due_notice(m, c)
    assert pending(m, "M-001") == [
        arrived.format(d=6, label="R-001", origin="you, refine of R-000", contract_id="contract-1",
                       gone=gone.format(buyer_id="B-001", contract_id="contract-1", ended_day=5)),
        deadline_day.format(d=8, contract_id="contract-1")]
    assert pending(m, "B-001") == [due_day.format(d=6, contract_id="contract-1", deadline=8),
                                   deadline_day.format(d=8, contract_id="contract-1")]

    m = market(day=14)
    c = contract(m, price=20.5, due=9)
    submit(c, "R-aaa", 12)
    processes.auto_release(m, c)
    paid = paid_text.format(to_manager="8.20", price="20.5", late=late_n.format(k=3))
    assert pending(m, "B-001") == [auto_b.format(
        d=14, label="R-aaa", contract_id="contract-1", auto_day=14, to_manager="8.20", price="20.5",
        manager_id="M-001", back=back.format(refunded="12.30"))]
    assert pending(m, "M-001") == [auto_m.format(d=14, label="R-aaa", contract_id="contract-1", buyer_id="B-001",
                                                 auto_day=14, paid_text=paid)]

    m = market(day=18)
    c = disputed(m, price=20.5, due=14, submitted=15, rejected=16, dispute_day=17)
    m.participants["M-001"].pending.clear()
    fixed_arbiter(monkeypatch, 5.0)
    processes.rule(m, c)
    said = head.format(label="R-b8b", contract_id="contract-14", reading="5.0", threshold="5")
    paid = paid_text.format(to_manager="16.40", price="20.5", late=late_1.format(k=1))
    assert pending(m, "M-001") == [won_m.format(d=18, head=said, fee="10.25", paid_text=paid,
                                                contract_id="contract-14")]
    assert pending(m, "B-001") == [won_b.format(d=18, head=said, manager_id="M-001", label="R-b8b", fee="10.25",
                                                back=back.format(refunded="4.10"))]

    for day, lost in ((18, lost_long), (19, lost_short)):
        m = market(day=day)
        c = disputed(m, due=14, submitted=17, rejected=day - 1, dispute_day=day - 1)
        m.participants["M-001"].pending.clear()
        fixed_arbiter(monkeypatch, 4.5)
        processes.rule(m, c)
        processes.lateness_close(m, c)
        said = head.format(label="R-b8b", contract_id="contract-14", reading="4.5", threshold="5")
        closed = dict(d=day, contract_id="contract-14", late=5, its="its deadline", price="22")
        closes = day == 19
        assert pending(m, "M-001") == [lost.format(d=day, head=said, fee="11")] + [close_m.format(**closed)] * closes
        assert pending(m, "B-001") == [lost_b.format(d=day, head=said)] + [close_b.format(**closed)] * closes

    m = market(day=4, rnd=2)
    for n, managers in ((1, ["M-001"]), (2, ["M-001", "M-002"])):
        m.posts[f"post-{n}"] = Post(f"post-{n}", f"task-{n}", "B-001", 3, 3)
        for manager in managers:
            bid = state.new_bid_id(m)
            m.bids[bid] = Bid(bid, f"post-{n}", manager, 20.0)
    processes.opening_notices(m)
    assert pending(m, "B-001") == [one_bid.format(d=4, r=2, post_id="post-1", n=1),
                                   bids.format(d=4, r=2, post_id="post-2", n=2)]


# Copies


def test_a_copy_draws_what_the_market_draws_and_leaves_it_untouched():
    m = market(day=15)
    c = disputed(m, q=5.2)
    plain = contract(m, "contract-2", form="plain", price=30.0)
    r = result(plain, "R-aaa", 6.5)
    result(plain, "R-bbb", arrival=16)
    review = contract(m, "contract-3")
    submit(review, "R-ccc", 13)
    late = contract(m, "contract-4", due=9)
    m.posts["post-9"] = Post("post-9", "task-9", "B-002", 15, 2)
    m.bids["bid-1"] = Bid("bid-1", "post-9", "M-002", 20.0)
    before = copy.deepcopy(m)

    def play(x):
        cs = list(x.contracts.values())
        run = processes.draw_run(x, cs[1], "employee", "refine", "R-aaa")
        started = processes.start_run(x, cs[1], "employee", "refine", "R-aaa", run)
        reading = processes.read(x, cs[1], cs[1].results[0], "buyer")
        paid = processes.count_payment(x, cs[1], 12.5)
        start_of_day(x)
        x.round = 1
        processes.opening_notices(x)
        return run, started, reading, paid, cs[0].deliveries[-1].dispute, [p.pending for p in x.participants.values()]

    shadow = copy.deepcopy(m)
    seen = play(shadow)
    assert shadow.log is None and m.log.__self__ == [] and copy.deepcopy(m) == before
    assert shadow.contracts["contract-14"].deliveries[-1].dispute.winner in ("manager", "buyer")
    assert shadow.contracts["contract-3"].release_day == 16 and shadow.contracts["contract-4"].closed == "late"
    assert r.buyer_readings == [] and plain.paid == 0.0 and late.closed is None and state.open_dispute(c)
    assert shadow.participants["B-002"].pending[-1].startswith("[day 16, round 1] The bids on post-9 are opened")
    assert play(m) == seen
    shadow.records = m.records
    assert copy.deepcopy(m) == shadow
    assert {x["type"] for x in m.log.__self__} >= {"run", "reading", "arrival", "ruling", "auto_release", "release",
                                                   "lateness_close", "money", "notification"}
