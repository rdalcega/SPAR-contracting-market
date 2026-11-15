"""One round (spec/rounds.md): who takes a turn, the views, the two waves of model calls taken up in the turn
order, the reading of each act, the two action phases, the round's credits and the end of each turn.

Nothing of a round is kept outside the market: its own data are locals of `play`.
"""

import copy
from dataclasses import asdict, dataclass

from . import actions, config, phase, prompts, state, turns, view, view_contracts
from .reading import blank, read_reply, turn_memory


@dataclass
class Ctx:
    """What a round needs from outside the market; never copied or pickled."""

    backends: dict  # models.md, 2: participant -> its model backend
    summarizer: object  # messaging.md, 5.5: the summarizer's backend, or None
    tracker: object  # models.md, 7: the run's cost tracker
    run_program: object  # turns.md, 6: runs one program
    map: object  # rounds.md, 4: map(fn, items) -> the results in the items' order, run in parallel or one by one


class Stop(Exception):
    """The episode is aborted (running.md, 4.2): its status and its error text."""

    def __init__(self, status, error):
        super().__init__(error)
        self.status, self.error = status, error


def _by_reference(request, last):
    """A request as its record holds it (running.md, 6.3 D), and its messages after the system message. `last`:
    the position of the record of the turn's call before it and that call's messages after the system message,
    or None for the turn's first call. `kept` is the longest run of messages the two share from their start."""
    prior, earlier = last or (None, [])
    rest = request["messages"][1:]
    kept = 0
    while kept < min(len(earlier), len(rest)) and earlier[kept] == rest[kept]:
        kept += 1
    names = [f["function"]["name"] for f in request["functions"]]
    return {"prior": prior, "kept": kept, "messages": rest[kept:], "functions": names,
            "tool_choice": request["tool_choice"]}, rest


def request(records, record):
    """The full request of a call or call_failed record, rebuilt from `records`, the whole log in order, the
    record of position n at records[n - 1] (running.md, 6.3 D). Messages: the participant's system prompt (its
    system_prompt record), the first `kept` messages after the system message of the call record at `prior`
    (rebuilt the same way; none when `prior` is None), then its own `messages`. Functions: those it names, as
    the log's functions record defines them. The tool choice is the record's."""
    pid = record["participant"]
    system = next(r["text"] for r in records if r["type"] == "system_prompt" and r["participant"] == pid)
    defined = next(r["functions"] for r in records if r["type"] == "functions")

    def rest(logged):
        earlier = [] if logged["prior"] is None else rest(records[logged["prior"] - 1]["request"])
        return earlier[:logged["kept"]] + logged["messages"]

    logged = record["request"]
    return {"messages": [{"role": "system", "content": system}] + rest(logged),
            "functions": [f for name in logged["functions"] for f in defined if f["function"]["name"] == name],
            "tool_choice": logged["tool_choice"]}


def _wave(m, ctx, pids, chain, call, last):
    """A wave (rounds.md, 4 and 8.4): the cost cap checked once, every chain run, then the replies taken up in the
    turn order: each charged (turns.md, 13.1) and logged, each program logged (running.md, 6.3 D). `last`: per
    participant, the position of the record of its turn's last call so far and that call's messages after the
    system message, which the record of its next call refers to."""
    ctx.tracker.check()
    chains = dict(zip(pids, ctx.map(chain, pids)))
    for pid, c in chains.items():
        p = m.participants[pid]
        spec = config.seat_spec(m.cfg, p.role, p.seat)
        for i, step in enumerate(c.steps, 1):
            reply = step["reply"]
            charged = state.charge(m, pid, step["charge"], "tokens")
            logged, rest = _by_reference(step["request"], last.get(pid))
            state.emit(m, "call", participant=pid, call=call, step=i, request=logged, spec=spec,
                       reply=asdict(reply), charge=charged)
            last[pid] = m.records, rest
            state.count_reply(m, p.role, reply)
            state.count(m, f"{p.role}_reasoning_tokens", reply.reasoning_tokens)
            for program in step["programs"]:
                state.emit(m, "program", participant=pid, call=call, step=i, **program)
                if program["status"] != "refused":
                    state.count(m, f"{p.role}_programs")
        if c.error is not None:
            state.emit(m, "call_failed", participant=pid, call=call, step=len(c.steps) + 1,
                       request=_by_reference(c.request, last.get(pid))[0], spec=spec, error=c.error,
                       failed_cost=c.failed_cost)
    return chains


