"""Running episodes (spec/running.md, 1 to 6): the command, a run over its seeds, one episode from its beginning
or its checkpoint to its summary, and the episode's files.

An episode's folder holds config.json (6.1), log.jsonl (6.2, 6.3), summary.json (6.4) and, while the episode is
unfinished, checkpoint.pickle (5).
"""

import argparse
import dataclasses
import functools
import json
import os
import pickle
import shutil
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import __version__, config, episode, models, rounds, sandbox, state, turns
from .fmt import F

RUN_SETTINGS = ("seeds", "parallel_episodes", "cost_cap_usd", "output_dir", "name")  # config.md, 3.1


def _replace(path, data):
    """Write a file atomically: a reader sees the previous version or the new one, never a partial one (5, 6.4)."""
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)


# 6.4 The summary


def write_summary(folder, summary):
    """Replace the episode's summary (6.4)."""
    _replace(os.path.join(folder, "summary.json"), json.dumps(summary, indent=1).encode("ascii"))


def read_summary(folder):
    """The episode's summary; {} when there is none that can be read as a JSON object (2, step 4)."""
    try:
        with open(os.path.join(folder, "summary.json"), encoding="ascii") as f:
            summary = json.load(f)
    except (OSError, ValueError, RecursionError):
        return {}
    return summary if isinstance(summary, dict) else {}


# 5. Checkpoints and resuming


LAYOUT = {name: [field.name for field in dataclasses.fields(kind)]  # how this version holds the state
          for name, kind in vars(state).items() if dataclasses.is_dataclass(kind)}


def save(m, folder):
    """The checkpoint of a completed day (5): how the state is held, the config it is written for as JSON text,
    the market without its config, and the function definitions its calls are offered (the texts a resume must
    send unchanged), pickled together and replaced atomically."""
    _replace(os.path.join(folder, "checkpoint.pickle"),
             pickle.dumps((LAYOUT, json.dumps(m.cfg), dataclasses.replace(m, cfg=None), turns.role_functions(m.cfg))))


def _compared(cfg):
    """What two configs are compared on for a resume (5): everything but the run settings, the models as each
    seat's and the summarizer's calls see them (models.resolved)."""
    part = {key: value for key, value in cfg.items() if key not in RUN_SETTINGS}
    part["models"] = {role: [models.resolved(spec) for spec in specs]
                      for role, specs in config.seat_specs(cfg).items()}
    part["models"]["summarizer"] = models.resolved(cfg["models"]["summarizer"])
    return part


def _same(a, b):
    """Two values of a config are the same value (5), at any depth: as ==, but true and false are no numbers."""
    pairs = [(a, b)]
    while pairs:
        a, b = pairs.pop()
        if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
            pairs += [(a[key], b[key]) for key in a]
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            pairs += zip(a, b)
        elif isinstance(a, (dict, list)) or isinstance(a, bool) != isinstance(b, bool) or a != b:
            return False
    return True


def load(folder, seed, cfg):
    """The market of the episode's checkpoint, restored under this run's config (5). None when the episode has
    no checkpoint; why it is not used when it cannot be read (one that holds the state or offers the functions
    differently included) or was written for another seed or config."""
    path = os.path.join(folder, "checkpoint.pickle")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            layout, text, m, defined = pickle.load(f)
        same = _same(_compared(json.loads(text)), _compared(cfg))
        other_seed = m.seed != seed
    except Exception:
        return "unreadable"
    if layout != LAYOUT:
        return "unreadable"
    if other_seed or not same:
        return "another seed" if other_seed else "another config"
    if defined != turns.role_functions(cfg):  # written by a version that offers the functions differently, such
        return "unreadable"  # as one from before act named the actions (SPEC_QUESTIONS Q117)
    m.cfg = cfg
    return m


# 6.2 The log


def open_log(folder, position=None):
    """The episode's log, open for appending (6.2): begun afresh, or, on a resume, cut back to its first
    `position` records; None when it does not hold them all, complete and readable (5, step 2)."""
    path = os.path.join(folder, "log.jsonl")
    if position is None:
        return open(path, "w", encoding="ascii")
    end = 0
    try:
        with open(path, "rb") as f:
            for n in range(1, position + 1):
                line = f.readline()
                if not line.endswith(b"\n") or json.loads(line)["n"] != n:
                    return None
                end += len(line)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return None
    log = open(path, "a", encoding="ascii")
    log.truncate(end)
    return log


