"""The market's own mechanics (spec/processes.md): production, readings, the due-day notices, the value of a
result, the escrow's release, timers and ruling, when a contract can be ended, and the opening of sealed bids.

Every function reads and changes only the market it is given. The processes of a day's start and of the close
(`arrivals`, `due_notice`, `rule`, `auto_release`, `lateness_close`) test their own conditions, so each is called
for every contract in creation order (episode.md, 3 and 6).
"""

import math

from . import config, rng, state
from .fmt import CR, F, G, INT, SUM, finite, unit


def rel(m, d):
    """A day relative to today (actions-buyer.md, 1.13; view-contracts.md, 2.3; episode.md, 3.2)."""
    n = d - m.day
    if n > 0:
        return unit(INT(n), "day") + " left"
    return "today" if n == 0 else unit(INT(-n), "day") + " ago"


# 1. Production


def origin(r):
    """`{origin}` of a result (1.4)."""
    who = "your employee" if r.worker == "employee" else "you"
    return f"{who}, fresh" if r.kind == "fresh" else f"{who}, refine of {r.parent}"


def _started(c, worker, kind):
    """k: the runs of a worker and kind already started on a contract (randomness.md, 3.8)."""
    return sum(r.worker == worker and r.kind == kind for r in c.results)


def _parent(c, parent):
    """The parent result, given as itself or as its label, or None for a fresh run (1.1)."""
    return state.result(c, parent) if isinstance(parent, str) else parent


def draw_run(m, c, worker, kind, parent=None):
    """A run's draw, made at the balance check: it changes nothing, so a refused run draws the same again
    (1.2; randomness.md, 3.8). A cost that is not finite stops the episode (config.md, 6)."""
    p = _parent(c, parent)
    run = rng.run(m.seed, rng.stream_key(c.id), worker, kind, _started(c, worker, kind), m.cfg["production"],
                  p.q if p else None)
    finite(run["cost"], "the cost of a run")
    return run


def start_run(m, c, worker, kind, parent, run):
    """An accepted run: its label is drawn and its result created, running; the caller then charges the cost
    (1.3; actions-manager.md, 4, effects 1 to 3; randomness.md, 3.9)."""
    k, p = _started(c, worker, kind), _parent(c, parent)
    drawn = rng.label(m.seed, rng.stream_key(c.id), c.label_draws, {r.label for r in c.results})
    c.label_draws = drawn["n"]
    r = state.Result(drawn["label"], worker, kind, p.label if p else None, m.day, m.day + run["days"], run["q"])
    c.results.append(r)
    state.emit(m, "run", contract=c.id, label=r.label, worker=worker, kind=kind, parent=r.parent, k=k,
               z_q=run["z_q"], z_t=run["z_t"], z_c=run["z_c"], quality=r.q, days=run["days"], cost=run["cost"],
               arrival=r.arrival)
    return r


def arrivals(m, c):
    """The results of a contract that arrive today, in the order their runs started (1.4; episode.md, 3.3a)."""
    gone = "" if c.ended is None else f" {c.buyer} ended {c.id} on day {INT(c.ended)}, so it cannot be delivered."
    for r in c.results:
        if r.arrival == m.day:
            state.emit(m, "arrival", contract=c.id, label=r.label, worker=r.worker, kind=r.kind, parent=r.parent,
                       quality=r.q)
            state.notify(m, c.manager, f"{r.label} ({origin(r)}) for {c.id} has arrived.{gone}")


# 2. Readings


def read(m, c, r, side):
    """One reading of a result by a side, "manager" or "buyer"; the caller charged it first (2.1;
    randomness.md, 3.10)."""
    readings = getattr(r, side + "_readings")
    tests = m.cfg["tests"]
    drawn = rng.reading(m.seed, rng.stream_key(c.id), side, r.label, len(readings), r.q, tests[side + "_sd"],
                        tests["decimals"])
    state.emit(m, "reading", contract=c.id, label=r.label, side=side, i=len(readings), z=drawn["z"],
               reading=drawn["reading"])
    readings.append(drawn["reading"])
    return drawn["reading"]


def readings_text(m, readings, side):
    """The readings text of one side's readings of one result (2.3)."""
    if not readings:
        return "readings: none"
    n, shown, decimals = len(readings), m.cfg["view"]["readings_shown"], m.cfg["tests"]["decimals"]
    more = "..., " if n > shown else ""
    values = ", ".join(F(x, decimals) for x in readings[-shown:])
    se = m.cfg["tests"][side + "_sd"] / math.sqrt(n)
    return f"readings [{more}{values}] mean {F(SUM(readings) / n, 2)} (n={INT(n)}, \u00b1{F(se, 2)} standard error)"


