# The episode

This file covers one episode outside its rounds: how it starts (participants, IDs, seat order, wealth shares, start
balances, per-task budgets, the hidden last arrival day), the day loop, everything that happens at the start of a day
and at its end, in exact order (task arrivals, result arrivals, due-day notices, arbiter rulings, automatic releases,
escrow lateness closes), when arrivals stop, the close rule, what happens at the close, the final settlement of every
contract, the scores and welfare, and out of business. What happens inside a round is in `rounds.md`; the mechanics
and texts of runs, readings and the escrow's timers are in `processes.md`; the keys of the random draws are in
`randomness.md`.

`message_channels` changes nothing in this file, except that the conversation summarizer is used when it is not `off`
(1.4).

Number formats (`G`, `CREDITS`, `F2`, `INT`, ...), `round(x, n)` and the singular rule are the README's ("Number
formats and rounding").

## 1. Episode start

Nothing is shown to agents at the start of an episode except, at each agent's first turn, its system prompt
(`prompts.md`) and its first view (`view.md`). The draws of 1.1 to 1.3 may be made in any order: each is a keyed draw
that depends on nothing else (`randomness.md`, "The keyed-draw contract"), and an ID is checked for a repeat against
the participants of earlier seats (1.1), whatever order the draws are made in. The steps of 1.4 come after them, in the
order of `running.md`, "3. One episode".

### 1.1 Participants, IDs and seat order

- There are `participants.buyers` buyers and `participants.managers` managers. Each role's seats are numbered
  0 .. n-1.
- Seat order is buyer seat 0, 1, ..., then manager seat 0, 1, .... It is the order of:
  - the "Participants" line of every view (`view.md`, "Lines and the header"; not sorted);
  - the list the turn order of each round is shuffled from (`randomness.md`, "The turn order of a round");
  - task arrivals each day (3.2);
  - the agents in the settlement's scores (8).
- Each participant gets an ID: `B-` for a buyer, `M-` for a manager, followed by 3 lowercase hexadecimal digits,
  zero-padded (`B-c53`, `M-0a4`). The number is drawn uniformly from 0 .. 4095, one draw per seat, participants
  created in seat order. A drawn ID that a participant created earlier already has is drawn again until it is new
  (`randomness.md`, "Participant IDs"). Only full IDs must differ: `B-abc` and `M-abc` may both exist. The 3 digits
  are fixed, not a parameter.
- Each participant's model is its role's model spec, or its seat's override (`config.md`, "Models, the `llm` block
  and token prices"; `models.md`).

### 1.2 The last arrival day L

The episode has two periods. Tasks arrive on days 1 .. L. From day L + 1 on, no task arrives, and the market trades
on until the close (5). L is drawn once, at the start: L = `time.horizon_min_days` with probability
`time.horizon_end_prob`, else each later day is the last with that probability, up to the horizon cap, which takes the
rest of the tail. The cap is `time.horizon_min_days` + n, where n is computed as `config.md`, "6.1 The horizon cap"
gives it (the smallest integer >= 0 with (1 - end_prob)^n < 0.01; at the rare values within rounding error of a
boundary either neighbour is accepted); 54 at the defaults. The draw: `randomness.md`, "3.2 The last arrival day L (the horizon)".

L is never shown or told to any agent, by any text. The view's day header has no "of N" (`view.md`, "Lines and the
header"). L is not the market's last day: the market can run past the cap, up to max(L, the latest due day +
`time.close_after_due_days` - 1) (5.2).

### 1.3 Wealth shares, start balances and per-task budgets

For every participant (both roles), one share is drawn:

```
share = round(uniform(lo, hi), money.share_decimals)       # [lo, hi] = money.share_range
```

`uniform(lo, hi)` = lo + (hi - lo) x U, U uniform on [0, 1). A seat's share depends only on the seed, its role and
its seat index, so adding a seat does not change the others' shares (`randomness.md`, "Wealth shares").

- Start balance (both roles) = round(`money.start_amount` x share, 2).
- Per-task budget (buyers) = round(`money.task_budget` x share, 2). It is credited to the buyer each time one of its
  tasks arrives (3.2). There is no other income: managers get nothing from the market after the start.
