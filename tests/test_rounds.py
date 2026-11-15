"""One round (market/rounds.py) against spec/rounds.md: the order of section 11 as the log records it, the takers
(2), the views (3), the waves and their take-up (4, 8.4), the three reading cases (5), who gets a correction and
what it is told (8.1 to 8.3, on the spec's own examples), the correction phase and its hidden ids (8.6), the end of
the round (9), lost turns (10) and message_channels (12); with spec/turns.md 9 to 13 for the lines, the results text
and the memory, and spec/running.md 6.3 D for the log.

Models are scripted: a backend returns the replies it was given, in order, then empty acts. Every expected text is
copied from the spec section the test names, or read from the spec file itself."""

import copy
import json
import pathlib
import pickle
from concurrent.futures import ThreadPoolExecutor

import pytest

from market import config, episode, prompts, rounds, state, turns, view
from market.models import CallFailed, CostCapReached, Reply
from market.state import Bid, Contract, Delivery, Market, Memory, Message, Post, Result, Task

SPEC = pathlib.Path(__file__).parent.parent / "spec"
B1, B2, M1, M2 = "B-c53", "B-d75", "M-14b", "M-849"
ORDER = [M1, B2, B1, M2]  # a turn order that is not the seat order
BUYER_LIST = ("pay, post_task, accept_bid, test_delivery, approve_submission, reject_submission, pay_escrow_arbiter, "
              "end_contract, edit_post, withdraw_post, keep_delivery")
MANAGER_LIST = "pay, bid, delegate_run, run_myself, test_result, deliver, dispute, withdraw_bid"
HEADING, CORRECTION, NO_ACTIONS = "Results of your actions:", "Correction:", "(no actions run)"
NO_MEMORY = "(note: your reply had no memory; your memory is unchanged)"
NOT_A_CALL = "your reply was not a function call: every reply must call compute or act"
NO_ACT = "your reply did not call act: your turn ends with act, with your actions and your memory"
NOT_SENT = "(note: your correction could not be sent; the results above stand)"
OUT = "Your balance is at or below 0: you are out of business and cannot act."
BACK = "Your balance is positive again: you are back in business."
CANNOT_ACT = "you are out of business (balance at or below 0) and cannot act"
ONE_FAILED = (
    "1 of your actions failed (the results of your reply are below; the actions that succeeded have run and stand). "
    "You may send up to 1 action now to replace it, in the same format as before, with your memory, which then "
    "replaces the one you just sent. This is your only correction this turn, and compute cannot be called in it.")
TWO_FAILED = (
    "2 of your actions failed (the results of your reply are below; the actions that succeeded have run and stand). "
    "You may send up to 2 actions now to replace them, in the same format as before, with your memory, which then "
    "replaces the one you just sent. This is your only correction this turn, and compute cannot be called in it.")
UNREADABLE = (
    "Your reply could not be read, so none of its actions ran (below). You may call act again now, with up to 6 "
    "actions and your memory. This is your only correction this turn, and compute cannot be called in it.")
NEWS = "What reached you from this round so far (your next view shows it too):"


def block(name, marker):
    """The text of the first fenced block after a marker in a spec file."""
    text = (SPEC / name).read_text(encoding="utf-8")
    start = text.index("```\n", text.index(marker)) + 4
    return text[start:text.index("\n```", start)]


EXAMPLES = block("rounds.md", "Examples, each a whole result line as sent:").split("\n")


# Scripted models


def call(name, arguments, ident="c1"):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": arguments}}


def a(name, **args):
    return {"action": name, **args}


def act(*actions, memory="m", **fields):
    """A reply that calls act with these actions and this memory (None: no memory)."""
    arguments = {"actions": list(actions)} | ({} if memory is None else {"memory": memory})
    return Reply(tool_calls=[call("act", json.dumps(arguments), "a1")], **fields)


def compute(code="print(1)", **fields):
    return Reply(tool_calls=[call("compute", json.dumps({"code": code}))], **fields)


def words(**fields):
    """A reply with no function call: a format error (turns.md, 7.1)."""
    return Reply(text="I will think about it.", **fields)


FAILED = CallFailed("HTTP 402 for model 'm': no credit")
BOGUS = a("end_turn")  # turns.md, 7.6: an unknown action
BOGUS_BUYER = f"- end_turn: ERROR: unknown action 'end_turn'. Your actions: {BUYER_LIST}."
BOGUS_MANAGER = f"- end_turn: ERROR: unknown action 'end_turn'. Your actions: {MANAGER_LIST}."


class Scripted:
    """A model that gives its replies in order, raising an exception among them, then empty acts."""

    def __init__(self, pid, replies, called):
        self.pid, self.replies, self.called, self.requests = pid, list(replies), called, []

    def call(self, messages, functions=None, tool_choice=None):
        self.called.append(self.pid)
        self.requests.append({"messages": copy.deepcopy(messages), "functions": functions, "tool_choice": tool_choice})
        item = self.replies.pop(0) if self.replies else act()
        if isinstance(item, Exception):
            raise item
        return item


class Tracker:
    """A cost tracker that notes each check in the log and stops at the check it is told to."""

    def __init__(self, stop_at=None):
        self.checks, self.stop_at, self.records = 0, stop_at, None

    def check(self):
        self.checks += 1
        self.records.append({"type": "cap_check"})
        if self.checks == self.stop_at:
            raise CostCapReached("cost cap $1.00 reached (spent $1.00)")


def program(code, compute_block):
    """A program runner: a blank program is refused, `print(sum(range(10)))` prints 45, any other prints 1."""
    if not code.strip():
        return "refused", None, 0.0, "(program not run: no program given)"
    return "ok", 0, 0.01, "45" if code == "print(sum(range(10)))" else "1"


def backwards(fn, items):
    """A map that runs the last chain first."""
    items = list(items)
    return [fn(item) for item in reversed(items)][::-1]


class Round:
    """One round played with scripted models; its log records, its backends and the order they were called in."""

    def __init__(self, m, scripts, order=ORDER, mapper=map, tracker=None, summarizer=None):
        self.records, self.called, self.tracker = [], [], tracker or Tracker()
        self.tracker.records = self.records
        self.backends = {pid: Scripted(pid, scripts.get(pid, ()), self.called) for pid in m.participants}
        m.log = self.records.append
        self.ctx = rounds.Ctx(self.backends, summarizer, self.tracker, program, mapper)
        rounds.play(m, order, self.ctx)

    def of(self, *types):
        return [r for r in self.records if r["type"] in types]

    def view(self, pid):
        """The view the participant's turn was sent."""
        return self.backends[pid].requests[0]["messages"][1]["content"]

    def body(self, pid):
        """The body of the participant's correction: the content of its request's last message."""
        return self.backends[pid].requests[-1]["messages"][-1]["content"]


def from_the_start(m, scripts, **how):
    """A round played right after the episode's fresh start: the round, and the whole log as a reader has it,
    each record read back from its JSON line, the record of position n at index n - 1 (running.md, 6.2)."""
    start = []
    m.log = start.append
    episode.fresh_start(m)
    r = Round(m, scripts, **how)
    return r, json.loads(json.dumps(start + [x for x in r.records if "n" in x]))


def requests(log, pid):
    """The requests of a participant's calls, failed ones too, as rounds.request rebuilds them from the log."""
    return [rounds.request(log, x) for x in log if x["type"] in ("call", "call_failed") and x["participant"] == pid]


def who(records):
    return [(r["type"], r.get("participant", r.get("to"))) for r in records]


# Markets made by hand


def market(mode="off", day=5, rnd=1):
    m = Market(seed=0, cfg=config.resolve({"message_channels": mode}))
    for pid in (B1, B2):
        state.add_participant(m, pid, "buyer")
    for pid in (M1, M2):
        state.add_participant(m, pid, "manager")
    m.day, m.round = day, rnd
    return m


def contract(m, n, form="plain", price=20.0, deadline=9, label=None, delivered=None, buyer=B1, manager=M1):
    """contract-n on task-n, signed on day 1; `label` is an arrived result of it, `delivered` the day that
    result was delivered, or submitted and is under review."""
    cid, task, post = f"contract-{n}", f"task-{n}", f"post-{n}"
    m.tasks[task] = Task(task, buyer, 60, deadline, "contracted", post, cid)
    m.posts[post] = Post(post, task, buyer, 1, 1, closed="accepted", closed_day=1, closed_round=3)
    c = m.contracts[cid] = Contract(cid, task, post, buyer, manager, form, price,
                                    6.0 if form == "escrow" else None, 1, deadline)
    if label:
        c.results.append(Result(label, "employee", "fresh", None, 1, 2, 6.0))
        c.label_draws = 1
    if delivered:
        c.deliveries.append(Delivery(label, delivered, 1, outcome="review" if form == "escrow" else None))
    return c


