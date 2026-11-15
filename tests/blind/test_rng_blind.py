"""Blind tests derived from spec/randomness.md and the supplied rng API.

No reference-generator outputs are assumed. Market-level scheduling, config
validation, balances, and persistence are outside this pure module's API.
"""

import copy
import itertools
import json
import math
import os
import random
import subprocess
import sys
from statistics import NormalDist

import pytest

from market import rng


NORMAL = NormalDist()
LOW_U = 1e-12
HIGH_U = 1 - 1e-12

PRODUCTION = {
    "cost_ref_worth": 80,
    "sig_t": 0.35,
    "sig_c": 0.25,
    "employee": {
        "mu": 6.0,
        "sd": 2.5,
        "gain": 4.0,
        "sd_ref": 1.5,
        "t_med": 1.0,
        "t_ref_med": 0.7,
        "fee": 1.0,
        "cost_frac": 0.0375,
        "ref_cost_frac": 0.0225,
        "capacity": 2,
    },
    "manager": {
        "mu": 7.0,
        "sd": 1.2,
        "gain": 7.0,
        "sd_ref": 0.8,
        "t_med": 2.5,
        "t_ref_med": 1.0,
        "k": 0.1,
        "ref_cost_fixed": 14.0,
        "capacity": 1,
    },
}


def phi(z):
    # erfc retains accuracy in the lower tail.
    return 0.5 * math.erfc(-z / math.sqrt(2))


def expected_normal(u):
    return NORMAL.inv_cdf(min(max(u, LOW_U), HIGH_U))


def expected_tn(mean, sd, z):
    a = phi(-mean / sd)
    b = phi((10 - mean) / sd)
    p = min(max(a + phi(z) * (b - a), 1e-15), 1 - 1e-15)
    return float(min(10, max(0, mean + sd * NORMAL.inv_cdf(p))))


def expected_run(production, worker, kind, us, parent=None):
    zq, zt, zc = map(expected_normal, us)
    w = production[worker]
    if kind == "fresh":
        mean, sd, median = w["mu"], w["sd"], w["t_med"]
    else:
        mean = parent + w["gain"] * (10 - parent) / 10
        sd, median = w["sd_ref"], w["t_ref_med"]
    multiplier = math.exp(
        production["sig_c"] * zc - production["sig_c"] ** 2 / 2
    )
    if worker == "employee":
        fraction = w["cost_frac"] if kind == "fresh" else w["ref_cost_frac"]
        raw_cost = w["fee"] + fraction * production["cost_ref_worth"] * multiplier
    elif kind == "fresh":
        raw_cost = w["k"] * production["cost_ref_worth"] * multiplier
    else:
        raw_cost = w["ref_cost_fixed"] * multiplier
    return {
        "z_q": zq,
        "z_t": zt,
        "z_c": zc,
        "q": expected_tn(mean, sd, zq),
        "days": max(1, math.ceil(median * math.exp(production["sig_t"] * zt))),
        "cost": round(raw_cost, 2),
    }


class UniformTape:
    def __init__(self, values):
        self.values = iter(values)
        self.consumed = 0

    def __iter__(self):
        return self

    def __next__(self):
        try:
            value = next(self.values)
        except StopIteration:
            pytest.fail("Draw consumed more uniforms than supplied by its contract")
        self.consumed += 1
        return value


@pytest.fixture
def feed(monkeypatch):
    def install(scripts):
        calls = []
        tapes = []

        def uniforms(seed, *parts):
            key = (seed, *parts)
            assert key in scripts, f"Unexpected draw key: {key!r}"
            calls.append(key)
            tape = UniformTape(scripts[key])
            tapes.append(tape)
            return tape

        monkeypatch.setattr(rng, "uniforms", uniforms)
        return calls, tapes

    return install


def assert_run(actual, expected, quality_tolerance=2e-11):
    assert set(actual) == {"z_q", "z_t", "z_c", "q", "days", "cost"}
    for name in ("z_q", "z_t", "z_c"):
        assert type(actual[name]) is float
        assert actual[name] == pytest.approx(expected[name], rel=0, abs=3e-14)
    assert type(actual["q"]) is float
    assert 0 <= actual["q"] <= 10
    assert actual["q"] == pytest.approx(
        expected["q"], rel=0, abs=quality_tolerance
    )
    assert type(actual["days"]) is int
    assert actual["days"] == expected["days"]
    assert type(actual["cost"]) is float
    assert actual["cost"] == expected["cost"]


