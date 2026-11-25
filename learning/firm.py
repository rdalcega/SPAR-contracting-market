"""What one firm may see of an episode, and its money by contract.

A firm is one seat (a role and a seat index) whose instructions persist across episodes. Its participant ID changes
every seed. Everything a digest shows about a firm comes through `firm_data`, which copies only the fields listed in
VISIBLE below. This is the information firewall: a field that is not listed never reaches a digest.

The rules, and why:
- A firm sees its own turns: its reasoning, visible reply text, memory writes, actions and their results, and the
  notifications sent to it. These are what the agent itself saw or wrote.
- It sees the public facts of its own contracts (form, price, threshold, days, outcome, deliveries with their message
  and stated quality, decisions, disputes, the arbiter's reading, which both parties are told) and the payments it made
  or received.
- A manager sees its own runs (worker, kind, parent, cost, days) and its own test readings. It never sees a result's
  true quality, which nobody in the market sees.
- A buyer sees its own test readings and, after the episode, the value each of its contracts realised. A buyer that
  used a result would know what it was worth. The value implies the true quality, which is accepted for buyers only.
- A firm sees the messages it sent or received, including group conversations it belongs to.
- After the episode, a firm sees its own score, its money by kind, how many days the market ran, and the number of
  tasks spawned (its own for a buyer, all for a manager), which normalises its score. It never sees other firms'
  scores, the last arrival day, the close reason, or any market-level metric.
"""

from dataclasses import dataclass, field

# The fields copied from each record type, per role. A record type that is absent here is never shown.
VISIBLE = {
    "contract": ("contract", "task", "post", "bid", "buyer", "manager", "form", "price", "threshold", "due",
                 "deadline", "attempt", "setup_fee", "day", "round"),
    "delivery": ("contract", "label", "number", "message", "stated_quality", "days_late", "day", "round"),
    "decision": ("contract", "label", "decision", "message", "day", "round"),
    "keep": ("contract", "label", "day", "round"),
    "release": ("contract", "label", "how", "to_manager", "refunded", "days_late", "day", "round"),
    "auto_release": ("contract", "label", "submitted", "day", "round"),
    "dispute": ("contract", "label", "deposit", "ruling_day", "day", "round"),
    "ruling": ("contract", "label", "reading", "threshold", "winner", "at_close", "deposit", "deposit_fate",
               "fee_owed", "fee_charged", "day", "round"),
    "fee_paid": ("contract", "label", "fee", "day", "round"),
    "end": ("contract", "rule", "refund", "day", "round"),
    "lateness_close": ("contract", "refunded", "day", "round"),
    "refund_at_close": ("contract", "refunded", "day", "round"),
    "payment": ("payer", "payee", "amount", "contract", "to_price", "beyond", "message", "day", "round"),
    "bid": ("bid", "post", "manager", "price", "threshold", "form", "verb", "day", "round"),
    "bid_withdrawn": ("bid", "post", "manager", "day", "round"),
    "post": ("post", "task", "buyer", "form", "threshold", "day", "round"),
    "post_edited": ("post", "form", "threshold", "day", "round"),
    "post_withdrawn": ("post", "task", "day", "round"),
    # a manager's own runs: never the quality or the variates
    "run": ("contract", "label", "worker", "kind", "parent", "cost", "days", "arrival", "day", "round"),
    "arrival": ("contract", "label", "worker", "kind", "parent", "day", "round"),
    # readings: only the firm's own side, filtered below
    "reading": ("contract", "label", "side", "i", "reading", "day", "round"),
    "task": ("task", "buyer", "worth", "D", "due", "budget", "day", "round"),
    "money": ("kind", "amount", "balance", "deferred", "held", "day", "round"),
    "message": ("sender", "to", "text", "day", "round"),
    "memory": ("text", "length", "cut", "from_correction", "day", "round"),
    "action": ("phase", "number", "name", "arguments", "status", "result", "outcome", "day", "round"),
    "notification": ("text", "day", "round"),
    "turn": ("balance", "day", "round"),
    "lost_turn": ("reason", "day", "round"),
}

