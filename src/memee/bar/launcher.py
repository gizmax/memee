"""macOS LaunchAgent installer for the menubar miniapp.

Writes a per-user plist to ``~/Library/LaunchAgents`` and loads it
with ``launchctl``. The agent runs ``memee bar start`` foreground —
``rumps`` is its own run loop, so the LaunchAgent's job spec uses
``KeepAlive=true`` and ``RunAtLoad=true`` so the menubar survives
crashes and reappears on next login.

Linux equivalent (``~/.config/autostart/memee-bar.desktop``) is a
follow-up — the v2.3.0 ship is macOS-only per the autoresearch.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LABEL = "cz.memee.bar"
PLIST_NAME = f"{LABEL}.plist"


def launch_agents_dir() -> Path:
    """Return ``~/Library/LaunchAgents`` honouring ``$HOME``."""
    return Path(os.environ.get("HOME") or str(Path.home())) / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return launch_agents_dir() / PLIST_NAME


def _resolve_memee_binary() -> str | None:
    """Find the ``memee`` executable to invoke from the LaunchAgent.

    Prefer an absolute path under ``sys.prefix`` (the pipx / venv that's
    running this command) so the LaunchAgent doesn't depend on PATH
    being set inside launchd's restricted environment. Fall back to
    ``shutil.which`` and finally to the literal ``memee`` so the user
    sees a sensible value they can fix.
    """
    candidate = Path(sys.prefix) / "bin" / "memee"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    found = shutil.which("memee")
    if found:
        return found
    return None


def build_plist_bytes() -> bytes:
    """Construct the plist payload for the user-level LaunchAgent.

    KeepAlive=True keeps the menubar alive across crashes. RunAtLoad
    starts it on bootstrap (login). StandardOut/ErrorPath route to
    ``~/.memee/bar.log`` so a curious user can ``tail -f`` it.
    """
    binary = _resolve_memee_binary() or "memee"
    home = Path(os.environ.get("MEMEE_HOME") or str(Path.home() / ".memee"))
    log = home / "bar.log"
    program: dict[str, Any] = {
        "Label": LABEL,
        "ProgramArguments": [binary, "bar", "start"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "EnvironmentVariables": {
            # Ensure the agent can find user-installed binaries on the
            # rare path where pipx hasn't symlinked into /usr/local/bin.
            "PATH": (
                f"{Path(sys.prefix) / 'bin'}:"
                f"/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
            ),
        },
    }
    return plistlib.dumps(program, sort_keys=False)


@dataclass
class LauncherResult:
    ok: bool
    message: str
    plist_path: Path | None = None


def _is_darwin() -> bool:
    return sys.platform == "darwin"


def _launchctl(*args: str) -> tuple[int, str]:
    """Run ``launchctl`` with the given args, return (rc, combined output)."""
    try:
        proc = subprocess.run(
            ["launchctl", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError) as e:
        return 127, f"launchctl missing: {e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def install_launch_agent() -> LauncherResult:
    """Write the plist and load it. Idempotent."""
    if not _is_darwin():
        return LauncherResult(
            ok=False,
            message=(
                "memee bar install: macOS-only in v2.3.0. "
                "On Linux, run `memee bar start` from your session "
                "autostart (gnome-session-properties / KDE Autostart)."
            ),
        )

    binary = _resolve_memee_binary()
    if not binary:
        return LauncherResult(
            ok=False,
            message=(
                "Cannot find a `memee` binary to launch. Install with "
                "`pipx install --force 'memee[bar]'` and retry."
            ),
        )

    target_dir = launch_agents_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = plist_path()

    payload = build_plist_bytes()
    target.write_bytes(payload)

    # Unload first (ignore failure — plist might not have been loaded
    # yet), then bootstrap. `bootstrap` is the modern API; `load` is
    # the legacy one. We try modern, fall back to legacy.
    uid = os.getuid()
    domain = f"gui/{uid}"
    service = f"{domain}/{LABEL}"

    _launchctl("bootout", service)
    rc, msg = _launchctl("bootstrap", domain, str(target))
    if rc != 0:
        rc, msg = _launchctl("load", str(target))
        if rc != 0:
            return LauncherResult(
                ok=False,
                message=f"plist written but load failed: {msg}",
                plist_path=target,
            )

    return LauncherResult(
        ok=True,
        message=(
            "LaunchAgent installed. Menubar will appear shortly; on "
            "next login it auto-starts."
        ),
        plist_path=target,
    )


def uninstall_launch_agent() -> LauncherResult:
    """Unload and delete the plist. Idempotent — no-op if absent."""
    if not _is_darwin():
        return LauncherResult(
            ok=True,
            message="No LaunchAgent to remove on non-macOS platforms.",
        )

    target = plist_path()
    uid = os.getuid()
    service = f"gui/{uid}/{LABEL}"

    if target.exists():
        _launchctl("bootout", service)
        _launchctl("unload", str(target))
        try:
            target.unlink()
        except OSError as e:
            return LauncherResult(
                ok=False,
                message=f"could not delete {target}: {e}",
                plist_path=target,
            )

    return LauncherResult(
        ok=True,
        message="LaunchAgent removed.",
        plist_path=target if target.exists() else None,
    )


def launch_agent_status() -> dict[str, Any]:
    """Return a dict describing the current LaunchAgent state.

    Useful for ``memee bar doctor``. Never raises — every probe is fenced
    so a slow ``launchctl print`` can't break the report.
    """
    target = plist_path()
    info: dict[str, Any] = {
        "installed": target.exists(),
        "path": str(target),
        "loaded": False,
        "pid": None,
    }
    if not _is_darwin() or not target.exists():
        return info
    uid = os.getuid()
    rc, out = _launchctl("print", f"gui/{uid}/{LABEL}")
    if rc == 0:
        info["loaded"] = True
        # `launchctl print` output includes "pid = NNNN" on a running
        # job. Parse it cheaply; format is stable enough.
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("pid ="):
                try:
                    info["pid"] = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
                break
    return info