def _log_lost(m, pid, kind, error):
    """The log's record of a lost turn (rounds.md, 10.1, item 2, and 10.2); `kind` is provider or format."""
    p = m.participants[pid]
    due = [c.id for c in m.contracts.values() if c.manager == pid and not c.deliveries
           and view_contracts.listing_day(m, c) == m.day]
    state.count(m, f"{p.role}_turns_lost_{kind}")
    state.emit(m, "lost_turn", participant=pid, role=p.role,
               reason="failed model call" if kind == "provider" else "format error", error=error, contracts=due)


def _lose(m, pid, undo, error):
    """A turn lost to a failed model call (rounds.md, 10.1): the participant restored, the turn counted and
    logged, and the episode aborted when too many are lost."""
    view.undo(m, pid, undo)
    m.lost_turns += 1
    _log_lost(m, pid, "provider", error)
    if m.lost_turns > config.lost_turn_limit(m.cfg):
        raise Stop("aborted_lost_turns", f"{m.lost_turns} turns lost to failed model calls; the last: {error}")


def _read(m, pid, chain, limit, call):
    """The reply that ended a chain, read into actions and a memory or a format error, and logged (rounds.md, 5
    and 8.6; turns.md, 7 and 13.2)."""
    if chain.empty:
        reading = blank()
    else:
        reading = read_reply(chain.reply.tool_calls, chain.reply.finish_reason == "length", limit)
    if reading["error"] is not None:
        state.count(m, f"{m.participants[pid].role}_format_errors")
    state.emit(m, "reply", participant=pid, call=call, actions=reading["actions"], memory=reading["memory"],
               error=reading["error"])
    return reading


def _lines(nodes, notes):
    """The result lines of a participant's actions of a phase, each failed one with its note (rounds.md, 8.2),
    then the note for its stopped actions (turns.md, 9)."""
    lines = [actions.result_line(n.name, n.ok, n.message) + (f" {notes[n.pos]}" if n.pos in notes else "")
             for n in nodes if n.outcome != "stopped"]
    stopped = sum(n.outcome == "stopped" for n in nodes)
    return lines + ([actions.stopped_note(stopped)] if stopped else [])


