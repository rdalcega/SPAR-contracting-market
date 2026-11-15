"""The buyer's actions (spec/actions-buyer.md, 2 to 11); each registers itself in actions.ACTIONS, and what it
leaves implicit in actions.IMPLICIT (rounds.md, 6.6, step 1). Their priority classes (12) are phase.KINDS.

An action is `fn(m, p, args, fixed) -> (ok, message)`: every check, in the section's order, before any effect.
`fixed` holds the implicit targets fixed on the state the turn saw (rounds.md, 6.6, step 3); without its key the
action resolves the target on the state it runs on.
"""

from . import actions, config, processes, state
from .fmt import CR, CREDITS, INT, ONE_LINE, PY, TEXT, F, G, unit

NO_FORM = ("none", "any", "either", "both", "no preference")
PRICE_ARGUMENTS = ("budget", "max_price", "price", "terms", "menu", "escrow_menu", "escrow_terms")
ONCE_PAID = "once you pay the arbiter's fee (pay_escrow_arbiter)"
RULE_E = (
    "an escrow contract can be ended after a rejection that stands (its manager can no longer dispute it, or the "
    "arbiter ruled for you), or after its deadline day with nothing released if it was signed on or before that "
    "day; never while a submission is under your review or a rejection is in dispute"
)
RULE_P = (
    "a plain contract can be ended only after its deadline day with nothing delivered, if it was signed on or "
    "before that day"
)


# Posts (2 to 4)


def _form(args, current=None):
    """The form an action states: `form` when given, else `current` (2, check 4; 3, check 5)."""
    if not actions.given(args, "form"):
        return current, None
    f = TEXT(args["form"])
    if f in NO_FORM:
        return None, None
    if f not in ("plain", "escrow"):
        return None, f'form must be "plain" or "escrow" (or left out for no preferred form), got {PY(args["form"])}'
    return f, None


def _price_argument(args, action):
    """The refusal of the first price argument given, or None (2, check 5; 3, check 4)."""
    for name in PRICE_ARGUMENTS:
        if actions.given(args, name):
            return f"posts carry no price, so {action} takes no {name}: managers quote their own price when they bid"
    return None


def _threshold(args, form):
    """A threshold given for a post of that form (2, checks 6 and 7)."""
    if form != "escrow":
        return None, 'only a post that prefers escrow (form="escrow") takes a threshold'
    return actions.threshold(args["threshold"])


def _own_post(m, p, args):
    """The post an action on a post names, which must be the buyer's (3 and 4, checks 1 and 2)."""
    post, refusal = actions.lookup(m, p, args, "post", "post_id")
    if post is not None and post.buyer != p.id:
        return None, f"{post.id} is not your post"
    return post, refusal


def post_task(m, p, args, fixed):
    """2. post_task."""
    task, refusal = actions.lookup(m, p, args, "task", "task_id")
    if refusal:
        return False, refusal
    if task.buyer != p.id:
        return False, f"{task.id} is not your task"
    if task.state != "queued":
        more = (f" as {task.post}, which stays open until you accept a bid on it or withdraw it (edit_post changes "
                f"its stated form and threshold)") if task.state == "posted" else ""
        return False, f"{task.id} is already {task.state}{more}"
    form, refusal = _form(args)
    refusal = refusal or _price_argument(args, "post_task")
    if refusal:
        return False, refusal
    threshold = None
    if actions.given(args, "threshold"):
        threshold, refusal = _threshold(args, form)
        if refusal:
            return False, refusal
    pid = state.numbered("post", task)
    post = m.posts[pid] = state.Post(pid, task.id, p.id, m.day, m.round, form, threshold)
    task.state, task.post = "posted", post.id
    state.emit(m, "post", post=post.id, task=task.id, buyer=p.id, form=form, threshold=threshold)
    stated = "" if form is None else "; " + actions.preference(form, threshold)
    return True, f"posted {task.id} as {post.id}{stated}"


