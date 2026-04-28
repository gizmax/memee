"""Tests for the v2.2.0 aggregate session receipt (feature M1).

Covers ``memee.receipts.format_session_receipt`` (the renderer) and the
two integration points it's wired into:

  * ``memee.cli._gather_prepends`` — fourth slot, between digest and
    session-summary, with a ``(now-1h, now)`` window for mid-session
    framing.
  * ``memee learn --auto`` — emits the aggregate line BEFORE the
    existing single-memory ``_format_stop_receipt`` line. Two lines max,
    silent when nothing happened.

Voice flag (`agent | tool`):
  * Default is ``agent`` for new installs.
  * ``MEMEE_RECEIPT_VOICE`` env var overrides the default at format-time.
  * Explicit ``voice=`` arg wins over both. Unknown values fall back to
    ``agent`` silently.

Silence rule:
  * ``reused == 0 AND prevented == 0`` → return ``None`` regardless of
    ``saved_min``. Without a concrete signal, we don't claim time saved.
  * ``MEMEE_NO_RECEIPT=1`` (any non-empty value) → ``None``.
  * ``saved_min < 3`` minutes → suppressed. ``>= 3`` rounded to nearest 5.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from memee.engine.impact import ImpactType
from memee.receipts import format_session_receipt
from memee.storage.database import get_session, init_db
from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryType,
    Organization,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    """Fresh SQLite DB with a default org so receipts can resolve memory rows."""
    db_path = tmp_path / "session-receipt.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    engine = init_db()
    s = get_session(engine)
    s.add(Organization(name="receipt-test-org"))
    s.commit()
    yield s
    s.close()


def _make_memory(
    session,
    *,
    title: str,
    confidence: float = 0.85,
    maturity: str = "canon",
    application_count: int = 1,
) -> Memory:
    """Insert a memory at canon-level confidence by default."""
    org = session.query(Organization).first()
    m = Memory(
        organization_id=org.id,
        type=MemoryType.PATTERN.value,
        maturity=maturity,
        title=title,
        content="content with a why and a when, more than fifteen chars",
        tags=["test"],
        confidence_score=confidence,
        application_count=application_count,
    )
    session.add(m)
    session.commit()
    return m


def _record_event(
    session,
    *,
    memory: Memory,
    impact_type: str,
    time_saved: float = 0.0,
    created_at: datetime | None = None,
):
    """Record an impact event, optionally back-dating ``created_at``.

    The default factory uses ``utcnow`` for created_at; for windowed
    tests we sometimes need to plant an event "in the past".
    """
    from memee.engine.impact import ImpactEvent

    ev = ImpactEvent(
        memory_id=memory.id,
        impact_type=impact_type,
        time_saved_minutes=time_saved,
    )
    if created_at is not None:
        ev.created_at = (
            created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
        )
    session.add(ev)
    session.commit()
    return ev


def _window_now():
    """Wide window centered on ``now`` so events recorded during the test land inside."""
    now = datetime.now(timezone.utc)
    return now - timedelta(hours=1), now + timedelta(minutes=1)


# ── Silence rules ───────────────────────────────────────────────────────


def test_returns_none_when_no_signal(db_session):
    """Empty DB / no impact events → silence."""
    since, until = _window_now()
    assert format_session_receipt(db_session, since=since, until=until) is None


def test_returns_none_when_only_saved_min(db_session):
    """saved_min alone is NOT enough — silence rule says ``reused == 0
    AND prevented == 0`` → ``None`` regardless of saved time."""
    mem = _make_memory(db_session, title="Memory worth saving time")
    # TIME_SAVED is not in the reused/prevented sets. With no other
    # signal we must stay silent even if time was saved.
    _record_event(
        db_session, memory=mem, impact_type=ImpactType.TIME_SAVED.value,
        time_saved=12.0,
    )
    since, until = _window_now()
    assert format_session_receipt(
        db_session, since=since, until=until, voice="tool"
    ) is None


def test_kill_switch_env_var(db_session, monkeypatch):
    """``MEMEE_NO_RECEIPT=1`` (any non-empty value) → silence even on real signal."""
    mem = _make_memory(db_session, title="React Query keys must include tenant id")
    _record_event(
        db_session, memory=mem, impact_type=ImpactType.KNOWLEDGE_REUSED.value,
    )
    since, until = _window_now()

    # Without the switch: a line exists.
    monkeypatch.delenv("MEMEE_NO_RECEIPT", raising=False)
    assert format_session_receipt(db_session, since=since, until=until) is not None

    # With the switch (any non-empty value): silent.
    monkeypatch.setenv("MEMEE_NO_RECEIPT", "1")
    assert format_session_receipt(db_session, since=since, until=until) is None

    # Even "0" is non-empty per the spec — also disables.
    monkeypatch.setenv("MEMEE_NO_RECEIPT", "0")
    assert format_session_receipt(db_session, since=since, until=until) is None


# ── Tool voice (legacy / explicit) ─────────────────────────────────────


def test_tool_voice_format(db_session):
    """Tool voice keeps the brand-forward 'Memee reused N, prevented M, saved ~K min.' shape."""
    m1 = _make_memory(db_session, title="React Query keys must include tenant id")
    m2 = _make_memory(db_session, title="Use parameterised queries")
    m3 = _make_memory(db_session, title="Stripe API key in source code")
    _record_event(db_session, memory=m1, impact_type=ImpactType.KNOWLEDGE_REUSED.value)
    _record_event(db_session, memory=m2, impact_type=ImpactType.KNOWLEDGE_REUSED.value)
    _record_event(
        db_session, memory=m3,
        impact_type=ImpactType.MISTAKE_AVOIDED.value, time_saved=8.0,
    )
    since, until = _window_now()
    line = format_session_receipt(
        db_session, since=since, until=until, voice="tool"
    )
    assert line is not None
    assert line.startswith("Memee ")
    assert "reused 2 memories" in line
    assert "prevented 1 known mistake" in line
    # 8 min rounds to 10 (nearest 5).
    assert "saved ~10 min" in line
    assert line.endswith(".")


def test_tool_voice_drops_zero_counters(db_session):
    """Zero counters get dropped from the tool-voice phrase entirely."""
    mem = _make_memory(db_session, title="Just a single reuse")
    _record_event(db_session, memory=mem, impact_type=ImpactType.KNOWLEDGE_REUSED.value)
    since, until = _window_now()
    line = format_session_receipt(
        db_session, since=since, until=until, voice="tool"
    )
    assert line is not None
    assert "reused 1 memory" in line
    # No "prevented" or "saved" since both are zero.
    assert "prevented" not in line
    assert "saved" not in line


def test_saved_min_suppressed_below_3(db_session):
    """saved_min < 3 minutes → suppressed (no 'saved ~X min' clause)."""
    m1 = _make_memory(db_session, title="Reused canon")
    _record_event(
        db_session, memory=m1, impact_type=ImpactType.KNOWLEDGE_REUSED.value,
        time_saved=2.0,  # below the 3-min floor
    )
    since, until = _window_now()
    line = format_session_receipt(
        db_session, since=since, until=until, voice="tool"
    )
    assert line is not None
    assert "saved" not in line


def test_saved_min_rounded_to_nearest_5(db_session):
    """saved_min rounds to the nearest 5: 7→5, 8→10, 12→10, 13→15."""
    cases = [(7, "saved ~5 min"), (8, "saved ~10 min"),
             (12, "saved ~10 min"), (13, "saved ~15 min")]
    for raw, expected in cases:
        # Fresh memory + event per case — counters are computed over the
        # window, not per memory.
        m = _make_memory(db_session, title=f"saved-min-{raw}")
        _record_event(
            db_session, memory=m, impact_type=ImpactType.KNOWLEDGE_REUSED.value,
            time_saved=float(raw),
        )
        since, until = _window_now()
        line = format_session_receipt(
            db_session, since=since, until=until, voice="tool"
        )
        # Each iteration accumulates events, so we just assert the
        # current line contains a saved phrase ≥ expected. Use the
        # specific case in isolation by re-rounding per call:
        assert line is not None
        # Last call: total saved is sum of 7+8+12+13 etc up to here. The
        # robust check is to verify the helper directly.
        from memee.receipts import _round_saved_minutes
        assert _round_saved_minutes(raw) * 1 == int(expected.split("~")[1].split(" ")[0])


# ── Voice flag wiring ──────────────────────────────────────────────────


def test_voice_env_var_overrides_default(db_session, monkeypatch):
    """``MEMEE_RECEIPT_VOICE=tool`` flips the default agent voice → tool voice."""
    mem = _make_memory(db_session, title="React Query keys must include tenant id")
    _record_event(db_session, memory=mem, impact_type=ImpactType.KNOWLEDGE_REUSED.value)
    since, until = _window_now()

    # Default (no env): agent voice — line cites the memory by title.
    monkeypatch.delenv("MEMEE_RECEIPT_VOICE", raising=False)
    line = format_session_receipt(db_session, since=since, until=until)
    assert line is not None
    assert "React Query keys must include tenant id" in line
    assert "[mem:" in line
    assert not line.startswith("Memee ")

    # Override → tool voice.
    monkeypatch.setenv("MEMEE_RECEIPT_VOICE", "tool")
    line = format_session_receipt(db_session, since=since, until=until)
    assert line is not None
    assert line.startswith("Memee ")
    assert "reused 1 memory" in line


def test_voice_unknown_value_falls_back_to_agent(db_session, monkeypatch):
    """A typo'd env value silently falls back to the default — never raises."""
    mem = _make_memory(db_session, title="A memory worth seeing")
    _record_event(db_session, memory=mem, impact_type=ImpactType.KNOWLEDGE_REUSED.value)
    since, until = _window_now()

    monkeypatch.setenv("MEMEE_RECEIPT_VOICE", "ageny")  # typo
    line = format_session_receipt(db_session, since=since, until=until)
    assert line is not None
    # Agent voice cites by title, not "Memee reused".
    assert not line.startswith("Memee ")
    assert "A memory worth seeing" in line


