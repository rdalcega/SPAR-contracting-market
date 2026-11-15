"""The buyer's actions (market/actions_buyer.py) against spec/actions-buyer.md, 2 to 12: every check's refusal in
the section's order, the success text, the state and the money after, the notifications with their order and stamps.

Every expected text is copied from the spec section the test names. Each refusal is checked to change nothing: the
market is compared with a deep copy taken before the action.
"""

import copy

import pytest

from market import actions, actions_buyer, config, phase, processes, rng, state
from market.state import Bid, Contract, Credit, Delivery, Dispute, Market, Post, Result, Task

B, B2 = "B-c53", "B-d75"
M1, M2, M3 = "M-14b", "M-849", "M-f8c"
PM = "\u00b1"
RULE_E = (
    "an escrow contract can be ended after a rejection that stands (its manager can no longer dispute it, or the "
    "arbiter ruled for you), or after its deadline day with nothing released if it was signed on or before that "
    "day; never while a submission is under your review or a rejection is in dispute"
)
RULE_P = (
    "a plain contract can be ended only after its deadline day with nothing delivered, if it was signed on or "
    "before that day"
)
NO_BIDS = "Open bids on your posts: none."


def market(day=3, rnd=2, overrides=None, buyers=(B, B2), managers=(M1, M2, M3)):
    """A market on day 3, round 2 (round index 8) with a log that is a list; every balance is 60."""
    m = Market(seed=1, cfg=config.resolve(overrides))
    for pid in buyers:
        state.add_participant(m, pid, "buyer")
    for pid in managers:
        state.add_participant(m, pid, "manager")
    m.day, m.round = day, rnd
    m.log = [].append
    return m


def records(m, kind=None):
    return [r for r in m.log.__self__ if kind is None or r["type"] == kind]


def task(m, n, buyer=B, status="queued", due=9, **fields):
    t = m.tasks[f"task-{n}"] = Task(f"task-{n}", buyer, 60, due, state=status, **fields)
    return t


def post(m, pid, buyer=B, day=1, rnd=1, due=9, **fields):
    """An open post made on day 1, round 1 unless said (its bids are open by round index 8), with its task."""
    tid = "task-" + pid.split("-")[1]
    if tid not in m.tasks:
        task(m, tid.split("-")[1], buyer, "posted", due, post=pid)
    po = m.posts[pid] = Post(pid, tid, buyer, day, rnd, **fields)
    return po


def bid(m, n, post_id, manager, price, thr=None, status="open"):
    b = m.bids[f"bid-{n}"] = Bid(f"bid-{n}", post_id, manager, price, thr, status)
    m.bids_created = max(m.bids_created, n)
    return b


def contract(m, cid="contract-1", form="plain", buyer=B, manager=M1, price=20.0, due=9, signed=1, deadline=None,
             **fields):
    """A contract and its task; the deadline is processes.md 3.1's unless given."""
    tid = "task-" + cid.split("-")[1]
    if tid not in m.tasks:
        task(m, tid.split("-")[1], buyer, "contracted", due, contract=cid)
    if deadline is None:
        deadline = config.deadline(m.cfg, due, signed)
    c = m.contracts[cid] = Contract(cid, tid, "post" + cid.removeprefix("contract"), buyer, manager, form, price,
                                    5.0 if form == "escrow" else None, signed, deadline, **fields)
    return c


def deliver(c, label, day, outcome=None, q=6.0, **fields):
    """A delivery (plain) or a submission (escrow, with its outcome) of a new arrived result."""
    c.results.append(Result(label, "employee", "fresh", None, 1, 2, q))
    d = Delivery(label, day, 1, outcome=outcome, **fields)
    c.deliveries.append(d)
    return d


def released(c, how, day, received=None):
    """The escrow of a contract released on its latest submission (processes.md, 6.8)."""
    c.release_day, c.release_how, c.paid, c.received = day, how, c.price, received


def data(m):
    return {k: v for k, v in vars(m).items() if k != "log"}


def refuse(m, pid, name, args, text, fixed=None):
    """The action fails with exactly that text and changes nothing (1.2)."""
    before, n = copy.deepcopy(m), len(records(m))
    assert actions.run(m, pid, name, args, fixed) == (False, text)
    assert data(m) == data(before)
    assert len(records(m)) == n


def ok(m, pid, name, args, text, fixed=None):
    assert actions.run(m, pid, name, args, fixed) == (True, text)


def pending(m, pid):
    return m.participants[pid].pending


def ledger(m, pid):
    return m.participants[pid].ledger


def balance(m, pid):
    return m.participants[pid].balance


def set_balance(m, pid, x):
    m.participants[pid].balance = x


# The registries and section 12


def test_every_buyer_action_is_registered():
    for name in actions.OWN["buyer"]:
        assert actions.ACTIONS[name] is getattr(actions_buyer, name)
    for name in ("accept_bid", "test_delivery", "approve_submission", "reject_submission"):
        assert name in actions.IMPLICIT
    for name in ("post_task", "edit_post", "withdraw_post", "keep_delivery", "pay_escrow_arbiter", "end_contract"):
        assert name not in actions.IMPLICIT


def test_the_priority_classes_of_section_12():
    table = {
        0: ("post_task", "test_delivery", "keep_delivery", "pay_escrow_arbiter", "pay", "send_message", "compute",
            "no_such_action"),
        1: ("deliver",),
        2: ("approve_submission", "reject_submission", "end_contract"),
        3: ("delegate_run", "run_myself", "test_result", "dispute"),
        4: ("accept_bid", "withdraw_post"),
        5: ("withdraw_bid",),
        6: ("bid",),
        7: ("edit_post",),
    }
    for cls, names in table.items():
        for name in names:
            role = "manager" if name in actions.OWN["manager"] else "buyer"
            assert phase.classify(role, "all", name) == (name not in ("compute", "no_such_action"), cls)


# 2. post_task


def test_post_task_checks_in_order():
    m = market()
    task(m, 1), task(m, 2, B2), task(m, 4)
    task(m, 3, status="posted", post="post-3")
    task(m, 16, status="contracted", contract="contract-16")
    task(m, 5, status="withdrawn", post="post-5")
    queued = "Your queued (not yet posted) tasks: task-1, task-4."
    refuse(m, B, "post_task", {}, "missing argument 'task_id'")
    refuse(m, B, "post_task", {"task_id": "", "form": "premium"}, "missing argument 'task_id'")
    refuse(m, B, "post_task", {"task": "task-1"}, "missing argument 'task_id'")
    refuse(m, B, "post_task", {"task_id": "task-9", "form": "premium"}, f"no task with id 'task-9'. {queued}")
    refuse(m, B, "post_task", {"task_id": "Task-1"}, f"no task with id 'Task-1'. {queued}")
    refuse(m, B2, "post_task", {"task_id": 7}, "no task with id '7'. Your queued (not yet posted) tasks: task-2.")
    refuse(m, M1, "post_task", {"task_id": 1}, "'post_task' is not available to a manager: it is a buyer's action")
    refuse(m, B, "post_task", {"task_id": "task-2", "form": "premium"}, "task-2 is not your task")
    refuse(m, B2, "post_task", {"task_id": 3, "form": "premium"}, "task-3 is not your task")
    refuse(m, B, "post_task", {"task_id": 3, "form": "premium"},
           "task-3 is already posted as post-3, which stays open until you accept a bid on it or withdraw it "
           "(edit_post changes its stated form and threshold)")
    refuse(m, B, "post_task", {"task_id": "16", "form": "premium"}, "task-16 is already contracted")
    refuse(m, B, "post_task", {"task_id": "task-5", "price": 3}, "task-5 is already withdrawn")
    form = 'form must be "plain" or "escrow" (or left out for no preferred form), got '
    for value, got in (("premium", "'premium'"), (False, "False"), ("Escrow", "'Escrow'"), (" escrow ", "' escrow '"),
                       (0, "0"), (["plain"], "['plain']")):
        refuse(m, B, "post_task", {"task_id": 1, "form": value, "price": 5, "threshold": 99}, form + got)
    price = "posts carry no price, so post_task takes no {}: managers quote their own price when they bid"
    refuse(m, B, "post_task", {"task_id": 1, "form": "plain", "price": 5, "threshold": 7}, price.format("price"))
    refuse(m, B, "post_task", {"task_id": 1, "escrow_terms": 0, "menu": [], "terms": False, "max_price": 1,
                               "price": 2}, price.format("max_price"))
    refuse(m, B, "post_task", {"task_id": 1, "escrow_terms": " ", "budget": ""}, price.format("escrow_terms"))
    for name in ("budget", "max_price", "price", "terms", "menu", "escrow_menu", "escrow_terms"):
        refuse(m, B, "post_task", {"task_id": 1, name: 0}, price.format(name))
    only = 'only a post that prefers escrow (form="escrow") takes a threshold'
    refuse(m, B, "post_task", {"task_id": 1, "threshold": 7}, only)
    refuse(m, B, "post_task", {"task_id": 1, "form": "plain", "threshold": "high"}, only)
    refuse(m, B, "post_task", {"task_id": 1, "form": "none", "threshold": 7}, only)
    refuse(m, B, "post_task", {"task_id": 1, "form": "", "threshold": "none"}, only)
    for value, text in ((11, "a quality between 0 and 10, got 11"), ("high", "a quality between 0 and 10, got 'high'"),
                        (True, "a quality between 0 and 10, got True"), ([7], "a quality between 0 and 10, got [7]"),
                        ("none", "a quality between 0 and 10, got 'none'"),
                        (7.5, "a whole number between 0 and 10, got 7.5"),
                        ("7.5", "a whole number between 0 and 10, got '7.5'")):
        refuse(m, B, "post_task", {"task_id": 1, "form": "escrow", "threshold": value}, "threshold must be " + text)


def test_post_task_success_texts_and_state():
    m = market()
    task(m, 1), task(m, 3)
    ok(m, B, "post_task", {"task_id": "task-1", "form": "escrow", "threshold": 7, "message": "x"},
       "posted task-1 as post-1; prefers escrow, threshold 7")
    assert m.posts["post-1"] == Post("post-1", "task-1", B, 3, 2, "escrow", 7.0)
    assert (m.tasks["task-1"].state, m.tasks["task-1"].post) == ("posted", "post-1")
    ok(m, B, "post_task", {"task_id": 3, "form": "plain"}, "posted task-3 as post-3; prefers a plain contract")
    assert m.posts["post-3"] == Post("post-3", "task-3", B, 3, 2, "plain", None)
    assert list(m.posts) == ["post-1", "post-3"]
    assert all(not p.pending for p in m.participants.values())
    assert [(r["type"], r["post"], r["task"], r["form"], r["threshold"]) for r in records(m)] == [
        ("post", "post-1", "task-1", "escrow", 7.0), ("post", "post-3", "task-3", "plain", None)]
    assert state.sealed(m, m.posts["post-1"])


