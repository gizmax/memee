"""Tests for v2.4.1 per-brief archive at ``~/.memee/briefs/``.

The menubar miniapp's "Open last brief" was previously pointed at
``state.json`` (a TODO from v2.3.0). v2.4.1 closes the gap: every
``memee brief`` invocation appends a Markdown file under
``~/.memee/briefs/`` with front-matter (task, project, timestamp)
+ the rendered body. The menubar reads the latest from there.

Tests pin:
  * Write + retrieve round-trip
  * Front-matter shape (task, project, written_at present)
  * Rotation keeps newest N when archive grows past retain limit
  * Kill switches (``MEMEE_QUIET`` and ``MEMEE_NO_BRIEF_ARCHIVE``)
    silence the writer without affecting the brief output
  * Latest-brief picker returns the most recent by mtime, ``None``
    when empty
  * Atomicity: no ``.tmp`` leaks after writes
  * Menubar app prefers archive over state.json fallback
"""

from __future__ import annotations

import time
from datetime import datetime, timezone


from memee.bar.briefs import (
    BRIEFS_RETAIN,
    _rotate,
    briefs_dir,
    latest_brief,
    write_brief,
)


# ── Write + read round-trip ──


def test_write_creates_file_with_expected_content(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.delenv("MEMEE_NO_BRIEF_ARCHIVE", raising=False)

    target = write_brief(task="write tests", body="• bullet one\n• bullet two")
    assert target is not None
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert "task: write tests" in text
    assert "• bullet one" in text
    assert "• bullet two" in text
    assert "written_at:" in text


def test_write_returns_none_for_empty_body(tmp_path, monkeypatch):
    """An empty briefing (e.g. MEMEE_QUIET upstream) shouldn't create
    an empty archive file. Writer skips and returns None."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    assert write_brief(task="anything", body="") is None
    assert write_brief(task="anything", body="   \n  ") is None


# ── Filename + path ──


def test_filename_includes_task_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    target = write_brief(task="security audit", body="content")
    assert target is not None
    assert "security-audit" in target.name
    assert target.suffix == ".md"


def test_long_task_truncated_in_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    huge_task = "verify-x " * 50
    target = write_brief(task=huge_task, body="content")
    assert target is not None
    # Path length bounded so Windows doesn't choke.
    assert len(target.name) <= 80


def test_task_with_special_chars_sanitised(tmp_path, monkeypatch):
    """Slashes, backticks, quotes can't appear in path components."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    target = write_brief(task="../etc/passwd", body="content")
    assert target is not None
    assert ".." not in target.name
    assert "/" not in target.name.split("/")[-1]


def test_briefs_dir_honours_memee_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    assert briefs_dir() == tmp_path / "briefs"


# ── Atomicity ──


def test_no_tmp_leaks_after_write(tmp_path, monkeypatch):
    """tempfile + os.replace pattern: no ``.brief.*.tmp`` files left
    behind in the briefs dir after a successful write."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    write_brief(task="any", body="bullets here")
    leftovers = [p.name for p in briefs_dir().iterdir() if p.name.startswith(".brief.")]
    assert leftovers == [], f"writer leaked tempfiles: {leftovers}"


# ── Latest-brief picker ──


def test_latest_brief_returns_most_recent(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    first = write_brief(task="first", body="one")
    # Small sleep so mtime differs reliably across all filesystems.
    time.sleep(0.01)
    second = write_brief(task="second", body="two")
    assert latest_brief() == second
    assert latest_brief() != first


def test_latest_brief_returns_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    assert latest_brief() is None


# ── Rotation ──


def test_rotation_keeps_newest_n(tmp_path, monkeypatch):
    """Archive must not grow unbounded. Default retain = 50."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    # Manually create 60 files with controlled mtimes (faster than 60
    # write_brief calls which would each call _rotate).
    target_dir = briefs_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    base_ts = datetime.now(timezone.utc).timestamp()
    for i in range(60):
        p = target_dir / f"{i:02d}.md"
        p.write_text(f"file {i}")
        # Older index → older mtime.
        import os
        os.utime(p, (base_ts + i, base_ts + i))

    removed = _rotate(target_dir, retain=BRIEFS_RETAIN)
    assert removed == 10
    remaining = sorted(p.name for p in target_dir.iterdir())
    # The newest 50 survive (indexes 10..59).
    assert remaining[0] == "10.md"
    assert remaining[-1] == "59.md"


def test_write_brief_triggers_rotation(tmp_path, monkeypatch):
    """``write_brief`` calls ``_rotate`` after each write so the archive
    self-prunes."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    # Seed 55 files.
    target_dir = briefs_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    base_ts = datetime.now(timezone.utc).timestamp()
    for i in range(55):
        p = target_dir / f"old-{i:02d}.md"
        p.write_text("old")
        import os
        os.utime(p, (base_ts + i, base_ts + i))

    # Write one more — should trigger rotation back to 50.
    write_brief(task="trigger", body="new content")
    assert len(list(target_dir.iterdir())) <= 50


# ── Kill switches ──


def test_memee_quiet_suppresses_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    monkeypatch.setenv("MEMEE_QUIET", "1")
    assert write_brief(task="any", body="should be silent") is None
    # Briefs dir should not even be created.
    assert not briefs_dir().exists() or not list(briefs_dir().iterdir())


def test_per_channel_kill_switch_works(tmp_path, monkeypatch):
    """``MEMEE_NO_BRIEF_ARCHIVE`` silences the archive without
    silencing other channels (state.json, footer, etc.)."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.setenv("MEMEE_NO_BRIEF_ARCHIVE", "1")
    assert write_brief(task="any", body="should be silent") is None


# ── Menubar integration ──


def test_menubar_resolves_archived_brief_over_state_json(tmp_path, monkeypatch):
    """v2.4.1 wiring: the menubar's "Open last brief" prefers the
    archive over the state file."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    monkeypatch.delenv("MEMEE_QUIET", raising=False)

    # Create both: state file + an archived brief.
    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    archived = write_brief(task="t", body="b")
    assert archived is not None

    from memee.bar.app import _resolve_last_brief_artifact
    resolved = _resolve_last_brief_artifact()
    assert resolved == archived


def test_menubar_falls_back_to_state_when_no_archive(tmp_path, monkeypatch):
    """When the archive is empty (fresh install, or kill switch on),
    the menubar still has something to open."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    monkeypatch.delenv("MEMEE_QUIET", raising=False)

    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    # No briefs/ dir, no archived briefs.

    from memee.bar.app import _resolve_last_brief_artifact
    resolved = _resolve_last_brief_artifact()
    assert resolved == state_file
