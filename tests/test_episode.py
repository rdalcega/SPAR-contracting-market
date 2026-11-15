"""The episode outside its rounds (market/episode.py) against spec/episode.md: the start, the start of a day and
of a round in their fixed order, the close rule, the close's steps, the settlement, the scores with the money
check, and the core metrics of spec/running.md 6.7, on markets built by hand."""

import copy
import pathlib
import pickle
import re

import pytest

from market import config, episode, processes, rng, state, turns, view
from market.state import Bid, Contract, Delivery, Dispute, Market, Message, Payment, Post, Result, Task

SPEC = pathlib.Path(__file__).resolve().parents[1] / "spec"


def market(day=3, rnd=None, overrides=None, shares=(1.0, 1.0, 1.0, 1.0)):
    """Two buyers and two managers (share 1: balance 60, budget 30) with a log that is a list."""
    m = Market(seed=7, cfg=config.resolve(overrides))
    people = (("B-001", "buyer"), ("B-002", "buyer"), ("M-001", "manager"), ("M-002", "manager"))
    for (pid, role), share in zip(people, shares):
        state.add_participant(m, pid, role, share)
    m.day, m.round, m.last_arrival = day, rnd, 10
    m.log = [].append
    return m


def log(m, *kinds):
    return [r for r in m.log.__self__ if r["type"] in kinds]


def notes(m):
    """Every notification in the order produced, as (recipient, line)."""
    return [(r["to"], r["text"]) for r in log(m, "notification")]


def task(m, n, buyer="B-001", worth=60, due=9, status="contracted"):
    t = m.tasks[f"task-{n}"] = Task(f"task-{n}", buyer, worth, due, state=status)
    return t


def contract(m, cid="contract-1", form="escrow", price=20.0, due=9, signed=1, threshold=5.0, buyer="B-001",
             manager="M-001", worth=60):
    """A contract with the deadline of processes.md 3.1, and its task when it is the task's first."""
    tid = "task-" + cid.split("-")[1]
    if tid not in m.tasks:
        m.tasks[tid] = Task(tid, buyer, worth, due, state="contracted", contract=cid)
    c = m.contracts[cid] = Contract(cid, tid, "post" + cid.removeprefix("contract"), buyer, manager, form, price,
                                    threshold if form == "escrow" else None, signed,
                                    config.deadline(m.cfg, m.tasks[tid].due, signed))
    return c


def run(c, label, q=6.0, worker="employee", kind="fresh", parent=None, start=1, arrival=2):
    r = Result(label, worker, kind, parent, start, arrival, q)
    c.results.append(r)
    return r


def give(c, label, day, rnd=1, outcome=None, decided=None, dispute=None, q=6.0):
    """A delivery (plain) or, with an outcome, a submission (escrow) of a new arrived result."""
    run(c, label, q)
    d = Delivery(label, day, rnd, outcome=outcome, decided=decided, dispute=dispute)
    c.deliveries.append(d)
    return d


def disputed(c, label, day, q=6.0):
    """A submission made on `day`, rejected the next day and disputed the day after: the dispute is open."""
    return give(c, label, day, outcome="rejected", decided=day + 1, dispute=Dispute(day + 2), q=q)


def arbiter(monkeypatch, readings):
    """Pin the arbiter's reading per label (or one reading for all)."""
    monkeypatch.setattr(rng, "arbiter", lambda seed, stream, label, q, sd, decimals: {
        "z": 0.25, "reading": readings[label] if isinstance(readings, dict) else readings})


def pin_arrivals(monkeypatch, counts, worth=50, offset=4):
    """Replace the three arrival draws; returns the list of their keys as they are made."""
    keys = []

    def draw(name, value):
        def f(seed, *parts):
            keys.append((name, seed, *parts))
            return value(*parts) if callable(value) else value
        monkeypatch.setattr(rng, name, f)

    draw("arrivals", lambda day, seat, p: counts[seat])
    draw("worth", worth)
    draw("deadline", offset)
    return keys


def value(worth, q, late):
    """processes.md 4 at the defaults, written out."""
    return worth * min(1.0, max(0.0, (q - 1) / 9)) ** 2 * max(0, 1 - 0.2 * late)


def balances(m):
    return {p.id: round(p.balance, 6) for p in m.participants.values()}


# 1. Episode start


@pytest.mark.parametrize("seed", range(12))
def test_start_participants_ids_seats_shares_balances_and_l(seed):
    cfg = config.resolve(None)
    m = episode.start(seed, cfg)
    people = list(m.participants.values())
    assert [(p.role, p.seat) for p in people] == [("buyer", 0), ("buyer", 1), ("manager", 0), ("manager", 1),
                                                  ("manager", 2)]
    assert len({p.id for p in people}) == 5
    for p in people:
        assert re.fullmatch(("B" if p.role == "buyer" else "M") + "-[0-9a-f]{3}", p.id)
        assert m.participants[p.id] is p
        assert p.share == rng.share(seed, p.role, p.seat, 0.6, 1.4, 2) == round(p.share, 2)
        assert 0.6 <= p.share <= 1.4
        assert p.balance == round(60 * p.share, 2) == config.start_balance(cfg, p.share)
        assert not p.out_of_business and p.pending == [] and p.ledger == [] and p.turns == 0
    assert m.last_arrival == rng.horizon(seed, 10, 0.1, 54)
    assert 10 <= m.last_arrival <= 54
    assert (m.seed, m.cfg, m.day, m.round, m.records) == (seed, cfg, 0, None, 0)
    assert not m.tasks and not m.contracts and m.close_day is None
    assert pickle.loads(pickle.dumps(m)) == m == copy.deepcopy(m)


def test_start_draws_depend_only_on_their_keys():
    """1, 1.3; randomness.md 4: a seat added to one role changes no other seat's ID or share, nor L."""
    small = episode.start(4, config.resolve(None))
    big = episode.start(4, config.resolve({"participants": {"buyers": 3, "managers": 5}}))
    facts = [[(p.id, p.share, p.balance) for p in state.of_role(m, role)] for m in (small, big)
             for role in ("buyer", "manager")]
    assert facts[2][:2] == facts[0] and facts[3][:3] == facts[1]
    assert big.last_arrival == small.last_arrival
    assert [p.id[0] for p in big.participants.values()] == list("BBBMMMMM")


def test_start_draws_an_id_again_until_no_earlier_participant_has_it(monkeypatch):
    """1.1: only full IDs must differ, so B-005 and M-005 both exist; the attempt counts from 0 per seat."""
    drawn = iter([5, 5, 5, 7, 5, 0xabc, 5, 0xabc, 4095])
    monkeypatch.setattr(rng, "integer", lambda us, a, b: next(drawn))
    m = episode.start(0, config.resolve(None))
    assert list(m.participants) == ["B-005", "B-007", "M-005", "M-abc", "M-fff"]


def test_start_ids_stay_distinct_with_many_seats():
    m = episode.start(1, config.resolve({"participants": {"buyers": 600, "managers": 600}}))
    assert len(m.participants) == 1200
    assert [p.seat for p in state.of_role(m, "buyer")] == list(range(600))


def test_start_worked_numbers(monkeypatch):
    """1.2: uniforms 0.7543, 0.4056, 0.1340, 0.8446, 0.0525 give L = 14; 1.3: share 1.36 gives 81.60 and
    40.80; a share of exactly 1 gives the configured amounts."""
    real = rng.uniforms
    monkeypatch.setattr(rng, "uniforms", lambda seed, *parts: (
        iter([0.7543, 0.4056, 0.1340, 0.8446, 0.0525]) if parts == ("horizon",) else real(seed, *parts)))
    monkeypatch.setattr(rng, "share", lambda seed, role, seat, lo, hi, decimals: 1.36 if role == "buyer" else 1.0)
    m = episode.start(0, config.resolve(None))
    assert m.last_arrival == 14
    buyer, manager = state.of_role(m, "buyer")[0], state.of_role(m, "manager")[0]
    assert (buyer.balance, config.per_task_budget(m.cfg, buyer.share)) == (81.6, 40.8)
    assert manager.balance == 60 and config.per_task_budget(m.cfg, 1.0) == 30


def test_start_l_is_at_most_the_cap(monkeypatch):
    """1.2: the cap takes the rest of the tail (54 at the defaults; config.md 6.1)."""
    real = rng.uniforms
    monkeypatch.setattr(rng, "uniforms", lambda seed, *parts: (
        iter([0.99] * 2000) if parts == ("horizon",) else real(seed, *parts)))
    assert episode.start(0, config.resolve(None)).last_arrival == 54
    cfg = config.resolve({"time": {"horizon_min_days": 3, "horizon_end_prob": 0.5}})
    assert episode.start(0, cfg).last_arrival == 3 + 7


def test_fresh_start_record():
    """1.4; running.md 6.3 A: the participants in seat order with role, seat, model, share, start balance and
    budget, and L; then the function definitions every call's record names (running.md 6.3 D); nothing else is
    logged or shown at the start."""
    cfg = config.resolve({"name": "pilot", "message_channels": "managers",
                          "models": {"manager_seats": {1: {"provider": "mock"}}}})
    m = episode.start(5, cfg)
    m.log = [].append
    episode.fresh_start(m)
    record, functions = m.log.__self__
    assert functions == {"n": 2, "type": "functions", "day": 0, "round": None, "functions": turns.functions(cfg)}
    assert (record["n"], record["type"], record["day"], record["round"]) == (1, "fresh_start", 0, None)
    assert (record["name"], record["seed"], record["message_channels"]) == ("pilot", 5, "managers")
    assert record["last_arrival_day"] == m.last_arrival
    people = list(m.participants.values())
    assert [(s["id"], s["role"], s["seat"], s["share"]) for s in record["participants"]] == [
        (p.id, p.role, p.seat, p.share) for p in people]
    for s, p in zip(record["participants"], people):
        assert s["start_balance"] == p.balance == round(60 * p.share, 2)
        assert s["budget"] == (round(30 * p.share, 2) if p.role == "buyer" else None)
        assert s["model"] == config.seat_spec(cfg, p.role, p.seat)
    assert [s["model"]["provider"] for s in record["participants"]] == ["openrouter"] * 3 + ["mock", "openrouter"]
    assert all(p.pending == [] for p in people)


# 3. The start of a day


