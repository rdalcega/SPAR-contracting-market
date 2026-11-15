"""An action phase (market/phase.py) against spec/rounds.md, 6 to 8, and spec/vectors/round_order.jsonl, both steps
of "How an implementation can check itself": every case's order and clash records from its input alone (step 1),
then its outcomes on a market built from its `market`: each action's outcome, status and line, the notifications,
the credits and what each correction is told (step 2). After them, small phases made by hand: each rule of 6.3's
table, each cycle of 6.5, and the running of a phase (6.6 to 6.8, 7, 8.2, 8.3, 8.6).

`build` makes a market from a case's `market`, field by field; `seed` adds the four facts a case's `market` does
not give, from its expected lines; `feed` gives the draws the values the reference implementation drew."""

import copy
import json
import pathlib
import re

import pytest

from market import actions, config, phase, rng, state
from market.phase import Node
from market.state import Bid, Contract, Credit, Delivery, Dispute, Market, Post, Result, Task

VECTORS = pathlib.Path(__file__).parent.parent / "spec" / "vectors" / "round_order.jsonl"
CASES = [json.loads(line) for line in VECTORS.read_text(encoding="utf-8").splitlines()]
DECISIONS = {"under review": "review", "approved": "approved", "released without a decision": "auto",
             "rejected": "rejected"}
PREFERENCES = {"prefers escrow": ("escrow", None), "prefers a plain contract": ("plain", None),
               "no preferred form": (None, None)}
STATED = re.compile(r"already states this \((.*)\): nothing changed$|\(was: (.*)\); its bids stand as they were made$")
THRESHOLD = re.compile(r"ERROR: threshold must be a (quality|whole number) between 0 and 10, got ")
DISPUTABLE = re.compile(r"can dispute your rejection of (.*) through day (\d+): ")
FEE_PAID = re.compile(r"^- test_delivery: OK: |ERROR: you owe the arbiter nothing on ")
FEE = re.compile(FEE_PAID.pattern
                 + r"|^- pay_escrow_arbiter: OK: |once you pay the arbiter's fee \(pay_escrow_arbiter\)$")
PAID_FOR = re.compile(r"^- pay: OK: paid (\S+) to \S+ for (\S+)$")
STARTED = re.compile(r"OK: started (\S+) on .* arrives at the start of day (\d+); charged (\S+) credits$")
READ = re.compile(r"OK: (\S+) reading: (\S+) \(charged ")


# The market of a case (vectors/README.md, "Fields")


def number(ident):
    """The first number of an id: tasks and bids are numbered as they are created."""
    return int(ident.split("-")[1])


def merge(*lists):
    """One order that keeps the order of every list: the creation order, where the lists agree."""
    before = {}
    for ids in lists:
        for i, ident in enumerate(ids):
            before.setdefault(ident, set()).update(ids[:i])
    out = []
    while before:
        ident = next((i for i in before if before[i] <= set(out)), None)
        assert ident is not None, f"the lists disagree on the order of {sorted(before)}"
        out.append(ident)
        del before[ident]
    return out


def ordered(items, *lists):
    order = merge(*lists)
    assert set(order) == set(items)
    return {ident: items[ident] for ident in order}


def factor(cfg, deadline, due, day):
    """The share of the price a release on a submission of that day pays the manager (processes.md, 6.8)."""
    decay = cfg["value"]["late_decay"]
    if deadline == due:
        return max(0, 1 - decay * max(0, day - due))
    if day - due >= config.escrow_close_days(cfg):
        return 0
    return max(0, 1 - decay * max(0, day - deadline))


def contract(m, cid, given):
    """A contract listed under `contracts`. What the case does not give: a decision's day is the earliest that
    gives the contract's state, and the arbiter's fee of a ruling for the manager is not paid yet."""
    escrow = m.cfg["contracts"]["escrow"]
    assert given["due_day"] == m.tasks[given["task"]].due
    c = Contract(cid, given["task"], given["post"], given["buyer"], given["manager"], given["form"], given["price"],
                 given["threshold"], given["signed"][0], given["deadline_day"],
                 ended=given["ended"] and given["ended"][0])
    for r in given["results"]:
        assert r["arrived"] == (r["arrival_day"] <= m.day)
        c.results.append(Result(r["result"], r["worker"], r["kind"], r["refines"], r["started"], r["arrival_day"],
                                5.0, list(r["manager_readings"]), list(r["buyer_readings"])))
    c.label_draws = len(c.results)
    if c.form == "plain":
        c.kept, c.paid = given["kept"], given["paid"]
        c.deliveries = [Delivery(d["result"], d["day"], d["round"]) for d in given["deliveries"]]
        return c
    for s in given["submissions"]:
        d = Delivery(s["result"], s["day"], s["round"], outcome=DECISIONS[s["decision"]])
        if d.outcome != "review":
            d.decided = s["day"] + (escrow["review_days"] if d.outcome == "auto" else 0)
        if s.get("disputed"):
            assert s["disputed"]["ruling_day"] == s["disputed"]["day"] + 1
            d.dispute = Dispute(s["disputed"]["day"], winner=s["disputed"]["winner"])
        c.deliveries.append(d)
    last = state.latest(c)
    if given["state"] == "HELD, REJECTED, DISPUTABLE":
        last.decided = max(last.day, m.day - escrow["dispute_window_days"])
    if given["state"] == "RELEASED":
        ruled = last.outcome == "rejected"
        c.release_how = "ruling" if ruled else "automatic" if last.outcome == "auto" else "approved"
        c.release_day = last.dispute.day + 1 if ruled else last.decided
        c.paid = round(c.price * factor(m.cfg, c.deadline, given["due_day"], last.day), 4)
        c.received = None if ruled and state.dispute_fee(m, c) > 0 else c.release_day
    if given["state"] == "CLOSED UNRELEASED":
        c.closed = "late"
    return c


def build(inp):
    """The market as a phase starts, from a case's input alone."""
    given = inp["market"]
    m = Market(seed=0, cfg=config.resolve({"message_channels": inp["message_channels"]}))
    m.day, m.round, m.bids_created = inp["day"], inp["round"], given["bids_created"]
    roles = [p["role"] for p in given["participants"].values()]
    assert roles == sorted(roles)  # seat order, buyers first
    for pid, p in given["participants"].items():
        q = state.add_participant(m, pid, p["role"])
        q.balance, q.out_of_business = p["balance"], p["out_of_business"]
    for tid, t in given["tasks"].items():
        m.tasks[tid] = Task(tid, t["buyer"], 60, t["due_day"], t["state"], t["current_post"], t["current_contract"])
    for post_id, p in given["posts"].items():
        closed = p.get("closed") or {}
        assert p["open"] == (not closed)
        m.posts[post_id] = Post(post_id, p["task"], p["buyer"], *p["posted"], closed=closed.get("why"),
                                closed_day=closed.get("day"), closed_round=closed.get("round"))
    for bid_id, b in given["bids"].items():
        m.bids[bid_id] = Bid(bid_id, b["post"], b["manager"], b["price"], b["threshold"], b["status"])
    for cid, c in given["contracts"].items():
        m.contracts[cid] = contract(m, cid, c)

    # What only a participant's lists name: stubs that the hints, the capacity and the next ids read
    for pid, p in given["participants"].items():
        for cid, tid, ended in p.get("contracts", ()):
            if cid not in given["contracts"]:
                stub = Contract(cid, tid, "post" + cid.removeprefix("contract"), "", "", "plain", 0.0, None, 0, 0,
                                ended=0 if ended else None)
                setattr(m.contracts.setdefault(cid, stub), p["role"], pid)
                m.tasks.setdefault(tid, Task(tid, pid if p["role"] == "buyer" else "", 60, 0, "contracted"))
        for run in p.get("runs_in_progress", ()):
            if run["contract"] not in given["contracts"]:
                c = m.contracts[run["contract"]]
                c.results.append(Result(run["result"], run["worker"], "fresh", None, 0, run["arrival_day"], 5.0))
                c.label_draws += 1
        for tid in p.get("queued_tasks", ()):
            if tid not in given["tasks"]:
                m.tasks[tid] = Task(tid, pid, 60, 0)
        for bid_id, manager, price, threshold, post_id in p.get("open_bids_on_posts", ()):
            m.bids.setdefault(bid_id, Bid(bid_id, post_id, manager, price, threshold))
            m.posts.setdefault(post_id, Post(post_id, f"task-{number(post_id)}", pid, 0, 1))
        for bid_id, post_id in p.get("open_bids", ()):
            m.bids.setdefault(bid_id, Bid(bid_id, post_id, pid, 0.0))
    for post_id in given["open_posts"]:
        m.posts.setdefault(post_id, Post(post_id, f"task-{number(post_id)}", "", 0, 1))

    # Creation order
    m.tasks = dict(sorted(m.tasks.items(), key=lambda item: number(item[0])))
    m.bids = dict(sorted(m.bids.items(), key=lambda item: number(item[0])))
    m.posts = ordered(m.posts, list(given["posts"]), given["open_posts"])
    m.contracts = ordered(m.contracts, list(given["contracts"]),
                          *([c[0] for c in p.get("contracts", ())] for p in given["participants"].values()))
    for t in m.tasks.values():
        t.ended = [c.id for c in m.contracts.values() if c.task == t.id and c.ended is not None]
    return m


