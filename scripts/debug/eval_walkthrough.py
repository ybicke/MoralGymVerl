"""Step-by-step walkthrough of the behavioral-eval pipeline. NOT part of
the eval itself — a learning/debugging harness that calls the real code
one stage at a time with a SCRIPTED policy (no GPU, no model download),
printing every intermediate object so you can see exactly what the model
would see and what happens to its output.

Run on the login node (torch imports fine there, nothing heavy executes):

    cd /users/bickery/MoralGymVerl
    PYTHONPATH=src /usr/bin/python3.11 scripts/debug/eval_walkthrough.py            # all stages
    PYTHONPATH=src /usr/bin/python3.11 scripts/debug/eval_walkthrough.py --stage 3  # one stage

Pipeline map (stage -> code it exercises):
  1  config -> EpisodeConfig     eval/behavioral.py:build_eval_config
  2  prompt rendering            game/prompts_reasoning.py:build_prompt
  3  teacher wrapping            eval/teacher_context.py:wrap_prompt
  4  response parsing            game/prompts_reasoning.py:parse_action_structured
  5  one episode, round by round game/trajectory.py:run_episode
  6  metric aggregation          eval/behavioral.py:_aggregate_rollout_metrics

The ONLY thing this file cannot show is the model itself: in a real run,
`make_policy_fn` (behavioral.py:149) wraps model.generate() behind the same
`policy_fn(prompt) -> str` interface our scripted policies implement here.
Everything upstream (what the model reads) and downstream (what happens to
its text) is identical.
"""

from __future__ import annotations

import argparse
import copy
import random

CONFIG_PATH = "configs/eval/teacher_signal_9b.yaml"


def banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def sub(title: str) -> None:
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
# Stage 1 — YAML config -> EpisodeConfig
# ---------------------------------------------------------------------------
def stage1_config():
    """The eval starts from a YAML config. build_eval_config() turns it into
    an EpisodeConfig: ONE episode's fully-specified presentation (labels,
    matrix layout, prose order, role, payoffs). All randomization is drawn
    HERE, once per episode, from a dedicated `presentation_rng` — never
    inside the episode."""
    from moralgym_verl.eval.behavioral import build_eval_config, load_config

    banner("STAGE 1: YAML config -> EpisodeConfig  (behavioral.py:272)")
    cfg = load_config(CONFIG_PATH)

    sub("relevant config blocks")
    for key in ("game", "prompt", "evaluation", "teacher"):
        print(f"{key}: {cfg[key]}")

    sub("fixed presentation (Tennant-exact defaults)")
    # evaluate() (behavioral.py:572) builds this stream as seed + 1_000_003
    # so presentation draws never perturb the module-level random stream.
    rng = random.Random(cfg["seed"] + 1_000_003)
    ep = build_eval_config(cfg, opponent="random", rng=rng)
    print(ep)

    sub("randomized presentation (robustness axes switched on)")
    rcfg = copy.deepcopy(cfg)
    rcfg["evaluation"].update(
        tokens="randomize", layout="randomize", prose="randomize", role="randomize"
    )
    rng_a = random.Random(cfg["seed"] + 1_000_003)
    rng_b = random.Random(cfg["seed"] + 1_000_003)
    for i in range(3):
        ep_a = build_eval_config(rcfg, opponent="random", rng=rng_a)
        ep_b = build_eval_config(rcfg, opponent="random", rng=rng_b)
        assert ep_a == ep_b, "same-seeded streams must pair"
        print(
            f"episode {i}: labels=({ep_a.coop_label},{ep_a.defect_label}) "
            f"layout={ep_a.matrix_layout} opener={ep_a.opener_order} "
            f"closer={ep_a.closer_order} agent_is_row={ep_a.agent_is_row}"
        )
    print(
        "\n(two same-seeded streams produced identical draws -> runs with the\n"
        " same flags are presentation-PAIRED, e.g. robustness cells across\n"
        " moral values; this is the 2026-07-28 statistics-hygiene change)"
    )
    return cfg


