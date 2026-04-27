"""Grid-search the ranking constants in ``memee.engine.search`` (v2.0.0).

The hand-tuned constants in ``search.py`` were each defended by a small
A/B run, but they were never tuned **jointly**. This harness sweeps the
4-dim grid the v2.0.0 brief asks for and prints the macro-best variant
that survives a paired permutation test (p<0.10 vs the current default)
AND doesn't regress any cluster nDCG@10 by more than 0.02.

Dimensions (justified inline below):

  1. ``RRF_K`` ∈ {20, 40, 60, 80}
     Note: only fires in the hybrid (vector-on) path, so on the BM25-only
     ``retrieval_eval`` it has *no* measurable effect. We still sweep it
     so the grid log records that we looked, but the picked value is the
     macro-best WITHOUT degradation in BM25-only mode (our shipping
     default for callers without embeddings).

  2. ``TITLE_PHRASE_BOOST`` ∈ {1.10 .. 1.50, step 0.05}

  3. BM25-only blend coefficients (the "tag/conf coefficient pair" the
     brief asks us to find): ``BM25_ONLY_TAG_W`` and ``BM25_ONLY_CONF_W``,
     swept ±50 % of their current values:
       tag  ∈ {0.125, 0.250, 0.375}
       conf ∈ {0.100, 0.200, 0.300}

  4. ``MATURITY_MULTIPLIER`` shape — 3 alternatives:
       baseline:   {canon 1.0, validated 0.85, tested 0.65,
                    hypothesis 0.4,  deprecated 0.05}
       flatter:    {canon 1.0, validated 0.90, tested 0.70,
                    hypothesis 0.50, deprecated 0.05}
       steeper:    {canon 1.0, validated 0.80, tested 0.55,
                    hypothesis 0.30, deprecated 0.05}

Total combinations (excluding RRF_K, see note 1):
    9 × 3 × 3 × 3 = 243   (≈ 0.9 s / run → ~4 min)

We pin ``MEMEE_RERANK=0`` for the entire run so the cross-encoder
(default-on in v2.0.0 when the HF cache is warm) doesn't mask the lift
or regression of the constants we're tuning.

Idempotency: every combination is deterministic (no RNG inside the
ranker; the eval seeds Python's ``random`` only inside ``permutation_test``
with a fixed seed). Running this script twice produces identical
``v2_grid_search_after.json`` byte-for-byte.

Run:
    MEMEE_RERANK=0 .venv/bin/python -m tests.grid_search_ranker
    MEMEE_RERANK=0 .venv/bin/python -m tests.grid_search_ranker --quick   # 27 combos
"""

from __future__ import annotations

import json
import os
import sys
import time
from itertools import product
from pathlib import Path

# Pin the kill switch BEFORE importing anything that might load the model.
# The v2.0.0 default-on rerank would otherwise add ~50-200 ms / query and
# the 0.0355 lift it provides would mask the (smaller) lift we're trying
# to measure on the BM25 stack. The grid is about the BM25 stack alone.
os.environ["MEMEE_RERANK"] = "0"
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MEMEE_TELEMETRY", "0")

# Quiet alembic — every fresh DB triggers an init log line that buries the
# grid progress output under hundreds of identical INFO records.
import logging  # noqa: E402

logging.getLogger("alembic").setLevel(logging.WARNING)
logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tests.retrieval_eval import (  # noqa: E402
    CLUSTER_NAMES,
    evaluate,
    permutation_test,
)


# ── Grid definition ─────────────────────────────────────────────────────────

RRF_K_GRID = [20, 40, 60, 80]  # documented; not measurable in BM25-only

TITLE_PHRASE_BOOST_GRID = [1.10, 1.15, 1.20, 1.25, 1.30, 1.35, 1.40, 1.45, 1.50]

# ±50 % of the current (0.25, 0.20). Three points per dim keeps the run
# tractable without losing the curvature.
TAG_W_GRID = [0.125, 0.250, 0.375]
CONF_W_GRID = [0.100, 0.200, 0.300]

# Three maturity-multiplier shapes. The deprecated tier stays at 0.05
# everywhere — moving it would require a separate eval since the corpus
# has only one deprecated row and the metric would be noise-dominated.
MATURITY_SHAPES = {
    "baseline": {
        "canon": 1.0, "validated": 0.85, "tested": 0.65,
        "hypothesis": 0.40, "deprecated": 0.05,
    },
    "flatter": {
        "canon": 1.0, "validated": 0.90, "tested": 0.70,
        "hypothesis": 0.50, "deprecated": 0.05,
    },
    "steeper": {
        "canon": 1.0, "validated": 0.80, "tested": 0.55,
        "hypothesis": 0.30, "deprecated": 0.05,
    },
}

# Quick mode: drop RRF_K (no effect anyway) and shrink the grid for smoke
# tests. Useful when iterating on this harness itself; the production run
# uses the full grid.
QUICK_TITLE = [1.20, 1.30, 1.40]
QUICK_TAG = [0.250]
QUICK_CONF = [0.200]
QUICK_MAT = ["baseline", "flatter", "steeper"]

