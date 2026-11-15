"""Blind specification tests. No filesystem access or generated fixtures."""

import copy
import math
from statistics import NormalDist

import pytest

from market import config, processes as p, rng, state


BUYER = "B-c53"
MANAGER = "M-14b"


def market(overrides=None, *, day=9, form="escrow", price=20.0,
           due=9, deadline=9, signed=1, cid="contract-1"):
    m = state.Market(seed=37, cfg=config.resolve(overrides or {}))
    m.day = day
    state.add_participant(m, BUYER, "buyer", 1.0)
    state.add_participant(m, MANAGER, "manager", 1.0)
    task = state.Task("task-1", BUYER, 80, due, state="contracted",
                      post="post-1", contract=cid)
    c = state.Contract(
        cid, task.id, "post-1", BUYER, MANAGER, form, price,
        6 if form == "escrow" else None, signed, deadline,
    )
    m.tasks[task.id] = task
    m.contracts[c.id] = c
    records = []
    m.log = records.append
    return m, c, records


def result(c, label="R-b8b", *, q=7.0219, worker="employee",
           kind="fresh", parent=None, start=2, arrival=3):
    r = state.Result(label, worker, kind, parent, start, arrival, q)
    c.results.append(r)
    return r


def submission(c, *, day=9, outcome="review", decided=None,
               disputed=None, label="R-b8b", q=7.0219):
    r = result(c, label, q=q)
    d = state.Delivery(label, day, 1, outcome=outcome, decided=decided)
    if disputed is not None:
        d.dispute = state.Dispute(disputed)
    c.deliveries.append(d)
    return r, d


def notifications(records):
    return [(r["to"], r["text"]) for r in records
            if r["type"] == "notification"]


def balances(m):
    return {pid: person.balance for pid, person in m.participants.items()}


def snapshot(m):
    return copy.deepcopy({k: v for k, v in vars(m).items() if k != "log"})


def fixed_normals(monkeypatch, values):
    values = iter(values)
    monkeypatch.setattr(rng, "normal", lambda *a, **k: next(values))


def fixed_arbiter(monkeypatch, reading):
    calls = []

    def draw(seed, stream, label, q, sd, decimals):
        calls.append((seed, stream, label, q, sd, decimals))
        return {"z": 0.0, "reading": reading}

    monkeypatch.setattr(rng, "arbiter", draw)
    return calls


def tn_from_spec(mean, sd, z):
    """RND 2's inverse-CDF construction, independent of market code."""
    phi = lambda x: math.erfc(-x / math.sqrt(2)) / 2
    a = phi(-mean / sd)
    b = phi((10 - mean) / sd)
    probability = min(max(a + phi(z) * (b - a), 1e-15), 1 - 1e-15)
    return min(10.0, max(0.0, mean + sd * NormalDist().inv_cdf(probability)))


@pytest.mark.parametrize("offset,text", [
    (-100, "100 days ago"), (-2, "2 days ago"), (-1, "1 day ago"),
    (0, "today"), (1, "1 day left"), (2, "2 days left"),
    (100, "100 days left"),
])
def test_relative_day(offset, text):
    # P 3; E 3.2; AB 1.13.
    m, _, _ = market(day=10**30)
    assert p.rel(m, m.day + offset) == text


@pytest.mark.parametrize("worker,kind,parent,text", [
    ("employee", "fresh", None, "your employee, fresh"),
    ("employee", "refine", "R-dbe", "your employee, refine of R-dbe"),
    ("manager", "fresh", None, "you, fresh"),
    ("manager", "refine", "R-dbe", "you, refine of R-dbe"),
])
def test_origin(worker, kind, parent, text):
    # P 1.4.
    r = state.Result("R-001", worker, kind, parent, 1, 2, 5.0)
    assert p.origin(r) == text


@pytest.mark.parametrize("worker,kind,k", [
    ("employee", "fresh", 2),
    ("employee", "refine", 1),
    ("manager", "fresh", 1),
    ("manager", "refine", 0),
])
@pytest.mark.parametrize("cid,stream", [
    ("contract-1", "task-1"), ("contract-1-3", "task-1-3"),
])
def test_draw_run_key_counts_and_purity(monkeypatch, worker, kind, k,
                                        cid, stream):
    # P 1.2; RND 3.8: counters are per contract, worker and kind.
    m, c, _ = market(cid=cid)
    parent = result(c, "R-001", q=5.7648123456789)
    result(c, "R-002", arrival=30)
    result(c, "R-003", kind="refine", parent=parent.label)
    result(c, "R-004", worker="manager")
    other = copy.deepcopy(c)
    other.id = "contract-99"
    m.contracts[other.id] = other
    calls = []
    drawn = {"z_q": 0.1, "z_t": -0.2, "z_c": 0.3,
             "q": 6.123456789, "days": 2, "cost": 4.12}

    def draw(seed, key, w, kind_, count, production, q_parent=None):
        calls.append((seed, key, w, kind_, count, production, q_parent))
        return dict(drawn)

    monkeypatch.setattr(rng, "run", draw)
    before = snapshot(m)
    named_parent = parent.label if kind == "refine" else None
    assert p.draw_run(m, c, worker, kind, named_parent) == drawn
    assert p.draw_run(m, c, worker, kind, named_parent) == drawn
    expected = (37, stream, worker, kind, k, m.cfg["production"],
                parent.q if kind == "refine" else None)
    assert calls == [expected, expected]
    assert snapshot(m) == before


@pytest.mark.parametrize("worker,kind,parent_q,zs,q,days,cost", [
    ("employee", "fresh", None, (-0.0379, 1.0489, -0.3925),
     5.7649, 2, 3.64),
    ("manager", "fresh", None, (1.8723, -1.0971, 0.4217),
     9.1499, 2, 8.62),
    ("employee", "refine", 5.7648, (0.4241, -3.1075, -1.0166),
     7.9737, 1, 2.35),
    ("manager", "refine", 3.9116, (-0.8212, 0.9382, -0.1958),
     7.5100, 2, 12.92),
])
def test_run_worked_examples(monkeypatch, worker, kind, parent_q,
                             zs, q, days, cost):
    # P 1.2; RND 3.8: displayed variates and qualities are approximate.
    m, c, _ = market()
    parent = result(c, q=parent_q) if parent_q is not None else None
    fixed_normals(monkeypatch, zs)
    draw = p.draw_run(m, c, worker, kind,
                      parent.label if parent else None)
    assert draw["q"] == pytest.approx(q, abs=0.0002)
    assert draw["days"] == days
    assert draw["cost"] == cost
    assert (draw["z_q"], draw["z_t"], draw["z_c"]) == zs


@pytest.mark.parametrize("worker,kind,expected", [
    ("employee", "fresh", 4.0), ("employee", "refine", 2.8),
    ("manager", "fresh", 8.0), ("manager", "refine", 14.0),
])
def test_default_expected_costs_at_multiplier_one(monkeypatch, worker,
                                                  kind, expected):
    # P 1.2; C 6.5: z_c = sig_c / 2 makes the multiplier exactly one.
    m, c, _ = market()
    parent = result(c, q=5.0)
    fixed_normals(monkeypatch, (0.0, 0.0, 0.125))
    draw = p.draw_run(m, c, worker, kind,
                      parent.label if kind == "refine" else None)
    assert draw["cost"] == expected


@pytest.mark.parametrize("worker,kind,cost", [
    ("employee", "fresh", 7.0), ("employee", "refine", 4.6),
    ("manager", "fresh", 16.0), ("manager", "refine", 28.0),
])
def test_cost_multiplier_and_reference_worth(monkeypatch, worker, kind, cost):
    # P 1.2; RND 3.8: only the variable part of employee cost is scaled.
    m, c, _ = market()
    m.tasks[c.task].worth = 43
    parent = result(c, q=4.0)
    z_c = (math.log(2) + 0.25**2 / 2) / 0.25
    fixed_normals(monkeypatch, (0.0, 0.0, z_c))
    draw = p.draw_run(m, c, worker, kind,
                      parent.label if kind == "refine" else None)
    assert draw["cost"] == cost