def test_day_1_arrivals(monkeypatch):
    """3.1, 3.2: day 1 gives every buyer first_day_tasks tasks with no count draw; ids rise in seat order; each
    task brings its budget and its notification; managers are told nothing."""
    worths = iter([57, 44, 80, 40])
    keys = pin_arrivals(monkeypatch, None, worth=lambda *parts: next(worths), offset=6)
    m = market(day=0, rnd=3, shares=(1.36, 1.13, 1.0, 1.0))
    episode.start_day(m)
    assert (m.day, m.round) == (1, None)
    assert [(t.id, t.buyer, t.worth, t.due, t.state, t.post, t.contract) for t in m.tasks.values()] == [
        ("task-1", "B-001", 57, 7, "queued", None, None), ("task-2", "B-001", 44, 7, "queued", None, None),
        ("task-3", "B-002", 80, 7, "queued", None, None), ("task-4", "B-002", 40, 7, "queued", None, None)]
    assert keys == [("worth", 7, 1, 0, 0, 40, 80), ("deadline", 7, "task-1", 4, 9),
                    ("worth", 7, 1, 0, 1, 40, 80), ("deadline", 7, "task-2", 4, 9),
                    ("worth", 7, 1, 1, 0, 40, 80), ("deadline", 7, "task-3", 4, 9),
                    ("worth", 7, 1, 1, 1, 40, 80), ("deadline", 7, "task-4", 4, 9)]
    assert notes(m) == [
        ("B-001", "[day 1] New task task-1 in your queue, worth 57 to you, due day 7 (6 days left). It brought "
                  "40.80 credits, added to your balance."),
        ("B-001", "[day 1] New task task-2 in your queue, worth 44 to you, due day 7 (6 days left). It brought "
                  "40.80 credits, added to your balance."),
        ("B-002", "[day 1] New task task-3 in your queue, worth 80 to you, due day 7 (6 days left). It brought "
                  "33.90 credits, added to your balance."),
        ("B-002", "[day 1] New task task-4 in your queue, worth 40 to you, due day 7 (6 days left). It brought "
                  "33.90 credits, added to your balance.")]
    assert m.participants["B-001"].pending == [text for to, text in notes(m) if to == "B-001"]
    assert balances(m) == {"B-001": 81.6 + 81.6, "B-002": 67.8 + 67.8, "M-001": 60, "M-002": 60}
    assert m.participants["B-001"].ledger == [("per-task budgets", 40.8)] * 2
    assert m.participants["M-001"].pending == [] and m.participants["M-001"].ledger == []
    assert [r["type"] for r in m.log.__self__][:5] == ["day", "arrivals", "task", "money", "notification"]
    assert log(m, "day") == [{"n": 1, "type": "day", "day": 1, "round": None}]
    assert [(r["buyer"], r["tasks"]) for r in log(m, "arrivals")] == [("B-001", 2), ("B-002", 2)]
    assert [(r["task"], r["buyer"], r["worth"], r["D"], r["due"], r["budget"]) for r in log(m, "task")] == [
        ("task-1", "B-001", 57, 6, 7, 40.8), ("task-2", "B-001", 44, 6, 7, 40.8),
        ("task-3", "B-002", 80, 6, 7, 33.9), ("task-4", "B-002", 40, 6, 7, 33.9)]
    assert [(r["participant"], r["kind"], r["amount"]) for r in log(m, "money")] == [
        ("B-001", "per-task budgets", 40.8)] * 2 + [("B-002", "per-task budgets", 33.9)] * 2


def test_later_days_draw_a_count_per_buyer_and_stop_after_l(monkeypatch):
    """3.2: days 2..L draw each buyer's count, keyed by day and seat (a count of 0 included); the task counter
    goes on; after L nothing arrives and nothing is drawn."""
    keys = pin_arrivals(monkeypatch, {0: 0, 1: 3}, worth=61, offset=1)
    m = market(day=1)
    task(m, 1, status="queued")
    m.last_arrival = 2
    episode.start_day(m)
    assert keys[:2] == [("arrivals", 7, 2, 0, 0.5), ("arrivals", 7, 2, 1, 0.5)]
    assert [k[:5] for k in keys[2:] if k[0] == "worth"] == [("worth", 7, 2, 1, j) for j in range(3)]
    assert [k[2] for k in keys if k[0] == "deadline"] == ["task-2", "task-3", "task-4"]
    assert [(t.id, t.buyer, t.due) for t in m.tasks.values()][1:] == [
        ("task-2", "B-002", 3), ("task-3", "B-002", 3), ("task-4", "B-002", 3)]
    assert notes(m)[0] == ("B-002", "[day 2] New task task-2 in your queue, worth 61 to you, due day 3 (1 day "
                                    "left). It brought 30.00 credits, added to your balance.")
    assert [(r["buyer"], r["tasks"]) for r in log(m, "arrivals")] == [("B-001", 0), ("B-002", 3)]
    assert balances(m)["B-001"] == 60 and balances(m)["B-002"] == 150
    del keys[:]
    seen = len(m.log.__self__)
    episode.start_day(m)
    assert m.day == 3 and keys == [] and len(m.tasks) == 4
    assert m.log.__self__[seen:] == [{"n": seen + 1, "type": "day", "day": 3, "round": None}]
    assert balances(m)["B-002"] == 150


def test_a_budget_of_0_is_still_credited(monkeypatch):
    """3.2: a share that rounds to 0 gives a budget of 0: a money movement and an accounts entry all the same."""
    pin_arrivals(monkeypatch, None)
    m = market(day=0, shares=(0.0, 1.0, 1.0, 1.0))
    episode.start_day(m)
    buyer = m.participants["B-001"]
    assert buyer.balance == 0 and buyer.ledger == [("per-task budgets", 0.0)] * 2
    assert buyer.pending[0].endswith("It brought 0.00 credits, added to your balance.")
    assert len([r for r in log(m, "money") if r["participant"] == "B-001"]) == 2


def test_the_real_draws_of_the_arrivals_and_the_day_loop_to_the_close(monkeypatch):
    """3.2 with randomness.md 3.4 to 3.6, then 2 and 5.2: with nothing ever done, the market's last day is
    max(L, the latest due day + close_after_due_days - 1)."""
    played = []
    monkeypatch.setattr(episode.rounds, "play", lambda m, order, ctx: played.append((m.day, m.round)))
    cfg = config.resolve(None)
    m = episode.start(2, cfg)
    while m.close_day is None:
        before = len(m.tasks)
        episode.play_day(m, None)
        new = list(m.tasks.values())[before:]
        expected = []
        if m.day <= m.last_arrival:
            for b in state.of_role(m, "buyer"):
                n = 2 if m.day == 1 else rng.arrivals(2, m.day, b.seat, 0.5)
                expected += [(b.id, rng.worth(2, m.day, b.seat, j, 40, 80)) for j in range(n)]
        assert [(t.buyer, t.worth) for t in new] == expected
        for t in new:
            assert t.due == m.day + rng.deadline(2, t.id, 4, 9) and 40 <= t.worth <= 80
            assert 4 <= t.due - m.day <= 9
    assert list(m.tasks) == [f"task-{n + 1}" for n in range(len(m.tasks))]
    latest = max(t.due for t in m.tasks.values())
    assert m.close_day == m.day == m.days_completed == max(m.last_arrival, latest + 4)
    assert m.close_day == config.last_possible_day(cfg, m.last_arrival, latest)
    assert m.close_reason == "due_limit"
    assert played == [(d, r) for d in range(1, m.close_day + 1) for r in (1, 2, 3)]
    for b in state.of_role(m, "buyer"):
        mine = [t for t in m.tasks.values() if t.buyer == b.id]
        assert b.balance == pytest.approx(config.start_balance(cfg, b.share)
                                          + len(mine) * config.per_task_budget(cfg, b.share))


def start_of_day_market(monkeypatch):
    """Day 11 of a market whose day 12 has every step of section 3 to do, across eight contracts."""
    pin_arrivals(monkeypatch, {0: 1, 1: 0})
    arbiter(monkeypatch, {"R-b8b": 6.1, "R-c7c": 4.9})
    m = market(day=11, rnd=3)
    m.last_arrival = 12
    c1 = contract(m, "contract-1", "plain", due=12)
    run(c1, "R-000", arrival=9)
    run(c1, "R-0a1", kind="refine", parent="R-000", start=10, arrival=12)
    run(c1, "R-0b2", worker="manager", start=10, arrival=12)
    run(c1, "R-0c3", start=11, arrival=13)
    c2 = contract(m, "contract-2", due=12, buyer="B-002")
    c2.ended = 10
    run(c2, "R-0d4", start=9, arrival=12)
    contract(m, "contract-3", due=12, signed=9, manager="M-002")
    contract(m, "contract-4", price=7.0, due=7, signed=5, buyer="B-002", manager="M-002")
    c5 = contract(m, "contract-5", due=9)
    give(c5, "R-111", 10, outcome="review")
    c6 = contract(m, "contract-6", price=22.0, due=14, buyer="B-002", manager="M-002")
    disputed(c6, "R-b8b", 9)
    c7 = contract(m, "contract-7", price=10.0, due=7, manager="M-002")
    disputed(c7, "R-c7c", 9)
    c8 = contract(m, "contract-8", price=30.0, due=6, signed=5, buyer="B-002")
    give(c8, "R-d8d", 10, outcome="rejected", decided=11)
    lost = m.participants["M-002"]
    lost.balance, lost.out_of_business = -3.0, True
    return m


