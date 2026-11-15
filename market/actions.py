"""The rules common to every action (spec/actions-buyer.md, 1; actions-manager.md, 1), the dispatcher, and compute
as an action (turns.md, 7.6).

A helper that reads a value returns `(value, None)`, or `(None, text)` with the refusal's text; a helper that only
checks returns the refusal's text or None. A result label is `TEXT` of the argument (1.8). The helpers impose no
order: each action calls them in the order of its own checks.
"""

import math

from . import processes, state
from .fmt import CR, CREDITS, INT, PY, TEXT, F, G, finite, unit
from .reading import has_text

ACTIONS = {}  # name -> fn(m, p, args, fixed) -> (ok, message): filled by the modules imported at the end
IMPLICIT = {}  # name -> fn(s, p, args) -> dict: an action's implicit targets (rounds.md, 6.6 step 1)

OWN = {
    "buyer": (
        "post_task", "accept_bid", "test_delivery", "approve_submission", "reject_submission",
        "pay_escrow_arbiter", "end_contract", "edit_post", "withdraw_post", "keep_delivery",
    ),
    "manager": ("bid", "delegate_run", "run_myself", "test_result", "deliver", "dispute", "withdraw_bid"),
}
NOT_IN_THIS_MARKET = ("verify", "write_code", "self_test", "view_delivery", "note")
NO_NAME = (
    'the action names no action: give its name with "action", as {"action": "<action name>", '
    '"<argument name>": <value>}'
)
CLOSED_YOURS = {"withdrawn": "you withdrew it", "accepted": "a bid on it was accepted"}  # to the post's buyer
CLOSED_THEIRS = {"withdrawn": "its buyer withdrew it", "accepted": "its buyer accepted a bid"}  # to anyone else
OUT_OF_BUSINESS = "you are out of business (balance at or below 0) and cannot act"
COMPUTE_AS_ACTION = (
    "the program did not run: compute is a function you call before act, not an action (at most 1 call a turn)"
)


def can_send(role, mode):
    """Whether the role has send_message in the mode (messaging.md, 1)."""
    return mode in ("within_roles", "all", role + "s")


def own(role, mode):
    """A role's own actions in a mode, in the order a refusal lists them (1.1; rounds.md, 6.2)."""
    return (("send_message",) if can_send(role, mode) else ()) + ("pay",) + OWN[role]


def action_list(role, mode):
    """`{list}`, the actions a refusal shows (1.1)."""
    return ", ".join(own(role, mode))


def name_refusal(name, role, mode, out_of_business):
    """The first refusal of 1.2, steps 1 to 6, that applies to an action's name, or None."""
    if not has_text(name):
        return NO_NAME
    other = "manager" if role == "buyer" else "buyer"
    if name in NOT_IN_THIS_MARKET or (name == "send_message" and mode == "off"):
        return f"'{name}' is not available in this market. Your actions: {action_list(role, mode)}."
    if name not in own(role, mode) + OWN[other] + ("compute",):
        text = f"unknown action '{name}'. Your actions: {action_list(role, mode)}."
        key = name.strip().lower()
        if "contract" in key:
            text += " Contracts are created only when a buyer accepts a bid (accept_bid)."
        if role == "manager" and key.startswith("submit"):
            text += " To deliver a result, or submit it for the buyer's review, use deliver."
        return text
    if name in OWN[other]:
        return f"'{name}' is not available to a {role}: it is a {other}'s action"
    if out_of_business:
        return OUT_OF_BUSINESS
    return None


def shown(name):
    """`{shown}` of a result line (turns.md, 9): the name trimmed, `?` with no name."""
    return name.strip() if has_text(name) else "?"


def result_line(name, ok, message):
    """An action's line in its participant's results (1.3; turns.md, 9)."""
    return f"- {shown(name)}: {'OK' if ok else 'ERROR'}: {message}"


def stopped_note(n):
    """The note after the last line of a participant stopped in a phase (turns.md, 9)."""
    return f"(note: your remaining {unit(INT(n), 'action')} did not run: you are out of business)"


# Arguments (1.4)


def given(args, name):
    """Whether an argument counts as given (1.4): present, not null and not the empty text."""
    return args.get(name) is not None and args[name] != ""


def missing(args, name):
    """The refusal of a required argument that is not given (1.4), or None."""
    return None if given(args, name) else f"missing argument '{name}'"


# Ids (1.6, 1.7)


def terms(threshold):
    """`{terms}` of a bid: its threshold when it is an escrow bid, else empty (1.6)."""
    return "" if threshold is None else f" with threshold {G(threshold)} (escrow)"


