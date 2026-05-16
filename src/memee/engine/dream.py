"""Dream Mode: sleep-time compute for organizational knowledge.

Nightly process that:
1. Auto-connects related memories (builds the graph)
2. Identifies contradictions between patterns
3. Infers ``depends_on`` and ``supersedes`` edges (R9)
4. Folds semantic duplicates into their canonical entry (v2.2.4)
5. Boosts confidence of well-connected memories
6. Proposes promotions for memories near thresholds
7. Generates digest of what the org learned

This runs as a batch job, not real-time.
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from typing import Any

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

# ── v2.2.4 semantic dedup tunables ──
#
# Quality gate's existing dedup pass (`engine/quality_gate.py`) catches
# lexical near-duplicates via SequenceMatcher on normalised titles. It
# misses semantic paraphrases — "User likes Vim", "user prefers Vim",
# "Vim is the user's editor of choice" share no token n-grams but mean
# the same thing. Letta issue #3116 and Mem0 audit #4573 both flag this
# exact failure mode (808 entries asserting the same preference).
#
# Memee already stores 384-dim embeddings for every memory; comparing
# them is the obvious second pass. We run it in dream rather than at
# write time so the write path stays cheap (no on-the-fly encode) and
# the merge has access to confidence/maturity stats that only stabilise
# overnight.
#
# Threshold: 0.92 cosine. Empirically the boundary where embeddings
# disagree on "same fact, different phrasing" vs "related but distinct".
# Below 0.92 the false-positive rate climbs sharply on tech docs (where
# "Always pass timeout to requests" and "Always set timeout on httpx"
# legitimately differ). Operators can tune via env var.
SEMANTIC_DEDUP_THRESHOLD_DEFAULT = 0.92
SEMANTIC_DEDUP_TAG_OVERLAP_MIN = 0.5
# Hard cap per cycle keeps the wall-clock bounded even if a corpus has
# many near-duplicates; the rest get folded on the next dream pass.
SEMANTIC_DEDUP_MAX_MERGES_PER_CYCLE = 100

# ── v2.4.8 contradiction semantic gate ──
#
# Pre-v2.4.8 the contradiction classifier flagged any (pattern,
# anti-pattern) pair that shared two tags. On a real 247-memory live
# install this produced 500 false positives — pairs like
# "Use connection pooling for SQLAlchemy" ⊥ "Never run CPU-bound code
# in the asyncio event loop" share `async, sqlalchemy` tags without
# semantically contradicting each other.
#
# Fix: gate the heuristic through the existing cross-encoder reranker.
# We score the (m1.title + body[:200]) text against m2's same shape;
# only pairs above ``MEMEE_CONTRADICTION_THRESHOLD`` (default 0.55)
# keep the ``contradicts`` label. Pairs below fall back to
# ``related_to`` — they're co-tagged advice, not contradictions.
#
# 0.55 is a starting estimate against the ms-marco-MiniLM-L-6-v2
# distribution. Operators can tune via env var; ``memee dream
# --rebuild-contradictions`` lets them re-evaluate the whole edge set
# after a tweak without waiting for natural churn.
CONTRADICTION_THRESHOLD_DEFAULT = 0.55


def _contradiction_threshold() -> float:
    """Cross-encoder threshold for contradiction classification, env-tunable.

    ``MEMEE_CONTRADICTION_THRESHOLD`` accepts any float. Out-of-range or
    unparseable values fall back to the default. We don't clamp to a
    [0, 1] window because the cross-encoder score is unbounded — a user
    setting it to 2.0 to require very strong signal is legitimate.
    """
    raw = os.environ.get("MEMEE_CONTRADICTION_THRESHOLD")
    if raw is None:
        return CONTRADICTION_THRESHOLD_DEFAULT
    try:
        return float(raw)
    except (TypeError, ValueError):
        return CONTRADICTION_THRESHOLD_DEFAULT


class _ContradictionScorer:
    """Thin per-cycle wrapper around the cross-encoder reranker.

    Why this exists separately from ``CrossEncoderReranker``: the
    reranker's API is shaped around (query, candidate-list) for search
    rerank. Dream needs pairwise scoring with caching across calls in
    one cycle, so this class:

      * Loads the cross-encoder once (delegated to the reranker's
        module-cached loader — same model, same weights, no double load).
      * Exposes ``score_pair(m1, m2)`` returning a float or ``None``.
      * Caches by ``(m1.id, m2.id)`` (order-insensitive) so the same
        pair isn't re-scored within a cycle.
      * Fails safe: if the cross-encoder can't load (no HF cache, no
        ``sentence-transformers``, network-offline cold install),
        ``score_pair`` returns ``None`` and ``_infer_relationship``
        treats the absence as "no claim → ``related_to``".

    Cost: ~5-50 ms per pair on CPU after warm-up. Dream is a nightly
    batch — at 500 candidate pairs this is well under a minute total,
    and the cache means subsequent passes inside the same cycle are
    free.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], float | None] = {}
        self._model: Any | None = None
        self._tried_load = False

    def _ensure_model(self) -> Any | None:
        if self._tried_load:
            return self._model
        self._tried_load = True
        try:
            from memee.engine.reranker import (
                _model_name_from_env,
                _try_load,
            )
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("contradiction scorer: reranker import failed: %s", e)
            return None
        model_name = _model_name_from_env()
        if not model_name:
            return None
        self._model = _try_load(model_name)
        return self._model

    @staticmethod
    def _text_for(memory: Memory) -> str:
        title = (memory.title or "").strip()
        content = (memory.content or "")[:200].strip()
        if title and content:
            return f"{title} — {content}"
        return title or content

    def score_pair(self, m1: Memory, m2: Memory) -> float | None:
        """Return the cross-encoder score for the pair, or ``None`` if the
        model isn't available. Order-insensitive cache.
        """
        key = tuple(sorted([m1.id, m2.id]))
        if key in self._cache:
            return self._cache[key]
        model = self._ensure_model()
        if model is None:
            self._cache[key] = None
            return None
        try:
            score = model.predict([(self._text_for(m1), self._text_for(m2))])
        except Exception as e:
            logger.warning("contradiction scorer: predict failed: %s", e)
            self._cache[key] = None
            return None
        # ``predict`` returns numpy scalar / array; coerce to plain float.
        try:
            value = float(score[0])
        except (TypeError, IndexError, ValueError):
            try:
                value = float(score)
            except (TypeError, ValueError):
                self._cache[key] = None
                return None
        self._cache[key] = value
        return value


