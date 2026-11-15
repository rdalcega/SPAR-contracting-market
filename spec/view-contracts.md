# The view's market sections

This file specifies, byte for byte, the market sections of the per-turn view: the manager's "Open posts on the
forum", "Your production", "Your active contracts" and "Your finished contracts"; the buyer's "Your tasks not yet
contracted", "Your active contracts", "Your finished contracts" and "Other buyers' open posts (for reference)". It
gives every line variant for plain and escrow contracts in each state, the orders, the limits and their overflow
texts, and the rules that move a contract from the active list to the finished list. The view's frame (header,
results, notifications, payments, messages, accounts, record, memory, final instructions) and the spacing of the
view's items are in `view.md` ("Items and empty lines"). Nothing in these sections depends on `message_channels`:
they are the same in all five modes.

## 1. Where the sections sit

The market sections follow the record section (`view.md`, "Your record with each counterparty (all your contracts
so far)") and precede the memory section (`view.md`, "Your self-written memory"). Before spacing, they are these
lines, in this order. "(empty line)" is one empty line; exactly one empty line comes before each title (`view.md`,
"Items and empty lines"), and the first one shown is that line, not an extra one.

Manager:

```
(empty line)
--- Open posts on the forum ---
{open-post lines, section 3}
(empty line)
--- Your production ---
{expected run costs line, section 5}
{capacity line, section 5}
(empty line)
--- Your active contracts ---
{contract items, section 7, or the bids note, section 7.4}
(empty line)
--- Your finished contracts{count} ---
{finished lines, section 11}
```

Buyer:

```
(empty line)
--- Your tasks not yet contracted ---
{task lines, section 4}
(empty line)
--- Your active contracts ---
{contract items, section 8}
(empty line)
--- Your finished contracts{count} ---
{finished lines, section 11}
(empty line)
--- Other buyers' open posts (for reference) ---
{open-post lines, section 3}
```

`{count}` is empty when the viewer has no finished contract, else ` (latest {a} of {b})` or
` (latest {a} of {b}, and {k} older delivered and not paid in full)` (section 11.1).

Every section is always present. A section with nothing in it is its title alone (no "(none)" or other
placeholder).

Lines that start with four spaces (`    `) are sub-lines: they belong to the line above them. A line that does not
start with whitespace, together with the sub-lines under it, is one item. The layout of `view.md`, "Items and empty
lines", then puts one empty line after each title that has content and exactly one empty line between two
consecutive items, including after a contract's last sub-line; sub-lines are never separated from their line or from
each other. So each post, task, contract, overflow
note and summary line is its own paragraph, and a contract's sub-lines hug it. Every message these sections quote
is put on one line (ONE_LINE, section 2.3), so no line of them holds a line break.

All views of a round are built from the market as the round starts, before anyone acts (`rounds.md`, "Views: all
from the market as the round starts").
Below, **today** is the view's day and **now** is the view's round index g = (day - 1) x `time.rounds_per_day` +
round.

## 2. Terms, values and shared texts

### 2.1 Values of a contract

For a contract c on task t, signed on day s:

| term | meaning |
|---|---|
| due(c) | t's due day |
| dl(c) | c's deadline, fixed when c is signed: min(max(due, s + `contracts.min_contract_days`), due + E - 1), with E = escrow_close_days (`config.md`, "escrow_close_days" and "The full-pay cap and the contract deadline"). The same formula for both forms. Defaults: min(max(due, s + 4), due + 4) |
| close(c) | an escrow contract's close day: due(c) + E (default due + 5) |
| LD(c), the listing deadline | dl(c) for a plain contract, due(c) for an escrow contract |
| ended | its buyer ended it with `end_contract` (either form); the day it did so is its end day |
| held / released / closed unreleased | an escrow contract's state: the price is held from signing until it is released to the manager (approval, automatic release, or a ruling for the manager) or comes back to the buyer (the escrow closes unreleased, or the buyer ends it) |
| settled | ended, or an escrow contract that is no longer held |
| deliveries | a plain contract's deliveries, or an escrow contract's submissions, in the order they were made. Each has: its result label, its day, its number n on the contract (1, 2, ...), its message (possibly empty) and its stated quality (or none). A submission also has its outcome: under review, approved, released with no decision (automatic release), or rejected; its decision day; the buyer's rejection message (possibly empty); and, if the manager disputed the rejection, the dispute (its ruling day, and once ruled the arbiter's reading and the winner) |
| latest delivery day | the day of c's last delivery or submission |
| in use (plain) | the delivery in use: the one the buyer kept with `keep_delivery`, if any, else the latest. Its label is the **in-use label**, its day the **in-use day**. (On a plain contract each label is delivered at most once.) |
| paid (plain) | how much of the price the buyer has paid on c (the part of its payments counted toward the price; never above the price; `processes.md`, "What is owed") |
| held amount | an escrow's price while held, else 0 |
| to manager | what the release paid the manager (the price x the lateness factor, rounded to 4 decimals; `processes.md`, "The release and the pay cut"); 0 until released |
| refunded | what came back to the buyer: on a release, the price minus the to-manager amount; on a close unreleased or an end, the whole price; else 0 |
| owes the arbiter's fee | a ruling for the manager released c's price, its fee (the dispute fee) is above 0, and the buyer has not paid it with `pay_escrow_arbiter` |
| open to dispute | c's latest submission is a rejection that was not disputed, and today <= its decision day + `contracts.escrow.dispute_window_days` |
| under review | one of c's submissions awaits the buyer's decision (at most one at a time) |
| in dispute | the manager disputed the latest rejection and the arbiter has not ruled yet |
| takes a submission today | c is held, nothing is under review, nothing is in dispute, and today < close(c) |
| released submission | on a released escrow, the submission whose price was released (always its latest submission) |
| results | the runs started on c (by either worker), in the order they were started. A result is **running** until the start of its arrival day and **arrived** from then on, so in a view a running result always arrives tomorrow or later |

A result's **manager readings** and **buyer readings** are the readings each side took of it, in the order taken.
A contract's **counterparty** is its buyer in the manager's view and its manager in the buyer's view.

### 2.2 Number formats

The formats (`G`, `CR`, `F1`, `F2`, `Fn`, `REPR`, `PCT0`, `INT`) are the README's ("Number formats and rounding").
Which format each number takes:

| number | format |
|---|---|
| a bid's or a contract's price where written `at {price}` or `price {price}` | `G` (`25`, `18.5`, `22.25`) |
| every other money amount in these sections (deposits, paid, released, refunded, unpaid sums, run costs) | `F2`, except where `CR` is named |
| the arbiter's fee and the dispute fee | `CR` |
| a threshold | `G` (`7`) |
| a task's worth | `INT` |
| a reading and the arbiter's reading | `Fn` with n = `tests.decimals` (`F1` at the default) |
| a stated quality | `G` |
| a mean of readings, a standard error | `F2` |
| an on-time value | `F1` |
| `production.sig_c x 100`, `tests.manager_cost` | `G` |
| days, rounds, counts, delivery numbers | `INT` |

### 2.3 Shared texts

`{REL(d)}`, a day relative to today, with k = d - today:

| k | text |
|---|---|
| k = 1 | `1 day left` |
| k > 1 | `{k} days left` |
| k = 0 | `today` |
| k = -1 | `1 day ago` |
| k < -1 | `{-k} days ago` |

`{WHEN(g)}`, a round index g as a day and round: `day {d}, round {r}` with d = (g - 1) div `time.rounds_per_day` + 1
and r = (g - 1) mod `time.rounds_per_day` + 1.