def post(m, n, buyer):
    """An open post made on day 1, round 1: its bids are open to its buyer."""
    m.tasks[f"task-{n}"] = Task(f"task-{n}", buyer, 60, 12, "posted", f"post-{n}")
    m.posts[f"post-{n}"] = Post(f"post-{n}", f"task-{n}", buyer, 1, 1)


def bid(m, n, post_id, manager, price, threshold=None):
    m.bids[f"bid-{n}"] = Bid(f"bid-{n}", post_id, manager, price, threshold)
    m.bids_created = max(m.bids_created, n)


def base(mode="off"):
    """Day 5, round 1. B-c53 and M-14b hold contract-1 (plain, past its deadline day 4, nothing delivered, R-a01
    arrived), contract-2 (plain, R-b02 delivered) and contract-3 (escrow, R-c03 under review). B-c53's post-9 is
    open with bid-1 of M-14b and bid-2 of M-849, its post-8 with bid-3 of M-14b; B-d75's post-7 is open with no
    bid, and its task-10 is in its queue."""
    m = market(mode)
    contract(m, 1, deadline=4, label="R-a01")
    contract(m, 2, label="R-b02", delivered=4)
    contract(m, 3, "escrow", label="R-c03", delivered=4)
    post(m, 7, B2)
    post(m, 8, B1)
    post(m, 9, B1)
    bid(m, 1, "post-9", M1, 20.0)
    bid(m, 2, "post-9", M2, 18.0, 5.0)
    bid(m, 3, "post-8", M1, 15.0)
    m.tasks["task-10"] = Task("task-10", B2, 60, 12)
    return m


DELIVER = a("deliver", contract_id="contract-1", result="R-a01")
DELIVERED = "[day 5, round 1] M-14b delivered R-a01 for contract-1 (delivery #1). You can test_delivery it."


# rounds.md, 11: the order of what happens in a round


def test_the_order_of_a_round_as_the_log_records_it():
    m = base()
    r = Round(m, {
        M1: [act(DELIVER, cost=0.25)],
        B2: [FAILED],
        B1: [act(a("end_contract", contract_id="contract-1"), a("pay", to=M1, amount=5)),
             act(a("pay", to=M2, amount=1), memory="again")],
        M2: [compute(), act()],
    })
    assert who(r.records) == [
        ("round", None),                                                    # 1: the takers decided
        ("system_prompt", M1), ("turn", M1), ("system_prompt", B2), ("turn", B2),   # 2: views and turns
        ("system_prompt", B1), ("turn", B1), ("system_prompt", M2), ("turn", M2),
        ("cap_check", None),                                                # 3: the cap, then the first calls
        ("money", M1), ("call", M1), ("call_failed", B2), ("call", B1),
        ("call", M2), ("program", M2), ("call", M2),
        ("lost_turn", B2),                                                  # 4: turns lost to the provider
        ("reply", M1), ("reply", B1), ("reply", M2),                        # 5: acts read
        ("phase", None),                                                    # 6: the first phase
        ("delivery", None), ("notification", B1), ("action", M1),
        ("action", B1),
        ("money", B1), ("money", M1), ("payment", None), ("notification", M1), ("action", B1),
        ("clash", None),
        ("cap_check", None), ("call", B1), ("reply", B1),                   # 8: the correction
        ("phase", None),
        ("money", B1), ("money", M2), ("payment", None), ("notification", M2), ("action", B1),
        ("credit_landed", M1), ("credit_landed", M2),                       # 9: the round's credits land
        ("memory", M1), ("memory", B1), ("memory", M2),                     # 10: each turn ends
    ]
    assert [x["n"] for x in r.records if "n" in x] == list(range(1, len(r.records) - 1))
    assert all((x["day"], x["round"]) == (5, 1) for x in r.records if "n" in x)
    assert r.records[0]["order"] == ORDER and r.records[0]["takers"] == ORDER
    assert [(x["phase"], x["order"]) for x in r.of("phase")] == [
        ("first", [(M1, 0, "deliver"), (B1, 0, "end_contract"), (B1, 1, "pay")]), ("correction", [(B1, 0, "pay")])]
    assert [(x["call"], x["step"]) for x in r.of("call", "call_failed", "program")] == [
        ("decide", 1), ("decide", 1), ("decide", 1), ("decide", 1), ("decide", 1), ("decide", 2), ("correction", 1)]
    assert [x["call"] for x in r.of("reply")] == ["decide"] * 3 + ["correction"]
    assert m.deferred == [] and m.actor is None
    assert pickle.loads(pickle.dumps(m)) == copy.deepcopy(m)  # the round left nothing but plain data


# rounds.md, 2: who takes a turn


def test_takers_out_of_business_at_the_start_during_the_round_and_back_at_its_end():
    m = base()
    waiting = "[day 4, round 3] " + OUT
    m.participants[M2].balance, m.participants[M2].out_of_business = -1.0, True
    m.participants[M2].pending = [waiting]
    m.participants[M1].balance = 1.0
    r = Round(m, {
        M1: [act(a("test_result", contract_id="contract-2", result="R-b02"), a("bid", post_id="post-7", price=9),
                 memory="mine")],
        B1: [act(a("pay", to=M2, amount=5), a("pay", to=M1, amount=3))],
    })
    out, back = m.participants[M2], m.participants[M1]
    # out of business at the round's start: no view, no call, no charge, no turn logged, no memory write
    assert r.records[0]["order"] == ORDER and r.records[0]["takers"] == [M1, B2, B1]
    assert r.backends[M2].requests == [] and M2 not in [x["participant"] for x in r.of(
        "system_prompt", "turn", "call", "reply", "action", "memory")]
    assert (out.turns, out.results, out.memory, out.prompt_logged) == (0, None, None, False)
    # put back in business by money landing at the round's end: its notifications wait for its next turn
    assert (out.balance, out.out_of_business) == (4.0, False)
    assert out.pending == [waiting, f"[day 5, round 1] {B1} paid you 5", "[day 5, round 1] " + BACK]
    # a taker that goes out of business during the round keeps its turn up to that point, and stays a taker
    assert back.results[1].startswith("- test_result: OK: R-b02 reading: ")
    assert back.results[2:] == ["(note: your remaining 1 action did not run: you are out of business)"]
    assert (back.balance, back.out_of_business, back.memory) == (3.0, False, Memory("mine", 5, 1, 1, False))
    assert back.pending == [f"[day 5, round 1] {B1} paid you 3 for contract-2", "[day 5, round 1] " + OUT,
                            "[day 5, round 1] " + BACK]
    assert who(r.of("out_of_business", "credit_landed", "back_in_business", "memory")) == [
        ("out_of_business", M1), ("credit_landed", M2), ("back_in_business", M2), ("credit_landed", M1),
        ("back_in_business", M1), ("memory", M1), ("memory", B2), ("memory", B1)]
    assert not r.of("clash") and len(r.backends[M1].requests) == 1  # stopped actions bring no correction
    # it takes turns from the next round, and its view then shows what waited
    m.round = 2
    r = Round(m, {})
    assert r.records[0]["takers"] == ORDER and out.turns == 1 and out.pending == []
    assert "\n\n".join(["--- Notifications since your last turn ---", waiting, f"[day 5, round 1] {B1} paid you 5",
                        "[day 5, round 1] " + BACK]) in r.view(M2)
    assert "--- Results of your actions in your previous turn ---\n\n--- Notifications" in r.view(M2)


def test_a_manager_at_a_balance_of_zero_is_in_business_until_its_first_token_charge():
    """rounds.md, 2: the status decides, not the balance; a charge of 0 changes nothing (turns.md, 13.1)."""
    m = base()
    m.participants[M1].balance = m.participants[M2].balance = 0.0
    r = Round(m, {M1: [act(cost=0.0)], M2: [act(cost=0.000001)]})
    assert r.records[0]["takers"] == ORDER
    assert [(p.balance, p.out_of_business) for p in (m.participants[M1], m.participants[M2])] == [
        (0.0, False), (-0.000001, True)]
    assert m.participants[M1].ledger == [] and m.participants[M2].ledger == [("tokens", 0.000001)]
    assert [x["charge"] for x in r.of("call")] == [0.0, 0.0, 0.0, 0.000001]


# rounds.md, 3: views, all from the market as the round starts


