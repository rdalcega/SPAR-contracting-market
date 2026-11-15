"""Acceptance check 5 (spec/README.md, "Acceptance checks", item 5; spec/running.md, 7.7): free episodes.

Every seat on the offline mock with the list prices of the default models and every other parameter at its
default: seeds 0, 1 and 2 in each of the five modes, run through the command, the real sandbox running the mock's
program. Then what running.md 7.1 and 5 guarantee of such an episode: run twice it gives the same log, and stopped
on any day and resumed it gives what the uninterrupted episode gave.
"""

import collections
import dataclasses
import json
import shutil
import time

import pytest
import yaml

from market import config, episode, mock, models, rounds, run, turns

MOCK = {"provider": "mock", "price_in_per_m": 0.15, "price_out_per_m": 1.875}
SEEDS = (0, 1, 2)
WANTED = (  # running.md, 7.7, in its order
    "a program run", "a plain post", "an escrow post", "a plain bid", "an escrow bid", "an accepted plain bid",
    "an accepted escrow bid", "an employee run", "an own run", "a manager reading", "a buyer reading",
    "a plain delivery", "an escrow submission", "an approval", "a payment",
    "an invalid action followed by a correction",
)
TIMES = {"program": "wall", "call": "latency"}  # the measured durations a log holds, by record type


def write_config(folder, mode):
    """The smoke config of one mode: mock seats, everything else at its default (7.1, "Prompts as configured")."""
    path = folder / f"{mode}.yaml"
    path.write_text(yaml.safe_dump({"name": mode, "message_channels": mode,
                                    "models": {"buyers": MOCK, "managers": MOCK}}))
    return str(path)


def records(folder):
    """The records of an episode's log, one by one (6.2)."""
    with open(folder / "log.jsonl", encoding="ascii") as f:
        for line in f:
            yield json.loads(line)


def summary(folder):
    return json.loads((folder / "summary.json").read_text())


def shown(folder):
    """What 7.7 lists, as the things one episode's log shows, with its messages and summaries; every balance as
    the log's money records rebuild it (6.3, E, "money"); and the log's records counted by type and role."""
    seen, forms, invalid, balances, roles, counts = set(), {}, set(), {}, {}, collections.Counter()
    for r in records(folder):
        kind = r["type"]
        turn = r["day"], r["round"], r.get("participant")
        role = roles.get(r.get("participant"))
        counts[kind] += 1
        counts[kind, role] += 1
        if kind == "call":
            counts["correction", role] += r["call"] == "correction"
            counts["input", role] += r["reply"]["input_tokens"]
            counts["output", role] += r["reply"]["output_tokens"]
            counts["dollars", role] += r["reply"]["cost"]
        elif kind == "action":
            counts["failed", role] += r["status"] == "ERROR"
        elif kind == "money" and r["kind"] == "tokens":
            counts["tokens"] -= r["amount"]
        elif kind in ("reading", "run"):
            counts[kind, r.get("side") or r["worker"]] += 1
        if kind == "fresh_start":
            balances.update({p["id"]: p["start_balance"] for p in r["participants"]})
            roles.update({p["id"]: p["role"] for p in r["participants"]})
        elif kind == "money" and not r.get("deferred") and not r.get("held"):
            balances[r["participant"]] += r["amount"]
        elif kind == "credit_landed":
            balances[r["to"]] += r["amount"]
        elif kind == "program":
            ok = (r["status"], r["exit_code"], r["output"]) == ("ok", 0, "45")
            seen.add("a program run" if ok else "a program that did not print 45")
        elif kind == "post":
            seen.add({"plain": "a plain post", "escrow": "an escrow post"}[r["form"]])
        elif kind == "bid":
            seen.add("a plain bid" if r["threshold"] is None else "an escrow bid")
        elif kind == "contract":
            forms[r["contract"]] = r["form"]
            seen.add({"plain": "an accepted plain bid", "escrow": "an accepted escrow bid"}[r["form"]])
        elif kind == "run":
            seen.add({"employee": "an employee run", "manager": "an own run"}[r["worker"]])
        elif kind == "reading":
            seen.add({"manager": "a manager reading", "buyer": "a buyer reading"}[r["side"]])
        elif kind == "delivery":
            seen.add({"plain": "a plain delivery", "escrow": "an escrow submission"}[forms[r["contract"]]])
        elif kind == "decision" and r["decision"] == "approved":
            seen.add("an approval")
        elif kind == "payment":
            seen.add("a payment")
        elif kind == "action" and r["phase"] == "first" and r["status"] == "ERROR":
            if {"action": r["name"], **r["arguments"]} in mock.INVALID:
                invalid.add(turn)
        elif kind == "call" and r["call"] == "correction" and turn in invalid:
            seen.add("an invalid action followed by a correction")
        elif kind in ("message", "summary"):
            seen.add(kind)
    return seen, balances, counts


