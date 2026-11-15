"""The keyed draws of spec/randomness.md.

Every random value comes from a key, the tuple (seed, *parts); `uniforms` gives the key's stream, section 2's
constructions are built on it, and each draw of section 3 is one pure function of its key and its parameters.
Nothing here holds state: the counters (attempt, k, n, i) live in the market and are passed in.
"""

import hashlib
import itertools
import json
from math import ceil, erfc, exp, sqrt
from statistics import NormalDist

_STANDARD = NormalDist()


def uniforms(seed, *parts):
    """The stream of independent uniforms on [0, 1) of the key (seed, *parts) (section 1, items 1 and 2)."""
    key = json.dumps([seed, *parts])
    for j in itertools.count():
        digest = hashlib.sha256(f"{key}{j}".encode()).digest()
        yield (int.from_bytes(digest[:8], "big") >> 11) / 2**53


def normal(u):
    """The standard normal Z of a uniform u, its tails clipped at 1e-12 (section 2)."""
    return _STANDARD.inv_cdf(min(max(u, 1e-12), 1 - 1e-12))


def integer(us, a, b):
    """A uniform integer on [a, b], both ends included, taken from the uniforms `us` (section 2)."""
    n = b - a + 1
    chunks = 1
    while 2 ** (53 * chunks) < n:
        chunks += 1
    size = 2 ** (53 * chunks) // n
    while True:
        r = 0
        for _ in range(chunks):
            r = (r << 53) | int(next(us) * 2**53)
        if r // size < n:
            return a + r // size


def permutation(us, items):
    """A uniform permutation of `items`, as a new list, taken from the uniforms `us` (section 2)."""
    out = list(items)
    for i in range(len(out) - 1, 0, -1):
        j = integer(us, 0, i)
        out[i], out[j] = out[j], out[i]
    return out


def Phi(x):
    """The standard normal CDF, accurate deep in its lower tail (section 2, "Accuracy"; prompts.md, 2)."""
    return erfc(-x / sqrt(2)) / 2


def tn(mean, sd, z):
    """The truncated normal TN(mean, sd, Z) on [0, 10], by inverse CDF (section 2)."""
    a, b = Phi((0 - mean) / sd), Phi((10 - mean) / sd)
    p = min(max(a + Phi(z) * (b - a), 1e-15), 1 - 1e-15)
    return min(10.0, max(0.0, mean + sd * _STANDARD.inv_cdf(p)))


def stream_key(contract_id):
    """The stream key of a contract: task-N for contract-N, task-N-k for contract-N-k (section 3)."""
    return "task" + contract_id.removeprefix("contract")


def participant_id(seed, role, seat, used):
    """3.1: the ID of a seat, drawn again while it is in `used`, the IDs of the participants created earlier."""
    for attempt in itertools.count():
        x = integer(uniforms(seed, "id", role, seat, attempt), 0, 4095)
        pid = f"{'B' if role == 'buyer' else 'M'}-{x:03x}"
        if pid not in used:
            return pid


def horizon(seed, min_days, end_prob, cap):
    """3.2: the last arrival day L, at most `cap` (config.md, 6.1)."""
    us = uniforms(seed, "horizon")
    day = min_days
    while day < cap and next(us) >= end_prob:
        day += 1
    return day


def share(seed, role, seat, lo, hi, decimals):
    """3.3: a participant's wealth share."""
    return round(lo + (hi - lo) * next(uniforms(seed, "share", role, seat)), decimals)


def arrivals(seed, day, seat, p):
    """3.4: the number of tasks a buyer seat gets on a day of 2..L."""
    us = uniforms(seed, "arrivals", day, seat)
    n = 0
    while next(us) < p:
        n += 1
    return n


def worth(seed, day, seat, j, lo, hi):
    """3.5: the worth of the j-th task (0-based) arriving for a buyer seat on a day."""
    return integer(uniforms(seed, "worth", day, seat, j), lo, hi)


def deadline(seed, task_id, lo, hi):
    """3.6: a task's due-day offset D."""
    return integer(uniforms(seed, task_id, "deadline"), lo, hi)


def order(seed, day, round_of_day, participants):
    """3.7: the turn order of a round, from all participants in seat order."""
    return permutation(uniforms(seed, "order", day, round_of_day), participants)


def cost(production, worker, kind, m=1.0):
    """3.8: a run's cost before rounding, for the cost multiplier m (config.md, 6.5: m = 1 is the expected cost)."""
    w, ref = production[worker], production["cost_ref_worth"]
    if worker == "employee":
        return w["fee"] + w["cost_frac" if kind == "fresh" else "ref_cost_frac"] * ref * m
    return w["k"] * ref * m if kind == "fresh" else w["ref_cost_fixed"] * m


def run(seed, stream, worker, kind, k, production, q_parent=None):
    """3.8: a run's three standard normals and its quality, days and cost; `production` is that config block."""
    us = uniforms(seed, stream, worker, kind, k)
    z_q, z_t, z_c = normal(next(us)), normal(next(us)), normal(next(us))
    w = production[worker]
    sig_c = production["sig_c"]
    if kind == "fresh":
        q, t = tn(w["mu"], w["sd"], z_q), w["t_med"]
    else:
        q, t = tn(q_parent + w["gain"] * (10 - q_parent) / 10, w["sd_ref"], z_q), w["t_ref_med"]
    c = cost(production, worker, kind, exp(sig_c * z_c - sig_c * sig_c / 2))
    days = max(1, ceil(t * exp(production["sig_t"] * z_t)))
    return {"z_q": z_q, "z_t": z_t, "z_c": z_c, "q": q, "days": days, "cost": round(c, 2)}


def label(seed, stream, n, used):
    """3.9: a result's label, unused on its contract, and the contract's label-draw count after it."""
    while True:
        text = f"R-{integer(uniforms(seed, stream, 'label', n), 0, 4095):03x}"
        n += 1
        if text not in used:
            return {"label": text, "n": n}


def _noisy(us, q, sd, decimals):
    """A reading of true quality q with noise sd: clipped to [0, 10], then rounded (3.10, 3.11)."""
    z = normal(next(us))
    return {"z": z, "reading": round(min(10.0, max(0.0, q + sd * z)), decimals)}


def reading(seed, stream, side, label, i, q, sd, decimals):
    """3.10: a side's i-th reading (0-based) of a result of true quality q, and its standard normal."""
    return _noisy(uniforms(seed, stream, "test", side, label, i), q, sd, decimals)


def arbiter(seed, stream, label, q, sd, decimals):
    """3.11: the arbiter's reading of a disputed result of true quality q, and its standard normal."""
    return _noisy(uniforms(seed, stream, "arbiter", label), q, sd, decimals)
