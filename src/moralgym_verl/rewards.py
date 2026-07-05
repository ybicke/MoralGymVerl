"""Per-round reward functions for GRPO training.

Composite reward:
    r_t = R_game(a_t, a'_t) + lambda * R_intrinsic(a_t, a'_{t-1})

R_game variants:
    'raw':          Raw payoff points (Tennant et al. style).
    'normalized':   (pts - U_min) / (U_max - U_min), maps to [0, 1].
    'none':         Always 0. Use for intrinsic-only training (Tennant PART 3).
    'utilitarian':  Collective payoff (sum of both players' points, Tennant Ut).

R_intrinsic variants (backward-looking reciprocity, Tennant et al.):
    'deontological':  -1 if agent defects after opponent cooperated last round.
    'v1':  +1 for reciprocating cooperation, -1 for betraying it.
    'none':  0 always. Pure game reward.

Experiment mapping:
    T1 (game only):              game_reward='raw',    intrinsic='none'
    T2 (deontological + game):   game_reward='raw',    intrinsic='deontological', lambda=3.0
    T3 (deontological only):     game_reward='none',   intrinsic='deontological', lambda=3.0
    T4 (utilitarian):            game_reward='utilitarian', intrinsic='none'
    A1 (normalized, no intr.):   game_reward='normalized', intrinsic='none'
    A2 (normalized + deont.):    game_reward='normalized', intrinsic='deontological', lambda=X
"""

from __future__ import annotations

from typing import Dict, List

from moralgym_verl.game.environment import EpisodeConfig, get_score


# ---------------------------------------------------------------------------
# Intrinsic reward variants
# ---------------------------------------------------------------------------

def r_intrinsic_deontological(action: str, opp_prev_action: str) -> float:
    """Deontological penalty: -1 for defecting after opponent cooperated.

    Backward-looking reciprocity norm (Tennant et al.): penalises the agent
    for betraying an opponent who showed cooperation on the previous round.
    The caller must pass the opponent's *previous* action, not the current one.
    On round 1 with no history, the caller should skip the intrinsic reward.
    """
    if action == "D" and opp_prev_action == "C":
        return -1.0
    return 0.0


def r_intrinsic_v1(action: str, opp_prev_action: str) -> float:
    """V1 universalization: +1 reciprocate cooperation, -1 betray it.

    Like deontological but also rewards cooperating after the opponent
    cooperated. Both checks are against the opponent's *previous* action.
    """
    if action == "C" and opp_prev_action == "C":
        return 1.0
    if action == "D" and opp_prev_action == "C":
        return -1.0
    return 0.0


def r_intrinsic_none(action: str, opp_prev_action: str) -> float:
    """No intrinsic reward (pure game reward baseline)."""
    return 0.0


def r_intrinsic_deontological_tailored(
    action: str,
    fa: str | None,
    fo: str | None,
    game_type: str,
    shaping: dict,
) -> float:
    """Per-cell, per-game tailored deontological penalty.

    `shaping` is a dict keyed by game_type (or "_all" for cross-game rules).
    Each value is a list of rules: [{"a": ..., "fa": ..., "fo": ..., "penalty": ...}, ...].
    A rule fires when all specified fields match (omit or set to "*" for wildcard).
    fa or fo may be None (round 1 nohist) — rules that require specific values
    will not match in that case.

    Returns the sum of all matching rules' penalties.

    Example shaping dict:
        {
            "prisoners_dilemma": [{"a": "D", "fo": "C", "penalty": -1.0}],     # standard
            "stag_hunt":          [{"a": "D", "fa": "D", "fo": "C", "penalty": -1.0}],
            "chicken":            [{"a": "D", "fa": "C", "fo": "C", "penalty": -2.0}],
            "_all":               [{"a": "D", "fa": "D", "fo": "D", "penalty": -0.5}],  # spiral-exit
        }
    """
    rules = list(shaping.get(game_type, []))
    rules.extend(shaping.get("_all", []))

    total = 0.0
    for rule in rules:
        a_match = rule.get("a", "*")
        if a_match != "*" and a_match != action:
            continue
        fa_match = rule.get("fa", "*")
        if fa_match != "*":
            if fa is None or fa_match != fa:
                continue
        fo_match = rule.get("fo", "*")
        if fo_match != "*":
            if fo is None or fo_match != fo:
                continue
        total += float(rule.get("penalty", 0.0))
    return total


