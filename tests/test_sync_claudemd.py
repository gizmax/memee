"""Regression tests for the CLAUDE.md sync parser.

Covers:
  - Dedup by (title, type) — same bullet imported from two projects yields
    ONE memory but TWO ProjectMemory links.
  - Markdown splitter honours fenced code blocks (``## heading`` inside a
    fence is body, not a section boundary).
  - Heading keyword matching uses full-word tokens for contractions so
    "Things the parser doesn't do" is NOT classified as anti-pattern.
"""

from __future__ import annotations

from pathlib import Path

from memee.storage.database import get_session, init_db
from memee.storage.models import (
    Memory,
    MemoryType,
    Project,
    ProjectMemory,
)
from memee.sync.claudemd import _split_sections, sync_claudemd


# ── Splitter ──


def test_split_sections_ignores_fenced_code_blocks():
    content = (
        "# Real Heading\n"
        "Body before the fence.\n"
        "\n"
        "```python\n"
        "## fake heading inside fence\n"
        "def f(): pass\n"
        "```\n"
        "\n"
        "trailing body\n"
    )
    sections = _split_sections(content)
    # Only one real heading (Real Heading). Root section has no body before
    # the first heading, so it may or may not appear. The key assertion:
    # "fake heading inside fence" must NOT appear as a section heading.
    headings = [h for h, _ in sections]
    assert "fake heading inside fence" not in headings
    # At least one section whose body contains the fenced content
    assert any("fake heading inside fence" in body for _, body in sections)


def test_split_sections_allows_tilde_fence():
    content = (
        "# Root\n"
        "~~~\n"
        "## not a heading\n"
        "~~~\n"
    )
    sections = _split_sections(content)
    headings = [h for h, _ in sections]
    assert "not a heading" not in headings


# ── Heading misclassification ──


def test_heading_with_doesnt_is_not_antipattern(tmp_path, monkeypatch):
    """``## Things the parser doesn't do`` historically triggered the
    anti-pattern branch via substring match on "don't". Full-word token
    matching fixes this.
    """
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# Project X\n"
        "\n"
        "## Things the parser doesn't do\n"
        "\n"
        "- Parse binary files\n"
        "- Handle network I/O\n",
        encoding="utf-8",
    )

    engine = init_db()
    session = get_session(engine)
    # Clean any prior anti-patterns for this exact content
    session.query(Memory).filter(
        Memory.title.in_(["Parse binary files", "Handle network I/O"])
    ).delete(synchronize_session=False)
    session.commit()

    stats = sync_claudemd(str(tmp_path))
    # No anti-patterns should be extracted
    assert stats.get("anti_patterns", 0) == 0

    # And no memories with those titles were created as anti-patterns
    ap_titles = [
        m.title
        for m in session.query(Memory)
        .filter(
            Memory.title.in_(["Parse binary files", "Handle network I/O"]),
            Memory.type == MemoryType.ANTI_PATTERN.value,
        )
        .all()
    ]
    assert ap_titles == []


# ── Dedup + cross-project link ──


def test_same_bullet_imported_into_two_projects_links_both(tmp_path):
    """Import the same CLAUDE.md content into two projects. Expect ONE Memory
    but TWO ProjectMemory rows — one per project.
    """
    shared_title = "Use timeout on HTTP calls — regression-unique-xyz-42"

    # Project A
    proj_a_dir = tmp_path / "proj_a"
    proj_a_dir.mkdir()
    (proj_a_dir / "CLAUDE.md").write_text(
        "# A\n"
        "\n"
        "## Pitfalls\n"
        "\n"
        f"- {shared_title}\n",
        encoding="utf-8",
    )

    # Project B — same content
    proj_b_dir = tmp_path / "proj_b"
    proj_b_dir.mkdir()
    (proj_b_dir / "CLAUDE.md").write_text(
        "# B\n"
        "\n"
        "## Pitfalls\n"
        "\n"
        f"- {shared_title}\n",
        encoding="utf-8",
    )

    engine = init_db()
    session = get_session(engine)

    # Register both projects so sync_claudemd finds them by absolute path.
    from memee.storage.models import Organization

    org = session.query(Organization).first()
    if org is None:
        org = Organization(name="claudemd-test-org")
        session.add(org)
        session.flush()

    abs_a = str(Path(proj_a_dir).resolve())
    abs_b = str(Path(proj_b_dir).resolve())
    pa = Project(organization_id=org.id, name="proj-a", path=abs_a)
    pb = Project(organization_id=org.id, name="proj-b", path=abs_b)
    session.add_all([pa, pb])
    session.commit()
    pa_id = pa.id
    pb_id = pb.id

    # Import from both projects
    sync_claudemd(str(proj_a_dir))
    sync_claudemd(str(proj_b_dir))

    # Reopen session to observe committed state
    session.close()
    session = get_session(engine)

    memories = (
        session.query(Memory)
        .filter_by(title=shared_title, type=MemoryType.ANTI_PATTERN.value)
        .all()
    )
    assert len(memories) == 1, (
        f"Expected exactly one memory for the shared bullet, got {len(memories)}"
    )
    memory_id = memories[0].id

    links = (
        session.query(ProjectMemory)
        .filter_by(memory_id=memory_id)
        .all()
    )
    linked_project_ids = {link.project_id for link in links}
    assert pa_id in linked_project_ids
    assert pb_id in linked_project_ids
    assert len(linked_project_ids) == 2