# Fields that must never appear for the firm, whatever VISIBLE says (checked by the tests).
FORBIDDEN_FIELDS = ("quality", "settled_q", "z", "z_q", "z_t", "z_c", "last_arrival_day", "close_reason")

# Settlement fields a firm sees for its own contracts (running.md, 6.5); realized_value for buyers only.
SETTLEMENT_VISIBLE = ("contract", "form", "price", "threshold", "outcome", "deliveries", "receive_day", "days_late",
                      "paid", "paid_beyond_price", "refunded", "setup_fee", "signed_day", "due_day",
                      "deadline_day")


def _pick(record, fields):
    return {k: record.get(k) for k in fields if k in record}


@dataclass
class Call:
    """One model call of the firm's agent: when, which step, its reasoning and its visible reply text."""
    day: int
    round: int
    call: str
    step: int
    reasoning: str
    text: str
    n: int  # the record number, for ordering


@dataclass
class FirmData:
    pid: str
    role: str
    seat: int
    share: float
    start_balance: float
    budget: float | None
    score: float | None
    days: int | None  # the day the market closed (how many days it ran)
    tasks_spawned: int  # the normaliser: the buyer's own tasks, or all tasks for a manager
    own_tasks: dict = field(default_factory=dict)  # buyers: task id -> visible task fields
    contracts: dict = field(default_factory=dict)  # contract id -> visible contract fields
    settlement: dict = field(default_factory=dict)  # contract id -> visible settlement fields
    events: dict = field(default_factory=dict)  # record type -> list of visible records
    calls: list = field(default_factory=list)  # Call, in order
    money: list = field(default_factory=list)  # the firm's own money records (visible fields)


def _party(record, pid, contracts):
    """Whether a contract-bound record belongs to a contract the firm is party to."""
    c = contracts.get(record.get("contract"))
    return c is not None and pid in (c.get("buyer"), c.get("manager"))


def _call(r):
    reply = r.get("reply") or {}
    return Call(day=r.get("day"), round=r.get("round"), call=r.get("call"), step=r.get("step"),
                reasoning=reply.get("reasoning") or "", text=reply.get("text") or "", n=r.get("n"))


def _message_reaches(record, pid, role):
    to = record.get("to")
    return record.get("sender") == pid or to == pid or to == role + "s" or to == "everyone"


def firm_data(ep, pid):
    """Everything the firm that held seat `pid` in this episode may see (the module docstring gives the rules)."""
    me = ep.participants[pid]
    role = me.role
    contracts_all = ep.contracts
    mine = {cid: c for cid, c in contracts_all.items() if pid in (c.get("buyer"), c.get("manager"))}
    my_posts = {r["post"] for r in ep.of("post") if r.get("buyer") == pid}  # a buyer's own posts

    def party(r):
        return _party(r, pid, contracts_all)

    def own(r):
        return r.get("participant") == pid

    # (record types, which records of them the firm sees)
    rules = [
        (("contract", "delivery", "decision", "keep", "release", "auto_release", "dispute", "ruling", "fee_paid",
          "end", "lateness_close", "refund_at_close"), party),
        (("payment",), lambda r: pid in (r.get("payer"), r.get("payee"))),
    ]
    if role == "manager":
        rules += [(("run", "arrival"), party), (("bid", "bid_withdrawn"), lambda r: r.get("manager") == pid)]
    else:
        rules += [(("post",), lambda r: r.get("buyer") == pid),
                  # edits, withdrawals and bids on its own posts: the buyer saw them in its views
                  (("post_edited", "post_withdrawn", "bid"), lambda r: r.get("post") in my_posts)]
    rules += [
        (("reading",), lambda r: party(r) and r.get("side") == role),
        (("message",), lambda r: _message_reaches(r, pid, role)),
        (("memory", "action", "turn", "lost_turn"), own),
        (("notification",), lambda r: r.get("to") == pid),
    ]
    events = {}
    for kinds, sees in rules:
        for kind in kinds:
            for r in ep.of(kind):
                if sees(r):
                    events.setdefault(kind, []).append(_pick(r, VISIBLE[kind]))

    money = [_pick(r, VISIBLE["money"]) for r in ep.of("money") if own(r)]
    calls = [_call(r) for r in ep.of("call") if own(r)]
    fields = SETTLEMENT_VISIBLE + (("realized_value",) if role == "buyer" else ())
    settlement = {cid: _pick(row, fields) for cid, row in ep.settlement.items() if cid in mine}

    own_tasks = {}
    if role == "buyer":
        own_tasks = {tid: _pick(t, VISIBLE["task"]) for tid, t in ep.tasks.items() if t.get("buyer") == pid}
    tasks_spawned = len(own_tasks) if role == "buyer" else len(ep.tasks)
    score_row = ep.scores.get(pid) or {}

    return FirmData(pid=pid, role=role, seat=me.seat, share=me.share, start_balance=me.start_balance,
                    budget=me.budget, score=score_row.get("score"), days=ep.close_day,
                    tasks_spawned=tasks_spawned, own_tasks=own_tasks,
                    contracts={cid: _pick(c, VISIBLE["contract"]) for cid, c in mine.items()},
                    settlement=settlement, events=events, calls=calls, money=money)