@pytest.mark.parametrize("args, tail, form, threshold", [
    ({}, "", None, None),
    ({"form": "escrow"}, "; prefers escrow", "escrow", None),
    ({"form": "escrow", "threshold": " 7 "}, "; prefers escrow, threshold 7", "escrow", 7.0),
    ({"form": "escrow", "threshold": -0.0}, "; prefers escrow, threshold 0", "escrow", 0.0),
    ({"form": "escrow", "threshold": "1e1"}, "; prefers escrow, threshold 10", "escrow", 10.0),
    ({"form": "none"}, "", None, None),
    ({"form": "any"}, "", None, None),
    ({"form": "either"}, "", None, None),
    ({"form": "both"}, "", None, None),
    ({"form": "no preference"}, "", None, None),
    ({"form": "", "threshold": None, "price": ""}, "", None, None),
])
def test_post_task_forms(args, tail, form, threshold):
    m = market()
    task(m, 1)
    ok(m, B, "post_task", {"task_id": "1", **args}, "posted task-1 as post-1" + tail)
    assert (m.posts["post-1"].form, m.posts["post-1"].threshold) == (form, threshold)
    assert repr(m.posts["post-1"].threshold) != "-0.0"


def test_a_task_is_posted_again_as_its_kth_post():
    m = market()
    task(m, 8, ended=["contract-8"])
    ok(m, B, "post_task", {"task_id": 8, "form": "escrow", "threshold": 6},
       "posted task-8 as post-8-2; prefers escrow, threshold 6")
    assert m.posts["post-8-2"].task == "task-8" and m.tasks["task-8"].post == "post-8-2"


# 3. edit_post


def test_edit_post_checks_in_order():
    m = market()
    post(m, "post-1", form="escrow"), post(m, "post-2", B2), post(m, "post-6", form="plain")
    post(m, "post-3", closed="withdrawn", closed_day=2, closed_round=1)
    post(m, "post-4", closed="accepted", closed_day=2, closed_round=1)
    post(m, "post-5", form="escrow", threshold=5.0), post(m, "post-7")
    opened = "Open posts: post-1, post-2, post-6, post-5, post-7."
    refuse(m, B, "edit_post", {"form": "plain"}, "missing argument 'post_id'")
    refuse(m, B, "edit_post", {"post": "post-1"}, "missing argument 'post_id'")
    refuse(m, B, "edit_post", {"post_id": "post-9", "price": 1}, f"no post with id 'post-9'. {opened}")
    refuse(m, B, "edit_post", {"post_id": "task-1"}, f"no post with id 'task-1'. {opened}")
    refuse(m, B, "edit_post", {"post_id": 2, "price": 1}, "post-2 is not your post")
    refuse(m, B, "edit_post", {"post_id": 3, "price": 1}, "post-3 is closed: you withdrew it")
    refuse(m, B, "edit_post", {"post_id": "post-4", "form": "x"}, "post-4 is closed: a bid on it was accepted")
    price = "posts carry no price, so edit_post takes no {}: managers quote their own price when they bid"
    refuse(m, B, "edit_post", {"post_id": 1, "form": "premium", "terms": "x", "price": 1}, price.format("price"))
    refuse(m, B, "edit_post", {"post_id": 1, "escrow_menu": 0}, price.format("escrow_menu"))
    form = 'form must be "plain" or "escrow" (or left out for no preferred form), got '
    refuse(m, B, "edit_post", {"post_id": 1, "form": "ESCROW", "threshold": 99}, form + "'ESCROW'")
    refuse(m, B, "edit_post", {"post_id": 1, "form": False, "threshold": "none"}, form + "False")
    only = 'only a post that prefers escrow (form="escrow") takes a threshold'
    refuse(m, B, "edit_post", {"post_id": 1, "form": "plain", "threshold": 99}, only)
    refuse(m, B, "edit_post", {"post_id": 1, "form": "any", "threshold": 5}, only)
    refuse(m, B, "edit_post", {"post_id": 6, "threshold": 5}, only)
    refuse(m, B, "edit_post", {"post_id": 7, "threshold": "None"}, only)
    for value, text in (("None", "a quality between 0 and 10, got 'None'"),
                        (" none ", "a quality between 0 and 10, got ' none '"),
                        ("null", "a quality between 0 and 10, got 'null'"),
                        (False, "a quality between 0 and 10, got False"),
                        ({"t": 5}, "a quality between 0 and 10, got {'t': 5}"),
                        (4.5, "a whole number between 0 and 10, got 4.5")):
        refuse(m, B, "edit_post", {"post_id": 1, "threshold": value}, "threshold must be " + text)
        refuse(m, B, "edit_post", {"post_id": 6, "form": "escrow", "threshold": value}, "threshold must be " + text)
    same = "{} already states this ({}): nothing changed"
    refuse(m, B, "edit_post", {"post_id": 1}, same.format("post-1", "prefers escrow"))
    refuse(m, B, "edit_post", {"post_id": 1, "form": "escrow"}, same.format("post-1", "prefers escrow"))
    refuse(m, B, "edit_post", {"post_id": 1, "threshold": "none"}, same.format("post-1", "prefers escrow"))
    refuse(m, B, "edit_post", {"post_id": 5}, same.format("post-5", "prefers escrow, threshold 5"))
    refuse(m, B, "edit_post", {"post_id": 5, "form": "escrow"}, same.format("post-5", "prefers escrow, threshold 5"))
    refuse(m, B, "edit_post", {"post_id": 5, "threshold": "5"}, same.format("post-5", "prefers escrow, threshold 5"))
    refuse(m, B, "edit_post", {"post_id": 5, "form": ""}, same.format("post-5", "prefers escrow, threshold 5"))
    refuse(m, B, "edit_post", {"post_id": 6, "form": "plain"}, same.format("post-6", "prefers a plain contract"))
    refuse(m, B, "edit_post", {"post_id": 6, "threshold": "none"}, same.format("post-6", "prefers a plain contract"))
    refuse(m, B, "edit_post", {"post_id": 7, "form": "either"}, same.format("post-7", "no preferred form"))
    refuse(m, B, "edit_post", {"post_id": 7, "form": "both", "threshold": "none"},
           same.format("post-7", "no preferred form"))


def test_edit_post_examples_and_the_threshold_that_stays():
    m = market()
    po = post(m, "post-1", form="escrow")
    stand = "; its bids stand as they were made"
    ok(m, B, "edit_post", {"post_id": "post-1", "threshold": 0},
       "post-1 now states: prefers escrow, threshold 0 (was: prefers escrow)" + stand)
    assert (po.form, po.threshold) == ("escrow", 0.0)
    ok(m, B, "edit_post", {"post_id": "post-1", "form": "none"},
       "post-1 now states: no preferred form (was: prefers escrow, threshold 0)" + stand)
    assert (po.form, po.threshold) == (None, None)
    ok(m, B, "edit_post", {"post_id": 1, "form": "escrow"},
       "post-1 now states: prefers escrow (was: no preferred form)" + stand)
    ok(m, B, "edit_post", {"post_id": 1, "form": "escrow", "threshold": 6},
       "post-1 now states: prefers escrow, threshold 6 (was: prefers escrow)" + stand)
    ok(m, B, "edit_post", {"post_id": 1, "threshold": "7"},
       "post-1 now states: prefers escrow, threshold 7 (was: prefers escrow, threshold 6)" + stand)
    ok(m, B, "edit_post", {"post_id": 1, "form": "plain"},
       "post-1 now states: prefers a plain contract (was: prefers escrow, threshold 7)" + stand)
    ok(m, B, "edit_post", {"post_id": 1, "form": "escrow"},
       "post-1 now states: prefers escrow (was: prefers a plain contract)" + stand)
    ok(m, B, "edit_post", {"post_id": 1, "form": "escrow", "threshold": 3},
       "post-1 now states: prefers escrow, threshold 3 (was: prefers escrow)" + stand)
    ok(m, B, "edit_post", {"post_id": 1, "threshold": "none"},
       "post-1 now states: prefers escrow (was: prefers escrow, threshold 3)" + stand)
    ok(m, B, "edit_post", {"post_id": 1, "form": "plain", "threshold": "none"},
       "post-1 now states: prefers a plain contract (was: prefers escrow)" + stand)
    assert (po.form, po.threshold, po.day, po.round, po.closed) == ("plain", None, 1, 1, None)
    assert [r["type"] for r in records(m)] == ["post_edited"] * 10


def test_edit_post_notifies_each_manager_with_an_open_bid_by_manager_id():
    m = market()
    po = post(m, "post-1", day=3, rnd=1)
    bid(m, 1, "post-1", M3, 30.0), bid(m, 2, "post-1", M1, 25.0, 5.0), bid(m, 3, "post-1", M2, 9.0, status="withdrawn")
    post(m, "post-2"), bid(m, 4, "post-2", M2, 12.0)
    m.participants[M1].out_of_business = True
    bids = copy.deepcopy(m.bids)
    assert state.sealed(m, po)
    ok(m, B, "edit_post", {"post_id": 1, "form": "escrow", "threshold": 7},
       "post-1 now states: prefers escrow, threshold 7 (was: no preferred form); its bids stand as they were made")
    line = ("[day 3, round 2] B-c53 changed post-1's stated preference to: prefers escrow, threshold 7 (was: no "
            "preferred form); your bid on it stands as you made it")
    assert [(r["to"], r["text"]) for r in records(m, "notification")] == [(M1, line), (M3, line)]
    assert (pending(m, M1), pending(m, M3), pending(m, M2), pending(m, B)) == ([line], [line], [], [])
    assert m.bids == bids and (po.day, po.round) == (3, 1)


# 4. withdraw_post


def test_withdraw_post_checks_in_order():
    m = market()
    post(m, "post-1"), post(m, "post-2", B2)
    post(m, "post-3", closed="withdrawn", closed_day=2, closed_round=1)
    post(m, "post-11", closed="accepted", closed_day=2, closed_round=1)
    refuse(m, B, "withdraw_post", {}, "missing argument 'post_id'")
    refuse(m, B, "withdraw_post", {"post_id": "post 1"}, "no post with id 'post 1'. Open posts: post-1, post-2.")
    refuse(m, B, "withdraw_post", {"post_id": "post-2"}, "post-2 is not your post")
    refuse(m, B, "withdraw_post", {"post_id": 3}, "post-3 is not open: you withdrew it")
    refuse(m, B, "withdraw_post", {"post_id": "post-11"}, "post-11 is not open: a bid on it was accepted")
    m.posts.pop("post-1"), m.posts.pop("post-2")
    refuse(m, B, "withdraw_post", {"post_id": "1"}, "no post with id '1'. Open posts: none.")


def test_withdraw_post_loses_every_open_bid_in_bid_order():
    m = market()
    po = post(m, "post-12")
    bid(m, 1, "post-12", M3, 30.0), bid(m, 2, "post-12", M1, 25.0, 5.0), bid(m, 3, "post-12", M2, 9.0)
    bid(m, 4, "post-12", M2, 8.0, status="withdrawn")
    post(m, "post-2"), bid(m, 5, "post-2", M2, 12.0)
    ok(m, B, "withdraw_post", {"post_id": "post-12"}, "withdrew post-12: task-12 leaves the market; 3 open bids on "
                                                      "it lost")
    assert (po.closed, po.closed_day, po.closed_round) == ("withdrawn", 3, 2)
    assert m.tasks["task-12"].state == "withdrawn"
    assert [b.status for b in m.bids.values()] == ["lost", "lost", "lost", "withdrawn", "open"]
    assert [(r["to"], r["text"]) for r in records(m, "notification")] == [
        (M3, "[day 3, round 2] B-c53 withdrew post-12: your bid bid-1 on it is lost"),
        (M1, "[day 3, round 2] B-c53 withdrew post-12: your bid bid-2 on it is lost"),
        (M2, "[day 3, round 2] B-c53 withdrew post-12: your bid bid-3 on it is lost"),
    ]
    assert pending(m, M2) == ["[day 3, round 2] B-c53 withdrew post-12: your bid bid-3 on it is lost"]
    assert records(m, "post_withdrawn")[0]["bids_lost"] == ["bid-1", "bid-2", "bid-3"]
    refuse(m, B, "post_task", {"task_id": "task-12"}, "task-12 is already withdrawn")
    refuse(m, B, "withdraw_post", {"post_id": "post-12"}, "post-12 is not open: you withdrew it")
    refuse(m, B, "edit_post", {"post_id": "post-12"}, "post-12 is closed: you withdrew it")


