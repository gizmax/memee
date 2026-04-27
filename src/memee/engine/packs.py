"""Engine: ``.memee`` pack export / install / verify / list.

Bridges the format helpers in :mod:`memee.packs_format` with the live DB.

Idempotency ledger lives at ``~/.memee/packs.json`` — a flat JSON list of
install records. v2 deliberately avoids a schema migration; the ledger is
small (one entry per pack install) and can be promoted to a SQL table later
without an upgrade path break.
"""

from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from memee import packs_format as pf
from memee.engine.quality_gate import merge_duplicate, run_quality_gate
from memee.storage.models import AntiPattern, Memory, MemoryType, Severity

logger = logging.getLogger(__name__)

LEDGER_PATH = Path.home() / ".memee" / "packs.json"


# ── Ledger I/O ──────────────────────────────────────────────────────────────


def _ledger_path(override: Path | None = None) -> Path:
    """Resolve the ledger path lazily, so that tests monkeypatching
    ``memee.engine.packs.LEDGER_PATH`` after import still take effect.
    Function default arguments freeze at def-time; module attribute access
    re-reads on every call.
    """
    if override is not None:
        return override
    # Re-read the module attr each call so monkeypatch is observed.
    import memee.engine.packs as _self
    return _self.LEDGER_PATH


def _read_ledger(path: Path | None = None) -> list[dict]:
    """Return the install ledger or [] if it doesn't exist / is corrupt.

    A corrupt ledger should not block install — we log and start fresh, the
    user only loses the "you already installed this" idempotency check for
    that one run.
    """
    p = _ledger_path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("packs ledger %s unreadable: %s — starting fresh", p, e)
        return []
    if not isinstance(data, list):
        logger.warning("packs ledger %s is not a list — starting fresh", p)
        return []
    return data


def _write_ledger(entries: list[dict], path: Path | None = None) -> None:
    """Atomic write — tmp file + rename — so a kill mid-write can't corrupt
    the ledger that other CLI runs depend on.
    """
    p = _ledger_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    os.replace(tmp, p)


def list_installed(path: Path | None = None) -> list[dict]:
    """Return install ledger (read-only) for ``memee pack list``."""
    return _read_ledger(path)


def find_installed(name: str, version: str | None = None,
                   path: Path | None = None) -> dict | None:
    """Return the latest ledger entry matching name (and optionally version)."""
    rows = _read_ledger(path)
    matches = [r for r in rows if r.get("name") == name]
    if version is not None:
        matches = [r for r in matches if r.get("version") == version]
    if not matches:
        return None
    matches.sort(key=lambda r: r.get("installed_at", ""), reverse=True)
    return matches[0]


# ── Export ──────────────────────────────────────────────────────────────────


@dataclass
class ExportResult:
    out_path: Path | None
    name: str
    version: str
    memories: int
    signed: bool
    size_bytes: int


def _select_export_memories(
    session: Session,
    canon_only: bool = False,
) -> list[Memory]:
    """Return memories eligible for export.

    Per ``docs/pack-format.md`` §"Export semantics":
      * maturity in ("validated", "canon")
      * confidence >= 0.7

    ``canon_only=True`` restricts further to maturity == "canon".
    """
    q = session.query(Memory).filter(Memory.confidence_score >= 0.7)
    if canon_only:
        q = q.filter(Memory.maturity == "canon")
    else:
        q = q.filter(Memory.maturity.in_(["validated", "canon"]))
    return q.all()


def _compute_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {
        "memories": len(rows),
        "patterns": sum(1 for r in rows if r.get("type") == "pattern"),
        "anti_patterns": sum(1 for r in rows if r.get("type") == "anti_pattern"),
        "decisions": sum(1 for r in rows if r.get("type") == "decision"),
        "lessons": sum(1 for r in rows if r.get("type") == "lesson"),
    }
    return counts


