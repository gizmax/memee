"""Quality Gate: validate, deduplicate, classify before storing memories.

Pipeline:
  1. Basic validation (rules, 0 cost)
  2. Deduplication (similarity search, 0 cost)
  3. Source classification (rules, 0 cost)
  4. Scope-dependent: LLM quality check for team/org (optional, ~$0.001)

Scope gates:
  Personal: basic + dedup
  Team:     basic + dedup + source classification
  Org:      basic + dedup + source + quality flag
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from memee.storage.models import Memory, MemoryType

logger = logging.getLogger(__name__)

# Source confidence multipliers
SOURCE_MULTIPLIERS = {
    "human": 1.2,       # Human explicitly recorded — higher trust
    "llm": 0.8,         # LLM auto-generated — lower trust
    "import": 0.6,      # Imported from CLAUDE.md etc — lowest trust
    "unknown": 1.0,     # Default
}

# Scope-aware dedup thresholds.
# Personal scope: solo user benefits from aggressive dedup so the brief stays clean.
# Team/org scope: similar titles across many projects usually encode different rules;
# collapsing them is destructive, so require a much higher fuzzy score before merging.
DEDUP_THRESHOLDS = {
    "personal": 0.88,
    "team":     0.92,
    "org":      0.95,
}

# Once a single memory has absorbed this many near-duplicates, stop auto-merging
# and route further candidates to manual review — runaway clustering at this scale
# almost always means the memory has become a grab-bag, not a canonical pattern.
LARGE_CLUSTER_MERGE_LIMIT = 5


@dataclass
class GateResult:
    """Result of quality gate evaluation."""
    accepted: bool = True
    merged: bool = False
    merged_id: str | None = None
    flagged: bool = False
    reason: str | None = None
    issues: list[str] = field(default_factory=list)
    source_type: str = "unknown"
    initial_confidence: float = 0.5
    quality_score: float | None = None
    dedup_similarity: float = 0.0


def run_quality_gate(
    session: Session,
    title: str,
    content: str,
    tags: list[str] | None = None,
    memory_type: str = "pattern",
    scope: str = "personal",
    source: str = "unknown",
) -> GateResult:
    """Run the full quality gate pipeline.

    Returns GateResult with accept/reject/merge decision.
    """
    result = GateResult(source_type=source)

    # 1. Basic validation
    issues = _validate_basic(title, content, tags, memory_type)
    if issues:
        result.accepted = False
        result.issues = issues
        logger.info(f"Quality gate rejected: {issues}")
        return result

    # 2. Deduplication (type-aware + tag-clustered, scope-aware threshold)
    threshold = DEDUP_THRESHOLDS.get(scope, DEDUP_THRESHOLDS["personal"])
    duplicate, similarity = _find_duplicate(
        session, title, content,
        threshold=threshold, memory_type=memory_type, tags=tags,
    )
    if duplicate:
        # Cluster-size caution: a memory that has already swallowed ≥5 candidates
        # probably represents an over-generic canonicalization. Don't auto-merge —
        # flag for manual review so an operator can split or promote it.
        existing_merges = int(duplicate.merge_count or 0)
        if existing_merges >= LARGE_CLUSTER_MERGE_LIMIT:
            result.merged = False
            result.merged_id = duplicate.id
            result.dedup_similarity = similarity
            result.flagged = True
            result.accepted = False
            result.reason = "large_cluster_manual_review"
            result.issues.append(
                f"Candidate matches memory {duplicate.id[:8]} "
                f"which already has {existing_merges} merged predecessors "
                f"(similarity {similarity:.2f}). Flagged for manual review."
            )
            logger.info(
                "Quality gate flagged for manual review: '%s' → '%s' "
                "(cluster size %d, similarity %.2f)",
                title, duplicate.title, existing_merges, similarity,
            )
            return result

        result.merged = True
        result.merged_id = duplicate.id
        result.dedup_similarity = similarity
        result.accepted = False
        logger.info(f"Quality gate merged: '{title}' → existing '{duplicate.title}' "
                     f"(similarity: {similarity:.2f})")
        return result

    # 3. Source classification
    multiplier = SOURCE_MULTIPLIERS.get(source, 1.0)
    result.initial_confidence = 0.5 * multiplier
    result.source_type = source

    # 4. Quality scoring
    result.quality_score = _assess_quality(title, content, tags)
    if scope in ("team", "org") and result.quality_score is not None and result.quality_score < 2.5:
        result.flagged = True
        result.issues.append(
            f"Low quality score ({result.quality_score:.1f}/5). "
            f"Consider adding WHY/WHEN context before sharing to {scope}."
        )
    elif scope == "personal" and result.quality_score is not None and result.quality_score < 1.5:
        result.flagged = True
        result.issues.append(
            f"Very low quality ({result.quality_score:.1f}/5). Consider adding detail."
        )

    return result


def _validate_basic(
    title: str,
    content: str,
    tags: list[str] | None,
    memory_type: str,
) -> list[str]:
    """Basic validation rules. Zero cost."""
    issues = []

    if not title or len(title.strip()) < 10:
        issues.append("Title must be at least 10 characters")

    if len(title) > 500:
        issues.append("Title must be under 500 characters")

    if not content or len(content.strip()) < 15:
        issues.append("Content must be at least 15 characters")

    # Content must differ from title (actual information, not copy-paste)
    if content and title and content.strip().lower() == title.strip().lower():
        issues.append("Content must add information beyond the title")

    valid_types = [t.value for t in MemoryType]
    if memory_type not in valid_types:
        issues.append(f"Invalid type '{memory_type}'. Must be one of: {valid_types}")

    # Reject non-actionable content (meeting notes, TODOs, vague)
    garbage_patterns = ["todo", "fix later", "meeting notes", "standup", "tbd",
                        "need to check", "will do", "reminder", "don't forget"]
    title_lower = (title or "").lower()
    if any(p in title_lower for p in garbage_patterns):
        issues.append("Memory must be actionable knowledge, not TODOs or meeting notes")

    # Tags required for team/org discoverability
    if not tags or len(tags) < 1:
        issues.append("At least 1 tag required for discoverability")

    return issues


import re as _re


def _normalize_title(title: str) -> str:
    """Normalize title: lowercase, strip common words, remove punctuation + weekly/version suffixes.

    "W12: Always use timeout on HTTP requests (v23)" → "use timeout http requests"
    """
    t = (title or "").lower()
    # Strip weekly/version markers like "W12:", "(v23)", "(variant 4)"
    t = _re.sub(r"w\d+:\s*|\(v\d+\)|\(variant\s+\d+\)|\(eval-\d+\)|\(cal-\d+\)|\(w\d+-\d+\)", "", t)
    # Remove punctuation except spaces
    t = _re.sub(r"[^\w\s]", " ", t)
    # Collapse whitespace
    t = _re.sub(r"\s+", " ", t).strip()
    # Strip ultra-common words that add no signal
    stopwords = {"the", "a", "an", "for", "of", "in", "on", "to", "and", "or",
                 "always", "never", "use", "using", "with"}
    words = [w for w in t.split() if w not in stopwords and len(w) > 2]
    return " ".join(sorted(set(words)))  # Sorted set = order-independent


def _fingerprint(memory_type: str, title: str, tags: list[str] | None) -> str:
    """Canonical fingerprint: type + normalized title + dominant tags.

    Different PROJECTS with same pattern should produce SAME fingerprint
    (so they strengthen, not duplicate). Different PATTERNS should differ.
    """
    norm = _normalize_title(title)
    tag_str = ",".join(sorted([t.lower() for t in (tags or [])]))
    return f"{memory_type}::{norm}::{tag_str}"


def _find_duplicate(
    session: Session,
    title: str,
    content: str,
    threshold: float = 0.88,
    memory_type: str = "pattern",
    tags: list[str] | None = None,
) -> tuple[Memory | None, float]:
    """Find duplicate by canonical fingerprint + fuzzy title match.

    A duplicate is ONLY when type + normalized title + tags all match.
    Different projects with same pattern → same fingerprint → merge.
    Similar-sounding but different patterns → different fingerprints → separate.
    """
    fingerprint = _fingerprint(memory_type, title, tags)
    norm_title = _normalize_title(title)

    # Only compare within same type (pattern vs pattern, not pattern vs anti-pattern)
    recent = (
        session.query(Memory)
        .filter(Memory.type == memory_type)
        .order_by(Memory.created_at.desc())
        .limit(500)
        .all()
    )

    best_match = None
    best_score = 0.0

    for memory in recent:
        # Fingerprint exact match = definite dup
        mem_fp = _fingerprint(memory.type, memory.title or "", memory.tags)
        if mem_fp == fingerprint and norm_title:
            return memory, 1.0

        # Fuzzy: same tags AND high title similarity
        mem_tags = set(t.lower() for t in (memory.tags or []))
        query_tags = set(t.lower() for t in (tags or []))
        if not query_tags or not mem_tags:
            continue

        tag_overlap = len(mem_tags & query_tags) / len(mem_tags | query_tags)
        if tag_overlap < 0.5:
            continue  # Different tag clusters → different patterns, even if titles similar

        mem_norm = _normalize_title(memory.title or "")
        if not mem_norm or not norm_title:
            continue

        score = SequenceMatcher(None, norm_title, mem_norm).ratio()
        combined = score * 0.7 + tag_overlap * 0.3
        if combined > best_score:
            best_score = combined
            best_match = memory

    if best_score >= threshold and best_match:
        return best_match, best_score

    return None, 0.0


def _assess_quality(
    title: str,
    content: str,
    tags: list[str] | None,
) -> float:
    """Assess memory quality without LLM (heuristic scoring).

    Returns score 1-5:
      5: Excellent — specific, actionable, well-tagged
      4: Good — clear, has content
      3: Acceptable — basic but usable
      2: Poor — vague, too short, no tags
      1: Bad — meaningless
    """
    score = 3.0  # Start at acceptable

    # Title quality
    title_words = len(title.split())
    if title_words >= 6:
        score += 0.5  # Descriptive title
    elif title_words <= 2:
        score -= 1.0  # Too vague

    # Content quality
    content_len = len(content)
    if content_len > 100:
        score += 0.5  # Detailed
    elif content_len < 30:
        score -= 0.5  # Too brief

    # Content != title (actually adds information)
    if content.strip() != title.strip() and content_len > len(title) * 1.5:
        score += 0.5

    # Tags quality
    if tags and len(tags) >= 2:
        score += 0.5  # Well-tagged
    elif not tags:
        score -= 0.5  # No tags = hard to find

    # Actionability keywords
    actionable_words = ["always", "never", "use", "avoid", "implement", "add", "set", "configure"]
    if any(word in title.lower() for word in actionable_words):
        score += 0.5  # Actionable

    # Context quality: does it explain WHY and WHEN?
    combined = (title + " " + content).lower()
    has_why = any(w in combined for w in
                  ["because", "since", "due to", "reason", "prevents", "avoids",
                   "otherwise", "without this", "this prevents", "to prevent"])
    has_when = any(w in combined for w in
                   ["when", "if ", "during", "after", "before", "always", "never",
                    "in case", "whenever", "for all"])
    if has_why:
        score += 0.5  # Explains reasoning
    else:
        score -= 0.5  # No reasoning = less useful
    if has_when:
        score += 0.3  # Explains context

    return max(1.0, min(5.0, score))


def merge_duplicate(
    session: Session,
    existing: Memory,
    new_content: str,
    new_tags: list[str] | None = None,
    new_title: str | None = None,
    similarity: float | None = None,
) -> Memory:
    """Merge new information into existing memory.

    Also records the merge in ``evidence_chain`` so operators can audit what
    got collapsed, and bumps ``merge_count`` so the cluster-size gate can
    halt runaway clustering.
    """
    # Append unique content
    if new_content and new_content not in (existing.content or ""):
        existing.content = f"{existing.content}\n\nAlso: {new_content}"

    # Merge tags — and re-sync the normalized MemoryTag index so propagation
    # and predictive scans see the newly-merged tags. Without this the JSON
    # column and the MemoryTag table drift apart and tag-indexed lookups
    # silently miss the merged tags.
    tags_changed = False
    if new_tags:
        existing_tags = set(existing.tags or [])
        before = set(existing_tags)
        existing_tags.update(new_tags)
        if existing_tags != before:
            existing.tags = list(existing_tags)
            tags_changed = True

    # Evidence of merge — JSON list column, so we reassign a new list to force
    # SQLAlchemy to notice the change (in-place append is easy to miss).
    entry = {
        "type": "dedup_merge",
        "from_title": new_title or "",
        "similarity": round(float(similarity), 4) if similarity is not None else None,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    chain = list(existing.evidence_chain or [])
    chain.append(entry)
    existing.evidence_chain = chain

    # Cluster-size accounting
    existing.merge_count = int(existing.merge_count or 0) + 1

    if tags_changed:
        from memee.engine.tag_index import sync_memory_tags
        session.flush()  # ensure existing.id is resolved and updates are visible
        sync_memory_tags(session, existing)

    session.commit()
    logger.info(
        f"Merged into existing memory: {existing.id[:8]} "
        f"(cluster size now {existing.merge_count})"
    )
    return existing
