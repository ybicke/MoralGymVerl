# Notes: from transformer to GPU — how the model computes, and why bit-reproducibility breaks

Personal learning notes (2026-07-31; tightened and corrected 2026-08-04
after the same-code divergence finding). Companions:
`notes_llm_and_sdpo_mechanics.md` (forward-pass/generation distinction
and the SDPO signal, conceptual) and `llm_generation_determinism.md`
(the sampling pipeline: temperature/softmax/seed/top-k, and the
same-code divergence case study). This doc goes bottom-up: what the
transformer computes, where it lives in GPU memory, what a kernel is,
and the causal chain behind the July *cross-code* divergence.

Concrete numbers throughout: `google/gemma-2-9b-it` — vocab ≈ 256,000,
hidden size d = 3584, 42 transformer layers, bf16.

---

## 1 · What an LLM is

One function: token sequence in → probability distribution over the
NEXT token out. Text is chopped by a fixed tokenizer into ids from a
256K vocabulary; the model only ever sees these integers. Pre-training
is this task at scale; an instruction-tuned model (-**it**) is the same
machine fine-tuned on chat-formatted text — hence the mandatory chat
template. Everything else is wrapping: "generation" = call it in a
loop and sample; "scoring / teacher forcing" = call it once on existing
text and read probabilities off; the SDPO teacher = the same function
on a value-wrapped prefix.

---

## 2 · The transformer stack

Forward pass over K tokens:

```
token ids            [K]              K integers
      ▼  embedding lookup             (table [256K × 3584])
hidden states        [K × 3584]       one vector per position
      ▼  42 × (attention + MLP)       shape never changes
final hidden states  [K × 3584]       h_k = everything known at pos k
      ▼  LM HEAD (last linear layer)  W [3584 × 256K]
logits               [K × 256K]       one score per vocab word per pos
      ▼  softmax per row
probabilities        [K × 256K]       p_θ(· | ctx)
```

- **Attention** mixes information *across* positions: each position
  scores the relevance of every earlier position (query·key) and pulls
  in a weighted blend of their vectors. **MLP** then processes each
  position independently.
- **Causal mask (load-bearing):** attention at position k sees only
  positions ≤ k. So one pass over K tokens yields, at every k, the
  same prediction as a separate call on the prefix τ_{<k} — this is
  what makes one-pass teacher-forced scoring legitimate.
- **LM head:** the transformer works in the 3584-dim hidden space; the
  vocabulary appears only in the final matmul h @ W. The full 256K row
  is always computed — softmax's denominator needs every logit anyway,
  and it's one GEMM.
- **Scale check:** hidden states are tiny ([500 × 3584] ≈ 7 MB); the
  logit matrix is monstrous ([500 × 256K] ≈ 0.5 GB fp32). Everything
  expensive is a [· × 256K] tensor.

---

## 3 · Two ways to run the model

**Generation (autoregressive loop).** One token at a time: forward pass
→ last-position logits [1 × 256K] → softmax → **sample** (the only
place randomness enters) → append → repeat. A KV cache stores past
keys/values so earlier positions aren't recomputed. Sampling mechanics
(temperature, seeded u on the cumulative-probability line, why a
last-bit logit shift can flip a near-tied token and cascade) live in
`llm_generation_determinism.md`.

**Teacher-forced scoring (one big pass).** The text already exists.
Feed [prompt ⊕ τ] through one pass; the causal mask conditions every
position on exactly the real prefix; out comes the full [K × 256K]
logit matrix. Nothing is sampled — scoring a fixed trace is fully
deterministic and temperature-independent.

The probe readouts differ in what they *keep* from that matrix:

- `token_delta` (old code): one scalar per row — the log-prob of the
  trace's actual next token. Keep [K] numbers, free the 0.5 GB matrix.
- `token_jsd` (new code): how far apart are the student's and
  teacher's next-token *distributions* at each position
  (Jensen-Shannon divergence). Needs whole rows: log-probs under both
  prompts, their mixture m = ½(s+t), two KL terms — four extra
  ~0.5 GB [K × 256K] temporaries per trace that the old code never
  allocated.

---

## 4 · Where it all lives: HBM and the caching allocator

All tensors sit in the GPU's on-board HBM (~96 GB on a GH200):

```
┌────────────────────────────────────────────────────────────┐
│ model weights                                    ~18 GB    │ static (bf16)
├────────────────────────────────────────────────────────────┤
│ PyTorch caching-allocator pool             grows as needed │ ← "memory state"
│   activations + KV cache (small) · logits ~0.5 GB          │
│   token_jsd temporaries (new code only)   4 × ~0.5 GB      │
│   (freed after each trace → holes in the pool)             │
├────────────────────────────────────────────────────────────┤
│ cuBLAS workspace (kernel scratch), CUDA context            │
└────────────────────────────────────────────────────────────┘
```

