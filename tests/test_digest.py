"""Tests for the v2.1.0 weekly digest.

Covers:
- Empty DB → returns None
- Fresh cache (within 7 days) → returns None even if numbers exist
- Stale cache → regenerates
- Corrupt cache → regenerates without crashing
- ``MEMEE_NO_DIGEST=1`` → returns None
- Each metric path independently (seeded rows for impact events,
  CANON-maturity memories, low-confidence hypotheses)
- Plural / singular wording
- Errors during regeneration are swallowed
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from memee.engine.impact import ImpactEvent, ImpactType
from memee.storage.database import get_engine, get_session, init_db
from memee.storage.models import MaturityLevel, Memory, MemoryType, Organization


# ── Test fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Repoint HOME + the digest cache + the DB at a per-test tmp dir.

    Every test in this file needs:
      * its own ``~/.memee/memee.db``
      * its own ``~/.memee/weekly_digest.json``
      * an explicit env var to flip the kill switch off (in case the dev
        machine has it set globally).
    """
    home = tmp_path / "home"
    home.mkdir()

    # Point Memee's settings at a tmp DB.
    db_path = home / ".memee" / "memee.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))

    # Make sure the kill switch isn't inherited from the parent shell.
    monkeypatch.delenv("MEMEE_NO_DIGEST", raising=False)

    # Reload memee.config so the new env var sticks for ``get_engine()``.
    import importlib

    from memee import config as memee_config
    importlib.reload(memee_config)

    # Repoint the cache file at our tmp HOME.
    from memee import digest as digest_module
    cache_path = home / ".memee" / "weekly_digest.json"
    monkeypatch.setattr(digest_module, "CACHE_PATH", cache_path)

    # Initialize the DB so callers can immediately seed rows.
    init_db(get_engine(db_path))

    return {
        "home": home,
        "db_path": db_path,
        "cache_path": cache_path,
        "digest_module": digest_module,
    }


def _seed_org(session) -> Organization:
    org = Organization(name="test-org")
    session.add(org)
    session.commit()
    return org


def _seed_memory(
    session,
    org: Organization,
    *,
    title: str,
    maturity: str = MaturityLevel.HYPOTHESIS.value,
    confidence: float = 0.5,
    validation_count: int = 0,
    invalidation_count: int = 0,
    updated_at: datetime | None = None,
) -> Memory:
    """Plant one memory and (optionally) backdate ``updated_at``.

    SQLAlchemy's ``onupdate`` fires on UPDATE statements, so we have to
    SET it directly via a second UPDATE — assigning at construction time
    is fine for INSERT.
    """
    mem = Memory(
        title=title,
        content=f"content for {title}",
        type=MemoryType.PATTERN.value,
        organization_id=org.id,
        maturity=maturity,
        confidence_score=confidence,
        validation_count=validation_count,
        invalidation_count=invalidation_count,
    )
    session.add(mem)
    session.commit()
    if updated_at is not None:
        mem.updated_at = updated_at
        session.commit()
    return mem


def _seed_impact_event(
    session,
    memory_id: str,
    impact_type: str,
    *,
    created_at: datetime | None = None,
) -> ImpactEvent:
    """Plant one impact event and (optionally) backdate ``created_at``."""
    event = ImpactEvent(memory_id=memory_id, impact_type=impact_type)
    session.add(event)
    session.commit()
    if created_at is not None:
        event.created_at = created_at
        session.commit()
    return event


# ── 1. Kill switch ─────────────────────────────────────────────────────────


def test_kill_switch_disables_digest(isolated_home, monkeypatch):
    """``MEMEE_NO_DIGEST=1`` short-circuits before any DB or cache access."""
    monkeypatch.setenv("MEMEE_NO_DIGEST", "1")
    digest = isolated_home["digest_module"]
    assert digest.format_digest_notice() is None
    # And no cache file should be written (we returned before write).
    assert not isolated_home["cache_path"].exists()


