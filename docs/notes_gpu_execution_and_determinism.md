# Notes: from transformer to GPU — how the model computes, and why bit-reproducibility breaks across code versions

Personal learning notes (2026-07-31), written while digesting the determinism
note in `eval_results/teacher_signal/mvp/README.md` (§1). Companion to
`notes_llm_and_sdpo_mechanics.md`, which covers the forward-pass/generation
distinction and the SDPO signal at the conceptual level; this doc goes
bottom-up: what an LLM is, what the transformer inside it computes, where it
lives in GPU memory, what a kernel is, and the exact causal chain from "we
added `token_jsd`" to "trace 2 diverged."

Concrete numbers throughout are `google/gemma-2-9b-it`: vocabulary ≈ 256,000
tokens, hidden size d = 3584, 42 transformer layers.

---

## 1 · What an LLM is: a next-token distribution machine

Strip away chat, reasoning, and agents: an LLM is a single function

```
token sequence in  →  probability distribution over the NEXT token, out
```

**Tokens.** Text is first chopped by a fixed tokenizer into pieces from a
256,000-entry vocabulary (whole words, word fragments, punctuation). "The
opponent defected" might become 4 token ids `[651, 28550, 708, 29885]`. The
model never sees characters — only these integers.

**The one task.** Given a prefix, output P(next token | prefix) — 256,000
probabilities summing to 1. That is the *entire* interface. Pre-training is
nothing but this task at scale: show the model trillions of tokens of text
and nudge it, position by position, to put more probability on the token that
actually came next. Every capability (game reasoning, moral judgment,
following the `Action:` format) exists only insofar as it helps predict next
tokens — and an instruction-tuned model like gemma-2-9b-**it** is the same
machine fine-tuned on chat-formatted text, which is why prompts must be
wrapped in the **chat template** (special turn markers like
`<start_of_turn>user`) — the format it was trained to continue.

Everything else is wrapping around this function: "generation" = call it in
a loop and sample (§3); "scoring/teacher forcing" = call it once on existing
text and read probabilities off (§3); the SDPO teacher = the same function
called with a value-wrapped prefix.

---

## 2 · Inside the machine: the transformer stack

The forward pass over K tokens:

```
token ids            [K]              K integers ("The", "prisoner", …)
      │
      ▼  embedding lookup
hidden states        [K × 3584]       one 3584-dim vector per position
      │
      ▼  transformer layer 1   (attention + MLP)
hidden states        [K × 3584]       same shape, refined
      │
      ⋮   … 42 layers, shape never changes …
      │
      ▼  transformer layer 42
final hidden states  [K × 3584]       h_k = everything known at position k
      │
      ▼  LM HEAD — the final linear layer
logits               [K × 256,000]    one score per vocab word, per position
      │
      ▼  softmax (per row)
probabilities        [K × 256,000]    p_θ(· | ctx) — the s_k / t_k of the eval
```

**Embeddings.** A lookup table [256,000 × 3584]: each token id becomes a
learned 3584-dim vector. From here on, position k carries a *hidden state*
h_k — a vector that starts as "which token am I" and is progressively
enriched into "everything relevant for predicting what comes after me."

**One transformer layer = attention + MLP.** Each of the 42 layers applies
two sub-steps, both of which read vectors and write refined vectors of the
same shape:

- **Attention — mix information *across* positions.** Each position computes
  how relevant every *earlier* position is to it (query·key match), then
  pulls in a weighted blend of their vectors (values). This is how the token
  at "…my move:" gets to condition on "opponent defected" from 40 tokens ago.

  ```
  position:      1      2        3         4
  token:        "The" "opponent" "defected" "…"
                                     ▲
  attention at 4:  ────────────────┘  (weights over positions 1–3 only)
  ```

- **MLP — process each position *independently*.** A two-matrix feed-forward
  net applied to every position's vector on its own; where per-position
  "computation" happens after attention has gathered the context.

**The causal mask (load-bearing fact).** Attention at position k is masked to
positions ≤ k — no peeking ahead. Consequence: in a single forward pass over
K tokens, the prediction at every position k is conditioned on *exactly the
prefix* τ_{<k}, as if the future tokens weren't there. This is what makes
one-pass teacher-forced scoring (§3) legitimate: the K predictions produced
by one pass are identical to what K separate prefix-by-prefix calls would
produce.

