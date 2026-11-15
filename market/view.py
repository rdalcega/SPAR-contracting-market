"""The per-turn view: its frame and its general sections (spec/view.md).

The market sections are view_contracts.py's (view-contracts.md) and the Messages section is messaging.py's
(messaging.md, 3). A section is `(title, items)`, each item a text printed as it is (2.3). Only `build` and `undo`
change the market.
"""

import re
import statistics

from . import actions, messaging, state, view_contracts
from .fmt import CR, INT, SUM, F, finite, unit
from .turns import CORRECTION_HEADING, NO_ACTIONS, RESULTS_HEADING

_OPENS = re.compile(r"- [^:\n]+: |\(note: |FORMAT ERROR")
SPENT = (
    "tokens", "employee runs", "your own runs", "tests", "payments made", "paid into escrow (total)",
    "escrow fees", "dispute deposits", "arbiter's fees paid",
)
NOTIFICATIONS = "--- Notifications since your last turn ---"  # 5
MEMORY = "--- Your self-written memory"  # 10: how the memory's title starts, in each of its three forms
FINAL = "Decide what to do now. compute can do the arithmetic for you. End your turn by calling act."


def build(m, pid, summarizer=None, tracker=None):
    """A participant's view of the round that starts, and what building it changes, in order (1; rounds.md, 3,
    step 2). Returns the text and what `undo` takes."""
    p = m.participants[pid]
    shown, saved = p.pending, (p.pending, p.seen, p.unseen, p.last_view)
    p.turns += 1
    p.pending = []
    messages = None
    if actions.can_send(p.role, m.cfg["message_channels"]):
        messages = messaging.section(m, p, summarizer, tracker)
    p.last_view = state.now(m)
    state.count(m, f"{p.role}_turns")
    return render(m, pid, shown, messages), saved


def undo(m, pid, undo):
    """A turn lost to a failed model call: the participant is as if its view had not been built (1; rounds.md,
    10.1)."""
    p = m.participants[pid]
    shown, p.seen, p.unseen, p.last_view = undo
    p.pending = shown + p.pending
    p.turns -= 1


def render(m, pid, notifications, messages=None):
    """The whole text of a view, from the state as it is (2): the header, then each section after one empty line,
    its items after one more and one apart (2.3). `messages` is the Messages section, None when the view has none."""
    p = m.participants[pid]
    ids = [", ".join(q.id for q in state.of_role(m, role)) for role in ("buyer", "manager")]
    lines = [
        f"=== DAY {INT(m.day)}, ROUND {INT(m.round)} of {INT(m.cfg['time']['rounds_per_day'])} today === "
        f"You are {p.id} ({p.role}). Your balance: {F(p.balance, 2)} credits.",
        f"Participants - buyers: {ids[0]}; managers: {ids[1]}.",
    ]
    sections = [
        ("--- Results of your actions in your previous turn ---", [space(p.results)] if p.results else []),
        (NOTIFICATIONS, notifications),
        _payments(m, p),
        *([messages] if messages else []),
        _accounts(m, p),
        _record(m, p),
        *view_contracts.sections(m, p),
        _memory(p),
        ("--- Final instructions ---", [FINAL]),
    ]
    for title, items in sections:
        lines += ["", title]
        if items:
            lines += ["", "\n\n".join(items)]
    return "\n".join(lines)


def space(entries):
    """The results text: its entries, with an empty line before each one that opens an item (4.3)."""
    out = []
    for i, entry in enumerate(entries):
        first = entry.split("\n", 1)[0]
        opens = first in (NO_ACTIONS, CORRECTION_HEADING, RESULTS_HEADING) or _OPENS.match(first)
        if i and opens and entries[i - 1] not in (RESULTS_HEADING, CORRECTION_HEADING):
            out.append("")
        out.append(entry)
    return "\n".join(out)


def _payments(m, p):
    """ "Payments": the latest of the participant's payments, made or received (6)."""
    k = m.cfg["view"]["payments_shown"]
    mine = [x for x in m.payments if p.id in (x.payer, x.payee)]
    items = [f"({INT(len(mine) - k)} earlier not listed)"] if len(mine) > k else []
    for x in mine[-k:]:
        src, dst = ("you" if q == p.id else q for q in (x.payer, x.payee))
        items.append(f"{messaging.stamp(x)} {src} -> {dst}: {unit(CR(x.amount), 'credit')}"
                     f"{f' for {x.contract}' if x.contract else ''}")
    return f"--- Payments (yours and to you; the latest {INT(k)} {'is' if k == 1 else 'are'} shown) ---", items


