"""Dream Mode: sleep-time compute for organizational knowledge.

Nightly process that:
1. Auto-connects related memories (builds the graph)
2. Identifies contradictions between patterns
3. Boosts confidence of well-connected memories
4. Proposes promotions for memories near thresholds
5. Generates digest of what the org learned

This runs as a batch job, not real-time.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from memee.engine.confidence import evaluate_maturity
from memee.engine.lifecycle import run_aging_cycle
from memee.storage.models import (
    MaturityLevel,
    Memory,
    MemoryConnection,
    MemoryType,
    utcnow,
)


def run_dream_cycle(session: Session) -> dict:
    """Run a full dream cycle.

    Returns detailed stats about what was discovered and changed.
    """
    stats = {
        "connections_created": 0,
        "contradictions_found": 0,
        "confidence_boosts": 0,
        "promotions_proposed": 0,
        "promotions_applied": 0,
        "meta_patterns": [],
        "digest": [],
    }

    # Phase 0: Auto-propagate patterns to matching projects
    from memee.engine.propagation import run_propagation_cycle

    prop_stats = run_propagation_cycle(session, confidence_threshold=0.50, max_propagations=500)
    stats["propagated_links"] = prop_stats["total_new_links"]

    # Phase 1: Auto-connect related memories
    connect_stats = _auto_connect(session)
    stats["connections_created"] = connect_stats["created"]

    # Phase 2: Find contradictions
    contradictions = _find_contradictions(session)
    stats["contradictions_found"] = len(contradictions)
    stats["digest"].extend(
        f"CONTRADICTION: '{c['memory_a_title']}' vs '{c['memory_b_title']}'"
        for c in contradictions
    )

    # Phase 3: Boost well-connected memories
    boost_count = _boost_connected_memories(session)
    stats["confidence_boosts"] = boost_count

    # Phase 4: Propose and apply promotions
    promo_stats = _propose_promotions(session)
    stats["promotions_proposed"] = promo_stats["proposed"]
    stats["promotions_applied"] = promo_stats["applied"]

    # Phase 5: Extract meta-patterns
    meta = _extract_meta_patterns(session)
    stats["meta_patterns"] = meta

    # Phase 6: Run aging cycle
    aging_stats = run_aging_cycle(session)

    session.commit()
    stats["aging"] = aging_stats
    return stats


def _auto_connect(session: Session) -> dict:
    """Connect memories that share 2+ tags."""
    stats = {"created": 0}
    tag_index: dict[str, list[Memory]] = defaultdict(list)

    all_memories = (
        session.query(Memory)
        .filter(Memory.maturity != MaturityLevel.DEPRECATED.value)
        .all()
    )

    for m in all_memories:
        for tag in (m.tags or []):
            tag_index[tag].append(m)

    # Find existing connections to avoid duplicates
    existing = set()
    for conn in session.query(MemoryConnection).all():
        existing.add((conn.source_id, conn.target_id))
        existing.add((conn.target_id, conn.source_id))

    seen = set()
    for tag, members in tag_index.items():
        # Limit to prevent quadratic explosion
        for i, m1 in enumerate(members[:30]):
            for m2 in members[i + 1 : 30]:
                pair = tuple(sorted([m1.id, m2.id]))
                if pair in seen or pair in existing:
                    continue

                shared_tags = set(m1.tags or []) & set(m2.tags or [])
                if len(shared_tags) >= 2:
                    # Determine relationship type
                    rel_type = _infer_relationship(m1, m2)
                    strength = min(len(shared_tags) / 5, 1.0)

                    conn = MemoryConnection(
                        source_id=pair[0],
                        target_id=pair[1],
                        relationship_type=rel_type,
                        strength=strength,
                    )
                    session.add(conn)
                    seen.add(pair)
                    stats["created"] += 1

    session.flush()
    return stats


def _infer_relationship(m1: Memory, m2: Memory) -> str:
    """Infer the relationship type between two memories."""
    # Pattern + Anti-Pattern with overlapping tags = contradicts
    types = {m1.type, m2.type}
    if MemoryType.PATTERN.value in types and MemoryType.ANTI_PATTERN.value in types:
        return "contradicts"

    # Same type = related_to or supports
    if m1.type == m2.type:
        return "supports" if m1.type == MemoryType.PATTERN.value else "related_to"

    return "related_to"


def _find_contradictions(session: Session) -> list[dict]:
    """Find memories that contradict each other."""
    contradictions = []

    # Get all "contradicts" connections
    conns = (
        session.query(MemoryConnection)
        .filter(MemoryConnection.relationship_type == "contradicts")
        .all()
    )

    for conn in conns:
        m_a = session.get(Memory, conn.source_id)
        m_b = session.get(Memory, conn.target_id)
        if m_a and m_b:
            contradictions.append({
                "memory_a_id": m_a.id,
                "memory_a_title": m_a.title,
                "memory_a_confidence": m_a.confidence_score,
                "memory_b_id": m_b.id,
                "memory_b_title": m_b.title,
                "memory_b_confidence": m_b.confidence_score,
            })

    return contradictions


def _boost_connected_memories(session: Session) -> int:
    """Boost confidence of memories with high-confidence neighbors.

    If a memory has 2+ connected neighbors with confidence > 0.6,
    apply a small boost proportional to avg neighbor confidence.
    """
    boosted = 0
    memories = (
        session.query(Memory)
        .filter(
            Memory.maturity.in_([
                MaturityLevel.HYPOTHESIS.value,
                MaturityLevel.TESTED.value,
            ]),
        )
        .all()
    )

    for memory in memories:
        conns = (
            session.query(MemoryConnection)
            .filter(
                (MemoryConnection.source_id == memory.id)
                | (MemoryConnection.target_id == memory.id)
            )
            .all()
        )

        neighbor_confs = []
        for c in conns:
            neighbor_id = c.target_id if c.source_id == memory.id else c.source_id
            neighbor = session.get(Memory, neighbor_id)
            if neighbor and neighbor.confidence_score > 0.45:
                neighbor_confs.append(neighbor.confidence_score * c.strength)

        if len(neighbor_confs) >= 2:
            avg_signal = sum(neighbor_confs) / len(neighbor_confs)
            boost = 0.02 * avg_signal * len(neighbor_confs)
            memory.confidence_score = min(0.99, memory.confidence_score + boost)
            boosted += 1

    return boosted


def _propose_promotions(session: Session) -> dict:
    """Find memories near promotion thresholds and push them over."""
    stats = {"proposed": 0, "applied": 0}

    memories = (
        session.query(Memory)
        .filter(Memory.maturity != MaturityLevel.DEPRECATED.value)
        .all()
    )

    for memory in memories:
        old_maturity = memory.maturity
        new_maturity = evaluate_maturity(memory)

        if new_maturity != old_maturity:
            stats["proposed"] += 1

            # Apply promotion if it's an upgrade (not deprecation from this path)
            maturity_order = [
                MaturityLevel.HYPOTHESIS.value,
                MaturityLevel.TESTED.value,
                MaturityLevel.VALIDATED.value,
                MaturityLevel.CANON.value,
            ]
            if (
                new_maturity in maturity_order
                and old_maturity in maturity_order
                and maturity_order.index(new_maturity) > maturity_order.index(old_maturity)
            ):
                memory.maturity = new_maturity
                stats["applied"] += 1
            elif new_maturity == MaturityLevel.DEPRECATED.value:
                memory.maturity = new_maturity
                memory.deprecated_at = utcnow()
                stats["applied"] += 1

    return stats


def _extract_meta_patterns(session: Session) -> list[str]:
    """Extract meta-patterns from organizational knowledge.

    Looks at tag frequency, anti-pattern clustering, etc.
    """
    meta = []

    # Most common anti-pattern tags
    anti_patterns = (
        session.query(Memory)
        .filter(Memory.type == MemoryType.ANTI_PATTERN.value)
        .all()
    )
    tag_counts: dict[str, int] = defaultdict(int)
    for ap in anti_patterns:
        for tag in (ap.tags or []):
            tag_counts[tag] += 1

    if tag_counts:
        top_tag = max(tag_counts, key=tag_counts.get)
        meta.append(
            f"Most common anti-pattern domain: '{top_tag}' "
            f"({tag_counts[top_tag]} anti-patterns)"
        )

    # Confidence distribution insight
    avg_conf = session.query(func.avg(Memory.confidence_score)).scalar() or 0
    if avg_conf < 0.5:
        meta.append(
            "Org confidence is below 0.5 — more validation needed across projects"
        )
    elif avg_conf > 0.7:
        meta.append(
            "Org confidence above 0.7 — knowledge base is maturing well"
        )

    # Stale knowledge ratio
    total = session.query(func.count(Memory.id)).scalar() or 1
    stale = (
        session.query(func.count(Memory.id))
        .filter(
            Memory.maturity == MaturityLevel.HYPOTHESIS.value,
            Memory.validation_count == 0,
        )
        .scalar()
    )
    stale_pct = stale / total * 100
    if stale_pct > 40:
        meta.append(
            f"High stale ratio: {stale_pct:.0f}% of memories never validated. "
            f"Consider focused validation sprints."
        )

    return meta
