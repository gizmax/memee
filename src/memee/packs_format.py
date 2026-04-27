"""File-level helpers for the ``.memee`` pack format.

A ``.memee`` file is a gzipped tarball with this layout::

    foo.memee/
    ├── manifest.toml          required
    ├── memories.jsonl         required — one memory per line
    ├── signature.bin          optional — ed25519 signature
    └── pubkey.pem             optional — author public key

This module does NOT touch SQLAlchemy. It only deals with bytes on disk:
read/write a manifest, stream a jsonl, build the tarball, sign + verify.
The DB-aware engine layer (``memee.engine.packs``) calls into here to
materialise an export or hydrate an install.

Signing uses ed25519 via the ``cryptography`` library when available. The
signature covers ``SHA256(manifest.toml || memories.jsonl)``. Without the
optional dep installed, the engine falls back to unsigned packs and prints
a note — verification of an existing signed pack is skipped (treated as
"signature present but unverifiable, refuse without --unsigned").
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Iterator

logger = logging.getLogger(__name__)

# ── Optional cryptography import ────────────────────────────────────────────


def _has_cryptography() -> bool:
    """Return True iff ``cryptography`` is importable."""
    try:
        import cryptography  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: F401
        return True
    except ImportError:
        return False


# ── Manifest model ──────────────────────────────────────────────────────────


@dataclass
class PackManifest:
    """In-memory representation of ``manifest.toml``.

    Required fields per ``docs/pack-format.md``: name, version, title,
    confidence_cap. Everything else is optional.
    """
    name: str
    version: str
    title: str
    confidence_cap: float = 0.6
    description: str = ""
    author: str = ""
    homepage: str = ""
    license: str = ""
    created: str = ""
    stack: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    provenance: list[dict] = field(default_factory=list)
    # Catch-all for forward-compatible fields we don't know yet.
    extra: dict = field(default_factory=dict)

    def to_toml_str(self) -> str:
        """Serialise to a TOML string. Hand-rolled to avoid pulling in a
        TOML *writer* dep — the spec is fixed and small enough that this
        stays readable.
        """
        lines: list[str] = []
        lines.append(f'name = "{_toml_escape(self.name)}"')
        lines.append(f'version = "{_toml_escape(self.version)}"')
        lines.append(f'title = "{_toml_escape(self.title)}"')
        if self.description:
            # Multi-line strings preserve description shape from hand-authored TOML.
            esc = self.description.replace('"""', '\\"""')
            lines.append(f'description = """\n{esc}\n"""')
        if self.author:
            lines.append(f'author = "{_toml_escape(self.author)}"')
        if self.homepage:
            lines.append(f'homepage = "{_toml_escape(self.homepage)}"')
        if self.license:
            lines.append(f'license = "{_toml_escape(self.license)}"')
        if self.created:
            lines.append(f'created = "{_toml_escape(self.created)}"')
        lines.append(f'confidence_cap = {self.confidence_cap}')
        if self.stack:
            stack_str = ", ".join(f'"{_toml_escape(s)}"' for s in self.stack)
            lines.append(f'stack = [{stack_str}]')
        if self.counts:
            lines.append("")
            lines.append("[counts]")
            for k in ("memories", "patterns", "anti_patterns", "decisions", "lessons"):
                if k in self.counts:
                    lines.append(f"{k} = {int(self.counts[k])}")
            for k, v in self.counts.items():
                if k not in {"memories", "patterns", "anti_patterns", "decisions", "lessons"}:
                    lines.append(f"{k} = {int(v)}")
        for entry in self.provenance:
            lines.append("")
            lines.append("[[provenance]]")
            for k, v in entry.items():
                if isinstance(v, str):
                    lines.append(f'{k} = "{_toml_escape(v)}"')
                elif isinstance(v, bool):
                    lines.append(f'{k} = {"true" if v else "false"}')
                elif isinstance(v, (int, float)):
                    lines.append(f"{k} = {v}")
        return "\n".join(lines) + "\n"


def _toml_escape(s: str) -> str:
    """Escape a TOML basic-string. Backslash + quote only — these manifests
    don't carry binary data, so the full escape table is overkill.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def parse_manifest(toml_bytes: bytes) -> PackManifest:
    """Parse ``manifest.toml`` bytes into a ``PackManifest``."""
    import tomllib
    data = tomllib.loads(toml_bytes.decode("utf-8"))
    name = data.get("name")
    version = data.get("version")
    title = data.get("title")
    if not (isinstance(name, str) and name.strip()):
        raise ValueError("manifest missing required field: name")
    if not (isinstance(version, str) and version.strip()):
        raise ValueError("manifest missing required field: version")
    if not (isinstance(title, str) and title.strip()):
        raise ValueError("manifest missing required field: title")
    confidence_cap = data.get("confidence_cap", 0.6)
    if not isinstance(confidence_cap, (int, float)):
        raise ValueError("manifest.confidence_cap must be a number")
    confidence_cap = float(confidence_cap)
    if not 0.0 <= confidence_cap <= 1.0:
        raise ValueError("manifest.confidence_cap must be in [0, 1]")

    return PackManifest(
        name=name.strip(),
        version=version.strip(),
        title=title.strip(),
        confidence_cap=confidence_cap,
        description=str(data.get("description", "")).strip(),
        author=str(data.get("author", "")),
        homepage=str(data.get("homepage", "")),
        license=str(data.get("license", "")),
        created=str(data.get("created", "")),
        stack=list(data.get("stack", []) or []),
        counts={k: int(v) for k, v in (data.get("counts", {}) or {}).items()
                if isinstance(v, (int, float))},
        provenance=list(data.get("provenance", []) or []),
        extra={k: v for k, v in data.items() if k not in {
            "name", "version", "title", "confidence_cap", "description",
            "author", "homepage", "license", "created", "stack", "counts",
            "provenance",
        }},
    )


