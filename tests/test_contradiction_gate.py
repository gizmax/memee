"""Tests for the v2.4.8 cross-encoder contradiction gate.

Before v2.4.8 the dream cycle classified every (pattern, anti-pattern)
pair sharing two tags as ``contradicts``. On a real 247-memory live
install that produced 500 false positives — co-tagged orthogonal advice
("Use connection pooling for SQLAlchemy" + "Never run CPU-bound code in
the asyncio event loop") was flagged as contradictory because both
carried the ``async`` and ``sqlalchemy`` tags.

These tests pin the new gate's contract:

* High semantic score → ``contradicts``.
* Low semantic score → ``related_to`` (the orthogonal-advice case).
* No scorer / scorer can't load → ``related_to`` (fail-safe).
* ``MEMEE_CONTRADICTION_THRESHOLD`` overrides the default.
* The per-cycle cache calls the underlying model at most once per pair.

We never actually load a cross-encoder here; the scorer is wrapped in a
fake so the suite stays deterministic and offline.
"""

from __future__ import annotations

import pytest

from memee.engine.dream import (
    CONTRADICTION_THRESHOLD_DEFAULT,
    _ContradictionScorer,
    _contradiction_threshold,
    _infer_relationship,
    run_dream_cycle,
)
from memee.storage.models import (
    AntiPattern,
    Memory,
    MemoryConnection,
    MemoryType,
)


# ── _infer_relationship unit tests ────────────────────────────────────


def _make_pair(session):
    """Seed one pattern + one anti-pattern sharing tags, return both."""
    pat = Memory(
        type=MemoryType.PATTERN.value,
        title="Use connection pooling for SQLAlchemy in async services",
        content="Wraps the async engine with NullPool / AsyncAdaptedQueuePool.",
        tags=["python", "sqlalchemy", "async"],
    )
    anti = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        title="Never run CPU-bound code in the asyncio event loop",
        content="Block the loop and every coroutine stalls.",
        tags=["python", "async", "performance"],
    )
    session.add_all([pat, anti])
    session.flush()
    session.add(
        AntiPattern(
            memory_id=anti.id,
            severity="high",
            trigger="CPU-bound work in coroutine body",
            consequence="event loop stalls, latency spikes",
        )
    )
    session.commit()
    return pat, anti


class FakeScorer:
    """Deterministic stand-in for ``_ContradictionScorer``.

    ``canned_score`` is what every ``score_pair`` returns; ``calls``
    records (a.id, b.id) tuples so the caching test can count
    underlying invocations.
    """

    def __init__(self, canned_score: float | None) -> None:
        self.canned_score = canned_score
        self.calls: list[tuple[str, str]] = []

    def score_pair(self, m1, m2):
        self.calls.append((m1.id, m2.id))
        return self.canned_score


def test_low_score_classifies_as_related_to(session):
    """Co-tagged but semantically unrelated → ``related_to``."""
    pat, anti = _make_pair(session)
    scorer = FakeScorer(canned_score=0.3)
    rel = _infer_relationship(pat, anti, scorer=scorer)
    assert rel == "related_to"


def test_high_score_classifies_as_contradicts(session):
    """Truly contradictory pair (high cross-encoder score) → ``contradicts``."""
    pat, anti = _make_pair(session)
    scorer = FakeScorer(canned_score=0.8)
    rel = _infer_relationship(pat, anti, scorer=scorer)
    assert rel == "contradicts"


def test_no_scorer_fails_safe_to_related_to(session):
    """Without a scorer the gate refuses to claim a contradiction."""
    pat, anti = _make_pair(session)
    rel = _infer_relationship(pat, anti, scorer=None)
    assert rel == "related_to"


def test_scorer_returns_none_falls_back_to_related_to(session):
    """Scorer signals "can't load model" by returning ``None`` → ``related_to``."""
    pat, anti = _make_pair(session)
    rel = _infer_relationship(pat, anti, scorer=FakeScorer(canned_score=None))
    assert rel == "related_to"


def test_threshold_default_constant_matches_module():
    assert _contradiction_threshold() == CONTRADICTION_THRESHOLD_DEFAULT


def test_env_var_overrides_threshold(monkeypatch, session):
    """``MEMEE_CONTRADICTION_THRESHOLD=0.95`` raises the bar."""
    pat, anti = _make_pair(session)
    monkeypatch.setenv("MEMEE_CONTRADICTION_THRESHOLD", "0.95")
    assert _contradiction_threshold() == pytest.approx(0.95)
    # 0.8 was a contradiction at the default 0.55; now it should not be.
    rel = _infer_relationship(pat, anti, scorer=FakeScorer(0.8))
    assert rel == "related_to"
    rel = _infer_relationship(pat, anti, scorer=FakeScorer(0.97))
    assert rel == "contradicts"


