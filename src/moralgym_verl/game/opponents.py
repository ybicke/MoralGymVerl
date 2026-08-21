"""All scripted co-players: 2x2 opponents + PGG contribution policies.

2-player opponent strategies for the classic iterated games.

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
from typing import List, Optional


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


# ---------------------------------------------------------------------------
# Contribution policies (the scripted group)
# ---------------------------------------------------------------------------
# Signature: (own_history, agent_history, others_histories) -> 'C' | 'D'
# where own_history is this bot's own past moves, agent_history the LLM
# agent's, and others_histories the remaining N-2 bots' histories (self and
# agent excluded). From a bot's perspective its N-1 "others" are therefore
# the agent plus the other bots; k_prev counts contributions among them
# last round. Module `random` retained (as for the 2x2 opponents) so eval
# seeding and reward-side re-simulation stay synchronized.
#
# 2x2 analogs — exact at N=2, where others_histories == [] and the agent
# is the only other player (docs/pgg_design.md §3.6):
#   conditional_contributor == tit_for_tat     (parity anchor)
#   noisy_conditional       == noisy_tft       (same 0.2 noise)
#   free_rider              == always_defect   (eval-only probe)
#   full_contributor        == always_cooperate (eval-only probe)
#   random_contributor      == random


def _k_prev(
    agent_history: List[str], others_histories: List[List[str]]
) -> Optional[int]:
    """Contributions among this bot's N-1 others last round; None round 1."""
    if not agent_history:
        return None
    last = [agent_history[-1]] + [h[-1] for h in others_histories]
    return sum(1 for m in last if m == "C")


def conditional_contributor(
    own_history: List[str],
    agent_history: List[str],
    others_histories: List[List[str]],
) -> str:
    """Contribute first round, then iff a majority of the other players
    contributed last round: k_prev >= ceil((N-1)/2). At N=2 the threshold
    is 1 on the agent's last move — exactly tit_for_tat, including the
    cooperate-first opening. This is what reciprocity means in a commons:
    match the group, don't farm it."""
    k = _k_prev(agent_history, others_histories)
    if k is None:
        return "C"
    n_others = 1 + len(others_histories)
    return "C" if k >= (n_others + 1) // 2 else "D"


def noisy_conditional(
    own_history: List[str],
    agent_history: List[str],
    others_histories: List[List[str]],
    noise: float = 0.2,
) -> str:
    """conditional_contributor with the noisy_tft flip probability (0.2 —
    same episode-coverage rationale, see noisy_tft above)."""
    base = conditional_contributor(own_history, agent_history, others_histories)
    if random.random() < noise:
        return "D" if base == "C" else "C"
    return base


def free_rider(
    own_history: List[str],
    agent_history: List[str],
    others_histories: List[List[str]],
) -> str:
    return "D"


def full_contributor(
    own_history: List[str],
    agent_history: List[str],
    others_histories: List[List[str]],
) -> str:
    return "C"


def random_contributor(
    own_history: List[str],
    agent_history: List[str],
    others_histories: List[List[str]],
) -> str:
    return random.choice(["C", "D"])


CONTRIBUTION_REGISTRY = {
    "conditional_contributor": conditional_contributor,
    "noisy_conditional": noisy_conditional,
    "free_rider": free_rider,
    "full_contributor": full_contributor,
    "random_contributor": random_contributor,
    # Alias: the game-agnostic protocol presets (eval/config.py
    # PROTOCOL_PRESETS) name the stochastic co-player "random" for every
    # game; the registries are per-paradigm namespaces, so the shared
    # name maps to each paradigm's own implementation.
    "random": random_contributor,
}


def get_group_actions(
    opponent_type: str,
    agent_history: List[str],
    bots_histories: List[List[str]],
) -> List[str]:
    """One simultaneous move per scripted group member (N-1 bots).

    bots_histories holds each bot's own past moves, NOT yet updated with
    the current round — same convention as get_opponent_action above.
    Single draw point for the group: both episode.run_episode and the
    training reward_fn must call this (and nothing else) so rollout and
    re-simulation see the same draws for stochastic policies. Homogeneous
    groups in v1; mixed groups are a later config axis.
    """
    if opponent_type not in CONTRIBUTION_REGISTRY:
        raise ValueError(
            f"Unknown contribution policy: {opponent_type}. "
            f"Choose from {list(CONTRIBUTION_REGISTRY)}"
        )
    fn = CONTRIBUTION_REGISTRY[opponent_type]
    return [
        fn(bots_histories[i], agent_history,
           bots_histories[:i] + bots_histories[i + 1:])
        for i in range(len(bots_histories))
    ]
