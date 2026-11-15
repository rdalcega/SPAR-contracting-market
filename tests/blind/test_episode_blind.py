"""Blind specification tests. No filesystem or network operations."""

import copy
import json
import math

import pytest

from market import config, episode, processes, rng, rounds, state
from market.state import (
    Bid,
    Contract,
    Delivery,
    Dispute,
    Market,
    Message,
    Payment,
    Post,
    Result,
    Task,
)


BUYERS = ["B-fff", "B-001"]
MANAGERS = ["M-fff", "M-001", "M-002"]
IDS = BUYERS + MANAGERS

COST_KEYS = (
    "tokens",
    "employee_runs",
    "own_runs",
    "manager_tests",
    "buyer_tests",
    "escrow_setup_fees",
    "dispute_costs",
)

COUNT_METRICS = """
tasks_created tasks_posted tasks_done tasks_withdrawn tasks_escrow_closed
bids bids_escrow bids_withdrawn accepted_above_worth
contracts contracts_plain contracts_escrow
plain_delivered plain_ended plain_undelivered
escrow_released escrow_ended escrow_closed_late escrow_refunded_at_close
plain_paid_in_full plain_seen plain_unpaid_after_seen plain_delivered_unseen
payments advance_payments payments_without_contract
payments_buyer_to_manager payments_manager_to_buyer
payments_manager_to_manager payments_buyer_to_buyer
runs_started own_runs refine_runs manager_readings buyer_readings
deliveries_plain submissions_escrow successful_contracts late_deliveries
runs_unfinished_at_end escrow_approved escrow_rejected
escrow_auto_released escrow_released_at_close escrow_released_late
disputes disputes_won_manager disputes_won_buyer disputes_ruled_at_close
dispute_rulings_against_q arbiter_fees_owed arbiter_fees_paid
out_of_business_events back_in_business_events managers_out_of_business_at_end
summarizer_calls summaries_written summaries_failed
summarizer_input_tokens summarizer_output_tokens calls_unknown_cost
messages messages_manager_manager messages_buyer_buyer messages_cross_role
messages_to_managers messages_to_buyers messages_to_everyone
""".split()

ROLE_COUNTERS = """
turns actions failed_actions format_errors corrections programs
turns_lost_provider turns_lost_format calls input_tokens output_tokens
reasoning_tokens
""".split()

CREDIT_METRICS = """
welfare value_obtained budgets_credited unpaid_amount payment_credits
paid_toward_prices paid_beyond_prices payment_credits_without_contract
escrow_amount_released escrow_amount_refunded
""".split()

EMPTY_MEANS = """
mean_value_frac mean_price_to_worth price_mean_plain price_mean_escrow
mean_delivered_q mean_days_late
""".split()


def market(raw=None):
    m = Market(seed=37, cfg=config.resolve(raw or {}))
    for pid in BUYERS:
        state.add_participant(m, pid, "buyer", 1.0)
    for pid in MANAGERS:
        state.add_participant(m, pid, "manager", 1.0)
    m.day = 20
    m.last_arrival = 10
    return m


def recording(m):
    records = []
    m.log = records.append
    return records


def task(m, worth=81, due=10, buyer=BUYERS[0]):
    tid = "task-" + str(len(m.tasks) + 1)
    t = Task(tid, buyer, worth, due)
    m.tasks[tid] = t
    return t


def contract(m, form="plain", price=20.0, worth=81, due=10,
             deadline=None, buyer=BUYERS[0], manager=MANAGERS[0],
             existing_task=None):
    t = existing_task or task(m, worth, due, buyer)
    suffix = t.id.removeprefix("task")
    attempt = len(t.ended) + 1
    if attempt > 1:
        suffix += "-" + str(attempt)
    cid = "contract" + suffix
    post_id = "post" + suffix
    p = Post(post_id, t.id, buyer, 1, 1, form,
             7 if form == "escrow" else None, closed="accepted")
    m.posts[post_id] = p
    c = Contract(
        cid, t.id, post_id, buyer, manager, form, price,
        7 if form == "escrow" else None, 1,
        due if deadline is None else deadline,
    )
    m.contracts[cid] = c
    t.state, t.post, t.contract = "contracted", post_id, cid
    return c


def result(c, q=7.0, arrival=2, worker="employee", kind="fresh",
           parent=None):
    label = "R-" + format(len(c.results), "03x")
    r = Result(label, worker, kind, parent, 1, arrival, q)
    c.results.append(r)
    return r


def delivery(c, q=7.0, day=10, round_number=1, outcome=None):
    r = result(c, q=q)
    d = Delivery(r.label, day, round_number, outcome=outcome)
    c.deliveries.append(d)
    return d


def fund_escrow(m, c):
    # Fixture bookkeeping, not an oracle for settlement.
    state.debit(m, c.buyer, c.price, "paid into escrow (total)")
    fee = round(m.cfg["contracts"]["escrow"]["fee_rate"] * c.price, 2)
    state.charge(m, c.buyer, fee, "escrow fees")


def disputed(m, c, q=7.0, submitted=10, disputed_day=10):
    d = delivery(c, q, submitted, outcome="rejected")
    d.decided = disputed_day
    d.dispute = Dispute(disputed_day)
    deposit = round(
        m.cfg["contracts"]["escrow"]["dispute_fee_rate"] * c.price, 2
    )
    state.debit(m, c.manager, deposit, "dispute deposits", check=True)
    return d


def rows(m):
    return episode.settle(m)["settlement"]


def metrics(m):
    return episode.settle(m)["metrics"]


def assert_metrics(m, expected):
    actual = metrics(m)
    for name, value in expected.items():
        assert actual[name] == value, name


def fix_arbiter(monkeypatch, reading):
    monkeypatch.setattr(
        rng, "arbiter",
        lambda *args, **kwargs: {"z": 0.0, "reading": reading},
    )


def forbid(*args, **kwargs):
    raise AssertionError("This operation must not occur")


def fixed_start_draws(monkeypatch):
    shares = {
        ("buyer", 0): 1.36,
        ("buyer", 1): 1.0,
        ("manager", 0): 0.6,
        ("manager", 1): 1.4,
        ("manager", 2): 1.0,
    }
    calls = []

    def pid(seed, role, seat, used):
        expected = IDS[len(calls)]
        assert seed == 123
        assert list(used) == IDS[:len(calls)]
        calls.append((role, seat))
        return expected

    monkeypatch.setattr(rng, "participant_id", pid)
    monkeypatch.setattr(
        rng, "share",
        lambda seed, role, seat, lo, hi, decimals: shares[(role, seat)],
    )
    monkeypatch.setattr(rng, "horizon", lambda *args: 14)
    return calls


def test_start_seats_shares_balances_and_no_initial_notices(monkeypatch):
    # Episode 1.1-1.4; config 6.4: the 1.36 worked example.
    calls = fixed_start_draws(monkeypatch)
    m = episode.start(123, config.resolve({}))
    assert calls == [
        ("buyer", 0), ("buyer", 1),
        ("manager", 0), ("manager", 1), ("manager", 2),
    ]
    assert list(m.participants) == IDS
    assert [p.seat for p in m.participants.values()] == [0, 1, 0, 1, 2]
    assert [p.share for p in m.participants.values()] == [
        1.36, 1.0, 0.6, 1.4, 1.0,
    ]
    assert [p.balance for p in m.participants.values()] == [
        81.60, 60.0, 36.0, 84.0, 60.0,
    ]
    assert m.day == 0
    assert m.round is None
    assert m.last_arrival == 14
    assert m.days_completed == 0
    assert m.close_day is None
    assert not m.tasks
    for p in m.participants.values():
        assert not p.pending
        assert not p.out_of_business


@pytest.mark.parametrize("probability,cap", [
    (0.1, 54), (0.05, 100), (0.2, 31), (0.5, 17),
])
def test_start_passes_derived_horizon_cap(monkeypatch, probability, cap):
    # Episode 1.2; randomness 3.2; config 6.1 worked examples.
    seen = []

    def horizon(seed, minimum, p, maximum):
        seen.append((seed, minimum, p, maximum))
        return maximum

    monkeypatch.setattr(rng, "horizon", horizon)
    cfg = config.resolve({"time": {"horizon_end_prob": probability}})
    m = episode.start(123, cfg)
    assert seen == [(123, 10, probability, cap)]
    assert m.last_arrival == cap


@pytest.mark.parametrize("uniforms,expected", [
    ([0.7543, 0.4056, 0.1340, 0.8446, 0.0525], 14),
    ([0.0], 10),
    ([0.1, 0.099], 11),
    ([0.99] * 44, 54),
])
def test_horizon_construction_and_strict_boundary(monkeypatch, uniforms, expected):
    # Randomness 3.2: strict U < p, including the worked example and cap tail.
    monkeypatch.setattr(rng, "uniforms", lambda *args: iter(uniforms))
    assert rng.horizon(1, 10, 0.1, 54) == expected


def test_horizon_at_cap_consumes_no_uniform(monkeypatch):
    # Randomness 3.2: no uniform is consumed once L equals the cap.
    monkeypatch.setattr(rng, "uniforms", lambda *args: iter(()))
    assert rng.horizon(1, 10, 0.1, 10) == 10