@pytest.fixture(scope="module")
def free(tmp_path_factory):
    """The 15 free episodes: one run of the command per mode, over seeds 0, 1 and 2."""
    root = tmp_path_factory.mktemp("free")
    trackers, exits, real = [], {}, models.CostTracker

    def tracked(cap):
        trackers.append(real(cap))
        return trackers[-1]

    began = time.monotonic()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(models, "CostTracker", tracked)
        for mode in config.MODES:
            exits[mode] = run.main([write_config(root, mode), "--out", str(root)])
    print(f"\nthe 15 free episodes took {time.monotonic() - began:.1f} s")
    yield {"root": root, "exits": exits, "trackers": trackers}
    shutil.rmtree(root)


@pytest.fixture(scope="module")
def scans(free):
    return {(mode, seed): shown(free["root"] / mode / f"seed_{seed}") for mode in config.MODES for seed in SEEDS}


@pytest.fixture(scope="module")
def logs(scans):
    """What each of the 15 logs shows of 7.7's list."""
    return {key: seen for key, (seen, _, _) in scans.items()}


def test_the_money_records_of_a_log_rebuild_every_balance(free, scans):
    for (mode, seed), (_, balances, _) in scans.items():
        scores = summary(free["root"] / mode / f"seed_{seed}")["scores"]
        assert {s["id"]: s["final_balance"] for s in scores} == {pid: round(x, 2) for pid, x in balances.items()}


def test_the_metrics_of_a_summary_are_those_its_log_gives(free, scans):
    for (mode, seed), (_, _, counts) in scans.items():
        m = summary(free["root"] / mode / f"seed_{seed}")["metrics"]
        for role in ("buyer", "manager"):
            assert m[f"{role}_turns"] == counts["turn", role] > 0
            assert m[f"{role}_calls"] == counts["call", role] == m[f"{role}_output_tokens"]  # 7.1: ct = 1
            assert m[f"{role}_corrections"] == counts["correction", role] > 0
            assert m[f"{role}_programs"] == counts["program", role] == m[f"{role}_turns"]  # 7.2: one a turn
            assert m[f"{role}_actions"] == counts["action", role]
            assert m[f"{role}_failed_actions"] == counts["failed", role] > 0
            assert m[f"{role}_input_tokens"] == counts["input", role]
            assert m[f"{role}_dollars"] == round(counts["dollars", role], 6)
            assert [m[f"{role}_{name}"] for name in ("format_errors", "turns_lost_provider", "turns_lost_format",
                                                     "reasoning_tokens")] == [0, 0, 0, 0]
        assert m["dollars"] == round(counts["dollars", "buyer"] + counts["dollars", "manager"], 6)
        assert abs(m["costs"]["tokens"] - counts["tokens"]) < 0.006
        assert (m["messages"], m["summaries_written"]) == (counts["message"], counts["summary"])
        assert (m["tasks_created"], m["tasks_posted"], m["bids"]) == (counts["task"], counts["post"], counts["bid"])
        assert (m["contracts"], m["payments"], m["runs_started"]) == (counts["contract"], counts["payment"],
                                                                      counts["run"])
        assert (m["own_runs"], m["manager_readings"], m["buyer_readings"]) == (
            counts["run", "manager"], counts["reading", "manager"], counts["reading", "buyer"])
        assert m["deliveries_plain"] + m["submissions_escrow"] == counts["delivery"]
        assert m["escrow_approved"] == counts["decision"] and m["escrow_released"] == counts["release"]
        assert counts["settled"] == counts["fresh_start"] == counts["close"] == 1 and counts["system_prompt"] == 5
        assert counts["functions"] == 1  # the function definitions, once (6.3 D)


