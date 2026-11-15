# Turns: one participant's turn as a model conversation

This file specifies what happens between a participant's view being built and its turn's results being stored: the
model conversation (the request messages and what each call sends back into it), the two functions the model is
offered (`compute` and `act`, with their exact definitions), which function the model must call at each step, how a
program sent to `compute` is run and what text comes back, how an `act` call is read into actions and a memory
(every shape read, every format error), the action cap, the memory, the correction call with its exact texts,
the text of the turn's results, and when the turn's model calls are charged to the participant. Who takes a turn,
the order in which turns' actions run and clash, and lost turns are in rounds.md; the view's text is in view.md and
view-contracts.md; what each action does and every action's own result text are in actions-buyer.md and
actions-manager.md; the model layer (request bodies, providers, retries, each call's dollar cost) is in models.md.
Nothing in this file depends on `message_channels`, except the note of section 7.5. The number
formats (`INT`, `REPR`, `TEXT`), "character", "whitespace", "trimmed", "lowercased" and the singular rule
are those of README.md, "Number formats and rounding"; "trimmed and lowercased" trims first.

## 1. A turn in outline

1. **View.** The participant's view is built from the round-start state (view.md). The turn's conversation starts
   with two messages: the participant's system prompt (prompts.md) and the view as the user message.
2. **Decide chain.** The model is called with the whole conversation so far. A reply that calls `compute` (and not
   `act`) gets its program run and its output added to the conversation, and the model is called again (sections 4
   and 5). The chain ends with the first reply that calls `act`, or a reply with no function call, or a reply that
   breaks the step bound, or a reply calling functions but not `act` after whose charge the participant is out of
   business (section 4.2). Nothing a model does during its chain changes the market; every taker's chain is
   independent of the others' and may run at the same time (rounds.md, "The first calls: in parallel, taken up in the
   turn order").
3. **Reading.** The reply that ended the chain is read into a list of actions and a memory, or is a format error
   (section 7). At most `turns.max_actions` actions are kept (section 8).
4. **Action phase.** Every taker's kept actions run together (rounds.md, "The action phase"); each produces a result
   line (section 9).
5. **Correction.** A participant with a failed action, or whose reply could not be read, gets exactly one more call,
   `act` only (section 11); its actions run in a second action phase (rounds.md, "Reading the corrections and the
   correction phase").
6. **Finish.** After the round's money from others has landed (rounds.md, "The round's credits land"), the memory is
   written (section 10) and
   the turn's results text is stored for the next view (section 12).

Every model call of the turn is charged to the participant when its reply is taken up (section 13).

## 2. The conversation's messages

The conversation is a list of messages, each with a role. The default model layer (OpenRouter) sends them in the
OpenAI chat-completions format shown here; another provider's adapter must carry the same content as far as its API
allows (models.md, "The messages, and re-sending a reply within a turn", says what may be dropped).

| message | fields |
|---|---|
| system | `{"role": "system", "content": <the participant's system prompt>}` |
| user | `{"role": "user", "content": <text>}`: the view (first), or a correction's body (section 11.4, text path) |
| assistant | `{"role": "assistant", "content": <the reply's visible text>, "tool_calls": <the reply's function calls>, "reasoning": <the reply's hidden reasoning>}`; `tool_calls` is left out only on a correction's text path (section 11.4); `reasoning` only when the reply had non-empty hidden reasoning |
| tool | `{"role": "tool", "tool_call_id": <the id of the call it answers>, "content": <text>}`; the id is the call's `id` exactly as the provider returned it (any JSON value), `null` when the call has none; calls that share an id are each answered with it unchanged (models.md, "4.7 Reading the reply") |

Rules for an assistant message that puts a reply back into the conversation:
- `content` is the reply's text with its think blocks removed (strip-think): remove every `<think>`...`</think>`
  block (shortest match, across lines); then, if `</think>` still occurs, keep only what follows its last
  occurrence; then, if `<think>` still occurs, keep only what precedes its first occurrence; then remove leading and
  trailing whitespace. The result may be the empty string. Example: `<think>hidden</think>Visible text` becomes
  `Visible text`.
- `tool_calls` is the reply's list of function calls exactly as the provider returned it (ids, `type`, `function`
  with `name` and `arguments`, and any extra fields the provider included), never rebuilt.
- `reasoning` is the reply's hidden reasoning text as the model layer returned it (models.md, "What a call
  returns"). It is sent back so the
  next call of the same turn need not redo it. It is never shown to any participant in any other way.

A turn's conversation exists only for that turn: the next turn starts again from `[system, user(view)]`.

## 3. The two functions

Two function definitions exist. Every call of the decide chain offers both, in the order `[compute, act]`; the
correction call offers `[act]` only (the same `act` definition). The definitions are identical for both roles and in
every `message_channels` mode.

### 3.1 Exact definitions at the default configuration

`compute` (key order as shown):

```json
{
 "type": "function",
 "function": {
  "name": "compute",
  "description": "Run a short Python program (standard library only, no access to the market or the network) and return what it prints, within this turn. One successful run per turn; a failed run gets one more attempt, once per turn. A compute call sent in the same reply as act doesn't run. Example: {\"code\": \"print(2 + 2)\"}",
  "parameters": {
   "type": "object",
   "properties": {
    "code": {
     "type": "string",
     "description": "the program, up to 20000 characters; it is stopped after 5 seconds of computing or 10 seconds in all, and the first 4000 characters of its output come back"
    }
   },
   "required": [
    "code"
   ]
  }
 }
}
```

`act` (key order as shown: `actions` before `memory` in `properties`):

```json
{
 "type": "function",
 "function": {
  "name": "act",
  "description": "End your turn: send up to 6 actions, which run after every participant has ended their turn, and your private memory. Example: {\"actions\": [{\"action\": \"<action name>\", \"<argument name>\": <value>}, {\"action\": \"<action name>\"}], \"memory\": \"<your memory>\"}",
  "parameters": {
   "type": "object",
   "properties": {
    "actions": {
     "type": "array",
     "items": {
      "type": "object"
     },
     "description": "the actions, in order: a list of up to 6 (any beyond the first 6 are dropped), each an object {\"action\": \"<action name>\", \"<argument name>\": <value>}. The list may be empty, [], when you take no action this turn."
    },
    "memory": {
     "type": "string",
     "description": "your private memory, up to 2000 characters (longer is cut), shown in your view at your next turn; it replaces your previous memory, and an empty memory leaves it unchanged"
    }
   },
   "required": [
    "memory"
   ]
  }
 }
}
```

The `items` schema is a bare object on purpose: no per-action schema is declared. Only `memory` is required: act may
carry no actions, as an empty list or with no `actions` at all (section 7.3).

### 3.2 The description templates

Only the descriptions depend on the configuration. Each text below is the description string itself (shown
unescaped; inside the JSON its double quotes are escaped as above).

compute's description (fixed; it does not depend on any parameter):

```
Run a short Python program (standard library only, no access to the market or the network) and return what it prints, within this turn. One successful run per turn; a failed run gets one more attempt, once per turn. A compute call sent in the same reply as act doesn't run. Example: {"code": "print(2 + 2)"}
```

compute's `code` description:

```
the program, up to {code_chars} characters; it is stopped after {cpu_s} seconds of computing or {wall_s} seconds in all, and the first {output_chars} characters of its output come back
```

act's description:

```
End your turn: send up to {max_actions} actions, which run after every participant has ended their turn, and your private memory. Example: {"actions": [{"action": "<action name>", "<argument name>": <value>}, {"action": "<action name>"}], "memory": "<your memory>"}
```

act's `actions` description:

```
the actions, in order: a list of up to {max_actions} (any beyond the first {max_actions} are dropped), each an object {"action": "<action name>", "<argument name>": <value>}. The list may be empty, [], when you take no action this turn.
```

act's `memory` description:

```
your private memory, up to {memory_chars} characters (longer is cut), shown in your view at your next turn; it replaces your previous memory, and an empty memory leaves it unchanged
```

| placeholder | value | format |
|---|---|---|
| `{code_chars}` | `compute.code_chars` | `INT` (`20000`); *(singular rule)*: `characters` after it |
| `{cpu_s}` | `compute.cpu_s` | `INT` (`5`); *(singular rule)*: `seconds` after it |
| `{wall_s}` | `compute.wall_s` | `INT` (`10`); *(singular rule)*: `seconds` after it |
| `{output_chars}` | `compute.output_chars` | `INT` (`4000`); *(singular rule)*: `characters` after it |
| `{max_actions}` | `turns.max_actions` | `INT` (`6`); *(singular rule)* in act's description only: `actions` after it. In act's `actions` description it stays a bare number (`a list of up to 1 (any beyond the first 1 are dropped)`) |
| `{memory_chars}` | `turns.memory_chars` | `INT` (`2000`, no separator); *(singular rule)*: `characters` after it |

So at `compute.cpu_s` = 1 the `code` description reads `... stopped after 1 second of computing ...`, and at
`turns.max_actions` = 1 act's description reads `send up to 1 action, which run ...`.

Everything else in the examples (`<action name>`, `<argument name>`, `<value>`, `<your memory>`) is literal text.

## 4. The decide chain: calls, offered functions and tool_choice

### 4.1 tool_choice before each call

The rules of this section and of sections 4.2, 5.2 and 5.4 are stated with three counts of the turn's decide chain
(any bookkeeping that makes the same choices will do, for example one derived from the conversation so far):
`programs` (programs run so far, section 5; a refused program counts as run), `calls` (model calls made so far),
and `free` (1 once the free retry of section 5.4 has been granted, else 0). Before each call of the chain:

- if `programs - free >= 1` or `calls - free >= 2`: tool_choice forces act:
  `{"type": "function", "function": {"name": "act"}}`;
- otherwise tool_choice is `"required"` (the model must call a function: compute or act).

The offered functions stay `[compute, act]` on every call of the chain, also when act is forced. A model spec's
provider-specific request fields may override tool_choice (models.md, "The body"); the market's own choice is as
above.

### 4.2 When a reply continues the chain

After each call (and its charge, section 13), with `calls` now counting this call:
- a reply with at least one function call named exactly `act` ends the chain (section 7);
- a reply with no function call at all ends the chain (a format error, section 7.1);
- a reply with function calls, none named `act`:
  - if the participant is out of business after this call's charge (section 13.2): none of its programs run, the
    chain ends, and the reply is read as an act call with an empty action list and no memory (section 13.2);
  - else, if `calls - free <= 2`, it is a **compute step** (section 5) and the model is called again;
  - else the chain ends and the reply is a format error (`your reply did not call act ...`, section 7.1). (This
    happens only when act was forced and the reply did not call it: a provider ignored the forced act, or a model
    spec's request fields override tool_choice, 4.1.)

Typical sequences at the default configuration:

| replies | requests |
|---|---|
| act | 1: `"required"` |
| compute (ran), act | 1: `"required"`; 2: act forced |
| compute (failed: free retry), compute (any status), act | 1: `"required"`; 2: `"required"`; 3: act forced |
| compute (failed) and compute in one reply (the second runs as the free retry), act | 1: `"required"`; 2: act forced |
| malformed compute (no program ran), malformed again, act | 1: `"required"`; 2: `"required"`; 3: act forced |
| compute (ran), compute again despite the forced act, compute again | 1: `"required"`; 2: act forced, answered `not run: no programs left ...`; 3: act forced; the third reply ends the chain as a format error |

### 4.3 The correction call

A correction call (section 11) offers `[act]` only, with tool_choice act forced, and is never followed by another
call. Compute is never run in it.

## 5. Compute steps

### 5.1 What a compute step adds to the conversation

A compute step appends, in this order:
1. the assistant message for the reply (section 2: stripped text, the calls as returned, its reasoning if any);
2. one tool message per function call of the reply, in the calls' order, each answering its call's id (section 2),
   with the content given in 5.2.

Then the model is called again (section 4).

### 5.2 The answer to each call

Let `left = 1 - (programs - free)` at the start of the step (programs counted before this step). Each call, in order:

- A call not named exactly `compute`, or whose arguments have no `code` that is a string (missing, or not a
  string): no program runs; the answer is

  ```
  not run: compute takes one argument, "code", a string; send your actions with act
  ```

  The call's arguments are read here strictly, without the closing brackets of section 7.2: an object the provider
  sent already decoded is used as it is, a string is read strictly (an empty string as no arguments), and anything
  else (a string that does not read as an object, or a decoded value that is not an object) counts as no arguments.
- A compute call with a `code` string when `left <= 0`:

  ```
  not run: no programs left this turn, and compute cannot be called again this turn; send your actions with act
  ```

- Otherwise the program runs (section 6) and `left` goes down by 1. Unless the program is granted the free retry
  (section 5.4, which gives its answer and sets `left` back up by 1), the answer is the program's output text
  (section 6.4) followed by:

  ```
  {output}

  (That was your last program this turn: compute cannot be called again this turn. Send your actions with act.)
  ```

  (`left` is always 0 at this point, so this tail always applies.)

So at most one program runs per step, or two when the first one failed and was granted the free retry (5.4): the
next compute call in the same reply then runs as the retry, and any later one gets `not run: no programs left ...`.
A `code` that is `""` or only whitespace (for example `"   "`) runs and is refused (section 6.2): it counts as a
program, and as a failed one (5.4).

### 5.3 Example

The second request of a turn whose first reply was `compute` with `print(sum(range(10)))` (as every turn of the
offline mock model begins: running.md, "Its replies in a turn") has four messages:
system, user (the view), `{"role": "assistant", "content": "", "tool_calls": [<the compute call>]}`, and
`{"role": "tool", "tool_call_id": <its id>, "content": "45\n\n(That was your last program this turn: compute cannot be called again this turn. Send your actions with act.)"}`.

### 5.4 The free retry after a failed program

At a compute step, when a program runs, its status (section 6.3) is not `ok`, and the free retry has not been granted
yet this turn (`free = 0`), whatever else the reply holds: the free retry is granted (`free = 1`), the failed program
does not count, and that program's own tool message is its output text (section 6.4) followed, in place of the
last-program tail of 5.2, by `\n\n` and

```
(The program failed, so it does not count: 1 more program allowed this turn.)
```

Every status other than `ok` counts as failed, including `refused`. There is one free retry per turn: a second failed
program gets the ordinary last-program tail. If the same reply holds another compute call after the failed one, that
call runs as the retry (5.2); otherwise the next call of the chain may run it. The chain's bound is unchanged (4.1):
act is forced once the programs are used up, or after two replies that ran no program.

Example (a program `1/0` alone in its reply):

```
Traceback (most recent call last):
  File "<program>", line 1, in <module>
ZeroDivisionError: division by zero
(exit code 1)

(The program failed, so it does not count: 1 more program allowed this turn.)
```

The next call is `"required"` with `[compute, act]`; a second program gets the last-program tail; the call after it
forces act.

## 6. Running a program

### 6.1 Isolation (requirements)

A program is the `code` string as sent, run by a CPython 3 interpreter with its standard library (the reference
implementation used CPython 3.15). The checks of 6.2 (blankness, length) apply to the string as received; the
interpreter is given its UTF-8 encoding, in which a character that cannot be encoded (an unpaired surrogate, from a
`\uD800`-style escape in the call's JSON, section 7.2) becomes `?` (so `print(ascii('\ud800'))`, with the escape in
the program's JSON string, prints `'?'`). It must
reach nothing but the computation itself:
- the standard library only: no third-party packages are importable;
- no access to the market, the run's files, logs, configuration, the repository or the home directory. It may read
  what the interpreter itself needs (its installation, the standard library's files included, and the system
  libraries it loads) and the system's random source (so `os.urandom` and `random` work), read and write
  `/dev/null`, and list its working directory (empty); it cannot write or create any other file. (The reference
  implementation also caps open file descriptors at 32, the standard streams included: an example, not a
  requirement);
- no network (a connection attempt fails), no subprocesses and no forking (an attempt fails), no environment variables
  (an empty environment). So a program is one process; it may start threads, which share its limits (6.3);
- standard input is closed (reading it fails); the program runs as the main module (`__name__ == "__main__"`), with
  an empty working directory;
- the builtins `exit`, `quit`, `help`, `copyright`, `credits` and `license` are not defined (as when CPython skips
  its site initialisation): using one raises `NameError` (status `error`, exit code 1, so `exit(3)` gives
  `(exit code 1)` and `quit()` is an `error`, not `ok`); `sys.exit` works as usual;
- the program's global namespace starts with only `__name__` = `"__main__"` (and the builtins): no `__file__`,
  `__doc__` and the like (`__file__` raises `NameError`; a name missing there is looked up in the builtins as usual,
  so `__doc__` and `__spec__` give the `builtins` module's own). It is not the namespace of the module `__main__`
  (importing `__main__` does not see the program's globals). What `sys.argv` and the module `__main__` hold is
  unspecified;
- standard output and standard error go to one stream, interleaved in the order written, unbuffered (so what a
  program printed before it was stopped comes back). Both are UTF-8 encoded. On standard output a lone surrogate
  U+DC80 to U+DCFF is written as the one byte its low 8 bits give (Python's `surrogateescape`; the bytes are then
  decoded as in 6.4, so `print(chr(0xdc80))` comes back as U+FFFD) and any other lone surrogate raises `UnicodeEncodeError` (status `error`); on standard error a
  character that cannot be encoded is written as its `\uXXXX` escape (Python's `backslashreplace`). (CPython's UTF-8
  mode gives exactly this.)

Any CPython 3 version is acceptable; CPython 3.15 matches the reference exactly. A program whose behaviour differs
between CPython versions (new syntax, a changed library function) may then print something else, and that is
accepted. Beyond the version, these vary from run to run or host to host and need not be reproduced: the wording of
tracebacks and error messages (6.4), timings and the date, what a program can learn about the host (the working
directory's path, process ids, the platform, the number of CPUs; in the reference some of these queries give `None`
or fail, for example `os.cpu_count()` gives `None`), the `random` module's seeding from the operating system, and the
iteration order of sets of strings (string hashing is randomised per run, as in a plain interpreter).

Example of a mechanism (not a requirement): the reference implementation, on macOS, starts a fresh interpreter for
each program in isolated mode without site initialisation (CPython's `-I -S -B -u`, an empty environment) under
`sandbox-exec` with a deny-by-default profile that allows reading only the interpreter's installation, the system
libraries, the random devices and its empty working directory, writing only `/dev/null`, executing only the interpreter, and no network or fork;
it sets the CPU-time limit as a hard resource limit inside the process and watches wall time, resident memory and
output from outside. On Linux a container or namespace sandbox with a seccomp filter can give the same isolation.

Programs from different participants may run at the same time; each is independent. A program is never run
without this isolation. A run on a host that cannot provide it is refused before any episode starts (running.md,
"2. A run", step 1; how the host is checked is the implementation's choice). If a
program nevertheless cannot be run isolated when it comes up, its episode stops with status `error` (running.md,
"Errors"); the other episodes go on.

A program costs the participant nothing (only model calls are charged).

### 6.2 Programs refused without running

Checked in this order:
1. the code is empty or only whitespace: status `refused`, reason `no program given`;
2. the code is longer than `compute.code_chars` characters: status `refused`, reason
   `program too long ({n} characters; the limit is {code_chars})`, with `{n}` the code's length (`INT`) and
   `{code_chars}` = `compute.code_chars` (`INT`, no separator).

### 6.3 Limits and statuses

| status | when | note appended to the output (section 6.4) |
|---|---|---|
| `ok` | the program ended with exit code 0 | none |
| `error` | the program ended with a non-zero exit code (an uncaught exception, `sys.exit(n)` with an integer n whose exit code below is not 0, `sys.exit("text")`); the exit code is n mod 256 for n from -2^63 to 2^63 - 1 and 255 outside that range (as CPython gives), so `sys.exit(-1)` shows `(exit code 255)`, `sys.exit(256)` is `ok` and `sys.exit(2**64)` shows `(exit code 255)` | `(exit code {exit_code})` |
| `timeout` | more than `compute.wall_s` seconds of wall time passed since the program was started | `(stopped: the program ran past {wall_s} seconds)` |
| `cpu_limit` | the program used `compute.cpu_s` seconds of CPU time: it ended by the signal SIGXCPU after its CPU time (as the operating system reports it when the program ends) reached `compute.cpu_s` seconds | `(stopped: the program used more than {cpu_s} seconds of CPU time)` |
| `memory_limit` | the program's memory (below) exceeded `compute.memory_mb` x 2^20 bytes | `(stopped: the program used more than {memory_mb} MB of memory)` |
| `output_limit` | the program's output (standard output and error together) exceeded `compute.output_kb` x 1024 bytes | `(stopped: the program wrote more than {output_kb} KB)` |
| `memory_unknown` | the program's memory could not be measured while it was running (below) | `(stopped: the program's memory use could not be measured)` |
| `killed` | the program ended by any other signal, or by a SIGXCPU before its CPU time reached `compute.cpu_s` (one it sent itself) | `(stopped: the program was killed)` |
| `refused` | section 6.2 | (section 6.4) |

All placeholders are `INT`; `{wall_s}` and `{cpu_s}` follow the singular rule (*(singular rule)*: `seconds` after
each). The CPU time is the program's process's total, all its threads together (user and system time); its memory is
that process's (below). A program that breaks a limit is stopped at once; what it printed before that is kept. When a
program ends by itself, `cpu_limit` (SIGXCPU after reaching the limit) is checked first, then
`output_limit` (it wrote more than the limit before ending), then `killed`, then `ok`/`error` by exit code. When
several of these are found at once while it runs, the first in this order is the status: wall time (`timeout`), the
memory limit (`memory_limit`), memory that could not be measured (`memory_unknown`), the output limit
(`output_limit`).

**The program's memory** is the physical memory its process holds (resident memory), not the address space it
has reserved: a program that reserves more than the limit without using it is not stopped. How it is watched is the
implementation's choice (polling, a kernel limit whose kill is reported as `memory_limit`, ...); an excess too brief
for the chosen means to notice may pass, and that is accepted. (The reference implementation polls the resident
memory every 0.02 seconds: an example.) The observable rules are: a program that builds up more than the limit
(for example `b'x' * (600 * 2**20)` at the default 512 MB) is stopped with `memory_limit`, not given a `MemoryError`,
and what it printed before is kept; a single allocation the host cannot provide at all (for example
`bytearray(10**15)`) still raises `MemoryError` in the program (status `error`). A program must not go on running
while its memory cannot be watched: an implementation whose means of watching can fail stops it then with
`memory_unknown` (the reference implementation does so after 3 polls in a row fail: an example). An implementation
that can always watch the memory never gives `memory_unknown`.

**Exit codes.** Only status `error` shows an exit code to the participant: the program's own exit code, from 1 to
255. The log records an exit code for every program (running.md, "6.3 The log: required content"): none for
`refused`; otherwise its exit code if it exited (0 for `ok`), else a code of the implementation's choice for how it
was stopped (the reference records the stopping signal's number negated: -9 at a limit, -24 for `cpu_limit`).
Nothing an agent reads depends on it.

An uncaught exception prints, after flushing what the program printed, Python's standard traceback for it, in which
the program's own frames appear as `File "<program>", line N, in <module>` (or the function's name) and no frame of
the runner that started it appears; the exit code is 1. `SystemExit` behaves as in Python.

### 6.4 The output text

- Keep at most the first `compute.output_kb` x 1024 bytes of the output stream; decode them as UTF-8, replacing
  each maximal ill-formed subsequence with one U+FFFD (Unicode's recommended practice, as Python's
  `errors="replace"` does: the bytes `41 E2 82 42 FF 43` give `A`, U+FFFD, `B`, U+FFFD, `C`); if the result is longer than `compute.output_chars` characters, keep its first
  `compute.output_chars` characters and mark it cut.
- status `refused`: the text is `(program not run: {reason})`.
- any other status: start from the kept output with trailing line feeds (`\n`) removed, or `(no output)` if that is
  empty; if the output was cut, append `\n(output cut at {output_chars} characters)` (`INT`, no separator;
  *(singular rule)*: `characters` after it); then
  append `\n` + the status's note from 6.3, if it has one.
- Module hint: if the text so far contains `ModuleNotFoundError` anywhere, append `\n` +

  ```
  (Only the Python standard library is installed here: math, statistics, random, itertools, fractions and the rest are available; numpy, scipy, pandas and other packages are not.)
  ```

This text is the program's output in the tool message (before the tail of 5.2 or the retry note of 5.4) and in the
log. Examples (rendered, before the tail). This block is notation, not exact text: `->` separates the program from
its output, `\n` in an output is a line feed, `...` elides, and "4000 times a" describes the text:

```
print(2+2)                  -> 4
x=1                         -> (no output)
import sys; sys.exit(3)     -> (no output)\n(exit code 3)
import sys; sys.exit('bye') -> bye\n(exit code 1)
time.sleep(30)              -> (no output)\n(stopped: the program ran past 10 seconds)
while True: pass            -> (no output)\n(stopped: the program used more than 5 seconds of CPU time)
print('a'*5000)             -> 4000 times a, then \n(output cut at 4000 characters)
endless printing            -> 4000 characters, then \n(output cut at 4000 characters)\n(stopped: the program wrote more than 64 KB)
"   \n"                     -> (program not run: no program given)
"#" * 20001                 -> (program not run: program too long (20001 characters; the limit is 20000))
print('a\n\n')              -> a
import numpy                -> Traceback (most recent call last):\n  File "<program>", line 1, in <module>\nModuleNotFoundError: No module named 'numpy' ...\n(exit code 1)\n(Only the Python standard library is installed here: ...)
```

Tracebacks and error messages are the host interpreter's: their exact wording (for example the text after
`No module named 'numpy'`, or the message of a refused file or network access) depends on the Python version and the
isolation mechanism and need not be byte-exact. Everything this file writes around them must be.

## 7. Reading the reply that ends the chain

The same reading applies to the decide chain's last reply and to the correction's reply. Let **cut** mean the reply
ended because it hit its token limit: its finish reason is `length` (a provider adapter that reports this stop otherwise must return `length`; models.md, "2.2 What a call returns").

### 7.1 Format errors

The reply's act calls are its function calls named exactly `act`, in order.
- No act call, and the reply has other function calls:

  ```
  your reply did not call act: your turn ends with act, with your actions and your memory
  ```

- No function call at all (text only, or empty):

  ```
  your reply was not a function call: every reply must call compute or act
  ```

- An act call whose arguments cannot be read as an object, or whose `actions` is present, not null and not a list
  (section 7.3); one such call makes the whole reply unreadable:

  ```
  your act call's "actions" could not be read: it must be a list of action objects, each naming its action with "action" and giving its arguments beside it
  ```

If the reply was cut, the error text gets this suffix (beginning with a space):

```
 - your reply hit the length limit before its act call was complete; reason more briefly
```

What a format error does to the turn: sections 11 and 12.

### 7.2 Reading JSON

**Strict reading**: standard JSON, one value, nothing but JSON whitespace (spaces, tabs, line feeds, carriage
returns) around it; control characters inside strings
are refused; a repeated key keeps its last value, in the position of its first occurrence (`{"a": 1, "b": 2, "a": 3}`
reads as `a` = 3, then `b` = 2); `NaN`, `Infinity` and `-Infinity` are accepted as numbers. A `\uXXXX` escape of an
unpaired surrogate (U+D800 to U+DFFF; a high surrogate escape followed by a low one is one character as usual) is
accepted by both readings and gives that one code point. It stays in the text wherever the text goes (a memory,
an argument quoted back, a message), except in a program's code (section 6.1); models.md, "The request", says how
a request carries it.

An integer literal of more than 4,300 digits in a reply, and JSON nested more than 400 levels deep, are outside
this spec (README.md, "Number formats and rounding"); shallower nesting must be read. An implementation may treat
either as JSON that cannot be decoded, in both readings, so the call gets its usual answer (preferred), or behave as
the reference implementation: for the literal, both its readings do so (an act call is a format error, 7.1; a
compute call has no arguments, 5.2); nesting it reads, but it ends the episode with status `error` when an action or
an action's argument is nested about 490 levels deep.

**Reading with closing brackets** (the one repair, for an act call's arguments only, 7.3): the text with its leading
and trailing whitespace removed, followed by `}`, `]}`, `}]}` and `]` in turn, each read strictly; the first that
reads as an object is the reading, else there is none. (It reads the replies of a small model that padded its act call
with whitespace and left out its last closing bracket.) No other repair is made: triple-quoted strings, trailing
commas, Python's `None`/`True`/`False`, several values or text around the object, backticks and raw control characters
inside strings all leave the text unreadable.

### 7.3 An act call's arguments

1. **Arguments object.** If the call's `arguments` is already an object (some servers send it decoded), use it. If it
   is missing, null or `""`, it is `{}`. Any other string is read strictly (7.2), then, if that fails and the reply
   was not cut, with closing brackets (7.2). If that gives no object (the text does not decode, or decodes to
   something other than an object), or `arguments` was already decoded as anything other than an object (a list, a
   number, `true`, `false`), the call's actions cannot be read (format error, 7.1).
2. **Memory.** `memory`, if it is a string with non-whitespace content (kept exactly as sent, not trimmed); else the
   call has no memory. No other key gives a memory: the call's other keys (`summary` among them) are ignored.
3. **Actions.** The value of `actions`, absent or null, means no actions (an empty list). Any other value must be a
   list; otherwise (a string, even one holding JSON, an object, a number or a boolean) the call's actions cannot be
   read (format error, 7.1).

So an act call that gives a memory but no `actions` (or `"actions": null`) carries no actions, and its memory is read
as usual; arguments missing, null or `""` give the same empty act, with no memory. A broken or cut act call never
passes as an empty one.

When the reply was cut, an act call's arguments string is read strictly only (no closing brackets): a cut call must
not run part of its actions.

Several act calls in one reply: their action lists are joined in order. **The reply's act memory is the memory of
its last act call** (an earlier act call's memory is ignored).

### 7.4 From items to actions

Each item of the joined list, in order, becomes one action with a **name** and its **arguments**, is dropped, or is
replaced by other items. No text ever tells the participant how its items were read.

- I1. An item that is not an object (a string such as `"bid"` or `"end_turn"`, a number, a list, `null`, `true`,
  `false`) is an action with no name and no arguments.
- I2. An object's name is its `action`, if that is a string with non-whitespace content (taken as it is, untrimmed;
  the key is removed); else its `name`, if that is such a string (the key is removed). When one of the two keys gives
  the name, the other stays an argument whatever its value: `{"action": "bid", "name": null}` runs bid with the
  argument `name` = null, and `{"action": "  ", "name": "bid"}` runs bid with the argument `action` = `"  "`. When
  neither gives a name, an `action` or `name` that is null, `""` or only whitespace is no name and is removed (so I4
  can drop the object); any other value (a number, a boolean, a list, an object) names nothing and stays an argument
  (`{"action": 5}` has no name and one argument).
- I3. Nested arguments: for each of `args`, `arguments`, `parameters`, `params`, in this order, if the object holds
  that key with an object value, the key is removed and each of that object's keys is added unless the item already
  has it. Each key is checked against the item as it then stands, after the earlier keys' merges: an object an
  earlier key merged in under a later key's name is merged in too (`{"args": {"params": {"x": 1}}}` gives `x` = 1),
  while one merged in under an earlier key's name stays an argument (`{"params": {"args": {"x": 1}}}` gives the
  argument `args` = `{"x": 1}`).
- I4. An object with no name and no key left (for example `{}`, `{"action": "  "}`, `{"action": null}`,
  `{"args": {}}`) is dropped.
- I5. An object named exactly `act` whose `actions` is a list: its items are read by I1 to I5 (recursively) and take
  its place, in order; an empty list drops it. Its other keys (a `memory` or `summary` among them) are ignored, and
  an inner `{"memory": "..."}` runs and fails as a nameless action: a memory never comes from inside the action list.
  Deliberate quirk: reproduce exactly. An object named `act` whose `actions` is not a list runs as an action named
  `act` (an unknown action).

Every other key of an object is one of its action's arguments, whatever its name: `tool`, `memory`, `summary`,
`parameter`, `_raw`, `end_turn`, `type`, or a key that is an action's name. An action ignores the arguments it does
not read (7.6). Nothing is dropped except by I4 and I5.

Examples:
- run as bid: `{"action": "bid", "post_id": "post-1", "price": 10}`, `{"name": "bid", ...}`,
  `{"action": "bid", "parameters": {"post_id": "post-1", "price": 10}}`; `{"action": "bid", ..., "memory": "..."}`
  also runs bid, its `memory` an ignored argument (never the turn's memory), as `tool` is in
  `{"action": "bid", "tool": "pay", ...}`;
- run under the name given, and fail as unknown actions (7.6): `{"action": "end_turn"}`,
  `{"action": "update_memory", "text": "..."}`, `{"action": "update_summary", "text": "..."}`;
- run with no name, failing with the no-name refusal (7.6): `{"tool": "bid", ...}`, `{"actions": "bid", ...}`,
  `{"actions": ["pay", {...}]}`, `{"bid": {"post_id": "post-1"}}`, `{"dispute": "contract-3"}`,
  `{"act": {"actions": [...]}}`, `{"actions": [...], "memory": "..."}`, `{"memory": "..."}`, `{"summary": "..."}`,
  `{"summary_updated": true}`, `{"end_turn": true}`, `{"finish": true}`, `{"type": "end_turn"}`, `{"tool": null}`,
  `{"_raw": "pay", "amount": 1}`, `"bid"`, `5`;
- `{"action": "accept_bid", "parameter": "bid-99"}` runs accept_bid with no `bid_id` (its refusal for the missing
  argument); `{"action": "run_myself", "contract_id": "contract-3", "refine": "None"}` runs run_myself with refine
  `None`, which its own refine check refuses (actions-manager.md);
- dropped: `{}`, `{"action": "  "}`, `{"action": "act", "actions": []}`.

The remaining items, in order, are the reply's actions; the cap (section 8) applies next.

### 7.5 An action named only by a key

An item that gives an action's name only as a key has no name (7.4). So with `message_channels` `off`,
`{"send_message": {...}}` fails with the no-name refusal, while `{"action": "send_message", ...}` gets the market's
not-available refusal (messaging.md, "2.2 Before send_message's own checks"): two different errors. Deliberate quirk:
reproduce exactly.

### 7.6 How the actions then run

Each kept action runs under its name exactly as sent (nothing is trimmed, lowercased or mapped: `Bid`, ` bid ` and
the old names `approve`, `reject`, `pay_arbiter`, `delegate` are unknown actions). An action with no name runs and
fails with:

```
the action names no action: give its name with "action", as {"action": "<action name>", "<argument name>": <value>}
```

This is the same refusal as actions-buyer.md, "Matching the name, and the checks before the action's own" (step
2), which also gives the market's other refusals (unknown name, not available in this market, the other role's
action, out of business); every action's own texts are in actions-buyer.md and actions-manager.md. Arguments an
action does not read are ignored without notice. One name that is not in the participants' action lists is handled
here:

- `compute` sent as an action never runs a program; it fails with

  ```
  the program did not run: compute is a function you call before act, not an action (at most 1 call a turn)
  ```

  It counts toward the cap and as a failed action (so it brings a correction).

`update_memory`, `update_summary` and `end_turn` are not actions: the memory is act's argument only (section 10), and
act ends the turn. Sent as names they are unknown actions (actions-buyer.md, step 4), failed actions that bring a
correction like any other, with `{list}` the participant's action list (actions-buyer.md, "1.1 The actions of each
role"):

```
- update_memory: ERROR: unknown action 'update_memory'. Your actions: {list}.
- update_summary: ERROR: unknown action 'update_summary'. Your actions: {list}.
- end_turn: ERROR: unknown action 'end_turn'. Your actions: {list}.
```

## 8. The action cap and the notes that open the results

The first `limit` actions (after section 7.4) are kept, in order; `limit` is `turns.max_actions` for the decide
chain's reply and the correction's limit (section 11.2) for a correction. Every action counts, including ones that
will fail; items dropped in section 7.4 do not count. The rest are dropped: not run, not counted as failed, and not
listed.

The reply's result lines open with these notes, in this order:
1. if the reply has both an act call and a compute call (function calls named exactly `act` and exactly `compute`;
   a call named ` compute` or `Compute` does not count) (a compute call beside act never runs):

   ```
   (note: your compute call did not run: it was sent in the same reply as act)
   ```

   (not for the empty act of an out-of-business participant, section 13.2);
2. if actions were dropped by the cap:

   ```
   (note: only your first {limit} action{s} {were} run)
   ```

   `{limit}` `INT`; `{s}` is `s` unless `limit` is 1; `{were}` is `was` if `limit` is 1, else `were` (at 1:
   `(note: only your first 1 action was run)`).

## 9. Result lines

Each action that ran gives one line, in the order the participant's actions ran (rounds.md, "The order the phase's
actions run in", keeps a participant's own order):

```
- {shown}: OK: {message}
- {shown}: ERROR: {message}
```

`{message}` is the action's result or refusal text (actions-buyer.md, actions-manager.md, this file's 7.6).
`{shown}` is the action's name with surrounding whitespace removed, its case kept; an action with no name (a missing
name, a name that is not a string, or a string that is empty or only whitespace) shows `?`. For example, " Bid " gives
`- Bid: ERROR: unknown action ' Bid '. Your actions: {list}.`; "approve" gives
`- approve: ERROR: unknown action 'approve'. Your actions: {list}.`
A failed line may get a clash note appended after one space before the correction (rounds.md, "What each failed
action is told").

A participant whose action puts it out of business (or who is out of business when its first action of the phase
comes up, after a token charge: 13.2) runs none of its later actions in that phase (rounds.md, "A participant that
goes out of business during a phase"); they get no line; the note below follows the participant's last line of that
phase:

```
(note: your remaining {n} action{s} did not run: you are out of business)
```

`{n}` `INT`, the number of the participant's actions of that phase that were stopped; `{s}` is `s` unless n is 1.

## 10. The memory

### 10.1 Which memory a reply carries

A reply's memory is its act memory (section 7.3: the last act call's), else none. Nothing is taken from the reply's
text, its reasoning or its action items. An empty or whitespace-only memory counts as none.
An unreadable reply (a format error, section 7.1) carries no memory, whatever any of its act calls holds (even a
readable last act call with a memory, beside an earlier one whose actions cannot be read): after an unreadable
decide reply the turn's memory can come only from the correction (10.3).

### 10.2 Writing it

The memory is written directly, never as an action: writing it gives no result line of its own (10.4 gives its
lines), and it is none of the turn's actions. Writing a memory text `t` stores `t` cut to its first `turns.memory_chars` characters (no trimming, line breaks
kept), with the day, the round and the turn in which it was written, and whether it was written from the correction
(view.md, "Your self-written memory", shows the memory and uses these for its section title). The text is not
otherwise changed.

### 10.3 When, and which one

The memory is written at the turn's end (after the correction phase and after the round's money from others has
landed; rounds.md, "Each turn ends"), whether or not the participant is then in business. The memory written is:
- the correction's memory, if the correction was read and had one;
- else the decide reply's memory.

A correction that was unreadable has its memory discarded; a correction that could not be sent leaves the decide
reply's memory in place. When the turn is lost (section 12; rounds.md, "Lost turns"), nothing is written.

### 10.4 The memory's lines in the results

Appended at the very end of the turn's results (after any correction block):
- written and not cut: no line;
- written and cut:

  ```
  (note: your memory was cut to its first {memory_chars} of {n} characters)
  ```

  `{memory_chars}` = `turns.memory_chars`, `{n}` the memory's length (both `INT`; the unit follows `{n}`, which is
  always at least 2 here, so the singular rule never changes this note);
- no memory to write:

  ```
  (note: your reply had no memory; your memory is unchanged)
  ```

## 11. The correction

### 11.1 Who gets one

After the first action phase, a participant gets exactly one correction call if its turn was not lost, it is in
business, and either its reply could not be read (a format error) or at least one of its actions failed (including
one that lost a clash; rounds.md, "The clash rules"). A correction never gets a correction.

### 11.2 Its limit and its note

- The decide reply could not be read: limit `k` = `turns.max_actions`; the note is

  ```
  Your reply could not be read, so none of its actions ran (below). You may call act again now, with up to {k} action{s} and your memory. This is your only correction this turn, and compute cannot be called in it.
  ```

  `{k}` `INT`; `{s}` is `s` unless `k` is 1.
- Otherwise: limit = `n`, the number of the reply's actions that failed; the note is

  ```
  {n} of your actions failed (the results of your reply are below; the actions that succeeded have run and stand). You may send up to {n} action{s} now to replace {they}, in the same format as before, with your memory, which then replaces the one you just sent. This is your only correction this turn, and compute cannot be called in it.
  ```

  `{n}` `INT`; `{s}` is `""` if `n` is 1 else `s`; `{they}` is `it` if `n` is 1 else `them`.

The correction may send any actions, not only replacements of the failed ones.

### 11.3 Its body

The body is the concatenation of:
1. `{note}` + `\n\nResults of your reply:\n` + `{results}`;
2. if anything reached the participant during this round so far (notifications added since its view was built):
   `\n\n` + `What reached you from this round so far (your next view shows it too):` + `\n` + `{news}`;
3. if the round's rules give it facts (the open bids after a lost acceptance, the balance and capacity after a lost
   run or test): `\n\n` + the fact lines joined by `\n`.

- `{results}`: the decide reply's result lines (sections 8 and 9, with any clash notes appended), joined by line
  breaks and spaced as the view spaces its results text (view.md, "Spacing inside the results text", each result line
  one entry even when it holds line breaks; the first gets no empty line before it). For an unreadable reply it is
  the single line
  `- FORMAT ERROR: {err}: none of your actions ran` (`{err}` the format error of 7.1 with its suffix, if any).
- `{news}` and the fact lines: rounds.md, "The news and the facts" (each news item is a notification line
  `[day {d}, round {r}] {text}`, one per line). The clash notes: rounds.md, "What each failed action is told".

Real example (one failed bid out of three, with news):

```
1 of your actions failed (the results of your reply are below; the actions that succeeded have run and stand). You may send up to 1 action now to replace it, in the same format as before, with your memory, which then replaces the one you just sent. This is your only correction this turn, and compute cannot be called in it.

