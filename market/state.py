"""The market object: one value of plain data that every rule reads and changes, and the helpers all modules share.

Everything an episode's rules, texts, draws, scores and metrics depend on is in one `Market`: dataclasses and
builtin containers only, so `copy.deepcopy` gives the state a turn saw (rounds.md, 6.6) and `pickle` the checkpoint
(running.md, 5). Each dict is in creation order (seat order for the participants); a list is in the order its
items were made. Values the spec derives from other state are the functions at the end, not fields.

The log sink is the one thing a copy does not carry: `emit` writes nothing on a copy, so a trial on a shadow logs
nothing, while its counters, being part of the copy, give its draws the values the market's own run would get
(randomness.md, 1, items 4 and 6).
"""

import copy
import math
import re
from dataclasses import dataclass, field

from . import config
from .fmt import TEXT

OUT_OF_BUSINESS_NOTICE = "Your balance is at or below 0: you are out of business and cannot act."  # episode.md, 9.1
BACK_IN_BUSINESS_NOTICE = "Your balance is positive again: you are back in business."  # episode.md, 9.3
DEFERRED = {"payments received": "transfer", "escrow releases": "escrow_release"}  # rounds.md, 7: the two credits


@dataclass
class Memory:
    text: str  # turns.md 10.2: as written, cut to turns.memory_chars
    day: int  # turns.md 10.2; view.md 10: the day of the turn that wrote it
    round: int  # turns.md 10.2; view.md 10
    turn: int  # view.md 10: the writer's turn counter at the write (form 2 or form 3 of the title)
    from_correction: bool  # turns.md 10.2; view.md 10


@dataclass
class Participant:
    id: str  # episode.md 1.1: B-xxx or M-xxx
    role: str  # episode.md 1.1: "buyer" or "manager"
    seat: int  # episode.md 1.1; randomness.md 3: its index within its role
    share: float  # episode.md 1.3: its start balance and per-task budget follow from it (config.md 6.4)
    balance: float  # episode.md 1.3, 9; rounds.md 7
    out_of_business: bool = False  # episode.md 9: a status, not a test of the balance (rounds.md 2)
    ledger: list = field(default_factory=list)  # view.md 8: (category, amount) entries in the order of their events
    pending: list = field(default_factory=list)  # view.md 5.1: notification lines `[{when}] {text}` not yet shown
    turns: int = 0  # view.md 1, 10: the turn counter (views built, less those lost to a failed call)
    last_view: int = 0  # episode.md 5.1; rounds.md 10.1: round index of its latest view not lost to a failed call
    results: list | None = None  # turns.md 12; view.md 4.3: its last turn's results text, entry by entry
    memory: Memory | None = None  # turns.md 10.2
    seen: int = 0  # messaging.md 3.4: how many messages the market held when its last view was built
    unseen: list = field(default_factory=list)  # messaging.md 3.4: indexes below `seen` of messages still new to it
    prompt_logged: bool = False  # rounds.md 3 step 1; running.md 5: its system prompt is logged once


@dataclass
class Task:
    id: str  # episode.md 3.2: task-N
    buyer: str  # episode.md 3.2
    worth: int  # episode.md 3.2; processes.md 4
    due: int  # episode.md 3.2: arrival day + D; the due(c) of every contract on it (processes.md 3.1)
    state: str = "queued"  # actions-buyer.md 2 check 3: queued, posted, contracted or withdrawn
    post: str | None = None  # actions-buyer.md 2, 11: its current post
    contract: str | None = None  # actions-buyer.md 5, 11: its current contract
    ended: list = field(default_factory=list)  # actions-buyer.md 2, 11; view-contracts.md 4.2: contract ids, as ended


@dataclass
class Post:
    id: str  # actions-buyer.md 2: post-N or post-N-k
    task: str  # actions-buyer.md 2
    buyer: str  # actions-buyer.md 2
    day: int  # processes.md 8: with `round`, its round index; an edit never moves it
    round: int  # processes.md 8
    form: str | None = None  # actions-buyer.md 2, 3: "plain", "escrow" or None (no preferred form)
    threshold: float | None = None  # actions-buyer.md 2, 3, 1.11: only with form escrow
    closed: str | None = None  # actions-buyer.md 4, 5: None while open, else "accepted" or "withdrawn"
    closed_day: int | None = None  # actions-manager.md 2 check 2; rounds.md 8.3
    closed_round: int | None = None  # actions-manager.md 2 check 2; rounds.md 8.3


