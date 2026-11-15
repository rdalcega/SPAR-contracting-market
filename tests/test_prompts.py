"""Tests of market/prompts.py against spec/prompts.md, its templates and its example prompts."""

import re
from pathlib import Path
from statistics import NormalDist

import pytest

from market import config, prompts

ROOT = Path(__file__).parent.parent
SPEC = (ROOT / "spec" / "prompts.md").read_text(encoding="utf-8")
MODES = ("off", "managers", "buyers", "within_roles", "all")
ROLES = ("buyer", "manager")
OTHER = {"buyer": "manager", "manager": "buyer"}
AGENTS = {"buyer": ("B-c53", 1.36), "manager": ("M-14b", 1.35)}
SENDERS = [(role, mode) for mode in MODES for role in ROLES if mode in ("within_roles", "all", role + "s")]
PLACEHOLDER = r"\{([a-z0-9_]+)\}"

# A: every parameter a system prompt states, away from its default.
A = {
    "message_channels": "all",
    "participants": {"buyers": 5, "managers": 12},
    "models": {
        "buyers": {"provider": "mock", "price_in_per_m": 0.123456789, "price_out_per_m": 1234.5678},
        "managers": {"provider": "mock", "price_in_per_m": 0.4, "price_out_per_m": 2.5},
        "manager_seats": {1: {"provider": "mock"}},
    },
    "credits_per_usd": 3,
    "time": {"rounds_per_day": 4, "close_after_due_days": 9},
    "turns": {"max_actions": 3, "memory_chars": 1500},
    "tasks": {"first_day_tasks": 3, "arrival_p": 0.4, "worth_range": [25, 120]},
    "money": {"start_amount": 250, "task_budget": 45, "share_range": [0.5, 2], "share_decimals": 1},
    "value": {"q0": 2.5, "exponent": 1.5, "late_decay": 0.125},
    "contracts": {
        "bid_window_rounds": 2,
        "min_contract_days": 6,
        "message_chars": 280,
        "escrow": {"fee_rate": 0.075, "review_days": 3, "dispute_window_days": 2, "dispute_fee_rate": 0.3333,
                   "arbiter_sd": 0.75},
    },
    "production": {
        "cost_ref_worth": 100,
        "sig_t": 0.18,
        "sig_c": 0.4,
        "employee": {"mu": 5, "sd": 1, "gain": 2.5, "sd_ref": 1.25, "t_med": 2.5, "t_ref_med": 1.0, "fee": 0.5,
                     "cost_frac": 0.05, "ref_cost_frac": 0.0275, "capacity": 3},
        "manager": {"mu": 10, "sd": 2, "gain": 3.333333333, "sd_ref": 0.6, "t_med": 0.4, "t_ref_med": 3.35,
                    "k": 0.125, "ref_cost_fixed": 9.999, "capacity": 2},
    },
    "tests": {"manager_sd": 0.25, "manager_cost": 2.5, "buyer_sd": 1.75, "buyer_cost": 2.5, "decimals": 2},
    "messaging": {"message_chars": 750, "recent": 4},
    "compute": {"cpu_s": 7, "wall_s": 30, "output_kb": 128, "output_chars": 6000, "code_chars": 15000},
}

# B: every configurable value that can be 1, at 1.
B = {
    "message_channels": "within_roles",
    "models": {
        "buyers": {"provider": "mock", "price_in_per_m": 1, "price_out_per_m": 0.9999999},
        "managers": {"provider": "mock"},
    },
    "time": {"rounds_per_day": 1, "close_after_due_days": 1},
    "turns": {"max_actions": 1, "memory_chars": 1},
    "tasks": {"arrival_p": 0.75, "worth_range": [1, 1]},
    "money": {"start_amount": 1, "task_budget": 1, "share_range": [1, 1], "share_decimals": 0},
    "value": {"q0": 0, "exponent": 1, "late_decay": 1},
    "contracts": {"min_contract_days": 1, "message_chars": 1,
                  "escrow": {"fee_rate": 0, "review_days": 1, "arbiter_sd": 0}},
    "production": {"cost_ref_worth": 100, "sig_t": 0,
                   "employee": {"fee": 0.5, "cost_frac": 0.00504, "ref_cost_frac": 0.005, "capacity": 1}},
    "tests": {"manager_cost": 0.9999999, "buyer_cost": 0.9999999, "decimals": 0},
    "messaging": {"message_chars": 1, "recent": 2},
    "compute": {"cpu_s": 1, "wall_s": 1, "output_kb": 1, "output_chars": 1, "code_chars": 1},
}


def cfg(raw=None, **top):
    return config.resolve({**(raw or {}), **top})


def render(c, role, seat=0):
    return prompts.system_prompt(c, role, *AGENTS[role], seat)


def fields(c, role, seat=0):
    return prompts.fields(c, role, *AGENTS[role], seat)


def block(marker):
    """The first fenced block of prompts.md after the marker."""
    start = SPEC.index("```\n", SPEC.index(marker)) + 4
    return SPEC[start:SPEC.index("\n```", start)]


def law(t, sig_t, subject="X"):
    c = cfg()
    c["production"]["sig_t"] = sig_t
    return prompts.duration_text(c, t, subject)


def closing_list(text):
    return text.split("\n\n")[-1].split("\n")


# --- Acceptance check 1 and section 8 ---


