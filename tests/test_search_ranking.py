"""Ranking regression gate for hybrid_search.

The reviewer found recall is strong but hit@1 was ~17%: the right memory is
usually in the top-5 but rarely first, because confidence/tag weights drowned
precise lexical matches. This test locks in post-fix ranking: 12 handcrafted
memories × 12 queries, asserting hit@1 >= 0.5 and hit@3 >= 0.9.

If you change W_* constants, title/intent boosts, or scoring logic in
``memee.engine.search``, expect this test to shift — re-tune, don't loosen.
"""

from __future__ import annotations

import pytest

from memee.engine.search import search_memories
from memee.storage.models import MaturityLevel, Memory, MemoryType


@pytest.fixture
def ranked_corpus(session):
    """12 memories spanning types, tags, confidence, maturity."""
    memories = [
        # 0 — exact title match for "timeout API", high-trust generic pattern
        Memory(
            type=MemoryType.PATTERN.value,
            title="Use timeout on API calls",
            content="Always pass timeout=10 to requests.get to avoid hanging.",
            tags=["python", "api", "http"],
            confidence_score=0.9,
            maturity=MaturityLevel.VALIDATED.value,
        ),
        # 1 — SQLite WAL concurrency pattern
        Memory(
            type=MemoryType.PATTERN.value,
            title="Enable SQLite WAL mode for concurrent reads",
            content="WAL journaling lets readers proceed during writes.",
            tags=["sqlite", "database", "performance"],
            confidence_score=0.8,
            maturity=MaturityLevel.VALIDATED.value,
        ),
        # 2 — anti-pattern about pypdf (should win on "pypdf" query)
        Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Do not use pypdf for complex PDFs",
            content="pypdf garbles layouts and tables; use pymupdf or pdfplumber.",
            tags=["python", "pdf", "parsing"],
            confidence_score=0.7,
            maturity=MaturityLevel.TESTED.value,
        ),
        # 3 — lesson about N+1 queries (debug / fix intent)
        Memory(
            type=MemoryType.LESSON.value,
            title="N+1 query bug in ORM relationships",
            content="Eager-load with selectinload to fix N+1 query performance bugs.",
            tags=["orm", "sqlalchemy", "performance"],
            confidence_score=0.75,
            maturity=MaturityLevel.VALIDATED.value,
        ),
        # 4 — decision
        Memory(
            type=MemoryType.DECISION.value,
            title="Chose FastAPI over Flask for async support",
            content="Decision: FastAPI selected over Flask/Django for native async/await.",
            tags=["python", "framework", "async"],
            confidence_score=0.85,
            maturity=MaturityLevel.CANON.value,
        ),
        # 5 — anti-pattern about secrets in repo (security intent)
        Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Never commit API keys to git",
            content="Hard-coded API keys in source trees leak on every push; use env vars.",
            tags=["security", "secrets", "git"],
            confidence_score=0.95,
            maturity=MaturityLevel.CANON.value,
        ),
        # 6 — testing pattern
        Memory(
            type=MemoryType.PATTERN.value,
            title="Pytest fixtures for database tests",
            content="Use function-scoped fixtures with rollback to isolate test state.",
            tags=["pytest", "testing", "database"],
            confidence_score=0.8,
            maturity=MaturityLevel.VALIDATED.value,
        ),
        # 7 — performance optimization pattern
        Memory(
            type=MemoryType.PATTERN.value,
            title="Add index on WHERE clause columns",
            content="Unindexed WHERE columns force full scans; add a btree index to optimize.",
            tags=["sql", "index", "performance"],
            confidence_score=0.82,
            maturity=MaturityLevel.VALIDATED.value,
        ),
        # 8 — react hook lesson
        Memory(
            type=MemoryType.LESSON.value,
            title="useEffect cleanup prevents memory leaks",
            content="Return a cleanup function from useEffect to cancel subscriptions.",
            tags=["react", "hooks", "frontend"],
            confidence_score=0.7,
            maturity=MaturityLevel.TESTED.value,
        ),
        # 9 — docker optimization pattern
        Memory(
            type=MemoryType.PATTERN.value,
            title="Multi-stage Docker builds shrink image size",
            content="Split builder and runtime stages to cut image weight by 70%.",
            tags=["docker", "devops", "performance"],
            confidence_score=0.78,
            maturity=MaturityLevel.VALIDATED.value,
        ),
        # 10 — decision about database
        Memory(
            type=MemoryType.DECISION.value,
            title="Chose PostgreSQL over MySQL for JSON support",
            content="PostgreSQL JSONB + GIN indexes beat MySQL JSON for our workload.",
            tags=["database", "postgres", "mysql"],
            confidence_score=0.88,
            maturity=MaturityLevel.CANON.value,
        ),
        # 11 — harden / security anti-pattern
        Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="SQL injection via string concatenation",
            content="f-string SQL leaks to injection; use parameterized queries to harden.",
            tags=["security", "sql", "injection"],
            confidence_score=0.92,
            maturity=MaturityLevel.CANON.value,
        ),
    ]
    session.add_all(memories)
    session.commit()
    # Return id list so asserts can survive auto-generated ids.
    return [m.id for m in memories]


# (query, expected_memory_index)
RANKING_QUERIES: list[tuple[str, int]] = [
    ("Use timeout on API calls", 0),
    ("SQLite WAL mode", 1),
    ("pypdf complex PDFs anti-pattern", 2),
    ("fix N+1 query bug in ORM", 3),
    ("decision FastAPI over Flask", 4),
    ("security never commit API keys", 5),
    ("testing pytest fixtures for database", 6),
    ("optimize index on WHERE clause columns", 7),
    ("useEffect cleanup memory leaks", 8),
    ("multi-stage Docker builds", 9),
    ("decide PostgreSQL over MySQL", 10),
    ("harden SQL injection string concatenation", 11),
]


def test_ranking_hit_at_1_and_3(session, ranked_corpus):
    """Regression gate: hit@1 >= 0.5 and hit@3 >= 0.9."""
    ids = ranked_corpus
    hits_at_1 = 0
    hits_at_3 = 0
    misses: list[tuple[str, int, list[str]]] = []

    for query, expected_idx in RANKING_QUERIES:
        expected_id = ids[expected_idx]
        results = search_memories(session, query, limit=10)
        top_ids = [r["memory"].id for r in results]

        if top_ids[:1] == [expected_id]:
            hits_at_1 += 1
        if expected_id in top_ids[:3]:
            hits_at_3 += 1
        else:
            misses.append((query, expected_idx, top_ids[:3]))

    n = len(RANKING_QUERIES)
    hit_at_1 = hits_at_1 / n
    hit_at_3 = hits_at_3 / n

    assert hit_at_1 >= 0.5, (
        f"hit@1 regressed to {hit_at_1:.2f} (need >= 0.5). "
        f"Top-3 misses: {misses}"
    )
    assert hit_at_3 >= 0.9, (
        f"hit@3 regressed to {hit_at_3:.2f} (need >= 0.9). "
        f"Top-3 misses: {misses}"
    )