@dataclass
class Bid:
    id: str  # actions-manager.md 2: bid-N, kept by an update in place
    post: str  # actions-manager.md 2
    manager: str  # actions-manager.md 2
    price: float  # actions-manager.md 2: rounded to the cent
    threshold: float | None = None  # actions-manager.md 2: an escrow bid has one, a plain bid None
    status: str = "open"  # actions-buyer.md 4, 5; actions-manager.md 3: open, accepted, lost or withdrawn


@dataclass
class Result:
    label: str  # processes.md 1.1; randomness.md 3.9: unique within its contract only
    worker: str  # processes.md 1.1: "employee" or "manager"
    kind: str  # processes.md 1.1: "fresh" or "refine"
    parent: str | None  # processes.md 1.1: the label a refinement refines
    start: int  # actions-manager.md 4 effect 3: the day its run started
    arrival: int  # processes.md 1.4: start day + the drawn days
    q: float  # processes.md 1.1, 1.2: the true quality, unrounded, never shown
    manager_readings: list = field(default_factory=list)  # processes.md 2; randomness.md 3.10: in the order taken
    buyer_readings: list = field(default_factory=list)  # processes.md 2; randomness.md 3.10


@dataclass
class Dispute:
    day: int  # processes.md 6.6: the dispute day; the ruling day is the day after
    reading: float | None = None  # processes.md 6.6: the arbiter's reading, None until the ruling
    winner: str | None = None  # processes.md 6.6: None while open, else "manager" or "buyer"
    at_close: bool = False  # running.md 6.5: ruled at the market's close


@dataclass
class Delivery:
    """A delivery (plain) or a submission (escrow); its number is its place in the contract's list, from 1."""

    label: str  # actions-manager.md 6.2
    day: int  # actions-manager.md 6.2; processes.md 3.2, 6.8
    round: int  # actions-manager.md 6.2; episode.md 5.1 (the view that sees it)
    message: str = ""  # actions-manager.md 6.1 check 6: as sent, cut; "" for none
    quality: float | None = None  # actions-manager.md 6.1 check 7: the stated quality
    outcome: str | None = None  # processes.md 6.1: a submission's "review", "approved", "auto" or "rejected"
    decided: int | None = None  # processes.md 6.4, 6.5: the day of the approval, release or rejection
    rejection: str = ""  # actions-buyer.md 9: the buyer's message, cut; "" for none
    dispute: Dispute | None = None  # processes.md 6.6: a rejection is disputed at most once


@dataclass
class Contract:
    id: str  # actions-buyer.md 5 effect 1: contract-N or contract-N-k (its stream key: randomness.md 3)
    task: str  # actions-buyer.md 5
    post: str  # actions-buyer.md 5; actions-manager.md 3 check 1
    buyer: str  # actions-buyer.md 5
    manager: str  # actions-buyer.md 5
    form: str  # actions-buyer.md 5: "plain" or "escrow"
    price: float  # actions-buyer.md 5
    threshold: float | None  # actions-buyer.md 5: the escrow's quality threshold
    signed: int  # actions-buyer.md 5, 11; processes.md 7: the signing day
    deadline: int  # processes.md 3.1: fixed once, at signing
    results: list = field(default_factory=list)  # processes.md 1.1: its runs' results, in the order started
    label_draws: int = 0  # randomness.md 3.9: label draws made on it, discarded ones included
    deliveries: list = field(default_factory=list)  # processes.md 5.1, 6.3: deliveries or submissions, as made
    kept: str | None = None  # processes.md 5.1: the label its buyer kept (plain)
    paid: float = 0.0  # processes.md 5.2 (toward a plain price), 6.8 (what an escrow released to the manager)
    ended: int | None = None  # actions-buyer.md 11: the day its buyer ended it
    release_day: int | None = None  # processes.md 6.8: the day the escrow released, on its latest submission
    release_how: str | None = None  # running.md 6.3 C: "approved", "automatic", "ruling" or "close"
    received: int | None = None  # processes.md 6.7; episode.md 7.3: the day the released result became the buyer's
    closed: str | None = None  # processes.md 6.9, episode.md 6: closed unreleased, "late" or at the "close"


@dataclass
class Payment:
    day: int  # view.md 6
    round: int  # view.md 6
    payer: str  # actions-manager.md 8
    payee: str  # actions-manager.md 8
    amount: float  # actions-manager.md 8: rounded to the cent
    contract: str | None  # actions-manager.md 8 steps 4, 5: the contract it counts toward
    to_price: float  # processes.md 5.2: the part counted toward the price
    advance: bool  # running.md 6.7: its contract had no delivery when it was made


