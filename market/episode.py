"""One episode outside its rounds (spec/episode.md): its start, the start of a day and of a round, the close
rule, the close's steps, and the settlement with its scores, money check and core metrics (running.md, 6.4 to
6.7).

The day-start and close processes are processes.py's: each tests its own condition, so each is called for every
contract in creation order. `settle` reads the market and changes nothing.
"""

from collections import Counter

from . import config, processes, rng, rounds, state, turns
from .fmt import INT, SUM, F, finite

# 1. Episode start


def start(seed, cfg):
    """The market of a new episode: the participants in seat order with their IDs and shares, and L (1.1 to 1.3;
    randomness.md, 3.1 to 3.3)."""
    m = state.Market(seed=seed, cfg=cfg)
    money, time = cfg["money"], cfg["time"]
    for role in ("buyer", "manager"):
        for seat in range(cfg["participants"][role + "s"]):
            pid = rng.participant_id(seed, role, seat, m.participants)
            state.add_participant(m, pid, role,
                                  rng.share(seed, role, seat, *money["share_range"], money["share_decimals"]))
    m.last_arrival = rng.horizon(seed, time["horizon_min_days"], time["horizon_end_prob"], config.horizon_cap(cfg))
    return m


def fresh_start(m):
    """The log's fresh-start record, with the draws of the start (1.4; running.md, 3, step 4, and 6.3, A and B),
    then the function definitions, once: each call's record names the functions it offered (running.md, 6.3 D)."""
    cfg = m.cfg
    seats = [{"id": p.id, "role": p.role, "seat": p.seat, "model": config.seat_spec(cfg, p.role, p.seat),
              "share": p.share, "start_balance": config.start_balance(cfg, p.share),
              "budget": config.per_task_budget(cfg, p.share) if p.role == "buyer" else None}
             for p in m.participants.values()]
    state.emit(m, "fresh_start", name=cfg["name"], seed=m.seed, message_channels=cfg["message_channels"],
               participants=seats, last_arrival_day=m.last_arrival)
    state.emit(m, "functions", functions=turns.role_functions(cfg))


# 3. The start of a day


def _arrive(m, buyer):
    """A buyer's tasks of the day, each with its worth, due day, budget and notification (3.2; randomness.md,
    3.4 to 3.6)."""
    tasks, budget = m.cfg["tasks"], config.per_task_budget(m.cfg, buyer.share)
    n = tasks["first_day_tasks"] if m.day == 1 else rng.arrivals(m.seed, m.day, buyer.seat, tasks["arrival_p"])
    state.emit(m, "arrivals", buyer=buyer.id, tasks=n)
    for j in range(n):
        tid = state.new_task_id(m)
        worth = rng.worth(m.seed, m.day, buyer.seat, j, *tasks["worth_range"])
        offset = rng.deadline(m.seed, tid, *tasks["due_days_range"])
        t = m.tasks[tid] = state.Task(tid, buyer.id, worth, m.day + offset)
        state.emit(m, "task", task=tid, buyer=buyer.id, worth=worth, D=offset, due=t.due, budget=budget)
        state.credit(m, buyer.id, budget, "per-task budgets")
        state.notify(m, buyer.id, f"New task {tid} in your queue, worth {INT(worth)} to you, due day {INT(t.due)} "
                                  f"({processes.rel(m, t.due)}). It brought {F(budget, 2)} credits, added to your "
                                  f"balance.")


def start_day(m):
    """The start of the next day, in its fixed order: the day, the task arrivals, per contract its result
    arrivals then its due-day notice, every ruling, every automatic release, every lateness close (3)."""
    m.day, m.round = m.day + 1, None
    state.emit(m, "day")
    if m.day <= m.last_arrival:
        for buyer in state.of_role(m, "buyer"):
            _arrive(m, buyer)
    for c in m.contracts.values():
        processes.arrivals(m, c)
        processes.due_notice(m, c)
    for step in (processes.rule, processes.auto_release, processes.lateness_close):
        for c in m.contracts.values():
            step(m, c)


# 4. The start of each round