def seed(m, case):
    """What `market` does not give and some expected lines state: a post's stated preference (from the lines of
    the edit_post actions on it, up to the first that succeeds; a threshold refused for its value on an edit that
    names no form shows that the post prefers escrow); the day of a rejection that can still be disputed; whether
    the buyer paid the arbiter's fee after a ruling for the manager (the first line that tells: a test of the
    result or `you owe the arbiter nothing` say it did); and what is owed on a contract that only its
    participants' lists name, when a payment that names no contract counts toward it."""
    stated, escrow, edited, fee = {}, set(), set(), set()
    for pid, pos, name in case["expected"]["order"]:
        line = case["expected"]["actions"][pid][pos]["line"]
        args = case["input"]["plans"][pid][pos]["arguments"]
        if line is None:
            continue
        c = state.find(m, "contract", args.get("contract_id"))
        if c is not None and c.release_how == "ruling" and c.id not in fee and FEE.search(line):
            fee.add(c.id)
            if FEE_PAID.search(line):
                c.received = c.release_day
        said = PAID_FOR.search(line)
        if name == "pay" and said and said[2] not in case["input"]["market"]["contracts"]:
            m.contracts[said[2]].price += float(said[1])
        post = state.find(m, "post", args.get("post_id"))
        if name == "edit_post" and post is not None and post.id not in edited:
            said = STATED.search(line)
            if said:
                stated.setdefault(post.id, said[1] or said[2])
                if said[2]:
                    edited.add(post.id)
            elif THRESHOLD.search(line) and args.get("form") in (None, ""):
                escrow.add(post.id)
        said = DISPUTABLE.search(line)
        c = state.find(m, "contract", args.get("contract_id"))
        if name == "end_contract" and said and c is not None:
            d = state.delivery_of(c, said[1])
            if d is not None and d.outcome == "rejected":
                d.decided = int(said[2]) - m.cfg["contracts"]["escrow"]["dispute_window_days"]
    for post_id in escrow:
        m.posts[post_id].form = "escrow"
    for post_id, text in stated.items():
        head, _, t = text.partition(", threshold ")
        m.posts[post_id].form, m.posts[post_id].threshold = PREFERENCES[head][0], float(t) if t else None


def start_state(m, inp):
    """The phase's start state, which the shadows are made from: the market before the token charges made since."""
    start = copy.deepcopy(m)
    for pid, p in inp["market"]["participants"].items():
        if "start_state" in p:
            start.participants[pid].balance = p["start_state"]["balance"]
            start.participants[pid].out_of_business = p["start_state"]["out_of_business"]
    return start


def contract_state(m, c):
    """A contract's state as `market` names it (processes.md, 6.1)."""
    if c.ended is not None:
        return "ENDED"
    if c.form == "plain":
        return "DELIVERED" if c.deliveries else "NOTHING DELIVERED"
    if c.release_day is not None:
        return "RELEASED"
    if c.closed:
        return "CLOSED UNRELEASED"
    if not c.deliveries:
        return "HELD, NOTHING SUBMITTED"
    if state.under_review(c):
        return "HELD, UNDER REVIEW"
    if state.open_dispute(c):
        return "HELD, IN DISPUTE"
    return "HELD, REJECTED, DISPUTABLE" if state.disputable(m, c) else "HELD, REJECTION STANDS"


# Step 1: the order and the clashes


def nodes_of(m, order, plans):
    """A phase's nodes, by their participant's rank and then by position, with their targets on the market."""
    mode = m.cfg["message_channels"]
    return [Node(pid, pos, name, args, *phase.classify(m.participants[pid].role, mode, name),
                 phase.targets(m, pid, name, args), 0)
            for pid in order for pos, (name, args) in enumerate(plans.get(pid, ()))]


def ordering(m, order, plans):
    """A phase's run order and clash records, as the log records them (rounds.md, 6.8)."""
    def ref(n):
        return [n.pid, n.pos, n.name or ""]

    nodes = nodes_of(m, order, plans)
    edges = phase.clashes(nodes)
    placed = phase.place(nodes, edges, {pid: rank for rank, pid in enumerate(order)})
    assert [n.at for n in placed] == list(range(len(nodes)))
    return ([ref(n) for n in placed],
            [{"rule": e.rule, "first": ref(e.first), "second": ref(e.second), "held": e.held} for e in edges])


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_vector_order_and_clashes(case):
    inp = case["input"]
    plans = {pid: [(a["name"], a["arguments"]) for a in plan] for pid, plan in inp["plans"].items()}
    order, edges = ordering(build(inp), inp["turn_order"], plans)
    assert order == case["expected"]["order"]
    assert edges == [{k: e[k] for k in ("rule", "first", "second", "held")} for e in case["expected"]["clashes"]]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_vector_market_gives_back_the_case(case):
    """The built market states what the case states: each contract's state and each list of a participant."""
    given = case["input"]["market"]
    m = build(case["input"])
    for seeded in (False, True):
        if seeded:
            seed(m, case)
        for cid, c in given["contracts"].items():
            assert contract_state(m, m.contracts[cid]) == c["state"]
            found = phase.targets(m, c["buyer"], "end_contract", {"contract_id": cid})
            assert found == {"contract": cid, "escrow": c["form"] == "escrow",
                             "rejected": c["latest_submission_rejected"]}
    bids = [b for b in m.bids.values() if b.status == "open"]
    assert [p.id for p in m.posts.values() if p.closed is None] == given["open_posts"]
    for pid, p in given["participants"].items():
        if "contracts" in p:
            assert [[c.id, c.task, c.ended is not None] for c in state.contracts_of(m, pid)] == p["contracts"]
        if "open_bids" in p:
            assert [[b.id, b.post] for b in bids if b.manager == pid] == p["open_bids"]
            runs = [{"result": r.label, "contract": c.id, "worker": worker, "arrival_day": r.arrival}
                    for worker in ("employee", "manager") for c, r in state.in_flight(m, pid, worker)]
            assert sorted(runs, key=str) == sorted(p["runs_in_progress"], key=str)
        if "queued_tasks" in p:
            assert [t.id for t in m.tasks.values() if t.buyer == pid and t.state == "queued"] == p["queued_tasks"]
            assert [[b.id, b.manager, b.price, b.threshold, b.post] for b in bids
                    if b.post in m.posts and m.posts[b.post].buyer == pid] == p["open_bids_on_posts"]
    start = start_state(m, case["input"])
    for pid, p in given["participants"].items():
        q, was = start.participants[pid], p.get("start_state", p)
        assert (q.balance, q.out_of_business) == (was["balance"], was["out_of_business"])
        assert (m.participants[pid].balance, m.participants[pid].out_of_business) == (p["balance"],
                                                                                    p["out_of_business"])


# Step 2: the outcomes


def draws(m, case):
    """The values the reference implementation drew, by the key of their draw (randomness.md, 3.8 to 3.10): each
    `drawn` line gives a run's label, arrival day and cost, or a reading."""
    sim, runs, labels, readings = copy.deepcopy(m), {}, {}, {}
    for pid, pos, name in case["expected"]["order"]:
        done = case["expected"]["actions"][pid][pos]
        if not done.get("drawn"):
            continue
        args = case["input"]["plans"][pid][pos]["arguments"]
        c = state.find(sim, "contract", args["contract_id"])
        stream = rng.stream_key(c.id)
        if name in phase.RUNS:
            label, arrival, cost = STARTED.search(done["line"]).groups()
            worker = "employee" if name == "delegate_run" else "manager"
            kind = "refine" if actions.given(args, "refine") else "fresh"
            k = sum(r.worker == worker and r.kind == kind for r in c.results)
            runs[stream, worker, kind, k] = {"days": int(arrival) - sim.day, "cost": float(cost)}
            labels[stream, len(c.results)] = label
            c.results.append(Result(label, worker, kind, None, sim.day, int(arrival), 5.0))
        else:
            label, value = READ.search(done["line"]).groups()
            side = "manager" if name == "test_result" else "buyer"
            taken = getattr(state.result(c, label), side + "_readings")
            readings[stream, side, label, len(taken)] = float(value)
            taken.append(float(value))
    return runs, labels, readings


def feed(monkeypatch, m, case):
    """Give the three draws whose values the expected texts hold the values the reference drew (vectors/README.md:
    yours differ); any other draw, and what the lines do not give (the variates, a quality), is the generator's."""
    runs, labels, readings = draws(m, case)
    run, label, reading = rng.run, rng.label, rng.reading

    def fed_run(seed, stream, worker, kind, k, production, q_parent=None):
        return run(seed, stream, worker, kind, k, production, q_parent) | runs.get((stream, worker, kind, k), {})

    def fed_label(seed, stream, n, used):
        found = labels.get((stream, len(used)))
        return {"label": found, "n": n + 1} if found else label(seed, stream, n, used)

    def fed_reading(seed, stream, side, result, i, q, sd, decimals):
        drawn = reading(seed, stream, side, result, i, q, sd, decimals)
        return drawn | {"reading": readings.get((stream, side, result, i), drawn["reading"])}

    monkeypatch.setattr(rng, "run", fed_run)
    monkeypatch.setattr(rng, "label", fed_label)
    monkeypatch.setattr(rng, "reading", fed_reading)


