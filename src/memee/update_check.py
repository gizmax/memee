"""Passive update-check against PyPI.

People install Memee once and forget. They don't run ``memee doctor``.
We can't ship a background updater (intrusive, surprises mid-task) and
can't auto-upgrade (touches their pipx env). What we *can* do is hit
PyPI's read-only JSON endpoint once a day, cache the answer, and surface
"a new version is out" through channels users actually look at:

- The hook briefing the agent receives at SessionStart (most reach —
  the agent passes it on conversationally).
- The MCP server startup banner (visible to clients that surface it).
- ``memee --version`` and ``memee doctor`` (for users who *do* check).

Design constraints:

- **Silent on the network**. Failures (offline, DNS, 5xx, slow connection)
  must never produce visible noise. The check is best-effort intel, not
  a feature gate.
- **Cheap**. 24-hour cache, 3-second timeout, single GET. We never call
  out to PyPI more than once a day per user.
- **Killable**. ``MEMEE_NO_UPDATE_CHECK=1`` (or any non-empty value)
  disables the network call entirely and short-circuits to "no update".
- **No new deps**. ``urllib`` from stdlib; nothing else.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

PYPI_JSON_URL = "https://pypi.org/pypi/memee/json"
CACHE_PATH = Path.home() / ".memee" / "update_check.json"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h
HTTP_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class UpdateStatus:
    """Result of a single check.

    ``available`` is True only when we successfully reached PyPI (or hit a
    fresh cache) and the latest version is strictly newer than the
    running one. Anything else (network error, parse error, kill switch,
    can't determine local version) → False, ``latest is None``, callers
    should stay silent.
    """
    available: bool
    current: str
    latest: str | None
    checked_at: float
    source: str  # "cache" | "network" | "disabled" | "unknown"


# ── Version comparison ──────────────────────────────────────────────────────


def _parse(version: str | None) -> tuple[int, ...] | None:
    """Parse ``"2.0.3"`` → ``(2, 0, 3)``. Stops at the first non-numeric
    component so dev/rc suffixes don't poison the comparison — for an
    update *prompt* we'd rather miss a pre-release than over-prompt on
    one. Returns None on garbage input.
    """
    if not version or not isinstance(version, str):
        return None
    parts: list[int] = []
    for chunk in version.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def _is_newer(current: str | None, latest: str | None) -> bool:
    a = _parse(current)
    b = _parse(latest)
    if a is None or b is None:
        return False
    return b > a


# ── Cache I/O ───────────────────────────────────────────────────────────────


def _read_cache() -> dict | None:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_cache(payload: dict) -> None:
    """Best-effort cache write — never raise. The cache is a hint; if the
    HOME dir is read-only or the disk is full, doctor still works."""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError:
        pass


# ── Network ─────────────────────────────────────────────────────────────────


def _fetch_latest_from_pypi(timeout: float = HTTP_TIMEOUT_SECONDS) -> str | None:
    """Single GET to PyPI's read-only JSON endpoint, return the latest
    version string or None on any failure. We deliberately swallow every
    exception type: this function must not raise.
    """
    try:
        # Defer the import so the module stays light when the check is
        # disabled — also means a broken urllib (vendored Pythons!) never
        # crashes ``import memee``.
        from urllib.request import Request, urlopen

        req = Request(
            PYPI_JSON_URL,
            headers={
                # Be a courteous client. PyPI logs UA strings.
                "User-Agent": "memee-update-check",
                "Accept": "application/json",
            },
        )
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    info = data.get("info") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        return None
    version = info.get("version")
    return version if isinstance(version, str) else None


# ── Public API ──────────────────────────────────────────────────────────────


def check(*, force: bool = False) -> UpdateStatus:
    """Return the current update status.

    ``force=True`` ignores the 24h cache and the kill switch; used by
    ``memee doctor`` when the user explicitly asks for fresh data.
    """
    from memee import __version__ as current

    if not force and os.environ.get("MEMEE_NO_UPDATE_CHECK"):
        return UpdateStatus(
            available=False, current=current, latest=None,
            checked_at=time.time(), source="disabled",
        )

    now = time.time()

    # Cache hit within TTL → use it. Cache stale → refresh in-line; one
    # extra ~200 ms request once a day is fine.
    if not force:
        cached = _read_cache()
        if cached and isinstance(cached.get("checked_at"), (int, float)):
            age = now - cached["checked_at"]
            if age >= 0 and age < CACHE_TTL_SECONDS:
                latest = cached.get("latest") if isinstance(cached.get("latest"), str) else None
                return UpdateStatus(
                    available=_is_newer(current, latest),
                    current=current,
                    latest=latest,
                    checked_at=cached["checked_at"],
                    source="cache",
                )

    latest = _fetch_latest_from_pypi()
    payload = {"current": current, "latest": latest, "checked_at": now}
    _write_cache(payload)

    if latest is None:
        return UpdateStatus(
            available=False, current=current, latest=None,
            checked_at=now, source="unknown",
        )
    return UpdateStatus(
        available=_is_newer(current, latest),
        current=current,
        latest=latest,
        checked_at=now,
        source="network",
    )


def format_notice(status: UpdateStatus, *, prefix: str = "") -> str | None:
    """Render a one-line notice for surfacing in briefings, MCP banners,
    and CLI version output. Returns None when there's nothing to say.
    """
    if not status.available or not status.latest:
        return None
    return (
        f"{prefix}Memee {status.current} → {status.latest} available. "
        f"Run `pipx upgrade memee`."
    )
