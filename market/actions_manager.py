"""The manager's actions and pay (spec/actions-manager.md, 2 to 8); each registers itself in actions.ACTIONS.

Every action is `fn(m, p, args, fixed) -> (ok, message)`: its checks in the spec's order, the first that applies
giving the refusal before anything changes, then its effects, its notifications and its success text. The classes
and clash rules of section 9 order a phase (rounds.md, 6.3 to 6.5); an action here runs all its own checks on the
state it is given, whatever ran before it.
"""

from . import actions, processes, state
from .fmt import CR, INT, ONE_LINE, TEXT, F, G, unit


# 2. bid


def _closed(m, post):
    """The refusal of a bid on a closed post (2, check 2)."""
    now = m.posts.get(m.tasks[post.task].post)
    again = now is not None and now.id != post.id and now.closed is None
    return actions.closed_post(m, post) + (f"; its task is posted again as {now.id} (open)" if again else "")


def bid(m, p, args, fixed):
    """Place a bid on an open post, or replace the manager's open bid on it (2). Class 6."""
    post, refusal = actions.lookup(m, p, args, "post", "post_id")
    if refusal:
        return False, refusal
    if post.closed is not None:
        return False, _closed(m, post)
    price, refusal = actions.amount(args, "price")
    threshold = None
    if not refusal and actions.given(args, "threshold"):
        threshold, refusal = actions.threshold(args["threshold"])
    if refusal:
        return False, refusal
    b = state.own_bid(m, post.id, p.id)
    verb = "updated" if b else "placed"
    if b:
        b.price, b.threshold = price, threshold
    else:
        bid_id = state.new_bid_id(m)
        b = m.bids[bid_id] = state.Bid(bid_id, post.id, p.id, price, threshold)
    state.emit(m, "bid", bid=b.id, post=post.id, manager=p.id, price=price, threshold=threshold,
               form="plain" if threshold is None else "escrow", verb=verb)
    text = f"{b.id} on {post.id} at {G(price)}{actions.terms(threshold)}"
    if not state.sealed(m, post):
        state.notify(m, post.buyer, f"{p.id} {verb} bid {text}")
    note = " (bid takes no message: yours was not sent)" if actions.given(args, "message") else ""
    return True, f"{verb} {text}{note}"


# 3. withdraw_bid


def _withdraw_implicit(s, p, args):
    """The bid a withdraw_bid picks on the state its turn saw (rounds.md, 6.6, step 1)."""
    b = actions.withdraw_target(s, p.id, args)
    return {} if b is None else {"bid": b.id}


def withdraw_bid(m, p, args, fixed):
    """Withdraw the manager's open bid on a post (3). Class 5."""
    post, refusal = actions.lookup(m, p, args, "post", "post_id")
    if refusal:
        return False, refusal
    b = m.bids.get(fixed["bid"]) if "bid" in fixed else actions.withdraw_target(m, p.id, args)
    if b is None:
        return False, f"you have no bid on {post.id}"
    if b.status == "accepted":
        contract = "contract" + post.id.removeprefix("post")  # actions-buyer.md, 5, effect 1
        return False, f"{b.id} on {post.id} was accepted: it is a contract now ({contract}) and cannot be withdrawn"
    if b.status != "open":
        return False, f"{b.id} on {post.id} is {b.status}: there is nothing to withdraw"
    b.status = "withdrawn"
    state.emit(m, "bid_withdrawn", bid=b.id, post=post.id, manager=p.id)
    if not state.sealed(m, post):
        state.notify(m, post.buyer, f"{p.id} withdrew its bid {b.id} on {post.id}")
    return True, f"withdrew {b.id} on {post.id}; bidding on {post.id} again would place a new bid"


# 4. delegate_run and run_myself


def _busy(m, p, worker):
    """The refusal of a run when the worker's runs in flight fill its capacity (4, check 4), or None."""
    flying, cap = state.in_flight(m, p.id, worker), m.cfg["production"][worker]["capacity"]
    if len(flying) < cap:
        return None
    if worker == "manager":
        c, r = flying[0]
        return f"you are busy on {r.label} ({c.id}) until day {INT(r.arrival)}; you can do {INT(cap)} run(s) at a time"
    runs = ", ".join(f"{r.label} on {c.id}, arrives day {INT(r.arrival)}" for c, r in flying)
    return f"your employee is busy: {INT(len(flying))} of {unit(INT(cap), 'run')} in flight ({runs})"


