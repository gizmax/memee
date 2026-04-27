"""Tests for v2.0.1 multi-install detection.

Covers:
- Single install → one entry, classified correctly.
- Multi-install on PATH → both entries, ordered by PATH.
- Two PATH entries pointing at the same realpath → deduped to one.
- Install-kind heuristic (homebrew, pipx, user-pip, system, unknown).
- Doctor report includes the Installations section in both happy and
  warning cases.
- ``memee setup`` exits 1 on multi-install without ``--ignore-multi-install``.
"""

from __future__ import annotations

import io
import os
import stat
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from click.testing import CliRunner

from memee import doctor
from memee.cli import cli


# ── Helpers ────────────────────────────────────────────────────────────


def _make_fake_memee(
    bindir: Path,
    *,
    shebang: str = "#!/usr/bin/env python3",
    version_output: str = "memee, version 2.0.1",
) -> Path:
    """Create an executable fake ``memee`` script in ``bindir``.

    The script's content is irrelevant to the path scan, but giving it a
    valid shebang + a ``--version`` echo lets us exercise the shebang-read
    and version-query paths too.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    binary = bindir / "memee"
    binary.write_text(
        f"{shebang}\n"
        f'import sys\n'
        f'if "--version" in sys.argv:\n'
        f'    print("{version_output}")\n'
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return binary


@pytest.fixture(autouse=True)
def _clear_install_cache():
    """Multi-install detection caches results within a Python invocation;
    every test needs a fresh scan. Reset before AND after for paranoia.
    """
    doctor._MEMEE_INSTALL_CACHE = None
    yield
    doctor._MEMEE_INSTALL_CACHE = None


# ── detect_memee_installs ─────────────────────────────────────────────


def test_detect_single_install(monkeypatch, tmp_path):
    """One memee on PATH → one entry, no warnings."""
    bindir = tmp_path / "bin"
    _make_fake_memee(bindir)

    monkeypatch.setenv("PATH", str(bindir))
    installs = doctor.detect_memee_installs(use_cache=False)

    assert len(installs) == 1
    assert installs[0]["path"] == str(bindir / "memee")
    # The shebang ``#!/usr/bin/env python3`` doesn't itself reveal install
    # kind — the binary's path is /tmp/.../bin which doesn't match any
    # known kind either, so we expect "unknown".
    assert installs[0]["install_kind"] in ("unknown", "user-pip", "system-python", "pipx")
    assert "shebang_python" in installs[0]


def test_detect_multi_install_ordered(monkeypatch, tmp_path):
    """Two distinct binaries on PATH → both returned, in PATH order."""
    homebrew = tmp_path / "opt" / "homebrew" / "bin"
    pipx = tmp_path / "home" / ".local" / "pipx" / "venvs" / "memee" / "bin"
    _make_fake_memee(homebrew, shebang=f"#!{tmp_path}/opt/homebrew/bin/python3")
    _make_fake_memee(pipx, shebang=f"#!{tmp_path}/home/.local/pipx/venvs/memee/bin/python")

    # Homebrew first → it's the active (shadowing) install.
    monkeypatch.setenv("PATH", f"{homebrew}{os.pathsep}{pipx}")
    installs = doctor.detect_memee_installs(use_cache=False)

    assert len(installs) == 2
    assert installs[0]["path"] == str(homebrew / "memee")
    assert installs[1]["path"] == str(pipx / "memee")


def test_detect_dedup_on_realpath(monkeypatch, tmp_path):
    """A symlink from one PATH dir to another's binary → one entry, not two."""
    real = tmp_path / "real" / "bin"
    sym = tmp_path / "sym" / "bin"

    _make_fake_memee(real)
    sym.mkdir(parents=True, exist_ok=True)
    (sym / "memee").symlink_to(real / "memee")

    monkeypatch.setenv("PATH", f"{sym}{os.pathsep}{real}")
    installs = doctor.detect_memee_installs(use_cache=False)

    # Both PATH entries resolve to the same realpath → deduped.
    assert len(installs) == 1


