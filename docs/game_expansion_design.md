# Game Battery Expansion: Design Investigation (2026-08-13)

Status: DESIGN — no code written. Inputs: the two-model single-turn
screen analysis (gemma-2-9b-it + Qwen3-8B, `eval_results/teacher_signal/
single_turn_screen_*/analysis/results_*.md`), `docs/pgg_design.md`
(2026-08-10, still the commons blueprint — this doc does not supersede
it), the generosity-arm submission (jobs 3077037–41, in flight), and an
explorative survey of external frameworks (OpenSpiel, CoopEval, GovSim,
Concordia, Melting Pot) conducted 2026-08-13.

Scope: which games and training paradigms to add so the agent learns
cooperative *and* strategic behaviour that is measurable and transfers
to the commons; plus the explorative directions (solver teachers,
strategic curricula, external simulators) recorded so they are not
re-derived later.

---

## 1. Selection criteria (the filter everything below passed through)

A game earns a slot iff:

1. **Tension.** The moral principle and self-interest conflict in at
   least one state. Without tension the teacher adds nothing over the
   student and the SDPO signal is empty — the stag-hunt null,
   generalized. Corollary: harmony games and zero-sum games are both
   excluded, for opposite reasons.
2. **State-varying tension.** The conflict must differ across states so
   *conditional* structure is measurable (the Δ-gap logic). A game with
   uniform tension measures a level, not a policy.
3. **Measurability.** Discrete actions, defined payoffs, and a
   behavioral statistic that maps onto a named construct
   (trustworthiness, fairness, honesty). Soft-judged environments fail
   this criterion regardless of realism.
4. **Instrument compatibility.** Single-decision reducible (screen +
   probe-B), text-renderable under the presentation axes, parseable
   labels. Multi-turn-only games are not excluded but are gated on the
   Phase-2 machinery.

**Probe-B is the general assay.** answer_delta at step 0 measures "does
this context bind to the decision" for ANY teacher context in ANY
environment — it is a teacher-signal instrument, not a moral-values
instrument. Every new game and every new feedback type below gets
screened the same way the wordings were.

## 2. What the 2026-08-13 two-model analysis settles

1. **PD is the ToC screening instrument.** A linear-PGG commons is an
   N-player PD (defection dominant, mutual defection Pareto-inferior);
   the core transferable skill is temptation-resistance conditional on
   others' behaviour. The arms separate most cleanly in PD on both
   models.
2. **SH and Chicken are instruments, not teachers.** SH has no
   temptation (trust only) → compliance detector: a flat lift there is
   instruction-following (utilitarian trips it on both models). Chicken
   rewards anti-coordination → override-strength probe: on qwen the
   moral texts erase a *correct* −48pp anti-coordination gap, the
   warning light for principles that steamroll all payoff reasoning.
   Their SH-like/CH-like regimes re-enter training *inside* PGG
   (threshold variant, r-continuum — pgg_design §3.1) in N-player,
   resource-framed form: nothing bilateral to unlearn.
3. **Model-dependence of the arms.** deontological(+repair) is the only
   arm strong on both models (PD Δ_opp gemma +65/+68, qwen +35/+61).
   Utilitarian is model-dependent in the worst way: inert-to-perverse
   on gemma, unconditional saturation (fully farmable) on qwen.
   Universalization is viable on qwen, behaviourally weak on gemma —
   its Session-2 case rests mostly on qwen.
4. **Defection spirals are the live failure mode.** deon+repair leaves
   P(C|DD) low (gemma 6–32, qwen 27–57): repair fixes one-sided
   spirals (DC → 91–98) but nobody moves first after mutual breakdown.
   The `generosity` rider (generous-TFT in principle form, DD escape
   clause) is in flight as `deontological+repair+generosity`
   (configs/sweeps/generosity_arm{,_qwen3}.yaml). Adoption rule: DD
   lifts to an INTERMEDIATE level (~30–60) without eroding the low CD
   cell and with Δ_opp intact; matrix is the overshoot-risk
   representation.

## 3. Tier 1 — new single-decision games (fit the screen machinery now)

Ordered by recommended build sequence. PGG (pgg_design.md P0/P1) stays
first on the critical path; the games here are the battery extension
after it. Trust + ultimatum/dictator are one implementation unit — they
share the two pieces of genuinely new machinery: a **role axis** (which
seat the agent occupies) and **in-episode sequencing** (the conditioning
state is the other player's *actual* move, not fabricated history — no
repair-style ownership problem).

### 3.1 Trust Game (Berg 1995), binarized

