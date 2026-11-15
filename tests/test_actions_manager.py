"""The manager's actions and pay (market/actions_manager.py) against spec/actions-manager.md, sections 2 to 9.

Every expected text is copied from the section the test names. Each action runs through the dispatcher
(`actions.run`), on markets built by hand; a refusal is also checked to change nothing. The last tests compare the
actions with the reference implementation's outcomes in spec/vectors/round_order.jsonl.
"""

import copy
import graphlib
import itertools
import json
import pathlib
import re
from collections import Counter

import pytest

from market import actions, actions_manager, config, rng, state
from market.state import Bid, Contract, Credit, Delivery, Dispute, Market, Payment, Post, Result, Task

PM = "\u00b1"
STAMP = "[day 5, round 2] "
BID_TAIL = " A bid becomes a contract only when the buyer accepts it."
OUT = "Your balance is at or below 0: you are out of business and cannot act."
RULE = "a payment can name only a plain contract on which you are the buyer and the payee is the manager"


def market(day=5, rnd=2, buyers=("B-c53", "B-d75"), managers=("M-14b", "M-849", "M-f8c")):
    """An empty market in round 2 of day 5 (round index 14), everyone at balance 60, with a log that is a list."""
    m = Market(seed=7, cfg=config.resolve(None))
    for pid in buyers:
        state.add_participant(m, pid, "buyer")
    for pid in managers:
        state.add_participant(m, pid, "manager")
    m.day, m.round = day, rnd
    m.log = [].append
    return m


def log(m, *kinds):
    return [r for r in m.log.__self__ if not kinds or r["type"] in kinds]


def add_post(m, n, buyer="B-c53", day=1, rnd=1, **fields):
    """post-{n} and its task; made in round 1 of day 1 unless said, so its bids are open to its buyer."""
    tid = "task-" + str(n).split("-")[0]
    m.tasks[tid] = Task(tid, buyer, 60, 9, state="posted", post=f"post-{n}")
    post = m.posts[f"post-{n}"] = Post(f"post-{n}", tid, buyer, day, rnd, **fields)
    return post


def add_bid(m, n, post, manager, price=20.0, thr=None, status="open"):
    m.bids[f"bid-{n}"] = Bid(f"bid-{n}", post, manager, price, thr, status)
    m.bids_created = max(m.bids_created, n)
    return m.bids[f"bid-{n}"]


def add_contract(m, n, buyer="B-c53", manager="M-14b", form="plain", price=20.0, due=9, deadline=9, **fields):
    """contract-{n} and its task; an escrow has the threshold 5."""
    tid = "task-" + str(n).split("-")[0]
    m.tasks[tid] = Task(tid, buyer, 60, due, state="contracted", contract=f"contract-{n}")
    c = m.contracts[f"contract-{n}"] = Contract(f"contract-{n}", tid, f"post-{n}", buyer, manager, form, price,
                                                5.0 if form == "escrow" else None, 1, deadline, **fields)
    return c


def add_result(c, label, arrival=2, worker="employee", kind="fresh", parent=None, q=6.0):
    r = Result(label, worker, kind, parent, 1, arrival, q)
    c.results.append(r)
    return r


def submit(c, label, day, outcome="review", **fields):
    """A submission (or, with outcome None, a delivery) of a new arrived result."""
    add_result(c, label)
    d = Delivery(label, day, 1, outcome=outcome, **fields)
    c.deliveries.append(d)
    return d


def pending(m, pid):
    return m.participants[pid].pending


def balance(m, pid):
    return m.participants[pid].balance


def ledger(m, pid):
    return m.participants[pid].ledger


def ok(m, pid, name, args, text, fixed=None):
    assert actions.run(m, pid, name, args, fixed) == (True, text)


def refused(m, pid, name, args, text, fixed=None):
    """The action fails with exactly this text and changes nothing (actions-buyer.md, 1.2)."""
    before, records = copy.deepcopy(m), len(log(m))
    before.log = m.log
    assert actions.run(m, pid, name, args, fixed) == (False, text)
    assert m == before
    assert len(log(m)) == records


def pin_run(monkeypatch, days=1, cost=4.41, q=6.0):
    """Replace the run draw: the calls it gets are returned as (stream, worker, kind, k, q_parent)."""
    calls = []

    def run(seed, stream, worker, kind, k, production, q_parent=None):
        calls.append((stream, worker, kind, k, q_parent))
        return {"z_q": 0.1, "z_t": 0.2, "z_c": 0.3, "q": q, "days": days, "cost": cost}

    monkeypatch.setattr(rng, "run", run)
    return calls


def pin_label(monkeypatch, *labels, skip=0):
    """Replace the label draw: each call takes the next label and makes 1 + skip draws."""
    calls, rest = [], iter(labels)

    def label(seed, stream, n, used):
        calls.append((stream, n, set(used)))
        return {"label": next(rest), "n": n + 1 + skip}

    monkeypatch.setattr(rng, "label", label)
    return calls


def pin_reading(monkeypatch, *values):
    calls, rest = [], iter(values)

    def reading(seed, stream, side, label, i, q, sd, decimals):
        calls.append((stream, side, label, i, q, sd, decimals))
        return {"z": 0.5, "reading": next(rest)}

    monkeypatch.setattr(rng, "reading", reading)
    return calls


def test_every_action_of_this_file_is_registered():
    for name in ("bid", "withdraw_bid", "delegate_run", "run_myself", "test_result", "deliver", "dispute", "pay"):
        assert actions.ACTIONS[name] is getattr(actions_manager, name)
    assert {"withdraw_bid", "pay"} <= set(actions.IMPLICIT)


# 2. bid


def test_bid_checks_in_order():
    m = market()
    add_post(m, 1)
    add_post(m, 2, closed="accepted", closed_day=5, closed_round=2)
    bad = {"price": "seven", "threshold": 11}
    for args in ({}, {"post": "post-1"}, {"post_id": None}, {"post_id": ""}):
        refused(m, "M-14b", "bid", args | bad, "missing argument 'post_id'")
    refused(m, "M-14b", "bid", {"post_id": "post-999"} | bad, "no post with id 'post-999'. Open posts: post-1.")
    refused(m, "M-14b", "bid", {"post_id": "post-2"} | bad,
            "post-2 closed earlier this round, when its buyer accepted a bid")
    for args in ({}, {"amount": 20}, {"price": None}, {"price": ""}):
        refused(m, "M-14b", "bid", {"post_id": "post-1", "threshold": 11} | args, "missing argument 'price'")
    refused(m, "M-14b", "bid", {"post_id": "post-1"} | bad, "price must be a number, got 'seven'")
    for price in (0, -3, 0.004, "inf", "nan"):
        refused(m, "M-14b", "bid", {"post_id": "post-1", "price": price, "threshold": 11},
                "price must be a positive number")
    refused(m, "M-14b", "bid", {"post_id": "post-1", "price": 20, "threshold": 11},
            "threshold must be a quality between 0 and 10, got 11")
    refused(m, "M-14b", "bid", {"post_id": "post-1", "price": 20, "threshold": "7.5"},
            "threshold must be a whole number between 0 and 10, got '7.5'")
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 20, "threshold": 7},
       "placed bid-1 on post-1 at 20 with threshold 7 (escrow)")


def test_bid_on_an_unknown_post_lists_the_open_posts_in_creation_order():
    m = market()
    for n in (3, 1, 4, 2, 5):
        add_post(m, n)
    refused(m, "M-14b", "bid", {"post_id": "post-999", "price": 20},
            "no post with id 'post-999'. Open posts: post-3, post-1, post-4, post-2, post-5.")
    for post in m.posts.values():
        post.closed, post.closed_day, post.closed_round = "withdrawn", 4, 1
    refused(m, "M-14b", "bid", {"post_id": "post-999", "price": 20}, "no post with id 'post-999'. Open posts: none.")


def test_bid_on_a_closed_post_says_when_and_why_and_lists_no_post():
    m = market(day=17, rnd=3)
    add_post(m, 8, closed="accepted", closed_day=17, closed_round=3)
    add_post(m, 31, closed="accepted", closed_day=17, closed_round=2)
    add_post(m, 30, closed="accepted", closed_day=16, closed_round=3)
    add_post(m, 24, closed="withdrawn", closed_day=17, closed_round=3)
    add_post(m, 25, closed="withdrawn", closed_day=2, closed_round=1)
    add_post(m, 1)
    args = {"price": 20}
    refused(m, "M-14b", "bid", {"post_id": "post-8"} | args,
            "post-8 closed earlier this round, when its buyer accepted a bid")
    refused(m, "M-14b", "bid", {"post_id": "post-31"} | args,
            "post-31 closed on day 17, round 2, when its buyer accepted a bid")
    refused(m, "M-14b", "bid", {"post_id": "post-30"} | args,
            "post-30 closed on day 16, round 3, when its buyer accepted a bid")
    refused(m, "M-14b", "bid", {"post_id": "post-24"} | args,
            "post-24 closed earlier this round, when its buyer withdrew it")
    refused(m, "M-14b", "bid", {"post_id": "25"} | args, "post-25 closed on day 2, round 1, when its buyer withdrew it")


def test_bid_on_a_closed_post_names_the_open_repost_of_its_task():
    m = market()
    add_post(m, 4, closed="accepted", closed_day=1, closed_round=3)
    add_post(m, "4-2")
    refused(m, "M-14b", "bid", {"post_id": "post-4", "price": 20},
            "post-4 closed on day 1, round 3, when its buyer accepted a bid; its task is posted again as post-4-2 "
            "(open)")
    m.posts["post-4-2"].closed, m.posts["post-4-2"].closed_day, m.posts["post-4-2"].closed_round = "accepted", 5, 1
    refused(m, "M-14b", "bid", {"post_id": "post-4", "price": 20},
            "post-4 closed on day 1, round 3, when its buyer accepted a bid")
    refused(m, "M-14b", "bid", {"post_id": "post-4-2", "price": 20},
            "post-4-2 closed on day 5, round 1, when its buyer accepted a bid")
    del m.posts["post-4-2"]  # rounds.md, 8.6: a hidden repost exists for no lookup
    refused(m, "M-14b", "bid", {"post_id": "post-4", "price": 20},
            "post-4 closed on day 1, round 3, when its buyer accepted a bid")


def test_a_new_bid_takes_the_next_number_and_tells_the_buyer():
    m = market(managers=("M-fda", "M-849"))
    add_post(m, 9)
    m.bids_created = 18
    ok(m, "M-fda", "bid", {"post_id": "post-9", "price": 30, "threshold": 6},
       "placed bid-19 on post-9 at 30 with threshold 6 (escrow)")
    assert m.bids == {"bid-19": Bid("bid-19", "post-9", "M-fda", 30.0, 6.0)}
    assert m.bids_created == 19
    assert pending(m, "B-c53") == [STAMP + "M-fda placed bid bid-19 on post-9 at 30 with threshold 6 (escrow)"]
    assert all(pending(m, pid) == [] for pid in ("B-d75", "M-fda", "M-849"))
    assert all(p.balance == 60 and p.ledger == [] for p in m.participants.values())
    record, = log(m, "bid")
    assert (record["bid"], record["post"], record["manager"], record["price"], record["threshold"], record["form"],
            record["verb"], record["day"], record["round"]) == ("bid-19", "post-9", "M-fda", 30.0, 6.0, "escrow",
                                                                "placed", 5, 2)
    ok(m, "M-849", "bid", {"post_id": 9, "price": "9.5"}, "placed bid-20 on post-9 at 9.5")
    assert m.bids["bid-20"] == Bid("bid-20", "post-9", "M-849", 9.5, None)
    assert pending(m, "B-c53")[1:] == [STAMP + "M-849 placed bid bid-20 on post-9 at 9.5"]
    assert log(m, "bid")[1]["form"] == "plain"


def test_a_bid_on_a_post_with_an_open_bid_of_the_manager_updates_it_in_place():
    m = market()
    add_post(m, 1)
    add_post(m, 2)
    add_bid(m, 1, "post-1", "M-14b", 30.0)
    add_bid(m, 2, "post-2", "M-849", 12.0, 4.0)
    add_bid(m, 3, "post-1", "M-849", 20.0)
    add_bid(m, 4, "post-1", "M-f8c", 25.0, 5.0)
    ok(m, "M-849", "bid", {"post_id": "post-1", "price": 16, "threshold": 6},
       "updated bid-3 on post-1 at 16 with threshold 6 (escrow)")
    assert list(m.bids) == ["bid-1", "bid-2", "bid-3", "bid-4"]
    assert m.bids["bid-3"] == Bid("bid-3", "post-1", "M-849", 16.0, 6.0)
    assert m.bids["bid-2"] == Bid("bid-2", "post-2", "M-849", 12.0, 4.0)
    assert m.bids_created == 4
    assert pending(m, "B-c53") == [STAMP + "M-849 updated bid bid-3 on post-1 at 16 with threshold 6 (escrow)"]
    ok(m, "M-849", "bid", {"post_id": "post-1", "price": 9.5}, "updated bid-3 on post-1 at 9.5")
    assert m.bids["bid-3"] == Bid("bid-3", "post-1", "M-849", 9.5, None)
    ok(m, "M-849", "bid", {"post_id": "post-1", "price": 9.5, "threshold": 0},
       "updated bid-3 on post-1 at 9.5 with threshold 0 (escrow)")
    assert m.bids["bid-3"].threshold == 0 and m.bids_created == 4
    assert [r["verb"] for r in log(m, "bid")] == ["updated"] * 3