def test_withdraw_post_counts_only_the_bids_its_buyer_could_see():
    m = market()
    post(m, "post-16"), bid(m, 1, "post-16", M1, 30.0), bid(m, 2, "post-16", M2, 20.0)
    m.participants[M1].out_of_business = True
    ok(m, B, "withdraw_post", {"post_id": 16}, "withdrew post-16: task-16 leaves the market; 1 open bid on it lost")
    assert [b.status for b in m.bids.values()] == ["lost", "lost"]
    assert pending(m, M1) == ["[day 3, round 2] B-c53 withdrew post-16: your bid bid-1 on it is lost"]
    post(m, "post-36"), bid(m, 3, "post-36", M1, 30.0)
    ok(m, B, "withdraw_post", {"post_id": "post-36"}, "withdrew post-36: task-36 leaves the market")
    assert m.bids["bid-3"].status == "lost" and len(pending(m, M1)) == 2
    post(m, "post-4")
    ok(m, B, "withdraw_post", {"post_id": "post-4"}, "withdrew post-4: task-4 leaves the market")
    post(m, "post-5", day=3, rnd=1), bid(m, 4, "post-5", M2, 30.0), bid(m, 5, "post-5", M3, 30.0)
    ok(m, B, "withdraw_post", {"post_id": "post-5"}, "withdrew post-5: task-5 leaves the market")
    assert (m.bids["bid-4"].status, m.bids["bid-5"].status) == ("lost", "lost")
    assert pending(m, M3) == ["[day 3, round 2] B-c53 withdrew post-5: your bid bid-5 on it is lost"]


# 5. accept_bid


def test_accept_bid_checks_in_order():
    m = market()
    post(m, "post-1"), bid(m, 1, "post-1", M1, 20.0, status="withdrawn"), bid(m, 2, "post-1", M2, 50.0, 5.0)
    post(m, "post-2", B2), bid(m, 3, "post-2", M1, 28.0, 6.0)
    post(m, "post-3", day=3, rnd=1), bid(m, 4, "post-3", M1, 10.0)
    post(m, "post-4", closed="accepted", closed_day=2, closed_round=1), bid(m, 5, "post-4", M1, 10.0, status="lost")
    bid(m, 6, "post-4", M2, 10.0, status="withdrawn"), bid(m, 7, "post-1", M3, 9.5)
    hint = ("Open bids on your posts: bid-2 (M-849 at 50 with threshold 5 (escrow) on post-1), bid-7 (M-f8c at 9.5 "
            "on post-1).")
    refuse(m, B, "accept_bid", {}, "missing argument 'bid_id'")
    refuse(m, B, "accept_bid", {"bid": "bid-2"}, "missing argument 'bid_id'")
    refuse(m, B, "accept_bid", {"bid_id": "bid-68"}, f"no bid with id 'bid-68'. {hint}")
    refuse(m, B, "accept_bid", {"bid_id": "bid-3"}, f"no bid with id 'bid-3'. {hint}")
    refuse(m, B, "accept_bid", {"bid_id": 3}, f"no bid with id '3'. {hint}")
    refuse(m, B, "accept_bid", {"bid_id": "bid-4"}, f"no bid with id 'bid-4'. {hint}")
    refuse(m, B2, "accept_bid", {"bid_id": "bid-2"},
           "no bid with id 'bid-2'. Open bids on your posts: bid-3 (M-14b at 28 with threshold 6 (escrow) on post-2).")
    refuse(m, B, "accept_bid", {"bid_id": "bid-5"}, "post-4 is already closed")
    refuse(m, B, "accept_bid", {"bid_id": 6}, "post-4 is already closed")
    refuse(m, B, "accept_bid", {"bid_id": "bid-1"}, "bid-1 is withdrawn")
    refuse(m, B, "accept_bid", {"bid_id": "bid-1"}, "bid-1 is withdrawn", {"price": 1.0, "threshold": None})
    m.participants[M2].out_of_business = True
    set_balance(m, B, 0.0)
    replaced = ("{} was replaced earlier this round: its terms are now {}, not the ones your turn saw: no contract "
                "was made")
    escrow = replaced.format("bid-2 on post-1", "50 credits with threshold 5 (escrow)")
    refuse(m, B, "accept_bid", {"bid_id": "bid-2"}, escrow, {"price": 49.0, "threshold": 5.0})
    refuse(m, B, "accept_bid", {"bid_id": "bid-2"}, escrow, {"price": 50.0, "threshold": 6.0})
    refuse(m, B, "accept_bid", {"bid_id": "bid-2"}, escrow, {"price": 50.0, "threshold": None})
    refuse(m, B, "accept_bid", {"bid_id": "bid-7"}, replaced.format("bid-7 on post-1", "9.5 credits (plain)"),
           {"price": 9.5, "threshold": 4.0})
    m.bids["bid-7"].price = 1.0
    refuse(m, B, "accept_bid", {"bid_id": "bid-7"}, replaced.format("bid-7 on post-1", "1 credit (plain)"),
           {"price": 9.5, "threshold": None})
    gone = "M-849 is out of business and cannot take contracts"
    refuse(m, B, "accept_bid", {"bid_id": "bid-2"}, gone)
    refuse(m, B, "accept_bid", {"bid_id": "bid-2"}, gone, {"price": 50.0, "threshold": 5.0})
    m.participants[M2].out_of_business = False
    set_balance(m, B, 54.99)
    refuse(m, B, "accept_bid", {"bid_id": "bid-2"},
           "insufficient balance for accepting the escrow bid bid-2 (its price 50 is held and the fee 5 charged): it "
           "costs 55, you have 54.99")
    assert not m.contracts


def test_accept_bid_example_of_the_replaced_bid():
    m = market()
    post(m, "post-3"), bid(m, 3, "post-3", M1, 28.0, 6.0)
    refuse(m, B, "accept_bid", {"bid_id": "bid-3"},
           "bid-3 on post-3 was replaced earlier this round: its terms are now 28 credits with threshold 6 (escrow), "
           "not the ones your turn saw: no contract was made", {"price": 30.0, "threshold": 6.0})
    ok(m, B, "accept_bid", {"bid_id": "bid-3"},
       "created contract-3 with M-14b at price 28 (escrow, threshold 6): 28 moved from your balance into escrow and "
       "the fee 2.80 was charged", {"price": 28.0, "threshold": 6.0})


def test_accept_bid_plain():
    m = market(buyers=("B-44c",), managers=("M-829", "M-fda"))
    post(m, "post-1", "B-44c", due=9), bid(m, 1, "post-1", "M-829", 32.0)
    set_balance(m, "B-44c", -5.0)
    ok(m, "B-44c", "accept_bid", {"bid_id": "bid-1"}, "created contract-1 with M-829 at price 32")
    assert m.contracts["contract-1"] == Contract("contract-1", "task-1", "post-1", "B-44c", "M-829", "plain", 32.0,
                                                 None, 3, 9)
    assert (balance(m, "B-44c"), ledger(m, "B-44c"), m.deferred) == (-5.0, [], [])
    po, t = m.posts["post-1"], m.tasks["task-1"]
    assert (po.closed, po.closed_day, po.closed_round) == ("accepted", 3, 2)
    assert (t.state, t.contract, t.post) == ("contracted", "contract-1", "post-1")
    assert m.bids["bid-1"].status == "accepted"
    assert pending(m, "M-829") == ["[day 3, round 2] B-44c accepted your bid bid-1: you now have contract-1 for "
                                   "task-1 at 32 credits."]
    assert [r["type"] for r in records(m)] == ["contract", "notification"]
    assert rng.stream_key("contract-1") == "task-1"


def test_accept_bid_escrow_effects_in_order():
    m = market(buyers=("B-44c",), managers=("M-fda", "M-14b", "M-849"))
    post(m, "post-6", "B-44c", due=9)
    bid(m, 12, "post-6", "M-849", 31.0), bid(m, 13, "post-6", "M-14b", 29.0, 5.0, status="withdrawn")
    bid(m, 14, "post-6", "M-14b", 35.0, 7.0), bid(m, 15, "post-6", "M-fda", 30.0, 6.0)
    post(m, "post-7", "B-44c"), bid(m, 16, "post-7", "M-fda", 11.0)
    m.participants["M-849"].out_of_business = True
    ok(m, "B-44c", "accept_bid", {"bid_id": "bid-15"},
       "created contract-6 with M-fda at price 30 (escrow, threshold 6): 30 moved from your balance into escrow and "
       "the fee 3 was charged")
    c = m.contracts["contract-6"]
    assert c == Contract("contract-6", "task-6", "post-6", "B-44c", "M-fda", "escrow", 30.0, 6.0, 3, 9)
    assert state.held(c) and c.paid == 0.0
    assert ledger(m, "B-44c") == [("paid into escrow (total)", 30.0), ("escrow fees", 3.0)]
    assert balance(m, "B-44c") == 27.0 and balance(m, "M-fda") == 60.0 and not ledger(m, "M-fda")
    assert m.deferred == []
    po = m.posts["post-6"]
    assert (po.closed, po.closed_day, po.closed_round) == ("accepted", 3, 2)
    assert (m.tasks["task-6"].state, m.tasks["task-6"].contract) == ("contracted", "contract-6")
    assert [b.status for b in m.bids.values()] == ["lost", "withdrawn", "lost", "accepted", "open"]
    log = [(r["type"], r.get("kind") or r.get("to"), r.get("text") or r.get("amount")) for r in records(m)]
    assert log == [
        ("contract", None, None),
        ("money", "paid into escrow (total)", -30.0),
        ("money", "escrow fees", -3.0),
        ("notification", "M-849", "[day 3, round 2] Your bid bid-12 on post-6 lost: B-44c accepted another bid."),
        ("notification", "M-14b", "[day 3, round 2] Your bid bid-14 on post-6 lost: B-44c accepted another bid."),
        ("notification", "M-fda", "[day 3, round 2] B-44c accepted your bid bid-15: you now have contract-6 for "
                                  "task-6 at 30 credits with threshold 6 (escrow)."),
    ]
    made = records(m, "contract")[0]
    assert (made["form"], made["price"], made["threshold"], made["deadline"], made["attempt"], made["held"],
            made["setup_fee"]) == ("escrow", 30.0, 6.0, 9, 1, 30.0, 3.0)
    refuse(m, "B-44c", "accept_bid", {"bid_id": "bid-12"}, "post-6 is already closed")
    refuse(m, "B-44c", "accept_bid", {"bid_id": "bid-15"}, "post-6 is already closed")


