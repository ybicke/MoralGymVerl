"""Metric aggregation over completed eval trajectories.

Consumes TrajectoryResult lists from run_episode; produces the per-opponent
result blocks written to behavioral.json. Reward scoring constants and
regret baselines live in `scoring` — this module only aggregates.
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, Iterator, List, Tuple

import numpy as np

from moralgym_verl.eval.scoring import (
    compute_regret, iter_decisions, iter_scored_decisions,
)
from moralgym_verl.game.episode import TrajectoryResult
from moralgym_verl.game.registry import get_game

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

    config = results[0].config
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
            compute_regret(mean, config, m) if mean is not None else None
        )
        legal_values = streams_legal[m]
        mean_legal = float(np.mean(legal_values)) if legal_values else None
        out[f"mean_r_{m}_legal"] = mean_legal
        out[f"regret_{m}_legal"] = (
            compute_regret(mean_legal, config, m) if mean_legal is not None else None
        )
    return out


def _pgg_block(results: List[TrajectoryResult]) -> Dict:
    """Public-goods metric vocabulary (docs/pgg_design.md §4), emitted
    under the "pgg" key — the pair-based 2x2 metrics stay None for PGG
    and these names must not collide with them.

    Headline: k_slope within own_prev — the change of P(C) per unit of
    k_prev, fitted separately for own_prev C and D. Pooled P(C) is
    confounded by the own-move anti-persistence artifact, so conditional
    claims come from these slopes, never from the pooled rate.

    sucker/freeride come in two variants: outcome-based (this round's
    realized k_others — the true generalization of the 2x2 pair stats)
    and _kprev (decision-based, conditioned on the prior state, per the
    design doc's original definitions). Denominators: legal decisions
    (outcome) / legal decisions with a prior state (_kprev).
    """
    config = results[0].config
    game = get_game(config.game_type)
    n_others = config.n_players - 1
    max_social = game.max_social_payoff(config)

    curve: Dict[str, Dict[int, List[str]]] = {}
    n_legal = 0
    n_legal_with_state = 0
    social_sum = 0
    sucker = freeride_w = 0.0
    sucker_kprev = freeride_kprev_w = 0.0

    for r in results:
        for d in iter_decisions(r):
            move = d["agent_move"]
            if d["agent_prev"] is not None and d["opp_prev"] is not None:
                curve.setdefault(d["agent_prev"], {}) \
                     .setdefault(d["opp_prev"], []).append(move)
            if move not in ("C", "D"):
                continue
            n_legal += 1
            social_sum += d["social_payoff"]
            k_now = d["obs"]
            if move == "C" and k_now == 0:
                sucker += 1
            if move == "D":
                freeride_w += k_now / n_others
            if d["opp_prev"] is not None:
                n_legal_with_state += 1
                if move == "C" and d["opp_prev"] == 0:
                    sucker_kprev += 1
                if move == "D":
                    freeride_kprev_w += d["opp_prev"] / n_others

    curve_out = {
        own: {str(k): _three_category(moves)
              for k, moves in sorted(by_k.items())}
        for own, by_k in sorted(curve.items())
    }

    # Slope of P(C) over k within fixed own_prev: OLS over the k-cells
    # with data; None with fewer than two cells. Uses the three-category
    # p_C (illegal in the denominator) — consistent with every other
    # rate in this module.
    k_slope: Dict[str, float | None] = {}
    for own, by_k in sorted(curve.items()):
        ks = [k for k, moves in sorted(by_k.items()) if moves]
        if len(ks) < 2:
            k_slope[own] = None
            continue
        p_cs = [_three_category(by_k[k])["p_C"] for k in ks]
        k_slope[own] = float(np.polyfit(ks, p_cs, 1)[0])

    return {
        "cond_contribution_curve": curve_out or None,
        "k_slope": k_slope or None,
        "group_efficiency": (
            (social_sum / n_legal) / max_social if n_legal else None
        ),
        "sucker_rate": sucker / n_legal if n_legal else None,
        "freeride_on_contributors": (
            freeride_w / n_legal if n_legal else None
        ),
        "sucker_rate_kprev": (
            sucker_kprev / n_legal_with_state
            if n_legal_with_state else None
        ),
        "freeride_on_contributors_kprev": (
            freeride_kprev_w / n_legal_with_state
            if n_legal_with_state else None
        ),
        "max_social_payoff": max_social,
    }


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

    # Episodes with no legal decision carry no evidence about the policy:
    # their rate properties are None and they are EXCLUDED from the means
    # below (not averaged in as 0.0, which would read as "always
    # defected"). The exclusion counts are reported so missing data is
    # visible next to every headline rate.
    coop_rates = [r.cooperation_rate for r in results
                  if r.cooperation_rate is not None]
    mutual_coop_rates = [r.mutual_cooperation_rate for r in results
                         if r.mutual_cooperation_rate is not None]
    exploit_rates = [r.exploitation_rate for r in results
                     if r.exploitation_rate is not None]
    total_rewards = [r.rewards["r_total"] for r in results]
    num_all_illegal = len(results) - len(coop_rates)
    num_no_legal_pairs = len(results) - len(mutual_coop_rates)

    # Sucker / mutual-defection: denominator is legal pairs, matching the
    # convention used by TrajectoryResult.mutual_cooperation_rate /
    # exploitation_rate — including the skip of no-legal-pair episodes.
    sucker_rates: List[float] = []
    mutual_defection_rates: List[float] = []
    for r in results:
        pairs = r.legal_pairs
        if not pairs:
            continue
        sucker_rates.append(
            sum(1 for a, o in pairs if a == "C" and o == "D") / len(pairs)
        )
        mutual_defection_rates.append(
            sum(1 for a, o in pairs if a == "D" and o == "D") / len(pairs)
        )

    # Conditional distributions: opponent's prev observation, and full
    # (agent, obs) state. Keyed generically (obs is a move str for 2x2,
    # k_prev int for PGG) — state keys render as "(C,D)" / "(C,2)".
    moves_by_opp: Dict = {}
    moves_by_state: Dict[str, List[str]] = {}
    # Per-round bucket of the same (a_prev, o_prev) -> moves mapping, used to
    # diagnose whether the Markov-1 rule is genuinely round-invariant.
    moves_by_round_state: Dict[int, Dict[str, List[str]]] = {}
    for r in results:
        for round_idx, a_prev, o_prev, move in _iter_conditioned(r):
            moves_by_opp.setdefault(o_prev, []).append(move)
            state_key = f"({a_prev},{o_prev})"
            moves_by_state.setdefault(state_key, []).append(move)
            moves_by_round_state.setdefault(round_idx, {}) \
                                 .setdefault(state_key, []).append(move)

    # 2x2 legacy keys (empty three-category blocks for PGG, where the
    # observation is k and the conditioning lives in the "pgg" block).
    cond_opp_c = _three_category(moves_by_opp.get("C", []))
    cond_opp_d = _three_category(moves_by_opp.get("D", []))
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
    pgg_block = (
        _pgg_block(results)
        if results and results[0].config.game_type == "public_goods"
        else None
    )

    def _mean(xs: List[float]) -> float | None:
        return float(np.mean(xs)) if xs else None

    def _std(xs: List[float]) -> float | None:
        return float(np.std(xs)) if xs else None

    return {
        "opponent": opponent,
        "num_episodes": num_episodes,
        "parse_failure_rate": parse_failure_rate,
        # Rates are None (JSON null) when EVERY episode lacked legal data.
        "num_episodes_all_illegal": num_all_illegal,
        "num_episodes_no_legal_pairs": num_no_legal_pairs,
        "cooperation_rate": _mean(coop_rates),
        "cooperation_rate_std": _std(coop_rates),
        "mutual_cooperation_rate": _mean(mutual_coop_rates),
        "exploitation_rate": _mean(exploit_rates),
        "sucker_rate": _mean(sucker_rates),
        "mutual_defection_rate": _mean(mutual_defection_rates),
        "mean_reward": _mean(total_rewards),
        "mean_reward_std": _std(total_rewards),
        "cond_given_opp_c": cond_opp_c,
        "cond_given_opp_d": cond_opp_d,
        "state_conditioning": state_conditioning or None,
        "per_round_state_conditioning": per_round_state_conditioning or None,
        # Public-goods vocabulary (None for 2x2 games) — see _pgg_block.
        # The one game-type gate in this module: reporting vocabulary,
        # not scoring logic (which is game-blind via scoring.py).
        "pgg": pgg_block,
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