@pytest.mark.parametrize("status", ["withdrawn", "lost", "accepted"])
def test_a_bid_that_is_not_open_is_never_reused(status):
    m = market()
    add_post(m, 1)
    add_bid(m, 1, "post-1", "M-14b", 30.0, status=status)
    add_bid(m, 2, "post-1", "M-849", 20.0)
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 28}, "placed bid-3 on post-1 at 28")
    assert m.bids["bid-1"] == Bid("bid-1", "post-1", "M-14b", 30.0, None, status)
    assert list(m.bids) == ["bid-1", "bid-2", "bid-3"] and m.bids_created == 3


def test_bid_has_no_other_check():
    m = market()
    add_post(m, 1, form="plain")
    add_post(m, 2, form="escrow", threshold=8.0)
    m.tasks["task-1"].due = 1
    m.participants["M-14b"].balance = 0.0
    m.participants["B-c53"].balance = -5.0
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 1000, "threshold": 2},
       "placed bid-1 on post-1 at 1000 with threshold 2 (escrow)")
    ok(m, "M-14b", "bid", {"post_id": "post-2", "price": 0.01}, "placed bid-2 on post-2 at 0.01")
    assert not m.participants["M-14b"].out_of_business


@pytest.mark.parametrize("price, used, shown", [
    (25, 25.0, "25"), ("24.555", 24.55, "24.55"), (0.005, 0.01, "0.01"), (" 25 ", 25.0, "25"), ("1e16", 1e16, "1e+16"),
    (1234567, 1234567.0, "1.23457e+06"), ("1e2", 100.0, "100"),
])
def test_the_price_of_a_bid_is_rounded_to_the_cent_and_written_in_g(price, used, shown):
    m = market()
    add_post(m, 1)
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": price}, f"placed bid-1 on post-1 at {shown}")
    assert m.bids["bid-1"].price == used
    assert pending(m, "B-c53") == [STAMP + f"M-14b placed bid bid-1 on post-1 at {shown}"]


@pytest.mark.parametrize("given, used", [(7, 7.0), ("7", 7.0), (" 7 ", 7.0), (7.0, 7.0), (0, 0.0), (10, 10.0)])
def test_a_threshold_makes_an_escrow_bid(given, used):
    m = market()
    add_post(m, 1)
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 20, "threshold": given},
       f"placed bid-1 on post-1 at 20 with threshold {int(used)} (escrow)")
    assert m.bids["bid-1"].threshold == used


@pytest.mark.parametrize("args", [{"threshold": None}, {"threshold": ""}, {"quality_threshold": 7}])
def test_a_threshold_that_is_not_given_makes_a_plain_bid(args):
    m = market()
    add_post(m, 1)
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 20} | args, "placed bid-1 on post-1 at 20")
    assert m.bids["bid-1"].threshold is None


@pytest.mark.parametrize("message", ["hello", " ", "\n", False, 0, [], {}])
def test_a_message_given_to_bid_is_noted_and_goes_nowhere(message):
    m = market()
    add_post(m, 1)
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 20, "message": message},
       "placed bid-1 on post-1 at 20 (bid takes no message: yours was not sent)")
    assert pending(m, "B-c53") == [STAMP + "M-14b placed bid bid-1 on post-1 at 20"]
    assert m.messages == []
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 25, "threshold": 5, "message": message},
       "updated bid-1 on post-1 at 25 with threshold 5 (escrow) (bid takes no message: yours was not sent)")


@pytest.mark.parametrize("args", [{}, {"message": None}, {"message": ""}, {"text": "hello"}])
def test_no_note_without_a_message(args):
    m = market()
    add_post(m, 1)
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 25, "threshold": 5} | args,
       "placed bid-1 on post-1 at 25 with threshold 5 (escrow)")


def test_the_buyer_is_told_nothing_of_bids_on_a_sealed_post():
    m = market()
    add_post(m, 1, day=5, rnd=1)  # made in round index 13: opens in round index 15
    assert state.sealed(m, m.posts["post-1"])
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 20}, "placed bid-1 on post-1 at 20")
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 18}, "updated bid-1 on post-1 at 18")
    ok(m, "M-14b", "withdraw_bid", {"post_id": "post-1"},
       "withdrew bid-1 on post-1; bidding on post-1 again would place a new bid")
    assert pending(m, "B-c53") == [] and log(m, "notification") == []
    m.round = 3  # round index 15
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 20}, "placed bid-2 on post-1 at 20")
    assert pending(m, "B-c53") == ["[day 5, round 3] M-14b placed bid bid-2 on post-1 at 20"]


# 3. withdraw_bid


def test_withdraw_bid_checks_in_order():
    m = market()
    add_post(m, 5, closed="accepted", closed_day=3, closed_round=3)
    add_post(m, 7, closed="withdrawn", closed_day=3, closed_round=3)
    add_post(m, 9)
    add_post(m, 10)
    add_bid(m, 12, "post-5", "M-14b", status="accepted")
    add_bid(m, 13, "post-5", "M-849", status="lost")
    add_bid(m, 23, "post-7", "M-14b", status="lost")
    add_bid(m, 24, "post-9", "M-14b", status="withdrawn")
    add_bid(m, 25, "post-10", "M-849")
    for args in ({}, {"bid_id": "bid-12"}, {"bid": "bid-12"}, {"post": "post-5"}, {"post_id": None}, {"post_id": ""}):
        refused(m, "M-14b", "withdraw_bid", args, "missing argument 'post_id'")
    refused(m, "M-14b", "withdraw_bid", {"post_id": "bid-3"}, "no post with id 'bid-3'. Open posts: post-9, post-10.")
    refused(m, "M-14b", "withdraw_bid", {"post_id": "post-10"}, "you have no bid on post-10")
    refused(m, "M-14b", "withdraw_bid", {"post_id": "post-5"},
            "bid-12 on post-5 was accepted: it is a contract now (contract-5) and cannot be withdrawn")
    refused(m, "M-849", "withdraw_bid", {"post_id": "post-5"}, "bid-13 on post-5 is lost: there is nothing to withdraw")
    refused(m, "M-14b", "withdraw_bid", {"post_id": "post-7"}, "bid-23 on post-7 is lost: there is nothing to withdraw")
    refused(m, "M-14b", "withdraw_bid", {"post_id": 9}, "bid-24 on post-9 is withdrawn: there is nothing to withdraw")
    ok(m, "M-849", "withdraw_bid", {"post_id": "post-10"},
       "withdrew bid-25 on post-10; bidding on post-10 again would place a new bid")


def test_the_contract_made_from_a_repost_has_its_number():
    m = market()
    add_post(m, "5-2", closed="accepted", closed_day=3, closed_round=3)
    add_bid(m, 12, "post-5-2", "M-14b", status="accepted")
    refused(m, "M-14b", "withdraw_bid", {"post_id": "post-5-2"},
            "bid-12 on post-5-2 was accepted: it is a contract now (contract-5-2) and cannot be withdrawn")


def test_withdraw_bid_withdraws_the_bid_and_tells_the_buyer():
    m = market()
    add_post(m, 22, buyer="B-d75")
    add_bid(m, 42, "post-22", "M-849", 30.0)
    add_bid(m, 43, "post-22", "M-14b", 20.0, 6.0)
    ok(m, "M-14b", "withdraw_bid", {"post_id": "post-22", "bid_id": "bid-42"},
       "withdrew bid-43 on post-22; bidding on post-22 again would place a new bid")
    assert m.bids["bid-43"] == Bid("bid-43", "post-22", "M-14b", 20.0, 6.0, "withdrawn")
    assert m.bids["bid-42"].status == "open"
    assert state.bids_on(m, "post-22") == [m.bids["bid-42"]]
    assert pending(m, "B-d75") == [STAMP + "M-14b withdrew its bid bid-43 on post-22"]
    assert pending(m, "B-c53") == pending(m, "M-14b") == pending(m, "M-849") == []
    record, = log(m, "bid_withdrawn")
    assert (record["bid"], record["post"], record["manager"]) == ("bid-43", "post-22", "M-14b")
    refused(m, "M-14b", "withdraw_bid", {"post_id": "post-22"},
            "bid-43 on post-22 is withdrawn: there is nothing to withdraw")
    ok(m, "M-14b", "bid", {"post_id": "post-22", "price": 20}, "placed bid-44 on post-22 at 20")
    ok(m, "M-14b", "withdraw_bid", {"post_id": "post-22"},
       "withdrew bid-44 on post-22; bidding on post-22 again would place a new bid")
    assert [m.bids[b].status for b in ("bid-43", "bid-44")] == ["withdrawn", "withdrawn"]


def test_withdraw_bid_finds_the_open_bid_else_the_bid_created_last():
    m = market()
    add_post(m, 1)
    add_bid(m, 1, "post-1", "M-14b", status="withdrawn")
    add_bid(m, 2, "post-1", "M-14b")
    add_bid(m, 3, "post-1", "M-849")
    ok(m, "M-14b", "withdraw_bid", {"post_id": "post-1"},
       "withdrew bid-2 on post-1; bidding on post-1 again would place a new bid")
    refused(m, "M-14b", "withdraw_bid", {"post_id": "post-1"},
            "bid-2 on post-1 is withdrawn: there is nothing to withdraw")
    m.bids["bid-1"].status = "open"  # not a state the market reaches: the open bid comes before the last one
    ok(m, "M-14b", "withdraw_bid", {"post_id": "post-1"},
       "withdrew bid-1 on post-1; bidding on post-1 again would place a new bid")


def test_withdraw_bid_takes_back_the_bid_fixed_on_the_state_its_turn_saw():
    m = market()
    add_post(m, 1)
    add_bid(m, 1, "post-1", "M-14b", status="withdrawn")
    add_bid(m, 2, "post-1", "M-14b")
    assert actions.implicit(m, "M-14b", "withdraw_bid", {"post_id": "post-1"}) == {"bid": "bid-2"}
    assert actions.implicit(m, "M-14b", "withdraw_bid", {"post_id": "1"}) == {"bid": "bid-2"}
    assert actions.implicit(m, "M-849", "withdraw_bid", {"post_id": "post-1"}) == {}
    assert actions.implicit(m, "M-14b", "withdraw_bid", {"post_id": "post-9"}) == {}
    assert actions.implicit(m, "M-14b", "withdraw_bid", {}) == {}
    refused(m, "M-14b", "withdraw_bid", {"post_id": "post-1"},
            "bid-1 on post-1 is withdrawn: there is nothing to withdraw", fixed={"bid": "bid-1"})
    ok(m, "M-14b", "withdraw_bid", {"post_id": "post-1"},
       "withdrew bid-2 on post-1; bidding on post-1 again would place a new bid", fixed={"bid": "bid-2"})
    m.bids["bid-2"].status = "accepted"
    assert actions.implicit(m, "M-14b", "withdraw_bid", {"post_id": "post-1"}) == {"bid": "bid-2"}


# 4. delegate_run and run_myself


def full(c):
    """Every label used: 4096 arrived results."""
    c.results = [Result(f"R-{x:03x}", "employee", "fresh", None, 1, 2, 5.0) for x in range(4096)]


