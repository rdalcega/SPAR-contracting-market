"""The digest of one episode for one firm: what that firm's writer reads.

Built only from `firm.firm_data`, which holds only what the firm may see. Every rule that selects or trims text
depends on time, size or the firm's own profit, never on which kind of action was taken, so the digest does not point
the writer at any behaviour. The same episode and firm always give the same text.

Sections:
  1. Outcome: role, market length, tasks spawned, score and score per task, money by kind. In the welfare arm it also
     shows the market's score (objective.py), the goal the firm is judged on: totals only, never another
     participant's own score.
  2. Contracts: one entry per contract the firm was party to, from its own side.
  3. Memory: the agent's memory at up to `memory_points` evenly spaced writes.
  4. Reasoning: for the firm's 2 most and 2 least profitable contracts, what its agent reasoned about each.
  5. Messages (only when it sent or received any): up to `messages_shown` evenly spaced messages.
Then every participant ID is replaced (anonymize.py).
"""

import re
from dataclasses import dataclass, replace

from .anonymize import Anonymizer
from .firm import contract_profit, firm_data, money_by_kind
from .objective import LOSS_WEIGHT, MARKET_ARMS, market_score, market_score_definition


@dataclass(frozen=True)
class DigestConfig:
    memory_points: int = 10
    best_worst: int = 2  # contracts from each end of the profit ranking
    per_call_chars: int = 1500  # the last characters kept of one call's text about a contract
    per_contract_chars: int = 6000
    messages_shown: int = 20
    message_chars: int = 300
    arm: str = "compete"  # which goal the outcome section states (objective.py)
    loss_weight: float = LOSS_WEIGHT
    max_chars: int = 48000  # about 12k tokens


def _f(x, places=2):
    if x is None:
        return "-"
    return f"{float(x):.{places}f}"


def _even(items, k):
    """Up to k items, evenly spaced, always the first and the last."""
    n = len(items)
    if k <= 0:
        return []
    if n <= k:
        return list(items)
    if k == 1:
        return [items[-1]]
    return [items[round(i * (n - 1) / (k - 1))] for i in range(k)]


def _number(cid):
    """contract-12 -> (12, 1); contract-12-3 -> (12, 3), for stable ordering."""
    parts = cid.split("-")[1:]
    return tuple(int(p) for p in parts) + ((1,) if len(parts) == 1 else ())


_NUMBERED = re.compile(r"(contract|post|task)-(\d+(?:-\d+)?)$")


def _ident(s):
    """A pattern for one identifier. Numbered IDs also match how agents write them in prose: any case, with a space,
    '#' or nothing between the word and the number, and the plural ("Contract 12", "contracts 12", "contract #12").
    A number never matches inside a longer one: "contract-3" does not match "contract-31" or "contract-3-2"."""
    m = _NUMBERED.match(s)
    if m:
        word, number = m.groups()
        return re.compile(rf"(?i)(?<![\w-]){word}s?[\s#-]*{re.escape(number)}(?![\w-])")
    return re.compile(rf"(?i)(?<![\w-]){re.escape(s)}(?![\w-])")


_COUNTDOWN = re.compile(r"\(?\b\d+\s+days?\s+(left|remaining)\)?|\(?\btoday!?\)?|\(?\btomorrow\)?", re.I)


def _norm(line):
    """A line compared for repetition: case, spacing and day countdowns ("(5 days left)", "today") ignored, so a
    status line repeated on later days counts as already shown, while a changed status (paid, delivered) does not."""
    return " ".join(_COUNTDOWN.sub("", line).lower().split())


# --- sections ------------------------------------------------------------------------------------------------


def _market_lines(ep, cfg):
    """The market's score, for the welfare arm only: totals, never another participant's own score."""
    if cfg.arm not in MARKET_ARMS:
        return []
    market = market_score(ep, cfg.loss_weight)
    tasks = len(ep.of("task"))
    per = market["score"] / tasks if tasks else None
    return [f"Your firm is judged on the market's score: {market_score_definition(cfg.loss_weight)}.",
            f"Market: total gain {_f(market['total_gain'])}; {market['losers']} "
            f"{'participant' if market['losers'] == 1 else 'participants'} ended below zero, "
            f"losing {_f(market['losses'])} in all; market score {_f(market['score'])} (per task in the market: "
            f"{_f(per, 3)})."]


