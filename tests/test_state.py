"""The market object (market/state.py): copies and checkpoints, ids, and the money helpers' rules."""

import copy
import pickle

import pytest

from market import config, state
from market.state import (
    Bid, Contract, Credit, Delivery, Dispute, Market, Memory, Message, Payment, Post, Result, Task,
)


def market():
    """A market with two buyers and two managers at share 1 (start balance 60), on day 3, round 2."""
    m = Market(seed=7, cfg=config.resolve(None))
    for pid, role in (("B-001", "buyer"), ("B-002", "buyer"), ("M-001", "manager"), ("M-002", "manager")):
        state.add_participant(m, pid, role)
    m.day, m.round = 3, 2
    return m


def full_market():
    """A market holding at least one of each entity, with every optional part filled in."""
    m = market()
    m.last_arrival = 12
    m.tasks["task-1"] = Task("task-1", "B-001", worth=60, due=9, state="contracted", post="post-1-2",
                             contract="contract-1-2", ended=["contract-1"])
    m.tasks["task-2"] = Task("task-2", "B-002", worth=50, due=8, state="posted", post="post-2")
    m.posts["post-1"] = Post("post-1", "task-1", "B-001", 1, 1, "escrow", 7.0, "accepted", 1, 3)
    m.posts["post-1-2"] = Post("post-1-2", "task-1", "B-001", 2, 1, None, None, "accepted", 2, 3)
    m.posts["post-2"] = Post("post-2", "task-2", "B-002", 3, 1, "plain")
    m.bids["bid-1"] = Bid("bid-1", "post-1", "M-001", 20.0, 7.0, "accepted")
    m.bids["bid-2"] = Bid("bid-2", "post-1-2", "M-002", 18.5, None, "accepted")
    m.bids["bid-3"] = Bid("bid-3", "post-2", "M-001", 25.0)
    m.bids_created = 3
    rejected = Delivery("R-0a1", 2, 1, "see this", 8.0, "rejected", 2, "too low", Dispute(2, 6.4, "buyer"))
    m.contracts["contract-1"] = Contract(
        "contract-1", "task-1", "post-1", "B-001", "M-001", "escrow", 20.0, 7.0, signed=1, deadline=9,
        results=[Result("R-0a1", "employee", "fresh", None, 1, 2, 6.25, [6.1, 6.4], [5.0])],
        label_draws=2, deliveries=[rejected], ended=3)
    m.contracts["contract-1-2"] = Contract(
        "contract-1-2", "task-1", "post-1-2", "B-001", "M-002", "plain", 18.5, None, signed=2, deadline=9,
        results=[Result("R-fff", "manager", "fresh", None, 2, 3, 7.5),
                 Result("R-0a1", "employee", "refine", "R-fff", 3, 4, 8.0)],
        label_draws=2, deliveries=[Delivery("R-fff", 3, 1)], kept="R-fff", paid=10.0)
    m.payments.append(Payment(3, 1, "B-001", "M-002", 10.0, "contract-1-2", 10.0, advance=True))
    m.messages.append(Message(3, 1, "M-001", "managers", "price 20?"))
    m.summaries["managers"] = ("M-001: asked about price 20.", 10)
    m.summaries[("B-001", "M-001")] = ("B-001: nothing in force.", 20)
    m.deferred.append(Credit("M-002", 10.0, "transfer", "B-001"))
    p = m.participants["B-001"]
    p.ledger += [("per-task budgets", 30.0), ("payments made", 10.0)]
    p.pending.append("[day 3] New task task-9 in your queue.")
    p.turns, p.last_view, p.seen, p.unseen, p.prompt_logged = 7, 8, 1, [0], True
    p.results = ["Results of your actions:", "- pay: OK: paid 10 to M-002 for contract-1-2"]
    p.memory = Memory("keep R-fff", 3, 1, 7, False)
    m.participants["M-001"].out_of_business = True
    m.replies, m.lost_turns, m.days_completed, m.records = 14, 1, 2, 300
    m.close_day, m.close_reason = 3, "due_limit"
    m.counts["buyer_turns"] = 12
    return m