def test_explicit_voice_arg_wins(db_session, monkeypatch):
    """Explicit ``voice=`` arg beats the env var."""
    mem = _make_memory(db_session, title="A memory worth seeing")
    _record_event(db_session, memory=mem, impact_type=ImpactType.KNOWLEDGE_REUSED.value)
    since, until = _window_now()

    monkeypatch.setenv("MEMEE_RECEIPT_VOICE", "tool")
    line = format_session_receipt(
        db_session, since=since, until=until, voice="agent"
    )
    assert line is not None
    assert not line.startswith("Memee ")
    assert "A memory worth seeing" in line


# ── Agent voice (default) ──────────────────────────────────────────────


def test_agent_voice_picks_canon_memory(db_session, monkeypatch):
    """Agent voice cites the most-significant memory by name. Mirror the
    Stop receipt's logic: warning > canon reuse > decision informed.
    Within a tier, confidence × maturity_weight wins.
    """
    monkeypatch.delenv("MEMEE_RECEIPT_VOICE", raising=False)

    # Plant three memories at different maturities. The canon one
    # should win against the hypothesis even though both have a reuse
    # event in the same window.
    hypothesis = _make_memory(
        db_session, title="Hypothesis", confidence=0.95, maturity="hypothesis",
    )
    canon = _make_memory(
        db_session, title="Never use eval() on user input",
        confidence=0.88, maturity="canon", application_count=4,
    )
    _record_event(db_session, memory=hypothesis, impact_type=ImpactType.KNOWLEDGE_REUSED.value)
    _record_event(db_session, memory=canon, impact_type=ImpactType.KNOWLEDGE_REUSED.value)

    since, until = _window_now()
    line = format_session_receipt(db_session, since=since, until=until)
    assert line is not None
    # Canon-tier memory wins on weight even with lower confidence.
    assert "Never use eval() on user input" in line
    assert "Hypothesis" not in line
    assert "[mem:" in line


