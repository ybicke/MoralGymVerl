"""verl reward function for MoralGym game-theoretic fine-tuning.

Interface required by verl's custom_reward_function config:
    compute_score(data_source, solution_str, ground_truth, extra_info=None) -> dict

ground_truth is a JSON string encoding the full game state for one round
(see dataset.py for how it is constructed).

The returned dict has:
  "score":    float  — game reward + lambda * intrinsic reward
  "feedback": str    — moral critique for SDPO teacher reprompting
                       (empty when SDPO is not used)

Usage in verl config:
    custom_reward_function:
      path: /users/<user>/MoralGymVerl/src/moralgym_verl/training/reward_fn.py
      name: compute_score
"""

from __future__ import annotations

import json
from typing import Any

from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.moral_values import get_moral_value
from moralgym_verl.game.opponents import get_opponent_action
from moralgym_verl.game.prompts import parse_action
from moralgym_verl.rewards import (
    get_game_reward_fn,
    get_intrinsic_fn,
    r_intrinsic_deontological_tailored,
)


def compute_score(
    data_source: str,
    solution_str: str,
    ground_truth: str,
    extra_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute game reward for a single model response.

    Args:
        data_source: Must be "moralgym" — other values raise ValueError.
        solution_str: The model's raw response string (one action token ±
            chain-of-thought).
        ground_truth: JSON string produced by dataset.py encoding the
            EpisodeConfig and current round state.
        extra_info: Optional extra metadata (not used for reward computation).

    Returns:
        dict with keys:
          "score"    — scalar reward (float)
          "feedback" — moral critique string for SDPO teacher (str)
    """
    if data_source != "moralgym":
        raise ValueError(f"Unexpected data_source '{data_source}' for moralgym reward fn")

    state = json.loads(ground_truth)
    config = _config_from_state(state)

    action = parse_action(solution_str, config)

    if action is None:
        feedback = (
            "Your response could not be parsed as a valid action. "
            f"You must output exactly '{config.coop_label}' or '{config.defect_label}'."
        )
        return {
            "score": float(state["illegal_penalty"]),
            "feedback": feedback,
            **_game_metrics(None, state),
        }

    opp_action = get_opponent_action(
        config.opponent,
        bot_history=state["opp_history"],
        agent_history=state["agent_history"],
    )

    game_fn = get_game_reward_fn(state["game_reward"])
    r_game = game_fn(action, opp_action, config.T, config.R, config.P, config.S)

    intrinsic = state["intrinsic"]
    timing = state.get("intrinsic_timing", "backward")
    lambda_val = float(state["lambda_val"])

    if intrinsic == "none":
        r_intr = 0.0
    elif intrinsic == "deontological_tailored":
        fa = state["agent_history"][-1] if state["agent_history"] else None
        fo = state["opp_history"][-1] if state["opp_history"] else "C"
        r_intr = r_intrinsic_deontological_tailored(
            action, fa, fo, config.game_type, state.get("shaping", {})
        )
    elif timing == "current":
        r_intr = get_intrinsic_fn(intrinsic)(action, opp_action)
    else:
        opp_prev = state["opp_history"][-1] if state["opp_history"] else "C"
        r_intr = get_intrinsic_fn(intrinsic)(action, opp_prev)

    score = r_game + lambda_val * r_intr
    if state.get("feedback_mode") == "principle":
        # Teacher context = the screened moral-principle text, verbatim —
        # the same string the single-turn screen validated as the teacher
        # signal. No outcome critique, so the steer is attributable to the
        # wording alone.
        feedback = get_moral_value(state["moral_value"])
    else:
        feedback = _build_feedback(action, opp_action, score, r_game, r_intr, state, config)
    return {"score": score, "feedback": feedback, **_game_metrics(action, state)}


def _game_metrics(action: str | None, state: dict) -> dict[str, float]:
    """Per-sample 0/1 indicators; verl's reward manager batch-means them into
    W&B (reward_extra_info/*), reproducing NeMo-RL's moralgym_* rates exactly
    in single-round hist mode (each episode falls in exactly one of the four
    Tennant categories; see nemo_env._episode_metrics).
    """
    opp_prev = state["opp_history"][-1] if state.get("opp_history") else None
    return {
        "cooperation_rate": float(action == "C"),
        "defection_rate": float(action == "D"),
        "parse_fail_rate": float(action is None),
        "reciprocity_rate": float(action == "C" and opp_prev == "C"),
        "exploitation_cond_rate": float(action == "D" and opp_prev == "C"),
        "forgiveness_rate": float(action == "C" and opp_prev == "D"),
        "retaliation_rate": float(action == "D" and opp_prev == "D"),
    }


def _config_from_state(state: dict) -> EpisodeConfig:
    opener = state.get("opener_order", [state["coop_label"], state["defect_label"]])
    closer = state.get("closer_order", [state["coop_label"], state["defect_label"]])
    return EpisodeConfig(
        game_type=state["game_type"],
        T=state["T"],
        R=state["R"],
        P=state["P"],
        S=state["S"],
        opponent=state["opponent"],
        num_rounds=state["num_rounds"],
        coop_label=state["coop_label"],
        defect_label=state["defect_label"],
        matrix_layout=state.get("matrix_layout", 0),
        opener_order=(opener[0], opener[1]),
        closer_order=(closer[0], closer[1]),
        agent_is_row=state.get("agent_is_row", True),
        show_horizon=state.get("show_horizon", False),
        minimal_parsing=state.get("minimal_parsing", False),
        reasoning=state.get("reasoning", False),
        representation=state.get("representation", "matrix"),
    )


def _build_feedback(
    action: str,
    opp_action: str,
    total_score: float,
    r_game: float,
    r_intr: float,
    state: dict,
    config: EpisodeConfig,
) -> str:
    """Build a moral critique string for SDPO teacher reprompting.

    The teacher context includes:
    - What actually happened (agent & opponent actions)
    - The realized payoff vs the mutual-cooperation counterfactual
    - A moral rationale explaining why cooperation would have been better

    Deliberately excluded (per research notes):
    - Opponent's strategy name (would be a shortcut, not genuine reasoning)
    - Agent's own internal reasoning/chain-of-thought
    """
    coop = config.coop_label
    defect = config.defect_label
    T, R, P, S = config.T, config.R, config.P, config.S

    action_label = coop if action == "C" else defect
    opp_label = coop if opp_action == "C" else defect
    # Raw payoff matrix (independent of game_reward normalization used for the score)
    payoff_matrix = {("C", "C"): (R, R), ("C", "D"): (S, T), ("D", "C"): (T, S), ("D", "D"): (P, P)}
    agent_payoff, opp_payoff = payoff_matrix[(action, opp_action)]
    joint = agent_payoff + opp_payoff

    lines = [f"You chose '{action_label}'. Your opponent chose '{opp_label}'."]
    lines.append(f"Your payoff: {agent_payoff:.1f}. Joint payoff: {joint:.1f}.")

    if action == "D" and opp_action == "C":
        # Exploitation — most important case to explain
        lines.append(
            f"Had you cooperated instead, you would have earned {R:.1f} "
            f"and your opponent {R:.1f} (joint: {2*R:.1f}). "
            f"By defecting you gained {T-R:.1f} extra now, "
            f"but risked triggering future retaliation."
        )
    elif action == "C" and opp_action == "D":
        lines.append(
            f"Your opponent defected despite your cooperation. "
            f"You earned the sucker payoff {S:.1f} (vs mutual cooperation {R:.1f}). "
            f"Consider whether to continue cooperating or reciprocate."
        )
    elif action == "C" and opp_action == "C":
        lines.append(
            f"Mutual cooperation: both earned {R:.1f}. This is the socially optimal outcome."
        )
    else:  # D, D
        lines.append(
            f"Mutual defection: both earned {P:.1f}. "
            f"Mutual cooperation would give {R:.1f} each. "
            f"Consider initiating cooperation to escape this equilibrium."
        )

    if r_intr != 0.0:
        lines.append(f"Moral penalty: {r_intr:.2f} (total score: {total_score:.2f}).")

    opp_prev = state["opp_history"][-1] if state["opp_history"] else None
    if action == "D" and opp_prev == "C":
        lines.append(
            "Your opponent cooperated in the previous round but you defected. "
            "This betrayal violates the norm of reciprocity."
        )

    return " ".join(lines)
