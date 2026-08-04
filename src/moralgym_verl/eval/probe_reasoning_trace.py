"""Probe B — student reasoning traces scored under teacher vs student prompt.

What SDPO would actually distill: sample traces from the STUDENT prompt
(that is what SDPO scores), teacher-force each under both prompts. Per
state/round: token_delta = (teacher - student) mean per-token logprob
(distillation pressure on the reasoning); answer_delta = label log-odds
shift with the SAME reasoning held fixed (trace truncated at its final
`Action:` marker via the parser's own find_action_marker).

--states fabricated (default): one decision per PROBE_STATES prior round
-> logprob_b.json (+traces). --states episode: live multi-turn episodes,
value wrapped ONLY into the first user message (training-exact for
multi-turn SDPO); per-round deltas show signal decay
-> logprob_multiturn.json (+traces).

Usage (container, 1 GPU):
    python3 -m moralgym_verl.eval.probe_reasoning_trace \
        --config configs/eval/teacher_signal_9b.yaml \
        --moral-value deontological --game prisoners_dilemma \
        --states fabricated --output-dir <run_dir>
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import torch

from moralgym_verl.eval.teacher_context import (
    load_distillation_alpha, wrap_first_user,
)
from moralgym_verl.eval.teacher_forcing import (
    PROBE_STATES, answer_logodds, chat_prefix, chat_prefix_messages,
    delta_stats, dual_continuation_scores, probe_setup, sample_trace,
)
from moralgym_verl.game.environment import FIXED_PAYOFFS
from moralgym_verl.game.players import get_opponent_action
from moralgym_verl.game.prompts import (
    build_prompt, parse_action, parse_failure_feedback,
)
from moralgym_verl.game.prompts_reasoning import find_action_marker

logger = logging.getLogger(__name__)


def _score_trace(
    model, tokenizer, student_ids, teacher_ids, trace: str, alpha: float,
    coop_label: str, defect_label: str,
) -> Dict:
    """Phase-2 scoring of one stored trace: dual teacher-forced pass
    (token_delta + full-vocab generalized JSD = step-0 SDPO per-token
    loss), then the answer log-odds shift with the reasoning held fixed
    (truncated at find_action_marker — the parser's own definition).
    answer_delta is None when the trace has no marker."""
    out: Dict = {"token_delta": None, "token_jsd": None, "answer_delta": None}
    sc = dual_continuation_scores(
        model, tokenizer, student_ids, teacher_ids, trace, alpha)
    if sc["num_tokens"]:
        out["token_delta"] = (
            sc["lp_t"] / sc["num_tokens"] - sc["lp_s"] / sc["num_tokens"])
        out["token_jsd"] = sc["token_jsd"]

    m = find_action_marker(trace)
    if m is not None:
        reasoning_prefix = trace[: m.start(1)].rstrip()
        cut_ids = tokenizer(
            reasoning_prefix, add_special_tokens=False, return_tensors="pt"
        ).input_ids.to(student_ids.device)
        lo_s, _, _ = answer_logodds(
            model, tokenizer, torch.cat([student_ids, cut_ids], dim=1),
            coop_label, defect_label)
        lo_t, _, _ = answer_logodds(
            model, tokenizer, torch.cat([teacher_ids, cut_ids], dim=1),
            coop_label, defect_label)
        out["answer_delta"] = lo_t - lo_s
    return out


def trace_probe(
    model, tokenizer, config, wrapper, alpha: float,
    num_traces: int, max_new_tokens: int, temperature: float,
    trace_log: Optional[list] = None,
) -> Dict:
    """Fabricated-state probe: per PROBE_STATES state, num_traces student
    traces scored under both prompts, plus the answer shift given the
    same reasoning.

    Two phases, mirroring training's rollout->trainer split (see
    docs/notes_gpu_execution_and_determinism.md §7): ALL traces are
    sampled before ANY scoring pass, so scoring-code changes can never
    perturb generation numerics. trace_log (list) collects every sampled
    trace with its scores."""
    cfg = copy.deepcopy(config)
    cfg.reasoning = True

    # Phase 1 — rollout: student prefixes and every trace draw, no scoring.
    prompts: Dict[str, str] = {}
    student_ids: Dict[str, torch.Tensor] = {}
    sampled: List[Tuple[str, str]] = []   # (state, trace) in draw order
    for state, hist_a, hist_o in PROBE_STATES:
        prompts[state] = build_prompt(cfg, hist_a, hist_o)
        student_ids[state] = chat_prefix(
            tokenizer, prompts[state], model.device)
        for _ in range(num_traces):
            sampled.append((state, sample_trace(
                model, tokenizer, student_ids[state],
                max_new_tokens, temperature)))

    # Phase 2 — scoring: teacher prefixes + teacher-forced passes over the
    # stored traces.
    teacher_ids = {
        state: chat_prefix(tokenizer, wrapper(prompts[state]), model.device)
        for state, _, _ in PROBE_STATES
    }
    buckets = {state: {"token_delta": [], "token_jsd": [], "answer_delta": [],
                       "n_empty": 0, "n_no_marker": 0, "n_parse_fail": 0}
               for state, _, _ in PROBE_STATES}
    for state, trace in sampled:
        b = buckets[state]
        if not trace.strip():
            b["n_empty"] += 1
            continue
        # Parse exactly as training / behavioral eval would (shared
        # parse_action): None = would be an illegal move in training.
        parsed = parse_action(trace, cfg)
        if parsed is None:
            b["n_parse_fail"] += 1
        scores = _score_trace(model, tokenizer, student_ids[state],
                              teacher_ids[state], trace, alpha,
                              cfg.coop_label, cfg.defect_label)
        if scores["answer_delta"] is None:
            b["n_no_marker"] += 1
        for key in ("token_delta", "token_jsd", "answer_delta"):
            if scores[key] is not None:
                b[key].append(scores[key])
        if trace_log is not None:
            trace_log.append({"state": state, "trace": trace,
                              "parsed_action": parsed, **scores})

    return {
        state: {
            "token_delta": delta_stats(b["token_delta"]),
            "token_jsd": delta_stats(b["token_jsd"]),
            "answer_delta": delta_stats(b["answer_delta"]),
            "n_empty_traces": b["n_empty"],
            "n_no_answer_marker": b["n_no_marker"],
            "n_parse_fail": b["n_parse_fail"],
        }
        for state, b in buckets.items()
    }


def play_episode(
    model, tokenizer, config, wrapper,
    max_new_tokens: int, temperature: float,
) -> List[Dict]:
    """Phase 1 — rollout: play one full student episode; no scoring
    passes. Transcript stays plain (as in training). Illegal moves freeze
    the game state and prepend the parse-failure reprompt to the next
    round's user message (as trajectory.run_episode / training do;
    `reprompted` marks those rounds). Returns one dict per round with
    everything score_rounds needs; teacher_messages = the transcript so
    far with only the first user turn value-wrapped (training-exact)."""
    messages: List[dict] = []
    agent_history: List[str] = []
    opp_history: List[str] = []
    rounds: List[Dict] = []
    pending_feedback: Optional[str] = None

    for rnd in range(config.num_rounds):
        prompt = build_prompt(config, agent_history, opp_history)
        reprompted = pending_feedback is not None
        if pending_feedback:
            # Training parity (same construction as trajectory.run_episode):
            # after an illegal move, the next user message is prefixed with
            # the parse-failure reprompt — the transcript training would see.
            prompt = pending_feedback + "\n\n" + prompt
            pending_feedback = None
        messages.append({"role": "user", "content": prompt})

        student_ids = chat_prefix_messages(tokenizer, messages, model.device)
        trace = sample_trace(model, tokenizer, student_ids,
                             max_new_tokens, temperature)
        messages.append({"role": "assistant", "content": trace})

        round_rec: Dict = {
            "round": rnd + 1, "reprompted": reprompted, "trace": trace,
            "student_ids": student_ids,
            "teacher_messages": wrap_first_user(messages[:-1], wrapper),
        }

        # Advance the game (mirrors trajectory.run_episode semantics:
        # frozen state + reprompt feedback on the next round).
        action = parse_action(trace, config)
        if action is None:
            pending_feedback = parse_failure_feedback(config)
            round_rec["agent"], round_rec["opp"] = "illegal", None
        else:
            opp_action = get_opponent_action(
                config.opponent, opp_history, agent_history)
            agent_history.append(action)
            opp_history.append(opp_action)
            round_rec["agent"], round_rec["opp"] = action, opp_action
        rounds.append(round_rec)
    return rounds


def score_rounds(
    model, tokenizer, config, rounds: List[Dict], alpha: float,
) -> List[Dict]:
    """Phase 2 — scoring: dual teacher-forced pass per stored round;
    returns the per-round records written to logprob_multiturn."""
    records: List[Dict] = []
    for r in rounds:
        teacher_ids = chat_prefix_messages(
            tokenizer, r["teacher_messages"], model.device)
        scores = _score_trace(model, tokenizer, r["student_ids"], teacher_ids,
                              r["trace"], alpha,
                              config.coop_label, config.defect_label)
        records.append({"round": r["round"], "reprompted": r["reprompted"],
                        "trace": r["trace"], **scores,
                        "agent": r["agent"], "opp": r["opp"]})
    return records


def probe_episode(
    model, tokenizer, config, wrapper, alpha: float,
    max_new_tokens: int, temperature: float,
) -> List[Dict]:
    """One episode end to end: rollout (play_episode) then scoring
    (score_rounds). main() phase-separates across ALL episodes instead of
    calling this — kept for single-episode use and tests."""
    rounds = play_episode(model, tokenizer, config, wrapper,
                          max_new_tokens, temperature)
    return score_rounds(model, tokenizer, config, rounds, alpha)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe B: reasoning traces scored teacher-vs-student")
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkpoint", default="base",
                        help="'base', LoRA adapter, or full-model checkpoint "
                             "path (as behavioral.py)")
    parser.add_argument("--moral-value", required=True,
                        help="Wording (or '+'-composite) to probe; not 'none' "
                             "(the probe compares against the plain prompt "
                             "internally).")
    parser.add_argument("--game", default=None, choices=sorted(FIXED_PAYOFFS))
    parser.add_argument("--states", default="fabricated",
                        choices=["fabricated", "episode"],
                        help="fabricated: single decision per PROBE_STATES "
                             "prior round (default). episode: live multi-turn "
                             "episodes, per-round signal decay.")
    parser.add_argument("--num-traces", type=int, default=None,
                        help="[fabricated] traces per state (default from "
                             "probe.num_traces, else 8).")
    parser.add_argument("--episodes", type=int, default=8,
                        help="[episode] student episodes to generate and score")
    parser.add_argument("--opponent", default="tit_for_tat",
                        help="[episode] opponent for the probe episodes")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Trace-sampling temperature. Overrides "
                             "evaluation.temperature; falls back to 0.7 "
                             "(training-rollout parity) if the config omits "
                             "it. July 2026 references used 1.0 — pass 1.0 "
                             "for comparability runs. Teacher-forced deltas "
                             "themselves are temperature-independent.")
    parser.add_argument("--output-dir", default="results",
                        help="Run directory; writes logprob_b.json (+traces) "
                             "or logprob_multiturn.json (+traces) into it.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    opponent = args.opponent if args.states == "episode" else "tit_for_tat"
    cfg, config, model, tokenizer, wrapper, metadata = probe_setup(
        args, opponent=opponent)
    if not cfg.get("prompt", {}).get("reasoning", False):
        raise SystemExit("Probe B needs prompt.reasoning: true (it scores "
                         "reasoning traces).")

    eval_cfg = cfg.get("evaluation", {})
    max_new_tokens = eval_cfg.get("max_new_tokens", 512)
    temperature = (args.temperature if args.temperature is not None
                   else eval_cfg.get("temperature", 0.7))

    # Divergence weight for token_jsd, read from the SDPO training yaml so
    # the probe measures the training loss verbatim (alpha=0.5 -> JSD).
    alpha = load_distillation_alpha(metadata["teacher_template_source"])
    metadata = {**metadata, "temperature": temperature,
                "distillation_alpha": alpha}
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.states == "fabricated":
        probe_cfg = cfg.get("probe") or {}
        num_traces = (args.num_traces if args.num_traces is not None
                      else probe_cfg.get("num_traces", 8))
        trace_log: list = []
        logger.info("Probe B (fabricated states), %d traces/state ...",
                    num_traces)
        probe_b = trace_probe(
            model, tokenizer, config, wrapper, alpha,
            num_traces=num_traces,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            trace_log=trace_log,
        )
        for state, r in probe_b.items():
            logger.info("  %s: token_delta %s, token_jsd %s, answer_delta %s",
                        state, r["token_delta"]["mean"],
                        r["token_jsd"]["mean"],
                        r["answer_delta"]["mean"])

        path_b = out_dir / "logprob_b.json"
        with open(path_b, "w") as f:
            json.dump({"metadata": {**metadata, "probe": "logprob_b_trace",
                                    "num_traces": num_traces},
                       "trace_probe": probe_b}, f, indent=2)
        traces_path = out_dir / "logprob_b.traces.jsonl"
        with open(traces_path, "w") as f:
            for rec in trace_log:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info("Probe B results saved to %s (+%d traces in %s)",
                    path_b, len(trace_log), traces_path)
        return

    # --states episode: per-round signal decay over live episodes.
    logger.info("Probe B (episode states): %d episodes x %d rounds vs %s ...",
                args.episodes, config.num_rounds, opponent)
    # Phase separation across ALL episodes (training's rollout->trainer
    # split): every episode is played before any scoring pass runs.
    episode_rounds = [
        play_episode(model, tokenizer, config, wrapper,
                     max_new_tokens, temperature)
        for _ in range(args.episodes)
    ]
    all_records: List[Dict] = []
    for ep, rounds in enumerate(episode_rounds):
        records = score_rounds(model, tokenizer, config, rounds, alpha)
        for r in records:
            r["episode"] = ep
        all_records.extend(records)

    per_round = {}
    for rnd in range(1, config.num_rounds + 1):
        rows = [r for r in all_records if r["round"] == rnd]
        per_round[f"round_{rnd}"] = {
            "token_delta": delta_stats([r["token_delta"] for r in rows]),
            "token_jsd": delta_stats([r["token_jsd"] for r in rows]),
            "answer_delta": delta_stats([r["answer_delta"] for r in rows]),
        }
        td = per_round[f"round_{rnd}"]["token_delta"]
        tj = per_round[f"round_{rnd}"]["token_jsd"]
        ad = per_round[f"round_{rnd}"]["answer_delta"]
        logger.info("  round %d: token_delta %s  token_jsd %s  answer_delta %s",
                    rnd, td["mean"], tj["mean"], ad["mean"])

    with open(out_dir / "logprob_multiturn.json", "w") as f:
        json.dump({
            "metadata": {**metadata, "probe": "logprob_multiturn_decay",
                         "opponent": opponent, "episodes": args.episodes,
                         "num_rounds": config.num_rounds,
                         "wrap_position": "first"},
            "per_round": per_round,
        }, f, indent=2)
    with open(out_dir / "logprob_multiturn.traces.jsonl", "w") as f:
        for r in all_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info("Saved to %s", out_dir)


if __name__ == "__main__":
    main()