def hint(m, p, kind):
    """`{hint}` of an id that names nothing, by kind, each list in creation order (1.6, step 3)."""
    if kind == "task":
        items = [t.id for t in m.tasks.values() if t.buyer == p.id and t.state == "queued"]
        return f"Your queued (not yet posted) tasks: {', '.join(items) or 'none'}."
    if kind == "post":
        items = [post.id for post in m.posts.values() if post.closed is None]
        return f"Open posts: {', '.join(items) or 'none'}."
    if kind == "bid":
        items = []
        for b in m.bids.values():
            post = m.posts.get(b.post)
            if (b.status == "open" and post is not None and post.buyer == p.id and not state.sealed(m, post)
                    and not m.participants[b.manager].out_of_business):
                items.append(f"{b.id} ({b.manager} at {G(b.price)}{terms(b.threshold)} on {b.post})")
        return f"Open bids on your posts: {', '.join(items) or 'none'}."
    items = [f"{c.id} ({c.task}{'' if c.ended is None else ', ended'})" for c in state.contracts_of(m, p.id)]
    text = f"Your contracts: {', '.join(items) or 'none'}."
    if p.role == "manager":
        text += " A bid becomes a contract only when the buyer accepts it."
        bids = [f"{b.id} on {b.post}" for b in m.bids.values() if b.manager == p.id and b.status == "open"]
        if bids:
            text += f" Your bids not yet accepted: {', '.join(bids)}."
    return text


def unknown(m, p, kind, raw):
    """The refusal of an id that names nothing of its kind (1.6, step 3)."""
    return f"no {kind} with id '{TEXT(raw)}'. {hint(m, p, kind)}"


def lookup(m, p, args, kind, name):
    """The task, post, bid or contract a required id argument names, among all of that kind (1.4, 1.6)."""
    if not given(args, name):
        return None, missing(args, name)
    found = state.find(m, kind, args[name])
    return (None, unknown(m, p, kind, args[name])) if found is None else (found, None)


def named(m, args, kind):
    """The task, post, bid or contract a `{kind}_id` argument names, or None: not given, or naming none (1.4,
    1.6)."""
    return state.find(m, kind, args[kind + "_id"]) if given(args, kind + "_id") else None


def own_contract(m, p, args):
    """The contract an action on a contract names: the agent must be its buyer or its manager, by role (1.7)."""
    c, refusal = lookup(m, p, args, "contract", "contract_id")
    if c is not None and p.id != (c.buyer if p.role == "buyer" else c.manager):
        return None, f"{c.id} is not one of your contracts. {hint(m, p, 'contract')}"
    return c, refusal


# Participants, amounts, thresholds, balances (1.9 to 1.12)


def participant(m, raw):
    """The participant an argument names, written exactly as its id (1.9)."""
    found = m.participants.get(TEXT(raw))
    if found is None:
        ids = ", ".join(q.id for role in ("buyer", "manager") for q in state.of_role(m, role))
        return None, f"no participant with id '{TEXT(raw)}' (participants: {ids})"
    return found, None


def amount(args, name):
    """A required amount, rounded to the cent: a bid's `price` or a payment's `amount` (1.10)."""
    if not given(args, name):
        return None, missing(args, name)
    try:
        x = float(TEXT(args[name]))
    except ValueError:
        return None, f"{name} must be a number, got {PY(args[name])}"
    if not math.isfinite(x) or round(x, 2) <= 0:
        return None, f"{name} must be a positive number"
    return round(x, 2), None


def _number(v):
    """The double a threshold or a stated quality reads as: NaN for a value that is no number (1.11)."""
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return math.nan
    try:
        return float(v)
    except (ValueError, OverflowError):  # an integer too large for a double: the first refusal (README.md)
        return math.nan


def threshold(v):
    """A threshold as given: a whole quality from 0 to 10 (1.11)."""
    t = _number(v)
    if not 0 <= t <= 10:
        return None, f"threshold must be a quality between 0 and 10, got {PY(v)}"
    if not t.is_integer():
        return None, f"threshold must be a whole number between 0 and 10, got {PY(v)}"
    return abs(t), None


def quality(v, decimals):
    """A delivery's stated quality as given, rounded to `tests.decimals` (actions-manager.md, 6.1 check 7)."""
    x = _number(v)
    if not 0 <= x <= 10:
        return None, f"quality must be a number between 0 and 10, got {PY(v)}"
    return abs(round(x, decimals)), None


def short(p, cost, what):
    """The refusal of the balance check for an action that costs `cost`, or None (1.12). A cost that is not finite
    stops the episode (config.md, 6, "Finiteness during an episode")."""
    if not state.covers(p, finite(cost, f"the cost of {what}")):
        return f"insufficient balance for {what}: it costs {CR(cost)}, you have {F(p.balance, 2)}"
    return None


def run_test(m, p, c, r, name):
    """The end of a test of a result by its contract's buyer or manager: the balance check, the charge, one
    reading and the result text (actions-buyer.md, 6; actions-manager.md, 5)."""
    cost = m.cfg["tests"][p.role + "_cost"]
    refusal = short(p, cost, name)
    if refusal:
        return False, refusal
    state.charge(m, p.id, cost, "tests")
    reading = processes.read(m, c, r, p.role)
    return True, (f"{r.label} reading: {F(reading, m.cfg['tests']['decimals'])} (charged {CREDITS(cost)}). Your "
                  f"{processes.readings_text(m, getattr(r, p.role + '_readings'), p.role)}")


