"""Blind tests derived only from spec/ and the supplied public API.

Run without pytest's disk cache or Python bytecode:
    PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider tests/blind/test_prompts_blind.py

The tests write no files and block all socket networking.
Expected values never come from the implementation under test.
"""

import copy
import importlib
import math
from pathlib import Path
import re
import socket
import sys
from fractions import Fraction

import pytest
import yaml


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "spec"
MODES = ("off", "managers", "buyers", "within_roles", "all")
ROLES = ("buyer", "manager")
PLACEHOLDER = re.compile("\\{([a-z0-9_]+)\\}")

VALUE_DEFAULT = (
    "1 -> 0.0%, 2 -> 1.2%, 3 -> 4.9%, 4 -> 11.1%, "
    "5 -> 19.8%, 6 -> 30.9%, 7 -> 44.4%, 8 -> 60.5%, "
    "9 -> 79.0%, 10 -> 100.0%"
)

GRACE_DEFAULT = (
    "Specifically, if a buyer posts a task in round 1 of a day, the grace "
    "period is round 2 of that day; if a buyer posts a task in round 2 of "
    "a day, the grace period is round 3 of that day; but if a buyer posts "
    "a task in round 3 of a day, the grace period is round 1 of the next "
    "day. So bids on a post made in round 1 can first be accepted in round "
    "3 of that day, on a post made in round 2 in round 1 of the next day, "
    "and on a post made in round 3 in round 2 of the next day."
)

