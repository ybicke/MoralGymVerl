#!/usr/bin/env python3.11
"""Chat-template preflight: run this BEFORE submitting a new model's first training run.

Every template bug we have hit was findable in seconds without a GPU, and each one instead
cost a wasted job:

  Gemma-2   stock template raises on verl's two-user probe        (caught in a smoke test)
  Gemma-3   same, unfixed for this model                          (2 dead jobs, 0 ckpts)
  Llama-3.1 tools=[] renders a tool-calling preamble              (3h run, unusable)
  Qwen3     history rendering is not concatenative                (would corrupt multi-turn)

This script states the properties the MoralGymVerl stack requires of a chat template and
checks them. Pure Jinja -- no GPU, no container, runs on the login node.

    /usr/bin/python3.11 scripts/preflight/check_model_template.py            # all known models
    /usr/bin/python3.11 scripts/preflight/check_model_template.py --model Qwen/Qwen3-8B
    /usr/bin/python3.11 scripts/preflight/check_model_template.py --multi-turn

Exit code is non-zero if any REQUIRED check fails, so it can gate a submit script.

Checks
------
P1 prompt fidelity  The rendered single-turn prompt must contain the game text and NOTHING
                    else that we did not ask for -- no injected system persona, no tool
                    preamble, no ipython environment block. Catches the tools=[] class.
P2 probe            Does verl's initialize_system_prompt() survive? Only REQUIRED for
                    multi-turn (--multi-turn); single-turn no longer needs it since the
                    lazy-property fix in ~/SDPO.
P3 concatenativity  Is render(msgs[:k]) always a prefix of render(msgs[:k+1])? Necessary
                    but NOT sufficient -- kept as a diagnostic that localises the cause.
P4 splice soundness Is render(turn-k prompt) + generated_text a prefix of render(turn-k+1
                    prompt)? THE criterion multi-turn needs. Strictly stronger than P3.
                    REQUIRED with --multi-turn.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

try:
    from jinja2 import BaseLoader, Environment
except ImportError:
    sys.exit("jinja2 not available for this interpreter; use /usr/bin/python3.11")

_CLUSTER_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "slurm", "cluster.env")
if os.path.isfile(_CLUSTER_ENV):
    for _line in open(_CLUSTER_ENV):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), os.path.expandvars(_v.strip().strip('"')))
HF_HOME = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))

# Models we have trained or intend to train, with the apply_chat_template kwargs the
# configs actually pass (configs/training/*.yaml -> data.apply_chat_template_kwargs).
KNOWN = {
    "Qwen/Qwen3-8B": {"enable_thinking": False},
    "Qwen/Qwen3-4B-Instruct-2507": {},   # non-thinking variant: no enable_thinking branch
    "meta-llama/Llama-3.1-8B-Instruct": {},
    "google/gemma-2-9b-it": {},
    "google/gemma-3-12b-it": {},
}

# Text a MoralGym prompt must never contain: every one of these is a template injecting
# instructions we did not write.
FORBIDDEN = [
    "function call",       # Llama tool preamble (the tools=[] bug)
    "Environment: ipython",
    "<tools>",             # Qwen tool preamble
    "You are a helpful",   # injected default persona
    "Cutting Knowledge",   # Llama's dated system block; harmless but must be deliberate
]
# Substrings that are acceptable even though they look like the above.
ALLOWED_EXCEPTIONS = {"meta-llama/Llama-3.1-8B-Instruct": ["Cutting Knowledge"]}

GAME_TEXT = (
    "You are playing a game with agent A. You must choose either action1 or action2."
)

U = lambda c: {"role": "user", "content": c}       # noqa: E731
A = lambda c: {"role": "assistant", "content": c}  # noqa: E731


def load_template(model_id: str) -> str | None:
    """Return the raw Jinja chat template for a cached model, or None if not cached."""
    org, name = model_id.split("/")
    base = f"{HF_HOME}/hub/models--{org}--{name}/snapshots/*"
    for pattern, extract in (
        (f"{base}/chat_template.jinja", lambda p: open(p, errors="ignore").read()),
        (f"{base}/chat_template.json", lambda p: _from_json(p, "chat_template")),
        (f"{base}/tokenizer_config.json", lambda p: _from_json(p, "chat_template")),
    ):
        hits = glob.glob(pattern)
        if hits:
            tpl = extract(hits[0])
            if tpl:
                return tpl
    return None


def _from_json(path: str, key: str):
    obj = json.load(open(path))
    if isinstance(obj, dict):
        return obj.get(key)
    return obj


def make_env() -> Environment:
    env = Environment(loader=BaseLoader())
    env.globals["raise_exception"] = lambda m: (_ for _ in ()).throw(Exception(m))
    env.globals["strftime_now"] = lambda fmt: "26 Jul 2026"
    return env


def render(tpl, messages, kwargs, **extra):
    return tpl.render(
        messages=messages,
        bos_token="<BOS>",
        eos_token="<EOS>",
        date_string="26 Jul 2026",
        **kwargs,
        **extra,
    )


def check_p1_prompt_fidelity(tpl, kwargs, model_id):
    """The rendered single-turn prompt must be our text and nothing else."""
    try:
        out = render(tpl, [U(GAME_TEXT)], kwargs, add_generation_prompt=True)
    except Exception as e:
        return False, f"render failed: {e}"
    if GAME_TEXT not in out:
        return False, "game text missing from rendered prompt"
    allowed = ALLOWED_EXCEPTIONS.get(model_id, [])
    hits = [f for f in FORBIDDEN if f in out and f not in allowed]
    if hits:
        return False, f"template injected: {', '.join(hits)}"
    return True, f"{len(out)} chars, no injected instructions"


def check_p2_probe(tpl, kwargs):
    """Does verl's initialize_system_prompt() probe survive this template?"""
    try:
        t1 = render(tpl, [U("")], kwargs, add_generation_prompt=False)
        t2 = render(tpl, [U(""), U("")], kwargs, add_generation_prompt=False)
    except Exception as e:
        return False, f"raises: {str(e)[:60]}"
    delta = len(t2) - len(t1)
    if delta <= 0:
        return False, f"nonsensical delta {delta}"
    return True, f"prefix = {len(t1) - delta} chars"


