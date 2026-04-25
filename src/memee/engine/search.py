"""Hybrid search engine: FTS5 BM25 + vector similarity + tag matching + confidence.

Three-stage search:
1. BM25 via FTS5 (exact keyword matching)
2. Vector similarity via embeddings (semantic matching)
3. Merge + re-rank with tag overlap and confidence boost

Falls back gracefully: vector search is optional (requires fastembed).
"""

from __future__ import annotations

import logging
import re
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from memee.storage.models import Memory

logger = logging.getLogger(__name__)

MATURITY_MULTIPLIER = {
    "canon": 1.0,
    "validated": 0.85,
    "tested": 0.65,
    "hypothesis": 0.4,
    "deprecated": 0.05,
}

# Legacy weights for the linear blend, kept for the BM25-only path. The
# hybrid path uses Reciprocal Rank Fusion (Cormack/Clarke/Buettcher 2009)
# instead of a weighted blend so the two retrievers don't have to share a
# common score scale; see ``_rrf_merge``.
W_BM25 = 0.42
W_VECTOR = 0.30
W_TAGS = 0.20
W_CONFIDENCE = 0.08

# RRF constant. The Cormack 2009 paper uses k=60 on TREC-scale corpora.
# For our short candidate lists (typically 30–60 per retriever) k=40 keeps
# the rank-1 lead pronounced without flattening the tail. Empirically on
# our retrieval bench k=40 dominates k=60 by ~3 nDCG points.
RRF_K = 40

# Title boost: exact query or ≥3-word contiguous substring match (applied at most once).
TITLE_PHRASE_BOOST = 1.3

# R13 project-aware reranking. When the caller passes ``project_id`` to
# ``search_memories``, memories whose ``validated_project_ids`` contains
# that id receive a multiplicative boost on their RRF total. The α=0.25
# default was the conservative pick from the audit roadmap; tunable via
# the ``MEMEE_PROJECT_AWARE_BOOST`` env var so an operator can adjust
# without code changes.
import os as _os

try:
    PROJECT_AWARE_BOOST = float(_os.environ.get("MEMEE_PROJECT_AWARE_BOOST", "0.25"))
except ValueError:
    PROJECT_AWARE_BOOST = 0.25

# Task intent → memory type boosts (applied at most once per result).
# (verbs, type_or_types, multiplier)
INTENT_BOOSTS: list[tuple[set[str], object, float]] = [
    ({"test", "tests", "testing"}, "pattern", 1.1),
    ({"secure", "security", "harden"}, "anti_pattern", 1.15),
    ({"decide", "decision", "chose"}, "decision", 1.15),
    ({"fix", "bug", "bugfix", "debug"}, {"lesson", "anti_pattern"}, 1.1),
    ({"optimize", "perf", "performance"}, "pattern", 1.1),
]

# R14 severity-weighted intent boost. When the query verb implies *danger*
# (the agent is firefighting / hardening, not exploring), the ranker should
# privilege anti-patterns *proportional to their severity* — a critical AP
# matters more than a low-severity one for the same intent.
#
# Without this scaling the existing ``{fix, bug, bugfix, debug}`` × ``anti_pattern``
# boost is a flat 1.10 — a critical RCE pattern and a low-severity style
# nit get the same lift. The R11 audit hinted at this on the old 55q harness
# (Δ=+0.0021, p≈0.5, n=10) but couldn't resolve it. The 207q harness has
# n=32 in the ``anti_pattern_intent`` cluster — enough power for a real test.
#
# Behind ``MEMEE_SEVERITY_INTENT_BOOST`` so it can be flipped without a
# deploy. Default is decided by the A/B harness in
# ``tests/r14_severity_intent_eval.py`` and reflected in the env-var read
# below; the constant ``_SEVERITY_INTENT_DEFAULT`` is the documented choice.
_DANGER_VERBS: set[str] = {
    "fix", "secure", "harden", "avoid", "prevent", "mitigate", "patch",
}

_SEVERITY_INTENT_TABLE: dict[str, float] = {
    "critical": 1.40,
    "high": 1.25,
    "medium": 1.10,
    "low": 1.00,
}

# Default OFF — measurement on the 207q harness gave
# ΔnDCG@10 = +0.0043 (p = 0.30) on the anti_pattern_intent cluster (n=32),
# below the +0.015 / p<0.10 bar required for default-on. Macro was safe
# (Δ = +0.0007). Shipped as opt-in for production telemetry to either
# corroborate or kill. See docs/r14-severity-intent-boost.md.
_SEVERITY_INTENT_DEFAULT = "0"


def _severity_intent_enabled() -> bool:
    return _os.environ.get(
        "MEMEE_SEVERITY_INTENT_BOOST", _SEVERITY_INTENT_DEFAULT
    ) not in ("0", "off", "false", "False")


