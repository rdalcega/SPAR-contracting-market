# Messaging

This file specifies everything that `message_channels` changes outside the system-prompt texts: who may send
messages to whom in each of the five modes; the `send_message` action (its arguments, what `to` may name, every check
in order with its exact text, the length cap, how it counts among a turn's actions, when its recipients see it); the
conversations each participant's view shows (which, in what order, under what titles, each line's format, the "new"
marker, the summary line); how a conversation's older messages are batched and replaced by one shared summary, when
that summary is written and what happens when writing it fails; the summarizer's exact requests and model call; and
what the summarizer costs. With `message_channels` = `off` none of this exists: no participant has `send_message` (an
action named send_message is refused as 2.2 says, with the text that applies under `off` only; an act item that gives
send_message only as its key has no name and gets the no-name refusal: turns.md, "7.5 An action named only by a
key"), no view has a Messages section, and the summarizer is never called.

Related rules that live elsewhere: the checks every action goes through before its own (actions-buyer.md, "1.2
Matching the name, and the checks before the action's own"); how an act call's items become actions and the action
cap (turns.md, "7.4 From items to actions" and "8. The action cap and the notes that open the results"); the order a
round's actions run in and R1 (rounds.md, "6. The action phase"); the view's other sections and its general spacing
(view.md, "2.2 The sections and their order", "2.3 Items and empty lines" and "7. The messages section"); the
system-prompt texts of each mode (prompts.md, "5. What each message_channels mode changes", and `text/`); the model
layer, its retries and the cost cap (models.md, "6. The summarizer's calls" and "7. The cost cap"); the log and the
offline mock model (running.md, "6.3 The log: required content" and "7. The offline mock model").

Every text in a fenced block is byte-exact apart from its `{placeholders}`. "Character", "whitespace", the number
formats (`INT`, ...), `TEXT` and the singular rule are as README.md, "Number formats and rounding", defines them;
lengths and cuts count characters.

## 1. The five modes

A role "can send" when it has the `send_message` action. The rule: buyers can send in `buyers`, `within_roles` and
`all`; managers can send in `managers`, `within_roles` and `all`.

| `message_channels` | buyer can send to | manager can send to | buyer's view | manager's view |
|---|---|---|---|---|
| `off` | (cannot send) | (cannot send) | no Messages section | no Messages section |
| `managers` | (cannot send) | one other manager; all managers | no Messages section | `--- Messages between managers ---` |
| `buyers` | one other buyer; all buyers | (cannot send) | `--- Messages between buyers ---` | no Messages section |
| `within_roles` | one other buyer; all buyers | one other manager; all managers | `--- Messages between buyers ---` | `--- Messages between managers ---` |
| `all` | any other participant; all buyers; everyone | any other participant; all managers; everyone | `--- Messages ---` | `--- Messages ---` |

There are three group conversations:

| group | who may send to it and read it | exists in modes |
|---|---|---|
| all managers | managers | `managers`, `within_roles`, `all` |
| all buyers | buyers | `buyers`, `within_roles`, `all` |
| everyone | every participant | `all` |

- A role that cannot send reads no conversation at all, even when the other role can (a buyer under `managers`, a
  manager under `buyers`): its view has no Messages section, not an empty one.
- A group conversation exists whatever the number of members. With exactly 2 members of a role, the all-buyers (or
  all-managers) conversation has the same two readers as their private conversation; both are kept and are separate
  conversations.
- A participant who is out of business (episode.md, "9. Out of business") can still be sent messages, and its
  conversations stay in the other participants' views. It takes no turns, so it sends nothing and its view is not
  built.
- Apart from the system prompts and the action lists that refusals quote (actions-buyer.md, "1.1 The actions of
  each role" and "1.2 Matching the name, and the checks before the action's own"), nothing else in the market depends
  on the mode: the act and compute
  function definitions are the same in all five modes (turns.md, "3. The two functions"), and messages never move
  money or produce notifications.

## 2. `send_message(to, text)`

### 2.1 Arguments

| argument | name | required |
|---|---|---|
| recipient | `to` | yes |
| message text | `text` | yes |

An argument counts as given as actions-buyer.md, "1.4 Arguments", defines (a value other than null and the empty text
`""`; `0`, `false`, whitespace, lists and objects count as given). Arguments the action does not read are ignored,
`recipient`, `message` and `content` among them.

### 2.2 Before send_message's own checks

The checks every action goes through come first (actions-buyer.md, "1.2 Matching the name, and the checks before the
action's own"). For send_message they mean:

1. A role that cannot send in the mode gets, for an action that reaches the market under the name send_message
   (turns.md, "7.4 From items to actions"), with `{list}` the role's "cannot send" list of actions-buyer.md, "1.1 The
   actions of each role": under `off` (every participant)
   ```
   'send_message' is not available in this market. Your actions: {list}.
   ```
   and under `managers` (a buyer) or `buyers` (a manager), where send_message is not one of its actions, the unknown
   action text
   ```
   unknown action 'send_message'. Your actions: {list}.
   ```
2. A sender who is out of business gets the generic refusal of actions-buyer.md, "1.2 Matching the name, and the
   checks before the action's own", step 6. This happens only to a manager that the token charge of its own reply
   put out of business, when send_message is the first of that reply's actions to come up (turns.md, "13.2 Out of
   business by a token charge").
3. R1 (rounds.md, "6.6 Running one action: the state its turn saw (R1)"): the action is first checked on the state
   the sender's turn saw. send_message's checks read only the mode, the participants and the arguments, which nothing
   in a round changes, so the result is the same on the market.

### 2.3 Its own checks, in order

The first that applies decides the result; a failed send_message stores nothing.

| # | condition | error text |
|---|---|---|
| a | the recipient argument is not given | `missing argument 'to'` |
| b | the recipient is not a text (a number, boolean, list or object) | `to must be {allowed}` |
| d | the recipient is `managers`, `buyers` or `everyone` and that group is not one the sender may send to in this mode (2.4) | `you cannot send to "{group}": to must be {allowed}` |
| e | it is no group key, and no participant has that ID (2.4) | `no participant with id '{raw}' (participants: {ids})` |
| f | the participant it names is the sender | `you cannot message yourself` |
| g | the mode is not `all` and the participant it names has the other role | `{id} is a {their_role}: messages go only to other {role}s (to a {role} ID, or to "{role}s" for all of them)` |
| h | the message text is not given | `missing argument 'text'` |
| i | `TEXT` of the message text is empty or whitespace only | `text is blank; no message was sent` |

So when both arguments are bad, the recipient's error is reported.

Placeholders:
- `{allowed}`, by the mode (`{role}` is the sender's role, `buyer` or `manager`):
  - `managers`, `buyers`, `within_roles`:
    ```
    one {role} ID or "{role}s" (all the other {role}s)
    ```
  - `all`:
    ```
    one participant ID, "{role}s" (all the other {role}s) or "everyone" (all the other participants)
    ```
- `{group}`: the key as sent: `managers`, `buyers` or `everyone`.
- `{raw}`: the recipient exactly as given (a text by then).
- `{ids}`: every participant's ID, buyers first then managers, each role in seat order, joined by `, ` (as
  actions-buyer.md, "1.9 Participants").
- `{id}`: the named participant's ID as the market writes it; `{their_role}`: its role, `buyer` or `manager`.

Exact texts (seed 0 of the reference: buyers B-c53, B-d75; managers M-14b, M-849, M-f8c):

```
to must be one manager ID or "managers" (all the other managers)
you cannot send to "managers": to must be one participant ID, "buyers" (all the other buyers) or "everyone" (all the other participants)
you cannot send to "buyers": to must be one manager ID or "managers" (all the other managers)
you cannot send to "everyone": to must be one manager ID or "managers" (all the other managers)
no participant with id 'all' (participants: B-c53, B-d75, M-14b, M-849, M-f8c)
no participant with id ' managers ' (participants: B-c53, B-d75, M-14b, M-849, M-f8c)
B-d75 is a buyer: messages go only to other managers (to a manager ID, or to "managers" for all of them)
M-849 is a manager: messages go only to other buyers (to a buyer ID, or to "buyers" for all of them)
```

### 2.4 What the recipient may name

The group keys are exactly `managers` (all managers), `buyers` (all buyers) and `everyone`, compared with the
recipient as sent (no trimming, no case folding). A key names its group when the mode has that group and the sender
may send to it: `managers` for a manager under `managers`, `within_roles` and `all`; `buyers` for a buyer under
`buyers`, `within_roles` and `all`; `everyone` for anyone under `all`. A group key the sender may not send to in the
mode fails by row d. Any other recipient is looked up as a participant ID, exactly as written (`m-849`, ` M-849 `,
`all`, `Managers`, ` managers ` and `all managers` name no one, row e).

Outcome of each recipient by mode and sender (`sent` = the message is sent; letters are the rows of 2.3):

| recipient (exactly as sent) | `managers`: manager | `buyers`: buyer | `within_roles`: manager | `within_roles`: buyer | `all`: manager | `all`: buyer |
|---|---|---|---|---|---|---|
| another manager's ID | sent | g | sent | g | sent | sent |
| another buyer's ID | g | sent | g | sent | sent | sent |
| the sender's own ID | f | f | f | f | f | f |
| `managers` | sent (all managers) | d | sent (all managers) | d | sent (all managers) | d |
| `buyers` | d | sent (all buyers) | d | sent (all buyers) | d | sent (all buyers) |
| `everyone` | d | d | d | d | sent (everyone) | sent (everyone) |
| any other text | e | e | e | e | e | e |
| not a text | b | b | b | b | b | b |
| not given | a | a | a | a | a | a |

### 2.5 A sent message

The message text is converted with `TEXT` (README.md, "Number formats and rounding": an object becomes its `PY`
form, the number 0 becomes `0`). The market then records the message:

- sender: the sender's ID;
- recipient: the named participant's ID as the market writes it, or the group (all managers, all buyers, everyone);
- day and round: the current ones;
- text: the first `messaging.message_chars` characters of the converted text, exactly as sent: not stripped, line
  breaks kept. The cut counts surrounding whitespace too;
- its position in one market-wide sequence of messages, in the order the messages were sent.

The blank check (2.3 row i) looks at the whole converted text, before the cut, so the recorded text can be whitespace
only (600 spaces then `hi` is sent, and recorded as 500 spaces). Such a text is recorded and shown as it is (3.3).

Messages are never edited or removed. The result text is:

```
message sent to {label}{cut}
```

`{label}`: the recipient's ID, or `all managers`, `all buyers` or `everyone` for a group. `{cut}`: when the converted
text is longer than `messaging.message_chars` characters,

```
 (cut to its first {message_chars} characters)
```

(with a leading space; `{message_chars}` = `messaging.message_chars` in `INT`; *(singular rule)*: `character` when
`messaging.message_chars` is 1), else empty. The result line reads, for example,
`- send_message: OK: message sent to all managers (cut to its first 500 characters)` (actions-buyer.md, "1.3 The
result line").

### 2.6 Counting, cost, timing

- **Cost:** none. No credits move.
- **Counting:** every send_message item of an act call, successful or failed, is one of the call's
  `turns.max_actions` actions; there is no separate limit on messages. A failed send_message is a failed action like
  any other and leads to the correction (turns.md, "11. The correction"). Messages may also be sent in the
  correction.
- **Priority class:** 0 (actions-buyer.md, "12. Priority classes"; rounds.md, "6.4 The fixed order of kinds"). It
  takes part in no clash rule.
- **No notification:** the recipients are not notified. A message appears only in conversations (section 3).
- **Order:** a conversation lists its messages in the order they were sent, which is the order the round's actions
  ran in (rounds.md, "6.5 The order the phase's actions run in"). All of a round's first-phase messages come before
  its correction-phase messages.
- **When it is seen:** every view of a round is built from the market as it stands at the round's start, before any
  action of the round runs (rounds.md, "3. Views: all from the market as the round starts"). So the recipients, and
  the sender, first see a message in their views of the next round (a message sent in a day's last round: in round 1
  of the next day). A correction shows no message sent in the round: it continues the turn's conversation, whose
  view (Messages section included) was built at the round's start, and messages are never news (rounds.md, "8.3 The
  news and the facts").

## 3. Conversations in the view

### 3.1 Which conversations a participant reads, in order

For a participant P whose role can send (no conversations otherwise):

1. One **private conversation** with each other participant P may message individually: under `managers`,
   `buyers` and `within_roles`, each other participant of P's role; under `all`, every other participant, managers
   first, then buyers. Within each role the IDs are in ascending code-point order (`M-14b`, `M-849`, `M-f8c`;
   `B-c53`, `B-d75`). It holds every message between P and that participant, in either direction.
2. P's **role group**: all managers (P a manager) or all buyers (P a buyer). It holds every message sent to that
   group.
3. Under `all` only, **everyone**. It holds every message sent to everyone.

Every conversation is listed in every view, even when empty.

| mode | buyer's conversations, in order | manager's conversations, in order |
|---|---|---|
| `off` | none | none |
| `managers` | none | each other manager; all managers |
| `buyers` | each other buyer; all buyers | none |
| `within_roles` | each other buyer; all buyers | each other manager; all managers |
| `all` | each manager; each other buyer; all buyers; everyone | each other manager; each buyer; all managers; everyone |

Headings:

| conversation | heading |
|---|---|
| private, with participant X | `Your conversation with {X}` |
| all managers | `Conversation among all managers` |
| all buyers | `Conversation among all buyers` |
| everyone | `Conversation among all participants` |

### 3.2 The Messages section

The section comes right after the Payments section and before "Your accounts so far" in both roles' views (view.md,
"2.2 The sections and their order" and "7. The messages section", places it; it is the only view section that is
absent rather than empty). Its title is `--- Messages ---` under
`all`, else `--- Messages between managers ---` (a manager) or `--- Messages between buyers ---` (a buyer).

Layout of the section (schematic, not exact text: each `<...>` stands for one conversation's lines, and the
section holds one such entry per conversation of 3.1, in that order):

- the title line (above);
- one empty line;
- `<conversation 1>`, one empty line, `<conversation 2>`, one empty line, and so on to `<last conversation>`;
- one empty line, then the next section's title (`--- Your accounts so far (credits) ---`).

That is: one empty line after the title, exactly one empty line between consecutive conversations, and one empty line
after the last conversation before the next section's title. A conversation with no messages is one line:

```
{heading}: (no messages yet)
```

Otherwise it is its heading line `{heading}:`, then the summary line (3.5) if some of its messages are summarized
(section 4), then each message shown in full (3.3), oldest first. Schematic, not exact text (`<...>` stands for a whole
line as 3.3 and 3.5 give it, its two-space indent included):

```
{heading}:
<summary line>

<message line>

<message line>
```

The summary line and the message lines are indented by two spaces (part of their templates in 3.3 and 3.5). Inside a
conversation exactly one empty line precedes every message line except a conversation's first line after its heading:
with no summary, the first message line follows the heading directly; with a summary, the summary line follows the
heading directly and an empty line precedes the first message line.

Example (mode `managers`, M-14b's view, day 11 round 1: the whole section, from its title to the next section's
title; the messages and the summaries come from a test capture, not from the offline mock model of 5.5):

```
--- Messages between managers ---

Your conversation with M-849:
  Summary of the 10 earlier messages (written automatically; you are M-14b in it): (summary of 10 messages)

  [day 8, round 2] M-849 -> you: Mock message 49 from M-849: price 20 on post-1, t7?

  [day 8, round 3] M-849 -> you: Mock message 134 from M-849: price 20 on post-1, t7?

  [day 8, round 3] you -> M-849: Mock message 220 from M-14b: price 20 on post-1, t7?

  [day 9, round 1] M-849 -> you: Mock message 404 from M-849: price 20 on post-1, t7?

Your conversation with M-f8c:
  Summary of the 10 earlier messages (written automatically; you are M-14b in it): (summary of 10 messages)

  [day 8, round 1] M-f8c -> you: Mock message 534 from M-f8c: price 20 on post-1, t7?

  [day 8, round 2] you -> M-f8c: Mock message 646 from M-14b: price 20 on post-1, t7?

  [day 9, round 1] M-f8c -> you: Mock message 898 from M-f8c: price 20 on post-1, t7?

  [day 9, round 3] you -> M-f8c: Mock message 9 from M-14b: price 20 on post-1, t7?

  [day 10, round 2] M-f8c -> you: Mock message 311 from M-f8c: price 20 on post-1, t7?

Conversation among all managers:
  Summary of the 20 earlier messages (written automatically; you are M-14b in it): (summary of 20 messages)

  [day 10, round 3] you -> all managers: Mock message 297 from M-14b: price 20 on post-1, t7?

  [day 10, round 3] M-849 -> all managers (NEW): Mock message 853 from M-849: price 20 on post-1, t7?

--- Your accounts so far (credits) ---
```

A complete Messages section with a real summary is in `examples/view_manager_all_M-7f8_day28_round3.txt`.

### 3.3 The message line

```
  [day {d}, round {r}] {src} -> {dst}{new}: {text}
```

- `{d}`, `{r}`: the day and round the message was sent in (`INT`).
- `{src}`: `you` if the reader sent it, else the sender's ID.
- `{dst}`: `all managers`, `all buyers` or `everyone` for a group message; else `you` if it was sent to the reader;
  else the recipient's ID.
- `{new}`: ` (NEW)` (with a leading space) if the message is new to the reader (3.4), else empty.
- `{text}`: the recorded text on one line: split into lines at every line boundary (`\n`, `\r`, `\r\n` as one
  boundary, `\x0b`, `\x0c`, `\x1c`, `\x1d`, `\x1e`, `\x85`, U+2028, U+2029), the lines joined with one space. Only
  the very last line boundary of a text that ends with one adds nothing (it ends the last line); every other boundary
  gives one space, so a blank line gives two spaces in a row; nothing else is changed or stripped (`"a\n\nb"` shows
  as `a  b`, `"abc\n"` as `abc`, `"abc\n\n"` as `abc ` with one trailing space, `"\nabc"` as ` abc`).

### 3.4 New messages

A message is new to the reader when someone else sent it after the reader's previous view was built. A turn lost to a
failed model call (rounds.md, "10.1 Lost to a failed model call") does not count as having seen its view: the messages
new in it are new again in the next view, as its notifications are shown again. A summary written (and paid for)
while building that view stays stored, and its cost stays spent; only the reader's record of what it has seen goes
back. Messages behind a summary still count in the summary line's `{n_new} of them new`. Building a view counts as
seeing only the messages it shows, in full or under a summary it shows: messages behind the placeholder of a summary
that could not be written (4.4) are not seen, and stay new to the reader until a view shows them, so the summary
line's `{n_new} of them new` counts them when the summary finally appears.

### 3.5 The summary line

```
  Summary of the {E} earlier message{s} (written automatically{newcount}{you}): {summary}
```

- `{E}`: the number of messages in the conversation's summarized part (4.1), also when the summary is unavailable
  (`INT`); `{s}`: `s` unless E = 1.
- `{newcount}`: `, {n_new} of them new` when n_new > 0, else empty; n_new = the number of the summarized messages that
  are new to the reader (3.4), in `INT`.
- `{summary}`: the conversation's summary as shown (4.5), or, when it could not be written, the placeholder:
  ```
  (summary unavailable; the earlier messages are not shown)
  ```
- `{you}`: `; you are {reader ID} in it` on every summary, including one that starts with `(`; empty only with the
  placeholder.

Examples: `(written automatically; you are M-7f8 in it)`, `(written automatically, 1 of them new; you are M-849 in
it)`, `(written automatically, 8 of them new)` (with the placeholder).

## 4. Batching and summaries

### 4.1 Which messages are summarized

For a conversation with L >= 1 messages and k = `messaging.recent`, let cut = k × floor((L − 1) / k). The first cut
messages are summarized; messages cut + 1 to L (1 to k of them) are shown in full.

So 1 to k messages are all shown; at k + 1 the first k are summarized and 1 is shown; at 2k + 1 the first 2k are
summarized; and so on. When several batches' worth arrive in one round (e.g. from 5 to 25 messages with k = 10), the
cut jumps straight to 20: one summary covers the jump, and the remaining 5 are shown. The prompts' sentence on
batching is `mm_shown` (prompts.md, "3.4 Message fields: only in a template whose role can send messages").

### 4.2 One summary per conversation

A summary belongs to a conversation, not to a reader: the group, or for a private conversation the unordered pair of
participants. Every reader of the conversation sees the same text (with its own `{you}` clause, 3.5). Each
conversation has at most one current summary, the "stored summary" of 4.3: its text and the number of messages it
covers. A new summary replaces it; older summaries may be kept (for the log, for example) but play no further part.

### 4.3 When a summary is written

A summary is requested only while a reader's view is being built, when that conversation's summarized part (4.1) is
not empty:

1. If the stored summary covers exactly the summarized part's number of messages, it is shown. No call is made.
2. Otherwise the summarizer is called (section 5):
   - **update**, when a stored summary exists (it covers fewer messages): the request carries the stored summary's
     text (uncut) and only the summarized messages after the ones it covers;
   - **first summary**, when none is stored (none was ever written, or every attempt failed): the request carries
     all the summarized messages.
3. On success the stored summary becomes the reply's text with every run of whitespace replaced by one space and
   surrounding whitespace removed, covering the summarized part's number of messages; it is shown (4.5).
4. On a failed attempt (5.3) the summarizer is called again at once with the same request, up to 4 attempts in all.
   Only when all 4 fail does the summary line show the placeholder (3.5); the summarized messages are not shown, and
   nothing is stored (the previous stored summary, if any, stays). The next view of any reader of that conversation
   tries again, which may be another reader's view in the same round. A failure that persists therefore costs up to
   4 attempts per reader view.

At a round's start the views of the round's participants are built one after another in the round's turn order
(rounds.md, "3. Views: all from the market as the round starts"), before any participant's model call of that round;
the first view that needs a new summary makes the call, and the later views of that round reuse its result (or retry
after a failure). Within one view, the outcome must be as if the conversations that need a summary requested it one
after another, in the order of 3.1, each call's outcome and cost known before the next conversation is looked at:
this fixes the order of the log's summary records and where a cost-cap stop falls (5.3). How the calls are scheduled
is the implementer's choice.

Every participant in business takes every round's turn, so a conversation's summary is written at the start of the
round after the round in which the message that made its count k × m + 1 (for some whole m >= 1; k =
`messaging.recent`) was sent, unless that attempt fails (4.3 item 4), all its readers are out of business then, or
the episode plays no further round (no view is built after its last round, so that summary is never written).
A conversation whose readers are all out of business is not summarized while they are; its summary is written at the
next view of any of its readers (a manager back in business takes turns again: episode.md, "9.3 Coming back"),
covering the summarized part (4.1) as it then stands.

### 4.4 Hidden messages after a failure

When the summary is unavailable, the summarized messages are not shown in that view; only the summary line with the
placeholder and the messages shown in full appear. Example (a failed summary in mode `all`, from a test of the
reference implementation):

```
Conversation among all participants:
  Summary of the 20 earlier messages (written automatically, 8 of them new): (summary unavailable; the earlier messages are not shown)

  [day 1, round 1] M-f8c -> everyone (NEW): more 8
```

### 4.5 How a summary is shown

The stored text is shown cut to at most `cap` = `messaging.summary_chars` characters, on one line:

1. `text` = the stored text with every run of whitespace replaced by one space and surrounding whitespace removed.
2. If `text` has at most `cap` characters, show it.
3. Else let `cut` = the first `cap + 1` characters of `text`. Find every sentence end in `cut`: one of `.` `!` `?`
   `;`, optionally followed by one of `"` `'` `”` `’` `)`, where the next character in `cut` is a space (the match
   takes the closing character when there is one). Scan left to right without overlaps; let `end` = the position just
   after the last sentence end (the number of characters up to and including it), or 0 if there is none.
4. If `end` < floor(`cap` / 2): show the first `cap - 1` characters of `cut`, trailing whitespace removed, followed by
   `…` (U+2026).
5. Else, if the last sentence end's mark (`.` `!` `?` or `;`, before any closing character) is `;`: show the first
   `end` characters with that `;` replaced by `.` (a closing character after it is kept: `x;"` gives `x."`, `x;)`
   gives `x.)`).
6. Else show the first `end` characters.

Examples with cap 2000: 900 × `A. ` gives 2000 characters ending `A.`; 900 × `word ` gives a hard cut of 2000
characters ending `word…`; 1500 × `x`, `; `, 600 × `y` gives 1501 characters ending `x.`; 999 × `x`, `. `, 1500 × `y`
gives 1000 characters ending `x.`; 998 × `x`, `. `, 1500 × `y` gives a hard cut. A summarizer's bulleted layout is
therefore shown flattened on one line (`M-849: - Asked ... B-c53: - ...`).

## 5. The summarizer

### 5.1 The request

Two chat messages, a system message and a user message, and no functions.

The system message is the same in every mode:

```
You summarize message threads for participants in an online market for tasks. Buyers (IDs starting "B-") post tasks on a forum; managers (IDs starting "M-") bid prices on them, sealed, and a buyer who accepts a bid gets a contract with that manager. Managers produce results (labels like R-7x3) and deliver them, or submit them for the buyer's review on an escrow contract. A contract is plain (what the buyer pays, and when, is up to the buyer) or escrow (the price is held and released when the buyer approves, with disputes settled by an arbiter); an escrow bid names a quality threshold (t6 means 6 out of 10). Posts, bids and contracts have IDs such as post-0, bid-0 and contract-0. Your summary may be the only information about the messages you summarize that the conversation's readers see.
```

(one line, no trailing line break.)

Every user message opens with the conversation's **audience sentence**:

| conversation | `{audience}` |
|---|---|
| all managers | `This conversation is among all managers: every manager reads it, and no buyer does.` |
| all buyers | `This conversation is among all buyers: every buyer reads it, and no manager does.` |
| everyone | `This conversation is among all participants: every buyer and manager reads it.` |
| private | `This conversation is private to {A} and {B}: only they read it.` |

and names the conversation by its **label** `{conversation}`: `Conversation among all managers`, `Conversation among
all buyers`, `Conversation among all participants`, or for a private one `Conversation between {A} and {B}`. `{A}` and
`{B}` are the pair's IDs with a manager before a buyer, and two of one role in ascending code-point order:
`M-849 and M-f8c`, `B-c53 and B-d75`, `M-f8c and B-d75`.

Each message given to the summarizer is one **message line**:

```
[day {d}, round {r}] {sender ID} -> {to}: {text}
```

`{to}` is `all managers`, `all buyers`, `everyone` or the recipient's ID (never `you`); `{text}` is the recorded
text on one line, as the view shows it (3.3). `{lines}` below is the message lines, in the order the messages were sent (oldest first),
joined with one line break, with no line break after the last; for an update, they are the summarized messages after
the first P, in that order.

**First summary** (N messages; `{s}` = `s` unless N = 1; `{W}` = min(200, 40 + 15 × N)):

```
{audience} Summarize these messages. Name every participant by ID, never as "you" or "I", and write in the past tense (write "M-849 offered" or "M-849 bid", not "M-849 offers" or "M-849 is bidding"). Repeat the ID each time instead of "it", "its", "they" or "their", and do not quote a message's "you" or "your": name the participant it meant (write "under M-f8c's 13", not "under your 13").
First, list what is still in force from each participant, under a heading for each (such as "M-849:"): every offer, request, claim on a post, promise, refusal, price cap or floor, and agreement, with who made it and to whom. Where a later message changes one, give only the latest version. Then, if words remain, other facts (bids placed, results, payments), briefly.
Keep numbers, prices, thresholds, days and IDs exactly as written. Add no advice, opinion or interpretation, and nothing that is not in the messages. At most {W} words. Reply with the summary only.

{conversation} ({N} message{s}):
{lines}
```

**Update** (the stored summary covers P messages; K new messages; T = P + K; `{sP}`, `{sK}` = `s` unless the count is
1; `{W}` = min(200, 40 + 15 × T); `{prior}` = the stored summary's text, not cut to `messaging.summary_chars`):

```
{audience} Update the summary of a conversation. Below are the previous summary of its first {P} message{sP} and the {K} message{sK} that came after. Write one summary of all {T} messages, which will replace the previous one. Name every participant by ID, never as "you" or "I", and write in the past tense (write "M-849 offered" or "M-849 bid", not "M-849 offers" or "M-849 is bidding"). Repeat the ID each time instead of "it", "its", "they" or "their", and do not quote a message's "you" or "your": name the participant it meant (write "under M-f8c's 13", not "under your 13").
First, list what is still in force from each participant, under a heading for each (such as "M-849:"): every offer, request, claim on a post, promise, refusal, price cap or floor, and agreement, with who made it and to whom. Keep every item of the previous summary that the new messages do not change; where a new message changes or withdraws one, give only the latest version. Then, if words remain, other facts (bids placed, results, payments), briefly.
Keep numbers, prices, thresholds, days and IDs exactly as written. Add no advice, opinion or interpretation, and nothing that is not in the previous summary or the messages. At most {W} words. Reply with the summary only.

Previous summary:
{prior}

{conversation}, the new message{sK}:
{lines}
```

(`{T} messages` is always plural.) The word budget min(200, 40 + 15 n) is a fixed rule, not a parameter. At the
default `messaging.recent` = 10, a first summary normally covers 10 messages ("At most 190 words"; 20 or more after a
jump or failed attempts, "At most 200 words"), and every update covers 20 or more ("At most 200 words"). The IDs
and examples in the texts (`M-849`, `M-f8c`, `R-7x3`, `t6`, `post-0`, `bid-0`, `contract-0`) are fixed, not real
participants; the system message's IDs use the number 0 or an x, which the market never issues. Complete requests:
`examples/summarizer_request_first_all.json` and `examples/summarizer_request_update_all.json`.

### 5.2 The model call

The summarizer is `models.summarizer` (config.md, "3.4.2 `models`"), called through the same model layer as the
agents with its own temperature, token limit and provider fields, as models.md, "6. The summarizer's calls",
specifies. It is set up only when `message_channels` is not `off`.

### 5.3 Reading the reply, and failures

1. The reply text goes through strip-think, the same rule as for agents' replies (turns.md, "2. The conversation's
   messages"), which also removes surrounding whitespace.
2. If the provider reports the reply's finish reason as `length` or `content_filter`, the attempt fails.
3. If the text is empty, the attempt fails.
4. Otherwise the text is the summary (stored as 4.3 says).

An attempt also fails when the model call itself fails after the model layer's retries (models.md, "2.4 How a call
can end"). A failed attempt is handled as 4.3 says: it is retried at once, up to 4 attempts in all; when all fail the
placeholder is shown, nothing is stored, and the next view retries. Each failed attempt is recorded (and counted in
`summaries_failed`). A failed attempt never counts toward the lost-turn abort.

The cost cap is checked before each summarizer call, every attempt included (not under the mock, 5.5); if it is
reached, the call is not
made and the episode stops with status `aborted_cost_cap` (models.md, "7. The cost cap"). This is not a failed
attempt: no placeholder is shown.

### 5.4 Cost

The summarizer is never charged to any participant (no credits move). Each call's dollar cost, including a call whose
reply was then unusable, counts toward the cost cap and the episode's `summarizer_dollars` (models.md, "6. The
summarizer's calls"; running.md, "6.7 Core metrics").

### 5.5 Under the offline mock

When `models.summarizer` names the mock provider, or every participant's model is the mock, the summarizer makes no
call and costs nothing; its reply is

```
(mock summary of {n} messages)
```

with `{n}` the number of messages the summary covers (`INT`). It gets the `{you}` clause like any summary (3.5),
although it starts with `(`. No cost-cap check is made for it, since no call is made (when the participants' models are real, the cap is
checked at their next calls; models.md, "7. The cost cap"). The mock model itself is in running.md, "7. The offline
mock model".

The `(summary of {n} messages)` texts in 3.2's example and in `examples/summarizer_request_update_all.json` come from
a stand-in summarizer of a test capture, not from this mock.

## 6. What each mode changes in the system prompts (summary)

The system prompts' texts in each mode, and what each mode changes in them, are prompts.md's ("3.4 Message fields:
only in a template whose role can send messages" and "5. What each message_channels mode changes") and `text/`.

The prompts state `messaging.message_chars` and `messaging.recent` (sending roles only).
`messaging.summary_chars` and the summarizer's model are stated nowhere.

## 7. What the log must record

running.md, "6.3 The log: required content", owns the log's content. For messaging it requires the `send_message`
action and message records (E), every summary written and every failed attempt (F), and every summarizer call with
its full request and reply, tokens and cost (D and F). The messages themselves and every view are thereby
reconstructible.
