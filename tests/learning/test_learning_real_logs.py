"""The reader, the firewall, the digest and the measures on real episodes.

Real logs are not in the repository. Set LEARNING_REAL_LOGS to a folder holding episode folders (any depth, each
with log.jsonl and summary.json) to run these tests; without it they are skipped."""

import glob
import os
import re

import pytest

from learning.anonymize import ID
from learning.digest import DigestConfig, _ident, _norm, _REASONING, build_digest
from learning.firm import firm_data, reconcile
from learning.logs import load_episode
from learning.measures import episode_measures

ROOT = os.environ.get("LEARNING_REAL_LOGS")
pytestmark = pytest.mark.skipif(not ROOT, reason="set LEARNING_REAL_LOGS to a folder of real episodes")


def folders():
    return sorted(os.path.dirname(p) for p in glob.glob(os.path.join(ROOT, "**", "log.jsonl"), recursive=True))


@pytest.fixture(scope="module")
def eps():
    return [load_episode(f) for f in folders()]


def test_real_logs_load_with_no_unknown_record_type(eps):
    assert eps, "no episodes found"
    for ep in eps:
        assert not ep.unknown_types, (ep.folder, ep.unknown_types)


def test_every_real_score_reconciles(eps):
    for ep in eps:
        for pid in ep.participants:
            score, rebuilt, profits, outside = reconcile(ep, firm_data(ep, pid))
            assert rebuilt == pytest.approx(score, abs=0.006), (ep.folder, pid)
            assert profits + outside == pytest.approx(score, abs=0.006), (ep.folder, pid)


def test_real_digests_fit_hold_no_ids_and_list_contracts_in_the_order_made(eps):
    for ep in eps:
        for pid in ep.participants:
            text = build_digest(ep, pid)
            assert len(text) <= DigestConfig().max_chars, (ep.folder, pid, len(text))
            assert not ID.search(text), (ep.folder, pid)
            ledger = text.split("== CONTRACTS ==")[1].split("== MEMORY ==")[0]
            listed = re.findall(r"^- (contract-\d+(?:-\d+)?) with ", ledger, re.M)
            made = [cid for cid in ep.contracts if pid in (ep.contracts[cid]["buyer"], ep.contracts[cid]["manager"])]
            assert listed == made, (ep.folder, pid)


def test_real_excerpts_are_deliberation_about_their_contract_and_never_repeat(eps):
    for ep in eps[:8]:
        for pid in ep.participants:
            fd = firm_data(ep, pid)
            text = build_digest(ep, pid, DigestConfig(max_chars=10**9))
            section = text.split("== REASONING ABOUT YOUR BEST AND WORST CONTRACTS ==")[1].split("== MESSAGES ==")[0]
            current, seen = None, set()
            for line in section.split("\n"):
                if line.startswith("-- contract-"):
                    current, seen = line.split()[1], set()
                    labels = {d["label"] for d in fd.events.get("delivery", []) if d["contract"] == current}
                    labels |= {r["label"] for r in fd.events.get("run", []) if r["contract"] == current}
                    c = fd.contracts[current]
                    patterns = [_ident(x) for x in {current, c["post"], c["task"], *labels}]
                elif current and line.startswith("  ") and not line.startswith("  [day ") and line.strip():
                    body = line.strip().removeprefix("...")
                    assert _REASONING.search(body), (ep.folder, pid, body[:80])
                    key = _norm(body)
                    assert key not in seen, (ep.folder, pid, body[:80])
                    seen.add(key)
                    # the line mentions the contract, unless it is the cut start of a long line ("...")
                    assert any(p.search(body) for p in patterns) or line.strip().startswith("..."), \
                        (ep.folder, pid, current, body[:80])


def test_every_real_episode_gets_every_measure(eps):
    for ep in eps:
        m = episode_measures(ep)
        assert m["status"] == "ok"
        assert m["unpaid_when_able"] <= m["plain_unpaid_seen"] <= m["plain_seen"]
        assert m["unpaid_when_able_value_above_price"] <= m["unpaid_when_able"]
        assert m["knowing_submissions"] <= m["escrow_submissions"]