@pytest.mark.parametrize("worker,kind,parent_q,mean,sd", [
    ("employee", "fresh", None, 6.0, 2.5),
    ("manager", "fresh", None, 7.0, 1.2),
    ("employee", "refine", 0.0, 4.0, 1.5),
    ("employee", "refine", 10.0, 10.0, 1.5),
    ("manager", "refine", 0.0, 7.0, 0.8),
    ("manager", "refine", 10.0, 10.0, 0.8),
    ("manager", "refine", 3.911612345, 8.1734837035, 0.8),
])
@pytest.mark.parametrize("z", [-2.0, 0.0, 2.0])
def test_quality_is_unrounded_truncated_normal(monkeypatch, worker, kind,
                                                parent_q, mean, sd, z):
    # P 1.1-1.2; RND 2, 3.8: inverse-CDF truncation, not clipping a normal.
    m, c, _ = market()
    parent = result(c, q=parent_q) if parent_q is not None else None
    fixed_normals(monkeypatch, (z, 0.0, 0.125))
    draw = p.draw_run(m, c, worker, kind,
                      parent.label if parent else None)
    expected = tn_from_spec(mean, sd, z)
    assert draw["q"] == pytest.approx(expected, abs=2e-10)
    assert 0 <= draw["q"] <= 10
    assert abs(draw["q"] - round(draw["q"], 1)) > 1e-8


@pytest.mark.parametrize("worker,kind,z_t,days", [
    ("employee", "fresh", -7.0, 1),
    ("employee", "fresh", 0.0, 1),
    ("employee", "fresh", 1.0, 2),
    ("employee", "refine", 0.0, 1),
    ("manager", "fresh", 0.0, 3),
    ("manager", "fresh", 7.03449, 30),
    ("manager", "refine", 0.0, 1),
])
def test_duration_ceiling_and_minimum(monkeypatch, worker, kind, z_t, days):
    # P 1.2; RND 2, 3.8.
    m, c, _ = market()
    parent = result(c)
    fixed_normals(monkeypatch, (0.0, z_t, 0.125))
    draw = p.draw_run(m, c, worker, kind,
                      parent.label if kind == "refine" else None)
    assert draw["days"] == days
    assert isinstance(draw["days"], int)


def test_zero_duration_noise(monkeypatch):
    # P 1.2; C 6.7: zero log-sd makes duration deterministic.
    m, c, _ = market({"production": {"sig_t": 0}})
    fixed_normals(monkeypatch, (0.0, -7.0, 0.125))
    assert p.draw_run(m, c, "manager", "fresh")["days"] == 3


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_run_cost_stops_before_mutation(monkeypatch, bad):
    # C 6, finiteness during an episode.
    m, c, records = market()
    monkeypatch.setattr(rng, "run", lambda *a, **k: {
        "z_q": 0.0, "z_t": 0.0, "z_c": 0.0,
        "q": 5.0, "days": 1, "cost": bad,
    })
    before = snapshot(m)
    with pytest.raises(ArithmeticError) as error:
        p.draw_run(m, c, "employee", "fresh")
    assert "cost" in str(error.value).lower()
    assert snapshot(m) == before
    assert records == []


@pytest.mark.parametrize("worker,kind", [
    ("employee", "fresh"), ("employee", "refine"),
    ("manager", "fresh"), ("manager", "refine"),
])
def test_start_records_result_without_charging_or_notifying(monkeypatch,
                                                           worker, kind):
    # P 1.1, 1.3; AM 4 effects; RND 3.9.
    m, c, records = market(day=12, cid="contract-1-2")
    parent = result(c, "R-001")
    c.label_draws = 7
    calls = []

    def label(seed, stream, n, used):
        calls.append((seed, stream, n, set(used)))
        return {"label": "R-abc", "n": 10}

    monkeypatch.setattr(rng, "label", label)
    drawn = {"z_q": 0.25, "z_t": -0.5, "z_c": 1.0,
             "q": 7.1234567890123, "days": 3, "cost": 5.67}
    before_balances = balances(m)
    r = p.start_run(m, c, worker, kind,
                    parent.label if kind == "refine" else None, drawn)
    assert calls == [(37, "task-1-2", 7, {"R-001"})]
    assert c.label_draws == 10
    assert c.results == [parent, r]
    assert r == state.Result(
        "R-abc", worker, kind,
        parent.label if kind == "refine" else None,
        12, 15, 7.1234567890123,
    )
    assert balances(m) == before_balances
    assert all(not x.ledger and not x.pending for x in m.participants.values())
    assert notifications(records) == []


def test_label_collisions_and_hex_endpoints(monkeypatch):
    # P 1.1; RND 3.9: discarded labels consume n, including in-flight labels.
    m, c, _ = market(cid="contract-1-2")
    result(c, "R-000", arrival=30)
    c.label_draws = 4
    keys = []
    integers = iter([0, 0, 4095, 10])

    def uniforms(seed, *parts):
        keys.append((seed, *parts))
        return iter([0.5])

    def integer(us, low, high):
        assert (low, high) == (0, 4095)
        next(us)
        return next(integers)

    monkeypatch.setattr(rng, "uniforms", uniforms)
    monkeypatch.setattr(rng, "integer", integer)
    draw = {"z_q": 0.0, "z_t": 0.0, "z_c": 0.0,
            "q": 6.0, "days": 1, "cost": 4.0}
    first = p.start_run(m, c, "employee", "fresh", None, draw)
    second = p.start_run(m, c, "manager", "fresh", None, draw)
    assert first.label == "R-fff"
    assert second.label == "R-00a"
    assert c.label_draws == 8
    assert keys == [
        (37, "task-1-2", "label", 4),
        (37, "task-1-2", "label", 5),
        (37, "task-1-2", "label", 6),
        (37, "task-1-2", "label", 7),
    ]


def test_labels_are_unique_only_within_contract(monkeypatch):
    # P 1.1; RND 3.9.
    m, c, _ = market()
    other = copy.deepcopy(c)
    other.id = "contract-2"
    result(other, "R-baf")
    m.contracts[other.id] = other

    def label(seed, stream, n, used):
        assert "R-baf" not in used
        return {"label": "R-baf", "n": n + 1}

    monkeypatch.setattr(rng, "label", label)
    draw = {"z_q": 0.0, "z_t": 0.0, "z_c": 0.0,
            "q": 6.0, "days": 1, "cost": 4.0}
    assert p.start_run(m, c, "employee", "fresh", None, draw).label == "R-baf"


@pytest.mark.parametrize("status", ["active", "ended", "released", "closed"])
def test_arrivals_order_and_exact_text_in_all_contract_states(status):
    # P 1.4, 9; E 3.3a: arrivals survive termination and settlement.
    m, c, records = market(day=4)
    if status == "ended":
        c.ended = 3
    elif status == "released":
        c.release_day = 3
        c.release_how = "approved"
    elif status == "closed":
        c.closed = "late"
    m.participants[MANAGER].out_of_business = True
    m.participants[MANAGER].balance = -1.0
    result(c, "R-dbe", arrival=3)
    result(c, "R-5b6", kind="refine", parent="R-dbe", arrival=4)
    result(c, "R-713", worker="manager", arrival=4)
    result(c, "R-714", arrival=5)
    before = balances(m)
    p.arrivals(m, c)
    gone = (" B-c53 ended contract-1 on day 3, so it cannot be delivered."
            if status == "ended" else "")
    assert notifications(records) == [
        (MANAGER, "[day 4] R-5b6 (your employee, refine of R-dbe) "
         "for contract-1 has arrived." + gone),
        (MANAGER, "[day 4] R-713 (you, fresh) "
         "for contract-1 has arrived." + gone),
    ]
    assert balances(m) == before
    assert m.participants[MANAGER].out_of_business
    assert m.participants[BUYER].pending == []


