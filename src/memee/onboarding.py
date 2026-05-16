"""First-week-no-silence onboarding receipt arc (v2.2.0, M2).

Right after ``memee setup`` a new user gets nothing back — silence until
they accidentally bump into a memory the agent already had. This module
fixes the silence with a tightly-bounded **3-receipt arc** tied to real
events, NOT a tutorial or a countdown:

    1. Day 1 SessionStart (after setup):
       ``Memee is listening. No memories yet.``
    2. First memory recorded:
       ``Memee learned "<title>" from this session.``
    3. First reuse (typically days 3-5):
       ``Memee reused "<title>" — first time it saved you a re-explain.``

Then the arc **ends**. There is no receipt #4. After 7 days from setup
OR after stage 3 fires (whichever first), the per-project marker is
flipped to ``completed=true`` and the module returns ``None`` forever
for that project.

Per-project keys are deliberate (UX/architect risk #3): a developer
using Memee in 5 client repos sees the arc once *per repo*, not once
globally — otherwise consultants who set up Memee on Project A and then
work on Project B for months would never get a receipt for B even
though it's their first session there.

Marker shape (``~/.memee/onboarding.json``)::

    {
      "setup_at": "<iso>",        # global anchor: when first project
                                  # marked complete. 7-day expiry uses
                                  # the per-project setup time.
      "version": "2.2.0",
      "by_project": {
        "<abs_path>": {
          "setup_at":            "<iso>",  # per-project anchor
          "first_memory_seen":   null,     # iso when memory_count > 0
          "first_reuse_seen":    null,     # iso when reuse_count > 0
          "completed":           false     # arc ended (success or expiry)
        }
      }
    }

Design constraints (mirroring ``digest.py`` and ``update_check.py``):

* **Silent failure**. Every read/write/DB error → ``None``. The briefing
  must never break.
* **Killable**. ``MEMEE_NO_ONBOARDING=1`` (any non-empty value) disables
  the whole thing.
* **No new deps**. ``json`` + stdlib datetime only.
* **Local-only**. Marker lives at ``~/.memee/onboarding.json``;
  nothing leaves the machine.
* **LRU-capped at 50 projects**. A consultant working across many repos
  doesn't need the marker file to grow unbounded; oldest-by-setup_at
  is evicted when the cap is hit.

This is M2 of v2.2.0. Coordination requirement for M6 (the prepend
orchestrator): when ``is_onboarding_active(project_path)`` is True for
the current project, the weekly digest MUST be suppressed for that
session. Onboarding outranks digest, period — a new user with an empty
DB doesn't want a "0 memories applied this week" report.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

MARKER_PATH = Path.home() / ".memee" / "onboarding.json"
ARC_DURATION_DAYS = 7
PROJECT_CAP = 50
MARKER_VERSION = "2.2.0"


# ── Marker I/O ──────────────────────────────────────────────────────────────


def _read_marker() -> dict | None:
    """Best-effort marker read. Returns None on missing file / parse
    error / wrong shape — every failure mode collapses to "no
    onboarding"."""
    try:
        with open(MARKER_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("by_project"), dict):
        # Legacy / corrupt shape — refuse rather than crash later.
        return None
    return data


def _write_marker(data: dict) -> None:
    """Best-effort marker write — never raise. Marker is a hint; if the
    HOME dir is read-only the arc just doesn't fire next session."""
    try:
        MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MARKER_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def _parse_iso(s: object) -> datetime | None:
    """Parse an ISO-8601 string back into an aware datetime. Returns
    None on garbage so corrupt entries fall through to "no
    onboarding"."""
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Treat naive timestamps as UTC — older versions may have
        # written naive ISO; don't crash on legacy markers.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _resolve_project(project_path: str | None) -> str:
    """Resolve a project path to an absolute string.

    Resolution chain (v2.2.2 fix for F4):

      1. Explicit ``project_path`` arg if given
      2. ``$CLAUDE_PROJECT_DIR`` env var (Claude Code provides it)
      3. ``git rev-parse --show-toplevel`` from CWD (2s timeout)
      4. ``Path.cwd()`` (last resort)

    Before this fix, running ``memee setup`` from ``~`` keyed the
    onboarding marker off ``~`` and the first-week arc never fired in
    the user's actual project. The fix is shared by every consumer of
    project resolution (setup, briefing prepends, onboarding stages).
    """
    if project_path:
        try:
            return str(Path(project_path).resolve(strict=False))
        except OSError:
            return str(project_path)

    env_path = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_path:
        try:
            return str(Path(env_path).resolve(strict=False))
        except OSError:
            return env_path

    try:
        import subprocess
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if proc.returncode == 0:
            top = proc.stdout.strip()
            if top:
                try:
                    return str(Path(top).resolve(strict=False))
                except OSError:
                    return top
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        return str(Path.cwd().resolve(strict=False))
    except OSError:
        return str(Path.cwd())


