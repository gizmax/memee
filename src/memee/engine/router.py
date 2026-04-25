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

    # ── Layer 0: Critical DNA (always shown) ──
    critical_aps = (
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

    if critical_aps:
        header = "CRITICAL (always):"
        if would_fit(header, token_budget - FOOTER_RESERVE):
            lines.append(header)
            for m, _ap in critical_aps:
                candidate = f"  ⚠ {m.title}"
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
                    conf = f"{m.confidence_score:.0%}"
                    candidate = f"  ✓ {m.title} ({conf})"
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
                    candidate = f"  [{sev}] {m.title}"
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