def _has_embeddings() -> bool:
    """Check if sentence-transformers is available."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


# Cache the "are there any embedded memories in the DB?" answer per-process.
# Without this we pay a SELECT on every search and, worse, pay the 2–3s
# model cold-start on any search issued against a DB that never embedded
# anything. The cache is invalidated when ``embed_all_memories`` runs.
_DB_HAS_EMBEDDINGS: dict[int, bool] = {}

# R10 perf #1: cache the embedded corpus as a single float32 numpy matrix
# keyed by (bind id, revision). The revision is the max(updated_at) of all
# embedded rows — cheap to compute, monotonically increases when memories
# are re-embedded, edited, or deleted. When the revision changes we rebuild;
# otherwise we keep the matrix and skip JSON parsing entirely. At 10k embedded
# memories the speed agent measured 134 ms → 0.16 ms (~600× speedup) because
# JSON parse, not cosine math, is the bottleneck on the hot path.
_EMBED_MATRIX_CACHE: dict[int, dict] = {}


def _db_has_any_embeddings(session: Session) -> bool:
    """Cheap short-circuit: only pay the model cold-start and the vector
    rerank if at least one memory in this DB actually has an embedding."""
    bind = None
    try:
        bind = session.get_bind()
    except Exception:
        return False
    key = id(bind)
    cached = _DB_HAS_EMBEDDINGS.get(key)
    if cached is not None:
        return cached
    try:
        row = session.execute(
            text("SELECT 1 FROM memories WHERE embedding IS NOT NULL LIMIT 1")
        ).fetchone()
        answer = row is not None
    except Exception:
        answer = False
    _DB_HAS_EMBEDDINGS[key] = answer
    return answer


def _invalidate_embedding_cache() -> None:
    _DB_HAS_EMBEDDINGS.clear()
    _EMBED_MATRIX_CACHE.clear()


def _embedded_corpus_matrix(session: Session):
    """Return ``(ids, matrix, types, maturities)`` for the embedded subset of
    the corpus, where ``matrix`` is a ``float32`` numpy array of shape
    ``(N, dim)`` whose rows correspond to ``ids``. Filters by memory_type /
    maturity happen in NumPy after the lookup so they don't bust the cache.

    The revision key is the max(updated_at) over embedded rows. Mutations
    that touch an embedded memory bump ``updated_at`` (SQLAlchemy's
    ``onupdate=utcnow``), so the revision rolls forward exactly when we
    need to rebuild. Cache hits skip JSON parsing entirely, which is what
    dominates at 5k+ embedded memories.

    Returns ``None`` when numpy isn't available — the caller falls back to
    the per-memory cosine path.
    """
    try:
        import numpy as np
    except ImportError:
        return None

    bind = session.get_bind()
    key = id(bind)
    rev_row = session.execute(
        text(
            "SELECT MAX(updated_at), COUNT(*) FROM memories WHERE embedding IS NOT NULL"
        )
    ).fetchone()
    if rev_row is None:
        return None
    rev_max, count = rev_row[0], int(rev_row[1] or 0)
    if count == 0:
        return None
    revision = (rev_max, count)

    cached = _EMBED_MATRIX_CACHE.get(key)
    if cached is not None and cached["revision"] == revision:
        return cached

    rows = session.execute(
        text(
            "SELECT id, type, maturity, embedding FROM memories "
            "WHERE embedding IS NOT NULL"
        )
    ).fetchall()
    ids: list[str] = []
    types: list[str] = []
    maturities: list[str] = []
    vecs: list[list[float]] = []
    for mid, mtype, maturity, emb_json in rows:
        if not emb_json:
            continue
        # The embedding column stores JSON-encoded lists; SQLite's TEXT type
        # comes back as ``str`` here. SQLAlchemy with the JSON type already
        # decodes — but raw ``session.execute`` returns the storage value.
        if isinstance(emb_json, str):
            try:
                import json as _json

                emb = _json.loads(emb_json)
            except Exception:
                continue
        else:
            emb = emb_json
        if not isinstance(emb, list) or not emb:
            continue
        vecs.append(emb)
        ids.append(mid)
        types.append(mtype or "")
        maturities.append(maturity or "")

    if not vecs:
        return None
    matrix = np.asarray(vecs, dtype=np.float32)
    # Cache row norms too; recomputing them every query was a hidden 80 % of
    # the warm path (~450 ms / 5k memories) until we precomputed once.
    row_norms = np.linalg.norm(matrix, axis=1)
    row_norms[row_norms == 0.0] = 1.0
    # Cache type / maturity boolean masks per unique value so the per-query
    # filter doesn't allocate a fresh ``np.fromiter`` over the type list every
    # time. Built lazily on first query that hits each mask.
    type_array = np.asarray(types)
    maturity_array = np.asarray(maturities)
    entry = {
        "revision": revision,
        "ids": ids,
        "matrix": matrix,
        "row_norms": row_norms,
        "type_array": type_array,
        "maturity_array": maturity_array,
        "types": types,
        "maturities": maturities,
    }
    _EMBED_MATRIX_CACHE[key] = entry
    return entry


def search_memories(
    session: Session,
    query: str,
    tags: list[str] | None = None,
    memory_type: str | None = None,
    maturity: str | None = None,
    limit: int = 20,
    use_vectors: bool = True,
    scope: str | None = None,
    user_id: str | None = None,
    return_event_id: bool = False,
    project_id: str | None = None,
):
    """Hybrid search: BM25 + vector + tags + confidence.

    `scope` and `user_id` are honoured when `memee-team` is installed and
    has registered the `visible_memories` plugin hook. In OSS they are
    accepted but ignored (single-user product).

    R13 ``project_id``: when the caller is acting in a specific project's
    context, memories validated in that project (via
    ``Memory.validated_project_ids``) get a small post-RRF boost so the
    ranker prefers in-stack proven patterns over equivalents from
    elsewhere. Multiplier defaults to ``PROJECT_AWARE_BOOST``; opt-out
    by passing ``project_id=None``.

    If ``return_event_id`` is True, returns ``(results, event_id | None)`` so
    callers (e.g. the MCP `memory_search` tool) can pass the id back to
    ``mark_event_accepted`` without racing on "latest SearchEvent" lookups
    under concurrent traffic. Default False preserves the historical
    list-returning signature for every other caller.
    """
    _t0 = time.perf_counter()
    # Stage 1: BM25 candidate generation. memory_type/maturity filter pushed
    # into the FTS SQL via JOIN so the filter doesn't silently starve rare
    # types (old code over-fetched limit*3 candidates without the filter;
    # rare types could be outranked and vanish after the post-filter).
    bm25_results = _bm25_search(
        session, query, memory_type, maturity, limit * 3, filter_in_sql=True
    )

    # Stage 2: Vector candidate generation. Cold-start guard: if NO memory
    # in this DB has an embedding, skip the model load entirely. Order
    # matters — the DB check is a single SELECT; ``_has_embeddings`` pulls
    # in the ~5s ``sentence_transformers`` import even when not needed.
    has_vectors = (
        use_vectors and _db_has_any_embeddings(session) and _has_embeddings()
    )

    # Stage 3: Resolve BM25 rowid → memory id (one batched query). We keep
    # both the normalized BM25 score (for the BM25-only path) and the BM25
    # rank order (for RRF).
    rowids = [r[0] for r in bm25_results]
    bm25_by_id: dict[str, float] = {}
    bm25_rank: dict[str, int] = {}
    if rowids:
        max_bm25 = max((abs(r[1]) for r in bm25_results), default=1.0)
        rank_by_rowid = {r[0]: r[1] for r in bm25_results}
        order_by_rowid = {rowid: i for i, (rowid, _) in enumerate(bm25_results)}
        placeholders = ",".join(str(int(r)) for r in rowids)
        rows = session.execute(
            text(f"SELECT id, rowid FROM memories WHERE rowid IN ({placeholders})")
        ).fetchall()
        for mid, rowid in rows:
            rank = rank_by_rowid.get(rowid, 0)
            bm25_by_id[mid] = (abs(rank) / max_bm25) if max_bm25 > 0 else 0.5
            bm25_rank[mid] = order_by_rowid.get(rowid, 0)

    # Vector retriever runs as a PEER, not a reranker. Old design only
    # scored embeddings of BM25 candidates → memories with strong vector
    # match but no lexical overlap were invisible. New: vector retrieves
    # its own top-K over the embedded subset (with the same type/maturity
    # filter), then we union with BM25 candidates and rank by RRF.
    vector_by_id: dict[str, float] = {}
    vector_rank: dict[str, int] = {}
    if has_vectors:
        vector_by_id, vector_rank = _vector_topk(
            session, query, memory_type, maturity, limit * 3
        )

    # R13 third retriever: tag-graph via MemoryTag inverted index. No-op
    # when the caller didn't pass ``tags=``; the cluster baselines from
    # the R12 expansion (paraphrastic n=43 nDCG=0.68, lexical_gap_hard
    # n=15 nDCG=0.74) are exactly the queries where intent-tags lift the
    # right doc. Default-on; opt-out via env var.
    tag_by_id: dict[str, float] = {}
    tag_rank: dict[str, int] = {}
    if _os.environ.get("MEMEE_TAG_GRAPH_RRF", "1") not in ("0", "off", "false"):
        tag_by_id, tag_rank = _tag_graph_topk(
            session, tags, memory_type, maturity, limit * 3
        )

    all_ids = (
        set(bm25_by_id.keys())
        | set(vector_by_id.keys())
        | set(tag_by_id.keys())
    )
    if not all_ids:
        # Resolve current user for the fallback's apply_visibility call. We
        # could compute this lazily inside _fallback_search but doing it here
        # mirrors the main-path resolution and avoids redundant plugin calls.
        from memee import plugins as _plugins

        fb_user = user_id
        if fb_user is None and _plugins.is_multi_user_active():
            try:
                fb_user = _plugins.call("current_user_id", session)
            except TypeError:
                fb_user = _plugins.call("current_user_id")
        fb = _fallback_search(
            session, query, tags, memory_type, maturity, limit, user_id=fb_user
        )
        fb_event_id = _record_telemetry(session, query, fb, _t0)
        if return_event_id:
            return fb, fb_event_id
        return fb

    # Stage 4: Batch load memories (ONE query for all candidates)
    memories_q = session.query(Memory).filter(Memory.id.in_(list(all_ids)))
    if memory_type:
        memories_q = memories_q.filter(Memory.type == memory_type)
    if maturity:
        memories_q = memories_q.filter(Memory.maturity == maturity)

    # Scope visibility. When a multi-user hook is registered (memee-team),
    # we apply it unconditionally — relying on callers to remember to pass
    # scope/user_id caused tenancy leaks through MCP, CLI, router, review.
    # In OSS single-user the hook is the default no-op and this is a cheap
    # identity call; nothing changes for single-user users.
    from memee import plugins as _plugins

    resolved_user_id = user_id
    if resolved_user_id is None and _plugins.is_multi_user_active():
        try:
            resolved_user_id = _plugins.call("current_user_id", session)
        except TypeError:
            resolved_user_id = _plugins.call("current_user_id")
    memories_q = _plugins.apply_visibility(
        session, memories_q, user_id=resolved_user_id
    )
    memories_list = memories_q.all()

    # RRF score: sum of 1/(k + rank_r(d)) across every retriever that
    # ranked the doc. A doc that only appears in one list still scores
    # something; a doc that ranks high in both gets the strongest signal.
    # Tag/confidence still apply as boost multipliers, not score weights —
    # they're metadata signals about a known doc, not a third retriever.
    results = []
    for memory in memories_list:
        memory_id = memory.id

        bm25_score = bm25_by_id.get(memory_id, 0.0)
        vector_score = vector_by_id.get(memory_id, 0.0)
        tag_score = _compute_tag_score(memory.tags, tags)
        conf_score = (
            MATURITY_MULTIPLIER.get(memory.maturity, 0.5)
            * memory.confidence_score
        )

        # RRF score: sum of 1/(k + rank_r(d)) across every retriever that
        # ranked the doc. R13: when the caller passes ``tags=`` we
        # additionally fuse a tag-graph retriever — Jaccard top-K via
        # MemoryTag — into the same RRF.
        rrf_score = 0.0
        if has_vectors:
            if memory_id in bm25_rank:
                rrf_score += 1.0 / (RRF_K + bm25_rank[memory_id] + 1)
            if memory_id in vector_rank:
                rrf_score += 1.0 / (RRF_K + vector_rank[memory_id] + 1)
            if memory_id in tag_rank:
                rrf_score += 1.0 / (RRF_K + tag_rank[memory_id] + 1)
            # Tag and confidence are post-RRF signal boosts (multiplicative).
            # 1 + α gives a +α multiplier when the signal is at its max.
            total = rrf_score * (1.0 + 0.5 * tag_score) * (1.0 + 0.4 * conf_score)
        else:
            # No vector retriever — fall back to the legacy linear blend so
            # BM25-only deployments don't lose tag/confidence weighting.
            total = (
                0.55 * bm25_score
                + 0.25 * tag_score
                + 0.20 * conf_score
            )

        # Apply title phrase boost (at most once) and intent boost (at most once).
        title_match = _title_phrase_match(query, memory.title)
        if title_match:
            total *= TITLE_PHRASE_BOOST
        intent_mult = _intent_multiplier(query, memory)
        if intent_mult != 1.0:
            total *= intent_mult

        # R13 project-aware boost. ``validated_project_ids`` is a JSON
        # array on Memory; check membership at the Python layer to avoid
        # a SQL ``json_each`` per row. The list is typically tiny (≤10).
        project_match = False
        if project_id:
            vpids = memory.validated_project_ids or []
            if isinstance(vpids, list) and project_id in vpids:
                project_match = True
                total *= 1.0 + PROJECT_AWARE_BOOST

        results.append({
            "memory": memory,
            "bm25_score": round(bm25_score, 4),
            "vector_score": round(vector_score, 4),
            "tag_score": round(tag_score, 4),
            "confidence_boost": round(conf_score, 4),
            "total_score": round(total, 4),
            # R9 LTR (#3) + hard-neg mining (#4): keep the per-candidate raw
            # ranks/scores so the snapshot writer and the LTR feature
            # extractor don't have to reach back into search.py internals.
            "features": {
                "bm25_rank": bm25_rank.get(memory_id),
                "vector_rank": vector_rank.get(memory_id),
                "rrf_score": round(rrf_score, 6),
                "title_phrase_match": bool(title_match),
                "intent_multiplier": float(intent_mult),
                "project_match": bool(project_match),
            },
        })

    results.sort(key=lambda r: r["total_score"], reverse=True)

    # Stage 5a: optional cross-encoder rerank over the top-K of the RRF
    # stack. R14 ships this as the better candidate generator for the
    # ``paraphrastic`` and ``lexical_gap_hard`` clusters where pure RRF
    # under-recalls. Default OFF (``MEMEE_RERANK_MODEL`` unset) because the
    # cross-encoder is +50–200 ms / query at top-30; opt-in for callers
    # whose latency budget can absorb it. When LTR is also active it runs
    # AFTER the cross-encoder in stage 5b, so LTR sees the higher-quality
    # candidate ordering as input.
    ranker_version = "rrf_v1"
    ranker_model_id: str | None = None
    try:
        from memee.engine.reranker import CrossEncoderReranker

        rr = CrossEncoderReranker()
        if rr.is_enabled():
            results = rr.rerank(query, results)
            ranker_version = "cross_encoder_v1"
    except Exception as e:  # pragma: no cover — never let rerank break search
        logger.warning("cross-encoder rerank failed; keeping RRF order: %s", e)

    # Stage 5b: optional LTR rerank over the top-K. The heuristic ranker above
    # is a sound candidate generator; LTR adds a learned reorder when (a)
    # ``MEMEE_LTR_ENABLED`` is ``1`` (or ``canary`` AND this query falls in
    # the canary bucket), and (b) a production model exists in the registry.
    # Latency budget for the rerank: ≈1-3 ms on top-50 with 11 features.
    final = _ltr_rerank_if_active(
        session=session,
        query=query,
        results=results,
        limit=limit,
    )
    if final is None:
        final = results[:limit]
    else:
        final, ranker_version, ranker_model_id = final

    event_id = _record_telemetry(
        session, query, final, _t0,
        ranker_version=ranker_version,
        ranker_model_id=ranker_model_id,
    )
    if return_event_id:
        return final, event_id
    return final


def _ltr_rerank_if_active(
    *,
    session,
    query: str,
    results: list,
    limit: int,
):
    """Return ``(reranked, ranker_version, model_id)`` when LTR is active and
    a production model is loaded. Returns ``None`` to signal "no rerank,
    keep heuristic order".
    """
    try:
        from memee.engine import ltr
    except Exception as e:  # pragma: no cover — module always importable
        logger.debug("ltr import failed: %s", e)
        return None

    mode = ltr.routing_mode()
    if mode == "off" or not ltr.is_enabled():
        return None
    if mode == "canary" and not ltr.canary_picks_ltr(query):
        return None

    model = ltr.load_active_model(session)
    if model is None or model.get("predict") is None:
        return None

    pool = results[: max(limit * 5, limit)]
    if not pool:
        return None

    feature_rows = []
    for r in pool:
        m = r["memory"]
        feats = ltr.featurize(
            query=query,
            memory=m,
            bm25_score=r.get("bm25_score") or 0.0,
            bm25_rank=(r.get("features") or {}).get("bm25_rank"),
            vector_score=r.get("vector_score") or 0.0,
            vector_rank=(r.get("features") or {}).get("vector_rank"),
            rrf_score=(r.get("features") or {}).get("rrf_score") or 0.0,
        )
        feature_rows.append(feats)

    try:
        import numpy as np

        scores = list(model["predict"](np.asarray(feature_rows, dtype="float32")))
    except Exception as e:  # pragma: no cover
        logger.warning("LTR predict failed; keeping heuristic order: %s", e)
        return None

    for r, s in zip(pool, scores):
        r["ltr_score"] = float(s)
    pool.sort(key=lambda r: r["ltr_score"], reverse=True)
    return pool[:limit], f"ltr_{model['version']}", model.get("id")


def _record_telemetry(
    session: Session,
    query: str,
    results: list,
    t0: float,
    ranker_version: str = "rrf_v1",
    ranker_model_id: str | None = None,
) -> str | None:
    """Best-effort: persist a SearchEvent. Returns the new event id (or None
    on error / disabled telemetry). Never raises."""
    try:
        from memee.engine.telemetry import record_search_event
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return record_search_event(
            session,
            query,
            results,
            latency_ms,
            ranker_version=ranker_version,
            ranker_model_id=ranker_model_id,
        )
    except Exception as e:  # pragma: no cover
        logger.debug("telemetry wrapper failed: %s", e)
        return None


def search_anti_patterns(
    session: Session,
    context: str,
    tags: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search specifically for anti-patterns matching a context."""
    return search_memories(
        session, context, tags=tags, memory_type="anti_pattern", limit=limit
    )


