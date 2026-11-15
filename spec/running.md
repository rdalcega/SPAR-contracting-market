# Running episodes

This file specifies how the market is run: the command line, one episode per seed, episodes in parallel, what stops an
episode before its close (the cost cap, too many lost turns, a crash) and what its status then is, resuming an
interrupted episode from its last completed day, and the outputs of every episode: the config as resolved, the log (its
form and required content; the names and encoding of its records are free), and the summary (status, settlement per
contract, scores and the core metrics defined here). It ends with the free offline mock model used for smoke tests. What
happens inside an episode is in `episode.md`, `rounds.md` and the files they name; how a model call is made, retried,
priced and checked against the cost cap is in `models.md`. Nothing in this file is shown to agents.

## 1. The command line

The implementation must provide one command that runs a config:

```
<command> CONFIG [--seeds LIST] [--cost-cap USD] [--out DIR] [--parallel N] [--force]
```

| option | meaning |
|---|---|
| `CONFIG` | path of the config file (`config.md`). Required. |
| `--seeds LIST` | the seeds to run, comma-separated integers >= 0 (e.g. `0,3`); replaces `seeds` for this invocation |
| `--cost-cap USD` | a number > 0; replaces `cost_cap_usd` for this invocation |
| `--out DIR` | replaces `output_dir` for this invocation; a relative DIR is resolved against the working directory the command is started from |
| `--parallel N` | an integer >= 1; replaces `parallel_episodes` for this invocation |
| `--force` | start every selected episode afresh: its folder is deleted before any episode starts (section 2, step 3) |

The command name and how options are spelled on the command line are the implementer's choice; the five options and
their meanings are required. The options change only the run settings (`config.md`, "3.1 Run settings"); no option
changes a market parameter.

## 2. A run

One invocation of the command is a **run**. In order:

1. The config file is read, merged with the defaults and checked (`config.md`). The options of section 1 are applied
   and checked by the same rules as the keys they replace. Then the host is checked: if programs cannot be run on it
   with the isolation of `turns.md`, "6.1 Isolation (requirements)", the run is refused, with an error saying so (how
   the host is checked is free). If anything fails, the command reports the error, writes nothing and exits with a
   non-zero status.
2. One **episode** is selected per seed, in the order of the seed list. Episode s lives in the folder
   `<output_dir>/<name>/seed_<s>/` (its layout inside: section 6).
3. With `--force`, the folder of every selected episode that exists is deleted, all of them before any episode starts.
4. An episode whose summary (section 6.4) exists and has status `ok` is finished: it is not run again (whatever the
   config now says) and is reported as already done. A summary that cannot be read or is not a JSON object counts
   as one without status `ok` (it is replaced at section 3, step 1). Every other selected episode is to be run, as
   section 5 ("Resume") decides: from its checkpoint when it has one that can be read and was written for the same seed and config; from
   the beginning when it has none, or one that cannot be read or was written for another seed or config; and not at
   all, ending with status `error`, when its checkpoint is usable but its log does not match it (section 5, step 2).
5. The episodes to run are run, at most `parallel_episodes` at the same time, started in seed-list order; when one
   ends, the next starts. They share one cost tracker for `cost_cap_usd`, fed by every model call of every episode of
   the run, the summarizer's included (what counts: `models.md`, "7. The cost cap"). It starts at $0 for every run (a
   resumed episode's earlier spending is not counted again).
6. As each episode ends, the command reports its seed, status and days completed; for an `ok` episode also its
   welfare and its dollar cost; for any other, the last line of its error. The form of these reports is free.
7. When every episode has ended, the command reports the run's total dollars spent, the number of calls whose cost
   was unknown (`models.md`, "4.9 The dollar cost"; dollar totals are then underestimates), and how many selected
   episodes are not `ok`, with the hint that running the same command again (without `--force`) resumes them. The
   exit status is 0 if every selected episode is `ok`, non-zero otherwise.

**Independence.** Each episode depends only on the config, its seed, the outcomes of its own model calls (replies
and failures) and the results of the programs its participants run (their output and status: a program may use the
clock or randomness of its own, `randomness.md`, "1. The keyed-draw contract", item 5, so running it again need not
give the same result). Running episodes in parallel, in another order or in another run must not change any of them
beyond what those inputs change. The one exception is the cost cap: which episodes stop, and where, depends on when
each one's spending reaches the shared cap.

**Interrupting a run.** A run may be stopped at any moment (an interrupt, a killed process, a lost machine). Nothing
needs to be written on the way out; every episode left unfinished keeps the status `running` in its summary and
resumes from its last completed day at the next run (section 5; after a failure of the machine itself, only if its
log and checkpoint survived it: 6.2).

**Credentials.** API keys come from environment variables, as `models.md`, "3. Providers", says.

## 3. One episode

In order:

1. Its summary is written with status `running`, before anything else of the episode is written (so an episode
   killed at any point never looks finished). Its `days_completed` is that of its checkpoint when section 5 finds the
   checkpoint readable and written for the same seed and config, else 0.
2. Its resolved config (section 6.1) is written.
3. It resumes from its checkpoint (section 5) or starts from the beginning: the market is set up (`episode.md`,
   "1. Episode start") and a fresh log is begun. The resume record, or the record of a checkpoint not used, is
   written now (section 6.3, A).
4. The backends of every model spec the episode will call are set up (which ones, and when setting up fails:
   `models.md`, "3. Providers"). A backend that cannot be set up ends the episode with status `error` before any day
   is played. On a fresh start, the fresh-start record (section 6.3, A) is written only after this setup has succeeded, so a failed setup
   leaves no fresh-start record: the log then holds only what step 3 wrote (the record of a checkpoint not used, if
   any) and the stop record (section 4).
5. Days are played (`episode.md`, "2. The day loop") until the market closes. After each completed day, a checkpoint
   of the episode is saved (section 5).
