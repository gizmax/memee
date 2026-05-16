"""Memee menubar miniapp (v2.3.0).

Optional, macOS-first GUI surface. Lives separately from the core engine
so that ``pipx install memee`` stays lean: GUI deps (``rumps``,
``watchdog``, ``Pillow``) are pulled in via the ``[bar]`` extras only.

The miniapp is a *watching* surface — read-only by design. All writes
to the knowledge base go through CLI / MCP / hooks. The popover shows
recent activity (last brief, recall counts, canon size, last sync)
and offers three click-actions: open last brief, run dream, quit.

See ``docs/bar.md`` (when written) or ``CHANGELOG.md`` v2.3.0 for the
rationale (autoresearch on presence/liveness signals, surveyed 14 tools).
"""

from memee.bar.state import (
    STATE_PATH,
    read_state,
    record_brief,
    record_learn,
    state_path,
    update_state,
    write_state,
)

__all__ = [
    "STATE_PATH",
    "state_path",
    "read_state",
    "write_state",
    "update_state",
    "record_brief",
    "record_learn",
]
