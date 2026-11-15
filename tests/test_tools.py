"""Acceptance check 2: the function definitions and each step's tool_choice (spec/turns.md, 3 and 4.1)."""

import copy
import json
from pathlib import Path

from market.turns import ACT_FORCED, functions, tool_choice

EXAMPLES = Path(__file__).parent.parent / "spec" / "examples"
CFG = {
    "compute": {"cpu_s": 5, "wall_s": 10, "memory_mb": 512, "output_kb": 64, "output_chars": 4000,
                "code_chars": 20000},
    "turns": {"max_actions": 6, "memory_chars": 2000},
}


def example(name):
    return (EXAMPLES / f"tools_{name}.json").read_text(encoding="utf-8")


def as_file(choice, tools):
    return json.dumps({"tool_choice": choice, "tools": tools}, indent=1, ensure_ascii=False)


def test_first_call_offers_compute_and_act_with_required():
    assert tool_choice(programs=0, calls=0, free=0) == "required"
    assert as_file("required", functions(CFG)) == example("first_call_compute_act").rstrip("\n")


def test_act_forced_after_a_program_still_offers_both():
    choice = tool_choice(programs=1, calls=1, free=0)
    assert choice == ACT_FORCED
    assert as_file(choice, functions(CFG)) == example("act_forced_compute_act").rstrip("\n")


def test_correction_offers_act_only_forced():
    assert as_file(ACT_FORCED, functions(CFG)[1:]) == example("act_forced_act").rstrip("\n")


def test_tool_choice_sequences():
    # turns.md 4.2, "Typical sequences": (programs, calls, free) before each request
    assert tool_choice(0, 0, 0) == "required"
    assert tool_choice(1, 1, 0) == ACT_FORCED  # compute ran
    assert tool_choice(1, 1, 1) == "required"  # compute failed: free retry granted
    assert tool_choice(2, 2, 1) == ACT_FORCED  # the retry ran, any status
    assert tool_choice(0, 1, 0) == "required"  # malformed compute, no program ran
    assert tool_choice(0, 2, 0) == ACT_FORCED  # malformed again


def test_singular_rule_in_descriptions():
    cfg = copy.deepcopy(CFG)
    cfg["compute"].update(cpu_s=1, wall_s=1, output_chars=1, code_chars=1)
    cfg["turns"].update(max_actions=1, memory_chars=1)
    compute, act = (f["function"] for f in functions(cfg))
    assert compute["parameters"]["properties"]["code"]["description"] == (
        "the program, up to 1 character; it is stopped after 1 second of computing or 1 second in all, and the "
        "first 1 character of its output come back")
    assert act["description"].startswith("End your turn: send up to 1 action, which run after")
    props = act["parameters"]["properties"]
    assert props["actions"]["description"].startswith(
        "the actions, in order: a list of up to 1 (any beyond the first 1 are dropped), each an object")
    assert props["memory"]["description"].startswith("your private memory, up to 1 character (longer is cut)")
