# Notes: probe B's three metrics — teacher forcing, step by step

Personal learning notes (2026-08-05), written while reading the
wording-screen results. Companion to `notes_llm_and_sdpo_mechanics.md`
(forward pass vs generation) and `notes_gpu_execution_and_determinism.md`
(what a forward pass costs and why it's deterministic).

**Where the code lives** (all under `src/moralgym_verl/`):

| step | file :: function |
|---|---|
| entry point (CLI) | `eval/probe_b.py::main` — run as `python3 -m moralgym_verl.eval.probe_b`; launched per cell by `scripts/slurm/eval_teacher_signal.sh` |
| trace sampling (phase 1) | `eval/probe_b.py::trace_probe` → `eval/teacher_forcing.py::sample_trace` |
| per-trace scoring (phase 2) | `eval/probe_b.py::_score_trace` |
| token_delta + token_jsd (§4, §5) | `eval/teacher_forcing.py::dual_continuation_scores` (the two full-trace passes) + `::generalized_jsd` |
| answer_delta (§6) | `eval/teacher_forcing.py::continuation_logprob` + `::answer_logodds`; cut point from `game/prompts_reasoning.py::find_action_marker` |
| aggregation (mean/std) | `eval/teacher_forcing.py::delta_stats` |

**Where the results land** (per run cell
`eval_results/teacher_signal/<EVAL_GROUP>/<game>__<moral_value>_<jobid>/`,
mirrored to `$STORE/eval_results/teacher_signal/<EVAL_GROUP>/`):
  
- `logprob_b.json` — per-state summaries: mean/std/n of the three
  metrics + `n_empty` / `n_no_answer_marker` / `n_parse_fail`, and the
  run metadata (α, temperature, seed, template source).
- `logprob_b.traces.jsonl` — one line per sampled trace: state, full
  trace text, parsed action, and its three per-trace scores (the mode
  splits are computed from this file).
- Episode mode (`--states episode`) writes `logprob_multiturn.json`
  (+`.traces.jsonl`) instead.

---

## 1 · The one fact everything rests on

A single forward pass over a K-token text does not produce one answer —
it produces **K full next-token distributions, one at every position**,
each conditioned (by the causal mask) on exactly the tokens before it.

So the model is never "asked to choose" anything. We *write the text
ourselves*, run one pass, and **read off** probabilities at the
positions we care about. That reading-off is teacher forcing: we force
the continuation, the model just tells us how likely it found each
forced token — and, if we keep whole rows, how likely it found every
*alternative* token too.

## 2 · Notation

- $\mathcal{V}$ — the vocabulary, $|\mathcal{V}| \approx 256{,}000$
  (Gemma-2). A *sequence* is a finite tuple of tokens from
  $\mathcal{V}$; $\oplus$ denotes sequence concatenation.
- $p_\theta(v \mid c)$ — the model's next-token probability of token
  $v \in \mathcal{V}$ given context sequence $c$. For every fixed $c$,
  $p_\theta(\cdot \mid c)$ is a probability distribution on
  $\mathcal{V}$ (one softmax row). All logarithms are natural (units:
  nats).
- $x_s$ — the chat-templated student prompt (plain game prompt) as a
  token sequence; $x_t$ — the teacher prompt: the same game prompt
  wrapped in the SDPO reprompt template with the moral value, then
  chat-templated. Identical construction except for the wrapping.
- $\tau = (\tau_1, \dots, \tau_K)$ — one reasoning trace,
  $\tau_k \in \mathcal{V}$; prefix $\tau_{<k} = (\tau_1,\dots,\tau_{k-1})$,
  with $\tau_{<1}$ the empty sequence. Traces are sampled once,
  autoregressively, from the temperature-$T$ student policy:
  $\tau_k \sim p_\theta^{(T)}(\cdot \mid x_s \oplus \tau_{<k})$, where
  $p^{(T)} \propto p^{1/T}$ ($T = 0.7$). Scoring never resamples.
- Per position $k \in \{1,\dots,K\}$, the two **rows** compared by the
  metrics are the distributions

$$
s_k(v) \;=\; p_\theta\!\left(v \,\middle|\, x_s \oplus \tau_{<k}\right),
\qquad
t_k(v) \;=\; p_\theta\!\left(v \,\middle|\, x_t \oplus \tau_{<k}\right),
\qquad v \in \mathcal{V}.
$$

- $W$ — the LM-head weight matrix, $[3584 \times |\mathcal{V}|]$: the
  model's final linear layer. For a context's final hidden state
  $h \in \mathbb{R}^{3584}$, $h W$ gives one logit per vocabulary
  token; softmaxing it yields the row $p_\theta(\cdot \mid c)$.
- Kullback–Leibler divergence between distributions $p, q$ on
  $\mathcal{V}$:

$$
\mathrm{KL}(p \,\|\, q) \;=\; \sum_{v \in \mathcal{V}} p(v)\,\log\frac{p(v)}{q(v)} \;\ge\; 0 .
$$

- Per state, each metric is aggregated over the $n = 32$ sampled traces
  as mean and population standard deviation
  ($\sigma^2 = \tfrac1n \sum_i (x_i - \bar{x})^2$, `delta_stats`); the
  **mode split** $(a/b)$ counts traces with
  $\text{answer\_delta} > 0$ vs $\le 0$.

## 3 · Setup: one trace, two contexts, six forward passes

One sampled trace for state CD (I cooperated, they defected):

```
τ = "They defected while I cooperated. Still, retaliation would
     break my own standard of good faith.
     Action: action3"
```

All three metrics score this same fixed $\tau$. Six teacher-forced
passes per trace:

| pass | text | used by |
|---|---|---|
| 1 | $x_s \oplus \tau$ (full trace) | token_delta, token_jsd |
| 2 | $x_t \oplus \tau$ (full trace) | token_delta, token_jsd |
| 3–6 | $x_{s/t} \oplus r \oplus a^{C/D}$ | answer_delta |

where $r$ = the trace cut after its `Action:` marker (reasoning +
answer slot, conclusion discarded) and $a^C, a^D$ are the label token
sequences (§6).

**There is only ONE trace per measurement** — the student-sampled τ.
No "teacher trace" exists anywhere. token_delta and token_jsd score
the *full* τ (all $K$ tokens, conclusion included) under both prompts;
answer_delta uses only the truncated prefix $r$ and swaps in the forced
labels. So token_delta is the difference between the log-prob sums of
the *same* reasoning tokens — evaluated once conditioned on $x_t$,
once on $x_s$ (§4).

**The $[K \times |\mathcal{V}|]$ matrix.** Passes 1–2 each yield one
such matrix per context: $K$ rows (one per token of the scored trace)
by $|\mathcal{V}| \approx 256$k columns (one per vocabulary token).
Row $k$ is the full next-token distribution given everything before
position $k$:

```
                 vocab token v →                     (≈256k columns)
row 1   p(v | x)              ← the row τ_1 was sampled from
row 2   p(v | x ⊕ τ_1)        ← the row τ_2 was sampled from
  ⋮
row K   p(v | x ⊕ τ_{<K})     ← the row τ_K was sampled from
```

**How a row is computed** (always the same, in every procedure): the
context goes through the 42 transformer layers; the resulting hidden
state (3584-dim for Gemma-2-9B) is multiplied with the LM head
$W\ [3584 \times 256\text{k}]$ and softmaxed → one probability per
vocabulary token, summing to 1. The full 256k-wide row always gets
computed — the softmax denominator needs every logit — even if only
one entry is read.

**Generation (where τ came from) uses rows sequentially.** Each step:
one forward pass on the context so far → ONE row → **sample** the next
token from it (multinomial at $T{=}0.7$: a token with 20% mass is
picked 20% of the time; argmax is only the greedy $T{=}0$ case — this
is the sole random step) → append the sampled *token* and **discard
the row** (only token ids and the KV cache carry over, never logits).
Consequently the matrix never exists as a whole during generation, and
under $x_t$ its rows never existed at all — τ was sampled under $x_s$
only.

**Scoring recomputes all rows in one parallel pass.** The transformer
is not inherently sequential — generation is, only because the next
token must be sampled before it can be conditioned on. With the text
$x \oplus \tau$ in hand ($N = |x| + K$ tokens), one pass computes every
position simultaneously: the causal mask (lower-triangular attention)
lets position $i$ see tokens $1..i$ only, so its hidden state — and
hence its row — is *identical* to what a separate pass on just tokens
$1..i$ would give. One matmul $[N \times 3584] \times W$ + per-row
softmax then yields all $N$ rows at once; the code keeps the $K$ that
predict the trace (`logits[:, start:start+K]`, `start = prefix_len−1`)
and discards the rows over the prompt. Run once under $x_s$ — row $k$
is then exactly the distribution $\tau_k$ was sampled from — and once
under $x_t$, which creates the counterfactual "what the value-wrapped
model would have predicted at each point of the same text". (This
position-parallelism is also what makes training efficient; each
matrix is a ~0.5 GB fp32 temporary, freed after the metrics are read.)