def test_uniform_stream_restart_interleaving_and_external_randomness():
    # Sections 1.1–1.5: streams depend only on their typed key.
    key = (73, "task-19", "manager", "refine", 4)
    first = rng.uniforms(*key)
    prefix = list(itertools.islice(first, 80))
    assert all(type(u) is float and 0 <= u < 1 for u in prefix)

    state = random.getstate()
    try:
        random.seed(999)
        for _ in range(100):
            random.random()
        list(itertools.islice(rng.uniforms(73, "unrelated"), 137))
        suffix = list(itertools.islice(first, 80))
    finally:
        random.setstate(state)

    assert prefix + suffix == list(itertools.islice(rng.uniforms(*key), 160))
    assert prefix == list(itertools.islice(rng.uniforms(*key), 80))


def test_key_boundaries_types_and_seed_are_not_aliased():
    # Section 1.2 and API: different lengths/types must remain distinct keys.
    keys = [
        (0,),
        (1,),
        (0, ""),
        (0, 1),
        (0, "1"),
        (0, "a", 1),
        (0, "a1"),
        (0, "a|b", "c"),
        (0, "a", "b|c"),
        (0, "a", "", "b"),
        (0, "a", "b"),
        (0, "é"),  # precomposed
        (0, "é"),  # decomposed: another string, so another key
        (0, 2**100),
        (0, str(2**100)),
    ]
    prefixes = [
        tuple(itertools.islice(rng.uniforms(*key), 32)) for key in keys
    ]
    assert len(set(prefixes)) == len(keys)


def test_uniforms_are_reproducible_across_process_hash_seeds():
    # Section 1.2: determinism survives process and hash randomization.
    code = (
        "import itertools,json; from market import rng; "
        "print(json.dumps(list(itertools.islice("
        "rng.uniforms(123,'task-12-3','test','buyer','R-0af',7),64))))"
    )
    outputs = []
    for hash_seed in ("1", "917"):
        env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        outputs.append(json.loads(result.stdout))
    local = list(itertools.islice(
        rng.uniforms(123, "task-12-3", "test", "buyer", "R-0af", 7), 64
    ))
    assert outputs[0] == outputs[1] == local


@pytest.mark.parametrize(
    "u",
    [0.0, 1e-14, LOW_U, 1e-10, 0.025, 0.25, 0.5, 0.75,
     0.975, HIGH_U, 1 - 1e-14, math.nextafter(1.0, 0.0)],
)
def test_normal_inverse_cdf_and_clipped_tails(u):
    # Section 2: clipped inverse-normal construction exposed by the API.
    actual = rng.normal(u)
    assert type(actual) is float
    assert actual == pytest.approx(expected_normal(u), rel=0, abs=3e-14)


def test_normal_clipping_plateaus_and_order():
    # Section 2: tails clip, rather than being redrawn or left unbounded.
    assert rng.normal(0.0) == rng.normal(1e-14) == rng.normal(LOW_U)
    assert rng.normal(HIGH_U) == rng.normal(1 - 1e-14)
    values = [rng.normal(u) for u in (0.0, 0.01, 0.5, 0.99, HIGH_U)]
    assert values == sorted(values)


@pytest.mark.parametrize("x", [0, 1, 15, 16, 175, 2048, 4094, 4095])
def test_hex_integer_cells_use_one_uniform(x):
    # Section 2 and API: [0,4095] is exactly floor(4096*u).
    for u in (x / 4096, (x + 0.5) / 4096):
        tape = UniformTape([u])
        actual = rng.integer(tape, 0, 4095)
        assert type(actual) is int
        assert actual == x
        assert tape.consumed == 1


@pytest.mark.parametrize("a,b", [(-4, 3), (40, 80), (4, 9), (2**100, 2**100 + 40)])
def test_integer_inclusive_bounds_and_monotonic_partition(a, b):
    # Section 2: increasing equal-width cells, including arbitrary-size integers.
    values = []
    for u in (0.0, 0.125, 0.25, 0.5, 0.75, 0.875, 0.999):
        tape = UniformTape([u])
        value = rng.integer(tape, a, b)
        assert type(value) is int
        assert a <= value <= b
        assert value == a + math.floor(u * (b - a + 1))
        assert tape.consumed == 1
        values.append(value)
    assert values[0] == a
    assert values[-1] == b
    assert values == sorted(values)


