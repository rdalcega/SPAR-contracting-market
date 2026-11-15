# Processes

This file specifies the market's own mechanics that are not a single action: how runs produce results (the laws of
quality, duration and cost, when a run is charged, capacity, arrival), how readings work, the contract deadline and
which day lateness counts from, the value of a result to its buyer, the plain contract (deliveries, the delivery in
use, what is owed), the escrow as a state machine (deposit and setup fee, submission, review, automatic release,
dispute and the arbiter, the arbiter's fee, the pay cut for lateness, the lateness close), when a contract can be
ended, sealed bids and their opening, and the exact text of every notification these processes send. The actions
that set these processes going (their arguments, checks, refusal texts and results) are in `actions-buyer.md` and
`actions-manager.md`; where a day's processing happens in order is in `episode.md`, "The start of a day"; how a
round runs is in `rounds.md`, and when money another participant's action credits lands in `rounds.md`, "Money from
others lands at the round's end (R2)".

The number formats (`G`, `CR`, `F2`, `Fn`, `INT`, ...), `round(x, n)` and the singular rule are defined in
`README.md`, "Number formats and rounding". Notification stamps: a notification produced at the start of a day is
stored and shown as `[day {d}] {text}`; one produced during round r as `[day {d}, round {r}] {text}` (`view.md`,
"Notifications since your last turn"). In the fenced texts below the stamp is written out.

## 1. Production

### 1.1 Workers, runs and results

- Every manager has two workers: its **employee** and **itself**. The employee is not a participant: it has no model,
  no balance and no turns; it is only the law below. A run by the employee is started with `delegate_run`, a run by
  the manager itself with `run_myself` (`actions-manager.md`, "delegate_run and run_myself").
- A run belongs to one contract of its manager and produces one **result** for that contract. A run is either
  **fresh** or a **refine** of a **parent** result. The parent must be an arrived result of the same contract: either
  worker's, fresh or refined, and whatever happened to it since (delivered, submitted, rejected, released or none of
  these).
- A result has a true quality q, a real number in [0, 10] (the quality scale is fixed). q is never rounded and never
  appears in any text an agent reads; agents learn about it only through readings (2) and the arbiter's reading (6.6).
- A result has a label: `R-` and 3 lowercase hex digits, drawn when the run is accepted (`randomness.md`, "Result
  labels"), unique within its contract only (two contracts may each hold an `R-baf`). A label says nothing about the
  result.
- A result belongs to the contract its run was for. It can be tested, refined, delivered or submitted only on that
  contract, and only once it has arrived (1.4).

### 1.2 The laws of a run

With w = `production.employee` (employee runs) or `production.manager` (the manager's own runs), and z_q, z_t, z_c the
run's three standard normals (`randomness.md`, "A run's quality, days and cost", which also gives the key and when
they are drawn; TN is the truncated normal of `randomness.md`, "Variates and constructions"):

| | fresh | refine of a parent with true quality q_p |
|---|---|---|
| quality q | TN(w.mu, w.sd, z_q) | TN(q_p + w.gain × (10 − q_p) / 10, w.sd_ref, z_q) |
| median days t | w.t_med | w.t_ref_med |

```
days = max(1, ceil(t × exp(production.sig_t × z_t)))          an integer >= 1
m    = exp(production.sig_c × z_c − production.sig_c² / 2)     the cost multiplier, mean 1
cost = round(C, 2)
```

| worker, kind | C | default expected cost (m = 1) |
|---|---|---|
| employee, fresh | `employee.fee + employee.cost_frac × cost_ref_worth × m` | 1 + 0.0375 × 80 = 4.00 |
| employee, refine | `employee.fee + employee.ref_cost_frac × cost_ref_worth × m` | 1 + 0.0225 × 80 = 2.80 |
| manager, fresh | `manager.k × cost_ref_worth × m` | 0.1 × 80 = 8.00 |
| manager, refine | `manager.ref_cost_fixed × m` | 14.00 |

(`cost_ref_worth` is `production.cost_ref_worth`, the same for every task whatever its worth. The employee's fee is
not multiplied by m.) `randomness.md`, "3.8 A run's quality, days and cost" states the same laws with the draws.
The three normals are independent, so a run's quality says nothing about its days or its cost (a fixed rule, not a
parameter). The expected costs (m = 1) are what the manager's prompt and its production line state (`config.md`,
"Expected run costs"; `prompts.md`, "Fields only in the manager's template"; `view-contracts.md`, "The manager's "Your
production""); the drawn cost is stated in the run's result.

### 1.3 Starting a run: charge and capacity

The checks a run action makes and their order (capacity, the draw before the balance check), the acceptance's steps
and every text are in `actions-manager.md`, "4. delegate_run and run_myself". The rules of the process:

- A run can be started only on a contract of the manager that is not ended and, for an escrow, still holds its price
  (6.1: not released, not closed unreleased). A plain contract takes runs for as long as the market runs, also after
  a delivery, after payment and after its deadline.