def status(n):
    return None if n.ok is None else "OK" if n.ok else "ERROR"


def ref(n):
    return [n.pid, n.pos, n.name or ""]


def observed(m, ph, start):
    """What a phase did, in the form of a case's `expected` (vectors/README.md, "Fields")."""
    out = {
        "order": [ref(n) for n in ph.order],
        "clashes": [{"rule": e.rule, "first": ref(e.first), "second": ref(e.second), "held": e.held,
                     "first_status": status(e.first), "second_status": status(e.second)} for e in ph.edges],
        "actions": {pid: [{"outcome": n.outcome, "status": status(n),
                           "line": None if n.ok is None else actions.result_line(n.name, n.ok, n.message)}
                          for n in plan] for pid, plan in ph.plans.items()},
        "notifications": {p.id: p.pending for p in m.participants.values() if p.pending},
        "credits_at_round_end": [{"to": c.to, "amount": c.amount, "kind": c.kind, "payer": c.payer}
                                 for c in m.deferred],
    }
    if ph.kind == "first":
        out["corrections"] = {}
        for pid, plan in ph.plans.items():
            if any(n.ok is False for n in plan) and not m.participants[pid].out_of_business:
                r = phase.review(m, ph, pid, start)
                out["corrections"][pid] = {
                    "lost": [{"position": pos, "rule": rule, "winner": winner and list(winner)}
                             for pos, rule, winner in r.lost],
                    "notes": {str(pos): note for pos, note in r.notes.items()},
                    "facts": r.facts, "shown_bids": r.shown}
    return out


def run_case(case, monkeypatch):
    """Run a case's phase on the market built from its input; the market, the phase and its start state."""
    inp = case["input"]
    m = build(inp)
    seed(m, case)
    start = start_state(m, inp)
    feed(monkeypatch, m, case)
    plans = {pid: [(a["name"], a["arguments"]) for a in plan] for pid, plan in inp["plans"].items()}
    return m, phase.run_phase(m, inp["phase"], inp["turn_order"], plans, start, inp.get("hidden")), start


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_vector_outcomes(case, monkeypatch):
    """Step 2 of "How an implementation can check itself": every action's outcome, status and line, the statuses
    in the clash records, the notifications, the credits and, for a first phase, what each correction is told."""
    m, ph, start = run_case(case, monkeypatch)
    got, expected = observed(m, ph, start), copy.deepcopy(case["expected"])
    for plan in expected["actions"].values():
        for done in plan:
            done.pop("drawn", None)
    for key in expected:
        assert got[key] == expected[key], key
    assert got.keys() == expected.keys()


# Small phases made by hand

BUYERS, MANAGERS = ("B-1", "B-2"), ("M-1", "M-2")
EVERYONE = BUYERS + MANAGERS
C1, C2, C3, C4, C5 = (f"contract-{n}" for n in range(1, 6))


def small(mode="all"):
    """Day 5, round 1. B-1 and M-1 hold contract-1 (plain, a delivery), contract-2 (escrow, nothing submitted),
    contract-3 (escrow, its latest submission rejected), contract-4 (escrow, a submission under review) and
    contract-5 (plain, nothing delivered). B-1's post-9 is open with bid-1 of M-1 and bid-2 of M-2, its post-8
    with bid-3 of M-1; B-2's post-7 is open with no bid."""
    m = Market(seed=0, cfg=config.resolve({"message_channels": mode}))
    for pid in EVERYONE:
        state.add_participant(m, pid, "buyer" if pid in BUYERS else "manager")
    m.day, m.round = 5, 1
    for n, form in enumerate(("plain", "escrow", "escrow", "escrow", "plain"), 1):
        m.tasks[f"task-{n}"] = Task(f"task-{n}", "B-1", 60, 9, "contracted", f"post-{n}", f"contract-{n}")
        m.posts[f"post-{n}"] = Post(f"post-{n}", f"task-{n}", "B-1", 1, 1, closed="accepted", closed_day=1,
                                    closed_round=3)
        m.contracts[f"contract-{n}"] = Contract(f"contract-{n}", f"task-{n}", f"post-{n}", "B-1", "M-1", form, 20.0,
                                                5.0 if form == "escrow" else None, 1, 9)
    m.contracts[C1].deliveries.append(Delivery("R-001", 4, 1))
    m.contracts[C3].deliveries.append(Delivery("R-003", 3, 1, outcome="rejected", decided=3))
    m.contracts[C4].deliveries.append(Delivery("R-004", 4, 1, outcome="review"))
    for n, buyer in ((7, "B-2"), (8, "B-1"), (9, "B-1")):
        m.tasks[f"task-{n}"] = Task(f"task-{n}", buyer, 60, 12, "posted", f"post-{n}")
        m.posts[f"post-{n}"] = Post(f"post-{n}", f"task-{n}", buyer, 3, 1)
    m.bids["bid-1"] = Bid("bid-1", "post-9", "M-1", 20.0)
    m.bids["bid-2"] = Bid("bid-2", "post-9", "M-2", 18.0, 5.0)
    m.bids["bid-3"] = Bid("bid-3", "post-8", "M-1", 15.0)
    m.bids_created = 3
    return m


def a(name, **args):
    return name, args


def on(name, contract_id):
    return name, {"contract_id": contract_id}


def phase_of(plans, order=EVERYONE, m=None):
    """The run order as (participant, position), and the edges as (rule, first, second) by whether they held."""
    placed, edges = ordering(m or small(), order, plans)
    return ([(pid, pos) for pid, pos, _ in placed],
            {held: {(e["rule"], tuple(e["first"][:2]), tuple(e["second"][:2])) for e in edges if e["held"] == held}
             for held in (True, False)})


# rounds.md, 6.2 and 6.4

OWN_KINDS = {
    "buyer": {"post_task": 0, "test_delivery": 0, "pay_escrow_arbiter": 0, "keep_delivery": 0, "pay": 0,
              "approve_submission": 2, "reject_submission": 2, "end_contract": 2, "accept_bid": 4,
              "withdraw_post": 4, "edit_post": 7},
    "manager": {"pay": 0, "deliver": 1, "delegate_run": 3, "run_myself": 3, "test_result": 3, "dispute": 3,
                "withdraw_bid": 5, "bid": 6},
}


@pytest.mark.parametrize("role", ["buyer", "manager"])
def test_classify_every_name(role):
    other = "manager" if role == "buyer" else "buyer"
    for mode in config.MODES:
        for name, kind in OWN_KINDS[role].items():
            assert phase.classify(role, mode, name) == (True, kind)
        for name in OWN_KINDS[other]:
            if name != "pay":
                assert phase.classify(role, mode, name) == (False, 0)  # the other role's action, whatever its kind
        for name in (None, "", "approve", "reject", "pay_arbiter", "delegate", "BID", " bid ", "Deliver", "compute",
                     "end_turn", "verify", "update_memory"):
            assert phase.classify(role, mode, name) == (False, 0)
        sends = mode in ("within_roles", "all", role + "s")
        assert phase.classify(role, mode, "send_message") == (sends, 0)


def test_targets_of_each_kind():
    m = small()
    for name in ("deliver", "delegate_run", "run_myself", "test_result", "dispute"):
        assert phase.targets(m, "M-1", name, {"contract_id": C2}) == {"contract": C2}
    for name in ("keep_delivery", "approve_submission", "reject_submission"):
        assert phase.targets(m, "B-1", name, {"contract_id": C2}) == {"contract": C2}
    assert phase.targets(m, "B-1", "accept_bid", {"bid_id": "bid-2"}) == {"bid": "bid-2", "post": "post-9",
                                                                         "manager": "M-2"}
    assert phase.targets(m, "M-1", "bid", {"post_id": "post-9", "price": 3}) == {"post": "post-9"}
    assert phase.targets(m, "B-1", "withdraw_post", {"post_id": "post-9"}) == {"post": "post-9"}
    assert phase.targets(m, "M-2", "withdraw_bid", {"post_id": "post-9"}) == {"bid": "bid-2", "post": "post-9"}
    for name in ("post_task", "edit_post", "test_delivery", "pay_escrow_arbiter", "pay", "send_message"):
        assert phase.targets(m, "B-1", name, {"contract_id": C1, "post_id": "post-9", "bid_id": "bid-1"}) == {}
    for name in ("pay", "send_message", "delegate", None, " bid "):
        assert phase.targets(m, "M-1", name, {"contract_id": C1, "post_id": "post-9"}) == {}


