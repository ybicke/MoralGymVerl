"""Evaluation harness for trained models.

Usage:
    python -m moralgym_verl.eval.behavioral \
        --config configs/nemo_rl/a1_hist_norm.yaml \
        --checkpoint $STORAGE_ROOT/results/a1_hist_norm_<jobid>/step_50/policy/weights/model

Runs the model against each evaluation opponent for multiple episodes
and reports cooperation metrics. Works with any HuggingFace-compatible
checkpoint (full model or LoRA adapter).
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np
import torch
import yaml
from transformers import AutoModelForCausalLM, AutoTokenizer

from moralgym_verl.eval.baselines import compute_regret
from moralgym_verl.eval.scoring import iter_scored_decisions
from moralgym_verl.eval.teacher_context import (
    load_reprompt_template, wrap_first_user, wrap_latest_user, wrap_prompt,
)
from moralgym_verl.game.moral_values import MORAL_VALUE_REGISTRY, get_moral_value
from moralgym_verl.game.environment import (
    FIXED_PAYOFFS, EpisodeConfig, sample_labels, sample_payoffs,
)
from moralgym_verl.game.prompts import build_prompt, parse_action, sample_prompt_randomization
from moralgym_verl.game.trajectory import FAB_STATES, TrajectoryResult, run_episode

MORALITIES = ("game", "deon", "util", "gamedeon")

logger = logging.getLogger(__name__)


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


def load_config(path: str) -> Dict:
    with open(path) as f:
        return yaml.safe_load(f)


def merge_lora_weights(model, checkpoint_path: str):
    """Merge LoRA adapter weights into the base model in-place.

    Computes W' = W + (lora_alpha / r) * lora_B @ lora_A for each adapted layer.
    Same math as PeftModel.merge_and_unload() and the NeMo-RL vLLM refit patch
    (patch 3 in docs/nemorl_patches.md), but without the peft dependency — which
    is not installed in the NeMo-RL container.
    """
    from safetensors.torch import load_file

    ckpt = Path(checkpoint_path)
    with open(ckpt / "adapter_config.json") as f:
        lora_cfg = json.load(f)

    r = lora_cfg["r"]
    alpha = lora_cfg["lora_alpha"]
    scale = alpha / r

    adapters = load_file(ckpt / "adapter_model.safetensors")
    state_dict = model.state_dict()

    merged_count = 0
    for key in list(adapters.keys()):
        if ".lora_B." not in key:
            continue
        a_key = key.replace(".lora_B.", ".lora_A.")
        # PEFT keys: "base_model.model.<module>.lora_B.weight"
        # Base model keys: "<module>.weight"
        base_key = (
            key.replace("base_model.model.", "")
            .replace(".lora_B.weight", ".weight")
        )
        if base_key not in state_dict:
            raise KeyError(
                f"LoRA target '{base_key}' not found in base model. "
                f"Adapter key: '{key}'"
            )
        B = adapters[key].to(device=state_dict[base_key].device)    # (out_dim, r)
        A = adapters[a_key].to(device=state_dict[base_key].device)  # (r, in_dim)
        state_dict[base_key] += (scale * (B @ A)).to(state_dict[base_key].dtype)
        merged_count += 1

    model.load_state_dict(state_dict)
    logger.info("Merged %d LoRA adapters (r=%d, alpha=%d, scale=%.1f)", merged_count, r, alpha, scale)
    return model


def load_model_for_eval(
    checkpoint: Optional[str],
    base_model: Optional[str] = None,
):
    """Load a trained model for evaluation.

    Handles three cases:
    - checkpoint=None + base_model: load base model only (no LoRA)
    - checkpoint + base_model: load LoRA adapter on top of base model
    - checkpoint only: load full-model checkpoint
    """
    if checkpoint is None and base_model:
        model = AutoModelForCausalLM.from_pretrained(
            base_model, torch_dtype=torch.bfloat16, device_map="auto",
        )
        tokenizer = AutoTokenizer.from_pretrained(base_model)
    elif base_model:
        model = AutoModelForCausalLM.from_pretrained(
            base_model, torch_dtype=torch.bfloat16, device_map="auto",
        )
        model = merge_lora_weights(model, checkpoint)
        tokenizer = AutoTokenizer.from_pretrained(base_model)
    else:
        model = AutoModelForCausalLM.from_pretrained(
            checkpoint, torch_dtype=torch.bfloat16, device_map="auto",
        )
        tokenizer = AutoTokenizer.from_pretrained(checkpoint)

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model.eval()
    return model, tokenizer


def render_chat_inputs(tokenizer, messages, device):
    """Chat-template `messages` into generation inputs, training-exact.

    add_special_tokens=False because the rendered template already starts
    with <bos> — the HF default would prepend a second one, deviating from
    verl's training tokenization. Returns (rendered_text, model inputs).
    """
    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
    )
    inputs = tokenizer(
        text, return_tensors="pt", add_special_tokens=False,
    ).to(device)
    return text, inputs


def make_policy_fn(
    model,
    tokenizer,
    max_new_tokens: int = 10,
    temperature: Optional[float] = 1.0,
    raw_log: Optional[list] = None,
    prompt_wrapper=None,
):
    """Policy function for run_episode.

    Default: T=1.0 multinomial sampling with top_k=0, top_p=1.0 — mirrors
    Tennant's `respond_to_batch(..., top_k=0, top_p=1.0)` from trl.core
    (used in every generation call in her inference_vsRandom.py). This
    measures the stochastic policy the model was trained to emit, and
    matches GovSim-style deployment where greedy NL decoding can degenerate.

    Pass `temperature=0` (or None) to fall back to greedy argmax.

    If `raw_log` is a list, each call appends a dict
    {"prompt": <user_text>, "raw": <model_output>} for offline inspection
    (e.g. debugging unexpected parse failures on hyper-peaked policies).

    `prompt_wrapper` (teacher-signal eval) is applied to the game prompt
    before chat templating — raw_log therefore records the wrapped
    prompt, i.e. exactly what the model saw.
    """

    use_sampling = temperature is not None and temperature > 0

    @torch.no_grad()
    def policy_fn(prompt: str) -> str:
        if prompt_wrapper is not None:
            prompt = prompt_wrapper(prompt)
        messages = [{"role": "user", "content": prompt}]
        _, inputs = render_chat_inputs(tokenizer, messages, model.device)
        gen_kwargs = {"max_new_tokens": max_new_tokens, "do_sample": use_sampling}
        if use_sampling:
            # top_k=0 / top_p=1.0 explicitly override HF's defaults (top_k=50)
            # so sampling is from the full unclipped distribution, matching
            # Tennant's torch.multinomial(F.softmax(logits)) path.
            gen_kwargs["temperature"] = temperature
            gen_kwargs["top_k"] = 0
            gen_kwargs["top_p"] = 1.0
        outputs = model.generate(**inputs, **gen_kwargs)
        generated = outputs[0][inputs["input_ids"].shape[1]:]
        raw = tokenizer.decode(generated, skip_special_tokens=True)
        if raw_log is not None:
            raw_log.append({"prompt": prompt, "raw": raw})
        return raw

    return policy_fn


def make_chat_policy_fn(
    model,
    tokenizer,
    max_new_tokens: int = 10,
    temperature: Optional[float] = 1.0,
    raw_log: Optional[list] = None,
    prompt_wrapper=None,
    wrap_position: str = "first",
):
    """Transcript-mode policy for multi-turn eval (Stage 1b).

    Mirrors verl's multi-turn agent loop (SDPO tool_agent_loop.py): the
    episode conversation accumulates — every prior round's user message
    AND the model's own responses stay in context, so history-dependent
    behavior (grudges, forgiveness) is expressible exactly as in training.

    Teacher semantics: the stored transcript is always plain; when
    `prompt_wrapper` is set, `wrap_position` decides where the moral
    value appears at generation time:
      'first'  (default) — wrap only the episode's FIRST user turn.
                Training-exact: multi-turn SDPO wraps raw_prompt (the
                initial message); all later rounds are shared response-
                region tokens (see teacher_context.wrap_first_user).
      'latest' — wrap the current round's user turn (persistent-context
                ablation; measures the "value always adjacent" variant).

    The caller MUST call `policy_fn.reset()` between episodes (evaluate()
    does) — otherwise conversations leak across episodes.
    """
    if wrap_position not in ("first", "latest"):
        raise ValueError(f"wrap_position must be 'first' or 'latest', "
                         f"got {wrap_position!r}")

    use_sampling = temperature is not None and temperature > 0
    state: Dict[str, list] = {"messages": []}
    wrap_fn = wrap_first_user if wrap_position == "first" else wrap_latest_user

    @torch.no_grad()
    def policy_fn(prompt: str) -> str:
        state["messages"].append({"role": "user", "content": prompt})
        gen_messages = (
            wrap_fn(state["messages"], prompt_wrapper)
            if prompt_wrapper is not None else state["messages"]
        )
        text, inputs = render_chat_inputs(tokenizer, gen_messages, model.device)
        gen_kwargs = {"max_new_tokens": max_new_tokens, "do_sample": use_sampling}
        if use_sampling:
            gen_kwargs["temperature"] = temperature
            gen_kwargs["top_k"] = 0
            gen_kwargs["top_p"] = 1.0
        outputs = model.generate(**inputs, **gen_kwargs)
        generated = outputs[0][inputs["input_ids"].shape[1]:]
        raw = tokenizer.decode(generated, skip_special_tokens=True)
        state["messages"].append({"role": "assistant", "content": raw})
        if raw_log is not None:
            # Log the fully rendered context, not just the last message —
            # the transcript the model actually saw is then inspectable.
            raw_log.append({"prompt": text, "raw": raw})
        return raw

    policy_fn.reset = lambda: state["messages"].clear()
    return policy_fn


def build_eval_config(
    cfg: Dict, opponent: str, rng: Optional[random.Random] = None,
) -> EpisodeConfig:
    """Build an EpisodeConfig for evaluation.

    All presentation draws go through `rng` when given. `evaluate()`
    passes a dedicated stream (seeded independently of everything else)
    so that turning presentation randomization on/off does not perturb
    any other random draws — fixed and randomized runs stay paired.
    Falls back to module `random` (probe callers, fixed presentation —
    which consumes no draws anyway).
    """
    game = cfg["game"]
    prompt_cfg = cfg["prompt"]
    eval_cfg = cfg.get("evaluation", {})

    # Eval defaults to Tennant-exact: fixed action3/action4 tokens, fixed
    # layout (=0), fixed prose order, agent_is_row=True, fixed payoffs.
    # Training-time randomization flags do NOT propagate to eval.
    # Override per-config under the `evaluation:` block:
    #   evaluation.tokens:  fixed | randomize   (default: fixed)
    #   evaluation.layout:  fixed | randomize   (default: fixed) — matrix grid permutation
    #   evaluation.prose:   fixed | randomize   (default: fixed) — opener/closer label order
    #   evaluation.role:    fixed | randomize   (default: fixed) — agent_is_row coin flip
    #   evaluation.payoffs: fixed | sample      (default: fixed)

    randomize_layout = eval_cfg.get("layout", "fixed") == "randomize"
    randomize_prose = eval_cfg.get("prose", "fixed") == "randomize"
    randomize_role = eval_cfg.get("role", "fixed") == "randomize"

    r = rng if rng is not None else random

    if eval_cfg.get("tokens", "fixed") == "randomize":
        cl, dl = sample_labels(rng=rng)
    else:
        cl, dl = "action3", "action4"

    layout = r.randint(0, 3) if randomize_layout else 0

    if eval_cfg.get("payoffs", "fixed") == "sample":
        T, R, P, S = sample_payoffs(game["type"], rng=rng)
    else:
        p = game["payoffs"]
        T, R, P, S = p["T"], p["R"], p["P"], p["S"]

    opener_order, closer_order, agent_is_row = sample_prompt_randomization(
        cl, dl,
        randomize_prose=randomize_prose,
        randomize_role=randomize_role,
        rng=rng,
    )

    return EpisodeConfig(
        game_type=game["type"],
        T=T, R=R, P=P, S=S,
        opponent=opponent,
        num_rounds=game["num_rounds"],
        coop_label=cl,
        defect_label=dl,
        matrix_layout=layout,
        opener_order=opener_order,
        closer_order=closer_order,
        agent_is_row=agent_is_row,
        show_horizon=prompt_cfg.get("show_horizon", False),
        minimal_parsing=prompt_cfg.get("minimal_parsing", False),
        reasoning=prompt_cfg.get("reasoning", False),
    )


def _three_category(moves: List[str]) -> Dict:
    """Distribution over {C, D, illegal} plus sample size.

    Empty input returns all-zero probabilities with n=0 so callers can emit the
    key unconditionally; downstream consumers gate on `n > 0`.
    """
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
    """Yield (round_idx, agent_prev, opp_prev, agent_move) for each round with
    a prior legal state to condition on. round_idx is 1-indexed and points to
    the round of the yielded move (the decision being conditioned).

    `last_legal` is seeded from fabricated history (if any) and only advances
    on legal (C/D, C/D) rounds. After an illegal round the state is frozen —
    so the next legal decision is conditioned on the same prior pair the
    opponent policy saw. Cold-start round 1 has no prior and is skipped.
    """
    last_agent = result.fab_agent
    last_opp = result.fab_opp
    for idx, (move, opp_move) in enumerate(
        zip(result.agent_moves, result.opponent_moves), start=1
    ):
        if last_agent is not None and last_opp is not None:
            yield idx, last_agent, last_opp, move
        if move in ("C", "D") and opp_move in ("C", "D"):
            last_agent, last_opp = move, opp_move


def _aggregate_rollout_metrics(
    results: List[TrajectoryResult],
    opponent: str,
    num_episodes: int,
) -> Dict:
    """Aggregate metrics across completed rollout episodes.

    Illegal (parse-failure) decisions are tracked as a third category rather
    than collapsed into D — see spec docs/experimental/eval_implementation_spec.md.
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
        # Backward-compat scalars (reciprocity / forgiveness); null if unconditioned.
        "p_c_given_opp_c": cond_opp_c["p_C"] if cond_opp_c["n"] else None,
        "p_c_given_opp_d": cond_opp_d["p_C"] if cond_opp_d["n"] else None,
        "state_conditioning": state_conditioning or None,
        "per_round_state_conditioning": per_round_state_conditioning or None,
        **reward_block,
    }