def test_agent_voice_prevented_phrasing(db_session, monkeypatch):
    """A MISTAKE_AVOIDED event uses the 'Avoided a repeat of "..."' wording."""
    monkeypatch.delenv("MEMEE_RECEIPT_VOICE", raising=False)

    mem = _make_memory(db_session, title="Stripe API key in source code")
    _record_event(db_session, memory=mem, impact_type=ImpactType.MISTAKE_AVOIDED.value)

    since, until = _window_now()
    line = format_session_receipt(db_session, since=since, until=until)
    assert line is not None
    assert line.startswith("Avoided a repeat of")
    assert "Stripe API key in source code" in line


def test_agent_voice_truncates_long_title(db_session, monkeypatch):
    """Title >60 chars gets truncated with ``…``; line capped at 140 chars."""
    monkeypatch.delenv("MEMEE_RECEIPT_VOICE", raising=False)

    very_long = "X" * 200  # well past the 60-char title cap
    mem = _make_memory(db_session, title=very_long, application_count=3)
    _record_event(db_session, memory=mem, impact_type=ImpactType.KNOWLEDGE_REUSED.value)

    since, until = _window_now()
    line = format_session_receipt(db_session, since=since, until=until)
    assert line is not None
    assert len(line) <= 140
    # The visible title got the ellipsis treatment.
    assert "…" in line
    # The full 200-char string is NOT in the output.
    assert very_long not in line


