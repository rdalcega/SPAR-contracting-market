# Models

This file specifies the model layer: the one interface through which the market calls every model (what a call
takes, what it returns, how it can end, and what the market does with each ending), the providers a model spec can
name, the OpenRouter implementation that is the default (its endpoint, request body, headers, time limits, retries,
the wait through a lost network, how a reply is read, the token estimate and the dollar cost), how a call's dollar
cost becomes a token charge in credits, the conversation summarizer's calls, and the cost cap. What a turn sends and
how it reads the reply is in turns.md; when calls are made in a round is in rounds.md; the summarizer's prompts and
how its replies are used are in messaging.md; the offline mock model, the log and episode statuses are in running.md.
Nothing in this file depends on `message_channels`, except that the summarizer is called only when it is not `off`.

## 1. The model layer

Every model call of the market is made through one provider-agnostic interface, the model layer: a call takes the
inputs of 2.1, returns the fields of 2.2 and ends in one of the ways of 2.4, whatever provider answers. The provider a
spec names may change only what this file says depends on it (the request on the wire, how the response is read,
where the dollar cost comes from); what a turn or the summarizer sends, how a reply is read, charged, counted toward
the cost cap and logged, and what each ending does (2.5) must be the same for every provider. How the layer is built
is the implementer's choice. There are exactly two kinds of caller:

