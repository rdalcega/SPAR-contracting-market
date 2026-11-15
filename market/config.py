"""The configuration of spec/config.md: the defaults, reading and checking a config, and the derived values."""

import json
import math
from fractions import Fraction

import yaml

from . import rng
from .fmt import G
from .rng import Phi

DEFAULTS = {
    "seeds": [0, 1, 2],
    "parallel_episodes": 4,
    "cost_cap_usd": 20,
    "output_dir": "runs",
    "name": "market",
    "message_channels": "off",
    "participants": {"buyers": 2, "managers": 3},
    "models": {
        "buyers": {"provider": "openrouter", "model": "qwen/qwen3.8-27b", "price_in_per_m": 0.15,
                   "price_out_per_m": 1.875,
                   "extra_body": {"provider": {"quantizations": ["bf16", "fp16", "fp8", "unknown"]}}},
        "managers": {"provider": "openrouter", "model": "qwen/qwen3.8-27b", "price_in_per_m": 0.15,
                     "price_out_per_m": 1.875,
                     "extra_body": {"provider": {"quantizations": ["bf16", "fp16", "fp8", "unknown"]}}},
        "buyer_seats": {},
        "manager_seats": {},
        "summarizer": {"provider": "openrouter", "model": "anthropic/claude-sonnet-5.5", "price_in_per_m": 2.0,
                       "price_out_per_m": 10.0, "temperature": None, "max_tokens": 8000,
                       "extra_body": {"reasoning": {"effort": "high"}}},
    },
    "llm": {"temperature": 0.8, "max_tokens": 64000, "timeout_s": 1200, "max_retries": 5,
            "network_patience_s": 900},
    "credits_per_usd": 1,
    "time": {"rounds_per_day": 3, "horizon_min_days": 10, "horizon_end_prob": 0.1, "close_after_due_days": 5},
    "turns": {"max_actions": 6, "memory_chars": 2000},
    "tasks": {"first_day_tasks": 2, "arrival_p": 0.5, "worth_range": [40, 80], "due_days_range": [4, 9]},
    "money": {"start_amount": 60, "task_budget": 30, "share_range": [0.6, 1.4], "share_decimals": 2},
    "value": {"q0": 1, "exponent": 2, "late_decay": 0.2},
    "contracts": {
        "bid_window_rounds": 1,
        "min_contract_days": 4,
        "message_chars": 500,
        "escrow": {"fee_rate": 0.10, "review_days": 2, "dispute_window_days": 1, "dispute_fee_rate": 0.5,
                   "arbiter_sd": 0.5},
    },
    "production": {
        "cost_ref_worth": 80,
        "sig_t": 0.35,
        "sig_c": 0.25,
        "employee": {"mu": 6.0, "sd": 2.5, "gain": 4.0, "sd_ref": 1.5, "t_med": 1.0, "t_ref_med": 0.7,
                     "fee": 1.0, "cost_frac": 0.0375, "ref_cost_frac": 0.0225, "capacity": 2},
        "manager": {"mu": 7.0, "sd": 1.2, "gain": 7.0, "sd_ref": 0.8, "t_med": 2.5, "t_ref_med": 1.0,
                    "k": 0.1, "ref_cost_fixed": 14.0, "capacity": 1},
    },
    "tests": {"manager_sd": 0.5, "manager_cost": 1, "buyer_sd": 2.0, "buyer_cost": 1, "decimals": 1},
    "messaging": {"message_chars": 500, "recent": 10, "summary_chars": 2000},
    "compute": {"cpu_s": 5, "wall_s": 10, "memory_mb": 512, "output_kb": 64, "output_chars": 4000,
                "code_chars": 20000},
    "view": {"finished_shown": 5, "open_shown": 8, "readings_shown": 5, "overdue_days": 5, "recent_days": 3,
             "messages_shown": 3, "payments_shown": 8, "overflow_ids": 20},
}


class ConfigError(Exception):
    """A refused config (section 1, item 6): the message names the key path(s) and states the rule."""