def test_views_and_turn_starts():
    m = base()
    m.participants[M1].balance = 76.804
    expected = {pid: view.build(copy.deepcopy(m), pid)[0] for pid in ORDER}
    r = Round(m, {M1: [act(DELIVER, cost=0.5)], B2: [act(a("post_task", task_id="task-10"))]})
    for pid in ORDER:
        p = m.participants[pid]
        system = prompts.system_prompt(m.cfg, p.role, pid, p.share, p.seat)
        assert r.backends[pid].requests[0]["messages"] == [{"role": "system", "content": system},
                                                           {"role": "user", "content": expected[pid]}]
        assert r.backends[pid].requests[0]["functions"] == turns.functions(m.cfg)
        assert r.backends[pid].requests[0]["tool_choice"] == "required"
        assert (p.turns, p.last_view, p.prompt_logged) == (1, 13, True)
    assert [(x["participant"], x["text"]) for x in r.of("system_prompt")] == [
        (pid, r.backends[pid].requests[0]["messages"][0]["content"]) for pid in ORDER]
    # the turn's start is logged with the balance its view showed, before this round's token charges
    assert [(x["participant"], x["role"], x["balance"]) for x in r.of("turn")] == [
        (M1, "manager", 76.8), (B2, "buyer", 60.0), (B1, "buyer", 60.0), (M2, "manager", 60.0)]
    assert "Your balance: 76.80 credits." in r.view(M1) and m.participants[M1].balance == 76.304
    assert not any("post-10" in r.view(pid) or "R-a01 for contract-1" in r.view(pid) for pid in ORDER)


def test_a_second_rounds_views_show_what_the_first_did():
    m = base()
    Round(m, {M1: [act(DELIVER, memory="delivered", cost=0.5)],
              B2: [act(a("post_task", task_id="task-10"), BOGUS), act()],
              B1: [act(a("pay", to=M1, amount=5))]})
    m.round = 2
    r = Round(m, {}, order=[B1, M2, M1, B2])
    assert not r.of("system_prompt")  # logged once, at the participant's first turn
    assert all(m.participants[pid].turns == 2 and m.participants[pid].pending == [] for pid in ORDER)
    assert "=== DAY 5, ROUND 2 of 3 today === You are M-14b (manager). Your balance: 64.50 credits." in r.view(M1)
    assert ("--- Results of your actions in your previous turn ---\n\nResults of your actions:\n"
            "- deliver: OK: delivered R-a01 for contract-1 to B-c53 (delivery #1, 1 day after the deadline)\n\n"
            "--- Notifications since your last turn ---\n\n[day 5, round 1] B-c53 paid you 5 for contract-2\n\n"
            "--- Payments") in r.view(M1)
    assert "[day 5, round 1] B-c53 -> you: 5 credits for contract-2" in r.view(M1)
    assert ("--- Your self-written memory (last updated day 5, round 1, with your last actions, before their "
            "results) ---\n\ndelivered\n") in r.view(M1)
    assert f"--- Notifications since your last turn ---\n\n{DELIVERED}\n\n--- Payments" in r.view(B1)
    assert ("Results of your actions:\n- post_task: OK: posted task-10 as post-10\n\n" + BOGUS_BUYER
            + "\n\nCorrection:\n(no actions run)\n\n--- Notifications") in r.view(B2)
    assert "post-10 by B-d75 | task-10 |" in r.view(M2)


# rounds.md, 4: the first calls, taken up in the turn order


def scripts_of_a_wave():
    return {M1: [compute(cost=0.125), compute("print(2)", cost=0.25), FAILED],
            B2: [act(cost=0.5)],
            B1: [compute("  ", cost=1.0), compute(cost=2.0), act(BOGUS, cost=4.0), act(cost=8.0)],
            M2: [act(BOGUS, cost=16.0, input_tokens=7, output_tokens=5, reasoning_tokens=3, cost_known=False),
                 act(cost=32.0)]}


def test_replies_are_taken_up_in_the_turn_order_whatever_order_the_chains_finish_in():
    forward, reverse = base(), base()
    one, two = Round(forward, scripts_of_a_wave()), Round(reverse, scripts_of_a_wave(), mapper=backwards)
    assert one.called == [M1, M1, M1, B2, B1, B1, B1, M2, B1, M2]
    assert two.called == [M2, B1, B1, B1, B2, M1, M1, M1, M2, B1]
    assert one.records == two.records and copy.deepcopy(forward) == copy.deepcopy(reverse)
    with ThreadPoolExecutor(4) as pool:  # and the same with the chains running at the same time
        assert Round(base(), scripts_of_a_wave(), mapper=pool.map).records == one.records
    assert who(one.of("money", "call", "program", "call_failed", "lost_turn", "reply")) == [
        ("money", M1), ("call", M1), ("program", M1), ("money", M1), ("call", M1), ("call_failed", M1),
        ("money", B2), ("call", B2),
        ("money", B1), ("call", B1), ("program", B1), ("money", B1), ("call", B1), ("program", B1),
        ("money", B1), ("call", B1),
        ("money", M2), ("call", M2),
        ("lost_turn", M1), ("reply", B2), ("reply", B1), ("reply", M2),
        ("money", B1), ("call", B1), ("money", M2), ("call", M2), ("reply", B1), ("reply", M2)]
    assert [(x["participant"], x["amount"], x["balance"]) for x in one.of("money")] == [
        (M1, -0.125, 59.875), (M1, -0.25, 59.625), (B2, -0.5, 59.5), (B1, -1.0, 59.0), (B1, -2.0, 57.0),
        (B1, -4.0, 53.0), (M2, -16.0, 44.0), (B1, -8.0, 45.0), (M2, -32.0, 12.0)]
    assert [x["charge"] for x in one.of("call")] == [0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 16.0, 8.0, 32.0]
    assert forward.participants[B1].ledger == [("tokens", 1.0), ("tokens", 2.0), ("tokens", 4.0), ("tokens", 8.0)]


def test_what_the_log_records_of_calls_programs_and_replies():
    """running.md, 6.3 D."""
    m = base()
    r, log = from_the_start(m, scripts_of_a_wave())
    calls, backend = r.of("call"), r.backends[B1]
    assert len(json.loads(json.dumps(r.records))) == len(r.records)  # every record can be a JSON line (6.2)
    assert requests(log, B1) == backend.requests
    assert [x["request"]["tool_choice"] for x in calls if x["participant"] == B1] == [
        "required", "required", turns.ACT_FORCED, turns.ACT_FORCED]
    assert calls[-2]["request"]["functions"] == ["act"]
    assert rounds.request(log, calls[-2])["functions"] == turns.functions(m.cfg)[1:]
    assert all(x["spec"] == config.seat_spec(m.cfg, m.participants[x["participant"]].role, 0) for x in calls)
    reply = calls[6]["reply"]
    assert (reply["tool_calls"], reply["text"], reply["reasoning"], reply["finish_reason"]) == (
        [call("act", json.dumps({"actions": [BOGUS], "memory": "m"}), "a1")], "", "", None)
    assert (reply["input_tokens"], reply["output_tokens"], reply["reasoning_tokens"]) == (7, 5, 3)
    assert (reply["cost"], reply["cost_known"], reply["cost_source"], reply["failed_cost"]) == (
        16.0, False, "reported", 0.0)
    assert [{k: v for k, v in x.items() if k not in ("n", "type", "day", "round")} for x in r.of("program")] == [
        {"participant": M1, "call": "decide", "step": 1, "code": "print(1)", "status": "ok", "exit_code": 0,
         "wall": 0.01, "output": "1"},
        {"participant": B1, "call": "decide", "step": 1, "code": "  ", "status": "refused", "exit_code": None,
         "wall": 0.0, "output": "(program not run: no program given)"},
        {"participant": B1, "call": "decide", "step": 2, "code": "print(1)", "status": "ok", "exit_code": 0,
         "wall": 0.01, "output": "1"}]
    failed = r.of("call_failed")[0]
    assert (failed["participant"], failed["call"], failed["step"]) == (M1, "decide", 3)
    assert (failed["error"], failed["failed_cost"]) == ("HTTP 402 for model 'm': no credit", 0.0)
    assert rounds.request(log, failed) == r.backends[M1].requests[2] == requests(log, M1)[2]
    assert [(x["participant"], x["call"], x["actions"], x["memory"], x["error"]) for x in r.of("reply")] == [
        (B2, "decide", [], "m", None), (B1, "decide", [("end_turn", {})], "m", None),
        (M2, "decide", [("end_turn", {})], "m", None), (B1, "correction", [], "m", None),
        (M2, "correction", [], "m", None)]
    assert m.counts == {
        "manager_turns": 2, "buyer_turns": 2, "manager_calls": 4, "buyer_calls": 5, "manager_programs": 1,
        "buyer_programs": 1, "manager_input_tokens": 7, "manager_output_tokens": 5, "manager_reasoning_tokens": 3,
        "buyer_input_tokens": 0, "buyer_output_tokens": 0, "buyer_reasoning_tokens": 0, "manager_dollars": 48.375,
        "buyer_dollars": 15.5, "calls_unknown_cost": 1, "manager_turns_lost_provider": 1, "manager_actions": 1,
        "manager_failed_actions": 1, "buyer_actions": 1, "buyer_failed_actions": 1, "manager_corrections": 1,
        "buyer_corrections": 1}


