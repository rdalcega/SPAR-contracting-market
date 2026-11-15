# Manager actions

This file specifies what happens when the market runs one action of a manager (`bid`, `withdraw_bid`,
`delegate_run`, `run_myself`, `test_result`, `deliver`, `dispute`) and `pay`, the one action both roles have besides
`send_message`: for each, its arguments with their accepted names, every check in the order it runs with its exact
error text, its effects on state and money and when they happen, its exact success text, every notification it sends
to others, and its priority class in a round. The rules every action goes through before its own checks (name
matching, availability, the out-of-business refusal, the R1 refusal), the result line, and how arguments, ids, result
labels, participants, amounts, thresholds and balance checks are read are in actions-buyer.md, "1. Rules common to
every action (both roles)", which applies here unchanged; this file repeats none of it. `send_message` is in
messaging.md, "2. `send_message(to, text)`". How an `act` call's items become actions is in turns.md, "7. Reading the
reply that ends the chain"; when a round's actions run and the state each agent's checks see (R1) are in rounds.md,
"6. The action phase"; when money credited to others lands (R2) is in rounds.md, "7. Money from others lands at the
round's end (R2)". The market's own processes that these actions start or feed (run draws and arrivals, readings,
releases, rulings, closes) are in processes.md; the draws' keys are in randomness.md, "3. The draws".

The number formats (`G`, `CR`, `F2`, `Fn`, `REPR`, `INT`), the conversions `TEXT` and `PY`, what a "character" and
"whitespace" are, and the singular rule are those of README.md, "Number formats and rounding". Every text in a fenced
block is byte-exact apart from its `{placeholders}`. Wherever this file writes `{raw}`, it means `TEXT` of the
argument as sent (not normalised). Every day number, round number and count in this file's texts (`{d}`, `{r}`,
`{dr}`, `{until}`, `{auto}`, `{close}`, `{n}`, `{k}`, `{cap}`) is written in `INT`, unless a format is given with it.
Notification texts are the part after the `[day {d}, round {r}] ` prefix (actions-buyer.md, "1.13 Effects,
notifications, priority").

## 1. Shared rules of the manager's actions

### 1.1 Arguments

The rules of actions-buyer.md, "1.4 Arguments" (what counts as given; each argument is read under the one name the
prompts state; a missing required argument names it: `missing argument '{first_name}'`; arguments an action does not
read are ignored without notice) apply to these rows:

| argument | name | used by |
|---|---|---|
| post | `post_id` | bid, withdraw_bid (section 3) |
| price | `price` | bid |
| threshold | `threshold` | bid (optional) |
| contract | `contract_id` | delegate_run, run_myself, test_result, deliver, dispute; pay (optional, section 8) |
| refine | `refine` | delegate_run, run_myself (optional) |
| result | `result` | test_result, deliver (required) |
| delivery message | `message` | deliver (optional) |
| stated quality | `quality` | deliver (optional) |
| payee | `to` | pay |
| amount | `amount` | pay |
| dropped text | bid: `message`; pay: `message` | bid, pay (never sent; sections 2 and 8) |

Other names for these arguments (`post`, `bid_id` and `bid` on withdraw_bid, `amount` on bid, `quality_threshold`,
`contract`, `parent`, `result_id`, `label`, `text` on deliver, `stated_quality`, `recipient`, `price` on pay, `text` on
bid, `memo` and `text` on pay) are arguments the action does not read: an action given only such a name fails with
`missing argument '{first_name}'` when the argument is required.

Every action of sections 4 to 7 (`delegate_run`, `run_myself`, `test_result`, `deliver`, `dispute`) finds its
contract as actions-buyer.md, "1.7 "One of your contracts"" says (`pay` reads its contract as section 8 says), with
the agent required to be the contract's manager (`{contract} is not one of your contracts. {hint}`, with the contract
hint of actions-buyer.md, "1.6 Ids of tasks, posts, bids and contracts": for a manager it always adds the sentence on
bids becoming contracts and, only when the manager has open bids, the list of them). Balance checks are those of
actions-buyer.md, "1.12 Balance checks"; each action below gives its `{what}`.

### 1.2 The contract's state checks

Two checks recur. Let `{buyer}` be the contract's buyer's id.

**Ended.** If the contract's buyer has ended it (`{d}` the day it was ended):

```
{buyer} ended {contract} on day {d}: it takes no more runs, tests or deliveries
```

**Ended or escrow closed.** The ended check above; then, for an escrow contract whose escrow no longer holds the
price:

```
the escrow of {contract} was released: it takes no more runs, tests or submissions
```

when it released a result (approval, automatic release or a ruling for the manager), or

```
the escrow of {contract} closed unreleased: it takes no more runs, tests or submissions
```

when it closed without releasing one (its lateness close: processes.md, "6.9 The lateness close"). A plain contract
is never closed except by being ended: runs, tests and deliveries on it stay possible after delivery, after payment
and after its deadline, until the market closes.

### 1.3 The result lookup

`delegate_run`/`run_myself` (with `refine`), `test_result` and `deliver` name a result of the contract. With
`{purpose}` = `refined`, `tested` or `delivered` respectively, and `{label}` the label read from `{raw}`
(actions-buyer.md, "1.8 Result labels"), in this order:

1. The contract has no result with that label, but another contract of the same manager does (the first such contract
   in creation order, ended ones included):
   ```
   {label} is a result of {other}, not of {contract}: a result can be tested, refined or delivered only on the contract it was run for. {results}
   ```
