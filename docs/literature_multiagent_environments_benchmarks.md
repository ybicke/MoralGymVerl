# Literature Survey: Multi-Agent / Multi-Turn Training Environments and Benchmarks for Learning Cooperation (2023 – Aug 2026)

Compiled 2026-08-25. Six parallel web surveys (social-dilemma benchmarks; negotiation & social deduction; MARL suites & LLM-RL frameworks; cooperation-training methods; multi-turn RL; Nov-2025–Aug-2026 recent work). Every arXiv ID / GitHub URL was resolved by fetch or search-result match during the survey; items that could not be verified are marked as such or omitted. Short form `2404.16698` = `https://arxiv.org/abs/2404.16698`.

Column legend: **Agents** = LLM players per instance · **MT** = multi-turn/repeated · **Comm** = free-text channel · **Train** = usable as RL training environment (Y / P=partial, needs wrapper / N) · **Bench** = usable as standardized benchmark with published baselines.

---

## 0. Executive summary — what to use for what

| Need | Recommended (ranked) | Why |
|---|---|---|
| **Cheap RL-ready text environments with defect incentive** | 1. own MoralGym/MoralGymVerl PD/PGG · 2. **TextArena** (Iterated PD w/ chat, Stag Hunt, PGG, Iterated Ultimatum, Negotiation) `2504.11442` · 3. **AdAlignLLM** testbed (IPD, Split, Trust-and-Split) `2511.19405` · 4. **CaSiNo / Deal-or-No-Deal** self-play · 5. **GLEE** bargaining/persuasion `2410.05254` | Only these expose step-level APIs or trivially wrappable game loops; all cheap at 8B. |
| **Cheap-talk + binding action (matches decision+prose split)** | **Cheap Talk, Empty Promise** promise-stage protocol `2604.04782` · **Bilateral Trade w/ private info** `2604.16472` · Trust-and-Split · TextArena IPD chat rounds | Promise-breaking rate is a directly verifiable deontological target. |
| **N-player / richer transfer benchmarks (eval)** | **CoopEval** (ICML'26) `2604.15267` · **GovSim** `2404.16698` · **M3-Bench** (24 games, process-aware) `2601.08462` · **MoralSim** (ethics vs payoff) `2505.19212` · **GAMA-Bench** PGG/Diner `2403.11807` · **SanctSim** PGG w/ institutions `2506.23276` · **Concordia** scenarios `2512.03318` · **GT-HarmBench** `2602.12316` | All eval harnesses; all report the same failure (cooperation collapses with varied co-players) that principle-internalisation should fix. |
| **External leaderboard at your model size** | **MindGames arena** (NeurIPS'25 comp; ≤9B open-weight IPD division) `2605.29512` | Only competition with an 8–9B IPD track. |
| **Deception / honesty probes (post-training, not training)** | **Mini-Mafia** `2509.23023` · **Among Us Deception-ELO** `2504.04072` · **ONUW** `2405.19946` · **LieCraft** `2603.06874` · **When Agents Lie** `2607.05132` · TERMS-Bench misstated terms `2605.13909` | Social deduction binds a "don't lie" principle asymmetrically by role → probes, not envs. |
| **Pure-coordination controls (no defect incentive)** | **Hanabi** (Sparks of Cooperative Reasoning, Qwen3-4B RL) `2601.18077` · **Collab-Overcooked** `2502.20073` · **Codenames** `2412.11373` · **LLM-Coordination** `2310.03903` | Check that moral training does not erode coordination ability. |
| **Strongest non-moral RL baseline to compare against** | **Advantage Alignment for LLMs** `2511.19405` (code) · **SEPO** exploitability/collusion penalties `2605.30854` · **GTAlign** welfare reward on verl `2510.08872` · CoopEval mechanisms (contracts, mediators) | Opponent-shaping / mechanism / welfare routes vs. normative route. |
| **Multi-turn credit assignment on verl** | **GiGPO / verl-agent** `2505.10978` · **Dr. MAS** per-agent normalisation (verl recipe) `2602.08847` · **SPIRAL RAE** per-role baselines `2506.24119` · **MT-GRPO** turn rewards `2505.11821` · **PBSD** privileged self-distillation credit `2606.09348` | Recurring-history games fit anchor-state grouping; asymmetric roles need per-role baselines. |
| **Two trainable co-players on verl** | **PettingLLMs / AT-GRPO** `2510.11062` · Agent Lightning proxy `2608.17528` · (non-verl) **MARTI** `2602.07848`, **MARFT** `2504.16129`, AdAlignLLM LoRA-hot-swap | verl agent loop is one trajectory per prompt; multi-trajectory is "under discussion" (issue #2618). |

**Headline gap (all six surveys agree):** no published work trains LLM cooperation from an explicit moral-principle signal via on-policy self-distillation. SDPO-family methods (SDPO, CRPO, PBSD, ZPPO) use verifiable feedback; RL-for-cooperation work uses opponent shaping, exploitability penalties, welfare rewards, or institutional mechanisms; Tennant et al. (ICLR'25) has no direct 2026 successor. Nearest cousin: *You Only Align Once* `2605.27586` (off-policy distillation from a different teacher into Qwen3-14B, no code).

---

## 1. Social-dilemma & economic-game environments (matrix, PGG, trust, CPR)

### 1A. Training-ready (step API or trivial wrapper, cheap at 8B)

| Resource | Year | Links | Games | Agents / MT / Comm | Train | Bench | Relevance |
|---|---|---|---|---|---|---|---|
| **TextArena** | 2025 | `2504.11442` · [github](https://github.com/LeonGuertler/TextArena) | Iterated PD (3 chat turns + decision/round), Iterated Stag Hunt, Public Goods, Iterated Ultimatum, Negotiation (2–15p), auctions, 100+ games | 2–15 / Y / Y | **Y** (Gym API; SPIRAL trains on it) | P (leaderboard mostly zero-sum) | Cheapest external pool of cheap-talk dilemmas for a verl loop |
| **AdAlignLLM testbed** | 2025 | `2511.19405` · [github](https://github.com/dereckpiche/AdAlignLLM) | IPD (10 rounds), Split No-Comm, Trust-and-Split (coins + 1 msg/round) | 2 / Y / Y | **Y** (vLLM async + LoRA hot-swap, Qwen2.5-7B) | P | Closest engineering sibling to MoralGym; group-relative CRN baseline drop-in |
| **Tennant LLM_morality** | 2024 | `2410.01639` · [github](https://github.com/liza-tennant/LLM_morality) | IPD vs fixed opponents; transfer to Stag Hunt, Chicken… | 1 vs scripted / Y / N | **Y** | P | Direct ancestor; reward-function definitions = comparison point |
| **GLEE** | 2024 (ICLR'25) | `2410.05254` · [github](https://github.com/eilamshapira/GLEE) | Alternating-offer bargaining, multi-issue negotiation, sender–receiver persuasion; 954K LLM + 3.4K human games | 2 / Y / configurable | **Y** (config-driven, efficiency/fairness metrics) | Y | Persuasion = explicit lie-to-gain; ultimatum fairness |
| **Cheap Talk, Empty Promise** protocol | 2026 | `2604.04782`; follow-up *When Agents Lie* `2607.05132` | Public-promise stage added to 6 normal-form games; ~57% promise-breaking, >90% premeditated | 2–n / one-shot+repeated / Y | **Y** (trivial to bolt onto PD/PGG; no repo) | Y | Cleanest promise-keeping probe for a deontological principle |
| **Bilateral Trade w/ Private Information** | 2026 | `2604.16472` | Event-driven bargaining, tool-call offers + free text; SFT+GRPO on Qwen3-8B/14B | 2 / Y / Y | **Y** (designed as RL env; code unconfirmed) | Y | Structure = decision+prose split; misrepresentation principle testable |
| **MoralSim** | 2025 | `2505.19212` · [github](https://github.com/sbackmann/moralsim) | Repeated PD & PGG re-skinned so the ethical action is dominated | 2 / 4 / Y / N | P (open code, needs RL wrapper) | Y | Tests principle-following when ethics and payoff conflict |
| **CoopEval** | 2026 (ICML) | `2604.15267` · [github](https://github.com/Xiao215/CoopEval) | PD, Traveler's Dilemma, Trust, PGG (+Stag Hunt, Matching Pennies) × mechanisms (repetition, reputation, mediator, contracts); replicator dynamics | 2–3 / Y / in mediation & contracting | P (YAML games, HF-local models) | **Y** | Repetition-induced cooperation collapses with varying co-players — the target failure mode |
| **GovSim** | 2024 (NeurIPS) | `2404.16698` · [github](https://github.com/giorgiopiatti/GovSim) · repro `2505.09289` | Fishery / pasture / pollution CPR, 12 months harvest + group chat | 5 / Y / Y | P (vLLM backend; minutes per episode) | **Y** | Universalization prompt causally improves sustainability — the transfer endpoint |
| **SanctSim** (Corrupted by Reasoning) | 2025 | `2506.23276` · [github](https://github.com/davidguzmanp/SanctSim) | Repeated PGG with sanctioning vs sanction-free institutions | groups / Y / N | P | Y | Reasoning models free-ride more → relevant to Qwen3 think on/off |
| **GAMA(γ)-Bench** | 2024 (ICLR'25) | `2403.11807` · [github](https://github.com/CUHK-ARISE/GAMABench) | Guess-2/3, El Farol, Divide-the-Dollar, PGG, Diner's Dilemma, auction, Battle Royale, Pirate | ≥3 / Y / N | P (API-oriented; PGG/Diner extractable) | Y | N-player transfer eval |
| **Agent-Trust** | 2024 (NeurIPS) | `2402.04559` · [github](https://github.com/camel-ai/agent-trust) | Trust-game variants vs human data | 2 / partial / N | P | Y | Ready harness for Tier-1 trust game |
| **ALIGN (Talk, Judge, Cooperate)** | 2026 (ICML) | `2602.07777` · [github](https://github.com/shuhui-zhu/ALIGN) | Indirect-reciprocity games with free-form gossip / reputation | multi / Y / Y | P (config-driven) | P | Reputation-mechanism contrast arm |
| **SocialJax** | ICLR'26 | [github](https://github.com/cooperativex/SocialJax) · [openreview](https://openreview.net/forum?id=Qg6kHVN91t) | JAX SSDs (Coins, Commons Harvest ×3, Clean Up, Territory, Coop Mining, PD Arena), ~50× faster than Melting Pot | 2–many / Y / N | Y for MARL, N for LLM without text wrapper | Y (MARL) | Fast ground-truth SSD dynamics; non-LLM baseline population |
| **Autoresearch for SSDs** | 2026 | `2605.30003` · [github](https://github.com/vicgalle/autoresearch-social-dilemmas) | Cleanup (N=10), Gathering (N=4) as lightweight Python; LLM synthesises policies for utilitarian vs Rawlsian welfare | 4–10 / Y / N | P (policies are code) | P | Explicit ethical welfare objectives as optimisation targets |

### 1B. Evaluation-only benchmarks (social dilemmas, game theory)

| Resource | Year | Links | Games | Agents / MT / Comm | Bench | Relevance |
|---|---|---|---|---|---|---|
| **M3-Bench** | 2026 | `2601.08462` · [github](https://github.com/FredericVAN/PKU_M3Bench) · [HF](https://huggingface.co/spaces/PKU-JX-LAB/M3-Bench) | 24 mixed-motive games, 4 levels (one-shot → repeated → group → hidden-info/language), silent vs comm; 11 LLMs + n=50 humans | 2–multi / Y / Y | Y (harness "on acceptance") | "Overthink–undercommunicate"; process-aware metrics for rationale-vs-behaviour |
| **GT-HarmBench** (own group) | 2026 | `2602.12316` · [github](https://github.com/causalNLP/gt-harmbench) | ~1.5–2K high-stakes scenarios mapped to PD / Stag Hunt / Chicken; 15 models | 2 / N / N | Y | Held-out realistic-framing generalisation eval |
| **TMGBench** | 2024 | `2410.10479` | All 144 Robinson–Goforth 2×2 topologies, narrative reframings, sequential/nested compositions | 2 / partial / N | Y | Payoff-topology robustness |
| **MAgIC** | 2023 (EMNLP'24) | `2311.08562` · [site](https://zhiyuanhubj.github.io/MAgIC/) | Chameleon, Undercover, cost-sharing, multi-player PD, PGG | 3–6 / Y / Y | Y | Rationality-vs-cooperation decomposition |
| **Behavioural-econ chatbot benchmark** | 2024 | `2412.12362` | Dictator, Ultimatum, Trust, PGG, Bomb Risk, PD; MobLab human distributions | 1 / N / N | Y | Quick pre/post social-preference profile |
| **Benevolent Dictators?** | 2025 | `2511.08721` | Dictator game across models/personas | 1 / N / N | P | Dictator-arm baselines |
| **Machiavelli** | 2023 (ICML) | `2304.03279` · [github](https://github.com/aypan17/machiavelli) | 134 text adventures, 572K ethics annotations | 1 / Y / N | Y | Single-agent deontological-violation labels; complementary morality eval |
| **GTBench** | 2024 (NeurIPS) | `2402.12348` · [github](https://github.com/jinhaoduan/GTBench) | 10 OpenSpiel games, mostly zero-sum | 2 / Y / negotiation only | Y | Strategic-reasoning control |
| **LLMArena** | 2024 (ACL) | `2402.16499` · [github](https://github.com/THU-BPM/LLMArena) | 7 games incl. Bargain, Undercover, Hanabi-like; TrueSkill | 2–N / Y / some | Y | Superseded by TextArena in breadth |
| **GameBench** | 2024 | `2406.06613` · [github](https://github.com/Joshuaclymer/GameBench) | 9 under-represented strategic games | 2+ / Y / some | P | Low |
| **Do LLMs Beat Nash?** | 2026 | `2608.12547` | One-shot self-play, 7 matrix archetypes, 13 models (undergrad preprint) | 2 / N / N | P | Cheap focal-point eval |
| **Humans Are More Diverse (AI development races)** | 2026 | `2608.01193` | Repeated safe-slow vs fast-risky race, 2–5 players | 2–5 / Y / ? | P | Multi-player dilemma with safety framing |

### 1C. Population / evolutionary / behavioural studies (protocols to replicate)

| Resource | Year | Links | Protocol | Relevance |
|---|---|---|---|---|
| **Playing repeated games with LLMs** (Akata) | 2023 / NHB 2025 | `2305.16867` | All 2×2 families, 10-round IPD, BoS | Standard reporting protocol |
| **Nicer than Humans** | 2024 (ICWSM'25) | `2406.13605` | 100-round IPD vs random adversaries, defection-rate sweep | Forgiveness/hostility-sweep eval |
| **Lorè & Heydari** framing vs structure | 2023 / SciRep'24 | `2309.05898` | One-shot PD/Stag Hunt/Snowdrift with framings | Motivates prose-representation sweeps |
| **Vallinder & Hughes** cultural evolution | 2024 (AAMAS'25) | `2412.10270` | Generational Donor Game with reputation; Claude evolves cooperation + punishment | Donor-game population eval (Tier-1) |
| **Willis, Du, Leibo** | 2025 (AAMAS) | `2501.16173` · [github](https://github.com/willis-richard/evollm) | LLM-written IPD strategies, replicator dynamics | Evolutionary-stability eval; "have the model write its strategy" |
| **Collective Behaviour of Hundreds of LLM Agents** | 2026 | `2602.16662` | Cultural evolution at population scale; newer models worse | Population-level invasion resistance |
| **Pal … Nowak** evolutionary robustness | 2026 (PNAS Nexus) | `2601.09849` | Memory-one strategy-space analysis | Apply to trained checkpoints |
| **Evolutionary Dynamics in Next-Gen LLM Agents** | 2026 | `2605.29874` · [zenodo](https://doi.org/10.5281/zenodo.20248615) | IPD EGT, 3 prompt styles × 4 populations | Prompt style shifts cooperation more than model generation — caution for trained-vs-prompted comparisons |
| **Collective cooperation without individual fidelity** | 2026 | `2606.30454` | 9 open-weight LLMs replayed in networked-PD human experiment | Human-calibrated individual-level yardstick |
| **NetworkGames** | 2025/26 | `2511.21783` | IPD on small-world/scale-free networks with persona agents | Topology + persona placement |
| **The AI in the Mirror** | 2025 | `2508.18467` | IPGG with "another AI" vs "yourself" framing | Identity-framing confound in self-play |
| **FAIRGAME** / payoff scaling across languages | 2025/26 | `2512.07462` · `2601.19082` | Strategy-recognition classifiers (TFT/WSLS…) over scaled PD/PGG | Post-hoc diagnostic of learned policy class |
| **RepuNet** reputation vs commons | 2025 | `2505.05029` | Dynamic reputation sustains CPR cooperation | Institutional baseline |
| **Social Catalysts, Not Moral Agents** | 2026 | `2602.02598` | Altruistic anchoring agents in PGG raise cooperation via strategic adaptation, not internalisation | Reasoning-chain coding scheme to borrow |
| **Social Learning & Norm Formation in CPR** (Rahwan) | 2025 | `2510.14401` | Ostrom-style norms/punishment, no reward signal | Harder PGG variant |
| **Emergent Social Conventions** | Sci Adv 2025 | [doi](https://www.science.org/doi/10.1126/sciadv.adu9368) | Naming game; committed minorities flip conventions | Can a defecting minority overturn distilled norms? |
| **Cooperative Resilience: Humans vs LLMs** | 2025 | `2512.11689` · [github](https://github.com/mavivi95/resilience_humans_vs_LLM) | Melting Pot commons with persistent unsustainable bot + shocks | Resilience-under-disruption metric |
| Small 2026 IPD notes | 2026 | `2602.06081` (cheap talk stabilises 7–9B strategic thinking) · `2603.19167` (counterfactual relabelled PD/RPS) · `2604.12250` · `2606.21001` | — | Cheap robustness probes |

---

## 2. Negotiation & bargaining

| Resource | Year | Links | Setting | Agents / MT / Comm | Train | Bench | Relevance |
|---|---|---|---|---|---|---|---|
| **NegotiationArena** | 2024 (ICML) | `2402.05863` · [github](https://github.com/vinid/NegotiationArena) | Ultimatum, resource split, buyer–seller | 2 / Y / Y | P | Y | Anchoring/exploit incentive vs fairness principle |
| **LLM-Deliberation** | 2023 (ICLR'24) | `2309.17234` · [github](https://github.com/S-Abdelnabi/LLM-Deliberation) · repro `2602.18230` | 6-party 5-issue scorable negotiation, greedy/saboteur roles | 6 / Y / Y | P (verifiable score, expensive) | Y | Sacrifice group agreement for own score |
| **CaSiNo** | 2021 data | `2103.15721` · [github](https://github.com/kushalchawla/CaSiNo) | Integrative campsite bargaining, human baseline | 2 / Y / Y | **Y** | Y | Honesty about preferences vs misrepresentation |
| **Deal-or-No-Deal** | 2017 (reused) | via SPIRAL `SimpleNegotiation`, IB-RL | Item split with private values | 2 / Y / Y | **Y** | P (saturated) | Pure self-play RL degenerates language — control |
| **GPT-Bargaining** | 2023 | `2305.10142` · [github](https://github.com/FranxYao/GPT-Bargaining) | Self-play + AI-critic in-context improvement | 2 / Y / Y | P | P | Critic-loop precedent (objective = better price) |
| **AgreeMate** | 2024 | `2412.18690` | LoRA Llama-3 haggling | 2 / Y / Y | P | P | Capability-without-values baseline |
| **RLVR negotiation trio** | 2026 | `2604.09855` (30B buyer) · `2604.16472` (bilateral trade, Qwen3-8B) · `2607.05863` (multi-buyer) | Surplus-reward GRPO | 2 / 1+N / Y / Y | P (no code) | P | Documents naive→aggressive-anchoring→deadlock drift under payoff RL |
| **GameTalk** | 2026 | `2601.16276` | Conversation-level reward; GRPO/DPO/STaR on Llama-3-3B; RPS, Bertrand duopoly, bargaining | 2 / Y / Y | P (code pending) | N | Small-model full-conversation reward recipe |
| **MERIT / TERMS-Bench / PieArena** | 2026 | `2602.10467` · `2605.13909` · `2602.05302` | Negotiation eval: structured feedback; surplus/consistency/hallucinated terms; realistic multi-issue ranking | 2 / Y / Y | N | Y | TERMS-Bench misstated-terms = deception metric |
| **Cattle Trade** | 2026 | `2605.14537` | 50–60-turn auction/bluff/bargain game | 4–5 / Y / limited | P | Y | Bluffing rewarded by design |
| **AI Negotiation Competition** | 2025 | `2503.06416` | 180K+ negotiations; which tactics win | 2 / Y / Y | N | P | Map of manipulative vs cooperative tactics |
| **Negotiating with LLMs: prompt hacks** | 2023 | `2312.03720` | Humans manipulate LLM seller | 2 / Y / Y | N | N | Robustness to exploiters |
| **IB-RL (Isolated Bilateral RL)** | 2026 | `2608.06735` | Joint training with isolated optimisation paths; DoND, TeleSales | 2 / Y / Y | P | P | Fixed-opponent training overfits; unseen-counterpart eval |

---

## 3. Social deduction / deception / hidden role (use as probes)

| Resource | Year | Links | Setting | Agents / MT / Comm | Train | Bench | Relevance |
|---|---|---|---|---|---|---|---|
| **Mini-Mafia** | 2025 | `2509.23023` · [github](https://github.com/bastoscostadavi/llm-mafia-game) | 4-player single-day Mafia; closed-form deceive/detect/disclose scores | 4 / Y / Y | Y (tiny) | Y | Cheapest deception probe at 8B |
| **Among Us sandbox** | 2025 | `2504.04072` · [github](https://github.com/7vik/AmongUs) | Open-weight agents, Deception ELO, SAE lie-probes | 5–10 / Y / Y | Y | Y | RL-trained models deceive better than detect — baseline to beat |
| **ONUW (Learning to Discuss Strategically)** | 2024 (NeurIPS) | `2405.19946` · [github](https://github.com/KylJin/Werewolf) | One-Night Werewolf, single discussion round, tactic labels | 5 / Y / Y | Y | Y | Cheapest social-deduction env |
| **Werewolf Arena** (Google) | 2024 | `2407.13943` · [github](https://github.com/google/werewolf_arena) | Bidding turn-taking, 8 agents | 8 / Y / Y | P | Y | — |
| **Werewolf RL / LSPO** | 2023 (ICML'24) / 2025 | `2310.18940` · `2502.04686` | RL over LLM candidates; latent-strategy CFR + DPO | 7 / Y / Y | P | P | Makes LLMs better deceivers — negative image |
| **AvalonBench / Strategist** | 2023 / 2024 | `2310.05036` · `2408.10635` · [github](https://github.com/jonathanmli/Avalon-LLM) | Resistance-Avalon + bots + MCTS strategies | 5–6 / Y / Y | Y | Y | Evil must deceive; good must coordinate |
| **Among Them / Hoodwinked / Hidden in Plain Text** | 2025 / 2023 / 2026 | `2502.20426` · `2308.01404` · `2601.13709` | Mafia/Among-Us variants; LLM deception harder to detect than human | 4–8 / Y / Y | P | Y | — |
| **LieCraft** | 2026 (AAAI) | `2603.06874` | Hidden-role in 10 realistic scenarios; agents choose ethical alignment; all 12 models lie | multi / Y / Y | P | Y | Propensity-to-defect vs deception skill under ethics framing |
| **Welfare Diplomacy** | 2023 | `2310.08901` · [github](https://github.com/mukobi/welfare-diplomacy) | General-sum Diplomacy with welfare points; cooperative but exploitable | 7 / Y / Y | P (expensive) | Y | Only Diplomacy variant built for cooperation |
| **DipLLM** | 2025 (ICML) | `2506.09655` · [github](https://github.com/KaiXIIM/dipllm) | Llama-3-8B no-press Diplomacy beats Cicero w/ 1.5% data | 7 / Y / N | P | Y | 8B Diplomacy feasible; no cheap talk |
| **AI Diplomacy (Every) / diplobench / Democratizing Diplomacy** | 2025 | [every.to](https://every.to/p/diplomacy) · [github](https://github.com/EveryInc/AI_Diplomacy) · [diplobench](https://github.com/sam-paech/diplobench) · `2508.07485` | Full-press frontier play; local 24B harness | 7 / Y / Y | P | P | Qualitative honesty probes |
| **Diplomacy tactics / deception detection** | 2025 | `2512.18292` (tactic taxonomy) · `2502.12436` (counterfactual-RL lie detector) | — | — | N | P | Teacher rubric; verifiable honesty reward component |
| **CICERO** | 2022 | [github](https://github.com/facebookresearch/diplomacy_cicero) | Reference honest-commitment agent | 7 / Y / Y | P (heavy) | P | — |
| **Richelieu** | 2024 (NeurIPS) | `2407.06813` | Self-evolving Diplomacy via memory, no weight updates | 7 / Y / Y | N | P | Non-RL control |
| **Social Gym + SPaRTan** | 2026 | `2608.09128` | 21 social games, Elo tournaments, tournament training | 3–10 / Y / Y | P | Y | Social reasoning > incentive cooperation |

---

## 4. Pure coordination & communication games (controls)

| Resource | Year | Links | Setting | Train | Bench | Relevance |
|---|---|---|---|---|---|---|
| **Sparks of Cooperative Reasoning (Hanabi)** | 2026 (ICML) | `2601.18077` | 17 models 4B–600B; Qwen3-4B SFT +21%, RL +156%; transfers | **Y** (dense move values) | Y | Coordination control; RL at 4B done |
| **LLM-Hanabi** | 2025 | `2510.04980` | ToM/rationale inference | P | Y | — |
| **Collab-Overcooked** | 2025 (EMNLP) | `2502.20073` · [github](https://github.com/YusaeMeow/Collab-Overcooked) | Overcooked with NL comm, 30 tasks, process metrics | P | Y | Pure-cooperation OOD test |
| **LLM-Coordination** | 2023/25 | `2310.03903` · [github](https://github.com/eric-ai-lab/llm_coordination) | Text Hanabi/Overcooked/Collab Capture/Escape + CoordQA | P | Y | — |
| **Codenames** | 2024/25 | `2412.11373` · [github](https://github.com/ilya-aby/llm-codenames) · [github](https://github.com/stepmat/Codenames_GPT) | Referential coordination | P | Y | Ad-hoc teamwork |
| **Lewis/referential games w/ LLMs** | 2025/26 | `2503.04395` · `2607.00233` | Frozen-LLM signalling games | P | P | Truthful channel use when aligned |
| **Communication Enables Cooperation vs Curriculum** | 2025 | `2510.05748` | One-word cheap talk lifts 4p Stag Hunt 0→96.7%; defection-game curricula → "learned pessimism" (−27% in IPGG) | P | P | **Curriculum ordering warning** for PD→PGG |
| **More Capable, Less Cooperative?** | 2026 (ICML) | `2604.07821` | Zero-cost info-sharing: o3 17% of optimum vs o3-mini 50% | P | Y | Capability ≠ cooperation |
| **GRPO Does Not Close the Coordination Gap** | 2026 | `2606.07845` | Dining philosophers, GRPO on Qwen3-14B etc.; plateau, inaction bias | N | P | Negative result for outcome-reward GRPO |
| CAMEL / CoMM / AgentVerse | 2023–24 | `2303.17760` · `2404.17729` · `2308.10848` | Cooperative prompting frameworks | N | N | Low |

---

## 5. Rich simulators & sequential social dilemmas

| Resource | Year | Links | Setting | Text-native | Train | Bench | Relevance |
|---|---|---|---|---|---|---|---|
| **Concordia** (+v2.2–2.4 HF wrapper 2026) | 2023/26 | `2312.03664` · [github](https://github.com/google-deepmind/concordia) | GM-narrated generative-agent scenarios | Y | P (GM cost ≥ policy cost) | Y | Free-text moral reasoning mid-episode |
| **Concordia Contest → NeurIPS'25 D&B** | 2024/25 | `2512.03318` · [openreview](https://openreview.net/forum?id=dfeFy1PSSw) · [CAIF](https://www.cooperativeai.com/contests/concordia-2024) | Negotiation → collective action; persuasion & norm-enforcement gaps | Y | P | **Y** | Canonical zero-shot generalisation test |
| **Sotopia / -π / -RL / OMAR / LHRL-VGR** | 2023–26 | `2310.11667` · `2403.08715` · `2508.03905` · `2602.03109` · `2608.05832` · [sotopia](https://github.com/sotopia-lab/sotopia) · [sotopia-rl](https://github.com/sotopia-lab/sotopia-rl) | 90 social scenarios, LLM-judged 7-dim reward; Sotopia-π = BC + self-reinforcement on filtered self-play; Sotopia-RL = utterance-level RM + GRPO (Qwen2.5-7B); OMAR = one model all roles self-play (Sotopia + Werewolf) | Y | **Y** (7B GRPO recipes) | Y | Sotopia-π ≈ rubric-teacher self-distillation; judge→turn-level credit template |
| **Melting Pot 2.0** | 2022 | `2211.13746` · [github](https://github.com/google-deepmind/meltingpot) | 50+ SSD substrates, 256 background-population scenarios | N (text layer: `2403.11381`) | P (needs macro-actions) | Y (MARL) | Scenario design source; novel-partner protocol |
| **Hypothetical Minds** | ICLR'25 | `2407.07086` · [site](https://locross93.github.io/HM-Website/) | ToM hypothesis scaffold on Melting Pot | Y | N | P | Teacher-prompt structure candidate |
| **JaxMARL** (STORM, Coin Game, Hanabi, OvercookedV2) | 2023/25 | `2311.10090` · `2503.17821` · [github](https://github.com/FLAIROx/JaxMARL) | JAX MARL suite | N | N for LLM | Y (MARL) | Payoff logic re-implementable |
| **MA-Craftax / Craftax-Coop** | 2025 | `2511.04904` · [github](https://github.com/BaselOmari/MA-Craftax) | Role-specialised survival with trading | N | N | Y (MARL) | — |
| **InvestESG** (+ opponent shaping) | 2024/26 | `2411.09856` · `2602.11829` | Climate-investment social dilemma (non-LLM) | N | Y (MARL) | Y | Non-LLM baseline |
| **MARBLE / MultiAgentBench** | 2025 (ACL) | `2503.01935` · [github](https://github.com/ulab-uiuc/MARBLE) | Research/Minecraft/DB/coding/Werewolf/bargaining, milestone KPIs | Y | N | Y | Process-level metrics template |
| **BattleAgentBench** | 2024 | `2408.15971` | 7-stage coop→compete grid battle | Y | P | Y | — |
| MineLand / VillagerBench / CoELA / MindAgent | 2023–24 | `2403.19267` · `2406.05720` · `2307.02485` · `2309.09971` | Minecraft / embodied cooperation | partial | N | P | Too heavy; cooperative only |
| Overcooked-AI / Hanabi LE (archived) / PettingZoo / OpenSpiel / Neural MMO / SMAC / GRF | — | [overcooked](https://github.com/HumanCompatibleAI/overcooked_ai) · [hanabi](https://github.com/google-deepmind/hanabi-learning-environment) · [pettingzoo](https://github.com/Farama-Foundation/PettingZoo) · [open_spiel](https://github.com/google-deepmind/open_spiel) · [nmmo](https://github.com/NeuralMMO/environment) | Classic MARL | N | N for LLM | Y (MARL) | PettingZoo AEC `agent_iter()` = verl per-turn analogue; OpenSpiel = equilibrium oracle (already rejected as env) |

---

## 6. Multi-turn RL algorithms & credit assignment (single-policy)

| Method | Year | Links | Idea | verl? | Relevance to repeated games |
|---|---|---|---|---|---|
| **GiGPO / verl-agent** | NeurIPS'25 | `2505.10978` · [github](https://github.com/langfengQ/verl-agent) | Episode-level + anchor-state step-level group advantages, critic-free | **Y** | Identical histories recur across rollouts → free turn-level credit |
| **RAGEN / StarPO(-S)** | 2025 | `2504.20073` · [github](https://github.com/RAGEN-AI/RAGEN) | Trajectory-level RL on verl; "Echo Trap" diagnostics; Gym env interface | **Y** | Stability guide; env-interface pattern |
| **MT-GRPO turn-level reward design** | 2025 | `2505.11821` | Mix per-turn (verifiable/judge) + outcome reward in advantage | easy | Per-round payoff + end-of-game outcome |
| **Turn-PPO** | Dec 2025 | `2512.17008` | Turn-level MDP with critic; GRPO group advantages degrade with horizon | — | Watch for plateau in >5-round games |
| **ArCHer** | ICML'24 | `2402.19446` · [github](https://github.com/yifeizhou02/archer) | Utterance-level off-policy critic + token policy | N | Canonical turn-credit baseline |
| **SWEET-RL + ColBench** | 2025 | `2503.15478` · [github](https://github.com/facebookresearch/sweet_rl) | Step critic with training-time privileged info; 8B collaborative coding w/ simulated human | wrap | Privileged-critic trick ↔ trainer sees both payoffs |
| **ARPO / AEPO** | ICLR'26 | `2507.19849` · [github](https://github.com/RUC-NLPIR/ARPO) · `2510.14545` | Entropy-adaptive branching at uncertain steps | Y | Branch at cooperate/defect fork |
| **LOOP** | 2025 | `2502.01600` · [github](https://github.com/apple/ml-loop) | RLOO + PPO clip, no critic, long horizon | in verl | Stay critic-free at 8B |
| **AgentGym-RL / ScalingInter-RL** | 2025 | `2509.08755` · [github](https://github.com/WooooDyy/AgentGym-RL) | Progressive horizon curriculum | replicable | 1-round → N-round curriculum |
| **ETO / DMPO** | 2024 | `2403.02502` · `2406.14868` | Offline trajectory-pair DPO; multi-turn DPO fix | TRL | Offline arm |
| **Practitioner's Guide to Multi-turn Agentic RL** | Oct 2025 | `2510.01132` · [github](https://github.com/pearls-lab/meow-tea-taro) | Dense vs sparse reward × algorithm ablations | — | Best single "what works" reference |
| **ARLArena / SAMPO** | ICML'26 | `2602.21534` | Four stability dimensions of agentic policy gradient | — | Stability diagnostics |
| WebRL / Agent Q / DigiRL / WebAgent-R1 / ReSpAct / Kimi-Researcher | 2024–25 | `2411.02337` · `2408.07199` · `2406.11896` · `2505.16421` · `2411.00927` · [blog](https://moonshotai.github.io/Kimi-Researcher/) | Web/device agents | — | Lineage only; ReSpAct's speak/act split |
| Single-agent envs: LMRL-Gym (`2311.18232`, Car-Dealer, Twenty Questions), ALFWorld `2010.03768`, TALES, WebArena `2307.13854`, AgentBench `2308.03688`, MINT `2309.10691`, τ/τ²-bench `2406.12045` (pass^k), BALROG `2411.13543`, SmartPlay `2310.01557`, lmgame-Bench `2505.15146`, GameArena `2412.06394` | | | | | Algorithm validation; τ² pass^k as reliability metric |

**Eval methodology:** LLMs Get Lost in Multi-Turn `2505.06120` (report per-round consistency) · MUSIC multi-turn RM `2512.24693` (consistency with earlier commitments → promise-keeping RM) · AgentRewardBench `2504.08942` (validate LLM-judge rewards) · multi-judge dialogue evaluator `2508.00454` · MT-Eval (EMNLP'24).

---

## 7. Multi-agent RL frameworks & algorithms for LLMs

| Resource | Year | Links | What | Backend | Multi-agent co-training | Relevance |
|---|---|---|---|---|---|---|
| **verl agent loop** | 2024–26 | [github](https://github.com/verl-project/verl) · [docs](https://verl.readthedocs.io/en/latest/advance/agent_loop.html) · [issue #2618](https://github.com/verl-project/verl/issues/2618) | `AgentLoopBase`; one trajectory per prompt; multi-trajectory "under discussion" | verl | Shared-policy self-play only (opponent from same vLLM) | Current stack |
| **PettingLLMs / AT-GRPO / MetaAgent-X** | Oct 2025–Apr 2026 | [github](https://github.com/pettingllms-ai/PettingLLMs) · `2510.11062` · `2605.14212` | Shared / per-agent LoRA / independent policies; process+agent+team rewards; agent- and turn-wise credit | **verl** | **Y** | Most mature verl-native multi-agent trainer; needs dilemma envs |
| **Dr. MAS** | Feb 2026 | `2602.08847` (verl recipe via README) | Agent-wise advantage normalisation; global GRPO norm blows up heterogeneous agents | **verl** | Y | Essential once moral score vs payoff have different scales |
| **Agent Lightning v1.0** | 2026 | `2608.17528` · [github](https://github.com/microsoft/agent-lightning) | LLM-endpoint proxy captures traces from any agent code → verl | verl | via forks | Train on external runner (GovSim, CoopEval) without rewriting |
| **MARTI / MARTI-v2 (MARS²)** | ICLR'26 | [github](https://github.com/TsinghuaC3I/MARTI) · `2602.07848` · [openreview](https://openreview.net/forum?id=E7jZqo0A50) | Graph workflows, heterogeneous models, PPO/GRPO/REINFORCE++/GSPO, tree search; Qwen3-8B/14B | OpenRLHF | **Y** | Reasoning/code focus |
| **MARFT** | ICLR'26 | `2504.16129` · [github](https://github.com/SII-MARFT/MARFT) | Flexible Markov Game; CTDE or per-agent LoRA critics | AReaL | Y | Centralised moral-judge critic |
| **MAGRPO / CoMLRL** | AAAI'26 | `2508.04652` · [github](https://github.com/OpenMLRL/CoMLRL) | Dec-POMDP, centralised group-relative advantage, decentralised execution | own | Y | Cleanest GRPO-native MARL for joint-reward dilemmas |
| **CCPO** | 2026 | `2603.21563` · [github](https://github.com/bhai114/ccpo) | Remove-one counterfactual per-agent credit in GRPO | — | Y | Share welfare reward back to individuals in PGG |
| **MAPoRL** | ACL'25 | `2502.18439` | Co-trained debaters; collaboration must be co-trained | — | Y | — |
| **SPIRAL** | 2025 | `2506.24119` · [github](https://github.com/spiral-rl/spiral) · [UnstableBaselines](https://github.com/LeonGuertler/UnstableBaselines) | Self-play on TextArena zero-sum; Role-conditioned Advantage Estimation; Qwen3-4B/8B | Oat | shared-policy | RAE = per-role baseline fix; competitive priors hypothesis |
| **MARSHAL** | ICLR'26 | `2510.15414` · [github](https://github.com/thu-nics/MARSHAL) | Self-play on coop + competitive games (OpenSpiel logic); turn-level advantage, agent-specific norm | own | shared-policy | Includes cooperative games |
| **OMAR** | 2026 | `2602.03109` | One model all roles; Sotopia + Werewolf; reward hacking observed | — | shared-policy | Fits verl one-trajectory constraint |
| **AdAlignLLM** | 2025 | `2511.19405` · [github](https://github.com/dereckpiche/AdAlignLLM) | Per-agent LoRA hot-swap on shared vLLM base | own | Y (heterogeneous) | How to co-train two 8B agents on one node |
| **SEPO** | 2026 | `2605.30854` | GRPO + exploitability / collusion / externality penalties; Gemma-4-E4B, Qwen3.5-4B; IPD, auctions, negotiation, Kuhn | — | — | Constant penalties cancel under group normalisation — same for fixed moral rewards |
| **GTAlign** | 2025 | `2510.08872` · [github](https://github.com/ulab-uiuc/GTAlign) | Social-welfare reward; model builds payoff matrix at inference | **verl** | — | Near-identical stack |
| **ToMPO** | 2025 | `2509.21134` | ToM-conditioned rollouts, balanced multi-reward | — | — | Reciprocity booster |
| **When Does MARL Improve LLM Workflows?** | 2026 | `2605.24202` | Shared vs isolated policies: isolated peak higher but collapse | — | — | Shared-vs-separate-LoRA decision |
| **FlexMARL** | 2026 | `2602.09578` | Async disaggregated MARL rollouts, 7.3× | — | — | Systems reference |
| Other frameworks: SkyRL `2511.16108` · AReaL `2505.24298` · ROLL `2506.06122` · slime [github](https://github.com/THUDM/slime) · NeMo-RL/Gym [github](https://github.com/NVIDIA-NeMo/Gym) · OpenRLHF `2405.11143` · TRL GRPO (single-turn only, [issue 2712](https://github.com/huggingface/trl/issues/2712)) | | | Single-agent multi-turn | | | |
| ICLR'26 items not opened (bot-challenge): Sandbox-RL `0pFcKF2li1`, Adaptive Punishment for Cooperation `DvxnTiEM0T`, COALA-PG learning-aware PG `GkWA6NjePN`, Interacting to Learn Reasoning `GGYXjJJpWc` — verify at `openreview.net/forum?id=<id>` | | | | | | |

---

## 8. Cooperation-training methods & moral alignment (LLM and classic MARL)

### 8A. LLM-scale
| Resource | Year | Links | Method / finding | Code | Use |
|---|---|---|---|---|---|
| **Tennant et al.** Moral Alignment | ICLR'25 (+WS) | `2410.01639` · [github](https://github.com/liza-tennant/LLM_morality) · [WS](https://iclr.cc/virtual/2025/10000485) | PPO on Gemma-2-2B with deontological/utilitarian intrinsic rewards; unlearning; cross-game transfer | Y | Baseline (reward twin) |
| **Advantage Alignment for LLMs** | 2025 | `2511.19405` · [github](https://github.com/dereckpiche/AdAlignLLM) | Naive GRPO → exploitation; AdAlign cooperates with cooperators, resists greedy; learns TFT | Y | **Primary non-moral baseline** |
| **ShapeLLM** opponent shaping | ICLR'26 | `2510.08255` | LLMs steer learning co-players to exploit or cooperate | ? | Robustness-to-shapers eval |
| **You Only Align Once** | 2026 | `2605.27586` | Teacher → Qwen3-14B seed agent; Red-Black 24.8→62.2%, transfers to Sugarscape | N | Closest cousin; transfer protocol |
| **Homo-moralis SFT** | 2025/26 | `2507.20796` | SFT on Kantian-universalisation optimal strategies | ? | Universalization via SFT |
| **MoralReason** | AAAI'26 | `2511.12271` | GRPO w/ framework-specific moral traces; OOD transfer | ? | Rationale-level evidence |
| **MAC-SPGG** | NeurIPS'25 | `2508.02076` | Sequential PGG reward where contribution is unique SPNE | ? | Mechanism contrast |
| **Adaptive Information Modulation** (RL governor) | 2024/25 | `2409.10372` | RL governor picks what history to reveal | N | Env-side alignment baseline |
| **In-context co-player inference** | 2026 | `2602.16301` | Train vs diverse co-players → ICL cooperation | N | Co-player diversity |
| **Multi-Agent Risks from Advanced AI** / Open Problems in Cooperative AI | 2025 / 2020 | `2502.14143` · `2012.08630` | Agenda & failure taxonomy (collusion) | — | Framing; collusion eval |

### 8B. Self-distillation / privileged feedback
| Resource | Year | Links | Idea | Code |
|---|---|---|---|---|
| **SDPO** | 2026 | `2601.20802` · [github](https://github.com/lasgroup/sdpo) | Feedback-conditioned self-teacher distilled into unconditioned policy | Y |
| **On-Policy Distillation** | Oct 2025 | [blog](https://thinkingmachines.ai/blog/on-policy-distillation/) · survey `2604.00626` · [awesome list](https://github.com/chrisliu298/awesome-on-policy-distillation) | Reverse-KL per-token teacher scoring | Y |
| **CRPO** | 2026 | `2607.28026` | Exposure bias of privileged self-teacher in multi-turn; entropy-split contrast | N |
| **PBSD** | 2026 | `2606.09348` | Turn-level credit from student/privileged-teacher likelihood ratio | N |
| **ZPPO** | 2026 | `2606.18216` · [page](https://byungkwanlee.github.io/ZPPO-page/) | Teacher in prompts (contrast), not gradients | page |
| **MulFeRL** | 2026 | `2601.22900` | Verbal-feedback regeneration loop in RL | N |
| **SDRL** self-debate / MAPoRL | 2026 / 2025 | `2601.22297` · `2502.18439` | Optimise standalone + others'-argument-conditioned answers | N |
| **Reflexion** | NeurIPS'23 | `2303.11366` · [github](https://github.com/noahshinn/reflexion) | Prompt-only verbal RL | Y |

### 8C. Classic MARL, portable
| Resource | Year | Links | Port to MoralGym |
|---|---|---|---|
| **LOLA** / **M-FOS** / **Shaper** | 2018 / 22 / 24 | `1709.04326` · `2205.01447` · `2312.12568` | Opponent shaping lineage → ShapeLLM / AdAlign |
| **Advantage Alignment Algorithms** | ICLR'25 oral | `2406.14662` · [github](https://github.com/jduquevan/advantage-alignment) | One-line advantage change inside GRPO |
| **Social Influence** (Jaques) | ICML'19 | `1810.08647` | Influence as teacher principle |
| **Inequity Aversion** (Hughes) | NeurIPS'18 | `1803.08884` | Fairness principle with known RL reward form |
| **RUSP** | NeurIPS'20 | `2011.05373` | Randomise principle strength per episode |
| **MOCA formal contracts** | AAMAS'23 | `2208.10469` | Mechanism baseline (cf. CoopEval contracts) |
| **Other-Play / Off-Belief Learning** | ICML'20/21 | `2003.02979` · `2103.04000` | Cross-play eval across seeds/principles/models |
| **FCP / COLE** | NeurIPS'21 / ICML'23 | `2110.08176` · `2302.04831` | Diverse opponent pool (TFT, grim, random, past ckpts) |
| **GRPO-GCC** | 2025 | `2510.08607` | GRPO with global cooperation constraint (spatial PGG) |

### 8D. Competitive → cooperative / reasoning transfer
SPIRAL `2506.24119` · MARSHAL `2510.15414` · Stratagem (transferability coefficient) `2604.17696` · Survive or Collapse `2605.22217` · Pursuit-evasion self-play `2608.21871` · Absolute Zero `2505.03335`. Open question nobody has measured: does zero-sum self-play degrade social-dilemma cooperation? (cheap negative-transfer experiment on existing PD/PGG eval).

---

## 9. Surveys

| Survey | Date | Link | Taxonomy |
|---|---|---|---|
| Game-Theoretic Lens on LLM-based MAS | Jan 2026 | `2601.15047` | players / strategies & equilibrium / payoffs / information; §3.2 RL & self-play |
| From Reasoning to Agentic: Credit Assignment in RL for LLMs (69 methods) | Apr 2026 | `2604.09459` | granularity × methodology; "agent coupling" barrier |
| Five Ws of Multi-Agent Communication (TMLR) | Feb 2026 | `2602.11583` | who/whom/when/what/why |
| Agentic Environment Engineering | Jun 2026 | `2606.12191` | 8 env attributes × 8 domains |
| Agentic Reasoning for LLMs | Jan 2026 | `2601.12538` · [github](https://github.com/weitianxin/Awesome-Agentic-Reasoning) | single → self-evolving → collective |
| Human-Centered MAS: Cognition, Culture, Values, Cooperation | Jun 2026 | `2606.08274` | only 2026 survey coupling value alignment + cooperation |
| Multi-Agent Debate Strategies (141 studies) | Jul 2026 | `2607.26212` | participants / mechanisms / agreement |
| Landscape of Agentic RL | 2025 | `2509.02547` | — |
| Beyond Single-Turn (multi-turn interactions) | 2025 | `2504.04717` · [awesome](https://github.com/yubol-bobo/Awesome-Multi-Turn-LLMs) | — |
| LLM-based MARL directions / Game Theory Meets LLMs | 2024 / 2025 | `2405.11106` · `2502.09053` | — |
| Agentic RL overview | 2026 | `2604.27859` | — |

No 2026 survey specifically catalogs multi-agent RL *for* LLMs in mixed-motive games or their environments.

---

## 10. Competitions & community

- **MindGames Challenge** (NeurIPS 2025; 2026 edition announced) — `2605.29512` · [site](https://www.mindgamesarena.com/) · [starter kit](https://github.com/mind-games-challenge/mindgames-starter-kit) · [MGC2025 data](https://huggingface.co/datasets/mindgameschallenge/MGC2025): Colonel Blotto, **Iterated PD**, Codenames, Secret Mafia; **≤9B open-weight "Efficient" division**; 944 agents, 29.6K games; MG-Ref offline protocol.
- **Concordia Contest** (NeurIPS 2024) → D&B report `2512.03318` · [CAIF post](https://www.cooperativeai.com/post/concordia-contest). No 2025/26 edition found.
- **Melting Pot Contest 2023** — [CAIF](https://www.cooperativeai.com/contests/melting-pot-2023).
- **ICML 2026 New Frontiers in Game-Theoretic Learning WS** (Jul 10) — [site](https://icml.cc/virtual/2026/workshop/54068): "Beyond Task Success: Evaluating Cooperation in LLM MAS", "Learning to Mediate Equilibrium Selection in LLM Games", "Beyond Scalar Rewards: Dense Feedback for LLM Policy Synthesis in SSDs", "Opponent Modeling and VoI in Deep RL for IPD", "When Agents Lie" (best paper). Several have no arXiv version yet.
- **ICLR 2026 Multi-Agent Learning in the Era of Generative AI WS** — [site](https://iclr.cc/virtual/2026/workshop/10000784); also AIMS (mechanism design) and Lifelong Agents workshops.
- **Cooperative AI Foundation** — [site](https://www.cooperativeai.com/): $10M Multi-Agent Safety Fund (deadline 2026-08-09), 2026 fellowships; no new benchmark in window.

---

## 11. Cross-cutting findings

1. **The normative-feedback slot is empty.** Every RL-for-cooperation result at LLM scale (AdAlign, SEPO, GTAlign, MAC-SPGG, RLVR negotiation, Among Us, Werewolf RL) uses payoff, welfare, exploitability or opponent-shaping objectives; every on-policy self-distillation method uses verifiable feedback. Tennant's moral-intrinsic-reward line has no visible 2026 successor.
2. **Naive payoff RL reliably produces exploitation/deception** (AdAlign baseline, RLVR negotiation drift, Among Us, LSPO), and reasoning models free-ride more (SanctSim, More Capable Less Cooperative). This is the motivating baseline for the SDPO arms.
3. **Robustness to co-player variation is the recurring failure** (CoopEval, Concordia, MindGames, IB-RL, Willis et al.). Evaluate with heterogeneous opponent pools, cross-play across seeds/principles, and population/replicator protocols — not only fixed-opponent IPD.
4. **Framing/identity/prompt-style confounds are as large as model differences** (Lorè & Heydari, FAIRGAME, AI-in-the-Mirror, GT-HarmBench ordering, EGT-2026 prompt styles). Internalisation claims need framing, language, self-vs-other, and process-level rationale sweeps.
5. **Cheap-talk + binding action is now standard** (Bilateral Trade, Cheap Talk Empty Promise, GLEE persuasion, Trust-and-Split) and matches the decision+prose split; the promise-stage protocol gives a verifiable promise-breaking rate.
6. **Self-play baselining pitfall**: shared-weight roles with different reward scales need per-role/per-agent baselines (SPIRAL RAE, Dr. MAS, AdAlign CRN); constant penalties cancel under group normalisation (SEPO).
7. **Curriculum ordering matters**: defection-equilibrium curricula induce "learned pessimism" (`2510.05748`); zero-sum self-play changes reasoning broadly (SPIRAL/MARSHAL) — negative transfer to cooperation is unmeasured.
8. **No verl-native multi-agent mixed-motive environment exists**; frameworks (PettingLLMs, MARTI, MARFT, Dr. MAS) ship math/code/search envs, while SSD suites (Melting Pot, SocialJax) have no LLM interface. MoralGymVerl + a TextArena wrapper is the practical bridge.
9. **Social deduction games bind principles asymmetrically by role** → use as post-training deception probes, not training environments.
10. **Unverified / omitted**: MAGNET, AgentSims (not confirmed); code for M3-Bench, OMAR, ToMPO, FlexMARL, SEPO (anonymous), GameTalk (pending), MARSHAL (repo found late), ShapeLLM not confirmed; four ICLR'26 OpenReview IDs listed in §7 not opened.

---

## 12. Suggested adoption map for MoralGym / MoralGymVerl

| Horizon | Action | Source |
|---|---|---|
| Now (eval) | Add promise-stage variant to PD/PGG; report promise-breaking rate | `2604.04782` |
| Now (eval) | Run CoopEval mechanism matrix + replicator dynamics on SDPO/GRPO checkpoints | `2604.15267` |
| Now (eval) | Hostility sweep (Nicer than Humans) + heterogeneous opponent pool + cross-play across seeds/principles | `2406.13605`, Other-Play |
| Now (eval) | Strategy-class classifier (TFT/WSLS/…) and memory-one analysis of checkpoints | `2512.07462`, `2601.09849` |
| Now (algo) | Per-role baseline (RAE) / agent-wise normalisation (Dr. MAS) for self-play runs | `2506.24119`, `2602.08847` |
| Next | Comparison arm: Advantage Alignment (same env, same model) vs principle self-distillation on exploitability–welfare frontier | `2511.19405`, `2406.14662` |
| Next | Wrap TextArena Iterated PD (chat) / PGG / Ultimatum as verl agent-loop envs | `2504.11442` |
| Next | Submit 8–9B checkpoint to MindGames IPD "Efficient" division | `2605.29512` |
| Next | GiGPO anchor-state grouping for per-round credit; PBSD if teacher-phrasing collapse appears | `2505.10978`, `2606.09348`, `2607.28026` |
| Later | Transfer battery: GovSim (universalization OFF), Concordia scenarios, M3-Bench, MoralSim, SanctSim, Hanabi/Collab-Overcooked controls, Mini-Mafia deception probe | §1, §4, §3 |
| Later | Negative-transfer experiment: SPIRAL-style zero-sum self-play → PD/PGG cooperation | `2506.24119` |
| Later | Two trainable co-players via PettingLLMs or Agent Lightning proxy | `2510.11062`, `2608.17528` |
