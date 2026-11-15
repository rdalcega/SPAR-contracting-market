# Spec questions

Places where `spec/` seemed wrong, incomplete or ambiguous while building from it. Each entry gives the
section, a quote, the case that shows the doubt, and the reading the build went on with. The user decides.
Every entry is open until marked otherwise. Entries are numbered in the order they were raised and grouped by
the part of the spec they concern, so the numbers are not in order down the file.

## Where to start

Most of the 115 entries are edge cases: an unbounded parameter pushed to an extreme, a state normal play cannot
reach, a field only the log reads. The build took the reading it would defend and nothing an agent reads at the
default configuration depends on them. These are the ones worth your time first.

**They touch an acceptance check.**
- Q27, Q28, Q29, Q47: in 24 of the 700 phases of `round_order.jsonl` the case's input does not determine the
  expected text (a post's stated preference, the day of a rejection, whether an arbiter's fee was paid, the
  price of a contract the case does not list). The harness takes that one fact from the expected lines, so for
  those lines the check is circular. Measured: with nothing taken from the expected lines, 676 phases pass
  from their input alone and these 24 do not: ro-143, 145, 178, 182, 184, 503, 512, 636, 640, 655, 657, 662,
  666, 668, 671, 672, 673, 674, 684, 685, 687, 689, 695, 698. The fix belongs to the vectors: four more fields
  in `market`.
- Q30: a few expected lines repeat a drawn label, day or cost without the `drawn` mark.
- Q88: check 4's correction turn needs the state at the end of day 1, round 1, which no file gives; it was
  rebuilt from the example's own view.

**Two sentences of the spec disagree, and a text or an outcome follows the choice.**
- Q42: `turns.md` 11.4 says a call's arguments go back "each in full"; `config.md` 5 fixes a 2000-character cut.
- Q12: `config.md` 3.8 refuses a share range that 6.4 gives as an accepted example.
- Q31: `models.md` 4.2 and 4.8 say opposite things about which messages the token estimate uses.
- Q1: a cut act call with no arguments (the sentence against step 1 and vector `act-0818`).
- Q45, Q46: `(no actions run)` beside a memory line; how many calls a chain has after a free retry.
- Q79, Q80: two definitions of a mean reading; the turn counter against the last view not lost.
- Q3, Q82, Q87: smaller disagreements of the same kind.

**A choice made for you that you may want otherwise.**
- Q116: a seat's reasoning effort does nothing under the forced tool_choice of turns.md 4.1 (seen in the paid
  Haiku 5.5 run); `extra_body` can override tool_choice.
- Q39 (decided): keys come from the environment, else from a git-ignored `.env` in the working directory.
- Q4, Q21: what a program's memory is measured as, and how much of the system it may read, on macOS.
- Q36: how the time limit of one model call is enforced while connecting.
- Q18, Q40: integers of more than 4,300 digits and `extra_body` nested too deep to write are refused at load.
- Q102 to Q107, Q112, Q113: readings of the offline mock's rules (a test tool; the spec lets them vary).

## Test vectors (`vectors/round_order.jsonl`)

Q27 to Q29 matter for acceptance check 6: in these cases the expected text cannot be worked out from the
case's input alone, so the test harness has to take a missing fact from the expected lines themselves, which
makes the check circular for exactly those lines. Found by the design step and confirmed independently by a
GPT review of the design. Q30 is of the same family.

### Q27. A post's stated form and threshold are not in a case's `market`
- Where: `vectors/README.md`, "round_order.jsonl", "Fields"
- Quote: "`posts` | `{post: {task, buyer, posted, open, closed}}`" and "a market built from `market` alone gives
  the same texts"
- Case: `ro-640`, `edit_post` of post-14 with threshold 5, expects `post-14 already states this (prefers
  escrow, threshold 5): nothing changed`; the input never states post-14's form or threshold. In all, 17
  `edit_post` lines and 3 notifications state the earlier preference, and 20 more `edit_post` refusals depend on
  whether the post prefers escrow.
- Reading taken: the harness seeds a post's preference from the expected lines of the case's `edit_post`
  actions on that post, up to its first success. The market itself stores the preference; only the vector
  input lacks it.

### Q28. The day of a rejection is not in a case's `market`
- Where: `vectors/README.md`, "round_order.jsonl", "Fields"; `processes.md`, "6.4 The review: approval and
  rejection"
- Quote: "`submissions` (`{result, day, round, decision}` ...)" against "the manager can dispute the rejection
  through day d + `contracts.escrow.dispute_window_days`"
- Case: `ro-218` (day 8; R-08c submitted on day 6, state `HELD, REJECTED, DISPUTABLE`) expects `M-0ed can
  dispute your rejection of R-08c through day 8`, so the rejection was on day 7; day 8 fits the input equally
  and would read `through day 9`. Eight `end_contract` lines depend on it.
- Reading taken: the harness takes the day from such a line when there is one; otherwise the earliest day
  consistent with the contract's state, which alone gives 6 of the 8 lines.

### Q29. Whether the arbiter's fee was paid is not in a case's `market`
- Where: `vectors/README.md`, "round_order.jsonl", "Fields"; `processes.md`, "6.7 The arbiter's fee, and when a
  released result is the buyer's"
- Quote: "Until the buyer pays the fee it owes, the released result is not its: it cannot test it"
- Case: `ro-050` expects `paid the arbiter's fee of 13 on contract-16`. The input records a `RELEASED` contract
  and a ruling for the manager, but not whether the buyer already paid the fee; had it paid, the same action
  must fail. The opposite also occurs: `ro-178`, `ro-182`, `ro-184` and `ro-698` expect a successful
  `test_delivery` of such a result, which needs the fee already paid.
- Reading taken: the harness takes it as unpaid unless the first expected line on that contract that tells
  says otherwise: a successful `test_delivery` or `you owe the arbiter nothing` means paid; a successful
  `pay_escrow_arbiter` means unpaid. Seven cases in all rest on this.

### Q30. Drawn values in lines that carry no `drawn` mark
- Where: `vectors/README.md`, "round_order.jsonl", "Fields", `actions`
- Quote: "`drawn: true` marks an `OK` line of `delegate_run`, `run_myself`, `test_result` or `test_delivery` ...
  the rest of such a line is exact"
- Case: `ro-176`, `- run_myself: ERROR: you are busy on R-dbe (contract-3) until day 5`, where R-dbe was
  started earlier in the same phase (its label and arrival day are drawn); `ro-020`'s balance fact after a run
  of drawn cost.
- Reading taken: the harness gives the three draws (a run, a label, a reading) the reference's values, parsed
  from the case's `drawn` lines, and then compares every text exactly.

## Reading a reply (`turns.md` 7)

### Q1. A cut act call with no arguments at all
- Where: `turns.md`, "7.3 An act call's arguments"
- Quote: "A broken or cut act call never passes as an empty one."
- Case: a cut reply (finish reason `length`) whose act call has no arguments (key absent, null or `""`). Step 1
  of the same section makes missing, null or `""` an empty object with no exception for a cut reply, and vector
  `act-0818` (cut, `calls: [{"name": "act"}]`) expects a readable empty act with the no-memory note.
- Reading taken: step 1 as written and as the vector shows. The sentence is taken to mean only that a cut
  call's non-empty text is never repaired. Only the absent-key form is pinned by a vector; null and `""` under
  cut follow the same rule.

### Q2. Where the 400 nesting levels are counted from
- Where: `turns.md`, "7.2 Reading JSON"; `README.md`, "Number formats and rounding"
- Quote: "JSON nested more than 400 levels deep, are outside this spec ...; shallower nesting must be read."
- Case: an arguments text whose action value is nested 399 levels is 401 levels from the root of the arguments
  (the act object and the actions list add two). The same paragraph describes the reference by an action's
  nesting ("about 490 levels deep"). It is also not said whether arguments a provider returned already decoded
  fall under the limit.
- Reading taken: counted from the root of the act call's arguments value (that object is level 1): up to 400
  read, 401 or more do not decode (the preferred answer), so the call is a format error. The same limit applies
  to arguments sent already decoded. The deepest vector (`act-1071`) is 388 levels from the root and reads.

### Q3. `compute` as the first action of a manager out of business
- Where: `turns.md`, "7.6 How the actions then run" against "13.2 Out of business by a token charge" and
  `actions-buyer.md`, "1.2 Matching the name, and the checks before the action's own"
- Quote: "`compute` sent as an action never runs a program; it fails with"
- Case: a manager put out of business by its reply's charge whose first action is named `compute`. 7.6 gives
  `compute` one unconditional refusal; 13.2 says the first action fails with the out-of-business text "unless
  the name itself is refused (no name, unknown, not available in this market, or the other role's action)",
  and `compute` is none of those four.
- Reading taken: the out-of-business text. `compute` passes steps 1 to 5 of 1.2, step 6 refuses a participant
  out of business, and the compute refusal is the action's own check (step 8). The vectors give `compute`'s
  result only for a participant in business.

## The turn conversation (`turns.md` 2, 4, 11, 12)

### Q42. Whether a call's arguments are cut on the correction's text path
- Where: `turns.md`, "11.4 How the body joins the conversation" (text path), against `config.md`, "5. Fixed
  values"
- Quote: `turns.md`: "the arguments of the reply's function calls, each in full, joined by line breaks";
  `config.md`: "the 2000-character cut of each function call's arguments when a correction sends a reply back
  as text"
- Case: an unreadable decide reply whose act call has an arguments string of 2,500 characters: one file shows
  all 2,500 in the correction request, the other 2,000.
- Reading taken: the cut, as `config.md` fixes it: each call's arguments, written as `TEXT`, cut to its first
  2,000 characters; whether a call is left out is decided on the uncut value; the reply's own text is never
  cut.

### Q43. "Shortest match" for nested think tags
- Where: `turns.md`, "2. The conversation's messages", strip-think
- Quote: "remove every `<think>`...`</think>` block (shortest match, across lines)"
- Case: `x<think>a<think>b</think>y`. From the first opening tag to the first closing tag gives `xy`; taking
  "shortest" as the innermost block leaves `x<think>ay`, and the third rule then gives `x`.
- Reading taken: scanned left to right, each block runs from an opening tag to the first closing tag after it
  (`xy`).

### Q44. A call whose arguments arrive decoded as `0.0`
- Where: `turns.md`, "11.4 How the body joins the conversation", text path
- Quote: "leaving out every call whose arguments are missing, null, `false`, `0`, `""`, `[]`, `{}` or only
  whitespace"
- Case: a provider returns a call's arguments already decoded as the number 0.0 (or -0.0); the list names `0`
  only.
- Reading taken: left out: every zero number counts as the listed `0`.

### Q45. `(no actions run)` when only a memory line follows the heading
- Where: `turns.md`, "12. The turn's results text" (first bullet and item 3)
- Quote: "`Results of your actions:` + line break + `(no actions run)` when there are no lines"; for the
  correction block: "`(no actions run)` if the correction produced no line at all (no opening note of section 8
  and no action line)"
