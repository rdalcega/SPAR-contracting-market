"""Acceptance check 2: the function definitions and each step's tool_choice (spec/turns.md, 3 and 4.1).

act departs from turns.md 3.2 on purpose (SPEC_QUESTIONS.md, Q117): its items name the role's actions in an enum,
and its description gives an example with real actions and says that the memory is not an action. Everything
else, compute included, is checked against the spec's examples as they are.
"""

import copy
import json
from pathlib import Path

import pytest

from departures import with_act
from market import actions, config
from market.turns import ACT_FORCED, EXAMPLE_ACTIONS, action_names, functions, tool_choice

EXAMPLES = Path(__file__).parent.parent / "spec" / "examples"
CFG = {
    "compute": {"cpu_s": 5, "wall_s": 10, "memory_mb": 512, "output_kb": 64, "output_chars": 4000,
                "code_chars": 20000},
    "turns": {"max_actions": 6, "memory_chars": 2000},
    "message_channels": "off",
}
MEMORY_RULE = 'Your memory is not an action: write it in "memory", never as an item of "actions".'


def example(name):
    return (EXAMPLES / f"tools_{name}.json").read_text(encoding="utf-8")


def as_file(choice, tools):
    return json.dumps({"tool_choice": choice, "tools": tools}, indent=1, ensure_ascii=False)


@pytest.mark.parametrize("role", ["buyer", "manager"])
def test_first_call_offers_compute_and_act_with_required(role):
    assert tool_choice(programs=0, calls=0, free=0) == "required"
    assert as_file("required", functions(CFG, role)) == with_act(example("first_call_compute_act"), CFG, role)


@pytest.mark.parametrize("role", ["buyer", "manager"])
def test_act_forced_after_a_program_still_offers_both(role):
    choice = tool_choice(programs=1, calls=1, free=0)
    assert choice == ACT_FORCED
    assert as_file(choice, functions(CFG, role)) == with_act(example("act_forced_compute_act"), CFG, role)


@pytest.mark.parametrize("role", ["buyer", "manager"])
def test_correction_offers_act_only_forced(role):
    assert as_file(ACT_FORCED, functions(CFG, role)[1:]) == with_act(example("act_forced_act"), CFG, role)


@pytest.mark.parametrize("role", ["buyer", "manager"])
def test_compute_is_the_spec_s_own(role):
    spec_compute = json.loads(example("first_call_compute_act"))["tools"][0]
    assert functions(CFG, role)[0] == spec_compute


@pytest.mark.parametrize("name", ["first_call_compute_act", "act_forced_compute_act", "act_forced_act"])
def test_the_examples_survive_a_round_trip_byte_for_byte(name):
    """So the comparisons through with_act, which reads and writes an example, still compare its exact text."""
    assert json.dumps(json.loads(example(name)), indent=1, ensure_ascii=False) == example(name).rstrip("\n")


@pytest.mark.parametrize("role", ["buyer", "manager"])
def test_act_departs_from_the_spec_only_in_its_example_and_items(role):
    spec_act = json.loads(example("first_call_compute_act"))["tools"][1]
    act = copy.deepcopy(functions(CFG, role)[1])
    spec_text, text = spec_act["function"].pop("description"), act["function"].pop("description")
    assert text.split("Example: ")[0] == spec_text.split("Example: ")[0]
    for definition in (spec_act, act):
        del definition["function"]["parameters"]["properties"]["actions"]["items"]
    assert act == spec_act


@pytest.mark.parametrize("mode", config.MODES)
@pytest.mark.parametrize("role", ["buyer", "manager"])
def test_act_s_items_name_exactly_the_role_s_actions(mode, role):
    cfg = CFG | {"message_channels": mode}
    items = functions(cfg, role)[1]["function"]["parameters"]["properties"]["actions"]["items"]
    assert items == {"type": "object", "properties": {"action": {"type": "string", "enum": action_names(role, mode)}},
                     "required": ["action"], "additionalProperties": True}
    names = action_names(role, mode)
    assert names == list(actions.own(role, mode))  # the order a refusal lists them
    assert ("send_message" in names) == actions.can_send(role, mode)
    assert "memory" not in names and "compute" not in names


EXAMPLES_SHOWN = {
    "buyer": 'Example: {"actions": [{"action": "post_task", "task_id": "<task id>"}, {"action": "test_delivery", '
             '"contract_id": "<contract id>"}], "memory": "<your memory>"}. ' + MEMORY_RULE,
    "manager": 'Example: {"actions": [{"action": "bid", "post_id": "<post id>", "price": <price>}, {"action": '
               '"test_result", "contract_id": "<contract id>", "result": "<result label>"}], "memory": "<your '
               'memory>"}. ' + MEMORY_RULE,
}


@pytest.mark.parametrize("mode", config.MODES)
@pytest.mark.parametrize("role", ["buyer", "manager"])
def test_act_s_example_uses_real_actions_with_placeholders_and_keeps_the_memory_out(mode, role):
    description = functions(CFG | {"message_channels": mode}, role)[1]["function"]["description"]
    assert description.endswith(EXAMPLES_SHOWN[role])
    shown = [part.split('"')[0] for part in EXAMPLE_ACTIONS[role].split('"action": "')[1:]]
    assert shown and set(shown) <= set(action_names(role, mode))  # real actions in every mode
    assert not any(word in description for word in ("escrow", "threshold", "plain"))  # no value is suggested


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
    compute, act = (f["function"] for f in functions(cfg, "buyer"))
    assert compute["parameters"]["properties"]["code"]["description"] == (
        "the program, up to 1 character; it is stopped after 1 second of computing or 1 second in all, and the "
        "first 1 character of its output come back")
    assert act["description"].startswith("End your turn: send up to 1 action, which run after")
    props = act["parameters"]["properties"]
    assert props["actions"]["description"].startswith(
        "the actions, in order: a list of up to 1 (any beyond the first 1 are dropped), each an object")
    assert props["memory"]["description"].startswith("your private memory, up to 1 character (longer is cut)")
