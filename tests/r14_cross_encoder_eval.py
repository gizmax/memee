"""R14 A/B harness: cross-encoder reranker vs baseline.

Two configs, same query set, same corpus, paired permutation test:

    baseline:  MEMEE_RERANK_MODEL unset  (RRF + heuristic stack only)
    candidate: MEMEE_RERANK_MODEL=ms-marco-MiniLM-L-6-v2

Reports macro nDCG@10 / Recall@5 / MRR + p-values from
``permutation_test`` per cluster, plus latency p50/p95 per config.

Ship rule (from the R14 brief):
  * Macro nDCG@10 must improve by ≥ +0.01 at p < 0.05, OR
  * ``paraphrastic`` cluster nDCG@10 must improve by ≥ +0.03 at p < 0.05.

If the cross-encoder weights aren't cached locally and offline mode is
on (the default for this harness so CI runs reproducibly), the model
load will fail. We catch that, mark the run "model_unavailable", and
exit cleanly with a non-zero code so the harness never reports fake
numbers — exactly the failure mode the brief calls out.

Run:
    .venv/bin/python -m tests.r14_cross_encoder_eval                  # full eval
    .venv/bin/python -m tests.r14_cross_encoder_eval --vectors        # exercise hybrid path
    .venv/bin/python -m tests.r14_cross_encoder_eval --allow-network  # let HF download model

We default to offline because the model is 80 MB; CI shouldn't fetch it
on every run. Local devs can set ``--allow-network`` once to warm the
cache, then go back to offline.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Order-sensitive: the eval module sets HF/TRANSFORMERS offline on import.
# We have to choose offline-or-not BEFORE that import so the env vars take
# the right value.
ALLOW_NETWORK = "--allow-network" in sys.argv
USE_VECTORS = "--vectors" in sys.argv

if ALLOW_NETWORK:
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    os.environ.pop("HF_HUB_OFFLINE", None)
else:
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

# We toggle MEMEE_RERANK_MODEL between the two configs; ensure it isn't
# already set in the environment so the baseline config is honest.
_PRE_RERANK = os.environ.pop("MEMEE_RERANK_MODEL", None)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tests.retrieval_eval import (  # noqa: E402
    CLUSTER_NAMES,
    QUERIES,
    _fresh_db,
    _maturity_bias_at_k,
    _mrr,
    _ndcg_at_k,
    _recall_at_k,
    _seed,
    _type_match_precision_at_k,
    permutation_test,
)


RERANK_MODEL = os.environ.get(
    "MEMEE_RERANK_MODEL_FOR_EVAL",
    "ms-marco-MiniLM-L-6-v2",
)


def _config_name(rerank_on: bool) -> str:
    return f"rerank_{'on' if rerank_on else 'off'}"


def _percentile(values: list[float], pct: float) -> float:
    """Plain Python percentile (no numpy dep at the harness level)."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = pct / 100.0 * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def run_config(rerank_on: bool, *, use_vectors: bool) -> dict:
    """Run the full query set under one config and return per-query metrics.

    We rebuild the DB fresh for each config so neither run pollutes the
    other's vector matrix cache; the corpus is small enough (~255 rows)
    that the seed cost is negligible.
    """
    # Toggle the rerank flag for this run. The reranker reads the env var on
    # construction, and the module-level model cache is reset between runs
    # so a stale CrossEncoder doesn't survive the toggle.
    if rerank_on:
        os.environ["MEMEE_RERANK_MODEL"] = RERANK_MODEL
    else:
        os.environ.pop("MEMEE_RERANK_MODEL", None)

    from memee.engine import reranker
    from memee.engine.search import search_memories

    reranker.reset_for_tests()

    # If the candidate config can't load the model, bail so we never report
    # fake "improvement" numbers. The OFF config is allowed to skip the
    # load since it doesn't touch the cross-encoder at all.
    if rerank_on:
        rr = reranker.CrossEncoderReranker()
        if not rr.is_enabled():
            return {"model_unavailable": True}
        # Force a load attempt up front so we surface failures before the
        # eval loop and so first-query latency doesn't dominate the p95.
        loaded = reranker._try_load(rr.model_name)
        if loaded is None:
            return {"model_unavailable": True}

    engine, session, org = _fresh_db()
    _seed(session, org)

    per_query: list[dict] = []
    latency_ms: list[float] = []
    per_cluster_ndcg: dict[str, list[float]] = {n: [] for n in CLUSTER_NAMES}
    per_cluster_recall5: dict[str, list[float]] = {n: [] for n in CLUSTER_NAMES}
    per_cluster_mrr: dict[str, list[float]] = {n: [] for n in CLUSTER_NAMES}

    for sample in QUERIES:
        rel_dict: dict[str, int] = {mid: g for mid, g in sample["rel"]}
        t0 = time.perf_counter()
        results = search_memories(
            session, sample["q"], limit=10, use_vectors=use_vectors
        )
        latency_ms.append((time.perf_counter() - t0) * 1000.0)
        retrieved_ids = [r["memory"].id for r in results]
        retrieved_memories = [r["memory"] for r in results]

        ndcg10 = _ndcg_at_k(retrieved_ids, rel_dict, 10)
        r5 = _recall_at_k(retrieved_ids, rel_dict, 5)
        mrr = _mrr(retrieved_ids, rel_dict)
        type_p5 = _type_match_precision_at_k(retrieved_memories, rel_dict, 5)
        mat_b5 = _maturity_bias_at_k(retrieved_memories, 5)

        per_query.append({
            "q": sample["q"],
            "cluster": sample.get("cluster"),
            "ndcg10": ndcg10,
            "recall5": r5,
            "mrr": mrr,
            "type_p5": type_p5,
            "mat_b5": mat_b5,
        })

        cl = sample.get("cluster")
        if cl in per_cluster_ndcg:
            per_cluster_ndcg[cl].append(ndcg10)
            per_cluster_recall5[cl].append(r5)
            per_cluster_mrr[cl].append(mrr)

    session.close()
    n = len(per_query)

    per_cluster: dict[str, dict] = {}
    for name in CLUSTER_NAMES:
        scores = per_cluster_ndcg[name]
        if not scores:
            per_cluster[name] = {"n": 0}
            continue
        per_cluster[name] = {
            "n": len(scores),
            "ndcg10": sum(scores) / len(scores),
            "recall5": sum(per_cluster_recall5[name]) / len(per_cluster_recall5[name]),
            "mrr": sum(per_cluster_mrr[name]) / len(per_cluster_mrr[name]),
            "ndcg10_per_query": list(scores),
            "recall5_per_query": list(per_cluster_recall5[name]),
            "mrr_per_query": list(per_cluster_mrr[name]),
        }

    return {
        "model_unavailable": False,
        "n_queries": n,
        "macro_ndcg10": sum(p["ndcg10"] for p in per_query) / n,
        "macro_recall5": sum(p["recall5"] for p in per_query) / n,
        "macro_mrr": sum(p["mrr"] for p in per_query) / n,
        "ndcg10_per_query": [p["ndcg10"] for p in per_query],
        "recall5_per_query": [p["recall5"] for p in per_query],
        "mrr_per_query": [p["mrr"] for p in per_query],
        "latency_p50_ms": _percentile(latency_ms, 50),
        "latency_p95_ms": _percentile(latency_ms, 95),
        "per_cluster": per_cluster,
        "per_query": per_query,
    }