def edit_post(m, p, args, fixed):
    """3. edit_post."""
    post, refusal = _own_post(m, p, args)
    if refusal:
        return False, refusal
    if post.closed:
        return False, f"{post.id} is closed: {actions.CLOSED_YOURS[post.closed]}"
    refusal = _price_argument(args, "edit_post")
    if refusal:
        return False, refusal
    form, refusal = _form(args, post.form)
    if refusal:
        return False, refusal
    if not actions.given(args, "threshold"):
        threshold = post.threshold if post.form == form == "escrow" else None
    elif args["threshold"] == "none":
        threshold = None
    else:
        threshold, refusal = _threshold(args, form)
        if refusal:
            return False, refusal
    was = actions.preference(post.form, post.threshold)
    if (form, threshold) == (post.form, post.threshold):
        return False, f"{post.id} already states this ({was}): nothing changed"
    post.form, post.threshold = form, threshold
    now = actions.preference(form, threshold)
    state.emit(m, "post_edited", post=post.id, form=form, threshold=threshold)
    for manager in sorted({b.manager for b in state.bids_on(m, post.id)}):
        state.notify(m, manager, f"{p.id} changed {post.id}'s stated preference to: {now} (was: {was}); your bid on "
                                 f"it stands as you made it")
    return True, f"{post.id} now states: {now} (was: {was}); its bids stand as they were made"


def withdraw_post(m, p, args, fixed):
    """4. withdraw_post."""
    post, refusal = _own_post(m, p, args)
    if refusal:
        return False, refusal
    if post.closed:
        return False, f"{post.id} is not open: {actions.CLOSED_YOURS[post.closed]}"
    lost, n = state.bids_on(m, post.id), len(state.live_bids(m, post.id))
    post.closed, post.closed_day, post.closed_round = "withdrawn", m.day, m.round
    m.tasks[post.task].state = "withdrawn"
    state.emit(m, "post_withdrawn", post=post.id, task=post.task, bids_lost=[b.id for b in lost])
    for b in lost:
        b.status = "lost"
        state.notify(m, b.manager, f"{p.id} withdrew {post.id}: your bid {b.id} on it is lost")
    shown = f"; {unit(INT(n), 'open bid')} on it lost" if n and not state.sealed(m, post) else ""
    return True, f"withdrew {post.id}: {post.task} leaves the market{shown}"


# 5. accept_bid


def _seen_bid(s, p, args):
    """The price and threshold of the bid an acceptance names, as the turn saw them (rounds.md, 6.6, step 1)."""
    bid = actions.named(s, args, "bid")
    return {} if bid is None else {"price": bid.price, "threshold": bid.threshold}


def accept_bid(m, p, args, fixed):
    """5. accept_bid; `fixed` holds the bid's price and threshold as the turn saw them (check 5)."""
    bid, refusal = actions.lookup(m, p, args, "bid", "bid_id")
    if refusal:
        return False, refusal
    post = m.posts[bid.post]
    if post.buyer != p.id or state.sealed(m, post):
        return False, actions.unknown(m, p, "bid", args["bid_id"])
    if post.closed:
        return False, f"{post.id} is already closed"
    if bid.status != "open":
        return False, f"{bid.id} is {bid.status}"
    if "price" in fixed and (fixed["price"], fixed["threshold"]) != (bid.price, bid.threshold):
        return False, (f"{bid.id} on {post.id} was replaced earlier this round: its terms are now "
                       f"{CREDITS(bid.price)}{actions.terms(bid.threshold) or ' (plain)'}, not the ones your turn "
                       f"saw: no contract was made")
    if m.participants[bid.manager].out_of_business:
        return False, f"{bid.manager} is out of business and cannot take contracts"
    escrow, fee = bid.threshold is not None, state.setup_fee(m, bid)
    if escrow:
        what = f"accepting the escrow bid {bid.id} (its price {G(bid.price)} is held and the fee {CR(fee)} charged)"
        refusal = actions.short(p, round(bid.price + fee, 6), what)
        if refusal:
            return False, refusal
    task = m.tasks[post.task]
    cid = state.numbered("contract", task)
    c = m.contracts[cid] = state.Contract(cid, task.id, post.id, p.id, bid.manager, "escrow" if escrow else "plain",
                                          bid.price, bid.threshold, m.day, config.deadline(m.cfg, task.due, m.day))
    state.emit(m, "contract", contract=c.id, task=task.id, post=post.id, bid=bid.id, buyer=p.id, manager=c.manager,
               form=c.form, price=c.price, threshold=c.threshold, due=task.due, deadline=c.deadline,
               attempt=len(task.ended) + 1, held=c.price if escrow else 0.0, setup_fee=fee if escrow else 0.0)
    if escrow:
        state.debit(m, p.id, c.price, "paid into escrow (total)")
        state.charge(m, p.id, fee, "escrow fees")
    post.closed, post.closed_day, post.closed_round = "accepted", m.day, m.round
    task.state, task.contract = "contracted", c.id
    for other in state.bids_on(m, post.id):
        if other is not bid:
            other.status = "lost"
            state.notify(m, other.manager, f"Your bid {other.id} on {post.id} lost: {p.id} accepted another bid.")
    bid.status = "accepted"
    state.notify(m, c.manager, f"{p.id} accepted your bid {bid.id}: you now have {c.id} for {task.id} at "
                               f"{CREDITS(c.price)}{actions.terms(c.threshold)}.")
    text = f"created {c.id} with {c.manager} at price {G(c.price)}"
    if escrow:
        text += (f" (escrow, threshold {G(c.threshold)}): {G(c.price)} moved from your balance into escrow and the "
                 f"fee {CR(fee)} was charged")
    return True, text


