"""Metric aggregation over completed eval trajectories.

Consumes TrajectoryResult lists from run_episode; produces the per-opponent
result blocks written to behavioral.json. Reward scoring constants live in
`scoring`, regret baselines in `baselines` — this module only aggregates.
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, Iterator, List, Tuple

import numpy as np

from moralgym_verl.eval.baselines import compute_regret
from moralgym_verl.eval.scoring import iter_decisions, iter_scored_decisions
from moralgym_verl.game.trajectory import TrajectoryResult

MORALITIES = ("game", "deon", "util", "gamedeon")


def _three_category(moves: List[str]) -> Dict:
    """Distribution over {C, D, illegal} plus sample size. Empty input ->
    all-zero probabilities with n=0, so callers can emit the key
    unconditionally and consumers gate on n > 0."""
    n = len(moves)
    if n == 0:
        return {"p_C": 0.0, "p_D": 0.0, "p_illegal": 0.0, "n": 0}
    return {
        "p_C": moves.count("C") / n,
        "p_D": moves.count("D") / n,
        "p_illegal": moves.count("illegal") / n,
        "n": n,
    }


def _iter_conditioned(
    result: TrajectoryResult,
) -> Iterator[Tuple[int, str, str, str]]:
    """Yield (round_idx, agent_prev, opp_prev, agent_move) for each round
    with a prior legal state (cold-start round 1 is skipped). The
    state-freeze convention lives in scoring.iter_decisions — the single
    implementation shared with reward scoring."""
    for d in iter_decisions(result):
        if d["agent_prev"] is not None and d["opp_prev"] is not None:
            yield d["round_idx"], d["agent_prev"], d["opp_prev"], d["agent_move"]


def _score_rewards(results: List[TrajectoryResult]) -> Dict:
    """Mean reward streams and regrets per morality, two versions each:
    primary (mean_r_*, regret_*) includes illegal decisions (r_m=-6),
    Tennant's scale (comparable to her Figure 5); legal-only (*_legal)
    isolates moral signal from parseability."""
    if not results:
        return {}

    game = results[0].config.game_type
    streams: Dict[str, List[float]] = {m: [] for m in MORALITIES}
    streams_legal: Dict[str, List[float]] = {m: [] for m in MORALITIES}

    for r in results:
        for decision in iter_scored_decisions(r):
            scores = decision["scores"]
            for m in MORALITIES:
                streams[m].append(scores[f"r_{m}"])
                if decision["agent_move"] in ("C", "D"):
                    streams_legal[m].append(scores[f"r_{m}"])

    out: Dict = {}
    for m in MORALITIES:
        mean = float(np.mean(streams[m])) if streams[m] else None
        out[f"mean_r_{m}"] = mean
        out[f"regret_{m}"] = (
            compute_regret(mean, game, m) if mean is not None else None
        )
        legal_values = streams_legal[m]
        mean_legal = float(np.mean(legal_values)) if legal_values else None
        out[f"mean_r_{m}_legal"] = mean_legal
        out[f"regret_{m}_legal"] = (
            compute_regret(mean_legal, game, m) if mean_legal is not None else None
        )
    return out


def aggregate_rollout_metrics(
    results: List[TrajectoryResult],
    opponent: str,
    num_episodes: int,
) -> Dict:
    """Aggregate metrics across completed rollout episodes.

    Illegal (parse-failure) decisions are a third category, never
    collapsed into D — that would conflate "chose to defect" with "failed
    to answer" (docs/teacher_signal_eval.md, aggregation row).
    """
    total_decisions = sum(len(r.agent_moves) for r in results)
    total_parse_failures = sum(r.parse_failures for r in results)
    parse_failure_rate = (
        total_parse_failures / total_decisions if total_decisions else 0.0
    )

    coop_rates = [r.cooperation_rate for r in results]
    mutual_coop_rates = [r.mutual_cooperation_rate for r in results]
    exploit_rates = [r.exploitation_rate for r in results]
    total_rewards = [r.rewards["r_total"] for r in results]

    # Sucker / mutual-defection: denominator is legal pairs, matching the
    # convention used by TrajectoryResult.mutual_cooperation_rate / exploitation_rate.
    sucker_rates: List[float] = []
    mutual_defection_rates: List[float] = []
    for r in results:
        legal_pairs = [
            (a, o) for a, o in zip(r.agent_moves, r.opponent_moves)
            if a in ("C", "D") and o in ("C", "D")
        ]
        denom = len(legal_pairs) or 1
        sucker_rates.append(
            sum(1 for a, o in legal_pairs if a == "C" and o == "D") / denom
        )
        mutual_defection_rates.append(
            sum(1 for a, o in legal_pairs if a == "D" and o == "D") / denom
        )

    # Conditional distributions: opponent's prev action, and full (agent, opp) state.
    moves_by_opp: Dict[str, List[str]] = {"C": [], "D": []}
    moves_by_state: Dict[str, List[str]] = {}
    # Per-round bucket of the same (a_prev, o_prev) -> moves mapping, used to
    # diagnose whether the Markov-1 rule is genuinely round-invariant.
    moves_by_round_state: Dict[int, Dict[str, List[str]]] = {}
    for r in results:
        for round_idx, a_prev, o_prev, move in _iter_conditioned(r):
            moves_by_opp[o_prev].append(move)
            state_key = f"({a_prev},{o_prev})"
            moves_by_state.setdefault(state_key, []).append(move)
            moves_by_round_state.setdefault(round_idx, {}) \
                                 .setdefault(state_key, []).append(move)

    cond_opp_c = _three_category(moves_by_opp["C"])
    cond_opp_d = _three_category(moves_by_opp["D"])
    state_conditioning = {
        key: _three_category(moves) for key, moves in sorted(moves_by_state.items())
    }
    per_round_state_conditioning = {
        f"round_{rnd}": {
            key: _three_category(moves) for key, moves in sorted(by_state.items())
        }
        for rnd, by_state in sorted(moves_by_round_state.items())
    }

    reward_block = _score_rewards(results)

    return {
        "opponent": opponent,
        "num_episodes": num_episodes,
        "parse_failure_rate": parse_failure_rate,
        "cooperation_rate": float(np.mean(coop_rates)),
        "cooperation_rate_std": float(np.std(coop_rates)),
        "mutual_cooperation_rate": float(np.mean(mutual_coop_rates)),
        "exploitation_rate": float(np.mean(exploit_rates)),
        "sucker_rate": float(np.mean(sucker_rates)),
        "mutual_defection_rate": float(np.mean(mutual_defection_rates)),
        "mean_reward": float(np.mean(total_rewards)),
        "mean_reward_std": float(np.std(total_rewards)),
        "cond_given_opp_c": cond_opp_c,
        "cond_given_opp_d": cond_opp_d,
        "state_conditioning": state_conditioning or None,
        "per_round_state_conditioning": per_round_state_conditioning or None,
        **reward_block,
    }


def per_round_breakdown(results: List[TrajectoryResult]) -> Dict:
    """Per-round move distributions ({C, D, illegal}, same three-category
    convention as everywhere — illegal NOT folded into D) and most common
    move sequences."""
    if not results:
        return {}

    num_rounds = len(results[0].agent_moves)
    round_moves: Dict[int, List[str]] = {r: [] for r in range(num_rounds)}
    sequences = []

    for traj in results:
        sequences.append("".join(traj.agent_moves))
        for r, move in enumerate(traj.agent_moves):
            round_moves[r].append(move)

    per_round = {
        f"round_{r + 1}": _three_category(round_moves[r])
        for r in range(num_rounds)
    }

    # Most common sequences
    seq_counts = Counter(sequences)
    total = len(sequences)
    top_sequences = [
        {"sequence": seq, "count": cnt, "fraction": cnt / total}
        for seq, cnt in seq_counts.most_common(5)
    ]

    return {"per_round": per_round, "top_sequences": top_sequences}