DURATION_DEFAULTS = {
    "emp_fresh_days": (
        "With probability 50%, employee fresh runs take 1 day, "
        "with probability 48%, employee fresh runs take 2 days, "
        "and with probability 2%, employee fresh runs take 3 or more days"
    ),
    "emp_ref_days": (
        "With probability 85%, employee refinements take 1 day, "
        "and with probability 15%, employee refinements take 2 or more days"
    ),
    "own_fresh_days": (
        "With probability 26%, manager fresh runs take 2 or fewer days, "
        "with probability 44%, manager fresh runs take 3 days, "
        "with probability 21%, manager fresh runs take 4 days, "
        "with probability 7%, manager fresh runs take 5 days, "
        "and with probability 2%, manager fresh runs take 6 or more days"
    ),
    "own_ref_days": (
        "With probability 50%, manager refinements take 1 day, "
        "with probability 48%, manager refinements take 2 days, "
        "and with probability 2%, manager refinements take 3 or more days"
    ),
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network access is forbidden in blind prompt tests")

    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.fixture
def api(no_network):
    return importlib.import_module("market.prompts")


@pytest.fixture
def cfg():
    # config.md section 2 is input data, not implementation output.
    text = (SPEC / "config.md").read_text(encoding="utf-8")
    schema = text.split("```yaml\n", 1)[1].split("```", 1)[0]
    result = yaml.safe_load(schema)
    if result["message_channels"] is False:
        result["message_channels"] = "off"
    return result


def put(cfg, path, value):
    parts = path.split(".")
    block = cfg
    for part in parts[:-1]:
        block = block[part]
    block[parts[-1]] = value


def at(cfg, path):
    value = cfg
    for part in path.split("."):
        value = value[part]
    return value


def g(value):
    return format(value, ".6g")


def unit(written, noun):
    return written + " " + noun + ("" if written == "1" else "s")


def credits(value):
    return g(value) + (" credit" if value == 1 else " credits")


def pct(value):
    return g(round(100 * value, 6)) + "%"


def pct0(value):
    return str(round(100 * value)) + "%"


def frac(value):
    answer = Fraction(value).limit_denominator(10**6)
    if answer.denominator == 1:
        return str(answer.numerator)
    return str(answer.numerator) + "/" + str(answer.denominator)


def cdf(value):
    return math.erfc(-value / math.sqrt(2)) / 2


def density(value):
    return math.exp(-value * value / 2) / math.sqrt(2 * math.pi)


def compensated(values):
    # README: explicitly implement its ordered Neumaier SUM.
    hi = 0.0
    lo = 0.0
    for value in values:
        total = hi + value
        if abs(hi) >= abs(value):
            lo = lo + ((hi - total) + value)
        else:
            lo = lo + ((value - total) + hi)
        hi = total
    return hi + lo if lo != 0 and math.isfinite(lo) else hi


def expected_value_list(cfg):
    q0 = cfg["value"]["q0"]
    exponent = cfg["value"]["exponent"]
    items = []
    for q in range(1, 11):
        clipped = max(0, min(1, (q - q0) / (10 - q0)))
        value = 100 * math.pow(clipped, exponent)
        items.append(str(q) + " -> " + format(value, ".1f") + "%")
    return ", ".join(items)


def day_phrase(day):
    if day == 0:
        return "that day"
    if day == 1:
        return "the next day"
    return "the day " + str(day) + " days later"


def expected_grace(rounds, window):
    def position(index):
        day, round0 = divmod(index - 1, rounds)
        return day, round0 + 1

    def span(start, end):
        groups = []
        while start <= end:
            day, first = position(start)
            last_position = min(end, (day + 1) * rounds)
            _, last = position(last_position)
            if first == last:
                words = "round " + str(first)
            elif last == first + 1:
                words = "rounds " + str(first) + " and " + str(last)
            else:
                words = "rounds " + str(first) + " to " + str(last)
            groups.append(words + " of " + day_phrase(day))
            start = last_position + 1
        if len(groups) == 1:
            return groups[0]
        return ", ".join(groups[:-1]) + " and " + groups[-1]

    first_sentence = "Specifically, "
    previous_wrap = False
    acceptance = []
    for posting_round in range(1, rounds + 1):
        wraps = position(posting_round + window)[0] >= 1
        if posting_round > 1:
            first_sentence += "; but " if wraps and not previous_wrap else "; "
        first_sentence += (
            "if a buyer posts a task in round " + str(posting_round)
            + " of a day, the grace period is "
            + span(posting_round + 1, posting_round + window)
        )
        previous_wrap = wraps
        day, round_number = position(posting_round + window + 1)
        acceptance.append(
            "round " + str(round_number) + " of " + day_phrase(day)
        )

    second_sentence = (
        "So bids on a post made in round 1 can first be accepted in "
        + acceptance[0]
    )
    for index in range(1, rounds):
        second_sentence += (
            ", " + ("and " if index == rounds - 1 else "")
            + "on a post made in round " + str(index + 1)
            + " in " + acceptance[index]
        )
    return first_sentence + ". " + second_sentence + "."


def days_mix(median, sigma):
    # config.md 6.7: retain only visited counts; never renormalize.
    result = []
    below = 0.0
    day = 1
    while below < 1 - 1e-9 and day < 1000:
        if sigma == 0:
            cumulative = 1.0 if median <= day else 0.0
        else:
            cumulative = cdf(math.log(day / median) / sigma)
        result.append((day, cumulative - below))
        below = cumulative
        day += 1
    if below < 1 - 1e-9:
        result.append((1000, 1 - below))
    return result


def expected_duration(median, sigma, subject):
    # prompts.md 4.4, including both specified earliest-bucket tie breaks.
    mix = days_mix(median, sigma)
    listed = [day for day, share in mix if day != 1000 and share >= 0.05]
    if not listed:
        raise IndexError("No listable day")
    first, last = listed[0], listed[-1]
    below = compensated(share for day, share in mix if day < first)
    high = compensated(share for day, share in mix if day > last)
    low = below > 0
    tail = high >= 0.005
    up = not tail and high >= 1e-4
    split = up and low and first == last
    names = []
    probabilities = []
    shares = dict(mix)

    for day in range(first, last + 1):
        probability = shares[day] + (below if day == first else 0)
        probability = probability + (
            high if day == last and not tail and not split else 0
        )
        probabilities.append(probability)
        if day == first and low:
            name = str(day) + " or fewer days"
        elif day == last and up and not split:
            name = str(day) + " or more days"
        else:
            name = unit(str(day), "day")
        names.append(name)

    if tail or split:
        names.append(str(last + 1) + " or more days")
        probabilities.append(high)

    if split:
        percents = ["more than 99%", "less than 1%"]
    else:
        scaled = [100 * probability for probability in probabilities]
        integers = [math.floor(value) for value in scaled]
        order = sorted(
            range(len(integers)),
            key=lambda index: (-(scaled[index] - integers[index]), index),
        )
        for index in order[:100 - sum(integers)]:
            integers[index] += 1
        if tail and integers[-1] == 0:
            donor = max(range(len(integers)), key=lambda index: integers[index])
            integers[donor] -= 1
            integers[-1] = 1
        percents = [str(value) + "%" for value in integers]

    clauses = [
        "with probability " + percent + ", " + subject + " take " + name
        for percent, name in zip(percents, names)
    ]
    if len(clauses) == 1:
        result = clauses[0]
    else:
        result = ", ".join(clauses[:-1]) + ", and " + clauses[-1]
    return "W" + result[1:]


def can_send(role, mode):
    return mode in ("all", "within_roles", role + "s")


def message_sentence(recent):
    k = str(recent)
    return (
        "Each conversation shows its messages in full, up to " + k
        + " of them. Whenever a conversation has more than " + k
        + " messages, the market replaces its earliest messages, in groups of "
        + k + ", with a summary written automatically by the market "
        "(covering any earlier summary too), so the conversation shows that "
        "summary and its latest 1 to " + k + " messages."
    )


def expected_fields(cfg, role, agent_id, share, seat):
    # Independent transcription of prompts.md sections 2-5.
    result = {}
    integer_fields = {
        "n_buyers": "participants.buyers",
        "n_managers": "participants.managers",
        "first_day_tasks": "tasks.first_day_tasks",
        "out_kb": "compute.output_kb",
        "max_actions": "turns.max_actions",
    }
    general_fields = {
        "start_std": "money.start_amount",
        "buyer_sd": "tests.buyer_sd",
        "manager_sd": "tests.manager_sd",
        "q0": "value.q0",
        "exponent": "value.exponent",
        "late_decay": "value.late_decay",
        "arbiter_sd": "contracts.escrow.arbiter_sd",
    }
    unit_fields = {
        "start_std_text": ("money.start_amount", "credit", g),
        "min_days_text": ("contracts.min_contract_days", "day", str),
        "review_days_text": ("contracts.escrow.review_days", "day", str),
        "cpu_s_text": ("compute.cpu_s", "second", str),
        "wall_s_text": ("compute.wall_s", "second", str),
        "code_chars_text": ("compute.code_chars", "character", str),
        "out_chars_text": ("compute.output_chars", "character", str),
        "max_actions_text": ("turns.max_actions", "action", str),
        "memory_chars_text": ("turns.memory_chars", "character", str),
        "msg_chars_text": ("contracts.message_chars", "character", str),
        "close_after_text": ("time.close_after_due_days", "day", str),
    }
    for field, path in integer_fields.items():
        result[field] = str(at(cfg, path))
    for field, path in general_fields.items():
        result[field] = g(at(cfg, path))
    for field, (path, noun, formatter) in unit_fields.items():
        result[field] = unit(formatter(at(cfg, path)), noun)

    result["agent_id"] = agent_id
    result["share"] = format(share, ".2f")
    start = cfg["money"]["start_amount"]
    balance = start if share == 1 else round(start * share, 2)
    result["start_balance"] = format(balance, ".2f")
    result["share_lo"] = g(cfg["money"]["share_range"][0])
    result["share_hi"] = g(cfg["money"]["share_range"][1])
    result["buyer_test_cost"] = credits(cfg["tests"]["buyer_cost"])
    result["manager_test_cost"] = credits(cfg["tests"]["manager_cost"])
    result["decimal_places"] = unit(str(cfg["tests"]["decimals"]), "decimal place")
    result["q_span"] = g(10 - cfg["value"]["q0"])
    result["value_list"] = expected_value_list(cfg)
    result["late_pct"] = pct(cfg["value"]["late_decay"])
    result["fee_pct"] = pct(cfg["contracts"]["escrow"]["fee_rate"])
    result["dispute_pct"] = pct(cfg["contracts"]["escrow"]["dispute_fee_rate"])

    close = math.ceil(1 / cfg["value"]["late_decay"] - 1e-9)
    result["escrow_close_text"] = unit(str(close), "day")
    result["cap_days_text"] = unit(str(close - 1), "day")

    rounds = cfg["time"]["rounds_per_day"]
    window = cfg["contracts"]["bid_window_rounds"]
    result["rounds_text"] = unit(str(rounds), "round")
    words = (
        "one", "two", "three", "four", "five",
        "six", "seven", "eight", "nine", "ten",
    )
    window_text = words[window - 1] if window <= 10 else str(window)
    result["grace_length"] = window_text + (" round" if window == 1 else " rounds")
    result["grace_examples"] = expected_grace(rounds, window)

    p = cfg["tasks"]["arrival_p"]
    result["prob0"] = frac(1 - p)
    result["prob1"] = frac((1 - p) * p)
    result["prob2"] = frac((1 - p) * p * p)
    result["arrival_law"] = (
        "(1/2)^(n+1)" if p == 0.5
        else "(" + frac(1 - p) + ") x (" + frac(p) + ")^n"
    )

    dispute_days = cfg["contracts"]["escrow"]["dispute_window_days"]
    suffix = (
        " and the day after" if dispute_days == 1
        else " and the " + str(dispute_days) + " days after"
    )
    result["dispute_window_b"] = "throughout the day of the buyer's rejection" + suffix
    result["dispute_until"] = (
        "the day after it" if dispute_days == 1
        else "the day " + str(dispute_days) + " days after it"
    )

    models = cfg["models"]
    # Corrected by the adjudicator: a resolved config keys the seat mappings by the seat
    # index's decimal digits (config.md 3.4.2 allows either spelling in the file; the
    # stored form is the implementation's choice, documented in the config API).
    model = models[role + "_seats"].get(str(seat), models[role + "s"])
    for direction in ("in", "out"):
        price = model.get("price_" + direction + "_per_m", 0)
        result["price_" + direction + "_text"] = unit(
            g(price * cfg["credits_per_usd"]), "credit"
        )

    sending = can_send(role, cfg["message_channels"])
    result["n_sections"] = "12" if sending else "11"
    if sending:
        result["mm_chars_text"] = unit(str(cfg["messaging"]["message_chars"]), "character")
        result["mm_shown"] = message_sentence(cfg["messaging"]["recent"])

    if role == "buyer":
        budget = cfg["money"]["task_budget"]
        result["budget_std"] = g(budget)
        result["budget_std_text"] = unit(g(budget), "credit")
        amount = budget if share == 1 else round(budget * share, 2)
        result["budget"] = format(amount, ".2f")
        return result

    result["worth_lo"] = g(cfg["tasks"]["worth_range"][0])
    result["worth_hi_text"] = unit(g(cfg["tasks"]["worth_range"][1]), "credit")
    result["dispute_window_m"] = "throughout the day of the rejection" + suffix
    capacity = cfg["production"]["manager"]["capacity"]
    result["own_busy"] = "a run" if capacity == 1 else str(capacity) + " runs"

    production = cfg["production"]
    ref = production["cost_ref_worth"]
    for prefix, worker_name in (("emp", "employee"), ("own", "manager")):
        worker = production[worker_name]
        mu, sd = worker["mu"], worker["sd"]
        a, b = (0 - mu) / sd, (10 - mu) / sd
        denominator = cdf(b) - cdf(a)
        mean = mu + sd * (density(a) - density(b)) / denominator
        result[prefix + "_mean"] = format(mean, ".1f")
        result[prefix + "_mu"] = g(mu)
        result[prefix + "_sd"] = g(sd)
        for q in range(5, 10):
            tail = (cdf(b) - cdf((q - mu) / sd)) / denominator
            result[prefix + "_reach" + str(q)] = pct0(tail)
        result[prefix + "_gain"] = g(round(worker["gain"] / 10, 6))
        result[prefix + "_sd_ref"] = g(worker["sd_ref"])
        result[prefix + "_runs"] = unit(str(worker["capacity"]), "run")
        fixed = worker["fee"] if prefix == "emp" else 0
        costs = (
            (
                worker["fee"] + worker["cost_frac"] * ref,
                worker["fee"] + worker["ref_cost_frac"] * ref,
            )
            if prefix == "emp"
            else (worker["k"] * ref, worker["ref_cost_fixed"])
        )
        for kind, median_key, subject_end, cost in (
            ("fresh", "t_med", "fresh runs", costs[0]),
            ("ref", "t_ref_med", "refinements", costs[1]),
        ):
            result[prefix + "_" + kind + "_days"] = expected_duration(
                worker[median_key], production["sig_t"],
                worker_name + " " + subject_end,
            )
            result[prefix + "_" + kind + "_cost"] = credits(round(cost, 2))
            spread = (production["sig_c"] * (cost - fixed)) / cost
            result[prefix + "_" + kind + "_spread"] = pct0(spread)
    return result


def source_template(role, mode):
    return (SPEC / "text" / (role + "_system_" + mode + ".txt")).read_bytes().decode("ascii")


def render_expected(cfg, role, agent_id, share, seat):
    fields = expected_fields(cfg, role, agent_id, share, seat)
    text = source_template(role, cfg["message_channels"])
    assert set(PLACEHOLDER.findall(text)) == set(fields)
    return PLACEHOLDER.sub(lambda match: fields[match.group(1)], text)


def check_agent(api, cfg, role, agent_id="X-test", share=1.0, seat=0):
    before = copy.deepcopy(cfg)
    expected = expected_fields(cfg, role, agent_id, share, seat)
    actual = api.fields(cfg, role, agent_id, share, seat)
    assert actual == expected
    assert all(isinstance(value, str) for value in actual.values())
    assert all("\n" not in value and "\r" not in value for value in actual.values())
    rendered = api.system_prompt(cfg, role, agent_id, share, seat)
    assert rendered == render_expected(cfg, role, agent_id, share, seat)
    assert cfg == before
    return actual, rendered


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("mode", MODES)
def test_template_exact_bytes_and_path(api, role, mode):
    # prompts.md 1.1; API template() and TEXT_DIR.
    expected = source_template(role, mode)
    assert api.TEXT_DIR == ROOT / "spec" / "text"
    assert api.template(role, mode).encode("ascii") == expected.encode("ascii")
    assert api.template(role, mode) == expected
    assert not expected.endswith("\n")
    assert "\r" not in expected
    assert PLACEHOLDER.sub("", expected).count("{") == 0
    assert PLACEHOLDER.sub("", expected).count("}") == 0


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("mode", MODES)
def test_all_fields_and_all_template_text(api, cfg, role, mode):
    # prompts.md 1-6 and 8: includes every literal, action and closing-list item.
    cfg["message_channels"] = mode
    agent_id, share = ("B-c53", 1.36) if role == "buyer" else ("M-14b", 1.35)
    fields, rendered = check_agent(api, cfg, role, agent_id, share)
    sending = can_send(role, mode)
    assert len(fields) == (53 if role == "buyer" else 88) + (2 if sending else 0)
    assert ("mm_shown" in fields) == sending
    assert ("mm_chars_text" in fields) == sending
    assert fields["n_sections"] == ("12" if sending else "11")
    assert not rendered.endswith("\n")
    assert rendered.isascii()


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("mode", ("off", "all"))
def test_acceptance_check_one(api, cfg, role, mode):
    # README acceptance check 1; prompts.md 8.
    cfg["message_channels"] = mode
    agent_id, share = ("B-c53", 1.36) if role == "buyer" else ("M-14b", 1.35)
    path = SPEC / "examples" / (
        "system_prompt_" + role + "_" + mode + "_" + agent_id + ".txt"
    )
    actual = api.system_prompt(cfg, role, agent_id, share, 0)
    assert actual.encode("ascii") == path.read_bytes()


@pytest.mark.parametrize("role,column", (("buyer", 1), ("manager", 2)))
def test_every_worked_example_table_entry(api, cfg, role, column):
    # prompts.md 8: literal entries are independently read from the normative table.
    text = (SPEC / "prompts.md").read_text(encoding="utf-8")
    table = text.split("## 8. Worked example:", 1)[1]
    agent_id, share = ("B-c53", 1.36) if role == "buyer" else ("M-14b", 1.35)
    expected = {}
    for line in table.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        name = cells[0].strip("`")
        value = cells[column]
        if value == "not in the file":
            continue
        if value.startswith("`") and value.endswith("`"):
            expected[name] = value[1:-1]
        elif name == "value_list":
            expected[name] = VALUE_DEFAULT
        elif name == "grace_examples":
            expected[name] = GRACE_DEFAULT
        else:
            expected[name] = DURATION_DEFAULTS[name]
    assert len(expected) == (53 if role == "buyer" else 88)
    assert api.fields(cfg, role, agent_id, share, 0) == expected


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("mode", MODES)
def test_single_pass_and_identity_preservation(api, cfg, role, mode):
    # prompts.md 1.2: inserted text is neither rescanned nor stripped.
    cfg["message_channels"] = mode
    agent_id = "  {share}:{agent_id}:{undefined}:\\literal  "
    _, rendered = check_agent(api, cfg, role, agent_id, 1.0)
    assert "your ID is " + agent_id + " and your wealth share is 1.00" in rendered
    assert "{undefined}" in rendered


@pytest.mark.parametrize("role", ROLES)
def test_deterministic_and_no_config_mutation(api, cfg, role):
    # prompts.md 1.3: fixed inputs give the same text, also after unrelated calls.
    original = copy.deepcopy(cfg)
    first = api.system_prompt(cfg, role, "X-fixed", 1.25, 0)
    other = copy.deepcopy(cfg)
    other["message_channels"] = "all"
    other["money"]["start_amount"] = 37
    api.system_prompt(other, role, "X-other", 0.75, 0)
    assert api.system_prompt(copy.deepcopy(cfg), role, "X-fixed", 1.25, 0) == first
    assert cfg == original


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("mode", MODES)
def test_mode_specific_closing_order(api, cfg, role, mode):
    # prompts.md 5; view.md 2.2: ordered sections and message insertion.
    cfg["message_channels"] = mode
    text = api.system_prompt(cfg, role, "X-order", 1, 0)
    sections = [
        "Results of your actions in your previous turn.",
        "Notifications since your last turn.",
        "Payments.",
        "Your accounts so far.",
        "Your record with each counterparty.",
    ]
    sections += (
        [
            "Your tasks not yet contracted.", "Your active contracts.",
            "Your finished contracts.", "Other buyers' open posts.",
        ]
        if role == "buyer"
        else [
            "Open posts on the forum.", "Your production.",
            "Your active contracts.", "Your finished contracts.",
        ]
    )
    sections += ["Your self-written memory.", "Final instructions."]
    if can_send(role, mode):
        sections.insert(
            3, "Messages." if mode == "all" else "Messages between " + role + "s."
        )
    closing = "\n".join(str(i) + ". " + item for i, item in enumerate(sections, 1))
    assert text.endswith("\n\n" + closing)
    if mode == "off":
        assert "send_message" not in text
    elif can_send(role, mode):
        number = 12 if role == "buyer" else 9
        assert str(number) + ". Name: send_message(to, text)." in text
        assert ", pay, and send_message." in text
        assert text.count("500 characters; longer text is cut to its first 500 characters") == 1
    else:
        assert "Name: send_message" not in text
        other = "managers" if role == "buyer" else "buyers"
        assert "One of them lets " + other + " send each other private messages" in text


@pytest.mark.parametrize("role", ROLES)
def test_deliberate_quirks(api, cfg, role):
    # prompts.md 6: do not repair intentionally simplified wording or example IDs.
    cfg["message_channels"] = "all"
    _, text = check_agent(api, cfg, role, "X-real", 1.23)
    for literal in (
        "R-7x3", "B-7x4", "M-x43", "post-0", "contract-0",
        "The buyer can end a plain contract whose deadline day has passed "
        "with nothing delivered.",
        "The result becomes the buyer's only when the buyer pays the arbiter's fee",
    ):
        assert literal in text
    if role == "buyer":
        for literal in (
            "task-0", "bid-0", "contract-0-2",
            "It is allowed only if the post prefers escrow after the edit.",
            "My reading gives quality ~3 but threshold is ~7. "
            "Please refine this result before submitting again.",
        ):
            assert literal in text
    else:
        assert 'e.g. 35.' in text


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("share", (0.0, 0.01, 0.6, 1.0, 1.35, 1.36, 1.4, 999999.0))
def test_balances_and_budgets(api, cfg, role, share):
    # prompts.md 3; config.md 6.4; episode.md 1.3.
    if share > 1.4:
        cfg["money"]["share_range"] = [1.0, 999999.0]
    fields, _ = check_agent(api, cfg, role, "X-money", share)
    if share == 0:
        assert fields["start_balance"] == "0.00"
        if role == "buyer":
            assert fields["budget"] == "0.00"
    if share == 1:
        assert fields["start_balance"] == "60.00"
        if role == "buyer":
            assert fields["budget"] == "30.00"


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("amount", (1, 999999))
def test_whole_money_parameter_boundaries(api, cfg, role, amount):
    # prompts.md 7; config.md 3.8: positive whole amounts below 10**6.
    cfg["money"]["start_amount"] = amount
    cfg["money"]["task_budget"] = amount
    check_agent(api, cfg, role, share=1.37)


@pytest.mark.parametrize("role", ROLES)
def test_singular_parameter_units(api, cfg, role):
    # README singular rule; prompts.md 2, 3 and 4.5.
    cfg["message_channels"] = "all"
    changes = {
        "time.rounds_per_day": 1,
        "time.close_after_due_days": 1,
        "money.start_amount": 1,
        "money.task_budget": 1,
        "tasks.worth_range": [1, 1],
        "value.late_decay": 1,
        "contracts.min_contract_days": 1,
        "contracts.message_chars": 1,
        "contracts.escrow.review_days": 1,
        "compute.cpu_s": 1,
        "compute.wall_s": 1,
        "compute.output_kb": 1,
        "compute.code_chars": 1,
        "compute.output_chars": 1,
        "turns.max_actions": 1,
        "turns.memory_chars": 1,
        "messaging.message_chars": 1,
        "messaging.recent": 2,
        "production.employee.capacity": 1,
        "production.manager.capacity": 1,
    }
    for path, value in changes.items():
        put(cfg, path, value)
    fields, _ = check_agent(api, cfg, role)
    assert fields["cap_days_text"] == "0 days"
    assert fields["max_actions"] == "1"
    assert fields["max_actions_text"] == "1 action"
    assert fields["start_balance"] == "1.00"
    assert fields["mm_chars_text"] == "1 character"


@pytest.mark.parametrize("decimals", (0, 1, 2, 3))
def test_decimal_place_plural(api, cfg, decimals):
    # prompts.md 3.1; config.md 3.12.
    cfg["tests"]["decimals"] = decimals
    expected = str(decimals) + (" decimal place" if decimals == 1 else " decimal places")
    for role in ROLES:
        fields, _ = check_agent(api, cfg, role)
        assert fields["decimal_places"] == expected


@pytest.mark.parametrize(
    "value,written",
    [
        (0.0, "0 credits"),
        (-0.0, "-0 credits"),
        (1.0, "1 credit"),
        (0.9999999, "1 credits"),
        (1.0000001, "1 credits"),
        (0.0001, "0.0001 credits"),
        (0.00001, "1e-05 credits"),
        (999999.0, "999999 credits"),
        (1000000.0, "1e+06 credits"),
        (1234567.0, "1.23457e+06 credits"),
        (2.675, "2.675 credits"),
    ],
)
def test_credits_format_uses_value_not_rendered_number(api, cfg, value, written):
    # README G and CREDITS; prompts.md 3.1: test costs are not rounded to cents here.
    cfg["tests"]["buyer_cost"] = value
    cfg["tests"]["manager_cost"] = value
    fields, _ = check_agent(api, cfg, "buyer")
    assert fields["buyer_test_cost"] == written
    assert fields["manager_test_cost"] == written


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("provider", ("mock", "ollama"))
def test_free_model_missing_prices_and_whole_seat_replacement(api, cfg, role, provider):
    # prompts.md 2 and 3.1; config.md 3.4.1-3.4.2.
    cfg["models"][role + "_seats"]["1"] = {"provider": provider, "model": "local"}
    fields, _ = check_agent(api, cfg, role, seat=1)
    assert fields["price_in_text"] == "0 credits"
    assert fields["price_out_text"] == "0 credits"
    default, _ = check_agent(api, cfg, role, seat=0)
    assert default["price_in_text"] == "0.15 credits"
    assert default["price_out_text"] == "1.875 credits"


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "price,expected",
    [(0.9999999, "1 credit"), (1.0000001, "1 credit"), (1e-5, "1e-05 credits")],
)
def test_price_unit_uses_written_g(api, cfg, role, price, expected):
    # README singular rule differs from CREDITS; prompts.md 3.1.
    cfg["models"][role + "s"]["price_in_per_m"] = price
    fields, _ = check_agent(api, cfg, role)
    assert fields["price_in_text"] == expected


