"""State file shared between hooks and the menubar miniapp.

The autoresearch on presence signals (v2.3.0) recommended watching a
small JSON file rather than the SQLite DB itself: SQLite in WAL mode
mutates ``-wal``/``-shm`` companions on every read, which would fire
the file-watcher continuously on activity that isn't actually a
state change worth surfacing.

Schema lives at ``~/.memee/state.json``. Atomic write via
``os.replace`` so a partially-written file is never observed by a
reader. All fields optional — readers must handle missing keys
gracefully, since older Memee CLIs may not write every field yet.

Example payload::

    {
      "version": "2.3.0",
      "updated_at": "2026-05-14T12:34:56.789012+00:00",
      "last_brief": {
        "ts": "2026-05-14T12:34:56.789012+00:00",
        "project": "/Users/.../Memee",
        "task": "write tests",
        "bullets": 7,
        "tokens": 442
      },
      "last_learn": {
        "ts": "2026-05-14T12:35:10.123456+00:00",
        "auto": true
      },
      "totals": {
        "memories": 61,
        "canon": 13,
        "validated": 46,
        "critical_warnings": 3
      }
    }

Updates are non-blocking from the hook's perspective: every helper here
swallows IOError / OSError so a state-file glitch can never break the
hook flow. The miniapp is decorative; the agent path is sacred.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _memee_home() -> Path:
    """Resolve ``~/.memee`` honouring ``MEMEE_HOME`` for tests.

    Mirrors the same convention used elsewhere in the codebase so the
    state file co-locates with the DB under test runs.
    """
    override = os.environ.get("MEMEE_HOME")
    if override:
        return Path(override)
    return Path.home() / ".memee"


STATE_FILENAME = "state.json"


def state_path() -> Path:
    """Return the resolved path to the state file.

    A function rather than a module-level constant so ``MEMEE_HOME``
    overrides in tests take effect after import.
    """
    return _memee_home() / STATE_FILENAME


# Back-compat module-level alias (resolved lazily on first attribute access
# by re-running state_path()). Some callers prefer the constant-shaped
# import. They get the *current* value, not a frozen one.
class _StatePathProxy:
    def __fspath__(self) -> str:
        return str(state_path())

    def __str__(self) -> str:
        return str(state_path())

    def __repr__(self) -> str:
        return f"STATE_PATH({state_path()!r})"

    @property
    def parent(self) -> Path:
        return state_path().parent

    def exists(self) -> bool:
        return state_path().exists()


STATE_PATH = _StatePathProxy()


def read_state() -> dict[str, Any]:
    """Return the parsed state dict, or ``{}`` when the file is absent
    or unreadable. Never raises — the miniapp must keep rendering even
    when the state file is mid-rewrite or corrupted (e.g. from a power
    loss between ``write`` and ``replace``)."""
    path = state_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("bar.state: read failed (%s) — returning empty", e)
        return {}


def write_state(payload: dict[str, Any]) -> bool:
    """Replace the state file with ``payload`` atomically.

    Writes to a tempfile in the same directory and ``os.replace`` it
    into place — guarantees readers never see a half-written file on
    POSIX. Returns True on success, False on any IO error (logged at
    debug level — callers in hook paths should NOT block on this).
    """
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug("bar.state: cannot create %s (%s)", path.parent, e)
        return False

    # Stamp every write with the version that produced it so a stale
    # state file from a future-uninstalled Memee is identifiable.
    try:
        from memee import __version__ as _v
        payload = {**payload, "version": _v}
    except Exception:
        payload = {**payload, "version": "unknown"}

    payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    tmp_path: Path | None = None
    try:
        # delete=False so we can hand it to os.replace; we clean up on
        # any failure path below.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8",
            dir=path.parent, prefix=".state.", suffix=".json.tmp",
            delete=False,
        ) as fh:
            json.dump(payload, fh, sort_keys=True, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
            tmp_path = Path(fh.name)
        os.replace(tmp_path, path)
        tmp_path = None
        return True
    except OSError as e:
        logger.debug("bar.state: write failed (%s)", e)
        return False
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass


def update_state(patch: dict[str, Any]) -> bool:
    """Read-modify-write: merge ``patch`` into the current state.

    Top-level keys in ``patch`` replace the same keys in the current
    state (deep merge would surprise — hooks set whole sub-objects like
    ``last_brief`` at a time). Use ``write_state`` directly when the
    caller has the full payload.
    """
    state = read_state()
    state.update(patch)
    return write_state(state)


def record_brief(
    *,
    project: str | None,
    task: str,
    bullets: int,
    tokens: int,
    totals: dict[str, int] | None = None,
    impact: dict[str, int] | None = None,
    update: dict[str, Any] | None = None,
) -> bool:
    """Hook helper: stamp ``last_brief`` (and optional ``totals``,
    ``impact``, ``update``).

    Called from the ``memee brief`` CLI command after a successful
    routed briefing. Failure is silent — the brief itself already
    landed; the state stamp is best-effort.

    v2.4.4: ``impact`` carries 7-day rolling summary that the menubar
    miniapp renders as "How Memee helped" (mistakes avoided / patterns
    applied / canon growth). ``update`` carries the result of
    ``update_check.check()`` so the bar can surface "↑ v X.Y.Z
    available" without making its own network call. Both are
    earned-visibility channels: empty / null counters → rows hidden in
    the popover.
    """
    payload: dict[str, Any] = {
        "last_brief": {
            "ts": datetime.now(timezone.utc).isoformat(),
            "project": project or "",
            "task": (task or "")[:120],  # cap so popover stays sane
            "bullets": int(bullets),
            "tokens": int(tokens),
        }
    }
    if totals:
        payload["totals"] = dict(totals)
    if impact:
        payload["impact"] = dict(impact)
    if update:
        payload["update"] = dict(update)
    return update_state(payload)


def record_learn(*, auto: bool, outcome: str | None = None) -> bool:
    """Hook helper: stamp ``last_learn`` after a Stop-hook learn cycle."""
    return update_state(
        {
            "last_learn": {
                "ts": datetime.now(timezone.utc).isoformat(),
                "auto": bool(auto),
                "outcome": outcome or "",
            }
        }
    )