@pytest.mark.parametrize("value", [-2**150, 0, 17, 2**150])
def test_integer_singleton(value):
    # Section 2: inclusive equal endpoints have one possible outcome.
    assert rng.integer(itertools.repeat(0.25), value, value) == value


def test_integer_redraw_tail_and_ranges_wider_than_53_bits():
    # Section 2/API: redraws and wide ranges must remain valid and deterministic.
    us = [math.nextafter(1.0, 0.0)] + [0.25, 0.5, 0.75, 0.125] * 128
    for a, b in [(0, 2), (-2**100, 2**100), (0, 2**54 - 1)]:
        first = rng.integer(iter(us), a, b)
        second = rng.integer(iter(us), a, b)
        assert type(first) is int
        assert a <= first <= b
        assert first == second


@pytest.mark.parametrize("size", [0, 1, 2, 9])
def test_permutation_is_new_preserves_items_and_depends_only_on_positions(size):
    # Section 2: uniform permutation; no particular shuffle algorithm is fixed.
    items = [object() for _ in range(size)]
    original = items[:]
    us = [0.17, 0.63, 0.28, 0.84] * 100
    positions = rng.permutation(iter(us), range(size))
    actual = rng.permutation(iter(us), iter(items))
    assert type(actual) is list
    assert actual is not items
    assert sorted(positions) == list(range(size))
    assert actual == [items[i] for i in positions]
    assert items == original
    assert rng.permutation(iter(us), items) == actual


def test_permutation_preserves_duplicate_unhashable_items():
    # Section 2/API: any iterable, with multiplicities preserved.
    items = [[1], [1], [2], [3]]
    original = copy.deepcopy(items)
    result = rng.permutation(itertools.repeat(0.375), items)
    assert sorted(result) == sorted(original)
    assert items == original
    assert result is not items


@pytest.mark.parametrize(
    "mean,sd,z,tolerance",
    [
        (5.0, 1.0, 0.0, 2e-12),
        (0.0, 1.0, 0.0, 2e-12),
        (10.0, 1.0, 0.0, 2e-12),
        (6.0, 2.5, -0.0379, 2e-12),
        (7.0, 1.2, 1.8723, 2e-12),
        (9.0, 1.0, -7.0, 2e-11),
        (5.0, 1.0, 5.0, 6e-10),
        (5.0, 1.0, expected_normal(HIGH_U), 8e-5),
        (5.0, 1.0, expected_normal(LOW_U), 2e-11),
        (-8.0, 1.0, 0.0, 8e-5),
        (18.0, 1.0, 0.0, 2e-11),
        (-100.0, 1.0, 0.0, 2e-11),
        (100.0, 1.0, 0.0, 2e-11),
        (5.0, 0.001, 0.37, 2e-12),
    ],
)
def test_tn_inverse_cdf_probability_clipping_and_final_bounds(
    mean, sd, z, tolerance
):
    # Section 2: both probability clamps and final [0,10] clamp are required.
    actual = rng.tn(mean, sd, z)
    assert type(actual) is float
    assert 0 <= actual <= 10
    assert actual == pytest.approx(
        expected_tn(mean, sd, z), rel=0, abs=tolerance
    )


@pytest.mark.parametrize("mean,sd", [(0.0, 2.5), (5.0, 1.2), (10.0, 0.8)])
def test_tn_preserves_rank_without_rounding(mean, sd):
    # Section 2: inverse-CDF rank coupling, not rejection sampling.
    zs = [-5.0, -2.0, -0.3, 0.0, 0.7, 2.0, 5.0]
    qualities = [rng.tn(mean, sd, z) for z in zs]
    assert qualities == sorted(qualities)
    for z, q in zip(zs[1:-1], qualities[1:-1]):
        a, b = phi(-mean / sd), phi((10 - mean) / sd)
        rank = (phi((q - mean) / sd) - a) / (b - a)
        assert rank == pytest.approx(phi(z), rel=0, abs=2e-13)
    assert any(q != round(q, 4) for q in qualities)


@pytest.mark.parametrize(
    "contract,stream",
    [
        ("contract-1", "task-1"),
        ("contract-37", "task-37"),
        ("contract-1-2", "task-1-2"),
        ("contract-37-19", "task-37-19"),
    ],
)
def test_contract_stream_key(contract, stream):
    # Section 3: first contracts and repost contracts have distinct streams.
    assert rng.stream_key(contract) == stream


