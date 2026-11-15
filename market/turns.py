"""One participant's turn as a model conversation (spec/turns.md)."""

import re
from dataclasses import dataclass

from . import models
from .fmt import INT, TEXT, unit
from .reading import call_arguments, call_name, read_json

ACT_FORCED = {"type": "function", "function": {"name": "act"}}
NOT_COMPUTE = 'not run: compute takes one argument, "code", a string; send your actions with act'
NO_PROGRAMS_LEFT = (
    "not run: no programs left this turn, and compute cannot be called again this turn; send your actions with act"
)
LAST_PROGRAM = (
    "(That was your last program this turn: compute cannot be called again this turn. Send your actions with act.)"
)
FREE_RETRY = "(The program failed, so it does not count: 1 more program allowed this turn.)"
ACT_ENDED = "not run: act ended the turn"
IN_LAST_ACT = "(the results of this reply are in the answer to its last act call)"
EMPTY_REPLY = "(an empty reply)"
RESULTS_HEADING = "Results of your actions:"
NO_ACTIONS = "(no actions run)"
CORRECTION_HEADING = "Correction:"
correction_failed = "(note: your correction could not be sent; the results above stand)"


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


def strip_think(text):
    """A reply's text with its think blocks removed (2, strip-think)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


def assistant(reply):
    """The assistant message that puts a reply back into the conversation (2)."""
    message = {"role": "assistant", "content": strip_think(reply.text), "tool_calls": reply.tool_calls}
    if reply.reasoning:
        message["reasoning"] = reply.reasoning
    return message


def _tool(call, content):
    """The tool message that answers a call, by its id as returned (2)."""
    return {"role": "tool", "tool_call_id": call.get("id"), "content": content}


def _strict(arguments):
    """The object a call's arguments read as strictly, or None (5.2, 11.4)."""
    if isinstance(arguments, str):
        try:
            arguments = read_json(arguments)
        except ValueError:
            return None
    return arguments if isinstance(arguments, dict) else None


@dataclass
class Chain:
    """A turn's decide chain, or its correction call, as the round takes it up (rounds.md, 4 and 8.4).

    `messages`: the conversation before the reply that ended it. `steps`: one per reply, in order, each
    {"request", "reply", "charge", "programs"}. `reply`: the reply that ended it. `empty`: that reply is read as
    an act call with no actions and no memory (13.2). `error`, `request`, `failed_cost`: a failed call's error
    text, request and reported dollars; the turn is then lost to the provider (rounds.md, 10.1).
    """

    messages: list
    steps: list
    reply: object = None
    empty: bool = False
    error: str = None
    request: dict = None
    failed_cost: float = 0.0


def _call(chain, backend, cfg, offered, choice):
    """One model call on the chain's conversation: its step with the reply's charge (13.1), None if it failed."""
    request = {"messages": list(chain.messages), "functions": offered, "tool_choice": choice}
    try:
        reply = backend.call(request["messages"], offered, choice)
    except models.CallFailed as failed:
        chain.error, chain.request, chain.failed_cost = failed.error, request, failed.failed_cost
        return None
    charge = models.charge(reply.cost, cfg["credits_per_usd"])
    chain.steps.append({"request": request, "reply": reply, "charge": charge, "programs": []})
    return chain.steps[-1]