- Case: a readable decide reply with no actions and no memory, no correction: `Results of your actions:` then
  the no-memory note, or `Results of your actions:`, `(no actions run)`, then the note?
- Reading taken: literal: at the top level the memory line counts as a line, so `(no actions run)` appears only
  when nothing at all follows the heading; inside the correction block it appears as item 3 defines.

### Q46. How many calls a chain can have once the free retry was granted
- Where: `turns.md`, "4.2 When a reply continues the chain", with 5.4
- Quote: "else, if `calls - free <= 2`, it is a **compute step**" and 5.4: "The chain's bound is unchanged"
- Case: replies: compute (failed, free retry granted), compute (failed again), compute despite the forced act,
  compute again. By the formula the third reply is still a compute step (answered `not run: no programs
  left ...`) and a fourth call is made; the table's last row (no free retry) ends at the third.
- Reading taken: the formula as written: at most 3 calls without a free retry, at most 4 with one.

## Running a program (`turns.md` 6)

### Q4. Which memory figure is "the physical memory its process holds"
- Where: `turns.md`, "6.3 Limits and statuses", "The program's memory"
- Quote: "is the physical memory its process holds (resident memory)" and "(for example `b'x' * (600 * 2**20)`
  at the default 512 MB) is stopped with `memory_limit`"
- Case: on an 8 GB Mac the kernel compresses the pages of that 600 MB example as they are written when two or
  more programs run at once; the resident size then peaks at about 460 MB (2 at once), 310 MB (4), 200 MB (8),
  and the spec's own example ends `ok`. Run alone it is above 512 MB for about 10 ms only, so polling every
  0.02 s (the reference's example interval) caught it 12 times in 30.
- Reading taken: memory is the kernel's physical footprint of the process (compressed pages included, untouched
  reservations not counted), polled every 0.002 s. With it the example is stopped every time. On an idle host
  it reads about 7 MB below the resident size. A briefer excess can still pass, as the paragraph accepts.

### Q5. A program that ignores SIGXCPU
- Where: `turns.md`, "6.3 Limits and statuses", row `cpu_limit`
- Quote: "it ended by the signal SIGXCPU after its CPU time (as the operating system reports it when the program
  ends) reached `compute.cpu_s` seconds"
- Case: `signal.signal(signal.SIGXCPU, signal.SIG_IGN)` then `while True: pass`. With the CPU limit set as a
  resource limit inside the process (the example mechanism), macOS sends SIGXCPU once, so the program runs past
  `cpu_s`, although the section also says a program that breaks a limit is stopped at once.
- Reading taken: as the example mechanism gives it: the program is stopped at `wall_s` with status `timeout`.
  CPU time is not watched from outside.

### Q6. An uncaught `KeyboardInterrupt`
- Where: `turns.md`, "6.3 Limits and statuses", last paragraph
- Quote: "An uncaught exception prints, after flushing what the program printed, Python's standard traceback
  for it"
- Case: `raise KeyboardInterrupt`. A plain CPython prints the traceback and ends itself by SIGINT, which would
  be status `killed` with no exit code shown; the paragraph says the exit code is 1 and names only `SystemExit`
  as behaving as in Python.
- Reading taken: every uncaught exception other than `SystemExit` gives the traceback, exit code 1 and status
  `error`.

### Q7. Whether a trailing line feed can cause the output cut
- Where: `turns.md`, "6.4 The output text", first bullet
- Quote: "if the result is longer than `compute.output_chars` characters"
- Case: `print('a' * 4000)` at the defaults. The stream is 4001 characters with its line feed, so it is marked
  cut and reads 4000 a's followed by the cut note, although nothing visible was lost; trailing line feeds are
  removed only in the third bullet.
- Reading taken: literal. The cut is decided on the decoded output before trailing line feeds are removed.

### Q8. Libraries outside the interpreter's installation
- Where: `turns.md`, "6.1 Isolation", second bullet
- Quote: "what the interpreter itself needs (its installation, the standard library's files included, and the
  system libraries it loads)"
- Case: Homebrew's CPython loads libmpdec, libssl, libcrypto, libsqlite3, liblzma and libzstd from other
  Homebrew folders, outside both its installation and the system's library folders. Without them `sqlite3`,
  `lzma`, `ssl` and `compression.zstd` do not import and `decimal` falls back to its pure-Python version.
- Reading taken: they count as libraries the interpreter loads. The profile allows reading exactly the library
  files its extension modules name (and those these name in turn), nothing else in those folders.

### Q9. Standard-library modules that need a denied host query
- Where: `turns.md`, "6.1 Isolation", paragraph after the bullets
- Quote: "in the reference some of these queries give `None` or fail, for example `os.cpu_count()` gives `None`"
- Case: `import ctypes` fails with `PermissionError` on macOS, because importing it calls `os.uname()`, which
  needs the same kernel query that makes `os.cpu_count()` give `None`. So one standard-library module is not
  importable, although 6.1 says the standard library is there.
- Reading taken: the query stays denied, as the reference's `os.cpu_count()` suggests. `ctypes`,
  `platform.uname()` and `socket.gethostname()` fail.

### Q10. What "standard input is closed" looks like to the program
- Where: `turns.md`, "6.1 Isolation", fourth bullet
- Quote: "standard input is closed (reading it fails)"
- Case: in an interpreter started with stdin already closed, `sys.stdin` is `None` and `input()` raises
  `RuntimeError`. Here the runner closes stdin after start, so `input()` and `sys.stdin.read()` raise
  `ValueError` and `os.read(0, 1)` raises `OSError`.
- Reading taken: any failure counts; the exception type is the interpreter's wording. `sys.stdin` is a closed
  file object.

### Q11. A program that replaces itself with a fresh interpreter
- Where: `turns.md`, "6.1 Isolation", third bullet
- Quote: "no subprocesses and no forking (an attempt fails)"
- Case: `os.execv(sys.executable, [sys.executable, '-c', 'exit(3)'])`. The profile must allow executing the
  interpreter to start it (the example mechanism: "executing only the interpreter"), so a program can replace
  itself with an interpreter started with its own flags, where `exit` is defined. It is the same process, under
  the same sandbox and limits.
- Reading taken: allowed, as in the example mechanism: neither a subprocess nor a fork. Executing anything else
  fails.

### Q21. How much of the system a program may read
- Where: `turns.md`, "6.1 Isolation (requirements)", second bullet, against the example mechanism of the same
  section
- Quote: "It may read what the interpreter itself needs (its installation, the standard library's files
  included, and the system libraries it loads)" against "a deny-by-default profile that allows reading only the
  interpreter's installation, the system libraries, the random devices and its empty working directory"
- Case: `os.listdir('/')` and reading a file under `/System/Library` both succeed. The requirement names only
  the libraries the interpreter loads; the example allows the system libraries as a whole. On this host the
  interpreter aborts at start unless it may list `/`, and the standard library aborts inside libffi unless
  `/usr/lib` and `/System/Library` are readable.
- Reading taken: the example's. The system library folders are readable as a whole, and so is the listing of
  `/`. Nothing in them is among what a program must not reach (the market, run files, logs, configuration, the
  repository, the home directory).

### Q22. Whether the program is compiled from text or from its UTF-8 bytes
- Where: `turns.md`, "6.1 Isolation (requirements)", first paragraph
- Quote: "A program is the `code` string as sent, run by a CPython 3 interpreter ... the interpreter is given
  its UTF-8 encoding"
- Case: CPython treats source bytes and a source string differently. A program that is only U+FEFF: as bytes
  the leading EF BB BF is a byte order mark and the program is empty (`ok`, `(no output)`); as a string U+FEFF
  is a syntax error. A program starting `# coding: latin-1`: as bytes the declaration is honoured.
- Reading taken: literal: the interpreter compiles the UTF-8 bytes, so a leading byte order mark is accepted
  and a coding declaration is honoured.

## Configuration (`config.md`)

### Q12. A low share bound that rounds to 0: refused by 3.8, accepted by 6.4
- Where: `config.md`, "3.8 `money`" against "6.4 Wealth shares, start balances and per-task budgets" (and
  `episode.md`, "9. Out of business": "a share can round to 0")
- Quote: 6.4: "A share can round to 0 (a low end of `money.share_range` near 0 with few decimals, e.g. 0.004
  with `share_decimals` 2); the config is not refused for that"
- Case: `money: {share_range: [0.004, 1.4], share_decimals: 2}`. round(0.004, 2) = 0.0, which is not 0.004, so
  3.8's range rule for the key refuses the very config 6.4 gives as accepted. More generally 3.8 forces the low
  end to be at least 10^-share_decimals, and then no drawn share can round to 0.
- Reading taken: 3.8 decides, as the key's own range rule: the config is refused. `start_balance` and
  `per_task_budget` still give 0 for a share of 0, so only the 3.8 check would need relaxing if the other
  reading is meant.

### Q13. `money.start_amount` and `money.task_budget` written as decimals
- Where: `config.md`, "3.8 `money`"
- Quote: "a whole number, 0 < x < 10^6 (so the prompts' "{share} x {start_std} = {start_balance}" is exact)"
- Case: section 1 item 5 defines only "integer" and "number". Is `money.start_amount: 60.0` accepted (a number
  whose value is whole) or refused (an integer written as a decimal)? `prompts.md` formats it with `G`.
- Reading taken: a number whose value must be whole: `60` and `60.0` are accepted, `60.5` and booleans refused;
  held as a double.

### Q14. `tasks.arrival_p` within 1e-12 of 0 or 1
- Where: `config.md`, "3.7 `tasks`"; `prompts.md`, "7. Range checks the fixed wording relies on"
- Quote: "number, 0 < p < 1, within 1e-12 of a fraction n/d with d <= 100, so the prompts' fractions are those
  of n/d"
- Case: p = 1e-13 is inside (0, 1) and within 1e-12 of 0/1, but the prompt's fraction would then read `0`,
  which `prompts.md` 7 says the check prevents ("never `0` or `1`").
- Reading taken: the fraction n/d must itself lie strictly between 0 and 1, so p = 1e-13 and 1 - 1e-13 are
  refused. The distance is measured exactly, and the 1e-12 bound is inclusive.

### Q15. A file value that is invalid but replaced by a command-line option
- Where: `running.md`, "2. A run", step 1
- Quote: "The config file is read, merged with the defaults and checked (`config.md`). The options of section 1
  are applied and checked by the same rules as the keys they replace."
- Case: a file with `seeds: []` (invalid) run with `--seeds 1`.
- Reading taken: the file is checked first, as the sentence orders it, so the run is refused.

### Q16. `messaging` keys checked when `message_channels` is `off`
- Where: `config.md`, "3.13 `messaging` (in effect only when `message_channels` is not `off`)", against section
  1 item 6
- Quote: "`messaging` (in effect only when `message_channels` is not `off`)"
- Case: `message_channels: off` with `messaging.recent: 1` (out of range). The summarizer's row says "checked in
  every mode"; the messaging rows do not.