def test_the_log_holds_each_request_by_reference_and_rebuilds_it_exactly():
    """running.md, 6.3 D: "the full request, recoverable exactly: its messages in order (the system prompt and
    earlier messages of the same turn may be given by reference to earlier records), the functions offered, the
    tool choice". Turns with a compute step, a free retry (turns.md, 5.4) and a correction on each path of
    turns.md, 11.4, and calls that fail: every request the log rebuilds is the one the model received."""
    decoded = Reply(tool_calls=[call("act", {"actions": [BOGUS], "memory": "m"}, "a1")])  # not a string: text path
    r, log = from_the_start(base(), {
        M1: [compute("  ", reasoning="hm"), compute(text="<think>so</think>next"), act(BOGUS, reasoning="well"),
             act()],  # the tool path
        B2: [compute("  "), compute(), words(reasoning="why"), act()],  # the text path, after an unreadable reply
        B1: [compute(), decoded, FAILED],  # the text path, and the correction call fails
        M2: [compute("  "), FAILED]})  # the second decide call fails
    assert [x["type"] for x in log[:2]] == ["fresh_start", "functions"]
    assert [x["n"] for x in log] == list(range(1, len(log) + 1))
    expected = {  # per call: its type, which call it is, how many messages it keeps, the roles of its own
        M1: [("call", "decide", 0, ["user"]), ("call", "decide", 1, ["assistant", "tool"]),
             ("call", "decide", 3, ["assistant", "tool"]), ("call", "correction", 5, ["assistant", "tool"])],
        B2: [("call", "decide", 0, ["user"]), ("call", "decide", 1, ["assistant", "tool"]),
             ("call", "decide", 3, ["assistant", "tool"]), ("call", "correction", 5, ["assistant", "user"])],
        B1: [("call", "decide", 0, ["user"]), ("call", "decide", 1, ["assistant", "tool"]),
             ("call_failed", "correction", 3, ["assistant", "user"])],
        M2: [("call", "decide", 0, ["user"]), ("call_failed", "decide", 1, ["assistant", "tool"])]}
    definitions = turns.functions(base().cfg)
    for pid in ORDER:
        mine = [x for x in log if x["type"] in ("call", "call_failed") and x["participant"] == pid]
        received = r.backends[pid].requests
        assert [json.dumps(rounds.request(log, x)) for x in mine] == [json.dumps(x) for x in received]  # exactly
        assert [(x["type"], x["call"], x["request"]["kept"], [y["role"] for y in x["request"]["messages"]])
                for x in mine] == expected[pid]
        # each call continues the record of the same turn's call before it, whatever was logged in between
        assert [x["request"]["prior"] for x in mine] == [None] + [x["n"] for x in mine[:-1]]
        assert [x["request"]["functions"] for x in mine] == [
            ["act"] if x["call"] == "correction" else ["compute", "act"] for x in mine]
        assert [x["request"]["tool_choice"] for x in mine] == [x["tool_choice"] for x in received]
        assert all(set(x["request"]) == {"prior", "kept", "messages", "functions", "tool_choice"} for x in mine)
        # the prompt and the view are in the log once
        system, view_text = (x["content"] for x in received[0]["messages"])
        assert [x["text"] for x in log if x["type"] == "system_prompt" and x["participant"] == pid] == [system]
        assert json.dumps(log).count(json.dumps(view_text)) == 1 and json.dumps(system) not in json.dumps(mine)
    assert [x["functions"] for x in log if x["type"] == "functions"] == [definitions]  # and the definitions
    assert json.dumps(log).count(json.dumps(definitions[1])) == 1
    # the round's calls are interleaved in the log: M-14b's correction continues its own third call
    third, correction = [x for x in log if x["type"] == "call" and x["participant"] == M1][2:]
    between = {x["participant"] for x in log[third["n"]:correction["n"] - 1] if x["type"] == "call"}
    assert correction["request"]["prior"] == third["n"] and between == {B2, B1, M2}


def test_a_request_that_does_not_extend_the_one_before_it():
    """running.md, 6.3 D: `kept` is how many messages the two requests share from their start, so a request is
    rebuilt exactly also when it changes or drops a message of the one before it."""
    definitions, system = turns.functions(config.resolve(None)), {"role": "system", "content": "S"}
    a, b, c, x = ({"role": "user", "content": text} for text in "abcx")
    sent = [{"messages": [system, a, b, c], "functions": definitions, "tool_choice": "required"},
            {"messages": [system, a, x, c], "functions": definitions[1:], "tool_choice": turns.ACT_FORCED},
            {"messages": [system, a], "functions": definitions[:1], "tool_choice": "required"},
            {"messages": [system, a, x], "functions": definitions, "tool_choice": "required"}]
    log = [{"n": 1, "type": "functions", "functions": definitions},
           {"n": 2, "type": "system_prompt", "participant": "p", "text": "S"}]
    last = None
    for request in sent:
        logged, rest = rounds._by_reference(request, last)
        log.append({"n": len(log) + 1, "type": "call", "participant": "p", "request": logged})
        last = len(log), rest
    assert [r["request"] for r in log[2:]] == [
        {"prior": None, "kept": 0, "messages": [a, b, c], "functions": ["compute", "act"], "tool_choice": "required"},
        {"prior": 3, "kept": 1, "messages": [x, c], "functions": ["act"], "tool_choice": turns.ACT_FORCED},
        {"prior": 4, "kept": 1, "messages": [], "functions": ["compute"], "tool_choice": "required"},
        {"prior": 5, "kept": 1, "messages": [x], "functions": ["compute", "act"], "tool_choice": "required"}]
    assert [rounds.request(log, r) for r in log[2:]] == sent


def test_the_cost_cap_is_checked_once_before_each_wave_and_its_stop_passes_through():
    m = base()
    r = Round(m, {B1: [act(BOGUS)]})
    assert r.tracker.checks == 2  # the first calls, then the correction calls
    assert Round(base(), {}).tracker.checks == 1  # no correction: no second wave
    for stop_at, calls, actions in ((1, 0, 0), (2, 4, 1)):
        m, tracker = base(), Tracker(stop_at)
        with pytest.raises(CostCapReached, match="cost cap"):
            Round(m, {B1: [act(BOGUS)]}, tracker=tracker)
        types = [x["type"] for x in tracker.records]
        assert (types.count("call"), types.count("action"), types[-1]) == (calls, actions, "cap_check")
        assert not {"credit_landed", "memory"} & set(types) and m.participants[B1].results is None


# rounds.md, 4 item 4, 6.6 and 8.2: out of business by its own tokens, before any action runs


def test_a_manager_put_out_of_business_by_its_own_tokens_before_the_first_phase():
    m = base()
    post(m, 6, B1)
    m.participants[M1].balance = 0.4
    r = Round(m, {
        M1: [act(a("bid", post_id="post-7", price=9), a("pay", to=B1, amount=0.1), memory="broke", cost=0.5)],
        M2: [act(a("bid", post_id="post-8", price=9), a("bid", post_id="post-6", price=8))],
        B1: [act(a("accept_bid", bid_id="bid-3"), BOGUS),
             act(a("accept_bid", bid_id="bid-5"), a("accept_bid", bid_id="bid-4"), memory="took it")],
    })
    out = m.participants[M1]
    assert who(r.records[9:15]) == [("cap_check", None), ("money", M1), ("out_of_business", M1),
                                    ("notification", M1), ("call", M1), ("call", B2)]
    assert (r.of("out_of_business")[0]["cause"], r.of("out_of_business")[0]["balance"]) == ("tokens", out.balance)
    assert out.balance == pytest.approx(-0.1) and out.out_of_business
    # its first action fails on its shadow and the rest do not run; no correction; its memory is written
    assert out.results == [HEADING, "- bid: ERROR: " + CANNOT_ACT,
                           "(note: your remaining 1 action did not run: you are out of business)"]
    assert len(r.backends[M1].requests) == 1 and out.memory == Memory("broke", 5, 1, 1, False)
    assert out.pending == ["[day 5, round 1] " + OUT,
                           "[day 5, round 1] Your bid bid-3 on post-8 lost: B-c53 accepted another bid."]
    assert [(x["participant"], x["name"], x["outcome"]) for x in r.of("action")][:3] == [
        (B1, "accept_bid", "ran"), (B1, "end_turn", "refused"), (M1, "bid", "refused")]
    # the acceptance of its bid fails with A10's note, with no A10 edge; the fact line about the post follows
    assert r.body(B1) == "\n".join([
        TWO_FAILED, "", "Results of your reply:", EXAMPLES[8], "", BOGUS_BUYER, "", NEWS,
        "[day 5, round 1] M-849 placed bid bid-4 on post-8 at 9", "[day 5, round 1] M-849 placed bid bid-5 on "
        "post-6 at 8", "", "Open bids on post-8 now: bid-4 by M-849 at 9."])
    assert [x["rule"] for x in r.of("clash")] == ["B_accept"]
    # 8.6: a bid shown open in its facts is on its correction's shadow; another made this round is not
    results = m.participants[B1].results
    assert results[:4] == [HEADING, EXAMPLES[8], BOGUS_BUYER, CORRECTION]
    assert results[4].startswith("- accept_bid: ERROR: no bid with id 'bid-5'. Open bids on your posts: ")
    assert results[5:] == ["- accept_bid: OK: created contract-8 with M-849 at price 9"]
    assert m.participants[B1].memory == Memory("took it", 5, 1, 1, True)
    assert m.bids["bid-3"].status == "lost" and m.contracts["contract-8"].manager == M2