@pytest.mark.parametrize("mode", ["off", "all"])
@pytest.mark.parametrize("role", ROLES)
def test_example_prompts_byte_for_byte(role, mode):
    example = ROOT / "spec" / "examples" / f"system_prompt_{role}_{mode}_{AGENTS[role][0]}.txt"
    assert render(cfg(message_channels=mode), role).encode("ascii") == example.read_bytes()


def section_8_table():
    rows = re.findall(r"^\| `(\w+)` \| (.+?) \| (.+?) \|$", SPEC[SPEC.index("## 8. Worked example"):], re.M)
    assert len(rows) == 50 + 3 + 4 + 34
    return rows


@pytest.mark.parametrize("column, role", list(enumerate(ROLES)))
def test_section_8_field_values(column, role):
    got = fields(cfg(), role)
    stated = {name: cells[column] for name, *cells in section_8_table()}
    assert set(got) == {name for name, cell in stated.items() if cell != "not in the file"}
    literal = {name: cell[1:-1] for name, cell in stated.items() if cell.startswith("`")}
    assert len(literal) == len(got) - (2 if role == "buyer" else 6)
    assert {name: got[name] for name in literal} == literal


@pytest.mark.parametrize("role", ROLES)
def test_section_8_other_modes_change_only_n_sections_and_message_fields(role):
    off = fields(cfg(), role)
    for mode in MODES[1:]:
        got = fields(cfg(message_channels=mode), role)
        added = {}
        if (role, mode) in SENDERS:
            added = {"mm_chars_text": "500 characters", "mm_shown": block("`mm_shown`, where").replace("{k}", "10")}
        assert got == off | added | {"n_sections": "12" if added else "11"}
    assert "up to 10 of them. Whenever a conversation has more than 10 messages, the market replaces its earliest " \
           "messages, in groups of 10, with a summary" in fields(cfg(message_channels="all"), role)["mm_shown"]


def test_section_8_rendered_paragraphs():
    text = render(cfg(), "buyer")
    assert block("So the buyer's paragraph on its ID and balance renders as:") in text.split("\n\n")
    assert block("and its per-task budget sentence as:") in text


# --- Section 1: how a prompt is built ---


CONFIGS = {"default": {}, "A": A, "B": B}


@pytest.mark.parametrize("name", CONFIGS)
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("role", ROLES)
def test_every_placeholder_filled(role, mode, name):
    c = cfg(CONFIGS[name], message_channels=mode)
    template = (ROOT / "spec" / "text" / f"{role}_system_{mode}.txt").read_bytes().decode("ascii")
    text, values = render(c, role), fields(c, role)
    assert "{" not in text and "}" not in text
    assert text.isascii() and not text.endswith("\n")
    assert set(values) == set(re.findall(PLACEHOLDER, template))
    assert all(isinstance(v, str) and v and "\n" not in v for v in values.values())
    parts = re.split(PLACEHOLDER, template)
    assert text == "".join(values[part] if i % 2 else part for i, part in enumerate(parts))


def test_replacement_is_a_single_pass():
    text = prompts.system_prompt(cfg(), "buyer", "{share}", 1.36, 0)
    assert "your ID is {share} and your wealth share is 1.36." in text


def test_template_and_fields_must_agree(monkeypatch):
    text = prompts.template("buyer", "off")
    monkeypatch.setattr(prompts, "template", lambda role, mode: text + " {nonsense}")
    with pytest.raises(ValueError, match="nonsense"):
        render(cfg(), "buyer")
    monkeypatch.setattr(prompts, "template", lambda role, mode: text.replace("{budget}", "40.80"))
    with pytest.raises(ValueError, match="budget"):
        render(cfg(), "buyer")


def test_prices_are_the_seat_s_model_spec():
    c = cfg(A)
    assert (fields(c, "manager", 0)["price_in_text"], fields(c, "manager", 0)["price_out_text"]) == \
        ("1.2 credits", "7.5 credits")
    assert (fields(c, "manager", 1)["price_in_text"], fields(c, "manager", 1)["price_out_text"]) == \
        ("0 credits", "0 credits")
    assert fields(c, "manager", 2)["price_in_text"] == "1.2 credits"
    assert "every million tokens you read cost you about 0 credits and every million tokens you write " \
           "(including any hidden reasoning/thinking) cost you about 0 credits," in render(c, "manager", 1)


@pytest.mark.parametrize("key", ["price_in_per_m", "price_out_per_m"])
@pytest.mark.parametrize("role", ROLES)
def test_a_token_price_that_is_not_finite_is_never_shown(role, key):
    """config.md 6, "Finiteness during an episode": an infinite amount stops the episode, naming the value."""
    spec = {"provider": "mock", "price_in_per_m": 1.0, "price_out_per_m": 1.0, key: 1e308}
    c = cfg(credits_per_usd=2, models={role + "s": spec})
    for build in (prompts.fields, prompts.system_prompt):
        with pytest.raises(ArithmeticError, match=key):
            build(c, role, *AGENTS[role], 0)
    c = cfg(credits_per_usd=1.5, models={role + "s": spec})  # a product that stays finite is stated
    assert fields(c, role)[key.replace("_per_m", "_text")] == "1.5e+308 credits"


# --- Section 5: what each mode changes ---


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("role", ROLES)
def test_n_sections_by_file(role, mode):
    row = re.search(rf"^\| `{mode}` \| (\d+) \| (\d+) \|$", SPEC, re.M)
    n = row[1 + ROLES.index(role)]
    assert n == ("12" if (role, mode) in SENDERS else "11")
    text = render(cfg(message_channels=mode), role)
    assert f"The rest of the view is divided into {n} sections, " in text
    items = closing_list(text)
    assert [item.split(". ")[0] for item in items] == [str(i) for i in range(1, int(n) + 1)]
    assert items[-1] == f"{n}. Final instructions."