def _run(m, p, args, name, worker, category):
    """Start a run of a worker on one of the manager's contracts (4)."""
    c, refusal = actions.own_contract(m, p, args)
    refusal = refusal or actions.contract_gone(m, c)
    parent = None
    if not refusal and actions.given(args, "refine"):
        parent, refusal = actions.find_result(m, p, c, args["refine"], "refined")
    refusal = refusal or _busy(m, p, worker)
    if not refusal and len(c.results) >= 4096:
        refusal = f"{c.id} has no result labels left ({INT(len(c.results))} results)"
    if refusal:
        return False, refusal
    kind = "refine" if parent else "fresh"
    run = processes.draw_run(m, c, worker, kind, parent)
    refusal = actions.short(p, run["cost"], name)
    if refusal:
        return False, refusal
    r = processes.start_run(m, c, worker, kind, parent, run)
    state.charge(m, p.id, run["cost"], category)
    return True, (f"started {r.label} on {c.id} ({processes.origin(r)}): it arrives at the start of day "
                  f"{INT(r.arrival)}; charged {F(run['cost'], 2)} credits")


def delegate_run(m, p, args, fixed):
    """A run by the manager's employee (4). Class 3."""
    return _run(m, p, args, "delegate_run", "employee", "employee runs")


def run_myself(m, p, args, fixed):
    """A run by the manager itself (4). Class 3."""
    return _run(m, p, args, "run_myself", "manager", "your own runs")


# 5. test_result


def test_result(m, p, args, fixed):
    """One reading of an arrived result of one of the manager's contracts (5). Class 3."""
    c, refusal = actions.own_contract(m, p, args)
    refusal = refusal or actions.contract_gone(m, c) or actions.missing(args, "result")
    if refusal:
        return False, refusal
    r, refusal = actions.find_result(m, p, c, args["result"], "tested")
    if refusal:
        return False, refusal
    return actions.run_test(m, p, c, r, "test_result")


# 6. deliver


def _not_taken(m, c, label):
    """The refusal of a result the contract does not take now (6.1, checks 4 and 5), or None."""
    before = state.delivery_of(c, label)
    if c.form == "plain":
        if before:
            return (f"{label} was already delivered for {c.id} on day {INT(before.day)}: on a plain contract a "
                    f"result is delivered once, and the buyer can keep it")
        return None
    review, disputed = state.under_review(c), state.open_dispute(c)
    if c.release_day is not None:
        return (f"the escrow of {c.id} was released on {state.latest(c).label}: the contract is closed and takes "
                f"no more submissions")
    if c.closed:
        return f"the escrow of {c.id} closed unreleased and refunded its buyer: it takes no more submissions"
    if review:
        return (f"{review.label} is under {c.buyer}'s review on {c.id}: one submission at a time (it is released "
                f"at the start of day {INT(state.auto_day(m, review))} if the buyer neither approves nor rejects "
                f"it)")
    if disputed:
        return (f"your dispute of the rejection of {disputed.label} on {c.id} is open: the arbiter rules at the "
                f"start of day {INT(state.ruling_day(disputed))}, and {c.id} takes no submission until then")
    if m.day >= state.close_day(m, c):
        return (f"the escrow of {c.id} takes no new submission from its close day {INT(state.close_day(m, c))}, "
                f"when a release would pay you nothing; it closes once nothing is pending")
    if before:
        return (f"{label} was already submitted to {c.id} and rejected: submit another result (a refinement of it "
                f"is a new result)")
    return None