The three metrics differ only in **what they keep from those rows**:
token_delta reads one entry per row (column $\tau_k$), token_jsd
compares entire rows, answer_delta reads two label columns from the
rows at the answer slot (in the $r$-based contexts of passes 3–6).

## 4 · Metric 1: `token_delta` — direction of pressure on the reasoning

Keep **one scalar per row**: the probability of the trace's *actual*
next token, $s_k(\tau_k)$ resp. $t_k(\tau_k)$. Define

$$
\text{token\_delta}(\tau)
\;=\;
\frac{1}{K}\sum_{k=1}^{K}\log t_k(\tau_k)
\;-\;
\frac{1}{K}\sum_{k=1}^{K}\log s_k(\tau_k)
\;=\;
\frac{1}{K}\,\log\frac{p_\theta(\tau \mid x_t)}{p_\theta(\tau \mid x_s)},
$$

the mean per-token log-likelihood ratio of the whole trace (the last
equality is the chain rule: the sequence probability factorizes into
the per-position rows).

**Example.** $K = 100$; summed log-probs
$\log p_\theta(\tau \mid x_t) = -165$,
$\log p_\theta(\tau \mid x_s) = -180$. Then
token_delta $= (-165 + 180)/100 = +0.15$ nats/token: with the value in
context, this reasoning is *more* probable — SDPO pressure would
reinforce reasoning like this. Negative = the value makes the reasoning
less likely → pressure to abandon it.