def _write(log, record):
    """One record, in the log file before the episode goes on past it (6.2)."""
    log.write(json.dumps(record) + "\n")
    log.flush()


# 3. One episode


def run_episode(cfg, seed, folder, tracker):
    """One episode (3), from its beginning or from its checkpoint (5) until it is settled or stopped (4);
    returns its summary (6.4)."""
    began = time.monotonic()
    os.makedirs(folder, exist_ok=True)
    m = load(folder, seed, cfg)
    resumed = isinstance(m, state.Market)
    days = m.days_completed if resumed else 0
    earlier = read_summary(folder).get("wall_s", 0.0) if resumed else 0.0
    specs = config.seat_specs(cfg)
    summarizer = cfg["models"]["summarizer"]
    summary = {"name": cfg["name"], "seed": seed, "message_channels": cfg["message_channels"],
               "models": {"buyers": [_named(spec) for spec in specs["buyer"]],
                          "managers": [_named(spec) for spec in specs["manager"]],
                          "summarizer": _named(summarizer)},
               "status": "running", "error": None, "days_completed": days, "wall_s": earlier}

    def end(status, error=None, **settled):  # 3.1: the settled content is in the summary of an ok episode only
        ended = {**summary, "status": status, "error": error, "days_completed": days,
                 "wall_s": earlier + time.monotonic() - began, **settled}
        write_summary(folder, ended)
        return ended

    write_summary(folder, summary)  # step 1
    with open(os.path.join(folder, "config.json"), "w", encoding="ascii") as f:  # step 2; 6.1
        json.dump({**cfg, "seed": seed, "version": __version__}, f, indent=1)
    if m and not resumed:  # 5: a checkpoint that is not used goes before the log it counted on is begun afresh
        try:
            os.remove(os.path.join(folder, "checkpoint.pickle"))
        except OSError as e:
            return end("error", f"the checkpoint that is not used ({m}) cannot be removed: {e}")
    log = open_log(folder, m.records if resumed else None)  # step 3
    if log is None:  # 5, step 2
        return end("error", f"the log does not match the checkpoint: it does not hold the checkpoint's "
                            f"{m.records} records, complete and readable (start afresh with --force)")
    settling = False
    backends = {}
    with log:
        try:
            if resumed:
                m.log = functools.partial(_write, log)
                state.emit(m, "resume", days_completed=days)
            else:  # an empty market holds the log until the episode's own is set up
                unused, m = m, state.Market(seed, cfg, log=functools.partial(_write, log))
                if unused:
                    state.emit(m, "checkpoint_not_used", why=unused)
                begun, m = m, episode.start(seed, cfg)
                m.log, m.records = begun.log, begun.records
            people = m.participants.values()  # step 4; models.md, 3, "Setting up"
            backends = {p.id: models.backend(specs[p.role][p.seat], cfg["llm"], tracker) for p in people}
            real = (cfg["message_channels"] != "off" and summarizer["provider"] != "mock"
                    and any(spec["provider"] != "mock" for spec in specs["buyer"] + specs["manager"]))
            summarizer = models.backend(summarizer, cfg["llm"], tracker) if real else None  # messaging.md, 5.5
            if not resumed:
                episode.fresh_start(m)
            with ThreadPoolExecutor(len(backends)) as pool:  # step 5
                ctx = rounds.Ctx(backends, summarizer, tracker, sandbox.run_program, pool.map)
                while m.close_day is None:
                    episode.play_day(m, ctx)
                    save(m, folder)
                    days = m.days_completed
            settling = True  # step 6
            episode.close(m)
            settled = {"last_arrival_day": m.last_arrival, "close_day": m.close_day,
                       "close_reason": m.close_reason, **episode.settle(m)}
            state.emit(m, "settled", status="ok", **settled)
            ended = end("ok", **settled)
            try:  # the checkpoint may be deleted now; the episode is ok whether or not it can be
                os.remove(os.path.join(folder, "checkpoint.pickle"))
            except OSError:
                pass
            return ended
        except models.CostCapReached as stop:  # 4.1
            status, error = "aborted_cost_cap", str(stop)
        except rounds.Stop as stop:  # 4.2
            status, error = stop.status, stop.error
        except Exception:  # 4.3
            status, error = "error", ("settlement failure: " if settling else "") + traceback.format_exc()
        finally:  # a backend with live sessions (provider claude_code) ends them with the episode
            for b in (*backends.values(), summarizer):
                getattr(b, "close", lambda: None)()
        state.emit(m, "stopped", status=status, error=error, days_completed=days)
    return end(status, error)