def start_round(m, r):
    """The start of round r of the day: its turn order is drawn, the round begins, the bids that open are
    announced (4; randomness.md, 3.7). Returns the turn order."""
    order = rng.order(m.seed, m.day, r, list(m.participants))
    m.round = r
    processes.opening_notices(m)
    return order


# 5. The end of a day: the close rule


def _on(m, t):
    """Every contract ever made on a task (5.1)."""
    return [c for c in m.contracts.values() if c.task == t.id]


def _got(c):
    """A delivered contract: an escrow that released, or a plain contract with a delivery (5.1; running.md,
    6.7)."""
    return c.release_day is not None if c.form == "escrow" else bool(c.deliveries)


def delivered(m, t):
    """A task is DELIVERED (5.1)."""
    return any(_got(c) for c in _on(m, t))


def done(m, t):
    """A task is DONE: delivered, withdrawn, or under an escrow that closed unreleased by lateness (5.1)."""
    return t.state == "withdrawn" or any(_got(c) or c.closed == "late" for c in _on(m, t))


def seen(m, c):
    """The buyer of a contract with a delivery has had a turn that saw its first delivery: a view built in a
    later round and not lost to a failed model call (5.1; rounds.md, 10.1)."""
    first = c.deliveries[0]
    return config.round_index(m.cfg, first.day, first.round) < m.participants[c.buyer].last_view


def close_reason(m):
    """The close rule at the end of the current day: the reason the market closes, or None (5.2, 5.3)."""
    if m.day < m.last_arrival:
        return None
    tasks = m.tasks.values()
    unseen = any(c.form == "plain" and c.deliveries and not state.paid_in_full(c) and not seen(m, c)
                 for c in m.contracts.values())
    if not unseen and all(done(m, t) for t in tasks):
        return "all_done" if all(delivered(m, t) for t in tasks) else "all_settled"
    if m.day >= max(t.due for t in tasks) + m.cfg["time"]["close_after_due_days"] - 1:
        return "due_limit"
    return None


# 6. At the close


def close(m):
    """The close's three steps, on the close day with no round: every open dispute ruled, every submission
    under review released, every price still held refunded (6)."""
    m.round = None
    for c in m.contracts.values():
        processes.rule(m, c, at_close=True)
    for c in m.contracts.values():
        processes.auto_release(m, c, "close")
    for c in m.contracts.values():
        if state.held(c):
            c.closed = "close"
            state.emit(m, "refund_at_close", contract=c.id, refunded=c.price)
            state.credit(m, c.buyer, c.price, "escrow refunds")


# 7. Settlement, 8. Scores and welfare; running.md, 6.4 to 6.7


def _entry(m, c):
    """A contract's settlement: its outcome, its settled result and the value it realises (7; running.md,
    6.5)."""
    t, plain = m.tasks[c.task], c.form == "plain"
    if plain:
        outcome = "ended" if c.ended is not None else "delivered" if c.deliveries else "undelivered"
    elif c.release_day is not None:
        outcome = "released"
    else:
        outcome = "ended" if c.ended is not None else "closed_late" if c.closed == "late" else "refunded_at_close"
    d = state.in_use(c) if plain else state.latest(c) if outcome == "released" else None
    q = day = late = None
    value = 0.0 if c.deliveries else None
    if d:
        q = state.result(c, d.label).q
        day = d.day if plain else c.received
        late = max(0, (d.day if day is None else day) - t.due)
        if day is not None:
            value = processes.value(m, c, q, day)
    return {"contract": c.id, "task": c.task, "buyer": c.buyer, "manager": c.manager, "form": c.form,
            "price": c.price, "threshold": c.threshold, "worth": t.worth, "due_day": t.due,
            "deadline_day": c.deadline, "signed_day": c.signed, "outcome": outcome, "deliveries": len(c.deliveries),
            "settled_label": d.label if d else None, "settled_q": q, "receive_day": day, "days_late": late,
            "realized_value": value, "paid": c.paid, "paid_beyond_price": SUM(state.beyond(m, c)),
            "refunded": state.refunded(c), "setup_fee": 0.0 if plain else state.setup_fee(m, c),
            "disputes": [{"label": s.label, "winner": s.dispute.winner, "at_close": s.dispute.at_close}
                         for s in c.deliveries if s.dispute]}


