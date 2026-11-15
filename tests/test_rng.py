"""spec/randomness.md: the keyed-draw contract, the constructions, and every draw's key, law and worked numbers."""

import itertools
import json
import math
import os
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from market import rng

N = 4000
STD = statistics.NormalDist()
PRODUCTION = {
    "cost_ref_worth": 80, "sig_t": 0.35, "sig_c": 0.25,
    "employee": {"mu": 6.0, "sd": 2.5, "gain": 4.0, "sd_ref": 1.5, "t_med": 1.0, "t_ref_med": 0.7,
                 "fee": 1.0, "cost_frac": 0.0375, "ref_cost_frac": 0.0225, "capacity": 2},
    "manager": {"mu": 7.0, "sd": 1.2, "gain": 7.0, "sd_ref": 0.8, "t_med": 2.5, "t_ref_med": 1.0,
                "k": 0.1, "ref_cost_fixed": 14.0, "capacity": 1},
}


def first(n, *key):
    return list(itertools.islice(rng.uniforms(*key), n))


def inject(monkeypatch, streams):
    """Replace the generator: `streams` is one list of uniforms for every key, or a dict key -> list.
    Returns the list of keys asked for."""
    asked = []

    def fake(*key):
        asked.append(key)
        return iter(streams[key] if isinstance(streams, dict) else streams)

    monkeypatch.setattr(rng, "uniforms", fake)
    return asked


def corr(xs, ys):
    return statistics.correlation(xs, ys)


# ---- 1. The keyed-draw contract -------------------------------------------------------------------------------


def test_uniforms_are_on_the_unit_interval_and_repeat():
    us = first(1000, 0, "share", "buyer", 0)
    assert all(0 <= u < 1 for u in us)
    assert us == first(1000, 0, "share", "buyer", 0)
    assert len(set(us)) == 1000


def test_same_key_same_variates_in_a_fresh_process():
    keys = [[0, "share", "buyer", 0], [7, "task-3-2", "test", "manager", "R-0af", 2], [0, "horizon"]]
    code = (
        "import itertools, json, sys; from market import rng; "
        "print(json.dumps([list(itertools.islice(rng.uniforms(*k), 5)) for k in json.loads(sys.argv[1])]))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, json.dumps(keys)],
        cwd=Path(__file__).parent.parent, env={**os.environ, "PYTHONHASHSEED": "4321"},
        capture_output=True, text=True, check=True,
    ).stdout
    assert json.loads(out) == [first(5, *k) for k in keys]


def test_distinct_keys_give_different_streams():
    keys = [
        (0, "a", 1), (0, "a1"), (0, "a", "1"), (0, "a|1"), (0, "a", 1, 0),
        (0, 1), (0, "1"), ("0", 1), (1, 0), (0,), (0, 0), ("0",), (0, ""), (0, "", ""),
        (0, "ab", "c"), (0, "a", "bc"), (0, "abc"), (0, 'a", "bc'), (0, "a, 1"), (0, "a]"), (0, "a", "]"),
        (0, "task-1", "label", 0), (0, "task-1", "arbiter", "R-000"), (0, "task-1", "deadline"),
        (0, "task-1-2", "label", 0), (0, "task-12", "label", 0), (1, "task-1", "label", 0),
        (0, "task-1", "employee", "fresh", 0), (0, "task-1", "manager", "fresh", 0),
        (0, "task-1", "employee", "refine", 0), (0, "task-1", "employee", "fresh", 1),
        (0, "task-1", "test", "manager", "R-000", 0), (0, "task-1", "test", "buyer", "R-000", 0),
        (0, "id", "buyer", 0, 0), (0, "id", "manager", 0, 0), (0, "id", "buyer", 0, 1), (0, "id", "buyer", 1, 0),
        (0, "share", "buyer", 0), (0, "order", 1, 2), (0, "order", 2, 1), (0, "arrivals", 1, 2),
        (0, "worth", 1, 2, 0), (0, "order", 12), (0, "order", 1, 20), (0, "order", 12, 0),
    ]
    streams = [tuple(first(4, *key)) for key in keys]
    assert len(set(streams)) == len(keys)
    values = [u for s in streams for u in s]
    assert len(set(values)) == len(values)


def test_a_keys_later_uniforms_are_not_another_keys_first():
    # the position in the stream must not read as one more part of the key
    assert first(3, 0, "a")[1] != first(1, 0, "a", 1)[0]
    assert first(13, 0, "a", 1)[12] != first(3, 0, "a", 11)[2]


def test_streams_behave_as_uniform_and_independent():
    a = [first(1, s, "share", "buyer", 0)[0] for s in range(N)]
    b = [first(1, s, "share", "buyer", 1)[0] for s in range(N)]
    c = first(N + 1, 0, "horizon")
    for xs in (a, b, c):
        assert abs(statistics.fmean(xs) - 0.5) < 0.02
        assert abs(statistics.pvariance(xs) - 1 / 12) < 0.006
        assert abs(sum(x < 0.1 for x in xs) / len(xs) - 0.1) < 0.02
    assert abs(corr(a, b)) < 0.06
    assert abs(corr(c[:-1], c[1:])) < 0.06


