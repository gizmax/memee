"""Tests for the v2.2.0 first-week-no-silence onboarding arc (M2).

Covers:
- Marker is written after setup
- Marker is per-project, not global
- Stage 1 message when no memories exist
- Stage 2 advances when a memory is recorded
- Stage 3 advances when a reuse is recorded
- Arc ends after stage 3 fires
- Arc ends after 7 days (expiry)
- ``MEMEE_NO_ONBOARDING=1`` kill switch
- LRU cap at 50 projects
- Corrupt marker is treated as "no onboarding"
- ``is_onboarding_active`` semantics within and after the window
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from memee.engine.impact import ImpactEvent, ImpactType
from memee.storage.database import get_engine, get_session, init_db
from memee.storage.models import (
    MaturityLevel,
    Memory,
    MemoryType,
    Organization,
)


# ── Test fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Repoint HOME + the onboarding marker + the DB at a per-test tmp dir."""
    home = tmp_path / "home"
    home.mkdir()

    # Point Memee's settings at a tmp DB.
    db_path = home / ".memee" / "memee.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))

    # Make sure the kill switch isn't inherited.
    monkeypatch.delenv("MEMEE_NO_ONBOARDING", raising=False)

    # Reload memee.config so the new env var sticks for ``get_engine()``.
    import importlib

    from memee import config as memee_config
    importlib.reload(memee_config)

    # Repoint the marker file.
    from memee import onboarding as onboarding_module
    marker_path = home / ".memee" / "onboarding.json"
    monkeypatch.setattr(onboarding_module, "MARKER_PATH", marker_path)

    # Initialize the DB so callers can immediately seed rows.
    init_db(get_engine(db_path))

    return {
        "home": home,
        "db_path": db_path,
        "marker_path": marker_path,
        "onboarding": onboarding_module,
    }


def _seed_org(session) -> Organization:
    org = Organization(name="test-org")
    session.add(org)
    session.commit()
    return org


def _seed_memory(session, org: Organization, *, title: str) -> Memory:
    mem = Memory(
        title=title,
        content=f"content for {title}",
        type=MemoryType.PATTERN.value,
        organization_id=org.id,
        maturity=MaturityLevel.HYPOTHESIS.value,
        confidence_score=0.5,
    )
    session.add(mem)
    session.commit()
    return mem


def _seed_reuse_event(session, memory_id: str) -> ImpactEvent:
    event = ImpactEvent(
        memory_id=memory_id,
        impact_type=ImpactType.KNOWLEDGE_REUSED.value,
    )
    session.add(event)
    session.commit()
    return event


def _read_marker_file(path) -> dict:
    return json.loads(path.read_text())


