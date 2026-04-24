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
        heading_lower = heading.lower()

        # Anti-patterns: "slepé cesty", "blind", "don't", "avoid", "gotcha"
        if any(
            kw in heading_lower
            for kw in ["slepé cesty", "blind", "don't", "avoid", "gotcha", "pitfall"]
        ):
            count = _extract_anti_patterns(session, proj, heading, body)
            stats["anti_patterns"] += count

        # Decisions: "stack", "technolog", "rozhodnut", "decision", "chose"
        elif any(
            kw in heading_lower
            for kw in ["stack", "technolog", "rozhodnut", "decision", "chose"]
        ):
            count = _extract_decisions(session, proj, heading, body)
            stats["decisions"] += count

        # Lessons: "lesson", "learned", "note", "tip", "naučen", "poznamk"
        elif any(
            kw in heading_lower
            for kw in ["lesson", "learned", "note", "tip", "naučen", "poznamk", "lekce"]
        ):
            count = _extract_lessons(session, proj, heading, body)
            stats["lessons"] += count

    session.commit()
    return stats


def _split_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown content into (heading, body) tuples."""
    sections = []
    current_heading = "Root"
    current_body = []

    for line in content.split("\n"):
        if line.startswith("#"):
            if current_body:
                sections.append((current_heading, "\n".join(current_body)))
            current_heading = line.lstrip("#").strip()
            current_body = []
        else:
            current_body.append(line)

    if current_body:
        sections.append((current_heading, "\n".join(current_body)))

    return sections


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

        # Check for duplicates
        existing = session.query(Memory).filter_by(title=item.strip()[:500]).first()
        if existing:
            continue

        memory = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title=item.strip()[:500],
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

        if proj:
            pm = ProjectMemory(project_id=proj.id, memory_id=memory.id)
            session.add(pm)

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

        title = f"Decision: {tech.strip()} for {purpose.strip()}"
        existing = session.query(Memory).filter_by(title=title[:500]).first()
        if existing:
            continue

        memory = Memory(
            type=MemoryType.DECISION.value,
            title=title[:500],
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

        if proj:
            pm = ProjectMemory(project_id=proj.id, memory_id=memory.id)
            session.add(pm)

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

        existing = session.query(Memory).filter_by(title=item.strip()[:500]).first()
        if existing:
            continue

        memory = Memory(
            type=MemoryType.LESSON.value,
            title=item.strip()[:500],
            content=item.strip(),
            tags=["imported", "claude-md"],
        )
        session.add(memory)

        if proj:
            session.flush()
            pm = ProjectMemory(project_id=proj.id, memory_id=memory.id)
            session.add(pm)

        count += 1

    return count
