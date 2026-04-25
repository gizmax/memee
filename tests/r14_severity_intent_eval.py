"""R14 A/B harness — severity-weighted intent boost.

Background
----------
The R11 algo audit suggested scaling the existing intent boost block in
``search.py`` by ``AntiPattern.severity`` when the query verb implies
*danger* ("fix", "secure", "harden", "avoid", "prevent", "mitigate",
"patch"). On the old 55q harness it measured Δ=+0.0021 (p≈0.5, n=10 too
small). The 207q harness has n=32 in the ``anti_pattern_intent`` cluster,
which is enough power to test it properly.

This module reuses the seed corpus + labelled query set from
``tests.retrieval_eval`` but seeds the database with **explicit per-AP
severities** (decoupled from maturity, so a critical AP doesn't already
win on confidence×maturity alone). Running once with the flag OFF and
once with it ON against the same seed gives paired per-query nDCG@10
scores; we run a 10k-iteration paired permutation test on the
``anti_pattern_intent`` cluster.

Ship rule
---------
* ΔnDCG@10 on ``anti_pattern_intent`` ≥ +0.015 at p < 0.10  → default ON.
* Macro nDCG@10 must not regress by more than 0.005.
* Otherwise ship as opt-in (``MEMEE_SEVERITY_INTENT_BOOST=1``).

Run
---
    .venv/bin/python -m tests.r14_severity_intent_eval                 # full A/B
    .venv/bin/python -m tests.r14_severity_intent_eval --save r14      # write JSON
    .venv/bin/python -m tests.r14_severity_intent_eval --verbose       # per-query

Output is JSON suitable for diffing across branches.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MEMEE_TELEMETRY", "0")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Reuse the canonical corpus + labelled queries from the main retrieval
# eval. We deliberately do NOT redefine them — keeping them here would
# silently drift from the main bench and invalidate this experiment.
from tests.retrieval_eval import (  # noqa: E402
    CLUSTER_NAMES,
    CORPUS,
    QUERIES,
    _ndcg_at_k,
    _recall_at_k,
    _mrr,
    permutation_test,
)


# ── R14 explicit severity map ──────────────────────────────────────────────
#
# The default ``_seed`` in ``tests.retrieval_eval`` assigns
# ``severity="medium"`` to every anti-pattern. That's fine for the macro
# bench but useless for *this* experiment — we'd be measuring "1.10 vs
# 1.10" and seeing zero effect.
#
# We assign a realistic severity per AP id below. Critical = RCE,
# secret leak, auth bypass; high = injection vector, blocking I/O,
# data leak, unsafe deserialisation; medium = quality-of-life pitfalls,
# logging, defensive bugs; low = style nits / minor footguns.
#
# We deliberately decouple severity from maturity:
#   - some validated APs are critical (e.g. r12_ap_pickle_untrusted is
#     validated maturity but pickle.loads on untrusted input is RCE)
#   - some canon APs are medium/low (e.g. test02 / fe01 are common but
#     not dangerous in the danger-verb sense)
#
# That decoupling matters because the call-site says "make sure the
# harness doesn't accidentally include critical anti-patterns that
# already win on confidence + maturity". If severity ≡ maturity the
# severity boost provides no new signal.
SEVERITY_MAP: dict[str, str] = {
    # ── Block: r12_ap_* (R12 expansion, severity-leaning, 22 entries) ──
    "r12_ap_rsa_key_in_env":         "critical",  # secret leak
    "r12_ap_unverified_webhooks":    "high",
    "r12_ap_pickle_untrusted":       "critical",  # RCE
    "r12_ap_yaml_load_unsafe":       "critical",  # RCE
    "r12_ap_xxe_xml":                "high",
    "r12_ap_open_redirect":          "medium",
    "r12_ap_ssrf_url_fetch":         "high",
    "r12_ap_timing_unsafe_compare":  "high",
    "r12_ap_path_traversal":         "high",
    "r12_ap_cors_star_creds":        "high",
    "r12_ap_jwt_secret_in_repo":     "critical",  # secret leak
    "r12_ap_disable_csrf_for_api":   "high",
    "r12_ap_mass_assign":            "high",      # auth bypass via is_admin
    "r12_ap_log_pii":                "medium",
    "r12_ap_dependency_pin_caret":   "medium",
    "r12_ap_unbounded_recursion":    "medium",
    "r12_ap_regex_redos":            "medium",
    "r12_ap_sleep_in_request":       "medium",
    "r12_ap_swallow_exceptions":     "low",
    "r12_ap_global_db_session":      "high",      # corruption risk
    "r12_ap_no_ttl_redis":           "medium",
    "r12_ap_kubectl_apply_in_ci":    "medium",
    # ── Block: r12_dr_* (diff_review APs, 16 entries) ──
    "r12_dr_requests_no_timeout":    "high",      # connection storm
    "r12_dr_print_in_handler":       "low",
    "r12_dr_assert_for_validation":  "high",      # disappears under -O
    "r12_dr_subprocess_shell_true":  "critical",  # cmd injection
    "r12_dr_str_format_sql":         "critical",  # SQL injection
    "r12_dr_md5_password":           "critical",  # cracked credential
    "r12_dr_pickle_in_diff":         "critical",  # RCE
    "r12_dr_eval_user_input":        "critical",  # RCE
    "r12_dr_open_no_close":          "low",
    "r12_dr_threading_lock_typo":    "medium",
    "r12_dr_console_log_in_pr":      "low",
    "r12_dr_setstate_mutate":        "medium",
    "r12_dr_useeffect_no_deps":      "medium",
    "r12_dr_time_sleep_async":       "medium",
    "r12_dr_dangerously_set_html":   "high",      # XSS
    "r12_dr_fs_unlink_user_path":    "high",      # destructive
    # ── Block: legacy domain APs ──
    "test02": "low",
    "test06": "medium",
    "test13": "low",
    "db01":   "high",      # SQL injection family
    "db06":   "medium",
    "db11":   "medium",
    "db16":   "medium",
    "api02":  "medium",
    "api07":  "medium",
    "api11":  "low",
    "api17":  "high",      # auth nit
    "sec01":  "critical",  # eval-style RCE
    "sec03":  "critical",  # secret leak
    "sec06":  "high",
    "sec10":  "high",
    "sec15":  "medium",
    "perf06": "medium",
    "perf12": "low",
    "fe01":   "low",
    "fe04":   "medium",
    "fe07":   "medium",
    "fe11":   "medium",
    "ops03":  "medium",
    "ops07":  "medium",
    "ops13":  "medium",
    "ml05":   "low",
    # ── Block: r12_ml_* (multilingual_lite APs, 4 entries) ──
    "r12_ml_cs_n1_orm":     "medium",
    "r12_ml_cs_docker_root": "high",   # container escape
    "r12_ml_de_n1_orm":     "medium",
    "r12_ml_de_docker_root": "high",
}


def _seed_with_severities(session) -> None:
    """Insert the corpus, but use ``SEVERITY_MAP`` for AP rows.

    Anti-pattern rows missing from ``SEVERITY_MAP`` fall back to
    ``severity="medium"`` (matches the legacy ``_seed`` in
    ``retrieval_eval``). That keeps the bench fully reproducible if the
    corpus grows new APs without updating the map — they just don't
    contribute differential signal.
    """
    from memee.storage.models import AntiPattern, Decision, Memory

    missing: list[str] = []
    for c in CORPUS:
        m = Memory(
            id=c["id"],
            type=c["type"],
            maturity=c.get("maturity", "validated"),
            title=c["title"],
            content=c["content"],
            tags=c["tags"],
            confidence_score=0.7,
        )
        session.add(m)
        session.flush()
        if c["type"] == "anti_pattern":
            sev = SEVERITY_MAP.get(c["id"])
            if sev is None:
                missing.append(c["id"])
                sev = "medium"
            session.add(
                AntiPattern(
                    memory_id=m.id,
                    severity=sev,
                    trigger=c["title"],
                    consequence=c["content"][:80],
                    alternative="see content",
                )
            )
        elif c["type"] == "decision":
            session.add(
                Decision(
                    memory_id=m.id,
                    chosen=c["title"],
                    alternatives=[],
                    criteria=[],
                )
            )
    session.commit()
    if missing:
        # Surfacing missing entries is informational — we still proceeded
        # with the medium fallback. The harness output reports the count
        # so you can spot corpus drift.
        print(
            f"[r14] {len(missing)} AP corpus entries missing from SEVERITY_MAP "
            f"(falling back to medium): {missing[:5]}{'...' if len(missing) > 5 else ''}",
            file=sys.stderr,
        )


def _fresh_db():
    from memee.storage.database import get_engine, get_session, init_db
    from memee.storage.models import Organization

    tmp = Path(tempfile.mkdtemp()) / "eval.db"
    os.environ["MEMEE_DB_PATH"] = str(tmp)
    engine = init_db(get_engine(tmp))
    session = get_session(engine)
    org = Organization(name="r14-eval-org")
    session.add(org)
    session.commit()
    return engine, session, org


def _evaluate_run(flag_value: str, *, use_vectors: bool = False) -> dict:
    """Run the full QUERIES set against a fresh DB with the given flag.

    Returns a dict whose ``per_query`` is keyed in the same order as
    ``QUERIES`` so the caller can pair them directly across runs.

    Note: we rebuild the DB per run (not per arm) because FTS5 is
    deterministic on identical input. The two arms therefore differ
    only in the multiplier branch; everything else (BM25 ranks, vector
    ranks if enabled, RRF, project boost, title boost) is byte-identical.
    """
    os.environ["MEMEE_SEVERITY_INTENT_BOOST"] = flag_value

    # search.py reads the env var lazily (per call to
    # ``_severity_intent_enabled``), so a setenv between arms is enough
    # — no module reimport needed.
    from memee.engine.search import search_memories

    _, session, _ = _fresh_db()
    _seed_with_severities(session)

    per_query: list[dict] = []
    per_cluster: dict[str, list[float]] = {n: [] for n in CLUSTER_NAMES}
    ndcgs: list[float] = []

    for sample in QUERIES:
        rel_dict: dict[str, int] = {mid: g for mid, g in sample["rel"]}
        results = search_memories(
            session, sample["q"], limit=10, use_vectors=use_vectors
        )
        retrieved_ids = [r["memory"].id for r in results]
        ndcg10 = _ndcg_at_k(retrieved_ids, rel_dict, 10)
        recall5 = _recall_at_k(retrieved_ids, rel_dict, 5)
        mrr = _mrr(retrieved_ids, rel_dict)
        ndcgs.append(ndcg10)
        cl = sample.get("cluster")
        if cl in per_cluster:
            per_cluster[cl].append(ndcg10)
        per_query.append({
            "q": sample["q"],
            "cluster": cl,
            "ndcg10": round(ndcg10, 4),
            "recall5": round(recall5, 4),
            "mrr": round(mrr, 4),
            "retrieved_top5": retrieved_ids[:5],
        })

    session.close()
    n = len(QUERIES)
    return {
        "flag": flag_value,
        "macro_ndcg10": round(sum(ndcgs) / n, 4),
        "per_cluster_ndcg10": {
            name: {
                "n": len(scores),
                "ndcg10": round(sum(scores) / len(scores), 4) if scores else 0.0,
                "ndcg10_per_query": [round(s, 4) for s in scores],
            }
            for name, scores in per_cluster.items()
        },
        "per_query": per_query,
    }


def run_ab(use_vectors: bool = False, n_iter: int = 10000) -> dict:
    """Run baseline (flag off) vs candidate (flag on) and report stats."""
    baseline = _evaluate_run("0", use_vectors=use_vectors)
    candidate = _evaluate_run("1", use_vectors=use_vectors)

    # Macro delta
    macro_delta = candidate["macro_ndcg10"] - baseline["macro_ndcg10"]

    # Per-cluster paired stats. Pair queries by index — both runs walked
    # ``QUERIES`` in order, so per_query[i] corresponds across arms.
    cluster_stats: dict[str, dict] = {}
    for name in CLUSTER_NAMES:
        a = candidate["per_cluster_ndcg10"][name]["ndcg10_per_query"]
        b = baseline["per_cluster_ndcg10"][name]["ndcg10_per_query"]
        if not a or not b or len(a) != len(b):
            cluster_stats[name] = {"skipped": "n_mismatch_or_empty",
                                   "n": len(a)}
            continue
        delta = sum(a) / len(a) - sum(b) / len(b)
        p = permutation_test(a, b, n_iter=n_iter, seed=0)
        cluster_stats[name] = {
            "n": len(a),
            "baseline_ndcg10": round(sum(b) / len(b), 4),
            "candidate_ndcg10": round(sum(a) / len(a), 4),
            "delta_ndcg10": round(delta, 4),
            "p_value": round(p, 4),
        }

    # Ship decision
    ap_intent = cluster_stats.get("anti_pattern_intent", {})
    ap_delta = ap_intent.get("delta_ndcg10", 0.0)
    ap_p = ap_intent.get("p_value", 1.0)
    macro_safe = macro_delta >= -0.005

    if ap_delta >= 0.015 and ap_p < 0.10 and macro_safe:
        ship = "default_on"
        reason = (
            f"Δ={ap_delta:+.4f} ≥ +0.015 at p={ap_p:.4f} < 0.10, "
            f"macro Δ={macro_delta:+.4f} within budget"
        )
    elif macro_safe:
        ship = "opt_in"
        reason = (
            f"Δ={ap_delta:+.4f} (p={ap_p:.4f}) didn't clear bar; "
            f"shipping as MEMEE_SEVERITY_INTENT_BOOST=1 opt-in"
        )
    else:
        ship = "do_not_ship"
        reason = (
            f"macro regressed Δ={macro_delta:+.4f} (>{0.005}); revert"
        )

    return {
        "n_queries": len(QUERIES),
        "n_corpus": len(CORPUS),
        "ranker": "hybrid" if use_vectors else "bm25_only",
        "baseline_macro_ndcg10": baseline["macro_ndcg10"],
        "candidate_macro_ndcg10": candidate["macro_ndcg10"],
        "macro_delta": round(macro_delta, 4),
        "cluster_stats": cluster_stats,
        "ship": ship,
        "ship_reason": reason,
        "baseline_per_query": baseline["per_query"],
        "candidate_per_query": candidate["per_query"],
    }


# ── CLI ────────────────────────────────────────────────────────────────────


def _parse_arg(name: str, default=None) -> str | None:
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(f"{name}="):
            return a.split("=", 1)[1]
    return default


def _print_summary(res: dict) -> None:
    print(json.dumps({
        "n_queries": res["n_queries"],
        "ranker": res["ranker"],
        "baseline_macro_ndcg10": res["baseline_macro_ndcg10"],
        "candidate_macro_ndcg10": res["candidate_macro_ndcg10"],
        "macro_delta": res["macro_delta"],
        "cluster_stats": res["cluster_stats"],
        "ship": res["ship"],
        "ship_reason": res["ship_reason"],
    }, indent=2))


def _print_cluster_table(res: dict) -> None:
    print()
    print(f"{'cluster':<22} {'n':>4} {'baseline':>10} {'candidate':>10} "
          f"{'Δ nDCG@10':>11} {'p':>8}")
    print("-" * 70)
    for name in CLUSTER_NAMES:
        body = res["cluster_stats"].get(name, {})
        if "skipped" in body:
            print(f"{name:<22} {body.get('n', 0):>4} {'-':>10} {'-':>10} "
                  f"{'-':>11} {body['skipped']:>8}")
            continue
        print(
            f"{name:<22} {body['n']:>4} "
            f"{body['baseline_ndcg10']:>10.4f} "
            f"{body['candidate_ndcg10']:>10.4f} "
            f"{body['delta_ndcg10']:>+11.4f} "
            f"{body['p_value']:>8.4f}"
        )


if __name__ == "__main__":
    use_vectors = "--vectors" in sys.argv
    n_iter = int(_parse_arg("--n-iter", "10000"))
    res = run_ab(use_vectors=use_vectors, n_iter=n_iter)

    _print_summary(res)
    _print_cluster_table(res)

    save_label = _parse_arg("--save")
    if save_label:
        bench_dir = ROOT / ".bench"
        bench_dir.mkdir(exist_ok=True)
        safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in save_label)
        out = bench_dir / f"r14_{safe}.json"
        out.write_text(json.dumps(res, indent=2))
        print(f"\nSaved: {out}")

    if "--verbose" in sys.argv:
        print("\nPer-query (anti_pattern_intent only):")
        cands = {q["q"]: q for q in res["candidate_per_query"]}
        for qb in res["baseline_per_query"]:
            if qb["cluster"] != "anti_pattern_intent":
                continue
            qc = cands.get(qb["q"], {})
            d = qc.get("ndcg10", 0.0) - qb["ndcg10"]
            sign = "+" if d >= 0 else ""
            print(f"  Δ={sign}{d:.4f}  base={qb['ndcg10']:.4f}  "
                  f"cand={qc.get('ndcg10', 0.0):.4f}  q={qb['q']}")
