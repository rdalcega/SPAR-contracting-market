"""The view's market sections (spec/view-contracts.md): open posts, the buyer's tasks, the manager's production,
the active and the finished contracts.

Read only. A section is `(title, items)`: an item is one line with its sub-lines, joined by line feeds, each
sub-line indented four spaces; view.md, 2.3, puts one empty line around the title and between the items. Today is
`m.day`, now is `state.now(m)`.
"""


from . import config, prompts, state
from .actions import preference, terms
from .fmt import CR, INT, ONE_LINE, PCT0, F, G, unit
from .processes import end_rule, f, readings_text, rel

FORUM = "--- Open posts on the forum ---"  # 3: the manager's title
TASKS = "--- Your tasks not yet contracted ---"  # 4
ACTIVE = "--- Your active contracts ---"  # 7, 8
FINISHED = "--- Your finished contracts"  # 11: how the title starts, with its counts or without
NO_RESULTS = "no results yet"  # 7.2
ESCROW_DAYS = "escrow days: "  # 9: how the escrow days line starts
HOW = {"approved": "approved", "ruling": "the arbiter ruled"}  # 6.4: any other release is automatic
END = {  # 6.6: the clauses that state no day, by the row of processes.md, 7
    "stands": " | can be ended now",
    "overdue": " | can be ended now",
    "delivered": " | cannot be ended (it has a delivery)",
    "cap": " | its escrow closes the day after its deadline day: can be ended only after a rejection that stands",
    "late_signed": " | signed after its deadline day: cannot be ended",
}


# 2. Terms, values and shared texts


def _when(m, g):
    """WHEN(g) (2.3)."""
    d, r = divmod(g - 1, m.cfg["time"]["rounds_per_day"])
    return f"day {INT(d + 1)}, round {INT(r + 1)}"


def _opens(m, post):
    """WHEN(ACCEPT_FROM(p)) (2.3)."""
    return _when(m, state.accept_from(m, post))


def _task_due(m, t):
    """TASK_DUE(t) (2.3)."""
    return f"due day {INT(t.due)} ({rel(m, t.due)})"


def _worths(m, worth, factor=1):
    """The values of a worth at the qualities 5 to 10, on time or at a lateness factor (2.3, 3.4)."""
    return ", ".join(F(worth * f(m, q) * factor, 1) for q in range(5, 11))


def _worth(m, t):
    """A task and its worth with VALUES(worth) (2.3, 4.2, 8.1)."""
    return f"{t.id} | worth {INT(t.worth)} to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: " \
           f"{_worths(m, t.worth)})"


def _named(m, names):
    """NAMED(ids) (2.3)."""
    n = m.cfg["view"]["overflow_ids"]
    return ", ".join(names[:n]) + (f" and {INT(len(names) - n)} more" if len(names) > n else "")


def _readings(m, c, label, side):
    """`your {READINGS(R, side)}` of a result of a contract (2.3)."""
    return "your " + readings_text(m, getattr(state.result(c, label), side + "_readings"), side)


def _item(line, subs):
    """A line and the sub-lines that belong to it (1)."""
    return "\n".join([line] + ["    " + s for s in subs])


def listing_day(m, c):
    """LD(c) (2.1)."""
    return c.deadline if c.form == "plain" else state.due(m, c)


# 3. Open posts


def _listed(m, things, rank):
    """The things listed in full, in their own order, and the hidden ones, newest first (3.1, 4.1)."""
    n = m.cfg["view"]["open_shown"]
    order = sorted(range(len(things)), key=lambda i: (rank(things[i]), -i))
    return [things[i] for i in sorted(order[:n])], [things[i] for i in sorted(order[n:], reverse=True)]