REJECT_REGRESSION_THRESHOLD = 0.02   # nDCG@10 / cluster
P_VALUE_GATE = 0.10


# ── Single-run wrapper ──────────────────────────────────────────────────────


def _apply_constants(
    *,
    title_phrase_boost: float,
    tag_w: float,
    conf_w: float,
    maturity_shape: dict,
    rrf_k: int,
) -> None:
    """Mutate ``memee.engine.search`` module globals in place.

    The ranker reads these constants at call time (no closure capture), so
    the eval inside ``run_combo`` will see the new values. We never reload
    the module — that would invalidate the embedded-corpus matrix cache
    and other per-process caches that are expensive to rebuild.

    Note on the BM25-only blend: the weights aren't required to sum to 1
    (the ranker uses them as relative weights inside a single linear
    combination, then applies multiplicative boosts on top). We sweep
    tag/conf coefficients directly; ``BM25_ONLY_BM25_W`` stays at default
    so the blend ratio shifts smoothly across the grid.
    """
    from memee.engine import search

    search.TITLE_PHRASE_BOOST = title_phrase_boost
    search.BM25_ONLY_TAG_W = tag_w
    search.BM25_ONLY_CONF_W = conf_w
    search.MATURITY_MULTIPLIER.clear()
    search.MATURITY_MULTIPLIER.update(maturity_shape)
    search.RRF_K = rrf_k


def run_combo(
    *,
    title_phrase_boost: float,
    tag_w: float,
    conf_w: float,
    maturity_shape: dict,
    maturity_label: str,
    rrf_k: int,
) -> dict:
    """Apply one combination, run the eval, return a result row."""
    _apply_constants(
        title_phrase_boost=title_phrase_boost,
        tag_w=tag_w,
        conf_w=conf_w,
        maturity_shape=maturity_shape,
        rrf_k=rrf_k,
    )
    res = evaluate(use_vectors=False)
    per_cluster = {}
    for name in CLUSTER_NAMES:
        body = res["per_cluster"].get(name, {})
        per_cluster[name] = {
            "n": body.get("n", 0),
            "ndcg10": body.get("ndcg10"),
            "ndcg10_per_query": body.get("ndcg10_per_query", []),
        }
    return {
        "config": {
            "title_phrase_boost": title_phrase_boost,
            "tag_w": tag_w,
            "conf_w": conf_w,
            "maturity_shape": maturity_label,
            "rrf_k": rrf_k,
        },
        "macro_ndcg10": res["macro_ndcg10"],
        "macro_recall5": res["macro_recall5"],
        "macro_mrr": res["macro_mrr"],
        "per_cluster": per_cluster,
        "per_query_ndcg10": [p["ndcg10"] for p in res["per_query"]],
    }


# ── Grid driver ─────────────────────────────────────────────────────────────


def _iter_grid(quick: bool):
    if quick:
        title_grid = QUICK_TITLE
        tag_grid = QUICK_TAG
        conf_grid = QUICK_CONF
        mat_grid = [(k, MATURITY_SHAPES[k]) for k in QUICK_MAT]
        rrf_grid = [40]
    else:
        title_grid = TITLE_PHRASE_BOOST_GRID
        tag_grid = TAG_W_GRID
        conf_grid = CONF_W_GRID
        mat_grid = list(MATURITY_SHAPES.items())
        # Skip RRF_K sweep in BM25-only mode — see module docstring note 1.
        # Pinning to 40 keeps the production hybrid path's RRF_K stable.
        rrf_grid = [40]
    yield from product(title_grid, tag_grid, conf_grid, mat_grid, rrf_grid)


def grid_search(quick: bool = False) -> dict:
    """Run the full grid; return ``{baseline, runs, winner}``.

    The baseline is computed first so we have a reference for the gate.
    Every other run is compared against it on (a) macro nDCG@10 with a
    paired permutation test, and (b) per-cluster nDCG@10 monotonicity
    (no cluster may drop > REJECT_REGRESSION_THRESHOLD).
    """
    baseline = run_combo(
        title_phrase_boost=1.30,
        tag_w=0.25,
        conf_w=0.20,
        maturity_shape=MATURITY_SHAPES["baseline"],
        maturity_label="baseline",
        rrf_k=40,
    )

    runs = []
    combos = list(_iter_grid(quick))
    print(f"[grid] {len(combos)} combinations to evaluate")
    t0 = time.time()
    for i, (title, tag, conf, (mat_name, mat_dict), rrf_k) in enumerate(combos):
        run = run_combo(
            title_phrase_boost=title,
            tag_w=tag,
            conf_w=conf,
            maturity_shape=mat_dict,
            maturity_label=mat_name,
            rrf_k=rrf_k,
        )
        runs.append(run)
        if (i + 1) % 25 == 0 or i == len(combos) - 1:
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(combos) - i - 1)
            best_so_far = max((r["macro_ndcg10"] for r in runs), default=0.0)
            print(
                f"[grid] {i + 1}/{len(combos)} "
                f"elapsed={elapsed:.1f}s eta={eta:.1f}s "
                f"best_macro_so_far={best_so_far:.4f}"
            )

    winner = pick_winner(baseline, runs)
    return {"baseline": baseline, "runs": runs, "winner": winner}