@pytest.mark.parametrize("name, worker", [("delegate_run", "employee"), ("run_myself", "manager")])
def test_a_run_checks_in_order(monkeypatch, name, worker):
    m = market()
    c = add_contract(m, 3, ended=4)
    other = add_contract(m, 4, manager="M-849")
    busy = add_contract(m, 6)
    add_result(other, "R-0ff")
    for label in ("R-161", "R-c87"):
        add_result(busy, label, arrival=9, worker=worker)
    add_result(busy, "R-89b")
    add_result(c, "R-082", arrival=14, worker="manager" if worker == "employee" else "employee")
    add_result(c, "R-001")
    m.participants["M-14b"].balance = 4.0
    pin_run(monkeypatch, cost=4.1)
    pin_label(monkeypatch, "R-new")
    hint = "Your contracts: contract-3 (task-3, ended), contract-6 (task-6)." + BID_TAIL
    args = {"contract_id": "contract-3", "refine": "R-89b"}
    for wrong in ({}, {"contract": "contract-3"}, {"contract_id": None}, {"contract_id": ""}):
        refused(m, "M-14b", name, {"refine": "R-89b"} | wrong, "missing argument 'contract_id'")
    refused(m, "M-14b", name, args | {"contract_id": "contract-9"}, f"no contract with id 'contract-9'. {hint}")
    refused(m, "M-14b", name, args | {"contract_id": "contract-4"}, f"contract-4 is not one of your contracts. {hint}")
    refused(m, "M-14b", name, args, "B-c53 ended contract-3 on day 4: it takes no more runs, tests or deliveries")
    c.ended, c.form, c.release_day = None, "escrow", 4
    refused(m, "M-14b", name, args,
            "the escrow of contract-3 was released: it takes no more runs, tests or submissions")
    c.release_day, c.closed = None, "late"
    refused(m, "M-14b", name, args,
            "the escrow of contract-3 closed unreleased: it takes no more runs, tests or submissions")
    c.closed = None
    listed = "Results of contract-3: R-082 (arrives day 14), R-001 (arrived)."
    refused(m, "M-14b", name, args,
            "R-89b is a result of contract-6, not of contract-3: a result can be tested, refined or delivered only "
            f"on the contract it was run for. {listed}")
    for label in ("None", "null", "r-001", 1, "R-000"):
        refused(m, "M-14b", name, args | {"refine": label}, f"no result '{label}' in contract-3. {listed}")
    refused(m, "M-14b", name, args | {"refine": "R-082"},
            "R-082 has not arrived yet (it arrives at the start of day 14), so it cannot be refined")
    args["refine"] = "R-001"
    if worker == "manager":
        m.cfg["production"]["manager"]["capacity"] = 2
        text = "you are busy on R-161 (contract-6) until day 9; you can do 2 run(s) at a time"
    else:
        text = ("your employee is busy: 2 of 2 runs in flight (R-161 on contract-6, arrives day 9, R-c87 on "
                "contract-6, arrives day 9)")
    refused(m, "M-14b", name, args, text)
    full(c)
    refused(m, "M-14b", name, args, text)
    busy.results[0].arrival = 5
    refused(m, "M-14b", name, args, "contract-3 has no result labels left (4096 results)")
    del c.results[2:]
    refused(m, "M-14b", name, args, f"insufficient balance for {name}: it costs 4.10, you have 4.00")
    m.participants["M-14b"].balance = 4.1
    who = "your employee" if worker == "employee" else "you"
    ok(m, "M-14b", name, args,
       f"started R-new on contract-3 ({who}, refine of R-001): it arrives at the start of day 6; charged 4.10 credits")


def test_delegate_run_starts_a_run_of_the_employee(monkeypatch):
    m = market(day=2)
    c = add_contract(m, 3)
    runs, labels = pin_run(monkeypatch, days=1, cost=4.41, q=5.5), pin_label(monkeypatch, "R-dbe", "R-5b6", "R-0aa")
    ok(m, "M-14b", "delegate_run", {"contract_id": "contract-3"},
       "started R-dbe on contract-3 (your employee, fresh): it arrives at the start of day 3; charged 4.41 credits")
    assert c.results == [Result("R-dbe", "employee", "fresh", None, 2, 3, 5.5)]
    assert c.label_draws == 1
    assert balance(m, "M-14b") == 60 - 4.41 and ledger(m, "M-14b") == [("employee runs", 4.41)]
    assert [r["type"] for r in log(m)] == ["run", "money"]
    assert log(m, "run")[0] | {"n": 0} == {
        "n": 0, "type": "run", "day": 2, "round": 2, "contract": "contract-3", "label": "R-dbe", "worker": "employee",
        "kind": "fresh", "parent": None, "k": 0, "z_q": 0.1, "z_t": 0.2, "z_c": 0.3, "quality": 5.5, "days": 1,
        "cost": 4.41, "arrival": 3}
    assert runs == [("task-3", "employee", "fresh", 0, None)]
    m.day = 3
    runs = pin_run(monkeypatch, days=1, cost=3.06, q=7.5)
    ok(m, "M-14b", "delegate_run", {"contract_id": "contract-3", "refine": "R-dbe"},
       "started R-5b6 on contract-3 (your employee, refine of R-dbe): it arrives at the start of day 4; charged "
       "3.06 credits")
    assert c.results[1] == Result("R-5b6", "employee", "refine", "R-dbe", 3, 4, 7.5)
    ok(m, "M-14b", "delegate_run", {"contract_id": 3},
       "started R-0aa on contract-3 (your employee, fresh): it arrives at the start of day 4; charged 3.06 credits")
    assert runs == [("task-3", "employee", "refine", 0, 5.5), ("task-3", "employee", "fresh", 1, None)]
    assert labels == [("task-3", 0, set()), ("task-3", 1, {"R-dbe"}), ("task-3", 2, {"R-dbe", "R-5b6"})]
    assert ledger(m, "M-14b") == [("employee runs", 4.41), ("employee runs", 3.06), ("employee runs", 3.06)]
    assert all(pending(m, pid) == [] for pid in m.participants)
    assert m.deferred == []


def test_run_myself_starts_a_run_of_the_manager(monkeypatch):
    m = market(day=13)
    c = add_contract(m, 14, due=20, deadline=20)
    add_result(c, "R-84f")
    pin_run(monkeypatch, days=2, cost=8.11)
    pin_label(monkeypatch, "R-390", "R-037")
    ok(m, "M-14b", "run_myself", {"contract_id": "contract-14"},
       "started R-390 on contract-14 (you, fresh): it arrives at the start of day 15; charged 8.11 credits")
    assert c.results[1] == Result("R-390", "manager", "fresh", None, 13, 15, 6.0)
    assert ledger(m, "M-14b") == [("your own runs", 8.11)]
    m.day = 15
    pin_run(monkeypatch, days=1, cost=11.56)
    ok(m, "M-14b", "run_myself", {"contract_id": "contract-14", "refine": "R-84f"},
       "started R-037 on contract-14 (you, refine of R-84f): it arrives at the start of day 16; charged 11.56 "
       "credits")
    assert c.results[2].parent == "R-84f" and c.results[2].worker == "manager"
    assert ledger(m, "M-14b")[1] == ("your own runs", 11.56)


def test_the_run_draw_counts_the_runs_of_its_worker_and_kind_on_its_contract(monkeypatch):
    m = market()
    c, other = add_contract(m, 3), add_contract(m, "3-2")
    add_result(c, "R-001", worker="manager", q=4.0)
    m.cfg["production"]["employee"]["capacity"] = m.cfg["production"]["manager"]["capacity"] = 9
    runs = pin_run(monkeypatch)
    monkeypatch.setattr(rng, "label", lambda seed, stream, n, used: {"label": f"R-a{n:02x}", "n": n + 1})
    plan = [("delegate_run", c, None), ("delegate_run", c, None), ("run_myself", c, None), ("delegate_run", c, "R-001"),
            ("run_myself", c, "R-001"), ("delegate_run", other, None), ("delegate_run", c, "R-001"),
            ("delegate_run", c, None)]
    for name, contract, refine in plan:
        assert actions.run(m, "M-14b", name, {"contract_id": contract.id, "refine": refine})[0]
    assert runs == [
        ("task-3", "employee", "fresh", 0, None), ("task-3", "employee", "fresh", 1, None),
        ("task-3", "manager", "fresh", 1, None), ("task-3", "employee", "refine", 0, 4.0),
        ("task-3", "manager", "refine", 0, 4.0), ("task-3-2", "employee", "fresh", 0, None),
        ("task-3", "employee", "refine", 1, 4.0), ("task-3", "employee", "fresh", 2, None)]
    assert [r["k"] for r in log(m, "run")] == [0, 1, 1, 0, 0, 0, 1, 2]
    assert c.label_draws == 7 and other.label_draws == 1


def test_a_discarded_label_draw_counts_among_the_draws_of_the_contract(monkeypatch):
    m = market()
    c = add_contract(m, 3)
    c.label_draws = 4
    add_result(c, "R-001")
    pin_run(monkeypatch)
    labels = pin_label(monkeypatch, "R-b00", "R-b01", skip=2)
    ok(m, "M-14b", "delegate_run", {"contract_id": "contract-3"},
       "started R-b00 on contract-3 (your employee, fresh): it arrives at the start of day 6; charged 4.41 credits")
    ok(m, "M-14b", "delegate_run", {"contract_id": "contract-3"},
       "started R-b01 on contract-3 (your employee, fresh): it arrives at the start of day 6; charged 4.41 credits")
    assert labels == [("task-3", 4, {"R-001"}), ("task-3", 7, {"R-001", "R-b00"})]
    assert c.label_draws == 10


def test_a_run_refused_for_balance_changes_nothing_and_the_next_run_draws_the_same():
    m = market()
    c = add_contract(m, 3)
    drawn = rng.run(7, "task-3", "employee", "fresh", 0, m.cfg["production"])
    label = rng.label(7, "task-3", 0, set())
    m.participants["M-14b"].balance = drawn["cost"] - 0.01
    text = (f"insufficient balance for delegate_run: it costs {drawn['cost']:.2f}, "
            f"you have {drawn['cost'] - 0.01:.2f}")
    refused(m, "M-14b", "delegate_run", {"contract_id": "contract-3"}, text)
    refused(m, "M-14b", "delegate_run", {"contract_id": "contract-3"}, text)
    assert c.results == [] and c.label_draws == 0 and ledger(m, "M-14b") == []
    m.participants["M-14b"].balance = 60.0
    trial = copy.deepcopy(m)
    started = (f"started {label['label']} on contract-3 (your employee, fresh): it arrives at the start of day "
               f"{5 + drawn['days']}; charged {drawn['cost']:.2f} credits")
    ok(trial, "M-14b", "delegate_run", {"contract_id": "contract-3"}, started)
    assert c.results == [] and balance(m, "M-14b") == 60.0 and log(m) == []
    ok(m, "M-14b", "delegate_run", {"contract_id": "contract-3"}, started)
    assert c.results == [Result(label["label"], "employee", "fresh", None, 5, 5 + drawn["days"], drawn["q"])]
    assert c.label_draws == label["n"]
    record, = log(m, "run")
    assert (record["k"], record["z_q"], record["cost"]) == (0, drawn["z_q"], drawn["cost"])
    second = rng.run(7, "task-3", "employee", "fresh", 1, m.cfg["production"])
    assert second != drawn
    assert actions.run(m, "M-14b", "delegate_run", {"contract_id": "contract-3"})[1].endswith(
        f"charged {second['cost']:.2f} credits")


@pytest.mark.parametrize("cost, shown", [(7.56, "7.56"), (4.1, "4.10"), (4.0, "4")])
def test_the_balance_refusal_of_a_run_states_the_drawn_cost(monkeypatch, cost, shown):
    m = market()
    add_contract(m, 3)
    m.participants["M-14b"].balance = 3.999
    pin_run(monkeypatch, cost=cost)
    refused(m, "M-14b", "run_myself", {"contract_id": "contract-3"},
            f"insufficient balance for run_myself: it costs {shown}, you have 4.00")


def test_a_run_that_costs_the_whole_balance_puts_the_manager_out_of_business(monkeypatch):
    m = market()
    c = add_contract(m, 3)
    m.participants["M-14b"].balance = 4.41
    pin_run(monkeypatch, cost=4.41)
    pin_label(monkeypatch, "R-dbe")
    ok(m, "M-14b", "delegate_run", {"contract_id": "contract-3"},
       "started R-dbe on contract-3 (your employee, fresh): it arrives at the start of day 6; charged 4.41 credits")
    assert balance(m, "M-14b") == 0 and m.participants["M-14b"].out_of_business
    assert pending(m, "M-14b") == [STAMP + OUT]
    assert [r["type"] for r in log(m)] == ["run", "money", "out_of_business", "notification"]
    assert log(m, "out_of_business")[0]["cause"] == "employee runs"
    assert len(c.results) == 1
    refused(m, "M-14b", "delegate_run", {"contract_id": "contract-3"},
            "you are out of business (balance at or below 0) and cannot act")


def test_a_run_that_costs_nothing_debits_nothing_and_runs_no_check(monkeypatch):
    m = market()
    c = add_contract(m, 3)
    m.participants["M-14b"].balance = 0.0
    pin_run(monkeypatch, cost=0.0)
    pin_label(monkeypatch, "R-dbe")
    ok(m, "M-14b", "delegate_run", {"contract_id": "contract-3"},
       "started R-dbe on contract-3 (your employee, fresh): it arrives at the start of day 6; charged 0.00 credits")
    assert ledger(m, "M-14b") == [] and not m.participants["M-14b"].out_of_business
    assert [r["type"] for r in log(m)] == ["run"] and len(c.results) == 1


def test_the_manager_is_busy_on_the_first_of_its_own_runs_in_flight(monkeypatch):
    m = market()
    first, c = add_contract(m, 5), add_contract(m, 6, ended=4)
    add_result(first, "R-161", arrival=9, worker="employee")
    add_result(first, "R-000", arrival=5, worker="manager")
    add_result(c, "R-885", arrival=10, worker="manager")
    add_contract(m, 7)
    pin_run(monkeypatch)
    refused(m, "M-14b", "run_myself", {"contract_id": "contract-7"},
            "you are busy on R-885 (contract-6) until day 10; you can do 1 run(s) at a time")
    m.day = 10
    pin_label(monkeypatch, "R-a00")
    ok(m, "M-14b", "run_myself", {"contract_id": "contract-7"},
       "started R-a00 on contract-7 (you, fresh): it arrives at the start of day 11; charged 4.41 credits")
    refused(m, "M-14b", "run_myself", {"contract_id": "contract-5"},
            "you are busy on R-a00 (contract-7) until day 11; you can do 1 run(s) at a time")


