"""The rules every action shares (market/actions.py): spec/actions-buyer.md 1.4 to 1.12, actions-manager.md 1.1 to
1.3 and 3 ("Finding the bid"), the dispatcher of 1.2 and the implicit targets of rounds.md 6.6 step 1.

Every expected text is copied from the spec section the test names. The last test compares the helpers with the
reference implementation's argument refusals in spec/vectors/round_order.jsonl.
"""

import copy
import json
import math
import re
from collections import Counter
from pathlib import Path

import pytest

from market import actions, config, state
from market.actions import (
    ACTIONS, COMPUTE_AS_ACTION, IMPLICIT, NO_NAME, OUT_OF_BUSINESS, amount, contract_gone, find_result, given, hint,
    implicit, lookup, missing, own_contract, participant, preference, quality, results_text, run, short,
    stopped_note, terms, threshold, unknown, withdraw_target,
)
from market.state import Bid, Contract, Delivery, Market, Post, Result, Task

HUGE = int("1" + "0" * 309)  # 1.10: a JSON integer too large in magnitude for a finite double
MANAGER_TAIL = " A bid becomes a contract only when the buyer accepts it."
BUYER_LIST = (
    "pay, post_task, accept_bid, test_delivery, approve_submission, reject_submission, pay_escrow_arbiter, "
    "end_contract, edit_post, withdraw_post, keep_delivery"
)
MANAGER_LIST = "pay, bid, delegate_run, run_myself, test_result, deliver, dispute, withdraw_bid"


def market(mode="off", buyers=("B-c53", "B-d75"), managers=("M-14b", "M-849", "M-f8c")):
    """An empty market on day 3, round 2 (round index 8): the participants of 1.9's example, at balance 60."""
    m = Market(seed=1, cfg=config.resolve({"message_channels": mode}))
    for pid in buyers:
        state.add_participant(m, pid, "buyer")
    for pid in managers:
        state.add_participant(m, pid, "manager")
    m.day, m.round = 3, 2
    return m


def add_task(m, n, buyer, status="queued", **fields):
    m.tasks[f"task-{n}"] = Task(f"task-{n}", buyer, worth=60, due=9, state=status, **fields)


def add_post(m, pid, buyer, day=1, rnd=1, **fields):
    """A post made on day 1, round 1 unless said: its bids are open to its buyer by round index 8."""
    m.posts[pid] = Post(pid, "task" + pid.removeprefix("post"), buyer, day, rnd, **fields)


def add_bid(m, n, post, manager, price, thr=None, status="open"):
    m.bids[f"bid-{n}"] = Bid(f"bid-{n}", post, manager, price, thr, status)
    m.bids_created = max(m.bids_created, n)


def add_contract(m, cid, buyer, manager, form="plain", **fields):
    thr = 5.0 if form == "escrow" else None
    c = m.contracts[cid] = Contract(cid, "task-" + cid.split("-")[1], "post" + cid.removeprefix("contract"), buyer,
                                    manager, form, 20.0, thr, signed=1, deadline=9, **fields)
    return c


def result(label, arrival, worker="employee"):
    return Result(label, worker, "fresh", None, start=1, arrival=arrival, q=5.0)


# 1.4 Arguments


@pytest.mark.parametrize("value", [0, False, " ", "\t", [], {}, 0.0, "x", "none", "null", [None], {"a": None}])
def test_any_value_but_null_and_the_empty_text_counts_as_given(value):
    assert given({"task_id": value}, "task_id")
    assert missing({"task_id": value}, "task_id") is None


@pytest.mark.parametrize("args", [{}, {"task_id": None}, {"task_id": ""}, {"task": "task-1"}, {"Task_id": "task-1"}])
def test_absent_null_and_the_empty_text_are_not_given(args):
    assert not given(args, "task_id")
    assert missing(args, "task_id") == "missing argument 'task_id'"


def test_an_argument_is_read_under_one_name_only():
    m = market()
    add_task(m, 1, "B-c53")
    p = m.participants["B-c53"]
    for other in ("task", "post", "bid", "contract", "id", "task-id", "taskId"):
        assert lookup(m, p, {other: "task-1"}, "task", "task_id") == (None, "missing argument 'task_id'")
    assert lookup(m, p, {"task_id": "task-1", "task": "task-9", "x": 1}, "task", "task_id") == (m.tasks["task-1"], None)


# 1.6 Ids


def ids_market():
    """One of each kind for task 3, and the k-th post and contract of task 3."""
    m = market()
    add_task(m, 3, "B-c53", "contracted", post="post-3-2", contract="contract-3-2", ended=["contract-3"])
    add_post(m, "post-3", "B-c53", closed="accepted", closed_day=1, closed_round=3)
    add_post(m, "post-3-2", "B-c53", closed="accepted", closed_day=2, closed_round=3)
    add_bid(m, 3, "post-3", "M-14b", 20.0, status="accepted")
    add_contract(m, "contract-3", "B-c53", "M-14b", ended=2)
    add_contract(m, "contract-3-2", "B-c53", "M-849")
    return m


@pytest.mark.parametrize("kind, raw, found", [
    ("task", "task-3", "task-3"), ("post", "post-3", "post-3"), ("post", "post-3-2", "post-3-2"),
    ("bid", "bid-3", "bid-3"), ("contract", "contract-3", "contract-3"), ("contract", "contract-3-2", "contract-3-2"),
    ("task", "3", "task-3"), ("post", "3", "post-3"), ("bid", "3", "bid-3"), ("contract", "3", "contract-3"),
    ("post", "3-2", "post-3-2"), ("contract", "3-2", "contract-3-2"),
    ("task", 3, "task-3"), ("bid", 3, "bid-3"), ("contract", 3, "contract-3"),
])
def test_an_id_as_stored_as_digits_or_as_a_json_integer_is_found(kind, raw, found):
    m = ids_market()
    entity, refusal = lookup(m, m.participants["B-c53"], {"x": raw}, kind, "x")
    assert (entity.id, refusal) == (found, None)
    assert state.find(m, kind, raw) is entity


@pytest.mark.parametrize("kind, raw, quoted", [
    ("contract", "Contract-3", "Contract-3"), ("contract", " contract-3", " contract-3"),
    ("contract", "contract 3", "contract 3"), ("contract", "contract-3 ", "contract-3 "),
    ("contract", "CONTRACT-3", "CONTRACT-3"), ("contract", "contract-3\n", "contract-3\n"),
    ("task", "05", "05"), ("task", "03", "03"), ("task", "task-03", "task-03"),
    ("bid", "\uff13", "\uff13"), ("bid", "\u0663", "\u0663"), ("post", "\uff13-\uff12", "\uff13-\uff12"),
    ("contract", "post-3", "post-3"), ("post", "task-3", "task-3"), ("bid", "contract-3", "contract-3"),
    ("task", " 3", " 3"), ("task", "3 ", "3 "), ("task", "3\n", "3\n"), ("task", "-3", "-3"), ("task", "3-", "3-"),
    ("post", "3-2-1", "3-2-1"), ("post", "3--2", "3--2"), ("task", "#3", "#3"),
    ("task", 3.0, "3.0"), ("task", -3, "-3"), ("task", True, "True"), ("task", False, "False"),
    ("task", [3], "[3]"), ("task", ["task-3"], "['task-3']"), ("task", {"id": 3}, "{'id': 3}"), ("task", 0, "0"),
])
def test_nothing_else_names_an_id_and_the_refusal_quotes_it_as_sent(kind, raw, quoted):
    m = ids_market()
    p = m.participants["B-c53"]
    assert state.find(m, kind, raw) is None
    entity, refusal = lookup(m, p, {"x": raw}, kind, "x")
    assert entity is None
    assert refusal == f"no {kind} with id '{quoted}'. {hint(m, p, kind)}"
    assert refusal == unknown(m, p, kind, raw)


