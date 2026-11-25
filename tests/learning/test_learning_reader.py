"""The log reader and the money by contract, on free episodes (conftest.py)."""

import pytest

from learning.firm import contract_profit, firm_data, money_by_kind, reconcile
from learning.logs import load_episode


def all_episodes(episodes):
    return [(name, folder) for name, folders in episodes.items() for folder in folders]


def test_every_episode_loads_with_no_unknown_record_type(episodes):
    for name, folder in all_episodes(episodes):
        ep = load_episode(folder)
        assert ep.status == "ok", (name, folder)
        assert not ep.unknown_types, (name, ep.unknown_types)
        assert len(ep.participants) == 5


def test_every_firm_score_rebuilds_from_its_money_and_from_its_contracts(episodes):
    """The score in summary.json equals (a) the firm's money plus, for a buyer, the value obtained, and (b) the sum of
    its contract profits plus what no contract carries (tokens, payments outside its contracts)."""
    for name, folder in all_episodes(episodes):
        ep = load_episode(folder)
        for pid in ep.participants:
            score, rebuilt, profits, outside = reconcile(ep, firm_data(ep, pid))
            assert rebuilt == pytest.approx(score, abs=0.006), (name, folder, pid, "money")
            assert profits + outside == pytest.approx(score, abs=0.006), (name, folder, pid, "contracts")


def test_the_rich_mock_reaches_the_cases_the_reader_must_handle(episodes):
    """Without these the reconciliation above would not test them."""
    seen = set()
    for folder in episodes["rich_off"] + episodes["rich_all"]:
        ep = load_episode(folder)
        if any(r.get("decision") == "rejected" for r in ep.of("decision")):
            seen.add("rejection")
        if ep.of("dispute"):
            seen.add("dispute")
        if ep.of("ruling"):
            seen.add("ruling")
        if any(r.get("stated_quality") is not None for r in ep.of("delivery")):
            seen.add("stated quality")
        if any(r.get("contract") is None for r in ep.of("payment")):
            seen.add("payment outside contracts")
        if ep.of("end"):
            seen.add("ended contract")
        if any(r.get("kind") == "tokens" for r in ep.of("money")):
            seen.add("token charges")
        if any((r.get("reply") or {}).get("reasoning") for r in ep.of("call")):
            seen.add("reasoning")
        if ep.of("message"):
            seen.add("messages")
    assert seen >= {"rejection", "dispute", "ruling", "stated quality", "payment outside contracts",
                    "token charges", "reasoning", "messages"}, seen


def test_each_contract_belongs_to_two_firms_whose_profits_sum_to_its_value_less_its_costs(episodes):
    """Across both parties a contract's money nets out: buyer profit + manager profit = value - every cost on it."""
    for name, folder in all_episodes(episodes):
        ep = load_episode(folder)
        fds = {pid: firm_data(ep, pid) for pid in ep.participants}
        cost = ep.config["tests"]
        for cid, c in ep.contracts.items():
            b, m = fds[c["buyer"]], fds[c["manager"]]
            s = ep.settlement[cid]
            runs = sum(r["cost"] for r in ep.of("run") if r["contract"] == cid)
            tests = sum(cost[f"{r['side']}_cost"] for r in ep.of("reading") if r["contract"] == cid)
            lost = sum(r["deposit"] for r in ep.of("ruling") if r["contract"] == cid and r["deposit_fate"] == "lost")
            arbiter = sum(r.get("fee", 0) for r in ep.of("fee_paid") if r["contract"] == cid)
            arbiter += sum(r.get("fee_charged", 0) for r in ep.of("ruling") if r["contract"] == cid)
            expected = (s.get("realized_value") or 0.0) - runs - tests - lost - s["setup_fee"] - arbiter
            total = contract_profit(ep, b, cid) + contract_profit(ep, m, cid)
            assert total == pytest.approx(expected, abs=1e-6), (name, cid)


def test_money_by_kind_leaves_out_records_marked_held(episodes):
    for folder in episodes["rich_off"]:
        ep = load_episode(folder)
        for pid in ep.participants:
            kinds = money_by_kind(firm_data(ep, pid))
            assert "dispute deposit lost" not in kinds