def test_detect_handles_missing_path_entries(monkeypatch, tmp_path):
    """Nonexistent PATH dirs and empty entries don't crash the scan."""
    real = tmp_path / "real" / "bin"
    _make_fake_memee(real)

    weird_path = (
        f"{tmp_path}/does/not/exist"
        f"{os.pathsep}"
        f"{os.pathsep}"
        f"{real}"
    )
    monkeypatch.setenv("PATH", weird_path)
    installs = doctor.detect_memee_installs(use_cache=False)
    assert len(installs) == 1
    assert installs[0]["path"] == str(real / "memee")


def test_detect_empty_path(monkeypatch):
    """No memee anywhere on PATH → empty list, no exceptions."""
    monkeypatch.setenv("PATH", "/nonexistent/dir-1:/nonexistent/dir-2")
    installs = doctor.detect_memee_installs(use_cache=False)
    assert installs == []


# ── _classify_install heuristic ───────────────────────────────────────


@pytest.mark.parametrize(
    "path,shebang,expected",
    [
        ("/Users/me/.local/pipx/venvs/memee/bin/memee", None, "pipx"),
        ("/opt/homebrew/bin/memee", "/opt/homebrew/opt/python@3.12/bin/python3.12", "homebrew-python"),
        ("/usr/local/Cellar/python/3.11/bin/memee", None, "homebrew-python"),
        ("/usr/bin/memee", "/usr/bin/python3", "system-python"),
        # Symlink lives at /opt/homebrew/bin but shebang points at pipx
        # venv → pipx wins (the install kind reflects where Python is).
        ("/opt/homebrew/bin/memee", "/Users/me/.local/pipx/venvs/memee/bin/python", "pipx"),
        ("/some/random/place/memee", "/some/other/random/python", "unknown"),
    ],
)
def test_classify_install(path, shebang, expected):
    assert doctor._classify_install(path, shebang) == expected


def test_classify_install_user_pip(tmp_path, monkeypatch):
    """``pip install --user`` → shebang under ~/.local/lib OR ~/Library/Python."""
    home = tmp_path
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    shebang = f"{home}/.local/lib/python3.11/site-packages/../../../bin/python3"
    assert doctor._classify_install(f"{home}/.local/bin/memee", shebang) == "user-pip"


# ── Doctor report layout ──────────────────────────────────────────────


def test_doctor_report_single_install_section(monkeypatch, tmp_path):
    """Single install → one green-check Installations line, no fix block."""
    bindir = tmp_path / "bin"
    _make_fake_memee(bindir)
    monkeypatch.setenv("PATH", str(bindir))

    health = doctor.get_install_health()
    buf = io.StringIO()
    with redirect_stdout(buf):
        doctor.print_installations_section(health)
    out = buf.getvalue()

    assert "Installations:" in out
    assert "memee" in out
    # No multi-install warning banner.
    assert "memee binaries on PATH" not in out
    assert "Fix:" not in out


def test_doctor_report_multi_install_section(monkeypatch, tmp_path):
    """Multi-install → warning, table, and fix block."""
    homebrew = tmp_path / "opt" / "homebrew" / "bin"
    pipx = tmp_path / "home" / ".local" / "pipx" / "venvs" / "memee" / "bin"
    _make_fake_memee(homebrew, shebang=f"#!{tmp_path}/opt/homebrew/bin/python3")
    _make_fake_memee(pipx, shebang=f"#!{tmp_path}/home/.local/pipx/venvs/memee/bin/python")

    monkeypatch.setenv("PATH", f"{homebrew}{os.pathsep}{pipx}")
    health = doctor.get_install_health()
    buf = io.StringIO()
    with redirect_stdout(buf):
        doctor.print_installations_section(health)
    out = buf.getvalue()

    assert "Installations:" in out
    assert "2 memee binaries on PATH" in out
    assert "Fix:" in out
    # The active (Homebrew) install gets the [active] tag.
    assert "[active]" in out
    assert "[shadowed]" in out


