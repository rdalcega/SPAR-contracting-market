# Rounds

This file covers one round, from the moment it is played (after its turn order is drawn and its start-of-round steps
have run: `episode.md`, "The start of each round") to the moment every turn of it has ended: who takes a turn, the
views built from the market as the round starts, the parallel model calls, the action phase (the validity rule, the
fixed order of action kinds, the clash rules, cycles, what a loser is told), money from others landing at the round's
end, the correction and its second action phase, the end of each turn (results kept for the next view, the memory),
lost turns and the abort after too many of them. A turn as a model conversation (messages, tools, tool_choice,
programs, reading act, the action cap, the memory's write and notes, token charges) is in `turns.md`; each action's
checks, effects and texts are in `actions-buyer.md` and `actions-manager.md`; the view's text is in `view.md` and
`view-contracts.md`.

`message_channels` changes nothing in this file except where section 12 says so.

## 1. Terms used in this file

- **Turn order** (the round's seeded order): the permutation of all participants drawn for this (day, round)
  (`randomness.md`, "The turn order of a round"). It includes participants out of business. It never decides who
  acts first in time. It decides only: the order in which views are built, replies are taken up and logged, lost
  turns are handled (so which lost turn triggers the abort, 10.1), acts and corrections are read, reply numbers are
  given (6.1), corrections are decided, turns end and memories are written; and, between actions of different
  participants that are otherwise equal, which runs first (6.5). "Rank" below = a participant's position in the turn
  order (0 first).
- **Taker**: a participant that takes a turn in this round (2).
- **Wave**: the set of model-call chains started together: the round's first calls (4), then, if any, its correction
  calls (8.4).
- **Plan**: the list of actions a participant's act sends for an action phase, in its listed order, after the action
  cap (`turns.md`, "The action cap and the notes that open the results"). A plan may be empty.
- **Kind name** of an action: its name exactly as sent. An action with no name (actions-buyer.md, "1.2 Matching the
  name, and the checks before the action's own", step 1) has an empty kind name. A name that is not one of the
  market's actions (`approve`, `reject`, `pay_arbiter`, `delegate`, a name in capitals or with spaces) is an unknown
  action: kind value 0 (6.4) and no targets (6.2). (The memory is no action: it is written at the turn's end, 9.2.)
- **Result line**: `- {name}: OK: {message}` or `- {name}: ERROR: {message}` (`turns.md`, "Result lines").
- An action **fails** when its result is ERROR. Out of business: `episode.md`, "Out of business".

## 2. Who takes a turn

At the round's start, before any view is built, the takers are decided once: every participant in the turn order
that is not out of business. So every buyer takes a turn in every round, and every manager in business does. There
is no other reason to skip a turn.

"Out of business" is the status that `episode.md`, "When" and "Coming back", set and clear, not a test of the
balance: a manager that paid its balance down to exactly 0 (or a hair below) is still in business, takes its turn,
and is put out of business by that turn's first token charge of more than 0.

- A manager out of business at the round's start takes no turn: no view, no model call, no token charge, no turn
  logged for it, no memory write. Its notifications wait for its next turn.
- A manager that goes out of business during the round keeps its turn up to that point (6.7, 9).
- A manager out of business at the round's start and put back in business during the round (by money landing at the
  round's end, 7) is not a taker of this round; it takes turns from the next round. A taker that goes out of business
  during the round and is put back in business by the round's credits stays a taker of this round (its memory is
  written: 9.2).

## 3. Views: all from the market as the round starts

For each taker, in the turn order, the steps below. The outcome (every view's text, every stored summary, the log's
order, and where a cost-cap stop falls) must be exactly as if the takers were handled one at a time in the turn order,
with nothing else changing the market between them; how this is scheduled is the implementer's choice.

1. Its system prompt is built (`prompts.md`; logged at the participant's first turn).
2. Its view is built (`view.md`, "When a view is built, and what building it changes"). Building it has effects that
   other files define and that happen here, in this order of takers: the participant's turn counter rises by 1; its
   pending notifications are shown and cleared; its message cursors move (`messaging.md`, "New messages"). When
   `message_channels` is not `off`, the summaries its view shows are obtained here, each summarizer call's outcome
   known before the next taker's view (`messaging.md`, "4.3 When a summary is written").
3. The turn's start is logged with the participant's balance rounded with round(x, 2) (before this round's token
   charges).

Then the **round-start state** is kept: the whole market as it stands after every view was built and before any
model call. The ids of all posts and bids that exist at that moment are kept too (8.6).

Consequence: every view of a round shows the market as it stood before anyone acted in that round. Nothing done in
the round (actions, notifications, payments, messages) is in any view of that round; it is in the next round's view,
and part of it may be in a correction (8).

## 4. The first calls: in parallel, taken up in the turn order

1. The cost cap is checked once, before the wave starts (`models.md`, "The cost cap"); if it is reached, the episode
   stops (`running.md`, "The cost cap"). No call inside an admitted wave checks the cap again, so whether a call is made
   never depends on how fast other calls return, and a wave may overshoot the cap.
2. Each taker's whole chain for its turn (its model calls and its programs, ending with act: `turns.md`, "The decide
   chain: calls, offered functions and tool_choice") starts. The chains are independent: no chain reads anything another
   chain produces, and no chain changes the market. They may run concurrently; nothing may depend on which finishes
   first.
3. Inside its own chain, whether a manager is out of business after a reply is decided on its balance as the wave
   starts minus its own charges of the chain so far, and nothing is debited, notified or logged while the chains run
   (`turns.md`, "13.1 What is charged, and when" and "13.2 Out of business by a token charge"); the take-up (item 4)
   reaches the same balances and the same out-of-business reply.
4. When every chain of the wave has finished, the replies are **taken up** on the market participant by participant,
   in the turn order, each participant's calls and programs in their own order: each reply is charged on the market
   (a manager whose balance reaches 0 or below is put out of business here, and only here, with its notification:
   `episode.md`, "When") and logged; each program is logged. Every state change and every log record of the wave
   follows the turn order, never the order in which replies arrived.
5. A chain that ended in a failed model call is a lost turn (10.1), handled right after the take-up, in the turn
   order.

All token charges of the first calls happen before any action of the round runs. So a manager put out of business by
its own tokens is out before any acceptance of its bids is tried (the acceptance then fails, and its note is rule
A10's, with no clash edge needed: 8.2).

## 5. Reading each act

In the turn order, for every taker whose turn was not lost, its act is read (`turns.md`, "Reading the reply that ends
the chain"):

- **Readable**: its plan is the kept actions (at most `turns.max_actions`), and its memory is kept for the turn's end
  (9.2). Any notes that open the results (`turns.md`, "The action cap and the notes that open the results") are the
  first result lines of the turn.
- **Unreadable** and the participant is not out of business: the format error is logged; its plan is empty; the
  turn's first result line is `- FORMAT ERROR: {err}: none of your actions ran` (`{err}`: `turns.md`, "Format
  errors"). It will get a correction (8.1).
- **Unreadable** and the participant is a manager already out of business (by its own tokens this round): the turn is
  lost as a format error (10.2). It has no plan.

## 6. The action phase

The first action phase runs once every taker's act has been read. It runs every plan:
an empty plan takes part with no actions; a participant whose turn was lost has none.

### 6.1 Reply numbers (log only)

Before any action runs, each participant with a plan (an empty one included) gets the next reply number, in the turn
order; all its actions in the phase carry it. The correction phase gives new numbers. The log needs them to tell whether
a reading came back in an earlier reply than an action (`running.md`, "The log: required content"). Nothing shown to
agents depends on them.

### 6.2 Targets: what each action acts on

When the phase starts, each action's targets are resolved on the market as it stands then (for the first phase: the
round-start state plus this round's token charges), each id resolved as the acting participant's own action would
resolve it (the same argument names and id rule: `actions-buyer.md`, "Ids of tasks, posts, bids and contracts";
`actions-manager.md`, "Arguments"). Resolving is a lookup of the id only: a target resolves even
when it is not the actor's contract or post, or the bid is on a post that is not the actor's; no other check of the
action applies (the one exception is withdraw_bid's own-bid rule below). A target that does not resolve is left out
(the action can then take part in no clash that needs it; it usually fails anyway).

An action that is not one of its participant's own actions resolves no target, whatever its arguments: an action of
the other role, an unknown or unnamed action, or `send_message` for a role that cannot send (the name as
`actions-buyer.md`, "Matching the name, and the checks before the action's own" matches it). It has kind value 0
(6.4), takes part in no clash and cannot close a cycle; it keeps its place in its own plan and fails as usual (e.g. a
buyer's `bid` and a manager's `withdraw_post` resolve nothing).

| kind name | targets |
|---|---|
| `deliver`, `end_contract`, `delegate_run`, `run_myself`, `test_result`, `dispute`, `keep_delivery`, `approve_submission`, `reject_submission` | contract: the contract named by `contract_id`. For `end_contract` also: escrow (the contract's form is escrow) and rejected (the buyer's decision on the contract's latest submission was a rejection, whatever happened to that rejection since: disputable, in dispute, standing, or overturned by the arbiter's ruling for the manager; not rejected when nothing was submitted or the latest submission is under review, approved or released without a decision) |
| `accept_bid` | bid (from `bid_id`), its post, and its manager |
| `withdraw_bid` | bid and post, as withdraw_bid picks them (below) |
| `bid`, `withdraw_post` | post (from `post_id`) |
| anything else (also an unknown name) | none |

withdraw_bid's target: the bid withdraw_bid itself finds, and that bid's post (`actions-manager.md`, "3. withdraw_bid",
"Finding the bid"); where that rule gives an error, unresolved.

Targets are resolved once, at the phase's start; nothing that happens later in the phase adds or removes a clash.

### 6.3 The clash rules

For every ordered pair (x, y) of actions of two DIFFERENT participants in the phase (x by participant X, y by
participant Y), the first rule below whose condition holds makes an edge "x must run before y". "Same contract" means
both actions have a resolved contract target and it is the same contract.

| rule | x | y | condition |
|---|---|---|---|
| A1 | `deliver` | `end_contract` | same contract; the contract is plain |
| A3 | `deliver` | `end_contract` | same contract; escrow; its latest submission was rejected |
| A2 | `deliver` | `end_contract` | same contract; escrow; its latest submission was not rejected (or there is none) |
| A4 | `end_contract` | `delegate_run` or `run_myself` | same contract |
| A5 | `end_contract` | `test_result` | same contract |
| A6 | `approve_submission` | `delegate_run` or `run_myself` | same contract |
| A7 | `approve_submission` | `test_result` | same contract |
| B_keep | `keep_delivery` | `deliver` | same contract |
| A8 | `accept_bid` with a resolved post | `bid` | y's post is x's post, and Y is the manager of the bid x accepts (Y's replacing bid) |
| B_accept | `accept_bid` with a resolved post | `bid` | y's post is x's post, and Y is anyone other than the manager of the bid x accepts (another manager's new or replacing bid) |
| A9 | `accept_bid` with a resolved post | `withdraw_bid` | y's bid is resolved and is x's bid |
| B_accept | `accept_bid` with a resolved post | `withdraw_bid` | otherwise, y's post is x's post |
| B_withdraw | `withdraw_post` with a resolved post | `bid` or `withdraw_bid` | y's post is x's post |
| A10 | `delegate_run`, `run_myself`, `test_result` or `dispute` | `accept_bid` | the manager of the bid y accepts is X |

Notes:
- A10 needs no shared contract: every run, test or dispute a manager sent in the phase must run before every
  acceptance of any of its bids in the phase, whatever contract the run, test or dispute names, and even if it is
  going to fail (an unknown contract id included).
- No other action takes part in any clash: `reject_submission`, `pay`, `post_task`, `edit_post`, `test_delivery`,
  `pay_escrow_arbiter`, `send_message`, unknown or unnamed actions, and any action that is not its participant's own
  (6.2).
- Edges join actions of different participants only. A participant's own actions are ordered by its own list (6.5).

### 6.4 The fixed order of kinds

| kind value | kind names |
|---|---|
| 0 | `keep_delivery`, every kind name not listed below (`post_task`, `test_delivery`, `pay`, `pay_escrow_arbiter`, `send_message`, unknown or unnamed actions), and every action that is not its participant's own (6.2), whatever its name |
| 1 | `deliver` (a delivery, or a submission to an escrow) |
| 2 | `approve_submission`, `reject_submission`, `end_contract` |
| 3 | `delegate_run`, `run_myself`, `test_result`, `dispute` |
| 4 | `accept_bid`, `withdraw_post` |
| 5 | `withdraw_bid` |
| 6 | `bid` (new or replacing) |
| 7 | `edit_post` |

### 6.5 The order the phase's actions run in

The nodes are all actions of all plans, each identified by (participant, position in its plan, 0 first). The key of a
node is (its kind value, its participant's rank, its position in the plan); smaller runs first. A node is **own-ready**
when it is first in its plan or the previous action of its plan has already been placed. A clash edge's first action
is a **predecessor** of its second.

Place the nodes one at a time until all are placed:
1. **Free** nodes: unplaced, own-ready, and every predecessor already placed. If there is any, place the free node with
   the smallest key.
2. If none is free (a cycle between the participants' own orders and the clash edges): the **ready** nodes are the
   unplaced own-ready ones (there is always at least one). A ready node's **blocking edges** are the clash edges into
   it from unplaced nodes (each ready node has at least one). The clash rules fall into five groups of strength,
   strongest first, as the prompts' clash rules give way to each other: A1, A2, A3 (rule iii); A4, A5, A6, A7 (rule
   iv); A8, A9 (rule i); B_accept, B_withdraw, B_keep; A10 (rule ii). The rules within one group are equally strong
   (A5 is no weaker than A4, nor A9 than A8). The **candidates** are the ready nodes that lie on a cycle
   nothing outside it holds back: a ready node n is a candidate when every unplaced node from which n can be reached
   (following clash edges and each plan's own order, through unplaced nodes only) can also be reached from n. (A
   ready node merely waiting behind a cycle, such as a withdrawal blocked only by an acceptance that waits for its
   buyer's own earlier action in the cycle, is not one; there is always at least one candidate.) Place the candidate
   whose strongest blocking edge is the weakest; among candidates whose strongest blocking edges fall in the same
   group, the one with the smallest key. Every
   blocking edge into the placed node is **dropped** (not held).

The actions then run in the order placed (6.6). Consequences:
- Every held clash edge puts its winner before its loser, whatever the turn order and whatever else either
  participant listed before it: the winner's earlier actions are pulled ahead with it, and the loser's later actions
  wait. An edge dropped in a cycle (below) gives no such guarantee.
- A participant's actions never run before its own earlier ones. So a high kind early in a list holds back what
  follows it: a manager listing `bid` (6) then `test_result` (3) runs its test only after its bid, after every free
  action of kinds 0-5 of everyone.
- Among the actions free to run at the same moment, the lower kind runs first, then the lower rank, then the earlier
  position. An action held back by its own plan or by a clash edge can therefore run after an action of a higher kind
  with no edge to it: a manager [`bid`, `pay`] and another manager [`deliver`] run deliver, bid, pay, in either
  turn order.

Cycles, as the algorithm resolves them (each in either turn order):
- Manager [`test_result` on C, `deliver` on C], its buyer [`end_contract` C]: edges end -> test (A5), deliver -> end
  (A1), and the manager's own test -> deliver. The ready nodes are the test (blocked by A5) and the end (blocked by
  A1); A5 is the weaker, so the test is placed first and A5 is dropped; then the delivery; then the end, which fails
  (the contract has a delivery). The same with `delegate_run` or `run_myself` (A4) in place of the test. The run or
  test happens and is charged, and the delivery wins: rule iii holds over rule iv.
- Manager [`bid` on P, `test_result`], buyer [`accept_bid` of that manager's bid on P]: edges accept -> bid (A8),
  test -> accept (A10), own bid -> test. The ready nodes are the bid (blocked by A8) and the acceptance (blocked by
  A10); A10 is the weakest rule, so the acceptance is placed first and A10 is dropped; the acceptance runs, then the
  bid (it loses A8), then the test. The same holds with `withdraw_bid` of that bid in place of the bid (A9 in place
  of A8: the acceptance runs, then the withdrawal, which loses A9, then the test), and with `delegate_run`,
  `run_myself` or `dispute` in place of the test.
- Buyer [`end_contract` X, `accept_bid` b], manager of both [`withdraw_bid` b, `deliver` X] (X overdue with nothing
  delivered): edges deliver -> end (A1), accept -> withdraw (A9), and the two own orders. The ready nodes are the end
  (blocked by A1) and the withdrawal (blocked by A9); A9 is the weaker, so the withdrawal is placed first and A9 is
  dropped: the withdrawal succeeds, the delivery succeeds, the end fails (A1's note, 8.2) and the acceptance fails
  (`bid-3 is withdrawn`, with the "it was possible on the market" note, 8.2).
- Buyer [`end_contract` X, `accept_bid` b], b's manager [`bid` on b's post (replacing b), `deliver` X]: likewise A8
  gives way to A1: the replacing bid runs first, then the delivery; the end fails, and the acceptance fails with
  R14's refusal (6.6, `actions-buyer.md`, "5. accept_bid") unless the replacing bid kept b's price and threshold.
- Buyer [`end_contract` C', `approve_submission` C], manager [`test_result` on C, `deliver` C']: edges deliver ->
  end (A1), approve -> test (A7). A7 is the weaker: the test runs first and is charged, then the delivery; the end
  fails; then the approval.
- Buyer [`accept_bid` of manager M's bid on another post, `approve_submission` C], M [`run_myself` C]: edges run ->
  accept (A10), approve -> run (A6). A10 is the weaker: the acceptance, then the approval, then the run, which fails
  uncharged.
- Manager M [`deliver` C, `test_result` C2], C's buyer [`accept_bid` of M's bid, `keep_delivery` C]: edges keep ->
  deliver (B_keep), test -> accept (A10). A10 is the weaker: the acceptance, the keep, then the delivery (its
  notices name the kept label), then the test.
- Manager [`deliver` X, `deliver` Y], their buyer [`end_contract` Y, `keep_delivery` X]: edges keep X -> deliver X
  (B_keep), deliver Y -> end Y (A1). B_keep is the weaker: the delivery of X runs first, then the delivery of Y, the
  end (which fails), and the keep. The end state is the one the keep running first would give; the delivery's
  notices come in the order the actions ran, so they do not name the label kept in that round.

### 6.6 Running one action: the state its turn saw (R1)

**The rule.** An action succeeds only if it is valid both on the state its participant's turn saw, plus the
participant's own earlier actions of the phase, and on the market at the moment it runs.

**The shadow.** For each participant, the state its turn saw is its *shadow*:
- in the first phase: the round-start state (3), except that the participant's own balance and out-of-business status
  are as they stand when the phase starts (after its own token charges of the wave); then the participant's own
  earlier actions of this phase that succeeded on the market, applied in order. Other participants' token charges,
  actions, new ids and money never appear on it;
- in the correction phase: the state after the first phase, minus the hidden ids (8.6), with the participant's own
  balance and status as they stand when that phase starts (after its correction call's token charge); then its own
  earlier successful actions of that phase.

Running anything on a shadow is a trial: it has no effect on the market (nothing logged, no notification sent, no id
or counter of the market advanced, no draw of the market changed).

The steps below state the outcome as a trial on the shadow followed by a run on the market. Any equivalent way of
checking is allowed if it gives the same outcomes: each action is valid on both states or fails; the shadow's check
comes first, and a refused action gets exactly the status and message the full action would give on the shadow (the
same precedence among its checks); the implicit targets are fixed from the shadow; and checking has no effect on the
market.

For each action, in the order of 6.5 (skipping a participant that is stopped, 6.7):
1. **Implicit targets** are resolved on the shadow, before the trial (each only when the action leaves it implicit and
   the shadow resolves it without error; otherwise nothing is fixed):
   - `test_delivery` naming no result (`result` not given, as `actions-buyer.md`,
     "Arguments" defines given: absent, null and `""` are not given): the result it would read on the shadow (its
     default: `actions-buyer.md`, "test_delivery"), if there is one;
   - `approve_submission`, `reject_submission`: the submission under review on the shadow;
   - `pay` naming no contract: the contract an unnamed payment counts toward on the shadow (`actions-manager.md`,
     "pay"), or none;
   - `withdraw_bid`: the bid it picks on the shadow (6.2's rule);
   - `accept_bid` (always implicit): the price and the threshold (none for a plain bid) of the bid it names, as the
     shadow has them (the bid id resolved as 6.2 resolves it).
2. **Trial.** The action runs on the shadow. If it fails there, it fails with exactly that status and message, and the
   market changes nothing (it is logged as an action with that result and counts as a failed action). Its outcome is
   **refused**.
3. **On the market.** If it succeeded on the shadow, it runs on the market with the implicit targets fixed in step 1:
   - `test_delivery` reads the fixed result as if it had been named (then checked on the market as usual);
   - `approve_submission` / `reject_submission` decide the fixed submission; if another one is under review on the
     market, the action fails with its R1 refusal (`actions-buyer.md`, "8. approve_submission", check 5);
   - `pay` with no contract counts toward the fixed contract (or toward none, if the shadow had none), whatever the
     market's state now;
   - `withdraw_bid` takes back the fixed bid (`actions-manager.md`, "3. withdraw_bid", "Finding the bid"), which
     under the market's rules is the bid the market finds when it runs;
   - `accept_bid` fails when the bid's price or threshold on the market differ from the fixed ones (its manager
     replaced it earlier in the phase, which a cycle allows: 6.5), with the refusal of `actions-buyer.md`, "5.
     accept_bid", check 5; a replacing bid on the same price and threshold does not fail it;
   The market's result (success or failure, with its message) is final. Its outcome is **ran**.
4. **After a success on the market**, the action's effects are part of the participant's shadow, and a bid it created
   has, on the shadow, the id the market gave it, so a later action of the same plan that names that bid (or finds it
   by its post) finds the market's bid. **An action that failed on the market** contributes nothing to the shadow:
   later trials of the participant see the start state plus its earlier successful actions only.
5. The result line is recorded for the participant's turn (`turns.md`, "Result lines"): one line per action that ran
   or was refused, in the order the participant listed them.

Consequences:
- An id another participant created in this round does not exist on the shadow, so an action naming it is refused
  with the market's usual unknown-id message, computed on the shadow (e.g. `no post with id 'post-4'. Open posts:
  post-3.`). This covers a post or repost made this round (a manager can bid on a post only from the round after it
  was posted), a bid another manager made this round, and a contract made this round by another participant's
  acceptance (its manager cannot run on, test on or deliver on it in the round's first phase; it can in its
  correction: 8.6).
- Likewise, in the first phase, a dispute of a rejection made this round, a submission after a rejection made this
  round, and an approval, rejection or test of a delivery or submission made this round are all refused: the turn did
  not see them. In the correction phase they are on the shadow if they were made in the first phase (8.6).
- A participant's balance on its shadow is its balance after its own token charges and its own earlier actions; money
  from others is never on any shadow (7).
- A manager put out of business by its own tokens this round fails its first action, on its shadow, and is stopped
  (6.7). That action gets the first error of `actions-buyer.md`, "Matching the name, and the checks before the
  action's own": an unnamed, unavailable, unknown or other-role action gets its own error there, any other action
  `you are out of business (balance at or below 0) and cannot act`.

### 6.7 A participant that goes out of business during a phase

If, after one of its actions, the participant is out of business (one of its own charges took a manager's balance to 0
or below: `episode.md`, "When"), its remaining actions in the phase do not run: no result line, not counted as failed,
not logged as actions (it is **stopped**). They still appear in the phase's logged order. They are counted for the
one note that follows the participant's last result line of the phase (`turns.md`, "9. Result lines").

### 6.8 What the log records for a phase

When the phase has at least one action: the phase (first or correction) and its run order, as a list of
(participant, position in its plan, kind name), every planned action included (stopped ones too). After the phase,
for every clash edge: the phase, the rule, both actions (participant, position, kind name), each action's status (or
none if it did not run), and whether the edge was held (false when dropped in a cycle). The clash records are in the
order of their first action (its participant's rank, then its position), then of their second action (likewise).

## 7. Money from others lands at the round's end (R2)

While an action runs on the market during an action phase (either phase), every credit it makes to a participant
OTHER than the acting one is not added to that balance at once. It is recorded in the round's list of credits
(recipient, amount, kind, payer), in the order made, and lands at the round's end (9.1). Everything else about the
credit happens at once: the ledger entries, the logged transfer or release, the contract's paid amount, the
recipient's notification (so it appears in the recipient's news if it gets a correction, 8.3, and in its next view).
A recipient out of business is not put back in business at once either.

The two such credits in this market:
- **transfer**: a `pay` to anyone else (`actions-manager.md`, "pay");
- **escrow_release**: the share of the price that a buyer's `approve_submission` releases to the manager.

Money moved at once:
- credits to the acting participant itself, e.g. the share of the escrowed price that comes back to the buyer in its
  own approval, or the price that comes back to a buyer that ends an escrow contract;
- every charge (debit), to anyone;
- every credit made outside the action phases: at the start of a day (task budgets, releases, rulings, refunds:
  `episode.md`, "The start of a day"; `processes.md`).

Consequence: a manager paid in the first phase cannot spend that money in the same phase or in its correction
(e.g. with 0.50 credits, paid 10 by its buyer in the same phase, its `test_result` fails with
`insufficient balance for test_result: it costs 1, you have 0.50`); from the next round it can.

## 8. The correction

### 8.1 Who gets one

After the first phase, in the turn order, a participant gets one correction when all of these hold:
- its turn was not lost (10), and it is not out of business now (after the first phase);
- its act was unreadable, or at least one of its actions failed in the first phase (stopped actions do not count).

A turn has at most one correction; a correction never gets another.

### 8.2 What each failed action is told

For a participant that gets a correction, each failed action of its first phase may get a note, computed as follows.

First, the **lost clashes**. For each failed action whose outcome is **ran** (it passed its shadow and failed on the
market):
- if it is an `accept_bid` whose target bid's manager is out of business now (after the first phase), it lost by rule
  A10, with no winning action (whatever made the manager go out of business: its own tokens, or a charge in this
  phase);
- otherwise, among the edges into it that were held and whose first action succeeded and ran before it, the one whose
  first action ran earliest gives the rule and the winner. If there is none, it lost no clash.

Each lost clash gets the clash note for its rule (below). Then, for each other failed action (outcome ran or refused),
in plan order:
- if its outcome is **refused**, an earlier action of the same plan lost a clash, and this action would have succeeded
  had the participant's whole plan run alone, in order, on its first-phase shadow as the phase started (6.6: the
  round-start state with its own balance and status after its token charges; no other participant acting; each
  action's implicit targets resolved as it runs there, none fixed in advance; trials only), the note is
  ```
  (this needed your {tool} above, which failed)
  ```
  `{tool}` = the kind name of the latest earlier action of the plan that lost a clash. (An action that passed its
  shadow, which lacked that failed action, did not need it, and gets the next note.)
- otherwise, if its outcome is **ran**, the note is
  ```
  (it was possible on the market as your turn saw it, but another participant's action earlier in this round changed that)
  ```
- otherwise (refused: it failed on the state the turn saw), no note.

Each note is appended to the end of the action's result line after one space:
`- {name}: ERROR: {message} {note}`. The note stays in the turn's results, so the next view shows it too (9.2).
Notes are added only for a participant that gets a correction.

The clash notes. `{who}` = the participant that made the winning action; `{c}` = the losing action's contract target;
`{tool}` = the losing action's kind name; `{post}` = the losing action's post target.

A1:
```
(this round {who} delivered on {c} before your end_contract ran: a delivery made in the same round as an end counts; the delivery is among the news below)
```
A2 and A3:
```
(this round {who} submitted a result for your review on {c} before your end_contract ran: a submission made in the same round as an end counts; it is among the news below, with the day it is released if you neither approve nor reject it)
```
A4 and A5:
```
(this round {who} ended {c} before your {tool} ran: an end comes before the manager's runs and tests on that contract, so your {tool} did not happen and nothing was charged; {c} is ended and takes no more runs, tests or deliveries)
```
A6 and A7:
```
(this round {who} approved the submission under review on {c} before your {tool} ran: an approval comes before the manager's runs and tests on that contract, so your {tool} did not happen and nothing was charged; {c} is closed, and the price released to you arrives at the end of the round)
```
A8:
```
(this round {who} accepted your bid {bid_id} on {post} before your new bid ran: an acceptance binds a bid as its buyer saw it, even one its manager replaces in the same round, so you hold {terms}, and your new terms did not apply)
```
A9:
```
(this round {who} accepted your bid {bid_id} on {post} before your withdrawal ran: an acceptance binds a bid as its buyer saw it, even one its manager withdraws in the same round, so you hold {terms})
```
- `{bid_id}`: A9: the withdrawal's bid target. A8: the participant's bid on `{post}` whose status is accepted; the
  empty string if there is none (giving two spaces: `your bid  on`).
- `{terms}` = `{contract_id} at {price}{bid_terms}`: `{contract_id}` = the contract made from that bid's post
  (the first created); `{price}` = `CREDITS` of the bid's price (`25 credits`, `24.5 credits`, `1 credit`); `{bid_terms}` = ` with threshold
  {threshold} (escrow)` with the threshold in `G` for an escrow bid, else empty. If the bid or that contract is not
  found, `{terms}` = `the contract`.

A10 (`{manager}` = the target bid's manager):
```
(this round {manager} went out of business before your acceptance ran, so no contract was made and nothing was charged)
```
The correction's fact line about the post (8.3) follows it.

B_accept:
```
(this round {who} accepted another bid on {post} before your {tool} ran)
```
B_withdraw:
```
(this round {who} withdrew {post} before your {tool} ran)
```
B_keep: the "it was possible on the market as your turn saw it ..." note above. (A delivery and a keep on one contract
normally both succeed.)

Examples, each a whole result line as sent:
```
- end_contract: ERROR: contract-1 has a delivery: a plain contract can be ended only after its deadline day with nothing delivered, if it was signed on or before that day (this round M-14b delivered on contract-1 before your end_contract ran: a delivery made in the same round as an end counts; the delivery is among the news below)
- post_task: ERROR: task-1 is already contracted (this needed your end_contract above, which failed)
- delegate_run: ERROR: B-c53 ended contract-9 on day 17: it takes no more runs, tests or deliveries (this round B-c53 ended contract-9 before your delegate_run ran: an end comes before the manager's runs and tests on that contract, so your delegate_run did not happen and nothing was charged; contract-9 is ended and takes no more runs, tests or deliveries)
- test_result: ERROR: the escrow of contract-6 was released: it takes no more runs, tests or submissions (this round B-44c approved the submission under review on contract-6 before your test_result ran: an approval comes before the manager's runs and tests on that contract, so your test_result did not happen and nothing was charged; contract-6 is closed, and the price released to you arrives at the end of the round)
- bid: ERROR: post-3 closed earlier this round, when its buyer accepted a bid (this round B-d75 accepted your bid bid-3 on post-3 before your new bid ran: an acceptance binds a bid as its buyer saw it, even one its manager replaces in the same round, so you hold contract-3 at 25 credits, and your new terms did not apply)
- withdraw_bid: ERROR: bid-3 on post-3 was accepted: it is a contract now (contract-3) and cannot be withdrawn (this round B-d75 accepted your bid bid-3 on post-3 before your withdrawal ran: an acceptance binds a bid as its buyer saw it, even one its manager withdraws in the same round, so you hold contract-3 at 25 credits)
- bid: ERROR: post-8 closed earlier this round, when its buyer accepted a bid (this round B-d75 accepted another bid on post-8 before your bid ran)
- withdraw_bid: ERROR: bid-4 on post-3 is lost: there is nothing to withdraw (this round B-d75 withdrew post-3 before your withdraw_bid ran)
- accept_bid: ERROR: M-14b is out of business and cannot take contracts (this round M-14b went out of business before your acceptance ran, so no contract was made and nothing was charged)
```
(The text before each note is the action's own message: `actions-buyer.md`, `actions-manager.md`.)

### 8.3 The news and the facts

**News**: every notification the participant received after its view was built, up to the end of the first phase, in the
order received, each written as a notification line `[day {d}, round {r}] {text}` (`view.md`, "Notifications since your
last turn"). They stay pending, so the next view's notifications show them again. Messages (`message_channels` not
`off`) are not notifications and are never news (12).

**Facts**, computed right after the first phase, only after these losses:
- When the participant lost an A10 clash in the phase: for each distinct post target of its failed `accept_bid`
  actions (every failed acceptance, not only the A10 one; any outcome), in plan order, one line:
  - the post is open:
    ```
    Open bids on {post} now: {bids}.
    ```
    `{bids}` = the post's bids field exactly as this participant's view would write it now (`view-contracts.md`,
    "4.3 The bids field on the buyer's own post"; for another buyer's post, refused on the shadow, "3.3 The bids field
    on another participant's post": `sealed (only the buyer sees them)`). Every open bid on that post (of any
    manager) is then **shown** to this participant (8.6), in every case.
  - the post closed in this round:
    ```
    {post} closed earlier this round, when {why}.
    ```
  - the post closed earlier:
    ```
    {post} closed on day {d}, round {r}, when {why}.
    ```
    `{why}` = when the reader is the post's buyer, `you withdrew it` if the post was withdrawn, else `a bid on it was
    accepted`; otherwise, `its buyer withdrew it` if the post was withdrawn, else `its buyer accepted a bid`.
- When the participant lost an A4, A5, A6 or A7 clash in the phase, one line:
  ```
  Your balance now: {balance} credits (money others pay you this round arrives at its end). {capacity}
  ```
  `{balance}` = the participant's balance now (after the first phase, before its correction call's charge, without the
  round's credits still to land), `F2`. `{capacity}` = the manager's capacity line exactly as its view's production
  section writes it at this moment (`view-contracts.md`, "5. The manager's "Your production""), e.g. `You
  (run_myself): free: 1 run_myself can start now on your contracts. Employee (delegate_run): 0 of 2 slots busy: 2
  delegate_run can start now on your contracts.`

Post lines come before the balance line.

### 8.4 The correction call

Each correction is one model call. Every due correction call starts together as a second wave, admitted once against
the cost cap exactly as in 4 (checked once before the wave). The calls are independent; their replies are taken up
in the turn order (charged, logged as correction calls). A manager whose correction call's charge puts it out of
business stays a correction-phase participant: its first correction action fails (as out of business, unless an
earlier check fails it first, as in 6.6's consequences) and it is stopped (6.7).

The call's note, its body (the note, then `Results of your reply:` and the result lines, then the news, then the
facts), the spacing of the result lines, how the body joins the turn's conversation, the tools offered (act only) and
the forced tool_choice are in `turns.md`, "The correction" (its sections "Its limit and its note", "Its body", "How
the body joins the conversation" and "The request"). This file gives what goes into the body:
- the result lines: the turn's first-phase result lines (its opening notes, then each action's line with its note,
  8.2), or, for an unreadable act, the single FORMAT ERROR line of 5;
- the news and the facts (8.3), the news lines joined with single line feeds (no spacing), and the fact lines too.

The correction's **limit** = n, the number of the turn's first-phase actions that failed, when the act was readable;
`turns.max_actions` when it was unreadable.

Example (real log, a readable act; an A4 clash note, news and the balance fact):
```
1 of your actions failed (the results of your reply are below; the actions that succeeded have run and stand). You may send up to 1 action now to replace it, in the same format as before, with your memory, which then replaces the one you just sent. This is your only correction this turn, and compute cannot be called in it.

Results of your reply:
- delegate_run: ERROR: B-c53 ended contract-9 on day 17: it takes no more runs, tests or deliveries (this round B-c53 ended contract-9 before your delegate_run ran: an end comes before the manager's runs and tests on that contract, so your delegate_run did not happen and nothing was charged; contract-9 is ended and takes no more runs, tests or deliveries)

What reached you from this round so far (your next view shows it too):
[day 17, round 1] B-c53 approved R-f55 on contract-12: you received 20.00 of the price 20; contract-12 is closed.
[day 17, round 1] B-c53 approved R-d08 on contract-19: you received 20.00 of the price 20; contract-19 is closed.
[day 17, round 1] B-c53 rejected R-39f on contract-15 (submission #1): it is returned to you and cannot be submitted again. Message: "R-39f: my readings average ~5.0 and your stated quality 6.5 is overstated. It is also 4+ days past the task's due day, so its value to me is low. Refine it if you can and resubmit.". You can dispute the rejection through day 18 (a deposit of 7), or submit another result.
[day 17, round 1] B-c53 ended contract-9 (its deadline day 16 passed with nothing released). Its price 22 went back to B-c53; the fees paid on it are not refunded. contract-9 takes no more runs, tests or deliveries; runs still going on it finish and stay charged, but their results cannot be delivered.

Your balance now: 86.08 credits (money others pay you this round arrives at its end). You (run_myself): free: 1 run_myself can start now on your contracts. Employee (delegate_run): 0 of 2 slots busy: 2 delegate_run can start now on your contracts.
```

If the correction call fails at the provider (`turns.md`, "Reading the correction"):
- the act was readable: the first phase's results stand and the turn goes on; the line
  `(note: your correction could not be sent; the results above stand)` is added at the end of the turn's first-phase
  result lines; no correction actions run;
- the act was unreadable: the turn is lost to the provider (10.1).

### 8.5 A correction sees no delivery

A correction is not a view: a delivery its news shows does not count as seen by the buyer (for the close's "unseen
unpaid delivery": `episode.md`, "Definitions"). Only a view built after a plain contract's first delivery counts.

### 8.6 Reading the corrections and the correction phase

Each correction reply is read in the turn order, as `turns.md`, "11.6 Reading the correction" says:
- an unreadable correction (a reply with no act call among them) gives no correction plan: if the first act was unreadable too, the turn is lost as a
  format error (10.2); otherwise its `Correction: FORMAT ERROR: ...` line is added at the end of the turn's
  first-phase result lines and its memory is discarded;
- a readable correction's plan is its kept actions, capped at the correction's limit (any actions, not only
  replacements of the failed ones; it may be empty); its memory, if any, is kept.

If at least one participant has a correction plan (an empty one counts), the **correction phase** runs exactly as section 6 (reply numbers, targets, clash rules, kinds, order, cycles, R1,
stopping), with R2 (7) adding to the same list of the round's credits, except:
- the start state is the market as it stood right after the first phase (before the correction calls' token
  charges); targets are resolved on the market as it stands when the correction phase starts;
- each participant's shadow (6.6) is that start state with these ids removed: the posts created in this round (not
  among the ids kept at the round's start, 3) whose buyer is not this participant; the bids created in this round
  whose manager is not this participant and that were not shown to it in its facts (8.3); and every bid on a removed
  post. Everything else the first phase did (contracts made by acceptances, deliveries, submissions, rejections,
  approvals, ends, withdrawals, edits, payments) is on the shadow. Ids made by others in the correction phase itself
  are unknown on it too;
- no note is computed for a failed correction action (no clash note, no "needed" note, no "possible on the market"
  note), and there is no further correction.

A bid shown open in a buyer's correction is accepted before its manager's withdrawal or replacement of it in the
correction phase only while the A9 or A8 edge is held: a cycle (6.5) drops that edge when a stronger rule (A1-A7)
blocks the other side of the cycle. Actions that are not their participant's own close no cycle (6.2): with buyer
[`bid` on P, `accept_bid` b] and b's manager [`withdraw_bid` b, `withdraw_post` P], the buyer's `bid` and the
manager's `withdraw_post` resolve nothing, so the only clash edge is accept -> withdrawal (A9): the acceptance runs
first and succeeds, and the withdrawal fails with A9's note. The same holds in the first phase.

## 9. The end of the round

In this order:

### 9.1 The round's credits land

For each credit in the round's list, in the order made: the amount is added to the recipient's balance; then, if the
recipient is a manager out of business and its balance is now above 0, it is back in business, notified
`[day {d}, round {r}] Your balance is positive again: you are back in business.` (stamped with this round;
`episode.md`, "Coming back"). It takes turns from the next round. The log records the round's credits (recipient,
amount, kind, payer), if any.

### 9.2 Each turn ends

For each taker whose turn was not lost to the provider, in the turn order:
- **Turn lost as a format error** (10.2): its stored results become that lost turn's text (`turns.md`, "The turn's
  results text"); no memory is written.
- **Otherwise**, its result lines are the first phase's lines (opening notes, each action's line with any note, then
  any line added in 8.4 or 8.6), then the correction block if a correction plan was read (8.6), then the memory's
  note, if any (below); the exact composition and texts: `turns.md`, "12. The turn's results text".
- **The memory**: which memory the turn writes (the correction's when the correction was read and carried one,
  else the first act's memory, none if that act was unreadable; an empty or blank memory counts as none) is decided
  by `turns.md`, "10.3 When, and which one". That memory is written now, once, after both phases and after the
  round's credits landed, never between the first phase and the correction, and whether or not the participant is
  then in business; it is no action of either phase. The write itself and the notes it can add (`(note: your reply had no memory;
  your memory is unchanged)`, the cut note): `turns.md`, "The memory"; how the next view's memory heading marks a
  memory written from a correction: `view.md`, "Your self-written memory".
- Its results text is then stored for its next view (`turns.md`, "The turn's results text"), which places and spaces
  it (`view.md`, "Results of your actions in your previous turn").

The memories are written in the turn order, after every credit has landed.

### 9.3 After the turns end

The round is over. The next round starts (`episode.md`, "The start of each round"), or, after the day's last round,
the end of the day (`episode.md`, "The end of a day: the close rule"). A round is never interrupted by a checkpoint:
an interrupted episode resumes from the end of its last completed day (`running.md`, "Checkpoints and resuming").

## 10. Lost turns

### 10.1 Lost to a failed model call

A turn is lost to the provider when any call of its decide chain (in the round's first wave, 4) fails at the provider
(after the model layer's own retries: `models.md`, "How a call can end"), or when its correction call fails and its first act was unreadable. The chain ends
at the failed call. Then, in the turn order (right after the wave's take-up):
1. The participant is restored as if this round's view had not been built: its pending notifications are those its
   view showed, followed by any received since; its message cursors go back (so the messages that view showed as new
   are new again in its next view); its turn counter goes back; its stored results stay those of its previous turn
   (its next view shows them again). For the close's "unseen unpaid delivery" (`episode.md`, "Definitions"), the lost
   view counts as never built: it saw no delivery, not even those it showed, and the participant's next view that is
   not itself lost sees every delivery made before that view was built, including those made earlier in this round
   (in its first action phase, when the turn is lost at its correction call).
2. The episode's count of turns lost to failed model calls rises by 1 (over the whole episode, all participants). The
   log records the lost turn: participant, role, the reason (a failed model call), the error's text (at least its
   first 500 characters: `running.md`, "6.3 The log: required content") and the ids, in contract creation order, of
   the contracts of which it is the manager (any status) that have no delivery or submission and whose listing
   deadline LD(c) is today (`view-contracts.md`: dl(c) for a plain contract, the task's due day due(c) for an escrow
   contract); for a buyer the list is empty.
3. If that count is now greater than 5 x `time.rounds_per_day`, the episode is aborted at once with status
   `aborted_lost_turns`, not settled (`running.md`, "Too many lost turns"). With the default 3 rounds, the 16th such
   turn aborts it.

The replies the chain received before the failure were charged and logged as usual, and its programs logged. The
participant runs no action this round, gets no correction, writes no memory, and is told nothing about the loss.

### 10.2 Lost as a format error

A turn is lost as a format error (not counted toward the abort) when:
- its first act was unreadable and its correction was unreadable too (an act call that cannot be read, or a reply
  with no act call, 8.6). Its stored results become the two-error text
  (`FORMAT ERROR: {err}; and your correction: {err2}. That turn was lost: ...`);
- or its first act was unreadable and it was a manager already out of business by its own tokens (5). Its stored
  results become the single-error text (`FORMAT ERROR: {err}. That turn was lost: ...`).

Both texts in full: `turns.md`, "The turn's results text". `{err}`, `{err2}`: the reading errors of the first and the
correction reply (`turns.md`, "Format errors"). Nothing of the turn runs and no memory is written. The log records the
lost turn as in 10.1 with the reason a format error, and `{err2}` (or `{err}`) as its error.

### 10.3 Summary

| how | counted toward the abort | shown to the participant next turn |
|---|---|---|
| a call of the decide chain fails at the provider | yes | nothing; its previous results and the notifications again |
| the correction call fails, first act unreadable | yes | the same |
| the correction call fails, first act readable | no (not lost) | `(note: your correction could not be sent; the results above stand)` among its results |
| first act and correction act both unreadable | no | `FORMAT ERROR: {err}; and your correction: {err2}. That turn was lost: ...` |
| unreadable first act of a manager out of business by its own tokens | no | `FORMAT ERROR: {err}. That turn was lost: ...` |

## 11. Order of what happens in a round (summary)

1. Takers decided (2). 2. Views built and turns started, in the turn order (3); the round-start state kept. 3. The
cost cap checked; the first calls run in parallel; replies taken up in the turn order (charges, logs) (4). 4. Turns
lost to the provider handled, in the turn order (10.1). 5. Acts read, in the turn order (5). 6. The first action
phase (6), with credits from others deferred (7). 7. Corrections decided, with their notes, news and facts (8.1-8.3).
8. If any: the cost cap checked; the correction calls run in parallel; replies taken up; turns lost to the provider
handled (8.4); corrections read; the correction phase (8.6). 9. The round's credits
land (9.1). 10. Each turn ends and its memory is written, in the turn order (9.2).

## 12. message_channels

The round, its phases, its rules, the corrections and every text in this file are the same in every mode. What the
modes add:
- `send_message` (in a mode where the sender's role may send: `messaging.md`, "The five modes") is an ordinary action of
  kind 0 that takes part in no clash. It runs under R1 (its checks on the shadow first, then on the market), counts
  toward the action cap, and can fail and earn a correction like any action.
- A message sent in a round is first seen by its recipients in their next round's view (this round's views are
  already built). It is not a notification, so it is never in a correction's news.
- With messages on, the conversation summaries are produced while the views are built (3), with the outcome of one
  view at a time in the turn order (3), before the round-start state is kept; they are not part of the parallel waves.
- A turn lost to the provider moves the message cursors back (10.1).
- `off`: there is no `send_message` action; nothing else differs.