def test_an_id_is_looked_up_among_all_ids_of_its_kind_any_participants():
    m = ids_market()
    for pid in m.participants:
        p = m.participants[pid]
        for kind, raw in (("task", "task-3"), ("post", "post-3-2"), ("bid", "bid-3"), ("contract", "contract-3-2")):
            assert lookup(m, p, {"x": raw}, kind, "x") == (getattr(m, kind + "s")[raw], None)


def test_the_task_hint_lists_the_agents_queued_tasks_in_creation_order():
    m = market()
    p, q = m.participants["B-c53"], m.participants["B-d75"]
    assert hint(m, p, "task") == "Your queued (not yet posted) tasks: none."
    add_task(m, 12, "B-c53")
    add_task(m, 2, "B-d75")
    add_task(m, 4, "B-c53", "posted", post="post-4")
    add_task(m, 5, "B-c53", "contracted")
    add_task(m, 6, "B-c53", "withdrawn")
    add_task(m, 7, "B-c53")
    assert hint(m, p, "task") == "Your queued (not yet posted) tasks: task-12, task-7."
    assert hint(m, q, "task") == "Your queued (not yet posted) tasks: task-2."
    assert lookup(m, p, {"task_id": "Task-12"}, "task", "task_id") == (
        None, "no task with id 'Task-12'. Your queued (not yet posted) tasks: task-12, task-7.")
    m.tasks["task-2"].state = "posted"
    assert unknown(m, q, "task", "task 8") == "no task with id 'task 8'. Your queued (not yet posted) tasks: none."


def test_the_post_hint_lists_every_open_post_in_the_market_in_creation_order():
    m = market()
    p = m.participants["M-14b"]
    assert hint(m, p, "post") == "Open posts: none."
    assert unknown(m, p, "post", "bid-3") == "no post with id 'bid-3'. Open posts: none."
    for pid, buyer in (("post-3", "B-c53"), ("post-1", "B-d75"), ("post-4", "B-c53"), ("post-2", "B-d75"),
                       ("post-5", "B-c53")):
        add_post(m, pid, buyer)
    add_post(m, "post-6", "B-c53", closed="withdrawn", closed_day=2, closed_round=1)
    add_post(m, "post-7", "B-d75", closed="accepted", closed_day=2, closed_round=1)
    add_post(m, "post-8-2", "B-d75", day=3, rnd=2)
    text = "Open posts: post-3, post-1, post-4, post-2, post-5, post-8-2."
    for pid in m.participants:
        assert hint(m, m.participants[pid], "post") == text
    assert unknown(m, p, "post", "post-999") == "no post with id 'post-999'. " + text


def bids_market():
    """The state behind 1.6's example of the bid hint."""
    m = market(buyers=("B-82c", "B-b79"), managers=("M-0ed", "M-7f8", "M-ee6"))
    add_post(m, "post-25", "B-82c")
    add_post(m, "post-26", "B-82c")
    add_post(m, "post-27", "B-b79")
    add_bid(m, 61, "post-25", "M-0ed", 35.0, 5.0)
    add_bid(m, 62, "post-27", "M-0ed", 40.0)
    add_bid(m, 63, "post-25", "M-7f8", 6.0, 5.0)
    add_bid(m, 64, "post-26", "M-0ed", 12.0, status="withdrawn")
    add_bid(m, 66, "post-26", "M-ee6", 9.5)
    return m


def test_the_bid_hint_is_the_specs_example():
    m = bids_market()
    p = m.participants["B-82c"]
    text = ("no bid with id 'bid-68'. Open bids on your posts: bid-61 (M-0ed at 35 with threshold 5 (escrow) on "
            "post-25), bid-63 (M-7f8 at 6 with threshold 5 (escrow) on post-25), bid-66 (M-ee6 at 9.5 on post-26).")
    assert lookup(m, p, {"bid_id": "bid-68"}, "bid", "bid_id") == (None, text)
    assert hint(m, m.participants["B-b79"], "bid") == "Open bids on your posts: bid-62 (M-0ed at 40 on post-27)."


def test_the_bid_hint_leaves_out_sealed_posts_and_managers_out_of_business():
    m = bids_market()
    p = m.participants["B-82c"]
    m.participants["M-0ed"].out_of_business = True
    assert hint(m, p, "bid") == ("Open bids on your posts: bid-63 (M-7f8 at 6 with threshold 5 (escrow) on post-25), "
                                 "bid-66 (M-ee6 at 9.5 on post-26).")
    m.posts["post-25"].day, m.posts["post-25"].round = 3, 1  # round index 7: opens at 9, now is 8
    assert state.sealed(m, m.posts["post-25"])
    assert hint(m, p, "bid") == "Open bids on your posts: bid-66 (M-ee6 at 9.5 on post-26)."
    m.posts["post-25"].day, m.posts["post-25"].round = 2, 3  # round index 6: opens at 8
    assert hint(m, p, "bid").startswith("Open bids on your posts: bid-63 ")
    m.participants["M-ee6"].out_of_business = m.participants["M-7f8"].out_of_business = True
    assert hint(m, p, "bid") == "Open bids on your posts: none."
    assert unknown(m, p, "bid", "bid-1") == "no bid with id 'bid-1'. Open bids on your posts: none."


def test_the_bid_hint_lists_open_bids_only_in_the_order_first_placed_with_g_prices():
    m = bids_market()
    p = m.participants["B-82c"]
    m.bids["bid-61"].price, m.bids["bid-61"].threshold = 24.55, None  # replaced in place: it keeps its place
    m.bids["bid-63"].status = "lost"
    m.bids["bid-66"].price, m.bids["bid-66"].threshold = 1e16, 0.0
    add_bid(m, 70, "post-25", "M-ee6", 1234567.0, 10.0)
    assert hint(m, p, "bid") == (
        "Open bids on your posts: bid-61 (M-0ed at 24.55 on post-25), bid-66 (M-ee6 at 1e+16 with threshold 0 "
        "(escrow) on post-26), bid-70 (M-ee6 at 1.23457e+06 with threshold 10 (escrow) on post-25).")


def test_the_contract_hint_with_and_without_items_for_each_role():
    m = market()
    buyer, manager = m.participants["B-c53"], m.participants["M-14b"]
    assert hint(m, buyer, "contract") == "Your contracts: none."
    assert unknown(m, buyer, "contract", "contract-1") == "no contract with id 'contract-1'. Your contracts: none."
    assert hint(m, manager, "contract") == "Your contracts: none." + MANAGER_TAIL
    add_contract(m, "contract-3", "B-c53", "M-14b")
    add_contract(m, "contract-1", "B-d75", "M-14b", ended=0)
    add_contract(m, "contract-4", "B-c53", "M-849", "escrow", ended=2)
    add_contract(m, "contract-4-2", "B-c53", "M-f8c", "escrow")
    assert hint(m, buyer, "contract") == (
        "Your contracts: contract-3 (task-3), contract-4 (task-4, ended), contract-4-2 (task-4).")
    assert hint(m, manager, "contract") == (
        "Your contracts: contract-3 (task-3), contract-1 (task-1, ended)." + MANAGER_TAIL)
    assert hint(m, m.participants["B-d75"], "contract") == "Your contracts: contract-1 (task-1, ended)."


