# Reading: GRPO with a thinking judge on the principle (seed 1)

Run `qwen3_8b_grpo_pd_judge-think_tft_200_seed1` (Rorqual job 27283138, 200
steps, 10:45 h, MoralGymVerl 893dbde). Trainer = the paper's GRPO v2 (lr 1e-5,
warmup 10, KL 0.04); the reward is the frozen base Qwen3-8B judging each
rollout against the deontological principle, P(yes) after thinking
(`configs/datasets/grpo_pd_judge-think_tft.yaml`). Design:
`~/moralgym-report/notes/judge_grpo_design.tex`, row 2. Numbers below are from
`results_training_rollouts.md` (training) and the three transfer groups
(`eval_results/transfer/qwen3_8b/pgg/*grpo_judge-think*`).

## 1. The policy learns strict reciprocity, as GRPO on the hand reward

Steps 181-200 (Table J1): P(C) 95% after mutual cooperation and 84% after
its own defection on a cooperator, 0% after being exploited and 10% after
mutual defection. Cooperation follows the other player's last move only.
SDPO on the same prompts cooperates in about 75% of decisions after being
exploited (paper, training figure). The judge drives this (Table J2):
retaliation scores 0.92, forgiveness 0.69, reciprocation 0.97.

## 2. The judge makes the traces talk about exploitation, not apply the principle

Normative vocabulary rises from 7% to 81% of rollouts (Table J4), almost all
of it the stem "exploit". Verbatim recitation stays at 0%. Table J5 splits
the exploitation language by direction:

- self-protective (the agent is or could be exploited): 2% -> 64%
- the principle's direction (the agent would exploit the other, or must not): 1% -> 33%
- "good faith", the principle's own test: 0% at every step

Typical decisive sentences at step 200: "To maximize your score, and avoid
exploitation, it's better to choose action2" (retaliating); "Choosing action1
incentivizes A to respond with action1 and gives you a stable 3 points. It
avoids exploitation" (cooperating). The reasons are strategic: protect the
own score, keep the opponent cooperative.

## 3. Many traces misread who exploited whom, and the judge rewards them

In state (D, C), where the agent itself defected on a cooperator, the share
of rollouts claiming the agent is being exploited rises from 2% to 80%
(Table J5, misrd). One step-200 rollout calls the opponent's cooperation
"exploiting your choice of action2". Such rollouts receive judge scores near
1.0 when the action is cooperative. The judge reads the action, not whether
the reasoning about the history is correct.

Judge leniency toward exploitation also grows: the mean score of defecting
after the other cooperated rises from 0.02 to 0.30 (Table J2), on few
rollouts (the policy rarely exploits).

## 4. No transfer to the public-goods game

Step 200, no moral text in the prompt (same protocols as the paper):

| | Single round | Full contributor | Free rider | Noisy |
|---|---|---|---|---|
| judge-GRPO, cold opening | 5 | 7 | 6 | 7 |
| judge-GRPO, fabricated opening | 5 | 6 | 4 | 5 |
| GRPO hand reward, 3 seeds | 3 | 10 | 4 | 6 |
| SDPO, 3 seeds | 63 | 92 | 38 | 80 |

Single round by state: 26% after its own contribution with all three others
contributing, 2% after its own keeping with all three contributing, the two
states where its own reciprocity rule says contribute (GRPO hand reward
10-30% and 0-2%, SDPO 98-100% and 86-96%). Live play stays at 2-12% in every
round; it opens at 8% (SDPO 76%). Transfer traces: normative vocabulary 5%,
recitation 0% (single round, Table T2).

## Reading

Giving GRPO the principle through a judge changes the training traces but
not the outcome. The policy learns the same strict rule as the hand reward,
its reasons are strategic and often misread the history, and nothing carries
to the public-goods game. This rules out the strictness of the rule alone
and surface moral vocabulary alone as the explanation of SDPO's transfer; it
leaves the update (one scalar per response against per-token distillation)
and what SDPO distils. The SFT run and SDPO with a strict-reciprocity teacher
separate these.

## Caveats

- One seed. The gap to SDPO (5 against 63% single round, 7 against 92% with
  full contributors) is far beyond the seed spread of either arm.
- Self / other / misread are regex measures; they miss paraphrase and can
  misfire on negation. Read as approximate shares; the morality reasoning
  index and the planned human check are the proper test.
- The judge is the same base model as the policy, with thinking. A stronger
  or rubric judge (arm B of the design note) might reward correct reasoning
  about the history; that is a different, untested arm.