def deliver(m, p, args, fixed):
    """Give an arrived result to the buyer: a delivery (plain) or a submission for review (escrow) (6). Class 1."""
    c, refusal = actions.own_contract(m, p, args)
    refusal = refusal or actions.contract_gone(m, c, closed=False) or actions.missing(args, "result")
    if refusal:
        return False, refusal
    r, refusal = actions.find_result(m, p, c, args["result"], "delivered")
    refusal = refusal or _not_taken(m, c, r.label)
    message, quality = "", None
    if actions.given(args, "message"):
        message = TEXT(args["message"])[:m.cfg["contracts"]["message_chars"]]
    if not refusal and actions.given(args, "quality"):
        quality, refusal = actions.quality(args["quality"], m.cfg["tests"]["decimals"])
    if refusal:
        return False, refusal
    escrow = c.form == "escrow"
    d = state.Delivery(r.label, m.day, m.round, message, quality, "review" if escrow else None)
    c.deliveries.append(d)
    n, late = len(c.deliveries), state.days_late(c, d)
    when = f"{unit(INT(late), 'day')} after the deadline" if late else "by the deadline"
    note = "" if quality is None else f" Stated quality: {G(quality)}."
    if message:
        note += f' Message: "{ONE_LINE(message)}".'
    state.emit(m, "delivery", contract=c.id, label=r.label, number=n, message=message, stated_quality=quality,
               quality=r.q, days_late=late)
    if escrow:
        auto = INT(state.auto_day(m, d))
        state.notify(m, c.buyer, f"{p.id} submitted {r.label} for your review on {c.id} (submission #{INT(n)})."
                                 f"{note} Approve it with approve_submission to release the price, or reject it "
                                 f"with reject_submission; if you do neither, it is released at the start of day "
                                 f"{auto}. You can test_delivery it.")
        return True, (f"submitted {r.label} for {c.buyer}'s review on {c.id} (submission #{INT(n)}, {when}); if "
                      f"the buyer neither approves nor rejects it, it is released at the start of day {auto}")
    keep = tail = ""
    if c.kept:
        keep = f" You kept {c.kept}, which stays in use unless you keep this one (keep_delivery)."
        tail = f". The buyer kept {c.kept}, which stays in use unless it keeps this one"
    elif n > 1:
        keep = (" It is now the delivery in use, as the latest; you can keep an earlier delivery instead "
                "(keep_delivery).")
    state.notify(m, c.buyer, f"{p.id} delivered {r.label} for {c.id} (delivery #{INT(n)}).{note} You can "
                             f"test_delivery it.{keep}")
    return True, f"delivered {r.label} for {c.id} to {c.buyer} (delivery #{INT(n)}, {when}){tail}"


# 7. dispute


def _undisputable(m, c):
    """The refusal of a dispute before its balance check (7, checks 2 to 8), or None."""
    refusal = actions.contract_gone(m, c, closed=False)
    if refusal:
        return refusal
    if c.form == "plain":
        return f"{c.id} is a plain contract: only a rejection on an escrow contract can be disputed"
    if not state.held(c):
        how = "closed unreleased" if c.release_day is None else "was released"
        return f"the escrow of {c.id} {how}: there is nothing to dispute"
    d = state.latest(c)
    if state.open_dispute(c):
        return (f"your dispute of the rejection of {d.label} is open: the arbiter rules at the start of day "
                f"{INT(state.ruling_day(d))}")
    if d is None or d.outcome != "rejected":
        if d is None:
            what = "nothing has been rejected"
        elif d.outcome == "review":
            what = f"{d.label} is under the buyer's review"
        else:
            what = "its latest submission was not rejected"
        return f"{c.id} has no rejection to dispute: {what}"
    if d.dispute:
        return f"the rejection of {d.label} was already disputed, and the arbiter ruled for the buyer"
    until = state.dispute_until(m, d)
    if m.day > until:
        return f"the rejection of {d.label} on day {INT(d.decided)} could be disputed through day {INT(until)}"
    return None


def dispute(m, p, args, fixed):
    """Dispute the buyer's rejection of the latest submission on an escrow contract (7). Class 3."""
    c, refusal = actions.own_contract(m, p, args)
    refusal = refusal or _undisputable(m, c)
    if refusal:
        return False, refusal
    d, fee = state.latest(c), state.dispute_fee(m, c)
    refusal = actions.short(p, fee, f"disputing the rejection of {d.label} (a deposit of {CR(fee)})")
    if refusal:
        return False, refusal
    state.debit(m, p.id, fee, "dispute deposits", check=True)
    d.dispute = state.Dispute(m.day)
    ruling, threshold = INT(state.ruling_day(d)), G(c.threshold)
    state.emit(m, "dispute", contract=c.id, label=d.label, deposit=fee, ruling_day=state.ruling_day(d))
    state.notify(m, c.buyer, f"{p.id} disputed your rejection of {d.label} on {c.id}. The arbiter takes one "
                             f"reading of it at the start of day {ruling}: at or above the threshold {threshold}, "
                             f"the price is released to {p.id} and you pay the arbiter's fee of {CR(fee)} to "
                             f"receive {d.label}; below it, your rejection stands and {p.id} pays the fee.")
    return True, (f"disputed the rejection of {d.label} on {c.id}: {CR(fee)} deposited. The arbiter takes one "
                  f"reading of {d.label} at the start of day {ruling}: at or above the threshold {threshold} you "
                  f"win, the price is released to you and your deposit comes back; below it you lose the deposit")