- Reading taken: refused in every mode: every check of sections 1, 3 and 4 runs at load, and "in effect" is
  read as about use.

### Q17. The resolved form of a model spec that leaves keys out
- Where: `config.md`, section 1 item 3 and "3.4.1 A model spec"; `running.md`, "6.1 The resolved config"
- Quote: "Example: `models: {managers: {provider: mock}}` gives the managers a spec with no `model`, no prices
  and no `extra_body`."
- Case: the resolved config must say what a spec's calls use; holding the spec literally makes every reader
  re-apply the "when absent" column.
- Reading taken: the resolved spec carries `temperature` (agents: `llm.temperature`; summarizer: null),
  `max_tokens` (`llm.max_tokens`) and `extra_body` (`{}`), which `running.md` 5 treats as the same spec. `model`,
  prices, `base_url` and `api_key_env` stay absent when not given; nothing comes from the default spec. No
  behaviour differs; only the stored form differs from the example's wording.

### Q18. An integer of more than 4,300 digits in the config file
- Where: `config.md`, section 6 (opening) and section 1 item 2
- Quote: "day numbers (6.3, 6.6), which are whole numbers computed exactly whatever the size of an integer
  parameter"
- Case: `contracts.min_contract_days` written with 4,301 digits. The interpreter's limit on converting digit
  strings to integers makes the YAML reader fail on it.
- Reading taken: refused, however the integer is spelled (in decimal the reader cannot read the file; in hex
  the reader reads it and the key's check refuses it), because such an integer cannot be written out in any
  text (`INT`). Integers up to 4,300 digits are accepted and computed exactly.

### Q19. A stated share of a run's days mix off by one in the fourth decimal
- Where: `prompts.md`, "4.4 Run durations: the duration text", against `config.md`, "6.7 A run's days mix"
- Quote: "t_med 2.5: 1: 0.0044 (folded into "2 or fewer days"), 2: 0.2575, 3: 0.4369, 4: 0.2116, 5: 0.0658,
  then 0.0237 in all (its own bucket)"
- Case: by 6.7 the share after d = 5 for t = 2.5 is 1 - Phi(ln(5/2.5)/0.35) = 0.023828, which rounds to 0.0238.
  It is the same quantity as t_med 1.0's "tail after 2 = 0.0238" in the same paragraph.
- Reading taken: 6.7's formula decides; 0.0237 is taken as a slip. No text depends on it (the bucket reads 2%
  either way).

## System prompts (`prompts.md`)

### Q23. A stated token price that is not finite
- Where: `config.md`, section 6, "Finiteness during an episode", with `prompts.md` 3.1 (`price_in_text`,
  `price_out_text`)
- Quote: "an amount computed during an episode infinite or not a number: a start balance or per-task budget
  (6.4), a run's cost, a charge, a credit, a balance, a payment, a value, a score or a sum of these"
- Case: `price_in_per_m` and `credits_per_usd` have no upper bound, so the stated price `price_in_per_m` x
  `credits_per_usd` can overflow (1e308 x 2). The list of amounts does not name the stated prices, and
  "Finiteness at load" does not list them, so the text does not say whether the prompt reads `about inf
  credits` or the episode stops.
- Reading taken: a stated price is an amount shown to the agent, so the rule applies: building the prompt
  fails naming the value, and the episode stops with status `error`.

### Q24. `arrival_law` when p is within rounding of 0.5 but not 0.5
- Where: `prompts.md`, "4.5 The generalised sentences", row `arrival_law`
- Quote: "if p = 0.5 exactly: `(1/2)^(n+1)`; otherwise `(<FRAC of 1 − p>) x (<FRAC of p>)^n`"
- Case: p = 0.5 + 1e-13 is an accepted value and not 0.5 exactly, so the law reads `(1/2) x (1/2)^n` while
  the three probabilities beside it read as at p = 0.5, where the law reads `(1/2)^(n+1)`.
- Reading taken: literal: the short form only when the double equals 0.5.

### Q25. A duration law whose only bucket is "1 or more days"
- Where: `prompts.md`, "4.4 Run durations: the duration text", buckets, step 4
- Quote: "otherwise `<d> or more days` if d = last and up and not split; otherwise `1 day` if d = 1, else `<d>
  days`"
- Case: a law whose only listed count is 1, with a share after it between 1e-4 and 0.005 (sig_t 0.35 with a
  median of 0.4: 1 day has 0.9956) gives the single clause `With probability 100%, ... take 1 or more days`,
  which says nothing and hides that 1 day is nearly certain.
- Reading taken: the rule as written.

### Q26. Which bucket gives up 1 percent to an empty tail bucket
- Where: `prompts.md`, "4.4 Run durations: the duration text", buckets, step 6
- Quote: "then, if tail and the bucket of step 5 has 0, it gets 1, taken from the bucket with the largest
  percent (the earliest such)"
- Case: whether "the largest percent" is judged before or after the tail bucket receives its 1. It matters
  only if no other bucket has more than 1 percent, which cannot happen when a listed count holds at least 5%.
- Reading taken: judged before, so the tail bucket is never the one taken from. No text depends on it.

### Q41. Accepted values that a system prompt cannot state
- Where: `config.md`, "3.7 `tasks`" (`worth_range`: a pair of integers) and "3.11 `production`" (`sig_c`: a
  number >= 0), with `prompts.md` 3.3
- Quote: `config.md` 6, "Finiteness at load", lists only four values; "Finiteness during an episode" lists
  amounts.
- Case: `tasks.worth_range: [40, 10**400]` is a valid pair of integers, but `G` of the upper worth has no
  double; `production.sig_c: 1e308` makes the manager's stated cost spread infinite. Both configs load, and the
  manager's system prompt then cannot be written.
- Reading taken: as Q23: building the prompt fails with an arithmetic error and the episode stops with status
  `error` at its first turn. Refusing such configs at load would be kinder; the spec's list of load checks is
  closed, so it was not added.

## The model layer (`models.md`)

### Q31. Which messages the input-token estimate uses when `extra_body` replaces them
- Where: `models.md`, "4.2 The body" against "4.8 Token counts, and the estimate when usage is missing"
- Quote: 4.2: "An `extra_body` that replaces `messages` or `tools` sends its own value instead (4.8's estimate
  still uses the caller's)." 4.8: "The messages and functions are those of the body as sent, after the model
  spec's `extra_body` has been merged in (4.2): an `extra_body` that replaces `messages` or `tools` changes the
  estimate."
- Case: `extra_body: {messages: [...]}` with a provider that reports no prompt tokens: the two sentences give
  different input tokens, and so a different computed cost and charge.
- Reading taken: 4.8, the section that owns the estimate: the body as sent, after the merge.

### Q32. The estimate over message shapes the spec does not describe
- Where: `models.md`, "4.8 Token counts, and the estimate when usage is missing"
- Quote: "for each message in order, its `content` (`""` if missing or null), followed, if the message has
  function calls, by the JSON text of its `tool_calls`"
- Case: an `extra_body` may put its own, unchecked `messages` and `tools` in the body: a content that is a
  list of parts or a number, a message that is not an object, `messages` that is not a list.
- Reading taken: a content that is neither a string nor null counts as its JSON text; a message that is not an
  object counts nothing; `messages` or `tools` that is not a list counts nothing.

### Q33. An empty token count that is not null
- Where: `models.md`, "4.7 Reading the reply" with 4.8
- Quote: "An empty `choices`, `message`, `tool_calls`, `function`, `content`, `usage`,
  `completion_tokens_details` or token count is never malformed: it is read as above."
- Case: `"prompt_tokens": false` (or `""`, `[]`, `{}`). 4.8 gives the estimate only for "missing or null" and
  says "A reported 0 is used as 0".
- Reading taken: only missing or null gives the estimate; any other empty value is a reported count of 0.

### Q34. A cost that is a boolean or too large for a double
- Where: `models.md`, "4.4 What each attempt ends in" and 4.7
- Quote: "whose `usage` is an object with a `cost` that is a finite number"
- Case: `"cost": true` (4.7 says "a boolean is not a number" only for token counts), or a cost written as a
  400-digit integer.
- Reading taken: a boolean cost is not a number (not added on a failed attempt; malformed in a 2xx reply); a
  cost beyond the double range is not finite, treated the same.

### Q35. Which failures count as a lost network
- Where: `models.md`, "4.4 What each attempt ends in", the network-failure row, with 4.6
- Quote: "A network failure: the connection could not be made or the request not sent (DNS failure, connection
  refused, a dropped network, or out of time, 4.3)"
- Case: a failure to connect that is permanent (a certificate refused, a mistyped host name in `base_url`): as
  written, each call waits out `llm.network_patience_s` (15 minutes at the default) before failing, every turn.
  And "later in the attempt the connection was reset or broken" does not say which errors count as broken.
- Reading taken: as written: every error while connecting or sending is a network failure. While reading the
  response: reset, aborted, broken pipe, a close before any response, network down or unreachable and the
  kernel's timeout are network failures; any other input/output error is a counted failure.

### Q36. The bound of about three times `llm.timeout_s` on one attempt
- Where: `models.md`, "4.3 One attempt and its time limit"
- Quote: "it must be given up no later than about three times `llm.timeout_s` after its start, however the
  server sends"
- Case: (a) a server that trickles header bytes more often than `timeout_s`: the example scheme (a limit per
  step, a check before each read of the body) never stops it; (b) before any server sends: a host name that
  resolves to several unreachable addresses, or a resolver that blocks, can take longer than the bound.
- Reading taken: (a) the "must" is literal: the connection is shut `timeout_s` after it was made, so an attempt
  ends at most about 2 x `timeout_s` after its start; (b) the bound governs what the server sends once
  connected; connecting is limited by `timeout_s` per address tried, as in the spec's example.

### Q115. A time limit longer than a socket can wait
- Where: `config.md`, "3.4.3 `llm`: call settings", with `models.md`, "4.3 One attempt and its time limit"
- Quote: "`llm.timeout_s` | time limit of one attempt of one call, from its start (connecting included) to the
  last byte of the reply, in seconds ... | 1200 | number > 0"
- Case: the range has no upper bound, but on this platform a socket or a timer takes no wait above about
  9.2e9 seconds, so `timeout_s: 1e10` is a valid config whose limit cannot be handed to the operating system.
- Reading taken: a limit above 1e9 seconds is used as 1e9 seconds (about 31.7 years): the attempt is given up
  then, and the timeout error names that figure, which is the time actually waited. The alternative, refusing
  such a value at load as Q18 and Q40 do for other extremes, is not what "number > 0" says. See also Q36.

### Q37. A `base_url` that is not an http or https URL
- Where: `config.md`, "3.4.1 A model spec" (`base_url`: "non-empty string"); `models.md` 3 and 4.1
- Quote: "`base_url` | the server's base URL | ... | non-empty string"
- Case: `base_url: localhost:8000/v1` (no scheme) passes the config check. Treated as a host it would look like
  a lost network and be waited out on every call.
- Reading taken: setting up the backend fails, so the episode ends with status `error` before playing. The
  same holds for any `base_url` a request cannot be made to (a port that is no port, a broken address, spaces
  or non-ASCII characters) and for a key whose value a request header cannot carry (a trailing line feed, for
  one).

### Q38. `served by` when the provider field is empty
- Where: `models.md`, "4.7 Reading the reply"
- Quote: "**served by**: the body's top-level `provider` field ..., else none."
- Case: `"provider": ""`, or a value that is not a string.
- Reading taken: an empty value is none; any other value is passed on as given.

### Q39. Reading keys from a `.env` file
- Where: `models.md`, "3. Providers"
- Quote: "An implementation may also read variables from a `.env` file, the real environment taking
  precedence."
- Case: the spec allows it without requiring it; this repository's own rule is that keys come from the
  environment, never from repo files, and a `.env` in the working directory is normally a file in the repo
  folder.
- Decided (the user asked for `.env` support): read. A key variable unset or empty in the environment is looked
  up in `.env` in the working directory (git-ignored, never committed); otherwise the environment wins. A `.env`
  that cannot be read counts as absent. The setup error reads `set {variable} in your environment or in .env`.

### Q40. An `extra_body` nested deeper than its JSON text can be written
- Where: `config.md`, "3.4.1 A model spec", row `extra_body`
- Quote: "a mapping, not checked against any schema, that must have a JSON form (at every depth only mappings
  with string keys, lists, strings, finite numbers, booleans and null)"
- Case: an `extra_body` nested 200,000 levels, or holding an integer of more than 4,300 digits: every element
  is of an allowed kind, but the request body and the resolved config cannot be written out.
- Reading taken: refused at load: "must have a JSON form" is read as a form that can be written. Nesting of a
  thousand levels is accepted and sent.

## Randomness (`randomness.md`)

### Q20. A run whose day count is not a finite number
- Where: `randomness.md`, "3.8 A run's quality, days and cost", with `config.md`, section 6, "Finiteness during
  an episode"
- Quote: "days = max(1, ceil(t × exp(`production.sig_t` × z_t))), an integer ≥ 1."
- Case: `production.sig_t` and the `t_med` keys have no upper bound. With sig_t = 1000 and z_t = 1, t ×
  exp(sig_t × z_t) is infinite, so the day count has no integer value. The finiteness rule lists amounts only.
- Reading taken: the draw fails and the episode stops with status `error`, as for any failure of the
  implementation (`running.md`, "4.3 Errors"). A non-finite run cost is likewise stopped by the caller at the
  point the amount would be used, as `config.md` 6 says.

## Test vectors, continued

### Q47. An unnamed payment toward a contract the case does not list
- Where: spec/vectors/README.md, round_order.jsonl, "Fields" (the paragraph after "Which objects `market`
  lists")
- Quote: "A `pay` that names no contract (actions-manager.md, "8. pay", step 5) finds, among the listed
  contracts, the same contract the reference implementation's full market gave it, or none when it gave none:
  the unlisted contracts play no part in any case."
- Case: ro-503 expects `- pay: OK: paid 1 to M-7f8 for contract-3`, but `market.contracts` lists only
  contract-4, contract-8 and contract-6; contract-3 appears only in the `contracts` lists of B-b79 and M-7f8,
  with no form, price, paid amount or deliveries. From the input alone the payment goes to contract-4. Same in
  ro-512 (expects contract-1) and ro-684 (expects contract-20). These are the only three unnamed payments in
  the file that count toward a contract; the other 14 are on no contract.
- Reading taken: Followed the vector. The harness (`seed` in tests/test_round_order.py) gives the stub
  contract that such an expected line names a price equal to the amount paid toward it, so step 5 finds it. No
  market code is bent; the check is circular for those three lines, as with Q27 to Q29.


## Processes (`processes.md`)

### Q48. A ruling for the manager when the arbiter's fee rounds to 0
- Where: spec/processes.md 6.6 (the ruling, a fee of 0)
- Quote: "If the fee is 0 (`contracts.escrow.dispute_fee_rate` is above 0, but the fee rounds to 0 on a small
  enough price), the result is the buyer's at once (received on the ruling day) and nothing is owed; the texts
  below are the same as for any other fee."
- Case: dispute_fee_rate 0.001, price 2: the fee is 0, the manager wins. The buyer is told `R-b8b is yours
  once you pay the arbiter's fee of 0 (pay_escrow_arbiter).` although the result is already its and
  pay_escrow_arbiter would refuse with `you owe the arbiter nothing`.
