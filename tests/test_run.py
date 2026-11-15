"""Running episodes (spec/running.md, 1 to 6): market/run.py, through the command and through its parts.

The episodes here are short (a few days) and free: mock seats, or a scripted backend that answers as the mock
does at a real price, or fails every call. A stand-in answers the mock's program; tests/test_free_episodes.py
runs whole default episodes with the real sandbox.
"""

import copy
import dataclasses
import json
import os
import pickle
import shutil
import threading
import time
import types

import pytest
import yaml

from market import __version__, config, episode, mock, models, rounds, run, sandbox, state, turns

MOCK = {"provider": "mock", "price_in_per_m": 0.15, "price_out_per_m": 1.875}
PAID = {"provider": "openai_compatible", "base_url": "http://localhost:9/v1", "model": "scripted",
        "price_in_per_m": 0.15, "price_out_per_m": 1.875}
FAST = {"name": "fast", "time": {"horizon_min_days": 2, "horizon_end_prob": 0.9, "close_after_due_days": 1},
        "value": {"late_decay": 1}, "tasks": {"due_days_range": [1, 2]}, "models": {"buyers": MOCK, "managers": MOCK}}
PAID_SEATS = {"models": {"buyers": PAID, "managers": PAID}}
SETTLED = {"last_arrival_day", "close_day", "close_reason", "settlement", "scores", "welfare", "money_check",
           "metrics"}


class Killed(BaseException):
    """A killed process: nothing is written on the way out (2, "Interrupting a run")."""


@pytest.fixture(autouse=True)
def programs(monkeypatch):
    """The mock's program answered without a sandbox; the host check stays the real one."""
    stand_in = types.SimpleNamespace(run_program=lambda code, compute: sandbox.Result("ok", 0, 0.0, "45"),
                                     host_check=sandbox.host_check)
    monkeypatch.setattr(run, "sandbox", stand_in)
    return stand_in


@pytest.fixture
def command(tmp_path):
    """The command on the short config with some keys replaced: command(*options, out=..., **keys) -> status."""
    def command(*options, out="out", **keys):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump({**FAST, **keys}))
        return run.main([str(path), "--out", str(tmp_path / out), *options])

    return command


@pytest.fixture
def trackers(monkeypatch):
    """The cost tracker of each run of the command, in order."""
    made, real = [], models.CostTracker

    def tracked(cap):
        made.append(real(cap))
        return made[-1]

    monkeypatch.setattr(models, "CostTracker", tracked)
    return made


class Scripted:
    """A scripted backend with a real price: the mock's replies, each adding its dollars to the run's tracker as
    the model layer does (models.md, 7); a request without functions is the summarizer's."""

    def __init__(self, spec, tracker, price, known):
        self.answers, self.tracker, self.price, self.known = mock.backend(spec), tracker, price, known

    def call(self, messages, functions=None, tool_choice=None):
        self.tracker.add(self.price, self.known)
        cost = {"cost": self.price, "cost_known": self.known, "cost_source": "reported" if self.known else "unknown"}
        if not functions:
            return models.Reply(text="A summary.", finish_reason="stop", **cost)
        return dataclasses.replace(self.answers.call(messages, functions, tool_choice), **cost)


class Failing:
    """A backend whose every call fails after the model layer's retries."""

    def call(self, messages, functions=None, tool_choice=None):
        raise models.CallFailed("HTTP 500 for model 'scripted': no")


@pytest.fixture
def scripted(monkeypatch):
    """Replaces the model layer's backends: scripted(price) or scripted(fail=True); returns the specs set up."""
    def scripted(price=0.01, known=True, fail=False):
        specs = []

        def backend(spec, llm, tracker):
            specs.append(spec)
            return Failing() if fail else Scripted(spec, tracker, price, known)

        monkeypatch.setattr(models, "backend", backend)
        return specs

    return scripted


def folder(tmp_path, seed=0, out="out", name="fast"):
    return tmp_path / out / name / f"seed_{seed}"


def records(path):
    return [json.loads(line) for line in (path / "log.jsonl").read_text().splitlines()]


def kinds(path):
    return [r["type"] for r in records(path)]


def summary(path):
    return json.loads((path / "summary.json").read_text())


def comparable(path, extra):
    """A log's records without the place each has in its log: no `n`, and each call's request in full, as
    rounds.request rebuilds it from the records its `prior` and its functions' names refer to (6.3 D)."""
    log = records(path)
    return [{k: rounds.request(log, r) if k == "request" else v for k, v in r.items() if k != "n"}
            for r in log if r["type"] not in extra]


def same_episode(path, other, extra=("resume",)):
    """Two folders hold the same episode: the log apart from the resume records (and the place each record has
    in its log), the summary apart from the wall time (5, "The guarantee")."""
    assert comparable(path, extra) == comparable(other, extra)
    a, b = summary(path), summary(other)
    assert a.pop("wall_s") >= 0 and b.pop("wall_s") >= 0
    assert a == b


def stop_at(monkeypatch, day, rnd, how=RuntimeError, seed=None):
    """From now on, an episode (of that seed) stops when it reaches that round of that day."""
    play = rounds.play

    def stopping(m, order, ctx):
        if (m.day, m.round) == (day, rnd) and seed in (None, m.seed):
            raise how("stopped here")
        play(m, order, ctx)

    monkeypatch.setattr(rounds, "play", stopping)


REAL = [(rounds, "play", rounds.play), (episode, "play_day", episode.play_day), (episode, "settle", episode.settle),
        (os, "replace", os.replace), (os, "_exit", os._exit)]


def go_on(monkeypatch):
    """Nothing stops or fails an episode any more (the stand-in for the sandbox stays)."""
    for module, name, real in REAL:
        monkeypatch.setattr(module, name, real)


# 1. The command line


def test_one_episode_per_seed_each_in_its_folder(command, tmp_path, capsys):
    assert command("--seeds", "0,3") == 0
    assert sorted(os.listdir(tmp_path / "out" / "fast")) == ["seed_0", "seed_3"]
    for seed in (0, 3):
        assert sorted(os.listdir(folder(tmp_path, seed))) == ["config.json", "log.jsonl", "summary.json"]
        assert summary(folder(tmp_path, seed))["seed"] == seed
    out = capsys.readouterr().out.splitlines()
    assert sorted(line[:11] for line in out[:2]) == ["seed 0: ok,", "seed 3: ok,"]


def test_the_options_replace_the_run_settings_and_nothing_else(command, tmp_path):
    assert command("--seeds", "3", "--cost-cap", "7.5", "--parallel", "2") == 0
    resolved = json.loads((folder(tmp_path, 3) / "config.json").read_text())
    plain = config.resolve(FAST)
    assert resolved == {**plain, "seeds": [3], "cost_cap_usd": 7.5, "parallel_episodes": 2,
                        "output_dir": str(tmp_path / "out"), "seed": 3, "version": __version__}
    assert set(config.DEFAULTS) <= set(resolved)  # 6.1: every key of the schema


def test_a_relative_out_is_resolved_against_the_working_directory(command, tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(FAST))
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    assert run.main([str(tmp_path / "config.yaml"), "--out", "here", "--seeds", "1"]) == 0
    assert summary(tmp_path / "elsewhere" / "here" / "fast" / "seed_1")["status"] == "ok"


@pytest.mark.parametrize("options, named", [
    (("--seeds", "0,0"), "seeds"),
    (("--seeds", "-1"), "seeds"),
    (("--cost-cap", "0"), "cost_cap_usd"),
    (("--cost-cap", "nan"), "cost_cap_usd"),
    (("--parallel", "0"), "parallel_episodes"),
    (("--out", ""), "output_dir"),
])
def test_an_option_is_checked_by_the_rule_of_the_key_it_replaces(tmp_path, capsys, options, named):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(FAST))
    assert run.main([str(tmp_path / "config.yaml"), "--out", str(tmp_path / "out"), *options]) != 0
    assert named in capsys.readouterr().err
    assert os.listdir(tmp_path) == ["config.yaml"]


