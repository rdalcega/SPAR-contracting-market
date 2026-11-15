"""Messaging (spec/messaging.md): the send_message action, the conversations a participant reads, the Messages
section of its view, and the summaries of a conversation's older messages with the summarizer's request.

A conversation's key is its group, `managers`, `buyers` or `everyone`, or for a private one the sorted pair of its
two participants' ids (4.2). A participant's read state is `seen`, the number of messages the market held when its
last view was built, and `unseen`, the earlier messages still new to it (3.4).
"""

import re
from dataclasses import asdict

from . import actions, models, state
from .fmt import INT, ONE_LINE, TEXT, unit
from .turns import strip_think

LABEL = {"managers": "all managers", "buyers": "all buyers", "everyone": "everyone"}  # 2.5, 3.3, 5.1
HEADING = {  # 3.1; also the summarizer's label of a group conversation (5.1)
    "managers": "Conversation among all managers",
    "buyers": "Conversation among all buyers",
    "everyone": "Conversation among all participants",
}
AUDIENCE = {  # 5.1
    "managers": "This conversation is among all managers: every manager reads it, and no buyer does.",
    "buyers": "This conversation is among all buyers: every buyer reads it, and no manager does.",
    "everyone": "This conversation is among all participants: every buyer and manager reads it.",
}
TITLE = "--- Messages"  # 3.2: how the section's title starts, in every mode
PLACEHOLDER = "(summary unavailable; the earlier messages are not shown)"  # 3.5
SENTENCE_END = re.compile("[.!?;][\"'\u201d\u2019)]?(?= )")  # 4.5, step 3
SYSTEM = (  # 5.1
    "You summarize message threads for participants in an online market for tasks. Buyers (IDs starting \"B-\") "
    "post tasks on a forum; managers (IDs starting \"M-\") bid prices on them, sealed, and a buyer who accepts a "
    "bid gets a contract with that manager. Managers produce results (labels like R-7x3) and deliver them, or "
    "submit them for the buyer's review on an escrow contract. A contract is plain (what the buyer pays, and "
    "when, is up to the buyer) or escrow (the price is held and released when the buyer approves, with disputes "
    "settled by an arbiter); an escrow bid names a quality threshold (t6 means 6 out of 10). Posts, bids and "
    "contracts have IDs such as post-0, bid-0 and contract-0. Your summary may be the only information about the "
    "messages you summarize that the conversation's readers see."
)
FIRST = (  # 5.1, "First summary"
    "{audience} Summarize these messages. Name every participant by ID, never as \"you\" or \"I\", and write in "
    "the past tense (write \"M-849 offered\" or \"M-849 bid\", not \"M-849 offers\" or \"M-849 is bidding\"). "
    "Repeat the ID each time instead of \"it\", \"its\", \"they\" or \"their\", and do not quote a message's "
    "\"you\" or \"your\": name the participant it meant (write \"under M-f8c's 13\", not \"under your 13\").\n"
    "First, list what is still in force from each participant, under a heading for each (such as \"M-849:\"): "
    "every offer, request, claim on a post, promise, refusal, price cap or floor, and agreement, with who made it "
    "and to whom. Where a later message changes one, give only the latest version. Then, if words remain, other "
    "facts (bids placed, results, payments), briefly.\n"
    "Keep numbers, prices, thresholds, days and IDs exactly as written. Add no advice, opinion or interpretation, "
    "and nothing that is not in the messages. At most {W} words. Reply with the summary only.\n"
    "\n"
    "{conversation} ({N} message{s}):\n"
    "{lines}"
)
UPDATE = (  # 5.1, "Update"
    "{audience} Update the summary of a conversation. Below are the previous summary of its first {P} "
    "message{sP} and the {K} message{sK} that came after. Write one summary of all {T} messages, which will "
    "replace the previous one. Name every participant by ID, never as \"you\" or \"I\", and write in the past "
    "tense (write \"M-849 offered\" or \"M-849 bid\", not \"M-849 offers\" or \"M-849 is bidding\"). Repeat the "
    "ID each time instead of \"it\", \"its\", \"they\" or \"their\", and do not quote a message's \"you\" or "
    "\"your\": name the participant it meant (write \"under M-f8c's 13\", not \"under your 13\").\n"
    "First, list what is still in force from each participant, under a heading for each (such as \"M-849:\"): "
    "every offer, request, claim on a post, promise, refusal, price cap or floor, and agreement, with who made it "
    "and to whom. Keep every item of the previous summary that the new messages do not change; where a new "
    "message changes or withdraws one, give only the latest version. Then, if words remain, other facts (bids "
    "placed, results, payments), briefly.\n"
    "Keep numbers, prices, thresholds, days and IDs exactly as written. Add no advice, opinion or interpretation, "
    "and nothing that is not in the previous summary or the messages. At most {W} words. Reply with the summary "
    "only.\n"
    "\n"
    "Previous summary:\n"
    "{prior}\n"
    "\n"
    "{conversation}, the new message{sK}:\n"
    "{lines}"
)