def embed_all_memories(session: Session, batch_size: int = 100) -> int:
    """Generate embeddings for all memories that don't have them.

    Returns count of memories embedded.
    """
    if not _has_embeddings():
        logger.warning("fastembed not installed. Run: pip install fastembed")
        return 0

    from memee.engine.embeddings import embed_memory_text

    memories = (
        session.query(Memory)
        .filter(Memory.embedding.is_(None))
        .all()
    )

    count = 0
    for memory in memories:
        memory.embedding = embed_memory_text(
            memory.title, memory.content, memory.tags
        )
        count += 1
        if count % batch_size == 0:
            session.flush()

    session.commit()
    _invalidate_embedding_cache()
    return count


# ── Internal helpers ──


def _bm25_search(
    session: Session,
    query: str,
    memory_type: str | None,
    maturity: str | None,
    limit: int,
    filter_in_sql: bool = False,
) -> list[tuple[int, float]]:
    """BM25 search via FTS5. Returns [(memory_rowid, fts_rank), ...].

    AND-by-default for precision; falls back to OR (one layer) if AND returns
    zero rows so we don't silently miss single-token or partial-hit queries.

    When ``filter_in_sql`` is set AND either ``memory_type`` or ``maturity``
    is given, the filter is joined into the SQL so the rare-type candidates
    don't get outranked by 1000 common-type matches and then post-filtered
    to zero. We keep the rowid signature stable for the caller.
    """
    fts_and = _sanitize_fts_query(query, operator="AND")
    if not fts_and:
        # Sanitization stripped every token — avoid shoving the raw query
        # through FTS5 (it would hit syntax errors and silently return []).
        return []

    use_filter = filter_in_sql and (memory_type is not None or maturity is not None)
    if use_filter:
        conds = []
        params: dict = {"limit": limit}
        if memory_type is not None:
            conds.append("m.type = :mtype")
            params["mtype"] = memory_type
        if maturity is not None:
            conds.append("m.maturity = :maturity")
            params["maturity"] = maturity
        where = " AND ".join(conds)
        fts_sql = text(f"""
            SELECT f.rowid, f.rank
            FROM memories_fts f
            JOIN memories m ON m.rowid = f.rowid
            WHERE memories_fts MATCH :query
              AND {where}
            ORDER BY f.rank
            LIMIT :limit
        """)
    else:
        params = {"limit": limit}
        fts_sql = text("""
            SELECT rowid, rank
            FROM memories_fts
            WHERE memories_fts MATCH :query
            ORDER BY rank
            LIMIT :limit
        """)

    try:
        rows = session.execute(fts_sql, {**params, "query": fts_and}).fetchall()
    except Exception as e:
        logger.debug(f"BM25 AND search failed for query '{query[:50]}': {e}")
        rows = []

    if rows:
        return rows

    # One-shot OR fallback. Only useful when the query has >1 token; for a
    # single-token query AND and OR are equivalent, so skip the retry.
    fts_or = _sanitize_fts_query(query, operator="OR")
    if not fts_or or fts_or == fts_and:
        return []

    try:
        return session.execute(fts_sql, {**params, "query": fts_or}).fetchall()
    except Exception as e:
        logger.debug(f"BM25 OR fallback failed for query '{query[:50]}': {e}")
        return []