def closed_post(m, post, yours=False):
    """`{post} closed {when}, when {why}`, as its buyer (`yours`) or another participant reads it
    (actions-manager.md, 2, check 2; rounds.md, 8.3)."""
    if (post.closed_day, post.closed_round) == (m.day, m.round):
        when = "earlier this round"
    else:
        when = f"on day {INT(post.closed_day)}, round {INT(post.closed_round)}"
    return f"{post.id} closed {when}, when {(CLOSED_YOURS if yours else CLOSED_THEIRS)[post.closed]}"


def preference(form, threshold):
    """A post's stated preference, `{state}`, `{now}` and `{was}` (actions-buyer.md, 3)."""
    if form == "escrow":
        return "prefers escrow" + ("" if threshold is None else f", threshold {G(threshold)}")
    return "prefers a plain contract" if form == "plain" else "no preferred form"


# The manager's shared rules (actions-manager.md, 1.2, 1.3 and 3)


def contract_gone(m, c, closed=True):
    """The refusal of a contract its buyer ended or, with `closed`, whose escrow no longer holds the price (1.2)."""
    if c.ended is not None:
        return f"{c.buyer} ended {c.id} on day {INT(c.ended)}: it takes no more runs, tests or deliveries"
    if closed and c.form == "escrow" and not state.held(c):
        how = "closed unreleased" if c.release_day is None else "was released"
        return f"the escrow of {c.id} {how}: it takes no more runs, tests or submissions"
    return None


def results_text(m, c):
    """`{results}`: a contract's results in the order their runs started (1.3)."""
    if not c.results:
        return f"{c.id} has no results yet (start a run with delegate_run or run_myself)."
    items = [f"{r.label} (arrived)" if state.arrived(m, r) else f"{r.label} (arrives day {INT(r.arrival)})"
             for r in c.results]
    return f"Results of {c.id}: {', '.join(items)}."


def find_result(m, p, c, raw, purpose):
    """The arrived result of the manager's contract that a label names, to be `purpose` (1.3)."""
    label = TEXT(raw)
    r = state.result(c, label)
    if r is None:
        other = next((o for o in m.contracts.values() if o.manager == p.id and state.result(o, label)), None)
        if other is not None:
            return None, (f"{label} is a result of {other.id}, not of {c.id}: a result can be tested, refined or "
                          f"delivered only on the contract it was run for. {results_text(m, c)}")
        return None, f"no result '{label}' in {c.id}. {results_text(m, c)}"
    if not state.arrived(m, r):
        return None, (f"{label} has not arrived yet (it arrives at the start of day {INT(r.arrival)}), "
                      f"so it cannot be {purpose}")
    return r, None


def withdraw_target(s, pid, args):
    """The bid a withdraw_bid finds: the manager's open bid on the post, else its bid on it created last, else
    None (3, "Finding the bid")."""
    post = named(s, args, "post")
    if post is None:
        return None
    mine = [b for b in s.bids.values() if b.post == post.id and b.manager == pid]
    return next((b for b in mine if b.status == "open"), mine[-1] if mine else None)


# The dispatcher (1.2; rounds.md, 6.6)


def _refusal(m, p, name):
    """The refusal of 1.2, steps 1 to 6, for this participant on this state, or None."""
    return name_refusal(name, p.role, m.cfg["message_channels"], p.out_of_business)


def run(m, pid, name, args, fixed=None):
    """One action on a state: steps 1 to 6 of 1.2, then the action's own checks and effects (step 8).

    `fixed` holds the implicit targets fixed on the state the turn saw (rounds.md, 6.6, steps 1 and 3). The action
    runs with `m.actor` set, so that what it credits to another participant waits for the round's end (rounds.md, 7).
    """
    p = m.participants[pid]
    refusal = _refusal(m, p, name)
    if refusal is not None:
        return False, refusal
    m.actor = pid
    try:
        return ACTIONS[name](m, p, args, fixed or {})
    finally:
        m.actor = None


def implicit(s, pid, name, args):
    """The implicit targets of an action, resolved on the state its turn saw (rounds.md, 6.6, step 1)."""
    p = s.participants[pid]
    if _refusal(s, p, name) is not None or name not in IMPLICIT:
        return {}
    return IMPLICIT[name](s, p, args)


def compute(m, p, args, fixed):
    """`compute` sent as an action never runs a program (turns.md, 7.6)."""
    return False, COMPUTE_AS_ACTION


ACTIONS["compute"] = compute

from . import actions_buyer, actions_manager, messaging  # noqa: E402, F401: they register their actions
