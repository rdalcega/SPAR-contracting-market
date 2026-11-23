"""Analysis of the messages experiment: Qwen3.5-9B markets with message_channels off and all, on the same seeds.

    python experiments/qwen_messages/analyze.py RUNS_ROOT OUT.json [--reps=rep1,rep2] [--partial]

RUNS_ROOT holds rep1/ and rep2/ (the --out folders of the two repetitions), each with
qwen35_9b_reasoning_msgs_{off,all}/seed_12/ to seed_19/. The
output is one JSON document: every episode's figures, the per-metric paired comparison over seeds, and the messages
of the arm with channels on.

Pairing: the market's draws (tasks, worths, due days, the horizon, the turn orders) depend only on the seed, so an
off and an all episode of one seed face the same market. Per seed and arm, the two repetitions are averaged; the
arms are compared on the 8 per-seed differences, with a t interval and an exact sign-flip permutation test.
"""

import itertools
import json
import math
import os
import re
import statistics
import sys

ARMS = {"off": "qwen35_9b_reasoning_msgs_off", "all": "qwen35_9b_reasoning_msgs_all"}

# (key, label, higher is better: True, False or None when neither)
METRICS = [
    ("welfare", "Welfare (credits)", True),
    ("welfare_per_worth", "Welfare / total task worth", True),
    ("buyer_gain_mean", "Buyer score, mean", True),
    ("manager_profit_mean", "Manager score, mean", True),
    ("manager_share", "Managers' share of welfare", None),
    ("tasks_done_rate", "Tasks delivered / created", True),
    ("mean_price_to_worth", "Price / worth, mean over contracts", None),
    ("bid_price_to_worth", "Bid price / worth, mean over bids", None),
    ("bids_per_post", "Bids per posted task", None),
    ("escrow_share", "Escrow share of contracts", None),
    ("mean_delivered_q", "True quality delivered, mean", True),
    ("mean_value_frac", "Realised value / worth, mean", True),
    ("success_rate", "Successful contracts / contracts", True),
    ("late_rate", "Late deliveries / delivered", False),
    ("unpaid_amount", "Unpaid after seen (credits)", False),
    ("disputes", "Disputes", None),
    ("own_run_share", "Own runs / runs", None),
    ("buyer_invalid_action_rate", "Buyer invalid-action rate", False),
    ("manager_invalid_action_rate", "Manager invalid-action rate", False),
    ("buyer_invalid_rate_no_msgs", "Buyer invalid-action rate, messages left out", False),
    ("manager_invalid_rate_no_msgs", "Manager invalid-action rate, messages left out", False),
    ("buyer_failed_per_turn", "Buyer failed actions per turn, messages left out", False),
    ("manager_failed_per_turn", "Manager failed actions per turn, messages left out", False),
    ("token_credits", "Token costs charged to agents (credits)", False),
    ("token_credits_per_turn", "Token credits per turn", False),
    ("output_tokens_per_call", "Output tokens per call", None),
    ("close_day", "Close day", None),
    ("dollars", "Dollars, all calls", False),
    ("summarizer_dollars", "Dollars, summarizer", False),
    ("messages", "Messages sent", None),
]


# Message topics: keyword families, case-insensitive; a message can match several or none. A rough reading aid,
# checked by reading samples, not a classifier.
TOPICS = {
    "delivery": r"\bdeliver|\bsubmit|\bsubmission|\boverdue|\bdeadline",
    "price": r"\bprice|\bpric|\bbid\b|\bbids\b|credits? each|\d\s?cr\b|\bquote",
    "capacity": r"\bcapacity|\bavailable|\bslots?\b|ready for new",
    "payment": r"\bpay\b|\bpayment|\bunpaid|\bowed|\boutstanding",
    "quality": r"\bquality|\breading|\btested",
    "coordination": r"\bagree|coordinat|\bsplit|divide|\bfloor\b|minimum price|undercut|\bcollud|"
                    r"\bavoid (?:competing|bidding)|\byou take|\bi'?ll take",
}