def test_draws_are_pure_functions_of_their_arguments():
    # 1.1, 1.4: no dependence on the order of draws or on other keys' draws
    a = rng.run(3, "task-1", "employee", "fresh", 0, PRODUCTION)
    rng.run(3, "task-1", "employee", "fresh", 1, PRODUCTION)
    rng.reading(3, "task-1", "manager", "R-abc", 0, 5.0, 0.5, 1)
    rng.horizon(3, 10, 0.1, 54)
    assert rng.run(3, "task-1", "employee", "fresh", 0, PRODUCTION) == a


# ---- 2. Variates and constructions -----------------------------------------------------------------------------


def test_normal_is_the_clipped_inverse_cdf():
    assert rng.normal(0.5) == 0
    assert rng.normal(STD.cdf(1.0)) == pytest.approx(1.0, abs=1e-12)
    assert rng.normal(0.975) == pytest.approx(1.959964, abs=1e-6)
    assert rng.normal(0.1) == pytest.approx(-rng.normal(0.9), abs=1e-12)
    assert rng.normal(0.0) == rng.normal(1e-13) == rng.normal(1e-12) == pytest.approx(-7.03448, abs=5e-6)
    assert rng.normal(1 - 1e-14) == rng.normal(1 - 1e-12) == pytest.approx(7.03449, abs=5e-6)
    assert rng.normal(1 - 1e-14) != pytest.approx(7.65073, abs=1e-3)
    zs = [rng.normal(u) for u in (0.001, 0.2, 0.5, 0.7, 0.999)]
    assert zs == sorted(zs)


def test_normal_law():
    zs = [rng.normal(u) for u in first(N, 0, "z")]
    assert abs(statistics.fmean(zs)) < 0.06
    assert abs(statistics.pstdev(zs) - 1) < 0.05
    assert abs(sum(z < 1 for z in zs) / N - STD.cdf(1)) < 0.03


def test_integer_from_hand_checked_uniforms():
    assert rng.integer(iter([0.0]), 0, 4095) == 0
    assert rng.integer(iter([0.5]), 0, 4095) == 2048
    assert rng.integer(iter([1 - 2**-53]), 0, 4095) == 4095
    assert rng.integer(iter([0.25]), 40, 80) == 50  # the 11th of 41 equal parts
    assert rng.integer(iter([0.999]), 40, 80) == 80
    assert rng.integer(iter([0.3]), 7, 7) == 7
    assert [rng.integer(iter([u]), 4, 9) for u in (0.0, 0.17, 0.34, 0.5, 0.67, 0.84)] == [4, 5, 6, 7, 8, 9]


def test_integer_is_exact():
    # 2**53 is not a multiple of 3: the 2 top values are redrawn, so each of the 3 integers keeps 1/3
    us = iter([1 - 2**-53, 1 - 2**-52, 0.5])
    assert rng.integer(us, 0, 2) == 1 and next(us, None) is None
    assert rng.integer(iter([1 - 3 * 2**-53]), 0, 2) == 2
    # a range wider than one uniform's 53 bits takes several
    us = iter([0.5, 0.0, 0.9])
    assert rng.integer(us, 0, 2**106 - 1) == 2**105 and next(us) == 0.9
    assert rng.integer(iter([0.0, 2**-53]), 10, 9 + 2**106) == 11
    big = [rng.integer(rng.uniforms(s, "big"), 0, 10**30) for s in range(300)]
    assert all(0 <= x <= 10**30 for x in big) and max(big) > 9 * 10**29 and min(big) < 10**29


def test_integer_law():
    counts = Counter(rng.integer(rng.uniforms(s, "int"), 4, 9) for s in range(N))
    assert sorted(counts) == [4, 5, 6, 7, 8, 9]
    assert all(abs(c / N - 1 / 6) < 0.03 for c in counts.values())


def test_permutation_from_hand_checked_uniforms():
    assert rng.permutation(iter([0.99, 0.99]), ["a", "b", "c"]) == ["a", "b", "c"]
    assert rng.permutation(iter([0.0, 0.0]), ["a", "b", "c"]) == ["b", "c", "a"]
    assert rng.permutation(iter([0.5, 0.0]), ["a", "b", "c"]) == ["c", "a", "b"]
    assert rng.permutation(iter([]), ["a"]) == ["a"] and rng.permutation(iter([]), []) == []
    items = ["a", "b", "c"]
    rng.permutation(iter([0.0, 0.0]), items)
    assert items == ["a", "b", "c"]


def test_permutation_law():
    counts = Counter(tuple(rng.permutation(rng.uniforms(s, "perm"), "abc")) for s in range(N))
    assert len(counts) == 6
    assert all(abs(c / N - 1 / 6) < 0.03 for c in counts.values())


def test_tn_against_the_formula():
    for mean, sd, z in [(6.0, 2.5, -0.0379), (7.0, 1.2, 1.8723), (7.45888, 1.5, 0.4241), (9.5, 0.8, 2.0),
                        (0.0, 2.5, -1.0), (10.0, 1.5, 0.3)]:
        dist = statistics.NormalDist(mean, sd)
        a, b = dist.cdf(0), dist.cdf(10)
        want = dist.inv_cdf(a + STD.cdf(z) * (b - a))
        assert rng.tn(mean, sd, z) == pytest.approx(want, abs=1e-9)
    assert rng.tn(5.0, 2.0, 0.0) == pytest.approx(5.0, abs=1e-12)  # symmetric: the median is the mean
    assert rng.tn(5.0, 1e-3, 1.0) == pytest.approx(5.001, abs=1e-9)  # [0, 10] far outside: plain Normal