def _tomorrow(m, t):
    """What a result of a task delivered tomorrow is worth to its buyer (3.4, case B, buyer; 8.1)."""
    late = m.day + 1 - t.due
    factor = max(0, 1 - m.cfg["value"]["late_decay"] * late)
    if factor <= 0:
        worth = "worth nothing at any quality"
    else:
        worth = (f"worth {PCT0(factor)} of its on-time value (at quality 5, 6, 7, 8, 9, 10: "
                 f"{_worths(m, t.worth, factor)}), and nothing from day "
                 f"{INT(t.due + config.escrow_close_days(m.cfg))}")
    return f"a result delivered tomorrow, day {INT(m.day + 1)}, is {unit(INT(late), 'day')} late and {worth}"


def _late(m, t, role):
    """LATE(due, worth, role), the lateness clause of a post or an uncontracted task (3.4)."""
    due, today = t.due, m.day
    dl = config.deadline(m.cfg, due, today)
    signed = f"a contract signed today has deadline day {INT(dl)}"
    if today < due:
        if dl == due:
            return ""
        return (f" | {signed}, {unit(INT(dl - today), 'day')} after signing (the task's due day is {INT(due)}: "
                f"its value to {'the buyer' if role == 'manager' else 'you'} falls from then)")
    head = f" | {'DUE TODAY' if today == due else 'PAST DUE'} (the task's due day {INT(due)}): "
    if role == "buyer":
        return f"{head}{_tomorrow(m, t)}; {signed}"
    return (f"{head}{signed}; a result's value to the buyer {'will fall' if today == due else 'falls'} by "
            f"{PCT0(m.cfg['value']['late_decay'])} of its on-time value for each day after day {INT(due)}, and it "
            f"is worth nothing from day {INT(due + config.escrow_close_days(m.cfg))}")


def bids_field(m, post, pid):
    """`{bids}` of a post as a participant's view writes it now (3.3, 4.3)."""
    if pid == post.buyer:
        if state.sealed(m, post):
            return f"sealed until {_opens(m, post)} (you see them then)"
        bids = sorted(state.live_bids(m, post.id), key=lambda b: b.price)
        return "; ".join(f"{b.id} by {b.manager} at {G(b.price)}{terms(b.threshold)}" for b in bids) or "no bids yet"
    if m.participants[pid].role == "buyer":
        return "sealed (only the buyer sees them)"
    b = state.own_bid(m, post.id, pid)
    if b is None:
        return "sealed (only the buyer sees them); you have not bid"
    return (f"sealed; yours: {b.id} at {G(b.price)}{terms(b.threshold)} (awaiting the buyer: not a contract unless "
            f"accepted)")


def open_posts(m, p):
    """The manager's "Open posts on the forum" and the buyer's "Other buyers' open posts (for reference)" (3)."""
    manager = p.role == "manager"
    posts = [post for post in m.posts.values() if post.closed is None and (manager or post.buyer != p.id)]
    listed, hidden = _listed(m, posts, lambda post: int(manager and state.own_bid(m, post.id, p.id) is not None))
    items = []
    if hidden:
        items.append(f"({unit(INT(len(hidden)), 'more open post')} not listed: "
                     f"{_named(m, [post.id for post in hidden])})")
    for post in listed:
        t = m.tasks[post.task]
        late = _late(m, t, "manager") if manager else ""
        window = f" | bids open; the buyer can accept one from {_opens(m, post)}" if state.sealed(m, post) else ""
        items.append(f"{post.id} by {post.buyer} | {t.id} | {_task_due(m, t)}{late} | "
                     f"{preference(post.form, post.threshold)}{window} | bids: {bids_field(m, post, p.id)}")
    return FORUM if manager else "--- Other buyers' open posts (for reference) ---", items


# 4. The buyer's "Your tasks not yet contracted"


def _ended(m, t):
    """ENDED(t) (4.2)."""
    return "you ended " + ", ".join(f"{cid} on day {INT(m.contracts[cid].ended)}" for cid in t.ended)


