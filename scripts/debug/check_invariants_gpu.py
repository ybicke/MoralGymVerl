"""GPU-tier invariant checks from the 2026-08-05 pre-debug assessment.

Checks with the REAL gemma-2-9b-it (1 GPU, moralgym_verl container):
  1  continuation_logprob offset — chain-rule comparison against
     incremental one-token-at-a-time scoring
  2  dual_continuation_scores self-consistency + JSD bounds
  4  single BOS through render_chat_inputs
  7  probe-B phase separation (call-order instrumentation)
  10 decode->re-encode seam on real sampled traces (empirical)
  A  answer-slot plausibility of the forced labels (probe A path)

Run:
  cd /users/bickery/MoralGymVerl && python3 scripts/debug/check_invariants_gpu.py

One-shot verification aid — delete after the debugging session if unwanted.
"""
from __future__ import annotations

import math
import sys

import torch

sys.path.insert(0, "/users/bickery/MoralGymVerl/src")

from moralgym_verl.eval.config import build_eval_config, load_config
from moralgym_verl.eval.generation import render_chat_inputs
from moralgym_verl.eval.model_loading import load_model_for_eval
from moralgym_verl.eval.teacher_context import (
    DEFAULT_FEEDBACK_TEMPLATE, load_reprompt_template, wrap_prompt,
)
from moralgym_verl.eval import probe_b as prt
from moralgym_verl.eval.teacher_forcing import (
    chat_prefix, continuation_logprob, dual_continuation_scores,
)
from moralgym_verl.game.prompts import build_prompt

CFG = "/users/bickery/MoralGymVerl/configs/eval/harness/gemma2_9b/classic.yaml"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)