Investor: send-all / keep endowment E=10; sent amount triples; trustee:
return-half / keep-all. Outcomes: keep = (10, 0); send→return =
(15, 15); send→keep-all = (0, 30).

- **Measures:** trustee cell = trustworthiness under explicit voluntary
  vulnerability — the deon wording verbatim. Investor cell = trust,
  conditionable on trustee reputation.
- **The wedge that earns the slot:** joint payoff is constant across
  the trustee's options (30 either way), so utilitarian is INDIFFERENT
  exactly where deon binds maximally. Cleanest arm dissociation in the
  battery; also the natural probe-B target (answer_delta should be
  ~0 for util, large for deon, at the trustee state).
- **States:** (role, other's move): trustee|sent, investor|first,
  investor|reputation-r for discrete r.
- **Code deltas:** asymmetric payoff definition; role axis in
  EpisodeConfig + sweep; prose/matrix templates (matrix = 2×2 outcome
  table over (their move, your move)); per-role C-label convention
  (send/return = C) for probe-B log-odds. Parsing, protocol, sweep
  plumbing unchanged.

### 3.2 Ultimatum + Dictator pair (Güth 1982)

Proposer: fair (5/5) / unfair (8/2) split of 10; responder: accept /
reject (reject → 0/0). Dictator: same proposer choice, no veto.

- **Measures:** the internalization question as a game contrast.
  Ultimatum−dictator offer gap = the *strategic* component of fairness;
  dictator giving = the *internalized* component. Training that moves
  ultimatum but not dictator distilled strategy; both → the value.
  This is the privileged/non-privileged arm logic implemented in games
  rather than prompts.
- **Responder cell** measures costly punishment of unfairness
  (reject-unfair rate) — the 2-player precursor of PGG peer punishment.
- **States:** proposer|veto, proposer|no-veto, responder|fair-offer,
  responder|unfair-offer. Four cells, all single-decision.
- **Code deltas:** reuses trust's role machinery entirely; one new
  payoff table.

### 3.3 Promise game (cheap talk + PD or trust)

One pre-play message round (binary v1: promise-to-cooperate / no
promise, sampled or opponent-scripted), then the base game.

- **Measures:** behavioural honesty — promise→action consistency (own
  promise kept?) and credulity/responsiveness (cooperation lift after
  receiving a promise). Charness–Dufwenberg anchor (guilt aversion).
- **Why it earns a slot:** the most LLM-native addition possible — the
  message channel is text; and "break trust" in the deon wording binds
  on promise-breaking even more literally than on defection. No other
  game in the battery measures honesty at all.
- **v1 keeps it single-decision:** the received message is part of the
  state (like fabricated history, but cheap-talk); the agent's own
  promise can be scripted in fabricated history ("you promised to
  cooperate") — with the same ownership caveat as repair, noted.
- **Code deltas:** a message line in the prompt builder + message axis
  in the state grid. Smallest delta in Tier 1.

### 3.4 Indirect-reciprocity donor game (Nowak–Sigmund)

Pairs rotate; agent sees a stranger's reputation (how they treated
THIRD parties) and chooses help (cost c, benefit b>c to them) / pass.

- **Measures:** reputation-conditioned helping gap — the third-party
  Δ_opp. Directly tests the 2026-08-10 scope generalization: "those who
  act in good faith" with no "toward you". If deon conditions here, the
  dyadic→general rewording did its job; if not, the transfer claim for
  commons is weakened at the wording level, cheaper to learn here than
  in GovSim.
- **States:** reputation ∈ {good, bad, unknown} × (optionally) own
  reputation. Single-decision by construction.
- **Code deltas:** opponent "move" generalizes to a reputation token in
  the prompt; payoffs asymmetric (helper pays, recipient gains) but
  role-symmetric across episodes — no role axis needed.

## 4. Tier 2 — PGG parameter variants (near-free once P0 exists)

All three are parameters of the pgg_design engine, not new games:

1. **r-continuum / regime axis** — already designed (pgg_design §3.1):
   dilemma / compliance-null (r>N) / waste-null (r<1). Also the
   pre-registered test of the 2026-08-12 cross-game payoff-gradient
   observation (arms track the payoff quantity their wording names).
2. **Threshold/step-level PGG** — the N-player stag hunt (pgg_design
   v3 lists the regenerating-stock version; the static threshold
   variant is cheaper and single-round). Replaces 2-player SH as a
   *training* environment; SH itself stays eval-only.
3. **Asymmetric-endowment PGG** (new since pgg_design) — unequal E_i.
   Measures WHICH fairness norm was internalized: equal absolute
   contribution vs equal proportional sacrifice — the moral families
   genuinely disagree (util → contributions from whoever values them
   least; deon/virtue → proportionality readings). Invisible in every
   symmetric game. Delta: per-player E in PGG_PARAMS + prompt line;
   metric = contribution as fraction of endowment, sliced by E_i.

## 5. Tier 3 — gated on multi-turn Phase 2 (record now, build later)

1. **Noisy iterated PD** — THE testbed for the generosity arm's actual
   claim. Metrics: spiral entry rate, recovery half-life after an
   accidental defection, exploitability by always-defect. Canonical
   result to reproduce: plain TFT locks in under noise; generous/
   contrite variants dominate.
2. **PGG + punishment stage** (Fehr–Gächter) — second decision: costly
   sanctioning. Metrics: punishment targeting (defectors vs antisocial
   punishment of cooperators), proportionality (graduated sanctions,
   Ostrom). Home of the future `strategic_nplayer` rider
   (moral_values.py docstring).
3. **Walk-away PD / partner choice** — third action: leave and rematch.
   Cooperation sustained by exclusion, the mechanism real commons use.
   Metrics: who is left, who is kept, cooperation lift under partner
   choice.
4. **Centipede** — sequential trust, growing pot; exit-round
   distribution = a graded "trust horizon" scalar. Cheap; lowest
   priority.

## 6. Rejected / deprioritized (with reasons, so they stay rejected)

- **SH / Chicken as training games** — no temptation / wrong
  conditional (anti-coordination; trains exploitable yielding). Kept as
  eval instruments: compliance null + override probe (§2.2).
- **Battle of the Sexes** — alternation conventions need long horizons;
  BoS was already rejected in pgg_design for C/D-semantic reasons.
- **Bargaining (Nash demand, alternating offers)** — fairness signal
  confounded with strategic sophistication.
- **Gift exchange** — measures positive reciprocity to generosity;
  largely redundant with trust game; revisit only if trust results
  suggest the positive/negative reciprocity split matters.
- **Gridworld SSDs (Melting Pot: Harvest/Cleanup)** — conceptually our
  domain scaled up, but pixel/grid observations break the entire
  text-rendering + parse-and-probe stack. Mine their *scenario
  taxonomy* (background populations probing exploitability,
  free-riding, trust) for eval design; do not run them.
- **OpenSpiel catalog as environment** — see §9.1.

## 7. Training paradigms (orthogonal to the game axis)

1. **Institution internalization** (biggest framing payoff). Train
   UNDER a cooperation-sustaining institution — reputation visibility,
   peer punishment, contracts (CoopEval's mechanism axis) — evaluate
   with the institution REMOVED. Exact structural parallel to the
   universalization/GovSim privileged-prompt logic, applied to
   mechanisms instead of principles. Unifying thesis frame: prompts and
   institutions are both scaffolds; SDPO asks what survives scaffold
   removal.
2. **Self-play / population training.** Current plan trains vs fixed
   opponents. Against copies of itself, cooperation equilibria become
   self-reinforcing and spirals self-inflicted (the 5-agents-commons
   question as a training loop). Evaluation addition: CROSS-PLAY
   between independently trained seeds — tests whether cooperation
   generalizes to novel partners or is a private convention (the
   zero-shot-coordination lesson from the Hanabi literature).
3. **Adversarial curriculum.** Mix exploiters (always-defect,
   best-response-to-previous-checkpoint) into the training opponent
   registry so conditional cooperation is load-bearing DURING learning.
   Cheap; pairs with the exploitability metric (§8).
4. **Strategic-then-moral curriculum** (explorative, records the
   OpenSpiel discussion's conclusion). Stage 1: SDPO with SOLVER
   teachers (CFR/best-response output as teacher context) on small
   solvable games → strategic competence. Stage 2: moral-principle SDPO
   on top. Target phenotype: an agent that demonstrably CAN exploit
   (low regret, high unleashed exploitation capacity) and measurably
   DOESN'T (conditional cooperation, low exploitability) — closes the
   "cooperation = incompetence at exploiting" critique. Caveats
   recorded: zero-sum Nash = unexploitable, NOT maximally-winning
   (exploiting weak opponents is a deviation with its own risks);
   mixed-strategy equilibria require calibrated randomization, which
   LLMs are bad at (the per-state P(C) table measures exactly this);
   solvers don't scale past small games, so stage 1 bets on
   generalization of the reasoning.

## 8. Metric additions

Two statistics off the SAME per-state P(C) table the eval already
produces — an afternoon of numpy against behavioral JSONs, no new
dependencies:

1. **Exploitability** (other-directed): payoff of the exact
   best-responding opponent against the agent's state-conditioned
   policy, minus that opponent's payoff vs a reference (e.g. TFT).
   Distinguishes conditional from naive cooperation — deon+repair and
   utilitarian can share a mean cooperation level while utilitarian is
   maximally farmable. Report per arm, per game.
2. **Regret** (self-directed, already in pgg_design §4): the agent's
   own forgone payoff given opponents' actual play — the PRICE of the
   principle. The pair is the right report: regret = what the principle
   costs the agent; exploitability = what the agent leaves open to
   predators. Training target: regret rises modestly, exploitability
   stays near zero.

## 9. External frameworks (surveyed 2026-08-13; verdicts)

### 9.1 OpenSpiel

NOT an environment substrate: no PGG/trust/ultimatum/dictator in the
catalog; states are integer-action engine objects, so the whole
instrument stack (representation axes, fabricated states,
parse-and-probe) would still be written by us per game while a C++
source build (aarch64, unstated support) replaces only the trivial
payoff-lookup 5% of the code. Adopt its CONCEPTS instead:
exploitability/best-response (§8, no dependency needed at our game
sizes), PSRO-style opponent populations (§7.3), and its `chat_games`
line ("States as Strings", Gemp et al. 2024) as related work — they
steer LLMs with solvers at inference time; the §7.4 curriculum would
distill the steering away. Revisit only for engine-heavy cooperative
games (Hanabi-class), where the engine is 95% of the work.

### 9.2 Others

- **CoopEval** (arXiv:2604.15267) — closest external match: LLM agents
  on PD/Trust/PGG/Traveler's-Dilemma × cooperation-sustaining
  mechanisms (repetition, reputation, mediators, contracts). ACTION
  ITEM: read before freezing PGG P1 prompts — protocol compatibility =
  free comparability; "prompt-time mechanisms vs distilled principles"
  is a natural paper framing against it.
- **GovSim** — unchanged: the transfer endpoint (internalization =
  performance with its universalization prompt OFF).
- **Concordia** (+ contest scenarios) — far-transfer demo only;
  outcomes judged, not payoff-exact.
- **Social Gym** (arXiv:2608.09128), **MAC-SPGG** (sequential PGG for
  multi-LLM ensembles) — related work to cite; MAC-SPGG's sequential
  formalization is prior art for PGG v2+.
- **PettingZoo** — same impedance objection as OpenSpiel, weaker
  algorithm payoff.

### 9.3 SDPO feedback taxonomy (why this program is bigger than games)

The paradigm needs only: context that shifts the policy toward better
actions, insertable at rollout time. Five sources — (1) oracle/solver
(engines, test execution, worked solutions), (2) privileged information
(hidden opponent state; post-game role revelation in social deduction),
(3) hindsight/outcome (universal but leaks unknowables — distilling
toward it trains unjustified confidence unless the outcome is inferable
from student-visible state), (4) reference knowledge (walkthroughs,
wikis — internalizing retrieval), (5) normative (this project).
Recorded so the "other simulators?" question has a standing answer:
choose by feedback type first, environment second; screen any new
(environment, feedback) pair with probe-B before training on it.

## 10. Build order and open decisions

Order: **PGG P0/P1** (critical path, designed) → **trust +
ultimatum/dictator** (one unit: role axis + sequencing) → **promise
game** (smallest delta) → **donor game** → **Tier-2 PGG variants** →
Tier 3 with multi-turn Phase 2. Paradigms: adversarial curriculum can
enter at Session-2 training directly; institution internalization needs
a design pass (which institution first — reputation is cheapest);
strategic-then-moral is a second-thesis-sized bet, parked.

Open decisions (recommendations marked):

1. Role axis: new EpisodeConfig field + sweep axis (RECOMMENDED), vs
   encoding role into the game name (rejected: breaks the
   game-as-structure convention).
2. Per-role C-label convention for probe-B (send/return/fair/accept =
   C): config table in the game layer (RECOMMENDED), not inference from
   payoffs.
3. Promise-game v1 own-promise handling: fabricated ("you promised…",
   RECOMMENDED for the screen, ownership caveat documented) vs
   generated (needs two-decision episodes → Phase 2).
4. Screen budget for new games: trust/UG/dictator have 3–4 states, so
   400 eps/cell at 100/state stays the norm; donor game with own-rep
   axis doubles to 6 — decide when the state grid is final.
5. Whether asymmetric-endowment PGG enters v1 or waits for v2 graded
   contributions (RECOMMENDED: wait — fraction-of-endowment metrics
   need graded actions to be meaningful).
