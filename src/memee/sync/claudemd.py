"""Parser for CLAUDE.md files — extracts memories from existing project knowledge."""

from __future__ import annotations

import re
from pathlib import Path

from memee.storage.database import get_session, init_db
from memee.storage.models import (
    AntiPattern,
    Decision,
    Memory,
    MemoryType,
    Project,
    ProjectMemory,
    Severity,
)


def sync_claudemd(project_path: str) -> dict:
    """Parse CLAUDE.md from a project and import as memories.

    Extracts:
    - Anti-patterns from "SLEPÉ CESTY" / "blind spots" / "don't" sections
    - Stack/technology decisions from stack tables or lists
    - Lessons learned from sections containing "lesson", "learned", "note"

    Returns stats: {anti_patterns: N, decisions: N, lessons: N, skipped: N}
    """
    claude_md = Path(project_path) / "CLAUDE.md"
    if not claude_md.exists():
        return {"error": f"No CLAUDE.md found at {project_path}"}

    content = claude_md.read_text(encoding="utf-8")
    engine = init_db()
    session = get_session(engine)

    # Find the project
    abs_path = str(Path(project_path).resolve())
    proj = session.query(Project).filter_by(path=abs_path).first()

    stats = {"anti_patterns": 0, "decisions": 0, "lessons": 0, "skipped": 0}

    # Parse sections
    sections = _split_sections(content)

    for heading, body in sections:
        # Anti-patterns: "slepé cesty", "blind", "don't", "avoid", "gotcha"
        if _heading_matches(
            heading,
            ["slepé cesty", "blind", "don't", "avoid", "gotcha", "pitfall"],
        ):
            count = _extract_anti_patterns(session, proj, heading, body)
            stats["anti_patterns"] += count

        # Decisions: "stack", "technolog", "rozhodnut", "decision", "chose"
        elif _heading_matches(
            heading,
            ["stack", "technolog", "rozhodnut", "decision", "chose"],
        ):
            count = _extract_decisions(session, proj, heading, body)
            stats["decisions"] += count

        # Lessons: "lesson", "learned", "note", "tip", "naučen", "poznamk"
        elif _heading_matches(
            heading,
            ["lesson", "learned", "note", "tip", "naučen", "poznamk", "lekce"],
        ):
            count = _extract_lessons(session, proj, heading, body)
            stats["lessons"] += count

    session.commit()
    return stats


