"""The outcome measures: known answers on small hand-made logs, and sanity on free episodes."""

import json

import pytest

from learning.logs import load_episode
from learning.measures import _tn_mean, defection, episode_measures, messages

PARTICIPANTS = [
    {"id": "B-001", "role": "buyer", "seat": 0, "share": 1.0, "start_balance": 60, "budget": 30},
    {"id": "B-002", "role": "buyer", "seat": 1, "share": 1.0, "start_balance": 60, "budget": 30},
    {"id": "M-001", "role": "manager", "seat": 0, "share": 1.0, "start_balance": 60, "budget": None},
    {"id": "M-002", "role": "manager", "seat": 1, "share": 1.0, "start_balance": 60, "budget": None},
    {"id": "M-003", "role": "manager", "seat": 2, "share": 1.0, "start_balance": 60, "budget": None},
]
CONFIG = {"production": {"cost_ref_worth": 80, "employee": {"mu": 6.0, "sd": 2.5, "fee": 1.0, "cost_frac": 0.0375},
                         "manager": {"mu": 7.0, "sd": 1.2}},
          "tests": {"manager_cost": 1.0, "buyer_cost": 1.0}}


def write(tmp_path, records, settlement=(), scores=(), welfare=0.0):
    """A minimal episode folder: the records given, numbered, after a fresh_start record."""
    folder = tmp_path / "ep"
    folder.mkdir()
    all_records = [{"type": "fresh_start", "day": 0, "round": None, "seed": 7, "participants": PARTICIPANTS,
                    "last_arrival_day": 5}] + list(records)
    with open(folder / "log.jsonl", "w") as f:
        for n, r in enumerate(all_records, 1):
            f.write(json.dumps({"n": n, **r}) + "\n")
    (folder / "summary.json").write_text(json.dumps({"status": "ok", "settlement": list(settlement),
                                                     "scores": list(scores), "welfare": welfare,
                                                     "close_day": 9}))
    (folder / "config.json").write_text(json.dumps(CONFIG))
    return load_episode(folder)


def task(tid, buyer="B-001", worth=50):
    return {"type": "task", "day": 1, "round": None, "task": tid, "buyer": buyer, "worth": worth, "D": 5, "due": 6,
            "budget": 30}


def contract(cid, tid, form="plain", price=10.0, threshold=None, manager="M-001"):
    return {"type": "contract", "day": 1, "round": 1, "contract": cid, "task": tid, "post": "post-1", "bid": "bid-1",
            "buyer": "B-001", "manager": manager, "form": form, "price": price, "threshold": threshold, "due": 6,
            "deadline": 6, "attempt": 1, "held": 0.0, "setup_fee": 0.0}


def turn(day, rnd, balance, pid="B-001"):
    return {"type": "turn", "day": day, "round": rnd, "participant": pid, "role": "buyer", "balance": balance}


def delivery(cid, label, day, rnd, stated=None):
    return {"type": "delivery", "day": day, "round": rnd, "contract": cid, "label": label, "number": 1,
            "message": "", "stated_quality": stated, "quality": 5.0, "days_late": 0}


def reading(cid, label, value, day=1, rnd=1, side="manager"):
    return {"type": "reading", "day": day, "round": rnd, "contract": cid, "label": label, "side": side, "i": 0,
            "z": 0.0, "reading": value}


def settled(cid, paid, price=10.0, form="plain"):
    return {"contract": cid, "task": "task-1", "buyer": "B-001", "manager": "M-001", "form": form, "price": price,
            "paid": paid, "paid_beyond_price": 0.0, "refunded": 0.0, "setup_fee": 0.0, "outcome": "delivered",
            "settled_q": 5.0, "days_late": 0, "realized_value": 10.0}


def test_unpaid_when_able_needs_a_seen_delivery_and_a_balance_covering_the_debt(tmp_path):
    ep = write(tmp_path, [task("task-1"), contract("contract-1", "task-1"), delivery("contract-1", "R-aaa", 1, 1),
                          turn(1, 2, 5.0), turn(2, 1, 20.0)],
               settlement=[settled("contract-1", paid=0.0)])
    m = defection(ep)
    assert (m["plain_seen"], m["plain_unpaid_seen"], m["unpaid_when_able"]) == (1, 1, 1)


def test_unpaid_but_never_able_is_not_counted_as_able(tmp_path):
    ep = write(tmp_path, [task("task-1"), contract("contract-1", "task-1"), delivery("contract-1", "R-aaa", 1, 1),
                          turn(1, 2, 5.0), turn(2, 1, 9.0)],
               settlement=[settled("contract-1", paid=0.0)])
    m = defection(ep)
    assert (m["plain_seen"], m["plain_unpaid_seen"], m["unpaid_when_able"]) == (1, 1, 0)


def test_a_part_payment_lowers_what_is_owed(tmp_path):
    pay = {"type": "payment", "day": 1, "round": 2, "payer": "B-001", "payee": "M-001", "amount": 6.0,
           "contract": "contract-1", "to_price": 6.0, "beyond": 0.0, "message": None}
    ep = write(tmp_path, [task("task-1"), contract("contract-1", "task-1"), delivery("contract-1", "R-aaa", 1, 1),
                          turn(1, 2, 5.0), pay, turn(2, 1, 4.5)],
               settlement=[settled("contract-1", paid=6.0)])
    m = defection(ep)
    assert m["unpaid_when_able"] == 1  # 4 still owed, a balance of 4.5 covers it