- Reading taken: As written: the same two texts with {fee} = `0`; received is set to the ruling day and
  nothing is owed.

### Q49. The release factor's zero branch cannot be reached
- Where: spec/processes.md 6.8 (the release factor)
- Quote: "else: factor = 0 if s − due >= escrow_close_days"
- Case: 6.3 forbids a submission from due + escrow_close_days, and 6.8 itself says "the factor of a released
  submission is above 0", so this branch can never be taken by a market that follows the rules; it also exists
  only for deadline != due, while for deadline = due the same lateness goes through the other formula.
- Reading taken: Implemented as written (two branches); the zero branch is reached only on a state built by
  hand, where the release then pays the manager nothing and refunds the whole price.

### Q50. The receive day of a result ruled on at the close with a fee of 0
- Where: spec/processes.md 6.7 against spec/episode.md 6 step 1 (receive day of a ruling at the close)
- Quote: "from the ruling day when the arbiter's fee is 0 (6.6)"
- Case: A dispute made on the market's last day is ruled at the close, one day before its ruling day (6.6:
  "The ruling day is the dispute day + 1 (fixed)"). With a fee of 0, 6.7's list gives the ruling day, which is
  after the close day; episode.md 6 says "the result is the buyer's, received on the close day".
- Reading taken: The day the ruling is made: the close day at the close (episode.md 6), dispute day + 1
  otherwise.

### Q51. Notifications of the close's steps
- Where: spec/episode.md 6 and spec/processes.md 6.5 (notifications at the close)
- Quote: "The notifications that steps 1-3 would produce are never read (no turn follows); they need not be
  produced."
- Case: A submission of day 10 released at the close on day 10 with review_days 2: if the notice is produced,
  it reads `... by the start of day 12`, a day that is never played; processes.md 6.5 allows for it ("equal to
  `{d}` except at the market's close").
- Reading taken: They are produced and logged as at a day's start (no separate path for the close), with
  {auto_day} = submission day + review_days; the money and outcomes are the same either way.

### Q52. The log record of a deposit lost to a ruling
- Where: spec/running.md 6.3 C (money of these processes)
- Quote: "every money movement they make (releases, refunds, deposits lost or returned, the arbiter's fee
  charged at the close), as in E, "money""
- Case: A ruling for the buyer: the deposit left the manager's balance at the dispute and now leaves the
  market from the held money. E's money record is "participant, amount and kind ... so every balance can be
  rebuilt", but this movement changes no balance; a record shaped like the others would be counted twice by a
  rebuild.
- Reading taken: One `money` record naming the manager, kind `dispute deposit lost`, the deposit as its
  amount, marked held=True (no balance moves); the ruling record also states the deposit and its fate.

### Q53. "Cannot be delivered" for a run arriving on an ended escrow contract
- Where: spec/processes.md 1.4 ({gone})
- Quote: "` {buyer_id} ended {contract_id} on day {ended_day}, so it cannot be delivered.` (leading space;
  `{ended_day}` INT) when the contract's buyer ended it"
- Case: A run arrives on an escrow contract its buyer ended: the text says "cannot be delivered" although on
  an escrow a result is submitted, and 1.4 words the escrow case separately only for released or closed
  escrows.
- Reading taken: As written: the same sentence for an ended contract of either form; nothing added for an
  escrow released or closed unreleased.


## Actions (`actions-buyer.md`, `actions-manager.md`)

### Q54. The balance check for an action that costs 0
- Where: spec/actions-buyer.md, 1.12 Balance checks
- Quote: "An action that costs `cost` fails when `balance + 1e-9 < cost`"
- Case: A buyer whose token charges took its balance to -3 calls test_delivery with tests.buyer_cost set to 0
  (valid: number >= 0). By the formula -3 + 1e-9 < 0, so a test that costs nothing is refused with
  `insufficient balance for test_delivery: it costs 0, you have -3.00`. Section 5 says of a plain acceptance
  that it 'needs no balance (acceptance is free, also with a negative balance)', but nothing says so for a
  checked action whose cost is 0.
- Reading taken: As written: short(p, cost, what) refuses whenever balance + 1e-9 < cost, also at cost 0 with
  a balance below -1e-9.

### Q55. Whether the other contract's result must have arrived in the result lookup
- Where: spec/actions-manager.md, 1.3 The result lookup, step 1
- Quote: "The contract has no result with that label, but another contract of the same manager does (the first
  such contract in creation order, ended ones included)"
- Case: A manager names on contract-15 a label that is a run still in flight on its contract-26 (not arrived).
  It is not said whether the other contract's result must have arrived for step 1's text; otherwise step 2's
  `no result '{raw}' in {contract}` would apply. The vectors hold no line of step 1.
- Reading taken: Any result of the other contract counts, arrived or not: find_result gives `{label} is a
  result of {other}, not of {contract}: ...`.

### Q56. "Its bid on it created last" in withdraw_bid
- Where: spec/actions-manager.md, 3. withdraw_bid, Finding the bid
- Quote: "The bid is this manager's open bid on it, else its bid on it created last, else:"
- Case: A manager with two bids on a post that are no longer open (bid-7 withdrawn, then bid-8 withdrawn).
  'Created last' is not tied to an order: by bid number, or by the day and round last recorded on the bid (an
  update in place replaces a bid's day and round but keeps its id and place).
- Reading taken: The last of the manager's bids on the post in the order bids were first placed (bid-N order),
  so bid-8; withdraw_target returns it and withdraw_bid then refuses it by its status.

### Q57. edit_post with an empty form on a post that states one
- Where: spec/actions-buyer.md, 2 post_task check 4, against 1.4 Arguments and 3 edit_post check 5
- Quote: "`none`, `any`, `either`, `both`, `no preference` and the empty text mean no preference"
- Case: edit_post with form "" on a post that prefers escrow: by 1.4 the empty text is not given, so check 5
  ("when not given, the post's current form") keeps escrow; by section 2's list it would set no preference.
- Reading taken: 1.4 decides: form "" is not given, so edit_post keeps the current form (and post_task posts
  with no preference either way).

### Q58. A bid on a post withdrawn while its bids are still sealed
- Where: spec/actions-buyer.md, 5 accept_bid, checks 2 and 3
- Quote: "The bid is on another buyer's post, or on this buyer's post while its bids are sealed: the SAME text
  as 1"
- Case: A buyer withdraws its post in the round after posting it (still inside the seal window) and then names
  a bid on it: sealed is defined by the round index alone, so check 2 answers `no bid with id ...` until the
  opening round and `{post} is already closed` only after it.
- Reading taken: Sealed is by time only, also for a closed post: the not-found text while the round index is
  below the opening round.

### Q59. "1 credits" in end_contract's texts
- Where: spec/actions-buyer.md, 11 end_contract, the notification's {money} and the success text's {back}
- Quote: "` The {paid} credits {buyer} paid on it before ending it are not returned.` (`{paid}` = `G` of
  `paid`)"
