"""Blind specification tests. No test creates or changes a file."""

import builtins
import copy
import datetime
import io
import json
import math
from pathlib import Path
from unittest.mock import mock_open

import pytest
import yaml

from market import config


DEFAULT_YAML = """
seeds: [0, 1, 2]
parallel_episodes: 4
cost_cap_usd: 20
output_dir: runs
name: market
message_channels: "off"
participants: {buyers: 2, managers: 3}
models:
  buyers:
    provider: openrouter
    model: qwen/qwen3.8-27b
    price_in_per_m: 0.15
    price_out_per_m: 1.875
    extra_body: {provider: {quantizations: [bf16, fp16, fp8, unknown]}}
  managers:
    provider: openrouter
    model: qwen/qwen3.8-27b
    price_in_per_m: 0.15
    price_out_per_m: 1.875
    extra_body: {provider: {quantizations: [bf16, fp16, fp8, unknown]}}
  buyer_seats: {}
  manager_seats: {}
  summarizer:
    provider: openrouter
    model: anthropic/claude-sonnet-5.5
    price_in_per_m: 2.0
    price_out_per_m: 10.0
    temperature: null
    max_tokens: 8000
    extra_body: {reasoning: {effort: high}}
llm:
  temperature: 0.8
  max_tokens: 64000
  timeout_s: 1200
  max_retries: 5
  network_patience_s: 900
credits_per_usd: 1
time:
  rounds_per_day: 3
  horizon_min_days: 10
  horizon_end_prob: 0.1
  close_after_due_days: 5
turns: {max_actions: 6, memory_chars: 2000}
tasks:
  first_day_tasks: 2
  arrival_p: 0.5
  worth_range: [40, 80]
  due_days_range: [4, 9]
money:
  start_amount: 60
  task_budget: 30
  share_range: [0.6, 1.4]
  share_decimals: 2
value: {q0: 1, exponent: 2, late_decay: 0.2}
contracts:
  bid_window_rounds: 1
  min_contract_days: 4
  message_chars: 500
  escrow:
    fee_rate: 0.10
    review_days: 2
    dispute_window_days: 1
    dispute_fee_rate: 0.5
    arbiter_sd: 0.5
production:
  cost_ref_worth: 80
  sig_t: 0.35
  sig_c: 0.25
  employee:
    mu: 6.0
    sd: 2.5
    gain: 4.0
    sd_ref: 1.5
    t_med: 1.0
    t_ref_med: 0.7
    fee: 1.0
    cost_frac: 0.0375
    ref_cost_frac: 0.0225
    capacity: 2
  manager:
    mu: 7.0
    sd: 1.2
    gain: 7.0
    sd_ref: 0.8
    t_med: 2.5
    t_ref_med: 1.0
    k: 0.1
    ref_cost_fixed: 14.0
    capacity: 1
tests:
  manager_sd: 0.5
  manager_cost: 1
  buyer_sd: 2.0
  buyer_cost: 1
  decimals: 1
messaging: {message_chars: 500, recent: 10, summary_chars: 2000}
compute:
  cpu_s: 5
  wall_s: 10
  memory_mb: 512
  output_kb: 64
  output_chars: 4000
  code_chars: 20000
view:
  finished_shown: 5
  open_shown: 8
  readings_shown: 5
  overdue_days: 5
  recent_days: 3
  messages_shown: 3
  payments_shown: 8
  overflow_ids: 20
"""

DEFAULT = yaml.safe_load(DEFAULT_YAML)

# Section 3: integer fields and their individual lower/upper bounds.
INTEGER_RULES = {
    "parallel_episodes": (1, None),
    "participants.buyers": (2, 4096),
    "participants.managers": (2, 4096),
    "llm.max_tokens": (1, None),
    "llm.max_retries": (0, None),
    "time.rounds_per_day": (1, None),
    "time.horizon_min_days": (1, None),
    "time.close_after_due_days": (1, None),
    "turns.max_actions": (1, None),
    "turns.memory_chars": (1, None),
    "tasks.first_day_tasks": (2, None),
    "money.share_decimals": (0, 2),
    "contracts.bid_window_rounds": (1, None),
    "contracts.min_contract_days": (1, None),
    "contracts.message_chars": (1, None),
    "contracts.escrow.review_days": (1, None),
    "contracts.escrow.dispute_window_days": (1, None),
    "production.employee.capacity": (1, None),
    "production.manager.capacity": (1, None),
    "tests.decimals": (0, 3),
    "messaging.message_chars": (1, None),
    "messaging.recent": (2, None),
    "messaging.summary_chars": (50, None),
    "compute.cpu_s": (1, None),
    "compute.wall_s": (1, None),
    "compute.memory_mb": (1, None),
    "compute.output_kb": (1, None),
    "compute.output_chars": (1, None),
    "compute.code_chars": (1, None),
    "view.finished_shown": (1, None),
    "view.open_shown": (1, None),
    "view.readings_shown": (1, None),
    "view.overdue_days": (1, None),
    "view.recent_days": (1, None),
    "view.messages_shown": (1, None),
    "view.payments_shown": (1, None),
    "view.overflow_ids": (1, None),
}

POSITIVE_NUMBERS = [
    "cost_cap_usd",
    "credits_per_usd",
    "llm.timeout_s",
    "value.exponent",
    "contracts.escrow.dispute_fee_rate",
    "production.cost_ref_worth",
    "production.sig_c",
    "production.employee.sd",
    "production.employee.sd_ref",
    "production.employee.t_med",
    "production.employee.t_ref_med",
    "production.employee.fee",
    "production.employee.cost_frac",
    "production.employee.ref_cost_frac",
    "production.manager.sd",
    "production.manager.sd_ref",
    "production.manager.t_med",
    "production.manager.t_ref_med",
    "production.manager.k",
    "production.manager.ref_cost_fixed",
    "tests.manager_sd",
    "tests.buyer_sd",
]

NONNEGATIVE_NUMBERS = [
    "llm.temperature",
    "llm.network_patience_s",
    "production.sig_t",
    "contracts.escrow.arbiter_sd",
    "tests.manager_cost",
    "tests.buyer_cost",
]

BOUNDED_NUMBERS = {
    "time.horizon_end_prob": (0, 1, False, False),
    "tasks.arrival_p": (0, 1, False, False),
    "money.start_amount": (0, 1_000_000, False, False),
    "money.task_budget": (0, 1_000_000, False, False),
    "value.q0": (0, 10, True, False),
    "value.late_decay": (0, 1, False, True),
    "contracts.escrow.fee_rate": (0, 1, True, True),
    "production.employee.mu": (0, 10, True, True),
    "production.manager.mu": (0, 10, True, True),
    "production.employee.gain": (0, 10, False, True),
    "production.manager.gain": (0, 10, False, True),
}

NUMBER_PATHS = POSITIVE_NUMBERS + NONNEGATIVE_NUMBERS + list(BOUNDED_NUMBERS)
MEDIANS = [
    "production.employee.t_med",
    "production.employee.t_ref_med",
    "production.manager.t_med",
    "production.manager.t_ref_med",
]
MODEL_PATHS = [
    "models.buyers",
    "models.managers",
    "models.summarizer",
    "models.buyer_seats.0",
    "models.manager_seats.0",
]
AGENT_PATHS = [p for p in MODEL_PATHS if p != "models.summarizer"]


def get_at(mapping, path):
    for part in path.split("."):
        mapping = mapping[part]
    return mapping