def _s(n):
    """`{s}`: the plural of `message` unless the count is 1 (5.1)."""
    return "" if n == 1 else "s"


# 2. send_message


def send(m, p, args, fixed):
    """send_message's own checks, in order, then the message is recorded (2.3 to 2.5)."""
    mode, role = m.cfg["message_channels"], p.role
    allowed = (f'one participant ID, "{role}s" (all the other {role}s) or "everyone" (all the other participants)'
               if mode == "all" else f'one {role} ID or "{role}s" (all the other {role}s)')
    to = args.get("to")
    if not actions.given(args, "to"):
        return False, actions.missing(args, "to")
    if not isinstance(to, str):
        return False, f"to must be {allowed}"
    if to in LABEL:
        if to != role + "s" and not (to == "everyone" and mode == "all"):
            return False, f'you cannot send to "{to}": to must be {allowed}'
    else:
        q, refusal = actions.participant(m, to)
        if q is None:
            return False, refusal
        if q.id == p.id:
            return False, "you cannot message yourself"
        if mode != "all" and q.role != role:
            return False, (f"{q.id} is a {q.role}: messages go only to other {role}s (to a {role} ID, or to "
                           f'"{role}s" for all of them)')
    if not actions.given(args, "text"):
        return False, actions.missing(args, "text")
    sent = TEXT(args["text"])
    if not sent.strip():
        return False, "text is blank; no message was sent"
    chars = m.cfg["messaging"]["message_chars"]
    m.messages.append(state.Message(m.day, m.round, p.id, to, sent[:chars]))
    state.emit(m, "message", sender=p.id, to=to, sent=sent, text=sent[:chars])
    cut = f" (cut to its first {unit(INT(chars), 'character')})" if len(sent) > chars else ""
    return True, f"message sent to {LABEL.get(to, to)}{cut}"


# 3. Conversations in the view


def conversations(m, p):
    """The keys of the conversations a participant reads, in the view's order; none for a role that cannot send
    (3.1)."""
    mode = m.cfg["message_channels"]
    if not actions.can_send(p.role, mode):
        return []
    roles = ("manager", "buyer") if mode == "all" else (p.role,)
    others = [pid for role in roles for pid in sorted(q.id for q in state.of_role(m, role)) if pid != p.id]
    groups = [p.role + "s"] + (["everyone"] if mode == "all" else [])
    return [tuple(sorted((p.id, pid))) for pid in others] + groups


def thread(m, key):
    """The indexes in the market's sequence of a conversation's messages, in the order sent (2.6, 3.1)."""
    if isinstance(key, str):
        return [i for i, msg in enumerate(m.messages) if msg.to == key]
    pair = (key, key[::-1])
    return [i for i, msg in enumerate(m.messages) if (msg.sender, msg.to) in pair]


def is_new(m, p, i):
    """Whether a message is new to a reader: someone else sent it, and no view of the reader has shown it (3.4)."""
    return m.messages[i].sender != p.id and (i >= p.seen or i in p.unseen)


def stamp(x):
    """The day and round a message was sent in, as its line opens (3.3, 5.1), or a payment made (view.md, 6)."""
    return f"[day {INT(x.day)}, round {INT(x.round)}]"


