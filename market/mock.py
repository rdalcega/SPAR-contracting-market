"""The offline mock model (spec/running.md, 7): the free, deterministic backend of provider `mock`.

A reply depends only on the request. The mock reads its ID, with it its role, and the action cap from the system
prompt (prompts.md), and the market from the view, the request's second message, as view.md and view-contracts.md
lay it out: the section titles it looks for are the ones the view's modules write.
"""

import hashlib
import json
import re

from . import messaging, models, view_contracts
from .view import MEMORY as MEMORY_TITLE
from .view import NOTIFICATIONS

CODE = "print(sum(range(10)))"
MEMORY = "mock memory"
INVALID = (  # 7.6
    {"action": "frobnicate"},
    {"action": "bid", "post_id": "post-0", "price": 5},
    {"action": "accept_bid", "bid_id": "bid-0"},
    {"action": "pay", "to": "X-000", "amount": 1},
    {"action": "deliver", "contract_id": "contract-0", "result": "R-zzz"},
)

_ID = re.compile(r"your ID is ([BM]-[0-9a-f]{3})")
_CAP = re.compile(r"Actions beyond the first (\d+) are dropped")
_FRAME = re.compile(r"=== DAY (\d+), ROUND (\d+) of (\d+) today === .*\nParticipants - buyers: (.*); managers: (.*)\.")
_PARTIES = re.compile(r"(\S+) with (\S+) \| ")
_REVIEW = re.compile(r" \| price held; \S+ UNDER REVIEW since day ")
_UNTESTED = re.compile(r"\S+ delivered day \d+ \(in use\) \| your readings: none")
_OWED = re.compile(r" \(plain\) \| paid (\d+\.\d\d) of (\d+\.\d\d) \| .* \| in use: ")
_BID = re.compile(r" \| bids: (bid-\d+) by ")
# A number is odd or even by its last digit (int refuses a text of more than 4300 digits, and the mock never fails).
_QUEUED = re.compile(r"(task-(\d+)) \| .* \| QUEUED \(not posted")
_POST = re.compile(r"(post-(\d+)\S*) by .*; you have not bid")
_WORK = re.compile(r"contract-(\d+).* \| (NOT DELIVERED|price held; (nothing submitted|\d+ submissions? rejected))")
# A result line, field by field (view-contracts.md, 7.3): `flying` until it arrives, then with the manager's
# `readings` of it and, once it is delivered or submitted, how it was `given`.
_RESULT = re.compile(
    r"(?P<label>\S+) \| [^|]+ \| [^|]+ \| started day \d+, (?:(?P<flying>arrives)|arrived) day [^|]*"
    r"(?: \| your (?P<readings>readings[^|]*?)(?: \| (?P<given>SUBMITTED|DELIVERED|delivered) day .*)?)?")
_UNTRIED = "readings: none"
_NOTES = f"\n{NOTIFICATIONS}\n"
_MEMORY = "\n" + MEMORY_TITLE


def backend(spec):
    """The backend of a mock spec (models.md, 3): no key, no network, no cost tracker."""
    return _Mock(spec)


class _Mock:
    """A mock spec's backend; it keeps the spec's prices, a missing one as 0 (7.1)."""

    def __init__(self, spec):
        self.prices = [float(models.resolved(spec)[key]) for key in ("price_in_per_m", "price_out_per_m")]

    def call(self, messages, functions=None, tool_choice=None):
        """One reply (7.2), with its tokens and its cost by the mock's own formula (7.1)."""
        offered = [f["function"]["name"] for f in functions or []]
        system, view = ((message.get("content") or "") for message in [*messages, {}, {}][:2])
        calls = [call for message in messages for call in message.get("tool_calls") or []]
        if len(messages) == 2 and "compute" in offered:
            name, arguments = "compute", {"code": CODE}
        elif any(call["function"]["name"] == "act" for call in calls):
            name, arguments = "act", {"actions": [], "memory": MEMORY}
        else:
            name, arguments = "act", {"actions": actions(system, view), "memory": MEMORY}
        position = 1 + sum(message.get("role") == "assistant" for message in messages)
        tokens_in, tokens_out = models.estimate(models.request_text({"messages": messages})), models.estimate("")
        return models.Reply(
            tool_calls=[{"id": f"call_mock_{position}", "type": "function",
                         "function": {"name": name, "arguments": json.dumps(arguments)}}],
            input_tokens=tokens_in, output_tokens=tokens_out,
            cost=(tokens_in * self.prices[0] + tokens_out * self.prices[1]) / 10**6,
            input_estimated=True, output_estimated=True, cost_source="computed", finish_reason="stop")


