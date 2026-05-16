"""Per-brief archive (v2.4.1).

When the menubar miniapp's "Open last brief" action fires, the user
expects to see the actual briefing — not `state.json`. v2.3.0 punted on
this with a TODO. v2.4.1 closes it: every successful ``memee brief``
appends a small Markdown file under ``~/.memee/briefs/`` so the bar
app (and any curious operator with a text editor) can read the real
content that just went to the agent.

The archive is also a quiet visibility surface for the user goal of
"občas viditelné" — a tail of the last few briefings is the equivalent
of a Slack channel of "here's what Memee just told the agent." No
push, no notification — pure pull.

Conventions:
  * One file per brief: ``~/.memee/briefs/<UTC-ISO>.md``.
  * Rotated to the last ``BRIEFS_RETAIN`` (default 50) — oldest files
    deleted by the writer after each successful write. Small footprint
    (<1 MB at default settings) and the rotation is amortised so the
    SessionStart hook stays fast.
  * Reads are race-free against the writer because each file is
    rewritten only as a tempfile + ``os.replace`` (see
    ``state.write_state`` for the same pattern).
  * Skipped when ``MEMEE_QUIET=1`` (master) or
    ``MEMEE_NO_BRIEF_ARCHIVE=1`` (per-channel) is set.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from memee.bar.state import _memee_home


logger = logging.getLogger(__name__)

BRIEFS_DIRNAME = "briefs"
BRIEFS_RETAIN = 50
# Exclude dots from slugs so a task like ``../etc/passwd`` can never
# survive into the filename. dashes and underscores are fine.
_SAFE_TASK = re.compile(r"[^A-Za-z0-9_-]+")


def briefs_dir() -> Path:
    """Return ``<MEMEE_HOME>/briefs``."""
    return _memee_home() / BRIEFS_DIRNAME


def _archive_suppressed() -> bool:
    return bool(
        os.environ.get("MEMEE_QUIET")
        or os.environ.get("MEMEE_NO_BRIEF_ARCHIVE")
    )


def write_brief(
    *,
    task: str,
    body: str,
    project: str | None = None,
) -> Path | None:
    """Persist a single briefing to the archive.

    Returns the written path on success, ``None`` on suppression or
    failure (logged at debug). Failures are never raised — the brief
    already landed; the archive is decorative.

    The filename is ``<UTC-timestamp>-<task-slug>.md`` so an operator
    listing the dir sees what was briefed when. Task slug is sanitised
    to ASCII alnum/dash; long tasks are truncated to 40 chars to keep
    paths sane on Windows.
    """
    if _archive_suppressed():
        return None
    if not body or not body.strip():
        return None

    target_dir = briefs_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug("bar.briefs: mkdir failed (%s)", e)
        return None

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    task_slug = _SAFE_TASK.sub("-", (task or "").strip())[:40].strip("-") or "brief"
    target = target_dir / f"{ts}-{task_slug}.md"

    # Front matter so a reader knows what they're looking at without
    # parsing the body shape.
    header = (
        "---\n"
        f"task: {task or ''}\n"
        f"project: {project or ''}\n"
        f"written_at: {datetime.now(timezone.utc).isoformat()}\n"
        "---\n\n"
    )

    try:
        # Same tempfile + replace pattern as bar/state.py for atomicity.
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8",
            dir=target_dir, prefix=".brief.", suffix=".md.tmp",
            delete=False,
        ) as fh:
            fh.write(header)
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
            tmp_path = Path(fh.name)
        os.replace(tmp_path, target)
    except OSError as e:
        logger.debug("bar.briefs: write failed (%s)", e)
        return None

    _rotate(target_dir)
    return target


def latest_brief() -> Path | None:
    """Return the path to the most recent archived brief, or ``None``."""
    target_dir = briefs_dir()
    if not target_dir.exists():
        return None
    try:
        candidates = [
            p for p in target_dir.iterdir()
            if p.is_file() and p.suffix == ".md"
        ]
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _rotate(target_dir: Path, retain: int = BRIEFS_RETAIN) -> int:
    """Delete oldest archive files beyond ``retain``. Returns count removed.

    Cheap: typical retain is 50 files, ``iterdir`` + sort + drop tail.
    Failure is silent — the briefing still archived; rotation is
    housekeeping.
    """
    try:
        files = [
            p for p in target_dir.iterdir()
            if p.is_file() and p.suffix == ".md"
        ]
    except OSError:
        return 0
    if len(files) <= retain:
        return 0
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in files[retain:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed
