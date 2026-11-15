# Spec questions

Places where `spec/` seemed wrong, incomplete or ambiguous while building from it. Each entry gives the
section, a quote, the case that shows the doubt, and the reading the build went on with. The user decides.
Every entry is open until marked otherwise.

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
  must fail.
- Reading taken: unpaid in every such case (the three `pay_escrow_arbiter` successes need it; no case needs
  the opposite).

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
- Reading taken: not read. Keys come from environment variables only, and the setup error reads `set
  {variable} in your environment`. Say so if `.env` support is wanted: it is a dozen lines.

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
