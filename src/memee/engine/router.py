"""Smart Knowledge Router v2: search-based, not domain-based.

v1 used hardcoded domains: task → domain → tags → filter (fragile, limited)
v2 uses existing hybrid search: task → search_memories() → done (robust, unlimited)

Why this works:
  - BM25 catches exact keywords ("timeout" in title)
  - Vector catches semantics ("request deadline" finds "timeout" pattern)
  - Tags are bonus signal, not gate (no fragmentation risk)
  - Scales with org: more memories = better matches
  - Zero configuration: no domain definitions needed

Architecture:
  Layer 0: Critical DNA (~100 tokens) — CRITICAL anti-patterns, always
  Layer 1: Search-routed (~300 tokens) — hybrid search on task + stack context
  Footer: Token count + search hint (~50 tokens)
  Total: ≤500 tokens
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryType,
    Project,
)


# R14: Maturity-gated expansion. The R10 expansion gate skipped expansion
# when the DB had no embeddings (BM25-only DBs paid -0.0265 nDCG@10 on
# the 55-q harness because the extra terms diluted lexical precision).
# Audit roadmap Item ``Maturity-gated query expansion`` adds a second
# gate: if the raw query *already* matches a CANON/VALIDATED pattern
# strongly, expansion will only dilute the win — skip it.
#
# Probe: one ``MATCH`` against ``memories_fts`` joined to ``memories``
# (filter on type=pattern AND maturity ∈ {canon, validated}) returning
# the top 3 ranks. Cost: ~1-2 ms warm; cheaper than running the
# expansion path it bypasses.
#
# Tunables (env):
#   MEMEE_MATURITY_GATED_EXPANSION ∈ {1,0}         default 0 (opt-in)
#   MEMEE_MATURITY_GATE_THRESHOLD  ∈ [0.0, 1.0]    default 0.7
#
# Default-OFF policy: the R14 A/B harness on the 207-query bench
# (``tests/r14_maturity_gated_expansion_eval.py``) measured ΔnDCG@10 =
# 0.0000 at p=1.0000 for both 0.7 and 0.85 thresholds — the gate fires
# correctly but rarely intersects with the small subset of queries
# where ``_expand_query`` would have changed the result, so the
# measured impact on this bench is null. Ship rule said: "if neither
# macro nor cluster gain crosses p<0.10, ship behind opt-in." So the
# code is committed, the env knob is exposed, and operators in
# vector-aware deployments who see different traffic mixes can flip
# it on with a one-liner. Re-enabling will be a one-line PR if a
# future bench shows the gain.
try:
    MATURITY_GATE_THRESHOLD = float(
        os.environ.get("MEMEE_MATURITY_GATE_THRESHOLD", "0.7")
    )
except ValueError:
    MATURITY_GATE_THRESHOLD = 0.7


def _maturity_gate_enabled() -> bool:
    """Default-OFF opt-in flag — see module-level note for the rationale.

    Accepts ``1``/``true``/``on``/``yes`` to enable; everything else (or
    unset) leaves the gate disabled.
    """
    raw = os.environ.get("MEMEE_MATURITY_GATED_EXPANSION", "0").strip().lower()
    return raw in ("1", "true", "on", "yes")


def _layer0_suppressed() -> bool:
    """True iff the always-on critical-AP block (Layer 0) must be silent.

    Symmetric with ``get_citation_footer()`` in ``citations.py``: the
    operator can silence the surface without uninstalling Memee.

      * ``MEMEE_QUIET=1``    — master cross-context shield (v2.2.1).
      * ``MEMEE_NO_LAYER0=1`` — per-channel switch added in v2.2.3,
                                introduced because Layer 0 is the only
                                briefing block that fires uncondition-
                                ally regardless of search results.

    Any non-empty value of either env var counts as set, matching the
    convention used by ``MEMEE_NO_FOOTER`` / ``MEMEE_NO_DIGEST`` etc.
    """
    return bool(
        os.environ.get("MEMEE_QUIET") or os.environ.get("MEMEE_NO_LAYER0")
    )


def _layer05_suppressed() -> bool:
    """True iff the authoritative-pinned block (Layer 0.5) must be silent.

    New in v2.3.1. Symmetric with ``_layer0_suppressed()`` so an operator
    can silence the pinned surface independently of the critical-AP
    block:

      * ``MEMEE_QUIET=1``     — master cross-context shield.
      * ``MEMEE_NO_PINNED=1`` — per-channel switch for Layer 0.5.

    Rationale: a user pinning a sensitive org policy (e.g. "PII never to
    logs") may want it visible across all sessions, while another user
    finds the policy redundant for personal projects. Per-channel kill
    switches preserve agency.
    """
    return bool(
        os.environ.get("MEMEE_QUIET") or os.environ.get("MEMEE_NO_PINNED")
    )

# Approximate tokens per memory line (legacy sentinel — kept for backward-compat
# in tests that imported the symbol; real accounting uses _count_tokens below).
TOKENS_PER_LINE = 15


def _count_tokens(text: str) -> int:
    """Conservative token estimator: ~4 chars per token (Claude rule of thumb).

    Counts every character in the joined text — headers, blank lines, prefixes,
    severity badges, footer — nothing is free. Anchors the budget to reality
    instead of a flat per-line guess that drifted 4-6x low.
    """
    if not text:
        return 0
    return len(text) // 4


def _evidence_prefix(memory) -> str:
    """Verifiable provenance tag for a briefing bullet.

    Renders e.g. ``[mem:a1b2c3d4 · canon 91%]`` — an 8-char citation
    handle (resolvable with ``memee cite``) plus maturity + confidence.
    A concrete resolved handle, never a fill-in template, so it reads as
    *attributed canon state* rather than an unsigned imperative —
    the signal Anthropic-trained models use to separate organizational
    memory from prompt injection (OWASP LLM01).

    Crucially this is attribution, NOT instruction: it labels Memee's own
    rows and never asks the model to emit a token. That is the distinction
    the v2.2.1 footer rewrite missed — the old footer *demanded* the agent
    produce ``[mem:xxx]`` tokens (an imperative toward the model = injection
    signal), whereas a self-applied provenance prefix only describes where
    a fact came from. Returns ``""`` when the row has no id (defensive;
    callers fall back to the bare title).
    """
    from memee.engine.citations import short_hash

    h = short_hash(getattr(memory, "id", "") or "")
    if not h:
        return ""
    conf = getattr(memory, "confidence_score", None) or 0.0
    mat = getattr(memory, "maturity", None) or "?"
    return f"[mem:{h} · {mat} {conf:.0%}]"


def _bullet(glyph: str, memory, title: str | None = None) -> str:
    """Render a briefing bullet with its evidence prefix.

    ``glyph`` is the layer marker (``•`` critical, ``→`` pinned, ``?``
    re-check, ``✓`` pattern, ``[SEV]`` warning). The prefix sits between
    glyph and title so that even after the compact path strips section
    headers, every surviving line still carries its own provenance.
    """
    prefix = _evidence_prefix(memory)
    text = title if title is not None else memory.title
    if prefix:
        return f"  {glyph} {prefix} {text}"
    return f"  {glyph} {text}"


# Stack exclusion: filter out memories from completely unrelated stacks
_UNRELATED_TAGS = {
    "python": {"react", "swift", "swiftui", "kotlin", "angular", "vue",
               "hooks", "jsx", "ios", "android", "jetpack"},
    "react": {"swift", "swiftui", "kotlin", "django", "flask",
              "ios", "android", "jetpack", "coredata"},
    "swift": {"react", "typescript", "django", "flask", "fastapi",
              "kotlin", "hooks", "jsx", "android", "jetpack"},
    "kotlin": {"react", "swift", "swiftui", "django", "flask",
               "fastapi", "hooks", "jsx", "ios", "coredata"},
    "go": {"react", "swift", "kotlin", "django", "flask",
            "hooks", "jsx", "ios", "android"},
}


def smart_briefing(
    session: Session,
    project_path: str | None = None,
    task: str = "",
    token_budget: int = 500,
) -> str:
    """Generate a token-budgeted briefing using hybrid search.

    No hardcoded domains. Just searches organizational memory
    for whatever is relevant to the task + project stack.

    Layer 0: Critical anti-patterns (always, ~100 tokens)
    Layer 1: Search results for task (hybrid BM25+vector, ~300 tokens)
    """
    lines: list[str] = []

    def current_tokens() -> int:
        return _count_tokens("\n".join(lines))

    def would_fit(candidate: str, budget: int) -> bool:
        projected = _count_tokens("\n".join(lines + [candidate]))
        return projected <= budget

    # Reserve tail budget for footer (2 lines: stats + token counter).
    # Footer lines are ~50 chars each → ~25 tokens total. Round up a bit.
    FOOTER_RESERVE = 40

    # Find project context
    project = _find_project(session, project_path)
    stack_tags = _get_stack_tags(project)
    exclude_tags = _get_exclude_tags(stack_tags)

    # ── Layer 0: Critical anti-patterns in scope ──
    #
    # v2.2.3 hardening: the previous header ("CRITICAL (always):") and the
    # ``⚠`` glyph mimicked Claude Code's own system-warning surfaces and
    # framed the block as instruction rather than state. Combined with
    # imperative seed-pack titles ("Never X") the rendered output read as
    # an unsigned directive — exactly the pattern v2.2.1 rewrote out of
    # the citation footer. Header is now a declarative section label,
    # bullets use a neutral glyph, and the block honours ``MEMEE_QUIET``
    # / new ``MEMEE_NO_LAYER0`` kill switches so an operator can silence
    # this surface without uninstalling Memee. Seed-pack titles were
    # rewritten in tandem so the bullet text itself is declarative.
    critical_aps = (
        [] if _layer0_suppressed()
        else (
            session.query(Memory, AntiPattern)
            .join(AntiPattern, AntiPattern.memory_id == Memory.id)
            .filter(
                AntiPattern.severity == "critical",
                Memory.maturity != MaturityLevel.DEPRECATED.value,
            )
            .order_by(Memory.confidence_score.desc())
            .limit(3)
            .all()
        )
    )

    if critical_aps:
        header = "Critical anti-patterns in scope:"
        if would_fit(header, token_budget - FOOTER_RESERVE):
            lines.append(header)
            for m, _ap in critical_aps:
                candidate = _bullet("•", m)
                # Layer-0 cap: keep critical block ≤ ~100 tokens of content,
                # but still respect overall budget first.
                if current_tokens() + _count_tokens("\n" + candidate) > min(
                    token_budget - FOOTER_RESERVE,
                    _count_tokens("\n".join(lines)) + 100,
                ):
                    break
                if not would_fit(candidate, token_budget - FOOTER_RESERVE):
                    break
                lines.append(candidate)
            lines.append("")

    # ── Layer 0.5: Authoritative ("pinned") memories ──
    #
    # v2.3.1. Acts on Mem0 issue #4926 and Letta issue #3116: org
    # policies, personas, and hard constraints get out-ranked by
    # conversational similarity hits, so the agent never sees them.
    # Layer 0.5 surfaces every ``is_authoritative=True`` memory whose
    # tags overlap the task or project stack — regardless of
    # cosine/BM25 score. Bypasses Layer 1's filter chain entirely.
    #
    # Tag-overlap gate: at least one tag in common with either the task
    # tokens or the project stack. Falls back to "always show" when no
    # task/stack tags are available — a pinned policy with no scope is
    # explicitly global. The maturity gate excludes DEPRECATED so a
    # superseded policy doesn't keep firing.
    #
    # Budget: shares the global ``token_budget - FOOTER_RESERVE`` with
    # everything else. Hard sub-cap of ~120 tokens (~5 pinned bullets)
    # so a user who pins 50 policies doesn't starve the search-routed
    # block. Caller can tune via ``MEMEE_PINNED_MAX_BULLETS``.
    pinned_ids: set[str] = set()
    if not _layer05_suppressed():
        try:
            max_pinned = max(
                1, int(os.environ.get("MEMEE_PINNED_MAX_BULLETS", "5"))
            )
        except ValueError:
            max_pinned = 5

        # v2.3.3 perf fix: push tag-overlap into SQL via the
        # normalised ``memory_tags`` index instead of loading every
        # authoritative row and filtering in Python. At 10 k
        # authoritative memories the pre-v2.3.3 Python loop measured
        # ~500 ms; the SQL path serves the same set in <10 ms.
        #
        # Scope rules preserved exactly:
        #   • no scope_tags (no task + no stack) → load top-N pinned
        #   • mem_tags empty (declared global policy) → always include
        #   • both non-empty → require ≥1 tag in common
        # The query is split into "tagged" (overlap) + "global" (empty
        # tags) so we can express each branch as a single SQL.
        from memee.storage.models import MemoryTag

        task_tokens = {
            tok.lower()
            for tok in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", task or "")
        } if task else set()
        scope_tags = {t.lower() for t in stack_tags} | task_tokens

        matching: list[Memory] = []
        base_filter = (
            Memory.is_authoritative.is_(True),
            Memory.maturity != MaturityLevel.DEPRECATED.value,
        )

        if not scope_tags:
            # No scope signal — surface the top-N pinned by confidence.
            matching = (
                session.query(Memory)
                .filter(*base_filter)
                .order_by(Memory.confidence_score.desc())
                .limit(max_pinned)
                .all()
            )
        else:
            # Overlap branch: pinned rows whose normalised tag-index
            # contains at least one scope tag. ``DISTINCT`` because a
            # row can match on multiple tags.
            overlap_rows = (
                session.query(Memory)
                .join(MemoryTag, MemoryTag.memory_id == Memory.id)
                .filter(
                    *base_filter,
                    MemoryTag.tag.in_(scope_tags),
                )
                .distinct()
                .order_by(Memory.confidence_score.desc())
                .limit(max_pinned)
                .all()
            )
            seen = {m.id for m in overlap_rows}
            # Global branch: pinned rows with NO tags at all. Cheap
            # because we LEFT JOIN and keep only the NULL side.
            remaining = max_pinned - len(overlap_rows)
            global_rows: list[Memory] = []
            if remaining > 0:
                from sqlalchemy import not_, exists
                global_rows = (
                    session.query(Memory)
                    .filter(
                        *base_filter,
                        not_(
                            exists().where(MemoryTag.memory_id == Memory.id)
                        ),
                    )
                    .order_by(Memory.confidence_score.desc())
                    .limit(remaining)
                    .all()
                )
            # Concatenate, preserving confidence order via merge.
            merged = list(overlap_rows) + [
                m for m in global_rows if m.id not in seen
            ]
            merged.sort(key=lambda m: (m.confidence_score or 0.0), reverse=True)
            matching = merged[:max_pinned]

        if matching:
            header = "Pinned policies in scope:"
            if would_fit(header, token_budget - FOOTER_RESERVE):
                lines.append(header)
                pinned_start_tokens = _count_tokens("\n".join(lines))
                for m in matching:
                    candidate = _bullet("→", m)
                    # Sub-cap: ≤ ~120 tokens of pinned content. Same
                    # shape as Layer 0's cap, just sized for the longer
                    # titles policies tend to have.
                    if current_tokens() + _count_tokens("\n" + candidate) > min(
                        token_budget - FOOTER_RESERVE,
                        pinned_start_tokens + 120,
                    ):
                        break
                    if not would_fit(candidate, token_budget - FOOTER_RESERVE):
                        break
                    lines.append(candidate)
                    pinned_ids.add(m.id)
                lines.append("")

    # ── Layer 0.7: Re-checking canon (v2.4.1 — Tier 1.6) ──
    #
    # The cognitive-science dossier (docs/memee-2026-roadmap.md
    # Tier 1.6) flagged: canon as currently implemented is
    # *unfalsifiable*. A pattern that hit canon two years ago and is
    # silently stale only ever deprecates if somebody manually
    # invalidates it — survivorship bias. Spaced-repetition
    # literature (Wozniak SM-2, FSRS / Ye et al. KDD 2022) solved
    # the analogous problem for human memory: actively schedule
    # re-tests of items with uncertain stability and let the test
    # outcome update the model.
    #
    # The Beta-Binomial posterior (v2.4.0) gives us the right signal
    # for free — wide HDI on a canon claim means "we say it's solid
    # but we have surprisingly little evidence for that". Combined
    # with staleness (last_applied_at age), this picks the top N
    # canon memories most in need of re-validation and surfaces them
    # under a declarative "Re-checking canon:" header.
    #
    # Feedback closes naturally: agent applying the candidate (via
    # `MemoryUsage` / `SearchEvent.accepted_memory_id`) already
    # routes to ``update_confidence`` which bumps α; explicit
    # invalidate routes to β. Skipping the candidate leaves the
    # row's score unchanged so the next select_verify_candidates
    # picks it up again — eventually surfacing the staleness to the
    # operator if nothing happens.
    verify_ids: set[str] = set()
    try:
        from memee.engine.repetition import (
            select_verify_candidates,
            verify_limit_from_env,
        )

        verify_rows = select_verify_candidates(
            session, limit=verify_limit_from_env()
        )
        # De-dup against earlier layers (v2.4.6 fix):
        #   * Layer 0 critical anti-patterns — surfaced under the
        #     "⚠"/"•" glyph already; re-surfacing them as "? verify
        #     this" was the most visible duplication bug a real
        #     install demonstrated.
        #   * Layer 0.5 pinned policies — same logic, different glyph.
        critical_ids = {m.id for m, _ in critical_aps} if critical_aps else set()
        verify_rows = [
            m for m in verify_rows
            if m.id not in pinned_ids and m.id not in critical_ids
        ]

        if verify_rows:
            header = "Re-checking canon:"
            if would_fit(header, token_budget - FOOTER_RESERVE):
                lines.append(header)
                verify_start_tokens = _count_tokens("\n".join(lines))
                for m in verify_rows:
                    candidate = _bullet("?", m)
                    if current_tokens() + _count_tokens("\n" + candidate) > min(
                        token_budget - FOOTER_RESERVE,
                        verify_start_tokens + 120,
                    ):
                        break
                    if not would_fit(candidate, token_budget - FOOTER_RESERVE):
                        break
                    lines.append(candidate)
                    verify_ids.add(m.id)
                lines.append("")
    except Exception:
        # Best-effort: a broken verify pass never breaks the briefing.
        # The Beta-Binomial posterior or the staleness query could
        # fail on partial schema (mid-migration); fall through to
        # Layer 1 with no Layer 0.7 block.
        verify_ids = set()

    # ── Layer 1: Search-routed briefing ──
    # R10 accuracy fix: ``_build_search_query`` expands the task with related
    # tokens to broaden recall — sized for vector retrieval. On BM25-only DBs
    # the expansion *dilutes* the signal (measured ΔnDCG@10 = -0.0265 on the
    # 55-query harness, p=0.035). When no embedded memories exist, skip the
    # expansion and search the raw task instead. Vector-aware DBs keep the
    # expansion, where the semantic retriever covers the recall gap.
    from memee.engine.search import _db_has_any_embeddings, search_memories

    has_vectors = _db_has_any_embeddings(session)
    # On vector-aware DBs we may still call expansion, but only after the
    # R14 maturity gate has had a chance to short-circuit. Passing the
    # session lets ``_build_search_query`` run the cheap canon probe.
    search_query = (
        _build_search_query(task, stack_tags, session=session)
        if has_vectors
        else (task or (" ".join(list(stack_tags)[:4]) if stack_tags else ""))
    )

    if search_query:
        # Use our existing hybrid search (BM25 + vector + tags)
        # Search with task as primary query, stack tags as boost
        results = search_memories(
            session, search_query,
            tags=list(stack_tags) if stack_tags else None,
            limit=10,
            use_vectors=True,
        )

        # Filter: exclude unrelated TECH stacks (not business/marketing content)
        shown_ids = {m.id for m, _ in critical_aps} if critical_aps else set()
        # v2.3.1: also de-dup Layer 0.5 pinned memories so the same
        # policy doesn't appear twice when search would also surface it.
        shown_ids |= pinned_ids
        # v2.4.1: also de-dup Layer 0.7 verify candidates so the same
        # canon row doesn't appear twice when search would also surface
        # it (verify block already named it; search hit is redundant).
        shown_ids |= verify_ids
        tech_exclude = exclude_tags or set()
        filtered = []
        for r in results:
            m = r["memory"]
            if m.id in shown_ids:
                continue
            if m.maturity == MaturityLevel.DEPRECATED.value:
                continue
            mem_tags = set(m.tags or [])
            # Only exclude if memory is PURELY tech from wrong stack
            # Don't exclude business/marketing/product content
            is_pure_tech = mem_tags and mem_tags.issubset(
                tech_exclude | {"testing", "ci", "devops", "deployment"}
            )
            if tech_exclude and mem_tags & tech_exclude and is_pure_tech:
                continue
            filtered.append(r)

        # Separate patterns and warnings
        patterns = [r for r in filtered if r["memory"].type == MemoryType.PATTERN.value]
        warnings = [r for r in filtered
                     if r["memory"].type == MemoryType.ANTI_PATTERN.value
                     and r["memory"].anti_pattern
                     and r["memory"].anti_pattern.severity != "critical"]  # Critical already shown

        # Show patterns
        if patterns:
            label = (
                f"For \"{task[:40]}\":"
                if task
                else f"For {', '.join(sorted(stack_tags)[:3])}:"
                if stack_tags
                else "Relevant:"
            )
            if would_fit(label, token_budget - FOOTER_RESERVE):
                lines.append(label)
                for r in patterns:
                    m = r["memory"]
                    # Evidence prefix carries the confidence now, so the
                    # trailing ``(conf)`` suffix is dropped to avoid showing
                    # it twice.
                    candidate = _bullet("✓", m)
                    if not would_fit(candidate, token_budget - FOOTER_RESERVE):
                        break
                    lines.append(candidate)
                lines.append("")

        # Show non-critical warnings
        if warnings:
            header = "Warnings:"
            if would_fit(header, token_budget - FOOTER_RESERVE):
                lines.append(header)
                for r in warnings:
                    m = r["memory"]
                    sev = m.anti_pattern.severity.upper() if m.anti_pattern else "!"
                    candidate = _bullet(f"[{sev}]", m)
                    if not would_fit(candidate, token_budget - FOOTER_RESERVE):
                        break
                    lines.append(candidate)
                lines.append("")

    # ── Footer ──
    total = session.query(func.count(Memory.id)).scalar() or 0
    lines.append(f"[{total} memories — memee search <query> for more]")
    # Report the actual rendered token count rather than a flat-per-line guess.
    rendered = "\n".join(lines)
    tokens_used = _count_tokens(rendered) + _count_tokens(
        f"[~{token_budget} tokens / {token_budget} budget]"
    )
    lines.append(f"[~{tokens_used} tokens / {token_budget} budget]")

    return "\n".join(lines)


def _find_project(session: Session, project_path: str | None) -> Project | None:
    """Find project by path (with resolve fallback for tests)."""
    if not project_path:
        return None
    abs_path = str(Path(project_path).resolve())
    project = session.query(Project).filter_by(path=abs_path).first()
    if not project:
        project = session.query(Project).filter_by(path=project_path).first()
    return project


def _get_stack_tags(project: Project | None) -> set[str]:
    """Extract lowercase tags from project stack + tags."""
    if not project:
        return set()
    tags = set()
    for s in (project.stack or []):
        tags.add(s.lower())
    for t in (project.tags or []):
        tags.add(t.lower())
    return tags


def _get_exclude_tags(stack_tags: set[str]) -> set[str]:
    """Get tags that are unrelated to current stack."""
    exclude = set()
    for tag in stack_tags:
        exclude.update(_UNRELATED_TAGS.get(tag, set()))
    exclude -= stack_tags
    return exclude


def _strong_canon_match(
    session: Session,
    raw_query: str,
    threshold: float | None = None,
) -> bool:
    """R14 maturity gate: True iff the raw query already lights up a strong
    CANON/VALIDATED ``pattern`` via BM25.

    Probe: a single FTS5 ``MATCH`` joined to ``memories`` with the type +
    maturity filter pushed into SQL, returning the top 3 ranks. We then
    normalise the top hit's BM25 rank (FTS5 returns negative scores; the
    most negative = best) against the running probe's max-magnitude and
    test against ``threshold``.

    Why one query, not the full hybrid path:

      * The expansion gate fires *before* search runs; we don't want to
        pay the vector model load (slow cold-start) just to decide
        whether to expand.
      * The probe is a single SELECT with a tight LIMIT 3. Warm cost is
        sub-millisecond on the 255-row eval corpus and ≤2 ms on the
        500-memory production tier.
      * False-negatives (gate says "no canon, expand") cost only the
        existing baseline; false-positives (gate says "skip expansion"
        when the answer needed expansion) cost recall. The high default
        threshold (0.7) biases toward the safer false-negative.

    Returns False on any error so the caller falls through to the
    existing expansion path — never block the agent on a probe failure.
    """
    if not raw_query or not raw_query.strip():
        return False
    if threshold is None:
        threshold = MATURITY_GATE_THRESHOLD

    # Defer the import to avoid a cycle at module load (search.py imports
    # router-adjacent names elsewhere). Cheap on the warm path.
    from memee.engine.search import _sanitize_fts_query

    fts_and = _sanitize_fts_query(raw_query, operator="AND")
    if not fts_and:
        return False

    sql = text(
        """
        SELECT f.rank
        FROM memories_fts f
        JOIN memories m ON m.rowid = f.rowid
        WHERE memories_fts MATCH :query
          AND m.type = 'pattern'
          AND m.maturity IN ('canon', 'validated')
        ORDER BY f.rank
        LIMIT 3
        """
    )
    try:
        rows = session.execute(sql, {"query": fts_and}).fetchall()
    except Exception:
        # FTS5 syntax / OperationalError / DB-level failure. Falling
        # through preserves the existing behaviour exactly — the gate
        # is purely additive when it works and a no-op when it doesn't.
        return False
    if not rows:
        return False

    # FTS5 ``rank`` is an unbounded negative score (more negative = better).
    # An absolute magnitude threshold won't generalise across corpora; what
    # we want is "the top hit dominates". Normalise the dominance ratio
    #
    #     dominance = |top| / (|top| + |second|)
    #
    # which is bounded in (0, 1]: at 1.0 the second hit is a non-match and
    # the top is unambiguous; at 0.5 top and second are tied and the
    # canon answer isn't clearly the right one. Threshold 0.7 means the
    # top is at least ~2.3× the second hit — a strong-canon signal.
    #
    # If only one row came back, treat as full dominance (top with no rival
    # is the strongest possible signal). Empty result handled above.
    ranks = [abs(r[0]) for r in rows if r[0] is not None]
    if not ranks:
        return False
    top = ranks[0]
    if len(ranks) == 1:
        return True  # single canon match, no rival
    second = ranks[1]
    denom = top + second
    if denom <= 0.0:
        return False
    dominance = top / denom
    return dominance >= threshold


def _build_search_query(
    task: str,
    stack_tags: set[str],
    session: Session | None = None,
) -> str:
    """Build search query from task description with expansion.

    Expands task with related terms to catch more relevant memories.
    "CI/CD pipeline" → "CI/CD pipeline pre-commit hooks Docker deploy"

    R14: optional maturity gate. When the gate is enabled and a session
    is available, run the cheap canon probe first; if the raw query
    already matches a CANON/VALIDATED pattern strongly, return the raw
    task and skip expansion. The expansion broadens recall at the cost
    of precision; when the canon answer is already the top BM25 hit,
    broadening only dilutes it.
    """
    if task:
        if session is not None and _maturity_gate_enabled():
            if _strong_canon_match(session, task):
                return task
        expanded = _expand_query(task)
        return expanded
    if stack_tags:
        return " ".join(list(stack_tags)[:4])
    return ""


# Query expansion: common task descriptions → add related search terms
_EXPANSIONS = {
    # ── Engineering ──
    "ci": ["pre-commit", "hooks", "lint", "pipeline", "github actions"],
    "cd": ["deploy", "release", "rollback", "docker"],
    "pipeline": ["ci", "deploy", "docker", "github actions"],
    "test": ["pytest", "mock", "fixture", "coverage", "assert"],
    "deploy": ["docker", "kubernetes", "health check", "graceful shutdown", "rollback"],
    "security": ["auth", "secret", "key", "injection", "xss", "eval", "validate"],
    "audit": ["security", "vulnerability", "compliance", "secret"],
    "performance": ["slow", "optimize", "cache", "pool", "index", "async", "latency"],
    "slow": ["performance", "optimize", "index", "pool", "N+1", "cache"],
    "database": ["query", "index", "pool", "migration", "orm", "N+1", "sql"],
    "api": ["endpoint", "timeout", "retry", "validation", "pydantic", "rest"],
    "memory leak": ["cleanup", "useEffect", "async", "gc", "close", "dispose"],
    "leak": ["memory", "cleanup", "close", "resource", "gc"],
    "migrate": ["migration", "schema", "database", "upgrade", "rollback"],
    "refactor": ["clean", "architecture", "pattern", "structure"],
    "bug": ["debug", "fix", "error", "exception", "log"],
    "monitor": ["logging", "health", "alert", "metrics", "observability"],

    # ── Marketing & Content ──
    "seo": ["meta", "title tag", "keyword", "sitemap", "canonical", "schema markup",
            "search ranking", "organic", "backlink", "content optimization"],
    "marketing": ["campaign", "landing page", "conversion", "funnel", "lead",
                  "brand", "audience", "messaging", "copy", "CTA"],
    "content": ["blog", "article", "headline", "copy", "tone", "readability",
                "engagement", "seo", "keyword", "editorial"],
    "copy": ["headline", "CTA", "value proposition", "benefit", "tone of voice",
             "persuasion", "clarity", "conversion"],
    "landing": ["conversion rate", "CTA", "above fold", "hero", "social proof",
                "testimonial", "form", "A/B test"],
    "email": ["subject line", "open rate", "click rate", "unsubscribe",
              "personalization", "drip", "sequence", "CAN-SPAM"],
    "social": ["post", "engagement", "hashtag", "schedule", "platform",
               "audience", "analytics", "viral"],
    "ads": ["CPC", "CPM", "ROAS", "targeting", "creative", "audience",
            "budget", "bidding", "retargeting"],
    "brand": ["identity", "guideline", "tone", "voice", "positioning",
              "logo", "color", "typography", "consistency"],

    # ── Product ──
    "product": ["roadmap", "feature", "user story", "requirement", "prioritize",
                "stakeholder", "MVP", "iteration", "feedback"],
    "roadmap": ["priority", "quarter", "milestone", "epic", "deadline",
                "resource", "dependency", "scope"],
    "feature": ["requirement", "user story", "acceptance criteria", "scope",
                "edge case", "rollout", "flag"],
    "user research": ["interview", "survey", "persona", "journey", "pain point",
                      "usability", "prototype", "feedback"],
    "pricing": ["tier", "freemium", "conversion", "churn", "LTV", "ARPU",
                "willingness to pay", "competitor"],
    "metrics": ["KPI", "OKR", "north star", "funnel", "retention", "activation",
                "engagement", "churn", "DAU", "MAU"],
    "launch": ["go-to-market", "announcement", "beta", "waitlist", "Product Hunt",
               "press", "demo", "onboarding"],
    "mvp": ["scope", "minimal", "validate", "hypothesis", "iteration", "lean"],
    "feedback": ["NPS", "survey", "interview", "review", "support ticket",
                 "churn reason", "feature request"],
    "onboarding": ["activation", "first value", "tutorial", "checklist",
                   "time to value", "drop-off", "retention"],

    # ── Design & UX ──
    "design": ["UI", "UX", "wireframe", "mockup", "prototype", "component",
               "layout", "spacing", "typography", "color"],
    "ux": ["usability", "flow", "journey", "friction", "accessibility",
           "navigation", "information architecture", "heuristic"],
    "ui": ["component", "design system", "responsive", "mobile", "dark mode",
           "animation", "icon", "button", "form"],
    "accessibility": ["WCAG", "aria", "screen reader", "contrast", "keyboard",
                      "alt text", "focus", "semantic HTML"],
    "design system": ["token", "component library", "documentation", "variant",
                      "theme", "consistency", "Storybook"],

    # ── Data & Analytics ──
    "analytics": ["tracking", "event", "funnel", "cohort", "dashboard",
                  "Google Analytics", "Mixpanel", "segment"],
    "data": ["pipeline", "ETL", "warehouse", "lake", "quality", "schema",
             "transformation", "dbt", "SQL"],
    "dashboard": ["chart", "visualization", "KPI", "real-time", "filter",
                  "export", "Grafana", "Recharts"],
    "a/b test": ["experiment", "variant", "significance", "sample size",
                 "confidence", "control", "hypothesis"],
    "tracking": ["event", "property", "user ID", "session", "attribution",
                 "pixel", "consent", "GDPR"],

    # ── Operations & Legal ──
    "gdpr": ["consent", "data deletion", "right to access", "DPA",
             "data processor", "retention", "privacy policy"],
    "compliance": ["GDPR", "SOC2", "HIPAA", "audit", "policy", "retention",
                   "encryption", "access control"],
    "legal": ["terms", "privacy", "GDPR", "license", "contract",
              "intellectual property", "liability"],
    "hiring": ["job description", "interview", "assessment", "culture fit",
               "compensation", "equity", "onboarding"],
    "process": ["workflow", "automation", "documentation", "template",
                "checklist", "SOP", "retrospective"],
}


import re as _re


def _keyword_matches(task_lower: str, keyword: str) -> bool:
    """True iff ``keyword`` occurs in ``task_lower`` at word/phrase boundaries.

    Old code used ``keyword in task_lower`` which matched substrings inside
    longer words — ``"ci"`` inside ``"pricing"`` pulled pre-commit, lint, and
    hooks into an SEO-copy task. We now anchor every expansion key to
    boundaries so ``pricing`` no longer triggers ``ci``.

    Keys can be single tokens (``"ci"``) or multi-token phrases (``"user
    research"`` / ``"a/b test"``). Both are treated as whole phrases.
    """
    if not keyword:
        return False
    # Escape the key and wrap with boundary assertions. ``\b`` anchors on
    # word-boundary transitions, which is what we want for identifier-like
    # tokens and for multi-word phrases alike. For keys containing a slash
    # (e.g. ``a/b test``) ``\b`` still does the right thing on both sides.
    pattern = r"\b" + _re.escape(keyword) + r"\b"
    return _re.search(pattern, task_lower) is not None


def _expand_query(task: str) -> str:
    """Expand a task description with related terms for broader search.

    The match rule is token/phrase boundaries, not naive substring. We also
    cap total added terms so a generic query ("pricing page copy" → pricing +
    copy + landing) doesn't drown the primary signal.
    """
    task_lower = task.lower()
    additions: list[str] = []
    seen: set[str] = set()

    # Iterate in insertion order so the most specific (multi-word) keys have
    # first shot at the budget. Callers control order via dict ordering above.
    for keyword, expansions in _EXPANSIONS.items():
        if not _keyword_matches(task_lower, keyword):
            continue
        for exp in expansions[:3]:  # top 3 expansions per matched keyword
            key = exp.lower()
            if key in seen:
                continue
            seen.add(key)
            additions.append(exp)
            if len(additions) >= 9:  # hard cap; 3 keys × 3 terms keeps queries tight
                break
        if len(additions) >= 9:
            break

    if additions:
        return task + " " + " ".join(additions)
    return task
