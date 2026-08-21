"""Episode rollout: play K rounds of an iterated game and collect data.

Used both for:
  - Evaluation (LLM vs opponent, record metrics)
  - GRPO data generation (roll out G episodes, collect per-round rewards)

The loop is game-agnostic: everything paradigm-specific (opponent draws,
scoring, fabricated states, record fields) is delegated to the Game
implementation resolved via registry.get_game(config.game_type). The
opponent-side observations flow through opaquely (move strs for classic
games, k_others ints for public_goods).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

# Re-exported from classic_games.py (its home) — eval code imports
# FAB_STATES from here.
from moralgym_verl.game.classic_games import FAB_STATES as FAB_STATES
from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.prompts import (
    build_env_message, build_prompt, parse_action, parse_failure_feedback,
)
from moralgym_verl.game.registry import get_game
from moralgym_verl.rewards import compute_episode_rewards


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

    # public_goods only (None for 2x2 games): k_others per round (None on
    # illegal rounds) and the fabricated k_prev seed. opponent_moves is
    # all-None for PGG — there is no single opponent, so the pair-based
    # rate properties below correctly return None; use k_history and
    # per_round["others_moves"] instead.
    k_history: List[int | None] | None = None
    fab_k: int | None = None

    @property
    def fab_obs(self):
        """Fabricated opponent observation, game-agnostic: fab_opp (move
        str) for 2x2 games, fab_k (int) for public_goods, None when the
        episode had no fabricated history."""
        return self.fab_opp if self.fab_opp is not None else self.fab_k

    # Rate properties return None (not 0.0) when the episode has no legal
    # decisions: an all-illegal episode carries no evidence about the
    # policy, and 0.0 would read as "always defected" — aggregators must
    # exclude None episodes rather than average them in.

    @property
    def cooperation_rate(self) -> float | None:
        legal = [m for m in self.agent_moves if m in ("C", "D")]
        if not legal:
            return None
        return legal.count("C") / len(legal)

    @property
    def legal_pairs(self) -> List[Tuple[str, str]]:
        """(agent, opponent) move pairs for rounds where both are legal."""
        return [
            (a, o) for a, o in zip(self.agent_moves, self.opponent_moves)
            if a in ("C", "D") and o in ("C", "D")
        ]

    @property
    def mutual_cooperation_rate(self) -> float | None:
        pairs = self.legal_pairs
        if not pairs:
            return None
        return sum(1 for a, o in pairs if a == "C" and o == "C") / len(pairs)

    @property
    def exploitation_rate(self) -> float | None:
        pairs = self.legal_pairs
        if not pairs:
            return None
        return sum(1 for a, o in pairs if a == "D" and o == "C") / len(pairs)


def run_episode(
    config: EpisodeConfig,
    policy_fn: Callable[[str], str],
    lambda_val: float = 0.0,
    intrinsic_type: str = "deontological",
    verbose: bool = False,
    fabricate_history: bool = False,
    fab_state: Tuple | None = None,
    game_reward_type: str = "raw",
    shaping: dict | None = None,
) -> TrajectoryResult:
    """Play a full K-round episode and return trajectory + rewards.

    Args:
        config: Fully specified episode (game, opponent, presentation). All
            per-episode randomization (labels, matrix_layout, opener/closer
            prose, agent_is_row) is already baked in — drawn by the caller
            at EpisodeConfig construction time.
        policy_fn: callable(prompt) -> raw_response string. For
            num_rounds > 1 it MUST accumulate the episode conversation
            (make_chat_policy_fn): multi-round episodes are conversations
            (verl multi-turn parity), and rounds >= 2 receive only the
            build_env_message outcome message — the rules and history live
            in the accumulated dialogue. Single-round episodes work with
            any policy_fn.
        lambda_val: Weight for intrinsic reward.
        intrinsic_type: Which intrinsic reward variant to use.
        verbose: Print round-by-round results.
        fabricate_history: If True, seed round 1 with a fabricated prior
            state (mid-game entry). Matches training Game Design 2.
        fab_state: Explicit (agent_prev, obs_prev) for the fabricated
            state — (own, opp_move) for 2x2 games (see FAB_STATES),
            (own, k_prev) for public_goods (see pgg.pgg_fab_states).
            None (default) samples uniformly via module `random`
            (training-parity behavior); the eval passes an explicit state
            to run a balanced design.
        game_reward_type: Which game reward function to use for reward
            computation ('raw', 'normalized', 'none', 'utilitarian').
        shaping: Per-game shaping dict for intrinsic_type='deontological_tailored'.
            See rewards.r_intrinsic_deontological_tailored for schema.
    """
    game = get_game(config.game_type)

    fab_agent: str | None = None
    fab_obs = None

    if fabricate_history:
        if fab_state is not None:
            fab_agent, fab_obs = fab_state
        else:
            fab_agent, fab_obs = game.sample_fab_state(config)
        agent_history: List[str] = [fab_agent]
    else:
        agent_history = []
    opp_side = game.make_opponents(
        config, fab_obs if fabricate_history else None
    )

    per_round: List[Dict] = []
    parse_failures = 0
    pending_feedback: str | None = None
    # Previous round's outcome for the env message; None after an illegal
    # round (state frozen, nothing to report).
    prev_agent: str | None = None
    prev_obs = None

    for rnd in range(config.num_rounds):
        if rnd == 0:
            prompt = build_prompt(config, agent_history, opp_side.observations)
        else:
            # Rounds >= 2: the rules (round-1 prompt) and all previous
            # rounds are already in the accumulated conversation — send
            # only the env message. Training parity:
            # game_interaction.generate_response builds the same message.
            prompt = build_env_message(
                config, prev_agent, prev_obs, round_idx=rnd + 1
            )
        if pending_feedback:
            # Training parity: after an illegal move, verl's interaction
            # prepends this exact string to the next user message
            # (training/game_interaction.py:generate_response).
            prompt = pending_feedback + "\n\n" + prompt
            pending_feedback = None

        raw = policy_fn(prompt)
        agent_move = parse_action(raw, config)

        if agent_move is None:
            # Illegal: state frozen — histories not updated, so next
            # round's prompt shows the same history. No opponent draw,
            # no payoff. Round counter advances. Matches training (nemo_env.py).
            parse_failures += 1
            pending_feedback = parse_failure_feedback(config)
            prev_agent = prev_obs = None
            per_round.append(
                {
                    "round": rnd + 1,
                    "prompt": prompt,
                    "raw_response": raw,
                    "agent_move": "illegal",
                    "opp_move": None,
                    "agent_pts": None,
                    "opp_pts": None,
                    **game.illegal_extras(),
                }
            )
            if verbose:
                print(f"  R{rnd + 1:>2}: Agent=illegal (state frozen)")
            continue

        # Simultaneous draw: the opponent side sees histories BEFORE this
        # round (the agent's current move is not yet appended).
        obs = opp_side.draw(agent_history)
        agent_pts = game.score(config, agent_move, obs)

        agent_history.append(agent_move)
        opp_side.advance(obs)
        prev_agent, prev_obs = agent_move, obs

        per_round.append(
            {
                "round": rnd + 1,
                "prompt": prompt,
                "raw_response": raw,
                "agent_move": agent_move,
                "agent_pts": agent_pts,
                **game.record_extras(config, agent_move, obs, opp_side),
            }
        )

        if verbose:
            print(game.verbose_line(
                config, rnd, agent_move, obs, agent_pts,
                agent_history, opp_side.observations,
                lambda_val, intrinsic_type, game_reward_type, shaping,
            ))

    # agent_moves / opponent_moves: per-round trajectory record with illegal
    # markers preserved (length = num_rounds, supports per-round indexing).
    # Fabricated entries are not added to per_round, so no slicing needed.
    agent_moves_record = [pr["agent_move"] for pr in per_round]
    opp_moves_record = [pr["opp_move"] for pr in per_round]

    # Episode rewards computed over legal decisions only — histories
    # already exclude illegal rounds (nothing appended on illegal).
    # Drop the fabricated entry (if any) so reward aggregation sees real
    # play only.
    fab_offset = 1 if fabricate_history else 0
    legal_agent = agent_history[fab_offset:]
    legal_obs = opp_side.observations[fab_offset:]
    rewards = compute_episode_rewards(
        legal_agent, legal_obs, config, lambda_val, intrinsic_type,
        game_reward_type=game_reward_type,
        opp_prev_initial=fab_obs,
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
        parse_failures=parse_failures,
        **game.result_extras(fab_obs, per_round),
    )