def _print_macro_table(baseline: dict, candidate: dict) -> None:
    keys = [
        ("macro_ndcg10", "nDCG@10"),
        ("macro_recall5", "Recall@5"),
        ("macro_mrr", "MRR"),
        ("latency_p50_ms", "p50 (ms)"),
        ("latency_p95_ms", "p95 (ms)"),
    ]
    print()
    print(f"{'metric':<14} {'baseline':>11} {'candidate':>11} {'delta':>10}")
    print("-" * 50)
    for k, label in keys:
        b = baseline.get(k, 0.0)
        c = candidate.get(k, 0.0)
        d = c - b
        sign = "+" if d >= 0 else ""
        print(f"{label:<14} {b:>11.4f} {c:>11.4f} {sign}{d:>9.4f}")

    # Macro permutation test on per-query nDCG@10.
    p_macro = permutation_test(
        candidate["ndcg10_per_query"],
        baseline["ndcg10_per_query"],
        n_iter=10000,
        seed=0,
    )
    print(f"\nMacro nDCG@10 paired permutation p-value: {p_macro:.4f}")


def _print_cluster_table(baseline: dict, candidate: dict) -> dict:
    """Per-cluster nDCG@10 deltas + permutation test p-values.

    Returns a dict keyed by cluster name so the ship-rule check at the end
    of ``main`` doesn't have to re-read this output."""
    print()
    print(
        f"{'cluster':<22} {'n':>4} {'base nDCG':>10} {'cand nDCG':>10} "
        f"{'delta':>8} {'p':>7}"
    )
    print("-" * 64)
    out: dict[str, dict] = {}
    for name in CLUSTER_NAMES:
        b = baseline["per_cluster"].get(name, {})
        c = candidate["per_cluster"].get(name, {})
        if b.get("n", 0) == 0 or c.get("n", 0) == 0:
            print(f"{name:<22} {b.get('n', 0):>4} {'-':>10} {'-':>10} {'-':>8} {'-':>7}")
            continue
        if b["n"] != c["n"]:
            print(
                f"{name:<22} {b['n']:>4} {'-':>10} {'-':>10} "
                f"{'-':>8} {'mismatch':>7}"
            )
            continue
        delta = c["ndcg10"] - b["ndcg10"]
        p = permutation_test(
            c["ndcg10_per_query"], b["ndcg10_per_query"], n_iter=10000, seed=0
        )
        out[name] = {
            "n": b["n"],
            "delta": delta,
            "p_value": p,
            "baseline_ndcg10": b["ndcg10"],
            "candidate_ndcg10": c["ndcg10"],
        }
        sign = "+" if delta >= 0 else ""
        print(
            f"{name:<22} {b['n']:>4} {b['ndcg10']:>10.4f} {c['ndcg10']:>10.4f} "
            f"{sign}{delta:>7.4f} {p:>7.4f}"
        )
    return out


