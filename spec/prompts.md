# System prompts

This file specifies the system prompt each buyer and each manager receives: which template file it comes from, how the
template's placeholders are filled, the exact definition and format of every field for any valid configuration, what
the `message_channels` modes change, the wording that looks accidental but must be kept, and the configuration range
checks the fixed wording relies on. The templates themselves are the ten files in `spec/text/`; their bytes are
normative. Number formats (`G`, `CREDITS`, `F1`, `F2`, `PCT`, `PCT0`, `FRAC`, `INT`), `round(x, n)` and the
singular rule are defined in README.md, "Number formats and rounding"; parameters are written `section.key` as in
config.md.

## 1. How a system prompt is built

1. **Template.** The template is the file `spec/text/<role>_system_<mode>.txt`, where `<role>` is the agent's role
   (`buyer` or `manager`) and `<mode>` is the episode's `message_channels` value (`off`, `managers`, `buyers`,
   `within_roles` or `all`). The ten files are pure ASCII, use `\n` line breaks, and do not end with a newline.
   Paragraphs are separated by one empty line; the numbered list that closes each file has one item per line.
2. **Placeholders.** A placeholder is `{name}`, where `name` is made of lowercase letters, digits and underscores. The
   templates contain no other `{` or `}` characters. Every placeholder in a file must be replaced by its field's
   value (section 3); a file never contains a placeholder this file does not define. Replacement is a single pass:
   text inserted for a placeholder is not scanned again. Nothing else is changed: no text is added, trimmed or
   re-wrapped, and the rendered prompt has no trailing newline. No field value contains a line break.
3. **One text per agent per episode.** Each agent's system prompt is the same text, byte for byte, in every model
   call of the episode, also after the episode is resumed. Every input it depends on (the configuration, the agent's
   ID, role, seat, wealth share and model spec) is fixed when the episode starts.
4. **Use.** The rendered prompt must be the first message, with role `system`, of every model call the agent makes in
   the episode: each turn's calls and each correction call (turns.md, "The conversation's messages"). It is sent
   exactly as rendered; no other text is ever inserted into it or appended to it.

## 2. Conventions for the field definitions

- "The agent" is the buyer or manager whose prompt is being built. Its ID, seat, wealth share and starting balance,
  and a buyer's per-task budget, are defined in episode.md, "Participants, IDs and seat order" and "Wealth shares,
  start balances and per-task budgets".
- "The agent's model spec" is its seat's entry in `models.buyer_seats` / `models.manager_seats` if there is one,
  else `models.buyers` / `models.managers` (config.md, "`models`").
- All arithmetic is IEEE-754 double precision, evaluated in the order written (left to right inside a product or
  sum), except the two compensated sums `below` and `high` of section 4.4, step 2.
- `Φ` is the standard normal CDF and `φ` the standard normal density, `φ(x) = exp(−x·x/2) / √(2π)`. `Φ` must be
  accurate relative to its own value also deep in the lower tail, where it is tiny but still a positive double (down
  to about x = −38): `erfc(−x / √2) / 2` is (the reference implementation computes it so); `(1 + erf(x / √2)) / 2` is
  not, as it gives 0 below about x = −8.3, which with narrow duration laws (`production.sig_t` up to about 0.027 at
  the default medians, and up to about 0.1 at some other medians) turns "<n> or fewer days" into "<n> days" (section
  4.4, step 3).
- **Accuracy of the math functions.** `erfc`, `exp`, `ln` (natural logarithm) and the power are double-precision
  functions accurate to a few units in the last place (correct rounding is not required); `√` and the four basic
  operations are IEEE-754 correctly rounded. At the default configuration every figure they feed is far from the
  point where its format or a threshold of section 4.4 would change the text (the closest: `value_list`'s 44.444...
  for q = 7, 1.2e-4 relative from 44.45), so the default prompts render byte for byte. For other values, a figure
  within a few units in the last place of such a point may render either way; that is accepted.
- **Counts with units.** A field whose value is given as "..., unit <u>" (each named `<x>_text`) is a count
  followed by its unit: the count in the format its row gives, one space, then the unit (`day`, `second`,
  `character`, `action` or `credit`) under README.md's singular rule: singular exactly when the count as written
  is `1`, so `1.00` keeps the plural. Each such row is marked *(singular rule)*.
  Examples: `4 days`, `1 day`, `20000 characters`, `1 action`, `60 credits`, `1 credit`.
- Every field's "default" is its rendering under the default configuration; an agent-specific default is given for
  seed 0's first buyer (B-c53) and first manager (M-14b).

## 3. The fields

### 3.1 Fields in both roles' templates

