# Multi-round eval: qwen3_8b / classic / multi_round

Qwen3-8B, game `prisoners_dilemma`, representation `prose`, 5 live rounds per episode, cold open (no fabricated history; rules in round 1 only, one outcome message per later round, conversation accumulates), opponents `tit_for_tat`, `always_defect`, `always_cooperate`, 20 episodes per opponent per policy, T = 0.7, no moral text in any prompt. Jobs 3328654; parse-failure at worst 0.0%.

Policies are the single-step-trained checkpoints; this document measures them IN PLAY. The single-round tables are the same policies' fabricated-history rows (Table M2 pairs them).


### Table M1 — cooperation per round, in play

*Live episodes: cold open (round 1 has no history), one conversation per episode, moves simultaneous.*

P(C) per round over that opponent's episodes (20 per cell, binomial s.e. ≤11 points/round). pooled = all decisions. Round 1 is the untrained opening state; later rounds condition on the episode's own history.

**vs 'tit_for_tat'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 0 | 10 | 0 | 0 | **2** |
| GRPO deon s180 | 10 | 75 | 20 | 50 | 15 | **34** |
| SDPO deon-repair-gen s110 | 15 | 60 | 45 | 60 | 45 | **45** |
| SDPO deon-repair-gen s180 | 70 | 100 | 80 | 100 | 80 | **86** |

**vs 'always_defect'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 5 | 5 | 5 | 5 | **4** |
| GRPO deon s180 | 25 | 5 | 0 | 0 | 0 | **6** |
| SDPO deon-repair-gen s110 | 15 | 40 | 25 | 40 | 25 | **29** |
| SDPO deon-repair-gen s180 | 70 | 15 | 40 | 10 | 40 | **35** |

**vs 'always_cooperate'**

| Policy | r1 | r2 | r3 | r4 | r5 | pooled |
|---|---|---|---|---|---|---|
| base | 0 | 5 | 5 | 5 | 5 | **4** |
| GRPO deon s180 | 25 | 70 | 80 | 80 | 80 | **67** |
| SDPO deon-repair-gen s110 | 5 | 30 | 35 | 45 | 45 | **32** |
| SDPO deon-repair-gen s180 | 60 | 100 | 100 | 100 | 100 | **92** |


### Figure 1 — cooperation per round

![P(C) per round per opponent](figures/multi_round_qwen3_8b.png)

One panel per opponent, one line per policy (base dashed grey). An alternating line is the repair-retaliate 2-cycle; a rising flat-topped line is absorption into cooperation.


### Table M2 — the state table measured in play

*Rounds \geq2 pooled over opponents and rounds; states are the episode's own realized (last move, observation) pairs, so their frequencies are policy-dependent and UNBALANCED.*

P(C | state) in live play, under each policy its single-round fabricated-history row where evaluated (--reference): matching numbers mean the installed table survives the conversation surface. * marks live cells with n < 10 — read those as anecdotes.

| Policy | C<sub><small>A</small></sub>C<sub><small>O</small></sub> | C<sub><small>A</small></sub>D<sub><small>O</small></sub> | D<sub><small>A</small></sub>C<sub><small>O</small></sub> | D<sub><small>A</small></sub>D<sub><small>O</small></sub> |
|---|---|---|---|---|
| base | 100\* | 60\* | 1 | 2 |
| GRPO deon s180 | 98 | 4 | 53 | 2 |
|   · single-round (fabricated) | 96 | 1 | 72 | 3 |
| SDPO deon-repair-gen s110 | 98 | 19 | 31 | 34 |
| SDPO deon-repair-gen s180 | 100 | 16 | 100 | 32 |

*Live n per cell varies by policy (a policy that never reaches a state contributes no estimate there).*

### Table M3 — episode outcomes

*Per policy and opponent, over that cell's episodes.*

open = P(C) in round 1, the state training never showed. final = P(C) in round 5. mutual coop = share of episodes whose LAST round is jointly cooperative (agent C and the opponent/majority of co-players C) — the absorption readout.

| Policy | opponent | open | final | mutual coop | episodes |
|---|---|---|---|---|---|
| base | 'tit_for_tat' | 0 | 0 | 0 | 20 |
|  | 'always_defect' | 0 | 5 | 0 | 20 |
|  | 'always_cooperate' | 0 | 5 | 5 | 20 |
| GRPO deon s180 | 'tit_for_tat' | 10 | 15 | 15 | 20 |
|  | 'always_defect' | 25 | 0 | 0 | 20 |
|  | 'always_cooperate' | 25 | 80 | 80 | 20 |
| SDPO deon-repair-gen s110 | 'tit_for_tat' | 15 | 45 | 35 | 20 |
|  | 'always_defect' | 15 | 25 | 0 | 20 |
|  | 'always_cooperate' | 5 | 45 | 45 | 20 |
| SDPO deon-repair-gen s180 | 'tit_for_tat' | 70 | 80 | 80 | 20 |
|  | 'always_defect' | 70 | 40 | 0 | 20 |
|  | 'always_cooperate' | 60 | 100 | 100 | 20 |


### Table M4 — reasoning traces in play

*All rounds and opponents pooled.*

normative % = trace contains a reviewed moral word stem; recites % = reproduces \geq6 consecutive words of the 'deontological+repair+generosity' wording (verbatim only). The principle text is in none of these prompts.

| Policy | n | normative % | recites % |
|---|---|---|---|
| base | 300 | 2 | 0 |
| GRPO deon s180 | 300 | 3 | 0 |
| SDPO deon-repair-gen s110 | 300 | 75 | 0 |
| SDPO deon-repair-gen s180 | 300 | 100 | 12 |

