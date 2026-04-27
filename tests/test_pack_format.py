"""Round-trip tests for the ``.memee`` pack format."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from memee import packs_format as pf
from memee.engine.packs import (
    LEDGER_PATH,  # noqa: F401  (re-exported for monkeypatching)
    build_export_bundle,
    export_pack,
    install_pack,
    verify_file,
)
from memee.storage.models import Memory, MemoryType


def _seed_memories(session) -> None:
    """Seed enough validated/canon memories that an export has rows."""
    rows = [
        Memory(
            type=MemoryType.PATTERN.value,
            title="Always pass timeout to outbound HTTP",
            content=(
                "Use requests.get(url, timeout=10). Without an explicit "
                "timeout the call can hang the worker indefinitely on a "
                "slow upstream. Default httplib timeout is None.\n\n"
                "Why: hung sockets accumulate until the worker is unresponsive."
            ),
            tags=["python", "http", "reliability"],
            maturity="canon",
            confidence_score=0.9,
            source_type="human",
        ),
        Memory(
            type=MemoryType.PATTERN.value,
            title="Use connection pooling for SQLAlchemy",
            content=(
                "Configure pool_size, max_overflow, and pool_pre_ping=True. "
                "Without pool_pre_ping a stale connection returns OperationalError."
            ),
            tags=["python", "sqlalchemy", "database"],
            maturity="validated",
            confidence_score=0.75,
            source_type="human",
        ),
        Memory(
            type=MemoryType.LESSON.value,
            title="Always log structured errors with context",
            content=(
                "Include trace_id, user_id, and the failing operation name "
                "in every error log. Plain text logs are unsearchable at scale."
            ),
            tags=["observability", "logging"],
            maturity="validated",
            confidence_score=0.8,
            source_type="human",
        ),
        Memory(
            type=MemoryType.PATTERN.value,
            title="Skipped — too low confidence",
            content="Body content here that meets the length requirement.",
            tags=["python"],
            maturity="hypothesis",
            confidence_score=0.4,
            source_type="human",
        ),
    ]
    session.add_all(rows)
    session.commit()


@pytest.fixture
def isolated_ledger(tmp_path, monkeypatch):
    """Redirect the ledger to a temp file so tests don't touch ~/.memee."""
    fake = tmp_path / "packs.json"
    monkeypatch.setattr("memee.engine.packs.LEDGER_PATH", fake)
    return fake


def test_manifest_serialise_roundtrip():
    m = pf.PackManifest(
        name="my-pack",
        version="1.2.3",
        title="Roundtrip pack",
        description="Multi-line\ndescription with quotes \"like this\"",
        author="me",
        license="MIT",
        confidence_cap=0.55,
        stack=["python", "fastapi"],
        counts={"memories": 3, "patterns": 2, "anti_patterns": 1,
                "decisions": 0, "lessons": 0},
        provenance=[{"kind": "seed", "note": "test"}],
    )
    blob = m.to_toml_str().encode("utf-8")
    parsed = pf.parse_manifest(blob)
    assert parsed.name == "my-pack"
    assert parsed.version == "1.2.3"
    assert parsed.confidence_cap == pytest.approx(0.55)
    assert parsed.stack == ["python", "fastapi"]
    assert parsed.counts["memories"] == 3
    assert parsed.provenance[0]["kind"] == "seed"


def test_manifest_rejects_missing_required():
    bad = b'name = "x"\nversion = "1"\n'  # missing title
    with pytest.raises(ValueError, match="title"):
        pf.parse_manifest(bad)


def test_jsonl_writer_skips_bad_lines():
    blob = (
        b'{"type": "pattern", "title": "ok", "content": "body", "tags": ["a"]}\n'
        b'{ this line is invalid json\n'
        b'{"type": "lesson", "title": "ok2", "content": "body2", "tags": ["b"]}\n'
    )
    rows = list(pf.read_memories_jsonl(blob))
    # Bad line dropped, two good ones survive.
    assert len(rows) == 2
    assert rows[0]["title"] == "ok"
    assert rows[1]["title"] == "ok2"