def _semantic_dedup_threshold() -> float:
    """Cosine threshold for semantic-dup detection, env-tunable.

    ``MEMEE_SEMANTIC_DEDUP_THRESHOLD`` accepts a float in (0.0, 1.0].
    Out-of-range or unparseable values fall back to the default.
    """
    raw = os.environ.get("MEMEE_SEMANTIC_DEDUP_THRESHOLD")
    if raw is None:
        return SEMANTIC_DEDUP_THRESHOLD_DEFAULT
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return SEMANTIC_DEDUP_THRESHOLD_DEFAULT
    if not (0.0 < val <= 1.0):
        return SEMANTIC_DEDUP_THRESHOLD_DEFAULT
    return val


def run_dream_cycle(
    session: Session,
    *,
    rebuild_contradictions: bool | None = None,
    scorer: _ContradictionScorer | None = None,
) -> dict:
    """Run a full dream cycle.

    R11 concurrency #4: the cycle does many writes (auto-connect, dependency
    inference, supersession inference, boost, promotion). Each phase
    flushes incrementally; on the default WAL setup that's many small
    fsyncs. We open the cycle inside ``BEGIN EXCLUSIVE`` so the whole
    sequence runs in one transaction — net 1.17-1.21× faster dream wall
    in the R11 audit, at the cost of one cycle-long write lock (acceptable:
    dream is a nightly batch; concurrent readers are unaffected because
    WAL still permits reads during EXCLUSIVE).

    ``rebuild_contradictions`` (v2.4.8): when True (or when
    ``MEMEE_REBUILD_CONTRADICTIONS=1``), purge every existing
    ``contradicts`` edge before running auto-connect. Existing users
    whose graphs were polluted by the v2.4.7-and-earlier naive heuristic
    can pass this once to re-evaluate every pair under the new semantic
    gate. ``None`` falls through to the env var.

    Returns detailed stats about what was discovered and changed.
    """
    if rebuild_contradictions is None:
        env_val = os.environ.get("MEMEE_REBUILD_CONTRADICTIONS", "").strip().lower()
        rebuild_contradictions = env_val in {"1", "true", "yes", "on"}

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
        "contradictions_purged": 0,
        "confidence_boosts": 0,
        "promotions_proposed": 0,
        "promotions_applied": 0,
        "meta_patterns": [],
        "digest": [],
    }

    if rebuild_contradictions:
        # v2.4.8: wipe stale contradicts edges so the new semantic gate
        # gets a clean canvas. The exact-match WHERE keeps us from
        # touching depends_on / supersedes / supports / semantic_dup_of.
        purge_count = (
            session.query(MemoryConnection)
            .filter(MemoryConnection.relationship_type == "contradicts")
            .delete(synchronize_session=False)
        )
        stats["contradictions_purged"] = int(purge_count)
        session.flush()
        logger.info(
            "dream: --rebuild-contradictions purged %d stale contradicts edges",
            purge_count,
        )

    # v2.4.8 contradiction gate. Constructed once per cycle so the
    # cross-encoder is loaded at most one time, with per-pair caching.
    if scorer is None:
        scorer = _ContradictionScorer()

    # Phase 0: Auto-propagate patterns to matching projects
    from memee.engine.propagation import run_propagation_cycle

    prop_stats = run_propagation_cycle(session, confidence_threshold=0.50, max_propagations=500)
    stats["propagated_links"] = prop_stats["total_new_links"]

    # Phase 1: Auto-connect related memories
    connect_stats = _auto_connect(session, scorer=scorer)
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

    # Phase 1d (v2.2.4): Fold semantic duplicates that the write-time
    # lexical dedup missed. Runs AFTER supersessions so a "B replaces A"
    # relationship is on the graph before we'd otherwise quietly merge
    # them — the existing-edge guard in _semantic_dedup_pass leaves those
    # pairs alone. Runs BEFORE contradictions so a freshly-folded loser
    # doesn't get reported as contradicting its winner.
    semdup_stats = _semantic_dedup_pass(session)
    stats["semantic_duplicates_merged"] = semdup_stats["merged"]
    stats["digest"].extend(semdup_stats["digest"])

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