def test_targets_are_a_lookup_of_the_id_only():
    m = small()
    # not the actor's contract, post or bid; a closed post; a bid on another buyer's post
    assert phase.targets(m, "M-2", "deliver", {"contract_id": C1}) == {"contract": C1}
    assert phase.targets(m, "B-2", "end_contract", {"contract_id": C1}) == {"contract": C1, "escrow": False,
                                                                           "rejected": False}
    assert phase.targets(m, "B-2", "withdraw_post", {"post_id": "post-9"}) == {"post": "post-9"}
    assert phase.targets(m, "B-2", "accept_bid", {"bid_id": "bid-1"}) == {"bid": "bid-1", "post": "post-9",
                                                                         "manager": "M-1"}
    assert phase.targets(m, "M-1", "bid", {"post_id": "post-1"}) == {"post": "post-1"}
    # the id rule of actions-buyer.md, 1.6: a bare number, an integer; nothing trimmed, lowercased or rewritten
    assert phase.targets(m, "M-1", "deliver", {"contract_id": "1"}) == {"contract": C1}
    assert phase.targets(m, "M-1", "deliver", {"contract_id": 1}) == {"contract": C1}
    assert phase.targets(m, "B-1", "accept_bid", {"bid_id": 2})["bid"] == "bid-2"
    for raw in ("Contract-1", " contract-1", "contract 1", "post-1", "01", 1.0, "", None, True, [1], "contract-99"):
        assert phase.targets(m, "M-1", "deliver", {"contract_id": raw}) == {}
    # the one name each argument is read under
    assert phase.targets(m, "M-1", "deliver", {"contract": C1}) == {}
    assert phase.targets(m, "B-1", "accept_bid", {"bid": "bid-1"}) == {}
    assert phase.targets(m, "M-1", "bid", {"post": "post-9"}) == {}
    assert phase.targets(m, "M-1", "withdraw_bid", {"bid_id": "bid-1"}) == {}


def test_targets_of_an_action_that_is_not_its_participants_own():
    m = small("managers")
    assert phase.targets(m, "B-1", "bid", {"post_id": "post-9"}) == {}
    assert phase.targets(m, "B-1", "deliver", {"contract_id": C1}) == {}
    assert phase.targets(m, "M-1", "withdraw_post", {"post_id": "post-9"}) == {}
    assert phase.targets(m, "M-1", "end_contract", {"contract_id": C1}) == {}
    assert phase.targets(m, "M-1", "accept_bid", {"bid_id": "bid-1"}) == {}
    assert phase.targets(m, "B-1", "send_message", {"to": "M-1", "contract_id": C1}) == {}


def test_targets_of_withdraw_bid():
    m = small()
    assert phase.targets(m, "M-1", "withdraw_bid", {"post_id": "9"}) == {"bid": "bid-1", "post": "post-9"}
    # no open bid: its bid on the post created last; with an open one: that one
    m.bids["bid-1"].status = "lost"
    m.bids["bid-4"] = Bid("bid-4", "post-9", "M-1", 9.0, status="withdrawn")
    assert phase.targets(m, "M-1", "withdraw_bid", {"post_id": "post-9"}) == {"bid": "bid-4", "post": "post-9"}
    m.bids["bid-1"].status = "open"
    assert phase.targets(m, "M-1", "withdraw_bid", {"post_id": "post-9"}) == {"bid": "bid-1", "post": "post-9"}
    # where finding the bid gives an error: no post_id, an unknown post, no bid of its own on the post
    assert phase.targets(m, "M-1", "withdraw_bid", {}) == {}
    assert phase.targets(m, "M-1", "withdraw_bid", {"post_id": "bid-1"}) == {}
    assert phase.targets(m, "M-2", "withdraw_bid", {"post_id": "post-8"}) == {}


def test_targets_of_end_contract():
    m = small()

    def found(cid):
        return phase.targets(m, "B-1", "end_contract", {"contract_id": cid})

    assert found(C1) == {"contract": C1, "escrow": False, "rejected": False}
    assert found(C2) == {"contract": C2, "escrow": True, "rejected": False}  # nothing submitted
    assert found(C4) == {"contract": C4, "escrow": True, "rejected": False}  # under review
    assert found(C3) == {"contract": C3, "escrow": True, "rejected": True}  # the rejection stands
    last = m.contracts[C3].deliveries[-1]
    last.decided = 5  # disputable
    assert found(C3)["rejected"]
    last.dispute = Dispute(5)  # in dispute
    assert found(C3)["rejected"]
    last.dispute = Dispute(4, 6.0, "buyer")  # standing after the ruling
    assert found(C3)["rejected"]
    last.dispute = Dispute(4, 6.0, "manager")  # overturned by the ruling
    m.contracts[C3].release_day, m.contracts[C3].release_how = 5, "ruling"
    assert found(C3)["rejected"]
    for outcome in ("approved", "auto"):
        m.contracts[C4].deliveries[-1].outcome = outcome
        assert not found(C4)["rejected"]
    m.contracts[C3].deliveries.append(Delivery("R-005", 5, 1, outcome="review"))  # a submission after the rejection
    assert not found(C3)["rejected"]


# rounds.md, 6.3: each rule of the table; the first action of a pair is the one that must run first

B, M, N = ("B-1", 0), ("M-1", 0), ("M-2", 0)
POST9 = {"post_id": "post-9"}
RULES = [
    ("A1", {"M-1": [on("deliver", C1)], "B-1": [on("end_contract", C1)]}, {("A1", M, B)}),
    ("A3", {"M-1": [on("deliver", C3)], "B-1": [on("end_contract", C3)]}, {("A3", M, B)}),
    ("A2 nothing submitted", {"M-1": [on("deliver", C2)], "B-1": [on("end_contract", C2)]}, {("A2", M, B)}),
    ("A2 under review", {"M-1": [on("deliver", C4)], "B-1": [on("end_contract", C4)]}, {("A2", M, B)}),
    ("A4 delegate_run", {"M-1": [on("delegate_run", C1)], "B-1": [on("end_contract", C1)]}, {("A4", B, M)}),
    ("A4 run_myself", {"M-1": [on("run_myself", C3)], "B-1": [on("end_contract", C3)]}, {("A4", B, M)}),
    ("A5", {"M-1": [on("test_result", C2)], "B-1": [on("end_contract", C2)]}, {("A5", B, M)}),
    ("A6 delegate_run", {"M-1": [on("delegate_run", C4)], "B-1": [on("approve_submission", C4)]}, {("A6", B, M)}),
    ("A6 run_myself", {"M-1": [on("run_myself", C4)], "B-1": [on("approve_submission", C4)]}, {("A6", B, M)}),
    ("A7", {"M-1": [on("test_result", C4)], "B-1": [on("approve_submission", C4)]}, {("A7", B, M)}),
    ("B_keep", {"M-1": [on("deliver", C1)], "B-1": [on("keep_delivery", C1)]}, {("B_keep", B, M)}),
    ("A8", {"B-1": [a("accept_bid", bid_id="bid-1")], "M-1": [a("bid", price=9, **POST9)]}, {("A8", B, M)}),
    ("B_accept bid", {"B-1": [a("accept_bid", bid_id="bid-1")], "M-2": [a("bid", price=9, **POST9)]},
     {("B_accept", B, N)}),
    ("A9", {"B-1": [a("accept_bid", bid_id="bid-1")], "M-1": [a("withdraw_bid", **POST9)]}, {("A9", B, M)}),
    ("B_accept withdraw_bid", {"B-1": [a("accept_bid", bid_id="bid-1")], "M-2": [a("withdraw_bid", **POST9)]},
     {("B_accept", B, N)}),
    ("B_withdraw", {"B-1": [a("withdraw_post", **POST9)], "M-1": [a("bid", price=9, **POST9)],
                    "M-2": [a("withdraw_bid", **POST9)]}, {("B_withdraw", B, M), ("B_withdraw", B, N)}),
    ("A10", {"M-1": [on("delegate_run", C1), on("run_myself", "contract-99"), a("test_result"), on("dispute", C3)],
             "M-2": [on("test_result", C1)], "B-1": [a("accept_bid", bid_id="bid-1")]},
     {("A10", ("M-1", pos), B) for pos in range(4)}),
    ("A10 and the bid of another buyer's post", {"M-2": [on("dispute", C3)], "B-2": [a("accept_bid", bid_id=2)]},
     {("A10", N, ("B-2", 0))}),
    # no rule
    ("another contract", {"M-1": [on("deliver", C1), on("test_result", C2), on("run_myself", C3)],
                          "B-1": [on("end_contract", C5), on("approve_submission", C4), on("keep_delivery", C5)]},
     set()),
    ("an unknown contract on both sides", {"M-1": [on("deliver", "contract-99")],
                                           "B-1": [on("end_contract", "contract-99")]}, set()),
    ("actions that take part in no clash",
     {"B-1": [on("reject_submission", C4), on("test_delivery", C1), on("pay_escrow_arbiter", C4),
              a("edit_post", **POST9), a("post_task", task_id="task-9"), a("pay", to="M-1", amount=1, contract_id=C1),
              a("send_message", to="M-1", text="x"), a("approve", contract_id=C4), a(None, contract_id=C1)],
      "M-1": [on("test_result", C4), on("delegate_run", C1), on("deliver", C1), a("bid", price=9, **POST9),
              a("withdraw_bid", post_id="post-8"), a("pay", to="B-1", amount=1), a("send_message", to="B-1")]},
     set()),
    ("an acceptance whose bid does not resolve", {"B-1": [a("accept_bid", bid_id="bid-99"), a("accept_bid")],
                                                  "M-1": [a("bid", price=9, **POST9), a("withdraw_bid", **POST9)]},
     set()),
    ("an acceptance on another post", {"B-1": [a("accept_bid", bid_id="bid-3"), a("withdraw_post", post_id="8")],
                                       "M-2": [a("bid", price=9, **POST9), a("withdraw_bid", **POST9)]}, set()),
    ("a withdrawal that finds no bid", {"B-1": [a("accept_bid", bid_id="bid-3"), a("withdraw_post", post_id="8")],
                                        "M-2": [a("withdraw_bid", post_id="post-8")]}, set()),
    ("a post that does not resolve", {"B-1": [a("withdraw_post", post_id="post-99")],
                                      "M-1": [a("bid", price=9, post_id="post-99")]}, set()),
    ("one participant's own actions", {"M-1": [on("test_result", C1), on("deliver", C1), a("withdraw_bid", **POST9),
                                               a("bid", price=9, **POST9)],
                                       "B-1": [on("keep_delivery", C1), on("end_contract", C1),
                                               a("withdraw_post", **POST9), a("accept_bid", bid_id="bid-2")]},
     {("A1", ("M-1", 1), ("B-1", 1)), ("A5", ("B-1", 1), M), ("B_keep", B, ("M-1", 1)),
      ("B_withdraw", ("B-1", 2), ("M-1", 2)), ("B_withdraw", ("B-1", 2), ("M-1", 3)),
      ("B_accept", ("B-1", 3), ("M-1", 2)), ("B_accept", ("B-1", 3), ("M-1", 3))}),
    # rounds.md, 6.2 and 8.6: actions that are not their participant's own resolve nothing
    ("not own", {"B-2": [a("bid", price=9, **POST9), a("accept_bid", bid_id="bid-1")],
                 "M-1": [a("withdraw_bid", **POST9), a("withdraw_post", **POST9)]},
     {("A9", ("B-2", 1), M)}),
    ("not own: the other role's names", {"B-1": [on("deliver", C1), on("test_result", C1), on("dispute", C3)],
                                         "B-2": [on("deliver", C1), a("withdraw_bid", **POST9)],
                                         "M-1": [on("end_contract", C1), on("keep_delivery", C1),
                                                 on("approve_submission", C4), a("accept_bid", bid_id="bid-2")],
                                         "M-2": [on("run_myself", C4), on("deliver", C1), a("bid", price=9, **POST9)]},
     set()),
]