**The LM head.** The transformer proper never touches the vocabulary — it
works entirely in the 3584-dim hidden space. The vocabulary appears only at
the last step, via one weight matrix W of shape [3584 × 256,000] (one learned
direction per vocab word). The logit of word v at position k is a dot
product, `logit_k(v) = h_k · W[:, v]` — "how aligned is this hidden state
with word v's direction?" — and doing this for all positions and words at
once is a single matrix multiplication:

```
h [K × 3584]  @  W [3584 × 256,000]  =  logits [K × 256,000]
```

**The full 256k-wide row is always computed**, never just the token you care
about, for two reasons:

1. Softmax needs the whole row anyway — the denominator Σ_v' exp(z_v') runs
   over the entire vocabulary; even P(" action3") requires all 256k logits.
2. It is one matmul — the GPU produces the whole product in a single
   optimized operation (the GEMM kernel of §5); extracting one column would
   not be cheaper in any way that matters.

Scale check: the hidden states are tiny ([500 × 3584] ≈ 7 MB), the logit
matrix is monstrous ([500 × 256,000] × 4 bytes ≈ 0.5 GB). The vocabulary
dimension is ~70× wider than the hidden dimension — everything expensive in
this story is a [· × 256k] tensor.

---

## 3 · Two ways to run the model

**Generation (autoregressive loop).** One token at a time: forward pass on
the context → logits for the *last position only* [1 × 256k] → softmax →
**sample** one token (the only place randomness enters) → append → repeat.
A KV cache stores each layer's attention keys/values so past positions are
not recomputed. A 500-token trace ≈ 500 small forward passes, each producing
a tiny [1 × 256k] logit row that is used and discarded.