def channel(msg):
    """The message's channel: sender role to recipient role or group."""
    to = {"managers": "all managers", "buyers": "all buyers", "everyone": "everyone"}.get(msg["to"], msg["to_role"])
    return f"{msg['sender_role']} to {to}"


def message_figures(messages):
    """Counts by channel and topic, over the messages of the arm with channels on."""
    by_channel, by_topic, by_channel_topic = {}, {}, {}
    for msg in messages:
        ch = channel(msg)
        by_channel[ch] = by_channel.get(ch, 0) + 1
        for topic, pattern in TOPICS.items():
            if re.search(pattern, msg["text"], re.IGNORECASE):
                by_topic[topic] = by_topic.get(topic, 0) + 1
                by_channel_topic.setdefault(ch, {})[topic] = by_channel_topic.setdefault(ch, {}).get(topic, 0) + 1
    return {"total": len(messages), "by_channel": by_channel, "by_topic": by_topic,
            "by_channel_topic": by_channel_topic}


def read_log(path):
    """The figures an episode's log gives beyond its summary, and its messages."""
    worth = {}
    post_task = {}
    bids = {}
    actions = {}  # (role, is send_message, failed) -> count
    messages = []
    roles = {}
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            t = r["type"]
            if t == "fresh_start":
                roles = {p["id"]: p["role"] for p in r["participants"]}
            elif t == "task":
                worth[r["task"]] = r["worth"]
            elif t == "post":
                post_task[r["post"]] = r["task"]
            elif t == "action":  # each action that ran or was refused (spec/running.md 6.3 E)
                key = (r["role"], r["name"] == "send_message", r["status"] != "OK")
                actions[key] = actions.get(key, 0) + 1
            elif t == "bid":  # placed or updated: an update is the same bid (spec/running.md 6.7), keep its latest
                bids[r["bid"]] = (r["post"], r["price"])
            elif t == "message":
                messages.append({"day": r["day"], "round": r["round"], "sender": r["sender"],
                                 "sender_role": roles.get(r["sender"]), "to": r["to"],
                                 "to_role": roles.get(r["to"], r["to"]), "text": r["text"],  # as delivered
                                 "cut": len(r["sent"]) > len(r["text"])})
    ratios = []
    for post, price in bids.values():
        ratios.append(price / worth[post_task[post]])
    return {"total_worth": sum(worth.values()), "bid_price_to_worth": statistics.fmean(ratios) if ratios else None,
            "bids_logged": len(bids), "actions": actions}, messages