# rounds.md, 5: reading each act


def test_the_three_cases_of_reading_an_act():
    m = base()
    m.participants[M2].balance = 0.25
    six = [a("pay", to=M1, amount=1)] * 7
    r = Round(m, {
        B1: [Reply(tool_calls=[call("compute", "{}"), call("act", json.dumps({"actions": six, "memory": "kept"}))])],
        B2: [words(finish_reason="length"), act(memory="after")],
        M2: [words(cost=0.5)],
    })
    # readable: the kept actions, the memory kept for the turn's end, the notes first
    assert r.of("reply")[2]["actions"] == [("pay", {"to": M1, "amount": 1})] * 6
    assert m.participants[B1].results == [
        HEADING, "(note: your compute call did not run: it was sent in the same reply as act)",
        "(note: only your first 6 actions were run)"] + ["- pay: OK: paid 1 to M-14b for contract-2"] * 6
    assert m.participants[B1].memory == Memory("kept", 5, 1, 1, False)
    # unreadable, in business: the format error logged, an empty plan, the FORMAT ERROR line, a correction
    cut = NOT_A_CALL + " - your reply hit the length limit before its act call was complete; reason more briefly"
    line = f"- FORMAT ERROR: {cut}: none of your actions ran"
    assert (r.of("reply")[1]["participant"], r.of("reply")[1]["error"], r.of("reply")[1]["actions"]) == (B2, cut, [])
    assert r.body(B2) == f"{UNREADABLE}\n\nResults of your reply:\n{line}"
    assert r.backends[B2].requests[1]["messages"][2:] == [
        {"role": "assistant", "content": "I will think about it."}, {"role": "user", "content": r.body(B2)}]
    assert m.participants[B2].results == [HEADING, line, CORRECTION, NO_ACTIONS]
    assert m.participants[B2].memory == Memory("after", 5, 1, 1, True)
    # unreadable, a manager already out of business by its own tokens: the turn is lost as a format error (10.2)
    lost = m.participants[M2]
    assert lost.out_of_business and len(r.backends[M2].requests) == 1 and lost.memory is None
    assert lost.results == [
        f"FORMAT ERROR: {NOT_A_CALL}. That turn was lost: none of its actions ran. End every turn by calling the "
        "function act with a list of your actions (an empty list does nothing) and your memory."]
    assert [(x["participant"], x["reason"], x["error"], x["contracts"]) for x in r.of("lost_turn")] == [
        (M2, "format error", NOT_A_CALL, [])]
    assert (lost.turns, lost.last_view, lost.pending, m.lost_turns) == (1, 13, ["[day 5, round 1] " + OUT], 0)
    # 6.1: every participant with a plan, an empty one included, gets the next reply number, in the turn order
    assert m.replies == 4 and {x["number"] for x in r.of("action")} == {3}
    assert (m.counts["buyer_format_errors"], m.counts["manager_format_errors"]) == (1, 1)
    assert (m.counts["manager_turns_lost_format"], m.counts["buyer_corrections"]) == (1, 1)
    assert "manager_turns_lost_provider" not in m.counts and "manager_corrections" not in m.counts


def test_reply_numbers():
    """rounds.md, 6.1: the next number for each participant with a plan, in the turn order; new ones in the
    correction phase; none for a lost turn."""
    m = base()
    m.replies = 10
    r = Round(m, {M1: [act(BOGUS), act(a("pay", to=B1, amount=1))], B2: [FAILED],
                  B1: [act(a("pay", to=M1, amount=1), BOGUS), act(BOGUS)], M2: [act()]})
    assert [(x["participant"], x["phase"], x["number"]) for x in r.of("action")] == [
        (M1, "first", 11), (B1, "first", 12), (B1, "first", 12), (M1, "correction", 14), (B1, "correction", 15)]
    assert m.replies == 15


def test_a_reply_that_called_compute_only_and_put_its_manager_out_of_business():
    """turns.md, 4.2 and 13.2: no program runs; read as an act with no actions and no memory."""
    m = base()
    m.participants[M1].balance = 0.25
    m.participants[M1].memory = before = Memory("old", 4, 3, 0, False)
    r = Round(m, {M1: [compute(cost=0.5)]})
    p = m.participants[M1]
    assert len(r.backends[M1].requests) == 1 and not r.of("program") and not r.of("lost_turn")
    assert (r.of("reply")[0]["actions"], r.of("reply")[0]["memory"], r.of("reply")[0]["error"]) == ([], None, None)
    assert p.results == [HEADING, NO_MEMORY] and p.memory is before and p.out_of_business
    assert who(r.of("memory")) == [("memory", B2), ("memory", B1), ("memory", M2)]


# rounds.md, 8.1 to 8.4: who gets a correction, and what it is told


def test_the_examples_of_8_2_a_lost_end_and_what_needed_it():
    m = base()
    r = Round(m, {
        M1: [act(DELIVER)],
        B1: [act(a("end_contract", contract_id="contract-1"), a("post_task", task_id="task-1")),
             act(a("pay", to=M1, amount=20, contract_id="contract-1"), memory=None)],
        B2: [act(a("post_task", task_id="task-10"))],
    })
    assert r.body(B1) == "\n".join([TWO_FAILED, "", "Results of your reply:", EXAMPLES[0], "", EXAMPLES[1], "", NEWS,
                                    DELIVERED])
    request = r.backends[B1].requests[1]
    assert (request["functions"], request["tool_choice"]) == (turns.functions(m.cfg)[1:], turns.ACT_FORCED)
    assert request["messages"][:2] == r.backends[B1].requests[0]["messages"]
    assert [x["role"] for x in request["messages"][2:]] == ["assistant", "tool"]
    assert request["messages"][3]["tool_call_id"] == "a1"
    # only B-c53 had a failed action: one correction in the round
    assert [len(r.backends[pid].requests) for pid in ORDER] == [1, 1, 2, 1]
    assert [x["participant"] for x in r.of("call") if x["call"] == "correction"] == [B1]
    # the notes stay in the turn's results; the news stay pending for the next view
    assert m.participants[B1].results == [HEADING, EXAMPLES[0], EXAMPLES[1], CORRECTION,
                                          "- pay: OK: paid 20 to M-14b for contract-1"]
    assert m.participants[B1].pending == [DELIVERED]
    assert m.participants[B1].memory == Memory("m", 5, 1, 1, False)  # the correction had none


def test_the_real_example_of_8_4_a_lost_run_with_news_and_the_balance_fact():
    m = market(day=17)
    contract(m, 9, "escrow", 22.0, 16)
    contract(m, 12, "escrow", label="R-f55", delivered=16, deadline=18)
    contract(m, 15, "escrow", 14.0, label="R-39f", delivered=16, deadline=18)
    contract(m, 19, "escrow", label="R-d08", delivered=16, deadline=18)
    m.participants[M1].balance = 86.58
    message = ("R-39f: my readings average ~5.0 and your stated quality 6.5 is overstated. It is also 4+ days past "
               "the task's due day, so its value to me is low. Refine it if you can and resubmit.")
    r = Round(m, {
        B1: [act(a("approve_submission", contract_id="contract-12"), a("approve_submission", contract_id="contract-19"),
                 a("reject_submission", contract_id="contract-15", message=message),
                 a("end_contract", contract_id="contract-9"))],
        M1: [act(a("delegate_run", contract_id="contract-9"), cost=0.5), act(cost=0.25)],
    })
    assert r.body(M1) == block("rounds.md", "Example (real log, a readable act; an A4 clash note, news and the")
    assert [len(r.backends[pid].requests) for pid in ORDER] == [2, 1, 1, 1]
    # 7 and 9.1: the price released lands at the round's end, after the correction call's charge
    assert [(x["participant"], x["balance"]) for x in r.of("money") if x["kind"] == "tokens"] == [
        (M1, 86.08), (M1, 85.83)]
    assert who(r.of("credit_landed", "memory")[:3]) == [("credit_landed", M1), ("credit_landed", M1), ("memory", M1)]
    assert m.participants[M1].balance == 125.83
    assert m.participants[M1].results == [HEADING, EXAMPLES[2], CORRECTION, NO_ACTIONS]


