"""Plugin hooks — extension points the paid `memee-team` package plugs into.

The OSS `memee` package is a single-user product. Multi-user concerns
(identity, team/org scoping, promotion, audit log) live in the `memee-team`
package, which is proprietary and licence-gated.

On import, `memee_team` calls `memee.plugins.register(...)` to override
the single-user defaults defined here. If `memee-team` is not installed,
the defaults in this module are used and everything operates as a
single-user product.

Available hooks:
    - `current_user_id` — returns an opaque user identifier or None
    - `visible_memories` — filter a Memory query to what the user may see
    - `promote` — move a memory between scopes (team/org)
    - `can_promote` — boolean guard for whether promotion is allowed
    - `on_record` — post-record hook (audit log, notification, etc.)

Adding a hook:
    ``` memee.plugins.register("promote", my_promote_fn) ```

Reading a hook (OSS callsites):
    ``` memee.plugins.get("promote") ```  # returns the registered impl or default
"""

from __future__ import annotations

from typing import Any, Callable

_HOOKS: dict[str, Callable[..., Any]] = {}


def register(hook_name: str, impl: Callable[..., Any]) -> None:
    """Register or replace a hook implementation."""
    _HOOKS[hook_name] = impl


def get(hook_name: str) -> Callable[..., Any] | None:
    """Return the registered implementation or None."""
    return _HOOKS.get(hook_name)


def call(hook_name: str, *args, default=None, **kwargs):
    """Call a hook; return `default` if not registered."""
    fn = _HOOKS.get(hook_name)
    if fn is None:
        return default() if callable(default) else default
    return fn(*args, **kwargs)


# ── Single-user defaults ──


def _default_current_user_id() -> str | None:
    """OSS: no identity concept. Returns None."""
    return None


def _default_visible_memories(session, base_query=None):
    """OSS: a user sees every memory they recorded locally."""
    from memee.storage.models import Memory
    return base_query if base_query is not None else session.query(Memory)


class LicenseRequiredError(Exception):
    """Raised when an operation requires a feature only memee-team provides."""


def _default_promote(session, memory_id: str, target_scope: str, user_id=None):
    """OSS: promotion across scopes is a team-tier feature."""
    raise LicenseRequiredError(
        "Promoting memories to team or org scope requires Memee Team.\n"
        "The OSS version is single-user: every memory is personal.\n"
        "Get a licence at https://memee.eu/#pricing and install `memee-team`."
    )


def _default_can_promote(memory, target_scope: str, user=None) -> bool:
    """OSS: no promotion available."""
    return False


def _default_on_record(memory) -> None:
    """OSS: no-op. memee-team hooks audit trail here."""
    return None


# Register defaults at module load.
register("current_user_id", _default_current_user_id)
register("visible_memories", _default_visible_memories)
register("promote", _default_promote)
register("can_promote", _default_can_promote)
register("on_record", _default_on_record)
