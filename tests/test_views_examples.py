"""Acceptance check 3 of spec/README.md: the complete example views, each built by market/view.py from a market
state set up by hand and compared with the example byte for byte.

A state is where a history ends. A history is a list of events `(day, round, function, arguments...)`, round 0
being the start of the day, applied in the order of their days and rounds (events of one round in the order
listed). Each function below changes the market as the event it names does, by the spec section in its docstring,
and asserts the rules the event must meet, so that no line of a view comes from a state the rules cannot reach.
What a view does not show (true qualities, the run behind a delivery, worths, others' token charges) is chosen
freely. Texts a participant or the summarizer wrote, and the stored results text, are taken from the example.
"""

import json
import pathlib
from unittest import mock

from market import config, processes, state, view
from market.state import Bid, Contract, Delivery, Dispute, Market, Memory, Message, Payment, Post, Result, Task

EXAMPLES = pathlib.Path(__file__).parent.parent / "spec" / "examples"
RESULTS = "--- Results of your actions in your previous turn ---\n\n"
HIDDEN = "(a message the summary covers)"


def example(name):
    return (EXAMPLES / name).read_text(encoding="utf-8")


def quoted(text, start, end="\n"):
    """What the example shows between `start`, which it holds once, and the next `end`."""
    assert text.count(start) == 1
    return text.split(start)[1].split(end)[0]


def market(mode, shares):
    """An episode's start: the participants in seat order, each at its start balance (episode.md, 1)."""
    m = Market(seed=0, cfg=config.resolve({"message_channels": mode}))
    for pid, share in shares.items():
        state.add_participant(m, pid, "buyer" if pid.startswith("B") else "manager", share)
    return m


def play(m, events, day, rnd):
    """The market after a history, as the round whose views are compared starts. Each day's timers run after its
    start-of-day events and before its round 1."""
    dawned = 0
    for d, r, event, *arguments in sorted(events, key=lambda e: e[:2]) + [(day, rnd, None)]:
        assert (d, r) < (day, rnd) or event is None
        while dawned < (d if r else d - 1):
            dawned += 1
            dawn(m, dawned)
        m.day, m.round = d, r or None
        if event:
            event(m, *arguments)


def rounds(tokens, day, rnd):
    """The start of every round before the one given."""
    return [(d, r, start, tokens) for d in range(1, day + 1) for r in (1, 2, 3) if (d, r) < (day, rnd)]


def contract(m, n):
    return m.contracts[f"contract-{n}"]


# The events


def dawn(m, day):
    """A day's automatic releases, then its lateness closes, as the market makes them (episode.md, 3.5, 3.6)."""
    m.day, m.round = day, None
    for step in (processes.auto_release, processes.lateness_close):
        for c in m.contracts.values():
            step(m, c)


def start(m, tokens):
    """A round starts: every participant in business gets its view, which shows its notifications and messages,
    and is charged for its turn's tokens, here all the same (view.md, 1; turns.md, 13)."""
    for p in m.participants.values():
        if not p.out_of_business:
            p.turns, p.pending, p.seen, p.last_view = p.turns + 1, [], len(m.messages), state.now(m)
            state.charge(m, p.id, tokens, "tokens")


def arrive(m, n, buyer, worth, due):
    """A task arrives (episode.md, 3.2)."""
    low, high = m.cfg["tasks"]["due_days_range"]
    assert low <= due - m.day <= high and all(int(t.id[5:]) < n for t in m.tasks.values())
    budget = config.per_task_budget(m.cfg, m.participants[buyer].share)
    m.tasks[f"task-{n}"] = Task(f"task-{n}", buyer, worth, due)
    state.credit(m, buyer, budget, "per-task budgets")
    state.notify(m, buyer, f"New task task-{n} in your queue, worth {worth} to you, due day {due} "
                           f"({processes.rel(m, due)}). It brought {budget:.2f} credits, added to your balance.")


def post(m, n, form=None, threshold=None):
    """post_task (actions-buyer.md, 2)."""
    t = m.tasks[f"task-{n}"]
    assert t.state == "queued"
    t.state, t.post = "posted", state.numbered("post", t)
    m.posts[t.post] = Post(t.post, t.id, t.buyer, m.day, m.round, form, threshold)


