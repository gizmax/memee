"""Tests for v2.3.1 authoritative memory class.

Three surfaces under test:

  1. ``Memory.is_authoritative`` field — schema + default + write path
  2. Router Layer 0.5 — selection, tag-overlap gate, budget cap,
     kill switches, de-dup against Layer 0 / Layer 1
  3. CLI ``memee record --authoritative`` flag — wires through to the
     stored row

The MCP tool surface is covered indirectly by the model + Memory()
constructor test below (the tool passes ``is_authoritative`` to the
same constructor).
"""

from __future__ import annotations

from memee.engine.router import smart_briefing
from memee.storage.models import (
    MaturityLevel,
    Memory,
    MemoryType,
)


# ── Model field ──


def test_is_authoritative_defaults_to_false(session):
    """An old-style Memory(...) call without the new kwarg must keep
    the historical routing behaviour. Defaulting True would silently
    promote every existing row to Layer 0.5 on upgrade — the opposite
    of additive."""
    m = Memory(
        type=MemoryType.PATTERN.value,
        title="ordinary pattern",
        content="no pin",
        tags=["python"],
        maturity=MaturityLevel.VALIDATED.value,
        confidence_score=0.6,
        source_type="human",
    )
    session.add(m)
    session.commit()
    refetched = session.query(Memory).filter(Memory.title == "ordinary pattern").one()
    assert refetched.is_authoritative is False


def test_is_authoritative_round_trips_true(session):
    m = Memory(
        type=MemoryType.DECISION.value,
        title="PII never to logs",
        content="compliance",
        tags=["security", "logging", "pii"],
        maturity=MaturityLevel.CANON.value,
        confidence_score=0.9,
        source_type="human",
        is_authoritative=True,
    )
    session.add(m)
    session.commit()
    got = session.query(Memory).filter(Memory.title == "PII never to logs").one()
    assert got.is_authoritative is True


# ── Layer 0.5 routing helpers ──


def _seed_pinned(session) -> dict[str, Memory]:
    """Seed a mix of pinned and non-pinned memories the router can use."""
    rows = {
        "pii": Memory(
            type=MemoryType.DECISION.value,
            title="PII fields never go to logs",
            content="compliance policy: all PII must be redacted before logging",
            tags=["security", "pii", "logging"],
            maturity=MaturityLevel.CANON.value,
            confidence_score=0.95,
            source_type="human",
            is_authoritative=True,
        ),
        "global_policy": Memory(
            type=MemoryType.DECISION.value,
            title="Code reviews required before merge",
            content="org policy, no scope filter",
            tags=[],  # empty tags → global, always fires
            maturity=MaturityLevel.CANON.value,
            confidence_score=0.9,
            source_type="human",
            is_authoritative=True,
        ),
        "ts_persona": Memory(
            type=MemoryType.PATTERN.value,
            title="User prefers TypeScript over Flow",
            content="persona",
            tags=["typescript", "frontend"],
            maturity=MaturityLevel.CANON.value,
            confidence_score=0.85,
            source_type="human",
            is_authoritative=True,
        ),
        "non_pinned": Memory(
            type=MemoryType.PATTERN.value,
            title="Use timeout on HTTP calls",
            content="not authoritative, falls through to Layer 1",
            tags=["python", "http"],
            maturity=MaturityLevel.VALIDATED.value,
            confidence_score=0.6,
            source_type="human",
        ),
        "deprecated_pinned": Memory(
            type=MemoryType.DECISION.value,
            title="Old deprecated rule",
            content="superseded",
            tags=["security"],
            maturity=MaturityLevel.DEPRECATED.value,
            confidence_score=0.4,
            source_type="human",
            is_authoritative=True,
        ),
    }
    session.add_all(rows.values())
    session.commit()
    # v2.3.3: Layer 0.5 SQL pushdown joins ``MemoryTag``. Tests that
    # construct Memory rows directly (bypassing the quality gate) must
    # populate the normalised tag index too, otherwise the SQL path
    # sees zero tag rows and every pinned memory falls into the "global
    # / no tags" branch. Mirrors what ``run_quality_gate`` does in
    # production at write time.
    from memee.engine.tag_index import sync_memory_tags
    for m in rows.values():
        sync_memory_tags(session, m)
    session.commit()
    return rows


def test_layer05_renders_when_pinned_match_task(session, monkeypatch):
    """A pinned memory whose tags overlap the task tokens lands under
    a `Pinned policies in scope:` header."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.delenv("MEMEE_NO_PINNED", raising=False)
    _seed_pinned(session)

    result = smart_briefing(session, task="logging utility for users", token_budget=500)
    assert "Pinned policies in scope:" in result
    assert "PII fields never go to logs" in result
    # Slice out the pinned block (between its header and the next blank
    # line) — the TypeScript persona, being out of scope, must not
    # appear here. Layer 1 may still surface it on a broad search;
    # that's its job, not Layer 0.5's.
    lines = result.splitlines()
    pinned_start = next(
        i for i, ln in enumerate(lines) if ln.startswith("Pinned policies in scope:")
    )
    block = []
    for ln in lines[pinned_start + 1:]:
        if not ln.strip():
            break
        block.append(ln)
    pinned_block = "\n".join(block)
    assert "TypeScript over Flow" not in pinned_block


def test_layer05_global_policy_always_fires(session, monkeypatch):
    """Pinned memory with empty tags is a declared global policy and
    fires regardless of task tokens."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.delenv("MEMEE_NO_PINNED", raising=False)
    _seed_pinned(session)

    result = smart_briefing(session, task="any task here", token_budget=500)
    assert "Code reviews required before merge" in result