def episode(folder, arm, rep, seed):
    """One episode's row and messages, after checking that its folder holds the episode it is filed as."""
    s = json.load(open(os.path.join(folder, "summary.json")))
    if (s["name"], s["seed"], s["message_channels"]) != (ARMS[arm], seed, arm):
        raise SystemExit(f"{folder}: summary says {s['name']}, seed {s['seed']}, {s['message_channels']}")
    row = {"arm": arm, "rep": rep, "seed": seed, "status": s["status"], "days_completed": s["days_completed"]}
    if s["status"] != "ok":
        row["error"] = (s.get("error") or "")[-300:]
        return row, []
    m = s["metrics"]
    extra, messages = read_log(os.path.join(folder, "log.jsonl"))
    if extra["bids_logged"] != m["bids"]:
        raise SystemExit(f"{folder}: {extra['bids_logged']} bids in the log, {m['bids']} in the metrics")
    acts = extra["actions"]
    for role in ("buyer", "manager"):
        logged = sum(n for (r, _, _), n in acts.items() if r == role)
        failed = sum(n for (r, _, f), n in acts.items() if r == role and f)
        if (logged, failed) != (m[f"{role}_actions"], m[f"{role}_failed_actions"]):
            raise SystemExit(f"{folder}: {role} actions {logged}/{failed} failed in the log, "
                             f"{m[f'{role}_actions']}/{m[f'{role}_failed_actions']} in the metrics")
    managers = [x["score"] for x in s["scores"] if x["role"] == "manager"]
    turns = m["buyer_turns"] + m["manager_turns"]
    calls = m["buyer_calls"] + m["manager_calls"]
    delivered = m["plain_delivered"] + m["escrow_released"]

    def rate(a, b):
        return a / b if b else None

    row.update({
        "close_day": s["close_day"], "last_arrival_day": s["last_arrival_day"], "close_reason": s["close_reason"],
        "welfare": s["welfare"], "total_worth": extra["total_worth"],
        "welfare_per_worth": rate(s["welfare"], extra["total_worth"]),
        "buyer_gain_mean": m["buyer_gain_mean"], "manager_profit_mean": m["manager_profit_mean"],
        "manager_share": rate(sum(managers), s["welfare"]),
        "tasks_created": m["tasks_created"], "tasks_done": m["tasks_done"],
        "tasks_done_rate": rate(m["tasks_done"], m["tasks_created"]),
        "contracts": m["contracts"], "mean_price_to_worth": m["mean_price_to_worth"],
        "bid_price_to_worth": extra["bid_price_to_worth"], "bids": m["bids"],
        "bids_per_post": rate(m["bids"], m["tasks_posted"]),
        "escrow_share": rate(m["contracts_escrow"], m["contracts"]),
        "mean_delivered_q": m["mean_delivered_q"], "mean_value_frac": m["mean_value_frac"],
        "success_rate": rate(m["successful_contracts"], m["contracts"]),
        "late_rate": rate(m["late_deliveries"], delivered),
        "unpaid_amount": m["unpaid_amount"], "disputes": m["disputes"],
        "own_run_share": rate(m["own_runs"], m["runs_started"]),
        "buyer_invalid_action_rate": m["buyer_invalid_action_rate"],
        "manager_invalid_action_rate": m["manager_invalid_action_rate"],
        "token_credits": m["costs"]["tokens"], "token_credits_per_turn": rate(m["costs"]["tokens"], turns),
        "output_tokens_per_call": rate(m["buyer_output_tokens"] + m["manager_output_tokens"], calls),
        "dollars": m["dollars"], "summarizer_dollars": m["summarizer_dollars"], "messages": m["messages"],
        "message_kinds": {k: m[k] for k in m if k.startswith("messages_")},
        "out_of_business_events": m["out_of_business_events"],
        "lost_turns": sum(m[f"{r}_turns_lost_{w}"] for r in ("buyer", "manager") for w in ("provider", "format")),
        "wall_h": (s.get("wall_s") or 0) / 3600,
    })
    for role in ("buyer", "manager"):
        # The spec's invalid-action rate counts send_message actions, which nearly always succeed, in its
        # denominator; these two leave messages out, so they compare the arms on the same kinds of action.
        other = sum(n for (r, msg, _), n in acts.items() if r == role and not msg)
        other_failed = sum(n for (r, msg, f), n in acts.items() if r == role and not msg and f)
        fe = m[f"{role}_format_errors"]
        row[f"{role}_invalid_rate_no_msgs"] = rate(other_failed + fe, other + fe)
        row[f"{role}_failed_per_turn"] = rate(other_failed + fe, m[f"{role}_turns"])
    for msg in messages:
        msg.update(rep=rep, seed=seed)
    return row, messages


def t_quantile_975(df):
    """The 0.975 quantile of Student's t with df degrees of freedom (df <= 30 tabulated, else normal)."""
    table = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145,
             2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048,
             2.045, 2.042]
    return table[df - 1] if df <= 30 else 1.96


