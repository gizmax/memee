"""Tests for the v2.2.4 semantic-dedup pass in ``engine.dream``.

The pass folds memories whose embeddings are near-identical (cosine
≥ threshold) into a canonical entry, complementing the lexical dedup
the quality gate already runs at write time. See the autoresearch
finding in v2.2.4 — Letta #3116 and Mem0 #4573 both show "User likes
Vim" / "user prefers Vim" stacking up as distinct rows because the
lexical SequenceMatcher misses the paraphrase.

Tests build memories with **hand-crafted** 384-dim embeddings so we can
assert behaviour by construction without loading the
sentence-transformers model (which is ~80 MB and offline-mode disabled
in CI). Cosine similarity is set by the angle between the chosen
vectors; we use simple basis vectors plus small perturbations.
"""

from __future__ import annotations

import math

import pytest

from memee.engine.dream import (
    SEMANTIC_DEDUP_THRESHOLD_DEFAULT,
    _semantic_dedup_pass,
    _semantic_dedup_threshold,
)
from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryConnection,
    MemoryType,
)


pytest.importorskip("numpy")


def _vec(*, near: list[float], dim: int = 384, jitter: float = 0.0) -> list[float]:
    """Build a unit-norm ``dim``-vector.

    ``near`` supplies the first few components; the remainder is zero
    padding plus optional small jitter so two "near" vectors aren't
    perfectly co-linear (which would be a degenerate cosine = 1.0).
    The result is L2-normalised so cosine similarity ≈ dot product —
    keeping the assertions easy to reason about.
    """
    base = list(near) + [0.0] * (dim - len(near))
    if jitter:
        # Spread jitter across the unused tail so it perturbs the angle
        # without dominating the leading components.
        for i in range(len(near), dim):
            base[i] = jitter * ((i % 7) - 3) / 7.0
    norm = math.sqrt(sum(x * x for x in base))
    return [x / norm for x in base]


def _mk_memory(
    session,
    *,
    title: str,
    content: str,
    tags: list[str],
    embedding: list[float],
    mtype: str = MemoryType.PATTERN.value,
    confidence: float = 0.7,
    severity: str | None = None,
) -> Memory:
    m = Memory(
        type=mtype,
        title=title,
        content=content,
        tags=tags,
        maturity=MaturityLevel.VALIDATED.value,
        confidence_score=confidence,
        source_type="human",
        embedding=embedding,
    )
    session.add(m)
    session.flush()
    if mtype == MemoryType.ANTI_PATTERN.value and severity:
        session.add(
            AntiPattern(
                memory_id=m.id,
                severity=severity,
                trigger=title,
                consequence="for test",
            )
        )
    session.commit()
    return m


# ── Threshold tunable ──


def test_threshold_defaults_to_092():
    """Spec: default cosine threshold is 0.92."""
    assert _semantic_dedup_threshold() == SEMANTIC_DEDUP_THRESHOLD_DEFAULT == 0.92


def test_threshold_env_override(monkeypatch):
    monkeypatch.setenv("MEMEE_SEMANTIC_DEDUP_THRESHOLD", "0.85")
    assert _semantic_dedup_threshold() == 0.85


def test_threshold_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MEMEE_SEMANTIC_DEDUP_THRESHOLD", "not-a-float")
    assert _semantic_dedup_threshold() == SEMANTIC_DEDUP_THRESHOLD_DEFAULT
    monkeypatch.setenv("MEMEE_SEMANTIC_DEDUP_THRESHOLD", "1.5")
    assert _semantic_dedup_threshold() == SEMANTIC_DEDUP_THRESHOLD_DEFAULT
    monkeypatch.setenv("MEMEE_SEMANTIC_DEDUP_THRESHOLD", "-0.1")
    assert _semantic_dedup_threshold() == SEMANTIC_DEDUP_THRESHOLD_DEFAULT


# ── Core behaviour ──


