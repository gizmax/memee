"""Tests for the session ledger — citation eventing for the next briefing.

Covers the public API exposed by ``memee.session_ledger``:

  * ``record_session_end(session)`` snapshots citations created since
    ``last_ended_at`` into ``~/.memee/last_session_cites.json``.
  * ``format_session_summary()`` returns the one-line receipt or
    ``None``, honouring ``MEMEE_NO_SESSION_RECEIPT``.

Every test isolates HOME so the real ``~/.memee/last_session_cites.json``
is never touched. The autouse ``_isolate_pack_ledger`` fixture in
``conftest.py`` only handles the *pack* ledger, not this one.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from memee.storage.database import get_session, init_db
from memee.storage.models import Memory, Organization


# ── HOME isolation ─────────────────────────────────────────────────────


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Redirect ``Path.home()`` (via $HOME) to a per-test tmp_path and
    reload ``memee.session_ledger`` so its module-level ``CACHE_PATH``
    picks up the new home. Returns the tmp HOME path.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    # On macOS, pytest may also have set USERPROFILE-style; HOME is what
    # Path.home() reads on POSIX. Reload the module so CACHE_PATH is
    # recomputed against the patched HOME.
    import memee.session_ledger as session_ledger
    importlib.reload(session_ledger)
    # Sanity: the cache path must now live under tmp_path, not real HOME.
    assert str(session_ledger.CACHE_PATH).startswith(str(tmp_path))
    yield tmp_path
    # Reload one more time so subsequent tests see whatever HOME is then.
    importlib.reload(session_ledger)


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    """A fresh DB with a default org. Different file from the pack
    fixture's so collisions are impossible."""
    db_path = tmp_path / "session-ledger.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    engine = init_db()
    s = get_session(engine)
    s.add(Organization(name="ledger-test-org"))
    s.commit()
    yield s
    s.close()


# ── Helpers ────────────────────────────────────────────────────────────


def _make_memory(
    session,
    *,
    title: str,
    confidence: float,
    maturity: str,
    citation_ts: datetime,
    citation_note: str = "applied",
):
    """Insert a memory with one citation evidence entry stamped at
    ``citation_ts`` and ``last_applied_at`` matching, mirroring what
    ``confirm_citation`` does in production.
    """
    org = session.query(Organization).first()
    m = Memory(
        organization_id=org.id,
        type="pattern",
        maturity=maturity,
        title=title,
        content="content with a why and a when, ten plus chars",
        tags=["test"],
        confidence_score=confidence,
        application_count=1,
        last_applied_at=citation_ts,
        evidence_chain=[
            {
                "kind": "citation",
                "ts": citation_ts.isoformat(),
                "note": citation_note,
            }
        ],
    )
    session.add(m)
    session.commit()
    return m


# ── First call: cache absent ───────────────────────────────────────────


def test_first_call_no_cache_returns_none(isolated_home):
    """No cache file yet → format_session_summary returns None."""
    from memee.session_ledger import CACHE_PATH, format_session_summary

    assert not CACHE_PATH.exists()
    assert format_session_summary() is None


def test_first_record_session_end_writes_empty_snapshot(
    isolated_home, db_session
):
    """First-ever record_session_end: empty snapshot, marker stamped."""
    from memee.session_ledger import (
        CACHE_PATH,
        format_session_summary,
        record_session_end,
    )

    record_session_end(db_session)
    assert CACHE_PATH.exists()
    data = json.loads(CACHE_PATH.read_text())
    assert data["citations"] == []
    assert isinstance(data["ended_at"], str) and data["ended_at"]
    # Empty snapshot → still no receipt.
    assert format_session_summary() is None


# ── Zero new citations ─────────────────────────────────────────────────


def test_zero_new_citations_returns_none(isolated_home, db_session):
    """Stamp the marker, then call again with no new citations: None."""
    from memee.session_ledger import (
        format_session_summary,
        record_session_end,
    )

    # Burn-in: first call stamps the marker.
    record_session_end(db_session)
    # Second call: no citations have happened in between.
    record_session_end(db_session)
    assert format_session_summary() is None


# ── One citation ───────────────────────────────────────────────────────