@pytest.mark.parametrize("side,sd", [("manager", 0.5), ("buyer", 2.0)])
def test_read_uses_unrounded_quality_and_own_counter(monkeypatch, side, sd):
    # P 2.1-2.2, 9; RND 3.10.
    m, c, records = market(cid="contract-1-2")
    r = result(c, q=9.149812345)
    r.manager_readings[:] = [1.0, 2.0]
    r.buyer_readings[:] = [3.0]
    calls = []

    def reading(seed, stream, who, label, i, q, noise, decimals):
        calls.append((seed, stream, who, label, i, q, noise, decimals))
        return {"z": -0.78218, "reading": 8.8}

    monkeypatch.setattr(rng, "reading", reading)
    before = balances(m)
    assert p.read(m, c, r, side) == 8.8
    assert p.read(m, c, r, side) == 8.8
    initial = 2 if side == "manager" else 1
    assert calls == [
        (37, "task-1-2", side, "R-b8b", initial, 9.149812345, sd, 1),
        (37, "task-1-2", side, "R-b8b", initial + 1, 9.149812345, sd, 1),
    ]
    assert r.manager_readings == ([1.0, 2.0, 8.8, 8.8]
                                  if side == "manager" else [1.0, 2.0])
    assert r.buyer_readings == ([3.0, 8.8, 8.8]
                                if side == "buyer" else [3.0])
    assert balances(m) == before
    assert notifications(records) == []


@pytest.mark.parametrize("side,q,z,decimals,expected", [
    ("manager", 9.1498, -0.78218, 1, 8.8),
    ("manager", 0.1, -7.0, 1, 0.0),
    ("buyer", 9.9, 7.0, 1, 10.0),
    ("manager", 2.5, 0.0, 0, 2.0),
    ("manager", 3.5, 0.0, 0, 4.0),
    ("buyer", 1.25, 0.0, 1, 1.2),
    ("buyer", 1.75, 0.0, 1, 1.8),
    ("manager", 1.125, 0.0, 2, 1.12),
    ("manager", 1.375, 0.0, 2, 1.38),
    ("buyer", 1.0625, 0.0, 3, 1.062),
    ("buyer", 1.1875, 0.0, 3, 1.188),
])
def test_read_clips_then_rounds(monkeypatch, side, q, z, decimals, expected):
    # P 2.1; RND 3.10; README rounding.
    m, c, _ = market({"tests": {"decimals": decimals}})
    r = result(c, q=q)
    fixed_normals(monkeypatch, [z])
    assert p.read(m, c, r, side) == expected


@pytest.mark.parametrize("side,values,text", [
    ("manager", [], "readings: none"),
    ("buyer", [], "readings: none"),
    ("manager", [6.0],
     "readings [6.0] mean 6.00 (n=1, ±0.50 standard error)"),
    ("buyer", [5.2],
     "readings [5.2] mean 5.20 (n=1, ±2.00 standard error)"),
    ("manager", [2.3, 1.9, 2.3, 1.8, 2.6, 2.4],
     "readings [..., 1.9, 2.3, 1.8, 2.6, 2.4] mean 2.22 "
     "(n=6, ±0.20 standard error)"),
    ("manager", [1.0, 2.6, 6.5, 1.2],
     "readings [1.0, 2.6, 6.5, 1.2] mean 2.83 "
     "(n=4, ±0.25 standard error)"),
    ("manager", [5.1, 9.0, 2.6, 0.4],
     "readings [5.1, 9.0, 2.6, 0.4] mean 4.28 "
     "(n=4, ±0.25 standard error)"),
    ("buyer", [5.0, 5.0, 5.0, 5.0],
     "readings [5.0, 5.0, 5.0, 5.0] mean 5.00 "
     "(n=4, ±1.00 standard error)"),
    ("manager", [1.0, 2.0, 3.0, 4.0, 5.0],
     "readings [1.0, 2.0, 3.0, 4.0, 5.0] mean 3.00 "
     "(n=5, ±0.22 standard error)"),
])
def test_readings_exact_examples_and_compensated_mean(side, values, text):
    # P 2.3; README SUM and Fn.
    m, _, _ = market()
    before = list(values)
    assert p.readings_text(m, values, side) == text
    assert values == before


@pytest.mark.parametrize("decimals,last", [
    (0, "2"), (1, "2.0"), (2, "2.00"), (3, "2.000"),
])
def test_readings_configured_limit_precision_and_noise(decimals, last):
    # P 2.3: all readings determine mean and n; configured sd determines SE.
    m, _, _ = market({
        "tests": {"decimals": decimals, "manager_sd": 1.5, "buyer_sd": 3.0},
        "view": {"readings_shown": 1},
    })
    assert p.readings_text(m, [10.0, 2.0], "manager") == (
        "readings [..., " + last + "] mean 6.00 "
        "(n=2, ±1.06 standard error)"
    )
    assert p.readings_text(m, [10.0, 2.0], "buyer") == (
        "readings [..., " + last + "] mean 6.00 "
        "(n=2, ±2.12 standard error)"
    )


@pytest.mark.parametrize("form", ["plain", "escrow"])
@pytest.mark.parametrize("day,deadline,expected", [
    (9, 9, [
        (BUYER, "[day 9] contract-1 is due today (deadline day 9)."),
        (MANAGER, "[day 9] contract-1 is due today (deadline day 9)."),
    ]),
    (9, 12, [
        (BUYER, "[day 9] The task of contract-1 is due today (day 9): "
         "its value to you falls for each day after it. "
         "The contract's deadline day is 12."),
    ]),
    (12, 12, [
        (BUYER, "[day 12] contract-1 is due today (deadline day 12)."),
        (MANAGER, "[day 12] contract-1 is due today (deadline day 12)."),
    ]),
    (8, 9, []),
    (10, 9, []),
])
def test_due_notices_and_precedence(form, day, deadline, expected):
    # P 3.3, 9; E 3.3b.
    m, c, records = market(day=day, form=form, deadline=deadline)
    p.due_notice(m, c)
    assert notifications(records) == expected


@pytest.mark.parametrize("status", [
    "ended_plain", "ended_escrow", "released", "closed",
    "delivered_plain", "paid_plain", "review", "dispute", "rejected",
])
def test_due_notice_state_filters(status):
    # P 3.3: a plain payment is not a delivery; escrow submissions do not suppress.
    plain = status in {"ended_plain", "delivered_plain", "paid_plain"}
    m, c, records = market(form="plain" if plain else "escrow")
    if status.startswith("ended"):
        c.ended = 8
    elif status == "released":
        c.release_day = 8
    elif status == "closed":
        c.closed = "late"
    elif status == "delivered_plain":
        submission(c, day=8, outcome=None)
    elif status == "paid_plain":
        c.paid = 20
    elif status == "review":
        submission(c, day=8)
    elif status in {"dispute", "rejected"}:
        submission(c, day=8, outcome="rejected", decided=8,
                   disputed=8 if status == "dispute" else None)
    p.due_notice(m, c)
    expected = [] if status in {
        "ended_plain", "ended_escrow", "released", "closed", "delivered_plain"
    } else [
        (BUYER, "[day 9] contract-1 is due today (deadline day 9)."),
        (MANAGER, "[day 9] contract-1 is due today (deadline day 9)."),
    ]
    assert notifications(records) == expected


@pytest.mark.parametrize("q,expected", [
    (0, 0.0), (0.5, 0.0), (1, 0.0), (5.5, 0.25), (10, 1.0),
])
def test_default_quality_value_fraction(q, expected):
    # P 4: clip before squaring.
    m, _, _ = market()
    assert p.f(m, q) == expected