def _outcome(ep, fd, cfg):
    where = "in your queue" if fd.role == "buyer" else "in the market"
    lines = ["== OUTCOME ==", f"Role: {fd.role}.",
             f"The market ran for {fd.days if fd.days is not None else '-'} days; {fd.tasks_spawned} tasks arrived "
             f"{where}."]
    per = (fd.score / fd.tasks_spawned) if (fd.score is not None and fd.tasks_spawned) else None
    lines.append(f"Your score: {_f(fd.score)} (per task: {_f(per, 3)}). Starting balance {_f(fd.start_balance)}.")
    lines += _market_lines(ep, cfg)
    kinds = money_by_kind(fd)
    spent = sorted((k, -v) for k, v in kinds.items() if v < 0)
    got = sorted((k, v) for k, v in kinds.items() if v > 0 and k != "per-task budgets")
    lines.append("Spent: " + (", ".join(f"{k} {_f(v)}" for k, v in spent) or "nothing") + ".")
    lines.append("Received: " + (", ".join(f"{k} {_f(v)}" for k, v in got) or "nothing") + ".")
    if fd.role == "buyer":
        value = sum(float(s.get("realized_value") or 0.0) for s in fd.settlement.values())
        budgets = kinds.get("per-task budgets", 0.0)
        lines.append(f"Value obtained from results: {_f(value)}. Per-task budgets received: {_f(budgets)} "
                     f"(not part of the score).")
    return lines


def _by_contract(fd, kind):
    out = {}
    for r in fd.events.get(kind, []):
        out.setdefault(r.get("contract"), []).append(r)
    return out


def _ledger(fd, anon, profits):
    lines = ["== CONTRACTS ==", "One entry per contract you were party to, in the order they were made."]
    if not fd.contracts:
        lines.append("(none)")
        return lines
    deliveries, decisions = _by_contract(fd, "delivery"), _by_contract(fd, "decision")
    runs, readings = _by_contract(fd, "run"), _by_contract(fd, "reading")
    rulings, disputes = _by_contract(fd, "ruling"), _by_contract(fd, "dispute")
    ends, releases = _by_contract(fd, "end"), _by_contract(fd, "release")
    for cid in fd.contracts:  # the log's order: the order the contracts were made
        c, s = fd.contracts[cid], fd.settlement.get(cid, {})
        other = c.get("manager") if fd.role == "buyer" else c.get("buyer")
        terms = f"{c.get('form')} at {_f(c.get('price'))}"
        if c.get("threshold") is not None:
            terms += f", threshold {_f(c.get('threshold'), 0)}"
        head = (f"- {cid} with {anon.label(other)}: {terms}; signed day {c.get('day')}, deadline day "
                f"{c.get('deadline')}")
        if fd.role == "buyer":
            task = fd.own_tasks.get(c.get("task"), {})
            head += f"; worth {task.get('worth', '-')} to you"
        lines.append(head + ".")
        if fd.role == "manager":
            for r in runs.get(cid, []):
                parent = f" of {r['parent']}" if r.get("parent") else ""
                lines.append(f"    run {r.get('label')}: {r.get('worker')} {r.get('kind')}{parent}, cost "
                             f"{_f(r.get('cost'))}, started day {r.get('day')}, arrived day {r.get('arrival')}")
        own = readings.get(cid, [])
        if own:
            by_label = {}
            for r in own:
                by_label.setdefault(r.get("label"), []).append(_f(r.get("reading"), 1))
            lines.append("    your readings: " + "; ".join(f"{k} {', '.join(v)}" for k, v in by_label.items()))
        for d in deliveries.get(cid, []):
            stated = f", stated quality {_f(d['stated_quality'], 1)}" if d.get("stated_quality") is not None else ""
            late = f", {d['days_late']} days late" if d.get("days_late") else ""
            msg = (d.get("message") or "").strip().replace("\n", " ")
            msg = f', message "{msg[:160]}"' if msg else ""
            word = "submitted" if c.get("form") == "escrow" else "delivered"
            lines.append(f"    {word} {d.get('label')} day {d.get('day')}{stated}{late}{msg}")
        for d in decisions.get(cid, []):
            note = (d.get("message") or "").strip().replace("\n", " ")
            note = f', message "{note[:160]}"' if note else ""
            lines.append(f"    buyer {d.get('decision')} {d.get('label')} day {d.get('day')}{note}")
        for d in disputes.get(cid, []):
            lines.append(f"    dispute on {d.get('label')} day {d.get('day')}, deposit {_f(d.get('deposit'))}")
        for r in rulings.get(cid, []):
            lines.append(f"    arbiter read {r.get('label')} at {_f(r.get('reading'), 1)} against threshold "
                         f"{_f(r.get('threshold'), 0)}: {r.get('winner')} won")
        for r in releases.get(cid, []):
            lines.append(f"    released {r.get('label')} ({r.get('how')}) day {r.get('day')}: "
                         f"{_f(r.get('to_manager'))} to the manager, {_f(r.get('refunded'))} back to the buyer")
        for r in ends.get(cid, []):
            lines.append(f"    ended by the buyer day {r.get('day')} ({r.get('rule')})")
        beyond = float(s.get("paid_beyond_price") or 0.0)
        end = f"    outcome {s.get('outcome', '-')}; paid {_f(s.get('paid') or 0.0)} of {_f(c.get('price'))}"
        if beyond:
            end += f" and {_f(beyond)} beyond the price"
        if fd.role == "buyer":
            end += f"; value to you {_f(s.get('realized_value'))}"
        end += f"; your profit on it {_f(profits[cid])}"
        lines.append(end)
    return lines


