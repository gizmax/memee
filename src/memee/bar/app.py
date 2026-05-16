"""Menubar miniapp — macOS-first via ``rumps``.

A *watching* surface only: read-only popover that reflects the latest
hook activity from ``~/.memee/state.json``. No write capability — all
records go through the CLI / MCP / hook path. Surface design lifted
from the autoresearch on presence/liveness (Tailscale + 1Password +
Cron menubar conventions): four data lines, three actions, one quit.

The app is opt-in: ``memee bar start`` runs it foreground;
``memee bar install`` writes a LaunchAgent plist so it autostarts on
login. Quit through the menu (or ``launchctl unload``) tears it down.

Why rumps: tiny, dependency on stdlib + pyobjc (already required for
any macOS-native Python tray). pystray covers Linux/Windows in a
follow-up; the v2.3.0 ship is mac-only to keep the surface tight.

The popover refreshes on file-change via ``watchdog`` (FSEvents on
macOS) and as a 60-second timer fallback for clocks-and-counts that
don't change just because state.json was written. Polling SQLite is
explicitly avoided — WAL mutates the ``-wal`` / ``-shm`` companion
files on every read, which would fire the file watcher continuously.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memee.bar.state import read_state, state_path

logger = logging.getLogger(__name__)


# ── Pretty-printing ─────────────────────────────────────────────────


def _relative_time(iso_ts: str | None) -> str:
    """Format an ISO-8601 timestamp as "Xs/m/h/d ago".

    Robust to missing timezone, broken parses, future timestamps (NTP
    drift). Returns "—" when the input can't be parsed.
    """
    if not iso_ts:
        return "—"
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def render_menu_strings(state: dict[str, Any]) -> dict[str, str]:
    """Build the strings the popover shows, from the parsed state file.

    Returns a dict of menu-item labels:

      * ``title``           — the bar item itself when text mode is on
      * ``status``          — coloured-dot summary, systemctl-shaped
      * ``last_brief``      — "last brief: 'task' (5s ago)"
      * ``last_learn``      — "last learn: success (2m ago)"
      * ``totals``          — "47 memories · 13 canon · 3 critical"
      * ``impact``          — "3 mistakes avoided · 42 patterns applied"
                              ("" when there's no signal — earned silence)
      * ``update``          — "↑ v2.4.4 available — pipx upgrade memee"
                              ("" when up to date or check unavailable)

    Pure function — no side effects, easy to test.
    """
    totals = state.get("totals") or {}
    last_brief = state.get("last_brief") or {}
    last_learn = state.get("last_learn") or {}
    impact = state.get("impact") or {}
    update = state.get("update") or {}

    canon = totals.get("canon") or 0
    validated = totals.get("validated") or 0
    crits = totals.get("critical_warnings") or 0

    brief_ts = last_brief.get("ts")
    learn_ts = last_learn.get("ts")
    most_recent_ts = brief_ts if (
        not learn_ts or (brief_ts and brief_ts > learn_ts)
    ) else learn_ts

    # v2.4.4 impact line. Earned-silence: empty string when there's no
    # signal, so the renderer can hide the row entirely. Order chosen
    # so the "hard, evidence-backed" counter (mistakes_avoided) lands
    # first — it's the strictest claim Memee makes about itself.
    mistakes_avoided = int(impact.get("mistakes_avoided") or 0)
    patterns_applied = int(impact.get("patterns_applied") or 0)
    canon_growth_7d = int(impact.get("canon_growth_7d") or 0)
    impact_parts: list[str] = []
    if mistakes_avoided > 0:
        impact_parts.append(f"{mistakes_avoided} mistake{'s' if mistakes_avoided != 1 else ''} avoided")
    if patterns_applied > 0:
        impact_parts.append(f"{patterns_applied} pattern{'s' if patterns_applied != 1 else ''} applied")
    if canon_growth_7d > 0:
        impact_parts.append(f"+{canon_growth_7d} canon this week")
    impact_line = (
        "How Memee helped (7d): " + " · ".join(impact_parts)
        if impact_parts else ""
    )

    # v2.4.4 update notice. Empty when up to date so the renderer can
    # hide the row. Pull-only — `update_check.check()` is cached TTL
    # 24h so this is a cheap dict lookup most of the time.
    update_available = bool(update.get("available"))
    latest = update.get("latest") or ""
    current = update.get("current") or ""
    update_line = (
        f"↑ v{latest} available — pipx upgrade memee"
        if update_available and latest and latest != current else ""
    )

    return {
        "title": "Memee",
        "status": (
            f"● active · last {_relative_time(most_recent_ts)}"
            if most_recent_ts else "● standby · no activity yet"
        ),
        "last_brief": (
            f"Last brief: {(last_brief.get('task') or 'no task').strip() or 'no task'}"
            f" ({_relative_time(brief_ts)})"
            if brief_ts else "Last brief: —"
        ),
        "last_learn": (
            f"Last learn: {last_learn.get('outcome') or 'auto'}"
            f" ({_relative_time(learn_ts)})"
            if learn_ts else "Last learn: —"
        ),
        "totals": (
            f"{canon + validated} memories · {canon} canon · {crits} critical"
            if (canon or validated or crits)
            else "memee.db empty — run `memee pack install`"
        ),
        "impact": impact_line,
        "update": update_line,
    }


# ── Actions ─────────────────────────────────────────────────────────


def _open_in_default_app(path: Path | str) -> bool:
    """``open <path>`` on macOS; ``xdg-open`` on Linux. Returns success."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(path)])
        else:
            return False
        return True
    except OSError as e:
        logger.debug("bar.open: %s", e)
        return False


def _spawn_dream() -> None:
    """Run ``memee dream`` detached. Output goes to a log under the
    Memee home so a curious user can `tail` it; the miniapp returns
    immediately so the menubar stays responsive."""
    home = Path(os.environ.get("MEMEE_HOME", str(Path.home() / ".memee")))
    log_path = home / "bar-dream.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as fh:
            subprocess.Popen(
                ["memee", "dream"],
                stdout=fh, stderr=fh, stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
    except OSError as e:
        logger.debug("bar.dream: %s", e)


def _resolve_last_brief_artifact() -> Path | None:
    """Find a file the user can open to inspect "the last brief".

    v2.4.1: prefer the most recent file under
    ``~/.memee/briefs/`` (per-brief archive written by every
    successful ``memee brief``). Falls back to ``state.json`` if the
    archive directory is empty (e.g. ``MEMEE_NO_BRIEF_ARCHIVE=1`` is
    set, or the hook hasn't fired since the upgrade).
    """
    try:
        from memee.bar.briefs import latest_brief
        archived = latest_brief()
        if archived is not None:
            return archived
    except Exception:
        pass
    path = Path(str(state_path()))
    return path if path.exists() else None


# ── App ─────────────────────────────────────────────────────────────


def _build_app():
    """Construct the rumps.App + its menu structure.

    Factored out so tests can exercise the menu wiring without actually
    starting the run loop (which would block forever and need a real
    macOS GUI).
    """
    import rumps  # noqa: F401 — imported only when the app actually runs

    class MemeeBarApp(rumps.App):
        def __init__(self) -> None:
            super().__init__(
                "Memee",
                title="●",
                quit_button=None,  # We supply our own "Quit Memee bar".
            )
            # Menu order (v2.4.4):
            #   update-notice (hidden when up to date)
            #   ─
            #   status header
            #   ─
            #   last brief / last learn / totals
            #   impact summary (hidden when no signal)
            #   ─
            #   actions
            #   ─
            #   quit
            self.menu = [
                rumps.MenuItem("↑ v? available", callback=self.on_open_update),
                None,
                rumps.MenuItem("● standby · no activity yet"),
                None,
                rumps.MenuItem("Last brief: —"),
                rumps.MenuItem("Last learn: —"),
                rumps.MenuItem("—"),
                rumps.MenuItem("How Memee helped (7d): —"),
                None,
                rumps.MenuItem("Open last brief", callback=self.on_open_state),
                rumps.MenuItem("Run `memee dream`", callback=self.on_dream),
                None,
                rumps.MenuItem("Quit Memee bar", callback=rumps.quit_application),
            ]
            self.refresh()

        def refresh(self, *_args: object) -> None:
            """Reload state.json and update every menu label.

            Cheap (one file read + dict lookups); safe to call from a
            timer, a file-watcher callback, or after a manual action.
            v2.4.4: hides update notice + impact summary when their
            string is empty (earned silence — no signal, no row).
            """
            try:
                strings = render_menu_strings(read_state())
            except Exception as e:
                logger.debug("bar.refresh: %s", e)
                return
            try:
                # Resolve menu items by key — order is fixed in __init__
                # but title-based access decouples from index drift.
                update_item = self.menu.get("↑ v? available")
                update_line = strings.get("update") or ""
                if update_item is not None:
                    if update_line:
                        update_item.title = update_line
                        update_item.hidden = False
                    else:
                        update_item.hidden = True

                # The status / last_brief / last_learn / totals trio is
                # always shown — the row exists, only its text changes.
                items = list(self.menu.values())
                # Walk by content not index; tolerates the hidden-aware
                # rumps menu list when items get muted. Skip separator
                # items (``rumps.SeparatorMenuItem`` has no ``.title``).
                for it in items:
                    if not hasattr(it, "title"):
                        continue
                    title = it.title or ""
                    if title.startswith("●"):
                        it.title = strings["status"]
                    elif title.startswith("Last brief"):
                        it.title = strings["last_brief"]
                    elif title.startswith("Last learn"):
                        it.title = strings["last_learn"]
                    elif (
                        title == "—"
                        or ("memories" in title and "canon" in title)
                        or title.startswith("memee.db empty")
                    ):
                        it.title = strings["totals"]
                    elif title.startswith("How Memee helped"):
                        impact_line = strings.get("impact") or ""
                        if impact_line:
                            it.title = impact_line
                            it.hidden = False
                        else:
                            it.hidden = True
            except (IndexError, KeyError) as e:
                logger.debug("bar.refresh layout drift: %s", e)

        # ── Action: open PyPI / brew page for an upgrade ─────────
        def on_open_update(self, _sender: object) -> None:
            """Clicking the update notice opens PyPI in the browser so
            the user can read the release notes before upgrading."""
            try:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", "https://pypi.org/project/memee/"])
                elif sys.platform.startswith("linux"):
                    subprocess.Popen(["xdg-open", "https://pypi.org/project/memee/"])
            except OSError:
                pass

        # ── Actions ─────────────────────────────────────────────

        def on_open_state(self, _sender: object) -> None:
            target = _resolve_last_brief_artifact()
            if target is not None:
                _open_in_default_app(target)

        def on_dream(self, _sender: object) -> None:
            _spawn_dream()
            # Immediate refresh so the popover reflects the totals
            # rumble that dream's first phase will eventually trigger.
            self.refresh()

        # ── Timer fallback (60 s) ───────────────────────────────

        @rumps.timer(60)
        def _tick(self, _sender: object) -> None:
            self.refresh()

    return MemeeBarApp()


def _hide_from_dock() -> None:
    """Make the menubar app menu-bar-only (no Dock, no Cmd+Tab).

    Without this, pipx-installed rumps apps show up as a "Python" icon in
    the Dock because the host is a plain Python interpreter, not a proper
    ``.app`` bundle. Setting ``LSUIElement`` at runtime via the NSBundle
    info dictionary is the documented workaround for menubar-only Python
    apps.

    Must be called BEFORE ``rumps.App`` is constructed — setting it after
    the process has registered with the WindowServer is a no-op.

    Idempotent. Falls through silently when AppKit isn't available
    (non-macOS, missing pyobjc) so the surrounding code stays portable.
    """
    try:
        from AppKit import NSBundle  # pyobjc — transitive dep of rumps
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["LSUIElement"] = "1"   # NSDictionary expects string-y truthy
            info.setdefault("CFBundleName", "Memee")
            info.setdefault("CFBundleDisplayName", "Memee")
    except Exception:
        pass


def run() -> int:
    """Foreground entry point for ``memee bar start``.

    Returns an exit code so the CLI wrapper can propagate it sensibly.
    rumps' run loop blocks until the user quits via the menu (or a
    SIGTERM from launchctl).
    """
    if sys.platform != "darwin":
        sys.stderr.write(
            "memee bar: macOS-only in v2.3.0 — Linux/Windows support is on "
            "the roadmap, see CHANGELOG. Skipping.\n"
        )
        return 2

    try:
        import rumps  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "memee bar: rumps not installed. Run: "
            "pipx install --force 'memee[bar]'\n"
        )
        return 2

    # Hide the host Python process from the Dock + Cmd+Tab BEFORE the
    # WindowServer registration that rumps.App.__init__ triggers — too
    # late afterwards.
    _hide_from_dock()

    # File-watcher: refresh on state.json change. watchdog is a soft
    # dependency — fall back to the 60s rumps timer if it's missing so
    # the app still works.
    app = _build_app()
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        class _StateChanged(FileSystemEventHandler):
            def __init__(self, target_path: Path) -> None:
                self._target = target_path

            def on_modified(self, event):  # type: ignore[override]
                if Path(str(event.src_path)) == self._target:
                    app.refresh()

        path = Path(str(state_path()))
        path.parent.mkdir(parents=True, exist_ok=True)
        observer = Observer()
        observer.schedule(
            _StateChanged(path), str(path.parent), recursive=False,
        )
        observer.start()
    except ImportError:
        observer = None
        logger.debug("watchdog missing — 60s timer fallback only")

    try:
        app.run()
    finally:
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=2.0)
            except Exception:
                pass

    return 0