def test_every_episode_ends_ok_with_the_cost_tracker_at_0(free):
    assert free["exits"] == dict.fromkeys(config.MODES, 0)
    assert len(free["trackers"]) == 5
    assert [(t.total, t.unknown) for t in free["trackers"]] == [(0.0, 0)] * 5
    for mode in config.MODES:
        for seed in SEEDS:
            folder = free["root"] / mode / f"seed_{seed}"
            s = summary(folder)
            assert (s["status"], s["error"], s["seed"], s["message_channels"]) == ("ok", None, seed, mode)
            assert s["days_completed"] == s["close_day"] >= s["last_arrival_day"] >= 10
            assert s["money_check"] == 0
            assert s["metrics"]["dollars"] > 0  # the mock's notional cost (7.1)
            assert s["metrics"]["costs"]["tokens"] > 0  # charged to the agents in credits
            assert s["metrics"]["calls_unknown_cost"] == 0
            assert not (folder / "checkpoint.pickle").exists()


def test_the_logs_show_each_thing_of_7_7(logs):
    for thing in WANTED:
        where = [key for key, seen in logs.items() if thing in seen]
        assert where, f"no log shows {thing}"
        print(f"{thing}: {len(where)} logs, first {where[0]}")


def test_messages_and_summaries_in_every_mode_other_than_off_and_none_in_off(free, logs):
    for (mode, seed), seen in logs.items():
        metrics = summary(free["root"] / mode / f"seed_{seed}")["metrics"]
        if mode == "off":
            assert not {"message", "summary"} & seen
            assert (metrics["messages"], metrics["summaries_written"]) == (0, 0)
        else:
            assert {"message", "summary"} <= seen, (mode, seed)
            assert metrics["messages"] > 0 and metrics["summaries_written"] > 0
        assert metrics["summarizer_calls"] == 0  # messaging.md, 5.5: the free summary makes no call


def test_every_program_of_the_mock_ended_ok_with_45(logs):
    assert not [key for key, seen in logs.items() if "a program that did not print 45" in seen]


def test_every_request_is_rebuilt_from_the_log(free):
    """6.3 D: a call's record gives its request by reference (the system prompt, the earlier messages of its
    turn, the functions by name) and rounds.request rebuilds it. The mock's reply depends only on its request,
    its input tokens on the length of every message (7.1): asked again, it gives the reply the log holds."""
    for mode in config.MODES:
        for seed in SEEDS:
            log = list(records(free["root"] / mode / f"seed_{seed}"))
            assert [r["type"] for r in log[:2]] == ["fresh_start", "functions"]
            assert log[1]["functions"] == turns.role_functions(config.resolve({"message_channels": mode}))
            assert sum(r["type"] == "functions" for r in log) == 1
            calls = [r for r in log if r["type"] == "call"]
            assert len(calls) > 100 and not any(r["type"] == "call_failed" for r in log)
            for r in calls:
                reply = mock.backend(r["spec"]).call(**rounds.request(log, r))
                assert dataclasses.asdict(reply) | {"latency": r["reply"]["latency"]} == r["reply"]
            stored = sum(len(json.dumps(r["request"])) for r in calls)
            sent = sum(len(json.dumps(rounds.request(log, r))) for r in calls)
            assert stored < sent / 10  # the point of the references


