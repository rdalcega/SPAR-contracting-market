"""The learning loop of one run: a starting batch, then generations of write, play and decide.

    python -m learning.loop SPEC.yaml

A run's spec (YAML) holds:
  name                 the run's folder under `out`
  arm                  compete | sham | welfare (main arms, messages on) | compete_no_messages (ablation; its
                       market must have message_channels off)
  run                  the run's index within its arm (sets its training seeds; arms share seeds)
  generations          how many generations after the starting batch
  seeds_per_generation 8 (the balanced table); another number gives a random balanced table
  margin, margin_in_se keep a revision when its mean difference > margin (x its standard error if margin_in_se)
  sham_p               the sham arm's probability of keeping a revision
  loss_weight          welfare arm: the extra weight on participants' losses in the market's score (default 1:
                       every credit lost counts twice; objective.py)
  digests              digests per reflection (default 4)
  market               the market's config as a mapping (models, message_channels, ...), as in the market's YAML
  writer               "mock" (free, for dry runs) or a model spec as in the market's config
  parallel             episodes at the same time
  cost_cap_usd         dollar cap on all calls of this invocation (agents' and the writer's); a resume starts from $0
  out                  root folder

The run resumes from its state file after a stop at any step.
"""

import os
import random
import sys

import yaml

from . import design, lineage
from .accept import decide
from .digest import DigestConfig, build_digest
from .episodes import episode_folder, run_plays, tracker as make_tracker
from .objective import ARMS, LOSS_WEIGHT
from .lineage import Version, firm_key
from .logs import load_episode
from .writer import CAP, mock_writer, model_writer, revise, rules_text

DEFAULTS = {"seeds_per_generation": 8, "margin": 0.0, "margin_in_se": False, "sham_p": 0.5, "digests": 4,
            "loss_weight": LOSS_WEIGHT, "parallel": 8, "cost_cap_usd": 1.0, "writer": "mock", "generations": 1,
            "run": 0}


def _gen_dir(spec, g):
    return os.path.join(spec["out"], spec["name"], f"g{g:02d}")


def _assignment(spec, g, seeds):
    rows = design.table(len(seeds), random.Random(f"{spec['name']}|table|{g}"))
    return {(seed, play): firms for seed, play, firms in design.plays(seeds, rows)}


def _say(*parts):
    print(*parts, flush=True)