def tasks(m, p):
    """The buyer's "Your tasks not yet contracted" (4)."""

    def rank(t):
        if t.state == "queued":
            return 1
        post = m.posts[t.post]
        return 2 if state.sealed(m, post) or not state.live_bids(m, post.id) else 0

    mine = [t for t in m.tasks.values() if t.buyer == p.id and t.state in ("queued", "posted")]
    listed, hidden = _listed(m, mine, rank)
    items = []
    if hidden:
        items.append(f"({unit(INT(len(hidden)), 'more task')} not listed: "
                     f"{_named(m, [f'{t.id} ({t.state})' for t in hidden])})")
    for t in listed:
        head = f"{_worth(m, t)}{_late(m, t, 'buyer')} | {_task_due(m, t)} | "
        if t.state == "queued":
            items.append(f"{head}QUEUED (not posted{f' again since {_ended(m, t)}' if t.ended else ''})")
            continue
        post = m.posts[t.post]
        window = (f" | bids open; you can accept one from {_opens(m, post)}, not before (accept_bid fails earlier)"
                  if state.sealed(m, post) else "")
        items.append(f"{head}POSTED as {post.id}{f' ({_ended(m, t)})' if t.ended else ''}, "
                     f"{preference(post.form, post.threshold)} (edit_post changes its stated form and threshold)"
                     f"{window} | bids: {bids_field(m, post, p.id)}")
    return TASKS, items


# 5. The manager's "Your production"


def capacity_line(m, pid):
    """The capacity line (5)."""

    def on(c):
        return c.id + (", closed: its result cannot be used" if state.settled(c) else "")

    own, cap = (m.cfg["production"][worker]["capacity"] for worker in ("manager", "employee"))
    mine, emp = state.in_flight(m, pid, "manager"), state.in_flight(m, pid, "employee")
    if not mine:
        me = f"free: {INT(own)} run_myself can start now on your contracts"
    else:
        me = "busy on " + ", ".join(f"{r.label} ({on(c)}) until day {INT(r.arrival)}" for c, r in mine)
        if len(mine) >= own:
            me += f": no run_myself before day {INT(min(r.arrival for _, r in mine))}"
        else:
            me += f": {INT(own - len(mine))} more run_myself can start now on your contracts"
    busy = ", ".join(f"{r.label} on {on(c)}, arrives day {INT(r.arrival)}" for c, r in emp)
    if cap - len(emp) > 0:
        now = f"{INT(cap - len(emp))} delegate_run can start now on your contracts"
    else:
        now = f"no delegate_run before day {INT(min(r.arrival for _, r in emp))}"
    return (f"You (run_myself): {me}. Employee (delegate_run): {INT(len(emp))} of {unit(INT(cap), 'slot')} busy"
            f"{f' ({busy})' if emp else ''}: {now}.")


def production(m, p):
    """The manager's "Your production": the expected run costs line and the capacity line (5)."""
    runs = (("employee fresh", "employee", "fresh"), ("employee refine", "employee", "refine"),
            ("you fresh", "manager", "fresh"), ("you refine", "manager", "refine"))
    costs = ", ".join(f"{name} {F(config.expected_cost(m.cfg, worker, kind), 2)} "
                      f"(\u00b1{prompts.spread(m.cfg, worker, kind)})" for name, worker, kind in runs)
    line = (f"Expected run costs in credits, with how much each typically varies either way: {costs}. "
            f"test_result: {G(m.cfg['tests']['manager_cost'])} per reading.")
    return "--- Your production ---", [line, capacity_line(m, p.id)]


# 6. Fields shared by the contract lines


def _price(c):
    """`price {price}{form} | {money}` (6.1, 6.2)."""
    if c.form == "plain":
        of = "" if c.ended is not None else f" of {F(c.price, 2)}"
        return f"price {G(c.price)} (plain) | paid {F(c.paid, 2)}{of}"
    back = state.refunded(c)
    return (f"price {G(c.price)} (escrow, threshold {G(c.threshold)}) | deposit {F(c.price, 2)}: "
            f"{F(c.price if state.held(c) else 0, 2)} held in escrow, {F(c.paid, 2)} released"
            + (f", {F(back, 2)} refunded" if back > 1e-9 else ""))