# ── JSONL streaming ─────────────────────────────────────────────────────────


# Identity columns we MUST strip from a memory before exporting.
# Pack memories are public; provenance lives in the manifest, not per-row.
EXPORT_STRIP_FIELDS = (
    "owner_id",
    "team_id",
    "organization_id",
    "validated_project_ids",
    "same_project_val_counts",
    "model_families_seen",
    "source_session",
    "source_url",
    "source_commit",
    # Internal counters that don't survive cross-DB transit either.
    "id",
    "embedding",
    "merge_count",
    "model_count",
    "promoted_from",
    "deprecated_at",
    "deprecated_reason",
    "expires_at",
    "last_validated_at",
    "last_applied_at",
    "created_at",
    "updated_at",
    "scope",
    "validation_count",
    "invalidation_count",
    "application_count",
    "project_count",
    "source_agent",
    "source_model",
    "context",
    "quality_score",
)

# Required fields on a pack memory line per the spec.
REQUIRED_MEMORY_FIELDS = ("type", "title", "content", "tags")


def memory_to_export_dict(mem) -> dict:
    """Convert a ``Memory`` ORM row into the export-line dict.

    The format is documented in ``docs/pack-format.md`` §"Memory line format".
    Identity columns are dropped. Specialised type fields (severity for
    anti-patterns) are inlined when the relationship is loaded.
    """
    row: dict = {
        "type": mem.type,
        "title": mem.title or "",
        "content": mem.content or "",
        "tags": list(mem.tags or []),
    }
    if mem.maturity:
        row["maturity"] = mem.maturity
    if mem.confidence_score is not None:
        row["confidence"] = float(mem.confidence_score)
    if mem.summary:
        row["summary"] = mem.summary
    # Anti-pattern severity is stored on the AntiPattern child row.
    ap = getattr(mem, "anti_pattern", None)
    if ap is not None and getattr(ap, "severity", None):
        row["severity"] = ap.severity
    # Carry forward only the public bits of the evidence chain — drop any
    # entry whose 'agent' or 'session' looks like personal provenance.
    chain = list(mem.evidence_chain or [])
    if chain:
        row["evidence_chain"] = [_strip_evidence(e) for e in chain]
    return row