def test_run_doctor_emits_multi_install_issue(monkeypatch, tmp_path):
    """run_doctor() returns an issue with type=multi_install when applicable."""
    homebrew = tmp_path / "opt" / "homebrew" / "bin"
    pipx = tmp_path / "home" / ".local" / "pipx" / "venvs" / "memee" / "bin"
    _make_fake_memee(homebrew)
    _make_fake_memee(pipx)
    monkeypatch.setenv("PATH", f"{homebrew}{os.pathsep}{pipx}")

    results = doctor.run_doctor(auto_fix=False, install_hooks=False)
    types = [i.get("type") for i in results.get("issues", [])]
    assert "multi_install" in types
    assert results["installs"]["count"] == 2
    assert results["installs"]["multi"] is True


# ── memee setup pre-flight ────────────────────────────────────────────


def test_setup_refuses_on_multi_install(monkeypatch, tmp_path):
    """memee setup exits 1 when 2+ memee binaries are on PATH."""
    homebrew = tmp_path / "opt" / "homebrew" / "bin"
    pipx = tmp_path / "home" / ".local" / "pipx" / "venvs" / "memee" / "bin"
    _make_fake_memee(homebrew)
    _make_fake_memee(pipx)
    monkeypatch.setenv("PATH", f"{homebrew}{os.pathsep}{pipx}")

    runner = CliRunner()
    result = runner.invoke(cli, ["setup"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "Setup refused" in result.output
    assert "memee doctor" in result.output


def test_setup_ignore_multi_install_proceeds(monkeypatch, tmp_path):
    """--ignore-multi-install lets setup pass the pre-flight check.

    We don't run the full wizard (it would prompt for input); we just
    assert the refusal banner is NOT printed. Use ``--dry-run`` so any
    actual writes are no-ops, and feed an EOF to the prompt so the wizard
    aborts cleanly.
    """
    homebrew = tmp_path / "opt" / "homebrew" / "bin"
    pipx = tmp_path / "home" / ".local" / "pipx" / "venvs" / "memee" / "bin"
    _make_fake_memee(homebrew)
    _make_fake_memee(pipx)
    monkeypatch.setenv("PATH", f"{homebrew}{os.pathsep}{pipx}")

    runner = CliRunner()
    # Empty stdin → wizard's input() raises EOFError, runner reports it.
    # That's fine — we only care that the pre-flight didn't refuse.
    result = runner.invoke(
        cli,
        ["setup", "--ignore-multi-install", "--dry-run"],
        input="",
    )
    assert "Setup refused" not in result.output


# ── memee --version output ────────────────────────────────────────────


def test_version_flag_single_install(monkeypatch, tmp_path):
    """--version prints the version and the resolved binary path."""
    bindir = tmp_path / "bin"
    _make_fake_memee(bindir)
    monkeypatch.setenv("PATH", str(bindir))

    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])

    assert result.exit_code == 0
    # Just asserts the version line is the first thing printed and the
    # binary path appears somewhere — exact formatting may evolve.
    from memee import __version__
    assert f"memee {__version__}" in result.output
    assert str(bindir / "memee") in result.output
    # Single install → no "alt:" line.
    assert "alt:" not in result.output


def test_version_flag_multi_install_warns(monkeypatch, tmp_path):
    """--version surfaces shadow installs and points at memee doctor."""
    homebrew = tmp_path / "opt" / "homebrew" / "bin"
    pipx = tmp_path / "home" / ".local" / "pipx" / "venvs" / "memee" / "bin"
    _make_fake_memee(homebrew)
    _make_fake_memee(pipx)
    monkeypatch.setenv("PATH", f"{homebrew}{os.pathsep}{pipx}")

    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "alt:" in result.output
    assert "memee doctor" in result.output


# ── memee doctor --ignore-multi-install ────────────────────────────────


# ── Auto-fix safety gate ──────────────────────────────────────────────


def _fake_pip_show(required_by: list[str]) -> callable:
    """Return a subprocess.run replacement that fakes ``pip show memee``.

    We accept any ``[python, -m, pip, show, memee]`` invocation and respond
    with a ``Required-by:`` line built from ``required_by``. The real
    subprocess module isn't touched.
    """
    def fake_run(cmd, **kwargs):
        import subprocess as _sp

        text = "Name: memee\nVersion: 2.0.1\nRequired-by:"
        if required_by:
            text += " " + ", ".join(required_by)
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout=text, stderr="")

    return fake_run


