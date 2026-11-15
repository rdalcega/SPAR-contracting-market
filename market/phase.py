"""One action phase (spec/rounds.md, 6 to 8): the order its actions run in, each action run on the state its turn
saw and then on the market, and what a correction is told about the actions that failed."""

import copy
from dataclasses import dataclass

from . import actions, state, view_contracts
from .fmt import CREDITS, F

KINDS = {  # rounds.md, 6.4 (actions-buyer.md, 12; actions-manager.md, 9); every other name is kind 0
    "deliver": 1,
    "approve_submission": 2, "reject_submission": 2, "end_contract": 2,
    "delegate_run": 3, "run_myself": 3, "test_result": 3, "dispute": 3,
    "accept_bid": 4, "withdraw_post": 4,
    "withdraw_bid": 5,
    "bid": 6,
    "edit_post": 7,
}
ON_CONTRACT = (  # rounds.md, 6.2: the actions whose target is the contract `contract_id` names
    "deliver", "end_contract", "delegate_run", "run_myself", "test_result", "dispute", "keep_delivery",
    "approve_submission", "reject_submission",
)
RUNS = ("delegate_run", "run_myself")
STRENGTH = {  # rounds.md, 6.5 step 2: the five groups of the clash rules, 0 the strongest
    "A1": 0, "A2": 0, "A3": 0,
    "A4": 1, "A5": 1, "A6": 1, "A7": 1,
    "A8": 2, "A9": 2,
    "B_accept": 3, "B_withdraw": 3, "B_keep": 3,
    "A10": 4,
}
NEEDED = "(this needed your {tool} above, which failed)"  # rounds.md, 8.2
POSSIBLE = ("(it was possible on the market as your turn saw it, but another participant's action earlier in this "
            "round changed that)")
DELIVERED = ("(this round {who} delivered on {c} before your end_contract ran: a delivery made in the same round as "
             "an end counts; the delivery is among the news below)")
SUBMITTED = ("(this round {who} submitted a result for your review on {c} before your end_contract ran: a submission "
             "made in the same round as an end counts; it is among the news below, with the day it is released if "
             "you neither approve nor reject it)")
ENDED = ("(this round {who} ended {c} before your {tool} ran: an end comes before the manager's runs and tests on "
         "that contract, so your {tool} did not happen and nothing was charged; {c} is ended and takes no more "
         "runs, tests or deliveries)")
APPROVED = ("(this round {who} approved the submission under review on {c} before your {tool} ran: an approval "
            "comes before the manager's runs and tests on that contract, so your {tool} did not happen and nothing "
            "was charged; {c} is closed, and the price released to you arrives at the end of the round)")
CLASH_NOTES = {  # rounds.md, 8.2: the clash note of each rule
    "A1": DELIVERED, "A2": SUBMITTED, "A3": SUBMITTED, "A4": ENDED, "A5": ENDED, "A6": APPROVED, "A7": APPROVED,
    "A8": ("(this round {who} accepted your bid {bid_id} on {post} before your new bid ran: an acceptance binds a "
           "bid as its buyer saw it, even one its manager replaces in the same round, so you hold {terms}, and "
           "your new terms did not apply)"),
    "A9": ("(this round {who} accepted your bid {bid_id} on {post} before your withdrawal ran: an acceptance binds "
           "a bid as its buyer saw it, even one its manager withdraws in the same round, so you hold {terms})"),
    "A10": ("(this round {manager} went out of business before your acceptance ran, so no contract was made and "
            "nothing was charged)"),
    "B_accept": "(this round {who} accepted another bid on {post} before your {tool} ran)",
    "B_withdraw": "(this round {who} withdrew {post} before your {tool} ran)",
    "B_keep": POSSIBLE,
}


@dataclass(eq=False)
class Node:
    """One planned action of a phase (rounds.md, 6.5): its participant, its position in the plan, 0 first."""

    pid: str
    pos: int
    name: object  # the name as sent: a string, or None (the kind name is then "": rounds.md, 1)
    args: dict
    own: bool  # rounds.md, 6.2: one of its participant's own actions
    kind: int  # rounds.md, 6.4
    targets: dict  # rounds.md, 6.2
    number: int  # rounds.md, 6.1: its participant's reply number
    at: int = None  # its place in the run order
    outcome: str = None  # rounds.md, 6.6, 6.7: ran, refused or stopped
    ok: bool = None  # None when stopped
    message: str = None