**Teacher-forced scoring (one big pass).** The text already exists (the
student's sampled trace τ). Feed [prompt ⊕ τ] through ONE forward pass; by
the causal mask (§2) every position k is conditioned on exactly the real
prefix τ_{<k}; out comes the full [K × 256k] logit matrix at once. Nothing is
sampled, nothing is chosen — scoring a fixed trace is fully deterministic and
temperature-independent.

The readouts differ in what they *keep* from that matrix:

- `token_delta` (old code): gather **one number per row** — the log-prob of
  the trace's actual next token. Keep [K] scalars, free the 0.5 GB matrix
  immediately.
- `token_jsd` (new code): keep and combine the **full matrices** — log-probs
  under both prompts simultaneously (s_k and t_k), the mixture
  m_k = ½(s_k + t_k), the two KL terms. Each is another [K × 256k], ~0.5 GB
  temporary that simply did not exist in the old code.

**Sampling mechanics** (matters in §6): sampling is seeded — the RNG produces
bit-identical draws u across runs. The draw picks a token by finding which
token's interval contains u on the cumulative probability line:

```
  ├────── "Therefore" ──────┤── "However" ──┤ …
                          ▲ u = 0.61803  → "Therefore"
```

If two tokens are nearly tied and the logits shift in the last decimal, the
boundary moves a hair — and the *same* u can land on the other side. One
flipped token → every later token conditions on it → the trace diverges
completely (into a different, equally valid sample).

---

## 4 · Where it all lives: GPU memory (HBM) and the caching allocator

All tensors live in the GPU's on-board **HBM** (high-bandwidth memory) —
~96 GB on a GH200 (`nvidia-smi`: 97871 MiB), physically on the GPU package,
separate from CPU RAM. Rough map during a probe B run:

```
GH200 HBM, ~96 GB
┌────────────────────────────────────────────────────────────┐
│ model weights                                    ~18 GB    │  9B × 2 B (bf16), static
│   ├─ 42 transformer layers                                 │
│   └─ LM head W [3584 × 256k]  (~1.8 GB, tied embedding)    │
├────────────────────────────────────────────────────────────┤
│ PyTorch caching-allocator pool             grows as needed │  ← "memory state"
│   │ activations of current pass, KV cache        (small)   │
│   │ logits [K × 256k]                            ~0.5 GB   │  always created
│   │ ── token_jsd temporaries (new code only) ──────────    │
│   │ log-probs student / teacher, m_k, KL terms  4×~0.5 GB  │
│   │ (freed after each trace → holes in the pool)           │
├────────────────────────────────────────────────────────────┤
│ cuBLAS workspace (kernel scratch), CUDA context            │
└────────────────────────────────────────────────────────────┘
```

PyTorch does not ask CUDA for memory per tensor (too slow). Its **caching
allocator** grabs large slabs once, carves tensors out of them, and on free
keeps blocks in a reuse pool. Consequence: the allocator's state at any
moment — which slabs exist, where the free holes are, at which *addresses*
the next tensor will land, how much contiguous workspace is left — is a
function of the **entire allocation history so far**. (A parking lot where
nobody ever really leaves: where the next car parks depends on everyone who
came before.) The token_jsd temporaries punch four ~0.5 GB allocations into
that history between trace 1 and trace 2 — that is what "memory state
changed" means.

---

## 5 · What the GPU actually runs: kernels

The CPU-side code (PyTorch) computes nothing itself — it **launches
kernels**: functions that run on the GPU across ~100k threads ("GPU, run this
over that data"). Everything is a kernel: softmax, addition, and above all
matrix multiplication (**GEMM**) — every linear layer and the LM head.

For one operation like matmul, NVIDIA's cuBLAS has **dozens** of hand-tuned
variants: different output tile sizes per thread block (128×128 vs 64×256),
"split-K" variants that break the long inner sum into chunks merged at the
end, variants needing scratch **workspace** memory, variants requiring
16-byte-aligned pointers to load 4 floats per instruction. At call time a
heuristic picks a variant based on: matrix **shapes**, the input tensors'
**addresses/alignment**, and available **workspace**. All variants are
correct implementations of the same matmul.

**Why two correct kernels differ in the last bit:** float addition rounds
after every step, so it is not associative — the grouping matters. A logit
is a 3584-term dot product computed by many threads whose partial sums are
merged in an order set by the tiling/split-K choice:

```
kernel K1 (2-way split):   (a₁+…+a₁₇₉₂) + (a₁₇₉₃+…+a₃₅₈₄)
kernel K2 (4-way split):   ((a₁+…+a₈₉₆)+(a₈₉₇+…)) + ((…)+(…))
                            └─ different intermediate roundings
                               → last-bit difference in the logit
```

Each single kernel is fully deterministic: same kernel + same data = same
bits, on any node, any day. That is why *same code + same seed* reproduces
bit-identically. Wobble enters only through kernel **selection** — and
selection changes only when its inputs (shapes, addresses, workspace)
change.

---

## 6 · The causal chain, assembled

```
new token_jsd code
  → four ~0.5 GB temporaries allocated/freed while scoring trace 1     (§3)
    → caching-allocator state differs when trace 2 is generated        (§4)
      → tensor addresses / free workspace differ
        → cuBLAS heuristic picks GEMM variant K2 instead of K1         (§5)
          → different partial-sum order → last-decimal logit shift
            → at one near-tied token the same seeded draw u
              crosses the interval boundary → different token          (§3)
              → trace 2+ diverges into a fresh, equally valid sample
```

**Empirical fingerprint** (how the mechanism was confirmed): trace 1 is
generated *before* any token_jsd allocation exists — same memory state as the
old code — and reproduced **bit-identically**; traces 2+ are generated after
the perturbation and diverged. A seed or prompt difference would have broken
trace 1 too.

**Why this is noise, not bias:** the boundary in §3 moved by ~1e-7 — the
sampling *distribution* is unchanged for all practical purposes. Reruns under
changed code are fresh unbiased samples; scoring of any given trace stays
exact. Hence the README's rule: conclusions must survive resampling
(two bit-identical runs are the same sample twice, not a replication).

---

## 7 · Why training never sees this

SDPO training (like GRPO in verl) is two-phase: the rollout phase generates
**all** samples of the batch in parallel through vLLM, and only then does the
trainer phase run the forward passes that compute the loss. Generation and
scoring never interleave within a step, so scoring allocations cannot reach
back into generation numerics.

The exposure was specific to the probe B eval script
(`src/moralgym_verl/eval/probe_reasoning_trace.py`), a standalone HF-based
loop that originally interleaved: generate trace → score it (allocating the
token_jsd temporaries) → generate next trace. Statistically irrelevant
either way: N sequential and N interleaved draws from p_θ(· | x_s) are the
same experiment.

**Update 2026-08-03:** the probe was restructured to generate-all-then-score
(`trace_probe` phase 1/2; `play_episode` + `score_rounds` for episode mode,
phase-separated across all episodes in `main()`). Every trace is now sampled
before any scoring forward pass runs, mirroring training's rollout→trainer
split — so scoring-code changes can no longer perturb generation numerics,
and same-seed generations stay bit-identical under future scoring edits.
This closes only that one channel: bit-reproducibility across *generation*
code changes, driver updates, or allocator-history differences was never on
the table, and the scientific rule stands — conclusions must survive
resampling (README §1, consequence 3).