## 5 · Metric 2: `token_jsd` — the SDPO loss itself (magnitude)

**"Compare entire rows"**: where token_delta reads one entry per row
($s_k(\tau_k)$), token_jsd asks how different the two *distributions*
$s_k$ and $t_k$ are as wholes — all 256k probabilities of both rows
enter via KL sums over $\mathcal{V}$. It therefore sees pressure toward
tokens the trace never used, which token_delta cannot.

**The mixture row** $m_k(v) = (1-\alpha)\,s_k(v) + \alpha\,t_k(v)$ is
the element-wise weighted average of the two rows, itself a
distribution on $\mathcal{V}$. Comparing each side to this midpoint
keeps both KL terms finite — direct $\mathrm{KL}(s\|t)$ blows up
wherever the teacher puts ~zero mass on a token the student uses,
common over 256k tokens. At $\alpha=\tfrac12$ it is also symmetric,
and $\alpha$ interpolates between reverse ($\alpha{=}0$) and forward
($\alpha{=}1$) KL. Per position:

$$
\mathrm{JSD}_\alpha(s_k, t_k) \;=\;
(1-\alpha)\,\mathrm{KL}(s_k \,\|\, m_k) \;+\; \alpha\,\mathrm{KL}(t_k \,\|\, m_k),
$$

