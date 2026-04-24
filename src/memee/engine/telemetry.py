"""Retrieval telemetry — record a SearchEvent per search_memories call.

Design notes
------------
* Called **best-effort** from ``search_memories``. Any exception (missing
  table on an un-migrated DB, locked DB during a write storm, serialization
  error, etc.) must NOT break the caller's search. We catch everything.
* Gated by the ``MEMEE_TELEMETRY`` env var (default ON). Set to ``0``,
  ``off``, or ``false`` to disable — useful for perf benchmarks and for
  embedded agents that really cannot afford a write per search.
* The acceptance side is a separate helper (``mark_event_accepted``) that
  the MCP tool ``search_feedback`` and the CLI ``memee feedback`` both
  call. It is intentionally split from recording because we rarely know at
  search-time which (if any) result the agent will actually use.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

from sqlalchemy.orm import Session

from memee.storage.models import SearchEvent

logger = logging.getLogger(__name__)


def _telemetry_enabled() -> bool:
    """Default ON. Explicit MEMEE_TELEMETRY=0/off/false disables."""
    v = os.environ.get("MEMEE_TELEMETRY", "1").strip().lower()
    return v not in ("0", "off", "false", "no")


def record_search_event(
    session: Session,
    query_text: str,
    results: Iterable,
    latency_ms: float,
) -> str | None:
    """Persist a SearchEvent for this search.

    Parameters
    ----------
    results:
        The list-of-dicts returned by ``search_memories``. We only need the
        top row's memory id and the count — we don't store the full payload.
    latency_ms:
        Wall time for the whole search, measured by the caller (search.py).

    Returns the new event id, or ``None`` if telemetry was skipped or errored.
    Callers MUST tolerate a ``None`` return.
    """
    if not _telemetry_enabled():
        return None
    try:
        results_list = list(results) if not isinstance(results, list) else results
        top_memory_id = None
        if results_list:
            top = results_list[0]
            mem = top.get("memory") if isinstance(top, dict) else None
            if mem is not None:
                top_memory_id = getattr(mem, "id", None)

        event = SearchEvent(
            query_text=(query_text or "")[:2000],
            returned_count=len(results_list),
            top_memory_id=top_memory_id,
            latency_ms=float(latency_ms) if latency_ms is not None else 0.0,
        )
        session.add(event)
        # Flush only — do NOT commit. Committing on every search adds a
        # fsync per call and blew the 600-query perf budget by ~70%. The
        # row is visible to the same session immediately and will be
        # durably written on the next natural commit. If the process dies
        # before that, we lose a few telemetry rows — acceptable, telemetry
        # is best-effort by contract.
        session.flush()
        return event.id
    except Exception as e:  # pragma: no cover — telemetry must never break search
        logger.debug("telemetry: record_search_event failed: %s", e)
        try:
            session.rollback()
        except Exception:
            pass
        return None


def mark_event_accepted(
    session: Session,
    event_id: str,
    memory_id: str,
    position: int | None = None,
) -> bool:
    """Record that the caller used ``memory_id`` from the search.

    ``position`` is the 0-based rank inside the returned list. If the caller
    doesn't know (common for MCP tools that lost the ordering), leave it
    None — we can still count the acceptance, just not the hit@3 bucket.

    Returns True on success, False if the event is missing or the write
    failed. Callers may ignore the return.
    """
    try:
        ev = session.get(SearchEvent, event_id)
        if ev is None:
            return False
        ev.accepted_memory_id = memory_id
        if position is not None:
            ev.position_of_accepted = int(position)
        session.commit()
        return True
    except Exception as e:
        logger.debug("telemetry: mark_event_accepted failed: %s", e)
        try:
            session.rollback()
        except Exception:
            pass
        return False


def compute_retrieval_metrics(session: Session, window_days: int) -> dict:
    """Compute hit@1 / hit@3 / accepted_rate / p50 latency over a window.

    Window: events with ``created_at >= now - window_days``.
    All ratios are reported as floats in [0, 1]; latency is milliseconds.
    When there are zero events, all metrics are 0 and ``total`` is 0 — we do
    NOT raise or return None, so dashboard consumers can render a flat panel.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import and_, func

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    base_q = session.query(SearchEvent).filter(SearchEvent.created_at >= cutoff)
    total = base_q.count()
    if total == 0:
        return {
            "window_days": window_days,
            "total": 0,
            "accepted": 0,
            "hit_at_1": 0.0,
            "hit_at_3": 0.0,
            "accepted_memory_rate": 0.0,
            "time_to_solution_p50_ms": 0.0,
        }

    accepted_q = base_q.filter(SearchEvent.accepted_memory_id.isnot(None))
    accepted = accepted_q.count()

    # hit@1: accepted == top. Counted on ALL events (share of events where
    # the caller both accepted AND their pick equals the top result).
    hit_at_1_count = (
        base_q.filter(
            and_(
                SearchEvent.accepted_memory_id.isnot(None),
                SearchEvent.top_memory_id.isnot(None),
                SearchEvent.accepted_memory_id == SearchEvent.top_memory_id,
            )
        ).count()
    )

    # hit@3: acceptance with known position < 3.
    hit_at_3_count = (
        base_q.filter(
            and_(
                SearchEvent.position_of_accepted.isnot(None),
                SearchEvent.position_of_accepted < 3,
            )
        ).count()
    )

    # p50 latency among accepted events (proxy for time-to-solution, since we
    # don't yet emit a "solved" event). Reported as a proxy in the API.
    latencies = [
        row[0]
        for row in accepted_q.with_entities(SearchEvent.latency_ms).all()
        if row[0] is not None
    ]
    p50 = _p50(latencies)

    return {
        "window_days": window_days,
        "total": total,
        "accepted": accepted,
        "hit_at_1": round(hit_at_1_count / total, 4),
        "hit_at_3": round(hit_at_3_count / total, 4),
        "accepted_memory_rate": round(accepted / total, 4),
        "time_to_solution_p50_ms": round(p50, 2),
    }


def _p50(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def hit_at_1_sparkline(session: Session, days: int = 30) -> list[dict]:
    """One entry per calendar day: {date, hit_at_1, total}. Newest last.

    Used by the dashboard 30-day sparkline. Days with zero events report
    hit_at_1 = 0 so the chart has a complete time axis.
    """
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone.utc).date()
    out: list[dict] = []
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        day_q = session.query(SearchEvent).filter(
            SearchEvent.created_at >= start, SearchEvent.created_at < end
        )
        total = day_q.count()
        if total == 0:
            out.append({"date": day.isoformat(), "hit_at_1": 0.0, "total": 0})
            continue
        from sqlalchemy import and_

        hit1 = day_q.filter(
            and_(
                SearchEvent.accepted_memory_id.isnot(None),
                SearchEvent.top_memory_id.isnot(None),
                SearchEvent.accepted_memory_id == SearchEvent.top_memory_id,
            )
        ).count()
        out.append({
            "date": day.isoformat(),
            "hit_at_1": round(hit1 / total, 4),
            "total": total,
        })
    return out
