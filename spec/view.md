# The view: frame and general sections

This file specifies the text every participant receives at the start of each of its turns (the **view**), apart from
the market sections: when it is built and what building it changes; its two header lines; the fixed list of sections
for each role, with their exact titles and order; the empty lines between and inside sections; and the general
sections in full: the results of the previous turn, the notifications, the payments, where the messages section goes,
the accounts, the record with each counterparty, the memory and the final line. The market sections (the forum's open
posts, the buyer's tasks, the manager's production, active and finished contracts) are in `view-contracts.md`; the
contents of the messages section are in `messaging.md`; how the results text of a turn is composed is in `turns.md`
("The turn's results text"); the texts of the notifications are in the files the catalogue in section 5 names.

## 1. When a view is built, and what building it changes

- One view per turn. In each round, every participant that takes a turn gets its view built from the market as the
  round starts, before anyone acts (`rounds.md`, "Views: all from the market as the round starts"). A manager out of
  business takes no turn and gets no view.
- The view is the user message of the turn's first model request (`turns.md`, "The conversation's messages").
- Building a view, in this order: the participant's **turn counter** rises by 1 (it counts the views built for it;
  section 10 uses it, and so does `episode.md`, "5.1 Definitions", for whether a buyer has had a turn that saw a
  delivery); its pending notifications are shown and then cleared (section 5); its message cursors move
  and, when a conversation needs a new summary, the summarizer is called (`messaging.md`, "New messages" and "When a
  summary is written"; only when `message_channels` is not `off`).
- A turn lost to a failed model call is undone for the view (`rounds.md`, "Lost to a failed model call"): its
  notifications become pending again, ahead of any received since; its turn counter and message cursors go back; its
  stored results are unchanged. So its next view shows those notifications again and the same results section as
  the lost view.
- The view shows everything that happened before its round started, including the start-of-day steps of the same
  day and the start-of-round steps of the same round (`episode.md`, "The start of a day" and "The start of each
  round"), and nothing done during its round.

## 2. The whole text

### 2.1 Lines and the header

The view is a sequence of lines joined by a single line feed (LF), with no line feed at the end. It begins with
exactly these two lines (no empty line between them):

```
=== DAY {day}, ROUND {round} of {rounds_per_day} today === You are {id} ({role}). Your balance: {balance} credits.
Participants - buyers: {buyer_ids}; managers: {manager_ids}.
```

- `{day}`: the current day, from 1 (`INT`). The day count of the episode is never shown.
- `{round}`: the round of the day, 1 .. `time.rounds_per_day` (`INT`). `{rounds_per_day}`: `time.rounds_per_day`
  (`INT`).
- `{id}`: the participant's ID; `{role}`: `buyer` or `manager`.
- `{balance}`: the participant's balance as the round starts, `F2` (`163.20`). It includes the money from others that
  landed at the end of the previous round, and none of this round's token charges. The balance keeps its sign
  (`-3.50`; a value between -0.005 and 0 prints `-0.00`). A buyer's balance can be negative; a manager in business
  can show `0.00` or `-0.00`, for example after paying its balance down with `pay` (`episode.md`, "9.1 When"). The
  word `credits` is always plural.
- `{buyer_ids}`, `{manager_ids}`: every participant of that role, the viewer included, managers out of business
  included, in **seat order** (seat 0 first; `episode.md`, "Participants, IDs and seat order"), joined by `, `. Not
  sorted: e.g. `buyers: B-44c, B-204; managers: M-829, M-3c5, M-fda`.

### 2.2 The sections and their order

After the header come the sections, each always present, in this fixed order (numbered here without the messages
section; with it, it is section 4 and the later ones move down by one, as the system prompts number them):

| # | buyer's view | manager's view |
|---|---|---|
| 1 | `--- Results of your actions in your previous turn ---` | `--- Results of your actions in your previous turn ---` |
| 2 | `--- Notifications since your last turn ---` | `--- Notifications since your last turn ---` |
| 3 | `--- Payments (yours and to you; the latest {k} {verb} shown) ---` | the same |
| (4) | the messages section, only in the modes below | the messages section, only in the modes below |
| 4 | `--- Your accounts so far (credits) ---` | `--- Your accounts so far (credits) ---` |
| 5 | `--- Your record with each counterparty (all your contracts so far) ---` | the same |
| 6 | `--- Your tasks not yet contracted ---` | `--- Open posts on the forum ---` |
| 7 | `--- Your active contracts ---` | `--- Your production ---` |
| 8 | `--- Your finished contracts ---` or a counted title (`view-contracts.md`, "Order, the listed contracts and the title") | `--- Your active contracts ---` |
| 9 | `--- Other buyers' open posts (for reference) ---` | `--- Your finished contracts ---` or a counted title (`view-contracts.md`, "Order, the listed contracts and the title") |
| 10 | `--- Your self-written memory ---` or a dated title (section 10) | the same |
| 11 | `--- Final instructions ---` | `--- Final instructions ---` |

The messages section, placed right after Payments and before the accounts:

| `message_channels` | buyer's view | manager's view |
|---|---|---|
| `off` | none | none |
| `managers` | none | `--- Messages between managers ---` |
| `buyers` | `--- Messages between buyers ---` | none |
| `within_roles` | `--- Messages between buyers ---` | `--- Messages between managers ---` |
| `all` | `--- Messages ---` | `--- Messages ---` |

"None" means the section does not exist in that view at all (not even its title). So a view has 11 sections, or 12
when its role can send messages. The system prompts state this count and list the sections (`prompts.md`, "What
each message_channels mode changes").

A section with nothing in it is its title line alone. There is no placeholder such as `(none)`. The only exceptions
are in the market sections: the manager's `--- Your production ---` is never empty, and the manager's
`--- Your active contracts ---` with no active contract but with open bids holds one line about those bids
(`view-contracts.md`, "The manager's "Your production"" and "The bids note"). The messages section, where it exists,
is never empty (`messaging.md`, "The Messages section").

### 2.3 Items and empty lines

Each section's content is a sequence of **items**. An item is one entry the market writes: one notification, one
payment line, the payments' overflow line, one accounts line, one record line, one task, post or contract line
together with the indented lines that belong to it (lines that start with spaces, `view-contracts.md`, "Where the
sections sit"), one conversation (`messaging.md`, "The Messages section"), the results text, the memory. Of these,
only the results text and the memory can hold a participant's line breaks (every message a notification or a
contract line quotes is put on one line, `view-contracts.md`, "2.3 Shared texts", ONE_LINE): every line of an item
is printed as it is, and no empty line is inserted inside an item, except inside the results text by its own spacing
rule (section 4.3) and inside a conversation by `messaging.md` ("The Messages section").

Layout, which must be reproduced exactly:

1. After the two header lines: for each section in order, one empty line, then the title line.
2. If the section has content: one empty line after the title, then its items. If it has none: nothing (the next
   section's empty line follows the title directly).
3. Between two consecutive items of a section: exactly one empty line. This applies to every section except
   `Results of your actions in your previous turn` (one item; its own spacing rule, section 4.3), the memory (one
   item) and `Final instructions` (one line).
4. There is no other empty line, apart from those inside an item: the results text's own (section 4.3), the empty
   lines `messaging.md` ("The Messages section") puts between a conversation's lines, and any blank line in text a
   participant wrote that an item holds or quotes (its memory; an argument quoted in a result). The last line
   of the view is the final line of section 11, with no line feed after it.

So every title has exactly one empty line before it and, when its section has content, exactly one after it, unless
an item's own text begins or ends with a line break. An item is printed as it is, so such a line break adds an empty
line to the one this layout puts there. This happens with a memory that begins or ends with a line break (it is
stored as written: `turns.md`, "Writing it"): the memory `abc` followed by a line feed gives `abc`, then two empty
lines, then `--- Final instructions ---`; a memory that begins with a line feed gives two empty lines between the
memory's title and its first text. Text a participant wrote (a memory, a quoted message) never creates a section, a
title or an item of a section, whatever it contains: its lines are part of the item that holds it, and inside the
results text section 4.3's spacing never splits it either.

## 3. Where each section's rules are

| section | rules |
|---|---|
| Results of your actions in your previous turn | section 4 |
| Notifications since your last turn | section 5 |
| Payments | section 6 |
| Messages | section 7 (frame); contents in `messaging.md` |
| Your accounts so far | section 8 |
| Your record with each counterparty | section 9 |
| Your tasks not yet contracted, Other buyers' open posts, Open posts on the forum, Your production, Your active contracts, Your finished contracts | `view-contracts.md` |
| Your self-written memory | section 10 |
| Final instructions | section 11 |

## 4. "Results of your actions in your previous turn"

### 4.1 Content

The section's one item is the participant's **stored results text**: the text stored at the end of its previous turn
(`turns.md`, "The turn's results text"; `rounds.md`, "Each turn ends"), shown with the spacing of 4.3. On the
participant's first turn there is none and the section is its title alone.

What the stored text holds (the heading `Results of your actions:`; the opening notes and the result lines
`- {name}: OK: {message}` and `- {name}: ERROR: {message}`; the format-error lines; the `Correction:` part; the
memory's note; `(no actions run)`; and the text with no heading stored for a turn lost as a format error) is
specified in `turns.md`, "12. The turn's results text".

A correction's results never get a section of their own: they are part of the same stored text, after `Correction:`,
and are first seen in the next view.

### 4.2 After a lost turn, and after being out of business

- After a turn lost to a failed model call, nothing new is stored: the next view shows the same results text as the
  lost view did (the results of the turn before the lost one), under the same title. Deliberate: reproduce exactly.
- After a turn lost as a format error, the next view shows that turn's `FORMAT ERROR: ...` text.
- A manager back in business after a time out of business sees the results text of its last turn before that.

### 4.3 Spacing inside the results text

The results text is a sequence of **entries**, joined with single line feeds (LF): the heading
`Results of your actions:`, then each line `turns.md`, "12. The turn's results text" lists (an opening note, an
action's result line with any note appended, a format-error line, the correction-failed note, `Correction:`,
`(no actions run)`, the memory line), each one entry even when its text holds line feeds (text a participant wrote,
quoted in it). A stored text with no heading (a turn lost to format errors) is one entry. Before each entry except
the first, insert one empty line if the entry's first line (its text up to its first LF) **opens an item** and the
entry before it is neither exactly `Results of your actions:` nor exactly `Correction:`. An entry's first line opens
an item if:

- it starts with `- `, then one or more characters other than `:` and LF, then `: `; or
- it starts with `(note: `; or
- it is exactly `(no actions run)`, exactly `Correction:` or exactly `Results of your actions:`; or
- it starts with `FORMAT ERROR`.

So every action's result line opens an item whatever its shown name (`- ?: ERROR: ...` for an action with no name,
`- Bid: ERROR: ...` for a name sent with a capital, `- bid2: ERROR: ...`), and so does `- FORMAT ERROR: ...`.
`Correction: FORMAT ERROR: ...` opens no item and gets no empty line before it.

A later line of an entry never gets an empty line before it, whatever it reads like. For example, a `test_result`
whose result argument is `missing`, a line feed, `(note: quoted text)`, a line feed, `end` is shown as:

```
Results of your actions:
- test_result: ERROR: no result 'missing
(note: quoted text)
end' in contract-1. Results of contract-1: R-baf (arrived).
```

The same rule spaces the results quoted in a correction message (`turns.md`, "Its body"), whose entries are its
result lines.

Examples (the first two from the reference implementation's runs with a test model, not the offline mock of running.md, whose replies differ; the third from a real run):

```
--- Results of your actions in your previous turn ---

Results of your actions:
- post_task: OK: posted task-2 as post-2; prefers escrow, threshold 7

- deliver: ERROR: 'deliver' is not available to a buyer: it is a manager's action

Correction:
- post_task: ERROR: task-2 is already posted as post-2, which stays open until you accept a bid on it or withdraw it (edit_post changes its stated form and threshold)
```

```
--- Results of your actions in your previous turn ---

Results of your actions:
- send_message: OK: message sent to M-849

- frobnicate: ERROR: unknown action 'frobnicate'. Your actions: send_message, pay, bid, delegate_run, run_myself, test_result, deliver, dispute, withdraw_bid.

Correction:
(no actions run)
```

```
--- Results of your actions in your previous turn ---

Results of your actions:
- FORMAT ERROR: your reply was not a function call: every reply must call compute or act - your reply hit the length limit before its act call was complete; reason more briefly: none of your actions ran

Correction:
- deliver: OK: submitted R-1b2 for B-c53's review on contract-10 (submission #1, by the deadline); if the buyer neither approves nor rejects it, it is released at the start of day 11

- bid: OK: placed bid-32 on post-14 at 17 with threshold 7 (escrow)
```

## 5. "Notifications since your last turn"

### 5.1 Content and order

The section lists every notification the participant received since its previous view was built (for its first
turn: since the episode started), oldest first, each one item, and then clears them. A turn lost to a failed model
call puts them back (section 1). A manager out of business gets no view, so its notifications wait until its first
turn after it is back in business.

Each notification is written

```
[{when}] {text}
```

- `{when}` is fixed when the notification is produced: `day {d}, round {r}` when produced during a round (round-start
  steps included), `day {d}` when produced at the start of a day, before round 1 (`INT` each).
- `{text}`: the notification's text, as the file that defines it gives it (5.2).

Order: the order in which the market produced them. As the market runs, that is: the start of a day in
`episode.md`'s order ("The start of a day"); then each round's start (`episode.md`, "The start of each round": bid
openings); then, within a round: out-of-business notices from the decide chains' token charges (all made before
any action runs: `turns.md`, "What is charged, and when" and "Out of business by a token charge"); the first action
phase, in the order its actions run (`rounds.md`, "The order the phase's actions run in"); out-of-business notices
from the correction calls' token charges (made after the first action phase); the correction phase, likewise
(`rounds.md`, "Reading the corrections and the correction phase"); and the round's end, when money from others lands
(`rounds.md`, "The round's credits land"). So a manager whose bid was lost in the first action phase and whose
correction call's charge then put it out of business sees the bid-lost notice first. A notification produced at a
round's start (a bid opening) appears in that round's view. One produced later in a round, once its views are
built, first appears in the recipient's view of the next round it takes a turn in; after a day's last round that is
the next day's round 1, where it comes before that day's start-of-day notifications (it was produced earlier).

Messages sent with `send_message` are never notifications; they appear only in the messages section.

### 5.2 Every kind of notification

The texts are specified where the event is. Recipients and times:

| notification | to | produced | text and conditions in |
|---|---|---|---|
| a new task in the queue, with its per-task budget | buyer | start of day | `episode.md`, "Task arrivals" |
| a run's result has arrived (with a clause when the contract was ended) | manager | start of day | `processes.md`, "Arrival"; order: `episode.md`, "Per contract: result arrivals, then its due-day notice" |
| `{contract_id} is due today (deadline day {d}).` | buyer, then manager | start of day | `processes.md`, "The due-day notices"; order: `episode.md`, "Per contract: result arrivals, then its due-day notice" |
| the task of a contract is due today, with its value falling | buyer | start of day | `processes.md`, "The due-day notices"; order: `episode.md`, the same section |
| the arbiter's ruling (two texts per side: won, lost) | manager, then buyer | start of day | `processes.md`, "Dispute and the arbiter's ruling"; order: `episode.md`, "Arbiter rulings" |
| a submission released automatically (no decision by the review deadline) | buyer, then manager | start of day | `processes.md`, "Automatic release"; order: `episode.md`, "Automatic releases" |
| an escrow closed unreleased for lateness | buyer, then manager | start of day | `processes.md`, "The lateness close"; order: `episode.md`, "Escrow lateness closes" |
| `Your balance is at or below 0: you are out of business and cannot act.` | manager | when it happens | `episode.md`, "When" (under "Out of business") |
| `Your balance is positive again: you are back in business.` | manager | when it happens (during a round: at the round's end) | `episode.md`, "Coming back" and "Out of business at the start of a day"; `rounds.md`, "The round's credits land" |
| the bids on a post are opened | buyer | round start | `processes.md`, "Sealed bids and their opening"; order: `episode.md`, "The start of each round" |
| a bid placed or updated on the buyer's post | buyer | the bid's action | `actions-manager.md`, "bid" |
| a bid withdrawn | buyer | the action | `actions-manager.md`, "withdraw_bid" |
| a plain delivery | buyer | the action | `actions-manager.md`, "Plain contract: the delivery" |
| an escrow submission for review | buyer | the action | `actions-manager.md`, "Escrow contract: the submission" |
| a rejection disputed | buyer | the action | `actions-manager.md`, "dispute" |
| a payment received: `{payer} paid you {amount}{for}` | payee (either role) | the action | `actions-manager.md`, "pay" |
| the manager's bid accepted | manager | the action | `actions-buyer.md`, "accept_bid" |
| the manager's bid lost to another accepted bid | manager | the action | `actions-buyer.md`, "accept_bid" |
| a post's stated preference changed | each manager with an open bid on it | the action | `actions-buyer.md`, "edit_post" |
| a post withdrawn: the bid on it is lost | each manager with an open bid on it | the action | `actions-buyer.md`, "withdraw_post" |
| a delivery kept | manager | the action | `actions-buyer.md`, "keep_delivery" |
| a submission approved | manager | the action | `actions-buyer.md`, "approve_submission" |
| a submission rejected (with the buyer's message, if any) | manager | the action | `actions-buyer.md`, "reject_submission" |
| a contract ended | manager | the action | `actions-buyer.md`, "end_contract" |

No other notification exists. In particular `pay_escrow_arbiter`, `post_task`, `test_delivery` and `send_message`
notify nobody. `test_result`, `delegate_run` and `run_myself` notify nobody else either, but their charge, like
`dispute`'s deposit, can put the acting manager out of business, which sends it the out-of-business notification
above (`episode.md`, "9.1 When").

## 6. "Payments"

Title (`{k}` = `view.payments_shown`, `INT`; `{verb}` = `is` when k = 1, else `are`):

```
--- Payments (yours and to you; the latest {k} {verb} shown) ---
```

Content: every payment made with the `pay` action of which the participant is the payer or the payee, in the order the
payments were made (oldest first). Nothing else is a payment here: escrow deposits, releases and refunds, setup
fees, dispute deposits and the arbiter's fee are never listed. Only the latest k are listed. When there are n > k,
the first item is

```
({n - k} earlier not listed)
```

(`INT`; always `earlier`, whatever the count: `(1 earlier not listed)`). Then one item per listed payment:

```
[day {d}, round {r}] {src} -> {dst}: {amount} {unit}{for}
```

- `[day {d}, round {r}]`: the day and round in which the payment was made.
- `{src}`, `{dst}`: `you` for the participant itself, otherwise the other participant's ID.
- `{amount}`: the full amount paid (as accepted by `pay`: rounded to the cent), `CR` (`18`, `12.50`).
- `{unit}`: `credit` when the amount is exactly 1, else `credits`.
- `{for}`: ` for {contract_id}` when the payment counts toward a contract (the contract it named, or, unnamed, the
  one `actions-manager.md`, "8. pay", step 5 finds); empty when it is on no contract. A payment that names a contract
  always shows it, also when that contract was already paid in full.
- A payment never carries a message, so nothing follows.

The payment appears from the next round's view of both sides (views are built at round starts), although the
payee's balance is credited only at the end of the round in which it was made (`rounds.md`, "Money from others lands
at the round's end (R2)").

Example (from a real run):

```
--- Payments (yours and to you; the latest 8 are shown) ---

(6 earlier not listed)

[day 12, round 1] you -> M-f2b: 14 credits for contract-17

[day 16, round 1] M-bd6 -> you: 18 credits

[day 19, round 3] M-f2b -> you: 14 credits
```

(shortened; a listed line reads `[day 7, round 1] B-42c -> you: 1 credit for contract-3` for an amount of 1.)

## 7. The messages section

Present only as section 2.2's table says, right after Payments and before `--- Your accounts so far (credits) ---`,
with the title given there. Its items are the conversations the participant reads, in `messaging.md`'s order ("Which
conversations a participant reads, in order"), each one item (its heading line and its indented lines, laid out as
`messaging.md`, "The Messages section", specifies); consecutive conversations are separated by one empty line
(section 2.3). An empty conversation is the single line `{heading}: (no messages yet)`. Everything inside a
conversation (summaries, message lines, `(NEW)`) is `messaging.md`'s. In `off` mode no view has the section.

## 8. "Your accounts so far (credits)"

```
--- Your accounts so far (credits) ---

Spent: {spent}.

Received: {received}.

Held in escrow now: {held}.
```

Three items for a buyer; a manager's view has only the first two (no `Held in escrow now` line ever).

Each of the participant's money movements counts in one category below with its amount (an **entry**), at the moment
of the event that causes it (also when the balance credit itself is deferred to the round's end). `{spent}` and
`{received}` each list their categories as `{name} {sum}` joined by `, `, where `{sum}` is the category's amounts
added up in double precision from 0 by plain left-to-right addition (not `SUM`), in the order their events happened,
then `F2` (the order can matter at a half-cent boundary, as token charges have 6 decimals). A line with no category at all reads `nothing`
(`Spent: nothing.`, `Received: nothing.`).

Spent categories, listed in this fixed order, each only if it has at least one entry:

| name | entries |
|---|---|
| `tokens` | each token charge of a model call (`turns.md`, "Token charges"), as charged; a charge of 0 or less makes no entry |
| `employee runs` | each `delegate_run` charge |
| `your own runs` | each `run_myself` charge |
| `tests` | each reading's charge: a manager's `test_result`, a buyer's `test_delivery` |
| `payments made` | the full amount of each payment the participant made with `pay` |
| `paid into escrow (total)` | the price of each escrow contract, when its bid was accepted (a buyer's); refunds are never subtracted here |
| `escrow fees` | each escrow setup fee (a buyer's), when more than 0 |
| `dispute deposits` | each dispute deposit (a manager's), whatever its amount (also 0) |
| `arbiter's fees paid` | each arbiter's fee the buyer paid (`pay_escrow_arbiter`) |

Every charged entry (`tokens`, `employee runs`, `your own runs`, `tests`, `escrow fees`, `arbiter's fees paid`) is
the charge as made, round(amount, 6), and a charge of 0 or less makes no entry (`episode.md`, "When"). So a category
whose cost is configured as 0 never appears: with `tests.manager_cost` = `tests.buyer_cost` = 0 there is no `tests`
category, not `tests 0.00`.

Received categories, listed in the order in which each category's **first** entry was made (not a fixed order):

| name | entries |
|---|---|
| `per-task budgets` | each per-task budget credited with an arriving task (a buyer's), whatever its amount (also 0, when the buyer's share rounds to 0: config.md, "6.4 Wealth shares, start balances and per-task budgets (per episode, per seat)"), so such a buyer reads `Received: per-task budgets 0.00.` |
| `payments received` | the full amount of each payment received through `pay` |
| `escrow releases` | each release's payout to the manager, when more than 0 |
| `escrow refunds` | a buyer's refund: the unpaid part of a late release, when more than 0; or the whole price when the escrow closes unreleased or the buyer ends the contract |
| `dispute deposits returned` | a manager's deposit returned by a ruling in its favour, whatever its amount (also 0) |

At an arbiter's ruling for the manager, the deposit's return is recorded before the release's payout (so a manager
whose first receipts came that way reads `dispute deposits returned ..., escrow releases ...`).

`{held}` (buyers only): the `SUM` (README, "Number formats and rounding"), over the buyer's escrow contracts in
creation order, of the prices currently held on them (from acceptance until the escrow is released or refunded),
`F2`; `0.00` when none.

At the defaults, every participant's view in round 1 of day 1 reads `Spent: nothing.`; from its first charged model
call on, `tokens` is present (`tokens 0.00` while the sum is below 0.005).

Examples (real runs):

```
Spent: tokens 0.16, tests 37.00, payments made 177.00, paid into escrow (total) 199.00, escrow fees 19.90.

Received: per-task budgets 428.40, payments received 32.00, escrow refunds 17.00.

Held in escrow now: 54.00.
```

```
Spent: tokens 0.18, employee runs 83.29, your own runs 31.05, tests 30.00, payments made 12.00, dispute deposits 6.00.

Received: payments received 169.00, escrow releases 78.00, dispute deposits returned 6.00.
```

## 9. "Your record with each counterparty (all your contracts so far)"

### 9.1 Which lines, in which order

One item per participant with whom the viewer has had at least one contract (either form, any state, ended ones
included), sorted by the counterparty's ID in plain character-code order (digits before letters: `M-218`, `M-bd6`,
`M-f2b`). No line for anyone else; the section is its title alone before the viewer's first contract.

### 9.2 The line

All pieces on one line, in this order:

```
{other}: {P} plain contract{s}, {E} escrow contract{s}; plain: {D} delivered ({L} late); {who} paid {F} in full, {R} in part, {N} not at all ({paid_sum} of {price_sum} for delivered work){readings}{stated}{escrow}{beyond}{ended}
```

Let C be every contract between the viewer and `{other}`, in creation order.

- `{other}`: the counterparty's ID.
- `{P}`: C's plain contracts; `{E}`: C's escrow contracts (`INT`; `contract` when the count is 1, `contracts`
  otherwise, also for 0). The `plain:` part is always present, also with no plain contract.
- Let Dl be C's plain contracts that have at least one delivery; `{D}` = their number. The **delivery in use** of
  such a contract is the delivery its buyer kept with `keep_delivery`, or else its latest delivery (`processes.md`,
  "Deliveries and the delivery in use").
- `{L}`: those of Dl whose delivery in use was made on a day after the contract's deadline day (the contract's own
  deadline, as the views show it: `config.md`, "The full-pay cap and the contract deadline").
- A contract's **paid** is the part of the buyer's payments that counted toward its price (never more than the price;
  `actions-manager.md`, "pay"). `{F}`: those of Dl with paid >= price - 1e-6; `{N}`: those with paid <= 1e-9;
  `{R}` = D - F - N.
- `{who}`: `you` in the buyer's view, `they` in the manager's.
- `{paid_sum}`, `{price_sum}`: the `SUM`s (README, "Number formats and rounding") over Dl, in creation order, of
  paid and of the prices, `F2`.
- `{readings}` (buyer's view only; empty in the manager's):
  - if, among Dl, at least one delivery in use has a result the buyer has read with `test_delivery`: take, for each
    such contract, the mean of the buyer's readings of that result (the exact mean, then the nearest double); then
    m = `SUM`(those means, in creation order) / their number n. The piece is
    `; your readings of their deliveries in use: mean {m} over {n} result{s}` with m `F1`, n `INT`, `result` when
    n = 1;
  - else, if D > 0: `; you have not tested their deliveries in use`;
  - else (D = 0): empty.
- `{stated}` (buyer's view only): if, among Dl, at least one delivery in use came with a stated quality (`deliver`'s
  quality figure): `; quality they stated with those deliveries: mean {m} over {n}`, m = `SUM`(the stated figures,
  in creation order) / n, n their number; m `F1`, n `INT`, no noun after n. Else empty.
- `{escrow}`: only when E > 0:

  ```
  ; escrow: {rel} released ({rel_late} submitted late; {auto} with no decision), {rej} submission{s} rejected, {nd} dispute{s} ruled ({won} for the manager), {ref} closed unreleased and refunded
  ```

  over C's escrow contracts: `{rel}` those whose price was released (approved, released automatically, or by a
  ruling for the manager); `{rel_late}` those of them whose released submission was made on a day after the
  contract's deadline day; `{auto}` the submissions released automatically because the buyer neither approved nor
  rejected them in time; `{rej}` every rejection the buyer made (also those a ruling later overturned);
  `{nd}` the disputes the arbiter has ruled on; `{won}` those of them ruled for the manager; `{ref}` the escrow
  contracts closed with nothing released and their price refunded, not counting contracts the buyer ended. All
  `INT`; `submission`/`dispute` when the count is 1, plural otherwise (also 0).
- `{beyond}`: when the buyer has made payments on C's plain contracts with a part beyond what was owed of the price
  (a payment larger than what was left of the price, or a payment on a contract already paid in full; the part is
  round(amount - the part that counted toward the price, 2), counted when more than 0):

  ```
  ; other payments on your contracts: {who} paid {x} on plain contracts beyond what was owed of their prices ({n} payment{s})
  ```

  `{x}` the `SUM` of those parts, taken over C in creation order and, within a contract, in the order the payments
  were made, `F2`; `{n}` the number of such payments, `payment` when 1. `{who}` as above. Else
  empty. Payments on no contract (e.g. a manager's payment to a buyer) never appear in the record, and nothing in it
  offsets an overpayment the manager paid back. Deliberate: reproduce exactly.
- `{ended}`: if k > 0 of C were ended by the buyer: `; {k} ended by you` (buyer's view) or `; {k} ended by them`
  (manager's view). Else empty.

Examples (real runs):

```
M-bd6: 3 plain contracts, 3 escrow contracts; plain: 3 delivered (0 late); you paid 3 in full, 0 in part, 0 not at all (54.00 of 54.00 for delivered work); your readings of their deliveries in use: mean 7.4 over 3 results; quality they stated with those deliveries: mean 7.6 over 3; escrow: 2 released (0 submitted late; 0 with no decision), 2 submissions rejected, 1 dispute ruled (1 for the manager), 0 closed unreleased and refunded; other payments on your contracts: you paid 18.00 on plain contracts beyond what was owed of their prices (1 payment); 1 ended by you
```

```
B-b79: 12 plain contracts, 3 escrow contracts; plain: 12 delivered (0 late); they paid 10 in full, 0 in part, 2 not at all (121.00 of 145.00 for delivered work); escrow: 3 released (0 submitted late; 0 with no decision), 0 submissions rejected, 0 disputes ruled (0 for the manager), 0 closed unreleased and refunded; other payments on your contracts: they paid 12.00 on plain contracts beyond what was owed of their prices (1 payment)
```

```
M-f8c: 2 plain contracts, 0 escrow contracts; plain: 0 delivered (0 late); you paid 0 in full, 0 in part, 0 not at all (0.00 of 0.00 for delivered work)
```

## 10. "Your self-written memory"

Three forms of the section, chosen at each view:

1. The participant has never written a memory: the title alone.

   ```
   --- Your self-written memory ---
   ```

2. Its memory was last written in its **previous turn** (the turn counter at the write equals this view's turn
   counter minus 1: section 1), and was not written from that turn's correction (`turns.md`, "Writing it": it was
   the decide reply's memory, not the correction's):

   ```
   --- Your self-written memory (last updated day {d}, round {r}, with your last actions, before their results) ---

   {memory}
   ```

3. Otherwise (written in an earlier turn, or written from the correction, which had seen the results):

   ```
   --- Your self-written memory (last updated day {d}, round {r}) ---

   {memory}
   ```

- `{d}`, `{r}`: the day and the round of the turn in which the memory was last written (`INT`).
- `{memory}`: the stored memory exactly as written (at most `turns.memory_chars` characters, `turns.md`, "Writing it"): one item,
  its line breaks and any blank lines kept, nothing inserted, never put on one line.
- A turn in which no memory was written at the turn's end (`turns.md`, "When, and which one"; for example the
  reply carried no memory, or the turn was lost as a format error) leaves the stored memory and its day, round and
  turn unchanged, so the next view uses the plain dated form 3 for it. A turn lost to a failed
  model call does not count as a turn (section 1): after it, form 2 can still apply to the memory of the turn before
  it.

## 11. "Final instructions"

```
--- Final instructions ---

Decide what to do now. compute can do the arithmetic for you. End your turn by calling act.
```

The section's one line is the last line of the view, always the line above, whatever the rest of the view contains.

## 12. Formats used in these sections

| where | format |
|---|---|
| header balance | `F2` (sign kept) |
| days, rounds, counts, `{k}` | `INT` |
| payment amounts | `CR`, then `credit` (exactly 1) or `credits` |
| accounts sums, `Held in escrow now` | `F2` |
| record's paid and price sums, the `beyond` sum | `F2` |
| record's reading and stated means | `F1` |

## 13. Parameters these sections state

- `time.rounds_per_day`: the header's `ROUND {round} of {rounds_per_day} today`.
- `participants.buyers`, `participants.managers`: the participants line lists every seat.
- `view.payments_shown`: the Payments title and how many payments are listed.
- `message_channels`: whether the messages section exists, and its title.
- Through the results text: `turns.max_actions` (the cap note: `turns.md`, "The action cap and the notes that open the
  results"), `turns.memory_chars` (the memory's cut note: `turns.md`, "The memory's lines in the results"), and every
  parameter that an action's result line states, as the file that defines that result gives it (`actions-buyer.md`,
  `actions-manager.md`, `messaging.md`; for example `tests.manager_cost` in test_result's result).

## 14. Complete examples

These are complete views sent by the reference implementation. They are examples: the IDs, numbers and texts come
from the state they describe. 14.2 is also `examples/view_manager_all_M-7f8_day28_round3.txt`, and `examples/` holds
three more (`view_buyer_day1_off_B-c53.txt` and `view_manager_day1_off_M-14b.txt`: first views of day 1, round 1;
`view_buyer_all_B-42c_day23_round2.txt`: a busy buyer).

### 14.1 A buyer, day 1, round 2 (`message_channels` off)

Its first turn posted one task and wrote a memory; nobody has contracted yet.

```
=== DAY 1, ROUND 2 of 3 today === You are B-c53 (buyer). Your balance: 163.20 credits.
Participants - buyers: B-c53, B-d75; managers: M-14b, M-849, M-f8c.

--- Results of your actions in your previous turn ---

Results of your actions:
- post_task: OK: posted task-1 as post-1; prefers escrow, threshold 7

--- Notifications since your last turn ---

--- Payments (yours and to you; the latest 8 are shown) ---

--- Your accounts so far (credits) ---

Spent: tokens 0.00.

Received: per-task budgets 81.60.

Held in escrow now: 0.00.

--- Your record with each counterparty (all your contracts so far) ---

--- Your tasks not yet contracted ---

task-1 | worth 65 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 12.8, 20.1, 28.9, 39.3, 51.4, 65.0) | due day 9 (8 days left) | POSTED as post-1, prefers escrow, threshold 7 (edit_post changes its stated form and threshold) | bids open; you can accept one from day 1, round 3, not before (accept_bid fails earlier) | bids: sealed until day 1, round 3 (you see them then)

task-2 | worth 59 to you (on-time value to you at quality 5, 6, 7, 8, 9, 10: 11.7, 18.2, 26.2, 35.7, 46.6, 59.0) | due day 9 (8 days left) | QUEUED (not posted)

--- Your active contracts ---

--- Your finished contracts ---

--- Other buyers' open posts (for reference) ---

post-3 by B-d75 | task-3 | due day 7 (6 days left) | prefers escrow, threshold 7 | bids open; the buyer can accept one from day 1, round 3 | bids: sealed (only the buyer sees them)

--- Your self-written memory (last updated day 1, round 1, with your last actions, before their results) ---

mock memory

--- Final instructions ---

Decide what to do now. compute can do the arithmetic for you. End your turn by calling act.
```

### 14.2 A manager, day 28, round 3 (`message_channels` all)

A view from a long episode, with payments beyond the latest 8, conversations (one summarized), contracts of both
forms and a record.

```
=== DAY 28, ROUND 3 of 3 today === You are M-7f8 (manager). Your balance: 141.48 credits.
Participants - buyers: B-82c, B-b79; managers: M-0ed, M-ee6, M-7f8.

--- Results of your actions in your previous turn ---

Results of your actions:
- send_message: OK: message sent to B-b79

--- Notifications since your last turn ---

--- Payments (yours and to you; the latest 8 are shown) ---

(7 earlier not listed)

[day 12, round 3] B-b79 -> you: 12 credits for contract-26

[day 13, round 2] B-b79 -> you: 12 credits for contract-26

[day 14, round 1] you -> B-b79: 12 credits

[day 15, round 3] B-b79 -> you: 12 credits for contract-29

[day 15, round 3] B-b79 -> you: 11 credits for contract-32

[day 16, round 2] B-b79 -> you: 12 credits for contract-20

[day 20, round 1] B-82c -> you: 12 credits for contract-41

[day 22, round 3] B-b79 -> you: 12 credits for contract-31

--- Messages ---

Your conversation with M-0ed: (no messages yet)

Your conversation with M-ee6: (no messages yet)

Your conversation with B-82c:
  [day 4, round 2] you -> B-82c: Hi, R-681 (contract-2) and R-099 (contract-1) have been delivered and are in use. Please pay the agreed prices (12.0 each) when you can. Thanks.

  [day 5, round 3] you -> B-82c: Reminder: R-681 (contract-2) and R-099 (contract-1) are in use and unpaid (12 each). Please pay when you can. Thanks.

  [day 8, round 3] you -> B-82c: Reminder from M-7f8: contract-1 (R-099) and contract-2 (R-681) are delivered, in use, and unpaid (12 each, 24 total). Please pay when you can. Thanks.

  [day 9, round 1] you -> B-82c: Reminder from M-7f8: contract-1 (R-099) and contract-2 (R-681) are delivered and in use, 24 unpaid in total. Please pay so we can keep working together.

  [day 22, round 2] B-82c -> you: From B-82c: contract-1 (R-099) and contract-2 (R-681) were both paid in full on day 10 (12 each, see payment log). Please check your side. contract-41 (R-55b) is also paid. Open escrows contract-47/48/49 are noted; I'll test and decide when you submit.

Your conversation with B-b79:
  Summary of the 10 earlier messages (written automatically; you are M-7f8 in it): M-7f8: - Asked B-b79 to pay 24 outstanding: contract-30 (R-4d3, 12) and contract-31 (R-063, 12, delivered day 17 on time, in use). - Will not start new runs for B-b79 until payments catch up. - Said contract-17 (R-54a) is in use, but did not argue it further. - Said contract-20 is also in use. B-b79: - Refused to pay contract-30 (R-4d3): tests averaged 4.8, worth ~8 to B-b79, not 12. - Refused to pay contract-17 (R-54a): tested 0.2. - Open to M-7f8 delivering a better result for task-31 on time. Other facts: - B-b79 paid 12 for contract-20 (R-c5c, readings avg ~7.9) on day 16. - B-b79 said contract-26 was paid in full on day 12. A duplicate 12 sent on day 13 was refunded by M-7f8 on day 14. - M-7f8 reported contract-29 (12) and contract-32 (11) as paid by day 16. - M-7f8 reported contract-21 and contract-27 as paid by day 12. - B-b79 said R-a96 (contract-20) tested ~4.3. M-7f8 then delivered the refinement R-c5c.

  [day 27, round 1] you -> B-b79: M-7f8: Day 27. contract-43 (R-97c, my readings avg 7.0, stated quality 7.0) was submitted day 26 and is under review; it auto-releases day 28 unless you decide. Please approve it if it meets your threshold of 5. Also still unpaid: contract-30 (12) and contract-31 (12).

  [day 28, round 2] you -> B-b79: M-7f8: Reminder: contract-30 (12) and contract-31 (12) are still unpaid. contract-31 was delivered on time and is in use. I will keep new runs for you paused until these are paid. Open posts are all past due, so I am not bidding.

Conversation among all managers: (no messages yet)

Conversation among all participants: (no messages yet)

--- Your accounts so far (credits) ---

Spent: tokens 0.18, employee runs 83.29, your own runs 31.05, tests 30.00, payments made 12.00, dispute deposits 6.00.

Received: payments received 169.00, escrow releases 78.00, dispute deposits returned 6.00.

--- Your record with each counterparty (all your contracts so far) ---

B-82c: 3 plain contracts, 3 escrow contracts; plain: 3 delivered (0 late); they paid 3 in full, 0 in part, 0 not at all (36.00 of 36.00 for delivered work); escrow: 3 released (0 submitted late; 0 with no decision), 1 submission rejected, 1 dispute ruled (1 for the manager), 0 closed unreleased and refunded

B-b79: 12 plain contracts, 3 escrow contracts; plain: 12 delivered (0 late); they paid 10 in full, 0 in part, 2 not at all (121.00 of 145.00 for delivered work); escrow: 3 released (0 submitted late; 0 with no decision), 0 submissions rejected, 0 disputes ruled (0 for the manager), 0 closed unreleased and refunded; other payments on your contracts: they paid 12.00 on plain contracts beyond what was owed of their prices (1 payment)

--- Open posts on the forum ---

(2 more open posts not listed: post-18, post-11)

post-19 by B-b79 | task-19 | due day 10 (18 days ago) | PAST DUE (the task's due day 10): a contract signed today has deadline day 14; a result's value to the buyer falls by 20% of its on-time value for each day after day 10, and it is worth nothing from day 15 | prefers a plain contract | bids: sealed (only the buyer sees them); you have not bid

post-22 by B-b79 | task-22 | due day 12 (16 days ago) | PAST DUE (the task's due day 12): a contract signed today has deadline day 16; a result's value to the buyer falls by 20% of its on-time value for each day after day 12, and it is worth nothing from day 17 | prefers a plain contract | bids: sealed (only the buyer sees them); you have not bid

post-24 by B-b79 | task-24 | due day 12 (16 days ago) | PAST DUE (the task's due day 12): a contract signed today has deadline day 16; a result's value to the buyer falls by 20% of its on-time value for each day after day 12, and it is worth nothing from day 17 | prefers a plain contract | bids: sealed (only the buyer sees them); you have not bid

post-23 by B-b79 | task-23 | due day 13 (15 days ago) | PAST DUE (the task's due day 13): a contract signed today has deadline day 17; a result's value to the buyer falls by 20% of its on-time value for each day after day 13, and it is worth nothing from day 18 | prefers a plain contract | bids: sealed (only the buyer sees them); you have not bid

post-36 by B-b79 | task-36 | due day 22 (6 days ago) | PAST DUE (the task's due day 22): a contract signed today has deadline day 26; a result's value to the buyer falls by 20% of its on-time value for each day after day 22, and it is worth nothing from day 27 | prefers a plain contract | bids: sealed (only the buyer sees them); you have not bid

post-39 by B-b79 | task-39 | due day 23 (5 days ago) | PAST DUE (the task's due day 23): a contract signed today has deadline day 27; a result's value to the buyer falls by 20% of its on-time value for each day after day 23, and it is worth nothing from day 28 | prefers escrow, threshold 5 | bids: sealed (only the buyer sees them); you have not bid

post-40 by B-b79 | task-40 | due day 21 (7 days ago) | PAST DUE (the task's due day 21): a contract signed today has deadline day 25; a result's value to the buyer falls by 20% of its on-time value for each day after day 21, and it is worth nothing from day 26 | prefers escrow, threshold 5 | bids: sealed (only the buyer sees them); you have not bid

post-50 by B-b79 | task-50 | due day 26 (2 days ago) | PAST DUE (the task's due day 26): a contract signed today has deadline day 30; a result's value to the buyer falls by 20% of its on-time value for each day after day 26, and it is worth nothing from day 31 | prefers escrow, threshold 5 | bids: sealed (only the buyer sees them); you have not bid

--- Your production ---

Expected run costs in credits, with how much each typically varies either way: employee fresh 4.00 (±19%), employee refine 2.80 (±16%), you fresh 8.00 (±25%), you refine 14.00 (±25%). test_result: 1 per reading.

You (run_myself): free: 1 run_myself can start now on your contracts. Employee (delegate_run): 0 of 2 slots busy: 2 delegate_run can start now on your contracts.

--- Your active contracts ---

contract-48 with B-82c | task-48 | price 12 (escrow, threshold 6) | deposit 12.00: 0.00 held in escrow, 12.00 released | deadline day 28 | RELEASED R-025 (day 26, the arbiter ruled; submitted on time) | CLOSED: takes no more runs, tests or submissions
    R-025 | you | fresh | started day 21, arrived day 23 | your readings [7.5, 7.3, 6.6, 6.4] mean 6.95 (n=4, ±0.25 standard error) | SUBMITTED day 23: REJECTED day 24, disputed: the arbiter read 6.7 and ruled for the manager: RELEASED; the buyer's message: "B-82c: My 4 independent readings of R-025 average 5.15 (8.6, 3.1, 3.5, 5.4), well below the stated 7.5 and the threshold of 6. Please refine the result and resubmit with an honest stated quality."

--- Your finished contracts (latest 5 of 20) ---

contract-41 with B-82c | task-41 | price 12 (plain) | paid 12.00 of 12.00 | deadline day 23 | in use by B-82c: R-55b (kept; day 19, on time) | 1 result

contract-46 with B-b79 | task-46 | price 12 (escrow, threshold 5) | deposit 12.00: 0.00 held in escrow, 12.00 released | deadline day 27 | RELEASED R-417 (day 24, approved; submitted on time) | 1 result

contract-47 with B-82c | task-47 | price 12 (escrow, threshold 6) | deposit 12.00: 0.00 held in escrow, 12.00 released | deadline day 26 | RELEASED R-721 (day 25, approved; submitted on time) | 2 results

contract-49 with B-82c | task-49 | price 12 (escrow, threshold 6) | deposit 12.00: 0.00 held in escrow, 12.00 released | deadline day 25 | RELEASED R-97a (day 26, approved; submitted on time) | 2 results

contract-43 with B-b79 | task-43 | price 12 (escrow, threshold 5) | deposit 12.00: 0.00 held in escrow, 12.00 released | deadline day 27 | RELEASED R-97c (day 27, approved; submitted on time) | 1 result

Older finished contracts not listed: 15 (1 escrow: 1 released, 0 refunded; of the 14 plain, 12 paid in full; 2 delivered and not paid in full, with 24.00 credits of their prices unpaid; 0 never delivered). Delivered and not paid in full: contract-30 (B-b79, 12.00 unpaid), contract-17 (B-b79, 12.00 unpaid).

--- Your self-written memory (last updated day 28, round 2, with your last actions, before their results) ---

Day28 r2: No bids (all open posts past due; late results worth too little vs ~12 price). Sent B-b79 reminder for c30 (12) and c31 (12) unpaid. Paused new runs for B-b79 until paid. B-82c claims c1/c2 paid day 10 (not in my log; low priority). Balance ~141. Plan: wait for fresh on-time posts; bid only at prices well below expected value; prefer escrow; keep stated qualities honest; test before submitting. Employee and own runs free to start on new contracts.

--- Final instructions ---

Decide what to do now. compute can do the arithmetic for you. End your turn by calling act.
```
