"""Binary linear public-goods game: parameters, payoffs, states, group.

Everything that IS the N-player PGG lives here (docs/pgg_design.md); the
shared protocol machinery stays where it is and dispatches on
game_type == "public_goods": EpisodeConfig fields and validation in
environment.py, prompt wording in prompts.py, the episode loop in
trajectory.py, reward composition in rewards.py.

Game (§3.1): N players, endowment E, share s = r*E/N. Everyone
simultaneously either contributes their whole endowment (internal move
"C") or keeps it ("D"). With k = number of the OTHER N-1 players
contributing:

    contribute: s*(k+1)          keep: E + s*k

Social dilemma iff E/N < s < E — keeping dominates every round by the
constant margin E-s, yet full contribution (s*N each) beats full keeping
(E each). s > E is the compliance null (contributing dominant), s < E/N
the waste null (contributions destroy group value).

N=2 reduction (§3.2, the parity anchor): the binary PGG at N=2 is a PD
with T=E+s, R=2s, P=E, S=s, strict iff E/2 < s < E. tests/test_pgg.py
holds the PGG code path to the PD path under this mapping.

State (§3.4): history reaches the agent only as the COUNT k of others
contributing — no identities, no ordering (payoffs and prompts depend on
k alone), so the fabricated-history grid is {C,D} x {0..N-1}: 2N states,
not 2^N.
"""

from __future__ import annotations

import random
from typing import List, Optional, Tuple

from moralgym_verl.game.environment import EpisodeConfig

# "canonical" is the N=4 measurement cell (r=2, MPCR 0.5). "parity" is for
# the N=2 regression tests only: the canonical (E=10, s=5) sits exactly ON
# the N=2 strictness boundary (R = P = 10), so the tests carry their own
# (E, s) — see the 2026-08-20 design verification.
PGG_PARAMS = {
    "canonical": {"n_players": 4, "endowment": 10, "share": 5},
    "parity":    {"n_players": 2, "endowment": 10, "share": 6},
}


# ---------------------------------------------------------------------------
# Payoffs
# ---------------------------------------------------------------------------

def get_score_pgg(my_move: str, k_others: int, config: EpisodeConfig) -> int:
    """Agent's points for one round given k_others contributors among the
    N-1 others. Every payoff is an integer for integer (E, s) — r appears
    nowhere in scoring."""
    if my_move == "C":
        return config.share * (k_others + 1)
    return config.endowment + config.share * k_others


def group_payoff_pgg(m_contributors: int, config: EpisodeConfig) -> int:
    """Total group payoff with m contributors out of n_players:
    N*s*m + (N-m)*E. Equals the sum of every player's get_score_pgg; at
    N=2 it reduces to the 2x2 utilitarian sum (R+R / T+S / P+P)."""
    n, E, s = config.n_players, config.endowment, config.share
    return n * s * m_contributors + (n - m_contributors) * E


# ---------------------------------------------------------------------------
# Fabricated-history states
# ---------------------------------------------------------------------------

def pgg_fab_states(n_players: int) -> List[Tuple[str, int]]:
    """Canonical order of the 2N fabricated (own_prev, k_prev) states —
    the N-player generalization of trajectory.FAB_STATES. Balanced eval
    designs cycle this list (episode i -> i % 2N), so the invariant
    becomes num_episodes % (2 * n_players) == 0. A function of N rather
    than a constant; at N=2 it maps 1:1 onto FAB_STATES via
    k_prev=1 <-> opp_prev="C"."""
    return [(own, k) for own in ("C", "D") for k in range(n_players)]


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
    same episode-coverage rationale, see players.noisy_tft)."""
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
}


def get_group_actions(
    opponent_type: str,
    agent_history: List[str],
    bots_histories: List[List[str]],
) -> List[str]:
    """One simultaneous move per scripted group member (N-1 bots).

    bots_histories holds each bot's own past moves, NOT yet updated with
    the current round — same convention as players.get_opponent_action.
    Single draw point for the group: both trajectory.run_episode and the
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