INTRINSIC_REGISTRY = {
    "deontological": r_intrinsic_deontological,
    "v1": r_intrinsic_v1,
    "none": r_intrinsic_none,
}


def get_intrinsic_fn(name: str):
    """Return the intrinsic reward function for the given name."""
    if name not in INTRINSIC_REGISTRY:
        raise ValueError(
            f"Unknown intrinsic reward: {name}. "
            f"Choose from {list(INTRINSIC_REGISTRY)}"
        )
    return INTRINSIC_REGISTRY[name]


# ---------------------------------------------------------------------------
# Game reward variants
# ---------------------------------------------------------------------------

def r_game_raw(
    action: str, opp_action: str, T: int, R: int, P: int, S: int
) -> float:
    """Raw game payoff (Tennant et al. style).

    Returns the actual points from the payoff matrix.
    Use for single-game experiments where scale doesn't matter.
    """
    pts, _ = get_score(action, opp_action, T, R, P, S)
    return float(pts)


def r_game_normalized(
    action: str, opp_action: str, T: int, R: int, P: int, S: int
) -> float:
    """Normalized game payoff in [0, 1].

    Maps raw payoff to (pts - U_min) / (U_max - U_min) where
    U_max = max(T, R) and U_min = min(P, S).
    Use for cross-game experiments (PD + Stag Hunt + Chicken).
    """
    pts, _ = get_score(action, opp_action, T, R, P, S)
    u_max = max(T, R)
    u_min = min(P, S)
    return (pts - u_min) / (u_max - u_min)


def r_game_none(
    action: str, opp_action: str, T: int, R: int, P: int, S: int
) -> float:
    """Zero game payoff — for intrinsic-only training (Tennant PART 3)."""
    return 0.0


def r_game_utilitarian(
    action: str, opp_action: str, T: int, R: int, P: int, S: int
) -> float:
    """Utilitarian: collective payoff (sum of both players' points).

    Matches Tennant's reward_fn_Ut. Against TFT, mutual cooperation
    maximises collective welfare (R+R > T+S = S+T > P+P for standard PD).
    """
    my_pts, opp_pts = get_score(action, opp_action, T, R, P, S)
    return float(my_pts + opp_pts)


GAME_REWARD_REGISTRY = {
    "raw": r_game_raw,
    "normalized": r_game_normalized,
    "none": r_game_none,
    "utilitarian": r_game_utilitarian,
}


def get_game_reward_fn(name: str):
    """Return the game reward function for the given name."""
    if name not in GAME_REWARD_REGISTRY:
        raise ValueError(
            f"Unknown game reward type: {name}. "
            f"Choose from {list(GAME_REWARD_REGISTRY)}"
        )
    return GAME_REWARD_REGISTRY[name]


# ---------------------------------------------------------------------------
# Composite per-round reward
# ---------------------------------------------------------------------------

