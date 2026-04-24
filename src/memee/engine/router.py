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

from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryType,
    Project,
)

# Approximate tokens per memory line
TOKENS_PER_LINE = 15

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
    lines = []
    tokens_used = 0

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
        lines.append("CRITICAL (always):")
        for m, ap in critical_aps:
            if tokens_used >= 100:
                break
            lines.append(f"  ⚠ {m.title}")
            tokens_used += TOKENS_PER_LINE
        lines.append("")

    # ── Layer 1: Search-routed briefing ──
    # Build search query from task + stack context
    search_query = _build_search_query(task, stack_tags)

    if search_query:
        from memee.engine.search import search_memories

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
            label = f"For \"{task[:40]}\":" if task else f"For {', '.join(sorted(stack_tags)[:3])}:" if stack_tags else "Relevant:"
            lines.append(label)
            for r in patterns:
                if tokens_used >= token_budget - TOKENS_PER_LINE * 3:
                    break
                m = r["memory"]
                conf = f"{m.confidence_score:.0%}"
                lines.append(f"  ✓ {m.title} ({conf})")
                tokens_used += TOKENS_PER_LINE
            lines.append("")

        # Show non-critical warnings
        if warnings and tokens_used < token_budget - TOKENS_PER_LINE * 3:
            lines.append("Warnings:")
            for r in warnings:
                if tokens_used >= token_budget - TOKENS_PER_LINE * 2:
                    break
                m = r["memory"]
                sev = m.anti_pattern.severity.upper() if m.anti_pattern else "!"
                lines.append(f"  [{sev}] {m.title}")
                tokens_used += TOKENS_PER_LINE
            lines.append("")

    # ── Footer ──
    total = session.query(func.count(Memory.id)).scalar() or 0
    lines.append(f"[{total} memories — memee search <query> for more]")
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


def _build_search_query(task: str, stack_tags: set[str]) -> str:
    """Build search query from task description with expansion.

    Expands task with related terms to catch more relevant memories.
    "CI/CD pipeline" → "CI/CD pipeline pre-commit hooks Docker deploy"
    """
    if task:
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


def _expand_query(task: str) -> str:
    """Expand task description with related terms for broader search."""
    task_lower = task.lower()
    additions = set()

    for keyword, expansions in _EXPANSIONS.items():
        if keyword in task_lower:
            additions.update(expansions[:3])  # Top 3 expansions per keyword

    if additions:
        return task + " " + " ".join(additions)
    return task
