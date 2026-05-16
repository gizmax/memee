"""Tests for the v2.4.3 Beta calibration (Tier 1.7).

3-parameter calibrator from Kull, Filho, Flach (AISTATS 2017). The
non-parametric isotonic curve already shipped in
``engine/calibration.py``; the Beta variant fills the small-n hole
where pool-adjacent-violators overfits to bin boundaries.

Tests pin:
  * Identity behaviour at the default (a=1, b=1, c=0) and on degenerate
    inputs (all-zero / all-one labels).
  * Monotonicity: the fitted calibrator is non-decreasing on a
    monotonic outcome distribution.
  * Correctness: on a synthetic miscalibrated dataset, the post-fit
    calibrator's Brier score is strictly better than the raw probs.
  * Registry routing: ``fit_curves`` picks BetaCurve below the
    n-threshold and IsotonicCurve above. Round-trip through
    ``to_dict`` / ``from_dict`` preserves the curve type.
"""

from __future__ import annotations

import random

import pytest

from memee.engine.calibration import (
    BetaCurve,
    IsotonicCurve,
    CurveRegistry,
    brier_score,
    fit_beta_calibration,
    fit_curves,
    _curve_from_dict,
)


# ── Identity / degenerate ─────────────────────────────────────────


def test_default_beta_curve_is_near_identity():
    """Beta(a=1, b=1, c=0) is the sigmoid of log(p) + log(1/(1-p)) =
    logit(p), which IS the identity in probability space."""
    c = BetaCurve()  # defaults a=1, b=1, c=0
    for x in (0.1, 0.3, 0.5, 0.7, 0.9):
        assert c.predict(x) == pytest.approx(x, abs=1e-4)


def test_fit_returns_identity_on_all_zero_labels():
    """All-zero outcomes — sigmoid can't separate, fit must return
    identity rather than crashing or drifting."""
    pairs = [(p, 0) for p in (0.1, 0.3, 0.5, 0.7, 0.9)]
    c = fit_beta_calibration(pairs)
    assert (c.a, c.b, c.c) == (1.0, 1.0, 0.0)


def test_fit_returns_identity_on_all_one_labels():
    pairs = [(p, 1) for p in (0.1, 0.3, 0.5, 0.7, 0.9)]
    c = fit_beta_calibration(pairs)
    assert (c.a, c.b, c.c) == (1.0, 1.0, 0.0)


def test_fit_returns_identity_on_empty_input():
    c = fit_beta_calibration([])
    assert (c.a, c.b, c.c) == (1.0, 1.0, 0.0)
    assert c.n_train == 0


def test_predict_clamps_to_open_unit_interval():
    """``log(0)`` and ``log(1-1)`` are −∞; the predictor must clamp x
    to (eps, 1-eps) before feeding the sigmoid so callers don't have
    to."""
    c = BetaCurve()
    # Anything finite — no NaN, no ±inf.
    for x in (0.0, 1.0, -0.5, 1.5):
        out = c.predict(x)
        assert 0.0 < out < 1.0
        assert out == out  # not NaN


# ── Monotonicity ─────────────────────────────────────────────────


def test_fitted_curve_is_non_decreasing():
    """The Beta family is parametric and necessarily monotonic in p
    for non-negative a, b. We don't constrain a/b ≥ 0 in the fit
    (Newton-Raphson can step into negative territory), but on a
    monotonic outcome distribution the optimum lives in the
    non-negative corner so the result should still be non-decreasing
    on (0, 1)."""
    random.seed(42)
    # Generate (predicted_prob, outcome) pairs where outcome is more
    # likely as predicted prob rises — the standard "well-shaped" data.
    pairs = []
    for _ in range(200):
        p = random.random()
        outcome = 1 if random.random() < p else 0
        pairs.append((p, outcome))

    c = fit_beta_calibration(pairs)
    xs = [0.05, 0.15, 0.30, 0.50, 0.70, 0.85, 0.95]
    ys = [c.predict(x) for x in xs]
    for i in range(len(ys) - 1):
        assert ys[i] <= ys[i + 1] + 1e-6, (
            f"calibrator not monotonic at x={xs[i]}→{xs[i + 1]}: {ys[i]}→{ys[i + 1]}"
        )


