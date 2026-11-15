"""The system prompts of spec/prompts.md: a template of spec/text/ with every field filled."""

import functools
import math
import re
from pathlib import Path

from . import config
from .actions import can_send
from .fmt import CREDITS, FRAC, INT, PCT, PCT0, SUM, F, G, unit

TEXT_DIR = Path(__file__).parent.parent / "spec" / "text"
_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")
_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
_MM_SHOWN = (
    "Each conversation shows its messages in full, up to {k} of them. Whenever a conversation has more than {k} "
    "messages, the market replaces its earliest messages, in groups of {k}, with a summary written automatically by "
    "the market (covering any earlier summary too), so the conversation shows that summary and its latest 1 to {k} "
    "messages."
)


@functools.cache
def template(role, mode):
    """Section 1, item 1: the text of spec/text/<role>_system_<mode>.txt."""
    return (TEXT_DIR / f"{role}_system_{mode}.txt").read_bytes().decode("ascii")


def value_list(cfg):
    """Section 4.2: the ten quality -> value items."""
    q0, exponent = cfg["value"]["q0"], cfg["value"]["exponent"]
    return ", ".join(
        f"{INT(q)} -> {F(100 * math.pow(max(0, min(1, (q - q0) / (10 - q0))), exponent), 1)}%" for q in range(1, 11)
    )


def duration_text(cfg, t, subject):
    """Section 4.4: the duration text of the law (t, production.sig_t) for the subject words."""
    mix = config.days_mix(cfg, t)
    share = dict(mix)
    listed = [d for d, s in mix if d != 1000 and s >= 0.05]
    first, last = listed[0], listed[-1]
    below = SUM([s for d, s in mix if d < first])
    high = SUM([s for d, s in mix if d > last])
    low = below > 0
    tail = high >= 0.005
    up = not tail and high >= 1e-4
    split = up and low and first == last
    names, shares = [], []
    for d in range(first, last + 1):
        shares.append(share[d] + (below if d == first else 0) + (high if d == last and not tail and not split else 0))
        if d == first and low:
            names.append(f"{INT(d)} or fewer days")
        elif d == last and up and not split:
            names.append(f"{INT(d)} or more days")
        else:
            names.append(unit(INT(d), "day"))
    if tail or split:
        names.append(f"{INT(last + 1)} or more days")
    if split:
        percents = ["more than 99%", "less than 1%"]
    else:
        if tail:
            shares.append(high)
        n = [math.floor(100 * p) for p in shares]
        for i in sorted(range(len(n)), key=lambda i: n[i] - 100 * shares[i])[: 100 - sum(n)]:
            n[i] += 1
        if tail and n[-1] == 0:
            n[n.index(max(n))] -= 1
            n[-1] = 1
        percents = [INT(x) + "%" for x in n]
    clauses = [f"with probability {percent}, {subject} take {name}" for percent, name in zip(percents, names)]
    text = clauses[0] if len(clauses) == 1 else ", ".join(clauses[:-1]) + ", and " + clauses[-1]
    return text[0].upper() + text[1:]


def _day(k):
    """Section 4.5: the day phrase of k days after the post's day."""
    return "that day" if k == 0 else "the next day" if k == 1 else f"the day {INT(k)} days later"


def _place(i, R):
    """Section 4.5: position i as `round <r> of <day phrase>`, and its day k."""
    k, r = divmod(i - 1, R)
    return f"round {INT(r + 1)} of {_day(k)}", k


def _span(s, e, R):
    """Section 4.5: the span text of positions s..e."""
    first, last = (s - 1) // R, (e - 1) // R
    groups = []
    for k in range(first, last + 1):
        a = (s - 1) % R + 1 if k == first else 1
        b = (e - 1) % R + 1 if k == last else R
        rounds = f"round {INT(a)}" if a == b else f"rounds {INT(a)} {'and' if b == a + 1 else 'to'} {INT(b)}"
        groups.append(f"{rounds} of {_day(k)}")
    return groups[0] if len(groups) == 1 else ", ".join(groups[:-1]) + " and " + groups[-1]