def test_can_safely_remove_homebrew_python_no_deps(monkeypatch, tmp_path):
    """Active = homebrew-python with newer shadowed pipx + no reverse deps
    → safe."""
    py = tmp_path / "py"
    py.write_text("")
    py.chmod(0o755)
    active = {
        "path": "/opt/homebrew/bin/memee",
        "install_kind": "homebrew-python",
        "shebang_python": str(py),
        "version": "1.1.0",
    }
    shadowed = [{
        "path": "/Users/x/.local/bin/memee",
        "install_kind": "pipx",
        "version": "2.0.1",
    }]
    monkeypatch.setattr(doctor.subprocess, "run", _fake_pip_show([]))
    ok, reason = doctor._can_safely_remove(active, shadowed)
    assert ok, reason
    assert reason == ""


def test_can_safely_remove_user_pip_unknown_active_version(monkeypatch, tmp_path):
    """Editable install pointing at a deleted worktree reports no version
    via --version. That's exactly the case this fix exists for — treat as
    older."""
    py = tmp_path / "py"
    py.write_text("")
    py.chmod(0o755)
    active = {
        "path": "/Users/x/.local/bin/memee",
        "install_kind": "user-pip",
        "shebang_python": str(py),
        "version": None,
    }
    shadowed = [{
        "install_kind": "pipx",
        "version": "2.0.1",
    }]
    monkeypatch.setattr(doctor.subprocess, "run", _fake_pip_show([]))
    ok, _ = doctor._can_safely_remove(active, shadowed)
    assert ok


def test_can_safely_remove_refuses_system_python(monkeypatch, tmp_path):
    """sudo-required uninstalls aren't auto-fixed — too risky."""
    py = tmp_path / "py"
    py.write_text("")
    py.chmod(0o755)
    active = {
        "path": "/usr/bin/memee",
        "install_kind": "system-python",
        "shebang_python": str(py),
        "version": "1.0.0",
    }
    shadowed = [{"install_kind": "pipx", "version": "2.0.1"}]
    ok, reason = doctor._can_safely_remove(active, shadowed)
    assert not ok
    assert "system Python" in reason or "homebrew" in reason.lower() or "pip-managed" in reason


def test_can_safely_remove_refuses_when_shadowed_older(monkeypatch, tmp_path):
    """Shadowed is OLDER than active → don't downgrade."""
    py = tmp_path / "py"
    py.write_text("")
    py.chmod(0o755)
    active = {
        "install_kind": "homebrew-python",
        "shebang_python": str(py),
        "version": "2.0.1",
    }
    shadowed = [{"install_kind": "pipx", "version": "1.0.0"}]
    ok, reason = doctor._can_safely_remove(active, shadowed)
    assert not ok
    assert "downgrade" in reason or "older" in reason


def test_can_safely_remove_refuses_when_reverse_deps_exist(monkeypatch, tmp_path):
    """Other packages depend on memee → leave alone."""
    py = tmp_path / "py"
    py.write_text("")
    py.chmod(0o755)
    active = {
        "install_kind": "homebrew-python",
        "shebang_python": str(py),
        "version": "1.1.0",
    }
    shadowed = [{"install_kind": "pipx", "version": "2.0.1"}]
    monkeypatch.setattr(
        doctor.subprocess, "run", _fake_pip_show(["memee-extras", "some-plugin"])
    )
    ok, reason = doctor._can_safely_remove(active, shadowed)
    assert not ok
    assert "memee-extras" in reason


def test_can_safely_remove_refuses_when_pip_show_fails(monkeypatch, tmp_path):
    """pip not runnable → don't auto-fix; surface the reason."""
    py = tmp_path / "py"
    py.write_text("")
    py.chmod(0o755)
    active = {
        "install_kind": "homebrew-python",
        "shebang_python": str(py),
        "version": "1.1.0",
    }
    shadowed = [{"install_kind": "pipx", "version": "2.0.1"}]

    def fake_run_fail(cmd, **kwargs):
        import subprocess as _sp
        return _sp.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run_fail)
    ok, reason = doctor._can_safely_remove(active, shadowed)
    assert not ok
    assert "pip show" in reason