def test_the_real_example_of_turns_11_3_one_failed_bid_out_of_three():
    m = market(day=6, rnd=3)
    contract(m, 1, "escrow", 25.0, label="R-815", delivered=5)
    post(m, 6, B1)
    post(m, 7, B1)
    post(m, 8, B2)
    bid(m, 5, "post-8", M2, 20.0)
    m.bids_created = 20
    r = Round(m, {
        M1: [act(a("bid", post_id="post-8", price=24), a("bid", post_id="post-7", price=24, threshold=6),
                 a("bid", post_id="post-6", price=24, threshold=6))],
        B2: [act(a("accept_bid", bid_id="bid-5"))],
        B1: [act(a("approve_submission", contract_id="contract-1"))],
    })
    assert r.body(M1) == block("turns.md", "Real example (one failed bid out of three, with news):")
    assert r.body(M1).split("\n")[3] == EXAMPLES[6]


def test_the_messages_of_the_example_turns_of_acceptance_check_4():
    """README.md, acceptance check 4: a test model returns the examples' assistant messages; the messages the
    market writes after the view are the files'."""
    examples = {name: json.loads((SPEC / "examples" / f"turn_with_{name}.json").read_text(encoding="utf-8"))
                for name in ("compute_off_B-d75", "correction_off")}
    replies = {name: [Reply(text=x["content"], tool_calls=x["tool_calls"]) for x in file if x["role"] == "assistant"]
               for name, file in examples.items()}
    m = base()
    r = Round(m, {B2: replies["compute_off_B-d75"], M2: replies["correction_off"]})
    second, correction = r.backends[B2].requests[1], r.backends[M2].requests[2]
    assert second["messages"][2:] == examples["compute_off_B-d75"][1:]
    assert correction["messages"][2:] == examples["correction_off"][1:]
    assert second["messages"][:2] == r.backends[B2].requests[0]["messages"]
    assert correction["messages"][:2] == r.backends[M2].requests[0]["messages"]
    assert [x["tool_choice"] for x in r.backends[M2].requests] == ["required", turns.ACT_FORCED, turns.ACT_FORCED]
    assert [x["functions"] for x in r.backends[M2].requests] == [turns.functions(m.cfg)] * 2 + [
        turns.functions(m.cfg)[1:]]
    assert m.participants[M2].memory == Memory("m", 5, 1, 1, True)  # the correction's replaces "mock memory"


def test_a_correction_is_for_a_failed_action_or_an_unreadable_act_of_a_participant_in_business():
    """rounds.md, 8.1: stopped actions do not count, and a manager out of business after the first phase gets
    none, whatever failed."""
    m = base()
    m.participants[M1].balance = 1.0
    r = Round(m, {
        M1: [act(BOGUS, a("test_result", contract_id="contract-2", result="R-b02"), BOGUS)],
        M2: [act(a("bid", post_id="post-7", price=9))],
        B1: [act(a("pay", to=M2, amount=1))],
        B2: [words()],
    })
    assert [x["participant"] for x in r.of("call") if x["call"] == "correction"] == [B2]
    assert m.participants[M1].results[1] == BOGUS_MANAGER  # no note: it gets no correction
    assert m.participants[M1].results[3:] == ["(note: your remaining 1 action did not run: you are out of business)"]
    assert (m.counts["buyer_corrections"], m.counts.get("manager_corrections")) == (1, None)


def test_a_correction_plan_is_capped_at_the_number_of_failed_actions():
    """rounds.md, 8.4 and 8.6: the limit is the number of the first-phase actions that failed; any actions."""
    m = base()
    pay = a("pay", to=M1, amount=1)
    r = Round(m, {B1: [act(BOGUS, pay, BOGUS), act(pay, pay, pay, memory="  ")]})
    assert r.body(B1).startswith(TWO_FAILED + "\n\nResults of your reply:\n" + BOGUS_BUYER + "\n\n- pay: OK: ")
    paid = "- pay: OK: paid 1 to M-14b for contract-2"
    assert m.participants[B1].results == [HEADING, BOGUS_BUYER, paid, BOGUS_BUYER, CORRECTION,
                                          "(note: only your first 2 actions were run)", paid, paid]
    assert r.of("reply")[-1]["actions"] == [("pay", {"to": M1, "amount": 1})] * 2
    assert m.participants[B1].memory == Memory("m", 5, 1, 1, False)  # a blank memory counts as none


def test_a_correction_call_that_fails_after_a_readable_act():
    """rounds.md, 8.4: the first phase's results stand and the turn goes on."""
    m = base()
    r = Round(m, {B1: [act(a("pay", to=M1, amount=5), BOGUS, memory="mine"), FAILED]})
    p = m.participants[B1]
    assert p.results == [HEADING, "- pay: OK: paid 5 to M-14b for contract-2", BOGUS_BUYER, NOT_SENT]
    assert (p.memory, p.turns, m.lost_turns) == (Memory("mine", 5, 1, 1, False), 1, 0)
    assert who(r.of("call_failed", "lost_turn", "memory")) == [
        ("call_failed", B1), ("memory", M1), ("memory", B2), ("memory", B1), ("memory", M2)]
    assert (r.of("call_failed")[0]["call"], r.of("call_failed")[0]["step"]) == ("correction", 1)
    assert m.counts["buyer_corrections"] == 1 and "buyer_turns_lost_provider" not in m.counts
    assert len(r.of("phase")) == 1 and m.participants[M1].balance == 65.0


def test_a_correction_call_that_fails_after_an_unreadable_act_loses_the_turn_to_the_provider():
    m = base()
    p = m.participants[B1]
    before = [HEADING, NO_ACTIONS]
    p.pending, p.results, p.turns, p.last_view = ["[day 4] earlier"], before, 3, 12
    r = Round(m, {B1: [words(cost=0.5), FAILED], M1: [act(DELIVER)]})
    # restored as if this round's view had not been built: the notifications it showed, then those received since
    assert p.pending == ["[day 4] earlier", DELIVERED]
    assert (p.turns, p.last_view, p.memory) == (3, 12, None) and p.results is before
    assert p.balance == 59.5  # the reply that came back stays charged
    assert [(x["participant"], x["reason"], x["error"]) for x in r.of("lost_turn")] == [
        (B1, "failed model call", "HTTP 402 for model 'm': no credit")]
    assert (m.lost_turns, m.counts["buyer_turns_lost_provider"], m.counts["buyer_corrections"]) == (1, 1, 1)
    assert who(r.of("memory")) == [("memory", M1), ("memory", B2), ("memory", M2)]
    assert who(r.records)[-5:-2] == [("call_failed", B1), ("lost_turn", B1), ("memory", M1)]


def test_an_unreadable_correction():
    """rounds.md, 8.6 and 10.2; turns.md, 11.6 and 12."""
    m = base()
    other = Reply(tool_calls=[call("compute", "{}")], finish_reason="length")
    r = Round(m, {B1: [act(BOGUS, memory="first"), words()],  # readable, then unreadable
                  M1: [words(), other],  # unreadable twice
                  B2: [act(BOGUS, memory="first"), act(memory="second")]})
    line = f"Correction: FORMAT ERROR: {NOT_A_CALL}: none of its actions ran"
    assert m.participants[B1].results == [HEADING, BOGUS_BUYER, line]
    assert m.participants[B1].memory == Memory("first", 5, 1, 1, False)  # the correction's memory is discarded
    assert view.space(m.participants[B1].results) == f"{HEADING}\n{BOGUS_BUYER}\n{line}"
    err2 = NO_ACT + " - your reply hit the length limit before its act call was complete; reason more briefly"
    assert m.participants[M1].results == [
        f"FORMAT ERROR: {NOT_A_CALL}; and your correction: {err2}. That turn was lost: none of its actions ran. "
        "End every turn by calling the function act with a list of your actions (an empty list does nothing) and "
        "your memory."]
    assert m.participants[M1].memory is None and m.participants[M1].turns == 1
    assert [(x["participant"], x["reason"], x["error"]) for x in r.of("lost_turn")] == [(M1, "format error", err2)]
    assert [(x["participant"], x["call"], x["error"]) for x in r.of("reply")[-3:]] == [
        (M1, "correction", err2), (B2, "correction", None), (B1, "correction", NOT_A_CALL)]
    assert who(r.of("memory")) == [("memory", B2), ("memory", B1), ("memory", M2)]
    assert (m.counts["buyer_format_errors"], m.counts["manager_format_errors"]) == (1, 2)
    assert (m.counts["manager_turns_lost_format"], m.lost_turns) == (1, 0)
    # the correction phase ran for the one correction plan, an empty one: a reply number and no phase record
    assert [x["phase"] for x in r.of("phase")] == ["first"] and m.replies == 5


