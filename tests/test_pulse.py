"""Tests for v2.2.0 ``memee pulse`` — retrospective drill-down.

Covers:
- ``compute_pulse`` shape on empty DB → quiet payload
- Top-reused capped at 3, sorted by event count
- Day window respected (events outside the window excluded)
- Each bullet carries an 8-char ``[mem:xxxxxxxx]`` cite
- DB errors swallowed → quiet payload, never raises
- ``format_pulse`` renders all sections when data is present
- ``format_pulse`` quiet path = headline only
- Title truncation policy (60 chars + ``…``)
- CLI text format
- CLI JSON format
- CLI ``--days`` flag plumbs through
- ``compute_pulse`` works when ``memee.receipts`` is missing
  (the soft-dependency path)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from memee.cli import cli
from memee.engine.impact import ImpactEvent, ImpactType
from memee.storage.database import get_engine, get_session, init_db
from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryType,
    Organization,
    Severity,
)


# ── Helpers ────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Fresh DB at a per-test path with a default org."""
    db_path = tmp_path / "pulse.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))

    # Reload memee.config so the new env var sticks for ``get_engine()``.
    import importlib

    from memee import config as memee_config
    importlib.reload(memee_config)

    engine = init_db(get_engine(db_path))
    session = get_session(engine)
    org = Organization(name="pulse-test")
    session.add(org)
    session.commit()
    yield {"session": session, "org": org, "db_path": db_path}
    session.close()


def _seed_memory(
    session,
    org: Organization,
    *,
    title: str,
    mtype: str = MemoryType.PATTERN.value,
    maturity: str = MaturityLevel.HYPOTHESIS.value,
    confidence: float = 0.5,
    validation_count: int = 0,
    invalidation_count: int = 0,
    updated_at: datetime | None = None,
) -> Memory:
    mem = Memory(
        title=title,
        content=f"content for {title}",
        type=mtype,
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


def _seed_anti_pattern(session, org, title, severity=Severity.HIGH.value):
    mem = _seed_memory(
        session, org, title=title, mtype=MemoryType.ANTI_PATTERN.value,
        maturity=MaturityLevel.CANON.value, confidence=0.9,
    )
    ap = AntiPattern(
        memory_id=mem.id,
        severity=severity,
        trigger=f"trigger for {title}",
        consequence=f"consequence for {title}",
    )
    session.add(ap)
    session.commit()
    return mem


def _seed_event(
    session, memory_id, impact_type, *,
    created_at: datetime | None = None,
    time_saved_minutes: float = 0.0,
):
    ev = ImpactEvent(
        memory_id=memory_id,
        impact_type=impact_type,
        time_saved_minutes=time_saved_minutes,
    )
    session.add(ev)
    session.commit()
    if created_at is not None:
        ev.created_at = created_at
        session.commit()
    return ev


# ── 1. compute_pulse — shape & error swallowing ─────────────────────────


def test_compute_pulse_empty_db_returns_quiet_payload(isolated_db):
    """An empty DB → all lists empty, headline = 'Memee was quiet…',
    same key set as a populated payload (no defensive ``.get`` for
    consumers)."""
    from memee.pulse import compute_pulse

    out = compute_pulse(isolated_db["session"], days=7)
    assert isinstance(out, dict)
    expected_keys = {
        "days", "since", "until", "headline",
        "top_reused", "top_prevented", "recent_canon", "needs_review",
        "time_saved_minutes", "roi",
    }
    assert set(out) == expected_keys
    assert out["days"] == 7
    assert out["top_reused"] == []
    assert out["top_prevented"] == []
    assert out["recent_canon"] == []
    assert out["needs_review"] == []
    assert out["time_saved_minutes"] == 0
    assert out["roi"] is None
    assert "quiet" in out["headline"].lower()


def test_compute_pulse_top_reused_capped_at_3(isolated_db):
    """Five memories with reuse events → only top 3 returned, ordered
    by event count desc."""
    from memee.pulse import compute_pulse

    session = isolated_db["session"]
    org = isolated_db["org"]

    # Five memories, with descending reuse counts: 5, 4, 3, 2, 1.
    counts = [5, 4, 3, 2, 1]
    mems = []
    for i, n in enumerate(counts):
        mem = _seed_memory(session, org, title=f"Memory {i}")
        mems.append(mem)
        for _ in range(n):
            _seed_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)

    out = compute_pulse(session, days=7)
    assert len(out["top_reused"]) == 3
    apply_counts = [item["apply_count"] for item in out["top_reused"]]
    assert apply_counts == [5, 4, 3]
    # And ordered by count desc.
    assert apply_counts == sorted(apply_counts, reverse=True)


