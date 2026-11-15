"""One participant's turn as a model conversation (spec/turns.md)."""

from .fmt import INT, unit

ACT_FORCED = {"type": "function", "function": {"name": "act"}}


def functions(cfg):
    """The compute and act definitions, in the order [compute, act] (turns.md, 3.1 and 3.2)."""
    c, t = cfg["compute"], cfg["turns"]
    n = INT(t["max_actions"])
    compute = {
        "type": "function",
        "function": {
            "name": "compute",
            "description": "Run a short Python program (standard library only, no access to the market or the "
            "network) and return what it prints, within this turn. One successful run per turn; a failed run "
            "gets one more attempt, once per turn. A compute call sent in the same reply as act doesn't run. "
            'Example: {"code": "print(2 + 2)"}',
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": f"the program, up to {unit(INT(c['code_chars']), 'character')}; it is "
                        f"stopped after {unit(INT(c['cpu_s']), 'second')} of computing or "
                        f"{unit(INT(c['wall_s']), 'second')} in all, and the first "
                        f"{unit(INT(c['output_chars']), 'character')} of its output come back",
                    }
                },
                "required": ["code"],
            },
        },
    }
    act = {
        "type": "function",
        "function": {
            "name": "act",
            "description": f"End your turn: send up to {unit(n, 'action')}, which run after every participant "
            'has ended their turn, and your private memory. Example: {"actions": [{"action": "<action name>", '
            '"<argument name>": <value>}, {"action": "<action name>"}], "memory": "<your memory>"}',
            "parameters": {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": f"the actions, in order: a list of up to {n} (any beyond the first {n} "
                        'are dropped), each an object {"action": "<action name>", "<argument name>": <value>}. '
                        "The list may be empty, [], when you take no action this turn.",
                    },
                    "memory": {
                        "type": "string",
                        "description": "your private memory, up to "
                        f"{unit(INT(t['memory_chars']), 'character')} (longer is cut), shown in your view at "
                        "your next turn; it replaces your previous memory, and an empty memory leaves it "
                        "unchanged",
                    },
                },
                "required": ["memory"],
            },
        },
    }
    return [compute, act]


def tool_choice(programs, calls, free):
    """The tool_choice before a call of the decide chain (turns.md, 4.1)."""
    return ACT_FORCED if programs - free >= 1 or calls - free >= 2 else "required"