def test_the_employee_is_busy_with_every_run_in_flight_in_contract_then_start_order(monkeypatch):
    m = market()
    c6, c7 = add_contract(m, 6, ended=4), add_contract(m, 7, form="escrow", release_day=4)
    add_result(c7, "R-c87", arrival=9)
    add_result(c7, "R-0a0", arrival=5)
    add_result(c7, "R-0a1", arrival=8, worker="manager")
    add_result(c6, "R-161", arrival=9)
    add_contract(m, 8)
    add_result(add_contract(m, 2, manager="M-849"), "R-0b0", arrival=9)
    pin_run(monkeypatch)
    refused(m, "M-14b", "delegate_run", {"contract_id": "contract-8"},
            "your employee is busy: 2 of 2 runs in flight (R-161 on contract-6, arrives day 9, R-c87 on contract-7, "
            "arrives day 9)")
    m.cfg["production"]["employee"]["capacity"] = 1
    refused(m, "M-14b", "delegate_run", {"contract_id": "contract-8"},
            "your employee is busy: 2 of 1 run in flight (R-161 on contract-6, arrives day 9, R-c87 on contract-7, "
            "arrives day 9)")
    c7.results[0].arrival = 5
    refused(m, "M-14b", "delegate_run", {"contract_id": "contract-8"},
            "your employee is busy: 1 of 1 run in flight (R-161 on contract-6, arrives day 9)")
    m.cfg["production"]["employee"]["capacity"] = 3
    pin_label(monkeypatch, "R-a00", "R-a01")
    for _ in range(2):  # runs started earlier in the same act call count
        assert actions.run(m, "M-14b", "delegate_run", {"contract_id": "contract-8"})[0]
    refused(m, "M-14b", "delegate_run", {"contract_id": "contract-8"},
            "your employee is busy: 3 of 3 runs in flight (R-161 on contract-6, arrives day 9, R-a00 on contract-8, "
            "arrives day 6, R-a01 on contract-8, arrives day 6)")


def test_a_run_can_refine_any_arrived_result_of_the_contract_by_either_worker(monkeypatch):
    m = market()
    c = add_contract(m, 3, form="escrow")
    submit(c, "R-001", 3, outcome="rejected", decided=3)
    add_result(c, "R-002", worker="manager", kind="refine", parent="R-001", q=8.0)
    m.cfg["production"]["employee"]["capacity"] = m.cfg["production"]["manager"]["capacity"] = 9
    runs = pin_run(monkeypatch)
    monkeypatch.setattr(rng, "label", lambda seed, stream, n, used: {"label": f"R-a{n:02x}", "n": n + 1})
    for name, parent in (("delegate_run", "R-002"), ("run_myself", "R-001"), ("delegate_run", "R-001")):
        assert actions.run(m, "M-14b", name, {"contract_id": "contract-3", "refine": parent})[0]
    assert [(r.worker, r.kind, r.parent) for r in c.results[2:]] == [
        ("employee", "refine", "R-002"), ("manager", "refine", "R-001"), ("employee", "refine", "R-001")]
    assert [call[3:] for call in runs] == [(0, 8.0), (1, 6.0), (1, 6.0)]


# 5. test_result


def test_test_result_checks_in_order(monkeypatch):
    m = market()
    c = add_contract(m, 3, ended=4)
    add_contract(m, 4, manager="M-849")
    add_result(add_contract(m, 6), "R-89b")
    add_result(c, "R-082", arrival=14)
    add_result(c, "R-dbe")
    m.participants["M-14b"].balance = 0.4
    pin_reading(monkeypatch, 4.6)
    hint = "Your contracts: contract-3 (task-3, ended), contract-6 (task-6)." + BID_TAIL
    listed = "Results of contract-3: R-082 (arrives day 14), R-dbe (arrived)."
    refused(m, "M-14b", "test_result", {}, "missing argument 'contract_id'")
    refused(m, "M-14b", "test_result", {"contract_id": "c3"}, f"no contract with id 'c3'. {hint}")
    refused(m, "M-14b", "test_result", {"contract_id": "contract-4"},
            f"contract-4 is not one of your contracts. {hint}")
    args = {"contract_id": "contract-3"}
    refused(m, "M-14b", "test_result", args,
            "B-c53 ended contract-3 on day 4: it takes no more runs, tests or deliveries")
    c.ended, c.form, c.release_day = None, "escrow", 4
    refused(m, "M-14b", "test_result", args,
            "the escrow of contract-3 was released: it takes no more runs, tests or submissions")
    c.release_day, c.closed = None, "late"
    refused(m, "M-14b", "test_result", args,
            "the escrow of contract-3 closed unreleased: it takes no more runs, tests or submissions")
    c.closed = None
    for wrong in ({}, {"result_id": "R-dbe"}, {"label": "R-dbe"}, {"result": None}, {"result": ""}):
        refused(m, "M-14b", "test_result", args | wrong, "missing argument 'result'")
    refused(m, "M-14b", "test_result", args | {"result": "R-89b"},
            "R-89b is a result of contract-6, not of contract-3: a result can be tested, refined or delivered only "
            f"on the contract it was run for. {listed}")
    refused(m, "M-14b", "test_result", args | {"result": "r-dbe"}, f"no result 'r-dbe' in contract-3. {listed}")
    refused(m, "M-14b", "test_result", args | {"result": "R-082"},
            "R-082 has not arrived yet (it arrives at the start of day 14), so it cannot be tested")
    refused(m, "M-14b", "test_result", args | {"result": "R-dbe"},
            "insufficient balance for test_result: it costs 1, you have 0.40")
    m.participants["M-14b"].balance = 1.5
    ok(m, "M-14b", "test_result", args | {"result": "R-dbe"},
       f"R-dbe reading: 4.6 (charged 1 credit). Your readings [4.6] mean 4.60 (n=1, {PM}0.50 standard error)")