@pytest.mark.parametrize("role", ROLES)
def test_seat_prices_multiply_credits_per_usd(api, cfg, role):
    # prompts.md 3.1; config.md 3.4.4.
    cfg["credits_per_usd"] = 2.5
    cfg["models"][role + "_seats"]["1"] = {
        "provider": "mock", "price_in_per_m": 0.4, "price_out_per_m": 2,
    }
    fields, _ = check_agent(api, cfg, role, seat=1)
    assert fields["price_in_text"] == "1 credit"
    assert fields["price_out_text"] == "5 credits"
    cfg["models"][role + "_seats"]["1"] = {
        "provider": "mock", "price_out_per_m": 2,
    }
    fields, _ = check_agent(api, cfg, role, seat=1)
    assert fields["price_in_text"] == "0 credits"


@pytest.mark.parametrize(
    "decay,close",
    [
        (0.2, 5), (0.25, 4), (0.3, 4), (1 / 3, 3), (1.0, 1), (0.1, 10),
        (0.5, 2), (1 / (5 + 5e-10), 5), (1 / (5 + 2e-9), 6),
    ],
)
def test_escrow_close_epsilon_and_cap(api, cfg, decay, close):
    # prompts.md 4.1; config.md 6.2-6.3.
    cfg["value"]["late_decay"] = decay
    cfg["time"]["close_after_due_days"] = max(5, close)
    for role in ROLES:
        fields, _ = check_agent(api, cfg, role)
        assert fields["escrow_close_text"] == unit(str(close), "day")
        assert fields["cap_days_text"] == unit(str(close - 1), "day")