- Case: A plain contract ended with paid exactly 1 reads `The 1 credits B-c53 paid on it ...` and `; the 1
  credits you paid on it are not returned`. The text is not marked as a deliberate quirk and is not under the
  singular rule or the CREDITS format.
- Reading taken: Literal: G of paid followed by the word `credits`, whatever the amount.

### Q60. dispute's fallback text that no state reaches
- Where: spec/actions-manager.md, 7. dispute, check 6
- Quote: "`{what}` = `{label} is under the buyer's review` (a submission is under review) | `nothing has been
  rejected` (no submission yet) | `its latest submission was not rejected`."
- Case: A latest submission that is neither under review nor rejected was approved, released automatically or
  released by a ruling; the escrow then no longer holds the price and check 4 (`the escrow of {contract} was
  released: there is nothing to dispute`) refuses first. No state of the market reaches the third text.
- Reading taken: Kept as the fallback for any latest submission that is neither under review nor rejected
  (tested on a hand-made state only).

### Q61. `{n}` in "no result labels left"
- Where: spec/actions-manager.md, 4. delegate_run and run_myself, check 5
- Quote: "{contract} has no result labels left ({n} results)"
- Case: `{n}` is not defined beside the text (the file's preamble only says `{n}` is written in INT). A
  contract holding 4096 results.
- Reading taken: `{n}` is the number of results the contract holds, so `contract-3 has no result labels left
  (4096 results)`.

### Q62. Whether the log holds a pay message of whitespace only
- Where: spec/actions-manager.md, 8. pay (`{unsent}`) against spec/running.md, 6.3 E, "payment"
- Quote: "a string of whitespace only gives no note"
- Case: running.md asks the payment's log record for "any message given (never sent)". A `message` of " "
  counts as given (actions-buyer.md 1.4) but gets no note: it is not said whether the log holds it.
- Reading taken: The `payment` record carries the message whenever it counts as given (so also a
  whitespace-only one), None otherwise; log only, nothing an agent reads depends on it.


## Messaging (`messaging.md`)

### Q63. No row c in send_message's checks
- Where: spec/messaging.md, 2.3 Its own checks, in order (and 2.4)
- Quote: "| b | the recipient is not a text (a number, boolean, list or object) | `to must be {allowed}` | | d
  | the recipient is `managers`, `buyers` or `everyone` and that group is not one the sender may send to in
  this mode (2.4) |"
- Case: The rows are lettered a, b, d, e, f, g, h, i, and 2.4 says "letters are the rows of 2.3": there is no
  row c. A recipient that is a text but names nothing special (for example a whitespace-only text) could have
  been a check of its own between b and d.
- Reading taken: No check is missing: the eight rows are the whole list. An empty text is "not given" (row a);
  any other text that is no group key goes to row e.

### Q64. Whether the mock summary counts as a summary written
- Where: spec/messaging.md, 5.5 Under the offline mock (against running.md 6.3 F and 6.7)
- Quote: "the summarizer makes no call and costs nothing; its reply is"
- Case: A conversation reaches 11 messages in an all-mock episode. Is `(mock summary of 10 messages)` a
  summary written: stored as the conversation's summary, logged under F ("every summary written"), counted in
  `summaries_written` ("summaries stored")? running.md 7.7 asks the smoke logs to show summaries in every mode
  other than off, which suggests yes, but 5.5 does not say.
- Reading taken: Yes: it is stored with the number it covers, logged as a summary written with cost 0, and
  counted in summaries_written. No summarizer_call record, summarizer_calls stays 0, no cost-cap check.

### Q65. An update that fails while an older summary is stored
- Where: spec/messaging.md, 4.3 When a summary is written, item 4
- Quote: "Only when all 4 fail does the summary line show the placeholder (3.5); the summarized messages are
  not shown, and nothing is stored (the previous stored summary, if any, stays)."
- Case: A stored summary covers 10 messages; the conversation reaches 21 and all 4 update attempts fail. The
  old summary still exists. Is it shown for the first 10 (with messages 11 to 20 hidden), or is only the
  placeholder shown?
- Reading taken: Only the placeholder, with {E} = 20 and no {you} clause, as 3.5 and 4.4's example read. The
  old summary stays stored and is the {prior} of the next attempt's update request.

### Q66. "Message cursors" cannot be one position per conversation
- Where: spec/view.md 1 and spec/rounds.md 10.1, against spec/messaging.md 3.4
- Quote: "messages behind the placeholder of a summary that could not be written (4.4) are not seen, and stay
  new to the reader until a view shows them"
- Case: view.md and rounds.md speak of "message cursors" that move and go back. After a placeholder view, the
  messages shown in full are seen while older ones behind the placeholder stay new: one position per
  conversation cannot hold that (seen messages after unseen ones).
- Reading taken: The read state is per message: a count of the messages the market held at the reader's last
  view plus the list of earlier messages still new to it (Participant.seen, Participant.unseen). A lost view
  puts both back.

### Q67. A failed summarizer call's reported cost
- Where: spec/messaging.md 5.4 Cost (against spec/models.md 4.4 and running.md 6.3 F)
- Quote: "Each call's dollar cost, including a call whose reply was then unusable, counts toward the cost cap
  and the episode's `summarizer_dollars`"
- Case: A summarizer call fails after the model layer's retries, and its failed attempts reported a cost of
  $0.25. models.md 4.4 says such a cost is "not part of any participant's or the summarizer's dollar cost";
  running.md F asks each failed attempt's record to carry "its cost".
- Reading taken: summarizer_dollars sums the costs of replies only (usable or not). A failed call adds nothing
  to it; its failed attempts' cost is logged with the call record (failed_cost), and the failed attempt's own
  cost is logged as 0.

### Q68. "The text as sent" for a message text that is not a string
- Where: spec/running.md 6.3 E, message (named by spec/messaging.md 7)
- Quote: "sender, recipient as given, the text as sent (uncut) and as recorded"
- Case: send_message with text 0 (a JSON number) or an object. Is "the text as sent" the JSON value, or its
  TEXT conversion before the cut?
- Reading taken: The converted text, uncut ("0", the object's PY form). The raw value is in the action
  record's arguments as sent.


## The action phase and corrections (`rounds.md` 6 to 8)

### Q69. An acceptance whose bid resolves but whose post does not
- Where: spec/rounds.md 6.3 (table rows A8, B_accept, A9) with 6.2 (targets table)
- Quote: "`accept_bid` with a resolved post"
- Case: 6.2 gives accept_bid three targets, "bid (from `bid_id`), its post, and its manager", and A10 names
  only "the manager of the bid y accepts". The wording suggests an acceptance whose bid resolves while its
  post does not, but targets are looked up on the market (not on a shadow, where 8.6 can hide a post), and
  there every bid's post exists.
- Reading taken: The post and the manager resolve exactly when the bid does; an accept_bid has either all
  three targets or none. All 700 vector cases agree.

### Q70. The class of an action that is not its participant's own
- Where: spec/actions-buyer.md 12 against spec/rounds.md 6.4
- Quote: "| 6 | bid |"
- Case: actions-buyer.md 12 heads its table "actions (both roles)" and gives the classes by name alone, so a
  buyer's `bid` reads as class 6 and a manager's `edit_post` as class 7. rounds.md 6.4 puts in kind 0 "every
  action that is not its participant's own (6.2), whatever its name".
- Reading taken: rounds.md 6.4: kind 0 for any action that is not its participant's own, as the vectors'
  not-own cases require. actions-buyer.md 12 does not state the exception.

### Q71. Whether compute and the unavailable names are a participant's own
- Where: spec/rounds.md 6.2, second paragraph
- Quote: "An action that is not one of its participant's own actions resolves no target, whatever its
  arguments: an action of the other role, an unknown or unnamed action, or `send_message` for a role that
  cannot send"
- Case: `compute` sent as an action (actions-buyer.md 1.1: "One more name runs but is never listed to agents")
  and the five names of 1.2 step 3 (`verify`, ...) are in none of the three groups listed, so the text does
  not say whether they are the participant's own.
- Reading taken: Not own: own is the role's actions of actions-buyer.md 1.1, `pay`, and `send_message` when
  the role can send. Nothing observable depends on it: these names have kind 0 and no targets either way.

### Q72. The lost clash of an acceptance that failed for another reason
- Where: spec/rounds.md, 8.2 "What each failed action is told"
- Quote: "otherwise, among the edges into it that were held and whose first action succeeded and ran before
  it, the one whose first action ran earliest gives the rule and the winner"
- Case: ro-541 (also noted in the design as ro-545): B-42c's `accept_bid` of bid-36 fails with `bid-36 on
  post-26 was replaced earlier this round: its terms are now 17 credits (plain), not the ones your turn saw:
  no contract was made`. A held A10 edge from M-f2b's successful test_result runs before it, so the expected
  note is `(this round M-f2b went out of business before your acceptance ran, so no contract was made and
  nothing was charged)` and the fact is `Open bids on post-26 now: bid-36 by M-f2b at 17.` M-f2b is in
  business; the note tells the buyer something false.
- Reading taken: Literal, as the vector fixes it: the rule of the earliest such edge gives the note, whatever
  made the action fail. The first test (target bid's manager out of business now) is likewise applied whatever
  the acceptance's own error was.

### Q73. Which effects reach the shadow after a success on the market
- Where: spec/rounds.md, 6.6 "Running one action", step 4
- Quote: "After a success on the market, the action's effects are part of the participant's shadow, and a bid
  it created has, on the shadow, the id the market gave it"
- Case: Not said whether these are the effects the action had on the market or the effects it has when applied
  to the shadow. A buyer's `accept_bid` on a post where another manager placed a bid this round: on the market
  that bid becomes lost; on the buyer's shadow it does not exist.
- Reading taken: The action as applied to the shadow: the trial's effects are kept after a market success,
  with the shadow's bid counter set to the market's before the trial so a new bid gets the market's id.
  Nothing the participant's later actions can read differs. All 700 cases agree.

### Q74. How the plan runs alone for the "needed" note
- Where: spec/rounds.md, 8.2, the "needed" note
- Quote: "this action would have succeeded had the participant's whole plan run alone, in order, on its
  first-phase shadow as the phase started"
- Case: Not said whether, in that run, a failed action leaves the state unchanged and later actions go on, nor
  whether 6.7's stopping applies. A plan [test_result that takes the balance to 0, deliver]: with stopping the
  deliver does not run; without it, it runs and fails as out of business.
- Reading taken: Each action runs in order on one shadow; a success is applied, a failure changes nothing,
  nothing is fixed in advance. No separate stop: every action of a participant out of business fails anyway,
  so both readings give the same answer to "would have succeeded".

### Q75. What a correction's shadow shows of removed ids
- Where: spec/rounds.md, 8.6 "Reading the corrections and the correction phase"
- Quote: "the bids created in this round whose manager is not this participant and that were not shown to it
  in its facts (8.3); and every bid on a removed post"
- Case: Two things are not said. (1) Whether "every bid on a removed post" also removes the participant's own
  bid or a bid shown in its facts. (2) What a text that names or lists a removed id shows on that shadow, e.g.
  `; its task is posted again as {post2} (open)` (actions-manager.md 2, check 2) when post2 is a hidden
  repost. No vector case shows either.
- Reading taken: (1) Literal: every bid on a removed post is removed, own and shown ones included. (2) A
  removed id exists for no lookup and no listing on that shadow, so that `{again}` clause is empty and the
  `Open posts:` hint leaves the post out.


## Views (`view.md`, `view-contracts.md`)

### Q76. `production.sig_c x 100` in the number formats table
- Where: spec/view-contracts.md, 2.2 Number formats
- Quote: "| `production.sig_c x 100`, `tests.manager_cost` | `G` |"
- Case: No line of the file writes production.sig_c x 100. Section 5's costs line writes four spreads, 'the
  four cost spreads the manager's system prompt states ... each `PCT0`' (default 19%, 16%, 25%, 25%), not
  sig_c x 100 (25) in G.
- Reading taken: Section 5 decides: the four spreads of prompts.md 3.3 in PCT0, taken from prompts.fields;
  only tests.manager_cost is written in G. The sig_c x 100 entry of the table is treated as unused.

### Q77. The order of hidden post ids, "newest first"
- Where: spec/view-contracts.md, 3.1 Which posts, in what order (and 4.1 for tasks)
- Quote: "`({h} more open {posts} not listed: {NAMED(hidden post ids, newest first)})`"
- Case: A manager with open_shown 3 and 5 open posts, its own open bid on the newest one: the hidden posts are
  one old post it has not bid on and the newest post it has bid on. 'Newest first' by creation gives
  'post-new, post-old'; the order of the sort that picked them (rank ascending, then newest first) gives
  'post-old, post-new'. The example view (post-18, post-11) has one rank only and does not decide. The same
  question holds for the hidden tasks of 4.1 ('of the hidden tasks, newest first').
- Reading taken: The hidden ids are named in reverse creation (arrival) order, whatever their rank.

### Q78. The status word for a release at the market's close
- Where: spec/view-contracts.md, 6.4 {status}, escrow case 1
- Quote: "`{how}`: `approved`, `released with no decision` (automatic release) or `the arbiter ruled` (a
  ruling for the manager)"
- Case: A submission under review when the market closes is released at the close (episode.md 6, step 2;
  stored as release_how 'close'). No view is built after the close, but a render of the final state (for the
  log or a check) needs a word for it.
- Reading taken: Any release that is neither an approval nor a ruling reads 'released with no decision', the
  close's release included.

### Q79. Two definitions of a result's mean reading
- Where: spec/view.md 9.2 ({readings}) against spec/processes.md 2.3 ({mean})
- Quote: "the mean of the buyer's readings of that result (the exact mean, then the nearest double)"
- Case: processes.md 2.3 defines the same mean as "SUM(all n readings, in the order taken) / n, one
  double-precision division". For one result read 6.8, 6.0, 6.2, 7.6, 0.0, 1.3 the exact mean rounds to the
  double 4.65, which F1 writes 4.7, while SUM / 6 is 4.6499999999999995, which F1 writes 4.6. So the record
  line reads `mean 4.7 over 1 result` under one rule and `mean 4.6 over 1 result` under the other.
  processes.md says "The buyer's record line averages per-result means by its own rule", so it looks
  deliberate.
- Reading taken: As view.md writes it: each result's mean is the exact mean rounded to the nearest double
  (Python's statistics.mean), then m = SUM(those means) / n. A test pins the 4.7.

### Q80. The turn counter against the last view not lost
- Where: spec/view.md 1 against spec/rounds.md 10.1 (and episode.md 5.1, running.md 5)
- Quote: "the participant's **turn counter** rises by 1 (it counts the views built for it; section 10 uses it,
  and so does `episode.md`, "5.1 Definitions", for whether a buyer has had a turn that saw a delivery)"
- Case: rounds.md 10.1 says the lost view "counts as never built: it saw no delivery, not even those it
  showed, and the participant's next view that is not itself lost sees every delivery made before that view
  was built, including those made earlier in this round (in its first action phase, when the turn is lost at
  its correction call)". A first delivery made in round g's first phase, with the buyer's turn of round g then
  lost at its correction call: a turn counter noted at the delivery is not below the buyer's counter after its
  next view, so a counter comparison says the delivery is still unseen although rounds.md says that next view
  saw it.
- Reading taken: rounds.md 10.1, as the design decided: build records the round index of the view in
  Participant.last_view and undo puts the earlier value back; the turn counter is used only for the memory
  title (section 10). episode.py compares the delivery's round index with last_view.

### Q81. A stored memory that is only whitespace after the cut
- Where: spec/view.md 10 with spec/turns.md 10.1 and 10.2
- Quote: "`{memory}`: the stored memory exactly as written (at most `turns.memory_chars` characters,
  `turns.md`, "Writing it"): one item,"
- Case: turns.md 10.1 says "An empty or whitespace-only memory counts as none" and 10.2 stores the text "cut
  to its first `turns.memory_chars` characters (no trimming, line breaks kept)". With turns.memory_chars = 3
  and a memory of three spaces followed by `plan`, the memory is not blank, so it is written, and what is
  stored is three spaces. The spec does not say whether the next view then shows a dated title over an item of
  three spaces, or the title alone as for a section with nothing in it (2.2: "A section with nothing in it is
  its title line alone").
- Reading taken: Shown as stored: the dated title, one empty line, then the stored text as it is, whatever it
  holds. A participant that has a stored memory never gets the title-alone form.

## Later additions

### Q82. Whether the due-day notice to the buyer also tests when the contract was signed
- Where: `episode.md`, "3.3 Per contract: result arrivals, then its due-day notice", (b) item 2, against
  `processes.md`, "3.3 The due-day notices", item 2
- Quote: `episode.md`: "This happens only when the contract's deadline day is later than the task's due day: a
  contract signed before the due day but fewer than `contracts.min_contract_days` days before it";
  `processes.md`: "Else, if the task's due day is today, the contract is not ended, not settled, and is not a
  plain contract with a delivery: to the buyer only"
- Case: a contract whose deadline is after its due day, with the due day today, but signed on or after the due
  day. Normal play cannot produce it (a contract is never signed at a day's start); a state built by hand can.
- Reading taken: `processes.md`'s condition is the rule and `episode.md`'s sentence a consequence of it under
  normal play: the notice tests the deadline, the due day and the contract's state, not the signing day.

### Q83. A sum shown in a view that is not finite
- Where: `config.md`, section 6, "Finiteness during an episode", with `view-contracts.md` 11.4 (`{sum}`) and
  `view.md` 8 and 9 (the escrow held, the sums of the record with each counterparty)
- Quote: "an amount computed during an episode infinite or not a number: a start balance or per-task budget
  (6.4), a run's cost, a charge, a credit, a balance, a payment, a value, a score or a sum of these. Such a
  value must never be used, shown or logged as an amount"
- Case: a manager with three finished plain contracts at 1e308 each, delivered and unpaid, with
  `view.finished_shown` 1: the two older unpaid prices sum to infinity. The list names neither a price nor an
  unpaid remainder, so the text does not say whether the summary reads `inf credits of their prices unpaid` or
  the episode stops.
- Reading taken: as Q23 and Q41: a sum shown to an agent is an amount, so writing the view fails naming the
  value and the episode stops with status `error`. The same holds for the per-category totals of "Your accounts
  so far" (`view.md` 8), which two very large payments back and forth can push past the largest double while
  both balances stay finite.

## Rounds (`rounds.md` 8 to 10)

### Q84. The stopped-actions note when no action was stopped
- Where: spec/turns.md, 9 "Result lines", with spec/rounds.md, 6.7
- Quote: "`{n}` `INT`, the number of the participant's actions of that phase that were stopped; `{s}` is `s`
  unless n is 1."
- Case: A manager whose last planned action (a test_result that takes its balance to 0) puts it out of
  business: nothing after it was stopped. The text does not say whether the note then appears with n = 0
  ("your remaining 0 actions did not run") or not at all. The vectors do not carry the note.
- Reading taken: No note when no action was stopped; the note appears only with n >= 1.

### Q85. Lost turns later in the wave when the abort is reached
- Where: spec/rounds.md, 10.1 "Lost to a failed model call", item 3
- Quote: "If that count is now greater than 5 x `time.rounds_per_day`, the episode is aborted at once with
  status `aborted_lost_turns`, not settled"
- Case: 14 turns lost so far and three chains of one wave fail. The second in the turn order is the 16th. Not
  said whether the third is still restored, counted and logged before the abort (the log and the count in the
  stop record's error text differ by one).
- Reading taken: At once: play raises Stop while handling the 16th; lost turns later in the turn order of that
  wave are not restored, counted or logged (the day is abandoned and replayed).

### Q86. A summary written while building a view that is then lost
- Where: spec/rounds.md, 10.1, item 1 (with spec/view.md, 1 and spec/messaging.md, 4.3)
- Quote: "The participant is restored as if this round's view had not been built: its pending notifications
  are those its view showed, followed by any received since; its message cursors go back"
- Case: With message_channels on, a conversation summary was written (and its summarizer call paid for) while
  the view of a turn later lost to the provider was built. The list of what a restore undoes does not mention
  it: is the stored summary removed, so the next view asks for it again, or kept?
- Reading taken: Kept: the stored summary, its log records and its cost stand; only the notifications, the
  read state, the turn counter and the last view go back (view.undo), and the views-built metric is not
  decremented.

### Q87. A correction that calls act with arguments that cannot be read
- Where: spec/rounds.md, 8.6, against 10.2 and spec/turns.md, 11.6
- Quote: "an unreadable correction (a reply with no act call among them) gives no correction plan"
- Case: A correction reply that does call act, but with arguments that cannot be read (for example "actions":
  "bid"). 8.6's parenthesis names only a reply with no act call; 10.2 says "(an act call that cannot be read,
  or a reply with no act call, 8.6)" and turns.md 11.6 lists "an act call that cannot be read" too.
- Reading taken: Both are unreadable corrections, as turns.md 11.6 and rounds.md 10.2 say: the same reading as
  the decide reply's (reading.read_reply), with its format-error text as {err2}.


## Acceptance check 4

### Q88. The state behind the example correction turn
- Where: spec/README.md, "Acceptance checks", item 4
- Quote: "to reproduce a file, have a test model return those assistant messages in that state and compare the
  messages the market writes"
- Case: examples/turn_with_correction_off.json is a request of day 1, round 2. Its state is the end of round
  1, which no file gives: what each participant's model replied in round 1, that round's turn order, and the
  replies' costs. The file's view shows only the results: post-3 by B-d75 listed before post-1 by B-c53, both
  `prefers escrow, threshold 7`; `Results of your actions:` / `(no actions run)`; the memory `mock memory`;
  `Spent: tokens 0.00.` at balance 76.80.
- Reading taken: Round 1 is rebuilt from the file's own view lines and from view.md 14.1. Each buyer's model
  posts its first task with form escrow and threshold 7; B-d75 ranks before B-c53 in the turn order
  (view-contracts.md 3.1: posts are listed in creation order); no manager acts; every act carries the memory
  `mock memory`; each reply costs a positive amount small enough that the turn's charges sum below 0.005
  credits. The file's first message is then compared as market output, but its content partly restates these
  inputs.


## Settlement and metrics (`running.md` 6.5 to 6.7)

### Q89. Rounding of a mean of credit amounts
- Where: spec/running.md 6.7, conventions, against the rows buyer_gain_mean, manager_profit_mean,
  price_mean_plain, price_mean_escrow
- Quote: "credits are round(x, 2); rates and means are round(x, 4) and are null when their denominator (or
  set) is empty"
- Case: Two buyers with recorded scores -7.95 and -43.96: their mean is -25.955. It is a mean, but of credits:
  as a credit it would be recorded with 2 decimals, as a mean with 4. The same holds for the mean prices (160
  / 9 = 17.7778 or 17.78).
- Reading taken: A mean is a mean whatever it averages: round(x, 4) for all four (-25.955, 17.7778).

### Q90. What "of which" refers to in refine_runs
- Where: spec/running.md 6.7, "Deliveries and true quality", row runs_started, own_runs, refine_runs
- Quote: "runs started; of which by the manager itself; of which refinements"
- Case: A refinement run by the employee (delegate_run): it is a refinement among the runs started, but not
  among the manager's own runs. The second "of which" can refer to either.
- Reading taken: Both "of which" refer to the runs started: refine_runs counts every refinement, by the
  employee or the manager.

### Q91. A payment toward no contract in paid_beyond_prices
- Where: spec/running.md 6.7, "Payments, and unpaid plain deliveries", row paid_toward_prices,
  paid_beyond_prices
- Quote: "credits of all payments that went toward plain prices; credits paid on top of a price"
- Case: A payment of 2.5 from a manager to a buyer that counts toward no contract: it is not toward a price,
  and there is no price for it to be on top of. If it counted as beyond a price, paid_toward_prices +
  paid_beyond_prices would equal payment_credits; if not, they differ by payment_credits_without_contract.
- Reading taken: paid_beyond_prices is the sum of the parts beyond the price of the payments that counted
  toward a contract (the sum of 6.5's paid_beyond_price); a payment toward no contract is in neither figure.

### Q92. A rejection the arbiter overturned in escrow_rejected
- Where: spec/running.md 6.7, "Escrow outcomes", row escrow_approved, escrow_rejected
- Quote: "submissions approved; rejected"
- Case: A submission the buyer rejected, the manager disputed and the arbiter then released: its final state
  is released, but a rejection was made on it.
- Reading taken: Every rejection decision counts, also one the arbiter overturned; a submission's outcome
  stays 'rejected' after a ruling.

### Q93. A ruling at the close in arbiter_fees_owed and arbiter_fees_paid
- Where: spec/running.md 6.7, row arbiter_fees_owed, arbiter_fees_paid, against the costs row and
  spec/episode.md 6 step 1
- Quote: "rulings for the manager during the market with a fee above 0; of which the buyer paid the fee
  (`pay_escrow_arbiter`)"
- Case: A dispute ruled for the manager at the close with a fee of 20 that the buyer's balance covers: the fee
  is charged and is in costs.dispute_costs ("arbiter's fees paid, at the close included"), but the ruling was
  not "during the market" and the fee was not paid with pay_escrow_arbiter.
- Reading taken: As written: a ruling at the close counts in neither arbiter_fees_owed nor arbiter_fees_paid,
  whether or not its fee was charged; the charged fee counts in costs.dispute_costs.

### Q94. A manager brought back at the close in back_in_business_events
- Where: spec/episode.md 6 (last paragraph but two) with spec/running.md 6.7, row back_in_business_events
- Quote: "A credit to an out-of-business manager in steps 1 and 2 (a returned dispute deposit, an escrow's
  payment of more than 0) brings it back in business by the rule of 9.3"
- Case: A manager out of business wins a dispute ruled at the close: its deposit comes back and lifts it above
  0. The metric is "times a manager came back"; the spec does not say whether a return at the close, after the
  last turn, is one of those times.
- Reading taken: Counted (and logged as back in business), as any other return: the metric then agrees with
  managers_out_of_business_at_end, which is the state after these credits.

### Q95. A released escrow whose result never became the buyer's in successful_contracts
- Where: spec/running.md 6.7, row successful_contracts
- Quote: "delivered contracts with settled q >= 7 that are escrow, or plain and paid in full (7 is fixed, not
  a parameter)"
- Case: An escrow released by a ruling for the manager whose buyer never paid the arbiter's fee: settled q
  7.8, but the result never became the buyer's and the contract realised 0. By the definition it is a
  successful contract.
- Reading taken: As written: every released escrow with settled q >= 7 counts, received or not.

### Q96. When the close record is written relative to the checkpoint
- Where: spec/running.md 5 ("Checkpoint") against 6.3 C, row "the close"
- Quote: "After every completed day (after the end-of-day close check, so also after the close day, before the
  settlement), a checkpoint of the episode is saved"
- Case: An episode interrupted after its close day's checkpoint and resumed: the log is cut back to the
  checkpoint's position. If the close record is written after the checkpoint it is lost and must be written
  again on the resume; if before, it stays. The spec does not say on which side of the checkpoint's position
  the record of the close lies.
- Reading taken: The close record is written when the rule decides, at the end of the close day and before the
  checkpoint, so a resumed closed episode keeps it and goes straight to the close's steps without logging it
  again.


## Running, resume and the log (`running.md` 5 and 6)

### Q97. Which model-spec keys count as the same config for a resume
- Where: spec/running.md, 5. Checkpoints and resuming, Resume (market/run.py, _compared)
- Quote: "in each spec a key left out is the same as the key given with the value that takes its place when
  absent (`config.md`, "3.4.1 A model spec": an agent spec without `temperature` equals one with
  `llm.temperature`'s value, the summarizer's spec without it one with `temperature: null`, a spec without
  `max_tokens` one with `llm.max_tokens`'s, a spec without `extra_body` one with `{}`)"
- Case: The examples name three keys; 3.4.1's table has more. (a) An openrouter spec without `base_url` and
  `api_key_env` against one that gives `https://openrouter.ai/api/v1` and `OPENROUTER_API_KEY`. (b) A mock
  spec without prices against one with `price_in_per_m: 0`: 3.4.1 gives no value that takes a price's place,
  and models.md 4.9 treats a missing price (rule 3: free or unknown) differently from a price of 0 (rule 2:
  computed). (c) A role's spec changed while every seat of the role has its own entry.
