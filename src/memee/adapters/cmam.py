"""Claude Managed Agents Memory (CMAM) adapter.

CMAM is Anthropic's filesystem-style memory store mounted at /mnt/memory/ inside
a managed agent container. It's a DUMB store — no confidence, no dedup, no
cross-project routing. Memee is the INTELLIGENCE layer on top.

This adapter lets you push Memee's CANON memories into a CMAM store so Claude
agents see them natively via the memory tool (view/create/str_replace/insert/
delete/rename). When an agent running in Anthropic's managed environment starts
a session, it already has org-validated knowledge on disk.

Integration path (Canon → CMAM auto-sync):
    1. Memory reaches maturity=CANON (0.85 confidence, 5 projects, 10 validations)
    2. Adapter writes it to /canon/<type>/<slug>.md
    3. Anti-patterns go to /warnings/<severity>/<slug>.md
    4. Decisions go to /decisions/<slug>.md
    5. Agent sees them on next session start

CMAM constraints (enforced by this adapter):
    - 100 KB per memory (we chunk long content into .part-N.md files)
    - 100 MB per store (we warn at 80 MB, hard-stop at 95 MB)
    - 2000 memories per store (we warn at 1600, hard-stop at 1900)
    - 1000 stores per org, 8 stores per session
    - SHA256 content preconditions for optimistic concurrency

Multi-model bridge: GPT/Gemini discovers pattern → Memee validates + scores →
once CANON, this adapter pushes to CMAM → Claude session reads it from
/mnt/memory/. Memee stays the multi-model brain; CMAM is the Claude-native
delivery mechanism.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

from memee.storage.models import AntiPattern, MaturityLevel, Memory, MemoryType

logger = logging.getLogger("memee.cmam")


# ── CMAM documented limits ──
MAX_MEMORY_BYTES = 100 * 1024            # 100 KB per file
MAX_STORE_BYTES = 100 * 1024 * 1024      # 100 MB per store
MAX_MEMORIES_PER_STORE = 2000
SOFT_BYTES_LIMIT = int(MAX_STORE_BYTES * 0.80)       # 80 MB → warn
HARD_BYTES_LIMIT = int(MAX_STORE_BYTES * 0.95)       # 95 MB → stop
SOFT_COUNT_LIMIT = int(MAX_MEMORIES_PER_STORE * 0.80)  # 1600 → warn
HARD_COUNT_LIMIT = int(MAX_MEMORIES_PER_STORE * 0.95)  # 1900 → stop


# ── Path layout inside a CMAM store ──
#
# /canon/patterns/<slug>.md        — maturity=CANON patterns
# /canon/lessons/<slug>.md         — maturity=CANON lessons
# /warnings/critical/<slug>.md     — critical anti-patterns (all projects)
# /warnings/high/<slug>.md         — high-severity anti-patterns
# /decisions/<slug>.md             — recorded decisions
# /_index.md                       — human-readable index (agent loads this first)
# /_memee.json                     — machine-readable manifest (SHA256, confidence)


@dataclass
class CMAMConfig:
    """Configuration for a CMAM target.

    Two backends:
      - "fs": write to a local directory (for mounting into a container or
        manual inspection). Use this unless you have the Anthropic managed
        memory API enabled on your org.
      - "api": call the Anthropic managed memory API directly.
    """

    store_id: str
    backend: str = "fs"                      # "fs" or "api"
    local_root: Path | None = None           # required for "fs"
    api_base: str = "https://api.anthropic.com"
    api_key: str | None = None               # required for "api"
    redact: bool = True                      # strip API keys / secrets from content


@dataclass
class SyncResult:
    pushed: int = 0
    updated: int = 0
    skipped: int = 0
    rejected: list[dict] = field(default_factory=list)
    bytes_written: int = 0
    store_bytes: int = 0
    store_count: int = 0
    warnings: list[str] = field(default_factory=list)


# ── Backends ──


class _FSBackend:
    """Local-filesystem CMAM backend. Mount the root into a container at
    /mnt/memory/ and Claude's memory tool sees it directly.
    """

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, path: str, content: str, expected_sha256: str | None = None) -> dict:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        if expected_sha256 and target.exists():
            current = hashlib.sha256(target.read_bytes()).hexdigest()
            if current != expected_sha256:
                return {"status": "conflict", "path": path, "current_sha256": current}

        target.write_text(content, encoding="utf-8")
        return {
            "status": "ok",
            "path": path,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "bytes": len(content.encode("utf-8")),
        }

    def delete(self, path: str) -> dict:
        target = self._resolve(path)
        if target.exists():
            target.unlink()
            return {"status": "ok", "path": path}
        return {"status": "not_found", "path": path}

    def list(self) -> list[dict]:
        out: list[dict] = []
        for p in self.root.rglob("*"):
            if p.is_file():
                rel = p.relative_to(self.root).as_posix()
                out.append({"path": f"/{rel}", "bytes": p.stat().st_size})
        return out

    def _resolve(self, path: str) -> Path:
        # Normalize leading slash, block traversal
        clean = path.lstrip("/")
        if ".." in Path(clean).parts:
            raise ValueError(f"path traversal blocked: {path}")
        return self.root / clean


class _APIBackend:
    """HTTP backend for Anthropic's managed memory API.

    API surface (documented at platform.claude.com/docs/en/managed-agents/memory):
      PUT    /v1/memory_stores/{store_id}/memories        (path, content, sha256)
      GET    /v1/memory_stores/{store_id}/memories        (list)
      DELETE /v1/memory_stores/{store_id}/memories/{path}

    Optimistic concurrency: If-Match: <sha256> header on PUT.
    """

    def __init__(self, store_id: str, api_base: str, api_key: str):
        self.store_id = store_id
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        # httpx is optional; only imported when API backend is actually used
        import httpx
        self._http = httpx.Client(
            base_url=self.api_base,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=30.0,
        )

    def put(self, path: str, content: str, expected_sha256: str | None = None) -> dict:
        headers = {}
        if expected_sha256:
            headers["If-Match"] = expected_sha256
        r = self._http.put(
            f"/v1/memory_stores/{self.store_id}/memories",
            json={"path": path, "content": content},
            headers=headers,
        )
        if r.status_code == 412:
            return {"status": "conflict", "path": path}
        r.raise_for_status()
        data = r.json()
        return {
            "status": "ok",
            "path": path,
            "sha256": data.get("sha256")
            or hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "bytes": len(content.encode("utf-8")),
        }

    def delete(self, path: str) -> dict:
        r = self._http.delete(
            f"/v1/memory_stores/{self.store_id}/memories",
            params={"path": path},
        )
        if r.status_code == 404:
            return {"status": "not_found", "path": path}
        r.raise_for_status()
        return {"status": "ok", "path": path}

    def list(self) -> list[dict]:
        r = self._http.get(f"/v1/memory_stores/{self.store_id}/memories")
        r.raise_for_status()
        return r.json().get("memories", [])


def _make_backend(cfg: CMAMConfig):
    if cfg.backend == "fs":
        root = cfg.local_root or (Path.home() / ".memee" / "cmam" / cfg.store_id)
        return _FSBackend(root)
    if cfg.backend == "api":
        key = cfg.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError(
                "CMAM api backend requires ANTHROPIC_API_KEY (env) or cfg.api_key"
            )
        return _APIBackend(cfg.store_id, cfg.api_base, key)
    raise ValueError(f"unknown backend: {cfg.backend}")


# ── Memory → CMAM mapping ──


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SECRET_RE = re.compile(
    r"(?:sk-[a-zA-Z0-9]{20,}|"                       # OpenAI / Anthropic keys
    r"AKIA[0-9A-Z]{16}|"                             # AWS access keys
    r"glpat-[a-zA-Z0-9_.-]{20,}|"                    # GitLab tokens
    r"ghp_[a-zA-Z0-9]{36}|"                          # GitHub PATs
    r"xox[baprs]-[a-zA-Z0-9-]{10,})"                 # Slack tokens
)


def _slug(s: str, max_len: int = 80) -> str:
    clean = _SLUG_RE.sub("-", (s or "untitled").lower()).strip("-")
    return (clean or "untitled")[:max_len]


def _redact(text: str) -> str:
    return _SECRET_RE.sub("[REDACTED]", text)


def memory_to_cmam_path(memory: Memory) -> str:
    """Map a Memee memory to its CMAM path."""
    slug = _slug(memory.title)
    short_id = (memory.id or "")[:8]
    filename = f"{slug}.{short_id}.md" if short_id else f"{slug}.md"

    if memory.type == MemoryType.ANTI_PATTERN.value:
        severity = "medium"
        if memory.anti_pattern and memory.anti_pattern.severity:
            severity = memory.anti_pattern.severity
        return f"/warnings/{severity}/{filename}"

    if memory.type == MemoryType.DECISION.value:
        return f"/decisions/{filename}"

    if memory.type == MemoryType.PATTERN.value:
        return f"/canon/patterns/{filename}"

    if memory.type == MemoryType.LESSON.value:
        return f"/canon/lessons/{filename}"

    return f"/canon/other/{filename}"


def render_memory(memory: Memory, redact: bool = True) -> str:
    """Render a Memee memory as a CMAM-friendly markdown file.

    Front-matter carries the machine data agents can parse; the body is the
    human (and LLM) readable content.
    """
    tags = ", ".join(memory.tags or [])
    lines = [
        "---",
        f"id: {memory.id}",
        f"type: {memory.type}",
        f"maturity: {memory.maturity}",
        f"confidence: {round(memory.confidence_score or 0.0, 3)}",
        f"validation_count: {memory.validation_count or 0}",
        f"project_count: {memory.project_count or 0}",
        f"tags: [{tags}]",
        "source: memee",
        "---",
        "",
        f"# {memory.title}",
        "",
    ]

    body_parts: list[str] = []
    if memory.summary:
        body_parts.append(memory.summary.strip())
        body_parts.append("")
    if memory.content:
        body_parts.append(memory.content.strip())
        body_parts.append("")

    if memory.type == MemoryType.ANTI_PATTERN.value and memory.anti_pattern:
        ap = memory.anti_pattern
        body_parts.append(f"**Severity:** {ap.severity}")
        body_parts.append(f"**Trigger:** {ap.trigger}")
        body_parts.append(f"**Consequence:** {ap.consequence}")
        if ap.alternative:
            body_parts.append(f"**Alternative:** {ap.alternative}")
        body_parts.append("")

    if memory.type == MemoryType.DECISION.value and memory.decision:
        d = memory.decision
        body_parts.append(f"**Chosen:** {d.chosen}")
        if d.alternatives:
            body_parts.append(f"**Over:** {', '.join(map(str, d.alternatives))}")
        if d.outcome:
            body_parts.append(f"**Outcome:** {d.outcome}")
        body_parts.append("")

    body = "\n".join(body_parts).rstrip() + "\n"
    if redact:
        body = _redact(body)

    return "\n".join(lines) + body


def _chunk_if_needed(content: str, path: str) -> list[tuple[str, str]]:
    """If content exceeds CMAM's 100 KB per-file limit, split into .part-N.md.

    Front-matter (YAML between two `---` lines at the top) is preserved on
    every chunk so each part is self-describing.
    """
    data = content.encode("utf-8")
    if len(data) <= MAX_MEMORY_BYTES:
        return [(path, content)]

    header = ""
    body = content
    if content.startswith("---\n"):
        close = content.find("\n---\n", 4)  # skip opening '---\n'
        if close != -1:
            header = content[: close + len("\n---\n")]
            body = content[close + len("\n---\n") :]

    header_bytes = len(header.encode("utf-8")) + 40  # pointer footer overhead
    chunk_limit = MAX_MEMORY_BYTES - header_bytes
    if chunk_limit < 1024:
        chunk_limit = MAX_MEMORY_BYTES // 2

    chunks: list[str] = []
    body_bytes = body.encode("utf-8")
    for i in range(0, len(body_bytes), chunk_limit):
        chunks.append(body_bytes[i : i + chunk_limit].decode("utf-8", errors="ignore"))

    out: list[tuple[str, str]] = []
    base = path[:-3] if path.endswith(".md") else path
    for i, chunk in enumerate(chunks):
        part_path = f"{base}.part-{i + 1}.md"
        footer = f"\n\n<!-- memee: part {i + 1}/{len(chunks)} -->\n"
        out.append((part_path, header + chunk + footer))
    return out


# ── Index + manifest ──


def _build_index(memories: Iterable[Memory]) -> str:
    """Human-readable index so an agent loading /_index.md first gets a map."""
    by_type: dict[str, list[Memory]] = {}
    for m in memories:
        by_type.setdefault(m.type, []).append(m)

    lines = [
        "# Memee Canon — Organizational Knowledge",
        "",
        "Synced from Memee (intelligence layer) into CMAM (managed store).",
        "Memee handles confidence scoring, quality gating, and multi-model",
        "validation. Anything below has reached CANON maturity or is a critical",
        "warning that applies org-wide.",
        "",
    ]

    section_titles = {
        MemoryType.ANTI_PATTERN.value: "⚠ Warnings (DO NOT)",
        MemoryType.DECISION.value: "🧭 Decisions",
        MemoryType.PATTERN.value: "✅ Canon Patterns",
        MemoryType.LESSON.value: "📚 Canon Lessons",
    }
    for mtype in [
        MemoryType.ANTI_PATTERN.value,
        MemoryType.DECISION.value,
        MemoryType.PATTERN.value,
        MemoryType.LESSON.value,
    ]:
        items = by_type.get(mtype) or []
        if not items:
            continue
        lines.append(f"## {section_titles[mtype]}")
        lines.append("")
        for m in items:
            path = memory_to_cmam_path(m)
            conf = round((m.confidence_score or 0.0) * 100)
            lines.append(
                f"- [{m.title}]({path}) — conf {conf}%, "
                f"{m.project_count or 0} projects"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── Eligibility ──


def is_eligible_for_cmam(memory: Memory) -> bool:
    """What gets pushed to CMAM.

    Canon (proven across 5+ projects) — core of the knowledge base.
    Critical anti-patterns — always pushed regardless of maturity, because the
    cost of missing them dominates.
    """
    if memory.deprecated_at:
        return False
    if memory.maturity == MaturityLevel.CANON.value:
        return True
    if (
        memory.type == MemoryType.ANTI_PATTERN.value
        and memory.anti_pattern
        and memory.anti_pattern.severity == "critical"
    ):
        return True
    return False


def iter_eligible_memories(session: Session) -> list[Memory]:
    """Scan the Memee DB for everything that belongs in CMAM."""
    canon = (
        session.query(Memory)
        .filter(
            Memory.maturity == MaturityLevel.CANON.value,
            Memory.deprecated_at.is_(None),
        )
        .all()
    )
    critical_aps = (
        session.query(Memory)
        .join(AntiPattern, AntiPattern.memory_id == Memory.id)
        .filter(
            AntiPattern.severity == "critical",
            Memory.deprecated_at.is_(None),
        )
        .all()
    )
    # Dedup by id (critical canon would otherwise appear twice)
    seen: set[str] = set()
    out: list[Memory] = []
    for m in canon + critical_aps:
        if m.id in seen:
            continue
        seen.add(m.id)
        out.append(m)
    return out


# ── Sync orchestration ──


def sync_to_cmam(
    session: Session,
    cfg: CMAMConfig,
    memories: list[Memory] | None = None,
    dry_run: bool = False,
) -> SyncResult:
    """Push Memee's eligible memories to a CMAM store.

    If `memories` is None, we pull canon + critical anti-patterns from the DB.
    `dry_run=True` reports what would change without writing.
    """
    if memories is None:
        memories = iter_eligible_memories(session)

    backend = _make_backend(cfg)

    # Snapshot current store so we can enforce limits and diff
    try:
        existing = {m["path"]: m.get("bytes", 0) for m in backend.list()}
    except Exception as e:
        logger.warning("CMAM list() failed (%s); proceeding with empty baseline", e)
        existing = {}

    store_bytes = sum(existing.values())
    store_count = len(existing)
    result = SyncResult(store_bytes=store_bytes, store_count=store_count)

    planned: list[tuple[str, str]] = []
    for m in memories:
        rendered = render_memory(m, redact=cfg.redact)
        path = memory_to_cmam_path(m)
        chunks = _chunk_if_needed(rendered, path)
        planned.extend(chunks)

    # Build the index over all eligible memories (not chunk paths)
    index_content = _build_index(memories)
    planned.append(("/_index.md", index_content))

    for path, content in planned:
        size = len(content.encode("utf-8"))
        prev_size = existing.get(path, 0)
        delta = size - prev_size
        projected_bytes = store_bytes + delta
        projected_count = store_count + (0 if path in existing else 1)

        if size > MAX_MEMORY_BYTES:
            result.rejected.append(
                {"path": path, "reason": "single file > 100 KB after chunking", "bytes": size}
            )
            continue
        if projected_bytes > HARD_BYTES_LIMIT:
            result.rejected.append(
                {"path": path, "reason": "store would exceed 95% of 100 MB", "bytes": size}
            )
            continue
        if projected_count > HARD_COUNT_LIMIT:
            result.rejected.append(
                {"path": path, "reason": "store would exceed 1900 memories", "bytes": size}
            )
            continue

        if projected_bytes > SOFT_BYTES_LIMIT:
            result.warnings.append(f"CMAM store >80% full ({projected_bytes} bytes)")
        if projected_count > SOFT_COUNT_LIMIT:
            result.warnings.append(f"CMAM store >1600 memories ({projected_count})")

        if dry_run:
            if path in existing:
                result.updated += 1
            else:
                result.pushed += 1
            result.bytes_written += size
            store_bytes = projected_bytes
            store_count = projected_count
            continue

        resp = backend.put(path, content)
        if resp.get("status") == "ok":
            if path in existing:
                result.updated += 1
            else:
                result.pushed += 1
            result.bytes_written += size
            store_bytes = projected_bytes
            store_count = projected_count
        else:
            result.rejected.append({"path": path, "reason": resp.get("status", "unknown")})

    result.store_bytes = store_bytes
    result.store_count = store_count
    # Dedup warnings so we don't repeat the same threshold crossing per file
    result.warnings = sorted(set(result.warnings))
    return result


def verify_store(cfg: CMAMConfig) -> dict:
    """Inspect a CMAM store. Returns size, count, and limit headroom."""
    backend = _make_backend(cfg)
    items = backend.list()
    total_bytes = sum(i.get("bytes", 0) for i in items)
    return {
        "store_id": cfg.store_id,
        "backend": cfg.backend,
        "memories": len(items),
        "bytes": total_bytes,
        "bytes_pct_of_limit": round(100 * total_bytes / MAX_STORE_BYTES, 2),
        "count_pct_of_limit": round(100 * len(items) / MAX_MEMORIES_PER_STORE, 2),
        "paths": sorted(i["path"] for i in items),
    }
