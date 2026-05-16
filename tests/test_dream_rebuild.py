"""Tests for ``memee dream --rebuild-contradictions`` (v2.4.8).

Existing v2.4.7-and-earlier installs have ``contradicts`` edges that the
naive heuristic emitted on any pattern + anti-pattern pair sharing two
tags. The rebuild flag deletes every existing ``contradicts`` row and
then runs the normal cycle so the new cross-encoder gate re-evaluates
the graph from scratch.

These tests pin the rebuild contract:

* Pre-existing ``contradicts`` edges are removed before auto-connect runs.
* Non-contradicts edges (``depends_on``, ``supports``, etc.) survive.
* The cycle reports the purge count in ``stats["contradictions_purged"]``.
* The CLI flag and ``MEMEE_REBUILD_CONTRADICTIONS`` env var both work.
* Without the flag, existing contradicts edges stay put.
"""

from __future__ import annotations

from click.testing import CliRunner

from memee.cli import cli
from memee.engine.dream import run_dream_cycle
from memee.storage.models import (
    AntiPattern,
    Memory,
    MemoryConnection,
    MemoryType,
)


class FakeScorer:
    def __init__(self, canned_score: float | None) -> None:
        self.canned_score = canned_score

    def score_pair(self, m1, m2):
        return self.canned_score


def _seed_stale_contradicts(session, n: int = 5) -> list[tuple[str, str]]:
    """Insert ``n`` (pattern, anti-pattern) pairs + stale contradicts edges.

    Returns the (source_id, target_id) tuples so callers can assert
    they survived / were purged.
    """
    pair_ids: list[tuple[str, str]] = []
    for i in range(n):
        pat = Memory(
            type=MemoryType.PATTERN.value,
            title=f"Pattern {i}: use thing-{i}",
            content=f"good idea {i}",
            tags=[f"tag-{i}", "shared"],
        )
        anti = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title=f"Anti {i}: don't break thing-{i}",
            content=f"bad idea {i}",
            tags=[f"tag-{i}", "shared"],
        )
        session.add_all([pat, anti])
        session.flush()
        session.add(
            AntiPattern(
                memory_id=anti.id,
                severity="medium",
                trigger=f"trigger {i}",
                consequence=f"consequence {i}",
            )
        )
        # Stale "contradicts" edge that the v2.4.7 naive heuristic would
        # have created — seed it manually here.
        session.add(
            MemoryConnection(
                source_id=pat.id,
                target_id=anti.id,
                relationship_type="contradicts",
                strength=0.6,
            )
        )
        pair_ids.append((pat.id, anti.id))
    session.commit()
    return pair_ids


def _count_contradicts(session) -> int:
    return (
        session.query(MemoryConnection)
        .filter(MemoryConnection.relationship_type == "contradicts")
        .count()
    )


def test_rebuild_purges_all_stale_contradicts(session):
    """All 5 seeded contradicts edges are gone after a low-score rebuild."""
    _seed_stale_contradicts(session, n=5)
    assert _count_contradicts(session) == 5

    # Low score → gate refuses to re-classify any pair as contradiction.
    stats = run_dream_cycle(
        session,
        rebuild_contradictions=True,
        scorer=FakeScorer(canned_score=0.1),
    )
    assert stats["contradictions_purged"] == 5
    assert stats["contradictions_found"] == 0
    assert _count_contradicts(session) == 0


def test_rebuild_replaces_with_gated_edges(session):
    """High-score rebuild deletes 5, recreates them via the new gate."""
    _seed_stale_contradicts(session, n=5)
    stats = run_dream_cycle(
        session,
        rebuild_contradictions=True,
        scorer=FakeScorer(canned_score=0.9),
    )
    assert stats["contradictions_purged"] == 5
    # Every original pair shared 2 tags (``tag-i`` + ``shared``) so the
    # auto-connect tag gate still pulls them in; the new contradiction
    # gate flips them back to ``contradicts`` because the fake scorer
    # exceeds the default threshold.
    assert stats["contradictions_found"] >= 5
    assert _count_contradicts(session) >= 5