def _named(spec):
    """A model spec as the summary names it (6.4)."""
    return {"provider": spec["provider"], "model": spec.get("model")}


# 1. The command line, 2. A run


def _seeds(text):
    """--seeds LIST: comma-separated integers (1)."""
    return [int(seed) for seed in text.split(",")]


def _report(summary):
    """An episode's report as it ends (2, step 6)."""
    line = f"seed {summary['seed']}: {summary['status']}, {summary['days_completed']} days completed"
    if summary["status"] == "ok":
        return f"{line}, welfare {summary['welfare']}, ${summary['metrics']['dollars']}"
    return f"{line}: {((summary['error'] or '').strip().splitlines() or [''])[-1]}"


def main(argv=None):
    """The command (1) and its run (2); returns the exit status."""
    parser = argparse.ArgumentParser(prog="python -m market", description="Run one episode per seed of a config.")
    parser.add_argument("config", metavar="CONFIG", help="path of the config file")
    parser.add_argument("--seeds", metavar="LIST", type=_seeds, help="the seeds to run, e.g. 0,3")
    parser.add_argument("--cost-cap", metavar="USD", type=float, help="the run's dollar cap")
    parser.add_argument("--out", metavar="DIR", help="the root folder of the outputs")
    parser.add_argument("--parallel", metavar="N", type=int, help="how many episodes run at the same time")
    parser.add_argument("--force", action="store_true", help="start every selected episode afresh")
    args = parser.parse_args(argv)
    try:  # step 1
        cfg = config.load(args.config, seeds=args.seeds, cost_cap=args.cost_cap, out=args.out,
                          parallel=args.parallel)
    except config.ConfigError as refused:
        print(f"error: {refused}", file=sys.stderr)
        return 1
    refused = sandbox.host_check()
    if refused:
        print(f"error: {refused}", file=sys.stderr)
        return 1
    folders = {seed: os.path.join(cfg["output_dir"], cfg["name"], f"seed_{seed}") for seed in cfg["seeds"]}  # step 2
    if args.force:  # step 3
        for folder in folders.values():
            if os.path.isdir(folder):
                shutil.rmtree(folder)
    tracker = models.CostTracker(cfg["cost_cap_usd"])
    to_run = []
    for seed, folder in folders.items():  # step 4
        if read_summary(folder).get("status") == "ok":
            print(f"seed {seed}: already done", flush=True)
        else:
            to_run.append(seed)
    not_ok = 0
    with ThreadPoolExecutor(cfg["parallel_episodes"]) as pool:  # step 5
        futures = {pool.submit(run_episode, cfg, seed, folders[seed], tracker): seed for seed in to_run}
        try:
            for future in as_completed(futures):  # step 6
                try:
                    summary = future.result()
                except Exception:  # 4.3: an episode whose folder cannot be written stops no other
                    summary = {"seed": futures[future], "status": "error", "days_completed": 0,
                               "error": traceback.format_exc()}
                not_ok += summary["status"] != "ok"
                print(_report(summary), flush=True)
        except KeyboardInterrupt:  # "Interrupting a run": nothing is written on the way out
            print("interrupted: run the same command again (without --force) to resume", flush=True)
            os._exit(130)
    print(f"spent ${F(tracker.total, 6)} in this run; {tracker.unknown} calls had an unknown cost"  # step 7
          + (" (the dollar totals are underestimates)" if tracker.unknown else ""))
    print(f"{not_ok} of {len(folders)} episodes are not ok"
          + (": run the same command again (without --force) to resume them" if not_ok else ""))
    return 1 if not_ok else 0
