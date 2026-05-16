"""R12 P1: Confidence calibration substrate.

Memee treats ``Memory.confidence_score`` as a probability everywhere —
the router boost ``(1 + 0.4 × conf)``, the ranker, the briefing, the
lifecycle invalidation-ratio gate, the CMAM canon-promotion threshold —
but the value is the output of an additive update rule, not a
statistically calibrated probability. The audit explicitly flagged
this; ``confidence.py:1`` itself acknowledges it.

This module is the calibration substrate the codebase was missing.
What it ships:

- **Brier score**, **Expected Calibration Error (ECE, 10 bins)**,
  **Maximum Calibration Error (MCE)** — the standard reliability metrics.
- A reliability-diagram printer (ASCII, no plotting deps).
- Two rescalers: pure-Python piecewise isotonic regression and Bayesian
  Beta-Binomial smoothing. The isotonic curve is fit per
  ``(memory_type, scope, source_type)`` slice when there are ≥ 50
  records in the bucket, falling back to a global fit otherwise.
- ``rescale(memory)`` — opt-in via ``MEMEE_CALIBRATED_CONFIDENCE=1``.
  Production code that reads ``memory.confidence_score`` can switch to
  ``rescale(memory)`` once we trust the curve. Default off.
- A persisted ``calibration_curves`` JSON column on a singleton row in
  ``learning_snapshots`` so curves survive process restarts.

We deliberately keep ``scipy`` optional. Beta-Binomial is closed-form;
isotonic is implemented via the standard pool-adjacent-violators
algorithm in pure Python and runs in O(N log N) on a few thousand
training records — the audit-roadmap target for the calibration cycle.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── Metric primitives ─────────────────────────────────────────────────────


@dataclass
class CalibrationMetrics:
    """Pointwise calibration metrics over a labelled set.

    ``brier`` — mean squared error between predicted prob and outcome.
    ``ece`` — expected calibration error (10 equal-width bins by default).
    ``mce`` — maximum calibration error across the same bins.
    ``n`` — sample size; metrics are unstable below ~50.
    ``per_bin`` — list of (bin_low, bin_high, mean_pred, empirical, count).
    """
    brier: float
    ece: float
    mce: float
    n: int
    per_bin: list[tuple[float, float, float, float, int]] = field(default_factory=list)


def brier_score(predictions: Iterable[float], outcomes: Iterable[int]) -> float:
    """Mean squared error between prediction and binary outcome."""
    p = list(predictions)
    y = list(outcomes)
    if not p:
        return 0.0
    if len(p) != len(y):
        raise ValueError("predictions / outcomes length mismatch")
    return sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(p)


def calibration_metrics(
    predictions: Iterable[float],
    outcomes: Iterable[int],
    n_bins: int = 10,
) -> CalibrationMetrics:
    """Compute Brier + ECE + MCE + per-bin reliability points."""
    p = list(predictions)
    y = list(outcomes)
    n = len(p)
    if n == 0:
        return CalibrationMetrics(0.0, 0.0, 0.0, 0)

    # Bin by predicted probability.
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for pi, yi in zip(p, y):
        idx = min(int(pi * n_bins), n_bins - 1)
        bins[idx].append((pi, yi))

    brier = brier_score(p, y)

    ece = 0.0
    mce = 0.0
    per_bin: list[tuple[float, float, float, float, int]] = []
    for i, bucket in enumerate(bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        if not bucket:
            per_bin.append((lo, hi, 0.0, 0.0, 0))
            continue
        mean_pred = sum(b[0] for b in bucket) / len(bucket)
        empirical = sum(b[1] for b in bucket) / len(bucket)
        gap = abs(mean_pred - empirical)
        ece += gap * (len(bucket) / n)
        if gap > mce:
            mce = gap
        per_bin.append((lo, hi, mean_pred, empirical, len(bucket)))

    return CalibrationMetrics(brier=brier, ece=ece, mce=mce, n=n, per_bin=per_bin)


def reliability_diagram(metrics: CalibrationMetrics, width: int = 40) -> str:
    """Return a Markdown-friendly ASCII reliability diagram. Each row is
    one bin; columns show predicted-bin midpoint vs empirical frequency
    + the count.

    Read like a histogram: a perfectly-calibrated model has empirical ≈
    midpoint for every populated bin.
    """
    if metrics.n == 0:
        return "(no data)"

    lines = [
        f"reliability (n={metrics.n}, Brier={metrics.brier:.4f}, "
        f"ECE={metrics.ece:.4f}, MCE={metrics.mce:.4f})",
        f"{'pred':>6} {'emp':>6} {'cnt':>6}  bar",
    ]
    for lo, hi, mean_pred, empirical, count in metrics.per_bin:
        if count == 0:
            lines.append(f"{(lo + hi) / 2:>6.2f}      —     0  (empty)")
            continue
        bar_len = int(empirical * width)
        bar = "█" * bar_len + "░" * (width - bar_len)
        diff = empirical - mean_pred
        flag = " " if abs(diff) < 0.05 else ("+" if diff > 0 else "-")
        lines.append(
            f"{mean_pred:>6.2f} {empirical:>6.2f} {count:>6} {flag}{bar}"
        )
    return "\n".join(lines)


# ── Rescalers ─────────────────────────────────────────────────────────────


def fit_beta_binomial(
    validations: int,
    invalidations: int,
    *,
    prior_alpha: float = 2.0,
    prior_beta: float = 2.0,
) -> float:
    """Bayesian point estimate ``E[p | data]`` under a Beta(α, β) prior.

    ``Beta(2, 2)`` is the audit roadmap recommendation — gentle pull
    toward 0.5 without flattening strong evidence. Returns the posterior
    mean. Pure closed-form, no dep.
    """
    a = prior_alpha + max(0, validations)
    b = prior_beta + max(0, invalidations)
    return a / (a + b)


@dataclass
class IsotonicCurve:
    """Piecewise-constant isotonic regression curve fit by pool-adjacent-violators.

    Stored as parallel arrays of ``x_breakpoints`` (raw confidence) and
    ``y_predictions`` (calibrated probability). Lookup is binary search.
    """
    xs: list[float]
    ys: list[float]
    n_train: int
    slice_key: str = ""

    def predict(self, x: float) -> float:
        if not self.xs:
            return float(x)
        # Linear interpolation between adjacent breakpoints; clip outside.
        if x <= self.xs[0]:
            return self.ys[0]
        if x >= self.xs[-1]:
            return self.ys[-1]
        # Binary search to avoid O(N) scan; the curve has typically ≤ 10
        # breakpoints after pool merging so the constant matters more
        # than the asymptote, but bisect is cheap.
        import bisect

        idx = bisect.bisect_right(self.xs, x)
        x_lo, x_hi = self.xs[idx - 1], self.xs[idx]
        y_lo, y_hi = self.ys[idx - 1], self.ys[idx]
        if x_hi == x_lo:
            return y_lo
        t = (x - x_lo) / (x_hi - x_lo)
        return y_lo + t * (y_hi - y_lo)

    def to_dict(self) -> dict:
        return {
            "xs": self.xs,
            "ys": self.ys,
            "n_train": self.n_train,
            "slice_key": self.slice_key,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IsotonicCurve":
        return cls(
            xs=list(d["xs"]),
            ys=list(d["ys"]),
            n_train=int(d.get("n_train", 0)),
            slice_key=d.get("slice_key", ""),
        )


def fit_isotonic(
    pairs: Iterable[tuple[float, int]],
    *,
    slice_key: str = "global",
) -> IsotonicCurve:
    """Pure-Python pool-adjacent-violators isotonic regression.

    ``pairs`` is a sequence of ``(predicted_probability, outcome)``
    where outcome is 0 or 1. We sort by prediction, then sweep merging
    adjacent blocks whose mean violates the monotonicity constraint
    until no violations remain. The result is a piecewise-constant
    monotonic mapping from raw → calibrated.

    Returns an empty curve when given < 2 distinct points; the caller
    should fall back to the global curve in that case.
    """
    items = sorted(pairs, key=lambda p: p[0])
    if len(items) < 2:
        return IsotonicCurve(xs=[], ys=[], n_train=len(items), slice_key=slice_key)

    # Each block: [sum_x, sum_y, count]
    blocks: list[list[float]] = []
    for x, y in items:
        blocks.append([float(x), float(y), 1.0])
        # PAV merge while monotonicity is violated.
        while len(blocks) >= 2:
            a = blocks[-2]
            b = blocks[-1]
            mean_a = a[1] / a[2]
            mean_b = b[1] / b[2]
            if mean_a <= mean_b:
                break
            # Merge b into a.
            a[0] += b[0]
            a[1] += b[1]
            a[2] += b[2]
            blocks.pop()

    xs: list[float] = []
    ys: list[float] = []
    for sum_x, sum_y, count in blocks:
        xs.append(sum_x / count)
        ys.append(sum_y / count)
    return IsotonicCurve(
        xs=xs, ys=ys, n_train=len(items), slice_key=slice_key
    )


# ── Slice-aware curve registry ────────────────────────────────────────────


_MIN_PER_SLICE = 50


@dataclass
class BetaCurve:
    """3-parameter Beta calibration (Kull, Filho, Flach AISTATS 2017).

    v2.4.3 (Tier 1.7). Beta calibration dominates Platt scaling when
    the score's class-conditional distribution isn't sigmoidal, and
    dominates isotonic at small n (< ~500 records) where the
    pool-adjacent-violators fit overfits to bin boundaries. Memee's
    typical per-slice calibration data is exactly in that small-n
    regime, so Beta is the right default below the cutoff.

    Functional form (Kull 2017 eq. 4):
        σ_{a,b,c}(p) = 1 / (1 + exp(-(a·log(p) - b·log(1-p) + c)))

    The 3 parameters are fit via logistic regression on the transformed
    features ``[log(p), -log(1-p)]`` against the binary outcome. We use
    pure-Python Newton-Raphson on the log-likelihood; converges in
    O(10) iterations for any realistic input.

    Storage shape parallels :class:`IsotonicCurve` so the registry can
    hold either type transparently.
    """
    a: float = 1.0
    b: float = 1.0
    c: float = 0.0
    n_train: int = 0
    slice_key: str = ""

    def predict(self, x: float) -> float:
        # Clamp to (eps, 1-eps) so log() and log(1-x) are finite.
        eps = 1e-6
        x = max(eps, min(1.0 - eps, x))
        z = self.a * math.log(x) - self.b * math.log(1.0 - x) + self.c
        # Stable sigmoid: avoid overflow on large negative z.
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)

    def to_dict(self) -> dict:
        return {
            "kind": "beta",
            "a": self.a, "b": self.b, "c": self.c,
            "n_train": self.n_train,
            "slice_key": self.slice_key,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BetaCurve":
        return cls(
            a=float(d.get("a", 1.0)),
            b=float(d.get("b", 1.0)),
            c=float(d.get("c", 0.0)),
            n_train=int(d.get("n_train", 0)),
            slice_key=d.get("slice_key", ""),
        )


def fit_beta_calibration(
    pairs: Iterable[tuple[float, int]],
    *,
    slice_key: str = "global",
    max_iter: int = 50,
    tol: float = 1e-6,
) -> BetaCurve:
    """Fit a 3-parameter Beta calibrator (Kull et al. 2017).

    ``pairs``: ``[(predicted_probability, outcome_0_or_1), …]``.

    Pure-Python Newton-Raphson on the binary cross-entropy of
    ``σ(a·log(p) - b·log(1-p) + c)`` vs the observed outcomes. Three
    params → 3×3 Hessian, easy to invert in closed form. Returns the
    identity (a=1, b=1, c=0) when the input is degenerate (no data,
    all-zero or all-one labels — sigmoid is undefined there).
    """
    rows = list(pairs)
    if len(rows) < 2:
        return BetaCurve(slice_key=slice_key, n_train=len(rows))

    eps = 1e-6
    # Pre-compute features once.
    X = [(math.log(max(eps, min(1.0 - eps, p))),
          -math.log(max(eps, min(1.0 - eps, 1.0 - p))),
          1.0)
         for p, _ in rows]
    y = [int(o) for _, o in rows]
    n = len(rows)

    # Degenerate: all-same labels → calibrator can't separate; return identity.
    if all(o == 0 for o in y) or all(o == 1 for o in y):
        return BetaCurve(slice_key=slice_key, n_train=n)

    # Newton-Raphson on logistic loss. Init at a=1, b=1, c=0 (identity).
    theta = [1.0, 1.0, 0.0]
    for _ in range(max_iter):
        # Forward + gradient + Hessian
        g = [0.0, 0.0, 0.0]
        H = [[0.0] * 3 for _ in range(3)]
        loss = 0.0
        for xi, yi in zip(X, y):
            z = theta[0] * xi[0] + theta[1] * xi[1] + theta[2] * xi[2]
            # Stable sigmoid + log-loss.
            if z >= 0:
                p = 1.0 / (1.0 + math.exp(-z))
            else:
                ez = math.exp(z)
                p = ez / (1.0 + ez)
            p = max(eps, min(1.0 - eps, p))
            loss -= yi * math.log(p) + (1 - yi) * math.log(1.0 - p)
            err = p - yi
            for i in range(3):
                g[i] += err * xi[i]
                for j in range(3):
                    H[i][j] += p * (1.0 - p) * xi[i] * xi[j]

        # Newton step: θ -= H^-1 g. Solve the 3×3 linear system directly.
        # Add a tiny ridge so the inverse is stable on near-singular H.
        for i in range(3):
            H[i][i] += 1e-8
        delta = _solve_3x3(H, g)
        if delta is None:
            break
        max_step = max(abs(d) for d in delta)
        theta = [theta[i] - delta[i] for i in range(3)]
        if max_step < tol:
            break

    return BetaCurve(a=theta[0], b=theta[1], c=theta[2],
                     n_train=n, slice_key=slice_key)


def _solve_3x3(M: list[list[float]], b: list[float]) -> list[float] | None:
    """Cramer's rule on a 3×3 — cheaper than importing numpy here."""
    def det3(m: list[list[float]]) -> float:
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )
    d = det3(M)
    if abs(d) < 1e-12:
        return None
    out = []
    for col in range(3):
        Mi = [row[:] for row in M]
        for r in range(3):
            Mi[r][col] = b[r]
        out.append(det3(Mi) / d)
    return out


