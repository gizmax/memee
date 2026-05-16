"""Tests for the v2.3.0 menubar miniapp.

Focus on the pure / file-IO parts. The actual rumps run loop is not
exercised — it requires the macOS GUI event loop and would block the
suite. The app's structure is built so all logic worth testing is in
``state.py``, ``doctor.py``, ``launcher.py``, plus the
``render_menu_strings`` pure helper in ``app.py``.

Skipped on non-darwin platforms only where macOS-specific behaviour is
under test (launcher's plist + launchctl interactions). The rest of
the suite (state, doctor logic except platform check, popover render)
runs everywhere.
"""

from __future__ import annotations

import json
import sys

import pytest


# ── State file: read / write / atomic / update ──


def test_state_path_honours_memee_home(tmp_path, monkeypatch):
    """``MEMEE_HOME`` overrides the default ``~/.memee``."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.state import state_path

    assert state_path() == tmp_path / "state.json"


def test_read_state_returns_empty_dict_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.state import read_state

    assert read_state() == {}


def test_read_state_returns_empty_on_corrupt_json(tmp_path, monkeypatch):
    """A half-written or hand-mangled state file must NEVER raise.

    The miniapp keeps rendering even if the file is mid-rewrite or
    truncated by power loss between write and replace."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    target = tmp_path / "state.json"
    target.write_text("{not: valid: json::}", encoding="utf-8")
    from memee.bar.state import read_state

    assert read_state() == {}


def test_write_state_is_atomic_via_replace(tmp_path, monkeypatch):
    """Writer should use a tempfile + ``os.replace`` so readers never
    see a half-written file. We can't time-slice a real race in pytest,
    but we can verify the *no tempfile leaks* invariant after writes."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.state import write_state, read_state

    assert write_state({"hello": "world"})
    assert read_state()["hello"] == "world"
    # No `.state.*.tmp` leftovers in the dir.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".state.")]
    assert leftovers == [], f"writer leaked tempfiles: {leftovers}"


def test_write_state_stamps_version_and_updated_at(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.state import write_state, read_state

    write_state({"x": 1})
    state = read_state()
    assert "version" in state
    assert "updated_at" in state
    # ISO-8601 with a timezone suffix (the writer uses UTC).
    assert state["updated_at"].endswith("+00:00") or "T" in state["updated_at"]


def test_update_state_merges_top_level_keys(tmp_path, monkeypatch):
    """Top-level keys in the patch replace same-name keys; other keys
    survive. The whole-object semantics matters for ``last_brief`` /
    ``last_learn`` which the hooks set as one unit."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.state import update_state, read_state

    update_state({"last_brief": {"task": "first"}})
    update_state({"totals": {"canon": 5}})
    state = read_state()
    assert state["last_brief"] == {"task": "first"}
    assert state["totals"] == {"canon": 5}


def test_record_brief_caps_long_task_strings(tmp_path, monkeypatch):
    """A 1000-char task description should not balloon the state file.
    The popover only shows the first ~40 chars anyway; we cap at 120
    in the writer to keep the file readable in `cat`."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.state import record_brief, read_state

    huge_task = "x" * 1000
    record_brief(project="/p", task=huge_task, bullets=3, tokens=100)
    stored = read_state()["last_brief"]["task"]
    assert len(stored) == 120
    assert stored == "x" * 120


def test_record_brief_handles_none_project(tmp_path, monkeypatch):
    """Project resolution can return None (no cwd, fresh worktree). The
    writer must handle that without raising — the field becomes ""."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.state import record_brief, read_state

    record_brief(project=None, task="any", bullets=0, tokens=0)
    assert read_state()["last_brief"]["project"] == ""


# ── Popover rendering (pure function) ──


def test_render_menu_strings_empty_state():
    """Fresh DB / hooks haven't fired yet: every label has a sensible
    placeholder, none of them are tracebacks or KeyErrors."""
    from memee.bar.app import render_menu_strings

    out = render_menu_strings({})
    assert out["status"].startswith("●")
    assert "standby" in out["status"]
    assert out["last_brief"] == "Last brief: —"
    assert out["last_learn"] == "Last learn: —"
    assert "memee.db empty" in out["totals"]


def test_render_menu_strings_with_full_state():
    from memee.bar.app import render_menu_strings

    state = {
        "last_brief": {
            "ts": "2026-05-14T13:00:00+00:00",
            "project": "/some/path",
            "task": "write unit tests",
            "bullets": 7, "tokens": 442,
        },
        "last_learn": {
            "ts": "2026-05-14T13:01:00+00:00",
            "outcome": "success",
            "auto": True,
        },
        "totals": {
            "memories": 61, "canon": 13, "validated": 46,
            "critical_warnings": 3,
        },
    }
    out = render_menu_strings(state)
    assert "active" in out["status"]
    assert "write unit tests" in out["last_brief"]
    assert "success" in out["last_learn"]
    assert "13 canon" in out["totals"]
    assert "3 critical" in out["totals"]