@pytest.mark.parametrize(
    "rate,expected",
    [
        (0.0, "0%"), (-0.0, "-0%"), (0.125, "12.5%"),
        (1.0, "100%"), (0.000000123456789, "1.2e-05%"),
    ],
)
def test_pct_rounds_to_six_decimals_before_g(api, cfg, rate, expected):
    # README PCT; prompts.md 3.1.
    cfg["contracts"]["escrow"]["fee_rate"] = rate
    fields, _ = check_agent(api, cfg, "buyer")
    assert fields["fee_pct"] == expected


@pytest.mark.parametrize(
    "q0,exponent,expected",
    [
        (1, 2, VALUE_DEFAULT),
        (
            0, 1,
            "1 -> 10.0%, 2 -> 20.0%, 3 -> 30.0%, 4 -> 40.0%, "
            "5 -> 50.0%, 6 -> 60.0%, 7 -> 70.0%, 8 -> 80.0%, "
            "9 -> 90.0%, 10 -> 100.0%",
        ),
        (
            5, 2,
            "1 -> 0.0%, 2 -> 0.0%, 3 -> 0.0%, 4 -> 0.0%, "
            "5 -> 0.0%, 6 -> 4.0%, 7 -> 16.0%, 8 -> 36.0%, "
            "9 -> 64.0%, 10 -> 100.0%",
        ),
        (
            9.5, 0.5,
            "1 -> 0.0%, 2 -> 0.0%, 3 -> 0.0%, 4 -> 0.0%, "
            "5 -> 0.0%, 6 -> 0.0%, 7 -> 0.0%, 8 -> 0.0%, "
            "9 -> 0.0%, 10 -> 100.0%",
        ),
    ],
)
def test_value_list_exact(api, cfg, q0, exponent, expected):
    # prompts.md 4.2: clipping, power, ascending qualities and F1.
    cfg["value"].update(q0=q0, exponent=exponent)
    assert api.value_list(cfg) == expected
    fields, _ = check_agent(api, cfg, "buyer")
    assert fields["value_list"] == expected