def _fail(keys, rule):
    """Refuse the config (section 1, item 6)."""
    raise ConfigError(f"{keys}: {rule}")


_TOO_LONG = 10**4300  # an integer of more than 4,300 digits cannot be written out (SPEC_QUESTIONS.md, Q18)


def _key(k):
    """A key as an error names it; an integer of more than 4,300 digits cannot be written out."""
    return "(an integer of more than 4,300 digits)" if type(k) is int and abs(k) >= _TOO_LONG else k


def _int(low, high=None):
    """An "integer" parameter (section 1, item 5) in low..high."""
    rule = f"must be an integer >= {low}" if high is None else f"must be an integer, {low}..{high}"

    def check(path, v):
        if type(v) is not int or v < low or (high is not None and v > high):
            _fail(path, rule)
        if v >= _TOO_LONG:
            _fail(path, "must be an integer of at most 4,300 digits")
        return v

    return check


def _num(rule, ok, null=False):
    """A "number" parameter (section 1, item 5), stored as a double; null allowed where its row says so."""

    def check(path, v):
        if v is None and null:
            return None
        try:
            x = float(v) if type(v) in (int, float) else math.nan
        except OverflowError:
            x = math.nan
        if not (math.isfinite(x) and ok(x)):
            _fail(path, f"must be {rule}")
        return x

    return check


def _pair(element):
    """A pair [low, high] of the stated type (section 1, item 5), low <= high."""

    def check(path, v):
        if type(v) is not list or len(v) != 2:
            _fail(path, "must be a list of exactly two elements, [low, high]")
        low, high = (element(path, x) for x in v)
        if low > high:
            _fail(path, "must have low <= high")
        return [low, high]

    return check


def _text(path, v):
    """A non-empty string (3.1, 3.4.1)."""
    if type(v) is not str or not v:
        _fail(path, "must be a non-empty string")
    return v


def _name(path, v):
    """`name` (3.1): one folder name."""
    if _text(path, v) in (".", "..") or any(c in v for c in "/\\\0"):
        _fail(path, "must be one folder name: not . or .., and with no /, \\ or NUL character")
    return v


def _seeds(path, v):
    """`seeds` (3.1): a non-empty list of distinct integers >= 0."""
    if (type(v) is not list or not v or any(type(s) is not int or not 0 <= s < _TOO_LONG for s in v)
            or len(set(v)) != len(v)):
        _fail(path, "must be a non-empty list of distinct integers >= 0 (of at most 4,300 digits)")
    return list(v)


MODES = ("off", "managers", "buyers", "within_roles", "all")
PROVIDERS = ("openrouter", "openai_compatible", "ollama", "mock")
FREE_PROVIDERS = ("mock", "ollama")


def _channels(path, v):
    """`message_channels` (3.2; section 1, item 5): the boolean false means off."""
    if v is False:
        return "off"
    if type(v) is not str or v not in MODES:
        _fail(path, "must be one of " + ", ".join(MODES))
    return v


def _provider(path, v):
    """`provider` (3.4.1): a name the implementation does not have is refused."""
    if type(v) is not str or v not in PROVIDERS:
        _fail(path, "must be one of " + ", ".join(PROVIDERS))
    return v


def _extra_body(path, v):
    """`extra_body` (3.4.1): a copy of a mapping with a JSON form, checked against no schema.

    Its JSON text read back is the copy; it differs from the value given where a key was no string.
    """
    try:
        body = json.loads(json.dumps(v, allow_nan=False))
        ok = type(v) is dict and body == v
    except (TypeError, ValueError, RecursionError):  # no JSON form, or none that can be written
        ok = False
    if not ok:
        _fail(path, "must be a mapping with a JSON form that can be written (string keys, lists, strings, "
                    "finite numbers, booleans and null)")
    return body


def _arrival_p(x):
    """`tasks.arrival_p` (3.7): within 1e-12 of a fraction n/d with d <= 100."""
    nearest = Fraction(x).limit_denominator(100)
    return 0 < x < 1 and 0 < nearest < 1 and abs(Fraction(x) - nearest) <= Fraction(1, 10**12)