def test_tn_stays_in_range_and_is_monotone_in_z():
    zs = [rng.normal(0.0), -3.0, -1.0, 0.0, 1.0, 3.0, rng.normal(1.0)]
    for mean, sd in [(6.0, 2.5), (7.0, 1.2), (0.0, 0.5), (10.0, 0.5), (5.0, 1e6), (9.99, 1e-3), (5.0, 1e-300)]:
        qs = [rng.tn(mean, sd, z) for z in zs]
        assert all(isinstance(q, float) and 0 <= q <= 10 for q in qs)
        assert qs == sorted(qs)
    # the clipping of p: [0, 10] deep in N's upper tail
    assert rng.tn(-1000.0, 1.0, 0.0) == 0
    assert 0 <= rng.tn(1e6, 1.0, 0.0) <= 10


def test_tn_law_is_the_normal_conditioned_on_0_10():
    mean, sd = 9.0, 2.0
    qs = [rng.tn(mean, sd, rng.normal(u)) for u in first(N, 0, "tn")]
    a, b = (0 - mean) / sd, (10 - mean) / sd
    mass = STD.cdf(b) - STD.cdf(a)
    want = mean + sd * (STD.pdf(a) - STD.pdf(b)) / mass
    assert abs(statistics.fmean(qs) - want) < 0.1
    assert abs(sum(q < 8 for q in qs) / N - (STD.cdf(-0.5) - STD.cdf(a)) / mass) < 0.03


# ---- 3. The draws: keys ----------------------------------------------------------------------------------------


def test_each_draw_uses_exactly_its_key(monkeypatch):
    asked = inject(monkeypatch, [0.5, 0.5, 0.5, 0.01])
    rng.participant_id(5, "buyer", 2, set())
    rng.horizon(5, 10, 0.1, 54)
    rng.share(5, "manager", 1, 0.6, 1.4, 2)
    rng.arrivals(5, 3, 1, 0.5)
    rng.worth(5, 3, 1, 0, 40, 80)
    rng.deadline(5, "task-9", 4, 9)
    rng.order(5, 3, 2, ["a", "b"])
    rng.run(5, "task-9", "employee", "fresh", 0, PRODUCTION)
    rng.run(5, "task-9-2", "manager", "refine", 3, PRODUCTION, 5.0)
    rng.label(5, "task-9", 4, set())
    rng.reading(5, "task-9", "buyer", "R-0af", 1, 5.0, 2.0, 1)
    rng.arbiter(5, "task-9", "R-0af", 5.0, 0.5, 1)
    assert asked == [
        (5, "id", "buyer", 2, 0),
        (5, "horizon"),
        (5, "share", "manager", 1),
        (5, "arrivals", 3, 1),
        (5, "worth", 3, 1, 0),
        (5, "task-9", "deadline"),
        (5, "order", 3, 2),
        (5, "task-9", "employee", "fresh", 0),
        (5, "task-9-2", "manager", "refine", 3),
        (5, "task-9", "label", 4),
        (5, "task-9", "test", "buyer", "R-0af", 1),
        (5, "task-9", "arbiter", "R-0af"),
    ]


def test_stream_key():
    assert rng.stream_key("contract-7") == "task-7"
    assert rng.stream_key("contract-7-2") == "task-7-2"
    assert rng.stream_key("contract-12-10") == "task-12-10"


def test_a_reposts_contract_has_draws_of_its_own():
    first_contract = rng.run(0, rng.stream_key("contract-4"), "employee", "fresh", 0, PRODUCTION)
    repost = rng.run(0, rng.stream_key("contract-4-2"), "employee", "fresh", 0, PRODUCTION)
    assert first_contract["z_q"] != repost["z_q"]
    assert rng.label(0, "task-4", 0, set()) != rng.label(0, "task-4-2", 0, set()) or \
        rng.label(0, "task-4", 1, set()) != rng.label(0, "task-4-2", 1, set())


# ---- 3.1 Participant IDs ---------------------------------------------------------------------------------------


def test_participant_id_text(monkeypatch):
    inject(monkeypatch, [0.0])
    assert rng.participant_id(0, "buyer", 0, set()) == "B-000"
    assert rng.participant_id(0, "manager", 0, set()) == "M-000"
    inject(monkeypatch, [0xC53 / 4096])
    assert rng.participant_id(0, "buyer", 0, set()) == "B-c53"
    inject(monkeypatch, [0x0A4 / 4096])
    assert rng.participant_id(0, "manager", 3, set()) == "M-0a4"
    inject(monkeypatch, [1 - 2**-53])
    assert rng.participant_id(0, "manager", 0, set()) == "M-fff"