def _deadline(m, c):
    """`{deadline}` on an active line (6.3)."""
    due = state.due(m, c)
    notes = [] if state.settled(c) else [rel(m, c.deadline)]
    if c.deadline != due:
        notes.append(f"its task's due day is {INT(due)}")
    return f"deadline day {INT(c.deadline)}" + (f" ({'; '.join(notes)})" if notes else "")


def _lateness(c, d):
    """`{lateness}` of a delivery or a submission (6.4)."""
    late = state.days_late(c, d)
    return unit(INT(late), "day") + " late" if late else "on time"


def _status(m, c, role):
    """`{status}` (6.4)."""
    if c.ended is not None:
        return f"ENDED by {c.buyer} on day {INT(c.ended)}" + (" (price refunded)" if c.form == "escrow" else "")
    d = state.in_use(c)
    if c.form == "plain":
        if d is None:
            return "NOT DELIVERED"
        return (f"in use{'' if role == 'buyer' else f' by {c.buyer}'}: {d.label} ({'kept' if c.kept else 'latest'}; "
                f"day {INT(d.day)}, {_lateness(c, d)})")
    if c.release_day is not None:
        owed = ""
        if state.owes_fee(m, c):
            owed = (f"; {c.buyer} owes the arbiter's fee {CR(state.dispute_fee(m, c))} to the arbiter (not to "
                    f"{'the manager' if role == 'buyer' else 'you'}) before the result is its")
        return (f"RELEASED {d.label} (day {INT(c.release_day)}, "
                f"{HOW.get(c.release_how, 'released with no decision')}; submitted {_lateness(c, d)}){owed}")
    if c.closed:
        return "CLOSED UNRELEASED (price refunded)"
    if state.under_review(c):
        return (f"price held; {d.label} UNDER REVIEW since day {INT(d.day)} (released at the start of day "
                f"{INT(state.auto_day(m, d))} unless approved or rejected before)")
    if state.open_dispute(c):
        return (f"price held; the rejection of {d.label} is IN DISPUTE (the arbiter rules at the start of day "
                f"{INT(state.ruling_day(d))})")
    rejected = sum(x.outcome == "rejected" for x in c.deliveries)
    if not rejected:
        return "price held; nothing submitted"
    open_ = ""
    if state.disputable(m, c):
        open_ = f"; the rejection of {d.label} can be disputed through day {INT(state.dispute_until(m, d))}"
    return f"price held; {unit(INT(rejected), 'submission')} rejected{open_}"


def _closed(m, c, role):
    """`{closed}`, the closed mark (6.5)."""
    if c.ended is not None or not state.settled(c):
        return ""
    if role == "manager":
        return " | CLOSED: takes no more runs, tests or submissions"
    return "" if state.owes_fee(m, c) else " | CLOSED: settled; it cannot be approved, rejected or ended"


def _end(m, c):
    """`{end}`, when the buyer can end the contract (6.6)."""
    rule = end_rule(m, c)
    if rule == "disputable":
        until = state.dispute_until(m, state.latest(c))
        return f" | can be ended from day {INT(until + 1)} if {c.manager} does not dispute"
    if rule == "early":
        return (f" | can be ended from day {INT(c.deadline + 1)} if nothing is "
                f"{'delivered' if c.form == 'plain' else 'released'} by then")
    return END.get(rule, "")


# 7. The manager's "Your active contracts"


