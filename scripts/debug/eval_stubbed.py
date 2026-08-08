"""Run the REAL eval entrypoint end to end with only the model stubbed.

Executes behavioral.main() unchanged — the genuine main() -> evaluate()
-> run_episode() -> aggregation -> JSON path. Only the three functions
that touch the model are monkeypatched:

    behavioral.load_model_for_eval -> dummy objects (never used downstream)
    generation.render_chat_inputs  -> passes messages through untokenized
    generation.generate            -> scripted tit-for-tat from the prompt text

Everything else — config/protocol handling, teacher wrapping (applied
inside the real make_policy_fn), presentation RNG, the round loop,
parsing, metrics, JSON output — is the real code. Set breakpoints
directly in behavioral.py / trajectory.py / prompts_reasoning.py.

    cd /users/bickery/MoralGymVerl
    PYTHONPATH=src /usr/bin/python3.11 scripts/debug/eval_stubbed.py [behavioral.py flags]

Extra flags are forwarded to behavioral.main(), e.g.:
    ... eval_stubbed.py --protocol single_round --num-episodes 8
    ... eval_stubbed.py --moral-value deontological --opponent tit_for_tat
"""

from __future__ import annotations

import re
import sys
from types import SimpleNamespace

from moralgym_verl.eval import behavioral, generation


def _fake_load_model_for_eval(checkpoint, base_model=None):
    # Shapes only: policy_fn reads model.device; nothing else is touched
    # because the two generation helpers below are stubbed too.
    return SimpleNamespace(device="cpu", eval=lambda: None), SimpleNamespace()


def _fake_render_chat_inputs(tokenizer, messages, device):
    # Real version applies the chat template and tokenizes. Here the
    # messages pass straight through to _fake_generate as "inputs".
    return None, messages


def _fake_generate(model, tokenizer, messages, max_new_tokens, temperature):
    """Scripted tit-for-tat that plays by READING the prompt, like the model
    must: extract the two labels, mirror the opponent's last move from the
    Markov-1 history sentence, cooperate on a fresh round 1."""
    prompt = messages[-1]["content"]
    m = re.search(r"either (\w+) or (\w+)", prompt)
    coop, defect = m.groups() if m else ("action3", "action4")
    hist = re.search(r"they played (\w+)", prompt)
    mine = defect if (hist and hist.group(1) == defect) else coop
    return f"Scripted TFT: mirror the opponent's last move.\nAction: {mine}"


behavioral.load_model_for_eval = _fake_load_model_for_eval
generation.render_chat_inputs = _fake_render_chat_inputs
generation.generate = _fake_generate


if __name__ == "__main__":
    # Defaults keep a debug run small; any flag passed on the command line
    # is appended after them, and argparse lets the later value win.
    sys.argv = [
        "behavioral",
        "--config", "configs/eval/teacher_signal_9b.yaml",
        "--checkpoint", "base",
        "--num-episodes", "8",
        "--output", "eval_results/_debug/behavioral_stubbed.json",
        *sys.argv[1:],
    ]
    behavioral.main()
