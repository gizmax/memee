"""Pre-task briefing + CLAUDE.md injection: PUSH knowledge to agents.

This is the missing piece. Instead of agents asking for knowledge (PULL),
Memee tells agents what they need to know BEFORE they start (PUSH).

Three outputs:
  1. briefing() → structured text for agent context
  2. generate_claudemd_section() → markdown for CLAUDE.md injection
  3. inject_claudemd() → writes directly into project's CLAUDE.md
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import case, func
from sqlalchemy.orm import Session

# Explicit terminator for the auto-generated Memee block. The legacy generator
# relied on the "## " heading heuristic to find the end, which broke when the
# next section used a different heading level. An explicit end marker is the
# sane pair to `marker_start` and lets reinjections be idempotent.
MEMEE_END_MARKER = "<!-- /memee-section -->"

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

    # 1. Patterns — route by task_description when provided, else fall back
    # to project-stack filtering. Old code accepted task_description but
    # never used it; an agent calling briefing(task="write unit tests")
    # would get the same generic top-confidence patterns as briefing().
    patterns: list = []
    if task_description:
        from memee.engine.search import search_memories

        hits = search_memories(
            session,
            task_description,
            tags=list(stack_tags) if stack_tags else None,
            memory_type=MemoryType.PATTERN.value,
            limit=max_patterns * 3,
            use_vectors=False,  # briefing is a hot path
        )
        for r in hits:
            m = r["memory"]
            if m.maturity not in (
                MaturityLevel.CANON.value,
                MaturityLevel.VALIDATED.value,
            ):
                continue
            patterns.append(m)
            if len(patterns) >= max_patterns:
                break
    else:
        pattern_q = session.query(Memory).filter(
            Memory.type == MemoryType.PATTERN.value,
            Memory.maturity.in_(
                [MaturityLevel.CANON.value, MaturityLevel.VALIDATED.value]
            ),
        ).order_by(Memory.confidence_score.desc())
        for m in pattern_q.limit(max_patterns * 3).all():
            mem_tags = set(m.tags or [])
            relevant = not stack_tags or bool(mem_tags & stack_tags)
            if relevant:
                patterns.append(m)
            if len(patterns) >= max_patterns:
                break

    # 2. Critical anti-patterns. Critical severity ALWAYS shows regardless of
    # task (institutional DNA); the rest is task-filtered when task provided.
    severity_rank = case(
        {"critical": 0, "high": 1, "medium": 2, "low": 3},
        value=AntiPattern.severity,
        else_=4,
    )
    critical_q = (
        session.query(Memory, AntiPattern)
        .join(AntiPattern, AntiPattern.memory_id == Memory.id)
        .filter(
            AntiPattern.severity == "critical",
            Memory.maturity != MaturityLevel.DEPRECATED.value,
        )
        .order_by(Memory.confidence_score.desc())
    )
    critical_rows = critical_q.limit(max_warnings).all()

    warnings: list = list(critical_rows)
    seen_ids = {m.id for m, _ in critical_rows}

    if task_description and len(warnings) < max_warnings:
        from memee.engine.search import search_memories

        hits = search_memories(
            session,
            task_description,
            tags=list(stack_tags) if stack_tags else None,
            memory_type=MemoryType.ANTI_PATTERN.value,
            limit=max_warnings * 3,
            use_vectors=False,
        )
        for r in hits:
            m = r["memory"]
            if m.id in seen_ids:
                continue
            if m.maturity == MaturityLevel.DEPRECATED.value:
                continue
            ap = m.anti_pattern
            if not ap:
                continue
            warnings.append((m, ap))
            seen_ids.add(m.id)
            if len(warnings) >= max_warnings:
                break
    else:
        ap_q = (
            session.query(Memory, AntiPattern)
            .join(AntiPattern, AntiPattern.memory_id == Memory.id)
            .filter(Memory.maturity != MaturityLevel.DEPRECATED.value)
            .order_by(severity_rank, Memory.confidence_score.desc())
        )
        for m, ap in ap_q.limit(max_warnings * 3).all():
            if m.id in seen_ids:
                continue
            mem_tags = set(m.tags or [])
            relevant = (
                not stack_tags
                or bool(mem_tags & stack_tags)
                or ap.severity == "critical"
            )
            if relevant:
                warnings.append((m, ap))
                seen_ids.add(m.id)
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

    truncated = lines[:max_lines]
    # Always emit the end marker AFTER any truncation so it survives max_lines.
    truncated.append(MEMEE_END_MARKER)
    return "\n".join(truncated)


def inject_claudemd(
    project_path: str,
    session: Session | None = None,
) -> dict:
    """Write organizational knowledge into project's CLAUDE.md.

    Appends or replaces the "## Organizational Knowledge" section.
    Preserves all other content in CLAUDE.md.

    Guarantees:
      * Atomic replacement on POSIX (tmp-write then os.replace).
      * Section delimited by a start marker (`## Organizational Knowledge …`)
        and an explicit end marker (`<!-- /memee-section -->`). Running inject
        twice leaves exactly ONE section in the file.
      * Legacy files without the end marker fall back to the "next `## `
        heading" heuristic for one-time migration.
    """
    if session is None:
        session = get_session(init_db())

    abs_path = str(Path(project_path).resolve())
    section = generate_claudemd_section(session, abs_path)

    # Target: .claude/CLAUDE.md in project root (per-project instructions)
    claude_dir = Path(abs_path) / ".claude"
    claude_md = claude_dir / "CLAUDE.md"

    existing = ""
    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")

    marker_start = "## Organizational Knowledge (auto-generated by Memee)"
    end_marker = MEMEE_END_MARKER

    action: str
    if marker_start in existing:
        start_idx = existing.index(marker_start)
        before = existing[:start_idx]
        tail = existing[start_idx:]

        # Preferred: explicit end marker (new format).
        end_pos = tail.find(end_marker)
        if end_pos != -1:
            after = tail[end_pos + len(end_marker):]
        else:
            # Legacy fallback: find next "## " heading that isn't our own.
            remaining_lines = tail.split("\n")
            end_idx = len(remaining_lines)
            for i, line in enumerate(remaining_lines[1:], 1):
                if line.startswith("## ") and marker_start not in line:
                    end_idx = i
                    break
            after = "\n".join(remaining_lines[end_idx:])

        new_content = before.rstrip() + "\n\n" + section + "\n\n" + after.lstrip()
        action = "replaced"
    else:
        new_content = existing.rstrip() + "\n\n" + section + "\n"
        action = "appended"

    claude_dir.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to tmp file, then os.replace → POSIX-atomic rename.
    tmp_path = claude_md.with_suffix(claude_md.suffix + ".tmp")
    try:
        tmp_path.write_text(new_content, encoding="utf-8")
        os.replace(tmp_path, claude_md)
    except Exception:
        # Clean up the tmp file if something went wrong before the rename.
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise

    return {
        "path": str(claude_md),
        "section_lines": len(section.split("\n")),
        "total_lines": len(new_content.split("\n")),
        "action": action,
    }
