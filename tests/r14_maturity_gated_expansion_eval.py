"""R14 A/B harness: maturity-gated query expansion.

Why this file exists
--------------------
The router's ``_build_search_query`` runs ``_expand_query`` to broaden
recall on vector-aware deployments. R10 cycle 1 already gates expansion
on ``_db_has_any_embeddings`` (avoids a -0.0265 nDCG@10 hit on BM25-only
DBs where the extra terms dilute lexical precision). R14 adds a second
gate: when the *raw* task already lights up a CANON/VALIDATED pattern
strongly via BM25, the canon answer is already going to be top-K and
expansion can only dilute it. Skipping the expansion in that case
should preserve macro nDCG and lift the ``code_specific`` cluster
where the gate fires most often.

Configs
-------

  * ``baseline``      — current router (R10 expansion gate only).
                        ``MEMEE_MATURITY_GATED_EXPANSION=0``.
  * ``candidate-A``   — R14 maturity gate ON, threshold 0.7 (default).
  * ``candidate-B``   — R14 maturity gate ON, threshold 0.85 (stricter:
                        more queries fall through to expansion).

Per-config we run the entire labelled query set (same 207 queries the
``retrieval_eval`` harness uses), built into the ``_build_search_query``
output for that config, then handed to ``search_memories``. Per-cluster
nDCG@10 + macro nDCG@10 + paired permutation_test vs baseline.

Ship rule
---------

  * Macro nDCG@10 ≥ +0.003 at p < 0.10  **OR**
  * ``code_specific`` cluster ≥ +0.005 at p < 0.10

If neither, ship behind ``MEMEE_MATURITY_GATED_EXPANSION=1`` opt-in.

Run
---

::

    .venv/bin/python -m tests.r14_maturity_gated_expansion_eval
    .venv/bin/python -m tests.r14_maturity_gated_expansion_eval --vectors
    .venv/bin/python -m tests.r14_maturity_gated_expansion_eval --save r14
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MEMEE_TELEMETRY", "0")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tests.retrieval_eval import (  # noqa: E402
    CLUSTER_NAMES,
    CORPUS,
    QUERIES,
    _fresh_db,
    _maturity_bias_at_k,
    _mrr,
    _ndcg_at_k,
    _recall_at_k,
    _seed,
    permutation_test,
)


CONFIGS = [
    # label, env: gate enabled?, threshold
    ("baseline", {"MEMEE_MATURITY_GATED_EXPANSION": "0"}),
    ("candidate_A_thr_0.7", {
        "MEMEE_MATURITY_GATED_EXPANSION": "1",
        "MEMEE_MATURITY_GATE_THRESHOLD": "0.7",
    }),
    ("candidate_B_thr_0.85", {
        "MEMEE_MATURITY_GATED_EXPANSION": "1",
        "MEMEE_MATURITY_GATE_THRESHOLD": "0.85",
    }),
]


def _apply_env(env: dict[str, str]) -> dict[str, str | None]:
    """Set env vars and return the prior values so the caller can restore.

    Storing ``None`` means "the var was not set"; restorer pops on
    teardown. Keeps the harness self-contained when run inline without
    a fresh subprocess per config.
    """
    prior: dict[str, str | None] = {}
    for k, v in env.items():
        prior[k] = os.environ.get(k)
        os.environ[k] = v
    return prior


def _restore_env(prior: dict[str, str | None]) -> None:
    for k, v in prior.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _evaluate_config(
    label: str,
    env: dict[str, str],
    use_vectors: bool,
) -> dict:
    """Run the full eval through the *router*'s query builder.

    Each query is fed to ``_build_search_query(task, stack_tags=set(),
    session=session)``. The output is then handed to ``search_memories``
    with the same ``limit=10`` / ``use_vectors`` settings the
    ``retrieval_eval`` harness uses. Result-shape stays compatible with
    the cluster-permutation_test helpers.

    The gate config is set via env vars before each call. We re-import
    the engine module after env mutation to make sure the module-level
    ``MATURITY_GATE_THRESHOLD`` constant picks up the new value
    (Python reads it once at import-time otherwise).
    """
    prior = _apply_env(env)
    try:
        # Force re-import so ``MATURITY_GATE_THRESHOLD`` and the
        # ``_maturity_gate_enabled`` flag re-read the env.
        for mod in [
            "memee.engine.router",
        ]:
            sys.modules.pop(mod, None)
        from memee.engine.router import _build_search_query  # noqa: E402
        from memee.engine.search import search_memories  # noqa: E402

        engine, session, _org = _fresh_db()
        _seed(session, _org)

        per_cluster: dict[str, list[float]] = {n: [] for n in CLUSTER_NAMES}
        per_cluster_recall5: dict[str, list[float]] = {n: [] for n in CLUSTER_NAMES}
        ndcg10s: list[float] = []
        recall5s: list[float] = []
        recall10s: list[float] = []
        mrrs: list[float] = []
        mat_b5s: list[float] = []
        per_query: list[dict] = []
        # Diagnostic: how often does the gate skip expansion in this config?
        gate_fired = 0

        for sample in QUERIES:
            raw_q = sample["q"]
            built = _build_search_query(raw_q, set(), session=session)
            # The gate fired if and only if the built query equals the raw
            # task (i.e. expansion was skipped). The R10 vector gate also
            # returns the raw query on BM25-only DBs — this harness runs
            # with ``use_vectors=use_vectors`` so a False-and-False mix
            # is accounted for in the per-query log below.
            fired = (built == raw_q)
            if fired:
                gate_fired += 1

            results = search_memories(
                session, built, limit=10, use_vectors=use_vectors
            )
            retrieved_ids = [r["memory"].id for r in results]
            retrieved_memories = [r["memory"] for r in results]
            rel_dict = {mid: g for mid, g in sample["rel"]}

            ndcg10 = _ndcg_at_k(retrieved_ids, rel_dict, 10)
            r5 = _recall_at_k(retrieved_ids, rel_dict, 5)
            r10 = _recall_at_k(retrieved_ids, rel_dict, 10)
            mrr = _mrr(retrieved_ids, rel_dict)
            mat_b5 = _maturity_bias_at_k(retrieved_memories, 5)

            ndcg10s.append(ndcg10)
            recall5s.append(r5)
            recall10s.append(r10)
            mrrs.append(mrr)
            mat_b5s.append(mat_b5)

            cl = sample.get("cluster")
            if cl in per_cluster:
                per_cluster[cl].append(ndcg10)
                per_cluster_recall5[cl].append(r5)

            per_query.append({
                "q": raw_q[:60],
                "cluster": cl,
                "gate_fired": fired,
                "ndcg10": round(ndcg10, 4),
            })

        session.close()
    finally:
        _restore_env(prior)

    n = len(QUERIES)
    cluster_summary: dict[str, dict] = {}
    for cl in CLUSTER_NAMES:
        scores = per_cluster[cl]
        bn = len(scores)
        if bn == 0:
            cluster_summary[cl] = {"n": 0}
            continue
        cluster_summary[cl] = {
            "n": bn,
            "ndcg10": round(sum(scores) / bn, 4),
            "ndcg10_per_query": [round(s, 4) for s in scores],
            "recall5": round(
                sum(per_cluster_recall5[cl]) / bn, 4
            ),
        }

    return {
        "label": label,
        "ranker": "hybrid" if use_vectors else "bm25_only",
        "n_queries": n,
        "n_corpus": len(CORPUS),
        "macro_ndcg10": round(sum(ndcg10s) / n, 4),
        "macro_recall5": round(sum(recall5s) / n, 4),
        "macro_recall10": round(sum(recall10s) / n, 4),
        "macro_mrr": round(sum(mrrs) / n, 4),
        "macro_mat_b5": round(sum(mat_b5s) / n, 4),
        "ndcg10_per_query": [round(s, 4) for s in ndcg10s],
        "per_cluster": cluster_summary,
        "gate_fired_count": gate_fired,
        "gate_fire_rate": round(gate_fired / n, 4),
        "per_query": per_query,
    }


def _diff_table(baseline: dict, candidate: dict) -> str:
    keys = ["macro_ndcg10", "macro_recall5", "macro_recall10",
            "macro_mrr", "macro_mat_b5"]
    lines = [
        f"\n=== {candidate['label']} vs {baseline['label']} ===",
        f"{'metric':<18} {'baseline':>10} {'cand':>10} {'delta':>10}",
        "-" * 52,
    ]
    for k in keys:
        b = baseline[k]
        c = candidate[k]
        d = c - b
        sign = "+" if d >= 0 else ""
        lines.append(f"{k:<18} {b:>10.4f} {c:>10.4f} {sign}{d:>9.4f}")
    # Macro permutation test
    p_macro = permutation_test(
        candidate["ndcg10_per_query"],
        baseline["ndcg10_per_query"],
        n_iter=10000,
    )
    lines.append(f"\nMacro permutation_test p={p_macro:.4f}")
    lines.append(
        f"Gate fired: {candidate['gate_fired_count']} / "
        f"{candidate['n_queries']} ({candidate['gate_fire_rate']:.1%})"
    )

    # Per-cluster permutation tests
    lines.append("")
    lines.append(
        f"{'cluster':<22} {'n':>4} {'base':>8} {'cand':>8} "
        f"{'delta':>9} {'p':>8}"
    )
    lines.append("-" * 64)
    cur_pc = candidate["per_cluster"]
    base_pc = baseline["per_cluster"]
    for name in CLUSTER_NAMES:
        cur = cur_pc.get(name, {})
        base = base_pc.get(name, {})
        a = cur.get("ndcg10_per_query")
        b = base.get("ndcg10_per_query")
        n = cur.get("n", 0)
        if not a or not b or len(a) != len(b):
            lines.append(f"{name:<22} {n:>4} {'-':>8} {'-':>8} {'-':>9} {'-':>8}")
            continue
        p = permutation_test(a, b, n_iter=10000)
        delta = sum(a) / len(a) - sum(b) / len(b)
        lines.append(
            f"{name:<22} {n:>4} {base['ndcg10']:>8.4f} {cur['ndcg10']:>8.4f} "
            f"{delta:>+9.4f} {p:>8.4f}"
        )
    return "\n".join(lines)


def _ship_decision(baseline: dict, candidate: dict) -> tuple[str, dict]:
    """Apply the R14 ship rule. Returns ``(decision, reason_dict)``."""
    p_macro = permutation_test(
        candidate["ndcg10_per_query"],
        baseline["ndcg10_per_query"],
        n_iter=10000,
    )
    delta_macro = candidate["macro_ndcg10"] - baseline["macro_ndcg10"]
    cs_cur = candidate["per_cluster"].get("code_specific", {})
    cs_base = baseline["per_cluster"].get("code_specific", {})
    cs_a = cs_cur.get("ndcg10_per_query") or []
    cs_b = cs_base.get("ndcg10_per_query") or []
    if cs_a and cs_b and len(cs_a) == len(cs_b):
        p_cs = permutation_test(cs_a, cs_b, n_iter=10000)
        delta_cs = (
            sum(cs_a) / len(cs_a) - sum(cs_b) / len(cs_b)
        )
    else:
        p_cs = 1.0
        delta_cs = 0.0

    macro_pass = delta_macro >= 0.003 and p_macro < 0.10
    cs_pass = delta_cs >= 0.005 and p_cs < 0.10

    reason = {
        "macro": {
            "delta": round(delta_macro, 4),
            "p_value": round(p_macro, 4),
            "pass": macro_pass,
        },
        "code_specific": {
            "delta": round(delta_cs, 4),
            "p_value": round(p_cs, 4),
            "pass": cs_pass,
        },
    }
    if macro_pass or cs_pass:
        return "SHIP_DEFAULT_ON", reason
    # Fallback: opt-in only.
    return "SHIP_OPT_IN", reason


def main() -> dict:
    use_vectors = "--vectors" in sys.argv

    runs: list[dict] = []
    for label, env in CONFIGS:
        print(f"[r14] running {label} (use_vectors={use_vectors}) ...",
              file=sys.stderr)
        runs.append(_evaluate_config(label, env, use_vectors=use_vectors))

    baseline = runs[0]
    out = {
        "ranker": "hybrid" if use_vectors else "bm25_only",
        "runs": [
            {k: v for k, v in r.items() if k != "per_query"}
            for r in runs
        ],
        "comparisons": [],
    }

    for cand in runs[1:]:
        decision, reason = _ship_decision(baseline, cand)
        out["comparisons"].append({
            "candidate": cand["label"],
            "decision": decision,
            "reason": reason,
        })
        print(_diff_table(baseline, cand))
        print(f"\n→ {cand['label']}: {decision}")
        print(f"   reason: {reason}")

    return out


if __name__ == "__main__":
    out = main()
    save_label = None
    for i, a in enumerate(sys.argv[1:]):
        if a == "--save" and i + 1 < len(sys.argv) - 1:
            save_label = sys.argv[i + 2]
        elif a.startswith("--save="):
            save_label = a.split("=", 1)[1]
    if save_label:
        bench = ROOT / ".bench"
        bench.mkdir(exist_ok=True)
        path = bench / f"r14_{save_label}.json"
        path.write_text(json.dumps(out, indent=2))
        print(f"\nSaved: {path}")