def _evict_lru_if_needed(by_project: dict) -> None:
    """In-place LRU eviction by ``setup_at``. The cap is 50 projects;
    when exceeded we drop the oldest entries first. Best-effort: any
    entry without a parseable ``setup_at`` is treated as the oldest
    (sorted to the front), so corrupt entries get evicted preferentially.
    """
    if len(by_project) <= PROJECT_CAP:
        return

    def sort_key(item: tuple[str, object]) -> datetime:
        _, value = item
        if not isinstance(value, dict):
            return datetime.min.replace(tzinfo=timezone.utc)
        ts = _parse_iso(value.get("setup_at"))
        return ts or datetime.min.replace(tzinfo=timezone.utc)

    sorted_items = sorted(by_project.items(), key=sort_key)
    # Keep the newest PROJECT_CAP entries.
    keep = dict(sorted_items[-PROJECT_CAP:])
    by_project.clear()
    by_project.update(keep)


# ── DB helpers ──────────────────────────────────────────────────────────────


def _query_latest_memory_title(
    session, abs_project_path: str | None = None
) -> tuple[str | None, bool]:
    """Return the title of the most recently created memory.

    Returns ``(title, scoped_to_project)``. ``scoped_to_project`` is
    True when the title belongs to a memory linked to
    ``abs_project_path``; False when no project memories exist and the
    fallback global query found a memory belonging to a different
    project.

    v2.2.2 (F5): the first version queried globally regardless of
    project, which meant a consultant working in repo B would see stage
    2 receipts naming a memory recorded in repo A. Now we prefer
    project-scoped queries via the ``ProjectMemory`` link and fall
    back to a global query only when no project-scoped memories exist.
    The caller can append "(from another project)" when the fallback
    was used so the receipt stays honest.
    """
    from memee.storage.models import Memory, Project, ProjectMemory

    if abs_project_path:
        proj = (
            session.query(Project)
            .filter(Project.path == abs_project_path)
            .first()
        )
        if proj is not None:
            mem = (
                session.query(Memory)
                .join(ProjectMemory, ProjectMemory.memory_id == Memory.id)
                .filter(ProjectMemory.project_id == proj.id)
                .order_by(Memory.created_at.desc())
                .limit(1)
                .first()
            )
            if mem is not None:
                title = getattr(mem, "title", None)
                if isinstance(title, str) and title.strip():
                    return (title.strip(), True)

    # Fall back to the global query so a fresh project that hasn't yet
    # recorded its first memory still gets *some* signal from a sibling
    # project — but flag it so the renderer can be honest about origin.
    mem = (
        session.query(Memory)
        .order_by(Memory.created_at.desc())
        .limit(1)
        .first()
    )
    if mem is None:
        return (None, False)
    title = getattr(mem, "title", None)
    if not isinstance(title, str) or not title.strip():
        return (None, False)
    return (title.strip(), False)


def _query_latest_reuse_title(
    session, abs_project_path: str | None = None
) -> tuple[str | None, bool]:
    """Return the title of the memory referenced by the most recent
    KNOWLEDGE_REUSED ImpactEvent.

    Returns ``(title, scoped_to_project)``. v2.2.2 (F5): mirrors
    ``_query_latest_memory_title`` — prefer project-scoped events,
    fall back to global, flag origin so the caller can be honest.
    """
    from memee.engine.impact import ImpactEvent, ImpactType
    from memee.storage.models import Memory, Project

    if abs_project_path:
        proj = (
            session.query(Project)
            .filter(Project.path == abs_project_path)
            .first()
        )
        if proj is not None:
            event = (
                session.query(ImpactEvent)
                .filter(
                    ImpactEvent.impact_type == ImpactType.KNOWLEDGE_REUSED.value,
                    ImpactEvent.project_id == proj.id,
                )
                .order_by(ImpactEvent.created_at.desc())
                .limit(1)
                .first()
            )
            if event is not None and event.memory_id:
                mem = session.get(Memory, event.memory_id)
                if mem is not None:
                    title = getattr(mem, "title", None)
                    if isinstance(title, str) and title.strip():
                        return (title.strip(), True)

    event = (
        session.query(ImpactEvent)
        .filter(ImpactEvent.impact_type == ImpactType.KNOWLEDGE_REUSED.value)
        .order_by(ImpactEvent.created_at.desc())
        .limit(1)
        .first()
    )
    if event is None:
        return (None, False)
    mem = session.get(Memory, event.memory_id) if event.memory_id else None
    if mem is None:
        return (None, False)
    title = getattr(mem, "title", None)
    if not isinstance(title, str) or not title.strip():
        return (None, False)
    return (title.strip(), False)


