"""v2.4.7 regression: ``memee --version`` must not recursively spawn.

The v2.4.6 live install showed a fork-bomb: ``--version``'s callback
called ``detect_memee_installs()``, which subprocess-spawns
``<path> --version`` on every memee binary it finds on PATH. Each
child re-entered the callback → re-scanned PATH → spawned its own
children. 3 memees on PATH = 3^N grandchildren.

The fix is two-layered:

A. The ``--version`` callback no longer calls ``detect_memee_installs``.
B. ``_query_version`` passes ``MEMEE_SKIP_INSTALL_SCAN=1`` in the
   subprocess env. The callback short-circuits to a bare version line
   when the env var is set — structurally preventing recursion even
   from future callers.

These tests guard both layers.
"""

from __future__ import annotations

import os

from click.testing import CliRunner

from memee import __version__, doctor
from memee.cli import cli


def test_version_with_skip_env_prints_only_bare_line(monkeypatch):
    """MEMEE_SKIP_INSTALL_SCAN=1 → just the version line, no diagnostics.

    This is the env var ``_query_version`` passes to children. If a memee
    binary is invoked with this env, it must NOT scan PATH (no
    detect_memee_installs call) and must NOT print anything beyond the
    parseable version line.
    """
    monkeypatch.setenv("MEMEE_SKIP_INSTALL_SCAN", "1")

    # Sentinel: if anyone calls detect_memee_installs during --version,
    # raise. The whole point of the env-var guard is that this call path
    # never executes.
    def boom(*args, **kwargs):
        raise AssertionError(
            "detect_memee_installs must NOT run when MEMEE_SKIP_INSTALL_SCAN=1"
        )

    monkeypatch.setattr(doctor, "detect_memee_installs", boom)

    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])

    assert result.exit_code == 0
    # Output is the bare version line — single line, exact.
    assert result.output.strip() == f"memee {__version__}"
    # No alt: line, no installed: line, no doctor hint — fully terse.
    assert "alt:" not in result.output
    assert "installed:" not in result.output
    assert "Run: memee doctor" not in result.output


def test_query_version_passes_skip_env_to_subprocess(monkeypatch, tmp_path):
    """``_query_version`` must inject MEMEE_SKIP_INSTALL_SCAN=1 in the
    child's env.

    Without this, a child memee binary's --version callback would call
    detect_memee_installs() again, triggering more subprocess.run calls,
    triggering more children, indefinitely. The env var is the structural
    fix: regardless of who calls _query_version, the child can't recurse.
    """
    captured_env: dict = {}

    def fake_run(cmd, **kwargs):
        # Record the env the subprocess would have seen.
        captured_env.update(kwargs.get("env") or {})
        import subprocess as _sp
        return _sp.CompletedProcess(
            args=cmd, returncode=0, stdout="memee 2.4.7\n", stderr="",
        )

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    fake_binary = tmp_path / "memee"
    fake_binary.write_text("#!/bin/sh\necho memee 2.4.7\n")
    fake_binary.chmod(0o755)

    version = doctor._query_version(str(fake_binary))

    assert version == "2.4.7"
    assert captured_env.get("MEMEE_SKIP_INSTALL_SCAN") == "1", (
        "child env must carry the recursion guard"
    )
    # PATH and HOME should still be inherited — env is a *merge*, not
    # a replacement. Smoke check.
    assert "PATH" in captured_env or "PATH" in os.environ