# rounds.md, 8.6: the correction phase


def test_ids_made_by_others_in_the_round_are_hidden_from_a_correction():
    m = base()
    r = Round(m, {
        B2: [act(a("post_task", task_id="task-10"))],
        M2: [act(a("bid", post_id="post-8", price=9))],
        M1: [act(BOGUS, BOGUS, cost=0.5), act(a("bid", post_id="post-10", price=5), DELIVER, cost=0.25)],
        B1: [act(BOGUS, BOGUS, a("end_contract", contract_id="contract-1")),
             act(a("accept_bid", bid_id="bid-4"), a("end_contract", contract_id="contract-1"))],
    })
    assert m.posts["post-10"].closed is None and m.bids["bid-4"].status == "open"
    # the post made this round is not on the manager's correction shadow; the end made in the first phase is
    assert m.participants[M1].results[3:] == [
        CORRECTION, "- bid: ERROR: no post with id 'post-10'. Open posts: post-7, post-8, post-9.",
        "- deliver: ERROR: B-c53 ended contract-1 on day 5: it takes no more runs, tests or deliveries"]
    assert m.participants[B1].results[3:5] == [
        "- end_contract: OK: ended contract-1 with M-14b (its deadline day 4 passed with nothing delivered). task-1 "
        "is back in your queue, due day 4 (1 day ago), and can be posted again", CORRECTION]
    # the bid another manager made this round is not on the buyer's
    assert m.participants[B1].results[5:] == [
        "- accept_bid: ERROR: no bid with id 'bid-4'. Open bids on your posts: bid-1 (M-14b at 20 on post-9), bid-2 "
        "(M-849 at 18 with threshold 5 (escrow) on post-9), bid-3 (M-14b at 15 on post-8).",
        "- end_contract: ERROR: you already ended contract-1 on day 5"]
    # the phase runs as section 6: its order, its clash; no note for a failed action, no further correction
    assert [(x["phase"], x["order"]) for x in r.of("phase")][1] == (
        "correction", [(B1, 0, "accept_bid"), (M1, 0, "bid"), (M1, 1, "deliver"), (B1, 1, "end_contract")])
    assert [(x["phase"], x["rule"], x["held"]) for x in r.of("clash")] == [("correction", "A1", True)]
    assert [len(r.backends[pid].requests) for pid in ORDER] == [2, 1, 2, 1]
    assert m.participants[M1].balance == 59.25


def test_a_manager_put_out_of_business_by_its_correction_calls_charge():
    """rounds.md, 8.4: it stays a correction-phase participant, fails its first action and is stopped; its
    memory is the correction's (turns.md, 13.2)."""
    m = base()
    m.participants[M1].balance = 0.75
    r = Round(m, {M1: [act(BOGUS, BOGUS, cost=0.5),
                       act(DELIVER, a("bid", post_id="post-7", price=9), memory="last", cost=0.5)]})
    p = m.participants[M1]
    assert p.results == [HEADING, BOGUS_MANAGER, BOGUS_MANAGER, CORRECTION, "- deliver: ERROR: " + CANNOT_ACT,
                         "(note: your remaining 1 action did not run: you are out of business)"]
    assert p.out_of_business and p.memory == Memory("last", 5, 1, 1, True)
    assert [(x["phase"], x["outcome"]) for x in r.of("action")] == [("first", "refused")] * 2 + [
        ("correction", "refused")]
    assert r.of("phase")[1]["order"] == [(M1, 0, "deliver"), (M1, 1, "bid")]  # the stopped one is in the order


# rounds.md, 7 and 9: money from others, the round's credits, the end of each turn


def test_the_rounds_credits_land_at_its_end_and_not_before_a_correction():
    """rounds.md, 7: "with 0.50 credits, paid 10 by its buyer in the same phase, its test_result fails"."""
    m = base()
    m.participants[M1].balance = 0.5
    test = a("test_result", contract_id="contract-2", result="R-b02")
    r = Round(m, {B1: [act(a("pay", to=M1, amount=10), a("approve_submission", contract_id="contract-3"))],
                  M1: [act(test), act(test, memory=None)]})
    short = "- test_result: ERROR: insufficient balance for test_result: it costs 1, you have 0.50"
    p = m.participants[M1]
    assert p.results == [HEADING, short, CORRECTION, short]  # refused on the state its turn saw: no note
    assert r.body(M1) == "\n".join([
        ONE_FAILED, "", "Results of your reply:", short, "", NEWS, "[day 5, round 1] B-c53 paid you 10 for "
        "contract-2", "[day 5, round 1] B-c53 approved R-c03 on contract-3: you received 20.00 of the price 20; "
        "contract-3 is closed."])
    assert [(x["to"], x["amount"], x["kind"], x["payer"]) for x in r.of("credit_landed")] == [
        (M1, 10.0, "transfer", B1), (M1, 20.0, "escrow_release", B1)]
    assert (p.balance, m.deferred) == (30.5, [])
    assert p.ledger == [("payments received", 10.0), ("escrow releases", 20.0)]
    types = [x["type"] for x in r.records]
    assert types.index("credit_landed") > max(i for i, t in enumerate(types) if t == "action")
    assert types.index("memory") > max(i for i, t in enumerate(types) if t == "credit_landed")


def test_each_turns_results_text_and_memory():
    """rounds.md, 9.2; turns.md, 10.3, 10.4 and 12: one round per row of cases."""
    m = base()
    m.participants[B1].memory = before = Memory("old", 4, 3, 0, False)
    r = Round(m, {
        M1: [act(BOGUS, memory="first"), act(memory="second")],  # the correction's memory
        B2: [act(BOGUS, memory="first"), act(a("pay", to=M1, amount=1), memory=None)],  # the first act's
        B1: [act(memory=None)],  # none: unchanged
        M2: [act(memory="x" * 1999 + "\n" + "y" * 5)],  # cut
    })
    people = m.participants
    assert people[M1].results == [HEADING, BOGUS_MANAGER, CORRECTION, NO_ACTIONS]
    assert people[M1].memory == Memory("second", 5, 1, 1, True)
    assert people[B2].results == [HEADING, BOGUS_BUYER, CORRECTION, "- pay: OK: paid 1 to M-14b"]
    assert people[B2].memory == Memory("first", 5, 1, 1, False)
    assert people[B1].results == [HEADING, NO_MEMORY] and people[B1].memory is before
    assert people[M2].results == [HEADING, "(note: your memory was cut to its first 2000 of 2005 characters)"]
    assert people[M2].memory == Memory("x" * 1999 + "\n", 5, 1, 1, False)
    # written once, in the turn order, after the round's credits
    assert [{k: v for k, v in x.items() if k not in ("n", "type", "day", "round")} for x in r.of("memory")] == [
        {"participant": M1, "text": "second", "length": 6, "cut": False, "from_correction": True},
        {"participant": B2, "text": "first", "length": 5, "cut": False, "from_correction": False},
        {"participant": M2, "text": "x" * 1999 + "\n", "length": 2005, "cut": True, "from_correction": False}]
    assert who(r.records[-4:]) == [("credit_landed", M1), ("memory", M1), ("memory", B2), ("memory", M2)]

    m.round = 2
    r = Round(m, {
        M1: [words(), act(a("pay", to=B1, amount=1), memory="fixed")],  # unreadable, then corrected
        B2: [act(BOGUS, memory="kept"), FAILED],  # the correction could not be sent
        B1: [act(BOGUS, memory="mine"), Reply(tool_calls=[call("compute", "{}"), call("act", '{"memory": " "}')])],
        M2: [act(), act(BOGUS)],  # no correction for it: its second reply is never asked for
    })
    assert people[M1].results == [HEADING, f"- FORMAT ERROR: {NOT_A_CALL}: none of your actions ran", CORRECTION,
                                  "- pay: OK: paid 1 to B-c53"]
    assert people[M1].memory == Memory("fixed", 5, 2, 2, True)
    assert people[B2].results == [HEADING, BOGUS_BUYER, NOT_SENT] and people[B2].memory.text == "kept"
    # a correction whose only line is a note shows that note and no "(no actions run)"
    assert people[B1].results == [HEADING, BOGUS_BUYER, CORRECTION,
                                  "(note: your compute call did not run: it was sent in the same reply as act)"]
    assert people[B1].memory == Memory("mine", 5, 2, 2, False)
    assert people[M2].results == [HEADING, NO_ACTIONS] and people[M2].memory == Memory("m", 5, 2, 2, False)
    assert len(r.backends[M2].requests) == 1


