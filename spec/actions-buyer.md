# Buyer actions

This file specifies what happens when the market runs one action of a buyer: the rules every action of either role
goes through (how its name is matched, the checks before the action's own checks, the result line, how arguments,
ids, result labels, amounts and thresholds are read), then, for each buyer action (`post_task`, `edit_post`,
`withdraw_post`, `accept_bid`, `test_delivery`, `keep_delivery`, `approve_submission`, `reject_submission`,
`pay_escrow_arbiter`, `end_contract`), its arguments, every check in the order it runs with its exact error text, its
effects on state and money, its exact success text, the notifications it sends, and its priority class in a round.
`pay` (an action of both roles) is in actions-manager.md, "8. pay"; `send_message` is in messaging.md,
"2. `send_message(to, text)`". How an `act` call's items become actions (the shapes read, the action cap, the
memory) is in turns.md, "7. Reading the reply that ends the chain", "8. The action cap and the notes that open the
results" and "10. The memory"; when the actions of a round run and the state each agent's checks see (R1) are in
rounds.md, "6. The action phase"; when money credited to others lands (R2) is in rounds.md, "7. Money from others
lands at the round's end (R2)". actions-manager.md uses section 1 of this file unchanged.

The number formats (`G`, `CR`, `F2`, `F1`, `Fn`, `REPR`), the conversions `TEXT` and `PY`, and what a "character"
and "whitespace" are, are those of README.md, "Number formats and rounding". Every text in a fenced block is
byte-exact apart from its `{placeholders}`.

## 1. Rules common to every action (both roles)

### 1.1 The actions of each role

| role | its own actions (in this order) |
|---|---|
| buyer | `post_task`, `accept_bid`, `test_delivery`, `approve_submission`, `reject_submission`, `pay_escrow_arbiter`, `end_contract`, `edit_post`, `withdraw_post`, `keep_delivery` |
| manager | `bid`, `delegate_run`, `run_myself`, `test_result`, `deliver`, `dispute`, `withdraw_bid` |

Both roles also have `pay`, and `send_message` when the role can send (messaging.md, "1. The five modes": buyers in
`buyers`, `within_roles` and `all`; managers in `managers`, `within_roles` and `all`).

One more name runs but is never listed to agents: `compute` (always refused as an action; turns.md, "7.6 How the
actions then run"). `update_memory`, `update_summary` and `end_turn` are no actions: each is an unknown action (1.2,
step 4).

`{list}`, the list the refusals below show (the agent's role and whether it can send decide it). This order differs
from the order the prompts list the actions in; the refusals must use this order.

A buyer that cannot send:

```
pay, post_task, accept_bid, test_delivery, approve_submission, reject_submission, pay_escrow_arbiter, end_contract, edit_post, withdraw_post, keep_delivery
```

A buyer that can send:

```
send_message, pay, post_task, accept_bid, test_delivery, approve_submission, reject_submission, pay_escrow_arbiter, end_contract, edit_post, withdraw_post, keep_delivery
```

A manager that cannot send:

```
pay, bid, delegate_run, run_myself, test_result, deliver, dispute, withdraw_bid
```

A manager that can send:

```
send_message, pay, bid, delegate_run, run_myself, test_result, deliver, dispute, withdraw_bid
```

### 1.2 Matching the name, and the checks before the action's own

An action reaches the market as a name plus named arguments (turns.md, "7.4 From items to actions", says how act's
items are read into that form). The market then does, in this order (the first that applies decides the result;
nothing changes):

1. The name is the action's name exactly as sent. A missing name (absent, null, a value that is not a string, or a
   string that is empty or only whitespace) is "no name".

2. No name:
   ```
   the action names no action: give its name with "action", as {"action": "<action name>", "<argument name>": <value>}
   ```
3. The name is one of `verify`, `write_code`, `self_test`, `view_delivery`, `note`, or is `send_message` under
   `message_channels` `off`. (`end_turn` is not one of these names: an action named `end_turn` gets step 4's
   refusal.)
   ```
   '{name}' is not available in this market. Your actions: {list}.
   ```
   Under `managers` and `buyers`, `send_message` sent by the role that cannot send is not one of its actions, so it
   gets step 4's text (its `{list}` never shows `send_message`).
4. The name is none of: this agent's own actions, the other role's actions, `pay`, `compute`, and
   `send_message` when this agent's role can send:
   ```
   unknown action '{name}'. Your actions: {list}.
   ```
   followed, when the name with surrounding whitespace removed and lowercased contains `contract`, by
   ```
    Contracts are created only when a buyer accepts a bid (accept_bid).
   ```
   and then, for a manager (the role that has `deliver`) whose name, with surrounding whitespace removed and
   lowercased, starts with `submit`, by
   ```
    To deliver a result, or submit it for the buyer's review, use deliver.
   ```
   (Both additions begin with one space. So `Create_Contract` and ` submit_result` still get them.)
5. The name is an action of the other role:
   ```
   '{name}' is not available to a {role}: it is a {other_role}'s action
   ```
   `{role}` is `buyer` or `manager` (the actor's), `{other_role}` the other. Example:
   `'bid' is not available to a buyer: it is a manager's action`.
6. The agent is out of business:
   ```
   you are out of business (balance at or below 0) and cannot act
   ```
   Only managers go out of business (episode.md, "9. Out of business"); a buyer never gets this text, whatever its
   balance.
7. In a round's action phase, if the action already failed on the state the agent's turn saw (R1), it fails now with
   that same error text and nothing changes (rounds.md, "6.6 Running one action: the state its turn saw (R1)"). Hints
   in such a text (e.g. "Open posts: ...") therefore list the state the agent saw.
8. The action's own checks and effects (sections 2-11 here; actions-manager.md for the manager's actions and, in
   "8. pay", for `pay`).

Every check of an action runs before any of its effects: a failed action changes nothing (no state, no money, no
notification).

In 1-8, `{name}` is the name exactly as sent: a manager's `delegate` gives `unknown action 'delegate'. Your actions: pay, bid, delegate_run, run_myself, test_result, deliver, dispute, withdraw_bid.`, and ` bid ` gives `unknown action ' bid '. Your actions: {list}.`
Names are matched exactly: a name in capitals or with surrounding whitespace, or a name the market no longer uses
(`approve`, `reject`, `pay_arbiter`, `delegate`), is not one of the agent's actions and gets step 4's text.

### 1.3 The result line