def build_export_bundle(
    session: Session,
    name: str,
    version: str,
    title: str,
    *,
    description: str = "",
    author: str = "",
    homepage: str = "",
    license: str = "",
    confidence_cap: float = 0.6,
    stack: list[str] | None = None,
    canon_only: bool = False,
    private_key_pem: bytes | None = None,
) -> tuple[pf.PackBundle, dict]:
    """Materialise the export as an in-memory PackBundle.

    Returns ``(bundle, summary_dict)`` so callers (CLI / tests) can inspect
    the counts and signing status before deciding what to do with the bytes.
    """
    memories = _select_export_memories(session, canon_only=canon_only)
    rows = [pf.memory_to_export_dict(m) for m in memories]
    counts = _compute_counts(rows)

    manifest = pf.PackManifest(
        name=name,
        version=version,
        title=title,
        description=description,
        author=author,
        homepage=homepage,
        license=license,
        created=datetime.now(timezone.utc).date().isoformat(),
        confidence_cap=confidence_cap,
        stack=list(stack or []),
        counts=counts,
        provenance=[{
            "kind": "exported",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "note": "memee pack export",
        }],
    )

    manifest_bytes = manifest.to_toml_str().encode("utf-8")
    buf = io.BytesIO()
    pf.write_memories_jsonl(rows, buf)
    memories_bytes = buf.getvalue()

    bundle = pf.PackBundle(
        manifest_bytes=manifest_bytes,
        memories_bytes=memories_bytes,
    )

    sign_status = "unsigned"
    if private_key_pem is not None:
        try:
            bundle = pf.sign_bundle(bundle, private_key_pem)
            sign_status = "signed"
        except pf.SigningUnavailable:
            sign_status = "unsigned (cryptography missing)"

    summary = {
        "name": name,
        "version": version,
        "memories": len(rows),
        "counts": counts,
        "sign_status": sign_status,
    }
    return bundle, summary


def export_pack(
    session: Session,
    name: str,
    version: str,
    title: str,
    out: Path | str | None,
    *,
    description: str = "",
    author: str = "",
    homepage: str = "",
    license: str = "",
    confidence_cap: float = 0.6,
    stack: list[str] | None = None,
    canon_only: bool = False,
    private_key_pem: bytes | None = None,
) -> ExportResult:
    """Build, optionally sign, and write the pack to disk.

    ``out`` of ``None`` defaults to ``<cwd>/<name>.memee``. When ``out`` is
    a directory, the file is written inside it as ``<name>.memee``.
    """
    bundle, summary = build_export_bundle(
        session,
        name=name,
        version=version,
        title=title,
        description=description,
        author=author,
        homepage=homepage,
        license=license,
        confidence_cap=confidence_cap,
        stack=stack,
        canon_only=canon_only,
        private_key_pem=private_key_pem,
    )

    if out is None:
        out_path = Path.cwd() / f"{name}.memee"
    else:
        out_path = Path(out)
        if out_path.exists() and out_path.is_dir():
            out_path = out_path / f"{name}.memee"

    pf.write_pack(bundle, out_path)
    size = out_path.stat().st_size

    return ExportResult(
        out_path=out_path,
        name=name,
        version=version,
        memories=summary["memories"],
        signed=bundle.signed,
        size_bytes=size,
    )


def export_pack_to_stream(
    session: Session,
    name: str,
    version: str,
    title: str,
    stream,
    *,
    description: str = "",
    confidence_cap: float = 0.6,
    stack: list[str] | None = None,
    canon_only: bool = False,
    private_key_pem: bytes | None = None,
) -> ExportResult:
    """Stream variant for ``--out -`` (stdout) export."""
    bundle, summary = build_export_bundle(
        session,
        name=name,
        version=version,
        title=title,
        description=description,
        confidence_cap=confidence_cap,
        stack=stack,
        canon_only=canon_only,
        private_key_pem=private_key_pem,
    )
    pf.write_pack_to_stream(bundle, stream)
    return ExportResult(
        out_path=None,
        name=name,
        version=version,
        memories=summary["memories"],
        signed=bundle.signed,
        size_bytes=0,
    )


# ── Install ─────────────────────────────────────────────────────────────────


@dataclass
class InstallResult:
    name: str
    version: str
    imported: int = 0
    merged: int = 0
    skipped: int = 0
    rejected: int = 0
    signed: bool = False
    no_op: bool = False
    notes: list[str] = field(default_factory=list)
    skipped_reasons: list[str] = field(default_factory=list)