# rounds.md, 10: lost turns


def test_a_turn_lost_to_the_provider_is_restored_and_counted():
    m = base("all")
    m.messages.append(Message(4, 1, M1, B1, "hello"))
    lost, shown = m.participants[B1], ["[day 5] one", "[day 5] two"]
    lost.pending, lost.results, lost.memory = list(shown), [HEADING, NO_ACTIONS], Memory("old", 4, 3, 2, False)
    lost.turns, lost.last_view = 2, 12
    before = copy.deepcopy(lost)
    r = Round(m, {B1: [compute(cost=0.5), FAILED], M1: [act(DELIVER)], M2: [FAILED]})
    assert "Your conversation with M-14b:\n  [day 4, round 1] M-14b -> you (NEW): hello" in r.view(B1)
    # 10.1, item 1: its notifications are those its view showed, followed by any received since; its message
    # cursors, its turn counter and its last view go back; its stored results and memory stay
    assert lost.pending == shown + [DELIVERED]
    assert (lost.seen, lost.unseen, lost.turns, lost.last_view) == (0, [], 2, 12)
    assert (lost.results, lost.memory) == (before.results, before.memory)
    assert lost.balance == 59.5 and lost.ledger == [("tokens", 0.5)]  # the reply before the failure stays charged
    assert who(r.of("call", "program", "call_failed", "lost_turn")) == [
        ("call", M1), ("call", B2), ("call", B1), ("program", B1), ("call_failed", B1), ("call_failed", M2),
        ("lost_turn", B1), ("lost_turn", M2)]
    # it runs no action, gets no reply number and no correction, writes no memory
    assert who(r.of("reply", "memory")) == [("reply", M1), ("reply", B2), ("memory", M1), ("memory", B2)]
    assert m.replies == 2 and len(r.backends[B1].requests) == 2
    # item 2: counted over the episode and logged; the views built are still counted (running.md, 6.7)
    assert m.lost_turns == 2 and (m.counts["buyer_turns_lost_provider"], m.counts["buyer_turns"]) == (1, 2)
    assert [(x["participant"], x["role"], x["reason"], x["error"], x["contracts"]) for x in r.of("lost_turn")] == [
        (B1, "buyer", "failed model call", "HTTP 402 for model 'm': no credit", []),
        (M2, "manager", "failed model call", "HTTP 402 for model 'm': no credit", [])]
    # its next view shows the same notifications and results again, and the message as new again
    m.round = 2
    r = Round(m, {})
    assert "\n\n".join(["--- Notifications since your last turn ---", *shown, DELIVERED]) in r.view(B1)
    assert f"previous turn ---\n\n{HEADING}\n{NO_ACTIONS}\n\n--- Notifications" in r.view(B1)
    assert "M-14b -> you (NEW): hello" in r.view(B1)
    assert "(last updated day 4, round 3, with your last actions, before their results) ---\n\nold" in r.view(B1)
    assert (lost.turns, lost.last_view, lost.seen) == (3, 14, 1)


def test_the_lost_turns_log_lists_the_managers_contracts_due_that_day():
    """rounds.md, 10.1, item 2: no delivery or submission, the listing deadline today, in creation order."""
    m = base()
    contract(m, 4, deadline=5)  # plain, its deadline today
    contract(m, 5, "escrow", deadline=6)  # escrow: its task's due day, not its deadline
    contract(m, 6, "escrow", deadline=5, label="R-f06", delivered=5)  # something submitted
    contract(m, 11, deadline=5, manager=M2)  # another manager's
    c = contract(m, 12, "escrow", deadline=8)
    m.tasks["task-12"].due, c.ended = 5, 4  # any status
    r = Round(m, {M1: [FAILED], B1: [FAILED]})
    assert [(x["participant"], x["contracts"]) for x in r.of("lost_turn")] == [
        (M1, ["contract-4", "contract-12"]), (B1, [])]


def test_too_many_lost_turns_abort_the_episode_at_once():
    """rounds.md, 10.1, item 3: more than 5 x time.rounds_per_day, the 16th at the default."""
    m = base()
    m.lost_turns = 14
    Round(m, {B1: [FAILED]})
    assert m.lost_turns == 15 and m.participants[B1].turns == 0 and m.participants[M1].results == [HEADING, NO_ACTIONS]

    m, tracker = base(), Tracker()
    m.lost_turns = 14
    with pytest.raises(rounds.Stop) as stop:
        Round(m, {M1: [FAILED], B2: [CallFailed("the last failure")], B1: [FAILED]}, tracker=tracker)
    assert stop.value.status == "aborted_lost_turns" and str(stop.value) == stop.value.error
    assert "16" in stop.value.error and "the last failure" in stop.value.error
    # the turn order decides which lost turn triggers it; nothing after it happens
    assert who(tracker.records)[-4:] == [("call_failed", B1), ("call", M2), ("lost_turn", M1), ("lost_turn", B2)]
    assert m.lost_turns == 16
    assert [m.participants[pid].turns for pid in ORDER] == [0, 0, 1, 1]

    m = base()
    m.lost_turns = 15
    with pytest.raises(rounds.Stop):  # a correction call that fails after an unreadable act counts too
        Round(m, {B1: [words(), FAILED]})
    m = base()
    m.lost_turns = 15
    Round(m, {B1: [act(BOGUS), FAILED], M1: [words(), words()]})  # the other losses of 10.3 do not count
    assert m.lost_turns == 15


# rounds.md, 12: message_channels


def test_a_message_sent_in_a_round_is_seen_in_the_next_rounds_view_and_is_no_news():
    m = base("all")
    r = Round(m, {M1: [act(a("send_message", to=B1, text="hello"), DELIVER)],
                  B1: [act(a("end_contract", contract_id="contract-1")), act()]})
    assert m.participants[M1].results[1] == "- send_message: OK: message sent to B-c53"
    assert not any("hello" in r.view(pid) for pid in ORDER)
    assert "hello" not in r.body(B1) and r.body(B1).endswith(f"{NEWS}\n{DELIVERED}")
    m.round = 2
    r = Round(m, {})
    assert "Your conversation with M-14b:\n  [day 5, round 1] M-14b -> you (NEW): hello" in r.view(B1)
    assert "hello" not in r.view(B2)


def test_summaries_are_written_while_the_views_are_built_one_taker_at_a_time():
    """rounds.md, 3, step 2, and 12: before the round-start state is kept, not part of the waves; a cost-cap stop
    falls where it would with the takers handled one at a time."""
    def talk():
        m = base("all")
        m.messages = [Message(4, 1, M1, "everyone", f"note {i}") for i in range(11)]
        return m

    m, summarizer = talk(), Scripted("summarizer", [Reply(text="The ten notes.", cost=0.5)], [])
    r = Round(m, {}, summarizer=summarizer)
    assert who(r.records[:8]) == [
        ("round", None), ("system_prompt", M1), ("cap_check", None), ("summarizer_call", None), ("summary", None),
        ("turn", M1), ("system_prompt", B2), ("turn", B2)]
    assert len(summarizer.requests) == 1 and r.tracker.checks == 2  # the later views show the stored summary
    assert all("): The ten notes." in r.view(pid) for pid in ORDER)
    assert all(m.participants[pid].balance == 60.0 for pid in ORDER)  # never charged to a participant
    tracker = Tracker(stop_at=1)
    with pytest.raises(CostCapReached):
        Round(talk(), {}, tracker=tracker, summarizer=summarizer)
    assert [x["type"] for x in tracker.records] == ["round", "system_prompt", "cap_check"]


def test_send_message_is_an_ordinary_action_and_off_has_none():
    m = base("managers")
    r = Round(m, {M1: [act(a("send_message", to=M2, text="hi")), act()],
                  B1: [act(a("send_message", to=M1, text="hi")), act()]})
    assert m.participants[M1].results == [HEADING, "- send_message: OK: message sent to M-849"]
    assert m.participants[B1].results[1].startswith("- send_message: ERROR: ")
    assert [len(r.backends[pid].requests) for pid in (M1, B1)] == [1, 2]  # a failed one earns a correction
    m = base()
    Round(m, {M1: [act(a("send_message", to=M2, text="hi")), act()]})
    assert m.participants[M1].results[1] == (
        f"- send_message: ERROR: 'send_message' is not available in this market. Your actions: {MANAGER_LIST}.")
    assert m.messages == []


def test_the_source_files_are_pure_ascii():
    for path in (pathlib.Path(rounds.__file__), pathlib.Path(__file__)):
        assert path.read_bytes().isascii()