def test_a_deep_copy_holds_everything_but_the_log():
    m = full_market()
    assert copy.deepcopy(m) == m
    records = []
    m.log = records.append
    shadow = copy.deepcopy(m)
    assert shadow.log is None
    state.notify(shadow, "B-002", "seen on the shadow only")
    state.charge(shadow, "M-002", 1, "tests")
    assert records == [] and m.participants["B-002"].pending == [] and m.participants["M-002"].balance == 60
    m.log = None
    shadow.participants["B-002"].pending.clear()
    shadow.participants["M-002"].balance, shadow.participants["M-002"].ledger = 60.0, []
    assert shadow == m


def test_a_copy_shares_the_config_however_deep_it_is_nested():
    # config.md 3.4.1 puts no bound on extra_body's nesting; a copy must not have to walk it
    body = {}
    for _ in range(1100):
        body = {"x": body}
    cfg = config.resolve({"models": {"summarizer": {"provider": "mock", "extra_body": body}}})
    m = Market(seed=0, cfg=cfg)
    state.add_participant(m, "B-001", "buyer")
    shadow = copy.deepcopy(m)
    assert shadow.cfg is m.cfg
    assert shadow.participants == m.participants and shadow.participants is not m.participants
    shadow.participants["B-001"].balance = -1.0
    assert m.participants["B-001"].balance != -1.0


def test_a_copy_has_its_own_lists_of_the_messages_and_payments_it_shares():
    m = full_market()
    shadow = copy.deepcopy(m)
    assert shadow.messages == m.messages and shadow.messages is not m.messages
    assert shadow.payments == m.payments and shadow.payments is not m.payments
    assert all(a is b for a, b in zip(shadow.messages + shadow.payments, m.messages + m.payments))
    shadow.messages.append(state.Message(9, 1, "B-001", "everyone", "only on the copy"))
    shadow.payments.pop()
    assert len(m.messages) == len(shadow.messages) - 1 and len(m.payments) == len(shadow.payments) + 1


def test_a_copy_of_a_participant_has_its_own_ledger_of_the_same_entries():
    m = full_market()
    p = m.participants["B-001"]
    for q in (copy.deepcopy(p), copy.deepcopy(m).participants["B-001"]):
        assert q == p and q is not p
        assert q.ledger is not p.ledger and all(a is b for a, b in zip(q.ledger, p.ledger, strict=True))
        for name in ("pending", "results", "memory", "unseen"):  # everything else that can change is its own
            assert getattr(q, name) == getattr(p, name) and getattr(q, name) is not getattr(p, name)
        q.ledger.append(("tests", 1.0))
        p.ledger.append(("tokens", 0.5))
        assert q.ledger == [("per-task budgets", 30.0), ("payments made", 10.0), ("tests", 1.0)]
        assert p.ledger[2:] == [("tokens", 0.5)]
        del p.ledger[2:]
    shadow = copy.deepcopy(m)
    state.charge(shadow, "B-001", 2.0, "tests")
    assert len(shadow.participants["B-001"].ledger) == 3 and len(p.ledger) == 2 and p.balance == 60.0


def test_a_checkpoint_restores_the_market_exactly():
    m = full_market()
    m.log = [].append
    restored = pickle.loads(pickle.dumps(m))
    assert restored.log is None
    m.log = None
    assert restored == m
    assert list(restored.contracts) == ["contract-1", "contract-1-2"]  # creation order survives
    assert restored.participants["B-001"].ledger[0] == ("per-task budgets", 30.0)
    assert restored.summaries[("B-001", "M-001")] == ("B-001: nothing in force.", 20)


def test_emit_records_only_with_a_sink():
    m = market()
    state.emit(m, "day_start")
    assert m.records == 0
    records = []
    m.log = records.append
    state.emit(m, "day_start", note="x")
    assert records == [{"n": 1, "type": "day_start", "day": 3, "round": 2, "note": "x"}] and m.records == 1


def test_a_notification_is_stamped_when_produced():
    m = market()
    state.notify(m, "B-001", "in a round")
    m.round = None
    state.notify(m, "B-001", "at a day's start")
    assert m.participants["B-001"].pending == ["[day 3, round 2] in a round", "[day 3] at a day's start"]