def _auto_connect(
    session: Session,
    *,
    scorer: _ContradictionScorer | None = None,
) -> dict:
    """Connect memories that share 2+ tags.

    ``scorer`` is the per-cycle contradiction gate. Passed down
    explicitly rather than module-globalled so tests can inject a
    deterministic fake without monkey-patching the reranker module.
    """
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
                    # Determine relationship type (semantic gate on
                    # contradicts; see _infer_relationship for the
                    # 2.4.8 false-positive rationale).
                    rel_type = _infer_relationship(m1, m2, scorer=scorer)
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


def _infer_relationship(
    m1: Memory,
    m2: Memory,
    *,
    scorer: _ContradictionScorer | None = None,
) -> str:
    """Infer the relationship type between two memories.

    v2.4.8: pattern + anti-pattern with overlapping tags no longer
    auto-classifies as ``contradicts``. The naive heuristic produced
    ~500 false positives on a 247-memory corpus (real user report) by
    flagging orthogonal advice that happened to share tags. We now
    gate the classification through a cross-encoder semantic score;
    pairs below ``MEMEE_CONTRADICTION_THRESHOLD`` (default 0.55) get
    the safer ``related_to`` label instead. When the scorer isn't
    available (no HF cache, sentence-transformers missing, offline
    install), we also fall back to ``related_to`` — no claim is
    better than a wrong one.
    """
    types = {m1.type, m2.type}
    if MemoryType.PATTERN.value in types and MemoryType.ANTI_PATTERN.value in types:
        if scorer is None:
            # Fail safe: without a scorer we can't tell contradiction
            # from co-tagged orthogonal advice. Returning ``related_to``
            # keeps the graph honest at the cost of losing genuine
            # contradictions on installs without the cross-encoder.
            return "related_to"
        sim = scorer.score_pair(m1, m2)
        if sim is None:
            return "related_to"
        threshold = _contradiction_threshold()
        return "contradicts" if sim >= threshold else "related_to"

    # Same type = related_to or supports
    if m1.type == m2.type:
        return "supports" if m1.type == MemoryType.PATTERN.value else "related_to"

    return "related_to"


