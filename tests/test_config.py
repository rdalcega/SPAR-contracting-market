"""Tests of market/config.py against spec/config.md (and the default derived values other spec files state)."""

import copy
import json
import math
import re
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from market import config as c
from market.config import ConfigError, resolve
from market.fmt import SUM

SPEC = Path(__file__).parent.parent / "spec" / "config.md"
MOCK = {"provider": "mock"}


def raw(**dotted):
    """A file's mapping from dotted keys: raw(**{"time.rounds_per_day": 4})."""
    out = {}
    for path, value in dotted.items():
        *blocks, key = path.split(".")
        at = out
        for block in blocks:
            at = at.setdefault(block, {})
        at[key] = value
    return out


def cfg(**dotted):
    return resolve(raw(**dotted))


def refused(mapping, *named, **options):
    with pytest.raises(ConfigError) as e:
        resolve(mapping, **options)
    for key in named:
        assert key in str(e.value)
    return str(e.value)


def leaves(mapping, prefix=""):
    for k, v in mapping.items():
        if isinstance(v, dict) and not f"{prefix}{k}".startswith("models."):
            yield from leaves(v, f"{prefix}{k}.")
        else:
            yield f"{prefix}{k}"


D = resolve(None)


# Section 2: the schema with its defaults

def test_defaults_equal_section_2():
    block = re.search(r"## 2\. The schema with its defaults\n\n```yaml\n(.*?)```", SPEC.read_text(), re.S).group(1)
    schema = yaml.safe_load(block)
    assert schema.pop("message_channels") is False  # YAML 1.1's reading of the unquoted off
    defaults = copy.deepcopy(c.DEFAULTS)
    assert defaults.pop("message_channels") == "off"
    assert defaults == schema


def test_resolved_defaults():
    expected = copy.deepcopy(c.DEFAULTS)
    for role in ("buyers", "managers"):
        expected["models"][role] |= {"temperature": 0.8, "max_tokens": 64000}
    assert D == expected == resolve({})
    assert list(leaves(D)) == list(leaves(c.DEFAULTS))
    assert set(leaves(c.DEFAULTS)) == set(c._SCHEMA)
    assert json.loads(json.dumps(D)) == D
    assert resolve(D) == D
    assert c.DEFAULTS["models"]["buyers"].keys() == {"provider", "model", "price_in_per_m", "price_out_per_m",
                                                     "extra_body"}


def test_values_are_normalised():
    assert type(D["cost_cap_usd"]) is float and type(D["credits_per_usd"]) is float
    assert type(D["money"]["start_amount"]) is float and type(D["value"]["q0"]) is float
    assert type(D["tests"]["manager_cost"]) is float and D["tests"]["manager_cost"] == 1.0
    assert type(D["llm"]["timeout_s"]) is float and type(D["production"]["cost_ref_worth"]) is float
    assert all(type(x) is float for x in D["money"]["share_range"])
    assert all(type(x) is int for x in D["tasks"]["worth_range"] + D["tasks"]["due_days_range"] + D["seeds"])
    assert type(D["time"]["rounds_per_day"]) is int and type(D["llm"]["max_tokens"]) is int
    assert D["message_channels"] == "off"
    assert type(cfg(**{"tests.manager_cost": 1, "tests.buyer_cost": 1.0})["tests"]["manager_cost"]) is float


def test_resolve_leaves_its_inputs_alone():
    before = copy.deepcopy(c.DEFAULTS)
    given = raw(**{"models.buyers": dict(MOCK, extra_body={"a": [1]}), "seeds": [5], "tasks.worth_range": [1, 2]})
    kept = copy.deepcopy(given)
    out = resolve(given)
    out["models"]["buyers"]["extra_body"]["a"].append(2)
    out["models"]["managers"]["extra_body"]["provider"]["quantizations"].clear()
    out["seeds"].append(9)
    out["tasks"]["worth_range"].append(9)
    assert given == kept and c.DEFAULTS == before


# Section 1: the config file

def test_empty_and_non_mapping_files(tmp_path):
    for text in ("", "null\n", "# nothing\n", "{}\n"):
        (tmp_path / "c.yaml").write_text(text)
        assert c.load(tmp_path / "c.yaml") == D
    for text in ("[1, 2]\n", "3\n", "market\n", "false\n"):
        (tmp_path / "c.yaml").write_text(text)
        with pytest.raises(ConfigError):
            c.load(tmp_path / "c.yaml")
    for top in ([], 3, "market", False, 0):
        refused(top)


def test_unreadable_files(tmp_path):
    with pytest.raises(ConfigError):
        c.load(tmp_path / "missing.yaml")
    with pytest.raises(ConfigError):
        c.load(tmp_path)
    (tmp_path / "c.yaml").write_text("time: {rounds_per_day: 3\n")
    with pytest.raises(ConfigError):
        c.load(tmp_path / "c.yaml")
    (tmp_path / "c.yaml").write_bytes(b"name: \xff\xfe\n")
    with pytest.raises(ConfigError):
        c.load(tmp_path / "c.yaml")
    (tmp_path / "c.yaml").write_text(f"seeds: [{'1' * 4301}]\n")  # more digits than Python reads as an integer
    with pytest.raises(ConfigError):
        c.load(tmp_path / "c.yaml")