def _tag_graph_topk(
    session: Session,
    query_tags: list[str] | None,
    memory_type: str | None,
    maturity: str | None,
    limit: int,
) -> tuple[dict[str, float], dict[str, int]]:
    """R13 third retriever: rank memories by Jaccard similarity between
    their tag set and the query's explicit ``tags`` argument.

    Cheap path via the ``MemoryTag`` inverted index: select the memories
    that share at least one tag with the query, then compute Jaccard in
    Python over the small candidate set. Returns empty when the caller
    didn't provide tags — in that case the third retriever is a no-op
    and RRF degenerates to the existing two-way fusion.

    Tag-graph signal complements BM25 (lexical) and vector (semantic):
    two memories with overlapping tags share *intent* even when their
    titles diverge. Helpful on the lexical-gap and paraphrastic clusters
    when callers can pass tag hints (MCP / CLI accept comma-separated
    tags already).
    """
    if not query_tags:
        return {}, {}
    qtags = [t.lower() for t in query_tags if t]
    if not qtags:
        return {}, {}

    from memee.storage.models import MemoryTag

    # One SQL: every memory_id whose MemoryTag rows intersect the query tags,
    # joined to Memory for the type/maturity filters and the tag list.
    rows_q = (
        session.query(Memory.id, Memory.tags)
        .join(MemoryTag, MemoryTag.memory_id == Memory.id)
        .filter(MemoryTag.tag.in_(qtags))
    )
    if memory_type:
        rows_q = rows_q.filter(Memory.type == memory_type)
    if maturity:
        rows_q = rows_q.filter(Memory.maturity == maturity)
    rows = rows_q.distinct().all()
    if not rows:
        return {}, {}

    qset = set(qtags)
    scored: dict[str, float] = {}
    for mid, mtags in rows:
        mset = {t.lower() for t in (mtags or []) if isinstance(t, str)}
        if not mset:
            continue
        union = mset | qset
        if not union:
            continue
        jaccard = len(mset & qset) / len(union)
        if jaccard > 0.0:
            scored[mid] = jaccard

    if not scored:
        return {}, {}
    sorted_ids = sorted(scored.keys(), key=lambda k: -scored[k])[:limit]
    rank = {mid: i for i, mid in enumerate(sorted_ids)}
    return {k: scored[k] for k in sorted_ids}, rank