| field | value | format | default |
|---|---|---|---|
| `n_buyers` | `participants.buyers` | `INT` | `2` |
| `n_managers` | `participants.managers` | `INT` | `3` |
| `rounds_text` | section 4.5 | text | `3 rounds` |
| `first_day_tasks` | `tasks.first_day_tasks` | `INT` | `2` |
| `arrival_law` | section 4.5 | text | `(1/2)^(n+1)` |
| `prob0` | 1 − p, with p = `tasks.arrival_p` | `FRAC` | `1/2` |
| `prob1` | (1 − p) × p | `FRAC` | `1/4` |
| `prob2` | (1 − p) × p × p | `FRAC` | `1/8` |
| `share_lo` | `money.share_range[0]` | `G` | `0.6` |
| `share_hi` | `money.share_range[1]` | `G` | `1.4` |
| `start_std` | `money.start_amount` | `G` | `60` |
| `start_std_text` | `money.start_amount`, unit credit | `G`, unit *(singular rule)* | `60 credits` |
| `agent_id` | the agent's ID | as is | `B-c53` / `M-14b` |
| `share` | the agent's wealth share | `F2` | `1.36` / `1.35` |
| `start_balance` | the agent's starting balance (episode.md, "1.3 Wealth shares, start balances and per-task budgets") | `F2` | `81.60` / `81.00` |
| `buyer_sd` | `tests.buyer_sd` | `G` | `2` |
| `buyer_test_cost` | `tests.buyer_cost` | `CREDITS` | `1 credit` |
| `manager_sd` | `tests.manager_sd` | `G` | `0.5` |
| `manager_test_cost` | `tests.manager_cost` | `CREDITS` | `1 credit` |
| `decimal_places` | with d = `tests.decimals`: `INT` of d, then ` decimal place`, then `s` unless d = 1 | text | `1 decimal place` |
| `q0` | `value.q0` | `G` | `1` |
| `q_span` | 10 − `value.q0` | `G` | `9` |
| `exponent` | `value.exponent` | `G` | `2` |
| `value_list` | section 4.2 | text | `1 -> 0.0%, 2 -> 1.2%, ...` (section 4.2) |
| `late_decay` | `value.late_decay` | `G` | `0.2` |
| `late_pct` | `value.late_decay` | `PCT` | `20%` |
| `min_days_text` | `contracts.min_contract_days`, unit day | `INT`, unit *(singular rule)* | `4 days` |
| `cap_days_text` | escrow_close_days − 1 (section 4.1), unit day | `INT`, unit *(singular rule)* | `4 days` |
| `fee_pct` | `contracts.escrow.fee_rate` | `PCT` | `10%` |
| `review_days_text` | `contracts.escrow.review_days`, unit day | `INT`, unit *(singular rule)* | `2 days` |
| `dispute_window_b` | section 4.5 | text | `throughout the day of the buyer's rejection and the day after` |
| `dispute_until` | section 4.5 | text | `the day after it` |
| `dispute_pct` | `contracts.escrow.dispute_fee_rate` | `PCT` | `50%` |
| `arbiter_sd` | `contracts.escrow.arbiter_sd` | `G` | `0.5` |
| `escrow_close_text` | escrow_close_days (section 4.1), unit day | `INT`, unit *(singular rule)* | `5 days` |
| `grace_length` | section 4.5 | text | `one round` |
| `grace_examples` | section 4.5 | text | section 4.5 |
| `cpu_s_text` | `compute.cpu_s`, unit second | `INT`, unit *(singular rule)* | `5 seconds` |
| `wall_s_text` | `compute.wall_s`, unit second | `INT`, unit *(singular rule)* | `10 seconds` |
| `out_kb` | `compute.output_kb` | `INT` | `64` |
| `code_chars_text` | `compute.code_chars`, unit character | `INT`, unit *(singular rule)* | `20000 characters` |
| `out_chars_text` | `compute.output_chars`, unit character | `INT`, unit *(singular rule)* | `4000 characters` |
| `max_actions` | `turns.max_actions` (bare, twice: "Actions beyond the first {max_actions} are dropped.", "(up to {max_actions} if their act call couldn't be read)"; no unit follows, so the singular rule does not apply) | `INT` | `6` |
| `max_actions_text` | `turns.max_actions`, unit action | `INT`, unit *(singular rule)* | `6 actions` |
| `memory_chars_text` | `turns.memory_chars`, unit character | `INT`, unit *(singular rule)* | `2000 characters` |
| `msg_chars_text` | `contracts.message_chars`, unit character | `INT`, unit *(singular rule)* | `500 characters` |
| `price_in_text` | the agent's model spec's `price_in_per_m` × `credits_per_usd` (`price_in_per_m` taken as 0 if the spec has none, which config.md, "A model spec", allows only for the free providers `mock` and `ollama`), unit credit | `G`, unit *(singular rule)* | `0.15 credits` |
| `price_out_text` | the agent's model spec's `price_out_per_m` × `credits_per_usd` (0 likewise), unit credit | `G`, unit *(singular rule)* | `1.875 credits` |
| `close_after_text` | `time.close_after_due_days`, unit day | `INT`, unit *(singular rule)* | `5 days` |
| `n_sections` | the number of items in the numbered list that ends the template file (section 5) | `INT` | `11` |

### 3.2 Fields only in the buyer's template

| field | value | format | default |
|---|---|---|---|
| `budget_std` | `money.task_budget` | `G` | `30` |
| `budget_std_text` | `money.task_budget`, unit credit | `G`, unit *(singular rule)* | `30 credits` |
| `budget` | the buyer's per-task budget (episode.md, "1.3 Wealth shares, start balances and per-task budgets") | `F2` | `40.80` (B-c53) |

### 3.3 Fields only in the manager's template

| field | value | format | default |
|---|---|---|---|
| `worth_lo` | `tasks.worth_range[0]` | `G` | `40` |
| `worth_hi_text` | `tasks.worth_range[1]`, unit credit | `G`, unit *(singular rule)* | `80 credits` |
| `dispute_window_m` | section 4.5 | text | `throughout the day of the rejection and the day after` |
| `own_busy` | section 4.5 | text | `a run` |

Each worker has the same seventeen fields, with prefix `emp` for the employee (parameters
`production.employee.*`, subject words "employee") and prefix `own` for the manager's own work
(`production.manager.*`, subject words "manager"). Below, `w` is the worker's parameter block and
ref = `production.cost_ref_worth`.