def test_the_start_of_a_day_in_its_fixed_order(monkeypatch):
    """3.1 to 3.7: arrivals of tasks; per contract its result arrivals then its notice; every ruling; every
    automatic release; every lateness close; recipients in each step's order; a manager back in business is
    told so before the ruling that brought it back."""
    m = start_of_day_market(monkeypatch)
    episode.start_day(m)
    assert (m.day, m.round) == (12, None)
    head6 = "[day 12] The arbiter's reading of R-b8b on contract-6 is 6.1, against the threshold 5:"
    head7 = "[day 12] The arbiter's reading of R-c7c on contract-7 is 4.9, against the threshold 5:"
    assert notes(m) == [
        ("B-001", "[day 12] New task task-9 in your queue, worth 50 to you, due day 16 (4 days left). It brought "
                  "30.00 credits, added to your balance."),
        ("M-001", "[day 12] R-0a1 (your employee, refine of R-000) for contract-1 has arrived."),
        ("M-001", "[day 12] R-0b2 (you, fresh) for contract-1 has arrived."),
        ("B-001", "[day 12] contract-1 is due today (deadline day 12)."),
        ("M-001", "[day 12] contract-1 is due today (deadline day 12)."),
        ("M-001", "[day 12] R-0d4 (your employee, fresh) for contract-2 has arrived. B-002 ended contract-2 on "
                  "day 10, so it cannot be delivered."),
        ("B-001", "[day 12] The task of contract-3 is due today (day 12): its value to you falls for each day "
                  "after it. The contract's deadline day is 13."),
        ("M-002", "[day 12] Your balance is positive again: you are back in business."),
        ("M-002", f"{head6} you won the dispute. Your deposit of 11 came back, and you received 22.00 of the "
                  "price 22; contract-6 is closed."),
        ("B-002", f"{head6} the dispute went against you. The price was released to M-002. R-b8b is yours once "
                  "you pay the arbiter's fee of 11 (pay_escrow_arbiter)."),
        ("M-002", f"{head7} you lost the dispute and your deposit of 5. The rejection stands."),
        ("B-001", f"{head7} the dispute went your way. Your rejection stands and the price stays held."),
        ("B-001", "[day 12] R-111 on contract-5 was released: you neither approved nor rejected it by the start "
                  "of day 12. It is yours; 16.00 of the price 20 went to M-001, and 4.00 came back to you."),
        ("M-001", "[day 12] R-111 on contract-5 was released: B-001 neither approved nor rejected it by the "
                  "start of day 12. You received 16.00 of the price 20 (submitted 1 day late); contract-5 is "
                  "closed."),
        ("B-002", "[day 12] The escrow of contract-4 closed: nothing was released by 5 days after its task's "
                  "due day, so its price 7 came back to you."),
        ("M-002", "[day 12] The escrow of contract-4 closed unreleased (5 days after its task's due day): it "
                  "takes no more submissions."),
        ("B-001", "[day 12] The escrow of contract-7 closed: nothing was released by 5 days after its deadline, "
                  "so its price 10 came back to you."),
        ("M-002", "[day 12] The escrow of contract-7 closed unreleased (5 days after its deadline): it takes no "
                  "more submissions."),
    ]
    for p in m.participants.values():
        assert p.pending == [text for to, text in notes(m) if to == p.id]
    steps = [(r["type"], r.get("contract") or r.get("task") or r.get("buyer"))
             for r in log(m, "day", "arrivals", "task", "arrival", "ruling", "auto_release", "lateness_close")]
    assert steps == [("day", None), ("arrivals", "B-001"), ("task", "task-9"), ("arrivals", "B-002"),
                     ("arrival", "contract-1"), ("arrival", "contract-1"), ("arrival", "contract-2"),
                     ("ruling", "contract-6"), ("ruling", "contract-7"), ("auto_release", "contract-5"),
                     ("lateness_close", "contract-4"), ("lateness_close", "contract-7")]
    assert all(r["day"] == 12 and r["round"] is None for r in m.log.__self__)
    assert [r["label"] for r in log(m, "arrival")] == ["R-0a1", "R-0b2", "R-0d4"]
    assert balances(m) == {"B-001": 60 + 30 + 4 + 10, "B-002": 60 + 7, "M-001": 60 + 16, "M-002": -3 + 11 + 22}
    assert not m.participants["M-002"].out_of_business and m.counts["back_in_business_events"] == 1
    c = m.contracts
    assert (c["contract-6"].release_how, c["contract-6"].received, c["contract-6"].paid) == ("ruling", None, 22.0)
    assert (c["contract-5"].release_how, c["contract-5"].received, c["contract-5"].paid) == ("automatic", 12, 16.0)
    assert [c[f"contract-{n}"].closed for n in range(1, 9)] == [None, None, None, "late", None, None, "late", None]
    assert state.held(c["contract-3"]) and state.held(c["contract-8"])


def test_a_pending_item_delays_the_lateness_close_to_the_first_day_without_one(monkeypatch):
    """3.6: contract-8's rejection is disputable through day 12, so the escrow closes on day 13 (its close day
    was 11); 3.3: the run of day 13 arrives, and contract-3 reaches its deadline day, later than its task's."""
    m = start_of_day_market(monkeypatch)
    episode.start_day(m)
    del m.log.__self__[:]
    episode.start_day(m)
    assert notes(m) == [
        ("M-001", "[day 13] R-0c3 (your employee, fresh) for contract-1 has arrived."),
        ("B-001", "[day 13] contract-3 is due today (deadline day 13)."),
        ("M-002", "[day 13] contract-3 is due today (deadline day 13)."),
        ("B-002", "[day 13] The escrow of contract-8 closed: nothing was released by 7 days after its task's "
                  "due day, so its price 30 came back to you."),
        ("M-001", "[day 13] The escrow of contract-8 closed unreleased (7 days after its task's due day): it "
                  "takes no more submissions.")]
    assert m.contracts["contract-8"].closed == "late" and len(m.tasks) == 9


def test_an_automatic_release_brings_a_manager_back_before_its_notifications(monkeypatch):
    """3.5, 3.7: the release's credit reaches the manager at once; the result is the buyer's on day d."""
    m = market(day=11)
    c = contract(m, due=14)
    give(c, "R-111", 10, outcome="review")
    seller = m.participants["M-001"]
    seller.balance, seller.out_of_business = -1.0, True
    episode.start_day(m)
    assert notes(m) == [
        ("M-001", "[day 12] Your balance is positive again: you are back in business."),
        ("B-001", "[day 12] R-111 on contract-1 was released: you neither approved nor rejected it by the start "
                  "of day 12. It is yours; 20.00 of the price 20 went to M-001."),
        ("M-001", "[day 12] R-111 on contract-1 was released: B-001 neither approved nor rejected it by the "
                  "start of day 12. You received 20.00 of the price 20; contract-1 is closed.")]
    assert (seller.balance, seller.out_of_business, c.received) == (19.0, False, 12)


def test_a_credit_that_leaves_the_balance_at_or_below_0_brings_nobody_back(monkeypatch):
    """3.7, 9.3: strictly above 0 only; the day's start never puts a manager out of business."""
    arbiter(monkeypatch, 9.0)
    m = market(day=11)
    c = contract(m, price=22.0, due=14)
    disputed(c, "R-b8b", 9)
    seller = m.participants["M-001"]
    seller.balance, seller.out_of_business = -33.0, True
    episode.start_day(m)
    assert (seller.balance, seller.out_of_business) == (0.0, True)
    assert not any("back in business" in text for to, text in notes(m))
    assert m.counts.get("back_in_business_events", 0) == 0 and m.counts.get("out_of_business_events", 0) == 0


def test_no_due_notice_for_ended_settled_or_delivered_contracts(monkeypatch):
    """3.3 (b): neither notice for an ended contract, a released or closed escrow, or a plain contract with a
    delivery; an escrow with a submission still gets its notice."""
    arbiter(monkeypatch, 9.0)
    m = market(day=11)
    contract(m, "contract-1", "plain", due=12).ended = 11
    give(contract(m, "contract-2", "plain", due=12), "R-001", 11)
    released = contract(m, "contract-3", due=12)
    give(released, "R-002", 11, outcome="approved", decided=11)
    released.release_day, released.release_how, released.received, released.paid = 11, "approved", 11, 20.0
    contract(m, "contract-4", due=12).closed = "late"
    give(contract(m, "contract-5", due=12), "R-003", 11, outcome="review")
    give(contract(m, "contract-6", "plain", due=12, signed=9), "R-004", 11)
    episode.start_day(m)
    assert notes(m) == [("B-001", "[day 12] contract-5 is due today (deadline day 12)."),
                        ("M-001", "[day 12] contract-5 is due today (deadline day 12).")]


# 4. The start of each round


def test_start_round_order_stamp_and_bid_openings(monkeypatch):
    """4: the order is drawn for (day, round) over all participants in seat order, those out of business
    included; then the round is set; then the openings, posts in creation order, counting only the open bids of
    managers in business."""
    drawn = []
    monkeypatch.setattr(rng, "order", lambda seed, day, r, ids: drawn.append((seed, day, r, ids, m.round))
                        or ids[::-1])
    m = market(day=4, rnd=None)
    state.add_participant(m, "M-003", "manager")
    m.participants["M-002"].out_of_business = True
    for n, (buyer, day, rnd, closed) in enumerate([("B-001", 3, 3, None), ("B-002", 3, 3, None),
                                                   ("B-001", 3, 3, "withdrawn"), ("B-001", 3, 3, None),
                                                   ("B-002", 4, 1, None), ("B-002", 3, 3, None)], 1):
        m.posts[f"post-{n}"] = Post(f"post-{n}", f"task-{n}", buyer, day, rnd, closed=closed)
    bids = [("post-1", "M-001", "open"), ("post-1", "M-002", "open"), ("post-2", "M-001", "open"),
            ("post-2", "M-003", "withdrawn"), ("post-3", "M-001", "open"), ("post-4", "M-002", "open"),
            ("post-5", "M-001", "open"), ("post-6", "M-003", "open"), ("post-6", "M-001", "open")]
    for n, (post, manager, status) in enumerate(bids, 1):
        m.bids[f"bid-{n}"] = Bid(f"bid-{n}", post, manager, 20.0, status=status)
    order = episode.start_round(m, 2)
    assert order == ["M-003", "M-002", "M-001", "B-002", "B-001"]
    assert drawn == [(7, 4, 2, ["B-001", "B-002", "M-001", "M-002", "M-003"], None)]
    assert m.round == 2
    assert notes(m) == [
        ("B-001", "[day 4, round 2] The bids on post-1 are opened: 1 bid, which you can accept from now."),
        ("B-002", "[day 4, round 2] The bids on post-2 are opened: 1 bid, which you can accept from now."),
        ("B-002", "[day 4, round 2] The bids on post-6 are opened: 2 bids, which you can accept from now.")]
    assert episode.start_round(m, 3) == order
    assert notes(m)[3:] == [
        ("B-002", "[day 4, round 3] The bids on post-5 are opened: 1 bid, which you can accept from now.")]


def test_start_round_draws_a_permutation_keyed_by_day_and_round():
    """4 step 1; randomness.md 3.7."""
    m = market(day=6)
    ids = list(m.participants)
    orders = {(d, r): rng.order(7, d, r, ids) for d in (6, 7) for r in (1, 2, 3)}
    for (d, r), expected in orders.items():
        m.day = d
        assert episode.start_round(m, r) == expected and sorted(expected) == sorted(ids)
        assert m.round == r
    assert len({tuple(o) for o in orders.values()}) > 1 and notes(m) == []


# 5. The end of a day: the close rule


