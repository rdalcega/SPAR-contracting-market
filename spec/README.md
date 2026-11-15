# The contracting market: specification

This folder specifies a simulated market in which language-model agents trade work. **Buyers** own tasks and post
them on a forum. **Managers** bid on them, have results produced (by an employee or by themselves), test them and
deliver them. Buyers pay on plain contracts, or through an escrow that the buyer approves or rejects, with disputes
settled by an arbiter. Every model reply an agent receives is charged to its balance. Each agent is scored on its own
outcome.

Build a lean implementation of exactly this market from this folder alone. "Exactly" means:

- **The same rules, timings, distributions, costs and outcomes.**
- **Byte-identical text for everything a model reads**, at the default configuration: system prompts, per-turn
  views, function (tool) definitions, function results, correction messages, action results, notifications and
  the summarizer's prompts. Where a text depends on a parameter, the spec gives the template and the rule for every
  valid value. Byte-identical means the same text from the same market state: what models and programs write
  (agents' messages and memories, the summarizer's summaries, a program's output) is carried as written, a program's
  traceback and error wording are the interpreter's (`turns.md`, "6.4 The output text"), and drawn values (IDs,
  shares, ...) differ with your generator (`randomness.md`).
- **Every parameter configurable**, with the defaults in `config.md`.
- **One switch**, `message_channels` (off | managers | buyers | within_roles | all; default off). It decides who may
  message whom. There is no other switch.
