"""Acceptance check 4 of spec/README.md, the market-level half: a scripted test model returns the example files'
assistant messages in the market state the files describe, market/rounds.py plays the rounds, and every request
a model receives is recorded. The request each file names must be the file byte for byte: for a turn, after its
system message, which must be the participant's system prompt.

The turns' state is the reference's seed 0 on day 1 (test_views_examples.day_1: the participants, their shares
and the four tasks, set up by hand, as randomness.md leaves the draws to the generator). Round 1 is played as the
round-2 views show it: each buyer posted its first task (escrow, threshold 7), B-d75 before B-c53, no manager
acted, and everyone wrote the memory the example's act call carries. What no file shows is chosen freely: the
rest of the turn orders, each reply's cost (small enough for `tokens 0.00`), what the others do in round 2 and
what the correction answers.
"""

import copy
import json
import pathlib
import re

import pytest

import test_views_examples as views
from market import episode, processes, prompts, rounds, sandbox
from market.models import CostTracker, Reply

SPEC = pathlib.Path(__file__).parent.parent / "spec"
BUYERS, MANAGERS = ("B-c53", "B-d75"), ("M-14b", "M-849", "M-f8c")
ORDERS = [BUYERS + MANAGERS, ("M-f8c", "B-d75", "M-849", "B-c53", "M-14b")]
ROUND_1 = [("B-d75", "B-c53") + MANAGERS, ORDERS[1]]  # post-3 was made before post-1 (view-contracts.md, 3.1)
COST = 0.001  # dollars a reply; credits_per_usd is 1 (config.md, 3.4.4)

runs_programs = pytest.mark.skipif(sandbox.host_check() is not None, reason="programs cannot be run isolated here")


def example(name):
    return (SPEC / "examples" / name).read_text(encoding="utf-8")


def tools(name):
    return example(f"tools_{name}.json").rstrip("\n")


def as_file(value):
    """A JSON value as the example files write it."""
    return json.dumps(value, indent=1, ensure_ascii=False)


COMPUTE = json.loads(example("turn_with_compute_off_B-d75.json"))[1]
ACT = json.loads(example("turn_with_correction_off.json"))[3]


def act(*actions):
    """An assistant message of the test model: the example's act call, memory and all, with these actions."""
    call = copy.deepcopy(ACT["tool_calls"][0])
    call["function"]["arguments"] = json.dumps(json.loads(call["function"]["arguments"]) | {"actions": list(actions)})
    return {"content": ACT["content"], "tool_calls": [call]}


class Model:
    """A scripted test model: it returns the replies behind its assistant messages, in order, and records each
    request it receives."""

    def __init__(self, script=()):
        self.script, self.requests = list(script), []

    def call(self, messages, functions=None, tool_choice=None):
        self.requests.append({"messages": copy.deepcopy(messages), "functions": copy.deepcopy(functions),
                              "tool_choice": copy.deepcopy(tool_choice)})
        message = self.script.pop(0)
        return Reply(text=message["content"], tool_calls=copy.deepcopy(message["tool_calls"]), cost=COST)


class Played:
    """A market whose rounds market/rounds.py plays, each participant's model scripted; every turn's replies
    start with `opening`. Its log starts as an episode's does, with the function definitions (running.md, 6.3
    D)."""

    def __init__(self, m, opening=(), summarizer=None):
        self.m, self.log, self.opening = m, [], list(opening)
        m.log = self.log.append
        episode.fresh_start(m)
        self.models = {pid: Model() for pid in m.participants}
        self.ctx = rounds.Ctx(self.models, summarizer, CostTracker(None), sandbox.run_program, map)

    def play(self, rnd, order, scripts):
        """A round from its start (episode.md, 4, with the order given); returns each model's requests of it."""
        before = {pid: len(model.requests) for pid, model in self.models.items()}
        for pid, model in self.models.items():
            model.script = [*self.opening, *scripts.get(pid, [act()])]
        self.m.round = rnd
        processes.opening_notices(self.m)
        rounds.play(self.m, order, self.ctx)
        assert not any(model.script for model in self.models.values())
        return {pid: model.requests[before[pid]:] for pid, model in self.models.items()}

    def system(self, pid):
        p = self.m.participants[pid]
        return {"role": "system", "content": prompts.system_prompt(self.m.cfg, p.role, pid, p.share, p.seat)}

    def balances(self, pid):
        """The balance each turn of a participant started with, as logged (rounds.md, 3, step 3)."""
        return [r["balance"] for r in self.log if r["type"] == "turn" and r["participant"] == pid]


def round_1(order):
    """Day 1, round 1 of the reference's seed 0, every turn starting with the example's compute call."""
    day = Played(views.day_1(), [COMPUTE])
    post = {"action": "post_task", "form": "escrow", "threshold": 7}
    requests = day.play(1, order, {"B-c53": [act(post | {"task_id": "task-1"})],
                                   "B-d75": [act(post | {"task_id": "task-3"})]})
    return day, requests


def offered(request):
    """A request's functions and tool_choice, as examples/tools_*.json write them."""
    return as_file({"tool_choice": request["tool_choice"], "tools": request["functions"]})