def test_delivered_and_done():
    """5.1, each clause, read over every contract ever made on the task."""
    m = market(day=12)
    never = task(m, 1, status="queued")
    posted = task(m, 2, status="posted")
    withdrawn = task(m, 3, status="withdrawn")
    plain_open = contract(m, "contract-4", "plain")
    plain_given = contract(m, "contract-5", "plain")
    give(plain_given, "R-001", 5)
    review = contract(m, "contract-6")
    give(review, "R-002", 5, outcome="review")
    released = contract(m, "contract-7")
    give(released, "R-003", 5, outcome="approved", decided=5)
    released.release_day = 5
    late = contract(m, "contract-8")
    late.closed = "late"
    ended = contract(m, "contract-9")
    give(ended, "R-004", 5, outcome="rejected", decided=5)
    ended.ended = 8
    m.tasks["task-9"].state = "queued"
    again = contract(m, "contract-10")
    again.ended = 6
    give(contract(m, "contract-10-2", "plain"), "R-005", 9)
    ended_plain = contract(m, "contract-11", "plain")
    ended_plain.ended = 11
    refunded = contract(m, "contract-12")
    refunded.closed = "close"
    answers = {t.id: (episode.delivered(m, t), episode.done(m, t)) for t in m.tasks.values()}
    assert answers == {
        refunded.task: (False, False),
        never.id: (False, False), posted.id: (False, False), withdrawn.id: (False, True),
        plain_open.task: (False, False), plain_given.task: (True, True), review.task: (False, False),
        released.task: (True, True), late.task: (False, True), ended.task: (False, False),
        again.task: (True, True), ended_plain.task: (False, False)}


def closing(day=12, last=10):
    """A market at the end of a day with one task, delivered and paid: rule 2 closes it."""
    m = market(day=day, rnd=3)
    m.last_arrival = last
    c = contract(m, "contract-1", "plain", due=9)
    give(c, "R-001", 9, rnd=2)
    c.paid = 20.0
    m.participants["B-001"].last_view = config.round_index(m.cfg, day, 3)
    return m, c


def test_close_rule_1_no_close_before_l():
    m, c = closing(day=9, last=10)
    assert episode.close_reason(m) is None
    m.day = 10
    assert episode.close_reason(m) == "all_done"


def test_close_rule_2_and_its_reasons():
    """5.2 rule 2, 5.3: all_done when every task is delivered, all_settled when some task is done otherwise."""
    m, c = closing()
    assert episode.close_reason(m) == "all_done"
    task(m, 2, status="withdrawn", due=30)
    assert episode.close_reason(m) == "all_settled"
    del m.tasks["task-2"]
    contract(m, "contract-3", due=5).closed = "late"
    assert episode.close_reason(m) == "all_settled"
    task(m, 4, status="queued", due=9)
    assert episode.close_reason(m) is None


def test_an_unseen_unpaid_delivery_holds_the_market_open():
    """5.1, 5.2: a delivery of round g is seen by the buyer's view of a later round; paid in full, or seen
    once, it holds nothing; a buyer who never pays cannot hold the market open."""
    m, c = closing(day=10)
    c.paid = 0.0
    c.deliveries[0].day, c.deliveries[0].round = 10, 3
    buyer = m.participants["B-001"]
    assert buyer.last_view == 30 and not episode.seen(m, c)
    assert episode.close_reason(m) is None
    c.paid = 20.0 - 1e-6
    assert episode.close_reason(m) == "all_done"
    c.paid = 20.0 - 2e-6
    assert episode.close_reason(m) is None
    m.day, buyer.last_view = 11, 31
    assert episode.seen(m, c) and episode.close_reason(m) == "all_done"
    give(c, "R-002", 11, rnd=3)
    assert episode.seen(m, c) and episode.close_reason(m) == "all_done"
    other = contract(m, "contract-2", "plain", due=9, buyer="B-002")
    give(other, "R-003", 11, rnd=1)
    assert not episode.seen(m, other) and episode.close_reason(m) is None
    give(contract(m, "contract-3", due=9, buyer="B-002"), "R-004", 11, rnd=3, outcome="approved", decided=11)
    m.contracts["contract-3"].release_day = 11
    m.participants["B-002"].last_view = 32
    assert episode.close_reason(m) == "all_done"


def test_a_turn_lost_to_a_failed_call_saw_nothing_and_the_next_view_sees_all():
    """5.1; rounds.md 10.1: the lost view counts as never built; the next view not lost sees every delivery
    made before it was built, those of the lost turn's own round included."""
    m, c = closing(day=10)
    c.paid = 0.0
    c.deliveries[0].day, c.deliveries[0].round = 10, 1
    buyer = m.participants["B-001"]
    buyer.last_view = 27
    m.round = 2
    text, undo = view.build(m, "B-001")
    assert buyer.last_view == 29 and episode.seen(m, c)
    view.undo(m, "B-001", undo)
    assert buyer.last_view == 27 and not episode.seen(m, c)
    m.round = 3
    assert episode.close_reason(m) is None
    c.deliveries[0].round = 3
    text, undo = view.build(m, "B-001")
    assert not episode.seen(m, c) and episode.close_reason(m) is None
    view.undo(m, "B-001", undo)
    m.day, m.round = 11, 1
    view.build(m, "B-001")
    assert episode.seen(m, c)
    m.round = 3
    assert episode.close_reason(m) == "all_done"


def test_close_rule_3_the_due_limit():
    """5.2 rules 3 and 4: from the latest due day of any task (done, withdrawn or never posted) +
    close_after_due_days - 1, whatever is still open; never before L."""
    m, c = closing(day=13, last=10)
    task(m, 2, status="queued", due=10)
    assert episode.close_reason(m) is None
    m.day = 14
    assert episode.close_reason(m) == "due_limit"
    task(m, 3, status="withdrawn", due=11)
    assert episode.close_reason(m) is None
    m.day = 15
    assert episode.close_reason(m) == "due_limit"
    m.last_arrival = 16
    assert episode.close_reason(m) is None
    m.day = 16
    assert episode.close_reason(m) == "due_limit"
    m.cfg = config.resolve({"time": {"close_after_due_days": 7}})
    assert episode.close_reason(m) is None
    m.day = 17
    assert episode.close_reason(m) == "due_limit"


def test_the_due_limit_closes_over_an_unseen_delivery():
    m, c = closing(day=13)
    c.paid = 0.0
    c.deliveries[0].day, c.deliveries[0].round = 13, 3
    assert episode.close_reason(m) == "due_limit"
    m.day = 12
    c.deliveries[0].day = 12
    m.participants["B-001"].last_view = 36
    assert episode.close_reason(m) is None


# 2. The day loop


def test_play_day_starts_the_day_plays_each_round_and_checks_the_close(monkeypatch):
    """2: the start of the day, then for each round its start and the round, then the close rule; the close is
    logged with its day and reason and nothing is shown to anyone."""
    pin_arrivals(monkeypatch, {0: 0, 1: 0})
    calls = []

    def play(m, order, ctx):
        calls.append((m.day, m.round, order, ctx, len(m.log.__self__)))

    monkeypatch.setattr(episode.rounds, "play", play)
    m, c = closing(day=9, last=10)
    c.deliveries[0].day = 5
    m.posts["post-9"] = Post("post-9", "task-9", "B-002", 9, 3)
    m.bids["bid-1"] = Bid("bid-1", "post-9", "M-001", 20.0)
    episode.play_day(m, "ctx")
    ids = list(m.participants)
    assert [call[:4] for call in calls] == [(10, r, rng.order(7, 10, r, ids), "ctx") for r in (1, 2, 3)]
    assert [r["type"] for r in m.log.__self__[:calls[0][4]]] == ["day", "arrivals", "arrivals"]
    assert [r["text"] for r in m.log.__self__[calls[1][4] - 1:calls[1][4]]] == [
        "[day 10, round 2] The bids on post-9 are opened: 1 bid, which you can accept from now."]
    assert (m.close_day, m.close_reason, m.days_completed, m.day, m.round) == (10, "all_done", 1, 10, 3)
    assert m.log.__self__[-1] == {"n": len(m.log.__self__), "type": "close", "day": 10, "round": 3,
                                  "close_day": 10, "reason": "all_done"}
    assert notes(m) == [("B-002", "[day 10, round 2] The bids on post-9 are opened: 1 bid, which you can "
                                  "accept from now.")]


def test_play_day_without_a_close_goes_on(monkeypatch):
    pin_arrivals(monkeypatch, {0: 0, 1: 0})
    monkeypatch.setattr(episode.rounds, "play", lambda m, order, ctx: None)
    m, c = closing(day=7, last=10)
    episode.play_day(m, None)
    episode.play_day(m, None)
    assert (m.day, m.close_day, m.close_reason, m.days_completed) == (9, None, None, 2)
    assert log(m, "close") == []
    episode.play_day(m, None)
    assert (m.day, m.close_day, m.close_reason, m.days_completed) == (10, 10, "all_done", 3)


# 6. At the close


def closing_market(monkeypatch):
    """The close day 20 with five escrows: nothing submitted; under review; three open disputes."""
    arbiter(monkeypatch, 7.0)
    m = market(day=20, rnd=3)
    contract(m, "contract-1", due=18)
    give(contract(m, "contract-2", price=30.0, due=19), "R-222", 20, outcome="review", q=7.0)
    c3 = contract(m, "contract-3", price=40.0, due=17, threshold=6.0, buyer="B-002", manager="M-002")
    give(c3, "R-333", 18, outcome="rejected", decided=19, dispute=Dispute(20), q=7.0)
    c4 = contract(m, "contract-4", price=10.0, due=18, threshold=6.0, buyer="B-002", manager="M-002")
    give(c4, "R-444", 18, outcome="rejected", decided=19, dispute=Dispute(20), q=7.0)
    c5 = contract(m, "contract-5", price=50.0, due=18, threshold=9.0, buyer="B-002")
    give(c5, "R-555", 18, outcome="rejected", decided=19, dispute=Dispute(20), q=7.0)
    plain = contract(m, "contract-6", "plain", due=30, buyer="B-002")
    run(plain, "R-666", start=19, arrival=21)
    m.posts["post-9"] = Post("post-9", "task-9", "B-001", 20, 1)
    m.bids["bid-1"] = Bid("bid-1", "post-9", "M-001", 20.0)
    m.participants["B-002"].balance = 13.0
    seller = m.participants["M-002"]
    seller.balance, seller.out_of_business = -5.0, True
    return m