_positive = _num("a number > 0", lambda x: x > 0)
_non_negative = _num("a number >= 0", lambda x: x >= 0)
_non_negative_or_null = _num("a number >= 0, or null", lambda x: x >= 0, null=True)
_probability = _num("a number, 0 < p < 1", lambda x: 0 < x < 1)
_amount = _num("a whole number, 0 < x < 10^6", lambda x: x.is_integer() and 0 < x < 10**6)

_SPEC_KEYS = {
    "provider": _provider,
    "model": _text,
    "price_in_per_m": _non_negative,
    "price_out_per_m": _non_negative,
    "temperature": _non_negative_or_null,
    "max_tokens": _int(1),
    "extra_body": _extra_body,
    "base_url": _text,
    "api_key_env": lambda path, v: None if v is None else _text(path, v),
}


def _spec(agent):
    """A model spec (3.4.1; section 4's two rules on specs); agent: a seat's spec, not the summarizer's."""

    def check(path, v):
        if type(v) is not dict:
            _fail(path, "must be a mapping")
        spec = {}
        for k, x in v.items():
            if k not in _SPEC_KEYS:
                _fail(f"{path}.{_key(k)}", "unknown key")
            if x is not None or k in ("temperature", "api_key_env", "max_tokens"):
                spec[k] = _SPEC_KEYS[k](f"{path}.{k}", x)
        if "provider" not in spec:
            _fail(f"{path}.provider", "is required")
        if "model" not in spec and spec["provider"] != "mock":
            _fail(f"{path}.model", "is required, except with provider mock")
        if "base_url" not in spec and spec["provider"] == "openai_compatible":
            _fail(f"{path}.base_url", "is required with provider openai_compatible")
        if agent and spec["provider"] not in FREE_PROVIDERS:
            for k in ("price_in_per_m", "price_out_per_m"):
                if not spec.get(k, 0) > 0:
                    _fail(f"{path}.{k}", "is required, a number > 0, in an agent spec of a provider that is "
                                         "not free (mock, ollama)")
        return spec

    return check


def _seats(path, v):
    """`models.buyer_seats` / `models.manager_seats` (3.4.2): {seat index: model spec}; null is {}."""
    if v is None:
        return {}
    if type(v) is not dict:
        _fail(path, "must be a mapping")
    seats = {}
    for k, x in v.items():
        whole = type(k) is int and 0 <= k < _TOO_LONG
        if not (whole or (type(k) is str and k.isascii() and k.isdigit() and (k == "0" or k[0] != "0"))):
            _fail(f"{path}.{_key(k)}", "must be a seat index, an integer or its decimal digits")
        spec = _spec(True)(f"{path}.{k}", x)
        if whole or str(k) not in seats:
            seats[str(k)] = spec
    return seats


def _each(prefix, names, rule):
    """The same rule for several keys of one block."""
    return {f"{prefix}.{name}": rule for name in names.split()}


_worker = {
    **_each("", "sd sd_ref t_med t_ref_med", _positive),
    ".mu": _num("a number, 0 <= mu <= 10", lambda x: 0 <= x <= 10),
    ".gain": _num("a number, 0 < gain <= 10", lambda x: 0 < x <= 10),
    ".capacity": _int(1),
}

