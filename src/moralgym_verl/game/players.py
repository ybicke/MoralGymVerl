"""Opponent strategies for iterated 2-player games.

All strategies follow the signature:
    (my_history, opp_history) -> 'C' or 'D'

where my_history is the bot's own past moves and opp_history is the
agent's (LLM's) past moves.  This convention lets opponents condition
on what the *agent* did.

League composition:
  Conditional:  tit_for_tat, pavlov, noisy_tft
  Adversarial:  always_defect, grim_trigger
  Cooperative:  always_cooperate
  Stochastic:   random
"""

from __future__ import annotations

import random
from typing import List


def tit_for_tat(my_history: List[str], opp_history: List[str]) -> str:
    """Cooperate first, then mirror opponent's last move."""
    return opp_history[-1] if opp_history else "C"


def always_defect(my_history: List[str], opp_history: List[str]) -> str:
    return "D"


def always_cooperate(my_history: List[str], opp_history: List[str]) -> str:
    return "C"


def random_bot(my_history: List[str], opp_history: List[str]) -> str:
    return random.choice(["C", "D"])


def noisy_tft(
    my_history: List[str], opp_history: List[str], noise: float = 0.2
) -> str:
    """Tit-for-Tat with noise: flips intended move with probability *noise*.

    Default 0.2 (literature midpoint). At 0.1 the noise signal is too sparse
    for short-horizon (5-6 round) training — only ~40% of episodes contain
    any flip event. 0.2 gives ~67% (5 rd) / 70% (6 rd) episode coverage while
    keeping the underlying TFT pattern recognizable (80% of moves still mirror).
    """
    base = tit_for_tat(my_history, opp_history)
    if random.random() < noise:
        return "D" if base == "C" else "C"
    return base


def grim_trigger(my_history: List[str], opp_history: List[str]) -> str:
    """Cooperate until opponent defects once, then always defect."""
    return "D" if "D" in opp_history else "C"


def pavlov(my_history: List[str], opp_history: List[str]) -> str:
    """Win-stay, lose-shift."""
    if not my_history:
        return "C"
    if my_history[-1] == opp_history[-1]:
        return my_history[-1]
    return "D" if my_history[-1] == "C" else "C"


OPPONENT_REGISTRY = {
    "tit_for_tat": tit_for_tat,
    "always_defect": always_defect,
    "always_cooperate": always_cooperate,
    "random": random_bot,
    "noisy_tft": noisy_tft,
    "grim_trigger": grim_trigger,
    "pavlov": pavlov,
}


def get_opponent_action(
    opponent_type: str, bot_history: List[str], agent_history: List[str]
) -> str:
    """Get the opponent's action given its type and game history."""
    if opponent_type not in OPPONENT_REGISTRY:
        raise ValueError(
            f"Unknown opponent: {opponent_type}. "
            f"Choose from {list(OPPONENT_REGISTRY)}"
        )
    return OPPONENT_REGISTRY[opponent_type](bot_history, agent_history)
