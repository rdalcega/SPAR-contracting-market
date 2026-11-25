"""Firms' instructions in the agents' system prompts, without changing the market's code.

The market builds every system prompt with `market.prompts.system_prompt(cfg, role, agent_id, share, seat)` and logs
it once per participant. `install()` wraps that function: when instructions are registered for the episode (found by
its run name, `cfg["name"]`) and the seat, the wrapper adds them after the prompt under HEADING. With nothing
registered, the prompt is the market's own, byte for byte.

Each episode the loop runs has a run name of its own, so episodes running at the same time never share an entry.
This stands in for a per-seat instruction parameter in the market's config, which would need a spec amendment.
If one is added, only `install` changes.
"""

import threading

from market import prompts

HEADING = "YOUR FIRM'S INSTRUCTIONS"
SEPARATOR = "\n\n"

_lock = threading.Lock()
_registry = {}  # (episode run name, role, seat) -> instruction text
_original = None


def install():
    """Wrap the market's system prompt builder once (idempotent)."""
    global _original
    with _lock:
        if _original is not None:
            return
        _original = prompts.system_prompt

        def system_prompt(cfg, role, agent_id, share, seat):
            text = _original(cfg, role, agent_id, share, seat)
            extra = _registry.get((cfg.get("name"), role, seat))
            return section(text, extra)

        prompts.system_prompt = system_prompt


def uninstall():
    global _original
    with _lock:
        if _original is not None:
            prompts.system_prompt = _original
            _original = None


def section(prompt, instructions):
    """The prompt with the instructions after it, or the prompt unchanged when there are none."""
    if not instructions:
        return prompt
    return f"{prompt}{SEPARATOR}{HEADING}\n{instructions}"


def strip_section(prompt):
    """The market's own prompt, without an instruction section (for the writer's copy of the rules)."""
    marker = f"{SEPARATOR}{HEADING}\n"
    i = prompt.find(marker)
    return prompt if i < 0 else prompt[:i]


def register(run_name, instructions):
    """instructions: {(role, seat): text}. Empty texts are not registered."""
    with _lock:
        for (role, seat), text in instructions.items():
            if text:
                _registry[(run_name, role, seat)] = text


def unregister(run_name):
    with _lock:
        for key in [k for k in _registry if k[0] == run_name]:
            del _registry[key]
