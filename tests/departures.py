"""The spec's examples as this implementation's departures from the spec change them (SPEC_QUESTIONS.md, Q117)."""

import json

from market import turns


def with_act(example_text, cfg, role):
    """A tools example (`{"tool_choice": ..., "tools": [...]}`, as examples/tools_*.json write it) with its act
    definition replaced by the role's act of turns.functions; everything else as the spec writes it."""
    data = json.loads(example_text)
    act = turns.functions(cfg, role)[1]
    data["tools"] = [act if tool["function"]["name"] == "act" else tool for tool in data["tools"]]
    return json.dumps(data, indent=1, ensure_ascii=False)