@pytest.mark.parametrize("q,expected", [
    (0, 0.0), (2, 0.0), (4, 0.5), (10, 1.0),
])
def test_configured_fractional_exponent(q, expected):
    # P 4; C 3.9: threshold and positive exponent are configurable.
    m, _, _ = market({"value": {"q0": 2, "exponent": 0.5}})
    assert p.f(m, q) == expected


@pytest.mark.parametrize("day,expected", [
    (1, 20.0), (9, 20.0), (10, 16.0), (11, 12.0),
    (12, 8.0), (13, 4.0), (14, 0.0), (100, 0.0),
])
@pytest.mark.parametrize("form", ["plain", "escrow"])
def test_value_uses_task_due_and_receipt_day(form, day, expected):
    # P 3.2, 4; E 7: deadline and payment do not determine value.
    m, c, _ = market(day=100, form=form, deadline=13, price=999)
    c.paid = 999
    assert p.value(m, c, 5.5, day) == pytest.approx(expected, abs=1e-12)


def test_value_is_not_rounded_to_money_precision():
    # P 4; E 7: realised value is unrounded.
    m, c, _ = market()
    m.tasks[c.task].worth = 1
    assert p.value(m, c, 2.0, 9) == pytest.approx(1 / 81, abs=1e-16)
    assert p.value(m, c, 2.0, 9) != 0.01


@pytest.mark.parametrize("paid,amount,to_price,extra,after", [
    (0.0, 4.0, 4.0, 0.0, 4.0),
    (14.0, 11.0, 6.0, 5.0, 20.0),
    (20.0, 3.25, 0.0, 3.25, 20.0),
    (19.99, 0.02, 0.01, 0.01, 20.0),
    (0.0, 0.0, 0.0, 0.0, 0.0),
])
def test_count_plain_payment(paid, amount, to_price, extra, after):
    # P 5.2; AM 8: cap the counted payment, keep the rest separate.
    m, c, records = market(form="plain")
    c.paid = paid
    before = balances(m)
    assert p.count_payment(m, c, amount) == (to_price, extra)
    assert c.paid == after
    assert balances(m) == before
    assert notifications(records) == []


def test_plain_payment_accumulation_and_ended_contract():
    # P 5.2: accumulation rounds to four places; ended means owed zero.
    m, c, _ = market(form="plain", price=0.3)
    for amount, total in [(0.1, 0.1), (0.1, 0.2), (0.1, 0.3)]:
        assert p.count_payment(m, c, amount) == (0.1, 0.0)
        assert c.paid == total
    c.ended = 10
    assert p.count_payment(m, c, 0.2) == (0.0, 0.2)
    assert c.paid == 0.3


@pytest.mark.parametrize("deadline,submitted,price,paid,refund", [
    (9, 8, 20.0, 20.0, 0.0),
    (9, 9, 20.0, 20.0, 0.0),
    (9, 10, 20.0, 16.0, 4.0),
    (9, 11, 20.0, 12.0, 8.0),
    (9, 13, 20.0, 4.0, 16.0),
    (12, 10, 20.0, 20.0, 0.0),
    (12, 12, 20.0, 20.0, 0.0),
    (12, 13, 20.0, 16.0, 4.0),
    (13, 13, 20.0, 20.0, 0.0),
    (9, 10, 33.33, 26.664, 6.665999999999997),
    (9, 9, 0.0, 0.0, 0.0),
])
@pytest.mark.parametrize("how", ["approved", "automatic", "ruling", "close"])
def test_release_amounts_dates_and_accounts(deadline, submitted, price,
                                             paid, refund, how):
    # P 6.8, 6.10: submission day sets pay; release day does not.
    m, c, records = market(day=30, deadline=deadline, price=price)
    _, d = submission(c, day=submitted)
    got, returned = p.release(m, c, how)
    assert got == paid
    assert returned == price - paid
    assert returned == pytest.approx(refund, abs=1e-13)
    assert c.paid == paid
    assert c.release_day == 30
    assert c.release_how == how
    assert c.received is None
    assert d.outcome == "review"
    assert m.participants[MANAGER].balance == pytest.approx(60 + paid)
    assert m.participants[BUYER].balance == pytest.approx(60 + refund)
    assert m.participants[MANAGER].ledger == (
        [("escrow releases", paid)] if paid > 0 else []
    )
    assert m.participants[BUYER].ledger == (
        [("escrow refunds", returned)] if returned > 0 else []
    )
    assert notifications(records) == []


@pytest.mark.parametrize("deadline", [9, 12])
def test_release_formula_zero_after_close_boundary(deadline):
    # P 6.8 explicitly defines the factor even at the no-submission boundary.
    m, c, _ = market(day=16, deadline=deadline)
    submission(c, day=14)
    assert p.release(m, c, "approved") == (0.0, 20.0)
    assert c.paid == 0
    assert m.participants[MANAGER].ledger == []
    assert m.participants[BUYER].ledger == [("escrow refunds", 20.0)]


def test_release_rounds_four_places_and_does_not_round_refund():
    # P 6.8; README ties-to-even.
    m, c, _ = market({"value": {"late_decay": 0.25}}, price=0.01)
    submission(c, day=10)
    paid, refund = p.release(m, c, "approved")
    assert paid == 0.0075
    assert refund == 0.01 - 0.0075
    assert refund != 0.0
    assert m.participants[BUYER].ledger == [("escrow refunds", refund)]


def test_approval_release_defers_manager_credit_only():
    # P 6.8, 6.10; rounds 7: refund immediate, manager credit at round end.
    m, c, _ = market(day=12)
    submission(c, day=10)
    m.round = 2
    m.actor = BUYER
    manager = m.participants[MANAGER]
    manager.balance = -1
    manager.out_of_business = True
    assert p.release(m, c, "approved") == (16.0, 4.0)
    assert manager.balance == -1
    assert manager.out_of_business
    assert manager.ledger == [("escrow releases", 16.0)]
    assert m.participants[BUYER].balance == 64
    assert m.deferred == [state.Credit(MANAGER, 16.0, "escrow_release", BUYER)]
    m.actor = None
    state.land_credits(m)
    assert manager.balance == 15
    assert not manager.out_of_business
    assert manager.pending == [
        "[day 12, round 2] Your balance is positive again: "
        "you are back in business."
    ]


@pytest.mark.parametrize("submitted,paid,price,text", [
    (8, 20.0, 20.0, "20.00 of the price 20"),
    (9, 20.0, 20.0, "20.00 of the price 20"),
    (10, 16.0, 20.0, "16.00 of the price 20 (submitted 1 day late)"),
    (11, 12.0, 20.0, "12.00 of the price 20 (submitted 2 days late)"),
    (10, 26.664, 33.33, "26.66 of the price 33.33 (submitted 1 day late)"),
    (9, 1234567.0, 1234567.0, "1234567.00 of the price 1.23457e+06"),
])
def test_paid_text(submitted, paid, price, text):
    # P 6.8: G price, F2 paid, deadline-based singular/plural lateness.
    m, c, _ = market(price=price)
    submission(c, day=submitted)
    c.paid = paid
    assert p.paid_text(m, c) == text


@pytest.mark.parametrize("refund,text", [
    (0.0, ""), (1e-10, ""), (1e-9, ""),
    (1.0001e-9, ", and 0.00 came back to you"),
    (4.0, ", and 4.00 came back to you"),
    (6.666, ", and 6.67 came back to you"),
])
def test_refund_text_threshold(refund, text):
    # P 6.5-6.6: include back only for refund strictly above 1e-9.
    assert p.back(refund) == text


