"""Episode rollout: play K rounds of an iterated game and collect data.

Used both for:
  - Evaluation (LLM vs opponent, record metrics)
  - GRPO data generation (roll out G episodes, collect per-round rewards)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from moralgym_verl.game.environment import EpisodeConfig, get_score
from moralgym_verl.game.players import get_opponent_action
from moralgym_verl.game.prompts import (
    build_prompt, parse_action, parse_failure_feedback,
)
from moralgym_verl.rewards import compute_episode_rewards, compute_round_reward


@dataclass
class TrajectoryResult:
    """Complete record of one episode (one rollout)."""

    config: EpisodeConfig
    agent_moves: List[str]
    opponent_moves: List[str]
    per_round: List[Dict]
    rewards: Dict
    fab_agent: str | None = None
    fab_opp: str | None = None
    parse_failures: int = 0

    @property
    def cooperation_rate(self) -> float:
        legal = [m for m in self.agent_moves if m in ("C", "D")]
        if not legal:
            return 0.0
        return legal.count("C") / len(legal)

    @property
    def mutual_cooperation_rate(self) -> float:
        legal_pairs = [
            (a, o) for a, o in zip(self.agent_moves, self.opponent_moves)
            if a in ("C", "D") and o in ("C", "D")
        ]
        if not legal_pairs:
            return 0.0
        mc = sum(1 for a, o in legal_pairs if a == "C" and o == "C")
        return mc / len(legal_pairs)

    @property
    def exploitation_rate(self) -> float:
        legal_pairs = [
            (a, o) for a, o in zip(self.agent_moves, self.opponent_moves)
            if a in ("C", "D") and o in ("C", "D")
        ]
        if not legal_pairs:
            return 0.0
        ex = sum(1 for a, o in legal_pairs if a == "D" and o == "C")
        return ex / len(legal_pairs)


# Canonical order of the four fabricated (agent_prev, opp_prev) states.
# Balanced eval designs cycle through this list (episode i -> i % 4) so
# every state gets exactly num_episodes/4 decisions, deterministically.
FAB_STATES: List[Tuple[str, str]] = [
    ("C", "C"), ("C", "D"), ("D", "C"), ("D", "D"),
]


def run_episode(
    config: EpisodeConfig,
    policy_fn: Callable[[str], str],
    lambda_val: float = 0.0,
    intrinsic_type: str = "deontological",
    verbose: bool = False,
    fabricate_history: bool = False,
    fab_state: Tuple[str, str] | None = None,
    game_reward_type: str = "raw",
    shaping: dict | None = None,
) -> TrajectoryResult:
    """Play a full K-round episode and return trajectory + rewards.

    Args:
        config: Fully specified episode (game, opponent, presentation). All
            per-episode randomization (labels, matrix_layout, opener/closer
            prose, agent_is_row) is already baked in — drawn by the caller
            at EpisodeConfig construction time.
        policy_fn: callable(prompt) -> raw_response string.
        lambda_val: Weight for intrinsic reward.
        intrinsic_type: Which intrinsic reward variant to use.
        verbose: Print round-by-round results.
        fabricate_history: If True, seed round 1 with a fabricated prior
            state (mid-game entry). Matches training Game Design 2.
        fab_state: Explicit (agent_prev, opp_prev) for the fabricated
            state. None (default) samples uniformly from {C,D}x{C,D} via
            module `random` — training-parity behavior. The eval passes
            an explicit state to run a balanced design (see FAB_STATES).
        game_reward_type: Which game reward function to use for reward
            computation ('raw', 'normalized', 'none', 'utilitarian').
        shaping: Per-game shaping dict for intrinsic_type='deontological_tailored'.
            See rewards.r_intrinsic_deontological_tailored for schema.
    """
    fab_agent: str | None = None
    fab_opp: str | None = None

    if fabricate_history:
        if fab_state is not None:
            fab_agent, fab_opp = fab_state
        else:
            fab_agent = random.choice(["C", "D"])
            fab_opp = random.choice(["C", "D"])
        agent_history: List[str] = [fab_agent]
        opp_history: List[str] = [fab_opp]
    else:
        agent_history = []
        opp_history = []

    per_round: List[Dict] = []
    parse_failures = 0
    pending_feedback: str | None = None

    for rnd in range(config.num_rounds):
        prompt = build_prompt(config, agent_history, opp_history)
        if pending_feedback:
            # Training parity: after an illegal move, verl's interaction
            # prepends this exact string to the next user message
            # (training/game_interaction.py:generate_response).
            prompt = pending_feedback + "\n\n" + prompt
            pending_feedback = None

        raw = policy_fn(prompt)
        agent_move = parse_action(raw, config)

        if agent_move is None:
            # Illegal: state frozen — agent_history and opp_history not updated,
            # so next round's prompt shows the same history. No opponent move,
            # no payoff. Round counter advances. Matches training (nemo_env.py).
            parse_failures += 1
            pending_feedback = parse_failure_feedback(config)
            per_round.append(
                {
                    "round": rnd + 1,
                    "prompt": prompt,
                    "raw_response": raw,
                    "agent_move": "illegal",
                    "opp_move": None,
                    "agent_pts": None,
                    "opp_pts": None,
                }
            )
            if verbose:
                print(f"  R{rnd + 1:>2}: Agent=illegal (state frozen)")
            continue

        opp_move = get_opponent_action(
            config.opponent, opp_history, agent_history
        )

        agent_pts, opp_pts = get_score(
            agent_move, opp_move, config.T, config.R, config.P, config.S
        )

        agent_history.append(agent_move)
        opp_history.append(opp_move)

        per_round.append(
            {
                "round": rnd + 1,
                "prompt": prompt,
                "raw_response": raw,
                "agent_move": agent_move,
                "opp_move": opp_move,
                "agent_pts": agent_pts,
                "opp_pts": opp_pts,
            }
        )

        if verbose:
            opp_prev = opp_history[-2] if len(opp_history) >= 2 else None
            agent_prev = agent_history[-2] if len(agent_history) >= 2 else None
            rw = compute_round_reward(
                agent_move, opp_move, config, lambda_val, intrinsic_type,
                game_reward_type=game_reward_type,
                opp_prev_action=opp_prev,
                agent_prev_action=agent_prev,
                shaping=shaping,
            )
            print(
                f"  R{rnd + 1:>2}: Agent={agent_move}  Opp={opp_move}  "
                f"pts={agent_pts}/{opp_pts}  r={rw['r_total']:.3f}"
            )

    # agent_moves / opponent_moves: per-round trajectory record with illegal
    # markers preserved (length = num_rounds, supports per-round indexing).
    # Fabricated entries are not added to per_round, so no slicing needed.
    agent_moves_record = [pr["agent_move"] for pr in per_round]
    opp_moves_record = [pr["opp_move"] for pr in per_round]

    # Episode rewards computed over legal decisions only — agent_history /
    # opp_history already exclude illegal rounds (we didn't append on illegal).
    # Drop the fabricated entry (if any) so reward aggregation sees real play only.
    fab_offset = 1 if fabricate_history else 0
    legal_agent = agent_history[fab_offset:]
    legal_opp = opp_history[fab_offset:]
    rewards = compute_episode_rewards(
        legal_agent, legal_opp, config, lambda_val, intrinsic_type,
        game_reward_type=game_reward_type,
        opp_prev_initial=fab_opp,
        agent_prev_initial=fab_agent,
        shaping=shaping,
    )

    return TrajectoryResult(
        config=config,
        agent_moves=agent_moves_record,
        opponent_moves=opp_moves_record,
        per_round=per_round,
        rewards=rewards,
        fab_agent=fab_agent,
        fab_opp=fab_opp,
        parse_failures=parse_failures,
    )