@pytest.mark.parametrize("q0", (0.25, 2.5, 7.75, 9.999))
@pytest.mark.parametrize("exponent", (0.3, 1.5, 3.25, 40.0))
def test_value_list_nondefault_formulas(api, cfg, q0, exponent):
    # prompts.md 4.2; README F1; values chosen away from rounding ties.
    cfg["value"].update(q0=q0, exponent=exponent)
    assert api.value_list(cfg) == expected_value_list(cfg)


@pytest.mark.parametrize(
    "p,law,probabilities",
    [
        (0.5, "(1/2)^(n+1)", ("1/2", "1/4", "1/8")),
        (0.4, "(3/5) x (2/5)^n", ("3/5", "6/25", "12/125")),
        (0.01, "(99/100) x (1/100)^n", ("99/100", "99/10000", "99/1000000")),
        (0.99, "(1/100) x (99/100)^n", ("1/100", "99/10000", "9801/1000000")),
        (
            83 / 85 + 9.9e-13,
            "(2/85) x (83/85)^n",
            ("2/85", "166/7225", "21113/941067"),
        ),
        (
            0.5 + 5e-13,
            "(1/2) x (1/2)^n",
            ("1/2", "1/4", "1/8"),
        ),
    ],
)
def test_arrival_fraction_rules_and_tolerance_quirk(api, cfg, p, law, probabilities):
    # prompts.md 4.5 and 7: exact 0.5 branch; FRAC of ordered double products.
    cfg["tasks"]["arrival_p"] = p
    fields, _ = check_agent(api, cfg, "buyer")
    assert fields["arrival_law"] == law
    assert tuple(fields[key] for key in ("prob0", "prob1", "prob2")) == probabilities