def test_the_close_in_its_three_steps(monkeypatch):
    """6: every open dispute is ruled whatever its ruling day, then every submission under review is released,
    then every price still held goes back; the fee of a ruling for the manager is charged when the buyer's
    balance at that moment covers it."""
    m = closing_market(monkeypatch)
    episode.close(m)
    assert (m.day, m.round) == (20, None)
    outcomes = [(r["type"], r["contract"]) for r in log(m, "ruling", "auto_release", "refund_at_close")]
    assert outcomes == [("ruling", "contract-3"), ("ruling", "contract-4"), ("ruling", "contract-5"),
                        ("auto_release", "contract-2"), ("refund_at_close", "contract-1"),
                        ("refund_at_close", "contract-5")]
    assert [(r["winner"], r["at_close"], r["deposit"], r["deposit_fate"], r["fee_charged"])
            for r in log(m, "ruling")] == [("manager", True, 20.0, "returned", 20.0),
                                           ("manager", True, 5.0, "returned", 0.0),
                                           ("buyer", True, 25.0, "lost", 0.0)]
    assert [(r["contract"], r["how"], r["to_manager"], r["refunded"], r["days_late"])
            for r in log(m, "release")] == [("contract-3", "ruling", 32.0, 8.0, 1),
                                            ("contract-4", "ruling", 10.0, 0.0, 0),
                                            ("contract-2", "close", 24.0, 6.0, 1)]
    assert [(r["contract"], r["refunded"]) for r in log(m, "refund_at_close")] == [("contract-1", 20.0),
                                                                                   ("contract-5", 50.0)]
    assert balances(m) == {"B-001": 60 + 6 + 20, "B-002": 13 + 8 - 20 + 50, "M-001": 60 + 24,
                           "M-002": -5 + 20 + 32 + 5 + 10}
    c = m.contracts
    assert [(x.release_how, x.release_day, x.received, x.closed, x.paid) for x in c.values()] == [
        (None, None, None, "close", 0.0), ("close", 20, 20, None, 24.0), ("ruling", 20, 20, None, 32.0),
        ("ruling", 20, None, None, 10.0), (None, None, None, "close", 0.0), (None, None, None, None, 0.0)]
    review = c["contract-2"].deliveries[0]
    assert (review.outcome, review.decided) == ("auto", 20)
    assert [x.deliveries[0].dispute.winner for x in list(c.values())[2:5]] == ["manager", "manager", "buyer"]
    assert all(x.deliveries[0].dispute.at_close for x in list(c.values())[2:5])
    assert not any(state.held(x) for x in c.values())
    assert m.participants["B-002"].ledger == [("escrow refunds", 8.0), ("arbiter's fees paid", 20.0),
                                              ("escrow refunds", 50.0)]
    assert m.participants["M-002"].ledger == [("dispute deposits returned", 20.0), ("escrow releases", 32.0),
                                              ("dispute deposits returned", 5.0), ("escrow releases", 10.0)]
    seller = m.participants["M-002"]
    assert not seller.out_of_business and m.counts["back_in_business_events"] == 1
    assert [r["cause"] for r in log(m, "back_in_business")] == ["dispute deposits returned"]
    assert log(m, "arrival") == [] and c["contract-6"].paid == 0.0
    assert m.posts["post-9"].closed is None and m.bids["bid-1"].status == "open"
    moved = [(r["participant"], r["kind"], r["amount"]) for r in log(m, "money")]
    assert moved == [("M-002", "dispute deposits returned", 20.0), ("M-002", "escrow releases", 32.0),
                     ("B-002", "escrow refunds", 8.0), ("B-002", "arbiter's fees paid", -20.0),
                     ("M-002", "dispute deposits returned", 5.0), ("M-002", "escrow releases", 10.0),
                     ("M-001", "dispute deposit lost", 25.0), ("M-001", "escrow releases", 24.0),
                     ("B-001", "escrow refunds", 6.0), ("B-001", "escrow refunds", 20.0),
                     ("B-002", "escrow refunds", 50.0)]


def test_the_settlement_after_that_close(monkeypatch):
    """7.3: a release at the close is received on the close day (lateness from the task's due day) while the pay
    was counted at the submission day; a result whose fee could not be covered is never the buyer's."""
    m = closing_market(monkeypatch)
    episode.close(m)
    out = episode.settle(m)
    rows = {e["contract"]: e for e in out["settlement"]}
    assert list(rows) == [f"contract-{n}" for n in range(1, 7)]
    assert [(e["outcome"], e["settled_label"], e["receive_day"], e["days_late"], e["realized_value"])
            for e in rows.values()] == [
        ("refunded_at_close", None, None, None, None),
        ("released", "R-222", 20, 1, value(60, 7.0, 1)),
        ("released", "R-333", 20, 3, value(60, 7.0, 3)),
        ("released", "R-444", None, 0, 0.0),
        ("refunded_at_close", None, None, None, 0.0),
        ("undelivered", None, None, None, None)]
    assert rows["contract-2"]["realized_value"] == pytest.approx(21.3333, abs=1e-4)
    assert rows["contract-3"]["realized_value"] == pytest.approx(10.6667, abs=1e-4)
    assert [(e["paid"], e["refunded"], e["setup_fee"]) for e in rows.values()] == [
        (0.0, 20.0, 2.0), (24.0, 6.0, 3.0), (32.0, 8.0, 4.0), (10.0, 0.0, 1.0), (0.0, 50.0, 5.0), (0.0, 0.0, 0.0)]
    assert [e["disputes"] for e in rows.values()] == [
        [], [], [{"label": "R-333", "winner": "manager", "at_close": True}],
        [{"label": "R-444", "winner": "manager", "at_close": True}],
        [{"label": "R-555", "winner": "buyer", "at_close": True}], []]
    scores = {s["id"]: s for s in out["scores"]}
    assert scores["M-002"]["out_of_business"] is False
    metrics = out["metrics"]
    assert (metrics["disputes_ruled_at_close"], metrics["escrow_released_at_close"],
            metrics["escrow_refunded_at_close"], metrics["arbiter_fees_owed"], metrics["arbiter_fees_paid"],
            metrics["runs_unfinished_at_end"], metrics["costs"]["dispute_costs"]) == (3, 1, 2, 0, 0, 1, 45.0)


def test_a_fee_of_0_at_the_close_makes_the_result_the_buyers_on_the_close_day(monkeypatch):
    """6 step 1, first case: a fee that rounds to 0 on a tiny price."""
    arbiter(monkeypatch, 7.0)
    m = market(day=20, rnd=3, overrides={"contracts": {"escrow": {"dispute_fee_rate": 0.001}}})
    c = contract(m, price=2.0, due=19)
    give(c, "R-001", 19, outcome="rejected", decided=19, dispute=Dispute(20), q=7.0)
    m.participants["B-001"].balance = -4.0
    episode.close(m)
    assert (c.release_how, c.received, c.paid) == ("ruling", 20, 2.0)
    assert m.participants["B-001"].balance == -4.0 and m.participants["B-001"].ledger == []
    (entry,) = episode.settle(m)["settlement"]
    assert (entry["receive_day"], entry["days_late"], entry["realized_value"]) == (20, 1, value(60, 7.0, 1))


def test_the_close_fee_is_tested_before_the_refunds_of_steps_2_and_3(monkeypatch):
    """6 step 1: the balance tested is the buyer's at that moment, with a tolerance of 1e-9."""
    arbiter(monkeypatch, 7.0)
    for balance, charged in ((10.0 - 1e-10, True), (10.0 - 1e-8, False)):
        m = market(day=20, rnd=3)
        give(contract(m, "contract-1", due=19), "R-001", 20, outcome="review")
        contract(m, "contract-2", due=19)
        c = contract(m, "contract-3", due=19)
        give(c, "R-003", 19, outcome="rejected", decided=19, dispute=Dispute(20))
        m.participants["B-001"].balance = balance
        episode.close(m)
        assert (c.received == 20) is charged
        assert m.participants["B-001"].balance == pytest.approx(balance + 4 + 20 - (10 if charged else 0))


# 7. Settlement: the value each contract realises


def test_settlement_of_a_plain_contract():
    """7.2: the delivery in use (the kept label, else the latest), received on its own day; what was paid does
    not change the value; no delivery, no value."""
    m = market(day=20)
    kept = contract(m, "contract-1", "plain", due=9)
    give(kept, "R-001", 8, q=7.0)
    give(kept, "R-002", 11, q=10.0)
    kept.kept, kept.paid = "R-001", 3.5
    latest = contract(m, "contract-2", "plain", due=9, signed=8)
    give(latest, "R-001", 8, q=10.0)
    give(latest, "R-002", 12, q=5.5)
    worthless = contract(m, "contract-3", "plain", due=9)
    give(worthless, "R-001", 14, q=9.0)
    give(contract(m, "contract-4", "plain", due=9), "R-001", 9, q=0.5)
    contract(m, "contract-5", "plain", due=9).ended = 12
    contract(m, "contract-6", "plain", due=9)
    rows = episode.settle(m)["settlement"]
    assert [(e["outcome"], e["deliveries"], e["settled_label"], e["settled_q"], e["receive_day"], e["days_late"],
             e["realized_value"]) for e in rows] == [
        ("delivered", 2, "R-001", 7.0, 8, 0, 60 * (6 / 9) ** 2),
        ("delivered", 2, "R-002", 5.5, 12, 3, 60 * 0.25 * (1 - 0.2 * 3)),
        ("delivered", 1, "R-001", 9.0, 14, 5, 0.0),
        ("delivered", 1, "R-001", 0.5, 9, 0, 0.0),
        ("ended", 0, None, None, None, None, None),
        ("undelivered", 0, None, None, None, None, None)]
    assert rows[0]["realized_value"] == pytest.approx(26.6667, abs=1e-4)
    assert rows[1]["realized_value"] == pytest.approx(6.0)
    assert rows[1]["deadline_day"] == 12 and rows[1]["days_late"] == 3


def test_settlement_of_an_escrow_contract():
    """7.3: the released result at the day it became the buyer's; never received, 0; submissions and nothing
    released, 0; no submission, no value."""
    m = market(day=20)

    def released(n, how, submitted, release_day, received, due=9, signed=1):
        c = contract(m, f"contract-{n}", due=due, signed=signed)
        give(c, "R-001", submitted, outcome="rejected" if how == "ruling" else "approved", q=7.0)
        c.release_day, c.release_how, c.received, c.paid = release_day, how, received, 20.0
        return c

    released(1, "approved", 9, 11, 11)
    released(2, "automatic", 7, 9, 9)
    released(3, "ruling", 8, 10, 13)
    released(4, "ruling", 8, 10, None)
    released(5, "approved", 12, 12, 12, signed=8)
    released(6, "ruling", 12, 14, None)
    ended = contract(m, "contract-7")
    give(ended, "R-001", 9, outcome="rejected", decided=9)
    ended.ended = 12
    late = contract(m, "contract-8")
    give(late, "R-001", 9, outcome="rejected", decided=9)
    late.closed = "late"
    contract(m, "contract-9").closed = "late"
    contract(m, "contract-10").ended = 12
    contract(m, "contract-11").closed = "close"
    twice = contract(m, "contract-12")
    give(twice, "R-001", 8, outcome="rejected", decided=8, q=10.0)
    give(twice, "R-002", 9, outcome="approved", decided=9, q=5.5)
    twice.release_day, twice.release_how, twice.received, twice.paid = 9, "approved", 9, 20.0
    rows = episode.settle(m)["settlement"]
    full = 60 * (6 / 9) ** 2
    assert [(e["outcome"], e["deliveries"], e["settled_label"], e["receive_day"], e["days_late"],
             e["realized_value"]) for e in rows] == [
        ("released", 1, "R-001", 11, 2, full * (1 - 0.2 * 2)),
        ("released", 1, "R-001", 9, 0, full),
        ("released", 1, "R-001", 13, 4, full * (1 - 0.2 * 4)),
        ("released", 1, "R-001", None, 0, 0.0),
        ("released", 1, "R-001", 12, 3, full * (1 - 0.2 * 3)),
        ("released", 1, "R-001", None, 3, 0.0),
        ("ended", 1, None, None, None, 0.0),
        ("closed_late", 1, None, None, None, 0.0),
        ("closed_late", 0, None, None, None, None),
        ("ended", 0, None, None, None, None),
        ("refunded_at_close", 0, None, None, None, None),
        ("released", 2, "R-002", 9, 0, 60 * 0.25)]
    assert rows[4]["deadline_day"] == 12
    assert [e["settled_q"] for e in rows] == [7.0] * 6 + [None] * 5 + [5.5]


