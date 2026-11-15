# Test vectors

Acceptance data for the parts of the spec whose rules are easiest to get subtly wrong. Each file is JSON Lines: one
case per line, an input and the reference implementation's exact observable outcome for it. They are not
implementation instructions: the spec is the requirement, and every field below is defined by the spec section it
cites. An implementation passes a file when it gives every line's expected outcome from that line's input. No
case holds an input that README.md, "Number formats and rounding", puts outside the spec.

## act_reading.jsonl: reading the reply that ends a turn

1,645 cases of reading a reply into actions and a memory: turns.md, "7. Reading the reply that ends the chain"
(7.1 to 7.6), "8. The action cap and the notes that open the results", "9. Result lines", "10. The memory" and the
unreadable correction of "11.6 Reading the correction", with the name checks of actions-buyer.md, "1.2 Matching the
name, and the checks before the action's own" and the result line of "1.3 The result line". Parameters it depends on:
`turns.max_actions` = 6 and `turns.memory_chars` = 2000. The argument checks of actions-buyer.md, 1.4 to 1.11 (what
counts as given, the one name each argument is read under, ids, labels, participants, amounts, thresholds) give texts
that depend on the market's state; this file records each action's arguments as read, and round_order.jsonl checks
those readings through the actions' outcomes (its `arguments` cases).

### Conventions of this file