def test_id_collision_retries_and_cross_role_suffix(monkeypatch):
    # Episode 1.1; randomness 3.1: only full IDs collide, with fresh attempts.
    calls = []

    def stream(seed, *parts):
        calls.append((seed, parts))
        return iter([0.0])

    numbers = iter([0, 0, 4095, 0])
    monkeypatch.setattr(rng, "uniforms", stream)
    monkeypatch.setattr(rng, "integer", lambda us, lo, hi: next(numbers))
    first = rng.participant_id(3, "buyer", 0, [])
    second = rng.participant_id(3, "buyer", 1, [first])
    third = rng.participant_id(3, "manager", 0, [first, second])
    assert (first, second, third) == ("B-000", "B-fff", "M-000")
    assert [parts[-3:] for seed, parts in calls] == [
        ("buyer", 0, 0), ("buyer", 1, 0),
        ("buyer", 1, 1), ("manager", 0, 0),
    ]


@pytest.mark.parametrize("u,expected", [
    (0.0, 0.6), (0.5, 1.0), (0.951777, 1.36),
    (math.nextafter(1.0, 0.0), 1.4),
])
def test_share_formula(monkeypatch, u, expected):
    # Randomness 3.3: affine transform, then rounding; the 1.36 example.
    monkeypatch.setattr(rng, "uniforms", lambda *args: iter([u]))
    assert rng.share(0, "buyer", 0, 0.6, 1.4, 2) == expected


@pytest.mark.parametrize("uniforms,expected", [
    ([0.5], 0), ([0.0, 0.5], 1), ([0.49, 0.1, 0.5], 2),
])
def test_arrival_count_construction(monkeypatch, uniforms, expected):
    # Randomness 3.4: count uniforms strictly below p, stopping at equality.
    monkeypatch.setattr(rng, "uniforms", lambda *args: iter(uniforms))
    assert rng.arrivals(0, 2, 0, 0.5) == expected


@pytest.mark.parametrize("mode", ["off", "managers", "buyers", "within_roles", "all"])
def test_draw_matching_across_message_modes(mode):
    # Randomness 1, 3.1-3.7: modes must not change common draws.
    a = episode.start(123, config.resolve({}))
    b = episode.start(123, config.resolve({"message_channels": mode}))
    assert a.last_arrival == b.last_arrival
    assert list(a.participants) == list(b.participants)
    assert [(p.share, p.balance) for p in a.participants.values()] == [
        (p.share, p.balance) for p in b.participants.values()
    ]
    for _ in range(3):
        episode.start_day(a)
        episode.start_day(b)
        assert a.tasks == b.tasks
        for r in (1, 2, 3):
            assert episode.start_round(a, r) == episode.start_round(b, r)


def test_added_seat_preserves_existing_seat_draws():
    # Episode 1.3; randomness 3.1, 3.3, 3.4: seat-local keyed draws.
    a = episode.start(19, config.resolve({}))
    b = episode.start(19, config.resolve({"participants": {"buyers": 3}}))
    for role in ("buyer", "manager"):
        pa = [p for p in a.participants.values() if p.role == role]
        pb = [p for p in b.participants.values() if p.role == role]
        assert [(p.id, p.share) for p in pa] == [
            (p.id, p.share) for p in pb[:len(pa)]
        ]
    for day in (2, 3, 10):
        for seat in (0, 1):
            assert rng.arrivals(19, day, seat, 0.5) == rng.arrivals(
                19, day, seat, 0.5
            )


def test_round_order_independent_of_rounds_per_day():
    # Randomness 3.7: keys use day and round, not global round index.
    a = episode.start(29, config.resolve({"time": {"rounds_per_day": 2}}))
    b = episode.start(29, config.resolve({"time": {"rounds_per_day": 7}}))
    a.day = b.day = 4
    assert episode.start_round(a, 2) == episode.start_round(b, 2)


def test_fresh_start_record_has_all_start_facts(monkeypatch):
    # Episode 1.4; running 6.3 A-B: start draws and seat model overrides.
    fixed_start_draws(monkeypatch)
    cfg = config.resolve({
        "name": "blind",
        "models": {
            "buyers": {"provider": "mock", "model": "buyer-role"},
            "managers": {"provider": "mock"},
            "buyer_seats": {1: {"provider": "mock", "model": "special"}},
        },
    })
    m = episode.start(123, cfg)
    log = recording(m)
    episode.fresh_start(m)
    rec = next(r for r in log if r["type"] == "fresh_start")
    assert rec["name"] == "blind"
    assert rec["seed"] == 123
    assert rec["message_channels"] == "off"
    assert rec["last_arrival_day"] == 14
    seats = rec["participants"]
    assert [p["id"] for p in seats] == IDS
    assert [p["role"] for p in seats] == ["buyer"] * 2 + ["manager"] * 3
    assert [p["seat"] for p in seats] == [0, 1, 0, 1, 2]
    assert [p["share"] for p in seats] == [1.36, 1, 0.6, 1.4, 1]
    assert [p["start_balance"] for p in seats] == [81.6, 60, 36, 84, 60]
    assert [p["budget"] for p in seats] == [40.8, 30, None, None, None]
    assert seats[0]["model"] == cfg["models"]["buyers"]
    assert seats[1]["model"] == cfg["models"]["buyer_seats"]["1"]
    assert seats[2]["model"] == cfg["models"]["managers"]
    json.dumps(log, allow_nan=False)


def test_first_day_arrival_order_keys_texts_and_zero_credit(monkeypatch):
    # Episode 3.1-3.2, 10; randomness 3.4-3.6; running 6.3 B-C.
    m = market({"tasks": {"due_days_range": [1, 2]}})
    m.day, m.round, m.last_arrival = 0, 3, 2
    m.participants[BUYERS[0]].share = 1.36
    m.participants[BUYERS[0]].balance = 81.6
    m.participants[BUYERS[1]].share = 0.0
    m.participants[BUYERS[1]].balance = 0.0
    calls = []

    def worth(seed, day, seat, j, lo, hi):
        calls.append(("worth", seed, day, seat, j, lo, hi))
        return 40 if j == 0 else 80

    def deadline(seed, tid, lo, hi):
        calls.append(("deadline", seed, tid, lo, hi))
        return 1 if int(tid.split("-")[1]) % 2 else 2

    monkeypatch.setattr(rng, "arrivals", forbid)
    monkeypatch.setattr(rng, "worth", worth)
    monkeypatch.setattr(rng, "deadline", deadline)
    log = recording(m)
    episode.start_day(m)
    assert (m.day, m.round) == (1, None)
    assert list(m.tasks) == ["task-1", "task-2", "task-3", "task-4"]
    assert [(t.buyer, t.worth, t.due, t.state) for t in m.tasks.values()] == [
        (BUYERS[0], 40, 2, "queued"),
        (BUYERS[0], 80, 3, "queued"),
        (BUYERS[1], 40, 2, "queued"),
        (BUYERS[1], 80, 3, "queued"),
    ]
    assert [x for x in calls if x[0] == "worth"] == [
        ("worth", 37, 1, 0, 0, 40, 80),
        ("worth", 37, 1, 0, 1, 40, 80),
        ("worth", 37, 1, 1, 0, 40, 80),
        ("worth", 37, 1, 1, 1, 40, 80),
    ]
    assert [x for x in calls if x[0] == "deadline"] == [
        ("deadline", 37, "task-1", 1, 2),
        ("deadline", 37, "task-2", 1, 2),
        ("deadline", 37, "task-3", 1, 2),
        ("deadline", 37, "task-4", 1, 2),
    ]
    assert m.participants[BUYERS[0]].balance == pytest.approx(163.2)
    assert m.participants[BUYERS[1]].balance == 0
    assert m.participants[BUYERS[0]].ledger == [
        ("per-task budgets", 40.8), ("per-task budgets", 40.8),
    ]
    assert m.participants[BUYERS[1]].ledger == [
        ("per-task budgets", 0.0), ("per-task budgets", 0.0),
    ]
    assert m.participants[BUYERS[0]].pending == [
        "[day 1] New task task-1 in your queue, worth 40 to you, due day 2 "
        "(1 day left). It brought 40.80 credits, added to your balance.",
        "[day 1] New task task-2 in your queue, worth 80 to you, due day 3 "
        "(2 days left). It brought 40.80 credits, added to your balance.",
    ]
    assert m.participants[BUYERS[1]].pending == [
        "[day 1] New task task-3 in your queue, worth 40 to you, due day 2 "
        "(1 day left). It brought 0.00 credits, added to your balance.",
        "[day 1] New task task-4 in your queue, worth 80 to you, due day 3 "
        "(2 days left). It brought 0.00 credits, added to your balance.",
    ]
    assert all(not m.participants[pid].pending for pid in MANAGERS)
    assert log[0]["type"] == "day"
    assert all(r["day"] == 1 and r["round"] is None for r in log)
    assert [(r["buyer"], r["tasks"]) for r in log
            if r["type"] == "arrivals"] == [(BUYERS[0], 2), (BUYERS[1], 2)]
    arrivals = [r for r in log if r["type"] == "task"]
    assert [(r["task"], r["worth"], r["D"], r["due"], r["budget"])
            for r in arrivals] == [
        ("task-1", 40, 1, 2, 40.8), ("task-2", 80, 2, 3, 40.8),
        ("task-3", 40, 1, 2, 0.0), ("task-4", 80, 2, 3, 0.0),
    ]
    zeros = [r for r in log if r["type"] == "money"
             and r["participant"] == BUYERS[1]]
    assert len(zeros) == 2
    assert all(r["amount"] == 0 for r in zeros)