@dataclass(eq=False)
class Edge:
    """A clash edge: `first` must run before `second` (rounds.md, 6.3); not held once dropped in a cycle (6.5)."""

    rule: str
    first: Node
    second: Node
    held: bool = True


@dataclass
class Phase:
    """A phase as it ran (rounds.md, 6.8)."""

    kind: str  # "first" or "correction"
    plans: dict  # pid -> its nodes in plan order
    order: list  # every node in run order, stopped ones included
    edges: list  # in the order of 6.8
    own: dict  # pid -> (balance, out_of_business) as the phase started (6.6)


@dataclass
class Review:
    """What a participant's correction is told about its first phase (rounds.md, 8.2, 8.3)."""

    lost: list  # its lost clashes: (position, rule, (the winner's participant, its position) or None)
    notes: dict  # position -> the note appended to that action's result line
    facts: list  # the fact lines
    shown: list  # the ids of the bids shown to it (8.6)


def classify(role, mode, name):
    """Whether an action is its participant's own (rounds.md, 6.2), and its kind value (6.4)."""
    own = name in actions.own(role, mode)
    return own, KINDS.get(name, 0) if own else 0


def targets(m, pid, name, args):
    """What an action acts on, each id looked up on the market as the phase starts (rounds.md, 6.2)."""
    if name not in actions.own(m.participants[pid].role, m.cfg["message_channels"]):
        return {}
    if name in ON_CONTRACT:
        c = actions.named(m, args, "contract")
        if c is None:
            return {}
        if name != "end_contract":
            return {"contract": c.id}
        last = state.latest(c)
        return {"contract": c.id, "escrow": c.form == "escrow",
                "rejected": last is not None and last.outcome == "rejected"}
    if name == "accept_bid":
        b = actions.named(m, args, "bid")
        return {} if b is None else {"bid": b.id, "post": b.post, "manager": b.manager}
    if name == "withdraw_bid":
        b = actions.withdraw_target(m, pid, args)
        return {} if b is None else {"bid": b.id, "post": b.post}
    if name in ("bid", "withdraw_post"):
        post = actions.named(m, args, "post")
        return {} if post is None else {"post": post.id}
    return {}


def _rule(x, y):
    """The first clash rule that makes x run before y, or None (rounds.md, 6.3)."""
    if not (x.own and y.own):
        return None
    a, b, s, t = x.name, y.name, x.targets, y.targets
    same = "contract" in s and s["contract"] == t.get("contract")
    if a == "deliver" and b == "end_contract" and same:
        return "A1" if not t["escrow"] else "A3" if t["rejected"] else "A2"
    if a == "end_contract" and same and b in RUNS:
        return "A4"
    if a == "end_contract" and same and b == "test_result":
        return "A5"
    if a == "approve_submission" and same and b in RUNS:
        return "A6"
    if a == "approve_submission" and same and b == "test_result":
        return "A7"
    if a == "keep_delivery" and b == "deliver" and same:
        return "B_keep"
    if a == "accept_bid" and "post" in s:
        if b == "bid" and t.get("post") == s["post"]:
            return "A8" if y.pid == s["manager"] else "B_accept"
        if b == "withdraw_bid" and t.get("bid") == s["bid"]:
            return "A9"
        if b == "withdraw_bid" and t.get("post") == s["post"]:
            return "B_accept"
    if a == "withdraw_post" and "post" in s and b in ("bid", "withdraw_bid") and t.get("post") == s["post"]:
        return "B_withdraw"
    if a in RUNS + ("test_result", "dispute") and b == "accept_bid" and t.get("manager") == x.pid:
        return "A10"
    return None


def clashes(nodes):
    """The clash edges between the actions of different participants (rounds.md, 6.3).

    They come in the order of the nodes, by first action and then by second: 6.8's order when the nodes are given
    by their participant's rank, then by position.
    """
    return [Edge(rule, x, y) for x in nodes for y in nodes
            if x.pid != y.pid and (rule := _rule(x, y)) is not None]


def _reach(start, arcs):
    """The nodes that can be reached from a node along the arcs, the node itself only through a cycle."""
    seen, todo = set(), [start]
    while todo:
        for n in arcs[todo.pop()]:
            if n not in seen:
                seen.add(n)
                todo.append(n)
    return seen