def test_participant_id_is_drawn_again_until_new(monkeypatch):
    streams = {(0, "id", "buyer", 1, 0): [0.5], (0, "id", "buyer", 1, 1): [0.5], (0, "id", "buyer", 1, 2): [0.25],
               (0, "id", "manager", 1, 0): [0.5]}
    asked = inject(monkeypatch, streams)
    assert rng.participant_id(0, "buyer", 1, {"B-800"}) == "B-400"
    assert asked == [(0, "id", "buyer", 1, 0), (0, "id", "buyer", 1, 1), (0, "id", "buyer", 1, 2)]
    assert rng.participant_id(0, "buyer", 1, {"B-801", "M-800"}) == "B-800"
    # only participants of the same role can collide
    assert rng.participant_id(0, "manager", 1, {"B-800", "B-400"}) == "M-800"


def ids(seed, buyers, managers):
    out = []
    for role, seats in (("buyer", buyers), ("manager", managers)):
        for seat in range(seats):
            out.append(rng.participant_id(seed, role, seat, set(out)))
    return out


def test_participant_ids_law_and_seat_changes():
    for seed in range(3):
        got = ids(seed, 300, 300)
        assert len(set(got)) == 600
        assert all(len(i) == 5 and i[:2] in ("B-", "M-") and 0 <= int(i[2:], 16) <= 4095 and i[2:] == i[2:].lower()
                   for i in got)
        # section 4: more seats of a role leave every other seat's ID unchanged
        more = ids(seed, 320, 300)
        assert more[:300] == got[:300] and more[320:] == got[300:]
    xs = [int(rng.participant_id(s, "buyer", 0, set())[2:], 16) for s in range(N)]
    assert min(xs) < 60 and max(xs) > 4035
    assert abs(statistics.fmean(xs) - 2047.5) < 80
    assert abs(sum(x < 1024 for x in xs) / N - 0.25) < 0.03


def test_every_id_of_a_role_can_be_taken():
    used = {f"B-{x:03x}" for x in range(4096)} - {"B-7e1"}
    assert rng.participant_id(0, "buyer", 4095, used) == "B-7e1"


# ---- 3.2 The last arrival day L --------------------------------------------------------------------------------


def test_horizon_example(monkeypatch):
    us = iter([0.7543, 0.4056, 0.1340, 0.8446, 0.0525, 0.9])
    monkeypatch.setattr(rng, "uniforms", lambda *key: us)
    assert rng.horizon(0, 10, 0.1, 54) == 14
    assert next(us) == 0.9  # one uniform per day considered


def test_horizon_stops_at_the_cap_without_a_uniform(monkeypatch):
    us = iter([0.9] * 44)
    monkeypatch.setattr(rng, "uniforms", lambda *key: us)
    assert rng.horizon(0, 10, 0.1, 54) == 54
    assert next(us, None) is None
    monkeypatch.setattr(rng, "uniforms", lambda *key: iter([]))
    assert rng.horizon(0, 10, 0.1, 10) == 10
    inject(monkeypatch, [0.05])
    assert rng.horizon(0, 10, 0.1, 54) == 10
    inject(monkeypatch, [0.1, 0.0999])  # stop when u < end_prob, not at equality
    assert rng.horizon(0, 10, 0.1, 54) == 11


def test_horizon_law():
    ls = [rng.horizon(s, 10, 0.1, 54) for s in range(N)]
    assert all(isinstance(x, int) and 10 <= x <= 54 for x in ls)
    for j in range(4):
        assert abs(ls.count(10 + j) / N - 0.1 * 0.9**j) < 0.02
    assert abs(statistics.fmean(ls) - (10 + 9 * (1 - 0.9**44))) < 0.6
    assert ls.count(54) / N < 0.03
    short = [rng.horizon(s, 3, 0.5, 5) for s in range(N)]
    assert set(short) == {3, 4, 5}
    assert abs(short.count(3) / N - 0.5) < 0.03 and abs(short.count(5) / N - 0.25) < 0.03
    # the uniforms are the key's, whatever the parameters: a longer cap changes only the capped episodes
    assert all(a == min(b, 20) for a, b in zip((rng.horizon(s, 10, 0.1, 20) for s in range(500)), ls))


# ---- 3.3 Wealth shares -----------------------------------------------------------------------------------------


def test_share_example(monkeypatch):
    inject(monkeypatch, [0.951777])
    assert rng.share(0, "buyer", 0, 0.6, 1.4, 2) == 1.36
    assert rng.share(0, "buyer", 0, 0.6, 1.4, 0) == 1.0
    assert rng.share(0, "buyer", 0, 1, 1, 2) == 1
    inject(monkeypatch, [0.0])
    assert rng.share(0, "buyer", 0, 0.6, 1.4, 2) == 0.6


def test_share_law_and_keys():
    shares = [rng.share(s, "buyer", 0, 0.6, 1.4, 2) for s in range(N)]
    assert all(0.6 <= x <= 1.4 and x == round(x, 2) for x in shares)
    assert abs(statistics.fmean(shares) - 1.0) < 0.02
    assert abs(sum(x < 0.8 for x in shares) / N - 0.25) < 0.03
    assert len({rng.share(0, role, seat, 0.6, 1.4, 2) for role in ("buyer", "manager") for seat in range(8)}) > 8
    # the same U whatever the range and the decimals
    u = rng.share(0, "manager", 2, 0, 1, 15)
    assert rng.share(0, "manager", 2, 0.6, 1.4, 2) == round(0.6 + 0.8 * u, 2)
    assert u == pytest.approx(next(rng.uniforms(0, "share", "manager", 2)), abs=1e-15)


