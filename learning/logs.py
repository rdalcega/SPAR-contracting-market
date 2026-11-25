"""Reading one episode's folder into plain structures (spec/running.md, 6.1 to 6.6).

The reader keeps every record. It indexes the record types the digest and the measures use, and keeps the rest in
`Episode.other` by type, so a log with record types this reader does not know still loads (and the unknown types
are listed in `Episode.unknown_types`, for review when the market changes).
"""

import json
import os
from dataclasses import dataclass, field

# Record types the reader indexes. Any other type goes to Episode.other.
KNOWN = {
    "fresh_start", "functions", "day", "arrivals", "task", "round", "system_prompt", "turn", "call", "call_failed",
    "program", "reply", "memory", "lost_turn", "phase", "action", "clash", "notification", "money", "credit_landed",
    "post", "post_edited", "post_withdrawn", "bid", "bid_withdrawn", "contract", "run", "arrival", "reading",
    "delivery", "keep", "decision", "release", "auto_release", "dispute", "ruling", "fee_paid", "end",
    "lateness_close", "refund_at_close", "payment", "message", "summary", "summary_failed", "summarizer_call",
    "out_of_business", "back_in_business", "close", "settled", "stopped", "resume", "checkpoint_not_used",
}


@dataclass
class Participant:
    id: str
    role: str  # "buyer" or "manager"
    seat: int
    share: float
    start_balance: float
    budget: float | None  # a buyer's per-task budget; None for a manager


@dataclass
class Episode:
    folder: str
    seed: int
    config: dict
    summary: dict
    records: list
    participants: dict  # id -> Participant
    by_type: dict  # record type -> list of records, in log order
    other: dict = field(default_factory=dict)
    unknown_types: set = field(default_factory=set)

    # --- simple accessors -------------------------------------------------------------------------------------

    def of(self, kind):
        """Every record of one type, in log order (an empty list if there is none)."""
        return self.by_type.get(kind, [])

    def seat_of(self, pid):
        p = self.participants[pid]
        return p.role, p.seat

    def id_of(self, role, seat):
        for p in self.participants.values():
            if p.role == role and p.seat == seat:
                return p.id
        raise KeyError((role, seat))

    @property
    def status(self):
        return self.summary.get("status")

    @property
    def close_day(self):
        return self.summary.get("close_day")

    @property
    def scores(self):
        """id -> the participant's score row in summary.json (6.6)."""
        return {row["id"]: row for row in self.summary.get("scores") or []}

    @property
    def settlement(self):
        """contract id -> its settlement row in summary.json (6.5)."""
        return {row["contract"]: row for row in self.summary.get("settlement") or []}

    @property
    def tasks(self):
        """task id -> its task record (episode.md, 3.2)."""
        return {r["task"]: r for r in self.of("task")}

    @property
    def contracts(self):
        """contract id -> its contract record (actions-buyer.md, accept_bid)."""
        return {r["contract"]: r for r in self.of("contract")}


def _read_optional_json(path):
    """The file's JSON, or an empty dict when the file does not exist."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_episode(folder):
    """One episode's folder. Raises on a missing or unreadable log; summary.json and config.json are optional
    (an episode stopped early may lack a full summary), and an empty dict stands in for one that is missing."""
    log_path = os.path.join(folder, "log.jsonl")
    records = []
    with open(log_path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{log_path}: line {number} is not JSON ({e})") from None
    summary = _read_optional_json(os.path.join(folder, "summary.json"))
    config = _read_optional_json(os.path.join(folder, "config.json"))

    by_type, other, unknown = {}, {}, set()
    for r in records:
        kind = r.get("type")
        if kind in KNOWN:
            by_type.setdefault(kind, []).append(r)
        else:
            other.setdefault(kind, []).append(r)
            unknown.add(kind)

    starts = by_type.get("fresh_start") or []
    if not starts:
        raise ValueError(f"{log_path}: no fresh_start record")
    start = starts[0]
    participants = {
        p["id"]: Participant(id=p["id"], role=p["role"], seat=p["seat"], share=p["share"],
                             start_balance=p["start_balance"], budget=p.get("budget"))
        for p in start["participants"]
    }
    return Episode(folder=folder, seed=start["seed"], config=config, summary=summary, records=records,
                   participants=participants, by_type=by_type, other=other, unknown_types=unknown)


def episode_folders(run_dir):
    """The episode folders of one run (`<output_dir>/<name>/seed_<seed>/`), sorted by seed."""
    out = []
    for name in os.listdir(run_dir):
        path = os.path.join(run_dir, name)
        if name.startswith("seed_") and os.path.exists(os.path.join(path, "log.jsonl")):
            out.append((int(name[len("seed_"):]), path))
    return [path for _, path in sorted(out)]