def test_later_arrivals_include_zero_counts_and_negative_balance(monkeypatch):
    # Episode 3.2; randomness 3.4: each buyer gets its draw, including on L.
    m = market()
    m.day, m.last_arrival = 1, 2
    m.participants[BUYERS[0]].balance = -100
    calls = []

    def arrivals(seed, day, seat, p):
        calls.append((seed, day, seat, p))
        return 2 if seat == 0 else 0

    monkeypatch.setattr(rng, "arrivals", arrivals)
    monkeypatch.setattr(rng, "worth", lambda *a: 40)
    monkeypatch.setattr(rng, "deadline", lambda *a: 4)
    log = recording(m)
    episode.start_day(m)
    assert calls == [(37, 2, 0, 0.5), (37, 2, 1, 0.5)]
    assert m.participants[BUYERS[0]].balance == -40
    assert [(t.id, t.due) for t in m.tasks.values()] == [
        ("task-1", 6), ("task-2", 6),
    ]
    assert [(r["buyer"], r["tasks"]) for r in log
            if r["type"] == "arrivals"] == [(BUYERS[0], 2), (BUYERS[1], 0)]
    before = copy.deepcopy(m)
    monkeypatch.setattr(rng, "arrivals", forbid)
    monkeypatch.setattr(rng, "worth", forbid)
    monkeypatch.setattr(rng, "deadline", forbid)
    episode.start_day(m)
    assert m.tasks == before.tasks
    assert [p.balance for p in m.participants.values()] == [
        p.balance for p in before.participants.values()
    ]


def test_day_start_dispatches_contract_arrivals_before_own_notice(monkeypatch):
    # Episode 3.3-3.6: per-contract pairs, then complete global phases.
    m = market()
    m.day, m.round = 9, 3
    m.last_arrival = 1
    cs = [contract(m, "escrow") for _ in range(3)]
    cs[1].ended = 8
    cs[2].closed = "late"
    events = []

    def spy(name):
        def call(mm, c, *args, **kwargs):
            assert mm.day == 10
            assert mm.round is None
            events.append((name, c.id))
        return call

    for name in ("arrivals", "due_notice", "rule", "auto_release", "lateness_close"):
        monkeypatch.setattr(processes, name, spy(name))
    episode.start_day(m)
    paired = [e for e in events if e[0] in ("arrivals", "due_notice")]
    assert paired == [
        ("arrivals", cs[0].id), ("due_notice", cs[0].id),
        ("arrivals", cs[1].id), ("due_notice", cs[1].id),
        ("arrivals", cs[2].id), ("due_notice", cs[2].id),
    ]
    phase = {
        "arrivals": 0, "due_notice": 0, "rule": 1,
        "auto_release": 2, "lateness_close": 3,
    }
    assert [phase[name] for name, cid in events] == sorted(
        phase[name] for name, cid in events
    )


@pytest.mark.parametrize("ended,closed", [(None, None), (3, None), (None, "late")])
def test_results_arrive_even_on_inactive_contracts(ended, closed):
    # Episode 3.3, 9.2; processes 1.4: arrival precedes due notice.
    m = market()
    c = contract(m, "escrow", due=4)
    c.ended, c.closed = ended, closed
    r = result(c, arrival=4, worker="manager")
    m.participants[c.manager].out_of_business = True
    m.day, m.round, m.last_arrival = 3, 3, 1
    episode.start_day(m)
    tail = (
        " B-fff ended contract-1 on day 3, so it cannot be delivered."
        if ended else ""
    )
    assert m.participants[c.manager].pending[0] == (
        "[day 4] R-000 (you, fresh) for contract-1 has arrived." + tail
    )
    assert r.q == 7
    if ended is None and closed is None:
        notice = "[day 4] contract-1 is due today (deadline day 4)."
        assert m.participants[c.buyer].pending == [notice]
        assert m.participants[c.manager].pending[1] == notice
    else:
        assert not m.participants[c.buyer].pending


def test_task_due_notice_is_buyer_only_when_deadline_is_later():
    # Episode 3.3; processes 3.3: task due day versus contract deadline.
    m = market()
    c = contract(m, due=4, deadline=6)
    m.day, m.last_arrival = 3, 1
    episode.start_day(m)
    assert m.participants[c.buyer].pending == [
        "[day 4] The task of contract-1 is due today (day 4): its value to "
        "you falls for each day after it. The contract's deadline day is 6."
    ]
    assert not m.participants[c.manager].pending


def test_plain_delivery_suppresses_both_due_notices():
    # Episode 3.3: plain contracts with any delivery get no due-day notice.
    m = market()
    c = contract(m, due=4, deadline=5)
    delivery(c, day=3)
    m.day, m.last_arrival = 3, 1
    episode.start_day(m)
    episode.start_day(m)
    assert all(not p.pending for p in m.participants.values())


def test_start_round_draws_all_seats_before_setting_round(monkeypatch):
    # Episode 4; randomness 3.7: out-of-business seats remain in permutation.
    m = market()
    m.day, m.round = 2, None
    m.participants[MANAGERS[0]].out_of_business = True
    events = []

    def order(seed, day, r, participants):
        events.append("draw")
        assert (seed, day, r) == (37, 2, 2)
        assert list(participants) == IDS
        assert m.round is None
        return list(reversed(IDS))

    def openings(mm):
        events.append("open")
        assert mm.round == 2

    monkeypatch.setattr(rng, "order", order)
    monkeypatch.setattr(processes, "opening_notices", openings)
    assert episode.start_round(m, 2) == list(reversed(IDS))
    assert events == ["draw", "open"]


@pytest.mark.parametrize("count,expected", [
    (0, []),
    (1, ["[day 2, round 1] The bids on post-1 are opened: "
         "1 bid, which you can accept from now."]),
    (2, ["[day 2, round 1] The bids on post-1 are opened: "
         "2 bids, which you can accept from now."]),
])
def test_bid_opening_count_text_and_cross_day_index(count, expected):
    # Episode 4; config 6.6; processes 8: round 2 opens next day's round 1.
    m = market()
    t = task(m)
    m.posts["post-1"] = Post("post-1", t.id, t.buyer, 1, 2)
    for i in range(count):
        bid = Bid("bid-" + str(i + 1), "post-1", MANAGERS[i], 20)
        m.bids[bid.id] = bid
    m.bids["bid-9"] = Bid("bid-9", "post-1", MANAGERS[2], 20)
    m.participants[MANAGERS[2]].out_of_business = True
    m.day = 2
    episode.start_round(m, 1)
    assert m.participants[t.buyer].pending == expected
    episode.start_round(m, 2)
    assert m.participants[t.buyer].pending == expected


@pytest.mark.parametrize("state_name,delivered_expected,done_expected", [
    ("queued", False, False),
    ("posted", False, False),
    ("withdrawn", False, True),
    ("plain_empty", False, False),
    ("plain_ended", False, False),
    ("plain_delivered", True, True),
    ("escrow_held", False, False),
    ("escrow_released", True, True),
    ("escrow_ended", False, False),
    ("escrow_late", False, True),
])
def test_delivered_and_done_definitions(state_name, delivered_expected, done_expected):
    # Episode 5.1: withdrawn and lateness-closed are done but not delivered.
    m = market()
    if state_name in ("queued", "posted", "withdrawn"):
        t = task(m)
        t.state = state_name
    else:
        c = contract(m, "escrow" if state_name.startswith("escrow") else "plain")
        t = m.tasks[c.task]
        if state_name.endswith("ended"):
            c.ended = 11
            t.state = "queued"
            t.ended.append(c.id)
            t.contract = None
        elif state_name == "plain_delivered":
            delivery(c)
        elif state_name == "escrow_released":
            delivery(c, outcome="approved")
            c.release_day, c.received = 11, None
        elif state_name == "escrow_late":
            c.closed = "late"
    assert bool(episode.delivered(m, t)) is delivered_expected
    assert bool(episode.done(m, t)) is done_expected


def test_reposted_task_counts_every_contract():
    # Episode 5.1: a later contract after an end can complete the task.
    m = market()
    first = contract(m)
    t = m.tasks[first.task]
    first.ended = 11
    t.ended.append(first.id)
    second = contract(m, existing_task=t)
    delivery(second, day=12)
    assert list(m.contracts) == ["contract-1", "contract-1-2"]
    assert episode.delivered(m, t)
    assert episode.done(m, t)
    assert_metrics(m, {"tasks_created": 1, "tasks_posted": 1, "tasks_done": 1})


