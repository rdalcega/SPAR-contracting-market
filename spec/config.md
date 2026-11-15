# Configuration

This file defines every parameter of the market: what it means, its default, its unit, the range the implementation
must accept, and which spec file states its value to agents. It also defines how a config file is given and merged
with the defaults, the model specs and the `llm` block, the one switch `message_channels`, the checks that involve
several parameters, and the values derived from parameters (with their formulas). What a parameter does in the market
is specified in the file named in its row; this file is the single list of names, defaults and ranges.

## 1. The config file

1. A config is one YAML file holding a mapping. Every key is optional: a key the file does not give takes its default
   (section 2). One config is one market run over a list of seeds (`seeds`). An empty file (or one holding only
   `null`) gives every default; a file whose top level is any other non-mapping is refused.
2. Reading the YAML. A file the reader cannot read is refused. An unquoted `off` may be read as the string `off` or,
   as YAML 1.1 types it, as the boolean false; both mean `off` (item 5). Beyond that, the YAML version, its scalar
   typing and what a key repeated in one mapping does are the implementation's choice: a config whose meaning
   depends on them (a repeated key; a plain scalar that YAML 1.1 and YAML 1.2's core schema type differently, such as
   `on`, `yes`, `010` or `1e3`) is outside this spec. The default config reads the same either way. (The reference
   implementation types scalars as YAML 1.1 does, where `1e3` is a string, and a repeated key takes its last value.)
3. Merging: a mapping in the file is merged key by key into the default mapping at the same place, at every depth.
   A list or a scalar replaces the default. A model spec (`models.buyers`, `models.managers`, `models.summarizer`,
   and each entry of `models.buyer_seats` and `models.manager_seats`) replaces the default spec as a whole: a key the
   file's spec leaves out is absent, not taken from the default spec. Example: `models: {managers: {provider: mock}}`
   gives the managers a spec with no `model`, no prices and no `extra_body`. A key whose default is a mapping (a block
   such as `llm`, `time` or `models`; `contracts.escrow`; `production.employee`; `production.manager`; a model spec)
   must be given as a mapping, except that `models.buyer_seats` or `models.manager_seats` given as null is `{}`.
   Inside a model spec, a key given as null means what its row gives for null (`temperature`, `api_key_env`; section
   3.4.1), `max_tokens` given as null is refused, and any other key given as null counts as absent.
4. Unknown keys are refused, at every depth (top level, inside every block, inside a model spec, inside
   `contracts.escrow`, `production.employee`, `production.manager`): the error names the full key path
   (e.g. `production.manager.speed`). Two places hold no schema keys and are exempt: the contents of a model spec's
   `extra_body` (section 3.4.1) and the keys of `models.buyer_seats` / `models.manager_seats`, which are seat indices
   (section 3.4.2).
5. Types:
   - An "integer" parameter must be a YAML integer. `true`/`false` are refused; so is a number with a fractional
     part or written as a decimal (`3.0`).
   - A "number" parameter must be a YAML integer or decimal that is finite as a double. `true`/`false`, `.nan`,
     `.inf` and strings are refused.
     Every number is used as an IEEE-754 double: a number given as an integer behaves exactly as the same double
     (e.g. `tests.manager_cost: 1` is the double 1.0).
   - A pair (`[low, high]`) must be a YAML list of exactly two elements of the stated type.
   - `message_channels`: the string `off` and the boolean false (what a YAML 1.1 reader makes of an unquoted `off`)
     both mean `off`. Any other non-string value (`true`, a number) is refused.