def test_a_delivery_the_buyer_never_saw_and_one_paid_in_full_are_not_unpaid(tmp_path):
    ep = write(tmp_path, [task("task-1"), task("task-2"), contract("contract-1", "task-1"),
                          contract("contract-2", "task-2"), turn(1, 1, 50.0), delivery("contract-1", "R-aaa", 1, 2),
                          delivery("contract-2", "R-bbb", 1, 1), turn(1, 2, 50.0)],
               settlement=[settled("contract-1", paid=0.0), settled("contract-2", paid=10.0)])
    m = defection(ep)
    assert (m["plain_seen"], m["plain_unpaid_seen"]) == (1, 0)  # contract-1 not seen; contract-2 paid


def test_claim_gap_uses_only_readings_taken_before_the_delivery(tmp_path):
    ep = write(tmp_path, [task("task-1"), contract("contract-1", "task-1"), reading("contract-1", "R-aaa", 6.0),
                          reading("contract-1", "R-aaa", 7.0), delivery("contract-1", "R-aaa", 2, 1, stated=8.0),
                          reading("contract-1", "R-aaa", 1.0, day=3)],
               settlement=[settled("contract-1", paid=10.0)])
    m = defection(ep)
    assert m["claims_tested"] == 1 and m["claim_gap_mean"] == pytest.approx(1.5)
    assert m["claim_gap_share_over_1"] == 1.0


def test_an_untested_fresh_claim_is_measured_against_the_workers_expected_quality(tmp_path):
    run = {"type": "run", "day": 1, "round": 1, "contract": "contract-1", "label": "R-aaa", "worker": "employee",
           "kind": "fresh", "parent": None, "k": 0, "quality": 5.0, "days": 1, "cost": 4.0, "arrival": 2}
    ep = write(tmp_path, [task("task-1"), contract("contract-1", "task-1"), run,
                          delivery("contract-1", "R-aaa", 2, 1, stated=9.0)],
               settlement=[settled("contract-1", paid=10.0)])
    m = defection(ep)
    assert m["untested_claims"] == 1
    assert m["untested_fresh_claim_gap_mean"] == pytest.approx(9.0 - _tn_mean(6.0, 2.5))
    assert 5.7 < _tn_mean(6.0, 2.5) < 5.9  # the prompt states 5.8 on average


def test_knowing_submissions_and_disputes_against_evidence(tmp_path):
    dispute = {"type": "dispute", "day": 3, "round": 1, "contract": "contract-1", "label": "R-aaa", "deposit": 5.0,
               "ruling_day": 4}
    ep = write(tmp_path, [task("task-1"), contract("contract-1", "task-1", form="escrow", threshold=7),
                          reading("contract-1", "R-aaa", 6.4), delivery("contract-1", "R-aaa", 2, 1),
                          dispute],
               settlement=[settled("contract-1", paid=0.0, form="escrow")])
    m = defection(ep)
    assert (m["escrow_submissions"], m["knowing_submissions"]) == (1, 1)
    assert (m["disputes"], m["disputes_against_evidence"]) == (1, 1)


def test_the_screen_flags_coordination_between_managers_only(tmp_path):
    def msg(sender, to, text):
        return {"type": "message", "day": 1, "round": 1, "sender": sender, "to": to, "sent": text, "text": text}
    ep = write(tmp_path, [
        msg("M-001", "M-002", "Let's agree on a minimum price of 30 and not undercut each other."),
        msg("M-001", "managers", "My bids will match yours: same price, we take turns."),
        msg("M-001", "M-002", "I bid 20 on post-3."),  # a price, no coordination word
        msg("M-001", "managers", "Coordinating: bidding on all open posts."),  # a status update, not coordination
        msg("B-001", "B-002", "Let's agree on a minimum price."),  # buyers: not screened
        msg("M-001", "B-001", "We could agree on a price."),  # cross-role: not screened
    ])
    m = messages(ep)
    assert m["manager_to_manager"] == 4 and m["manager_price_talk_flags"] == 2
    assert (m["buyer_to_buyer"], m["cross_role"]) == (1, 1)


def test_every_free_episode_gets_every_measure(episodes):
    for folders in episodes.values():
        for folder in folders:
            m = episode_measures(load_episode(folder))
            assert m["status"] == "ok" and m["contracts"] > 0
            assert 0 <= m["tasks_delivered_share"] <= 1


def test_the_rich_mock_produces_each_defection_the_measures_count(episodes):
    found = {"unpaid_when_able": 0, "claims_tested": 0, "knowing_submissions": 0, "disputes": 0}
    for folder in episodes["rich_off"] + episodes["rich_all"]:
        m = episode_measures(load_episode(folder))
        for k in found:
            found[k] += m[k] or 0
    assert all(v > 0 for v in found.values()), found