# 3. Deadlines and lateness


def due_notice(m, c):
    """At most one of the two due-day notices of a contract (3.3; episode.md, 3.3b)."""
    if not (state.held(c) if c.form == "escrow" else c.ended is None and not c.deliveries):
        return
    d = INT(m.day)
    if c.deadline == m.day:
        for pid in (c.buyer, c.manager):
            state.notify(m, pid, f"{c.id} is due today (deadline day {d}).")
    elif state.due(m, c) == m.day:
        state.notify(m, c.buyer, f"The task of {c.id} is due today (day {d}): its value to you falls for each day "
                                 f"after it. The contract's deadline day is {INT(c.deadline)}.")


# 4. The value of a result to its buyer


def f(m, q):
    """The value fraction of a quality (4; view-contracts.md, 2.3)."""
    q0 = m.cfg["value"]["q0"]
    return min(1.0, max(0.0, (q - q0) / (10 - q0))) ** m.cfg["value"]["exponent"]


def value(m, c, q, day):
    """The value to a contract's buyer of a result of true quality q received on a day (4)."""
    late = max(0, day - state.due(m, c))
    return m.tasks[c.task].worth * f(m, q) * max(0, 1 - m.cfg["value"]["late_decay"] * late)


# 5. The plain contract


def count_payment(m, c, amount):
    """Count a payment toward a plain price: the part counted and the rest of the payment (5.2;
    actions-manager.md, 8)."""
    to_price = round(min(amount, state.owed(c)), 2)
    c.paid = round(c.paid + to_price, 4)
    return to_price, round(amount - to_price, 2)


# 6. The escrow contract


def release(m, c, how):
    """The escrow releases on its latest submission, with the pay cut counted at the submission day (6.8)."""
    d = state.latest(c)
    due, late = state.due(m, c), state.days_late(c, d)
    if c.deadline != due and d.day - due >= config.escrow_close_days(m.cfg):
        factor = 0
    else:
        factor = max(0, 1 - m.cfg["value"]["late_decay"] * late)
    to_manager = round(c.price * factor, 4)
    refund = c.price - to_manager
    c.release_day, c.release_how = m.day, how
    if to_manager > 0:
        c.paid = to_manager
    state.emit(m, "release", contract=c.id, label=d.label, how=how, to_manager=to_manager, refunded=refund,
               days_late=late)
    if to_manager > 0:
        state.credit(m, c.manager, to_manager, "escrow releases")
    if refund > 0:
        state.credit(m, c.buyer, refund, "escrow refunds")
    return to_manager, refund


def paid_text(m, c):
    """`{paid_text}`: what a release paid the manager (6.8)."""
    k = state.days_late(c, state.latest(c))
    late = f" (submitted {unit(INT(k), 'day')} late)" if k else ""
    return f"{F(c.paid, 2)} of the price {G(c.price)}{late}"


def back(refund):
    """`{back}`: what a release refunded the buyer (6.5, 6.6)."""
    return f", and {F(refund, 2)} came back to you" if refund > 1e-9 else ""


def auto_release(m, c, how="automatic"):
    """The automatic release of a submission still under review at the start of its day, or at the market's
    close with how "close" (6.5; episode.md, 3.5 and 6, step 2)."""
    d = state.under_review(c)
    if d is None:
        return
    auto = state.auto_day(m, d)
    if how != "close" and auto > m.day:
        return
    d.outcome, d.decided = "auto", m.day
    state.emit(m, "auto_release", contract=c.id, label=d.label, submitted=d.day)
    to_manager, refund = release(m, c, how)
    c.received = m.day
    head = f"{d.label} on {c.id} was released:"
    by = f"neither approved nor rejected it by the start of day {INT(auto)}."
    state.notify(m, c.buyer, f"{head} you {by} It is yours; {F(to_manager, 2)} of the price {G(c.price)} went to "
                             f"{c.manager}{back(refund)}.")
    state.notify(m, c.manager, f"{head} {c.buyer} {by} You received {paid_text(m, c)}; {c.id} is closed.")