def sign(m, n, manager, price, threshold=None):
    """A manager's bid and its acceptance once the post's bids are open (actions-manager.md, 2;
    actions-buyer.md, 5)."""
    t = m.tasks[f"task-{n}"]
    p = m.posts[t.post]
    assert not state.sealed(m, p)
    bid = state.new_bid_id(m)
    m.bids[bid] = Bid(bid, p.id, manager, price, threshold, "accepted")
    c = Contract(state.numbered("contract", t), t.id, p.id, t.buyer, manager, "plain" if threshold is None else
                 "escrow", price, threshold, m.day, config.deadline(m.cfg, t.due, m.day))
    m.contracts[c.id] = c
    if c.form == "escrow":
        assert m.participants[t.buyer].balance >= price + state.setup_fee(m, c)
        state.debit(m, t.buyer, price, "paid into escrow (total)")
        state.charge(m, t.buyer, state.setup_fee(m, c), "escrow fees")
    p.closed, p.closed_day, p.closed_round = "accepted", m.day, m.round
    t.state, t.contract = "contracted", c.id


def run(m, n, label, cost=4.0, days=1, worker="employee", parent=None):
    """delegate_run or run_myself (actions-manager.md, 4): the contract is open, the worker has a free slot, a
    refined result has arrived, the manager can pay the cost."""
    c = contract(m, n)
    assert c.ended is None and not state.settled(c) and state.result(c, label) is None
    assert len(state.in_flight(m, c.manager, worker)) < m.cfg["production"][worker]["capacity"]
    assert parent is None or state.arrived(m, state.result(c, parent))
    assert m.participants[c.manager].balance >= cost
    c.results.append(Result(label, worker, "refine" if parent else "fresh", parent, m.day, m.day + days, 7.0))
    state.charge(m, c.manager, cost, "employee runs" if worker == "employee" else "your own runs")


def read(m, n, label, side, *readings):
    """test_result by the manager on a contract that is open, test_delivery by the buyer on a delivery, or on the
    submission under its review, once per reading (actions-manager.md, 5; actions-buyer.md, 6)."""
    c = contract(m, n)
    r, d = state.result(c, label), state.delivery_of(c, label)
    assert state.arrived(m, r) and c.ended is None
    assert not state.settled(c) if side == "manager" else d and (c.form == "plain" or d is state.under_review(c))
    for reading in readings:
        state.charge(m, getattr(c, side), m.cfg["tests"][side + "_cost"], "tests")
        getattr(r, side + "_readings").append(reading)


def deliver(m, n, label, quality=None, message=""):
    """deliver: a delivery, or a submission for review while the escrow takes one (actions-manager.md, 6)."""
    c = contract(m, n)
    assert c.ended is None and state.arrived(m, state.result(c, label)) and state.delivery_of(c, label) is None
    if c.form == "escrow":
        assert state.held(c) and not state.under_review(c) and not state.open_dispute(c)
        assert m.day < state.close_day(m, c)
    c.deliveries.append(Delivery(label, m.day, m.round, message, quality, "review" if c.form == "escrow" else None))


def keep(m, n, label):
    """keep_delivery (actions-buyer.md, 7)."""
    c = contract(m, n)
    assert state.delivery_of(c, label)
    c.kept = label


def pay(m, payer, payee, amount, n=None):
    """pay, on a contract or on none (actions-manager.md, 8; processes.md, 5.2)."""
    c = m.contracts.get(f"contract-{n}")
    assert m.participants[payer].balance >= amount
    advance = c is not None and not c.deliveries
    state.debit(m, payer, amount, "payments made")
    state.credit(m, payee, amount, "payments received")
    to_price = processes.count_payment(m, c, amount)[0] if c else 0.0
    m.payments.append(Payment(m.day, m.round, payer, payee, amount, c and c.id, to_price, advance))


def approve(m, n):
    """approve_submission (actions-buyer.md, 8; processes.md, 6.4, 6.8)."""
    c = contract(m, n)
    d = state.under_review(c)
    d.outcome, d.decided = "approved", m.day
    processes.release(m, c, "approved")
    c.received = m.day


def reject(m, n, message=""):
    """reject_submission (actions-buyer.md, 9)."""
    d = state.under_review(contract(m, n))
    d.outcome, d.decided, d.rejection = "rejected", m.day, message


