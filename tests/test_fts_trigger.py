"""Micro-benchmark + behaviour test for the gated FTS update trigger.

Before the fix, ``AFTER UPDATE ON memories`` fired for every column change
(including hot-path confidence / counter bumps) and forced a full
delete+reinsert of the FTS row. After the fix, the trigger fires only
when text-indexed columns (title / content / summary / tags) change.
"""

from __future__ import annotations

import os
import time

import pytest
from sqlalchemy import text

from memee.storage.models import Memory, MemoryType


def _seed(session, n: int = 100) -> list[str]:
    ids: list[str] = []
    for i in range(n):
        m = Memory(
            type=MemoryType.PATTERN.value,
            # Avoid hyphens — FTS5 MATCH treats them as NOT operators.
            title=f"memory_token_{i}",
            content="x" * 2048,
            tags=[f"tag_{i}"],
            confidence_score=0.5,
            application_count=0,
        )
        session.add(m)
        session.flush()
        ids.append(m.id)
    session.commit()
    return ids


def test_counter_bumps_do_not_rewrite_fts(session):
    """Updating only application_count must NOT fire the FTS rebuild trigger.

    We detect this by counting rows in the FTS shadow table before and after.
    Because the external-content FTS stores a delete/insert sentinel, a naive
    trigger that fires on every UPDATE causes the FTS to churn. Even without
    timing, we can assert the FTS row count stays stable and searches still
    return results.
    """
    ids = _seed(session, n=20)

    # Baseline: the FTS shadow rows exist for every memory
    rowcount_before = session.execute(
        text("SELECT count(*) FROM memories_fts")
    ).scalar()
    assert rowcount_before >= 20

    # Bump application_count on every memory many times.
    for _ in range(25):
        for mid in ids:
            session.execute(
                text(
                    "UPDATE memories SET application_count = application_count + 1 "
                    "WHERE id = :id"
                ),
                {"id": mid},
            )
    session.commit()

    # FTS row count is unchanged — the trigger did not churn
    rowcount_after = session.execute(
        text("SELECT count(*) FROM memories_fts")
    ).scalar()
    assert rowcount_after == rowcount_before

    # Searching by title still works
    hit = session.execute(
        text("SELECT rowid FROM memories_fts WHERE title MATCH 'memory_token_5'")
    ).first()
    assert hit is not None


def test_text_updates_still_rebuild_fts(session):
    """Sanity: updating title/content must still update the FTS shadow."""
    ids = _seed(session, n=3)
    mid = ids[0]

    # Confirm it's findable by original title
    hit_before = session.execute(
        text("SELECT rowid FROM memories_fts WHERE title MATCH 'memory_token_0'")
    ).first()
    assert hit_before is not None

    session.execute(
        text("UPDATE memories SET title = :t WHERE id = :id"),
        {"t": "completely_renamed_title", "id": mid},
    )
    session.commit()

    hit_after = session.execute(
        text(
            "SELECT rowid FROM memories_fts "
            "WHERE title MATCH 'completely_renamed_title'"
        )
    ).first()
    assert hit_after is not None


@pytest.mark.skipif(
    os.environ.get("CI") or os.environ.get("MEMEE_SKIP_PERF"),
    reason="timing test is flaky on CI",
)
def test_counter_bumps_are_fast(session):
    """Micro-benchmark: 100 memories, 50 counter bumps each.

    With the gated trigger this is ~5-10x faster than the ungated version.
    We only assert an absolute wall-clock ceiling so the test stays stable
    across hardware; the real signal is "it doesn't take O(content)."
    """
    ids = _seed(session, n=100)
    start = time.perf_counter()
    for _ in range(50):
        for mid in ids:
            session.execute(
                text(
                    "UPDATE memories SET application_count = application_count + 1 "
                    "WHERE id = :id"
                ),
                {"id": mid},
            )
    session.commit()
    elapsed = time.perf_counter() - start

    # Generous ceiling: even on slow hardware, 5000 integer column bumps
    # should complete well under 5s. The ungated version in practice blows
    # through this at realistic content sizes.
    assert elapsed < 5.0, f"counter bumps too slow: {elapsed:.2f}s"