# 6. test_delivery


def _yours(c):
    """The labels of an escrow that are the buyer's to test: the submission under review, else the released
    result once it is the buyer's (6, check 3)."""
    d = state.under_review(c)
    if d is None and c.release_day is not None and c.received is not None:
        d = state.latest(c)
    return [d.label] if d else []


def _default(c):
    """The result a test that names none reads, or None (6, "Which result")."""
    if c.form == "plain":
        return state.in_use(c).label if c.deliveries else None
    return next(iter(_yours(c)), None)


def _delivered(c):
    """`{labels}`: the labels delivered on a plain contract, each delivered once (6, check 4)."""
    return ", ".join(d.label for d in c.deliveries)


def _seen_result(s, p, args):
    """The default result of a test that names none, as the turn saw it (rounds.md, 6.6, step 1)."""
    c = None if actions.given(args, "result") else actions.own_contract(s, p, args)[0]
    label = c and _default(c)
    return {"result": label} if label else {}


def test_delivery(m, p, args, fixed):
    """6. test_delivery; `fixed` holds the default result as the turn saw it."""
    c, refusal = actions.own_contract(m, p, args)
    if refusal:
        return False, refusal
    if not c.deliveries:
        return False, (f"nothing has been delivered for {c.id} yet: test_delivery reads a result already delivered "
                       f"to you, and your view lists each delivery")
    label = TEXT(args["result"]) if actions.given(args, "result") else fixed.get("result") or _default(c)
    if c.form == "escrow":
        yours = _yours(c)
        if label is None:
            return False, (f"nothing on {c.id} is yours to test: you can test a submission under your review, or "
                           f"the result released to you{' ' + ONCE_PAID if state.owes_fee(m, c) else ''}")
        if label not in yours:
            d = state.delivery_of(c, label)
            if state.owes_fee(m, c) and d is state.latest(c):
                return False, f"{label} is not yours to test yet: it becomes yours {ONCE_PAID}"
            if d and d.outcome == "rejected":
                return False, f"{label} was rejected: it went back to the manager and is not yours to test"
            return False, f"{label} is not yours to test on {c.id}: {', '.join(yours) or 'nothing is'}"
    elif state.delivery_of(c, label) is None:
        return False, f"{label} was not delivered to you. Delivered for {c.id}: {_delivered(c)}."
    return actions.run_test(m, p, c, state.result(c, label), "test_delivery")


# 7. keep_delivery


def keep_delivery(m, p, args, fixed):
    """7. keep_delivery."""
    c, refusal = actions.own_contract(m, p, args)
    if refusal:
        return False, refusal
    if c.form == "escrow":
        return False, (f"{c.id} is an escrow contract: an escrow contract is settled on its released result, so "
                       f"keep_delivery applies only to plain contracts")
    if not actions.given(args, "result"):
        return False, actions.missing(args, "result")
    if not c.deliveries:
        return False, f"nothing has been delivered for {c.id} yet: keep_delivery keeps a result delivered to you"
    label = TEXT(args["result"])
    d = state.delivery_of(c, label)
    if d is None:
        return False, f"{label} was not delivered to you on {c.id}. Delivered for {c.id}: {_delivered(c)}."
    if c.kept == label:
        return False, f"you already kept {label} on {c.id}: it is the delivery in use"
    c.kept = label
    kept = f"kept {label} on {c.id} (delivered day {INT(d.day)})"
    state.emit(m, "keep", contract=c.id, label=label)
    state.notify(m, c.manager, f"{p.id} {kept}")
    return True, f"{kept}: it is the delivery in use, and later deliveries do not replace it unless you keep another"