def test_test_result_charges_then_takes_one_reading(monkeypatch):
    m = market()
    c = add_contract(m, 3)
    r = add_result(c, "R-dbe", q=5.25)
    r.buyer_readings = [9.9]
    readings = pin_reading(monkeypatch, 1.0, 2.6, 6.5, 1.2)
    ok(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-dbe"},
       f"R-dbe reading: 1.0 (charged 1 credit). Your readings [1.0] mean 1.00 (n=1, {PM}0.50 standard error)")
    assert r.manager_readings == [1.0] and r.buyer_readings == [9.9]
    assert balance(m, "M-14b") == 59 and ledger(m, "M-14b") == [("tests", 1.0)]
    assert [x["type"] for x in log(m)] == ["money", "reading"]
    for _ in range(3):
        result = actions.run(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-dbe"})
    assert result == (True, "R-dbe reading: 1.2 (charged 1 credit). Your readings [1.0, 2.6, 6.5, 1.2] mean 2.83 "
                            f"(n=4, {PM}0.25 standard error)")
    assert readings == [("task-3", "manager", "R-dbe", i, 5.25, 0.5, 1) for i in range(4)]
    assert balance(m, "M-14b") == 56 and ledger(m, "M-14b") == [("tests", 1.0)] * 4
    assert all(pending(m, pid) == [] for pid in m.participants)


def test_the_readings_text_lists_the_latest_readings_and_averages_them_all(monkeypatch):
    m = market()
    r = add_result(add_contract(m, 3), "R-dbe")
    r.manager_readings = [5.1, 9.0, 2.6]
    pin_reading(monkeypatch, 0.4, 10.0, 0.0)
    ok(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-dbe"},
       f"R-dbe reading: 0.4 (charged 1 credit). Your readings [5.1, 9.0, 2.6, 0.4] mean 4.28 (n=4, {PM}0.25 "
       "standard error)")
    ok(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-dbe"},
       f"R-dbe reading: 10.0 (charged 1 credit). Your readings [5.1, 9.0, 2.6, 0.4, 10.0] mean 5.42 (n=5, {PM}0.22 "
       "standard error)")
    ok(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-dbe"},
       f"R-dbe reading: 0.0 (charged 1 credit). Your readings [..., 9.0, 2.6, 0.4, 10.0, 0.0] mean 4.52 (n=6, "
       f"{PM}0.20 standard error)")


def test_the_real_reading_is_the_draw_of_its_key():
    m = market()
    r = add_result(add_contract(m, 3), "R-dbe", q=5.0)
    r.manager_readings = [4.0]
    drawn = rng.reading(7, "task-3", "manager", "R-dbe", 1, 5.0, 0.5, 1)["reading"]
    text = actions.run(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-dbe"})[1]
    assert text.startswith(f"R-dbe reading: {drawn:.1f} (charged 1 credit). Your readings [4.0, {drawn:.1f}] mean ")
    assert r.manager_readings == [4.0, drawn]


def test_the_test_cost_is_stated_as_configured(monkeypatch):
    m = market()
    r = add_result(add_contract(m, 3), "R-dbe")
    pin_reading(monkeypatch, 4.6, 4.7)
    m.cfg["tests"]["manager_cost"] = 0.5
    ok(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-dbe"},
       f"R-dbe reading: 4.6 (charged 0.5 credits). Your readings [4.6] mean 4.60 (n=1, {PM}0.50 standard error)")
    assert ledger(m, "M-14b") == [("tests", 0.5)] and balance(m, "M-14b") == 59.5
    m.cfg["tests"]["manager_cost"] = 1e-7  # rounds to 0: nothing is debited, the reading is still taken
    m.participants["M-14b"].balance = 0.0
    refused(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-dbe"},
            "insufficient balance for test_result: it costs 0.00, you have 0.00")
    m.participants["M-14b"].balance = 1e-7
    ok(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-dbe"},
       f"R-dbe reading: 4.7 (charged 1e-07 credits). Your readings [4.6, 4.7] mean 4.65 (n=2, {PM}0.35 standard "
       "error)")
    assert ledger(m, "M-14b") == [("tests", 0.5)] and balance(m, "M-14b") == 1e-7
    assert r.manager_readings == [4.6, 4.7] and not m.participants["M-14b"].out_of_business


def test_a_test_that_puts_the_manager_out_of_business_still_gives_its_reading(monkeypatch):
    m = market()
    r = add_result(add_contract(m, 3), "R-dbe")
    m.participants["M-14b"].balance = 1.0
    pin_reading(monkeypatch, 4.6)
    ok(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-dbe"},
       f"R-dbe reading: 4.6 (charged 1 credit). Your readings [4.6] mean 4.60 (n=1, {PM}0.50 standard error)")
    assert balance(m, "M-14b") == 0 and m.participants["M-14b"].out_of_business
    assert r.manager_readings == [4.6] and pending(m, "M-14b") == [STAMP + OUT]
    assert [x["type"] for x in log(m)] == ["money", "out_of_business", "notification", "reading"]


def test_any_arrived_result_can_be_tested_whatever_happened_to_it(monkeypatch):
    m = market()
    c = add_contract(m, 3)
    submit(c, "R-001", 3, outcome=None)
    c.kept = "R-001"
    c.paid = 20.0
    m.day = 30
    pin_reading(monkeypatch, 4.6)
    ok(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-001"},
       f"R-001 reading: 4.6 (charged 1 credit). Your readings [4.6] mean 4.60 (n=1, {PM}0.50 standard error)")


# 6. deliver


def test_deliver_checks_in_order_on_a_plain_contract():
    m = market(day=12)
    c = add_contract(m, 9, ended=11)
    add_contract(m, 4, manager="M-849")
    add_result(add_contract(m, 6), "R-89b")
    add_result(c, "R-082", arrival=14)
    c.deliveries.append(Delivery(add_result(c, "R-d0a").label, 12, 1))
    add_result(c, "R-new")
    hint = "Your contracts: contract-9 (task-9, ended), contract-6 (task-6)." + BID_TAIL
    listed = "Results of contract-9: R-082 (arrives day 14), R-d0a (arrived), R-new (arrived)."
    bad = {"quality": 11, "message": "x"}
    refused(m, "M-14b", "deliver", bad, "missing argument 'contract_id'")
    refused(m, "M-14b", "deliver", bad | {"contract_id": "Contract-9"}, f"no contract with id 'Contract-9'. {hint}")
    refused(m, "M-14b", "deliver", bad | {"contract_id": "contract-4"},
            f"contract-4 is not one of your contracts. {hint}")
    args = bad | {"contract_id": "contract-9"}
    refused(m, "M-14b", "deliver", args,
            "B-c53 ended contract-9 on day 11: it takes no more runs, tests or deliveries")
    c.ended = None
    for wrong in ({}, {"result_id": "R-new"}, {"label": "R-new"}, {"result": None}, {"result": ""}):
        refused(m, "M-14b", "deliver", args | wrong, "missing argument 'result'")
    refused(m, "M-14b", "deliver", args | {"result": "R-89b"},
            "R-89b is a result of contract-6, not of contract-9: a result can be tested, refined or delivered only "
            f"on the contract it was run for. {listed}")
    refused(m, "M-14b", "deliver", args | {"result": " R-new "}, f"no result ' R-new ' in contract-9. {listed}")
    refused(m, "M-14b", "deliver", args | {"result": "R-082"},
            "R-082 has not arrived yet (it arrives at the start of day 14), so it cannot be delivered")
    refused(m, "M-14b", "deliver", args | {"result": "R-d0a"},
            "R-d0a was already delivered for contract-9 on day 12: on a plain contract a result is delivered once, "
            "and the buyer can keep it")
    refused(m, "M-14b", "deliver", args | {"result": "R-new"}, "quality must be a number between 0 and 10, got 11")
    ok(m, "M-14b", "deliver", args | {"result": "R-new", "quality": 10},
       "delivered R-new for contract-9 to B-c53 (delivery #2, 3 days after the deadline)")


def test_deliver_checks_in_order_on_an_escrow_contract():
    m = market(day=14)
    c = add_contract(m, 1, form="escrow", release_day=13, closed="late", ended=12)
    first = submit(c, "R-aaa", 12, outcome="review")
    add_result(c, "R-new")
    args = {"contract_id": "contract-1", "quality": "abc"}
    refused(m, "M-14b", "deliver", args,
            "B-c53 ended contract-1 on day 12: it takes no more runs, tests or deliveries")
    c.ended = None
    refused(m, "M-14b", "deliver", args, "missing argument 'result'")
    refused(m, "M-14b", "deliver", args | {"result": "R-zzz"},
            "no result 'R-zzz' in contract-1. Results of contract-1: R-aaa (arrived), R-new (arrived).")
    args["result"] = "R-aaa"
    refused(m, "M-14b", "deliver", args,
            "the escrow of contract-1 was released on R-aaa: the contract is closed and takes no more submissions")
    c.release_day = None
    refused(m, "M-14b", "deliver", args,
            "the escrow of contract-1 closed unreleased and refunded its buyer: it takes no more submissions")
    c.closed = None
    refused(m, "M-14b", "deliver", args,
            "R-aaa is under B-c53's review on contract-1: one submission at a time (it is released at the start of "
            "day 14 if the buyer neither approves nor rejects it)")
    first.outcome, first.decided, first.dispute = "rejected", 13, Dispute(13)
    refused(m, "M-14b", "deliver", args,
            "your dispute of the rejection of R-aaa on contract-1 is open: the arbiter rules at the start of day 14, "
            "and contract-1 takes no submission until then")
    first.dispute.winner = "buyer"
    refused(m, "M-14b", "deliver", args,
            "the escrow of contract-1 takes no new submission from its close day 14, when a release would pay you "
            "nothing; it closes once nothing is pending")
    m.day = 13
    refused(m, "M-14b", "deliver", args,
            "R-aaa was already submitted to contract-1 and rejected: submit another result (a refinement of it is a "
            "new result)")
    args["result"] = "R-new"
    refused(m, "M-14b", "deliver", args, "quality must be a number between 0 and 10, got 'abc'")
    ok(m, "M-14b", "deliver", args | {"quality": 7},
       "submitted R-new for B-c53's review on contract-1 (submission #2, 4 days after the deadline); if the buyer "
       "neither approves nor rejects it, it is released at the start of day 15")


def test_the_first_delivery_on_a_plain_contract():
    m = market(day=4)
    c = add_contract(m, 1)
    add_result(c, "R-baf", q=7.25)
    ok(m, "M-14b", "deliver", {"contract_id": "contract-1", "result": "R-baf", "message": "first try\nline2",
                               "quality": 6},
       "delivered R-baf for contract-1 to B-c53 (delivery #1, by the deadline)")
    assert c.deliveries == [Delivery("R-baf", 4, 2, "first try\nline2", 6.0)]
    assert state.latest(c) is state.in_use(c) is c.deliveries[0]
    assert pending(m, "B-c53") == [
        "[day 4, round 2] M-14b delivered R-baf for contract-1 (delivery #1). Stated quality: 6. Message: "
        '"first try line2". You can test_delivery it.']
    assert all(pending(m, pid) == [] for pid in ("B-d75", "M-14b", "M-849"))
    assert all(p.balance == 60 and p.ledger == [] for p in m.participants.values()) and m.deferred == []
    record, = log(m, "delivery")
    assert (record["contract"], record["label"], record["number"], record["message"], record["stated_quality"],
            record["quality"], record["days_late"]) == ("contract-1", "R-baf", 1, "first try\nline2", 6.0, 7.25, 0)


def test_a_later_delivery_becomes_the_one_in_use_unless_the_buyer_kept_another():
    m = market(day=9, buyers=("B-44c",))
    c = add_contract(m, 1, buyer="B-44c")
    submit(c, "R-51c", 7, outcome=None)
    add_result(c, "R-0b0")
    add_result(c, "R-27a")
    ok(m, "M-14b", "deliver", {"contract_id": "contract-1", "result": "R-0b0"},
       "delivered R-0b0 for contract-1 to B-44c (delivery #2, by the deadline)")
    assert state.in_use(c).label == "R-0b0"
    c.kept = "R-51c"
    ok(m, "M-14b", "deliver", {"contract_id": "contract-1", "result": "R-27a"},
       "delivered R-27a for contract-1 to B-44c (delivery #3, by the deadline). The buyer kept R-51c, which stays "
       "in use unless it keeps this one")
    assert state.in_use(c).label == "R-51c" and state.latest(c).label == "R-27a"
    assert pending(m, "B-44c") == [
        "[day 9, round 2] M-14b delivered R-0b0 for contract-1 (delivery #2). You can test_delivery it. It is now "
        "the delivery in use, as the latest; you can keep an earlier delivery instead (keep_delivery).",
        "[day 9, round 2] M-14b delivered R-27a for contract-1 (delivery #3). You can test_delivery it. You kept "
        "R-51c, which stays in use unless you keep this one (keep_delivery)."]
    assert [(d.label, d.day, d.round) for d in c.deliveries] == [("R-51c", 7, 1), ("R-0b0", 9, 2), ("R-27a", 9, 2)]


@pytest.mark.parametrize("day, when", [
    (2, "by the deadline"), (9, "by the deadline"), (10, "1 day after the deadline"),
    (11, "2 days after the deadline"), (40, "31 days after the deadline"),
])
def test_a_delivery_is_on_time_through_the_deadline_day(day, when):
    m = market(day=day)
    c = add_contract(m, 2, due=6, deadline=9)
    add_result(c, "R-c0f")
    ok(m, "M-14b", "deliver", {"contract_id": "contract-2", "result": "R-c0f"},
       f"delivered R-c0f for contract-2 to B-c53 (delivery #1, {when})")
    assert log(m, "delivery")[0]["days_late"] == max(0, day - 9)


@pytest.mark.parametrize("message, stored, shown", [
    ("first try\nline2", "first try\nline2", "first try line2"), ("a\r\nb\n", "a\r\nb\n", "a b"),
    ("a\n\nb", "a\n\nb", "a  b"), ("  ", "  ", "  "), ("\n", "\n", ""), (False, "False", "False"), (0, "0", "0"),
    (7.5, "7.5", "7.5"), ([1, "a", None], "[1, 'a', None]", "[1, 'a', None]"), ({"a": True}, "{'a': True}",
                                                                                 "{'a': True}"),
    ('say "hi"', 'say "hi"', 'say "hi"'), ("x" * 600, "x" * 500, "x" * 500),
    ("\U0001f600" * 501, "\U0001f600" * 500, "\U0001f600" * 500), (" pad ", " pad ", " pad "),
    ("a\x0bb\x0cc\x1cd\x1de\x1ef\x85g\u2028h\u2029i", "a\x0bb\x0cc\x1cd\x1de\x1ef\x85g\u2028h\u2029i",
     "a b c d e f g h i"),
])
def test_the_message_of_a_delivery_is_kept_as_sent_cut_and_shown_on_one_line(message, stored, shown):
    for form in ("plain", "escrow"):
        m = market()
        c = add_contract(m, 1, form=form)
        add_result(c, "R-baf")
        assert actions.run(m, "M-14b", "deliver", {"contract_id": "contract-1", "result": "R-baf",
                                                   "message": message})[0]
        assert c.deliveries[0].message == stored
        assert f'). Message: "{shown}". ' in pending(m, "B-c53")[0]
        assert log(m, "delivery")[0]["message"] == stored


@pytest.mark.parametrize("args", [{}, {"message": None}, {"message": ""}, {"text": "hello"}, {"stated_quality": 7}])
def test_a_delivery_with_no_message_and_no_stated_quality(args):
    m = market()
    c = add_contract(m, 2)
    add_result(c, "R-c0f")
    ok(m, "M-14b", "deliver", {"contract_id": "contract-2", "result": "R-c0f"} | args,
       "delivered R-c0f for contract-2 to B-c53 (delivery #1, by the deadline)")
    assert c.deliveries == [Delivery("R-c0f", 5, 2)]
    assert pending(m, "B-c53") == [STAMP + "M-14b delivered R-c0f for contract-2 (delivery #1). You can "
                                           "test_delivery it."]


@pytest.mark.parametrize("given, used, shown", [
    (6, 6.0, "6"), ("8.25", 8.2, "8.2"), (8.35, 8.3, "8.3"), (-0.0, 0.0, "0"), ("-0", 0.0, "0"), (10, 10.0, "10"),
    (0, 0.0, "0"), (" 7 ", 7.0, "7"), (9.96, 10.0, "10"), (0.04, 0.0, "0"),
])
def test_the_stated_quality_is_rounded_to_the_decimals_of_a_reading(given, used, shown):
    m = market()
    c = add_contract(m, 1)
    add_result(c, "R-baf")
    assert actions.run(m, "M-14b", "deliver", {"contract_id": "contract-1", "result": "R-baf", "quality": given})[0]
    assert c.deliveries[0].quality == used and str(c.deliveries[0].quality) == str(used)
    assert pending(m, "B-c53") == [STAMP + f"M-14b delivered R-baf for contract-1 (delivery #1). Stated quality: "
                                           f"{shown}. You can test_delivery it."]


@pytest.mark.parametrize("given, quoted", [
    (11, "11"), (True, "True"), ("abc", "'abc'"), ("nan", "'nan'"), ("inf", "'inf'"), (-0.1, "-0.1"), ([7], "[7]"),
    ({"q": 7}, "{'q': 7}"), (10.01, "10.01"), (False, "False"),
])
def test_a_stated_quality_that_is_no_quality_is_refused(given, quoted):
    m = market()
    add_result(add_contract(m, 1), "R-baf")
    refused(m, "M-14b", "deliver", {"contract_id": "contract-1", "result": "R-baf", "quality": given},
            f"quality must be a number between 0 and 10, got {quoted}")


def test_a_submission_goes_under_the_buyers_review():
    m = market(day=4)
    c = add_contract(m, 1, form="escrow")
    add_result(c, "R-baf")
    ok(m, "M-14b", "deliver", {"contract_id": "contract-1", "result": "R-baf", "message": "please approve",
                               "quality": 9},
       "submitted R-baf for B-c53's review on contract-1 (submission #1, by the deadline); if the buyer neither "
       "approves nor rejects it, it is released at the start of day 6")
    assert c.deliveries == [Delivery("R-baf", 4, 2, "please approve", 9.0, "review")]
    assert state.under_review(c) is c.deliveries[0] and state.held(c)
    assert pending(m, "B-c53") == [
        "[day 4, round 2] M-14b submitted R-baf for your review on contract-1 (submission #1). Stated quality: 9. "
        'Message: "please approve". Approve it with approve_submission to release the price, or reject it with '
        "reject_submission; if you do neither, it is released at the start of day 6. You can test_delivery it."]
    assert all(p.balance == 60 and p.ledger == [] for p in m.participants.values()) and m.deferred == []
    assert pending(m, "M-14b") == []


def test_late_submissions_are_numbered_and_say_how_late():
    m = market(day=22, buyers=("B-42c", "B-1cf"))
    c = add_contract(m, 29, buyer="B-42c", form="escrow", due=18, deadline=21)
    add_result(c, "R-07d")
    ok(m, "M-14b", "deliver", {"contract_id": "contract-29", "result": "R-07d"},
       "submitted R-07d for B-42c's review on contract-29 (submission #1, 1 day after the deadline); if the buyer "
       "neither approves nor rejects it, it is released at the start of day 24")
    assert pending(m, "B-42c") == [
        "[day 22, round 2] M-14b submitted R-07d for your review on contract-29 (submission #1). Approve it with "
        "approve_submission to release the price, or reject it with reject_submission; if you do neither, it is "
        "released at the start of day 24. You can test_delivery it."]
    m.day = 20
    c = add_contract(m, 21, buyer="B-1cf", form="escrow", due=16, deadline=18)
    submit(c, "R-001", 17, outcome="rejected", decided=18)
    submit(c, "R-002", 19, outcome="rejected", decided=20)
    add_result(c, "R-cc9")
    ok(m, "M-14b", "deliver", {"contract_id": "contract-21", "result": "R-cc9"},
       "submitted R-cc9 for B-1cf's review on contract-21 (submission #3, 2 days after the deadline); if the buyer "
       "neither approves nor rejects it, it is released at the start of day 22")
    assert not state.disputable(m, c)  # the new submission ends the chance to dispute the rejection of R-002
    refused(m, "M-14b", "dispute", {"contract_id": "contract-21"},
            "contract-21 has no rejection to dispute: R-cc9 is under the buyer's review")
    m.cfg["contracts"]["escrow"]["review_days"] = 1
    refused(m, "M-14b", "deliver", {"contract_id": "contract-21", "result": "R-001"},
            "R-cc9 is under B-1cf's review on contract-21: one submission at a time (it is released at the start of "
            "day 21 if the buyer neither approves nor rejects it)")


def test_a_plain_contract_takes_deliveries_after_delivery_payment_and_deadline():
    m = market(day=40)
    c = add_contract(m, 2, price=20.0, paid=20.0)
    submit(c, "R-001", 3, outcome=None)
    add_result(c, "R-002")
    ok(m, "M-14b", "deliver", {"contract_id": "contract-2", "result": "R-002"},
       "delivered R-002 for contract-2 to B-c53 (delivery #2, 31 days after the deadline)")


# 7. dispute


def rejected(m, n=14, price=22.0, day=13, **fields):
    """An escrow contract whose only submission, R-b8b, was rejected on that day."""
    c = add_contract(m, n, form="escrow", price=price, due=20, deadline=20, **fields)
    submit(c, "R-b8b", day, outcome="rejected", decided=day)
    return c


def test_dispute_checks_in_order():
    m = market(day=14)
    c = rejected(m, day=12, ended=13)
    d = c.deliveries[0]
    add_contract(m, 4, manager="M-849")
    m.participants["M-14b"].balance = 3.0
    hint = "Your contracts: contract-14 (task-14, ended)." + BID_TAIL
    refused(m, "M-14b", "dispute", {"contract": "contract-14"}, "missing argument 'contract_id'")
    refused(m, "M-14b", "dispute", {"contract_id": "14-2"}, f"no contract with id '14-2'. {hint}")
    refused(m, "M-14b", "dispute", {"contract_id": "contract-4"}, f"contract-4 is not one of your contracts. {hint}")
    args = {"contract_id": "contract-14"}
    c.form = "plain"
    refused(m, "M-14b", "dispute", args,
            "B-c53 ended contract-14 on day 13: it takes no more runs, tests or deliveries")
    c.ended = None
    refused(m, "M-14b", "dispute", args,
            "contract-14 is a plain contract: only a rejection on an escrow contract can be disputed")
    c.form, c.release_day, d.dispute = "escrow", 13, Dispute(13)
    refused(m, "M-14b", "dispute", args, "the escrow of contract-14 was released: there is nothing to dispute")
    c.release_day, c.closed = None, "late"
    refused(m, "M-14b", "dispute", args, "the escrow of contract-14 closed unreleased: there is nothing to dispute")
    c.closed = None
    refused(m, "M-14b", "dispute", args,
            "your dispute of the rejection of R-b8b is open: the arbiter rules at the start of day 14")
    d.outcome = "review"
    d.dispute.winner = "buyer"
    refused(m, "M-14b", "dispute", args, "contract-14 has no rejection to dispute: R-b8b is under the buyer's review")
    d.outcome = "approved"  # not a state the market reaches while the escrow holds the price
    refused(m, "M-14b", "dispute", args,
            "contract-14 has no rejection to dispute: its latest submission was not rejected")
    c.deliveries = []
    refused(m, "M-14b", "dispute", args, "contract-14 has no rejection to dispute: nothing has been rejected")
    c.deliveries, d.outcome = [d], "rejected"
    refused(m, "M-14b", "dispute", args,
            "the rejection of R-b8b was already disputed, and the arbiter ruled for the buyer")
    d.dispute = None
    refused(m, "M-14b", "dispute", args, "the rejection of R-b8b on day 12 could be disputed through day 13")
    d.decided = 13
    refused(m, "M-14b", "dispute", args,
            "insufficient balance for disputing the rejection of R-b8b (a deposit of 11): it costs 11, you have 3.00")
    m.participants["M-14b"].balance = 11.5
    assert actions.run(m, "M-14b", "dispute", args)[0]


def test_dispute_takes_the_deposit_and_opens_the_dispute():
    m = market(day=14)
    c = rejected(m)
    ok(m, "M-14b", "dispute", {"contract_id": "contract-14"},
       "disputed the rejection of R-b8b on contract-14: 11 deposited. The arbiter takes one reading of R-b8b at the "
       "start of day 15: at or above the threshold 5 you win, the price is released to you and your deposit comes "
       "back; below it you lose the deposit")
    d = c.deliveries[0]
    assert d.dispute == Dispute(14) and state.open_dispute(c) is d and state.held(c)
    assert not state.disputable(m, c)
    assert balance(m, "M-14b") == 49 and ledger(m, "M-14b") == [("dispute deposits", 11.0)]
    assert balance(m, "B-c53") == 60 and ledger(m, "B-c53") == [] and m.deferred == []
    assert pending(m, "B-c53") == [
        "[day 14, round 2] M-14b disputed your rejection of R-b8b on contract-14. The arbiter takes one reading of "
        "it at the start of day 15: at or above the threshold 5, the price is released to M-14b and you pay the "
        "arbiter's fee of 11 to receive R-b8b; below it, your rejection stands and M-14b pays the fee."]
    assert pending(m, "M-14b") == []
    assert [r["type"] for r in log(m)] == ["money", "dispute", "notification"]
    record, = log(m, "dispute")
    assert (record["contract"], record["label"], record["deposit"], record["ruling_day"]) == (
        "contract-14", "R-b8b", 11.0, 15)
    refused(m, "M-14b", "dispute", {"contract_id": "contract-14"},
            "your dispute of the rejection of R-b8b is open: the arbiter rules at the start of day 15")
    add_result(c, "R-new")
    refused(m, "M-14b", "deliver", {"contract_id": "contract-14", "result": "R-new"},
            "your dispute of the rejection of R-b8b on contract-14 is open: the arbiter rules at the start of day "
            "15, and contract-14 takes no submission until then")


@pytest.mark.parametrize("price, fee", [(21.1, "10.55"), (10.0, "5"), (1.0, "0.50"), (22.0, "11")])
def test_the_deposit_is_the_fee_rate_times_the_price_rounded_to_the_cent(price, fee):
    m = market(day=14)
    rejected(m, price=price)
    m.participants["M-14b"].balance = 0.25
    refused(m, "M-14b", "dispute", {"contract_id": "contract-14"},
            f"insufficient balance for disputing the rejection of R-b8b (a deposit of {fee}): it costs {fee}, you "
            "have 0.25")
    m.participants["M-14b"].balance = 60.0
    assert actions.run(m, "M-14b", "dispute", {"contract_id": "contract-14"})[1].startswith(
        f"disputed the rejection of R-b8b on contract-14: {fee} deposited. ")
    assert f"the arbiter's fee of {fee} to receive R-b8b" in pending(m, "B-c53")[0]
    assert ledger(m, "M-14b") == [("dispute deposits", round(0.5 * price, 2))]


@pytest.mark.parametrize("window, decided, day, passed", [
    (1, 13, 13, False), (1, 13, 14, False), (1, 13, 15, True), (3, 10, 13, False), (3, 10, 14, True),
])
def test_a_rejection_can_be_disputed_through_its_window_and_is_ruled_the_next_day(window, decided, day, passed):
    m = market(day=day)
    m.cfg["contracts"]["escrow"]["dispute_window_days"] = window
    c = rejected(m, day=decided)
    if passed:
        refused(m, "M-14b", "dispute", {"contract_id": "contract-14"},
                f"the rejection of R-b8b on day {decided} could be disputed through day {decided + window}")
    else:
        text = actions.run(m, "M-14b", "dispute", {"contract_id": "contract-14"})[1]
        assert f"The arbiter takes one reading of R-b8b at the start of day {day + 1}: " in text
        assert c.deliveries[0].dispute == Dispute(day)


def test_a_deposit_that_takes_the_whole_balance_puts_the_manager_out_of_business():
    m = market(day=14)
    rejected(m)
    m.participants["M-14b"].balance = 11.0
    assert actions.run(m, "M-14b", "dispute", {"contract_id": "contract-14"})[0]
    assert balance(m, "M-14b") == 0 and m.participants["M-14b"].out_of_business
    assert pending(m, "M-14b") == ["[day 14, round 2] " + OUT]
    assert [r["type"] for r in log(m)] == ["money", "out_of_business", "notification", "dispute", "notification"]
    assert log(m, "out_of_business")[0]["cause"] == "dispute deposits"


def test_a_deposit_of_zero_is_still_taken_and_checked():
    m = market(day=14)
    rejected(m)
    m.cfg["contracts"]["escrow"]["dispute_fee_rate"] = 0.0001
    m.participants["M-14b"].balance = 0.0
    assert actions.run(m, "M-14b", "dispute", {"contract_id": "contract-14"})[1].startswith(
        "disputed the rejection of R-b8b on contract-14: 0 deposited. ")
    assert ledger(m, "M-14b") == [("dispute deposits", 0.0)] and m.participants["M-14b"].out_of_business
    assert pending(m, "M-14b") == ["[day 14, round 2] " + OUT]


# 8. pay


def test_pay_checks_in_order():
    m = market()
    add_contract(m, 1, buyer="B-c53", manager="M-14b", form="escrow", ended=4)
    add_contract(m, 2, buyer="B-d75", manager="M-14b")
    add_contract(m, 3, buyer="B-c53", manager="M-849", ended=4)
    m.participants["B-c53"].balance = -2.31
    ids = "B-c53, B-d75, M-14b, M-849, M-f8c"
    hint = "Your contracts: contract-1 (task-1, ended), contract-3 (task-3, ended)."
    bad = {"amount": "five", "contract_id": 0}
    for wrong in ({}, {"recipient": "M-14b"}, {"to": None}, {"to": ""}):
        refused(m, "B-c53", "pay", bad | wrong, "missing argument 'to'")
    for wrong in ("m-14b", " M-14b ", "M-000", "managers", 14):
        refused(m, "B-c53", "pay", bad | {"to": wrong}, f"no participant with id '{wrong}' (participants: {ids})")
    refused(m, "B-c53", "pay", bad | {"to": "B-c53"}, "you cannot pay yourself")
    for wrong in ({}, {"price": 5}, {"amount": None}, {"amount": ""}):
        refused(m, "B-c53", "pay", {"to": "M-f8c", "contract_id": 0} | wrong, "missing argument 'amount'")
    refused(m, "B-c53", "pay", bad | {"to": "M-f8c"}, "amount must be a number, got 'five'")
    for wrong in (0, -3, 0.004, "inf", "nan"):
        refused(m, "B-c53", "pay", bad | {"to": "M-f8c", "amount": wrong}, "amount must be a positive number")
    args = {"to": "M-f8c", "amount": 5}
    refused(m, "B-c53", "pay", args | {"contract_id": 0}, f"no contract with id '0'. {hint}")
    refused(m, "B-c53", "pay", args | {"contract_id": False}, f"no contract with id 'False'. {hint}")
    refused(m, "M-14b", "pay", args | {"contract_id": "contract-1"},
            f"contract-1 is a contract on which you are the manager: {RULE}")
    refused(m, "B-c53", "pay", args | {"contract_id": "contract-2"}, f"contract-2 is not one of your contracts: {RULE}")
    refused(m, "M-849", "pay", args | {"contract_id": "contract-2"}, f"contract-2 is not one of your contracts: {RULE}")
    refused(m, "B-c53", "pay", args | {"contract_id": "contract-1"},
            f"contract-1 is your contract with M-14b, not with M-f8c: {RULE}")
    refused(m, "B-c53", "pay", args | {"to": "B-d75", "contract_id": "1"},
            f"contract-1 is your contract with M-14b, not with B-d75: {RULE}")
    refused(m, "B-c53", "pay", args | {"to": "M-14b", "contract_id": "contract-1"},
            f"contract-1 is an escrow contract: the escrow pays it, and {RULE}")
    refused(m, "B-c53", "pay", args | {"to": "M-849", "contract_id": "contract-3"},
            "contract-3 was ended on day 4: nothing is owed on it, and a payment can't name it")
    m.contracts["contract-3"].ended = None
    refused(m, "B-c53", "pay", args | {"to": "M-849", "contract_id": "contract-3"}, "insufficient balance (-2.31)")
    refused(m, "B-c53", "pay", args | {"to": "M-849"}, "insufficient balance (-2.31)")
    m.participants["B-c53"].balance = 5.0
    ok(m, "B-c53", "pay", args | {"to": "M-849", "contract_id": "contract-3"}, "paid 5 to M-849 for contract-3")


def test_a_payment_on_a_named_contract_moves_the_payers_money_at_once_and_the_payees_at_the_rounds_end():
    m = market()
    c = add_contract(m, 1, price=30.0)
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 30, "contract_id": "contract-1"}, "paid 30 to M-14b for contract-1")
    assert balance(m, "B-c53") == 30 and ledger(m, "B-c53") == [("payments made", 30.0)]
    assert balance(m, "M-14b") == 60 and ledger(m, "M-14b") == [("payments received", 30.0)]
    assert m.deferred == [Credit("M-14b", 30.0, "transfer", "B-c53")]
    assert c.paid == 30.0 and state.paid_in_full(c) and state.beyond(m, c) == []
    assert m.payments == [Payment(5, 2, "B-c53", "M-14b", 30.0, "contract-1", 30.0, True)]
    assert pending(m, "M-14b") == [STAMP + "B-c53 paid you 30 for contract-1"]
    assert pending(m, "B-c53") == []
    assert [r["type"] for r in log(m)] == ["money", "money", "payment", "notification"]
    record, = log(m, "payment")
    assert (record["payer"], record["payee"], record["amount"], record["contract"], record["to_price"],
            record["beyond"], record["message"]) == ("B-c53", "M-14b", 30.0, "contract-1", 30.0, 0.0, None)
    state.land_credits(m)
    assert balance(m, "M-14b") == 90 and m.deferred == []