- **Encoding.** UTF-8; every non-ASCII character is written as a `\uXXXX` escape, so the file is plain ASCII.
- **Numbers.** As in the spec's reading of JSON (turns.md, "7.2 Reading JSON", and README.md, "Number formats and
  rounding"): a number written without a fraction or exponent is an integer, exact at any size (one line holds a
  4,300-digit integer); any other number is a double. So `1`, `1.0` and `true` are three different values, and
  `-0.0` is negative zero. The values NaN, infinity and minus infinity appear as the bare tokens `NaN`, `Infinity`
  and `-Infinity`, which standard JSON does not allow; read the file with a JSON reader that accepts them (the
  spec's own reading does).
- **Unpaired surrogates.** A text may hold an unpaired surrogate code point (turns.md, 7.2), written as a `\uXXXX`
  escape (`\ud800`). Read the file with a reader that keeps it as one code point rather than refusing or replacing it.
  (Python's `json` module, for one, reads the whole file as written: exact integers, `NaN` and the infinities, lone
  surrogates.)
- **Texts inside `calls`.** A call's `arguments` given as a string is the raw text the model sent, before any
  reading: the file's JSON encoding is undone once (one level of `\"` and `\\` escapes), and what remains is the text
  your reading of 7.2 and 7.3 receives.

### What the cases cover

The cases were chosen for coverage, not volume:

- `constructed/...` (1,483 cases), written to reach every rule of turns.md 7 to 10:
  - the reading of an act call's arguments (7.2, 7.3): standard JSON with JSON whitespace around it; the one repair,
    closing brackets, for each closer that reads (`}`, `]}`, `}]}`; `]` never completes an object, and a text it would
    complete as a list stays unreadable), after removing surrounding whitespace (Unicode whitespace included, so a
    text padded with U+3000 or U+001C and one bracket short reads, while the same text complete does not), and never
    for a cut reply; arguments missing, null or `""` (an empty act); arguments a provider returned already decoded,
    as an object or as anything else; `actions` absent, null, a list, or anything else (a string holding JSON, an
    object, a number, a boolean); `memory` as the only source of the memory, kept untrimmed, with blank, non-string
    and repeated values; several act calls (their lists joined, the last call's memory; one unreadable call making
    the reply unreadable);
  - texts no reading can read (7.2): trailing commas, `None`/`True`/`False`, `"""` strings, single quotes, several
    values, text or backticks around the object, comments, control characters, a BOM, unpaired surrogates kept,
    nesting up to 385 levels, odd values (`-0.0`, NaN, the infinities, repeated keys, a 30-digit and a 4,300-digit
    integer);
  - every item rule of 7.4 (I1 to I5): items that are not objects; names from `action`, else `name`, untrimmed, with
    blank, null and non-string values (and which of the two keys stays an argument); nested `args`, `arguments`,
    `parameters` and `params`, in order, without overriding; dropped items; act items replaced by their actions,
    recursively, their `memory` and `summary` ignored; and every shape an earlier reading tolerated (an `actions`,
    `tool` or `_raw` key naming the item, an action's name as a key, a bare string such as `"bid"` or `"end_turn"`,
    `parameter`, `refine` written as `None`, `summary_updated`, end markers such as `{"end_turn": true}` or
    `{"type": "end_turn"}`, memory items, `update_memory` and `update_summary` actions): each now gives the ordinary
    outcome the rules above give it (a nameless action, an unknown action, or an ignored argument), and the file keeps
    them as tests of exactly that;
  - names (7.6 and actions-buyer.md 1.2): every action of both roles exactly, capitalised and padded (some in capitals);
    the old names (`approve`, `reject`, `pay_arbiter`, `delegate`); `update_memory`, `update_summary` and `end_turn`;
    the names not available in this market; the other role's actions; the unknown-action additions for names that
    contain `contract` or (for a manager) start with `submit` once trimmed and lowercased; `compute` as an action;
    `send_message` as a key and as a name, for both roles in all five `message_channels` modes;
  - replies with compute calls beside act (only calls named exactly `compute` count), other calls only, or no call;
    cut replies; corrections with no act call (unreadable, 11.6); the cap at 6 and at each correction limit (1, 2, 3,
    6), with dropped items that do not count; and the memory rules of 10 (cut at 2000 characters, counted in code
    points; no memory; a correction's memory).

  The part after the slash names the group: `item`, `itemcut`, `itemtext`, `names`, `send_message`, `top`, `text`,
  `decoded`, `calls`, `cap`, `memory`, `update_memory`, `combo`, `deep` and `correction-no-act` hold the shapes the
  file had before the strict reading (each now read under the current rules), `strict-item`, `strict-text`,
  `strict-calls` and `strict-names` the strict reading's own rules.
- `fuzz` and `fuzz-text` (29 cases): randomly generated replies and character-level edits of act texts, each kept
  only because it reached a line of the reference implementation's reading, or an outcome, that no case before it
  reached.
- `log` (133 cases): act calls and corrections that models actually sent in the reference implementation's runs
  (Claude Haiku, modes `all` and `managers`), sampled across outcome shapes, with the memory the participant had at
  the time. Those runs used an earlier, more tolerant reading; on all 1,689 act-ending replies and corrections of
  those runs, the current reading gives the same actions, with the same names and arguments, as the actions the runs
  executed then.

### Fields

Each line is `{"id", "source", "input", "expected"}`. `id` is `act-0001` to `act-1645`; `source` is the origin above.

`input`:

| field | meaning |
|---|---|
| `role` | `buyer` or `manager`: the participant whose reply this is |
| `message_channels` | the mode (`off`, `managers`, `buyers`, `within_roles`, `all`). It decides whether `send_message` is one of the participant's actions and the refusals' texts (actions-buyer.md, 1.1 and 1.2) |
| `calls` | the reply's function calls, in order. Each has `name`, the function's name exactly as sent (only a call named exactly `act` is an act call, 7.1), and `arguments` as the provider returned it: a string is the text exactly as the model sent it; any other JSON value (an object, a list, a number, a boolean, `null`) means the provider returned the arguments already decoded (7.3 step 1); the key is absent when the call had no arguments. `[]` is a reply with no function call |
| `cut` | `true` if the reply ended because it hit its token limit (finish reason `length`; turns.md, 7: "cut") |
| `correction` | `null` for the reply that ended the decide chain; `{"limit": n}` for the reply to a correction call whose limit is n (turns.md, "11.2 Its limit and its note"; 11.6) |
| `memory_before` | the participant's memory before this turn, or `null` if it has none yet |

`expected`, when the reply cannot be read:

| field | meaning |
|---|---|
| `format_error` | the exact format-error text of 7.1, with its length-limit suffix when cut: the `{err}` of turns.md, 11.3 and 12 for a decide reply, the `{err2}` of 11.6 for a correction (a correction with no act call is unreadable too). Nothing else is given: none of the reply's actions runs and it carries no memory (10.1) |

`expected`, when the reply is read (`format_error` is `null`):

| field | meaning |
|---|---|
| `notes` | the lines that open the reply's results, in order (turns.md, 8): the compute-beside-act note and the cap note |
| `actions` | the actions kept after 7.4 and the cap (8), in the order they run |
| `capped` | how many actions the cap dropped (8): the count after 7.4 minus the count kept |
| `memory` | the memory outcome, below |

Each item of `actions`:

| field | meaning |
|---|---|
| `name` | the action's name (7.4, I2): the value of `action` or `name` exactly as sent, untrimmed and with its case kept (`" Bid "`); the market runs it under exactly this name (7.6). `null` for an action with no name (I1: an item that is not an object; I2: no `action` or `name` that is a string with non-whitespace content) |
| `shown` | the `{shown}` of its result line (turns.md, 9): `name` with surrounding whitespace removed, its case kept; `?` with no name |
| `arguments` | the action's arguments as read (7.4: its keys other than the one that gave its name, after I3), each value as decoded (7.2). Compare it as a mapping from argument name to value: values by type and value (`1`, `1.0` and `true` differ; NaN equals NaN; `-0.0` differs from `0.0`); the order of the keys is not checked. **Leave out the key `tool`**: by 7.4 an item's `tool` is an argument like any other, which no action reads, but the reference implementation holds the action's name under that key internally, so its record of the arguments never shows the item's own `tool`; nothing an agent sees depends on it. `null` for an action with no name: it fails with one fixed text whatever it holds (7.6) |
| `result` | the action's exact result line (turns.md, 9, before any clash note of rounds.md), given only where it cannot depend on the market's state: an action with no name (actions-buyer.md, 1.2 step 2), a name refused by steps 3 to 5 (not available in this market, unknown, the other role's action; `update_memory`, `update_summary`, `end_turn`, the old names and any name in capitals or with surrounding whitespace are unknown) and `compute` (7.6), for a participant in business. `null` for every other action: it reaches the checks of 1.2 steps 6 to 8, whose outcome depends on the market |

`memory` (turns.md, 10), for this scenario: the participant is in business at the turn's end. For a decide reply,
no correction follows, or one follows that is read and carries no memory (10.3 then writes the decide reply's
memory just as when none follows). For a correction, the decide reply it corrects carried no memory, so what 10.3
and 10.4 decide depends on this reply alone.

| field | meaning |
|---|---|
| `reply_memory` | the reply's memory (10.1): the `memory` of its last act call (7.3), exactly as sent and not yet cut; `null` for none |
| `written` | whether the participant's memory was written at the turn's end (10.2, 10.3) |
| `after` | the participant's memory at the turn's end, cut to `turns.memory_chars` characters as 10.2 writes it; `memory_before` when nothing was written; `null` if there was none and nothing was written |
| `note` | the memory line appended at the very end of the turn's results (10.4), or `null` |

### How an implementation can check itself

For each line, give the `input` to your build's reading of a reply and compare:

1. the format error, as exact text, or that there is none;
2. the actions your reading keeps, in order: for each, its name, the name its result line shows, and its arguments
   (as a mapping, without `tool`, compared as above);
3. for each action whose `result` is not `null`: the result line your market gives when the participant runs that
   action in business, exactly;
4. the notes, exactly, and the number of actions the cap dropped;
5. the memory: the reply's memory, then, completing the turn in the scenario above (no correction, or one read with
   no memory, after a decide reply; a decide reply with no memory before a correction): whether a memory was written,
   the memory at the turn's end, and the memory line of the results.

One way is a test harness that calls your reading code and your turn-end code directly. Another, closer to the real
path, is a scripted test model whose reply is `calls` (with finish reason `length` when `cut`), on a market whose
participant has `memory_before` as its memory; for a correction case, the decide reply must have `limit` failed
actions and no memory. Either way the comparison is of what the spec makes observable, so internal forms (how an
item or a name is held) are yours to choose.

### How the expected outcomes were made

Every `expected` was produced by running the reference implementation's own code, configured as the spec's default
market, on the `input`: its reading of the reply, its cap, its name checks (an action's `result` is taken only where
the market's state cannot change it) and its turn-end memory step. Re-running the reference implementation on every
line's input reproduces the file byte for byte, in either order of the lines. Independently, a reading written from
this spec alone (turns.md 7 to 10 and actions-buyer.md 1.1 to 1.3) gives every line's `expected`, field by field,
with `arguments` compared as above.

## round_order.jsonl: ordering a round's actions

700 action phases, each with the order its actions are tried in, its clashes, and what each action did: rounds.md,
"6. The action phase" (6.1 to 6.8), "7. Money from others lands at the round's end (R2)", "8.1 Who gets one" to
"8.3 The news and the facts", and the correction phase of "8.6 Reading the corrections and the correction phase";
the priority classes of actions-buyer.md, "12. Priority classes", and actions-manager.md, "9. Priority classes and
clash rules of these actions"; and, through each action's outcome, the checks and texts of every action in those
two files, the argument readings of actions-buyer.md, 1.4 to 1.11 and actions-manager.md, 1.1 among them.
Parameters it depends on: the default configuration throughout (config.md), with `message_channels` given per case
(`all` or `managers`).

### Conventions of this file

- **Encoding.** UTF-8; every non-ASCII character is written as a `\uXXXX` escape, so the file is plain ASCII.
- **Numbers.** As in the spec's reading of JSON (turns.md, "7.2 Reading JSON"): a number written without a fraction
  or exponent is an integer, any other number a double, so an argument `15` and an argument `15.0` are different
  values (a bid at `15` and one at `15.0` both have the price 15.0). Amounts in `market` (balances, prices, paid
  amounts, readings) are doubles written as the shortest decimal that reads back as the same double (`REPR`). The
  file holds no NaN or infinity and no unpaired surrogate, so any standard JSON reader reads it (an argument may be a
  310-digit integer, which actions-buyer.md, 1.10 reads as an amount too large for a finite double).
- **Ids.** Participant ids, task, post, bid and contract ids and result labels are the ones the reference
  implementation drew or made in the run the market state comes from (randomness.md: yours differ); a case is
  checked with its own ids, set up as given.

### What the cases cover

Every case starts from a market state the reference implementation reached in its runs (Claude Haiku, modes `all`
and `managers`, under an earlier version of the rules; the states are valid states of the current market). The
synthetic cases put new plans on such a state (a few first change one fact of it: a manager's balance, a contract's
deadline day, a result's arrival, a bid's manager, which the case's `market` then shows); a synthetic correction
phase follows a synthetic first phase on such a state. `source` says where a case comes from:

| source | cases (of them correction phases) | what it is |
|---|---|---|
| `log` | 140 (23) | phases of those runs with the plans the models sent (the current reading reads those replies into the same plans: act_reading.jsonl, `log`), run on the current rules: every logged phase that held a clash, a failed action or a correction phase, and a sample of the rest. Their run order is the one those runs logged; their texts follow the current rules |
| `fuzz` | 133 (28) | random plans aimed at a few contracts, posts and bids: every kind, the other role's actions, old names, odd names (none, unknown, untrimmed, capitals, a non-string `action` left as an argument), id spellings (bare numbers, upper case, spaces, another kind's prefix, unknown ids, empty and null), and argument names the actions do not read |
| `examples-6.5` | 118 (9) | the cycles listed in rounds.md, 6.5, on real contracts and posts, in the round's turn order or a shuffled one: a run or test before a delivery against an end (A4, A5 against A1); a bid or withdrawal and a run, test or dispute against an acceptance (A10 against A8, A9); the end / acceptance / withdrawal / delivery cycle (A9 against A1); a withdrawal of the post or an acceptance of another bid against an end (B_withdraw, B_accept against A1); an approval against an end (A6, A7 against A1) or an acceptance (A10 against A6); keep_delivery against an end (B_keep against A1) or an acceptance (A10 against B_keep) |
| `replaced-bid` | 36 (1) | R14: buyer [`end_contract` X, `accept_bid` b], b's manager [`bid` on b's post, `deliver` X]: the replacing bid runs first (A8 gives way to A1), so the acceptance fails with R14's refusal, or succeeds when the new bid kept b's price and threshold; some with the buyer's two actions the other way round (no cycle; the bid loses A8) |
| `cycle-candidates` | 23 (6) | rounds.md, 6.5 step 2's candidates: buyer [`end_contract` X, `accept_bid` b2], X's manager [a test or run on X, `deliver` X], and another manager's `withdraw_bid` or `bid` on b2's post, ready but only waiting behind the cycle: not a candidate, so A9, A8 or B_accept holds and the test or run gives way |
| `not-own` | 37 (9) | actions a participant does not have (rounds.md, 6.2 to 6.4): the other role's actions, `send_message` for a role that cannot send, and unknown names (capitals, spaces, old names), aimed at targets where the participant's own action of that name would clash: kind 0, no targets, no clash |
| `arguments` | 66 (14) | the argument readings of actions-buyer.md, 1.4 to 1.11: each action varies one argument (an id spelled exactly, by its number, or in a spelling that names nothing; a label; a participant id; an amount; a threshold; a form; `refine`; or another name for the argument) on real targets, its other arguments written as the prompts state them |
| `example-8.6` | 11 (7) | rounds.md, 8.6's example (buyer [`bid` on P, `accept_bid` b], b's manager [`withdraw_bid` b, `withdraw_post` P]), in first and correction phases |
| `end-race` | 66 (2) | a delivery or submission against an end the buyer could make (A1, A2, A3 held), with a run or test alone against the end (A4, A5 lost), and a later buyer action that needed the end (the "needed" note) |
| `out-of-business` | 32 (0) | a manager whose own test takes its balance to exactly 0 mid-phase: its later actions stopped, its bids' acceptances lost by A10 with no winner |
| `out-by-tokens` | 27 (0) | a manager out of business by this round's token charges (in business at the round's start): its first action refused, the rest stopped; acceptances of its bids, the open-bids fact, a post closed earlier this round or on an earlier day (by an acceptance or by the buyer's own withdrawal, said in the buyer's own words) |
| `new-ids` | 11 (11) | correction phases that name posts and bids other participants made this round (hidden, unless shown in the facts) |