def test_rebuild_preserves_other_edge_types(session):
    """Only ``contradicts`` edges get wiped; depends_on / supports survive."""
    _seed_stale_contradicts(session, n=2)
    # Drop a foreign-type edge on an independent memory pair so the
    # ``(source_id, target_id)`` uniqueness constraint doesn't collide
    # with the seeded contradicts edges.
    extra_a = Memory(
        type=MemoryType.PATTERN.value,
        title="Independent pattern A",
        content="A",
        tags=["alpha", "beta"],
    )
    extra_b = Memory(
        type=MemoryType.PATTERN.value,
        title="Independent pattern B",
        content="B",
        tags=["alpha", "beta"],
    )
    session.add_all([extra_a, extra_b])
    session.flush()
    session.add(
        MemoryConnection(
            source_id=extra_a.id,
            target_id=extra_b.id,
            relationship_type="depends_on",
            strength=0.5,
        )
    )
    session.commit()

    run_dream_cycle(
        session,
        rebuild_contradictions=True,
        scorer=FakeScorer(canned_score=0.1),
    )

    survivors = (
        session.query(MemoryConnection)
        .filter(
            MemoryConnection.source_id == extra_a.id,
            MemoryConnection.target_id == extra_b.id,
            MemoryConnection.relationship_type == "depends_on",
        )
        .count()
    )
    assert survivors == 1


def test_no_rebuild_keeps_existing_contradicts(session):
    """Default cycle (no rebuild) leaves pre-existing contradicts in place."""
    _seed_stale_contradicts(session, n=3)
    before = _count_contradicts(session)
    stats = run_dream_cycle(session, scorer=FakeScorer(canned_score=0.9))
    after = _count_contradicts(session)
    # No purge happened (count stays put or grows via the new auto-connect).
    assert stats["contradictions_purged"] == 0
    assert after >= before


def test_env_var_triggers_rebuild(session, monkeypatch):
    """``MEMEE_REBUILD_CONTRADICTIONS=1`` triggers the purge even with no flag."""
    _seed_stale_contradicts(session, n=4)
    assert _count_contradicts(session) == 4
    monkeypatch.setenv("MEMEE_REBUILD_CONTRADICTIONS", "1")
    stats = run_dream_cycle(session, scorer=FakeScorer(canned_score=0.1))
    assert stats["contradictions_purged"] == 4
    assert _count_contradicts(session) == 0


def test_cli_flag_invokes_rebuild(tmp_path, monkeypatch):
    """End-to-end: ``memee dream --rebuild-contradictions`` reaches the cycle.

    Cross-encoder is unavailable in CI (HF_HUB_OFFLINE=1 in conftest,
    no cached weights), so the gate fails safe to ``related_to`` for
    every purged pair. That's the right thing to assert: the flag
    wired through to the cycle and emptied the contradicts set.
    """
    db_path = tmp_path / "rebuild_cli.db"
    # ``memee.config.settings`` is constructed at import time, so the
    # CLI's ``init_db()`` call ignores fresh ``MEMEE_DB_PATH`` env
    # mutations. Patch the cached settings object directly.
    import memee.config

    monkeypatch.setattr(memee.config.settings, "db_path", db_path)
    monkeypatch.delenv("MEMEE_REBUILD_CONTRADICTIONS", raising=False)
    # Force the cross-encoder kill-switch so the gate consistently
    # falls back to ``related_to`` regardless of the local HF cache.
    # That way every CI machine sees the same purge-then-empty outcome.
    monkeypatch.setenv("MEMEE_RERANK", "0")
    # ``_ContradictionScorer`` caches the reranker module's load result
    # at process scope; reset so the kill-switch is picked up.
    from memee.engine import reranker as reranker_mod

    reranker_mod.reset_for_tests()

    from memee.storage.database import get_engine, get_session, init_db

    engine = init_db(get_engine(db_path))
    sess = get_session(engine)
    _seed_stale_contradicts(sess, n=3)
    sess.commit()
    sess.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["dream", "--rebuild-contradictions"])
    assert result.exit_code == 0, result.output
    assert "purged" in result.output.lower()
    assert "3" in result.output  # the purge count

    # Verify the DB now has zero contradicts.
    sess2 = get_session(engine)
    assert _count_contradicts(sess2) == 0
    sess2.close()