`{ACCEPT_FROM(p)}`: for a post p made in round index g, the round index g + `contracts.bid_window_rounds` + 1 (the
first round its bids can be accepted in; default g + 2). A post's bids are **sealed** while now < ACCEPT_FROM(p).

`{TASK_DUE(t)}`: `due day {due} ({REL(due)})`, due being task t's due day.

`{f(q)}`: the value fraction clip((q - `value.q0`) / (10 - `value.q0`), 0, 1) ^ `value.exponent`, in double
arithmetic.

`{VALUES(V)}`, the on-time values of a task worth V (the quality list 5..10 is fixed):

```
on-time value to you at quality 5, 6, 7, 8, 9, 10: {v5}, {v6}, {v7}, {v8}, {v9}, {v10}
```

with v_q = `F1`(V x f(q)). Example, worth 65 at the defaults: `12.8, 20.1, 28.9, 39.3, 51.4, 65.0`.

`{NAMED(ids)}`, a list of names: the first `view.overflow_ids` of them joined with `, `, followed by
` and {k} more` when k > 0 names remain.

`{READINGS(R, side)}`: the readings text of `processes.md`, "2.3 The readings text", for the readings R of one
result taken by that side (manager or buyer).

Every use below is preceded by `your `, giving `your readings ...`. Example:
`your readings [..., 5.9, 7.4, 4.3, 7.5, 6.4] mean 6.20 (n=6, ±0.82 standard error)`.

`SUM` (used in section 11.4) is the README's compensated sum, in the list's order ("Number formats and rounding").

`{ONE_LINE(m)}`: the text m with its line breaks removed: m is split into lines at every line boundary (`\n`, `\r`,
`\r\n`, `\v`, `\f`, `\x1c`, `\x1d`, `\x1e`, `\x85`, `\u2028` (line separator), `\u2029` (paragraph
separator); a boundary at the very end ends the last line and starts no new one), and the lines are joined with one
space each. So `a\r\nb\n` gives `a b`, and `a\n\nb` gives `a  b` (two spaces).

Messages are shown between plain double quotes; quotes inside a message are not escaped.

## 3. Open posts

### 3.1 Which posts, in what order

- Manager, section `--- Open posts on the forum ---`: every open post.
- Buyer, section `--- Other buyers' open posts (for reference) ---`: every open post of another buyer.

A post is open from when it is posted until its buyer accepts a bid on it or withdraws it. Nothing else closes a
post: a post whose task is past its due day stays open and listed. Posts are taken in the order they were created
(a repost `post-N-k` is created when it is posted; posts made in one round are created in the order the round's
actions run, `rounds.md`, "The order the phase's actions run in").