def _resolve_source(source: str | Path, *, allow_url: bool = True) -> bytes:
    """Load a pack's bytes from a local path or HTTPS URL.

    URLs use ``urllib.request`` to avoid pulling in ``requests`` as a hard
    dep. HTTPS only — refuses ``http://`` or ``file://`` to keep the install
    surface tight.
    """
    if isinstance(source, (str,)) and source.startswith(("http://", "https://")):
        if not allow_url:
            raise ValueError("URL sources disabled here")
        if source.startswith("http://"):
            raise ValueError("only https:// URLs are supported for pack install")
        import urllib.request
        with urllib.request.urlopen(source, timeout=30) as resp:  # noqa: S310
            return resp.read()
    p = Path(source)
    if not p.exists():
        raise FileNotFoundError(f"pack file not found: {p}")
    return p.read_bytes()


def install_pack(
    session: Session,
    source: str | Path,
    *,
    organization_id: str | None = None,
    allow_unsigned: bool = False,
    overwrite_version: bool = False,
    ledger_path: Path | None = None,
    pack_filename: str | None = None,
) -> InstallResult:
    """Install a ``.memee`` pack into ``session``'s DB.

    Returns an :class:`InstallResult`. The caller (CLI) is responsible for
    presenting warnings/prompts; this function makes binary decisions:

      * Signature invalid + ``allow_unsigned=False`` → raises ValueError.
      * Same name + version already installed → no-op (idempotent).
      * Same name + different version + ``overwrite_version=False`` →
        raises ValueError so the CLI can prompt the user.
    """
    raw = _resolve_source(source)
    bundle = pf.read_pack_from_bytes(raw)

    ok, reason = pf.verify_bundle(bundle)
    if not ok and not allow_unsigned:
        raise ValueError(
            f"pack signature check failed: {reason}. "
            f"Re-run with --unsigned to install anyway."
        )

    manifest = pf.parse_manifest(bundle.manifest_bytes)

    # Idempotency.
    existing = find_installed(manifest.name, manifest.version, path=ledger_path)
    if existing is not None:
        return InstallResult(
            name=manifest.name,
            version=manifest.version,
            no_op=True,
            signed=bundle.signed,
            notes=[f"pack {manifest.name} v{manifest.version} already installed"],
        )

    name_match = find_installed(manifest.name, path=ledger_path)
    if name_match is not None and not overwrite_version:
        raise ValueError(
            f"pack {manifest.name} already installed at version "
            f"{name_match['version']}. Re-run with --upgrade to install "
            f"{manifest.version} alongside."
        )

    # Stream the JSONL.
    rows = list(pf.read_memories_jsonl(bundle.memories_bytes))
    result = InstallResult(
        name=manifest.name,
        version=manifest.version,
        signed=bundle.signed,
    )

    pack_evidence = {
        "kind": "pack",
        "name": manifest.name,
        "version": manifest.version,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    for i, row in enumerate(rows, start=1):
        issues = pf.validate_memory_row(row)
        if issues:
            result.skipped += 1
            msg = f"line {i}: {'; '.join(issues)}"
            result.skipped_reasons.append(msg)
            logger.warning("pack %s: skipped row %d: %s", manifest.name, i, msg)
            continue

        title = row["title"]
        content = row["content"]
        tags = [str(t).lower() for t in row.get("tags", []) if isinstance(t, str)]
        mtype = row["type"]

        gate = run_quality_gate(
            session, title, content, tags, mtype, source="import",
        )

        if not gate.accepted and gate.merged and gate.merged_id:
            existing_mem = session.get(Memory, gate.merged_id)
            if existing_mem is not None:
                merge_duplicate(
                    session, existing_mem, content, tags,
                    new_title=title, similarity=gate.dedup_similarity,
                )
                # Append the pack-evidence entry on top of the dedup_merge entry
                # the merge helper already wrote.
                chain = list(existing_mem.evidence_chain or [])
                chain.append(pack_evidence)
                existing_mem.evidence_chain = chain
                # Per docs/pack-format.md §"Import semantics": once an
                # imported memory is folded into an existing row, the row's
                # source_type drops to "import" so the ×0.6 multiplier
                # applies on the next confidence update. User-validated
                # memories outrank pack defaults via confidence_cap + the
                # subsequent validation cycle, not by clinging to a higher
                # source_type.
                existing_mem.source_type = "import"
                session.commit()
                result.merged += 1
                continue

        if not gate.accepted:
            result.rejected += 1
            logger.info(
                "pack %s: row %d rejected by quality gate: %s",
                manifest.name, i, gate.issues,
            )
            continue

        # Insert.
        confidence = float(row.get("confidence", 0.5))
        confidence = max(0.0, min(confidence, manifest.confidence_cap))
        maturity = row.get("maturity", "validated")
        memory = Memory(
            type=mtype,
            title=title,
            content=content,
            summary=row.get("summary") or None,
            tags=tags,
            maturity=maturity,
            confidence_score=confidence,
            source_type="import",
            quality_score=gate.quality_score,
            evidence_chain=[pack_evidence],
        )
        if organization_id is not None:
            memory.organization_id = organization_id
        session.add(memory)
        session.flush()

        # Specialised child rows.
        if mtype == MemoryType.ANTI_PATTERN.value:
            severity = row.get("severity") or Severity.MEDIUM.value
            ap = AntiPattern(
                memory_id=memory.id,
                severity=severity,
                trigger=row.get("trigger") or _derive_trigger(content),
                consequence=row.get("consequence") or _derive_consequence(content),
                alternative=row.get("alternative") or _derive_alternative(content),
            )
            session.add(ap)

        result.imported += 1

    session.commit()

    # Record in ledger.
    ledger = _read_ledger(ledger_path)
    ledger.append({
        "name": manifest.name,
        "version": manifest.version,
        "title": manifest.title,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "file": str(source) if not str(source).startswith("http") else None,
        "url": str(source) if str(source).startswith("http") else None,
        "filename": pack_filename,
        "signed": bundle.signed,
        "imported": result.imported,
        "merged": result.merged,
        "skipped": result.skipped,
        "rejected": result.rejected,
    })
    _write_ledger(ledger, ledger_path)

    return result


def _derive_trigger(content: str) -> str:
    """Best-effort fallback for an anti-pattern's `trigger`. The seed packs
    encode trigger/consequence/alternative as labelled paragraphs in the
    body; if a row is missing the explicit field we extract it.
    """
    return _extract_label(content, "Trigger") or content[:200]


def _derive_consequence(content: str) -> str:
    return _extract_label(content, "Consequence") or ""


def _derive_alternative(content: str) -> str:
    return _extract_label(content, "Alternative") or ""


def _extract_label(content: str, label: str) -> str:
    """Pull the paragraph following ``<label>:`` from ``content``."""
    needle = f"{label}:"
    idx = content.find(needle)
    if idx < 0:
        return ""
    rest = content[idx + len(needle):].lstrip()
    # Stop at the next labelled section.
    for stop in ("Trigger:", "Consequence:", "Alternative:"):
        j = rest.find("\n\n" + stop)
        if 0 <= j:
            rest = rest[:j]
            break
    return rest.strip()


# ── Verify (no install) ─────────────────────────────────────────────────────


@dataclass
class VerifyResult:
    name: str
    version: str
    signed: bool
    valid: bool
    reason: str
    memories: int
    counts: dict[str, int] = field(default_factory=dict)


def verify_file(source: str | Path) -> VerifyResult:
    """Read a ``.memee`` file and return signature + structural check."""
    raw = _resolve_source(source)
    bundle = pf.read_pack_from_bytes(raw)
    ok, reason = pf.verify_bundle(bundle)
    manifest = pf.parse_manifest(bundle.manifest_bytes)
    rows = list(pf.read_memories_jsonl(bundle.memories_bytes))
    counts = manifest.counts or {}
    return VerifyResult(
        name=manifest.name,
        version=manifest.version,
        signed=bundle.signed,
        valid=ok,
        reason=reason,
        memories=len(rows),
        counts=counts,
    )


# ── Helpers exposed for tests / introspection ───────────────────────────────


def _result_dict(r) -> dict:
    """Stable shape for InstallResult / ExportResult / VerifyResult."""
    d = asdict(r)
    if isinstance(d.get("out_path"), Path):
        d["out_path"] = str(d["out_path"])
    return d
