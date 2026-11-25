"""Whether each firm keeps its revision: the paired comparison of a generation.

For each firm and seed: (normalised score with the revision) - (normalised score with the current version), from
the seed's two plays. A firm keeps its revision if the mean of these differences is above the margin. In the sham
arm the decision is a coin with a fixed probability instead, drawn from a seeded generator, so it is reproducible.

Normalised score: a firm's score divided by tasks spawned (its own tasks for a buyer, all tasks for a manager). In
the welfare arm every firm is judged on the market's score (objective.py) divided by all tasks.
"""

import math
import random
from dataclasses import dataclass
from statistics import mean, stdev

from .design import FIRMS
from .objective import ARMS, LOSS_WEIGHT, MARKET_ARMS, market_score


def normalised_score(ep, arm, role, seat, loss_weight=LOSS_WEIGHT):
    """The score a firm is judged on in one episode, per task spawned. None if there are no such tasks."""
    tasks = ep.of("task")
    if arm in MARKET_ARMS:
        return market_score(ep, loss_weight)["score"] / len(tasks) if tasks else None
    pid = ep.id_of(role, seat)
    n = sum(t.get("buyer") == pid for t in tasks) if role == "buyer" else len(tasks)
    return ep.scores[pid]["score"] / n if n else None


@dataclass
class Decision:
    firm: tuple
    diffs: list  # per seed: revision minus current
    mean: float | None
    se: float | None
    kept: bool
    reason: str


def _se(xs):
    return stdev(xs) / math.sqrt(len(xs)) if len(xs) >= 2 else None


def decide(episodes, assignment, arm, margin=0.0, margin_in_se=False, sham_p=0.5, sham_seed=None,
           has_revision=None, loss_weight=LOSS_WEIGHT):
    """episodes: {(seed, play): Episode}; assignment: {(seed, play): {firm: 'revision' | 'current'}}.

    margin: keep when mean > margin (or mean > margin x se when margin_in_se). sham_p, sham_seed: the sham arm's
    coin, one generator per firm, so a firm's draw does not depend on whether other firms had a revision.
    has_revision: {firm: bool}; a firm whose writer produced no revision keeps its current version.
    Returns {firm: Decision}."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    seeds = sorted({seed for seed, _ in episodes})
    out = {}
    for firm in FIRMS:
        if has_revision is not None and not has_revision.get(firm, True):
            out[firm] = Decision(firm, [], None, None, False, "no revision")
            continue
        diffs = []
        for seed in seeds:
            by_version = {}
            for play in (0, 1):
                version = assignment[(seed, play)][firm]
                by_version[version] = normalised_score(episodes[(seed, play)], arm, *firm, loss_weight)
            if None in by_version.values() or set(by_version) != {"revision", "current"}:
                continue
            diffs.append(by_version["revision"] - by_version["current"])
        m = mean(diffs) if diffs else None
        se = _se(diffs)
        if arm == "sham":
            kept = random.Random(f"sham|{sham_seed}|{firm[0]}_{firm[1]}").random() < sham_p
            reason = f"sham coin (p={sham_p})"
        elif m is None:
            kept, reason = False, "no comparable seeds"
        else:
            bar = margin * se if (margin_in_se and se is not None) else margin
            kept = m > bar
            reason = f"mean {m:.4f} {'>' if kept else '<='} {bar:.4f}"
        out[firm] = Decision(firm, diffs, m, se, kept, reason)
    return out