def set_at(mapping, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        mapping = mapping.setdefault(part, {})
    mapping[parts[-1]] = value


def raw_with(path, value):
    result = {}
    set_at(result, path, value)
    return result


def resolved_with(path, value):
    return config.resolve(raw_with(path, value))


def assert_rejected(raw, **options):
    with pytest.raises(config.ConfigError):
        config.resolve(raw, **options)


def expected_defaults():
    result = copy.deepcopy(DEFAULT)
    for path in NUMBER_PATHS:
        set_at(result, path, float(get_at(result, path)))
    result["money"]["share_range"] = [0.6, 1.4]
    for role in ("buyers", "managers"):
        result["models"][role]["temperature"] = 0.8
        result["models"][role]["max_tokens"] = 64000
    return result


def assert_typed_equal(actual, expected, *, ordered=True):
    assert type(actual) is type(expected)
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        if ordered:
            assert list(actual) == list(expected)
        for key in expected:
            # The schema orders blocks, but does not order injected model keys.
            child_ordered = ordered and key != "models"
            assert_typed_equal(actual[key], expected[key], ordered=child_ordered)
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected):
            assert_typed_equal(a, e, ordered=ordered)
    else:
        assert actual == expected


def mutable_ids(value):
    result = set()
    if isinstance(value, dict):
        result.add(id(value))
        for item in value.values():
            result.update(mutable_ids(item))
    elif isinstance(value, list):
        result.add(id(value))
        for item in value:
            result.update(mutable_ids(item))
    return result


def fake_file(monkeypatch, contents=None, error=None):
    """Intercept common read APIs, using memory only."""
    if error is None:
        opener = mock_open(read_data=contents)

        def read_text(self, *args, **kwargs):
            return contents
    else:
        opener = mock_open()
        opener.side_effect = error

        def read_text(self, *args, **kwargs):
            raise error

    monkeypatch.setattr(builtins, "open", opener)
    monkeypatch.setattr(io, "open", opener)
    monkeypatch.setattr(Path, "open", opener)
    monkeypatch.setattr(Path, "read_text", read_text)
    # Also cover a module-local import of open without inspecting its source.
    monkeypatch.setattr(config, "open", opener, raising=False)
    return opener


def reference_mix(s, t):
    """Section 6.7's explicit algorithm, independent of the tested module."""
    result = []
    below = 0.0
    for day in range(1, 1000):
        if below >= 1.0 - 1e-9:
            break
        if s == 0:
            cumulative = 1.0 if t <= day else 0.0
        else:
            z = math.log(day / t) / s
            cumulative = math.erfc(-z / math.sqrt(2.0)) / 2.0
        result.append((day, cumulative - below))
        below = cumulative
    if below < 1.0 - 1e-9:
        result.append((1000, 1.0 - below))
    return result


def test_constants_and_every_default():
    # Sections 2, 3.2, 3.4; API constants and resolved types.
    assert_typed_equal(config.DEFAULTS, DEFAULT)
    assert config.MODES == ("off", "managers", "buyers", "within_roles", "all")
    assert config.PROVIDERS == ("openrouter", "openai_compatible", "ollama", "mock")
    assert config.FREE_PROVIDERS == ("mock", "ollama")
    for raw in (None, {}):
        cfg = config.resolve(raw)
        assert_typed_equal(cfg, expected_defaults())
        assert list(cfg["models"]) == list(DEFAULT["models"])


def test_deep_merge_and_list_replacement():
    # Section 1.3: merge ordinary mappings recursively; replace lists.
    raw = {
        "seeds": [19],
        "contracts": {"escrow": {"review_days": 9}},
        "production": {"employee": {"capacity": 8}},
        "tasks": {"worth_range": [3, 7]},
        "llm": {"max_retries": 0},
    }
    expected = expected_defaults()
    expected["seeds"] = [19]
    expected["contracts"]["escrow"]["review_days"] = 9
    expected["production"]["employee"]["capacity"] = 8
    expected["tasks"]["worth_range"] = [3, 7]
    expected["llm"]["max_retries"] = 0
    assert_typed_equal(config.resolve(raw), expected)


def test_no_input_mutation_or_shared_mutable_objects():
    # Section 1.3; API purity, independent resolved configs, JSON stability.
    raw = {
        "seeds": [8, 3],
        "models": {
            "buyer_seats": {
                0: {"provider": "mock", "extra_body": {"nested": [1, {"x": True}]}}
            }
        },
    }
    before_raw = copy.deepcopy(raw)
    before_defaults = copy.deepcopy(config.DEFAULTS)
    a = config.resolve(raw)
    b = config.resolve(raw)
    assert raw == before_raw
    assert config.DEFAULTS == before_defaults
    assert not mutable_ids(a) & mutable_ids(raw)
    assert not mutable_ids(a) & mutable_ids(config.DEFAULTS)
    assert not mutable_ids(a) & mutable_ids(b)
    assert json.loads(json.dumps(a, allow_nan=False)) == a
    assert config.resolve(a) == a
    a["models"]["buyer_seats"]["0"]["extra_body"]["nested"].append("changed")
    assert raw == before_raw
    assert b["models"]["buyer_seats"]["0"]["extra_body"]["nested"] == [1, {"x": True}]


@pytest.mark.parametrize("raw", [False, True, 0, 1.5, "", "null", [], [1]])
def test_top_level_requires_mapping(raw):
    # Section 1.1: only a mapping or null is a config.
    assert_rejected(raw)


BLOCKS = [
    key for key, value in DEFAULT.items() if isinstance(value, dict)
] + ["contracts.escrow", "production.employee", "production.manager"]


@pytest.mark.parametrize("path", BLOCKS + MODEL_PATHS)
@pytest.mark.parametrize("bad", [None, False, 1, "mapping", []])
def test_mapping_blocks_and_model_specs_cannot_be_scalars(path, bad):
    # Section 1.3: mapping defaults cannot be replaced by scalars or lists.
    assert_rejected(raw_with(path, bad))


@pytest.mark.parametrize("path", ["models.buyer_seats", "models.manager_seats"])
@pytest.mark.parametrize("bad", [False, 1, "mapping", []])
def test_seat_maps_require_mapping_or_null(path, bad):
    # Sections 1.3, 3.4.2: null is the sole non-mapping seat-map exception.
    assert_rejected(raw_with(path, bad))


@pytest.mark.parametrize("path", ["models.buyer_seats", "models.manager_seats"])
def test_null_seat_map_is_empty(path):
    # Sections 1.3, 3.4.2.
    assert get_at(resolved_with(path, None), path) == {}


UNKNOWN_PARENTS = [""] + BLOCKS + MODEL_PATHS


@pytest.mark.parametrize("parent", UNKNOWN_PARENTS)
def test_unknown_keys_at_every_schema_depth(parent):
    # Sections 1.4 and 1.6: unknown keys report the full key path.
    raw = {}
    if parent in MODEL_PATHS:
        set_at(raw, parent, {"provider": "mock"})
    path = f"{parent}.unexpected" if parent else "unexpected"
    set_at(raw, path, 1)
    with pytest.raises(config.ConfigError) as exc:
        config.resolve(raw)
    assert path in str(exc.value).split(":", 1)[0]


@pytest.mark.parametrize(
    "path",
    [
        "horizon_cap",
        "escrow_close_days",
        "lost_turn_limit",
        "last_arrival_day",
        "participants.ids",
        "money.shares",
        "production.quality_max",
        "compute.successes_per_turn",
        "messaging.summary_words",
    ],
)
def test_fixed_and_drawn_values_are_not_configuration_keys(path):
    # Sections 1.7 and 5: derived, drawn and fixed values are not parameters.
    assert_rejected(raw_with(path, 1))


@pytest.mark.parametrize("path", INTEGER_RULES)
@pytest.mark.parametrize("bad", [True, False, 3.0, 3.5, "3", None, [], {}])
def test_integer_fields_require_actual_integers(path, bad):
    # Sections 1.5 and 3: booleans and decimal-written integers are refused.
    assert_rejected(raw_with(path, bad))


@pytest.mark.parametrize("path,bounds", INTEGER_RULES.items())
def test_integer_lower_and_upper_bounds(path, bounds):
    # Sections 3.1–3.15 and 4: inclusive integer bounds.
    low, high = bounds
    assert_rejected(raw_with(path, low - 1))
    raw = raw_with(path, low)
    if path == "money.share_decimals":
        raw["money"]["share_range"] = [1, 1]
    if path == "time.close_after_due_days":
        raw["value"] = {"late_decay": 1}
    cfg = config.resolve(raw)
    assert get_at(cfg, path) == low
    assert type(get_at(cfg, path)) is int
    if high is not None:
        raw = raw_with(path, high)
        if path == "money.share_decimals":
            raw["money"]["share_range"] = [1, 1]
        assert get_at(config.resolve(raw), path) == high
        assert_rejected(raw_with(path, high + 1))