PyTorch doesn't ask CUDA for memory per tensor; its **caching
allocator** grabs big slabs, carves tensors out, and keeps freed blocks
for reuse. Consequence: the allocator's state — which slabs exist,
where the holes are, at which *addresses* the next tensor lands, how
much contiguous workspace remains — is a function of the **entire
allocation history**. (A parking lot where nobody truly leaves: where
the next car parks depends on everyone who came before.)

---

## 5 · What the GPU actually runs: kernels

PyTorch computes nothing itself — it **launches kernels**: functions
executed on the GPU across ~100k threads. Everything is a kernel;
above all matrix multiplication (**GEMM**). For one matmul, cuBLAS has
dozens of hand-tuned variants — tile sizes (128×128 vs 64×256),
split-K variants that chunk the long inner sum, variants needing
scratch workspace or 16-byte-aligned pointers. A heuristic picks one
at call time based on **shapes**, tensor **addresses/alignment**, and
available **workspace**. All variants are correct.

Why two correct kernels differ in the last bit: float addition rounds
after every step, so grouping matters. A logit is a 3584-term dot
product whose partial sums are merged in an order set by the
tiling/split-K choice:

```
kernel K1 (2-way split):  (a₁+…+a₁₇₉₂) + (a₁₇₉₃+…+a₃₅₈₄)
kernel K2 (4-way split):  ((a₁+…+a₈₉₆)+(a₈₉₇+…)) + ((…)+(…))
                           └─ different intermediate roundings
                              → last-bit difference in the logit
```

**Determinism status (corrected 2026-08-04).** A given kernel on given
data is deterministic (excepting atomic-add kernels, whose summation
order follows thread timing). The original version of this note
concluded "same code + same seed ⇒ bit-identical replay" — that was
**falsified** on 2026-08-03: two jobs with identical code, command and
seed (2991881/nid007490 vs 2993459/nid006136) diverged at a near-tie
token with the RNG provably in the same state. So kernel selection
inputs — or something else in the execution environment — evidently
differ across jobs/nodes even at fixed code. What still holds: within
one job the computation is fixed, and deterministic layers (prefill
teacher-forced scoring, probe A) replay byte-exactly across jobs,
nodes and code versions.

**Resolved 2026-08-05 (pinned-node experiments): H2 — per-job
nondeterminism.** Same-node same-day flags-off pair: 5/200 byte-equal.
Pair with torch.use_deterministic_algorithms(True) +
CUBLAS_WORKSPACE_CONFIG=:4096:8: 0/200, with zero nondeterministic-op
warnings — the jitter lives outside torch's determinism scope.
Candidates consistent with all evidence: flash_attn custom kernels
(torch.library ops the flag doesn't govern; the model demonstrably
runs them) and per-job allocator-address/workspace differences feeding
the cuBLAS selection heuristic (this section's mechanism, likewise
untouched by the flag). Bitwise replay of sampled generation is
unattainable on this stack; full account in
`llm_generation_determinism.md` §5.

---

## 6 · The July cross-code divergence, assembled

```
new token_jsd code
  → four ~0.5 GB temporaries alloc'd/freed while scoring trace 1   (§3)
    → caching-allocator state differs when trace 2 is generated    (§4)
      → tensor addresses / free workspace differ
        → cuBLAS heuristic picks GEMM variant K2 instead of K1     (§5)
          → different partial-sum order → last-bit logit shift
            → at one near-tied token the same seeded u crosses
              the interval boundary → different token
              → trace 2+ diverges into a fresh, equally valid sample
```

**Empirical fingerprint:** trace 1 is generated *before* any token_jsd
allocation exists — same memory state as the old code — and reproduced
bit-identically; traces 2+ diverged. A seed or prompt difference would
have broken trace 1 too.

**Noise, not bias:** the boundary moved by ~1e-7; the sampling
distribution is unchanged for all practical purposes. Reruns are fresh
unbiased samples; scoring of any given trace stays exact. Hence the
rule: conclusions must survive resampling — two bit-identical runs are
the same sample twice, not a replication.

---

## 7 · Why training never sees this

Training (GRPO/SDPO in verl) is two-phase: rollout generates all
samples via vLLM, then the trainer runs the scoring forward passes.
Generation and scoring never interleave, so scoring allocations can't
reach back into generation numerics. The exposure was specific to the
probe B eval script, which originally interleaved generate → score →
generate. **2026-08-03:** the probe was restructured to
generate-all-then-score, mirroring training's rollout→trainer split —
scoring-code changes can no longer perturb generation numerics. That
closes only this one channel; cross-job/node bit-replay of sampled
generation was never on the table (§5), and the scientific rule stands:
conclusions must survive resampling.