def grace_examples(cfg):
    """Section 4.5: the two sentences on the grace period of a post made in each round of a day."""
    R, W = cfg["time"]["rounds_per_day"], cfg["contracts"]["bid_window_rounds"]
    one = "Specifically, "
    two = "So bids on a post made in round 1 can first be accepted in "
    wrapped = False
    for r in range(1, R + 1):
        wraps = _place(r + W, R)[1] >= 1
        if r > 1:
            one += "; but " if wraps and not wrapped else "; "
            two += ", " + ("and " if r == R else "") + f"on a post made in round {INT(r)} in "
        one += f"if a buyer posts a task in round {INT(r)} of a day, the grace period is {_span(r + 1, r + W, R)}"
        two += _place(r + W + 1, R)[0]
        wrapped = wraps
    return f"{one}. {two}."


def _phi(x):
    """Section 2: the standard normal density."""
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def _worker(cfg, prefix, name):
    """Sections 3.3 and 4.3: the seventeen fields of a worker (name employee or manager)."""
    p = cfg["production"]
    w = p[name]
    mu, sd = w["mu"], w["sd"]
    a, b = (0 - mu) / sd, (10 - mu) / sd
    mass = config.Phi(b) - config.Phi(a)
    out = {
        "mean": F(mu + sd * (_phi(a) - _phi(b)) / mass, 1),
        "mu": G(mu),
        "sd": G(sd),
        "gain": G(round(w["gain"] / 10, 6)),
        "sd_ref": G(w["sd_ref"]),
        "runs": unit(INT(w["capacity"]), "run"),
    }
    for q in range(5, 10):
        out[f"reach{q}"] = PCT0((config.Phi(b) - config.Phi((q - mu) / sd)) / mass)
    fixed = w["fee"] if name == "employee" else 0
    runs = (("fresh", "fresh", w["t_med"], "fresh runs"), ("refine", "ref", w["t_ref_med"], "refinements"))
    for kind, key, t, subject in runs:
        cost = config.expected_cost(cfg, name, kind)
        out[f"{key}_days"] = duration_text(cfg, t, f"{name} {subject}")
        out[f"{key}_cost"] = CREDITS(round(cost, 2))
        out[f"{key}_spread"] = PCT0(p["sig_c"] * (cost - fixed) / cost)
    return {f"{prefix}_{k}": v for k, v in out.items()}


def _price(cfg, spec, key):
    """Section 3.1: a token price in credits; config.md 6, "Finiteness during an episode": never an infinite one."""
    price = spec.get(key, 0) * cfg["credits_per_usd"]
    if not math.isfinite(price):
        raise ArithmeticError(
            f"the stated token price is not finite: {key} {spec[key]} x credits_per_usd {cfg['credits_per_usd']}"
        )
    return unit(G(price), "credit")