def _rate(a, b):
    """A rate a / b: round(x, 4), null when its denominator is 0 (running.md, 6.7)."""
    return round(a / b, 4) if b else None


def _mean(xs):
    """A mean over a set, null when the set is empty (running.md, 6.7)."""
    return _rate(sum(xs), len(xs))


def _booked(m, category, role=None):
    """The accounts entries of a category, of one role or of everyone (view.md, 8)."""
    return [x for p in m.participants.values() if role in (None, p.role) for kind, x in p.ledger if kind == category]


def _all_finite(x, name):
    """The settlement with every number in it checked, at any depth: none may be infinite or not a number
    (config.md, 6, "Finiteness during an episode")."""
    if isinstance(x, float):
        finite(x, name)
    for key, v in x.items() if isinstance(x, dict) else enumerate(x) if isinstance(x, list) else ():
        _all_finite(v, f"{name}.{key}")
    return x


def settle(m):
    """After the close's steps: the settlement per contract, the scores, the welfare, the money check and the
    core metrics, as the summary records them (7, 8; running.md, 6.4 to 6.7)."""
    cfg, people, contracts = m.cfg, m.participants.values(), m.contracts.values()
    settlement = [_entry(m, c) for c in contracts]
    scores, welfare = [], 0
    for p in people:
        spec = config.seat_spec(cfg, p.role, p.seat)
        row = {"id": p.id, "role": p.role, "seat": p.seat, "provider": spec["provider"], "model": spec.get("model"),
               "final_balance": round(p.balance, 2)}
        if p.role == "buyer":
            value = SUM([e["realized_value"] for e in settlement
                         if e["buyer"] == p.id and e["realized_value"] is not None])
            budgets = SUM([x for kind, x in p.ledger if kind == "per-task budgets"])
            score = ((p.balance + value) - config.start_balance(cfg, p.share)) - budgets
            row.update(score=round(score, 2), value_obtained=round(value, 2), budgets_credited=round(budgets, 2))
        else:
            score = p.balance - config.start_balance(cfg, p.share)
            row.update(score=round(score, 2), out_of_business=p.out_of_business)
        scores.append(row)
        welfare = welfare + score
    lost = [state.dispute_fee(m, c) for c in contracts for s in c.deliveries
            if s.dispute and s.dispute.winner == "buyer"]
    costs = {"tokens": sum(_booked(m, "tokens")), "employee_runs": sum(_booked(m, "employee runs")),
             "own_runs": sum(_booked(m, "your own runs")), "manager_tests": sum(_booked(m, "tests", "manager")),
             "buyer_tests": sum(_booked(m, "tests", "buyer")), "escrow_setup_fees": sum(_booked(m, "escrow fees")),
             "dispute_costs": sum(lost) + sum(_booked(m, "arbiter's fees paid"))}
    budgets = sum(_booked(m, "per-task budgets"))
    in_market = (sum(p.balance for p in people) + sum(c.price for c in contracts if state.held(c))
                 + sum(state.dispute_fee(m, c) for c in contracts if state.open_dispute(c)))
    put_in = sum(config.start_balance(cfg, p.share) for p in people) + budgets - sum(costs.values())
    return _all_finite({"settlement": settlement, "scores": scores, "welfare": round(welfare, 2),
                        "money_check": round(in_market - put_in, 6),
                        "metrics": _metrics(m, settlement, scores, round(welfare, 2), costs, budgets)},
                       "the settlement")


def _sent(role, x):
    """The kind of a message, by its recipient and the two roles (running.md, 6.7, "Messages")."""
    if x.to not in role:
        return "to_" + x.to
    return f"{role[x.to]}_{role[x.to]}" if role[x.sender] == role[x.to] else "cross_role"