def dispute(m, n):
    """dispute, within the window (actions-manager.md, 7; processes.md, 6.6)."""
    c = contract(m, n)
    assert state.disputable(m, c) and m.participants[c.manager].balance >= state.dispute_fee(m, c)
    state.debit(m, c.manager, state.dispute_fee(m, c), "dispute deposits", check=True)
    state.latest(c).dispute = Dispute(m.day)


def rule(m, n, reading):
    """The arbiter's ruling at the start of the day after the dispute, its reading given (processes.md, 6.6)."""
    c = contract(m, n)
    assert m.round is None and state.open_dispute(c).dispute.day == m.day - 1
    with mock.patch.object(processes.rng, "arbiter", lambda *key: {"z": 0.0, "reading": reading}):
        processes.rule(m, c)


def pay_fee(m, n):
    """pay_escrow_arbiter (actions-buyer.md, 10; processes.md, 6.7)."""
    c = contract(m, n)
    assert state.owes_fee(m, c)
    state.charge(m, c.buyer, state.dispute_fee(m, c), "arbiter's fees paid")
    c.received = m.day


def end(m, n):
    """end_contract, where processes.md, 7, allows it (actions-buyer.md, 11)."""
    c = contract(m, n)
    t = m.tasks[c.task]
    assert processes.end_rule(m, c) in ("stands", "overdue")
    c.ended = m.day
    t.state, t.post, t.contract = "queued", None, None
    t.ended.append(c.id)
    if c.form == "escrow":
        state.credit(m, c.buyer, c.price, "escrow refunds")


def say(m, sender, to, text=HIDDEN):
    """send_message (messaging.md, 2.5)."""
    m.messages.append(Message(m.day, m.round, sender, to, text))


def said(text, reader, day, rnd, sender, to):
    """The event of a message the reader's view shows in full, with the text the example gives it."""
    src, dst = ("you" if pid == reader else pid for pid in (sender, to))
    return day, rnd, say, sender, to, quoted(text, f"\n  [day {day}, round {rnd}] {src} -> {dst}: ")


def stored(m, pid, text, day, rnd, other):
    """The texts the market holds for the participant's next view: the results text of its previous turn, entry by
    entry, and the memory that turn's decide reply wrote (turns.md, 10.2, 12); and the summary of the first 10
    messages of its conversation with one other participant (messaging.md, 4.2)."""
    p = m.participants[pid]
    p.results = quoted(text, RESULTS, "\n\n--- ").replace("\n\n", "\n").split("\n")
    p.memory = Memory(quoted(text, f"(last updated day {day}, round {rnd}, with your last actions, before their "
                                   f"results) ---\n\n", "\n\n--- "), day, rnd, p.turns, False)
    m.summaries[tuple(sorted((pid, other)))] = (quoted(text, f"you are {pid} in it): "), 10)


# Day 1, round 1 (message_channels off): seed 0's start in the reference run


def day_1():
    """The four tasks of day 1 have arrived; nobody has acted. The shares follow from the balances and budgets
    the examples show (60 and 30 credits times the share: episode.md, 1.3); M-f8c's is not shown."""
    m = market("off", {"B-c53": 1.36, "B-d75": 1.13, "M-14b": 1.35, "M-849": 1.28, "M-f8c": 1.0})
    play(m, [(1, 0, arrive, 1, "B-c53", 65, 9), (1, 0, arrive, 2, "B-c53", 59, 9),
             (1, 0, arrive, 3, "B-d75", 70, 7), (1, 0, arrive, 4, "B-d75", 62, 10)], 1, 1)
    return m


def test_the_first_view_of_a_buyer():
    assert view.build(day_1(), "B-c53")[0] == example("view_buyer_day1_off_B-c53.txt")


def test_the_first_view_of_a_manager():
    assert view.build(day_1(), "M-14b")[0] == example("view_manager_day1_off_M-14b.txt")


def test_the_first_view_of_the_other_buyer_of_the_same_market():
    """The view that opens examples/turn_with_compute_off_B-d75.json (README.md, acceptance check 4)."""
    messages = json.loads(example("turn_with_compute_off_B-d75.json"))
    assert view.build(day_1(), "B-d75")[0] == messages[0]["content"]