@pytest.mark.parametrize("role, mode", SENDERS)
def test_a_role_that_can_send(role, mode):
    other = OTHER[role]
    messaging = {"message_chars": 750, "recent": 4}
    text = render(cfg(message_channels=mode, messaging=messaging), role)
    off = render(cfg(messaging=messaging), role)
    # send_message at the end of the list of action names
    assert f"The actions {role}s can take are called " in text
    assert ", and pay." in off and "send_message" not in off
    assert ", pay, and send_message." in text and ", and pay." not in text
    # its entry after the last action entry
    n = 12 if role == "buyer" else 9
    paragraphs = text.split("\n\n")
    (i,) = [i for i, p in enumerate(paragraphs) if p.startswith(f"{n}. Name: send_message(")]
    assert paragraphs[i - 1].startswith(f"{n - 1}. Name: pay(")
    assert paragraphs[i + 1].startswith(f"Note that {other}s have other actions")
    assert not any(p.startswith(f"{n + 1}. Name:") for p in paragraphs)
    entry = paragraphs[i]
    assert entry.count("750 characters") == 2 == text.count("750 characters")
    shown = block("`mm_shown`, where").replace("{k}", "4")
    assert entry.count(shown) == 1 == text.count(shown)
    own_id, other_id = ('"B-7x4"', '"M-x43"') if role == "buyer" else ('"M-x43"', '"B-7x4"')
    if mode == "all":
        assert "sends a private message to another participant (buyer or manager), " \
               f"to all other {role}s at once, or to all other participants at once." in entry
        assert f'e.g. {other_id} or {own_id}; "{role}s" to send it' in entry and '"everyone"' in entry
    else:
        assert f"sends a private message to another {role}, or to all other {role}s at once." in entry
        assert f'e.g. {own_id}, or "{role}s" to send it' in entry
        assert other_id not in entry and '"everyone"' not in entry
    # item 4 of the closing list, the later items renumbered
    items, off_items = closing_list(text), closing_list(off)
    assert items[3] == ("4. Messages." if mode == "all" else f"4. Messages between {role}s.")
    assert len(items) == 12 and len(off_items) == 11
    assert [item.split(". ", 1)[1] for item in items[:3] + items[4:]] == [x.split(". ", 1)[1] for x in off_items]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("role", ROLES)
def test_the_note_before_the_action_entries(role, mode):
    other = OTHER[role]
    note = f'(note that "pay" is also available to {other}s)'
    if mode == "within_roles":
        note = note[:-1] + f' and that {other}s have their own version of "send_message" that they can use to ' \
                           "message each other)"
    if mode == "all":
        note = f'(note that "pay" and "send_message" are also available to {other}s)'
    text = render(cfg(message_channels=mode), role)
    assert text.count("(note that") == 1 and note + ":\n\n1. Name: " in text


@pytest.mark.parametrize("role, mode", [("buyer", "managers"), ("manager", "buyers")])
def test_a_role_that_cannot_send_while_the_other_can(role, mode):
    other = OTHER[role]
    text, off = render(cfg(message_channels=mode), role), render(cfg(), role)
    sentence = f"Note that {other}s have other actions that are not available to {role}s."
    head, tail = off.split(sentence)
    added = f" One of them lets {other}s send each other private messages, to one other {other} or to all other " \
            f"{other}s at once. {role.capitalize()}s can't read these messages or send any messages to each other."
    assert text == head + sentence + added + tail
    assert "send_message" not in text and len(closing_list(text)) == 11
    assert not {"mm_chars_text", "mm_shown"} & set(fields(cfg(message_channels=mode), role))


@pytest.mark.parametrize("role", ROLES)
def test_off_mentions_no_messages_between_participants(role):
    text = render(cfg(), role)
    assert "send_message" not in text and "private message" not in text and "conversation" not in text
    assert len(closing_list(text)) == 11 and not any("Messages" in item for item in closing_list(text))


@pytest.mark.parametrize("mode", ["off", "within_roles", "all"])
@pytest.mark.parametrize("role", ROLES)
def test_the_other_actions_sentence_gets_no_addition(role, mode):
    sentence = f"Note that {OTHER[role]}s have other actions that are not available to {role}s."
    assert f"\n\n{sentence}\n\n" in render(cfg(message_channels=mode), role)


# --- Section 4: the computations, against the spec's own numbers ---


def test_4_1_escrow_close_days():
    got = fields(cfg(), "buyer")
    assert (got["escrow_close_text"], got["cap_days_text"]) == ("5 days", "4 days")
    # ceil(1 / late_decay - 1e-9): 1, 2, ceil(3.33), 4, 10 (not 11), ceil(33.33)
    for late_decay, close, cap in ((1, "1 day", "0 days"), (0.5, "2 days", "1 day"), (0.3, "4 days", "3 days"),
                                   (0.25, "4 days", "3 days"), (0.1, "10 days", "9 days"),
                                   (0.03, "34 days", "33 days")):
        got = fields(cfg(value={"late_decay": late_decay}, time={"close_after_due_days": 40}), "manager")
        assert (got["escrow_close_text"], got["cap_days_text"]) == (close, cap)