@dataclass
class Message:
    day: int  # messaging.md 2.5
    round: int  # messaging.md 2.5
    sender: str  # messaging.md 2.5
    to: str  # messaging.md 2.5: a participant's id, or the group "managers", "buyers" or "everyone"
    text: str  # messaging.md 2.5: as sent, cut to messaging.message_chars


@dataclass
class Credit:
    to: str  # rounds.md 7: the recipient
    amount: float  # rounds.md 7
    kind: str  # rounds.md 7: "transfer" or "escrow_release"
    payer: str  # rounds.md 7: the participant whose action made it


@dataclass
class Market:
    seed: int  # randomness.md 1: the first part of every key
    cfg: dict  # config.md: the resolved config (running.md 5)
    last_arrival: int = 0  # episode.md 1.2: L
    day: int = 0  # episode.md 3.1
    round: int | None = None  # episode.md 3.1, 4: None from a day's start until its round 1
    participants: dict = field(default_factory=dict)  # episode.md 1.1: by id, in seat order, buyers first
    tasks: dict = field(default_factory=dict)  # episode.md 3.2: by id; the next number is their count + 1
    posts: dict = field(default_factory=dict)  # actions-buyer.md 1.6: by id
    bids: dict = field(default_factory=dict)  # actions-buyer.md 1.6: by id, in the order first placed
    contracts: dict = field(default_factory=dict)  # processes.md 1.4; episode.md 3.3: by id
    bids_created: int = 0  # actions-manager.md 2: bids ever created (a shadow may lack some: rounds.md 8.6)
    payments: list = field(default_factory=list)  # view.md 6: every payment, in the order made
    messages: list = field(default_factory=list)  # messaging.md 2.5: the one market-wide sequence
    summaries: dict = field(default_factory=dict)  # messaging.md 4.2: conversation -> (text, messages covered)
    deferred: list = field(default_factory=list)  # rounds.md 7: the round's credits still to land
    actor: str | None = None  # rounds.md 7: the participant whose action is running, in an action phase
    replies: int = 0  # rounds.md 6.1: reply numbers given so far
    lost_turns: int = 0  # rounds.md 10.1; running.md 4.2: turns lost to failed model calls
    close_day: int | None = None  # episode.md 5.2: set when the close is decided
    close_reason: str | None = None  # episode.md 5.3
    days_completed: int = 0  # running.md 5, 6.4
    counts: dict = field(default_factory=dict)  # running.md 6.7: the metric counts the final state does not give
    records: int = 0  # running.md 5: the log's position
    log: object = None  # running.md 6.2: the sink, a function of one record; never on a copy

    def __getstate__(self):
        """A copy or a checkpoint has no log sink."""
        return {**self.__dict__, "log": None}

    def __deepcopy__(self, memo):
        """A copy shares the config, which no rule changes (and which may be nested deeper than a copy can go)."""
        new = copy.copy(self)
        for name, value in vars(new).items():
            if name != "cfg":
                setattr(new, name, copy.deepcopy(value, memo))
        return new


def emit(m, record, /, **fields):
    """Append one record of that type to the episode's log (running.md, 6.2, 6.3); nothing on a copy."""
    if m.log is not None:
        m.records += 1
        m.log({"n": m.records, "type": record, "day": m.day, "round": m.round, **fields})


def count(m, name, n=1):
    """Add to a metric count (running.md, 6.7)."""
    m.counts[name] = m.counts.get(name, 0) + n


def notify(m, pid, text):
    """Queue a notification for a participant's next view, stamped as produced now (view.md, 5.1)."""
    when = f"day {m.day}" if m.round is None else f"day {m.day}, round {m.round}"
    line = f"[{when}] {text}"
    m.participants[pid].pending.append(line)
    emit(m, "notification", to=pid, text=line)


def add_participant(m, pid, role, share=1.0):
    """Create a participant at its start balance (episode.md, 1.1, 1.3); buyers must be added before managers."""
    seat = len(of_role(m, role))
    p = m.participants[pid] = Participant(pid, role, seat, share, config.start_balance(m.cfg, share))
    return p


# Ids (actions-buyer.md, 1.6)

_NUMBER = re.compile(r"\d+(-\d+)?")  # any Unicode decimal digits, the whole text