def test_render_menu_strings_handles_broken_timestamp():
    """Garbage ts → "—", not a crash."""
    from memee.bar.app import render_menu_strings

    state = {"last_brief": {"ts": "not-a-date", "task": "x"}}
    out = render_menu_strings(state)
    assert "—" in out["last_brief"]


def test_relative_time_rounds_to_human_units():
    from memee.bar.app import _relative_time
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    iso = lambda dt: dt.isoformat()  # noqa: E731

    assert _relative_time(iso(now - timedelta(seconds=3))).endswith("s ago")
    assert _relative_time(iso(now - timedelta(minutes=5))).endswith("m ago")
    assert _relative_time(iso(now - timedelta(hours=2))).endswith("h ago")
    assert _relative_time(iso(now - timedelta(days=4))).endswith("d ago")
    # Future timestamp (clock skew) collapses to "just now" not a negative.
    assert _relative_time(iso(now + timedelta(seconds=30))) == "just now"


# ── Doctor diagnostics ──


def test_doctor_returns_ok_on_macos_with_deps(tmp_path, monkeypatch):
    """Sanity: when platform=macOS, rumps importable, watchdog
    importable, the doctor reports ok=True (state file missing is
    expected-and-not-fatal)."""
    if sys.platform != "darwin":
        pytest.skip("doctor's ok-flag is only meaningful on macOS")
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.doctor import diagnose

    report = diagnose()
    # Lines list always non-empty; ok depends on environment.
    assert report["lines"]
    assert report["ok"] is True


def test_doctor_reports_state_file_when_present(tmp_path, monkeypatch):
    """A real state file → one of the lines mentions it's present."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.state import write_state
    from memee.bar.doctor import diagnose

    write_state({"last_brief": {"task": "x"}})
    report = diagnose()
    text = "\n".join(report["lines"])
    assert "state file" in text
    assert "fresh" in text or "recent" in text


def test_doctor_flags_stale_state_file(tmp_path, monkeypatch):
    """A state file that hasn't been updated in >1d shows a warning
    glyph + a note about hooks not firing."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.doctor import diagnose

    # Write a state file with a 2-day-old timestamp manually.
    target = tmp_path / "state.json"
    target.write_text(json.dumps({
        "updated_at": "2026-05-12T00:00:00+00:00",
        "version": "2.3.0",
    }))
    report = diagnose()
    text = "\n".join(report["lines"])
    assert "stale" in text


# ── LaunchAgent (macOS-only) ──


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only path")
def test_build_plist_bytes_contains_program_arguments(monkeypatch, tmp_path):
    """The plist must list `memee bar start` as the program. Without
    this the LaunchAgent would either fail to load or run something
    unintended."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.launcher import build_plist_bytes
    import plistlib

    parsed = plistlib.loads(build_plist_bytes())
    assert parsed["Label"] == "cz.memee.bar"
    args = parsed["ProgramArguments"]
    assert args[-2:] == ["bar", "start"]
    assert parsed["KeepAlive"] is True
    assert parsed["RunAtLoad"] is True


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only path")
def test_uninstall_when_nothing_installed_is_a_noop(monkeypatch, tmp_path):
    """Idempotency: uninstall on a clean machine returns ok=True without
    touching anything."""
    fake_home = tmp_path / "Home"
    (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    from memee.bar.launcher import uninstall_launch_agent, plist_path

    result = uninstall_launch_agent()
    assert result.ok is True
    assert not plist_path().exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only path")
def test_install_writes_plist(monkeypatch, tmp_path):
    """Install (without actually loading via launchctl) should write the
    plist. We monkey-patch launchctl to a no-op so we can test the write
    leg without polluting the user's real launchctl state."""
    fake_home = tmp_path / "Home"
    (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    from memee.bar import launcher

    def _noop_launchctl(*args):
        return 0, ""

    monkeypatch.setattr(launcher, "_launchctl", _noop_launchctl)

    result = launcher.install_launch_agent()
    assert result.ok is True
    plist = launcher.plist_path()
    assert plist.exists()
    # And the contents are the bytes our builder produced.
    import plistlib
    parsed = plistlib.loads(plist.read_bytes())
    assert parsed["Label"] == "cz.memee.bar"


# ── State writer wired into CLI ──


def test_brief_command_writes_state_file(tmp_path, monkeypatch):
    """Smoke check: running `memee brief --task X` updates state.json so
    the menubar can reflect it. This wires the v2.3.0 plumbing through
    the existing CLI."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    # Make sure state file doesn't exist beforehand.
    state_target = tmp_path / "state.json"
    assert not state_target.exists()

    from memee import config as cfg
    cfg.settings = cfg.Settings(db_path=db_path)

    from click.testing import CliRunner
    from memee.cli import cli

    runner = CliRunner()
    init_result = runner.invoke(cli, ["init"])
    assert init_result.exit_code == 0

    brief_result = runner.invoke(
        cli, ["brief", "--task", "write tests", "--budget", "200"]
    )
    assert brief_result.exit_code == 0
    assert state_target.exists(), "memee brief did not write state.json"
    data = json.loads(state_target.read_text())
    assert "last_brief" in data
    assert data["last_brief"]["task"] == "write tests"