def _score_rewards(results: List[TrajectoryResult]) -> Dict:
    """Compute mean reward streams and regrets per morality.

    Emits two versions of each metric (see eval_implementation_spec.md §C):
      - primary (mean_r_*, regret_*): includes illegal decisions (r_m=-6),
        matches Tennant's scale — directly comparable to her Figure 5.
      - legal-only (mean_r_*_legal, regret_*_legal): parseable decisions
        only, isolates moral signal from parseability.
    """
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


def per_round_breakdown(results: List[TrajectoryResult]) -> Dict:
    """Compute per-round move distributions and most common move sequences.

    Per-round entries use the same three-category convention ({C, D,
    illegal} via _three_category) as the rest of the metrics — illegal
    moves are NOT folded into D.
    """
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
    from collections import Counter
    seq_counts = Counter(sequences)
    total = len(sequences)
    top_sequences = [
        {"sequence": seq, "count": cnt, "fraction": cnt / total}
        for seq, cnt in seq_counts.most_common(5)
    ]

    return {"per_round": per_round, "top_sequences": top_sequences}


# Probe-mode evaluation was removed — see eval_implementation_spec.md §8.
# Rollouts against the Random opponent give equivalent balanced state coverage
# for the 2-way conditional P(C|opp_prev) while exercising the full policy.


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

    eval_cfg = cfg["evaluation"]
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

        result = _aggregate_rollout_metrics(trajectories, opp, num_episodes)
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

        print(f"\nvs {opp}:")
        print(f"  Cooperation rate:        {result['cooperation_rate']:.1%}"
              f" (± {result['cooperation_rate_std']:.1%})")
        print(f"  Mutual cooperation rate: {result['mutual_cooperation_rate']:.1%}")
        print(f"  Exploitation rate:       {result['exploitation_rate']:.1%}")
        print(f"  Sucker rate:             {result['sucker_rate']:.1%}")
        print(f"  Mutual defection rate:   {result['mutual_defection_rate']:.1%}")
        print(f"  Mean reward:             {result['mean_reward']:.3f}"
              f" (± {result['mean_reward_std']:.3f})")
        if result["p_c_given_opp_c"] is not None:
            print(f"  P(C | opp prev C):       {result['p_c_given_opp_c']:.1%}  # reciprocity")
        if result["p_c_given_opp_d"] is not None:
            print(f"  P(C | opp prev D):       {result['p_c_given_opp_d']:.1%}  # forgiveness")
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

    return all_results


