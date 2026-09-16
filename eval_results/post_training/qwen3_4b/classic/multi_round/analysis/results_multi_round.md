# Multi-round eval: qwen3_4b / classic / multi_round

Qwen3-4B-Instruct-2507, game `prisoners_dilemma`, representation `prose`, 5 live rounds per episode, cold open (no fabricated history; rules in round 1 only, one outcome message per later round, conversation accumulates), opponents `tit_for_tat`, `always_defect`, `always_cooperate`, 20 episodes per opponent per policy, T = 0.7, no moral text in any prompt. Jobs 3338250 / 3357453; parse-failure at worst 3.0%.

Policies are the single-step-trained checkpoints; this document measures them IN PLAY. The single-round tables are the same policies' fabricated-history rows (Table M2 pairs them).


### Table M1 — cooperation per round, in play

*Live episodes: cold open (round 1 has no history), one conversation per episode, moves simultaneous.*

P(C) per round over that opponent's episodes (20 per cell, binomial s.e. ≤11 points/round). pooled = all decisions. Round 1 is the untrained opening state; later rounds condition on the episode's own history.

**vs 'tit_for_tat'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 5 | 5 | 5 | 0 | **3** |
| SDPO deon-repair-gen s80 | 40 | 55 | 40 | 60 | 45 | **49** |
| SDPO deon-repair-gen s150 | 95 | 100 | 95 | 100 | 95 | **97** |

**vs 'always_defect'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 5 | 0 | 0 | 0 | **1** |
| SDPO deon-repair-gen s80 | 50 | 0 | 15 | 5 | 15 | **18** |
| SDPO deon-repair-gen s150 | 95 | 5 | 10 | 5 | 10 | **25** |

**vs 'always_cooperate'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 0 | 0 | 0 | 0 | **0** |
| SDPO deon-repair-gen s80 | 40 | 60 | 60 | 60 | 60 | **56** |
| SDPO deon-repair-gen s150 | 90 | 90 | 90 | 90 | 90 | **90** |


### Figure 1 — cooperation per round

![P(C) per round per opponent](figures/multi_round_qwen3_4b_prisoners_dilemma.png)

One panel per opponent, one line per policy (base dashed grey). An alternating line is the repair-retaliate 2-cycle; a rising flat-topped line is absorption into cooperation.


### Figure 2 — joint-outcome composition per round

![outcome shares vs tit_for_tat](figures/multi_round_stacked_qwen3_4b_prisoners_dilemma.png)

Vs `tit_for_tat` (the reactive opponent), each bar splits that round's episodes by joint outcome. Absorption = one band taking over; the repair-retaliate cycle = the exploiting and suckered bands swapping between rounds.


### Table M2 — the state table measured in play

*Rounds \geq2 pooled over opponents and rounds; states are the episode's own realized (last move, observation) pairs, so their frequencies are policy-dependent and UNBALANCED.*

P(C | state) in live play, under each policy its single-round fabricated-history row where evaluated (--reference): matching numbers mean the installed table survives the conversation surface. * marks live cells with n < 10 — read those as anecdotes.

| Policy | C<sub><small>A</small></sub>C<sub><small>O</small></sub> | C<sub><small>A</small></sub>D<sub><small>O</small></sub> | D<sub><small>A</small></sub>C<sub><small>O</small></sub> | D<sub><small>A</small></sub>D<sub><small>O</small></sub> |
|---|---|---|---|---|
| base | —\* | 0\* | 2 | 1 |
| SDPO deon-repair-gen s80 | 99 | 15 | 18 | 7 |
| SDPO deon-repair-gen s150 | 100 | 16 | 20 | 4 |

*Live n per cell varies by policy (a policy that never reaches a state contributes no estimate there).*

### Table M3 — episode outcomes

*Per policy and opponent, over that cell's episodes.*

open = P(C) in round 1, the state training never showed. final = P(C) in round 5. mutual coop = share of episodes whose LAST round is jointly cooperative (agent C and the opponent/majority of co-players C) — the absorption readout.

| Policy | opponent | open | final | mutual coop | episodes |
|---|---|---|---|---|---|
| base | 'tit_for_tat' | 0 | 0 | 0 | 20 |
|  | 'always_defect' | 0 | 0 | 0 | 20 |
|  | 'always_cooperate' | 0 | 0 | 0 | 20 |
| SDPO deon-repair-gen s80 | 'tit_for_tat' | 40 | 45 | 45 | 20 |
|  | 'always_defect' | 50 | 15 | 0 | 20 |
|  | 'always_cooperate' | 40 | 60 | 60 | 20 |
| SDPO deon-repair-gen s150 | 'tit_for_tat' | 95 | 95 | 95 | 20 |
|  | 'always_defect' | 95 | 10 | 0 | 20 |
|  | 'always_cooperate' | 90 | 90 | 90 | 20 |


### Table M4 — reasoning traces in play

*All rounds and opponents pooled.*

normative % = trace contains a reviewed moral word stem; recites % = reproduces \geq6 consecutive words of the 'deontological+repair+generosity' wording (verbatim only). The principle text is in none of these prompts.

| Policy | n | normative % | recites % |
|---|---|---|---|
| base | 300 | 5 | 0 |
| SDPO deon-repair-gen s80 | 300 | 81 | 0 |
| SDPO deon-repair-gen s150 | 300 | 100 | 20 |