# ---------------------------------------------------------------------------
# Stage 2 — EpisodeConfig -> prompt text
# ---------------------------------------------------------------------------
def stage2_prompt(cfg):
    """build_prompt() renders what the model reads each round. With
    prompt.reasoning=true (this config) it routes to prompts_reasoning.py:
    CoT closer, answer format `Action: <label>`. History is Markov-1: only
    the LAST (own, opp) move pair is shown, as one sentence."""
    from moralgym_verl.eval.behavioral import build_eval_config
    from moralgym_verl.game.prompts import build_prompt
    from moralgym_verl.game.trajectory import FAB_STATES

    banner("STAGE 2: EpisodeConfig -> prompt text  (prompts_reasoning.py:22)")
    ep = build_eval_config(cfg, opponent="random")

    sub("round-1 prompt, no history (game_design=nohist)")
    print(build_prompt(ep, [], []))

    sub("the same prompt with a fabricated (D, C) state (game_design=hist)")
    # Stage-1a eval seeds round 1 with a fake previous round: agent_history
    # and opp_history each already hold one move. The prompt renders it as
    # the 'Last round, you played ...' sentence — the model cannot tell
    # fabricated history from real history.
    print("\n".join(build_prompt(ep, ["D"], ["C"]).splitlines()[-8:]))

    sub("all four fabricated states -> the history sentence they produce")
    for a, o in FAB_STATES:
        line = [
            ln for ln in build_prompt(ep, [a], [o]).splitlines()
            if "Last round" in ln
        ][0]
        print(f"({a},{o}): {line}")


# ---------------------------------------------------------------------------
# Stage 3 — teacher wrapping
# ---------------------------------------------------------------------------
def stage3_teacher(cfg):
    """Teacher-signal eval wraps the game prompt in the SDPO reprompt
    template (read from the TRAINING yaml — single source of truth) with a
    static moral value in the {feedback} slot. moral_value='none' returns
    the prompt byte-identical -> the baseline cell really is the plain
    student."""
    from moralgym_verl.eval.behavioral import build_eval_config
    from moralgym_verl.eval.teacher_context import load_reprompt_template, wrap_prompt
    from moralgym_verl.game.moral_values import MORAL_VALUE_REGISTRY, get_moral_value
    from moralgym_verl.game.prompts import build_prompt

    banner("STAGE 3: teacher wrapping  (teacher_context.py:90)")
    sub("registry")
    for name in sorted(MORAL_VALUE_REGISTRY):
        text = get_moral_value(name)
        print(f"{name}: {text[:80] + '...' if len(text) > 80 else text}")

    template = load_reprompt_template(cfg["teacher"]["template_source"])
    sub(f"reprompt_template (from {cfg['teacher']['template_source']})")
    print(template)

    ep = build_eval_config(cfg, opponent="random")
    game_prompt = build_prompt(ep, ["C"], ["C"])

    sub("wrapped prompt, moral_value=deontological (what the model sees)")
    print(
        wrap_prompt(
            game_prompt,
            reprompt_template=template,
            moral_value_text=get_moral_value("deontological"),
            feedback_template=cfg["teacher"]["feedback_template"],
        )
    )

    sub("moral_value=none")
    unwrapped = wrap_prompt(
        game_prompt, reprompt_template=template,
        moral_value_text=get_moral_value("none"),
        feedback_template=cfg["teacher"]["feedback_template"],
    )
    print(f"byte-identical to plain prompt: {unwrapped == game_prompt}")


# ---------------------------------------------------------------------------
# Stage 4 — parsing the model's raw text
# ---------------------------------------------------------------------------
def stage4_parsing(cfg):
    """parse_action() routes on config flags; reasoning=true -> the STRICT
    structured parser: last well-formed `Action: <label>` after any </think>
    block, else illegal. No lenient fallback ('better no signal than a
    wrong one' — prose-mention inference was measurably wrong on Stage-1a
    traces, 2026-07-14)."""
    from moralgym_verl.eval.behavioral import build_eval_config
    from moralgym_verl.game.prompts import parse_action

    banner("STAGE 4: raw text -> C / D / illegal  (prompts_reasoning.py:79)")
    ep = build_eval_config(cfg, opponent="random")  # labels action3/action4

    cases = [
        ("Action: action3", "clean"),
        ("I weigh trust vs points...\nAction: action4", "reasoning then label"),
        ("**Action:** action3", "markdown separator"),
        ("Action: action3.", "trailing punctuation"),
        ("Action: action3\nBut A likely chooses action4.",
         "prose mention AFTER the Action line (old optional-separator bug "
         "let this hijack; separator now required)"),
        ("I will pick action3 for mutual benefit.",
         "prose only, no Action line -> illegal (no lenient fallback)"),
        ("Let me think about whether action3 or",
         "truncated before the label -> illegal (raise max_new_tokens)"),
        ("<think>Action: action3 is tempting</think>\nAction: action4",
         "Action inside <think> ignored; post-</think> region wins"),
    ]
    for raw, why in cases:
        result = parse_action(raw, ep)
        print(f"{str(result):>7}  <- {raw!r}\n         ({why})")