@pytest.mark.parametrize(
    "path",
    [
        p for p, (_, high) in INTEGER_RULES.items()
        if high is None
        and p not in ("time.horizon_min_days", "contracts.bid_window_rounds")
    ],
)
def test_unbounded_integer_fields_do_not_acquire_machine_integer_limits(path):
    # Sections 3 and 6: integer arithmetic is exact, without a machine-size cap.
    huge = 10**100
    assert get_at(resolved_with(path, huge), path) == huge


@pytest.mark.parametrize("path", NUMBER_PATHS)
@pytest.mark.parametrize(
    "bad", [True, False, "1", float("nan"), float("inf"), -float("inf"), [], {}, 10**400]
)
def test_number_fields_require_finite_non_boolean_numbers(path, bad):
    # Section 1.5: numbers must be finite IEEE-754 doubles.
    assert_rejected(raw_with(path, bad))


@pytest.mark.parametrize(
    "path", [p for p in NUMBER_PATHS if p not in ("cost_cap_usd", "llm.temperature")]
)
def test_number_fields_without_null_exception_reject_null(path):
    # Sections 1.5 and 3: only explicitly nullable number fields accept null.
    assert_rejected(raw_with(path, None))


@pytest.mark.parametrize("path", POSITIVE_NUMBERS)
@pytest.mark.parametrize("bad", [0, -0.0, -1])
def test_strictly_positive_number_ranges(path, bad):
    # Section 3: positive means strictly greater than zero.
    assert_rejected(raw_with(path, bad))


@pytest.mark.parametrize("path", NONNEGATIVE_NUMBERS)
def test_nonnegative_number_ranges(path):
    # Section 3: zero is allowed; coupled test costs must remain equal.
    assert_rejected(raw_with(path, -0.01))
    raw = raw_with(path, 0)
    if path in ("tests.manager_cost", "tests.buyer_cost"):
        raw["tests"] = {"manager_cost": 0, "buyer_cost": 0}
    cfg = config.resolve(raw)
    assert get_at(cfg, path) == 0.0
    assert type(get_at(cfg, path)) is float


@pytest.mark.parametrize("path,rule", BOUNDED_NUMBERS.items())
def test_bounded_number_endpoints(path, rule):
    # Section 3: open and closed numeric range endpoints.
    low, high, include_low, include_high = rule
    assert_rejected(raw_with(path, low - 1))
    assert_rejected(raw_with(path, high + 1))
    for value, allowed in [(low, include_low), (high, include_high)]:
        if allowed:
            cfg = resolved_with(path, value)
            assert get_at(cfg, path) == float(value)
            assert type(get_at(cfg, path)) is float
        else:
            assert_rejected(raw_with(path, value))


@pytest.mark.parametrize(
    "path",
    [p for p in NUMBER_PATHS if float(get_at(DEFAULT, p)).is_integer()],
)
def test_integer_and_float_inputs_have_identical_number_semantics(path):
    # Section 1.5: integer-written number parameters behave as doubles.
    value = int(get_at(DEFAULT, path))
    a = resolved_with(path, value)
    b = resolved_with(path, float(value))
    assert a == b
    assert type(get_at(a, path)) is float


@pytest.mark.parametrize("path", ["money.start_amount", "money.task_budget"])
@pytest.mark.parametrize("value", [1, 1.0, 999999, 999999.0])
def test_standard_money_accepts_whole_numbers_in_both_numeric_forms(path, value):
    # Section 3.8: whole-number amounts, represented as doubles by the API.
    assert get_at(resolved_with(path, value), path) == float(value)


@pytest.mark.parametrize("path", ["money.start_amount", "money.task_budget"])
@pytest.mark.parametrize("value", [0.5, 1.25, 999999.5])
def test_standard_money_refuses_fractional_amounts(path, value):
    # Section 3.8: whole-number requirement is stronger than numeric typing.
    assert_rejected(raw_with(path, value))


@pytest.mark.parametrize("value", [None, 0.01, 1, 1e100])
def test_optional_cost_cap(value):
    # Section 3.1: null disables the cap; positive finite amounts are accepted.
    expected = None if value is None else float(value)
    assert resolved_with("cost_cap_usd", value)["cost_cap_usd"] == expected


@pytest.mark.parametrize("value", [None, 0, 0.25, 100])
def test_nullable_global_temperature(value):
    # Section 3.4.3: null omits temperature; no positive upper bound is specified.
    cfg = resolved_with("llm.temperature", value)
    expected = None if value is None else float(value)
    assert cfg["llm"]["temperature"] == expected
    assert cfg["models"]["buyers"]["temperature"] == expected


@pytest.mark.parametrize(
    "bad", [None, [], [1, 1], [-1], [True], [False], [1.0], ["1"], {}, "1,2", (1, 2)]
)
def test_seed_validation(bad):
    # Section 3.1: nonempty list of distinct nonnegative integers.
    assert_rejected({"seeds": bad})


def test_seed_order_and_arbitrarily_large_seed_are_preserved():
    # Section 3.1; running.md §2: episodes retain the supplied seed order.
    seeds = [9, 0, 10**100, 2]
    assert config.resolve({"seeds": seeds})["seeds"] == seeds


@pytest.mark.parametrize("path", ["name", "output_dir"])
@pytest.mark.parametrize("bad", ["", None, False, 12, [], {}])
def test_run_strings(path, bad):
    # Section 3.1: names and output directories are nonempty strings.
    assert_rejected(raw_with(path, bad))


@pytest.mark.parametrize("bad", [".", "..", "a/b", "a\\b", "\0", "a\0b"])
def test_name_must_be_a_single_folder(bad):
    # Section 3.1: exact forbidden folder-name cases.
    assert_rejected({"name": bad})


@pytest.mark.parametrize("name", ["market", ".hidden", "...", "a b", "é😀", " "])
def test_valid_name_is_not_trimmed_or_restricted_to_ascii(name):
    # Section 3.1: no trimming or ASCII-only restriction is specified.
    assert config.resolve({"name": name})["name"] == name


@pytest.mark.parametrize("directory", [".", "..", "relative/path", "/absolute/path", " "])
def test_output_directory_is_preserved(directory):
    # Section 3.1; API: resolve does not implement CLI path resolution.
    assert config.resolve({"output_dir": directory})["output_dir"] == directory


@pytest.mark.parametrize("mode", ["off", "managers", "buyers", "within_roles", "all", False])
def test_all_message_modes(mode):
    # Sections 1.5 and 3.2: false is the sole non-string spelling accepted.
    cfg = config.resolve({"message_channels": mode})
    assert cfg["message_channels"] == ("off" if mode is False else mode)


@pytest.mark.parametrize("bad", [True, 0, 1, None, [], {}, "OFF", "within-roles", ""])
def test_invalid_message_modes(bad):
    # Section 3.2: the five mode strings are exact.
    assert_rejected({"message_channels": bad})


@pytest.mark.parametrize("path", ["tasks.worth_range", "tasks.due_days_range", "money.share_range"])
@pytest.mark.parametrize("bad", [None, [], [1], [1, 2, 3], (1, 2), "1,2", {"low": 1}])
def test_pair_shape(path, bad):
    # Section 1.5: pairs must be lists of exactly two elements.
    assert_rejected(raw_with(path, bad))


@pytest.mark.parametrize("path", ["tasks.worth_range", "tasks.due_days_range"])
@pytest.mark.parametrize("bad", [True, False, 1.0, 1.5, "1", None, float("inf")])
@pytest.mark.parametrize("position", [0, 1])
def test_integer_pair_element_types(path, bad, position):
    # Sections 1.5 and 3.7.
    pair = [1, 2]
    pair[position] = bad
    assert_rejected(raw_with(path, pair))


