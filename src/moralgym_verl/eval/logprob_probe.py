"""Teacher-signal logprob probe (Session 1, complements the behavioral eval).

The behavioral eval (behavioral.py --moral-value ...) measures what the
morally-prompted model PLAYS. This probe measures what SDPO will actually
DISTILL: the difference between the model's token distributions under the
plain (student) prompt and the moral (teacher) prompt — two forward passes
per measurement, no sampling noise.

Probe states: the decision is conditioned on a fabricated previous round
(agent_prev, opp_prev) in {CC, CD, DC, DD}, plus the no-history first
round. Reciprocity in logit space = a state-dependent sign flip of the
log-odds shift: toward C in opp-C states, NOT toward C in opp-D states.
A uniformly positive shift = the unconditional-cooperator failure mode.

Two probe levels:

A. answer_token_probe (non-reasoning prompt, closer ends "Your answer:"):
   For both action labels, teacher-force the label continuation and sum
   its token logprobs under the student and teacher prompts. Report
     logodds  = logP(coop) - logP(defect)        (per prompt)
     delta    = logodds_teacher - logodds_student
     jsd      = Jensen-Shannon divergence of the two-way {C, D}
                distributions (the same divergence the SDPO loss uses).

B. trace_probe (reasoning prompt, only when prompt.reasoning is true):
   Sample num_traces reasoning traces from the STUDENT prompt (that is
   what SDPO scores), then teacher-force each full trace under both
   prompts. Report
     mean_token_delta   = mean over traces of (teacher - student) mean
                          per-token logprob — the distillation pressure
                          on the reasoning itself;
     answer_delta_mean  = log-odds shift at the answer position given the
                          SAME student reasoning (trace truncated after
                          its final "Action:" marker) — does moral context
                          flip the decision with the reasoning held fixed?

Usage (inside the moralgym_verl container, 1 GPU):
    python3 -m moralgym_verl.eval.logprob_probe \
        --config configs/eval/teacher_signal_9b.yaml \
        --moral-value deon_no_exploit --game prisoners_dilemma \
        --output eval_results/teacher_signal/pd__deon_no_exploit_probe.json
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import torch
import yaml

from moralgym_verl.eval.behavioral import (
    build_eval_config, load_config, load_model_for_eval, render_chat_inputs,
)
from moralgym_verl.eval.teacher_context import load_reprompt_template, wrap_prompt
from moralgym_verl.game.environment import FIXED_PAYOFFS
from moralgym_verl.game.moral_values import get_moral_value
from moralgym_verl.game.prompts import build_prompt, parse_action
from moralgym_verl.game.prompts_reasoning import find_action_marker

logger = logging.getLogger(__name__)

# (state_name, agent_history, opp_history) — one fabricated prior round.
PROBE_STATES: List[Tuple[str, List[str], List[str]]] = [
    ("first", [], []),
    ("CC", ["C"], ["C"]),   # I cooperated, opp cooperated
    ("CD", ["C"], ["D"]),   # I cooperated, opp defected (sucker)
    ("DC", ["D"], ["C"]),   # I defected, opp cooperated (exploit)
    ("DD", ["D"], ["D"]),   # mutual defection
]


def _chat_prefix(tokenizer, user_text: str, device) -> torch.Tensor:
    """Chat-templated prompt ids — mirrors make_policy_fn exactly."""
    _, inputs = render_chat_inputs(
        tokenizer, [{"role": "user", "content": user_text}], device,
    )
    return inputs.input_ids


@torch.no_grad()
def _continuation_logprob(
    model, tokenizer, prefix_ids: torch.Tensor, continuation: str
) -> Tuple[float, int]:
    """Sum of token logprobs of `continuation` teacher-forced after prefix."""
    cont_ids = tokenizer(
        continuation, add_special_tokens=False, return_tensors="pt"
    ).input_ids.to(prefix_ids.device)
    input_ids = torch.cat([prefix_ids, cont_ids], dim=1)
    logits = model(input_ids).logits
    logprobs = torch.log_softmax(logits[:, :-1].float(), dim=-1)
    targets = input_ids[:, 1:]
    token_lp = logprobs.gather(-1, targets.unsqueeze(-1)).squeeze(-1)
    cont_lp = token_lp[:, prefix_ids.shape[1] - 1:]
    return cont_lp.sum().item(), cont_lp.shape[1]


def _two_way_jsd(lp_c1: float, lp_d1: float, lp_c2: float, lp_d2: float) -> float:
    """JSD between the two-way {C, D} distributions implied by sequence
    logprobs (renormalized over the label pair, natural log)."""
    def norm(lp_c, lp_d):
        m = max(lp_c, lp_d)
        pc, pd = math.exp(lp_c - m), math.exp(lp_d - m)
        z = pc + pd
        return pc / z, pd / z

    p, q = norm(lp_c1, lp_d1), norm(lp_c2, lp_d2)
    mix = [(pi + qi) / 2 for pi, qi in zip(p, q)]

    def kl(a, b):
        return sum(ai * math.log(ai / bi) for ai, bi in zip(a, b) if ai > 0)

    return 0.5 * kl(p, mix) + 0.5 * kl(q, mix)


def _answer_logodds(
    model, tokenizer, prefix_ids: torch.Tensor, coop_label: str, defect_label: str
) -> Tuple[float, float, float]:
    """(logodds, lp_coop, lp_defect) for ' <label>' continuations."""
    lp_c, _ = _continuation_logprob(model, tokenizer, prefix_ids, f" {coop_label}")
    lp_d, _ = _continuation_logprob(model, tokenizer, prefix_ids, f" {defect_label}")
    return lp_c - lp_d, lp_c, lp_d


def answer_token_probe(model, tokenizer, config, wrapper) -> Dict:
    """Probe A: label logodds shift + JSD per state, non-reasoning closer."""
    cfg = copy.deepcopy(config)
    cfg.reasoning = False
    results = {}
    for state, hist_a, hist_o in PROBE_STATES:
        game_prompt = build_prompt(cfg, hist_a, hist_o)
        student_ids = _chat_prefix(tokenizer, game_prompt, model.device)
        teacher_ids = _chat_prefix(tokenizer, wrapper(game_prompt), model.device)
        lo_s, lp_cs, lp_ds = _answer_logodds(
            model, tokenizer, student_ids, cfg.coop_label, cfg.defect_label)
        lo_t, lp_ct, lp_dt = _answer_logodds(
            model, tokenizer, teacher_ids, cfg.coop_label, cfg.defect_label)
        results[state] = {
            "logodds_student": lo_s,
            "logodds_teacher": lo_t,
            "delta": lo_t - lo_s,
            "p_coop_student": 1 / (1 + math.exp(-lo_s)),
            "p_coop_teacher": 1 / (1 + math.exp(-lo_t)),
            "jsd": _two_way_jsd(lp_cs, lp_ds, lp_ct, lp_dt),
        }
    return results


@torch.no_grad()
def _sample_trace(model, tokenizer, prefix_ids, max_new_tokens, temperature) -> str:
    out = model.generate(
        input_ids=prefix_ids,
        max_new_tokens=max_new_tokens,
        do_sample=temperature > 0,
        temperature=temperature if temperature > 0 else None,
        top_k=0, top_p=1.0,
    )
    return tokenizer.decode(out[0][prefix_ids.shape[1]:], skip_special_tokens=True)


def trace_probe(
    model, tokenizer, config, wrapper,
    num_traces: int, max_new_tokens: int, temperature: float,
    trace_log: Optional[list] = None,
) -> Dict:
    """Probe B: student traces scored under both prompts (= SDPO's teacher
    pass), plus answer logodds shift given the same reasoning.

    If `trace_log` is a list, every sampled trace is appended as
    {"state", "trace", "token_delta", "answer_delta"} so the reasoning
    driving each delta can be inspected offline."""
    cfg = copy.deepcopy(config)
    cfg.reasoning = True
    results = {}
    for state, hist_a, hist_o in PROBE_STATES:
        game_prompt = build_prompt(cfg, hist_a, hist_o)
        student_ids = _chat_prefix(tokenizer, game_prompt, model.device)
        teacher_ids = _chat_prefix(tokenizer, wrapper(game_prompt), model.device)

        token_deltas, answer_deltas = [], []
        n_empty = n_no_marker = n_parse_fail = 0
        for _ in range(num_traces):
            trace = _sample_trace(model, tokenizer, student_ids,
                                  max_new_tokens, temperature)
            if not trace.strip():
                n_empty += 1
                continue
            # Parse exactly as training / behavioral eval would (shared
            # parse_action): None = would be an illegal move in training.
            parsed = parse_action(trace, cfg)
            if parsed is None:
                n_parse_fail += 1
            record = {"state": state, "trace": trace, "parsed_action": parsed,
                      "token_delta": None, "answer_delta": None}
            if trace_log is not None:
                trace_log.append(record)
            # Distillation pressure on the full trace: mean per-token
            # logprob difference, teacher-forced under both prompts.
            lp_s, n_s = _continuation_logprob(model, tokenizer, student_ids, trace)
            lp_t, n_t = _continuation_logprob(model, tokenizer, teacher_ids, trace)
            if n_s:
                record["token_delta"] = lp_t / n_t - lp_s / n_s
                token_deltas.append(record["token_delta"])

            # Answer shift with the reasoning held fixed: truncate the
            # trace at its final answer marker (find_action_marker — the
            # parser's own definition) and compare label logodds there.
            m = find_action_marker(trace)
            if m is None:
                n_no_marker += 1
            else:
                reasoning_prefix = trace[: m.start(1)].rstrip()
                s_pref = torch.cat([student_ids, tokenizer(
                    reasoning_prefix, add_special_tokens=False,
                    return_tensors="pt").input_ids.to(model.device)], dim=1)
                t_pref = torch.cat([teacher_ids, tokenizer(
                    reasoning_prefix, add_special_tokens=False,
                    return_tensors="pt").input_ids.to(model.device)], dim=1)
                lo_s, _, _ = _answer_logodds(
                    model, tokenizer, s_pref, cfg.coop_label, cfg.defect_label)
                lo_t, _, _ = _answer_logodds(
                    model, tokenizer, t_pref, cfg.coop_label, cfg.defect_label)
                record["answer_delta"] = lo_t - lo_s
                answer_deltas.append(record["answer_delta"])

        def stats(xs):
            if not xs:
                return {"mean": None, "std": None, "n": 0}
            mean = sum(xs) / len(xs)
            var = sum((x - mean) ** 2 for x in xs) / len(xs)
            return {"mean": mean, "std": math.sqrt(var), "n": len(xs)}

        results[state] = {
            "token_delta": stats(token_deltas),
            "answer_delta": stats(answer_deltas),
            "n_empty_traces": n_empty,
            "n_no_answer_marker": n_no_marker,
            "n_parse_fail": n_parse_fail,
        }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Teacher-signal logprob probe")
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkpoint", default="base",
                        help="'base' or LoRA checkpoint path (as behavioral.py)")
    parser.add_argument("--moral-value", required=True,
                        help="Wording (or '+'-composite) to probe; not 'none' "
                             "(the probe compares against the plain prompt "
                             "internally).")
    parser.add_argument("--game", default=None, choices=sorted(FIXED_PAYOFFS))
    parser.add_argument("--num-traces", type=int, default=None,
                        help="Trace-probe samples per state (0 disables; "
                             "default from probe.num_traces, else 8).")
    parser.add_argument("--output-dir", default=None,
                        help="Run directory. Writes logprob_a.json "
                             "(answer-token probe), logprob_b.json (trace "
                             "probe stats) and logprob_b.traces.jsonl "
                             "(per-trace records) into it.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    cfg = load_config(args.config)
    if args.game is not None:
        cfg["game"]["type"] = args.game
        cfg["game"]["payoffs"] = dict(FIXED_PAYOFFS[args.game])

    moral_text = get_moral_value(args.moral_value)
    if not moral_text:
        raise SystemExit("--moral-value 'none' is meaningless here: the probe "
                         "always compares against the plain prompt.")

    teacher_cfg = cfg.get("teacher") or {}
    template = load_reprompt_template(teacher_cfg["template_source"])
    feedback_template = teacher_cfg.get("feedback_template")

    def wrapper(prompt: str) -> str:
        return wrap_prompt(prompt, template, moral_text, feedback_template)

    seed = cfg.get("seed", 42)
    random.seed(seed)
    torch.manual_seed(seed)

    checkpoint = None if args.checkpoint == "base" else args.checkpoint
    model, tokenizer = load_model_for_eval(checkpoint, cfg["policy"]["model_name"])

    # Probes are fixed-presentation diagnostics: force any randomized axes
    # off so probe cells stay comparable across values/runs (and never draw
    # from an unpaired RNG stream). Opponent is irrelevant here — the probe
    # conditions on fabricated states.
    randomized = [ax for ax in ("tokens", "layout", "prose", "role", "payoffs")
                  if cfg.get("evaluation", {}).get(ax, "fixed") != "fixed"]
    if randomized:
        logger.warning("Ignoring randomized presentation axes %s — probes "
                       "always run fixed presentation.", randomized)
        for ax in randomized:
            cfg["evaluation"][ax] = "fixed"
    config = build_eval_config(cfg, opponent="tit_for_tat")

    logger.info("Probe A (answer-token) over %d states ...", len(PROBE_STATES))
    probe_a = answer_token_probe(model, tokenizer, config, wrapper)
    for state, r in probe_a.items():
        logger.info("  %s: p(C) %.2f -> %.2f  (delta logodds %+.2f, jsd %.4f)",
                    state, r["p_coop_student"], r["p_coop_teacher"],
                    r["delta"], r["jsd"])

    probe_cfg = cfg.get("probe") or {}
    num_traces = (args.num_traces if args.num_traces is not None
                  else probe_cfg.get("num_traces", 8))
    probe_b = None
    trace_log: list = []
    if cfg.get("prompt", {}).get("reasoning", False) and num_traces > 0:
        eval_cfg = cfg.get("evaluation", {})
        logger.info("Probe B (reasoning traces), %d traces/state ...", num_traces)
        probe_b = trace_probe(
            model, tokenizer, config, wrapper,
            num_traces=num_traces,
            max_new_tokens=eval_cfg.get("max_new_tokens", 256),
            temperature=eval_cfg.get("temperature", 1.0),
            trace_log=trace_log,
        )
        for state, r in probe_b.items():
            logger.info("  %s: token_delta %s, answer_delta %s",
                        state, r["token_delta"]["mean"], r["answer_delta"]["mean"])

    metadata = {
        "moral_value": args.moral_value,
        "base_model": cfg["policy"]["model_name"],
        "checkpoint": args.checkpoint,
        "game_type": cfg["game"]["type"],
        "teacher_template_source": teacher_cfg.get("template_source"),
        "num_traces": num_traces,
        "eval_seed": seed,
        "timestamp": datetime.now().isoformat(),
    }
    out_dir = Path(args.output_dir) if args.output_dir else Path("results")
    out_dir.mkdir(parents=True, exist_ok=True)

    # One file per experiment, named by what it contains.
    path_a = out_dir / "logprob_a.json"
    with open(path_a, "w") as f:
        json.dump({"metadata": {**metadata, "probe": "logprob_a_answer_token"},
                   "answer_token_probe": probe_a}, f, indent=2)
    logger.info("Probe A results saved to %s", path_a)

    if probe_b is not None:
        path_b = out_dir / "logprob_b.json"
        with open(path_b, "w") as f:
            json.dump({"metadata": {**metadata, "probe": "logprob_b_trace"},
                       "trace_probe": probe_b}, f, indent=2)
        traces_path = out_dir / "logprob_b.traces.jsonl"
        with open(traces_path, "w") as f:
            for rec in trace_log:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info("Probe B results saved to %s (+%d traces in %s)",
                    path_b, len(trace_log), traces_path)


if __name__ == "__main__":
    main()