@pytest.mark.parametrize("submitted,paid,back,late", [
    (9, "20.00", "", ""),
    (10, "16.00", ", and 4.00 came back to you", " (submitted 1 day late)"),
    (11, "12.00", ", and 8.00 came back to you", " (submitted 2 days late)"),
])
def test_automatic_release_exact_notifications(submitted, paid, back, late):
    # P 6.5, 6.8, 9; E 3.5: buyer before manager.
    day = submitted + 2
    m, c, records = market(day=day)
    _, d = submission(c, day=submitted)
    p.auto_release(m, c)
    assert d.outcome == "auto"
    assert d.decided == day
    assert c.release_day == day
    assert c.release_how == "automatic"
    assert c.received == day
    assert notifications(records) == [
        (BUYER, f"[day {day}] R-b8b on contract-1 was released: "
         f"you neither approved nor rejected it by the start of day {day}. "
         f"It is yours; {paid} of the price 20 went to M-14b{back}."),
        (MANAGER, f"[day {day}] R-b8b on contract-1 was released: "
         f"B-c53 neither approved nor rejected it by the start of day {day}. "
         f"You received {paid} of the price 20{late}; contract-1 is closed."),
    ]
    before = snapshot(m)
    p.auto_release(m, c)
    assert snapshot(m) == before


@pytest.mark.parametrize("review_days", [1, 2, 5])
def test_automatic_release_timer_and_overdue_processing(review_days):
    # P 6.5; E 3.5: release when scheduled day is today or earlier.
    m, c, records = market(
        {"contracts": {"escrow": {"review_days": review_days}}}, day=8,
    )
    _, d = submission(c, day=8)
    m.day = 8 + review_days - 1
    before = snapshot(m)
    p.auto_release(m, c)
    assert snapshot(m) == before
    m.day = 8 + review_days + 1
    p.auto_release(m, c)
    assert d.decided == m.day
    assert c.received == m.day
    assert f"start of day {8 + review_days}." in notifications(records)[0][1]


@pytest.mark.parametrize("outcome", [None, "rejected", "approved", "auto"])
def test_no_automatic_release_without_review(outcome):
    # P 6.1, 6.5.
    m, c, _ = market(day=20)
    if outcome is not None:
        submission(c, outcome=outcome, decided=10)
        if outcome in {"approved", "auto"}:
            c.release_day = 10
            c.release_how = outcome
            c.received = 10
    before = snapshot(m)
    p.auto_release(m, c)
    assert snapshot(m) == before


def test_market_close_releases_review_immediately():
    # E 6 step 2: close overrides review timer, preserving submission-day pay.
    m, c, _ = market(day=10)
    _, d = submission(c, day=10)
    p.auto_release(m, c, "close")
    assert (d.outcome, d.decided) == ("auto", 10)
    assert (c.release_how, c.release_day, c.received) == ("close", 10, 10)
    assert c.paid == 16
    assert m.participants[BUYER].balance == 64
    assert m.participants[MANAGER].balance == 76


@pytest.mark.parametrize("reading,threshold,winner", [
    (5.9, 6, "buyer"), (6.0, 6, "manager"), (6.1, 6, "manager"),
    (0.0, 0, "manager"), (10.0, 10, "manager"), (9.9, 10, "buyer"),
])
def test_ruling_threshold_ties_and_money(monkeypatch, reading, threshold,
                                         winner):
    # P 6.6-6.7: equality wins for manager; positive fee gates receipt.
    m, c, _ = market(day=11)
    c.threshold = threshold
    r, d = submission(c, day=9, outcome="rejected", decided=10, disputed=10)
    calls = fixed_arbiter(monkeypatch, reading)
    p.rule(m, c)
    assert calls == [(37, "task-1", "R-b8b", r.q, 0.5, 1)]
    assert (d.dispute.reading, d.dispute.winner) == (reading, winner)
    assert d.outcome == "rejected"
    assert d.decided == 10
    assert d.dispute.at_close is False
    assert c.received is None
    if winner == "manager":
        assert c.release_day == 11
        assert c.release_how == "ruling"
        assert c.paid == 20
        assert m.participants[MANAGER].balance == 90
        assert m.participants[MANAGER].ledger == [
            ("dispute deposits returned", 10.0), ("escrow releases", 20.0),
        ]
    else:
        assert c.release_day is None
        assert c.paid == 0
        assert m.participants[MANAGER].balance == 60
        assert m.participants[MANAGER].ledger == []
    assert m.participants[BUYER].balance == 60
    assert m.participants[BUYER].ledger == []
    before = snapshot(m)
    p.rule(m, c)
    assert snapshot(m) == before
    assert len(calls) == 1


@pytest.mark.parametrize("price,fee_text", [
    (20.0, "10"), (11.1, "5.55"), (3.6, "1.80"),
])
def test_manager_winning_ruling_exact_text(monkeypatch, price, fee_text):
    # P 6.6, 6.8, 9; README CR and G.
    m, c, records = market(day=11, price=price)
    submission(c, day=9, outcome="rejected", decided=10, disputed=10)
    fixed_arbiter(monkeypatch, 6.4)
    p.rule(m, c)
    head = ("[day 11] The arbiter's reading of R-b8b on contract-1 is 6.4, "
            "against the threshold 6:")
    assert notifications(records) == [
        (MANAGER, head + f" you won the dispute. Your deposit of {fee_text} "
         f"came back, and you received {price:.2f} of the price {price:g}; "
         "contract-1 is closed."),
        (BUYER, head + " the dispute went against you. The price was released "
         f"to M-14b. R-b8b is yours once you pay the arbiter's fee of {fee_text} "
         "(pay_escrow_arbiter)."),
    ]


def test_ruling_late_release_text_and_refund(monkeypatch):
    # P 6.6 and 6.8: pay cut uses disputed submission, not ruling day.
    m, c, records = market(day=13)
    submission(c, day=10, outcome="rejected", decided=11, disputed=12)
    fixed_arbiter(monkeypatch, 6.0)
    p.rule(m, c)
    head = ("[day 13] The arbiter's reading of R-b8b on contract-1 is 6.0, "
            "against the threshold 6:")
    assert notifications(records) == [
        (MANAGER, head + " you won the dispute. Your deposit of 10 came back, "
         "and you received 16.00 of the price 20 (submitted 1 day late); "
         "contract-1 is closed."),
        (BUYER, head + " the dispute went against you. The price was released "
         "to M-14b, and 4.00 came back to you. R-b8b is yours once you pay "
         "the arbiter's fee of 10 (pay_escrow_arbiter)."),
    ]
    assert c.paid == 16
    assert c.received is None
    assert m.participants[BUYER].balance == 64


@pytest.mark.parametrize("day,tail", [
    (13, " The rejection stands; you can submit another result while "
     "the escrow holds the price."),
    (14, " The rejection stands."),
    (15, " The rejection stands."),
])
def test_buyer_winning_ruling_exact_text(monkeypatch, day, tail):
    # P 6.6, 9: short manager text from close day onward.
    m, c, records = market(day=day)
    submission(c, day=11, outcome="rejected", decided=11, disputed=12)
    fixed_arbiter(monkeypatch, 5.9)
    p.rule(m, c)
    head = (f"[day {day}] The arbiter's reading of R-b8b on contract-1 is 5.9, "
            "against the threshold 6:")
    assert notifications(records) == [
        (MANAGER, head + " you lost the dispute and your deposit of 10." + tail),
        (BUYER, head + " the dispute went your way. "
         "Your rejection stands and the price stays held."),
    ]


def test_ruling_worked_reading_example(monkeypatch):
    # RND 3.11; P 6.6: 7.0219 - 0.5 * 1.15229 rounds to 6.4.
    m, c, _ = market(day=11)
    _, d = submission(c, day=9, q=7.0219, outcome="rejected",
                      decided=10, disputed=10)
    fixed_normals(monkeypatch, [-1.15229])
    p.rule(m, c)
    assert d.dispute.reading == 6.4
    assert d.dispute.winner == "manager"