def test_accept_bid_effects_come_in_the_order_of_section_5(monkeypatch):
    """What each notification finds done: the contract, the money, the post and the task before any bid is lost;
    the bids lost before the accepted one is accepted."""
    m = market()
    post(m, "post-1"), bid(m, 1, "post-1", M1, 31.0), bid(m, 2, "post-1", M2, 30.0, 6.0), bid(m, 3, "post-1", M3, 9.0)
    seen, notify = [], state.notify

    def spy(mk, pid, text):
        seen.append((pid, list(mk.contracts), balance(mk, B), ledger(mk, B)[-1][0], mk.posts["post-1"].closed,
                     mk.tasks["task-1"].state, [b.status for b in mk.bids.values()]))
        notify(mk, pid, text)

    monkeypatch.setattr(state, "notify", spy)
    assert actions.run(m, B, "accept_bid", {"bid_id": "bid-2"})[0]
    done = (["contract-1"], 27.0, "escrow fees", "accepted", "contracted")
    assert seen == [(M1, *done, ["lost", "open", "open"]), (M3, *done, ["lost", "open", "lost"]),
                    (M2, *done, ["lost", "accepted", "lost"])]


def test_accept_bid_money_at_the_edge_and_small_prices():
    m = market()
    post(m, "post-3"), bid(m, 1, "post-3", M1, 20.0, 5.0)
    post(m, "post-2"), bid(m, 2, "post-2", M1, 50.0, 5.0)
    post(m, "post-4"), bid(m, 3, "post-4", M2, 1.0), bid(m, 4, "post-4", M3, 0.04, 0.0)
    set_balance(m, B, 77.0)
    ok(m, B, "accept_bid", {"bid_id": "bid-1"},
       "created contract-3 with M-14b at price 20 (escrow, threshold 5): 20 moved from your balance into escrow and "
       "the fee 2 was charged")
    assert balance(m, B) == 55.0
    ok(m, B, "accept_bid", {"bid_id": 2},
       "created contract-2 with M-14b at price 50 (escrow, threshold 5): 50 moved from your balance into escrow and "
       "the fee 5 was charged")
    assert balance(m, B) == 0.0
    refuse(m, B, "accept_bid", {"bid_id": "4"},
           "insufficient balance for accepting the escrow bid bid-4 (its price 0.04 is held and the fee 0 charged): "
           "it costs 0.04, you have 0.00")
    set_balance(m, B, 0.04)
    ok(m, B, "accept_bid", {"bid_id": "4"},
       "created contract-4 with M-f8c at price 0.04 (escrow, threshold 0): 0.04 moved from your balance into escrow "
       "and the fee 0 was charged", {"price": 0.04, "threshold": 0.0})
    assert ledger(m, B)[-1] == ("paid into escrow (total)", 0.04) and balance(m, B) == 0.0
    assert pending(m, M2) == ["[day 3, round 2] Your bid bid-3 on post-4 lost: B-c53 accepted another bid."]
    assert pending(m, M3) == ["[day 3, round 2] B-c53 accepted your bid bid-4: you now have contract-4 for task-4 at "
                              "0.04 credits with threshold 0 (escrow)."]
    post(m, "post-5"), bid(m, 5, "post-5", M2, 1.0)
    ok(m, B, "accept_bid", {"bid_id": "bid-5"}, "created contract-5 with M-849 at price 1")
    assert pending(m, M2)[-1] == ("[day 3, round 2] B-c53 accepted your bid bid-5: you now have contract-5 for task-5 "
                                  "at 1 credit.")
    assert list(m.contracts) == ["contract-3", "contract-2", "contract-4", "contract-5"]


def test_accept_bid_stops_the_episode_on_a_cost_that_is_not_finite():
    # config.md, 6, "Finiteness during an episode": "Such a value must never be used, shown or logged as an amount:
    # at the computation that yields it, the episode stops with status `error` ... and the error text names the
    # value that was not finite." Each of the price and the fee is finite; their sum, check 7's cost, is not.
    m = market(overrides={"contracts": {"escrow": {"fee_rate": 1}}})
    post(m, "post-1"), bid(m, 1, "post-1", M1, 1e308, 5.0), bid(m, 2, "post-1", M2, 1e308)
    before, n = copy.deepcopy(m), len(records(m))
    with pytest.raises(ArithmeticError, match=r"the cost of accepting the escrow bid bid-1 .* is not finite: inf"):
        actions.run(m, B, "accept_bid", {"bid_id": "bid-1"})
    assert data(m) == data(before) and len(records(m)) == n and m.actor is None
    half = market(overrides={"contracts": {"escrow": {"fee_rate": 0.5}}})  # a cost that stays finite is refused
    post(half, "post-1"), bid(half, 1, "post-1", M1, 1e308, 5.0)
    text = actions.run(half, B, "accept_bid", {"bid_id": "bid-1"})[1]
    assert text.startswith("insufficient balance for accepting the escrow bid bid-1 (its price 1e+308 is held")
    assert text.endswith(f"it costs {1.5e308:.0f}, you have 60.00")
    ok(m, B, "accept_bid", {"bid_id": "bid-2"}, "created contract-1 with M-849 at price 1e+308")  # plain: no cost


@pytest.mark.parametrize("day, deadline", [(1, 9), (5, 9), (6, 10), (8, 12), (9, 13), (11, 13), (20, 13)])
def test_accept_bid_sets_the_deadline_once(day, deadline):
    m = market(day=day, rnd=3)
    post(m, "post-1", due=9), bid(m, 1, "post-1", M1, 20.0)
    ok(m, B, "accept_bid", {"bid_id": "bid-1"}, "created contract-1 with M-14b at price 20")
    c = m.contracts["contract-1"]
    assert (c.signed, c.deadline, state.due(m, c)) == (day, deadline, 9)


def test_the_contract_of_a_repost_is_contract_n_k():
    m = market(day=20)
    contract(m, "contract-8", deadline=13, ended=None)
    ok(m, B, "end_contract", {"contract_id": "contract-8"},
       "ended contract-8 with M-14b (its deadline day 13 passed with nothing delivered). task-8 is back in your "
       "queue, due day 9 (11 days ago), and can be posted again")
    ok(m, B, "post_task", {"task_id": "task-8", "form": "escrow", "threshold": 6},
       "posted task-8 as post-8-2; prefers escrow, threshold 6")
    bid(m, 1, "post-8-2", M2, 10.0, 6.0)
    m.day = 21
    ok(m, B, "accept_bid", {"bid_id": "bid-1"},
       "created contract-8-2 with M-849 at price 10 (escrow, threshold 6): 10 moved from your balance into escrow "
       "and the fee 1 was charged")
    c = m.contracts["contract-8-2"]
    assert (c.task, c.post, c.deadline, rng.stream_key(c.id)) == ("task-8", "post-8-2", 13, "task-8-2")
    assert records(m, "contract")[0]["attempt"] == 2
    c.deliveries.append(Delivery("R-001", 21, 3, outcome="rejected", decided=21))
    m.day = 23
    ok(m, B, "end_contract", {"contract_id": "contract-8-2"},
       "ended contract-8-2 with M-849 (its rejection of R-001 stands); its price 10 came back to you (the fees are "
       "not refunded). task-8 is back in your queue, due day 9 (14 days ago), and can be posted again")
    assert m.tasks["task-8"].ended == ["contract-8", "contract-8-2"]
    ok(m, B, "post_task", {"task_id": "task-8"}, "posted task-8 as post-8-3")
    bid(m, 2, "post-8-3", M2, 10.0)
    m.day = 24
    ok(m, B, "accept_bid", {"bid_id": "bid-2"}, "created contract-8-3 with M-849 at price 10")
    assert list(m.contracts) == ["contract-8", "contract-8-2", "contract-8-3"]
    assert list(m.posts) == ["post-8-2", "post-8-3"]
    assert m.tasks["task-8"].contract == "contract-8-3" and len(m.tasks) == 1


def test_accept_bid_implicit_target_is_the_bid_as_the_turn_saw_it():
    m = market()
    post(m, "post-1"), bid(m, 1, "post-1", M1, 20.0), post(m, "post-2", B2), bid(m, 2, "post-2", M1, 28.0, 6.0)
    s = copy.deepcopy(m)
    assert actions.implicit(s, B, "accept_bid", {"bid_id": "bid-1"}) == {"price": 20.0, "threshold": None}
    assert actions.implicit(s, B, "accept_bid", {"bid_id": 2}) == {"price": 28.0, "threshold": 6.0}
    assert actions.implicit(s, B, "accept_bid", {"bid_id": "bid-9"}) == {}
    assert actions.implicit(s, B, "accept_bid", {}) == {}
    fixed = actions.implicit(s, B, "accept_bid", {"bid_id": "bid-1"})
    m.bids["bid-1"].price, m.bids["bid-1"].threshold = 20.0, None
    assert actions.run(s, B, "accept_bid", {"bid_id": "bid-1"}, fixed)[0]
    assert not records(m) and not m.contracts and s.contracts and not pending(m, M1) and pending(s, M1)
    m.bids["bid-1"].threshold = 6.0
    refuse(m, B, "accept_bid", {"bid_id": "bid-1"},
           "bid-1 on post-1 was replaced earlier this round: its terms are now 20 credits with threshold 6 (escrow), "
           "not the ones your turn saw: no contract was made", fixed)
    ok(m, B, "accept_bid", {"bid_id": "bid-1"},
       "created contract-1 with M-14b at price 20 (escrow, threshold 6): 20 moved from your balance into escrow and "
       "the fee 2 was charged")


# 6. test_delivery


def fix_reading(monkeypatch, *values):
    """Replace the reading's draw; returns the list of the draws' (side, label, i, q, sd, decimals)."""
    calls, values = [], list(values)

    def reading(seed, stream, side, label, i, q, sd, decimals):
        calls.append((stream, side, label, i, q, sd, decimals))
        return {"z": 0.0, "reading": values.pop(0)}

    monkeypatch.setattr(rng, "reading", reading)
    return calls