# A busy buyer: B-42c on day 23, round 2 (message_channels all)


def buyer_day_23(text):
    """B-42c's 21 tasks, each contracted once: 10 plain contracts and 11 escrows with the three managers.

    From the view: the start balance 40.80 and the budget 20.40 (share 0.68) give the balance 85.14 with the
    accounts' sums; 21 budgets make 428.40; the 6 payments not listed make 87 (the prices of contract-3, 5, 4, 6
    and 10); the escrows not listed hold 58 of the 199 paid into escrow; 37 readings were charged.
    """
    me, m218, mbd6, mf2b = "B-42c", "M-218", "M-bd6", "M-f2b"
    m = market("all", {"B-79d": 1.0, me: 0.68, mbd6: 1.0, mf2b: 1.0, m218: 1.0})
    tasks = {  # number: arrival day, worth, due day
        3: (1, 55, 7), 4: (1, 66, 8), 5: (2, 48, 6), 6: (2, 74, 11), 10: (3, 58, 9), 12: (4, 52, 12),
        14: (5, 64, 14), 15: (5, 57, 14), 17: (6, 69, 12), 18: (6, 50, 13), 20: (7, 61, 14), 23: (12, 70, 21),
        24: (12, 45, 21), 25: (12, 62, 20), 26: (13, 47, 18), 27: (13, 42, 19), 28: (13, 78, 19),
        29: (14, 61, 21), 35: (16, 60, 22), 36: (16, 63, 25), 39: (19, 72, 28)}
    events = rounds(0.0024, 23, 2)
    events += [(day, 0, arrive, n, me, worth, due) for n, (day, worth, due) in tasks.items()]
    events += [(day, 1, post, n) for n, (day, _, _) in tasks.items()]
    events += [
        # contract-3, M-218, plain: two deliveries, paid in two parts
        (2, 1, sign, 3, m218, 18.0), (4, 1, run, 3, "R-2b4"), (5, 1, deliver, 3, "R-2b4"),
        (5, 1, run, 3, "R-50f", 2.8, 1, "employee", "R-2b4"), (5, 3, read, 3, "R-2b4", "buyer", 5.1, 5.2),
        (6, 1, pay, me, m218, 14.0, 3), (6, 2, deliver, 3, "R-50f", 9.0), (6, 3, read, 3, "R-50f", "buyer", 5.4, 6.0),
        (7, 1, pay, me, m218, 4.0, 3),
        # contract-4, M-bd6, plain
        (2, 1, sign, 4, mbd6, 20.0), (2, 2, run, 4, "R-376"), (3, 1, deliver, 4, "R-376"),
        (3, 2, read, 4, "R-376", "buyer", 2.9, 5.5, 6.2), (4, 1, run, 4, "R-f64", 2.8, 1, "employee", "R-376"),
        (5, 1, deliver, 4, "R-f64", 8.7), (5, 2, read, 4, "R-f64", "buyer", 8.3), (6, 1, pay, me, mbd6, 20.0, 4),
        # contract-5, M-218, plain
        (3, 1, sign, 5, m218, 16.0), (4, 1, run, 5, "R-844"), (5, 1, deliver, 5, "R-844"),
        (5, 3, read, 5, "R-844", "buyer", 6.1), (6, 1, pay, me, m218, 16.0, 5),
        # contract-6, M-f2b, plain
        (3, 1, sign, 6, mf2b, 15.0), (6, 1, run, 6, "R-035"), (7, 1, deliver, 6, "R-035", 9.1),
        (7, 1, pay, me, mf2b, 15.0, 6), (7, 2, read, 6, "R-035", "buyer", 9.6),
        # contract-15 and 17, M-f2b, plain
        (8, 1, sign, 15, mf2b, 14.0), (12, 1, run, 15, "R-034"), (13, 1, deliver, 15, "R-034"),
        (13, 2, read, 15, "R-034", "buyer", 9.8), (14, 1, pay, me, mf2b, 14.0, 15),
        (8, 1, sign, 17, mf2b, 14.0), (10, 1, run, 17, "R-02a"), (11, 1, deliver, 17, "R-02a", 8.5),
        (11, 3, read, 17, "R-02a", "buyer", 7.9), (12, 1, pay, me, mf2b, 14.0, 17),
        # contract-10, M-bd6, plain: paid twice, the second payment beyond its price and paid back
        (4, 1, sign, 10, mbd6, 18.0), (6, 1, run, 10, "R-b61"), (7, 1, deliver, 10, "R-b61", 7.1),
        (7, 2, read, 10, "R-b61", "buyer", 6.8), (9, 1, pay, me, mbd6, 18.0, 10), (14, 1, pay, me, mbd6, 18.0, 10),
        (16, 1, pay, mbd6, me, 18.0),
        # contract-12, 18 and 20, M-218, escrows approved: the three not listed
        (8, 1, sign, 12, m218, 20.0, 7.0), (9, 1, run, 12, "R-6d0"), (10, 1, deliver, 12, "R-6d0"),
        (10, 2, read, 12, "R-6d0", "buyer", 7.4, 8.1), (11, 1, approve, 12),
        (9, 1, sign, 18, m218, 20.0, 7.0), (11, 1, run, 18, "R-b3a"), (12, 1, deliver, 18, "R-b3a"),
        (12, 2, read, 18, "R-b3a", "buyer", 8.0, 7.2), (13, 1, approve, 18),
        (10, 1, sign, 20, m218, 18.0, 7.0), (12, 1, run, 20, "R-e15"), (13, 1, deliver, 20, "R-e15"),
        (13, 2, read, 20, "R-e15", "buyer", 7.6, 6.9), (14, 1, approve, 20),
        # contract-14, M-bd6, plain
        (9, 1, sign, 14, mbd6, 16.0), (13, 1, run, 14, "R-d69"), (14, 1, deliver, 14, "R-d69", 7.0),
        (14, 2, read, 14, "R-d69", "buyer", 6.8, 7.4, 7.0, 7.2), (15, 2, pay, me, mbd6, 16.0, 14),
        # contract-24 and 25, M-218, escrows approved
        (14, 1, sign, 24, m218, 18.0, 7.0), (16, 1, run, 24, "R-96c"), (17, 1, deliver, 24, "R-96c"),
        (17, 2, read, 24, "R-96c", "buyer", 6.9, 9.6), (17, 3, approve, 24),
        (14, 1, sign, 25, m218, 18.0, 7.0), (15, 1, run, 25, "R-4bf"), (16, 1, deliver, 25, "R-4bf"),
        (16, 2, read, 25, "R-4bf", "buyer", 7.7), (17, 1, approve, 25),
        # contract-23, M-f2b, plain: paid twice, the second payment beyond its price and paid back
        (15, 1, sign, 23, mf2b, 14.0), (16, 1, run, 23, "R-c98"), (17, 1, deliver, 23, "R-c98"),
        (17, 2, read, 23, "R-c98", "buyer", 9.5), (18, 1, pay, me, mf2b, 14.0, 23), (19, 1, pay, me, mf2b, 14.0, 23),
        (19, 3, pay, mf2b, me, 14.0),
        # contract-28, M-bd6, escrow approved
        (15, 1, sign, 28, mbd6, 17.0, 7.0), (18, 1, run, 28, "R-017"), (19, 1, deliver, 28, "R-017"),
        (19, 2, read, 28, "R-017", "buyer", 10.0), (19, 3, approve, 28),
        # contract-26, M-bd6, escrow: rejected, disputed, ruled for the manager; the arbiter's fee not paid
        (16, 1, sign, 26, mbd6, 17.0, 7.0), (19, 1, run, 26, "R-99e"),
        (20, 1, deliver, 26, "R-99e", 7.4, quoted(text, 'stated quality 7.4; message "', '"\n')),
        (20, 2, read, 26, "R-99e", "buyer", 6.6), (21, 1, read, 26, "R-99e", "buyer", 5.1), (21, 1, reject, 26),
        (21, 2, dispute, 26), (22, 0, rule, 26, 8.2),
        # contract-27, M-bd6, escrow: rejected, not disputed, ended in the previous turn
        (16, 1, sign, 27, mbd6, 17.0, 7.0), (19, 1, run, 27, "R-1e2"), (20, 1, deliver, 27, "R-1e2"),
        (20, 2, read, 27, "R-1e2", "buyer", 4.0, 5.2), (21, 1, read, 27, "R-1e2", "buyer", 3.1, 4.6),
        (21, 1, reject, 27), (23, 1, end, 27),
        # contract-29, M-218, escrow: under review, read twice more in the previous turn
        (17, 1, sign, 29, m218, 18.0, 7.0), (21, 1, run, 29, "R-07d"),
        (22, 1, deliver, 29, "R-07d", 7.8, quoted(text, 'stated quality 7.8; message "', '"\n')),
        (22, 2, read, 29, "R-07d", "buyer", 5.6), (23, 1, read, 29, "R-07d", "buyer", 4.6, 6.3),
        # contract-35 and 36, M-218, escrows, and contract-39, M-f2b, plain: nothing delivered
        (17, 1, sign, 35, m218, 18.0, 7.0), (17, 1, sign, 36, m218, 18.0, 7.0), (20, 1, sign, 39, mf2b, 14.0),
    ]
    events += [(3 + i, 2, say, (mbd6, me)[i % 2], (me, mbd6)[i % 2]) for i in range(10)]
    events += [said(text, me, *message) for message in (
        (5, 2, m218, me), (6, 2, me, m218), (6, 3, m218, me), (7, 2, m218, me), (15, 3, me, m218), (16, 1, mbd6, me),
        (5, 2, mf2b, me), (7, 2, mf2b, me), (11, 3, mf2b, me), (12, 1, me, mf2b), (19, 2, me, mf2b),
        (19, 3, mf2b, me))]
    play(m, events, 23, 2)
    stored(m, me, text, 23, 1, mbd6)
    return m


