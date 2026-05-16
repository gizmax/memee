"""Diagnostic command for the menubar miniapp.

Surfaces the small set of things that can break the app:

  * Wrong platform (Linux/Windows in v2.3.0)
  * Missing ``rumps`` / ``watchdog`` extras
  * State file absent (hooks haven't fired yet — common on fresh installs)
  * State file present but stale (clock-skew, hooks unwired, etc.)
  * LaunchAgent installed but not loaded (after a reboot, a `pip
    install --user --upgrade` that swapped the binary path, etc.)

Output is one line per check, glyph + label + short detail. Returns a
dict with ``ok`` + ``lines`` so the CLI layer stays the only place
that knows about ANSI colours.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memee.bar.state import read_state, state_path


GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


def _line(glyph_colour: str, glyph: str, label: str, detail: str | None = None) -> str:
    detail_part = f" {DIM}{detail}{RESET}" if detail else ""
    return f"  {glyph_colour}{glyph}{RESET} {label}{detail_part}"


def diagnose() -> dict[str, Any]:
    """Build the diagnostic report. Returns ``{"ok": bool, "lines": [str]}``."""
    lines: list[str] = []
    ok = True

    lines.append(f"  {DIM}━━━ MEMEE BAR HEALTH ━━━{RESET}")

    # ── Platform ─────────────────────────────────────────────────
    if sys.platform == "darwin":
        lines.append(_line(GREEN, "✓", "platform: macOS"))
    elif sys.platform.startswith("linux"):
        lines.append(
            _line(YELLOW, "!", "platform: linux",
                  "v2.3.0 menubar is macOS-only; Linux support in a follow-up release")
        )
        ok = False
    else:
        lines.append(
            _line(RED, "✗", f"platform: {sys.platform}",
                  "no menubar support — Memee CLI/MCP works regardless")
        )
        ok = False

    # ── rumps (required to actually render the bar) ──────────────
    try:
        import rumps  # noqa: F401
        version = getattr(rumps, "__version__", "unknown")
        lines.append(_line(GREEN, "✓", "rumps installed", version))
    except ImportError:
        lines.append(
            _line(RED, "✗", "rumps missing",
                  "pipx install --force 'memee[bar]'")
        )
        ok = False

    # ── watchdog (used for live refresh; degrades to 60s timer) ──
    try:
        import watchdog  # noqa: F401
        try:
            import importlib.metadata as _md
            version = _md.version("watchdog")
        except Exception:
            version = "unknown"
        lines.append(_line(GREEN, "✓", "watchdog installed", str(version)))
    except ImportError:
        lines.append(
            _line(YELLOW, "!", "watchdog missing",
                  "60-second timer fallback active; recall counts will refresh slowly")
        )

    # ── State file: presence + freshness ─────────────────────────
    path = Path(str(state_path()))
    if not path.exists():
        lines.append(
            _line(YELLOW, "!", f"state file not yet written: {path}",
                  "expected — hooks fire on first agent SessionStart / Stop")
        )
    else:
        state = read_state()
        updated_at = state.get("updated_at")
        if updated_at:
            try:
                ts = datetime.fromisoformat(updated_at)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_s = (datetime.now(timezone.utc) - ts).total_seconds()
                if age_s < 0:
                    detail = "future timestamp (clock skew?)"
                    lines.append(_line(YELLOW, "!", "state file present", detail))
                elif age_s < 3600:
                    lines.append(
                        _line(GREEN, "✓", "state file fresh",
                              f"updated {int(age_s)}s ago")
                    )
                elif age_s < 86400:
                    lines.append(
                        _line(GREEN, "✓", "state file recent",
                              f"updated {int(age_s/60)}m ago")
                    )
                else:
                    lines.append(
                        _line(YELLOW, "!", "state file stale",
                              f"updated {int(age_s/86400)}d ago "
                              "— hooks may have stopped firing")
                    )
            except ValueError:
                lines.append(
                    _line(YELLOW, "!", "state file present",
                          "but `updated_at` did not parse")
                )
        else:
            lines.append(
                _line(YELLOW, "!", "state file present",
                      "no updated_at — likely written by older Memee")
            )

    # ── LaunchAgent status ───────────────────────────────────────
    if sys.platform == "darwin":
        try:
            from memee.bar.launcher import launch_agent_status

            la = launch_agent_status()
            if not la["installed"]:
                lines.append(
                    _line(DIM, "-", "LaunchAgent not installed",
                          "run `memee bar install` to autostart on login")
                )
            elif not la["loaded"]:
                lines.append(
                    _line(YELLOW, "!", "LaunchAgent installed but not loaded",
                          "run `memee bar install` again to reload")
                )
            else:
                pid = la.get("pid")
                detail = f"loaded (pid {pid})" if pid else "loaded"
                lines.append(_line(GREEN, "✓", "LaunchAgent active", detail))
        except Exception as e:
            lines.append(
                _line(YELLOW, "!", "LaunchAgent probe failed",
                      str(e)[:80])
            )

    return {"ok": ok, "lines": lines}
