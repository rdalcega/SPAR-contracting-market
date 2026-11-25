"""The loop's parts (hook, design, acceptance, writer, lineage) and a full dry run on the free mock."""

import json

import pytest

from learning import design, hook, lineage
from learning.accept import decide
from learning.anonymize import ID
from learning.logs import load_episode
from learning.loop import run_loop
from learning.writer import CAP, parse, revise
from market import config, prompts

# --- hook ---------------------------------------------------------------------------------------------------


def test_with_nothing_registered_every_prompt_is_the_markets_own():
    cfg = config.resolve({"name": "x"})
    before = prompts.system_prompt(cfg, "manager", "M-abc", 1.0, 0)
    hook.install()
    try:
        assert prompts.system_prompt(cfg, "manager", "M-abc", 1.0, 0) == before
        hook.register("x", {("manager", 0): "Do A.", ("buyer", 1): ""})
        with_text = prompts.system_prompt(cfg, "manager", "M-abc", 1.0, 0)
        assert with_text == before + "\n\n" + hook.HEADING + "\nDo A."
        assert prompts.system_prompt(cfg, "manager", "M-abc", 1.0, 1) == prompts.system_prompt(
            config.resolve({"name": "y"}), "manager", "M-abc", 1.0, 1)
        assert hook.strip_section(with_text) == before
    finally:
        hook.unregister("x")
        hook.uninstall()
    assert prompts.system_prompt(cfg, "manager", "M-abc", 1.0, 0) == before


# --- design -------------------------------------------------------------------------------------------------


def test_the_eight_seed_table_is_exactly_balanced_and_the_second_play_flips_it():
    rows = design.table(8)
    assert design.balanced(rows)
    plays = design.plays(list(range(8)), rows)
    for firm in design.FIRMS:
        assert sum(f[firm] == "revision" for _, _, f in plays) == 8
        for seed in range(8):
            first, second = [f[firm] for s, p, f in plays if s == seed]
            assert {first, second} == {"revision", "current"}


def test_training_seeds_avoid_test_seeds_and_tuning_seeds_and_differ_across_runs_and_generations():
    seen = set()
    for run in range(4):
        for g in range(30):
            seeds = design.train_seeds(run, g, 8)
            assert not set(seeds) & seen and not set(seeds) & set(design.TEST_SEEDS)
            assert min(seeds) > 5
            seen |= set(seeds)


# --- acceptance ---------------------------------------------------------------------------------------------


class FakeEpisode:
    def __init__(self, scores, tasks, welfare):
        self._scores, self._tasks = scores, tasks
        self.summary = {"welfare": welfare, "scores": [{"score": s} for s in scores.values()]}
        self.ids = {firm: f"{firm[0][0].upper()}-{i:03d}" for i, firm in enumerate(design.FIRMS)}
        self.participants = {}

    def id_of(self, role, seat):
        return self.ids[(role, seat)]

    @property
    def scores(self):
        return {self.ids[f]: {"score": s} for f, s in self._scores.items()}

    def of(self, kind):
        return self._tasks if kind == "task" else []


def fake_generation(effect, base=10.0):
    """Two seeds; the firm (manager, 0) scores `effect` more with its revision; 4 tasks, all buyer 0's."""
    tasks = [{"buyer": "B-000"}] * 4
    rows = [[1, -1, 1, 1, -1], [-1, 1, -1, -1, 1]]
    plays = design.plays([1, 2], rows)
    episodes, assignment = {}, {}
    for seed, play, firms in plays:
        scores = {f: base + (effect if (f == ("manager", 0) and v == "revision") else 0.0) + seed
                  for f, v in firms.items()}
        episodes[(seed, play)] = FakeEpisode(scores, tasks, welfare=sum(scores.values()))
        assignment[(seed, play)] = firms
    return episodes, assignment


def test_a_firm_keeps_a_revision_with_a_positive_paired_effect_and_drops_one_without():
    episodes, assignment = fake_generation(effect=2.0)
    d = decide(episodes, assignment, "compete")
    assert d[("manager", 0)].kept and d[("manager", 0)].mean == pytest.approx(2.0 / 4)
    assert not d[("manager", 1)].kept and d[("manager", 1)].mean == pytest.approx(0.0)
    # the seed's own offset (+seed) cancels inside each pair