def test_4_2_value_list():
    assert prompts.value_list(cfg()) == block("### 4.2 value_list")
    assert fields(cfg(), "manager")["value_list"] == block("### 4.2 value_list")
    # q0 0, exponent 1: f(q) = q / 10
    assert prompts.value_list(cfg(value={"q0": 0, "exponent": 1})) == \
        ", ".join(f"{q} -> {q}0.0%" for q in range(1, 11))
    # q0 4, exponent 0.5: f(q) = sqrt((q - 4) / 6): 0.408248, 0.577350, 0.707107, 0.816497, 0.912871, 1
    assert prompts.value_list(cfg(value={"q0": 4, "exponent": 0.5})) == \
        "1 -> 0.0%, 2 -> 0.0%, 3 -> 0.0%, 4 -> 0.0%, 5 -> 40.8%, 6 -> 57.7%, 7 -> 70.7%, 8 -> 81.6%, 9 -> 91.3%, " \
        "10 -> 100.0%"


@pytest.mark.parametrize("prefix, mu, sd, mean, reach", [
    ("emp", 6.0, 2.5, 5.7638, ["64%", "48%", "31%", "17%", "6%"]),
    ("own", 7.0, 1.2, 6.9788, ["95%", "80%", "50%", "20%", "4%"]),
])
def test_4_3_fresh_run_figures(prefix, mu, sd, mean, reach):
    got = fields(cfg(), "manager")
    normal = NormalDist()
    a, b = (0 - mu) / sd, (10 - mu) / sd
    exact = mu + sd * (normal.pdf(a) - normal.pdf(b)) / (normal.cdf(b) - normal.cdf(a))
    assert round(exact, 4) == mean
    assert got[f"{prefix}_mean"] == f"{exact:.1f}"
    assert [got[f"{prefix}_reach{q}"] for q in range(5, 10)] == reach


def test_4_3_and_3_3_expected_costs_and_spreads():
    got = fields(cfg(), "manager")
    assert [got[f"emp_{k}"] for k in ("fresh_cost", "fresh_spread", "ref_cost", "ref_spread")] == \
        ["4 credits", "19%", "2.8 credits", "16%"]
    assert [got[f"own_{k}"] for k in ("fresh_cost", "fresh_spread", "ref_cost", "ref_spread")] == \
        ["8 credits", "25%", "14 credits", "25%"]
    # PCT0 rounds a tie to even: the manager's spread is sig_c itself, 0.125 gives 12%
    assert fields(cfg(production={"sig_c": 0.125}), "manager")["own_fresh_spread"] == "12%"


def test_4_4_default_duration_texts():
    got = fields(cfg(), "manager")
    for name in ("emp_fresh_days", "emp_ref_days", "own_fresh_days", "own_ref_days"):
        assert got[name] == block(f"`{name}`:")


def test_4_4_split_example():
    c = cfg(production={"sig_t": 0.05})
    assert fields(c, "manager")["own_fresh_days"] == block("The split case")


def test_4_4_sum_example():
    c = cfg(production={"sig_t": 1.5, "employee": {"t_med": 5.09489020404268}})
    assert fields(c, "manager")["emp_fresh_days"].endswith(
        ", and with probability 50%, employee fresh runs take 6 or more days")
    # shares 1: 0.138852, 2: 0.127661, 3: 0.095500, 4: 0.073917, 5: 0.059070, then 0.505 in all: floors
    # 13, 12, 9, 7, 5, 50 leave 4 points, to the remainders .91 (5 days), .89 (1), .77 (2) and .55 (3)
    assert law(5.09489020404268, 1.5) == \
        "With probability 14%, X take 1 day, with probability 13%, X take 2 days, with probability 10%, X take 3 " \
        "days, with probability 7%, X take 4 days, with probability 6%, X take 5 days, and with probability 50%, X " \
        "take 6 or more days"


@pytest.mark.parametrize("R, W", [(3, 1), (2, 1), (4, 2), (1, 1)])
def test_4_5_grace_examples_of_the_spec(R, W):
    marker = "Default (R = 3, W = 1):" if (R, W) == (3, 1) else f"\nR = {R}, W = {W}:"
    c = cfg(time={"rounds_per_day": R}, contracts={"bid_window_rounds": W})
    assert prompts.grace_examples(c) == block(marker) == fields(c, "buyer")["grace_examples"]


def test_4_5_examples_of_the_spec():
    got = fields(cfg(tasks={"arrival_p": 0.4}), "buyer")
    assert [got[k] for k in ("arrival_law", "prob0", "prob1", "prob2")] == \
        ["(3/5) x (2/5)^n", "3/5", "6/25", "12/125"]
    c = cfg(contracts={"escrow": {"dispute_window_days": 2}}, production={"manager": {"capacity": 2}})
    got = fields(c, "manager")
    assert got["dispute_window_b"] == "throughout the day of the buyer's rejection and the 2 days after"
    assert got["dispute_until"] == "the day 2 days after it"
    assert got["own_busy"] == "2 runs"


# --- Sections 4.4 and 4.5 at other parameters, worked by hand from the rules ---