@pytest.mark.parametrize("path", ["tasks.worth_range", "tasks.due_days_range"])
@pytest.mark.parametrize("pair", [[0, 1], [-1, 2], [2, 1]])
def test_task_pair_ranges(path, pair):
    # Section 3.7: positive, ordered, inclusive integer endpoints.
    assert_rejected(raw_with(path, pair))


@pytest.mark.parametrize("path", ["tasks.worth_range", "tasks.due_days_range"])
@pytest.mark.parametrize("pair", [[1, 1], [1, 2], [10**100, 10**100 + 1]])
def test_task_pair_equal_and_large_endpoints(path, pair):
    # Section 3.7: equal endpoints and arbitrarily large integers are allowed.
    assert get_at(resolved_with(path, pair), path) == pair


@pytest.mark.parametrize("bad", [True, False, "1", None, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("position", [0, 1])
def test_share_pair_element_types(bad, position):
    # Sections 1.5 and 3.8: share endpoints are finite numbers, not booleans.
    pair = [0.6, 1.4]
    pair[position] = bad
    assert_rejected({"money": {"share_range": pair}})


@pytest.mark.parametrize(
    "pair,decimals",
    [
        ([0, 1], 2),
        ([-1, 1], 2),
        ([2, 1], 2),
        ([0.5, 0.5], 2),
        ([2, 2], 2),
        ([0.601, 1.4], 2),
        ([0.6, 1.401], 2),
        ([0.6, 1.4], 0),
        ([0.61, 1.4], 1),
        ([1, 12345.67], 2),
        ([1.234567, 2], 2),
    ],
)
def test_share_range_precision_and_order(pair, decimals):
    # Section 3.8; README G: decimal-place and six-significant-digit checks.
    assert_rejected({"money": {"share_range": pair, "share_decimals": decimals}})


@pytest.mark.parametrize(
    "pair,decimals",
    [
        ([1, 1], 0),
        ([1, 1], 1),
        ([1, 1], 2),
        ([1, 2], 0),
        ([0.1, 1.4], 1),
        ([0.01, 1.01], 2),
        ([1, 9999.99], 2),
        ([1e6, 2e6], 0),
        ([1e308, 1.1e308], 2),
    ],
)
def test_valid_share_ranges(pair, decimals):
    # Sections 3.8 and 6: finite endpoints have no separate upper range bound.
    cfg = config.resolve({"money": {"share_range": pair, "share_decimals": decimals}})
    assert cfg["money"]["share_range"] == [float(x) for x in pair]
    assert all(type(x) is float for x in cfg["money"]["share_range"])


@pytest.mark.parametrize(
    "value",
    [0.01, 0.99, 1 / 3, 2 / 7, 0.5 + 9e-13, 0.5 - 9e-13, 83 / 85 + 9.9e-13],
)
def test_arrival_probability_fraction_tolerance_accepts(value):
    # Section 3.7; prompts.md §7: use the supplied double, without snapping.
    assert resolved_with("tasks.arrival_p", value)["tasks"]["arrival_p"] == value


@pytest.mark.parametrize("value", [1 / 101, 0.123456, 0.5 + 2e-12, 0.5 - 2e-12])
def test_arrival_probability_fraction_tolerance_rejects(value):
    # Section 3.7: within 1e-12 of a fraction with denominator at most 100.
    assert_rejected({"tasks": {"arrival_p": value}})


@pytest.mark.parametrize("path", MODEL_PATHS)
def test_model_spec_replaces_defaults_and_role_as_a_whole(path):
    # Sections 1.3 and 3.4.2: mock replacement inherits only call defaults.
    cfg = resolved_with(path, {"provider": "mock"})
    expected = {
        "provider": "mock",
        "temperature": None if path == "models.summarizer" else 0.8,
        "max_tokens": 64000,
        "extra_body": {},
    }
    assert get_at(cfg, path) == expected


@pytest.mark.parametrize("path", MODEL_PATHS)
def test_model_null_keys_are_absent_except_documented_exceptions(path):
    # Section 1.3: null optional keys disappear; temperature/API-key nulls remain.
    spec = {
        "provider": "mock",
        "model": None,
        "price_in_per_m": None,
        "price_out_per_m": None,
        "extra_body": None,
        "base_url": None,
        "temperature": None,
        "api_key_env": None,
    }
    actual = get_at(resolved_with(path, spec), path)
    assert actual == {
        "provider": "mock",
        "temperature": None,
        "api_key_env": None,
        "max_tokens": 64000,
        "extra_body": {},
    }


@pytest.mark.parametrize("path", MODEL_PATHS)
@pytest.mark.parametrize("spec", [{}, {"provider": None}, {"provider": False}, {"provider": 1}])
def test_provider_is_required_and_string_typed(path, spec):
    # Section 3.4.1: every model spec requires a provider.
    assert_rejected(raw_with(path, spec))


@pytest.mark.parametrize("path", MODEL_PATHS)
@pytest.mark.parametrize("provider", ["openrouter", "openai_compatible", "ollama"])
@pytest.mark.parametrize("model", [None, "", False, 1, [], {}])
def test_non_mock_providers_require_nonempty_model(path, provider, model):
    # Section 3.4.1: mock alone may omit the model identifier.
    spec = {
        "provider": provider,
        "model": model,
        "base_url": "http://example.invalid/v1",
        "price_in_per_m": 1,
        "price_out_per_m": 2,
    }
    assert_rejected(raw_with(path, spec))


@pytest.mark.parametrize("path", MODEL_PATHS)
@pytest.mark.parametrize("model", ["", False, 1, [], {}])
def test_optional_mock_model_is_still_validated_when_present(path, model):
    # Section 3.4.1: a supplied mock model must be a nonempty string.
    assert_rejected(raw_with(path, {"provider": "mock", "model": model}))


@pytest.mark.parametrize("path", MODEL_PATHS)
@pytest.mark.parametrize("field", ["max_tokens", "temperature"])
@pytest.mark.parametrize("bad", [True, False, -1, "1", [], {}, math.nan, math.inf])
def test_model_call_setting_types_and_ranges(path, field, bad):
    # Section 3.4.1: model-level call settings have their own typed ranges.
    assert_rejected(raw_with(path, {"provider": "mock", field: bad}))


@pytest.mark.parametrize("path", MODEL_PATHS)
@pytest.mark.parametrize("bad", [None, 0, 1.0, 1.5])
def test_model_max_tokens_requires_positive_integer(path, bad):
    # Sections 1.3 and 3.4.1: even explicit null max_tokens is refused.
    assert_rejected(raw_with(path, {"provider": "mock", "max_tokens": bad}))


@pytest.mark.parametrize("temperature", [None, 0, 1.25])
def test_model_call_setting_precedence(temperature):
    # Sections 3.4.1–3.4.3: explicit settings beat globals; summaries default null.
    cfg = config.resolve({
        "llm": {"temperature": 2, "max_tokens": 17},
        "models": {
            "buyers": {"provider": "mock"},
            "managers": {"provider": "mock", "temperature": temperature, "max_tokens": 1},
            "summarizer": {"provider": "mock"},
            "buyer_seats": {"0": {"provider": "mock", "temperature": 0, "max_tokens": 9}},
        },
    })
    assert cfg["models"]["buyers"]["temperature"] == 2.0
    assert cfg["models"]["buyers"]["max_tokens"] == 17
    assert cfg["models"]["managers"]["temperature"] == temperature
    assert cfg["models"]["managers"]["max_tokens"] == 1
    assert cfg["models"]["summarizer"]["temperature"] is None
    assert cfg["models"]["summarizer"]["max_tokens"] == 17
    assert config.seat_spec(cfg, "buyer", 0)["temperature"] == 0.0
    assert config.seat_spec(cfg, "buyer", 0)["max_tokens"] == 9


@pytest.mark.parametrize("path", MODEL_PATHS)
@pytest.mark.parametrize("field", ["timeout_s", "max_retries", "network_patience_s"])
def test_global_retry_settings_are_not_model_keys(path, field):
    # Section 3.4.3: a model spec cannot override these global settings.
    assert_rejected(raw_with(path, {"provider": "mock", field: 1}))


@pytest.mark.parametrize("path", AGENT_PATHS)
@pytest.mark.parametrize("provider", ["openrouter", "openai_compatible"])
@pytest.mark.parametrize("price_key", ["price_in_per_m", "price_out_per_m"])
@pytest.mark.parametrize("bad", [None, 0, -1])
def test_paid_agents_require_both_strictly_positive_prices(path, provider, price_key, bad):
    # Sections 3.4.1 and 4: check role defaults and every seat, independently.
    spec = {
        "provider": provider,
        "model": "example/model",
        "base_url": "http://example.invalid/v1",
        "price_in_per_m": 1,
        "price_out_per_m": 2,
    }
    spec[price_key] = bad
    assert_rejected(raw_with(path, spec))


@pytest.mark.parametrize("path", MODEL_PATHS)
@pytest.mark.parametrize("price_key", ["price_in_per_m", "price_out_per_m"])
@pytest.mark.parametrize("bad", [True, False, "1", -0.01, math.nan, math.inf, -math.inf, [], {}])
def test_present_prices_always_have_numeric_range_checks(path, price_key, bad):
    # Section 3.4.1: even free providers and summaries validate given prices.
    assert_rejected(raw_with(path, {"provider": "mock", price_key: bad}))


@pytest.mark.parametrize("path", AGENT_PATHS)
@pytest.mark.parametrize("provider", ["mock", "ollama"])
def test_free_provider_prices_can_be_absent_or_zero(path, provider):
    # Section 3.4.1: no synthetic price keys are inserted when omitted.
    spec = {"provider": provider, "model": "local-model"}
    actual = get_at(resolved_with(path, spec), path)
    assert "price_in_per_m" not in actual
    assert "price_out_per_m" not in actual
    spec.update(price_in_per_m=0, price_out_per_m=0)
    actual = get_at(resolved_with(path, spec), path)
    assert type(actual["price_in_per_m"]) is float
    assert type(actual["price_out_per_m"]) is float
    assert actual["price_in_per_m"] == actual["price_out_per_m"] == 0.0


@pytest.mark.parametrize("provider", ["openrouter", "openai_compatible", "ollama", "mock"])
def test_summarizer_prices_optional_for_every_provider(provider):
    # Section 3.4.1: paid summarizers may omit prices, including in off mode.
    spec = {
        "provider": provider,
        "model": "summary",
        "base_url": "http://example.invalid/v1",
    }
    cfg = resolved_with("models.summarizer", spec)
    assert "price_in_per_m" not in cfg["models"]["summarizer"]
    assert "price_out_per_m" not in cfg["models"]["summarizer"]
    spec.update(price_in_per_m=0, price_out_per_m=0)
    cfg = resolved_with("models.summarizer", spec)
    assert cfg["models"]["summarizer"]["price_in_per_m"] == 0.0


@pytest.mark.parametrize("path", MODEL_PATHS)
@pytest.mark.parametrize("base_url", [None, "", False, 1, [], {}])
def test_compatible_provider_requires_base_url(path, base_url):
    # Sections 3.4.1 and 4: openai_compatible always requires base_url.
    spec = {
        "provider": "openai_compatible",
        "model": "model",
        "price_in_per_m": 1,
        "price_out_per_m": 1,
        "base_url": base_url,
    }
    assert_rejected(raw_with(path, spec))


@pytest.mark.parametrize("path", MODEL_PATHS)
@pytest.mark.parametrize("field", ["base_url", "api_key_env"])
@pytest.mark.parametrize("bad", ["", False, 1, [], {}])
def test_optional_model_strings_are_validated(path, field, bad):
    # Section 3.4.1: supplied URL and API-key variable names are nonempty strings.
    assert_rejected(raw_with(path, {"provider": "mock", field: bad}))


def test_backend_credentials_are_not_checked_at_config_load(monkeypatch):
    # Section 3.4.1: missing environment variables are a backend setup concern.
    name = "BLIND_CONFIG_TEST_UNSET_API_KEY"
    monkeypatch.delenv(name, raising=False)
    cfg = config.resolve({
        "models": {
            "buyers": {
                "provider": "openai_compatible",
                "model": "remote",
                "price_in_per_m": 1,
                "price_out_per_m": 2,
                "base_url": "http://example.invalid/v1",
                "api_key_env": name,
            }
        }
    })
    assert cfg["models"]["buyers"]["api_key_env"] == name


@pytest.mark.parametrize("path", MODEL_PATHS)
def test_extra_body_is_schema_free_json_data(path):
    # Sections 1.4 and 3.4.1: arbitrary nested JSON, including request overrides.
    body = {
        "model": "overridden",
        "temperature": None,
        "max_tokens": False,
        "unknown": [None, True, False, "é", 1, -2, 0.125, {"nested": []}],
        "": {},
    }
    actual = get_at(resolved_with(path, {"provider": "mock", "extra_body": body}), path)
    assert actual["extra_body"] == body
    assert not mutable_ids(actual["extra_body"]) & mutable_ids(body)


@pytest.mark.parametrize("path", MODEL_PATHS)
@pytest.mark.parametrize(
    "body",
    [
        [],
        "body",
        1,
        True,
        {1: "non-string key"},
        {"nested": {False: 1}},
        {"nested": [{"x": math.nan}]},
        {"nested": math.inf},
        {"nested": -math.inf},
        {"nested": {1, 2}},
        {"nested": (1, 2)},
        {"nested": b"bytes"},
        {"nested": datetime.date(2026, 1, 1)},
    ],
)
def test_extra_body_requires_json_form_at_every_depth(path, body):
    # Section 3.4.1: JSON restrictions apply recursively.
    assert_rejected(raw_with(path, {"provider": "mock", "extra_body": body}))


@pytest.mark.parametrize("role,map_name,count", [
    ("buyer", "buyer_seats", 2), ("manager", "manager_seats", 3)
])
def test_seat_selection_and_integer_key_tie_break(role, map_name, count):
    # Section 3.4.2: integer entry wins, regardless of insertion order.
    for entries in (
        [("0", {"provider": "mock", "model": "string"}),
         (0, {"provider": "mock", "model": "integer"})],
        [(0, {"provider": "mock", "model": "integer"}),
         ("0", {"provider": "mock", "model": "string"})],
    ):
        cfg = config.resolve({"models": {map_name: dict(entries)}})
        seats = cfg["models"][map_name]
        assert list(seats) == ["0"]
        assert config.seat_spec(cfg, role, 0) is seats["0"]
        assert seats["0"]["model"] == "integer"
        fallback = cfg["models"]["buyers" if role == "buyer" else "managers"]
        for seat in range(1, count):
            assert config.seat_spec(cfg, role, seat) is fallback


@pytest.mark.parametrize("map_name", ["buyer_seats", "manager_seats"])
@pytest.mark.parametrize("invalid_key", [0, "0"])
def test_duplicate_seat_entries_are_both_checked(map_name, invalid_key):
    # Section 3.4.2: a losing string entry does not escape validation.
    seats = {0: {"provider": "mock"}, "0": {"provider": "mock"}}
    seats[invalid_key] = {"provider": "mock", "max_tokens": 0}
    assert_rejected({"models": {map_name: seats}})


@pytest.mark.parametrize("map_name", ["buyer_seats", "manager_seats"])
@pytest.mark.parametrize(
    "key", [-1, "-1", "+0", "00", "01", " 0", "0 ", "1.0", 0.0, True, False, None, ""]
)
def test_invalid_seat_key_spellings(map_name, key):
    # Section 3.4.2: integer or unsigned canonical decimal string only.
    assert_rejected({"models": {map_name: {key: {"provider": "mock"}}}})


@pytest.mark.parametrize(
    "map_name,participant_key", [("buyer_seats", "buyers"), ("manager_seats", "managers")]
)
def test_seat_index_bounds_follow_configured_participant_count(map_name, participant_key):
    # Sections 3.3, 3.4.2 and 4: range is 0..count-1.
    for key in (4095, "4095"):
        cfg = config.resolve({
            "participants": {participant_key: 4096},
            "models": {map_name: {key: {"provider": "mock"}}},
        })
        assert "4095" in cfg["models"][map_name]
    for key in (4096, "4096"):
        assert_rejected({
            "participants": {participant_key: 4096},
            "models": {map_name: {key: {"provider": "mock"}}},
        })
    default_count = DEFAULT["participants"][participant_key]
    assert_rejected({"models": {map_name: {default_count: {"provider": "mock"}}}})


@pytest.mark.parametrize("mode", ["off", "managers", "buyers", "within_roles", "all"])
def test_inactive_messaging_and_summarizer_are_still_validated(mode):
    # Sections 1.6 and 3.4.2: checks run even when no summary will be called.
    assert_rejected({"message_channels": mode, "messaging": {"recent": 1}})
    assert_rejected({
        "message_channels": mode,
        "models": {"summarizer": {"provider": "mock", "max_tokens": 0}},
    })


def test_run_overrides_replace_only_run_settings():
    # Section 1.8; running.md §1: four run overrides, with None meaning absent.
    cfg = config.resolve(
        {"name": "kept", "seeds": [1], "cost_cap_usd": None},
        seeds=[7, 2], cost_cap=3, out="relative-output", parallel=9,
    )
    expected = expected_defaults()
    expected.update(
        name="kept", seeds=[7, 2], cost_cap_usd=3.0,
        output_dir="relative-output", parallel_episodes=9,
    )
    assert_typed_equal(cfg, expected)
    assert config.resolve({"cost_cap_usd": None}, cost_cap=None)["cost_cap_usd"] is None


@pytest.mark.parametrize(
    "option,key,bad",
    [
        ("seeds", "seeds", []),
        ("seeds", "seeds", [1, 1]),
        ("seeds", "seeds", [True]),
        ("seeds", "seeds", [1.0]),
        ("seeds", "seeds", [-1]),
        ("cost_cap", "cost_cap_usd", 0),
        ("cost_cap", "cost_cap_usd", True),
        ("cost_cap", "cost_cap_usd", math.inf),
        ("out", "output_dir", ""),
        ("out", "output_dir", 1),
        ("parallel", "parallel_episodes", 0),
        ("parallel", "parallel_episodes", True),
        ("parallel", "parallel_episodes", 2.0),
    ],
)
def test_run_override_validation(option, key, bad):
    # Section 1.8; API: overrides use the replaced key's validation rules.
    with pytest.raises(config.ConfigError) as exc:
        config.resolve(None, **{option: bad})
    assert key in str(exc.value).split(":", 1)[0]


@pytest.mark.parametrize(
    "raw,options",
    [
        ({"seeds": []}, {"seeds": [0]}),
        ({"cost_cap_usd": 0}, {"cost_cap": 1}),
        ({"output_dir": ""}, {"out": "valid"}),
        ({"parallel_episodes": 0}, {"parallel": 1}),
    ],
)
def test_valid_override_does_not_excuse_invalid_file(raw, options):
    # Section 1.6; running.md §2: validate the file before applying options.
    assert_rejected(raw, **options)


@pytest.mark.parametrize("text", ["", "null\n", "{}\n", "# only a comment\n"])
@pytest.mark.parametrize("as_path", [False, True])
def test_load_empty_or_null_yaml_without_disk_writes(monkeypatch, text, as_path):
    # Section 1.1: empty/null YAML supplies all defaults.
    fake_file(monkeypatch, text)
    path = Path("in-memory.yaml") if as_path else "in-memory.yaml"
    assert_typed_equal(config.load(path), expected_defaults())


def test_load_parses_yaml_and_forwards_overrides(monkeypatch):
    # Sections 1.2, 1.5 and 1.8: YAML off and run overrides.
    fake_file(monkeypatch, """
message_channels: off
models:
  managers: {provider: mock}
tests: {manager_cost: 1, buyer_cost: 1}
""")
    cfg = config.load("in-memory.yaml", seeds=[4], parallel=2, cost_cap=1, out="relative")
    assert cfg["message_channels"] == "off"
    assert cfg["seeds"] == [4]
    assert cfg["parallel_episodes"] == 2
    assert cfg["cost_cap_usd"] == 1.0
    assert cfg["output_dir"] == "relative"
    assert cfg["models"]["managers"] == {
        "provider": "mock", "temperature": 0.8, "max_tokens": 64000, "extra_body": {}
    }


@pytest.mark.parametrize("text", ["[", "a: [1", "[]", "true", "3", '"string"'])
def test_load_refuses_malformed_yaml_and_non_mapping_documents(monkeypatch, text):
    # Sections 1.1–1.2: malformed or non-mapping YAML is refused.
    fake_file(monkeypatch, text)
    with pytest.raises(config.ConfigError):
        config.load("in-memory.yaml")


@pytest.mark.parametrize("error", [
    FileNotFoundError("missing"), PermissionError("unreadable"), OSError("read failed")
])
def test_load_wraps_read_failures(monkeypatch, error):
    # Section 1.2: missing and unreadable files raise ConfigError.
    fake_file(monkeypatch, error=error)
    with pytest.raises(config.ConfigError):
        config.load(Path("in-memory.yaml"))


@pytest.mark.parametrize("text", [
    "parallel_episodes: 3.0",
    "parallel_episodes: true",
    "credits_per_usd: .nan",
    "credits_per_usd: .inf",
    'credits_per_usd: "1"',
    "money: {share_range: [0.6]}",
    "production: {manager: {speed: 1}}",
    "models: {buyers: {provider: mock, extra_body: {date: 2026-01-01}}}",
])
def test_load_runs_all_validation_before_returning(monkeypatch, text):
    # Sections 1.5–1.6 and 3.4.1: parse success does not bypass validation.
    fake_file(monkeypatch, text)
    with pytest.raises(config.ConfigError):
        config.load("in-memory.yaml")


@pytest.mark.parametrize("tests", [
    {"manager_sd": 2, "buyer_sd": 2},
    {"manager_sd": 3, "buyer_sd": 2},
    {"manager_cost": 1, "buyer_cost": 2},
    {"manager_cost": 2, "buyer_cost": 1},
])
def test_cross_parameter_test_quality_and_cost_rules(tests):
    # Section 4: managers have strictly better readings at equal prices.
    assert_rejected({"tests": tests})


def test_cross_parameter_test_cost_equality_uses_double_values():
    # Sections 1.5 and 4: integers are converted before numeric comparisons.
    cfg = config.resolve({"tests": {
        "manager_cost": 2**53 + 1,
        "buyer_cost": float(2**53),
    }})
    assert cfg["tests"]["manager_cost"] == cfg["tests"]["buyer_cost"] == float(2**53)


@pytest.mark.parametrize("manager", [
    {"k": 0.05},
    {"k": 0.049},
    {"ref_cost_fixed": 2.8},
    {"ref_cost_fixed": 2.79},
])
def test_employee_costs_must_be_strictly_lower_for_both_run_kinds(manager):
    # Sections 4 and 6.5: equality also fails, using unrounded expected costs.
    assert_rejected({"production": {"manager": manager}})


def test_employee_cheaper_check_does_not_round_to_cents():
    # Sections 4 and 6.5: strictly cheaper may still display as equal cents.
    cfg = config.resolve({"production": {"manager": {
        "k": 0.050001, "ref_cost_fixed": 2.800001,
    }}})
    assert config.expected_cost(cfg, "manager", "fresh") == 0.050001 * 80.0
    assert config.expected_cost(cfg, "manager", "refine") == 2.800001


@pytest.mark.parametrize("worker", ["employee", "manager"])
def test_quality_normalization_must_be_positive_in_double_precision(worker):
    # Section 4's worked refusal: sd=1e17, mu=6 makes the CDF difference zero.
    assert_rejected({"production": {worker: {"mu": 6, "sd": 1e17}}})


@pytest.mark.parametrize("worker", ["employee", "manager"])
@pytest.mark.parametrize("mu", [0, 10])
def test_narrow_quality_laws_at_scale_boundaries_are_accepted(worker, mu):
    # Section 4: tiny positive sd is valid if normalization remains positive.
    cfg = config.resolve({"production": {worker: {"mu": mu, "sd": 1e-300}}})
    assert cfg["production"][worker]["sd"] == 1e-300


@pytest.mark.parametrize("path", MEDIANS)
def test_every_run_kind_must_be_listable(path):
    # Sections 4 and 6.7: t=50, s=.35 has no day with at least 5%.
    assert_rejected(raw_with(path, 50))


@pytest.mark.parametrize("path", MEDIANS)
@pytest.mark.parametrize("median", [999.01, 1000, 1e100])
def test_deterministic_day_1000_is_not_listable(path, median):
    # Sections 4 and 6.7: the catch-all count 1000 never satisfies listability.
    raw = {"production": {"sig_t": 0}}
    set_at(raw, path, median)
    assert_rejected(raw)


@pytest.mark.parametrize("path", MEDIANS)
def test_deterministic_day_999_is_listable(path):
    # Sections 4 and 6.7: the last ordinary count is accepted.
    raw = {"production": {"sig_t": 0}}
    set_at(raw, path, 999)
    assert get_at(config.resolve(raw), path) == 999.0


def test_at_least_one_run_kind_must_take_more_than_one_day():
    # Section 4: listability alone is insufficient when all four laws finish day 1.
    raw = {"production": {"sig_t": 0}}
    for path in MEDIANS:
        set_at(raw, path, 1)
    assert_rejected(raw)
    for path in MEDIANS:
        one_longer = copy.deepcopy(raw)
        set_at(one_longer, path, 1.01)
        assert get_at(config.resolve(one_longer), path) == 1.01


@pytest.mark.parametrize("path", MEDIANS)
def test_finite_duration_result_survives_infinite_intermediate(path):
    # Section 6, Finiteness at load: d/t may overflow while Phi and shares remain finite.
    raw = raw_with(path, 5e-324)
    cfg = config.resolve(raw)
    assert config.days_mix(cfg, 5e-324) == [(1, 1.0)]


@pytest.mark.parametrize("raw", [
    {"value": {"late_decay": 5e-324}},
    {"time": {"horizon_end_prob": 5e-324}},
    {"production": {"employee": {"fee": 1e308, "cost_frac": 1e308}}},
    {"production": {"manager": {"k": 1e308}}},
    {"production": {"employee": {"ref_cost_frac": 1e308}}},
    {"production": {"cost_ref_worth": 1e308, "manager": {"k": 10}}},
])
def test_nonfinite_config_derived_values_are_refused_at_load(raw):
    # Section 6, Finiteness at load: valid scalar types do not excuse derived overflow.
    assert_rejected(raw)


@pytest.mark.parametrize("p,cap", [(0.1, 54), (0.05, 100), (0.2, 31), (0.5, 17)])
def test_horizon_worked_examples(p, cap):
    # Section 6.1: all stated default-minimum horizon examples.
    cfg = resolved_with("time.horizon_end_prob", p)
    assert config.horizon_cap(cfg) == cap


@pytest.mark.parametrize("p,allowed_n", [
    (0.40051574968105896, {9, 10}),
    (0.021588999306809686, {211, 212}),
    (0.99, {1, 2}),
])
def test_horizon_boundary_tolerance(p, allowed_n):
    # Section 6.1: both neighboring n values are permitted within tolerance.
    cfg = resolved_with("time.horizon_end_prob", p)
    assert config.horizon_cap(cfg) - 10 in allowed_n


@pytest.mark.parametrize("p,n", [(0.989999, 2), (0.990001, 1), (0.9, 3)])
def test_horizon_strict_threshold_away_from_tolerance(p, n):
    # Section 6.1: first reach probability strictly below 1%.
    # p=.9 is itself within the n=2 tolerance and is tested separately below.
    if p == 0.9:
        cfg = resolved_with("time.horizon_end_prob", p)
        assert config.horizon_cap(cfg) in (12, 13)
    else:
        assert config.horizon_cap(resolved_with("time.horizon_end_prob", p)) == 10 + n


def test_horizon_1000_day_cap_and_bid_window_bound():
    # Sections 4 and 6.1: inclusive 1000-day cap and inclusive cap*rounds bid bound.
    cfg = config.resolve({"time": {"horizon_min_days": 956}})
    assert config.horizon_cap(cfg) == 1000
    assert_rejected({"time": {"horizon_min_days": 957}})
    assert_rejected({"time": {"horizon_min_days": 1000}})
    assert_rejected({"time": {"horizon_end_prob": 0.004}})
    cfg = resolved_with("contracts.bid_window_rounds", 162)
    assert config.opening_round(cfg, 1) == 164
    assert_rejected({"contracts": {"bid_window_rounds": 163}})
    cfg = config.resolve({
        "time": {"rounds_per_day": 1, "horizon_end_prob": 0.5},
        "contracts": {"bid_window_rounds": 17},
    })
    assert config.horizon_cap(cfg) == 17
    assert_rejected({
        "time": {"rounds_per_day": 1, "horizon_end_prob": 0.5},
        "contracts": {"bid_window_rounds": 18},
    })


def test_horizon_tolerance_also_applies_to_cap_acceptance():
    # Section 6.1: a boundary may put the cap on either side of 1000.
    raw = {"time": {
        "horizon_min_days": 991,
        "horizon_end_prob": 0.40051574968105896,
    }}
    try:
        cfg = config.resolve(raw)
    except config.ConfigError:
        return
    assert config.horizon_cap(cfg) in (1000, 1001)


@pytest.mark.parametrize("decay,days", [
    (0.2, 5), (0.25, 4), (0.3, 4), (1 / 3, 3), (1.0, 1), (0.1, 10),
    (1 / (4 + 0.5e-9), 4),
    (1 / (4 + 2e-9), 5),
])
def test_escrow_close_examples_and_epsilon(decay, days):
    # Section 6.2: ceil(1/decay - 1e-9), including both sides of the epsilon.
    cfg = config.resolve({
        "value": {"late_decay": decay},
        "time": {"close_after_due_days": days},
    })
    assert config.escrow_close_days(cfg) == days
    assert config.full_pay_cap(cfg, 20) == 20 + days - 1
    if days > 1:
        assert_rejected({
            "value": {"late_decay": decay},
            "time": {"close_after_due_days": days - 1},
        })


@pytest.mark.parametrize("due,signing,expected", [
    (10, 1, 10),
    (10, 6, 10),
    (10, 7, 11),
    (10, 10, 14),
    (10, 11, 14),
    (10, 100, 14),
])
def test_deadline_max_then_cap(due, signing, expected):
    # Section 6.3: due-date floor, minimum contract period, then full-pay cap.
    cfg = config.resolve(None)
    assert config.full_pay_cap(cfg, due) == due + 4
    assert config.deadline(cfg, due, signing) == expected


def test_deadline_with_one_day_escrow_and_huge_minimum():
    # Sections 6.2–6.3: exact integer arithmetic and minimum-period clipping.
    cfg = config.resolve({
        "value": {"late_decay": 1},
        "contracts": {"min_contract_days": 10**400},
    })
    due = 10**400
    assert config.full_pay_cap(cfg, due) == due
    assert config.deadline(cfg, due, due + 1) == due
    cfg = resolved_with("contracts.min_contract_days", 10**400)
    assert config.deadline(cfg, due, due + 1) == due + 4


@pytest.mark.parametrize("share,start,budget", [
    (1.36, 81.60, 40.80),
    (1.0, 60.0, 30.0),
    (0.0, 0.0, 0.0),
    (0.6, 36.0, 18.0),
    (1.4, 84.0, 42.0),
    (1.01, 60.60, 30.30),
])
def test_wealth_amount_examples(share, start, budget):
    # Section 6.4; episode.md §1.3: rounded wealth amounts, including explicit zero.
    cfg = config.resolve(None)
    assert config.start_balance(cfg, share) == start
    assert config.per_task_budget(cfg, share) == budget


@pytest.mark.parametrize("share,start,budget", [
    (1.0, 999999.0, 1.0),
    (1.01, 1009998.99, 1.01),
    (0.01, 9999.99, 0.01),
])
def test_wealth_amounts_use_configured_standard_amounts(share, start, budget):
    # Section 6.4; README rounding: products are rounded from their double values.
    cfg = config.resolve({"money": {"start_amount": 999999, "task_budget": 1}})
    assert config.start_balance(cfg, share) == start
    assert config.per_task_budget(cfg, share) == budget


@pytest.mark.parametrize("worker,kind,expected", [
    ("employee", "fresh", 4.0),
    ("employee", "refine", 2.8),
    ("manager", "fresh", 8.0),
    ("manager", "refine", 14.0),
])
def test_expected_cost_defaults(worker, kind, expected):
    # Section 6.5: all four worked expected costs, without cent rounding.
    assert config.expected_cost(config.resolve(None), worker, kind) == expected


def test_expected_costs_use_each_distinct_config_parameter():
    # Section 6.5: fee is additive; reference worth applies only where specified.
    cfg = config.resolve({"production": {
        "cost_ref_worth": 100,
        "employee": {"fee": 1.125, "cost_frac": 0.03125, "ref_cost_frac": 0.015625},
        "manager": {"k": 0.125, "ref_cost_fixed": 8.0625},
    }})
    assert config.expected_cost(cfg, "employee", "fresh") == 4.25
    assert config.expected_cost(cfg, "employee", "refine") == 2.6875
    assert config.expected_cost(cfg, "manager", "fresh") == 12.5
    assert config.expected_cost(cfg, "manager", "refine") == 8.0625


@pytest.mark.parametrize("day,round_number,expected", [
    (1, 1, 1), (1, 3, 3), (2, 1, 4), (2, 3, 6), (10, 2, 29),
])
def test_round_index_and_default_opening(day, round_number, expected):
    # Section 6.6: one-based round index and g+w+1 opening.
    cfg = config.resolve(None)
    assert config.round_index(cfg, day, round_number) == expected
    assert config.opening_round(cfg, expected) == expected + 2


@pytest.mark.parametrize("arrival,due,expected", [
    (20, 10, 20), (5, 10, 14), (14, 10, 14),
])
def test_last_possible_day_default(arrival, due, expected):
    # Section 6.6: maximum of the last arrival and latest due plus close offset.
    assert config.last_possible_day(config.resolve(None), arrival, due) == expected


def test_round_close_and_lost_turn_arithmetic_is_exact_for_huge_integers():
    # Sections 6, 6.6 and 6.8: integer arithmetic never passes through float.
    huge = 10**400
    cfg = config.resolve({
        "time": {"rounds_per_day": huge, "close_after_due_days": huge},
        "contracts": {"bid_window_rounds": huge},
    })
    assert config.round_index(cfg, huge, huge) == huge * huge
    assert config.opening_round(cfg, huge) == 2 * huge + 1
    assert config.last_possible_day(cfg, huge, huge) == 2 * huge - 1
    assert config.lost_turn_limit(cfg) == 5 * huge


@pytest.mark.parametrize("rounds,expected", [(1, 5), (3, 15), (7, 35)])
def test_lost_turn_limit(rounds, expected):
    # Sections 5 and 6.8: fixed multiplier five, not a separately configurable limit.
    assert config.lost_turn_limit(resolved_with("time.rounds_per_day", rounds)) == expected


@pytest.mark.parametrize("x", [-math.inf, -38, -20, -10, -9, -1, 0, 1, 9, math.inf])
def test_normal_cdf_including_deep_lower_tail(x):
    # Section 4; prompts.md §2: CDF must retain tiny positive lower-tail values.
    expected = math.erfc(-x / math.sqrt(2.0)) / 2.0
    actual = config.Phi(x)
    if expected in (0.0, 0.5, 1.0):
        assert actual == expected
    else:
        assert actual > 0
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=5e-324)


@pytest.mark.parametrize("s,t", [
    (0.35, 1.0),
    (0.35, 0.7),
    (0.35, 2.5),
    (0.027, 2.5),
    (0.1, 2.5),
    (1.5, 1.0),
    (1.5, 2.5),
    (1.5, 5.09489020404268),
])
def test_days_mix_matches_ordered_double_algorithm(s, t):
    # Section 6.7; prompts.md §§2, 4.4: preserve every visited count and raw share.
    cfg = config.resolve({"production": {
        "sig_t": s, "employee": {"t_med": t},
    }})
    expected = reference_mix(s, t)
    actual = config.days_mix(cfg, t)
    assert isinstance(actual, list)
    assert [d for d, _ in actual] == [d for d, _ in expected]
    assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in actual)
    for (_, share), (_, wanted) in zip(actual, expected):
        assert math.isfinite(share)
        assert share >= 0
        assert math.isclose(share, wanted, rel_tol=1e-11, abs_tol=3e-15)
        if wanted > 0:
            assert share > 0
    assert 1 - 1.1e-9 <= math.fsum(p for _, p in actual) <= 1 + 1e-14