def test_near_identical_embeddings_get_folded(session):
    """Two same-type memories with cosine ~0.998 are folded into one.

    Winner keeps `validated` maturity; loser is deprecated and points
    at the winner via a ``semantic_dup_of`` edge. ``merge_count`` on
    the winner increments.
    """
    e1 = _vec(near=[1.0, 0.0, 0.0])
    e2 = _vec(near=[1.0, 0.05, 0.0])  # cosine ≈ 0.9988 with e1
    winner = _mk_memory(
        session,
        title="user prefers Vim",
        content="Editor preference: Vim",
        tags=["editor", "preference", "user"],
        embedding=e1,
        confidence=0.8,
    )
    loser = _mk_memory(
        session,
        title="Vim is the user's editor",
        content="The user said they like Vim",
        tags=["editor", "preference", "user"],
        embedding=e2,
        confidence=0.6,
    )

    stats = _semantic_dedup_pass(session)
    session.commit()
    session.refresh(winner)
    session.refresh(loser)

    assert stats["merged"] == 1
    assert winner.merge_count == 1
    assert loser.maturity == MaturityLevel.DEPRECATED.value
    edges = (
        session.query(MemoryConnection)
        .filter(
            MemoryConnection.source_id == loser.id,
            MemoryConnection.target_id == winner.id,
        )
        .all()
    )
    assert len(edges) == 1
    assert edges[0].relationship_type == "semantic_dup_of"
    # Audit trail recorded.
    assert any(
        entry.get("type") == "dedup_merge" for entry in (winner.evidence_chain or [])
    )


def test_below_threshold_not_merged(session):
    """Cosine 0.5 stays well below the 0.92 default — no merge."""
    e1 = _vec(near=[1.0, 0.0, 0.0])
    e2 = _vec(near=[0.5, 0.866, 0.0])  # cosine = 0.5
    a = _mk_memory(
        session,
        title="A pattern",
        content="aaa",
        tags=["python", "http"],
        embedding=e1,
        confidence=0.7,
    )
    b = _mk_memory(
        session,
        title="A different pattern",
        content="bbb",
        tags=["python", "http"],
        embedding=e2,
        confidence=0.7,
    )

    stats = _semantic_dedup_pass(session)
    session.refresh(a)
    session.refresh(b)
    assert stats["merged"] == 0
    assert a.maturity != MaturityLevel.DEPRECATED.value
    assert b.maturity != MaturityLevel.DEPRECATED.value


def test_cross_type_pair_is_skipped(session):
    """Even with near-identical embeddings, a PATTERN and an
    ANTI_PATTERN are never merged into each other."""
    e1 = _vec(near=[1.0, 0.0, 0.0])
    e2 = _vec(near=[1.0, 0.02, 0.0])  # cosine ≈ 0.9998
    pat = _mk_memory(
        session,
        title="Use parameterised SQL",
        content="bind params",
        tags=["sql", "security"],
        embedding=e1,
        mtype=MemoryType.PATTERN.value,
    )
    ap = _mk_memory(
        session,
        title="String-concat SQL with user input",
        content="injection risk",
        tags=["sql", "security"],
        embedding=e2,
        mtype=MemoryType.ANTI_PATTERN.value,
        severity="critical",
    )

    stats = _semantic_dedup_pass(session)
    session.refresh(pat)
    session.refresh(ap)
    assert stats["merged"] == 0
    assert pat.maturity != MaturityLevel.DEPRECATED.value
    assert ap.maturity != MaturityLevel.DEPRECATED.value


def test_already_edged_pair_is_skipped(session):
    """If a pair already has a graph edge (depends_on, supersedes, etc.)
    the semantic-dedup pass leaves it alone — collapsing the relationship
    would lose information the graph already encodes."""
    e1 = _vec(near=[1.0, 0.0, 0.0])
    e2 = _vec(near=[1.0, 0.03, 0.0])
    a = _mk_memory(
        session, title="A", content="aaa", tags=["x", "y"], embedding=e1,
    )
    b = _mk_memory(
        session, title="B", content="bbb", tags=["x", "y"], embedding=e2,
    )
    # Pre-existing supersedes edge.
    session.add(
        MemoryConnection(
            source_id=b.id,
            target_id=a.id,
            relationship_type="supersedes",
            strength=0.9,
        )
    )
    session.commit()

    stats = _semantic_dedup_pass(session)
    session.refresh(a)
    session.refresh(b)
    assert stats["merged"] == 0
    assert a.maturity != MaturityLevel.DEPRECATED.value
    assert b.maturity != MaturityLevel.DEPRECATED.value


