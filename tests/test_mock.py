"""The offline mock model (market/mock.py) against spec/running.md, 7.

The views are built by market/view.py from markets made by hand (the seat order is not the sorted order, so the
participant list's order shows), and every action a rule gives is also run on its market by market/actions.py:
the mock's valid actions must be accepted, its invalid ones refused.
"""

import ast
import collections
import copy
import dataclasses
import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

from market import actions, config, mock, models, processes, prompts, reading, sandbox, state, turns, view
from market.state import Bid, Contract, Delivery, Dispute, Market, Memory, Post, Result, Task

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "spec" / "examples"
BUYERS, MANAGERS = ("B-d75", "B-c53"), ("M-f8c", "M-14b", "M-849")
MODES = ("off", "managers", "buyers", "within_roles", "all")
PRICED = {"provider": "mock", "price_in_per_m": 0.15, "price_out_per_m": 1.875}
COMPUTE = {"id": "call_mock_1", "type": "function",
           "function": {"name": "compute", "arguments": '{"code": "print(sum(range(10)))"}'}}
LONE = chr(0xD800)


# A market by hand


def market(mode="off", day=5, rnd=1, **overrides):
    m = Market(seed=0, cfg=config.resolve({"message_channels": mode, **overrides}))
    for pid in BUYERS:
        state.add_participant(m, pid, "buyer")
    for pid in MANAGERS:
        state.add_participant(m, pid, "manager")
    m.day, m.round = day, rnd
    return m


def task(m, n, buyer="B-d75", due=9):
    t = m.tasks[f"task-{n}"] = Task(f"task-{n}", buyer, 60, due)
    return t


def post(m, n, day=1, rnd=1, form=None, threshold=None, **kw):
    """An open post of task-n (made here unless it exists), posted in that round."""
    t = m.tasks.get(f"task-{n}") or task(m, n, **kw)
    t.state, t.post = "posted", state.numbered("post", t)
    p = m.posts[t.post] = Post(t.post, t.id, t.buyer, day, rnd, form, threshold)
    return p


def bid(m, on, manager, price=20.0, threshold=None):
    b = Bid(state.new_bid_id(m), on.id, manager, price, threshold)
    m.bids[b.id] = b
    return b


def contract(m, n, manager="M-f8c", form="plain", price=20.0, signed=1, **kw):
    """A contract on task-n, signed on its post of day 1."""
    p = post(m, n, **kw)
    t = m.tasks[p.task]
    p.closed, p.closed_day, p.closed_round = "accepted", signed, 3
    t.state, t.contract = "contracted", state.numbered("contract", t)
    c = m.contracts[t.contract] = Contract(
        t.contract, t.id, p.id, t.buyer, manager, form, price, 7.0 if form == "escrow" else None, signed,
        config.deadline(m.cfg, t.due, signed))
    return c


def end(m, c, day):
    t = m.tasks[c.task]
    c.ended, t.state, t.contract, t.post = day, "queued", None, None
    t.ended.append(c.id)


def result(c, label, arrival=2, mine=(), theirs=()):
    r = Result(label, "employee", "fresh", None, 1, arrival, 7.0, list(mine), list(theirs))
    c.results.append(r)
    return r


def give(c, label, day, theirs=(), **kw):
    """A delivery (plain) or a submission (escrow, under review unless told) of a result, run here unless it
    exists."""
    if state.result(c, label) is None:
        result(c, label, theirs=theirs)
    kw.setdefault("outcome", "review" if c.form == "escrow" else None)
    d = Delivery(label, day, 1, **kw)
    c.deliveries.append(d)
    return d


# What the mock reads and says


def system(m, pid):
    p = m.participants[pid]
    return prompts.system_prompt(m.cfg, p.role, pid, p.share, p.seat)


def look(m, pid):
    return view.build(copy.deepcopy(m), pid)[0]


def digest(text):
    """7.6, written out: the SHA-256 of the UTF-8 bytes, a lone surrogate as the three bytes of its code point."""
    try:
        return hashlib.sha256(text.encode("utf-8")).digest()
    except UnicodeEncodeError:
        pass
    out = bytearray()
    for n in map(ord, text):
        lone = 0xD800 <= n <= 0xDFFF
        out += bytes((0xE0 | n >> 12, 0x80 | n >> 6 & 0x3F, 0x80 | n & 0x3F)) if lone else chr(n).encode("utf-8")
    return hashlib.sha256(bytes(out)).digest()


def plan(m, pid):
    """The mock's actions on its view of the market, without the invalid action its digest adds (7.6)."""
    text = look(m, pid)
    out = mock.actions(system(m, pid), text)
    h = digest(text)
    if h[0] % 8 == 0 and out and out[-1] == mock.INVALID[h[1] % 5]:
        del out[-1]
    assert not any(action in mock.INVALID for action in out)
    return out


def run(m, pid, action):
    """What the market answers the action, sent as the mock sends it, on a copy."""
    (name, args), = reading.read_items([action])
    return actions.run(copy.deepcopy(m), pid, name, args)


def accepted(m, pid, acts):
    return all(run(m, pid, action)[0] for action in acts)


def request(m, pid, *more):
    return [{"role": "system", "content": system(m, pid)}, {"role": "user", "content": look(m, pid)}, *more]


def act_of(reply):
    """The one act call of a reply: its id and its decoded arguments."""
    (call,) = reply.tool_calls
    assert call["type"] == "function" and call["function"]["name"] == "act" and reply.text == ""
    return call["id"], json.loads(call["function"]["arguments"])


def with_memory(m, pid, wanted, text="note {}"):
    """The market with a memory of the participant under which its view's digest is as wanted (7.6)."""
    for i in range(5000):
        m.participants[pid].memory = Memory(text.format(i), 1, 1, 0, True)
        if wanted(digest(look(m, pid))):
            return m
    raise AssertionError("no such view")


def approve(cid):
    return {"action": "approve_submission", "contract_id": cid}


def read(cid):
    return {"action": "test_delivery", "contract_id": cid}


def pay(to, amount, cid):
    return {"action": "pay", "to": to, "amount": amount, "contract_id": cid}


def start(cid):
    """7.4 rule 2: a fresh run, by the employee on an even contract number, by the manager on an odd one."""
    return {"action": "run_myself" if int(cid.split("-")[1]) % 2 else "delegate_run", "contract_id": cid}


def message(pid, to, day, rnd):
    return {"action": "send_message", "to": to,
            "text": f"Mock message from {pid} on day {day}, round {rnd}: price 20, threshold 7?"}


# 7.2 Its replies in a turn


def test_the_first_call_runs_the_program():
    m = market()
    task(m, 1)
    reply = mock.backend(PRICED).call(request(m, "B-d75"), turns.functions(m.cfg), "required")
    assert reply.text == "" and reply.tool_calls == [COMPUTE]


def test_the_program_prints_45():
    if sandbox.host_check() is not None:
        pytest.skip("programs cannot be run isolated on this host")
    status, exit_code, _, output = sandbox.run_program(mock.CODE, config.resolve({})["compute"])
    assert (status, exit_code, output) == ("ok", 0, "45")