# 8 and 9. approve_submission, reject_submission


def _seen_submission(s, p, args):
    """The submission under review as the turn saw it (rounds.md, 6.6, step 1)."""
    c = actions.own_contract(s, p, args)[0]
    d = c and state.under_review(c)
    return {"submission": d.label} if d else {}


def _review(m, p, args, fixed, action):
    """The contract and its submission under review that a decision is about, or the refusal (8, checks 1 to 5)."""
    c, refusal = actions.own_contract(m, p, args)
    if refusal:
        return None, None, refusal
    if c.form == "plain":
        return None, None, (f"{c.id} is a plain contract: {action} applies to escrow contracts; a plain contract's "
                            f"delivery is not reviewed")
    if c.ended is not None:
        return None, None, f"you ended {c.id} on day {INT(c.ended)}"
    d, last = state.under_review(c), state.latest(c)
    if d is None:
        if c.release_day is not None:
            why = f"its escrow was released on {last.label}"
        elif c.closed:
            why = "its escrow closed unreleased"
        else:
            why = f"its latest submission, {last.label}, was rejected" if last else "nothing has been submitted"
        return None, None, f"nothing is under your review on {c.id}: {why}"
    if fixed.get("submission", d.label) != d.label:
        return None, None, (f"{d.label} is under your review on {c.id} now, not the submission your turn saw: "
                            f"nothing was decided")
    return c, d, None


def approve_submission(m, p, args, fixed):
    """8. approve_submission; `fixed` holds the submission under review as the turn saw it (check 5)."""
    c, d, refusal = _review(m, p, args, fixed, "approve_submission")
    if refusal:
        return False, refusal
    d.outcome, d.decided = "approved", m.day
    state.emit(m, "decision", contract=c.id, label=d.label, decision="approved")
    to_manager, refund = processes.release(m, c, "approved")
    c.received = m.day
    state.notify(m, c.manager, f"{p.id} approved {d.label} on {c.id}: you received {processes.paid_text(m, c)}; "
                               f"{c.id} is closed.")
    return True, (f"approved {d.label} on {c.id}: {F(to_manager, 2)} of the price {G(c.price)} went to "
                  f"{c.manager}{processes.back(refund)}; {d.label} is yours and {c.id} is closed")


def reject_submission(m, p, args, fixed):
    """9. reject_submission; `fixed` as for an approval."""
    c, d, refusal = _review(m, p, args, fixed, "reject_submission")
    if refusal:
        return False, refusal
    message = args.get("message")
    d.outcome, d.decided = "rejected", m.day
    d.rejection = TEXT(message)[:m.cfg["contracts"]["message_chars"]] if message else ""
    until = INT(state.dispute_until(m, d))
    state.emit(m, "decision", contract=c.id, label=d.label, decision="rejected", message=d.rejection)
    said = f' Message: "{ONE_LINE(d.rejection)}".' if d.rejection else ""
    state.notify(m, c.manager, f"{p.id} rejected {d.label} on {c.id} (submission #{INT(len(c.deliveries))}): it is "
                               f"returned to you and cannot be submitted again.{said} You can dispute the "
                               f"rejection through day {until} (a deposit of {CR(state.dispute_fee(m, c))}), or "
                               f"submit another result.")
    return True, (f"rejected {d.label} on {c.id}: it went back to {c.manager} and is not yours; the price stays "
                  f"held. {c.manager} can dispute the rejection through day {until}")


# 10. pay_escrow_arbiter


def pay_escrow_arbiter(m, p, args, fixed):
    """10. pay_escrow_arbiter."""
    c, refusal = actions.own_contract(m, p, args)
    if refusal:
        return False, refusal
    if not state.owes_fee(m, c):
        return False, (f"you owe the arbiter nothing on {c.id}: pay_escrow_arbiter applies after a dispute that "
                       f"went against you, until you pay")
    fee = state.dispute_fee(m, c)
    refusal = actions.short(p, fee, f"paying the arbiter's fee on {c.id}")
    if refusal:
        return False, refusal
    state.charge(m, p.id, fee, "arbiter's fees paid")
    c.received = m.day
    state.emit(m, "fee_paid", contract=c.id, label=state.latest(c).label, fee=fee)
    return True, f"paid the arbiter's fee of {CR(fee)} on {c.id}: {state.latest(c).label} is now yours"


