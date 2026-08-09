"""Sweep spec validation, grid expansion, and submission mapping."""

import pytest
import yaml

from moralgym_verl.eval.sweep import (
    cell_submission, expand_cells, load_sweep,
)

SPEC = {
    "name": "unit_sweep",
    "eval_group": "unit_group",
    "num_episodes": 50,
    "axes": {
        "game": ["prisoners_dilemma"],
        "moral_value": ["none", "deontological"],
        "representation": ["matrix", "prose"],
        "protocol": ["single_round"],
    },
    "env": {"RUN_PROBES": "on"},
    "extra_args": ["--temperature", "0.7"],
}


def test_load_sweep_validates(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump({"name": "x", "axes": {"game": ["pd"]}}))
    with pytest.raises(ValueError, match="moral_value"):
        load_sweep(str(path))
    path.write_text(yaml.safe_dump({"name": "x"}))
    with pytest.raises(ValueError, match="axes"):
        load_sweep(str(path))


def test_load_sweep_defaults_group_to_name(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump(
        {"name": "x", "axes": {"game": ["pd"], "moral_value": ["none"],
                               "protocol": ["single_round"]}}))
    assert load_sweep(str(path))["eval_group"] == "x"


def test_protocol_axis_is_mandatory(tmp_path):
    """Phase separation: without a declared protocol the cells would inherit
    the eval yaml's turn structure (the configs are 5-round), so a sweep meant
    to be single-turn would silently be multi-round."""
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump(
        {"name": "x", "axes": {"game": ["pd"], "moral_value": ["none"]}}))
    with pytest.raises(ValueError, match="protocol"):
        load_sweep(str(path))


def test_load_sweep_rejects_unknown_protocol(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump(
        {"name": "x", "axes": {"game": ["pd"], "moral_value": ["none"],
                               "protocol": ["multi_round_conversation"]}}))
    with pytest.raises(ValueError, match="unknown protocol"):
        load_sweep(str(path))


def test_expand_cells_cartesian():
    cells = expand_cells(SPEC)
    assert len(cells) == 1 * 2 * 2 * 1
    assert cells[0] == {"game": "prisoners_dilemma", "moral_value": "none",
                        "representation": "matrix", "protocol": "single_round"}
    assert len({tuple(c.items()) for c in cells}) == 4


def test_probes_run_only_in_first_protocol_cells():
    spec = {**SPEC, "axes": {**SPEC["axes"],
                             "protocol": ["single_round", "multi_round"]}}
    by_protocol = {c["protocol"]: cell_submission(spec, c)[0]
                   for c in expand_cells(spec) if c["moral_value"] == "none"
                   and c["representation"] == "matrix"}
    assert by_protocol["single_round"].get("RUN_PROBES") == "on"
    assert by_protocol["multi_round"].get("RUN_PROBES") == "off"


def test_cell_submission_mapping():
    env, argv = cell_submission(SPEC, expand_cells(SPEC)[3])
    assert argv[:5] == ["sbatch", "scripts/slurm/eval_teacher_signal.sh",
                        "prisoners_dilemma", "deontological", "50"]
    assert env["EVAL_GROUP"] == "unit_group"
    assert env["RUN_PROBES"] == "on"
    assert env["REPRESENTATION"] == "prose"      # env axis, reaches probes
    assert "--representation" not in argv
    assert argv[argv.index("--protocol") + 1] == "single_round"
    assert argv[-2:] == ["--temperature", "0.7"]
