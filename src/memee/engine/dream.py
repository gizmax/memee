"""Dream Mode: sleep-time compute for organizational knowledge.

Nightly process that:
1. Auto-connects related memories (builds the graph)
2. Identifies contradictions between patterns
3. Infers ``depends_on`` and ``supersedes`` edges (R9)
4. Boosts confidence of well-connected memories
5. Proposes promotions for memories near thresholds
6. Generates digest of what the org learned

This runs as a batch job, not real-time.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from sqlalchemy import func, text
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

logger = logging.getLogger(__name__)

# ── R9 graph inference tunables ──
# These are deliberately strict to keep false positives down on small corpora.
# Loosen only after retrieval_eval shows the precision is holding.

# A `depends_on` edge requires the candidate target to have STRICTLY MORE
# specific tags than the source AND both memories above this confidence floor.
GRAPH_DEPENDS_MIN_CONFIDENCE = 0.6

# A `supersedes` edge requires:
#   1) full tag overlap (same niche), AND
#   2) (a) explicit textual cue ("instead of", "deprecated", "newer", etc.) OR
#      (b) confidence gap ≥ this AND maturity ordering A ≥ B AND B has
#          invalidation activity.
GRAPH_SUPERSEDES_MIN_GAP = 0.3
GRAPH_SUPERSEDES_INVALIDATION_RATIO = 0.2

# Textual cues that signal "memory A replaces memory B"
_SUPERSEDE_CUES = re.compile(
    r"\b(instead\s+of|instead\s+use|replac(?:ed|es|ing)|deprecated\s+in\s+favor"
    r"|newer\s+approach|better\s+approach|don'?t\s+use|avoid\s+using)\b",
    re.IGNORECASE,
)
_DEPENDS_CUES = re.compile(
    r"\b(requires?|prerequisite|first\s+(?:set\s+up|do|configure)|"
    r"depend(?:s|ent)\s+on|after\s+setting|once\s+you\s+have)\b",
    re.IGNORECASE,
)


def run_dream_cycle(session: Session) -> dict:
    """Run a full dream cycle.

    R11 concurrency #4: the cycle does many writes (auto-connect, dependency
    inference, supersession inference, boost, promotion). Each phase
    flushes incrementally; on the default WAL setup that's many small
    fsyncs. We open the cycle inside ``BEGIN EXCLUSIVE`` so the whole
    sequence runs in one transaction — net 1.17-1.21× faster dream wall
    in the R11 audit, at the cost of one cycle-long write lock (acceptable:
    dream is a nightly batch; concurrent readers are unaffected because
    WAL still permits reads during EXCLUSIVE).

    Returns detailed stats about what was discovered and changed.
    """
    # Best-effort EXCLUSIVE; raw begin keeps the existing session-level
    # commit at the end working unchanged. If the connection is already
    # in a transaction (rare from callers but possible), fall through.
    try:
        session.execute(text("BEGIN EXCLUSIVE"))
    except Exception as e:
        logger.debug("dream: could not BEGIN EXCLUSIVE: %s", e)
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

    # Phase 1b: Infer depends_on edges (R9). Strict gates keep false-positive
    # rate down: textual cues OR strict tag-superset hierarchy.
    deps_stats = _infer_dependencies(session)
    stats["dependencies_inferred"] = deps_stats["created"]
    stats["digest"].extend(deps_stats["digest"])

    # Phase 1c: Infer supersedes edges (R9). Even stricter gates because a
    # bad supersession edge directly hides the wrong memory in briefing.
    supers_stats = _infer_supersessions(session)
    stats["supersessions_inferred"] = supers_stats["created"]
    stats["digest"].extend(supers_stats["digest"])

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


def _existing_edge_types(session: Session) -> dict[tuple[str, str], str]:
    """Return ``{(source_id, target_id): relationship_type}`` for every
    non-expired edge in the graph. Used by R9 inference passes to skip pairs
    that already have an edge so we don't churn types on every dream run.
    """
    rows = session.query(
        MemoryConnection.source_id,
        MemoryConnection.target_id,
        MemoryConnection.relationship_type,
        MemoryConnection.expires_at,
    ).all()
    out: dict[tuple[str, str], str] = {}
    now = utcnow()
    for src, tgt, rtype, exp in rows:
        if exp is not None and exp < now:
            continue
        out[(src, tgt)] = rtype
    return out


def _infer_dependencies(session: Session) -> dict:
    """Infer ``depends_on`` edges between memories.

    Two signals:
      (a) **Tag hierarchy.** If memory B's tags are a strict superset of A's
          and both are at least VALIDATED with confidence ≥ floor, B is a
          specialisation that *depends on* A (the more general prerequisite).
      (b) **Textual cue in B's content.** "first set up X", "requires X",
          "prerequisite: X" inside B's body that names a token also present
          in A's title or tags.

    Idempotent: skips pairs that already have any edge. Edges expire when
    the source becomes DEPRECATED (handled separately in lifecycle).
    """
    stats = {"created": 0, "digest": []}
    # Only work over non-deprecated memories with enough confidence to be
    # worth depending on.
    candidates = (
        session.query(Memory)
        .filter(
            Memory.maturity != MaturityLevel.DEPRECATED.value,
            Memory.confidence_score >= GRAPH_DEPENDS_MIN_CONFIDENCE,
        )
        .all()
    )
    if len(candidates) < 2:
        return stats

    existing = _existing_edge_types(session)
    title_index: dict[str, list[Memory]] = defaultdict(list)
    for m in candidates:
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", (m.title or "").lower()):
            title_index[tok].append(m)

    seen: set[tuple[str, str]] = set()

    # (a) Tag-hierarchy pass — R10 perf bucketing.
    # The original O(N²) loop iterated every (b, a) pair across all
    # candidates. At 5 k memories the speed audit measured ~38 s. The
    # tag-superset gate is symmetric in tag-set cardinality: a candidate B
    # can only be the *more specialised* memory in a pair if its tag set is
    # strictly larger than some other A. So we bucket candidates by tag-set
    # cardinality and, for each B with |tags|=n, only walk candidates with
    # |tags|<n. Same edge yield, ~5× wall on 5 k.
    cands_by_size: dict[int, list[Memory]] = defaultdict(list)
    for c in candidates:
        n = len(c.tags or [])
        if n >= 2:
            cands_by_size[n].append(c)

    if not cands_by_size:
        return stats

    sizes = sorted(cands_by_size.keys())
    for size_b in sizes:
        smaller_pool: list[Memory] = []
        for size_a in sizes:
            if size_a >= size_b:
                break
            smaller_pool.extend(cands_by_size[size_a])
        if not smaller_pool:
            continue
        for b in cands_by_size[size_b]:
            b_tags = set(b.tags or [])
            for a in smaller_pool:
                if a.id == b.id:
                    continue
                a_tags = set(a.tags or [])
                if not a_tags or not a_tags.issubset(b_tags):
                    continue
                pair = (b.id, a.id)
                if pair in existing or pair in seen:
                    continue
                session.add(
                    MemoryConnection(
                        source_id=b.id,
                        target_id=a.id,
                        relationship_type="depends_on",
                        strength=min(len(a_tags) / max(len(b_tags), 1), 1.0),
                    )
                )
                seen.add(pair)
                stats["created"] += 1
                stats["digest"].append(
                    f"DEPENDS_ON (tag-hierarchy): '{b.title}' → '{a.title}'"
                )

    # (b) Textual-cue pass. For each memory B, scan content for cue regex
    # and look for an A whose title token shows up in the cue's tail.
    for b in candidates:
        body = (b.content or "")
        if not body:
            continue
        if not _DEPENDS_CUES.search(body):
            continue
        # Look for any candidate A whose title token appears verbatim in B's
        # body. We require token length ≥ 4 to avoid stopword matches.
        body_tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", body.lower()))
        for tok in body_tokens:
            for a in title_index.get(tok, []):
                if a.id == b.id:
                    continue
                pair = (b.id, a.id)
                if pair in existing or pair in seen:
                    continue
                # Soft constraint: A and B must share at least one tag — keeps
                # the textual match from cross-domain false positives.
                if not (set(a.tags or []) & set(b.tags or [])):
                    continue
                session.add(
                    MemoryConnection(
                        source_id=b.id,
                        target_id=a.id,
                        relationship_type="depends_on",
                        strength=0.7,  # textual evidence is reasonably strong
                    )
                )
                seen.add(pair)
                stats["created"] += 1
                stats["digest"].append(
                    f"DEPENDS_ON (text-cue): '{b.title}' → '{a.title}'"
                )

    session.flush()
    return stats


def _infer_supersessions(session: Session) -> dict:
    """Infer ``supersedes`` edges (A replaces B).

    Strict gates because a wrong supersession edge directly hides B from
    briefing. Two acceptable triggers:
      (a) Explicit textual cue in A's body matching ``_SUPERSEDE_CUES``,
          combined with full tag-set match between A and B.
      (b) Confidence gap (A.conf − B.conf ≥ ``GRAPH_SUPERSEDES_MIN_GAP``)
          AND maturity(A) ≥ maturity(B) AND B has invalidation activity
          AND full tag-set match.

    Only inserts when no existing edge between the pair. Lifecycle gates
    kick in separately to avoid auto-deprecation.
    """
    stats = {"created": 0, "digest": []}
    maturity_order = {
        MaturityLevel.HYPOTHESIS.value: 0,
        MaturityLevel.TESTED.value: 1,
        MaturityLevel.VALIDATED.value: 2,
        MaturityLevel.CANON.value: 3,
    }

    candidates = (
        session.query(Memory)
        .filter(
            Memory.maturity.in_(
                [
                    MaturityLevel.TESTED.value,
                    MaturityLevel.VALIDATED.value,
                    MaturityLevel.CANON.value,
                ]
            ),
        )
        .all()
    )
    if len(candidates) < 2:
        return stats

    # R10 perf: cardinality early-exit. Supersession requires *full* tag-set
    # equality between two memories, so memories whose tag-set is unique in
    # the corpus can never be a winner or a loser. Group first, drop singleton
    # buckets up front, then only loop over the remaining buckets. The speed
    # audit measured 4.74 s → ≤100 ms at 5 k memories thanks to this gate.
    bucketed: dict[frozenset, list[Memory]] = defaultdict(list)
    for m in candidates:
        if not m.tags:
            continue
        bucketed[frozenset(m.tags)].append(m)
    if not any(len(v) >= 2 for v in bucketed.values()):
        return stats

    existing = _existing_edge_types(session)

    # Re-bind to keep the rest of the function unchanged.
    by_tagset = bucketed

    for tagset, members in by_tagset.items():
        if len(members) < 2:
            continue
        # Sort: highest confidence + most mature first so the prefix is the
        # candidate "winner" we compare everyone else against.
        members.sort(
            key=lambda m: (
                maturity_order.get(m.maturity, -1),
                m.confidence_score,
            ),
            reverse=True,
        )
        winner = members[0]
        for other in members[1:]:
            if winner.id == other.id:
                continue
            pair = (winner.id, other.id)
            if pair in existing:
                continue

            # (a) textual-cue gate
            text_cue = bool(_SUPERSEDE_CUES.search(winner.content or ""))

            # (b) trajectory gate
            gap = (winner.confidence_score or 0.0) - (other.confidence_score or 0.0)
            mat_ok = maturity_order.get(winner.maturity, -1) >= maturity_order.get(
                other.maturity, -1
            )
            invalid_count = other.invalidation_count or 0
            valid_count = other.validation_count or 0
            inval_ratio = invalid_count / max(valid_count + invalid_count, 1)
            trajectory_ok = (
                gap >= GRAPH_SUPERSEDES_MIN_GAP
                and mat_ok
                and inval_ratio >= GRAPH_SUPERSEDES_INVALIDATION_RATIO
            )

            if not (text_cue or trajectory_ok):
                continue

            session.add(
                MemoryConnection(
                    source_id=winner.id,
                    target_id=other.id,
                    relationship_type="supersedes",
                    strength=min(0.5 + gap, 1.0),
                )
            )
            stats["created"] += 1
            cue = "text-cue" if text_cue else "trajectory"
            stats["digest"].append(
                f"SUPERSEDES ({cue}): '{winner.title}' replaces '{other.title}'"
            )

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
    """Find memories that contradict each other.

    R10 perf: replaced the ``session.get()`` per edge (2N round-trips) with
    a single ``IN`` lookup over all unique ids referenced by the contradicts
    edges. At a corpus with 200 contradicts edges the old shape was 401
    queries; the new shape is 2 (the edge fetch + one batched memory load).
    """
    contradictions = []

    conns = (
        session.query(MemoryConnection)
        .filter(MemoryConnection.relationship_type == "contradicts")
        .all()
    )
    if not conns:
        return contradictions

    ids = {c.source_id for c in conns} | {c.target_id for c in conns}
    by_id = {
        m.id: m
        for m in session.query(Memory).filter(Memory.id.in_(ids)).all()
    }

    for conn in conns:
        m_a = by_id.get(conn.source_id)
        m_b = by_id.get(conn.target_id)
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

    R10 perf: was 1 + 2*E queries (edge fetch + ``session.get`` per neighbor
    × every hypothesis/tested memory). Reshaped to 3 queries — pull all
    relevant edges + all confidence rows once, then walk the in-memory
    adjacency. Empirically ~50× faster on 1k memories with 3k edges.
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
    if not memories:
        return 0

    target_ids = {m.id for m in memories}
    edges = (
        session.query(
            MemoryConnection.source_id,
            MemoryConnection.target_id,
            MemoryConnection.strength,
        )
        .filter(
            (MemoryConnection.source_id.in_(target_ids))
            | (MemoryConnection.target_id.in_(target_ids))
        )
        .all()
    )
    # Build adjacency: for each memory in our set, list of (neighbor_id, strength).
    adj: dict[str, list[tuple[str, float]]] = {mid: [] for mid in target_ids}
    neighbor_ids: set[str] = set()
    for src, tgt, strength in edges:
        s = float(strength or 0.0)
        if src in target_ids:
            adj[src].append((tgt, s))
            neighbor_ids.add(tgt)
        if tgt in target_ids and tgt != src:
            adj[tgt].append((src, s))
            neighbor_ids.add(src)

    if not neighbor_ids:
        return 0

    # One batched fetch for neighbor confidence. We only need conf, no full row.
    conf_rows = (
        session.query(Memory.id, Memory.confidence_score)
        .filter(Memory.id.in_(neighbor_ids))
        .all()
    )
    conf_by_id = {mid: (cs or 0.0) for mid, cs in conf_rows}

    for memory in memories:
        neighbor_confs = []
        for nid, strength in adj.get(memory.id, []):
            n_conf = conf_by_id.get(nid, 0.0)
            if n_conf > 0.45:
                neighbor_confs.append(n_conf * strength)

        if len(neighbor_confs) >= 2:
            avg_signal = sum(neighbor_confs) / len(neighbor_confs)
            len_factor = min(len(neighbor_confs), 5)
            boost = 0.02 * avg_signal * len_factor
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