At most `view.open_shown` posts are listed in full. Rank each post 0, or 1 if the viewer is a manager with an open
bid on it (a buyer's rank is always 0). Sort by rank ascending, then newest first; the first `view.open_shown` are
**listed**, the others are **hidden**. The listed posts are shown in creation order. So a manager's posts with its
open bid are hidden before any post it has not bid on, however new: the section shows first the posts it can still
bid on, and a hidden post is still named in the line below.

The section's lines are:
1. the title;
2. if any post is hidden, `({h} more open {posts} not listed: {NAMED(hidden post ids, newest first)})` with h the
   number hidden and `{posts}` = `post` if h = 1, else `posts`, for example:

   ```
   (1 more open post not listed: post-3)
   ```

3. one post line per listed post.

### 3.2 The post line

```
{post_id} by {buyer_id} | {task_id} | {TASK_DUE(t)}{late}{terms}{window} | bids: {bids}
```

- `{late}`: in the manager's view, the lateness clause `LATE(due, worth, manager)` of section 3.4; empty in the
  buyer's view.
- `{terms}`: ` | ` followed by `prefers a plain contract`, `prefers escrow` or `no preferred form` (the post's
  stated form: plain, escrow or none), then `, threshold {threshold}` if the post states a threshold (only a post
  that prefers escrow can).
- `{window}`: while the post's bids are sealed, ` | bids open; the buyer can accept one from {WHEN(ACCEPT_FROM(p))}`;
  empty from round ACCEPT_FROM(p) on.
- `{bids}`, section 3.3.

Examples:

```
post-3 by B-d75 | task-3 | due day 7 (6 days left) | prefers escrow, threshold 7 | bids open; the buyer can accept one from day 1, round 3 | bids: sealed (only the buyer sees them); you have not bid
post-3 by B-d75 | task-3 | due day 7 (3 days left) | a contract signed today has deadline day 8, 4 days after signing (the task's due day is 7: its value to the buyer falls from then) | prefers escrow, threshold 7 | bids: sealed (only the buyer sees them); you have not bid
post-12 by B-c53 | task-12 | due day 15 (5 days left) | prefers a plain contract | bids: sealed; yours: bid-41 at 18 with threshold 5 (escrow) (awaiting the buyer: not a contract unless accepted)
post-3 by B-d75 | task-3 | due day 7 (6 days left) | prefers escrow, threshold 7 | bids open; the buyer can accept one from day 1, round 3 | bids: sealed (only the buyer sees them)
```

(The first three in a manager's view, the last in a buyer's.)

### 3.3 The bids field on another participant's post

Bids are sealed from everyone but the post's buyer at all times. For a viewer other than the post's buyer, whether
or not any bid exists and whether or not the window is open:

| viewer | `{bids}` |
|---|---|
| a manager with an open bid on the post | `sealed; yours: {bid_id} at {price}{bid_terms} (awaiting the buyer: not a contract unless accepted)` |
| a manager without one | `sealed (only the buyer sees them); you have not bid` |
| a buyer | `sealed (only the buyer sees them)` |

`{price}` is the bid's price in `G`; `{bid_terms}` is ` with threshold {threshold} (escrow)` for an escrow bid
and empty for a plain bid. A manager has at most one open bid per post (bidding again updates it, under the same
id). Withdrawn and lost bids are never shown: a manager that withdrew its bid reads `you have not bid`. The post's
own buyer sees its bids in its tasks section (section 4.3).

### 3.4 The lateness clause on posts and uncontracted tasks

`LATE(due, worth, role)`, role being manager (a post line) or buyer (a line in the buyer's own tasks section), with
dl = min(max(due, today + `contracts.min_contract_days`), due + E - 1) (the deadline a contract signed today would
get) and zero = due + E (E = escrow_close_days):

**Case A**, today < due < dl:

```
 | a contract signed today has deadline day {dl}, {k} {days} after signing (the task's due day is {due}: its value to {whose} falls from then)
```

with k = dl - today, `{days}` = `day` if k = 1 else `days`, and `{whose}` = `the buyer` (manager) or `you` (buyer).

**Case B**, today >= due, with `{label}` = `DUE TODAY` if today = due, else `PAST DUE`:

- manager:

  ```
   | {label} (the task's due day {due}): a contract signed today has deadline day {dl}; a result's value to the buyer {falls} by {PCT0(late_decay)} of its on-time value for each day after day {due}, and it is worth nothing from day {zero}
  ```

  with `{falls}` = `will fall` if today = due, else `falls`.

- buyer, with tomorrow = today + 1, L = tomorrow - due, and factor F = max(0, 1 - `value.late_decay` x L):

  ```
   | {label} (the task's due day {due}): a result delivered tomorrow, day {tomorrow}, is {L} {days} late and {worth_clause}{gone}; a contract signed today has deadline day {dl}
  ```

  - `{days}`: `day` if L = 1, else `days`;
  - `{worth_clause}`: `worth nothing at any quality` if F <= 0, else
    `worth {PCT0(F)} of its on-time value (at quality 5, 6, 7, 8, 9, 10: {w5}, {w6}, {w7}, {w8}, {w9}, {w10})` with
    w_q = `F1`((worth x f(q)) x F), multiplied in that order;
  - `{gone}`: `, and nothing from day {zero}` if F > 0, else empty.

**Otherwise** (today < due and dl = due): empty.

Examples (defaults):

```
 | DUE TODAY (the task's due day 9): a result delivered tomorrow, day 10, is 1 day late and worth 80% of its on-time value (at quality 5, 6, 7, 8, 9, 10: 10.3, 16.0, 23.1, 31.5, 41.1, 52.0), and nothing from day 14; a contract signed today has deadline day 13
 | PAST DUE (the task's due day 13): a result delivered tomorrow, day 18, is 5 days late and worth nothing at any quality; a contract signed today has deadline day 17
 | PAST DUE (the task's due day 13): a contract signed today has deadline day 17; a result's value to the buyer falls by 20% of its on-time value for each day after day 13, and it is worth nothing from day 18
 | DUE TODAY (the task's due day 7): a contract signed today has deadline day 11; a result's value to the buyer will fall by 20% of its on-time value for each day after day 7, and it is worth nothing from day 12
```

A third role, used only on the buyer's own active contracts, is in section 8.1.

## 4. The buyer's "Your tasks not yet contracted"

### 4.1 Which tasks, in what order

The buyer's tasks that are **queued** (arrived and never posted, or back in the queue after the buyer ended a
contract on them) or **posted** (on an open post). Contracted tasks and withdrawn tasks (whose post the buyer
withdrew: they left the market) are not listed. Tasks are taken in the order they arrived.

At most `view.open_shown` tasks are listed in full. Rank each task:
- 0: posted, its post's bids no longer sealed (now >= ACCEPT_FROM(post)), and at least one open bid on it from a
  manager that is not out of business;
- 1: queued;
- 2: any other posted task.

Sort by rank ascending, then newest first; the first `view.open_shown` are listed, the rest hidden; the listed tasks are shown
in arrival order. The section's lines are the title; if any task is hidden,
`({h} more {tasks} not listed: {NAMED(names)})` where `{tasks}` = `task` if h = 1, else `tasks`, and names are
`{task_id} ({status})` (status `queued` or `posted`) of the hidden tasks, newest first (e.g.
`(1 more task not listed: task-3 (posted))`); then one task line per listed task.

### 4.2 The task lines

Both kinds start with the head

```
{task_id} | worth {worth} to you ({VALUES(worth)}){late}
```

with `{late}` = `LATE(due, worth, buyer)` (section 3.4). Note that the lateness clause comes before the due field
here, and after it on a post line.

A queued task never posted since an end, or never posted at all:

```
{head} | {TASK_DUE(t)} | QUEUED (not posted)
{head} | {TASK_DUE(t)} | QUEUED (not posted again since {ENDED(t)})
```

the second when the buyer has ended one or more contracts on the task, with
`{ENDED(t)}` = `you ended ` followed by `{contract_id} on day {end_day}` for each contract on t the buyer ended, in
the order ended, joined with `, `.

A posted task:

```
{head} | {TASK_DUE(t)} | POSTED as {post_id}{again}, {form}{threshold} (edit_post changes its stated form and threshold){window} | bids: {bids}
```

- `{again}`: ` ({ENDED(t)})` when the buyer ended contracts on the task before this post, else empty;
- `{form}`: `prefers a plain contract`, `prefers escrow` or `no preferred form`;
- `{threshold}`: `, threshold {threshold}` if the post states one, else empty;
- `{window}`: while the post's bids are sealed,
  ` | bids open; you can accept one from {WHEN(ACCEPT_FROM(p))}, not before (accept_bid fails earlier)`; empty from
  round ACCEPT_FROM(p) on;
- `{bids}`: section 4.3.

A task line carries no budget. The per-task budget is stated in the buyer's system prompt (`prompts.md`, "3.2
Fields only in the buyer's template", `budget`) and in the task's arrival notice (`episode.md`, "Task arrivals");
the accounts section shows only the total of the budgets received (`view.md`, "Your accounts so far (credits)").

### 4.3 The bids field on the buyer's own post

- While sealed: `sealed until {WHEN(ACCEPT_FROM(p))} (you see them then)`, whether or not bids exist.
- From round ACCEPT_FROM(p) on: the open bids on the post from managers that are not out of business, sorted by
  price ascending (equal prices in the order the bids were created; a bid updated in place keeps its place), each
  `{bid_id} by {manager_id} at {price}{bid_terms}` (section 3.3's `{price}` and `{bid_terms}`), joined with `; `;
  or `no bids yet` when there are none. Withdrawn, lost and accepted bids are not listed. Bids carry no message.

### 4.4 Examples

```
task-1 | worth 65 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 12.8, 20.1, 28.9, 39.3, 51.4, 65.0) | due day 9 (8 days left) | QUEUED (not posted)
task-22 | worth 57 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 11.3, 17.6, 25.3, 34.5, 45.0, 57.0) | a contract signed today has deadline day 18, 4 days after signing (the task's due day is 17: its value to you falls from then) | due day 17 (3 days left) | POSTED as post-22, prefers escrow, threshold 7 (edit_post changes its stated form and threshold) | bids open; you can accept one from day 14, round 2, not before (accept_bid fails earlier) | bids: sealed until day 14, round 2 (you see them then)
task-5 | worth 50 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 9.9, 15.4, 22.2, 30.2, 39.5, 50.0) | DUE TODAY (the task's due day 5): a result delivered tomorrow, day 6, is 1 day late and worth 80% of its on-time value (at quality 5, 6, 7, 8, 9, 10: 7.9, 12.3, 17.8, 24.2, 31.6, 40.0), and nothing from day 10; a contract signed today has deadline day 9 | due day 5 (today) | POSTED as post-5, prefers escrow, threshold 7 (edit_post changes its stated form and threshold) | bids: bid-2 by M-849 at 18.5; bid-3 by M-f8c at 18.5 with threshold 7 (escrow); bid-1 by M-14b at 20 with threshold 6 (escrow)
task-9 | worth 54 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 10.7, 16.7, 24.0, 32.7, 42.7, 54.0) | due day 9 (4 days left) | POSTED as post-9, prefers a plain contract (edit_post changes its stated form and threshold) | bids: no bids yet
task-27 | worth 42 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 8.3, 13.0, 18.7, 25.4, 33.2, 42.0) | PAST DUE (the task's due day 19): a result delivered tomorrow, day 24, is 5 days late and worth nothing at any quality; a contract signed today has deadline day 23 | due day 19 (4 days ago) | QUEUED (not posted again since you ended contract-27 on day 23)
task-12 | worth 47 to you (...) | ... | POSTED as post-12-2 (you ended contract-12 on day 10), no preferred form (edit_post changes its stated form and threshold) | bids: no bids yet
```

(The last is abridged.)

## 5. The manager's "Your production"

Two lines.

```
Expected run costs in credits, with how much each typically varies either way: employee fresh {ef} (±{sef}), employee refine {er} (±{ser}), you fresh {mf} (±{smf}), you refine {mr} (±{smr}). test_result: {tc} per reading.
```

- `{ef}`, `{er}`, `{mf}`, `{mr}`: the four expected run costs of `config.md`, "Expected run costs", in `F2`;
- `{sef}`, `{ser}`, `{smf}`, `{smr}`: the four cost spreads the manager's system prompt states, in the same order
  (`emp_fresh_spread`, `emp_ref_spread`, `own_fresh_spread`, `own_ref_spread`; prompts.md, "3.3 Fields only in the
  manager's template"), each `PCT0`;
- `{tc}`: `G`(`tests.manager_cost`).

Default:

```
Expected run costs in credits, with how much each typically varies either way: employee fresh 4.00 (±19%), employee refine 2.80 (±16%), you fresh 8.00 (±25%), you refine 14.00 (±25%). test_result: 1 per reading.
```

The capacity line:

```
You (run_myself): {me}. Employee (delegate_run): {e} of {cap} slots busy{busy}: {now}.
```

Let mine be the manager's own runs in flight and emp its employee's runs in flight: the running results of that
worker on any of the manager's contracts, ended and settled ones included, in contract creation order and then in
the order the runs started. own = `production.manager.capacity`, cap = `production.employee.capacity`, e = the
number of emp. `slots` follows `{cap}` *(singular rule)*: `slot` when cap = 1 (`0 of 1 slot busy`).

- `{me}`:
  - mine empty: `free: {own} run_myself can start now on your contracts`;
  - else `busy on {list}` followed by `: no run_myself before day {first}` if the number of mine >= own, else by
    `: {own - number of mine} more run_myself can start now on your contracts`. `{list}` is
    `{label} ({ON(r)}) until day {arrival day}` for each run in mine, joined with `, `; `{first}` is the earliest
    arrival day among mine.
- `{busy}`: empty if emp is empty, else ` (` + `{label} on {ON(r)}, arrives day {arrival day}` for each run in emp,
  joined with `, `, + `)`.
- `{now}`: `{cap - e} delegate_run can start now on your contracts` if cap - e > 0 (the word `delegate_run` is never
  inflected), else `no delegate_run before day {first}` with the earliest arrival day among emp.
- `{ON(r)}`: the run's contract id, followed by `, closed: its result cannot be used` if that contract is settled.

Examples:

```
You (run_myself): free: 1 run_myself can start now on your contracts. Employee (delegate_run): 0 of 2 slots busy: 2 delegate_run can start now on your contracts.
You (run_myself): busy on R-1e4 (contract-9) until day 8: no run_myself before day 8. Employee (delegate_run): 2 of 2 slots busy (R-8a7 on contract-9, arrives day 6, R-047 on contract-10, arrives day 7): no delegate_run before day 6.
You (run_myself): busy on R-a01 (contract-17, closed: its result cannot be used) until day 8: no run_myself before day 8. Employee (delegate_run): 1 of 2 slots busy (R-a02 on contract-17, closed: its result cannot be used, arrives day 6): 1 delegate_run can start now on your contracts.
```

## 6. Fields shared by the contract lines

### 6.1 `{form}`

` (plain)` for a plain contract; ` (escrow, threshold {threshold})` for an escrow contract. It follows
`price {price}` directly.

### 6.2 `{money}`

| contract | text |
|---|---|
| escrow (any state) | `deposit {F2 price}: {F2 held amount} held in escrow, {F2 to manager} released` followed by `, {F2 refunded} refunded` if refunded > 1e-9 |
| plain, not ended | `paid {F2 paid} of {F2 price}` |
| plain, ended | `paid {F2 paid}` |

Examples: `deposit 25.00: 25.00 held in escrow, 0.00 released`;
`deposit 24.00: 0.00 held in escrow, 19.20 released, 4.80 refunded`;
`deposit 17.00: 0.00 held in escrow, 0.00 released, 17.00 refunded`; `paid 10.00 of 25.50`; `paid 3.00`.

### 6.3 `{deadline}` on an active line

| contract | dl = due | dl ≠ due |
|---|---|---|
| not settled | `deadline day {dl} ({REL(dl)})` | `deadline day {dl} ({REL(dl)}; its task's due day is {due})` |
| settled | `deadline day {dl}` | `deadline day {dl} (its task's due day is {due})` |

A finished line always has `deadline day {dl}` alone.

### 6.4 `{status}`

The status depends on the viewer's role only where marked.

Ended (either form): `ENDED by {buyer_id} on day {end_day}`, followed by ` (price refunded)` on an escrow contract.

Plain, not ended:
- nothing delivered: `NOT DELIVERED`;
- delivered: buyer `in use: {in-use label} ({which}; day {in-use day}, {lateness})`, manager
  `in use by {buyer_id}: {in-use label} ({which}; day {in-use day}, {lateness})`, with `{which}` = `kept` if the
  buyer kept a delivery, else `latest`, and `{lateness}` = `on time` if the in-use day <= dl, else `1 day late` or
  `{k} days late` with k = in-use day - dl.

Escrow, not ended, first match:
1. released:
   `RELEASED {label} (day {release day}, {how}; submitted {lateness}){owed}`
   - `{label}`: the released submission's label; release day: the day the price was released;
   - `{how}`: `approved`, `released with no decision` (automatic release) or `the arbiter ruled` (a ruling for the
     manager);
   - `{lateness}`: `on time` if the released submission's day <= dl, else `1 day late` / `{k} days late`, k = that
     day - dl;
   - `{owed}`, only while the buyer owes the arbiter's fee: buyer
     `; {buyer_id} owes the arbiter's fee {CR fee} to the arbiter (not to the manager) before the result is its`,
     manager `; {buyer_id} owes the arbiter's fee {CR fee} to the arbiter (not to you) before the result is its`.
2. closed unreleased: `CLOSED UNRELEASED (price refunded)`.
3. a submission under review:
   `price held; {label} UNDER REVIEW since day {its day} (released at the start of day {its day + review_days} unless approved or rejected before)`.
4. in dispute:
   `price held; the rejection of {label} is IN DISPUTE (the arbiter rules at the start of day {ruling day})`, label
   being the disputed submission's (the ruling day is the dispute's day + 1).
5. otherwise, with r = the number of its submissions whose outcome is rejected (including rejections the arbiter
   upheld):
   - r > 0: `price held; 1 submission rejected` or `price held; {r} submissions rejected`, followed, while the
     latest rejection is open to dispute, by
     `; the rejection of {label} can be disputed through day {decision day + dispute_window_days}`;
   - r = 0: `price held; nothing submitted`.

(`review_days` and `dispute_window_days` are `contracts.escrow.*`.)

### 6.5 `{closed}`, the closed mark (active lines only)

On a settled contract that is not ended (an escrow released or closed unreleased), the manager's mark:

```
 | CLOSED: takes no more runs, tests or submissions
```

and the buyer's:

```
 | CLOSED: settled; it cannot be approved, rejected or ended
```

except that the buyer's line has no mark while the buyer owes the arbiter's fee. Empty otherwise.

### 6.6 `{end}`, when the buyer can end the contract (the buyer's active lines only)

Empty on a settled contract. Otherwise the first that applies:

1. The buyer can end it now (the rule `end_contract` applies: `processes.md`, "When a contract can be ended", a row
   that says yes): ` | can be ended now`.
2. Plain with a delivery: ` | cannot be ended (it has a delivery)`.
3. Escrow, when one of the following holds (otherwise go on to rule 5; rule 4 is for plain contracts only):
   - a submission under review or in dispute: empty (the status says what is pending);
   - a rejection open to dispute:
     ` | can be ended from day {decision day + dispute_window_days + 1} if {manager_id} does not dispute`;
   - dl + 1 >= close(c) (with the deadline formula, exactly when dl = due + E - 1; at the defaults, the escrows
     signed on or after their task's due day):

     ```
      | its escrow closes the day after its deadline day: can be ended only after a rejection that stands
     ```

     An escrow signed after dl always has dl = due + E - 1, so it always takes this clause.
4. Plain signed after dl: ` | signed after its deadline day: cannot be ended`.
5. Today <= dl: ` | can be ended from day {dl + 1} if nothing is released by then` (escrow) or
   ` | can be ended from day {dl + 1} if nothing is delivered by then` (plain).
6. Otherwise empty.

## 7. The manager's "Your active contracts"

### 7.1 Which contracts

The contracts the viewer manages that are active (section 10.1), in the order the contracts were created.

### 7.2 A contract item

```
{contract_id} with {buyer_id} | {task_id} | price {price}{form} | {money} | {deadline} | {status}{closed}
    {escrow days line}
    {result line}
    ...
```

- `{price}` in `G`; `{form}`, `{money}`, `{deadline}`, `{status}` (manager), `{closed}` (manager): section 6.
- The escrow days line (section 9) is present only on an escrow contract that is held.
- Then one result line per result on the contract, in the order the runs started; or the single sub-line
  `    no results yet` when it has none.

### 7.3 The result line

```
{label} | {who} | {kind} | started day {start day}, {arrival}{wait}{readings}{delivery}
```

- `{who}`: `employee` or `you`.
- `{kind}`: `fresh`, or `refine of {parent label}`.
- `{arrival}`: `arrived day {d}` once arrived, `arrives day {d}` while running.
- `{wait}`, running only: ` (tomorrow: it cannot be tested, refined or delivered before then)` if it arrives
  tomorrow, else ` (in {k} days: it cannot be tested, refined or delivered before then)` with k = arrival day -
  today. Empty once arrived.
- `{readings}`, arrived only: ` | your {READINGS(manager readings, manager)}`.
- `{delivery}`:
  - escrow, if the label was submitted: ` | SUBMITTED day {its day}: {OUTCOME}`, followed by
    `; the buyer's message: "{ONE_LINE(rejection message)}"` when the buyer rejected it with a non-empty message
    (on one line: ONE_LINE, section 2.3).
  - plain, if the label was delivered: ` | DELIVERED day {its day} (in use)` if it is the in-use label, else
    ` | delivered day {its day} (not in use)`.
  - otherwise empty.

`{OUTCOME}`, one submission's outcome (also used in the buyer's submission lines, section 8.3), with
`until` = its decision day + `contracts.escrow.dispute_window_days`:

| submission | text |
|---|---|
| under review | `UNDER REVIEW (released at the start of day {its day + review_days} unless approved or rejected)` |
| approved | `APPROVED day {decision day}` |
| released automatically | `RELEASED with no decision day {decision day}` |
| rejected, not disputed, today <= until, it is the contract's latest submission and the escrow is held | `REJECTED day {decision day} (disputable through day {until})` |
| rejected, not disputed, today > until | `REJECTED day {decision day} (not disputed; the dispute window closed after day {until})` |
| rejected, not disputed, otherwise (a later submission exists, or the escrow is settled, within the window) | `REJECTED day {decision day}` |
| rejected, dispute not yet ruled | `REJECTED day {decision day}, IN DISPUTE (the arbiter rules at the start of day {ruling day})` |
| rejected, ruled for the manager | `REJECTED day {decision day}, disputed: the arbiter read {reading} and ruled for the manager: RELEASED` |
| rejected, ruled for the buyer | `REJECTED day {decision day}, disputed: the arbiter read {reading} and ruled for the buyer` |

(`{reading}` in `Fn`, n = `tests.decimals`.)

Examples:

```
R-dbe | employee | fresh | started day 2, arrives day 3 (tomorrow: it cannot be tested, refined or delivered before then)
R-aaa | employee | fresh | started day 5, arrives day 8 (in 2 days: it cannot be tested, refined or delivered before then)
R-81c | employee | refine of R-b1e | started day 5, arrived day 6 | your readings [5.4] mean 5.40 (n=1, ±0.50 standard error) | SUBMITTED day 6: REJECTED day 7 (disputable through day 8); the buyer's message: "R-81c tests average ~4.8 (n=3), below the escrow threshold of 5. Please refine the result and resubmit before the deadline on day 8."
R-bbb | employee | fresh | started day 1, arrived day 2 | your readings [5.0] mean 5.00 (n=1, ±0.50 standard error) | DELIVERED day 4 (in use)
R-bcc | employee | refine of R-bbb | started day 4, arrived day 5 | your readings: none | delivered day 5 (not in use)
```

### 7.4 The bids note

When the manager has no active contract and at least one open bid, the section's only line is

```
(your bids on {posts} are not contracts until their buyers accept them)
```

with `{posts}` the distinct post ids of its open bids, sorted as strings by code point (so `post-10` comes before
`post-2`, and `post-12` before `post-12-2`), joined with `, `. With no active contract and no open bid, the section
is its title alone.

## 8. The buyer's "Your active contracts"

### 8.1 A contract item

The contracts the viewer is the buyer of that are active (section 10.2), in creation order. Each:

```
{contract_id} with {manager_id} | {task_id} | worth {worth} to you ({VALUES(worth)}){late} | price {price}{form} | {money} | signed day {signing day} | {deadline} | {status}{untested}{closed}{end}
    {escrow days line}
    {delivery or submission lines}
    {message lines}
```

- `{late}`: only when the contract is not settled and has no delivery or submission, and today > due (from the day
  after the task's due day; never on it):

  ```
   | PAST DUE ({past}): a result delivered tomorrow, day {tomorrow}, is {L} {days} late and {worth_clause}{gone}
  ```

  with tomorrow, L, `{days}`, `{worth_clause}` and `{gone}` as in section 3.4's buyer case (no deadline clause), and,
  with dl the contract's own deadline day (the one its `{deadline}` field shows, section 6.3),
  `{past}` = `the task's due day {due}, not the contract's deadline day {dl}` while today <= dl, else
  `the task's due day {due} and the contract's deadline day {dl}`. (The buyer's value counts from the task's due day,
  the manager's pay from the contract's deadline: the label says which has passed.)
- `{price}` in `G`; `{form}`, `{money}`, `{deadline}`, `{status}` (buyer), `{closed}` (buyer), `{end}`:
  section 6.
- `{untested}`: ` (nothing to test yet)` when the contract has no delivery or submission, else empty.
- The escrow days line (section 9) is present only on an escrow contract that is held.

### 8.2 Delivery lines (plain)

One line per delivered label, in the order delivered:

```
{label} delivered day {day}{in_use} | your {READINGS(buyer readings of label, buyer)}
```

with `{in_use}` = ` (in use)` for the in-use label, else empty. Every delivery is listed; there is no cap.

### 8.3 Submission lines (escrow)

One line per submission, in order, every submission listed:

```
{label} submitted day {day} (#{n}): {OUTCOME}{extra} | your {READINGS(buyer readings of label, buyer)}
```

`{OUTCOME}` as in section 7.3. `{extra}`:
- a submission whose price was released (approved, released automatically, or ruled for the manager):
  `; {F2 to manager} paid to {manager_id}`, then ` and {F2 refunded} back to you` if refunded > 1e-9, then, while
  the buyer owes the arbiter's fee,
  `; yours once you pay the arbiter's fee {CR fee} to the arbiter, not to the manager (pay_escrow_arbiter)`;
- a rejected submission not released: ` (not yours)`, then `; your message: "{ONE_LINE(rejection message)}"` if the
  buyer gave a non-empty one (on one line: ONE_LINE, section 2.3);
- a submission under review: empty.

After the submission lines, on an escrow that closed unreleased, one more sub-line:

```
escrow closed unreleased: its price {F2 refunded} came back to you
```

(Reachable when the escrow closes unreleased the day after its latest submission, which section 10.2's rule 5 then
keeps active that day: for example, a submission made, rejected and disputed on the day before the close day, with
the dispute ruled for the buyer at the start of the close day, which closes the escrow that morning. A latest
submission made earlier leaves the contract finished that day, and the sub-line is not shown. With nothing
submitted it is the item's only sub-line, which section 10.2's rule 4 lists only when `view.overdue_days` >
escrow_close_days.)

### 8.4 Message lines

The contract's deliveries or submissions that carried a non-empty message or a stated quality, in order. If there
are more than `view.messages_shown`, a line `({k} earlier delivery {messages} not listed)` (k = the number not
shown; `{messages}` = `message` if k = 1, else `messages`) comes first, then the last `view.messages_shown` of them.
Each:

| delivery | line |
|---|---|
| a message, no stated quality | `message with delivery #{n} ({label}, day {day}): "{ONE_LINE(message)}"` |
| a stated quality | `with delivery #{n} ({label}, day {day}): stated quality {G stated}` followed by `; message "{ONE_LINE(message)}"` if the message is non-empty |

The word is `delivery` for escrow submissions too. (Payments carry no message, so no payment appears here.)
Rejection messages are not message lines: they are printed on the submission lines (section 8.3) and the manager's
result lines (section 7.3), and `view.messages_shown` does not limit them.

### 8.5 Examples

```
contract-6 with M-14b | task-6 | worth 70 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 13.8, 21.6, 31.1, 42.3, 55.3, 70.0) | price 25.5 (plain) | paid 10.00 of 25.50 | signed day 3 | deadline day 8 (2 days left) | in use: R-bbb (kept; day 4, on time) | cannot be ended (it has a delivery)
    R-bbb delivered day 4 (in use) | your readings [4.0, 6.0] mean 5.00 (n=2, ±1.41 standard error)
    R-bcc delivered day 5 | your readings: none
    with delivery #1 (R-bbb, day 4): stated quality 6.5; message "hi there"
    with delivery #2 (R-bcc, day 5): stated quality 7

contract-39 with M-f2b | task-39 | worth 72 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 14.2, 22.2, 32.0, 43.6, 56.9, 72.0) | price 14 (plain) | paid 0.00 of 14.00 | signed day 20 | deadline day 28 (5 days left) | NOT DELIVERED (nothing to test yet) | can be ended from day 29 if nothing is delivered by then

contract-26 with M-bd6 | task-26 | worth 47 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 9.3, 14.5, 20.9, 28.4, 37.1, 47.0) | price 17 (escrow, threshold 7) | deposit 17.00: 0.00 held in escrow, 17.00 released | signed day 16 | deadline day 20 (its task's due day is 18) | RELEASED R-99e (day 22, the arbiter ruled; submitted on time); B-42c owes the arbiter's fee 8.50 to the arbiter (not to the manager) before the result is its
    R-99e submitted day 20 (#1): REJECTED day 21, disputed: the arbiter read 8.2 and ruled for the manager: RELEASED; 17.00 paid to M-bd6; yours once you pay the arbiter's fee 8.50 to the arbiter, not to the manager (pay_escrow_arbiter) | your readings [6.6, 5.1] mean 5.85 (n=2, ±1.41 standard error)
    with delivery #1 (R-99e, day 20): stated quality 7.4; message "M-bd6: R-99e submitted for review. My mean reading is 7.4 over 3 readings; stated quality 7.4."

contract-29 with M-218 | task-29 | worth 61 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 12.0, 18.8, 27.1, 36.9, 48.2, 61.0) | price 18 (escrow, threshold 7) | deposit 18.00: 18.00 held in escrow, 0.00 released | signed day 17 | deadline day 21 (2 days ago) | price held; R-07d UNDER REVIEW since day 22 (released at the start of day 24 unless approved or rejected before)
    escrow days: full price if submitted by day 21 (the deadline day, 2 days ago) | R-07d is released at the start of day 24 if you neither approve nor reject it (sooner if the market ends first) | with nothing released, closes at the start of day 26 (later if a decision or dispute is pending then) | dispute fee 9 credits
    R-07d submitted day 22 (#1): UNDER REVIEW (released at the start of day 24 unless approved or rejected) | your readings [5.6, 4.6, 6.3] mean 5.50 (n=3, ±1.15 standard error)
    with delivery #1 (R-07d, day 22): stated quality 7.8; message "Submitting R-07d (employee refine, tested 7.8 on day 22). It is 1 day late, so I understand the price is scaled to 14.40."

contract-35 with M-218 | task-35 | worth 60 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 11.9, 18.5, 26.7, 36.3, 47.4, 60.0) | PAST DUE (the task's due day 22 and the contract's deadline day 22): a result delivered tomorrow, day 24, is 2 days late and worth 60% of its on-time value (at quality 5, 6, 7, 8, 9, 10: 7.1, 11.1, 16.0, 21.8, 28.4, 36.0), and nothing from day 27 | price 18 (escrow, threshold 7) | deposit 18.00: 18.00 held in escrow, 0.00 released | signed day 17 | deadline day 22 (1 day ago) | price held; nothing submitted (nothing to test yet) | can be ended now
    escrow days: full price if submitted by day 22 (the deadline day, 1 day ago) | a submission made today is released at the start of day 25 if you neither approve nor reject it (sooner if the market ends first) | with nothing released, closes at the start of day 27 (later if a decision or dispute is pending then) | dispute fee 9 credits

contract-9 with M-849 | task-9 | worth 60 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 11.9, 18.5, 26.7, 36.3, 47.4, 60.0) | PAST DUE (the task's due day 10, not the contract's deadline day 14): a result delivered tomorrow, day 13, is 3 days late and worth 40% of its on-time value (at quality 5, 6, 7, 8, 9, 10: 4.7, 7.4, 10.7, 14.5, 19.0, 24.0), and nothing from day 15 | price 15 (escrow, threshold 5) | deposit 15.00: 15.00 held in escrow, 0.00 released | signed day 10 | deadline day 14 (2 days left; its task's due day is 10) | price held; nothing submitted (nothing to test yet) | its escrow closes the day after its deadline day: can be ended only after a rejection that stands
    escrow days: full price if submitted by day 14 (the deadline day) | a submission made today is released at the start of day 14 if you neither approve nor reject it (sooner if the market ends first) | with nothing released, closes at the start of day 15 (later if a decision or dispute is pending then) | dispute fee 7.50 credits

contract-6 with M-14b | task-6 | worth 60 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 11.9, 18.5, 26.7, 36.3, 47.4, 60.0) | price 21 (escrow, threshold 6) | deposit 21.00: 21.00 held in escrow, 0.00 released | signed day 8 | deadline day 13 (1 day left) | price held; 1 submission rejected; the rejection of R-d02 can be disputed through day 13 | can be ended from day 14 if M-14b does not dispute
    escrow days: full price if submitted by day 13 (the deadline day) | a submission made today is released at the start of day 14 if you neither approve nor reject it (sooner if the market ends first) | with nothing released, closes at the start of day 18 (later if a decision or dispute is pending then) | dispute fee 10.50 credits
    R-d02 submitted day 12 (#1): REJECTED day 12 (disputable through day 13) (not yours) | your readings: none

contract-8 with M-14b | task-8 | worth 60 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 11.9, 18.5, 26.7, 36.3, 47.4, 60.0) | price 24 (escrow, threshold 6) | deposit 24.00: 0.00 held in escrow, 14.40 released, 9.60 refunded | signed day 5 | deadline day 9 | RELEASED R-d04 (day 13, released with no decision; submitted 2 days late) | CLOSED: settled; it cannot be approved, rejected or ended
    R-d04 submitted day 11 (#1): RELEASED with no decision day 13; 14.40 paid to M-14b and 9.60 back to you | your readings: none

contract-5 with M-14b | task-5 | worth 60 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 11.9, 18.5, 26.7, 36.3, 47.4, 60.0) | price 16 (escrow, threshold 7) | deposit 16.00: 0.00 held in escrow, 0.00 released, 16.00 refunded | signed day 6 | deadline day 10 | CLOSED UNRELEASED (price refunded) | CLOSED: settled; it cannot be approved, rejected or ended
    R-a01 submitted day 14 (#1): REJECTED day 14, disputed: the arbiter read 6.1 and ruled for the buyer (not yours) | your readings [5.0] mean 5.00 (n=1, ±2.00 standard error)
    escrow closed unreleased: its price 16.00 came back to you
    with delivery #1 (R-a01, day 14): stated quality 7; message "m"
```

With more message items than `view.messages_shown`:

```
    (2 earlier delivery messages not listed)
    message with delivery #3 (R-b02, day 13): "msg 2"
    with delivery #4 (R-b03, day 14): stated quality 9; message "msg 3"
    message with delivery #5 (R-b04, day 14): "msg 4 x"
```

## 9. The escrow days line

The first sub-line of every held escrow contract, in both roles (an ended or settled escrow has none):

```
    escrow days: {p1} | {p2} | {p3} | dispute fee {fee} {credits}
```

`{p2}` and the ` | ` before it are omitted where stated.

- `{p1}`: `full price if submitted by day {dl} (the deadline day)` while today <= dl; after it,
  `full price if submitted by day {dl} (the deadline day, {REL(dl)})`.
- `{p2}`:
  - a submission under review:
    `{label} is released at the start of day {its day + review_days} if {undecided} (sooner if the market ends first)`;
  - otherwise, if the escrow takes a submission today:
    `a submission made today is released at the start of day {today + review_days} if {undecided} (sooner if the market ends first)`;
  - otherwise (in dispute, or today >= close(c)): omitted.

  `{undecided}`: buyer `you neither approve nor reject it`; manager `the buyer neither approves nor rejects it`.
- `{p3}`:
  - today < close(c):

    ```
    with nothing released, closes at the start of day {close} (later if a decision or dispute is pending then)
    ```

    with `{close}` = close(c), followed in the manager's view by `, and takes no new submission from that day`;
  - today >= close(c) (the escrow is held open past its close day by a submission under review, a dispute, or a
    rejection open to dispute): `closes once no decision or dispute is pending (its close day {close(c)} has come)`,
    followed in the manager's view by `; no new submission`.
- `{fee}`: `CR`(round(`contracts.escrow.dispute_fee_rate` x price, 2)). `{credits}`: `credit` when `{fee}` is
  written as exactly `1` (`dispute fee 1 credit`), else `credits` (the singular rule).

The market's last day is never shown, so every release clause carries ` (sooner if the market ends first)`.

Examples:

```
    escrow days: full price if submitted by day 9 (the deadline day) | R-cdd is released at the start of day 8 if the buyer neither approves nor rejects it (sooner if the market ends first) | with nothing released, closes at the start of day 14 (later if a decision or dispute is pending then), and takes no new submission from that day | dispute fee 9 credits
    escrow days: full price if submitted by day 14 (the deadline day) | with nothing released, closes at the start of day 19 (later if a decision or dispute is pending then) | dispute fee 10 credits
    escrow days: full price if submitted by day 7 (the deadline day, 5 days ago) | closes once no decision or dispute is pending (its close day 12 has come); no new submission | dispute fee 8 credits
    escrow days: full price if submitted by day 13 (the deadline day) | a submission made today is released at the start of day 14 if you neither approve nor reject it (sooner if the market ends first) | with nothing released, closes at the start of day 18 (later if a decision or dispute is pending then) | dispute fee 10.50 credits
```

(The second is a buyer's view with a dispute open; the third a manager's, held open by a rejection open to dispute.)

## 10. Active or finished

Every view decides afresh, for each of the viewer's contracts, whether it is active (listed in "Your active
contracts") or finished (counted in "Your finished contracts"). A contract can go back from finished to active when
a condition holds again (for instance, the manager starts a run on a plain contract past its deadline). A contract
is **overdue** when it has no delivery or submission and today - LD(c) >= `view.overdue_days`.

### 10.1 The manager's view (first rule that applies)

1. Ended: finished.
2. An escrow that is held: active.
3. Some result on it is running, or some result on it was never delivered or submitted on it and arrived on or
   after day today - `view.recent_days`: active. (So a released or closed escrow, or a plain contract past its deadline, stays
   listed, with the closed mark if settled, while it has such a result.)
4. No delivery or submission: active unless overdue.
5. Otherwise: active while today <= LD(c).

### 10.2 The buyer's view (first rule that applies)

1. Ended: finished.
2. An escrow that is held: active.
3. An escrow that was released: active while the buyer owes the arbiter's fee, or today <= due(c), or the release
   day >= today - 1.
4. No delivery or submission: active unless overdue.
5. Otherwise (a plain contract with a delivery, or an escrow closed unreleased after submissions): active while
   today <= LD(c) or the latest delivery day >= today - 1.

A plain contract leaves the active list by these rules whether or not it is paid; one delivered and not paid in full
keeps its detail lines in the finished section (section 11.3).

## 11. "Your finished contracts"

### 11.1 Order, the listed contracts and the title

Let done be the viewer's finished contracts (section 10). With none, the section is the title
`--- Your finished contracts ---` alone.

Otherwise sort done by: the latest delivery day if the contract has a delivery or submission, else LD(c); then by
signing day; contracts equal on both stay in creation order. The **latest** are the last `view.finished_shown` of
the sorted list. A contract is **owed** when it is plain, not ended, delivered, and paid < price - 1e-6. In the
buyer's view only, the **kept** are the owed contracts among the others (those before the latest), the last
`view.overflow_ids` of them in sorted order; the manager has none. The **listed** contracts are the latest and the
kept, shown in sorted order (oldest first); the rest are **older**. Title:

```
--- Your finished contracts (latest {a} of {b}) ---
--- Your finished contracts (latest {a} of {b}, and {k} older delivered and not paid in full) ---
```

a = the number of latest, b = the number in done, k = the number kept (the second form when k > 0). The count is
shown even when every contract is listed (`(latest 1 of 1)`).

### 11.2 The manager's finished line

```
{contract_id} with {buyer_id} | {task_id} | price {price}{form} | {money} | deadline day {dl} | {status} | {r} {results}
```

`{status}` in the manager's role (section 6.4, with its owed clause where it applies); no closed mark; r = the
number of results run on the contract, `{results}` = `result` if r = 1, else `results`.

### 11.3 The buyer's finished line

```
{contract_id} with {manager_id} | {task_id} | worth {worth} | price {price}{form} | {money} | deadline day {dl} | {status}{tail}
```

- `{status}` in the buyer's role; no closed mark, no end clause, no on-time values.
- `{tail}`: ` | your {READINGS(buyer readings of the label, buyer)}` when the contract has a delivery or submission,
  is plain or a released escrow, and is not owed; the label is the in-use label (plain) or the released
  submission's (escrow). Empty otherwise.
- Under every listed owed contract, its delivery lines and message lines (sections 8.2 and 8.4), each indented four
  spaces.

### 11.4 The older contracts' summary line

If any contract is older, the last line of the section is

```
Older finished contracts not listed: {n} ({ended}{e} escrow: {rel} released, {ref} refunded; of the {p} plain, {full} paid in full; {u} delivered and not paid in full, with {sum} credits of their prices unpaid; {never} never delivered).{owed_list}
```

over the older contracts:
- `{n}`: their number;
- `{ended}`: `{x} ended by the buyer; ` with x the number of them that were ended (either form), or empty if none;
- `{e}`: those that are escrow and not ended; `{rel}` of them released, `{ref}` of them closed unreleased;
- `{p}`: those that are plain and not ended; `{u}`: of these, the ones with a delivery and paid < price - 1e-6 (the
  owed ones, as the owed list counts them); `{full}`: those with a delivery and paid >= price - 1e-6; `{never}`:
  those with no delivery (so p = full + u + never); `{sum}`: `F2` of SUM (section 2.3) of price - paid over those u,
  in sorted order, which equals the owed list's total;
- `{owed_list}`: the older owed contracts, latest first, as
  ` Delivered and not paid in full: {NAMED(items)}.` with items `{contract_id} ({counterparty}, {F2 price - paid} unpaid)`;
  empty when there are none. (In the buyer's view these are the owed contracts beyond the kept ones.)

### 11.5 Examples

Manager:

```
--- Your finished contracts (latest 5 of 11) ---

contract-15 with B-c53 | task-15 | price 12 (escrow, threshold 5) | deposit 12.00: 0.00 held in escrow, 0.00 released, 12.00 refunded | deadline day 10 | CLOSED UNRELEASED (price refunded) | 0 results

contract-10 with B-c53 | task-10 | price 15 (plain) | paid 0.00 of 15.00 | deadline day 11 | in use by B-c53: R-5a0 (latest; day 9, on time) | 1 result

contract-14 with B-c53 | task-14 | price 30 (escrow, threshold 7) | deposit 30.00: 0.00 held in escrow, 30.00 released | deadline day 12 | RELEASED R-e01 (day 12, the arbiter ruled; submitted on time); B-c53 owes the arbiter's fee 15 to the arbiter (not to you) before the result is its | 1 result

contract-11 with B-c53 | task-11 | price 16 (plain) | paid 16.00 of 16.00 | deadline day 12 | in use by B-c53: R-6a0 (latest; day 10, on time) | 1 result

contract-12 with B-c53 | task-12 | price 17 (plain) | paid 0.00 of 17.00 | deadline day 13 | in use by B-c53: R-7a0 (latest; day 11, on time) | 1 result

Older finished contracts not listed: 6 (1 ended by the buyer; 0 escrow: 0 released, 0 refunded; of the 5 plain, 3 paid in full; 1 delivered and not paid in full, with 11.00 credits of their prices unpaid; 1 never delivered). Delivered and not paid in full: contract-6 (B-c53, 11.00 unpaid).
```

Buyer (the same contracts):

```
--- Your finished contracts (latest 5 of 10, and 1 older delivered and not paid in full) ---

contract-6 with M-14b | task-6 | worth 51 | price 11 (plain) | paid 0.00 of 11.00 | deadline day 7 | in use: R-1a0 (latest; day 5, on time)
    R-1a0 delivered day 5 (in use) | your readings: none

contract-8 with M-14b | task-8 | worth 53 | price 13 (plain) | paid 0.00 of 13.00 | deadline day 9 | NOT DELIVERED

contract-15 with M-14b | task-15 | worth 41 | price 12 (escrow, threshold 5) | deposit 12.00: 0.00 held in escrow, 0.00 released, 12.00 refunded | deadline day 10 | CLOSED UNRELEASED (price refunded)

contract-10 with M-14b | task-10 | worth 55 | price 15 (plain) | paid 0.00 of 15.00 | deadline day 11 | in use: R-5a0 (latest; day 9, on time)
    R-5a0 delivered day 9 (in use) | your readings: none

contract-11 with M-14b | task-11 | worth 56 | price 16 (plain) | paid 16.00 of 16.00 | deadline day 12 | in use: R-6a0 (latest; day 10, on time) | your readings: none

contract-12 with M-14b | task-12 | worth 57 | price 17 (plain) | paid 0.00 of 17.00 | deadline day 13 | in use: R-7a0 (latest; day 11, on time)
    R-7a0 delivered day 11 (in use) | your readings: none

Older finished contracts not listed: 4 (1 ended by the buyer; 0 escrow: 0 released, 0 refunded; of the 3 plain, 3 paid in full; 0 delivered and not paid in full, with 0.00 credits of their prices unpaid; 0 never delivered).
```

(The buyer counts 10 finished contracts against the manager's 11 because the released escrow contract-14, whose
arbiter's fee is owed, is still active for the buyer.) Further finished lines from buyers' views:

```
contract-12 with M-f8c | task-12 | worth 47 | price 13 (plain) | paid 3.00 | deadline day 8 | ENDED by B-c53 on day 10
contract-27 with M-bd6 | task-27 | worth 42 | price 17 (escrow, threshold 7) | deposit 17.00: 0.00 held in escrow, 0.00 released, 17.00 refunded | deadline day 20 | ENDED by B-42c on day 23 (price refunded)
contract-2 with M-14b | task-2 | worth 59 | price 25 (escrow, threshold 5) | deposit 25.00: 0.00 held in escrow, 25.00 released | deadline day 9 | RELEASED R-c0f (day 5, approved; submitted on time) | your readings [6.1] mean 6.10 (n=1, ±2.00 standard error)
Older finished contracts not listed: 11 (3 escrow: 3 released, 0 refunded; of the 8 plain, 8 paid in full; 0 delivered and not paid in full, with 0.00 credits of their prices unpaid; 0 never delivered).
```

and from a manager's view:

```
contract-8 with B-c53 | task-8 | price 24 (escrow, threshold 6) | deposit 24.00: 0.00 held in escrow, 14.40 released, 9.60 refunded | deadline day 9 | RELEASED R-d04 (day 13, released with no decision; submitted 2 days late) | 1 result
contract-41 with B-82c | task-41 | price 12 (plain) | paid 12.00 of 12.00 | deadline day 23 | in use by B-82c: R-55b (kept; day 19, on time) | 1 result
```

## 12. Status catalogue

Every combination of form and state, with what each part of an active line shows. "B" is the buyer's view, "M" the
manager's; `—` means empty.

| form, state | `{status}` | `{closed}` | buyer `{end}` | escrow days line | buyer `{late}`, `{untested}` |
|---|---|---|---|---|---|
| plain, nothing delivered | `NOT DELIVERED` | — | 6.6 rules 1, 4, 5 | no | late from due + 1; untested |
| plain, delivered | B `in use: ...`, M `in use by B-x: ...` | — | `cannot be ended (it has a delivery)` | no | — |
| plain, ended | `ENDED by B-x on day d` | — (never active) | — | no | — |
| escrow, held, nothing submitted | `price held; nothing submitted` | — | 6.6 rules 1, 3, 5 | yes | late from due + 1; untested |
| escrow, held, under review | `price held; R-x UNDER REVIEW since day ...` | — | — | yes, p2 names the label | — |
| escrow, held, rejection open to dispute | `price held; r submission(s) rejected; the rejection of R-x can be disputed through day ...` | — | `can be ended from day ... if M-x does not dispute` | yes | — |
| escrow, held, in dispute | `price held; the rejection of R-x is IN DISPUTE (...)` | — | — | yes, no p2 | — |
| escrow, held, rejection stands (window passed or ruled for the buyer) | `price held; r submission(s) rejected` | — | `can be ended now` | yes | — |
| escrow, released, fee not owed | `RELEASED R-x (day d, how; submitted ...)` | B and M marks | — | no | — |
| escrow, released, fee owed | `RELEASED ...; B-x owes the arbiter's fee ...` (role wording) | M mark only | — | no | — |
| escrow, closed unreleased | `CLOSED UNRELEASED (price refunded)` | B and M marks | — | no | untested if nothing was submitted (listed only when `view.overdue_days` > escrow_close_days) |
| escrow, ended | `ENDED by B-x on day d (price refunded)` | — (never active) | — | no | — |