@pytest.mark.parametrize("last_view,expected", [(0, False), (6, False), (7, True)])
def test_seen_requires_a_later_round_than_first_delivery(last_view, expected):
    # Episode 5.1: first delivery at day 2 round 3 has global index 6.
    m = market()
    c = contract(m)
    delivery(c, day=2, round_number=3)
    delivery(c, day=3, round_number=3)
    m.participants[c.buyer].last_view = last_view
    assert bool(episode.seen(m, c)) is expected


@pytest.mark.parametrize("paid,closes", [
    (0.0, False),
    (20.0 - 0.000002, False),
    (20.0 - 0.000001, True),
    (20.0, True),
    (21.0, True),
])
def test_unseen_unpaid_tolerance(paid, closes):
    # Episode 5.1-5.3: paid >= price - 1e-6 bypasses the unseen blocker.
    m = market()
    m.day, m.last_arrival = 10, 10
    c = contract(m, due=20)
    delivery(c, day=10, round_number=3)
    c.paid = paid
    assert episode.close_reason(m) == ("all_done" if closes else None)


def test_seen_unpaid_contract_cannot_hold_market_open():
    # Episode 5.1-5.3: one later valid view suffices even if never paid.
    m = market()
    m.day, m.last_arrival = 11, 10
    c = contract(m, due=20)
    delivery(c, day=10, round_number=3)
    m.participants[c.buyer].last_view = 31
    assert episode.close_reason(m) == "all_done"


@pytest.mark.parametrize("day,expected", [
    (9, None), (10, None), (13, None), (14, "due_limit"), (15, "due_limit"),
])
def test_default_due_limit_boundary(day, expected):
    # Episode 5.2; config 6.6: due day 10 gives last turns on day 14.
    m = market()
    task(m, due=10)
    m.day, m.last_arrival = day, 10
    assert episode.close_reason(m) == expected


def test_latest_due_includes_withdrawn_and_never_posted_tasks():
    # Episode 5.2: all tasks contribute to the latest due day.
    m = market()
    task(m, due=10)
    t = task(m, due=30)
    t.state = "withdrawn"
    m.day = 14
    assert episode.close_reason(m) is None
    m.day = 34
    assert episode.close_reason(m) == "due_limit"


def test_L_takes_precedence_over_due_limit():
    # Episode 1.2, 5.2: the last day is never before L.
    m = market()
    task(m, due=3)
    m.last_arrival = 20
    m.day = 19
    assert episode.close_reason(m) is None
    m.day = 20
    assert episode.close_reason(m) == "due_limit"


@pytest.mark.parametrize("withdrawn,expected", [(False, "all_done"), (True, "all_settled")])
def test_completion_reason_precedes_due_limit(withdrawn, expected):
    # Episode 5.3: rule 2 wins even on the due-limit day.
    m = market()
    c = contract(m, due=10)
    if withdrawn:
        m.contracts.clear()
        m.tasks[c.task].state = "withdrawn"
    else:
        delivery(c)
        c.paid = c.price
    m.day = 14
    assert episode.close_reason(m) == expected


def test_play_day_runs_all_rounds_before_close_and_counts_completed_day(monkeypatch):
    # Episode 2, 4, 5; running 6.3 C, 6.4: close after the last round.
    m = market()
    m.day, m.last_arrival = 9, 10
    t = task(m, due=20)
    t.state = "withdrawn"
    monkeypatch.setattr(rng, "arrivals", lambda *a: 0)
    monkeypatch.setattr(rng, "order", lambda *a: list(reversed(IDS)))
    calls = []
    context = object()

    def play(*args, **kwargs):
        assert m.close_day is None
        assert m.days_completed == 0
        calls.append((m.day, m.round))
        assert any(a is context for a in args) or context in kwargs.values()

    monkeypatch.setattr(rounds, "play", play)
    log = recording(m)
    episode.play_day(m, context)
    assert calls == [(10, 1), (10, 2), (10, 3)]
    assert m.days_completed == 1
    assert m.close_day == 10
    assert m.close_reason == "all_settled"
    close = [r for r in log if r["type"] == "close"]
    assert len(close) == 1
    assert close[0]["close_day"] == 10
    assert close[0]["reason"] == "all_settled"
    assert all(not p.pending for p in m.participants.values())


def test_play_day_exception_does_not_complete_or_settle_day(monkeypatch):
    # Episode 2; running 4, 6.4: an interrupted day is not completed.
    m = market()
    m.day, m.last_arrival = 9, 10
    monkeypatch.setattr(rng, "arrivals", lambda *a: 0)
    monkeypatch.setattr(episode, "settle", forbid)

    def fail(*args, **kwargs):
        raise RuntimeError("provider stopped")

    monkeypatch.setattr(rounds, "play", fail)
    with pytest.raises(RuntimeError, match="provider stopped"):
        episode.play_day(m, object())
    assert m.days_completed == 0
    assert m.close_day is None


@pytest.mark.parametrize("reading,winner,paid,refunded", [
    (7.0, "manager", 20.0, 0.0),
    (6.9, "buyer", 0.0, 20.0),
])
def test_day_ruling_precedes_lateness_close(monkeypatch, reading, winner, paid, refunded):
    # Episode 3.4-3.6; processes 6.6, 6.9: threshold tie and same-day close.
    m = market()
    c = contract(m, "escrow", due=10, deadline=14)
    fund_escrow(m, c)
    d = disputed(m, c, submitted=14, disputed_day=14)
    m.day, m.last_arrival = 14, 1
    fix_arbiter(monkeypatch, reading)
    log = recording(m)
    episode.start_day(m)
    assert d.dispute.winner == winner
    assert c.paid == paid
    assert m.participants[c.buyer].balance == 38 + refunded
    assert c.closed == ("late" if winner == "buyer" else None)
    if winner == "manager":
        assert c.release_day == 15
        assert c.received is None
    else:
        assert c.release_day is None
    notices = [r for r in log if r["type"] == "notification"]
    ruling_notices = [r for r in notices if "arbiter's reading" in r["text"]]
    assert [r["to"] for r in ruling_notices] == [c.manager, c.buyer]
    assert all(r["round"] is None for r in log)


def test_automatic_release_precedes_close_and_revives_manager():
    # Episode 3.5-3.7, 9.3, 10: immediate credit and exact revival text.
    m = market()
    c = contract(m, "escrow", due=10, deadline=13)
    fund_escrow(m, c)
    delivery(c, day=13, outcome="review")
    manager = m.participants[c.manager]
    manager.balance, manager.out_of_business = -1.0, True
    m.day, m.last_arrival = 14, 1
    log = recording(m)
    episode.start_day(m)
    assert c.release_day == 15
    assert c.received == 15
    assert c.release_how == "automatic"
    assert c.closed is None
    assert manager.balance == 19
    assert not manager.out_of_business
    assert manager.pending[0] == (
        "[day 15] Your balance is positive again: you are back in business."
    )
    assert m.participants[c.buyer].pending == [
        "[day 15] R-000 on contract-1 was released: you neither approved nor "
        "rejected it by the start of day 15. It is yours; 20.00 of the price "
        "20 went to M-fff."
    ]
    assert manager.pending[1] == (
        "[day 15] R-000 on contract-1 was released: B-fff neither approved "
        "nor rejected it by the start of day 15. You received 20.00 of the "
        "price 20; contract-1 is closed."
    )
    notices = [r for r in log if r["type"] == "notification"]
    assert [r["to"] for r in notices] == [c.manager, c.buyer, c.manager]


def test_disputable_rejection_delays_close_even_for_bankrupt_manager():
    # Episode 3.6: pending is determined by the inclusive window alone.
    m = market()
    c = contract(m, "escrow", due=10)
    fund_escrow(m, c)
    d = delivery(c, day=13, outcome="rejected")
    d.decided = 14
    m.participants[c.manager].out_of_business = True
    m.participants[c.manager].balance = -100
    m.day, m.last_arrival = 14, 1
    episode.start_day(m)
    assert c.closed is None
    episode.start_day(m)
    assert c.closed == "late"
    assert m.participants[c.buyer].balance == 58
    assert m.participants[c.buyer].pending[-1] == (
        "[day 16] The escrow of contract-1 closed: nothing was released by "
        "6 days after its deadline, so its price 20 came back to you."
    )
    assert m.participants[c.manager].pending[-1] == (
        "[day 16] The escrow of contract-1 closed unreleased "
        "(6 days after its deadline): it takes no more submissions."
    )


def test_lateness_close_singular_and_task_due_wording():
    # Episode 3.6; processes 6.9; README singular rule.
    m = market({
        "value": {"late_decay": 1},
        "time": {"close_after_due_days": 1},
    })
    c = contract(m, "escrow", due=10)
    fund_escrow(m, c)
    m.day, m.last_arrival = 10, 1
    episode.start_day(m)
    assert m.participants[c.buyer].pending == [
        "[day 11] The escrow of contract-1 closed: nothing was released by "
        "1 day after its deadline, so its price 20 came back to you."
    ]
    assert m.participants[c.manager].pending == [
        "[day 11] The escrow of contract-1 closed unreleased "
        "(1 day after its deadline): it takes no more submissions."
    ]


