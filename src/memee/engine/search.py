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

# Weights for hybrid scoring (sum to 1.0)
W_BM25 = 0.42
W_VECTOR = 0.30
W_TAGS = 0.20
W_CONFIDENCE = 0.08

# Title boost: exact query or ≥3-word contiguous substring match (applied at most once).
TITLE_PHRASE_BOOST = 1.3

# Task intent → memory type boosts (applied at most once per result).
# (verbs, type_or_types, multiplier)
INTENT_BOOSTS: list[tuple[set[str], object, float]] = [
    ({"test", "tests", "testing"}, "pattern", 1.1),
    ({"secure", "security", "harden"}, "anti_pattern", 1.15),
    ({"decide", "decision", "chose"}, "decision", 1.15),
    ({"fix", "bug", "bugfix", "debug"}, {"lesson", "anti_pattern"}, 1.1),
    ({"optimize", "perf", "performance"}, "pattern", 1.1),
]


def _has_embeddings() -> bool:
    """Check if sentence-transformers is available."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


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
):
    """Hybrid search: BM25 + vector + tags + confidence.

    `scope` and `user_id` are honoured when `memee-team` is installed and
    has registered the `visible_memories` plugin hook. In OSS they are
    accepted but ignored (single-user product).

    If ``return_event_id`` is True, returns ``(results, event_id | None)`` so
    callers (e.g. the MCP `memory_search` tool) can pass the id back to
    ``mark_event_accepted`` without racing on "latest SearchEvent" lookups
    under concurrent traffic. Default False preserves the historical
    list-returning signature for every other caller.
    """
    _t0 = time.perf_counter()
    # Stage 1: BM25 search
    bm25_results = _bm25_search(session, query, memory_type, maturity, limit * 3)

    # Stage 2: Vector search (if available)
    vector_results = {}
    has_vectors = use_vectors and _has_embeddings()
    if has_vectors:
        vector_results = _vector_search(session, query, memory_type, maturity, limit * 3)

    # Stage 3: Batch rowid → id resolution (ONE query, not N)
    rowids = [r[0] for r in bm25_results]
    bm25_by_id = {}
    if rowids:
        max_bm25 = max((abs(r[1]) for r in bm25_results), default=1.0)
        rank_by_rowid = {r[0]: r[1] for r in bm25_results}
        # SQLite IN requires inline placeholders (expanding=True via bindparam)
        placeholders = ",".join(str(int(r)) for r in rowids)
        rows = session.execute(
            text(f"SELECT id, rowid FROM memories WHERE rowid IN ({placeholders})")
        ).fetchall()
        for mid, rowid in rows:
            rank = rank_by_rowid.get(rowid, 0)
            # FTS5 rank is negative (lower = better). Larger abs(rank) = stronger
            # match → normalize so the best match maps to 1.0, not 0.0.
            bm25_by_id[mid] = (abs(rank) / max_bm25) if max_bm25 > 0 else 0.5

    all_ids = set(bm25_by_id.keys()) | set(vector_results.keys())
    if not all_ids:
        fb = _fallback_search(session, query, tags, memory_type, maturity, limit)
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
    memories_list = memories_q.all()

    # Scope visibility is a memee-team plugin. If registered, use it.
    visible_ids = None
    if scope and user_id:
        from memee import plugins as _plugins
        _scoping_hook = _plugins.get("visible_memories")
        if _scoping_hook is not None and _scoping_hook is not _plugins._default_visible_memories:
            visible_q = _scoping_hook(session)
            visible_ids = {m.id for m in visible_q.with_entities(Memory.id).all()}

    results = []
    for memory in memories_list:
        # Scope filter
        if visible_ids is not None and memory.id not in visible_ids:
            continue
        memory_id = memory.id

        bm25_score = bm25_by_id.get(memory_id, 0.0)
        vector_score = vector_results.get(memory_id, 0.0)
        tag_score = _compute_tag_score(memory.tags, tags)
        conf_score = (
            MATURITY_MULTIPLIER.get(memory.maturity, 0.5)
            * memory.confidence_score
        )

        if has_vectors:
            total = (
                W_BM25 * bm25_score
                + W_VECTOR * vector_score
                + W_TAGS * tag_score
                + W_CONFIDENCE * conf_score
            )
        else:
            # No vectors — redistribute weight
            total = (
                0.55 * bm25_score
                + 0.25 * tag_score
                + 0.20 * conf_score
            )

        # Apply title phrase boost (at most once) and intent boost (at most once).
        if _title_phrase_match(query, memory.title):
            total *= TITLE_PHRASE_BOOST
        intent_mult = _intent_multiplier(query, memory.type)
        if intent_mult != 1.0:
            total *= intent_mult

        results.append({
            "memory": memory,
            "bm25_score": round(bm25_score, 4),
            "vector_score": round(vector_score, 4),
            "tag_score": round(tag_score, 4),
            "confidence_boost": round(conf_score, 4),
            "total_score": round(total, 4),
        })

    results.sort(key=lambda r: r["total_score"], reverse=True)
    final = results[:limit]
    event_id = _record_telemetry(session, query, final, _t0)
    if return_event_id:
        return final, event_id
    return final


def _record_telemetry(session: Session, query: str, results: list, t0: float) -> str | None:
    """Best-effort: persist a SearchEvent. Returns the new event id (or None
    on error / disabled telemetry). Never raises."""
    try:
        from memee.engine.telemetry import record_search_event
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return record_search_event(session, query, results, latency_ms)
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
    return count


# ── Internal helpers ──


def _bm25_search(
    session: Session,
    query: str,
    memory_type: str | None,
    maturity: str | None,
    limit: int,
) -> list[tuple[int, float]]:
    """BM25 search via FTS5. Returns [(rowid, rank), ...].

    AND-by-default for precision; falls back to OR (one layer) if AND returns
    zero rows so we don't silently miss single-token or partial-hit queries.
    """
    fts_and = _sanitize_fts_query(query, operator="AND")
    if not fts_and:
        # Sanitization stripped every token — avoid shoving the raw query
        # through FTS5 (it would hit syntax errors and silently return []).
        return []

    fts_sql = text("""
        SELECT rowid, rank
        FROM memories_fts
        WHERE memories_fts MATCH :query
        ORDER BY rank
        LIMIT :limit
    """)

    try:
        rows = session.execute(
            fts_sql, {"query": fts_and, "limit": limit}
        ).fetchall()
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
        return session.execute(
            fts_sql, {"query": fts_or, "limit": limit}
        ).fetchall()
    except Exception as e:
        logger.debug(f"BM25 OR fallback failed for query '{query[:50]}': {e}")
        return []


def _vector_search(
    session: Session,
    query: str,
    memory_type: str | None,
    maturity: str | None,
    limit: int,
) -> dict[str, float]:
    """Vector similarity search. Returns {memory_id: similarity_score}."""
    try:
        from memee.engine.embeddings import cosine_similarity, embed_text

        query_embedding = embed_text(query)

        # Get all memories with embeddings
        q = session.query(Memory).filter(Memory.embedding.isnot(None))
        if memory_type:
            q = q.filter(Memory.type == memory_type)
        if maturity:
            q = q.filter(Memory.maturity == maturity)

        memories = q.all()

        scored = {}
        for m in memories:
            if m.embedding:
                emb = m.embedding if isinstance(m.embedding, list) else []
                sim = cosine_similarity(query_embedding, emb)
                if sim > 0.3:  # Threshold to reduce noise
                    scored[m.id] = sim

        # Normalize to 0-1
        if scored:
            max_sim = max(scored.values())
            min_sim = min(scored.values())
            range_sim = max_sim - min_sim if max_sim != min_sim else 1.0
            scored = {k: (v - min_sim) / range_sim for k, v in scored.items()}

        # Return top N
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


def _intent_multiplier(query: str, memory_type: str | None) -> float:
    """Return the first matching intent boost multiplier, else 1.0."""
    if not query or not memory_type:
        return 1.0
    tokens = set(re.findall(r"[a-zA-Z]+", query.lower()))
    if not tokens:
        return 1.0
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
) -> list[dict]:
    """LIKE-based fallback when both FTS5 and vector return nothing."""
    q = session.query(Memory).filter(
        (Memory.title.ilike(f"%{query}%")) | (Memory.content.ilike(f"%{query}%"))
    )
    if memory_type:
        q = q.filter(Memory.type == memory_type)
    if maturity:
        q = q.filter(Memory.maturity == maturity)

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