def play(m, order, ctx):
    """One round, in the order of rounds.md, 11; `order` is its turn order, over all participants."""
    cfg, people = m.cfg, m.participants
    takers = [pid for pid in order if not people[pid].out_of_business]  # 2
    state.emit(m, "round", order=list(order), takers=takers)
    jobs, undo = {}, {}
    for pid in takers:  # 3
        p = people[pid]
        system = prompts.system_prompt(cfg, p.role, pid, p.share, p.seat)
        if not p.prompt_logged:
            p.prompt_logged = True
            state.emit(m, "system_prompt", participant=pid, text=system)
        text, undo[pid] = view.build(m, pid, ctx.summarizer, ctx.tracker)
        state.emit(m, "turn", participant=pid, role=p.role, balance=round(p.balance, 2))
        jobs[pid] = (system, text, ctx.backends[pid], cfg, p.balance, p.role == "manager", ctx.run_program)
    start, round_ids = copy.deepcopy(m), set(m.posts) | set(m.bids)

    last = {}  # running.md, 6.3 D: participant -> its turn's last call so far, as its next call's record refers to it
    chains = _wave(m, ctx, takers, lambda pid: turns.decide(*jobs[pid]), "decide", last)  # 4
    lost = set()  # the turns lost to the provider
    for pid in takers:  # 10.1
        if chains[pid].error is not None:
            lost.add(pid)
            _lose(m, pid, undo[pid], chains[pid].error)

    readings, lines, plans, format_lost = {}, {}, {}, {}
    for pid in takers:  # 5
        if pid in lost:
            continue
        reading = readings[pid] = _read(m, pid, chains[pid], cfg["turns"]["max_actions"], "decide")
        if reading["error"] is None:
            plans[pid], lines[pid] = reading["actions"], list(reading["notes"])
        elif not people[pid].out_of_business:
            plans[pid], lines[pid] = [], [turns.format_line(reading["error"])]
        else:  # 10.2
            format_lost[pid] = turns.lost_text(reading["error"])
            _log_lost(m, pid, "format", reading["error"])
    first = phase.run_phase(m, "first", order, plans, start)  # 6, 7

    due = {}  # 8.1 to 8.3: participant -> its correction's limit, its request's messages, the bids shown to it
    for pid, nodes in first.plans.items():
        failed, unreadable = sum(n.ok is False for n in nodes), readings[pid]["error"] is not None
        if people[pid].out_of_business or not (unreadable or failed):
            lines[pid] += _lines(nodes, {})
            continue
        told = phase.review(m, first, pid, start)
        lines[pid] += _lines(nodes, told.notes)
        limit = cfg["turns"]["max_actions"] if unreadable else failed
        body = turns.correction_body(turns.correction_note(limit, unreadable), view.space(lines[pid]),
                                     people[pid].pending, told.facts)
        due[pid] = limit, turns.correction_messages(chains[pid], readings[pid], body), told.shown

    corrected, memories = {}, {}  # the lines and the memory of each correction that was read
    if due:  # 8.4, 8.6
        after = copy.deepcopy(m)
        calls = _wave(m, ctx, list(due), lambda pid: turns.correct(due[pid][1], ctx.backends[pid], cfg),
                      "correction", last)
        for pid, chain in calls.items():
            state.count(m, f"{people[pid].role}_corrections")
            if chain.error is None:
                continue
            if readings[pid]["error"] is None:
                lines[pid].append(turns.correction_failed)
            else:  # 10.1
                lost.add(pid)
                _lose(m, pid, undo[pid], chain.error)
        again = {}
        for pid, chain in calls.items():
            if chain.error is not None:
                continue
            err = readings[pid]["error"]
            reading = _read(m, pid, chain, due[pid][0], "correction")
            if reading["error"] is None:
                again[pid], corrected[pid] = reading["actions"], list(reading["notes"])
                memories[pid] = reading["memory"]
            elif err is None:
                lines[pid].append(turns.correction_format_line(reading["error"]))
            else:  # 10.2
                format_lost[pid] = turns.lost_text(err, reading["error"])
                _log_lost(m, pid, "format", reading["error"])
        if again:
            hidden = {pid: phase.hidden(m, pid, round_ids, due[pid][2]) for pid in again}
            second = phase.run_phase(m, "correction", order, again, after, hidden)
            for pid, nodes in second.plans.items():
                corrected[pid] += _lines(nodes, {})

    state.land_credits(m)  # 9.1
    for pid in takers:  # 9.2
        p = people[pid]
        if pid in lost:
            continue
        if pid in format_lost:
            p.results = [format_lost[pid]]
            continue
        sent = readings[pid]["memory"] if memories.get(pid) is None else memories[pid]
        memory = turn_memory(p.memory and p.memory.text, readings[pid]["memory"], memories.get(pid),
                             cfg["turns"]["memory_chars"])
        if memory["written"]:
            p.memory = state.Memory(memory["text"], m.day, m.round, p.turns, memory["from_correction"])
            state.emit(m, "memory", participant=pid, text=memory["text"], length=len(sent),
                       cut=len(sent) > len(memory["text"]), from_correction=memory["from_correction"])
        p.results = turns.results(lines[pid], corrected.get(pid), memory["note"])
