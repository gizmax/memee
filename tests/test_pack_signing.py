"""Pack signing + verification tests (ed25519)."""

from __future__ import annotations

import pytest

from memee import packs_format as pf
from memee.engine.packs import (
    LEDGER_PATH,  # noqa: F401
    build_export_bundle,
    verify_file,
)
from memee.storage.models import Memory, MemoryType

# All tests in this module require the optional ``cryptography`` dep.
pytestmark = pytest.mark.skipif(
    not pf._has_cryptography(),
    reason="cryptography not installed (pip install memee[pack])",
)


def _seed(session):
    session.add_all([
        Memory(
            type=MemoryType.PATTERN.value,
            title="Always validate input on the boundary",
            content=(
                "Validate every external input — query string, request body, "
                "env var — at the trust boundary. Inside the boundary, code "
                "may assume the data is shaped."
            ),
            tags=["security", "validation"],
            maturity="canon",
            confidence_score=0.92,
            source_type="human",
        ),
        Memory(
            type=MemoryType.LESSON.value,
            title="Cache invalidation is the second hard problem",
            content="Every cache needs a story for staleness, ownership, and eviction.",
            tags=["caching", "lessons"],
            maturity="validated",
            confidence_score=0.8,
            source_type="human",
        ),
    ])
    session.commit()


def test_sign_and_verify_roundtrip(session, tmp_path, monkeypatch):
    monkeypatch.setattr("memee.engine.packs.LEDGER_PATH", tmp_path / "packs.json")
    _seed(session)
    priv_pem, pub_pem = pf.generate_keypair()
    bundle, summary = build_export_bundle(
        session, name="signed-pack", version="0.1.0",
        title="Signed pack", private_key_pem=priv_pem,
    )
    assert bundle.signed
    assert bundle.signature is not None
    assert bundle.pubkey_pem is not None

    out_path = tmp_path / "signed-pack.memee"
    pf.write_pack(bundle, out_path)

    result = verify_file(out_path)
    assert result.valid is True
    assert result.signed is True
    assert result.reason == "valid"


def test_tamper_byte_breaks_verification(session, tmp_path, monkeypatch):
    monkeypatch.setattr("memee.engine.packs.LEDGER_PATH", tmp_path / "packs.json")
    _seed(session)
    priv_pem, _ = pf.generate_keypair()
    bundle, _ = build_export_bundle(
        session, name="tamper-test", version="0.1.0",
        title="Tamper test", private_key_pem=priv_pem,
    )
    # Flip a byte in memories_bytes — preserves structure (still valid JSONL)
    # but invalidates the signature digest.
    raw = bytearray(bundle.memories_bytes)
    # Change one ASCII char in the body — find a lowercase letter and bump it.
    for i, b in enumerate(raw):
        if 0x61 <= b <= 0x7a:  # a..z
            raw[i] = b + 1
            break
    tampered = pf.PackBundle(
        manifest_bytes=bundle.manifest_bytes,
        memories_bytes=bytes(raw),
        signature=bundle.signature,
        pubkey_pem=bundle.pubkey_pem,
    )

    ok, reason = pf.verify_bundle(tampered)
    assert ok is False
    assert "match" in reason or "tampered" in reason


def test_install_refuses_tampered_pack_without_unsigned_flag(
    session, tmp_path, monkeypatch,
):
    """End-to-end: a tampered file fails install unless --unsigned is given."""
    from memee.engine.packs import install_pack
    monkeypatch.setattr("memee.engine.packs.LEDGER_PATH", tmp_path / "packs.json")
    _seed(session)

    priv_pem, _ = pf.generate_keypair()
    bundle, _ = build_export_bundle(
        session, name="tamper-installer", version="0.1.0",
        title="Tamper installer", private_key_pem=priv_pem,
    )
    raw = bytearray(bundle.memories_bytes)
    for i, b in enumerate(raw):
        if 0x61 <= b <= 0x7a:
            raw[i] = b + 1
            break
    tampered = pf.PackBundle(
        manifest_bytes=bundle.manifest_bytes,
        memories_bytes=bytes(raw),
        signature=bundle.signature,
        pubkey_pem=bundle.pubkey_pem,
    )
    out = tmp_path / "tampered.memee"
    pf.write_pack(tampered, out)

    # Fresh receiver DB.
    from memee.storage.database import get_engine, get_session, init_db
    s2 = get_session(init_db(get_engine(tmp_path / "rcv.db")))
    with pytest.raises(ValueError, match="signature"):
        install_pack(s2, out, allow_unsigned=False)

    # With --unsigned the install proceeds.
    result = install_pack(s2, out, allow_unsigned=True)
    assert result.imported >= 1
    # Signed flag is still True (the bundle has signature bytes), but
    # verification failed — caller is responsible for using the signal.
    assert result.signed is True
