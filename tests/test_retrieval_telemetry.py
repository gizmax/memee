"""Retrieval telemetry: SearchEvent persistence + hit@k metrics.

Covers the wave-5 dashboard work:
* ``search_memories`` writes a SearchEvent per call (best-effort, env-gated).
* ``mark_event_accepted`` back-fills which result the caller actually used.
* ``compute_retrieval_metrics`` returns hit@1 / hit@3 / accepted_rate that
  match the hand-calculated fractions for a controlled fixture.
* The ``/api/v1/retrieval`` endpoint surfaces the same numbers across the
  1 / 7 / 30 day windows.
"""

from __future__ import annotations

import os

import pytest

from memee.engine.search import search_memories
from memee.engine.telemetry import (
    compute_retrieval_metrics,
    mark_event_accepted,
    record_search_event,
)
from memee.storage.models import MaturityLevel, Memory, MemoryType, SearchEvent


@pytest.fixture
def corpus(session):
    """Small fixture: 4 memories with distinct titles so searches can hit."""
    memories = [
        Memory(
            type=MemoryType.PATTERN.value,
            title="Use timeout on API calls",
            content="Always pass timeout=10 to requests.get to avoid hanging.",
            tags=["python", "api", "http"],
            confidence_score=0.9,
            maturity=MaturityLevel.VALIDATED.value,
        ),
        Memory(
            type=MemoryType.PATTERN.value,
            title="Enable SQLite WAL mode",
            content="WAL lets readers proceed during writes.",
            tags=["sqlite", "database"],
            confidence_score=0.8,
            maturity=MaturityLevel.VALIDATED.value,
        ),
        Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Do not use pypdf for complex PDFs",
            content="pypdf garbles layouts; use pymupdf or pdfplumber.",
            tags=["python", "pdf"],
            confidence_score=0.75,
            maturity=MaturityLevel.TESTED.value,
        ),
        Memory(
            type=MemoryType.LESSON.value,
            title="N+1 query bug in ORM relationships",
            content="Eager-load with selectinload to fix N+1 performance bugs.",
            tags=["orm", "sqlalchemy", "performance"],
            confidence_score=0.75,
            maturity=MaturityLevel.VALIDATED.value,
        ),
    ]
    session.add_all(memories)
    session.commit()
    return memories


def test_search_records_event(session, corpus):
    """Each search_memories call persists exactly one SearchEvent row."""
    os.environ["MEMEE_TELEMETRY"] = "1"

    before = session.query(SearchEvent).count()
    search_memories(session, "timeout API")
    search_memories(session, "SQLite WAL")

    events = session.query(SearchEvent).all()
    assert len(events) == before + 2
    last = session.query(SearchEvent).order_by(SearchEvent.created_at.desc()).first()
    assert last.query_text == "SQLite WAL"
    assert last.returned_count >= 1
    assert last.top_memory_id is not None
    assert last.latency_ms >= 0.0
    assert last.accepted_memory_id is None


def test_telemetry_disabled(session, corpus):
    """MEMEE_TELEMETRY=0 skips recording (no row written)."""
    os.environ["MEMEE_TELEMETRY"] = "0"
    try:
        before = session.query(SearchEvent).count()
        search_memories(session, "timeout API")
        after = session.query(SearchEvent).count()
        assert after == before
    finally:
        os.environ["MEMEE_TELEMETRY"] = "1"


