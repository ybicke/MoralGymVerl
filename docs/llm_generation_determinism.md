# LLM generation, sampling, and (non-)determinism — learning note

Distilled from the MoralGymVerl same-seed divergence investigation
(jobs 2991881 vs 2993459, 2026-08). Model in examples: Gemma-2-9b-it
(42 layers, hidden size 3584, vocab ~256K, bf16).

Companion: `notes_gpu_execution_and_determinism.md` (bottom-up — the
transformer stack, HBM/caching allocator, kernels, and the July
cross-code divergence chain). This note owns the sampling pipeline:
temperature/softmax/RNG mechanics, the knobs, and the same-code case
study.

## 1. One generation step, end to end

Text so far: `"I would choose action"`

**Step 1 — forward pass (deterministic function).**
The context runs through the network: per layer, attention + MLP
(matrix multiplies over the 9B weights). With a KV cache, past tokens'
keys/values are reused; only the newest token is computed fresh. The
result is a single hidden vector (3584 numbers) summarizing "the text
so far." The **LM head** — one final linear layer, hidden(3584) x
matrix(3584 x 256K), weight-tied to the input embeddings in Gemma —
maps it to one raw score per vocabulary token: the **logits**.

```
"1": 8.2    "3": 7.7    "4": 7.3    ...256K others: tiny
```

**Step 2 — temperature divides the logits** (before softmax):

```
T=1.0:  8.2  7.7  7.3    unchanged — the model's honest distribution
T=0.5: 16.4 15.4 14.6    gaps doubled -> sharper
T->0:   gaps -> inf      argmax becomes certainty (greedy)
T=2.0:  4.1 3.85 3.65    gaps halved -> flatter
```

**Step 3 — softmax turns scores into probabilities** (sum to 1).
With logits z_i and temperature T, both steps in one formula:

```
p_i = exp(z_i / T) / sum_j exp(z_j / T)      (sum over all 256K tokens)

T=1.0:  "1": 0.50   "3": 0.30   "4": 0.20
```

T in the denominator of the exponent is why temperature works: T<1
stretches the logit gaps before exponentiation (sharper), T>1 shrinks
them (flatter), T->0 sends the largest logit's share to 1 (argmax).

**Step 3b — optional truncation: top-k / top-p.**
Before sampling, the candidate set can be cut down, then renormalized:

- **top-k**: keep only the k highest-probability tokens, drop the
  other 256K-k entirely. k=1 is greedy; k=50 is a common default.
- **top-p (nucleus)**: keep the smallest set of tokens whose
  cumulative probability reaches p (e.g. 0.9) — adaptive size: few
  candidates when the model is confident, more when uncertain.

Why truncate at all: softmax never outputs an exact zero (exp is
always positive), so every one of the 256K tokens keeps some sliver of
probability at every step. Individually negligible, but the tail sums
to real mass — often a few percent spread over tens of thousands of
junk continuations. Pure T=1 sampling therefore lands in the tail
every few dozen tokens, and because generation is autoregressive, ONE
absurd token poisons all following context ("I would choose actionĶ"
-> the model must now continue from nonsense). Truncation encodes the
judgment that the tail is softmax smoothing noise, not genuine model
intent: cut it, renormalize the head, sample there. top-k does this
with a fixed candidate count; top-p adapts the count to the model's
confidence (sharp distribution -> few survive, flat -> many), which is
why it aged better as the default in practice.

Two caveats: (1) truncation changes the distribution being sampled —
an eval measuring behavioral probabilities (like p_C) should pin these
settings explicitly and report them, since p_C under top-p 0.9 is a
different quantity than under the raw distribution; HF generate can
silently inherit top_k/top_p from a model's generation_config. (2) for
reproducibility, the cutoff is itself a near-tie comparison — numerics
jitter can flip which token sits at rank k / crosses the p threshold,
another boundary two runs can disagree on.

**Step 4 — the sampler picks one token.**
Stack the probabilities on [0,1) (cumulative intervals); the seeded
RNG emits its next uniform number u; the token whose interval contains
u is emitted. Append, repeat from step 1.

```
0.0 ────────── 0.50 ─────── 0.80 ────── 1.0
        "1"           "3"         "4"
u = 0.62  ->  lands in "3"  ->  emit "3"
```

Key separation: **the model builds the ruler (steps 1–3), the RNG
supplies the dart (step 4).** The model is a deterministic function;
ALL sampling randomness is injected in step 4. A big interval catches
more darts — that is exactly how "probability 0.5" becomes "picked 50%
of the time." The u's themselves carry no meaning; a seeded run is one
fair draw from the space of possible runs, frozen so it can be looked
at twice.