@pytest.mark.parametrize("t, sig_t, text", [
    # sig_t 0: the whole share on ceil(t); below is 0, so no "or fewer"
    (2.5, 0, "With probability 100%, X take 3 days"),
    (1.0, 0, "With probability 100%, X take 1 day"),
    (0.7, 0, "With probability 100%, X take 1 day"),
    # 1: 2.5e-16 (a positive double only with the accurate Phi), 2: 1 - 4.4e-9, 3: 4.4e-9: low; high below 1e-4
    # is added to the last bucket without a name of its own
    (1.5, 0.05, "With probability 100%, X take 2 or fewer days"),
    # 1: 0.9999998, 2: 0.0000002: not low; high below 1e-4
    (0.4, 0.18, "With probability 100%, X take 1 day"),
    # 1: 0.995577, then 0.004423 in all: up (1e-4 <= high < 0.005) without low, so no split
    (0.4, 0.35, "With probability 100%, X take 1 or more days"),
    # 1: 0.5, 2: 0.4999411, then 0.0000589: 50 and 49.99..., the missing point to the second bucket
    (1.0, 0.18, "With probability 50%, X take 1 day, and with probability 50%, X take 2 days"),
    # 1: 0.993405, then 0.006595 (tail): floors 99 and 0, the missing point to the tail's remainder .66
    (0.42, 0.35, "With probability 99%, X take 1 day, and with probability 1%, X take 2 or more days"),
    # 1: 0.478097, 2: 0.0138 (not listed), ..., 1000: 0.3858: high = 0.521903 with d = 1000 in it; 47.8 and 52.2
    (3.0, 20, "With probability 48%, X take 1 day, and with probability 52%, X take 2 or more days"),
    # 1: 0.0000002, 2: 0.1075455, 3: 0.7368997, 4: 0.1510424, then 0.0045122: low and up; buckets 0.1075457,
    # 0.7368997, 0.1555546: floors 10, 73, 15 leave 2 points, to .75 and .69
    (2.5, 0.18, "With probability 11%, X take 2 or fewer days, with probability 74%, X take 3 days, and with "
                "probability 15%, X take 4 or more days"),
    # 2: 0.0020809, 3: 0.2678425, 4: 0.5678110, 5: 0.1492208, then 0.0130447 (tail): buckets 0.2699234, 0.5678110,
    # 0.1492208, 0.0130447: floors 26, 56, 14, 1 leave 3 points, to .99, .92 and .78
    (3.35, 0.18, "With probability 27%, X take 3 or fewer days, with probability 57%, X take 4 days, with "
                 "probability 15%, X take 5 days, and with probability 1%, X take 6 or more days"),
    # 3: 0.0574667 (with 8.6e-14 before it: low), 4: 0.9368843, 5: 0.0056490 (tail): floors 5, 93, 0 leave 2
    # points, to .75 and .69; the tail has 0, so it gets 1 from the largest, 94
    (3.35, 0.07, "With probability 6%, X take 3 or fewer days, with probability 93%, X take 4 days, and with "
                 "probability 1%, X take 5 or more days"),
    # 6 or fewer: 0.136469, 7: 0.660421, 8: 0.196895, tail 0.006215: floors 13, 66, 19, 0 leave 2 points, to .69
    # (8 days) and .65 (6 or fewer); the tail's 1 comes from the 66
    (6.55, 0.08, "With probability 14%, X take 6 or fewer days, with probability 65%, X take 7 days, with "
                 "probability 20%, X take 8 days, and with probability 1%, X take 9 or more days"),
    # 1: 2.6e-75, 2: 0.000004, 3: 0.999863, 4: 0.000133: low, up and first = last: split
    (2.5, 0.05, "With probability more than 99%, X take 3 or fewer days, and with probability less than 1%, X take "
                "4 or more days"),
])
def test_duration_text_by_hand(t, sig_t, text):
    assert law(t, sig_t) == text


def test_duration_text_subject_words():
    got = fields(cfg(production={"sig_t": 0}), "manager")
    assert got["emp_fresh_days"] == "With probability 100%, employee fresh runs take 1 day"
    assert got["emp_ref_days"] == "With probability 100%, employee refinements take 1 day"
    assert got["own_fresh_days"] == "With probability 100%, manager fresh runs take 3 days"
    assert got["own_ref_days"] == "With probability 100%, manager refinements take 1 day"


@pytest.mark.parametrize("R, W, text", [
    # R 2: positions 2..4 are round 2 of day 0 and rounds 1, 2 of day 1; 3..5 are day 1 and round 1 of day 2;
    # both clauses wrap, so no "but"; acceptance at positions 5 and 6, both on day 2
    (2, 3, "Specifically, if a buyer posts a task in round 1 of a day, the grace period is round 2 of that day and "
           "rounds 1 and 2 of the next day; if a buyer posts a task in round 2 of a day, the grace period is rounds "
           "1 and 2 of the next day and round 1 of the day 2 days later. So bids on a post made in round 1 can "
           "first be accepted in round 1 of the day 2 days later, and on a post made in round 2 in round 2 of the "
           "day 2 days later."),
    # R 5: spans of three rounds read "a to b"; clause 3 is the first to wrap
    (5, 3, "Specifically, if a buyer posts a task in round 1 of a day, the grace period is rounds 2 to 4 of that "
           "day; if a buyer posts a task in round 2 of a day, the grace period is rounds 3 to 5 of that day; but if "
           "a buyer posts a task in round 3 of a day, the grace period is rounds 4 and 5 of that day and round 1 of "
           "the next day; if a buyer posts a task in round 4 of a day, the grace period is round 5 of that day and "
           "rounds 1 and 2 of the next day; if a buyer posts a task in round 5 of a day, the grace period is rounds "
           "1 to 3 of the next day. So bids on a post made in round 1 can first be accepted in round 5 of that "
           "day, on a post made in round 2 in round 1 of the next day, on a post made in round 3 in round 2 of the "
           "next day, on a post made in round 4 in round 3 of the next day, and on a post made in round 5 in round "
           "4 of the next day."),
    # R 1: three groups, joined by ", " and " and "
    (1, 3, "Specifically, if a buyer posts a task in round 1 of a day, the grace period is round 1 of the next day, "
           "round 1 of the day 2 days later and round 1 of the day 3 days later. So bids on a post made in round 1 "
           "can first be accepted in round 1 of the day 4 days later."),
    # R 3, W 7: a whole day in the middle; every clause wraps
    (3, 7, "Specifically, if a buyer posts a task in round 1 of a day, the grace period is rounds 2 and 3 of that "
           "day, rounds 1 to 3 of the next day and rounds 1 and 2 of the day 2 days later; if a buyer posts a task "
           "in round 2 of a day, the grace period is round 3 of that day, rounds 1 to 3 of the next day and rounds "
           "1 to 3 of the day 2 days later; if a buyer posts a task in round 3 of a day, the grace period is rounds "
           "1 to 3 of the next day, rounds 1 to 3 of the day 2 days later and round 1 of the day 3 days later. So "
           "bids on a post made in round 1 can first be accepted in round 3 of the day 2 days later, on a post made "
           "in round 2 in round 1 of the day 3 days later, and on a post made in round 3 in round 2 of the day 3 "
           "days later."),
])
def test_grace_examples_by_hand(R, W, text):
    assert prompts.grace_examples(cfg(time={"rounds_per_day": R}, contracts={"bid_window_rounds": W})) == text


