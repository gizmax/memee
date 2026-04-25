"""Regression tests for the R7 multi-tenancy + perf helpers.

Three new behaviour-bearing helpers landed in R7:

* ``plugins.is_multi_user_active`` / ``plugins.apply_visibility`` — let
  ``search_memories`` apply a registered ``visible_memories`` hook on
  every query, even when the caller forgot to pass ``scope`` or
  ``user_id``. Tenancy leak prevention.
* ``search._db_has_any_embeddings`` — short-circuit so DBs without any
  embedded memories skip the ~5 s ``sentence_transformers`` cold-start.
* ``mcp_server._with_session`` — async/sync decorator that closes the
  injected session on both happy and exception paths.

These cover regressions, not new features. If any of them stop holding,
the file should fail loudly.
"""

from __future__ import annotations

import asyncio

import pytest

from memee import plugins as _plugins
from memee.storage.models import Memory, MemoryType


# ── Tenancy: apply_visibility ─────────────────────────────────────────


@pytest.fixture
def restore_plugins():
    """Snapshot/restore plugin registry so individual tests can register
    a multi-user hook without leaking it into the rest of the suite."""
    saved = dict(_plugins._HOOKS)
    yield
    _plugins._HOOKS.clear()
    _plugins._HOOKS.update(saved)


def test_apply_visibility_is_no_op_in_oss(session):
    """Single-user OSS: no hook registered → query is returned unchanged."""
    base = session.query(Memory)
    result = _plugins.apply_visibility(session, base, user_id=None)
    assert result is base
    assert _plugins.is_multi_user_active() is False


def test_apply_visibility_runs_hook_when_registered(session, restore_plugins):
    """When a multi-user hook is registered, search must funnel through it
    even if the caller didn't pass a user_id (the default in OSS callsites
    is ``user_id=None``). Without this, every MCP / CLI path that forgot
    the scope kwarg leaked across tenants.
    """
    visible_a = Memory(
        type=MemoryType.PATTERN.value,
        title="Tenant A pattern A",
        content="visible only to tenant A",
        tags=["t"],
        confidence_score=0.5,
    )
    invisible_b = Memory(
        type=MemoryType.PATTERN.value,
        title="Tenant B pattern B",
        content="hidden from tenant A",
        tags=["t"],
        confidence_score=0.5,
    )
    session.add_all([visible_a, invisible_b])
    session.commit()

    def fake_visible(session, base_query=None, user_id=None):
        # tenant A only sees their own memory regardless of caller
        q = base_query if base_query is not None else session.query(Memory)
        return q.filter(Memory.title.like("Tenant A%"))

    _plugins.register("visible_memories", fake_visible)
    assert _plugins.is_multi_user_active() is True

    base = session.query(Memory)
    filtered = _plugins.apply_visibility(session, base, user_id=None)
    titles = [m.title for m in filtered.all()]
    assert titles == ["Tenant A pattern A"]


def test_apply_visibility_back_compat_old_signature(session, restore_plugins):
    """A legacy hook that only accepts ``(session)`` still works — the
    visibility wrapper degrades gracefully into an `IN (subquery)` filter.
    """
    a = Memory(
        type=MemoryType.PATTERN.value,
        title="Allowed pattern row",
        content="x",
        tags=["t"],
    )
    b = Memory(
        type=MemoryType.PATTERN.value,
        title="Forbidden pattern row",
        content="y",
        tags=["t"],
    )
    session.add_all([a, b])
    session.commit()

    def legacy_hook(session_):
        return session_.query(Memory).filter(Memory.title.like("Allowed%"))

    _plugins.register("visible_memories", legacy_hook)

    base = session.query(Memory)
    filtered = _plugins.apply_visibility(session, base, user_id=None)
    titles = sorted(m.title for m in filtered.all())
    assert titles == ["Allowed pattern row"]


# ── Search: _db_has_any_embeddings cold-start guard ───────────────────