def comparable(folder):
    """A log's records without what two runs of one episode may differ in (7.1; 5, "The guarantee"): measured
    durations, the resume records, and the position each record has in its log, so with each call's request in
    full, as rounds.request rebuilds it from the records its `prior` and its functions' names refer to."""
    log = list(records(folder))
    for n, r in enumerate(log, 1):
        assert r["n"] == n
        if r["type"] == "resume":
            continue
        r = {key: value for key, value in r.items() if key != "n"}
        if r["type"] == "program":
            assert r.pop("wall") >= 0
        if r["type"] == "call":
            r["reply"] = {key: value for key, value in r["reply"].items() if key != "latency"}
            r["request"] = rounds.request(log, r)
        yield r


def same_episode(folder, other):
    """Two folders hold the same episode: log, summary, scores and metrics, apart from times."""
    count = 0
    for a, b in zip(comparable(folder), comparable(other), strict=True):
        assert a == b
        count += 1
    assert count > 1000
    a, b = summary(folder), summary(other)
    assert a.pop("wall_s") > 0 and b.pop("wall_s") > 0
    assert a == b
    assert a["status"] == "ok" and a["scores"] and a["metrics"]


@pytest.fixture
def out(tmp_path):
    yield tmp_path
    shutil.rmtree(tmp_path)


def test_the_same_seed_and_config_run_twice_give_the_same_log(free, out):
    assert run.main([write_config(out, "all"), "--out", str(out), "--seeds", "2"]) == 0
    same_episode(out / "all" / "seed_2", free["root"] / "all" / "seed_2")


class Killed(BaseException):
    """A killed process: nothing is written on the way out."""


@pytest.mark.parametrize("mode, seed, kills", [
    ("all", 2, [(1, 2, Killed)]),  # during the first day: no checkpoint, the next run starts from the beginning
    ("all", 2, [(2, 1, Killed), (3, 3, RuntimeError), (4, None, Killed)]),
    ("within_roles", 0, [(9, 2, RuntimeError), (17, None, RuntimeError)]),
    ("off", 2, [("last", 3, Killed)]),
    ("buyers", 0, [("settlement", None, Killed)]),  # after the close day's checkpoint: settled without a day
])
def test_an_episode_stopped_and_resumed_gives_what_the_uninterrupted_episode_gave(free, out, monkeypatch, mode,
                                                                                    seed, kills):
    """Each kill is (day, round, how): the run stops in that round of that day, or at the day's start when the
    round is None, by a killed process or by a failure of the implementation (4.3)."""
    whole = free["root"] / mode / f"seed_{seed}"
    folder = out / mode / f"seed_{seed}"
    argv = [write_config(out, mode), "--out", str(out), "--seeds", str(seed)]
    last = summary(whole)["close_day"]
    play_day, play, settle = episode.play_day, rounds.play, episode.settle
    resumes = 0
    for day, rnd, how in kills:
        day = last if day == "last" else day

        def stop_day(m, ctx):
            if (m.day + 1, None) == (day, rnd):
                raise how("stopped")
            play_day(m, ctx)

        def stop_round(m, order, ctx):
            if (m.day, m.round) == (day, rnd):
                raise how("stopped")
            play(m, order, ctx)

        def stop_settlement(m):
            raise how("stopped")

        monkeypatch.setattr(episode, "play_day", stop_day)
        monkeypatch.setattr(rounds, "play", stop_round)
        monkeypatch.setattr(episode, "settle", stop_settlement if day == "settlement" else settle)
        if how is Killed:
            with pytest.raises(Killed):
                run.main(argv)
            assert summary(folder)["status"] == "running"
        else:
            assert run.main(argv) == 1
            stopped = summary(folder)
            assert stopped["status"] == "error" and "RuntimeError: stopped" in stopped["error"]
            assert "settlement" not in stopped and "scores" not in stopped and "metrics" not in stopped
        completed = last if day == "settlement" else day - 1
        assert (folder / "checkpoint.pickle").exists() == (completed > 0)
        resumes += completed > 0
    monkeypatch.undo()
    assert run.main(argv) == 0
    assert sum(r["type"] == "resume" for r in records(folder)) == resumes
    assert sum(r["type"] == "stopped" for r in records(folder)) == 0  # cut back with the interrupted day
    same_episode(folder, whole)
