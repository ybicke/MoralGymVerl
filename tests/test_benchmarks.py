"""Benchmark spec identity, validation, cell naming and lm-eval argv."""

import pytest
import yaml

from moralgym_verl.eval import benchmarks as b

HARNESS = {
    "model_name": "Qwen/Qwen3-8B",
    "vllm": {"dtype": "bfloat16", "enable_thinking": False},
    "max_lora_rank": 32,
    "benchmarks": {
        "gsm8k": {"tasks": "gsm8k_cot_llama", "num_fewshot": 8,
                  "apply_chat_template": True, "fewshot_as_multiturn": True,
                  "gen_kwargs": "max_gen_toks=1024"},
        "mmlu": {"tasks": "mmlu", "num_fewshot": 5,
                 "apply_chat_template": False},
    },
}


def _spec_file(tmp_path, body, model="qwen3_8b", family="retention",
               experiment="core", root="benchmarks"):
    path = tmp_path / "configs" / "eval" / root / model / family / f"{experiment}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body))
    return str(path)


def test_identity_from_path(tmp_path):
    path = _spec_file(tmp_path, {"checkpoints": ["base"]})
    assert b.spec_identity(path) == ("qwen3_8b", "retention", "core")


@pytest.mark.parametrize("kw", [{"root": "transfer"}, {"model": "qwen9"},
                                {"family": "pgg"}])
def test_identity_rejects_misplaced_spec(tmp_path, kw):
    with pytest.raises(ValueError):
        b.spec_identity(_spec_file(tmp_path, {"checkpoints": ["base"]}, **kw))


def test_load_spec_derives_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "harness_config", lambda m, f: f"configs/eval/harness/{m}/{f}.yaml")
    spec = b.load_spec(_spec_file(tmp_path, {"checkpoints": [
        "base", "qwen3_8b_grpo_pd_deon_tft_200/global_step_180"]}))
    assert spec["eval_group"] == "qwen3_8b/retention/core"
    assert spec["results_dir"] == "benchmarks"
    assert spec["benchmarks"] == ["gsm8k", "mmlu"]
    assert spec["config"] == "configs/eval/harness/qwen3_8b/retention.yaml"


@pytest.mark.parametrize("body", [
    {"checkpoints": []},
    {"checkpoints": ["base", "base"]},
    {"checkpoints": ["gemma2_9b_sdpo_pd_deon_tft_200/global_step_60"]},
    {"checkpoints": ["base"], "benchmarks": ["mask"]},
    {"checkpoints": ["base"], "name": "x"},
])
def test_load_spec_rejects(tmp_path, body, monkeypatch):
    monkeypatch.setattr(b, "harness_config", lambda m, f: "h.yaml")
    with pytest.raises(ValueError):
        b.load_spec(_spec_file(tmp_path, body))


def test_run_dir_stem_flattens_path():
    cell = {"checkpoint": "qwen3_8b_grpo_pd_deon_tft_200/global_step_180"}
    assert b.run_dir_stem(cell) == "qwen3_8b_grpo_pd_deon_tft_200-global_step_180"
    assert b.run_dir_stem({"checkpoint": "base"}) == "base"


def test_batch_payload_carries_identity():
    spec = {"eval_group": "qwen3_8b/retention/core", "results_dir": "benchmarks",
            "benchmarks": ["gsm8k"], "config": "h.yaml"}
    cell = b.batch_payload(spec, [{"checkpoint": "base"}])["cells"][0]
    assert cell == {"eval_group": "qwen3_8b/retention/core",
                    "results_dir": "benchmarks", "run_dir_stem": "base",
                    "checkpoint": "base", "benchmarks": ["gsm8k"],
                    "config": "h.yaml"}


def test_resolve_checkpoint(tmp_path):
    run = tmp_path / "run" / "global_step_10" / "actor"
    (run / "lora_adapter").mkdir(parents=True)
    assert b.resolve_checkpoint("base", str(tmp_path)) == ("base", "")
    with pytest.raises(FileNotFoundError):
        b.resolve_checkpoint("run/global_step_10", str(tmp_path))
    (run / "lora_adapter" / "adapter_config.json").write_text("{}")
    assert b.resolve_checkpoint("run/global_step_10", str(tmp_path)) == (
        "adapter", str(run / "lora_adapter"))


def test_lm_eval_command_base_vs_adapter():
    base = b.lm_eval_command(HARNESS, "gsm8k", "base", "", "out")
    assert base[:5] == ["python3", "-m", "lm_eval", "--model", "vllm"]
    margs = base[base.index("--model_args") + 1]
    assert margs == "pretrained=Qwen/Qwen3-8B,dtype=bfloat16,enable_thinking=False"
    assert "--apply_chat_template" in base and "--fewshot_as_multiturn" in base
    assert base[base.index("--gen_kwargs") + 1] == "max_gen_toks=1024"
    assert base[base.index("--num_fewshot") + 1] == "8"

    ad = b.lm_eval_command(HARNESS, "mmlu", "adapter", "/ck/lora_adapter", "out")
    margs = ad[ad.index("--model_args") + 1]
    assert margs.endswith(",lora_local_path=/ck/lora_adapter,max_lora_rank=32")
    assert "--apply_chat_template" not in ad and "--gen_kwargs" not in ad

    full = b.lm_eval_command(HARNESS, "mmlu", "weights", "/ck/huggingface", "out")
    assert "pretrained=/ck/huggingface" in full[full.index("--model_args") + 1]


def test_load_harness_rejects_unknown_flag(tmp_path):
    bad = dict(HARNESS, benchmarks={"gsm8k": {"tasks": "x", "temperature": 0.7}})
    path = tmp_path / "h.yaml"
    path.write_text(yaml.safe_dump(bad))
    with pytest.raises(ValueError):
        b.load_harness(str(path))