# ── Correctness on synthetic miscalibration ──────────────────────


def test_calibration_improves_brier_on_overconfident_predictor():
    """Synthetic dataset: predictor always reports p=0.9 but actual
    base rate is 0.6. Raw Brier ≈ (0.9 - 0.6)² + variance = 0.09+. The
    fitted Beta calibrator should pull predictions toward 0.6 and
    drop the Brier accordingly."""
    random.seed(42)
    # Build a dataset where outcomes are noisy and the predictor's
    # raw score is biased high. With monotonic outcome rate we want
    # the calibrator to shrink high predictions toward the realised
    # frequency.
    pairs = []
    for _ in range(400):
        p_raw = random.uniform(0.6, 0.95)
        # True probability is lower than reported — predictor is
        # overconfident.
        p_true = max(0.0, p_raw - 0.25)
        outcome = 1 if random.random() < p_true else 0
        pairs.append((p_raw, outcome))

    curve = fit_beta_calibration(pairs)
    raw_brier = brier_score([p for p, _ in pairs], [o for _, o in pairs])
    cal_brier = brier_score(
        [curve.predict(p) for p, _ in pairs],
        [o for _, o in pairs],
    )
    assert cal_brier < raw_brier - 0.01, (
        f"calibration didn't improve Brier: raw={raw_brier:.3f} "
        f"calibrated={cal_brier:.3f}"
    )


# ── Registry routing ─────────────────────────────────────────────


def test_fit_curves_picks_beta_for_small_n():
    """Below the n threshold (~500), the global curve is a BetaCurve."""
    random.seed(0)
    records = [
        {
            "prediction": random.random(),
            "outcome": random.randint(0, 1),
            "memory_type": "pattern",
            "scope": "personal",
            "source_type": "human",
        }
        for _ in range(100)
    ]
    registry = fit_curves(records)
    assert isinstance(registry.global_curve, BetaCurve)


def test_fit_curves_picks_isotonic_for_large_n():
    """At or above the threshold, isotonic earns its complexity."""
    random.seed(0)
    records = [
        {
            "prediction": random.random(),
            "outcome": random.randint(0, 1),
            "memory_type": "pattern",
            "scope": "personal",
            "source_type": "human",
        }
        for _ in range(600)
    ]
    registry = fit_curves(records)
    assert isinstance(registry.global_curve, IsotonicCurve)


def test_to_dict_round_trip_preserves_beta_type():
    """BetaCurve.to_dict + _curve_from_dict round-trips correctly,
    distinguishable from legacy IsotonicCurve dicts by the
    ``kind: "beta"`` marker."""
    original = BetaCurve(a=1.5, b=0.8, c=0.3, n_train=42, slice_key="test")
    restored = _curve_from_dict(original.to_dict())
    assert isinstance(restored, BetaCurve)
    assert restored.a == pytest.approx(1.5)
    assert restored.b == pytest.approx(0.8)
    assert restored.c == pytest.approx(0.3)
    assert restored.n_train == 42
    assert restored.slice_key == "test"


def test_registry_to_dict_round_trip_mixed_curves():
    """A registry with both Beta and Isotonic curves serialises and
    restores correctly."""
    beta_curve = BetaCurve(a=1.2, n_train=100)
    iso_curve = IsotonicCurve(xs=[0.1, 0.5, 0.9], ys=[0.2, 0.5, 0.8], n_train=600)
    registry = CurveRegistry(
        global_curve=beta_curve,
        by_slice={"pattern::personal::human": iso_curve},
    )
    restored = CurveRegistry.from_dict(registry.to_dict())
    assert isinstance(restored.global_curve, BetaCurve)
    assert isinstance(restored.by_slice["pattern::personal::human"], IsotonicCurve)


def test_legacy_isotonic_dict_without_kind_marker_loads_as_isotonic():
    """Forward compatibility: dicts persisted by pre-v2.4.3 Memee have
    no ``kind`` field but are unambiguously isotonic (xs/ys present)."""
    legacy = {"xs": [0.1, 0.5], "ys": [0.2, 0.6], "n_train": 100}
    curve = _curve_from_dict(legacy)
    assert isinstance(curve, IsotonicCurve)
    assert curve.xs == [0.1, 0.5]