def _accounts(m, p):
    """ "Your accounts so far (credits)": each category's entries added one by one in the order of their events;
    the received categories in the order of their first entries (8)."""
    sums = {}
    for category, amount in p.ledger:
        sums[category] = finite(sums.get(category, 0.0) + amount, f"the {category} total in the accounts of {p.id}")
    lists = (("Spent", [name for name in SPENT if name in sums]),
             ("Received", [name for name in sums if name not in SPENT]))
    items = [f"{head}: {', '.join(f'{name} {F(sums[name], 2)}' for name in names) or 'nothing'}."
             for head, names in lists]
    if p.role == "buyer":
        held = state.total([c.price for c in state.contracts_of(m, p.id) if state.held(c)],
                           f"what {p.id} holds in escrow")
        items.append(f"Held in escrow now: {F(held, 2)}.")
    return "--- Your accounts so far (credits) ---", items


def _record(m, p):
    """ "Your record with each counterparty (all your contracts so far)": one line per counterparty, by id (9.1)."""
    buyer, by = p.role == "buyer", {}
    for c in state.contracts_of(m, p.id):
        by.setdefault(c.manager if buyer else c.buyer, []).append(c)
    return ("--- Your record with each counterparty (all your contracts so far) ---",
            [_record_line(m, other, by[other], buyer) for other in sorted(by)])


def _record_line(m, other, cs, buyer):
    """The record with one counterparty; `cs` is every contract with it, in creation order (9.2)."""
    who = "you" if buyer else "they"
    plain, escrow = ([c for c in cs if c.form == form] for form in ("plain", "escrow"))
    dl = [c for c in plain if c.deliveries]
    used = [state.in_use(c) for c in dl]
    full, none = sum(state.paid_in_full(c) for c in dl), sum(c.paid <= 1e-9 for c in dl)
    line = (f"{other}: {unit(INT(len(plain)), 'plain contract')}, {unit(INT(len(escrow)), 'escrow contract')}; "
            f"plain: {INT(len(dl))} delivered ({INT(sum(state.days_late(c, d) > 0 for c, d in zip(dl, used)))} late); "
            f"{who} paid {INT(full)} in full, {INT(len(dl) - full - none)} in part, {INT(none)} not at all "
            f"({F(state.total([c.paid for c in dl], 'the payments for delivered work'), 2)} of "
            f"{F(state.total([c.price for c in dl], 'the prices of delivered work'), 2)} for delivered work)")
    if buyer:
        read = [state.result(c, d.label).buyer_readings for c, d in zip(dl, used)]
        means = [statistics.mean(readings) for readings in read if readings]
        if means:
            line += (f"; your readings of their deliveries in use: mean {F(SUM(means) / len(means), 1)} over "
                     f"{unit(INT(len(means)), 'result')}")
        elif dl:
            line += "; you have not tested their deliveries in use"
        stated = [d.quality for d in used if d.quality is not None]
        if stated:
            line += (f"; quality they stated with those deliveries: mean {F(SUM(stated) / len(stated), 1)} over "
                     f"{INT(len(stated))}")
    if escrow:
        released = [c for c in escrow if c.release_day is not None]
        subs = [d for c in escrow for d in c.deliveries]
        ruled = [d.dispute.winner for d in subs if d.dispute and d.dispute.winner]
        line += (f"; escrow: {INT(len(released))} released "
                 f"({INT(sum(state.days_late(c, state.latest(c)) > 0 for c in released))} submitted late; "
                 f"{INT(sum(d.outcome == 'auto' for d in subs))} with no decision), "
                 f"{unit(INT(sum(d.outcome == 'rejected' for d in subs)), 'submission')} rejected, "
                 f"{unit(INT(len(ruled)), 'dispute')} ruled ({INT(ruled.count('manager'))} for the manager), "
                 f"{INT(sum(c.closed is not None and c.ended is None for c in escrow))} closed unreleased and "
                 f"refunded")
    parts = [part for c in cs for part in state.beyond(m, c)]
    if parts:
        beyond = state.total(parts, "the payments beyond the prices")
        line += (f"; other payments on your contracts: {who} paid {F(beyond, 2)} on plain contracts beyond what "
                 f"was owed of their prices ({unit(INT(len(parts)), 'payment')})")
    ended = sum(c.ended is not None for c in cs)
    if ended:
        line += f"; {INT(ended)} ended by {'you' if buyer else 'them'}"
    return line


def _memory(p):
    """ "Your self-written memory": never written, written by the previous turn's decide reply, or older (10)."""
    memory = p.memory
    if memory is None:
        return f"{MEMORY} ---", []
    last = memory.turn == p.turns - 1 and not memory.from_correction
    return (f"{MEMORY} (last updated day {INT(memory.day)}, round {INT(memory.round)}"
            f"{', with your last actions, before their results' if last else ''}) ---", [memory.text])