@pytest.mark.parametrize("W, text", [(1, "one round"), (2, "two rounds"), (3, "three rounds"), (9, "nine rounds"),
                                     (10, "ten rounds"), (11, "11 rounds"), (25, "25 rounds")])
def test_grace_length(W, text):
    assert fields(cfg(contracts={"bid_window_rounds": W}), "manager")["grace_length"] == text


@pytest.mark.parametrize("p, law_text, probs", [
    (0.5, "(1/2)^(n+1)", ["1/2", "1/4", "1/8"]),
    (0.75, "(1/4) x (3/4)^n", ["1/4", "3/16", "9/64"]),
    (1 / 3, "(2/3) x (1/3)^n", ["2/3", "2/9", "2/27"]),
    (0.01, "(99/100) x (1/100)^n", ["99/100", "99/10000", "99/1000000"]),
    # section 7's edge case: prob2 misses 13778/614125 near the tolerance's edge, and gives it within 1e-13
    (83 / 85 + 9.9e-13, "(2/85) x (83/85)^n", ["2/85", "166/7225", "21113/941067"]),
    (83 / 85 + 1e-13, "(2/85) x (83/85)^n", ["2/85", "166/7225", "13778/614125"]),
])
def test_arrival_law_and_fractions(p, law_text, probs):
    got = fields(cfg(tasks={"arrival_p": p}), "buyer")
    assert got["arrival_law"] == law_text
    assert [got["prob0"], got["prob1"], got["prob2"]] == probs


def test_dispute_window_and_own_busy():
    c = cfg(contracts={"escrow": {"dispute_window_days": 3}}, production={"manager": {"capacity": 7}})
    got = fields(c, "manager")
    assert got["dispute_window_b"] == "throughout the day of the buyer's rejection and the 3 days after"
    assert got["dispute_window_m"] == "throughout the day of the rejection and the 3 days after"
    assert got["dispute_until"] == "the day 3 days after it"
    assert (got["own_busy"], got["own_runs"]) == ("7 runs", "7 runs")
    got = fields(cfg(), "manager")
    assert got["dispute_window_m"] == "throughout the day of the rejection and the day after"
    assert (got["own_busy"], got["own_runs"]) == ("a run", "1 run")


# --- The singular rule, at every configurable value that can be 1 ---


def test_singular_rule_buyer():
    got = prompts.fields(cfg(B), "buyer", "B-0a1", 1, 0)
    assert {k: v for k, v in got.items() if k not in ("grace_examples", "mm_shown", "value_list")} == {
        "n_buyers": "2", "n_managers": "3", "rounds_text": "1 round", "first_day_tasks": "2",
        "arrival_law": "(1/4) x (3/4)^n", "prob0": "1/4", "prob1": "3/16", "prob2": "9/64",
        "share_lo": "1", "share_hi": "1", "start_std": "1", "start_std_text": "1 credit",
        "agent_id": "B-0a1", "share": "1.00", "start_balance": "1.00",
        "buyer_sd": "2", "buyer_test_cost": "1 credits", "manager_sd": "0.5", "manager_test_cost": "1 credits",
        "decimal_places": "0 decimal places", "q0": "0", "q_span": "10", "exponent": "1",
        "late_decay": "1", "late_pct": "100%", "min_days_text": "1 day", "cap_days_text": "0 days",
        "fee_pct": "0%", "review_days_text": "1 day",
        "dispute_window_b": "throughout the day of the buyer's rejection and the day after",
        "dispute_until": "the day after it", "dispute_pct": "50%", "arbiter_sd": "0",
        "escrow_close_text": "1 day", "grace_length": "one round",
        "cpu_s_text": "1 second", "wall_s_text": "1 second", "out_kb": "1",
        "code_chars_text": "1 character", "out_chars_text": "1 character",
        "max_actions": "1", "max_actions_text": "1 action", "memory_chars_text": "1 character",
        "msg_chars_text": "1 character",
        # the unit looks at the written number: 0.9999999 is written 1
        "price_in_text": "1 credit", "price_out_text": "1 credit",
        "close_after_text": "1 day", "n_sections": "12",
        "budget_std": "1", "budget_std_text": "1 credit", "budget": "1.00", "mm_chars_text": "1 character",
    }
    assert got["grace_examples"] == block("\nR = 1, W = 1:")
    assert got["value_list"] == ", ".join(f"{q} -> {q}0.0%" for q in range(1, 11))
    assert got["mm_shown"].startswith("Each conversation shows its messages in full, up to 2 of them. Whenever a "
                                      "conversation has more than 2 messages, the market replaces its earliest "
                                      "messages, in groups of 2, with")
    assert got["mm_shown"].endswith("shows that summary and its latest 1 to 2 messages.")