def test_a_payment_counts_to_the_price_and_the_rest_comes_on_top_of_it():
    m = market(managers=("M-f8c",))
    c = add_contract(m, 6, manager="M-f8c", price=20.0, paid=14.0)
    c.deliveries.append(Delivery(add_result(c, "R-001").label, 3, 1))
    other = add_contract(m, 7, manager="M-f8c", price=20.0)
    ok(m, "B-c53", "pay", {"to": "M-f8c", "amount": 11, "contract_id": "contract-6"},
       "paid 11 to M-f8c for contract-6 (6 of it paid the rest of its price; the other 5 came on top of it)")
    assert c.paid == 20.0 and state.beyond(m, c) == [5.0] and other.paid == 0.0
    assert m.payments == [Payment(5, 2, "B-c53", "M-f8c", 11.0, "contract-6", 6.0, False)]
    assert pending(m, "M-f8c") == [STAMP + "B-c53 paid you 11 for contract-6"]
    ok(m, "B-c53", "pay", {"to": "M-f8c", "amount": 6, "contract_id": "6"},
       "paid 6 to M-f8c for contract-6 (its price was already paid in full: this came on top of it)")
    assert c.paid == 20.0 and state.beyond(m, c) == [5.0, 6.0] and other.paid == 0.0
    assert m.payments[1] == Payment(5, 2, "B-c53", "M-f8c", 6.0, "contract-6", 0.0, False)
    assert balance(m, "B-c53") == 43 and m.deferred == [Credit("M-f8c", 11.0, "transfer", "B-c53"),
                                                        Credit("M-f8c", 6.0, "transfer", "B-c53")]
    assert [(r["to_price"], r["beyond"]) for r in log(m, "payment")] == [(6.0, 5.0), (0.0, 6.0)]