def test_layer05_skips_deprecated(session, monkeypatch):
    """A pinned memory at maturity=DEPRECATED stays silent — the operator
    has explicitly retired it."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.delenv("MEMEE_NO_PINNED", raising=False)
    _seed_pinned(session)

    result = smart_briefing(session, task="security audit", token_budget=500)
    assert "Old deprecated rule" not in result


def test_layer05_suppressed_by_memee_no_pinned(session, monkeypatch):
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.setenv("MEMEE_NO_PINNED", "1")
    _seed_pinned(session)

    result = smart_briefing(session, task="logging utility", token_budget=500)
    assert "Pinned policies in scope:" not in result
    # Layer 0 / Layer 1 still fire as before.


def test_layer05_suppressed_by_memee_quiet(session, monkeypatch):
    """Master ``MEMEE_QUIET=1`` reaches Layer 0.5 too, same shape as
    Layer 0's switch."""
    monkeypatch.setenv("MEMEE_QUIET", "1")
    _seed_pinned(session)

    result = smart_briefing(session, task="logging utility", token_budget=500)
    assert "Pinned policies in scope:" not in result


def test_layer05_respects_max_bullets_env(session, monkeypatch):
    """``MEMEE_PINNED_MAX_BULLETS=1`` caps the block at one bullet even
    when more would match. Lets operators tighten the budget."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.delenv("MEMEE_NO_PINNED", raising=False)
    monkeypatch.setenv("MEMEE_PINNED_MAX_BULLETS", "1")
    _seed_pinned(session)

    result = smart_briefing(session, task="anything matches global", token_budget=500)
    # Only ONE → bullet under the header. (Global policy wins because it
    # always matches; TS-persona / PII may or may not depending on order.)
    bullets = [
        ln for ln in result.splitlines()
        if ln.startswith("  → ")
    ]
    assert len(bullets) == 1


def test_layer05_invalid_max_bullets_falls_back(session, monkeypatch):
    """Unparseable ``MEMEE_PINNED_MAX_BULLETS`` falls back to the default
    rather than crashing the router."""
    monkeypatch.setenv("MEMEE_PINNED_MAX_BULLETS", "not-a-number")
    _seed_pinned(session)

    # Just verifying it doesn't raise.
    result = smart_briefing(session, task="anything", token_budget=500)
    assert isinstance(result, str)


# ── De-dup against Layer 0 and Layer 1 ──


def test_pinned_memory_does_not_repeat_in_layer1(session, monkeypatch):
    """When a pinned memory ALSO matches the search query, Layer 1's
    de-dup must skip it so the agent doesn't see the same title twice."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.delenv("MEMEE_NO_PINNED", raising=False)

    # Pin a memory whose tags also match a strong search hit.
    session.add(
        Memory(
            type=MemoryType.PATTERN.value,
            title="Logging is always JSON",
            content="logging logging logging — strong BM25 hit on the term",
            tags=["logging", "observability"],
            maturity=MaturityLevel.CANON.value,
            confidence_score=0.9,
            source_type="human",
            is_authoritative=True,
        )
    )
    session.commit()

    result = smart_briefing(session, task="set up logging in our service", token_budget=500)
    # Must appear under the pinned header...
    assert "Pinned policies in scope:" in result
    # ...but only ONCE in the whole briefing.
    assert result.count("Logging is always JSON") == 1


# ── CLI: --authoritative flag wires through ──


def test_cli_record_authoritative_persists(tmp_path, monkeypatch):
    """``memee record pattern "X" --authoritative`` writes a row whose
    ``is_authoritative`` is True. Catches a regression in the click
    binding or the Memory() constructor pass-through."""
    db_path = tmp_path / "auth.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config as cfg
    cfg.settings = cfg.Settings(db_path=db_path)

    from click.testing import CliRunner
    from memee.cli import cli

    runner = CliRunner()
    runner.invoke(cli, ["init"])
    result = runner.invoke(
        cli,
        [
            "record", "decision", "PII never to logs",
            "-c", "compliance policy: redact before logging",
            "-t", "security,pii,logging",
            "--authoritative",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "[pinned]" in result.output

    from memee.storage.database import get_session, init_db
    s = get_session(init_db())
    try:
        m = s.query(Memory).filter(Memory.title == "PII never to logs").one()
        assert m.is_authoritative is True
    finally:
        s.close()


def test_cli_record_without_flag_stays_unpinned(tmp_path, monkeypatch):
    """Default path: omitting ``--authoritative`` leaves the row at
    False so the v2.3.1 release doesn't silently change existing
    workflows."""
    db_path = tmp_path / "unpinned.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config as cfg
    cfg.settings = cfg.Settings(db_path=db_path)

    from click.testing import CliRunner
    from memee.cli import cli

    runner = CliRunner()
    runner.invoke(cli, ["init"])
    result = runner.invoke(
        cli,
        [
            "record", "pattern", "Use timeout on HTTP",
            "-c", "request timeout of 10s on all outbound calls",
            "-t", "python,http",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "[pinned]" not in result.output

    from memee.storage.database import get_session, init_db
    s = get_session(init_db())
    try:
        m = s.query(Memory).filter(Memory.title == "Use timeout on HTTP").one()
        assert m.is_authoritative is False
    finally:
        s.close()