| caller | messages | functions offered | where specified |
|---|---|---|---|
| a participant's turn (each call of the decide chain, and the correction call) | system and user (the view), and within the turn assistant and tool messages, and on a correction's text path a second user message (the correction's body; 2.3) | `[compute, act]`, or `[act]` in a correction | turns.md |
| the conversation summarizer (only when `message_channels` is not `off`) | system, user | none | messaging.md, "5. The summarizer" |

Each call uses one model spec (config.md, "A model spec"): a participant's call uses its seat's spec (its entry in
`models.buyer_seats` / `models.manager_seats`, else its role's spec); a summarizer call uses `models.summarizer`. The
`llm` block supplies what a spec leaves out (except a temperature for `models.summarizer`, which sends none unless its
spec names one: 2.1) and the settings no spec can change (`llm.timeout_s`, `llm.max_retries`,
`llm.network_patience_s`).

The market sends no random seed to any model of its own accord (a spec's `extra_body` may name one, 4.2): two
episodes of the same seed draw the same market (randomness.md) but differ wherever a model's reply differs.

## 2. A call

### 2.1 What a call takes

| input | value |
|---|---|
| messages | the conversation, in order (2.3) |
| functions | a list of function definitions in the OpenAI tools format (`{"type": "function", "function": {"name", "description", "parameters"}}`; their exact texts: turns.md, "Exact definitions at the default configuration"), or none |
| tool_choice | `"required"` (the model must call some function), a named function (`{"type": "function", "function": {"name": "act"}}`), or none; only sent with functions |
| model | the spec's `model` |
| max_tokens | the spec's `max_tokens`, else `llm.max_tokens`: one limit for hidden reasoning and the reply together |
| temperature | for a participant: the spec's `temperature` if the spec has that key, else `llm.temperature`; for the summarizer: the spec's `temperature` if it has that key, else none. A null value means no temperature is sent |
| provider-specific extras | the spec's `extra_body` (a mapping; empty if absent), merged over the request last (4.2) |
| time limits and retries | `llm.timeout_s`, `llm.max_retries`, `llm.network_patience_s` (sections 4.3 to 4.6) |

The market itself never sets a per-call token limit other than the spec's.

### 2.2 What a call returns

A call that succeeds returns one reply with these fields:

| field | meaning |
|---|---|
| text | the reply's visible text, `""` if none (4.7) |
| function calls | the reply's function calls exactly as the provider returned them, normally each with its `id`, `type`, and `function` holding `name` and `arguments` (normally a JSON string), plus any extra fields the provider included; or none. Only their shape is checked (4.7) |
| reasoning | the reply's hidden reasoning text if the provider returned it, else `""` (4.7). Never shown to any participant; sent back to the model within the same turn (2.3) and logged |
| input tokens | the provider's reported prompt tokens, else an estimate (4.8) |
| output tokens | the provider's reported completion tokens, else an estimate (4.8). They include hidden reasoning, as providers bill it |
| reasoning tokens | the provider's reported reasoning tokens, else 0 (already inside the output tokens; for the log and statistics only) |
| dollar cost | the call's cost in US dollars (4.9) |
| cost known | false only when no cost could be found or computed (4.9) |
| counts estimated | for the input and the output tokens each, whether the count was reported or estimated (4.8; log only) |
| cost source | how the dollar cost was obtained (4.9): reported (rule 1), computed from the spec's prices (rule 2), $0 for a free provider (rule 3, `ollama`) or unknown (rule 3, any other provider); the mock's: running.md, "Requirements" (log only) |
| failed attempts' cost | the sum of the dollar costs reported by this call's failed attempts (4.4), 0 if none (cost cap and log only) |
| finish reason | the provider's reason the reply ended, as given (`stop`, `tool_calls`, `length`, `content_filter`, ...), or none. An adapter for a provider whose API names these stops otherwise must report a stop at the token limit as `length` (turns.md, "7. Reading the reply that ends the chain", reads such a reply as cut) and a stop by the provider's content filter as `content_filter` (messaging.md, "5.3 Reading the reply, and failures", treats a summary so ended as unusable) |
| served by | which upstream served the call, when the provider says (OpenRouter's routing; log only), else none |
| latency | optional: wall time of the call including retries and waits, if the implementation logs it |

What the market reads from a reply: turns.md, "Reading the reply that ends the chain" (a participant's reply; `length`
matters there) and messaging.md, "Reading the reply, and failures" (a summary). The log records each reply's fields
(running.md, "The log: required content").

### 2.3 The messages, and re-sending a reply within a turn

The model layer must carry these message shapes to the provider (the OpenAI chat-completions format; turns.md,
"The conversation's messages", says which messages a turn builds):

```
{"role": "system", "content": <text>}
{"role": "user", "content": <text>}
{"role": "assistant", "content": <text>, "tool_calls": <list>, "reasoning": <text>}
{"role": "tool", "tool_call_id": <id>, "content": <text>}
```

What each message holds when a reply is put back into its turn's conversation (the stripped text, the function
calls exactly as returned, `reasoning` only when the reply's reasoning field (2.2) is non-empty, and the
correction's text path) is turns.md's: "2. The conversation's messages" and "11.4 How the body joins the
conversation". The OpenRouter default sends `reasoning` as written above. An adapter for a provider whose API has another
way to pass a previous reply's reasoning must pass it that way; one whose API has none may drop it. Nothing else about a
reply (token counts, cost, finish reason) is sent back.

### 2.4 How a call can end

| ending | what it is |
|---|---|
| a reply | 2.2. Whatever it contains (empty, no function call, cut at the token limit) it is a reply; whether the market can use it is the caller's question (turns.md, "Reading the reply that ends the chain"; messaging.md, "Reading the reply, and failures") |
| a failed call | the provider could not give a reply: a failure that is not retried, or the retries or the network patience used up (sections 4.4 to 4.6). It carries an error text (4.10) and the sum of the dollar costs its failed attempts reported (4.4; 0 if none), which counts toward the cost cap and is logged, and nothing else |
| the cost cap reached | the call was not made because the run's spending has reached `cost_cap_usd` (section 7). This is not a failed call |

Any other failure inside the model layer (a defect) is not a failed call: it ends the episode with status `error`
(running.md, "Errors"). A response the layer cannot read is not such a defect: a 2xx body that is not a JSON object,
malformed `tool_calls` and a non-finite token count or cost are each a failed attempt, retried like any other (4.4,
4.7), never an end of the episode.

### 2.5 What the market does with each ending

| caller and ending | consequence | where |
|---|---|---|
| a participant's decide-chain call fails | the turn is lost to the provider: the participant is restored as if the round's view had not been built, runs no action, writes no memory, is told nothing; replies of the chain received before the failure stay charged | rounds.md, "Lost to a failed model call" |
| a participant's correction call fails, its first act readable | the first act's results stand and gain the line `(note: your correction could not be sent; the results above stand)` | turns.md, "Reading the correction" |
| a participant's correction call fails, its first act unreadable | the turn is lost to the provider, as above | rounds.md, "Lost to a failed model call" |
| more than 5 x `time.rounds_per_day` turns of the episode (all participants together, the whole episode, across resumes) lost to failed calls | the episode is aborted at once with status `aborted_lost_turns` and is not settled; with the default 3 rounds, the 16th such turn | rounds.md, "Lost to a failed model call"; running.md, "Too many lost turns" |
| a participant's reply the market cannot read | a format error, not a failed call; the reply is charged | turns.md, "7.1 Format errors" and "11.6 Reading the correction" |
| a summarizer call fails, or its reply is unusable | that summary attempt fails and is retried at once, up to 4 attempts in all; when all fail, the view shows the placeholder and the next view tries again; an unusable reply's cost is kept in the episode's dollar total. Never counted toward the abort above | messaging.md, "4.3 When a summary is written" and "5.3 Reading the reply, and failures" |
| the cost cap is reached, any caller | the episode stops at once (status `aborted_cost_cap`), not settled; it can be resumed from its last completed day | section 7; running.md, "The cost cap" |

## 3. Providers

A model spec's `provider` names one of these. An implementation must provide all four. All but `mock` speak the OpenAI
chat-completions protocol, and the OpenRouter default of section 4 applies to all of them, with the differences in the
table.

| provider | base URL when the spec gives none | API key variable when the spec gives none | differences from section 4 |
|---|---|---|---|
| `openrouter` (the default implementation) | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | none: only this provider gets `usage: {include: true}` in the body and the `X-Title` header |
| `ollama` (a local server) | `http://localhost:11434/v1` | none (no key sent) | no `usage` field, no `X-Title`; free: a reply with no reported cost costs $0 unless the spec gives both prices (4.9), and that cost counts as known |
| `openai_compatible` (any server with `POST {base_url}/chat/completions`: another hosted API or a self-hosted model) | none: the spec must give `base_url`, and a config whose `openai_compatible` spec has none is refused when it is loaded (config.md, "Checks across parameters") | none | no `usage` field, no `X-Title` |
| `mock` (the free offline mock model) | not used | not used | makes no network call; specified in running.md, "The offline mock model"; its cost: 4.9 and section 7 |

A spec's `base_url` replaces the provider's; its `api_key_env` replaces the provider's key variable, and
`api_key_env: null` sends no key. The key is the value of that environment variable when the backend is set up
(below). An implementation may also read variables from a `.env` file, the real environment taking precedence.

A self-hosted model that reports no dollar cost is charged at its spec's configured prices (4.9); this is how any
provider other than OpenRouter is normally priced.

**Setting up.** At the start of an episode (fresh or resumed), before any day is played, the backend of every spec the
episode will call is set up: every seat's spec, and `models.summarizer` only when `message_channels` is not `off` and
the summarizer will make real calls (messaging.md, "Under the offline mock"). Setting up fails, and the episode ends
with status `error` before playing (running.md, "4.3 Errors"; the other episodes of the run go on), when the key
variable is named (by the provider or the spec) but unset or empty; the error text names the variable (for example
`set OPENROUTER_API_KEY in your environment or in .env`). This holds for every provider, `mock` included: a mock spec
that names an `api_key_env` needs that variable set, though the mock never uses the key.

Another provider (an API that is not OpenAI-compatible) may be added behind the same interface. Its adapter must take
the inputs of 2.1, carry the messages of 2.3 (function calls, tool answers and, where the API allows, reasoning) and
return the fields of 2.2, with the same endings (2.4) and the same dollar-cost rule (4.9: the provider's reported
cost, else tokens at the spec's prices).

## 4. The OpenRouter default

Everything in this section applies to every OpenAI-compatible provider of section 3, with that table's differences.

### 4.1 The request

- `POST {base_url}/chat/completions`, where `{base_url}` is the spec's or the provider's base URL with any trailing
  `/` removed (default: `https://openrouter.ai/api/v1/chat/completions`).
- Headers: `Content-Type: application/json`; `Authorization: Bearer {key}` when a key is set; for `openrouter` only,
  `X-Title: {application name}` (OpenRouter's app attribution; any fixed name). No other headers are required.
- The body is the JSON text of 4.2's object. A string holding an unpaired surrogate (turns.md, "7.2 Reading JSON")
  must be sent as its `\uXXXX` escape (it has no UTF-8 form). The reference implementation writes every non-ASCII
  character as such an escape (as 4.8's JSON text), so its body is ASCII.
- The request asks for one complete JSON response, not a stream (4.2 sends no `stream` key; for an `extra_body` that
  sets one, see 4.2).

### 4.2 The body

Its keys, with `extra_body` merged over them last:

1. `"model"`: the spec's `model`.
2. `"messages"`: the messages (2.3).
3. `"max_tokens"`: 2.1.
4. `"temperature"`: 2.1; the key is left out when the value is null.
5. `"usage": {"include": true}`: `openrouter` only. It makes OpenRouter report each call's dollar cost.
6. `"tools"`: the function definitions; only when the list is non-empty.
7. `"tool_choice"`: only when functions are sent and a tool_choice is given.
8. Then every top-level key of the spec's `extra_body` is set on the body, replacing a key of the same name set above
   (a shallow merge: a nested mapping in `extra_body` replaces the whole value, it is not merged into it).

Nothing else is sent (no seed, no `top_p` and so on, unless `extra_body` names them). Because `extra_body` is merged
last it can override anything the market sets, including `tool_choice` (for example `extra_body: {tool_choice: auto}`
makes every call of that spec leave the function choice to the model; a reply without a function call is then read as
turns.md, "Format errors" says). Sampling settings, reasoning effort and OpenRouter's provider routing all go in
`extra_body`.

What the other files require of a call's messages and functions (the system prompt as the first message: prompts.md,
"1. How a system prompt is built"; a turn's messages, functions and tool_choice: turns.md) applies to what the market
passes to the model layer (2.1). `extra_body` changes only the request on the wire, after that, and is the operator's
setting: it is not checked against those requirements, and a config is not refused for it. An `extra_body` that
replaces `messages` or `tools` sends its own value instead (4.8's estimate still uses the caller's). One that sets
`stream: true` is sent as it is; the streamed response is not a JSON body, so every attempt is retried (4.4) and every
call of that spec fails once its retries are used up.

At the default configuration a participant's first call of a turn sends:

```json
{"model": "qwen/qwen3.8-27b", "messages": [...], "max_tokens": 64000, "temperature": 0.8,
 "usage": {"include": true}, "tools": [<compute>, <act>], "tool_choice": "required",
 "provider": {"quantizations": ["bf16", "fp16", "fp8", "unknown"]}}
```

and a summarizer call (when `message_channels` is not `off`) sends:

```json
{"model": "anthropic/claude-sonnet-5.5", "messages": [<system>, <user>], "max_tokens": 8000,
 "usage": {"include": true}, "reasoning": {"effort": "high"}}
```

(The body's top-level key order and the JSON whitespace are free; the function definitions keep the key order
turns.md, "3.1 Exact definitions at the default configuration", gives.)

### 4.3 One attempt and its time limit

A call makes one or more attempts, each sending the same request. A response is read (4.4, 4.7) only if its complete
body arrived within `llm.timeout_s` seconds of the attempt's start (connecting included). An attempt whose body is not
complete by then ends as a **timeout** (4.4): also when the server is still sending (OpenRouter keeps a slow request
open by sending padding, so the limit covers reading the whole body), and also when the body is complete by the time
the attempt notices that its time is up. Failing to connect or to send the request in time is a **network failure**
(4.4) instead. The attempt may notice that its time is up later than `llm.timeout_s` (the outcome is the same), but it
must be given up no later than about three times `llm.timeout_s` after its start, however the server sends. Example:
the reference implementation gives connecting, sending and the wait for the response's headers up to `llm.timeout_s`
each and checks the limit before each read of the body, so it notices up to about one more `llm.timeout_s` late
while a body is arriving.

### 4.4 What each attempt ends in

Here and in 4.7 to 4.9, a JSON value is **empty** when it is missing, null, `false`, a number equal to 0 (`0`, `0.0`),
`""`, `[]` or `{}`; any other value is **non-empty**.

Checked in this order:

| attempt's outcome | kind |
|---|---|
| A response with HTTP status 2xx whose body is a JSON object with a non-empty `error` and an empty `choices` | retried ("provider error") |
| Any other 2xx JSON object whose first choice is an object with `finish_reason` equal to `"error"` or a non-empty `error` field (the upstream model failed mid-generation) | retried ("generation error") |
| Any other 2xx JSON object that is malformed (4.7: a field read there has a shape or a value it cannot be read in) | retried ("malformed response") |
| Any other 2xx JSON object | **the reply** (4.7). This includes a body with an `error` beside non-empty `choices` (the error is ignored) and a body with neither (an empty reply) |
| HTTP status 408, 409, 425 or 429, or any status 500 or above | retried |
| Any other status that is not 2xx: a redirect (3xx; redirects are not followed) or an HTTP error status (400, 401, 402, 403, 404, 413, 422, ...) | **the call fails at once**, no retry (for example 402 when the account has no credits) |
| A network failure: the connection could not be made or the request not sent (DNS failure, connection refused, a dropped network, or out of time, 4.3), or later in the attempt the connection was reset or broken, or closed by the server before any response | waited out (4.6); not counted as an attempt |
| A timeout (4.3); a response that is not valid HTTP or is cut short; a body that is not valid JSON, or a 2xx body that is valid JSON but not an object; any other input/output error that is not a network failure | retried |

"Retried" failures are counted: after each one, if the number of counted failures so far exceeds `llm.max_retries`,
the call fails; otherwise the call waits (4.5) and makes a new attempt. So a call makes at most
`llm.max_retries + 1` counted attempts (6 at the default), plus any number of attempts that ended in network failures
within the network patience.

Failed attempts are never charged to a participant, and a failed attempt is not a reply: it has no reply cost
(4.9). But if a failed attempt's response body, of a 2xx response or of an HTTP error status alike, is a JSON object
whose `usage` is an object with a `cost` that is a finite number (in practice a provider error or a generation error, which
the provider may have billed), that cost is added to the cost cap's running total when the attempt ends (section 7).
Such a cost belongs to no reply: it is not charged in credits, not part of any participant's or the summarizer's
dollar cost, and not counted as a call of unknown cost.

### 4.5 Waits between attempts

After a counted failure that does not end the call, the call waits before its next attempt, so that a provider that
fails briefly or limits the rate has time to recover: no wait may be shorter than about 2 seconds or longer than about
a minute, and at the defaults (5 retries) the waits before a call gives up must add up to about a minute. Any
randomness in them must come from outside the market's keyed draws (randomness.md), so that retrying never changes the
market. The bounds are behaviour: they decide how long a provider's outage or rate limit a call outlasts, and so
whether a turn is lost to it (rounds.md, "10.1 Lost to a failed model call") and when an episode is aborted for lost
turns. Within them the exact lengths are free: no text and nothing in the log's required content depends on them. Example: the reference implementation waits about 2, 4, 8, 16 and 32 seconds (doubling, capped at
60, plus up to 1 second of jitter).

### 4.6 Waiting out a lost network

A network failure (4.4) means this side lost its connection (Wi-Fi dropped, the machine asleep), not that the
provider failed, so it is waited out instead of costing participants their turns:
- At the first network failure of a streak, note the time. Any counted failure ends the streak.
- At each network failure: if more than `llm.network_patience_s` seconds (default 900) have passed since the streak's
  first failure, measured in wall-clock time (time during which the machine sleeps counts), the call fails.
  Otherwise wait, then make a new attempt without counting one.
- The waits between these attempts must not let attempts follow each other without pause, and must let a new attempt
  follow within about half a minute of the network's return; any randomness in them comes from outside the keyed
  draws, as in 4.5, and their lengths are otherwise free. Example: the reference implementation doubles them from
  about 2 seconds up to 30, plus up to 1 second of jitter.

### 4.7 Reading the reply

From the body (a JSON object; "empty" as defined in 4.4):
- the choice is the first element of `choices` (an empty object if `choices` is empty); the message is the choice's
  `message` (an empty object if it is empty);
- **text**: the message's `content` if it is a string; if it is a list of parts, the concatenation, in order, of the
  `text` field of every part that is an object (`""` for a part without one; parts that are not objects are
  skipped); `""` if it is empty;
- **function calls**: the message's `tool_calls`, as given; none if empty. Beyond the shape the malformed rules
  below check, the calls are not checked or changed: a call may lack `id`, `type` or `function` (a call whose
  `function` is empty has no name and no arguments), its `name` and `arguments` may be any JSON value, and two calls
  may share an id. The caller reads them as turns.md says (a call is act or compute only when its name is exactly
  that string), and a tool message answers a call with its `id` as given, `null` when the call has none;
- **reasoning**: the message's `reasoning` if non-empty, else its `reasoning_content` if non-empty, else `""`; a value
  that is not a string is replaced by its JSON text (4.8);
- **finish reason**: the choice's `finish_reason`, as given;
- **token counts**: 4.8; **reasoning tokens**: `usage.completion_tokens_details.reasoning_tokens`, or 0 when it is
  empty (an empty `usage` or `completion_tokens_details` is read as an empty object);
- **dollar cost**: 4.9;
- **served by**: the body's top-level `provider` field (OpenRouter's name of the upstream it routed to, e.g.
  `Together`), else none.

Token counts are whole numbers; the cost is a double. Reported counts and costs are otherwise used as given. A key
that occurs more than once in an object of the response keeps its last value, as in agents' JSON (turns.md, "7.2
Reading JSON").

The body is **malformed** (retried, 4.4) when a field read above does not have the shape it is read in: `choices`
not a list whose first element is an object; `message` not an object; `tool_calls` not a list of objects, or a call
whose `function` is not an object; `content` neither a string nor a list, or an object part whose `text` is present
and not a string (`null` included); `usage` or `completion_tokens_details` not an object; a token count that is not
a finite number (a boolean is not a number); a `usage.cost` that is present, not null and not a finite number
(`false` and `""` included); or a cost from rule 2 of 4.9 that is not finite. An empty `choices`, `message`,
`tool_calls`, `function`, `content`, `usage`, `completion_tokens_details` or token count is never malformed: it is
read as above. So every count, cost and charge the market uses is a finite number.

### 4.8 Token counts, and the estimate when usage is missing

- Input tokens: `usage.prompt_tokens`; if it is missing or null (or `usage` is missing), the estimate of the
  request's text. A reported 0 is used as 0.
- Output tokens: `usage.completion_tokens`; if it is missing or null, the estimate of the reply's text.

A reported count (these two, and the reasoning tokens) that is a number but not a whole number is truncated toward
zero, and rule 2 of 4.9 prices the truncated count (reported `prompt_tokens: 10.7` gives 10 input tokens, priced as
10).

`estimate(s) = max(1, floor(len(s) / 4))`, with `len(s)` the number of characters of s (README, "Number formats and
rounding").

- The request's text: for each message in order, its `content` (`""` if missing or null), followed, if the message has
  function calls, by the JSON text of its `tool_calls`; all concatenated; then, if the body has a non-empty list of
  functions, the JSON text of that list appended. Other fields (`role`, `tool_call_id`, `reasoning`) are not
  counted. The messages and functions are those of the body as sent, after the model spec's `extra_body` has been
  merged in (4.2): an `extra_body` that replaces `messages` or `tools` changes the estimate.
- The reply's text: its text (4.7) followed by the JSON text of its function calls, or, when it has none, the JSON
  text of the empty string (`""`, two characters).

JSON text here means the serialization with `, ` between items, `: ` between a key and its value, keys in the order
received, and every non-ASCII character written as a `\uXXXX` escape (a character above U+FFFF as its UTF-16
surrogate pair) (what Python's `json.dumps` writes by default).

Examples: a 400-character user message and the reply `hello world!!` with no function calls and no usage: 100 input
tokens, 3 output tokens. Reported `prompt_tokens: 0, completion_tokens: 0` with the same request and the reply `hi`:
0 and 0.

### 4.9 The dollar cost

In this order:
1. If the body has `usage.cost` (present and not null, hence a finite number: 4.7): that value, as reported, even 0 (OpenRouter's billed cost,
   which it reports because of `usage: {include: true}`). Cost known.
2. Else, if the spec has both `price_in_per_m` and `price_out_per_m`:
   `input tokens x price_in_per_m / 10^6 + output tokens x price_out_per_m / 10^6`, evaluated left to right in
   double precision, with the token counts of 4.8 (reported, truncated to whole numbers, or estimated). Cost known. Example: 100 and 3 tokens at
   0.15 and 1.875 give 2.0625e-05.
3. Else $0: cost known for provider `ollama`, unknown for every other provider.

No rounding is applied here. Every reply has a cost, including replies the market then cannot use.

The mock provider's replies have the cost running.md, "Requirements" (under "The offline mock model") gives them, so
a mock participant with prices is charged credits; but it spends no real money (section 7).

Calls of unknown cost are counted, so that the run can report that its dollar total is an underestimate (running.md,
"A run" and "Core metrics"). Which specs may lack prices: config.md, "3.4.1 A model spec". At the default
configuration every spec has prices, so rule 3 never occurs.

### 4.10 Error texts

A failed call's error text is recorded in the log (running.md, "The log: required content") and, when it ends an
episode, in its summary (running.md, "The summary"). It is never shown to a participant or a model. Its wording is
free; it must name the model and the reason (for example `HTTP 402 for model 'qwen/qwen3.8-27b': {the first 500
characters of the response body}`, or `giving up on '{model}' after 6 attempts: {the last failure}`).

## 5. Token charges in credits

Every reply a participant's call receives is charged to that participant (turns.md, "Token charges", says when, and
what a charge does to a manager's business):

```
charge = round(dollar cost x credits_per_usd, 6)
```

`round` is the README's (6 decimals, so a charge can be a fraction of a cent). A charge of 0 or less is skipped: no
balance change, no ledger line. The multiplication comes before the rounding. At the default `credits_per_usd` = 1 the
charge is the dollar cost rounded to 6 decimals.

The prices a participant's system prompt states are its own seat spec's `price_in_per_m` and `price_out_per_m`, each
times `credits_per_usd` (prompts.md, "Fields in both roles' templates"). The actual charge follows the provider's
reported cost when there is one, which can differ from those prices (the prompt says so). Failed attempts and failed
calls are never charged, even when a failed attempt reported a cost (4.4); summarizer calls are never charged to anyone
(section 6).

## 6. The summarizer's calls

When `message_channels` is not `off`, the market calls the summarizer while views are built (messaging.md, "When a
summary is written"). Each call:
- uses `models.summarizer` through the same model layer (sections 2 to 4): two messages, system and user (their texts:
  messaging.md, "The request"), no functions, no tool_choice;
- sends no temperature unless `models.summarizer` has a `temperature` key with a non-null value (the agents'
  `llm.temperature` never applies); `max_tokens` is the spec's (default 8000), else `llm.max_tokens`; the spec's
  `extra_body` is merged last (default `{reasoning: {effort: high}}`);
- has the same time limits, retries and network patience as every call (`llm.*`);
- checks the cost cap before it is made (section 7), each attempt of a summary that is retried included;
- is never charged to any participant (no credits move; `credits_per_usd` does not apply). Its dollar cost, including
  that of a reply that turns out unusable, counts toward the cost cap and the episode's reported dollar total
  (running.md, "Core metrics").

How the reply is read, and what a failed call or an unusable reply does (it is retried at once, up to 4 attempts
in all, before the view shows the placeholder): messaging.md, "Reading the reply, and failures" (2.5 here summarizes
it). In `off` the summarizer is never set up or called.

## 7. The cost cap

`cost_cap_usd` (config.md, "Run settings") caps the real dollars spent on model calls by one invocation of the runner
(all its episodes together, running in parallel or not). It is counted in dollars; `credits_per_usd` does not affect it.

**What counts.** One running total per invocation, starting at $0, shared by every episode and every call of the
invocation, participants' and the summarizer's alike. Each reply received from a real provider adds its dollar cost
(4.9) as soon as it arrives, whether or not the market can use the reply; a reply of unknown cost adds $0. A failed
attempt adds the cost its response reported, if any, as soon as the attempt ends (4.4); otherwise failed attempts and
failed calls add nothing. Replies of the mock provider add $0 (it spends nothing), though they are
charged credits in the market. A later invocation (for example one that resumes interrupted episodes) starts again
from $0.

**When it is checked.** The cap is reached when the total is at or above `cost_cap_usd` (null: never). It is checked:
1. once before each wave of participants' calls in a round: before the round's first calls start, and before its
   correction calls start if there are any (rounds.md, "The first calls: in parallel, taken up in the turn order" and
   "The correction call");
2. before each summarizer call.

It is not checked before the individual calls inside a wave: once a wave is admitted, every call of every chain in it
is made (with its retries), so whether a call is made never depends on how fast other calls come back. A wave can
therefore overshoot the cap by all of its calls, and so can parallel episodes' waves admitted at the same time.

**When it is reached.** The check raises a stop, not a failed call: the episode that made the check stops at once,
in the middle of its round if need be, with status `aborted_cost_cap` and an error text naming the cap and the amount
spent (reference wording: `cost cap ${cap} reached (spent ${spent})`, both `F2`); it is not settled; its last
completed day is kept, so a later invocation resumes it from the start of the interrupted day (running.md,
"Checkpoints and resuming"). Every other
episode of the invocation stops the same way at its own next check. A stop is never counted as a lost turn and never
shows the summarizer's failure placeholder.

## 8. What participants are told about the model layer

Participants are told only their token prices, in their system prompt (section 5; prompts.md, "Fields in both roles'
templates"), and see their charges in their accounts (view.md, "Your accounts so far (credits)"). Nothing else of
this file is stated to any participant: not the model's name, the provider, the temperature, the token limit's value,
the time limits or retries, the cost cap, or that a turn was lost. A reply cut at the token limit is told only that it
"hit the length limit" (turns.md, "7.1 Format errors"). A failed call leaves only two traces a participant
sees (2.5): the line `(note: your correction could not be sent; the results above stand)` in its results after a
failed correction call, and, when `message_channels` is not `off`, the summary placeholder in a view after a
summarizer call failed or its reply was unusable (messaging.md, "3.5 The summary line" and "5.3 Reading the reply,
and failures").