def test_a_settlement_entry_in_full():
    """running.md 6.5: every field, for a plain and an escrow contract; the credit fields as the market holds
    them."""
    m = market(day=20)
    plain = contract(m, "contract-1", "plain", price=20.0, due=9, signed=2, worth=64)
    give(plain, "R-0aa", 10, q=5.5)
    plain.paid = 20.0
    for amount, to_price in ((18.7, 12.5), (10.2, 7.5), (6.8, 0.0), (4.6, 0.0)):
        m.payments.append(Payment(11, 1, "B-001", "M-001", amount, "contract-1", to_price, False))
    m.payments.append(Payment(11, 1, "B-001", "M-001", 9.0, None, 0.0, False))
    escrow = contract(m, "contract-2", price=33.33, due=9, threshold=7.5, buyer="B-002", manager="M-002")
    give(escrow, "R-0bb", 8, outcome="rejected", decided=8, dispute=Dispute(8, 4.0, "buyer"), q=8.0)
    give(escrow, "R-0cc", 10, outcome="rejected", decided=10, dispute=Dispute(11, 7.5, "manager"), q=7.0)
    escrow.release_day, escrow.release_how, escrow.received, escrow.paid = 12, "ruling", 13, 26.664
    first, second = episode.settle(m)["settlement"]
    assert first == {
        "contract": "contract-1", "task": "task-1", "buyer": "B-001", "manager": "M-001", "form": "plain",
        "price": 20.0, "threshold": None, "worth": 64, "due_day": 9, "deadline_day": 9, "signed_day": 2,
        "outcome": "delivered", "deliveries": 1, "settled_label": "R-0aa", "settled_q": 5.5, "receive_day": 10,
        "days_late": 1, "realized_value": 64 * 0.25 * 0.8, "paid": 20.0, "paid_beyond_price": 20.3,
        "refunded": 0.0, "setup_fee": 0.0, "disputes": []}
    assert 6.2 + 2.7 + 6.8 + 4.6 != 20.3  # README, "Number formats and rounding": SUM, not plain addition
    assert second == {
        "contract": "contract-2", "task": "task-2", "buyer": "B-002", "manager": "M-002", "form": "escrow",
        "price": 33.33, "threshold": 7.5, "worth": 60, "due_day": 9, "deadline_day": 9, "signed_day": 1,
        "outcome": "released", "deliveries": 2, "settled_label": "R-0cc", "settled_q": 7.0, "receive_day": 13,
        "days_late": 4, "realized_value": value(60, 7.0, 4), "paid": 26.664, "paid_beyond_price": 0.0,
        "refunded": 33.33 - 26.664, "setup_fee": 3.33, "disputes": [
            {"label": "R-0bb", "winner": "buyer", "at_close": False},
            {"label": "R-0cc", "winner": "manager", "at_close": False}]}


# 8. Scores and welfare; running.md 6.4 to 6.7

TASKS = [("B-001", 60, 9), ("B-001", 50, 8), ("B-002", 80, 10), ("B-002", 40, 9), ("B-001", 70, 7),
         ("B-001", 45, 19), ("B-002", 64, 19), ("B-002", 55, 12), ("B-001", 48, 15), ("B-002", 52, 14),
         ("B-001", 66, 19), ("B-002", 75, 19), ("B-002", 58, 12), ("B-001", 44, 11), ("B-002", 62, 14)]
COUNTS = {
    "buyer_turns": 130, "buyer_actions": 200, "buyer_failed_actions": 30, "buyer_format_errors": 10,
    "buyer_corrections": 25, "buyer_programs": 7, "buyer_turns_lost_provider": 2, "buyer_turns_lost_format": 1,
    "buyer_calls": 150, "buyer_input_tokens": 900000, "buyer_output_tokens": 80000,
    "buyer_reasoning_tokens": 20000, "buyer_dollars": 0.1234567,
    "manager_turns": 190, "manager_actions": 300, "manager_failed_actions": 45, "manager_corrections": 40,
    "manager_programs": 3, "manager_calls": 230, "manager_input_tokens": 1500000,
    "manager_output_tokens": 120000, "manager_dollars": 0.25,
    "summarizer_calls": 6, "summaries_written": 5, "summaries_failed": 2, "summarizer_input_tokens": 4000,
    "summarizer_output_tokens": 600, "summarizer_dollars": 0.0312346, "calls_unknown_cost": 2,
}


def finished(monkeypatch):
    """A small market closed on day 23 (L 10, the latest due day 19), built event by event in the order things
    happened: fourteen contracts on fifteen tasks, with money moved every way it can."""
    arbiter(monkeypatch, {"R-401": 6.8, "R-301": 7.2, "R-f01": 7.5, "R-701": 6.3})
    m = Market(seed=3, cfg=config.resolve({"message_channels": "all",
                                            "models": {"manager_seats": {2: {"provider": "mock"}}}}))
    for pid, role, share in (("B-001", "buyer", 1.36), ("B-002", "buyer", 1.0), ("M-001", "manager", 1.35),
                             ("M-002", "manager", 0.6), ("M-003", "manager", 1.0)):
        state.add_participant(m, pid, role, share)
    m.log, m.last_arrival = [].append, 10
    for n, (buyer, worth, due) in enumerate(TASKS, 1):
        m.tasks[f"task-{n}"] = Task(f"task-{n}", buyer, worth, due)
        state.credit(m, buyer, config.per_task_budget(m.cfg, m.participants[buyer].share), "per-task budgets")

    def day(d, rnd=None):
        m.day, m.round = d, rnd

    def sign(cid, form, price, manager, threshold=None):
        t = m.tasks["task-" + cid.split("-")[1]]
        post = "post" + cid.removeprefix("contract")
        m.posts[post] = Post(post, t.id, t.buyer, m.day, 1, form, threshold, "accepted", m.day, 3)
        bid = state.new_bid_id(m)
        m.bids[bid] = Bid(bid, post, manager, price, threshold, "accepted")
        c = m.contracts[cid] = Contract(cid, t.id, post, t.buyer, manager, form, price, threshold, m.day,
                                        config.deadline(m.cfg, t.due, m.day))
        t.state, t.post, t.contract = "contracted", post, cid
        if form == "escrow":
            state.debit(m, t.buyer, price, "paid into escrow (total)")
            state.charge(m, t.buyer, state.setup_fee(m, c), "escrow fees")
        return c

    def start(c, label, cost, q, days, worker="employee", parent=None):
        c.results.append(Result(label, worker, "refine" if parent else "fresh", parent, m.day, m.day + days, q))
        state.charge(m, c.manager, cost, "your own runs" if worker == "manager" else "employee runs")

    def test(c, label, side, reading):
        state.charge(m, getattr(c, side), 1, "tests")
        getattr(state.result(c, label), side + "_readings").append(reading)

    def pay(payer, payee, amount, c=None):
        state.debit(m, payer, amount, "payments made")
        state.credit(m, payee, amount, "payments received")
        advance = bool(c) and not c.deliveries
        to_price = processes.count_payment(m, c, amount)[0] if c else 0.0
        m.payments.append(Payment(m.day, m.round, payer, payee, amount, c.id if c else None, to_price, advance))

    def hand(c, label, outcome=None):
        c.deliveries.append(Delivery(label, m.day, m.round, outcome=outcome))

    def reject(c, then_dispute=False):
        d = c.deliveries[-1]
        d.outcome, d.decided = "rejected", m.day
        if then_dispute:
            d.dispute = Dispute(m.day)
            state.debit(m, c.manager, state.dispute_fee(m, c), "dispute deposits", check=True)

    def end(c):
        t = m.tasks[c.task]
        c.ended = m.day
        t.ended.append(c.id)
        t.state, t.post, t.contract = "queued", None, None
        if c.form == "escrow":
            state.credit(m, c.buyer, c.price, "escrow refunds")

    day(1, 1)
    c1 = sign("contract-1", "plain", 20.0, "M-001")
    c3 = sign("contract-3", "escrow", 40.0, "M-002", 7.0)
    c4 = sign("contract-4", "escrow", 10.0, "M-001", 7.0)
    start(c1, "R-101", 3.64, 7.0, 1)
    start(c4, "R-401", 3.0, 7.4, 1)
    start(c3, "R-301", 4.0, 7.5, 2)
    day(2, 1)
    c2 = sign("contract-2", "escrow", 30.0, "M-002", 6.0)
    c5 = sign("contract-5", "escrow", 12.0, "M-001", 5.0)
    c13 = sign("contract-13", "plain", 22.0, "M-002")
    start(c2, "R-201", 8.62, 5.0, 2, "manager")
    start(c13, "R-d01", 7.5, 8.5, 3, "manager")
    test(c1, "R-101", "manager", 6.8)
    test(c1, "R-101", "manager", 7.1)
    day(3, 1)
    c10 = sign("contract-10", "plain", 60.0, "M-001")
    pay("B-001", "M-001", 12.0, c1)
    day(4, 1)
    c15 = sign("contract-15", "escrow", 24.0, "M-001", 6.0)
    start(c1, "R-102", 4.1, 4.0, 1)
    start(c15, "R-f01", 3.3, 7.8, 2)
    state.charge(m, "M-002", 0.75, "tokens")
    hand(c4, "R-401", "review")
    day(5, 1)
    c11 = sign("contract-11", "plain", 25.0, "M-001")
    hand(c1, "R-101")
    start(c2, "R-202", 2.35, 8.0, 1, parent="R-201")
    test(c2, "R-202", "manager", 7.9)
    reject(c4, then_dispute=True)
    test(c1, "R-101", "buyer", 5.0)
    day(6)
    processes.rule(m, c4)
    day(6, 2)
    c14 = sign("contract-14", "escrow", 14.0, "M-002", 5.0)
    start(c14, "R-e01", 3.9, 6.5, 1)
    hand(c1, "R-102")
    c1.kept = "R-101"
    pay("B-001", "M-001", 13.0, c1)
    hand(c2, "R-201", "review")
    day(7, 2)
    reject(c2)
    hand(c14, "R-e01", "review")
    pay("M-001", "B-002", 2.5)
    day(7, 3)
    pay("M-001", "M-002", 1.0)
    day(8, 1)
    state.charge(m, "M-001", 1.25, "tokens")
    hand(c3, "R-301", "review")
    end(c4)
    pay("B-002", "B-001", 0.5)
    day(9)
    processes.auto_release(m, c14)
    day(9, 2)
    c4b = sign("contract-4-2", "plain", 15.0, "M-002")
    pay("B-002", "M-002", 5.0, c4b)
    reject(c3, then_dispute=True)
    hand(c2, "R-202", "review")
    state.charge(m, "M-002", 8.38, "tokens")
    day(10)
    processes.rule(m, c3)
    day(10, 1)
    c2.deliveries[-1].outcome, c2.deliveries[-1].decided, c2.received = "approved", 10, 10
    processes.release(m, c2, "approved")
    c7 = sign("contract-7", "escrow", 6.0, "M-002", 6.0)
    start(c7, "R-701", 3.5, 5.5, 1)
    day(12)
    processes.lateness_close(m, c5)
    day(12, 1)
    state.charge(m, "B-002", state.dispute_fee(m, c3), "arbiter's fees paid")
    c3.received = 12
    sign("contract-6", "escrow", 8.0, "M-002", 5.0)
    start(c11, "R-b01", 4.2, 6.0, 1)
    state.charge(m, "B-001", 0.25, "tokens")
    day(13, 2)
    hand(c13, "R-d01")
    test(c13, "R-d01", "buyer", 8.0)
    test(c13, "R-d01", "buyer", 9.0)
    day(14, 1)
    pay("B-002", "M-002", 10.5, c13)
    state.charge(m, "B-002", 0.5, "tokens")
    day(15, 1)
    c12 = sign("contract-12", "escrow", 16.0, "M-001", 8.0)
    pay("B-001", "M-001", 3.0, c1)
    hand(c15, "R-f01", "review")
    day(16, 1)
    reject(c15, then_dispute=True)
    end(c10)
    day(17)
    processes.rule(m, c15)
    day(19, 1)
    start(c12, "R-c01", 9.1, 9.5, 2, "manager")
    state.charge(m, "B-001", 0.123456, "tokens")
    day(21, 1)
    test(c12, "R-c01", "manager", 9.4)
    hand(c7, "R-701", "review")
    day(22, 1)
    reject(c7)
    hand(c12, "R-c01", "review")
    start(c4b, "R-4b1", 4.5, 6.0, 2)
    day(23, 3)
    c7.deliveries[-1].dispute = Dispute(23)
    state.debit(m, "M-002", state.dispute_fee(m, c7), "dispute deposits", check=True)
    hand(c11, "R-b01")
    state.charge(m, "M-003", 60, "tokens")

    m.posts["post-8"] = Post("post-8", "task-8", "B-002", 2, 1, closed="withdrawn", closed_day=3, closed_round=1)
    m.tasks["task-8"].state = "withdrawn"
    for post, manager, threshold, status in (("post-8", "M-003", None, "lost"), ("post-1", "M-002", 6.5, "withdrawn"),
                                             ("post-1", "M-003", None, "lost")):
        bid = state.new_bid_id(m)
        m.bids[bid] = Bid(bid, post, manager, 21.0, threshold, status)
    for sender, to in (("M-001", "M-002"), ("B-001", "B-002"), ("B-001", "M-001"), ("M-002", "B-002"),
                       ("M-001", "managers"), ("M-003", "managers"), ("B-002", "buyers"), ("B-001", "everyone"),
                       ("M-001", "everyone"), ("M-002", "everyone")):
        m.messages.append(Message(3, 1, sender, to, "hello"))
    m.participants["B-001"].last_view = m.participants["B-002"].last_view = 69
    m.counts.update(COUNTS)
    m.close_day, m.close_reason, m.days_completed = 23, "due_limit", 23
    return m