def _strip_evidence(entry) -> dict:
    """Sanitise one evidence_chain entry — keep type/ref/timestamp/outcome,
    drop anything that looks like identity/PII.
    """
    if not isinstance(entry, dict):
        return {"note": str(entry)}
    keep = {"type", "kind", "ref", "ts", "timestamp", "outcome", "from_title",
            "similarity", "name", "version"}
    return {k: v for k, v in entry.items() if k in keep}


def write_memories_jsonl(rows: Iterator[dict] | list[dict], out: IO[bytes]) -> int:
    """Write ``rows`` as JSONL bytes to ``out``. Returns the row count."""
    n = 0
    for row in rows:
        line = json.dumps(row, ensure_ascii=False, sort_keys=True)
        out.write(line.encode("utf-8"))
        out.write(b"\n")
        n += 1
    return n


def read_memories_jsonl(blob: bytes) -> Iterator[dict]:
    """Yield one dict per non-blank line of ``blob``. Bad lines are skipped
    with a warning — a corrupt mid-file row should not abort the install of
    the rest of the pack.
    """
    for i, raw in enumerate(blob.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            yield json.loads(line.decode("utf-8") if isinstance(line, bytes) else line)
        except json.JSONDecodeError as e:
            logger.warning("memories.jsonl line %d: bad JSON, skipping (%s)", i, e)


def validate_memory_row(row: dict) -> list[str]:
    """Return a list of issues with this memory row. Empty list = valid."""
    issues: list[str] = []
    for k in REQUIRED_MEMORY_FIELDS:
        if k not in row:
            issues.append(f"missing required field: {k}")
    if "tags" in row and not isinstance(row["tags"], list):
        issues.append("tags must be a list")
    if "tags" in row and isinstance(row["tags"], list) and not row["tags"]:
        issues.append("tags must contain at least one tag")
    if "title" in row and not isinstance(row["title"], str):
        issues.append("title must be a string")
    if "content" in row and not isinstance(row["content"], str):
        issues.append("content must be a string")
    if "type" in row and row["type"] not in {
        "pattern", "anti_pattern", "decision", "lesson", "observation",
    }:
        issues.append(f"unknown type: {row['type']}")
    return issues


# ── Tar + gzip packaging ────────────────────────────────────────────────────


@dataclass
class PackBundle:
    """In-memory representation of a ``.memee`` file.

    Only carries the four files the format defines. Everything else is
    derivable: ``digest`` from manifest+memories, ``signed`` from
    ``signature is not None``.
    """
    manifest_bytes: bytes
    memories_bytes: bytes
    signature: bytes | None = None
    pubkey_pem: bytes | None = None

    @property
    def signed(self) -> bool:
        return self.signature is not None and self.pubkey_pem is not None

    def digest(self) -> bytes:
        """SHA256(manifest.toml || memories.jsonl) — what the signature covers."""
        h = hashlib.sha256()
        h.update(self.manifest_bytes)
        h.update(self.memories_bytes)
        return h.digest()


def write_pack(bundle: PackBundle, out_path: Path | str) -> Path:
    """Tar+gzip ``bundle`` to ``out_path``. Returns the resolved path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wb") as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            _add_member(tar, "manifest.toml", bundle.manifest_bytes)
            _add_member(tar, "memories.jsonl", bundle.memories_bytes)
            if bundle.signature is not None:
                _add_member(tar, "signature.bin", bundle.signature)
            if bundle.pubkey_pem is not None:
                _add_member(tar, "pubkey.pem", bundle.pubkey_pem)
    return out_path


def write_pack_to_stream(bundle: PackBundle, out: IO[bytes]) -> None:
    """Tar+gzip ``bundle`` into an open binary stream."""
    with gzip.GzipFile(fileobj=out, mode="wb") as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            _add_member(tar, "manifest.toml", bundle.manifest_bytes)
            _add_member(tar, "memories.jsonl", bundle.memories_bytes)
            if bundle.signature is not None:
                _add_member(tar, "signature.bin", bundle.signature)
            if bundle.pubkey_pem is not None:
                _add_member(tar, "pubkey.pem", bundle.pubkey_pem)


def _add_member(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def read_pack(src_path: Path | str) -> PackBundle:
    """Read and parse a ``.memee`` file. Raises ValueError on missing
    required members.
    """
    src_path = Path(src_path)
    with gzip.open(src_path, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r") as tar:
            return _bundle_from_tar(tar)


def read_pack_from_bytes(data: bytes) -> PackBundle:
    """Read a ``.memee`` file from raw bytes (e.g. a downloaded URL body)."""
    with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as gz:
        with tarfile.open(fileobj=gz, mode="r") as tar:
            return _bundle_from_tar(tar)


def _bundle_from_tar(tar: tarfile.TarFile) -> PackBundle:
    manifest_bytes: bytes | None = None
    memories_bytes: bytes | None = None
    signature: bytes | None = None
    pubkey_pem: bytes | None = None
    for member in tar.getmembers():
        if not member.isfile():
            continue
        # Strip leading path prefix if present (some tar tools wrap in a dir).
        name = Path(member.name).name
        f = tar.extractfile(member)
        if f is None:
            continue
        data = f.read()
        if name == "manifest.toml":
            manifest_bytes = data
        elif name == "memories.jsonl":
            memories_bytes = data
        elif name == "signature.bin":
            signature = data
        elif name == "pubkey.pem":
            pubkey_pem = data
    if manifest_bytes is None:
        raise ValueError("pack missing manifest.toml")
    if memories_bytes is None:
        raise ValueError("pack missing memories.jsonl")
    return PackBundle(
        manifest_bytes=manifest_bytes,
        memories_bytes=memories_bytes,
        signature=signature,
        pubkey_pem=pubkey_pem,
    )


# ── Signing / verification (ed25519) ────────────────────────────────────────


class SigningUnavailable(Exception):
    """Raised when the optional ``cryptography`` dep isn't installed."""


def generate_keypair() -> tuple[bytes, bytes]:
    """Return ``(private_pem, public_pem)`` for a fresh ed25519 keypair.

    Used by tests and by users who want to mint a signing key. Raises
    ``SigningUnavailable`` if ``cryptography`` isn't installed.
    """
    if not _has_cryptography():
        raise SigningUnavailable(
            "Install with: pip install memee[pack]  (provides 'cryptography')"
        )
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv_pem, pub_pem


def sign_bundle(
    bundle: PackBundle,
    private_key_pem: bytes,
) -> PackBundle:
    """Return a copy of ``bundle`` with signature + pubkey populated.

    Signs ``SHA256(manifest.toml || memories.jsonl)`` with ed25519. Raises
    ``SigningUnavailable`` if ``cryptography`` isn't installed.
    """
    if not _has_cryptography():
        raise SigningUnavailable(
            "Install with: pip install memee[pack]  (provides 'cryptography')"
        )
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise ValueError("private key is not an ed25519 key")
    digest = bundle.digest()
    signature = priv.sign(digest)
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return PackBundle(
        manifest_bytes=bundle.manifest_bytes,
        memories_bytes=bundle.memories_bytes,
        signature=signature,
        pubkey_pem=pub_pem,
    )


def verify_bundle(bundle: PackBundle) -> tuple[bool, str]:
    """Verify the bundle's signature against its bundled pubkey.

    Returns ``(ok, reason)``. ``ok=True`` covers two cases:
      * Bundle is unsigned (no signature, no pubkey). Caller decides
        whether to refuse via ``--unsigned`` policy.
      * Bundle is signed and the signature checks out.

    ``ok=False`` means a signature was claimed but invalid (tamper, wrong
    key, or ``cryptography`` not installed so we can't verify).
    """
    if bundle.signature is None and bundle.pubkey_pem is None:
        return True, "unsigned"
    if bundle.signature is None or bundle.pubkey_pem is None:
        return False, "incomplete signature (one of signature.bin/pubkey.pem missing)"
    if not _has_cryptography():
        return False, "cryptography not installed; cannot verify signature"
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        pub = serialization.load_pem_public_key(bundle.pubkey_pem)
    except Exception as e:
        return False, f"could not load pubkey.pem: {e}"
    if not isinstance(pub, Ed25519PublicKey):
        return False, "bundled pubkey is not ed25519"
    digest = bundle.digest()
    try:
        pub.verify(bundle.signature, digest)
    except InvalidSignature:
        return False, "signature does not match (tampered or wrong key)"
    return True, "valid"