def test_test_delivery_checks_in_order():
    m = market()
    contract(m, "contract-1", "escrow"), contract(m, "contract-2", "escrow"), contract(m, "contract-3")
    contract(m, "contract-4", buyer=B2), contract(m, "contract-5"), contract(m, "contract-6", ended=2)
    set_balance(m, B, 0.4)
    mine = ("Your contracts: contract-1 (task-1), contract-2 (task-2), contract-3 (task-3), contract-5 (task-5), "
            "contract-6 (task-6, ended).")
    refuse(m, B, "test_delivery", {"result": "R-baf"}, "missing argument 'contract_id'")
    refuse(m, B, "test_delivery", {"contract_id": "contract-9"}, f"no contract with id 'contract-9'. {mine}")
    refuse(m, B, "test_delivery", {"contract_id": "contract-4"}, f"contract-4 is not one of your contracts. {mine}")
    nothing = ("nothing has been delivered for {} yet: test_delivery reads a result already delivered to you, and "
               "your view lists each delivery")
    refuse(m, B, "test_delivery", {"contract_id": "contract-1", "result": "R-baf"}, nothing.format("contract-1"))
    refuse(m, B, "test_delivery", {"contract_id": 3}, nothing.format("contract-3"))
    refuse(m, B, "test_delivery", {"contract_id": 6, "result": "R-baf"}, nothing.format("contract-6"))
    c1, c2, c3 = m.contracts["contract-1"], m.contracts["contract-2"], m.contracts["contract-3"]
    deliver(c1, "R-0aa", 2, "rejected", decided=2), deliver(c1, "R-baf", 3, "review")
    deliver(c2, "R-211", 2, "rejected", decided=2)
    c1.results.append(Result("R-999", "employee", "fresh", None, 1, 2, 5.0))
    refuse(m, B, "test_delivery", {"contract_id": "contract-1", "result": "R-123"},
           "R-123 is not yours to test on contract-1: R-baf")
    refuse(m, B, "test_delivery", {"contract_id": "contract-1", "result": "R-999"},
           "R-999 is not yours to test on contract-1: R-baf")
    refuse(m, B, "test_delivery", {"contract_id": "contract-1", "result": 123},
           "123 is not yours to test on contract-1: R-baf")
    refuse(m, B, "test_delivery", {"contract_id": "contract-1", "result": "r-baf"},
           "r-baf is not yours to test on contract-1: R-baf")
    refuse(m, B, "test_delivery", {"contract_id": "contract-1", "result": "R-0aa"},
           "R-0aa was rejected: it went back to the manager and is not yours to test")
    refuse(m, B, "test_delivery", {"contract_id": "contract-2"},
           "nothing on contract-2 is yours to test: you can test a submission under your review, or the result "
           "released to you")
    refuse(m, B, "test_delivery", {"contract_id": "contract-2", "result": ""},
           "nothing on contract-2 is yours to test: you can test a submission under your review, or the result "
           "released to you")
    refuse(m, B, "test_delivery", {"contract_id": "contract-2", "result": "R-212"},
           "R-212 is not yours to test on contract-2: nothing is")
    refuse(m, B, "test_delivery", {"contract_id": "contract-2", "result": "R-211"},
           "R-211 was rejected: it went back to the manager and is not yours to test")
    c2.deliveries[0].dispute = Dispute(2, 6.1, "manager")
    released(c2, "ruling", 3)
    refuse(m, B, "test_delivery", {"contract_id": "contract-2"},
           "nothing on contract-2 is yours to test: you can test a submission under your review, or the result "
           "released to you once you pay the arbiter's fee (pay_escrow_arbiter)")
    refuse(m, B, "test_delivery", {"contract_id": "contract-2", "result": "R-211"},
           "R-211 is not yours to test yet: it becomes yours once you pay the arbiter's fee (pay_escrow_arbiter)")
    refuse(m, B, "test_delivery", {"contract_id": "contract-2", "result": "R-212"},
           "R-212 is not yours to test on contract-2: nothing is")
    deliver(c3, "R-5b6", 2), deliver(c3, "R-95c", 3)
    c3.results.append(Result("R-777", "employee", "fresh", None, 1, 2, 5.0))
    refuse(m, B, "test_delivery", {"contract_id": "contract-3", "result": "R-777"},
           "R-777 was not delivered to you. Delivered for contract-3: R-5b6, R-95c.")
    refuse(m, B, "test_delivery", {"contract_id": "contract-3", "result": " R-5b6 "},
           " R-5b6  was not delivered to you. Delivered for contract-3: R-5b6, R-95c.")
    short = "insufficient balance for test_delivery: it costs 1, you have 0.40"
    refuse(m, B, "test_delivery", {"contract_id": "contract-3"}, short)
    refuse(m, B, "test_delivery", {"contract_id": "contract-3", "result": "R-5b6"}, short)
    refuse(m, B, "test_delivery", {"contract_id": "contract-1"}, short)
    assert not actions.implicit(m, B, "test_delivery", {"contract_id": "contract-2"})


def test_test_delivery_examples(monkeypatch):
    m = market()
    c = contract(m, "contract-3", manager=M2)
    deliver(c, "R-5b6", 2, q=4.5), deliver(c, "R-95c", 3)
    c.results[0].buyer_readings, c.results[0].manager_readings = [5.7], [4.4]
    c.results[1].buyer_readings = [5.7, 5.9, 7.4, 4.3, 7.5]
    calls = fix_reading(monkeypatch, 4.2, 6.4)
    ok(m, B, "test_delivery", {"contract_id": "contract-3", "result": "R-5b6"},
       f"R-5b6 reading: 4.2 (charged 1 credit). Your readings [5.7, 4.2] mean 4.95 (n=2, {PM}1.41 standard error)")
    ok(m, B, "test_delivery", {"contract_id": 3},
       f"R-95c reading: 6.4 (charged 1 credit). Your readings [..., 5.9, 7.4, 4.3, 7.5, 6.4] mean 6.20 (n=6, "
       f"{PM}0.82 standard error)")
    assert calls == [("task-3", "buyer", "R-5b6", 1, 4.5, 2.0, 1), ("task-3", "buyer", "R-95c", 5, 6.0, 2.0, 1)]
    assert c.results[0].buyer_readings == [5.7, 4.2] and c.results[0].manager_readings == [4.4]
    assert balance(m, B) == 58.0 and ledger(m, B) == [("tests", 1), ("tests", 1)]
    assert [r["type"] for r in records(m)] == ["money", "reading", "money", "reading"]
    assert all(not p.pending for p in m.participants.values()) and balance(m, M2) == 60.0


def test_test_delivery_mean_is_a_compensated_sum(monkeypatch):
    m = market()
    c = contract(m)
    deliver(c, "R-001", 2)
    c.results[0].buyer_readings = [6.2, 2.7, 6.8]
    fix_reading(monkeypatch, 4.6, 0.0, 10.0)
    ok(m, B, "test_delivery", {"contract_id": 1},
       f"R-001 reading: 4.6 (charged 1 credit). Your readings [6.2, 2.7, 6.8, 4.6] mean 5.08 (n=4, {PM}1.00 "
       f"standard error)")
    ok(m, B, "test_delivery", {"contract_id": 1},
       f"R-001 reading: 0.0 (charged 1 credit). Your readings [6.2, 2.7, 6.8, 4.6, 0.0] mean 4.06 (n=5, {PM}0.89 "
       f"standard error)")
    ok(m, B, "test_delivery", {"contract_id": 1},
       f"R-001 reading: 10.0 (charged 1 credit). Your readings [..., 2.7, 6.8, 4.6, 0.0, 10.0] mean 5.05 (n=6, "
       f"{PM}0.82 standard error)")


@pytest.mark.parametrize("cost, charged, entries, left", [
    (0.5, "0.5 credits", [("tests", 0.5)], 0.0), (0, "0 credits", [], 0.5), (0.0000004, "4e-07 credits", [], 0.5),
])
def test_test_delivery_cost(monkeypatch, cost, charged, entries, left):
    m = market(overrides={"tests": {"buyer_cost": cost, "manager_cost": cost}})
    c = contract(m)
    deliver(c, "R-001", 2)
    set_balance(m, B, 0.5)
    fix_reading(monkeypatch, 5.2)
    ok(m, B, "test_delivery", {"contract_id": 1},
       f"R-001 reading: 5.2 (charged {charged}). Your readings [5.2] mean 5.20 (n=1, {PM}2.00 standard error)")
    assert ledger(m, B) == entries and balance(m, B) == left


def test_test_delivery_reads_with_the_real_draw():
    m = market()
    c = contract(m)
    deliver(c, "R-001", 2, q=6.0)
    drawn = rng.reading(1, "task-1", "buyer", "R-001", 0, 6.0, 2.0, 1)["reading"]
    okay, text = actions.run(m, B, "test_delivery", {"contract_id": 1})
    assert okay and text.startswith(f"R-001 reading: {drawn:.1f} (charged 1 credit). Your readings [{drawn:.1f}] ")
    assert c.results[0].buyer_readings == [drawn]
    assert records(m, "reading")[0]["i"] == 0


def test_test_delivery_default_on_a_plain_contract(monkeypatch):
    m = market()
    c = contract(m)
    deliver(c, "R-001", 2), deliver(c, "R-002", 2), deliver(c, "R-003", 3)
    fix_reading(monkeypatch, 5.0, 5.0, 5.0, 5.0)
    assert actions.implicit(m, B, "test_delivery", {"contract_id": 1}) == {"result": "R-003"}
    assert actions.implicit(m, B, "test_delivery", {"contract_id": 1, "result": None}) == {"result": "R-003"}
    assert actions.implicit(m, B, "test_delivery", {"contract_id": 1, "result": "R-001"}) == {}
    assert actions.implicit(m, B2, "test_delivery", {"contract_id": 1}) == {}
    assert actions.run(m, B, "test_delivery", {"contract_id": 1})[1].startswith("R-003 reading")
    c.kept = "R-001"
    assert actions.implicit(m, B, "test_delivery", {"contract_id": 1}) == {"result": "R-001"}
    assert actions.run(m, B, "test_delivery", {"contract_id": 1})[1].startswith("R-001 reading")
    assert actions.run(m, B, "test_delivery", {"contract_id": 1}, {"result": "R-002"})[1].startswith("R-002 reading")
    assert actions.run(m, B, "test_delivery", {"contract_id": 1, "result": "R-003"},
                       {"result": "R-002"})[1].startswith("R-003 reading")
    refuse(m, B, "test_delivery", {"contract_id": 1}, "R-00x was not delivered to you. Delivered for contract-1: "
                                                      "R-001, R-002, R-003.", {"result": "R-00x"})


def test_test_delivery_on_an_escrow_reads_what_is_the_buyers(monkeypatch):
    m = market()
    c = contract(m, "contract-1", "escrow")
    deliver(c, "R-0aa", 2, "rejected", decided=2), deliver(c, "R-baf", 3, "review")
    fix_reading(monkeypatch, 5.0, 5.0, 5.0, 5.0, 5.0)
    assert actions.implicit(m, B, "test_delivery", {"contract_id": 1}) == {"result": "R-baf"}
    assert actions.run(m, B, "test_delivery", {"contract_id": 1})[1].startswith("R-baf reading")
    assert actions.run(m, B, "test_delivery", {"contract_id": 1, "result": "R-baf"})[1].startswith("R-baf reading")
    refuse(m, B, "test_delivery", {"contract_id": 1},
           "R-0aa was rejected: it went back to the manager and is not yours to test", {"result": "R-0aa"})
    c.deliveries[1].outcome, c.deliveries[1].decided = "auto", 5
    released(c, "automatic", 5, received=5)
    m.day = 9
    assert actions.implicit(m, B, "test_delivery", {"contract_id": 1}) == {"result": "R-baf"}
    assert actions.run(m, B, "test_delivery", {"contract_id": 1})[1].startswith("R-baf reading")
    c2 = contract(m, "contract-2", "escrow")
    deliver(c2, "R-211", 2, "rejected", decided=2, dispute=Dispute(2, 6.1, "manager"))
    released(c2, "ruling", 3)
    assert actions.implicit(m, B, "test_delivery", {"contract_id": 2}) == {}
    ok(m, B, "pay_escrow_arbiter", {"contract_id": 2}, "paid the arbiter's fee of 10 on contract-2: R-211 is now yours")
    assert actions.implicit(m, B, "test_delivery", {"contract_id": 2}) == {"result": "R-211"}
    assert actions.run(m, B, "test_delivery", {"contract_id": 2})[1].startswith("R-211 reading")
    assert actions.run(m, B, "test_delivery", {"contract_id": 2, "result": "R-211"})[1].startswith("R-211 reading")
    assert c2.results[0].buyer_readings == [5.0, 5.0]