@pytest.mark.parametrize("plans,expected", [rule[1:] for rule in RULES], ids=[rule[0] for rule in RULES])
@pytest.mark.parametrize("order", [EVERYONE, EVERYONE[::-1]], ids=["buyers first", "managers first"])
def test_clash_rule(order, plans, expected):
    placed, edges = phase_of(plans, order)
    assert edges[True] | edges[False] == expected
    assert sorted(placed) == sorted((pid, pos) for pid, plan in plans.items() for pos in range(len(plan)))
    at = {node: i for i, node in enumerate(placed)}
    assert all(at[first] < at[second] for _, first, second in edges[True])
    assert all(at[(pid, pos)] < at[(pid, pos + 1)] for pid, pos in placed if (pid, pos + 1) in at)


def test_clash_records_are_in_the_order_of_their_first_then_second_action():
    plans = {"B-1": [a("accept_bid", bid_id="bid-1"), on("end_contract", C1)],
             "M-1": [on("test_result", C1), a("bid", price=9, **POST9), on("deliver", C1)],
             "M-2": [a("withdraw_bid", **POST9), a("bid", price=9, **POST9)]}
    for order in (("M-2", "B-1", "B-2", "M-1"), ("M-1", "B-1", "M-2", "B-2")):
        rank = {pid: i for i, pid in enumerate(order)}
        edges = ordering(small(), order, plans)[1]
        keys = [(rank[e["first"][0]], e["first"][1], rank[e["second"][0]], e["second"][1]) for e in edges]
        assert keys == sorted(keys)
        assert sorted((e["rule"], e["first"][0], e["second"][0]) for e in edges) == [
            ("A1", "M-1", "B-1"), ("A10", "M-1", "B-1"), ("A5", "B-1", "M-1"), ("A8", "B-1", "M-1"),
            ("B_accept", "B-1", "M-2"), ("B_accept", "B-1", "M-2")]


# rounds.md, 6.5: free nodes by kind, rank and position

def test_free_nodes_run_by_kind_then_rank_then_position():
    plans = {"B-1": [a("post_task"), a("post_task")], "B-2": [a("post_task"), a("post_task")]}
    assert phase_of(plans, ("B-1", "M-1", "B-2", "M-2"))[0] == [("B-1", 0), ("B-1", 1), ("B-2", 0), ("B-2", 1)]
    assert phase_of(plans, ("M-2", "B-2", "B-1", "M-1"))[0] == [("B-2", 0), ("B-2", 1), ("B-1", 0), ("B-1", 1)]


def test_every_kind_in_its_fixed_order():
    plans = {"B-1": [a("edit_post", **POST9)], "M-1": [a("bid", price=9, post_id="post-8")],
             "M-2": [a("withdraw_bid", post_id="post-7")]}
    for order in (EVERYONE, EVERYONE[::-1]):
        assert phase_of(plans, order)[0] == [("M-2", 0), ("M-1", 0), ("B-1", 0)]
    plans = {"B-1": [a("withdraw_post", post_id="post-8")], "B-2": [on("reject_submission", C4)],
             "M-1": [on("dispute", C3)], "M-2": [on("deliver", C5)]}
    for order in (EVERYONE, EVERYONE[::-1]):
        assert phase_of(plans, order)[0] == [("M-2", 0), ("B-2", 0), ("M-1", 0), ("B-1", 0)]
    plans = {"B-1": [on("keep_delivery", C1)], "M-1": [a("pay", to="B-1", amount=1)], "M-2": [on("deliver", C5)],
             "B-2": [a("accept_bid", bid_id="bid-2")]}
    assert phase_of(plans, EVERYONE)[0] == [("B-1", 0), ("M-1", 0), ("M-2", 0), ("B-2", 0)]
    assert phase_of(plans, EVERYONE[::-1])[0] == [("M-1", 0), ("B-1", 0), ("M-2", 0), ("B-2", 0)]


def test_an_action_that_is_not_its_participants_own_has_kind_0():
    plans = {"B-1": [a("bid", price=9, **POST9)], "M-1": [a("edit_post", **POST9)], "M-2": [on("deliver", C5)],
             "B-2": [a("Deliver"), a(None)]}
    assert phase_of(plans, EVERYONE)[0] == [("B-1", 0), ("B-2", 0), ("B-2", 1), ("M-1", 0), ("M-2", 0)]


@pytest.mark.parametrize("order", [EVERYONE, EVERYONE[::-1]], ids=["buyers first", "managers first"])
def test_a_high_kind_early_in_a_plan_holds_back_what_follows_it(order):
    # "a manager listing bid (6) then test_result (3) runs its test only after its bid, after every free action of
    # kinds 0-5 of everyone"
    plans = {"M-1": [a("bid", price=9, post_id="post-7"), on("test_result", C1)],
             "M-2": [a("withdraw_bid", **POST9)], "B-1": [a("post_task"), a("withdraw_post", post_id="post-8")]}
    assert phase_of(plans, order)[0] == [("B-1", 0), ("B-1", 1), ("M-2", 0), ("M-1", 0), ("M-1", 1)]
    # "a manager [bid, pay] and another manager [deliver] run deliver, bid, pay, in either turn order"
    plans = {"M-1": [a("bid", price=9, post_id="post-7"), a("pay", to="B-1", amount=1)], "M-2": [on("deliver", C5)]}
    assert phase_of(plans, order)[0] == [("M-2", 0), ("M-1", 0), ("M-1", 1)]


@pytest.mark.parametrize("order", [EVERYONE, EVERYONE[::-1]], ids=["buyers first", "managers first"])
def test_a_held_edge_pulls_the_winners_earlier_actions_ahead(order):
    plans = {"M-1": [a("bid", price=9, post_id="post-7"), on("deliver", C5)],
             "B-1": [on("end_contract", C5), a("post_task")], "B-2": [a("edit_post", post_id="post-7")]}
    placed, edges = phase_of(plans, order)
    assert placed == [("M-1", 0), ("M-1", 1), ("B-1", 0), ("B-1", 1), ("B-2", 0)]
    assert edges == {True: {("A1", ("M-1", 1), B)}, False: set()}


# rounds.md, 6.5: the cycles it lists, each in either turn order

