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
from moralgym_verl.game.pgg import (
    get_group_actions, get_score_pgg, group_payoff_pgg,
)
from moralgym_verl.game.players import get_opponent_action
from moralgym_verl.game.prompts import (
    build_env_message, build_prompt, parse_action, parse_failure_feedback,
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

    # public_goods only (None for 2x2 games): k_others per round (None on
    # illegal rounds) and the fabricated k_prev seed. opponent_moves is
    # all-None for PGG — there is no single opponent, so the pair-based
    # rate properties below correctly return None; use k_history and
    # per_round["others_moves"] instead.
    k_history: List[int | None] | None = None
    fab_k: int | None = None

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


# Canonical order of the four fabricated (agent_prev, opp_prev) states.
# Balanced eval designs cycle through this list (episode i -> i % 4) so
# every state gets exactly num_episodes/4 decisions, deterministically.
# The public_goods generalization ({C,D} x {0..N-1}, 2N states) is
# pgg.pgg_fab_states(n_players).
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
        fab_state: Explicit (agent_prev, opp_prev) for the fabricated
            state. None (default) samples uniformly from {C,D}x{C,D} via
            module `random` — training-parity behavior. The eval passes
            an explicit state to run a balanced design (see FAB_STATES).
            For public_goods the state is (own_prev, k_prev) with
            k_prev an int in 0..N-1 (see pgg.pgg_fab_states); uniform
            sampling covers the same 2N grid.
        game_reward_type: Which game reward function to use for reward
            computation ('raw', 'normalized', 'none', 'utilitarian').
        shaping: Per-game shaping dict for intrinsic_type='deontological_tailored'.
            See rewards.r_intrinsic_deontological_tailored for schema.
    """
    is_pgg = config.game_type == "public_goods"
    n_bots = config.n_players - 1 if is_pgg else 1

    fab_agent: str | None = None
    fab_opp: str | None = None
    fab_k: int | None = None
    # PGG group state: each bot's own move history, plus the k-history the
    # prompt builders consume (k per legal round, parallel to
    # agent_history, fabricated seed included).
    bots_histories: List[List[str]] = [[] for _ in range(n_bots)] if is_pgg else []
    k_history: List[int] = []

    if fabricate_history:
        if is_pgg:
            if fab_state is not None:
                fab_agent, fab_k = fab_state
            else:
                fab_agent = random.choice(["C", "D"])
                fab_k = random.randint(0, n_bots)
            # Which bots contributed is arbitrary (payoffs, prompts, and
            # homogeneous policies depend only on the count).
            bots_histories = [
                ["C" if i < fab_k else "D"] for i in range(n_bots)
            ]
        else:
            if fab_state is not None:
                fab_agent, fab_opp = fab_state
            else:
                fab_agent = random.choice(["C", "D"])
                fab_opp = random.choice(["C", "D"])
        agent_history: List[str] = [fab_agent]
        opp_history: List[str] = [fab_opp] if not is_pgg else []
        k_history = [fab_k] if is_pgg else []
    else:
        agent_history = []
        opp_history = []

    per_round: List[Dict] = []
    parse_failures = 0
    pending_feedback: str | None = None
    # Previous round's outcome for the env message; None after an illegal
    # round (state frozen, nothing to report). For PGG, prev_opp holds the
    # round's k_others (int) — the builders' PGG convention.
    prev_agent: str | None = None
    prev_opp = None

    for rnd in range(config.num_rounds):
        if rnd == 0:
            prompt = build_prompt(
                config, agent_history, k_history if is_pgg else opp_history
            )
        else:
            # Rounds >= 2: the rules (round-1 prompt) and all previous
            # rounds are already in the accumulated conversation — send
            # only the env message. Training parity:
            # game_interaction.generate_response builds the same message.
            prompt = build_env_message(
                config, prev_agent, prev_opp, round_idx=rnd + 1
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
            # Illegal: state frozen — agent_history and opp_history not updated,
            # so next round's prompt shows the same history. No opponent move,
            # no payoff. Round counter advances. Matches training (nemo_env.py).
            parse_failures += 1
            pending_feedback = parse_failure_feedback(config)
            prev_agent = prev_opp = None
            entry = {
                "round": rnd + 1,
                "prompt": prompt,
                "raw_response": raw,
                "agent_move": "illegal",
                "opp_move": None,
                "agent_pts": None,
                "opp_pts": None,
            }
            if is_pgg:
                entry.update(
                    {"k_others": None, "others_moves": None,
                     "group_payoff": None}
                )
            per_round.append(entry)
            if verbose:
                print(f"  R{rnd + 1:>2}: Agent=illegal (state frozen)")
            continue

        if is_pgg:
            # Simultaneous group draw: bot policies see histories BEFORE
            # this round, same convention as get_opponent_action below.
            bot_moves = get_group_actions(
                config.opponent, agent_history, bots_histories
            )
            k_others = sum(1 for m in bot_moves if m == "C")
            agent_pts = get_score_pgg(agent_move, k_others, config)
            m_total = k_others + (1 if agent_move == "C" else 0)
            group_pts = group_payoff_pgg(m_total, config)

            agent_history.append(agent_move)
            for hist, move in zip(bots_histories, bot_moves):
                hist.append(move)
            k_history.append(k_others)
            prev_agent, prev_opp = agent_move, k_others

            per_round.append(
                {
                    "round": rnd + 1,
                    "prompt": prompt,
                    "raw_response": raw,
                    "agent_move": agent_move,
                    "opp_move": None,
                    "agent_pts": agent_pts,
                    "opp_pts": None,
                    "k_others": k_others,
                    "others_moves": bot_moves,
                    "group_payoff": group_pts,
                }
            )
            if verbose:
                print(
                    f"  R{rnd + 1:>2}: Agent={agent_move}  "
                    f"k={k_others}/{n_bots}  pts={agent_pts}  "
                    f"group={group_pts}"
                )
            continue

        opp_move = get_opponent_action(
            config.opponent, opp_history, agent_history
        )

        agent_pts, opp_pts = get_score(
            agent_move, opp_move, config.T, config.R, config.P, config.S
        )

        agent_history.append(agent_move)
        opp_history.append(opp_move)
        prev_agent, prev_opp = agent_move, opp_move

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
    # For PGG the opponent side is the k-history (compute_episode_rewards'
    # PGG convention) and the fabricated seed is fab_k.
    fab_offset = 1 if fabricate_history else 0
    legal_agent = agent_history[fab_offset:]
    legal_opp = k_history[fab_offset:] if is_pgg else opp_history[fab_offset:]
    rewards = compute_episode_rewards(
        legal_agent, legal_opp, config, lambda_val, intrinsic_type,
        game_reward_type=game_reward_type,
        opp_prev_initial=fab_k if is_pgg else fab_opp,
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
        k_history=(
            [pr["k_others"] for pr in per_round] if is_pgg else None
        ),
        fab_k=fab_k,
    )