def test_kill_switch_any_nonempty_value(isolated_home, monkeypatch):
    """Spec says "any non-empty value disables" — exercise a couple."""
    digest = isolated_home["digest_module"]
    for val in ("yes", "true", "0"):  # even "0" is truthy as a non-empty string
        monkeypatch.setenv("MEMEE_NO_DIGEST", val)
        assert digest.format_digest_notice() is None


# ── 2. Empty DB ────────────────────────────────────────────────────────────


def test_empty_db_returns_none(isolated_home):
    """No memories, no impact events → all counters zero → no digest."""
    digest = isolated_home["digest_module"]
    result = digest.format_digest_notice()
    assert result is None
    # Cache still gets stamped — we don't want to rerun this query every
    # session for the next 7 days even when the answer is "nothing".
    assert isolated_home["cache_path"].exists()


# ── 3. Cache freshness ─────────────────────────────────────────────────────


def test_fresh_cache_suppresses_even_with_data(isolated_home):
    """If the cache is younger than 7 days, the digest is suppressed —
    the user already saw it on Monday, don't repeat it Tuesday."""
    digest = isolated_home["digest_module"]
    # Plant a fresh cache entry.
    cache = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"memories_applied": 99},
    }
    isolated_home["cache_path"].parent.mkdir(parents=True, exist_ok=True)
    isolated_home["cache_path"].write_text(json.dumps(cache))

    # Even if the DB is loaded with data, the fresh cache wins.
    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    mem = _seed_memory(session, org, title="A pattern")
    _seed_impact_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)
    session.close()

    assert digest.format_digest_notice() is None


def test_stale_cache_regenerates(isolated_home):
    """Cache older than 7 days → regenerate."""
    digest = isolated_home["digest_module"]
    stale_when = datetime.now(timezone.utc) - timedelta(days=8)
    cache = {
        "generated_at": stale_when.isoformat(),
        "payload": {"memories_applied": 0},
    }
    isolated_home["cache_path"].parent.mkdir(parents=True, exist_ok=True)
    isolated_home["cache_path"].write_text(json.dumps(cache))

    # Seed a fresh impact event so the regen has something to render.
    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    mem = _seed_memory(session, org, title="A pattern")
    _seed_impact_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)
    session.close()

    result = digest.format_digest_notice()
    assert result is not None
    assert "1 memory applied" in result  # singular form


def test_corrupt_cache_regenerates(isolated_home):
    """Corrupt JSON, wrong shape, missing keys — all collapse to regen."""
    digest = isolated_home["digest_module"]
    isolated_home["cache_path"].parent.mkdir(parents=True, exist_ok=True)
    isolated_home["cache_path"].write_text("{not valid json")

    # The regen path runs without crashing. Empty DB → None, but no error.
    assert digest.format_digest_notice() is None
    # And the cache got rewritten as valid JSON.
    rewritten = json.loads(isolated_home["cache_path"].read_text())
    assert "generated_at" in rewritten


def test_cache_with_wrong_top_level_shape_regenerates(isolated_home):
    """Cache that's a list instead of a dict — should not crash."""
    digest = isolated_home["digest_module"]
    isolated_home["cache_path"].parent.mkdir(parents=True, exist_ok=True)
    isolated_home["cache_path"].write_text("[1, 2, 3]")
    assert digest.format_digest_notice() is None


def test_cache_with_garbage_timestamp_regenerates(isolated_home):
    """Cache exists but ``generated_at`` is unparseable → regen."""
    digest = isolated_home["digest_module"]
    isolated_home["cache_path"].parent.mkdir(parents=True, exist_ok=True)
    isolated_home["cache_path"].write_text(
        json.dumps({"generated_at": "not-a-date", "payload": {}})
    )
    # Empty DB → returns None (no story to tell), but didn't crash on parse.
    assert digest.format_digest_notice() is None