def actions(system, view):
    """The action list of a mock participant's turn: its role's rules in order (7.3, 7.4), its message (7.5) and
    its invalid action (7.6), cut at the action cap (7.2)."""
    me, frame, cap = _ID.search(system), _FRAME.match(view), _CAP.search(system)
    if not (me and frame):
        return []
    me, sections = me[1], _sections(view)
    out = _buyer(sections) if me[0] == "B" else _manager(sections)
    title = next((title for title in sections if title.startswith(messaging.TITLE)), None)
    if title:
        day, rnd, rounds = (int(n) for n in frame.group(1, 2, 3))
        everyone = title == messaging.TITLE + " ---"
        others = [q for ids in frame.group(4, 5) for q in ids.split(", ") if q != me and (everyone or q[0] == me[0])]
        targets = ["everyone"] * everyone + ["buyers" if me[0] == "B" else "managers"] + others
        out.append({"action": "send_message", "to": targets[((day - 1) * rounds + rnd) % len(targets)],
                    "text": f"Mock message from {me} on day {day}, round {rnd}: price 20, threshold 7?"})
    h = hashlib.sha256(view.encode("utf-8", "surrogatepass")).digest()
    if h[0] % 8 == 0:
        out.append(dict(INVALID[h[1] % 5]))
    return out[:int(cap[1])] if cap else out


def _sections(view):
    """The view's sections by their titles, each the list of its items; an item is its line followed by the lines
    indented under it, without their indent (view.md, 2.2, 2.3). The results text and the memory are their
    participant's text, which creates no section whatever it holds (2.3), and are not read: the memory's title is
    the first one after a Notifications title, and the results text ends at the last Notifications title before
    it. (A results text that quotes both titles in that order reads like a memory that does, and is taken for
    the market.)"""
    sections, items = {}, None
    for line in view.partition(_NOTES)[2].partition(_MEMORY)[0].rpartition(_NOTES)[2].split("\n"):
        if line.startswith("--- ") and line.endswith(" ---"):
            items = sections[line] = []
        elif line and items is not None:
            if line[0] == " " and items:
                items[-1].append(line.lstrip(" "))
            else:
                items.append([line])
    return sections


def _section(sections, title):
    """The items of the section whose title starts so (a finished section's title may carry a count)."""
    return next((items for name, items in sections.items() if name.startswith(title)), [])


def _contracts(sections, title):
    """The contract items of that section: those whose line opens with the contract and its counterparty (the
    bids note and the older contracts' summary line are none)."""
    return [item for item in _section(sections, title) if _PARTIES.match(item[0])]


def _buyer(sections):
    """A mock buyer's actions: approve, test, pay, accept, post, each at most once (7.3, rules 1 to 5)."""
    tasks = [item[0] for item in _section(sections, view_contracts.TASKS)]
    active = _contracts(sections, view_contracts.ACTIVE)
    out = []
    for item in active:
        if _REVIEW.search(item[0]):
            out.append({"action": "approve_submission", "contract_id": _PARTIES.match(item[0])[1]})
            break
    for item in active:
        if " (plain) | " in item[0] and any(_UNTESTED.fullmatch(line) for line in item[1:]):
            out.append({"action": "test_delivery", "contract_id": _PARTIES.match(item[0])[1]})
            break
    for item in active + _contracts(sections, view_contracts.FINISHED):
        owed = _OWED.search(item[0])
        if owed and float(owed[1]) < float(owed[2]):
            contract, manager = _PARTIES.match(item[0]).groups()
            out.append({"action": "pay", "to": manager, "amount": float(owed[2]) - float(owed[1]),
                        "contract_id": contract})
            break
    bid = next(filter(None, map(_BID.search, tasks)), None)
    if bid:
        out.append({"action": "accept_bid", "bid_id": bid[1]})
    task = next(filter(None, map(_QUEUED.match, tasks)), None)
    if task:
        terms = {"form": "escrow", "threshold": 7} if int(task[2][-1]) % 2 else {"form": "plain"}
        out.append({"action": "post_task", "task_id": task[1], **terms})
    return out


def _manager(sections):
    """A mock manager's actions: one bid, then one piece of work per contract that can take work (7.4, rules 1
    and 2). A contract with a line under it that is no result line, escrow days line or `no results yet` gets
    none."""
    out = []
    posts = _section(sections, view_contracts.FORUM)
    post = next(filter(None, (_POST.fullmatch(item[0]) for item in posts)), None)
    if post:
        escrow = " | prefers escrow" in post[0] or (" | no preferred form" in post[0] and int(post[2][-1]) % 2)
        out.append({"action": "bid", "post_id": post[1], "price": 20, **({"threshold": 7} if escrow else {})})
    for item in _contracts(sections, view_contracts.ACTIVE):
        work = _WORK.match(item[0])
        results = [_RESULT.fullmatch(line) for line in item[1:]
                   if line != view_contracts.NO_RESULTS and not line.startswith(view_contracts.ESCROW_DAYS)]
        if not work or not all(results):
            continue
        contract = _PARTIES.match(item[0])[1]
        ready = [r for r in results if not r["flying"] and not r["given"]]
        untested = [r["label"] for r in ready if r["readings"] == _UNTRIED]
        tested = [r["label"] for r in ready if r["readings"] != _UNTRIED]
        if untested:
            out.append({"action": "test_result", "contract_id": contract, "result": untested[0]})
        elif tested:
            out.append({"action": "deliver", "contract_id": contract, "result": tested[0]})
        elif not any(r["flying"] for r in results):
            out.append({"action": "run_myself" if int(work[1][-1]) % 2 else "delegate_run", "contract_id": contract})
    return out
