# Multi-round eval: qwen3_8b / pgg / multi_round

Qwen3-8B, game `public_goods`, representation `decision_full`, 5 live rounds per episode, cold open (no fabricated history; rules in round 1 only, one outcome message per later round, conversation accumulates), opponents `full_contributor`, `free_rider`, `noisy_conditional`, 20 episodes per opponent per policy, T = 0.7, no moral text in any prompt. Jobs 3714302; parse-failure at worst 0.0%.

Policies are the single-step-trained checkpoints; this document measures them IN PLAY. The single-round tables are the same policies' fabricated-history rows (Table M2 pairs them).


### Table M1 — cooperation per round, in play

*Live episodes: cold open (round 1 has no history), one conversation per episode, moves simultaneous.*

P(C) per round over that opponent's episodes (20 per cell, binomial s.e. ≤11 points/round). pooled = all decisions. Round 1 is the untrained opening state; later rounds condition on the episode's own history.

**vs 'full_contributor'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 0 | 0 | 0 | 0 | **0** |
| GRPO deon s180 | 10 | 5 | 5 | 5 | 5 | **6** |
| SDPO deon-repair-gen s110 | 20 | 90 | 90 | 90 | 90 | **76** |
| SDPO deon-repair-gen s180 | 60 | 85 | 90 | 85 | 90 | **82** |

**vs 'free_rider'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 15 | 5 | 15 | 5 | **8** |
| GRPO deon s180 | 30 | 0 | 0 | 0 | 0 | **6** |
| SDPO deon-repair-gen s110 | 35 | 25 | 15 | 15 | 10 | **20** |
| SDPO deon-repair-gen s180 | 70 | 20 | 25 | 10 | 15 | **28** |

**vs 'noisy_conditional'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 5 | 5 | 5 | 5 | **4** |
| GRPO deon s180 | 5 | 10 | 5 | 15 | 5 | **8** |
| SDPO deon-repair-gen s110 | 25 | 70 | 70 | 70 | 65 | **60** |
| SDPO deon-repair-gen s180 | 75 | 75 | 85 | 80 | 90 | **81** |


### Figure 1 — cooperation per round

![P(C) per round per opponent](figures/multi_round_qwen3_8b_public_goods.png)

One panel per opponent, one line per policy (base dashed grey). An alternating line is the repair-retaliate 2-cycle; a rising flat-topped line is absorption into cooperation.


### Figure 2 — joint-outcome composition per round

![outcome shares vs noisy_conditional](figures/multi_round_stacked_qwen3_8b_public_goods.png)

Vs `noisy_conditional` (the reactive opponent), each bar splits that round's episodes by joint outcome. Absorption = one band taking over; the repair-retaliate cycle = the exploiting and suckered bands swapping between rounds.


### Table M2 — the state table measured in play

*Rounds \geq2 pooled over opponents and rounds; states are the episode's own realized (last move, observation) pairs, so their frequencies are policy-dependent and UNBALANCED.*

P(C | state) in live play, under each policy its single-round fabricated-history row where evaluated (--reference): matching numbers mean the installed table survives the conversation surface. * marks live cells with n < 10 — read those as anecdotes.

| Policy | C<sub><small>A</small></sub>,k=0 | C<sub><small>A</small></sub>,k=1 | C<sub><small>A</small></sub>,k=2 | C<sub><small>A</small></sub>,k=3 | D<sub><small>A</small></sub>,k=0 | D<sub><small>A</small></sub>,k=1 | D<sub><small>A</small></sub>,k=2 | D<sub><small>A</small></sub>,k=3 |
|---|---|---|---|---|---|---|---|---|
| base | 30 | —\* | —\* | —\* | 11 | 0 | 0 | 0 |
| GRPO deon s180 | 0\* | 0\* | 0\* | 78\* | 1 | 0 | 0 | 3 |
|   · single-round (fabricated) | 0 | 0 | 0 | 18 | 6 | 2 | 0 | 0 |
| SDPO deon-repair-gen s110 | 35 | 50\* | 89 | 100 | 14 | 9 | 12\* | 72 |
|   · single-round (fabricated) | 8 | 10 | 18 | 78 | 4 | 18 | 10 | 38 |
| SDPO deon-repair-gen s180 | 44 | 100\* | 85 | 99 | 5 | 33\* | 0\* | 59 |
|   · single-round (fabricated) | 38 | 72 | 74 | 100 | 4 | 18 | 22 | 74 |

*Live n per cell varies by policy (a policy that never reaches a state contributes no estimate there).*

### Table M3 — episode outcomes

*Per policy and opponent, over that cell's episodes.*

open = P(C) in round 1, the state training never showed. final = P(C) in round 5. mutual coop = share of episodes whose LAST round is jointly cooperative (agent C and the opponent/majority of co-players C) — the absorption readout.

| Policy | opponent | open | final | mutual coop | episodes |
|---|---|---|---|---|---|
| base | 'full_contributor' | 0 | 0 | 0 | 20 |
|  | 'free_rider' | 0 | 5 | 0 | 20 |
|  | 'noisy_conditional' | 0 | 5 | 0 | 20 |
| GRPO deon s180 | 'full_contributor' | 10 | 5 | 5 | 20 |
|  | 'free_rider' | 30 | 0 | 0 | 20 |
|  | 'noisy_conditional' | 5 | 5 | 5 | 20 |
| SDPO deon-repair-gen s110 | 'full_contributor' | 20 | 90 | 90 | 20 |
|  | 'free_rider' | 35 | 10 | 0 | 20 |
|  | 'noisy_conditional' | 25 | 65 | 50 | 20 |
| SDPO deon-repair-gen s180 | 'full_contributor' | 60 | 90 | 90 | 20 |
|  | 'free_rider' | 70 | 15 | 0 | 20 |
|  | 'noisy_conditional' | 75 | 90 | 75 | 20 |


### Table M4 — reasoning traces in play

*All rounds and opponents pooled.*

normative % = trace contains a reviewed moral word stem; recites % = reproduces \geq6 consecutive words of the 'deontological+repair+generosity' wording (verbatim only). The principle text is in none of these prompts.

| Policy | n | normative % | recites % |
|---|---|---|---|
| base | 300 | 0 | 0 |
| GRPO deon s180 | 300 | 2 | 0 |
| SDPO deon-repair-gen s110 | 300 | 92 | 23 |
| SDPO deon-repair-gen s180 | 300 | 100 | 52 |