BM = [("B-1", 0), ("B-1", 1), ("M-1", 0), ("M-1", 1)]
MB = BM[2:] + BM[:2]
ACCEPT1 = a("accept_bid", bid_id="bid-1")
CYCLES = [
    ("test, deliver against end", {"M-1": [on("test_result", C5), on("deliver", C5)], "B-1": [on("end_contract", C5)]},
     [M, ("M-1", 1), B], {("A1", ("M-1", 1), B)}, {("A5", B, M)}),
    ("delegate_run, deliver against end",
     {"M-1": [on("delegate_run", C5), on("deliver", C5)], "B-1": [on("end_contract", C5)]},
     [M, ("M-1", 1), B], {("A1", ("M-1", 1), B)}, {("A4", B, M)}),
    ("run_myself, submit against end",
     {"M-1": [on("run_myself", C3), on("deliver", C3)], "B-1": [on("end_contract", C3)]},
     [M, ("M-1", 1), B], {("A3", ("M-1", 1), B)}, {("A4", B, M)}),
    ("bid, test against accept", {"M-1": [a("bid", price=9, **POST9), on("test_result", C1)], "B-1": [ACCEPT1]},
     [B, M, ("M-1", 1)], {("A8", B, M)}, {("A10", ("M-1", 1), B)}),
    ("withdraw_bid, test against accept", {"M-1": [a("withdraw_bid", **POST9), a("test_result")], "B-1": [ACCEPT1]},
     [B, M, ("M-1", 1)], {("A9", B, M)}, {("A10", ("M-1", 1), B)}),
    ("bid, delegate_run against accept",
     {"M-1": [a("bid", price=9, **POST9), on("delegate_run", C2)], "B-1": [ACCEPT1]},
     [B, M, ("M-1", 1)], {("A8", B, M)}, {("A10", ("M-1", 1), B)}),
    ("withdraw_bid, run_myself against accept",
     {"M-1": [a("withdraw_bid", **POST9), on("run_myself", C2)], "B-1": [ACCEPT1]},
     [B, M, ("M-1", 1)], {("A9", B, M)}, {("A10", ("M-1", 1), B)}),
    ("bid, dispute against accept", {"M-1": [a("bid", price=9, **POST9), on("dispute", C3)], "B-1": [ACCEPT1]},
     [B, M, ("M-1", 1)], {("A8", B, M)}, {("A10", ("M-1", 1), B)}),
    ("end, accept against withdraw_bid, deliver",
     {"B-1": [on("end_contract", C5), ACCEPT1], "M-1": [a("withdraw_bid", **POST9), on("deliver", C5)]},
     MB, {("A1", ("M-1", 1), B)}, {("A9", ("B-1", 1), M)}),
    ("end, accept against bid, deliver",
     {"B-1": [on("end_contract", C5), ACCEPT1], "M-1": [a("bid", price=9, **POST9), on("deliver", C5)]},
     MB, {("A1", ("M-1", 1), B)}, {("A8", ("B-1", 1), M)}),
    ("end, approve against test, deliver",
     {"B-1": [on("end_contract", C5), on("approve_submission", C4)],
      "M-1": [on("test_result", C4), on("deliver", C5)]},
     MB, {("A1", ("M-1", 1), B)}, {("A7", ("B-1", 1), M)}),
    ("accept, approve against run_myself",
     {"B-1": [a("accept_bid", bid_id="bid-3"), on("approve_submission", C4)], "M-1": [on("run_myself", C4)]},
     [B, ("B-1", 1), M], {("A6", ("B-1", 1), M)}, {("A10", M, B)}),
    ("deliver, test against accept, keep",
     {"M-1": [on("deliver", C1), on("test_result", C2)], "B-1": [ACCEPT1, on("keep_delivery", C1)]},
     BM, {("B_keep", ("B-1", 1), M)}, {("A10", ("M-1", 1), B)}),
    ("deliver, deliver against end, keep",
     {"M-1": [on("deliver", C1), on("deliver", C5)], "B-1": [on("end_contract", C5), on("keep_delivery", C1)]},
     MB, {("A1", ("M-1", 1), B)}, {("B_keep", ("B-1", 1), M)}),
    # step 2's candidates: a ready node that only waits behind the cycle is not placed to break it
    ("a withdrawal waiting behind the cycle",
     {"B-1": [on("end_contract", C5), a("accept_bid", bid_id="bid-2")],
      "M-1": [on("test_result", C5), on("deliver", C5)], "M-2": [a("withdraw_bid", **POST9)]},
     MB + [N], {("A1", ("M-1", 1), B), ("A9", ("B-1", 1), N)}, {("A5", B, M)}),
    ("a bid waiting behind the cycle",
     {"B-1": [on("end_contract", C5), a("accept_bid", bid_id="bid-2")],
      "M-1": [on("run_myself", C5), on("deliver", C5)], "M-2": [a("bid", price=9, **POST9)]},
     MB + [N], {("A1", ("M-1", 1), B), ("A8", ("B-1", 1), N)}, {("A4", B, M)}),
    ("another manager's bid waiting behind the cycle",
     {"B-1": [on("end_contract", C5), a("accept_bid", bid_id="bid-3")],
      "M-1": [on("delegate_run", C5), on("deliver", C5)], "M-2": [a("bid", price=9, post_id="post-8")]},
     MB + [N], {("A1", ("M-1", 1), B), ("B_accept", ("B-1", 1), N), ("A10", M, ("B-1", 1))}, {("A4", B, M)}),
    # every blocking edge into the node placed is dropped, not only the strongest
    ("two blocking edges into the node placed",
     {"B-1": [on("end_contract", C5), on("approve_submission", C5)],
      "M-1": [on("test_result", C5), on("deliver", C5)]},
     MB, {("A1", ("M-1", 1), B)}, {("A5", B, M), ("A7", ("B-1", 1), M)}),
    ("a run waiting behind the cycle",
     {"B-1": [on("end_contract", C5), on("end_contract", C1)], "M-1": [on("test_result", C1), on("deliver", C5)],
      "M-2": [on("delegate_run", C5)]},
     [M, ("M-1", 1), B, ("B-1", 1), N], {("A1", ("M-1", 1), B), ("A4", B, N)}, {("A5", ("B-1", 1), M)}),
]


@pytest.mark.parametrize("plans,placed,held,dropped", [c[1:] for c in CYCLES], ids=[c[0] for c in CYCLES])
@pytest.mark.parametrize("order", [EVERYONE, EVERYONE[::-1], ("M-2", "B-1", "B-2", "M-1")],
                         ids=["buyers first", "managers first", "mixed"])
def test_cycle(order, plans, placed, held, dropped):
    assert phase_of(plans, order) == (placed, {True: held, False: dropped})


def ring(first, second):
    """A cycle through four participants: two ends (A1) each wait for a delivery, and each manager's earlier action
    waits for a later action of the other manager's buyer; `first` and `second` are those (buyer, manager) pairs."""
    return {"M-1": [first[1], on("deliver", C5)], "B-1": [on("end_contract", C5), second[0]],
            "M-2": [second[1], on("deliver", C1)], "B-2": [on("end_contract", C1), first[0]]}


@pytest.mark.parametrize("strong,weak,first,second", [
    ("A5", "A9", (on("end_contract", C2), on("test_result", C2)),
     (a("accept_bid", bid_id="bid-2"), a("withdraw_bid", **POST9))),
    ("A4", "A8", (on("end_contract", C2), on("delegate_run", C2)),
     (a("accept_bid", bid_id="bid-2"), a("bid", price=9, **POST9))),
    ("A8", "B_withdraw", (ACCEPT1, a("bid", price=9, **POST9)),
     (a("withdraw_post", post_id="post-8"), a("bid", price=9, post_id="post-8"))),
    ("A6", "B_withdraw", (on("approve_submission", C4), on("run_myself", C4)),
     (a("withdraw_post", **POST9), a("withdraw_bid", **POST9))),
])
@pytest.mark.parametrize("order", [EVERYONE, EVERYONE[::-1]], ids=["buyers first", "managers first"])
def test_the_weaker_group_gives_way_whatever_the_turn_order(order, strong, weak, first, second):
    """Rule iv over rule i over the B rules; rule iii over all of them."""
    placed, edges = phase_of(ring(first, second), order)
    assert placed[0] == N
    assert edges[False] == {(weak, ("B-1", 1), N)}
    assert sorted(rule for rule, _, _ in edges[True]) == ["A1", "A1", strong]


def test_equally_strong_rules_leave_it_to_the_smaller_key():
    """A5 is no weaker than A4 (nor A7 than A6): the candidate of the lower rank is placed."""
    for first, second in ((("end_contract", "delegate_run"), ("end_contract", "test_result")),
                          (("approve_submission", "test_result"), ("end_contract", "run_myself"))):
        plans = ring([on(name, C4) for name in first], [on(name, C3) for name in second])
        for order, winner in ((("B-1", "M-1", "B-2", "M-2"), "M-1"), (("B-1", "M-2", "B-2", "M-1"), "M-2")):
            placed, edges = phase_of(plans, order)
            assert placed[0] == (winner, 0)
            assert [second for _, _, second in edges[False]] == [(winner, 0)]
            assert len(edges[True]) == 3


# rounds.md, 6.6 to 6.8, 7 and 8: small phases run

OUT = "(this round M-1 went out of business before your acceptance ran, so no contract was made and nothing was charged)"


def ready():
    """small(), with contract-5 past its deadline day, and an arrived result on contract-5 and on contract-1."""
    m = small()
    m.contracts[C5].deadline = 4
    m.contracts[C5].results.append(Result("R-005", "employee", "fresh", None, 2, 4, 5.0))
    m.contracts[C1].results.append(Result("R-001", "employee", "fresh", None, 2, 4, 5.0))
    return m