6. Every check of this file (sections 1, 3 and 4, and section 6's "Finiteness at load") runs when the config is
   loaded, before any episode starts, any model is called or any output is written. A config that fails a check is
   refused as a whole: the error names the key (or keys) that break the rule, and states the rule. When several rules
   fail, naming the first one found is enough. Error texts are never shown to agents; their wording is free.
7. Nothing else configures the market: the fixed values of section 5 are not keys, and the drawn values
   (shares, the last arrival day, IDs, ...) are never given by the config.
8. Run options given outside the file (for example on a command line) may override the run settings of section 3.1;
   running.md, "The command line", says which. They never change a market parameter.

## 2. The schema with its defaults

```yaml
# runs
seeds: [0, 1, 2]
parallel_episodes: 4
cost_cap_usd: 20
output_dir: runs
name: market

message_channels: off           # off | managers | buyers | within_roles | all

participants: {buyers: 2, managers: 3}

models:
  buyers:   {provider: openrouter, model: qwen/qwen3.8-27b, price_in_per_m: 0.15, price_out_per_m: 1.875,
             extra_body: {provider: {quantizations: [bf16, fp16, fp8, unknown]}}}
  managers: {provider: openrouter, model: qwen/qwen3.8-27b, price_in_per_m: 0.15, price_out_per_m: 1.875,
             extra_body: {provider: {quantizations: [bf16, fp16, fp8, unknown]}}}
  buyer_seats: {}
  manager_seats: {}
  summarizer: {provider: openrouter, model: anthropic/claude-sonnet-5.5, price_in_per_m: 2.0, price_out_per_m: 10.0,
               temperature: null, max_tokens: 8000, extra_body: {reasoning: {effort: high}}}
llm: {temperature: 0.8, max_tokens: 64000, timeout_s: 1200, max_retries: 5, network_patience_s: 900}
credits_per_usd: 1

time:
  rounds_per_day: 3
  horizon_min_days: 10
  horizon_end_prob: 0.1
  close_after_due_days: 5

turns:
  max_actions: 6
  memory_chars: 2000

tasks:
  first_day_tasks: 2
  arrival_p: 0.5
  worth_range: [40, 80]
  due_days_range: [4, 9]

money:
  start_amount: 60
  task_budget: 30
  share_range: [0.6, 1.4]
  share_decimals: 2

value: {q0: 1, exponent: 2, late_decay: 0.2}

contracts:
  bid_window_rounds: 1
  min_contract_days: 4
  message_chars: 500
  escrow: {fee_rate: 0.10, review_days: 2, dispute_window_days: 1, dispute_fee_rate: 0.5, arbiter_sd: 0.5}

production:
  cost_ref_worth: 80
  sig_t: 0.35
  sig_c: 0.25
  employee: {mu: 6.0, sd: 2.5, gain: 4.0, sd_ref: 1.5, t_med: 1.0, t_ref_med: 0.7,
             fee: 1.0, cost_frac: 0.0375, ref_cost_frac: 0.0225, capacity: 2}
  manager:  {mu: 7.0, sd: 1.2, gain: 7.0, sd_ref: 0.8, t_med: 2.5, t_ref_med: 1.0,
             k: 0.1, ref_cost_fixed: 14.0, capacity: 1}

tests: {manager_sd: 0.5, manager_cost: 1, buyer_sd: 2.0, buyer_cost: 1, decimals: 1}

messaging: {message_chars: 500, recent: 10, summary_chars: 2000}

compute: {cpu_s: 5, wall_s: 10, memory_mb: 512, output_kb: 64, output_chars: 4000, code_chars: 20000}

view: {finished_shown: 5, open_shown: 8, readings_shown: 5, overdue_days: 5, recent_days: 3,
       messages_shown: 3, payments_shown: 8, overflow_ids: 20}
```

## 3. The parameters

Column "stated in" names the spec files whose agent-visible texts contain the parameter's value or a value computed
from it; changing the parameter must change those texts as those files define. "never" means no agent ever reads it.
`prompts.md` means the system prompts (both roles unless a role is named).

### 3.1 Run settings

None of these changes the market's rules or texts, or what happens in the episode of a given seed, except that
`cost_cap_usd` can stop episodes before their close (running.md, "The cost cap"). running.md ("The command line",
"A run") specifies how they are used.

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `seeds` | one episode per seed, each with the market's draws keyed on that seed (randomness.md, "The keyed-draw contract") | `[0, 1, 2]` | non-empty list of distinct integers >= 0 | never |
| `parallel_episodes` | how many episodes run at the same time | 4 | integer >= 1 | never |
| `cost_cap_usd` | dollar cap on all model calls (agents' and the summarizer's) of all episodes of one invocation together; a later invocation (e.g. a resume) starts again from $0 (models.md, "The cost cap"; running.md, "The cost cap") | 20 | number > 0, or null for no cap | never |
| `output_dir` | root folder of the outputs; episodes go to `<output_dir>/<name>/seed_<seed>/` (running.md, "The outputs of an episode"); how a relative path is resolved is the implementation's choice (the `--out` option's rule is running.md's, "The command line") | `runs` | non-empty string | never |
| `name` | the run's name, a folder directly inside `output_dir` | `market` | one folder name: a non-empty string that is not `.` or `..` and contains no `/`, `\` or NUL character | never |

### 3.2 The switch: `message_channels`

| key | meaning | default | valid values | stated in |
|---|---|---|---|---|
| `message_channels` | which participants can send messages to whom | `off` | `off`, `managers`, `buyers`, `within_roles`, `all` | prompts.md, messaging.md, view.md; actions-buyer.md, "The actions of each role" (the action lists that both roles' refusals quote); turns.md, "An action named only by a key" (the two refusals of a send_message under `off`) |

Who can send to whom in each mode, and the views' Messages section: messaging.md, "The five modes". What each mode
changes in the system prompts: prompts.md, "What each message_channels mode changes". In every mode other than `off`
the `messaging.*` parameters take effect and the conversation summarizer (`models.summarizer`) is used; in `off`
neither exists and the summarizer is never called. Everything else in the market is the same in all five modes,
apart from the action lists that refusals quote and the names act recognises (messaging.md, "The five modes").

### 3.3 `participants`

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `participants.buyers` | number of buyer seats | 2 | integer, 2..4096 | prompts.md (the count); view.md, "Lines and the header" (the participants line) |
| `participants.managers` | number of manager seats | 3 | integer, 2..4096 | prompts.md (the count); view.md, "Lines and the header" (the participants line) |

Seats are numbered 0..n-1 within each role, in creation order (episode.md, "Participants, IDs and seat order"). The
upper bound 4096 is the number of distinct IDs a role has (`B-` or `M-` plus 3 hex digits).

### 3.4 Models, the `llm` block and token prices

#### 3.4.1 A model spec

A model spec is a mapping with these keys and no others:

| key | meaning | when absent | valid values |
|---|---|---|---|
| `provider` | which backend answers the calls (what each needs: models.md, "Providers") | refused: required | `openrouter` (the default implementation), `openai_compatible` (any OpenAI-compatible endpoint), `ollama` (a local OpenAI-compatible server; free, no API key) or `mock` (the free offline mock model; running.md, "The offline mock model") |
| `model` | the provider's model identifier, sent with every call | refused, except with provider `mock` | non-empty string |
| `price_in_per_m` | the model's dollar list price per million input tokens | see "prices" below | number >= 0; > 0 where required (below) |
| `price_out_per_m` | the model's dollar list price per million output tokens (hidden reasoning counts as output) | see "prices" below | number >= 0; > 0 where required (below) |
| `temperature` | sampling temperature sent with each call; `null` sends no temperature | for an agent's spec: `llm.temperature`; for `models.summarizer`: no temperature is sent | number >= 0, or null |
| `max_tokens` | per-call token limit (reasoning and reply together) | `llm.max_tokens` | integer >= 1 |
| `extra_body` | provider-specific request fields, set over the request last, so they can override any field the market sets (models.md, "The body") | nothing added | a mapping, not checked against any schema, that must have a JSON form (at every depth only mappings with string keys, lists, strings, finite numbers, booleans and null); anything else is refused, naming the spec's `extra_body` path |
| `base_url` | the server's base URL | the provider's (models.md, "Providers"); with `openai_compatible`, refused: required | non-empty string |
| `api_key_env` | name of the environment variable holding the API key; `null` sends no key | the provider's (models.md, "Providers") | non-empty string, or null |

Prices: `price_in_per_m` and `price_out_per_m` are required (number > 0) in every agent spec (`models.buyers`,
`models.managers`, every entry of `models.buyer_seats` and `models.manager_seats`) whose provider is not a free one.
The free providers are `mock` and `ollama`. In the summarizer's spec the prices are optional. They price a call whose
reply reports no dollar cost (models.md, "The dollar cost"; the mock's cost: running.md, "Requirements"), and an
agent's system prompt states its own seat's prices x `credits_per_usd` (section 3.4.4; prompts.md, "Fields in both
roles' templates"), 0 for a price a free provider's spec leaves out.

An implementation may accept further provider names for providers it adds behind the same interface (models.md,
"Providers"). Such a provider is not free: an agent spec that uses it must give both prices > 0. Any name the
implementation does not have is refused.

Whether the API key variable is set is not a config check: it is checked when the episode sets up that spec's
backend (models.md, "Providers"; a missing key ends that episode with status `error`, running.md, "Errors").

#### 3.4.2 `models`

| key | meaning | default | valid values | stated in |
|---|---|---|---|---|
| `models.buyers` | the model spec of every buyer seat without its own | the schema's | a model spec | prompts.md (its prices) |
| `models.managers` | the model spec of every manager seat without its own | the schema's | a model spec | prompts.md (its prices) |
| `models.buyer_seats` | per-seat model specs for buyers: `{seat index: model spec}` | `{}` | a mapping (null is `{}`); each key a seat index in 0..`participants.buyers` - 1, written as a YAML integer or as a string of its decimal digits with no sign, space or leading zero (`"0"`, `"12"`); any other key is refused, naming it; each value a model spec. If a seat is named both ways (`0` and `"0"`), both entries are checked and the integer key's entry is the seat's spec | prompts.md (that seat's prices) |
| `models.manager_seats` | the same for managers | `{}` | the same, with keys in 0..`participants.managers` - 1 | prompts.md (that seat's prices) |
| `models.summarizer` | the conversation summarizer's model spec | the schema's | a model spec, checked in every mode; when it makes real calls: messaging.md, "Under the offline mock" | never |

A seat's spec is its entry in `buyer_seats` / `manager_seats` if there is one, else its role's spec. The entry
replaces the role's spec as a whole (no key is taken from the role's spec). The seat's spec decides that seat's
calls, the dollar cost of its calls, and the prices stated in its own system prompt. The summarizer's calls are never
charged to any agent; they count toward `cost_cap_usd`.

#### 3.4.3 `llm`: call settings

| key | meaning | default | type and valid range |
|---|---|---|---|
| `llm.temperature` | the temperature of every agent spec that has no `temperature` key; null sends none | 0.8 | number >= 0, or null |
| `llm.max_tokens` | the per-call token limit of every spec (the summarizer's included) that has no `max_tokens` key | 64000 | integer >= 1 |
| `llm.timeout_s` | time limit of one attempt of one call, from its start (connecting included) to the last byte of the reply, in seconds (models.md, "One attempt and its time limit") | 1200 | number > 0 |
| `llm.max_retries` | how many times a failed attempt that can be retried is retried before the call fails | 5 | integer >= 0 |
| `llm.network_patience_s` | how long a lost network connection is waited out before the call fails, in seconds | 900 | number >= 0 |

`timeout_s`, `max_retries` and `network_patience_s` apply to every call, agents' and the summarizer's alike; a model
spec cannot set them. Which failures are retried and what a failed call does: models.md, "How a call can end" and "What
the market does with each ending"; rounds.md, "Lost to a failed model call". None of these values is stated to agents.

#### 3.4.4 `credits_per_usd`

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `credits_per_usd` | credits charged per dollar of model cost: every token charge to an agent is round(the call's dollar cost x `credits_per_usd`, 6) (turns.md, "Token charges"; models.md, "Token charges in credits"), and the token prices a system prompt states are the seat's `price_in_per_m` x `credits_per_usd` and `price_out_per_m` x `credits_per_usd` | 1 | number > 0 | prompts.md (`price_in`, `price_out`) |

The cost cap (`cost_cap_usd`) counts dollars and is not affected by `credits_per_usd`.

### 3.5 `time`

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `time.rounds_per_day` | rounds in a day; every participant in business takes one turn per round | 3 | integer >= 1 | prompts.md; view.md, "Lines and the header" ("ROUND {round} of {rounds_per_day} today") |
| `time.horizon_min_days` | the earliest possible last arrival day L; from it on, each day is the last with probability `horizon_end_prob`, up to the horizon cap (section 6.1; episode.md, "The last arrival day L"; randomness.md, "The last arrival day L (the horizon)") | 10 | integer >= 1 | never |
| `time.horizon_end_prob` | the per-day probability above | 0.1 | number, 0 < p < 1 | never |
| `time.close_after_due_days` | the market closes at the latest at the end of day max(L, the latest due day of any task + `close_after_due_days` - 1), L the last arrival day (section 6.6; episode.md, "The end of a day: the close rule") | 5 | integer >= 1 and >= escrow_close_days (section 4) | prompts.md |

### 3.6 `turns`

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `turns.max_actions` | most actions run from one act call (messages count among them); the rest are dropped (turns.md, "The action cap and the notes that open the results") | 6 | integer >= 1 | prompts.md; turns.md, "The description templates" (act's descriptions), "The action cap and the notes that open the results" (the cap note), "Its limit and its note" (the correction's limit) |
| `turns.memory_chars` | an agent's private memory is cut to its first `memory_chars` characters (turns.md, "Writing it") | 2000 | integer >= 1 | prompts.md; turns.md, "The description templates" (act's `memory` description), "The memory's lines in the results" (the cut note) |

### 3.7 `tasks`

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `tasks.first_day_tasks` | tasks each buyer gets on day 1 | 2 | integer >= 2 | prompts.md |
| `tasks.arrival_p` | on each later day up to the last arrival day, each buyer gets n new tasks with probability (1 - p) p^n, n = 0, 1, 2, ... (episode.md, "Task arrivals"; randomness.md, "Task counts (arrivals)") | 0.5 | number, 0 < p < 1, within 1e-12 of a fraction n/d with d <= 100, so the prompts' fractions are those of n/d (with one edge case: prompts.md, "7", the `tasks.arrival_p` row) | prompts.md |
| `tasks.worth_range` | a task's worth is drawn uniformly from the whole numbers low..high, both included | `[40, 80]` | pair of integers, 0 < low <= high | prompts.md (manager's: the range); each task's worth: episode.md, "Task arrivals" (the arrival notice); view-contracts.md |
| `tasks.due_days_range` | a task's due day is its arrival day + D, D drawn uniformly from the whole numbers low..high, both included | `[4, 9]` | pair of integers, 1 <= low <= high | never as a range; each task's due day: episode.md, "Task arrivals"; view-contracts.md |

### 3.8 `money`

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `money.start_amount` | the standard starting amount of every participant, buyers and managers alike; a participant's start balance is its wealth share x this (section 6.4) | 60 | a whole number, 0 < x < 10^6 (so the prompts' "{share} x {start_std} = {start_balance}" is exact) | prompts.md (the standard amount and the agent's own start balance); view.md, "Lines and the header" (the balance) |
| `money.task_budget` | a buyer's standard per-task budget; each task arriving in a buyer's queue (day 1's included) credits the buyer its share x this (section 6.4; episode.md, "Task arrivals") | 30 | a whole number, 0 < x < 10^6 (so the prompts' "{share} x {budget_std} = {budget}" is exact) | prompts.md (buyer's: the standard amount and its own budget); episode.md, "Task arrivals" (the arrival notice); view.md, "Your accounts so far (credits)" |
| `money.share_range` | each participant's wealth share is drawn uniformly between low and high (randomness.md, "Wealth shares") | `[0.6, 1.4]` | pair of numbers, 0 < low <= high; low = high only as `[1, 1]`; each with at most `money.share_decimals` decimals (round(x, `share_decimals`) = x) and at most 6 significant digits (`G` reads back as x), so a drawn share lies within the range the prompts state | prompts.md |
| `money.share_decimals` | the drawn share is rounded to this many decimals before use | 2 | integer, 0..2 | prompts.md (the share, in `F2`) |

### 3.9 `value`

f(q) = clip((q - q0) / (10 - q0), 0, 1)^exponent is the fraction of a task's worth a result of quality q is worth to its
buyer on time; each day late multiplies it by max(0, 1 - late_decay x days late) (processes.md, "The value of a result
to its buyer"; episode.md, "Settlement: the value each contract realises").

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `value.q0` | the quality at and below which a result is worth nothing | 1 | number, 0 <= q0 < 10 | prompts.md; view-contracts.md, "Shared texts" (the buyer's on-time values at quality 5..10) |
| `value.exponent` | the curve's exponent | 2 | number > 0 | prompts.md; view-contracts.md, "Shared texts" |
| `value.late_decay` | the share of the on-time value lost per day late; also the escrow's cut of the manager's pay per day late, and the source of escrow_close_days and the full-pay cap (section 6.2) | 0.2 | number, 0 < late_decay <= 1 | prompts.md; processes.md, "The lateness close" (its notice); view-contracts.md, "The lateness clause on posts and uncontracted tasks" and the contract lines |

### 3.10 `contracts`

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `contracts.bid_window_rounds` | w: every bid on a post that was posted in round index g, whenever the bid was made, can be accepted (and is shown to the buyer) from round index g + w + 1; editing the post does not move this; bids stay sealed until then (section 6.6; processes.md, "Sealed bids and their opening") | 1 | integer, 1 <= w <= horizon cap x `time.rounds_per_day` | prompts.md; view-contracts.md, "The bids field on the buyer's own post" ("sealed until ..."); processes.md, "Sealed bids and their opening" (the opening notice) |
| `contracts.min_contract_days` | a contract's deadline is at least this many days after the day it is signed, capped at the full-pay cap (section 6.3; processes.md, "The contract's deadline") | 4 | integer >= 1 | prompts.md; actions-buyer.md, actions-manager.md, view-contracts.md (each contract's deadline) |
| `contracts.message_chars` | a delivery's or a rejection's message is cut to its first `message_chars` characters, silently (actions-manager.md, "deliver"; actions-buyer.md, "reject_submission") | 500 | integer >= 1 | prompts.md |
| `contracts.escrow.fee_rate` | the buyer's non-refundable setup fee when an escrow bid is accepted: round(fee_rate x price, 2) | 0.10 | number, 0 <= fee_rate <= 1 | prompts.md; actions-buyer.md, "accept_bid" (its result and its balance refusal); view.md, "Your accounts so far (credits)" (the `escrow fees` category) |
| `contracts.escrow.review_days` | a submission the buyer has neither approved nor rejected is auto-released at the start of the day `review_days` days after the day it was submitted (processes.md, "Automatic release") | 2 | integer >= 1 | prompts.md; view-contracts.md; processes.md, "Automatic release" (its notices); actions-manager.md, "deliver" (the automatic release day) |
| `contracts.escrow.dispute_window_days` | a rejection made on day d can be disputed by the manager through the end of day d + `dispute_window_days` (processes.md, "Dispute and the arbiter's ruling") | 1 | integer >= 1 (with 0, a rejection made in a day's last round could never be disputed) | prompts.md; actions-manager.md, "dispute" (its refusal); actions-buyer.md, "reject_submission", "end_contract"; view-contracts.md |
| `contracts.escrow.dispute_fee_rate` | the dispute fee the manager pays to dispute, round(dispute_fee_rate x price, 2); the arbiter's fee equals it (processes.md, "Dispute and the arbiter's ruling") | 0.5 | number > 0 (with 0 the ruling texts would be false); a fee that rounds to 0 on a small price is still possible and is not refused (what happens then: processes.md, "Dispute and the arbiter's ruling") | prompts.md; actions-manager.md, "dispute"; actions-buyer.md, "pay_escrow_arbiter"; processes.md, "Dispute and the arbiter's ruling"; view-contracts.md, "The escrow days line" |
| `contracts.escrow.arbiter_sd` | standard deviation of the arbiter's one reading of a disputed result (processes.md, "Dispute and the arbiter's ruling") | 0.5 | number >= 0 | prompts.md |

### 3.11 `production`

The laws of a run's quality, days and cost, in which these parameters appear (m is the run's cost multiplier, mean
1): processes.md, "The laws of a run"; draws: randomness.md, "A run's quality, days and cost".

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `production.cost_ref_worth` | the reference worth every run cost is priced against | 80 | number > 0 (0 always fails section 4's employee-cheaper rule) | never (only the costs it yields) |
| `production.sig_t` | log-sd of a run's days | 0.35 | number >= 0 | prompts.md (manager's duration lists) |
| `production.sig_c` | log-sd of a run's cost multiplier | 0.25 | number > 0 | prompts.md (manager's: the four cost spreads); view-contracts.md, "The manager's "Your production"" (the same spreads) |
| `production.employee.mu`, `.sd` | the employee's fresh-run quality law | 6.0, 2.5 | mu: number, 0 <= mu <= 10; sd: number > 0 | prompts.md (manager's) |
| `production.employee.gain`, `.sd_ref` | the employee's refinement law | 4.0, 1.5 | gain: number, 0 < gain <= 10; sd_ref: number > 0 | prompts.md (manager's) |
| `production.employee.t_med`, `.t_ref_med` | median days of the employee's fresh runs and refinements | 1.0, 0.7 | numbers > 0 | prompts.md (manager's duration lists) |
| `production.employee.fee`, `.cost_frac`, `.ref_cost_frac` | the employee's run cost: fresh fee + cost_frac x cost_ref_worth x m, refine fee + ref_cost_frac x cost_ref_worth x m | 1.0, 0.0375, 0.0225 | fee: number > 0 (it keeps every expected run cost above 0, which the cost spreads divide by); cost_frac, ref_cost_frac: numbers > 0 (the fee is not scaled by m, so without them the employee's run cost would not vary) | prompts.md (manager's expected costs and cost spreads); view-contracts.md, "The manager's "Your production""; actions-manager.md, "delegate_run and run_myself" (each run's cost) |
| `production.employee.capacity` | runs a manager's employee can have in flight at once | 2 | integer >= 1 | prompts.md (manager's); view-contracts.md, "The manager's "Your production"" (the capacity line); actions-manager.md, "delegate_run and run_myself" (refusal when full) |
| `production.manager.mu`, `.sd` | the manager's own fresh-run quality law | 7.0, 1.2 | as the employee's | prompts.md (manager's) |
| `production.manager.gain`, `.sd_ref` | the manager's own refinement law | 7.0, 0.8 | as the employee's | prompts.md (manager's) |
| `production.manager.t_med`, `.t_ref_med` | median days of the manager's own fresh runs and refinements | 2.5, 1.0 | numbers > 0 | prompts.md (manager's duration lists) |
| `production.manager.k` | the manager's own fresh-run cost: k x cost_ref_worth x m | 0.1 | number > 0 (0 always fails section 4's employee-cheaper rule) | prompts.md (manager's expected costs); view-contracts.md, "The manager's "Your production""; actions-manager.md, "delegate_run and run_myself" (as costs) |
| `production.manager.ref_cost_fixed` | the manager's own refinement cost: ref_cost_fixed x m | 14.0 | number > 0 (0 always fails section 4's employee-cheaper rule) | prompts.md (manager's expected costs); view-contracts.md, "The manager's "Your production""; actions-manager.md, "delegate_run and run_myself" (as costs) |
| `production.manager.capacity` | runs the manager can do itself at once | 1 | integer >= 1 | prompts.md (manager's); view-contracts.md, "The manager's "Your production"" (the capacity line); actions-manager.md, "delegate_run and run_myself" (refusal when busy) |

### 3.12 `tests`

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `tests.manager_sd` | standard deviation of a manager's test reading (test_result) | 0.5 | number > 0 | prompts.md; processes.md, "The readings text" (the standard error), shown in actions-manager.md, "test_result" and view-contracts.md, "Shared texts" |
| `tests.manager_cost` | credits charged per manager reading | 1 | number >= 0 | prompts.md; view-contracts.md, "The manager's "Your production""; actions-manager.md, "test_result" (the result text); view.md, "Your accounts so far (credits)" (the `tests` category) |
| `tests.buyer_sd` | standard deviation of a buyer's test reading (test_delivery) | 2.0 | number > 0 | prompts.md; processes.md, "The readings text" (the standard error), shown in actions-buyer.md, "test_delivery" and view-contracts.md, "Shared texts" |
| `tests.buyer_cost` | credits charged per buyer reading | 1 | number >= 0 | prompts.md; actions-buyer.md, "test_delivery" (the result text); view.md, "Your accounts so far (credits)" (the `tests` category) |
| `tests.decimals` | every reading (tests and the arbiter's) and a delivery's stated quality is rounded to this many decimals, and readings are shown with this many | 1 | integer, 0..3 | prompts.md; actions-buyer.md, "test_delivery"; actions-manager.md, "test_result", "deliver" (the stated quality); processes.md, "Dispute and the arbiter's ruling" (the arbiter's reading); view-contracts.md, "Shared texts" (readings) |

### 3.13 `messaging` (in effect only when `message_channels` is not `off`)

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `messaging.message_chars` | a sent message is cut to its first `message_chars` characters | 500 | integer >= 1 | prompts.md (sending roles); messaging.md, "A sent message" (send_message's result) |
| `messaging.recent` | a conversation shows up to this many messages in full; when it has more, its earliest messages are summarized in groups of this many (messaging.md, "Batching and summaries") | 10 | integer >= 2 | prompts.md (sending roles: `recent`) |
| `messaging.summary_chars` | a summary shown in a view is cut to at most this many characters (messaging.md, "How a summary is shown") | 2000 | integer >= 50 | never |

### 3.14 `compute` (the agents' program runner; turns.md, "Running a program")

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `compute.cpu_s` | a program is stopped after this many seconds of CPU time | 5 | integer >= 1 | prompts.md; turns.md, "The description templates" (compute's `code` description), "Limits and statuses" (the status notes) |
| `compute.wall_s` | a program is stopped after this many seconds in all | 10 | integer >= 1 | prompts.md; turns.md, "The description templates", "Limits and statuses" |
| `compute.memory_mb` | a program is stopped when its memory exceeds this many MB | 512 | integer >= 1 | turns.md, "Limits and statuses" (the memory-limit status note); not in the system prompts |
| `compute.output_kb` | a program is stopped when its output (standard output and error together) exceeds `output_kb` x 1024 bytes | 64 | integer >= 1 | prompts.md; turns.md, "Limits and statuses", "The output text" |
| `compute.output_chars` | only the first `output_chars` characters of a program's output come back | 4000 | integer >= 1 | prompts.md; turns.md, "The description templates", "The output text" |
| `compute.code_chars` | a program longer than this many characters is refused | 20000 | integer >= 1 | prompts.md; turns.md, "The description templates", "Programs refused without running" |

### 3.15 `view` (listing limits; view.md, view-contracts.md)

| key | meaning | default | type and valid range | stated in |
|---|---|---|---|---|
| `view.finished_shown` | the latest finished contracts listed one per line; a buyer's view also lists, besides these, up to `view.overflow_ids` older ones delivered and not paid in full | 5 | integer >= 1 | view-contracts.md, "Your finished contracts" (its title) |
| `view.open_shown` | open posts and uncontracted tasks listed in full; the rest are named only | 8 | integer >= 1 | view-contracts.md, "Which posts, in what order", "Which tasks, in what order" |
| `view.readings_shown` | latest readings listed per result (the mean and count cover all) | 5 | integer >= 1 | processes.md, "The readings text"; shown in actions-manager.md, "test_result", actions-buyer.md, "test_delivery" and view-contracts.md, "Shared texts" |
| `view.overdue_days` | a contract with no delivery or submission is overdue once today minus its listing deadline (the contract's deadline for a plain contract, the task's due day for an escrow) is at least this; an overdue contract is finished unless an earlier rule keeps it active (view-contracts.md, "Active or finished") | 5 | integer >= 1 | view-contracts.md, "Active or finished" |
| `view.recent_days` | in a manager's view, a contract that is not ended and holds no escrow price stays active while a result on it that arrived on or after day today - this has never been delivered or submitted on it | 3 | integer >= 1 | view-contracts.md, "Active or finished" |
| `view.messages_shown` | the latest deliveries or submissions that carried a non-empty message or a stated quality, listed in a contract's message lines on the buyer's side; the earlier ones are counted in one line. Submission lines and rejection messages are not limited by it, and payments carry no message | 3 | integer >= 1 | view-contracts.md, "Message lines" |
| `view.payments_shown` | payments listed in the Payments section | 8 | integer >= 1 | view.md, "Payments" (its title) |
| `view.overflow_ids` | most IDs named for what is not listed in full; also the most older finished contracts, delivered and not paid in full, a buyer's view lists with their detail besides the latest `view.finished_shown` | 20 | integer >= 1 | view-contracts.md, "Shared texts" (the list of names), "Order, the listed contracts and the title" (the kept contracts and the title's count) |

## 4. Checks across parameters

Besides each key's own range (section 3), the implementation must refuse a config that breaks any of these rules,
naming the keys involved. The system prompts' wording holds only inside them; the ranges that wording relies on are
listed in prompts.md, "Range checks the fixed wording relies on", and each is a check of section 3 or of this section.

| rule | keys to name |
|---|---|
| `tests.manager_sd` < `tests.buyer_sd`, and `tests.manager_cost` = `tests.buyer_cost` | `tests.*` |
| The employee's runs cost less than the manager's own, fresh and refine, at their expected costs (section 6.5): `employee.fee + employee.cost_frac x cost_ref_worth` < `manager.k x cost_ref_worth`, and `employee.fee + employee.ref_cost_frac x cost_ref_worth` < `manager.ref_cost_fixed` | `production.*` |
| Every kind of run has a listable duration law: for each of the four (employee fresh with `employee.t_med`, employee refine with `employee.t_ref_med`, manager fresh with `manager.t_med`, manager refine with `manager.t_ref_med`, all with `sig_t`), some day count d other than 1000 has a share >= 0.05 in the days mix (section 6.7) | `production.sig_t`, the `t_med` / `t_ref_med` keys |
| Some kind of run can take more than 1 day: in at least one of the four days mixes some d > 1 has a share > 0 | the same |
| For each worker (`production.employee`, `production.manager`), Φ(b) − Φ(a) > 0 in double precision, with a = (0 − mu) / sd, b = (10 − mu) / sd and Φ the standard normal CDF with the accuracy deep in the lower tail that prompts.md, "Conventions for the field definitions" requires (for example `erfc(−x / √2) / 2`); otherwise the manager's prompt cannot state the fresh-run mean and shares, which divide by it (prompts.md, "The workers' fresh-run figures"). Example refused: sd = 1e17 with mu = 6 | `production.<worker>.mu`, `production.<worker>.sd` |
| `time.close_after_due_days` >= escrow_close_days (section 6.2) | `time.close_after_due_days`, `value.late_decay` |
| The horizon cap (section 6.1) is at most 1000 | `time.horizon_min_days`, `time.horizon_end_prob` |
| `contracts.bid_window_rounds` <= horizon cap x `time.rounds_per_day` | `contracts.bid_window_rounds` |
| Each key of `models.buyer_seats` is a seat index < `participants.buyers`; each key of `models.manager_seats` < `participants.managers` | the seat key |
| Agent specs of a provider other than `mock` and `ollama` give both prices, each > 0 (section 3.4.1) | the spec's path, e.g. `models.manager_seats.1.price_in_per_m` |
| Every spec (agents' and the summarizer's) with provider `openai_compatible` gives `base_url` (section 3.4.1) | the spec's path, e.g. `models.summarizer.base_url` |

## 5. Fixed values (not parameters)

A config cannot change these; the files named define them. Quality scale 0 to 10 (processes.md, "Workers, runs and
results"); result labels `R-` + 3 lowercase hex digits (randomness.md, "Result labels"); a run's quality independent
of its duration and its cost (processes.md, "The laws of a run"); one successful program per turn, with one more
attempt after a failed one (turns.md, "The free retry after a failed program"); the arbiter rules at the start of the
day after the dispute (processes.md, "Dispute and the arbiter's ruling"); the summary word budget min(200, 40 + 15 n)
(messaging.md, "The request"); the abort after more than 5 x `time.rounds_per_day` turns lost to failed model calls
(section 6.8); the horizon cap's 1% reach rule and the 1000-day limit (section 6.1); the duration lists' thresholds
5%, 0.5% and 1e-4 (prompts.md, "Run durations: the duration text"; section 6.7); agent IDs `B-`/`M-` + 3 lowercase hex
digits (episode.md, "Participants, IDs and seat order"); the 2000-character cut of each function call's arguments
when a correction sends a reply back as text (turns.md, "How the body joins the conversation"; it does not follow
`turns.memory_chars`).

## 6. Derived values

Values that depend only on the config (6.1, 6.2, 6.5, 6.7, 6.8) or also on an episode's draws and events (6.3, 6.4,
6.6). Every
formula is exact as written, in double-precision arithmetic, except day numbers (6.3, 6.6), which are whole numbers
computed exactly whatever the size of an integer parameter (an enormous `contracts.min_contract_days` gives the
full-pay cap as the deadline). 6.7's algorithm decides (its prose describes it in exact arithmetic); 6.1 is defined
in exact arithmetic, with a tolerance at boundaries, and its algorithm is an example. The math functions (`ln`,
`log1p`, `exp`, Φ via `erfc`) are the platform's double-precision functions; any implementation accurate to a few
units in the last place is acceptable (prompts.md, "Conventions for the field definitions", "Accuracy of the math
functions"). At the defaults no derived value is near a boundary (6.1: x = 43.71, n = 44).

Finiteness at load. The values that depend only on the config must be finite for an accepted config: 6.1's cap,
6.2's escrow_close_days (and with it 6.3's offset `escrow_close_days - 1`, the only part of 6.3 known at load), 6.5's
expected costs and 6.7's shares; a config for which one is not is refused, naming the keys of its formula. A value
is checked as its formula gives it: an infinite intermediate that the formula takes to a finite value is no failure
(6.7's ln(d / t) for a `t_med` so small that d / t is infinite gives Φ = 1, so share(1) = 1; 6.1's x is infinite for a
`horizon_end_prob` near 0, and the cap is then refused as above 1000, not as infinite). The check covers no draw or
event of any episode.

Finiteness during an episode. The ranges do not bound every parameter from above, so extreme values can still make
an amount computed during an episode infinite or not a number: a start balance or per-task budget (6.4), a run's
cost, a charge, a credit, a balance, a payment, a value, a score or a sum of these. Such a value must never be
used, shown or logged as an amount: at the computation that yields it, the episode stops with status `error`, as
running.md, "4. What stops an episode before its close" and "4.3 Errors", specify (the day in progress is
abandoned; its effects do not count), and the error text names the value that was not finite.

### 6.1 The horizon cap

The last arrival day is drawn per episode (episode.md, "The last arrival day L"; randomness.md, "The last arrival day L
(the horizon)") and never exceeds the horizon cap, cap = `horizon_min_days` + n, where n is the smallest whole number
>= 0 with (1 - p)^n < 0.01, p = `horizon_end_prob` (the double's exact value, in exact arithmetic): the cap is the
first day the last arrival day reaches with probability under 1%. Tolerance: if (1 - p)^m lies within 1e-12 x 0.01
of 0.01 for a whole number m, both m and m + 1 are accepted as n, in the cap and in the check that refuses a cap
above 1000 (double arithmetic cannot settle such a case). Elsewhere n must be exact.

An example of computing n in doubles (the reference implementation's way):

```
log_stay = ln(1 - horizon_end_prob)            (computed as log1p(-horizon_end_prob))
x = ln(0.01) / log_stay
if not (x < 1000): n = 1001                    (the cap is then refused)
else:
    n = max(0, ceil(x))
    while n <= 1000 and exp(n x log_stay) >= 0.01: n = n + 1
```

It gives the exact n except at a few values of `horizon_end_prob` within rounding error of a boundary, where it
gives the neighbouring n, which the tolerance accepts (examples: 0.40051574968105896 gives 9 where the exact n is 10,
(1 - p)^9 being 3.3e-16 x 0.01 above 0.01; 0.021588999306809686 gives 212 where the exact n is 211). A cap above
1000 is refused (section 4). Defaults: x = 43.71, n = 44, cap = 54. Other values with
`horizon_min_days` 10: `horizon_end_prob` 0.05 gives 100, 0.2 gives 31, 0.5 gives 17. With `horizon_min_days` 10,
any `horizon_end_prob` below about 0.00464 is refused. The cap is never stated to agents. The market itself can run
past the cap: it can stay open until the end of day max(L, the latest due day + `time.close_after_due_days` - 1)
(episode.md, "The end of a day: the close rule").

### 6.2 escrow_close_days

```
escrow_close_days = ceil(1 / late_decay - 1e-9)
```

The number of days after a task's due day from which an escrow contract takes no submission, since the lateness
factor max(0, 1 - late_decay x days late) is then 0, or a positive amount below 1e-9 where rounding leaves one (the
1e-9 makes a 1 / late_decay up to 1e-9 above a whole number k count as k). An escrow contract still holding its price closes unreleased at the start of the day
`escrow_close_days` days after the task's due day, or later if something is still pending then (processes.md, "The
lateness close"). Default: ceil(5 - 1e-9) = 5. Examples: 0.25 gives 4, 0.3 gives 4, 1/3 gives 3, 1 gives 1, 0.1
gives 10.

### 6.3 The full-pay cap and the contract deadline

The full-pay cap is the due day + `escrow_close_days - 1` (default: the due day + 4). It is the upper bound of every
contract's deadline. A contract's deadline (both forms) is

```
deadline = min(max(due_day, signing_day + min_contract_days), due_day + escrow_close_days - 1)
```

(processes.md, "The contract's deadline"; actions-buyer.md, "accept_bid"). Defaults: min(max(due, signing day + 4),
due + 4).

### 6.4 Wealth shares, start balances and per-task budgets (per episode, per seat)

Owned by episode.md, "Wealth shares, start balances and per-task budgets" (the draw: randomness.md, "Wealth shares"):
`share = round(u, share_decimals)`, u = low + (high - low) x U with [low, high] = `money.share_range` and U uniform
on [0, 1), and

```
start balance   = start_amount                    if share = 1 exactly
                = round(start_amount x share, 2)  otherwise
per-task budget = task_budget                     if share = 1 exactly       (buyers only)
                = round(task_budget x share, 2)   otherwise
```

Example at the defaults: share 1.36 gives start balance 81.60 and per-task budget 40.80. A share can round to 0 (a
low end of `money.share_range` near 0 with few decimals, e.g. 0.004 with `share_decimals` 2); the config is not
refused for that, and that seat's amounts are then 0 (what a manager's balance of 0 means: episode.md, "Out of
business").

### 6.5 Expected run costs

The cost of a run with multiplier m = 1 (the mean of m), used by the check in section 4 and stated to managers:

| run | expected cost | default |
|---|---|---|
| employee fresh | `employee.fee + employee.cost_frac x cost_ref_worth` | 1 + 0.0375 x 80 = 4.00 |
| employee refine | `employee.fee + employee.ref_cost_frac x cost_ref_worth` | 1 + 0.0225 x 80 = 2.80 |
| manager fresh | `manager.k x cost_ref_worth` | 0.1 x 80 = 8.00 |
| manager refine | `manager.ref_cost_fixed` | 14.00 |

The comparison in section 4 uses these doubles unrounded. A charged run cost is round(expected cost formula with the
drawn m, 2) (processes.md, "The laws of a run").

### 6.6 Bid acceptance and the market's latest close

- Round index g = (day - 1) x `rounds_per_day` + round. Every bid on a post that was posted in round index g, whenever
  the bid was made, can be accepted from round index g + `bid_window_rounds` + 1 (default g + 2; processes.md,
  "Sealed bids and their opening").
- The market's last possible day is max(L, max(due day of every task) + `close_after_due_days` - 1) (episode.md,
  "The end of a day: the close rule").

### 6.7 A run's days mix (for the checks in section 4 and the manager's duration lists)

For a median t and log-sd s = `production.sig_t`, the share of each day count d:

```
cum(d) = Phi(ln(d / t) / s)    if s > 0          (Phi: the standard normal CDF)
       = 1 if t <= d else 0    if s = 0
below = 0; d = 1
while below < 1 - 1e-9 and d < 1000:
    share(d) = cum(d) - below
    below = cum(d); d = d + 1
if below < 1 - 1e-9: share(1000) = 1 - below
```

Every d the loop visits gets a share (possibly 0). How the manager's prompt words the shares: prompts.md, "Run
durations: the duration text". Defaults: employee fresh (t 1.0) 1: 0.5000, 2: 0.4762, 3: 0.0230, 4: 0.0008, ...
(through d = 9); employee refine (0.7) 1: 0.8459, 2: 0.1527, 3: 0.0013, ... (through d = 6); manager fresh (2.5) 1:
0.0044, 2: 0.2575, 3: 0.4369, 4: 0.2116, 5: 0.0658, ...; manager refine (1.0) as employee fresh. Examples refused by
the listability check: t = 50 with s = 0.35 (no count reaches 5%). With s = 0: any t <= 999 is accepted (one count,
ceil(t), at 100%), though if every t is at most 1 the "more than 1 day" check fails; any t > 999 is refused (the whole
share sits at d = 1000, which no listable count may be).

### 6.8 The lost-turn limit

When more than 5 x `time.rounds_per_day` turns (default 15), counted over the whole episode and all agents, have been
lost to failed model calls, the episode is aborted with status `aborted_lost_turns` (rounds.md, "Lost to a failed
model call"; running.md, "Too many lost turns"). Turns lost as format errors do not count.
