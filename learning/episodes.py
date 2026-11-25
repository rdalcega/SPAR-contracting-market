"""Running the episodes of a generation, each with its firms' instructions.

Each episode gets a run name of its own (`s<seed>_p<play>`) inside the generation's folder, so `hook` can give it
its own instructions while episodes run at the same time. Episodes run through the market's own `run_episode`,
which also resumes an episode from its checkpoint and leaves an episode that is already ok as it is.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from market import config, models, run, sandbox

from . import hook


def episode_name(seed, play):
    return f"s{seed}_p{play}"


def episode_folder(out_dir, seed, play):
    return os.path.join(out_dir, episode_name(seed, play), f"seed_{seed}")


def run_plays(market_raw, jobs, out_dir, parallel, tracker):
    """jobs: [(seed, play, {firm: instruction text})]. Runs every episode not already ok; raises if any episode
    does not end ok. The episodes are read back from their folders (`episode_folder`) by the caller."""
    refused = sandbox.host_check()
    if refused:
        raise RuntimeError(refused)
    hook.install()

    def one(seed, play, instructions):
        name = episode_name(seed, play)
        cfg = config.resolve({**market_raw, "name": name, "seeds": [seed], "output_dir": out_dir})
        folder = episode_folder(out_dir, seed, play)
        if run.read_summary(folder).get("status") == "ok":
            return seed, play, {"status": "ok"}
        hook.register(name, instructions)
        try:
            return seed, play, run.run_episode(cfg, seed, folder, tracker)
        finally:
            hook.unregister(name)

    results = {}
    with ThreadPoolExecutor(parallel) as pool:
        for future in as_completed([pool.submit(one, *job) for job in jobs]):
            seed, play, summary = future.result()
            results[(seed, play)] = summary
    bad = {k: v.get("status") for k, v in results.items() if v.get("status") != "ok"}
    if bad:
        raise RuntimeError(f"episodes not ok: {bad}")


def tracker(cap):
    return models.CostTracker(cap)