def test_compute_pulse_respects_days_window(isolated_db):
    """Events outside the ``days`` window must NOT contribute to
    top_reused / time_saved / headline counters."""
    from memee.pulse import compute_pulse

    session = isolated_db["session"]
    org = isolated_db["org"]
    now = datetime.now(timezone.utc)

    # Two memories: one with recent activity, one with ancient activity.
    recent_mem = _seed_memory(session, org, title="Recent reuse")
    old_mem = _seed_memory(session, org, title="Old reuse")

    _seed_event(
        session, recent_mem.id, ImpactType.KNOWLEDGE_REUSED.value,
        created_at=now - timedelta(days=2), time_saved_minutes=10.0,
    )
    _seed_event(
        session, old_mem.id, ImpactType.KNOWLEDGE_REUSED.value,
        created_at=now - timedelta(days=30), time_saved_minutes=999.0,
    )

    out = compute_pulse(session, days=7)
    assert len(out["top_reused"]) == 1
    assert out["top_reused"][0]["title"] == "Recent reuse"
    # The 999-minute ancient event must not leak into time_saved.
    assert out["time_saved_minutes"] == 10


def test_compute_pulse_includes_8_char_cite_tokens(isolated_db):
    """Every bucket bullet must carry a `[mem:xxxxxxxx]` cite (8 hex)."""
    import re

    from memee.pulse import compute_pulse

    session = isolated_db["session"]
    org = isolated_db["org"]
    now = datetime.now(timezone.utc)

    # Cover every bucket: top_reused, top_prevented, recent_canon, needs_review.
    reuse_mem = _seed_memory(session, org, title="Reused pattern")
    _seed_event(session, reuse_mem.id, ImpactType.KNOWLEDGE_REUSED.value)

    ap_mem = _seed_anti_pattern(session, org, title="Prevented mistake")
    _seed_event(session, ap_mem.id, ImpactType.MISTAKE_AVOIDED.value)

    _seed_memory(
        session, org, title="Fresh canon",
        maturity=MaturityLevel.CANON.value,
        updated_at=now - timedelta(days=1),
    )
    _seed_memory(
        session, org, title="Contested hypothesis",
        maturity=MaturityLevel.HYPOTHESIS.value,
        confidence=0.3, invalidation_count=2, validation_count=1,
    )

    out = compute_pulse(session, days=7)
    cite_re = re.compile(r"^\[mem:[0-9a-f]{8}\]$")
    for bucket in ("top_reused", "top_prevented", "recent_canon", "needs_review"):
        assert out[bucket], f"expected non-empty bucket: {bucket}"
        for item in out[bucket]:
            assert "cite" in item
            assert cite_re.match(item["cite"]), (
                f"bad cite token in {bucket}: {item['cite']!r}"
            )


def test_compute_pulse_swallows_db_errors(isolated_db, monkeypatch):
    """Any error inside the bucket queries collapses to the quiet
    payload. The pulse is a courtesy, not a feature gate."""
    from memee import pulse as pulse_module
    from memee.pulse import compute_pulse

    def boom(*_a, **_kw):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(pulse_module, "_query_top_reused", boom)
    out = compute_pulse(isolated_db["session"], days=7)
    # Quiet payload shape.
    assert out["headline"] == "Memee was quiet this week."
    assert out["top_reused"] == []
    assert out["top_prevented"] == []
    assert out["recent_canon"] == []
    assert out["needs_review"] == []
    assert out["time_saved_minutes"] == 0
    assert out["roi"] is None