# ---------------------------------------------------------------------------
# Scripted policies (stand-ins for the model)
# ---------------------------------------------------------------------------
def make_scripted_tft(config):
    """A 'model' that plays tit-for-tat by actually READING the prompt,
    the way the real model must: find the history sentence, extract the
    opponent's last label, mirror it. Cooperates on a fresh round 1."""

    def policy_fn(prompt: str) -> str:
        import re

        m = re.search(r"they played (\w+)", prompt)
        opp_label = m.group(1) if m else config.coop_label
        mine = (
            config.coop_label
            if opp_label == config.coop_label
            else config.defect_label
        )
        return f"A reads the history and mirrors it.\nAction: {mine}"

    return policy_fn


def make_replay_policy(responses):
    """Replays a fixed list of raw strings — for demonstrating exact
    failure sequences (e.g. an illegal round)."""
    queue = list(responses)
    return lambda prompt: queue.pop(0)


# ---------------------------------------------------------------------------
# Stage 5 — one full episode
# ---------------------------------------------------------------------------
def stage5_episode(cfg):
    """run_episode() is the core loop: build prompt -> policy -> parse ->
    opponent move -> payoffs -> append history. Illegal moves FREEZE the
    state (history not advanced, no opponent move, no payoff) and inject a
    parse-fail feedback line into the next prompt — training parity with
    verl's game_interaction."""
    from moralgym_verl.eval.behavioral import build_eval_config
    from moralgym_verl.game.trajectory import run_episode

    banner("STAGE 5: one episode, round by round  (trajectory.py:71)")
    random.seed(cfg["seed"])  # opponent bots draw from module random

    sub("5 rounds of scripted TFT vs tit_for_tat (mutual cooperation lock-in)")
    ep = build_eval_config(cfg, opponent="tit_for_tat")
    traj = run_episode(
        ep, make_scripted_tft(ep),
        lambda_val=cfg["reward"]["lambda"],
        intrinsic_type=cfg["reward"]["intrinsic"],
        game_reward_type=cfg["reward"]["game_reward"],
        verbose=True,
    )
    print(f"agent_moves={traj.agent_moves} opp_moves={traj.opponent_moves}")
    print(f"cooperation_rate={traj.cooperation_rate:.0%}  rewards={traj.rewards}")

    sub("Stage-1a shape: single round, fabricated (C,D) state, vs random")
    from dataclasses import replace

    ep = replace(build_eval_config(cfg, opponent="random"), num_rounds=1)
    traj = run_episode(
        ep, make_scripted_tft(ep),
        lambda_val=cfg["reward"]["lambda"],
        intrinsic_type=cfg["reward"]["intrinsic"],
        game_reward_type=cfg["reward"]["game_reward"],
        fabricate_history=True, fab_state=("C", "D"),
        verbose=True,
    )
    print(
        f"fab state ({traj.fab_agent},{traj.fab_opp}) -> agent answered "
        f"{traj.agent_moves[0]} (TFT retaliates the fabricated D)"
    )

    sub("illegal round: state freezes, feedback injected into next prompt")
    ep = replace(build_eval_config(cfg, opponent="tit_for_tat"), num_rounds=3)
    traj = run_episode(
        ep,
        make_replay_policy([
            "I refuse to answer in the requested format.",  # -> illegal
            f"Action: {ep.coop_label}",
            f"Action: {ep.coop_label}",
        ]),
        lambda_val=cfg["reward"]["lambda"],
        intrinsic_type=cfg["reward"]["intrinsic"],
        game_reward_type=cfg["reward"]["game_reward"],
        verbose=True,
    )
    print(f"agent_moves={traj.agent_moves}  parse_failures={traj.parse_failures}")
    print("\nround-2 prompt began with the injected feedback + frozen history:")
    print("\n".join(traj.per_round[1]["prompt"].splitlines()[:2]))
    assert "first round" in traj.per_round[1]["prompt"]  # history really frozen