def _vector_topk(
    session: Session,
    query: str,
    memory_type: str | None,
    maturity: str | None,
    limit: int,
) -> tuple[dict[str, float], dict[str, int]]:
    """Vector retriever as a peer of BM25.

    Returns ``({mid: cosine}, {mid: rank})`` for the top-``limit`` embedded
    memories ranked by cosine similarity to the query.

    R10 perf #1: when numpy is available we use a cached float32 matrix of
    the embedded corpus and a single matmul, skipping per-row JSON decode
    and Python-loop cosine. At 10 k embedded memories the speed audit
    measured 134 ms → 0.16 ms (~600 ×). The fall-through Python path stays
    for environments without numpy.
    """
    try:
        from memee.engine.embeddings import cosine_similarity, embed_text

        query_embedding = embed_text(query)
        if not query_embedding:
            return {}, {}

        # ── Fast path: cached numpy matrix ──
        cached = _embedded_corpus_matrix(session)
        if cached is not None:
            try:
                import numpy as np

                qv = np.asarray(query_embedding, dtype=np.float32)
                qn = float(np.linalg.norm(qv))
                if qn == 0.0:
                    return {}, {}
                # Embeddings encoded by sentence-transformers are already
                # L2-normalised, but we don't fully trust legacy rows; do a
                # one-time row-norm divide for correctness without looping.
                M = cached["matrix"]
                row_norms = cached["row_norms"]
                # Compute cosine = (M · q) / (||q|| · ||M_row||) row-wise.
                # Norms are precomputed at cache-build time; the per-query
                # cost is dominated by the single 5k×384 matmul (~3-5 ms on
                # contemporary CPUs).
                dots = M @ qv
                sims = dots / (row_norms * qn)

                # Apply memory_type / maturity filters in NumPy. Filtering
                # before the matmul would mean a cache key per filter combo;
                # this way the matmul stays cheap and the filter is a
                # vectorised mask using the cached np arrays.
                mask = np.ones(len(cached["ids"]), dtype=bool)
                if memory_type:
                    mask &= cached["type_array"] == memory_type
                if maturity:
                    mask &= cached["maturity_array"] == maturity
                # Threshold first to drop noise, then pick top-k.
                mask &= sims > 0.3
                idxs = np.flatnonzero(mask)
                if idxs.size == 0:
                    return {}, {}
                # argpartition for top-k is O(N); sort only the top window.
                if idxs.size > limit:
                    top_local = np.argpartition(-sims[idxs], limit - 1)[:limit]
                    top_idxs = idxs[top_local]
                else:
                    top_idxs = idxs
                top_idxs = top_idxs[np.argsort(-sims[top_idxs])]
                ids_list = cached["ids"]
                scored = {ids_list[i]: float(sims[i]) for i in top_idxs}
                rank = {mid: i for i, mid in enumerate(scored.keys())}
                return scored, rank
            except Exception as e:  # pragma: no cover — fall through
                logger.debug("vector_topk numpy path failed, fallback: %s", e)

        # ── Fallback: per-row Python cosine (no numpy or cache miss) ──
        q = session.query(Memory.id, Memory.embedding).filter(
            Memory.embedding.isnot(None)
        )
        if memory_type:
            q = q.filter(Memory.type == memory_type)
        if maturity:
            q = q.filter(Memory.maturity == maturity)
        rows = q.all()

        scored = {}
        for mid, emb in rows:
            if not isinstance(emb, list) or not emb:
                continue
            sim = cosine_similarity(query_embedding, emb)
            if sim > 0.3:
                scored[mid] = sim

        if not scored:
            return {}, {}

        sorted_ids = sorted(scored.keys(), key=lambda k: -scored[k])[:limit]
        rank = {mid: i for i, mid in enumerate(sorted_ids)}
        scored = {k: scored[k] for k in sorted_ids}
        return scored, rank

    except Exception as e:
        logger.warning(f"Vector top-k failed: {e}")
        return {}, {}