def test_db_has_any_embeddings_false_for_empty_db(session):
    """Fresh DB → cheap probe returns False so we skip the model load."""
    from memee.engine.search import _db_has_any_embeddings, _invalidate_embedding_cache

    _invalidate_embedding_cache()
    assert _db_has_any_embeddings(session) is False


def test_db_has_any_embeddings_true_when_at_least_one(session):
    """One embedded memory is enough to flip the probe to True."""
    from memee.engine.search import _db_has_any_embeddings, _invalidate_embedding_cache

    _invalidate_embedding_cache()
    m = Memory(
        type=MemoryType.PATTERN.value,
        title="Embedded memory row",
        content="content content",
        tags=["tag"],
        embedding=[0.0] * 384,
    )
    session.add(m)
    session.commit()

    assert _db_has_any_embeddings(session) is True


def test_db_has_any_embeddings_caches_per_engine(session, monkeypatch):
    """The probe is cached by engine identity so repeat searches don't pay
    the SELECT cost again. Adding rows after the first probe doesn't flip
    the cached answer until ``_invalidate_embedding_cache`` is called.
    """
    from memee.engine.search import _db_has_any_embeddings, _invalidate_embedding_cache

    _invalidate_embedding_cache()
    # First probe: no embeddings → False, cached.
    assert _db_has_any_embeddings(session) is False

    # Add one — but DON'T invalidate. Cached False should stick.
    session.add(
        Memory(
            type=MemoryType.PATTERN.value,
            title="A late embedded memory",
            content="...",
            tags=["t"],
            embedding=[0.1] * 384,
        )
    )
    session.commit()
    assert _db_has_any_embeddings(session) is False, (
        "cache must not auto-refresh — that would defeat the cold-start guard"
    )

    # After explicit invalidation, the next probe sees the new row.
    _invalidate_embedding_cache()
    assert _db_has_any_embeddings(session) is True


# ── MCP _with_session: deterministic close ────────────────────────────


def test_with_session_closes_session_on_success():
    """Happy path: decorated coro returns → session.close() runs."""
    from memee.mcp_server import _with_session

    closed = {"flag": False}

    class FakeSession:
        def close(self_inner):
            closed["flag"] = True

    @_with_session
    async def echo(value: str, *, session) -> str:
        assert isinstance(session, FakeSession)
        return f"echoed:{value}"

    # Patch the session factory on the module so we don't touch the real engine
    import memee.mcp_server as mod
    original_factory = mod._get_session
    mod._get_session = lambda: FakeSession()
    try:
        result = asyncio.run(echo("hello"))
    finally:
        mod._get_session = original_factory

    assert result == "echoed:hello"
    assert closed["flag"] is True


def test_with_session_closes_session_on_exception():
    """Exception path: decorated coro raises → session.close() still runs.
    Without this guard MCP tools that crashed mid-way leaked SQLite
    connections under load.
    """
    from memee.mcp_server import _with_session

    closed = {"flag": False}

    class FakeSession:
        def close(self_inner):
            closed["flag"] = True

    @_with_session
    async def boom(*, session):
        raise RuntimeError("intentional")

    import memee.mcp_server as mod
    original_factory = mod._get_session
    mod._get_session = lambda: FakeSession()
    try:
        with pytest.raises(RuntimeError, match="intentional"):
            asyncio.run(boom())
    finally:
        mod._get_session = original_factory

    assert closed["flag"] is True


def test_with_session_supports_sync_function():
    """Decorator handles both async and sync callables."""
    from memee.mcp_server import _with_session

    closed = {"flag": False}

    class FakeSession:
        def close(self_inner):
            closed["flag"] = True

    @_with_session
    def sync_tool(x, *, session):
        return x * 2

    import memee.mcp_server as mod
    original_factory = mod._get_session
    mod._get_session = lambda: FakeSession()
    try:
        assert sync_tool(21) == 42
    finally:
        mod._get_session = original_factory

    assert closed["flag"] is True