def test_env_var_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MEMEE_CONTRADICTION_THRESHOLD", "not-a-float")
    assert _contradiction_threshold() == CONTRADICTION_THRESHOLD_DEFAULT


def test_same_type_pair_still_supports(session):
    """The gate only fires on cross-type pairs; same-type contracts intact."""
    a = Memory(
        type=MemoryType.PATTERN.value,
        title="Always set requests timeout",
        content="timeout=10 prevents hangs",
        tags=["python", "http"],
    )
    b = Memory(
        type=MemoryType.PATTERN.value,
        title="Always retry on 5xx",
        content="exponential backoff",
        tags=["python", "http"],
    )
    session.add_all([a, b])
    session.commit()
    rel = _infer_relationship(a, b, scorer=FakeScorer(0.9))
    assert rel == "supports"


# ── _ContradictionScorer caching tests ─────────────────────────────────


def test_scorer_caches_per_pair(session, monkeypatch):
    """Scoring the same pair twice hits the model exactly once.

    We mock the reranker loader so the test never actually loads
    sentence-transformers, then assert ``predict`` was called once.
    """
    pat, anti = _make_pair(session)

    call_log: list = []

    class FakeModel:
        def predict(self, pairs):
            call_log.append(pairs)
            # cross-encoders return numpy arrays; a plain list works too
            # because _ContradictionScorer coerces via ``float(score[0])``.
            return [0.8] * len(pairs)

    fake_model = FakeModel()

    # Patch the underlying loader path the scorer reaches into. The
    # scorer reaches into ``memee.engine.reranker._try_load`` and
    # ``_model_name_from_env``; patching both keeps the test offline.
    import memee.engine.reranker as reranker_mod

    monkeypatch.setattr(reranker_mod, "_model_name_from_env", lambda: "fake/model")
    monkeypatch.setattr(reranker_mod, "_try_load", lambda name: fake_model)

    scorer = _ContradictionScorer()
    s1 = scorer.score_pair(pat, anti)
    s2 = scorer.score_pair(pat, anti)
    s3 = scorer.score_pair(anti, pat)  # reversed order — cache is symmetric

    assert s1 == pytest.approx(0.8)
    assert s2 == pytest.approx(0.8)
    assert s3 == pytest.approx(0.8)
    assert len(call_log) == 1, f"model.predict should fire once, got {len(call_log)}"


def test_scorer_returns_none_when_model_unavailable(monkeypatch, session):
    """No model name configured → scorer returns ``None`` (fail-safe)."""
    pat, anti = _make_pair(session)

    import memee.engine.reranker as reranker_mod

    monkeypatch.setattr(reranker_mod, "_model_name_from_env", lambda: None)

    scorer = _ContradictionScorer()
    assert scorer.score_pair(pat, anti) is None


def test_scorer_returns_none_when_load_fails(monkeypatch, session):
    """``_try_load`` returning None propagates as scorer.score_pair() → None."""
    pat, anti = _make_pair(session)

    import memee.engine.reranker as reranker_mod

    monkeypatch.setattr(reranker_mod, "_model_name_from_env", lambda: "fake/model")
    monkeypatch.setattr(reranker_mod, "_try_load", lambda name: None)

    scorer = _ContradictionScorer()
    assert scorer.score_pair(pat, anti) is None


# ── End-to-end through run_dream_cycle ────────────────────────────────


def test_dream_cycle_no_contradicts_under_threshold(session):
    """Low-score scorer ⇒ no contradicts edges land in the graph."""
    _make_pair(session)
    stats = run_dream_cycle(session, scorer=FakeScorer(0.2))
    assert stats["contradictions_found"] == 0
    edges = session.query(MemoryConnection).filter_by(
        relationship_type="contradicts"
    ).all()
    assert edges == []


def test_dream_cycle_creates_contradicts_above_threshold(session):
    """High-score scorer ⇒ contradicts edge exists after the cycle."""
    _make_pair(session)
    stats = run_dream_cycle(session, scorer=FakeScorer(0.9))
    assert stats["contradictions_found"] >= 1
    edges = session.query(MemoryConnection).filter_by(
        relationship_type="contradicts"
    ).all()
    assert len(edges) >= 1