@pytest.mark.parametrize("role,prefix", [("buyer", "B"), ("manager", "M")])
@pytest.mark.parametrize("x", [0, 1, 175, 2748, 4095])
def test_participant_id_format_and_other_role_is_harmless(feed, role, prefix, x):
    # Section 3.1: exactly three lowercase hex digits; collision is on full ID.
    key = (11, "id", role, 3, 0)
    calls, tapes = feed({key: [(x + 0.5) / 4096]})
    other = "M" if prefix == "B" else "B"
    used = {f"{other}-{x:03x}"}
    assert rng.participant_id(11, role, 3, used) == f"{prefix}-{x:03x}"
    assert calls == [key]
    assert tapes[0].consumed == 1
    assert used == {f"{other}-{x:03x}"}


def test_participant_collision_attempts_and_membership_only_container(feed):
    # Section 3.1: retry attempts start at zero and advance only on collision.
    class Used:
        def __contains__(self, item):
            return item in {"B-000", "B-0af"}

    keys = [(19, "id", "buyer", 2, attempt) for attempt in range(3)]
    calls, tapes = feed(dict(zip(keys, [[0.0], [175 / 4096], [4095 / 4096]])))
    assert rng.participant_id(19, "buyer", 2, Used()) == "B-fff"
    assert calls == keys
    assert [t.consumed for t in tapes] == [1, 1, 1]


@pytest.mark.parametrize(
    "minimum,p,cap,us,expected",
    [
        (10, 0.1, 54, [0.7543, 0.4056, 0.1340, 0.8446, 0.0525], 14),
        (10, 0.1, 54, [0.0], 10),
        (10, 0.1, 54, [0.1, 0.099], 11),
        (10, 0.1, 54, [0.9] * 44, 54),
        (10, 0.05, 100, [0.8] * 90, 100),
        (10, 0.2, 31, [0.8] * 21, 31),
        (10, 0.5, 17, [0.8] * 7, 17),
        (1, 0.5, 8, [0.5, 0.0], 2),
    ],
)
def test_horizon_stopping_rule_cap_and_examples(feed, minimum, p, cap, us, expected):
    # Section 3.2; config 6.1: caller supplies cap; stop uses strict u < p.
    key = (8, "horizon")
    calls, tapes = feed({key: us})
    actual = rng.horizon(8, minimum, p, cap)
    assert type(actual) is int
    assert actual == expected
    assert calls == [key]
    assert tapes[0].consumed == len(us)


def test_horizon_already_at_cap_consumes_no_uniform(feed):
    # Section 3.2/API: no uniform is consumed once L equals the supplied cap.
    calls, tapes = feed({(8, "horizon"): []})
    assert rng.horizon(8, 10, 0.1, 10) == 10
    assert sum(t.consumed for t in tapes) == 0


@pytest.mark.parametrize(
    "lo,hi,decimals,u,expected",
    [
        (0.6, 1.4, 2, 0.951777, 1.36),
        (0.6, 1.4, 2, 0.0, 0.6),
        (0.6, 1.4, 2, math.nextafter(1.0, 0.0), 1.4),
        (1.0, 1.0, 2, 0.375, 1.0),
        (1.0, 2.0, 2, 0.125, 1.12),
        (1.0, 2.0, 2, 0.375, 1.38),
        (1.0, 2.0, 1, 0.25, 1.2),
        (1.0, 2.0, 1, 0.75, 1.8),
        (1.0, 4.0, 0, 0.5, 2.0),
    ],
)
@pytest.mark.parametrize("role", ["buyer", "manager"])
def test_share_defaults_endpoints_and_ties_to_even(
    feed, role, lo, hi, decimals, u, expected
):
    # Section 3.3 and README rounding: round the exact double, ties to even.
    key = (2, "share", role, 7)
    calls, tapes = feed({key: [u]})
    actual = rng.share(2, role, 7, lo, hi, decimals)
    assert type(actual) is float
    assert actual == expected
    assert calls == [key]
    assert tapes[0].consumed == 1


@pytest.mark.parametrize(
    "p,us,expected",
    [
        (0.5, [0.5], 0),
        (0.5, [0.0, 0.49, 0.5], 2),
        (0.5, [0.9], 0),
        (0.25, [0.249, 0.25], 1),
        (0.75, [0.0] * 1000 + [0.75], 1000),
    ],
)
def test_arrival_geometric_count_strict_boundary_and_no_artificial_cap(
    feed, p, us, expected
):
    # Section 3.4: count successes before the first u >= p, including zero.
    key = (6, "arrivals", 2, 0)
    calls, tapes = feed({key: us})
    actual = rng.arrivals(6, 2, 0, p)
    assert type(actual) is int
    assert actual == expected
    assert calls == [key]
    assert tapes[0].consumed == expected + 1