- **Capacity.** A worker runs at most `production.employee.capacity` (employee) or `production.manager.capacity` (the
  manager itself) runs at once, counted across all of its manager's contracts, runs on contracts since ended, released
  or closed included until they arrive. Runs are not queued.
- The run's cost is charged in full when it starts (a cost of 0.00 charges nothing); a refused run changes nothing.
- The charge is never refunded: not when the contract is later ended or settled, not when the market closes before
  the result arrives.
- Nobody else is notified. A buyer never learns of runs, their costs or their labels, except through results
  delivered or submitted to it.

### 1.4 Arrival

A run started on day s that takes N days arrives at the start of day s + N. At the start of each day d (its place in the
day's processing: `episode.md`, "Per contract: result arrivals, then its due-day notice", step (a)), every result still
in progress whose arrival day is d or earlier arrives, contracts in creation order and, within a contract, results in
the order their runs started. Its manager is notified, also when the contract was ended or its escrow released or
closed, and also when the manager is out of business (the notification then waits for its next turn):

```
[day {d}] {label} ({origin}) for {contract_id} has arrived.{gone}
```

- `{origin}`: `your employee, fresh` | `your employee, refine of {parent}` | `you, fresh` | `you, refine of {parent}`,
  `{parent}` the parent's label.
- `{gone}`: ` {buyer_id} ended {contract_id} on day {ended_day}, so it cannot be delivered.` (leading space;
  `{ended_day}` INT) when the contract's buyer ended it; else empty. Nothing is added for an escrow that was released
  or closed unreleased (its result cannot be submitted either; the contract line says so: `view-contracts.md`,
  "`{closed}`, the closed mark (active lines only)").

Examples: `[day 4] R-5b6 (your employee, refine of R-dbe) for contract-3 has arrived.`,
`[day 4] R-713 (you, fresh) for contract-4 has arrived.`

Before it arrives a result cannot be tested, refined, delivered or submitted. A result that arrives at the start of
a contract's deadline day can be delivered or submitted that day without counting as late (3.2). Runs still in
progress when the market closes never arrive (`episode.md`, "At the close").

## 2. Readings

### 2.1 The law

```
reading = round(min(10, max(0, q + sd × Z)), tests.decimals)
```

q is the result's true quality (unrounded), Z the reading's standard normal (`randomness.md`, "Test readings": every
reading has its own key, so readings are independent), sd = `tests.manager_sd` for a manager's reading (`test_result`)
and `tests.buyer_sd` for a buyer's (`test_delivery`). Clip to [0, 10] first, then round. `randomness.md`, "3.10
Test readings" states the same law with the draw.

### 2.2 Who can read what, and the cost

- A manager (`test_result`) can read any arrived result of one of its contracts, any number of times (results
  delivered, submitted, rejected or never used included), while the contract is not ended and, for an escrow, still
  holds its price. On a plain contract this stays possible until the market closes.
- A buyer (`test_delivery`) can read, on a plain contract, any result delivered to it on that contract (any earlier
  delivery, not only the one in use), at any time; on an escrow contract, the submission under its review, or the
  released result once that result is the buyer's (6.7). Never a rejected submission, whether its rejection is still
  disputable, in dispute or stands (except one the arbiter's ruling for the manager released, testable once it is the
  buyer's, 6.7, although its submission stays recorded as rejected); never a result the manager did not deliver or
  submit to it.
- The checks, their order, the default result when none is named and every text: `actions-manager.md`,
  "test_result"; `actions-buyer.md`, "test_delivery".
- Each reading costs `tests.manager_cost` (manager) or `tests.buyer_cost` (buyer), charged when the reading is taken,
  before it is drawn, like every charge (rounded to 6 decimals; one that rounds to 0 debits nothing, and the reading is
  still taken: `episode.md`, "9.1 When"). The test is refused when the balance + 1e-9 < the cost as configured. A
  manager's test charge can put it out of business (`episode.md`, "Out of business").
- Readings are private: the other side of the contract is never told of a test or its value, and no notification is
  sent.

### 2.3 The readings text

The text that sums up one side's readings of one result. This section defines it; it is one text, built by the same
rule wherever it appears: `test_result`'s and `test_delivery`'s results (after `Your `) and the views (after `your `;
`view-contracts.md`, "2.3 Shared texts", `READINGS`):

```
readings: none
readings [{more}{values}] mean {mean} (n={n}, ±{se} standard error)
```

- The first form when that side has no reading of the result.
- `{values}`: the latest `view.readings_shown` readings, in the order taken, each `F{tests.decimals}` (`F1` at the
  default), joined by `, `.
- `{more}`: `..., ` when that side has more than `view.readings_shown` readings; else empty.
- `{mean}`: `F2` of SUM(all n readings, in the order taken) / n, one double-precision division; all n readings, not
  only those listed. `SUM` is defined in `README.md`, "Number formats and rounding"; plain left-to-right addition can
  change the second decimal: readings 1.0, 2.6, 6.5, 1.2 give `2.83` (plain addition, 11.299999999999999, would give
  `2.82`).
  (The buyer's record line averages per-result means by its own rule: `view.md`, "9.2 The line".)
- `{n}`: the number of readings, `INT`.
- `{se}`: sd / sqrt(n), `F2`, with sd the configured standard deviation of that side (`tests.manager_sd` or
  `tests.buyer_sd`); it is not computed from the readings. `±` is U+00B1.

Examples (defaults): `readings [6.0] mean 6.00 (n=1, ±0.50 standard error)` (manager);
`readings [..., 1.9, 2.3, 1.8, 2.6, 2.4] mean 2.22 (n=6, ±0.20 standard error)` (manager);
`readings [5.2] mean 5.20 (n=1, ±2.00 standard error)` (buyer).

### 2.4 Where readings are shown

- In the test action's result, which the agent reads at the start of its next turn, or in its correction call in the
  same round if it gets one (`turns.md`, "The turn's results text"; `rounds.md`, "The correction"). So a reading cannot
  be used by a later action of the same act call.
- In the reader's own view: a manager's readings on its result lines (`view-contracts.md`, "The result line"), a
  buyer's on its delivery and submission lines (`view-contracts.md`, "Delivery lines (plain)", "Submission lines
  (escrow)").
- The arbiter's reading (6.6) is shown only in the ruling's notifications and in the submission's outcome in the
  views.

## 3. Contract deadlines and lateness

### 3.1 The contract's deadline

Every contract, plain or escrow, gets its deadline day once, when it is signed (the bid is accepted):

```
deadline = min(max(due, signing_day + contracts.min_contract_days), due + escrow_close_days − 1)
escrow_close_days = ceil(1 / value.late_decay − 1e-9)          (5 at the default)
```

- `due` is the task's due day (its arrival day + D, fixed when the task arrives; every contract ever made on the task,
  including those after an ended contract, has the same `due`).
- `due + escrow_close_days − 1` is the **full-pay cap**: the latest day any contract's deadline can be, and so the
  latest day any submission can be paid in full. A submission is paid in full only up to its own contract's deadline
  (6.8).
- So the deadline is never before the due day; a contract signed early enough has the due day as its deadline; one
  signed later gets `contracts.min_contract_days` days from signing, but never more than the cap; a contract signed
  after the cap day starts past its deadline.
- Examples (defaults, due day 9): signed day 1 or 5 → 9; day 6 → 10; day 8 → 12; day 9, 11 or 20 → 13.
- This is the "deadline day" agents see on contract lines (`view-contracts.md`, "`{deadline}` on an active line");
  posts and queued tasks show the deadline a contract signed today would get, by the same formula with signing day =
  today (`view-contracts.md`, "The lateness clause on posts and uncontracted tasks").

### 3.2 Which day lateness counts from

| what | counted from | on which day |
|---|---|---|
| the buyer's value of a result (4) | the task's due day | the day the buyer received the result |
| the escrow's pay to the manager (6.8) | the contract's deadline day (equal to the due day when the deadline is the due day) | the submission day |
| the escrow's lateness close (6.9) | the task's due day | closes from due + escrow_close_days |
| what agents are told is late: a delivery's or submission's result ("N days after the deadline"), "submitted N days late", contract and record lines ("N days late", late counts) | the contract's deadline day | the delivery or submission day |
| past-due texts on tasks, posts and undelivered contracts; the task's due-day notice (3.3) | the task's due day | (`view-contracts.md`, "The lateness clause on posts and uncontracted tasks"; "The buyer's "Your active contracts"") |

Days late = max(0, day − the day it counts from). A delivery or submission on the deadline day is on time.

### 3.3 The due-day notices

At the start of each day, right after a contract's arrivals (1.4), at most one of these, for each contract in creation
order (`episode.md`, "Per contract: result arrivals, then its due-day notice", step (b)):

1. If the contract's deadline day is today, the contract is not ended, not settled (an escrow released or closed
   unreleased), and is not a plain contract with a delivery: to the buyer, then the same to the manager:

   ```
   [day {d}] {contract_id} is due today (deadline day {d}).
   ```

2. Else, if the task's due day is today, the contract is not ended, not settled, and is not a plain contract with a
   delivery: to the buyer only:

   ```
   [day {d}] The task of {contract_id} is due today (day {d}): its value to you falls for each day after it. The contract's deadline day is {deadline}.
   ```

`{d}`, `{deadline}`: `INT`.

## 4. The value of a result to its buyer

```
f(q)  = clip((q − value.q0) / (10 − value.q0), 0, 1) ^ value.exponent
value = worth × f(q) × max(0, 1 − value.late_decay × days_late)
days_late = max(0, receive_day − due)
```

- `worth` is the task's worth, `q` the result's true quality, `due` the task's due day (3.2).
- The receive day: on a plain contract, the delivery day of the delivery in use (5.1); on an escrow contract, the day
  the released result became the buyer's (6.4, 6.5, 6.6, 6.7). A result that never became the buyer's (a rejection
  that stands, an arbiter's fee never paid, an escrow that released nothing) is worth 0 to it.
- At the defaults f(q) = clip((q − 1)/9, 0, 1)² (clip first, then square: a quality at or below 1 is worth 0), and a
  result received escrow_close_days (5) or more days after the due day is worth 0.
- Settlement applies this once, at the episode's end: `episode.md`, "Settlement: the value each contract realises".
  Agents are never told any q or any realised value; the buyer's view states values at given qualities
  (`view-contracts.md`, "The task lines").

## 5. The plain contract

### 5.1 Deliveries and the delivery in use

- The manager delivers an arrived result of the contract with `deliver` (`actions-manager.md`, "Plain contract: the
  delivery"). A label can be delivered at most once on a plain contract, so each delivered label has one delivery day.
- Every delivery on a contract (and every submission on an escrow contract) takes the contract's next number:
  `delivery #1`, `#2`, ... (`submission #n` on an escrow).
- A delivery moves no money and changes nothing on the buyer's side but what it holds.
- **The delivery in use** is the label the buyer kept with `keep_delivery` (`actions-buyer.md`, "keep_delivery"), else
  the latest delivery. A kept delivery stays in use when later deliveries come, until the buyer keeps another. The buyer
  may keep any delivered label other than the one it already kept, at any time until the market closes (also after
  paying, and switching as often as it likes); keeping the label already kept is refused (`actions-buyer.md`,
  "keep_delivery"). Keeping the latest delivery locks it in.
- The buyer's value from the contract is the value of the delivery in use, received on its delivery day (4). A plain
  contract with no delivery yields no value.

### 5.2 What is owed

- Payment is never automatic and never enforced. A buyer pays with `pay` (`actions-manager.md`, "pay"), before or after
  delivery; a payment before delivery is an advance.
- `paid`: the credits of the buyer's payments counted toward the price. A payment counts round(min(amount, owed), 2)
  toward the price; `paid` accumulates as round(paid + that, 4), so it never exceeds the price. The rest of a payment,
  round(amount − the part counted, 2), is recorded on the contract beside the price (a bonus on top of it) when it is
  above 0 (`actions-manager.md`, "8. pay"); a rest that rounds to 0 is not recorded.
- `owed` = 0 on a contract its buyer ended, else max(0, round(price − paid, 2)).
- Paid in full: paid ≥ price − 1e-6 (used by the close rule, `episode.md`, "The end of a day: the close rule", and
  the views).
- Nothing paid on a plain contract is ever returned, also when the buyer ends it (an advance stays with the manager).
  At the market's close unpaid prices stay unpaid.

### 5.3 What stays possible

A plain contract never closes except by being ended (7). Runs, tests and deliveries on it stay possible after a
delivery, after payment and after its deadline, until the market closes. Once ended it takes no runs, tests or
deliveries; runs in progress on it still arrive (1.4).

## 6. The escrow contract

An escrow contract is made by accepting an escrow bid (a bid with a quality threshold, a whole number 0 to 10). The
buyer deposits the price into an escrow held by the market; the manager submits results for the buyer's review; the
price is released to the manager when a submission is approved, is not decided in time, or is upheld by the arbiter.

### 6.1 States

The names in capitals name this spec's states; they are not texts. Some of the same words appear in what the views
print, but every view text is defined in `view-contracts.md` alone, not derived from these names.

| state | meaning | ends when |
|---|---|---|
| HELD, NOTHING SUBMITTED | the price is held; no submission yet | a submission (6.3); the buyer ends it (7); the lateness close (6.9) |
| HELD, UNDER REVIEW | one submission awaits the buyer's decision | approval (6.4) or automatic release (6.5) → RELEASED; rejection (6.4) → REJECTED, DISPUTABLE |
| HELD, REJECTED, DISPUTABLE | the latest submission was rejected on day r, it is not disputed, and today ≤ r + `contracts.escrow.dispute_window_days` | a dispute (6.6) → IN DISPUTE; a new submission (6.3) → UNDER REVIEW; the window passes → REJECTION STANDS |
| HELD, IN DISPUTE | the manager disputed the latest rejection; the arbiter rules at the start of the next day | the ruling (6.6): for the manager → RELEASED; for the buyer → REJECTION STANDS |
| HELD, REJECTION STANDS | the latest submission was rejected and can no longer be disputed (its window passed undisputed, or the arbiter ruled for the buyer) | a new submission → UNDER REVIEW; the buyer ends it (7); the lateness close (6.9) |
| RELEASED | the price went to the manager (less any pay cut, refunded to the buyer) on one result | final |
| CLOSED UNRELEASED | the lateness close (or the market's close) gave the whole price back to the buyer | final |
| ENDED | the buyer ended it; the whole price came back to the buyer | final |

At most one submission is under review at a time, and at most one dispute is open. Only the latest submission's
rejection can be disputed. What each state allows:

| state | runs and the manager's tests | a new submission | approve / reject | dispute | end_contract | the buyer's tests |
|---|---|---|---|---|---|---|
| NOTHING SUBMITTED | yes | yes, before the close day | no | no | after the deadline, if the deadline is before the full-pay cap (7) | nothing to test |
| UNDER REVIEW | yes | no | yes | no | no | the submission under review |
| REJECTED, DISPUTABLE | yes | yes, before the close day | no | yes | no | nothing to test |
| IN DISPUTE | yes | no | no | no (already open) | no | nothing to test |
| REJECTION STANDS | yes | yes, before the close day | no | no | yes, any day | nothing to test |
| RELEASED | no | no | no | no | no | the released result, once it is the buyer's (6.7) |
| CLOSED UNRELEASED | no | no | no | no | no | nothing to test |
| ENDED | no | no | no | no | no (already ended) | nothing to test |

"The close day" is due + escrow_close_days (6.9). Runs in progress when an escrow is released, closed or ended still
arrive (1.4); their results cannot be submitted.

### 6.2 Acceptance: the deposit and the setup fee

When the buyer accepts an escrow bid (`actions-buyer.md`, "accept_bid"; its balance check and texts are there):

- setup fee = round(`contracts.escrow.fee_rate` × price, 2);
- the acceptance needs the buyer's balance + 1e-9 ≥ round(price + setup fee, 6), checked before anything changes;
- the price leaves the buyer's balance and is held in the escrow (money held by the market, not a cost); then the
  setup fee is charged to the buyer if it is above 0 (it leaves the market; never refunded, whatever happens later);
- the manager pays nothing at acceptance, and nothing for any submission.

### 6.3 Submissions

The manager submits an arrived result of the contract with `deliver` (`actions-manager.md`, "deliver"; its checks,
their order and texts are there). A submission is possible only when all of these hold:

- the contract is not ended and the escrow still holds the price;
- no submission is under review (one at a time);
- no dispute is open;
- today is before the close day, due + escrow_close_days (from then a release would pay nothing);
- the label was never submitted on this contract (a result once rejected cannot be submitted again; a refinement of
  it is a new result).

A submission made on day s goes under the buyer's review; it is released automatically at the start of day
s + `contracts.escrow.review_days` if the buyer neither approves nor rejects it before (6.5). A submission made while
the latest rejection is still disputable ends the chance to dispute that rejection. The submission's notification to
the buyer and the manager's result: `actions-manager.md`, "Escrow contract: the submission".

### 6.4 The review: approval and rejection

The buyer decides on the submission under review with `approve_submission` or `reject_submission`
(`actions-buyer.md`, "approve_submission" and "reject_submission": checks, results, and the notification each sends
to the manager).

- **Approval** on day d: the escrow releases on the submission's result (6.8), with the pay cut counted at the
  submission day; the result is the buyer's from day d (its receive day for the value).
- **Rejection** on day d, with an optional message (cut to its first `contracts.message_chars` characters; shown to the
  manager in the rejection's notification and on both sides' contract lines): the result goes back to the manager
  and is not the buyer's (unless a dispute the manager wins releases it, 6.6, 6.7); the price stays held; the manager
  can dispute the rejection through day d + `contracts.escrow.dispute_window_days` (6.6), or submit another result.

### 6.5 Automatic release

At the start of day s + `contracts.escrow.review_days`, for a submission made on day s that is still under review (its
place in the day: `episode.md`, "Automatic releases"): the escrow releases on it as if approved (6.8), with the pay
cut counted at the submission day; the result is the buyer's from that day. Notifications, to the
buyer first, then to the manager:

```
[day {d}] {label} on {contract_id} was released: you neither approved nor rejected it by the start of day {auto_day}. It is yours; {to_manager} of the price {price} went to {manager_id}{back}.
[day {d}] {label} on {contract_id} was released: {buyer_id} neither approved nor rejected it by the start of day {auto_day}. You received {paid_text}; {contract_id} is closed.
```

- `{auto_day}` = s + `contracts.escrow.review_days` (`INT`; equal to `{d}` except at the market's close, whose
  notifications need not be produced: `episode.md`, "At the close").
- `{to_manager}`: `F2`. `{price}`: `G`. `{paid_text}`: 6.8.
- `{back}` = `, and {refunded} came back to you` (`{refunded}` in `F2`) when the release refunded the buyer more than
  1e-9 (6.8), else empty; e.g. `... It is yours; 16.00 of the price 20 went to M-14b, and 4.00 came back to you.`

### 6.6 Dispute and the arbiter's ruling

- **The dispute.** The manager disputes the latest rejection with `dispute` (`actions-manager.md`, "dispute": checks,
  texts, and the notification to the buyer), while the contract is not ended, the escrow holds the price, no dispute is
  open, the latest submission is a rejection that was not disputed before, and today ≤ the rejection day +
  `contracts.escrow.dispute_window_days`.
- **The dispute deposit** = round(`contracts.escrow.dispute_fee_rate` × price, 2). It needs the manager's balance +
  1e-9 ≥ the deposit; it leaves the manager's balance at once and is held by the market until the ruling. If it leaves
  the manager at or below 0, the manager is out of business at once (`episode.md`, "Out of business").
- **The ruling day** is the dispute day + 1 (fixed; it does not depend on the dispute window). No submission is
  possible while the dispute is open.
- **The ruling**, at the start of the ruling day (its place in the day: `episode.md`, "Arbiter rulings"): the arbiter
  takes one reading of the disputed result,

  ```
  reading = round(min(10, max(0, q + contracts.escrow.arbiter_sd × Z)), tests.decimals)
  ```

  (q the true quality, Z from `randomness.md`, "The arbiter's reading", which states the same law). **The manager wins if the reading is at or
  above the contract's threshold** (a tie goes to the manager). The deposit leaves the held money either way.
  - **The manager wins:** the deposit is returned to the manager at once (the deposit, or else the release's credit
    that follows it, can put it back in business; that notification comes before the ruling's: `episode.md`, "Out of
    business at the start of a day"); the escrow releases
    on the disputed result (6.8), with the pay cut counted at the disputed submission's day; the buyer owes the
    arbiter's fee, equal to the deposit, and the result becomes the buyer's only once it pays it (6.7). If the fee is 0
    (`contracts.escrow.dispute_fee_rate` is above 0, but the fee rounds to 0 on a small enough price), the result is the
    buyer's at once (received on the ruling day) and nothing is owed; the texts below are the same as for any other fee.
  - **The buyer wins:** the deposit is lost (it leaves the market). The rejection stands and can no longer be
    disputed; the price stays held; the manager may submit another result if the close day has not come (6.3).
- Notifications, to the manager first, then to the buyer, with
  `{head}` = `The arbiter's reading of {label} on {contract_id} is {reading}, against the threshold {threshold}:`

  The manager won:

  ```
  [day {d}] {head} you won the dispute. Your deposit of {fee} came back, and you received {paid_text}; {contract_id} is closed.
  [day {d}] {head} the dispute went against you. The price was released to {manager_id}{back}. {label} is yours once you pay the arbiter's fee of {fee} (pay_escrow_arbiter).
  ```

  The buyer won:

  ```
  [day {d}] {head} you lost the dispute and your deposit of {fee}. The rejection stands; you can submit another result while the escrow holds the price.
  [day {d}] {head} you lost the dispute and your deposit of {fee}. The rejection stands.
  [day {d}] {head} the dispute went your way. Your rejection stands and the price stays held.
  ```

  - `{reading}`: `F{tests.decimals}` (`F1` at the default). `{threshold}`: `G`. `{fee}`: the deposit, `CR`.
  - `{back}`: `, and {refunded} came back to you` when the release refunded the buyer more than 1e-9 (6.8), `{refunded}`
    `F2`; else empty. `{paid_text}`: 6.8.
  - The manager's text: the first form before the escrow's close day (the task's due day + `escrow_close_days`,
    6.9); the second, shorter form on or after it, when the escrow closes the same morning and takes no more
    submissions. The buyer's text is the same either way.
  - Example: `[day 15] The arbiter's reading of R-b8b on contract-14 is 6.1, against the threshold 5: you won the
    dispute. Your deposit of 11 came back, and you received 22.00 of the price 22; contract-14 is closed.`
- A dispute still open when the market closes is ruled on at the close (`episode.md`, "At the close").

### 6.7 The arbiter's fee, and when a released result is the buyer's

- After a ruling for the manager, the buyer owes the arbiter's fee (= the dispute deposit). It pays it with
  `pay_escrow_arbiter` (`actions-buyer.md`, "pay_escrow_arbiter"): the fee is charged at once and leaves the market (it
  goes to the arbiter, not to the manager). The manager is not notified.
- A released result is the buyer's: from the day of approval (6.4), from the day of the automatic release (6.5), from
  the ruling day when the arbiter's fee is 0 (6.6), from the day the buyer pays the arbiter's fee, or, for a dispute
  ruled at the market's close, from the market's last day (the day it closed) when the fee is collected then. That day
  is its receive day for the value (4).
- Until the buyer pays the fee it owes, the released result is not its: it cannot test it, and the contract stays
  among its active contracts (`view-contracts.md`, "Active or finished"). There is no deadline for paying. A fee owed
  from an earlier ruling and never paid leaves the result never the buyer's (value 0); only a dispute still open at
  the market's close has its fee collected then, if the buyer's balance covers it (`episode.md`, "At the close").

### 6.8 The release and the pay cut

When an escrow releases on a result (approval, automatic release, or a ruling for the manager), with s = the day that
result was submitted, `due` the task's due day and `deadline` the contract's deadline (3.1):

```
if deadline = due:  factor = max(0, 1 − value.late_decay × max(0, s − due))
else:               factor = 0                                              if s − due >= escrow_close_days
                    factor = max(0, 1 − value.late_decay × max(0, s − deadline))   otherwise
to_manager = round(price × factor, 4)
refunded   = price − to_manager            (not rounded)
```

- The full-pay rule: a submission made by the contract's deadline is paid in full, also when that deadline is after
  the task's due day (a contract signed less than `contracts.min_contract_days` days before the due day, or after it);
  each day after the deadline cuts `value.late_decay` of the price. Since no submission is possible from
  due + escrow_close_days (6.3), the factor of a released submission is above 0.
- The escrow no longer holds the price; the contract is RELEASED on that result, on today's date; the contract's paid
  amount becomes `to_manager` (when above 0).
- The manager is credited `to_manager` (if above 0): at once at the start of a day (automatic release, ruling); at the
  round's end when the buyer's approval released it during a round (`rounds.md`, "Money from others lands at the
  round's end (R2)"). A credit that lifts an out-of-business manager above 0 puts it back in business (`episode.md`,
  "Coming back").
- The buyer is refunded `refunded` (if above 0) at once.
- Balances can carry sub-cent amounts from a cut release (e.g. 26.664); views show 2 decimals.
- `{paid_text}`, used in the texts that tell the manager what it received:

  ```
  {to_manager} of the price {price}{late}
  ```

  `{to_manager}` `F2`, `{price}` `G`; `{late}` = ` (submitted {k} day late)` if k = 1, ` (submitted {k} days late)`
  if k > 1, else empty, where k = max(0, the latest submission's day − deadline) (the latest submission is the
  released one). Example: `16.00 of the price 20 (submitted 1 day late)`.

### 6.9 The lateness close

At the start of each day d, after the rulings and the automatic releases (`episode.md`, "Escrow lateness closes"), each
escrow contract, in creation order, that still holds its price, with d ≥ due + escrow_close_days (its **close day**) and
nothing pending (no submission under review, no open dispute, and no rejection still disputable) closes unreleased: the
whole price goes back to the buyer at once. The setup fee and any lost dispute deposit stay lost. The contract is CLOSED
UNRELEASED: it takes no more runs, tests or submissions, and its task does not return to the buyer's queue (the task
counts as done for the market's close rule: `episode.md`, "5.1 Definitions").

- Something pending on the close day delays the close to the start of the first day with nothing pending, unless the
  escrow released by then. (A ruling for the buyer at the start of a day on or after the close day is followed by the
  close the same morning; an automatic release at the start of a day comes before the close step and wins.)
- An escrow contract signed on or after its close day can take no submission and closes at the start of the next day.

Notifications, to the buyer first, then to the manager:

```
[day {d}] The escrow of {contract_id} closed: nothing was released by {late} days after {its}, so its price {price} came back to you.
[day {d}] The escrow of {contract_id} closed unreleased ({late} days after {its}): it takes no more submissions.
```

- `{late}` = d − due (`INT`, at least escrow_close_days), followed by `days` *(singular rule)*: `day` when `{late}`
  is 1, which can happen only when escrow_close_days is 1 (`value.late_decay` = 1, within the 1e-9 of its formula).
- `{its}` = `its task's due day` when the contract's deadline differs from the due day, else `its deadline`.
- `{price}`: `G`.
- Example: `[day 33] The escrow of contract-53-2 closed: nothing was released by 5 days after its task's due day, so
  its price 7 came back to you.`

An escrow still holding its price when the market closes refunds it then (`episode.md`, "At the close").

### 6.10 Money of an escrow contract

| event | money | when |
|---|---|---|
| acceptance | buyer: price → held; setup fee → out of the market | at once |
| submission, rejection | none | — |
| approval | held → manager `to_manager`; held → buyer `refunded` | buyer at once; manager at the round's end |
| automatic release | as approval | at once (start of day) |
| dispute | manager: deposit → held | at once |
| ruling for the manager | held deposit → manager; held price → manager `to_manager` and buyer `refunded` | at once (start of day) |
| ruling for the buyer | held deposit → out of the market | at once |
| arbiter's fee paid | buyer: fee → out of the market | at once |
| lateness close, end_contract | held price → buyer, whole | at once |
| market's close | open disputes ruled as above (the arbiter's fee taken from the buyer → out of the market, if its balance covers it); submissions under review released as an automatic release; then any held price → buyer, whole | at once (`episode.md`, "At the close") |

## 7. When a contract can be ended

The buyer ends a contract with `end_contract` (`actions-buyer.md`, "end_contract": the refusal texts in check order,
the effects and the texts). Whether it can, by form and state, checked in this order (the first matching row decides):

| form | state | can it be ended now? |
|---|---|---|
| either | already ended | no |
| escrow | RELEASED | no, never |
| escrow | CLOSED UNRELEASED | no, never |
| escrow | UNDER REVIEW | no (approve or reject first) |
| escrow | IN DISPUTE | no, until the ruling |
| escrow | REJECTED, DISPUTABLE | no; from the day after the dispute window if the manager does not dispute |
| escrow | REJECTION STANDS | yes, on any day (before or after the deadline) |
| escrow | NOTHING SUBMITTED, deadline = the full-pay cap (due + escrow_close_days − 1) | no, never as overdue: only once a later submission is rejected and the rejection stands (the REJECTION STANDS row); otherwise its escrow closes unreleased first (6.9) |
| escrow | NOTHING SUBMITTED, today ≤ deadline | no; from deadline + 1 |
| escrow | NOTHING SUBMITTED, today > deadline | yes (overdue) |
| plain | has a delivery | no, never |
| plain | signed after its deadline day | no, never |
| plain | today ≤ deadline | no; from deadline + 1 |
| plain | today > deadline, nothing delivered | yes (overdue) |

At the defaults an escrow's deadline is the full-pay cap exactly when it was signed on or after the task's due day.
Ending is free; the contract is ENDED, its task goes back to the buyer's queue, and it yields no value. Its effects
and texts: `actions-buyer.md`, "11. end_contract" (the money: 5.2 and 6.10).

## 8. Sealed bids and their opening

- The round index of round r of day d is g = (d − 1) × `time.rounds_per_day` + r. A post made in round index g can
  have a bid accepted from round index g + `contracts.bid_window_rounds` + 1 (its **opening round**), counted across
  days. Example (defaults): a post made in round 1 opens in round 3 of the same day; in round 2, round 1 of the next
  day; in round 3, round 2 of the next day.
- Until its opening round the post's bids are **sealed** from its buyer: its view shows none (`view-contracts.md`,
  "The bids field on the buyer's own post"), it is not notified of bids placed, updated or withdrawn on the post, and
  `accept_bid` answers as if the bid did not exist (`actions-buyer.md`, "accept_bid"). From the opening round the
  buyer sees the post's open bids and is notified of later bids, updates and withdrawals (`actions-manager.md`, "bid"
  and "withdraw_bid"). Other buyers and other managers never see a manager's bids.
- A post takes bids until a bid is accepted or the post is withdrawn; it never expires. Editing a post does not
  change its opening round.
- **The opening notice.** At the start of each round, after its turn order is drawn and before any turn (`episode.md`,
  "The start of each round"), for each post still open whose opening round is this round and that has at least one open
  bid by a manager not out of business, in the order the posts were made, the post's buyer is notified:

  ```
  [day {d}, round {r}] The bids on {post_id} are opened: {n} bid, which you can accept from now.
  [day {d}, round {r}] The bids on {post_id} are opened: {n} bids, which you can accept from now.
  ```

  `{n}` = the post's open bids by managers not out of business (`INT`); the first form when n = 1. No notice when there
  is no such bid.

## 9. The notifications of these processes

| text (stamp omitted) | to | when | section |
|---|---|---|---|
| `{label} ({origin}) for {contract_id} has arrived.{gone}` | manager | start of day | 1.4 |
| `{contract_id} is due today (deadline day {d}).` | buyer, then manager | start of day | 3.3 |
| `The task of {contract_id} is due today (day {d}): its value to you falls for each day after it. The contract's deadline day is {deadline}.` | buyer | start of day | 3.3 |
| `{head} you won the dispute. ...` / `{head} you lost the dispute ...` | manager | start of day | 6.6 |
| `{head} the dispute went against you. ...` / `{head} the dispute went your way. ...` | buyer, after the manager | start of day | 6.6 |
| `{label} on {contract_id} was released: you neither approved nor rejected it ...` | buyer | start of day | 6.5 |
| `{label} on {contract_id} was released: {buyer_id} neither approved nor rejected it ...` | manager, after the buyer | start of day | 6.5 |
| `The escrow of {contract_id} closed: nothing was released ...` | buyer | start of day | 6.9 |
| `The escrow of {contract_id} closed unreleased ...` | manager, after the buyer | start of day | 6.9 |
| `The bids on {post_id} are opened: {n} bid, which you can accept from now.` (n = 1) / `... {n} bids, ...` (otherwise) | buyer | start of round | 8 |

The out-of-business and back-in-business notifications are in `episode.md`, "Out of business". No notification is sent
when a run starts, when a reading is taken, or when the buyer pays the arbiter's fee. The notifications the actions send
(deliveries, submissions, keeps, payments, approvals, rejections, disputes, ends, bids, acceptances) are in the actions
files.