Over the file: 4,377 actions and 1,210 clash records; every rule held, and every rule but A1, A2 and A3 (the
strongest, which never give way: rounds.md, 6.5) also dropped in a cycle:

| rule | A1 | A2 | A3 | A4 | A5 | A6 | A7 | B_keep | A8 | B_accept | A9 | B_withdraw | A10 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| held | 196 | 80 | 38 | 57 | 66 | 31 | 26 | 38 | 57 | 82 | 30 | 53 | 137 |
| dropped | 0 | 0 | 0 | 72 | 77 | 14 | 9 | 27 | 31 | 7 | 12 | 7 | 63 |

Also: 254 phases with a cycle; ties between participants decided by the turn order in every kind value 0 to 7;
2,526 actions refused on the state the turn saw, 1,760 run on the market (302 of them failing there), 91 stopped
(in 52 phases); 25 acceptances refused by R14 and 6 that succeed after a replacing bid on the same terms; 368 actions
that are not their participant's own (231 of them under a name of the other role or of `send_message`); a lost
clash of every rule but B_keep (whose loser, a delivery, never fails for it), with its note; 29 phases with a
"needed" note and 30 with an "it was possible on the market" note; each kind of fact line, the closed-post facts in
both the post's own buyer's words and others'; 21 correction phases with hidden ids; 108 phases with credits
deferred to the round's end; 129 phases with an empty plan. Every argument-reading text of actions-buyer.md, 1.6 to
1.11 appears (unknown task, post, bid and contract ids, unknown results, unknown participants, amounts that are not
numbers or not positive, thresholds out of range or not whole, forms, `you cannot send to`), as does
`missing argument` for actions given only another name for an argument.