# Threshold below which BetaCurve is preferred over IsotonicCurve.
# Kull et al. show beta wins decisively at n < ~1000; isotonic wins
# above. The 500 split is conservative — at the boundary either is
# acceptable.
_BETA_CALIBRATION_MAX_N = 500


@dataclass
class CurveRegistry:
    """Per-(type, scope, source) calibration curves with a global fallback.

    v2.4.3: curves may be either :class:`IsotonicCurve` (large n) or
    :class:`BetaCurve` (small n). Both expose ``predict(x)`` so the
    registry doesn't care which kind it holds.
    """
    global_curve: IsotonicCurve | BetaCurve
    by_slice: dict[str, IsotonicCurve | BetaCurve] = field(default_factory=dict)

    @staticmethod
    def slice_key(memory_type: str | None, scope: str | None, source: str | None) -> str:
        return f"{memory_type or '_'}::{scope or '_'}::{source or '_'}"

    def predict_for(self, memory) -> float:
        raw = float(getattr(memory, "confidence_score", 0.0) or 0.0)
        key = self.slice_key(
            getattr(memory, "type", None),
            getattr(memory, "scope", None),
            getattr(memory, "source_type", None),
        )
        curve = self.by_slice.get(key, self.global_curve)
        return curve.predict(raw)

    def to_dict(self) -> dict:
        return {
            "global": self.global_curve.to_dict(),
            "slices": {k: v.to_dict() for k, v in self.by_slice.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CurveRegistry":
        return cls(
            global_curve=_curve_from_dict(d.get("global", {"xs": [], "ys": [], "n_train": 0})),
            by_slice={
                k: _curve_from_dict(v) for k, v in (d.get("slices") or {}).items()
            },
        )


def _curve_from_dict(d: dict) -> IsotonicCurve | BetaCurve:
    """Pick the right curve class based on the persisted ``kind`` marker.

    v2.4.3: introduced ``"kind": "beta"`` for BetaCurve. Legacy
    IsotonicCurve dicts have no ``kind`` field and are detected by
    structure (presence of ``xs``/``ys``). Future curve types add
    new ``kind`` markers without breaking old dicts.
    """
    if d.get("kind") == "beta":
        return BetaCurve.from_dict(d)
    return IsotonicCurve.from_dict(d)


def _fit_curve_by_size(
    pairs: list[tuple[float, int]],
    *,
    slice_key: str,
) -> IsotonicCurve | BetaCurve:
    """Pick a calibrator type based on training set size.

    v2.4.3 (Tier 1.7): below ``_BETA_CALIBRATION_MAX_N``, BetaCurve's
    3-parameter parametric form generalises better than isotonic's
    pool-adjacent-violators fit. Above the threshold, the data has
    enough resolution that isotonic's non-parametric flexibility
    earns its complexity. Kull et al. AISTATS 2017 §5.2.
    """
    if len(pairs) < _BETA_CALIBRATION_MAX_N:
        return fit_beta_calibration(pairs, slice_key=slice_key)
    return fit_isotonic(pairs, slice_key=slice_key)


def fit_curves(records: Iterable[dict]) -> CurveRegistry:
    """Fit a global curve + per-slice curves where each slice has ≥ 50 records.

    Each record: ``{"prediction": float, "outcome": int, "memory_type":
    str, "scope": str, "source_type": str}``.

    v2.4.3: per-slice fits use BetaCurve below ~500 records, isotonic
    above. The global curve uses the same rule. Both classes share the
    ``predict(x)`` interface so the registry stays type-blind.
    """
    rows = list(records)
    if not rows:
        return CurveRegistry(global_curve=IsotonicCurve(xs=[], ys=[], n_train=0))

    global_pairs = [(r["prediction"], int(r["outcome"])) for r in rows]
    global_curve = _fit_curve_by_size(global_pairs, slice_key="global")

    by_slice_inputs: dict[str, list[tuple[float, int]]] = {}
    for r in rows:
        key = CurveRegistry.slice_key(
            r.get("memory_type"), r.get("scope"), r.get("source_type")
        )
        by_slice_inputs.setdefault(key, []).append(
            (r["prediction"], int(r["outcome"]))
        )

    by_slice = {
        k: _fit_curve_by_size(pairs, slice_key=k)
        for k, pairs in by_slice_inputs.items()
        if len(pairs) >= _MIN_PER_SLICE
    }
    return CurveRegistry(global_curve=global_curve, by_slice=by_slice)


# ── Persistence (singleton row on learning_snapshots) ─────────────────────


_REGISTRY_CACHE: dict[int, CurveRegistry] = {}


def _registry_path(session: Session) -> int:
    bind = session.get_bind()
    return id(bind)


def save_curves(session: Session, registry: CurveRegistry) -> None:
    """Persist the fitted curves on a designated singleton row.

    We keep the storage simple: write to a file under ``~/.memee/`` —
    the schema-free path. A future migration can promote this to a
    dedicated ``calibration_curves`` table; for now it's a JSON file
    keyed by engine identity.
    """
    from pathlib import Path

    home = Path(os.environ.get("MEMEE_HOME", str(Path.home() / ".memee")))
    home.mkdir(parents=True, exist_ok=True)
    path = home / "calibration.json"
    path.write_text(json.dumps(registry.to_dict(), indent=2))
    _REGISTRY_CACHE[_registry_path(session)] = registry


def load_curves(session: Session) -> CurveRegistry | None:
    """Load curves persisted by ``save_curves``; ``None`` if not present."""
    cached = _REGISTRY_CACHE.get(_registry_path(session))
    if cached is not None:
        return cached
    from pathlib import Path

    home = Path(os.environ.get("MEMEE_HOME", str(Path.home() / ".memee")))
    path = home / "calibration.json"
    if not path.exists():
        return None
    try:
        registry = CurveRegistry.from_dict(json.loads(path.read_text()))
    except Exception as e:
        logger.debug("calibration: load_curves failed: %s", e)
        return None
    _REGISTRY_CACHE[_registry_path(session)] = registry
    return registry


def invalidate_cache() -> None:
    _REGISTRY_CACHE.clear()


# ── Public rescale ────────────────────────────────────────────────────────


def is_enabled() -> bool:
    """``MEMEE_CALIBRATED_CONFIDENCE=1`` flips this on. Off by default."""
    return os.environ.get("MEMEE_CALIBRATED_CONFIDENCE", "0").strip().lower() in (
        "1",
        "true",
        "on",
    )


def rescale(memory, *, session: Session | None = None) -> float:
    """Return the calibrated probability for ``memory.confidence_score``.

    When the calibration flag is off (default), returns the raw value.
    When on but no curve is available, returns the raw value (so we
    degrade safely on a fresh install). When a curve is available,
    returns the per-slice or global prediction, whichever the registry
    has.
    """
    raw = float(getattr(memory, "confidence_score", 0.0) or 0.0)
    if not is_enabled():
        return raw
    if session is None:
        return raw
    registry = load_curves(session)
    if registry is None:
        return raw
    return registry.predict_for(memory)


# ── Beta-Binomial helper for the lifecycle gate ──────────────────────────


def beta_binomial_posterior(memory, *, alpha: float = 2.0, beta: float = 2.0) -> float:
    """Posterior mean ``E[p | data]`` from a memory's validation counts.

    Wraps :func:`fit_beta_binomial` so callers don't have to reach into
    Memory internals. Used by ``lifecycle._infer_supersessions`` and the
    invalidation-ratio gate when ``MEMEE_CALIBRATED_CONFIDENCE`` is on.
    """
    return fit_beta_binomial(
        int(getattr(memory, "validation_count", 0) or 0),
        int(getattr(memory, "invalidation_count", 0) or 0),
        prior_alpha=alpha,
        prior_beta=beta,
    )


__all__ = [
    "BetaCurve",
    "CalibrationMetrics",
    "CurveRegistry",
    "IsotonicCurve",
    "beta_binomial_posterior",
    "brier_score",
    "calibration_metrics",
    "fit_beta_binomial",
    "fit_beta_calibration",
    "fit_curves",
    "fit_isotonic",
    "invalidate_cache",
    "is_enabled",
    "load_curves",
    "reliability_diagram",
    "rescale",
    "save_curves",
]
