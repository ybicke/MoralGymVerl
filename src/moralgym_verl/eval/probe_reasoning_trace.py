"""Probe B — student reasoning traces scored under teacher vs student prompt.

Measures what SDPO would actually distill: sample reasoning traces from the
STUDENT prompt (that is what SDPO scores), teacher-force each trace under
both prompts, and report per state/round
    token_delta  = (teacher - student) mean per-token logprob — the
                   distillation pressure on the reasoning itself;
    answer_delta = label log-odds shift at the answer position with the SAME
                   student reasoning held fixed (trace truncated at its final
                   `Action:` marker via the parser's own find_action_marker).

Two state sources (--states):
    fabricated  (default) — single decision per PROBE_STATES fabricated
        prior round (first/CC/CD/DC/DD); writes logprob_b.json +
        logprob_b.traces.jsonl.
    episode     — live multi-turn episodes vs a scripted opponent; the moral
        value is wrapped ONLY into the episode's first user message
        (wrap_first_user, training-exact for multi-turn SDPO) and the
        per-round deltas show whether that episode-start signal still moves
        late-round tokens (signal decay); writes logprob_multiturn.json +
        logprob_multiturn.traces.jsonl.

Usage (inside the moralgym_verl container, 1 GPU):
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
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import torch

from moralgym_verl.eval.behavioral import (
    build_eval_config, load_config, load_model_for_eval,
)
from moralgym_verl.eval.teacher_context import (
    load_reprompt_template, wrap_first_user, wrap_prompt,
)
from moralgym_verl.eval.teacher_forcing import (
    PROBE_STATES, answer_logodds, chat_prefix, chat_prefix_messages,
    continuation_logprob, delta_stats, force_fixed_presentation, sample_trace,
)
from moralgym_verl.game.environment import FIXED_PAYOFFS
from moralgym_verl.game.moral_values import get_moral_value
from moralgym_verl.game.players import get_opponent_action
from moralgym_verl.game.prompts import build_prompt, parse_action
from moralgym_verl.game.prompts_reasoning import find_action_marker

logger = logging.getLogger(__name__)


def trace_probe(
    model, tokenizer, config, wrapper,
    num_traces: int, max_new_tokens: int, temperature: float,
    trace_log: Optional[list] = None,
) -> Dict:
    """Fabricated-state probe: per PROBE_STATES state, num_traces student
    traces scored under both prompts (= SDPO's teacher pass), plus the
    answer logodds shift given the same reasoning.

    If `trace_log` is a list, every sampled trace is appended as
    {"state", "trace", "parsed_action", "token_delta", "answer_delta"}."""
    cfg = copy.deepcopy(config)
    cfg.reasoning = True
    results = {}
    for state, hist_a, hist_o in PROBE_STATES:
        game_prompt = build_prompt(cfg, hist_a, hist_o)
        student_ids = chat_prefix(tokenizer, game_prompt, model.device)
        teacher_ids = chat_prefix(tokenizer, wrapper(game_prompt), model.device)

        token_deltas, answer_deltas = [], []
        n_empty = n_no_marker = n_parse_fail = 0
        for _ in range(num_traces):
            trace = sample_trace(model, tokenizer, student_ids,
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
            lp_s, n_s = continuation_logprob(model, tokenizer, student_ids, trace)
            lp_t, n_t = continuation_logprob(model, tokenizer, teacher_ids, trace)
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
                lo_s, _, _ = answer_logodds(
                    model, tokenizer, s_pref, cfg.coop_label, cfg.defect_label)
                lo_t, _, _ = answer_logodds(
                    model, tokenizer, t_pref, cfg.coop_label, cfg.defect_label)
                record["answer_delta"] = lo_t - lo_s
                answer_deltas.append(record["answer_delta"])

        results[state] = {
            "token_delta": delta_stats(token_deltas),
            "answer_delta": delta_stats(answer_deltas),
            "n_empty_traces": n_empty,
            "n_no_answer_marker": n_no_marker,
            "n_parse_fail": n_parse_fail,
        }
    return results


def probe_episode(
    model, tokenizer, config, wrapper,
    max_new_tokens: int, temperature: float,
) -> List[Dict]:
    """One student episode + per-round dual scoring (episode mode). Returns
    one record per round: {round, trace, token_delta, answer_delta, agent,
    opp}. The transcript stays plain; the teacher prefix wraps only the
    first user message (wrap_first_user, training-exact)."""
    messages: List[dict] = []
    agent_history: List[str] = []
    opp_history: List[str] = []
    records: List[Dict] = []

    for rnd in range(config.num_rounds):
        prompt = build_prompt(config, agent_history, opp_history)
        messages.append({"role": "user", "content": prompt})

        # Student generates (plain context — as in training rollouts).
        student_ids = chat_prefix_messages(tokenizer, messages, model.device)
        trace = sample_trace(model, tokenizer, student_ids,
                             max_new_tokens, temperature)
        messages.append({"role": "assistant", "content": trace})

        # Teacher prefix: identical transcript, first user turn wrapped.
        teacher_ids = chat_prefix_messages(
            tokenizer, wrap_first_user(messages[:-1], wrapper), model.device)

        record: Dict = {"round": rnd + 1, "trace": trace,
                        "token_delta": None, "answer_delta": None}
        lp_s, n_s = continuation_logprob(model, tokenizer, student_ids, trace)
        lp_t, n_t = continuation_logprob(model, tokenizer, teacher_ids, trace)
        if n_s:
            record["token_delta"] = lp_t / n_t - lp_s / n_s

        m = find_action_marker(trace)
        if m is not None:
            reasoning_prefix = trace[: m.start(1)].rstrip()
            cut_ids = tokenizer(reasoning_prefix, add_special_tokens=False,
                                return_tensors="pt").input_ids.to(model.device)
            lo_s, _, _ = answer_logodds(
                model, tokenizer, torch.cat([student_ids, cut_ids], dim=1),
                config.coop_label, config.defect_label)
            lo_t, _, _ = answer_logodds(
                model, tokenizer, torch.cat([teacher_ids, cut_ids], dim=1),
                config.coop_label, config.defect_label)
            record["answer_delta"] = lo_t - lo_s

        # Advance the game (mirrors trajectory.run_episode semantics).
        action = parse_action(trace, config)
        if action is None:
            record["agent"], record["opp"] = "illegal", None
        else:
            opp_action = get_opponent_action(
                config.opponent, opp_history, agent_history)
            agent_history.append(action)
            opp_history.append(opp_action)
            record["agent"], record["opp"] = action, opp_action
        records.append(record)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe B: reasoning traces scored teacher-vs-student")
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkpoint", default="base",
                        help="'base' or LoRA checkpoint path (as behavioral.py)")
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
    parser.add_argument("--output-dir", default="results",
                        help="Run directory; writes logprob_b.json (+traces) "
                             "or logprob_multiturn.json (+traces) into it.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    cfg = load_config(args.config)
    if args.game is not None:
        cfg["game"]["type"] = args.game
        cfg["game"]["payoffs"] = dict(FIXED_PAYOFFS[args.game])
    if not cfg.get("prompt", {}).get("reasoning", False):
        raise SystemExit("Probe B needs prompt.reasoning: true (it scores "
                         "reasoning traces).")

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

    force_fixed_presentation(cfg)
    opponent = args.opponent if args.states == "episode" else "tit_for_tat"
    config = build_eval_config(cfg, opponent=opponent)

    eval_cfg = cfg.get("evaluation", {})
    max_new_tokens = eval_cfg.get("max_new_tokens", 512)
    temperature = eval_cfg.get("temperature", 1.0)

    metadata = {
        "moral_value": args.moral_value,
        "base_model": cfg["policy"]["model_name"],
        "checkpoint": args.checkpoint,
        "game_type": cfg["game"]["type"],
        "teacher_template_source": teacher_cfg.get("template_source"),
        "temperature": temperature,
        "eval_seed": seed,
        "timestamp": datetime.now().isoformat(),
    }
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
            model, tokenizer, config, wrapper,
            num_traces=num_traces,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            trace_log=trace_log,
        )
        for state, r in probe_b.items():
            logger.info("  %s: token_delta %s, answer_delta %s",
                        state, r["token_delta"]["mean"],
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
    all_records: List[Dict] = []
    for ep in range(args.episodes):
        records = probe_episode(model, tokenizer, config, wrapper,
                                max_new_tokens, temperature)
        for r in records:
            r["episode"] = ep
        all_records.extend(records)

    per_round = {}
    for rnd in range(1, config.num_rounds + 1):
        rows = [r for r in all_records if r["round"] == rnd]
        per_round[f"round_{rnd}"] = {
            "token_delta": delta_stats([r["token_delta"] for r in rows]),
            "answer_delta": delta_stats([r["answer_delta"] for r in rows]),
        }
        td = per_round[f"round_{rnd}"]["token_delta"]
        ad = per_round[f"round_{rnd}"]["answer_delta"]
        logger.info("  round %d: token_delta %s  answer_delta %s",
                    rnd, td["mean"], ad["mean"])

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