def test_singular_rule_manager():
    got = prompts.fields(cfg(B), "manager", "M-0a1", 1, 0)
    assert (got["worth_lo"], got["worth_hi_text"]) == ("1", "1 credit")
    assert (got["price_in_text"], got["price_out_text"]) == ("0 credits", "0 credits")
    assert (got["emp_runs"], got["own_runs"], got["own_busy"]) == ("1 run", "1 run", "a run")
    # CREDITS looks at the rounded cost: 0.5 + 0.00504 x 100 = 1.004 rounds to 1; 0.5 + 0.005 x 100 = 1
    assert (got["emp_fresh_cost"], got["emp_ref_cost"]) == ("1 credit", "1 credit")
    assert (got["own_fresh_cost"], got["own_ref_cost"]) == ("10 credits", "14 credits")
    # CREDITS looks at the value: 0.9999999 is not 1
    assert (got["buyer_test_cost"], got["manager_test_cost"]) == ("1 credits", "1 credits")


def test_singular_rule_other_ones():
    got = fields(cfg(), "manager")
    assert (got["buyer_test_cost"], got["manager_test_cost"], got["decimal_places"]) == \
        ("1 credit", "1 credit", "1 decimal place")
    got = fields(cfg(value={"late_decay": 0.5}, production={"employee": {"capacity": 1}}), "manager")
    assert (got["cap_days_text"], got["escrow_close_text"], got["emp_runs"]) == ("1 day", "2 days", "1 run")
    got = fields(cfg(tests={"decimals": 3}), "buyer")
    assert got["decimal_places"] == "3 decimal places"
    text = render(cfg(B), "buyer")
    assert "Each day is divided into 1 round, and each participant" in text
    assert "Actions beyond the first 1 are dropped." in text
    assert "(up to 1 if their act call couldn't be read)" in text


# --- Every field's format at a non-default value ---


def test_formats_buyer():
    c = cfg(A)
    got = prompts.fields(c, "buyer", "B-0a1", 1.7, 0)
    assert got == {
        "n_buyers": "5", "n_managers": "12", "rounds_text": "4 rounds", "first_day_tasks": "3",
        "arrival_law": "(3/5) x (2/5)^n", "prob0": "3/5", "prob1": "6/25", "prob2": "12/125",
        "share_lo": "0.5", "share_hi": "2", "start_std": "250", "start_std_text": "250 credits",
        "agent_id": "B-0a1", "share": "1.70", "start_balance": "425.00",
        "buyer_sd": "1.75", "buyer_test_cost": "2.5 credits", "manager_sd": "0.25",
        "manager_test_cost": "2.5 credits", "decimal_places": "2 decimal places",
        "q0": "2.5", "q_span": "7.5", "exponent": "1.5",
        # f(q) = ((q - 2.5) / 7.5)^1.5: 0.017213, 0.089443, 0.192450, 0.318787, 0.464758, 0.627990, 0.806823, 1
        "value_list": "1 -> 0.0%, 2 -> 0.0%, 3 -> 1.7%, 4 -> 8.9%, 5 -> 19.2%, 6 -> 31.9%, 7 -> 46.5%, 8 -> 62.8%, "
                      "9 -> 80.7%, 10 -> 100.0%",
        "late_decay": "0.125", "late_pct": "12.5%", "min_days_text": "6 days", "cap_days_text": "7 days",
        "fee_pct": "7.5%", "review_days_text": "3 days",
        "dispute_window_b": "throughout the day of the buyer's rejection and the 2 days after",
        "dispute_until": "the day 2 days after it", "dispute_pct": "33.33%", "arbiter_sd": "0.75",
        "escrow_close_text": "8 days", "grace_length": "two rounds", "grace_examples": block("\nR = 4, W = 2:"),
        "cpu_s_text": "7 seconds", "wall_s_text": "30 seconds", "out_kb": "128",
        "code_chars_text": "15000 characters", "out_chars_text": "6000 characters",
        "max_actions": "3", "max_actions_text": "3 actions", "memory_chars_text": "1500 characters",
        "msg_chars_text": "280 characters",
        # 0.123456789 x 3 = 0.370370367 and 1234.5678 x 3 = 3703.7034, in G
        "price_in_text": "0.37037 credits", "price_out_text": "3703.7 credits",
        "close_after_text": "9 days", "n_sections": "12",
        "budget_std": "45", "budget_std_text": "45 credits", "budget": "76.50",
        "mm_chars_text": "750 characters", "mm_shown": block("`mm_shown`, where").replace("{k}", "4"),
    }
    default = fields(cfg(message_channels="buyers"), "buyer") | {"n_sections": "11"}
    assert not [name for name in got if got[name] == default[name]]