@runs_programs
@pytest.mark.parametrize("order", ROUND_1)
def test_the_turn_with_a_program(order):
    """examples/turn_with_compute_off_B-d75.json: the second request of B-d75 on day 1, round 1 (balance 135.60)."""
    day, requests = round_1(order)
    first, second = requests["B-d75"]
    assert second["messages"][0] == day.system("B-d75")
    assert as_file(second["messages"][1:]) == example("turn_with_compute_off_B-d75.json")
    assert first["messages"] == second["messages"][:2]  # turns.md, 5.1: the step adds to the same conversation
    assert offered(first) == tools("first_call_compute_act")  # turns.md, 4.1
    assert offered(second) == tools("act_forced_compute_act")
    assert day.balances("B-d75") == [135.6]


@runs_programs
@pytest.mark.parametrize("order", ROUND_1)
def test_the_first_requests_of_day_1(order):
    """The first request of the first buyer and of the first manager: their example prompts and day-1 views."""
    requests = round_1(order)[1]
    for pid, role in (("B-c53", "buyer"), ("M-14b", "manager")):
        assert requests[pid][0]["messages"] == [
            {"role": "system", "content": example(f"system_prompt_{role}_off_{pid}.txt")},
            {"role": "user", "content": example(f"view_{role}_day1_off_{pid}.txt")}]


@runs_programs
@pytest.mark.parametrize("order", ROUND_1)
@pytest.mark.parametrize("next_order", ORDERS)
def test_the_turn_with_a_correction(order, next_order):
    """examples/turn_with_correction_off.json: M-849's correction request on day 1, round 2 (balance 76.80)."""
    day = round_1(order)[0]
    requests = day.play(2, next_order, {"M-849": [ACT, act()]})
    first, second, correction = requests["M-849"]
    assert correction["messages"][0] == day.system("M-849")
    assert as_file(correction["messages"][1:]) == example("turn_with_correction_off.json")
    assert first["messages"] == correction["messages"][:2] and second["messages"] == correction["messages"][:4]
    assert offered(correction) == tools("act_forced_act")  # turns.md, 11.5
    assert [len(requests[pid]) for pid in BUYERS + MANAGERS] == [2, 2, 2, 3, 2]  # the round's one correction
    assert day.balances("M-849") == [76.8, 76.8]
    # view.md, 14.1: the same round's view of B-c53, whose first turn posted task-1
    text = (SPEC / "view.md").read_text(encoding="utf-8")
    start = text.index("```\n", text.index("### 14.1 A buyer, day 1, round 2")) + 4
    assert requests["B-c53"][0]["messages"][1]["content"] == text[start:text.index("\n```", start)]
    # running.md, 6.3 D: the log holds each call's request as it was sent, the earlier messages by reference
    for pid, model in day.models.items():
        calls = [r for r in day.log if r["type"] == "call" and r["participant"] == pid]
        assert [rounds.request(day.log, r) for r in calls] == model.requests
        assert [as_file(rounds.request(day.log, r)["messages"][1:]) for r in calls if r["call"] == "correction"] == [
            example("turn_with_correction_off.json")] * (pid == "M-849")


def test_the_summarizer_requests():
    """examples/summarizer_request_first_all.json and summarizer_request_update_all.json: the conversation among
    all participants that the two files show, each message sent with send_message in its day and round; the
    first summary is asked for by the first view built once an 11th message exists, the update once a 21st does
    (messaging.md, 4.1, 4.3), and carries what the stand-in summarizer answered the first time."""
    files = [example(f"summarizer_request_{kind}_all.json") for kind in ("first", "update")]
    sent = [(int(day), int(rnd), sender, text) for file in files for day, rnd, sender, text in re.findall(
        r"^\[day (\d+), round (\d+)\] (\S+) -> everyone: (.*)$", json.loads(file)[1]["content"], flags=re.M)]
    assert len(sent) == 20 and sent == sorted(sent, key=lambda x: x[:2])
    sent.append((8, 1, "B-c53", "one more"))  # no file shows the 21st message
    summarizer = Model({"content": f"(summary of {n} messages)", "tool_calls": None} for n in (10, 20))
    m = views.market("all", {p.id: p.share for p in views.day_1().participants.values()})
    played, asked = Played(m, summarizer=summarizer), []
    for day, rnd in [(day, rnd) for day in range(1, 9) for rnd in (1, 2, 3)][:23]:
        scripts = {}
        for _, _, sender, text in (x for x in sent if x[:2] == (day, rnd)):
            scripts.setdefault(sender, []).append({"action": "send_message", "to": "everyone", "text": text})
        m.day = day
        # a round's messages are in the order its actions ran: its senders' ranks in the turn order (2.6)
        order = list(scripts) + [pid for pid in m.participants if pid not in scripts]
        requests = played.play(rnd, order, {pid: [act(*actions)] for pid, actions in scripts.items()})
        if len(summarizer.requests) > len(asked):
            asked.append((day, rnd))
            n = 10 * len(asked)
            for pid in order:  # every reader's view of the round shows the summary just written
                assert (f"Conversation among all participants:\n  Summary of the {n} earlier messages (written "
                        f"automatically; you are {pid} in it): (summary of {n} messages)\n\n"
                        ) in requests[pid][0]["messages"][1]["content"]
    assert asked == [(5, 3), (8, 2)] and len(m.messages) == 21
    assert [as_file(r["messages"]) for r in summarizer.requests] == files
    assert all(r["functions"] is None and r["tool_choice"] is None for r in summarizer.requests)  # 5.1


def test_this_file_is_pure_ascii():
    assert pathlib.Path(__file__).read_text(encoding="utf-8").isascii()