def test_a_busy_buyer():
    text = example("view_buyer_all_B-42c_day23_round2.txt")
    assert view.build(buyer_day_23(text), "B-42c")[0] == text


# A manager late in a long episode: M-7f8 on day 28, round 3 (message_channels all)


def manager_day_28(text):
    """M-7f8's 21 contracts, 6 with B-82c and 15 with B-b79, and B-b79's ten open posts.

    From the view: the start balance 51.00 (share 0.85) gives the balance 141.48 with the accounts' sums; the 7
    payments not listed make 86 (contract-1 and 2 at 12 each, and 62 on five of B-b79's plain contracts); the
    escrow not listed released 18 of the 78; 20 employee runs and 4 runs of its own on the 24 results of its
    contracts cost 83.29 and 31.05; 30 readings were charged.
    """
    me, b82c, bb79 = "M-7f8", "B-82c", "B-b79"
    m = market("all", {b82c: 1.0, bb79: 1.0, "M-0ed": 1.0, "M-ee6": 1.0, me: 0.85})
    tasks = {  # number: arrival day, buyer, due day
        1: (1, b82c, 6), 2: (1, b82c, 7), 5: (2, bb79, 8), 8: (3, bb79, 9), 11: (4, bb79, 10), 13: (4, bb79, 10),
        17: (5, bb79, 12), 18: (5, bb79, 11), 19: (6, bb79, 10), 20: (6, bb79, 14), 21: (6, bb79, 12),
        22: (7, bb79, 12), 23: (7, bb79, 13), 24: (7, bb79, 12), 26: (8, bb79, 14), 27: (8, bb79, 13),
        29: (9, bb79, 15), 30: (10, bb79, 16), 31: (11, bb79, 17), 32: (11, bb79, 17), 34: (12, bb79, 20),
        36: (14, bb79, 22), 39: (15, bb79, 23), 40: (15, bb79, 21), 41: (16, b82c, 23), 43: (18, bb79, 27),
        46: (18, bb79, 27), 47: (19, b82c, 26), 48: (19, b82c, 28), 49: (19, b82c, 25), 50: (19, bb79, 26)}
    stated = {19: ("plain",), 22: ("plain",), 23: ("plain",), 24: ("plain",), 36: ("plain",), 39: ("escrow", 5.0),
              40: ("escrow", 5.0), 50: ("escrow", 5.0)}
    events = rounds(0.0022, 28, 3)
    events += [(day, 0, arrive, n, buyer, 60, due) for n, (day, buyer, due) in tasks.items()]
    events += [(day, 2 if n == 23 else 1, post, n, *stated.get(n, ())) for n, (day, _, _) in tasks.items()]
    events += [
        # B-82c's first two, plain, paid on day 10
        (1, 3, sign, 1, me, 12.0), (2, 1, run, 1, "R-099", 4.35), (3, 1, read, 1, "R-099", "manager", 6.2),
        (3, 1, deliver, 1, "R-099"), (10, 1, pay, b82c, me, 12.0, 1),
        (1, 3, sign, 2, me, 12.0), (2, 1, run, 2, "R-681", 7.62, 2, "manager"),
        (4, 1, read, 2, "R-681", "manager", 7.1), (4, 1, deliver, 2, "R-681"), (10, 1, pay, b82c, me, 12.0, 2),
        # B-b79's plain contracts paid before the payments listed: contract-5, 8, 13, 21 and 27
        (3, 1, sign, 5, me, 12.0), (4, 1, run, 5, "R-1c0", 4.35), (5, 1, read, 5, "R-1c0", "manager", 6.0),
        (5, 1, deliver, 5, "R-1c0"), (6, 1, pay, bb79, me, 12.0, 5),
        (4, 1, sign, 8, me, 13.0), (5, 1, run, 8, "R-2d1", 4.35), (6, 1, read, 8, "R-2d1", "manager", 6.6),
        (6, 1, deliver, 8, "R-2d1"), (7, 1, pay, bb79, me, 13.0, 8),
        (5, 1, sign, 13, me, 13.0), (5, 2, run, 13, "R-3e2", 8.11, 3, "manager"),
        (8, 1, read, 13, "R-3e2", "manager", 7.3), (8, 1, deliver, 13, "R-3e2"), (9, 1, pay, bb79, me, 13.0, 13),
        (7, 1, sign, 21, me, 12.0), (9, 1, run, 21, "R-4f3", 4.35), (10, 1, read, 21, "R-4f3", "manager", 6.4),
        (10, 1, deliver, 21, "R-4f3"), (11, 1, pay, bb79, me, 12.0, 21),
        (9, 1, sign, 27, me, 12.0), (10, 1, run, 27, "R-5a4", 4.35), (11, 1, read, 27, "R-5a4", "manager", 6.8),
        (11, 1, deliver, 27, "R-5a4"), (12, 1, pay, bb79, me, 12.0, 27),
        # contract-17 and 30, plain, delivered and never paid
        (6, 1, sign, 17, me, 12.0), (8, 1, run, 17, "R-54a", 4.35), (9, 1, read, 17, "R-54a", "manager", 5.9),
        (9, 1, deliver, 17, "R-54a"),
        (11, 1, sign, 30, me, 12.0), (14, 1, run, 30, "R-4d3", 4.35), (15, 1, read, 30, "R-4d3", "manager", 6.1),
        (15, 1, deliver, 30, "R-4d3"),
        # contract-20, plain: a second delivery, a refinement
        (8, 1, sign, 20, me, 12.0), (10, 1, run, 20, "R-a96", 4.35), (11, 1, read, 20, "R-a96", "manager", 5.5),
        (11, 1, deliver, 20, "R-a96"), (13, 1, run, 20, "R-c5c", 3.1, 1, "employee", "R-a96"),
        (14, 1, read, 20, "R-c5c", "manager", 7.8, 8.0), (14, 1, deliver, 20, "R-c5c"),
        (16, 2, pay, bb79, me, 12.0, 20),
        # contract-26, plain: paid twice, the second payment beyond its price and paid back
        (9, 1, sign, 26, me, 12.0), (11, 1, run, 26, "R-6b5", 4.35), (12, 1, read, 26, "R-6b5", "manager", 6.7),
        (12, 1, deliver, 26, "R-6b5"), (12, 3, pay, bb79, me, 12.0, 26), (13, 2, pay, bb79, me, 12.0, 26),
        (14, 1, pay, me, bb79, 12.0),
        # contract-29, 32 and 31, plain
        (10, 1, sign, 29, me, 12.0), (13, 1, run, 29, "R-7c6", 4.35), (14, 1, read, 29, "R-7c6", "manager", 6.9),
        (14, 1, deliver, 29, "R-7c6"), (15, 3, pay, bb79, me, 12.0, 29),
        (12, 1, sign, 32, me, 11.0), (14, 1, run, 32, "R-8d7", 4.35), (15, 1, read, 32, "R-8d7", "manager", 6.3),
        (15, 1, deliver, 32, "R-8d7"), (15, 3, pay, bb79, me, 11.0, 32),
        (13, 1, sign, 31, me, 12.0), (14, 2, run, 31, "R-063", 7.4, 3, "manager"),
        (17, 1, read, 31, "R-063", "manager", 7.2), (17, 1, deliver, 31, "R-063"), (22, 3, pay, bb79, me, 12.0, 31),
        # contract-34, B-b79's escrow not listed, approved
        (14, 1, sign, 34, me, 18.0, 5.0), (16, 1, run, 34, "R-9e8", 4.35), (17, 1, read, 34, "R-9e8", "manager", 6.5),
        (17, 1, deliver, 34, "R-9e8"), (18, 1, approve, 34),
        # contract-41, B-82c, plain: its one delivery kept
        (17, 1, sign, 41, me, 12.0), (18, 1, run, 41, "R-55b", 4.35), (19, 1, read, 41, "R-55b", "manager", 7.0),
        (19, 1, deliver, 41, "R-55b"), (19, 3, keep, 41, "R-55b"), (20, 1, pay, b82c, me, 12.0, 41),
        # contract-43 and 46, B-b79, escrows approved
        (20, 1, sign, 43, me, 12.0, 5.0), (24, 1, run, 43, "R-97c", 4.35),
        (25, 1, read, 43, "R-97c", "manager", 6.9, 7.1), (26, 1, deliver, 43, "R-97c", 7.0), (27, 1, approve, 43),
        (20, 1, sign, 46, me, 12.0, 5.0), (21, 2, run, 46, "R-417", 4.35), (22, 1, read, 46, "R-417", "manager", 6.6),
        (23, 1, deliver, 46, "R-417"), (24, 1, approve, 46),
        # contract-47 and 49, B-82c, escrows approved on a refinement, the first result not submitted
        (20, 2, sign, 47, me, 12.0, 6.0), (20, 3, run, 47, "R-af9", 4.35), (21, 1, read, 47, "R-af9", "manager", 5.6),
        (22, 1, run, 47, "R-721", 3.12, 1, "employee", "R-af9"), (23, 1, read, 47, "R-721", "manager", 7.4, 7.0),
        (24, 1, deliver, 47, "R-721"), (25, 1, approve, 47),
        (20, 2, sign, 49, me, 12.0, 6.0), (21, 1, run, 49, "R-b0a", 4.35), (22, 1, read, 49, "R-b0a", "manager", 5.8),
        (23, 1, run, 49, "R-97a", 3.12, 1, "employee", "R-b0a"), (24, 1, read, 49, "R-97a", "manager", 7.3),
        (25, 1, deliver, 49, "R-97a"), (26, 1, approve, 49),
        # contract-48, B-82c, escrow: rejected, disputed, ruled for the manager; the arbiter's fee paid
        (20, 2, sign, 48, me, 12.0, 6.0), (21, 1, run, 48, "R-025", 7.92, 2, "manager"),
        (23, 1, read, 48, "R-025", "manager", 7.5, 7.3, 6.6, 6.4), (23, 2, deliver, 48, "R-025", 7.5),
        (24, 1, reject, 48, quoted(text, 'the buyer\'s message: "', '"\n')), (25, 1, dispute, 48),
        (26, 0, rule, 48, 6.7), (26, 1, pay_fee, 48),
    ]
    events += [(12 + i, 2, say, (me, bb79)[i % 2], (bb79, me)[i % 2]) for i in range(10)]
    events += [said(text, me, *message) for message in (
        (4, 2, me, b82c), (5, 3, me, b82c), (8, 3, me, b82c), (9, 1, me, b82c), (22, 2, b82c, me),
        (27, 1, me, bb79), (28, 2, me, bb79))]
    play(m, events, 28, 3)
    stored(m, me, text, 28, 2, bb79)
    return m


def test_a_manager_late_in_a_long_episode():
    text = example("view_manager_all_M-7f8_day28_round3.txt")
    assert view.build(manager_day_28(text), "M-7f8")[0] == text