### Fields

Each line is `{"id", "source", "input", "expected"}`. `id` is `ro-001` to `ro-700`.

`input`:

| field | meaning |
|---|---|
| `message_channels` | the mode |
| `day`, `round` | the round the phase is in |
| `phase` | `first` (rounds.md, 6) or `correction` (8.6) |
| `turn_order` | the round's turn order (rounds.md, 1): every participant, out-of-business ones included; rank = index |
| `plans` | the plans of the phase (rounds.md, 1, "Plan"): `{participant: [action, ...]}`, one entry per participant with a plan (an empty list is an empty plan; a participant with no plan in this phase is absent). Each action is `{"name", "arguments"}` as reading the act leaves it after the cap (turns.md, 7.4 and 8): `name` is a string with non-whitespace content, exactly as sent, or `null` for an action with no name; `arguments` is an object. An action's position is its index in its list, 0 first |
| `market` | the market as the phase starts (below) |
| `hidden` | correction phases only: for each participant with a plan, the ids removed from its shadow (rounds.md, 8.6): `posts` and `bids` |

`market`. "The phase's start state" is the state the shadows are made from (rounds.md, 6.6): for a first phase the
round-start state (3), for a correction phase the market right after the first phase (8.6). It differs from the
market as the phase starts only in the token charges made between them, shown under `start_state`. So, as 6.6 builds
the shadows: a participant's own shadow has its own `balance` and `out_of_business`, and every other participant
appears on it with its `start_state` values where given (else its `balance` and `out_of_business`); the market the
actions run on has every participant's `balance` and `out_of_business`.