# ---- 3.4 Task counts -------------------------------------------------------------------------------------------


def test_arrivals_counts_the_uniforms_below_p(monkeypatch):
    inject(monkeypatch, [0.6, 0.1])
    assert rng.arrivals(0, 2, 0, 0.5) == 0
    us = iter([0.1, 0.4999, 0.2, 0.5, 0.1])
    monkeypatch.setattr(rng, "uniforms", lambda *key: us)
    assert rng.arrivals(0, 2, 0, 0.5) == 3
    assert next(us) == 0.1


def test_arrivals_law():
    counts = [rng.arrivals(0, day, seat, 0.5) for day in range(2, 2 + N // 4) for seat in range(4)]
    assert all(isinstance(n, int) and n >= 0 for n in counts)
    assert abs(statistics.fmean(counts) - 1.0) < 0.1
    for n in range(4):
        assert abs(counts.count(n) / N - 0.5 ** (n + 1)) < 0.03
    other = [rng.arrivals(1, day, 0, 0.25) for day in range(2, 2 + N)]
    assert abs(statistics.fmean(other) - 1 / 3) < 0.05
    # one key: a higher p can only add tasks
    assert all(rng.arrivals(0, d, 0, 0.3) <= rng.arrivals(0, d, 0, 0.6) for d in range(2, 300))


# ---- 3.5, 3.6 Worths and due-day offsets -----------------------------------------------------------------------


def test_worth_law():
    worths = [rng.worth(s, 1, 0, 0, 40, 80) for s in range(N)]
    assert set(worths) == set(range(40, 81))
    assert abs(statistics.fmean(worths) - 60) < 0.8
    assert abs(sum(w < 50 for w in worths) / N - 10 / 41) < 0.03
    assert rng.worth(0, 1, 0, 0, 55, 55) == 55
    # keyed by day, buyer seat and j
    a = [rng.worth(0, day, seat, j, 0, 10**9) for day in (1, 2) for seat in (0, 1) for j in (0, 1)]
    assert len(set(a)) == 8


def test_deadline_law():
    ds = [rng.deadline(0, f"task-{n}", 4, 9) for n in range(1, N + 1)]
    counts = Counter(ds)
    assert sorted(counts) == [4, 5, 6, 7, 8, 9]
    assert all(abs(c / N - 1 / 6) < 0.03 for c in counts.values())
    assert rng.deadline(0, "task-1", 3, 3) == 3


# ---- 3.7 The turn order ----------------------------------------------------------------------------------------


def test_order_law():
    people = ["B-001", "B-002", "M-001", "M-002", "M-003"]
    orders = [rng.order(0, day, r, people) for day in range(1, 1 + N // 4) for r in range(1, 5)]
    assert all(sorted(o) == people for o in orders)
    assert people == ["B-001", "B-002", "M-001", "M-002", "M-003"]
    for place in range(5):
        counts = Counter(o[place] for o in orders)
        assert all(abs(counts[p] / N - 0.2) < 0.03 for p in people)
    assert abs(sum(o.index("B-001") < o.index("M-003") for o in orders) / N - 0.5) < 0.03
    assert len({tuple(o) for o in orders}) == 120


def test_order_is_keyed_by_day_and_round_only():
    people = list("abcdefgh")
    assert rng.order(0, 3, 2, people) == rng.order(0, 3, 2, people)
    assert len({tuple(rng.order(0, d, r, people)) for d in (1, 2, 3) for r in (1, 2, 3)}) == 9
    # the same key permutes any list of that length the same way
    moved = rng.order(0, 3, 2, list(range(8)))
    assert rng.order(0, 3, 2, people) == [people[i] for i in moved]


# ---- 3.8 A run -------------------------------------------------------------------------------------------------


def run_with(monkeypatch, zs, worker, kind, q_parent=None):
    inject(monkeypatch, [STD.cdf(z) for z in zs])
    return rng.run(0, "task-1", worker, kind, 0, PRODUCTION, q_parent)


def test_run_examples(monkeypatch):
    r = run_with(monkeypatch, (-0.0379, 1.0489, -0.3925), "employee", "fresh")
    assert r["q"] == pytest.approx(5.7649, abs=3e-4) and r["days"] == 2 and r["cost"] == 3.64
    assert (r["z_q"], r["z_t"], r["z_c"]) == pytest.approx((-0.0379, 1.0489, -0.3925), abs=1e-12)
    assert 1.0 * math.exp(0.35 * r["z_t"]) == pytest.approx(1.4436, abs=1e-4)
    assert math.exp(0.25 * r["z_c"] - 0.25**2 / 2) == pytest.approx(0.8786, abs=1e-4)

    r = run_with(monkeypatch, (1.8723, -1.0971, 0.4217), "manager", "fresh")
    assert r["q"] == pytest.approx(9.1499, abs=3e-4) and r["days"] == 2 and r["cost"] == 8.62

    r = run_with(monkeypatch, (0.4241, -3.1075, -1.0166), "employee", "refine", 5.7648)
    assert r["q"] == pytest.approx(7.9737, abs=3e-4) and r["days"] == 1 and r["cost"] == 2.35

    r = run_with(monkeypatch, (-0.8212, 0.9382, -0.1958), "manager", "refine", 3.9116)
    assert r["q"] == pytest.approx(7.5100, abs=3e-4) and r["days"] == 2 and r["cost"] == 12.92


def test_run_at_the_clip_takes_30_days_not_37(monkeypatch):
    inject(monkeypatch, [0.5, 1 - 1e-14, 0.5])
    r = rng.run(0, "task-1", "manager", "fresh", 0, PRODUCTION)
    assert r["z_t"] == pytest.approx(7.03449, abs=5e-6) and r["days"] == 30
    assert math.ceil(2.5 * math.exp(0.35 * 7.65073)) == 37


def test_run_expected_costs_at_m_1(monkeypatch):
    z_c = 0.25 / 2  # m = exp(sig_c z_c - sig_c^2 / 2) = 1
    costs = {(w, k): run_with(monkeypatch, (0.0, 0.0, z_c), w, k, 5.0)["cost"]
             for w in ("employee", "manager") for k in ("fresh", "refine")}
    assert costs == {("employee", "fresh"): 4.0, ("employee", "refine"): 2.8,
                     ("manager", "fresh"): 8.0, ("manager", "refine"): 14.0}
    # the employee's fee is not multiplied by m: m = exp(-sig_c^2 / 2) at z_c = 0
    m = math.exp(-0.25**2 / 2)
    assert run_with(monkeypatch, (0.0, 0.0, 0.0), "employee", "fresh")["cost"] == round(1 + 3 * m, 2)
    assert run_with(monkeypatch, (0.0, 0.0, 0.0), "employee", "refine", 5.0)["cost"] == round(1 + 1.8 * m, 2)


def test_run_days_and_medians(monkeypatch):
    days = {(w, k): run_with(monkeypatch, (0.0, 0.0, 0.0), w, k, 5.0)["days"]
            for w in ("employee", "manager") for k in ("fresh", "refine")}
    assert days == {("employee", "fresh"): 1, ("employee", "refine"): 1,
                    ("manager", "fresh"): 3, ("manager", "refine"): 1}
    assert run_with(monkeypatch, (0.0, -7.0, 0.0), "manager", "fresh")["days"] == 1  # never below 1
    assert run_with(monkeypatch, (0.0, 0.01, 0.0), "employee", "fresh")["days"] == 2  # ceil
    assert all(isinstance(d, int) for d in days.values())


def test_run_quality_laws(monkeypatch):
    fresh = run_with(monkeypatch, (0.0, 0.0, 0.0), "manager", "fresh")
    assert fresh["q"] == pytest.approx(rng.tn(7.0, 1.2, 0.0)) and isinstance(fresh["q"], float)
    for worker, gain, sd_ref in (("employee", 4.0, 1.5), ("manager", 7.0, 0.8)):
        for q_p in (0.0, 3.9116, 10.0):
            got = run_with(monkeypatch, (0.7, 0.0, 0.0), worker, "refine", q_p)["q"]
            assert got == pytest.approx(rng.tn(q_p + gain * (10 - q_p) / 10, sd_ref, 0.7), abs=1e-9)
    # an unrounded quality
    assert run_with(monkeypatch, (0.123, 0.0, 0.0), "employee", "fresh")["q"] != round(
        run_with(monkeypatch, (0.123, 0.0, 0.0), "employee", "fresh")["q"], 4)


def test_run_variates_depend_on_the_key_alone():
    fresh = rng.run(0, "task-2", "employee", "fresh", 0, PRODUCTION)
    # a run refused for insufficient balance leaves k unchanged: the same variates, days, cost and quality
    assert rng.run(0, "task-2", "employee", "fresh", 0, PRODUCTION) == fresh
    # the variates do not depend on which result is refined; only the mean does
    low = rng.run(0, "task-2", "employee", "refine", 0, PRODUCTION, 2.0)
    high = rng.run(0, "task-2", "employee", "refine", 0, PRODUCTION, 8.0)
    for name in ("z_q", "z_t", "z_c", "days", "cost"):
        assert low[name] == high[name]
    assert low["q"] < high["q"]
    assert rng.run(0, "task-2", "employee", "refine", 0, PRODUCTION, 2.0) == low
    # nor on the parameters
    other = {**PRODUCTION, "sig_t": 0.9, "sig_c": 0.1, "employee": {**PRODUCTION["employee"], "mu": 2.0}}
    changed = rng.run(0, "task-2", "employee", "fresh", 0, other)
    assert [changed[n] for n in ("z_q", "z_t", "z_c")] == [fresh[n] for n in ("z_q", "z_t", "z_c")]
    assert changed["q"] < fresh["q"]
    # fresh and refine, the two workers, k and contracts are counted separately
    keys = [("task-2", "employee", "fresh", 0), ("task-2", "employee", "refine", 0),
            ("task-2", "manager", "fresh", 0), ("task-2", "manager", "refine", 0),
            ("task-2", "employee", "fresh", 1), ("task-3", "employee", "fresh", 0),
            ("task-2-2", "employee", "fresh", 0)]
    zs = [rng.run(0, *key, PRODUCTION, 5.0)[z] for key in keys for z in ("z_q", "z_t", "z_c")]
    assert len(set(zs)) == len(zs)
    assert rng.run(1, "task-2", "employee", "fresh", 0, PRODUCTION)["z_q"] != fresh["z_q"]


def test_run_law():
    runs = [rng.run(0, f"task-{n}", "employee", "fresh", 0, PRODUCTION) for n in range(1, N + 1)]
    zq, zt, zc = ([r[name] for r in runs] for name in ("z_q", "z_t", "z_c"))
    for zs in (zq, zt, zc):
        assert abs(statistics.fmean(zs)) < 0.06 and abs(statistics.pstdev(zs) - 1) < 0.05
    assert abs(corr(zq, zt)) < 0.06 and abs(corr(zq, zc)) < 0.06 and abs(corr(zt, zc)) < 0.06
    qs, days, costs = ([r[name] for r in runs] for name in ("q", "days", "cost"))
    assert all(0 <= q <= 10 for q in qs)
    a, b = (0 - 6.0) / 2.5, (10 - 6.0) / 2.5
    assert abs(statistics.fmean(qs) - (6.0 + 2.5 * (STD.pdf(a) - STD.pdf(b)) / (STD.cdf(b) - STD.cdf(a)))) < 0.15
    assert all(isinstance(d, int) and d >= 1 for d in days)
    assert abs(days.count(1) / N - 0.5) < 0.03 and abs(days.count(2) / N - 0.4762) < 0.03
    assert all(c > 1.0 and c == round(c, 2) for c in costs)
    assert abs(statistics.fmean(costs) - 4.0) < 0.06  # the multiplier's mean is 1
    assert abs(corr(qs, costs)) < 0.06 and abs(corr(qs, days)) < 0.06

    own = [rng.run(0, f"task-{n}", "manager", "refine", 2, PRODUCTION, 6.0) for n in range(1, N + 1)]
    assert abs(statistics.fmean(r["cost"] for r in own) - 14.0) < 0.25
    assert all(0 <= r["q"] <= 10 for r in own)
    assert abs(statistics.median(r["q"] for r in own) - rng.tn(8.8, 0.8, 0.0)) < 0.08


def test_run_values_that_are_not_finite(monkeypatch):
    # config.md, "Finiteness during an episode": a cost can come out infinite; the market stops on it
    inject(monkeypatch, [0.5, 0.5, 0.5])
    huge = {**PRODUCTION, "cost_ref_worth": 1.7e308, "manager": {**PRODUCTION["manager"], "k": 10.0}}
    assert rng.run(0, "task-1", "manager", "fresh", 0, huge)["cost"] == math.inf
    # a day count that is not a finite number has no integer
    inject(monkeypatch, [0.5, STD.cdf(1.0), 0.5])
    with pytest.raises(OverflowError):
        rng.run(0, "task-1", "manager", "fresh", 0, {**PRODUCTION, "sig_t": 1000.0})
    with pytest.raises(OverflowError):
        rng.run(0, "task-1", "manager", "fresh", 0, {**PRODUCTION, "sig_t": 1e308})
    assert rng.run(0, "task-1", "manager", "fresh", 0, {**PRODUCTION, "sig_t": 600.0})["days"] > 10**260


# ---- 3.9 Result labels -----------------------------------------------------------------------------------------


def test_label_text_and_counter(monkeypatch):
    inject(monkeypatch, [0xBAF / 4096])
    assert rng.label(0, "task-1", 0, set()) == {"label": "R-baf", "n": 1}
    assert rng.label(0, "task-1", 7, {"R-000"}) == {"label": "R-baf", "n": 8}
    inject(monkeypatch, [0.0])
    assert rng.label(0, "task-1", 0, set())["label"] == "R-000"


def test_label_discards_used_labels_and_counts_them(monkeypatch):
    streams = {(0, "task-1", "label", 2): [0.5], (0, "task-1", "label", 3): [0.5], (0, "task-1", "label", 4): [0.75],
               (0, "task-1", "label", 5): [0.25]}
    asked = inject(monkeypatch, streams)
    assert rng.label(0, "task-1", 2, {"R-800", "R-c00"}) == {"label": "R-400", "n": 6}
    assert asked == [(0, "task-1", "label", n) for n in (2, 3, 4, 5)]
    assert rng.label(0, "task-1", 2, {"R-c00"}) == {"label": "R-800", "n": 3}


def test_labels_are_unique_within_a_contract_and_keyed_by_it():
    used, n = set(), 0
    for _ in range(600):
        got = rng.label(0, "task-5", n, used)
        assert got["label"] not in used and got["n"] > n
        assert len(got["label"]) == 5 and got["label"][:2] == "R-" and 0 <= int(got["label"][2:], 16) <= 4095
        used.add(got["label"])
        n = got["n"]
    assert n > 600  # some draws were discarded, so n exceeds the number of results
    assert rng.label(0, "task-5", 0, set()) == rng.label(0, "task-5", 0, {"R-zzz"})
    xs = [int(rng.label(s, "task-5", 0, set())["label"][2:], 16) for s in range(N)]
    assert abs(statistics.fmean(xs) - 2047.5) < 80
    # two contracts may hold the same label; a contract's labels do not depend on the other's
    assert {rng.label(0, f"task-{t}", 0, set())["label"] for t in range(1, 50)} != \
        {rng.label(0, "task-5", 0, set())["label"]}


def test_the_last_free_label_is_found():
    used = {f"R-{x:03x}" for x in range(4096)} - {"R-07c"}
    assert rng.label(0, "task-1", 0, used)["label"] == "R-07c"


# ---- 3.10, 3.11 Readings ---------------------------------------------------------------------------------------


def test_reading_example(monkeypatch):
    inject(monkeypatch, [STD.cdf(-0.78218)])
    got = rng.reading(0, "task-1", "manager", "R-abc", 0, 9.1498, 0.5, 1)
    assert got == {"z": pytest.approx(-0.78218, abs=1e-12), "reading": 8.8}


def test_arbiter_example(monkeypatch):
    inject(monkeypatch, [STD.cdf(-1.15229)])
    got = rng.arbiter(0, "task-1", "R-abc", 7.0219, 0.5, 1)
    assert got == {"z": pytest.approx(-1.15229, abs=1e-12), "reading": 6.4}


def test_readings_clip_first_then_round(monkeypatch):
    inject(monkeypatch, [STD.cdf(2.0)])
    assert rng.reading(0, "task-1", "buyer", "R-abc", 0, 9.0, 2.0, 1)["reading"] == 10
    assert rng.arbiter(0, "task-1", "R-abc", 9.9, 0.5, 1)["reading"] == 10
    assert rng.reading(0, "task-1", "buyer", "R-abc", 0, 5.0, 2.0, 0)["reading"] == 9
    assert rng.reading(0, "task-1", "buyer", "R-abc", 0, 5.12345, 0.001, 3)["reading"] == 5.125
    inject(monkeypatch, [STD.cdf(-2.0)])
    assert rng.reading(0, "task-1", "buyer", "R-abc", 0, 1.0, 2.0, 1)["reading"] == 0
    assert rng.arbiter(0, "task-1", "R-abc", 0.5, 0.5, 1)["reading"] == 0
    assert rng.arbiter(0, "task-1", "R-abc", 6.26, 0, 1)["reading"] == 6.3  # arbiter_sd may be 0
    got = rng.reading(0, "task-1", "buyer", "R-abc", 0, 5.0, 2.0, 0)["reading"]
    assert isinstance(got, float)


def test_reading_keys_and_counters():
    base = rng.reading(0, "task-1", "manager", "R-abc", 0, 5.0, 0.5, 1)
    assert rng.reading(0, "task-1", "manager", "R-abc", 0, 5.0, 0.5, 1) == base
    others = [
        rng.reading(0, "task-1", "buyer", "R-abc", 0, 5.0, 0.5, 1),  # the two sides are counted separately
        rng.reading(0, "task-1", "manager", "R-abc", 1, 5.0, 0.5, 1),
        rng.reading(0, "task-1", "manager", "R-abd", 0, 5.0, 0.5, 1),
        rng.reading(0, "task-1-2", "manager", "R-abc", 0, 5.0, 0.5, 1),
        rng.reading(1, "task-1", "manager", "R-abc", 0, 5.0, 0.5, 1),
        rng.arbiter(0, "task-1", "R-abc", 5.0, 0.5, 1),
        rng.arbiter(0, "task-2", "R-abc", 5.0, 0.5, 1),
        rng.arbiter(0, "task-1", "R-abd", 5.0, 0.5, 1),
    ]
    assert len({r["z"] for r in others} | {base["z"]}) == 9
    # the variate is the key's, whatever the quality, the sd and the decimals
    assert rng.reading(0, "task-1", "manager", "R-abc", 0, 2.0, 2.0, 3)["z"] == base["z"]
    assert rng.arbiter(0, "task-1", "R-abc", 2.0, 0.1, 0)["z"] == rng.arbiter(0, "task-1", "R-abc", 5.0, 0.5, 1)["z"]


def test_reading_law():
    for draw, sd in ((lambda n: rng.reading(0, "task-1", "manager", "R-abc", n, 6.0, 0.5, 1), 0.5),
                     (lambda n: rng.reading(0, "task-1", "buyer", "R-abc", n, 6.0, 2.0, 1), 2.0),
                     (lambda n: rng.arbiter(0, f"task-{n + 1}", "R-abc", 6.0, 0.5, 1), 0.5)):
        got = [draw(n) for n in range(N)]
        zs, values = [g["z"] for g in got], [g["reading"] for g in got]
        assert abs(statistics.fmean(zs)) < 0.06 and abs(statistics.pstdev(zs) - 1) < 0.05
        assert all(0 <= v <= 10 and v == round(v, 1) for v in values)
        assert all(g["reading"] == round(min(10, max(0, 6.0 + sd * g["z"])), 1) for g in got)
        assert abs(statistics.fmean(values) - 6.0) < 0.12
        assert abs(statistics.pstdev(values) - sd) < 0.12
    edge = [rng.reading(0, "task-1", "buyer", "R-abc", n, 9.5, 2.0, 1)["reading"] for n in range(N)]
    assert max(edge) == 10 and abs(edge.count(10) / N - (1 - STD.cdf(0.225))) < 0.03