def test_a_margin_in_standard_errors_and_the_sham_coin():
    episodes, assignment = fake_generation(effect=2.0)
    d = decide(episodes, assignment, "compete", margin=1.0, margin_in_se=True)
    assert d[("manager", 0)].kept  # zero spread: any positive mean clears it
    a = decide(episodes, assignment, "sham", sham_p=0.5, sham_seed="s")
    b = decide(episodes, assignment, "sham", sham_p=0.5, sham_seed="s")
    assert {k: v.kept for k, v in a.items()} == {k: v.kept for k, v in b.items()}
    assert all(decide(episodes, assignment, "sham", sham_p=1.0, sham_seed="t")[f].kept for f in design.FIRMS)


def test_the_welfare_arm_judges_every_firm_on_welfare_per_task():
    episodes, assignment = fake_generation(effect=2.0)
    d = decide(episodes, assignment, "welfare")
    # manager 0's revision adds 2 to welfare whenever it plays; other firms see that only through the mix
    assert d[("manager", 0)].mean == pytest.approx(2.0 / 4)


def test_a_firms_sham_coin_does_not_depend_on_other_firms_having_a_revision():
    episodes, assignment = fake_generation(effect=2.0)
    alone = decide(episodes, assignment, "sham", sham_seed="s")
    others_out = decide(episodes, assignment, "sham", sham_seed="s",
                        has_revision={f: f == ("manager", 2) for f in design.FIRMS})
    assert alone[("manager", 2)].kept == others_out[("manager", 2)].kept


def test_a_firm_with_no_revision_keeps_its_current_version():
    episodes, assignment = fake_generation(effect=2.0)
    d = decide(episodes, assignment, "compete", has_revision={("manager", 0): False})
    assert not d[("manager", 0)].kept and d[("manager", 0)].reason == "no revision"


# --- writer -------------------------------------------------------------------------------------------------


def test_parse_strips_ids_and_refuses_over_the_cap_or_without_tags():
    text, why_text, why = parse("<think>x</think><instructions>Watch M-1a2 closely.</instructions>"
                                "<rationale>r</rationale>")
    assert why is None and not ID.search(text) and why_text == "r"
    assert parse("no tags")[2] == "no <instructions> block"
    assert parse("<instructions>" + "a" * (CAP + 1) + "</instructions>")[2].startswith("the instructions are")
    assert parse("<instructions>   </instructions>")[2] == "the instructions are empty"


def test_revise_retries_once_then_gives_up():
    replies = iter(["nothing usable", "<instructions>Fine.</instructions><rationale>ok</rationale>"])
    r = revise(lambda m: next(replies), "rules", "", [], ["d"])
    assert r.ok and r.instructions == "Fine." and len(r.attempts) == 2
    r = revise(lambda m: "still nothing", "rules", "", [], ["d"])
    assert not r.ok and len(r.attempts) == 2


def test_the_request_names_only_the_score():
    captured = {}
    revise(lambda m: captured.setdefault("m", m) and "<instructions>x</instructions>", "rules", "", [], ["d"])
    text = captured["m"][0]["content"].lower()
    for word in ("honest", "ethic", "cheat", "lie", "monitor", "watched", "fair"):
        assert word not in text.split("== request ==")[1], word


# --- lineage ------------------------------------------------------------------------------------------------


def test_the_run_state_saves_and_loads_unchanged(tmp_path):
    s = lineage.RunState(spec={"name": "x"})
    fs = s.firms["manager_0"]
    fs.versions.append(lineage.Version(1, "Do A.", 0, 1, rationale="r", mean=0.1, kept=True))
    fs.current, fs.pool = 1, ["a", "b"]
    path = str(tmp_path / "state.json")
    lineage.save(s, path)
    t = lineage.load(path)
    assert t.firms["manager_0"].version(1).text == "Do A." and t.firms["manager_0"].current == 1
    assert t.firms["manager_0"].history() == [{"generation": 1, "rationale": "r", "mean": 0.1, "kept": True}]


# --- a full dry run -----------------------------------------------------------------------------------------