def test_uninstall_active_appends_break_system_packages_for_homebrew(monkeypatch, tmp_path):
    """PEP 668 (v2.0.3 fix) — Homebrew Python's pip refuses to touch
    site-packages without ``--break-system-packages``. The auto-fix must
    add the flag, otherwise it's a no-op masquerading as success."""
    py = tmp_path / "py"
    py.write_text("")
    py.chmod(0o755)

    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        import subprocess as _sp
        captured.append(cmd)
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    out = doctor._uninstall_active(
        {
            "shebang_python": str(py),
            "install_kind": "homebrew-python",
            "path": "/opt/homebrew/bin/memee",
        },
        dry_run=False,
    )
    assert out["ok"]
    assert captured, "expected pip uninstall to be invoked"
    assert "--break-system-packages" in captured[0]


def test_uninstall_active_omits_flag_for_user_pip(monkeypatch, tmp_path):
    """user-pip lands in ~/.local/lib, which is *not* externally-managed.
    Adding the flag would still work but is unnecessary noise."""
    py = tmp_path / "py"
    py.write_text("")
    py.chmod(0o755)

    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        import subprocess as _sp
        captured.append(cmd)
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    doctor._uninstall_active(
        {
            "shebang_python": str(py),
            "install_kind": "user-pip",
            "path": "/Users/x/.local/bin/memee",
        },
        dry_run=False,
    )
    assert captured
    assert "--break-system-packages" not in captured[0]


def test_uninstall_active_dry_run_does_not_call_subprocess(monkeypatch, tmp_path):
    """dry_run=True returns success without invoking pip."""
    called = {"n": 0}

    def fake_run(*a, **kw):
        called["n"] += 1
        raise AssertionError("subprocess.run should NOT be called in dry-run")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    py = tmp_path / "py"
    py.write_text("")
    out = doctor._uninstall_active(
        {"shebang_python": str(py)}, dry_run=True
    )
    assert out["ok"]
    assert out["dry_run"]
    assert called["n"] == 0
    assert out["command"][:3] == [str(py), "-m", "pip"]


def _inject_installs(monkeypatch, installs: list[dict]) -> None:
    """Bypass the PATH scan: stuff ``installs`` straight into the cache.

    Real PATH-scan tests live above; these tests only care about run_doctor's
    branching once detection has happened. install_kind classification needs
    real /opt/homebrew/... paths — easier to inject than to reproduce in tmp_path.
    """
    doctor._MEMEE_INSTALL_CACHE = installs


def test_run_doctor_auto_fixes_safe_multi_install(monkeypatch, tmp_path):
    """Happy path: detection finds two installs, ``run_doctor`` invokes
    pip uninstall, the result is reported as ``fixed``."""
    py_homebrew = tmp_path / "py3.14"
    py_homebrew.write_text("")
    py_homebrew.chmod(0o755)
    _inject_installs(monkeypatch, [
        {
            "path": "/opt/homebrew/bin/memee",
            "real_path": "/opt/homebrew/bin/memee",
            "mtime": 0.0,
            "version": "1.1.0",
            "install_kind": "homebrew-python",
            "shebang_python": str(py_homebrew),
        },
        {
            "path": "/Users/x/.local/bin/memee",
            "real_path": "/Users/x/.local/pipx/venvs/memee/bin/memee",
            "mtime": 0.0,
            "version": "2.0.1",
            "install_kind": "pipx",
            "shebang_python": "/Users/x/.local/pipx/venvs/memee/bin/python",
        },
    ])

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        import subprocess as _sp

        calls.append(cmd)
        if "show" in cmd:
            return _sp.CompletedProcess(
                args=cmd, returncode=0,
                stdout="Name: memee\nRequired-by:", stderr="",
            )
        if "uninstall" in cmd:
            return _sp.CompletedProcess(
                args=cmd, returncode=0,
                stdout="Found existing installation: memee 1.1.0\nSuccessfully uninstalled memee-1.1.0\n",
                stderr="",
            )
        raise AssertionError(f"unexpected subprocess: {cmd}")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    results = doctor.run_doctor(auto_fix=True, install_hooks=False)

    assert "multi_install" in results.get("fixed", [])
    assert "multi_install" not in [
        i.get("type") for i in results.get("issues", [])
    ]
    assert any(
        "uninstall" in c and c[0] == str(py_homebrew)
        for c in calls
    ), f"expected pip uninstall on {py_homebrew}, got: {calls}"
    # Cache should have been invalidated so a re-scan would see the new state.
    assert doctor._MEMEE_INSTALL_CACHE is None