def check_p3_concatenative(tpl, kwargs):
    """render(msgs[:k]) must be a prefix of render(msgs[:k+1]) for every k."""
    conv = [U("r1"), A("a1"), U("r2"), A("a2"), U("r3")]
    for k in range(1, len(conv)):
        try:
            short = render(tpl, conv[:k], kwargs, add_generation_prompt=False)
            longer = render(tpl, conv[: k + 1], kwargs, add_generation_prompt=False)
        except Exception as e:
            return False, f"render failed at k={k}: {str(e)[:50]}"
        if not longer.startswith(short):
            i = next(
                (j for j in range(min(len(short), len(longer))) if short[j] != longer[j]),
                min(len(short), len(longer)),
            )
            return False, f"appending msg {k + 1} rewrites earlier tokens (char {i})"
    return True, "safe to splice"


def check_p4_splice_sound(tpl, kwargs):
    """The criterion multi-turn actually needs.

    render(turn-k prompt) + generated_text must be a PREFIX of render(turn-k+1 prompt).
    If it is not, the token sequence the agent loop splices together is not the sequence
    the model would see at inference -- training and serving silently disagree.

    Strictly stronger than P3: Qwen3 can look concatenative in history alone yet still fail
    here, because the generation prompt prefills <think>\n\n</think>\n\n while no history
    rendering reproduces it.
    """
    hist = [U("r1")]
    for i in range(2):
        try:
            prompt = render(tpl, hist, kwargs, add_generation_prompt=True)
            gen = f"a{i}"
            hist = hist + [A(gen), U(f"r{i + 2}")]
            nxt = render(tpl, hist, kwargs, add_generation_prompt=True)
        except Exception as e:
            return False, f"render failed at turn {i}: {str(e)[:50]}"
        if not nxt.startswith(prompt + gen):
            return False, f"turn {i}: appending the reply rewrites the prompt"
    return True, "prompt + generation splices cleanly"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", help="model id (repeatable); default: all known")
    ap.add_argument("--multi-turn", action="store_true",
                    help="require P2 and P3 as well (needed once rollouts are multi-turn)")
    args = ap.parse_args()

    models = args.model or list(KNOWN)
    env = make_env()
    required = ["P1"] + (["P2", "P3", "P4"] if args.multi_turn else [])
    print(f"Required for this mode: {', '.join(required)}"
          f"   ({'multi-turn' if args.multi_turn else 'single-turn'})\n")

    failed = False
    for model_id in models:
        raw = load_template(model_id)
        if raw is None:
            print(f"{model_id}\n   SKIP  template not cached under $HF_HOME\n")
            continue
        tpl = env.from_string(raw)
        kwargs = KNOWN.get(model_id, {})
        print(f"{model_id}   apply_chat_template_kwargs={kwargs or '{}'}")
        for name, fn in (
            ("P1 prompt fidelity ", lambda: check_p1_prompt_fidelity(tpl, kwargs, model_id)),
            ("P2 verl probe      ", lambda: check_p2_probe(tpl, kwargs)),
            ("P3 concatenative   ", lambda: check_p3_concatenative(tpl, kwargs)),
            ("P4 splice-sound    ", lambda: check_p4_splice_sound(tpl, kwargs)),
        ):
            ok, detail = fn()
            tag = name.split()[0]
            is_required = tag in required
            if ok:
                mark = "PASS"
            elif is_required:
                mark, failed = "FAIL", True
            else:
                mark = "warn"
            print(f"   {mark:<4} {name} {detail}")
        print()

    if failed:
        print("PREFLIGHT FAILED — do not submit. Remedies:")
        print("  P1  the template is injecting text. Check what the caller passes to")
        print("      apply_chat_template (an empty tools=[] renders Llama's tool preamble).")
        print("  P2  supply actor_rollout_ref.model.custom_chat_template = the stock")
        print("      template minus ONLY its role-alternation raise, and assert it renders")
        print("      byte-identically to stock for legal chats.")
        print("  P3  the template rewrites history (e.g. Qwen3 strips <think> from earlier")
        print("      assistant turns). Force the history branch in a custom_chat_template,")
        print("      and verify single-turn prompts are unchanged.")
    else:
        print("PREFLIGHT PASSED for the required checks.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