def place(nodes, edges, rank):
    """The order the phase's actions run in (rounds.md, 6.5); `rank` is each participant's place in the turn order.

    Sets each node's `at`, and `held` to False on every edge dropped in a cycle.
    """
    def key(n):
        return n.kind, rank[n.pid], n.pos

    order, done = [], dict.fromkeys(rank, 0)  # done: how many of a participant's actions are placed
    while len(order) < len(nodes):
        ready = [n for n in nodes if n.at is None and n.pos == done[n.pid]]
        blocking = {n: [e for e in edges if e.second is n and e.first.at is None] for n in ready}
        free = [n for n in ready if not blocking[n]]
        if free:
            chosen = min(free, key=key)
        else:
            left = [n for n in nodes if n.at is None]
            after = {n: [o for o in left if o.pid == n.pid and o.pos == n.pos + 1] for n in left}
            before = {n: [o for o in left if o.pid == n.pid and o.pos == n.pos - 1] for n in left}
            for e in edges:
                if e.first.at is None and e.second.at is None:
                    after[e.first].append(e.second)
                    before[e.second].append(e.first)
            candidates = [n for n in ready if _reach(n, before) <= _reach(n, after)]
            chosen = min(candidates, key=lambda n: (-min(STRENGTH[e.rule] for e in blocking[n]), key(n)))
            for e in blocking[chosen]:
                e.held = False
        chosen.at = len(order)
        order.append(chosen)
        done[chosen.pid] += 1
    return order


def shadow(start, pid, own, hidden=None):
    """The state a participant's turn saw (rounds.md, 6.6): the phase's start state with its own balance and
    status as the phase starts, less the ids hidden from its correction (8.6)."""
    s = copy.deepcopy(start)
    s.participants[pid].balance, s.participants[pid].out_of_business = own
    for kind in ("posts", "bids"):
        for ident in (hidden or {}).get(kind, ()):
            getattr(s, kind).pop(ident, None)
    return s


def _status(n):
    """An action's status as the log records it (rounds.md, 6.8): none for an action that did not run."""
    return None if n.ok is None else "OK" if n.ok else "ERROR"


def run_phase(m, kind, order, plans, start, hidden=None):
    """One action phase on the market (rounds.md, 6 and 7; 8.6 for the correction's).

    `plans` is {participant: [(name, arguments), ...]}, `order` the round's turn order, `start` the phase's start
    state, which the shadows are made from, and `hidden` {participant: the ids its correction's shadow lacks}.
    """
    mode, nodes = m.cfg["message_channels"], []
    for pid in order:
        if pid in plans:
            m.replies += 1  # 6.1
            for pos, (name, args) in enumerate(plans[pid]):
                nodes.append(Node(pid, pos, name, args, *classify(m.participants[pid].role, mode, name),
                                  targets(m, pid, name, args), m.replies))
    edges = clashes(nodes)
    placed = place(nodes, edges, {pid: rank for rank, pid in enumerate(order)})
    ph = Phase(kind, {pid: [n for n in nodes if n.pid == pid] for pid in plans}, placed, edges,
               {pid: (m.participants[pid].balance, m.participants[pid].out_of_business) for pid in plans})
    if nodes:
        state.emit(m, "phase", phase=kind, order=[(n.pid, n.pos, n.name or "") for n in placed])
    shadows = {pid: shadow(start, pid, ph.own[pid], (hidden or {}).get(pid)) for pid in plans if plans[pid]}
    stopped = set()
    for n in placed:
        if n.pid in stopped:  # 6.7
            n.outcome = "stopped"
            continue
        p = m.participants[n.pid]
        last = n is ph.plans[n.pid][-1]  # its shadow is not read again: the trial needs no copy of it
        trial = shadows[n.pid] if last else copy.deepcopy(shadows[n.pid])
        trial.bids_created = m.bids_created  # 6.6, step 4: a bid it creates has the id the market gives it
        fixed = actions.implicit(trial, n.pid, n.name, n.args)
        n.ok, n.message = actions.run(trial, n.pid, n.name, n.args, fixed)
        n.outcome = "refused"
        if n.ok:
            n.ok, n.message = actions.run(m, n.pid, n.name, n.args, fixed)
            n.outcome = "ran"
            if n.ok:
                shadows[n.pid] = trial
        state.emit(m, "action", participant=n.pid, role=p.role, phase=kind, number=n.number, name=n.name,
                   arguments=n.args, status=_status(n), result=n.message, outcome=n.outcome)
        state.count(m, p.role + "_actions")
        if not n.ok:
            state.count(m, p.role + "_failed_actions")
        if p.out_of_business:
            stopped.add(n.pid)
    for e in edges:  # 6.8
        state.emit(m, "clash", phase=kind, rule=e.rule, first=(e.first.pid, e.first.pos, e.first.name or ""),
                   second=(e.second.pid, e.second.pos, e.second.name or ""), first_status=_status(e.first),
                   second_status=_status(e.second), held=e.held)
    return ph


