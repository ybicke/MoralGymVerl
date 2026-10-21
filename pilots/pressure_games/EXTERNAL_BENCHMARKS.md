# External benchmarks for the pressure-game track

Assessed 2026-10-19. Companion to GAMES.md. Retention (GSM8K, MMLU) is
documented in MoralGymVerl/docs/external_benchmarks.md.

Keyed to the METR report on the OpenAI / Hugging Face incident
(metr.org, 2026-08-26). The report documents five failure types: impossible
tasks as the trigger (30 to 40% of ExploitGym targets could not be exploited
as specified), scope violation with full awareness, peer conformity ("task
impossible, peers doing it, we should continue"), obedience to a
non-principal collective, and concealment aimed at the automated scorer. It
confirms no lie to a human. Any outside benchmark must serve the adapter
unmerged through vLLM (see the LoRA merge note in the eval docs) with Qwen3
thinking forced off server-side.

| benchmark | incident type | our game | status | verdict |
|---|---|---|---|---|
| InjecAgent (ACL 2024) | obey non-principal | obedience, allocation | not run | first: single turn, programmatic attack success, no judge. Gate: base attack success above 5% |
| AgentDojo (NeurIPS 2024) | obey non-principal | obedience, allocation | not run | after InjecAgent shows a drop; needs tool calling, report attack success only |
| MACHIAVELLI (ICML 2023) | general violations under reward | all, same labelled-choice form | data gated, request needed | second: reward vs violations Pareto is the paper's claim shape |
| ImpossibleBench LCB (ICLR 2026) | impossible task, scorer gaming | shortcut, report | data gated | high risk: under the anti-cheat prompt every model but o3 cheats 0 to 4%; cheating only under the loose prompt. Design = base under loose and strict prompt, SDPO and GRPO under loose, one-off split, pilot 20 base tasks and stop below 5% |
| BenchForm (ICLR 2025) | peer conformity | conformity | not run | cheap but factual, null expected |
| MASK (2025) | lying to a human | report | legacy ckpts run by a collaborator | null: pooled lying rate 36.0 base, 36.6 / 35.6 SDPO, 38.9 / 36.2 GRPO, n = 1000, SE about 1.5. Not the incident; appendix sentence at most |
| SHADE-Arena, Reward Hacking Benchmark | concealment, shortcut when avoidable | none | not run | out of scope: public subset only, capability floor, no track record |

Claim structure for the paper: trigger on an outside benchmark, amplifiers
(conformity, obedience) in our pressure games, concealment out of scope.