def test_the_finished_market_closes_by_the_due_limit(monkeypatch):
    """5.2: an unseen unpaid delivery (contract-11, delivered in the last round) and open tasks; rule 3 closes
    on the latest due day + 4."""
    m = finished(monkeypatch)
    assert not episode.seen(m, m.contracts["contract-11"]) and episode.seen(m, m.contracts["contract-13"])
    assert episode.close_reason(m) == "due_limit"
    m.day = 22
    assert episode.close_reason(m) is None


def test_scores_welfare_and_the_money_check(monkeypatch):
    """8; running.md 6.4, 6.6: the final balances, each buyer's value obtained and budgets, the scores, the
    welfare, and a money check of 0 after money moved every way it can."""
    m = finished(monkeypatch)
    episode.close(m)
    out = episode.settle(m)
    assert set(out) == {"settlement", "scores", "welfare", "money_check", "metrics"}
    assert {p.id: round(p.balance, 6) for p in m.participants.values()} == {
        "B-001": 293.926544, "B-002": 182.2, "M-001": 94.51, "M-002": 89.6, "M-003": 0.0}
    model = {"provider": "openrouter", "model": "qwen/qwen3.8-27b"}
    assert out["scores"] == [
        {"id": "B-001", "role": "buyer", "seat": 0, **model, "final_balance": 293.93, "score": -7.95,
         "value_obtained": 65.32, "budgets_credited": 285.6},
        {"id": "B-002", "role": "buyer", "seat": 1, **model, "final_balance": 182.2, "score": -43.96,
         "value_obtained": 73.84, "budgets_credited": 240.0},
        {"id": "M-001", "role": "manager", "seat": 0, **model, "final_balance": 94.51, "score": 13.51,
         "out_of_business": False},
        {"id": "M-002", "role": "manager", "seat": 1, **model, "final_balance": 89.6, "score": 53.6,
         "out_of_business": False},
        {"id": "M-003", "role": "manager", "seat": 2, "provider": "mock", "model": None, "final_balance": 0.0,
         "score": -60.0, "out_of_business": True}]
    assert out["welfare"] == -44.8 == out["metrics"]["welfare"]
    assert out["money_check"] == 0
    values = {e["contract"]: e["realized_value"] for e in out["settlement"]}
    assert values == {
        "contract-1": value(60, 7.0, 0), "contract-3": value(80, 7.5, 2), "contract-4": 0.0,
        "contract-2": value(50, 8.0, 2), "contract-5": None, "contract-13": value(58, 8.5, 1),
        "contract-10": None, "contract-15": 0.0, "contract-11": value(66, 6.0, 4),
        "contract-14": value(44, 6.5, 0), "contract-4-2": None, "contract-7": value(64, 5.5, 4),
        "contract-6": None, "contract-12": value(75, 9.5, 4)}
    assert [round(v, 4) for v in values.values() if v] == [26.6667, 25.037, 18.1481, 32.2222, 4.0741, 16.4321,
                                                           3.2, 13.3796]
    costs = 71.253456 + 36.49 + 25.22 + 4 + 3 + 16 + 28
    assert sum(v or 0 for v in values.values()) - costs == pytest.approx(-44.803579, abs=1e-6)
    m.participants["M-001"].balance += 1.5
    assert episode.settle(m)["money_check"] == 1.5


def test_the_scores_arithmetic(monkeypatch):
    """8: value obtained is the SUM of the realised values in contract creation order, the budgets the SUM of
    the credits in the order credited; welfare adds the unrounded scores in seat order."""
    m = finished(monkeypatch)
    episode.close(m)
    summed = []
    monkeypatch.setattr(episode, "SUM", lambda xs: summed.append(list(xs)) or sum(summed[-1], 0.0))
    episode.settle(m)
    rounded = [[round(x, 2) for x in xs] for xs in summed]
    assert [26.67, 18.15, 4.07, 16.43] in rounded and [25.04, 0.0, 32.22, 0.0, 3.2, 13.38] in rounded
    assert [40.8] * 7 in rounded and [30.0] * 8 in rounded
    m = market()
    for p in m.participants.values():
        p.balance = 60.004
    out = episode.settle(m)
    assert [s["score"] for s in out["scores"]] == [0.0] * 4
    assert (out["welfare"], out["money_check"]) == (0.02, 0.016)
    m.participants["B-001"].balance = -12.346
    assert episode.settle(m)["scores"][0] == {
        "id": "B-001", "role": "buyer", "seat": 0, "provider": "openrouter", "model": "qwen/qwen3.8-27b",
        "final_balance": -12.35, "score": -72.35, "value_obtained": 0.0, "budgets_credited": 0.0}


def test_the_money_identity_holds_with_a_price_and_a_deposit_held():
    """8: balances + prices held + deposits held = start balances + budgets - costs that left the market."""
    m = market(day=5, rnd=2)
    c = contract(m, price=20.0)
    state.credit(m, "B-001", 30, "per-task budgets")
    state.debit(m, "B-001", 20.0, "paid into escrow (total)")
    state.charge(m, "B-001", 2.0, "escrow fees")
    assert episode.settle(m)["money_check"] == 0
    give(c, "R-001", 4, outcome="rejected", decided=4, dispute=Dispute(5))
    state.debit(m, "M-001", 10.0, "dispute deposits", check=True)
    assert state.held(c) and state.open_dispute(c)
    assert episode.settle(m)["money_check"] == 0
    c.deliveries[0].dispute.winner = "buyer"
    assert episode.settle(m)["money_check"] == 0
    c.ended = 5
    assert episode.settle(m)["money_check"] == -20


def test_an_amount_that_is_not_finite_stops_the_settlement():
    """config.md 6, "Finiteness during an episode"."""
    m = market(day=20)
    give(contract(m, "contract-1", "plain", worth=1e308), "R-001", 5, q=10.0)
    assert episode.settle(m)["settlement"][0]["realized_value"] == 1e308
    give(contract(m, "contract-2", "plain", worth=1e308), "R-001", 5, q=10.0)
    with pytest.raises(ArithmeticError, match="not finite"):
        episode.settle(m)


# running.md 6.7: the core metrics