# 7. keep_delivery


def test_keep_delivery_checks_in_order():
    m = market()
    contract(m, "contract-1", "escrow"), contract(m, "contract-2"), contract(m, "contract-4", manager=M3)
    contract(m, "contract-5", buyer=B2)
    mine = "Your contracts: contract-1 (task-1), contract-2 (task-2), contract-4 (task-4)."
    refuse(m, B, "keep_delivery", {"result": "R-ee0"}, "missing argument 'contract_id'")
    refuse(m, B, "keep_delivery", {"contract_id": "5-1"}, f"no contract with id '5-1'. {mine}")
    refuse(m, B, "keep_delivery", {"contract_id": 5}, f"contract-5 is not one of your contracts. {mine}")
    refuse(m, B, "keep_delivery", {"contract_id": 1},
           "contract-1 is an escrow contract: an escrow contract is settled on its released result, so keep_delivery "
           "applies only to plain contracts")
    refuse(m, B, "keep_delivery", {"contract_id": 2}, "missing argument 'result'")
    refuse(m, B, "keep_delivery", {"contract_id": 2, "result": "", "label": "R-ee0"}, "missing argument 'result'")
    refuse(m, B, "keep_delivery", {"contract_id": 2, "result": "R-ee0"},
           "nothing has been delivered for contract-2 yet: keep_delivery keeps a result delivered to you")
    c = m.contracts["contract-4"]
    deliver(c, "R-ee0", 3), deliver(c, "R-1a2", 3)
    c.results.append(Result("R-777", "employee", "fresh", None, 1, 2, 5.0))
    refuse(m, B, "keep_delivery", {"contract_id": 4, "result": "R-777"},
           "R-777 was not delivered to you on contract-4. Delivered for contract-4: R-ee0, R-1a2.")
    refuse(m, B, "keep_delivery", {"contract_id": 4, "result": " R-ee0 "},
           " R-ee0  was not delivered to you on contract-4. Delivered for contract-4: R-ee0, R-1a2.")
    refuse(m, B, "keep_delivery", {"contract_id": 4, "result": 5},
           "5 was not delivered to you on contract-4. Delivered for contract-4: R-ee0, R-1a2.")
    c.kept = "R-1a2"
    refuse(m, B, "keep_delivery", {"contract_id": 4, "result": "R-1a2"},
           "you already kept R-1a2 on contract-4: it is the delivery in use")


def test_keep_delivery_effects():
    m = market(day=6)
    c = contract(m, "contract-4", manager=M3, paid=20.0)
    deliver(c, "R-ee0", 3), deliver(c, "R-1a2", 5)
    tail = ": it is the delivery in use, and later deliveries do not replace it unless you keep another"
    ok(m, B, "keep_delivery", {"contract_id": "contract-4", "result": "R-ee0"},
       "kept R-ee0 on contract-4 (delivered day 3)" + tail)
    assert c.kept == "R-ee0" and state.in_use(c).label == "R-ee0"
    ok(m, B, "keep_delivery", {"contract_id": "contract-4", "result": "R-1a2"},
       "kept R-1a2 on contract-4 (delivered day 5)" + tail)
    assert c.kept == "R-1a2"
    assert pending(m, M3) == ["[day 6, round 2] B-c53 kept R-ee0 on contract-4 (delivered day 3)",
                              "[day 6, round 2] B-c53 kept R-1a2 on contract-4 (delivered day 5)"]
    assert [r["type"] for r in records(m)] == ["keep", "notification"] * 2
    assert not m.participants[B].ledger and not pending(m, B)


# 8 and 9. approve_submission, reject_submission


@pytest.mark.parametrize("action", ["approve_submission", "reject_submission"])
def test_review_checks_in_order(action):
    m = market(day=7)
    contract(m, "contract-1"), contract(m, "contract-2", "escrow", ended=5), contract(m, "contract-3", "escrow")
    c4, c5 = contract(m, "contract-4", "escrow"), contract(m, "contract-5", "escrow")
    c6, c7 = contract(m, "contract-6", "escrow", closed="late"), contract(m, "contract-7", "escrow")
    contract(m, "contract-8", "escrow", buyer=B2)
    mine = ("Your contracts: contract-1 (task-1), contract-2 (task-2, ended), contract-3 (task-3), contract-4 "
            "(task-4), contract-5 (task-5), contract-6 (task-6), contract-7 (task-7).")
    deliver(c4, "R-001", 2, "rejected", decided=2), deliver(c4, "R-713", 3, "rejected", decided=4)
    deliver(c5, "R-002", 2, "rejected", decided=2), deliver(c5, "R-5aa", 3, "approved", decided=4)
    released(c5, "approved", 4, received=4)
    deliver(c6, "R-003", 2, "rejected", decided=2)
    deliver(c7, "R-004", 2, "rejected", decided=2), deliver(c7, "R-7c1", 6, "review")
    refuse(m, B, action, {"contract": "contract-7"}, "missing argument 'contract_id'")
    refuse(m, B, action, {"contract_id": "contract-9"}, f"no contract with id 'contract-9'. {mine}")
    refuse(m, B, action, {"contract_id": "contract-8"}, f"contract-8 is not one of your contracts. {mine}")
    refuse(m, B, action, {"contract_id": 1}, f"contract-1 is a plain contract: {action} applies to escrow contracts; "
                                             f"a plain contract's delivery is not reviewed")
    refuse(m, B, action, {"contract_id": 2}, "you ended contract-2 on day 5")
    why = "nothing is under your review on {}: {}"
    refuse(m, B, action, {"contract_id": 5}, why.format("contract-5", "its escrow was released on R-5aa"))
    refuse(m, B, action, {"contract_id": 6}, why.format("contract-6", "its escrow closed unreleased"))
    refuse(m, B, action, {"contract_id": "contract-4"},
           "nothing is under your review on contract-4: its latest submission, R-713, was rejected")
    refuse(m, B, action, {"contract_id": 3}, why.format("contract-3", "nothing has been submitted"))
    refuse(m, B, action, {"contract_id": 3}, why.format("contract-3", "nothing has been submitted"),
           {"submission": "R-7c1"})
    refuse(m, B, action, {"contract_id": 7},
           "R-7c1 is under your review on contract-7 now, not the submission your turn saw: nothing was decided",
           {"submission": "R-004"})
    assert actions.implicit(m, B, action, {"contract_id": 7}) == {"submission": "R-7c1"}
    assert actions.implicit(m, B, action, {"contract_id": "contract-4"}) == {}
    assert actions.implicit(m, B, action, {"contract_id": 1}) == {}
    assert actions.implicit(m, B2, action, {"contract_id": 7}) == {}
    assert actions.implicit(m, B, action, {}) == {}
    assert actions.run(m, B, action, {"contract_id": 7}, {"submission": "R-7c1"})[0]


def test_approve_submission_on_time():
    m = market(day=7, buyers=("B-d75",), managers=("M-14b",))
    c = contract(m, "contract-13", "escrow", buyer="B-d75", price=28.0, due=9)
    d = deliver(c, "R-95c", 6, "review")
    m.participants["B-d75"].balance, m.participants[M1].balance = 10.0, -3.0
    m.participants[M1].out_of_business = True
    ok(m, "B-d75", "approve_submission", {"contract_id": "contract-13"},
       "approved R-95c on contract-13: 28.00 of the price 28 went to M-14b; R-95c is yours and contract-13 is closed")
    assert pending(m, M1) == ["[day 7, round 2] B-d75 approved R-95c on contract-13: you received 28.00 of the price "
                              "28; contract-13 is closed."]
    assert (d.outcome, d.decided) == ("approved", 7)
    assert (c.release_day, c.release_how, c.paid, c.received) == (7, "approved", 28.0, 7)
    assert not state.held(c) and state.settled(c) and state.refunded(c) == 0.0
    assert ledger(m, M1) == [("escrow releases", 28.0)] and balance(m, M1) == -3.0
    assert m.participants[M1].out_of_business
    assert m.deferred == [Credit(M1, 28.0, "escrow_release", "B-d75")]
    assert ledger(m, "B-d75") == [] and balance(m, "B-d75") == 10.0 and not pending(m, "B-d75")
    assert [r["type"] for r in records(m)] == ["decision", "release", "money", "notification"]
    assert records(m, "money")[0]["deferred"] is True
    state.land_credits(m)
    assert balance(m, M1) == 25.0 and not m.participants[M1].out_of_business and m.deferred == []
    refuse(m, "B-d75", "approve_submission", {"contract_id": "contract-13"},
           "nothing is under your review on contract-13: its escrow was released on R-95c")
    refuse(m, "B-d75", "end_contract", {"contract_id": "contract-13"},
           "the escrow of contract-13 was released on R-95c: the contract is settled and cannot be ended")


def test_approve_submission_late_pays_a_cut_and_refunds_at_once():
    m = market(day=11, buyers=(B,), managers=("M-ee6",))
    c = contract(m, "contract-7", "escrow", manager="M-ee6", price=10.0, due=9)
    deliver(c, "R-08c", 10, "review")
    set_balance(m, B, 1.0)
    ok(m, B, "approve_submission", {"contract_id": 7},
       "approved R-08c on contract-7: 8.00 of the price 10 went to M-ee6, and 2.00 came back to you; R-08c is yours "
       "and contract-7 is closed")
    assert pending(m, "M-ee6") == ["[day 11, round 2] B-c53 approved R-08c on contract-7: you received 8.00 of the "
                                   "price 10 (submitted 1 day late); contract-7 is closed."]
    assert ledger(m, B) == [("escrow refunds", 2.0)] and balance(m, B) == 3.0
    assert ledger(m, "M-ee6") == [("escrow releases", 8.0)] and balance(m, "M-ee6") == 60.0
    assert m.deferred == [Credit("M-ee6", 8.0, "escrow_release", B)]
    assert (c.paid, c.received, state.refunded(c)) == (8.0, 11, 2.0)
    assert processes.value(m, c, 6.0, c.received) == processes.value(m, c, 6.0, 11)


def test_approve_submission_two_days_late():
    m = market(day=12)
    c = contract(m, "contract-7", "escrow", price=26.67, due=9)
    deliver(c, "R-08c", 11, "review")
    ok(m, B, "approve_submission", {"contract_id": 7},
       "approved R-08c on contract-7: 16.00 of the price 26.67 went to M-14b, and 10.67 came back to you; R-08c is "
       "yours and contract-7 is closed")
    assert pending(m, M1) == ["[day 12, round 2] B-c53 approved R-08c on contract-7: you received 16.00 of the price "
                              "26.67 (submitted 2 days late); contract-7 is closed."]
    assert c.paid == round(26.67 * 0.6, 4) and balance(m, B) == 60.0 + (26.67 - c.paid)