@pytest.mark.parametrize("pid", ["B-d75", "M-f8c"])
def test_a_whole_turn_and_its_correction(pid):
    m = market("all")
    task(m, 1)
    post(m, 2)
    backend, programs = mock.backend(PRICED), []

    def run_program(code, compute):
        programs.append(code)
        return "ok", 0, 0.0, "45"

    text = look(m, pid)
    chain = turns.decide(system(m, pid), text, backend, m.cfg, 60.0, pid[0] == "M", run_program)
    first, second = (step["reply"] for step in chain.steps)
    assert programs == [mock.CODE] and first.tool_calls == [COMPUTE]
    assert second is chain.reply and second.finish_reason == "stop"
    read = reading.read_reply(second.tool_calls, False, m.cfg["turns"]["max_actions"])
    wanted = mock.actions(system(m, pid), text)
    assert wanted and read["error"] is None and read["notes"] == [] and read["memory"] == "mock memory"
    assert read["actions"] == reading.read_items(wanted)
    assert act_of(second) == ("call_mock_2", {"actions": wanted, "memory": "mock memory"})
    messages = turns.correction_messages(chain, read, "1 of your actions failed")
    assert [x.get("tool_call_id") for x in messages[3:]] == ["call_mock_1", None, "call_mock_2"]  # the tool path
    third = turns.correct(messages, backend, m.cfg).reply
    assert act_of(third) == ("call_mock_3", {"actions": [], "memory": "mock memory"})
    assert third.input_tokens > second.input_tokens > first.input_tokens


def test_a_later_call_that_offers_act_is_answered_with_act_also_after_a_failed_program():
    m = market()
    task(m, 1)
    programs = []

    def run_program(code, compute):
        programs.append(code)
        return "timeout", -9, 10.0, "(stopped)"

    chain = turns.decide(system(m, "B-d75"), look(m, "B-d75"), mock.backend(PRICED), m.cfg, 60.0, False, run_program)
    assert len(programs) == 1 and len(chain.steps) == 2
    assert chain.steps[1]["request"]["tool_choice"] == "required"  # the failed program left compute on offer
    assert act_of(chain.reply) == ("call_mock_2", {"actions": plan(m, "B-d75") + extra(m, "B-d75"),
                                                   "memory": "mock memory"})


def extra(m, pid):
    """The invalid action the view's digest adds, as a list (7.6)."""
    h = digest(look(m, pid))
    return [mock.INVALID[h[1] % 5]] if h[0] % 8 == 0 else []


def test_the_shape_of_the_request_decides_the_reply():
    m = market()
    task(m, 1)
    both = turns.functions(m.cfg)
    backend = mock.backend(PRICED)
    tool = {"role": "tool", "tool_call_id": "call_mock_1", "content": "45"}
    compute = {"role": "assistant", "content": "", "tool_calls": [COMPUTE]}
    act = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_mock_2", "type": "function", "function": {"name": "act", "arguments": "{}"}}]}
    wanted = {"actions": plan(m, "B-d75") + extra(m, "B-d75"), "memory": "mock memory"}
    assert wanted["actions"][0]["action"] == "post_task"
    assert backend.call(request(m, "B-d75"), both, "required").tool_calls == [COMPUTE]
    assert act_of(backend.call(request(m, "B-d75", compute, tool), both, turns.ACT_FORCED)) == ("call_mock_2", wanted)
    assert act_of(backend.call(request(m, "B-d75", compute, tool), both, "required")) == ("call_mock_2", wanted)
    assert act_of(backend.call(request(m, "B-d75", compute, tool, compute, tool), both, "required")) == (
        "call_mock_3", wanted)
    empty = {"actions": [], "memory": "mock memory"}
    assert act_of(backend.call(request(m, "B-d75", compute, tool, act, tool), both[1:], turns.ACT_FORCED)) == (
        "call_mock_3", empty)
    assert act_of(backend.call(request(m, "B-d75", act, tool), both[1:], turns.ACT_FORCED)) == ("call_mock_2", empty)
    assert act_of(backend.call(request(m, "B-d75", compute, tool, act, tool), both, "required")) == (
        "call_mock_3", empty)
    # no act call of the turn in the request: not a correction, whatever is offered
    assert act_of(backend.call(request(m, "B-d75", compute, tool), both[1:], turns.ACT_FORCED)) == (
        "call_mock_2", wanted)
    assert act_of(backend.call(request(m, "B-d75"), both[1:], turns.ACT_FORCED)) == ("call_mock_1", wanted)


# 7.1 Requirements: tokens and cost


def test_tokens_and_cost_by_the_mocks_formula():
    reply = mock.backend(PRICED).call(
        [{"role": "system", "content": "s" * 400}, {"role": "user", "content": "v" * 3}], turns.functions(
            config.resolve({})), "required")
    assert (reply.input_tokens, reply.output_tokens) == (100, 1)
    assert reply.cost == (100 * 0.15 + 1 * 1.875) / 10**6 == 1.6875e-05
    assert dataclasses.asdict(reply) == {
        "text": "", "tool_calls": [COMPUTE], "reasoning": "", "input_tokens": 100, "output_tokens": 1,
        "reasoning_tokens": 0, "cost": 1.6875e-05, "cost_known": True, "input_estimated": True,
        "output_estimated": True, "cost_source": "computed", "failed_cost": 0.0, "finish_reason": "stop",
        "served_by": None, "latency": None}


def test_the_cost_is_not_rule_2_of_models_4_9():
    reply = mock.backend(PRICED).call([{"role": "system", "content": "s" * 20}, {"role": "user", "content": ""}],
                                      None, None)
    assert (reply.input_tokens, reply.output_tokens) == (5, 1)
    assert reply.cost == 2.625e-06 != 5 * 0.15 / 10**6 + 1 * 1.875 / 10**6 == 2.6250000000000003e-06


def test_the_input_counts_contents_and_function_calls_and_nothing_else():
    calls = [{"id": "call_mock_1", "type": "function", "function": {"name": "compute", "arguments": chr(0xE9)}}]
    json_text = ('[{"id": "call_mock_1", "type": "function", "function": {"name": "compute", "arguments": "'
                 + chr(92) + 'u00e9"}}]')
    assert len(json_text) == 99
    messages = [{"role": "system", "content": "a" * 10}, {"role": "user", "content": "b" * 11},
                {"role": "assistant", "content": "", "tool_calls": calls, "reasoning": "r" * 400},
                {"role": "tool", "tool_call_id": "call_mock_1" * 40, "content": "c" * 9},
                {"role": "assistant", "content": None, "tool_calls": []}, {"role": "user"}]
    both = turns.functions(config.resolve({}))
    backend = mock.backend(PRICED)
    assert backend.call(messages, both, "required").input_tokens == (10 + 11 + 99 + 9) // 4 == 32
    assert backend.call(messages, both[1:], turns.ACT_FORCED).input_tokens == 32  # the definitions are not counted
    assert models.estimate(models.request_text({"messages": messages, "tools": both})) > 32  # as 4.8 would
    assert backend.call([{"role": "system", "content": ""}, {"role": "user", "content": "abc"}], both,
                        "required").input_tokens == 1


@pytest.mark.parametrize("spec, cost", [
    ({"provider": "mock"}, 0.0),
    ({"provider": "mock", "price_in_per_m": 2}, 200 / 10**6),
    ({"provider": "mock", "price_out_per_m": 3, "price_in_per_m": None}, 3 / 10**6),
    ({"provider": "mock", "price_in_per_m": 2, "price_out_per_m": 3}, 203 / 10**6),
])
def test_a_missing_price_counts_as_0(spec, cost):
    reply = mock.backend(spec).call([{"role": "system", "content": "s" * 400}, {"role": "user", "content": ""}],
                                    None, None)
    assert reply.cost == cost and type(reply.cost) is float
    assert reply.cost_known and reply.cost_source == "computed"