def _outcome(m, c, d):
    """OUTCOME, one submission's outcome (7.3)."""
    if d.outcome == "review":
        return f"UNDER REVIEW (released at the start of day {INT(state.auto_day(m, d))} unless approved or rejected)"
    if d.outcome == "approved":
        return f"APPROVED day {INT(d.decided)}"
    if d.outcome == "auto":
        return f"RELEASED with no decision day {INT(d.decided)}"
    text = f"REJECTED day {INT(d.decided)}"
    if d.dispute is None:
        until = state.dispute_until(m, d)
        if m.day > until:
            return f"{text} (not disputed; the dispute window closed after day {INT(until)})"
        if d is state.latest(c) and state.held(c):
            return f"{text} (disputable through day {INT(until)})"
        return text
    if d.dispute.winner is None:
        return f"{text}, IN DISPUTE (the arbiter rules at the start of day {INT(state.ruling_day(d))})"
    return (f"{text}, disputed: the arbiter read {F(d.dispute.reading, m.cfg['tests']['decimals'])} and ruled for "
            f"the {'manager: RELEASED' if d.dispute.winner == 'manager' else 'buyer'}")


def _result_line(m, c, r):
    """The result line (7.3)."""
    kind = "fresh" if r.kind == "fresh" else f"refine of {r.parent}"
    text = f"{r.label} | {'employee' if r.worker == 'employee' else 'you'} | {kind} | started day {INT(r.start)}, "
    if state.arrived(m, r):
        text += f"arrived day {INT(r.arrival)} | {_readings(m, c, r.label, 'manager')}"
    else:
        wait = "tomorrow" if r.arrival == m.day + 1 else f"in {INT(r.arrival - m.day)} days"
        text += f"arrives day {INT(r.arrival)} ({wait}: it cannot be tested, refined or delivered before then)"
    d = state.delivery_of(c, r.label)
    if d is None:
        return text
    if c.form == "plain":
        return text + (f" | DELIVERED day {INT(d.day)} (in use)" if d is state.in_use(c)
                       else f" | delivered day {INT(d.day)} (not in use)")
    message = f"; the buyer's message: \"{ONE_LINE(d.rejection)}\"" if d.rejection else ""
    return f"{text} | SUBMITTED day {INT(d.day)}: {_outcome(m, c, d)}{message}"


def _manager_item(m, c):
    """A contract item of the manager's active contracts (7.2)."""
    line = (f"{c.id} with {c.buyer} | {c.task} | {_price(c)} | {_deadline(m, c)} | {_status(m, c, 'manager')}"
            f"{_closed(m, c, 'manager')}")
    days = [_escrow_days(m, c, "manager")] if state.held(c) else []
    return _item(line, days + ([_result_line(m, c, r) for r in c.results] or [NO_RESULTS]))


# 8. The buyer's "Your active contracts"


def _extra(m, c, d):
    """`{extra}` of a submission line (8.3)."""
    if d.outcome == "review":
        return ""
    if c.release_day is None or d is not state.latest(c):
        return " (not yours)" + (f"; your message: \"{ONE_LINE(d.rejection)}\"" if d.rejection else "")
    back = state.refunded(c)
    text = f"; {F(c.paid, 2)} paid to {c.manager}" + (f" and {F(back, 2)} back to you" if back > 1e-9 else "")
    if state.owes_fee(m, c):
        text += (f"; yours once you pay the arbiter's fee {CR(state.dispute_fee(m, c))} to the arbiter, not to the "
                 f"manager (pay_escrow_arbiter)")
    return text


def _details(m, c):
    """The buyer's delivery or submission lines, the closed line and the message lines of a contract (8.2 to
    8.4)."""
    lines, messages = [], []
    for n, d in enumerate(c.deliveries, 1):
        if c.form == "plain":
            line = f"{d.label} delivered day {INT(d.day)}{' (in use)' if d is state.in_use(c) else ''}"
        else:
            line = f"{d.label} submitted day {INT(d.day)} (#{INT(n)}): {_outcome(m, c, d)}{_extra(m, c, d)}"
        lines.append(f"{line} | {_readings(m, c, d.label, 'buyer')}")
        head = f"with delivery #{INT(n)} ({d.label}, day {INT(d.day)}): "
        if d.quality is not None:
            messages.append(f"{head}stated quality {G(d.quality)}"
                            + (f"; message \"{ONE_LINE(d.message)}\"" if d.message else ""))
        elif d.message:
            messages.append(f"message {head}\"{ONE_LINE(d.message)}\"")
    if c.closed:
        lines.append(f"escrow closed unreleased: its price {F(state.refunded(c), 2)} came back to you")
    k = len(messages) - m.cfg["view"]["messages_shown"]
    if k > 0:
        messages[:k] = [f"({unit(INT(k), 'earlier delivery message')} not listed)"]
    return lines + messages


