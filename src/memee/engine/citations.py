"""Citation tokens: short-hash references to canon memories.

Two-way contract:
  * The briefing footer instructs the agent to cite memories with
    ``[mem:abc12345]`` (first 8 hex chars of the memory UUID).
  * ``memee why`` and ``memee cite`` round-trip those tokens back to the
    underlying memory + lineage so a screenshot of an agent reply has a
    one-command audit path.

Memory IDs in this codebase are full UUID4 strings (see ``Memory.new_id``).
The canonical citation hash is the first 8 hex characters of that UUID,
which gives ~16M-deep namespace collisions — fine for org-scale stores;
the resolver disambiguates on collision and refuses the citation rather
than guessing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from memee.storage.models import (
    Memory,
    MemoryValidation,
    Project,
)


# Length of the short hash in `[mem:<hash>]` tokens. The full UUID is
# always accepted too — we just lookup by prefix.
SHORT_HASH_LEN = 8


def short_hash(memory_id: str) -> str:
    """Return the canonical short hash for a memory id."""
    if not memory_id:
        return ""
    # Full UUIDs may include dashes; take the first 8 *hex* chars by
    # stripping dashes. This keeps `short_hash(uuid)` stable regardless
    # of whether the caller already trimmed dashes.
    raw = memory_id.replace("-", "")
    return raw[:SHORT_HASH_LEN]


def cite_token(memory_id: str) -> str:
    """Render a citation token for the given memory id."""
    return f"[mem:{short_hash(memory_id)}]"


def resolve(session: Session, hash_or_id: str) -> Memory | None:
    """Look up a memory by short hash, dashed prefix, or full UUID.

    Returns ``None`` when nothing matches OR when more than one memory
    shares the prefix (caller must surface a clean ambiguity error —
    silently picking one would let a citation drift to a different
    memory after a future insert).
    """
    if not hash_or_id:
        return None

    raw = hash_or_id.strip()
    # Tolerate `[mem:abc12345]` or bare hashes alike — strip the wrapper.
    if raw.startswith("[mem:") and raw.endswith("]"):
        raw = raw[len("[mem:") : -1]

    # Try direct lookup first (full UUID hits the PK index).
    direct = session.get(Memory, raw)
    if direct is not None:
        return direct

    # Prefix match — UUID hex has dashes, so we LIKE on the first chars
    # of `id`. Note: SQLAlchemy `like` is case-sensitive on SQLite by
    # default; UUID4s are lowercase so this matches the on-disk shape.
    candidates = (
        session.query(Memory).filter(Memory.id.like(f"{raw}%")).limit(2).all()
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # Ambiguous prefix — refuse to guess.
        return None

    # Last resort: the user pasted a hash that doesn't dovetail with the
    # dashed UUID layout. Strip dashes from candidate ids and compare on
    # the joined hex. This is O(n) but the only way to honour an 8-char
    # hash that crosses a dash boundary in the underlying UUID.
    if len(raw) <= SHORT_HASH_LEN:
        scan = (
            session.query(Memory)
            .filter(Memory.id.like(f"{raw[:1]}%"))  # cheap shard
            .all()
        )
        matches = [m for m in scan if short_hash(m.id).startswith(raw)]
        if len(matches) == 1:
            return matches[0]
    return None


def lineage(session: Session, memory: Memory) -> list[dict]:
    """Build a chronologically ordered lineage trail for a memory.

    Sources, in order of authority:
      1. ``created_at`` — the recording event itself, attributed to the
         ``source_agent`` / ``source_model`` if known.
      2. ``MemoryValidation`` rows — each validation, with the validator
         model and project (if any).
      3. ``evidence_chain`` JSON — free-form events stamped by other
         engines (dedup_merge, citation, etc.).
      4. Maturity promotion — synthesized from the current ``maturity``
         when ``last_validated_at`` is present.

    Returned shape: ``[{"ts": iso, "kind": str, "note": str}, ...]``
    """
    out: list[dict] = []

    # 1. Recording.
    if memory.created_at:
        agent = memory.source_agent or "unknown agent"
        model = memory.source_model or memory.source_session or ""
        proj_name = ""
        if memory.projects:
            # ProjectMemory rows; pick first registered project for the note.
            for pm in memory.projects:
                if pm.project and pm.project.name:
                    proj_name = pm.project.name
                    break
        note = f"recorded by {agent}"
        if model:
            note += f" ({model})"
        if proj_name:
            note += f" in project {proj_name}"
        out.append(
            {
                "ts": _iso(memory.created_at),
                "kind": "recorded",
                "note": note,
            }
        )

    # 2. Validations — one row per validation event.
    validations = (
        session.query(MemoryValidation)
        .filter(MemoryValidation.memory_id == memory.id)
        .order_by(MemoryValidation.created_at.asc())
        .all()
    )
    for v in validations:
        verb = "validated" if v.validated else "invalidated"
        validator = v.validator_model or "unknown model"
        proj_part = ""
        if v.project_id:
            proj = session.get(Project, v.project_id)
            if proj and proj.name:
                proj_part = f" in {proj.name}"
        note = f"{verb} by {validator}{proj_part}"
        if v.evidence:
            note += f" — {v.evidence[:80]}"
        out.append(
            {
                "ts": _iso(v.created_at),
                "kind": verb,
                "note": note,
            }
        )

    # 3. Free-form evidence chain entries.
    for e in memory.evidence_chain or []:
        ts = e.get("ts") or ""
        kind = e.get("kind") or e.get("type") or "evidence"
        note = e.get("note") or ""
        # dedup_merge entries from quality_gate stash details under specific
        # keys; render them readably.
        if kind == "dedup_merge":
            from_title = e.get("from_title", "")
            sim = e.get("similarity")
            note = f"merged duplicate '{from_title}'"
            if sim is not None:
                note += f" (similarity {sim})"
        out.append({"ts": ts, "kind": kind, "note": note})

    # 4. Maturity promotion (synthesized).
    if memory.last_validated_at and memory.maturity in {"validated", "canon"}:
        out.append(
            {
                "ts": _iso(memory.last_validated_at),
                "kind": "promoted",
                "note": (
                    f"promoted to {memory.maturity} "
                    f"({memory.project_count or 0} projects, "
                    f"{memory.validation_count or 0} validations)"
                ),
            }
        )

    out.sort(key=lambda e: e.get("ts") or "")
    return out


def confirm_citation(
    session: Session,
    memory: Memory,
    note: str = "",
) -> dict:
    """Manually confirm an agent applied this memory.

    Bumps ``application_count`` once and appends a ``citation`` entry to
    the evidence chain. Returns the new shape so the CLI can echo it.
    """
    memory.application_count = (memory.application_count or 0) + 1
    memory.last_applied_at = datetime.now(timezone.utc)
    chain = list(memory.evidence_chain or [])
    chain.append(
        {
            "kind": "citation",
            "ts": datetime.now(timezone.utc).isoformat(),
            "note": note or "manual confirm via `memee cite --confirm`",
        }
    )
    memory.evidence_chain = chain
    session.commit()
    return {
        "memory_id": memory.id,
        "application_count": memory.application_count,
        "evidence_entries": len(chain),
    }


def explain(
    session: Session,
    snippet: str,
    limit: int = 3,
) -> list[dict]:
    """Find canon entries that would have prevented or explained ``snippet``.

    Pipeline mirrors ``memee why`` — runs the existing review keyword
    extractor + hybrid search across anti-patterns and lessons, then
    deduplicates by memory id.

    Code snippets like ``eval(user_input)`` contain punctuation FTS5
    chokes on (parentheses, dots, brackets). We extract identifier
    tokens and re-join them with spaces before passing to the search
    layer — this preserves the keywords the user cares about while
    keeping the FTS5 query syntactically valid.
    """
    import re

    from memee.engine.review import _extract_keywords
    from memee.engine.search import search_memories

    snippet = (snippet or "").strip()
    if not snippet:
        return []

    # Treat the input as if it were the "added lines" of a diff. Most agent
    # snippets are 1-N lines so this works for both code and free-form
    # questions ("we used eval to parse user math, why was that bad?").
    lines = [ln.strip() for ln in snippet.splitlines() if ln.strip()]
    if not lines:
        lines = [snippet]
    keywords = _extract_keywords(lines)

    # Sanitise the query for FTS5: extract identifier-like tokens and the
    # detected keyword set. ``eval(user_input)`` becomes "eval user input
    # security" — the right hits surface and FTS5 doesn't trip on the
    # parenthesis. Empty fallback keeps the function honest if someone
    # passes pure punctuation.
    raw_tokens = re.findall(r"[A-Za-z_][A-Za-z_0-9]{0,30}", snippet)
    # Split snake_case identifiers so `user_input` matches `user input`.
    expanded: list[str] = []
    for t in raw_tokens:
        expanded.append(t)
        if "_" in t:
            expanded.extend(p for p in t.split("_") if p)
    query = " ".join(dict.fromkeys(expanded + keywords)) or snippet

    pool: dict[str, dict] = {}
    # Anti-pattern hits first (warnings outrank lessons in the demo block).
    ap_hits = search_memories(
        session,
        query,
        tags=keywords or None,
        memory_type="anti_pattern",
        limit=limit * 3,
        use_vectors=False,
    )
    for r in ap_hits:
        m = r["memory"]
        if m.id in pool:
            continue
        pool[m.id] = {"memory": m, "score": r.get("total_score", 0.0)}

    lesson_hits = search_memories(
        session,
        query,
        tags=keywords or None,
        memory_type="lesson",
        limit=limit * 3,
        use_vectors=False,
    )
    for r in lesson_hits:
        m = r["memory"]
        if m.id in pool:
            continue
        pool[m.id] = {"memory": m, "score": r.get("total_score", 0.0)}

    ranked = sorted(pool.values(), key=lambda h: -h["score"])
    return ranked[:limit]


def _iso(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(dt)


# Footer text for compact briefings — kept short by spec (≤200 tokens).
# This is the single source of truth so tests can import it directly.
CITATION_FOOTER = (
    "---\n"
    "Cite Memee canon you apply with [mem:<8-char-id>]. Any memory in "
    "this briefing is fair game. Run `memee cite <id>` to inspect "
    "lineage. Memee counts a citation as a soft validation; an "
    "uncontested cite within 24h becomes evidence."
)


def get_citation_footer() -> str:
    """Return the canonical citation footer string."""
    return CITATION_FOOTER