@pytest.mark.parametrize("price, paid, amount, to_price, paid_after, text", [
    (20.0, 0.0, 15, 15.0, 15.0, "paid 15 to M-14b for contract-3"),
    (20.0, 15.0, 5, 5.0, 20.0, "paid 5 to M-14b for contract-3"),
    (20.0, 19.5, 0.5, 0.5, 20.0, "paid 0.50 to M-14b for contract-3"),
    (20.0, 14.45, 5.55, 5.55, 20.0, "paid 5.55 to M-14b for contract-3"),
    (20.0, 19.99, 0.02, 0.01, 20.0, "paid 0.02 to M-14b for contract-3 (0.01 of it paid the rest of its price; the "
                                    "other 0.01 came on top of it)"),
    (0.3, 0.1, 0.2, 0.2, 0.3, "paid 0.20 to M-14b for contract-3"),
    (20.0, 0.0, "24.555", 20.0, 20.0, "paid 24.55 to M-14b for contract-3 (20 of it paid the rest of its price; the "
                                      "other 4.55 came on top of it)"),
])
def test_what_a_payment_counts_toward_the_price(price, paid, amount, to_price, paid_after, text):
    m = market()
    c = add_contract(m, 3, price=price, paid=paid)
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": amount, "contract_id": "contract-3"}, text)
    assert m.payments[0].to_price == to_price and c.paid == paid_after and c.paid <= c.price


def test_a_payment_naming_no_contract_counts_toward_the_oldest_unpaid_plain_contract_with_the_payee():
    m = market()
    add_contract(m, 1, manager="M-849")
    add_contract(m, 2, buyer="B-d75")
    add_contract(m, 3, form="escrow")
    add_contract(m, 4, ended=4)
    full_paid = add_contract(m, 5, paid=20.0)
    first, second, third = add_contract(m, 6), add_contract(m, 7, paid=5.0), add_contract(m, 8)
    for c in (full_paid, second, third):
        c.deliveries.append(Delivery(add_result(c, "R-001").label, 3, 1))
    fixed = actions.implicit(m, "B-c53", "pay", {"to": "M-14b", "amount": 5})
    assert fixed == {"contract": "contract-7"}
    for args in ({}, {"contract": "contract-6"}, {"contract_id": None}, {"contract_id": ""}):
        assert actions.implicit(m, "B-c53", "pay", {"to": "M-14b", "amount": 5} | args) == fixed
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 5, "contract": "contract-6"}, "paid 5 to M-14b for contract-7")
    assert second.paid == 10.0 and m.payments[0] == Payment(5, 2, "B-c53", "M-14b", 5.0, "contract-7", 5.0, False)
    assert pending(m, "M-14b") == [STAMP + "B-c53 paid you 5 for contract-7"]
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 12},
       "paid 12 to M-14b for contract-7 (10 of it paid the rest of its price; the other 2 came on top of it)")
    assert second.paid == 20.0 and third.paid == 0.0 and state.beyond(m, second) == [2.0]
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 1}, "paid 1 to M-14b for contract-8")
    third.paid = 20.0 - 1e-10  # not below price - 1e-9: no longer unpaid
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 1}, "paid 1 to M-14b for contract-6")
    assert m.payments[-1] == Payment(5, 2, "B-c53", "M-14b", 1.0, "contract-6", 1.0, True)
    first.paid = 20.0
    assert actions.implicit(m, "B-c53", "pay", {"to": "M-14b", "amount": 1}) == {"contract": None}
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 1}, "paid 1 to M-14b")
    assert m.payments[-1] == Payment(5, 2, "B-c53", "M-14b", 1.0, None, 0.0, False)
    assert pending(m, "M-14b")[-1] == STAMP + "B-c53 paid you 1"
    assert log(m, "payment")[-1]["contract"] is None and log(m, "payment")[-1]["beyond"] == 0.0


@pytest.mark.parametrize("payer, payee, text, note", [
    ("M-14b", "B-79d", "paid 11 to B-79d", "M-14b paid you 11"),
    ("M-14b", "M-849", "paid 11 to M-849", "M-14b paid you 11"),
    ("B-d75", "B-79d", "paid 11 to B-79d", "B-d75 paid you 11"),
    ("B-d75", "M-849", "paid 11 to M-849", "B-d75 paid you 11"),
])
def test_a_payment_by_a_manager_or_to_a_buyer_is_on_no_contract(payer, payee, text, note):
    m = market(buyers=("B-79d", "B-d75"))
    add_contract(m, 1, buyer="B-79d", manager="M-14b")
    add_contract(m, 2, buyer="B-79d", manager="M-849")
    assert actions.implicit(m, payer, "pay", {"to": payee, "amount": 11}) == {"contract": None}
    ok(m, payer, "pay", {"to": payee, "amount": 11}, text)
    assert m.payments == [Payment(5, 2, payer, payee, 11.0, None, 0.0, False)]
    assert pending(m, payee) == [STAMP + note]
    assert balance(m, payer) == 49 and balance(m, payee) == 60
    assert ledger(m, payer) == [("payments made", 11.0)] and ledger(m, payee) == [("payments received", 11.0)]
    assert m.deferred == [Credit(payee, 11.0, "transfer", payer)]
    assert all(c.paid == 0 for c in m.contracts.values())


def test_the_target_of_an_unnamed_payment_is_the_one_fixed_on_the_state_the_turn_saw():
    m = market()
    old, new = add_contract(m, 1), add_contract(m, 2)
    new.deliveries.append(Delivery(add_result(new, "R-001").label, 5, 2))  # made earlier in this phase
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 5}, "paid 5 to M-14b for contract-1",
       fixed={"contract": "contract-1"})
    assert old.paid == 5.0 and new.paid == 0.0
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 5}, "paid 5 to M-14b", fixed={"contract": None})
    assert m.payments[-1].contract is None and old.paid == 5.0 and new.paid == 0.0
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 5}, "paid 5 to M-14b for contract-2")
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 5, "contract_id": "contract-1"}, "paid 5 to M-14b for contract-1",
       fixed={"contract": "contract-2"})


@pytest.mark.parametrize("args", [
    {"to": "M-14b", "amount": 5, "contract_id": "contract-1"}, {"to": "M-14b", "amount": 5, "contract_id": 0},
    {"amount": 5}, {"to": "nobody", "amount": 5}, {"to": None, "amount": 5},
])
def test_nothing_is_fixed_for_a_payment_that_names_a_contract_or_no_payee(args):
    m = market()
    add_contract(m, 1)
    assert actions.implicit(m, "B-c53", "pay", args) == {}


@pytest.mark.parametrize("amount, shown", [
    (5, "5"), (15, "15"), (5.55, "5.55"), (0.5, "0.50"), (1234567, "1234567"), ("0.005", "0.01"), (1, "1"),
    ("1e2", "100"),
])
def test_the_amount_of_a_payment_is_written_in_cr(amount, shown):
    m = market()
    m.participants["B-d75"].balance = 2e6
    ok(m, "B-d75", "pay", {"to": "M-849", "amount": amount}, f"paid {shown} to M-849")
    assert pending(m, "M-849") == [STAMP + f"B-d75 paid you {shown}"]


@pytest.mark.parametrize("held, amount, paid", [
    (5.0, 5, "5"), (5.0, 5.01, None), (5.0, "5.004", "5"), (4.99999999, 5, None), (4.9999999995, 5, "5"),
    (0.0, 0.01, None), (0.01, 0.01, "0.01"), (-0.001, 0.01, None),
])
def test_a_payment_needs_the_amount_within_the_balance(held, amount, paid):
    m = market()
    m.participants["B-d75"].balance = held
    if paid:
        ok(m, "B-d75", "pay", {"to": "M-849", "amount": amount}, f"paid {paid} to M-849")
    else:
        refused(m, "B-d75", "pay", {"to": "M-849", "amount": amount}, f"insufficient balance ({held:.2f})")


def test_a_payment_is_no_charge_and_never_puts_a_manager_out_of_business():
    m = market()
    ok(m, "M-14b", "pay", {"to": "M-849", "amount": 60}, "paid 60 to M-849")
    assert balance(m, "M-14b") == 0 and not m.participants["M-14b"].out_of_business
    assert pending(m, "M-14b") == [] and log(m, "out_of_business") == []
    refused(m, "M-14b", "pay", {"to": "M-849", "amount": 0.01}, "insufficient balance (0.00)")
    add_post(m, 1)
    ok(m, "M-14b", "bid", {"post_id": "post-1", "price": 20}, "placed bid-1 on post-1 at 20")


def test_a_payee_out_of_business_is_back_when_the_payment_lands():
    m = market()
    payee = m.participants["M-849"]
    payee.balance, payee.out_of_business = -1.0, True
    ok(m, "B-c53", "pay", {"to": "M-849", "amount": 1}, "paid 1 to M-849")
    ok(m, "B-d75", "pay", {"to": "M-849", "amount": 5}, "paid 5 to M-849")
    assert payee.balance == -1.0 and payee.out_of_business
    assert payee.pending == [STAMP + "B-c53 paid you 1", STAMP + "B-d75 paid you 5"]
    state.land_credits(m)
    assert payee.balance == 5.0 and not payee.out_of_business
    assert payee.pending[2:] == [STAMP + "Your balance is positive again: you are back in business."]


@pytest.mark.parametrize("message", ["thanks", " x ", [], {}, [None], 0, 7.5, False, True])
def test_a_message_given_to_pay_is_noted_and_goes_nowhere(message):
    m = market()
    add_contract(m, 2)
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 10, "message": message},
       "paid 10 to M-14b for contract-2 (pay takes no message: yours was not sent)")
    assert pending(m, "M-14b") == [STAMP + "B-c53 paid you 10 for contract-2"]
    assert m.messages == [] and log(m, "payment")[0]["message"] == message
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 15, "message": message},
       "paid 15 to M-14b for contract-2 (10 of it paid the rest of its price; the other 5 came on top of it) (pay "
       "takes no message: yours was not sent)")


@pytest.mark.parametrize("args", [{}, {"message": None}, {"message": ""}, {"message": " "}, {"message": "\n\t"},
                                  {"message": "\u2003"}, {"memo": "thanks"}, {"text": "thanks"}])
def test_no_note_for_a_message_of_whitespace_or_under_another_name(args):
    m = market()
    add_contract(m, 2)
    ok(m, "B-c53", "pay", {"to": "M-14b", "amount": 10} | args, "paid 10 to M-14b for contract-2")


# 9. A clash rule fixes the order of two actions, not their outcome