@pytest.mark.parametrize("argv", [[], ["c.yaml", "--seeds", "a"], ["c.yaml", "--parallel", "1.5"],
                                  ["c.yaml", "--cost-cap", "much"], ["c.yaml", "--fast"]])
def test_a_command_line_that_cannot_be_read_is_refused(argv, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as refused:
        run.main(argv)
    assert refused.value.code != 0
    assert os.listdir(tmp_path) == []


def test_a_refused_config_writes_nothing_and_exits_non_zero(command, tmp_path, capsys):
    assert command(turns={"speed": 3}) != 0
    assert "turns.speed" in capsys.readouterr().err
    assert run.main([str(tmp_path / "none.yaml"), "--out", str(tmp_path / "out")]) != 0
    assert "none.yaml" in capsys.readouterr().err
    assert os.listdir(tmp_path) == ["config.yaml"]


def test_a_host_that_cannot_isolate_programs_is_refused_before_any_episode(command, tmp_path, capsys, programs):
    programs.host_check = lambda: "programs cannot be run isolated on this host: no sandbox"
    assert command() != 0
    assert "programs cannot be run isolated on this host: no sandbox" in capsys.readouterr().err
    assert os.listdir(tmp_path) == ["config.yaml"]


def test_the_host_is_checked_after_the_config(command, capsys, programs):
    programs.host_check = lambda: pytest.fail("the config is refused first")
    assert command("--parallel", "0") != 0


# 2. A run


def test_force_deletes_every_selected_folder_before_any_episode_starts(command, tmp_path, monkeypatch):
    assert command("--seeds", "0,1,2") == 0
    for seed in (0, 1, 2):
        (folder(tmp_path, seed) / "old").write_text("old")
    run_episode, starts = run.run_episode, []

    def spy(cfg, seed, path, tracker):
        starts.append([os.path.exists(folder(tmp_path, s)) for s in (0, 1)])
        return run_episode(cfg, seed, path, tracker)

    monkeypatch.setattr(run, "run_episode", spy)
    assert command("--seeds", "0,1", "--parallel", "1", "--force") == 0
    assert starts == [[False, False], [True, False]]
    assert [os.path.exists(folder(tmp_path, seed) / "old") for seed in (0, 1, 2)] == [False, False, True]
    assert [summary(folder(tmp_path, seed))["status"] for seed in (0, 1)] == ["ok", "ok"]


def test_force_with_no_folder_yet(command, tmp_path):
    assert command("--force") == 0
    assert sorted(os.listdir(tmp_path / "out" / "fast")) == ["seed_0", "seed_1", "seed_2"]


def test_a_finished_episode_is_not_run_again_whatever_the_config_says(command, tmp_path, monkeypatch, capsys):
    assert command("--seeds", "0,1") == 0
    before = [(folder(tmp_path, seed) / name).read_bytes() for seed in (0, 1) for name in ("summary.json", "log.jsonl")]
    monkeypatch.setattr(run, "run_episode", lambda *a: pytest.fail("a finished episode was run again"))
    capsys.readouterr()
    assert command("--seeds", "0,1", message_channels="all", turns={"max_actions": 2}) == 0
    assert before == [(folder(tmp_path, seed) / name).read_bytes()
                      for seed in (0, 1) for name in ("summary.json", "log.jsonl")]
    out = capsys.readouterr().out
    assert "seed 0: already done" in out and "seed 1: already done" in out
    assert "0 of 2 episodes are not ok" in out


def test_a_summary_that_holds_status_ok_and_nothing_else_is_a_finished_episode(command, tmp_path, capsys):
    folder(tmp_path).mkdir(parents=True)
    (folder(tmp_path) / "summary.json").write_text('{"status": "ok"}')
    assert command("--seeds", "0,1") == 0  # 2, step 4: not run again, reported as already done; the run goes on
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "seed 0: already done" and out[1].startswith("seed 1: ok, ")
    assert out[-1] == "0 of 2 episodes are not ok"
    assert os.listdir(folder(tmp_path)) == ["summary.json"] and summary(folder(tmp_path, 1))["status"] == "ok"


@pytest.mark.parametrize("text", ["", "{", "[]", '"ok"', '{"status": "running"}', '{"status": "error"}'])
def test_a_summary_without_status_ok_means_the_episode_is_run(command, tmp_path, text):
    assert command("--seeds", "0") == 0
    done = summary(folder(tmp_path))
    (folder(tmp_path) / "summary.json").write_text(text)
    assert run.read_summary(folder(tmp_path)).get("status") != "ok"
    assert command("--seeds", "0") == 0
    again = summary(folder(tmp_path))
    assert again.pop("wall_s") > 0 and done.pop("wall_s") > 0
    assert again == done
    assert kinds(folder(tmp_path))[0] == "fresh_start"


def test_at_most_parallel_episodes_at_once_started_in_seed_order_sharing_one_tracker(command, monkeypatch, trackers):
    lock, running, most, seen = threading.Lock(), [], [], []

    def fake(cfg, seed, path, tracker):
        with lock:
            running.append(seed)
            most.append(len(running))
            seen.append((seed, tracker))
        time.sleep(0.05)
        with lock:
            running.remove(seed)
        return {"seed": seed, "status": "ok", "days_completed": 1, "welfare": 0.0, "metrics": {"dollars": 0.0}}

    monkeypatch.setattr(run, "run_episode", fake)
    assert command("--seeds", "4,2,7,0,5", "--parallel", "2") == 0
    assert max(most) == 2
    assert sorted(seed for seed, _ in seen) == [0, 2, 4, 5, 7]
    assert {id(tracker) for _, tracker in seen} == {id(trackers[0])} and len(trackers) == 1
    seen.clear()
    assert command("--seeds", "4,2,7,0,5", "--parallel", "1") == 0
    assert [seed for seed, _ in seen] == [4, 2, 7, 0, 5]
    assert max(most[5:]) == 1
    assert len(trackers) == 2 and trackers[1] is not trackers[0]  # each run its own, from $0


def test_the_tracker_has_the_runs_cost_cap(command, trackers):
    assert command("--seeds", "0") == 0 and command("--seeds", "1", "--cost-cap", "3") == 0
    assert command("--seeds", "2", cost_cap_usd=None) == 0
    assert [t.cap for t in trackers] == [20.0, 3.0, None]


def test_episodes_in_parallel_are_those_run_alone(command, tmp_path):
    assert command("--seeds", "0,1,2,3", "--parallel", "4") == 0
    for seed in (3, 1, 0, 2):
        assert command("--seeds", str(seed), "--parallel", "1", out="alone") == 0
        same_episode(folder(tmp_path, seed), folder(tmp_path, seed, "alone"), extra=())


def test_a_failure_of_one_episode_never_stops_the_others(command, tmp_path, monkeypatch, capsys):
    stop_at(monkeypatch, 2, 2, seed=1)
    assert command("--seeds", "0,1,2", "--parallel", "1") == 1
    assert [summary(folder(tmp_path, seed))["status"] for seed in (0, 1, 2)] == ["ok", "error", "ok"]
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("seed 0: ok, ") and out[2].startswith("seed 2: ok, ")
    assert out[1] == "seed 1: error, 1 days completed: RuntimeError: stopped here"
    assert out[-1] == "1 of 3 episodes are not ok: run the same command again (without --force) to resume them"
    go_on(monkeypatch)
    assert command("--seeds", "0,1,2") == 0
    assert kinds(folder(tmp_path, 1)).count("resume") == 1


def test_an_episode_whose_folder_cannot_be_written_stops_no_other(command, tmp_path, monkeypatch, capsys):
    real = run.write_summary

    def write_summary(where, s):
        if s["seed"] == 1:
            raise OSError("read-only file system")
        real(where, s)

    monkeypatch.setattr(run, "write_summary", write_summary)
    assert command("--seeds", "0,1,2", "--parallel", "1") == 1
    assert [summary(folder(tmp_path, seed))["status"] for seed in (0, 2)] == ["ok", "ok"]
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "seed 1: error, 0 days completed: OSError: read-only file system"
    assert out[-1].startswith("1 of 3 episodes are not ok")


def test_the_reports_of_a_run(command, tmp_path, capsys, scripted):
    scripted(price=0.01, known=False)
    assert command("--seeds", "0", **PAID_SEATS) == 0
    s = summary(folder(tmp_path))
    out = capsys.readouterr().out.splitlines()
    calls = s["metrics"]["buyer_calls"] + s["metrics"]["manager_calls"]
    assert s["metrics"]["calls_unknown_cost"] == calls and s["metrics"]["dollars"] == round(0.01 * calls, 6)
    assert out == [f"seed 0: ok, {s['close_day']} days completed, welfare {s['welfare']}, ${s['metrics']['dollars']}",
                   f"spent ${0.01 * calls:.6f} in this run; {calls} calls had an unknown cost (the dollar totals "
                   f"are underestimates)",
                   "0 of 1 episodes are not ok"]


def test_an_interrupt_ends_the_run_at_once_and_leaves_the_episodes_running(command, tmp_path, monkeypatch):
    def exit_now(status):
        raise SystemExit(status)

    stop_at(monkeypatch, 2, 1, KeyboardInterrupt)
    monkeypatch.setattr(os, "_exit", exit_now)
    with pytest.raises(SystemExit) as stopped:
        command("--seeds", "0,1", "--parallel", "1")
    assert stopped.value.code == 130
    left = summary(folder(tmp_path))
    assert (left["status"], left["error"], left["days_completed"]) == ("running", None, 0)
    go_on(monkeypatch)
    assert command("--seeds", "0,1") == 0


# 3. One episode


def test_the_steps_of_an_episode_in_order(command, tmp_path, monkeypatch):
    seen, path = [], folder(tmp_path)
    start, backend, play_day, settle = episode.start, models.backend, episode.play_day, episode.settle

    def look(step):
        files = sorted(os.listdir(path))
        seen.append((step, files, summary(path)["status"], kinds(path) if "log.jsonl" in files else None))

    def spy(step, real):
        def spied(*args):
            if step not in [s for s, *_ in seen]:
                look(step)
            return real(*args)
        return spied

    monkeypatch.setattr(episode, "start", spy("start", start))
    monkeypatch.setattr(models, "backend", spy("backend", backend))
    monkeypatch.setattr(episode, "play_day", spy("day", play_day))
    monkeypatch.setattr(episode, "settle", spy("settle", settle))
    assert command("--seeds", "0") == 0
    assert seen[0] == ("start", ["config.json", "log.jsonl", "summary.json"], "running", [])
    assert seen[1] == ("backend", ["config.json", "log.jsonl", "summary.json"], "running", [])
    assert seen[2] == ("day", ["config.json", "log.jsonl", "summary.json"], "running", ["fresh_start", "functions"])
    step, files, status, log = seen[3]
    assert (step, status) == ("settle", "running") and "checkpoint.pickle" in files and log[-1] != "settled"
    assert kinds(path)[-1] == "settled" and summary(path)["status"] == "ok"
    assert not (path / "checkpoint.pickle").exists()


def test_the_running_summary_is_written_before_anything_else(tmp_path, monkeypatch):
    path = tmp_path / "seed_0"
    monkeypatch.setattr(run, "open_log", lambda *a: pytest.fail("the summary comes first"))
    real = run.write_summary

    def write_summary(where, s):
        assert sorted(os.listdir(path)) in ([], ["summary.json"])
        real(where, s)
        raise Killed

    monkeypatch.setattr(run, "write_summary", write_summary)
    with pytest.raises(Killed):
        run.run_episode(config.resolve(FAST), 0, str(path), models.CostTracker(None))
    s = summary(path)
    assert s == {"name": "fast", "seed": 0, "message_channels": "off", "status": "running", "error": None,
                 "days_completed": 0, "wall_s": 0.0,
                 "models": {"buyers": [{"provider": "mock", "model": None}] * 2,
                            "managers": [{"provider": "mock", "model": None}] * 3,
                            "summarizer": {"provider": "openrouter", "model": "anthropic/claude-sonnet-5.5"}}}


def test_the_summary_of_an_ok_episode(command, tmp_path):
    seats = {"manager_seats": {"0": MOCK | {"model": "named"}}}
    assert command("--seeds", "2", message_channels="all", models={**FAST["models"], **seats,
                                                                   "summarizer": MOCK}) == 0
    s = summary(folder(tmp_path, 2))
    assert set(s) == {"name", "seed", "message_channels", "models", "status", "error", "days_completed",
                      "wall_s"} | SETTLED
    assert (s["name"], s["seed"], s["message_channels"], s["status"], s["error"]) == ("fast", 2, "all", "ok", None)
    assert s["models"] == {"buyers": [{"provider": "mock", "model": None}] * 2,
                           "managers": [{"provider": "mock", "model": "named"}] + [{"provider": "mock",
                                                                                    "model": None}] * 2,
                           "summarizer": {"provider": "mock", "model": None}}
    assert s["days_completed"] == s["close_day"] >= s["last_arrival_day"] >= 2
    assert s["close_reason"] in ("all_done", "all_settled", "due_limit")
    assert [e["contract"] for e in s["settlement"]] and len(s["scores"]) == 5
    assert [(x["role"], x["seat"]) for x in s["scores"]] == [("buyer", 0), ("buyer", 1), ("manager", 0),
                                                             ("manager", 1), ("manager", 2)]
    assert s["scores"][2]["model"] == "named" and s["welfare"] == s["metrics"]["welfare"]
    assert s["money_check"] == 0 and s["wall_s"] > 0
    last = records(folder(tmp_path, 2))[-1]
    assert last["type"] == "settled" and last["status"] == "ok"
    assert {key: last[key] for key in SETTLED} == {key: s[key] for key in SETTLED}


def test_a_seat_with_its_own_spec_gets_its_own_backend(command, tmp_path, scripted):
    seat = PAID | {"model": "other"}
    specs = scripted()
    assert command("--seeds", "0", models={**FAST["models"], "buyer_seats": {1: seat}}) == 0
    assert [spec.get("model") for spec in specs] == [None, "other", None, None, None]
    assert summary(folder(tmp_path))["models"]["buyers"] == [{"provider": "mock", "model": None},
                                                             {"provider": "openai_compatible", "model": "other"}]
    first = records(folder(tmp_path))[0]
    assert [p["model"].get("model") for p in first["participants"]] == [None, "other", None, None, None]


def test_the_log_is_json_lines_each_record_in_the_file_before_the_episode_goes_on(command, tmp_path, monkeypatch):
    path, play, counts = folder(tmp_path), rounds.play, []

    def spy(m, order, ctx):
        text = (path / "log.jsonl").read_text()
        assert text.endswith("\n") and len(text.splitlines()) == m.records
        counts.append(m.records)
        play(m, order, ctx)

    monkeypatch.setattr(rounds, "play", spy)
    assert command("--seeds", "0") == 0
    assert counts == sorted(counts) and len(set(counts)) == len(counts) > 5
    log = records(path)
    assert [r["n"] for r in log] == list(range(1, len(log) + 1))
    assert all(isinstance(r["type"], str) and "day" in r and "round" in r for r in log)
    assert (log[0]["type"], log[0]["name"], log[0]["seed"], log[0]["message_channels"]) == ("fresh_start", "fast", 0,
                                                                                            "off")
    for line in (path / "log.jsonl").read_text().splitlines():
        assert line.isascii() and json.loads(line)


# 4. What stops an episode before its close


def test_the_cost_cap_stops_every_episode_and_a_later_run_resumes_them(command, tmp_path, scripted, trackers, capsys):
    scripted(price=0.02)
    assert command("--seeds", "0,1", "--parallel", "1", "--cost-cap", "1", **PAID_SEATS) == 1
    spent = trackers[0].total
    assert 1 <= spent < 1.5
    error = f"cost cap $1.00 reached (spent ${spent:.2f})"
    first, second = summary(folder(tmp_path, 0)), summary(folder(tmp_path, 1))
    for s in (first, second):
        assert (s["status"], s["error"]) == ("aborted_cost_cap", error) and not SETTLED & set(s)
    assert (first["days_completed"], second["days_completed"]) == (1, 0)
    assert records(folder(tmp_path, 0))[-1] | {"n": 0} == {
        "n": 0, "type": "stopped", "day": 2, "round": records(folder(tmp_path, 0))[-1]["round"],
        "status": "aborted_cost_cap", "error": error, "days_completed": 1}
    stopped = kinds(folder(tmp_path, 1))  # it started after the cap was reached: it stops at its first check
    assert stopped.count("call") == 0 and stopped[-1] == "stopped" and "turn" in stopped
    assert (folder(tmp_path, 0) / "checkpoint.pickle").exists()
    assert not (folder(tmp_path, 1) / "checkpoint.pickle").exists()
    out = capsys.readouterr().out
    assert f"seed 0: aborted_cost_cap, 1 days completed: {error}" in out
    assert "2 of 2 episodes are not ok: run the same command again" in out

    assert command("--seeds", "0,1", "--parallel", "1", "--cost-cap", "100", **PAID_SEATS) == 0
    assert kinds(folder(tmp_path, 0)).count("resume") == 1 and kinds(folder(tmp_path, 1)).count("resume") == 0
    assert command("--seeds", "0,1", "--cost-cap", "100", out="whole", **PAID_SEATS) == 0
    for seed in (0, 1):
        same_episode(folder(tmp_path, seed), folder(tmp_path, seed, "whole"))
    assert 0 < trackers[1].total < trackers[2].total  # the later run started again from $0
    day_1 = sum(r["type"] == "call" and r["day"] == 1 for r in records(folder(tmp_path, 0)))
    assert trackers[1].total == pytest.approx(trackers[2].total - 0.02 * day_1)


def test_episodes_in_parallel_all_stop_at_the_cap(command, tmp_path, scripted):
    scripted(price=0.02)
    assert command("--seeds", "0,1,2", "--parallel", "3", "--cost-cap", "1", **PAID_SEATS) == 1
    assert [summary(folder(tmp_path, seed))["status"] for seed in (0, 1, 2)] == ["aborted_cost_cap"] * 3


def test_mock_calls_never_reach_the_cap(command, tmp_path, trackers):
    assert command("--seeds", "0", "--cost-cap", "0.000001") == 0
    assert trackers[0].total == 0 and summary(folder(tmp_path))["metrics"]["dollars"] > 0.000001


def test_too_many_lost_turns_abort_the_episode(command, tmp_path, scripted):
    scripted(fail=True)
    assert command("--seeds", "0", **PAID_SEATS) == 1
    s, log = summary(folder(tmp_path)), records(folder(tmp_path))
    error = "16 turns lost to failed model calls; the last: HTTP 500 for model 'scripted': no"
    assert (s["status"], s["error"], s["days_completed"]) == ("aborted_lost_turns", error, 1)
    assert not SETTLED & set(s)
    lost = [r for r in log if r["type"] == "lost_turn"]
    assert [r["day"] for r in lost] == [1] * 15 + [2]  # 5 x 3 rounds a day: the 16th aborts at once
    assert log[-2]["type"] == "lost_turn"
    assert {**log[-1], "n": 0} == {"n": 0, "type": "stopped", "day": 2, "round": 1, "status": "aborted_lost_turns",
                                   "error": error, "days_completed": 1}
    assert run.load(str(folder(tmp_path)), 0, config.resolve({**FAST, **PAID_SEATS})).lost_turns == 15

    assert command("--seeds", "0", **PAID_SEATS) == 1  # the count is part of the state: it goes on from 15
    log = records(folder(tmp_path))
    assert [r["day"] for r in log if r["type"] == "lost_turn"] == [1] * 15 + [2]
    assert [r["type"] for r in log].count("resume") == 1 and log[-1]["type"] == "stopped"
    assert summary(folder(tmp_path))["error"] == error


def test_a_backend_that_cannot_be_set_up_ends_the_episode_before_any_day(command, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(episode, "play_day", lambda m, ctx: pytest.fail("no day is played"))
    models_ = {"buyers": MOCK, "managers": config.DEFAULTS["models"]["managers"]}
    assert command("--seeds", "0,1", models=models_) == 1
    for seed in (0, 1):
        s, log = summary(folder(tmp_path, seed)), records(folder(tmp_path, seed))
        assert (s["status"], s["days_completed"]) == ("error", 0) and not SETTLED & set(s)
        assert s["error"].strip().splitlines()[-1].endswith("set OPENROUTER_API_KEY in your environment or in .env")
        assert log == [{"n": 1, "type": "stopped", "day": 0, "round": None, "status": "error", "error": s["error"],
                        "days_completed": 0}]


def test_a_mock_spec_that_names_a_key_variable_needs_it_set(command, tmp_path, monkeypatch):
    monkeypatch.delenv("NO_SUCH_KEY", raising=False)
    assert command("--seeds", "0", models={"buyers": MOCK | {"api_key_env": "NO_SUCH_KEY"}, "managers": MOCK}) == 1
    assert "set NO_SUCH_KEY in your environment or in .env" in summary(folder(tmp_path))["error"]
    monkeypatch.setenv("NO_SUCH_KEY", "set")
    assert command("--seeds", "0", models={"buyers": MOCK | {"api_key_env": "NO_SUCH_KEY"}, "managers": MOCK}) == 0


def test_a_key_variable_can_come_from_the_env_file(command, tmp_path, monkeypatch):
    monkeypatch.delenv("NO_SUCH_KEY", raising=False)
    (tmp_path / ".env").write_text("NO_SUCH_KEY=set\n")
    assert command("--seeds", "0", models={"buyers": MOCK | {"api_key_env": "NO_SUCH_KEY"}, "managers": MOCK}) == 0


def test_a_failed_setup_on_a_fresh_start_after_a_checkpoint_not_used(command, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    stop_at(monkeypatch, 2, 1)
    assert command("--seeds", "0") == 1
    assert command("--seeds", "0", models={"buyers": MOCK, "managers": config.DEFAULTS["models"]["managers"]}) == 1
    assert [(r["type"], r.get("why")) for r in records(folder(tmp_path))] == [("checkpoint_not_used",
                                                                              "another config"), ("stopped", None)]
    assert summary(folder(tmp_path))["days_completed"] == 0


@pytest.mark.parametrize("mode, summarizer, seats, set_up", [
    ("off", PAID | {"model": "s"}, PAID, False),  # models.md, 3: only when message_channels is not off
    ("all", PAID | {"model": "s"}, PAID, True),
    ("managers", PAID | {"model": "s"}, PAID, True),
    ("all", MOCK, PAID, False),  # messaging.md, 5.5: the summarizer's spec is the mock
    ("all", PAID | {"model": "s"}, MOCK, False),  # or every seat is on the mock
])
def test_the_summarizer_is_set_up_only_when_it_will_make_real_calls(command, tmp_path, scripted, mode, summarizer,
                                                                    seats, set_up):
    specs = scripted(price=0.001)
    assert command("--seeds", "1", message_channels=mode, messaging={"recent": 2},
                   models={"buyers": seats, "managers": seats, "summarizer": summarizer}) == 0
    assert [spec.get("model") for spec in specs] == [seats.get("model")] * 5 + ["s"] * set_up
    metrics = summary(folder(tmp_path, 1))["metrics"]
    assert (metrics["summarizer_calls"] > 0) == set_up
    assert (metrics["summaries_written"] > 0) == (mode != "off")


def test_one_seat_on_a_real_model_makes_the_summarizer_real(command, scripted):
    specs = scripted(price=0.001)
    assert command("--seeds", "1", message_channels="all",
                   models={"buyers": MOCK, "managers": MOCK, "manager_seats": {2: PAID},
                           "summarizer": PAID | {"model": "s"}}) == 0
    assert [spec.get("model") for spec in specs] == [None, None, None, None, "scripted", "s"]


def test_an_error_while_playing_stops_the_episode_with_a_stop_record(command, tmp_path, monkeypatch):
    stop_at(monkeypatch, 3, 2)
    assert command("--seeds", "0") == 1
    s, log = summary(folder(tmp_path)), records(folder(tmp_path))
    assert (s["status"], s["days_completed"]) == ("error", 2) and not SETTLED & set(s)
    assert s["error"].startswith("Traceback") and s["error"].strip().endswith("RuntimeError: stopped here")
    assert {**log[-1], "n": 0} == {"n": 0, "type": "stopped", "day": 3, "round": 2, "status": "error",
                                   "error": s["error"], "days_completed": 2}
    assert run.load(str(folder(tmp_path)), 0, config.resolve(FAST)).days_completed == 2


def test_a_program_that_cannot_be_run_isolated_stops_the_episode(command, tmp_path, programs):
    def run_program(code, compute):
        raise RuntimeError("the sandbox cannot be started")

    programs.run_program = run_program
    assert command("--seeds", "0") == 1
    s = summary(folder(tmp_path))
    assert s["status"] == "error" and "RuntimeError: the sandbox cannot be started" in s["error"]
    assert s["days_completed"] == 0 and kinds(folder(tmp_path))[-1] == "stopped"


def test_an_amount_that_is_not_finite_at_the_start_stops_the_episode(command, tmp_path):
    assert command("--seeds", "0", money={"start_amount": 999999, "share_range": [1, 1e303]}) == 1
    s, log = summary(folder(tmp_path)), records(folder(tmp_path))
    assert s["status"] == "error" and "the start balance is not finite" in s["error"]
    assert [r["type"] for r in log] == ["stopped"] and log[0]["error"] == s["error"]


def test_a_failure_while_settling_keeps_the_checkpoint_and_the_next_run_settles_again(command, tmp_path, monkeypatch):
    assert command("--seeds", "0", out="whole") == 0

    def settle(m):
        raise ArithmeticError("the welfare is not finite: inf")

    monkeypatch.setattr(episode, "settle", settle)
    assert command("--seeds", "0") == 1
    s, last = summary(folder(tmp_path)), summary(folder(tmp_path, out="whole"))["close_day"]
    assert (s["status"], s["days_completed"]) == ("error", last) and not SETTLED & set(s)
    assert s["error"].startswith("settlement failure: Traceback")
    assert s["error"].strip().endswith("ArithmeticError: the welfare is not finite: inf")
    assert kinds(folder(tmp_path))[-1] == "stopped" and (folder(tmp_path) / "checkpoint.pickle").exists()
    go_on(monkeypatch)
    monkeypatch.setattr(episode, "play_day", lambda m, ctx: pytest.fail("no other day is played"))
    assert command("--seeds", "0") == 0
    assert kinds(folder(tmp_path)).count("resume") == 1 and kinds(folder(tmp_path))[-1] == "settled"
    same_episode(folder(tmp_path), folder(tmp_path, out="whole"))


def test_a_checkpoint_that_cannot_be_deleted_leaves_the_settled_episode_ok(command, tmp_path, monkeypatch, capsys):
    remove = os.remove

    def refused(path):
        raise PermissionError(f"operation not permitted: {path}")

    monkeypatch.setattr(os, "remove", refused)
    assert command("--seeds", "0") == 0  # 3, step 6: the checkpoint may be deleted, after the ok summary
    monkeypatch.setattr(os, "remove", remove)
    s, path = summary(folder(tmp_path)), folder(tmp_path)
    assert (s["status"], s["error"]) == ("ok", None) and SETTLED <= set(s)
    assert kinds(path)[-1] == "settled" and "stopped" not in kinds(path)
    assert (path / "checkpoint.pickle").exists()
    assert capsys.readouterr().out.splitlines()[0].startswith("seed 0: ok, ")
    assert command("--seeds", "0", out="whole") == 0
    same_episode(path, folder(tmp_path, out="whole"), extra=())


def test_an_ok_summary_that_cannot_be_written_leaves_no_settled_content(command, tmp_path, monkeypatch):
    assert command("--seeds", "0", out="whole") == 0
    real = run.write_summary

    def write_summary(where, s):
        if s["status"] == "ok":
            raise OSError("the disk is full")
        real(where, s)

    monkeypatch.setattr(run, "write_summary", write_summary)
    assert command("--seeds", "0") == 1
    s = summary(folder(tmp_path))  # 3.1: status, error text, days completed, nothing of the settled content
    assert set(s) == {"name", "seed", "message_channels", "models", "status", "error", "days_completed", "wall_s"}
    assert s["status"] == "error" and s["error"].startswith("settlement failure: Traceback")
    assert s["error"].strip().endswith("OSError: the disk is full")
    assert kinds(folder(tmp_path))[-2:] == ["settled", "stopped"]
    assert (folder(tmp_path) / "checkpoint.pickle").exists()
    monkeypatch.setattr(run, "write_summary", real)
    assert command("--seeds", "0") == 0  # the next run settles again
    assert kinds(folder(tmp_path)).count("settled") == 1 and "stopped" not in kinds(folder(tmp_path))
    same_episode(folder(tmp_path), folder(tmp_path, out="whole"))


# 5. Checkpoints and resuming


def test_a_checkpoint_after_every_completed_day_and_none_inside_a_day(command, tmp_path, monkeypatch):
    path, save, saved = folder(tmp_path), run.save, []

    def spy(m, where):
        save(m, where)
        lines = len((path / "log.jsonl").read_text().splitlines())
        layout, text, held, defined = pickle.loads((path / "checkpoint.pickle").read_bytes())
        assert defined == turns.role_functions(m.cfg)
        saved.append((m.days_completed, m.day, held.days_completed, held.records == m.records == lines,
                      held.cfg, held.log, json.loads(text) == m.cfg and layout == run.LAYOUT, m.deferred))

    monkeypatch.setattr(run, "save", spy)
    assert command("--seeds", "0") == 0
    days = summary(path)["close_day"]
    assert saved == [(d, d, d, True, None, None, True, []) for d in range(1, days + 1)]
    assert sorted(os.listdir(path)) == ["config.json", "log.jsonl", "summary.json"]


def test_the_checkpoint_restores_the_market_exactly(tmp_path, command, monkeypatch):
    stop_at(monkeypatch, 3, 1)
    kept, play_day = [], episode.play_day

    def keep(m, ctx):
        play_day(m, ctx)
        kept.append(pickle.loads(pickle.dumps(dataclasses.replace(m, cfg=None))))

    monkeypatch.setattr(episode, "play_day", keep)
    assert command("--seeds", "1", message_channels="all") == 1
    cfg = config.resolve({**FAST, "message_channels": "all"}, cost_cap=3.0, seeds=[9, 1], parallel=7, out="x")
    m = run.load(str(folder(tmp_path, 1)), 1, cfg)
    assert m.cfg is cfg and m.log is None and m.days_completed == 2 and m.messages and m.contracts
    m.cfg = None
    assert m == kept[-1]


def test_saving_is_atomic(tmp_path, command, monkeypatch):
    replace, calls = os.replace, []

    def failing(src, dst):
        calls.append(os.path.basename(dst))
        if calls.count("checkpoint.pickle") == 2 and dst.endswith("checkpoint.pickle"):
            raise OSError("the disk is full")
        replace(src, dst)

    monkeypatch.setattr(os, "replace", failing)
    assert command("--seeds", "0") == 1
    s = summary(folder(tmp_path))
    assert s["status"] == "error" and "the disk is full" in s["error"] and s["days_completed"] == 1
    assert run.load(str(folder(tmp_path)), 0, config.resolve(FAST)).days_completed == 1
    assert set(calls) == {"summary.json", "checkpoint.pickle"}  # both are replaced, never written in place
    go_on(monkeypatch)
    assert command("--seeds", "0") == 0 and command("--seeds", "0", out="whole") == 0
    same_episode(folder(tmp_path), folder(tmp_path, out="whole"))


def test_the_summary_is_replaced_atomically(tmp_path, monkeypatch):
    run.write_summary(str(tmp_path), {"status": "running"})
    monkeypatch.setattr(os, "replace", lambda src, dst: (_ for _ in ()).throw(Killed()))
    with pytest.raises(Killed):
        run.write_summary(str(tmp_path), {"status": "ok"})
    assert run.read_summary(str(tmp_path)) == {"status": "running"}


@pytest.mark.parametrize("day, rnd, how", [(1, 1, Killed), (1, 3, RuntimeError), (2, 1, Killed), (2, 3, RuntimeError),
                                           (3, 2, Killed), ("last", 3, RuntimeError)])
def test_a_stopped_episode_resumes_from_its_last_completed_day(command, tmp_path, monkeypatch, day, rnd, how):
    assert command("--seeds", "0", message_channels="all", out="whole") == 0
    whole = folder(tmp_path, out="whole")
    day = summary(whole)["close_day"] if day == "last" else day
    stop_at(monkeypatch, day, rnd, how)
    if how is Killed:
        with pytest.raises(Killed):
            command("--seeds", "0", message_channels="all")
        assert summary(folder(tmp_path))["status"] == "running"
    else:
        assert command("--seeds", "0", message_channels="all") == 1
    go_on(monkeypatch)
    spied, play_day = [], episode.play_day
    monkeypatch.setattr(episode, "play_day", lambda m, ctx: (spied.append((m.day + 1, summary(folder(tmp_path)))),
                                                             play_day(m, ctx)))
    assert command("--seeds", "0", message_channels="all") == 0
    first_day, running = spied[0]
    assert first_day == day  # it continues with day (days completed + 1), played from its start
    assert (running["status"], running["days_completed"]) == ("running", day - 1)  # 3, step 1
    log = records(folder(tmp_path))
    resume = [r for r in log if r["type"] == "resume"]
    assert [(r["days_completed"], r["day"], r["round"]) for r in resume] == [(day - 1, day - 1, 3)] * (day > 1)
    assert "stopped" not in [r["type"] for r in log]
    assert [r["n"] for r in log] == list(range(1, len(log) + 1))
    assert [r["type"] for r in log].count("system_prompt") == 5  # a prompt already logged is not logged again
    # nor are the function definitions, which the fresh start logged once (6.3 D)
    definitions = turns.role_functions(config.resolve({**FAST, "message_channels": "all"}))
    assert [r for r in log if r["type"] == "functions"] == [
        {"n": 2, "type": "functions", "day": 0, "round": None, "functions": definitions}]
    calls = [r for r in log if r["type"] == "call"]
    assert {tuple(r["request"]["functions"]) for r in calls} == {("compute", "act"), ("act",)}
    role = {p["id"]: p["role"] for p in log[0]["participants"]}
    for r in calls:  # each call names its functions, and continues the call before it of the same turn
        prior, full = r["request"]["prior"], rounds.request(log, r)
        assert full["functions"] == definitions[role[r["participant"]]][-len(r["request"]["functions"]):]
        assert [x["role"] for x in full["messages"][:2]] == ["system", "user"]
        assert (prior is None) == (r["step"] == 1 and r["call"] == "decide")
        assert prior is None or [log[prior - 1][k] for k in ("type", "participant", "day", "round")] == [
            "call", r["participant"], r["day"], r["round"]]
    same_episode(folder(tmp_path), whole)  # with every request rebuilt: the places differ after a resume record


def test_several_stops_in_one_episode(command, tmp_path, monkeypatch):
    assert command("--seeds", "3", out="whole") == 0
    for day, rnd in ((2, 2), (2, 3), (3, 1)):
        stop_at(monkeypatch, day, rnd)
        assert command("--seeds", "3") == 1
        go_on(monkeypatch)
    assert command("--seeds", "3") == 0  # the resume record of a day that was abandoned again is cut with it
    assert [r["days_completed"] for r in records(folder(tmp_path, 3)) if r["type"] == "resume"] == [1, 2]
    same_episode(folder(tmp_path, 3), folder(tmp_path, 3, "whole"))


def test_a_half_written_last_record_is_discarded(command, tmp_path, monkeypatch):
    assert command("--seeds", "0", out="whole") == 0
    stop_at(monkeypatch, 3, 1, Killed)
    with pytest.raises(Killed):
        command("--seeds", "0")
    go_on(monkeypatch)
    with open(folder(tmp_path) / "log.jsonl", "a") as f:
        f.write('{"n": 9999, "type": "mon')
    assert command("--seeds", "0") == 0
    same_episode(folder(tmp_path), folder(tmp_path, out="whole"))


def cut_short(text):
    return "".join(text.splitlines(True)[:20])


def damaged(text):
    lines = text.splitlines(True)
    return "".join(lines[:9] + [lines[9][:-10] + "\n"] + lines[10:])


def half_written(text):
    lines = text.splitlines(True)
    position = [i for i, line in enumerate(lines) if json.loads(line)["type"] == "day"][2]
    return "".join(lines[:position])[:-1]


def renumbered(text):
    lines = text.splitlines(True)
    return "".join(lines[:4] + lines[5:])


@pytest.mark.parametrize("change", [None, cut_short, damaged, half_written, renumbered, lambda text: ""])
def test_a_log_that_does_not_match_its_checkpoint(command, tmp_path, monkeypatch, change):
    stop_at(monkeypatch, 3, 2)
    assert command("--seeds", "0") == 1
    go_on(monkeypatch)
    path = folder(tmp_path)
    log, checkpoint = path / "log.jsonl", path / "checkpoint.pickle"
    if change is None:
        log.unlink()
    else:
        log.write_text(change(log.read_text()))
    before = log.read_bytes() if change else None, checkpoint.read_bytes()
    for _ in range(2):  # a later run fails the same way
        assert command("--seeds", "0") == 1
        s = summary(path)
        assert (s["status"], s["days_completed"]) == ("error", 2) and not SETTLED & set(s)
        assert "the log does not match the checkpoint" in s["error"]
        assert (log.read_bytes() if log.exists() else None, checkpoint.read_bytes()) == before  # no stop record
    assert command("--seeds", "0", "--force") == 0  # until --force starts the episode afresh
    assert kinds(path)[0] == "fresh_start" and "resume" not in kinds(path)


def test_open_log_cuts_the_log_back_to_the_position(tmp_path):
    with run.open_log(str(tmp_path)) as log:
        for n in (1, 2, 3):
            run._write(log, {"n": n, "type": "x"})
        log.write('{"n": 4, "ty')
    assert run.open_log(str(tmp_path), 4) is None
    assert (tmp_path / "log.jsonl").read_text().count("\n") == 3  # left as it is
    with run.open_log(str(tmp_path), 2) as log:
        run._write(log, {"n": 3, "type": "resume"})
    assert [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()] == [
        {"n": 1, "type": "x"}, {"n": 2, "type": "x"}, {"n": 3, "type": "resume"}]
    with run.open_log(str(tmp_path)):
        pass
    assert (tmp_path / "log.jsonl").read_text() == ""  # begun afresh


@pytest.mark.parametrize("why", ["unreadable", "another config", "another seed"])
def test_a_checkpoint_that_is_not_used_starts_the_episode_from_the_beginning(command, tmp_path, monkeypatch, why):
    stop_at(monkeypatch, 3, 2)
    assert command("--seeds", "0") == 1
    go_on(monkeypatch)
    seed, keys = 0, {}
    if why == "unreadable":
        (folder(tmp_path) / "checkpoint.pickle").write_bytes(b"not a checkpoint")
    elif why == "another config":
        keys = {"turns": {"max_actions": 5}}
    else:
        seed = 5
        shutil.copytree(folder(tmp_path), folder(tmp_path, 5))
    spied, start = [], episode.start
    monkeypatch.setattr(episode, "start", lambda *a: (spied.append(summary(folder(tmp_path, seed))), start(*a))[1])
    assert command("--seeds", str(seed), **keys) == 0
    assert (spied[0]["status"], spied[0]["days_completed"]) == ("running", 0)  # 3, step 1: else 0
    log = records(folder(tmp_path, seed))
    assert log[0] == {"n": 1, "type": "checkpoint_not_used", "day": 0, "round": None, "why": why}
    assert log[1]["type"] == "fresh_start" and "resume" not in kinds(folder(tmp_path, seed))
    assert command("--seeds", str(seed), out="whole", **keys) == 0
    same_episode(folder(tmp_path, seed), folder(tmp_path, seed, "whole"), extra=("checkpoint_not_used",))


def test_a_checkpoint_that_is_not_used_goes_with_the_log_it_counted_on(command, tmp_path, monkeypatch):
    stop_at(monkeypatch, 3, 2)
    assert command("--seeds", "0") == 1
    assert (folder(tmp_path) / "checkpoint.pickle").exists()
    stop_at(monkeypatch, 1, 2, Killed)
    with pytest.raises(Killed):
        command("--seeds", "0", turns={"max_actions": 5})  # another config begins the log afresh, and is killed
    assert kinds(folder(tmp_path))[0] == "checkpoint_not_used"
    assert not (folder(tmp_path) / "checkpoint.pickle").exists()
    go_on(monkeypatch)
    assert command("--seeds", "0") == 0  # the first config again: its old checkpoint has no log any more
    assert kinds(folder(tmp_path))[0] == "fresh_start" and "resume" not in kinds(folder(tmp_path))


def test_a_checkpoint_that_is_not_used_is_removed_before_its_log_is_begun_afresh(command, tmp_path, monkeypatch):
    # a failure between the two must never leave a checkpoint whose log was begun afresh
    stop_at(monkeypatch, 3, 2)
    assert command("--seeds", "0") == 1
    go_on(monkeypatch)
    before, open_log = (folder(tmp_path) / "log.jsonl").read_bytes(), run.open_log

    def broken(*args):
        raise OSError("the log cannot be opened")

    monkeypatch.setattr(run, "open_log", broken)
    assert command("--seeds", "0", turns={"max_actions": 5}) != 0  # another config: the checkpoint is not used
    assert not (folder(tmp_path) / "checkpoint.pickle").exists()
    assert (folder(tmp_path) / "log.jsonl").read_bytes() == before
    monkeypatch.setattr(run, "open_log", open_log)
    assert command("--seeds", "0") == 0  # the first config again starts from the beginning
    assert kinds(folder(tmp_path))[0] == "fresh_start" and "resume" not in kinds(folder(tmp_path))


def test_a_checkpoint_that_is_not_used_and_cannot_be_removed_ends_the_episode_with_an_error(
        command, tmp_path, monkeypatch):
    # running.md 4.3: anything else that stops an episode gives status error, in its summary
    stop_at(monkeypatch, 3, 2)
    assert command("--seeds", "0") == 1
    go_on(monkeypatch)
    before, remove = (folder(tmp_path) / "log.jsonl").read_bytes(), os.remove

    def refuse(path, *args, **kwargs):
        if str(path).endswith("checkpoint.pickle"):
            raise OSError("not permitted")
        return remove(path, *args, **kwargs)

    monkeypatch.setattr(os, "remove", refuse)
    assert command("--seeds", "0", turns={"max_actions": 5}) != 0  # another config: the checkpoint is not used
    ended = summary(folder(tmp_path))
    assert ended["status"] == "error" and "cannot be removed" in ended["error"]
    assert (folder(tmp_path) / "log.jsonl").read_bytes() == before


def checkpoint(tmp_path, raw, seed=0):
    """A checkpoint written for a config, as a folder."""
    cfg = config.resolve(raw)
    m = episode.start(seed, cfg)
    m.days_completed = 3
    run.save(m, str(tmp_path))
    return str(tmp_path)


def test_load_without_a_checkpoint_or_with_one_that_cannot_be_read(tmp_path):
    cfg = config.resolve(FAST)
    assert run.load(str(tmp_path), 0, cfg) is None
    m, defined = episode.start(0, cfg), turns.role_functions(cfg)
    legacy = defined["buyer"][:1] + [copy.deepcopy(defined["buyer"][1])]
    legacy[1]["function"]["parameters"]["properties"]["actions"]["items"] = {"type": "object"}
    for data in (b"", b"junk", pickle.dumps("text"), pickle.dumps((run.LAYOUT, "{}", m, defined)),
                 pickle.dumps((run.LAYOUT, "{", m, defined)), pickle.dumps((json.dumps(cfg), m, defined)),
                 pickle.dumps((run.LAYOUT, json.dumps(cfg), "no market", defined)),
                 pickle.dumps((run.LAYOUT, json.dumps({"seeds": [0]}), m, defined)),
                 pickle.dumps(({**run.LAYOUT, "Task": ["id"]}, json.dumps(cfg), m, defined)),  # state held otherwise
                 pickle.dumps((run.LAYOUT, json.dumps(cfg), m)),  # a checkpoint from before act named the actions
                 pickle.dumps((run.LAYOUT, json.dumps(cfg), m, legacy)),  # one list for both roles, other definitions
                 pickle.dumps((run.LAYOUT, json.dumps(cfg), m, {**defined, "manager": defined["buyer"]}))):
        (tmp_path / "checkpoint.pickle").write_bytes(data)
        assert run.load(str(tmp_path), 0, cfg) == "unreadable"
    (tmp_path / "checkpoint.pickle").write_bytes(pickle.dumps((run.LAYOUT, json.dumps(cfg), m, defined)))
    assert run.load(str(tmp_path), 0, cfg) == m
    assert run.load(checkpoint(tmp_path, FAST), 0, cfg).days_completed == 3
    assert run.load(checkpoint(tmp_path, FAST), 1, cfg) == "another seed"
    assert set(run.LAYOUT) >= {"Market", "Participant", "Task", "Post", "Bid", "Contract", "Result", "Delivery",
                               "Dispute", "Payment", "Message", "Memory"}
    assert run.LAYOUT["Bid"] == ["id", "post", "manager", "price", "threshold", "status"]


SPEC = {"provider": "openrouter", "model": "m", "price_in_per_m": 1, "price_out_per_m": 2}


@pytest.mark.parametrize("written, now", [
    ({}, {"seeds": [5], "parallel_episodes": 1, "cost_cap_usd": None, "output_dir": "elsewhere", "name": "other"}),
    ({}, {"turns": {"memory_chars": 2000, "max_actions": 6}, "time": {"rounds_per_day": 3}}),  # defaults, any order
    ({}, {"message_channels": False}),
    ({"models": {"buyers": SPEC}}, {"models": {"buyers": SPEC, "buyer_seats": {0: SPEC, "1": SPEC}}}),
    ({"models": {"buyers": MOCK, "buyer_seats": {0: SPEC, 1: SPEC}}},
     {"models": {"buyers": SPEC, "buyer_seats": {"1": SPEC}}, "participants": {"managers": 3, "buyers": 2}}),
    ({"models": {"managers": SPEC}}, {"models": {"managers": SPEC | {"temperature": 0.8, "max_tokens": 64000,
                                                                     "extra_body": {}}}}),
    ({"models": {"managers": SPEC}, "llm": {"temperature": None, "max_tokens": 9}},
     {"models": {"managers": SPEC | {"temperature": None, "max_tokens": 9}},
      "llm": {"max_tokens": 9, "temperature": None}}),
    ({"models": {"summarizer": SPEC}}, {"models": {"summarizer": SPEC | {"temperature": None, "max_tokens": 64000,
                                                                         "extra_body": {}}}}),
    ({"models": {"managers": SPEC}}, {"models": {"managers": SPEC | {"base_url": "https://openrouter.ai/api/v1",
                                                                     "api_key_env": "OPENROUTER_API_KEY"}}}),
    ({"models": {"managers": MOCK}}, {"models": {"managers": MOCK | {"api_key_env": None}}}),
    ({"models": {"buyers": {"provider": "mock"}}},  # running.md, 7.1: the mock's missing price counts as 0
     {"models": {"buyers": {"provider": "mock", "price_in_per_m": 0, "price_out_per_m": 0.0}}}),
    ({"models": {"managers": {"provider": "mock", "price_in_per_m": 0.15}}},
     {"models": {"managers": {"provider": "mock", "price_in_per_m": 0.15, "price_out_per_m": 0}}}),
    ({"credits_per_usd": 1, "models": {"buyers": SPEC | {"extra_body": {"a": 1, "b": [{"c": True}, 2.0]}}}},
     {"credits_per_usd": 1.0, "models": {"buyers": SPEC | {"extra_body": {"b": [{"c": True}, 2], "a": 1.0}}}}),
])
def test_a_checkpoint_is_used_for_the_same_config_apart_from_the_run_settings(tmp_path, written, now):
    assert isinstance(run.load(checkpoint(tmp_path, written), 0, config.resolve(now)), state.Market)
    assert isinstance(run.load(checkpoint(tmp_path, now), 0, config.resolve(written)), state.Market)


@pytest.mark.parametrize("written, now", [
    ({}, {"tests": {"decimals": 2}}),  # a market parameter
    ({}, {"message_channels": "all"}),
    ({}, {"llm": {"timeout_s": 60}}),
    ({}, {"llm": {"temperature": 0.5}}),  # the temperature of every agent spec without its own
    ({}, {"credits_per_usd": 2}),
    ({}, {"participants": {"buyers": 3}}),
    ({}, {"models": {"buyers": SPEC}}),
    ({}, {"models": {"summarizer": SPEC}}),
    ({}, {"models": {"manager_seats": {2: SPEC}}}),
    ({"models": {"buyers": SPEC}}, {"models": {"buyers": SPEC | {"temperature": None}}}),
    ({"models": {"buyers": SPEC}}, {"models": {"buyers": SPEC | {"extra_body": {"a": 1}}}}),
    ({"models": {"buyers": SPEC}}, {"models": {"buyers": SPEC | {"api_key_env": None}}}),  # no key sent
    ({"models": {"buyers": SPEC}}, {"models": {"buyers": SPEC | {"base_url": "http://localhost:1/v1"}}}),
    ({"models": {"summarizer": SPEC}}, {"models": {"summarizer": SPEC | {"temperature": 0.8}}}),
    ({"models": {"buyers": SPEC | {"extra_body": {"stream": False}}}},  # by value: false is not the number 0
     {"models": {"buyers": SPEC | {"extra_body": {"stream": 0}}}}),
    ({"models": {"summarizer": SPEC | {"extra_body": {"a": [{"b": True}]}}}},  # nor true the number 1
     {"models": {"summarizer": SPEC | {"extra_body": {"a": [{"b": 1}]}}}}),
    ({"models": {"buyers": SPEC | {"extra_body": {"a": [1, 2]}}}},
     {"models": {"buyers": SPEC | {"extra_body": {"a": [2, 1]}}}}),
    ({"models": {"buyers": SPEC | {"extra_body": {"a": [1]}}}},
     {"models": {"buyers": SPEC | {"extra_body": {"a": {}}}}}),
    ({"models": {"buyers": {"provider": "mock"}}}, {"models": {"buyers": {"provider": "mock", "price_in_per_m": 1}}}),
    # models.md, 4.9: only the mock's missing price is 0; another spec's is rule 3, a price of 0 rule 2
    ({"models": {"managers": {"provider": "ollama", "model": "m"}}},
     {"models": {"managers": {"provider": "ollama", "model": "m", "price_in_per_m": 0, "price_out_per_m": 0}}}),
    ({"models": {"summarizer": {"provider": "openrouter", "model": "m"}}},
     {"models": {"summarizer": {"provider": "openrouter", "model": "m", "price_in_per_m": 0, "price_out_per_m": 0}}}),
])
def test_a_checkpoint_written_for_another_config_is_not_used(tmp_path, written, now):
    assert run.load(checkpoint(tmp_path, written), 0, config.resolve(now)) == "another config"
    assert run.load(checkpoint(tmp_path, now), 0, config.resolve(written)) == "another config"


def test_a_checkpoint_holds_a_config_nested_deeper_than_a_pickle_goes(tmp_path):
    body = {}
    for _ in range(5000):
        body = {"deeper": body}
    raw = {"models": {"buyers": MOCK | {"extra_body": body}}}
    m = run.load(checkpoint(tmp_path, raw), 0, config.resolve(raw))
    assert m.days_completed == 3 and m.cfg["models"]["buyers"]["extra_body"] == body
    assert run.load(str(tmp_path), 0, config.resolve({})) == "another config"


# 6. The outputs of an episode


def test_the_wall_time_is_summed_over_the_runs_of_an_episode(command, tmp_path, monkeypatch):
    stop_at(monkeypatch, 3, 2)
    assert command("--seeds", "0") == 1
    first = summary(folder(tmp_path))["wall_s"]
    go_on(monkeypatch)
    spied, play_day = [], episode.play_day
    monkeypatch.setattr(episode, "play_day", lambda m, ctx: (spied.append(summary(folder(tmp_path))["wall_s"]),
                                                             play_day(m, ctx)))
    assert command("--seeds", "0") == 0
    assert 0 < first == spied[0] < summary(folder(tmp_path))["wall_s"]


def test_an_episode_that_is_not_ok_has_no_settled_content(command, tmp_path, monkeypatch):
    stop_at(monkeypatch, 2, 2)
    assert command("--seeds", "0") == 1
    s = summary(folder(tmp_path))
    assert set(s) == {"name", "seed", "message_channels", "models", "status", "error", "days_completed", "wall_s"}


def test_the_resolved_config_is_written_at_every_start_and_resume(command, tmp_path, monkeypatch):
    stop_at(monkeypatch, 2, 2)
    assert command("--seeds", "0", "--cost-cap", "5") == 1
    assert json.loads((folder(tmp_path) / "config.json").read_text())["cost_cap_usd"] == 5
    go_on(monkeypatch)
    assert command("--seeds", "0", "--cost-cap", "6") == 0
    resolved = json.loads((folder(tmp_path) / "config.json").read_text())
    assert (resolved["cost_cap_usd"], resolved["seed"], resolved["version"]) == (6, 0, __version__)
    assert kinds(folder(tmp_path)).count("resume") == 1  # the run settings apart, the config is the same


def test_the_source_is_ascii():
    for name in ("run.py", "__main__.py"):
        with open(os.path.join(os.path.dirname(run.__file__), name), "rb") as f:
            assert f.read().isascii()
    for name in ("test_run.py", "test_free_episodes.py"):
        with open(os.path.join(os.path.dirname(__file__), name), "rb") as f:
            assert f.read().isascii()
