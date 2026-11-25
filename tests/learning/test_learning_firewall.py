"""The information firewall: nothing a firm may not see reaches its data or its digest.

Each test plants sentinel values in the fields a firm must never see, then checks every firm's data and digest."""

from learning.anonymize import ID
from learning.digest import build_digest
from learning.firm import FORBIDDEN_FIELDS, firm_data
from learning.logs import load_episode

QUALITY = 1234.5678  # out of the 0-10 range: never a natural value
QUALITY_TEXTS = ("1234.57", "1234.6", "1234.568", "1234.5678", "1235")
OTHER_SCORE = 98765.43
LAST_ARRIVAL = 987654  # far above any day, record number or amount


def planted(folder, pid):
    """The episode with sentinels in everything the firm `pid` must not see."""
    ep = load_episode(folder)
    role = ep.participants[pid].role
    for kind in ("delivery", "run", "arrival"):
        for r in ep.of(kind):
            r["quality"] = QUALITY
    for r in ep.of("run"):
        r["z_q"] = r["z_t"] = r["z_c"] = QUALITY
    for r in ep.of("reading") + ep.of("ruling"):
        r["z"] = QUALITY
    for row in ep.summary.get("settlement") or []:
        row["settled_q"] = QUALITY
        if role == "manager":
            row["realized_value"] = QUALITY
    for row in ep.summary.get("scores") or []:
        if row["id"] != pid:
            row["score"] = OTHER_SCORE
    ep.summary["last_arrival_day"] = LAST_ARRIVAL
    ep.summary["close_reason"] = "SENTINEL-CLOSE"
    for r in ep.of("fresh_start"):
        r["last_arrival_day"] = LAST_ARRIVAL
    for r in ep.of("call"):
        if r["participant"] != pid:
            r["reply"]["reasoning"] = "SENTINEL-REASONING of another firm"
            r["reply"]["text"] = "SENTINEL-TEXT of another firm"
    for r in ep.of("memory"):
        if r["participant"] != pid:
            r["text"] = "SENTINEL-MEMORY of another firm"
    for r in ep.of("notification"):
        if r["to"] != pid:
            r["text"] = "SENTINEL-NOTIFICATION to another firm"
    for r in ep.of("message"):
        to = r.get("to")
        if not (r["sender"] == pid or to == pid or to == role + "s" or to == "everyone"):
            r["text"] = "SENTINEL-MESSAGE not to this firm"
    for r in ep.of("reading"):
        if r["side"] != role:
            r["reading"] = QUALITY
    return ep


def _walk(x, inside_arguments=False):
    """(key, value, whether it is inside an action's arguments), for every field at any depth. An action's
    arguments are what the agent itself wrote (a stated quality is one), so their keys are not market fields."""
    if isinstance(x, dict):
        for k, v in x.items():
            yield k, v, inside_arguments
            yield from _walk(v, inside_arguments or k == "arguments")
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _walk(v, inside_arguments)
    elif hasattr(x, "__dict__"):
        yield from _walk(vars(x), inside_arguments)


def every_case(episodes):
    for folders in episodes.values():
        for folder in folders:
            ep = load_episode(folder)
            for pid in ep.participants:
                yield folder, pid


def test_no_forbidden_field_and_no_sentinel_in_any_firm_data(episodes):
    for folder, pid in every_case(episodes):
        fd = firm_data(planted(folder, pid), pid)
        for key, value, agents_own in _walk(fd):
            assert agents_own or key not in FORBIDDEN_FIELDS, (folder, pid, key)
            assert value != QUALITY, (folder, pid, key)
            assert value != OTHER_SCORE, (folder, pid, key)
            assert value != LAST_ARRIVAL, (folder, pid, key)
            if isinstance(value, str):
                assert "SENTINEL" not in value, (folder, pid, key, value[:80])


def test_no_sentinel_in_any_digest(episodes):
    for folder, pid in every_case(episodes):
        text = build_digest(planted(folder, pid), pid)
        assert "SENTINEL" not in text, (folder, pid)
        for q in QUALITY_TEXTS:
            assert q not in text, (folder, pid, q)
        assert "98765" not in text, (folder, pid)
        assert str(LAST_ARRIVAL) not in text, (folder, pid)


def test_a_buyer_sees_its_realised_values_and_a_manager_does_not(episodes):
    folder = episodes["rich_off"][0]
    ep = load_episode(folder)
    for pid, p in ep.participants.items():
        fd = firm_data(ep, pid)
        has = any("realized_value" in s for s in fd.settlement.values())
        assert has == (p.role == "buyer"), pid


def test_no_participant_id_survives_in_any_digest(episodes):
    for folder, pid in every_case(episodes):
        text = build_digest(load_episode(folder), pid)
        assert not ID.search(text), (folder, pid, ID.search(text)[0])