_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _split_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown content into (heading, body) tuples.

    Respects fenced code blocks — ``## heading`` lines inside a ```` ``` ````
    block are body text, not section boundaries.
    """
    sections = []
    current_heading = "Root"
    current_body: list[str] = []
    in_fence = False

    for line in content.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            current_body.append(line)
            continue
        if not in_fence and line.startswith("#"):
            if current_body:
                sections.append((current_heading, "\n".join(current_body)))
            current_heading = line.lstrip("#").strip()
            current_body = []
        else:
            current_body.append(line)

    if current_body:
        sections.append((current_heading, "\n".join(current_body)))

    return sections


def _heading_tokens(heading: str) -> set[str]:
    """Lowercase word tokens of ``heading``. Used for full-word keyword matches
    so ``Things the parser doesn't do`` doesn't misclassify as an anti-pattern
    via substring "don't" matching inside "doesn't".
    """
    return {t.lower() for t in re.findall(r"[\w']+", heading)}


# Keywords that must match a full word token (contractions + short stems that
# cause false positives on substring match).
_STRICT_WORD_KEYWORDS = {"don't", "avoid", "blind", "chose"}


def _heading_matches(heading: str, keywords: list[str]) -> bool:
    """True if any keyword appears in the heading. Contractions and short
    stems (see ``_STRICT_WORD_KEYWORDS``) must match as a full word token so
    e.g. "doesn't" or "blinded" don't trigger misclassification. Everything
    else uses the historical substring check.
    """
    tokens = _heading_tokens(heading)
    hlow = heading.lower()
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in _STRICT_WORD_KEYWORDS:
            if kw_lower in tokens:
                return True
        else:
            if kw_lower in hlow:
                return True
    return False


def _link_project_memory(session, proj: Project | None, memory_id: str) -> None:
    """Ensure a ProjectMemory link exists for (proj, memory). Idempotent."""
    if not proj:
        return
    existing_link = (
        session.query(ProjectMemory)
        .filter_by(project_id=proj.id, memory_id=memory_id)
        .first()
    )
    if existing_link is None:
        session.add(ProjectMemory(project_id=proj.id, memory_id=memory_id))


def _extract_anti_patterns(
    session, proj: Project | None, heading: str, body: str
) -> int:
    """Extract anti-patterns from a section."""
    count = 0
    # Look for list items (- or *)
    items = re.findall(r"^[\-\*]\s+(.+)$", body, re.MULTILINE)

    for item in items:
        # Skip empty or too short items
        if len(item.strip()) < 5:
            continue

        title = item.strip()[:500]

        # Dedup by (title, type): a "pattern" and a "lesson" with the same
        # title must not collide. If the memory already exists, DO NOT
        # re-create it — but DO link it to the current project so the
        # knowledge is visible there too.
        existing = (
            session.query(Memory)
            .filter_by(title=title, type=MemoryType.ANTI_PATTERN.value)
            .first()
        )
        if existing:
            _link_project_memory(session, proj, existing.id)
            continue

        memory = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title=title,
            content=item.strip(),
            tags=["imported", "claude-md"],
        )
        session.add(memory)
        session.flush()

        # Try to parse "X → use Y instead" pattern
        alternative = ""
        arrow_match = re.search(r"→\s*(.+)$", item)
        if arrow_match:
            alternative = arrow_match.group(1).strip()

        ap = AntiPattern(
            memory_id=memory.id,
            severity=Severity.MEDIUM.value,
            trigger=item.strip(),
            consequence="Known issue from project experience",
            alternative=alternative,
        )
        session.add(ap)

        _link_project_memory(session, proj, memory.id)

        count += 1

    return count


def _extract_decisions(
    session, proj: Project | None, heading: str, body: str
) -> int:
    """Extract technology decisions from a section."""
    count = 0

    # Look for table rows: | purpose | tech |
    table_rows = re.findall(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|", body)
    for purpose, tech in table_rows:
        # Skip header/separator rows
        if purpose.startswith("-") or purpose.lower() in ("účel", "purpose", ""):
            continue

        title = f"Decision: {tech.strip()} for {purpose.strip()}"[:500]
        existing = (
            session.query(Memory)
            .filter_by(title=title, type=MemoryType.DECISION.value)
            .first()
        )
        if existing:
            _link_project_memory(session, proj, existing.id)
            continue

        memory = Memory(
            type=MemoryType.DECISION.value,
            title=title,
            content=f"Chose {tech.strip()} for {purpose.strip()}",
            tags=["imported", "claude-md", "stack"],
        )
        session.add(memory)
        session.flush()

        decision = Decision(
            memory_id=memory.id,
            chosen=tech.strip(),
            alternatives=[],
            criteria=[{"name": "project_requirement", "value": purpose.strip()}],
        )
        session.add(decision)

        _link_project_memory(session, proj, memory.id)

        count += 1

    return count


def _extract_lessons(
    session, proj: Project | None, heading: str, body: str
) -> int:
    """Extract lessons learned from a section."""
    count = 0
    items = re.findall(r"^[\-\*]\s+(.+)$", body, re.MULTILINE)

    for item in items:
        if len(item.strip()) < 10:
            continue

        title = item.strip()[:500]
        existing = (
            session.query(Memory)
            .filter_by(title=title, type=MemoryType.LESSON.value)
            .first()
        )
        if existing:
            _link_project_memory(session, proj, existing.id)
            continue

        memory = Memory(
            type=MemoryType.LESSON.value,
            title=title,
            content=item.strip(),
            tags=["imported", "claude-md"],
        )
        session.add(memory)
        session.flush()

        _link_project_memory(session, proj, memory.id)

        count += 1

    return count