# ── 2. format_pulse — rendering ─────────────────────────────────────────


def test_format_pulse_renders_all_sections_when_data_present(isolated_db):
    from memee.pulse import compute_pulse, format_pulse

    session = isolated_db["session"]
    org = isolated_db["org"]
    now = datetime.now(timezone.utc)

    reuse_mem = _seed_memory(session, org, title="Reused pattern A")
    _seed_event(
        session, reuse_mem.id, ImpactType.KNOWLEDGE_REUSED.value,
        time_saved_minutes=15.0,
    )
    ap_mem = _seed_anti_pattern(session, org, title="Prevented mistake A")
    _seed_event(session, ap_mem.id, ImpactType.MISTAKE_AVOIDED.value)
    _seed_memory(
        session, org, title="Fresh canon entry",
        maturity=MaturityLevel.CANON.value,
        updated_at=now - timedelta(days=1),
    )
    _seed_memory(
        session, org, title="Hypothesis on the rocks",
        maturity=MaturityLevel.HYPOTHESIS.value,
        confidence=0.25, invalidation_count=3,
    )

    payload = compute_pulse(session, days=7)
    rendered = format_pulse(payload)

    # Sections all present.
    assert "## Top reuses" in rendered
    assert "## Mistakes prevented" in rendered
    assert "## Recently promoted to canon" in rendered
    assert "## Needs review" in rendered
    assert "## ROI" in rendered

    # Bullet content from each bucket appears.
    assert "Reused pattern A" in rendered
    assert "Prevented mistake A" in rendered
    assert "Fresh canon entry" in rendered
    assert "Hypothesis on the rocks" in rendered

    # Cite tokens appear.
    assert "[mem:" in rendered

    # ROI footer mentions the proxy honestly.
    assert "min" in rendered.lower()
    assert "proxy" in rendered.lower()


def test_format_pulse_quiet_when_empty(isolated_db):
    """Empty payload → headline only, no section headers."""
    from memee.pulse import compute_pulse, format_pulse

    payload = compute_pulse(isolated_db["session"], days=7)
    rendered = format_pulse(payload)

    # Just the headline. No section headers.
    assert "Memee was quiet this week." in rendered
    assert "## Top reuses" not in rendered
    assert "## Mistakes prevented" not in rendered
    assert "## Recently promoted to canon" not in rendered
    assert "## ROI" not in rendered


def test_format_pulse_truncates_long_titles(isolated_db):
    """Titles over 60 chars → 59 chars + ``…``. Matches the Stop receipt
    and session_ledger truncation policy."""
    from memee.pulse import compute_pulse, format_pulse

    session = isolated_db["session"]
    org = isolated_db["org"]

    long_title = "x" * 120
    mem = _seed_memory(session, org, title=long_title)
    _seed_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)

    payload = compute_pulse(session, days=7)
    rendered = format_pulse(payload)

    # The full 120-char title must NOT appear in the rendered output…
    assert long_title not in rendered
    # …but the truncated form does (59 x's + ellipsis).
    assert ("x" * 59 + "…") in rendered


# ── 3. CLI ──────────────────────────────────────────────────────────────


def _patch_cli_db(tmp_path, monkeypatch):
    """Repoint memee at a fresh tmp DB for CliRunner tests.

    The CLI's ``init_db()`` uses ``memee.config.settings`` which we have
    to reload after stamping the env var. Same trick used by
    test_memee_why.
    """
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    return db_path


def test_cli_pulse_text_format(tmp_path, monkeypatch):
    """``memee pulse`` (default text) on an empty DB prints the quiet
    headline and exits 0."""
    _patch_cli_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(cli, ["pulse"])
    assert result.exit_code == 0, result.output
    assert "quiet" in result.output.lower()