def test_one_citation_renders_singular_notice(isolated_home, db_session):
    """Single citation → '...applied 1 memory: ...' notice."""
    from memee.session_ledger import (
        format_session_summary,
        record_session_end,
    )

    record_session_end(db_session)  # stamp marker
    after_marker = datetime.now(timezone.utc) + timedelta(seconds=1)
    _make_memory(
        db_session,
        title="React Query keys must include tenant id",
        confidence=0.82,
        maturity="canon",
        citation_ts=after_marker,
    )
    record_session_end(db_session)
    summary = format_session_summary()
    assert summary is not None
    assert summary.startswith(">")
    assert "applied 1 memory" in summary
    assert "React Query keys must include tenant id" in summary
    assert "[mem:" in summary


# ── Three citations: pick highest-scoring ──────────────────────────────


def test_three_citations_picks_highest_confidence(isolated_home, db_session):
    """Highest confidence × maturity wins; ties broken by most-recent."""
    from memee.session_ledger import (
        format_session_summary,
        record_session_end,
    )

    record_session_end(db_session)  # stamp marker
    base = datetime.now(timezone.utc) + timedelta(seconds=1)

    # Hypothesis with high confidence — strong looking but immature.
    _make_memory(
        db_session,
        title="Hypothesis trial pattern",
        confidence=0.95,
        maturity="hypothesis",
        citation_ts=base,
    )
    # Canon with mid confidence — should win on weight.
    _make_memory(
        db_session,
        title="Never use eval() on user input",
        confidence=0.88,
        maturity="canon",
        citation_ts=base + timedelta(seconds=1),
    )
    # Validated with mid confidence — should lose to canon above.
    _make_memory(
        db_session,
        title="Use parameterised queries",
        confidence=0.80,
        maturity="validated",
        citation_ts=base + timedelta(seconds=2),
    )

    record_session_end(db_session)
    summary = format_session_summary()
    assert summary is not None
    assert "applied 3 memories" in summary
    # Canon wins.
    assert "Never use eval() on user input" in summary
    # The other two must NOT be the highlighted memory.
    assert "Hypothesis trial pattern" not in summary
    assert "Use parameterised queries" not in summary


# ── Kill switch ────────────────────────────────────────────────────────


def test_kill_switch_disables_format(isolated_home, db_session, monkeypatch):
    """MEMEE_NO_SESSION_RECEIPT=1 disables format_session_summary."""
    from memee.session_ledger import (
        format_session_summary,
        record_session_end,
    )

    record_session_end(db_session)
    after_marker = datetime.now(timezone.utc) + timedelta(seconds=1)
    _make_memory(
        db_session,
        title="A memory worth seeing",
        confidence=0.9,
        maturity="canon",
        citation_ts=after_marker,
    )
    record_session_end(db_session)
    # Without the switch: a notice exists.
    assert format_session_summary() is not None
    # With the switch (any non-empty value): silent.
    monkeypatch.setenv("MEMEE_NO_SESSION_RECEIPT", "1")
    assert format_session_summary() is None
    # Explicit "0" is still non-empty per spec — also disables.
    monkeypatch.setenv("MEMEE_NO_SESSION_RECEIPT", "0")
    assert format_session_summary() is None


# ── Corrupt cache ──────────────────────────────────────────────────────


def test_corrupt_cache_returns_none(isolated_home):
    """A garbage cache file must NOT raise — returns None silently."""
    from memee.session_ledger import CACHE_PATH, format_session_summary

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text("{ this is not valid json")
    # Must not raise.
    assert format_session_summary() is None


def test_cache_with_non_dict_returns_none(isolated_home):
    """Cache parses but isn't a dict (e.g. someone wrote a list): None."""
    from memee.session_ledger import CACHE_PATH, format_session_summary

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(["unexpected", "list"]))
    assert format_session_summary() is None


def test_cache_missing_citations_field_returns_none(isolated_home):
    """Cache is a dict but has no citations array: None."""
    from memee.session_ledger import CACHE_PATH, format_session_summary

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps({"ended_at": "2026-04-28T00:00:00+00:00"}))
    assert format_session_summary() is None