- **Model access behind one provider-agnostic interface**, with an OpenRouter implementation as the default
  (`models.md`). All four providers of `models.md`, "3. Providers" are required: `openrouter`, `mock`, and `ollama`
  and `openai_compatible` for models served elsewhere or by yourself (the market is meant to be run with smaller
  models than the default's). Swapping providers, or serving models yourself, must change nothing but what the provider returns
  (replies, token counts, costs) and what follows from it. The offline mock's own exceptions are in `running.md`, "7.
  The offline mock model".
- **Keyed randomness.** Every draw depends only on the episode's seed and a key naming what it is for, so episodes of
  one seed stay draw-matched across modes and models; the generator is your choice (`randomness.md`).
- **Outputs.** Per episode, a complete log from which every text, view and score can be rebuilt, and a summary; runs
  over a list of seeds, in parallel, under a cost cap, with resume and a free offline mock model (`running.md`).

## How to read this spec

- "Must" is a requirement. Everything stated is behaviour to reproduce unless it is marked as an example.
- A fenced block that quotes a text (anything a model reads, or a text the spec calls exact) is byte-exact apart from
  its `{placeholders}`; each placeholder is defined next to it (or in `prompts.md` for the system prompts), and any
  other brace is literal. Line breaks inside such a block are part of the text. Fenced blocks of formulas,
  pseudo-code, the config schema or request bodies are notation, not text.
- **Deliberate quirk** marks a text or behaviour that may look like a mistake but is what the market does. Reproduce
  it exactly; do not fix it.
- Orderings, tie-breaks, rounding and exact texts are behaviour. Data structures, modules, languages, libraries,
  concurrency mechanisms, the random generator, the names of the log's record types and fields (and how it encodes
  values) and the sandbox technology are your choice, as long as the observable behaviour matches. The log's form
  (append-only JSON lines, in the order things happen on the market) and its content are fixed (`running.md`, "6.2
  The log: form" and "6.3 The log: required content").
- Parameters are written `section.key` (for example `contracts.escrow.fee_rate`), as defined in `config.md`.

## Files

| File | What it specifies |
|---|---|
| `config.md` | Every parameter: meaning, default, valid range, derived values; model specs; the switch; config errors |
| `randomness.md` | The keyed-draw contract and every random draw: its key, distribution and timing |
| `episode.md` | An episode outside a round: start, the day's processing in order, task arrivals, the hidden horizon, the close, settlement, scores, out of business |
| `rounds.md` | A round: simultaneous turns, the action phase (validity, priority, clashes), corrections, money landing, lost turns |
| `turns.md` | One turn as a model conversation: messages, the compute and act functions, running programs, reading act, format errors, corrections, token charges |
| `actions-buyer.md` | The rules common to all actions, and each buyer action: arguments, checks, effects, exact results and notifications |
| `actions-manager.md` | Each manager action, and pay (both roles) |
| `processes.md` | The market's own mechanics: production, readings, deadlines and lateness, the value of a result, the plain contract, the escrow's states and timers, when a contract can be ended, sealed bids and their opening, and their notices |
| `messaging.md` | Everything `message_channels` changes outside the system prompts: sending, conversations in views, summaries and the summarizer |
| `view.md` | The per-turn view's frame and general sections, for both roles |
| `view-contracts.md` | The view's market sections: the forum, the buyer's tasks and posts, production, active and finished contracts |
| `prompts.md` | The system prompts: how they are built from `text/`, and every field's definition |
| `models.md` | The model layer: the interface, the OpenRouter default, retries, costs, the summarizer's calls, the cost cap |
| `running.md` | Running episodes: the command line, seeds, parallel episodes, aborts, resume, the log's required content, the summary and core metrics, the offline mock model |
| `text/` | The system-prompt templates, one per role and mode, exact |
| `examples/` | Exact texts the reference implementation sent, for comparison (see below) |
| `vectors/` | Test vectors: inputs and the reference implementation's exact outcomes for reading act calls (`turns.md` 7) and for ordering a round's actions (`rounds.md` 6-8); `vectors/README.md` defines them |

## Number formats and rounding

Amounts are IEEE-754 double-precision numbers. Every number an agent reads is written in one of these formats; the
spec names the format wherever a number appears.

| Name | Definition | Examples |
|---|---|---|
| `G` | C's `%g`: 6 significant digits, trailing zeros and a trailing decimal point removed; exponent form (`1e+06`, `1.23457e+06`) when the decimal exponent is below -4 or at least 6; negative zero gives `-0` | `20`, `20.25`, `0.15`, `1.875` |
| `CR` | `F0` if the value is a whole number (its digits, never exponent form), otherwise `F2` | `6`, `1.80`, `5.55`, `1234567` |
| `CREDITS` | `G` followed by ` credit` if the value equals 1 exactly, otherwise ` credits` (the value decides, not the written `G`: 0.9999999 gives `1 credits`) | `1 credit`, `2.8 credits` |
| `F0`, `F1`, `F2`, `Fn` | Fixed n decimals, correctly rounded from the double's exact value (ties to even). A negative value keeps its minus sign even when it rounds to zero (`-0.001` gives `-0.00`); negative zero gives `-0.00` (in `F2`) | `81.60`, `5.7` |
| `REPR` | The shortest decimal string that reads back as the same double: positional with at least one digit after the point when the decimal exponent is between -4 and 15 (`25.0`, `24.55`), exponent form otherwise (`1e+16`, `1e-05`); negative zero is `-0.0`, NaN is `nan`, the infinities are `inf` and `-inf`. This is Python's `repr` of a float | `25.0`, `1.0` |
| `PCT` | `G` of round(100 × x, 6), followed by `%` | `20%`, `12.5%` |
| `PCT0` | The integer nearest to the double 100 × x, ties to even (Python's `round(100 * x)`), followed by `%` | `20%`; 0.125 gives `12%` |
| `FRAC` | The fraction nearest to x with a denominator of at most 10^6, written `n/d` in lowest terms (`n` alone when d = 1). No double is equally near two such fractions, so no tie rule is needed | `1/2`, `3/16` |
| `INT` | A plain integer | `4` |

`round(x, n)` means: take the exact value of the double x, round it to n decimal places with ties to even, and
return the nearest double. Python's `round` does exactly this. Every amount "rounded to the cent" uses round(x, 2);
the spec states each other rounding where it applies.

`SUM`, the sum of a list of doubles, is compensated (Neumaier) summation in the list's order, in double arithmetic:
start with hi = 0 and lo = 0; for each x in order, t = hi + x, then lo = lo + ((hi - t) + x) if |hi| >= |x|, else
lo = lo + ((x - t) + hi), then hi = t; the sum is hi + lo if lo is non-zero and finite, else hi. (Python's built-in
`sum` over floats computes exactly this, from version 3.12.) Plain left-to-right addition is not equivalent and can
change a shown figure: 6.2, 2.7, 6.8, 4.6 give SUM 20.3, plain addition 20.299999999999997. Each file says where a
sum is a `SUM`.

Values an agent sent (an action's arguments) are quoted back in two conversions, `TEXT` and `PY`, defined below. A
JSON number written without a fraction or exponent is an integer, exact
(`123456789012345678901234567890` stays those digits; no 64-bit or double limit); any other number is a double. Two
agent inputs are outside this spec: (a) a JSON integer too large in magnitude for a finite double (2^1024 − 2^970 or
more, either sign) given as a threshold or a stated quality, and (b) an integer literal of more than 4,300 digits, or
JSON nested more than 400 levels deep, anywhere in a reply. An implementation may give such a value the usual answer
(preferred): for (a), refuse the action with that field's usual refusal text; for (b), treat the literal or the nested value as JSON that cannot be decoded (`turns.md`, "7.2
Reading JSON"). Or it may behave as the reference implementation does, which can end the episode with status `error`
on either. No particular outcome is required. An amount or price that large is not such a case: it reads as an
infinite double and is refused (`actions-buyer.md`, "1.10 Amounts (bid's price, pay's amount)"). JSON reading
accepts `NaN`, `Infinity` and `-Infinity` as doubles (`turns.md`, "7.2 Reading JSON").

- `TEXT(v)`: a string as it is; an integer in decimal digits (`-` if negative; `-0` is the integer 0, so `0`); a
  double in `REPR` (`nan`, `inf`, `-inf`, `-0.0` included); `true` and `false` as `True` and `False`; `null` as
  `None`; a list or an object as `PY(v)`.
- `PY(v)`: what Python's `repr` prints: a string in single quotes, or in double quotes when it contains `'` and no
  `"`, with `\` as `\\`, the chosen quote as `\'`, newline as `\n`, carriage return as `\r`, tab as `\t`, and every
  other non-printable character as `\x` and 2 hex digits if its code point is below 0x100, else `\u` and 4 if below
  0x10000, else `\U` and 8 (hex digits lowercase). The non-printable characters are those of Unicode general
  categories Cc, Cf, Cs, Co, Cn, Zl, Zp and Zs, except the space U+0020 (Python's `str.isprintable`); every other
  character is written as it is (`é`, `😀`). Numbers, booleans and `null` as in
  `TEXT`; a list as `[`, its items in `PY` joined by `, `, then `]`; an object as `{`, its `PY(key): PY(value)` pairs
  joined by `, `, then `}`.

Examples: `TEXT("contract 1")` is `contract 1`; `TEXT(3)` is `3`; `TEXT(3.0)` is `3.0`; `PY("7.5")` is `'7.5'`;
`PY(true)` is `True`; `PY([7])` is `[7]`; `PY([null, NaN])` is `[None, nan]`; `PY("a'b")` is `"a'b"`;
`PY("a'\"b")` is `'a\'"b'`; `x`, U+00A0 and U+200B in a string give `'x\xa0\u200b'`.

A "character" is a Unicode code point. "Whitespace" is what Python's `str.isspace` accepts: U+0009 to U+000D, U+001C
to U+001F, U+0020, U+0085, U+00A0, U+1680, U+2000 to U+200A, U+2028, U+2029, U+202F, U+205F and U+3000; "surrounding
whitespace removed" (trimmed) removes these from both ends. "Lowercased" and "uppercased" are Unicode's full default
case mappings, as Python's `str.lower` and `str.upper` apply them (one character can become several: `ﬀ` uppercases
to `FF`). General categories and case mappings are those of Unicode 17.0, the version the reference
implementation uses.

**The singular rule.** A count or amount that comes from a configurable parameter and is followed by its unit takes
the singular unit when it is written as exactly `1` ("1 day", "1 second", "1 character", "1 action", "1 slot", "1
run", "1 round", "1 credit"; a cent amount written `1.00` keeps the plural). The spec marks each text this applies to
with *(singular rule)*. The `CREDITS` format is close to it but not the same: it looks at the value, not at the
written number, so a value that `G` writes as `1` without being exactly 1 (`0.9999999`) reads `1 credits` in a
`CREDITS` text, while a unit field under the singular rule (prompts.md, section 2) reads `1 credit` for it. In the system prompts every
configurable value that can be 1 follows it (`prompts.md`); the other ranges keep such values at 2 or more. Some
values are 1 at the default configuration (a test's cost, `1 credit`; the manager's own capacity, `1 run`); the
reference implementation's default texts already read so. It does not apply to texts marked as deliberate quirks,
which keep their wording at every value.

## Vocabulary

- **Participant:** a buyer (ID `B-` and 3 lowercase hex digits) or a manager (`M-` and 3 lowercase hex digits).
- **Seat:** a participant's index within its role, 0 to n-1, in creation order.
- **Task:** `task-N`. It has a worth (private to its buyer) and a due day, and sits in its buyer's queue until it is
  posted, and again from when its buyer ends a contract on it (`end_contract`) until it is posted again.
- **Post:** `post-N` for task-N's first post; `post-N-k` for its k-th, after its buyer ended k-1 contracts on it with
  `end_contract` (the only way a task is posted again).
- **Bid:** `bid-N`. A bid is plain, or escrow with a quality threshold.
- **Contract:** `contract-N`, or `contract-N-k` for the k-th on task-N. A contract is plain or escrow.
- **Run:** fresh or refine; done by the manager's employee (`delegate_run`) or by the manager itself
  (`run_myself`). A run produces a **result** (`R-` and 3 lowercase hex digits) with a true quality q in [0, 10] that no
  participant ever sees.
- **Reading:** a test's noisy measurement of a result's quality.
- **Delivery and submission:** a result given to the buyer on a plain contract is a delivery; on an escrow contract
  it is a submission, which goes to the buyer's review. A submission is then approved, rejected or auto-released;
  a rejection may be disputed, and the **arbiter** rules on the dispute.
- **Day and round:** a day has `time.rounds_per_day` rounds. The round index is g = (day - 1) × rounds_per_day +
  round.
- **Turn:** what one participant does in one round: a chain of model calls that normally ends with an act call
  (`turns.md`, "1. A turn in outline"); a **correction** may follow.
- **Memory:** a participant's private text, shown back to it in its next view.
- **View:** the per-turn message describing the market from one participant's side.
- **Notification:** a notice delivered to a participant's next view.
- **Out of business:** the status of a manager that a charge or a dispute deposit left at a balance of 0 or below,
  until a credit brings its balance above 0 again; a balance at or below 0 alone does not make it so (`episode.md`,
  "9. Out of business").
- **Settlement:** the valuation of every contract at the episode's end.
- **Score:** a participant's outcome: a manager's profit, a buyer's gain.

## Acceptance checks

These checks help you confirm that the build matches. The spec itself is the requirement.

1. **System prompts.** Render the buyer and manager templates for modes `off` and `all` with the field values listed
   in `prompts.md`, "8. Worked example: seed 0, mode `off`" (the paragraph after its table gives mode `all`). The
   results must equal `examples/system_prompt_<role>_<mode>_<id>.txt` byte for byte; the other three modes have no
   example file. Those files come from seed 0
   of the reference implementation: B-c53 has wealth share 1.36, M-14b has 1.35. Your own seed 0 will draw other
   IDs and shares; that is expected (`randomness.md`).
2. **Function definitions.** At the default configuration, the compute and act definitions and each step's
   `tool_choice` must equal `examples/tools_*.json`.
3. **Views.** `examples/view_*_day1_*` are complete day-1 views. `examples/view_*_all_*` are complete mid-episode
   views from real runs, with plain and escrow contracts, payments, conversations and a conversation summary.
   Every line in them must be producible by your view code from the state it describes, in the same order and with
   the same spacing. The market state behind them is not shipped (it would be in the reference implementation's own
   form, not yours), so this is a comparison, not a mechanical test: set up a state that matches what a view shows
   and compare the text, or check each line against the rules of `view.md` and `view-contracts.md`.
4. **Turns.** `examples/turn_with_compute_*.json` and `examples/turn_with_correction_*.json` show the messages of a
   turn with a program and of a turn with a correction: each is one request's message list with its first message,
   the system message, left out (it is that participant's system prompt, built as in item 1; `turns.md`, "5.3
   Example"). The program turn is the second request of seed 0's buyer B-d75 on day 1, round 1 (balance 135.60); the
   correction turn is the manager M-849's correction request on day 1, round 2 (balance 76.80). The assistant messages
   (the compute call, the act call with the text `Mock reply.` and an `accept_bid` of bid-1, a buyer's action that a
   manager cannot take) come from a scripted test model made to provoke these texts, not from the offline mock of
   `running.md`: to reproduce a file, have a test model return
   those assistant messages in that state and compare the messages the market writes, the view (the user message)
   and the function results (the tool messages). `examples/summarizer_request_*.json` show the
   summarizer's two kinds of request; their conversation contents are placeholders from a test run.
5. **Free episodes.** With the offline mock model on every seat (`running.md`, "7. The offline mock model"), seeds 0,
   1 and 2 must each end with status `ok` in each of the five `message_channels` modes, with the run's cost tracker at
   $0 (no money spent; mock replies are still charged to agents in credits, and the episodes' dollar metrics report
   that notional cost), and the logs must show what `running.md`, "7.7 What a
   smoke test must see", lists.
6. **Test vectors.** Your reading of act calls must give each case of `vectors/act_reading.jsonl` its expected
   outcome, and your ordering of a round's actions each phase of `vectors/round_order.jsonl` its expected order,
   clashes and per-action outcomes, as `vectors/README.md` describes. The vectors come from the reference
   implementation, and they agree with the spec: where they seem not to, read the spec section they cite again.