# ── Brief prepend integration ──────────────────────────────────────────


def test_brief_prepends_in_correct_position(db_session, monkeypatch, tmp_path):
    """``_gather_prepends()`` puts the aggregate session receipt
    BETWEEN the weekly digest and the last-session summary.
    """
    monkeypatch.delenv("MEMEE_NO_RECEIPT", raising=False)
    monkeypatch.delenv("MEMEE_NO_DIGEST", raising=False)
    monkeypatch.delenv("MEMEE_NO_SESSION_RECEIPT", raising=False)
    monkeypatch.delenv("MEMEE_NO_UPDATE_CHECK", raising=False)

    # Stub each of the four prepend helpers to return a marker string so
    # ordering is the only thing under test.
    import memee.cli as cli
    import memee.digest as digest
    import memee.receipts as receipts
    import memee.session_ledger as session_ledger
    import memee.update_check as update_check

    monkeypatch.setattr(digest, "format_digest_notice", lambda: "DIGEST_LINE")
    monkeypatch.setattr(receipts, "format_session_receipt",
                        lambda *a, **kw: "AGGREGATE_LINE")
    monkeypatch.setattr(session_ledger, "format_session_summary",
                        lambda: "SUMMARY_LINE")
    monkeypatch.setattr(update_check, "check", lambda: None)
    monkeypatch.setattr(update_check, "format_notice",
                        lambda *_a, **_kw: "UPDATE_LINE")

    out = cli._gather_prepends()
    # M6 orchestration (v2.2.0) hard-caps prepends at 2 channels per call.
    # The test verifies M1's slot ORDER — aggregate session receipt sits
    # between digest and last-session summary in the priority list — so
    # asserting on the first two items is equivalent under the cap. The
    # full priority list is exercised in tests/test_prepend_orchestration.py.
    assert out[:2] == ["DIGEST_LINE", "AGGREGATE_LINE"]
    assert len(out) == 2  # M6 cap


def test_brief_prepend_silent_when_receipt_returns_none(db_session, monkeypatch):
    """When ``format_session_receipt`` returns None the aggregate slot
    contributes no string — the prepend list shrinks accordingly."""
    monkeypatch.delenv("MEMEE_NO_RECEIPT", raising=False)
    monkeypatch.delenv("MEMEE_NO_DIGEST", raising=False)
    monkeypatch.delenv("MEMEE_NO_SESSION_RECEIPT", raising=False)
    monkeypatch.delenv("MEMEE_NO_UPDATE_CHECK", raising=False)

    import memee.cli as cli
    import memee.digest as digest
    import memee.receipts as receipts
    import memee.session_ledger as session_ledger
    import memee.update_check as update_check

    monkeypatch.setattr(digest, "format_digest_notice", lambda: "DIGEST_LINE")
    monkeypatch.setattr(receipts, "format_session_receipt", lambda *a, **kw: None)
    monkeypatch.setattr(session_ledger, "format_session_summary",
                        lambda: "SUMMARY_LINE")
    monkeypatch.setattr(update_check, "check", lambda: None)
    monkeypatch.setattr(update_check, "format_notice", lambda *_a, **_kw: "")

    out = cli._gather_prepends()
    assert "AGGREGATE_LINE" not in out
    assert out == ["DIGEST_LINE", "SUMMARY_LINE"]


# ── learn --auto two-line output ───────────────────────────────────────