def find(m, kind, raw):
    """The task, post, bid or contract an id argument names, or None (actions-buyer.md, 1.6, steps 1 and 2)."""
    s = TEXT(raw)
    return getattr(m, kind + "s").get(f"{kind}-{s}" if _NUMBER.fullmatch(s) else s)


def numbered(kind, task):
    """The id of the next post or contract on a task: `-k` from its second (actions-buyer.md, 2 and 5)."""
    k = len(task.ended) + 1
    return kind + task.id.removeprefix("task") + (f"-{k}" if k > 1 else "")


def new_task_id(m):
    """episode.md, 3.2: task-N, N counting the tasks created."""
    return f"task-{len(m.tasks) + 1}"


def new_bid_id(m):
    """actions-manager.md, 2: bid-N, N one more than the bids ever created; advances the count."""
    m.bids_created += 1
    return f"bid-{m.bids_created}"


# Money (episode.md, 9; rounds.md, 7; view.md, 8)


def _finite(x, what):
    """config.md, 6, "Finiteness during an episode": an amount that is not finite stops the episode."""
    if not math.isfinite(x):
        raise ArithmeticError(f"{what} is not finite: {x!r}")
    return x


def debit(m, pid, amount, category, check=False):
    """Take an amount from a balance at once, as it is, with its accounts entry.

    A payment and an escrow's price are no charges (`check` False); a dispute deposit is taken with `check`,
    whatever its amount: a manager in business whose balance is then at or below 0 is out of business (9.1).
    """
    p = m.participants[pid]
    p.balance = _finite(p.balance - _finite(amount, category), f"the balance of {pid}")
    p.ledger.append((category, amount))
    emit(m, "money", participant=pid, kind=category, amount=-amount, balance=p.balance)
    if check and p.role == "manager" and not p.out_of_business and p.balance <= 0:
        p.out_of_business = True
        count(m, "out_of_business_events")
        emit(m, "out_of_business", participant=pid, cause=category, balance=p.balance)
        notify(m, pid, OUT_OF_BUSINESS_NOTICE)


def charge(m, pid, amount, category):
    """A charge (9.1): rounded to 6 decimals, ignored at 0 or less, then the out-of-business check.

    Returns the amount charged, 0.0 when nothing was.
    """
    amount = round(_finite(amount, category), 6)
    if amount <= 0:
        return 0.0
    debit(m, pid, amount, category, check=True)
    return amount


def _land(m, pid, amount, cause):
    """An amount reaches a balance; a manager out of business that it leaves above 0 is back (9.3)."""
    p = m.participants[pid]
    p.balance = _finite(p.balance + amount, f"the balance of {pid}")
    if p.out_of_business and p.balance > 0:
        p.out_of_business = False
        count(m, "back_in_business_events")
        emit(m, "back_in_business", participant=pid, cause=cause, balance=p.balance)
        notify(m, pid, BACK_IN_BUSINESS_NOTICE)


def credit(m, pid, amount, category):
    """Credit an amount, with its accounts entry now (view.md, 8).

    While an action runs (`m.actor`), a credit to another participant waits in the round's list and reaches the
    balance when `land_credits` runs (rounds.md, 7); every other credit reaches it at once.
    """
    wait = m.actor is not None and pid != m.actor
    kind = DEFERRED[category] if wait else None  # rounds.md 7: no other credit is made to another participant
    m.participants[pid].ledger.append((category, _finite(amount, category)))
    emit(m, "money", participant=pid, kind=category, amount=amount, deferred=wait)
    if wait:
        m.deferred.append(Credit(pid, amount, kind, m.actor))
    else:
        _land(m, pid, amount, DEFERRED.get(category, category))  # one name per cause, landed now or later


def land_credits(m):
    """The round's credits land, in the order made (rounds.md, 9.1)."""
    credits, m.deferred = m.deferred, []
    for c in credits:
        emit(m, "credit_landed", to=c.to, amount=c.amount, kind=c.kind, payer=c.payer)
        _land(m, c.to, c.amount, c.kind)


# Derived values several modules read


def now(m):
    """The current round index g (README.md, "Vocabulary")."""
    return config.round_index(m.cfg, m.day, m.round)


def sealed(m, post):
    """Whether a post's bids are sealed from its buyer: by the round index alone (processes.md, 8)."""
    return now(m) < config.opening_round(m.cfg, config.round_index(m.cfg, post.day, post.round))


def of_role(m, role):
    """The participants of a role, in seat order (episode.md, 1.1)."""
    return [p for p in m.participants.values() if p.role == role]


