"""Free episodes for the learning tools' tests: the market's own mock, and a "rich" mock that adds what the plain mock
never does (rejections, disputes, stated qualities, payments outside contracts, ended contracts, reasoning text
naming IDs, token charges). The rich mock exists only here, to exercise the reader and the digest; the market is
untouched."""

import hashlib
import re

import pytest
import yaml

from market import mock, run

SEEDS = [0, 1, 2]
CONTRACT = re.compile(r"\bcontract-\d+(?:-\d+)?\b")
PARTICIPANTS = re.compile(r"Participants - buyers: ([^;]+); managers: ([^.\n]+)")
REJECTED = re.compile(r"rejected (R-[0-9a-f]{3}) on (contract-\d+(?:-\d+)?)")
ID = re.compile(r"\b[BM]-[0-9a-f]{3}\b")
LABEL = re.compile(r"\bR-[0-9a-f]{3}\b")


def _h(view, i):
    return hashlib.sha256(view.encode("utf-8", "surrogatepass")).digest()[i]


def rich_actions(original):
    def actions(system, view):
        out = original(system, view)
        me = re.search(r"your ID is ([BM]-[0-9a-f]{3})", system)
        if not me:
            return out
        me = me[1]
        changed = []
        for a in out:
            a = dict(a)
            if a.get("action") == "approve_submission" and _h(view, 2) % 2 == 0:
                a = {"action": "reject_submission", "contract_id": a["contract_id"],
                     "message": f"Not good enough for {a['contract_id']}; {me} expected more."}
            if a.get("action") == "deliver":
                a["quality"] = 9
                a["message"] = f"Tested and fine. Quality 9 on {a['contract_id']}."
            if a.get("action") == "pay" and a.get("contract_id") and _h(a["contract_id"], 0) % 4 == 0:
                continue  # a buyer that never pays for a quarter of its contracts, the same ones every turn
            changed.append(a)
        if me.startswith("M-"):
            for label, cid in REJECTED.findall(view):
                changed.append({"action": "dispute", "contract_id": cid})
                break
        contracts = CONTRACT.findall(view)
        if me.startswith("B-") and contracts and _h(view, 3) % 7 == 0:
            changed.append({"action": "end_contract", "contract_id": contracts[0]})
        found = PARTICIPANTS.search(view)
        if found and _h(view, 4) % 13 == 0:
            others = [q.strip() for part in found.groups() for q in part.split(",") if q.strip() != me]
            if others:
                changed.append({"action": "pay", "to": others[0], "amount": 0.5})
        return changed

    return actions


def rich_call(original):
    def call(self, messages, functions=None, tool_choice=None):
        reply = original(self, messages, functions, tool_choice)
        view = next((m.get("content") or "" for m in messages if m.get("role") == "user"), "")
        ids = sorted(set(ID.findall(view)))
        contracts = sorted(set(CONTRACT.findall(view)))
        labels = sorted(set(LABEL.findall(view)))
        paragraphs = [f"Overview: I see {', '.join(ids)} in the market."]
        for cid in contracts:
            paragraphs.append(f"About {cid}: I should weigh what it is worth and whether to act on it now.")
        for label in labels[:3]:
            paragraphs.append(f"Result {label} needs a look before I decide.")
        paragraphs.append("Nothing else to consider this turn.")
        reply.reasoning = "\n\n".join(paragraphs)
        return reply

    return call


def write_config(folder, name, mode, prices):
    spec = {"provider": "mock", **({"price_in_per_m": 0.5, "price_out_per_m": 2.0} if prices else {})}
    path = folder / f"{name}.yaml"
    path.write_text(yaml.safe_dump({"name": name, "message_channels": mode, "seeds": SEEDS,
                                    "parallel_episodes": 3, "cost_cap_usd": 1,
                                    "models": {"buyers": spec, "managers": spec}}))
    return str(path)


@pytest.fixture(scope="session")
def episodes(tmp_path_factory):
    """name -> list of episode folders. 'plain_off' and 'plain_all' use the market's mock as it is; 'rich_off' and
    'rich_all' use the rich mock with token prices."""
    root = tmp_path_factory.mktemp("learning_runs")
    out = {}
    for name, mode, rich in (("plain_off", "off", False), ("plain_all", "all", False),
                             ("rich_off", "off", True), ("rich_all", "all", True)):
        with pytest.MonkeyPatch.context() as patch:
            if rich:
                patch.setattr(mock, "actions", rich_actions(mock.actions))
                patch.setattr(mock._Mock, "call", rich_call(mock._Mock.call))
            code = run.main([write_config(root, name, mode, rich), "--out", str(root)])
        assert code == 0, f"{name}: the market's command exited with {code}"
        out[name] = [root / name / f"seed_{s}" for s in SEEDS]
    return out