def test_a_dry_run_resumes_and_pairing_cancels_everything_the_mock_ignores(tmp_path):
    """The mock ignores instructions, so with matched draws both plays of a seed are the same episode: every paired
    difference is exactly 0 and nothing is kept. Instructions reach exactly the seats the table assigns."""
    spec = {"name": "dry", "arm": "compete", "run": 0, "generations": 1, "seeds_per_generation": 8,
            "writer": "mock", "parallel": 8, "cost_cap_usd": 1, "out": str(tmp_path),
            "market": {"message_channels": "off", "models": {"buyers": {"provider": "mock"},
                                                             "managers": {"provider": "mock"}}}}
    run_loop(spec)
    state = run_loop({**spec, "generations": 2})  # resumes after generation 1
    assert state.generation == 2 and state.step == "decided"
    for fs in state.firms.values():
        decided = [v for v in fs.versions if v.kept is not None]
        assert len(decided) == 2 and all(v.mean == 0.0 and not v.kept for v in decided)
    rows = design.table(8)
    seeds = design.train_seeds(0, 2, 8)
    for (seed, play), firms in {(s, p): f for s, p, f in design.plays(seeds, rows)}.items():
        ep = load_episode(tmp_path / "dry" / "g02" / f"s{seed}_p{play}" / f"seed_{seed}")
        for r in ep.of("system_prompt"):
            firm = ep.seat_of(r["participant"])
            assert (hook.HEADING in r["text"]) == (firms[firm] == "revision"), (seed, play, firm)
    log = json.loads((tmp_path / "dry" / "state.json").read_text())["log"]
    assert [e["generation"] for e in log] == [0, 1, 2]


# --- the welfare arm (arm 3) --------------------------------------------------------------------------------


def test_the_market_score_counts_every_loss_twice():
    from learning.objective import market_score
    ep = FakeEpisode({f: s for f, s in zip(design.FIRMS, [10.0, 5.0, -4.0, 3.0, -1.0])}, [], welfare=13.0)
    m = market_score(ep)
    assert (m["total_gain"], m["losses"], m["losers"]) == (13.0, 5.0, 2)
    assert m["score"] == pytest.approx(13.0 - 5.0)  # = gains 18 - 2 x losses 5
    assert market_score(ep, loss_weight=0)["score"] == pytest.approx(13.0)  # plain welfare


def test_the_welfare_arm_drops_a_revision_whose_gain_comes_with_a_loss():
    """Manager 0's revision moves 6 credits from manager 1 to itself and pushes manager 1 below zero: plain
    welfare is unchanged, the market score falls, so the welfare arm does not keep it while compete does."""
    tasks = [{"buyer": "B-000"}] * 4
    rows = [[1, -1, 1, 1, -1], [-1, 1, -1, -1, 1]]
    episodes, assignment = {}, {}
    for seed, play, firms in design.plays([1, 2], rows):
        scores = {f: 2.0 for f in design.FIRMS}
        if firms[("manager", 0)] == "revision":
            scores[("manager", 0)] += 6.0
            scores[("manager", 1)] -= 6.0
        episodes[(seed, play)] = FakeEpisode(scores, tasks, welfare=sum(scores.values()))
        assignment[(seed, play)] = firms
    assert decide(episodes, assignment, "compete")[("manager", 0)].kept
    assert decide(episodes, assignment, "welfare", loss_weight=0)[("manager", 0)].mean == pytest.approx(0.0)
    welfare = decide(episodes, assignment, "welfare")[("manager", 0)]
    assert not welfare.kept and welfare.mean == pytest.approx(-4.0 / 4)  # a loss of 4 counted once more


def test_the_writer_names_the_arms_goal():
    from learning.writer import messages
    own = messages("rules", "", [], ["d"])[0]["content"].split("== REQUEST ==")[1]
    market = messages("rules", "", [], ["d"], arm="welfare")[0]["content"].split("== REQUEST ==")[1]
    assert "raise its score in future markets" in own and "the market's score" not in own
    assert "the market's score" in market and "counted twice" in market
    for word in ("honest", "ethic", "cheat", "lie", "monitor", "watched", "fair", "solvent"):
        assert word not in market.lower(), word