def pick_winner(baseline: dict, runs: list[dict]) -> dict | None:
    """Pick the macro-best run that passes both gates.

    Gates:
      1. Paired permutation test on the per-query nDCG@10 list:
         p < ``P_VALUE_GATE`` AND mean(current) > mean(baseline).
      2. No cluster drops by more than ``REJECT_REGRESSION_THRESHOLD``
         in nDCG@10 vs the baseline.

    Sorted by macro nDCG@10 descending; the first run passing both gates
    wins. Returning ``None`` means the grid produced nothing better than
    the baseline within the gate — the caller should keep current values.
    """
    base_per_q = baseline["per_query_ndcg10"]
    base_per_cluster = {
        name: baseline["per_cluster"][name].get("ndcg10")
        for name in CLUSTER_NAMES
    }

    candidates = sorted(runs, key=lambda r: r["macro_ndcg10"], reverse=True)
    for run in candidates:
        if run["macro_ndcg10"] <= baseline["macro_ndcg10"]:
            # Stop scanning when we drop below baseline — the grid is sorted.
            break
        # Gate 1: per-cluster monotonicity
        regressed_cluster = None
        for name in CLUSTER_NAMES:
            cur = run["per_cluster"][name].get("ndcg10")
            base = base_per_cluster.get(name)
            if cur is None or base is None:
                continue
            if base - cur > REJECT_REGRESSION_THRESHOLD:
                regressed_cluster = (name, base, cur, base - cur)
                break
        if regressed_cluster:
            run["rejected"] = {
                "reason": "cluster_regression",
                "cluster": regressed_cluster[0],
                "baseline": regressed_cluster[1],
                "current": regressed_cluster[2],
                "delta": -regressed_cluster[3],
            }
            continue
        # Gate 2: paired permutation test
        p = permutation_test(
            run["per_query_ndcg10"], base_per_q, n_iter=10000, seed=0
        )
        run["p_value"] = round(p, 4)
        if p < P_VALUE_GATE:
            run["accepted"] = True
            return run
        run["rejected"] = {"reason": "p_value", "p_value": p}
    return None


# ── CLI ─────────────────────────────────────────────────────────────────────


def _save_grid_log(result: dict, path: Path) -> None:
    path.parent.mkdir(exist_ok=True)
    # Strip the per-query nDCG list from the saved runs — it's huge (207
    # floats × N runs) and only needed for the permutation test in-memory.
    runs_lite = []
    for r in result["runs"]:
        clean = {k: v for k, v in r.items() if k != "per_query_ndcg10"}
        # Same slim-down on per_cluster
        clean["per_cluster"] = {
            n: {k: v for k, v in body.items() if k != "ndcg10_per_query"}
            for n, body in r["per_cluster"].items()
        }
        runs_lite.append(clean)
    payload = {
        "baseline": {
            k: v for k, v in result["baseline"].items()
            if k != "per_query_ndcg10"
        },
        "runs": runs_lite,
        "winner": (
            {k: v for k, v in result["winner"].items() if k != "per_query_ndcg10"}
            if result["winner"] else None
        ),
    }
    # Drop ndcg10_per_query from the baseline's per_cluster too
    payload["baseline"]["per_cluster"] = {
        n: {k: v for k, v in body.items() if k != "ndcg10_per_query"}
        for n, body in result["baseline"]["per_cluster"].items()
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    result = grid_search(quick=quick)

    log_path = ROOT / ".bench" / (
        "v2_grid_search_log_quick.json" if quick else "v2_grid_search_log.json"
    )
    _save_grid_log(result, log_path)
    print(f"\n[grid] log written to {log_path}")

    print("\n[grid] Baseline macro nDCG@10:", result["baseline"]["macro_ndcg10"])
    if result["winner"]:
        w = result["winner"]
        cfg = w["config"]
        print(f"[grid] WINNER macro nDCG@10: {w['macro_ndcg10']} "
              f"(p={w.get('p_value')})")
        print(f"        TITLE_PHRASE_BOOST = {cfg['title_phrase_boost']}")
        print(f"        BM25_ONLY_TAG_W    = {cfg['tag_w']}")
        print(f"        BM25_ONLY_CONF_W   = {cfg['conf_w']}")
        print(f"        MATURITY_SHAPE     = {cfg['maturity_shape']}")
        print(f"        RRF_K              = {cfg['rrf_k']} (held; not measurable in BM25-only eval)")
        print("\n[grid] Per-cluster after vs baseline:")
        for name in CLUSTER_NAMES:
            cur = w["per_cluster"][name].get("ndcg10") or 0.0
            base = result["baseline"]["per_cluster"][name].get("ndcg10") or 0.0
            sign = "+" if cur >= base else ""
            print(f"        {name:<22} {base:.4f} → {cur:.4f}  ({sign}{cur - base:+.4f})")
    else:
        print("[grid] NO winner passed both gates. Keep current constants.")
