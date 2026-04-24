"""Tests for hybrid search engine."""

from memee.engine.search import search_anti_patterns, search_memories
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