def _lost(m, ph, n):
    """The clash a failed action lost, as (rule, the winning action or None), or None (rounds.md, 8.2)."""
    if n.outcome != "ran" or n.ok:
        return None
    if n.name == "accept_bid" and "manager" in n.targets and m.participants[n.targets["manager"]].out_of_business:
        return "A10", None
    won = [e for e in ph.edges if e.second is n and e.held and e.first.ok and e.first.at < n.at]
    e = min(won, key=lambda e: e.first.at, default=None)
    return e and (e.rule, e.first)


def _clash_note(m, n, rule, winner):
    """The clash note of a lost clash (rounds.md, 8.2)."""
    post, bid = n.targets.get("post"), None
    if rule == "A9":
        bid = m.bids[n.targets["bid"]]
    elif rule == "A8":
        bid = next((b for b in m.bids.values() if (b.post, b.manager, b.status) == (post, n.pid, "accepted")), None)
    c = bid and next((c for c in m.contracts.values() if c.post == bid.post), None)
    terms = f"{c.id} at {CREDITS(bid.price)}{actions.terms(bid.threshold)}" if c else "the contract"
    return CLASH_NOTES[rule].format(who=winner and winner.pid, c=n.targets.get("contract"), tool=n.name, post=post,
                                    bid_id=bid.id if bid else "", terms=terms, manager=n.targets.get("manager"))


def _alone(ph, pid, start):
    """Whether each action of a plan succeeds when the whole plan runs alone, in order, on its shadow as the phase
    started (rounds.md, 8.2)."""
    s = shadow(start, pid, ph.own[pid])
    return [actions.run(s, pid, n.name, n.args)[0] for n in ph.plans[pid]]


def _post_fact(m, pid, post):
    """The fact line about the post of a failed acceptance (rounds.md, 8.3)."""
    if post.closed is None:
        return f"Open bids on {post.id} now: {view_contracts.bids_field(m, post, pid)}."
    return actions.closed_post(m, post, yours=pid == post.buyer) + "."


def review(m, ph, pid, start):
    """What a participant's correction is told, right after the first phase: its lost clashes, the notes of its
    failed actions, the facts, and the bids the facts showed it (rounds.md, 8.2, 8.3)."""
    r = Review([], {}, [], [])
    alone, tool = None, None  # tool: the kind name of the latest action so far that lost a clash
    for n in ph.plans[pid]:
        found = _lost(m, ph, n)
        if found:
            rule, winner = found
            r.lost.append((n.pos, rule, winner and (winner.pid, winner.pos)))
            r.notes[n.pos] = _clash_note(m, n, rule, winner)
            tool = n.name
        elif n.outcome == "ran" and not n.ok:
            r.notes[n.pos] = POSSIBLE
        elif n.outcome == "refused" and tool is not None:
            alone = alone or _alone(ph, pid, start)
            if alone[n.pos]:
                r.notes[n.pos] = NEEDED.format(tool=tool)
    rules = {rule for _, rule, _ in r.lost}
    if "A10" in rules:
        failed = [n.targets["post"] for n in ph.plans[pid]
                  if n.name == "accept_bid" and n.ok is False and "post" in n.targets]
        for post_id in dict.fromkeys(failed):
            r.facts.append(_post_fact(m, pid, m.posts[post_id]))
            if m.posts[post_id].closed is None:
                r.shown += [b.id for b in state.bids_on(m, post_id)]
    if rules & {"A4", "A5", "A6", "A7"}:
        r.facts.append(f"Your balance now: {F(m.participants[pid].balance, 2)} credits (money others pay you this "
                       f"round arrives at its end). {view_contracts.capacity_line(m, pid)}")
    return r


def hidden(m, pid, round_ids, shown):
    """The ids a participant's correction shadow lacks (rounds.md, 8.6): `round_ids` holds the ids of the posts and
    bids that existed when the round-start state was kept, `shown` the bids its facts showed it."""
    posts = [p.id for p in m.posts.values() if p.id not in round_ids and p.buyer != pid]
    bids = [b.id for b in m.bids.values()
            if b.post in posts or (b.id not in round_ids and b.manager != pid and b.id not in shown)]
    return {"posts": posts, "bids": bids}