def paired(rows, key, reps_used):
    """The per-seed paired comparison of `key`: all minus off, each arm's value the mean of its repetitions.

    A seed counts for a metric only when every repetition of both arms gives it a value (a rate with an empty
    denominator gives none), so each per-seed difference compares the same design; `excluded` names the others.
    `reps` holds, per seed and arm, the values in `reps_used` order (None where missing)."""
    reps = {}
    for r in rows:
        if r["status"] == "ok":
            reps.setdefault(r["seed"], {}).setdefault(r["arm"], {})[r["rep"]] = r.get(key)
    table = {s: {arm: [reps.get(s, {}).get(arm, {}).get(rep) for rep in reps_used] for arm in ARMS} for s in SEEDS}
    seeds = sorted(s for s, v in table.items()
                   if all(x is not None for arm in ARMS for x in v.get(arm, [None])))
    off = [statistics.fmean(table[s]["off"]) for s in seeds]
    on = [statistics.fmean(table[s]["all"]) for s in seeds]
    diffs = [b - a for a, b in zip(off, on)]
    out = {"seeds": seeds, "excluded": sorted(set(table) - set(seeds)), "off": off, "all": on, "diffs": diffs,
           "reps": {str(s): table[s] for s in sorted(table)}}
    if len(diffs) < 2:
        return out
    mean = statistics.fmean(diffs)
    sd = statistics.stdev(diffs)
    half = t_quantile_975(len(diffs) - 1) * sd / math.sqrt(len(diffs))
    extreme = sum(1 for signs in itertools.product((1, -1), repeat=len(diffs))
                  if abs(sum(sg * d for sg, d in zip(signs, diffs))) >= abs(sum(diffs)) - 1e-12)
    # Repetition noise: the spread between the two repetitions of one seed and arm, pooled.
    gaps = [abs(v[0] - v[1]) for s in seeds for v in table[s].values()] if len(reps_used) == 2 else []
    base = abs(statistics.fmean(off))  # relative change is against |off|, so its sign is the direction of change
    out.update({"mean_off": statistics.fmean(off), "mean_all": statistics.fmean(on), "mean_diff": mean,
                "ci95": [mean - half, mean + half], "p_signflip": extreme / 2 ** len(diffs),
                "seeds_all_higher": sum(d > 0 for d in diffs), "rep_gap_mean": statistics.fmean(gaps) if gaps else None,
                "rel_diff": mean / base if base else None,
                "rel_ci95": [(mean - half) / base, (mean + half) / base] if base else None})
    return out


REPS = ("rep1", "rep2")
SEEDS = range(12, 20)


def main(root, out, *flags):
    """`--reps=rep1` limits the analysis to the repetitions named (default: REPS). `--partial` accepts missing or
    unfinished episodes (for a look before the runs end); otherwise every (repetition, arm, seed) of the design must
    be there and ok."""
    reps_used = REPS
    for flag in flags:
        if flag.startswith("--reps="):
            reps_used = tuple(flag.removeprefix("--reps=").split(","))
    rows, messages, models, missing = [], [], {}, []
    for rep in reps_used:
        for arm, name in ARMS.items():
            for seed in SEEDS:
                folder = os.path.join(root, rep, name, f"seed_{seed}")
                if not os.path.exists(os.path.join(folder, "summary.json")):
                    missing.append(f"{rep}/{name}/seed_{seed}: no summary")
                    continue
                row, msgs = episode(folder, arm, rep, seed)
                if row["status"] != "ok":
                    missing.append(f"{rep}/{name}/seed_{seed}: {row['status']}")
                rows.append(row)
                messages += msgs
                cfg = json.load(open(os.path.join(folder, "config.json")))
                models[json.dumps(cfg["models"], sort_keys=True)] = folder
    if len(models) > 1:
        raise SystemExit(f"the episodes ran different models: {sorted(models.values())}")
    if missing and "--partial" not in flags:
        raise SystemExit("incomplete design (pass --partial to look anyway):\n" + "\n".join(missing))
    comparison = [{"key": k, "label": label, "better": better, **paired(rows, k, reps_used)} for k, label, better in METRICS]
    json.dump({"episodes": rows, "comparison": comparison, "message_figures": message_figures(messages),
               "messages": messages}, open(out, "w"), indent=1)
    for c in comparison:
        if "mean_diff" in c:
            print(f"{c['label']:42} off {c['mean_off']:10.4f} all {c['mean_all']:10.4f} diff {c['mean_diff']:+10.4f} "
                  f"CI [{c['ci95'][0]:+.4f}, {c['ci95'][1]:+.4f}] p {c['p_signflip']:.3f} "
                  f"all>off {c['seeds_all_higher']}/{len(c['diffs'])} rep-gap {c['rep_gap_mean']}")
    bad = [r for r in rows if r["status"] != "ok"]
    print(f"{len(rows)} episodes, {len(bad)} not ok, {len(messages)} messages")


if __name__ == "__main__":
    main(*sys.argv[1:])