def _vector_rerank(
    session: Session,
    query: str,
    candidate_ids: list[str],
) -> dict[str, float]:
    """Score only ``candidate_ids`` by cosine similarity to the query.

    Two-phase retrieval:
      1. BM25 pre-ranks to get a small candidate set (≤ limit*3, typically 30–60)
      2. This function re-scores those candidates with the vector model

    The old ``_vector_search`` scanned every embedded row in the DB (O(N),
    Python cosine), which dominated at 5K+ memories and also paid a cold-start
    model-load cost on every call. This path is O(K) where K = candidates,
    and only pays the model load once per process.
    """
    if not candidate_ids:
        return {}
    try:
        from memee.engine.embeddings import cosine_similarity, embed_text

        query_embedding = embed_text(query)
        if not query_embedding:
            return {}

        # Fetch only the candidate embeddings in one IN clause.
        rows = (
            session.query(Memory.id, Memory.embedding)
            .filter(Memory.id.in_(candidate_ids))
            .filter(Memory.embedding.isnot(None))
            .all()
        )

        scored: dict[str, float] = {}
        for mid, emb in rows:
            if not isinstance(emb, list) or not emb:
                continue
            sim = cosine_similarity(query_embedding, emb)
            if sim > 0.3:
                scored[mid] = sim

        # Normalize. all-MiniLM-L6-v2 is L2-normalized at encode time so raw
        # cosine ∈ [0, 1]; min-max normalization is only useful when there
        # are ≥3 candidates with a meaningful spread. With 1–2 candidates
        # min == max and the old code collapsed the score to 0 (bug). For
        # tiny pools we keep the raw cosine directly.
        if len(scored) >= 3:
            max_sim = max(scored.values())
            min_sim = min(scored.values())
            range_sim = max_sim - min_sim if max_sim != min_sim else 1.0
            scored = {k: (v - min_sim) / range_sim for k, v in scored.items()}
        return scored

    except Exception as e:
        logger.warning(f"Vector rerank failed: {e}")
        return {}


