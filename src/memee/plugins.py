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


def _default_current_user_id(*args, **kwargs) -> str | None:
    """OSS: no identity concept. Returns None regardless of args so that
    callers can pass a session, a request, or nothing — whichever is handy.
    """
    return None


def _default_visible_memories(session, base_query=None, user_id=None):
    """OSS: a user sees every memory they recorded locally.

    ``base_query`` is optional for backward compatibility. When callers pass
    a pre-built query (with other filters already applied) we return it as
    the visibility-applied query so the engine can keep composing without
    materialising ids up front.
    """
    from memee.storage.models import Memory
    return base_query if base_query is not None else session.query(Memory)


def is_multi_user_active() -> bool:
    """True iff the `memee-team` package (or another integrator) has
    registered a non-default ``visible_memories`` hook.

    Used by OSS engine modules to decide whether to apply scoping by default
    (when the hook is registered we MUST apply it on every Memory query —
    bypassing it is a tenancy leak). In single-user OSS this returns False
    and every query stays unfiltered, so there is no behaviour change.
    """
    fn = _HOOKS.get("visible_memories")
    return fn is not None and fn is not _default_visible_memories


def apply_visibility(session, base_query, user_id=None):
    """Apply the registered ``visible_memories`` hook to ``base_query``.

    This is the single entry point every engine path should go through before
    returning Memory rows. If no multi-user hook is registered, returns the
    query unchanged — zero cost in OSS.

    Contract: a registered hook MUST compose with the supplied ``base_query``
    (i.e. the result must be a subset of base_query). Hooks that build a
    fresh, unrelated query throw away every filter the engine already
    applied — candidate set, memory_type, maturity — and silently leak
    unrelated rows past the search ranker. We enforce this by intersecting
    the hook's output with ``base_query`` after the call. The intersection
    is cheap (an extra ``id IN (subquery)`` clause SQLite optimises into a
    semi-join) and is the most permissive enforcement strategy: bad hooks
    don't crash, but they can't widen the candidate set either.
    """
    if not is_multi_user_active():
        return base_query
    fn = _HOOKS["visible_memories"]
    try:
        result = fn(session, base_query=base_query, user_id=user_id)
    except TypeError:
        # Back-compat: older hooks only accept (session, base_query)
        try:
            result = fn(session, base_query=base_query)
        except TypeError:
            # Even older: (session) → returns a Memory query we then intersect.
            result = fn(session)

    if result is None:
        # Hook explicitly opted out of any filtering; honour base_query.
        return base_query

    # Intersect hook output with base_query to enforce composition. If the
    # hook already nested base_query the intersection is a no-op (SQLite
    # collapses ``id IN (SELECT id FROM x WHERE id IN (...))`` cleanly).
    from memee.storage.models import Memory
    return base_query.filter(
        Memory.id.in_(result.with_entities(Memory.id))
    )


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