@pytest.mark.parametrize("window", range(1, 13))
def test_grace_length_words_and_digits(api, cfg, window):
    # prompts.md 4.5: spell only one through ten.
    cfg["contracts"]["bid_window_rounds"] = window
    words = (
        "one round", "two rounds", "three rounds", "four rounds",
        "five rounds", "six rounds", "seven rounds", "eight rounds",
        "nine rounds", "ten rounds", "11 rounds", "12 rounds",
    )
    fields, _ = check_agent(api, cfg, "buyer")
    assert fields["grace_length"] == words[window - 1]


@pytest.mark.parametrize("rounds", (1, 2, 3, 4, 5, 7))
@pytest.mark.parametrize("window", (1, 2, 3, 4, 5, 7, 10, 11, 17, 54))
def test_grace_grouping_wraps_and_acceptance(api, cfg, rounds, window):
    # prompts.md 4.5; config.md 6.6: grouping and punctuation over multiple days.
    cfg["time"]["rounds_per_day"] = rounds
    cfg["contracts"]["bid_window_rounds"] = window
    expected = expected_grace(rounds, window)
    assert api.grace_examples(cfg) == expected
    assert api.fields(cfg, "buyer", "B-abc", 1, 0)["grace_examples"] == expected


@pytest.mark.parametrize(
    "rounds,window,expected",
    [
        (3, 1, GRACE_DEFAULT),
        (
            1, 1,
            "Specifically, if a buyer posts a task in round 1 of a day, the "
            "grace period is round 1 of the next day. So bids on a post made "
            "in round 1 can first be accepted in round 1 of the day 2 days later.",
        ),
        (
            2, 1,
            "Specifically, if a buyer posts a task in round 1 of a day, the "
            "grace period is round 2 of that day; but if a buyer posts a task "
            "in round 2 of a day, the grace period is round 1 of the next day. "
            "So bids on a post made in round 1 can first be accepted in round "
            "1 of the next day, and on a post made in round 2 in round 2 "
            "of the next day.",
        ),
    ],
)
def test_literal_grace_examples(api, cfg, rounds, window, expected):
    # prompts.md 4.5: literal worked examples independently anchor the oracle.
    cfg["time"]["rounds_per_day"] = rounds
    cfg["contracts"]["bid_window_rounds"] = window
    assert api.grace_examples(cfg) == expected


@pytest.mark.parametrize("days", (1, 2, 17, 10**25))
@pytest.mark.parametrize("capacity", (1, 2, 19))
def test_dispute_sentences_and_busy_wording(api, cfg, days, capacity):
    # prompts.md 4.5: no ordinal substitution and no numeric replacement of "a run".
    cfg["contracts"]["escrow"]["dispute_window_days"] = days
    cfg["production"]["manager"]["capacity"] = capacity
    check_agent(api, cfg, "buyer")
    fields, _ = check_agent(api, cfg, "manager")
    assert fields["own_busy"] == ("a run" if capacity == 1 else str(capacity) + " runs")