@pytest.mark.parametrize("median,last_day,first_shares", [
    (1.0, 9, [0.5000, 0.4762, 0.0230, 0.0008]),
    (0.7, 6, [0.8459, 0.1527, 0.0013]),
])
def test_days_mix_default_worked_examples(median, last_day, first_shares):
    # Section 6.7: stated four-decimal examples and exact stopping counts.
    mix = config.days_mix(config.resolve(None), median)
    assert mix[-1][0] == last_day
    assert [round(p, 4) for _, p in mix[:len(first_shares)]] == first_shares


def test_manager_fresh_days_mix_worked_example():
    # Section 6.7: manager fresh first five stated probabilities.
    mix = config.days_mix(config.resolve(None), 2.5)
    assert [round(p, 4) for _, p in mix[:5]] == [0.0044, 0.2575, 0.4369, 0.2116, 0.0658]


@pytest.mark.parametrize("median,day", [(0.1, 1), (1, 1), (1.01, 2), (2, 2), (2.5, 3), (999, 999)])
def test_zero_log_sd_has_zero_entries_before_the_deterministic_day(median, day):
    # Section 6.7: include visited zero shares; deterministic duration is ceil(t).
    cfg = config.resolve({"production": {"sig_t": 0}})
    expected = [(d, 0.0) for d in range(1, day)] + [(day, 1.0)]
    assert config.days_mix(cfg, median) == expected


def test_days_mix_retains_the_day_1000_tail():
    # Section 6.7: add residual mass at 1000 when the stopping threshold is unmet.
    cfg = config.resolve({"production": {"sig_t": 1.5}})
    mix = config.days_mix(cfg, 2.5)
    assert [d for d, _ in mix] == list(range(1, 1001))
    cumulative_999 = math.erfc(-math.log(999 / 2.5) / 1.5 / math.sqrt(2)) / 2
    assert mix[-1][1] == pytest.approx(1 - cumulative_999, rel=1e-10, abs=2e-15)
    assert mix[-1][1] > 1e-9


def test_days_mix_does_not_renormalize_the_early_stopped_list():
    # Section 6.7: early termination omits the tiny remainder instead of redistributing it.
    cfg = config.resolve(None)
    mix = config.days_mix(cfg, 1.0)
    expected_total = math.erfc(-math.log(9.0) / 0.35 / math.sqrt(2)) / 2
    assert math.fsum(p for _, p in mix) == pytest.approx(expected_total, abs=2e-15)
    assert math.fsum(p for _, p in mix) < 1.0
    assert all(day != 1000 for day, _ in mix)