def test_mock_calls_add_nothing_to_the_cost_tracker_and_are_charged_in_credits():
    m = market()
    tracker = models.CostTracker(1e-9)
    backend = models.backend(PRICED, m.cfg["llm"], tracker)
    chain = turns.decide(system(m, "M-14b"), look(m, "M-14b"), backend, m.cfg, 60.0, True,
                         lambda code, compute: ("ok", 0, 0.0, "45"))
    assert len(chain.steps) == 2 and all(step["reply"].cost > 0 and step["charge"] > 0 for step in chain.steps)
    assert (tracker.total, tracker.unknown, tracker.reached()) == (0.0, 0, False)


def test_setting_up_follows_models_3():
    with pytest.raises(models.SetupError, match="MOCK_KEY"):
        models.backend({**PRICED, "api_key_env": "MOCK_KEY"}, {}, models.CostTracker(None), env={})
    keyed = models.backend({**PRICED, "api_key_env": "MOCK_KEY"}, {}, models.CostTracker(None), env={"MOCK_KEY": "k"})
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "v"}]
    assert keyed.call(messages, None, None) == mock.backend(PRICED).call(messages, None, None)


# 7.1 Requirements: deterministic


def transcript():
    """The replies to a first call, a later call and a correction on each example view, as plain data."""
    cfg = config.resolve({"message_channels": "all"})
    both = turns.functions(cfg)
    backend = mock.backend(PRICED)
    out = []
    for path in sorted(EXAMPLES.glob("view_*.txt")):
        pid = path.stem[path.stem.index("-") - 1:][:5]
        role = "buyer" if pid[0] == "B" else "manager"
        messages = [{"role": "system", "content": prompts.system_prompt(cfg, role, pid, 1.0, 0)},
                    {"role": "user", "content": path.read_text(encoding="utf-8") + LONE}]
        for offered, choice in ((both, "required"), (both, turns.ACT_FORCED), (both[1:], turns.ACT_FORCED)):
            reply = backend.call(messages, offered, choice)
            out.append(dataclasses.asdict(reply))
            messages += [turns.assistant(reply), {"role": "tool", "tool_call_id": reply.tool_calls[0]["id"],
                                                  "content": "45"}]
    return out


CHILD = """
import json, os, random, sys, time
sys.path[:0] = [".", "tests"]
from test_mock import transcript
def used(*a, **k):
    raise AssertionError("the mock used a clock or randomness")
for module, names in ((time, ("time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "sleep")),
                      (random, ("random", "randrange", "randint", "getrandbits", "choice")), (os, ("urandom",))):
    for name in names:
        setattr(module, name, used)
print(json.dumps(transcript()))
"""


def test_the_same_request_gives_the_same_reply_in_two_processes():
    outputs = []
    for hash_seed in ("1", "2"):
        done = subprocess.run([sys.executable, "-c", CHILD], cwd=ROOT, capture_output=True, text=True, check=False,
                              env={**os.environ, "PYTHONHASHSEED": hash_seed})
        assert done.returncode == 0, done.stderr
        outputs.append(json.loads(done.stdout))
    here = json.loads(json.dumps(transcript()))
    assert outputs[0] == outputs[1] == here == transcript()
    assert len(here) == 12 and [r["tool_calls"][0]["function"]["name"] for r in here[:3]] == ["compute", "act", "act"]


def test_a_call_changes_nothing_of_its_request():
    m = market("all")
    post(m, 1)
    messages = request(m, "M-14b", {"role": "assistant", "content": "", "tool_calls": [COMPUTE]},
                       {"role": "tool", "tool_call_id": "call_mock_1", "content": "45"})
    both = turns.functions(m.cfg)
    before = copy.deepcopy((messages, both))
    backend = mock.backend(PRICED)
    assert backend.call(messages, both, "required") == backend.call(messages, both, "required")
    assert (messages, both) == before