- Reading taken: (a) the same config: an absent base_url or api_key_env equals the provider's own (models.md 3
  table). (b) another config: a price left out is not a price of 0, except for the mock, where a missing
  price counts as 0 (Q108). (c) the same config: only the spec each
  seat ends up with is compared, so a role spec no seat uses does not count.

### Q98. Record numbers after a resume
- Where: spec/running.md, 5. Checkpoints and resuming, The guarantee (against 6.2)
- Quote: "the same log records, apart from the one resume record, wall-clock times, measured durations"
- Case: 6.2 says every record "can be located by its position in the log (for example by a sequence number)".
  After a resume every later record sits one place further, so its sequence number differs from the
  uninterrupted log's. An episode stopped on days 3 and 6 has two resume records, not one; and when a resumed
  day is abandoned again, its resume record lies after the checkpoint's position and is cut with that day.
- Reading taken: The record number `n` is the record's line in its own log, resume records counted. "The same
  records" is compared without `n`. One resume record stays per resume whose day was completed; tests compare
  on that basis.

### Q99. What becomes of a checkpoint that is not used
- Where: spec/running.md, 5. Checkpoints and resuming, last paragraph of Resume
- Quote: "the episode starts from the beginning: its log is begun afresh, and if a checkpoint existed, the log
  records why it was not used"
