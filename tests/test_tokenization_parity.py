"""Training-parity guarantee for eval tokenization (the double-BOS guard).

verl tokenizes chat prompts via apply_chat_template(tokenize=True)
(agent_loop.py rollouts, ray_trainer.py SDPO teacher pass), which adds no
special tokens beyond the template's own <bos>. The eval renders the
template to text first (raw-response logging needs the string), so it must
re-tokenize with add_special_tokens=False — the HF default would prepend a
second <bos> (gemma-2-9b-it: ids [2, 2, 106, ...]).

Needs the tokenizers from the local HF cache ($HF_HOME); each model skips
when unavailable.

Two different invariants here, deliberately not merged:
  - test_matches_training_tokenization is family-agnostic (eval must
    tokenize exactly as training does) and runs for every screened model.
  - test_single_bos is the gemma-specific instance of it: gemma's template
    emits <bos> itself, so the HF default would double it. Qwen2.5 has no
    BOS at all, so asserting one there would be wrong, not stricter.
"""

import pytest

transformers = pytest.importorskip("transformers")
pytest.importorskip("torch")

from moralgym_verl.eval.generation import render_chat_inputs

MODEL = "google/gemma-2-9b-it"
# Every base model the single-turn screen can run. A second family is where
# the render-then-re-tokenize path is most likely to drift, since each has
# its own template and its own special-token conventions.
SCREENED_MODELS = [
    "google/gemma-2-9b-it",
    "Qwen/Qwen2.5-7B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.2",
]

SINGLE_TURN = [{"role": "user", "content": "Choose action3 or action4."}]
MULTI_TURN = [
    {"role": "user", "content": "Round 1: choose action3 or action4."},
    {"role": "assistant", "content": "Action: action3"},
    {"role": "user", "content": "Round 2: choose action3 or action4."},
]


@pytest.fixture(scope="module")
def tokenizer():
    try:
        return transformers.AutoTokenizer.from_pretrained(
            MODEL, local_files_only=True,
        )
    except Exception as exc:
        pytest.skip(f"{MODEL} tokenizer not in local HF cache: {exc}")


def _training_ids(tokenizer, messages):
    """Token ids the way verl produces them: apply_chat_template(tokenize=True)."""
    out = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True, return_dict=True,
    )
    ids = out["input_ids"]
    if hasattr(ids[0], "ids"):        # transformers >=5 returns Encoding
        return list(ids[0].ids)
    if isinstance(ids[0], list):      # batched list
        return list(ids[0])
    return list(ids)


@pytest.mark.parametrize("messages", [SINGLE_TURN, MULTI_TURN])
def test_single_bos(tokenizer, messages):
    _, inputs = render_chat_inputs(tokenizer, messages, "cpu")
    ids = inputs.input_ids[0].tolist()
    assert ids[0] == tokenizer.bos_token_id
    assert ids[1] != tokenizer.bos_token_id


@pytest.mark.parametrize("messages", [SINGLE_TURN, MULTI_TURN])
def test_matches_training_tokenization(tokenizer, messages):
    _, inputs = render_chat_inputs(tokenizer, messages, "cpu")
    assert inputs.input_ids[0].tolist() == _training_ids(tokenizer, messages)


@pytest.mark.parametrize("model_id", SCREENED_MODELS)
@pytest.mark.parametrize("messages", [SINGLE_TURN, MULTI_TURN])
def test_matches_training_tokenization_every_screened_model(model_id, messages):
    """The parity invariant for each base model the screen can select.

    render_chat_inputs is family-agnostic by construction (it uses the
    tokenizer's own template and never adds special tokens on top), so this
    is a regression guard: it goes red if a model's template makes
    render-then-re-tokenize diverge from training's single-shot tokenize.
    """
    try:
        tok = transformers.AutoTokenizer.from_pretrained(
            model_id, local_files_only=True,
        )
    except Exception as exc:
        pytest.skip(f"{model_id} tokenizer not in local HF cache: {exc}")
    _, inputs = render_chat_inputs(tok, messages, "cpu")
    assert inputs.input_ids[0].tolist() == _training_ids(tok, messages)