| field | value | format | default `emp` / `own` |
|---|---|---|---|
| `<prefix>_mean` | the mean of Normal(w.mu, w.sd) truncated to [0, 10] (section 4.3) | `F1` | `5.8` / `7.0` |
| `<prefix>_mu` | w.mu | `G` | `6` / `7` |
| `<prefix>_sd` | w.sd | `G` | `2.5` / `1.2` |
| `<prefix>_reach5` ... `<prefix>_reach9` | for q = 5, 6, 7, 8, 9: the share of that truncated normal at or above q (section 4.3) | `PCT0` | `64%`, `48%`, `31%`, `17%`, `6%` / `95%`, `80%`, `50%`, `20%`, `4%` |
| `<prefix>_fresh_days` | the duration text (section 4.4) of the law (w.t_med, `production.sig_t`), subject `employee fresh runs` / `manager fresh runs` | text | section 4.4 |
| `<prefix>_fresh_cost` | the expected fresh-run cost: employee `w.fee + w.cost_frac × ref`; manager `w.k × ref`; then round(·, 2) | `CREDITS` | `4 credits` / `8 credits` |
| `<prefix>_fresh_spread` | how much a fresh run's actual cost typically varies either way: `production.sig_c` × (E − F) / E, with E the expected fresh-run cost before rounding and F its part the cost multiplier does not scale (employee `w.fee`; manager 0) | `PCT0` | `19%` / `25%` |
| `<prefix>_gain` | round(w.gain / 10, 6) | `G` | `0.4` / `0.7` |
| `<prefix>_sd_ref` | w.sd_ref | `G` | `1.5` / `0.8` |
| `<prefix>_ref_days` | the duration text of the law (w.t_ref_med, `production.sig_t`), subject `employee refinements` / `manager refinements` | text | section 4.4 |
| `<prefix>_ref_cost` | the expected refinement cost: employee `w.fee + w.ref_cost_frac × ref`; manager `w.ref_cost_fixed`; then round(·, 2) | `CREDITS` | `2.8 credits` / `14 credits` |
| `<prefix>_ref_spread` | the same for a refinement: `production.sig_c` × (E − F) / E, with E the expected refinement cost before rounding and F as for `<prefix>_fresh_spread` | `PCT0` | `16%` / `25%` |
| `<prefix>_runs` | with n = w.capacity: `INT` of n, then ` run`, then `s` unless n = 1 | text | `2 runs` / `1 run` |

The expected costs are the run costs of processes.md, "The laws of a run", with the cost multiplier at its mean, 1
(config.md, "Expected run costs"). `CREDITS` applies to the rounded value, so its "credit"/"credits" choice looks
at the rounded value. A spread is computed in the order written, (`production.sig_c` × (E − F)) / E, from the
unrounded E (default employee: 0.25 × 3 / 4 = 0.1875 and 0.25 × 1.8 / 2.8 = 0.1607...); E is above 0 (section 7).
The manager's view states the same four spreads (view-contracts.md, "The manager's "Your production"").

### 3.4 Message fields: only in a template whose role can send messages

These two fields occur only in the files where the role has `send_message` (section 5: the manager in `managers`,
the buyer in `buyers`, both in `within_roles` and `all`).

| field | value | format | default |
|---|---|---|---|
| `mm_chars_text` | `messaging.message_chars`, unit character | `INT`, unit *(singular rule)* | `500 characters` |
| `mm_shown` | the sentence below, with k = `messaging.recent` | text | below, k = 10 |

`mm_shown`, where `{k}` is `INT` of k:

```
Each conversation shows its messages in full, up to {k} of them. Whenever a conversation has more than {k} messages, the market replaces its earliest messages, in groups of {k}, with a summary written automatically by the market (covering any earlier summary too), so the conversation shows that summary and its latest 1 to {k} messages.
```

At the default it reads "... up to 10 of them. Whenever a conversation has more than 10 messages, the market
replaces its earliest messages, in groups of 10, with a summary ...". messaging.md, "Batching and summaries",
specifies the behaviour this sentence describes, for any pattern of arrivals.

## 4. Computations

### 4.1 escrow_close_days

escrow_close_days = ceil(1 / `value.late_decay` − 1e-9), the number of days after a task's due day that gives its
escrow's close day: from the start of that day, an escrow still holding its price closes unreleased once nothing is
pending (processes.md, "6.9 The lateness close"; config.md, "6.2 escrow_close_days"). Default 5.
`{escrow_close_text}` states it and `{cap_days_text}` states escrow_close_days − 1 (default 4), the last day after the
due day on which a contract's deadline can fall.

### 4.2 value_list