boundary cases (as in `generalized_jsd`, matching SDPO's
`compute_self_distillation_loss`):
$\mathrm{JSD}_0 := \mathrm{KL}(t \,\|\, s)$,
$\mathrm{JSD}_1 := \mathrm{KL}(s \,\|\, t)$. For $\alpha=\tfrac12$:
$0 \le \mathrm{JSD}_{1/2} \le \ln 2 \approx 0.69$ nats, with $0$ iff
$s_k = t_k$. The metric is the per-position mean:

$$
\text{token\_jsd}(\tau) \;=\; \frac{1}{K}\sum_{k=1}^{K}\mathrm{JSD}_\alpha(s_k, t_k)
$$

(computed vectorized: element-wise mixture/KL on the two
$[K \times |\mathcal{V}|]$ tensors from passes 1–2, summed over the
vocab dimension — no loop over $k$).

**Relation to the SDPO loss** — an identity, not an analogy: SDPO's
self-distillation loss for a rollout is the mean over response
positions of exactly this $\mathrm{JSD}_\alpha$, with $t_k$ from the
reprompted context and the same $\alpha$ (read from the training yaml).
The probe evaluates it on base-model rows = **the value the training
loss would take at step 0**. The two ingredients training adds — EMA
teacher and importance ratio — equal the actor and 1 at step 0, so the
identity is exact there.

**Direction-free value, directional gradient.** Only the student rows
$s_k$ depend on $\theta$ (the teacher side is detached), so
$\nabla_\theta\,\mathrm{JSD}$ is precisely the update that moves each
$s_k$ toward its target $t_k$, entry by entry over the vocabulary.
"Direction-free" only means the *scalar* cannot tell the analyst which
way the pull points — the optimizer always has the full gradient;
recovering the direction for behavior is answer_delta's job (§6).

**Toy example** ($\mathcal{V} = \{\text{trust}, \text{gain}, \text{the}\}$,
one position, $\alpha = 0.5$): $s = (0.6, 0.3, 0.1)$,
$t = (0.8, 0.1, 0.1)$ — the value upweights "trust", downweights
"gain". Mixture $m = (0.7, 0.2, 0.1)$.

$$
\mathrm{KL}(s\|m) = 0.6\ln\tfrac{0.6}{0.7} + 0.3\ln\tfrac{0.3}{0.2} + 0.1\ln 1 \approx 0.029
$$
$$
\mathrm{KL}(t\|m) = 0.8\ln\tfrac{0.8}{0.7} + 0.1\ln\tfrac{0.1}{0.2} + 0.1\ln 1 \approx 0.038
$$
$$
\mathrm{JSD}_{0.5} = \tfrac12(0.029 + 0.038) \approx 0.033 \text{ nats}
$$

— the same order as the real measurements (0.04–0.06 nats/token).

**What the 0.033 nats mean.** A loss *value*, not an update: this
position's contribution to the SDPO loss (≈5% of the $\ln 2$ ceiling).
At this point of the reasoning the moral value shifts the model's
next-token opinion by this much — here, mass moved from "gain" to
"trust". Training would differentiate it and nudge $\theta$ so $s$
moves toward $t$; over steps the number shrinks toward 0. The probe
just measures how much work training *would have to do* here.

## 6 · Metric 3: `answer_delta` — direction at the decision token

Let $M$ be the character index of the label group found by the parser's
own `find_action_marker`; the **reasoning prefix** $r$ is the trace text
up to $M$ (re-tokenized), i.e. everything up to and including
`Action:`. The trace's own conclusion is discarded. Let
$a^C = (a^C_1, \dots, a^C_{m_C})$ and $a^D$ be the token sequences of
the forced continuations ` action3` and ` action4` (leading space
included; here $a^C = ($` action`$,$ `3`$)$, $m_C = 2$).

For a label $a$ of length $m$ and prompt $x \in \{x_s, x_t\}$, the
teacher-forced label log-probability is (chain rule again):

$$
\ell(a \mid x) \;=\; \sum_{k=1}^{m} \log p_\theta\!\bigl(a_k \,\bigm|\, x \oplus r \oplus a_{<k}\bigr)
\;=\; \log p_\theta(a \mid x \oplus r).
$$

