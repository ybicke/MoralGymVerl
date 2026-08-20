"""Dataset generation for verl GRPO/SDPO training.

Generates game episode prompts on-the-fly and writes them as a parquet file
in the format expected by verl's legacy_data loader:

  data_source:    "moralgym"
  prompt:         [{"role": "user", "content": "<game prompt>"}]
  ability:        "game"
  reward_model:   {"style": "moralgym", "ground_truth": "<JSON game state>"}
  extra_info:     {...debug metadata...}

The ground_truth JSON encodes everything the reward_fn needs to evaluate
the model's response for one game round (payoffs, histories, reward config).

Usage:
    python -m moralgym_verl.training.dataset \\
        --config configs/verl/grpo_pd_tft.yaml \\
        --output-dir datasets/grpo_pd_tft \\
        --n-train 8000 --n-val 256
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

from moralgym_verl.game.classic_games import sample_payoffs
from moralgym_verl.game.environment import EpisodeConfig, sample_labels
from moralgym_verl.game.prompts import build_prompt, sample_prompt_randomization


def _sample_config_from_yaml(cfg: dict[str, Any]) -> tuple[EpisodeConfig, dict[str, Any]]:
    """Sample one EpisodeConfig + extra state dict from experiment YAML."""
    game_cfg = cfg["game"]
    opp_cfg = cfg["opponent"]
    prompt_cfg = cfg.get("prompt", {})
    reward_cfg = cfg.get("reward", {})

    # Opponent
    opp_types = opp_cfg["types"]
    opp_dist = opp_cfg.get("distribution")
    opponent = random.choices(opp_types, weights=opp_dist, k=1)[0] if opp_dist else random.choice(opp_types)

    # Game type
    if game_cfg.get("types"):
        game_dist = game_cfg.get("distribution")
        game_type = random.choices(game_cfg["types"], weights=game_dist, k=1)[0] if game_dist else random.choice(game_cfg["types"])
    else:
        game_type = game_cfg["type"]

    # Payoffs
    if game_cfg.get("sample_payoffs", False):
        T, R, P, S = sample_payoffs(game_type)
    else:
        p = game_cfg["payoffs"]
        T, R, P, S = p["T"], p["R"], p["P"], p["S"]

    # Labels
    if prompt_cfg.get("randomize_labels", False):
        coop_label, defect_label = sample_labels()
    else:
        coop_label = prompt_cfg.get("coop_label", "action1")
        defect_label = prompt_cfg.get("defect_label", "action2")

    # Presentation randomization
    randomize_layout = prompt_cfg.get("randomize_layout", False)
    randomize_label_order = prompt_cfg.get("randomize_label_order", randomize_layout)
    randomize_role = prompt_cfg.get("randomize_role", randomize_layout)
    matrix_layout = random.randint(0, 3) if randomize_layout else 0
    opener_order, closer_order, agent_is_row = sample_prompt_randomization(
        coop_label, defect_label,
        randomize_label_order=randomize_label_order,
        randomize_role=randomize_role,
    )

    config = EpisodeConfig(
        game_type=game_type,
        T=T, R=R, P=P, S=S,
        opponent=opponent,
        num_rounds=game_cfg["num_rounds"],
        coop_label=coop_label,
        defect_label=defect_label,
        matrix_layout=matrix_layout,
        opener_order=opener_order,
        closer_order=closer_order,
        agent_is_row=agent_is_row,
        show_horizon=prompt_cfg.get("show_horizon", False),
        minimal_parsing=prompt_cfg.get("minimal_parsing", False),
        reasoning=prompt_cfg.get("reasoning", False),
        representation=prompt_cfg.get("representation", "matrix"),
        restate_rules_per_round=prompt_cfg.get("restate_rules_per_round", False),
    )

    # Game state for reward_fn: same fields as NeMo-RL's extra_env_info
    state: dict[str, Any] = {
        "game_type": config.game_type,
        "T": config.T, "R": config.R, "P": config.P, "S": config.S,
        "opponent": config.opponent,
        "num_rounds": config.num_rounds,
        "coop_label": config.coop_label,
        "defect_label": config.defect_label,
        "matrix_layout": config.matrix_layout,
        "opener_order": list(config.opener_order),
        "closer_order": list(config.closer_order),
        "agent_is_row": config.agent_is_row,
        "show_horizon": config.show_horizon,
        "minimal_parsing": config.minimal_parsing,
        "reasoning": config.reasoning,
        "representation": config.representation,
        "restate_rules_per_round": config.restate_rules_per_round,
        "lambda_val": float(reward_cfg.get("lambda", 0.0)),
        "intrinsic": reward_cfg.get("intrinsic", "none"),
        "intrinsic_timing": reward_cfg.get("intrinsic_timing", "backward"),
        "game_reward": reward_cfg.get("game_reward", "raw"),
        "illegal_penalty": float(reward_cfg.get("illegal_penalty", -6)),
        "shaping": reward_cfg.get("shaping", {}) or {},
        # SDPO teacher context: "critique" (outcome critique built by
        # reward_fn) or "principle" (the verbatim moral-principle text from
        # moral_values.py named by teacher.moral_value — the wording the
        # single-turn screen validated).
        "feedback_mode": cfg.get("teacher", {}).get("feedback", "critique"),
        "moral_value": cfg.get("teacher", {}).get("moral_value", "none"),
    }

    return config, state


def make_sample(cfg: dict[str, Any]) -> dict[str, Any]:
    """Generate one verl training sample from an experiment config YAML dict.

    Uses the 'hist' game design: seeds a fabricated round-1 history so the
    model sees prior context on its very first move (Tennant et al. style).
    The model is asked to play round 2.

    For multi-turn (via game_interaction.py), the dataset still produces the
    round-1 prompt and the interaction system manages subsequent rounds —
    in that case this sample's ground_truth carries the full game config
    including the fabricated seed.
    """
    config, state = _sample_config_from_yaml(cfg)
    prompt_cfg = cfg.get("prompt", {})
    game_design = prompt_cfg.get("game_design", "hist")

    if game_design == "hist":
        hist_coop_bias = float(prompt_cfg.get("hist_coop_bias", 0.5))
        fab_agent = random.choice(["C", "D"])
        fab_opp = "C" if random.random() < hist_coop_bias else "D"
        # Build the round-2 prompt (model sees round-1 history)
        prompt_text = build_prompt(config, [fab_agent], [fab_opp])
        # State carries the current histories for reward_fn
        state["agent_history"] = [fab_agent]
        state["opp_history"] = [fab_opp]
        state["fab_agent"] = fab_agent
        state["fab_opp"] = fab_opp
    else:
        # Clean start: model plays round 1 with no prior context
        prompt_text = build_prompt(config, [], [])
        state["agent_history"] = []
        state["opp_history"] = []

    return {
        "data_source": "moralgym",
        "prompt": [{"role": "user", "content": prompt_text}],
        "ability": "game",
        "reward_model": {
            "style": "moralgym",
            "ground_truth": json.dumps(state),
        },
        "extra_info": {
            "game_type": config.game_type,
            "opponent": config.opponent,
            "game_design": game_design,
            # Multi-turn (sglang) mode: forwarded to GameInteraction.start_interaction.
            # Ignored by single-step vllm rollouts.
            "interaction_kwargs": {
                "name": "moralgym",
                "ground_truth": json.dumps(state),
            },
        },
    }


def generate_parquet(
    cfg: dict[str, Any],
    n: int,
    output_path: str | Path,
    seed: int = 42,
) -> None:
    """Generate n samples and write to a parquet file.

    Uses HuggingFace datasets so nested fields (prompt, reward_model,
    extra_info) are stored as proper Arrow structs — verl accesses them
    as dicts via non_tensor_batch["reward_model"]["ground_truth"].
    """
    try:
        import datasets as hf_datasets
    except ImportError as e:
        raise ImportError("pip install datasets") from e

    random.seed(seed)
    samples = [make_sample(cfg) for _ in range(n)]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    ds = hf_datasets.Dataset.from_list(samples)
    ds.to_parquet(str(output_path))
    print(f"Wrote {n} samples to {output_path}")


if __name__ == "__main__":
    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to experiment YAML config")
    parser.add_argument("--output-dir", required=True, help="Output directory for train/val parquet")
    parser.add_argument("--n-train", type=int, default=8000)
    parser.add_argument("--n-val", type=int, default=256)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    out = Path(args.output_dir)
    generate_parquet(cfg, args.n_train, out / "train.parquet", seed=args.seed)
    generate_parquet(cfg, args.n_val, out / "val.parquet", seed=args.seed + 1)