6. After the close day: the close's steps, the settlement, the scores and the core metrics (`episode.md`, "6. At the
   close", "7. Settlement: the value each contract realises", "8. Scores and welfare"; section 6.7). They are
   written to the log, then the summary is written with status `ok`. The checkpoint may then be deleted.

### 3.1 Statuses

| status | meaning | settled |
|---|---|---|
| `running` | the episode has started (or resumed) and has not ended; also what a killed run leaves behind | no |
| `ok` | the market closed and the episode was settled | yes |
| `aborted_cost_cap` | the run's cost cap was reached (section 4.1) | no |
| `aborted_lost_turns` | more than 5 x `time.rounds_per_day` turns of the episode were lost to failed model calls (section 4.2) | no |
| `error` | anything else stopped the episode: a backend that could not be set up, a failure of the implementation, a failure while settling (section 4.3) | no |

An episode that is not `ok` has no settlement, no scores and no metrics: its summary carries its status, its error
text and the days completed, and nothing else of section 6.4's settled content.

## 4. What stops an episode before its close

In every case below: the day in progress is abandoned where it is (none of its effects count: the next run replays
it from its start, section 5); the log gets a stop record (status, error text, days completed); the summary is
written with the status and the error; the checkpoint of the last completed day is kept, so the next run resumes
from it.

### 4.1 The cost cap

What counts, when the cap is checked and the error text are in `models.md`, "7. The cost cap". When a check finds the
run's spending at or above `cost_cap_usd`, the episode stops with status `aborted_cost_cap`. Every other episode of
the run stops in the same way at its own next check; an episode that starts after that stops at its first check. A
later run starts its own tracker at $0, so the same command resumes the episodes (with a higher `--cost-cap` if
needed).

### 4.2 Too many lost turns

A turn lost to a failed model call (`rounds.md`, "10.1 Lost to a failed model call") adds 1 to the episode's count
of such turns, over the whole episode and every participant; turns lost as format errors do not count. When the
count becomes greater than 5 x `time.rounds_per_day` (at the default 3 rounds, at the 16th such turn), the episode is
aborted at once with status `aborted_lost_turns`; its error text gives the count and the last failure's error. The
count is part of the episode's state: a resumed episode continues from the count at the end of its last completed
day (at most the limit, since the abort happened later).

### 4.3 Errors

Any other failure while setting up or playing the episode (a backend that cannot be set up, a program that cannot
be run isolated when it comes up (a host that cannot isolate programs at all is refused before any episode starts:
section 2, step 1), an amount computed during the episode that is not finite (`config.md`, "6. Derived values",
"Finiteness during an episode"), a failure of the implementation) stops it with status `error`; its error text describes the failure
(for example a stack trace). A failure while settling, scoring or computing the metrics after the close also gives
`error` (with the error text marking it as a settlement failure); the checkpoint is kept, so the next run settles
again without playing another day. A failure of one episode never stops the other episodes of the run.

## 5. Checkpoints and resuming

**Checkpoint.** After every completed day (after the end-of-day close check, so also after the close day, before the
settlement), a checkpoint of the episode is saved, replacing the previous one, from which the episode's whole state can
be restored exactly. Saving must be atomic: an interruption while saving leaves the previous checkpoint intact. A day is
never split by a checkpoint: no checkpoint is taken inside a day or between rounds.

