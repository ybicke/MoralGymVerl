"""Teacher-context wrapping for the Session-1 teacher-signal eval.

Reproduces, at eval time, the teacher prompt the SDPO trainer builds in
SDPO/verl/trainer/ppo/ray_trainer.py:_build_teacher_message:

    reprompt_template.format(prompt=<game prompt>,
                             solution=<demo section>,
                             feedback=<feedback section>)

Differences at eval time, both deliberate:
  - {solution} is always empty: demonstrations come from sibling rollouts
    during training; a behavioral eval has none (and Session 2 disables
    them anyway — single-label demos carry no information here).
  - {feedback} holds a static moral value instead of the hindsight
    critique: the critique describes a move, and at generation time no
    move exists yet. The wrapping feedback template is therefore taken
    from the eval config (default below), NOT from the training yaml,
    whose current wording ("Feedback on your previous attempt") assumes
    a critiqued move. Session 2 should adopt the same neutral wording.

`reprompt_template` itself IS read from the training yaml — single
source of truth for the teacher's structure. Keep this module free of
torch/transformers imports so it stays unit-testable outside the
container.
"""

from __future__ import annotations

from typing import Optional

import yaml

# Neutral feedback wrapper for static moral values (eval default; mirror
# it in the training yaml's feedback_template for Session 2).
DEFAULT_FEEDBACK_TEMPLATE = "\nMoral value to follow:\n{feedback_raw}\n"


def load_reprompt_template(yaml_path: str) -> str:
    """Read actor_rollout_ref.actor.self_distillation.reprompt_template
    from a (hydra-style) SDPO training yaml. Plain yaml.safe_load is
    enough — the key is a literal string, no interpolation involved."""
    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)
    try:
        return cfg["actor_rollout_ref"]["actor"]["self_distillation"][
            "reprompt_template"
        ]
    except (KeyError, TypeError) as e:
        raise KeyError(
            f"No actor_rollout_ref.actor.self_distillation.reprompt_template "
            f"in {yaml_path}"
        ) from e


def load_distillation_alpha(yaml_path: str, default: float = 0.5) -> float:
    """Read actor_rollout_ref.actor.self_distillation.alpha from the SDPO
    training yaml — single source of truth, so the probe's full-vocab JSD
    cannot drift from the divergence the training loss actually uses."""
    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)
    try:
        return float(
            cfg["actor_rollout_ref"]["actor"]["self_distillation"]["alpha"]
        )
    except (KeyError, TypeError):
        return default


def wrap_latest_user(messages: list, wrapper) -> list:
    """Return a copy of a chat transcript with ONLY the last user message
    passed through `wrapper` (a callable str -> str).

    Mirrors the SDPO teacher exactly: during training the transcript is
    student-generated (plain), and the reprompt template wraps only the
    final user turn (ray_trainer._build_teacher_message: msgs[:-1] pass
    through, msgs[-1] is templated). Used by the behavioral eval's
    transcript mode (multi-turn, Stage 1b)."""
    if not messages or messages[-1].get("role") != "user":
        raise ValueError("transcript must end with a user message")
    wrapped = list(messages)
    wrapped[-1] = {"role": "user", "content": wrapper(messages[-1]["content"])}
    return wrapped


def wrap_first_user(messages: list, wrapper) -> list:
    """Return a copy of a chat transcript with ONLY the FIRST user message
    passed through `wrapper`.

    Training-exact for MULTI-TURN SDPO: the trainer wraps `raw_prompt` —
    the episode's initial message — and every later round (env messages +
    traces) lives in the response region, reused verbatim. The teacher
    therefore sees the moral value once, at episode start. (Structural:
    the loss needs identical suffix tokens in both passes, so only the
    prefix can differ.) Used by transcript mode with wrap_position='first'
    (default); wrap_latest_user remains as the persistent-context
    ablation ('latest')."""
    if not messages or messages[0].get("role") != "user":
        raise ValueError("transcript must start with a user message")
    wrapped = list(messages)
    wrapped[0] = {"role": "user", "content": wrapper(messages[0]["content"])}
    return wrapped


def wrap_prompt(
    game_prompt: str,
    reprompt_template: str,
    moral_value_text: str,
    feedback_template: Optional[str] = None,
) -> str:
    """Wrap one game prompt in the teacher template.

    Empty moral_value_text returns the game prompt unchanged — matching
    the trainer, which reprompts only samples that have solution or
    feedback (ray_trainer.py:734-741). This keeps the 'none' baseline
    byte-identical to the plain student eval.
    """
    if not moral_value_text:
        return game_prompt
    feedback_section = (feedback_template or DEFAULT_FEEDBACK_TEMPLATE).format(
        feedback_raw=moral_value_text
    )
    return reprompt_template.format(
        prompt=game_prompt,
        solution="",
        feedback=feedback_section,
    )