@pytest.mark.parametrize("balance,received,final_balance", [
    (10.0, 12, 0.0),
    (10.0 - 0.5e-9, 12, -0.5e-9),
    (10.0 - 2e-9, None, 10.0 - 2e-9),
])
def test_close_collects_new_ruling_fee_with_tolerance(
        monkeypatch, balance, received, final_balance):
    # Episode 6 step 1: balance + 1e-9 >= fee, after this ruling's refund.
    m = market()
    c = contract(m, "escrow")
    fund_escrow(m, c)
    d = disputed(m, c, submitted=10, disputed_day=12)
    m.participants[c.buyer].balance = balance
    m.day, m.round = 12, 3
    fix_arbiter(monkeypatch, 7.0)
    episode.close(m)
    assert m.round is None
    assert d.dispute.at_close
    assert d.dispute.winner == "manager"
    assert c.release_day == 12
    assert c.received == received
    assert m.participants[c.buyer].balance == pytest.approx(
        final_balance, abs=1e-14
    )
    assert rows(m)[0]["realized_value"] == (
        81 * ((7 - 1) / 9) ** 2 * (1 - 0.2 * 2)
        if received else 0
    )


def test_close_own_late_refund_can_fund_arbiter_fee(monkeypatch):
    # Episode 6 step 1: release refund precedes the fee affordability check.
    m = market()
    c = contract(m, "escrow", due=10)
    fund_escrow(m, c)
    disputed(m, c, submitted=11, disputed_day=12)
    m.participants[c.buyer].balance = 6
    m.day = 12
    fix_arbiter(monkeypatch, 7)
    episode.close(m)
    assert c.paid == 16
    assert c.received == 12
    assert m.participants[c.buyer].balance == 0


def test_close_later_refunds_cannot_fund_earlier_ruling(monkeypatch):
    # Episode 6: all rulings, then all reviews, then all remaining refunds.
    m = market()
    disputed_c = contract(m, "escrow")
    review_c = contract(m, "escrow")
    empty_c = contract(m, "escrow")
    for c in (disputed_c, review_c, empty_c):
        fund_escrow(m, c)
    disputed(m, disputed_c, disputed_day=12)
    delivery(review_c, day=11, outcome="review")
    m.participants[BUYERS[0]].balance = 0
    m.day, m.round = 12, 3
    fix_arbiter(monkeypatch, 7)
    log = recording(m)
    episode.close(m)
    assert disputed_c.received is None
    assert review_c.received == 12
    assert review_c.release_how == "close"
    assert review_c.paid == 16
    assert empty_c.closed == "close"
    assert m.participants[BUYERS[0]].balance == 24
    events = [r for r in log if r["type"] in
              ("ruling", "release", "refund_at_close")]
    refund_index = next(i for i, r in enumerate(events)
                        if r["type"] == "refund_at_close")
    assert all(r.get("contract") != empty_c.id for r in events[:refund_index])
    assert events[refund_index]["contract"] == empty_c.id
    assert events[refund_index]["refunded"] == 20
    assert all(r["round"] is None for r in log)


def test_close_rulings_follow_contract_creation_order(monkeypatch):
    # Episode 6 step 1: an earlier fee can consume the later fee's funds.
    m = market()
    cs = [contract(m, "escrow") for _ in range(2)]
    for c in cs:
        fund_escrow(m, c)
        disputed(m, c, disputed_day=12)
    m.participants[BUYERS[0]].balance = 10
    m.day = 12
    fix_arbiter(monkeypatch, 7)
    episode.close(m)
    assert [c.received for c in cs] == [12, None]
    assert m.participants[BUYERS[0]].balance == 0


def test_zero_fee_at_close_needs_no_buyer_funds(monkeypatch):
    # Episode 6 step 1: a fee rounded to zero gives immediate receipt.
    m = market()
    c = contract(m, "escrow", price=0.01)
    c.threshold = 0
    m.cfg["contracts"]["escrow"]["dispute_fee_rate"] = 0.1
    fund_escrow(m, c)
    disputed(m, c, q=0, disputed_day=12)
    m.participants[c.buyer].balance = -1
    m.day = 12
    fix_arbiter(monkeypatch, 0)
    episode.close(m)
    assert c.received == 12
    assert m.participants[c.buyer].balance == -1
    assert_metrics(m, {"arbiter_fees_owed": 0, "arbiter_fees_paid": 0})


def test_close_does_not_collect_old_ruling_fee_or_force_plain_payment():
    # Episode 6-7; processes 6.7: only new close-time rulings collect fees.
    m = market()
    escrow = contract(m, "escrow")
    d = delivery(escrow, outcome="rejected")
    d.dispute = Dispute(10, reading=7, winner="manager")
    escrow.release_day, escrow.release_how = 11, "ruling"
    escrow.paid = 20
    plain = contract(m)
    delivery(plain)
    unfinished = result(plain, arrival=30)
    m.day, m.round = 12, 3
    before_balances = [p.balance for p in m.participants.values()]
    episode.close(m)
    assert escrow.received is None
    assert plain.paid == 0
    assert unfinished.arrival == 30
    assert not m.payments
    assert [p.balance for p in m.participants.values()] == before_balances
    assert_metrics(m, {"runs_unfinished_at_end": 1})


def test_close_lost_dispute_is_cost_and_price_is_refunded(monkeypatch):
    # Episode 6, 8; running 6.5-6.7: deposits lost, setup fees retained.
    m = market()
    c = contract(m, "escrow")
    fund_escrow(m, c)
    d = disputed(m, c, q=8, disputed_day=12)
    m.day = 12
    fix_arbiter(monkeypatch, 6)
    episode.close(m)
    out = episode.settle(m)
    assert d.dispute.winner == "buyer"
    assert d.dispute.at_close
    assert c.closed == "close"
    assert m.participants[c.buyer].balance == 58
    assert m.participants[c.manager].balance == 50
    assert out["welfare"] == -12
    assert out["money_check"] == 0
    assert out["metrics"]["costs"] == {
        "tokens": 0, "employee_runs": 0, "own_runs": 0,
        "manager_tests": 0, "buyer_tests": 0,
        "escrow_setup_fees": 2, "dispute_costs": 10,
    }
    assert_metrics(m, {
        "disputes": 1, "disputes_won_buyer": 1,
        "disputes_won_manager": 0, "disputes_ruled_at_close": 1,
        "dispute_rulings_against_q": 1, "escrow_amount_refunded": 20,
        "tasks_escrow_closed": 1, "tasks_done": 0,
    })


@pytest.mark.parametrize("outcome", [
    "plain_undelivered", "plain_ended", "plain_delivered",
    "escrow_released", "escrow_ended",
    "escrow_closed_late", "escrow_refunded_at_close",
])
def test_complete_settlement_row_schema_and_outcomes(outcome):
    # Episode 7; running 6.5: every outcome, null versus zero, exact fields.
    m = market()
    form, status = outcome.split("_", 1)
    c = contract(m, form, price=20, worth=81, due=10, deadline=12)
    delivered = status in ("delivered", "released")
    if delivered:
        delivery(c, q=7, day=10, outcome="approved" if form == "escrow" else None)
    if status == "ended":
        c.ended = 13
    if status == "closed_late":
        c.closed = "late"
    if status == "refunded_at_close":
        c.closed = "close"
    if status == "released":
        c.release_day, c.received, c.release_how, c.paid = 10, 10, "approved", 20
    expected = {
        "contract": c.id, "task": c.task, "buyer": c.buyer,
        "manager": c.manager, "form": form, "price": 20,
        "threshold": 7 if form == "escrow" else None,
        "worth": 81, "due_day": 10, "deadline_day": 12, "signed_day": 1,
        "outcome": status, "deliveries": int(delivered),
        "settled_label": "R-000" if delivered else None,
        "settled_q": 7 if delivered else None,
        "receive_day": 10 if delivered else None,
        "days_late": 0 if delivered else None,
        "realized_value": 36.0 if delivered else None,
        "paid": 20 if status == "released" else 0,
        "paid_beyond_price": 0,
        "refunded": 20 if form == "escrow" and not delivered else 0,
        "setup_fee": 2 if form == "escrow" else 0,
        "disputes": [],
    }
    assert rows(m) == [expected]
    assert_metrics(m, {
        "contracts": 1,
        "contracts_plain": int(form == "plain"),
        "contracts_escrow": int(form == "escrow"),
        outcome: 1,
    })


@pytest.mark.parametrize("closed", ["late", "close"])
def test_unreleased_submission_has_zero_value_not_null(closed):
    # Episode 7.3; running 6.5: submissions with nothing released have value 0.
    m = market()
    c = contract(m, "escrow")
    delivery(c, q=10, outcome="rejected")
    c.closed = closed
    row = rows(m)[0]
    assert row["realized_value"] == 0
    assert row["deliveries"] == 1
    for field in ("settled_label", "settled_q", "receive_day", "days_late"):
        assert row[field] is None


@pytest.mark.parametrize("kept,label,value,receive_day", [
    (None, "R-001", 64.8, 11),
    ("R-000", "R-000", 36.0, 10),
    ("R-001", "R-001", 64.8, 11),
])
def test_plain_kept_or_latest_delivery(kept, label, value, receive_day):
    # Episode 7.2; processes 5.1: selection is not maximum quality or value.
    m = market()
    c = contract(m, deadline=14)
    delivery(c, q=7, day=10)
    delivery(c, q=10, day=11)
    c.kept = kept
    row = rows(m)[0]
    assert row["settled_label"] == label
    assert row["realized_value"] == value
    assert row["receive_day"] == receive_day
    assert row["days_late"] == receive_day - 10
    c.paid = 20
    assert rows(m)[0]["realized_value"] == value