@pytest.mark.parametrize(
    "name,args,key,lo,hi",
    [
        ("worth", (4, 1, 0, 0), (4, "worth", 1, 0, 0), 40, 80),
        ("worth", (4, 7, 2, 5), (4, "worth", 7, 2, 5), 1, 8),
        ("deadline", (4, "task-19"), (4, "task-19", "deadline"), 4, 9),
        ("deadline", (4, "task-20"), (4, "task-20", "deadline"), 1, 8),
    ],
)
@pytest.mark.parametrize("u", [0.0, 0.5, 0.999])
def test_worth_and_deadline_inclusive_ranges_and_keys(feed, name, args, key, lo, hi, u):
    # Sections 3.5–3.6: worth uses day/seat/j; deadline uses the task ID.
    calls, tapes = feed({key: [u]})
    actual = getattr(rng, name)(*args, lo, hi)
    assert type(actual) is int
    assert actual == lo + math.floor(u * (hi - lo + 1))
    assert calls == [key]
    assert tapes[0].consumed == 1


def test_worth_and_deadline_singleton_ranges(feed):
    # Sections 3.5–3.6: equal range endpoints are permitted by config.md.
    feed({
        (1, "worth", 1, 0, 0): [0.25],
        (1, "task-1", "deadline"): [0.25],
    })
    assert rng.worth(1, 1, 0, 0, 71, 71) == 71
    assert rng.deadline(1, "task-1", 4, 4) == 4


def test_round_order_retains_all_seats_and_uses_day_and_round_key(feed):
    # Section 3.7: permute all seats, including participants out of business.
    participants = [
        {"id": "B-001", "out": False},
        {"id": "B-002", "out": False},
        {"id": "M-001", "out": True},
        {"id": "M-002", "out": False},
    ]
    original = copy.deepcopy(participants)
    key = (3, "order", 9, 2)
    us = [0.17, 0.63, 0.28, 0.84] * 100
    calls, _ = feed({key: us})
    actual = rng.order(3, 9, 2, participants)
    assert type(actual) is list
    assert actual is not participants
    assert sorted(p["id"] for p in actual) == sorted(p["id"] for p in participants)
    assert participants == original
    assert calls == [key]

    participants[2]["out"] = False
    again = rng.order(3, 9, 2, participants)
    assert [p["id"] for p in again] == [p["id"] for p in actual]


@pytest.mark.parametrize(
    "worker,kind,parent,zs,shown_q,days,cost",
    [
        ("employee", "fresh", None, (-0.0379, 1.0489, -0.3925), 5.7649, 2, 3.64),
        ("manager", "fresh", None, (1.8723, -1.0971, 0.4217), 9.1499, 2, 8.62),
        ("employee", "refine", 5.7648, (0.4241, -3.1075, -1.0166), 7.9737, 1, 2.35),
        ("manager", "refine", 3.9116, (-0.8212, 0.9382, -0.1958), 7.5100, 2, 12.92),
    ],
)
def test_run_all_four_worked_examples(feed, worker, kind, parent, zs, shown_q, days, cost):
    # Section 3.8: displayed z values determine formulas; displayed q is approximate.
    us = [phi(z) for z in zs]
    key = (17, "task-8", worker, kind, 0)
    calls, tapes = feed({key: us})
    actual = rng.run(17, "task-8", worker, kind, 0, copy.deepcopy(PRODUCTION), parent)
    assert_run(actual, expected_run(PRODUCTION, worker, kind, us, parent))
    assert actual["q"] == pytest.approx(shown_q, rel=0, abs=2e-4)
    assert actual["days"] == days
    assert actual["cost"] == cost
    assert calls == [key]
    assert tapes[0].consumed == 3


