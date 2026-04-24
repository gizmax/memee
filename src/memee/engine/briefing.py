"""Pre-task briefing + CLAUDE.md injection: PUSH knowledge to agents.

This is the missing piece. Instead of agents asking for knowledge (PULL),
Memee tells agents what they need to know BEFORE they start (PUSH).

Three outputs:
  1. briefing() → structured text for agent context
  2. generate_claudemd_section() → markdown for CLAUDE.md injection
  3. inject_claudemd() → writes directly into project's CLAUDE.md
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from memee.storage.database import get_session, init_db
from memee.storage.models import (
    AntiPattern,
    Decision,
    MaturityLevel,
    Memory,
    MemoryType,
    Project,
    ProjectMemory,
)


def briefing(
    session: Session,
    project_path: str | None = None,
    task_description: str = "",
    max_patterns: int = 7,
    max_warnings: int = 5,
    max_decisions: int = 3,
    compact: bool = False,
) -> str:
    """Generate a knowledge briefing for an agent starting a task.

    Returns structured text ready to inject into agent context.
    """
    # Find project
    project = None
    if project_path:
        abs_path = str(Path(project_path).resolve())
        project = session.query(Project).filter_by(path=abs_path).first()

    # Get project stack for matching
    stack_tags = set()
    if project:
        for s in (project.stack or []):
            stack_tags.add(s.lower())
        for t in (project.tags or []):
            stack_tags.add(t.lower())

    # 1. Canon + Validated patterns
    pattern_q = session.query(Memory).filter(
        Memory.type == MemoryType.PATTERN.value,
        Memory.maturity.in_([MaturityLevel.CANON.value, MaturityLevel.VALIDATED.value]),
    ).order_by(Memory.confidence_score.desc())

    patterns = []
    for m in pattern_q.limit(max_patterns * 3).all():
        mem_tags = set(m.tags or [])
        relevant = not stack_tags or bool(mem_tags & stack_tags)
        if relevant:
            patterns.append(m)
        if len(patterns) >= max_patterns:
            break

    # 2. Critical anti-patterns
    ap_q = (
        session.query(Memory, AntiPattern)
        .join(AntiPattern, AntiPattern.memory_id == Memory.id)
        .filter(Memory.maturity != MaturityLevel.DEPRECATED.value)
        .order_by(
            # Critical first, then high
            AntiPattern.severity.desc(),
            Memory.confidence_score.desc(),
        )
    )

    warnings = []
    for m, ap in ap_q.limit(max_warnings * 3).all():
        mem_tags = set(m.tags or [])
        relevant = not stack_tags or bool(mem_tags & stack_tags) or ap.severity == "critical"
        if relevant:
            warnings.append((m, ap))
        if len(warnings) >= max_warnings:
            break

    # 3. Relevant decisions
    dec_q = (
        session.query(Memory, Decision)
        .join(Decision, Decision.memory_id == Memory.id)
    )
    if project:
        dec_q = dec_q.join(ProjectMemory, ProjectMemory.memory_id == Memory.id).filter(
            ProjectMemory.project_id == project.id
        )

    decisions = dec_q.order_by(Memory.created_at.desc()).limit(max_decisions).all()

    # 4. Stats
    total_memories = session.query(func.count(Memory.id)).scalar() or 0
    total_canon = session.query(func.count(Memory.id)).filter(
        Memory.maturity == MaturityLevel.CANON.value
    ).scalar() or 0

    # Format
    return _format_briefing(
        project, patterns, warnings, decisions,
        total_memories, total_canon, task_description, compact,
    )


def _format_briefing(
    project, patterns, warnings, decisions,
    total_memories, total_canon, task_description, compact,
) -> str:
    """Format briefing as structured text."""
    lines = []
    proj_name = project.name if project else "this project"

    if not compact:
        lines.append(f"## Memee Briefing for {proj_name}")
        lines.append("")

    # Patterns
    if patterns:
        lines.append("### Must-know patterns:" if not compact else "PATTERNS:")
        for m in patterns:
            conf = f"{m.confidence_score:.0%}"
            mat = m.maturity
            lines.append(f"- {m.title} (conf: {conf}, {mat})")
        lines.append("")

    # Warnings
    if warnings:
        lines.append("### Critical warnings — DO NOT:" if not compact else "WARNINGS:")
        for m, ap in warnings:
            sev = ap.severity.upper()
            lines.append(f"- [{sev}] {m.title}")
            if ap.alternative and not compact:
                lines.append(f"  Instead: {ap.alternative}")
        lines.append("")

    # Decisions
    if decisions and not compact:
        lines.append("### Tech decisions already made:")
        for m, dec in decisions:
            alts = ", ".join(a.get("name", "?") for a in (dec.alternatives or []))
            lines.append(f"- {dec.chosen} (over {alts})" if alts else f"- {dec.chosen}")
        lines.append("")

    # Footer
    if not compact:
        lines.append(f"Org knowledge: {total_memories} memories, {total_canon} canon.")
        lines.append("Run `memee search <query>` for more.")
    else:
        lines.append(f"[{total_memories} memories, {total_canon} canon — memee search for more]")

    return "\n".join(lines)


def generate_claudemd_section(
    session: Session,
    project_path: str | None = None,
    max_lines: int = 40,
) -> str:
    """Generate a CLAUDE.md section with organizational knowledge.

    This is the BRIDGE between Memee DB and agent context.
    Claude Code reads CLAUDE.md automatically — so this is how
    knowledge reaches agents without them asking.
    """
    lines = []
    lines.append("## Organizational Knowledge (auto-generated by Memee)")
    lines.append("")
    lines.append("> This section is auto-generated. Run `memee inject` to update.")
    lines.append("")

    b = briefing(session, project_path, compact=False,
                 max_patterns=5, max_warnings=5, max_decisions=3)

    # Strip the "## Memee Briefing" header (we have our own)
    for line in b.split("\n"):
        if line.startswith("## Memee Briefing"):
            continue
        lines.append(line)

    lines.append("")
    lines.append(f"*Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")

    return "\n".join(lines[:max_lines])


def inject_claudemd(
    project_path: str,
    session: Session | None = None,
) -> dict:
    """Write organizational knowledge into project's CLAUDE.md.

    Appends or replaces the "## Organizational Knowledge" section.
    Preserves all other content in CLAUDE.md.
    """
    if session is None:
        session = get_session(init_db())

    abs_path = str(Path(project_path).resolve())
    section = generate_claudemd_section(session, abs_path)

    # Target: .claude/CLAUDE.md in project root (per-project instructions)
    claude_dir = Path(abs_path) / ".claude"
    claude_md = claude_dir / "CLAUDE.md"

    # Read existing content
    existing = ""
    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")

    # Replace or append the section
    marker_start = "## Organizational Knowledge (auto-generated by Memee)"
    marker_end = "*Last updated:"

    if marker_start in existing:
        # Replace existing section
        before = existing[:existing.index(marker_start)]
        # Find end of section (next ## or end of file)
        after_marker = existing[existing.index(marker_start):]
        # Find next section header after our section
        remaining_lines = after_marker.split("\n")
        end_idx = len(remaining_lines)
        for i, line in enumerate(remaining_lines[1:], 1):
            if line.startswith("## ") and marker_start not in line:
                end_idx = i
                break
        after = "\n".join(remaining_lines[end_idx:])
        new_content = before.rstrip() + "\n\n" + section + "\n\n" + after.lstrip()
    else:
        # Append
        new_content = existing.rstrip() + "\n\n" + section + "\n"

    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_md.write_text(new_content, encoding="utf-8")

    return {
        "path": str(claude_md),
        "section_lines": len(section.split("\n")),
        "total_lines": len(new_content.split("\n")),
        "action": "replaced" if marker_start in existing else "appended",
    }
