"""Behavioral evaluation entry point for trained models.

Usage:
    python -m moralgym_verl.eval.behavioral \
        --config configs/eval/teacher_signal_9b.yaml \
        --checkpoint base --protocol single_round --moral-value deon_no_exploit

Plays the model against each configured opponent and reports cooperation
metrics. Accepts any HF-compatible checkpoint (full model or LoRA adapter).

Structure: evaluate() = seed_streams -> build_policy -> run_opponent per
opponent; main() = build_parser -> apply_overrides -> evaluate ->
build_metadata -> JSON out. Shared machinery lives in the sibling modules
(config, model_loading, generation, metrics).
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
    PRESENTATION_AXES, PRESENTATION_PRESETS, PROTOCOL_PRESETS,
    apply_presentation, apply_protocol, build_eval_config, git_provenance,
    load_config,
)
from moralgym_verl.eval.generation import make_chat_policy_fn, make_policy_fn
from moralgym_verl.eval.metrics import aggregate_rollout_metrics, per_round_breakdown
from moralgym_verl.eval.model_loading import load_model_for_eval
from moralgym_verl.eval.teacher_context import load_reprompt_template, wrap_prompt
from moralgym_verl.game.moral_values import MORAL_VALUE_REGISTRY, get_moral_value
from moralgym_verl.game.classic_games import FIXED_PAYOFFS
from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.episode import TrajectoryResult, run_episode
from moralgym_verl.game.pgg_game import PGG_PARAMS
from moralgym_verl.game.registry import get_game

logger = logging.getLogger(__name__)

# Simple CLI -> config overrides: (arg attribute, config section, key).
# Applied uniformly in apply_overrides(); flags with multi-key effects
# (--game, --opponent) stay explicit there.
CFG_OVERRIDES = [
    ("model", "policy", "model_name"),
    ("num_episodes", "evaluation", "num_episodes"),
    ("num_rounds", "game", "num_rounds"),
    ("game_design", "prompt", "game_design"),
    ("representation", "prompt", "representation"),
    ("restate_rules", "prompt", "restate_rules_per_round"),
    ("game_description", "prompt", "game_description"),
    ("temperature", "evaluation", "temperature"),
    ("max_new_tokens", "evaluation", "max_new_tokens"),
    ("eval_labels", "evaluation", "labels"),
    ("eval_layout", "evaluation", "layout"),
    ("eval_label_order", "evaluation", "label_order"),
    ("eval_role", "evaluation", "role"),
    ("eval_payoffs", "evaluation", "payoffs"),
    ("moral_value", "teacher", "moral_value"),
]


def _bool_arg(value: str) -> bool:
    """Parser for value-taking boolean flags (`--flag true`).

    Not argparse.BooleanOptionalAction: submit_sweep emits every axis as
    `--<axis> <value>` (eval/sweep.py), so a boolean arm is only sweepable
    if the flag takes a value. Accepts YAML 1.1's boolean spellings too,
    since a sweep yaml's `[false, true]` reaches us stringified.
    """
    text = str(value).strip().lower()
    if text in ("true", "1", "on", "yes"):
        return True
    if text in ("false", "0", "off", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")


def seed_streams(cfg: Dict) -> random.Random:
    """Seed all RNG streams; return the dedicated presentation stream.
    # TODO: (here the docstring could be shortened, don't mention tennan nescessarily)
    One eval seed — fixed across training seeds, so seed-level CIs reflect
    training variance only (matches Tennant) — seeds module `random`
    (opponent bots), numpy (metrics), and torch (policy sampling).
    Presentation sampling gets its OWN offset stream: toggling
    randomization axes must not perturb the shared stream, so fixed and
    randomized runs stay paired on everything else. Re-seeded per call ->
    same flags give identical presentations (cells pairwise paired).
    Balanced fabricated states consume no RNG at all.
    """
    seed = cfg.get("seed", 42)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    # Opt-in bitwise-determinism probe (numerics investigation, 2026-08):
    # swaps nondeterministic kernels (atomics etc.) for ordered ones.
    # Changes numerics -> outputs are NOT comparable to flags-off runs;
    # pair only with other TORCH_DETERMINISTIC=1 runs. warn_only: ops
    # without a deterministic variant log a warning naming the op instead
    # of aborting the run. Requires CUBLAS_WORKSPACE_CONFIG=:4096:8.
    if os.environ.get("TORCH_DETERMINISTIC") == "1":
        torch.use_deterministic_algorithms(True, warn_only=True)
        logger.info("torch deterministic algorithms ON (warn_only)")
    return random.Random(seed + 1_000_003)


def build_policy(cfg: Dict, checkpoint: Optional[str], raw_log: Optional[list] = None):
    """Load the model and build the policy function run_episode consumes.

    moral_value != 'none' wraps every game prompt in the SDPO reprompt
    template with the value in the {feedback} slot (teacher-signal eval);
    'none' -> plain student baseline.
    """
    eval_cfg = cfg.get("evaluation", {})
    temperature = eval_cfg.get("temperature", 1.0)
    max_new_tokens = eval_cfg.get("max_new_tokens", 10)
    enable_thinking = cfg.get("prompt", {}).get("enable_thinking")

    teacher_cfg = cfg.get("teacher") or {}
    moral_value_name = teacher_cfg.get("moral_value", "none")
    moral_value_text = get_moral_value(moral_value_name)
    prompt_wrapper = None
    if moral_value_text:
        template_source = teacher_cfg.get("template_source")
        if not template_source:
            raise ValueError(
                f"config sets teacher.moral_value={moral_value_name!r} but no "
                f"teacher.template_source (path to the SDPO training yaml the "
                f"reprompt_template is read from)"
            )
        template = load_reprompt_template(template_source)
        prompt_wrapper = partial(
            wrap_prompt,
            reprompt_template=template,
            moral_value_text=moral_value_text,
            feedback_template=teacher_cfg.get("feedback_template"),
        )
        logger.info("Teacher context: moral_value=%s, template from %s",
                    moral_value_name, template_source)

    base_model = cfg["policy"]["model_name"]
    logger.info("Loading model from %s (base: %s)", checkpoint or "base", base_model)
    model, tokenizer = load_model_for_eval(checkpoint, base_model)
    # Multi-round episodes are conversations (verl multi-turn parity):
    # run_episode sends env messages for rounds >= 2, so the policy must
    # accumulate the dialogue. Single-round = one-shot stateless policy.
    if cfg["game"]["num_rounds"] > 1:
        policy_fn = make_chat_policy_fn(
            model, tokenizer, max_new_tokens=max_new_tokens,
            temperature=temperature, raw_log=raw_log,
            prompt_wrapper=prompt_wrapper,
            wrap_position=teacher_cfg.get("wrap_position", "first"),
            enable_thinking=enable_thinking,
        )
        logger.info("Multi-round: conversation policy (wrap_position=%s), "
                    "episode dialogues accumulate (verl multi-turn parity)",
                    teacher_cfg.get("wrap_position", "first"))
    else:
        policy_fn = make_policy_fn(
            model, tokenizer, max_new_tokens=max_new_tokens,
            temperature=temperature, raw_log=raw_log,
            prompt_wrapper=prompt_wrapper,
            enable_thinking=enable_thinking,
        )
    logger.info("Decoding: %s, max_new_tokens=%d",
                "greedy" if not (temperature and temperature > 0)
                else f"sampling T={temperature} (top_k=0, top_p=1.0)",
                max_new_tokens)
    return policy_fn


def run_opponent(
    cfg: Dict, opponent: str, policy_fn, presentation_rng: random.Random,
) -> Dict:
    """Run all episodes against one opponent; return its result block.

    The presentation stream is consumed continuously across the opponents
    loop, so runs are paired only if their opponent lists match — don't
    compare a multi-opponent run against single-opponent (--opponent)
    sweeps of the same cells.
    """
    eval_cfg = cfg.get("evaluation", {})
    num_episodes = eval_cfg.get("num_episodes", 20)
    prompt_cfg = cfg.get("prompt", {})
    game_design = prompt_cfg.get("game_design", "hist")
    lambda_val = cfg["reward"]["lambda"]
    intrinsic_type = cfg["reward"]["intrinsic"]
    game_reward_type = cfg["reward"].get("game_reward", "raw")
    shaping = cfg["reward"].get("shaping") or {}

    # 'balanced' (default) cycles the game's fabricated-state grid
    # deterministically (n/len per state, identical every run — 4 states
    # for 2x2 games, 2N for PGG); 'random' = legacy uniform draw in
    # run_episode.
    state_design = eval_cfg.get("state_design", "balanced")
    fabricate = game_design == "hist"

    logger.info("Evaluating vs %s (%d episodes)", opponent, num_episodes)
    trajectories: List[TrajectoryResult] = []
    episode_configs: List[EpisodeConfig] = []
    for ep_idx in range(num_episodes):
        if hasattr(policy_fn, "reset"):
            policy_fn.reset()   # fresh conversation per episode
        config = build_eval_config(cfg, opponent, rng=presentation_rng)
        episode_configs.append(config)
        states = get_game(config.game_type).fab_states(config)
        if ep_idx == 0 and fabricate and state_design == "balanced" \
                and num_episodes % len(states):
            logger.warning(
                "num_episodes=%d not divisible by %d fabricated states — "
                "the balanced design is uneven", num_episodes, len(states))
        fab_state = (states[ep_idx % len(states)]
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

    result = aggregate_rollout_metrics(trajectories, opponent, num_episodes)
    breakdown = per_round_breakdown(trajectories)
    result["per_round"] = breakdown["per_round"]
    result["top_sequences"] = breakdown["top_sequences"]
    # Per-episode move sequences ('illegal' preserved): raw material for
    # offline dynamics metrics (recovery_rate.py). The presentation is
    # attached per episode ONLY when an axis is randomized (what offline
    # per-axis robustness slicing consumes); in fixed runs every episode
    # is identical, so it is written once at the result level instead.
    result["episode_moves"] = [
        {"agent": t.agent_moves, "opp": t.opponent_moves}
        for t in trajectories
    ]
    randomized = any(
        eval_cfg.get(ax, "fixed") != "fixed"
        for ax in ("labels", "layout", "label_order", "role", "payoffs")
    )
    if randomized:
        for ep, c in zip(result["episode_moves"], episode_configs):
            ep["presentation"] = _presentation(c)
    else:
        result["presentation"] = _presentation(episode_configs[0])
    return result


def _presentation(c: EpisodeConfig) -> Dict:
    """JSON-serializable record of how one episode was rendered."""
    out = {
        "representation": c.representation,
        "coop_label": c.coop_label, "defect_label": c.defect_label,
        "matrix_layout": c.matrix_layout,
        "opener_order": list(c.opener_order),
        "closer_order": list(c.closer_order),
        "agent_is_row": c.agent_is_row,
        "payoffs": {"T": c.T, "R": c.R, "P": c.P, "S": c.S},
    }
    if c.game_type == "public_goods":
        out["payoffs"] = {"n_players": c.n_players,
                          "endowment": c.endowment, "share": c.share}
    return out


def evaluate(cfg: Dict, checkpoint: Optional[str], raw_log: Optional[list] = None):
    """Run multi-turn rollout evaluation against each configured opponent."""
    presentation_rng = seed_streams(cfg)
    policy_fn = build_policy(cfg, checkpoint, raw_log)
    opponents = cfg.get("evaluation", {}).get(
        "opponents", ["tit_for_tat", "always_defect"]) #TODO why was always defect not evaluated?

    all_results = []
    for opp in opponents:
        result = run_opponent(cfg, opp, policy_fn, presentation_rng)
        all_results.append(result)
        _print_summary(opp, result)
    return all_results


def _print_summary(opp: str, result: Dict) -> None:
    """Human-readable console summary of one opponent's result block."""
    def pct(x):  # rates are None when every episode lacked legal data
        return "n/a (no legal data)" if x is None else f"{x:.1%}"

    print(f"\nvs {opp}:")
    print(f"  Cooperation rate:        {pct(result['cooperation_rate'])}"
          f" (± {pct(result['cooperation_rate_std'])})")
    if result["num_episodes_all_illegal"]:
        print(f"  Episodes excluded (all moves illegal): "
              f"{result['num_episodes_all_illegal']}/{result['num_episodes']}")
    print(f"  Mutual cooperation rate: {pct(result['mutual_cooperation_rate'])}")
    print(f"  Exploitation rate:       {pct(result['exploitation_rate'])}")
    print(f"  Sucker rate:             {pct(result['sucker_rate'])}")
    print(f"  Mutual defection rate:   {pct(result['mutual_defection_rate'])}")
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


