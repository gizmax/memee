"""Hard-negative mining for the LTR retraining loop (R9 #4).

A "hard negative" is a memory that the heuristic ranker placed at the top
of a search but the agent rejected in favour of a memory ranked lower —
the most informative training signal for a learned reranker. We persist
per-candidate features at search time (``SearchRankingSnapshot``) so this
mining job is purely SQL + feature reconstruction; no need to recompute
from a possibly-mutated ``Memory`` row.

Two public entry points:

* :func:`mine_hard_negatives` — produces ``(rejected_top, accepted_lower)``
  records ready for the LTR trainer or for offline export.
* :func:`export_hard_negatives_jsonl` — writes the records to JSONL so
  external trainers / experiments can consume them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class HardNegative:
    event_id: str
    query_text: str
    rejected_memory_id: str
    rejected_features: list[float]
    accepted_memory_id: str
    accepted_features: list[float]
    accepted_position: int
    created_at: str  # ISO8601
    stale: bool      # True if either snapshot's memory has changed since the event


def _features_from_snapshot(snapshot, query_text: str) -> list[float]:
    """Materialise the v1 feature vector from a stored snapshot row. Layout
    mirrors :data:`memee.engine.ltr.FEATURE_NAMES`."""
    from memee.engine.ltr import _MATURITY_MULT, _TYPE_ENCODE

    return [
        float(snapshot.bm25_score or 0.0),
        float(-1 if snapshot.bm25_rank is None else snapshot.bm25_rank),
        float(snapshot.vector_score or 0.0),
        float(-1 if snapshot.vector_rank is None else snapshot.vector_rank),
        float(snapshot.rrf_score or 0.0),
        float(snapshot.memory_confidence or 0.0),
        float(_MATURITY_MULT.get(snapshot.memory_maturity or "", 0.5)),
        float(snapshot.memory_validation_count or 0),
        float(_TYPE_ENCODE.get(snapshot.memory_type or "", -1)),
        float(len(query_text or "")),
        1.0 if "?" in (query_text or "") else 0.0,
    ]


def _is_stale(session: Session, snapshot, event_created_at) -> bool:
    """Heuristic drift guard: if the underlying ``Memory`` row's
    ``updated_at`` is later than the search event, the snapshot's
    memory-side features may no longer reflect what was ranked. We mark
    those rows ``stale=True`` so the trainer can drop or weight them.
    """
    from memee.storage.models import Memory

    m = session.get(Memory, snapshot.memory_id)
    if m is None:
        return True
    upd = m.updated_at
    if upd is None:
        return False
    if upd.tzinfo is None:
        upd = upd.replace(tzinfo=timezone.utc)
    base = event_created_at
    if base is None:
        return False
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return upd > base


def mine_hard_negatives(
    session: Session,
    *,
    since_days: int | None = None,
    drop_stale: bool = True,
) -> list[HardNegative]:
    """Mine ``(rejected_top, accepted_lower)`` pairs from telemetry.

    Parameters
    ----------
    since_days:
        Only consider events created within this window. ``None`` keeps the
        full history (handy for an initial backfill).
    drop_stale:
        Drop rows where the memory's ``updated_at`` is later than the event;
        those features no longer reflect what was actually ranked.
    """
    from memee.storage.models import SearchEvent, SearchRankingSnapshot

    q = session.query(SearchEvent).filter(
        SearchEvent.accepted_memory_id.isnot(None),
        SearchEvent.top_memory_id.isnot(None),
        SearchEvent.accepted_memory_id != SearchEvent.top_memory_id,
    )
    if since_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        q = q.filter(SearchEvent.created_at >= cutoff)
    events = q.all()

    out: list[HardNegative] = []
    for ev in events:
        snaps = (
            session.query(SearchRankingSnapshot)
            .filter(SearchRankingSnapshot.event_id == ev.id)
            .all()
        )
        if not snaps:
            continue
        by_memory = {s.memory_id: s for s in snaps}
        rejected = by_memory.get(ev.top_memory_id)
        accepted = by_memory.get(ev.accepted_memory_id)
        if rejected is None or accepted is None:
            continue
        rej_stale = _is_stale(session, rejected, ev.created_at)
        acc_stale = _is_stale(session, accepted, ev.created_at)
        stale = rej_stale or acc_stale
        if drop_stale and stale:
            continue
        out.append(
            HardNegative(
                event_id=ev.id,
                query_text=ev.query_text or "",
                rejected_memory_id=ev.top_memory_id,
                rejected_features=_features_from_snapshot(
                    rejected, ev.query_text or ""
                ),
                accepted_memory_id=ev.accepted_memory_id,
                accepted_features=_features_from_snapshot(
                    accepted, ev.query_text or ""
                ),
                accepted_position=int(ev.position_of_accepted or accepted.rank or 0),
                created_at=(
                    ev.created_at.isoformat() if ev.created_at else ""
                ),
                stale=stale,
            )
        )
    return out


def export_hard_negatives_jsonl(
    session: Session,
    path: Path,
    *,
    since_days: int | None = None,
) -> int:
    """Write hard-negative pairs to JSONL. Returns count written."""
    rows: Iterable[HardNegative] = mine_hard_negatives(
        session, since_days=since_days
    )
    n = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(asdict(r)) + "\n")
            n += 1
    logger.info("Wrote %d hard-negative rows to %s", n, path)
    return n