## 2. The knobs

| knob | acts on | effect on randomness |
|---|---|---|
| temperature | step 2 | volume dial: T=0 none (greedy), T=1 model's own distribution, higher = more |
| seed (`torch.manual_seed`) | step 4 | none added/removed — makes the u-sequence repeatable |
| numerics (kernels, rounding order, TF32, hardware) | step 1 | unwanted last-bit jitter in logits -> boundaries shift ~1e-6 |
| `torch.use_deterministic_algorithms(True)` + `CUBLAS_WORKSPACE_CONFIG` | step 1 | removes jitter within one hardware+software stack (slower); no help across nodes/versions |

## 3. Where the numerics jitter comes from

Floating-point addition is not associative: results are rounded after
every operation, so the *order* of summation changes the last bits.
Toy example, 4 significant digits: (1000 + 0.4) + 0.4 = 1000 but
1000 + (0.4 + 0.4) = 1001. bf16 has ~3 significant decimal digits, so
the effect is coarse.

A forward pass is trillions of additions inside huge dot products, and
the summation order is set by implementation details, not by the math:

- **Matmul reduction order.** GPU kernels tile the dot products across
  thousands of threads and merge partial sums; tiling/merge order
  depends on which kernel cuBLAS selects (shape- and heuristic-
  dependent) and on split-k strategies.
- **Parallel reductions elsewhere.** Softmax denominators, RMSNorm
  sums — same order-dependence.
- **Atomic adds.** Some kernels accumulate in thread-arrival order,
  which depends on runtime timing — nondeterministic even on the same
  GPU, run to run (this is what the determinism flags swap out).
- **Precision modes.** bf16 weights/activations, fp32 accumulation,
  TF32 (fp32 matmuls with 10-bit mantissa inputs on Ampere+) — config
  differences change the rounding itself.
- **Different code paths, different kernels.** Prefill (whole prompt at
  once, big matmuls) and KV-cache decode (one token at a time, skinny
  matmuls) use different kernels — one path can be bit-stable while
  the other jitters.

Net effect: logits differing by ~1 part in 1e6 between jobs/nodes.
Irrelevant for 299 of 300 tokens; but when a drawn u lands inside the
sliver between two runs' interval boundaries (a near-tie token), the
same dart falls on different sides -> different token.

## 4. Why one flipped token cascades

1. **Within an episode:** generation is autoregressive — a different
   token means a different context for every later step; the rest of
   the episode is a different (still coherent) continuation.
2. **Across episodes:** one global RNG stream, one u consumed per
   generated token. A flip that changes the episode's token count
   shifts every later draw -> all subsequent episodes are effectively
   independent resamples. A length-preserving flip keeps the stream
   aligned -> later episodes resync bit-exactly (observed!).

## 5. Case study (MoralGymVerl, 2026-08)

Two jobs, identical code/command/seed(42)/T=1.0, different nodes
(nid007490 vs nid006136): prompts 200/200 byte-identical; only
episodes 0–1 byte-equal; first flip at ep 2 token 99 — draw-count
accounting proved both RNGs were in the identical state and drew the
same u, yet different tokens came out => the distributions themselves
differed => numerics, not seeding (H4 ruled out). Deterministic layers
(probe A: teacher-forced answer-token logprobs, prefill path, no
sampling) replay byte-exactly across jobs, nodes, AND code versions.
Behavioral effect: none-(D,C) p_C 0.40 vs 0.46 (July: 0.66) — pooled
0.51, n=150. Open: H1 (node-deterministic) vs H2 (per-job nondet.) vs
H3 (env drift) — pinned-node rerun job 3000274 tests this; launcher
now logs an env fingerprint (driver, torch/cuda/cudnn, TF32 flags).

## 6. Practical protocol

- **Sampled behavioral evals measure a distribution** (e.g. p_C);
  greedy/T=0 cannot — it collapses every identical prompt to one
  answer. Sampling "errors" ARE the measurement.
- **Seeds do not guarantee bitwise replay** of sampled generation
  across jobs/nodes — they fix the darts, not the board. Expect exact
  reproducibility only for deterministic layers (teacher-forced
  scoring) or, at best, within one pinned hardware+software stack with
  determinism flags on.
- **Statistics over bytes:** run k replicates with different seeds
  (42, 43, 44, ...), pool, report error bars. A single n=50 cell has
  SE ~0.07 at p=0.5 — run-to-run spread of that size is expected.
- Keep one canonical fixed-seed run per config as a forensic anchor;
  pin nodes only when debugging the machinery, not to measure models.