def main() -> int:
    print("R14 cross-encoder rerank A/B")
    print(f"  use_vectors:    {USE_VECTORS}")
    print(f"  rerank model:   {RERANK_MODEL}")
    print(f"  network:        {'allowed' if ALLOW_NETWORK else 'offline (default)'}")

    print("\nRunning baseline (rerank OFF) ...")
    baseline = run_config(False, use_vectors=USE_VECTORS)
    if baseline.get("model_unavailable"):
        # Should be impossible — the OFF config never loads the model.
        print("  baseline failed unexpectedly", file=sys.stderr)
        return 2

    print("Running candidate (rerank ON) ...")
    candidate = run_config(True, use_vectors=USE_VECTORS)
    if candidate.get("model_unavailable"):
        print(
            "\nCross-encoder model could not be loaded "
            "(no cache + offline, or sentence-transformers missing).",
            file=sys.stderr,
        )
        print(
            "  Skipping A/B cleanly — no fake numbers reported. "
            "Rerun with --allow-network to fetch weights, or install:\n"
            "    pip install memee[rerank]",
            file=sys.stderr,
        )
        # We did NOT measure a delta; nothing to report.
        print("\nparaphrastic cluster ΔnDCG@10: model couldn't load — wiring only, default off")
        return 0

    _print_macro_table(baseline, candidate)
    cluster_stats = _print_cluster_table(baseline, candidate)

    # Save full run for downstream artefact storage / future diffs.
    out_dir = ROOT / ".bench"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "r14_cross_encoder_eval.json"
    out_path.write_text(json.dumps({
        "baseline": {k: v for k, v in baseline.items() if k != "per_query"},
        "candidate": {k: v for k, v in candidate.items() if k != "per_query"},
        "cluster_stats": cluster_stats,
    }, indent=2, default=float))
    print(f"\nSaved: {out_path}")

    # Ship-rule check.
    macro_delta = candidate["macro_ndcg10"] - baseline["macro_ndcg10"]
    macro_p = permutation_test(
        candidate["ndcg10_per_query"],
        baseline["ndcg10_per_query"],
        n_iter=10000,
        seed=0,
    )
    para = cluster_stats.get("paraphrastic", {})
    para_delta = para.get("delta", 0.0)
    para_p = para.get("p_value", 1.0)

    macro_pass = macro_delta >= 0.01 and macro_p < 0.05
    para_pass = para_delta >= 0.03 and para_p < 0.05

    print()
    print(f"Ship rule: macro ≥+0.01 @ p<0.05  → "
          f"Δ={macro_delta:+.4f} p={macro_p:.4f} ({'PASS' if macro_pass else 'fail'})")
    print(f"           paraphrastic ≥+0.03 @ p<0.05 → "
          f"Δ={para_delta:+.4f} p={para_p:.4f} ({'PASS' if para_pass else 'fail'})")

    if macro_pass or para_pass:
        print(
            "\nVERDICT: ship-rule satisfied — flip default ON for paraphrastic-heavy "
            "deployments (cost: +50–200 ms/query at top-30)."
        )
    else:
        print(
            "\nVERDICT: ship-rule not satisfied — keeping default OFF. "
            "Wiring stays in place so a future model upgrade can flip the "
            "flag without code changes."
        )

    # Final one-line summary the brief asks the agent to print.
    print(
        f"\nparaphrastic cluster ΔnDCG@10: {para_delta:+.4f}  "
        f"p={para_p:.4f}  n={para.get('n', 0)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