def run_loop(spec, writer_factory=None):
    """Run (or resume) one run. writer_factory(firm, generation, tracker) -> complete(messages) -> text."""
    spec = {**DEFAULTS, **spec}
    if spec["arm"] not in ARMS:
        raise ValueError(f"unknown arm {spec['arm']!r}; one of {', '.join(ARMS)}")
    channels = str(spec["market"].get("message_channels", "off"))
    if spec["arm"] == "compete_no_messages" and channels not in ("off", "False"):
        raise ValueError(f"arm compete_no_messages needs message_channels off, not {channels!r}")
    root = os.path.join(spec["out"], spec["name"])
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "state.json")
    state = lineage.load(path) if os.path.exists(path) else lineage.RunState(spec=spec)
    tracker = make_tracker(spec["cost_cap_usd"])
    n = spec["seeds_per_generation"]

    if writer_factory is None:
        if spec["writer"] == "mock":
            def writer_factory(firm, g, _tracker):
                return mock_writer(firm, g)
        else:
            def writer_factory(firm, g, trk):
                market_llm = {**{"temperature": 0.8, "max_tokens": 64000, "timeout_s": 1200, "max_retries": 5,
                                 "network_patience_s": 900}, **(spec["market"].get("llm") or {})}
                return model_writer(spec["writer"], market_llm, trk)

    # the starting batch: every firm on its empty starting version
    if state.step == "start":
        seeds = design.train_seeds(spec["run"], 0, n)
        out = _gen_dir(spec, 0)
        run_plays(spec["market"], [(s, 0, {}) for s in seeds], out, spec["parallel"], tracker)
        for firm in design.FIRMS:
            state.firms[firm_key(firm)].pool = [episode_folder(out, s, 0) for s in seeds]
        state.step = "decided"
        state.log.append({"generation": 0, "step": "start", "seeds": seeds})
        lineage.save(state, path)
        _say(f"{spec['name']}: starting batch done")

    while state.generation < spec["generations"]:
        g = state.generation + 1
        if state.step == "decided":
            _write(spec, state, g, writer_factory, tracker)
            state.step = "written"
            lineage.save(state, path)
            _say(f"{spec['name']}: generation {g} written")
        seeds = design.train_seeds(spec["run"], g, n)
        assignment = _assignment(spec, g, seeds)
        out = _gen_dir(spec, g)
        if state.step == "written":
            jobs = []
            for (seed, play), firms in assignment.items():
                jobs.append((seed, play, {firm: _text_for(state, firm, version)
                                          for firm, version in firms.items()}))
            run_plays(spec["market"], jobs, out, spec["parallel"], tracker)
            state.step = "played"
            lineage.save(state, path)
            _say(f"{spec['name']}: generation {g} played")
        if state.step == "played":
            episodes = {(s, p): load_episode(episode_folder(out, s, p)) for s, p in assignment}
            has = {firm: state.firms[firm_key(firm)].candidate is not None for firm in design.FIRMS}
            decisions = decide(episodes, assignment, spec["arm"], spec["margin"], spec["margin_in_se"],
                               spec["sham_p"], sham_seed=f"{spec['name']}|{g}", has_revision=has,
                               loss_weight=spec["loss_weight"])
            entry = {"generation": g, "step": "decided", "seeds": seeds, "decisions": {}}
            for firm, d in decisions.items():
                fs = state.firms[firm_key(firm)]
                if fs.candidate is not None:
                    v = fs.version(fs.candidate)
                    v.mean, v.se, v.diffs, v.kept, v.reason = d.mean, d.se, d.diffs, d.kept, d.reason
                    if d.kept:
                        fs.current = fs.candidate
                used = "revision" if (d.kept and fs.candidate is not None) else "current"
                fs.pool = [episode_folder(out, s, p) for (s, p), firms in assignment.items() if firms[firm] == used]
                fs.candidate = None
                entry["decisions"][firm_key(firm)] = {"kept": d.kept, "mean": d.mean, "se": d.se, "reason": d.reason}
            entry["dollars_so_far_this_invocation"] = tracker.total
            state.log.append(entry)
            state.generation, state.step = g, "decided"
            lineage.save(state, path)
            _say(f"{spec['name']}: generation {g} decided: "
                 + ", ".join(f"{k} {'kept' if v['kept'] else 'not kept'}" for k, v in entry["decisions"].items()))
    return state


def _text_for(state, firm, version):
    fs = state.firms[firm_key(firm)]
    vid = fs.candidate if (version == "revision" and fs.candidate is not None) else fs.current
    return fs.version(vid).text


def _write(spec, state, g, writer_factory, tracker):
    """One revision per firm, from digests of episodes it played with its current version."""
    for firm in design.FIRMS:
        fs = state.firms[firm_key(firm)]
        pick = random.Random(f"{spec['name']}|digests|{g}|{firm_key(firm)}")
        chosen = sorted(pick.sample(fs.pool, min(spec["digests"], len(fs.pool))))
        eps = [load_episode(folder) for folder in chosen]
        dcfg = DigestConfig(arm=spec["arm"], loss_weight=spec["loss_weight"])
        digests = [build_digest(ep, ep.id_of(*firm), dcfg) for ep in eps]
        rules = rules_text(eps[0], eps[0].id_of(*firm))
        current = fs.version(fs.current).text
        result = revise(writer_factory(firm, g, tracker), rules, current, fs.history(), digests, cap=CAP,
                        arm=spec["arm"], loss_weight=spec["loss_weight"])
        _save_writer_record(spec, g, firm, chosen, result)
        if result.ok and result.instructions != current:
            v = Version(id=len(fs.versions), text=result.instructions, parent=fs.current, generation=g,
                        rationale=result.rationale)
            fs.versions.append(v)
            fs.candidate = v.id
        else:
            fs.candidate = None


def _save_writer_record(spec, g, firm, chosen, result):
    """The writer's full requests and replies, for audit."""
    folder = os.path.join(spec["out"], spec["name"], f"g{g:02d}", "writer")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{firm_key(firm)}.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump({"digests_from": chosen, "ok": result.ok, "instructions": result.instructions,
                        "rationale": result.rationale, "attempts": result.attempts}, f, allow_unicode=True,
                       sort_keys=False, width=120)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m learning.loop SPEC.yaml", file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    run_loop(spec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