def test_ids():
    m = full_market()
    assert state.find(m, "contract", "contract-1-2").id == "contract-1-2"
    assert state.find(m, "contract", "1-2").id == "contract-1-2"
    assert state.find(m, "bid", 3).id == "bid-3"
    for raw in ("Bid-3", " bid-3", "bid 3", "03", "\uff13", 3.0, True, None, ["bid-3"], "3\n", "post-2"):
        assert state.find(m, "bid", raw) is None
    assert state.numbered("post", m.tasks["task-2"]) == "post-2"
    assert state.numbered("contract", m.tasks["task-1"]) == "contract-1-2"
    assert state.new_task_id(m) == "task-3"
    assert state.new_bid_id(m) == "bid-4" and m.bids_created == 4


def test_derived_values():
    m = full_market()
    ended, plain = m.contracts["contract-1"], m.contracts["contract-1-2"]
    assert not state.held(ended) and state.settled(ended) and state.refunded(ended) == 20.0
    assert state.setup_fee(m, ended) == 2.0 and state.dispute_fee(m, ended) == 10.0
    assert state.due(m, plain) == 9 and state.close_day(m, ended) == 14
    assert state.owed(plain) == 8.5 and not state.paid_in_full(plain) and state.owed(ended) == 0.0
    assert state.in_use(plain).label == "R-fff" and state.latest(plain).label == "R-fff"
    assert [r.label for _, r in state.in_flight(m, "M-002", "employee")] == ["R-0a1"]
    assert state.in_flight(m, "M-002", "manager") == []
    assert not state.disputable(m, ended) and state.open_dispute(ended) is None
    assert [c.id for c in state.contracts_of(m, "M-002")] == ["contract-1-2"]
    assert [b.id for b in state.bids_on(m, "post-2")] == ["bid-3"]
    assert state.sealed(m, m.posts["post-2"]) and not state.sealed(m, m.posts["post-1-2"])


def test_the_days_and_bids_several_modules_derive():
    m = full_market()
    ended, plain = m.contracts["contract-1"], m.contracts["contract-1-2"]
    rejected, escrow = ended.deliveries[0], m.cfg["contracts"]["escrow"]
    assert state.auto_day(m, rejected) == 2 + escrow["review_days"]  # processes.md 6.5
    assert state.dispute_until(m, rejected) == 2 + escrow["dispute_window_days"]  # 6.6
    assert state.ruling_day(rejected) == 3  # 6.6: the day after the dispute's
    assert state.days_late(plain, plain.deliveries[0]) == 0 and state.days_late(ended, Delivery("R-1", 9, 1)) == 0
    assert state.days_late(ended, Delivery("R-1", 12, 1)) == 3
    post = m.posts["post-2"]  # made on day 3, round 1: round index 7 with 3 rounds a day
    assert state.accept_from(m, post) == 7 + m.cfg["contracts"]["bid_window_rounds"] + 1 == 9  # processes.md 8
    m.bids["bid-4"] = Bid("bid-4", "post-2", "M-002", 21.0)
    m.bids["bid-5"] = Bid("bid-5", "post-2", "M-002", 19.0, status="withdrawn")
    assert [b.id for b in state.bids_on(m, "post-2")] == ["bid-3", "bid-4"]
    assert [b.id for b in state.live_bids(m, "post-2")] == ["bid-4"]  # episode.md 9.2: M-001 is out of business
    assert state.own_bid(m, "post-2", "M-001").id == "bid-3" and state.own_bid(m, "post-2", "M-002").id == "bid-4"
    assert state.own_bid(m, "post-1", "M-001") is None and state.live_bids(m, "post-1") == []


def test_a_balance_covers_a_cost_within_the_tolerance():
    p = market().participants["B-001"]  # actions-buyer.md 1.12
    assert state.covers(p, 60.0) and state.covers(p, 60.0 + 9e-10) and not state.covers(p, 60.0 + 2e-9)


def test_a_reply_is_counted_for_its_role_or_the_summarizer():
    m = market()  # running.md 6.7
    reply = type("Reply", (), {"input_tokens": 7, "output_tokens": 3, "cost": 0.25, "cost_known": True})
    state.count_reply(m, "buyer", reply)
    state.count_reply(m, "buyer", reply)
    assert m.counts == {"buyer_calls": 2, "buyer_input_tokens": 14, "buyer_output_tokens": 6, "buyer_dollars": 0.5}
    reply.cost_known = False
    state.count_reply(m, "summarizer", reply)
    assert m.counts["calls_unknown_cost"] == 1 and m.counts["summarizer_dollars"] == 0.25