def test_future_cache_regenerates(isolated_home):
    """Clock skew: a cache stamped in the future shouldn't lock us out
    forever. Treat negative ages as 'regenerate'."""
    digest = isolated_home["digest_module"]
    future_when = datetime.now(timezone.utc) + timedelta(days=30)
    cache = {
        "generated_at": future_when.isoformat(),
        "payload": {"memories_applied": 0},
    }
    isolated_home["cache_path"].parent.mkdir(parents=True, exist_ok=True)
    isolated_home["cache_path"].write_text(json.dumps(cache))

    # Plant data so the regen has something to render.
    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    mem = _seed_memory(session, org, title="A pattern")
    _seed_impact_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)
    session.close()

    result = digest.format_digest_notice()
    assert result is not None


# ── 4. Each metric path independently ──────────────────────────────────────


def test_memories_applied_counter(isolated_home):
    """Three eligible impact types are counted; ineligible ones are not."""
    digest = isolated_home["digest_module"]

    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    mem = _seed_memory(session, org, title="A pattern")

    # Eligible types — should each contribute 1.
    _seed_impact_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)
    _seed_impact_event(session, mem.id, ImpactType.MISTAKE_AVOIDED.value)
    _seed_impact_event(session, mem.id, ImpactType.DECISION_INFORMED.value)

    # Ineligible types — should NOT count toward "applied".
    _seed_impact_event(session, mem.id, ImpactType.TIME_SAVED.value)
    _seed_impact_event(session, mem.id, ImpactType.CODE_CHANGED.value)
    session.close()

    result = digest.format_digest_notice()
    assert result is not None
    # 3 applied (KR + MA + DI). MISTAKE_AVOIDED also counts as a warning,
    # so the warnings counter is 1.
    assert "3 memories applied" in result
    assert "1 warning checked" in result


def test_warnings_checked_counter(isolated_home):
    """All three warning-touching types count toward warnings_checked."""
    digest = isolated_home["digest_module"]

    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    mem = _seed_memory(session, org, title="A pattern")

    _seed_impact_event(session, mem.id, ImpactType.MISTAKE_AVOIDED.value)
    _seed_impact_event(session, mem.id, ImpactType.MISTAKE_MADE.value)
    _seed_impact_event(session, mem.id, ImpactType.WARNING_INEFFECTIVE.value)
    session.close()

    result = digest.format_digest_notice()
    assert result is not None
    assert "3 warnings checked" in result


def test_promoted_to_canon_counter(isolated_home):
    """A CANON memory with ``updated_at`` inside the window counts as a
    promotion (proxy). One outside the window or non-CANON does not."""
    digest = isolated_home["digest_module"]

    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    now = datetime.now(timezone.utc)

    # In-window CANON: counts.
    _seed_memory(
        session, org, title="Canon recent",
        maturity=MaturityLevel.CANON.value,
        updated_at=now - timedelta(days=2),
    )
    # Out-of-window CANON: does NOT count.
    _seed_memory(
        session, org, title="Canon old",
        maturity=MaturityLevel.CANON.value,
        updated_at=now - timedelta(days=30),
    )
    # In-window but not CANON: does NOT count.
    _seed_memory(
        session, org, title="Hypothesis recent",
        maturity=MaturityLevel.HYPOTHESIS.value,
        updated_at=now - timedelta(days=1),
    )
    session.close()

    result = digest.format_digest_notice()
    assert result is not None
    assert "1 promoted to canon" in result


def test_needs_review_counter(isolated_home):
    """Hypotheses with confidence < 0.4 AND some validation activity
    are flagged. Untouched hypotheses (no validation activity) are not.
    Non-hypothesis maturities are not."""
    digest = isolated_home["digest_module"]

    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)

    # Counts: hypothesis, low confidence, has been argued against.
    _seed_memory(
        session, org, title="Contested hypothesis",
        maturity=MaturityLevel.HYPOTHESIS.value,
        confidence=0.3,
        invalidation_count=2,
        validation_count=1,
    )
    # Doesn't count: confidence above the bar.
    _seed_memory(
        session, org, title="Healthy hypothesis",
        maturity=MaturityLevel.HYPOTHESIS.value,
        confidence=0.6,
        invalidation_count=0,
        validation_count=0,
    )
    # Doesn't count: low confidence but no validation activity at all
    # (just sitting at the default — no one has bothered with it).
    _seed_memory(
        session, org, title="Untouched hypothesis",
        maturity=MaturityLevel.HYPOTHESIS.value,
        confidence=0.3,
        invalidation_count=0,
        validation_count=0,
    )
    # Doesn't count: not a hypothesis any more.
    _seed_memory(
        session, org, title="Tested low-confidence",
        maturity=MaturityLevel.TESTED.value,
        confidence=0.3,
        invalidation_count=2,
    )
    session.close()

    result = digest.format_digest_notice()
    assert result is not None
    assert "Needs review: 1 hypothesis" in result