def test_the_managers_contract_hint_adds_its_open_bids_in_the_order_first_placed():
    m = market()
    p = m.participants["M-14b"]
    add_contract(m, "contract-3", "B-c53", "M-14b")
    for pid in ("post-9", "post-14", "post-18"):
        add_post(m, pid, "B-d75", day=3, rnd=2)  # sealed from its buyer: the manager's own bids all the same
    add_bid(m, 25, "post-9", "M-14b", 20.0)
    add_bid(m, 30, "post-14", "M-849", 20.0)
    add_bid(m, 39, "post-9", "M-14b", 20.0, status="withdrawn")
    add_bid(m, 40, "post-14", "M-14b", 20.0, 5.0)
    add_bid(m, 41, "post-18", "M-14b", 20.0)
    add_bid(m, 42, "post-18", "M-14b", 20.0, status="lost")
    add_bid(m, 43, "post-18", "M-14b", 20.0, status="accepted")
    tail = MANAGER_TAIL + " Your bids not yet accepted: bid-25 on post-9, bid-40 on post-14, bid-41 on post-18."
    assert unknown(m, p, "contract", "01") == "no contract with id '01'. Your contracts: contract-3 (task-3)." + tail
    p.out_of_business = True
    assert hint(m, p, "contract") == "Your contracts: contract-3 (task-3)." + tail
    assert hint(m, m.participants["M-f8c"], "contract") == "Your contracts: none." + MANAGER_TAIL
    assert hint(m, m.participants["M-849"], "contract") == (
        "Your contracts: none." + MANAGER_TAIL + " Your bids not yet accepted: bid-30 on post-14.")


# 1.7 "One of your contracts"


def test_one_of_your_contracts():
    m = market()
    add_contract(m, "contract-3", "B-c53", "M-14b")
    add_contract(m, "contract-4", "B-d75", "M-849", ended=2)
    c3, c4 = m.contracts["contract-3"], m.contracts["contract-4"]
    buyer, manager = m.participants["B-c53"], m.participants["M-14b"]
    for p in (buyer, manager):
        assert own_contract(m, p, {}) == (None, "missing argument 'contract_id'")
        assert own_contract(m, p, {"contract_id": None, "contract": "contract-3"}) == (
            None, "missing argument 'contract_id'")
        assert own_contract(m, p, {"contract_id": "contract-3"}) == (c3, None)
        assert own_contract(m, p, {"contract_id": 3}) == (c3, None)
    assert own_contract(m, buyer, {"contract_id": "Contract-3"}) == (
        None, "no contract with id 'Contract-3'. Your contracts: contract-3 (task-3).")
    assert own_contract(m, buyer, {"contract_id": "4"}) == (
        None, "contract-4 is not one of your contracts. Your contracts: contract-3 (task-3).")
    assert own_contract(m, manager, {"contract_id": 4}) == (
        None, "contract-4 is not one of your contracts. Your contracts: contract-3 (task-3)." + MANAGER_TAIL)
    assert own_contract(m, m.participants["M-f8c"], {"contract_id": "contract-4"}) == (
        None, "contract-4 is not one of your contracts. Your contracts: none." + MANAGER_TAIL)
    assert own_contract(m, m.participants["B-d75"], {"contract_id": "contract-4"}) == (c4, None)
    assert own_contract(m, m.participants["M-849"], {"contract_id": "contract-4"}) == (c4, None)


def test_the_role_decides_which_side_of_the_contract_the_agent_must_be():
    m = market()
    c = add_contract(m, "contract-3", "B-c53", "M-14b")
    c.manager = "B-d75"  # no market makes this: a buyer on the manager's side is still not the contract's buyer
    assert own_contract(m, m.participants["B-d75"], {"contract_id": "contract-3"}) == (
        None, "contract-3 is not one of your contracts. Your contracts: contract-3 (task-3).")


# 1.9 Participants


def test_a_participant_is_named_exactly_by_its_id():
    m = market()
    ids = "B-c53, B-d75, M-14b, M-849, M-f8c"
    for pid in m.participants:
        assert participant(m, pid) == (m.participants[pid], None)
    assert participant(m, "M-000") == (None, f"no participant with id 'M-000' (participants: {ids})")
    for raw, quoted in (("m-14b", "m-14b"), (" M-14b ", " M-14b "), ("everyone", "everyone"), ("M14b", "M14b"),
                        ("14b", "14b"), (5, "5"), (True, "True"), (["M-14b"], "['M-14b']"), (1.5, "1.5")):
        assert participant(m, raw) == (None, f"no participant with id '{quoted}' (participants: {ids})")


def test_the_participants_are_listed_buyers_first_each_role_in_seat_order():
    m = market(buyers=("B-f00", "B-001"), managers=("M-zzz", "M-aaa"))
    assert [p.seat for p in m.participants.values()] == [0, 1, 0, 1]
    assert participant(m, "B-000")[1] == "no participant with id 'B-000' (participants: B-f00, B-001, M-zzz, M-aaa)"


# 1.10 Amounts


@pytest.mark.parametrize("value, used", [
    (25, 25.0), ("25", 25.0), (" 25 ", 25.0), ("24.555", 24.55), (24.555, 24.55), ("1e2", 100.0), ("1E2", 100.0),
    ("5", 5.0), ("5.", 5.0), (".5", 0.5), ("5.5", 5.5), ("+7", 7.0), ("1_0", 10.0), ("1_000.5", 1000.5),
    ("\uff12\uff15", 25.0), ("\t25\n", 25.0), ("\u300025\u00a0", 25.0), (0.005, 0.01), ("0.005", 0.01), (0.01, 0.01),
    (1e16, 1e16), ("1e-2", 0.01), (15.0, 15.0), (1234567, 1234567.0), ("2.5e1", 25.0), ("1e+2", 100.0),
])
def test_an_amount_that_reads_is_rounded_to_the_cent(value, used):
    for name in ("price", "amount"):
        got, refusal = amount({name: value}, name)
        assert (got, refusal) == (used, None) and isinstance(got, float)


@pytest.mark.parametrize("value, quoted", [
    ("25 credits", "'25 credits'"), ("$24", "'$24'"), ("1,000", "'1,000'"), ("five", "'five'"), (True, "True"),
    (False, "False"), ([25], "[25]"), ([], "[]"), ({"x": 7}, "{'x': 7}"), ("\u001c7", "'\\x1c7'"),
    ("7\u001f", "'7\\x1f'"), (".", "'.'"), ("1__0", "'1__0'"), ("_1", "'_1'"), ("1_", "'1_'"), (" ", "' '"),
    ("1e", "'1e'"), ("e5", "'e5'"), ("0x10", "'0x10'"), ("1 000", "'1 000'"), ("--1", "'--1'"), ("5,5", "'5,5'"),
    ("it's 5", '"it\'s 5"'), ("infinit", "'infinit'"), ("1.2.3", "'1.2.3'"),
])
def test_an_amount_that_does_not_read(value, quoted):
    assert amount({"price": value}, "price") == (None, f"price must be a number, got {quoted}")
    assert amount({"amount": value}, "amount") == (None, f"amount must be a number, got {quoted}")