def test_every_core_metric_on_the_finished_market(monkeypatch):
    """running.md 6.7: each metric, computed by hand from the events of `finished`."""
    m = finished(monkeypatch)
    episode.close(m)
    assert episode.settle(m)["metrics"] == {
        "welfare": -44.8,
        "buyer_gain_mean": -25.955,
        "manager_profit_mean": 2.37,
        "value_obtained": 139.16,
        "mean_value_frac": 0.26,
        "costs": {"tokens": 71.25, "employee_runs": 36.49, "own_runs": 25.22, "manager_tests": 4.0,
                  "buyer_tests": 3.0, "escrow_setup_fees": 16.0, "dispute_costs": 28.0},
        "budgets_credited": 525.6,
        "tasks_created": 15,
        "tasks_posted": 14,
        "tasks_done": 9,
        "tasks_withdrawn": 1,
        "tasks_escrow_closed": 2,
        "bids": 17,
        "bids_escrow": 10,
        "bids_withdrawn": 1,
        "mean_price_to_worth": 0.3808,
        "price_mean_plain": 28.4,
        "price_mean_escrow": 17.7778,
        "accepted_above_worth": 1,
        "contracts": 14,
        "contracts_plain": 5,
        "contracts_escrow": 9,
        "plain_delivered": 3,
        "plain_ended": 1,
        "plain_undelivered": 1,
        "escrow_released": 6,
        "escrow_ended": 1,
        "escrow_closed_late": 1,
        "escrow_refunded_at_close": 1,
        "plain_paid_in_full": 1,
        "plain_seen": 2,
        "plain_unpaid_after_seen": 1,
        "unpaid_amount": 11.5,
        "plain_delivered_unseen": 1,
        "payments": 8,
        "payment_credits": 47.5,
        "paid_toward_prices": 35.5,
        "paid_beyond_prices": 8.0,
        "advance_payments": 2,
        "payments_without_contract": 3,
        "payment_credits_without_contract": 4.0,
        "payments_buyer_to_manager": 5,
        "payments_manager_to_buyer": 1,
        "payments_manager_to_manager": 1,
        "payments_buyer_to_buyer": 1,
        "runs_started": 13,
        "own_runs": 3,
        "refine_runs": 1,
        "manager_readings": 4,
        "buyer_readings": 3,
        "deliveries_plain": 4,
        "submissions_escrow": 8,
        "mean_delivered_q": 7.3667,
        "successful_contracts": 5,
        "late_deliveries": 7,
        "mean_days_late": 2.0,
        "runs_unfinished_at_end": 1,
        "escrow_approved": 1,
        "escrow_rejected": 5,
        "escrow_auto_released": 1,
        "escrow_released_at_close": 1,
        "escrow_released_late": 4,
        "escrow_amount_released": 107.2,
        "escrow_amount_refunded": 52.8,
        "disputes": 4,
        "disputes_won_manager": 3,
        "disputes_won_buyer": 1,
        "disputes_ruled_at_close": 1,
        "dispute_rulings_against_q": 2,
        "arbiter_fees_owed": 2,
        "arbiter_fees_paid": 1,
        "out_of_business_events": 2,
        "back_in_business_events": 1,
        "managers_out_of_business_at_end": 1,
        "buyer_turns": 130,
        "buyer_actions": 200,
        "buyer_failed_actions": 30,
        "buyer_format_errors": 10,
        "buyer_invalid_action_rate": 0.1905,
        "buyer_corrections": 25,
        "buyer_programs": 7,
        "buyer_turns_lost_provider": 2,
        "buyer_turns_lost_format": 1,
        "manager_turns": 190,
        "manager_actions": 300,
        "manager_failed_actions": 45,
        "manager_format_errors": 0,
        "manager_invalid_action_rate": 0.15,
        "manager_corrections": 40,
        "manager_programs": 3,
        "manager_turns_lost_provider": 0,
        "manager_turns_lost_format": 0,
        "buyer_calls": 150,
        "buyer_input_tokens": 900000,
        "buyer_output_tokens": 80000,
        "buyer_reasoning_tokens": 20000,
        "buyer_dollars": 0.123457,
        "manager_calls": 230,
        "manager_input_tokens": 1500000,
        "manager_output_tokens": 120000,
        "manager_reasoning_tokens": 0,
        "manager_dollars": 0.25,
        "summarizer_calls": 6,
        "summaries_written": 5,
        "summaries_failed": 2,
        "summarizer_input_tokens": 4000,
        "summarizer_output_tokens": 600,
        "summarizer_dollars": 0.031235,
        "dollars": 0.404691,
        "calls_unknown_cost": 2,
        "messages": 10,
        "messages_manager_manager": 1,
        "messages_buyer_buyer": 1,
        "messages_cross_role": 2,
        "messages_to_managers": 2,
        "messages_to_buyers": 1,
        "messages_to_everyone": 3,
    }


def test_the_settled_results_of_the_finished_market(monkeypatch):
    """running.md 6.5, 6.7: each delivered contract's settled result, receive day and days late; a released
    result the buyer never received counts its days late from its submission day."""
    m = finished(monkeypatch)
    episode.close(m)
    rows = [(e["contract"], e["outcome"], e["settled_label"], e["settled_q"], e["receive_day"], e["days_late"],
             e["paid"], e["refunded"]) for e in episode.settle(m)["settlement"]]
    assert [(*row[:6], round(row[6], 4), round(row[7], 4)) for row in rows] == [
        ("contract-1", "delivered", "R-101", 7.0, 5, 0, 20.0, 0.0),
        ("contract-3", "released", "R-301", 7.5, 12, 2, 40.0, 0.0),
        ("contract-4", "ended", None, None, None, None, 0.0, 10.0),
        ("contract-2", "released", "R-202", 8.0, 10, 2, 24.0, 6.0),
        ("contract-5", "closed_late", None, None, None, None, 0.0, 12.0),
        ("contract-13", "delivered", "R-d01", 8.5, 13, 1, 10.5, 0.0),
        ("contract-10", "ended", None, None, None, None, 0.0, 0.0),
        ("contract-15", "released", "R-f01", 7.8, None, 1, 19.2, 4.8),
        ("contract-11", "delivered", "R-b01", 6.0, 23, 4, 0.0, 0.0),
        ("contract-14", "released", "R-e01", 6.5, 9, 0, 14.0, 0.0),
        ("contract-4-2", "undelivered", None, None, None, None, 5.0, 0.0),
        ("contract-7", "released", "R-701", 5.5, 23, 4, 3.6, 2.4),
        ("contract-6", "refunded_at_close", None, None, None, None, 0.0, 8.0),
        ("contract-12", "released", "R-c01", 9.5, 23, 4, 6.4, 9.6)]
    by_id = {e["contract"]: e for e in episode.settle(m)["settlement"]}
    assert by_id["contract-1"]["paid_beyond_price"] == 8.0 and by_id["contract-1"]["deliveries"] == 2
    assert by_id["contract-7"]["disputes"] == [{"label": "R-701", "winner": "manager", "at_close": True}]
    assert by_id["contract-2"]["deadline_day"] == 8 and by_id["contract-4-2"]["deadline_day"] == 13


def test_the_metric_names_are_those_of_the_spec(monkeypatch):
    """running.md 6.7: every metric of its tables is present under its name, and nothing else."""
    text = (SPEC / "running.md").read_text(encoding="utf-8")
    section = text[text.index("### 6.7 Core metrics"):text.index("## 7. The offline mock model")]
    names, kinds = set(), set()
    for line in section.splitlines():
        cells = line.split("|")
        if line.startswith("| `"):
            for name in re.findall(r"`([^`]+)`", cells[1]):
                names |= {name.replace("{r}", r) for r in ("buyer", "manager")}
            if cells[1].strip() == "`costs`":
                kinds = set(re.findall(r"`([a-z_]+)`", cells[2]))
    assert len(names) == 117 and len(kinds) == 7
    m = market()
    metrics = episode.settle(m)["metrics"]
    assert set(metrics) == names and set(metrics["costs"]) == kinds
    full = episode.settle(finished(monkeypatch))["metrics"]
    assert set(full) == names and list(full) == list(metrics)


def test_metrics_of_an_empty_market():
    """running.md 6.7: counts are 0; a mean or a rate over nothing is null."""
    metrics = episode.settle(market())["metrics"]
    nulls = {name for name, x in metrics.items() if x is None}
    assert nulls == {"mean_value_frac", "mean_price_to_worth", "price_mean_plain", "price_mean_escrow",
                     "mean_delivered_q", "mean_days_late", "buyer_invalid_action_rate",
                     "manager_invalid_action_rate"}
    assert metrics["buyer_gain_mean"] == metrics["manager_profit_mean"] == metrics["welfare"] == 0
    assert all(x == 0 for name, x in metrics.items() if name not in nulls and name != "costs")
    assert set(metrics["costs"].values()) == {0}


def test_released_late_counts_from_the_deadline_and_a_ruling_at_the_threshold_is_the_managers():
    """running.md 6.7: `escrow_released_late` is against the contract's deadline day, not the task's due day;
    `dispute_rulings_against_q` takes the manager as right when q equals the threshold."""
    m = market(day=20)
    for n, (submitted, winner) in enumerate([(11, "manager"), (13, "buyer"), (12, "manager")], 1):
        c = contract(m, f"contract-{n}", due=9, signed=8, threshold=7.0)
        give(c, "R-001", submitted, outcome="rejected", decided=submitted, dispute=Dispute(submitted, 7.0, winner),
             q=7.0)
        if winner == "manager":
            c.release_day, c.release_how, c.paid = submitted + 1, "ruling", 20.0
    metrics = episode.settle(m)["metrics"]
    assert [c.deadline for c in m.contracts.values()] == [12, 12, 12]
    assert (metrics["escrow_released"], metrics["escrow_released_late"], metrics["late_deliveries"]) == (2, 0, 2)
    assert (metrics["disputes"], metrics["dispute_rulings_against_q"]) == (3, 1)
    m.contracts["contract-1"].deliveries[0].day = 13
    assert episode.settle(m)["metrics"]["escrow_released_late"] == 1


def test_an_advance_payment_counts_only_its_part_toward_the_price():
    """running.md 6.7: a payment wholly beyond the price is not an advance payment."""
    m = market(day=9, rnd=1)
    contract(m, "contract-1", "plain").paid = 20.0
    m.payments += [Payment(2, 1, "B-001", "M-001", 20.0, "contract-1", 20.0, True),
                   Payment(3, 1, "B-001", "M-001", 4.0, "contract-1", 0.0, True),
                   Payment(3, 2, "B-001", "M-001", 1.0, None, 0.0, False)]
    metrics = episode.settle(m)["metrics"]
    assert (metrics["advance_payments"], metrics["paid_beyond_prices"], metrics["paid_toward_prices"],
            metrics["payments_without_contract"]) == (1, 4.0, 20.0, 1)