def _write_marker_file(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# ── 1. Setup writes the marker ─────────────────────────────────────────────


def test_marker_written_after_setup(isolated_home, tmp_path):
    """``mark_setup_complete`` writes a per-project entry with three
    null timestamps and ``completed=false``."""
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "myproj"
    project.mkdir()

    onboarding.mark_setup_complete(str(project))

    assert isolated_home["marker_path"].exists()
    data = _read_marker_file(isolated_home["marker_path"])
    assert data["version"] == "2.2.0"
    assert "setup_at" in data
    assert isinstance(data["by_project"], dict)

    abs_path = str(project.resolve())
    assert abs_path in data["by_project"]
    entry = data["by_project"][abs_path]
    assert entry["first_memory_seen"] is None
    assert entry["first_reuse_seen"] is None
    assert entry["completed"] is False
    assert "setup_at" in entry


# ── 2. Per-project keys, not global ────────────────────────────────────────


def test_marker_per_project_not_global(isolated_home, tmp_path):
    """A consultant working across repos sees the arc once *per repo*."""
    onboarding = isolated_home["onboarding"]
    proj_a = tmp_path / "client_a"
    proj_b = tmp_path / "client_b"
    proj_a.mkdir()
    proj_b.mkdir()

    onboarding.mark_setup_complete(str(proj_a))
    onboarding.mark_setup_complete(str(proj_b))

    data = _read_marker_file(isolated_home["marker_path"])
    keys = set(data["by_project"].keys())
    assert str(proj_a.resolve()) in keys
    assert str(proj_b.resolve()) in keys
    assert len(keys) == 2

    # Advancing project A's marker mustn't touch project B.
    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    _seed_memory(session, org, title="A pattern about retries")
    session.close()

    msg_a = onboarding.format_onboarding_notice(str(proj_a))
    assert msg_a is not None
    assert 'learned' in msg_a

    data_after = _read_marker_file(isolated_home["marker_path"])
    entry_a = data_after["by_project"][str(proj_a.resolve())]
    entry_b = data_after["by_project"][str(proj_b.resolve())]
    assert entry_a["first_memory_seen"] is not None
    # Project B is independent — its first_memory_seen still null until
    # someone reads its notice.
    assert entry_b["first_memory_seen"] is None


# ── 3. Stage 1 ─────────────────────────────────────────────────────────────


def test_stage_1_message_when_no_memories(isolated_home, tmp_path):
    """Fresh marker + empty DB → stage 1 message."""
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "proj"
    project.mkdir()
    onboarding.mark_setup_complete(str(project))

    msg = onboarding.format_onboarding_notice(str(project))
    assert msg == "Memee is listening. No memories yet."

    # Stage 1 reads don't advance state when DB is empty.
    data = _read_marker_file(isolated_home["marker_path"])
    entry = data["by_project"][str(project.resolve())]
    assert entry["first_memory_seen"] is None


# ── 4. Stage 2 ─────────────────────────────────────────────────────────────


def test_stage_2_advances_when_memory_appears(isolated_home, tmp_path):
    """Stage 1 read + memory exists in DB → atomic transition to stage 2."""
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "proj"
    project.mkdir()
    onboarding.mark_setup_complete(str(project))

    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    _seed_memory(session, org, title="Connection pooling beats reconnect")
    session.close()

    msg = onboarding.format_onboarding_notice(str(project))
    assert msg is not None
    assert 'learned "Connection pooling beats reconnect"' in msg
    assert "from this session" in msg

    data = _read_marker_file(isolated_home["marker_path"])
    entry = data["by_project"][str(project.resolve())]
    assert entry["first_memory_seen"] is not None
    assert entry["first_reuse_seen"] is None
    assert entry["completed"] is False


# ── 5. Stage 3 ─────────────────────────────────────────────────────────────


def test_stage_3_advances_when_reuse_appears(isolated_home, tmp_path):
    """Stage 2 → stage 3: KNOWLEDGE_REUSED event lands, render the
    reuse message and persist ``first_reuse_seen``."""
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "proj"
    project.mkdir()
    onboarding.mark_setup_complete(str(project))

    # Force the entry into stage 2 directly so we don't conflate
    # transitions.
    data = _read_marker_file(isolated_home["marker_path"])
    abs_path = str(project.resolve())
    data["by_project"][abs_path]["first_memory_seen"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()
    _write_marker_file(isolated_home["marker_path"], data)

    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    mem = _seed_memory(session, org, title="Always set requests timeout")
    _seed_reuse_event(session, mem.id)
    session.close()

    msg = onboarding.format_onboarding_notice(str(project))
    assert msg is not None
    assert 'reused "Always set requests timeout"' in msg
    assert "saved you a re-explain" in msg

    data_after = _read_marker_file(isolated_home["marker_path"])
    entry = data_after["by_project"][abs_path]
    assert entry["first_reuse_seen"] is not None
    # Stage 3 fires once. ``completed`` flips on the *next* read.
    assert entry["completed"] is False


# ── 6. Arc ends after stage 3 ──────────────────────────────────────────────


def test_arc_ends_after_stage_3(isolated_home, tmp_path):
    """After stage 3 fires, the next call returns None and flips
    ``completed=true``. No receipt #4 ever."""
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "proj"
    project.mkdir()
    onboarding.mark_setup_complete(str(project))

    abs_path = str(project.resolve())
    now = datetime.now(timezone.utc)
    data = _read_marker_file(isolated_home["marker_path"])
    data["by_project"][abs_path]["first_memory_seen"] = (
        now - timedelta(days=2)
    ).isoformat()
    data["by_project"][abs_path]["first_reuse_seen"] = (
        now - timedelta(hours=1)
    ).isoformat()
    _write_marker_file(isolated_home["marker_path"], data)

    msg = onboarding.format_onboarding_notice(str(project))
    assert msg is None

    data_after = _read_marker_file(isolated_home["marker_path"])
    assert data_after["by_project"][abs_path]["completed"] is True

    # Even if MORE reuses land, no receipt #4.
    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    mem = _seed_memory(session, org, title="Late memory")
    _seed_reuse_event(session, mem.id)
    session.close()
    assert onboarding.format_onboarding_notice(str(project)) is None


# ── 7. Expiry ──────────────────────────────────────────────────────────────


def test_arc_ends_after_7_days(isolated_home, tmp_path):
    """``setup_at >= 7 days`` ago → return None and mark completed,
    regardless of stage."""
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "proj"
    project.mkdir()
    onboarding.mark_setup_complete(str(project))

    abs_path = str(project.resolve())
    data = _read_marker_file(isolated_home["marker_path"])
    data["by_project"][abs_path]["setup_at"] = (
        datetime.now(timezone.utc) - timedelta(days=8)
    ).isoformat()
    _write_marker_file(isolated_home["marker_path"], data)

    # Even with a fresh memory, expired arc returns nothing.
    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    _seed_memory(session, org, title="A late pattern")
    session.close()

    msg = onboarding.format_onboarding_notice(str(project))
    assert msg is None

    data_after = _read_marker_file(isolated_home["marker_path"])
    assert data_after["by_project"][abs_path]["completed"] is True


# ── 8. Kill switch ─────────────────────────────────────────────────────────


def test_kill_switch_env_var(isolated_home, tmp_path, monkeypatch):
    """``MEMEE_NO_ONBOARDING=1`` short-circuits all three public
    functions before any DB or marker access."""
    monkeypatch.setenv("MEMEE_NO_ONBOARDING", "1")
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "proj"
    project.mkdir()

    # mark_setup_complete is a no-op
    onboarding.mark_setup_complete(str(project))
    assert not isolated_home["marker_path"].exists()

    # format_onboarding_notice returns None
    assert onboarding.format_onboarding_notice(str(project)) is None

    # is_onboarding_active returns False even if a marker existed.
    monkeypatch.delenv("MEMEE_NO_ONBOARDING", raising=False)
    onboarding.mark_setup_complete(str(project))
    assert onboarding.is_onboarding_active(str(project)) is True

    monkeypatch.setenv("MEMEE_NO_ONBOARDING", "1")
    assert onboarding.is_onboarding_active(str(project)) is False


# ── 9. LRU cap ─────────────────────────────────────────────────────────────


def test_marker_lru_capped_at_50_projects(isolated_home, tmp_path):
    """Cap is 50 entries; oldest-by-setup_at evicted on overflow."""
    onboarding = isolated_home["onboarding"]
    # Pre-populate a marker with 50 entries, all dated in the past, so
    # we know which key to expect evicted when we add the 51st.
    by_project = {}
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for i in range(50):
        path = str((tmp_path / f"p{i}").resolve())
        by_project[path] = {
            "setup_at": (base + timedelta(days=i)).isoformat(),
            "first_memory_seen": None,
            "first_reuse_seen": None,
            "completed": False,
        }
    _write_marker_file(
        isolated_home["marker_path"],
        {
            "setup_at": base.isoformat(),
            "version": "2.2.0",
            "by_project": by_project,
        },
    )

    # Now add a 51st via mark_setup_complete — oldest (p0) should evict.
    new_proj = tmp_path / "p_newest"
    new_proj.mkdir()
    onboarding.mark_setup_complete(str(new_proj))

    data = _read_marker_file(isolated_home["marker_path"])
    keys = set(data["by_project"].keys())
    assert len(keys) == 50
    # Newest survived
    assert str(new_proj.resolve()) in keys
    # Oldest evicted
    assert str((tmp_path / "p0").resolve()) not in keys
    # Second-oldest survived
    assert str((tmp_path / "p1").resolve()) in keys


# ── 10. Corrupt marker ─────────────────────────────────────────────────────


def test_corrupt_marker_treated_as_no_onboarding(isolated_home, tmp_path):
    """Garbage JSON / wrong shape → ``format_onboarding_notice`` returns
    None and ``is_onboarding_active`` returns False, no crash."""
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "proj"
    project.mkdir()

    isolated_home["marker_path"].parent.mkdir(parents=True, exist_ok=True)
    isolated_home["marker_path"].write_text("{not valid json")

    assert onboarding.format_onboarding_notice(str(project)) is None
    assert onboarding.is_onboarding_active(str(project)) is False

    # Wrong shape — list instead of dict.
    isolated_home["marker_path"].write_text(json.dumps([1, 2, 3]))
    assert onboarding.format_onboarding_notice(str(project)) is None
    assert onboarding.is_onboarding_active(str(project)) is False

    # Right top-level dict but ``by_project`` missing.
    isolated_home["marker_path"].write_text(
        json.dumps({"version": "2.2.0"})
    )
    assert onboarding.format_onboarding_notice(str(project)) is None
    assert onboarding.is_onboarding_active(str(project)) is False


# ── 11. is_onboarding_active semantics ─────────────────────────────────────


def test_is_onboarding_active_returns_true_within_7_days(
    isolated_home, tmp_path
):
    """Fresh marker, not completed → active."""
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "proj"
    project.mkdir()
    onboarding.mark_setup_complete(str(project))

    assert onboarding.is_onboarding_active(str(project)) is True


def test_is_onboarding_active_returns_false_after_completion(
    isolated_home, tmp_path
):
    """Manually flip ``completed=true`` → no longer active."""
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "proj"
    project.mkdir()
    onboarding.mark_setup_complete(str(project))

    abs_path = str(project.resolve())
    data = _read_marker_file(isolated_home["marker_path"])
    data["by_project"][abs_path]["completed"] = True
    _write_marker_file(isolated_home["marker_path"], data)

    assert onboarding.is_onboarding_active(str(project)) is False


def test_is_onboarding_active_returns_false_after_7_days(
    isolated_home, tmp_path
):
    """Age >= 7 days → not active even if ``completed`` still false."""
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "proj"
    project.mkdir()
    onboarding.mark_setup_complete(str(project))

    abs_path = str(project.resolve())
    data = _read_marker_file(isolated_home["marker_path"])
    data["by_project"][abs_path]["setup_at"] = (
        datetime.now(timezone.utc) - timedelta(days=8)
    ).isoformat()
    _write_marker_file(isolated_home["marker_path"], data)

    assert onboarding.is_onboarding_active(str(project)) is False


def test_is_onboarding_active_returns_false_for_unknown_project(
    isolated_home, tmp_path
):
    """No marker for this project → not active."""
    onboarding = isolated_home["onboarding"]
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    onboarding.mark_setup_complete(str(proj_a))

    assert onboarding.is_onboarding_active(str(proj_a)) is True
    assert onboarding.is_onboarding_active(str(proj_b)) is False


# ── 12. Re-running setup doesn't reset progress ────────────────────────────


def test_re_setup_does_not_reset_progress(isolated_home, tmp_path):
    """Calling ``mark_setup_complete`` twice on the same project does
    NOT zero out ``first_memory_seen``."""
    onboarding = isolated_home["onboarding"]
    project = tmp_path / "proj"
    project.mkdir()
    onboarding.mark_setup_complete(str(project))

    abs_path = str(project.resolve())
    data = _read_marker_file(isolated_home["marker_path"])
    data["by_project"][abs_path]["first_memory_seen"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()
    _write_marker_file(isolated_home["marker_path"], data)

    onboarding.mark_setup_complete(str(project))

    data_after = _read_marker_file(isolated_home["marker_path"])
    entry = data_after["by_project"][abs_path]
    assert entry["first_memory_seen"] is not None