def main():
    parser = argparse.ArgumentParser(description="Evaluate a trained MoralGym model")
    parser.add_argument("--config", type=str, required=True)
    parser.add_argument("--checkpoint", type=str, required=True,
                        help="Path to LoRA checkpoint, or 'base' for untuned model")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSON path (overrides config output_dir)")
    parser.add_argument("--num-episodes", type=int, default=None,
                        help="Number of episodes per opponent (overrides config)")
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
    parser.add_argument("--hist-coop-bias", type=float, default=None,
                        help="Override prompt.hist_coop_bias. Forces the fab_opp "
                             "sampling distribution at eval, independent of the "
                             "model's training-time bias. Use 0.5 for fair "
                             "head-to-head across models trained at different "
                             "biases (exposes all 4 fab cells uniformly).")
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
    parser.add_argument("--transcript", type=str, default=None,
                        choices=["true", "false"],
                        help="Override evaluation.transcript. 'true' = the "
                             "episode conversation accumulates across rounds "
                             "(verl multi-turn training parity, Stage 1b); "
                             "'false' = stateless Markov-1 prompt per round "
                             "(default, Stage 1a).")
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
    if args.num_episodes is not None:
        cfg["evaluation"]["num_episodes"] = args.num_episodes

    # --game / --opponent let one config evaluate any (game, opponent) cell.
    # Needed for cross-game / cross-opponent sweeps driven by eval_plan.sh.
    if args.game is not None:
        cfg["game"]["type"] = args.game
        cfg["game"]["sample_payoffs"] = False
        cfg["game"]["payoffs"] = dict(FIXED_PAYOFFS[args.game])
    if args.opponent is not None:
        cfg.setdefault("evaluation", {})["opponents"] = [args.opponent]

    # --num-rounds / --game-design force eval off the training protocol
    # (e.g. rolling a 1-round-trained T-model out over 5 rounds without
    # fabricated history). eval_plan.sh passes these from a plan-level
    # protocol_override: block.
    if args.num_rounds is not None:
        cfg["game"]["num_rounds"] = args.num_rounds
    if args.game_design is not None:
        cfg.setdefault("prompt", {})["game_design"] = args.game_design
    if args.hist_coop_bias is not None:
        cfg.setdefault("prompt", {})["hist_coop_bias"] = args.hist_coop_bias
    if args.temperature is not None:
        cfg.setdefault("evaluation", {})["temperature"] = args.temperature
    if args.max_new_tokens is not None:
        cfg.setdefault("evaluation", {})["max_new_tokens"] = args.max_new_tokens
    if args.eval_tokens is not None:
        cfg.setdefault("evaluation", {})["tokens"] = args.eval_tokens
    if args.eval_layout is not None:
        cfg.setdefault("evaluation", {})["layout"] = args.eval_layout
    if args.eval_prose is not None:
        cfg.setdefault("evaluation", {})["prose"] = args.eval_prose
    if args.eval_role is not None:
        cfg.setdefault("evaluation", {})["role"] = args.eval_role
    if args.eval_payoffs is not None:
        cfg.setdefault("evaluation", {})["payoffs"] = args.eval_payoffs
    if args.moral_value is not None:
        cfg.setdefault("teacher", {})["moral_value"] = args.moral_value
    if args.transcript is not None:
        cfg.setdefault("evaluation", {})["transcript"] = args.transcript == "true"

    checkpoint = None if args.checkpoint == "base" else args.checkpoint
    raw_log = [] if args.save_raw_responses else None
    rollout_results = evaluate(cfg, checkpoint, raw_log=raw_log)

    import os as _os
    # Base-eval label derives from model_name so distinct base models stay
    # distinguishable in plots that group by experiment_name. Preserves
    # bare "base" for google/gemma-2-2b-it to keep older eval JSONs/plots
    # backward-compatible (that was the default base before multi-model evals).
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
        # state, paired across every run); 'random' = legacy uniform draw
        # (pre-2026-07 runs, multinomial n per state).
        "state_design": cfg.get("evaluation", {}).get("state_design",
                                                      "balanced"),
        "run_name": _os.environ.get("MORALGYM_RUN_NAME"),
        "slurm_job_id": _os.environ.get("SLURM_JOB_ID"),
        # Two distinct seeds: eval_seed drives this evaluation's RNG streams;
        # training_seed identifies which training run produced the checkpoint
        # (None for base-model eval). Was ambiguously a single "seed" key.
        "eval_seed": cfg.get("seed", 42),
        "training_seed": _parse_training_seed(
            args.checkpoint, _os.environ.get("MORALGYM_RUN_NAME")
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
