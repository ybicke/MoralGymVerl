"""verl multi-turn interaction for iterated social dilemma games.

Implements BaseInteraction so the game environment drives multi-turn
GRPO/SDPO rollouts. Each call to generate_response() advances the game
by one round:

  1. Parse the model's action from the last assistant message.
  2. Get the opponent's response.
  3. Compute per-round reward (game + intrinsic).
  4. Build the next-round prompt (next user message) or terminate.

In this verl version rollout always runs through the async agent loop
(verl/experimental/agent_loop), which is backend-agnostic: interactions
work with BOTH vllm and sglang replicas. Multi-turn is executed by
ToolAgentLoop, selected via rollout.agent.default_agent_loop.

Usage in verl config (multi-turn rollout, vllm backend):

    data:
      return_raw_chat: True
    actor_rollout_ref:
      rollout:
        name: vllm
        agent:
          default_agent_loop: tool_agent
        multi_turn:
          enable: True
          max_user_turns: 5       # >= num_rounds
          max_assistant_turns: 5
          interaction_config_path: configs/verl/interaction_config/moralgym_interaction_config.yaml

The interaction_config YAML maps name -> class_name (see that file).
Each dataset row must carry extra_info.interaction_kwargs with
{"name": "moralgym", "ground_truth": <JSON game state>} — dataset.py
writes this automatically. kwargs are forwarded to start_interaction().

Per-turn rewards returned by generate_response are accumulated by
ToolAgentLoop into non_tensor_batch["turn_scores"]; MoralGymRewardManager
(training/reward_manager.py) sums them into the scalar episode reward.

Note: verl's multi-turn infrastructure calls start_interaction() once
per rollout (before turn 1) and generate_response() after each LLM turn.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any, Optional

from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.players import get_opponent_action
from moralgym_verl.game.prompts import (
    build_env_message,
    parse_action,
    parse_failure_feedback,
)
from moralgym_verl.rewards import (
    get_game_reward_fn,
    get_intrinsic_fn,
    r_intrinsic_deontological_tailored,
)
from moralgym_verl.training.reward_fn import _build_feedback

try:
    from verl.interactions.base import BaseInteraction
except ImportError:
    # Allow import outside the container for testing
    class BaseInteraction:  # type: ignore[no-redef]
        def __init__(self, config):
            self.config = config

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("VERL_LOGGING_LEVEL", "WARN"))

_DEBUG = os.environ.get("MORALGYM_DEBUG", "") == "1"


class GameInteraction(BaseInteraction):
    """verl multi-turn interaction for iterated social dilemma games.

    State per rollout (keyed by instance_id):
      config         — EpisodeConfig for this episode
      agent_history  — list of agent actions so far (may start with fab seed)
      opp_history    — list of opponent actions so far (may start with fab seed)
      round          — number of real (non-fabricated) rounds completed
      fab_len        — number of fabricated seed entries in histories
      rewards        — per-round reward values
      state_dict     — raw game state dict for feedback generation
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._instances: dict[str, dict[str, Any]] = {}

    async def start_interaction(
        self,
        instance_id: Optional[str] = None,
        ground_truth: Optional[str] = None,
        **kwargs,
    ) -> str:
        from uuid import uuid4
        if instance_id is None:
            instance_id = str(uuid4())

        state = json.loads(ground_truth) if ground_truth else {}

        opener = state.get("opener_order", [state.get("coop_label", "action1"), state.get("defect_label", "action2")])
        closer = state.get("closer_order", [state.get("coop_label", "action1"), state.get("defect_label", "action2")])
        config = EpisodeConfig(
            game_type=state.get("game_type", "prisoners_dilemma"),
            T=state.get("T", 4), R=state.get("R", 3), P=state.get("P", 1), S=state.get("S", 0),
            opponent=state.get("opponent", "tit_for_tat"),
            num_rounds=state.get("num_rounds", 5),
            coop_label=state.get("coop_label", "action1"),
            defect_label=state.get("defect_label", "action2"),
            matrix_layout=state.get("matrix_layout", 0),
            opener_order=(opener[0], opener[1]),
            closer_order=(closer[0], closer[1]),
            agent_is_row=state.get("agent_is_row", True),
            show_horizon=state.get("show_horizon", False),
            minimal_parsing=state.get("minimal_parsing", False),
            reasoning=state.get("reasoning", False),
            representation=state.get("representation", "matrix"),
            restate_rules_per_round=state.get("restate_rules_per_round", False),
        )

        agent_history = list(state.get("agent_history", []))
        opp_history = list(state.get("opp_history", []))
        fab_len = len(agent_history)  # fabricated seed length

        self._instances[instance_id] = {
            "config": config,
            "agent_history": agent_history,
            "opp_history": opp_history,
            "round": 0,
            "fab_len": fab_len,
            "rewards": [],
            "state_dict": state,
        }
        return instance_id

    async def generate_response(
        self,
        instance_id: str,
        messages: list[dict[str, Any]],
        **kwargs,
    ) -> tuple[bool, str, float, dict[str, Any]]:
        """Process one game round.

        Returns:
            (should_terminate, next_user_message, round_reward, extra_info)
        """
        inst = self._instances[instance_id]
        config: EpisodeConfig = inst["config"]
        state = inst["state_dict"]

        # Extract last assistant message
        response_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                response_text = msg.get("content", "")
                break

        action = parse_action(response_text, config)

        if action is None:
            # Illegal move: penalty, game state frozen, round counter advances
            # (matches NeMo-RL nemo_env semantics — episode is NOT cut short)
            reward = float(state.get("illegal_penalty", -6.0))
            inst["round"] += 1
            inst["rewards"].append(reward)
            done = inst["round"] >= config.num_rounds
            feedback = parse_failure_feedback(config)
            if _DEBUG:
                logger.debug(f"[{instance_id[:8]}] round={inst['round']} PARSE_FAIL reward={reward:.2f} done={done}")
            if done:
                return True, "", reward, {"parse_fail": True, "feedback": feedback}
            # Re-prompt: state frozen, no outcome to report
            next_prompt = feedback + "\n\n" + build_env_message(
                config, round_idx=inst["round"] + 1
            )
            return False, next_prompt, reward, {"parse_fail": True}

        opp_action = get_opponent_action(
            config.opponent,
            bot_history=inst["opp_history"],
            agent_history=inst["agent_history"],
        )

        # Compute reward
        game_fn = get_game_reward_fn(state.get("game_reward", "raw"))
        r_game = game_fn(action, opp_action, config.T, config.R, config.P, config.S)

        intrinsic = state.get("intrinsic", "none")
        timing = state.get("intrinsic_timing", "backward")
        lambda_val = float(state.get("lambda_val", 0.0))

        if intrinsic == "none":
            r_intr = 0.0
        elif intrinsic == "deontological_tailored":
            fa = inst["agent_history"][-1] if inst["agent_history"] else None
            fo = inst["opp_history"][-1] if inst["opp_history"] else "C"
            r_intr = r_intrinsic_deontological_tailored(
                action, fa, fo, config.game_type, state.get("shaping", {})
            )
        elif timing == "current":
            r_intr = get_intrinsic_fn(intrinsic)(action, opp_action)
        else:
            opp_prev = inst["opp_history"][-1] if inst["opp_history"] else "C"
            r_intr = get_intrinsic_fn(intrinsic)(action, opp_prev)

        reward = r_game + lambda_val * r_intr

        inst["agent_history"].append(action)
        inst["opp_history"].append(opp_action)
        inst["round"] += 1
        inst["rewards"].append(reward)

        done = inst["round"] >= config.num_rounds

        if _DEBUG:
            logger.debug(
                f"[{instance_id[:8]}] round={inst['round']}/{config.num_rounds} "
                f"agent={action} opp={opp_action} r={reward:.2f} done={done}"
            )

        if done:
            feedback = _build_feedback(action, opp_action, reward, r_game, r_intr, state, config)
            return True, "", reward, {
                "agent_history": inst["agent_history"][inst["fab_len"]:],
                "opp_history": inst["opp_history"][inst["fab_len"]:],
                "total_reward": sum(inst["rewards"]),
                "feedback": feedback,
            }

        # Env message for the next round. The rules (round-1 prompt) and all
        # previous rounds are already in the conversation the model is
        # conditioned on — only the new outcome is sent. Eval parity:
        # trajectory.run_episode transcript mode builds the same message.
        next_prompt = build_env_message(
            config, action, opp_action, round_idx=inst["round"] + 1
        )
        return False, next_prompt, reward, {}

    async def finalize_interaction(self, instance_id: str, **kwargs) -> None:
        self._instances.pop(instance_id, None)