# Back-compat shim: kept so existing imports/tests that reach into
# ``_vector_search`` don't break. Signature preserved; internally it now
# runs the same O(N) brute path only when no candidate list is available
# (used by code paths outside search_memories such as the bench utilities).
def _vector_search(
    session: Session,
    query: str,
    memory_type: str | None,
    maturity: str | None,
    limit: int,
) -> dict[str, float]:
    """Legacy full-scan vector search. Only kept for direct callers — the
    main ``search_memories`` path now uses ``_vector_rerank`` over the
    BM25 candidate set."""
    try:
        from memee.engine.embeddings import cosine_similarity, embed_text

        query_embedding = embed_text(query)
        if not query_embedding:
            return {}

        q = session.query(Memory).filter(Memory.embedding.isnot(None))
        if memory_type:
            q = q.filter(Memory.type == memory_type)
        if maturity:
            q = q.filter(Memory.maturity == maturity)

        memories = q.all()
        scored: dict[str, float] = {}
        for m in memories:
            emb = m.embedding if isinstance(m.embedding, list) else []
            if not emb:
                continue
            sim = cosine_similarity(query_embedding, emb)
            if sim > 0.3:
                scored[m.id] = sim

        # See ``_vector_rerank`` — min-max normalization collapses single-hit
        # pools to 0. Use raw cosine for n<3.
        if len(scored) >= 3:
            max_sim = max(scored.values())
            min_sim = min(scored.values())
            range_sim = max_sim - min_sim if max_sim != min_sim else 1.0
            scored = {k: (v - min_sim) / range_sim for k, v in scored.items()}

        sorted_ids = sorted(scored.keys(), key=lambda k: -scored[k])[:limit]
        return {k: scored[k] for k in sorted_ids}

    except Exception as e:
        logger.warning(f"Vector search failed: {e}")
        return {}


def _rowid_to_id(session: Session, rowid: int) -> str | None:
    """Convert SQLite rowid to Memory.id."""
    result = session.execute(
        text("SELECT id FROM memories WHERE rowid = :rowid"),
        {"rowid": rowid},
    ).fetchone()
    return result[0] if result else None