def run(m, plans, order=EVERYONE, kind="first", start=None, hidden=None):
    start = start or copy.deepcopy(m)
    return phase.run_phase(m, kind, order, plans, start, hidden), start


def done(ph, pid):
    """Each action's outcome and result line."""
    return [(n.outcome, None if n.ok is None else actions.result_line(n.name, n.ok, n.message))
            for n in ph.plans[pid]]


def test_shadow():
    start = ready()
    s = phase.shadow(start, "M-1", (3.5, True))
    assert (s.participants["M-1"].balance, s.participants["M-1"].out_of_business) == (3.5, True)
    assert (start.participants["M-1"].balance, start.participants["M-1"].out_of_business) == (60.0, False)
    assert s.participants["M-2"] == start.participants["M-2"] and s.bids == start.bids and s.posts == start.posts
    s.bids["bid-1"].price = 1.0
    assert start.bids["bid-1"].price == 20.0
    s = phase.shadow(start, "M-1", (60.0, False), {"posts": ["post-7"], "bids": ["bid-2"]})
    assert list(s.posts) == [p for p in start.posts if p != "post-7"] and list(s.bids) == ["bid-1", "bid-3"]
    assert "post-7" in start.posts and "bid-2" in start.bids


def test_an_action_fails_on_the_state_its_turn_saw_before_it_reaches_the_market():
    """Its own balance after its token charges is on its shadow; an id another participant makes in the round is
    not, and the hint lists what the turn saw (rounds.md, 6.6, consequences)."""
    m = ready()
    m.tasks["task-10"] = Task("task-10", "B-2", 60, 12)
    start = copy.deepcopy(m)
    m.participants["M-1"].balance = 0.5
    ph, _ = run(m, {"B-2": [a("post_task", task_id="task-10")],
                    "M-1": [a("test_result", contract_id=C1, result="R-001"), a("bid", post_id="post-10", price=5)]},
                start=start)
    assert done(ph, "B-2") == [("ran", "- post_task: OK: posted task-10 as post-10")]
    assert done(ph, "M-1") == [
        ("refused", "- test_result: ERROR: insufficient balance for test_result: it costs 1, you have 0.50"),
        ("refused", "- bid: ERROR: no post with id 'post-10'. Open posts: post-7, post-8, post-9.")]
    assert m.posts["post-10"].closed is None and m.bids_created == 3 and m.participants["M-1"].balance == 0.5
    assert m.contracts[C1].results[0].manager_readings == [] and m.actor is None


def test_a_bid_made_in_the_phase_has_on_the_shadow_the_id_the_market_gave_it():
    m = ready()
    ph, _ = run(m, {"M-1": [a("bid", post_id="post-7", price=9), a("withdraw_bid", post_id="post-7")],
                    "M-2": [a("bid", post_id="post-8", price=5)]}, order=("M-2", "M-1", "B-1", "B-2"))
    assert [(n.pid, n.pos) for n in ph.order] == [("M-2", 0), ("M-1", 0), ("M-1", 1)]
    assert done(ph, "M-2") == [("ran", "- bid: OK: placed bid-4 on post-8 at 5")]
    assert done(ph, "M-1") == [
        ("ran", "- bid: OK: placed bid-5 on post-7 at 9"),
        ("ran", "- withdraw_bid: OK: withdrew bid-5 on post-7; bidding on post-7 again would place a new bid")]
    assert (m.bids["bid-4"].status, m.bids["bid-5"].status, m.bids_created) == ("open", "withdrawn", 5)


def test_implicit_targets_are_fixed_from_the_shadow():
    """rounds.md, 6.6, steps 1 and 3: a test that names no result and a payment that names no contract act on what
    the turn saw, whatever a delivery made earlier in the phase changed on the market."""
    later = a("edit_post", post_id="post-9", form="plain")  # kind 7: what follows it runs after the delivery
    m = ready()
    m.contracts[C1].results.append(Result("R-002", "manager", "fresh", None, 2, 4, 5.0))
    ph, _ = run(m, {"B-1": [later, a("test_delivery", contract_id=C1)],
                    "M-1": [a("deliver", contract_id=C1, result="R-002")]})
    assert [(n.pid, n.pos) for n in ph.order] == [("M-1", 0), ("B-1", 0), ("B-1", 1)]
    assert all(n.ok for n in ph.order) and state.in_use(m.contracts[C1]).label == "R-002"
    assert ph.plans["B-1"][1].message.startswith("R-001 reading: ")
    assert [len(r.buyer_readings) for r in m.contracts[C1].results] == [1, 0]

    m = ready()
    m.contracts[C1].deliveries.clear()
    ph, _ = run(m, {"B-1": [later, a("pay", to="M-1", amount=3)],
                    "M-1": [a("deliver", contract_id=C5, result="R-005")]})
    assert all(n.ok for n in ph.order) and m.contracts[C5].deliveries
    assert done(ph, "B-1")[1] == ("ran", "- pay: OK: paid 3 to M-1 for contract-1")
    assert (m.contracts[C1].paid, m.contracts[C5].paid) == (3.0, 0.0)


def test_an_action_that_failed_on_the_market_is_not_on_the_shadow_and_what_the_correction_is_told():
    """rounds.md, 6.5's cycle of an end and an acceptance against a withdrawal and a delivery, with a post_task
    that needed the end (8.2)."""
    m = ready()
    ph, start = run(m, {"B-1": [on("end_contract", C5), a("post_task", task_id="task-5"), ACCEPT1],
                        "M-1": [a("withdraw_bid", **POST9), a("deliver", contract_id=C5, result="R-005")]})
    assert [(n.pid, n.pos) for n in ph.order] == [("M-1", 0), ("M-1", 1), ("B-1", 0), ("B-1", 1), ("B-1", 2)]
    assert [n.outcome for n in ph.plans["M-1"]] == ["ran", "ran"] and all(n.ok for n in ph.plans["M-1"])
    assert done(ph, "B-1") == [
        ("ran", "- end_contract: ERROR: contract-5 has a delivery: a plain contract can be ended only after its "
                "deadline day with nothing delivered, if it was signed on or before that day"),
        ("refused", "- post_task: ERROR: task-5 is already contracted"),
        ("ran", "- accept_bid: ERROR: bid-1 is withdrawn")]
    r = phase.review(m, ph, "B-1", start)
    assert r.lost == [(0, "A1", ("M-1", 1))]
    assert r.notes == {
        0: "(this round M-1 delivered on contract-5 before your end_contract ran: a delivery made in the same round "
           "as an end counts; the delivery is among the news below)",
        1: "(this needed your end_contract above, which failed)",
        2: "(it was possible on the market as your turn saw it, but another participant's action earlier in this "
           "round changed that)"}
    assert (r.facts, r.shown) == ([], [])


def test_a_participant_that_goes_out_of_business_is_stopped():
    """rounds.md, 6.7; the acceptance of its bid loses by A10 with no winner, with the open-bids fact (8.2, 8.3)."""
    m = ready()
    m.participants["M-1"].balance = 1.0
    ph, start = run(m, {"M-1": [a("test_result", contract_id=C1, result="R-001"),
                                a("deliver", contract_id=C5, result="R-005"), a("pay", to="B-1", amount=1)],
                        "B-1": [ACCEPT1]})
    assert [(n.pid, n.pos) for n in ph.order] == [("M-1", 0), ("M-1", 1), ("M-1", 2), ("B-1", 0)]
    assert [(n.outcome, n.ok) for n in ph.plans["M-1"]] == [("ran", True), ("stopped", None), ("stopped", None)]
    assert done(ph, "M-1")[1:] == [("stopped", None)] * 2
    assert done(ph, "B-1") == [("ran", "- accept_bid: ERROR: M-1 is out of business and cannot take contracts")]
    assert m.participants["M-1"].out_of_business and not m.contracts[C5].deliveries
    assert m.counts == {"out_of_business_events": 1, "manager_actions": 1, "buyer_actions": 1,
                        "buyer_failed_actions": 1}
    assert [(e.rule, e.held, e.first.ok, e.second.ok) for e in ph.edges] == [("A10", True, True, False)]
    r = phase.review(m, ph, "B-1", start)
    assert (r.lost, r.notes) == ([(0, "A10", None)], {0: OUT})
    assert r.facts == ["Open bids on post-9 now: bid-2 by M-2 at 18 with threshold 5 (escrow)."]
    assert r.shown == ["bid-1", "bid-2"]