@pytest.mark.parametrize("q,z,decimals,expected", [
    (0.1, -7.0, 3, 0.0),
    (9.9, 7.0, 3, 10.0),
    (6.5, 0.0, 0, 6.0),
    (7.5, 0.0, 0, 8.0),
    (6.25, 0.0, 1, 6.2),
    (6.125, 0.0, 2, 6.12),
    (6.0625, 0.0, 3, 6.062),
])
def test_arbiter_clips_and_rounds(monkeypatch, q, z, decimals, expected):
    # P 6.6; RND 3.11; README rounding.
    m, c, records = market({"tests": {"decimals": decimals}}, day=11)
    _, d = submission(c, day=9, q=q, outcome="rejected",
                      decided=10, disputed=10)
    fixed_normals(monkeypatch, [z])
    p.rule(m, c)
    assert d.dispute.reading == expected
    assert f" is {expected:.{decimals}f}, against" in notifications(records)[0][1]


def test_arbiter_zero_sd_and_repost_stream(monkeypatch):
    # RND 3.11; C 3.10: arbiter noise may be zero.
    m, c, _ = market(
        {"contracts": {"escrow": {"arbiter_sd": 0}}},
        day=11, cid="contract-1-2",
    )
    r, _ = submission(c, day=9, outcome="rejected", decided=10, disputed=10)
    calls = fixed_arbiter(monkeypatch, 7.0)
    p.rule(m, c)
    assert calls == [(37, "task-1-2", "R-b8b", r.q, 0.0, 1)]


def test_ruling_waits_until_next_day_regardless_of_window(monkeypatch):
    # P 6.6; E 3.4: ruling delay is one day, independent of dispute window.
    m, c, _ = market(
        {"contracts": {"escrow": {"dispute_window_days": 7}}}, day=10,
    )
    _, d = submission(c, day=8, outcome="rejected", decided=9, disputed=10)
    calls = fixed_arbiter(monkeypatch, 6.0)
    before = snapshot(m)
    p.rule(m, c)
    assert snapshot(m) == before
    assert calls == []
    m.day = 11
    p.rule(m, c)
    assert d.dispute.winner == "manager"
    assert len(calls) == 1


@pytest.mark.parametrize("state_name", ["empty", "review", "undisputed"])
def test_rule_without_open_dispute_is_noop(monkeypatch, state_name):
    # P 6.1, 6.6; E 3.4.
    m, c, _ = market(day=20)
    if state_name == "review":
        submission(c)
    elif state_name == "undisputed":
        submission(c, outcome="rejected", decided=10)
    calls = fixed_arbiter(monkeypatch, 6.0)
    before = snapshot(m)
    p.rule(m, c)
    assert snapshot(m) == before
    assert calls == []


def test_zero_fee_returns_zero_deposit_and_receives_immediately(monkeypatch):
    # P 6.6-6.7; view 8: zero returned deposits still have an accounts entry.
    m, c, records = market(
        {"contracts": {"escrow": {"dispute_fee_rate": 0.1}}},
        day=11, price=0.01,
    )
    submission(c, day=9, outcome="rejected", decided=10, disputed=10)
    fixed_arbiter(monkeypatch, 6.0)
    p.rule(m, c)
    assert c.received == 11
    assert m.participants[MANAGER].ledger == [
        ("dispute deposits returned", 0.0), ("escrow releases", 0.01),
    ]
    assert m.participants[BUYER].ledger == []
    assert "deposit of 0 came back" in notifications(records)[0][1]
    assert "arbiter's fee of 0 (pay_escrow_arbiter)." in notifications(records)[1][1]


@pytest.mark.parametrize("starting,comes_back", [
    (-31.0, False), (-30.0, False), (-29.0, True),
    (-10.0, True), (-1.0, True), (0.0, True),
])
def test_ruling_revival_precedes_ruling_notices(monkeypatch, starting,
                                               comes_back):
    # P 6.6; E 3.7, 9.3: deposit or release can revive, strictly above zero.
    m, c, records = market(day=11)
    submission(c, day=9, outcome="rejected", decided=10, disputed=10)
    manager = m.participants[MANAGER]
    manager.balance = starting
    manager.out_of_business = True
    fixed_arbiter(monkeypatch, 6.0)
    p.rule(m, c)
    assert manager.balance == starting + 30
    assert manager.out_of_business is (not comes_back)
    notices = notifications(records)
    if comes_back:
        assert notices[0] == (
            MANAGER, "[day 11] Your balance is positive again: "
            "you are back in business.",
        )
        assert len(notices) == 3
    else:
        assert len(notices) == 2
    assert "you won the dispute" in notices[-2][1]
    assert notices[-1][0] == BUYER


def test_auto_release_revival_precedes_buyer_notice():
    # P 6.5; E 3.7, 9.3.
    m, c, records = market(day=11)
    submission(c, day=9)
    m.participants[MANAGER].balance = 0
    m.participants[MANAGER].out_of_business = True
    p.auto_release(m, c)
    notices = notifications(records)
    assert notices[0] == (
        MANAGER, "[day 11] Your balance is positive again: "
        "you are back in business.",
    )
    assert [pid for pid, _ in notices[1:]] == [BUYER, MANAGER]
    assert not m.participants[MANAGER].out_of_business


@pytest.mark.parametrize("balance,submitted,received,remaining", [
    (10.0, 9, True, 0.0),
    (10.0 - 5e-10, 9, True, -5e-10),
    (10.0 - 2e-9, 9, False, 10.0 - 2e-9),
    (6.0, 10, True, 0.0),
    (5.0, 10, False, 9.0),
    (-1.0, 10, False, 3.0),
])
def test_close_ruling_collects_fee_after_own_refund(monkeypatch, balance,
                                                   submitted, received,
                                                   remaining):
    # E 6 step 1; P 6.7: balance + 1e-9, after this release's refund.
    m, c, _ = market(day=11)
    _, d = submission(c, day=submitted, outcome="rejected",
                      decided=11, disputed=11)
    m.participants[BUYER].balance = balance
    fixed_arbiter(monkeypatch, 6.0)
    p.rule(m, c, at_close=True)
    assert d.dispute.at_close is True
    assert d.dispute.winner == "manager"
    assert c.release_day == 11
    assert c.release_how == "ruling"
    assert c.received == (11 if received else None)
    assert m.participants[BUYER].balance == pytest.approx(remaining, abs=1e-12)
    fees = [entry for entry in m.participants[BUYER].ledger
            if entry[0] == "arbiter's fees paid"]
    assert fees == ([("arbiter's fees paid", 10.0)] if received else [])


def test_close_does_not_collect_fee_from_earlier_ruling(monkeypatch):
    # P 6.7; E 6 step 1: only disputes still open at close have fees collected.
    m, c, _ = market(day=11)
    _, d = submission(c, day=9, outcome="rejected", decided=10, disputed=10)
    calls = fixed_arbiter(monkeypatch, 6.0)
    p.rule(m, c)
    m.day = 12
    before = snapshot(m)
    p.rule(m, c, at_close=True)
    assert snapshot(m) == before
    assert c.received is None
    assert d.dispute.at_close is False
    assert len(calls) == 1


@pytest.mark.parametrize("mode,day,expected", [
    ("empty", 10, False),
    ("review", 10, True),
    ("rejected", 10, True),
    ("rejected", 11, True),
    ("rejected", 12, False),
    ("dispute", 30, True),
    ("lost", 10, False),
])
def test_pending_states_and_inclusive_window(mode, day, expected):
    # P 6.1, 6.9: window alone decides, regardless of balance or business.
    m, c, _ = market(day=day)
    m.participants[MANAGER].balance = -100
    m.participants[MANAGER].out_of_business = True
    if mode == "review":
        submission(c, day=9)
    elif mode != "empty":
        _, d = submission(c, day=9, outcome="rejected", decided=10,
                          disputed=10 if mode in {"dispute", "lost"} else None)
        if mode == "lost":
            d.dispute.winner = "buyer"
            d.dispute.reading = 0.0
    assert p.pending(m, c) is expected