def _sanitize_fts_query(query: str, operator: str = "AND") -> str:
    """Sanitize a query string for FTS5 MATCH.

    Tokens are individually quoted to neutralize FTS5 syntax chars. Joined
    with ``operator`` (AND by default — tighter precision; OR is reserved
    for the fallback retry when AND returns no rows).

    Returns an empty string if every token was stripped by sanitization, so
    callers can short-circuit instead of passing the raw query through FTS5
    (which would raise a syntax error and return []).
    """
    op = operator.upper()
    if op not in {"AND", "OR"}:
        op = "AND"

    tokens = query.split()
    safe_tokens = []
    for token in tokens:
        clean = re.sub(r'["\(\)\*]', "", token)
        if clean:
            safe_tokens.append(f'"{clean}"')
    if not safe_tokens:
        return ""
    return f" {op} ".join(safe_tokens)


def _title_phrase_match(query: str, title: str | None) -> bool:
    """True iff lowercased query (or any ≥3-word contiguous substring) occurs in title.

    Returns on first hit; caller applies the boost at most once.
    """
    if not title or not query:
        return False
    q = query.lower().strip()
    t = title.lower()
    if not q:
        return False
    if q in t:
        return True
    words = q.split()
    if len(words) < 3:
        return False
    for n in range(len(words), 2, -1):  # longest first
        for i in range(0, len(words) - n + 1):
            phrase = " ".join(words[i : i + n])
            if phrase in t:
                return True
    return False


def _intent_multiplier(query: str, memory) -> float:
    """Return the first matching intent boost multiplier, else 1.0.

    R14: when the query carries a *danger* verb (fix/secure/harden/avoid/
    prevent/mitigate/patch) AND the candidate is an ``anti_pattern``, the
    multiplier is scaled by ``AntiPattern.severity`` instead of the flat
    ``INTENT_BOOSTS`` row. Severity table:

        critical → 1.40
        high     → 1.25
        medium   → 1.10
        low      → 1.00

    Non-danger verbs (e.g. "test", "optimize", "decide") still hit the
    legacy table — those aren't crisis verbs and severity is unrelated to
    the intent. Behind ``MEMEE_SEVERITY_INTENT_BOOST`` (default on).

    Accepts a Memory ORM instance rather than a bare type string so the
    severity table lookup can read ``memory.anti_pattern.severity`` without
    a second DB hit. Returns 1.0 for non-memory inputs (defensive).
    """
    if memory is None or not query:
        return 1.0
    memory_type = getattr(memory, "type", None)
    if not memory_type:
        return 1.0
    tokens = set(re.findall(r"[a-zA-Z]+", query.lower()))
    if not tokens:
        return 1.0

    # R14 severity branch. Only fires when:
    #   1. flag is on
    #   2. query has at least one danger verb
    #   3. candidate is an anti_pattern with an attached AntiPattern row
    # We deliberately bypass the legacy ``INTENT_BOOSTS`` table in this case
    # so a critical AP doesn't get capped at 1.15 (which the old "secure"
    # row would do for a "harden" query).
    if (
        _severity_intent_enabled()
        and memory_type == "anti_pattern"
        and tokens & _DANGER_VERBS
    ):
        ap = getattr(memory, "anti_pattern", None)
        sev = getattr(ap, "severity", None) if ap is not None else None
        if isinstance(sev, str):
            mult = _SEVERITY_INTENT_TABLE.get(sev.lower())
            if mult is not None:
                return mult
        # Missing severity: fall back to the medium row so we don't drop
        # an AP candidate that lost its child row to a half-recorded write.
        return _SEVERITY_INTENT_TABLE["medium"]

    for verbs, target, mult in INTENT_BOOSTS:
        if not (tokens & verbs):
            continue
        if isinstance(target, set):
            if memory_type in target:
                return mult
        else:
            if memory_type == target:
                return mult
    return 1.0


def _compute_tag_score(
    memory_tags: list | None, query_tags: list[str] | None
) -> float:
    """Jaccard similarity between memory tags and query tags."""
    if not query_tags or not memory_tags:
        return 0.0
    mem_set = set(memory_tags) if isinstance(memory_tags, list) else set()
    q_set = set(query_tags)
    union = mem_set | q_set
    if not union:
        return 0.0
    return len(mem_set & q_set) / len(union)


def _fallback_search(
    session: Session,
    query: str,
    tags: list[str] | None,
    memory_type: str | None,
    maturity: str | None,
    limit: int,
    user_id: str | None = None,
) -> list[dict]:
    """LIKE-based fallback when both FTS5 and vector return nothing.

    Routes the same visibility hook the main path uses; without this, a hook
    that hides team-private memories from the active user would still leak
    them via the fallback whenever a query happened to land in the LIKE
    branch (a real risk for short / unusual queries).
    """
    q = session.query(Memory).filter(
        (Memory.title.ilike(f"%{query}%")) | (Memory.content.ilike(f"%{query}%"))
    )
    if memory_type:
        q = q.filter(Memory.type == memory_type)
    if maturity:
        q = q.filter(Memory.maturity == maturity)

    from memee import plugins as _plugins
    q = _plugins.apply_visibility(session, q, user_id=user_id)

    memories = q.limit(limit).all()
    return [
        {
            "memory": m,
            "bm25_score": 0.5,
            "vector_score": 0.0,
            "tag_score": _compute_tag_score(m.tags, tags),
            "confidence_boost": 0.0,
            "total_score": 0.5,
        }
        for m in memories
    ]
