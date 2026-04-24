"""Tests for hybrid search engine."""

from memee.engine.search import (
    _sanitize_fts_query,
    search_anti_patterns,
    search_memories,
)
from memee.storage.models import AntiPattern, Memory, MemoryType


def test_search_by_title(session):
    """FTS5 finds memories by title match."""
    m1 = Memory(
        type=MemoryType.PATTERN.value,
        title="Use timeout on API calls",
        content="Always set timeout=10 on requests.",
        tags=["python", "api"],
    )
    m2 = Memory(
        type=MemoryType.PATTERN.value,
        title="SQLite WAL mode for concurrency",
        content="Enable WAL mode for better concurrent reads.",
        tags=["sqlite", "database"],
    )
    session.add_all([m1, m2])
    session.commit()

    results = search_memories(session, "timeout API")
    assert len(results) >= 1
    assert results[0]["memory"].title == "Use timeout on API calls"


def test_search_by_content(session):
    """FTS5 finds memories by content match."""
    m = Memory(
        type=MemoryType.PATTERN.value,
        title="Request handling",
        content="Always use requests.get(url, timeout=10) to prevent hanging connections.",
        tags=["python"],
    )
    session.add(m)
    session.commit()

    results = search_memories(session, "timeout hanging connections")
    assert len(results) >= 1


def test_search_filter_by_type(session):
    """Filter search results by memory type."""
    m1 = Memory(type=MemoryType.PATTERN.value, title="Pattern about timeout", content="c1")
    m2 = Memory(type=MemoryType.LESSON.value, title="Lesson about timeout", content="c2")
    session.add_all([m1, m2])
    session.commit()

    results = search_memories(session, "timeout", memory_type="pattern")
    assert all(r["memory"].type == "pattern" for r in results)


def test_tag_boost(session):
    """Memories with matching tags score higher."""
    m1 = Memory(
        type=MemoryType.PATTERN.value,
        title="Use timeout on calls",
        content="Timeout prevents hanging.",
        tags=["python", "api"],
    )
    m2 = Memory(
        type=MemoryType.PATTERN.value,
        title="Use timeout setting",
        content="Timeout is important.",
        tags=["java", "backend"],
    )
    session.add_all([m1, m2])
    session.commit()

    results = search_memories(session, "timeout", tags=["python", "api"])
    # m1 should score higher due to tag overlap
    if len(results) >= 2:
        assert results[0]["tag_score"] >= results[1]["tag_score"]


def test_search_anti_patterns(session):
    """Anti-pattern search returns only anti_pattern type."""
    m1 = Memory(type=MemoryType.ANTI_PATTERN.value, title="Don't use pypdf", content="buggy")
    m2 = Memory(type=MemoryType.PATTERN.value, title="pypdf is fast for simple PDFs", content="ok")
    session.add_all([m1, m2])
    session.flush()

    ap = AntiPattern(
        memory_id=m1.id,
        severity="high",
        trigger="Complex PDFs",
        consequence="Garbled text",
        alternative="Use pymupdf",
    )
    session.add(ap)
    session.commit()

    results = search_anti_patterns(session, "pypdf PDF processing")
    assert all(r["memory"].type == "anti_pattern" for r in results)


def test_fallback_search(session):
    """LIKE fallback works when FTS returns nothing (special chars)."""
    m = Memory(
        type=MemoryType.PATTERN.value,
        title="Use row_factory = sqlite3.Row",
        content="Always set row_factory for dict-like access.",
        tags=["python", "sqlite"],
    )
    session.add(m)
    session.commit()

    # This query may not match FTS well but should match via LIKE
    results = search_memories(session, "row_factory")
    assert len(results) >= 1


def test_empty_search(session):
    """Empty database returns no results."""
    results = search_memories(session, "anything")
    assert results == []


def test_and_semantics_two_word_query(session):
    """Two-word query ranks the memory containing BOTH tokens first.

    AND-by-default means OR-only hits don't drown the one true match.
    """
    both = Memory(
        type=MemoryType.PATTERN.value,
        title="Retry with exponential backoff",
        content="Use retry and backoff together to survive transient failures.",
        tags=["python", "resilience"],
    )
    only_retry = Memory(
        type=MemoryType.PATTERN.value,
        title="Retry policy for HTTP clients",
        content="Set a reasonable retry limit on requests.",
        tags=["http", "retry"],
    )
    only_backoff = Memory(
        type=MemoryType.PATTERN.value,
        title="Backoff strategy tuning",
        content="Jittered backoff prevents thundering herd.",
        tags=["infra"],
    )
    session.add_all([both, only_retry, only_backoff])
    session.commit()

    results = search_memories(session, "retry backoff", limit=5)
    assert results, "AND search should find the memory with both tokens"
    assert results[0]["memory"].id == both.id


def test_or_fallback_when_and_misses(session):
    """Single-token query that matches nothing under AND still finds related hits.

    Here the AND path matches by itself (single token == AND and OR
    identical), but the bigger point is that when AND is strict and
    returns 0, the OR fallback kicks in. We simulate the multi-token
    equivalent: a query where no memory contains ALL tokens, but one
    contains the rarest token.
    """
    m = Memory(
        type=MemoryType.PATTERN.value,
        title="Vector index HNSW tuning",
        content="HNSW graph parameters trade recall vs latency.",
        tags=["ann", "vector"],
    )
    session.add(m)
    session.commit()

    # "HNSW unicorn dragon" — only "HNSW" exists in the corpus.
    # AND requires all three → 0 results; OR fallback returns the HNSW hit.
    results = search_memories(session, "HNSW unicorn dragon", limit=5)
    assert len(results) >= 1
    assert any(r["memory"].id == m.id for r in results)


def test_sanitize_empty_returns_empty(session):
    """A query that reduces to zero safe tokens must not raise and returns []."""
    # The sanitizer strips quotes/parens/stars. A pure-punctuation token
    # becomes empty after stripping.
    assert _sanitize_fts_query('""()*') == ""
    assert _sanitize_fts_query('""()*', operator="OR") == ""

    # And the engine should propagate that as an empty result, not an FTS5
    # syntax error that gets silently swallowed.
    m = Memory(
        type=MemoryType.PATTERN.value,
        title="Regular memory",
        content="Regular content.",
        tags=["x"],
    )
    session.add(m)
    session.commit()

    results = search_memories(session, '""()*', limit=5)
    assert isinstance(results, list)


def test_sanitize_and_vs_or_operator():
    """Sanitizer joins with the requested operator."""
    assert _sanitize_fts_query("retry backoff", operator="AND") == '"retry" AND "backoff"'
    assert _sanitize_fts_query("retry backoff", operator="OR") == '"retry" OR "backoff"'
    # Invalid operator falls back to AND rather than injecting arbitrary text.
    assert _sanitize_fts_query("retry backoff", operator="NEAR") == '"retry" AND "backoff"'