- Case: Not said what happens to the unused checkpoint. Run under config A stops after day 5. A run under
  config B starts afresh (the log is now B's) and is killed in day 1, before any new checkpoint. A third run
  under config A finds A's old checkpoint usable, against a log that is B's; if B's partial log happens to
  hold as many records, the resume would continue on another episode's log.
- Reading taken: The unused checkpoint is deleted when the fresh log is begun: the log it counted on no longer
  exists, so it is no checkpoint of this episode.

### Q100. NaN and the infinities in a log record
- Where: spec/running.md, 6.2 The log: form
- Quote: "in JSON-lines form: one JSON object per line ... can be read back exactly (texts as sent, numbers as
  doubles without rounding unless a rounding is stated)"
- Case: turns.md 7.2 accepts `NaN`, `Infinity` and `-Infinity` in a reply, and 6.3 E requires an action's
  "arguments as sent". JSON has no such numbers, so a record holding one cannot be both strict JSON and exact.
- Reading taken: Written as Python's json module writes them (`NaN`, `Infinity`, `-Infinity`) and read back
  exactly by it. A strict JSON reader rejects that one line. No encoding of its own was invented.

### Q101. `wall_s` across runs of one episode
- Where: spec/running.md, 6.4 The summary, `wall_s`
- Quote: "wall time spent on the episode in seconds, summed over its runs (optional)"
- Case: An episode stops after day 5 and is then started from the beginning (its checkpoint unreadable or for
  another config, or it was killed in day 1 and has none). Whether the earlier runs' time still counts is not
  said. A killed run writes nothing on the way out, so its time is never recorded either way.
- Reading taken: Summed over the runs that resumed: carried from the previous summary on a resume, counted
  from 0 on a fresh start.


## The offline mock (`running.md` 7)

### Q102. A mock manager's overdue plain contract in the finished list
- Where: spec/running.md, 7.4 A mock manager's actions, rule 2
- Quote: "for each of its contracts that can still take work (a plain contract with no delivery; an escrow
  that holds its price with no submission under review and no dispute open), in view order, one action"
- Case: A plain contract with no delivery that is overdue by view.overdue_days and has no running or recent
  result is in the manager's finished list, as `... | NOT DELIVERED | 0 results`, with no result lines. The
  buyer's rules name their sections (rule 2: in "Your active contracts"; rule 3: active, then finished); this
  rule names none. By the letter such a contract can still take work and has no run in progress, so it would
  get a fresh run.
- Reading taken: Only the items of "Your active contracts" are worked: they are the ones whose result lines
  the view shows. A contract in the finished list gets no action, so a plain contract that goes overdue before
  its first run is never served by a mock manager (pinned by
  test_a_contract_in_the_finished_list_takes_no_work).