def test_an_escrow_state_follows_from_its_submissions():
    m = market()
    m.tasks["task-1"] = Task("task-1", "B-001", worth=60, due=9)
    c = Contract("contract-1", "task-1", "post-1", "B-001", "M-001", "escrow", 20.0, 7.0, signed=1, deadline=9)
    assert state.held(c) and not state.settled(c) and state.refunded(c) == 0.0 and state.latest(c) is None
    c.deliveries.append(Delivery("R-001", 2, 1, outcome="review"))
    assert state.under_review(c).label == "R-001" and not state.disputable(m, c)
    c.deliveries[0].outcome, c.deliveries[0].decided = "rejected", 2
    assert state.under_review(c) is None and state.disputable(m, c)  # day 3 <= 2 + 1
    m.day = 4
    assert not state.disputable(m, c)  # the window passed: the rejection stands
    c.deliveries[0].dispute = Dispute(3)
    assert state.open_dispute(c).label == "R-001" and not state.disputable(m, c)
    c.deliveries[0].dispute.winner, c.release_day, c.release_how, c.paid = "manager", 4, "ruling", 16.0
    assert state.open_dispute(c) is None and not state.held(c) and state.settled(c)
    assert state.refunded(c) == 4.0 and state.owes_fee(m, c)
    c.received = 5
    assert not state.owes_fee(m, c)


# The money helpers (episode.md, 9; rounds.md, 7; view.md, 8)


def test_a_charge_is_rounded_booked_and_logged():
    m = market()
    records = []
    m.log = records.append
    assert state.charge(m, "B-001", 0.12345649, "tokens") == 0.123456
    p = m.participants["B-001"]
    assert p.balance == 60 - 0.123456 and p.ledger == [("tokens", 0.123456)]
    assert [r["type"] for r in records] == ["money"]
    assert records[0]["participant"] == "B-001" and records[0]["kind"] == "tokens"


@pytest.mark.parametrize("amount", [0, 0.0000004, -3])
def test_a_charge_of_zero_or_less_is_ignored(amount):
    m = market()
    m.participants["M-001"].balance = 0.0
    assert state.charge(m, "M-001", amount, "tests") == 0.0
    p = m.participants["M-001"]
    assert p.balance == 0.0 and p.ledger == [] and not p.out_of_business and p.pending == []


def test_a_charge_that_leaves_a_manager_at_zero_puts_it_out_of_business():
    m = market()
    m.participants["M-001"].balance = 1.0
    state.charge(m, "M-001", 1, "tests")
    p = m.participants["M-001"]
    assert p.balance == 0.0 and p.out_of_business
    assert p.pending == ["[day 3, round 2] Your balance is at or below 0: you are out of business and cannot act."]
    assert m.counts == {"out_of_business_events": 1}
    state.charge(m, "M-001", 1, "tests")  # already out: no second event or notification
    assert len(p.pending) == 1 and m.counts == {"out_of_business_events": 1}


def test_a_manager_at_zero_stays_in_business_until_a_charge():
    m = market()
    p = m.participants["M-001"]
    state.debit(m, "M-001", 60.0, "payments made")  # a payment is no charge
    assert p.balance == 0.0 and not p.out_of_business and p.ledger == [("payments made", 60.0)]
    state.charge(m, "M-001", 0.000001, "tokens")
    assert p.out_of_business


def test_a_buyer_is_never_out_of_business():
    m = market()
    state.charge(m, "B-001", 75, "tokens")
    p = m.participants["B-001"]
    assert p.balance == -15 and not p.out_of_business and p.pending == []


def test_a_dispute_deposit_of_zero_still_runs_the_check():
    m = market()
    m.participants["M-001"].balance = 0.0
    state.debit(m, "M-001", 0.0, "dispute deposits", check=True)
    p = m.participants["M-001"]
    assert p.out_of_business and p.ledger == [("dispute deposits", 0.0)]


