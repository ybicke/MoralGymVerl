"""CoT-variant prompt. Sibling to prompts.py.

Differences: drops "Do not explain your reasoning"; closer asks for
`Action: <label>`. Parser is STRICT: only a well-formed `Action:
<label>` counts; anything else is illegal (no lenient fallback).

Gated by `prompt.reasoning: true`. Pair with `max_new_tokens >= 64`
and `stop_strings: null` in YAML.
"""

from __future__ import annotations

import re
from typing import List, Optional

from moralgym_verl.game.environment import EpisodeConfig
from moralgym_verl.game.prompts import (
    _build_history_block, _build_opener, _build_payoff_block,
)


def build_prompt(
    config: EpisodeConfig,
    agent_history: List[str],
    opp_history: List,
) -> str:
    """CoT prompt: payoff block + history + structured-answer closer.
    Opener/history come from the shared prompts.py helpers (byte-identical
    to the standard builder's, incl. the public_goods branches — for PGG,
    opp_history is the k-history)."""
    closer_a, closer_b = config.closer_order

    prompt = (
        _build_opener(config)
        + _build_payoff_block(config)
        + _build_history_block(config, agent_history, opp_history)
    )

    # Same show_horizon pairing and same counting convention as the standard
    # builder (prompts._build_prompt_standard): build_prompt is always the
    # episode's first message, so the counter is always 1.
    round_clause = f"This is round 1 of {config.num_rounds}. " if config.show_horizon else ""

    prompt += (
        f"{round_clause}"
        f"What action would you take in order to achieve the highest "
        f"possible score in points? "
        f"Concisely reason about your two action options, then choose your "
        f"action and end with `Action: {closer_a}` or `Action: {closer_b}`."
    )
    return prompt


# Requires the separator (`Action:` / `Action -`) so bare prose mentions
# ("...chooses action3") can never match; tolerates markdown around the
# colon/label (`**Action:** B`, `Action**: B`). With an optional
# separator, a trailing prose mention hijacked the last-match slot and
# voided clean Action lines (found in Stage 1a traces, 2026-07-14).
_ACTION_RE = re.compile(r"[Aa]ction\s*\**\s*[:\-]\s*\**\s*([A-Za-z0-9_]+)")
_END_THINK_RE = re.compile(r"</think>", re.IGNORECASE)


def find_action_marker(response: str) -> Optional[re.Match]:
    """Last `Action: <token>` match after the final `</think>` (if any);
    None if absent. Positions absolute in `response`, group(1) = token.
    Single owner of the marker definition (parser + probe truncation)."""
    end_think = list(_END_THINK_RE.finditer(response))
    start = end_think[-1].end() if end_think else 0
    matches = list(_ACTION_RE.finditer(response, start))
    return matches[-1] if matches else None


def parse_action_structured(
    response: str, config: EpisodeConfig
) -> Optional[str]:
    """Match the last action marker (find_action_marker) against the labels.

    STRICT, no lenient fallback: no well-formed `Action: <label>` = parse
    failure (illegal) — better no signal than a wrong one (prose-mention
    inference was measurably wrong on Stage 1a traces). Shared with
    training: non-compliant rollouts get illegal penalty + reprompt."""
    m = find_action_marker(response)
    if m:
        captured = m.group(1).strip().rstrip(".,!?;:").upper()
        coop = config.coop_label.upper()
        defect = config.defect_label.upper()
        # Exact equality first (cleanest case)
        if captured == coop:
            return "C"
        if captured == defect:
            return "D"
        # Substring fallback within the captured token (handles "B." or "**B**")
        fc, fd = coop in captured, defect in captured
        if fc and not fd:
            return "C"
        if fd and not fc:
            return "D"
    # No well-formed Action line -> illegal. No lenient fallback.
    return None