@pytest.mark.parametrize("worker", ["employee", "manager"])
@pytest.mark.parametrize("kind", ["fresh", "refine"])
@pytest.mark.parametrize("parent", [0.0, 5.7648123456789, 10.0])
def test_run_custom_parameters_and_unrounded_parent(feed, worker, kind, parent):
    # Section 3.8: configurable worker-specific formulas, with unrounded quality.
    production = copy.deepcopy(PRODUCTION)
    production.update(cost_ref_worth=125, sig_t=0.47, sig_c=0.61)
    production["employee"].update(
        mu=2.3, sd=1.7, gain=2.6, sd_ref=0.9,
        t_med=1.7, t_ref_med=0.43, fee=1.125,
        cost_frac=0.021, ref_cost_frac=0.013,
    )
    production["manager"].update(
        mu=8.1, sd=0.7, gain=8.4, sd_ref=1.1,
        t_med=3.2, t_ref_med=1.4, k=0.19, ref_cost_fixed=23.7,
    )
    before = copy.deepcopy(production)
    us = [0.37, 0.83, 0.19]
    key = (22, "task-5-3", worker, kind, 7)
    feed({key: us})
    actual = rng.run(22, "task-5-3", worker, kind, 7, production, parent)
    assert_run(actual, expected_run(production, worker, kind, us, parent))
    assert production == before
    assert actual["q"] != round(actual["q"], 4)


@pytest.mark.parametrize(
    "worker,kind,expected_cost",
    [
        ("employee", "fresh", 4.0),
        ("employee", "refine", 2.8),
        ("manager", "fresh", 8.0),
        ("manager", "refine", 14.0),
    ],
)
def test_default_expected_cost_at_unit_multiplier(feed, worker, kind, expected_cost):
    # Section 3.8: z_c = sig_c/2 makes the cost multiplier one.
    us = [0.5, 0.6, phi(0.125)]
    feed({(1, "task-1", worker, kind, 0): us})
    actual = rng.run(1, "task-1", worker, kind, 0, PRODUCTION, 5.0)
    assert actual["cost"] == expected_cost


@pytest.mark.parametrize("median,expected", [(0.01, 1), (1.0, 1), (1.01, 2), (2.0, 2), (2.01, 3)])
def test_days_ceil_minimum_and_zero_time_spread(feed, median, expected):
    # Section 3.8/config: sig_t may be zero; days are ceil, with minimum one.
    production = copy.deepcopy(PRODUCTION)
    production["sig_t"] = 0.0
    production["manager"]["t_med"] = median
    feed({(1, "task-1", "manager", "fresh", 0): [0.5, 0.9, 0.5]})
    actual = rng.run(1, "task-1", "manager", "fresh", 0, production)
    assert type(actual["days"]) is int
    assert actual["days"] == expected


def test_clipped_normal_manager_duration_example(feed):
    # Section 2: u=1-1e-14 gives a default manager duration of 30, not 37.
    feed({(1, "task-1", "manager", "fresh", 0): [0.5, 1 - 1e-14, 0.5]})
    actual = rng.run(1, "task-1", "manager", "fresh", 0, PRODUCTION)
    assert actual["z_t"] == pytest.approx(7.03449, abs=5e-6)
    assert actual["days"] == 30


def test_run_variates_are_separate_and_parent_only_changes_refinement_quality(feed):
    # Sections 1.4, 3.8: rechecks reuse k; parent affects only refinement mean.
    key = (7, "task-6", "employee", "refine", 4)
    scripts = {key: [0.4, 0.7, 0.2]}
    feed(scripts)
    first = rng.run(7, "task-6", "employee", "refine", 4, PRODUCTION, 1.0)
    repeated = rng.run(7, "task-6", "employee", "refine", 4, PRODUCTION, 1.0)
    different_parent = rng.run(7, "task-6", "employee", "refine", 4, PRODUCTION, 8.0)
    assert repeated == first
    assert different_parent["q"] > first["q"]
    for field in ("z_q", "z_t", "z_c", "days", "cost"):
        assert different_parent[field] == first[field]

    for index, affected in [(0, {"z_q", "q"}), (1, {"z_t", "days"}), (2, {"z_c", "cost"})]:
        changed_us = scripts[key][:]
        changed_us[index] = 0.95
        feed({key: changed_us})
        changed = rng.run(7, "task-6", "employee", "refine", 4, PRODUCTION, 1.0)
        for field in set(first) - affected:
            assert changed[field] == first[field]
        assert changed[("z_q", "z_t", "z_c")[index]] != first[("z_q", "z_t", "z_c")[index]]


def test_fresh_parent_ignored_and_parameters_do_not_change_normals(feed):
    # Sections 1.3, 3.8: production changes values, never the keyed variates.
    key = (9, "task-2", "manager", "fresh", 3)
    feed({key: [0.2, 0.8, 0.3]})
    a = rng.run(9, "task-2", "manager", "fresh", 3, PRODUCTION)
    b = rng.run(9, "task-2", "manager", "fresh", 3, PRODUCTION, 9.87654321)
    assert a == b

    changed = copy.deepcopy(PRODUCTION)
    changed.update(cost_ref_worth=160, sig_t=0.1, sig_c=0.7)
    changed["manager"].update(mu=3.0, sd=2.0)
    c = rng.run(9, "task-2", "manager", "fresh", 3, changed)
    for field in ("z_q", "z_t", "z_c"):
        assert c[field] == a[field]