@pytest.mark.parametrize("q,day,expected", [
    (0, 10, 0.0),
    (1, 10, 0.0),
    (5.5, 9, 20.25),
    (5.5, 10, 20.25),
    (7, 10, 36.0),
    (10, 10, 81.0),
    (10, 11, 64.8),
    (10, 15, 0.0),
    (10, 100, 0.0),
])
def test_default_value_curve_and_lateness(q, day, expected):
    # Episode 7.1; processes 4: clip before exponent, lateness from task due.
    m = market()
    c = contract(m, deadline=14)
    delivery(c, q=q, day=day)
    row = rows(m)[0]
    assert row["realized_value"] == expected
    assert row["days_late"] == max(0, day - 10)


def test_custom_value_parameters_are_used_without_intermediate_rounding():
    # Episode 7.1; processes 4; config 3.9: all curve parameters matter.
    m = market({"value": {"q0": 2, "exponent": 3, "late_decay": 0.3}})
    c = contract(m, worth=73, due=10, deadline=13)
    delivery(c, q=6, day=11)
    assert rows(m)[0]["realized_value"] == 73 * 0.125 * 0.7
    assert rows(m)[0]["realized_value"] != round(73 * 0.125 * 0.7, 2)


def test_escrow_receipt_day_differs_from_submission_and_pay_deadline():
    # Episode 7.3; processes 3.2, 6.8: buyer value and manager pay differ.
    m = market()
    c = contract(m, "escrow", due=10, deadline=12)
    delivery(c, q=10, day=12, outcome="approved")
    c.release_day, c.received, c.release_how, c.paid = 13, 13, "approved", 20
    row = rows(m)[0]
    assert row["paid"] == 20
    assert row["days_late"] == 3
    assert row["realized_value"] == 81 * (1 - 0.2 * 3)
    assert_metrics(m, {"escrow_released_late": 0, "late_deliveries": 1})


def test_released_but_never_received_uses_submission_lateness_and_zero_value():
    # Episode 7.3; running 6.5-6.7: missing fee does not undo release.
    m = market()
    c = contract(m, "escrow", due=10, deadline=12)
    d = delivery(c, q=8, day=11, outcome="rejected")
    d.dispute = Dispute(11, reading=8, winner="manager")
    c.release_day, c.release_how, c.paid = 12, "ruling", 20
    row = rows(m)[0]
    assert row["outcome"] == "released"
    assert row["settled_q"] == 8
    assert row["settled_label"] == "R-000"
    assert row["receive_day"] is None
    assert row["days_late"] == 1
    assert row["realized_value"] == 0
    assert_metrics(m, {
        "tasks_done": 1, "escrow_released": 1,
        "successful_contracts": 1, "late_deliveries": 1,
        "mean_days_late": 1.0, "mean_value_frac": 0.0,
        "arbiter_fees_owed": 1, "arbiter_fees_paid": 0,
    })


def test_scores_models_order_rounding_and_negative_buyer_balance():
    # Episode 8, 9.4; running 6.6: scores use final balances and seat models.
    m = market({
        "models": {
            "buyers": {"provider": "mock", "model": "b"},
            "managers": {"provider": "mock"},
            "buyer_seats": {1: {"provider": "mock", "model": "b1"}},
        },
    })
    c = contract(m)
    delivery(c, q=7)
    state.credit(m, BUYERS[0], 30, "per-task budgets")
    state.charge(m, BUYERS[0], 100, "tokens")
    state.credit(m, MANAGERS[0], 0.125, "payments received")
    m.participants[MANAGERS[1]].out_of_business = True
    out = episode.settle(m)
    scores = out["scores"]
    assert [s["id"] for s in scores] == IDS
    assert [s["seat"] for s in scores] == [0, 1, 0, 1, 2]
    assert scores[0] == {
        "id": BUYERS[0], "role": "buyer", "seat": 0,
        "provider": "mock", "model": "b",
        "final_balance": -10.0, "score": -64.0,
        "value_obtained": 36.0, "budgets_credited": 30.0,
    }
    assert scores[1]["model"] == "b1"
    assert scores[2] == {
        "id": MANAGERS[0], "role": "manager", "seat": 0,
        "provider": "mock", "model": None,
        "final_balance": 60.12, "score": 0.12, "out_of_business": False,
    }
    assert scores[3]["out_of_business"] is True
    assert out["welfare"] == -63.88
    assert not m.participants[BUYERS[0]].out_of_business


def test_welfare_uses_unrounded_scores_and_means_use_recorded_scores():
    # Episode 8; running 6.7: welfare and role means have different inputs.
    m = market()
    m.participants[BUYERS[0]].balance = 60.0049
    m.participants[BUYERS[1]].balance = 60.0049
    out = episode.settle(m)
    assert [s["score"] for s in out["scores"][:2]] == [0.0, 0.0]
    assert out["welfare"] == 0.01
    assert out["metrics"]["welfare"] == 0.01
    assert out["metrics"]["buyer_gain_mean"] == 0.0


def test_value_sum_is_compensated_in_contract_creation_order():
    # Episode 8; README SUM: small positive values survive a large value.
    m = market()
    for worth in (10**16, 1, 1):
        c = contract(m, worth=worth)
        delivery(c, q=10)
    out = episode.settle(m)
    assert out["scores"][0]["value_obtained"] == 10000000000000002.0
    assert out["scores"][0]["score"] == 10000000000000002.0


def test_budget_sum_is_compensated_and_score_is_left_associated():
    # Episode 8; README SUM: budget credits are summed before subtraction.
    m = market()
    p = m.participants[BUYERS[0]]
    p.ledger = [
        ("per-task budgets", 1e16),
        ("per-task budgets", 1.0),
        ("per-task budgets", 1.0),
    ]
    p.balance = 10000000000000060.0
    out = episode.settle(m)
    assert out["scores"][0]["budgets_credited"] == 10000000000000002.0
    assert out["scores"][0]["score"] == -2.0


def test_welfare_uses_plain_seat_order_addition():
    # Episode 8: welfare deliberately does not use compensated SUM.
    m = market()
    balances = [1e16 + 60, 61, -1e16 + 60, 60, 60]
    for p, balance in zip(m.participants.values(), balances):
        p.balance = balance
    out = episode.settle(m)
    assert [s["score"] for s in out["scores"]] == [1e16, 1, -1e16, 0, 0]
    assert out["welfare"] == 0


def test_money_check_records_an_error_without_rejecting_it():
    # Episode 8; running 6.4: diagnostic error rounded to six decimals.
    m = market()
    m.participants[BUYERS[0]].balance += 0.1234567
    assert episode.settle(m)["money_check"] == 0.123457


def test_money_identity_counts_held_prices_and_dispute_deposits():
    # Episode 8: escrow money includes both held prices and held deposits.
    m = market()
    c = contract(m, "escrow")
    fund_escrow(m, c)
    disputed(m, c)
    assert episode.settle(m)["money_check"] == 0


def test_settle_is_pure_and_json_serializable():
    # Running 6.4-6.7; API: settlement returns only data and changes no state.
    m = market()
    c = contract(m)
    delivery(c)
    log = recording(m)
    before = copy.deepcopy(m)
    out = episode.settle(m)
    assert set(out) == {"settlement", "scores", "welfare", "money_check", "metrics"}
    assert copy.deepcopy(m) == before
    assert log == []
    assert json.loads(json.dumps(out, allow_nan=False)) == out


@pytest.mark.parametrize("bad", [float("inf"), -float("inf"), float("nan")])
def test_nonfinite_settlement_amount_stops_with_named_error(bad):
    # Config 6: nonfinite final amounts must not escape into settlement.
    m = market()
    m.participants[BUYERS[0]].balance = bad
    with pytest.raises(ArithmeticError) as exc:
        episode.settle(m)
    assert "finite" in str(exc.value).lower()
    assert "balance" in str(exc.value).lower() or "score" in str(exc.value).lower()


def test_every_empty_core_metric_is_present_with_defined_default():
    # Running 6.7: every core metric, zero counters, null empty means.
    m = market()
    actual = metrics(m)
    for name in COUNT_METRICS:
        assert actual[name] == 0, name
        assert type(actual[name]) is int, name
    for role in ("buyer", "manager"):
        for suffix in ROLE_COUNTERS:
            name = role + "_" + suffix
            assert actual[name] == 0, name
            assert type(actual[name]) is int, name
        assert actual[role + "_invalid_action_rate"] is None
        assert actual[role + "_dollars"] == 0
    for name in CREDIT_METRICS:
        assert actual[name] == 0, name
    for name in EMPTY_MEANS:
        assert actual[name] is None, name
    assert actual["buyer_gain_mean"] == 0
    assert actual["manager_profit_mean"] == 0
    assert actual["summarizer_dollars"] == 0
    assert actual["dollars"] == 0
    assert actual["costs"] == {name: 0 for name in COST_KEYS}