@pytest.mark.parametrize("recent", (2, 3, 10, 1000000))
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("mode", MODES)
def test_message_fields_only_for_sending_role(api, cfg, recent, role, mode):
    # prompts.md 3.4 and 5; messaging.md 1 and batching sentence.
    cfg["message_channels"] = mode
    cfg["messaging"]["recent"] = recent
    cfg["messaging"]["message_chars"] = 1
    fields, _ = check_agent(api, cfg, role)
    if can_send(role, mode):
        assert fields["mm_shown"] == message_sentence(recent)
        assert fields["mm_chars_text"] == "1 character"
    else:
        assert "mm_shown" not in fields
        assert "mm_chars_text" not in fields


@pytest.mark.parametrize("mu,sd", ((0, 1), (10, 1), (5, 2), (3.25, 1.7), (8.5, 3.1)))
@pytest.mark.parametrize("worker_name,prefix", (("employee", "emp"), ("manager", "own")))
def test_truncated_normal_mean_and_upper_tails(api, cfg, mu, sd, worker_name, prefix):
    # prompts.md 4.3: truncate rather than clip; normalize all five upper tails.
    cfg["production"][worker_name].update(mu=mu, sd=sd)
    fields, _ = check_agent(api, cfg, "manager")
    if mu == 5:
        assert fields[prefix + "_mean"] == "5.0"
        assert fields[prefix + "_reach5"] == "50%"
    values = [int(fields[prefix + "_reach" + str(q)][:-1]) for q in range(5, 10)]
    assert values == sorted(values, reverse=True)


@pytest.mark.parametrize(
    "cost,expected",
    [(1.125, "1.12 credits"), (1.375, "1.38 credits"), (1.004, "1 credit"), (2.675, "2.67 credits")],
)
def test_run_cost_rounds_before_credits_format(api, cfg, cost, expected):
    # prompts.md 3.3; README round(x, 2), ties-to-even and CREDITS.
    cfg["production"]["employee"].update(fee=0.1, cost_frac=0.001, ref_cost_frac=0.001)
    cfg["production"]["manager"]["ref_cost_fixed"] = cost
    fields, _ = check_agent(api, cfg, "manager")
    assert fields["own_ref_cost"] == expected


@pytest.mark.parametrize("sigma,expected", ((0.125, "12%"), (0.375, "38%")))
def test_pct0_ties_even(api, cfg, sigma, expected):
    # README PCT0; prompts.md 3.3: exact 12.5 and 37.5 percent.
    cfg["production"]["sig_c"] = sigma
    fields, _ = check_agent(api, cfg, "manager")
    assert fields["own_fresh_spread"] == expected
    assert fields["own_ref_spread"] == expected


def test_employee_spread_uses_unrounded_cost_and_fixed_fee(api, cfg):
    # prompts.md 3.3: (sig_c * (E - F)) / E, not rounded E or total E scaled.
    cfg["production"]["cost_ref_worth"] = 1
    cfg["production"]["employee"].update(
        fee=0.99, cost_frac=0.014, ref_cost_frac=0.014,
    )
    cfg["production"]["manager"].update(k=2, ref_cost_fixed=3)
    cfg["production"]["sig_c"] = 1
    fields, _ = check_agent(api, cfg, "manager")
    assert fields["emp_fresh_cost"] == "1 credit"
    assert fields["emp_ref_cost"] == "1 credit"
    assert fields["emp_fresh_spread"] == "1%"
    assert fields["emp_ref_spread"] == "1%"
    assert fields["own_fresh_spread"] == "100%"


@pytest.mark.parametrize(
    "gain,expected",
    [(0.000001, "0"), (1.234567, "0.123457"), (4, "0.4"), (10, "1")],
)
def test_refinement_gain_divides_then_rounds(api, cfg, gain, expected):
    # prompts.md 3.3: round(gain / 10, 6), then G.
    for worker in ("employee", "manager"):
        cfg["production"][worker]["gain"] = gain
    fields, _ = check_agent(api, cfg, "manager")
    assert fields["emp_gain"] == expected
    assert fields["own_gain"] == expected


@pytest.mark.parametrize(
    "median,subject,key",
    [
        (1, "employee fresh runs", "emp_fresh_days"),
        (0.7, "employee refinements", "emp_ref_days"),
        (2.5, "manager fresh runs", "own_fresh_days"),
        (1, "manager refinements", "own_ref_days"),
    ],
)
def test_literal_default_durations(api, cfg, median, subject, key):
    # prompts.md 4.4: exact wording, probabilities and Oxford comma.
    assert api.duration_text(cfg, median, subject) == DURATION_DEFAULTS[key]


@pytest.mark.parametrize(
    "median,expected",
    [
        (0.001, "1 day"), (1, "1 day"), (1.0001, "2 days"),
        (2, "2 days"), (2.5, "3 days"), (999, "999 days"),
    ],
)
def test_zero_sigma_duration(api, cfg, median, expected):
    # config.md 6.7; prompts.md 4.4: ceil, no artificial lower or upper tail.
    cfg["production"]["sig_t"] = 0
    assert api.duration_text(cfg, median, "runs") == (
        "With probability 100%, runs take " + expected
    )


def test_literal_split_duration(api, cfg):
    # prompts.md 4.4: high in [1e-4, .005), low positive and one listed count.
    cfg["production"]["sig_t"] = 0.05
    assert api.duration_text(cfg, 2.5, "manager fresh runs") == (
        "With probability more than 99%, manager fresh runs take 3 or fewer days, "
        "and with probability less than 1%, manager fresh runs take 4 or more days"
    )


@pytest.mark.parametrize("sigma", (0.01, 0.02, 0.025, 0.027))
def test_positive_deep_lower_tail_is_not_erf_zero(api, cfg, sigma):
    # prompts.md 2 and 4.4: erfc accuracy preserves "or fewer" deep below -8.3.
    cfg["production"]["sig_t"] = sigma
    assert api.duration_text(cfg, 2.5, "runs") == (
        "With probability 100%, runs take 3 or fewer days"
    )