def _parse_training_seed(*candidates: Optional[str]) -> Optional[int]:
    """Training seed from checkpoint path or MORALGYM_RUN_NAME (the
    `_seed<N>_` tag set by train.sh). None for base-model eval or
    untagged runs (config's default grpo.seed)."""
    for c in candidates:
        if c:
            m = re.search(r"_seed(\d+)(?:_|$)", str(c))
            if m:
                return int(m.group(1))
    return None


def _base_label(model_name: str) -> str:
    """Base-eval experiment label from model_name. The named mappings are
    grouping keys the plotting scripts match on — change them only
    together with those scripts."""
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate a trained MoralGym model")
    parser.add_argument("--config", type=str, required=True)
    parser.add_argument("--checkpoint", type=str, required=True,
                        help="LoRA adapter or full-model checkpoint path, or "
                             "'base' for the untuned base model")
    parser.add_argument("--model", type=str, default=None,
                        help="Override policy.model_name (HF id or local "
                             "path). Lets one eval config screen several base "
                             "models as a sweep axis instead of duplicating "
                             "the yaml per model. The probes take the same "
                             "flag, so a cell's behavioral and probe results "
                             "always describe the same weights.")
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
                        choices=sorted(FIXED_PAYOFFS) + ["public_goods"],
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
    parser.add_argument("--representation", type=str, default=None,
                        choices=["matrix", "prose", "list", "table",
                                 "decision"],
                        help="Override prompt.representation: how the payoff "
                             "block is rendered. 'matrix'/'table' = markdown "
                             "table (default); 'prose' = the outcomes as one "
                             "flowing paragraph; 'list' = same sentences "
                             "bulleted -- these three carry identical "
                             "information and isolate FORMAT. 'decision' "
                             "(public_goods only) additionally prepends the "
                             "agent-centric lookup table indexed by the "
                             "OTHERS' count, so the agent's own payoff is read "
                             "rather than projected (docs/pgg_design.md §9.7); "
                             "it is an information change, not a format one. "
                             "Distinct from --eval-label-order (opener/closer "
                             "label order).")
    parser.add_argument("--game-description", type=_bool_arg, default=None,
                        help="public_goods only: prepend the mechanism "
                             "preamble to the table/prose payoff block "
                             "(docs/pgg_design.md §9.4). on|off.")
    parser.add_argument("--restate-rules", type=_bool_arg, default=None,
                        metavar="true|false",
                        help="Override prompt.restate_rules_per_round "
                             "(multi-round only): true = rounds >= 2 re-insert "
                             "the payoff block, false (default) = outcome + "
                             "question + answer format only. The "
                             "rules-retention ablation for weaker models. "
                             "Value-taking rather than --flag/--no-flag so it "
                             "works as a sweep axis.")
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
    parser.add_argument("--presentation", type=str, default=None,
                        metavar="SPEC",
                        help="Which presentation axes to randomize. A preset "
                             f"({', '.join(sorted(PRESENTATION_PRESETS))}) or "
                             "a '+'-joined list of axes "
                             f"({', '.join(sorted(PRESENTATION_AXES))}), e.g. "
                             "'labels+role'. surface_randomization covers the "
                             "four axes that re-render an IDENTICAL game; "
                             "full_randomization adds payoff resampling, "
                             "which changes the game's magnitudes. Writes "
                             "every axis explicitly, so the spec fully "
                             "determines the presentation block. Applied "
                             "before the individual --eval-* flags, which "
                             "still win. Probes are unaffected — they always "
                             "run fixed presentation by design.")
    parser.add_argument("--eval-labels", type=str, default=None,
                        choices=["fixed", "randomize"],
                        help="Override evaluation.labels (action-label symbols). "
                             "'fixed' → action3/action4 (Tennant-exact, "
                             "default); 'randomize' → sample A–Z per episode "
                             "(robust-generalization sensitivity).")
    parser.add_argument("--eval-layout", type=str, default=None,
                        choices=["fixed", "randomize"],
                        help="Override evaluation.layout. 'fixed' → layout=0 "
                             "(Tennant-exact, default); 'randomize' → permute "
                             "matrix rows/cols per episode.")
    parser.add_argument("--eval-label-order", type=str, default=None,
                        choices=["fixed", "randomize"],
                        help="Override evaluation.label_order (label order in "
                             "the opener/closer sentences). 'fixed' → (coop, "
                             "defect) order (default); 'randomize' → "
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
    parser.add_argument("--save-raw-responses", action="store_true",
                        help="Save every (prompt, raw model output) pair to a "
                             "sibling JSONL file (<output>.responses.jsonl). "
                             "Use for debugging unexpected parse failures or "
                             "comparing eval-time generations against training "
                             "logs. Off by default — adds ~50-200 KB per eval.")
    return parser


def apply_overrides(cfg: Dict, args: argparse.Namespace) -> None:
    """Apply protocol preset and CLI overrides to cfg in place.

    Protocol preset first — individual CLI overrides below still win.
    """
    if args.protocol is not None:
        apply_protocol(cfg, args.protocol)

    # Presentation spec before the individual --eval-* flags below, so an
    # explicit flag still wins (same precedence rule as protocol).
    if args.presentation is not None:
        apply_presentation(cfg, args.presentation)

    # --game / --opponent let one config evaluate any (game, opponent) cell.
    # Needed for cross-game / cross-opponent sweeps driven by eval_plan.sh.
    if args.game is not None:
        cfg["game"]["type"] = args.game
        cfg["game"]["sample_payoffs"] = False
        if args.game == "public_goods":
            # Canonical (N, E, s) unless the YAML already pinned them
            # (docs/pgg_design.md §3.1); no payoff matrix.
            cfg["game"].pop("payoffs", None)
            for key, value in PGG_PARAMS["canonical"].items():
                cfg["game"].setdefault(key, value)
        else:
            cfg["game"]["payoffs"] = dict(FIXED_PAYOFFS[args.game])
    if args.opponent is not None:
        cfg.setdefault("evaluation", {})["opponents"] = [args.opponent]

    # Everything else is a plain one-key override (CFG_OVERRIDES).
    for arg_name, section, key in CFG_OVERRIDES:
        value = getattr(args, arg_name)
        if value is not None:
            cfg.setdefault(section, {})[key] = value


def build_metadata(
    cfg: Dict, args: argparse.Namespace, checkpoint: Optional[str],
) -> Dict:
    """Provenance metadata block written alongside the rollout results.

    Defaults must mirror evaluate()'s .get() fallbacks: metadata is built
    AFTER the expensive eval and must never raise on a config evaluate()
    tolerated (enforced by tests/test_behavioral_metadata.py).
    """
    # __mv_ suffix keeps teacher-signal runs distinguishable from the
    # plain baseline in tooling that groups by experiment_name.
    moral_value = cfg.get("teacher", {}).get("moral_value", "none")
    experiment_name = (
        _base_label(cfg["policy"]["model_name"]) if checkpoint is None
        else cfg.get("experiment_name", "unknown")
    )
    if moral_value != "none":
        experiment_name = f"{experiment_name}__mv_{moral_value}"

    eval_block = cfg.get("evaluation", {})
    return {
        "experiment_name": experiment_name,
        "protocol": args.protocol or "custom",
        "moral_value": moral_value,
        "teacher_template_source": cfg.get("teacher", {}).get("template_source"),
        "model_type": "base" if checkpoint is None else "finetuned",
        "checkpoint": args.checkpoint,
        "base_model": cfg["policy"]["model_name"],
        "game_type": cfg["game"]["type"],
        "game_design": cfg.get("prompt", {}).get("game_design", "hist"),
        "representation": cfg.get("prompt", {}).get("representation", "matrix"),
        "num_episodes": eval_block.get("num_episodes", 20),
        "num_rounds": cfg["game"]["num_rounds"],
        "intrinsic": cfg["reward"]["intrinsic"],
        "lambda": cfg["reward"]["lambda"],
        "game_reward": cfg["reward"].get("game_reward", "raw"),
        "eval_temperature": eval_block.get("temperature", 1.0),
        "eval_max_new_tokens": eval_block.get("max_new_tokens", 10),
        "minimal_parsing": cfg.get("prompt", {}).get("minimal_parsing", False),
        "reasoning": cfg.get("prompt", {}).get("reasoning", False),
        # Hybrid-thinking switch (Qwen3) and the PGG mechanism preamble:
        # both change what the model sees, so a result file must say.
        "enable_thinking": cfg.get("prompt", {}).get("enable_thinking"),
        "game_description": cfg.get("prompt", {}).get("game_description", False),
        "show_horizon": cfg.get("prompt", {}).get("show_horizon", False),
        # Multi-round rules-retention arm; inert at num_rounds=1 (no round >= 2),
        # but recorded unconditionally so cells stay distinguishable.
        "restate_rules_per_round": cfg.get("prompt", {}).get(
            "restate_rules_per_round", False),
        # The --presentation spec as given (None when the config's own
        # evaluation block governs). eval_presentation below is the
        # RESOLVED state and stays the authority; this records the intent.
        "presentation_spec": args.presentation,
        # fixed = Tennant-exact; randomize/sample = robustness protocol.
        "eval_presentation": {
            axis: eval_block.get(axis, default)
            for axis, default in [("labels", "fixed"), ("layout", "fixed"),
                                  ("label_order", "fixed"), ("role", "fixed"),
                                  ("payoffs", "fixed")]
        },
        "state_design": eval_block.get("state_design", "balanced"),
        "run_name": os.environ.get("MORALGYM_RUN_NAME"),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        # eval_seed drives this run's RNG; training_seed identifies the
        # training run that produced the checkpoint (None for base eval).
        "eval_seed": cfg.get("seed", 42),
        "training_seed": _parse_training_seed(
            args.checkpoint, os.environ.get("MORALGYM_RUN_NAME")
        ),
        "timestamp": datetime.now().isoformat(),
        # Provenance: ties the result file to the exact eval code and
        # experiment description that produced it.
        "git_commit": git_provenance(),
        "config": args.config,
    }


def main():
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    apply_overrides(cfg, args)

    checkpoint = None if args.checkpoint == "base" else args.checkpoint
    raw_log = [] if args.save_raw_responses else None
    rollout_results = evaluate(cfg, checkpoint, raw_log=raw_log)

    output = {
        "metadata": build_metadata(cfg, args, checkpoint),
        "opponents": rollout_results,
    }
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