def _metrics(m, settlement, scores, welfare, costs, budgets):
    """The core metrics, from the final state and the market's counts (running.md, 6.7)."""
    counts, contracts, tasks = Counter(m.counts), m.contracts.values(), m.tasks.values()
    role = {p.id: p.role for p in m.participants.values()}
    worth = {c.id: m.tasks[c.task].worth for c in contracts}
    plain = [c for c in contracts if c.form == "plain"]
    escrow = [c for c in contracts if c.form == "escrow"]
    got = [e for e in settlement if e["settled_label"] is not None]
    outcomes = Counter((e["form"], e["outcome"]) for e in settlement)
    with_delivery = [c for c in plain if c.deliveries]
    saw = [c for c in with_delivery if seen(m, c)]
    payers = Counter(f"{role[x.payer]}_to_{role[x.payee]}" for x in m.payments)
    no_contract = [x.amount for x in m.payments if x.contract is None]
    runs = [r for c in contracts for r in c.results]
    submissions = [(c, s) for c in escrow for s in c.deliveries]
    disputes = [(c, s.dispute, state.result(c, s.label).q) for c, s in submissions if s.dispute]
    owed = [c for c, d, _ in disputes if d.winner == "manager" and not d.at_close and state.dispute_fee(m, c) > 0]
    sent = Counter(_sent(role, x) for x in m.messages)
    metrics = {
        "welfare": welfare,
        "buyer_gain_mean": _mean([s["score"] for s in scores if s["role"] == "buyer"]),
        "manager_profit_mean": _mean([s["score"] for s in scores if s["role"] == "manager"]),
        "value_obtained": round(sum(e["realized_value"] or 0.0 for e in settlement), 2),
        "mean_value_frac": _mean([round(e["realized_value"] / e["worth"], 4) for e in got]),
        "costs": {kind: round(x, 2) for kind, x in costs.items()},
        "budgets_credited": round(budgets, 2),
        "tasks_created": len(tasks),
        "tasks_posted": len({post.task for post in m.posts.values()}),
        "tasks_done": sum(delivered(m, t) for t in tasks),
        "tasks_withdrawn": sum(t.state == "withdrawn" for t in tasks),
        "tasks_escrow_closed": sum(not delivered(m, t) and any(c.closed for c in _on(m, t)) for t in tasks),
        "bids": len(m.bids),
        "bids_escrow": sum(b.threshold is not None for b in m.bids.values()),
        "bids_withdrawn": sum(b.status == "withdrawn" for b in m.bids.values()),
        "mean_price_to_worth": _mean([c.price / worth[c.id] for c in contracts]),
        "price_mean_plain": _mean([c.price for c in plain]),
        "price_mean_escrow": _mean([c.price for c in escrow]),
        "accepted_above_worth": sum(c.price > worth[c.id] + 1e-9 for c in contracts),
        "contracts": len(contracts),
        "contracts_plain": len(plain),
        "contracts_escrow": len(escrow),
        "plain_delivered": outcomes["plain", "delivered"],
        "plain_ended": outcomes["plain", "ended"],
        "plain_undelivered": outcomes["plain", "undelivered"],
        "escrow_released": outcomes["escrow", "released"],
        "escrow_ended": outcomes["escrow", "ended"],
        "escrow_closed_late": outcomes["escrow", "closed_late"],
        "escrow_refunded_at_close": outcomes["escrow", "refunded_at_close"],
        "plain_paid_in_full": sum(state.paid_in_full(c) for c in with_delivery),
        "plain_seen": len(saw),
        "plain_unpaid_after_seen": sum(not state.paid_in_full(c) for c in saw),
        "unpaid_amount": round(sum(max(0, c.price - c.paid) for c in saw), 2),
        "plain_delivered_unseen": len(with_delivery) - len(saw),
        "payments": len(m.payments),
        "payment_credits": round(sum(x.amount for x in m.payments), 2),
        "paid_toward_prices": round(sum(x.to_price for x in m.payments), 2),
        "paid_beyond_prices": round(sum(e["paid_beyond_price"] for e in settlement), 2),
        "advance_payments": sum(x.advance and x.to_price > 0 for x in m.payments),
        "payments_without_contract": len(no_contract),
        "payment_credits_without_contract": round(sum(no_contract), 2),
        "payments_buyer_to_manager": payers["buyer_to_manager"],
        "payments_manager_to_buyer": payers["manager_to_buyer"],
        "payments_manager_to_manager": payers["manager_to_manager"],
        "payments_buyer_to_buyer": payers["buyer_to_buyer"],
        "runs_started": len(runs),
        "own_runs": sum(r.worker == "manager" for r in runs),
        "refine_runs": sum(r.kind == "refine" for r in runs),
        "manager_readings": sum(len(r.manager_readings) for r in runs),
        "buyer_readings": sum(len(r.buyer_readings) for r in runs),
        "deliveries_plain": sum(len(c.deliveries) for c in plain),
        "submissions_escrow": len(submissions),
        "mean_delivered_q": _mean([round(e["settled_q"], 4) for e in got]),
        "successful_contracts": sum(e["settled_q"] >= 7 and (e["form"] == "escrow" or e["paid"] >= e["price"] - 1e-6)
                                    for e in got),
        "late_deliveries": sum(e["days_late"] > 0 for e in got),
        "mean_days_late": _mean([e["days_late"] for e in got]),
        "runs_unfinished_at_end": sum(not state.arrived(m, r) for r in runs),
        "escrow_approved": sum(s.outcome == "approved" for _, s in submissions),
        "escrow_rejected": sum(s.outcome == "rejected" for _, s in submissions),
        "escrow_auto_released": sum(c.release_how == "automatic" for c in escrow),
        "escrow_released_at_close": sum(c.release_how == "close" for c in escrow),
        "escrow_released_late": sum(c.release_day is not None and state.days_late(c, state.latest(c)) > 0
                                    for c in escrow),
        "escrow_amount_released": round(sum(c.paid for c in escrow), 2),
        "escrow_amount_refunded": round(sum(state.refunded(c) for c in escrow), 2),
        "disputes": len(disputes),
        "disputes_won_manager": sum(d.winner == "manager" for _, d, _ in disputes),
        "disputes_won_buyer": sum(d.winner == "buyer" for _, d, _ in disputes),
        "disputes_ruled_at_close": sum(d.at_close for _, d, _ in disputes),
        "dispute_rulings_against_q": sum(d.winner is not None and (d.winner == "manager") != (q >= c.threshold)
                                         for c, d, q in disputes),
        "arbiter_fees_owed": len(owed),
        "arbiter_fees_paid": sum(c.received is not None for c in owed),
        "out_of_business_events": counts["out_of_business_events"],
        "back_in_business_events": counts["back_in_business_events"],
        "managers_out_of_business_at_end": sum(p.out_of_business for p in m.participants.values()),
    }
    for r in ("buyer", "manager"):
        for name in ("turns", "actions", "failed_actions", "format_errors", "corrections", "programs",
                     "turns_lost_provider", "turns_lost_format", "calls", "input_tokens", "output_tokens",
                     "reasoning_tokens"):
            metrics[f"{r}_{name}"] = counts[f"{r}_{name}"]
        errors = counts[f"{r}_format_errors"]
        metrics[f"{r}_invalid_action_rate"] = _rate(counts[f"{r}_failed_actions"] + errors,
                                                    counts[f"{r}_actions"] + errors)
        metrics[f"{r}_dollars"] = round(counts[f"{r}_dollars"], 6)
    for name in ("summarizer_calls", "summaries_written", "summaries_failed", "summarizer_input_tokens",
                 "summarizer_output_tokens", "calls_unknown_cost"):
        metrics[name] = counts[name]
    metrics["summarizer_dollars"] = round(counts["summarizer_dollars"], 6)
    metrics["dollars"] = round(counts["buyer_dollars"] + counts["manager_dollars"] + counts["summarizer_dollars"], 6)
    metrics["messages"] = len(m.messages)
    for kind in ("manager_manager", "buyer_buyer", "cross_role", "to_managers", "to_buyers", "to_everyone"):
        metrics[f"messages_{kind}"] = sent[kind]
    return metrics


# 2. The day loop


def play_day(m, ctx):
    """One day: its start, its rounds, then the close rule (2; running.md, 6.3, C)."""
    start_day(m)
    for r in range(1, m.cfg["time"]["rounds_per_day"] + 1):
        rounds.play(m, start_round(m, r), ctx)
    reason = close_reason(m)
    if reason:
        m.close_day, m.close_reason = m.day, reason
        state.emit(m, "close", close_day=m.day, reason=reason)
    m.days_completed += 1
