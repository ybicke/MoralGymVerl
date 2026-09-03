"""Teacher-context wrapping for the Session-1 teacher-signal eval.

Reproduces the teacher prompt SDPO's ray_trainer._build_teacher_message
builds: reprompt_template.format(prompt=..., solution=..., feedback=...).
Two deliberate eval-time differences: {solution} is always empty (demos
come from sibling rollouts during training; eval has none), and
{feedback} holds a static moral value instead of the hindsight critique
— so the feedback wrapper comes from the eval config (default below),
NOT the training yaml, whose wording assumes a critiqued move (Session 2
should adopt the same neutral wording). reprompt_template itself IS read
from the training yaml — single source of truth for the teacher's
structure.

Keep this module torch-free so it stays unit-testable outside the container.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

# Neutral feedback wrapper for static moral values (eval default; mirror
# it in the training yaml's feedback_template for Session 2).
DEFAULT_FEEDBACK_TEMPLATE = "\nMoral value to follow:\n{feedback_raw}\n"

# src/moralgym_verl/eval/teacher_context.py -> repo root. Valid because
# the repo is always bind-mounted / editable-installed from source.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve_yaml_path(yaml_path: str) -> str:
    """teacher.template_source is absolute or repo-relative (the
    convention in configs/eval/harness/<model>/<family>.yaml) — a relative path always anchors
    to the repo root, never to the caller's cwd, so resolution is
    deterministic no matter where the process was launched."""
    p = Path(yaml_path)
    return yaml_path if p.is_absolute() else str(_REPO_ROOT / p)


def load_reprompt_template(yaml_path: str) -> str:
    """Read actor_rollout_ref.actor.self_distillation.reprompt_template
    from an SDPO training yaml (plain safe_load — the key is a literal
    string, no hydra interpolation)."""
    with open(_resolve_yaml_path(yaml_path)) as f:
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
    """Read self_distillation.alpha from the SDPO training yaml — single
    source of truth, so the probe's JSD cannot drift from the training
    loss's divergence."""
    with open(_resolve_yaml_path(yaml_path)) as f:
        cfg = yaml.safe_load(f)
    try:
        return float(
            cfg["actor_rollout_ref"]["actor"]["self_distillation"]["alpha"]
        )
    except (KeyError, TypeError):
        return default


def wrap_latest_user(messages: list, wrapper) -> list:
    """Copy of the transcript with ONLY the last user message wrapped
    (wrapper: str -> str). Mirrors ray_trainer._build_teacher_message:
    plain student transcript, template on the final user turn. Used by
    conversation mode as the 'latest' wrap position."""
    if not messages or messages[-1].get("role") != "user":
        raise ValueError("transcript must end with a user message")
    wrapped = list(messages)
    wrapped[-1] = {"role": "user", "content": wrapper(messages[-1]["content"])}
    return wrapped


def wrap_first_user(messages: list, wrapper) -> list:
    """Copy of the transcript with ONLY the FIRST user message wrapped.

    Training-exact for MULTI-TURN SDPO: the trainer wraps raw_prompt (the
    episode's initial message); every later round lives in the response
    region, reused verbatim — the loss needs identical suffix tokens in
    both passes, so only the prefix can differ. Default wrap position for
    conversation mode; wrap_latest_user is the persistent-context ablation."""
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

    Empty moral_value_text returns the prompt unchanged — matching the
    trainer, which reprompts only samples with solution/feedback; keeps
    the 'none' baseline byte-identical to the plain student eval.
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
