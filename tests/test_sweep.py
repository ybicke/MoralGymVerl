"""Sweep spec validation, grid expansion, and submission mapping."""

import pytest
import yaml

from moralgym_verl.eval.sweep import (
    batch_payload, cell_submission, expand_cells, load_sweep, pack_batches,
    run_dir_stem,
)

# An already-loaded spec, as load_sweep returns it (identity fields derived).
SPEC = {
    "name": "unit_sweep",
    "eval_group": "unit_group",
    "results_dir": "teacher_signal",
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

# Minimal valid axes for a spec file.
AXES = {"game": ["prisoners_dilemma"], "moral_value": ["none"],
        "protocol": ["single_round"]}


def _spec_file(tmp_path, body, root="teacher_signal", subject="qwen3_8b",
               family="classic", experiment="s"):
    """Write a sweep spec where the naming contract requires it,
    configs/eval/<root>/<subject>/<family>/<experiment>.yaml, because the
    path is the spec's identity and load_sweep refuses any other location."""
    path = tmp_path / "configs" / "eval" / root / subject / family / f"{experiment}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body))
    return str(path)


def test_load_sweep_validates(tmp_path):
    path = _spec_file(tmp_path, {"axes": {"game": ["prisoners_dilemma"]}})
    with pytest.raises(ValueError, match="moral_value"):
        load_sweep(path)
    path = _spec_file(tmp_path, {}, experiment="t")
    with pytest.raises(ValueError, match="axes"):
        load_sweep(path)


def test_load_sweep_derives_identity_from_path(tmp_path):
    spec = load_sweep(_spec_file(tmp_path, {"axes": AXES}))
    assert spec["eval_group"] == "qwen3_8b/classic/s"
    assert spec["results_dir"] == "teacher_signal"
    assert spec["config"] == "configs/eval/harness/qwen3_8b/classic.yaml"


def test_load_sweep_rejects_declared_identity(tmp_path):
    """Identity comes from the path; a spec that declares it could point
    its results somewhere its config does not live."""
    path = _spec_file(tmp_path, {"name": "x", "axes": AXES})
    with pytest.raises(ValueError, match="declares 'name'"):
        load_sweep(path)


def test_load_sweep_rejects_misplaced_spec(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump({"axes": AXES}))
    with pytest.raises(ValueError, match="must live at configs/eval"):
        load_sweep(str(path))


def test_protocol_axis_is_mandatory(tmp_path):
    """Phase separation: without a declared protocol the cells would inherit
    the eval yaml's turn structure (the configs are 5-round), so a sweep meant
    to be single-turn would silently be multi-round."""
    path = _spec_file(tmp_path, {"axes": {"game": ["prisoners_dilemma"],
                                          "moral_value": ["none"]}})
    with pytest.raises(ValueError, match="must include protocol"):
        load_sweep(path)


def test_load_sweep_rejects_unknown_protocol(tmp_path):
    path = _spec_file(tmp_path, {"axes": {**AXES,
                                          "protocol": ["multi_round_conversation"]}})
    with pytest.raises(ValueError, match="unknown protocol"):
        load_sweep(path)


def test_game_axis_must_stay_in_family(tmp_path):
    path = _spec_file(tmp_path, {"axes": {**AXES, "game": ["public_goods"]}})
    with pytest.raises(ValueError, match="not in family"):
        load_sweep(path)


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
    assert env["RESULTS_DIR"] == "teacher_signal"
    assert env["RUN_PROBES"] == "on"
    assert env["REPRESENTATION"] == "prose"      # env axis, reaches probes
    assert "--representation" not in argv
    assert argv[-2:] == ["--temperature", "0.7"]


def test_cell_defining_axes_travel_by_env_not_flags():
    """Trailing flags reach behavioral only (the launcher passes "${@:4}" to
    it alone), so any axis that defines what the cell IS must go by env or
    the probes silently keep the eval yaml's value."""
    spec = {**SPEC, "axes": {**SPEC["axes"],
                             "model": ["google/gemma-2-9b-it"]}}
    env, argv = cell_submission(spec, expand_cells(spec)[0])
    assert env["MODEL"] == "google/gemma-2-9b-it"
    assert env["PROTOCOL"] == "single_round"
    assert env["REPRESENTATION"] == "matrix"
    for flag in ("--model", "--protocol", "--representation"):
        assert flag not in argv


def test_model_axis_expands_the_grid():
    spec = {**SPEC, "axes": {**SPEC["axes"],
                             "model": ["google/gemma-2-9b-it",
                                       "Qwen/Qwen2.5-7B-Instruct"]}}
    cells = expand_cells(spec)
    assert len(cells) == 1 * 2 * 2 * 1 * 2
    assert len({(c["model"], c["moral_value"], c["representation"])
                for c in cells}) == 8


def test_episode_probe_rejected_in_a_single_round_sweep(tmp_path):
    """The episode probe measures per-round teacher decay; at num_rounds=1
    there is no curve, so a one-point 'decay' file must never be produced."""
    path = _spec_file(tmp_path, {"axes": AXES,
                                 "env": {"RUN_PROBE_B_EPISODE": "on"}})
    with pytest.raises(ValueError, match="single-round"):
        load_sweep(path)


def test_episode_probe_allowed_under_multi_round(tmp_path):
    path = _spec_file(tmp_path, {"axes": {**AXES, "protocol": ["multi_round"]},
                                 "env": {"RUN_PROBE_B_EPISODE": "on"}})
    assert load_sweep(path)["axes"]["protocol"] == ["multi_round"]


def test_presentation_spec_validated_at_submit(tmp_path):
    """As a forwarded flag a typo would only surface once the cell is on a
    GPU; load_sweep catches it while the sweep is still on the login node."""
    path = _spec_file(tmp_path, {"axes": {**AXES, "presentation":
                                          ["fixed_representation", "lables+role"]}})
    with pytest.raises(ValueError, match="unknown presentation spec"):
        load_sweep(path)


def test_presentation_spec_accepts_composites_and_keywords(tmp_path):
    path = _spec_file(tmp_path, {"axes": {**AXES, "presentation":
                                          ["fixed_representation",
                                           "surface_randomization",
                                           "labels+layout+role"]}})
    assert len(load_sweep(path)["axes"]["presentation"]) == 3


def test_presentation_is_a_forwarded_flag_not_env():
    """Probes force fixed presentation by design, so this axis must NOT
    travel by env; behavioral-only is the correct reach."""
    spec = {**SPEC, "axes": {**SPEC["axes"],
                             "presentation": ["surface_randomization"]}}
    env, argv = cell_submission(spec, expand_cells(spec)[0])
    assert "PRESENTATION" not in env
    assert argv[argv.index("--presentation") + 1] == "surface_randomization"


# --- packing: 4 cells per node ------------------------------------------
# Clariden is OverSubscribe=EXCLUSIVE, so a 1-GPU job is billed for all 4
# GPUs of the node. Packing exists to stop paying 4x (and taking the
# matching fairshare hit) for GPUs that sit idle.

def test_run_dir_stem_is_unique_per_cell():
    """Under packing four cells share one SLURM_JOB_ID, so the stem — not
    the job id — is what keeps them in separate directories. Cells are a
    cartesian product, so every stem must differ."""
    spec = {**SPEC, "axes": {**SPEC["axes"],
                             "presentation": ["fixed_representation",
                                              "surface_randomization"]}}
    cells = expand_cells(spec)
    stems = [run_dir_stem(c) for c in cells]
    assert len(set(stems)) == len(cells)


def test_run_dir_stem_separates_cells_sharing_game_and_value():
    """The exact collision packing would otherwise cause: same game and
    moral value, different representation."""
    a = {"game": "pd", "moral_value": "deontological", "representation": "matrix",
         "protocol": "single_round"}
    b = {**a, "representation": "prose"}
    assert run_dir_stem(a) != run_dir_stem(b)


def test_pack_batches_cover_every_cell_exactly_once():
    spec = {**SPEC, "axes": {**SPEC["axes"],
                             "moral_value": ["none", "deontological",
                                             "utilitarian", "virtue"]}}
    cells = expand_cells(spec)
    batches = pack_batches(spec, cells, pack_size=4)
    flat = [c for b in batches for c in b]
    assert len(flat) == len(cells)
    assert {run_dir_stem(c) for c in flat} == {run_dir_stem(c) for c in cells}
    assert all(len(b) <= 4 for b in batches)


def test_pack_batches_group_probe_cells_together():
    """A batch holds the node until its slowest cell ends, so probe cells
    (~85min) must not be mixed with probe-less ones (~60min) except at the
    single ragged boundary."""
    spec = {**SPEC, "axes": {**SPEC["axes"],
                             "moral_value": ["none", "deontological",
                                             "utilitarian", "virtue"]}}
    batches = pack_batches(spec, expand_cells(spec), pack_size=4)
    mixed = [b for b in batches
             if len({c["moral_value"] == "none" for c in b}) > 1]
    assert len(mixed) <= 1


def test_batch_payload_carries_resolved_names_and_env():
    spec = {**SPEC, "axes": {**SPEC["axes"], "model": ["google/gemma-2-9b-it"]}}
    batch = pack_batches(spec, expand_cells(spec), pack_size=4)[0]
    payload = batch_payload(spec, batch)["cells"]
    assert len(payload) == len(batch)
    for entry in payload:
        # The launcher must never re-derive the name; it is resolved here.
        assert entry["run_dir_stem"]
        assert entry["eval_group"] == "unit_group"
        assert entry["env"]["MODEL"] == "google/gemma-2-9b-it"
        assert entry["env"]["PROTOCOL"] == "single_round"
        assert "EVAL_GROUP" not in entry["env"]      # carried per-cell instead
        assert entry["args"][-2:] == ["--temperature", "0.7"]