def section(m, p, summarizer, tracker):
    """The Messages section of a participant's view, as its title and its items, one per conversation (3.2 to 3.5).

    Building it obtains each summary it needs, in the conversations' order (4.3), and moves the reader's read
    state: it has then seen every message but the new ones behind a summary that could not be written (3.4).
    `summarizer` is the summarizer's backend, or None for the free mock summary (5.5); `tracker` is the run's cost
    tracker.
    """
    mode, recent, cap = m.cfg["message_channels"], m.cfg["messaging"]["recent"], m.cfg["messaging"]["summary_chars"]
    title = f"{TITLE} ---" if mode == "all" else f"{TITLE} between {p.role}s ---"
    items, hidden = [], []
    for key in conversations(m, p):
        if isinstance(key, str):
            heading = HEADING[key]
        else:
            heading = f"Your conversation with {key[1] if key[0] == p.id else key[0]}"
        ids = thread(m, key)
        if not ids:
            items.append(f"{heading}: (no messages yet)")
            continue
        cut = recent * ((len(ids) - 1) // recent)  # 4.1
        lines = []
        if cut:
            new = [i for i in ids[:cut] if is_new(m, p, i)]
            newcount = f", {INT(len(new))} of them new" if new else ""
            text = _summary(m, key, cut, summarizer, tracker)
            if text is None:
                hidden += new
                you, text = "", PLACEHOLDER
            else:
                you, text = f"; you are {p.id} in it", shown(text, cap)
            lines.append(f"  Summary of the {unit(INT(cut), 'earlier message')} (written automatically{newcount}"
                         f"{you}): {text}")
        for i in ids[cut:]:
            msg = m.messages[i]
            src = "you" if msg.sender == p.id else msg.sender
            dst = "you" if msg.to == p.id else LABEL.get(msg.to, msg.to)
            new = " (NEW)" if is_new(m, p, i) else ""
            lines.append(f"  {stamp(msg)} {src} -> {dst}{new}: {ONE_LINE(msg.text)}")
        items.append(f"{heading}:\n" + "\n\n".join(lines))
    p.seen, p.unseen = len(m.messages), hidden
    return title, items


# 4. Batching and summaries


def _summary(m, key, cut, summarizer, tracker):
    """The text of a conversation's summary of its first `cut` messages, written now unless the stored one covers
    them; None when all 4 attempts failed (4.3, 5.3 to 5.5)."""
    text, covers = m.summaries.get(key, (None, 0))
    if covers == cut:
        return text
    if summarizer is None:
        return _store(m, key, cut, f"(mock summary of {INT(cut)} messages)", 0.0)
    messages = request(m, key, covers, cut)
    record = {"conversation": key, "covers": cut, "messages": messages, "spec": m.cfg["models"]["summarizer"]}
    for _ in range(4):
        tracker.check()
        try:
            reply = summarizer.call(messages)
        except models.CallFailed as e:
            error, cost = e.error, 0.0
            state.emit(m, "summarizer_call", **record, error=error, failed_cost=e.failed_cost)
        else:
            text, cost = strip_think(reply.text), reply.cost
            cut_short = reply.finish_reason in ("length", "content_filter")
            error = f"finish reason {reply.finish_reason}" if cut_short else None if text else "empty reply"
            state.emit(m, "summarizer_call", **record, reply=asdict(reply))
            state.count_reply(m, "summarizer", reply)
            if error is None:
                return _store(m, key, cut, text, cost)
        state.count(m, "summaries_failed")
        state.emit(m, "summary_failed", conversation=key, covers=cut, error=error, cost=cost)
    return None


def _store(m, key, cut, text, cost):
    """A written summary becomes the conversation's stored summary (4.2; 4.3, item 3)."""
    text = " ".join(text.split())
    m.summaries[key] = (text, cut)
    state.count(m, "summaries_written")
    state.emit(m, "summary", conversation=key, covers=cut, text=text, cost=cost)
    return text


def shown(text, cap):
    """A stored summary as a view shows it: on one line, cut to at most `cap` characters at a sentence end (4.5)."""
    text = " ".join(text.split())
    if len(text) <= cap:
        return text
    cut = text[:cap + 1]
    ends = list(SENTENCE_END.finditer(cut))
    end = ends[-1].end() if ends else 0
    if end < cap // 2:
        return cut[:cap - 1].rstrip() + "\u2026"
    mark = ends[-1].start()
    return cut[:mark] + cut[mark:end].replace(";", ".")


# 5. The summarizer


def request(m, key, covers, cut):
    """The summarizer's two messages for a conversation's first `cut` messages: a first summary when `covers` is
    0, else an update of the stored summary, which covers the first `covers` (5.1)."""
    if isinstance(key, str):
        audience, label = AUDIENCE[key], HEADING[key]
    else:
        a, b = sorted(key, key=lambda pid: (m.participants[pid].role != "manager", pid))
        audience = f"This conversation is private to {a} and {b}: only they read it."
        label = f"Conversation between {a} and {b}"
    lines = "\n".join(f"{stamp(msg)} {msg.sender} -> {LABEL.get(msg.to, msg.to)}: {ONE_LINE(msg.text)}"
                      for msg in (m.messages[i] for i in thread(m, key)[covers:cut]))
    words = INT(min(200, 40 + 15 * cut))
    if covers:
        new = cut - covers
        user = UPDATE.format(audience=audience, P=INT(covers), sP=_s(covers), K=INT(new), sK=_s(new), T=INT(cut),
                             W=words, prior=m.summaries[key][0], conversation=label, lines=lines)
    else:
        user = FIRST.format(audience=audience, W=words, conversation=label, N=INT(cut), s=_s(cut), lines=lines)
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


actions.ACTIONS["send_message"] = send
