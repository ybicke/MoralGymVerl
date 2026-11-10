# Multi-round eval: qwen3_4b / pgg / multi_round_hanabi_rl

Qwen3-4B-Instruct-2507, game `public_goods`, representation `decision_full`, 5 live rounds per episode, cold open (no fabricated history; rules in round 1 only, one outcome message per later round, conversation accumulates), opponents `full_contributor`, `free_rider`, `noisy_conditional`, 20 episodes per opponent per policy, T = 0.7, no moral text in any prompt. Jobs 3338250; parse-failure at worst 1.0%.

Policies are the single-step-trained checkpoints; this document measures them IN PLAY. The single-round tables are the same policies' fabricated-history rows (Table M2 pairs them).


### Table M1 — cooperation per round, in play

*Live episodes: cold open (round 1 has no history), one conversation per episode, moves simultaneous.*

P(C) per round over that opponent's episodes (20 per cell, binomial s.e. ≤11 points/round). pooled = all decisions. Round 1 is the untrained opening state; later rounds condition on the episode's own history.

**vs 'full_contributor'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 0 | 0 | 0 | 0 | **0** |
| GRPO o3ratings final | 0 | 0 | 0 | 0 | 0 | **0** |

**vs 'free_rider'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 0 | 0 | 0 | 0 | **0** |
| GRPO o3ratings final | 0 | 0 | 0 | 0 | 0 | **0** |

**vs 'noisy_conditional'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 0 | 0 | 0 | 0 | **0** |
| GRPO o3ratings final | 0 | 0 | 0 | 0 | 0 | **0** |


### Figure 1 — cooperation per round

![P(C) per round per opponent](figures/multi_round_qwen3_4b_public_goods.png)

One panel per opponent, one line per policy (base dashed grey). An alternating line is the repair-retaliate 2-cycle; a rising flat-topped line is absorption into cooperation.


### Figure 2 — joint-outcome composition per round

![outcome shares vs noisy_conditional](figures/multi_round_stacked_qwen3_4b_public_goods.png)

Vs `noisy_conditional` (the reactive opponent), each bar splits that round's episodes by joint outcome. Absorption = one band taking over; the repair-retaliate cycle = the exploiting and suckered bands swapping between rounds.


### Table M2 — the state table measured in play

*Rounds \geq2 pooled over opponents and rounds; states are the episode's own realized (last move, observation) pairs, so their frequencies are policy-dependent and UNBALANCED.*

P(C | state) in live play, under each policy its single-round fabricated-history row where evaluated (--reference): matching numbers mean the installed table survives the conversation surface. * marks live cells with n < 10 — read those as anecdotes.

| Policy | D<sub><small>A</small></sub>,k=0 | D<sub><small>A</small></sub>,k=1 | D<sub><small>A</small></sub>,k=2 | D<sub><small>A</small></sub>,k=3 |
|---|---|---|---|---|
| base | 0 | 0 | 0 | 0 |
| GRPO o3ratings final | 0 | 0 | 0 | 0 |

*Live n per cell varies by policy (a policy that never reaches a state contributes no estimate there).*

### Table M3 — episode outcomes

*Per policy and opponent, over that cell's episodes.*

open = P(C) in round 1, the state training never showed. final = P(C) in round 5. mutual coop = share of episodes whose LAST round is jointly cooperative (agent C and the opponent/majority of co-players C) — the absorption readout.

| Policy | opponent | open | final | mutual coop | episodes |
|---|---|---|---|---|---|
| base | 'full_contributor' | 0 | 0 | 0 | 20 |
|  | 'free_rider' | 0 | 0 | 0 | 20 |
|  | 'noisy_conditional' | 0 | 0 | 0 | 20 |
| GRPO o3ratings final | 'full_contributor' | 0 | 0 | 0 | 20 |
|  | 'free_rider' | 0 | 0 | 0 | 20 |
|  | 'noisy_conditional' | 0 | 0 | 0 | 20 |


### Table M4 — reasoning traces in play

*All rounds and opponents pooled.*

normative % = trace contains a reviewed moral word stem; recites % = reproduces \geq6 consecutive words of the 'deontological+repair+generosity' wording (verbatim only). The principle text is in none of these prompts.

| Policy | n | normative % | recites % |
|---|---|---|---|
| base | 300 | 2 | 0 |
| GRPO o3ratings final | 300 | 2 | 0 |