def test_load_reads_yaml_and_passes_the_options(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("message_channels: off\ntime:\n  rounds_per_day: 4\nseeds: [7]\n")
    assert c.load(path) == cfg(**{"time.rounds_per_day": 4, "seeds": [7]})
    assert c.load(str(path), seeds=[1], out="o")["seeds"] == [1]
    for text, mode in (("off", "off"), ('"off"', "off"), ("false", "off"), ("all", "all"), ("managers", "managers")):
        path.write_text(f"message_channels: {text}\n")
        assert c.load(path)["message_channels"] == mode
    for text in ("time: {rounds_per_day: 3.0}", "time: {rounds_per_day: true}", "value: {exponent: .nan}",
                 "value: {exponent: .inf}", "value: {exponent: true}", "value: {exponent: '2'}",
                 "message_channels: true", "message_channels: 1", "message_channels: on"):
        path.write_text(text + "\n")
        with pytest.raises(ConfigError):
            c.load(path)


def test_merging_at_every_depth():
    out = cfg(**{"time.rounds_per_day": 4, "contracts.escrow.review_days": 3, "production.manager.capacity": 2})
    expected = copy.deepcopy(D)
    expected["time"]["rounds_per_day"] = 4
    expected["contracts"]["escrow"]["review_days"] = 3
    expected["production"]["manager"]["capacity"] = 2
    assert out == expected


def test_a_list_or_a_scalar_replaces_the_default():
    out = cfg(seeds=[5], name="x", **{"tasks.worth_range": [10, 20], "money.share_range": [1, 1]})
    assert out["seeds"] == [5] and out["name"] == "x"
    assert out["tasks"]["worth_range"] == [10, 20] and out["money"]["share_range"] == [1.0, 1.0]


def test_a_model_spec_replaces_the_default_as_a_whole():
    out = resolve({"models": {"managers": {"provider": "mock"}}})
    managers = out["models"]["managers"]
    assert managers == {"provider": "mock", "temperature": 0.8, "max_tokens": 64000, "extra_body": {}}
    assert not {"model", "price_in_per_m", "price_out_per_m", "base_url", "api_key_env"} & managers.keys()
    assert out["models"]["buyers"] == D["models"]["buyers"]
    assert out["models"]["summarizer"] == D["models"]["summarizer"]
    summarizer = cfg(**{"models.summarizer": {"provider": "mock"}})["models"]["summarizer"]
    assert summarizer == {"provider": "mock", "temperature": None, "max_tokens": 64000, "extra_body": {}}
    seat = cfg(**{"models.buyer_seats": {1: {"provider": "ollama", "model": "m"}}})["models"]["buyer_seats"]["1"]
    assert seat == {"provider": "ollama", "model": "m", "temperature": 0.8, "max_tokens": 64000, "extra_body": {}}


def test_what_a_spec_leaves_out_comes_from_llm():
    out = cfg(**{"llm.temperature": None, "llm.max_tokens": 9, "models.managers": dict(MOCK, temperature=0.25),
                 "models.buyers": dict(MOCK, max_tokens=3)})
    assert out["models"]["buyers"]["temperature"] is None and out["models"]["buyers"]["max_tokens"] == 3
    assert out["models"]["managers"]["temperature"] == 0.25 and out["models"]["managers"]["max_tokens"] == 9
    assert out["models"]["summarizer"]["temperature"] is None and out["models"]["summarizer"]["max_tokens"] == 8000


@pytest.mark.parametrize("key", ["llm", "time", "models", "contracts.escrow", "production.employee",
                                 "production.manager", "participants", "view", "models.buyers",
                                 "models.managers", "models.summarizer"])
@pytest.mark.parametrize("value", [None, 3, "x", [], False])
def test_a_mapping_must_be_given_as_a_mapping(key, value):
    refused(raw(**{key: value}), key)


def test_seats_given_as_null_are_empty():
    out = cfg(**{"models.buyer_seats": None, "models.manager_seats": None})
    assert out["models"]["buyer_seats"] == {} == out["models"]["manager_seats"]
    for value in (3, "x", [], False):
        refused(raw(**{"models.buyer_seats": value}), "models.buyer_seats")
    refused(raw(**{"models.manager_seats": {0: None}}), "models.manager_seats.0")


def test_null_inside_a_model_spec():
    spec = {"provider": "ollama", "model": "m", "temperature": None, "api_key_env": None, "price_in_per_m": None,
            "price_out_per_m": None, "extra_body": None, "base_url": None}
    out = cfg(**{"models.buyers": spec, "models.summarizer": spec})["models"]
    for role in ("buyers", "summarizer"):
        assert out[role] == {"provider": "ollama", "model": "m", "temperature": None, "api_key_env": None,
                             "max_tokens": 64000, "extra_body": {}}
    refused(raw(**{"models.buyers": dict(MOCK, max_tokens=None)}), "models.buyers.max_tokens")
    refused(raw(**{"models.buyers": {"provider": None}}), "models.buyers.provider")
    refused(raw(**{"models.buyers": {"provider": "openrouter", "model": None, "price_in_per_m": 1,
                                     "price_out_per_m": 1}}), "models.buyers.model")
    refused(raw(**{"models.summarizer": {"provider": "openai_compatible", "model": "m", "base_url": None}}),
            "models.summarizer.base_url")


@pytest.mark.parametrize("mapping, path", [
    ({"speed": 1}, "speed"),
    ({"time": {"speed": 1}}, "time.speed"),
    ({"production": {"manager": {"speed": 1}}}, "production.manager.speed"),
    ({"production": {"employee": {"k": 0.1}}}, "production.employee.k"),
    ({"production": {"manager": {"fee": 1.0}}}, "production.manager.fee"),
    ({"contracts": {"escrow": {"speed": 1}}}, "contracts.escrow.speed"),
    ({"models": {"speed": 1}}, "models.speed"),
    ({"models": {"buyers": dict(MOCK, timeout_s=3)}}, "models.buyers.timeout_s"),
    ({"models": {"summarizer": dict(MOCK, speed=None)}}, "models.summarizer.speed"),
    ({"models": {"manager_seats": {1: dict(MOCK, speed=1)}}}, "models.manager_seats.1.speed"),
    ({"llm": {"speed": 1}}, "llm.speed"),
    ({"view": {1: 2}}, "view.1"),
    ({"horizon_cap": 54}, "horizon_cap"),
    ({"escrow_close_days": 5}, "escrow_close_days"),
])
def test_unknown_keys_are_refused_with_their_path(mapping, path):
    assert path in refused(mapping)


def test_extra_body_and_seat_keys_hold_no_schema_keys():
    body = {"anything": {"at": [1, 2.5, "x", True, None, {"any": "depth"}]}, "max_tokens": 10**30}
    out = cfg(**{"models.buyers": dict(MOCK, extra_body=body), "models.manager_seats": {2: MOCK, "0": MOCK}})
    assert out["models"]["buyers"]["extra_body"] == body
    assert set(out["models"]["manager_seats"]) == {"0", "2"}


@pytest.mark.parametrize("key", [k for k, v in c._SCHEMA.items() if v is c._SCHEMA["parallel_episodes"]]
                         + ["participants.buyers", "llm.max_retries", "tasks.first_day_tasks",
                            "money.share_decimals", "tests.decimals", "messaging.recent",
                            "messaging.summary_chars", "llm.max_tokens"])
@pytest.mark.parametrize("value", [True, False, 3.0, 3.5, "3", None, [3]])
def test_an_integer_must_be_a_yaml_integer(key, value):
    refused(raw(**{key: value}), key)


def at(mapping, path):
    for key in path.split("."):
        mapping = mapping[key]
    return mapping


NUMBERS = sorted(k for k in leaves(D) if type(at(D, k)) is float)


def test_the_number_keys():
    assert len(NUMBERS) == 39 and "money.start_amount" in NUMBERS and "llm.temperature" in NUMBERS


@pytest.mark.parametrize("key", NUMBERS)
@pytest.mark.parametrize("value", [True, False, math.nan, math.inf, -math.inf, "1", "1e3", [1], 10**400])
def test_a_number_must_be_a_finite_yaml_number(key, value):
    refused(raw(**{key: value}), key)


@pytest.mark.parametrize("key", [k for k in NUMBERS if k not in ("cost_cap_usd", "llm.temperature")])
def test_a_number_cannot_be_null(key):
    refused(raw(**{key: None}), key)


@pytest.mark.parametrize("key", ["tasks.worth_range", "tasks.due_days_range", "money.share_range"])
@pytest.mark.parametrize("value", [[1], [1, 1, 1], [], (1, 1), {"low": 1, "high": 1}, 1, "1, 1", None,
                                   [1, True], [1, "1"], [None, 1], [1, math.inf]])
def test_a_pair_is_a_list_of_two(key, value):
    refused(raw(**{key: value}), key)


def test_a_pair_of_integers_takes_no_decimal():
    refused(raw(**{"tasks.worth_range": [40.0, 80]}), "tasks.worth_range")
    refused(raw(**{"tasks.due_days_range": [4, 9.5]}), "tasks.due_days_range")
    assert cfg(**{"money.share_range": [1, 1.5]})["money"]["share_range"] == [1.0, 1.5]


def test_message_channels():
    for mode in ("off", "managers", "buyers", "within_roles", "all"):
        assert cfg(message_channels=mode)["message_channels"] == mode
    assert cfg(message_channels=False)["message_channels"] == "off"
    for value in (True, 0, 1, 2.5, None, [], "on", "OFF", "none", "", ["off"]):
        refused({"message_channels": value}, "message_channels")


# Section 3: each key's range

REFUSED = {
    "seeds": [[], [0, 0], [-1], [0, 1.0], [True], ["0"], 0, "0,1", None, (0, 1)],
    "parallel_episodes": [0, -1],
    "cost_cap_usd": [0, -1],
    "output_dir": ["", 3, None, False, ["runs"]],
    "name": ["", ".", "..", "a/b", "a\\b", "a\0b", "/", 3, None],
    "participants.buyers": [1, 0, 4097],
    "participants.managers": [1, 4097],
    "llm.temperature": [-0.1],
    "llm.max_tokens": [0],
    "llm.timeout_s": [0, -1],
    "llm.max_retries": [-1],
    "llm.network_patience_s": [-1],
    "credits_per_usd": [0, -1],
    "time.rounds_per_day": [0],
    "time.horizon_min_days": [0],
    "time.horizon_end_prob": [0, 1, 1.5, -0.1],
    "time.close_after_due_days": [0],
    "turns.max_actions": [0],
    "turns.memory_chars": [0],
    "tasks.first_day_tasks": [1, 0],
    "tasks.arrival_p": [0, 1, 0.3141, 1 / 101, 0.5 + 2e-12, 1e-13, 1 - 1e-13, 1.5],
    "tasks.worth_range": [[0, 80], [80, 40], [-1, 5]],
    "tasks.due_days_range": [[0, 9], [9, 4]],
    "money.start_amount": [0, -60, 60.5, 10**6, 1e6],
    "money.task_budget": [0, 30.25, 10**6],
    "money.share_range": [[0, 1.4], [1.4, 0.6], [0.6, 0.6], [2, 2], [0.605, 1.4], [0.6, 1.405], [-1, 1]],
    "money.share_decimals": [-1, 3],
    "value.q0": [-0.1, 10, 11],
    "value.exponent": [0, -1],
    "value.late_decay": [0, 1.1, -0.2],
    "contracts.bid_window_rounds": [0],
    "contracts.min_contract_days": [0],
    "contracts.message_chars": [0],
    "contracts.escrow.fee_rate": [-0.1, 1.1],
    "contracts.escrow.review_days": [0],
    "contracts.escrow.dispute_window_days": [0],
    "contracts.escrow.dispute_fee_rate": [0, -0.5],
    "contracts.escrow.arbiter_sd": [-0.1],
    "production.cost_ref_worth": [0, -80],
    "production.sig_t": [-0.1],
    "production.sig_c": [0, -0.25],
    "production.employee.mu": [-0.1, 10.1],
    "production.employee.sd": [0, -1],
    "production.employee.gain": [0, 10.1],
    "production.employee.sd_ref": [0],
    "production.employee.t_med": [0, -1],
    "production.employee.t_ref_med": [0],
    "production.employee.fee": [0, -1],
    "production.employee.cost_frac": [0],
    "production.employee.ref_cost_frac": [0],
    "production.employee.capacity": [0],
    "production.manager.mu": [-0.1, 10.1],
    "production.manager.sd": [0],
    "production.manager.gain": [0, 10.1],
    "production.manager.sd_ref": [0],
    "production.manager.t_med": [0],
    "production.manager.t_ref_med": [0],
    "production.manager.k": [0],
    "production.manager.ref_cost_fixed": [0],
    "production.manager.capacity": [0],
    "tests.manager_sd": [0, -0.5],
    "tests.manager_cost": [-1],
    "tests.buyer_sd": [0],
    "tests.buyer_cost": [-1],
    "tests.decimals": [-1, 4],
    "messaging.message_chars": [0],
    "messaging.recent": [1, 0],
    "messaging.summary_chars": [49, 0],
    "compute.cpu_s": [0],
    "compute.wall_s": [0],
    "compute.memory_mb": [0],
    "compute.output_kb": [0],
    "compute.output_chars": [0],
    "compute.code_chars": [0],
    "view.finished_shown": [0],
    "view.open_shown": [0],
    "view.readings_shown": [0],
    "view.overdue_days": [0],
    "view.recent_days": [0],
    "view.messages_shown": [0],
    "view.payments_shown": [0],
    "view.overflow_ids": [0],
}


def test_every_key_has_a_refusal():
    assert set(REFUSED) == set(leaves(D)) - {"message_channels"} - {k for k in leaves(D) if k.startswith("models.")}


@pytest.mark.parametrize("key, value", [(k, v) for k, values in REFUSED.items() for v in values])
def test_out_of_range_is_refused(key, value):
    refused(raw(**{key: value}), key)


ACCEPTED = [
    {"seeds": [0]}, {"seeds": [3, 10**30, 0]}, {"parallel_episodes": 1}, {"cost_cap_usd": None},
    {"cost_cap_usd": 0.01}, {"output_dir": "/tmp/a b/../c"}, {"name": "..."}, {"name": "a b.c"},
    {"participants.buyers": 4096, "participants.managers": 2}, {"llm.temperature": 0}, {"llm.temperature": None},
    {"llm.max_tokens": 1, "llm.max_retries": 0, "llm.network_patience_s": 0, "llm.timeout_s": 0.5},
    {"credits_per_usd": 1e-9}, {"time.rounds_per_day": 1}, {"time.horizon_min_days": 1},
    {"time.horizon_end_prob": 0.999}, {"turns.max_actions": 1, "turns.memory_chars": 1},
    {"tasks.first_day_tasks": 2}, {"tasks.arrival_p": 0.01}, {"tasks.arrival_p": 0.99}, {"tasks.arrival_p": 1 / 3},
    {"tasks.arrival_p": 0.4}, {"tasks.arrival_p": 83 / 85 + 9.9e-13}, {"tasks.arrival_p": 0.5 - 9e-13},
    {"tasks.worth_range": [1, 1]}, {"tasks.due_days_range": [1, 1]},
    {"money.start_amount": 1, "money.task_budget": 999999}, {"money.start_amount": 60.0},
    {"money.share_range": [1, 1]}, {"money.share_range": [0.01, 99.99]},
    {"money.share_range": [1, 3], "money.share_decimals": 0},
    {"money.share_range": [0.5, 1.5], "money.share_decimals": 1},
    {"value.q0": 0}, {"value.q0": 9.99}, {"value.exponent": 0.5},
    {"value.late_decay": 1}, {"value.late_decay": 0.25},
    {"contracts.escrow.fee_rate": 0}, {"contracts.escrow.fee_rate": 1}, {"contracts.escrow.arbiter_sd": 0},
    {"contracts.escrow.dispute_fee_rate": 1e-9}, {"production.sig_t": 0}, {"production.sig_t": 0.05},
    {"production.employee.mu": 0, "production.manager.mu": 10},
    {"production.employee.gain": 10, "production.manager.gain": 0.1},
    {"tests.manager_cost": 0, "tests.buyer_cost": 0}, {"tests.decimals": 0}, {"tests.decimals": 3},
    {"messaging.recent": 2, "messaging.summary_chars": 50},
]


@pytest.mark.parametrize("dotted", ACCEPTED)
def test_in_range_is_accepted(dotted):
    assert resolve(resolve(raw(**dotted))) == resolve(raw(**dotted))


def test_share_range_follows_share_decimals():
    refused(raw(**{"money.share_range": [0.6, 1.4], "money.share_decimals": 0}), "money.share_range",
            "money.share_decimals")
    refused(raw(**{"money.share_range": [0.65, 1.4], "money.share_decimals": 1}), "money.share_range")
    refused(raw(**{"money.share_range": [1234567, 1234568], "money.share_decimals": 0}), "money.share_range")
    refused(raw(**{"money.share_range": [0.01, 12345.67]}), "money.share_range")
    assert cfg(**{"money.share_range": [0.01, 1234.56]})["money"]["share_range"] == [0.01, 1234.56]


# Section 3.4: model specs

def spec_refused(spec, key, where="models.buyers"):
    refused(raw(**{where: spec}), f"{where}.{key}")


PAID = {"provider": "openrouter", "model": "m", "price_in_per_m": 1, "price_out_per_m": 2}


def test_spec_provider_and_model():
    spec_refused({}, "provider")
    spec_refused({"model": "m"}, "provider")
    for name in ("anthropic", "Mock", "", 3, True, ["mock"]):
        spec_refused({"provider": name, "model": "m"}, "provider")
    for provider in ("openrouter", "ollama", "openai_compatible"):
        spec_refused({**PAID, "provider": provider, "base_url": "http://x"} | {"model": None}, "model")
    for model in ("", 3, True, ["m"]):
        spec_refused({**PAID, "model": model}, "model")
        spec_refused({"provider": "mock", "model": model}, "model")
    assert cfg(**{"models.buyers": {"provider": "mock", "model": "any"}})["models"]["buyers"]["model"] == "any"


def test_spec_prices():
    for key in ("price_in_per_m", "price_out_per_m"):
        for provider in ("openrouter", "openai_compatible"):
            paid = {**PAID, "provider": provider, "base_url": "http://x"}
            for where in ("models.buyers", "models.managers", "models.buyer_seats.1", "models.manager_seats.1"):
                for price in (0, None, -1, "1", True, math.inf):
                    spec_refused({**paid, key: price}, key, where)
                spec_refused({k: v for k, v in paid.items() if k != key}, key, where)
            out = cfg(**{"models.summarizer": {k: v for k, v in paid.items() if k != key}})
            assert key not in out["models"]["summarizer"]
            assert cfg(**{"models.summarizer": {**paid, key: 0}})["models"]["summarizer"][key] == 0.0
        for where in ("models.buyers", "models.manager_seats.2", "models.summarizer"):
            for provider in ("mock", "ollama"):
                free = {"provider": provider, "model": "m"}
                spec_refused({**free, key: -1}, key, where)
                spec_refused({**free, key: "1"}, key, where)
                assert resolve(raw(**{where: {**free, key: 0}}))
    out = cfg(**{"models.buyers": PAID})["models"]["buyers"]
    assert out["price_in_per_m"] == 1.0 and type(out["price_in_per_m"]) is float


def test_spec_temperature_and_max_tokens():
    for value in (-0.1, "0.5", True, math.nan):
        spec_refused({**PAID, "temperature": value}, "temperature")
    for value in (0, 1.0, "8", True, None, -1):
        spec_refused({**PAID, "max_tokens": value}, "max_tokens")
        spec_refused({**PAID, "max_tokens": value}, "max_tokens", "models.summarizer")
    out = cfg(**{"models.buyers": {**PAID, "temperature": 0, "max_tokens": 1}})["models"]["buyers"]
    assert out["temperature"] == 0.0 and type(out["temperature"]) is float and out["max_tokens"] == 1


def test_spec_extra_body():
    loop = {}
    loop["self"] = loop
    for body in ([], "x", 3, {1: "x"}, {"a": {2: "x"}}, {"a": math.nan}, {"a": [math.inf]}, {"a": (1, 2)},
                 {"a": {1, 2}}, {"a": b"x"}, {"a": [{"b": object()}]}, loop):
        spec_refused({**PAID, "extra_body": body}, "extra_body")
        spec_refused({**PAID, "extra_body": body}, "extra_body", "models.summarizer")
        spec_refused({**PAID, "extra_body": body}, "extra_body", "models.manager_seats.0")
    assert cfg(**{"models.buyers": {**PAID, "extra_body": {}}})["models"]["buyers"]["extra_body"] == {}


def nested(depth):
    body = {"a": []}
    for _ in range(depth):
        body = {"a": [body["a"]]}
    return body


def test_spec_extra_body_at_every_depth(tmp_path):
    for depth in (100, 400, 600, 999, 1001, 5000):  # deeper than Python's recursion follows
        deep = nested(depth)
        for where in ("buyers", "summarizer"):
            out = cfg(**{f"models.{where}": {**PAID, "extra_body": deep}})["models"][where]["extra_body"]
            assert out == deep and out["a"] is not deep["a"] and json.loads(json.dumps(out)) == out
        deep["a"][0][0][0][0].append(math.nan)
        spec_refused({**PAID, "extra_body": deep}, "extra_body")
    lines = ["a0: &a0 []"] + [f"a{i}: &a{i} [*a{i - 1}]" for i in range(1, 1001)]
    (tmp_path / "c.yaml").write_text("models:\n  buyers:\n    provider: mock\n    extra_body:\n"
                                     + "".join(f"      {line}\n" for line in lines))
    body = c.load(tmp_path / "c.yaml")["models"]["buyers"]["extra_body"]
    assert len(body) == 1001 and body["a0"] == [] and body["a3"] == [[[[]]]]


def test_spec_extra_body_shared_and_self_holding():
    shared = [1, {"x": None}]
    body = {"a": shared, "b": [shared, shared], "c": {"d": shared}}  # what a YAML alias gives: it has a JSON form
    out = cfg(**{"models.buyers": {**PAID, "extra_body": body}})["models"]["buyers"]["extra_body"]
    assert out == body and out["a"] is not shared and shared == [1, {"x": None}]
    ring = []
    ring.append(ring)
    far = {"a": [{"b": []}]}
    far["a"][0]["b"].append(far["a"])
    for body in ({"a": ring}, far, {"a": {"b": far}}):
        spec_refused({**PAID, "extra_body": body}, "extra_body")


def test_spec_base_url_and_api_key_env():
    for where in ("models.buyers", "models.summarizer", "models.manager_seats.1"):
        spec_refused({**PAID, "provider": "openai_compatible"}, "base_url", where)
        for value in ("", 3, True):
            spec_refused({**PAID, "base_url": value}, "base_url", where)
            spec_refused({**PAID, "api_key_env": value}, "api_key_env", where)
    spec = {**PAID, "provider": "openai_compatible", "base_url": "http://localhost:8000/v1", "api_key_env": "KEY"}
    out = cfg(**{"models.buyers": spec, "models.summarizer": spec})["models"]
    assert out["buyers"]["base_url"] == "http://localhost:8000/v1" and out["summarizer"]["api_key_env"] == "KEY"
    assert "api_key_env" not in D["models"]["buyers"] and "base_url" not in D["models"]["buyers"]
    assert cfg(**{"models.buyers": {**PAID, "api_key_env": None}})["models"]["buyers"]["api_key_env"] is None


def test_the_summarizer_is_checked_in_every_mode():
    for mode in ("off", "all"):
        refused({"message_channels": mode, "models": {"summarizer": {"provider": "openrouter"}}},
                "models.summarizer.model")


def test_seat_keys():
    for key in (-1, "-1", "01", "00", " 1", "1 ", "+1", "1.0", 1.0, 1.5, True, None, "x", "", "²", "١"):
        refused(raw(**{"models.manager_seats": {key: MOCK}}), "models.manager_seats", str(key))
    refused(raw(**{"models.buyer_seats": {2: MOCK}}), "models.buyer_seats.2")
    refused(raw(**{"models.buyer_seats": {"2": MOCK}}), "models.buyer_seats.2")
    refused(raw(**{"models.manager_seats": {3: MOCK}}), "models.manager_seats.3")
    refused(raw(**{"models.manager_seats": {"12": MOCK}}), "models.manager_seats.12")
    out = cfg(**{"participants.managers": 13, "models.manager_seats": {"12": MOCK, 0: MOCK},
                 "models.buyer_seats": {1: MOCK}})
    assert set(out["models"]["manager_seats"]) == {"0", "12"} and set(out["models"]["buyer_seats"]) == {"1"}


def test_a_seat_key_of_any_length_is_refused_by_name(tmp_path):
    long = "1" * 4301  # more digits than Python converts to an integer
    for role in ("buyer", "manager"):
        refused(raw(**{f"models.{role}_seats": {long: MOCK}}), f"models.{role}_seats.{long}")
        refused(raw(**{f"models.{role}_seats": {"0" + long: MOCK}}), f"models.{role}_seats.0{long}")
    (tmp_path / "c.yaml").write_text(f'models:\n  buyer_seats:\n    ? "{long}"\n    : {{provider: mock}}\n')
    with pytest.raises(ConfigError, match="models.buyer_seats.1111"):
        c.load(tmp_path / "c.yaml")
    for key, count in (("9", 9), ("10", 10), ("99", 12), ("100", 99), ("4096", 4096), ("10000", 4096)):
        refused(raw(**{"models.buyer_seats": {key: MOCK}, "participants.buyers": count}), f"models.buyer_seats.{key}")
    for key, count in (("9", 10), ("10", 11), ("9", 100), ("99", 100), ("4095", 4096), ("0", 2)):
        assert key in cfg(**{"models.buyer_seats": {key: MOCK}, "participants.buyers": count})["models"]["buyer_seats"]


def test_a_seat_named_both_ways():
    one, other = dict(MOCK, model="integer"), dict(MOCK, model="string")
    for seats in ({0: one, "0": other}, {"0": other, 0: one}):
        out = cfg(**{"models.buyer_seats": seats})
        assert out["models"]["buyer_seats"]["0"]["model"] == "integer"
        assert c.seat_spec(out, "buyer", 0)["model"] == "integer"
    refused(raw(**{"models.buyer_seats": {0: one, "0": dict(MOCK, speed=1)}}), "models.buyer_seats.0.speed")
    refused(raw(**{"models.buyer_seats": {"0": dict(MOCK, model=""), 0: one}}), "models.buyer_seats.0.model")


def test_seat_spec():
    assert c.seat_spec(D, "buyer", 0) == c.seat_spec(D, "buyer", 1) == D["models"]["buyers"]
    assert c.seat_spec(D, "manager", 2) == D["models"]["managers"]
    out = cfg(**{"models.manager_seats": {"1": {"provider": "ollama", "model": "m", "temperature": None}},
                 "models.buyer_seats": {0: dict(MOCK, max_tokens=7)}})
    assert c.seat_spec(out, "manager", 1) == {"provider": "ollama", "model": "m", "temperature": None,
                                              "max_tokens": 64000, "extra_body": {}}
    assert c.seat_spec(out, "manager", 0) == c.seat_spec(out, "manager", 2) == D["models"]["managers"]
    assert c.seat_spec(out, "buyer", 0) == {"provider": "mock", "temperature": 0.8, "max_tokens": 7,
                                            "extra_body": {}}
    assert c.seat_spec(out, "buyer", 1) == D["models"]["buyers"]
    again = resolve(json.loads(json.dumps(out)))
    assert again == out and c.seat_spec(again, "manager", 1)["provider"] == "ollama"


# Section 4: checks across parameters

def test_tests_rule():
    refused(raw(**{"tests.manager_sd": 2.0}), "tests.manager_sd", "tests.buyer_sd")
    refused(raw(**{"tests.manager_sd": 3, "tests.buyer_sd": 2.5}), "tests.")
    refused(raw(**{"tests.manager_cost": 2}), "tests.manager_cost", "tests.buyer_cost")
    refused(raw(**{"tests.buyer_cost": 0.5}), "tests.")
    assert cfg(**{"tests.manager_cost": 2, "tests.buyer_cost": 2.0, "tests.manager_sd": 1.9})


def test_the_employee_must_be_cheaper():
    refused(raw(**{"production.employee.fee": 5}), "production.")  # fresh 8 = 8
    refused(raw(**{"production.employee.cost_frac": 0.0875}), "production.")
    refused(raw(**{"production.manager.k": 0.05}), "production.")  # 4 = 4
    refused(raw(**{"production.manager.ref_cost_fixed": 2.8}), "production.")
    refused(raw(**{"production.employee.ref_cost_frac": 0.2}), "production.")
    refused(raw(**{"production.cost_ref_worth": 10}), "production.")  # fresh 1.375 > 1
    assert cfg(**{"production.manager.ref_cost_fixed": 2.81, "production.manager.k": 0.051})


def test_every_run_has_a_listable_duration_law():
    for key in ("production.employee.t_med", "production.employee.t_ref_med", "production.manager.t_med",
                "production.manager.t_ref_med"):
        refused(raw(**{key: 50}), "production.sig_t", key)
        refused(raw(**{key: 999.5, "production.sig_t": 0}), "production.sig_t", key)
        refused(raw(**{key: 5000, "production.sig_t": 0}), key)
        assert cfg(**{key: 999, "production.sig_t": 0})
        assert cfg(**{key: 998.5, "production.sig_t": 0})
        assert cfg(**{key: 1e-320})
        assert cfg(**{key: 1e-320, "production.sig_t": 5e-324})
    assert cfg(**{"production.sig_t": 0.05}) and cfg(**{"production.sig_t": 5})


def test_some_run_can_take_more_than_one_day():
    quick = {f"production.{w}.{k}": 1.0 for w in ("employee", "manager") for k in ("t_med", "t_ref_med")}
    message = refused(raw(**quick, **{"production.sig_t": 0}), "production.sig_t")
    assert all(key in message for key in quick)
    refused(raw(**{k: 0.5 for k in quick}, **{"production.sig_t": 0}), "production.sig_t")
    refused(raw(**{k: 1e-300 for k in quick}, **{"production.sig_t": 1e-3}), "production.sig_t")
    assert cfg(**quick | {"production.sig_t": 0, "production.manager.t_med": 1.5})
    assert cfg(**quick | {"production.sig_t": 0.01})


def test_each_worker_has_mass_between_0_and_10():
    for worker in ("employee", "manager"):
        refused(raw(**{f"production.{worker}.sd": 1e17, f"production.{worker}.mu": 6}),
                f"production.{worker}.mu", f"production.{worker}.sd")
        refused(raw(**{f"production.{worker}.sd": 1e300}), f"production.{worker}.sd")
        assert cfg(**{f"production.{worker}.sd": 1e15, f"production.{worker}.mu": 6})
        assert cfg(**{f"production.{worker}.sd": 1e-300})
        assert cfg(**{f"production.{worker}.sd": 0.1, f"production.{worker}.mu": 0})


def test_close_after_due_days_covers_the_escrow_close():
    refused(raw(**{"time.close_after_due_days": 4}), "time.close_after_due_days", "value.late_decay")
    refused(raw(**{"value.late_decay": 0.1}), "time.close_after_due_days", "value.late_decay")
    assert cfg(**{"value.late_decay": 0.1, "time.close_after_due_days": 10})
    assert cfg(**{"value.late_decay": 0.25, "time.close_after_due_days": 4})
    assert cfg(**{"value.late_decay": 1, "time.close_after_due_days": 1})


def test_the_horizon_cap_is_at_most_1000():
    refused(raw(**{"time.horizon_end_prob": 0.0046}), "time.horizon_min_days", "time.horizon_end_prob")
    refused(raw(**{"time.horizon_end_prob": 5e-324}), "time.horizon_min_days", "time.horizon_end_prob")
    refused(raw(**{"time.horizon_end_prob": 1e-300}), "time.horizon_end_prob")
    refused(raw(**{"time.horizon_min_days": 957}), "time.horizon_min_days", "time.horizon_end_prob")
    refused(raw(**{"time.horizon_min_days": 10**30}), "time.horizon_min_days")
    assert c.horizon_cap(cfg(**{"time.horizon_end_prob": 0.0047})) <= 1000
    assert c.horizon_cap(cfg(**{"time.horizon_min_days": 956})) == 1000


def test_the_bid_window_fits_the_horizon():
    assert cfg(**{"contracts.bid_window_rounds": 162})  # 54 x 3
    refused(raw(**{"contracts.bid_window_rounds": 163}), "contracts.bid_window_rounds")
    assert cfg(**{"contracts.bid_window_rounds": 17, "time.rounds_per_day": 1, "time.horizon_end_prob": 0.5})
    refused(raw(**{"contracts.bid_window_rounds": 18, "time.rounds_per_day": 1, "time.horizon_end_prob": 0.5}),
            "contracts.bid_window_rounds")


def test_finiteness_at_load():
    refused(raw(**{"value.late_decay": 5e-324, "time.close_after_due_days": 10**400}), "value.late_decay")
    refused(raw(**{"production.manager.k": 1e200, "production.cost_ref_worth": 1e200}),
            "production.manager.k", "production.cost_ref_worth")
    refused(raw(**{"production.employee.cost_frac": 1e200, "production.cost_ref_worth": 1e200,
                   "production.manager.ref_cost_fixed": 1e308}), "production.employee.cost_frac")
    big = cfg(**{"production.manager.k": 1e150, "production.cost_ref_worth": 1e150,
                 "production.manager.ref_cost_fixed": 1e300})
    assert c.expected_cost(big, "manager", "fresh") == 1e150 * 1e150
    tiny = cfg(**{"production.employee.t_med": 5e-324})  # d / t is infinite: share(1) = 1
    assert c.days_mix(tiny, 5e-324) == [(1, 1.0)]


# running.md 1: the command's options

def test_the_options_replace_the_run_settings():
    out = resolve({"seeds": [4], "name": "n"}, seeds=[0, 3], cost_cap=2.5, out="elsewhere", parallel=2)
    assert (out["seeds"], out["cost_cap_usd"], out["output_dir"], out["parallel_episodes"]) == ([0, 3], 2.5,
                                                                                                "elsewhere", 2)
    assert type(resolve(None, cost_cap=3)["cost_cap_usd"]) is float
    assert resolve({"cost_cap_usd": None}, cost_cap=1)["cost_cap_usd"] == 1.0
    assert resolve({"cost_cap_usd": None})["cost_cap_usd"] is None
    rest = {k: v for k, v in out.items() if k not in ("seeds", "cost_cap_usd", "output_dir", "parallel_episodes")}
    assert rest == {k: v for k, v in resolve({"name": "n"}).items() if k in rest}
    assert resolve({"seeds": [4]}, seeds=None, cost_cap=None, out=None, parallel=None) == cfg(seeds=[4])


@pytest.mark.parametrize("option, value, key", [
    ("seeds", [], "seeds"), ("seeds", [1, 1], "seeds"), ("seeds", [-1], "seeds"), ("seeds", [1.0], "seeds"),
    ("seeds", "0,3", "seeds"), ("cost_cap", 0, "cost_cap_usd"), ("cost_cap", -1, "cost_cap_usd"),
    ("cost_cap", math.inf, "cost_cap_usd"), ("cost_cap", "5", "cost_cap_usd"), ("out", "", "output_dir"),
    ("out", 3, "output_dir"), ("parallel", 0, "parallel_episodes"), ("parallel", 2.0, "parallel_episodes"),
    ("parallel", True, "parallel_episodes"),
])
def test_the_options_follow_the_rules_of_their_keys(option, value, key):
    refused(None, key, **{option: value})


def test_an_option_does_not_excuse_the_file():
    refused({"seeds": []}, "seeds", seeds=[1])
    refused({"parallel_episodes": 0}, "parallel_episodes", parallel=1)


# Section 6: derived values

def test_horizon_cap():
    log_stay = math.log1p(-0.1)
    assert round(math.log(0.01) / log_stay, 2) == 43.71
    assert c.horizon_cap(D) == 54
    assert 0.9**44 < 0.01 <= 0.9**43
    for p, cap in ((0.05, 100), (0.2, 31), (0.5, 17)):
        assert c.horizon_cap(cfg(**{"time.horizon_end_prob": p})) == cap
    assert c.horizon_cap(cfg(**{"time.horizon_end_prob": 0.75, "time.horizon_min_days": 3})) == 7  # 0.25^4 < 0.01
    assert c.horizon_cap(cfg(**{"time.horizon_end_prob": 0.99, "time.horizon_min_days": 1})) in (2, 3)  # 0.01^1
    assert c.horizon_cap(cfg(**{"time.horizon_end_prob": 0.995, "time.horizon_min_days": 1})) == 2
    assert c.horizon_cap(cfg(**{"time.horizon_end_prob": 0.01})) == 10 + 459  # 0.99^459 = 0.00992
    assert c.horizon_cap(cfg(**{"time.horizon_end_prob": 0.40051574968105896})) in (19, 20)
    assert c.horizon_cap(cfg(**{"time.horizon_end_prob": 0.021588999306809686})) in (221, 222)


@pytest.mark.parametrize("p", [0.1, 0.05, 0.2, 0.5, 0.3, 0.07, 0.75, 0.01, 0.0047, 0.999, 0.33, 0.123456789])
def test_horizon_cap_in_exact_arithmetic(p):
    stay, n = 1 - Fraction(p), 0
    while stay**n >= Fraction(1, 100):
        n += 1
    assert c.horizon_cap(cfg(**{"time.horizon_end_prob": p})) == 10 + n


def test_escrow_close_days():
    assert c.escrow_close_days(D) == 5
    for decay, days in ((0.25, 4), (0.3, 4), (1 / 3, 3), (1, 1), (0.1, 10), (0.5, 2), (0.21, 5), (0.19, 6),
                        (1 / (3 + 1e-9), 3), (1 / (3 + 1e-8), 4)):
        out = cfg(**{"value.late_decay": decay, "time.close_after_due_days": 10})
        assert c.escrow_close_days(out) == days


def test_full_pay_cap_and_deadline():
    assert c.full_pay_cap(D, 9) == 13
    for due, signed, deadline in ((9, 1, 9), (9, 5, 9), (9, 6, 10), (9, 8, 12), (9, 9, 13), (9, 30, 13)):
        assert c.deadline(D, due, signed) == deadline == min(max(due, signed + 4), due + 4)
    short = cfg(**{"contracts.min_contract_days": 2, "value.late_decay": 0.5})
    assert c.full_pay_cap(short, 10) == 11
    assert [c.deadline(short, 10, s) for s in (3, 8, 9, 12)] == [10, 10, 11, 11]
    sharp = cfg(**{"value.late_decay": 1})
    assert c.full_pay_cap(sharp, 10) == 10 and c.deadline(sharp, 10, 1) == 10 == c.deadline(sharp, 10, 20)
    huge = cfg(**{"contracts.min_contract_days": 10**30 + 1})
    assert c.deadline(huge, 7, 2) == 11 and type(c.deadline(huge, 7, 2)) is int
    assert c.deadline(D, 10**30, 10**30 - 1) == 10**30 + 3


def test_start_balance_and_per_task_budget():
    assert c.start_balance(D, 1.36) == 81.6 and c.per_task_budget(D, 1.36) == 40.8
    assert c.start_balance(D, 1.35) == 81.0
    assert c.start_balance(D, 1.0) == 60.0 and c.per_task_budget(D, 1) == 30.0
    assert c.start_balance(D, 0.0) == 0.0 and c.per_task_budget(D, 0.0) == 0.0
    other = cfg(**{"money.start_amount": 100, "money.task_budget": 7})
    assert c.start_balance(other, 0.73) == 73.0 and c.per_task_budget(other, 0.61) == 4.27
    assert c.per_task_budget(other, 1.4) == 9.8 and c.start_balance(other, 1) == 100.0


def test_an_amount_that_is_not_finite_is_never_returned():
    # Section 6, "Finiteness during an episode": the config is accepted; the amount stops the episode, by name.
    wide = cfg(**{"money.share_range": [1e308, 1.1e308]})
    share = 1.0483651033685292e308
    with pytest.raises(ArithmeticError, match="start balance is not finite.*money.start_amount"):
        c.start_balance(wide, share)
    with pytest.raises(ArithmeticError, match="per-task budget is not finite.*money.task_budget"):
        c.per_task_budget(wide, share)
    small = cfg(**{"money.share_range": [1e308, 1.1e308], "money.start_amount": 1, "money.task_budget": 1})
    assert c.start_balance(small, share) == share == c.per_task_budget(small, share)


def test_expected_costs():
    costs = [c.expected_cost(D, w, k) for w in ("employee", "manager") for k in ("fresh", "refine")]
    assert costs == [4.0, 1 + 0.0225 * 80, 8.0, 14.0]
    assert [round(x, 2) for x in costs] == [4.0, 2.8, 8.0, 14.0]
    other = cfg(**{"production.cost_ref_worth": 100, "production.employee.fee": 2,
                   "production.employee.cost_frac": 0.05, "production.employee.ref_cost_frac": 0.01,
                   "production.manager.k": 0.2, "production.manager.ref_cost_fixed": 9})
    costs = [c.expected_cost(other, w, k) for w in ("employee", "manager") for k in ("fresh", "refine")]
    assert costs == [7.0, 3.0, 20.0, 9.0]


def test_round_index_and_opening_round():
    assert [c.round_index(D, d, r) for d, r in ((1, 1), (1, 3), (2, 1), (5, 2))] == [1, 3, 4, 14]
    assert c.opening_round(D, 1) == 3 == c.round_index(D, 1, 3)
    assert c.opening_round(D, 2) == c.round_index(D, 2, 1) and c.opening_round(D, 3) == c.round_index(D, 2, 2)
    assert c.opening_round(D, 14) == 16
    other = cfg(**{"time.rounds_per_day": 4, "contracts.bid_window_rounds": 3})
    assert c.round_index(other, 3, 2) == 10 and c.opening_round(other, 10) == 14


def test_last_possible_day():
    assert c.last_possible_day(D, 14, 20) == 24 and c.last_possible_day(D, 54, 20) == 54
    assert c.last_possible_day(D, 10, 6) == 10
    other = cfg(**{"time.close_after_due_days": 7})
    assert c.last_possible_day(other, 12, 20) == 26 and c.last_possible_day(other, 30, 20) == 30


def test_phi():
    assert c.Phi(0) == 0.5 and c.Phi(math.inf) == 1 and c.Phi(-math.inf) == 0
    assert c.Phi(1.959963984540054) == pytest.approx(0.975, rel=1e-14)
    assert c.Phi(-38) == pytest.approx(2.885428351e-316, rel=1e-6) and c.Phi(-10) == pytest.approx(7.619853024e-24)


def stated(mix):
    return {d: round(share, 4) for d, share in mix}


def test_days_mix_at_the_defaults():
    fresh, refine, own = c.days_mix(D, 1.0), c.days_mix(D, 0.7), c.days_mix(D, 2.5)
    assert [d for d, _ in fresh] == list(range(1, 10)) and [d for d, _ in refine] == list(range(1, 7))
    assert stated(fresh) | {1: 0.5, 2: 0.4762, 3: 0.0230, 4: 0.0008} == stated(fresh)
    assert stated(refine) | {1: 0.8459, 2: 0.1527, 3: 0.0013} == stated(refine)
    assert stated(own) | {1: 0.0044, 2: 0.2575, 3: 0.4369, 4: 0.2116, 5: 0.0658} == stated(own)
    assert round(SUM([s for d, s in fresh if d > 2]), 4) == 0.0238
    assert round(SUM([s for d, s in refine if d > 2]), 4) == 0.0014
    assert SUM([s for d, s in own if d > 5]) == pytest.approx(0.02383, abs=1e-5)  # prompts.md 4.4 says 0.0237
    for mix in (fresh, refine, own):
        assert [d for d, _ in mix] == list(range(1, len(mix) + 1))
        assert SUM([s for _, s in mix]) == pytest.approx(1, abs=1e-9)
        assert SUM([s for _, s in mix[:-1]]) < 1 - 1e-9
    assert fresh[0] == (1, 0.5) and fresh[1][1] == c.Phi(math.log(2) / 0.35) - 0.5


def test_days_mix_elsewhere():
    wide = cfg(**{"production.sig_t": 1.5, "production.employee.t_med": 5.09489020404268})
    mix = c.days_mix(wide, 5.09489020404268)  # prompts.md 4.4, step 2
    assert [d for d, s in mix if s >= 0.05] == [1, 2, 3, 4, 5]
    assert SUM([s for d, s in mix if d > 5]) == 0.5050000000000001
    assert mix[-1][0] == 1000 and len(mix) == 1000 and mix[-1][1] == 1 - c.Phi(math.log(999 / 5.09489020404268) / 1.5)
    unit = cfg(**{"production.sig_t": 1})
    mix = c.days_mix(unit, 1.0)
    assert mix[0] == (1, 0.5) and mix[1][1] == pytest.approx((1 + math.erf(math.log(2) / math.sqrt(2))) / 2 - 0.5)
    exact = cfg(**{"production.sig_t": 0})
    assert c.days_mix(exact, 2.5) == [(1, 0.0), (2, 0.0), (3, 1.0)]
    assert c.days_mix(exact, 1.0) == [(1, 1.0)] == c.days_mix(exact, 0.7)
    assert c.days_mix(exact, 3) == [(1, 0.0), (2, 0.0), (3, 1.0)]
    assert c.days_mix(exact, 999)[-1] == (999, 1.0) and len(c.days_mix(exact, 999)) == 999
    assert c.days_mix(exact, 999.5)[-1] == (1000, 1.0) and len(c.days_mix(exact, 999.5)) == 1000
    narrow = cfg(**{"production.sig_t": 0.05})
    assert stated(c.days_mix(narrow, 2.5))[3] == round(c.Phi(math.log(3 / 2.5) / 0.05) - c.Phi(math.log(2 / 2.5) / 0.05), 4)
    assert c.days_mix(D, 50)[0] == (1, c.Phi(math.log(1 / 50) / 0.35))
    assert max(s for _, s in c.days_mix(D, 50)) < 0.05


def test_lost_turn_limit():
    assert c.lost_turn_limit(D) == 15
    assert c.lost_turn_limit(cfg(**{"time.rounds_per_day": 1})) == 5
    assert c.lost_turn_limit(cfg(**{"time.rounds_per_day": 4})) == 20


def test_an_integer_of_more_than_4300_digits_is_refused_however_it_is_spelled(tmp_path):
    # SPEC_QUESTIONS.md Q18: such an integer cannot be written out in any text, so the config is refused at load
    for value in (16**3600, 10**4300):
        with pytest.raises(ConfigError, match="contracts.min_contract_days"):
            resolve({"contracts": {"min_contract_days": value}})
        with pytest.raises(ConfigError, match="seeds"):
            resolve({"seeds": [value]})
        with pytest.raises(ConfigError, match="seeds"):
            resolve(None, seeds=[value])
    (tmp_path / "c.yaml").write_text("contracts: {min_contract_days: 0x1" + "0" * 3600 + "}\n")
    with pytest.raises(ConfigError, match="contracts.min_contract_days"):
        c.load(tmp_path / "c.yaml")
    longest = 10**4300 - 1
    assert resolve({"contracts": {"min_contract_days": longest}})["contracts"]["min_contract_days"] == longest


def test_an_extra_body_whose_json_form_cannot_be_written_is_refused():
    # config.md 3.4.1: extra_body "must have a JSON form"; one nested past what can be written has none in practice
    deep = {}
    for _ in range(200_000):
        deep = {"x": deep}
    for body in (deep, {"n": 10**4300}):
        with pytest.raises(ConfigError, match="models.buyers.extra_body"):
            resolve({"models": {"buyers": {"provider": "mock", "extra_body": body}}})
    fine = {}
    for _ in range(1000):
        fine = {"x": fine}
    spec = resolve({"models": {"buyers": {"provider": "mock", "extra_body": fine}}})["models"]["buyers"]
    assert json.dumps(spec["extra_body"]) == json.dumps(fine)


@pytest.mark.parametrize("raw", [
    {16**3600: 1},
    {"time": {16**3600: 1}},
    {"models": {"buyer_seats": {16**3600: {"provider": "mock"}}}},
    {"models": {"buyers": {16**3600: 1, "provider": "mock"}}},
])
def test_a_too_long_integer_used_as_a_key_is_refused_not_a_crash(raw, tmp_path):
    with pytest.raises(ConfigError, match="4,300 digits"):
        resolve(raw)


def test_a_file_nested_deeper_than_the_reader_goes_is_refused(tmp_path):
    (tmp_path / "c.yaml").write_text("models: {buyers: {provider: mock, extra_body: " + "{a: " * 5000 + "1"
                                     + "}" * 5000 + "}}\n")
    with pytest.raises(ConfigError, match="cannot be read"):
        c.load(tmp_path / "c.yaml")