2. No result with that label on the contract:
   ```
   no result '{raw}' in {contract}. {results}
   ```
3. The result has not arrived yet (`{d}` its arrival day):
   ```
   {label} has not arrived yet (it arrives at the start of day {d}), so it cannot be {purpose}
   ```

`{results}` is, when the contract has no result,

```
{contract} has no results yet (start a run with delegate_run or run_myself).
```

and otherwise `Results of {contract}: ` followed by every result of the contract in the order its run started, each
`{label} (arrived)` or `{label} (arrives day {d})`, joined by `, `, and a final `.`. Real:
`R-89b is a result of contract-26, not of contract-29: a result can be tested, refined or delivered only on the contract it was run for. Results of contract-29: R-082 (arrives day 14).`;
`R-b7b is a result of contract-8, not of contract-15: a result can be tested, refined or delivered only on the contract it was run for. contract-15 has no results yet (start a run with delegate_run or run_myself).`;
`R-560 has not arrived yet (it arrives at the start of day 13), so it cannot be refined`.

Any arrived result of the contract passes, whoever ran it, fresh or refined, and whatever happened to it (delivered,
submitted, rejected, released, not in use).

### 1.4 Money and notifications

- The manager's own costs (runs, tests, a dispute deposit) are taken from its balance at once, each followed by the
  out-of-business check of episode.md, "9.1 When", which also states how a run's or a test's charge is rounded,
  that a charge of 0 debits nothing and runs no check, and that a dispute deposit is taken as it is and always runs
  it. Its later actions of the turn then do not run (rounds.md, "6.7 A participant that goes out of business during
  a phase").
- `pay` moves the payer's money at once and credits the payee at the round's end (R2; section 8).
- No manager action credits anyone at once.
- Notifications are queued in the order given, for the recipient's next view.

## 2. bid

Places a bid on an open post, or replaces the manager's open bid on it. Free (no balance is needed). Priority class 6.

Arguments: `post_id` (required), `price` (required), `threshold` (optional; with it the bid is an escrow bid with that
threshold, without it a plain bid). `message` is dropped (never sent).

Checks, in order:

1. Post: `missing argument 'post_id'`; not found (actions-buyer.md, "1.6 Ids of tasks, posts, bids and contracts"):
   ```
   no post with id '{raw}'. Open posts: {ids}.
   ```
   (or `Open posts: none.`). Example: `no post with id 'post-999'. Open posts: post-3, post-1, post-4, post-2, post-5.`
   Under R1, a post made in the same round by a buyer is unknown on the state the manager's turn saw, so a manager can
   never bid on a post in the round it was made (rounds.md, "6.6 Running one action: the state its turn saw (R1)").
2. The post is closed:
   ```
   {post} closed {when}, when {why}{again}
   ```
   - `{when}` = `earlier this round` if it closed in the current round (same day and round, including during the
     round's corrections), else `on day {d}, round {r}` (the day and round it closed);
   - `{why}` = `its buyer accepted a bid` | `its buyer withdrew it`;
   - `{again}` = `; its task is posted again as {post2} (open)` when the post's task now has a different current post
     that is open (a repost after an ended contract), else empty.
   No list of open posts follows. Examples: `post-8 closed earlier this round, when its buyer accepted a bid`,
   `post-31 closed on day 17, round 3, when its buyer accepted a bid`,
   `post-4 closed on day 1, round 3, when its buyer accepted a bid; its task is posted again as post-4-2 (open)`.
3. Price: `missing argument 'price'`; then actions-buyer.md, "1.10 Amounts (bid's price, pay's amount)", with
   `{name}` = `price` (`price must be a number, got {v}`, `price must be a positive number`). The price used is rounded
   to the cent.
4. Threshold, when given: actions-buyer.md, "1.11 Thresholds (post_task, edit_post, bid)"
   (`threshold must be a quality between 0 and 10, got {v}`,
   `threshold must be a whole number between 0 and 10, got {v}`).

There is no other check: none against the post's stated form or threshold (a manager may bid either form at any
threshold on any post), none on the manager's balance, the post's due day or the buyer's balance.

Effects:
- If the manager has an OPEN bid on this post, that bid is updated in place: it keeps its id; its price, threshold
  (none for a plain re-bid, so a re-bid can change the form either way), day and round are replaced. Verb `updated`.
- Otherwise a new bid is created: id `bid-N`, N one more than the number of bids ever created in the market (a
  market-wide counter from 1, advanced only by a new bid; in a round, ids follow the order the bids run on the market:
  rounds.md, "6.5 The order the phase's actions run in"); open; on this post, by this manager, with this price,
  threshold, day and round. Verb `placed`. A bid the manager withdrew, or that was lost, is never reused.
- Notification to the post's buyer, only when the post's bids are not sealed from it (processes.md, "8. Sealed bids
  and their opening"; while sealed the buyer is told nothing, and learns of the bids when they are opened: episode.md,
  "4. The start of each round"):
  ```
  {manager} {verb} bid {bid} on {post} at {price}{terms}
  ```
  `{price}` = `G` of the rounded price; `{terms}` as in the success text below. Examples:
  `M-fda placed bid bid-19 on post-9 at 30 with threshold 6 (escrow)`;
  `M-849 updated bid bid-3 on post-1 at 16 with threshold 6 (escrow)`.

Success:

```
{verb} {bid} on {post} at {price}{terms}{note}
```

- `{price}` = `G` of the rounded price (`25`, `24.55`, `1e+16`).
- `{terms}` = ` with threshold {t} (escrow)` (`{t}` in `G`) for an escrow bid, else empty.
- `{note}` = ` (bid takes no message: yours was not sent)` when `message` was given (actions-buyer.md,
  "1.4 Arguments": any value other than null and `""`, so whitespace, `false` and `0` count), else empty. The text
  itself goes nowhere. (`text` is then an argument bid does not read, ignored without notice.)

Examples: `placed bid-1 on post-1 at 25 with threshold 5 (escrow)`, `updated bid-66 on post-26 at 9.5`,
`placed bid-1 on post-1 at 20 (bid takes no message: yours was not sent)`.

Clash rules (rounds.md, "6.3 The clash rules"): an `accept_bid` of this manager's bid on the post runs before its
replacing bid (A8: when the acceptance succeeds, the replacement then fails as the post is closed, and the contract is
on the terms the buyer saw);
an `accept_bid` of another bid on the post runs before it (B_accept); a `withdraw_post` of the post runs before it
(B_withdraw). The bid then fails (check 2) only when that acceptance or withdrawal succeeds; a refused one closes
nothing, and the bid runs as usual (section 9). The manager's system prompt says, more briefly, that a bid fails "if
the post's buyer accepts a bid or withdraws the post in the same round"; that text is reproduced as it is.

## 3. withdraw_bid

Withdraws the manager's open bid on a post. Free. Priority class 5.

Arguments: `post_id` (required: `missing argument 'post_id'`). A bid id is not read: given as `post_id` it is looked
up as a post (`no post with id 'bid-3'. Open posts: {ids}.`), and `bid_id` alone gets `missing argument 'post_id'`.

Finding the bid:
- `post_id` is looked up as a post (actions-buyer.md, "1.6 Ids of tasks, posts, bids and contracts":
  `no post with id '{raw}'. Open posts: {ids}.`). The bid is this manager's open bid on it, else its bid on it created last, else:
  ```
  you have no bid on {post}
  ```
- (R1) In a round's action phase, the bid withdrawn is the one found this way on the state the manager's turn saw,
  including its own earlier successful actions in that phase (rounds.md, "6.6 Running one action: the state its turn
  saw (R1)"). Under the market's rules the market, when the action runs, finds the same bid on the same post.

Checks, in order, after finding the bid:

1. The bid was accepted (`{contract}` the contract made from this post):
   ```
   {bid} on {post} was accepted: it is a contract now ({contract}) and cannot be withdrawn
   ```
2. The bid is not open (`{status}` = `lost` or `withdrawn`):
   ```
   {bid} on {post} is {status}: there is nothing to withdraw
   ```
   Real: `bid-23 on post-7 is lost: there is nothing to withdraw`.

Effects: the bid becomes withdrawn (it leaves the buyer's bid list; bidding on the post again places a new bid with a
new id). Notification to the post's buyer, only when the post's bids are not sealed from it (while sealed, nothing):

```
{manager} withdrew its bid {bid} on {post}
```

Success:

```
withdrew {bid} on {post}; bidding on {post} again would place a new bid
```

Real: `withdrew bid-43 on post-22; bidding on post-22 again would place a new bid`.

Clash rules (rounds.md, "6.3 The clash rules"): an `accept_bid` of this bid runs before it (A9: when the acceptance
succeeds, the withdrawal then fails as accepted); an `accept_bid` of another bid on the post (B_accept) and a `withdraw_post` of the post
(B_withdraw) run before it.

## 4. delegate_run and run_myself

Start a run on one of the manager's contracts: `delegate_run` by the manager's employee (worker `employee`),
`run_myself` by the manager itself (worker `manager`). Cost: the run's drawn cost,
charged in full when the run starts. Priority class 3.

Arguments: `contract_id` (required), `refine` (optional: the label of an arrived result of the same contract to
refine; not given: a fresh run). (A `refine` of `"None"` or `"null"` is no exception: it is read as a label, and
the refine check below refuses it.)

With `w` = `production.employee` (delegate_run) or `production.manager` (run_myself) and `{action}` = `delegate_run`
or `run_myself`, checks, in order:

1. The contract (1.1): `missing argument 'contract_id'`; `no contract with id '{raw}'. {hint}`;
   `{contract} is not one of your contracts. {hint}`.
2. Ended or escrow closed (1.2).
3. When `refine` is given: the result lookup (1.3) with `{purpose}` = `refined`. The parent can be any arrived result
   of the contract, by either worker (a manager's run can refine an employee's result and the reverse).
4. Capacity. The worker's runs in flight are all its runs not yet arrived, across ALL of the manager's contracts in
   creation order and, within a contract, in the order they started (runs on contracts since ended or closed count
   until they arrive). A run started earlier in the same act call counts. Runs are not queued. When their number is at
   least `w.capacity`:
   - run_myself (`{label}`, `{contract2}`, `{d}` of the FIRST of the manager's own runs in flight; `{cap}` =
     `production.manager.capacity` as `INT`):
     ```
     you are busy on {label} ({contract2}) until day {d}; you can do {cap} run(s) at a time
     ```
     (`run(s)` literally. Real: `you are busy on R-885 (contract-6) until day 10; you can do 1 run(s) at a time`.)
   - delegate_run (`{n}` the number in flight, `{cap}` = `production.employee.capacity`, both `INT`; `{runs}` every
     employee run in flight as `{label} on {contract2}, arrives day {d}`, joined by `, `):
     ```
     your employee is busy: {n} of {cap} runs in flight ({runs})
     ```
     *(singular rule)*: `runs` follows `{cap}`, so a capacity of 1 gives `1 of 1 run in flight`.
     Real: `your employee is busy: 2 of 2 runs in flight (R-161 on contract-6, arrives day 9, R-c87 on contract-7, arrives day 9)`.
5. The contract already holds 4096 results (every label is used):
   ```
   {contract} has no result labels left ({n} results)
   ```
6. The run is drawn: its quality, days and cost, from the key of randomness.md, "3.8 A run's quality, days and cost"
   (k = the number of runs of this worker and kind already started on this contract; kind `refine` when `refine` was
   given, else `fresh`), with the laws of processes.md, "1.2 The laws of a run". The cost is rounded to the cent.
7. The balance check (actions-buyer.md, "1.12 Balance checks") against the drawn cost, `{what}` = `{action}`:
   ```
   insufficient balance for {action}: it costs {cost}, you have {balance}
   ```
   `{cost}` = `CR` of the drawn cost (`7.56`, `4.10`, `4`). A cost exactly
   equal to the balance passes (when the cost is above 0, the manager then ends at 0 and is out of business; a cost
   of 0.00 debits nothing and runs no check, 1.4). A refused run leaves k unchanged, so the next run of that worker
   and kind on that contract reuses the same draws (randomness.md, "3.8 A run's quality, days and cost").

A run refused at any step changes nothing: no charge, no label, k unchanged.

Effects, in this order:
1. k for this worker and kind on this contract advances by 1.
2. A label is drawn for the result (randomness.md, "3.9 Result labels").
3. The result is created on the contract, running: label, worker, kind, parent (for a refinement), start day = today,
   arrival day = today + the drawn days, its cost and its true quality (never shown). It appears at once in the
   manager's own view (view-contracts.md, "5. The manager's "Your production"") and occupies one of the worker's slots
   until it arrives at the start of its arrival day (processes.md, "1.4 Arrival": the arrival and its notification).
4. The cost is charged at once (booked as `employee runs` for delegate_run, `your own runs` for run_myself), then the
   out-of-business check (1.4).

No one else is notified: the buyer never learns of runs, their costs or their labels until a result is delivered or
submitted to it.

Success:

```
started {label} on {contract} ({origin}): it arrives at the start of day {d}; charged {cost} credits
```

- `{origin}` = `your employee, fresh` | `your employee, refine of {parent}` | `you, fresh` | `you, refine of {parent}`
  (`{parent}` the refined result's label).
- `{d}` = the arrival day; `{cost}` = `F2` of the cost charged (`3.08`, `4.00`).

Real, one manager in one episode: `started R-dbe on contract-3 (your employee, fresh): it arrives at the start of day 3; charged 4.41 credits`
(day 2); `started R-5b6 on contract-3 (your employee, refine of R-dbe): it arrives at the start of day 4; charged 3.06 credits`
(day 3); `started R-390 on contract-14 (you, fresh): it arrives at the start of day 15; charged 8.11 credits`.

Clash rules (rounds.md, "6.3 The clash rules"): the buyer's `end_contract` (A4) and `approve_submission` (A6) on the
same contract run before it in the same round (when the end or approval succeeds, the run then fails by check 2 and
nothing is charged); it runs before an `accept_bid` of any of this manager's bids (A10). A cycle with the
participants' own orders drops the weakest clash that closes it, by the prompts' priority (rounds.md, "6.5 The order
the phase's actions run in"): A4 and A6 (rule iv) give way only to A1-A3 (rule iii), and the run then starts and is
charged before the end or approval runs; A10 (rule ii) gives way to every other rule (e.g. the buyer [`accept_bid`
of this manager's bid on another post, `approve_submission` C], the manager [`run_myself` C] drops A10: the
approval runs first and the run fails uncharged).

## 5. test_result

Takes one noisy reading of an arrived result of one of the manager's contracts. Costs `tests.manager_cost`. Priority
class 3.

Arguments: `contract_id` (required), `result` (required).

Checks, in order:

1. The contract (1.1).
2. Ended or escrow closed (1.2).
3. `missing argument 'result'` (checked after 1 and 2).
4. The result lookup (1.3) with `{purpose}` = `tested`.
5. The balance check, `{what}` = `test_result`, cost `tests.manager_cost`:
   `insufficient balance for test_result: it costs 1, you have 0.40` (`{cost}` in `CR`).

Effects: `tests.manager_cost` is charged at once (booked as `tests`), then the out-of-business check (1.4). The
balance check (step 5) uses `tests.manager_cost` as configured; a charge that rounds to 0 debits nothing (1.4), while
the reading is still taken and the success text still states `tests.manager_cost` (`{cost}` below). One reading is
drawn (randomness.md, "3.10 Test readings"; processes.md, "2.1 The law", manager side) and recorded among the
manager's readings of that result. A result can be tested any number of times. Nobody else is
told: the buyer never learns of the manager's tests or readings.

Success:

```
{label} reading: {value} (charged {cost}). Your readings [{more}{shown}] mean {mean} (n={n}, ±{se} standard error)
```

- `{value}`: the reading in `Fn`, n = `tests.decimals` (`4.6`, `0.0`, `10.0`).
- `{cost}`: `CREDITS` of `tests.manager_cost` (`1 credit`; at 0.5: `0.5 credits`).
- `readings [{more}{shown}] mean {mean} (n={n}, ±{se} standard error)` is the readings text of processes.md, "2.3 The
  readings text" (`{shown}` is its `{values}`), over all the manager's readings of this result, this one included,
  on the manager's side (sd = `tests.manager_sd`). The mean is `F2` of SUM / n (README.md, "Number formats and
  rounding"): readings 1.0, 2.6, 6.5, 1.2 give `2.83` (plain addition would give `2.82`); 5.1, 9.0, 2.6, 0.4 give
  `4.28` (not `4.27`).

Example: `R-dbe reading: 4.6 (charged 1 credit). Your readings [4.6] mean 4.60 (n=1, ±0.50 standard error)`.
The reading comes back only with the action's result (the next view, or the correction if the turn gets one): it
cannot inform a delivery in the same act call.

Clash rules (rounds.md, "6.3 The clash rules"): the buyer's `end_contract` (A5) and `approve_submission` (A7) on the
same contract run before it in the same round (when the end or approval succeeds, the test then fails by check 2 and
nothing is charged); it runs before an `accept_bid` of any of this manager's bids (A10). A cycle with the
participants' own orders drops the weakest clash that closes it, by the prompts' priority (rounds.md, "6.5 The order
the phase's actions run in"): A5 and A7 (rule iv) give way only to A1-A3 (rule iii), and the test is then taken and
charged before the end or approval runs (e.g. the manager [`test_result` C, `deliver` C], its buyer [`end_contract`
C]); A10 gives way to every other rule.

## 6. deliver

Gives an arrived result to the contract's buyer: on a plain contract a delivery, on an escrow contract a submission
for the buyer's review. Free (the manager never pays an escrow fee). Priority class 1.

Arguments: `contract_id` (required), `result` (required), `message` (optional), `quality` (optional: the quality the
manager states).

### 6.1 Checks, in order

1. The contract (1.1).
2. Ended (1.2, the ended text only).
3. `missing argument 'result'`, then the result lookup (1.3) with `{purpose}` = `delivered`.
4. Escrow contract, in this order (`{label}` the result named):
   - the escrow released a result (`{released}` its label):
     ```
     the escrow of {contract} was released on {released}: the contract is closed and takes no more submissions
     ```
   - the escrow closed unreleased:
     ```
     the escrow of {contract} closed unreleased and refunded its buyer: it takes no more submissions
     ```
   - a submission is under the buyer's review (`{under}` its label, `{auto}` its automatic release day = its
     submission day + `contracts.escrow.review_days`):
     ```
     {under} is under {buyer}'s review on {contract}: one submission at a time (it is released at the start of day {auto} if the buyer neither approves nor rejects it)
     ```
   - a dispute is open (`{disputed}` the disputed label, `{r}` the ruling day):
     ```
     your dispute of the rejection of {disputed} on {contract} is open: the arbiter rules at the start of day {r}, and {contract} takes no submission until then
     ```
   - today is on or after the escrow's close day (`{close}` = the task's due day + escrow_close_days: config.md,
     "6.2 escrow_close_days"):
     ```
     the escrow of {contract} takes no new submission from its close day {close}, when a release would pay you nothing; it closes once nothing is pending
     ```
   - the label was submitted on this contract before (it can only have been rejected):
     ```
     {label} was already submitted to {contract} and rejected: submit another result (a refinement of it is a new result)
     ```
5. Plain contract: the label was delivered on this contract before (`{d}` the day it was delivered):
   ```
   {label} was already delivered for {contract} on day {d}: on a plain contract a result is delivered once, and the buyer can keep it
   ```
   Real: `R-d0a was already delivered for contract-9 on day 12: on a plain contract a result is delivered once, and the buyer can keep it`.
6. The message has no check: when given, `TEXT(message)` (a list or an object as `PY`; `false` gives `False`, `0`
   gives `0`), silently cut to its first `contracts.message_chars` characters. It is not trimmed: whitespace alone is
   a message. Not given: no message.
7. The stated quality, when given: read as actions-buyer.md, "1.11 Thresholds (post_task, edit_post, bid)", reads a
   threshold (a boolean, a list, an object or an unreadable string counts as NaN); if NaN or not in [0, 10] (so also
   `inf`):
   ```
   quality must be a number between 0 and 10, got {v}
   ```
   with `{v}` = `PY(quality)` (`got 11`, `got True`, `got 'abc'`, `got 'nan'`). Otherwise the stated quality is
   round(x, `tests.decimals`) (`"8.25"` gives 8.2; `-0` becomes 0). No whole-number requirement. A JSON integer too
   large for a finite double is outside this spec (README.md, "Number formats and rounding"): an implementation may
   refuse it with this text (preferred) or behave as the reference implementation, which ends the episode with status
   `error`.

### 6.2 Effects common to both forms

- The delivery is numbered: `{n}` = the number of deliveries and submissions on the contract, this one included.
- It is recorded on the contract with its label, day, round, number, message and stated quality; it becomes the
  contract's latest delivery.
- A plain contract's first delivery starts the close's wait for its buyer to see it (episode.md, "5.1 Definitions",
  an UNSEEN UNPAID DELIVERY).
- No money moves.
- `{when}` = `by the deadline` when today is not after the contract's deadline (processes.md, "3.1 The contract's
  deadline"); else `{k} day after the deadline` (k = 1) or `{k} days after the deadline` (k >= 2), with
  k = today − the deadline. A result delivered on the deadline day is on time.
- `{note}` = ` Stated quality: {q}.` (`{q}` in `G`) when a quality was stated, followed by
  ` Message: "{ONE_LINE(message)}".` when there is a message (on one line: view-contracts.md, "2.3 Shared texts",
  ONE_LINE; no escaping). Either part may be absent; with neither, empty. The contract's record keeps the message as
  sent.

### 6.3 Plain contract: the delivery

The label becomes the delivery in use, unless the buyer has kept another delivery (actions-buyer.md, "7.
keep_delivery"), which then stays in use. The task counts as done for the close (episode.md, "5.1 Definitions").

Notification to the buyer:

```
{manager} delivered {label} for {contract} (delivery #{n}).{note} You can test_delivery it.{keep}
```

`{keep}` = ` You kept {kept}, which stays in use unless you keep this one (keep_delivery).` when the buyer has kept a
label (`{kept}`); else, when this is not the contract's first delivery,
` It is now the delivery in use, as the latest; you can keep an earlier delivery instead (keep_delivery).`; else
empty. Example (the message sent as `first try` + line break + `line2`):
`M-14b delivered R-baf for contract-1 (delivery #1). Stated quality: 6. Message: "first try line2". You can test_delivery it.`

Success:

```
delivered {label} for {contract} to {buyer} (delivery #{n}, {when}){kept_tail}
```

`{kept_tail}` = `. The buyer kept {kept}, which stays in use unless it keeps this one` when the buyer has kept a label,
else empty. Real: `delivered R-27a for contract-1 to B-44c (delivery #3, by the deadline). The buyer kept R-51c, which stays in use unless it keeps this one`;
example: `delivered R-c0f for contract-2 to B-c53 (delivery #1, by the deadline)`.

### 6.4 Escrow contract: the submission

The submission goes under the buyer's review, with `{auto}` = today + `contracts.escrow.review_days`: unless the buyer
approves or rejects it first, it is released at the start of day `{auto}` (processes.md, "6.5 Automatic release"). A new
submission after a rejection ends the chance to dispute that rejection (only the latest submission's rejection can be
disputed, section 7).

Notification to the buyer:

```
{manager} submitted {label} for your review on {contract} (submission #{n}).{note} Approve it with approve_submission to release the price, or reject it with reject_submission; if you do neither, it is released at the start of day {auto}. You can test_delivery it.
```

Example: `M-14b submitted R-baf for your review on contract-1 (submission #1). Stated quality: 9. Message: "please approve". Approve it with approve_submission to release the price, or reject it with reject_submission; if you do neither, it is released at the start of day 6. You can test_delivery it.`
(a submission on day 4).

Success:

```
submitted {label} for {buyer}'s review on {contract} (submission #{n}, {when}); if the buyer neither approves nor rejects it, it is released at the start of day {auto}
```

Real: `submitted R-07d for B-42c's review on contract-29 (submission #1, 1 day after the deadline); if the buyer neither approves nor rejects it, it is released at the start of day 24`;
`submitted R-cc9 for B-1cf's review on contract-21 (submission #3, 2 days after the deadline); if the buyer neither approves nor rejects it, it is released at the start of day 22`.

Clash rules (rounds.md, "6.3 The clash rules"): it runs before the buyer's `end_contract` on the same contract in the
same round (A1 plain, A2 escrow, A3 escrow whose latest submission was rejected): when the delivery or submission
succeeds, it counts and the end then fails; the buyer's `keep_delivery` on the contract runs before it (B_keep), so
its texts name the kept label. A cycle with the participants' own orders drops the weakest clash that closes it, by
the prompts' priority (rounds.md, "6.5 The order the phase's actions run in"): A1, A2 and A3 (rule iii) give way to no
other rule, so the delivery or submission still runs before the end; B_keep gives way to A1-A9 (e.g. the manager
[`deliver` X, `deliver` Y], the buyer [`end_contract` Y, `keep_delivery` X]): the delivery then runs before the keep
and its texts, coming in the order the actions ran, do not name the label kept in that round, while the end state is
the same.

## 7. dispute

Disputes the buyer's rejection of the latest submission on an escrow contract; the arbiter rules on it at the start
of the next day. Costs a deposit. Priority class 3.

Arguments: `contract_id` (required).

Let `fee` = round(`contracts.escrow.dispute_fee_rate` × price, 2) (the deposit; the arbiter's fee is the same amount).
For a rejection decided on day `dr`, `until` = dr + `contracts.escrow.dispute_window_days`.

Checks, in order:

1. The contract (1.1).
2. Ended (1.2, the ended text).
3. Plain contract:
   ```
   {contract} is a plain contract: only a rejection on an escrow contract can be disputed
   ```
4. The escrow no longer holds the price:
   ```
   the escrow of {contract} was released: there is nothing to dispute
   ```
   or
   ```
   the escrow of {contract} closed unreleased: there is nothing to dispute
   ```
5. A dispute is open on the contract (`{label}` the disputed label, `{r}` the ruling day):
   ```
   your dispute of the rejection of {label} is open: the arbiter rules at the start of day {r}
   ```
6. The latest submission was not rejected:
   ```
   {contract} has no rejection to dispute: {what}
   ```
   `{what}` = `{label} is under the buyer's review` (a submission is under review) | `nothing has been rejected` (no
   submission yet) | `its latest submission was not rejected`.
7. The latest submission's rejection was already disputed (and the arbiter ruled for the buyer):
   ```
   the rejection of {label} was already disputed, and the arbiter ruled for the buyer
   ```
8. Its window has passed (today > until):
   ```
   the rejection of {label} on day {dr} could be disputed through day {until}
   ```
9. The balance check, cost `fee`, `{what}` = `disputing the rejection of {label} (a deposit of {fee_cr})` with
   `{fee_cr}` = `CR` of the fee:
   ```
   insufficient balance for disputing the rejection of {label} (a deposit of {fee_cr}): it costs {cost}, you have {balance}
   ```
   (`{cost}` = `CR` of the fee, e.g. `it costs 10.55`, `it costs 5`.)

Effects:
- The fee leaves the manager's balance at once as a deposit (booked as `dispute deposits`); the market holds it with
  the escrow's money until the ruling. Then the out-of-business check (1.4).
- The rejection is disputed: the ruling day is today + 1 (fixed, whatever `contracts.escrow.dispute_window_days`
  is). Until then the contract takes no submission (section 6.1) and the buyer cannot end it. The ruling at the start
  of the ruling day, its money and its notifications are in processes.md, "6.6 Dispute and the arbiter's ruling"; a
  dispute still open at the close is in episode.md, "6. At the close".

Notification to the buyer (`{t}` = the contract's threshold in `G`):

```
{manager} disputed your rejection of {label} on {contract}. The arbiter takes one reading of it at the start of day {r}: at or above the threshold {t}, the price is released to {manager} and you pay the arbiter's fee of {fee_cr} to receive {label}; below it, your rejection stands and {manager} pays the fee.
```

Success:

```
disputed the rejection of {label} on {contract}: {fee_cr} deposited. The arbiter takes one reading of {label} at the start of day {r}: at or above the threshold {t} you win, the price is released to you and your deposit comes back; below it you lose the deposit
```

`{r}` = today + 1. Real: `disputed the rejection of R-b8b on contract-14: 11 deposited. The arbiter takes one reading of R-b8b at the start of day 15: at or above the threshold 5 you win, the price is released to you and your deposit comes back; below it you lose the deposit`.

Clash rule (rounds.md, "6.3 The clash rules"): it runs before an `accept_bid` of any of this manager's bids in the
same round (A10; a cycle can drop it: rounds.md, "6.5 The order the phase's actions run in").

## 8. pay

Pays credits to another participant. Both roles have it. Free besides the amount. Priority class 0.

Arguments: `to` (required), `amount` (required), `contract_id` (optional). `message` is dropped (never sent).
Below, `{payer}` and `{payee}` are the two participants' ids.

The contract argument is read as every other action reads it: a contract is named when `contract_id` counts as given
(actions-buyer.md, "1.4 Arguments": any value other than null and `""`), and that value is then looked up
(actions-buyer.md, "1.6 Ids of tasks, posts, bids and contracts"). So `contract_id: 0` gives
`no contract with id '0'. Your contracts: ...`, `false` gives `no contract with id 'False'. ...`, and `contract` is
ignored.

Checks, in order:

1. Payee: `missing argument 'to'`; not a participant (actions-buyer.md, "1.9 Participants"):
   ```
   no participant with id '{raw}' (participants: {ids})
   ```
2. The payee is the payer:
   ```
   you cannot pay yourself
   ```
3. Amount: `missing argument 'amount'`; then actions-buyer.md, "1.10 Amounts (bid's price, pay's amount)", with
   `{name}` = `amount` (`amount must be a number, got {v}`, `amount must be a positive number`). The amount used is
   rounded to the cent. There is no minimum.
4. A contract named: looked up as in actions-buyer.md, "1.6 Ids of tasks, posts, bids and contracts"
   (`no contract with id '{raw}'. {hint}`, the payer's contract hint; e.g. `contract_id: 0` gives
   `no contract with id '0'. Your contracts: ...`). Then, in order, with `{rule}` =
   `a payment can name only a plain contract on which you are the buyer and the payee is the manager`:
   - the payer is its manager: `{contract} is a contract on which you are the manager: {rule}`
   - the payer is not its buyer: `{contract} is not one of your contracts: {rule}`
   - the payee is not its manager (`{m}` its manager):
     `{contract} is your contract with {m}, not with {payee}: {rule}`
   - it is an escrow contract: `{contract} is an escrow contract: the escrow pays it, and {rule}`
   - the buyer ended it (`{d}` the day):
     `{contract} was ended on day {d}: nothing is owed on it, and a payment can't name it`
   A named contract may be undelivered (an advance) or already paid in full.
5. No contract named: the payment counts toward the payer's oldest unpaid plain contract with the payee, if any:
   among the contracts on which the payer is the buyer and the payee the manager, plain, not ended, and with
   paid < price − 1e-9, those with at least one delivery first, then those with none, each group in creation order;
   the first. Otherwise (no such contract; a payment by a manager; a payment to a buyer) it is on no contract. In a
   round's action phase this target is the one found on the state the payer's turn saw (R1: rounds.md, "6.6 Running
   one action: the state its turn saw (R1)").
6. The balance: when amount > balance + 1e-9:
   ```
   insufficient balance ({balance})
   ```
   (`{balance}` in `F2`, possibly negative: a buyer whose token charges put it below 0 can pay nothing). Money
   credited to the payer by others in the same round has not landed yet (R2).

Effects:
- The payer's balance falls by the amount at once (booked as `payments made`). This is not a charge: it never puts a
  manager out of business, even when it leaves the balance at 0; its next charge does (episode.md, "9.1 When").
- The payee is credited the amount at the round's end (R2: rounds.md, "7. Money from others lands at the round's end
  (R2)"; booked as `payments received`). If the payee is a manager out of business whose balance is then above 0, it
  is back in business (episode.md, "9.3 Coming back"), with its notification at that time.
- When the payment counts toward a contract and the payer is that contract's buyer (always so for a contract, as
  only such contracts can be named or targeted): `owed` = max(0, round(price − paid, 2));
  `to_price` = round(min(amount, owed), 2) is added to the contract's paid amount (stored as round(paid + to_price,
  4), so paid never exceeds the price); `beyond` = round(amount − to_price, 2), when above 0, is recorded on that same
  contract as paid on top of its price (it does not spill to another contract). Each payment's `beyond` is kept as
  its own part, in the order the payments were made; the contract's total paid on top of its price is the `SUM`
  (README.md, "Number formats and rounding") of its parts in that order, not rounded further (a total over several
  contracts is defined where it is used: view.md, "9.2 The line"). The paid amount shows in the
  contract's lines (view-contracts.md, "6.2 `{money}`"); the parts paid on top of plain prices show in the record
  with the counterparty (view.md, "9.2 The line"). A contract is paid in full when paid >= price − 1e-6 (episode.md,
  "5.1 Definitions").
- The payment is listed in both parties' payments (view.md, "6. "Payments"").
- The message, if any, is not sent and not shown to anyone.

Notification to the payee:

```
{payer} paid you {amount}{for}
```

`{amount}` = `CR` of the amount; `{for}` = ` for {contract}` when the payment counts toward a contract (named or
found by step 5), else empty. Real: `B-c53 paid you 30 for contract-1`; example: `B-d75 paid you 5`.

Success:

```
paid {amount} to {payee}{for}{note}{unsent}
```

- `{amount}` = `CR` (`15`, `5.55`, `0.50`, `1234567`: `paid 1234567 to M-849`). `{for}` as above.
- `{note}`, only for a payment on a contract with `beyond` > 0: ` ({part} of it paid the rest of its price; the other
  {extra} came on top of it)` when `to_price` > 0 (`{part}` = `CR` of to_price, `{extra}` = `CR` of beyond), or
  ` (its price was already paid in full: this came on top of it)` when `to_price` = 0; else empty.
- `{unsent}` = ` (pay takes no message: yours was not sent)` when `message` is given (actions-buyer.md, "1.4
  Arguments") and is a list, an object, a number, a boolean, or a string with at least one non-whitespace character;
  a string of whitespace only gives no note. `memo` and `text` are arguments pay does not read, ignored without
  notice.

Real: `paid 15 to M-f8c for contract-3`, `paid 11 to B-79d`,
`paid 11 to M-f8c for contract-6 (6 of it paid the rest of its price; the other 5 came on top of it)`,
`paid 6 to M-f8c for contract-11 (its price was already paid in full: this came on top of it)`; example:
`paid 10 to M-14b for contract-2 (pay takes no message: yours was not sent)`.

No clash rule involves pay.

## 9. Priority classes and clash rules of these actions

| action | class | clash rules (rounds.md, "6.3 The clash rules") |
|---|---|---|
| pay | 0 | none |
| deliver | 1 | A1, A2, A3 (before the buyer's end_contract on the contract); B_keep (after the buyer's keep_delivery on it) |
| delegate_run, run_myself | 3 | A4 (after end_contract), A6 (after approve_submission) on the contract; A10 (before accept_bid of the manager's bids) |
| test_result | 3 | A5 (after end_contract), A7 (after approve_submission) on the contract; A10 |
| dispute | 3 | A10 |
| withdraw_bid | 5 | A9 (after accept_bid of the bid); B_accept (after accept_bid on the post); B_withdraw (after withdraw_post of the post) |
| bid | 6 | A8 (after accept_bid of the manager's bid on the post); B_accept; B_withdraw |

A clash rule fixes the order in which two actions are attempted, not their outcome: each action still goes through
all its checks, and the consequences this file states for the later action (it "then fails") hold only when the
earlier one succeeds. A failed `end_contract`, approval or acceptance does not itself make the other action fail
(e.g. a buyer's `end_contract` refused because the deadline has not passed, followed by the manager's run on the
same contract: the run starts and is charged).

A cycle between the participants' own orders and these rules drops the edges into the action that rounds.md, "6.5
The order the phase's actions run in" places to break it, by the prompts' priority: rule iii (A1-A3) holds first,
then rule iv (A4-A7), then rule i (A8, A9), then the B rules, then rule ii (A10), which gives way to all others (the
rules within one of these groups are equally strong); that section works through the cases. An action that is not its participant's own takes part in no clash (rounds.md, "6.2 Targets: what each
action acts on").

The buyer's actions' classes are in actions-buyer.md, "12. Priority classes". The kind values that order a phase are
in rounds.md, "6.4 The fixed order of kinds"; the texts a losing action's line gets in a correction are in rounds.md,
"8.2 What each failed action is told".