_SCHEMA = {
    "seeds": _seeds,
    "parallel_episodes": _int(1),
    "cost_cap_usd": _num("a number > 0, or null", lambda x: x > 0, null=True),
    "output_dir": _text,
    "name": _name,
    "message_channels": _channels,
    **_each("participants", "buyers managers", _int(2, 4096)),
    **_each("models", "buyers managers", _spec(True)),
    **_each("models", "buyer_seats manager_seats", _seats),
    "models.summarizer": _spec(False),
    "llm.temperature": _non_negative_or_null,
    "llm.max_tokens": _int(1),
    "llm.timeout_s": _positive,
    "llm.max_retries": _int(0),
    "llm.network_patience_s": _non_negative,
    "credits_per_usd": _positive,
    **_each("time", "rounds_per_day horizon_min_days close_after_due_days", _int(1)),
    "time.horizon_end_prob": _probability,
    **_each("turns", "max_actions memory_chars", _int(1)),
    "tasks.first_day_tasks": _int(2),
    "tasks.arrival_p": _num("a number, 0 < p < 1, within 1e-12 of a fraction n/d with d <= 100", _arrival_p),
    **_each("tasks", "worth_range due_days_range", _pair(_int(1))),
    **_each("money", "start_amount task_budget", _amount),
    "money.share_range": _pair(_positive),
    "money.share_decimals": _int(0, 2),
    "value.q0": _num("a number, 0 <= q0 < 10", lambda x: 0 <= x < 10),
    "value.exponent": _positive,
    "value.late_decay": _num("a number, 0 < late_decay <= 1", lambda x: 0 < x <= 1),
    **_each("contracts", "bid_window_rounds min_contract_days message_chars", _int(1)),
    "contracts.escrow.fee_rate": _num("a number, 0 <= fee_rate <= 1", lambda x: 0 <= x <= 1),
    **_each("contracts.escrow", "review_days dispute_window_days", _int(1)),
    "contracts.escrow.dispute_fee_rate": _positive,
    "contracts.escrow.arbiter_sd": _non_negative,
    **_each("production", "cost_ref_worth sig_c", _positive),
    "production.sig_t": _non_negative,
    **{f"production.{w}{k}": rule for w in ("employee", "manager") for k, rule in _worker.items()},
    **_each("production.employee", "fee cost_frac ref_cost_frac", _positive),
    **_each("production.manager", "k ref_cost_fixed", _positive),
    **_each("tests", "manager_sd buyer_sd", _positive),
    **_each("tests", "manager_cost buyer_cost", _non_negative),
    "tests.decimals": _int(0, 3),
    "messaging.message_chars": _int(1),
    "messaging.recent": _int(2),
    "messaging.summary_chars": _int(50),
    **_each("compute", "cpu_s wall_s memory_mb output_kb output_chars code_chars", _int(1)),
    **_each("view", "finished_shown open_shown readings_shown overdue_days recent_days messages_shown "
                    "payments_shown overflow_ids", _int(1)),
}


def _merge(defaults, given, prefix):
    """Section 1, items 3 to 5: merge a mapping of the file into the defaults, checking every key."""
    if type(given) is not dict:
        _fail(prefix.rstrip(".") or "the config", "must be a mapping")
    for k in given:
        if k not in defaults:
            _fail(f"{prefix}{_key(k)}", "unknown key")
    merged = {}
    for k, default in defaults.items():
        path = f"{prefix}{k}"
        if path in _SCHEMA:
            merged[k] = _SCHEMA[path](path, given[k] if k in given else default)
        else:
            merged[k] = _merge(default, given[k] if k in given else {}, path + ".")
    return merged


_RUNS = (("employee", "fresh", "t_med"), ("employee", "refine", "t_ref_med"),
        ("manager", "fresh", "t_med"), ("manager", "refine", "t_ref_med"))