@pytest.mark.parametrize("median,sig_t", [(1e308, 1.0), (1.0, 1000.0)])
def test_nonfinite_day_count_raises_overflow(feed, median, sig_t):
    # Section 3.8/API: both multiplication and exp overflow make days invalid.
    production = copy.deepcopy(PRODUCTION)
    production["manager"]["t_med"] = median
    production["sig_t"] = sig_t
    feed({(1, "task-1", "manager", "fresh", 0): [0.5, phi(2.0), 0.5]})
    with pytest.raises(OverflowError):
        rng.run(1, "task-1", "manager", "fresh", 0, production)


def test_nonfinite_cost_is_returned_for_caller_to_check(feed):
    # Section 3.8/API and config finiteness: cost may overflow while days stay valid.
    production = copy.deepcopy(PRODUCTION)
    production["manager"]["k"] = 1e306
    feed({(1, "task-1", "manager", "fresh", 0): [0.5, 0.5, HIGH_U]})
    actual = rng.run(1, "task-1", "manager", "fresh", 0, production)
    assert type(actual["cost"]) is float
    assert actual["cost"] == math.inf
    assert actual["days"] == 3


def test_label_collisions_advance_draw_count_and_leave_used_unchanged(feed):
    # Section 3.9: discarded draws count; used includes running and arrived results.
    keys = [(5, "task-3-2", "label", n) for n in (8, 9, 10)]
    calls, tapes = feed(dict(zip(keys, [[0.0], [175 / 4096], [4095 / 4096]])))
    used = {"R-000", "R-0af"}
    actual = rng.label(5, "task-3-2", 8, used)
    assert actual == {"label": "R-fff", "n": 11}
    assert type(actual["n"]) is int
    assert used == {"R-000", "R-0af"}
    assert calls == keys
    assert [t.consumed for t in tapes] == [1, 1, 1]


@pytest.mark.parametrize("x", [0, 1, 175, 4095])
def test_label_format_and_contract_local_uniqueness(feed, x):
    # Section 3.9: labels may repeat across contracts and have their own keys.
    keys = [(4, stream, "label", 0) for stream in ("task-1", "task-1-2")]
    feed({key: [(x + 0.5) / 4096] for key in keys})
    for stream in ("task-1", "task-1-2"):
        assert rng.label(4, stream, 0, set()) == {"label": f"R-{x:03x}", "n": 1}


def test_last_unused_label_can_be_drawn(feed):
    # Section 3.9: 4095 existing labels still permits the remaining one.
    used = {f"R-{x:03x}" for x in range(4095)}
    feed({(2, "task-1", "label", 6000): [4095 / 4096]})
    assert rng.label(2, "task-1", 6000, used) == {"label": "R-fff", "n": 6001}
    assert len(used) == 4095


@pytest.mark.parametrize(
    "mode,q,sd,z,expected",
    [
        ("manager", 9.1498, 0.5, -0.78218, 8.8),
        ("buyer", 9.1498, 2.0, -0.78218, 7.6),
        ("arbiter", 7.0219, 0.5, -1.15229, 6.4),
    ],
)
def test_reading_and_arbiter_worked_examples(feed, mode, q, sd, z, expected):
    # Sections 3.10–3.11: default noise scales and the two worked readings.
    if mode == "arbiter":
        key = (8, "task-9", "arbiter", "R-0af")
    else:
        key = (8, "task-9", "test", mode, "R-0af", 3)
    calls, tapes = feed({key: [phi(z)]})
    if mode == "arbiter":
        actual = rng.arbiter(8, "task-9", "R-0af", q, sd, 1)
    else:
        actual = rng.reading(8, "task-9", mode, "R-0af", 3, q, sd, 1)
    assert set(actual) == {"z", "reading"}
    assert type(actual["z"]) is float
    assert actual["z"] == pytest.approx(z, rel=0, abs=3e-14)
    assert type(actual["reading"]) is float
    assert actual["reading"] == expected
    assert calls == [key]
    assert tapes[0].consumed == 1