- When the share is exactly 1, the start balance is `money.start_amount` and the per-task budget is
  `money.task_budget`, unrounded.
- The balance starts at the start balance. No agent ever sees another agent's share or start balance.

At the defaults (seed-dependent): share 1.36 gives start balance 81.60 and per-task budget 40.80.

The system prompts state each agent's share, start balance and (buyers) per-task budget, each in `F2` (`prompts.md`,
"The fields": `{share}`, `{start_balance}`, `{budget}`).

### 1.4 Other start-of-episode steps

- When `message_channels` is not `off`, the conversation summarizer is used (`messaging.md`, "5. The summarizer").
  Which model backends are set up, and how setting one up can fail: `running.md`, "3. One episode", step 4.
- On a fresh start, the log's fresh-start record holds the participants, roles, models, L, and every seat's share,
  start balance and per-task budget (`running.md`, "3. One episode", step 4, and "6.3 The log: required content",
  A).

## 2. The day loop

Days are numbered from 1, consecutively. While no close has been decided, the next day is played:

1. The start of the day (3).
2. Rounds 1 .. `time.rounds_per_day`. Before each round, its turn order is drawn and the round starts (4); then the
   round is played (`rounds.md`).
3. The end of the day (5): the close rule is checked. If it closes the market, this day was the last: no day is
   played after it, and the episode is settled (6, 7, 8).

Agents are never told that the market has closed: they simply get no more turns. An episode aborted before its close
(`running.md`, "What stops an episode before its close": the cost cap, status `aborted_cost_cap`; too many lost
turns, status `aborted_lost_turns`), or stopped by an error (status `error`), is not settled and has no scores.

## 3. The start of a day

Everything in this section happens before round 1 of day d, in this order. Every notification produced here is
stamped `[day {d}] ` (no round) and appears, in the order produced, in the recipient's notifications at its next turn
(`view.md`, "Notifications since your last turn"). Money moved here moves at once (no round-end deferral).

### 3.1 Day bookkeeping

The current day becomes d; no round is in progress (so the stamps of this section have no round).

### 3.2 Task arrivals

If d > L, nothing in this step happens (no task, no credit, no draw).

Otherwise, for each buyer in seat order:

1. The number of tasks n:
   - day 1: n = `tasks.first_day_tasks` (no draw);
   - days 2 .. L: n is drawn with P(n = k) = (1 - p) x p^k for k = 0, 1, 2, ..., p = `tasks.arrival_p` (keyed by
     day and buyer seat; `randomness.md`, "Task counts (arrivals)").
