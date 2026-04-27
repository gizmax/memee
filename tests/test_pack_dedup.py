"""Pack install: idempotency + dedup-into-existing tests."""

from __future__ import annotations


from memee.engine.packs import (
    LEDGER_PATH,  # noqa: F401
    export_pack,
    install_pack,
)
from memee.storage.models import Memory, MemoryType


def _seed_source(session) -> None:
    session.add_all([
        Memory(
            type=MemoryType.PATTERN.value,
            title="Always pass timeout to outbound HTTP",
            content=(
                "Use requests.get(url, timeout=10). Without an explicit "
                "timeout the call can hang the worker indefinitely on a "
                "slow upstream."
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
                "Configure pool_size, max_overflow, and pool_pre_ping=True "
                "on the engine."
            ),
            tags=["python", "sqlalchemy", "database"],
            maturity="validated",
            confidence_score=0.78,
            source_type="human",
        ),
    ])
    session.commit()


def test_install_twice_is_idempotent(session, tmp_path, monkeypatch):
    monkeypatch.setattr("memee.engine.packs.LEDGER_PATH", tmp_path / "packs.json")
    _seed_source(session)

    out = export_pack(
        session, name="dedup-pack", version="0.1.0",
        title="Dedup pack", out=tmp_path,
    )

    # First install — fresh receiver DB.
    from memee.storage.database import get_engine, get_session, init_db
    s2 = get_session(init_db(get_engine(tmp_path / "rcv.db")))

    r1 = install_pack(s2, out.out_path, allow_unsigned=True)
    assert r1.no_op is False
    assert r1.imported == 2

    # Second install of the SAME (name, version) is a no-op.
    r2 = install_pack(s2, out.out_path, allow_unsigned=True)
    assert r2.no_op is True
    assert r2.imported == 0

    # Total memory count unchanged.
    assert s2.query(Memory).count() == 2


def test_install_into_db_with_matching_local_memory_merges(
    session, tmp_path, monkeypatch,
):
    """A pack memory that matches an existing local memory should be merged
    via the quality gate and tagged with source_type='import'."""
    monkeypatch.setattr("memee.engine.packs.LEDGER_PATH", tmp_path / "packs.json")
    _seed_source(session)

    out = export_pack(
        session, name="merge-pack", version="0.1.0",
        title="Merge pack", out=tmp_path,
    )

    from memee.storage.database import get_engine, get_session, init_db
    s2 = get_session(init_db(get_engine(tmp_path / "rcv-merge.db")))

    # Pre-existing memory in receiver that matches one in the pack closely
    # enough for the quality gate's dedup pass.
    pre = Memory(
        type=MemoryType.PATTERN.value,
        title="Always pass timeout to outbound HTTP",
        content=(
            "Set requests.get(url, timeout=10) on every call. Default "
            "httplib timeout is None and a hung socket blocks the worker."
        ),
        tags=["python", "http", "reliability"],
        maturity="validated",
        confidence_score=0.7,
        source_type="human",
    )
    s2.add(pre)
    s2.commit()
    pre_id = pre.id

    result = install_pack(s2, out.out_path, allow_unsigned=True)
    # One memory merged into the pre-existing row, the other imported new.
    assert result.merged + result.imported == 2
    assert result.merged >= 1

    # The pre-existing row should have been touched: source_type flipped to
    # "import", evidence_chain has both a dedup_merge entry and a pack entry.
    refreshed = s2.get(Memory, pre_id)
    assert refreshed is not None
    assert refreshed.source_type == "import"
    chain = refreshed.evidence_chain or []
    kinds = {e.get("kind") or e.get("type") for e in chain}
    assert "pack" in kinds
    assert "dedup_merge" in kinds


def test_install_skips_invalid_rows(session, tmp_path, monkeypatch):
    """A row missing required fields should be skipped, not crash the install."""
    monkeypatch.setattr("memee.engine.packs.LEDGER_PATH", tmp_path / "packs.json")
    from memee import packs_format as pf

    # Hand-build a tiny pack with one good row and one bad row.
    rows = [
        {"type": "pattern", "title": "Valid pattern with body",
         "content": "Body content that meets the minimum length",
         "tags": ["test"]},
        {"type": "pattern", "title": "missing tags"},  # missing content + tags
    ]
    import io
    buf = io.BytesIO()
    pf.write_memories_jsonl(rows, buf)
    manifest = pf.PackManifest(
        name="invalid-row-pack",
        version="0.1.0",
        title="Invalid row pack",
        confidence_cap=0.6,
        counts={"memories": 2, "patterns": 2},
    )
    bundle = pf.PackBundle(
        manifest_bytes=manifest.to_toml_str().encode("utf-8"),
        memories_bytes=buf.getvalue(),
    )
    out = tmp_path / "invalid.memee"
    pf.write_pack(bundle, out)

    from memee.storage.database import get_engine, get_session, init_db
    s2 = get_session(init_db(get_engine(tmp_path / "rcv-bad.db")))

    result = install_pack(s2, out, allow_unsigned=True)
    assert result.imported == 1
    assert result.skipped == 1
    assert any("missing" in r for r in result.skipped_reasons)


def test_ledger_records_install(session, tmp_path, monkeypatch):
    ledger = tmp_path / "packs.json"
    monkeypatch.setattr("memee.engine.packs.LEDGER_PATH", ledger)
    _seed_source(session)

    out = export_pack(
        session, name="ledger-pack", version="0.2.0",
        title="Ledger pack", out=tmp_path,
    )

    from memee.engine.packs import list_installed
    from memee.storage.database import get_engine, get_session, init_db
    s2 = get_session(init_db(get_engine(tmp_path / "rcv-l.db")))
    install_pack(s2, out.out_path, allow_unsigned=True)

    assert ledger.exists()
    rows = list_installed(ledger)
    assert len(rows) == 1
    assert rows[0]["name"] == "ledger-pack"
    assert rows[0]["version"] == "0.2.0"
    assert rows[0]["imported"] >= 1