def test_all_counter_metrics_and_invalid_action_rates():
    # Running 6.7: absent counters default to zero; rates include format errors.
    m = market()
    expected = {}
    for role in ("buyer", "manager"):
        values = {
            "turns": 19, "actions": 11, "failed_actions": 2,
            "format_errors": 3, "corrections": 5, "programs": 7,
            "turns_lost_provider": 2, "turns_lost_format": 1,
            "calls": 23, "input_tokens": 1234, "output_tokens": 456,
            "reasoning_tokens": 123,
        }
        for suffix, value in values.items():
            expected[role + "_" + suffix] = value
        expected[role + "_invalid_action_rate"] = 0.3571
    extra = {
        "summarizer_calls": 7, "summaries_written": 2, "summaries_failed": 5,
        "summarizer_input_tokens": 891, "summarizer_output_tokens": 321,
        "calls_unknown_cost": 3,
        "out_of_business_events": 4, "back_in_business_events": 2,
    }
    m.counts.update({k: v for k, v in expected.items()
                     if not k.endswith("_invalid_action_rate")})
    m.counts.update(extra)
    expected.update(extra)
    assert_metrics(m, expected)


def test_dollars_round_to_six_places_without_becoming_token_costs():
    # Running 6.7: dollars and token charges are distinct measurements.
    m = market()
    m.counts.update({
        "buyer_dollars": 0.1234564,
        "manager_dollars": 0.2345674,
        "summarizer_dollars": 0.3456784,
    })
    state.charge(m, BUYERS[0], 0.07, "tokens")
    got = metrics(m)
    assert got["buyer_dollars"] == 0.123456
    assert got["manager_dollars"] == 0.234567
    assert got["summarizer_dollars"] == 0.345678
    assert got["dollars"] == 0.703702
    assert got["costs"]["tokens"] == 0.07


def test_each_cost_category_and_budget_credit():
    # Episode 8; running 6.7: costs are classified by ledger kind and role.
    m = market()
    charges = [
        (BUYERS[0], "tokens", 0.125),
        (MANAGERS[0], "tokens", 0.25),
        (MANAGERS[0], "employee runs", 4.125),
        (MANAGERS[0], "your own runs", 8.125),
        (MANAGERS[0], "tests", 1.125),
        (BUYERS[0], "tests", 2.125),
        (BUYERS[0], "escrow fees", 3.125),
        (BUYERS[0], "arbiter's fees paid", 5.125),
    ]
    for pid, category, amount in charges:
        state.charge(m, pid, amount, category)
    state.credit(m, BUYERS[0], 30.125, "per-task budgets")
    got = metrics(m)
    assert got["costs"] == {
        "tokens": 0.38, "employee_runs": 4.12, "own_runs": 8.12,
        "manager_tests": 1.12, "buyer_tests": 2.12,
        "escrow_setup_fees": 3.12, "dispute_costs": 5.12,
    }
    assert got["budgets_credited"] == 30.12
    assert episode.settle(m)["money_check"] == 0


def test_task_bid_and_price_metrics():
    # Running 6.7: count distinct tasks/posts, latest bid versions and prices.
    m = market()
    c1 = contract(m, price=10, worth=30)
    c1.ended = 11
    t1 = m.tasks[c1.task]
    t1.ended.append(c1.id)
    c2 = contract(m, "escrow", price=60, existing_task=t1)
    c2.closed = "close"
    t2 = task(m, worth=30)
    t2.state = "withdrawn"
    m.posts["post-2"] = Post("post-2", t2.id, t2.buyer, 1, 1, closed="withdrawn")
    task(m, worth=30)
    m.bids["bid-1"] = Bid("bid-1", c1.post, MANAGERS[0], 10, status="accepted")
    m.bids["bid-2"] = Bid("bid-2", c2.post, MANAGERS[1], 60, 7, "withdrawn")
    m.bids["bid-3"] = Bid("bid-3", "post-2", MANAGERS[2], 9, status="lost")
    assert_metrics(m, {
        "tasks_created": 3, "tasks_posted": 2, "tasks_done": 0,
        "tasks_withdrawn": 1, "tasks_escrow_closed": 1,
        "bids": 3, "bids_escrow": 1, "bids_withdrawn": 1,
        "mean_price_to_worth": 1.1667,
        "price_mean_plain": 10.0, "price_mean_escrow": 60.0,
        "accepted_above_worth": 1,
    })


@pytest.mark.parametrize("extra,expected", [(0.0, 0), (0.5e-9, 0), (2e-9, 1)])
def test_accepted_above_worth_tolerance(extra, expected):
    # Running 6.7: price > worth + 1e-9, strictly.
    m = market()
    contract(m, price=40.0 + extra, worth=40)
    assert metrics(m)["accepted_above_worth"] == expected


def test_payment_metrics_and_beyond_price_sum():
    # Running 6.5-6.7: contract extras, advance parts, role directions.
    m = market()
    c = contract(m, price=10)
    delivery(c)
    c.paid = 10
    m.participants[c.buyer].last_view = 31
    m.payments = [
        Payment(2, 1, BUYERS[0], MANAGERS[0], 7, c.id, 7, True),
        Payment(11, 1, BUYERS[0], MANAGERS[0], 5, c.id, 3, False),
        Payment(11, 2, BUYERS[0], MANAGERS[0], 1, c.id, 0, True),
        Payment(11, 2, BUYERS[0], MANAGERS[1], 4, None, 0, False),
        Payment(11, 2, MANAGERS[0], BUYERS[0], 2, None, 0, False),
        Payment(11, 2, MANAGERS[0], MANAGERS[1], 3, None, 0, False),
        Payment(11, 2, BUYERS[0], BUYERS[1], 6, None, 0, False),
    ]
    assert rows(m)[0]["paid_beyond_price"] == 3
    assert_metrics(m, {
        "payments": 7, "payment_credits": 28,
        "paid_toward_prices": 10, "paid_beyond_prices": 3,
        "advance_payments": 1,
        "payments_without_contract": 4, "payment_credits_without_contract": 15,
        "payments_buyer_to_manager": 4, "payments_manager_to_buyer": 1,
        "payments_manager_to_manager": 1, "payments_buyer_to_buyer": 1,
        "plain_paid_in_full": 1, "plain_seen": 1,
        "plain_unpaid_after_seen": 0, "unpaid_amount": 0,
        "plain_delivered_unseen": 0,
    })


@pytest.mark.parametrize("seen,paid,expected", [
    (False, 0, (0, 0, 0, 1)),
    (False, 20, (1, 0, 0, 1)),
    (True, 5.125, (0, 1, 14.88, 0)),
    (True, 20 - 1e-6, (1, 0, 0.0, 0)),
])
def test_seen_unpaid_metrics(seen, paid, expected):
    # Running 6.7: seen is independent of payment; unpaid amount includes tolerance.
    m = market()
    c = contract(m)
    delivery(c)
    c.paid = paid
    m.participants[c.buyer].last_view = 31 if seen else 0
    full, unpaid, amount, unseen = expected
    assert_metrics(m, {
        "plain_paid_in_full": full,
        "plain_seen": int(seen),
        "plain_unpaid_after_seen": unpaid,
        "unpaid_amount": amount,
        "plain_delivered_unseen": unseen,
    })


def test_runs_readings_deliveries_and_quality_metrics():
    # Running 6.7: all runs/readings count; quality means use settled results.
    m = market()
    c = contract(m, worth=81)
    d1 = delivery(c, q=6, day=10)
    d2 = delivery(c, q=7, day=11)
    c.paid = 20
    c.results[1].worker = "manager"
    c.results[1].kind = "refine"
    c.results[1].parent = d1.label
    c.results[0].manager_readings = [5.0, 6.0]
    c.results[1].buyer_readings = [8.0]
    result(c, q=9, arrival=21)
    e = contract(m, "escrow", worth=81)
    delivery(e, q=8, day=10, outcome="approved")
    e.release_day, e.received, e.release_how, e.paid = 10, 10, "approved", 20
    got = metrics(m)
    assert got["runs_started"] == 4
    assert got["own_runs"] == 1
    assert got["refine_runs"] == 1
    assert got["manager_readings"] == 2
    assert got["buyer_readings"] == 1
    assert got["deliveries_plain"] == 2
    assert got["submissions_escrow"] == 1
    assert got["runs_unfinished_at_end"] == 1
    assert got["mean_delivered_q"] == 7.5
    assert got["successful_contracts"] == 2
    assert got["late_deliveries"] == 1
    assert got["mean_days_late"] == 0.5
    assert got["mean_value_frac"] == 0.4803
    assert got["value_obtained"] == 77.8
    assert got["escrow_approved"] == 1
    assert d2.label == rows(m)[0]["settled_label"]


@pytest.mark.parametrize("q,paid,expected", [
    (6.99999, 20, 0), (7.0, 20, 1), (7.0, 19.99, 0),
    (7.0, 20 - 1e-6, 1),
])
def test_success_uses_unrounded_q_and_full_payment(q, paid, expected):
    # Running 6.7: fixed q >= 7, not rounded q or the escrow threshold.
    m = market()
    c = contract(m)
    delivery(c, q=q)
    c.paid = paid
    assert metrics(m)["successful_contracts"] == expected


