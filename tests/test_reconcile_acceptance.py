"""v2.4.13 — confirming a citation closes the retrieval-feedback loop.

Searches log SearchEvents, but ``accepted_memory_id`` stayed 0% because the
agent never sees a raw event id to feed back. Now an explicit
``memee cite --confirm`` reconciles to the most recent search that surfaced
the memory and marks it accepted — so hit@k / acceptance-rate reflect real
use.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memee.engine.citations import confirm_citation
from memee.engine.telemetry import reconcile_acceptance
from memee.storage.models import (
    MaturityLevel,
    Memory,
    MemoryType,
    SearchEvent,
    SearchRankingSnapshot,
)


def _mk_memory(session, org, title="Use connection pooling"):
    m = Memory(
        organization_id=org.id,
        type=MemoryType.PATTERN.value,
        title=title,
        content=title + " in production to avoid exhaustion",
        tags=["database"],
        maturity=MaturityLevel.HYPOTHESIS.value,
        confidence_score=0.5,
    )
    session.add(m)
    session.flush()
    return m


def _mk_event(session, *, top_memory_id=None, age_seconds=0, accepted=None):
    ev = SearchEvent(
        query_text="how to pool connections",
        returned_count=3,
        top_memory_id=top_memory_id,
        accepted_memory_id=accepted,
        latency_ms=5.0,
        ranker_version="rrf_v1",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )
    session.add(ev)
    session.flush()
    return ev


def test_reconcile_marks_top_hit(session, org):
    m = _mk_memory(session, org)
    ev = _mk_event(session, top_memory_id=m.id)
    session.commit()

    marked = reconcile_acceptance(session, m.id)
    assert marked == ev.id
    session.expire_all()
    refreshed = session.get(SearchEvent, ev.id)
    assert refreshed.accepted_memory_id == m.id
    assert refreshed.position_of_accepted == 0


def test_reconcile_marks_via_ranking_snapshot(session, org):
    m = _mk_memory(session, org)
    ev = _mk_event(session, top_memory_id="some-other-id")
    session.add(
        SearchRankingSnapshot(
            event_id=ev.id, memory_id=m.id, rank=2, rrf_score=0.3,
        )
    )
    session.commit()

    marked = reconcile_acceptance(session, m.id)
    assert marked == ev.id
    session.expire_all()
    assert session.get(SearchEvent, ev.id).position_of_accepted == 2


def test_reconcile_prefers_most_recent_event(session, org):
    m = _mk_memory(session, org)
    old = _mk_event(session, top_memory_id=m.id, age_seconds=3600)
    new = _mk_event(session, top_memory_id=m.id, age_seconds=10)
    session.commit()

    marked = reconcile_acceptance(session, m.id)
    assert marked == new.id
    session.expire_all()
    assert session.get(SearchEvent, old.id).accepted_memory_id is None


def test_reconcile_skips_already_accepted(session, org):
    m = _mk_memory(session, org)
    _mk_event(session, top_memory_id=m.id, accepted=m.id, age_seconds=10)
    session.commit()
    # Only event already has acceptance → nothing new to mark.
    assert reconcile_acceptance(session, m.id) is None


def test_reconcile_returns_none_when_no_event_surfaced_memory(session, org):
    m = _mk_memory(session, org)
    _mk_event(session, top_memory_id="unrelated")
    session.commit()
    assert reconcile_acceptance(session, m.id) is None


def test_reconcile_respects_lookback_window(session, org):
    m = _mk_memory(session, org)
    # 5 newer unaccepted events that do NOT surface m, then 1 older that does.
    for i in range(5):
        _mk_event(session, top_memory_id="other", age_seconds=i)
    target = _mk_event(session, top_memory_id=m.id, age_seconds=100)
    session.commit()
    # Lookback of 3 newest can't reach the target → None.
    assert reconcile_acceptance(session, m.id, max_lookback_events=3) is None
    # Full lookback finds it.
    assert reconcile_acceptance(session, m.id, max_lookback_events=50) == target.id


def test_confirm_citation_reconciles_acceptance(session, org):
    """End-to-end: cite --confirm fills accepted_memory_id."""
    m = _mk_memory(session, org)
    ev = _mk_event(session, top_memory_id=m.id, age_seconds=5)
    session.commit()

    result = confirm_citation(session, m, note="applied")
    assert result["reconciled_event_id"] == ev.id
    session.expire_all()
    assert session.get(SearchEvent, ev.id).accepted_memory_id == m.id