@pytest.mark.parametrize("value", [
    0, -3, 0.004, "inf", "nan", "infinity", "Infinity", "INF", "NaN", "-inf", "+inf", "-nan", math.inf, -math.inf,
    math.nan, HUGE, -HUGE, str(HUGE), "0", "-0", -0.0, "0.004", "-24.5", 0.0, 1e-9, "1e999", "0.0049",
])
def test_an_amount_that_is_not_finite_or_not_above_zero_once_rounded(value):
    assert amount({"price": value}, "price") == (None, "price must be a positive number")
    assert amount({"amount": value}, "amount") == (None, "amount must be a positive number")


def test_a_missing_amount_names_its_argument():
    assert amount({}, "price") == (None, "missing argument 'price'")
    assert amount({"price": None, "amount": 5}, "price") == (None, "missing argument 'price'")
    assert amount({"amount": "", "price": 5}, "amount") == (None, "missing argument 'amount'")


# 1.11 Thresholds; actions-manager.md 6.1, check 7


@pytest.mark.parametrize("value, used", [
    (7, 7.0), (7.0, 7.0), ("7", 7.0), (" 7 ", 7.0), (0, 0.0), (10, 10.0), ("10", 10.0), ("1e1", 10.0), ("7.0", 7.0),
    (-0.0, 0.0), ("-0", 0.0), ("-0.0", 0.0), ("\uff17", 7.0), ("+5", 5.0), ("0_7", 7.0),
])
def test_a_threshold_is_a_whole_quality_as_a_double(value, used):
    got, refusal = threshold(value)
    assert (got, refusal) == (used, None) and isinstance(got, float)
    assert math.copysign(1, got) == 1  # -0 becomes 0


@pytest.mark.parametrize("value, quoted", [
    (11, "11"), (-1, "-1"), ("high", "'high'"), (True, "True"), (False, "False"), ([7], "[7]"), ({"t": 7}, "{'t': 7}"),
    ("inf", "'inf'"), (math.inf, "inf"), (math.nan, "nan"), ("nan", "'nan'"), ("\u001c7", "'\\x1c7'"),
    (10.5, "10.5"), (-0.5, "-0.5"), ("11", "'11'"), ("none", "'none'"), ("null", "'null'"), (" ", "' '"),
    (10.000001, "10.000001"), (10**400, "1" + "0" * 400), (-(10**400), "-1" + "0" * 400), (str(HUGE), f"'{HUGE}'"),
    ("7 or more", "'7 or more'"), (None, "None"),
])
def test_a_threshold_that_is_no_quality_between_0_and_10(value, quoted):
    assert threshold(value) == (None, f"threshold must be a quality between 0 and 10, got {quoted}")


@pytest.mark.parametrize("value, quoted", [(7.5, "7.5"), ("7.5", "'7.5'"), (0.1, "0.1"), ("9.99", "'9.99'"),
                                           (1e-9, "1e-09"), (" 2.5 ", "' 2.5 '")])
def test_a_threshold_that_is_no_whole_number(value, quoted):
    assert threshold(value) == (None, f"threshold must be a whole number between 0 and 10, got {quoted}")


@pytest.mark.parametrize("value, used", [
    ("8.25", 8.2), (8.25, 8.2), (7, 7.0), ("7", 7.0), (9.96, 10.0), (0.04, 0.0), (-0.0, 0.0), ("-0", 0.0), (10, 10.0),
    (6.35, 6.3), (" 6 ", 6.0), (0, 0.0),
])
def test_a_stated_quality_is_rounded_to_the_tests_decimals(value, used):
    got, refusal = quality(value, 1)
    assert (got, refusal) == (used, None) and isinstance(got, float)
    assert math.copysign(1, got) == 1


def test_a_stated_quality_at_other_decimals():
    assert quality("8.25", 2) == (8.25, None)
    got, refusal = quality(7.5, 0)
    assert (got, refusal) == (8.0, None) and isinstance(got, float)
    assert quality(6.5, 0) == (6.0, None)


@pytest.mark.parametrize("value, quoted", [
    (11, "11"), (True, "True"), ("abc", "'abc'"), ("nan", "'nan'"), (-1, "-1"), ("inf", "'inf'"), ([8], "[8]"),
    ({"q": 8}, "{'q': 8}"), (10.01, "10.01"), ("high", "'high'"), (False, "False"), (10**400, "1" + "0" * 400),
    (-0.01, "-0.01"), ("\u001c7", "'\\x1c7'"),
])
def test_a_stated_quality_that_is_no_number_between_0_and_10(value, quoted):
    assert quality(value, 1) == (None, f"quality must be a number between 0 and 10, got {quoted}")


# 1.12 Balance checks


@pytest.mark.parametrize("balance, cost, what, text", [
    (54.99, 55.0, "accepting the escrow bid bid-2 (its price 50 is held and the fee 5 charged)",
     "insufficient balance for accepting the escrow bid bid-2 (its price 50 is held and the fee 5 charged): it costs "
     "55, you have 54.99"),
    (0.4, 1.0, "test_delivery", "insufficient balance for test_delivery: it costs 1, you have 0.40"),
    (0.4, 1.0, "test_result", "insufficient balance for test_result: it costs 1, you have 0.40"),
    (3.0, 10.55, "paying the arbiter's fee on contract-1",
     "insufficient balance for paying the arbiter's fee on contract-1: it costs 10.55, you have 3.00"),
    (-2.31, 29.15, "delegate_run", "insufficient balance for delegate_run: it costs 29.15, you have -2.31"),
    (-0.001, 4.1, "run_myself", "insufficient balance for run_myself: it costs 4.10, you have -0.00"),
    (-0.0, 7.56, "run_myself", "insufficient balance for run_myself: it costs 7.56, you have -0.00"),
    (0.0, 4.0, "delegate_run", "insufficient balance for delegate_run: it costs 4, you have 0.00"),
    (-0.004, 0.5, "test_result", "insufficient balance for test_result: it costs 0.50, you have -0.00"),
    (0.004, 0.5, "test_result", "insufficient balance for test_result: it costs 0.50, you have 0.00"),
    (1234566.5, 1234567.0, "x", "insufficient balance for x: it costs 1234567, you have 1234566.50"),
    (2.675, 5.0, "x", "insufficient balance for x: it costs 5, you have 2.67"),
    (4.999999, 5.0, "x", "insufficient balance for x: it costs 5, you have 5.00"),
    (-12.0, 1.0, "test_delivery", "insufficient balance for test_delivery: it costs 1, you have -12.00"),
    # the rule as written: a balance below 0 covers no cost, not even 0
    (-3.0, 0.0, "test_delivery", "insufficient balance for test_delivery: it costs 0, you have -3.00"),
])
def test_the_balance_refusal(balance, cost, what, text):
    m = market()
    p = m.participants["M-14b"]
    p.balance = balance
    assert short(p, cost, what) == text
    assert p.balance == balance or (balance == 0 and p.balance == 0)


@pytest.mark.parametrize("balance, cost", [
    (5.0, 5.0), (60.0, 1.0), (5.0 - 5e-10, 5.0), (0.0, 0.0), (-5e-10, 0.0), (0.1 + 0.2, 0.3), (1.0, 1.0 + 1e-10),
    (-0.0, 0.0), (1e16, 1e16),
])
def test_a_cost_the_balance_covers_within_1e_9_passes(balance, cost):
    m = market()
    p = m.participants["B-c53"]
    p.balance = balance
    assert short(p, cost, "test_delivery") is None