def test_a_credit_outside_an_action_lands_at_once_and_can_bring_a_manager_back():
    m = market()
    m.round = None
    p = m.participants["M-001"]
    p.balance, p.out_of_business = -2.0, True
    state.credit(m, "M-001", 2.0, "dispute deposits returned")  # leaves it at 0: not above
    assert p.balance == 0.0 and p.out_of_business and p.pending == []
    state.credit(m, "M-001", 16.0, "escrow releases")
    assert p.balance == 16.0 and not p.out_of_business
    assert p.pending == ["[day 3] Your balance is positive again: you are back in business."]
    assert p.ledger == [("dispute deposits returned", 2.0), ("escrow releases", 16.0)]
    assert m.deferred == [] and m.counts == {"back_in_business_events": 1}


def test_a_credit_to_another_participant_waits_for_the_round_end():
    m = market()
    payee = m.participants["M-001"]
    payee.balance, payee.out_of_business = 0.0, True
    m.actor = "B-001"
    state.debit(m, "B-001", 10.0, "payments made")
    state.credit(m, "M-001", 10.0, "payments received")
    state.credit(m, "M-002", 16.0, "escrow releases")
    state.credit(m, "B-001", 4.0, "escrow refunds")  # the actor's own money moves at once
    m.actor = None
    assert payee.balance == 0.0 and payee.out_of_business and payee.pending == []
    assert payee.ledger == [("payments received", 10.0)]  # booked at the event
    assert m.participants["B-001"].balance == 54.0 and m.participants["M-002"].balance == 60.0
    assert m.deferred == [Credit("M-001", 10.0, "transfer", "B-001"), Credit("M-002", 16.0, "escrow_release", "B-001")]

    records = []
    m.log = records.append
    state.land_credits(m)
    assert m.deferred == [] and payee.balance == 10.0 and not payee.out_of_business
    assert payee.pending == ["[day 3, round 2] Your balance is positive again: you are back in business."]
    assert payee.ledger == [("payments received", 10.0)]  # no second entry at the landing
    assert m.participants["M-002"].balance == 76.0
    assert [r["type"] for r in records] == ["credit_landed", "back_in_business", "notification", "credit_landed"]
    assert {k: records[0][k] for k in ("to", "amount", "kind", "payer")} == {
        "to": "M-001", "amount": 10.0, "kind": "transfer", "payer": "B-001"}


def test_a_shadow_defers_like_the_market_and_keeps_its_own_list():
    m = market()
    shadow = copy.deepcopy(m)
    shadow.actor = "B-001"
    state.credit(shadow, "M-001", 5.0, "payments received")
    assert shadow.deferred == [Credit("M-001", 5.0, "transfer", "B-001")] and m.deferred == []
    assert shadow.participants["M-001"].balance == 60.0


def test_a_dispute_deposit_that_is_not_finite_stops_the_episode():
    # config.md 6, "Finiteness during an episode": dispute_fee_rate has no upper bound, so rate x price can overflow
    m = full_market()
    c = next(c for c in m.contracts.values() if c.form == "escrow")
    assert state.dispute_fee(m, c) == round(m.cfg["contracts"]["escrow"]["dispute_fee_rate"] * c.price, 2)
    m.cfg["contracts"]["escrow"]["dispute_fee_rate"] = 1e308
    with pytest.raises(ArithmeticError, match=c.id):
        state.dispute_fee(m, c)


@pytest.mark.parametrize("amount", [float("inf"), float("nan")])
def test_an_amount_that_is_not_finite_stops_the_episode(amount):
    m = market()
    with pytest.raises(ArithmeticError):
        state.charge(m, "B-001", amount, "tokens")
    with pytest.raises(ArithmeticError):
        state.credit(m, "B-001", amount, "per-task budgets")
    assert m.participants["B-001"].ledger == []


def test_what_a_copy_shares_cannot_be_changed():
    # a market copy shares its messages and payments (the state a turn saw must not move): they are frozen
    message = state.Message(1, 1, "B-001", "everyone", "text")
    payment = Payment(3, 1, "B-001", "M-002", 10.0, "contract-1-2", 10.0, advance=True)
    with pytest.raises(AttributeError):
        message.text = "changed"
    with pytest.raises(AttributeError):
        payment.amount = 1.0