def test_old_impact_events_excluded(isolated_home):
    """Events older than 7 days are NOT in the window."""
    digest = isolated_home["digest_module"]

    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    mem = _seed_memory(session, org, title="A pattern")

    old_when = datetime.now(timezone.utc) - timedelta(days=30)
    _seed_impact_event(
        session, mem.id, ImpactType.KNOWLEDGE_REUSED.value,
        created_at=old_when,
    )
    session.close()

    # No in-window events → no story.
    result = digest.format_digest_notice()
    assert result is None


# ── 5. Rendering details ───────────────────────────────────────────────────


def test_zero_counters_returns_none(isolated_home):
    """If every counter is zero, ``_render`` returns None — no useful
    receipt to show. The caller sees None, the integrator skips the
    prepend, the briefing is unaffected."""
    digest = isolated_home["digest_module"]
    # Empty DB: every counter is zero.
    assert digest.format_digest_notice() is None


def test_singular_plural_forms(isolated_home):
    """Quick check that 1-item phrasing ('1 memory') differs from
    multi-item phrasing ('2 memories')."""
    digest = isolated_home["digest_module"]

    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    mem = _seed_memory(session, org, title="A pattern")
    _seed_impact_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)
    _seed_impact_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)
    session.close()

    result = digest.format_digest_notice()
    assert result is not None
    assert "2 memories applied" in result
    assert "memory applied" not in result  # not the singular form


def test_render_only_includes_nonzero_metrics(isolated_home):
    """Only the metric for KNOWLEDGE_REUSED is non-zero — the digest
    should mention "memories applied" but NOT "warnings checked" nor
    "promoted to canon"."""
    digest = isolated_home["digest_module"]

    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    mem = _seed_memory(session, org, title="A pattern")
    _seed_impact_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)
    session.close()

    result = digest.format_digest_notice()
    assert result is not None
    assert "memories applied" in result or "memory applied" in result
    assert "warning" not in result
    assert "canon" not in result
    assert "Needs review" not in result


def test_output_starts_with_quoted_block(isolated_home):
    """Smart-briefing is a quoted block; our digest must blend in."""
    digest = isolated_home["digest_module"]

    session = get_session(get_engine(isolated_home["db_path"]))
    org = _seed_org(session)
    mem = _seed_memory(session, org, title="A pattern")
    _seed_impact_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)
    session.close()

    result = digest.format_digest_notice()
    assert result is not None
    assert result.startswith("> Memee — last 7 days:")
    assert not result.endswith("\n")  # integrator adds the separator


# ── 6. DB error swallowing ─────────────────────────────────────────────────


def test_db_error_returns_none(isolated_home, monkeypatch):
    """A failure inside ``_compute_metrics`` (DB locked, schema-drift,
    anything) collapses to None — never bubbles to the caller."""
    digest = isolated_home["digest_module"]

    def boom(_session, _since):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(digest, "_compute_metrics", boom)
    assert digest.format_digest_notice() is None


def test_init_db_failure_returns_none(isolated_home, monkeypatch):
    """If even ``init_db`` blows up (the DB path is unwriteable, etc.)
    we should still not raise. The whole regen block is wrapped."""
    digest = isolated_home["digest_module"]

    # Patch get_engine inside the module's import path so the regen
    # raises immediately, before we even open a session.
    import memee.storage.database as db_module

    def boom(*_args, **_kw):
        raise RuntimeError("simulated engine failure")

    monkeypatch.setattr(db_module, "get_engine", boom)
    assert digest.format_digest_notice() is None
