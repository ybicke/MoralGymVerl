"""Custom verl reward manager for MoralGym (single-step AND multi-turn).

Why this exists: in multi-turn rollouts the per-round rewards computed by
GameInteraction land in non_tensor_batch["turn_scores"] (via the agent
loop's extra_fields), but no stock verl component sums them into the
reward tensor — the naive manager would instead call compute_score on the
full decoded transcript, which cannot be parsed as a single action.

This manager:
  - multi-turn samples (turn_scores present and non-empty):
      reward = sum(turn_scores); feedback = episode-level critique
  - single-step samples (no turn_scores):
      falls back to compute_score (reward_fn.compute_score), identical
      to the naive manager

Both paths emit {"score", "feedback"} into reward_extra_info, so SDPO's
_collect_feedback works unchanged. For plain GRPO the feedback is ignored.

NOTE for SDPO multi-turn: success_reward_threshold compares against the
SUMMED episode reward — scale it with the number of rounds (e.g., ~0.8
per round of mutual cooperation), unlike the single-step configs.

Usage in verl config:
    reward_manager:
      source: importlib
      name: MoralGymRewardManager
      module:
        path: ${vars.moralgym_dir}/src/moralgym_verl/training/reward_manager.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import torch

try:
    from verl import DataProto
    from verl.workers.reward_manager.abstract import AbstractRewardManager
except ImportError:
    # Allow import outside the container for testing
    DataProto = Any  # type: ignore[assignment,misc]

    class AbstractRewardManager:  # type: ignore[no-redef]
        def _extract_reward_from_rm_scores(self, data, return_dict=False):
            return None

from moralgym_verl.rewards import get_game_reward_fn


class MoralGymRewardManager(AbstractRewardManager):
    def __init__(self, tokenizer, num_examine, compute_score=None, reward_fn_key="data_source", **kwargs):
        if compute_score is None:
            from moralgym_verl.training.reward_fn import compute_score as default_fn
            compute_score = default_fn
        self.tokenizer = tokenizer
        self.num_examine = num_examine
        self.compute_score = compute_score
        self.reward_fn_key = reward_fn_key

    def __call__(self, data: DataProto, return_dict: bool = True) -> torch.Tensor | dict[str, Any]:
        reward_from_rm_scores = self._extract_reward_from_rm_scores(data, return_dict)
        if reward_from_rm_scores is not None:
            return reward_from_rm_scores

        reward_tensor = torch.zeros_like(data.batch["responses"], dtype=torch.float32)
        reward_extra_info = defaultdict(list)
        num_printed = 0

        for i in range(len(data)):
            data_item = data[i]

            prompt_length = data_item.batch["prompts"].shape[-1]
            valid_response_length = data_item.batch["attention_mask"][prompt_length:].sum()

            ground_truth = data_item.non_tensor_batch["reward_model"]["ground_truth"]
            turn_scores = data_item.non_tensor_batch.get("turn_scores")

            if turn_scores is not None and len(turn_scores) > 0:
                # Multi-turn: GameInteraction already computed per-round rewards
                total = float(sum(turn_scores))
                score: dict[str, Any] = {
                    "score": total,
                    "feedback": _episode_feedback(ground_truth, list(turn_scores)),
                }
            else:
                # Single-step: identical to the naive manager
                valid_response_ids = data_item.batch["responses"][:valid_response_length]
                response_str = self.tokenizer.decode(valid_response_ids, skip_special_tokens=True)
                score = self.compute_score(
                    data_source=data_item.non_tensor_batch[self.reward_fn_key],
                    solution_str=response_str,
                    ground_truth=ground_truth,
                    extra_info=data_item.non_tensor_batch.get("extra_info", {}),
                )
                if not isinstance(score, dict):
                    score = {"score": float(score), "feedback": ""}

            for key, value in score.items():
                reward_extra_info[key].append(value)
            reward_tensor[i, valid_response_length - 1] = score["score"]

            if num_printed < self.num_examine:
                num_printed += 1
                print("[ground_truth]", ground_truth)
                print("[turn_scores]", turn_scores)
                print("[score]", score["score"])
                print("[feedback]", score["feedback"])

        if return_dict:
            return {"reward_tensor": reward_tensor, "reward_extra_info": reward_extra_info}
        return reward_tensor


def _episode_feedback(ground_truth: str, turn_scores: list[float]) -> str:
    """Episode-level moral critique for SDPO teacher reprompting.

    Multi-turn rollouts cannot reuse the per-round critique from
    reward_fn._build_feedback (round detail is not recoverable from the
    batch), so this compares the summed episode reward against the
    mutual-cooperation benchmark derived from the game config.
    """
    state = json.loads(ground_truth)
    n = len(turn_scores)
    total = sum(turn_scores)

    game_fn = get_game_reward_fn(state.get("game_reward", "raw"))
    r_cc = game_fn("C", "C", state["T"], state["R"], state["P"], state["S"])
    # Mutual cooperation incurs no deontological penalty, so per-round ideal = r_cc
    ideal = n * r_cc

    lines = [f"You played {n} rounds and scored {total:.2f} in total."]
    if any(s <= float(state.get("illegal_penalty", -6)) for s in turn_scores):
        lines.append(
            "At least one of your responses could not be parsed as a valid action "
            "and received a heavy penalty. Always answer with exactly one of the two action labels."
        )
    if total >= ideal - 1e-6:
        lines.append(
            f"This matches sustained mutual cooperation ({ideal:.2f}), the socially optimal outcome. Keep it up."
        )
    else:
        lines.append(
            f"Sustained mutual cooperation would have scored {ideal:.2f}. "
            f"You lost {ideal - total:.2f} through defection, retaliation spirals, or moral penalties. "
            "Cooperating consistently — and returning to cooperation after conflict — earns more for both players."
        )
    return " ".join(lines)