def test_formats_manager():
    c = cfg(A)
    got = prompts.fields(c, "manager", "M-9fe", 0.5, 0)
    buyer = prompts.fields(c, "buyer", "M-9fe", 0.5, 0)
    shared = set(got) & set(buyer) - {"price_in_text", "price_out_text"}
    assert len(shared) == 50 and all(got[name] == buyer[name] for name in shared)
    assert (got["share"], got["start_balance"]) == ("0.50", "125.00")
    assert {name: got[name] for name in got if name not in buyer} == {
        "worth_lo": "25", "worth_hi_text": "120 credits",
        "dispute_window_m": "throughout the day of the rejection and the 2 days after", "own_busy": "2 runs",
        # Normal(5, 1) on [0, 10] is symmetric: mean 5; shares 0.5, 0.158655, 0.022750, 0.001350, 0.000031
        "emp_mean": "5.0", "emp_mu": "5", "emp_sd": "1",
        "emp_reach5": "50%", "emp_reach6": "16%", "emp_reach7": "2%", "emp_reach8": "0%", "emp_reach9": "0%",
        "emp_fresh_days": "With probability 11%, employee fresh runs take 2 or fewer days, with probability 74%, "
                          "employee fresh runs take 3 days, and with probability 15%, employee fresh runs take 4 "
                          "or more days",
        # 0.5 + 0.05 x 100 = 5.5 and 0.4 x 5 / 5.5 = 0.3636; 0.5 + 0.0275 x 100 = 3.25 and 0.4 x 2.75 / 3.25 = 0.3385
        "emp_fresh_cost": "5.5 credits", "emp_fresh_spread": "36%", "emp_gain": "0.25", "emp_sd_ref": "1.25",
        "emp_ref_days": "With probability 50%, employee refinements take 1 day, and with probability 50%, employee "
                        "refinements take 2 days",
        "emp_ref_cost": "3.25 credits", "emp_ref_spread": "34%", "emp_runs": "3 runs",
        # Normal(10, 2) on [0, 10]: mean 10 - 2 x 0.398941 / 0.5 = 8.4042; shares 0.98758, 0.95450, 0.86639,
        # 0.68269, 0.38293
        "own_mean": "8.4", "own_mu": "10", "own_sd": "2",
        "own_reach5": "99%", "own_reach6": "95%", "own_reach7": "87%", "own_reach8": "68%", "own_reach9": "38%",
        "own_fresh_days": "With probability 100%, manager fresh runs take 1 day",
        # 0.125 x 100 = 12.5; 9.999 rounds to 10; the manager's spreads are sig_c; gain 0.3333333333 to 6 places
        "own_fresh_cost": "12.5 credits", "own_fresh_spread": "40%", "own_gain": "0.333333", "own_sd_ref": "0.6",
        "own_ref_days": "With probability 27%, manager refinements take 3 or fewer days, with probability 57%, "
                        "manager refinements take 4 days, with probability 15%, manager refinements take 5 days, "
                        "and with probability 1%, manager refinements take 6 or more days",
        "own_ref_cost": "10 credits", "own_ref_spread": "40%", "own_runs": "2 runs",
    }
    default = fields(cfg(message_channels="managers"), "manager") | {"n_sections": "11"}
    assert not [name for name in got if got[name] == default[name]]


def test_formats_at_the_edges_of_g_and_pct():
    c = cfg(
        tasks={"worth_range": [40, 2500000]},
        money={"start_amount": 999999},
        value={"q0": 0.1 + 0.2, "late_decay": 0.3},
        contracts={"escrow": {"dispute_fee_rate": 1 / 3, "fee_rate": 1}},
        tests={"buyer_sd": 1.2345678},
        production={"employee": {"sd_ref": 0.00001234}},
    )
    got = fields(c, "manager")
    assert got["worth_hi_text"] == "2.5e+06 credits"
    assert (got["start_std"], got["start_std_text"], got["start_balance"]) == \
        ("999999", "999999 credits", "1349998.65")
    assert (got["q0"], got["q_span"]) == ("0.3", "9.7")
    assert (got["late_decay"], got["late_pct"]) == ("0.3", "30%")
    assert (got["dispute_pct"], got["fee_pct"]) == ("33.3333%", "100%")
    assert got["buyer_sd"] == "1.23457"
    assert got["emp_sd_ref"] == "1.234e-05"


# --- Section 6: the deliberate quirks are the templates' fixed text at any configuration ---


@pytest.mark.parametrize("raw", [{}, A, B])
def test_quirks_are_fixed_text(raw):
    buyer = prompts.system_prompt(cfg(raw), "buyer", "B-0a1", 1, 0)
    manager = prompts.system_prompt(cfg(raw), "manager", "M-0a1", 1, 0)
    for fixed in ('"task-0"', '"post-0"', '"bid-0"', '"contract-0"', '"contract-0-2"', "such as R-7x3", '"B-7x4"',
                  '"M-x43"', '"My reading gives quality ~3 but threshold is ~7. Please refine this result before '
                  'submitting again."', "is allowed only if the post prefers escrow after the edit",
                  "9. Name: pay_escrow_arbiter(contract_id)."):
        assert fixed in buyer
    for fixed in ('"post-0"', '"contract-0"', "such as R-7x3", '"B-7x4"', '"M-x43"', "in credits, e.g. 35."):
        assert fixed in manager
    for text in (buyer, manager):
        assert "The buyer can end a plain contract whose deadline day has passed with nothing delivered." in text
        assert "The result becomes the buyer's only when the buyer pays the arbiter's fee" in text