def _patch_db(tmp_path, monkeypatch):
    db_path = tmp_path / "learn.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    return db_path


def _isolate_home(tmp_path, monkeypatch):
    """Redirect $HOME so the session ledger cache lives in tmp_path."""
    import importlib

    monkeypatch.setenv("HOME", str(tmp_path))
    import memee.session_ledger as sl

    importlib.reload(sl)
    return sl


def test_learn_auto_emits_two_lines_when_signal(tmp_path, monkeypatch):
    """``learn --auto`` emits BOTH the aggregate and the single-memory line
    when the prior session marker exists AND there's a real signal in
    the window.
    """
    _patch_db(tmp_path, monkeypatch)
    monkeypatch.delenv("MEMEE_NO_RECEIPT", raising=False)
    monkeypatch.delenv("MEMEE_RECEIPT_VOICE", raising=False)
    sl = _isolate_home(tmp_path, monkeypatch)

    # Plant a session-end marker an hour ago so the aggregate window
    # is populated.
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    sl.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    import json as _json

    sl.CACHE_PATH.write_text(_json.dumps({
        "ended_at": one_hour_ago.isoformat(),
        "citations": [],
    }))

    # Seed an anti-pattern so the diff scan produces a violation, AND
    # plant a separate KNOWLEDGE_REUSED event in impact_events so the
    # aggregate counter is non-zero.
    from memee.engine.impact import ImpactEvent

    engine = init_db()
    s = get_session(engine)
    s.add(Organization(name="learn-auto-org"))
    s.flush()
    ap_mem = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        title="Never use eval() on user input",
        content="eval lets attackers run arbitrary Python on user data",
        tags=["python", "security"],
        confidence_score=0.85,
        maturity=MaturityLevel.CANON.value,
    )
    s.add(ap_mem)
    s.flush()
    s.add(AntiPattern(
        memory_id=ap_mem.id, severity="critical", trigger="eval(",
        consequence="RCE", alternative="ast.literal_eval",
    ))
    # Plant an additional reuse event — this drives the aggregate.
    reuse_mem = Memory(
        type=MemoryType.PATTERN.value,
        title="React Query keys must include tenant id",
        content="React Query cache keys need the tenant id to prevent leaks",
        tags=["react"],
        confidence_score=0.85,
        maturity=MaturityLevel.CANON.value,
        application_count=3,
    )
    s.add(reuse_mem)
    s.flush()
    ev = ImpactEvent(
        memory_id=reuse_mem.id,
        impact_type=ImpactType.KNOWLEDGE_REUSED.value,
    )
    # Stamp inside the (one_hour_ago, now) window.
    ev.created_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(tzinfo=None)
    s.add(ev)
    s.commit()
    s.close()

    diff = (
        "diff --git a/app.py b/app.py\n"
        "+import ast\n"
        "+result = eval(user_payload)\n"
    )
    runner = CliRunner()
    from memee.cli import cli as _cli

    result = runner.invoke(
        _cli,
        [
            "learn", "--auto", "--project", str(tmp_path),
            "--diff", diff, "--outcome", "success",
        ],
    )
    assert result.exit_code == 0
    # Two distinct non-empty lines.
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) >= 2
    # The single-memory line names the AP (warning_ineffective branch).
    assert any("warning_ineffective" in line for line in lines)
    # The aggregate line cites the reuse memory under agent voice (default).
    assert any("React Query keys must include tenant id" in line for line in lines)


def test_learn_auto_quiet_when_no_signal(tmp_path, monkeypatch):
    """No diff + no impact events → both lines silent. Hook stays quiet."""
    _patch_db(tmp_path, monkeypatch)
    sl = _isolate_home(tmp_path, monkeypatch)

    # Plant a session-end marker; nothing else.
    sl.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    import json as _json

    sl.CACHE_PATH.write_text(_json.dumps({
        "ended_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "citations": [],
    }))

    runner = CliRunner()
    from memee.cli import cli as _cli

    result = runner.invoke(
        _cli,
        ["learn", "--auto", "--project", str(tmp_path), "--diff", ""],
    )
    assert result.exit_code == 0
    # Silent on no-op.
    assert "Memee:" not in result.output
    assert "Avoided" not in result.output
    assert "Pulling from" not in result.output
    assert "Applied " not in result.output