def _has_any_memory(session) -> bool:
    """Cheap "is there any memory in the DB" probe — used by stage 1 to
    decide whether to advance immediately to stage 2."""
    from memee.storage.models import Memory

    return session.query(Memory.id).limit(1).first() is not None


def _has_any_reuse(session) -> bool:
    """Cheap "has any KNOWLEDGE_REUSED event landed" probe."""
    from memee.engine.impact import ImpactEvent, ImpactType

    return (
        session.query(ImpactEvent.id)
        .filter(ImpactEvent.impact_type == ImpactType.KNOWLEDGE_REUSED.value)
        .limit(1)
        .first()
        is not None
    )


# ── Public API ──────────────────────────────────────────────────────────────


def mark_setup_complete(project_path: str | None = None) -> None:
    """Write/update the onboarding marker for ``project_path``.

    Called from the setup wizard right after hooks merge into
    settings.json. Initialises the per-project entry with all three
    stage timestamps set to ``None``. Safe to call multiple times — a
    re-setup of an already-marked project is a no-op (we don't reset
    progress). Every error swallowed.
    """
    if os.environ.get("MEMEE_QUIET") or os.environ.get("MEMEE_NO_ONBOARDING"):
        return
    try:
        abs_path = _resolve_project(project_path)
        now_iso = datetime.now(timezone.utc).isoformat()

        existing = _read_marker() or {}
        by_project = existing.get("by_project")
        if not isinstance(by_project, dict):
            by_project = {}

        if abs_path not in by_project or not isinstance(
            by_project.get(abs_path), dict
        ):
            by_project[abs_path] = {
                "setup_at": now_iso,
                "first_memory_seen": None,
                "first_reuse_seen": None,
                "completed": False,
            }
        # Re-running setup on a project that already has a marker leaves
        # progress alone — we don't re-arm the arc.

        _evict_lru_if_needed(by_project)

        marker = {
            "setup_at": existing.get("setup_at") or now_iso,
            "version": MARKER_VERSION,
            "by_project": by_project,
        }
        _write_marker(marker)
    except Exception:
        # Best-effort. Never block setup on an onboarding marker IO error.
        return


def _get_project_entry(
    marker: dict, abs_path: str
) -> dict | None:
    """Return the per-project entry, or None if missing / wrong shape."""
    by_project = marker.get("by_project")
    if not isinstance(by_project, dict):
        return None
    entry = by_project.get(abs_path)
    if not isinstance(entry, dict):
        return None
    return entry


def _save_entry(marker: dict, abs_path: str, entry: dict) -> None:
    """Persist a mutated entry back to disk. Best-effort."""
    by_project = marker.setdefault("by_project", {})
    by_project[abs_path] = entry
    _write_marker(marker)


def format_onboarding_notice(project_path: str | None = None) -> str | None:
    """Return the next-stage receipt for ``project_path``, or None.

    See module docstring for the 3-receipt arc. All errors swallowed →
    None. Honours ``MEMEE_NO_ONBOARDING=1``.

    Stage transitions are atomic: when stage 1 reads and finds memories
    already exist, it sets ``first_memory_seen=now`` AND renders stage
    2 in the same call. Likewise stage 2 → stage 3 if a reuse has
    landed. No "skip" — the user always sees the next milestone they
    actually hit.
    """
    if os.environ.get("MEMEE_QUIET") or os.environ.get("MEMEE_NO_ONBOARDING"):
        return None
    try:
        return _format_onboarding_notice_inner(project_path)
    except Exception:
        return None