@pytest.mark.parametrize("cost", [math.inf, -math.inf, math.nan])
def test_a_cost_that_is_not_finite_stops_the_episode(cost):
    # config.md, 6, "Finiteness during an episode": never "used, shown or logged as an amount"; the text names it
    m = market()
    p = m.participants["B-c53"]
    with pytest.raises(ArithmeticError, match=r"the cost of test_delivery is not finite: (inf|-inf|nan)"):
        short(p, cost, "test_delivery")
    assert p.balance == 60.0


def test_the_balance_check_fails_just_past_the_tolerance():
    m = market()
    p = m.participants["B-c53"]
    p.balance = 5.0 - 3e-9
    assert short(p, 5.0, "x") == "insufficient balance for x: it costs 5, you have 5.00"


# Shared texts of the actions


def test_terms_preference_and_the_stopped_note():
    assert terms(None) == ""
    assert terms(5.0) == " with threshold 5 (escrow)"
    assert terms(0.0) == " with threshold 0 (escrow)"
    assert terms(10.0) == " with threshold 10 (escrow)"
    assert preference("escrow", 7.0) == "prefers escrow, threshold 7"
    assert preference("escrow", 0.0) == "prefers escrow, threshold 0"
    assert preference("escrow", None) == "prefers escrow"
    assert preference("plain", None) == "prefers a plain contract"
    assert preference(None, None) == "no preferred form"
    assert stopped_note(1) == "(note: your remaining 1 action did not run: you are out of business)"
    assert stopped_note(2) == "(note: your remaining 2 actions did not run: you are out of business)"
    assert stopped_note(5) == "(note: your remaining 5 actions did not run: you are out of business)"


# actions-manager.md 1.2 The contract's state checks


def test_a_contract_its_buyer_ended_takes_nothing_more():
    m = market()
    text = "B-c53 ended contract-3 on day 2: it takes no more runs, tests or deliveries"
    for form in ("plain", "escrow"):
        c = add_contract(m, "contract-3", "B-c53", "M-14b", form, ended=2)
        assert contract_gone(m, c) == text
        assert contract_gone(m, c, closed=False) == text
    c.ended = 0
    assert contract_gone(m, c) == "B-c53 ended contract-3 on day 0: it takes no more runs, tests or deliveries"


def test_an_escrow_that_no_longer_holds_the_price():
    m = market()
    c = add_contract(m, "contract-3-2", "B-c53", "M-14b", "escrow")
    assert contract_gone(m, c) is None and contract_gone(m, c, closed=False) is None
    c.deliveries.append(Delivery("R-0a1", 2, 1, outcome="rejected", decided=2))
    assert contract_gone(m, c) is None
    for how in ("approved", "automatic", "ruling"):
        c.release_day, c.release_how, c.closed = 3, how, None
        assert contract_gone(m, c) == (
            "the escrow of contract-3-2 was released: it takes no more runs, tests or submissions")
        assert contract_gone(m, c, closed=False) is None
    c.release_day, c.release_how, c.closed = None, None, "late"
    assert contract_gone(m, c) == (
        "the escrow of contract-3-2 closed unreleased: it takes no more runs, tests or submissions")
    assert contract_gone(m, c, closed=False) is None
    c.ended = 3  # the ended check comes first
    assert contract_gone(m, c) == "B-c53 ended contract-3-2 on day 3: it takes no more runs, tests or deliveries"


def test_a_plain_contract_is_never_closed_except_by_being_ended():
    m = market()
    m.day = 40
    c = add_contract(m, "contract-3", "B-c53", "M-14b", paid=20.0, deliveries=[Delivery("R-0a1", 2, 1)], kept="R-0a1")
    assert contract_gone(m, c) is None and contract_gone(m, c, closed=False) is None


# actions-manager.md 1.3 The result lookup


def results_market():
    """1.3's real examples: today is day 13."""
    m = market(managers=("M-14b", "M-849"))
    m.day = 13
    add_contract(m, "contract-8", "B-c53", "M-14b", ended=9, results=[result("R-b7b", 5)])
    add_contract(m, "contract-15", "B-c53", "M-14b")
    add_contract(m, "contract-26", "B-d75", "M-14b", "escrow", results=[result("R-89b", 12), result("R-560", 13)])
    add_contract(m, "contract-29", "B-d75", "M-14b", results=[result("R-082", 14), result("R-560", 15, "manager")])
    add_contract(m, "contract-30", "B-d75", "M-849", results=[result("R-777", 3), result("R-89b", 3)])
    return m


def test_the_results_of_a_contract():
    m = results_market()
    assert results_text(m, m.contracts["contract-15"]) == (
        "contract-15 has no results yet (start a run with delegate_run or run_myself).")
    assert results_text(m, m.contracts["contract-29"]) == (
        "Results of contract-29: R-082 (arrives day 14), R-560 (arrives day 15).")
    assert results_text(m, m.contracts["contract-26"]) == "Results of contract-26: R-89b (arrived), R-560 (arrived)."
    assert results_text(m, m.contracts["contract-8"]) == "Results of contract-8: R-b7b (arrived)."


def test_a_result_of_another_contract_of_the_same_manager():
    m = results_market()
    p = m.participants["M-14b"]
    c15, c29 = m.contracts["contract-15"], m.contracts["contract-29"]
    assert find_result(m, p, c29, "R-89b", "delivered") == (None, (
        "R-89b is a result of contract-26, not of contract-29: a result can be tested, refined or delivered only on "
        "the contract it was run for. Results of contract-29: R-082 (arrives day 14), R-560 (arrives day 15)."))
    assert find_result(m, p, c15, "R-b7b", "tested") == (None, (
        "R-b7b is a result of contract-8, not of contract-15: a result can be tested, refined or delivered only on "
        "the contract it was run for. contract-15 has no results yet (start a run with delegate_run or run_myself)."))
    # the first such contract in creation order, also one whose result has not arrived
    assert find_result(m, p, c15, "R-560", "refined")[1].startswith("R-560 is a result of contract-26, not of ")
    assert find_result(m, p, m.contracts["contract-8"], "R-082", "refined")[1].startswith(
        "R-082 is a result of contract-29, not of contract-8: ")


def test_another_managers_result_is_no_result():
    m = results_market()
    p = m.participants["M-14b"]
    assert find_result(m, p, m.contracts["contract-15"], "R-777", "tested") == (
        None, ("no result 'R-777' in contract-15. contract-15 has no results yet (start a run with delegate_run or "
               "run_myself)."))
    q = m.participants["M-849"]
    assert find_result(m, q, m.contracts["contract-30"], "R-082", "tested") == (
        None, "no result 'R-082' in contract-30. Results of contract-30: R-777 (arrived), R-89b (arrived).")
    assert find_result(m, q, m.contracts["contract-30"], "R-89b", "tested") == (
        m.contracts["contract-30"].results[1], None)


@pytest.mark.parametrize("raw, quoted", [
    ("r-082", "r-082"), ("082", "082"), ("R-082 ", "R-082 "), (" R-082", " R-082"), ("R-08A", "R-08A"), (123, "123"),
    (82, "82"), (True, "True"), (["R-082"], "['R-082']"), ("R-000", "R-000"), ("None", "None"), ("null", "null"),
])
def test_a_label_names_a_result_exactly_as_written(raw, quoted):
    m = results_market()
    p = m.participants["M-14b"]
    assert find_result(m, p, m.contracts["contract-29"], raw, "delivered") == (
        None, (f"no result '{quoted}' in contract-29. Results of contract-29: R-082 (arrives day 14), R-560 (arrives "
               "day 15)."))