def _semantic_dedup_pass(session: Session) -> dict:
    """Fold semantic-duplicate memories into their canonical entry (v2.2.4).

    Companion to the lexical dedup that the quality gate already runs at
    write time. The quality gate uses SequenceMatcher on normalised titles
    and catches "API keys in source" vs "Api Keys in Source"; semantic
    dedup uses the existing 384-dim embeddings and catches "user prefers
    Vim" vs "Vim is the editor the user likes".

    Pipeline:
      1. Pull the cached embedding matrix from search._embedded_corpus_matrix.
      2. Compute all-pairs cosine via one matrix multiply.
      3. For each upper-triangle pair with cosine ≥ threshold:
           - skip cross-type pairs
           - skip pairs where either side is DEPRECATED
           - skip pairs that already have ANY edge (depends_on,
             supersedes, contradicts, supports, related_to, …)
           - require tag overlap ≥ SEMANTIC_DEDUP_TAG_OVERLAP_MIN
           - require winner.merge_count < LARGE_CLUSTER_MERGE_LIMIT (5)
      4. Pick a winner: higher confidence_score, tie-break older
         created_at. Fold loser into winner via the existing
         ``merge_duplicate`` helper (re-uses the evidence-chain audit
         entry and the MemoryTag re-sync logic).
      5. Mark loser DEPRECATED and create a ``semantic_dup_of`` edge
         pointing loser → winner so the relationship is auditable.

    Cap: ``SEMANTIC_DEDUP_MAX_MERGES_PER_CYCLE`` (100). Anything beyond
    that gets folded on subsequent nightly runs. Keeps wall-clock bounded
    on corpora that just imported a giant pack with many overlaps.

    Returns ``{"merged": N, "skipped_no_embedding": N, "scanned_pairs": N}``.
    """
    stats = {
        "merged": 0,
        "skipped_no_embedding": 0,
        "scanned_pairs": 0,
        "digest": [],
    }

    try:
        import numpy as np
    except ImportError:
        logger.debug("semantic_dedup: numpy missing, skipping pass")
        return stats

    from memee.engine.search import _embedded_corpus_matrix

    corpus = _embedded_corpus_matrix(session)
    if corpus is None:
        return stats

    ids = corpus["ids"]
    matrix = corpus["matrix"]  # shape (N, dim)
    row_norms = corpus["row_norms"]
    type_array = corpus["type_array"]
    maturity_array = corpus["maturity_array"]
    n = len(ids)
    if n < 2:
        return stats

    # Cosine similarity = (M @ Mᵀ) / (‖row_i‖ · ‖row_j‖). One matmul; the
    # whole thing fits easily in float32 up to ~10k memories (10k×10k×4B ≈ 400 MB
    # before we mask) — well above any realistic single-org corpus.
    sim = (matrix @ matrix.T) / (row_norms[:, None] * row_norms[None, :])

    threshold = _semantic_dedup_threshold()

    # Upper triangle only (i<j), threshold mask.
    iu, ju = np.where(np.triu(sim >= threshold, k=1))
    stats["scanned_pairs"] = int(len(iu))
    if stats["scanned_pairs"] == 0:
        return stats

    # Pre-filter using cached type / maturity arrays — same-type AND both
    # non-deprecated. Avoids loading Memory rows for pairs we'll throw out.
    deprecated_val = MaturityLevel.DEPRECATED.value
    same_type = type_array[iu] == type_array[ju]
    both_live = (
        (maturity_array[iu] != deprecated_val)
        & (maturity_array[ju] != deprecated_val)
    )
    keep = same_type & both_live
    iu = iu[keep]
    ju = ju[keep]
    if len(iu) == 0:
        return stats

    # Walk candidate pairs in descending similarity so the most-confident
    # merges happen first and downstream pairs get a chance to skip cluster-
    # capped winners cleanly.
    sims_for_pairs = sim[iu, ju]
    order = np.argsort(-sims_for_pairs)
    iu = iu[order]
    ju = ju[order]
    sims_for_pairs = sims_for_pairs[order]

    existing_edges = _existing_edge_types(session)

    # Hydrate Memory rows we'll actually need. Build the id set from the
    # filtered pair lists so we don't fetch the whole corpus.
    needed_ids = {ids[i] for i in iu} | {ids[j] for j in ju}
    rows = (
        session.query(Memory)
        .filter(Memory.id.in_(needed_ids))
        .all()
    )
    by_id: dict[str, Memory] = {m.id: m for m in rows}

    # Track ids we already folded — skip them as either side of further pairs.
    folded: set[str] = set()

    from memee.engine.quality_gate import (
        LARGE_CLUSTER_MERGE_LIMIT,
        merge_duplicate,
    )

    for idx in range(len(iu)):
        if stats["merged"] >= SEMANTIC_DEDUP_MAX_MERGES_PER_CYCLE:
            break

        a_id = ids[int(iu[idx])]
        b_id = ids[int(ju[idx])]
        if a_id in folded or b_id in folded:
            continue
        a = by_id.get(a_id)
        b = by_id.get(b_id)
        if a is None or b is None:
            continue

        # Skip pairs that already have ANY edge in either direction —
        # depends_on, supersedes, contradicts, supports, related_to. The
        # graph already encodes a relationship and we don't want to
        # quietly collapse something the contradictions or supersession
        # passes flagged for human review.
        if (a.id, b.id) in existing_edges or (b.id, a.id) in existing_edges:
            continue

        # Tag overlap gate. Two pieces of canon about the same topic
        # should share most of their tags; if they don't, the embedding
        # similarity is probably picking up shared phrasing rather than
        # shared meaning (e.g. two unrelated patterns that both use the
        # word "always").
        a_tags = set(a.tags or [])
        b_tags = set(b.tags or [])
        if not a_tags or not b_tags:
            continue
        overlap = len(a_tags & b_tags) / max(len(a_tags | b_tags), 1)
        if overlap < SEMANTIC_DEDUP_TAG_OVERLAP_MIN:
            continue

        # Winner: higher confidence; tie-break = older (more validated).
        if (a.confidence_score or 0.0) != (b.confidence_score or 0.0):
            winner, loser = (
                (a, b) if (a.confidence_score or 0.0) > (b.confidence_score or 0.0)
                else (b, a)
            )
        else:
            winner, loser = (a, b) if (a.created_at or utcnow()) <= (b.created_at or utcnow()) else (b, a)

        if int(winner.merge_count or 0) >= LARGE_CLUSTER_MERGE_LIMIT:
            # The cluster-size gate exists for the same reason as in
            # quality_gate: runaway merging hides genuinely-distinct
            # memories under one bloated entry. Skip and let an operator
            # split the cluster if needed.
            continue

        similarity = float(sims_for_pairs[idx])
        # v2.3.3 atomicity fix: pass commit=False so dream's
        # BEGIN EXCLUSIVE stays atomic. The default merge_duplicate
        # commit() would land partial state mid-cycle.
        merge_duplicate(
            session,
            winner,
            loser.content or "",
            new_tags=list(b_tags if loser is b else a_tags),
            new_title=loser.title,
            similarity=similarity,
            commit=False,
        )

        # Deprecate the loser and link it to the winner with a typed edge
        # so an operator running `memee why` later can trace the merge.
        loser.maturity = MaturityLevel.DEPRECATED.value
        session.add(
            MemoryConnection(
                source_id=loser.id,
                target_id=winner.id,
                relationship_type="semantic_dup_of",
                strength=similarity,
            )
        )
        folded.add(loser.id)
        stats["merged"] += 1
        stats["digest"].append(
            f"SEMANTIC_DUP: '{loser.title}' folded into '{winner.title}' "
            f"(cos={similarity:.3f})"
        )

    session.flush()
    return stats


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