**Worked numbers.** Under $x_s$: say
$p_\theta(\text{" action"} \mid x_s \oplus r) = 0.20$ and
$p_\theta(\text{"3"} \mid x_s \oplus r \oplus \text{" action"}) = 0.55$,
so $\ell(a^C \mid x_s) = \log 0.20 + \log 0.55 = -2.21$. Forcing
$a^D$ instead (same context): $\ell(a^D \mid x_s) = -1.81$. Same two
passes under $x_t$: $\ell(a^C \mid x_t) = -1.11$,
$\ell(a^D \mid x_t) = -2.61$.

**Shared-prefix cancellation.** $a^C_1 = a^D_1 = $ ` action` and the
context is identical, so $\log p_\theta(a_1 \mid x \oplus r)$ is the
*same number* (here $\log 0.20$) in both $\ell$'s and cancels in the
log-odds below: $\Lambda$ reduces to
$\log p(\text{"3"}) - \log p(\text{"4"})$ at the one slot where the
labels diverge. The full label must still be forced ($a_1$ has to sit
in context to condition that slot), but only the diverging tokens carry
the measurement. With randomized single-letter labels (robustness axis)
there is no shared prefix and nothing cancels.

Define the label log-odds under each prompt, and their shift:

$$
\Lambda(x) \;=\; \ell(a^C \mid x) - \ell(a^D \mid x)
\;=\; \log \frac{p_\theta(a^C \mid x \oplus r)}{p_\theta(a^D \mid x \oplus r)}
$$

$$
\boxed{\;\text{answer\_delta}(\tau) \;=\; \Lambda(x_t) - \Lambda(x_s)\;}
$$

With the numbers above: $\Lambda(x_s) = -0.40$, $\Lambda(x_t) = +1.50$,
answer_delta $= +1.90$. Interpreting $\Lambda$ via the two-label
renormalization $P(C) = \sigma(\Lambda) = 1/(1+e^{-\Lambda})$: the
plain-prompted model, given this reasoning, picks C with
$\sigma(-0.40) \approx 40\%$; add the moral value (same reasoning!) and
it picks C with $\sigma(+1.50) \approx 82\%$. This trace counts C-ward
in the mode split.

## 7 · How the three metrics divide the SDPO signal

SDPO applies distributional pressure at EVERY response position; the
metrics slice that one signal by question:

| metric | keeps from the rows | question answered |
|---|---|---|
| token_jsd | full rows $s_k, t_k$, all $k$ | **How much** total pressure? (= the step-0 loss) |
| token_delta | scalars $s_k(\tau_k), t_k(\tau_k)$ | **Which way** on the reasoning as written — reinforce or suppress it? |
| answer_delta | label log-odds at the answer slot | **Which way** on the decision, reasoning held fixed? |

The adoption rules key on answer_delta (direction of behavior is the
screening question); token_jsd tells you the pressure budget exists at
all; token_delta whether the reasoning style itself gets pulled toward
or away.

## 8 · Bookkeeping

- **6 teacher-forced passes per trace** (2 full-trace + 4 label), all
  deterministic — sampling noise exists only in *which* 32 traces were
  drawn per state (§2, temperature-$T$ policy), never in their scores.
- Rows are computed in fp32 only over the $K$ continuation positions
  (a full-sequence $[\text{seq} \times 256\text{k}]$ fp32 tensor would
  be GBs).
- A trace with no `Action:` marker has no answer slot →
  `answer_delta = None` (counted in `n_no_answer_marker`); empty traces
  and parse failures are counted separately (`n_empty`, `n_parse_fail`).
- Seam caveat: $r$ and $\tau$ are re-tokenized and concatenated to the
  prompt ids, which can differ from how the joined text would tokenize.
  Identical construction on both sides, so all *deltas* are consistent;
  the absolute log-probs are not "the model's natural tokenization"
  (`continuation_logprob` docstring).