def fields(cfg, role, agent_id, share, seat):
    """Section 3: the value of every field of the agent's template, by name."""
    mode = cfg["message_channels"]
    time, tasks, money, value, tests = cfg["time"], cfg["tasks"], cfg["money"], cfg["value"], cfg["tests"]
    contracts, escrow, compute, turns = cfg["contracts"], cfg["contracts"]["escrow"], cfg["compute"], cfg["turns"]
    spec = config.seat_spec(cfg, role, seat)
    p = tasks["arrival_p"]
    W, D = contracts["bid_window_rounds"], escrow["dispute_window_days"]
    after = " and the day after" if D == 1 else f" and the {INT(D)} days after"
    close_days = config.escrow_close_days(cfg)
    out = {
        "n_buyers": INT(cfg["participants"]["buyers"]),
        "n_managers": INT(cfg["participants"]["managers"]),
        "rounds_text": unit(INT(time["rounds_per_day"]), "round"),
        "first_day_tasks": INT(tasks["first_day_tasks"]),
        "arrival_law": "(1/2)^(n+1)" if p == 0.5 else f"({FRAC(1 - p)}) x ({FRAC(p)})^n",
        "prob0": FRAC(1 - p),
        "prob1": FRAC((1 - p) * p),
        "prob2": FRAC((1 - p) * p * p),
        "share_lo": G(money["share_range"][0]),
        "share_hi": G(money["share_range"][1]),
        "start_std": G(money["start_amount"]),
        "start_std_text": unit(G(money["start_amount"]), "credit"),
        "agent_id": agent_id,
        "share": F(share, 2),
        "start_balance": F(config.start_balance(cfg, share), 2),
        "buyer_sd": G(tests["buyer_sd"]),
        "buyer_test_cost": CREDITS(tests["buyer_cost"]),
        "manager_sd": G(tests["manager_sd"]),
        "manager_test_cost": CREDITS(tests["manager_cost"]),
        "decimal_places": unit(INT(tests["decimals"]), "decimal place"),
        "q0": G(value["q0"]),
        "q_span": G(10 - value["q0"]),
        "exponent": G(value["exponent"]),
        "value_list": value_list(cfg),
        "late_decay": G(value["late_decay"]),
        "late_pct": PCT(value["late_decay"]),
        "min_days_text": unit(INT(contracts["min_contract_days"]), "day"),
        "cap_days_text": unit(INT(close_days - 1), "day"),
        "fee_pct": PCT(escrow["fee_rate"]),
        "review_days_text": unit(INT(escrow["review_days"]), "day"),
        "dispute_window_b": "throughout the day of the buyer's rejection" + after,
        "dispute_until": "the day after it" if D == 1 else f"the day {INT(D)} days after it",
        "dispute_pct": PCT(escrow["dispute_fee_rate"]),
        "arbiter_sd": G(escrow["arbiter_sd"]),
        "escrow_close_text": unit(INT(close_days), "day"),
        "grace_length": (_WORDS[W - 1] if 1 <= W <= 10 else INT(W)) + (" round" if W == 1 else " rounds"),
        "grace_examples": grace_examples(cfg),
        "cpu_s_text": unit(INT(compute["cpu_s"]), "second"),
        "wall_s_text": unit(INT(compute["wall_s"]), "second"),
        "out_kb": INT(compute["output_kb"]),
        "code_chars_text": unit(INT(compute["code_chars"]), "character"),
        "out_chars_text": unit(INT(compute["output_chars"]), "character"),
        "max_actions": INT(turns["max_actions"]),
        "max_actions_text": unit(INT(turns["max_actions"]), "action"),
        "memory_chars_text": unit(INT(turns["memory_chars"]), "character"),
        "msg_chars_text": unit(INT(contracts["message_chars"]), "character"),
        "price_in_text": _price(cfg, spec, "price_in_per_m"),
        "price_out_text": _price(cfg, spec, "price_out_per_m"),
        "close_after_text": unit(INT(time["close_after_due_days"]), "day"),
        "n_sections": INT(len(template(role, mode).split("\n\n")[-1].split("\n"))),
    }
    if role == "buyer":
        out |= {
            "budget_std": G(money["task_budget"]),
            "budget_std_text": unit(G(money["task_budget"]), "credit"),
            "budget": F(config.per_task_budget(cfg, share), 2),
        }
    else:
        C = cfg["production"]["manager"]["capacity"]
        out |= {
            "worth_lo": G(tasks["worth_range"][0]),
            "worth_hi_text": unit(G(tasks["worth_range"][1]), "credit"),
            "dispute_window_m": "throughout the day of the rejection" + after,
            "own_busy": "a run" if C == 1 else f"{INT(C)} runs",
        }
        out |= _worker(cfg, "emp", "employee")
        out |= _worker(cfg, "own", "manager")
    if can_send(role, mode):
        out |= {
            "mm_chars_text": unit(INT(cfg["messaging"]["message_chars"]), "character"),
            "mm_shown": _MM_SHOWN.format(k=INT(cfg["messaging"]["recent"])),
        }
    return out


def system_prompt(cfg, role, agent_id, share, seat):
    """Section 1: the agent's system prompt, its template with every placeholder replaced in one pass."""
    text = template(role, cfg["message_channels"])
    values = fields(cfg, role, agent_id, share, seat)
    named = set(_PLACEHOLDER.findall(text))
    if named != set(values):
        raise ValueError(
            f"{role}_system_{cfg['message_channels']}.txt: undefined fields {sorted(named - set(values))}, "
            f"unused fields {sorted(set(values) - named)}"
        )
    return _PLACEHOLDER.sub(lambda m: values[m[1]], text)
