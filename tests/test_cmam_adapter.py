"""Tests for the CMAM (Claude Managed Agents Memory) adapter.

We only exercise the `fs` backend in tests — no network calls. The `api`
backend is a thin httpx wrapper with the same surface, so we validate shape
rather than actually hitting Anthropic.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from memee.adapters.cmam import (
    CMAMConfig,
    HARD_BYTES_LIMIT,
    MAX_MEMORY_BYTES,
    _chunk_if_needed,
    _FSBackend,
    _redact,
    _slug,
    is_eligible_for_cmam,
    iter_eligible_memories,
    memory_to_cmam_path,
    render_memory,
    sync_to_cmam,
    verify_store,
)
from memee.storage.models import (
    AntiPattern,
    Decision,
    MaturityLevel,
    Memory,
    MemoryType,
    Project,
    Severity,
)


def _canon_pattern(session, org, *, title="Use timeout on HTTP", tags=None):
    m = Memory(
        type=MemoryType.PATTERN.value,
        maturity=MaturityLevel.CANON.value,
        title=title,
        content="Always pass timeout= to requests.get to avoid hangs.",
        tags=tags or ["python", "http"],
        confidence_score=0.92,
        validation_count=12,
        project_count=6,
    )
    session.add(m)
    session.flush()
    return m


def _critical_ap(session, *, title="Never eval() user input"):
    m = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        maturity=MaturityLevel.HYPOTHESIS.value,   # critical bypasses maturity
        title=title,
        content="eval() on untrusted input = arbitrary code execution.",
        tags=["security", "python"],
        confidence_score=0.6,
    )
    session.add(m)
    session.flush()
    ap = AntiPattern(
        memory_id=m.id,
        severity=Severity.CRITICAL.value,
        trigger="calling eval() on user-supplied data",
        consequence="arbitrary code execution, RCE",
        alternative="use ast.literal_eval or a proper parser",
    )
    session.add(ap)
    m.anti_pattern = ap
    session.flush()
    return m


# ── Mapping / rendering ──


def test_slug_lowercases_and_strips():
    assert _slug("Hello, World!") == "hello-world"
    assert _slug("   ") == "untitled"
    assert _slug("x" * 200).startswith("x" * 80) and len(_slug("x" * 200)) <= 80


def test_redact_strips_common_secrets():
    bad = "key=sk-abcdefghijklmnopqrstuvwxyz1234 and AKIAABCDEFGHIJKLMNOP tail"
    out = _redact(bad)
    assert "sk-abc" not in out
    assert "AKIAABCD" not in out
    assert out.count("[REDACTED]") == 2


def test_memory_to_cmam_path_by_type(session, org):
    pattern = _canon_pattern(session, org)
    ap = _critical_ap(session)
    dec = Memory(
        type=MemoryType.DECISION.value,
        maturity=MaturityLevel.CANON.value,
        title="Postgres over SQLite",
        content="concurrent writes require postgres",
        tags=["db"],
        confidence_score=0.9,
    )
    session.add(dec)
    session.flush()

    assert memory_to_cmam_path(pattern).startswith("/canon/patterns/")
    assert memory_to_cmam_path(ap).startswith("/warnings/critical/")
    assert memory_to_cmam_path(dec).startswith("/decisions/")


def test_render_memory_has_frontmatter_and_body(session, org):
    m = _canon_pattern(session, org)
    text = render_memory(m)
    assert text.startswith("---\n")
    assert f"id: {m.id}" in text
    assert "maturity: canon" in text
    assert "confidence: 0.92" in text
    assert "# Use timeout on HTTP" in text
    assert "Always pass timeout=" in text


def test_render_anti_pattern_includes_severity_trigger(session, org):
    m = _critical_ap(session)
    text = render_memory(m)
    assert "**Severity:** critical" in text
    assert "**Trigger:**" in text
    assert "**Consequence:**" in text


def test_render_redacts_secrets(session, org):
    m = Memory(
        type=MemoryType.LESSON.value,
        maturity=MaturityLevel.CANON.value,
        title="Do not hardcode secrets",
        content="Example bad token: sk-abcdefghijklmnopqrstuvwxyz1234567890 here",
        tags=["security"],
        confidence_score=0.9,
    )
    session.add(m)
    session.flush()
    out = render_memory(m, redact=True)
    assert "sk-abcd" not in out
    assert "[REDACTED]" in out


# ── Chunking ──


def test_chunk_small_content_returns_single_file():
    content = "---\nid: abc\n---\n# hi\n\nshort body\n"
    out = _chunk_if_needed(content, "/canon/patterns/hi.md")
    assert len(out) == 1
    assert out[0][0] == "/canon/patterns/hi.md"


def test_chunk_large_content_splits_into_parts():
    header = "---\nid: abc\n---\n\n# big\n"
    body = "x" * (MAX_MEMORY_BYTES * 3)
    content = header + body
    out = _chunk_if_needed(content, "/canon/patterns/big.md")
    assert len(out) >= 3
    for path, data in out:
        assert path.startswith("/canon/patterns/big.part-")
        assert len(data.encode("utf-8")) <= MAX_MEMORY_BYTES
    # Every chunk keeps the header so it's readable standalone
    assert all("id: abc" in d for _, d in out)


# ── Eligibility ──


def test_only_canon_and_critical_are_eligible(session, org):
    canon = _canon_pattern(session, org)
    critical = _critical_ap(session)
    hyp = Memory(
        type=MemoryType.PATTERN.value,
        maturity=MaturityLevel.HYPOTHESIS.value,
        title="Maybe cache queries",
        content="unproven idea about caching",
        tags=["db"],
        confidence_score=0.4,
    )
    session.add(hyp)
    from datetime import datetime, timezone
    deprecated = Memory(
        type=MemoryType.PATTERN.value,
        maturity=MaturityLevel.CANON.value,
        title="Old pattern",
        content="obsolete approach",
        tags=["legacy"],
        confidence_score=0.9,
        deprecated_at=datetime.now(timezone.utc),
    )
    session.add(deprecated)
    session.commit()

    assert is_eligible_for_cmam(canon) is True
    assert is_eligible_for_cmam(critical) is True
    assert is_eligible_for_cmam(hyp) is False
    assert is_eligible_for_cmam(deprecated) is False

    all_eligible = iter_eligible_memories(session)
    ids = {m.id for m in all_eligible}
    assert canon.id in ids
    assert critical.id in ids
    assert hyp.id not in ids
    assert deprecated.id not in ids


# ── Filesystem backend ──


def test_fs_backend_put_and_list(tmp_path):
    backend = _FSBackend(tmp_path / "store")
    resp = backend.put("/canon/patterns/x.md", "hello\n")
    assert resp["status"] == "ok"
    assert resp["sha256"] == hashlib.sha256(b"hello\n").hexdigest()

    items = backend.list()
    assert any(i["path"] == "/canon/patterns/x.md" for i in items)


def test_fs_backend_blocks_path_traversal(tmp_path):
    backend = _FSBackend(tmp_path / "store")
    with pytest.raises(ValueError):
        backend.put("/canon/../../etc/passwd", "uhoh")


def test_fs_backend_sha256_precondition_detects_conflict(tmp_path):
    backend = _FSBackend(tmp_path / "store")
    backend.put("/a.md", "v1")
    # Wrong expected sha → conflict
    resp = backend.put("/a.md", "v2", expected_sha256="0" * 64)
    assert resp["status"] == "conflict"
    # Right expected sha → ok
    current = hashlib.sha256(b"v1").hexdigest()
    resp = backend.put("/a.md", "v2", expected_sha256=current)
    assert resp["status"] == "ok"


# ── Full sync ──


def test_sync_writes_canon_and_critical_to_fs(session, org, tmp_path):
    _canon_pattern(session, org, title="Use timeout on HTTP", tags=["python", "http"])
    _critical_ap(session, title="Never eval() user input")
    session.commit()

    cfg = CMAMConfig(
        store_id="test-store",
        backend="fs",
        local_root=tmp_path / "cmam",
    )
    result = sync_to_cmam(session, cfg)

    assert result.pushed >= 3  # pattern + anti-pattern + index
    assert result.rejected == []
    # Index exists
    index = tmp_path / "cmam" / "_index.md"
    assert index.exists()
    assert "Use timeout on HTTP" in index.read_text()
    assert "Never eval() user input" in index.read_text()
    # Critical AP path
    warnings_dir = tmp_path / "cmam" / "warnings" / "critical"
    assert any(p.suffix == ".md" for p in warnings_dir.iterdir())


def test_sync_is_idempotent(session, org, tmp_path):
    _canon_pattern(session, org)
    session.commit()

    cfg = CMAMConfig(store_id="test", backend="fs", local_root=tmp_path / "cmam")
    first = sync_to_cmam(session, cfg)
    second = sync_to_cmam(session, cfg)

    # Second run writes the same content over existing files → updates, no new
    assert second.pushed == 0
    assert second.updated == first.pushed


def test_sync_dry_run_writes_nothing(session, org, tmp_path):
    _canon_pattern(session, org)
    _critical_ap(session)
    session.commit()

    root = tmp_path / "cmam"
    cfg = CMAMConfig(store_id="test", backend="fs", local_root=root)
    result = sync_to_cmam(session, cfg, dry_run=True)

    assert result.pushed >= 3
    # Root may exist from backend init but contain nothing
    if root.exists():
        assert not any(root.rglob("*.md"))


def test_sync_respects_hard_byte_limit(session, org, tmp_path, monkeypatch):
    _canon_pattern(session, org)
    session.commit()

    # Simulate a store that's already nearly full
    monkeypatch.setattr(
        "memee.adapters.cmam.HARD_BYTES_LIMIT",
        50,  # bytes — any new file exceeds this
    )
    cfg = CMAMConfig(store_id="full", backend="fs", local_root=tmp_path / "cmam")
    result = sync_to_cmam(session, cfg)

    assert result.pushed == 0
    assert len(result.rejected) >= 1
    assert any("100 MB" in r["reason"] or "exceed" in r["reason"] for r in result.rejected)


def test_verify_store_reports_headroom(session, org, tmp_path):
    _canon_pattern(session, org)
    session.commit()

    cfg = CMAMConfig(store_id="t", backend="fs", local_root=tmp_path / "cmam")
    sync_to_cmam(session, cfg)
    info = verify_store(cfg)

    assert info["memories"] >= 2  # canon + _index.md
    assert info["bytes"] > 0
    assert info["bytes_pct_of_limit"] < 1.0  # nowhere near 100 MB
    assert info["count_pct_of_limit"] < 1.0


def test_api_backend_requires_api_key(monkeypatch):
    # Ensure ANTHROPIC_API_KEY is not set
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = CMAMConfig(store_id="x", backend="api", api_key=None)
    # Instantiating via the public sync path should fail with clear error
    from memee.adapters.cmam import _make_backend
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        _make_backend(cfg)