def test_a_run_or_test_after_an_end_or_approval_fails_only_when_that_succeeded(monkeypatch):
    m = market(day=12)
    c = add_contract(m, 3, form="escrow", due=12, deadline=12)
    add_result(c, "R-001")
    pin_run(monkeypatch)
    pin_label(monkeypatch, "R-a00")
    pin_reading(monkeypatch, 4.6)
    assert actions.run(m, "M-14b", "delegate_run", {"contract_id": "contract-3"})[0]  # the end was refused
    assert actions.run(m, "M-14b", "test_result", {"contract_id": "contract-3", "result": "R-001"})[0]
    assert ledger(m, "M-14b") == [("employee runs", 4.41), ("tests", 1.0)]
    c.ended = 12  # the end succeeded
    for name in ("delegate_run", "run_myself", "test_result"):
        refused(m, "M-14b", name, {"contract_id": "contract-3", "result": "R-001"},
                "B-c53 ended contract-3 on day 12: it takes no more runs, tests or deliveries")
    c.ended = None
    submit(c, "R-002", 12, outcome="approved", decided=12)  # the approval succeeded
    c.release_day, c.release_how = 12, "approved"
    for name in ("delegate_run", "run_myself", "test_result"):
        refused(m, "M-14b", name, {"contract_id": "contract-3", "result": "R-001"},
                "the escrow of contract-3 was released: it takes no more runs, tests or submissions")
    assert ledger(m, "M-14b") == [("employee runs", 4.41), ("tests", 1.0)]


def test_a_bid_or_withdrawal_after_an_acceptance_or_withdrawal_of_the_post_fails_only_when_that_succeeded():
    m = market()
    post = add_post(m, 8)
    mine = add_bid(m, 3, "post-8", "M-14b")
    ok(m, "M-14b", "bid", {"post_id": "post-8", "price": 18}, "updated bid-3 on post-8 at 18")  # nothing closed
    post.closed, post.closed_day, post.closed_round, mine.status = "accepted", 5, 2, "accepted"
    refused(m, "M-14b", "bid", {"post_id": "post-8", "price": 16},
            "post-8 closed earlier this round, when its buyer accepted a bid")
    refused(m, "M-14b", "withdraw_bid", {"post_id": "post-8"},
            "bid-3 on post-8 was accepted: it is a contract now (contract-8) and cannot be withdrawn")
    mine.status, post.closed = "lost", "withdrawn"
    refused(m, "M-14b", "bid", {"post_id": "post-8", "price": 16},
            "post-8 closed earlier this round, when its buyer withdrew it")
    refused(m, "M-14b", "withdraw_bid", {"post_id": "post-8"}, "bid-3 on post-8 is lost: there is nothing to withdraw")


def test_a_delivery_that_runs_before_the_keep_does_not_name_the_kept_label():
    m = market()
    c = add_contract(m, 1)
    submit(c, "R-001", 3, outcome=None)
    add_result(c, "R-002")
    before = copy.deepcopy(m)
    ok(before, "M-14b", "deliver", {"contract_id": "contract-1", "result": "R-002"},
       "delivered R-002 for contract-1 to B-c53 (delivery #2, by the deadline)")
    c.kept = "R-001"  # the keep ran first (B_keep held)
    ok(m, "M-14b", "deliver", {"contract_id": "contract-1", "result": "R-002"},
       "delivered R-002 for contract-1 to B-c53 (delivery #2, by the deadline). The buyer kept R-001, which stays "
       "in use unless it keeps this one")


# spec/vectors/round_order.jsonl: the reference implementation's outcomes of these actions

VECTORS = pathlib.Path(__file__).parent.parent / "spec" / "vectors" / "round_order.jsonl"
OWN = ("bid", "withdraw_bid", "delegate_run", "run_myself", "test_result", "deliver", "dispute")
DECISION = {"under review": "review", "approved": "approved", "released without a decision": "auto",
            "rejected": "rejected"}
STARTED = re.compile(r"started (R-[0-9a-f]{3}) on .* day (\d+); charged ([0-9.]+) credits")
UNLISTED = re.compile(r"- pay: OK: paid \S+ to \S+ for (contract-[0-9-]+)")
READING = re.compile(r"R-[0-9a-f]{3} reading: ([0-9.]+) \(")


def built_contract(cid, f, day, window):
    """A listed contract (vectors/README.md, "Fields"). A decision's day is not given: a rejection still
    disputable is dated today, any other one before its window."""
    c = Contract(cid, f["task"], f["post"], f["buyer"], f["manager"], f["form"], f["price"], f["threshold"],
                 f["signed"][0], f["deadline_day"], ended=f["ended"][0] if f["ended"] else None)
    c.results = [Result(r["result"], r["worker"], r["kind"], r["refines"], r["started"], r["arrival_day"], 5.0,
                        list(r["manager_readings"]), list(r["buyer_readings"])) for r in f["results"]]
    c.label_draws = len(c.results)
    if c.form == "plain":
        c.kept, c.paid = f["kept"], f["paid"]
        c.deliveries = [Delivery(d["result"], d["day"], d["round"]) for d in f["deliveries"]]
        return c
    for s in f["submissions"]:
        d = Delivery(s["result"], s["day"], s["round"], outcome=DECISION[s["decision"]])
        if d.outcome == "rejected":
            d.decided = day if "DISPUTABLE" in f["state"] else day - window - 1
        if s.get("disputed"):
            d.dispute = Dispute(s["disputed"]["day"], winner=s["disputed"]["winner"])
        c.deliveries.append(d)
    if f["state"] == "RELEASED":
        c.release_day, c.release_how = day, "approved"
    if f["state"] == "CLOSED UNRELEASED":
        c.closed = "late"
    return c


def built(case, cfg, shadow_of=None):
    """The market a case's phase starts on or, for `shadow_of`, the state that participant's turn saw: the others
    as the round started, the ids hidden from it removed (vectors/README.md, "Fields"; rounds.md, 6.6, 8.6)."""
    given, listed = case["input"], case["input"]["market"]
    m = Market(seed=0, cfg=cfg)
    m.day, m.round, m.bids_created = given["day"], given["round"], listed["bids_created"]
    for pid, f in listed["participants"].items():
        p = state.add_participant(m, pid, f["role"])
        now = f if shadow_of in (None, pid) else f.get("start_state", f)
        p.balance, p.out_of_business = now["balance"], now["out_of_business"]
    for tid, f in listed["tasks"].items():
        m.tasks[tid] = Task(tid, f["buyer"], 60, f["due_day"], f["state"], f["current_post"], f["current_contract"])
    for pid in listed["open_posts"] + [pid for pid in listed["posts"] if pid not in listed["open_posts"]]:
        f = listed["posts"].get(pid)
        closed = f and f.get("closed") or {}
        m.posts[pid] = Post(pid, f["task"], f["buyer"], *f["posted"], closed=closed.get("why"),
                            closed_day=closed.get("day"), closed_round=closed.get("round")) if f else Post(
            pid, "", "", 0, 1)
    bids = {bid: Bid(bid, f["post"], f["manager"], f["price"], f["threshold"], f["status"])
            for bid, f in listed["bids"].items()}
    orders = [list(listed["contracts"])]
    for pid, f in listed["participants"].items():
        for bid, post in f.get("open_bids", []):
            bids.setdefault(bid, Bid(bid, post, pid, 0.0))
        orders.append([cid for cid, _, _ in f.get("contracts", [])])
    m.bids = {bid: bids[bid] for bid in sorted(bids, key=lambda bid: int(bid.split("-")[1]))}
    stubs = {}
    for pid, f in listed["participants"].items():
        for cid, task, ended in f.get("contracts", []):
            stub = stubs.setdefault(cid, Contract(cid, task, "", "", "", "plain", 0.0, None, 0, 0,
                                                  ended=0 if ended else None))
            setattr(stub, f["role"], pid)
        for run in f.get("runs_in_progress", []):
            if run["contract"] not in listed["contracts"]:
                stubs[run["contract"]].results.append(
                    Result(run["result"], run["worker"], "fresh", None, 0, run["arrival_day"], 5.0))
    sorter = graphlib.TopologicalSorter()
    for order in orders:
        for cid in order:
            sorter.add(cid)
        for first, second in itertools.pairwise(order):
            sorter.add(second, first)
    window = cfg["contracts"]["escrow"]["dispute_window_days"]
    for cid in sorter.static_order():
        f = listed["contracts"].get(cid)
        m.contracts[cid] = built_contract(cid, f, m.day, window) if f else stubs[cid]
        m.tasks.setdefault(m.contracts[cid].task, Task(m.contracts[cid].task, m.contracts[cid].buyer, 60, 0))
    for kind in ("posts", "bids"):
        for hidden in given.get("hidden", {}).get(shadow_of, {}).get(kind, []):
            getattr(m, kind).pop(hidden, None)
    return m


def pin_drawn(monkeypatch, line, day):
    """Give the draws the values the reference implementation drew (vectors/README.md, `drawn`)."""
    started, reading = STARTED.search(line), READING.search(line)
    if started:
        real = rng.run
        monkeypatch.setattr(rng, "run", lambda *a: real(*a) | {"days": int(started[2]) - day,
                                                              "cost": float(started[3])})
        monkeypatch.setattr(rng, "label", lambda seed, stream, n, used: {"label": started[1], "n": n + 1})
    if reading:
        monkeypatch.setattr(rng, "reading", lambda *a: {"z": 0.0, "reading": float(reading[1])})


def mine(roles, pid, name):
    """An action of this file that is its participant's own: pay, or a manager's action sent by a manager."""
    return isinstance(name, str) and (name == "pay" or (name in OWN and roles[pid]["role"] == "manager"))


def test_the_outcomes_of_these_actions_in_the_round_order_vectors(monkeypatch):
    cfgs = {mode: config.resolve({"message_channels": mode}) for mode in ("all", "managers")}
    checked, wrong = Counter(), []
    for text in VECTORS.read_text(encoding="utf-8").splitlines():
        case = json.loads(text)
        given, expected = case["input"], case["expected"]
        cfg, plans, roles = cfgs[given["message_channels"]], given["plans"], given["market"]["participants"]

        # An action refused on the state its turn saw, before any success of its participant in the phase.
        for pid, plan in plans.items():
            for action, e in zip(plan, expected["actions"][pid]):
                if e["status"] == "OK":
                    break
                if e["outcome"] == "refused" and mine(roles, pid, action["name"]):
                    s = built(case, cfg, pid)
                    fixed = actions.implicit(s, pid, action["name"], action["arguments"])
                    result = actions.run(s, pid, action["name"], action["arguments"], fixed)
                    checked["refused", action["name"]] += 1
                    if actions.result_line(action["name"], *result) != e["line"]:
                        wrong.append((case["id"], pid, action, result, e["line"]))
        # The actions that ran, in the phase's order, for as long as this file's actions alone changed the market
        # and each one's implicit target is the one its turn saw: their whole outcome.
        m, succeeded, whole = built(case, cfg), set(), True
        for pid, position, _ in expected["order"]:
            action, e = plans[pid][position], expected["actions"][pid][position]
            name, args = action["name"], action["arguments"]
            if e["outcome"] != "ran":
                continue
            toward = UNLISTED.search(e["line"])
            unlisted = bool(toward) and toward[1] not in given["market"]["contracts"]
            checked["payments toward a contract the case does not list"] += unlisted
            unnamed = name == "pay" and not actions.given(args, "contract_id")
            if not mine(roles, pid, name) or unlisted or (unnamed and pid in succeeded):
                whole = whole and e["status"] != "OK"
                if not whole:
                    break
                continue
            fixed = None if pid in succeeded else actions.implicit(built(case, cfg, pid), pid, name, args)
            with monkeypatch.context() as patch:
                pin_drawn(patch, e["line"], m.day)
                result = actions.run(m, pid, name, args, fixed)
            checked["ran", name, e["status"]] += 1
            if actions.result_line(name, *result) != e["line"]:
                wrong.append((case["id"], pid, action, result, e["line"]))
                break
            if result[0]:
                succeeded.add(pid)
        told = {pid: p.pending for pid, p in m.participants.items() if p.pending}
        credits = [{"to": c.to, "amount": c.amount, "kind": c.kind, "payer": c.payer} for c in m.deferred]
        if whole:
            checked["whole phases"] += 1
            if told != expected["notifications"] or credits != expected["credits_at_round_end"]:
                wrong.append((case["id"], told, credits))
        elif (any(line not in expected["notifications"].get(pid, []) for pid in told for line in told[pid])
              or any(c not in expected["credits_at_round_end"] for c in credits)):
            wrong.append((case["id"], told, credits))
    assert wrong == []
    assert checked == {
        ("refused", "bid"): 148, ("refused", "withdraw_bid"): 141, ("refused", "delegate_run"): 75,
        ("refused", "run_myself"): 83, ("refused", "test_result"): 125, ("refused", "deliver"): 240,
        ("refused", "dispute"): 73, ("refused", "pay"): 119,
        ("ran", "bid", "OK"): 109, ("ran", "withdraw_bid", "OK"): 15, ("ran", "delegate_run", "OK"): 70,
        ("ran", "run_myself", "OK"): 28, ("ran", "test_result", "OK"): 98, ("ran", "deliver", "OK"): 176,
        ("ran", "dispute", "OK"): 4, ("ran", "pay", "OK"): 53,
        "whole phases": 430, "payments toward a contract the case does not list": 1}