Each action that ran gives one line in the agent's results, `- {shown}: OK: {message}` or
`- {shown}: ERROR: {message}`, where `{message}` is the success text or the error text given in this file (or in
actions-manager.md). The line's form and `{shown}` are owned by turns.md, "9. Result lines" (the name with surrounding
whitespace removed, its case kept: `" Bid "` shows as `- Bid: ERROR: unknown action ' Bid '. ...`). How the lines are
assembled into the results text, and the notes beside them (the cap note, the memory note, the correction's lines),
are in turns.md, "8. The action cap and the notes that open the results" and "12. The turn's results text"; their
spacing in the view is in view.md, "4.3 Spacing inside the results text"; the clash notes appended to a line are in
rounds.md, "8.2 What each failed action is told".

### 1.4 Arguments

An argument counts as given when its name is present with a value other than null and other than the empty text
`""`. Any other value counts as given, including `0`, `false`, whitespace, lists and objects. Each argument is read
under one name only, the one the prompts state. A required argument that is not given fails with that name:

```
missing argument '{first_name}'
```

| argument | name | used by |
|---|---|---|
| task | `task_id` | post_task |
| post | `post_id` | edit_post, withdraw_post, bid |
| bid | `bid_id` | accept_bid |
| contract | `contract_id` | every action on a contract (pay: actions-manager.md, "8. pay") |
| result | `result` | test_delivery (optional), keep_delivery (required); manager actions |
| form | `form` | post_task, edit_post (optional) |
| threshold | `threshold` | post_task, edit_post (optional); bid |
| rejection message | `message` | reject_submission (optional) |

(The manager actions' other arguments are in actions-manager.md, "1.1 Arguments".) Arguments an action does not read
are ignored without notice, whatever their names and values (e.g. `message` on `post_task`, `x` on any action). So
another name for one of these arguments (`task`, `post`, `bid`, `contract`, `result_id`, `label`, `prefer`,
`preference`, `quality_threshold`, and `reason` or `text` on `reject_submission`) is ignored: an action given only
such a name fails with `missing argument '{first_name}'` when the argument is required.

### 1.5 How argument values appear in texts

Argument values (as the action holds them after turns.md, "7.4 From items to actions") are quoted back in texts with
the conversions `TEXT` and `PY` of README.md, "Number formats and rounding". Wherever this file writes `{raw}`, it
means `TEXT` of the argument as sent (not normalised).

### 1.6 Ids of tasks, posts, bids and contracts

Ids are stored lowercase: `task-N`, `post-N`, `post-N-k`, `bid-N`, `contract-N`, `contract-N-k` (N, k decimal
integers; how they are made: sections 2 and 5, and episode.md, "3.2 Task arrivals" for tasks). An id argument of kind
`{kind}` (task, post, bid or contract) is looked up as follows, among ALL ids of that kind in the market (any
participant's):

1. `s` = `TEXT(raw)`. Nothing is trimmed, lowercased or stripped of spaces: `Contract-1`, ` contract-1` and
   `contract 1` name no contract.
2. If `s` is decimal digits, or digits, `-`, digits (`3`, `3-2`), it becomes `{kind}-{s}` (`bid-3`, `post-3-2`).
   Here a digit is any Unicode decimal digit, and the digits keep their text as written (no conversion to an
   integer): `05` as a task becomes `task-05`, and `３` as a bid becomes `bid-３`. Ids are made only of ASCII digits
   without leading zeros, so such texts are never found (step 3).
   No other rewrite applies: an id of another kind names nothing (`post-5` as a contract and `task-5` as a post are
   not found).
3. Not found:
   ```
   no {kind} with id '{raw}'. {hint}
   ```
   `{hint}` by kind (lists in the order the items were created; the bid hint in the order bids were first placed):
   - task: `Your queued (not yet posted) tasks: {ids}.` with the agent's tasks whose state is queued, joined by `, `,
     or `Your queued (not yet posted) tasks: none.`
   - post: `Open posts: {ids}.` with every open post in the market, any buyer's, joined by `, `, or
     `Open posts: none.`
   - bid (a buyer): `Open bids on your posts: {items}.` or `Open bids on your posts: none.`, the items joined by `, `,
     each `{bid} ({manager} at {price}{terms} on {post})`, over the open bids on this buyer's posts whose bids it can
     see now (not sealed: section 5) and whose manager is not out of business. `{price}` is `G` of the bid's price;
     `{terms}` is ` with threshold {t} (escrow)` (`{t}` in `G`) for an escrow bid, else empty.
     Example: `no bid with id 'bid-68'. Open bids on your posts: bid-61 (M-0ed at 35 with threshold 5 (escrow) on post-25), bid-63 (M-7f8 at 6 with threshold 5 (escrow) on post-25), bid-66 (M-ee6 at 9.5 on post-26).`
   - contract: `Your contracts: {items}.` or `Your contracts: none.`, over every contract the agent is the buyer or the
     manager of, each `{contract} ({task})`, or `{contract} ({task}, ended)` for one its buyer ended, joined by `, `.
     For a manager this is followed by ` A bid becomes a contract only when the buyer accepts it.` and, when it has
     open bids, by ` Your bids not yet accepted: {bid} on {post}, {bid} on {post}.` (bids in the order first placed).
   Examples: `no bid with id 'bid-1'. Open bids on your posts: none.`; `no contract with id 'contract-1'. Your contracts: none.`

### 1.7 "One of your contracts"

Every action on a contract (all buyer actions from section 6 on) first finds the contract: the contract argument is
required (`missing argument 'contract_id'`), looked up as in 1.6, and the agent must be the contract's buyer (for a
buyer's action) or its manager (for a manager's action), else:

```
{contract} is not one of your contracts. {hint}
```

with `{contract}` the id found and `{hint}` the contract hint of 1.6.

### 1.8 Result labels

A result argument names a result label exactly as written: the label is `TEXT(raw)`, unchanged. Labels are `R-` plus 3
lowercase hex digits (processes.md, "1.1 Workers, runs and results"), so `r-3ad`, `3ad`, `R-3AD`, ` R-3ad ` and the
integer `123` name no result and fail with the action's not-found text (e.g. `no result 'r-3ad' in contract-5.
{results}`).

### 1.9 Participants

A participant argument (pay, send_message) must be written exactly as the participant's id: `m-14b` or ` M-14b `
names no one.
Not found:

```
no participant with id '{raw}' (participants: {ids})
```

`{ids}`: every participant, buyers first then managers, each in seat order, joined by `, `
(example: `no participant with id 'M-000' (participants: B-c53, B-d75, M-14b, M-849, M-f8c)`).

### 1.10 Amounts (bid's price, pay's amount)

`x` = the double that the text `TEXT(v)` reads as with Python's `float` syntax (surrounding whitespace allowed,
except U+001C to U+001F, which are not removed from its ends, so `"\u001c7"` is unreadable; an optional sign, then digits with an optional decimal point that has digits on at least one side (`5`, `5.`, `.5`,
`5.5`; not `.`), then an optional exponent (`e` or `E`, an optional sign, digits); or `inf`, `infinity`, `nan` in any
case; a single underscore is allowed between two digits; a decimal digit is any Unicode decimal digit, as Python's
`float` accepts, so a fullwidth `"２５"` reads as 25). Nothing else is removed. So `25`, `"25"`, `" 25 "`,
`"24.555"` and `"1e2"` are read; `"25 credits"`, `"$24"`, `"1,000"`, `"five"`, `true` and lists are not:

```
{name} must be a number, got {v}
```

with `{v}` = `PY(v)`. Then, if `x` is not finite or `round(x, 2) <= 0`:

```
{name} must be a positive number
```

(`0`, `-3`, `0.004`, `"inf"`, `"nan"` all give this; so does a JSON integer too large in magnitude for a finite
double, e.g. `1` followed by 309 zeros, either sign, whose text reads as `inf` or `-inf`). The amount used is `round(x, 2)` (`24.555` gives 24.55; `0.005`
gives 0.01). `{name}` is `price` (bid) or `amount` (pay).

### 1.11 Thresholds (post_task, edit_post, bid)

`t` = the double the value reads as (a number as it is, an integer as the nearest double; a string read as in 1.10:
`"\u001c7"` is unreadable, `" 7 "` reads as 7; a boolean, a list, an object or an unreadable string
counts as NaN). A JSON integer too large for a finite double, wherever this reading is used (also a bid's threshold
and a delivery's stated quality, actions-manager.md), is outside this spec (README.md, "Number formats and
rounding", input (a)); the preferred answer is the first refusal below. The same number sent as a string reads as
`inf` and is refused below. Then, in order:

```
threshold must be a quality between 0 and 10, got {v}
```
when `t` is NaN or not in [0, 10] (so also for `inf`), and
```
threshold must be a whole number between 0 and 10, got {v}
```
when `t` is not a whole number; `{v}` = `PY(v)` (`got 7.5`, `got '7.5'`, `got 11`, `got 'high'`, `got True`,
`got [7]`). The threshold used is `t` as a double (a whole number; `-0` becomes `0`), shown everywhere in `G` (`7`).

### 1.12 Balance checks

An action that costs `cost` fails when `balance + 1e-9 < cost`, with

```
insufficient balance for {what}: it costs {cost}, you have {balance}
```

`{cost}` is `CR` of the cost (`1`, `29.15`, `10.55`, `4.10`), `{balance}` is `F2` of the agent's balance (it can be negative: `-2.31`; a negative balance that rounds to 0 keeps its
sign and reads `-0.00`). `{what}` is given per action. Money credited to the agent by others in the same round has
not landed yet (rounds.md, "7. Money from others lands at the round's end (R2)").

### 1.13 Effects, notifications, priority

- Money the acting agent pays or gets back moves at once. Money an action credits to ANOTHER participant lands at the
  round's end (rounds.md, "7. Money from others lands at the round's end (R2)"); among this file's actions this is
  only the manager's share of an approval (section 8); a `pay` to another participant lands then too
  (actions-manager.md, "8. pay").
- A notification is queued for its recipient and shown in its next view, prefixed `[day {d}, round {r}] ` (view.md,
  5. "Notifications since your last turn"). The texts below are the part after that prefix. Notifications are
  queued in the order given.
- Each action has a priority class (0 to 7). In a round's action phase, among the actions free to run, a lower class
  runs first; but a participant's action never runs before its own earlier actions, and the clash rules can put an
  action ahead of another (rounds.md, "6.3 The clash rules" and "6.5 The order the phase's actions run in"). Each
  action's class and clash rules are listed with it; the classes are summed up in section 12.
- Every day number, round number, count and submission number in this file's texts (`{d}`, `{r}`, `{until}`,
  `{until_plus_1}`, `{dl}`, `{dl_plus_1}`, `{close}`, `{s}`, `{due}`, `{n}`, `{k}`, and the `n` of `{rel(d)}`) is an
  integer written in `INT`, unless a format is given with it.
- Where a text uses `{rel(d)}`: with `today` the current day, `{n} days left` (d > today, n = d − today, n ≥ 2),
  `1 day left`, `today`, `1 day ago`, `{n} days ago` (d < today, n = today − d, n ≥ 2).

## 2. post_task

Posts one of the buyer's queued tasks on the forum. Free. Priority class 0.

Arguments: `task_id` (required), `form` (optional), `threshold` (optional).

Checks, in order:

1. Task: `missing argument 'task_id'`; not found (1.6):
   `no task with id '{raw}'. Your queued (not yet posted) tasks: {ids}.` (or `... none.`).
2. Not the agent's task:
   ```
   {task} is not your task
   ```
3. The task's state is not queued:
   ```
   {task} is already {state}
   ```
   with `{state}` = `posted`, `contracted` or `withdrawn`; for `posted` the text continues
   ```
    as {post}, which stays open until you accept a bid on it or withdraw it (edit_post changes its stated form and threshold)
   ```
   (`{post}` the task's current post). Examples: `task-1 is already posted as post-1, which stays open until you accept a bid on it or withdraw it (edit_post changes its stated form and threshold)`;
   real: `task-16 is already contracted`. A task whose contract its buyer ended is queued again (section 11).
4. Form, when given: `f` = `TEXT(form)`, exactly as written. `none`, `any`, `either`, `both`, `no preference` and
   the empty text mean no preference; `plain` and `escrow` are the two forms; anything else (also `Escrow` or
   ` escrow `):
   ```
   form must be "plain" or "escrow" (or left out for no preferred form), got {v}
   ```
   (`{v}` = `PY(form)`: `got 'premium'`, `got False`, `got 'Escrow'`).
5. A price argument: if any of `budget`, `max_price`, `price`, `terms`, `menu`, `escrow_menu`, `escrow_terms` is given
   (1.4), the first in that order is named:
   ```
   posts carry no price, so post_task takes no {name}: managers quote their own price when they bid
   ```
6. `threshold` given and the form (after 4) is not escrow (plain or no preference):
   ```
   only a post that prefers escrow (form="escrow") takes a threshold
   ```
7. The threshold's own checks (1.11).

Effects:
- A post is created: id `post-N` where task-N is the task, or `post-N-k` for the task's k-th post (k = the number of
  contracts on the task its buyer has ended, plus 1; `-k` only when k ≥ 2). It records its buyer, its task, the day
  and round it was made, its form (plain, escrow or none) and its threshold (or none). Its bids can be accepted from
  the round given in section 5; until then they are sealed from the buyer.
- The task's state becomes posted, and the post is its current post.
- No notification. (Other participants see the post in their next views; under R1 no manager can bid on it in the
  round it was made: rounds.md, "6.6 Running one action: the state its turn saw (R1)".) A buyer may post any queued
  task at any time, also after its due day, and hold any number of posts.

Success:

```
posted {task} as {post}{pref}{thr}
```

`{pref}` = `; prefers a plain contract` | `; prefers escrow` | empty (no preference); `{thr}` = `, threshold {t}`
(`G`) when a threshold was given, else empty. Examples: `posted task-1 as post-1; prefers escrow, threshold 7`,
`posted task-3 as post-3; prefers a plain contract`, `posted task-1 as post-1`, `posted task-8 as post-8-2; prefers escrow, threshold 6`.

## 3. edit_post

Changes the form and threshold an open post states. Free. Priority class 7, the highest (where that puts it in a
round: rounds.md, "6.5 The order the phase's actions run in").

Arguments: `post_id` (required), `form` (optional), `threshold` (optional).

Checks, in order:

1. Post: `missing argument 'post_id'`; not found (1.6): `no post with id '{raw}'. Open posts: {ids}.` (or `... none.`).
2. Not the agent's post:
   ```
   {post} is not your post
   ```
3. The post is closed:
   ```
   {post} is closed: {why}
   ```
   `{why}` = `you withdrew it` | `a bid on it was accepted`. Real: `post-1 is closed: a bid on it was accepted`.
4. A price argument: if any of `budget`, `max_price`, `price`, `terms`, `menu`, `escrow_menu`, `escrow_terms` is
   given (1.4), the first in that order is named:
   ```
   posts carry no price, so edit_post takes no {name}: managers quote their own price when they bid
   ```
5. The new form: when `form` is given, read as in section 2, check 4 (same error text); when not given, the post's
   current form.
6. The new threshold:
   - `threshold` given as exactly the string `none` (the value the buyer's prompt offers): no threshold (no further
     check, whatever the new form). Any other spelling (`"null"`, `"None"`, `" none "`) is read as a threshold below;
   - `threshold` given otherwise: section 2's check 6 against the new
     form (`only a post that prefers escrow (form="escrow") takes a threshold`), then 1.11;
   - not given: the post's current threshold if both the post's current form and the new form are escrow, else no
     threshold (so changing the form to plain or no preference removes it, and changing to escrow from another form
     starts with none).
7. Nothing changes (the new form and threshold equal the current ones):
   ```
   {post} already states this ({state}): nothing changed
   ```

`{state}`, `{now}`, `{was}` are a post's stated preference: `prefers escrow, threshold {t}` (`G`) | `prefers escrow` |
`prefers a plain contract` | `no preferred form` (as views show a post with no form).

Effects: the post's form and threshold are replaced. Its bids are unchanged (each keeps its own price and terms), and
its bid window (section 5) is unchanged.

Notification to each manager that has an open bid on the post (also a manager whose bid the buyer cannot see yet, and
a manager out of business), one per manager, in order of manager id (character order):

```
{buyer} changed {post}'s stated preference to: {now} (was: {was}); your bid on it stands as you made it
```

Success:

```
{post} now states: {now} (was: {was}); its bids stand as they were made
```

Examples: `post-1 now states: prefers escrow, threshold 0 (was: prefers escrow); its bids stand as they were made`,
`post-1 now states: no preferred form (was: prefers escrow, threshold 0); its bids stand as they were made`.

## 4. withdraw_post

Withdraws one of the buyer's open posts and, with it, its task. Free. Priority class 4.

Arguments: `post_id` (required).

Checks, in order:

1. Post: `missing argument 'post_id'`; not found: `no post with id '{raw}'. Open posts: {ids}.` (or `... none.`).
2. `{post} is not your post`
3. The post is closed:
   ```
   {post} is not open: {why}
   ```
   `{why}` = `you withdrew it` | `a bid on it was accepted`. Real: `post-11 is not open: a bid on it was accepted`.

Effects:
- The post closes as withdrawn (its closing day and round recorded: managers' bid refusals state them,
  actions-manager.md, "2. bid").
- The task's state becomes withdrawn, for good: it can never be posted again (`post_task` answers
  `{task} is already withdrawn`), and it counts as done for the market's close rule (episode.md, "5.1 Definitions").
- Every open bid on the post becomes lost (including bids of managers out of business), and each bid's manager is
  notified, in the order the bids were first placed (bid-N order; a replacing bid keeps its id and its place):
  ```
  {buyer} withdrew {post}: your bid {bid} on it is lost
  ```
  (also while the bids are still sealed from the buyer).

Success:

```
withdrew {post}: {task} leaves the market{lost}
```

`{lost}` = `; {n} open bid on it lost` (n = 1) or `; {n} open bids on it lost` (n ≥ 2), n = the number of bids made
lost whose manager is not out of business (the bids the buyer's views and the bid hint, 1.6, show), only when n ≥ 1
and the post's bids are not sealed from the buyer (section 5); else empty. While sealed the buyer is not told bids
existed; when every lost bid is a hidden one (its manager out of business), `{lost}` is empty. Every lost bid's
manager is still notified. Real: `withdrew post-12: task-12 leaves the market; 3 open bids on it lost`,
`withdrew post-16: task-16 leaves the market; 1 open bid on it lost`, `withdrew post-36: task-36 leaves the market`.

Clash rule (rounds.md, "6.3 The clash rules", B_withdraw): a withdraw_post of a post runs before any bid or
withdraw_bid on that post in the same round. A cycle with the participants' own orders drops B_withdraw only against
a stronger rule (rounds.md, "6.5 The order the phase's actions run in"). Actions that are not their participant's own
take part in no clash (rounds.md, "6.2 Targets: what each action acts on"): when the buyer and a manager each list
[`bid` P, `withdraw_post` P], B_withdraw holds, in either turn order, and the buyer's withdrawal runs before the
manager's bid.

## 5. accept_bid

Accepts a bid on one of the buyer's posts, which creates a contract. Free, except that an escrow bid moves its price
into escrow and charges the setup fee. Priority class 4.

Sealed bids. A post made in round index g (README.md, "Vocabulary": g = (day − 1) × rounds_per_day + round) has its
bids sealed from its buyer while the current round index is below
`accept_from = g + contracts.bid_window_rounds + 1` (processes.md, "8. Sealed bids and their opening"). Other buyers
never see a post's bids.

Arguments: `bid_id` (required).

Checks, in order:

1. Bid: `missing argument 'bid_id'`; not found (1.6):
   ```
   no bid with id '{raw}'. Open bids on your posts: {items}.
   ```
   (or `... none.`).
2. The bid is on another buyer's post, or on this buyer's post while its bids are sealed: the SAME text as 1 (so a
   buyer cannot probe bids it cannot see).
3. The post is closed:
   ```
   {post} is already closed
   ```
4. The bid is not open (only `withdrawn` can occur here):
   ```
   {bid} is {status}
   ```
   (`bid-1 is withdrawn`).
5. In an action phase, the bid's price or threshold differs from the one the buyer's turn saw (its manager replaced
   it earlier in the phase: rounds.md, "6.6 Running one action: the state its turn saw (R1)", steps 1 and 3; a
   replacement on the same price and threshold passes):
   ```
   {bid} on {post} was replaced earlier this round: its terms are now {price}{terms}, not the ones your turn saw: no contract was made
   ```
   `{price}` = `CREDITS` of the bid's new price; `{terms}` = ` with threshold {t} (escrow)` (`{t}` in `G`) for an
   escrow bid, else ` (plain)`. Example: `bid-3 on post-3 was replaced earlier this round: its terms are now 28
   credits with threshold 6 (escrow), not the ones your turn saw: no contract was made`.
6. The bid's manager is out of business:
   ```
   {manager} is out of business and cannot take contracts
   ```
7. An escrow bid (one with a threshold), with `fee = round(contracts.escrow.fee_rate × price, 2)`: the balance check
   (1.12) with cost `round(price + fee, 6)` and
   `{what}` = `accepting the escrow bid {bid} (its price {price_g} is held and the fee {fee_cr} charged)`:
   ```
   insufficient balance for accepting the escrow bid {bid} (its price {price_g} is held and the fee {fee_cr} charged): it costs {cost}, you have {balance}
   ```
   `{price_g}` = `G` of the price, `{fee_cr}` = `CR` of the fee, `{cost}` = `CR`, `{balance}` = `F2`. Example:
   `insufficient balance for accepting the escrow bid bid-2 (its price 50 is held and the fee 5 charged): it costs 55, you have 54.99`.
   A plain bid needs no balance (acceptance is free, also with a negative balance).

Effects, in this order:
1. The contract is created:
   - id `contract-N` (task-N its task) for the task's first contract, `contract-N-k` for its k-th (k = the number of
     contracts on the task its buyer has ended, plus 1; `-k` only when k ≥ 2). The contract made from post-N-k is
     always contract-N-k.
   - buyer, manager (the bid's), price (the bid's), form escrow with the bid's threshold if the bid has one, else
     plain; signed on the current day and round; the task's due day (it never changes).
   - its deadline, set once at signing, for both forms, by the formula of processes.md, "3.1 The contract's
     deadline" (which also defines `escrow_close_days`): the "deadline day" agents are shown and that the end and
     lateness rules use.
   - its own random-draw key (randomness.md, "3. The draws": the contract's stream key; a task's k-th contract has
     its own).
2. Escrow only: the price moves from the buyer's balance into escrow (held; at once; booked in the buyer's accounts
   as `paid into escrow (total)`), then the fee, if above 0, is charged (at once; it leaves the market; booked as
   `escrow fees`). The manager pays nothing.
3. The post closes (accepted; its closing day and round recorded); the task's state becomes contracted and the
   contract is its current contract.
4. Every OTHER open bid on the post becomes lost (including bids of managers out of business), and each bid's manager
   is notified, in the order the bids were first placed (bid-N order; a replacing bid keeps its id and its place):
   ```
   Your bid {other_bid} on {post} lost: {buyer} accepted another bid.
   ```
   (The winning price is not named.)
5. The accepted bid becomes accepted, and its manager is notified:
   ```
   {buyer} accepted your bid {bid}: you now have {contract} for {task} at {price}{terms}.
   ```
   `{price}` = `CREDITS` of the price; `{terms}` = ` with threshold {t} (escrow)` (`G`) for an escrow bid, else empty.
   Example: `B-44c accepted your bid bid-15: you now have contract-6 for task-6 at 30 credits with threshold 6 (escrow).`;
   a price of 1: `at 1 credit`.

The manager's bids on other posts are unaffected (a manager may win any number of posts).

Success, plain:

```
created {contract} with {manager} at price {price}
```

escrow: the same followed by

```
 (escrow, threshold {t}): {price_g} moved from your balance into escrow and the fee {fee_cr} was charged
```

`{price}` = `G` of the price (`32`); `{t}`, `{price_g}` in `G`; `{fee_cr}` = `CR` of the fee charged (`0` when the
fee is 0). Examples:
`created contract-1 with M-829 at price 32`;
`created contract-6 with M-fda at price 30 (escrow, threshold 6): 30 moved from your balance into escrow and the fee 3 was charged`;
`created contract-3 with M-14b at price 20 (escrow, threshold 5): 20 moved from your balance into escrow and the fee 2 was charged`.

Clash rules (rounds.md, "6.3 The clash rules"): an accept_bid of bid X on post P runs before its manager's replacing
bid on P (A8), before a withdraw_bid of X (A9), and before anyone else's bid on P and every withdraw_bid of another
bid on P, X's manager's included (B_accept); the bid's manager's own runs, test_result and dispute run before it
(A10). A cycle between the participants' own orders and these rules drops the weakest of the clashes that close it,
by the prompts' priority: A10 (rule ii) gives way to every other rule, and A8, A9 (rule i) and B_accept give way to
A1-A7 (rounds.md, "6.5 The order the phase's actions run in", which lists the cases). When A8 gives way, the
replacing bid runs first and the acceptance fails by check 5. Which bids an acceptance can name under R1: rounds.md, "6.6 Running one action: the
state its turn saw (R1)" and "8.6 Reading the corrections and the correction phase".

## 6. test_delivery

Takes one noisy reading of a result delivered to the buyer (plain) or under its review or released to it (escrow).
Costs `tests.buyer_cost`. Priority class 0.

Arguments: `contract_id` (required), `result` (optional).

Which result: the one named (1.8); when none is named, the default: on a plain contract the delivery in use (the
label the buyer kept, else the latest delivered); on an escrow contract the submission under the buyer's review if
any, else the released result if it is the buyer's (see check 3), else none. In a round's action phase an unnamed
result is the default as found on the state the buyer's turn saw (R1: rounds.md, "6.6 Running one action: the state
its turn saw (R1)"); when that finds one, the action proceeds as if that label had been named (and `{raw}` below is
that label).

Checks, in order:

1. The contract (1.7).
2. Nothing was ever delivered or submitted on the contract (also an escrow with no submission, and a plain contract
   its buyer ended, which by rule has no delivery):
   ```
   nothing has been delivered for {contract} yet: test_delivery reads a result already delivered to you, and your view lists each delivery
   ```
3. Escrow contract. "Yours" is: the label of the submission under the buyer's review (if any), then the released
   label if the result is the buyer's (released by approval, by automatic release, or by the arbiter's ruling for the
   manager and then only once the buyer has paid the arbiter's fee; processes.md, "6.7 The arbiter's fee, and when a
   released result is the buyer's"). At most one exists at a time.
   - No result named (and none found as above) and nothing is yours:
     ```
     nothing on {contract} is yours to test: you can test a submission under your review, or the result released to you{owed}
     ```
     `{owed}` = ` once you pay the arbiter's fee (pay_escrow_arbiter)` when the buyer owes the arbiter's fee on the
     contract (section 10), else empty.
   - The label is not yours: if the label is the released label and the buyer owes the arbiter's fee on the contract
     (section 10):
     ```
     {label} is not yours to test yet: it becomes yours once you pay the arbiter's fee (pay_escrow_arbiter)
     ```
     else, if any submission of that label on this contract was rejected:
     ```
     {label} was rejected: it went back to the manager and is not yours to test
     ```
     Otherwise:
     ```
     {raw} is not yours to test on {contract}: {yours}
     ```
     `{yours}` = the yours labels joined by `, `, or `nothing is` when there are none
     (`R-123 is not yours to test on contract-1: R-baf`; `R-211 is not yours to test on contract-2: nothing is`).
4. Plain contract: the label is not one of the labels delivered on this contract (any delivery, not only the one in
   use):
   ```
   {raw} was not delivered to you. Delivered for {contract}: {labels}.
   ```
   `{labels}`: the distinct delivered labels in order of first delivery, joined by `, `.
5. The balance check (1.12): `{what}` = `test_delivery`, cost `tests.buyer_cost`:
   e.g. `insufficient balance for test_delivery: it costs 1, you have 0.40`.

Effects: `tests.buyer_cost` is charged at once (booked as `tests`); one reading of the result is drawn, as
processes.md, "2. Readings", specifies (buyer side: standard deviation `tests.buyer_sd`, clipped to [0, 10], rounded
to `tests.decimals` decimals), and recorded among the buyer's readings of that result. Nobody else is told (the
manager learns nothing of the buyer's tests). There is no other state check: a buyer may test a plain contract's deliveries
at any time (paid, past the deadline), and keep testing a released result that is its own.

Success:

```
{label} reading: {value} (charged {cost}). Your readings [{more}{shown}] mean {mean} (n={n}, ±{se} standard error)
```

- `{value}`: the reading in fixed `tests.decimals` decimals (`Fn`, n = `tests.decimals`: `5.7`, `0.0`, `10.0`).
- `{cost}`: `CREDITS` of `tests.buyer_cost` (`1 credit`; at 0.5: `0.5 credits`).
- `{more}`, `{shown}`, `{mean}`, `{n}`, `{se}`: as the readings text of processes.md, "2.3 The readings text" writes
  them (`{shown}` is its `{values}`), on the buyer's side (sd = `tests.buyer_sd`), R being all the buyer's readings of this result in the order taken, this one included; the mean is SUM
  (README.md, "Number formats and rounding") / n, and plain addition can change its second decimal (readings 6.2,
  2.7, 6.8, 4.6 give `5.08`, not `5.07`).

Examples: `R-5b6 reading: 4.2 (charged 1 credit). Your readings [5.7, 4.2] mean 4.95 (n=2, ±1.41 standard error)`;
`R-95c reading: 6.4 (charged 1 credit). Your readings [..., 5.9, 7.4, 4.3, 7.5, 6.4] mean 6.20 (n=6, ±0.82 standard error)`.

The reading comes back only with the action's result (the next view, or the correction if the turn gets one); it
cannot inform another action of the same act call.

## 7. keep_delivery

On a plain contract, makes one of the delivered results the delivery in use (the one the contract is settled on).
Free. Priority class 0.

Arguments: `contract_id` (required), `result` (required).

Checks, in order:

1. The contract (1.7).
2. Escrow contract:
   ```
   {contract} is an escrow contract: an escrow contract is settled on its released result, so keep_delivery applies only to plain contracts
   ```
3. `missing argument 'result'` (checked after 2).
4. Nothing delivered:
   ```
   nothing has been delivered for {contract} yet: keep_delivery keeps a result delivered to you
   ```
5. The label (1.8) is not one of the delivered labels:
   ```
   {raw} was not delivered to you on {contract}. Delivered for {contract}: {labels}.
   ```
   (`{labels}` as in section 6, check 4.)
6. The label is already the kept one:
   ```
   you already kept {label} on {contract}: it is the delivery in use
   ```
   (Keeping the latest delivery when nothing is kept is allowed: it locks it in.)

No check on payment or deadline: allowed at any time until the market ends, as often as wanted.

Effects: the label becomes the kept delivery, which is the delivery in use; later deliveries do not replace it
unless the buyer keeps another. `{d}` = the day that label was delivered (each label is delivered once on a plain
contract). Notification to the manager:

```
{buyer} kept {label} on {contract} (delivered day {d})
```

Success:

```
kept {label} on {contract} (delivered day {d}): it is the delivery in use, and later deliveries do not replace it unless you keep another
```

Real: `kept R-ee0 on contract-4 (delivered day 3): it is the delivery in use, and later deliveries do not replace it unless you keep another`.

Clash rule (rounds.md, "6.3 The clash rules", B_keep): a keep_delivery on a contract runs before its manager's
deliver on it in the same round, so the delivery's notices name the kept label. A cycle with the participants' own
orders drops B_keep when it closes against A1-A9, which are stronger (rounds.md, "6.5 The order the phase's actions
run in"): the delivery then runs first and its notices, coming in the order the actions ran, do not name the label
kept in that round, while the end state is the same (e.g. the manager [`deliver` X, `deliver` Y], the buyer
[`end_contract` Y, `keep_delivery` X]). Against A10 B_keep holds.

## 8. approve_submission

Approves the submission under review on an escrow contract: the escrow releases. Free. Priority class 2.

Arguments: `contract_id` (required).

Checks, in order (shared with reject_submission, section 9; `{action}` is `approve_submission` or
`reject_submission`):

1. The contract (1.7).
2. Plain contract:
   ```
   {contract} is a plain contract: {action} applies to escrow contracts; a plain contract's delivery is not reviewed
   ```
3. Ended by the buyer:
   ```
   you ended {contract} on day {d}
   ```
4. Nothing under review:
   ```
   nothing is under your review on {contract}: {why}
   ```
   `{why}`, the first that applies: `its escrow was released on {label}` | `its escrow closed unreleased` |
   `its latest submission, {label}, was rejected` | `nothing has been submitted`.
   Real: `nothing is under your review on contract-4: its latest submission, R-713, was rejected`.
5. (R1, defensive: in a round's action phase, the submission now under review is not the one under review on the
   state the buyer's turn saw (rounds.md, "6.6 Running one action: the state its turn saw (R1)"). Under the market's
   rules this cannot happen within a round; if it does:)
   ```
   {label} is under your review on {contract} now, not the submission your turn saw: nothing was decided
   ```

Effects:
- The submission is approved (decided today). The escrow releases on it, with lateness counted at the SUBMISSION's
  day: `to_manager = round(price × factor, 4)` with `factor` the release factor of processes.md, "6.8 The release and
  the pay cut", at the submission day; `refund = price − to_manager` (not rounded). The escrow holds nothing more;
  the contract is settled (released on that label).
- The manager is credited `to_manager` (if above 0) at the round's END (R2; booked for it as `escrow releases`; if it
  was out of business and its balance is then above 0, it is back in business: rounds.md, "9.1 The round's credits
  land").
- The buyer gets `refund` (if above 0) back at once (booked as `escrow refunds`).
- The result is the buyer's from today (its receive day for value: episode.md, "7. Settlement: the value each contract
  realises"), and the task counts as done for the market's close (episode.md, "5. The end of a day: the close rule").

Notification to the manager:

```
{buyer} approved {label} on {contract}: you received {paid}; {contract} is closed.
```

`{paid}` = `{to_manager} of the price {price}` with `{to_manager}` in `F2` and `{price}` in `G`, followed by
` (submitted {k} day late)` (k = 1) or ` (submitted {k} days late)` (k ≥ 2) when
`k = max(0, submission day − the contract's deadline)` is above 0. Real:
`B-d75 approved R-95c on contract-13: you received 28.00 of the price 28; contract-13 is closed.`

Success:

```
approved {label} on {contract}: {to_manager} of the price {price} went to {manager}{back}; {label} is yours and {contract} is closed
```

`{to_manager}` in `F2`, `{price}` in `G`; `{back}` = `, and {refund} came back to you` (`F2`) when refund > 1e-9, else
empty. Real: `approved R-08c on contract-7: 8.00 of the price 10 went to M-ee6, and 2.00 came back to you; R-08c is yours and contract-7 is closed`.

Clash rules (rounds.md, "6.3 The clash rules"): an approval runs before its manager's delegate_run, run_myself (A6)
and test_result (A7) on the contract in the same round; those then fail uncharged. A cycle with the participants' own
orders drops A6 or A7 only when it closes against A1-A3 (rule iii), which are stronger (rounds.md, "6.5 The order the
phase's actions run in"): the run or test is then taken and charged before the approval (e.g. the buyer
[`end_contract` C', `approve_submission` C], the manager [`test_result` C, `deliver` C']). Against A10 they hold: the
buyer [`accept_bid` of the manager's bid on another post, `approve_submission` C], the manager [`run_myself` C] run
the acceptance, the approval, then the run, which fails uncharged.

## 9. reject_submission

Rejects the submission under review on an escrow contract: the result goes back to the manager, the price stays
held. Free. Priority class 2.

Arguments: `contract_id` (required), `message` (optional).

Checks: those of section 8 (with `{action}` = `reject_submission`). The message has no check: its value, if given, is
converted with `TEXT` (a value that is `false`, `0`, `0.0`, `-0.0` or an empty list or object counts as no message), and silently cut
to its first `contracts.message_chars` characters. It is not trimmed: whitespace alone is a
message.

Effects:
- The submission is rejected (decided today), with its message. The result goes back to the manager and is not the
  buyer's while the rejection stands; the price stays held. (A dispute and the arbiter's ruling: processes.md, "6.6
  Dispute and the arbiter's ruling" and "6.7 The arbiter's fee, and when a released result is the buyer's"; section
  10.) The manager may dispute the rejection through day
  `until = today + contracts.escrow.dispute_window_days` (actions-manager.md, "7. dispute"), and may submit another
  result (which ends the chance to dispute this rejection).

Notification to the manager:

```
{buyer} rejected {label} on {contract} (submission #{n}): it is returned to you and cannot be submitted again.{said} You can dispute the rejection through day {until} (a deposit of {deposit}), or submit another result.
```

`{n}` = the submission's number on the contract (counting every delivery or submission on it); `{said}` =
` Message: "{ONE_LINE(message)}".` when there is a message (on one line: view-contracts.md, "2.3 Shared texts"), else
empty (the record keeps the message as sent); `{deposit}` = `CR` of
`round(contracts.escrow.dispute_fee_rate × price, 2)`.

Success:

```
rejected {label} on {contract}: it went back to {manager} and is not yours; the price stays held. {manager} can dispute the rejection through day {until}
```

Real: `rejected R-7c1 on contract-8: it went back to M-3c5 and is not yours; the price stays held. M-3c5 can dispute the rejection through day 8`.
(Views show the message: view-contracts.md, "7.3 The result line" for the manager and "8.3 Submission lines
(escrow)" for the buyer.)

## 10. pay_escrow_arbiter

Pays the arbiter's fee owed after a dispute the buyer lost (the arbiter ruled for the manager: processes.md, "6.6
Dispute and the arbiter's ruling"); the released result becomes the buyer's. Priority class 0.

Arguments: `contract_id` (required).

The fee owed is the dispute fee `round(contracts.escrow.dispute_fee_rate × price, 2)`, owed from the ruling for the
manager until paid. (`contracts.escrow.dispute_fee_rate` is above 0, but on a small price the fee can round to 0:
then nothing is owed and the result was the buyer's at the ruling.)

Checks, in order:

1. The contract (1.7).
2. Nothing owed (no ruling for the manager on this contract, or the fee already paid; also any plain contract):
   ```
   you owe the arbiter nothing on {contract}: pay_escrow_arbiter applies after a dispute that went against you, until you pay
   ```
3. The balance check (1.12): `{what}` = `paying the arbiter's fee on {contract}`, cost the fee:
   e.g. `insufficient balance for paying the arbiter's fee on contract-1: it costs 10.55, you have 3.00`.

Effects: the fee is charged at once (it leaves the market; booked as `arbiter's fees paid`); the released result is
the buyer's from today (its receive day for value). No notification (the manager's view simply stops showing the
fee as owed).

Success:

```
paid the arbiter's fee of {fee} on {contract}: {label} is now yours
```

`{fee}` in `CR`, `{label}` the released result. Real: `paid the arbiter's fee of 11 on contract-14: R-b8b is now yours`,
`paid the arbiter's fee of 5 on contract-18-2: R-8b6 is now yours`.

## 11. end_contract

Ends one of the buyer's contracts that has stalled; its task goes back to the buyer's queue. Free. Priority class 2.

Arguments: `contract_id` (required).

Let `dl` = the contract's deadline (section 5), `due` = its task's due day, `close` = `due + escrow_close_days` (the
day its escrow closes, processes.md, "6.9 The lateness close"), `today` the current day.

Checks, in order:

1. The contract (1.7).
2. Already ended:
   ```
   you already ended {contract} on day {d}
   ```
3. Escrow contract, in this order (`{rule_e}` is the text after this list):
   - released:
     ```
     the escrow of {contract} was released on {label}: the contract is settled and cannot be ended
     ```
   - closed unreleased (its price refunded at its close):
     ```
     the escrow of {contract} already closed unreleased and refunded you: it cannot be ended
     ```
   - a submission under the buyer's review:
     ```
     {label} is under your review on {contract}: approve it or reject it first (approve_submission, reject_submission) ({rule_e})
     ```
   - a rejection in dispute (`{r}` = the ruling day):
     ```
     your rejection of {label} on {contract} is in dispute, and the arbiter rules at the start of day {r} ({rule_e})
     ```
   - the latest submission was rejected, not disputed, and `today <= until` (its dispute window, section 9):
     ```
     {manager} can dispute your rejection of {label} through day {until}: if it does not, you can end {contract} from day {until_plus_1} ({rule_e})
     ```
     (`{until_plus_1}` = until + 1.)
   - the latest submission was rejected and the rejection stands (its window passed undisputed, or the arbiter ruled
     for the buyer): ALLOWED, reason "rejected" (on any day).
   - `dl + 1 >= close` (the deadline is the last day before the escrow closes; at the default values: signed on or
     after the due day):
     ```
     {contract}'s deadline day {dl} is the last day before its escrow closes (day {close}): it can be ended only after a rejection that stands
     ```
   - `today <= dl`:
     ```
     the deadline day of {contract} is day {dl}: {rule_e} (from day {dl_plus_1})
     ```
   - otherwise ALLOWED, reason "overdue" (in practice nothing was ever submitted).

   `{rule_e}`:
   ```
   an escrow contract can be ended after a rejection that stands (its manager can no longer dispute it, or the arbiter ruled for you), or after its deadline day with nothing released if it was signed on or before that day; never while a submission is under your review or a rejection is in dispute
   ```
4. Plain contract, in this order (`{rule_p}` after the list):
   - it has a delivery:
     ```
     {contract} has a delivery: {rule_p}
     ```
   - signed after its deadline (`signed_day > dl`; possible when signed more than `escrow_close_days − 1` days after
     the due day; such a contract can never be ended):
     ```
     {contract} was signed on day {s}, after its deadline day {dl}: {rule_p}
     ```
   - `today <= dl`:
     ```
     the deadline day of {contract} is day {dl}: {rule_p} (from day {dl_plus_1})
     ```
   - otherwise ALLOWED, reason "overdue".

   `{rule_p}`:
   ```
   a plain contract can be ended only after its deadline day with nothing delivered, if it was signed on or before that day
   ```

Real: `the deadline day of contract-10 is day 13: a plain contract can be ended only after its deadline day with nothing delivered, if it was signed on or before that day (from day 14)`;
`contract-30 has a delivery: a plain contract can be ended only after its deadline day with nothing delivered, if it was signed on or before that day`;
`M-569 can dispute your rejection of R-eb6 through day 16: if it does not, you can end contract-18 from day 17 (an escrow contract can be ended after a rejection that stands (its manager can no longer dispute it, or the arbiter ruled for you), or after its deadline day with nothing released if it was signed on or before that day; never while a submission is under your review or a rejection is in dispute)`.

Effects, in this order:
1. The contract is ended (its end day and round recorded).
2. The task's state becomes queued again; it has no current post or contract; the ended contract is added to the
   task's ended contracts (with k the number of contracts on the task its buyer has now ended, its next post is
   `post-N-(k+1)` and its next contract `contract-N-(k+1)`). It keeps its
   worth and due day.
3. Escrow: the whole held price goes back to the buyer at once (booked as `escrow refunds`); the setup fee is not
   refunded. Plain: what the buyer paid on it with pay stays with the manager.
4. Runs still going on the contract continue, stay charged and arrive normally, but nothing more can be run, tested
   or delivered on it (processes.md, "1.4 Arrival"; actions-manager.md, "1.2 The contract's state checks"). An
   ended contract realises nothing at settlement.
5. Notification to the manager:
   ```
   {buyer} ended {contract} ({why}). {money} {contract} takes no more runs, tests or deliveries; runs still going on it finish and stay charged, but their results cannot be delivered.
   ```
   - `{why}` = `its rejection of {label} stands` (reason rejected; `{label}` the latest submission) |
     `its deadline day {dl} passed with nothing released` (escrow, overdue) |
     `its deadline day {dl} passed with nothing delivered` (plain, overdue).
   - `{money}`, escrow: `Its price {price} went back to {buyer}; the fees paid on it are not refunded.` (`{price}` in
     `G`); plain: `Nothing more is owed on it.`, followed, when the contract's `paid` is above 1e-9, by
     ` The {paid} credits {buyer} paid on it before ending it are not returned.` (`{paid}` = `G` of `paid`).
     `paid` is the contract's `paid` of processes.md, "5.2 What is owed" (toward the price only, never a payment's
     part beyond it).

Success:

```
ended {contract} with {manager} ({why}){back}. {task} is back in your queue, due day {due} ({rel_due}), and can be posted again
```

`{back}`: escrow `; its price {refund} came back to you (the fees are not refunded)` (`{refund}` = the price, `G`);
plain with its `paid` above 1e-9 `; the {paid} credits you paid on it are not returned` (`{paid}` as in the
notification: `G` of `paid`, toward the price only); else empty.
`{rel_due}` = `{rel(due)}` (1.13). Real:
`ended contract-9 with M-14b (its deadline day 16 passed with nothing released); its price 22 came back to you (the fees are not refunded). task-9 is back in your queue, due day 15 (2 days ago), and can be posted again`;
`ended contract-37 with M-218 (its deadline day 23 passed with nothing delivered); the 8 credits you paid on it are not returned. task-37 is back in your queue, due day 23 (1 day ago), and can be posted again`.

Clash rules (rounds.md, "6.3 The clash rules"): a manager's deliver on the contract in the same round runs before
the end (A1 plain, A2/A3 escrow), so the delivery or submission counts and the end then fails with its normal
refusal; the end runs before the manager's delegate_run, run_myself (A4) and test_result (A5) on the contract, which
then fail uncharged. A cycle between the participants' own orders and these rules drops the weakest clash that
closes it, by the prompts' priority (rounds.md, "6.5 The order the phase's actions run in", which lists the cases):
A1-A3 (rule iii) never give way to another rule, so in a cycle the delivery or submission still counts and the end
fails; A4 and A5 (rule iv) give way only to A1-A3, so the run or test is then taken and charged before the end.

## 12. Priority classes

| class | actions (both roles) |
|---|---|
| 0 | post_task, test_delivery, keep_delivery, pay_escrow_arbiter, pay, send_message, and every action not listed below |
| 1 | deliver |
| 2 | approve_submission, reject_submission, end_contract |
| 3 | delegate_run, run_myself, test_result, dispute |
| 4 | accept_bid, withdraw_post |
| 5 | withdraw_bid |
| 6 | bid |
| 7 | edit_post |

How classes, the clash rules and the agents' seeded order combine: rounds.md, "6.4 The fixed order of kinds" and
"6.5 The order the phase's actions run in".