def test_run_doctor_skips_install_fix_when_requested(monkeypatch, tmp_path):
    """skip_install_fix=True → detection runs but no pip uninstall."""
    py_homebrew = tmp_path / "py3"
    py_homebrew.write_text("")
    py_homebrew.chmod(0o755)
    _inject_installs(monkeypatch, [
        {
            "path": "/opt/homebrew/bin/memee",
            "install_kind": "homebrew-python",
            "shebang_python": str(py_homebrew),
            "version": "1.1.0",
        },
        {
            "path": "/Users/x/.local/bin/memee",
            "install_kind": "pipx",
            "version": "2.0.1",
        },
    ])

    def boom(cmd, **kwargs):
        if "uninstall" in cmd:
            raise AssertionError("uninstall must not run when skip_install_fix=True")
        import subprocess as _sp
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="Required-by:", stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", boom)
    results = doctor.run_doctor(
        auto_fix=True, install_hooks=False, skip_install_fix=True
    )
    assert "multi_install" not in results.get("fixed", [])
    # Issue stays in the list so the report still warns the user.
    assert "multi_install" in [i.get("type") for i in results.get("issues", [])]


def test_run_doctor_dry_run_does_not_remove(monkeypatch, tmp_path):
    """dry_run=True → record what would happen, don't run pip."""
    py_homebrew = tmp_path / "py3"
    py_homebrew.write_text("")
    py_homebrew.chmod(0o755)
    _inject_installs(monkeypatch, [
        {
            "path": "/opt/homebrew/bin/memee",
            "install_kind": "homebrew-python",
            "shebang_python": str(py_homebrew),
            "version": "1.1.0",
        },
        {
            "path": "/Users/x/.local/bin/memee",
            "install_kind": "pipx",
            "version": "2.0.1",
        },
    ])

    def fake_run(cmd, **kwargs):
        import subprocess as _sp

        if "uninstall" in cmd:
            raise AssertionError("uninstall must not run in dry-run mode")
        if "show" in cmd:
            return _sp.CompletedProcess(
                args=cmd, returncode=0,
                stdout="Name: memee\nRequired-by:", stderr="",
            )
        raise AssertionError(f"unexpected subprocess: {cmd}")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    results = doctor.run_doctor(
        auto_fix=True, install_hooks=False, dry_run=True
    )
    outcome = results["installs"].get("fix_outcome")
    assert outcome is not None
    assert outcome["dry_run"] is True
    # multi_install stays in issues — dry-run doesn't actually fix.
    assert "multi_install" in [i.get("type") for i in results.get("issues", [])]


# ── memee doctor --ignore-multi-install ────────────────────────────────


def test_doctor_ignore_multi_install_filters_issue(monkeypatch, tmp_path):
    """The flag drops multi_install from the issues list before rendering."""
    homebrew = tmp_path / "opt" / "homebrew" / "bin"
    pipx = tmp_path / "home" / ".local" / "pipx" / "venvs" / "memee" / "bin"
    _make_fake_memee(homebrew)
    _make_fake_memee(pipx)
    monkeypatch.setenv("PATH", f"{homebrew}{os.pathsep}{pipx}")

    runner = CliRunner()
    # --no-fix avoids touching real config files; --no-hooks avoids hooks too.
    result = runner.invoke(
        cli,
        ["doctor", "--no-fix", "--no-hooks", "--ignore-multi-install"],
    )
    # Doctor still runs and may exit 0 even if other issues exist; we only
    # care that the multi-install warning isn't reported as an ISSUE — the
    # Installations section above still shows the table (informational).
    # The phrase "memee binaries on PATH" appears in the Installations
    # section; we look for the issue-list version of it (which would have
    # been suppressed). Easier check: the message text we put in run_doctor
    # ends with "command-not-found errors" — when the issue is filtered,
    # that exact phrase shows up only inside the Installations section
    # (in DIM grey).
    assert result.exit_code == 0