def test_reject_submission_effects_and_example():
    m = market(day=7, buyers=(B,), managers=("M-3c5",))
    c = contract(m, "contract-8", "escrow", manager="M-3c5", price=22.0)
    deliver(c, "R-001", 2, "rejected", decided=2)
    d = deliver(c, "R-7c1", 6, "review")
    before = copy.deepcopy(m.participants)
    ok(m, B, "reject_submission", {"contract_id": "contract-8", "reason": "bad", "text": "bad"},
       "rejected R-7c1 on contract-8: it went back to M-3c5 and is not yours; the price stays held. M-3c5 can "
       "dispute the rejection through day 8")
    assert pending(m, "M-3c5") == [
        "[day 7, round 2] B-c53 rejected R-7c1 on contract-8 (submission #2): it is returned to you and cannot be "
        "submitted again. You can dispute the rejection through day 8 (a deposit of 11), or submit another result."]
    assert (d.outcome, d.decided, d.rejection, d.dispute) == ("rejected", 7, "", None)
    assert state.held(c) and state.disputable(m, c) and c.received is None and c.release_day is None
    assert [(p.balance, p.ledger) for p in m.participants.values()] == [(p.balance, p.ledger) for p in before.values()]
    assert m.deferred == [] and not pending(m, B)
    assert [r["type"] for r in records(m)] == ["decision", "notification"]
    refuse(m, B, "reject_submission", {"contract_id": 8},
           "nothing is under your review on contract-8: its latest submission, R-7c1, was rejected")
    refuse(m, B, "end_contract", {"contract_id": 8},
           f"M-3c5 can dispute your rejection of R-7c1 through day 8: if it does not, you can end contract-8 from "
           f"day 9 ({RULE_E})")


@pytest.mark.parametrize("message, stored, said", [
    (None, "", ""), ("", "", ""), (False, "", ""), (0, "", ""), (0.0, "", ""), (-0.0, "", ""), ([], "", ""),
    ({}, "", ""),
    ("too rough", "too rough", ' Message: "too rough".'),
    ("  ", "  ", ' Message: "  ".'),
    (" a \"b\"\r\nc\n", " a \"b\"\r\nc\n", ' Message: " a "b" c".'),
    ("a\n\nb", "a\n\nb", ' Message: "a  b".'),
    (7, "7", ' Message: "7".'), (True, "True", ' Message: "True".'), (2.50, "2.5", ' Message: "2.5".'),
    ("0", "0", ' Message: "0".'), ([0], "[0]", ' Message: "[0]".'), ({"a": 1}, "{'a': 1}", ' Message: "{\'a\': 1}".'),
    ("x" * 501, "x" * 500, f' Message: "{"x" * 500}".'),
])
def test_reject_submission_message(message, stored, said):
    m = market(day=7)
    c = contract(m, "contract-1", "escrow", price=21.1)
    d = deliver(c, "R-7c1", 6, "review")
    ok(m, B, "reject_submission", {"contract_id": 1, "message": message},
       "rejected R-7c1 on contract-1: it went back to M-14b and is not yours; the price stays held. M-14b can "
       "dispute the rejection through day 8")
    assert d.rejection == stored
    assert pending(m, M1) == [
        f"[day 7, round 2] B-c53 rejected R-7c1 on contract-1 (submission #1): it is returned to you and cannot be "
        f"submitted again.{said} You can dispute the rejection through day 8 (a deposit of 10.55), or submit "
        f"another result."]
    assert records(m, "decision")[0]["message"] == stored


def test_reject_submission_cuts_by_characters_and_uses_the_window():
    m = market(day=7, overrides={"contracts": {"message_chars": 3, "escrow": {"dispute_window_days": 3}}})
    c = contract(m, "contract-1", "escrow", price=0.01)
    d = deliver(c, "R-7c1", 6, "review")
    assert state.dispute_fee(m, c) == 0.01
    ok(m, B, "reject_submission", {"contract_id": 1, "message": "\u00e9\U0001f600\n\nabc"},
       "rejected R-7c1 on contract-1: it went back to M-14b and is not yours; the price stays held. M-14b can "
       "dispute the rejection through day 10")
    assert d.rejection == "\u00e9\U0001f600\n"
    assert pending(m, M1) == [
        "[day 7, round 2] B-c53 rejected R-7c1 on contract-1 (submission #1): it is returned to you and cannot be "
        "submitted again. Message: \"\u00e9\U0001f600\". You can dispute the rejection through day 10 (a deposit of "
        "0.01), or submit another result."]


# 10. pay_escrow_arbiter


def ruled(m, cid, price, label, winner="manager", **fields):
    """An escrow whose latest submission was rejected, disputed on day 2 and ruled on day 3."""
    c = contract(m, cid, "escrow", price=price, **fields)
    deliver(c, label, 2, "rejected", decided=2, dispute=Dispute(2, 6.1, winner))
    if winner == "manager":
        released(c, "ruling", 3, received=3 if state.dispute_fee(m, c) == 0 else None)
    return c


def test_pay_escrow_arbiter_checks_in_order():
    m = market()
    contract(m, "contract-2"), contract(m, "contract-3", "escrow"), contract(m, "contract-9", buyer=B2)
    c1 = ruled(m, "contract-1", 21.1, "R-001")
    ruled(m, "contract-4", 20.0, "R-004", winner="buyer")
    c6, c7 = contract(m, "contract-6", "escrow"), ruled(m, "contract-7", 20.0, "R-007")
    deliver(c6, "R-006", 2, "approved", decided=3), released(c6, "approved", 3, received=3)
    c7.received = 3
    set_balance(m, B, 3.0)
    mine = ("Your contracts: contract-2 (task-2), contract-3 (task-3), contract-1 (task-1), contract-4 (task-4), "
            "contract-6 (task-6), contract-7 (task-7).")
    refuse(m, B, "pay_escrow_arbiter", {}, "missing argument 'contract_id'")
    refuse(m, B, "pay_escrow_arbiter", {"contract_id": "post-1"}, f"no contract with id 'post-1'. {mine}")
    refuse(m, B, "pay_escrow_arbiter", {"contract_id": 9}, f"contract-9 is not one of your contracts. {mine}")
    for n in (2, 3, 4, 6, 7):
        refuse(m, B, "pay_escrow_arbiter", {"contract_id": n},
               f"you owe the arbiter nothing on contract-{n}: pay_escrow_arbiter applies after a dispute that went "
               f"against you, until you pay")
    refuse(m, B, "pay_escrow_arbiter", {"contract_id": "contract-1"},
           "insufficient balance for paying the arbiter's fee on contract-1: it costs 10.55, you have 3.00")
    set_balance(m, B, 10.55)
    ok(m, B, "pay_escrow_arbiter", {"contract_id": "contract-1"},
       "paid the arbiter's fee of 10.55 on contract-1: R-001 is now yours")
    assert balance(m, B) == 0.0 and c1.received == 3


def test_a_fee_that_rounds_to_0_is_never_owed():
    m = market(overrides={"contracts": {"escrow": {"dispute_fee_rate": 0.1}}})
    c = ruled(m, "contract-5", 0.04, "R-005")
    assert state.dispute_fee(m, c) == 0 and c.received == 3
    refuse(m, B, "pay_escrow_arbiter", {"contract_id": 5},
           "you owe the arbiter nothing on contract-5: pay_escrow_arbiter applies after a dispute that went against "
           "you, until you pay")
    assert actions.implicit(m, B, "test_delivery", {"contract_id": 5}) == {"result": "R-005"}


def test_pay_escrow_arbiter_effects_and_examples():
    m = market(day=16)
    c = ruled(m, "contract-14", 22.0, "R-b8b")
    c2 = ruled(m, "contract-18-2", 10.0, "R-8b6", manager=M2)
    assert state.owes_fee(m, c) and state.owes_fee(m, c2)
    ok(m, B, "pay_escrow_arbiter", {"contract_id": "contract-14"},
       "paid the arbiter's fee of 11 on contract-14: R-b8b is now yours")
    ok(m, B, "pay_escrow_arbiter", {"contract_id": "18-2"},
       "paid the arbiter's fee of 5 on contract-18-2: R-8b6 is now yours")
    assert (c.received, c2.received) == (16, 16) and not state.owes_fee(m, c)
    assert ledger(m, B) == [("arbiter's fees paid", 11.0), ("arbiter's fees paid", 5.0)] and balance(m, B) == 44.0
    assert all(not p.pending for p in m.participants.values())
    assert balance(m, M1) == 60.0 and not ledger(m, M1) and m.deferred == []
    assert [r["type"] for r in records(m)] == ["money", "fee_paid"] * 2
    refuse(m, B, "pay_escrow_arbiter", {"contract_id": "contract-14"},
           "you owe the arbiter nothing on contract-14: pay_escrow_arbiter applies after a dispute that went "
           "against you, until you pay")


# 11. end_contract: every row of processes.md, 7


def end_refused(m, cid, text):
    refuse(m, B, "end_contract", {"contract_id": cid}, text)


def test_end_contract_finds_the_contract_first():
    m = market()
    contract(m, "contract-1", ended=2), contract(m, "contract-2", buyer=B2)
    refuse(m, B, "end_contract", {}, "missing argument 'contract_id'")
    refuse(m, B, "end_contract", {"contract_id": " contract-1"},
           "no contract with id ' contract-1'. Your contracts: contract-1 (task-1, ended).")
    refuse(m, B, "end_contract", {"contract_id": 2},
           "contract-2 is not one of your contracts. Your contracts: contract-1 (task-1, ended).")
    refuse(m, M1, "end_contract", {"contract_id": 1},
           "'end_contract' is not available to a manager: it is a buyer's action")


@pytest.mark.parametrize("form", ["plain", "escrow"])
def test_end_row_already_ended(form):
    m = market(day=20)
    contract(m, "contract-1", form, ended=12)
    end_refused(m, "contract-1", "you already ended contract-1 on day 12")


def test_end_row_escrow_released():
    m = market(day=20)
    c = contract(m, "contract-1", "escrow")
    deliver(c, "R-001", 4, "rejected", decided=4), deliver(c, "R-002", 6, "auto", decided=8)
    released(c, "automatic", 8, received=8)
    end_refused(m, 1, "the escrow of contract-1 was released on R-002: the contract is settled and cannot be ended")
    c2 = ruled(m, "contract-2", 20.0, "R-003")
    assert c2.received is None
    end_refused(m, 2, "the escrow of contract-2 was released on R-003: the contract is settled and cannot be ended")


def test_end_row_escrow_closed_unreleased():
    m = market(day=20)
    c = contract(m, "contract-1", "escrow", closed="late")
    deliver(c, "R-001", 4, "rejected", decided=4)
    end_refused(m, 1, "the escrow of contract-1 already closed unreleased and refunded you: it cannot be ended")
    contract(m, "contract-2", "escrow", closed="late")
    end_refused(m, 2, "the escrow of contract-2 already closed unreleased and refunded you: it cannot be ended")


def test_end_row_escrow_under_review():
    m = market(day=20)
    c = contract(m, "contract-1", "escrow")
    deliver(c, "R-001", 4, "rejected", decided=4), deliver(c, "R-002", 19, "review")
    end_refused(m, 1, f"R-002 is under your review on contract-1: approve it or reject it first "
                      f"(approve_submission, reject_submission) ({RULE_E})")