@pytest.mark.parametrize("first,line", [
    (a("bid", post_id="post-7", price=9),
     "- bid: ERROR: you are out of business (balance at or below 0) and cannot act"),
    (a("frobnicate"), "- frobnicate: ERROR: unknown action 'frobnicate'. Your actions: send_message, pay, bid, "
                      "delegate_run, run_myself, test_result, deliver, dispute, withdraw_bid."),
    (a(None), '- ?: ERROR: the action names no action: give its name with "action", as {"action": "<action name>", '
              '"<argument name>": <value>}'),
])
def test_a_manager_out_of_business_by_its_tokens_fails_its_first_action_and_is_stopped(first, line):
    """rounds.md, 6.6, consequences; the facts about the posts of the failed acceptances, in the words of the
    post's buyer and of another buyer (8.3)."""
    m = ready()
    m.bids["bid-4"] = Bid("bid-4", "post-7", "M-1", 9.0)
    m.bids["bid-5"] = Bid("bid-5", "post-1", "M-2", 20.0, status="accepted")
    start = copy.deepcopy(m)
    m.participants["M-1"].balance, m.participants["M-1"].out_of_business = -0.01, True
    ph, _ = run(m, {"M-1": [first, a("pay", to="B-1", amount=1)],
                    "B-1": [a("withdraw_post", post_id="post-8"), a("accept_bid", bid_id="bid-3"),
                            a("accept_bid", bid_id="bid-5"), ACCEPT1],
                    "B-2": [a("accept_bid", bid_id="bid-3"), a("accept_bid", bid_id="bid-4"),
                            a("accept_bid", bid_id="bid-5"), a("accept_bid", bid_id="bid-2")]}, start=start)
    assert done(ph, "M-1") == [("refused", line), ("stopped", None)]
    assert done(ph, "B-1") == [
        ("ran", "- withdraw_post: OK: withdrew post-8: task-8 leaves the market"),
        ("refused", "- accept_bid: ERROR: post-8 is already closed"),
        ("refused", "- accept_bid: ERROR: post-1 is already closed"),
        ("ran", "- accept_bid: ERROR: M-1 is out of business and cannot take contracts")]
    r = phase.review(m, ph, "B-1", start)
    assert (r.lost, r.notes) == ([(3, "A10", None)], {3: OUT})
    assert r.facts == ["post-8 closed earlier this round, when you withdrew it.",
                       "post-1 closed on day 1, round 3, when a bid on it was accepted.",
                       "Open bids on post-9 now: bid-2 by M-2 at 18 with threshold 5 (escrow)."]
    assert r.shown == ["bid-1", "bid-2"]
    assert [n.outcome for n in ph.plans["B-2"]] == ["refused", "ran", "refused", "refused"]
    r = phase.review(m, ph, "B-2", start)
    assert (r.lost, r.notes) == ([(1, "A10", None)], {1: OUT})
    assert r.facts == ["post-8 closed earlier this round, when its buyer withdrew it.",
                       "Open bids on post-7 now: no bids yet.",
                       "post-1 closed on day 1, round 3, when its buyer accepted a bid.",
                       "Open bids on post-9 now: sealed (only the buyer sees them)."]
    assert r.shown == ["bid-4", "bid-1", "bid-2"]


def test_what_the_log_records_for_a_phase():
    """rounds.md, 6.1 and 6.8: a trial logs nothing; an action's own records come before its action record."""
    m, records = ready(), []
    m.log = records.append
    start = copy.deepcopy(m)
    plans = {"B-2": [], "B-1": [on("end_contract", C5)],
             "M-1": [a("deliver", contract_id=C5, result="R-005"), a("bid", post_id="post-99", price=1)]}
    phase.run_phase(m, "first", EVERYONE, plans, start)
    assert [r["type"] for r in records] == ["phase", "delivery", "notification"] + ["action"] * 3 + ["clash"]
    assert [r["n"] for r in records] == list(range(1, 8)) and start.log is None
    assert records[0]["phase"] == "first"
    assert records[0]["order"] == [("M-1", 0, "deliver"), ("B-1", 0, "end_contract"), ("M-1", 1, "bid")]
    done = [{k: r[k] for k in ("participant", "role", "phase", "number", "name", "arguments", "status", "outcome")}
            for r in records if r["type"] == "action"]
    assert done == [
        {"participant": "M-1", "role": "manager", "phase": "first", "number": 3, "name": "deliver",
         "arguments": {"contract_id": C5, "result": "R-005"}, "status": "OK", "outcome": "ran"},
        {"participant": "B-1", "role": "buyer", "phase": "first", "number": 1, "name": "end_contract",
         "arguments": {"contract_id": C5}, "status": "ERROR", "outcome": "ran"},
        {"participant": "M-1", "role": "manager", "phase": "first", "number": 3, "name": "bid",
         "arguments": {"post_id": "post-99", "price": 1}, "status": "ERROR", "outcome": "refused"}]
    assert records[5]["result"] == "no post with id 'post-99'. Open posts: post-7, post-8, post-9."
    assert {k: v for k, v in records[6].items() if k not in ("n", "type", "day", "round")} == {
        "phase": "first", "rule": "A1", "first": ("M-1", 0, "deliver"), "second": ("B-1", 0, "end_contract"),
        "first_status": "OK", "second_status": "ERROR", "held": True}
    assert m.counts == {"manager_actions": 2, "manager_failed_actions": 1, "buyer_actions": 1,
                        "buyer_failed_actions": 1}
    # a phase with no action logs nothing, and its participants still get their reply numbers
    ph = phase.run_phase(m, "correction", EVERYONE, {"B-1": [], "M-2": []}, start)
    assert (len(records), m.replies, ph.order, ph.edges) == (7, 5, [], [])


def test_money_from_others_lands_at_the_rounds_end():
    """rounds.md, 7: "with 0.50 credits, paid 10 by its buyer in the same phase, its test_result fails"."""
    m = ready()
    m.participants["M-1"].balance = 0.5
    ph, _ = run(m, {"B-1": [a("pay", to="M-1", amount=10), on("approve_submission", C4)],
                    "M-1": [a("test_result", contract_id=C1, result="R-001")]})
    assert [n.ok for n in ph.plans["B-1"]] == [True, True]
    assert done(ph, "B-1")[0] == ("ran", "- pay: OK: paid 10 to M-1 for contract-1")
    assert done(ph, "M-1") == [
        ("refused", "- test_result: ERROR: insufficient balance for test_result: it costs 1, you have 0.50")]
    assert m.deferred == [Credit("M-1", 10.0, "transfer", "B-1"), Credit("M-1", 20.0, "escrow_release", "B-1")]
    assert (m.participants["M-1"].balance, m.participants["B-1"].balance) == (0.5, 50.0)
    assert m.participants["M-1"].ledger == [("payments received", 10.0), ("escrow releases", 20.0)]
    assert len(m.participants["M-1"].pending) == 2 and m.contracts[C1].paid == 10.0
    state.land_credits(m)
    assert (m.participants["M-1"].balance, m.deferred) == (30.5, [])


def test_the_ids_hidden_from_a_correction_and_its_shadow():
    """rounds.md, 8.6."""
    m = ready()
    round_ids = set(m.posts) | set(m.bids)
    m.tasks["task-10"] = Task("task-10", "B-2", 60, 12, "posted", "post-10")
    m.posts["post-10"] = Post("post-10", "task-10", "B-2", 5, 1)
    m.bids["bid-4"] = Bid("bid-4", "post-8", "M-2", 9.0)
    m.bids["bid-5"] = Bid("bid-5", "post-7", "M-1", 9.0)
    m.bids["bid-6"] = Bid("bid-6", "post-10", "M-1", 9.0)
    m.bids_created = 6
    assert phase.hidden(m, "B-1", round_ids, []) == {"posts": ["post-10"], "bids": ["bid-4", "bid-5", "bid-6"]}
    assert phase.hidden(m, "B-1", round_ids, ["bid-4", "bid-6"]) == {"posts": ["post-10"], "bids": ["bid-5", "bid-6"]}
    assert phase.hidden(m, "M-1", round_ids, []) == {"posts": ["post-10"], "bids": ["bid-4", "bid-6"]}
    assert phase.hidden(m, "M-2", round_ids, []) == {"posts": ["post-10"], "bids": ["bid-5", "bid-6"]}
    assert phase.hidden(m, "B-2", round_ids, []) == {"posts": [], "bids": ["bid-4", "bid-5", "bid-6"]}

    plans = {"B-1": [a("accept_bid", bid_id="bid-4")],
             "M-2": [a("bid", post_id="post-10", price=5), a("bid", post_id="post-8", price=7)]}
    for shown, accepted in (([], False), (["bid-4"], True)):
        market = copy.deepcopy(m)
        hidden = {pid: phase.hidden(market, pid, round_ids, shown if pid == "B-1" else []) for pid in plans}
        ph, _ = run(market, plans, kind="correction", hidden=hidden)
        if accepted:
            assert done(ph, "B-1") == [("ran", "- accept_bid: OK: created contract-8 with M-2 at price 9")]
            second = ("ran", "- bid: ERROR: post-8 closed earlier this round, when its buyer accepted a bid")
        else:
            assert done(ph, "B-1") == [
                ("refused", "- accept_bid: ERROR: no bid with id 'bid-4'. Open bids on your posts: bid-1 (M-1 at 20 "
                            "on post-9), bid-2 (M-2 at 18 with threshold 5 (escrow) on post-9), bid-3 (M-1 at 15 on "
                            "post-8).")]
            second = ("ran", "- bid: OK: updated bid-4 on post-8 at 7")
        assert done(ph, "M-2") == [
            ("refused", "- bid: ERROR: no post with id 'post-10'. Open posts: post-7, post-8, post-9."), second]