def _format_onboarding_notice_inner(project_path: str | None) -> str | None:
    """Inner implementation — wrapped above so any exception collapses
    to None."""
    abs_path = _resolve_project(project_path)
    marker = _read_marker()
    if marker is None:
        return None

    entry = _get_project_entry(marker, abs_path)
    if entry is None:
        # No marker for this project — the user hasn't set Memee up
        # here, so there's no arc to render.
        return None

    if entry.get("completed"):
        return None

    setup_at = _parse_iso(entry.get("setup_at"))
    if setup_at is None:
        # Corrupt entry — refuse rather than render with a bogus age.
        return None

    now = datetime.now(timezone.utc)
    age = now - setup_at

    # ── Expiry: 7 days regardless of stage. Marker flips to completed
    #    so we never re-render and the project is just "post-onboarding"
    #    forever after.
    if age >= timedelta(days=ARC_DURATION_DAYS):
        entry["completed"] = True
        _save_entry(marker, abs_path, entry)
        return None

    # ── Open a session for live queries. Any DB error → no notice.
    try:
        from memee.storage.database import get_engine, get_session, init_db

        engine = init_db(get_engine())
        session = get_session(engine)
    except Exception:
        return None

    try:
        first_memory_seen = _parse_iso(entry.get("first_memory_seen"))
        first_reuse_seen = _parse_iso(entry.get("first_reuse_seen"))
        now_iso = now.isoformat()

        # ── Stage 3: reuse already landed → arc ends here. Mark
        #    completed and return None. (We rendered stage 3 the LAST
        #    time we saw a reuse — see stage 2 transition below.)
        if first_reuse_seen is not None:
            entry["completed"] = True
            _save_entry(marker, abs_path, entry)
            return None

        # ── Stage 2 active: memory recorded, reuse not yet seen.
        #    Check if a reuse has just landed; if so, advance to stage
        #    3 and render the reuse message.
        #    v2.2.2 (F5): query project-scoped first; fall back to
        #    global with a "(from another project)" suffix so the
        #    receipt is honest about origin.
        if first_memory_seen is not None:
            if _has_any_reuse(session):
                title, scoped = _query_latest_reuse_title(session, abs_path)
                if title:
                    entry["first_reuse_seen"] = now_iso
                    _save_entry(marker, abs_path, entry)
                    suffix = "" if scoped else " (from another project)"
                    return (
                        f'Memee reused "{title}"{suffix} — first time it '
                        f"saved you a re-explain."
                    )
            # No reuse yet — render stage 2 with the latest memory
            # title. We re-query each session so the title stays
            # current with whatever the user just recorded.
            title, scoped = _query_latest_memory_title(session, abs_path)
            if title:
                suffix = "" if scoped else " (from another project)"
                return f'Memee learned "{title}"{suffix} from this session.'
            return "Memee is listening. No memories yet."

        # ── Stage 1 active: no memory yet recorded for this project.
        if _has_any_memory(session):
            title, scoped = _query_latest_memory_title(session, abs_path)
            if title:
                entry["first_memory_seen"] = now_iso
                if _has_any_reuse(session):
                    reuse_title, reuse_scoped = _query_latest_reuse_title(
                        session, abs_path
                    )
                    if reuse_title:
                        entry["first_reuse_seen"] = now_iso
                        _save_entry(marker, abs_path, entry)
                        suffix = "" if reuse_scoped else " (from another project)"
                        return (
                            f'Memee reused "{reuse_title}"{suffix} — first '
                            f"time it saved you a re-explain."
                        )
                _save_entry(marker, abs_path, entry)
                suffix = "" if scoped else " (from another project)"
                return f'Memee learned "{title}"{suffix} from this session.'

        return "Memee is listening. No memories yet."
    finally:
        try:
            session.close()
        except Exception:
            pass


def is_onboarding_active(project_path: str | None = None) -> bool:
    """True iff a marker exists for this project AND not yet completed
    AND age < 7 days.

    Used by the prepend orchestrator (M6) to suppress the weekly digest
    while onboarding is active — never both at once. Honours
    ``MEMEE_NO_ONBOARDING=1`` (returns False when the kill switch is
    set, since the user has explicitly opted out of the arc).

    Errors swallowed → False.
    """
    if os.environ.get("MEMEE_QUIET") or os.environ.get("MEMEE_NO_ONBOARDING"):
        return False
    try:
        abs_path = _resolve_project(project_path)
        marker = _read_marker()
        if marker is None:
            return False
        entry = _get_project_entry(marker, abs_path)
        if entry is None:
            return False
        if entry.get("completed"):
            return False
        setup_at = _parse_iso(entry.get("setup_at"))
        if setup_at is None:
            return False
        age = datetime.now(timezone.utc) - setup_at
        return age < timedelta(days=ARC_DURATION_DAYS)
    except Exception:
        return False