def test_underflowed_lower_tail_is_exactly_zero(api, cfg):
    # prompts.md 4.4 step 3: low tests the double, not mathematical positivity.
    cfg["production"]["sig_t"] = 0.001
    assert api.duration_text(cfg, 2.5, "runs") == "With probability 100%, runs take 3 days"


@pytest.mark.parametrize("sigma", (0.01, 0.05, 0.1, 0.2, 0.35, 0.6, 1.0, 1.5, 2.5))
@pytest.mark.parametrize("median", (0.2, 0.7, 1.0, 1.4, 2.5, 4.3, 8.7))
def test_duration_bucket_formulas_and_order(api, cfg, sigma, median):
    # prompts.md 4.4; config.md 6.7: thresholds, folding, allocation and ordering.
    cfg["production"]["sig_t"] = sigma
    expected = expected_duration(median, sigma, "custom runs")
    actual = api.duration_text(cfg, median, "custom runs")
    assert actual == expected
    assert not actual.endswith(".")
    if "more than" not in actual:
        percentages = re.findall("probability ([0-9]+)%", actual)
        assert sum(map(int, percentages)) == 100


def test_compensated_high_example(api, cfg):
    # prompts.md 4.4 step 2: the documented Neumaier-versus-plain-sum example.
    cfg["production"]["sig_t"] = 1.5
    median = 5.09489020404268
    actual = api.duration_text(cfg, median, "employee fresh runs")
    assert actual == expected_duration(median, 1.5, "employee fresh runs")
    assert actual.endswith(
        "and with probability 50%, employee fresh runs take 6 or more days"
    )


@pytest.mark.parametrize("median,sigma", ((1, 3), (2, 3), (5, 3)))
def test_duration_includes_the_1000_day_remainder(api, cfg, median, sigma):
    # config.md 6.7; prompts.md 4.4: sentinel contributes to high, never listed.
    cfg["production"]["sig_t"] = sigma
    mix = days_mix(median, sigma)
    assert mix[-1][0] == 1000
    assert mix[-1][1] > 0
    assert api.duration_text(cfg, median, "runs") == expected_duration(median, sigma, "runs")


@pytest.mark.parametrize("median,sigma", ((50, 0.35), (999.0001, 0), (1000, 0)))
def test_unlistable_duration_public_error(api, cfg, median, sigma):
    # prompts.md 4.4 and 7; API explicitly specifies IndexError on this helper.
    cfg["production"]["sig_t"] = sigma
    with pytest.raises(IndexError):
        api.duration_text(cfg, median, "runs")


def test_tiny_median_finite_result_despite_infinite_ratio(api, cfg):
    # config.md 6: an infinite d/t intermediate is permitted when the result is finite.
    median = float.fromhex("0x0.0000000000001p-1022")
    assert api.duration_text(cfg, median, "runs") == (
        "With probability 100%, runs take 1 day"
    )


@pytest.mark.parametrize("role", ROLES)
def test_remaining_scalar_fields_and_large_integer_units(api, cfg, role):
    # prompts.md 3 and 7: each direct field remains parameter-driven.
    changes = {
        "participants.buyers": 4096,
        "participants.managers": 4096,
        "tasks.first_day_tasks": 19,
        "tasks.worth_range": [1234567, 2345678],
        "tests.buyer_sd": 3.1234567,
        "tests.manager_sd": 0.00001,
        "contracts.escrow.arbiter_sd": 0,
        "contracts.escrow.dispute_fee_rate": 1.25,
        "contracts.min_contract_days": 10**30,
        "time.close_after_due_days": 10**25,
        "compute.output_kb": 10**24,
        "compute.code_chars": 123,
        "compute.output_chars": 234,
        "compute.cpu_s": 7,
        "compute.wall_s": 13,
        "turns.memory_chars": 345,
        "turns.max_actions": 9,
        "contracts.message_chars": 456,
        "production.employee.sd_ref": 0.00001,
        "production.manager.sd_ref": 1234567,
        "production.employee.capacity": 17,
        "production.manager.capacity": 23,
    }
    for path, value in changes.items():
        put(cfg, path, value)
    fields, _ = check_agent(api, cfg, role)
    assert fields["min_days_text"] == str(10**30) + " days"
    assert fields["out_kb"] == str(10**24)
    assert fields["arbiter_sd"] == "0"


@pytest.mark.parametrize("role", ROLES)
def test_unstated_settings_do_not_leak_into_prompts(api, cfg, role):
    # prompts.md 1-3; config.md 6.1 and 6.8 are not stated in system prompts.
    baseline = api.system_prompt(cfg, role, "X-fixed", 1, 0)
    for path, value in {
        "time.horizon_min_days": 11,
        "time.horizon_end_prob": 0.2,
        "tasks.due_days_range": [1, 12],
        "compute.memory_mb": 1024,
        "messaging.summary_chars": 3000,
        "view.finished_shown": 7,
        "llm.temperature": 0.2,
        "llm.timeout_s": 123,
    }.items():
        put(cfg, path, value)
    assert api.system_prompt(cfg, role, "X-fixed", 1, 0) == baseline


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("missing,extra", ((True, False), (False, True), (True, True)))
def test_template_field_set_mismatch_is_value_error(api, cfg, monkeypatch, role, missing, extra):
    # prompts.md 1.2; API: mismatches in either direction name file and fields.
    original = source_template(role, "off")
    changed = original
    if missing:
        changed = changed.replace("{agent_id}", "literal ID")
    if extra:
        changed += " {blind_extra_field}"
    monkeypatch.setattr(api, "template", lambda requested_role, mode: changed)
    with pytest.raises(ValueError) as caught:
        api.system_prompt(cfg, role, "X-test", 1, 0)
    message = str(caught.value)
    assert role + "_system_off.txt" in message
    if missing:
        assert "agent_id" in message
    if extra:
        assert "blind_extra_field" in message


@pytest.mark.parametrize("role,mode", (("unavailable_role", "off"), ("buyer", "unavailable_mode")))
def test_missing_template_public_error(api, cfg, role, mode):
    # API template() and system_prompt(): no template means FileNotFoundError.
    with pytest.raises(FileNotFoundError):
        api.template(role, mode)
    cfg["message_channels"] = mode
    with pytest.raises(FileNotFoundError):
        api.system_prompt(cfg, role, "X-test", 1, 0)
