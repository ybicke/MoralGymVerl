"""Unit tests for generalized_jsd — the probe's mirror of SDPO's loss.

Expected values are HAND-COMPUTED from the loss definition in
SDPO/verl/trainer/ppo/core_algos.py::compute_self_distillation_loss
(alpha=0 -> KL(t||s), alpha=1 -> KL(s||t), else mixture GJS) — never from
code output. Runs on the login node (torch, no GPU/model).
"""

import math

import pytest
import torch

from moralgym_verl.eval.teacher_forcing import generalized_jsd, two_way_jsd


def _logp(*rows):
    return torch.log(torch.tensor(rows, dtype=torch.float64))


S = _logp([0.75, 0.25])          # student
T = _logp([0.25, 0.75])          # teacher
# alpha=0.5 mixture m = (0.5, 0.5);
# KL(s||m) = 0.75*ln(1.5) + 0.25*ln(0.5) = KL(t||m) by symmetry.
JSD_HALF = 0.75 * math.log(1.5) + 0.25 * math.log(0.5)


def test_identical_distributions_zero():
    for alpha in (0.0, 0.5, 1.0):
        assert generalized_jsd(S, S, alpha).item() == pytest.approx(0.0, abs=1e-12)


def test_alpha_half_hand_computed_and_symmetric():
    assert generalized_jsd(S, T, 0.5).item() == pytest.approx(JSD_HALF)
    assert generalized_jsd(T, S, 0.5).item() == pytest.approx(JSD_HALF)
    # Bounded by ln 2 for alpha=0.5.
    assert 0.0 < JSD_HALF < math.log(2)


def test_alpha_endpoints_are_plain_kls():
    # Mirrors the code's special-cased branches, NOT the mixture limit
    # (which would be 0 at both endpoints).
    kl_ts = (0.25 * math.log(0.25 / 0.75) + 0.75 * math.log(0.75 / 0.25))
    assert generalized_jsd(S, T, 0.0).item() == pytest.approx(kl_ts)
    assert generalized_jsd(S, T, 1.0).item() == pytest.approx(kl_ts)


def test_matches_two_way_jsd_on_two_way_case():
    # alpha=0.5 over a 2-vocab must equal the probe-A label-margin JSD.
    got = generalized_jsd(S, T, 0.5).item()
    expected = two_way_jsd(
        math.log(0.75), math.log(0.25), math.log(0.25), math.log(0.75))
    assert got == pytest.approx(expected)


def test_per_position_shape():
    logp_s = _logp([0.75, 0.25], [0.5, 0.5])
    logp_t = _logp([0.25, 0.75], [0.5, 0.5])
    out = generalized_jsd(logp_s, logp_t, 0.5)
    assert out.shape == (2,)
    assert out[0].item() == pytest.approx(JSD_HALF)
    assert out[1].item() == 0.0