For q = 1, 2, ..., 10: f(q) = (max(0, min(1, (q − `value.q0`) / (10 − `value.q0`))))^`value.exponent` (the power as
C's `pow`), and the item is `INT` of q, then ` -> `, then `F1` of 100 × f(q), then `%`. The ten items are joined
with `, `. Default:

```
1 -> 0.0%, 2 -> 1.2%, 3 -> 4.9%, 4 -> 11.1%, 5 -> 19.8%, 6 -> 30.9%, 7 -> 44.4%, 8 -> 60.5%, 9 -> 79.0%, 10 -> 100.0%
```

### 4.3 The workers' fresh-run figures

With mu = w.mu, sd = w.sd, a = (0 − mu) / sd and b = (10 − mu) / sd:

- mean = mu + sd × (φ(a) − φ(b)) / (Φ(b) − Φ(a)); `{<prefix>_mean}` is its `F1`.
- for q = 5, 6, 7, 8, 9: share(q) = (Φ(b) − Φ((q − mu) / sd)) / (Φ(b) − Φ(a)); `{<prefix>_reach<q>}` is its
  `PCT0`.

These are the mean and the upper-tail shares of the fresh-run quality law in processes.md, "The laws of a run" (a
normal truncated to [0, 10]). Default values before rounding: employee mean 5.7638, manager mean 6.9788.

Both formulas divide by Φ(b) − Φ(a). For a very large sd (for example sd = 1e17 with mu = 6) it is 0 in double
precision and the fields are undefined; such a configuration is refused (section 7).

### 4.4 Run durations: the duration text

**Shares.** The share list of a law with median t_med and log-sd sig_t (= `production.sig_t`) is its days mix,
config.md, "6.7 A run's days mix" (Φ as in section 2): the (d, share) pairs that computation produces, in increasing
d, with d = 1000 if it adds one; counts after its loop stops are not in the list.

**Buckets.**

1. listed = the counts d ≠ 1000 in the share list whose share is at least 0.05. If none, the law cannot be stated:
   the configuration is refused (section 7). first = the smallest listed count, last = the largest. Every count from
   first to last is stated, whatever its share.
2. below = the sum of the shares of the counts before first, and high = the sum of the shares of the counts after
   last (including d = 1000 if present). Each is the `SUM` (as README.md, "Number formats and rounding", defines it)
   of its shares in increasing d. Plain successive additions are not equivalent: they can differ in the last bit (with
   `production.sig_t` 1.5 and `production.employee.t_med` 5.09489020404268, all else default, high after 5 days is
   0.5050000000000001 by `SUM` and 0.505 by plain additions). Under step 6 such a difference moves a percent only
   when it crosses a floor or changes the order of two remainders; in that example both values give the same
   percents (the employee's last fresh-run clause reads `and with probability 50%, employee fresh runs take 6 or
   more days`), and no example where they differ is known, but the `SUM` is the rule.
3. low = below > 0 (exactly: below is 0 when sig_t = 0, or when every share before first is 0 in double precision);
   tail = high ≥ 0.005; up = (not tail) and high ≥ 1e-4; split = up and low and first = last.
4. For each d from first to last, in order, a bucket:
   - its share p = (the share of d) + (below if d = first, else 0) + (high if d = last and not tail and not split,
     else 0), added left to right;
   - its name: `<d> or fewer days` if d = first and low; otherwise `<d> or more days` if d = last and up and not
     split; otherwise `1 day` if d = 1, else `<d> days` (d as `INT`).
5. Then, if tail: one more bucket named `<last+1> or more days` with share p = high. Otherwise, if split: one more
   bucket named `<last+1> or more days`. Otherwise no more bucket (a remaining high below 1e-4 gets no bucket or "or
   more" name of its own; step 4 has already added it to the last bucket's share).
6. The percents. If split (whose high is below 0.005): the bucket of step 4 (the only one) has the percent `more than
   99%` and the bucket of step 5 the percent `less than 1%`. Otherwise the whole percents add up to 100: each bucket
   gets n = floor(100 × p) (100 × p a double); the points still missing, 100 minus the sum of the n, go one each to
   the buckets with the largest remainders 100 × p − n, ties to the earlier bucket; then, if tail and the bucket of
   step 5 has 0, it gets 1, taken from the bucket with the largest percent (the earliest such). Each bucket's percent
   is its n (`INT`) followed by `%`.

**Wording.** Each bucket gives the clause `with probability <percent>, <subject> take <name>`. One clause stands
alone; two are joined by `, and `; three or more are joined by `, ` with `, and ` before the last. The first character
of the result is upper-cased (`W`). There is no final period (the template has it).

Default renderings (sig_t 0.35; employee t_med 1.0 and t_ref_med 0.7; manager t_med 2.5 and t_ref_med 1.0), each
block the field's exact value:

`emp_fresh_days`:

```
With probability 50%, employee fresh runs take 1 day, with probability 48%, employee fresh runs take 2 days, and with probability 2%, employee fresh runs take 3 or more days
```

`emp_ref_days`:

```
With probability 85%, employee refinements take 1 day, and with probability 15%, employee refinements take 2 or more days
```

`own_fresh_days`:

```
With probability 26%, manager fresh runs take 2 or fewer days, with probability 44%, manager fresh runs take 3 days, with probability 21%, manager fresh runs take 4 days, with probability 7%, manager fresh runs take 5 days, and with probability 2%, manager fresh runs take 6 or more days
```

`own_ref_days`:

```
With probability 50%, manager refinements take 1 day, with probability 48%, manager refinements take 2 days, and with probability 2%, manager refinements take 3 or more days
```

The shares behind them: t_med 1.0: 1: 0.5000, 2: 0.4762, 3: 0.0230, 4: 0.0008, ... (tail after 2 = 0.0238 ≥ 0.005,
its own bucket); t_med 0.7: 1: 0.8459, 2: 0.1527, 3: 0.0013, ... (high after 2 = 0.0014 < 0.005 and ≥ 1e-4: folded
into "2 or more days"); t_med 2.5: 1: 0.0044 (folded into "2 or fewer days"), 2: 0.2575, 3: 0.4369, 4: 0.2116, 5:
0.0658, then 0.0237 in all (its own bucket).

The split case, for example `production.sig_t` 0.05 with t_med 2.5 (`own_fresh_days`):

```
With probability more than 99%, manager fresh runs take 3 or fewer days, and with probability less than 1%, manager fresh runs take 4 or more days
```

### 4.5 The generalised sentences

These eight fields word settings whose sentences change shape with their value. Write R =
`time.rounds_per_day`, W = `contracts.bid_window_rounds`, D = `contracts.escrow.dispute_window_days` (D ≥ 1),
C = `production.manager.capacity`, p = `tasks.arrival_p`. Numbers inside these texts are `INT` (fractions `FRAC`).

| field | rule | default |
|---|---|---|
| `rounds_text` | `<R> round`, then `s` unless R = 1 | `3 rounds` |
| `grace_length` | W as a word if 1 ≤ W ≤ 10 (`one`, `two`, `three`, `four`, `five`, `six`, `seven`, `eight`, `nine`, `ten`), else `INT` of W; then ` round`, then `s` unless W = 1 | `one round` |
| `grace_examples` | below | below |
| `arrival_law` | if p = 0.5 exactly: `(1/2)^(n+1)`; otherwise `(<FRAC of 1 − p>) x (<FRAC of p>)^n` | `(1/2)^(n+1)` |
| `dispute_window_b` | `throughout the day of the buyer's rejection` then, if D = 1, ` and the day after`, else ` and the <D> days after` | `throughout the day of the buyer's rejection and the day after` |
| `dispute_until` | if D = 1: `the day after it`; else `the day <D> days after it` | `the day after it` |
| `dispute_window_m` | `throughout the day of the rejection` then, if D = 1, ` and the day after`, else ` and the <D> days after` | `throughout the day of the rejection and the day after` |
| `own_busy` | if C = 1: `a run`; else `<C> runs` | `a run` |

Examples: p = 0.4 gives `(3/5) x (2/5)^n` (with prob0 `3/5`, prob1 `6/25`, prob2 `12/125`); D = 2 gives `throughout
the day of the buyer's rejection and the 2 days after` and `the day 2 days after it`; C = 2 gives `2 runs`.

**grace_examples.** Number the rounds from a post's day on: position i = 1 is round 1 of the post's day, i = R + 1
round 1 of the next day, and so on, so position i is round r = ((i − 1) mod R) + 1 of the day k = floor((i − 1) / R)
days after the post's day. The day phrase of k is `that day` (k = 0), `the next day` (k = 1), or `the day <k> days
later` (k ≥ 2).

- The span text of positions s..e (s ≤ e) groups the positions by their day k, in order; a group whose rounds run
  from a to b reads `round <a>` if a = b, `rounds <a> and <b>` if b = a + 1, else `rounds <a> to <b>`, followed by
  ` of ` and the day phrase of its k. One group stands alone; several are joined by `, ` with ` and ` (no comma)
  before the last.
- For each round r = 1..R of posting, the grace period is positions r + 1 .. r + W, and its clause is
  `if a buyer posts a task in round <r> of a day, the grace period is <span text of r + 1 .. r + W>`. The clause
  "wraps" when position r + W is on a later day (k ≥ 1).
- Sentence 1: `Specifically, ` + clause 1, then for r = 2..R: `; but ` before clause r if clause r wraps and clause
  r − 1 does not, else `; `, then clause r; then `.`.
- Sentence 2: with (k, r') the day and round of position r + W + 1 (the first round a bid can be accepted), the
  acceptance text of r is `round <r'> of <day phrase of k>`. The sentence is `So bids on a post made in round 1 can
  first be accepted in ` + acceptance text of 1, then for r = 2..R: `, ` + (`and ` if r = R) + `on a post made in
  round <r> in ` + acceptance text of r; then `.`.
- `{grace_examples}` = sentence 1 + one space + sentence 2.

Default (R = 3, W = 1):

```
Specifically, if a buyer posts a task in round 1 of a day, the grace period is round 2 of that day; if a buyer posts a task in round 2 of a day, the grace period is round 3 of that day; but if a buyer posts a task in round 3 of a day, the grace period is round 1 of the next day. So bids on a post made in round 1 can first be accepted in round 3 of that day, on a post made in round 2 in round 1 of the next day, and on a post made in round 3 in round 2 of the next day.
```

R = 2, W = 1:

```
Specifically, if a buyer posts a task in round 1 of a day, the grace period is round 2 of that day; but if a buyer posts a task in round 2 of a day, the grace period is round 1 of the next day. So bids on a post made in round 1 can first be accepted in round 1 of the next day, and on a post made in round 2 in round 2 of the next day.
```

R = 4, W = 2:

```
Specifically, if a buyer posts a task in round 1 of a day, the grace period is rounds 2 and 3 of that day; if a buyer posts a task in round 2 of a day, the grace period is rounds 3 and 4 of that day; but if a buyer posts a task in round 3 of a day, the grace period is round 4 of that day and round 1 of the next day; if a buyer posts a task in round 4 of a day, the grace period is rounds 1 and 2 of the next day. So bids on a post made in round 1 can first be accepted in round 4 of that day, on a post made in round 2 in round 1 of the next day, on a post made in round 3 in round 2 of the next day, and on a post made in round 4 in round 3 of the next day.
```

R = 1, W = 1:

```
Specifically, if a buyer posts a task in round 1 of a day, the grace period is round 1 of the next day. So bids on a post made in round 1 can first be accepted in round 1 of the day 2 days later.
```

## 5. What each message_channels mode changes

The files hold the full text of every mode; this section says in words what differs from `off`, so the implementer
can check the right file is chosen. messaging.md, "The five modes", specifies who can send to whom.

| mode | buyer can send | manager can send |
|---|---|---|
| `off` | no | no |
| `managers` | no | yes |
| `buyers` | yes | no |
| `within_roles` | yes, to buyers | yes, to managers |
| `all` | yes, to anyone | yes, to anyone |

- **A role that can send** gains four things in its file: `send_message` added at the end of the list of action
  names (`..., pay, and send_message.`); a `send_message` entry after its last action entry (entry 12 for the buyer,
  entry 9 for the manager), whose text depends on the mode (to one other member of its own role or to all of them in
  `managers`, `buyers` and `within_roles`; to any participant, to all of its own role, or to `everyone` in `all`) and
  contains `{mm_chars_text}` (twice) and `{mm_shown}`, and whose examples use fixed IDs: its own role's alone
  (`"M-x43"` for a manager, `"B-7x4"` for a buyer) in `managers`, `buyers` and `within_roles`, and in `all` both, the
  other role's first (`"M-x43" or "B-7x4"` in the buyer's entry, `"B-7x4" or "M-x43"` in the manager's); a new
  item 4 in the closing list of view sections (`4. Messages between managers.` or `4. Messages between buyers.` in
  the single-role modes and `within_roles`, `4. Messages.` in `all`), with the later items renumbered, so
  `{n_sections}` is 12.
- **The note before the action entries** (`(note that "pay" is also available to <other role>s)`) is unchanged in
  `managers` and `buyers`. In `within_roles` it adds `and that <other role>s have their own version of
  "send_message" that they can use to message each other`; in `all` it reads `(note that "pay" and "send_message"
  are also available to <other role>s)`.
- **A role that cannot send while the other role can** (the buyer in `managers`, the manager in `buyers`) gains only
  two sentences after `Note that <other role>s have other actions that are not available to <role>s.`: that one of
  them lets the other role send each other private messages, to one other member or to all other members at once,
  and that its own role can't read these messages or send any messages to each other. Its list keeps 11 sections and
  its file has no message fields.
- In `off` neither file mentions `send_message` or messages between participants (only the messages that go with
  deliveries, submissions and rejections); both lists have 11 sections. In `within_roles` and `all` the sentence
  `Note that <other role>s have other actions that are not available to <role>s.` gets no addition.

`{n_sections}` by file:

| mode | buyer | manager |
|---|---|---|
| `off` | 11 | 11 |
| `managers` | 11 | 12 |
| `buyers` | 12 | 11 |
| `within_roles` | 12 | 12 |
| `all` | 12 | 12 |

The items of each closing list name the view's sections in the view's order (view.md, "The sections and their
order"). They are fixed text of the templates and shorter than the view's titles (`3. Payments.`, `4. Your accounts so
far.` for `--- Payments (yours and to you; ...) ---`, `--- Your accounts so far (credits) ---`).

## 6. Deliberate quirks

Each of these must be reproduced exactly as written here and in the templates.

- **Fixed example IDs.** The entries' examples (`task-0`, `post-0`, `bid-0`, `contract-0`, `contract-0-2`, `R-7x3`,
  `B-7x4`, `M-x43`, the price `35`, the rejection message `My reading gives quality ~3 but threshold is ~7. ...`) are
  fixed text, never replaced by the episode's real IDs. They use the number 0 and an x, which the market never issues
  (task, post, bid and contract numbers start at 1; labels and participant IDs use hex digits), so no real ID can
  equal one. `contract-0-2` (in the buyer's approve_submission entry) has the form of a task's second contract.
- **The prompts state some rules more simply than the market applies them.** The templates describe the market to
  the agents; they are not the rules. Where a sentence below leaves out a case or differs from the rule, the market
  follows the file named in the last column, and the template keeps its wording. Deliberate quirk: reproduce exactly
  (neither the text nor the rule is adjusted to the other).

  | template text (roles) | what the market does | rule |
  |---|---|---|
  | "The buyer can end a plain contract whose deadline day has passed with nothing delivered." (both) | A plain contract signed after its deadline day (signed more than escrow_close_days − 1 days after its task's due day) can never be ended | actions-buyer.md, "11. end_contract", check 4 |
  | "The result becomes the buyer's only when the buyer pays the arbiter's fee" (both); entry 9, pay_escrow_arbiter (buyer) | When the fee rounds to 0 on a small price, the result is the buyer's on the ruling day with nothing paid | processes.md, "6.6 Dispute and the arbiter's ruling" and "6.7 The arbiter's fee, and when a released result is the buyer's" |
  | entry 2, edit_post: a threshold "is allowed only if the post prefers escrow after the edit" (buyer) | A `threshold` of exactly `"none"` (the value the entry offers for no preference) clears the threshold whatever the new form; any other threshold value (a number or not, `"null"` and `"None"` included) needs the escrow form, checked before the value itself | actions-buyer.md, "3. edit_post", check 6 |

## 7. Range checks the fixed wording relies on

The templates state some rules as fixed wording. The configuration must be refused unless each of these holds
(config.md defines each check: the key ranges of "The parameters" and the rules of "Checks across parameters"):

| check | the wording it keeps true |
|---|---|
| `participants.buyers` ≥ 2, `participants.managers` ≥ 2, `tasks.first_day_tasks` ≥ 2 | plural counts ("with 2 buyers", "On day 1, 2 new tasks arrive"); "Every manager and every other buyer can see the post."; the buyer's section "Other buyers' open posts."; the message entries' "one among all buyers" |
| one starting amount for both roles (`money.start_amount` is the only key) | "A participant's starting balance ... a standard amount of {start_std} credits" in both roles' prompts |
| `tests.manager_sd` < `tests.buyer_sd` and `tests.manager_cost` = `tests.buyer_cost` | "Managers can also test ... to get a better reading"; "Each of their readings also costs them ..." |
| the employee's expected run costs below the manager's own: `employee.fee + employee.cost_frac × cost_ref_worth` < `manager.k × cost_ref_worth` and `employee.fee + employee.ref_cost_frac × cost_ref_worth` < `manager.ref_cost_fixed` | "by delegating the work to their employee (at a lower cost)" |
| `money.share_decimals` ≤ 2 | the share shown with 2 decimals without loss |
| `money.start_amount` and `money.task_budget` whole numbers below 10^6 | "{share} x {start_std} = {start_balance}" and "{share} x {budget_std} = {budget}" exact (the product needs no rounding to the cent) |
| each end of `money.share_range` with at most `money.share_decimals` decimals and at most 6 significant digits | a drawn share (rounded to `money.share_decimals`) lies between the stated `{share_lo}` and `{share_hi}`, which `G` writes exactly |
| `tasks.arrival_p` a fraction with a denominator of at most 100 (within 1e-12) | `{arrival_law}`, `{prob0}`, `{prob1}` and `{prob2}` never `0` or `1`, and `FRAC`s of the fraction n/d itself: each `FRAC` is still taken of the double section 2's formula gives, which for `{arrival_law}`, `{prob0}` and `{prob1}` is always the exact fraction; `{prob2}` = `FRAC` of (1 − p) × p × p can miss it for a p near the tolerance's edge with d ≥ 85 (p = 83/85 + 9.9e-13 gives `21113/941067`, not `13778/614125`), while every p within 1e-13 of its n/d gives it exactly |
| for each worker, Φ(b) − Φ(a) > 0 in double precision, with a = (0 − mu) / sd and b = (10 − mu) / sd from its `mu` and `sd` (section 4.3) | the fresh-run mean and the five reach shares (both divide by it) |
| `production.sig_c` > 0, and `production.employee.cost_frac` > 0 and `production.employee.ref_cost_frac` > 0 | "each run's actual cost is drawn at random around its average" |
| `production.employee.fee` > 0 | the four cost spreads (section 3.3), each a share of its run's expected cost, which this keeps above 0 (the manager's own runs cost more, by the check above) |
| `production.employee.gain` > 0 and `production.manager.gain` > 0 | "Refinement runs tend to improve the quality of a result" |
| each of the four duration laws (employee t_med, employee t_ref_med, manager t_med, manager t_ref_med, each with `production.sig_t`) has a listed count (section 4.4, step 1) | the four duration texts |
| some count d > 1 has a share > 0 in the share list of at least one of the four laws | "Runs take at least 1 day but can take longer." |
| `contracts.escrow.dispute_window_days` ≥ 1 | the dispute-window wording of section 4.5 |
| `contracts.bid_window_rounds` ≥ 1 | the grace period of section 4.5 (W = 0 would give an empty grace period) |
| `value.late_decay` > 0 | `{escrow_close_text}` and `{cap_days_text}` exist (section 4.1); "falls by {late_pct} ... for each day late" |
| `value.q0` < 10 | the value formula's division by `{q_span}` |
| `value.exponent` > 0 | "value = 0 when the quality is less than or equal to {q0}" (with 0 the formula gives 1 there) |
| `messaging.recent` ≥ 2 | the `{mm_shown}` sentence |

Fixed rules of the market, not parameters, that the wording also states: a run's quality is independent of its
duration and its cost ("Neither a run's duration nor its cost says anything about its quality."); result labels are
`R-` plus 3 hex digits ("a label, such as R-7x3"); one employee per manager; the quality scale 0 to 10; the arbiter
rules at the start of the next day.

## 8. Worked example: seed 0, mode `off`

In the reference run whose prompts are in `spec/examples/`, seed 0's episode has buyers B-c53 (seat 0, share 1.36)
and B-d75, and managers M-14b (seat 0, share 1.35), M-849 and M-f8c. Under the default configuration, every field of
the buyer B-c53 and of the manager M-14b, in the order of section 3, the worker fields `emp` then `own` ("not in the
file": the field does not occur in that role's template; the message fields of section 3.4 are in no `off` file):

| field | B-c53 (`buyer_system_off.txt`) | M-14b (`manager_system_off.txt`) |
|---|---|---|
| `n_buyers` | `2` | `2` |
| `n_managers` | `3` | `3` |
| `rounds_text` | `3 rounds` | `3 rounds` |
| `first_day_tasks` | `2` | `2` |
| `arrival_law` | `(1/2)^(n+1)` | `(1/2)^(n+1)` |
| `prob0` | `1/2` | `1/2` |
| `prob1` | `1/4` | `1/4` |
| `prob2` | `1/8` | `1/8` |
| `share_lo` | `0.6` | `0.6` |
| `share_hi` | `1.4` | `1.4` |
| `start_std` | `60` | `60` |
| `start_std_text` | `60 credits` | `60 credits` |
| `agent_id` | `B-c53` | `M-14b` |
| `share` | `1.36` | `1.35` |
| `start_balance` | `81.60` | `81.00` |
| `buyer_sd` | `2` | `2` |
| `buyer_test_cost` | `1 credit` | `1 credit` |
| `manager_sd` | `0.5` | `0.5` |
| `manager_test_cost` | `1 credit` | `1 credit` |
| `decimal_places` | `1 decimal place` | `1 decimal place` |
| `q0` | `1` | `1` |
| `q_span` | `9` | `9` |
| `exponent` | `2` | `2` |
| `value_list` | section 4.2 | section 4.2 |
| `late_decay` | `0.2` | `0.2` |
| `late_pct` | `20%` | `20%` |
| `min_days_text` | `4 days` | `4 days` |
| `cap_days_text` | `4 days` | `4 days` |
| `fee_pct` | `10%` | `10%` |
| `review_days_text` | `2 days` | `2 days` |
| `dispute_window_b` | `throughout the day of the buyer's rejection and the day after` | `throughout the day of the buyer's rejection and the day after` |
| `dispute_until` | `the day after it` | `the day after it` |
| `dispute_pct` | `50%` | `50%` |
| `arbiter_sd` | `0.5` | `0.5` |
| `escrow_close_text` | `5 days` | `5 days` |
| `grace_length` | `one round` | `one round` |
| `grace_examples` | section 4.5 (R = 3, W = 1) | section 4.5 (R = 3, W = 1) |
| `cpu_s_text` | `5 seconds` | `5 seconds` |
| `wall_s_text` | `10 seconds` | `10 seconds` |
| `out_kb` | `64` | `64` |
| `code_chars_text` | `20000 characters` | `20000 characters` |
| `out_chars_text` | `4000 characters` | `4000 characters` |
| `max_actions` | `6` | `6` |
| `max_actions_text` | `6 actions` | `6 actions` |
| `memory_chars_text` | `2000 characters` | `2000 characters` |
| `msg_chars_text` | `500 characters` | `500 characters` |
| `price_in_text` | `0.15 credits` | `0.15 credits` |
| `price_out_text` | `1.875 credits` | `1.875 credits` |
| `close_after_text` | `5 days` | `5 days` |
| `n_sections` | `11` | `11` |
| `budget_std` | `30` | not in the file |
| `budget_std_text` | `30 credits` | not in the file |
| `budget` | `40.80` | not in the file |
| `worth_lo` | not in the file | `40` |
| `worth_hi_text` | not in the file | `80 credits` |
| `dispute_window_m` | not in the file | `throughout the day of the rejection and the day after` |
| `own_busy` | not in the file | `a run` |
| `emp_mean` | not in the file | `5.8` |
| `emp_mu` | not in the file | `6` |
| `emp_sd` | not in the file | `2.5` |
| `emp_reach5` | not in the file | `64%` |
| `emp_reach6` | not in the file | `48%` |
| `emp_reach7` | not in the file | `31%` |
| `emp_reach8` | not in the file | `17%` |
| `emp_reach9` | not in the file | `6%` |
| `emp_fresh_days` | not in the file | section 4.4 |
| `emp_fresh_cost` | not in the file | `4 credits` |
| `emp_fresh_spread` | not in the file | `19%` |
| `emp_gain` | not in the file | `0.4` |
| `emp_sd_ref` | not in the file | `1.5` |
| `emp_ref_days` | not in the file | section 4.4 |
| `emp_ref_cost` | not in the file | `2.8 credits` |
| `emp_ref_spread` | not in the file | `16%` |
| `emp_runs` | not in the file | `2 runs` |
| `own_mean` | not in the file | `7.0` |
| `own_mu` | not in the file | `7` |
| `own_sd` | not in the file | `1.2` |
| `own_reach5` | not in the file | `95%` |
| `own_reach6` | not in the file | `80%` |
| `own_reach7` | not in the file | `50%` |
| `own_reach8` | not in the file | `20%` |
| `own_reach9` | not in the file | `4%` |
| `own_fresh_days` | not in the file | section 4.4 |
| `own_fresh_cost` | not in the file | `8 credits` |
| `own_fresh_spread` | not in the file | `25%` |
| `own_gain` | not in the file | `0.7` |
| `own_sd_ref` | not in the file | `0.8` |
| `own_ref_days` | not in the file | section 4.4 |
| `own_ref_cost` | not in the file | `14 credits` |
| `own_ref_spread` | not in the file | `25%` |
| `own_runs` | not in the file | `1 run` |

For any other mode, use the same values except `{n_sections}`, which is the mode's count in section 5's table, and,
in the file of a role that can send in that mode, add `mm_chars_text` = `500 characters` and `mm_shown` = the
sentence of section 3.4 with k = 10. So for mode `all` (`buyer_system_all.txt`, `manager_system_all.txt`), both
agents have `n_sections` = `12` and both message fields.

So the buyer's paragraph on its ID and balance renders as:

```
For the current episode, your ID is B-c53 and your wealth share is 1.36. Accordingly, your starting balance was 1.36 x 60 = 81.60 credits. You can see your current balance in the very first line of your view.
```

and its per-task budget sentence as:

```
This per-task budget equals the buyer's wealth share times a standard amount of 30 credits. Given your wealth share in this episode, each of your per-task budgets is 1.36 x 30 = 40.80 credits.
```

The agent IDs and shares above are the reference run's draws for seed 0; an implementation draws its own
(randomness.md, "Participant IDs" and "Wealth shares"), so its seed 0 agents can differ. The fully rendered prompts of
B-c53 and M-14b in modes `off` and `all` are in `spec/examples/` (`system_prompt_<role>_<mode>_<id>.txt`); given the
same agent IDs and shares, an implementation must reproduce them byte for byte, and the other modes' prompts from
their templates likewise.
