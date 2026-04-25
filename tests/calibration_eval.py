"""R12 P1: Confidence calibration synthetic harness.

Builds a deterministic population mimicking what we expect in production
(mixed memory types × scopes × sources, varying validation outcomes
drawn from a hidden true probability), measures the gap between the
raw confidence_score and the empirical outcome rate, then fits the
isotonic + Beta-Binomial rescalers and re-measures.

Run:
    .venv/bin/python -m tests.calibration_eval

Output is a markdown table + ASCII reliability diagram. JSON dumps
of the metrics land in ``.bench/calibration_<label>.json`` for diff-
ability across branches.
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MEMEE_TELEMETRY", "0")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT_DIR = ROOT / ".bench"
OUT_DIR.mkdir(exist_ok=True)


# ── Synthetic population generator ────────────────────────────────────────

# Hidden ground-truth probabilities by memory_type. The population is
# designed so anti-patterns hit slightly higher than patterns at the
# same nominal confidence — exactly the kind of structural mis-calibration
# Memee's update rule produces.
HIDDEN_BIAS = {
    "pattern": 0.0,
    "anti_pattern": 0.10,    # anti-patterns over-predict (true rate higher)
    "decision": -0.05,
    "lesson": 0.03,
    "observation": -0.02,
}

SCOPES = ["personal", "team", "org"]
SOURCES = ["human", "llm", "import"]


def _generate_records(n: int = 1000, seed: int = 42) -> list[dict]:
    """Generate ``n`` synthetic ``(prediction, outcome, slice_keys)`` records.

    ``prediction`` is sampled uniformly in [0, 1]; ``outcome`` is drawn
    from Bernoulli with the hidden true probability ``prediction +
    bias[type]``, clipped to [0, 1]. The bias makes the population
    structurally mis-calibrated by type so the per-slice rescaler has
    a real signal to recover.
    """
    rng = random.Random(seed)
    out: list[dict] = []
    for _ in range(n):
        mtype = rng.choice(list(HIDDEN_BIAS.keys()))
        scope = rng.choice(SCOPES)
        source = rng.choice(SOURCES)
        prediction = rng.random()
        true_p = max(0.0, min(1.0, prediction + HIDDEN_BIAS[mtype]))
        outcome = 1 if rng.random() < true_p else 0
        out.append(
            {
                "prediction": prediction,
                "outcome": outcome,
                "memory_type": mtype,
                "scope": scope,
                "source_type": source,
            }
        )
    return out


def _fake_memory(record: dict):
    """Build a duck-typed memory object that the rescaler accepts."""

    class _M:
        confidence_score = record["prediction"]
        type = record["memory_type"]
        scope = record["scope"]
        source_type = record["source_type"]
        validation_count = 0
        invalidation_count = 0

    return _M()


# ── Run ───────────────────────────────────────────────────────────────────


def main(label: str = "default"):
    from memee.engine.calibration import (
        CurveRegistry,
        beta_binomial_posterior,
        calibration_metrics,
        fit_beta_binomial,
        fit_curves,
        reliability_diagram,
    )

    records = _generate_records(n=2000, seed=42)
    raw = [r["prediction"] for r in records]
    y = [r["outcome"] for r in records]

    before = calibration_metrics(raw, y)
    print("=" * 72)
    print("BEFORE CALIBRATION (raw confidence_score)")
    print("=" * 72)
    print(reliability_diagram(before))
    print()

    # Fit isotonic curves and apply.
    registry = fit_curves(records)
    iso_preds = [
        registry.predict_for(_fake_memory(r)) for r in records
    ]
    after_iso = calibration_metrics(iso_preds, y)
    print("=" * 72)
    print("AFTER ISOTONIC RESCALE (per-slice + global fallback)")
    print("=" * 72)
    print(reliability_diagram(after_iso))
    print()

    # Beta-Binomial alternative: maps validation/invalidation counts → posterior.
    # Synthetic records here don't have counts, so we synthesise them as
    # ``round(prediction * 10)`` validations and ``10 - that`` invalidations
    # to demonstrate the closed-form path.
    bb_preds = []
    for r in records:
        v = int(round(r["prediction"] * 10))
        i = 10 - v
        bb_preds.append(fit_beta_binomial(v, i))
    after_bb = calibration_metrics(bb_preds, y)
    print("=" * 72)
    print("AFTER BETA-BINOMIAL RESCALE (Beta(2,2) prior, synthetic counts)")
    print("=" * 72)
    print(reliability_diagram(after_bb))
    print()

    # Slice-level table (isotonic only).
    print("=" * 72)
    print("PER-SLICE (memory_type) — Brier / ECE / MCE")
    print("=" * 72)
    print(f"{'type':<14} {'n':>5}  {'Brier_raw':>10} {'Brier_iso':>10}  {'ECE_raw':>9} {'ECE_iso':>9}")
    by_type: dict[str, list[int]] = {}
    for i, r in enumerate(records):
        by_type.setdefault(r["memory_type"], []).append(i)
    for mtype, idxs in by_type.items():
        sub_raw = [raw[i] for i in idxs]
        sub_iso = [iso_preds[i] for i in idxs]
        sub_y = [y[i] for i in idxs]
        m_raw = calibration_metrics(sub_raw, sub_y)
        m_iso = calibration_metrics(sub_iso, sub_y)
        print(
            f"{mtype:<14} {len(idxs):>5}  {m_raw.brier:>10.4f} {m_iso.brier:>10.4f}  "
            f"{m_raw.ece:>9.4f} {m_iso.ece:>9.4f}"
        )

    # Save metrics for diff.
    out_path = OUT_DIR / f"calibration_{label}.json"
    out_path.write_text(json.dumps({
        "n": len(records),
        "raw": {"brier": before.brier, "ece": before.ece, "mce": before.mce},
        "isotonic": {"brier": after_iso.brier, "ece": after_iso.ece, "mce": after_iso.mce},
        "beta_binomial": {"brier": after_bb.brier, "ece": after_bb.ece, "mce": after_bb.mce},
    }, indent=2))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "default"
    main(label)