@pytest.mark.parametrize("mode", ["manager", "buyer", "arbiter"])
@pytest.mark.parametrize("decimals", [0, 1, 2, 3])
@pytest.mark.parametrize("q,u", [(0.1, 0.001), (9.9, 0.999), (5.123456789, 0.73)])
def test_readings_clip_then_round_unrounded_quality(feed, mode, decimals, q, u):
    # Sections 3.10–3.11: bounds, supported precision, and unrounded input q.
    sd = 2.0 if mode == "buyer" else 0.5
    if mode == "arbiter":
        key = (6, "task-2-3", "arbiter", "R-001")
    else:
        key = (6, "task-2-3", "test", mode, "R-001", 11)
    feed({key: [u]})
    if mode == "arbiter":
        result = rng.arbiter(6, "task-2-3", "R-001", q, sd, decimals)
    else:
        result = rng.reading(6, "task-2-3", mode, "R-001", 11, q, sd, decimals)
    expected = round(min(10, max(0, q + sd * expected_normal(u))), decimals)
    assert result["reading"] == expected
    assert type(result["reading"]) is float
    assert 0 <= result["reading"] <= 10


@pytest.mark.parametrize(
    "q,decimals,expected",
    [(2.5, 0, 2.0), (3.5, 0, 4.0), (1.25, 1, 1.2),
     (1.75, 1, 1.8), (1.125, 2, 1.12), (1.375, 2, 1.38),
     (1.0625, 3, 1.062), (1.1875, 3, 1.188), (2.675, 2, 2.67)],
)
def test_zero_noise_arbiter_exact_double_rounding(feed, q, decimals, expected):
    # Section 3.11/config and README: zero arbiter_sd is valid; ties are even.
    feed({(1, "task-1", "arbiter", "R-000"): [0.73]})
    result = rng.arbiter(1, "task-1", "R-000", q, 0.0, decimals)
    assert result["reading"] == expected


def test_reading_counters_sides_labels_streams_and_parameters(feed):
    # Sections 1.4, 1.6, 3.10, 4: explicit counters suffice for retry/resume.
    arguments = [
        ("task-1", "manager", "R-001", 0),
        ("task-1", "manager", "R-001", 1),
        ("task-1", "buyer", "R-001", 0),
        ("task-1", "manager", "R-002", 0),
        ("task-1-2", "manager", "R-001", 0),
    ]
    keys = [(31, stream, "test", side, label, i) for stream, side, label, i in arguments]
    calls, _ = feed({key: [u] for key, u in zip(keys, [0.1, 0.2, 0.3, 0.4, 0.7])})
    original = rng.reading(31, *arguments[0], 5.0, 0.5, 1)
    for args in arguments[1:]:
        rng.reading(31, *args, 5.0, 0.5, 1)
    assert rng.reading(31, *arguments[0], 5.0, 0.5, 1) == original
    changed = rng.reading(31, *arguments[0], 8.0, 2.0, 3)
    assert changed["z"] == original["z"]
    assert calls == keys + [keys[0], keys[0]]


def test_run_counter_namespaces_and_no_hidden_progress(feed):
    # Sections 1.4, 1.6, 3.8, 4: k is separate per stream, worker, and kind.
    arguments = [
        ("task-1", "employee", "fresh", 0),
        ("task-1", "employee", "fresh", 1),
        ("task-1", "employee", "refine", 0),
        ("task-1", "manager", "fresh", 0),
        ("task-1", "manager", "refine", 0),
        ("task-1-2", "employee", "fresh", 0),
    ]
    keys = [(41, *args) for args in arguments]
    calls, _ = feed({key: [0.3, 0.6, 0.7] for key in keys})
    original = rng.run(41, *arguments[0], PRODUCTION, 5.0)
    for args in arguments[1:]:
        rng.run(41, *args, PRODUCTION, 5.0)
    assert rng.run(41, *arguments[0], PRODUCTION, 5.0) == original
    assert calls == keys + [keys[0]]


def test_extra_seats_do_not_change_existing_seat_draws():
    # Sections 3.1, 3.3, 3.4, 4: extending seat lists preserves prior seats.
    def seats(count):
        used = set()
        records = []
        for seat in range(count):
            participant = rng.participant_id(73, "buyer", seat, used)
            used.add(participant)
            records.append((
                participant,
                rng.share(73, "buyer", seat, 0.6, 1.4, 2),
                rng.arrivals(73, 2, seat, 0.5),
            ))
        return records

    assert seats(3) == seats(7)[:3]