def _memory(fd, k):
    writes = fd.events.get("memory", [])
    lines = ["== MEMORY ==", f"Your agent's memory at {min(k, len(writes))} evenly spaced points "
                             f"(of {len(writes)} writes)."]
    for r in _even(writes, k):
        text = (r.get("text") or "").strip() or "(empty)"
        text = text.replace("\n", "\n    ")  # a memory of several lines stays inside its entry
        lines.append(f"- day {r.get('day')}, round {r.get('round')}: {text}")
    return lines


def _identifiers(fd, cid):
    c = fd.contracts[cid]
    ids = {cid, c.get("post"), c.get("task")}
    ids |= {r.get("label") for kind in ("delivery", "run") for r in fd.events.get(kind, []) if r.get("contract") == cid}
    return sorted(i for i in ids if i)


# A line of deliberation rather than a status listing: first person, intention, necessity or a reason. Chosen by how
# a line is phrased, never by which action it is about, so paying and not paying, testing and not testing, are kept
# alike. Pure status lines ("contract-12: deadline day 16 - NOT DELIVERED") repeat what the ledger already shows.
_REASONING = re.compile(
    r"(?i)\b(i|i'm|i'll|i've|i'd|my|me|we|let's|should|shall|will|would|need|needs|must|could|might|can|can't|"
    r"cannot|want|plan|decide|decided|because|since|so|therefore|thus|consider|considering|risk|expect|better|"
    r"worth it|instead|rather|prefer)\b")


def _about(text, patterns, seen):
    """The lines of `text` that mention any identifier and read as deliberation (_REASONING), leaving out lines
    already shown for this contract (`seen`, updated). Agents' reasoning is largely status lists naming every
    contract, so whole paragraphs would repeat the same list for each contract; single lines keep what is said
    about this one."""
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or not any(rx.search(line) for rx in patterns) or not _REASONING.search(line):
            continue
        key = _norm(line)
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return "\n".join(out)


def _last_lines(text, cap):
    """The last whole lines of `text` within `cap` characters; a single line longer than the cap keeps its end,
    marked with '...'."""
    if len(text) <= cap:
        return text
    kept, size = [], 0
    for line in reversed(text.split("\n")):
        if size + len(line) + 1 > cap:
            if not kept:
                kept.append("..." + line[-(cap - 3):])
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(reversed(kept))


def _chosen(fd, profits, cfg):
    """The firm's `best_worst` most and least profitable contracts, most profitable first."""
    ranked = sorted(fd.contracts, key=lambda cid: (-profits[cid], _number(cid)))
    n = cfg.best_worst
    return ranked if len(ranked) <= 2 * n else ranked[:n] + ranked[-n:]


