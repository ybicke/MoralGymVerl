"""Unit tests for behavioral.py's CLI layer: build_metadata + apply_overrides.

The load-bearing invariant (stated in build_metadata's docstring): metadata
is built AFTER the expensive eval, so any config evaluate() tolerated must
never raise in build_metadata and lose the finished run. The minimal-config
test here goes red if build_metadata gains a bare cfg[...] access to an
optional section.
"""

import copy
import json

import moralgym_verl.eval.behavioral as behavioral

# The minimum evaluate() itself requires: no evaluation/prompt/teacher
# blocks, no seed, no experiment_name. Everything build_metadata reads
# beyond this must fall back to a default.
MINIMAL_CFG = {
    "policy": {"model_name": "google/gemma-2-9b-it"},
    "game": {"type": "prisoners_dilemma",
             "payoffs": {"T": 4, "R": 3, "P": 1, "S": 0},
             "num_rounds": 1},
    "reward": {"lambda": 0.0, "intrinsic": "deontological"},
}


def _args(*extra):
    return behavioral.build_parser().parse_args(
        ["--config", "unused.yaml", "--checkpoint", "base", *extra])


def test_minimal_config_never_raises_and_defaults():
    md = behavioral.build_metadata(copy.deepcopy(MINIMAL_CFG), _args(), None)
    assert md["experiment_name"] == "base_gemma2_9b"
    assert md["model_type"] == "base"
    assert md["protocol"] == "custom"
    assert md["moral_value"] == "none"
    assert md["teacher_template_source"] is None
    assert md["eval_seed"] == 42
    assert md["training_seed"] is None
    assert md["num_episodes"] == 20
    assert md["game_reward"] == "raw"
    assert md["state_design"] == "balanced"
    assert all(v == "fixed" for v in md["eval_presentation"].values())
    json.dumps(md)   # the block is written verbatim into behavioral.json


def test_moral_value_suffix_and_template_source():
    cfg = copy.deepcopy(MINIMAL_CFG)
    cfg["teacher"] = {"moral_value": "deon_no_exploit",
                     "template_source": "configs/verl/sdpo_pd_tft.yaml"}
    md = behavioral.build_metadata(cfg, _args(), None)
    assert md["experiment_name"] == "base_gemma2_9b__mv_deon_no_exploit"
    assert md["teacher_template_source"] == "configs/verl/sdpo_pd_tft.yaml"


def test_finetuned_checkpoint_and_training_seed():
    cfg = copy.deepcopy(MINIMAL_CFG)
    cfg["experiment_name"] = "t2_hist_robust_v2"
    ckpt = "checkpoints/t2_seed7_lr5x/adapter"
    md = behavioral.build_metadata(
        cfg, _args("--checkpoint", ckpt), checkpoint=ckpt)
    assert md["experiment_name"] == "t2_hist_robust_v2"
    assert md["model_type"] == "finetuned"
    assert md["checkpoint"] == ckpt
    assert md["training_seed"] == 7


def test_apply_overrides_table_and_game():
    cfg = copy.deepcopy(MINIMAL_CFG)
    args = _args("--game", "stag_hunt", "--opponent", "random",
                 "--num-episodes", "50", "--moral-value", "consequentialist")
    behavioral.apply_overrides(cfg, args)
    assert cfg["game"]["type"] == "stag_hunt"
    assert cfg["game"]["payoffs"] == dict(
        behavioral.FIXED_PAYOFFS["stag_hunt"])
    assert cfg["evaluation"]["opponents"] == ["random"]
    assert cfg["evaluation"]["num_episodes"] == 50
    assert cfg["teacher"]["moral_value"] == "consequentialist"
    # Flags left at None must not touch the config.
    assert "temperature" not in cfg["evaluation"]


def test_model_override_reaches_config_and_metadata():
    """--model lets one eval yaml screen several base models as a sweep
    axis; the recorded base_model/experiment_name must follow the override,
    not the yaml, or a two-model group is unattributable after the fact."""
    cfg = copy.deepcopy(MINIMAL_CFG)
    args = _args("--model", "Qwen/Qwen2.5-7B-Instruct")
    behavioral.apply_overrides(cfg, args)
    assert cfg["policy"]["model_name"] == "Qwen/Qwen2.5-7B-Instruct"
    md = behavioral.build_metadata(cfg, args, None)
    assert md["base_model"] == "Qwen/Qwen2.5-7B-Instruct"
    assert md["experiment_name"] == "base_qwen2.5_7b_instruct"


def test_no_model_flag_leaves_the_config_model():
    cfg = copy.deepcopy(MINIMAL_CFG)
    behavioral.apply_overrides(cfg, _args())
    assert cfg["policy"]["model_name"] == "google/gemma-2-9b-it"


def test_presentation_spec_writes_every_axis_explicitly():
    """A spec must fully determine the presentation block: unnamed axes are
    written 'fixed', never left to whatever the loaded config declared."""
    cfg = copy.deepcopy(MINIMAL_CFG)
    cfg["evaluation"] = {"labels": "randomize", "payoffs": "sample"}
    behavioral.apply_overrides(cfg, _args("--presentation", "role+layout"))
    assert cfg["evaluation"] == {
        "labels": "fixed", "layout": "randomize", "label_order": "fixed",
        "role": "randomize", "payoffs": "fixed",
    }


def test_presentation_all_and_fixed():
    cfg = copy.deepcopy(MINIMAL_CFG)
    behavioral.apply_overrides(cfg, _args("--presentation", "full_randomization"))
    assert cfg["evaluation"]["payoffs"] == "sample"        # not "randomize"
    assert cfg["evaluation"]["role"] == "randomize"
    cfg2 = copy.deepcopy(MINIMAL_CFG)
    behavioral.apply_overrides(cfg2, _args("--presentation", "fixed_representation"))
    assert set(cfg2["evaluation"].values()) == {"fixed"}


def test_explicit_eval_flag_overrides_the_presentation_spec():
    """Precedence mirrors protocol: preset first, explicit flag wins."""
    cfg = copy.deepcopy(MINIMAL_CFG)
    behavioral.apply_overrides(
        cfg, _args("--presentation", "full_randomization", "--eval-role", "fixed"))
    assert cfg["evaluation"]["role"] == "fixed"
    assert cfg["evaluation"]["labels"] == "randomize"


def test_presentation_spec_recorded_in_metadata():
    cfg = copy.deepcopy(MINIMAL_CFG)
    args = _args("--presentation", "labels+payoffs")
    behavioral.apply_overrides(cfg, args)
    md = behavioral.build_metadata(cfg, args, None)
    assert md["presentation_spec"] == "labels+payoffs"
    assert md["eval_presentation"]["labels"] == "randomize"
    assert md["eval_presentation"]["payoffs"] == "sample"
    assert md["eval_presentation"]["role"] == "fixed"