| field | meaning |
|---|---|
| `participants` | every participant: `role`; `balance` and `out_of_business` (episode.md, "9. Out of business") as the phase starts; `start_state: {balance, out_of_business}` only where the phase's start state differs (this round's token charges). For each participant with a plan, the lists the unknown-id hints print (actions-buyer.md, 1.6 step 3), in creation order: `contracts`, `[contract, task, ended]` for every contract it is the buyer or the manager of; a manager's `open_bids`, `[bid, post]`, and `runs_in_progress`, `{result, contract, worker, arrival_day}` with `worker` `employee` (delegate_run) or `manager` (run_myself); a buyer's `queued_tasks` and `open_bids_on_posts`, `[bid, manager, price, threshold, post]` for every open bid on its posts (the hint then leaves out sealed bids and bids of managers out of business) |
| `bids_created` | the number of bids ever created: the phase's first new bid is `bid-{bids_created + 1}` (actions-manager.md, "2. bid") |
| `open_posts` | every open post, in creation order (the post hint) |
| `tasks` | `{task: {buyer, state, due_day, current_post, current_contract}}`; `state` is `queued`, `posted`, `contracted` or `withdrawn`; the current post and contract are those that actions-buyer.md, "2. post_task", "5. accept_bid" and "11. end_contract" set (`null` for none) |
| `posts` | `{post: {task, buyer, posted, open, closed}}`; `posted` is `[day, round]`; `closed`, for a closed post only, is `{day, round, why}` with `why` `accepted` or `withdrawn` |
| `bids` | `{bid: {post, manager, status, price, threshold, placed}}` in creation order; `status` is `open`, `accepted`, `lost` or `withdrawn`; `threshold` is `null` for a plain bid; `placed` is `[day, round]` |
| `contracts` | `{contract: {...}}`: `task`, `post` (the post it was accepted on), `buyer`, `manager`, `form` (`plain` or `escrow`), `price`, `threshold` (`null` when plain), `signed` (`[day, round]`), `due_day` (due(c)) and `deadline_day` (dl(c)) as view-contracts.md defines them, `ended` (`[day, round]` if its buyer ended it, else `null`), `latest_submission_rejected` (rounds.md, 6.2: for `end_contract`'s targets), and `state`: for an escrow contract the state of processes.md, "6.1 States" (`HELD, NOTHING SUBMITTED` ... `ENDED`), for a plain one `NOTHING DELIVERED`, `DELIVERED` or `ENDED`. A plain contract adds `kept` (the result its buyer kept, or `null`), `paid` and `deliveries` (`{result, day, round}` in order); an escrow contract adds `submissions` (`{result, day, round, decision}`, `decision` being `under review`, `approved`, `released without a decision` or `rejected`, and `disputed: {day, ruling_day, winner}` for a disputed rejection, `winner` `null` until the ruling). `results` lists every result of the contract in start order: `result` (its label), `worker`, `kind` (`fresh` or `refine`), `refines` (the label refined, or `null`), `started` (day), `arrival_day`, `arrived`, and the readings so far, `manager_readings` and `buyer_readings`, as values |

Which objects `market` lists: every task, post, bid and contract whose number (the first number in its id) appears
as a run of decimal digits, in any script, in the value of an id argument of any planned action (`contract_id`,
`contract`, `post_id`, `post`, `bid_id`, `bid`, `task_id`, `task`, `result`, `result_id`, `label`, `refine`,
`parent`, including the names the actions do not read); every target the phase resolves; and, closed over these, a
listed bid's post, a listed post's task, its bids and the contract made from it, a listed contract's task and post,
and a listed task's current post and current contract. So any id an argument can name (actions-buyer.md, 1.6) is
listed if it exists, and an id that is not listed does not exist. Its result labels are those listed under
`results`.

Two things outside that rule also follow from what is listed, so a market built from `market` alone gives the same
texts:
- A `pay` that names no contract (actions-manager.md, "8. pay", step 5) finds, among the listed contracts, the same
  contract the reference implementation's full market gave it, or none when it gave none: the unlisted contracts
  play no part in any case.
- A contract that appears only in a manager's `runs_in_progress` (not under `contracts`) is not settled
  (view-contracts.md, 2.1; whether it is ended is in that manager's `contracts` list), so the capacity line of 8.3's
  balance fact gives its runs no `closed: its result cannot be used` mark.

`expected`:

| field | meaning |
|---|---|
| `order` | the run order (rounds.md, 6.5), as the log records it (6.8): `[participant, position, kind name]` for every planned action, stopped ones included. The kind name is rounds.md, 1's: the name exactly as sent, `""` for an action with no name |
| `clashes` | every clash record (rounds.md, 6.8), in 6.8's order: `rule`, `first` and `second` (each `[participant, position, kind name]`; `first` is the action that must run first), `held` (`false` when the edge was dropped in a cycle), and `first_status`, `second_status`: `OK`, `ERROR`, or `null` for an action that did not run (stopped) |
| `actions` | per participant, one entry per planned action in plan order: `outcome` is `ran` (it passed its shadow and ran on the market: 6.6 step 3), `refused` (it failed on its shadow: step 2) or `stopped` (6.7); `status` is `OK`, `ERROR` or `null` (stopped); `line` is its result line before any note (turns.md, 9), or `null` (stopped). `drawn: true` marks an `OK` line of `delegate_run`, `run_myself`, `test_result` or `test_delivery`: it holds values the reference implementation drew (a new result's label, arrival day and cost; a reading and the readings that follow it), which your generator draws differently (randomness.md); the rest of such a line is exact. The note that follows a participant's last line when it has stopped actions (turns.md, 9) is not listed: it follows from the number of `stopped` entries |
| `notifications` | per participant, the notifications this phase sent it, in the order received, as notification lines (`[day {d}, round {r}] {text}`); these are the news of rounds.md, 8.3 that this phase adds |
| `credits_at_round_end` | the credits this phase added to the round's list (rounds.md, 7), in the order made: `{to, amount, kind, payer}`, `kind` `transfer` or `escrow_release` |
| `corrections` | first phases only: for every participant with a plan, at least one action whose `status` is `ERROR`, and in business after the phase (the participants that get a correction, rounds.md, 8.1, given a readable act): `lost`, its lost clashes (8.2) as `{position, rule, winner}` with `winner` the winning action's `[participant, position]`, or `null` for an acceptance lost to A10 by a manager out of business; `notes`, `{position: note}` (the position written as a decimal string) for every failed action that gets a note (8.2), the note to append to its `line` after one space; `facts`, the fact lines of 8.3 in order; `shown_bids`, the bids 8.3 marks as shown to the participant for its correction phase's shadow (8.6): every bid open after the phase on each post an `Open bids on {post} now` line is written for, of any manager (bids placed in this phase included), so also a bid of a manager out of business that the line itself leaves out (`[]` when no such line is written) |

### How an implementation can check itself

1. **Order and clashes, from the input alone.** Resolve each action's targets on `market` (rounds.md, 6.2; an
   action that is not its participant's own resolves none), make the clash edges (6.3), place the actions (6.4,
   6.5), and compare `order` and each clash record's `rule`, `first`, `second` and `held` exactly. Nothing else is
   needed: each participant's role and the mode (which actions are its own), the posts that exist, each bid's post,
   manager and status in creation order, and each contract's form and `latest_submission_rejected` fix them.
2. **Outcomes.** Set up a market that matches `market` (and, for a correction phase, remove the `hidden` ids from
   each participant's shadow), with each participant's shadow taken from the phase's start state as 6.6 says, run
   the phase, and compare each action's `outcome`, `status` and `line` (where `drawn`, up to the drawn values), the
   statuses in each clash record, `notifications` and `credits_at_round_end`; then, for a first phase, each listed
   participant's `lost`, `notes`, `facts` and `shown_bids`. Every id a text names is either in `input` or made in
   the phase (a new bid, contract or result).

One way to run step 2 is a test harness that builds market objects from `market` and calls your action phase
directly; another is to replay the case's plans as scripted acts on a market brought to that state. Either way only
what the spec makes observable is compared, so the forms in which you hold the market are yours.

### How the expected outcomes were made

Every `expected` was produced by running the reference implementation's own action phase (its targets, clash rules,
placement, shadows, actions, notes and facts), configured as the spec's default market, on the case's market and
plans. Re-running the reference implementation on every case reproduces each line byte for byte, and every id a text
names is in the case's input or was made in the phase. Independently, an ordering written from this spec alone,
given only each case's `input`, gives every case's `order` and clash records (and those of all 8,371 candidate
phases the cases were chosen from).