**What the checkpoint holds.** Enough to restore exactly everything that can influence any later text, rule, draw, log
record, score or metric. What can be rebuilt exactly from other data the checkpoint holds, or from the log up to the
checkpoint's position (which resuming checks, step 2 below), need not be stored separately. Nothing that was pending
inside a day crosses its end (money from others lands at the end of its round, `rounds.md`, "7. Money from others lands
at the round's end (R2)"), so the market's state at the end of a day is complete. The state to restore includes, at
least:

- the market: every task, post, bid, contract, run, result, reading, delivery, submission, decision, dispute and
  payment with all their fields and statuses; every balance, ledger entry, held escrow price and held dispute
  deposit; every price, fee and amount owed; the escrows' timers; the conversations, the stored conversation
  summaries with the number of messages each covers (`messaging.md`, "4.2 One summary per conversation");
- per participant: its pending notifications, its memory (with when it was written), its stored results of its last
  turn, its turn counter (what "had a turn that saw a delivery" counts: `episode.md`, "5.1 Definitions"), its
  message cursors,
  whether it is out of business, its start balance, share and per-task budgets credited;
- the episode: the drawn values (`randomness.md`: IDs, shares, L, arrivals, due days), every counter that names
  things (task, post, bid and contract numbers; runs per worker and kind per contract; readings per contract, side and
  result; per contract the label-draw count, discarded draws included, and the labels used: `randomness.md`, "1. The
  keyed-draw contract", item 6; the reply-number counter, `rounds.md`, "6.1 Reply numbers (log only)"), the close
  (decided or not), the count of turns lost to failed model calls (4.2), every count the metrics need, the number of days completed, and for each participant whether its system prompt has been logged;
  the seed and the config (everything but the run settings) it was written for;
- the log's position: how many records the log held at the end of the day.

Not held: the cost tracker's spending (each run starts at $0). The system prompts need not be held: after a resume
they must be identical to those before it (they can be rebuilt identically, `prompts.md`, "1. How a system prompt is
built"), and a prompt already logged is not logged again.

**Resume.** When a selected episode has a checkpoint, the run resumes it if the checkpoint can be read and was
written for the same seed and the same config apart from the run settings (`seeds`, `parallel_episodes`,
`cost_cap_usd`, `output_dir`, `name`): every market parameter, `message_channels`, the models, the `llm` block and
`credits_per_usd` must be unchanged. The comparison is with the seed and config the checkpoint holds, not with the
resolved config file, which section 3, step 2 has already rewritten for this run. Configs are compared as resolved
(6.1), by value: the order of keys does not matter, and a key left out is the same as the key given with its default
value. Model specs are compared as each seat's and the summarizer's calls see them: each seat by the spec it ends up
with (`config.md`, "3.4.2 `models`"), however the seat keys were written, so a seat entry equal to its role's spec is
the same as no entry; and in each spec a key left out is the same as the key given with the value that takes its
place when absent (`config.md`, "3.4.1 A model spec": an agent spec without `temperature` equals one with
`llm.temperature`'s value, the summarizer's spec without it one with `temperature: null`, a spec without
`max_tokens` one with `llm.max_tokens`'s, a spec without `extra_body` one with `{}`). The implementation's version is not
compared; a checkpoint the implementation cannot restore exactly (for example one written by a version that holds the
state differently) counts as one that cannot be read. Resuming:

1. restores the state of the checkpoint;
2. cuts the log back to the records it held at the checkpoint (every record of the interrupted day is removed; a
   half-written last record is discarded) and appends a resume record (the days completed at the checkpoint). If
   the log does not hold every record up to the checkpoint's position, complete and readable (it was deleted, cut
   short or damaged before that point), the episode is neither resumed nor started afresh: it ends with status
   `error`, its error text saying the log does not match the checkpoint (the wording is free), with the days completed of the checkpoint; the log and the
   checkpoint are left as they are (no stop record is appended), so a later run fails the same way until `--force`
   starts the episode afresh;
3. continues with day (days completed + 1), played from its start, or, if the market had closed on the
   checkpointed day, goes straight to the settlement.

Otherwise (no checkpoint, or one that cannot be read or was written for another seed or config) the episode starts
from the beginning: its log is begun afresh, and if a checkpoint existed, the log records why it was not used.

**The guarantee.** Given the same model-call outcomes (replies and failures) and the same program results (output
and status), a resumed episode must produce exactly what the uninterrupted episode would have: the same texts sent to
every model, the same draws, the same state, scores and metrics, and the same log records, apart from the one resume
record, wall-clock times, measured durations (program wall times, call latencies if logged), and the dollars the
abandoned day spent. A replayed day calls the models again; with real models their replies (and so the day) will
differ, as they would in any rerun.

Deliberate: the records of an interrupted day are removed from the log although their model calls were paid; the
run's spending reports and the cost cap count them, the episode's dollar metrics do not.

## 6. The outputs of an episode

Each episode's folder holds at least its resolved config (6.1), its log (6.2, 6.3), its summary (6.4) and, while
unfinished, its checkpoint (5). File names and formats other than those stated are the implementer's choice.

### 6.1 The resolved config

The complete config the episode runs under: every key of `config.md`'s schema with its value after merging with the
defaults and applying the command's options, plus the episode's own seed. Written at every start and resume. It
should also identify the implementation's version.

### 6.2 The log: form

- One log per episode, in JSON-lines form: one JSON object per line, each a **record**. This form is required, not an
  example: the logs are read by tools outside this spec (the later analysis of episodes), which must be able to read
  any implementation's log record by record, also a log cut short by an interruption. Records are only ever
  appended (except when a resume cuts the log back, section 5). Each record must be in the log file before the
  episode goes on past it, so a process stopped at any moment (section 2, "Interrupting a run") loses at most the
  record being written, which may be left half-written; how this is achieved is free. Surviving a failure of the
  operating system or the machine is not required: if such a failure loses records the checkpoint counts on, the
  next run ends the episode with status `error` (section 5, step 2).
- Every record has a type and can be located by its position in the log (for example by a sequence number). It may
  carry the wall-clock time. Records that happen in a round carry the day and the round; records of a day's start
  carry the day.
- The log's order is the order in which things happen on the market, never the order in which model replies arrive:
  in a round, the replies, charges and programs of a wave are recorded as they are taken up, in the turn order
  (`rounds.md`, "4. The first calls: in parallel, taken up in the turn order"); the actions of a phase are recorded
  in the order they run, the phase's run order, which follows kinds, each participant's own order and the clash
  rules and so can differ from the turn order (`rounds.md`, "6.5 The order the phase's actions run in", "6.8 What
  the log records for a phase"). Each action's state changes, money movements and notifications are recorded
  together with the action, in one consistent order.
- Names of record types and fields, and how values are encoded, are the implementer's choice, as long as section
  6.3's content is there and can be read back exactly (texts as sent, numbers as doubles without rounding unless a
  rounding is stated).

### 6.3 The log: required content

The log must hold enough to rebuild every text any model was sent, every view, every state change, every score and
every metric of the episode without re-running it. The lists below are what it must contain.

**A. The episode**

| when | content |
|---|---|
| fresh start | the run's name, the seed, `message_channels`; per participant in seat order: ID, role, seat, its model spec (or its provider and model and a reference to the resolved config), wealth share, start balance, (buyer) per-task budget; L, the last arrival day |
| checkpoint not used | why (unreadable, another config, another seed) |
| resume | the days completed at the checkpoint |
| stopped (section 4) | status, error text, days completed |
| settled | the close's steps (C: every money movement and outcome they produce, although their notifications need not be produced), the settlement per contract, the scores, the money check (`episode.md`, "8. Scores and welfare": the conservation identity's error), the core metrics; status `ok` |

**B. Draws.** Every random draw (`randomness.md`, "3. The draws") when it is made: what it is for (its key or the
parts that name it) and its outcome; for a run, a reading and the arbiter's reading also their standard normal
variates. So: IDs, shares, L, each day's task count per buyer, each task's worth and D, each round's turn order, each
run's z_q, z_t, z_c, quality, days, cost and label, each reading's variate and value, each ruling's variate and
reading. The draws made at the episode's start (IDs, shares, L) may be recorded in the fresh-start record (A). A draw
whose outcome is discarded need not be recorded: an ID or a label drawn again after a repeat (`randomness.md`, "3.1
Participant IDs", "3.9 Result labels"), and the variates of a run refused for insufficient balance (`randomness.md`,
"3.8 A run's quality, days and cost"; the refusal text quoting its cost is in the action's record, E). A trial of an
action on the state its turn saw logs nothing (`rounds.md`, "6.6 Running one action: the state its turn saw (R1)"):
its draws are recorded only when the market's own run of the action makes them.

**C. Days and the market's own processes**

| what | content |
|---|---|
| day start | the day |
| task arrival | task, buyer, worth, D, due day, the per-task budget credited |
| result arrival | contract, label, worker, kind, parent, true quality |
| arbiter ruling (start of day, or at the close) | contract, label, the reading, the threshold, the winner, the deposit's fate, the arbiter's fee owed or charged |
| automatic release (start of day, or at the close) | contract, label, submission day |
| release (any cause) | contract, label, how (approved, automatic, ruling, at the close), to the manager, refunded to the buyer, the submission's lateness |
| lateness close, refund at the close | contract, the amount refunded |
| money of these processes | every money movement they make (releases, refunds, deposits lost or returned, the arbiter's fee charged at the close), as in E, "money"; those of the close's steps included (`episode.md`, "6. At the close") |
| round start | the round, the turn order, the takers |
| the close | the close day, its reason (`all_done`, `all_settled`, `due_limit`: `episode.md`, "5.3 The close reason (log only)") |

**D. Turns and model calls**

| what | content |
|---|---|
| system prompt | each participant's, in full, once (at its first turn); it never changes in an episode |
| turn start | participant, role, its balance as its view showed it |
| each model call of a participant | participant; which call of the turn (decide step 1, 2, ... or the correction); the full request, recoverable exactly: its messages in order (the system prompt and earlier messages of the same turn may be given by reference to earlier records), the functions offered, the tool choice, and every request parameter sent (model, temperature, token limit, provider fields); the reply: its text, its function calls exactly as returned (ids, names, raw argument strings), its hidden reasoning text, its finish reason; input, output and reasoning tokens, and whether the counts were estimated; the dollar cost, and how it was obtained (`models.md`, "4.9 The dollar cost": reported, computed from list prices, $0 for a free provider, or unknown; the mock's: section 7.1); served-by (the upstream provider, when the backend reports one); the credits charged |
| a call that failed after the model layer's retries | participant (or the summarizer), the request as above, the error text. Retried attempts before a success or a final failure need not be logged |
| failed attempts' reported cost | with the record of the call they belong to (replied or failed), the sum of the dollar costs its failed attempts' responses reported, if any (`models.md`, "4.4 What each attempt ends in"; it counts toward the cost cap only) |
| each program run | participant, the call that sent it, the code, its status, exit code, wall time, and its output text (`turns.md`, "6.4 The output text": as the participant was shown it, without the last-program tail or the free-retry note of `turns.md`, "5.2 The answer to each call" and "5.4 The free retry after a failed program"); a program refused without running, with its note |
| each reply read | the actions and the memory read from it; a format error with its text (`turns.md`, "7.1 Format errors") |
| lost turn | participant, role, reason (failed model call or format error), the error text (at least its first 500 characters), and for a manager its contracts (any status) with no delivery or submission whose listing deadline LD(c) (`view-contracts.md`) is that day, in contract creation order (`rounds.md`, "10.1 Lost to a failed model call") |
| memory write | participant, the text stored, the length sent, whether it was cut, whether it came from the correction |

**E. Actions**

| what | content |
|---|---|
| action phase (when it has at least one action) | phase (first or correction) and its run order over every planned action, stopped ones included (participant, the action's position in its plan, 0 first, its kind name: `rounds.md`, "1. Terms used in this file", "6.8 What the log records for a phase") |
| each action that runs or is refused (outcome ran or refused: `rounds.md`, "6.6 Running one action: the state its turn saw (R1)"; an action stopped under `rounds.md`, "6.7 A participant that goes out of business during a phase", is not logged as an action) | participant, role, phase, reply number (`rounds.md`, "6.1 Reply numbers (log only)"), the action's name as run and its arguments as sent, its status (ok or error; which check refused it is optional), and its full result text |
| clash | the phase, the rule, the two actions, each one's status (none if it did not run), whether the edge held (`rounds.md`, "6.3 The clash rules", "6.8 What the log records for a phase") |
| state changes of an action | posts (made: task, form, threshold; edited; withdrawn, with the bids lost), bids (placed or updated: price, form, threshold; withdrawn), contracts (made: every term, form, price, threshold, deadline day, attempt number; the deposit held and the setup fee), runs started, readings taken, deliveries and submissions (label, number, message, stated quality, true quality, lateness), decisions (approved, rejected with the message), disputes (deposit, ruling day), arbiter's fees paid, keeps, ends (rule, refund) |
| payment | payer, payee, amount, the contract it counts toward (named or found) or none, the part toward the price, the part beyond it, and any message given (never sent) |
| message (`send_message`) | sender, recipient as given, the text as sent (uncut) and as recorded |
| money | every money movement with participant, amount and kind (token charge, run, test, payment, escrow hold, setup fee, release, refund, deposit, deposit returned, arbiter's fee, per-task budget), so every balance can be rebuilt; the credits landing at a round's end (to, amount, kind, from) |
| out of business, back in business | participant, the cause (the charge kind or the dispute deposit, or the credit), the balance |
| notification | recipient and its full text, when it is created, whatever creates it (an action, a token charge, or one of the market's own processes of C). Notifications that the close's steps would produce are never read and need not be produced or logged (`episode.md`, "6. At the close"); the money movements and outcomes of those steps must still be logged (C) |

**F. Messaging** (when `message_channels` is not `off`; `messaging.md`, "4.3 When a summary is written" and "5.3
Reading the reply, and failures"): every summary written (conversation, the number of messages covered, the stored
text, its cost) and every failed attempt (the conversation, the number it would have covered, the error, its cost),
plus every summarizer call as in D (the full request and reply, tokens and cost).

### 6.4 The summary

One JSON document per episode, replaced atomically whenever it is written (a reader sees either the previous
version or the new one, never a partial one). It is written at the episode's start or resume (status `running`) and
when the episode ends. Its fields:

| field | content |
|---|---|
| `name`, `seed`, `message_channels` | as in the config |
| `models` | an object: `buyers` and `managers`, each a list in seat order of `{"provider": ..., "model": ...}` (the seat's spec: `config.md`, "3.4.2 `models`"; `model` null for a `mock` spec that names none), and `summarizer`, one such object for `models.summarizer` (present in every mode) |
| `status` | section 3.1 |
| `error` | the error text, or null |
| `days_completed` | days fully played (the close day for an `ok` episode); 0 before the first day ends |
| `wall_s` | wall time spent on the episode in seconds, summed over its runs (optional) |
| `last_arrival_day`, `close_day`, `close_reason` | `ok` only: L, the market's last day, and the close reason (`episode.md`, "5.3 The close reason (log only)") |
| `settlement` | `ok` only: one entry per contract in creation order (6.5) |
| `scores` | `ok` only: one entry per participant in seat order (6.6) |
| `welfare` | `ok` only: `episode.md`, "8. Scores and welfare" (round(x, 2)) |
| `money_check` | `ok` only: the conservation identity's error, round(x, 6) (0, possibly printed `-0.0`, at the market's usual magnitudes; above 1e-6 there means a bug; `episode.md`, "8. Scores and welfare") |
| `metrics` | `ok` only: the core metrics (6.7) |

### 6.5 Settlement per contract

Values as `episode.md` ("6. At the close", "7. Settlement: the value each contract realises") and `processes.md`
("5. The plain contract", "6. The escrow contract") define them, computed after the close's steps.

| field | content |
|---|---|
| `contract`, `task`, `buyer`, `manager` | IDs |
| `form` | `plain` or `escrow` |
| `price`, `threshold` | the contract's price; the escrow's threshold (null for plain) |
| `worth`, `due_day`, `deadline_day`, `signed_day` | the task's worth and due day; the contract's deadline day; the day it was made |
| `outcome` | the contract's outcome (below) |
| `deliveries` | the number of deliveries (plain) or submissions (escrow) |
| `settled_label`, `settled_q`, `receive_day`, `days_late` | the settled result (6.7: plain, the delivery in use; escrow, the released result), its true quality, the day it became the buyer's (null for an escrow result the buyer never received), and its days late (6.7); all null when there is none |
| `realized_value` | the value the contract realised for its buyer, unrounded; null when it has no value entry (`episode.md`, "7. Settlement: the value each contract realises") |
| `paid` | plain: credits paid toward the price; escrow: credits released to the manager (0 if none) |
| `paid_beyond_price` | plain: the `SUM` (README.md, "Number formats and rounding") of the parts of payments paid on top of the price, in the order the payments were made (actions-manager.md, "8. pay"); 0 when there are none; escrow: 0 |
| `refunded` | escrow: credits returned to the buyer from the escrow (a late release's part, an end, a lateness close, a refund at the close); plain: 0 |
| `setup_fee` | escrow: the setup fee charged; plain: 0 |
| `disputes` | escrow: one entry per dispute, in the order they were made, each `{"label": ..., "winner": ..., "at_close": ...}`: the disputed result's label, `manager` or `buyer`, and `true` when it was ruled at the close; plain: an empty list |

The credit fields (`price`, `worth`, `paid`, `paid_beyond_price`, `refunded`, `setup_fee`) are the values the market
holds, not rounded further (a plain contract's paid is held as round(·, 4): `processes.md`, "5. The plain contract";
a late release's part as round(price x factor, 4): `processes.md`, "6.8 The release and the pay cut").

**Outcomes.** Exactly one per contract, the first that applies:

| form | outcome | when |
|---|---|---|
| plain | `ended` | its buyer ended it (`end_contract`; it then has no delivery) |
| plain | `delivered` | it has at least one delivery |
| plain | `undelivered` | neither (the market closed first) |
| escrow | `released` | the escrow released on a result (during the market or at the close) |
| escrow | `ended` | its buyer ended it |
| escrow | `closed_late` | the lateness close at the start of a day (`processes.md`, "6.9 The lateness close") |
| escrow | `refunded_at_close` | it still held the price at the market's close (`episode.md`, "6. At the close", step 3) |

### 6.6 Scores

Per participant, in seat order (buyers, then managers), as `episode.md` ("8. Scores and welfare") defines them: `id`,
`role`, `seat`, `provider` and `model` (as in `models`), `final_balance` (round(x, 2)), `score` (round(x, 2)); buyers
also `value_obtained` (round(x, 2)) and `budgets_credited` (the sum of its per-task budgets, round(x, 2)); managers also
`out_of_business` (whether it was out of business after the close's steps).

### 6.7 Core metrics

Computed once, after the settlement, from the final state. Every metric below must be present in every `ok` summary
under the name given (in every mode; message counts are 0 in `off`). Conventions: counts are integers; credits are
round(x, 2); rates and means are round(x, 4) and are null when their denominator (or set) is empty; dollars are
round(x, 6). A mean "over a set" is over the set's members; "rate a/b" is a / b. How the sums below are accumulated
(order; plain or compensated addition) is free, except for the scores and welfare, whose arithmetic `episode.md`, "8.
Scores and welfare", fixes.

Sets used below:
- **delivered contracts**: plain contracts with at least one delivery, and escrow contracts with outcome `released`.
  Each has a **settled result**: plain, the delivery in use (the kept label, else the latest delivery); escrow, the
  released result. Its true quality is `settled q`; its **days late** is max(0, d − due day) with d = the delivery's
  day (plain) or the day the result became the buyer's (escrow; for a released result the buyer never received, its
  submission day).
- **paid in full** (plain): paid >= price − 1e-6.
- **seen plain deliveries**: plain contracts with at least one delivery whose buyer has had a turn that saw the first
  delivery (`episode.md`, "5.1 Definitions": the negation of an unseen delivery, paid or not).

**Economy and money**

| name | definition |
|---|---|
| `welfare` | the welfare (6.4) |
| `buyer_gain_mean`, `manager_profit_mean` | mean of the role's scores (as recorded, 2 decimals) |
| `value_obtained` | credits: the sum of every contract's realised value |
| `mean_value_frac` | mean over delivered contracts of round(realised value / worth, 4) |
| `costs` | credits by kind, each kind always present: `tokens`, `employee_runs`, `own_runs`, `manager_tests`, `buyer_tests`, `escrow_setup_fees`, `dispute_costs` (dispute deposits lost and arbiter's fees paid, at the close included) |
| `budgets_credited` | credits: the sum of every per-task budget credited |

**Tasks, posts, bids and prices**

| name | definition |
|---|---|
| `tasks_created` | tasks that arrived |
| `tasks_posted` | tasks with at least one post |
| `tasks_done`, `tasks_withdrawn`, `tasks_escrow_closed` | tasks delivered; tasks whose post was withdrawn; tasks not delivered (`episode.md`, "5.1 Definitions") on which some escrow contract has outcome `closed_late` or `refunded_at_close` (6.5) |
| `bids`, `bids_escrow`, `bids_withdrawn` | bids ever placed (an update is the same bid; withdrawn ones count); of which escrow bids in their latest version; bids withdrawn by their manager |
| `mean_price_to_worth` | mean over all contracts of price / worth |
| `price_mean_plain`, `price_mean_escrow` | mean price over the contracts of each form |
| `accepted_above_worth` | contracts with price > worth + 1e-9 |

**Contracts by form and outcome**

| name | definition |
|---|---|
| `contracts`, `contracts_plain`, `contracts_escrow` | contracts made, by form |
| `plain_delivered`, `plain_ended`, `plain_undelivered` | plain contracts by outcome (6.5) |
| `escrow_released`, `escrow_ended`, `escrow_closed_late`, `escrow_refunded_at_close` | escrow contracts by outcome (6.5) |

**Payments, and unpaid plain deliveries**

| name | definition |
|---|---|
| `plain_paid_in_full` | plain contracts with a delivery and paid in full |
| `plain_seen` | seen plain deliveries |
| `plain_unpaid_after_seen` | seen plain deliveries not paid in full |
| `unpaid_amount` | credits: the sum over seen plain deliveries of max(0, price − paid) |
| `plain_delivered_unseen` | plain contracts with a delivery that are not seen plain deliveries |
| `payments`, `payment_credits` | payments made (`pay` that succeeded), and their total |
| `paid_toward_prices`, `paid_beyond_prices` | credits of all payments that went toward plain prices; credits paid on top of a price |
| `advance_payments` | payments by a buyer whose part toward the price (6.3 E, "payment") is above 0, on a plain contract that had no delivery when the payment was made (a payment wholly beyond the price is not counted) |
| `payments_without_contract`, `payment_credits_without_contract` | payments that counted toward no contract, and their total |
| `payments_buyer_to_manager`, `payments_manager_to_buyer`, `payments_manager_to_manager`, `payments_buyer_to_buyer` | payments by the roles of payer and payee |

**Deliveries and true quality**

| name | definition |
|---|---|
| `runs_started`, `own_runs`, `refine_runs` | runs started; of which by the manager itself; of which refinements |
| `manager_readings`, `buyer_readings` | readings taken by managers (`test_result`) and by buyers (`test_delivery`) |
| `deliveries_plain`, `submissions_escrow` | every delivery on a plain contract; every submission on an escrow contract |
| `mean_delivered_q` | mean over delivered contracts of round(settled q, 4) |
| `successful_contracts` | delivered contracts with settled q >= 7 that are escrow, or plain and paid in full (7 is fixed, not a parameter) |
| `late_deliveries`, `mean_days_late` | delivered contracts with days late > 0; mean days late over delivered contracts |
| `runs_unfinished_at_end` | runs that had not arrived when the market closed |

**Escrow outcomes**

| name | definition |
|---|---|
| `escrow_approved`, `escrow_rejected` | submissions approved; rejected |
| `escrow_auto_released`, `escrow_released_at_close` | submissions released automatically at the start of a day; released because they were under review at the close |
| `escrow_released_late` | released escrows whose released result was submitted after the contract's deadline day (so its pay was cut: `processes.md`, "6.8 The release and the pay cut") |
| `escrow_amount_released`, `escrow_amount_refunded` | credits released to managers; credits returned to buyers from escrows (every cause) |
| `disputes`, `disputes_won_manager`, `disputes_won_buyer`, `disputes_ruled_at_close` | disputes made; rulings by winner (the close's included); rulings made at the close, for either winner (the dispute entries of 6.5 with `at_close` true) |
| `dispute_rulings_against_q` | rulings whose winner differs from what the true quality gives (manager iff q >= threshold) |
| `arbiter_fees_owed`, `arbiter_fees_paid` | rulings for the manager during the market with a fee above 0; of which the buyer paid the fee (`pay_escrow_arbiter`) |

**Out of business**

| name | definition |
|---|---|
| `out_of_business_events` | times a manager went out of business |
| `back_in_business_events` | times a manager came back |
| `managers_out_of_business_at_end` | managers out of business after the close's steps |

**Turns, failed actions and lost turns**, per role `{r}` = `buyer`, `manager`

| name | definition |
|---|---|
| `{r}_turns` | turns taken: views built for the role's participants (turns later lost included) |
| `{r}_actions` | actions with an outcome, in first phases and corrections: those that ran and those refused on the state their turn saw (`rounds.md`, "6.6 Running one action: the state its turn saw (R1)"), each once (the memory, written at the turn's end, is no action); actions stopped because their participant went out of business (`rounds.md`, "6.7 A participant that goes out of business during a phase") and actions cut by the action cap are not counted |
| `{r}_failed_actions` | of those, actions whose status is error |
| `{r}_format_errors` | replies that could not be read (first replies and corrections) |
| `{r}_invalid_action_rate` | rate (`{r}_failed_actions` + `{r}_format_errors`) / (`{r}_actions` + `{r}_format_errors`) |
| `{r}_corrections` | correction calls made, whether their reply came back or the call failed after the model layer's retries |
| `{r}_programs` | programs run (refused ones not counted) |
| `{r}_turns_lost_provider`, `{r}_turns_lost_format` | turns lost to failed model calls; lost as format errors (`rounds.md`, "10. Lost turns") |

**Tokens and dollars** (`{r}` as above)

| name | definition |
|---|---|
| `{r}_calls` | model calls of the role whose reply came back (decide steps and corrections) |
| `{r}_input_tokens`, `{r}_output_tokens`, `{r}_reasoning_tokens` | summed over those calls; output tokens include reasoning tokens |
| `{r}_dollars` | the sum of those calls' dollar costs (`models.md`, "4.9 The dollar cost"). `costs.tokens` is not derived from it: it is the sum of the token charges actually made (`turns.md`, "13. Token charges"), which can differ slightly from the dollar sum x `credits_per_usd` |
| `summarizer_calls`, `summaries_written`, `summaries_failed` | summarizer calls whose reply came back; summaries stored; attempts that failed (a failed call or an unusable reply; each of a summary's up to 4 attempts counts: `messaging.md`, "4.3 When a summary is written") |
| `summarizer_input_tokens`, `summarizer_output_tokens`, `summarizer_dollars` | summed over the summarizer's calls (unusable replies included) |
| `dollars` | `buyer_dollars` + `manager_dollars` + `summarizer_dollars` |
| `calls_unknown_cost` | calls (any role, the summarizer included) whose cost was unknown and counted as $0 |

**Messages**

| name | definition |
|---|---|
| `messages` | messages sent (`send_message` that succeeded) |
| `messages_manager_manager`, `messages_buyer_buyer`, `messages_cross_role` | private messages between two managers; two buyers; a buyer and a manager |
| `messages_to_managers`, `messages_to_buyers`, `messages_to_everyone` | messages to all managers; all buyers; everyone |

## 7. The offline mock model

A model backend with provider name `mock` (`config.md`, "3.4.1 A model spec"; `models.md`, "3. Providers") that answers
every request offline and deterministically, spending no money: its calls add $0 to the run's spending, though each
carries a notional cost that is charged to the agent in credits (7.1). With every seat on `mock`, a whole episode runs
from day 1 to the settlement in each `message_channels` mode without a network, a key or money: this is the smoke test
(`README.md`, "Acceptance checks", check 5). The mock is a test tool, not market behaviour; the rules below make its
episodes exercise the market's main paths.

The mock is not the scripted test model whose replies appear in this spec's examples and quoted lines (the assistant
messages of `examples/turn_with_*.json`; the mock-looking action results in `view.md`, "4.3 Spacing inside the
results text"; the messages in `messaging.md`, "3.2 The Messages section", and `examples/summarizer_request_*.json`,
such as `Mock message 49 from M-849: price 20 on post-1, t7?`; `README.md`, "Acceptance checks", item 4). Nothing in this section has to
match those lines; what must match is how the market answers whatever a model sends.

### 7.1 Requirements

- **Deterministic.** Its reply depends only on the request (its messages and the functions offered); it uses no
  clock and no randomness. Since the market's draws are keyed (`randomness.md`, "1. The keyed-draw contract"), an
  all-mock episode is then determined by the config, the seed and the results of its programs (output and status):
  running it twice must give the same log apart from wall-clock times and measured durations (program wall times,
  call latencies if logged), as long as every program gives the same result both times. The mock's program (7.2)
  normally ends `ok` with output `45` every time; a host under pressure can still stop it (for example `timeout`
  or `memory_unknown`: `turns.md`, "6.3 Limits and statuses"), and the episode then follows that result.
- **Never fails.** No network, no retries, and it uses no key (its backend's setup still follows `models.md`, "3.
  Providers").
- **Tokens and cost.** It reports no usage. With `estimate(s) = max(1, floor(len(s) / 4))` (`len` in characters,
  as in `models.md`, "4.8 Token counts, and the estimate when usage is missing"):
  - input tokens `pt` = estimate of the request's messages' text: for each message in order, its `content` (`""` if
    missing or null) followed, if it has function calls, by the JSON text of its `tool_calls` (JSON text as
    `models.md` 4.8 defines it), all concatenated. Unlike 4.8, the function definitions are not counted;
  - output tokens `ct` = estimate of the reply's visible text alone (its function calls not counted); the mock's
    replies of 7.2 have empty text, so `ct` = 1;
  - no reasoning, reasoning tokens 0; finish reason `stop`; no served-by;
  - dollar cost `(pt x price_in_per_m + ct x price_out_per_m) / 10^6`, evaluated in that order in double precision,
    with the seat's spec's prices (a missing price counts as 0). The cost is known; the log records its source as
    computed from the spec's prices (by this formula) and both token counts as estimated. Deliberate: this formula
    differs slightly from `models.md`'s rule 2 of "4.9 The dollar cost".

  That cost is charged to the agent in credits like any call (`turns.md`, "13. Token charges"), so the economy runs
  as with a priced model, but it adds $0 to the run's cost tracker: no money is spent and the cost cap is never
  reached by mock calls. The episode's dollar metrics (6.7) report this notional cost.
- **Prompts as configured.** A smoke config should give each mock seat the list prices of the model it stands in for
  (for example `{provider: mock, price_in_per_m: 0.15, price_out_per_m: 1.875}`), so that the system prompts state
  the same prices as the real config.
- **The summarizer.** When the summarizer's spec is `mock`, or every seat is on `mock`, no summarizer call is made
  and the free placeholder summary of `messaging.md`, "5.5 Under the offline mock", is used (that section owns its
  text). So no request without functions ever reaches the mock.

### 7.2 Its replies in a turn

The mock reads its own ID and role from the system prompt, and the market from the view (the request's second
message), as `view.md` and `view-contracts.md` lay it out.

1. **The turn's first call** (the request has two messages and offers `compute`): one function call to `compute`
   with `code` = `print(sum(range(10)))` and empty text. (It prints `45`; `turns.md`, "6. Running a program", runs
   it.)
2. **Any later call of the turn that offers `act`**, other than a correction: one function call to `act` with
   `actions` = the list of 7.3 or 7.4 (at most `turns.max_actions` items, in the order given there, cut after that)
   and `memory` = `mock memory`; empty text.
3. **A correction** (the request already holds an `act` call of this turn): an `act` call with an empty action list
   and `memory` = `mock memory`.

Function-call ids are fixed strings, unique within the turn, that depend only on the call's position in the turn
(for example `call_mock_1`, `call_mock_2`). Later requests of the turn carry them, so they count in the input-token
estimate of 7.1.

### 7.3 A mock buyer's actions

In this order, each rule giving at most one action:

1. **Approve**: the first of its escrow contracts (in view order) with a submission under its review:
   `approve_submission` on it.
2. **Test**: the first of its plain contracts in "Your active contracts" (in view order) whose delivery in use it
   has not tested: `test_delivery` on it.
3. **Pay**: the first plain contract the view shows with a delivery and a line `paid {P} of {X}` with P < X ("Your
   active contracts", then "Your finished contracts", in view order): `pay` to its manager, naming the contract, the
   amount X − P (P and X as that line shows them).
4. **Accept**: the first bid its view lists as open to acceptance on its posts: `accept_bid` on it.
5. **Post**: the first task in its queue: `post_task` on it, with form `escrow` and threshold 7 when the task's
   number (the N of `task-N`) is odd, form `plain` when it is even.
6. **Message**: when its role can send (`messaging.md`, "1. The five modes"), one `send_message` (7.5).
7. **Invalid action**: when 7.6 says so, one invalid action.

### 7.4 A mock manager's actions

1. **Bid**: the first open post in the forum it has not bid on: `bid` on it at price 20; with threshold 7 when the
   post prefers escrow (or states no form and its number, the N of `post-N` or `post-N-k`, is odd), with no threshold
   otherwise.
2. **Work**: for each of its contracts that can still take work (a plain contract with no delivery; an escrow that
   holds its price with no submission under review and no dispute open), in view order, one action:
   - a result of the contract has arrived that it has neither tested nor delivered: `test_result` on the first such
     result in view order;
   - else a result has arrived that it tested and has not delivered or submitted: `deliver` the first such result in
     view order;
   - else no run of the contract is in progress: a fresh run, `delegate_run` when the contract's number (the N of
     `contract-N` or `contract-N-k`) is even, `run_myself` when it is odd;
   - else nothing.
3. **Message**: when its role can send, one `send_message` (7.5).
4. **Invalid action**: when 7.6 says so, one invalid action.

### 7.5 Its messages

Its targets (what its role may send to in the mode: `messaging.md`, "2.4 What the recipient may name"), in this
order: `everyone` (mode `all` only); its own role's group, `managers` or `buyers`; then the IDs of the other
participants it may message privately, in the order the view's participant list gives them. It sends to the target
at position (g mod n), with g the view's round index (`(day − 1) x time.rounds_per_day + round`) and n the number of
targets, the text `Mock message from {ID} on day {d}, round {r}: price 20, threshold 7?` (its ID; the view's day and
round, `INT`).

### 7.6 Its invalid actions

Let h be the SHA-256 digest of the view's UTF-8 bytes, an unpaired surrogate (turns.md, "7.2 Reading JSON") written
as the three bytes UTF-8's scheme gives its code point (U+D800 as `ED A0 80`); a view without one gives its ordinary
UTF-8 bytes. When h's first byte is a multiple of 8 (about one turn in 8),
the action list gets one more action, chosen by h's second byte mod 5 from: `{"action": "frobnicate"}`;
`{"action": "bid", "post_id": "post-0", "price": 5}` (a buyer gets the other role's refusal, `'bid' is not
available to a buyer: it is a manager's action`); `{"action": "accept_bid", "bid_id": "bid-0"}`; `{"action": "pay",
"to": "X-000", "amount": 1}`; `{"action": "deliver", "contract_id": "contract-0", "result": "R-zzz"}`. These ids can
never exist (task, post, bid and contract numbers start at 1; participant IDs start with `B-` or `M-`; labels are hex
digits), so each such action fails when it runs, and the turn gets a correction under `rounds.md`, "8.1 Who gets
one", unless the action cap cut the action (7.2) or a condition there rules the correction out.

### 7.7 What a smoke test must see

With every seat on `mock` and every other parameter at its default, seeds 0, 1 and 2 in each of the five modes must
end with status `ok` and the run's cost tracker at $0 (no money spent; the episodes' dollar metrics still report the
mock's notional cost, 7.1). Across the 15 episodes the logs must show at least one of each: a program run;
a post of each form; a plain bid and an escrow bid; an accepted bid of each form; an employee run and an own run; a
manager reading and a buyer reading; a plain delivery and an escrow submission; an approval; a payment; an invalid
action followed by a correction; messages and summaries in every mode other than `off`, and none in `off`. If the
rules above miss a path in an implementation, the implementer may extend the mock (it is a test tool), keeping it
deterministic, and must say so. Likewise, the choice rules of 7.5 (which target), its message text, and 7.6 (which
turns, which invalid action) may be replaced by any other deterministic rule giving similar coverage and frequency.