def _buyer_item(m, c):
    """A contract item of the buyer's active contracts (8.1)."""
    t = m.tasks[c.task]
    late = ""
    if not state.settled(c) and not c.deliveries and m.day > t.due:
        past = (f", not the contract's deadline day {INT(c.deadline)}" if m.day <= c.deadline
                else f" and the contract's deadline day {INT(c.deadline)}")
        late = f" | PAST DUE (the task's due day {INT(t.due)}{past}): {_tomorrow(m, t)}"
    line = (f"{c.id} with {c.manager} | {_worth(m, t)}{late} | {_price(c)} | signed day {INT(c.signed)} | "
            f"{_deadline(m, c)} | {_status(m, c, 'buyer')}{'' if c.deliveries else ' (nothing to test yet)'}"
            f"{_closed(m, c, 'buyer')}{_end(m, c)}")
    return _item(line, ([_escrow_days(m, c, "buyer")] if state.held(c) else []) + _details(m, c))


def active_contracts(m, p):
    """"Your active contracts", with the manager's bids note (7, 8)."""
    mine = [c for c in state.contracts_of(m, p.id) if active(m, p, c)]
    if p.role == "buyer":
        return ACTIVE, [_buyer_item(m, c) for c in mine]
    items = [_manager_item(m, c) for c in mine]
    posts = sorted({b.post for b in m.bids.values() if b.manager == p.id and b.status == "open"})
    if posts and not items:
        items = [f"(your bids on {', '.join(posts)} are not contracts until their buyers accept them)"]
    return ACTIVE, items


# 9. The escrow days line


def _escrow_days(m, c, role):
    """The escrow days line of a held escrow (9)."""
    manager, today, close = role == "manager", m.day, state.close_day(m, c)
    review = m.cfg["contracts"]["escrow"]["review_days"]
    ago = "" if today <= c.deadline else f", {rel(m, c.deadline)}"
    parts = [f"full price if submitted by day {INT(c.deadline)} (the deadline day{ago})"]
    d = state.under_review(c)
    if d or (not state.open_dispute(c) and today < close):
        what = f"{d.label} is released at the start of day {INT(state.auto_day(m, d))}" if d else \
            f"a submission made today is released at the start of day {INT(today + review)}"
        undecided = "the buyer neither approves nor rejects it" if manager else "you neither approve nor reject it"
        parts.append(f"{what} if {undecided} (sooner if the market ends first)")
    if today < close:
        parts.append(f"with nothing released, closes at the start of day {INT(close)} (later if a decision or "
                     f"dispute is pending then)" + (", and takes no new submission from that day" if manager else ""))
    else:
        parts.append(f"closes once no decision or dispute is pending (its close day {INT(close)} has come)"
                     + ("; no new submission" if manager else ""))
    parts.append(f"dispute fee {unit(CR(state.dispute_fee(m, c)), 'credit')}")
    return ESCROW_DAYS + " | ".join(parts)


# 10. Active or finished


def active(m, p, c):
    """Whether a contract is active in a participant's view, else finished (10.1, 10.2)."""
    view, today = m.cfg["view"], m.day
    if c.ended is not None:
        return False
    if state.held(c):
        return True
    if p.role == "manager":
        used = {d.label for d in c.deliveries}
        if any(not state.arrived(m, r) or (r.label not in used and r.arrival >= today - view["recent_days"])
               for r in c.results):
            return True
    elif c.release_day is not None:
        return state.owes_fee(m, c) or today <= state.due(m, c) or c.release_day >= today - 1
    if not c.deliveries:
        return today - listing_day(m, c) < view["overdue_days"]
    return today <= listing_day(m, c) or (p.role == "buyer" and state.latest(c).day >= today - 1)


