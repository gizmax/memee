"""v2.4.18 — pack-signature trust anchor + remote install source policy.

Pre-v2.4.18 the pack signature was decorative: ``verify_bundle`` checked
the signature against the pubkey bundled INSIDE the archive, so an
attacker could ship their own keypair and the signature would always
verify. Unsigned packs returned ``(True, "unsigned")`` from the same
function, and ``install_pack`` accepted them by default even when the
``--unsigned`` flag suggested otherwise. ``--from-url`` was the loudest
hole — a remote URL could push an attacker-keyed pack and it would
install silently.

This patch adds two layers:

* :func:`packs_format.pubkey_fingerprint` + :func:`is_trusted_bundle`
  consult the ``MEMEE_PACK_TRUSTED_KEYS`` env var to anchor signatures.
* ``install_pack(source_kind="remote")`` refuses unsigned **and**
  signed-but-untrusted packs unless ``allow_unsigned=True``. ``"local"``
  keeps the legacy warn-and-install flow so wheel-bundled seed packs
  still install with no ceremony.
"""

from __future__ import annotations

import pytest

from memee import packs_format as pf
from memee.engine.packs import LEDGER_PATH, build_export_bundle, install_pack  # noqa: F401
from memee.storage.models import MaturityLevel, Memory, MemoryType


pytestmark = pytest.mark.skipif(
    not pf._has_cryptography(),
    reason="cryptography not installed (pip install memee[pack])",
)


def _seed(session):
    session.add(
        Memory(
            type=MemoryType.PATTERN.value,
            title="Use connection pooling in production",
            content="Pool DB connections to avoid exhaustion under load.",
            tags=["database", "performance"],
            maturity=MaturityLevel.VALIDATED.value,
            confidence_score=0.85,
            source_type="human",
        )
    )
    session.commit()


def _build_bundle(session, monkeypatch, tmp_path, *, signed: bool, name: str):
    """Build + write a .memee pack file. Returns its on-disk path."""
    monkeypatch.setattr("memee.engine.packs.LEDGER_PATH", tmp_path / "packs.json")
    _seed(session)
    priv = pub = None
    if signed:
        priv, pub = pf.generate_keypair()
    bundle, _summary = build_export_bundle(
        session, name=name, version="0.1.0", title=f"{name} title",
        private_key_pem=priv,
    )
    path = tmp_path / f"{name}.memee"
    with path.open("wb") as fh:
        pf.write_pack_to_stream(bundle, fh)
    return path, bundle, pub


# ── Fingerprint + trust store ──


def test_pubkey_fingerprint_stable_across_pem_reencoding():
    priv, pub = pf.generate_keypair()
    fp1 = pf.pubkey_fingerprint(pub)
    # Round-trip through PEM whitespace variation — the raw-key
    # fingerprint must not depend on the envelope encoding.
    re_encoded = b"\n".join(pub.strip().splitlines()) + b"\n"
    fp2 = pf.pubkey_fingerprint(re_encoded)
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex


def test_pubkey_fingerprint_empty_on_bad_input():
    assert pf.pubkey_fingerprint(b"") == ""
    assert pf.pubkey_fingerprint(b"not a pem") == ""


def test_is_trusted_bundle_requires_fingerprint_in_env(monkeypatch, session, tmp_path):
    _path, bundle, pub = _build_bundle(
        session, monkeypatch, tmp_path, signed=True, name="t1",
    )
    monkeypatch.delenv("MEMEE_PACK_TRUSTED_KEYS", raising=False)
    assert pf.is_trusted_bundle(bundle) is False

    monkeypatch.setenv(
        "MEMEE_PACK_TRUSTED_KEYS", pf.pubkey_fingerprint(pub),
    )
    assert pf.is_trusted_bundle(bundle) is True


def test_is_trusted_bundle_false_for_unsigned(monkeypatch, session, tmp_path):
    _path, bundle, _ = _build_bundle(
        session, monkeypatch, tmp_path, signed=False, name="u1",
    )
    monkeypatch.setenv("MEMEE_PACK_TRUSTED_KEYS", "a" * 64)
    assert pf.is_trusted_bundle(bundle) is False


# ── install_pack source policy ──


def test_remote_install_rejects_unsigned(monkeypatch, session, tmp_path):
    path, _bundle, _ = _build_bundle(
        session, monkeypatch, tmp_path, signed=False, name="remote_unsigned",
    )
    monkeypatch.delenv("MEMEE_PACK_TRUSTED_KEYS", raising=False)
    with pytest.raises(ValueError, match="remote pack is unsigned"):
        install_pack(
            session, path, source_kind="remote",
            ledger_path=tmp_path / "packs.json",
        )


def test_remote_install_rejects_signed_but_untrusted_key(
    monkeypatch, session, tmp_path,
):
    path, _bundle, _pub = _build_bundle(
        session, monkeypatch, tmp_path, signed=True, name="remote_untrusted",
    )
    monkeypatch.setenv("MEMEE_PACK_TRUSTED_KEYS", "")  # empty allowlist
    with pytest.raises(ValueError, match="not in MEMEE_PACK_TRUSTED_KEYS"):
        install_pack(
            session, path, source_kind="remote",
            ledger_path=tmp_path / "packs.json",
        )


def test_remote_install_accepts_signed_and_trusted_key(
    monkeypatch, session, tmp_path,
):
    path, _bundle, pub = _build_bundle(
        session, monkeypatch, tmp_path, signed=True, name="remote_trusted",
    )
    monkeypatch.setenv(
        "MEMEE_PACK_TRUSTED_KEYS", pf.pubkey_fingerprint(pub),
    )
    result = install_pack(
        session, path, source_kind="remote",
        ledger_path=tmp_path / "packs.json",
    )
    assert result.name == "remote_trusted"
    assert result.signed is True


def test_remote_install_bypassed_by_allow_unsigned(
    monkeypatch, session, tmp_path,
):
    path, _bundle, _ = _build_bundle(
        session, monkeypatch, tmp_path, signed=False, name="remote_force",
    )
    monkeypatch.delenv("MEMEE_PACK_TRUSTED_KEYS", raising=False)
    result = install_pack(
        session, path, source_kind="remote", allow_unsigned=True,
        ledger_path=tmp_path / "packs.json",
    )
    assert result.name == "remote_force"
    assert result.signed is False


def test_local_install_still_accepts_unsigned(monkeypatch, session, tmp_path):
    """Backward compat — local seed-pack flow must keep working."""
    path, _bundle, _ = _build_bundle(
        session, monkeypatch, tmp_path, signed=False, name="local_seed",
    )
    monkeypatch.delenv("MEMEE_PACK_TRUSTED_KEYS", raising=False)
    result = install_pack(
        session, path, source_kind="local",
        ledger_path=tmp_path / "packs.json",
    )
    assert result.name == "local_seed"
    assert result.signed is False


def test_local_install_accepts_signed_untrusted_without_env(
    monkeypatch, session, tmp_path,
):
    """Local + signed + no trust anchor = still installs (legacy flow).
    The trust gate only fires on remote sources; locally-acquired files
    are already user-vetted by virtue of being on disk."""
    path, _bundle, _ = _build_bundle(
        session, monkeypatch, tmp_path, signed=True, name="local_signed",
    )
    monkeypatch.setenv("MEMEE_PACK_TRUSTED_KEYS", "")
    result = install_pack(
        session, path, source_kind="local",
        ledger_path=tmp_path / "packs.json",
    )
    assert result.name == "local_signed"
    assert result.signed is True
