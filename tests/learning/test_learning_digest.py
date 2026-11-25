"""The digest: deterministic, complete where it must be, within its size, and selecting by fixed rules."""

from dataclasses import replace

from learning.digest import DigestConfig, _even, build_digest
from learning.firm import contract_profit, firm_data
from learning.logs import load_episode


def cases(episodes, names=None):
    for name, folders in episodes.items():
        if names and name not in names:
            continue
        for folder in folders:
            ep = load_episode(folder)
            for pid in ep.participants:
                yield name, folder, pid


def test_the_same_episode_gives_the_same_digest_byte_for_byte(episodes):
    for _, folder, pid in cases(episodes):
        assert build_digest(load_episode(folder), pid) == build_digest(load_episode(folder), pid)


def test_every_contract_of_the_firm_has_an_entry_with_its_profit(episodes):
    for _, folder, pid in cases(episodes):
        ep = load_episode(folder)
        fd = firm_data(ep, pid)
        text = build_digest(ep, pid, DigestConfig(max_chars=10**9))
        for cid in fd.contracts:
            assert f"- {cid} with " in text, (folder, pid, cid)
            assert f"your profit on it {contract_profit(ep, fd, cid):.2f}" in text, (folder, pid, cid)


def test_the_digest_holds_its_sections_in_order(episodes):
    for name, folder, pid in cases(episodes):
        text = build_digest(load_episode(folder), pid)
        heads = ["== OUTCOME ==", "== CONTRACTS ==", "== MEMORY ==", "== REASONING ABOUT YOUR BEST AND WORST"]
        positions = [text.find(h) for h in heads]
        assert all(p >= 0 for p in positions) and positions == sorted(positions), (name, folder, pid)
        assert ("== MESSAGES ==" in text) == name.endswith("_all"), (name, folder, pid)


def test_reasoning_is_shown_for_the_best_and_worst_contracts_only(episodes):
    for _, folder, pid in cases(episodes, {"rich_off", "rich_all"}):
        ep = load_episode(folder)
        fd = firm_data(ep, pid)
        if not fd.contracts:
            continue
        text = build_digest(ep, pid, DigestConfig(max_chars=10**9))
        section = text.split("== REASONING ABOUT YOUR BEST AND WORST CONTRACTS ==")[1].split("== MESSAGES ==")[0]
        profits = {cid: contract_profit(ep, fd, cid) for cid in fd.contracts}
        ranked = sorted(fd.contracts, key=lambda c: (-profits[c], [int(x) for x in c.split("-")[1:]]))
        chosen = ranked if len(ranked) <= 4 else ranked[:2] + ranked[-2:]
        for cid in fd.contracts:
            assert (f"-- {cid} (your profit" in section) == (cid in chosen), (folder, pid, cid)


def test_excerpts_hold_only_paragraphs_about_their_contract(episodes):
    """The rich mock writes one paragraph per contract it sees ("About contract-N: ..."), an overview and a closing
    line. An excerpt for contract-N may hold its own paragraph and lines naming its results, nothing else."""
    for _, folder, pid in cases(episodes, {"rich_off"}):
        text = build_digest(load_episode(folder), pid, DigestConfig(max_chars=10**9))
        section = text.split("== REASONING ABOUT YOUR BEST AND WORST CONTRACTS ==")[1]
        current = None
        for line in section.split("\n"):
            if line.startswith("-- contract-"):
                current = line.split()[1]
            elif line.strip().startswith("About contract-"):
                assert line.strip().startswith(f"About {current}:"), (folder, pid, current, line)
            assert "Overview:" not in line and "Nothing else to consider" not in line, (folder, pid, line)


def test_the_digest_stays_within_its_limit_and_trims_reasoning_first(episodes):
    for _, folder, pid in cases(episodes):
        ep = load_episode(folder)
        full = build_digest(ep, pid, DigestConfig(max_chars=10**9))
        limit = max(len(full) // 2, 3000)
        cut = build_digest(ep, pid, DigestConfig(max_chars=limit))
        head = full.split("== MEMORY ==")[0]
        assert cut.startswith(head), (folder, pid)  # outcome and contracts are never cut
        if len(head) < limit:
            assert len(cut) <= limit, (folder, pid, len(cut), limit)


def test_default_digests_fit_the_default_limit(episodes):
    for _, folder, pid in cases(episodes):
        text = build_digest(load_episode(folder), pid)
        assert len(text) <= DigestConfig().max_chars, (folder, pid, len(text))


def test_even_spacing_keeps_both_ends():
    items = list(range(25))
    assert _even(items, 10)[0] == 0 and _even(items, 10)[-1] == 24 and len(_even(items, 10)) == 10
    assert _even(items, 30) == items
    assert _even(items, 0) == []
    assert _even([], 5) == []


def test_memory_points_are_evenly_spaced_writes(episodes):
    folder = episodes["rich_off"][0]
    ep = load_episode(folder)
    pid = next(iter(ep.participants))
    fd = firm_data(ep, pid)
    writes = fd.events.get("memory", [])
    text = build_digest(ep, pid, replace(DigestConfig(), max_chars=10**9))
    shown = [line for line in text.split("== MEMORY ==")[1].split("== REASONING")[0].split("\n")
             if line.startswith("- day ")]
    assert len(shown) == min(10, len(writes))


def test_the_welfare_digest_states_the_market_score_and_no_other_participants_own_score(episodes):
    from learning.objective import market_score
    for _, folder, pid in cases(episodes, {"rich_off"}):
        ep = load_episode(folder)
        own = build_digest(ep, pid)
        market = build_digest(ep, pid, DigestConfig(arm="welfare"))
        assert "market's score" not in own and "Your firm is judged on the market's score" in market
        m = market_score(ep)
        assert f"market score {m['score']:.2f}" in market
        for row in ep.summary["scores"]:
            if row["id"] != pid and f"{row['score']:.2f}" not in own:
                # another participant's own score appears only if it happens to equal a figure the firm sees
                assert f"{row['score']:.2f}" not in market.split("== CONTRACTS ==")[0], (folder, pid)
