"""A run's state: every version of every firm's instructions, what each generation decided, and where the run is.

Saved as one JSON file after every step (atomically), so a run stopped at any point resumes from its last step.
"""

import json
import os
from dataclasses import asdict, dataclass, field

from .design import FIRMS


def firm_key(firm):
    role, seat = firm
    return f"{role}_{seat}"


@dataclass
class Version:
    id: int
    text: str
    parent: int | None
    generation: int  # the generation it was written for (0: the starting, empty version)
    rationale: str = ""
    mean: float | None = None
    se: float | None = None
    diffs: list = field(default_factory=list)
    kept: bool | None = None
    reason: str = ""


@dataclass
class FirmState:
    current: int = 0
    versions: list = field(default_factory=lambda: [Version(0, "", None, 0)])
    pool: list = field(default_factory=list)  # episode folders played with the current version, for its digests
    candidate: int | None = None  # the version under test this generation

    def version(self, vid):
        return next(v for v in self.versions if v.id == vid)

    def history(self):
        """Every decided revision, oldest first, for the writer."""
        return [{"generation": v.generation, "rationale": v.rationale, "mean": v.mean, "kept": v.kept}
                for v in self.versions if v.kept is not None]


@dataclass
class RunState:
    spec: dict
    generation: int = 0  # the last generation completed (0: none, or only the starting batch)
    step: str = "start"  # start | written | played | decided
    firms: dict = field(default_factory=lambda: {firm_key(f): FirmState() for f in FIRMS})
    log: list = field(default_factory=list)  # one entry per completed step


def _firm(d):
    return FirmState(current=d["current"], versions=[Version(**v) for v in d["versions"]], pool=d["pool"],
                     candidate=d.get("candidate"))


def save(state, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, indent=1)
    os.replace(tmp, path)


def load(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return RunState(spec=d["spec"], generation=d["generation"], step=d["step"],
                    firms={k: _firm(v) for k, v in d["firms"].items()}, log=d["log"])
