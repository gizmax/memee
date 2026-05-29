"""v2.4.14 — MemoryTag index stays in sync with Memory.tags JSON.

Pre-v2.4.14 ``cli.record`` and the MCP ``memory_record`` / ``decision_record``
/ ``antipattern_record`` tools wrote ``Memory.tags`` (JSON) without populating
the ``MemoryTag`` index. The router's pinned-policy "global" branch used
``not exists MemoryTag`` to mean "global policy" — so any row with a
populated tags JSON but no MemoryTag entry was leaked into every task,
regardless of stack.

Two layers of defence here:
  * write side: every Memory insert now calls ``sync_memory_tags`` so the
    index is never silently stale.
  * read side: the router's "global" branch now reads ``Memory.tags`` JSON
    directly (the source of truth) instead of ``not exists MemoryTag``.
"""

from __future__ import annotations

from click.testing import CliRunner

from memee.cli import cli
from memee.engine.router import smart_briefing
from memee.storage.models import (
    MaturityLevel,
    Memory,
    MemoryTag,
    MemoryType,
)


# ── Router read-side defence ──


def _mk_authoritative(session, *, title, tags, conf=0.9):
    m = Memory(
        type=MemoryType.PATTERN.value,
        title=title,
        content=title,
        tags=tags,
        is_authoritative=True,
        confidence_score=conf,
        maturity=MaturityLevel.VALIDATED.value,
    )
    session.add(m)
    session.flush()
    return m


def test_router_global_branch_ignores_stale_index_rows(session, org):
    """A pinned row with populated tags JSON but EMPTY MemoryTag must NOT
    be treated as a global policy. This is the exact leak the reviewer
    reproduced: a python-tagged pin showing up on a react task."""
    pin = _mk_authoritative(session, title="Python-only policy", tags=["python"])
    # Deliberately leave MemoryTag empty (the pre-v2.4.14 write-side bug
    # state). The router must NOT surface this row as a global pinned
    # policy for an unrelated task.
    session.commit()
    assert session.query(MemoryTag).filter_by(memory_id=pin.id).count() == 0

    out = smart_briefing(session, task="react component state", token_budget=500)
    assert "Python-only policy" not in out


def test_router_global_branch_surfaces_truly_global_rows(session, org):
    """A pinned row with an explicitly empty tags JSON IS a global
    policy and must surface regardless of task."""
    global_pin = _mk_authoritative(session, title="Org-wide rule", tags=[])
    session.commit()

    out = smart_briefing(session, task="anything at all", token_budget=500)
    assert "Org-wide rule" in out


def test_router_overlap_branch_still_matches_when_index_synced(session, org):
    """Sanity: when MemoryTag IS populated, the overlap branch still hits."""
    from memee.engine.tag_index import sync_memory_tags

    pin = _mk_authoritative(session, title="React-only policy", tags=["react"])
    sync_memory_tags(session, pin)
    session.commit()
    assert session.query(MemoryTag).filter_by(memory_id=pin.id).count() == 1

    out = smart_briefing(session, task="react component state", token_budget=500)
    assert "React-only policy" in out


# ── Write-side: every entry point syncs the index ──


def test_cli_record_syncs_memory_tag_index(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path / ".memee"))
    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(
        cli,
        [
            "record", "pattern", "Use connection pooling",
            "-c", "Pool connections to avoid exhaustion in production.",
            "--tags", "python,database",
        ],
    )
    assert result.exit_code == 0, result.output

    from memee.storage.database import get_session, init_db
    session = get_session(init_db())
    tags = {row.tag for row in session.query(MemoryTag).all()}
    assert "python" in tags
    assert "database" in tags


def test_reindex_tags_repairs_stale_install(tmp_path, monkeypatch):
    """``memee reindex-tags`` rebuilds the MemoryTag index from JSON
    columns, fixing a pre-v2.4.14 install where every recorded row left
    the index empty."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path / ".memee"))
    runner = CliRunner()
    runner.invoke(cli, ["init"])

    from memee.storage.database import get_session, init_db
    engine = init_db()
    session = get_session(engine)

    # Simulate a stale install: insert a Memory with tags but no MemoryTag
    # rows, the way pre-fix `record` did.
    m = Memory(
        type=MemoryType.PATTERN.value,
        title="Stale row",
        content="Imported pre-v2.4.14, tags JSON populated, index empty",
        tags=["python", "api"],
    )
    session.add(m)
    session.commit()
    assert session.query(MemoryTag).filter_by(memory_id=m.id).count() == 0
    session.close()

    result = runner.invoke(cli, ["reindex-tags"])
    assert result.exit_code == 0, result.output
    assert "memory_tags" in result.output

    session = get_session(engine)
    tags = {row.tag for row in session.query(MemoryTag).filter_by(memory_id=m.id).all()}
    assert tags == {"python", "api"}