def compute_round_reward(
    action: str,
    opp_action: str,
    config: EpisodeConfig,
    lambda_val: float = 0.0,
    intrinsic_type: str = "deontological",
    game_reward_type: str = "raw",
    opp_prev_action: str | None = None,
    agent_prev_action: str | None = None,
    shaping: dict | None = None,
) -> Dict[str, float]:
    """Compute per-round composite reward.

    r_t = R_game(a_t, a'_t) + lambda * R_intrinsic(a_t, a'_{t-1})

    Game reward uses the current opponent action. Intrinsic reward uses the
    opponent's *previous* action (backward-looking reciprocity, Tennant et al.).
    If opp_prev_action is None (round 1, no history), intrinsic reward is 0.

    Args:
        action: Agent's action ('C' or 'D').
        opp_action: Opponent's current action ('C' or 'D').
        config: Episode configuration with payoff values.
        lambda_val: Weight for intrinsic reward.
        intrinsic_type: 'deontological', 'v1', 'none', or 'deontological_tailored'.
        game_reward_type: 'raw', 'normalized', 'none', or 'utilitarian'.
        opp_prev_action: Opponent's action from the previous round, or None.
        agent_prev_action: Agent's own action from the previous round, or None.
            Required for 'deontological_tailored' rules that condition on fa.
        shaping: Per-game shaping dict for 'deontological_tailored'.
            See r_intrinsic_deontological_tailored docstring.
    """
    game_fn = get_game_reward_fn(game_reward_type)
    rg = game_fn(action, opp_action, config.T, config.R, config.P, config.S)

    if intrinsic_type == "deontological_tailored":
        ri = r_intrinsic_deontological_tailored(
            action, agent_prev_action, opp_prev_action,
            config.game_type, shaping or {},
        )
    elif opp_prev_action is not None:
        ri_fn = get_intrinsic_fn(intrinsic_type)
        ri = ri_fn(action, opp_prev_action)
    else:
        ri = 0.0

    return {
        "r_game": rg,
        "r_intrinsic": ri,
        "r_total": rg + lambda_val * ri,
    }


def compute_episode_rewards(
    actions: List[str],
    opp_actions: List[str],
    config: EpisodeConfig,
    lambda_val: float = 0.0,
    intrinsic_type: str = "deontological",
    game_reward_type: str = "raw",
    opp_prev_initial: str | None = None,
    agent_prev_initial: str | None = None,
    shaping: dict | None = None,
) -> Dict:
    """Compute rewards for a complete K-round episode.

    Intrinsic reward at round t uses opp_actions[t-1] (backward-looking).
    Round 0 uses opp_prev_initial if provided (e.g. fabricated opponent
    action from mid-game entry), otherwise intrinsic reward is 0.

    For 'deontological_tailored', agent_prev at round 0 comes from
    agent_prev_initial (fab_agent in hist mode, None in nohist).

    Returns:
        per_round: list of K dicts with {r_game, r_intrinsic, r_total}.
        r_game, r_intrinsic, r_total: trajectory-level averages.
    """
    per_round = [
        compute_round_reward(
            a, o, config, lambda_val, intrinsic_type, game_reward_type,
            opp_prev_action=opp_actions[i - 1] if i > 0 else opp_prev_initial,
            agent_prev_action=actions[i - 1] if i > 0 else agent_prev_initial,
            shaping=shaping,
        )
        for i, (a, o) in enumerate(zip(actions, opp_actions))
    ]
    K = len(per_round)
    if K == 0:
        # All rounds in this episode were illegal — no legal actions to aggregate.
        return {"per_round": [], "r_game": 0.0, "r_intrinsic": 0.0, "r_total": 0.0}
    return {
        "per_round": per_round,
        "r_game": sum(r["r_game"] for r in per_round) / K,
        "r_intrinsic": sum(r["r_intrinsic"] for r in per_round) / K,
        "r_total": sum(r["r_total"] for r in per_round) / K,
    }


# ---------------------------------------------------------------------------
# Lambda schedule (optional, for curriculum learning)
# ---------------------------------------------------------------------------

def get_lambda(
    step: int, total_steps: int, lambda_max: float = 0.3
) -> float:
    """3-phase lambda schedule: game-only -> ramp -> full alignment.

    Phase 1 (0-30%):   lambda = 0         (pure game competence)
    Phase 2 (30-60%):  linear ramp        (gradually introduce alignment)
    Phase 3 (60-100%): lambda = lambda_max (full prosocial pressure)
    """
    progress = step / max(total_steps, 1)
    if progress < 0.3:
        return 0.0
    elif progress < 0.6:
        return lambda_max * (progress - 0.3) / 0.3
    else:
        return lambda_max
