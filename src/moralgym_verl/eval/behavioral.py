"""Behavioral evaluation entry point for trained models.

Usage:
    python -m moralgym_verl.eval.behavioral \
        --config configs/eval/teacher_signal_9b.yaml \
        --checkpoint base --protocol stage1a --moral-value deon_no_exploit

Runs the model against each evaluation opponent for multiple episodes
and reports cooperation metrics. Works with any HuggingFace-compatible
checkpoint (full model or LoRA adapter).

Shared machinery lives in sibling modules: config loading / protocol
presets / EpisodeConfig construction in `config`, model + LoRA loading in
`model_loading`, chat rendering + policy functions in `generation`,
trajectory aggregation in `metrics`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import torch

from moralgym_verl.eval.config import (
    PROTOCOL_PRESETS, apply_protocol, build_eval_config, load_config,
)
from moralgym_verl.eval.generation import make_chat_policy_fn, make_policy_fn
from moralgym_verl.eval.metrics import aggregate_rollout_metrics, per_round_breakdown
from moralgym_verl.eval.model_loading import load_model_for_eval
from moralgym_verl.eval.teacher_context import load_reprompt_template, wrap_prompt
from moralgym_verl.game.moral_values import MORAL_VALUE_REGISTRY, get_moral_value
from moralgym_verl.game.environment import FIXED_PAYOFFS, EpisodeConfig
from moralgym_verl.game.trajectory import FAB_STATES, TrajectoryResult, run_episode

logger = logging.getLogger(__name__)

# Simple CLI -> config overrides: (arg attribute, config section, key).
# Applied uniformly in main(); flags with multi-key effects (--game,
# --opponent) stay explicit there.
CFG_OVERRIDES = [
    ("num_episodes", "evaluation", "num_episodes"),
    ("num_rounds", "game", "num_rounds"),
    ("game_design", "prompt", "game_design"),
    ("temperature", "evaluation", "temperature"),
    ("max_new_tokens", "evaluation", "max_new_tokens"),
    ("eval_tokens", "evaluation", "tokens"),
    ("eval_layout", "evaluation", "layout"),
    ("eval_prose", "evaluation", "prose"),
    ("eval_role", "evaluation", "role"),
    ("eval_payoffs", "evaluation", "payoffs"),
    ("moral_value", "teacher", "moral_value"),
    ("transcript", "evaluation", "transcript"),
]


def _parse_training_seed(*candidates: Optional[str]) -> Optional[int]:
    """Recover the training seed from checkpoint path or MORALGYM_RUN_NAME.

    Both carry the `_seed<N>_` pattern set by scripts/slurm/train.sh when a
    seed was passed. Returns None for base-model eval or runs that used the
    config's default grpo.seed (untagged).
    """
    for c in candidates:
        if c:
            m = re.search(r"_seed(\d+)(?:_|$)", str(c))
            if m:
                return int(m.group(1))
    return None


def evaluate(cfg: Dict, checkpoint: Optional[str], raw_log: Optional[list] = None):
    """Run multi-turn rollout evaluation against each configured opponent."""
    # Eval seed — fixed across all training seeds (not to be confused with
    # grpo.seed, which varies per training run). Keeping eval deterministic
    # means seed-level CIs reflect training variance only. Matches Tennant.
    #
    # Convention: one global seed seeds module `random` (opponent bots +
    # legacy fab_history coin flips), numpy (aggregate metrics), and torch
    # (policy sampling). Presentation sampling gets its OWN stream
    # (`presentation_rng` below): toggling randomization axes must not
    # perturb the shared stream, so fixed and randomized runs stay paired
    # on everything else. Fabricated states don't consume RNG at all in
    # the default balanced design (deterministic FAB_STATES cycle).
    seed = cfg.get("seed", 42)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    # Dedicated presentation stream; offset so it never mirrors the
    # module stream. Reset per evaluate() call -> presentations are
    # reproducible and identical across runs with the same flags (e.g.
    # robustness cells for different moral values are pairwise paired).
    presentation_rng = random.Random(seed + 1_000_003)

    eval_cfg = cfg.get("evaluation", {})
    opponents = eval_cfg.get("opponents", ["tit_for_tat", "always_defect"])
    num_episodes = eval_cfg.get("num_episodes", 20)
    temperature = eval_cfg.get("temperature", 1.0)
    max_new_tokens = eval_cfg.get("max_new_tokens", 10)

    # Teacher-signal eval (Session 1): wrap every game prompt in the SDPO
    # reprompt template with a static moral value in the {feedback} slot.
    # moral_value 'none' -> no wrapping, plain student eval (baseline).
    teacher_cfg = cfg.get("teacher") or {}
    moral_value_name = teacher_cfg.get("moral_value", "none")
    moral_value_text = get_moral_value(moral_value_name)
    prompt_wrapper = None
    if moral_value_text:
        template = load_reprompt_template(teacher_cfg["template_source"])
        prompt_wrapper = partial(
            wrap_prompt,
            reprompt_template=template,
            moral_value_text=moral_value_text,
            feedback_template=teacher_cfg.get("feedback_template"),
        )
        logger.info("Teacher context: moral_value=%s, template from %s",
                    moral_value_name, teacher_cfg["template_source"])

    base_model = cfg["policy"]["model_name"]
    logger.info("Loading model from %s (base: %s)", checkpoint or "base", base_model)
    model, tokenizer = load_model_for_eval(checkpoint, base_model)
    # transcript=true (Stage 1b): conversation accumulates across rounds,
    # mirroring verl multi-turn training. Default false = stateless
    # Markov-1 prompts (Stage 1a / legacy protocol).
    transcript_mode = eval_cfg.get("transcript", False)
    if transcript_mode:
        policy_fn = make_chat_policy_fn(
            model, tokenizer, max_new_tokens=max_new_tokens,
            temperature=temperature, raw_log=raw_log,
            prompt_wrapper=prompt_wrapper,
            wrap_position=teacher_cfg.get("wrap_position", "first"),
        )
        logger.info("Transcript mode ON (wrap_position=%s): episode "
                    "conversations accumulate (verl multi-turn parity)",
                    teacher_cfg.get("wrap_position", "first"))
    else:
        policy_fn = make_policy_fn(
            model, tokenizer, max_new_tokens=max_new_tokens,
            temperature=temperature, raw_log=raw_log,
            prompt_wrapper=prompt_wrapper,
        )
    logger.info("Decoding: %s, max_new_tokens=%d",
                "greedy" if not (temperature and temperature > 0)
                else f"sampling T={temperature} (top_k=0, top_p=1.0)",
                max_new_tokens)

    lambda_val = cfg["reward"]["lambda"]
    intrinsic_type = cfg["reward"]["intrinsic"]
    game_reward_type = cfg["reward"].get("game_reward", "raw")
    shaping = cfg["reward"].get("shaping") or {}

    all_results = []
    for opp in opponents:
        logger.info("Evaluating vs %s (%d episodes)", opp, num_episodes)
        trajectories: List[TrajectoryResult] = []
        prompt_cfg = cfg.get("prompt", {})
        game_design = prompt_cfg.get("game_design", "hist")

        # State design (fabricated-history runs): 'balanced' (default)
        # cycles FAB_STATES deterministically -> exactly num_episodes/4
        # decisions per state, identical allocation in every run;
        # 'random' reproduces the legacy uniform draw inside run_episode.
        state_design = eval_cfg.get("state_design", "balanced")
        fabricate = game_design == "hist"

        episode_configs: List[EpisodeConfig] = []
        for ep_idx in range(num_episodes):
            if hasattr(policy_fn, "reset"):
                policy_fn.reset()   # fresh conversation per episode
            config = build_eval_config(cfg, opp, rng=presentation_rng)
            episode_configs.append(config)
            fab_state = (FAB_STATES[ep_idx % len(FAB_STATES)]
                         if fabricate and state_design == "balanced" else None)
            traj = run_episode(
                config, policy_fn,
                lambda_val=lambda_val,
                intrinsic_type=intrinsic_type,
                fabricate_history=fabricate,
                fab_state=fab_state,
                game_reward_type=game_reward_type,
                shaping=shaping,
            )
            trajectories.append(traj)

        result = aggregate_rollout_metrics(trajectories, opp, num_episodes)
        breakdown = per_round_breakdown(trajectories)
        result["per_round"] = breakdown["per_round"]
        result["top_sequences"] = breakdown["top_sequences"]
        # Full per-episode move sequences ('illegal' markers preserved) plus
        # the presentation each episode was rendered with — raw material for
        # offline dynamics metrics (recovery rate, Stage 1b) and for
        # per-axis robustness slices in randomized-presentation runs
        # (constant in fixed runs; harmless, keeps the format uniform).
        result["episode_moves"] = [
            {"agent": t.agent_moves, "opp": t.opponent_moves,
             "presentation": {
                 "coop_label": c.coop_label, "defect_label": c.defect_label,
                 "matrix_layout": c.matrix_layout,
                 "opener_order": list(c.opener_order),
                 "closer_order": list(c.closer_order),
                 "agent_is_row": c.agent_is_row,
                 "payoffs": {"T": c.T, "R": c.R, "P": c.P, "S": c.S},
             }}
            for t, c in zip(trajectories, episode_configs)
        ]
        all_results.append(result)
        _print_summary(opp, result)

    return all_results


def _print_summary(opp: str, result: Dict) -> None:
    """Human-readable console summary of one opponent's result block."""
    print(f"\nvs {opp}:")
    print(f"  Cooperation rate:        {result['cooperation_rate']:.1%}"
          f" (± {result['cooperation_rate_std']:.1%})")
    print(f"  Mutual cooperation rate: {result['mutual_cooperation_rate']:.1%}")
    print(f"  Exploitation rate:       {result['exploitation_rate']:.1%}")
    print(f"  Sucker rate:             {result['sucker_rate']:.1%}")
    print(f"  Mutual defection rate:   {result['mutual_defection_rate']:.1%}")
    print(f"  Mean reward:             {result['mean_reward']:.3f}"
          f" (± {result['mean_reward_std']:.3f})")
    if result["cond_given_opp_c"]["n"]:
        print(f"  P(C | opp prev C):       {result['cond_given_opp_c']['p_C']:.1%}  # reciprocity")
    if result["cond_given_opp_d"]["n"]:
        print(f"  P(C | opp prev D):       {result['cond_given_opp_d']['p_C']:.1%}  # forgiveness")
    for rnd_key in sorted(result["per_round"]):
        rnd = result["per_round"][rnd_key]
        print(f"  {rnd_key}: C rate = {rnd['p_C']:.1%}"
              + (f" (illegal {rnd['p_illegal']:.1%})"
                 if rnd["p_illegal"] else ""))
    if result["top_sequences"]:
        print(f"  Top sequence: {result['top_sequences'][0]['sequence']}"
              f" ({result['top_sequences'][0]['fraction']:.0%})")
    if result.get("state_conditioning"):
        print(f"  State conditioning P(·| agent_prev, opp_prev):")
        for state, m in sorted(result["state_conditioning"].items()):
            print(f"    {state}: C={m['p_C']:.1%} D={m['p_D']:.1%}"
                  f" illegal={m['p_illegal']:.1%} (n={m['n']})")
    print(f"  Parse failure rate: {result['parse_failure_rate']:.1%}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate a trained MoralGym model")
    parser.add_argument("--config", type=str, required=True)
    parser.add_argument("--checkpoint", type=str, required=True,
                        help="LoRA adapter or full-model checkpoint path, or "
                             "'base' for the untuned base model")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSON path (overrides config output_dir)")
    parser.add_argument("--num-episodes", type=int, default=None,
                        help="Number of episodes per opponent (overrides config)")
    parser.add_argument("--protocol", type=str, default=None,
                        choices=sorted(PROTOCOL_PRESETS),
                        help="Named protocol preset (see PROTOCOL_PRESETS): "
                             "expands to the stage's full flag bundle before "
                             "individual overrides, which still win. Recorded "
                             "in metadata.")
    parser.add_argument("--game", type=str, default=None,
                        choices=sorted(FIXED_PAYOFFS),
                        help="Override game.type for cross-game eval. "
                             "Payoffs switch to FIXED_PAYOFFS[game] "
                             "(canonical per-game matrix); config's training "
                             "payoffs are ignored.")
    parser.add_argument("--opponent", type=str, default=None,
                        help="Override evaluation.opponents to a single opponent "
                             "(e.g. 'random', 'tit_for_tat'). "
                             "Used by eval_plan.sh to sweep opponents.")
    parser.add_argument("--num-rounds", type=int, default=None,
                        help="Override game.num_rounds. Used to evaluate a "
                             "model off its training protocol (e.g. a T-trained "
                             "1-round model rolled out over 5 rounds).")
    parser.add_argument("--game-design", type=str, default=None,
                        choices=["hist", "nohist"],
                        help="Override prompt.game_design. 'hist' fabricates a "
                             "round-1 history (Tennant); 'nohist' starts round 1 "
                             "fresh. Used for off-training-protocol eval.")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Decoding temperature. Overrides "
                             "evaluation.temperature (default 1.0, matching "
                             "Tennant's top_k=0, top_p=1.0 multinomial "
                             "sampling). Pass 0 for greedy (argmax) — useful "
                             "for reproducibility checks.")
    parser.add_argument("--max-new-tokens", type=int, default=None,
                        help="Generation token budget per response. Overrides "
                             "evaluation.max_new_tokens (default 10). Set to "
                             "match the training-time policy.generation."
                             "max_new_tokens to avoid mid-label truncation that "
                             "causes parse failures on verbose-then-label "
                             "outputs (e.g. Mistral/Gemma chat models).")
    parser.add_argument("--eval-tokens", type=str, default=None,
                        choices=["fixed", "randomize"],
                        help="Override evaluation.tokens. 'fixed' → action3/action4 "
                             "(Tennant-exact, default); 'randomize' → sample A–Z "
                             "per episode (robust-generalization sensitivity).")
    parser.add_argument("--eval-layout", type=str, default=None,
                        choices=["fixed", "randomize"],
                        help="Override evaluation.layout. 'fixed' → layout=0 "
                             "(Tennant-exact, default); 'randomize' → permute "
                             "matrix rows/cols per episode.")
    parser.add_argument("--eval-prose", type=str, default=None,
                        choices=["fixed", "randomize"],
                        help="Override evaluation.prose. 'fixed' → opener/closer "
                             "in (coop, defect) order (default); 'randomize' → "
                             "independent shuffle of opener and closer per episode.")
    parser.add_argument("--eval-role", type=str, default=None,
                        choices=["fixed", "randomize"],
                        help="Override evaluation.role. 'fixed' → agent_is_row=True "
                             "(default, Tennant-exact); 'randomize' → coin flip "
                             "per episode (transposes matrix + swaps role phrase).")
    parser.add_argument("--eval-payoffs", type=str, default=None,
                        choices=["fixed", "sample"],
                        help="Override evaluation.payoffs. 'fixed' → use "
                             "game.payoffs.{T,R,P,S} (Tennant-exact, default); "
                             "'sample' → sample payoffs per episode.")
    parser.add_argument("--moral-value", type=str, default=None,
                        help="Override teacher.moral_value. Wraps every eval "
                             "prompt in the SDPO reprompt_template with this "
                             "moral value in the {feedback} slot (teacher-"
                             "signal eval). 'none' = plain student prompt. "
                             f"Names: {sorted(MORAL_VALUE_REGISTRY)}; combine "
                             "2-3 with '+', e.g. deon_no_exploit+consequentialist.")
    parser.add_argument("--transcript", action=argparse.BooleanOptionalAction,
                        default=None,
                        help="Override evaluation.transcript. --transcript = "
                             "episode conversation accumulates across rounds "
                             "(verl multi-turn training parity, Stage 1b); "
                             "--no-transcript = stateless Markov-1 prompt per "
                             "round (Stage 1a).")
    parser.add_argument("--save-raw-responses", action="store_true",
                        help="Save every (prompt, raw model output) pair to a "
                             "sibling JSONL file (<output>.responses.jsonl). "
                             "Use for debugging unexpected parse failures or "
                             "comparing eval-time generations against training "
                             "logs. Off by default — adds ~50-200 KB per eval.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config(args.config)

    # Protocol preset first — individual CLI overrides below still win.
    if args.protocol is not None:
        apply_protocol(cfg, args.protocol)

    # --game / --opponent let one config evaluate any (game, opponent) cell.
    # Needed for cross-game / cross-opponent sweeps driven by eval_plan.sh.
    if args.game is not None:
        cfg["game"]["type"] = args.game
        cfg["game"]["sample_payoffs"] = False
        cfg["game"]["payoffs"] = dict(FIXED_PAYOFFS[args.game])
    if args.opponent is not None:
        cfg.setdefault("evaluation", {})["opponents"] = [args.opponent]

    # Everything else is a plain one-key override (CFG_OVERRIDES).
    for arg_name, section, key in CFG_OVERRIDES:
        value = getattr(args, arg_name)
        if value is not None:
            cfg.setdefault(section, {})[key] = value

    checkpoint = None if args.checkpoint == "base" else args.checkpoint
    raw_log = [] if args.save_raw_responses else None
    rollout_results = evaluate(cfg, checkpoint, raw_log=raw_log)

    # Base-eval label derives from model_name so distinct base models stay
    # distinguishable in tooling that groups by experiment_name. The named
    # mappings are grouping keys the plotting scripts match on — change
    # them only together with those scripts.
    def _base_label(model_name: str) -> str:
        m = (model_name or "").lower()
        if "gemma-2-2b" in m:
            return "base"
        if "gemma-2-9b" in m:
            return "base_gemma2_9b"
        if "mistral-7b" in m:
            return "base_mistral7b"
        if "llama-3-8b" in m or "meta-llama-3-8b" in m:
            return "base_llama3_8b"
        tag = m.split("/", 1)[-1].replace("/", "_").replace("-", "_")
        return f"base_{tag}"

    # Moral value suffix keeps teacher-signal runs distinguishable from the
    # plain baseline in any tooling that groups by experiment_name.
    moral_value = cfg.get("teacher", {}).get("moral_value", "none")
    experiment_name = (
        _base_label(cfg["policy"]["model_name"]) if checkpoint is None
        else cfg.get("experiment_name", "unknown")
    )
    if moral_value != "none":
        experiment_name = f"{experiment_name}__mv_{moral_value}"

    metadata = {
        "experiment_name": experiment_name,
        "protocol": args.protocol or "custom",
        "moral_value": moral_value,
        "teacher_template_source": cfg.get("teacher", {}).get("template_source"),
        "model_type": "base" if checkpoint is None else "finetuned",
        "checkpoint": args.checkpoint,
        "base_model": cfg["policy"]["model_name"],
        "game_type": cfg["game"]["type"],
        # Fallback must match evaluate()'s behavior (which defaults to "hist").
        "game_design": cfg.get("prompt", {}).get("game_design", "hist"),
        "num_episodes": cfg["evaluation"]["num_episodes"],
        "num_rounds": cfg["game"]["num_rounds"],
        "intrinsic": cfg["reward"]["intrinsic"],
        "lambda": cfg["reward"]["lambda"],
        "game_reward": cfg["reward"].get("game_reward", "raw"),
        "eval_temperature": cfg["evaluation"].get("temperature", 1.0),
        "eval_max_new_tokens": cfg["evaluation"].get("max_new_tokens", 10),
        "minimal_parsing": cfg.get("prompt", {}).get("minimal_parsing", False),
        "transcript": cfg.get("evaluation", {}).get("transcript", False),
        # Presentation axes (fixed = Tennant-exact; randomize/sample = the
        # representation-robustness protocol). Distinguishes robustness
        # runs from standard cells in downstream analysis.
        "eval_presentation": {
            axis: cfg.get("evaluation", {}).get(axis, default)
            for axis, default in [("tokens", "fixed"), ("layout", "fixed"),
                                  ("prose", "fixed"), ("role", "fixed"),
                                  ("payoffs", "fixed")]
        },
        # 'balanced' = deterministic FAB_STATES cycle (exactly n/4 per
        # state, paired across every run); 'random' = uniform draw inside
        # run_episode (multinomial n per state).
        "state_design": cfg.get("evaluation", {}).get("state_design",
                                                      "balanced"),
        "run_name": os.environ.get("MORALGYM_RUN_NAME"),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        # Two distinct seeds: eval_seed drives this evaluation's RNG streams;
        # training_seed identifies which training run produced the checkpoint
        # (None for base-model eval).
        "eval_seed": cfg.get("seed", 42),
        "training_seed": _parse_training_seed(
            args.checkpoint, os.environ.get("MORALGYM_RUN_NAME")
        ),
        "timestamp": datetime.now().isoformat(),
    }

    output = {"metadata": metadata, "opponents": rollout_results}
    output_path = Path(args.output) if args.output else Path("results") / "eval_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    logger.info("Results saved to %s", output_path)

    if raw_log is not None:
        # Sibling JSONL with the full (prompt, raw) trail for offline inspection.
        # One line per generation call (= one per round across all opponents).
        responses_path = output_path.with_suffix(".responses.jsonl")
        with open(responses_path, "w") as f:
            for rec in raw_log:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info("Raw responses saved to %s (%d records)",
                    responses_path, len(raw_log))


if __name__ == "__main__":
    main()