# --- money by contract --------------------------------------------------------------------------------------


def _test_cost(ep, side):
    tests = ep.config.get("tests") or {}
    return float(tests.get(f"{side}_cost", 1.0))


def contract_profit(ep, fd, cid):
    """The firm's profit on one contract, from its own side.

    Buyer: realised value - credits paid toward and beyond the price (plain) or released to the manager (escrow)
           - the setup fee - arbiter's fees paid on it - its own test readings on it.
    Manager: credits received on it (paid toward and beyond the price, or released) - its runs' costs on it - its own
           test readings on it - dispute deposits it lost on it.
    """
    def on(kind):
        return [r for r in fd.events.get(kind, []) if r.get("contract") == cid]

    def total(records, key):
        return sum(float(r.get(key) or 0.0) for r in records)

    s = fd.settlement.get(cid) or {}
    paid = total([s], "paid") + total([s], "paid_beyond_price")
    tests = len(on("reading")) * _test_cost(ep, fd.role)
    if fd.role == "buyer":
        arbiter = total(on("fee_paid"), "fee") + total(on("ruling"), "fee_charged")
        return total([s], "realized_value") - paid - total([s], "setup_fee") - arbiter - tests
    lost = total([r for r in on("ruling") if r.get("deposit_fate") == "lost"], "deposit")
    return paid - total(on("run"), "cost") - tests - lost


def money_by_kind(fd):
    """The firm's money movements summed by kind (positive in, negative out). Records marked `held` describe money
    already moved (a lost deposit) and are left out."""
    out = {}
    for r in fd.money:
        if r.get("held"):
            continue
        out[r["kind"]] = out.get(r["kind"], 0.0) + float(r["amount"])
    return out


def reconcile(ep, fd):
    """(score from the summary, score rebuilt from money and settlement, sum of contract profits, the rest).

    The rest is what no contract carries: token charges and payments not toward any of its contracts. The tests
    check that the rebuilt score equals the summary's and that contract profits plus the rest equal it too."""
    kinds = money_by_kind(fd)
    if fd.role == "buyer":
        value = sum(float(s.get("realized_value") or 0.0) for s in fd.settlement.values())
        rebuilt = sum(v for k, v in kinds.items() if k != "per-task budgets") + value
    else:
        rebuilt = sum(kinds.values())
    profits = sum(contract_profit(ep, fd, cid) for cid in fd.contracts)
    outside = kinds.get("tokens", 0.0)
    for p in fd.events.get("payment", []):
        if p.get("contract") not in fd.contracts:
            outside += float(p["amount"]) * (1 if p.get("payee") == fd.pid else -1)
    return fd.score, rebuilt, profits, outside
