"""R-future #2: Canon ledger — read surface over the R9 graph schema.

R9 shipped the schema (``MemoryConnection`` with ``depends_on`` /
``supersedes`` / ``contradicts``) and the dream-cycle inference passes.
Briefing reads the edges; lifecycle blocks deprecation when CANON
depends. What's missing is the **read surface** — the API/CLI that
turns the existing graph into a queryable claim ledger.

This module is the substrate. Five public functions:

- ``canon_state(session)`` — set of CANON memory ids that no other
  CANON memory has marked ``contradicts`` or ``supersedes``.
- ``contradiction_pairs(session)`` — pairs of CANON memories that
  contradict each other (a knowledge-base inconsistency).
- ``provenance(session, memory_id)`` — full evidence chain + 1-hop
  graph context for one memory.
- ``timeline(session, project_id=None, tag=None)`` — chronological
  ordering of canon emergence + supersession events.
- ``audit_export(session, scope='org')`` — JSON dump suitable for
  compliance ingestion.

All functions are read-only and side-effect-free. The graph data they
read comes from R9 plumbing that already runs nightly via dream cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from memee.storage.models import (
    MaturityLevel,
    Memory,
    MemoryConnection,
    ProjectMemory,
)

logger = logging.getLogger(__name__)


# ── canon_state ───────────────────────────────────────────────────────────


def canon_state(session: Session) -> dict:
    """Return the contradiction-free CANON set + the conflicts.

    A CANON memory is in the state set iff:
      1. its maturity is ``canon``
      2. no edge ``contradicts`` exists between it and another CANON
      3. no edge ``supersedes`` makes it the *target* of a CANON source
         (i.e. it hasn't been superseded by a newer CANON)

    Returns ``{
        "canon_total": int,
        "contradiction_free": int,
        "conflicts": [{a, b, type}],
        "superseded": [{loser, winner}],
    }``.
    """
    canon_ids = {
        mid for (mid,) in session.query(Memory.id)
        .filter(Memory.maturity == MaturityLevel.CANON.value)
        .all()
    }
    if not canon_ids:
        return {
            "canon_total": 0,
            "contradiction_free": 0,
            "conflicts": [],
            "superseded": [],
        }

    # Conflicts: canon ↔ canon contradicts edges (both endpoints in canon set).
    conflicts = []
    for src, tgt in (
        session.query(MemoryConnection.source_id, MemoryConnection.target_id)
        .filter(
            MemoryConnection.relationship_type == "contradicts",
            MemoryConnection.source_id.in_(canon_ids),
            MemoryConnection.target_id.in_(canon_ids),
        )
        .all()
    ):
        conflicts.append({"a": src, "b": tgt, "type": "contradicts"})

    # Superseded: canon source supersedes a canon target. The target should
    # be excluded from the contradiction-free set; the source stays.
    superseded = []
    superseded_targets: set[str] = set()
    for src, tgt in (
        session.query(MemoryConnection.source_id, MemoryConnection.target_id)
        .filter(
            MemoryConnection.relationship_type == "supersedes",
            MemoryConnection.source_id.in_(canon_ids),
            MemoryConnection.target_id.in_(canon_ids),
        )
        .all()
    ):
        superseded.append({"loser": tgt, "winner": src})
        superseded_targets.add(tgt)

    # Memories appearing in any conflict are flagged but not auto-excluded
    # (the operator decides which side wins). Superseded targets ARE excluded.
    conflicting_ids: set[str] = set()
    for c in conflicts:
        conflicting_ids.add(c["a"])
        conflicting_ids.add(c["b"])

    contradiction_free_ids = canon_ids - superseded_targets - conflicting_ids
    return {
        "canon_total": len(canon_ids),
        "contradiction_free": len(contradiction_free_ids),
        "conflicts": conflicts,
        "superseded": superseded,
        "contradiction_free_ids": list(contradiction_free_ids),
    }


# ── contradiction_pairs ───────────────────────────────────────────────────


def contradiction_pairs(session: Session) -> list[dict]:
    """List every (canon, canon) contradicts pair with both titles + the
    severity rank of either side (if anti-pattern). Used by dashboard
    alerting and the rule engine that fails closed on canon disagreement.
    """
    pairs = (
        session.query(MemoryConnection.source_id, MemoryConnection.target_id)
        .filter(MemoryConnection.relationship_type == "contradicts")
        .all()
    )
    if not pairs:
        return []

    ids = {sid for sid, _ in pairs} | {tid for _, tid in pairs}
    by_id = {
        m.id: m
        for m in session.query(Memory).filter(Memory.id.in_(ids)).all()
    }

    out = []
    for src, tgt in pairs:
        a = by_id.get(src)
        b = by_id.get(tgt)
        if not (a and b):
            continue
        if a.maturity != MaturityLevel.CANON.value:
            continue
        if b.maturity != MaturityLevel.CANON.value:
            continue
        out.append({
            "a_id": a.id,
            "a_title": a.title,
            "a_confidence": a.confidence_score,
            "b_id": b.id,
            "b_title": b.title,
            "b_confidence": b.confidence_score,
        })
    return out


# ── provenance ────────────────────────────────────────────────────────────


def provenance(session: Session, memory_id: str) -> dict | None:
    """Walk the evidence chain + 1-hop graph context for one memory.

    Returns ``None`` if the memory doesn't exist; otherwise:
      ``{memory: {...}, evidence_chain: [...], depends_on: [...],
        supersedes: [...], contradicts: [...], supports: [...]}``
    """
    m = session.get(Memory, memory_id)
    if m is None:
        return None

    edges = (
        session.query(MemoryConnection)
        .filter(
            or_(
                MemoryConnection.source_id == memory_id,
                MemoryConnection.target_id == memory_id,
            )
        )
        .all()
    )
    out: dict = {
        "memory": {
            "id": m.id,
            "type": m.type,
            "title": m.title,
            "maturity": m.maturity,
            "confidence_score": m.confidence_score,
            "validation_count": m.validation_count,
            "invalidation_count": m.invalidation_count,
            "tags": m.tags or [],
            "created_at": m.created_at.isoformat() if m.created_at else None,
        },
        "evidence_chain": list(m.evidence_chain or []),
        "depends_on": [],
        "supersedes": [],
        "contradicts": [],
        "supports": [],
        "related_to": [],
    }

    other_ids = {
        e.target_id if e.source_id == memory_id else e.source_id
        for e in edges
    }
    titles = {
        oid: title for oid, title in session.query(Memory.id, Memory.title)
        .filter(Memory.id.in_(other_ids))
        .all()
    }
    for e in edges:
        bucket = e.relationship_type if e.relationship_type in out else "related_to"
        if e.source_id == memory_id:
            other = e.target_id
            direction = "→"
        else:
            other = e.source_id
            direction = "←"
        out[bucket].append({
            "id": other,
            "title": titles.get(other, "<unknown>"),
            "direction": direction,
            "strength": e.strength,
        })
    return out


# ── timeline ──────────────────────────────────────────────────────────────


def timeline(
    session: Session,
    project_id: str | None = None,
    tag: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Chronological list of canon emergence + supersession events.

    Each entry: ``{date, kind, memory_id, memory_title, …}``. Kinds:
      * ``created`` — memory first inserted
      * ``promoted`` — maturity transition (today the schema doesn't
        log promotion timestamps explicitly; we use ``last_validated_at``
        as a proxy when maturity == canon)
      * ``deprecated`` — maturity transition to deprecated
      * ``superseded`` — incoming ``supersedes`` edge
      * ``contradicted`` — incoming ``contradicts`` edge

    ``project_id`` filters to memories linked to that project.
    ``tag`` filters to memories with that tag.
    """
    q = session.query(Memory)
    if project_id:
        q = q.join(ProjectMemory, ProjectMemory.memory_id == Memory.id).filter(
            ProjectMemory.project_id == project_id
        )
    memories = q.all()
    if tag:
        memories = [m for m in memories if tag in (m.tags or [])]

    events: list[tuple[datetime, dict]] = []
    for m in memories:
        if m.created_at:
            events.append((m.created_at, {
                "kind": "created",
                "memory_id": m.id,
                "memory_title": m.title,
                "maturity_at_event": "hypothesis",
            }))
        if m.maturity == MaturityLevel.CANON.value and m.last_validated_at:
            events.append((m.last_validated_at, {
                "kind": "promoted_canon",
                "memory_id": m.id,
                "memory_title": m.title,
                "maturity_at_event": "canon",
            }))
        if m.maturity == MaturityLevel.DEPRECATED.value and m.deprecated_at:
            events.append((m.deprecated_at, {
                "kind": "deprecated",
                "memory_id": m.id,
                "memory_title": m.title,
                "deprecated_reason": m.deprecated_reason,
            }))

    # Supersession + contradiction events from edges.
    target_ids = {m.id for m in memories}
    if target_ids:
        edges = (
            session.query(MemoryConnection)
            .filter(
                MemoryConnection.target_id.in_(target_ids),
                MemoryConnection.relationship_type.in_(("supersedes", "contradicts")),
            )
            .all()
        )
        edge_titles = {
            sid: title for sid, title in session.query(Memory.id, Memory.title)
            .filter(Memory.id.in_({e.source_id for e in edges}))
            .all()
        }
        for e in edges:
            if e.created_at:
                events.append((e.created_at, {
                    "kind": e.relationship_type,
                    "memory_id": e.target_id,
                    "memory_title": next(
                        (m.title for m in memories if m.id == e.target_id), "?"
                    ),
                    "by_id": e.source_id,
                    "by_title": edge_titles.get(e.source_id, "<unknown>"),
                }))

    events.sort(key=lambda x: x[0])
    return [{"date": ts.isoformat(), **payload} for ts, payload in events[:limit]]


# ── audit_export ──────────────────────────────────────────────────────────


def audit_export(session: Session) -> dict:
    """Compliance-ready JSON dump: every CANON memory with its evidence
    chain, validations, and graph context.

    The structure mirrors what an external auditor would ingest: one
    record per canon memory with provenance trail + relationship edges.
    """
    state = canon_state(session)
    canon_records = []
    canon_q = session.query(Memory).filter(
        Memory.maturity == MaturityLevel.CANON.value
    ).all()

    for m in canon_q:
        prov = provenance(session, m.id)
        canon_records.append({
            "id": m.id,
            "type": m.type,
            "title": m.title,
            "tags": m.tags or [],
            "confidence": m.confidence_score,
            "validation_count": m.validation_count,
            "model_count": m.model_count,
            "evidence_chain": prov.get("evidence_chain", []) if prov else [],
            "depends_on": [d["id"] for d in (prov.get("depends_on") or [])] if prov else [],
            "supersedes": [s["id"] for s in (prov.get("supersedes") or [])] if prov else [],
            "contradicts": [c["id"] for c in (prov.get("contradicts") or [])] if prov else [],
            "in_state": m.id in state.get("contradiction_free_ids", []),
        })
    return {
        "canon_total": state["canon_total"],
        "contradiction_free": state["contradiction_free"],
        "conflicts_count": len(state["conflicts"]),
        "superseded_count": len(state["superseded"]),
        "records": canon_records,
    }


# ── helper for simulation A/B harness ─────────────────────────────────────


def measure_ledger_value(session: Session) -> dict:
    """One-shot measurement of how much ledger surface adds over the raw
    canon list. Used by the simulation A/B harness to quantify "what
    would the ledger have caught that today's flat canon doesn't?"
    """
    state = canon_state(session)
    pairs = contradiction_pairs(session)

    # Canon memories that depend on another memory (chain integrity).
    canon_with_deps = (
        session.query(MemoryConnection.source_id)
        .join(Memory, Memory.id == MemoryConnection.source_id)
        .filter(
            MemoryConnection.relationship_type == "depends_on",
            Memory.maturity == MaturityLevel.CANON.value,
        )
        .distinct()
        .count()
    )

    # Memories that have been protected from auto-deprecation by R9
    # canon-dependent guard. We can't easily reconstruct that from a
    # snapshot — approximate by counting the dependents.
    protected = (
        session.query(MemoryConnection.target_id)
        .join(Memory, Memory.id == MemoryConnection.source_id)
        .filter(
            MemoryConnection.relationship_type == "depends_on",
            Memory.maturity == MaturityLevel.CANON.value,
        )
        .distinct()
        .count()
    )

    return {
        "canon_total": state["canon_total"],
        "contradiction_free": state["contradiction_free"],
        "conflicts_detected": len(pairs),
        "supersession_pairs": len(state["superseded"]),
        "canon_with_dependencies": canon_with_deps,
        "memories_protected_by_chain_integrity": protected,
    }


__all__ = [
    "audit_export",
    "canon_state",
    "contradiction_pairs",
    "measure_ledger_value",
    "provenance",
    "timeline",
]