2. Then n tasks, one after another. For each task:
   - It gets the ID `task-{N}`: N is a market-wide counter that starts at 1 and rises by 1 per task created, in
     creation order (so on a day, buyer seat 0's tasks have lower numbers than buyer seat 1's).
   - Worth: a whole number drawn uniformly from `tasks.worth_range`, both ends included (`randomness.md`, "Task
     worths").
   - D: a whole number drawn uniformly from `tasks.due_days_range`, both ends included (`randomness.md`, "Due-day
     offsets D"). The due day = d + D. The worth and the due day never change, also when the task is posted again
     after its contract was ended.
   - The task is in the buyer's queue, not posted.
   - The buyer's balance rises by its per-task budget (1.3). This credit counts as the buyer's per-task budget in its
     accounts (`view.md`, "Your accounts so far (credits)") and is not part of its score (8). A budget of 0 (a share
     that rounds to 0) is still credited: it is logged as a money movement and makes an accounts entry.
   - Notification to the buyer:

```
[day {d}] New task {task_id} in your queue, worth {worth} to you, due day {due_day} ({rel}). It brought {credits}, added to your balance.
```

   - `{task_id}`: the new task's ID, `task-{N}`.
   - `{d}`, `{worth}` (the task's worth), `{due_day}`: `INT`.
   - `{rel}`: counted from today, n = due day - d: `{n} days left` if n > 1, `1 day left` if n = 1, `today` if
     n = 0 (at arrival n = D >= 1, so at the defaults always `{D} days left`).
   - `{credits}`: the per-task budget in `F2`, then ` credits` (`40.80 credits`, `33.90 credits`, `30.00 credits`),
     as the system prompt states it (`{budget}`).

Managers are not told of arrivals; they see a task only once it is posted (`view-contracts.md`, "Open posts"). A task
posted again after `end_contract` is not a new task: it brings no credit and no notification.

### 3.3 Per contract: result arrivals, then its due-day notice

For each contract, in creation order (ended and settled contracts included), first (a) and then (b):

(a) Result arrivals. Each of its runs still in progress whose arrival day is d or earlier, in the order the runs
started, arrives, and its manager is notified. Arrival is what a result needs before it can be tested, refined,
delivered or submitted; each action's other checks still apply. Runs on a contract its buyer ended, or on an escrow
already released or closed unreleased, also arrive, but their results can be none of these (`actions-manager.md`,
"The contract's state checks"). The notification's text and the run lifecycle: `processes.md`, "Arrival".

(b) The due-day notice: at most one of these, checked in this order. Their exact texts are in `processes.md`, "The
due-day notices". `{dl}` is the contract's deadline day (the deadline it was given at signing; `processes.md`, "The
contract's deadline"), `{due}` the task's due day. `{dl}` is never earlier than `{due}`.

1. The deadline-day notice: if `{dl}` = d, the contract is not ended, not settled (an escrow released or closed
   unreleased), and is not a plain contract with a delivery, to the buyer, then the same to the manager.

2. Else, the task's due-day notice: if `{due}` = d, the contract is not ended, not settled (an escrow released or
   closed unreleased), and is not a plain contract with a delivery, to the buyer only. This happens only when the
   contract's deadline day is later than the task's due day: a contract signed before the due day but fewer than
   `contracts.min_contract_days` days before it, when escrow_close_days is at least 2 (with escrow_close_days 1,
   every deadline is the due day and notice 2 never occurs; `processes.md`, "3.1 The contract's deadline").

A contract signed on its deadline day or later never gets notice 1, and one signed on its task's due day or later
never gets notice 2: both are checked only at the start of a day, and a contract is never signed at the start of a
day.

### 3.4 Arbiter rulings

For each escrow contract, in creation order, with an open dispute whose ruling day (the day after the dispute) is d or
earlier: the arbiter rules. Effects, money and texts: `processes.md`, "Dispute and the arbiter's ruling". The
manager is notified before the buyer.

### 3.5 Automatic releases

For each escrow contract, in creation order, with a submission under review whose submission day +
`contracts.escrow.review_days` is d or earlier: the submission is released as if approved, with no decision. The
result becomes the buyer's on day d. Effects, money and texts: `processes.md`, "Automatic release". The buyer is
notified before the manager.

### 3.6 Escrow lateness closes

For each escrow contract, in creation order, still holding its price, with d >= due day + escrow_close_days
(`config.md`, "6.2 escrow_close_days"; 5 at the defaults) and nothing pending (no submission under review, no open
dispute, and no rejection still disputable: `processes.md`, "6.1 States", REJECTED, DISPUTABLE, which the dispute
window alone decides, whether or not the manager is in business or could pay the deposit): the escrow closes
unreleased and the whole price goes back to the buyer. A pending item delays the close to the start of the first day
with nothing pending (unless something was released by then). Effects and texts: `processes.md`, "6.9 The lateness
close". The buyer is notified before the manager.

All of 3.4 runs before any of 3.5, and all of 3.5 before any of 3.6.

### 3.7 Out of business at the start of a day

The credits of 3.4 and 3.5 (a returned dispute deposit, an escrow's payment) reach the manager at once. If one puts an
out-of-business manager back above 0, it is back in business at once (9.3) and takes turns from round 1 of the same
day. Its notification `Your balance is positive again: you are back in business.` (stamped `[day {d}] `) comes before
the ruling's or the automatic release's notifications that caused it. Nothing at the start of a day charges a
manager, so no manager goes out of business here.

## 4. The start of each round

Before each round r of day d, in this order:

1. The turn order: the participants in seat order (out-of-business managers included), shuffled by a uniformly random
   permutation drawn afresh for (day, round) (`randomness.md`, "The turn order of a round"). Its uses: `rounds.md`,
   "Terms used in this file".
2. The round becomes r (notifications produced from now on are stamped `[day {d}, round {r}] `).
3. Bid openings. The round index of round r of day d is g = (d - 1) x `time.rounds_per_day` + r. For each post, in
   creation order, that is still open, whose bids can be accepted from this round (the post's own round index +
   `contracts.bid_window_rounds` + 1 = g), and that has at least one open bid by a manager not out of business: the
   bid-opening notice to the post's buyer. Its exact text (two forms, by the number of such bids): `processes.md`,
   "Sealed bids and their opening".

Then the round is played (`rounds.md`).

## 5. The end of a day: the close rule

After the last round of day d, the close rule is checked (only while no close has been decided). Nothing else
happens at the end of a day, and nothing is shown to any agent.

### 5.1 Definitions

All are read over every contract ever made on the task, so a task posted again after an ended contract counts its
later contract.

- A task is DELIVERED when some contract on it is an escrow whose price was released, or a plain contract with at least
  one delivery (paid or not).
- A task is DONE when it is delivered, or some contract on it is an escrow that closed unreleased by lateness (3.6; not
  one its buyer ended, which sends the task back to the queue), or the task's post was withdrawn (`withdraw_post`; the
  task has left the market). A task never posted, posted with no contract, back in the queue after an ended contract,
  or under a contract that is not yet delivered, is not done. This is what the system prompts call a done task.
- An UNSEEN UNPAID DELIVERY is a plain contract that has a delivery, is not paid in full (paid in full: paid >=
  price - 1e-6), and whose buyer has had no turn that saw its first delivery. A buyer's turn sees the deliveries made
  before its view was built; so a delivery made in round r is seen at the buyer's turn in a later round (one made in a
  day's last round needs a turn on a later day); a correction that shows it in its news does not count (`rounds.md`,
  "8.5 A correction sees no delivery"). A turn
  lost to a failed model call (`rounds.md`, "Lost to a failed model call"), including one whose first reply was
  unreadable and whose correction call then failed, does not count as having seen anything. A turn with an unreadable
  reply that was not lost that way does count (also when it is then lost as a format error). Only the first delivery
  on the contract matters, and one such turn is enough: a buyer who never pays cannot hold the market open. Buyers are
  never out of business, so they always get turns.

### 5.2 The rule

At the end of day d:

1. If d < L: no close.
2. Else, if every task is done and there is no unseen unpaid delivery: the market closes.
3. Else, if d >= (the latest due day of any task) + `time.close_after_due_days` - 1: the market closes. The latest due
   day counts every task: done, withdrawn, never posted. (From day L on, every task has arrived.)
4. Else: no close; the next day is played.

So the market's last day is at least L and at most max(L, latest due day + `time.close_after_due_days` - 1). The
system prompt describes this as ending "after new tasks have stopped arriving", either when all tasks are done or, "at
the latest, at the start of the day {close_after_text} after the latest due day of any task in the market, so the last
turns are taken the day before". At the defaults, the due-day limit is the latest due day + 4. In the rare case where
L is later than that limit, the market runs to L, as "after new tasks have stopped arriving" says (at the defaults
this needs, among other things, no task arriving on days L - 8 .. L).

### 5.3 The close reason (log only)

The log records why the market closed (`running.md`, "The log: required content"): `all_done` if rule 2 closed it and
every task is delivered; `all_settled` if rule 2 closed it and some task is done without being delivered (withdrawn,
or its escrow closed unreleased); `due_limit` if rule 3 closed it. Agents never see it.

## 6. At the close

Once the market has closed, with the current day = the close day and no further turn, in this order:

1. Every open dispute is ruled on now, whatever its ruling day, by the same arbiter draw and rule as at the start of a
   day (`processes.md`, "Dispute and the arbiter's ruling"), contracts in creation order. If the manager wins: its
   deposit comes back, the escrow releases to it (its pay counted at the disputed submission's day, as always), and
   the buyer owes the arbiter's fee. Then:
   - if the fee is 0 (it rounds to 0 on a tiny price), the result is the buyer's, received on the close day;
   - else, if the buyer's balance covers the fee (balance + 1e-9 >= fee), the fee is charged to the buyer (it leaves
     the market, as when the buyer pays it with `pay_escrow_arbiter`) and the result is the buyer's, received on the
     close day. The balance tested is the buyer's balance at that moment: after this ruling's release has refunded the
     buyer's part of the price (a late submission) and after step 1 for earlier contracts, before any refund of
     steps 2 and 3;
   - else nothing is charged and the result is never the buyer's (it realises no value).
   If the buyer wins: the manager's deposit is lost (it leaves the market) and the price stays held (step 3 refunds it).
2. Every submission still under review is released, as an automatic release (`processes.md`, "Automatic release"):
   the manager's pay is counted at the submission day; the result is the buyer's, received on the close day.
   Contracts in creation order.
3. Every escrow still holding its price (nothing released: a rejection that stands, still disputable or lost by the
   manager; or nothing submitted) refunds the whole price to the buyer. Setup fees are not refunded.

A credit to an out-of-business manager in steps 1 and 2 (a returned dispute deposit, an escrow's payment of more
than 0) brings it back in business by the rule of 9.3, as at the start of a day (3.7): the out-of-business state the
summary records for each manager (`running.md`, "6.6 Scores": `out_of_business`) is the state after these credits.
Step 3 refunds buyers only.

Nothing else happens. Unpaid plain prices stay unpaid: no payment is forced. Runs still in progress never arrive;
their costs stay spent. Open posts and bids lapse. The notifications that steps 1-3 would produce are never read (no
turn follows); they need not be produced. The log must still record every money movement and outcome of steps 1-3
(rulings, releases, refunds, fees charged; `running.md`, "The log: required content").

## 7. Settlement: the value each contract realises

After 6, each contract, in creation order, realises a value for its buyer. Value is in credits and is not rounded.

### 7.1 The value of a result

The value of a result received on a given day: `processes.md`, "4. The value of a result to its buyer" (worth x f(q) x
the lateness factor). Lateness counts from the TASK's due day, not the contract's deadline day (a contract signed
after the due day, or with a later deadline day, still counts lateness from the due day). The receive day of each
contract's result is given below.

### 7.2 A plain contract

- With at least one delivery: the value of the delivery in use, i.e. the delivery of the label the buyer kept
  (`keep_delivery`), else the latest delivery. Its receive day is the day of that delivery (a label is delivered at
  most once on a plain contract, so it has one day).
- With no delivery (including every ended plain contract): no value (counted as 0; it has no entry in the
  settlement's per-contract values).
- What the buyer paid does not change the value.

### 7.3 An escrow contract

- With a released result: the value of that result, with receive day = the day the result became the buyer's: the
  day it approved it, the day of its automatic release, the day the arbiter ruled when the arbiter's fee was 0, the
  day the buyer paid the arbiter's fee, or the close day for a release at the close (6). If the buyer never received
  it (it never paid the arbiter's fee it owed, or could not cover it at the close): 0.
- With submissions but nothing released (rejected, ended, closed unreleased, refunded at the close): 0. A submission
  other than the released one (rejected and not released by the arbiter, or returned) is never the buyer's and is
  worth 0.
- With no submission: no value (no entry, as for a plain contract).

The manager's pay from an escrow counts lateness differently (from the submission day, against the contract's own
deadline; `processes.md`, "The release and the pay cut"). Deliberate: a buyer that approves late or pays the arbiter
late loses value it does not recover from the manager.

## 8. Scores and welfare

Computed once, after 7, from the final balances. No score, gain, welfare, close day or L is ever shown to an agent.

- A buyer's score (its gain):

  ```
  score = final balance + value obtained - start balance - (sum of its per-task budgets credited)
  ```

  value obtained = the sum of the values its contracts realised (7). Equivalently: value obtained + payments
  received + escrow refunds (full, partial on a late release, and at the close) - (token charges + test costs +
  payments made + prices paid into escrow + escrow setup fees + arbiter's fees paid). A buyer's balance may be
  negative (9.4); the formula is the same.
- A manager's score (its gain):

  ```
  score = final balance - start balance
  ```

  Equivalently: payments received + escrow releases + dispute deposits returned - (token charges + run costs + test
  costs + payments made + dispute deposits).
- Welfare = the sum of all participants' scores (computed from the unrounded scores). It equals the sum of all
  realised values minus every cost that left the market (token charges, runs, tests, setup fees, lost deposits,
  arbiter's fees).
- Money is conserved: at every moment, the sum of all balances + every price held in escrow + every dispute deposit
  held + every credit of the current round still waiting to land (`rounds.md`, "Money from others lands at the
  round's end (R2)") = the sum of the start balances + every per-task budget credited - every cost that left the
  market. The waiting credits are 0 outside a round's action phases and corrections (at the start of a day, after a
  round's credits land, and at the close).
- After the close's steps, the implementation must compute the identity's error = (the sum of all balances + escrow
  money still held, prices and deposits) - (the sum of the start balances + every per-task budget credited - every
  cost that left the market), rounded with round(x, 6), and record it as `running.md`, "6.4 The summary", says. It is
  a check on the implementation, never acted on. At the market's magnitudes the recorded error is 0 (it may print as
  `-0.0`); a magnitude above 1e-6 there means money was created or lost. The order in which its terms are accumulated
  is free.
- Arithmetic of the recorded figures (all in doubles, nothing rounded before the final round(x, 2)):
  - a buyer's value obtained = SUM (README, "Number formats and rounding")
    of the realised values of its contracts that have one (7), in contract creation order;
  - its per-task budgets credited = SUM of its per-task budget credits, in the order they were credited;
  - its score = ((final balance + value obtained) - start balance) - budgets credited, left to right;
  - a manager's score = final balance - start balance;
  - welfare = plain left-to-right addition, starting from 0, of the unrounded scores in seat order (not SUM).
  The recorded final balance, value obtained, score and welfare are round(x, 2) of these.
- What the summary records of these: `running.md`, "6.4 The summary", "6.5 Settlement per contract" and "6.6 Scores".

## 9. Out of business

Only managers can be out of business.

### 9.1 When

A manager starts in business whatever its start balance, 0 included (a share can round to 0: `config.md`, "Wealth
shares, start balances and per-task budgets (per episode, per seat)"). Nothing checks its balance at the start: only
a charge listed here, after which its balance is at or below 0, puts it out of business; a balance at or below 0
alone does not. With a free provider (`config.md`, "Models, the `llm` block and token prices"), a manager at 0 whose
token charges are all 0 stays in business and keeps taking turns.

Every charge to an agent is rounded first with round(amount, 6); a charge of 0 or less is ignored. Right after a
charge to a manager that is in business, if its balance is at or below 0 (exactly 0 counts), it is out of business at
once. The charges that can do this are:

- token charges (each model reply's charge, when the reply is taken up; `turns.md`, "Token charges");
- runs (`delegate_run`, `run_myself`, charged when the run starts);
- the manager's tests (`test_result`);
- a dispute deposit (`dispute`): the same check right after the deposit leaves its balance. The deposit is taken
  directly, not as a charge: it is not rounded again and is not ignored when 0, so the check runs after every
  dispute, even one whose deposit rounds to 0 (a manager in business at exactly 0 that disputes with a deposit of 0
  goes out of business).

A payment the manager makes (`pay`) does not trigger the check. `pay` requires amount <= balance + 1e-9
(`actions-manager.md`, "pay"), so a manager can pay its balance down to exactly 0, or to a hair below it, and stays in
business until its next charge of more than 0 (normally its next reply's token charge) or its next dispute deposit
puts it out of business.

The manager is notified at once, stamped with the current day and round (every charge listed here happens during a
round):

```
[day {d}, round {r}] Your balance is at or below 0: you are out of business and cannot act.
```

### 9.2 What it blocks

While out of business:

- It takes no turns: it is left out of each round's turn-takers when the round starts (it stays in the shuffled order
  list). No view is built for it, so its notifications wait and are shown at its first turn after it is back in
  business.
- In the turn in which it went out of business, its later actions do not run. Put out by one of its actions (a run,
  a test, a dispute deposit), the actions after that one do not run. Put out by a reply's token charge, the first of
  that reply's actions to come up in the action phase runs and fails (with the text below, unless its name is
  refused first), and the rest do not run. A reply whose token charge put it out of business runs no program. A
  manager still out of business when the round's corrections come gets none; its memory is still written at its
  turn's end (`turns.md`, "10.3 When, and which one"). Details:
  `rounds.md`, "A participant that goes out of business during a phase" and "Who gets one"; `turns.md`, "Out of
  business by a token charge". Any action it attempts fails with
  `you are out of business (balance at or below 0) and cannot act`, unless the action's name itself is refused first
  (no name, unknown, not available in this market, or the other role's action) (`actions-buyer.md`, "Matching the
  name, and the checks before the action's own").
- Buyers cannot accept its bids: `accept_bid` fails with `{manager_id} is out of business and cannot take contracts`
  (`actions-buyer.md`, "accept_bid"; with a correction note when it went out of business earlier in the same round:
  `rounds.md`, "What each failed action is told"). Its open bids stay open but are hidden from the post's buyer (bid
  lines and counts leave them out, withdraw_post's count of lost bids included: `actions-buyer.md`,
  "4. withdraw_post"; `view-contracts.md`, "The bids field on the buyer's own post") and are not counted
  in bid openings (4).
- Everything else continues: its contracts stay in force; its runs in progress arrive; its escrows can be approved,
  rejected, released automatically, ruled on and closed; buyers can pay it. No other participant is notified that it
  is out of business, and nothing in the views says so (the Participants line lists every participant). A buyer learns
  it only from a failed `accept_bid` (above).

### 9.3 Coming back

After a payment to it, an escrow's payment to it of more than 0, or a returned dispute deposit (a ruling it won), if
its balance is above 0 (strictly), it is back in business at once, with the notification:

```
[{when}] Your balance is positive again: you are back in business.
```

`{when}` = `day {d}` at the start of a day (3.7), `day {d}, round {r}` during a round. Money another participant's
action credits to it during a round lands at the round's end (`rounds.md`, "The round's credits land"), so it is
back from the next round. A credit that leaves its balance at or below 0 does not bring it back.

### 9.4 Buyers

A buyer is never out of business. Its token charges are taken without a balance check, so its balance can fall below
0. It keeps taking every turn; every buyer action that spends money checks the balance first and fails when it does
not cover the amount (`actions-buyer.md`, "Balance checks"). Its score is computed as in 8, negative balance
included. The buyer's system prompt states it in one sentence (`A buyer's balance can fall below 0 because of token
costs.`; `prompts.md`); nothing else does: the view shows the balance as it is, and no notification is sent.

## 10. Texts in this file

| text | to | when |
|---|---|---|
| `New task {task_id} in your queue, worth {worth} to you, due day {due_day} ({rel}). It brought {credits}, added to your balance.` | buyer | 3.2 |
| `Your balance is at or below 0: you are out of business and cannot act.` | manager | 9.1 |
| `Your balance is positive again: you are back in business.` | manager | 9.3, 3.7 |
| `you are out of business (balance at or below 0) and cannot act` | manager (action result) | 9.2 |
| `{manager_id} is out of business and cannot take contracts` | buyer (accept_bid result) | 9.2 |

Each notification is stored and shown as `[{when}] {text}`; the start-of-day ones carry `day {d}` only. The other
notifications of a day's and a round's start (result arrivals, the due-day notices, rulings, automatic releases,
lateness closes, bid openings) are `processes.md`'s ("The notifications of these processes").