def _check(cfg):
    """The rules across keys of 3.8 and section 4, and section 6's "Finiteness at load"."""
    low, high = cfg["money"]["share_range"]
    decimals = cfg["money"]["share_decimals"]
    if low == high != 1:
        _fail("money.share_range", "low = high only as [1, 1]")
    if any(round(x, decimals) != x or float(G(x)) != x for x in (low, high)):
        _fail("money.share_range, money.share_decimals",
              "each end must have at most share_decimals decimals and at most 6 significant digits")

    t = cfg["tests"]
    if not (t["manager_sd"] < t["buyer_sd"] and t["manager_cost"] == t["buyer_cost"]):
        _fail("tests.manager_sd, tests.buyer_sd, tests.manager_cost, tests.buyer_cost",
              "manager_sd must be below buyer_sd, and manager_cost must equal buyer_cost")

    p = cfg["production"]
    cost = {(w, kind): expected_cost(cfg, w, kind) for w, kind, _ in _RUNS}
    keys = ("production.cost_ref_worth, production.employee.fee, production.employee.cost_frac, "
            "production.employee.ref_cost_frac, production.manager.k, production.manager.ref_cost_fixed")
    if not all(map(math.isfinite, cost.values())):
        _fail(keys, "the expected run costs must be finite")
    for kind in ("fresh", "refine"):
        if not cost["employee", kind] < cost["manager", kind]:
            _fail(keys, f"the employee's expected {kind} cost must be below the manager's own")

    for w in ("employee", "manager"):
        mu, sd = p[w]["mu"], p[w]["sd"]
        if not Phi((10 - mu) / sd) - Phi((0 - mu) / sd) > 0:
            _fail(f"production.{w}.mu, production.{w}.sd",
                  "the normal's mass between 0 and 10 must be above 0 in double precision")

    mixes = {f"production.{w}.{key}": days_mix(cfg, p[w][key]) for w, _, key in _RUNS}
    for path, mix in mixes.items():
        if not all(math.isfinite(share) for _, share in mix):
            _fail(f"production.sig_t, {path}", "the days mix must be finite")
        if not any(d != 1000 and share >= 0.05 for d, share in mix):
            _fail(f"production.sig_t, {path}", "some day count other than 1000 must have a share >= 0.05")
    if not any(d > 1 and share > 0 for mix in mixes.values() for d, share in mix):
        _fail("production.sig_t, " + ", ".join(mixes), "some kind of run must be able to take more than 1 day")

    try:
        close_days = escrow_close_days(cfg)
    except OverflowError:
        _fail("value.late_decay", "escrow_close_days must be finite")
    if cfg["time"]["close_after_due_days"] < close_days:
        _fail("time.close_after_due_days, value.late_decay",
              f"close_after_due_days must be >= escrow_close_days ({close_days})")

    cap = horizon_cap(cfg)
    if cap > 1000:
        _fail("time.horizon_min_days, time.horizon_end_prob", "the horizon cap must be at most 1000")
    if cfg["contracts"]["bid_window_rounds"] > cap * cfg["time"]["rounds_per_day"]:
        _fail("contracts.bid_window_rounds",
              f"must be <= the horizon cap x time.rounds_per_day ({cap * cfg['time']['rounds_per_day']})")

    for role in ("buyer", "manager"):
        count = str(cfg["participants"][f"{role}s"])
        for seat in cfg["models"][f"{role}_seats"]:
            if (len(seat), seat) >= (len(count), count):  # the order of whole numbers, on their decimal digits
                _fail(f"models.{role}_seats.{seat}", f"must be a seat index below participants.{role}s")


def resolve(raw, *, seeds=None, cost_cap=None, out=None, parallel=None):
    """Section 1: the file's mapping merged into the defaults and checked; then running.md 1's options.

    Every model spec comes back with the temperature, max_tokens and extra_body its calls use (3.4.1, "when
    absent"), and the seat mappings with each seat index as its decimal digits.
    """
    cfg = _merge(DEFAULTS, {} if raw is None else raw, "")
    models, llm = cfg["models"], cfg["llm"]
    agents = [models["buyers"], models["managers"], *models["buyer_seats"].values(),
              *models["manager_seats"].values()]
    for spec in agents:
        spec.setdefault("temperature", llm["temperature"])
    models["summarizer"].setdefault("temperature", None)
    for spec in (*agents, models["summarizer"]):
        spec.setdefault("max_tokens", llm["max_tokens"])
        spec.setdefault("extra_body", {})
    _check(cfg)
    options = {"seeds": seeds, "cost_cap_usd": cost_cap, "output_dir": out, "parallel_episodes": parallel}
    for key, value in options.items():
        if value is not None:
            cfg[key] = _SCHEMA[key](key, value)
    return cfg