def test_10_searches_and_hit_at_k_math(session, corpus):
    """Hand-built scenario with known hit@1 / hit@3 / accepted_rate.

    Plan: 10 searches. For each we mark acceptance at a known position:
      * 4 events accepted at position 0 (top) → contribute to hit@1 AND hit@3
      * 2 events accepted at position 1       → contribute to hit@3 only
      * 1 event  accepted at position 3       → NOT a hit@3 (position < 3 rule)
      * 3 events left untouched                → no acceptance

    Expected:
      hit@1              = 4 / 10 = 0.4  (accepted==top on 4 events)
      hit@3              = (4 + 2) / 10 = 0.6  (position < 3 on 6 events)
      accepted_rate      = 7 / 10 = 0.7
    """
    os.environ["MEMEE_TELEMETRY"] = "1"

    # Clean slate so math matches.
    session.query(SearchEvent).delete()
    session.commit()

    m_top = corpus[0]  # will be our "top" for the first-position scenario

    # Create 10 events by calling record_search_event directly. We can't rely
    # on search_memories always returning the same memory at position 0
    # across arbitrary queries, so we seed explicit events with a known
    # top_memory_id. This exercises record_search_event (which is what
    # search.py calls internally) without fighting the ranker.
    results_with_top = [{"memory": m_top, "total_score": 1.0}]
    for _ in range(10):
        record_search_event(session, "probe", results_with_top, latency_ms=5.0)

    events = (
        session.query(SearchEvent)
        .order_by(SearchEvent.created_at.asc())
        .all()
    )
    assert len(events) == 10
    for e in events:
        assert e.top_memory_id == m_top.id

    # 4 accepted at position 0 (acceptance == top)
    for ev in events[0:4]:
        mark_event_accepted(session, ev.id, m_top.id, position=0)
    # 2 accepted at position 1 (not top, but < 3)
    for ev in events[4:6]:
        mark_event_accepted(session, ev.id, corpus[1].id, position=1)
    # 1 accepted at position 3 (NOT < 3 → excluded from hit@3)
    mark_event_accepted(session, events[6].id, corpus[2].id, position=3)
    # events[7:10] remain unaccepted.

    # Sanity: the DB reflects the marks.
    accepted_rows = session.query(SearchEvent).filter(
        SearchEvent.accepted_memory_id.isnot(None)
    ).count()
    assert accepted_rows == 7

    metrics = compute_retrieval_metrics(session, window_days=30)
    assert metrics["total"] == 10
    assert metrics["accepted"] == 7
    assert metrics["hit_at_1"] == pytest.approx(0.4, abs=1e-4)
    assert metrics["hit_at_3"] == pytest.approx(0.6, abs=1e-4)
    assert metrics["accepted_memory_rate"] == pytest.approx(0.7, abs=1e-4)
    # p50 over 7 accepted latencies, all 5.0 ms in the probe seeding.
    assert metrics["time_to_solution_p50_ms"] == pytest.approx(5.0, abs=1e-4)


def test_mark_event_accepted_without_position(session, corpus):
    """Acceptance without position counts toward accepted_rate only."""
    os.environ["MEMEE_TELEMETRY"] = "1"
    session.query(SearchEvent).delete()
    session.commit()

    m_top = corpus[0]
    for _ in range(5):
        record_search_event(
            session, "probe", [{"memory": m_top, "total_score": 1.0}], latency_ms=2.0
        )
    events = session.query(SearchEvent).all()

    # Accept 3 without position. One with position 0 == top.
    for ev in events[:3]:
        mark_event_accepted(session, ev.id, m_top.id)  # no position
    mark_event_accepted(session, events[3].id, m_top.id, position=0)

    m = compute_retrieval_metrics(session, window_days=30)
    assert m["total"] == 5
    assert m["accepted"] == 4
    assert m["accepted_memory_rate"] == pytest.approx(0.8, abs=1e-4)
    # hit@1: 4 accepted events have accepted_memory_id == top_memory_id
    #   → 4/5 = 0.8
    assert m["hit_at_1"] == pytest.approx(0.8, abs=1e-4)
    # hit@3: only 1 event has a known position < 3.
    assert m["hit_at_3"] == pytest.approx(0.2, abs=1e-4)


def test_missing_event_id_returns_false(session):
    """mark_event_accepted is safe against unknown ids."""
    assert mark_event_accepted(session, "does-not-exist", "also-no") is False


def test_retrieval_api_endpoint_shape(session, corpus):
    """``/api/v1/retrieval`` returns the expected window/sparkline shape."""
    from fastapi.testclient import TestClient

    from memee.api.app import app
    from memee.api.routes.api_v1 import get_db

    os.environ["MEMEE_TELEMETRY"] = "1"
    session.query(SearchEvent).delete()
    session.commit()

    m_top = corpus[0]
    for _ in range(3):
        record_search_event(
            session, "probe", [{"memory": m_top, "total_score": 1.0}], latency_ms=1.0
        )
    events = session.query(SearchEvent).all()
    mark_event_accepted(session, events[0].id, m_top.id, position=0)

    # Override dependency so the endpoint uses our in-memory session.
    app.dependency_overrides[get_db] = lambda: session
    try:
        client = TestClient(app)
        r = client.get("/api/v1/retrieval")
        assert r.status_code == 200
        body = r.json()
        assert set(body["windows"].keys()) == {"day_1", "day_7", "day_30"}
        assert body["windows"]["day_7"]["total"] == 3
        assert body["windows"]["day_7"]["accepted"] == 1
        assert len(body["hit_at_1_sparkline_30d"]) == 30
        # Notes must flag the p50 as a proxy — downstream consumers rely on it.
        assert "proxy" in body["notes"]["time_to_solution_p50_ms"].lower()
    finally:
        app.dependency_overrides.pop(get_db, None)