@pytest.mark.parametrize("purpose", ["refined", "tested", "delivered"])
def test_a_result_that_has_not_arrived(purpose):
    m = results_market()
    p = m.participants["M-14b"]
    c26, c29 = m.contracts["contract-26"], m.contracts["contract-29"]
    assert find_result(m, p, c29, "R-082", purpose) == (
        None, f"R-082 has not arrived yet (it arrives at the start of day 14), so it cannot be {purpose}")
    # a label of two contracts is read on the contract named
    assert find_result(m, p, c29, "R-560", purpose) == (
        None, f"R-560 has not arrived yet (it arrives at the start of day 15), so it cannot be {purpose}")
    assert find_result(m, p, c26, "R-560", purpose) == (c26.results[1], None)  # it arrives at the day's start
    m.day = 14
    assert find_result(m, p, c29, "R-082", purpose) == (c29.results[0], None)


def test_any_arrived_result_of_the_contract_passes_whatever_happened_to_it():
    m = results_market()
    p = m.participants["M-14b"]
    c = m.contracts["contract-26"]
    c.deliveries.append(Delivery("R-89b", 12, 1, outcome="rejected", decided=12))
    c.results.append(Result("R-0ff", "manager", "refine", "R-89b", start=12, arrival=13, q=7.0))
    assert find_result(m, p, c, "R-89b", "refined") == (c.results[0], None)
    assert find_result(m, p, c, "R-0ff", "tested") == (c.results[2], None)


# actions-manager.md 3, "Finding the bid"


def test_withdraw_bid_finds_the_managers_open_bid_else_its_bid_created_last():
    m = market()
    add_post(m, "post-7", "B-c53")
    add_post(m, "post-8", "B-c53", closed="accepted", closed_day=2, closed_round=1)
    add_post(m, "post-9", "B-d75")
    add_bid(m, 1, "post-7", "M-14b", 20.0, status="withdrawn")
    add_bid(m, 2, "post-7", "M-849", 21.0)
    add_bid(m, 3, "post-7", "M-14b", 22.0)
    add_bid(m, 4, "post-8", "M-14b", 23.0, status="lost")
    add_bid(m, 5, "post-8", "M-f8c", 24.0, status="withdrawn")
    add_bid(m, 6, "post-8", "M-f8c", 25.0, status="accepted")
    add_bid(m, 7, "post-7", "M-f8c", 26.0, status="withdrawn")
    add_bid(m, 8, "post-7", "M-f8c", 27.0, status="withdrawn")
    for raw in ("post-7", "7", 7):
        assert withdraw_target(m, "M-14b", {"post_id": raw}) is m.bids["bid-3"]
        assert withdraw_target(m, "M-849", {"post_id": raw}) is m.bids["bid-2"]
        assert withdraw_target(m, "M-f8c", {"post_id": raw}) is m.bids["bid-8"]
    m.bids["bid-3"].status = "withdrawn"
    assert withdraw_target(m, "M-14b", {"post_id": "post-7"}) is m.bids["bid-3"]
    m.bids["bid-1"].status = "open"  # no market makes this: the open bid is found before the one created last
    assert withdraw_target(m, "M-14b", {"post_id": "post-7"}) is m.bids["bid-1"]
    assert withdraw_target(m, "M-14b", {"post_id": "post-8"}) is m.bids["bid-4"]
    assert withdraw_target(m, "M-f8c", {"post_id": "post-8"}) is m.bids["bid-6"]
    assert withdraw_target(m, "M-849", {"post_id": "post-8"}) is None
    assert withdraw_target(m, "M-14b", {"post_id": "post-9"}) is None


@pytest.mark.parametrize("args", [
    {}, {"post_id": None}, {"post_id": ""}, {"bid_id": "bid-3"}, {"post": "post-7"}, {"post_id": "bid-3"},
    {"post_id": "Post-7"}, {"post_id": " post-7"}, {"post_id": "post-70"}, {"post_id": "task-7"}, {"post_id": [7]},
])
def test_withdraw_bid_finds_nothing_without_the_post(args):
    m = market()
    add_post(m, "post-7", "B-c53")
    add_bid(m, 3, "post-7", "M-14b", 22.0)
    assert withdraw_target(m, "M-14b", args) is None


# 1.2 The dispatcher; rounds.md 6.6, 7


def record(calls, answer=(True, "done")):
    def action(m, p, args, fixed):
        calls.append((m, p, args, fixed, m.actor))
        return answer
    return action


def test_compute_is_registered_and_always_fails():
    assert ACTIONS["compute"] is actions.compute
    for mode in config.MODES:
        m = market(mode)
        for pid in ("B-c53", "M-14b"):
            assert run(m, pid, "compute", {"code": "print(1)"}) == (False, COMPUTE_AS_ACTION)
            assert m.actor is None
    assert COMPUTE_AS_ACTION == ("the program did not run: compute is a function you call before act, not an action "
                                 "(at most 1 call a turn)")


def test_an_action_runs_with_the_participant_its_arguments_and_the_fixed_targets(monkeypatch):
    calls = []
    monkeypatch.setitem(ACTIONS, "post_task", record(calls, (True, "posted task-1 as post-1")))
    monkeypatch.setitem(ACTIONS, "pay", record(calls, (False, "you cannot pay yourself")))
    m = market()
    args, fixed = {"task_id": "task-1"}, {"contract": None}
    assert run(m, "B-c53", "post_task", args) == (True, "posted task-1 as post-1")
    assert run(m, "M-14b", "pay", args, fixed) == (False, "you cannot pay yourself")
    assert run(m, "B-d75", "pay", args, {}) == (False, "you cannot pay yourself")
    assert [(c[0] is m, c[1].id, c[2] is args, c[3], c[4]) for c in calls] == [
        (True, "B-c53", True, {}, "B-c53"), (True, "M-14b", True, {"contract": None}, "M-14b"),
        (True, "B-d75", True, {}, "B-d75")]
    assert calls[1][3] is fixed
    assert m.actor is None


def test_the_actor_is_reset_after_an_exception(monkeypatch):
    def broken(m, p, args, fixed):
        assert m.actor == "M-14b"
        raise ArithmeticError("the balance of M-14b is not finite")
    monkeypatch.setitem(ACTIONS, "bid", broken)
    m = market()
    with pytest.raises(ArithmeticError):
        run(m, "M-14b", "bid", {})
    assert m.actor is None


def test_what_an_action_credits_to_another_participant_waits_and_its_own_credit_lands(monkeypatch):
    def pays(m, p, args, fixed):
        state.credit(m, "M-14b", 5.0, "payments received")
        state.credit(m, p.id, 2.0, "escrow refunds")
        return True, "paid 5 to M-14b"
    monkeypatch.setitem(ACTIONS, "pay", pays)
    m = market()
    assert run(m, "B-c53", "pay", {}) == (True, "paid 5 to M-14b")
    assert (m.participants["M-14b"].balance, m.participants["B-c53"].balance) == (60.0, 62.0)
    assert [(c.to, c.amount, c.kind, c.payer) for c in m.deferred] == [("M-14b", 5.0, "transfer", "B-c53")]