def test_new_submission_supersedes_old_rejection():
    # P 6.1, 6.3: only the latest submission can remain disputable.
    m, c, _ = market(day=11)
    submission(c, day=8, outcome="rejected", decided=10, label="R-001")
    submission(c, day=10, outcome="rejected", decided=10,
               disputed=10, label="R-002")
    c.deliveries[-1].dispute.winner = "buyer"
    c.deliveries[-1].dispute.reading = 0.0
    assert p.pending(m, c) is False
    assert p.end_rule(m, c) == "stands"


@pytest.mark.parametrize("deadline,its", [
    (9, "its deadline"), (12, "its task's due day"),
])
@pytest.mark.parametrize("day,late", [(14, 5), (16, 7)])
def test_lateness_close_money_state_and_exact_text(deadline, its, day, late):
    # P 6.9-6.10, 9; E 3.6.
    m, c, records = market(day=day, deadline=deadline)
    result(c, arrival=30)
    p.lateness_close(m, c)
    assert c.closed == "late"
    assert c.release_day is None
    assert c.received is None
    assert c.paid == 0
    assert c.ended is None
    assert m.tasks[c.task].state == "contracted"
    assert m.tasks[c.task].contract == c.id
    assert m.participants[BUYER].balance == 80
    assert m.participants[BUYER].ledger == [("escrow refunds", 20.0)]
    assert m.participants[MANAGER].balance == 60
    assert notifications(records) == [
        (BUYER, f"[day {day}] The escrow of contract-1 closed: nothing was "
         f"released by {late} days after {its}, so its price 20 came back to you."),
        (MANAGER, f"[day {day}] The escrow of contract-1 closed unreleased "
         f"({late} days after {its}): it takes no more submissions."),
    ]
    before = snapshot(m)
    p.lateness_close(m, c)
    assert snapshot(m) == before


def test_lateness_close_singular_day():
    # P 6.9; C 6.2; README singular rule.
    m, c, records = market({"value": {"late_decay": 1}}, day=10)
    p.lateness_close(m, c)
    assert notifications(records) == [
        (BUYER, "[day 10] The escrow of contract-1 closed: nothing was released "
         "by 1 day after its deadline, so its price 20 came back to you."),
        (MANAGER, "[day 10] The escrow of contract-1 closed unreleased "
         "(1 day after its deadline): it takes no more submissions."),
    ]


@pytest.mark.parametrize("decay,offset", [
    (0.2, 5), (0.25, 4), (0.3, 4), (1 / 3, 3),
    (1.0, 1), (0.1, 10),
    (1 / (4 + 0.5e-9), 4),
    (1 / (4 + 2e-9), 5),
])
def test_derived_close_day_boundary(decay, offset):
    # C 6.2; P 6.9: ceil(1 / decay - 1e-9), including tolerance.
    m, c, _ = market({
        "value": {"late_decay": decay},
        "time": {"close_after_due_days": 20},
    }, day=9 + offset - 1)
    before = snapshot(m)
    p.lateness_close(m, c)
    assert snapshot(m) == before
    m.day += 1
    p.lateness_close(m, c)
    assert c.closed == "late"


@pytest.mark.parametrize("status", [
    "plain", "ended", "released", "closed", "review", "dispute", "disputable",
])
def test_lateness_close_does_not_override_other_states(status):
    # P 6.1, 6.9; E 3.6.
    m, c, _ = market(day=14, form="plain" if status == "plain" else "escrow")
    if status == "ended":
        c.ended = 10
    elif status == "released":
        c.release_day = 13
    elif status == "closed":
        c.closed = "close"
    elif status == "review":
        submission(c, day=13)
    elif status in {"dispute", "disputable"}:
        submission(c, day=12, outcome="rejected", decided=13,
                   disputed=13 if status == "dispute" else None)
    before = snapshot(m)
    p.lateness_close(m, c)
    assert snapshot(m) == before


def test_disputable_rejection_delays_close_until_window_expires():
    # P 6.9; E 3.6: an insolvent manager's dispute window still postpones close.
    m, c, _ = market(day=14)
    submission(c, day=12, outcome="rejected", decided=13)
    m.participants[MANAGER].balance = -1
    m.participants[MANAGER].out_of_business = True
    p.lateness_close(m, c)
    assert c.closed is None
    m.day = 15
    p.lateness_close(m, c)
    assert c.closed == "late"


@pytest.mark.parametrize("winner", ["manager", "buyer"])
def test_ruling_before_lateness_close(monkeypatch, winner):
    # E 3.4-3.6; P 6.9: buyer ruling permits close that same morning.
    m, c, records = market(day=14)
    submission(c, day=12, outcome="rejected", decided=12, disputed=13)
    fixed_arbiter(monkeypatch, 6.0 if winner == "manager" else 5.9)
    p.rule(m, c)
    p.auto_release(m, c)
    p.lateness_close(m, c)
    if winner == "manager":
        assert c.closed is None
        assert c.release_how == "ruling"
        assert c.paid == 8.0
        assert len(notifications(records)) == 2
    else:
        assert c.closed == "late"
        assert c.release_day is None
        assert m.participants[BUYER].balance == 80
        assert [pid for pid, _ in notifications(records)] == [
            MANAGER, BUYER, BUYER, MANAGER,
        ]


def test_automatic_release_wins_over_lateness_close():
    # E 3.5-3.6; P 6.9: pending review releases before the close step.
    m, c, _ = market(day=14)
    submission(c, day=12)
    p.auto_release(m, c)
    p.lateness_close(m, c)
    assert c.closed is None
    assert c.release_how == "automatic"
    assert c.received == 14
    assert c.paid == 8.0
    assert m.participants[BUYER].ledger == [("escrow refunds", 12.0)]


@pytest.mark.parametrize("form,day,signed,deadline,delivery,expected", [
    ("plain", 9, 1, 9, False, "early"),
    ("plain", 10, 1, 9, False, "overdue"),
    ("plain", 10, 9, 9, False, "overdue"),
    ("plain", 15, 14, 13, False, "late_signed"),
    ("plain", 15, 14, 13, True, "delivered"),
    ("plain", 8, 1, 9, True, "delivered"),
    ("escrow", 9, 1, 9, False, "early"),
    ("escrow", 10, 1, 9, False, "overdue"),
    ("escrow", 12, 8, 12, False, "early"),
    ("escrow", 13, 8, 12, False, "overdue"),
    ("escrow", 9, 9, 13, False, "cap"),
    ("escrow", 14, 9, 13, False, "cap"),
    ("escrow", 21, 20, 13, False, "cap"),
])
def test_end_rule_plain_and_empty_escrow(form, day, signed, deadline,
                                         delivery, expected):
    # P 7: first matching row; full-pay cap differs from ordinary overdue.
    m, c, _ = market(day=day, form=form, signed=signed, deadline=deadline)
    if delivery:
        submission(c, day=8, outcome=None)
    before = snapshot(m)
    assert p.end_rule(m, c) == expected
    assert snapshot(m) == before


@pytest.mark.parametrize("mode,day,expected", [
    ("review", 8, "review"),
    ("review", 20, "review"),
    ("dispute", 20, "dispute"),
    ("rejected", 10, "disputable"),
    ("rejected", 11, "disputable"),
    ("rejected", 12, "stands"),
    ("lost", 10, "stands"),
    ("released", 20, "released"),
    ("closed", 20, "closed"),
    ("ended", 20, "ended"),
])
def test_end_rule_escrow_state_precedence(mode, day, expected):
    # P 7: review/dispute/rejection rows precede cap and deadline.
    m, c, _ = market(day=day, deadline=13)
    if mode == "review":
        submission(c, day=7)
    elif mode in {"dispute", "rejected", "lost", "released", "ended"}:
        _, d = submission(c, day=8, outcome="rejected", decided=10,
                          disputed=10 if mode != "rejected" else None)
        if mode in {"lost", "released", "ended"}:
            d.dispute.winner = "buyer" if mode == "lost" else "manager"
            d.dispute.reading = 0.0 if mode == "lost" else 6.0
        if mode in {"released", "ended"}:
            c.release_day = 11
            c.release_how = "ruling"
        if mode == "ended":
            c.ended = 12
    elif mode == "closed":
        c.closed = "late"
    assert p.end_rule(m, c) == expected