# ---------------------------------------------------------------------------
# Stage 6 — aggregation into the numbers you analyze
# ---------------------------------------------------------------------------
def stage6_aggregation(cfg):
    """evaluate() runs num_episodes episodes per opponent and feeds them to
    _aggregate_rollout_metrics(). The state-conditioning table — the main
    Stage-1a result, P(C | agent_prev, opp_prev) — is built by
    _iter_conditioned(): each decision is conditioned on the last LEGAL
    (agent, opp) pair, seeded from the fabricated state."""
    from dataclasses import replace

    from moralgym_verl.eval.behavioral import (
        _aggregate_rollout_metrics, build_eval_config,
    )
    from moralgym_verl.game.trajectory import FAB_STATES, run_episode

    banner("STAGE 6: episodes -> metrics  (behavioral.py:381)")
    random.seed(cfg["seed"])

    # 8 single-round episodes vs random, balanced design: episode i gets
    # FAB_STATES[i % 4] — exactly n/4 decisions per state, deterministic
    # (state_design='balanced', the evaluate() default).
    n = 8
    trajectories = []
    for i in range(n):
        ep = replace(build_eval_config(cfg, opponent="random"), num_rounds=1)
        trajectories.append(
            run_episode(
                ep, make_scripted_tft(ep),
                lambda_val=cfg["reward"]["lambda"],
                intrinsic_type=cfg["reward"]["intrinsic"],
                game_reward_type=cfg["reward"]["game_reward"],
                fabricate_history=True,
                fab_state=FAB_STATES[i % len(FAB_STATES)],
            )
        )

    result = _aggregate_rollout_metrics(trajectories, "random", n)

    sub("state-conditioning table (the Stage-1a headline number)")
    for state, m in sorted(result["state_conditioning"].items()):
        print(
            f"  {state}: C={m['p_C']:.0%} D={m['p_D']:.0%} "
            f"illegal={m['p_illegal']:.0%} (n={m['n']})"
        )
    print(
        "\nScripted TFT -> P(C|*,C)=100%, P(C|*,D)=0%: 'textbook reciprocity'.\n"
        "The real deontological cell (2026-07-14) read 98/28/92/40 on this\n"
        "same table. teacher_signal_table.py renders exactly these keys."
    )

    sub("other keys the JSON will contain")
    scalars = {
        k: v for k, v in result.items()
        if isinstance(v, (int, float)) and v is not None
    }
    for k, v in sorted(scalars.items()):
        print(f"  {k} = {v:.3f}")
    print(
        "\n(mean_r_* / regret_* come from _score_rewards -> scoring.py +\n"
        " baselines.py: reward streams per morality, normalized against\n"
        " per-game best/worst — Tennant's Figure-5 scale. *_legal variants\n"
        " exclude illegal decisions to isolate moral signal from parseability.)"
    )


STAGES = {
    1: ("config -> EpisodeConfig", stage1_config),
    2: ("prompt rendering", stage2_prompt),
    3: ("teacher wrapping", stage3_teacher),
    4: ("response parsing", stage4_parsing),
    5: ("episode rollout", stage5_episode),
    6: ("metric aggregation", stage6_aggregation),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--stage", type=int, default=None, choices=sorted(STAGES),
        help="run one stage (default: all, in order)",
    )
    args = parser.parse_args()

    from moralgym_verl.eval.behavioral import load_config

    cfg = load_config(CONFIG_PATH)
    stages = [args.stage] if args.stage else sorted(STAGES)
    for s in stages:
        fn = STAGES[s][1]
        fn(cfg) if s != 1 else fn()

    print(
        "\nDone. Next layer down: read evaluate() (behavioral.py:551) — it is\n"
        "just these stages in a loop, plus model loading (make_policy_fn) and\n"
        "JSON writing. For the probes, see eval/probe_answer_token.py (A),\n"
        "eval/probe_reasoning_trace.py (B), and eval/teacher_forcing.py\n"
        "(shared primitives incl. generalized_jsd = the SDPO loss)."
    )


if __name__ == "__main__":
    main()