def test_a_trial_on_a_copy_leaves_the_market_alone(monkeypatch):
    def posts(m, p, args, fixed):
        m.tasks["task-1"] = Task("task-1", p.id, 60, 9)
        return True, "x"
    monkeypatch.setitem(ACTIONS, "post_task", posts)
    m = market()
    s = copy.deepcopy(m)
    assert run(s, "B-c53", "post_task", {}) == (True, "x")
    assert (s.actor, m.actor, list(s.tasks), m.tasks) == (None, None, ["task-1"], {})


def test_the_name_is_checked_before_the_action_in_the_order_of_steps_1_to_6(monkeypatch):
    calls = []
    for name in ("bid", "pay", "post_task", "send_message", "verify", "compute"):
        monkeypatch.setitem(ACTIONS, name, record(calls))
    m = market("managers")
    m.participants["M-14b"].out_of_business = True
    m.participants["M-14b"].balance = -1.0
    before = copy.deepcopy(m)
    listed = "send_message, " + MANAGER_LIST
    refused = [
        # step 2 comes before every other step: no name
        (None, NO_NAME), ("", NO_NAME), ("  ", NO_NAME), (7, NO_NAME), (["bid"], NO_NAME), ({"a": 1}, NO_NAME),
        # step 3 before 4 and 6: not available in this market
        ("verify", f"'verify' is not available in this market. Your actions: {listed}."),
        ("note", f"'note' is not available in this market. Your actions: {listed}."),
        # step 4 before 6: unknown, with both additions
        ("end_turn", f"unknown action 'end_turn'. Your actions: {listed}."),
        (" bid ", f"unknown action ' bid '. Your actions: {listed}."),
        ("Bid", f"unknown action 'Bid'. Your actions: {listed}."),
        (" Submit_Contract", (f"unknown action ' Submit_Contract'. Your actions: {listed}. Contracts are created only "
                              "when a buyer accepts a bid (accept_bid). To deliver a result, or submit it for the "
                              "buyer's review, use deliver.")),
        # step 5 before 6: the other role's action
        ("post_task", "'post_task' is not available to a manager: it is a buyer's action"),
        ("accept_bid", "'accept_bid' is not available to a manager: it is a buyer's action"),
        # step 6 before the action's own checks (also compute's: SPEC_QUESTIONS.md, Q3)
        ("bid", OUT_OF_BUSINESS), ("pay", OUT_OF_BUSINESS), ("send_message", OUT_OF_BUSINESS),
        ("compute", OUT_OF_BUSINESS), ("withdraw_bid", OUT_OF_BUSINESS),
    ]
    for name, text in refused:
        assert run(m, "M-14b", name, {"post_id": "post-1"}, {"bid": "bid-1"}) == (False, text), name
    assert calls == [] and m == before and m.actor is None
    assert OUT_OF_BUSINESS == "you are out of business (balance at or below 0) and cannot act"


def test_the_mode_and_the_role_decide_the_names(monkeypatch):
    calls = []
    for name in ("send_message", "pay", "bid", "post_task"):
        monkeypatch.setitem(ACTIONS, name, record(calls))
    m = market("managers")
    assert run(m, "M-14b", "send_message", {}) == (True, "done")
    assert run(m, "B-c53", "send_message", {}) == (
        False, f"unknown action 'send_message'. Your actions: {BUYER_LIST}.")
    assert run(m, "B-c53", "bid", {}) == (False, "'bid' is not available to a buyer: it is a manager's action")
    assert run(m, "B-c53", "delegate", {}) == (False, f"unknown action 'delegate'. Your actions: {BUYER_LIST}.")
    assert run(m, "B-c53", "Create_Contract", {}) == (
        False, (f"unknown action 'Create_Contract'. Your actions: {BUYER_LIST}. Contracts are created only when a "
                "buyer accepts a bid (accept_bid)."))
    assert run(m, "B-c53", "submit", {}) == (False, f"unknown action 'submit'. Your actions: {BUYER_LIST}.")
    off = market("off")
    for pid, listed in (("B-c53", BUYER_LIST), ("M-14b", MANAGER_LIST)):
        assert run(off, pid, "send_message", {}) == (
            False, f"'send_message' is not available in this market. Your actions: {listed}.")
    every = market("all")
    assert run(every, "B-c53", "send_message", {}) == (True, "done")
    assert run(every, "M-14b", "delegate", {}) == (
        False, f"unknown action 'delegate'. Your actions: send_message, {MANAGER_LIST}.")
    assert len(calls) == 2


def test_only_the_status_puts_a_participant_out_of_business(monkeypatch):
    calls = []
    for name in ("post_task", "bid"):
        monkeypatch.setitem(ACTIONS, name, record(calls))
    m = market()
    m.participants["B-c53"].balance = -5.0  # a buyer never gets the text, whatever its balance
    m.participants["M-14b"].balance = 0.0  # a balance at 0 alone does not make a manager out of business
    assert run(m, "B-c53", "post_task", {}) == (True, "done")
    assert run(m, "M-14b", "bid", {}) == (True, "done")
    m.participants["M-849"].out_of_business = True  # the status, also with a positive balance
    assert run(m, "M-849", "bid", {}) == (False, OUT_OF_BUSINESS)
    assert len(calls) == 2


def test_the_copy_a_trial_runs_on_has_its_own_status(monkeypatch):
    monkeypatch.setitem(ACTIONS, "bid", record([]))
    m = market()
    s = copy.deepcopy(m)
    s.participants["M-14b"].out_of_business = True
    assert run(s, "M-14b", "bid", {}) == (False, OUT_OF_BUSINESS)
    assert run(m, "M-14b", "bid", {}) == (True, "done")


# rounds.md 6.6, step 1: the implicit targets


def test_an_action_with_no_entry_leaves_nothing_implicit():
    m = market("all")
    for name in ("post_task", "pay", "compute", "bid", "send_message", "nonsense", None, 7, ["pay"], "", " pay "):
        assert implicit(m, "B-c53", name, {"contract_id": "contract-1"}) == {}
        assert implicit(m, "M-14b", name, {"contract_id": "contract-1"}) == {}


def test_the_implicit_targets_come_from_the_actions_entry(monkeypatch):
    calls = []

    def resolve(s, p, args):
        calls.append((s, p.id, args))
        return {"contract": "contract-3"}
    monkeypatch.setitem(IMPLICIT, "pay", resolve)
    monkeypatch.setitem(IMPLICIT, "accept_bid", lambda s, p, args: {"price": 20.0, "threshold": None})
    m = market()
    s = copy.deepcopy(m)
    args = {"to": "M-14b", "amount": 5}
    assert implicit(s, "B-c53", "pay", args) == {"contract": "contract-3"}
    assert implicit(s, "M-849", "pay", args) == {"contract": "contract-3"}
    assert [(c[0] is s, c[1], c[2] is args) for c in calls] == [(True, "B-c53", True), (True, "M-849", True)]
    assert implicit(s, "B-c53", "accept_bid", {"bid_id": "bid-1"}) == {"price": 20.0, "threshold": None}
    assert s == m and s.actor is None