@pytest.mark.parametrize("form", ["plain", "escrow"])
def test_ended_is_first_end_rule(form):
    # P 7: already ended precedes all form-specific rows.
    m, c, _ = market(form=form)
    c.ended = 8
    assert p.end_rule(m, c) == "ended"


def test_full_pay_cap_when_close_days_is_one():
    # P 7; C 6.2-6.3: due day itself is the full-pay cap.
    m, c, _ = market({"value": {"late_decay": 1}}, day=10, deadline=9)
    assert p.end_rule(m, c) == "cap"
    c.form = "plain"
    c.threshold = None
    assert p.end_rule(m, c) == "overdue"


@pytest.mark.parametrize("post_round,open_day,open_round", [
    (1, 1, 3), (2, 2, 1), (3, 2, 2),
])
def test_opening_round_worked_examples(post_round, open_day, open_round):
    # P 8; C 6.6; E 4: window counts across days.
    m, _, records = market(day=open_day)
    m.round = open_round
    post = state.Post("post-1", "task-1", BUYER, 1, post_round)
    m.posts[post.id] = post
    m.bids["bid-1"] = state.Bid("bid-1", post.id, MANAGER, 10)
    p.opening_notices(m)
    assert notifications(records) == [
        (BUYER, f"[day {open_day}, round {open_round}] The bids on post-1 "
         "are opened: 1 bid, which you can accept from now."),
    ]


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_opening_notice_only_at_exact_opening_round(offset):
    # P 8; C 6.6: custom window and rounds-per-day.
    m, _, records = market({
        "time": {"rounds_per_day": 4},
        "contracts": {"bid_window_rounds": 5},
    }, day=3)
    m.round = 2 + offset
    post = state.Post("post-1", "task-1", BUYER, 1, 4)
    m.posts[post.id] = post
    m.bids["bid-1"] = state.Bid("bid-1", post.id, MANAGER, 10)
    p.opening_notices(m)
    assert notifications(records) == ([
        (BUYER, "[day 3, round 2] The bids on post-1 are opened: "
         "1 bid, which you can accept from now."),
    ] if offset == 0 else [])


@pytest.mark.parametrize("closed", [None, "accepted", "withdrawn"])
@pytest.mark.parametrize("bid_status", ["open", "accepted", "lost", "withdrawn"])
@pytest.mark.parametrize("out_of_business", [False, True])
def test_opening_notice_filters(closed, bid_status, out_of_business):
    # P 8: only open posts and open bids from managers in business.
    m, _, records = market(day=1)
    m.round = 3
    m.posts["post-1"] = state.Post(
        "post-1", "task-1", BUYER, 1, 1, closed=closed,
    )
    m.bids["bid-1"] = state.Bid(
        "bid-1", "post-1", MANAGER, 10, status=bid_status,
    )
    m.participants[MANAGER].out_of_business = out_of_business
    m.participants[MANAGER].balance = 0
    p.opening_notices(m)
    expected = closed is None and bid_status == "open" and not out_of_business
    assert len(notifications(records)) == int(expected)


def test_opening_notices_creation_order_plural_and_no_empty_notice():
    # P 8-9; E 4: post creation order, not numeric IDs or bid order.
    m, _, records = market(day=1)
    m.round = 3
    state.add_participant(m, "M-002", "manager")
    state.add_participant(m, "M-003", "manager")
    m.participants["M-003"].out_of_business = True
    for pid in ["post-9", "post-2", "post-3"]:
        m.posts[pid] = state.Post(pid, "task-1", BUYER, 1, 1)
    m.posts["post-9"].form = "escrow"
    m.posts["post-9"].threshold = 8
    for bid in [
        state.Bid("bid-1", "post-2", MANAGER, 10),
        state.Bid("bid-2", "post-9", MANAGER, 11),
        state.Bid("bid-3", "post-9", "M-002", 12),
        state.Bid("bid-4", "post-9", "M-003", 13),
        state.Bid("bid-5", "post-3", "M-003", 14),
    ]:
        m.bids[bid.id] = bid
    before = balances(m)
    p.opening_notices(m)
    assert notifications(records) == [
        (BUYER, "[day 1, round 3] The bids on post-9 are opened: "
         "2 bids, which you can accept from now."),
        (BUYER, "[day 1, round 3] The bids on post-2 are opened: "
         "1 bid, which you can accept from now."),
    ]
    assert balances(m) == before
    assert all(not person.pending for pid, person in m.participants.items()
               if pid != BUYER)


def test_day_start_arrival_then_due_notice_per_contract():
    # E 3.3; P 1.4, 3.3, 9: exercise the exposed steps in prescribed order.
    m, first, records = market(day=9, cid="contract-9")
    result(first, "R-009", arrival=9)
    second = copy.deepcopy(first)
    second.id = "contract-2"
    second.results[0].label = "R-002"
    m.contracts[second.id] = second
    for c in m.contracts.values():
        p.arrivals(m, c)
        p.due_notice(m, c)
    assert notifications(records) == [
        (MANAGER, "[day 9] R-009 (your employee, fresh) "
         "for contract-9 has arrived."),
        (BUYER, "[day 9] contract-9 is due today (deadline day 9)."),
        (MANAGER, "[day 9] contract-9 is due today (deadline day 9)."),
        (MANAGER, "[day 9] R-002 (your employee, fresh) "
         "for contract-2 has arrived."),
        (BUYER, "[day 9] contract-2 is due today (deadline day 9)."),
        (MANAGER, "[day 9] contract-2 is due today (deadline day 9)."),
    ]


def test_close_rulings_consume_balance_in_contract_order(monkeypatch):
    # E 6 step 1: earlier collected fees affect later disputes at close.
    m, first, _ = market(day=11, cid="contract-9")
    submission(first, day=9, outcome="rejected", decided=11, disputed=11)
    second = copy.deepcopy(first)
    second.id = "contract-2"
    m.contracts[second.id] = second
    m.participants[BUYER].balance = 15
    fixed_arbiter(monkeypatch, 6.0)
    for c in m.contracts.values():
        p.rule(m, c, at_close=True)
    assert first.received == 11
    assert second.received is None
    assert first.release_day == second.release_day == 11
    assert m.participants[BUYER].balance == 5
    assert m.participants[BUYER].ledger == [("arbiter's fees paid", 10.0)]


def test_close_review_refund_does_not_retroactively_pay_dispute_fee(monkeypatch):
    # E 6 steps 1-2: review refunds occur after all close rulings.
    m, disputed, _ = market(day=11)
    submission(disputed, day=9, outcome="rejected", decided=11, disputed=11)
    review = copy.deepcopy(disputed)
    review.id = "contract-2"
    review.deliveries.clear()
    review.results.clear()
    submission(review, day=10)
    m.contracts[review.id] = review
    m.participants[BUYER].balance = 6
    fixed_arbiter(monkeypatch, 6.0)
    for c in m.contracts.values():
        p.rule(m, c, at_close=True)
    for c in m.contracts.values():
        p.auto_release(m, c, "close")
    assert disputed.received is None
    assert review.received == 11
    assert m.participants[BUYER].balance == 10
    assert m.participants[BUYER].ledger == [("escrow refunds", 4.0)]
