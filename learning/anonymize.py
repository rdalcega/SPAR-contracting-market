"""Participant IDs replaced by labels that hold within one episode.

IDs (`B-` or `M-` and three hex digits, spec/randomness.md, 3.1) are redrawn every seed, so a lesson tied to an ID is
useless in the next episode. In a digest, the firm itself is "you", and the others are "Buyer 1", "Buyer 2",
"Manager 1", ... in seat order, skipping the firm's own seat. Group recipients of messages ("managers", "buyers",
"everyone") keep their names.
"""

import re

ID = re.compile(r"\b[BM]-[0-9a-f]{3}\b")


class Anonymizer:
    def __init__(self, ep, pid):
        self.labels = {pid: "you"}
        for role, word in (("buyer", "Buyer"), ("manager", "Manager")):
            others = sorted((p for p in ep.participants.values() if p.role == role and p.id != pid),
                            key=lambda p: p.seat)
            for k, p in enumerate(others, 1):
                self.labels[p.id] = f"{word} {k}"

    def label(self, pid):
        return self.labels.get(pid, pid)

    def text(self, s):
        """Every participant ID in `s` replaced. An ID-shaped token of no participant becomes "someone", so no
        ID-shaped string survives."""
        if not s:
            return s
        return ID.sub(lambda m: self.labels.get(m[0], "someone"), s)
