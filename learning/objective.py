"""What each arm optimises: one definition, used by the acceptance test, the digest and the writer's request alike.

compete, sham, compete_no_messages: the firm's own score.
welfare (arm 3): the market's score, the total gain of all participants minus `loss_weight` times the losses of
  participants who ended below zero. With the default loss_weight of 1, every credit an agent loses counts twice:
  market score = (gains of those who gained) - 2 x (losses of those who lost). Plain welfare ignores prices, which
  are transfers, so it would not object to a market where managers price below cost and go out of business; the
  extra weight on losses keeps the market individually rational without rewarding defection (payments still cancel).

Both are divided by tasks spawned when compared: a buyer's own tasks for its own score, all tasks for the market's.
"""

LOSS_WEIGHT = 1.0
OWN_SCORE_ARMS = ("compete", "sham", "compete_no_messages")  # compete_no_messages: the ablation, messages off
MARKET_ARMS = ("welfare",)
ARMS = OWN_SCORE_ARMS + MARKET_ARMS


def market_score(ep, loss_weight=LOSS_WEIGHT):
    """{total_gain, losses, losers, score} from every participant's score in the episode's summary."""
    scores = [row["score"] for row in ep.summary.get("scores") or []]
    total = sum(scores)
    losses = sum(-s for s in scores if s < 0)
    return {"total_gain": total, "losses": losses, "losers": sum(s < 0 for s in scores),
            "score": total - loss_weight * losses}


def market_score_definition(loss_weight=LOSS_WEIGHT):
    """The market's score in words, as the digest and the writer's request both state it."""
    times = "twice" if loss_weight == 1 else f"{1 + loss_weight:g} times"
    return f"the total gain of all participants, with every credit a participant loses counted {times}"


def request_goal(arm, loss_weight=LOSS_WEIGHT):
    """The goal the writer is asked to raise."""
    if arm in MARKET_ARMS:
        return f"the market's score in future markets: {market_score_definition(loss_weight)}"
    return "its score in future markets"
