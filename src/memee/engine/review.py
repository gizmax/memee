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

from memee.engine.search import search_anti_patterns, search_memories
from memee.storage.models import (
    AntiPattern,
    Decision,
    MaturityLevel,
    Memory,
    MemoryType,
)


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
    """
    # Extract meaningful tokens from diff (added lines only)
    added_lines = _extract_added_lines(diff_text)
    if not added_lines:
        return {"warnings": [], "confirmations": [], "suggestions": []}

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
    """Extract only added lines from a unified diff."""
    lines = []
    for line in diff_text.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            # Remove the leading + and strip
            content = line[1:].strip()
            if content and not content.startswith("#"):  # Skip comments
                lines.append(content)
    return lines


def _extract_keywords(lines: list[str]) -> list[str]:
    """Extract meaningful keywords from code lines.

    Focuses on: function names, library calls, patterns.
    """
    keywords = set()

    # Common patterns to detect
    detectors = [
        (r"import\s+(\w+)", "import"),
        (r"from\s+(\w+)", "import"),
        (r"requests\.(get|post|put|delete)", "http"),
        (r"\.execute\(", "database"),
        (r"SELECT\s+\*", "select-star"),
        (r"eval\(", "eval"),
        (r"eval\(", "security"),
        (r"exec\(", "exec"),
        (r"exec\(", "security"),
        (r"timeout", "timeout"),
        (r"\.env|os\.getenv|environ", "env-config"),
        (r"password|secret|api_key|token", "secrets"),
        (r"async\s+def|await\s+", "async"),
        (r"threading|Thread\(|multiprocessing", "concurrency"),
        (r"except\s*:", "bare-except"),
        (r"except\s+Exception\s*:", "broad-except"),
        (r"git\s+reset\s+--hard", "git-reset"),
        (r"useEffect|useState|useCallback", "react-hooks"),
        (r"componentDidMount|componentWillMount", "react-legacy"),
        (r"style\s*=\s*\{\{", "inline-styles"),
        (r"\.query\(.*\)\.all\(\)", "orm-query"),
        (r"for\s+\w+\s+in\s+.*\.all\(\)", "n-plus-one"),
        (r"retry|backoff|tenacity", "retry-logic"),
        (r"logging\.|logger\.", "logging"),
        (r"pytest|unittest|test_", "testing"),
    ]

    combined = "\n".join(lines)
    for pattern, keyword in detectors:
        if re.search(pattern, combined, re.IGNORECASE):
            keywords.add(keyword)

    # Also extract plain words that might match memory tags
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
) -> list[dict]:
    """Check for anti-pattern matches."""
    warnings = []

    # Direct keyword matching against anti-patterns
    anti_patterns = (
        session.query(Memory)
        .join(AntiPattern, AntiPattern.memory_id == Memory.id)
        .filter(Memory.maturity != MaturityLevel.DEPRECATED.value)
        .all()
    )

    keyword_set = set(keywords)
    for memory in anti_patterns:
        mem_tags = set(memory.tags or [])
        overlap = mem_tags & keyword_set

        if overlap:
            ap = memory.anti_pattern
            warnings.append({
                "type": "anti_pattern",
                "memory_id": memory.id,
                "title": memory.title,
                "severity": ap.severity if ap else "medium",
                "trigger": ap.trigger if ap else "",
                "consequence": ap.consequence if ap else "",
                "alternative": ap.alternative if ap else "",
                "confidence": memory.confidence_score,
                "matched_keywords": list(overlap),
            })

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    warnings.sort(key=lambda w: severity_order.get(w["severity"], 4))

    return warnings


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