# 11. "Your finished contracts"


def _owed(c):
    """A contract delivered and not paid in full (11.1)."""
    return c.form == "plain" and c.ended is None and bool(c.deliveries) and not state.paid_in_full(c)


def _older(m, p, older):
    """The older contracts' summary line (11.4). An unpaid total that is not finite stops the episode (config.md,
    6, "Finiteness during an episode")."""
    ended = sum(c.ended is not None for c in older)
    escrow = [c for c in older if c.ended is None and c.form == "escrow"]
    plain = [c for c in older if c.ended is None and c.form == "plain"]
    owed = [c for c in plain if _owed(c)]
    full = sum(bool(c.deliveries) and state.paid_in_full(c) for c in plain)
    unpaid = state.total([c.price - c.paid for c in owed],
                         f"the unpaid total of the older finished contracts of {p.id}")
    text = (f"Older finished contracts not listed: {INT(len(older))} ("
            f"{f'{INT(ended)} ended by the buyer; ' if ended else ''}{INT(len(escrow))} escrow: "
            f"{INT(sum(c.release_day is not None for c in escrow))} released, "
            f"{INT(sum(c.closed is not None for c in escrow))} refunded; of the {INT(len(plain))} plain, "
            f"{INT(full)} paid in full; {INT(len(owed))} delivered and not paid in full, with "
            f"{F(unpaid, 2)} credits of their prices unpaid; "
            f"{INT(sum(not c.deliveries for c in plain))} never delivered).")
    if owed:
        other = "manager" if p.role == "buyer" else "buyer"
        names = [f"{c.id} ({getattr(c, other)}, {F(c.price - c.paid, 2)} unpaid)" for c in reversed(owed)]
        text += f" Delivered and not paid in full: {_named(m, names)}."
    return text


def finished_contracts(m, p):
    """"Your finished contracts": the latest, the buyer's kept ones and the older contracts' summary (11)."""
    view, buyer = m.cfg["view"], p.role == "buyer"
    done = [c for c in state.contracts_of(m, p.id) if not active(m, p, c)]
    if not done:
        return f"{FINISHED} ---", []
    done.sort(key=lambda c: (state.latest(c).day if c.deliveries else listing_day(m, c), c.signed))
    others, latest = done[:-view["finished_shown"]], done[-view["finished_shown"]:]
    kept = [c for c in others if _owed(c)][-view["overflow_ids"]:] if buyer else []
    shown = {c.id for c in kept + latest}
    items = []
    for c in done:
        if c.id not in shown:
            continue
        line = f"{c.id} with {c.manager if buyer else c.buyer} | {c.task} | "
        if buyer:
            line += f"worth {INT(m.tasks[c.task].worth)} | "
        line += f"{_price(c)} | deadline day {INT(c.deadline)} | {_status(m, c, p.role)}"
        if not buyer:
            items.append(f"{line} | {unit(INT(len(c.results)), 'result')}")
        elif _owed(c):
            items.append(_item(line, _details(m, c)))
        elif c.deliveries and (c.form == "plain" or c.release_day is not None):
            items.append(f"{line} | {_readings(m, c, state.in_use(c).label, 'buyer')}")
        else:
            items.append(line)
    older = [c for c in done if c.id not in shown]
    if older:
        items.append(_older(m, p, older))
    also = f", and {INT(len(kept))} older delivered and not paid in full" if kept else ""
    return f"{FINISHED} (latest {INT(len(latest))} of {INT(len(done))}{also}) ---", items


# 1. Where the sections sit


def sections(m, p):
    """The market sections of a participant's view, in its role's order (1)."""
    if p.role == "manager":
        return [open_posts(m, p), production(m, p), active_contracts(m, p), finished_contracts(m, p)]
    return [tasks(m, p), active_contracts(m, p), finished_contracts(m, p), open_posts(m, p)]