def test_a_name_the_market_refuses_fixes_nothing(monkeypatch):
    monkeypatch.setitem(IMPLICIT, "accept_bid", lambda s, p, args: {"price": 20.0, "threshold": None})
    monkeypatch.setitem(IMPLICIT, "withdraw_bid", lambda s, p, args: {"bid": "bid-1"})
    m = market()
    assert implicit(m, "M-14b", "accept_bid", {"bid_id": "bid-1"}) == {}  # the other role's action
    assert implicit(m, "B-c53", "withdraw_bid", {"post_id": "post-1"}) == {}
    assert implicit(m, "M-14b", "withdraw_bid", {"post_id": "post-1"}) == {"bid": "bid-1"}
    m.participants["M-14b"].out_of_business = True
    assert implicit(m, "M-14b", "withdraw_bid", {"post_id": "post-1"}) == {}


# The helpers read; they change nothing


def test_no_helper_changes_the_market():
    m = bids_market()
    add_task(m, 25, "B-82c", "posted", post="post-25")
    add_task(m, 30, "B-82c")
    add_contract(m, "contract-3", "B-82c", "M-0ed", "escrow", results=[result("R-0a1", 2), result("R-0a2", 9)],
                 deliveries=[Delivery("R-0a1", 2, 1, outcome="review")])
    add_contract(m, "contract-4", "B-b79", "M-0ed", ended=2, results=[result("R-0b1", 2)])
    before = copy.deepcopy(m)
    for pid in m.participants:
        p = m.participants[pid]
        for kind in ("task", "post", "bid", "contract"):
            hint(m, p, kind)
            for raw in ("3", "25", "nothing", 61, None, ""):
                lookup(m, p, {"x": raw}, kind, "x")
        for raw in ("contract-3", "contract-4", "contract-9"):
            own_contract(m, p, {"contract_id": raw})
        participant(m, "M-0ed"), participant(m, "nobody")
        short(p, 100.0, "x"), short(p, 1.0, "x")
        for c in m.contracts.values():
            contract_gone(m, c), results_text(m, c)
            for label in ("R-0a1", "R-0a2", "R-0b1", "R-fff"):
                find_result(m, p, c, label, "tested")
        withdraw_target(m, pid, {"post_id": "post-25"})
        implicit(m, pid, "pay", {})
    assert m == before


# The argument refusals of spec/vectors/round_order.jsonl that the helpers alone give


VECTORS = Path(__file__).parent.parent / "spec" / "vectors" / "round_order.jsonl"
PURPOSE = {"deliver": "delivered", "test_result": "tested", "delegate_run": "refined", "run_myself": "refined"}
ID_ARGUMENT = {"post_task": "task_id", "edit_post": "post_id", "withdraw_post": "post_id", "bid": "post_id",
               "withdraw_bid": "post_id", "accept_bid": "bid_id"}


def seen(case, pid):
    """The state a participant's turn saw in a first phase, as far as the hints, the result lookup and the
    contract's state checks read it (vectors/README.md, "Fields")."""
    given_, listed = case["input"], case["input"]["market"]
    m = Market(seed=0, cfg=config.resolve({"message_channels": given_["message_channels"]}))
    m.day, m.round = given_["day"], given_["round"]
    for q, fields in listed["participants"].items():
        state.add_participant(m, q, fields["role"])
        own = fields if q == pid else fields.get("start_state", fields)
        m.participants[q].out_of_business = own["out_of_business"]
    me, buyer = listed["participants"][pid], listed["participants"][pid]["role"] == "buyer"
    for task in me.get("queued_tasks", []):
        m.tasks[task] = Task(task, pid, 60, 0)
    for post in listed["open_posts"]:
        f = listed["posts"].get(post)
        m.posts[post] = Post(post, f["task"], f["buyer"], *f["posted"]) if f else Post(post, "", "", 0, 1)
    for bid, manager, price, thr, post in me.get("open_bids_on_posts", []):
        m.posts.setdefault(post, Post(post, "", pid, 0, 1))
        m.bids[bid] = Bid(bid, post, manager, price, thr)
    for bid, post in me.get("open_bids", []):
        m.bids[bid] = Bid(bid, post, pid, 0.0)
    for cid, task, ended in me.get("contracts", []):
        m.contracts[cid] = Contract(cid, task, "", pid if buyer else "", "" if buyer else pid, "plain", 0.0, None, 0, 0,
                                    ended=0 if ended else None)
    for cid, f in listed["contracts"].items():
        m.contracts[cid] = Contract(
            cid, f["task"], f["post"], f["buyer"], f["manager"], f["form"], f["price"], f["threshold"], f["signed"][0],
            f["deadline_day"], ended=f["ended"][0] if f["ended"] else None,
            release_day=1 if f["state"] == "RELEASED" else None,
            closed="late" if f["state"] == "CLOSED UNRELEASED" else None,
            results=[Result(r["result"], r["worker"], r["kind"], r["refines"], r["started"], r["arrival_day"], 5.0)
                     for r in f["results"]])
    return m


def helper_text(case, pid, position, name, args, message, refused):
    """(family, the helper's text) for an expected refusal that a helper alone gives, else None."""
    amount_name = re.fullmatch(r"(price|amount) must be (a number, got .*|a positive number)", message, re.S)
    if amount_name:
        return "amount", amount(args, amount_name[1])[1]
    if message.startswith("threshold must be "):
        return "threshold", threshold(args["threshold"])[1]
    if message.startswith("quality must be "):
        return "quality", quality(args["quality"], 1)[1]
    if message.startswith("missing argument '"):
        return "missing", missing(args, message.split("'")[1])
    if message.startswith("no participant with id '"):
        return "participant", participant(seen(case, pid), args["to"])[1]
    if case["input"]["phase"] != "first" or position or not refused:
        return None  # the state the action failed on is not the phase's start state
    m = seen(case, pid)
    p = m.participants[pid]
    kind = re.match(r"no (task|post|bid|contract) with id '", message)
    if kind:
        return kind[1] + " hint", unknown(m, p, kind[1], args[ID_ARGUMENT.get(name, "contract_id")])
    if " is not one of your contracts. " in message:
        return "not yours", own_contract(m, p, args)[1]
    if name not in PURPOSE:
        return None
    c = state.find(m, "contract", args.get("contract_id"))
    if message.startswith("no result '") or " has not arrived yet (it arrives " in message:
        raw = args["refine" if PURPOSE[name] == "refined" else "result"]
        return "result", find_result(m, p, c, raw, PURPOSE[name])[1]
    if message.endswith(": it takes no more runs, tests or deliveries") or message.endswith(
            ": it takes no more runs, tests or submissions"):
        return "contract gone", contract_gone(m, c, closed=name != "deliver")
    return None


def test_the_argument_refusals_of_the_round_order_vectors():
    checked, wrong = Counter(), []
    for line in VECTORS.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        for pid, plan in case["input"]["plans"].items():
            role = case["input"]["market"]["participants"][pid]["role"]
            for position, (action, expected) in enumerate(zip(plan, case["expected"]["actions"][pid])):
                name, args = action["name"], action["arguments"]
                own = isinstance(name, str) and name in actions.OWN[role] + ("pay", "send_message")
                if expected["status"] != "ERROR" or not own:
                    continue
                message = expected["line"].split(": ERROR: ", 1)[1]
                got = helper_text(case, pid, position, name, args, message, expected["outcome"] == "refused")
                if got:
                    checked[got[0]] += 1
                    if got[1] != message:
                        wrong.append((case["id"], pid, position, got[1], message))
    assert wrong == []
    assert checked == {"missing": 634, "amount": 28, "threshold": 34, "quality": 6, "participant": 30, "task hint": 2,
                       "post hint": 15, "bid hint": 10, "contract hint": 52, "not yours": 22, "result": 9,
                       "contract gone": 37}