def test_pack_roundtrip_export_install_count(session, tmp_path, isolated_ledger):
    _seed_memories(session)

    out = export_pack(
        session,
        name="rt-pack",
        version="0.1.0",
        title="Round-trip test pack",
        out=tmp_path,
        confidence_cap=0.6,
        stack=["python"],
    )
    assert out.out_path is not None
    assert out.out_path.exists()
    # 3 of 4 seeded memories qualify (one is hypothesis@0.4).
    assert out.memories == 3
    assert out.signed is False
    assert out.size_bytes > 0

    # Verify before installing.
    verified = verify_file(out.out_path)
    assert verified.valid is True
    assert verified.memories == 3
    assert verified.name == "rt-pack"
    assert verified.signed is False

    # Install into a SECOND, fresh DB so we can compare counts cleanly.
    from memee.storage.database import get_engine, get_session, init_db
    db2 = tmp_path / "second.db"
    engine2 = init_db(get_engine(db2))
    s2 = get_session(engine2)

    result = install_pack(s2, out.out_path, allow_unsigned=True)
    assert result.no_op is False
    # All 3 should import cleanly into a fresh DB.
    assert result.imported == 3
    assert result.merged == 0
    assert result.skipped == 0
    assert result.rejected == 0

    # Confidence capped at confidence_cap on import.
    imported = s2.query(Memory).all()
    assert len(imported) == 3
    for m in imported:
        assert m.source_type == "import"
        assert m.confidence_score <= 0.6 + 1e-9
        # Provenance entry from the pack.
        chain = m.evidence_chain or []
        assert any(e.get("kind") == "pack" for e in chain)
    titles = sorted(m.title for m in imported)
    assert "Always pass timeout to outbound HTTP" in titles


def test_export_canon_only_filters(session, tmp_path, isolated_ledger):
    _seed_memories(session)
    bundle, summary = build_export_bundle(
        session,
        name="canon-only",
        version="0.1.0",
        title="Canon only",
        canon_only=True,
    )
    # Only the one canon memory survives.
    assert summary["memories"] == 1
    rows = list(pf.read_memories_jsonl(bundle.memories_bytes))
    assert rows[0]["maturity"] == "canon"


def test_export_strips_identity_columns(session, tmp_path, isolated_ledger):
    """No identity columns should be present in exported JSONL."""
    _seed_memories(session)
    # Stamp an identity-bearing field on one of the rows.
    first = session.query(Memory).first()
    first.owner_id = "owner-abc"
    first.team_id = "team-xyz"
    first.source_session = "session-secret"
    first.source_url = "https://internal.example.com/pr/1"
    session.commit()

    bundle, _ = build_export_bundle(
        session, name="x", version="0.1", title="x",
    )
    raw = bundle.memories_bytes.decode("utf-8")
    # None of the identity columns should leak into the exported text.
    for needle in (
        "owner-abc", "team-xyz", "session-secret",
        "internal.example.com",
        "owner_id", "team_id", "source_session", "source_url",
    ):
        assert needle not in raw, f"identity leak: {needle!r}"


def test_seed_python_web_installs_into_fresh_db(tmp_path, monkeypatch):
    """The shipped seed pack ``packs/seed/python-web.memee`` should install
    cleanly into a fresh DB and yield ≥25 memories.

    This is the smoke test for the cold-start story: ``memee pack install
    python-web.memee`` is the v2.0.0 way to bootstrap a new install.
    """
    monkeypatch.setattr("memee.engine.packs.LEDGER_PATH", tmp_path / "packs.json")

    seed_path = (
        Path(__file__).resolve().parents[1]
        / "packs" / "seed" / "python-web.memee"
    )
    if not seed_path.exists():
        pytest.skip(f"seed pack not built yet: {seed_path}")

    from memee.engine.packs import install_pack
    from memee.storage.database import get_engine, get_session, init_db

    s2 = get_session(init_db(get_engine(tmp_path / "seed-receiver.db")))
    result = install_pack(s2, seed_path, allow_unsigned=True)

    assert result.imported >= 25, (
        f"expected ≥25 memories from python-web seed, got "
        f"imported={result.imported}, merged={result.merged}, "
        f"skipped={result.skipped}, rejected={result.rejected}"
    )
    assert s2.query(Memory).count() >= 25
    # Sanity: every imported memory carries import provenance.
    for m in s2.query(Memory).all():
        assert m.source_type == "import"
        assert any(
            (e.get("kind") == "pack" and e.get("name") == "python-web")
            for e in (m.evidence_chain or [])
        )


def test_stream_export_to_bytesio(session, tmp_path, isolated_ledger):
    from memee.engine.packs import export_pack_to_stream
    _seed_memories(session)
    buf = io.BytesIO()
    result = export_pack_to_stream(
        session, name="streamy", version="0.1.0",
        title="Streamy", stream=buf,
    )
    assert result.memories == 3
    raw = buf.getvalue()
    assert raw[:2] == b"\x1f\x8b"  # gzip magic
    bundle = pf.read_pack_from_bytes(raw)
    parsed = pf.parse_manifest(bundle.manifest_bytes)
    assert parsed.name == "streamy"