def load(path, **overrides):
    """Section 1, items 1 and 2: read the YAML file and resolve it; a file that cannot be read is refused."""
    try:
        with open(path, "rb") as f:
            raw = yaml.safe_load(f)
    except (OSError, ValueError, RecursionError, yaml.YAMLError) as e:  # too long an integer; nested too deep
        raise ConfigError(f"{path}: the config file cannot be read: {e}") from e
    return resolve(raw, **overrides)


def seat_spec(cfg, role, seat):
    """3.4.2: the seat's entry in buyer_seats / manager_seats if there is one, else its role's spec."""
    return cfg["models"][f"{role}_seats"].get(str(seat), cfg["models"][f"{role}s"])


def horizon_cap(cfg):
    """6.1: horizon_min_days + n, n the smallest whole number with (1 - horizon_end_prob)^n < 0.01."""
    log_stay = math.log1p(-cfg["time"]["horizon_end_prob"])
    x = math.log(0.01) / log_stay
    if not x < 1000:
        n = 1001
    else:
        n = max(0, math.ceil(x))
        while n <= 1000 and math.exp(n * log_stay) >= 0.01:
            n += 1
    return cfg["time"]["horizon_min_days"] + n


def escrow_close_days(cfg):
    """6.2: ceil(1 / late_decay - 1e-9)."""
    return math.ceil(1 / cfg["value"]["late_decay"] - 1e-9)


def full_pay_cap(cfg, due_day):
    """6.3: the due day + escrow_close_days - 1."""
    return due_day + escrow_close_days(cfg) - 1


def deadline(cfg, due_day, signing_day):
    """6.3: a contract's deadline, in whole days."""
    return min(max(due_day, signing_day + cfg["contracts"]["min_contract_days"]), full_pay_cap(cfg, due_day))


def _share_of(cfg, key, share, name):
    """6.4: the standard amount x the share; section 6, "Finiteness during an episode": never an infinite amount."""
    standard = cfg["money"][key]
    amount = standard if share == 1 else round(standard * share, 2)
    if not math.isfinite(amount):
        raise ArithmeticError(f"the {name} is not finite: money.{key} {standard} x share {share}")
    return amount


def start_balance(cfg, share):
    """6.4: a participant's start balance for its wealth share."""
    return _share_of(cfg, "start_amount", share, "start balance")


def per_task_budget(cfg, share):
    """6.4: a buyer's per-task budget for its wealth share."""
    return _share_of(cfg, "task_budget", share, "per-task budget")


def expected_cost(cfg, worker, kind):
    """6.5: the cost of a run with multiplier 1; worker employee or manager, kind fresh or refine."""
    return rng.cost(cfg["production"], worker, kind)


def round_index(cfg, day, round):
    """6.6: g = (day - 1) x rounds_per_day + round."""
    return (day - 1) * cfg["time"]["rounds_per_day"] + round


def opening_round(cfg, g):
    """6.6: the round index from which a bid on a post made in round index g can be accepted."""
    return g + cfg["contracts"]["bid_window_rounds"] + 1


def last_possible_day(cfg, last_arrival_day, latest_due_day):
    """6.6: the market's last possible day."""
    return max(last_arrival_day, latest_due_day + cfg["time"]["close_after_due_days"] - 1)


def days_mix(cfg, t):
    """6.7: the (day count, share) pairs of a run with median t days, in increasing day count."""
    s = cfg["production"]["sig_t"]
    mix, below, d = [], 0.0, 1
    while below < 1 - 1e-9 and d < 1000:
        cum = Phi(math.log(d / t) / s) if s > 0 else 1.0 if t <= d else 0.0
        mix.append((d, cum - below))
        below, d = cum, d + 1
    if below < 1 - 1e-9:
        mix.append((1000, 1 - below))
    return mix


def lost_turn_limit(cfg):
    """6.8: the episode is aborted when more turns than this are lost to failed model calls."""
    return 5 * cfg["time"]["rounds_per_day"]