def test_the_module_reads_no_clock_no_randomness_and_no_environment():
    tree = ast.parse((ROOT / "market" / "mock.py").read_text(encoding="ascii"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imported == {"hashlib", "json", "re"}
    assert [(node.module, [alias.name for alias in node.names]) for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)] == [
        (None, ["messaging", "models", "view_contracts"]), ("view", ["MEMORY"]), ("view", ["NOTIFICATIONS"])]


# 7.3 A mock buyer's actions


def test_approve_the_first_escrow_with_a_submission_under_review():
    m = market()
    contract(m, 1, form="escrow")
    give(contract(m, 2, form="escrow"), "R-0a2", 4, outcome="rejected", decided=4)
    give(contract(m, 3, form="escrow"), "R-0a3", 4)
    give(contract(m, 4, form="escrow"), "R-0a4", 4)
    give(contract(m, 5, form="escrow", buyer="B-c53"), "R-0a5", 4)
    assert plan(m, "B-d75") == [approve("contract-3")] and accepted(m, "B-d75", [approve("contract-3")])
    assert plan(m, "B-c53") == [approve("contract-5")]
    assert plan(market(), "B-d75") == []


def test_test_the_first_active_plain_contract_whose_delivery_in_use_is_untested():
    m = market()
    for n in (1, 2, 3, 4):
        contract(m, n).paid = 20.0
    one, two, three, four = m.contracts.values()
    give(one, "R-0a1", 4, theirs=[6.0], message="R-0a1 delivered day 4 (in use) | your readings: none")
    give(two, "R-0a2", 3, theirs=[6.0])
    give(two, "R-0b2", 4)
    two.kept = "R-0a2"
    give(three, "R-0a3", 3, theirs=[6.0])
    give(three, "R-0b3", 4)
    give(four, "R-0a4", 4)
    give(contract(m, 5, form="escrow"), "R-0a5", 4)
    text = look(m, "B-d75")
    assert "    R-0b2 delivered day 4 | your readings: none\n" in text
    assert "    R-0b3 delivered day 4 (in use) | your readings: none\n" in text
    assert plan(m, "B-d75") == [approve("contract-5"), read("contract-3")]
    assert accepted(m, "B-d75", [read("contract-3")])
    three.kept = "R-0a3"
    assert plan(m, "B-d75") == [approve("contract-5"), read("contract-4")]


def test_a_finished_contract_is_not_tested():
    m = market(day=9)
    give(contract(m, 1, due=3), "R-0a1", 4)
    text = look(m, "B-d75")
    assert "--- Your finished contracts (latest 1 of 1) ---\n\ncontract-1 with M-f8c" in text
    assert "    R-0a1 delivered day 4 (in use) | your readings: none\n" in text
    assert plan(m, "B-d75") == [pay("M-f8c", 20.0, "contract-1")]


def test_pay_what_is_left_on_the_first_plain_contract_delivered_and_not_paid_in_full():
    m = market(day=9)
    old = contract(m, 1, due=3, price=11.0, manager="M-849")
    give(old, "R-0a1", 4, theirs=[5.0])
    contract(m, 2, due=12)
    paid = contract(m, 3, due=12)
    give(paid, "R-0a3", 8, theirs=[5.0])
    paid.paid = 20.0
    part = contract(m, 4, due=12, price=25.5)
    give(part, "R-0a4", 8, theirs=[5.0])
    part.paid = 10.0
    escrow = contract(m, 5, due=12, form="escrow")
    escrow.release_day, escrow.release_how, escrow.paid, escrow.received = 8, "approved", 10.0, 8
    give(escrow, "R-0a5", 8, theirs=[5.0], outcome="approved", decided=8)
    ended = contract(m, 6, due=12)
    ended.paid = 3.0
    end(m, ended, 8)
    post(m, 6)
    text = look(m, "B-d75")
    assert " | price 11 (plain) | paid 0.00 of 11.00 | deadline day 5 | in use: R-0a1 (" in text
    assert " | price 20 (plain) | paid 0.00 of 20.00 | signed day 1 | deadline day 12 (3 days left) | NOT DELIV" in text
    assert " | price 25.5 (plain) | paid 10.00 of 25.50 | signed day 1 | " in text and " | paid 3.00 | " in text
    assert text.index("contract-4 with") < text.index("contract-1 with")
    assert plan(m, "B-d75") == [pay("M-f8c", 15.5, "contract-4")]
    assert accepted(m, "B-d75", [pay("M-f8c", 15.5, "contract-4")])
    part.paid = 25.5
    assert plan(m, "B-d75") == [pay("M-849", 11.0, "contract-1")]
    assert accepted(m, "B-d75", [pay("M-849", 11.0, "contract-1")])
    old.paid = 14.1
    assert plan(m, "B-d75") == []


@pytest.mark.parametrize("price, paid, line, sent", [
    (14.1, 0.3, "paid 0.30 of 14.10", "13.799999999999999"),
    (0.3, 0.1, "paid 0.10 of 0.30", "0.19999999999999998"),
    (25.5, 10.0, "paid 10.00 of 25.50", "15.5"),
])
def test_the_amount_paid_is_the_difference_of_the_two_numbers_shown_with_no_rounding(price, paid, line, sent):
    """7.3 rule 3: "the amount X - P (P and X as that line shows them)"; no rounding is stated (README, "Number
    formats and rounding": "the spec states each other rounding where it applies"), and pay rounds to the cent."""
    m = market()
    c = contract(m, 1, price=price)
    give(c, "R-0a1", 4, theirs=[5.0])
    c.paid = paid
    assert f" (plain) | {line} | " in look(m, "B-d75")
    (action,) = plan(m, "B-d75")
    assert action == pay("M-f8c", float(line.split()[3]) - float(line.split()[1]), "contract-1")
    assert json.dumps(action["amount"]) == sent
    (name, args), = reading.read_items([action])
    assert actions.run(m, "B-d75", name, args)[0]
    assert c.paid == pytest.approx(price, abs=1e-9) and plan(m, "B-d75") == []


def test_accept_the_first_bid_the_view_lists():
    m = market(day=2)
    sealed = post(m, 1, day=2, rnd=1)
    bid(m, sealed, "M-f8c")
    post(m, 2)
    opened = post(m, 3)
    bid(m, opened, "M-f8c", 22.0)
    bid(m, opened, "M-14b", 20.0, 7.0)
    bid(m, post(m, 4), "M-849", 5.0)
    text = look(m, "B-d75")
    assert " | POSTED as post-2, no preferred form (edit_post changes its stated form and threshold) | bids: no" in text
    assert " | bids: bid-3 by M-14b at 20 with threshold 7 (escrow); bid-2 by M-f8c at 22\n" in text
    assert plan(m, "B-d75") == [{"action": "accept_bid", "bid_id": "bid-3"}]
    assert accepted(m, "B-d75", plan(m, "B-d75"))
    m.participants["M-14b"].out_of_business = True
    assert plan(m, "B-d75") == [{"action": "accept_bid", "bid_id": "bid-2"}]
    m.bids["bid-2"].status = "withdrawn"
    assert plan(m, "B-d75") == [{"action": "accept_bid", "bid_id": "bid-4"}]
    assert plan(m, "B-c53") == []


def test_post_the_first_queued_task_escrow_when_its_number_is_odd_plain_when_even():
    m = market()
    task(m, 1)
    task(m, 2)
    odd = [{"action": "post_task", "task_id": "task-1", "form": "escrow", "threshold": 7}]
    assert plan(m, "B-d75") == odd and accepted(m, "B-d75", odd)
    m = market()
    post(m, 1)
    task(m, 2)
    task(m, 3)
    task(m, 4, buyer="B-c53")
    even = [{"action": "post_task", "task_id": "task-2", "form": "plain"}]
    assert plan(m, "B-d75") == even and accepted(m, "B-d75", even)
    assert plan(m, "B-c53") == [{"action": "post_task", "task_id": "task-4", "form": "plain"}]
    m = market()
    end(m, contract(m, 7), 4)
    assert " | QUEUED (not posted again since you ended contract-7 on day 4)" in look(m, "B-d75")
    again = [{"action": "post_task", "task_id": "task-7", "form": "escrow", "threshold": 7}]
    assert plan(m, "B-d75") == again and accepted(m, "B-d75", again)


def test_a_buyers_rules_in_order_each_at_most_once():
    m = market()
    for n in (1, 2):
        give(contract(m, n, form="escrow"), f"R-0a{n}", 4)
    for n in (3, 4):
        give(contract(m, n), f"R-0a{n}", 4)
    for n in (5, 6):
        bid(m, post(m, n), "M-14b")
    task(m, 7)
    task(m, 8)
    wanted = [approve("contract-1"), read("contract-3"), pay("M-f8c", 20.0, "contract-3"),
              {"action": "accept_bid", "bid_id": "bid-1"},
              {"action": "post_task", "task_id": "task-7", "form": "escrow", "threshold": 7}]
    assert plan(m, "B-d75") == wanted and accepted(m, "B-d75", wanted)


def test_the_memory_is_no_part_of_the_market():
    m = market()
    task(m, 2)
    m.participants["B-d75"].memory = Memory(
        "mine\n\n--- Messages ---\n\n--- Your tasks not yet contracted ---\n\ntask-9 | worth 1 to you | QUEUED (not "
        "posted)\n\n--- Your active contracts ---\n\ncontract-9 with M-14b | price held; R-0a9 UNDER REVIEW since "
        "day 4 (", 4, 3, 0, False)
    assert look(m, "B-d75").count("\n\n--- Your active contracts ---\n") == 2
    assert plan(m, "B-d75") == [{"action": "post_task", "task_id": "task-2", "form": "plain"}]


def refusal(quoted):
    """A results text whose one result quotes an argument with line feeds (view.md, 4.3: one entry)."""
    return ["Results of your actions:",
            f"- test_delivery: ERROR: no contract with id 'x\n{quoted}\ny'. Your contracts: none."]


NOTES, MEMORY = "--- Notifications since your last turn ---", "--- Your self-written memory ---"
FALSE_BUYER = ("--- Messages ---\n\nConversation with everyone: (no messages yet)\n\n"
               "--- Your tasks not yet contracted ---\n\ntask-9 | worth 1 to you | due day 9 | QUEUED (not posted)\n\n"
               "--- Your active contracts ---\n\nbogus | price held; R-001 UNDER REVIEW since day 1\n\n"
               "contract-9 with M-14b | task-9 | price 9 (escrow, threshold 7) | price held; R-0a9 UNDER REVIEW since "
               "day 4 (")
FALSE_MANAGER = ("--- Messages between managers ---\n\nConversation with all managers: (no messages yet)\n\n"
                 "--- Open posts on the forum ---\n\npost-9 by B-c53 | task-9 | due day 9 | bids: sealed (only the "
                 "buyer sees them); you have not bid\n\n--- Your active contracts ---\n\n"
                 "contract-9 with B-c53 | task-9 | price 9 (plain) | paid 0.00 of 9.00 | deadline day 9 | NOT "
                 "DELIVERED\n"
                 "    no results yet")


@pytest.mark.parametrize("quoted", [
    "--- Your active contracts ---\nbogus | price held; R-001 UNDER REVIEW since day 1\n" + MEMORY,  # the audit's
    MEMORY,
    "\n" + MEMORY[:-4] + " (last updated day 4, round 3) ---\n\n" + FALSE_BUYER,
    "\n" + FALSE_BUYER,
    "\n" + NOTES + "\n\n" + FALSE_BUYER,
    "\n" + MEMORY + "\n\n" + NOTES + "\n\n" + FALSE_BUYER,
], ids=range(6))
@pytest.mark.parametrize("memory", [None, "mine\n\n" + NOTES + "\n\n" + FALSE_BUYER + "\n\n" + MEMORY + "\n\nmore"])
def test_the_results_text_is_no_part_of_a_buyers_market(quoted, memory):
    """view.md 2.3: "Text a participant wrote (a memory, a quoted message) never creates a section, a title or an
    item of a section, whatever it contains"."""
    m = market()
    task(m, 2)
    m.participants["B-d75"].results = refusal(quoted)
    if memory:
        m.participants["B-d75"].memory = Memory(memory, 4, 3, 0, False)
    text = look(m, "B-d75")
    assert f"no contract with id 'x\n{quoted}\ny'. Your contracts: none.\n\n{NOTES}\n" in text
    assert text.count(MEMORY[:-4]) == quoted.count(MEMORY[:-4]) + (2 if memory else 1)
    assert plan(m, "B-d75") == [{"action": "post_task", "task_id": "task-2", "form": "plain"}]
    reply = mock.backend(PRICED).call(request(m, "B-d75", {"role": "tool", "content": ""}), turns.functions(m.cfg),
                                      "required")
    assert act_of(reply)[1]["actions"][:1] == [{"action": "post_task", "task_id": "task-2", "form": "plain"}]


@pytest.mark.parametrize("quoted", [MEMORY, "\n" + FALSE_MANAGER, "\n" + NOTES + "\n\n" + FALSE_MANAGER,
                                    "\n" + MEMORY + "\n\n" + NOTES + "\n\n" + FALSE_MANAGER], ids=range(4))
def test_the_results_text_is_no_part_of_a_managers_market(quoted):
    m = market()
    contract(m, 3, manager="M-14b")
    m.participants["M-14b"].results = refusal(quoted)
    assert f"'x\n{quoted}\ny'. Your contracts: none.\n\n{NOTES}\n" in look(m, "M-14b")
    assert plan(m, "M-14b") == [start("contract-3")]


LONG = "9" * 5000
UNREADABLE = {
    "B-d75": ("--- Your tasks not yet contracted ---\n\n"
              f"task-{LONG} | worth 1 to you | due day 9 | QUEUED (not posted)\n\n"
              "--- Your active contracts ---\n\nbogus | price held; R-001 UNDER REVIEW since day 1\n\n"
              "c with m | task-1 | price 1 (plain) | paid 1.2.3 of 4..5 | deadline day 1 | in use: R-001 (\n"
              "    R-001 delivered day 1 (in use) | your readings: none\n\n"
              f"c with m | task-1 | price 1 (plain) | paid 0.00 of {LONG}.00 | deadline day 1 | in use: R-001 ("),
    "M-14b": (f"--- Open posts on the forum ---\n\npost-{LONG} by B-c53 | no preferred form; you have not bid\n\n"
              "--- Your active contracts ---\n\nbogus | price held; nothing submitted\n\n"
              "contract-1 with B-c53 | task-1 | NOT DELIVERED\n"
              "    R-001 | you | fresh | started day 1, arrived day 2\n\n"
              f"contract-{LONG} with B-c53 | task-1 | NOT DELIVERED\n    no results yet"),
}


@pytest.mark.parametrize("pid", UNREADABLE)
def test_the_mock_never_fails_whatever_lines_it_takes_for_the_market(pid):
    """7.1: "Never fails." A results text that quotes a Notifications title and then a memory title cannot be told
    from a memory that quotes them; the mock takes what lies between them for the market, and still replies."""
    m = market()
    m.participants[pid].results = refusal("\n" + NOTES + "\n\n" + UNREADABLE[pid] + "\n\n" + MEMORY)
    reply = mock.backend(PRICED).call(request(m, pid, {"role": "tool", "content": ""}), turns.functions(m.cfg),
                                      "required")
    acts = act_of(reply)[1]["actions"]
    assert acts == mock.actions(system(m, pid), look(m, pid))
    assert [a["action"] for a in acts if a not in mock.INVALID] == {
        "B-d75": ["test_delivery", "pay", "post_task"], "M-14b": ["bid", "deliver", "run_myself"]}[pid]


def test_a_contract_with_a_line_the_mock_cannot_read_gets_no_work():
    """7.4, rule 2, reads the result lines of view-contracts.md, 7.3: a line under a contract that is none of them
    (nor the escrow days line, nor `no results yet`) is not taken for "no run in progress"."""
    lines = ("R-001 | you | fresh | started day 1", "R-001 | you | fresh | arrived day 2 | your readings: none",
             "R-001 | you | fresh | started day 1, arrived day 2 | your readings: none | kept", "no results",
             "no results yet")
    quoted = "--- Your active contracts ---" + "".join(
        f"\n\ncontract-{n} with B-c53 | task-{n} | NOT DELIVERED\n    {line}" for n, line in enumerate(lines, 1))
    m = market()
    m.participants["M-14b"].results = refusal("\n" + NOTES + "\n\n" + quoted + "\n\n" + MEMORY)
    assert plan(m, "M-14b") == [start("contract-5")]
    quoted = quoted.replace("started day 1\n", "started day 1, arrives day 9 (tomorrow)\n").replace(" | kept", "")
    m.participants["M-14b"].results = refusal("\n" + NOTES + "\n\n" + quoted + "\n\n" + MEMORY)
    assert plan(m, "M-14b") == [{"action": "test_result", "contract_id": "contract-3", "result": "R-001"},
                                start("contract-5")]


# 7.4 A mock manager's actions


@pytest.mark.parametrize("n, form, threshold, again, wanted", [
    (2, "escrow", 5.0, False, {"threshold": 7}),
    (2, "escrow", None, False, {"threshold": 7}),
    (1, "plain", None, False, {}),
    (3, None, None, False, {"threshold": 7}),
    (4, None, None, False, {}),
    (13, None, None, True, {"threshold": 7}),
    (14, None, None, True, {}),
    (14, "escrow", 7.0, True, {"threshold": 7}),
    (13, "plain", None, True, {}),
])
def test_bid_20_with_threshold_7_on_an_escrow_post_or_an_odd_post_with_no_form(n, form, threshold, again, wanted):
    m = market()
    if again:
        end(m, contract(m, n), 4)
    post(m, n, day=4, form=form, threshold=threshold)
    post_id = f"post-{n}-2" if again else f"post-{n}"
    assert f"\n{post_id} by B-d75 | task-{n} | " in look(m, "M-14b")
    acts = [{"action": "bid", "post_id": post_id, "price": 20, **wanted}]
    assert plan(m, "M-14b") == acts and accepted(m, "M-14b", acts)


def test_bid_on_the_first_open_post_not_bid_on():
    m = market()
    first, second = post(m, 1, form="plain"), post(m, 2, form="escrow", threshold=7.0, buyer="B-c53")
    bid(m, first, "M-f8c")
    mine = bid(m, second, "M-14b", 20.0, 7.0)
    assert plan(m, "M-f8c")[0] == {"action": "bid", "post_id": "post-2", "price": 20, "threshold": 7}
    assert plan(m, "M-14b")[0] == {"action": "bid", "post_id": "post-1", "price": 20}
    assert plan(m, "M-849")[0] == {"action": "bid", "post_id": "post-1", "price": 20}
    bid(m, first, "M-14b")
    assert plan(m, "M-14b") == []
    mine.status = "withdrawn"
    assert plan(m, "M-14b") == [{"action": "bid", "post_id": "post-2", "price": 20, "threshold": 7}]


def test_a_post_the_view_only_names_is_not_bid_on():
    m = market(view={"open_shown": 1})
    post(m, 1)
    post(m, 2)
    assert "(1 more open post not listed: post-1)" in look(m, "M-14b")
    assert plan(m, "M-14b") == [{"action": "bid", "post_id": "post-2", "price": 20}]
    bid(m, m.posts["post-2"], "M-14b")
    assert "(1 more open post not listed: post-2)" in look(m, "M-14b")
    assert plan(m, "M-14b") == [{"action": "bid", "post_id": "post-1", "price": 20, "threshold": 7}]


WORK = [
    ("no result", lambda c: None, "run"),
    ("a run in progress", lambda c: result(c, "R-0a1", arrival=6), None),
    ("arrived, not tested", lambda c: (result(c, "R-0a1", arrival=6), result(c, "R-0b1"), result(c, "R-0c1")),
     {"action": "test_result", "result": "R-0b1"}),
    ("tested, not delivered", lambda c: (result(c, "R-0a1", arrival=6), result(c, "R-0b1", mine=[7.0]),
                                         result(c, "R-0c1", mine=[7.0, 8.0])),
     {"action": "deliver", "result": "R-0b1"}),
    ("a test comes before a delivery", lambda c: (result(c, "R-0a1", mine=[7.0]), result(c, "R-0b1")),
     {"action": "test_result", "result": "R-0b1"}),
    ("arrived today", lambda c: result(c, "R-0a1", arrival=5), {"action": "test_result", "result": "R-0a1"}),
]


@pytest.mark.parametrize("form", ["plain", "escrow"])
@pytest.mark.parametrize("n", [1, 2])
@pytest.mark.parametrize("name, results, wanted", WORK, ids=[w[0] for w in WORK])
def test_work_on_a_contract_that_can_take_work(form, n, name, results, wanted):
    m = market()
    c = contract(m, n, manager="M-14b", form=form)
    results(c)
    acts = [] if wanted is None else [start(c.id) if wanted == "run" else {**wanted, "contract_id": c.id}]
    assert plan(m, "M-14b") == acts and accepted(m, "M-14b", acts)
    assert plan(m, "M-f8c") == []


def test_a_fresh_run_by_the_number_of_the_contract_also_on_a_repost():
    m = market()
    end(m, contract(m, 3, manager="M-14b"), 4)
    end(m, contract(m, 4, manager="M-14b"), 4)
    contract(m, 3, manager="M-14b", signed=4)
    contract(m, 4, manager="M-14b", signed=4, form="escrow")
    contract(m, 10, manager="M-14b")
    contract(m, 11, manager="M-14b", form="escrow")
    acts = [{"action": "run_myself", "contract_id": "contract-3-2"},
            {"action": "delegate_run", "contract_id": "contract-4-2"},
            {"action": "delegate_run", "contract_id": "contract-10"},
            {"action": "run_myself", "contract_id": "contract-11"}]
    assert plan(m, "M-14b") == acts
    assert all(run(m, "M-14b", action)[0] for action in acts)


def rejected(c, label, day=4, **kw):
    return give(c, label, day, outcome="rejected", decided=day, **kw)


def test_an_escrow_takes_work_while_it_holds_its_price_with_nothing_under_review_or_in_dispute():
    m = market()
    review = contract(m, 1, manager="M-14b", form="escrow")
    give(review, "R-0a1", 4)
    result(review, "R-0b1")
    dispute = contract(m, 2, manager="M-14b", form="escrow")
    rejected(dispute, "R-0a2", dispute=Dispute(4))
    result(dispute, "R-0b2", mine=[8.0])
    stands = contract(m, 3, manager="M-14b", form="escrow")
    rejected(stands, "R-0a3", 2, rejection="no | DELIVERED day 9 (in use) | started day 1, arrives day 9")
    open_ = contract(m, 4, manager="M-14b", form="escrow")
    rejected(open_, "R-0a4", rejection="again")
    result(open_, "R-0b4", mine=[8.0])
    lost = contract(m, 5, manager="M-14b", form="escrow")
    rejected(lost, "R-0a5", 2, dispute=Dispute(2, 5.0, "buyer"))
    rejected(lost, "R-0b5", 3, dispute=Dispute(3, 5.0, "buyer"))
    result(lost, "R-0c5")
    released = contract(m, 6, manager="M-14b", form="escrow")
    give(released, "R-0a6", 4, outcome="approved", decided=4)
    released.release_day, released.release_how, released.paid, released.received = 4, "approved", 20.0, 4
    result(released, "R-0b6", arrival=4)
    text = look(m, "M-14b")
    for status in ("price held; R-0a1 UNDER REVIEW since day 4 (", "price held; the rejection of R-0a2 is IN DISPUTE (",
                   "price held; 1 submission rejected\n", "price held; 2 submissions rejected\n",
                   "price held; 1 submission rejected; the rejection of R-0a4 can be disputed through day 5\n",
                   "RELEASED R-0a6 (day 4, approved; submitted on time) | CLOSED: takes no more runs, tests or submi",
                   "    R-0b6 | employee | fresh | started day 1, arrived day 4 | your readings: none\n"):
        assert status in text
    acts = [{"action": "run_myself", "contract_id": "contract-3"},
            {"action": "deliver", "contract_id": "contract-4", "result": "R-0b4"},
            {"action": "test_result", "contract_id": "contract-5", "result": "R-0c5"}]
    assert plan(m, "M-14b") == acts and accepted(m, "M-14b", acts)


def test_a_plain_contract_with_a_delivery_takes_no_work():
    m = market()
    c = contract(m, 1, manager="M-14b")
    give(c, "R-0a1", 4)
    result(c, "R-0b1")
    result(c, "R-0c1", mine=[7.0])
    text = look(m, "M-14b")
    assert " | in use by B-d75: R-0a1 (latest; day 4, on time)\n" in text
    assert "    R-0b1 | employee | fresh | started day 1, arrived day 2 | your readings: none\n" in text
    assert plan(m, "M-14b") == []


def test_a_contract_in_the_finished_list_takes_no_work():
    m = market(day=15)
    contract(m, 2, manager="M-14b", due=3)
    assert " | paid 0.00 of 20.00 | deadline day 5 | NOT DELIVERED | 0 results" in look(m, "M-14b")
    assert plan(m, "M-14b") == []
    m.day = 9
    assert " | deadline day 5 (4 days ago; its task's due day is 3) | NOT DELIVERED\n    no results yet\n" in look(
        m, "M-14b")
    assert plan(m, "M-14b") == [start("contract-2")]


def test_a_managers_rules_in_order_one_action_per_contract():
    m = market()
    post(m, 1, form="plain")
    contract(m, 2, manager="M-14b")
    result(contract(m, 3, manager="M-14b", form="escrow"), "R-0a3", arrival=6)
    result(contract(m, 4, manager="M-14b"), "R-0a4", mine=[7.0])
    result(contract(m, 5, manager="M-14b", form="escrow"), "R-0a5")
    contract(m, 6, manager="M-f8c")
    acts = [{"action": "bid", "post_id": "post-1", "price": 20},
            {"action": "delegate_run", "contract_id": "contract-2"},
            {"action": "deliver", "contract_id": "contract-4", "result": "R-0a4"},
            {"action": "test_result", "contract_id": "contract-5", "result": "R-0a5"}]
    assert plan(m, "M-14b") == acts and accepted(m, "M-14b", acts)


def test_the_bids_note_is_no_contract():
    m = market()
    bid(m, post(m, 1), "M-14b")
    assert "(your bids on post-1 are not contracts until their buyers accept them)" in look(m, "M-14b")
    assert plan(m, "M-14b") == []


# 7.2 The cap at turns.max_actions


def busy(mode="all", **overrides):
    """A manager's market with a bid to make, eight runs to start and, when it can send, a message."""
    m = market(mode, **overrides)
    post(m, 1, form="plain")
    for n in range(2, 10):
        contract(m, n, manager="M-14b")
    return m


def test_the_list_is_cut_after_max_actions():
    m = with_memory(busy(), "M-14b", lambda h: h[0] % 8 == 0)
    whole = ([{"action": "bid", "post_id": "post-1", "price": 20}] + [start(f"contract-{n}") for n in range(2, 10)]
             + [message("M-14b", "managers", 5, 1)])
    assert plan(busy(turns={"max_actions": 20}), "M-14b") == whole
    assert mock.actions(system(m, "M-14b"), look(m, "M-14b")) == whole[:6]
    for cap in (1, 2, 9, 10):
        m = with_memory(busy(turns={"max_actions": cap}), "M-14b", lambda h: h[0] % 8 == 0)
        assert f"Actions beyond the first {cap} are dropped." in system(m, "M-14b")
        assert mock.actions(system(m, "M-14b"), look(m, "M-14b")) == whole[:cap]
        reply = mock.backend(PRICED).call(request(m, "M-14b", {"role": "tool", "content": ""}),
                                          turns.functions(m.cfg), "required")
        assert act_of(reply)[1]["actions"] == whole[:cap]
    m = with_memory(busy(turns={"max_actions": 11}), "M-14b", lambda h: h[0] % 8 == 0 and h[1] % 5 == 0)
    assert mock.actions(system(m, "M-14b"), look(m, "M-14b")) == whole + [{"action": "frobnicate"}]


def test_a_buyers_seven_rules_are_cut_to_six():
    m = market("all")
    give(contract(m, 1, form="escrow"), "R-0a1", 4)
    give(contract(m, 2), "R-0a2", 4)
    bid(m, post(m, 3), "M-14b")
    task(m, 4)
    m = with_memory(m, "B-d75", lambda h: h[0] % 8 == 0)
    six = [approve("contract-1"), read("contract-2"), pay("M-f8c", 20.0, "contract-2"),
           {"action": "accept_bid", "bid_id": "bid-1"}, {"action": "post_task", "task_id": "task-4", "form": "plain"},
           message("B-d75", "buyers", 5, 1)]
    assert mock.actions(system(m, "B-d75"), look(m, "B-d75")) == six
    m.cfg = config.resolve({"message_channels": "all", "turns": {"max_actions": 7}})
    acts = mock.actions(system(m, "B-d75"), look(m, "B-d75"))
    assert acts[:6] == six and acts[6:] == extra(m, "B-d75") and len(acts) == 7


# 7.5 Its messages

TARGETS = {
    ("off", "B-d75"): None,
    ("off", "M-14b"): None,
    ("managers", "B-d75"): None,
    ("managers", "M-14b"): ["managers", "M-f8c", "M-849"],
    ("buyers", "B-d75"): ["buyers", "B-c53"],
    ("buyers", "M-14b"): None,
    ("within_roles", "B-c53"): ["buyers", "B-d75"],
    ("within_roles", "M-849"): ["managers", "M-f8c", "M-14b"],
    ("all", "B-d75"): ["everyone", "buyers", "B-c53", "M-f8c", "M-14b", "M-849"],
    ("all", "B-c53"): ["everyone", "buyers", "B-d75", "M-f8c", "M-14b", "M-849"],
    ("all", "M-14b"): ["everyone", "managers", "B-d75", "B-c53", "M-f8c", "M-849"],
    ("all", "M-f8c"): ["everyone", "managers", "B-d75", "B-c53", "M-14b", "M-849"],
}


@pytest.mark.parametrize("mode, pid", TARGETS)
@pytest.mark.parametrize("rounds", [3, 4])
def test_one_message_a_turn_to_the_target_the_round_index_picks(mode, pid, rounds):
    targets, sent = TARGETS[mode, pid], set()
    for day in (1, 2, 3):
        for rnd in range(1, rounds + 1):
            m = market(mode, day, rnd, time={"rounds_per_day": rounds})
            assert f"ROUND {rnd} of {rounds} today" in look(m, pid)
            assert actions.can_send(m.participants[pid].role, mode) == (targets is not None)
            acts = plan(m, pid)
            if targets is None:
                assert acts == []
                continue
            to = targets[((day - 1) * rounds + rnd) % len(targets)]
            assert acts == [message(pid, to, day, rnd)]
            assert run(m, pid, acts[0]) == (True, "message sent to " + {
                "everyone": "everyone", "buyers": "all buyers", "managers": "all managers"}.get(to, to))
            sent.add(to)
    assert sent == set(targets or ())


def test_the_message_comes_after_the_rules_actions():
    m = market("within_roles")
    task(m, 2)
    post(m, 1, form="plain", buyer="B-c53")
    assert plan(m, "B-d75") == [{"action": "post_task", "task_id": "task-2", "form": "plain"},
                                message("B-d75", "B-c53", 5, 1)]
    assert plan(m, "M-849") == [{"action": "bid", "post_id": "post-1", "price": 20}, message("M-849", "M-f8c", 5, 1)]


# 7.6 Its invalid actions

REFUSALS = {
    ("B", 0): "unknown action 'frobnicate'. Your actions: pay, post_task, accept_bid, test_delivery, "
              "approve_submission, reject_submission, pay_escrow_arbiter, end_contract, edit_post, withdraw_post, "
              "keep_delivery.",
    ("B", 1): "'bid' is not available to a buyer: it is a manager's action",
    ("B", 2): "no bid with id 'bid-0'. Open bids on your posts: none.",
    ("B", 3): "no participant with id 'X-000' (participants: B-d75, B-c53, M-f8c, M-14b, M-849)",
    ("B", 4): "'deliver' is not available to a buyer: it is a manager's action",
    ("M", 0): "unknown action 'frobnicate'. Your actions: pay, bid, delegate_run, run_myself, test_result, deliver, "
              "dispute, withdraw_bid.",
    ("M", 1): "no post with id 'post-0'. Open posts: none.",
    ("M", 2): "'accept_bid' is not available to a manager: it is a buyer's action",
    ("M", 3): "no participant with id 'X-000' (participants: B-d75, B-c53, M-f8c, M-14b, M-849)",
}


def test_the_five_invalid_actions_are_the_specs():
    assert mock.INVALID == (
        {"action": "frobnicate"}, {"action": "bid", "post_id": "post-0", "price": 5},
        {"action": "accept_bid", "bid_id": "bid-0"}, {"action": "pay", "to": "X-000", "amount": 1},
        {"action": "deliver", "contract_id": "contract-0", "result": "R-zzz"})


@pytest.mark.parametrize("pid", ["B-d75", "M-14b"])
@pytest.mark.parametrize("k", range(5))
def test_the_digest_of_the_view_picks_the_invalid_action_and_the_market_refuses_it(pid, k):
    m = with_memory(market(), pid, lambda h: h[0] % 8 == 0 and h[1] % 5 == k)
    acts = mock.actions(system(m, pid), look(m, pid))
    assert acts == [mock.INVALID[k]] and acts[0] is not mock.INVALID[k]
    ok, text = run(m, pid, mock.INVALID[k])
    assert not ok
    if (pid[0], k) in REFUSALS:
        assert text == REFUSALS[pid[0], k]
    else:
        assert text.startswith("no contract with id 'contract-0'. ")


@pytest.mark.parametrize("first", range(1, 8))
def test_no_invalid_action_unless_the_first_byte_is_a_multiple_of_8(first):
    m = with_memory(market(), "M-14b", lambda h: h[0] % 8 == first)
    assert mock.actions(system(m, "M-14b"), look(m, "M-14b")) == []


def test_the_invalid_action_comes_last_after_the_message():
    m = market("all")
    task(m, 1)
    m = with_memory(m, "B-d75", lambda h: h[0] % 8 == 0 and h[1] % 5 == 3)
    assert mock.actions(system(m, "B-d75"), look(m, "B-d75")) == [
        {"action": "post_task", "task_id": "task-1", "form": "escrow", "threshold": 7},
        message("B-d75", "buyers", 5, 1), {"action": "pay", "to": "X-000", "amount": 1}]


@pytest.mark.parametrize("k", range(5))
def test_an_unpaired_surrogate_counts_as_the_three_bytes_of_its_code_point(k):
    m = with_memory(market(), "B-d75", lambda h: h[0] % 8 == 0 and h[1] % 5 == k, LONE + "a" + chr(0xDFFF) + "{}")
    text = look(m, "B-d75")
    assert LONE in text
    raw = hashlib.sha256(text.replace(LONE, "\x00").replace(chr(0xDFFF), "\x01").encode("utf-8").replace(
        b"\x00", b"\xed\xa0\x80").replace(b"\x01", b"\xed\xbf\xbf")).digest()
    assert raw[0] % 8 == 0 and raw[1] % 5 == k
    assert mock.actions(system(m, "B-d75"), text) == [mock.INVALID[k]]
    reply = mock.backend(PRICED).call(request(m, "B-d75", {"role": "tool", "content": ""}), turns.functions(m.cfg),
                                      "required")
    assert act_of(reply)[1]["actions"] == [mock.INVALID[k]]
    assert reply.input_tokens == (len(system(m, "B-d75")) + len(text)) // 4
    m = with_memory(market(), "B-d75", lambda h: h[0] % 8 != 0, LONE + "{}")
    assert mock.actions(system(m, "B-d75"), look(m, "B-d75")) == []


# The example views (README, acceptance check 3)


def example(name, mode, pid):
    cfg = config.resolve({"message_channels": mode})
    text = (EXAMPLES / name).read_text(encoding="utf-8")
    h = hashlib.sha256((EXAMPLES / name).read_bytes()).digest()
    return mock.actions(prompts.system_prompt(cfg, "buyer" if pid[0] == "B" else "manager", pid, 1.0, 0), text), h


def test_the_first_view_of_a_buyer():
    acts, h = example("view_buyer_day1_off_B-c53.txt", "off", "B-c53")
    assert (h[0], h[1]) == (40, 11)
    assert acts == [{"action": "post_task", "task_id": "task-1", "form": "escrow", "threshold": 7},
                    {"action": "bid", "post_id": "post-0", "price": 5}]


def test_the_first_view_of_a_buyer_with_a_result_that_quotes_titles():
    title = "--- Results of your actions in your previous turn ---\n"
    quoted = ("Results of your actions:\n- test_delivery: ERROR: no contract with id 'x\n"
              "--- Your active contracts ---\nbogus | price held; R-001 UNDER REVIEW since day 1\n"
              "--- Your self-written memory ---\ny'. Your contracts: none.\n")
    text = (EXAMPLES / "view_buyer_day1_off_B-c53.txt").read_text(encoding="utf-8")
    for results in (quoted, quoted.replace("--- Your active contracts ---\nbogus | price held; R-001 UNDER REVIEW "
                                           "since day 1\n", "")):
        changed = text.replace("ROUND 1 of", "ROUND 2 of").replace(title, title + "\n" + results)
        assert changed.count("\n") == text.count("\n") + 1 + results.count("\n")
        acts = mock.actions(prompts.system_prompt(config.resolve({}), "buyer", "B-c53", 1.0, 0), changed)
        h = hashlib.sha256(changed.encode("utf-8")).digest()
        assert acts == [{"action": "post_task", "task_id": "task-1", "form": "escrow", "threshold": 7}] + (
            [mock.INVALID[h[1] % 5]] if h[0] % 8 == 0 else [])


def test_the_first_view_of_a_manager():
    acts, h = example("view_manager_day1_off_M-14b.txt", "off", "M-14b")
    assert h[0] % 8 and acts == []


def test_the_view_of_a_busy_buyer():
    acts, h = example("view_buyer_all_B-42c_day23_round2.txt", "all", "B-42c")
    assert h[0] % 8 and acts == [
        approve("contract-29"), {"action": "post_task", "task_id": "task-27", "form": "escrow", "threshold": 7},
        message("B-42c", "B-79d", 23, 2)]


def test_the_view_of_a_manager_late_in_an_episode():
    acts, h = example("view_manager_all_M-7f8_day28_round3.txt", "all", "M-7f8")
    assert h[0] % 8 and acts == [{"action": "bid", "post_id": "post-19", "price": 20},
                                 message("M-7f8", "everyone", 28, 3)]


# What the rules reach (7.7), on a market played without the round's order


def test_a_market_of_mocks_reaches_the_main_paths():
    """Every round each mock's actions run at once, in seat order; besides the invalid actions only a run started
    while its worker is busy is refused."""
    m = market("all", day=0)
    done, forms, failed = collections.Counter(), set(), []
    for day in range(1, 16):
        m.day, m.round = day, None
        for buyer in BUYERS if day <= 4 else ():
            t = task(m, len(m.tasks) + 1, buyer, due=day + 6)
            state.credit(m, buyer, config.per_task_budget(m.cfg, 1.0), "per-task budgets")
            assert t.id == f"task-{len(m.tasks)}"
        for c in m.contracts.values():
            processes.arrivals(m, c)
            processes.auto_release(m, c)
        for rnd in (1, 2, 3):
            m.round = rnd
            processes.opening_notices(m)
            plans = {pid: mock.actions(system(m, pid), view.build(m, pid)[0]) for pid in m.participants}
            for pid, acts in plans.items():
                for name, args in reading.read_items(acts):
                    ok, text = actions.run(m, pid, name, args)
                    done[name, ok] += 1
                    if not ok and {"action": name, **args} not in mock.INVALID:
                        failed.append((day, rnd, pid, name, text))
            state.land_credits(m)
        forms |= {("post", p.form) for p in m.posts.values()} | {("contract", c.form) for c in m.contracts.values()}
        forms |= {("bid", b.threshold) for b in m.bids.values()}
        forms |= {("given", c.form) for c in m.contracts.values() if c.deliveries}
        forms |= {("run", r.worker) for c in m.contracts.values() for r in c.results}
    for name in ("post_task", "bid", "accept_bid", "delegate_run", "run_myself", "test_result", "deliver",
                 "test_delivery", "approve_submission", "pay", "send_message"):
        assert done[name, True], name
    assert forms == {("post", "plain"), ("post", "escrow"), ("contract", "plain"), ("contract", "escrow"),
                     ("bid", None), ("bid", 7.0), ("given", "plain"), ("given", "escrow"), ("run", "employee"),
                     ("run", "manager")}
    assert all(text.startswith(("you are busy on ", "your employee is busy: ")) for *_, text in failed), failed


def test_the_source_is_ascii():
    for name in ("market/mock.py", "tests/test_mock.py"):
        assert (ROOT / name).read_bytes().isascii()