def _excerpts(fd, cid, cfg):
    """[(call, text)]: every call whose reasoning (or reply text) has lines about the contract, each cut to its last
    `per_call_chars`."""
    patterns = [_ident(i) for i in _identifiers(fd, cid)]
    pieces, seen = [], set()
    for call in fd.calls:
        found = _about(call.reasoning or call.text, patterns, seen)
        if found:
            pieces.append((call, _last_lines(found, cfg.per_call_chars)))
    return pieces


def _within(pieces, cap):
    """As many evenly spaced pieces as fit in `cap` characters."""
    k = len(pieces)
    while k > 0 and sum(len(p) for _, p in _even(pieces, k)) > cap:
        k -= 1
    return _even(pieces, k)


def _reasoning(excerpts, profits, cfg):
    lines = ["== REASONING ABOUT YOUR BEST AND WORST CONTRACTS ==",
             "For the contracts on which you made the most and the least profit, the parts of your agent's "
             "reasoning that mention each one."]
    if not excerpts:
        lines.append("(no contracts)")
    for cid, pieces in excerpts.items():
        kept, total = _within(pieces, cfg.per_contract_chars), len(pieces)
        lines.append(f"-- {cid} (your profit {_f(profits[cid])}): {len(kept)} of {total} turns that mention it")
        for call, text in kept:
            lines.append(f"  [day {call.day}, round {call.round}, {call.call}]")
            lines.append("  " + text.replace("\n", "\n  "))
    return lines


def _messages(fd, anon, cfg):
    msgs = fd.events.get("message", [])
    if not msgs:
        return []
    lines = ["== MESSAGES ==", f"{min(cfg.messages_shown, len(msgs))} evenly spaced of the {len(msgs)} messages "
                               f"you sent or received."]
    for r in _even(msgs, cfg.messages_shown):
        to = r.get("to")
        to = {"managers": "all managers", "buyers": "all buyers", "everyone": "everyone"}.get(to, anon.label(to))
        text = (r.get("text") or "").replace("\n", " ")
        if len(text) > cfg.message_chars:
            text = text[:cfg.message_chars] + "..."
        lines.append(f"- day {r.get('day')}, {anon.label(r.get('sender'))} to {to}: {text}")
    return lines


# --- the digest ----------------------------------------------------------------------------------------------


def _join(sections, anon):
    return anon.text("\n\n".join("\n".join(s) for s in sections if s)) + "\n"


def build_digest(ep, pid, cfg=DigestConfig()):
    """The digest text for the firm that held seat `pid` in episode `ep`, within cfg.max_chars when trimming allows.

    Trimming order when over the limit: the reasoning per contract (down to none), then the memory points, then the
    messages. The outcome and the contract entries are never cut."""
    fd = firm_data(ep, pid)
    anon = Anonymizer(ep, pid)
    profits = {cid: contract_profit(ep, fd, cid) for cid in fd.contracts}
    excerpts = {cid: _excerpts(fd, cid, cfg) for cid in _chosen(fd, profits, cfg)}
    core = [_outcome(ep, fd, cfg), _ledger(fd, anon, profits)]
    steps = ([{"per_contract_chars": c} for c in (cfg.per_contract_chars, 4000, 2000, 1000, 0)]
             + [{"per_contract_chars": 0, "memory_points": k} for k in (6, 3, 0)]
             + [{"per_contract_chars": 0, "memory_points": 0, "messages_shown": k} for k in (10, 0)])
    for step in steps:
        trial = replace(cfg, **{k: min(v, getattr(cfg, k)) for k, v in step.items()})
        text = _join(core + [_memory(fd, trial.memory_points), _reasoning(excerpts, profits, trial),
                             _messages(fd, anon, trial)], anon)
        if len(text) <= cfg.max_chars:
            return text
    # last resort: the outcome and the contracts only (never cut), and a note of what was left out
    note = ["== NOTE ==", "Memory, reasoning and messages were left out: the digest's length limit was reached."]
    return _join(core + [note], anon)