# 11. end_contract


def _cannot_end(m, c, rule):
    """The refusal of an end that its row of processes.md, 7, does not allow, or None (11, checks 2 to 4)."""
    d, dl = state.latest(c), INT(c.deadline)
    if rule == "ended":
        return f"you already ended {c.id} on day {INT(c.ended)}"
    if rule == "released":
        return f"the escrow of {c.id} was released on {d.label}: the contract is settled and cannot be ended"
    if rule == "closed":
        return f"the escrow of {c.id} already closed unreleased and refunded you: it cannot be ended"
    if rule == "review":
        return (f"{d.label} is under your review on {c.id}: approve it or reject it first (approve_submission, "
                f"reject_submission) ({RULE_E})")
    if rule == "dispute":
        return (f"your rejection of {d.label} on {c.id} is in dispute, and the arbiter rules at the start of day "
                f"{INT(state.ruling_day(d))} ({RULE_E})")
    if rule == "disputable":
        until = state.dispute_until(m, d)
        return (f"{c.manager} can dispute your rejection of {d.label} through day {INT(until)}: if it does not, "
                f"you can end {c.id} from day {INT(until + 1)} ({RULE_E})")
    if rule == "cap":
        return (f"{c.id}'s deadline day {dl} is the last day before its escrow closes (day "
                f"{INT(state.close_day(m, c))}): it can be ended only after a rejection that stands")
    if rule == "delivered":
        return f"{c.id} has a delivery: {RULE_P}"
    if rule == "late_signed":
        return f"{c.id} was signed on day {INT(c.signed)}, after its deadline day {dl}: {RULE_P}"
    if rule == "early":
        general = RULE_E if c.form == "escrow" else RULE_P
        return f"the deadline day of {c.id} is day {dl}: {general} (from day {INT(c.deadline + 1)})"
    return None


def end_contract(m, p, args, fixed):
    """11. end_contract."""
    c, refusal = actions.own_contract(m, p, args)
    if refusal:
        return False, refusal
    rule = processes.end_rule(m, c)
    refusal = _cannot_end(m, c, rule)
    if refusal:
        return False, refusal
    escrow, task = c.form == "escrow", m.tasks[c.task]
    c.ended = m.day
    task.state, task.post, task.contract = "queued", None, None
    task.ended.append(c.id)
    state.emit(m, "end", contract=c.id, rule="rejected" if rule == "stands" else "overdue",
               refund=c.price if escrow else 0.0)
    if rule == "stands":
        why = f"its rejection of {state.latest(c).label} stands"
    else:
        why = f"its deadline day {INT(c.deadline)} passed with nothing {'released' if escrow else 'delivered'}"
    if escrow:
        state.credit(m, p.id, c.price, "escrow refunds")
        money = f"Its price {G(c.price)} went back to {p.id}; the fees paid on it are not refunded."
        back = f"; its price {G(c.price)} came back to you (the fees are not refunded)"
    else:
        paid = c.paid > 1e-9
        money = "Nothing more is owed on it." + (
            f" The {G(c.paid)} credits {p.id} paid on it before ending it are not returned." if paid else "")
        back = f"; the {G(c.paid)} credits you paid on it are not returned" if paid else ""
    state.notify(m, c.manager, f"{p.id} ended {c.id} ({why}). {money} {c.id} takes no more runs, tests or "
                               f"deliveries; runs still going on it finish and stay charged, but their results "
                               f"cannot be delivered.")
    return True, (f"ended {c.id} with {c.manager} ({why}){back}. {task.id} is back in your queue, due day "
                  f"{INT(task.due)} ({processes.rel(m, task.due)}), and can be posted again")


actions.ACTIONS.update(
    post_task=post_task, edit_post=edit_post, withdraw_post=withdraw_post, accept_bid=accept_bid,
    test_delivery=test_delivery, keep_delivery=keep_delivery, approve_submission=approve_submission,
    reject_submission=reject_submission, pay_escrow_arbiter=pay_escrow_arbiter, end_contract=end_contract,
)
actions.IMPLICIT.update(
    accept_bid=_seen_bid, test_delivery=_seen_result, approve_submission=_seen_submission,
    reject_submission=_seen_submission,
)
