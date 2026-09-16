#!/usr/bin/env python3.11
"""Render a display prompt panel through the real game code.

The report's verbatim panels come from measured episodes (prose -- the
protocol every number was produced under). This renders the SAME fixed
presentation in another representation for DISPLAY (the report marks it
as such); going through build_prompt keeps it honest -- payoffs, labels,
state sentence and format line are the real ones, only the payoff-block
rendering differs.

    /usr/bin/python3.11 scripts/analysis/render_prompt_panel.py \\
        --representation list --state CD \\
        --out eval_results/post_training/comparison/analysis/generated/panels/game_prompt_list.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from moralgym_verl.game.environment import EpisodeConfig  # noqa: E402
from moralgym_verl.game.prompts import build_prompt  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--representation", default="list")
    ap.add_argument("--state", default="CD",
                    help="fabricated previous round, e.g. CD = own C, opp D")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    cfg = EpisodeConfig(game_type="prisoners_dilemma", T=4, R=3, P=1, S=0,
                        opponent="random", num_rounds=1,
                        coop_label="action3", defect_label="action4")
    cfg.representation = args.representation
    cfg.reasoning = True
    cfg.opener_order = ("action3", "action4")
    cfg.closer_order = ("action3", "action4")
    prompt = build_prompt(cfg, [args.state[0]], [args.state[1]])
    assert "either  or" not in prompt and "Action: `" not in prompt.replace(
        "`Action: action", ""), "labels did not render"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(prompt)
    print(f"saved -> {args.out}")


if __name__ == "__main__":
    main()
