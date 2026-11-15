"""Reading the reply that ends a chain, and the turn-end memory step (spec/turns.md, 7, 8, 10 and 11.6)."""

import json

from .fmt import INT, unit

NOT_A_CALL = "your reply was not a function call: every reply must call compute or act"
NO_ACT = "your reply did not call act: your turn ends with act, with your actions and your memory"
UNREADABLE = (
    'your act call\'s "actions" could not be read: it must be a list of action objects, each naming its action '
    'with "action" and giving its arguments beside it'
)
CUT = " - your reply hit the length limit before its act call was complete; reason more briefly"
COMPUTE_BESIDE_ACT = "(note: your compute call did not run: it was sent in the same reply as act)"
NO_MEMORY = "(note: your reply had no memory; your memory is unchanged)"
MAX_NESTING = 400


def nesting(value):
    """How many levels of lists and objects a decoded value is nested (0 for a number, 1 for `[]`)."""
    levels, layer = 0, [value]
    while layer := [v for v in layer if isinstance(v, (dict, list))]:
        levels += 1
        layer = [x for v in layer for x in (v.values() if isinstance(v, dict) else v)]
    return levels


def read_json(text):
    """Strict reading (7.2): the text's one JSON value, or ValueError.

    An integer literal of more than 4,300 digits and nesting deeper than 400 levels do not decode (the
    preferred answer of 7.2 to inputs outside the spec).
    """
    try:
        value = json.loads(text)
    except RecursionError:
        raise ValueError("nested too deep") from None
    if nesting(value) > MAX_NESTING:
        raise ValueError("nested too deep")
    return value


def has_text(value):
    """A string with non-whitespace content (7.3 step 2, 7.4 I2)."""
    return isinstance(value, str) and value.strip() != ""


def call_name(call):
    """A function call's name as the provider returned it; None when it has none (models.md, 4.7)."""
    return (call.get("function") or {}).get("name")


def call_arguments(call):
    """A function call's arguments as the provider returned them; None when it has none (models.md, 4.7)."""
    return (call.get("function") or {}).get("arguments")


def blank():
    """A reading with no error, no notes, no actions and no memory: where every reading starts (7), and what an
    empty reply reads as (13.2)."""
    return {"error": None, "notes": [], "actions": [], "dropped": 0, "memory": None}


def act_arguments(call, cut):
    """An act call's arguments object (7.3 step 1), or None when it cannot be read."""
    raw = call_arguments(call)
    if raw is None or raw == "":
        return {}
    if not isinstance(raw, str):
        return raw if isinstance(raw, dict) and nesting(raw) <= MAX_NESTING else None
    texts = [raw] if cut else [raw] + [raw.strip() + closer for closer in ("}", "]}", "}]}", "]")]
    for text in texts:
        try:
            value = read_json(text)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def read_items(items):
    """The actions of a list of act items, each a (name or None, arguments) pair (7.4, I1 to I5)."""
    actions = []
    for item in items:
        if not isinstance(item, dict):
            actions.append((None, {}))
            continue
        args, name = dict(item), None
        for key in ("action", "name"):
            if has_text(args.get(key)):
                name = args.pop(key)
                break
        else:
            for key in ("action", "name"):
                if key in args and (args[key] is None or isinstance(args[key], str)):
                    del args[key]
        for key in ("args", "arguments", "parameters", "params"):
            if isinstance(args.get(key), dict):
                for k, v in args.pop(key).items():
                    args.setdefault(k, v)
        if name is None and not args:
            continue
        if name == "act" and isinstance(args.get("actions"), list):
            actions += read_items(args["actions"])
        else:
            actions.append((name, args))
    return actions


def read_reply(calls, cut, limit):
    """The reply that ends a decide chain or answers a correction, read into actions and a memory (7, 8).

    `calls` are the reply's function calls as the provider returned them (none: an empty list or None), `cut`
    whether its finish reason is `length`, `limit` turns.max_actions or the correction's limit. A reply that
    cannot be read gives only `error` (7.1, with the suffix of a cut reply).
    """
    reading = blank()
    calls = calls or []
    names = [call_name(call) for call in calls]
    if "act" not in names:
        reading["error"] = (NO_ACT if calls else NOT_A_CALL) + (CUT if cut else "")
        return reading
    items, memory = [], None
    for call, name in zip(calls, names):
        if name != "act":
            continue
        args = act_arguments(call, cut)
        if args is not None and args.get("actions") is None:
            args = args | {"actions": []}
        if args is None or not isinstance(args["actions"], list):
            reading["error"] = UNREADABLE + (CUT if cut else "")
            return reading
        items += args["actions"]
        memory = args["memory"] if has_text(args.get("memory")) else None
    actions = read_items(items)
    reading["actions"] = actions[:limit]
    reading["dropped"] = len(actions) - len(reading["actions"])
    reading["memory"] = memory
    if "compute" in names:
        reading["notes"].append(COMPUTE_BESIDE_ACT)
    if reading["dropped"]:
        reading["notes"].append(
            f"(note: only your first {unit(INT(limit), 'action')} {'was' if limit == 1 else 'were'} run)"
        )
    return reading


def turn_memory(before, decide, correction, memory_chars):
    """The memory step at a turn's end (10.2 to 10.4).

    `before` is the participant's memory, `decide` the decide reply's memory, `correction` the memory of a
    correction that was read (None when it had none or no correction was read).
    """
    text = decide if correction is None else correction
    if text is None:
        return {"written": False, "text": before, "from_correction": False, "note": NO_MEMORY}
    note = None
    if len(text) > memory_chars:
        note = f"(note: your memory was cut to its first {INT(memory_chars)} of {INT(len(text))} characters)"
    return {"written": True, "text": text[:memory_chars], "from_correction": correction is not None, "note": note}