def test_quality_mean_rounds_each_quality_before_averaging():
    # Running 6.7: mean of round(q, 4), then round(mean, 4).
    m = market()
    c1 = contract(m)
    c2 = contract(m)
    delivery(c1, q=7.000049)
    delivery(c2, q=7.000149)
    # Rounded inputs are 7.0000 and 7.0001.
    expected = round((7.0 + 7.0001) / 2, 4)
    assert metrics(m)["mean_delivered_q"] == expected


def test_escrow_release_causes_and_all_rejections_are_counted():
    # Running 6.7: submissions, automatic/close causes, and late release counts.
    m = market()
    for how in ("approved", "automatic", "close", "ruling"):
        c = contract(m, "escrow")
        first = delivery(c, q=6, day=9, outcome="rejected")
        first.decided = 9
        d = delivery(c, q=8, day=11,
                     outcome="approved" if how == "approved" else
                     "rejected" if how == "ruling" else "auto")
        if how == "ruling":
            d.dispute = Dispute(11, reading=8, winner="manager")
        c.release_day, c.received, c.release_how, c.paid = 12, 12, how, 16
    assert_metrics(m, {
        "escrow_released": 4, "submissions_escrow": 8,
        "escrow_approved": 1, "escrow_rejected": 5,
        "escrow_auto_released": 1, "escrow_released_at_close": 1,
        "escrow_released_late": 4,
        "escrow_amount_released": 64, "escrow_amount_refunded": 16,
    })


def test_dispute_metrics_include_each_dispute_and_exclude_close_fee_from_owed():
    # Running 6.5-6.7: true-q disagreement and during-market fee counts.
    m = market()
    c = contract(m, "escrow")
    a = delivery(c, q=8, day=8, outcome="rejected")
    a.dispute = Dispute(8, reading=6, winner="buyer")
    b = delivery(c, q=6, day=10, outcome="rejected")
    b.dispute = Dispute(10, reading=8, winner="manager")
    c.release_day, c.received, c.release_how, c.paid = 11, 12, "ruling", 20
    e = contract(m, "escrow")
    d = delivery(e, q=7, outcome="rejected")
    d.dispute = Dispute(12, reading=7, winner="manager", at_close=True)
    e.release_day, e.received, e.release_how, e.paid = 12, 12, "ruling", 20
    assert rows(m)[0]["disputes"] == [
        {"label": "R-000", "winner": "buyer", "at_close": False},
        {"label": "R-001", "winner": "manager", "at_close": False},
    ]
    assert_metrics(m, {
        "disputes": 3, "disputes_won_buyer": 1,
        "disputes_won_manager": 2, "disputes_ruled_at_close": 1,
        "dispute_rulings_against_q": 2,
        "arbiter_fees_owed": 1, "arbiter_fees_paid": 1,
    })


def test_every_message_class():
    # Running 6.7: private role pairs and group destinations are disjoint.
    m = market({"message_channels": "all"})
    pairs = [
        (MANAGERS[0], MANAGERS[1]),
        (BUYERS[0], BUYERS[1]),
        (BUYERS[0], MANAGERS[0]),
        (MANAGERS[0], BUYERS[0]),
        (BUYERS[0], "managers"),
        (MANAGERS[0], "buyers"),
        (BUYERS[0], "everyone"),
    ]
    m.messages = [Message(1, 1, sender, to, "hello") for sender, to in pairs]
    assert_metrics(m, {
        "messages": 7, "messages_manager_manager": 1,
        "messages_buyer_buyer": 1, "messages_cross_role": 2,
        "messages_to_managers": 1, "messages_to_buyers": 1,
        "messages_to_everyone": 1,
    })


@pytest.mark.parametrize("credit,back,balance", [(1, False, 0), (1.01, True, 0.01)])
def test_close_revives_only_strictly_positive_manager(credit, back, balance):
    # Episode 6, 9.3; running 6.6-6.7: final status follows close credits.
    m = market()
    c = contract(m, "escrow", price=credit)
    delivery(c, day=10, outcome="review")
    p = m.participants[c.manager]
    p.balance, p.out_of_business = -1, True
    m.day = 10
    episode.close(m)
    assert p.balance == pytest.approx(balance)
    assert p.out_of_business is not back
    out = episode.settle(m)
    assert out["scores"][2]["out_of_business"] is not back
    assert out["metrics"]["managers_out_of_business_at_end"] == int(not back)


@pytest.mark.parametrize("amount,out", [
    (0.0, False), (-1.0, False), (0.0000004, False), (0.0000006, True),
])
def test_zero_balance_manager_charge_rounding(amount, out):
    # Episode 9.1, 10: round charges to six places; ignore nonpositive charges.
    m = market()
    p = m.participants[MANAGERS[0]]
    p.balance = 0
    m.day, m.round = 2, 1
    state.charge(m, p.id, amount, "tokens")
    assert p.out_of_business is out
    assert p.pending == (
        ["[day 2, round 1] Your balance is at or below 0: "
         "you are out of business and cannot act."] if out else []
    )


def test_zero_deposit_checks_business_status_but_payment_does_not():
    # Episode 9.1: direct dispute deposit, including zero, differs from payment.
    m = market()
    p = m.participants[MANAGERS[0]]
    state.debit(m, p.id, 60, "payments made")
    assert p.balance == 0
    assert not p.out_of_business
    state.debit(m, p.id, 0, "dispute deposits", check=True)
    assert p.out_of_business


def test_buyer_negative_balance_is_never_out_of_business():
    # Episode 9.4: buyers keep their status and receive no bankruptcy notice.
    m = market()
    p = m.participants[BUYERS[0]]
    state.charge(m, p.id, 61, "tokens")
    assert p.balance == -1
    assert not p.out_of_business
    assert not p.pending
    assert episode.settle(m)["scores"][0]["score"] == -61


@pytest.mark.parametrize("decay,expected", [
    (0.2, 5), (0.25, 4), (0.3, 4), (1 / 3, 3), (1.0, 1), (0.1, 10),
])
def test_escrow_close_days_examples(decay, expected):
    # Config 6.2: all worked examples of ceil(1 / decay - 1e-9).
    cfg = config.resolve({
        "value": {"late_decay": decay},
        "time": {"close_after_due_days": 10},
    })
    assert config.escrow_close_days(cfg) == expected


@pytest.mark.parametrize("signed,expected", [
    (1, 9), (5, 9), (6, 10), (8, 12), (9, 13), (11, 13), (20, 13),
])
def test_deadline_worked_examples(signed, expected):
    # Config 6.3; processes 3.1: due 9, minimum four days, cap 13.
    cfg = config.resolve({})
    assert config.deadline(cfg, 9, signed) == expected


def test_deadline_uses_exact_integer_arithmetic():
    # Config 6.3: enormous minimum days still resolve to the full-pay cap.
    cfg = config.resolve({"contracts": {"min_contract_days": 10**100}})
    assert config.deadline(cfg, 9, 1) == 13


@pytest.mark.parametrize("share,start,budget", [
    (1.36, 81.6, 40.8), (1, 60, 30), (0, 0, 0),
    (0.6, 36, 18), (1.4, 84, 42),
])
def test_derived_seat_money(share, start, budget):
    # Config 6.4; episode 1.3: both roles' start, buyer budget.
    cfg = config.resolve({})
    assert config.start_balance(cfg, share) == start
    assert config.per_task_budget(cfg, share) == budget


@pytest.mark.parametrize("worker,kind,expected", [
    ("employee", "fresh", 4.0), ("employee", "refine", 2.8),
    ("manager", "fresh", 8.0), ("manager", "refine", 14.0),
])
def test_expected_run_cost_examples(worker, kind, expected):
    # Config 6.5: unrounded expected cost with multiplier one.
    assert config.expected_cost(config.resolve({}), worker, kind) == expected


@pytest.mark.parametrize("t,expected", [
    (0.5, [(1, 1.0)]),
    (1.0, [(1, 1.0)]),
    (2.5, [(1, 0.0), (2, 0.0), (3, 1.0)]),
])
def test_zero_sigma_days_mix(t, expected):
    # Config 6.7: exact step CDF at zero duration sigma.
    cfg = config.resolve({"production": {"sig_t": 0}})
    assert config.days_mix(cfg, t) == expected


def test_default_employee_duration_mix_example():
    # Config 6.7: default employee fresh proportions and termination at day 9.
    mix = config.days_mix(config.resolve({}), 1.0)
    assert [day for day, share in mix] == list(range(1, 10))
    assert [round(share, 4) for day, share in mix[:4]] == [
        0.5, 0.4762, 0.0230, 0.0008,
    ]


@pytest.mark.parametrize("rounds_per_day,limit", [(1, 5), (3, 15), (7, 35)])
def test_derived_lost_turn_limit(rounds_per_day, limit):
    # Config 6.8: five times rounds per day; abort is strictly above it.
    cfg = config.resolve({"time": {"rounds_per_day": rounds_per_day}})
    assert config.lost_turn_limit(cfg) == limit