def test_end_row_escrow_in_dispute():
    m = market(day=5)
    c = contract(m, "contract-1", "escrow")
    deliver(c, "R-001", 4, "rejected", decided=4, dispute=Dispute(5))
    end_refused(m, 1, f"your rejection of R-001 on contract-1 is in dispute, and the arbiter rules at the start of "
                      f"day 6 ({RULE_E})")
    m.day, c.deliveries[0].decided, c.deliveries[0].dispute = 12, 10, Dispute(11)
    end_refused(m, 1, f"your rejection of R-001 on contract-1 is in dispute, and the arbiter rules at the start of "
                      f"day 12 ({RULE_E})")


def test_end_row_escrow_rejected_and_disputable():
    m = market(day=16, buyers=(B,), managers=("M-569",))
    c = contract(m, "contract-18", "escrow", manager="M-569", due=14)
    deliver(c, "R-eb6", 15, "rejected", decided=15)
    text = ("M-569 can dispute your rejection of R-eb6 through day 16: if it does not, you can end contract-18 from "
            "day 17 (an escrow contract can be ended after a rejection that stands (its manager can no longer "
            "dispute it, or the arbiter ruled for you), or after its deadline day with nothing released if it was "
            "signed on or before that day; never while a submission is under your review or a rejection is in "
            "dispute)")
    end_refused(m, "contract-18", text)
    m.day = 15
    end_refused(m, "contract-18", text)
    m.day = 17
    assert actions.run(m, B, "end_contract", {"contract_id": "contract-18"})[0]


@pytest.mark.parametrize("day, due, signed, decided, dispute", [
    (6, 9, 1, 4, None),  # the window passed undisputed, before the deadline
    (5, 9, 1, 4, Dispute(4, 3.0, "buyer")),  # the arbiter ruled for the buyer, within the window
    (13, 9, 9, 11, None),  # the deadline is the full-pay cap
    (30, 9, 1, 4, None),  # long after the deadline
])
def test_end_row_escrow_rejection_stands(day, due, signed, decided, dispute):
    m = market(day=day)
    c = contract(m, "contract-1", "escrow", price=22.0, due=due, signed=signed)
    deliver(c, "R-001", 3, "rejected", decided=3)
    deliver(c, "R-eb6", decided, "rejected", decided=decided, dispute=dispute)
    set_balance(m, B, 5.0)
    assert processes.end_rule(m, c) == "stands"
    ok(m, B, "end_contract", {"contract_id": "contract-1"},
       f"ended contract-1 with M-14b (its rejection of R-eb6 stands); its price 22 came back to you (the fees are "
       f"not refunded). task-1 is back in your queue, due day {due} ({processes.rel(m, due)}), and can be posted "
       f"again")
    assert pending(m, M1) == [
        f"[day {day}, round 2] B-c53 ended contract-1 (its rejection of R-eb6 stands). Its price 22 went back to "
        f"B-c53; the fees paid on it are not refunded. contract-1 takes no more runs, tests or deliveries; runs "
        f"still going on it finish and stay charged, but their results cannot be delivered."]
    assert balance(m, B) == 27.0 and ledger(m, B) == [("escrow refunds", 22.0)] and m.deferred == []
    assert records(m, "end")[0]["rule"] == "rejected" and records(m, "end")[0]["refund"] == 22.0


def test_end_row_escrow_deadline_at_the_full_pay_cap():
    m = market(day=10)
    c = contract(m, "contract-1", "escrow", due=9, signed=9)
    text = ("contract-1's deadline day 13 is the last day before its escrow closes (day 14): it can be ended only "
            "after a rejection that stands")
    assert c.deadline == 13
    end_refused(m, 1, text)
    m.day = 14
    end_refused(m, 1, text)
    c2 = contract(m, "contract-2", "escrow", due=9, signed=20)
    end_refused(m, 2, text.replace("contract-1", "contract-2"))
    c2.deadline = 14
    end_refused(m, 2, "contract-2's deadline day 14 is the last day before its escrow closes (day 14): it can be "
                      "ended only after a rejection that stands")


def test_end_row_escrow_not_yet_overdue():
    m = market(day=9)
    contract(m, "contract-1", "escrow", due=9), contract(m, "contract-2", "escrow", due=9, signed=8)
    end_refused(m, 1, f"the deadline day of contract-1 is day 9: {RULE_E} (from day 10)")
    m.day = 12
    end_refused(m, 2, f"the deadline day of contract-2 is day 12: {RULE_E} (from day 13)")


def test_end_row_escrow_overdue_example():
    m = market(day=17)
    c = contract(m, "contract-9", "escrow", price=22.0, due=15, signed=12)
    c.results.append(Result("R-0f0", "employee", "fresh", None, 16, 19, 6.0))
    set_balance(m, B, 1.5)
    assert c.deadline == 16
    ok(m, B, "end_contract", {"contract_id": "contract-9"},
       "ended contract-9 with M-14b (its deadline day 16 passed with nothing released); its price 22 came back to "
       "you (the fees are not refunded). task-9 is back in your queue, due day 15 (2 days ago), and can be posted "
       "again")
    assert pending(m, M1) == [
        "[day 17, round 2] B-c53 ended contract-9 (its deadline day 16 passed with nothing released). Its price 22 "
        "went back to B-c53; the fees paid on it are not refunded. contract-9 takes no more runs, tests or "
        "deliveries; runs still going on it finish and stay charged, but their results cannot be delivered."]
    t = m.tasks["task-9"]
    assert (c.ended, t.state, t.post, t.contract, t.ended, t.worth, t.due) == (17, "queued", None, None,
                                                                               ["contract-9"], 60, 15)
    assert balance(m, B) == 23.5 and ledger(m, B) == [("escrow refunds", 22.0)] and m.deferred == []
    assert balance(m, M1) == 60.0 and not ledger(m, M1)
    assert not state.held(c) and state.refunded(c) == 22.0 and len(c.results) == 1
    assert [r["type"] for r in records(m)] == ["end", "money", "notification"]
    assert (records(m, "end")[0]["rule"], records(m, "end")[0]["refund"]) == ("overdue", 22.0)
    end_refused(m, "contract-9", "you already ended contract-9 on day 17")
    refuse(m, B, "approve_submission", {"contract_id": "contract-9"}, "you ended contract-9 on day 17")
    assert state.numbered("post", t) == "post-9-2" and state.numbered("contract", t) == "contract-9-2"


def test_end_row_plain_has_a_delivery():
    m = market(day=30)
    c = contract(m, "contract-30", due=9)
    deliver(c, "R-001", 29)
    end_refused(m, "contract-30", "contract-30 has a delivery: a plain contract can be ended only after its deadline "
                                  "day with nothing delivered, if it was signed on or before that day")
    m.day = 5
    end_refused(m, "contract-30", f"contract-30 has a delivery: {RULE_P}")


def test_end_row_plain_signed_after_its_deadline():
    m = market(day=30)
    c = contract(m, "contract-1", due=9, signed=15)
    assert c.deadline == 13
    end_refused(m, 1, f"contract-1 was signed on day 15, after its deadline day 13: {RULE_P}")
    c2 = contract(m, "contract-2", due=9, signed=13)
    assert c2.deadline == 13 and actions.run(m, B, "end_contract", {"contract_id": 2})[0]


def test_end_row_plain_not_yet_overdue():
    m = market(day=13)
    contract(m, "contract-10", due=9, signed=9)
    end_refused(m, "contract-10", "the deadline day of contract-10 is day 13: a plain contract can be ended only "
                                  "after its deadline day with nothing delivered, if it was signed on or before "
                                  "that day (from day 14)")
    m.day = 2
    end_refused(m, "contract-10", f"the deadline day of contract-10 is day 13: {RULE_P} (from day 14)")


def test_end_row_plain_overdue_examples():
    m = market(day=24, buyers=(B,), managers=("M-218",))
    c = contract(m, "contract-37", manager="M-218", price=20.0, due=23, signed=3, paid=8.0)
    ok(m, B, "end_contract", {"contract_id": "contract-37"},
       "ended contract-37 with M-218 (its deadline day 23 passed with nothing delivered); the 8 credits you paid on "
       "it are not returned. task-37 is back in your queue, due day 23 (1 day ago), and can be posted again")
    assert pending(m, "M-218") == [
        "[day 24, round 2] B-c53 ended contract-37 (its deadline day 23 passed with nothing delivered). Nothing more "
        "is owed on it. The 8 credits B-c53 paid on it before ending it are not returned. contract-37 takes no more "
        "runs, tests or deliveries; runs still going on it finish and stay charged, but their results cannot be "
        "delivered."]
    assert balance(m, B) == 60.0 and not ledger(m, B) and state.owed(c) == 0.0 and c.paid == 8.0
    assert [r["type"] for r in records(m)] == ["end", "notification"]
    assert (records(m, "end")[0]["rule"], records(m, "end")[0]["refund"]) == ("overdue", 0.0)
    c2 = contract(m, "contract-38", manager="M-218", price=20.0, due=22, signed=3, paid=1.0)
    ok(m, B, "end_contract", {"contract_id": 38},
       "ended contract-38 with M-218 (its deadline day 22 passed with nothing delivered); the 1 credits you paid on "
       "it are not returned. task-38 is back in your queue, due day 22 (2 days ago), and can be posted again")
    assert "Nothing more is owed on it. The 1 credits B-c53 paid on it before" in pending(m, "M-218")[1]
    assert c2.ended == 24
    contract(m, "contract-39", manager="M-218", due=23, signed=3)
    ok(m, B, "end_contract", {"contract_id": 39},
       "ended contract-39 with M-218 (its deadline day 23 passed with nothing delivered). task-39 is back in your "
       "queue, due day 23 (1 day ago), and can be posted again")
    assert pending(m, "M-218")[2] == (
        "[day 24, round 2] B-c53 ended contract-39 (its deadline day 23 passed with nothing delivered). Nothing more "
        "is owed on it. contract-39 takes no more runs, tests or deliveries; runs still going on it finish and stay "
        "charged, but their results cannot be delivered.")


# 1.2: a trial on a copy has no effect on the market


def test_an_action_on_a_copy_leaves_the_market_alone(monkeypatch):
    m = market(day=7)
    task(m, 1), post(m, "post-2"), bid(m, 1, "post-2", M1, 20.0, 5.0)
    c = contract(m, "contract-3", "escrow")
    deliver(c, "R-001", 6, "review")
    c4 = contract(m, "contract-4")
    deliver(c4, "R-002", 6)
    fix_reading(monkeypatch, 5.0, 5.0)
    plan = [("post_task", {"task_id": 1}), ("accept_bid", {"bid_id": 1}), ("test_delivery", {"contract_id": 4}),
            ("keep_delivery", {"contract_id": 4, "result": "R-002"}), ("approve_submission", {"contract_id": 3})]
    s, before = copy.deepcopy(m), copy.deepcopy(m)
    trial = [actions.run(s, B, name, args, actions.implicit(s, B, name, args)) for name, args in plan]
    assert all(okay for okay, _ in trial)
    assert data(m) == data(before) and not records(m) and s.log is None and s.records == 0
    assert [actions.run(m, B, name, args) for name, args in plan] == trial
    assert data(m) != data(before) and m.actor is None
    assert {k: v for k, v in data(m).items() if k != "records"} == {k: v for k, v in data(s).items() if k != "records"}
