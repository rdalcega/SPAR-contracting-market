"""The rules common to every action (spec/actions-buyer.md, 1.1 to 1.3) and compute as an action (turns.md, 7.6)."""

from .reading import has_text

OWN = {
    "buyer": (
        "post_task", "accept_bid", "test_delivery", "approve_submission", "reject_submission",
        "pay_escrow_arbiter", "end_contract", "edit_post", "withdraw_post", "keep_delivery",
    ),
    "manager": ("bid", "delegate_run", "run_myself", "test_result", "deliver", "dispute", "withdraw_bid"),
}
NOT_IN_THIS_MARKET = ("verify", "write_code", "self_test", "view_delivery", "note")
NO_NAME = (
    'the action names no action: give its name with "action", as {"action": "<action name>", '
    '"<argument name>": <value>}'
)
OUT_OF_BUSINESS = "you are out of business (balance at or below 0) and cannot act"
COMPUTE_AS_ACTION = (
    "the program did not run: compute is a function you call before act, not an action (at most 1 call a turn)"
)


def can_send(role, mode):
    """Whether the role has send_message in the mode (messaging.md, 1)."""
    return mode in ("within_roles", "all", role + "s")


def action_list(role, mode):
    """`{list}`, the actions a refusal shows (1.1)."""
    return ", ".join((("send_message",) if can_send(role, mode) else ()) + ("pay",) + OWN[role])


def name_refusal(name, role, mode, out_of_business):
    """The first refusal of 1.2, steps 1 to 6, that applies to an action's name, or None."""
    if not has_text(name):
        return NO_NAME
    other = "manager" if role == "buyer" else "buyer"
    sends = can_send(role, mode)
    if name in NOT_IN_THIS_MARKET or (name == "send_message" and mode == "off"):
        return f"'{name}' is not available in this market. Your actions: {action_list(role, mode)}."
    if name not in OWN[role] + OWN[other] + ("pay", "compute") + (("send_message",) if sends else ()):
        text = f"unknown action '{name}'. Your actions: {action_list(role, mode)}."
        key = name.strip().lower()
        if "contract" in key:
            text += " Contracts are created only when a buyer accepts a bid (accept_bid)."
        if role == "manager" and key.startswith("submit"):
            text += " To deliver a result, or submit it for the buyer's review, use deliver."
        return text
    if name in OWN[other]:
        return f"'{name}' is not available to a {role}: it is a {other}'s action"
    if out_of_business:
        return OUT_OF_BUSINESS
    return None


def shown(name):
    """`{shown}` of a result line (turns.md, 9): the name trimmed, `?` with no name."""
    return name.strip() if has_text(name) else "?"


def result_line(name, ok, message):
    """An action's line in its participant's results (1.3; turns.md, 9)."""
    return f"- {shown(name)}: {'OK' if ok else 'ERROR'}: {message}"