def decide(system, view, backend, cfg, balance, manager, run_program):
    """The decide chain of a turn (4, 5), out of business decided on `balance` less its own charges (13.2)."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": view}]
    chain = Chain(messages=messages, steps=[])
    offered = functions(cfg)
    programs = calls = free = 0
    while True:
        step = _call(chain, backend, cfg, offered, tool_choice(programs, calls, free))
        if step is None:
            return chain
        calls += 1
        reply = step["reply"]
        names = [call_name(call) for call in reply.tool_calls or []]
        out = False
        if step["charge"] > 0:
            balance -= step["charge"]
            out = manager and balance <= 0
        chain.empty = out and bool(names) and "act" not in names
        if not names or "act" in names or out or calls - free > 2:
            chain.reply = reply
            return chain
        messages.append(assistant(reply))
        left = 1 - (programs - free)
        for call in reply.tool_calls:
            code = (_strict(call_arguments(call)) or {}).get("code") if call_name(call) == "compute" else None
            if not isinstance(code, str):
                answer = NOT_COMPUTE
            elif left <= 0:
                answer = NO_PROGRAMS_LEFT
            else:
                status, exit_code, wall, output = run_program(code, cfg["compute"])
                step["programs"].append(
                    {"code": code, "status": status, "exit_code": exit_code, "wall": wall, "output": output}
                )
                programs += 1
                left -= 1
                answer = output + "\n\n" + LAST_PROGRAM
                if status != "ok" and not free:
                    free = 1
                    left += 1
                    answer = output + "\n\n" + FREE_RETRY
            messages.append(_tool(call, answer))


def correction_note(limit, unreadable):
    """The note that opens a correction's body (11.2)."""
    actions = unit(INT(limit), "action")
    if unreadable:
        return (
            "Your reply could not be read, so none of its actions ran (below). You may call act again now, with "
            f"up to {actions} and your memory. This is your only correction this turn, and compute cannot be "
            "called in it."
        )
    return (
        f"{INT(limit)} of your actions failed (the results of your reply are below; the actions that succeeded "
        f"have run and stand). You may send up to {actions} now to replace {'it' if limit == 1 else 'them'}, in "
        "the same format as before, with your memory, which then replaces the one you just sent. This is your "
        "only correction this turn, and compute cannot be called in it."
    )


def correction_body(note, results_text, news, facts):
    """A correction's body: the note, the spaced result lines, then the news and the fact lines (11.3)."""
    body = note + "\n\nResults of your reply:\n" + results_text
    if news:
        body += "\n\nWhat reached you from this round so far (your next view shows it too):\n" + "\n".join(news)
    if facts:
        body += "\n\n" + "\n".join(facts)
    return body


def correction_messages(chain, reading, body):
    """The correction request's messages: the chain's conversation, its last reply and the body (11.4)."""
    reply = chain.reply
    calls = reply.tool_calls or []
    if reading["error"] is None and reply.finish_reason != "length" and all(
        isinstance(call_arguments(call), str) and _strict(call_arguments(call)) is not None for call in calls
    ):
        names = [call_name(call) for call in calls]
        last_act = len(names) - 1 - names[::-1].index("act")
        answers = [
            body if i == last_act else ACT_ENDED if name == "compute" else IN_LAST_ACT
            for i, name in enumerate(names)
        ]
        return chain.messages + [assistant(reply)] + [_tool(call, answer) for call, answer in zip(calls, answers)]
    shown = [arguments for arguments in map(call_arguments, calls) if arguments and str(arguments).strip()]
    content = strip_think(reply.text) or "\n".join(TEXT(arguments)[:2000] for arguments in shown) or EMPTY_REPLY
    message = {"role": "assistant", "content": content}
    if reply.reasoning:
        message["reasoning"] = reply.reasoning
    return chain.messages + [message, {"role": "user", "content": body}]


def correct(messages, backend, cfg):
    """The correction call: act only, act forced, no call after it (4.3, 11.5)."""
    chain = Chain(messages=messages, steps=[])
    step = _call(chain, backend, cfg, functions(cfg)[1:], ACT_FORCED)
    if step:
        chain.reply = step["reply"]
    return chain


def format_line(err):
    """The result line of an unreadable decide reply (11.3, 12)."""
    return f"- FORMAT ERROR: {err}: none of your actions ran"


def correction_format_line(err2):
    """The line an unreadable correction adds to the decide reply's results (11.6)."""
    return f"Correction: FORMAT ERROR: {err2}: none of its actions ran"


def lost_text(err, err2=None):
    """The stored results text of a turn lost as a format error; `err2` is the correction's error (12)."""
    both = "" if err2 is None else f"; and your correction: {err2}"
    return (
        f"FORMAT ERROR: {err}{both}. That turn was lost: none of its actions ran. End every turn by calling the "
        "function act with a list of your actions (an empty list does nothing) and your memory."
    )


def results(first, correction, memory_note):
    """The entries of a turn's results text (12); `correction` is None unless a correction was read."""
    lines = list(first)
    if correction is not None:
        lines += [CORRECTION_HEADING] + (list(correction) or [NO_ACTIONS])
    if memory_note:
        lines.append(memory_note)
    return [RESULTS_HEADING] + (lines or [NO_ACTIONS])