### Q103. Tasks and posts the view only names
- Where: spec/running.md, 7.3 rule 5 and 7.4 rule 1
- Quote: "the first task in its queue: `post_task` on it"
- Case: With more than view.open_shown tasks (or open posts), the oldest queued tasks are not listed in full
  but named in `(2 more tasks not listed: task-2 (queued), task-1 (queued))`; hidden posts are named likewise,
  without saying whether the manager has bid on them. The first task in the queue by arrival is then a hidden
  one. The same doubt applies to "the first open post in the forum it has not bid on".
- Reading taken: View order over the lines listed in full: the first task line that reads QUEUED, and the
  first post line that ends `you have not bid`. Tasks and posts the view only names are not acted on (pinned
  for posts by test_a_post_the_view_only_names_is_not_bid_on).

### Q104. Where the mock reads the action cap from
- Where: spec/running.md, 7.2 Its replies in a turn, item 2
- Quote: "at most `turns.max_actions` items, in the order given there, cut after that"
- Case: The mock's reply depends only on the request, and its backend is given only the model spec, so the cap
  must be read from the request. It is stated in the system prompt (`Actions beyond the first 6 are dropped.`)
  and in the act definition's descriptions (`a list of up to 6`). The spec does not say which the mock reads.
- Reading taken: From the system prompt's sentence `Actions beyond the first {max_actions} are dropped.`, the
  same message it reads its ID from. If the sentence is absent the list is not cut (the market cuts it
  anyway).

### Q105. A correction on the text path, as the mock sees it
- Where: spec/running.md, 7.2 Its replies in a turn, item 3
- Quote: "A correction (the request already holds an `act` call of this turn)"
- Case: On the correction's text path (turns.md 11.4) the first act is re-sent as an assistant message without
  tool_calls, so the request holds no act call although it is a correction and offers only act. The mock's own
  replies always take the tool path, so this arises only if another reply is mixed into a mock's turn.
- Reading taken: The letter: a correction is a request in which some message's tool_calls hold a call named
  act. A request that offers only act but holds no act call is answered as item 2, with the rule's actions.

### Q106. "Neither tested nor delivered" for a result submitted and rejected
- Where: spec/running.md, 7.4 rule 2, first bullet
- Quote: "a result of the contract has arrived that it has neither tested nor delivered: `test_result` on the
  first such result in view order"
- Case: On an escrow, a result that was submitted and rejected and that the manager never tested is, word for
  word, "neither tested nor delivered" (it was submitted, not delivered). The second bullet says "has not
  delivered or submitted".
- Reading taken: "Delivered" in the first bullet covers submitted too: a result whose line carries SUBMITTED,
  DELIVERED or delivered is neither tested nor delivered again. An escrow whose only results were rejected
  gets a fresh run.

### Q107. An escrow past its close day that still counts as able to take work
- Where: spec/running.md, 7.4 rule 2
- Quote: "an escrow that holds its price with no submission under review and no dispute open"
- Case: An escrow held open past its close day by a rejection still open to dispute holds its price with
  nothing under review and no dispute open, yet deliver is refused there (actions-manager.md 6.1: "takes no
  new submission from its close day"). By the rule the mock still works on it, and a deliver fails and brings
  a correction.
- Reading taken: The letter: such an escrow counts as able to take work; no check of the close day is made.

## Raised by the audits of the round, the episode, the mock and the command

### Q108. A mock spec with and without prices of 0, for a resume
- Where: spec/running.md 5 (Resume), against 7.1 and config.md 3.4.1 / models.md 4.9; narrows recorded Q97(b)
- Quote: ""in each spec a key left out is the same as the key given with the value that takes its place when
  absent (`config.md`, "3.4.1 A model spec": ...)" and 7.1: "with the seat's spec's prices (a missing price
  counts as 0)""
- Case: A checkpoint written for {provider: mock} resumed under {provider: mock, price_in_per_m: 0,
  price_out_per_m: 0}; the same pair for an ollama spec or for a summarizer spec on openrouter. 3.4.1's 'when
  absent' column for the prices only says 'see "prices" below'; the examples in running.md 5 name temperature,
  max_tokens and extra_body, not prices.
- Reading taken: For the mock a missing price is the same as a price of 0 (cost by 7.1's formula, cost source
  'computed' either way, prompt price 0), so the checkpoint is used. For every other provider a missing price
  stays different from 0: models.md 4.9 gives rule 3 (free or unknown, and for ollama 'unless the spec gives
  both prices') for a missing price and rule 2 (computed) for a price of 0, which changes costs or the logged
  cost source. Q97(b) states the same exception.

### Q109. "By value" for booleans and numbers in the config comparison
- Where: spec/running.md 5 (Resume): 'by value' for numbers and booleans
- Quote: ""Configs are compared as resolved (6.1), by value: the order of keys does not matter, and a key left
  out is the same as the key given with its default value.""
- Case: extra_body {stream: false} against {stream: 0}; and extra_body {n: 1} against {n: 1.0}, or
  credits_per_usd: 1 against 1.0. The first pair puts different JSON values in the request body; the second
  pair is the same number, though its JSON text in the request body differs (1 vs 1.0).
- Reading taken: A boolean never equals a number (another config). An integer and a float of the same numeric
  value are the same value (the checkpoint is used), in extra_body as everywhere else, although a provider
  that distinguishes 1 from 1.0 would then see a different body after the resume.

### Q110. The report of an episode that is already finished
- Where: spec/running.md 2, step 4 and step 6: the report of an already-finished episode
- Quote: ""An episode whose summary (section 6.4) exists and has status `ok` is finished: it is not run again
  (whatever the config now says) and is reported as already done.""
- Case: A summary.json that is a JSON object with status ok but without the other fields of 6.4 (for example
  {"status": "ok"}, hand-written or from another tool). Step 6's report content (welfare, dollar cost) is
  stated for episodes as they end, not for ones already done.
- Reading taken: It counts as finished whatever else it holds; the report says only `seed N: already done` and
  no longer repeats the welfare and dollars of the stored summary.

### Q111. A failure after the settlement is in the log
- Where: spec/running.md 3, step 6 and 4.3: a failure after the settlement is in the log
- Quote: ""They are written to the log, then the summary is written with status `ok`. The checkpoint may then
  be deleted." and 4.3: "A failure while settling, scoring or computing the metrics after the close also gives
  `error` (with the error text marking it as a settlement failure); the checkpoint is kept""
- Case: (a) The settled record is in the log but the ok summary cannot be written (disk full). (b) The ok
  summary is written but the checkpoint cannot be deleted.
- Reading taken: (a) status error marked as a settlement failure, a stop record after the settled record, no
  settled content in the summary, checkpoint kept; the next run cuts the log back to the checkpoint and
  settles again. (b) the episode stays ok and the checkpoint stays in the folder; nothing is logged about it.

### Q112. A view whose results text or memory quotes section titles, as the mock reads it
- Where: spec/running.md 7.2 (first paragraph) with spec/view.md 2.3, last sentence
- Quote: ""Text a participant wrote (a memory, a quoted message) never creates a section, a title or an item
  of a section, whatever it contains""
- Case: Both the results text (first section) and the memory (next to last) can hold any lines, title lines
  included. A results text that quotes a line `--- Notifications since your last turn ---` and later a line
  `--- Your self-written memory ---` (an argument with line feeds quoted in a refusal) gives a view whose
  titles read exactly like those of a view whose memory quotes a whole false market: R, N, ..., Mem, N, ...,
  Mem, Final. Which block is the market cannot be told from the titles; only knowing every result message's
  format would tell. Not reachable in an all-mock episode (the mock's own results and memory hold no line
  breaks), only by a request built by hand.
- Reading taken: The mock reads the lines after the last Notifications title that precedes the first memory
  title following a Notifications title, up to that memory title. Correct when only one of the two texts
  quotes titles, and for a results text that quotes anything but a Notifications title followed by a memory
  title; in that one case the quoted lines are taken for the market, and the mock still replies (never fails).

### Q113. Whether the mock's payment amount is rounded
- Where: spec/running.md 7.3 rule 3, against spec/README.md 'Number formats and rounding'
- Quote: ""the amount X − P (P and X as that line shows them)""
- Case: `paid 0.10 of 0.30`: as decimals X − P is 0.20; as doubles 0.3 − 0.1 is 0.19999999999999998. The
  builder sent round(X − P, 2) = 0.2, the auditor expects the double difference. The payment is the same
  either way (pay rounds to the cent); the act call's logged arguments and a correction request's input-token
  count differ.
- Reading taken: The double difference, unrounded: the README says amounts are doubles and "the spec states
  each other rounding where it applies", and rule 3 states none. Listed because two readers disagreed.

### Q114. Calls of a wave that are not in the log when a program cannot be run
- Where: `running.md`, "6.3 The log: required content", D ("each model call of a participant"), against "4. What
  stops an episode before its close"
- Quote: "the day in progress is abandoned where it is (none of its effects count: the next run replays it from
  its start, section 5); the log gets a stop record"
- Case: a participant's program cannot be started isolated when it comes up (`turns.md` 6.1: its episode stops
  with status `error`). The model call that sent the program, and the completed calls of the other chains of
  that wave, were paid for but are taken up and logged only after the whole wave, so they are not in the log
  before the stop record.
- Reading taken: left so. The day is abandoned and its records are removed at the next resume in any case
  (`running.md` 5: "the records of an interrupted day are removed from the log although their model calls were
  paid"); the run's cost tracker did count those calls.

### Q116. A configured reasoning effort has no effect under the market's forced tool_choice
- Where: `turns.md`, "4.1 tool_choice before each call"; `config.md` 3.4.1 (`extra_body`, e.g. the summarizer's
  `reasoning: {effort: high}`); `models.md`, "The body", step 8
- Quote: "otherwise tool_choice is `"required"` (the model must call a function: compute or act)" and "A model
  spec's provider-specific request fields may override tool_choice"
- Case: the paid run of 2026-11-15 (`configs/haiku55_medium_all.yaml`, every seat
  `anthropic/claude-haiku-5.5` with `extra_body: {reasoning: {effort: medium}}`): every one of the 534 calls of
  seed 2 sent tool_choice `"required"` or forced `act`, and all 534 came back with 0 reasoning tokens and no
  reasoning text. The same request outside the market gives 58 reasoning tokens with tool_choice `"auto"` and 0
  with `"required"`: Anthropic models do not think under a forced tool choice, and OpenRouter drops the
  `reasoning` field without an error. `qwen/qwen3.5-9b` (no effort set) reasons depending on who serves it: in
  seed 5 of `configs/qwen35_9b_all.yaml`, 14 of the 15 calls served by Together and both served by DeepInfra
  carried reasoning, forced tool choice or not, and none of the 172 served by PrimeIntellect did. So a seat's
  reasoning effort is silently a no-op for some models and depends on routing for others.
- Reading taken: none needed for the implementation, which follows 4.1. The spec already lets a seat's
  `extra_body` set `tool_choice` (for example `auto`), at the price of replies that call no function (a reading
  error and a correction). Whether the reference market meant seats to reason, and so whether its configs should
  set it, is for you to decide.