def main():
    torch.manual_seed(42)
    cfg = load_config(CFG)
    config = build_eval_config(cfg, "random")  # fixed presentation, no draws
    template = load_reprompt_template(cfg["teacher"]["template_source"])
    value_text = ("It is wrong to exploit someone who has cooperated with "
                  "you.")  # short stand-in; wording irrelevant to mechanics

    def wrapper(p):
        return wrap_prompt(p, template, value_text,
                           cfg["teacher"].get("feedback_template",
                                              DEFAULT_FEEDBACK_TEMPLATE))

    print("Loading model ...", flush=True)
    model, tok = load_model_for_eval(None, cfg["policy"]["model_name"])

    # ---------- Check 4: single BOS ----------
    import copy
    ccfg = copy.deepcopy(config)
    ccfg.reasoning = False
    prompt = build_prompt(ccfg, ["C"], ["D"])          # CD state, non-reasoning
    text, inputs = render_chat_inputs(
        tok, [{"role": "user", "content": prompt}], model.device)
    head = inputs.input_ids[0][:3].tolist()
    check("4.single BOS (ids start [2, 106], never [2, 2, ...])",
          head[0] == 2 and head[1] != 2, f"first ids: {head}")
    check("4.rendered text starts with template turn",
          text.startswith("<bos><start_of_turn>user"), repr(text[:30]))

    # ---------- Check 1: offset via chain-rule comparison ----------
    prefix_ids = chat_prefix(tok, prompt, model.device)

    def incremental(prefix, cont):
        ids = tok(cont, add_special_tokens=False,
                  return_tensors="pt").input_ids.to(prefix.device)
        total, cur = 0.0, prefix
        with torch.no_grad():
            for k in range(ids.shape[1]):
                lp = torch.log_softmax(model(cur).logits[0, -1].float(), -1)
                total += lp[ids[0, k]].item()
                cur = torch.cat([cur, ids[:, k:k + 1]], dim=1)
        return total, ids.shape[1]

    for cont in (" action3", " action4"):
        lp_fast, k_fast = continuation_logprob(model, tok, prefix_ids, cont)
        lp_incr, k_incr = incremental(prefix_ids, cont)
        diff = abs(lp_fast - lp_incr)
        check(f"1.offset chain-rule {cont!r} (K={k_fast})",
              k_fast == k_incr and diff < 0.05,
              f"batched={lp_fast:.4f} incremental={lp_incr:.4f} |diff|={diff:.5f}")

    # ---------- Check A: answer-slot plausibility ----------
    lp_c, _ = continuation_logprob(model, tok, prefix_ids, " action3")
    lp_d, _ = continuation_logprob(model, tok, prefix_ids, " action4")
    check("A.forced labels plausible at answer slot (not offset garbage)",
          max(lp_c, lp_d) > -8.0, f"lp_C={lp_c:.2f} lp_D={lp_d:.2f} "
          f"logodds={lp_c - lp_d:+.2f}")

    # ---------- Check 2: dual_continuation_scores ----------
    rcfg = copy.deepcopy(config)
    rcfg.reasoning = True
    rprompt = build_prompt(rcfg, ["C"], ["D"])
    s_ids = chat_prefix(tok, rprompt, model.device)
    t_ids = chat_prefix(tok, wrapper(rprompt), model.device)
    fake_trace = ("Cooperating keeps trust; defecting pays more this round. "
                  "I choose cooperation.\nAction: action3")
    same = dual_continuation_scores(model, tok, s_ids, s_ids, fake_trace, 0.5)
    check("2.identical prefixes -> lp_s == lp_t",
          abs(same["lp_s"] - same["lp_t"]) < 1e-3,
          f"lp_s={same['lp_s']:.4f} lp_t={same['lp_t']:.4f}")
    check("2.identical prefixes -> token_jsd ~ 0",
          same["token_jsd"] < 1e-4, f"jsd={same['token_jsd']:.2e}")
    lp_ref, _ = continuation_logprob(model, tok, s_ids, fake_trace)
    check("2.lp_s consistent with continuation_logprob",
          abs(same["lp_s"] - lp_ref) < 0.05,
          f"dual={same['lp_s']:.4f} single={lp_ref:.4f}")
    real = dual_continuation_scores(model, tok, s_ids, t_ids, fake_trace, 0.5)
    check("2.real teacher: 0 <= mean JSD <= ln2",
          0.0 <= real["token_jsd"] <= math.log(2),
          f"jsd={real['token_jsd']:.4f} nats/token, "
          f"token_delta={(real['lp_t'] - real['lp_s']) / real['num_tokens']:+.4f}")

    # ---------- Check 7: phase separation in trace_probe ----------
    events = []
    orig_sample, orig_dual = prt.sample_trace, prt.dual_continuation_scores
    orig_logodds = prt.answer_logodds
    prt.sample_trace = (lambda *a, **k: (events.append("sample"),
                                         orig_sample(*a, **k))[1])
    prt.dual_continuation_scores = (lambda *a, **k: (events.append("score"),
                                                     orig_dual(*a, **k))[1])
    prt.answer_logodds = (lambda *a, **k: (events.append("score"),
                                           orig_logodds(*a, **k))[1])
    try:
        prt.trace_probe(model, tok, config, wrapper, alpha=0.5,
                        num_traces=2, max_new_tokens=64, temperature=0.7)
    finally:
        prt.sample_trace, prt.dual_continuation_scores, prt.answer_logodds = (
            orig_sample, orig_dual, orig_logodds)
    last_sample = max(i for i, e in enumerate(events) if e == "sample")
    first_score = min(i for i, e in enumerate(events) if e == "score")
    check("7.ALL sampling precedes ALL scoring",
          last_sample < first_score,
          f"{events.count('sample')} samples then "
          f"{events.count('score')} scoring calls")

    # ---------- Check 10: decode->re-encode seam (empirical) ----------
    n_match = n_tot = 0
    first_div = None
    eot_seen = 0
    for i in range(4):
        with torch.no_grad():
            out = model.generate(input_ids=s_ids, max_new_tokens=200,
                                 do_sample=True, temperature=0.7,
                                 top_k=0, top_p=1.0)
        gen = out[0][s_ids.shape[1]:].tolist()
        eot_seen += int(107 in gen)  # <end_of_turn>
        decoded = tok.decode(out[0][s_ids.shape[1]:], skip_special_tokens=True)
        re_ids = tok(decoded, add_special_tokens=False).input_ids
        specials = set(tok.all_special_ids)
        gen_clean = [t for t in gen if t not in specials]
        n_tot += 1
        if re_ids == gen_clean:
            n_match += 1
        elif first_div is None:
            for j, (a, b) in enumerate(zip(gen_clean, re_ids)):
                if a != b:
                    first_div = (j, tok.decode([a]), tok.decode([b]))
                    break
            else:
                first_div = ("length", len(gen_clean), len(re_ids))
    check("10.decode->re-encode round trip on real traces",
          n_match == n_tot,
          f"{n_match}/{n_tot} identical"
          + (f"; first divergence {first_div}" if first_div else "")
          + f"; <end_of_turn> in {eot_seen}/{n_tot} gens (dropped by probe)")

    print(flush=True)
    fails = [n for n, ok in results if not ok]
    print(f"{len(results) - len(fails)}/{len(results)} passed"
          + (f"; FAILURES: {fails}" if fails else " — all good"), flush=True)


if __name__ == "__main__":
    main()