def bids_on(m, post_id, status="open"):
    """A post's bids of one status, in the order first placed (actions-buyer.md, 1.6)."""
    return [b for b in m.bids.values() if b.post == post_id and b.status == status]


def contracts_of(m, pid):
    """Every contract a participant is the buyer or the manager of, in creation order (actions-buyer.md, 1.6)."""
    return [c for c in m.contracts.values() if pid in (c.buyer, c.manager)]


def due(m, c):
    """due(c): the due day of a contract's task (processes.md, 3.1)."""
    return m.tasks[c.task].due


def close_day(m, c):
    """close(c): an escrow's close day, due + escrow_close_days (processes.md, 6.9)."""
    return due(m, c) + config.escrow_close_days(m.cfg)


def result(c, label):
    """A contract's result of that label, or None (processes.md, 1.1)."""
    return next((r for r in c.results if r.label == label), None)


def arrived(m, r):
    """A result has arrived from the start of its arrival day (processes.md, 1.4)."""
    return r.arrival <= m.day


def in_flight(m, manager, worker):
    """A worker's runs not yet arrived, as (contract, result), over all of the manager's contracts in creation
    order, then in the order started (processes.md, 1.3)."""
    return [(c, r) for c in m.contracts.values() if c.manager == manager
            for r in c.results if r.worker == worker and not arrived(m, r)]


def latest(c):
    """A contract's latest delivery or submission, or None (actions-manager.md, 6.2)."""
    return c.deliveries[-1] if c.deliveries else None


def delivery_of(c, label):
    """The delivery or submission of a label on a contract, or None: a label is given once (processes.md, 5.1, 6.3)."""
    return next((d for d in c.deliveries if d.label == label), None)


def in_use(c):
    """A plain contract's delivery in use: the kept label's, else the latest (processes.md, 5.1)."""
    return delivery_of(c, c.kept) if c.kept else latest(c)


def held(c):
    """An escrow holds its price until it releases, closes unreleased or is ended (processes.md, 6.1)."""
    return c.form == "escrow" and c.ended is None and c.release_day is None and c.closed is None


def settled(c):
    """view-contracts.md, 2.1: ended, or an escrow that no longer holds its price."""
    return c.ended is not None or (c.form == "escrow" and not held(c))


def refunded(c):
    """What came back to the buyer from an escrow: the price less what it released (processes.md, 6.8, 6.9)."""
    return 0.0 if c.form == "plain" or held(c) else c.price - c.paid


def under_review(c):
    """The submission awaiting the buyer's decision, or None: at most one, the latest (processes.md, 6.1)."""
    d = latest(c)
    return d if d and d.outcome == "review" else None


def open_dispute(c):
    """The submission whose dispute awaits the ruling, or None: at most one, the latest (processes.md, 6.1)."""
    d = latest(c)
    return d if d and d.dispute and d.dispute.winner is None else None


def disputable(m, c):
    """The latest submission is a rejection, not disputed, still within its window (view-contracts.md, 2.1)."""
    d = latest(c)
    window = m.cfg["contracts"]["escrow"]["dispute_window_days"]
    return bool(d) and d.outcome == "rejected" and d.dispute is None and m.day <= d.decided + window


def setup_fee(m, c):
    """An escrow's setup fee (processes.md, 6.2)."""
    return round(m.cfg["contracts"]["escrow"]["fee_rate"] * c.price, 2)


def dispute_fee(m, c):
    """The dispute deposit, which is also the arbiter's fee (processes.md, 6.6)."""
    return round(m.cfg["contracts"]["escrow"]["dispute_fee_rate"] * c.price, 2)


def owes_fee(m, c):
    """The buyer owes the arbiter's fee: a ruling released the price and the result is not yet its (6.7)."""
    return c.release_how == "ruling" and c.received is None and dispute_fee(m, c) > 0


def owed(c):
    """What is owed on a plain price: nothing on an ended contract (processes.md, 5.2)."""
    return 0.0 if c.ended is not None else max(0.0, round(c.price - c.paid, 2))


def paid_in_full(c):
    """processes.md, 5.2."""
    return c.paid >= c.price - 1e-6


def beyond(m, c):
    """The parts of payments paid on top of a contract's price, in payment order (actions-manager.md, 8)."""
    parts = (round(p.amount - p.to_price, 2) for p in m.payments if p.contract == c.id)
    return [part for part in parts if part > 0]