# 8. pay

RULE = "a payment can name only a plain contract on which you are the buyer and the payee is the manager"


def _target(m, payer, payee):
    """The contract an unnamed payment counts toward: the payer's oldest unpaid plain contract with the payee,
    those with a delivery first, or None (8, step 5)."""
    unpaid = [c for c in m.contracts.values() if c.buyer == payer and c.manager == payee and c.form == "plain"
              and c.ended is None and c.paid < c.price - 1e-9]
    return min(unpaid, key=lambda c: not c.deliveries, default=None)


def _pay_implicit(s, p, args):
    """The contract a payment naming none counts toward on the state its turn saw (rounds.md, 6.6, step 1)."""
    if actions.given(args, "contract_id") or not actions.given(args, "to"):
        return {}
    payee = actions.participant(s, args["to"])[0]
    if payee is None:
        return {}
    c = _target(s, p.id, payee.id)
    return {"contract": c.id if c else None}


def _named(m, p, payee, args):
    """The contract a payment names (8, step 4)."""
    c, refusal = actions.lookup(m, p, args, "contract", "contract_id")
    if refusal:
        return None, refusal
    if c.manager == p.id:
        return None, f"{c.id} is a contract on which you are the manager: {RULE}"
    if c.buyer != p.id:
        return None, f"{c.id} is not one of your contracts: {RULE}"
    if c.manager != payee.id:
        return None, f"{c.id} is your contract with {c.manager}, not with {payee.id}: {RULE}"
    if c.form == "escrow":
        return None, f"{c.id} is an escrow contract: the escrow pays it, and {RULE}"
    if c.ended is not None:
        return None, f"{c.id} was ended on day {INT(c.ended)}: nothing is owed on it, and a payment can't name it"
    return c, None


def pay(m, p, args, fixed):
    """Pay credits to another participant, of either role (8). Class 0."""
    if not actions.given(args, "to"):
        return False, actions.missing(args, "to")
    payee, refusal = actions.participant(m, args["to"])
    if not refusal and payee is p:
        refusal = "you cannot pay yourself"
    if refusal:
        return False, refusal
    amount, refusal = actions.amount(args, "amount")
    c = None
    if not refusal:
        if actions.given(args, "contract_id"):
            c, refusal = _named(m, p, payee, args)
        elif "contract" in fixed:
            c = m.contracts.get(fixed["contract"])
        else:
            c = _target(m, p.id, payee.id)
    if not refusal and not state.covers(p, amount):
        refusal = f"insufficient balance ({F(p.balance, 2)})"
    if refusal:
        return False, refusal
    state.debit(m, p.id, amount, "payments made")
    state.credit(m, payee.id, amount, "payments received")
    advance = bool(c) and not c.deliveries
    to_price, beyond = processes.count_payment(m, c, amount) if c else (0.0, 0.0)
    cid, message = c.id if c else None, args.get("message") if actions.given(args, "message") else None
    m.payments.append(state.Payment(m.day, m.round, p.id, payee.id, amount, cid, to_price, advance))
    state.emit(m, "payment", payer=p.id, payee=payee.id, amount=amount, contract=cid, to_price=to_price,
               beyond=beyond, message=message)
    where = f" for {cid}" if c else ""
    state.notify(m, payee.id, f"{p.id} paid you {CR(amount)}{where}")
    note = ""
    if beyond > 0:
        note = (f" ({CR(to_price)} of it paid the rest of its price; the other {CR(beyond)} came on top of it)"
                if to_price > 0 else " (its price was already paid in full: this came on top of it)")
    if message is not None and (not isinstance(message, str) or message.strip()):
        note += " (pay takes no message: yours was not sent)"
    return True, f"paid {CR(amount)} to {payee.id}{where}{note}"


actions.ACTIONS.update(bid=bid, withdraw_bid=withdraw_bid, delegate_run=delegate_run, run_myself=run_myself,
                       test_result=test_result, deliver=deliver, dispute=dispute, pay=pay)
actions.IMPLICIT.update(withdraw_bid=_withdraw_implicit, pay=_pay_implicit)