def test_cli_pulse_json_format(tmp_path, monkeypatch):
    """``--format json`` returns parseable JSON with the canonical
    pulse shape."""
    _patch_cli_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    # Seed a tiny bit of activity so JSON has something interesting in it.
    from memee.storage.database import get_session, init_db as _init_db
    from memee.storage.models import Organization

    engine = _init_db()
    session = get_session(engine)
    org = session.query(Organization).first()
    mem = _seed_memory(session, org, title="JSON-mode reuse")
    _seed_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)
    session.close()

    result = runner.invoke(cli, ["pulse", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # Canonical key set.
    expected_keys = {
        "days", "since", "until", "headline",
        "top_reused", "top_prevented", "recent_canon", "needs_review",
        "time_saved_minutes", "roi",
    }
    assert set(payload) == expected_keys
    # The seeded reuse made it into top_reused.
    assert payload["top_reused"], "expected at least one top_reused entry"
    first = payload["top_reused"][0]
    assert first["cite"].startswith("[mem:")
    assert first["cite"].endswith("]")


def test_cli_pulse_days_flag(tmp_path, monkeypatch):
    """``--days 30`` widens the window so events older than 7d count."""
    _patch_cli_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    from memee.storage.database import get_session, init_db as _init_db
    from memee.storage.models import Organization

    engine = _init_db()
    session = get_session(engine)
    org = session.query(Organization).first()
    mem = _seed_memory(session, org, title="Old but relevant")
    # Plant a 14-day-old event — outside default 7d window, inside 30d.
    old_when = datetime.now(timezone.utc) - timedelta(days=14)
    _seed_event(
        session, mem.id, ImpactType.KNOWLEDGE_REUSED.value,
        created_at=old_when,
    )
    session.close()

    # Default (7d): empty bucket.
    result7 = runner.invoke(cli, ["pulse", "--format", "json"])
    assert result7.exit_code == 0, result7.output
    payload7 = json.loads(result7.output)
    assert payload7["top_reused"] == []
    assert payload7["days"] == 7

    # Widen to 30d: the event is in window now.
    result30 = runner.invoke(cli, ["pulse", "--days", "30", "--format", "json"])
    assert result30.exit_code == 0, result30.output
    payload30 = json.loads(result30.output)
    assert payload30["days"] == 30
    assert payload30["top_reused"], "expected the 14-day-old event in 30d window"
    assert payload30["top_reused"][0]["title"] == "Old but relevant"


def test_cli_pulse_works_when_receipts_module_missing(
    tmp_path, monkeypatch, isolated_db,
):
    """``memee.receipts`` (M1) may not have merged yet — the pulse must
    still produce a valid headline by falling back to the hand-rolled
    formatter inside ``pulse._fallback_headline``."""
    from memee.pulse import compute_pulse

    # Pretend the receipts module simply isn't importable. Two layers of
    # protection: clear it from sys.modules so a real import re-resolves,
    # and stub the import to raise. Belt-and-suspenders.
    monkeypatch.delitem(sys.modules, "memee.receipts", raising=False)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "memee.receipts" or name.startswith("memee.receipts."):
            raise ImportError("simulated: memee.receipts not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    session = isolated_db["session"]
    org = isolated_db["org"]
    mem = _seed_memory(session, org, title="No receipts module here")
    _seed_event(session, mem.id, ImpactType.KNOWLEDGE_REUSED.value)

    out = compute_pulse(session, days=7)
    # Fallback headline mentions "last 7 days" + "memory applied".
    assert "last 7 days" in out["headline"]
    assert "applied" in out["headline"]
    # And the bucket data still came through (the import error must not
    # have torpedoed the rest of the function).
    assert out["top_reused"], "bucket query must still run when receipts is absent"
    assert out["top_reused"][0]["title"] == "No receipts module here"