def test_low_tag_overlap_skipped(session):
    """Even at high cosine, mismatched tag sets indicate the embedding
    is picking up shared phrasing on unrelated topics."""
    e1 = _vec(near=[1.0, 0.0, 0.0])
    e2 = _vec(near=[1.0, 0.02, 0.0])  # ~0.9998
    a = _mk_memory(
        session, title="Pattern A", content="x",
        tags=["python", "http"], embedding=e1,
    )
    b = _mk_memory(
        session, title="Pattern B", content="y",
        tags=["kotlin", "android"], embedding=e2,
    )

    stats = _semantic_dedup_pass(session)
    session.refresh(a)
    session.refresh(b)
    # Tag overlap = 0 → fails the 0.5 minimum, no merge.
    assert stats["merged"] == 0
    assert a.maturity != MaturityLevel.DEPRECATED.value


def test_cluster_cap_honoured(session):
    """merge_count ≥ LARGE_CLUSTER_MERGE_LIMIT on the winner blocks
    further folds — the cap exists in quality_gate for the same reason
    and we honour it here too."""
    from memee.engine.quality_gate import LARGE_CLUSTER_MERGE_LIMIT

    e1 = _vec(near=[1.0, 0.0, 0.0])
    e2 = _vec(near=[1.0, 0.03, 0.0])
    winner = _mk_memory(
        session, title="Already-big cluster",
        content="x", tags=["a", "b"], embedding=e1, confidence=0.9,
    )
    winner.merge_count = LARGE_CLUSTER_MERGE_LIMIT
    session.commit()
    loser = _mk_memory(
        session, title="Would-be addition",
        content="y", tags=["a", "b"], embedding=e2, confidence=0.6,
    )

    stats = _semantic_dedup_pass(session)
    session.refresh(winner)
    session.refresh(loser)
    assert stats["merged"] == 0
    assert loser.maturity != MaturityLevel.DEPRECATED.value


def test_higher_confidence_wins(session):
    """When confidences differ, the higher-confidence memory survives
    and the lower-confidence one gets deprecated."""
    e1 = _vec(near=[1.0, 0.0, 0.0])
    e2 = _vec(near=[1.0, 0.02, 0.0])
    low = _mk_memory(
        session, title="lower-conf", content="lo",
        tags=["x", "y"], embedding=e1, confidence=0.55,
    )
    high = _mk_memory(
        session, title="higher-conf", content="hi",
        tags=["x", "y"], embedding=e2, confidence=0.85,
    )

    _semantic_dedup_pass(session)
    session.refresh(low)
    session.refresh(high)
    assert high.maturity != MaturityLevel.DEPRECATED.value
    assert low.maturity == MaturityLevel.DEPRECATED.value


def test_idempotent_second_run_is_noop(session):
    """A second pass over the same corpus produces zero new merges —
    the loser is now DEPRECATED and skipped by the maturity gate."""
    e1 = _vec(near=[1.0, 0.0, 0.0])
    e2 = _vec(near=[1.0, 0.04, 0.0])
    _mk_memory(
        session, title="a", content="aa", tags=["x", "y"], embedding=e1,
        confidence=0.8,
    )
    _mk_memory(
        session, title="b", content="bb", tags=["x", "y"], embedding=e2,
        confidence=0.6,
    )

    s1 = _semantic_dedup_pass(session)
    s2 = _semantic_dedup_pass(session)
    assert s1["merged"] == 1
    assert s2["merged"] == 0


def test_empty_db_returns_zero(session):
    """No embeddings → nothing to do, stats all zero."""
    stats = _semantic_dedup_pass(session)
    assert stats["merged"] == 0
    assert stats["scanned_pairs"] == 0