Results of your reply:
- bid: ERROR: post-8 closed earlier this round, when its buyer accepted a bid (this round B-d75 accepted another bid on post-8 before your bid ran)

- bid: OK: placed bid-21 on post-7 at 24 with threshold 6 (escrow)

- bid: OK: placed bid-22 on post-6 at 24 with threshold 6 (escrow)

What reached you from this round so far (your next view shows it too):
[day 6, round 3] B-c53 approved R-815 on contract-1: you received 25.00 of the price 25; contract-1 is closed.
```

### 11.4 How the body joins the conversation

The correction call continues the decide chain's conversation (system, view, any compute steps), adding the reply
that ended the chain and the body, in one of two ways:

- **Tool path**, when the reply was readable, was not cut, and every one of its function calls has arguments that are
  a string whose strict reading is an object (an empty string does not qualify here, unlike in 7.3 step 1: a reply
  with any call whose arguments are `""` takes the text path): the assistant message as in section 2 (stripped text, the calls as
  returned, reasoning if any), then one tool message per call, in order, answering its id (section 2): the body for the reply's
  **last** act call; `not run: act ended the turn` for a call named exactly `compute`; and for any other call
  (including an earlier act call, or a call named ` compute` or `Compute`)

  ```
  (the results of this reply are in the answer to its last act call)
  ```

- **Text path**, otherwise (an unreadable reply, a cut reply, or arguments that are not a clean JSON object string):
  an assistant message with no `tool_calls`, whose `content` is the reply's stripped text; if that is empty, the
  arguments of the reply's function calls, each in full, joined by line breaks, leaving out every call whose
  arguments are missing, null, `false`, `0`, `""`, `[]`, `{}` or only whitespace (arguments a provider returned
  already decoded appear as `TEXT` of the decoded value); if no call is left, `(an empty reply)`; plus `reasoning` if
  the reply had any. Then a **user** message whose content is the body.

### 11.5 The request

Tools `[act]`, tool_choice `{"type": "function", "function": {"name": "act"}}`. The call is charged like any other
(section 13). Compute is not offered; a compute call in the correction's reply does not run (and gets the
compute-beside-act note if the reply also calls act).

### 11.6 Reading the correction

- **No reply** (the call failed at the provider): if the decide reply was unreadable, the turn is lost to the
  provider (rounds.md, "Lost to a failed model call"); otherwise the decide reply's results stand, its memory is
  written as usual, and this line is appended to its results:

  ```
  (note: your correction could not be sent; the results above stand)
  ```

- **Unreadable** (no act call: no function call at all, or only other functions; or an act call that cannot be
  read): the error is formed as in 7.1, with its suffix (`your reply was not a function call: ...`, `your reply did
  not call act: ...` or the act call's error). If the decide reply was also unreadable, the turn is lost (section 12). Otherwise
  this line is appended to the decide reply's results, and the correction's memory is discarded:

  ```
  Correction: FORMAT ERROR: {err2}: none of its actions ran
  ```

- **Readable**: read as in section 7 and capped at the correction's limit (section 8; its own notes open its own
  lines); its actions run in the correction phase (rounds.md, "Reading the corrections and the correction phase").
  Its memory, if any, is the turn's memory (section 10.3).

## 12. The turn's results text

At the turn's end the turn's results text is stored; view.md shows it in the next view under its section title
(view.md, "Results of your actions in your previous turn"), spaced as view.md, "Spacing inside the results text",
says: the heading and each line listed below are its entries, each one entry even when it holds line breaks.

- Normally: `Results of your actions:` + line break + the lines below joined by line breaks, or
  `Results of your actions:` + line break + `(no actions run)` when there are no lines:
  1. the decide reply's lines: its notes and action lines (sections 8 and 9, clash notes and the out-of-business
     note included), or `- FORMAT ERROR: {err}: none of your actions ran` if it was unreadable;
  2. then any `(note: your correction could not be sent; the results above stand)` or
     `Correction: FORMAT ERROR: {err2}: none of its actions ran` line (section 11.6);
  3. then, if a correction was read (readable): the line `Correction:`, then its notes and action lines, or
     `(no actions run)` if the correction produced no line at all (no opening note of section 8 and no action
     line); a correction whose only line is a note (for example the compute-beside-act note with an empty action
     list) shows that note and no `(no actions run)`;
  4. then the memory line, if any (section 10.4).
- **Lost to two format errors** (the decide reply and the correction both unreadable): nothing runs, no memory is
  written, the turn is lost (rounds.md, "Lost as a format error"), and the stored text is exactly (no heading):

  ```
  FORMAT ERROR: {err}; and your correction: {err2}. That turn was lost: none of its actions ran. End every turn by calling the function act with a list of your actions (an empty list does nothing) and your memory.
  ```

- **Unreadable reply of a participant already out of business** (put out of business by this turn's own charges; it
  gets no correction): the turn is lost and the stored text is

  ```
  FORMAT ERROR: {err}. That turn was lost: none of its actions ran. End every turn by calling the function act with a list of your actions (an empty list does nothing) and your memory.
  ```

- **Lost to the provider**: the previous results text stays (rounds.md, "Lost to a failed model call").

Example (from the reference implementation's runs; as stored, before the view's spacing):

```
Results of your actions:
- deliver: ERROR: no contract with id 'contract-1'. Your contracts: none. A bid becomes a contract only when the buyer accepts it.
Correction:
(no actions run)
```

## 13. Token charges

### 13.1 What is charged, and when

Every model call of the turn whose reply came back is charged to the participant: each call of the decide chain
(including compute steps and a reply that turns out unreadable) and the correction call. A call's charge is
`round(usd x credits_per_usd, 6)` (the multiplication before the rounding), where `usd` is the call's dollar cost
as the model layer reports it (models.md, "The dollar cost" and "Token charges in credits"). If the charge is 0 or
less, nothing happens. Otherwise it is taken from the participant's balance and added to its token costs (view.md,
"Your accounts so far (credits)"), and recorded in the ledger.

Timing: every taker's decide-chain charges are made after all chains of the round have finished, participant by
participant in the round's order (rounds.md, "The first calls: in parallel, taken up in the turn order"), each
participant's calls in the order made, and before any action of
the round runs. A correction call's charge is made after the first action phase and before the correction phase. So
a turn's own model calls lower the balance its actions see (the system prompt says so); the view showed the balance
before them.

While a chain runs, the decision of section 4.2 (is the participant out of business after this call's charge?) is
taken on the participant's private projection of its balance, and the real charges, with their ledger entries,
token costs and notification, are made at the take-up and reach the same result (rounds.md, "4. The first calls: in
parallel, taken up in the turn order", items 3 and 4). Charges of calls that came back stand when a later call of
the same turn fails and the turn is lost.
Programs cost nothing; the conversation summarizer's calls are never charged to a participant (messaging.md,
"Cost").

### 13.2 Out of business by a token charge

A buyer is never put out of business by a token charge: its balance may go below 0 with no other effect.

A manager whose balance is at or below 0 after a charge, and who was in business, is out of business at once
(episode.md, "Out of business"): it receives the notification
`Your balance is at or below 0: you are out of business and cannot act.` (written `[day {d}, round {r}] ...` like
every notification). Inside a decide chain (or a correction call) this is decided on the participant's private
projection of its balance; the market's status and the notification come when the reply is taken up (rounds.md, "4.
The first calls: in parallel, taken up in the turn order", items 3 and 4, and "8.4 The correction call"). Effects on
the turn in progress:
- the charge of a decide-chain reply that called functions but not act: its programs do not run, no further call is
  made, and the reply is read as an act call with an empty action list and no memory; its results are the single
  line `(note: your reply had no memory; your memory is unchanged)` (section 10.4);
- the charge of a reply with an act call: the first of its actions to come up in the action phase runs and fails with
  the first refusal that applies to it (actions-buyer.md, "Matching the name, and the checks before the action's
  own"), which is `you are out of business (balance at or below 0) and cannot act` unless the name itself is refused
  (no name, unknown, not available in this market, or the other role's action); either way its later actions do not
  run, and the out-of-business note of section 9 follows its line;
- the charge of an unreadable reply: the turn is lost with the single-error text of section 12;
- the charge of the correction call: the correction's first action fails the same way (the first refusal that
  applies to it), the rest do not run (with the same note);
- in every case it gets no correction (if not already given), and its memory is written at the turn's end as for
  any participant, by the rule of 10.3, in business or not: so a manager put out of business by its correction
  call's charge keeps the correction's memory if the correction had one, else the decide reply's, and gets the
  memory lines of 10.4.
