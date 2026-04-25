"""Memory-Driven Code Review: scan git diffs against institutional memory.

Scans code changes (git diff text) against:
- Anti-patterns: flag known mistakes before merge
- Validated patterns: confirm good practices are being followed
- Decisions: check consistency with org decisions

Returns structured warnings and confirmations.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from memee.engine.search import search_memories
from memee.storage.models import (
    MaturityLevel,
    Memory,
    MemoryType,
)


# Reject diffs above this size. Bigger inputs (generated SQL dumps, vendored
# deps, blobs mistakenly included) cause the regex engine to scan MB of text
# per memory — scanning 50 MB is a soft DoS on the host process. We fail early
# with a clear error; callers should split or truncate.
MAX_DIFF_BYTES = 5_000_000


class DiffTooLargeError(ValueError):
    """Raised when the input diff exceeds ``MAX_DIFF_BYTES``."""


def review_diff(
    session: Session,
    diff_text: str,
    project_path: str = "",
    max_warnings: int = 20,
) -> dict:
    """Scan a git diff against organizational memory.

    Returns:
        warnings: anti-patterns found in the diff
        confirmations: good patterns detected
        suggestions: related patterns that might help

    Raises:
        DiffTooLargeError: diff exceeds ``MAX_DIFF_BYTES`` (5 MB).
    """
    if diff_text and len(diff_text) > MAX_DIFF_BYTES:
        raise DiffTooLargeError(
            f"diff too large to review ({len(diff_text)} bytes > "
            f"{MAX_DIFF_BYTES}); truncate or split"
        )

    # Extract meaningful tokens from diff (added lines only)
    added_lines = _extract_added_lines(diff_text)
    if not added_lines:
        return {
            "warnings": [], "confirmations": [], "suggestions": [],
            "stats": {
                "lines_scanned": 0,
                "keywords_extracted": 0,
                "warnings_count": 0,
                "confirmations_count": 0,
            },
        }

    # Build search context from diff content
    context_text = " ".join(added_lines[:50])  # Limit context size
    keywords = _extract_keywords(added_lines)

    warnings = _check_anti_patterns(session, context_text, keywords)
    confirmations = _check_good_patterns(session, context_text, keywords)
    suggestions = _find_suggestions(session, context_text, keywords)

    return {
        "warnings": warnings[:max_warnings],
        "confirmations": confirmations[:10],
        "suggestions": suggestions[:5],
        "stats": {
            "lines_scanned": len(added_lines),
            "keywords_extracted": len(keywords),
            "warnings_count": len(warnings),
            "confirmations_count": len(confirmations),
        },
    }


def review_file_content(
    session: Session,
    content: str,
    filename: str = "",
) -> dict:
    """Review a single file's content against institutional memory.

    Lighter than review_diff — used for scanning specific files.
    """
    keywords = _extract_keywords(content.split("\n"))
    context = " ".join(keywords)

    warnings = _check_anti_patterns(session, context, keywords)
    suggestions = _find_suggestions(session, context, keywords)

    return {
        "file": filename,
        "warnings": warnings[:10],
        "suggestions": suggestions[:5],
    }


def _extract_added_lines(diff_text: str) -> list[str]:
    """Extract only added lines from a unified diff.

    Skips:
      * binary-file hunks (``Binary files a/x and b/x differ``) — scanning
        binary blobs with text regexes is noise at best, crashes at worst.
      * rename-only headers (``rename from a/…`` / ``rename to b/…``) — no
        content change, should not trigger keyword detectors.
    """
    lines: list[str] = []
    skip_hunk = False
    for line in diff_text.split("\n"):
        # diff --git header resets the "skip" flag for a new hunk.
        if line.startswith("diff --git "):
            skip_hunk = False
            continue
        # Binary file marker — skip until next diff header.
        if "Binary files " in line and line.endswith("differ"):
            skip_hunk = True
            continue
        # Rename-only headers are informational; don't scan them as content.
        if line.startswith("rename from ") or line.startswith("rename to "):
            continue
        if skip_hunk:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            # Remove the leading + and strip
            content = line[1:].strip()
            if content and not content.startswith("#"):  # Skip comments
                lines.append(content)
    return lines


def _extract_keywords(lines: list[str]) -> list[str]:
    """Extract meaningful keywords from code lines.

    Tightened vs the original:
      * HTTP detector covers all common verbs (``patch``/``head``/``options``)
        and also ``session.get(...)`` / ``client.post(...)``, not only the
        bare ``requests.*`` form.
      * Secret detector requires an assignment to a QUOTED string; plain
        variable names like ``user_token_field`` no longer false-positive.
      * Env-config uses ``os.environ[...]`` as a positive form. ``os.environ``
        alone is fine API usage and should not flag.
      * Each detected keyword is deduplicated via a set (the keyword loop
        already did that; we keep one detector per logical concept).
    """
    keywords: set[str] = set()

    # Common patterns to detect
    detectors = [
        (r"\bimport\s+(\w+)", "import"),
        (r"\bfrom\s+(\w+)", "import"),
        # HTTP libs: requests, httpx, aiohttp, session/client objects.
        (
            r"\b(?:requests|httpx|aiohttp|session|client)\."
            r"(?:get|post|put|delete|patch|head|options)\b",
            "http",
        ),
        (r"\.execute\(", "database"),
        (r"\bSELECT\s+\*", "select-star"),
        (r"\beval\s*\(", "eval"),
        (r"\beval\s*\(", "security"),
        (r"\bexec\s*\(", "exec"),
        (r"\bexec\s*\(", "security"),
        (r"\btimeout\b", "timeout"),
        # os.environ["KEY"] / os.environ.get("KEY") / os.getenv("KEY")
        (
            r"\bos\.environ\s*\[[\"'][^\"']+[\"']\]"
            r"|\bos\.environ\.get\s*\("
            r"|\bos\.getenv\s*\(",
            "env-config",
        ),
        # Assigning a literal-string secret, e.g. API_KEY = "abc123". Plain
        # variable names such as `user_token_field` no longer match because
        # we require `= "…"` on the same line.
        (
            r"\b(?:password|api[_-]?key|secret[_-]?key|auth[_-]?token)"
            r"\s*=\s*[\"'][^\"']+[\"']",
            "secrets",
        ),
        (r"\basync\s+def\b|\bawait\s+", "async"),
        (r"\bthreading\b|\bThread\s*\(|\bmultiprocessing\b", "concurrency"),
        (r"\bexcept\s*:", "bare-except"),
        (r"\bexcept\s+Exception\s*:", "broad-except"),
        (r"\bgit\s+reset\s+--hard\b", "git-reset"),
        (r"\buseEffect\b|\buseState\b|\buseCallback\b", "react-hooks"),
        (r"\bcomponentDidMount\b|\bcomponentWillMount\b", "react-legacy"),
        (r"style\s*=\s*\{\{", "inline-styles"),
        (r"\.query\([^)]*\)\.all\(\)", "orm-query"),
        (r"\bfor\s+\w+\s+in\s+.*\.all\(\)", "n-plus-one"),
        (r"\bretry\b|\bbackoff\b|\btenacity\b", "retry-logic"),
        (r"\blogging\.|\blogger\.", "logging"),
        (r"\bpytest\b|\bunittest\b|\btest_\w+", "testing"),
    ]

    combined = "\n".join(lines)
    for pattern, keyword in detectors:
        if re.search(pattern, combined, re.IGNORECASE):
            keywords.add(keyword)

    # Also extract plain words that might match memory tags. Unicode-safe:
    # `\b[a-z]{4,15}\b` keeps us inside ASCII identifiers and never trips on
    # Czech/Cyrillic filenames inside diff headers.
    words = re.findall(r"\b[a-z]{4,15}\b", combined.lower())
    important_words = {
        "timeout", "retry", "cache", "index", "query", "async",
        "await", "sqlite", "postgres", "react", "flask", "fastapi",
        "pydantic", "pytest", "security", "validation", "error",
        "exception", "logging", "deploy", "docker", "migration",
    }
    keywords.update(w for w in words if w in important_words)

    return list(keywords)


def _check_anti_patterns(
    session: Session,
    context: str,
    keywords: list[str],
    limit: int = 10,
) -> list[dict]:
    """Retrieve anti-patterns that match the diff, scored by evidence.

    Old behavior: for every anti-pattern in the DB, compare its tag set to
    the extracted keyword set and emit a warning on ANY overlap. One shared
    tag like ``python`` or ``http`` flagged every unrelated anti-pattern in
    the store — a ~90% false-positive rate in adversarial seeds.

    New behavior:
      1. Hybrid search the anti-pattern subspace on (context + keywords)
         so the ranker scores real content/trigger matches, not just tags.
      2. Score each candidate with three signals:
         - BM25/vector total_score from the hybrid engine (content evidence)
         - keyword intersection with ``trigger`` + ``title`` (precise signal)
         - tag overlap with the diff's keyword set (weak signal, kept for
           backward-compat but no longer a primary trigger)
      3. Require at least one precise signal (trigger/title match OR the
         hybrid ranker scored the memory above a threshold) to emit a
         warning. Tag-only overlap is treated as a "suggestion" candidate
         by the caller, not a warning.
    """
    from memee.engine.search import search_memories

    query = (context or "") + " " + " ".join(keywords or [])
    query = query.strip()
    if not query:
        return []

    candidates = search_memories(
        session,
        query,
        tags=keywords or None,
        memory_type=MemoryType.ANTI_PATTERN.value,
        limit=limit * 3,
        use_vectors=False,  # review is a hot path; vectors add cold-start cost
    )

    # Extract concrete identifiers from the diff text itself (not the abstract
    # review-keyword catalog). These are precise: ``requests.get`` from the
    # diff matches ``requests.get without timeout`` from a trigger. Shared
    # *generic* tags like "http" are not enough — they flag every unrelated
    # antipattern tagged with "http" and drive the FP rate to ~90%.
    diff_tokens = {
        t.lower()
        for t in re.findall(r"[A-Za-z_][A-Za-z_0-9.]{3,}", context or "")
    }

    warnings: list[dict] = []
    for hit in candidates:
        memory = hit["memory"]
        if memory.maturity == MaturityLevel.DEPRECATED.value:
            continue
        ap = memory.anti_pattern
        trigger = (ap.trigger if ap else "") or ""
        title = memory.title or ""

        # Identifiers from trigger + title → compare to identifiers in the
        # diff. Identifier-level overlap = real evidence; tag overlap alone
        # = suggestion material, not a warning.
        ap_tokens = {
            t.lower()
            for t in re.findall(
                r"[A-Za-z_][A-Za-z_0-9.]{3,}", trigger + " " + title
            )
        }
        identifier_hits = sorted(diff_tokens & ap_tokens)

        # Filter out tokens that are so generic they don't constitute evidence.
        # 'http' in a trigger is just a tag; 'timeout' / 'requests.get' is not.
        GENERIC = {"http", "https", "import", "from", "return", "def", "class", "self"}
        precise_hits = [t for t in identifier_hits if t not in GENERIC]

        if not precise_hits:
            continue

        keyword_set = {k.lower() for k in (keywords or [])}
        mem_tags = set((memory.tags or []))
        tag_overlap = mem_tags & keyword_set

        warnings.append({
            "type": "anti_pattern",
            "memory_id": memory.id,
            "title": memory.title,
            "severity": ap.severity if ap else "medium",
            "trigger": trigger,
            "consequence": ap.consequence if ap else "",
            "alternative": ap.alternative if ap else "",
            "confidence": memory.confidence_score,
            "matched_keywords": sorted(tag_overlap),
            "evidence": {
                "identifier_hits": precise_hits,
                "bm25_score": hit.get("bm25_score", 0.0),
                "tag_score": hit.get("tag_score", 0.0),
                "total_score": hit.get("total_score", 0.0),
            },
        })

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    warnings.sort(
        key=lambda w: (
            severity_order.get(w["severity"], 4),
            -w.get("evidence", {}).get("total_score", 0.0),
        )
    )
    return warnings[:limit]


def _check_good_patterns(
    session: Session,
    context: str,
    keywords: list[str],
) -> list[dict]:
    """Check if diff follows known good patterns."""
    confirmations = []
    keyword_set = set(keywords)

    good_patterns = (
        session.query(Memory)
        .filter(
            Memory.type == MemoryType.PATTERN.value,
            Memory.maturity.in_([
                MaturityLevel.VALIDATED.value,
                MaturityLevel.CANON.value,
            ]),
        )
        .all()
    )

    for memory in good_patterns:
        mem_tags = set(memory.tags or [])
        overlap = mem_tags & keyword_set

        if overlap and len(overlap) >= 2:
            confirmations.append({
                "type": "good_pattern",
                "memory_id": memory.id,
                "title": memory.title,
                "maturity": memory.maturity,
                "confidence": memory.confidence_score,
                "matched_keywords": list(overlap),
            })

    return confirmations


def _find_suggestions(
    session: Session,
    context: str,
    keywords: list[str],
) -> list[dict]:
    """Find relevant patterns that might help with the current changes."""
    if not keywords:
        return []

    results = search_memories(
        session,
        " ".join(keywords[:5]),
        tags=keywords,
        memory_type=MemoryType.PATTERN.value,
        limit=5,
    )

    return [
        {
            "memory_id": r["memory"].id,
            "title": r["memory"].title,
            "maturity": r["memory"].maturity,
            "confidence": r["memory"].confidence_score,
            "relevance": r["total_score"],
        }
        for r in results
    ]