def rule(m, c, at_close=False):
    """The arbiter's ruling on an open dispute, on its ruling day or at the market's close, where the fee of a
    ruling for the manager is collected if the buyer's balance covers it (6.6, 6.7; episode.md, 3.4 and 6,
    step 1)."""
    d = state.open_dispute(c)
    if d is None or not (at_close or state.ruling_day(d) <= m.day):
        return
    fee = state.dispute_fee(m, c)
    drawn = rng.arbiter(m.seed, rng.stream_key(c.id), d.label, state.result(c, d.label).q,
                        m.cfg["contracts"]["escrow"]["arbiter_sd"], m.cfg["tests"]["decimals"])
    won = drawn["reading"] >= c.threshold
    d.dispute.reading, d.dispute.winner, d.dispute.at_close = drawn["reading"], "manager" if won else "buyer", at_close
    charged = 0.0
    if won:
        state.credit(m, c.manager, fee, "dispute deposits returned")
        refund = release(m, c, "ruling")[1]
        if fee == 0:
            c.received = m.day
        elif at_close and state.covers(m.participants[c.buyer], fee):
            charged = state.charge(m, c.buyer, fee, "arbiter's fees paid")
            c.received = m.day
    else:
        state.emit(m, "money", participant=c.manager, kind="dispute deposit lost", amount=fee, held=True)
    state.emit(m, "ruling", contract=c.id, label=d.label, z=drawn["z"], reading=drawn["reading"],
               threshold=c.threshold, winner=d.dispute.winner, at_close=at_close, deposit=fee,
               deposit_fate="returned" if won else "lost", fee_owed=fee if state.owes_fee(m, c) else 0.0,
               fee_charged=charged)
    head = (f"The arbiter's reading of {d.label} on {c.id} is {F(drawn['reading'], m.cfg['tests']['decimals'])}, "
            f"against the threshold {G(c.threshold)}:")
    if won:
        state.notify(m, c.manager, f"{head} you won the dispute. Your deposit of {CR(fee)} came back, and you "
                                   f"received {paid_text(m, c)}; {c.id} is closed.")
        state.notify(m, c.buyer, f"{head} the dispute went against you. The price was released to "
                                 f"{c.manager}{back(refund)}. {d.label} is yours once you pay the arbiter's fee "
                                 f"of {CR(fee)} (pay_escrow_arbiter).")
    else:
        more = ("; you can submit another result while the escrow holds the price"
                if m.day < state.close_day(m, c) else "")
        state.notify(m, c.manager, f"{head} you lost the dispute and your deposit of {CR(fee)}. The rejection "
                                   f"stands{more}.")
        state.notify(m, c.buyer, f"{head} the dispute went your way. Your rejection stands and the price stays "
                                 f"held.")


def pending(m, c):
    """Something delays an escrow's lateness close: a submission under review, an open dispute, or a rejection
    still disputable (6.9)."""
    return bool(state.under_review(c) or state.open_dispute(c) or state.disputable(m, c))


def lateness_close(m, c):
    """The lateness close of an escrow from its close day, once nothing is pending (6.9; episode.md, 3.6)."""
    if not state.held(c) or m.day < state.close_day(m, c) or pending(m, c):
        return
    c.closed = "late"
    state.emit(m, "lateness_close", contract=c.id, refunded=c.price)
    state.credit(m, c.buyer, c.price, "escrow refunds")
    late = unit(INT(m.day - state.due(m, c)), "day")
    its = "its task's due day" if c.deadline != state.due(m, c) else "its deadline"
    state.notify(m, c.buyer, f"The escrow of {c.id} closed: nothing was released by {late} after {its}, so its "
                             f"price {G(c.price)} came back to you.")
    state.notify(m, c.manager, f"The escrow of {c.id} closed unreleased ({late} after {its}): it takes no more "
                               f"submissions.")


# 7. When a contract can be ended


def end_rule(m, c):
    """The first matching row of section 7, as a code; "stands" and "overdue" can be ended (texts:
    actions-buyer.md, 11)."""
    if c.ended is not None:
        return "ended"
    if c.form == "plain":
        if c.deliveries:
            return "delivered"
        if c.signed > c.deadline:
            return "late_signed"
    else:
        if c.release_day is not None:
            return "released"
        if c.closed:
            return "closed"
        if state.under_review(c):
            return "review"
        if state.open_dispute(c):
            return "dispute"
        if state.disputable(m, c):
            return "disputable"
        if c.deliveries:
            return "stands"
        if c.deadline + 1 >= state.close_day(m, c):
            return "cap"
    return "early" if m.day <= c.deadline else "overdue"


# 8. Sealed bids and their opening


def opening_notices(m):
    """The opening notices of the round that has just started, posts in the order made (8; episode.md, 4,
    step 3)."""
    for post in m.posts.values():
        if post.closed is None and state.accept_from(m, post) == state.now(m):
            n = len(state.live_bids(m, post.id))
            if n:
                state.notify(m, post.buyer, f"The bids on {post.id} are opened: {unit(INT(n), 'bid')}, which you "
                                            f"can accept from now.")
