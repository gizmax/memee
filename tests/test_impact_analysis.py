"""Impact analysis: quantify each bold idea's effect on Org IQ.

Baseline from simulation:
  Org IQ = 26.6 / 100
  - Canon ratio:        0.000 × 30 =  0.0
  - Validated ratio:    0.033 × 25 =  0.8
  - Avg confidence:     0.532 × 20 = 10.6
  - Avoidance rate:     0.363 × 15 =  5.4
  - Decision stability: 0.972 × 10 =  9.7

Bottlenecks identified:
  1. Pattern propagation = 1.0 proj/pattern (patterns don't spread)
  2. Stale hypotheses = 48.2% (knowledge dies unvalidated)
  3. Canon = 0% (thresholds too high OR not enough cross-project activity)
  4. Avoidance rate = 36.3% (only pull, no push)

This test simulates each bold idea individually and measures delta Org IQ.

Run: pytest tests/test_impact_analysis.py -v -s
"""

import random
from collections import defaultdict

import pytest
from sqlalchemy import func

from memee.engine.confidence import update_confidence
from memee.engine.lifecycle import run_aging_cycle
from memee.storage.models import (
    AntiPattern,
    Decision,
    MaturityLevel,
    Memory,
    MemoryConnection,
    MemoryType,
    MemoryValidation,
    Project,
    ProjectMemory,
)

random.seed(2026)

NUM_PROJECTS = 20
NUM_AGENTS = 8
NUM_WEEKS = 12

STACKS = {
    "python-api": ["Python", "FastAPI", "SQLite", "Pydantic"],
    "python-flask": ["Python", "Flask", "SQLite", "Jinja2"],
    "react-app": ["React", "TypeScript", "Tailwind", "Vite"],
    "swift-ios": ["Swift", "SwiftUI", "CoreData", "UIKit"],
    "data-pipeline": ["Python", "pandas", "Airflow", "SQLite"],
    "fullstack": ["Python", "FastAPI", "React", "PostgreSQL"],
}

PATTERN_TEMPLATES = [
    ("Always use timeout on HTTP requests", ["python", "http", "reliability"]),
    ("SQLite WAL mode for concurrent reads", ["sqlite", "database", "performance"]),
    ("Use row_factory for dict-like DB access", ["sqlite", "python", "database"]),
    ("React useEffect cleanup prevents leaks", ["react", "frontend", "hooks"]),
    ("ThreadPoolExecutor for parallel API calls", ["python", "concurrency", "api"]),
    ("Pydantic model_validate over manual parsing", ["python", "pydantic", "validation"]),
    ("Tailwind @apply for repeated patterns", ["tailwind", "css", "frontend"]),
    ("SwiftUI .task for async data loading", ["swift", "swiftui", "async"]),
    ("Index foreign keys in SQLite", ["sqlite", "database", "indexing"]),
    ("FastAPI Depends for session injection", ["python", "fastapi", "di"]),
    ("Use async/await for I/O bound operations", ["python", "async", "performance"]),
    ("Implement retry logic for 5xx errors", ["python", "http", "reliability"]),
    ("Use environment variables for config", ["security", "config", "deployment"]),
    ("Structured logging with correlation IDs", ["python", "logging", "observability"]),
    ("Use connection pooling for databases", ["database", "performance", "python"]),
    ("Circuit breaker for external APIs", ["python", "resilience", "api"]),
    ("Use TypeScript strict mode", ["typescript", "frontend", "safety"]),
    ("Validate all user input at API boundary", ["security", "api", "validation"]),
    ("Use Git hooks for pre-commit checks", ["git", "ci", "quality"]),
    ("Cache expensive computations with TTL", ["performance", "caching", "python"]),
]

AP_TEMPLATES = [
    ("Don't use pypdf for complex PDFs", "high", ["python", "pdf"]),
    ("Don't use git reset --hard in automation", "critical", ["git", "automation"]),
    ("Don't store API keys in code", "critical", ["security", "secrets"]),
    ("Avoid N+1 queries in ORM", "high", ["database", "performance"]),
    ("Don't use requests without timeout", "high", ["python", "http"]),
    ("Don't catch bare Exception", "medium", ["python", "error-handling"]),
    ("Don't use eval() or exec()", "critical", ["python", "security"]),
    ("Avoid synchronous I/O in async code", "high", ["python", "async"]),
]


def compute_org_iq(session) -> dict:
    """Compute Org IQ and all sub-scores."""
    total = session.query(func.count(Memory.id)).scalar() or 1
    canon = session.query(func.count(Memory.id)).filter(
        Memory.maturity == MaturityLevel.CANON.value
    ).scalar()
    validated = session.query(func.count(Memory.id)).filter(
        Memory.maturity == MaturityLevel.VALIDATED.value
    ).scalar()
    avg_conf = session.query(func.avg(Memory.confidence_score)).scalar() or 0

    canon_r = canon / total
    validated_r = validated / total

    return {
        "total": total,
        "canon": canon,
        "validated": validated,
        "canon_ratio": canon_r,
        "validated_ratio": validated_r,
        "avg_confidence": avg_conf,
        "canon_score": canon_r * 30,
        "validated_score": validated_r * 25,
        "confidence_score": avg_conf * 20,
        "iq": canon_r * 30 + validated_r * 25 + avg_conf * 20,
    }


def seed_base_data(session, org, projects):
    """Seed 12 weeks of base data (same as large simulation)."""
    random.seed(2026)
    agents = [f"agent-{chr(65 + i)}" for i in range(NUM_AGENTS)]
    all_memories = []
    all_anti_patterns = []

    for week in range(1, NUM_WEEKS + 1):
        maturity_mult = 1.0 + (week / NUM_WEEKS) * 0.5
        patterns_count = int(15 * maturity_mult)

        for _ in range(patterns_count):
            tmpl_idx = random.randint(0, len(PATTERN_TEMPLATES) - 1)
            title, tags = PATTERN_TEMPLATES[tmpl_idx]
            proj = random.choice(projects)

            m = Memory(
                type=MemoryType.PATTERN.value,
                title=f"W{week}: {title} (v{random.randint(1, 999)})",
                content=f"{title}. Week {week}.",
                tags=tags,
                source_agent=random.choice(agents),
            )
            session.add(m)
            session.flush()
            pm = ProjectMemory(project_id=proj.id, memory_id=m.id)
            session.add(pm)
            all_memories.append(m)

        for _ in range(4):
            tmpl_idx = random.randint(0, len(AP_TEMPLATES) - 1)
            title, severity, tags = AP_TEMPLATES[tmpl_idx]
            proj = random.choice(projects)

            am = Memory(
                type=MemoryType.ANTI_PATTERN.value,
                title=f"W{week}: {title} (v{random.randint(1, 999)})",
                content=f"Anti-pattern: {title}",
                tags=tags,
                source_agent=random.choice(agents),
            )
            session.add(am)
            session.flush()
            ap = AntiPattern(
                memory_id=am.id, severity=severity,
                trigger=title, consequence="Known issue",
                alternative="See best practices",
            )
            session.add(ap)
            session.flush()
            pm = ProjectMemory(project_id=proj.id, memory_id=am.id)
            session.add(pm)
            all_memories.append(am)
            all_anti_patterns.append(am)

        # Decisions
        for _ in range(3):
            proj = random.choice(projects)
            dm = Memory(
                type=MemoryType.DECISION.value,
                title=f"W{week}: Decision v{random.randint(1,999)}",
                content="Decision.",
                tags=["decision"],
            )
            session.add(dm)
            session.flush()
            dec = Decision(memory_id=dm.id, chosen="X", alternatives=[])
            session.add(dec)
            pm = ProjectMemory(project_id=proj.id, memory_id=dm.id)
            session.add(pm)
            all_memories.append(dm)

        session.commit()

        # Base validations (same as control)
        validations_count = int(20 * maturity_mult)
        validatable = [m for m in all_memories if m.type == MemoryType.PATTERN.value]
        for _ in range(min(validations_count, len(validatable))):
            m = random.choice(validatable)
            proj = random.choice(projects)
            validated = random.random() < 0.75
            v = MemoryValidation(
                memory_id=m.id, project_id=proj.id, validated=validated,
            )
            session.add(v)
            update_confidence(m, validated, proj.id)

        session.commit()
        run_aging_cycle(session)

    return all_memories, all_anti_patterns


@pytest.fixture
def base_env(session, org):
    """Create 20 projects."""
    projects = []
    stack_keys = list(STACKS.keys())
    for i in range(NUM_PROJECTS):
        stack_key = stack_keys[i % len(stack_keys)]
        proj = Project(
            organization_id=org.id,
            name=f"Project-{i:02d}-{stack_key}",
            path=f"/projects/project-{i:02d}",
            stack=STACKS[stack_key],
            tags=[stack_key],
        )
        session.add(proj)
        projects.append(proj)
    session.commit()
    return session, projects, org


class TestImpactAnalysis:

    def test_impact_comparison(self, base_env):
        """Run baseline + each improvement, compare Org IQ delta."""
        session, projects, org = base_env

        # ═══════════════════════════════════
        # CONTROL: Baseline (no improvements)
        # ═══════════════════════════════════
        random.seed(2026)
        all_memories, all_anti_patterns = seed_base_data(session, org, projects)
        baseline = compute_org_iq(session)

        # Count pattern propagation
        patterns = [m for m in all_memories if m.type == MemoryType.PATTERN.value]
        avg_spread_baseline = (
            sum(len(m.projects) for m in patterns) / len(patterns)
            if patterns else 0
        )
        stale_baseline = session.query(func.count(Memory.id)).filter(
            Memory.maturity == MaturityLevel.HYPOTHESIS.value,
            Memory.validation_count == 0,
        ).scalar()

        # ═══════════════════════════════════════
        # IMPROVEMENT 1: AUTO-PROPAGATION
        # When a pattern is validated, push it to all projects
        # with matching stack tags.
        # ═══════════════════════════════════════
        propagated = 0
        for memory in patterns:
            if memory.confidence_score < 0.55:
                continue
            mem_tags = set(memory.tags or [])
            origin_proj_ids = {pm.project_id for pm in memory.projects}

            for proj in projects:
                if proj.id in origin_proj_ids:
                    continue
                proj_stack_tags = set(t.lower() for t in (proj.stack or []))
                proj_tags = set(proj.tags or [])
                overlap = mem_tags & (proj_stack_tags | proj_tags)
                if overlap:
                    # Auto-propagate with validation
                    update_confidence(memory, True, proj.id)
                    propagated += 1
                    if propagated > 800:
                        break
            if propagated > 800:
                break

        session.commit()
        after_propagation = compute_org_iq(session)
        avg_spread_after = (
            sum(len(m.projects) for m in patterns) / len(patterns)
            if patterns else 0
        )

        # ═══════════════════════════════════════
        # IMPROVEMENT 2: DREAM MODE
        # Nightly: auto-connect related memories,
        # propose promotions, clean stale hypotheses.
        # ═══════════════════════════════════════

        # Find similar patterns and create connections
        connections_created = 0
        tag_groups = defaultdict(list)
        for m in all_memories:
            for tag in (m.tags or []):
                tag_groups[tag].append(m)

        # Connect patterns with 2+ shared tags
        seen_pairs = set()
        for tag, members in tag_groups.items():
            for i, m1 in enumerate(members[:20]):
                for m2 in members[i+1:20]:
                    pair = tuple(sorted([m1.id, m2.id]))
                    if pair in seen_pairs:
                        continue
                    shared = set(m1.tags or []) & set(m2.tags or [])
                    if len(shared) >= 2:
                        conn = MemoryConnection(
                            source_id=m1.id, target_id=m2.id,
                            relationship_type="related_to",
                            strength=len(shared) / 5,
                        )
                        session.add(conn)
                        seen_pairs.add(pair)
                        connections_created += 1

        # Auto-validate patterns that have graph neighbors with high confidence
        dream_validations = 0
        for m in patterns:
            if m.maturity in (MaturityLevel.HYPOTHESIS.value, MaturityLevel.TESTED.value):
                neighbor_confs = []
                conns = session.query(MemoryConnection).filter(
                    (MemoryConnection.source_id == m.id) |
                    (MemoryConnection.target_id == m.id)
                ).all()
                for c in conns:
                    neighbor_id = c.target_id if c.source_id == m.id else c.source_id
                    neighbor = session.get(Memory, neighbor_id)
                    if neighbor and neighbor.confidence_score > 0.6:
                        neighbor_confs.append(neighbor.confidence_score)

                if len(neighbor_confs) >= 2:
                    avg_neighbor = sum(neighbor_confs) / len(neighbor_confs)
                    if avg_neighbor > 0.6:
                        boost = 0.03 * len(neighbor_confs)
                        m.confidence_score = min(0.99, m.confidence_score + boost)
                        m.maturity = (
                            MaturityLevel.TESTED.value
                            if m.application_count >= 1
                            else m.maturity
                        )
                        dream_validations += 1

        session.commit()
        run_aging_cycle(session)
        after_dream = compute_org_iq(session)

        stale_after_dream = session.query(func.count(Memory.id)).filter(
            Memory.maturity == MaturityLevel.HYPOTHESIS.value,
            Memory.validation_count == 0,
        ).scalar()

        # ═══════════════════════════════════════
        # IMPROVEMENT 3: PREDICTIVE ANTI-PATTERN PUSH
        # When project is registered, auto-inject relevant anti-patterns.
        # Simulated: for each project, match AP tags to stack, auto-validate.
        # ═══════════════════════════════════════
        push_warnings = 0
        push_avoidances = 0
        for proj in projects:
            proj_tags = set(t.lower() for t in (proj.stack or []))
            for ap_mem in all_anti_patterns:
                ap_tags = set(ap_mem.tags or [])
                if ap_tags & proj_tags:
                    push_warnings += 1

                    # Every pushed warning creates/updates a ProjectMemory
                    # link so get_impact_summary counts it under
                    # warnings_shown.
                    pm = (
                        session.query(ProjectMemory)
                        .filter_by(project_id=proj.id, memory_id=ap_mem.id)
                        .first()
                    )
                    if pm is None:
                        pm = ProjectMemory(
                            project_id=proj.id, memory_id=ap_mem.id,
                        )
                        session.add(pm)

                    # 70% of pushed warnings prevent a mistake (vs 36% pull)
                    if random.random() < 0.70:
                        push_avoidances += 1
                        update_confidence(ap_mem, True, proj.id)
                        # Honest-metric bookkeeping: needs evidence_type
                        # for mistakes_avoided to count.
                        pm.applied = True
                        pm.outcome = "avoided"
                        pm.outcome_evidence_type = "review_comment"
                        pm.outcome_evidence_ref = (
                            f"sim://{proj.id}/ap/{ap_mem.id}"
                        )

        session.commit()
        after_push = compute_org_iq(session)
        push_avoidance_rate = push_avoidances / max(push_warnings, 1)

        # ═══════════════════════════════════════
        # IMPROVEMENT 4: MEMORY-DRIVEN CODE REVIEW
        # Scan "diffs" against anti-pattern DB.
        # Each caught issue = +1 avoidance, +1 AP validation.
        # ═══════════════════════════════════════
        review_catches = 0
        review_scans = NUM_PROJECTS * NUM_WEEKS * 3  # 3 PRs per project per week
        for _ in range(review_scans):
            if all_anti_patterns and random.random() < 0.25:
                ap = random.choice(all_anti_patterns)
                review_catches += 1
                update_confidence(ap, True, random.choice(projects).id)

        session.commit()
        after_review = compute_org_iq(session)
        review_catch_rate = review_catches / review_scans

        # ═══════════════════════════════════════
        # IMPROVEMENT 5: KNOWLEDGE COMPILER
        # Monthly: extract CANON + VALIDATED into generated doc.
        # Impact: indirect — makes knowledge accessible, increases adoption.
        # Simulate: 20% more validations from increased discoverability.
        # ═══════════════════════════════════════
        compiler_extra = int(len(patterns) * 0.20)
        for _ in range(compiler_extra):
            m = random.choice(patterns)
            proj = random.choice(projects)
            update_confidence(m, True, proj.id)

        session.commit()
        after_compiler = compute_org_iq(session)

        # ═══════════════════════════════════════
        # IMPROVEMENT 6: COMPETITIVE MEMORY (A/B)
        # Contradicting patterns compete. Winner gets boosted,
        # loser gets penalized. Net effect: faster convergence.
        # ═══════════════════════════════════════
        # Find patterns with same tags, different outcomes
        ab_tests = 0
        for tag, members in tag_groups.items():
            pats = [m for m in members if m.type == MemoryType.PATTERN.value]
            if len(pats) >= 2:
                winner = max(pats[:5], key=lambda m: m.confidence_score)
                for loser in pats[:5]:
                    if loser.id != winner.id:
                        winner.confidence_score = min(0.99, winner.confidence_score + 0.03)
                        loser.confidence_score = max(0.01, loser.confidence_score - 0.02)
                        ab_tests += 1

        session.commit()
        run_aging_cycle(session)
        after_ab = compute_org_iq(session)

        # ═══════════════════════════════════════
        # IMPROVEMENT 7: FAILURE CASCADE
        # When AP invalidated, reduce confidence of dependent patterns.
        # Net: fewer false-positive patterns, cleaner knowledge base.
        # ═══════════════════════════════════════
        cascades = 0
        deprecated_by_cascade = 0
        for ap_mem in all_anti_patterns:
            conns = session.query(MemoryConnection).filter(
                MemoryConnection.source_id == ap_mem.id
            ).all()
            for conn in conns:
                target = session.get(Memory, conn.target_id)
                if target and target.type == MemoryType.PATTERN.value:
                    target.confidence_score = max(
                        0.01, target.confidence_score * 0.8
                    )
                    cascades += 1
                    if target.confidence_score < 0.2:
                        deprecated_by_cascade += 1

        session.commit()
        run_aging_cycle(session)
        after_cascade = compute_org_iq(session)

        # ═══════════════════════════════════════
        # IMPROVEMENT 8: AGENT ROUTING
        # Route tasks to best-performing agent per domain.
        # Simulated: top agents validate 15% more successfully.
        # ═══════════════════════════════════════
        routing_extra = int(len(patterns) * 0.15)
        for _ in range(routing_extra):
            m = random.choice(patterns)
            proj = random.choice(projects)
            # Routed agent always validates positively (better match)
            update_confidence(m, True, proj.id)

        session.commit()
        after_routing = compute_org_iq(session)

        # ═══════════════════════════════════════
        # IMPROVEMENT 9: TEMPORAL KNOWLEDGE GRAPH
        # Time-indexed facts. Enables: "was this true at time T?"
        # Impact: reduces stale knowledge applied in wrong era.
        # Simulate: 10% of hypotheses get correct time-scoping.
        # ═══════════════════════════════════════
        temporal_fixes = 0
        hypos = session.query(Memory).filter(
            Memory.maturity == MaturityLevel.HYPOTHESIS.value
        ).limit(int(len(all_memories) * 0.10)).all()
        for m in hypos:
            # Temporal scoping either validates or correctly deprecates
            if m.confidence_score > 0.5:
                m.maturity = MaturityLevel.TESTED.value
                m.application_count = max(m.application_count, 1)
            else:
                m.maturity = MaturityLevel.DEPRECATED.value
            temporal_fixes += 1

        session.commit()
        after_temporal = compute_org_iq(session)

        # ═══════════════════════════════════════
        # IMPROVEMENT 10: MEMORY INHERITANCE
        # New projects inherit from similar existing projects.
        # Effect: faster ramp-up, more cross-project connections.
        # ═══════════════════════════════════════
        inherited = 0
        for proj in projects[:5]:  # 5 "new" projects
            similar = [p for p in projects[5:] if set(p.stack or []) & set(proj.stack or [])]
            for sim_proj in similar[:3]:
                sim_memories = session.query(ProjectMemory).filter(
                    ProjectMemory.project_id == sim_proj.id
                ).limit(10).all()
                for spm in sim_memories:
                    existing = session.query(ProjectMemory).filter_by(
                        project_id=proj.id, memory_id=spm.memory_id
                    ).first()
                    if not existing:
                        new_pm = ProjectMemory(
                            project_id=proj.id, memory_id=spm.memory_id
                        )
                        session.add(new_pm)
                        mem = session.get(Memory, spm.memory_id)
                        if mem:
                            update_confidence(mem, True, proj.id)
                        inherited += 1

        session.commit()
        after_inherit = compute_org_iq(session)

        # ═══════════════════════════════════════════════════
        # REPORT
        # ═══════════════════════════════════════════════════

        # NOTE: improvements are CUMULATIVE in this test (each builds on previous)
        # So we calculate both cumulative and isolated delta

        stages = [
            ("BASELINE (control)", baseline, None),
            ("1. Auto-Propagation", after_propagation, baseline),
            ("2. Dream Mode", after_dream, after_propagation),
            ("3. Predictive AP Push", after_push, after_dream),
            ("4. Code Review", after_review, after_push),
            ("5. Knowledge Compiler", after_compiler, after_review),
            ("6. Competitive A/B", after_ab, after_compiler),
            ("7. Failure Cascade", after_cascade, after_ab),
            ("8. Agent Routing", after_routing, after_cascade),
            ("9. Temporal Graph", after_temporal, after_routing),
            ("10. Memory Inheritance", after_inherit, after_temporal),
        ]

        print("\n" + "=" * 80)
        print("  IMPACT ANALYSIS: EACH IMPROVEMENT vs BASELINE")
        print("=" * 80)

        print(f"\n  {'Feature':<28s} | {'Org IQ':>7s} | {'Delta':>7s} | "
              f"{'%Improve':>8s} | {'Canon':>5s} | {'Valid':>5s} | {'AvgConf':>7s}")
        print(f"  {'─'*28} | {'─'*7} | {'─'*7} | {'─'*8} | {'─'*5} | {'─'*5} | {'─'*7}")

        for name, data, prev in stages:
            delta = data["iq"] - baseline["iq"]
            pct = (delta / baseline["iq"] * 100) if baseline["iq"] > 0 else 0
            step_delta = (data["iq"] - prev["iq"]) if prev else 0

            print(
                f"  {name:<28s} | {data['iq']:7.1f} | "
                f"{'+' if delta >= 0 else ''}{delta:6.1f} | "
                f"{'+' if pct >= 0 else ''}{pct:6.1f}% | "
                f"{data['canon']:5d} | {data['validated']:5d} | "
                f"{data['avg_confidence']:7.3f}"
            )

        # ═══════════════════════════════════════════════════
        # UNIQUENESS SCORING
        # ═══════════════════════════════════════════════════

        print(f"\n{'=' * 80}")
        print("  UNIQUENESS vs COMPETITORS (Mem0, Zep, LangMem, Letta)")
        print("=" * 80)

        features = [
            {
                "name": "Auto-Propagation",
                "impact_pct": ((after_propagation["iq"] - baseline["iq"]) / baseline["iq"] * 100),
                "uniqueness": 95,
                "effort_days": 3,
                "description": "Push patterns to matching-stack projects automatically",
                "competitors": "Nobody does cross-project push. All are single-context.",
            },
            {
                "name": "Dream Mode",
                "impact_pct": ((after_dream["iq"] - after_propagation["iq"]) / baseline["iq"] * 100),
                "uniqueness": 80,
                "effort_days": 5,
                "description": "Nightly: auto-connect, find contradictions, propose promotions",
                "competitors": "Letta has sleep-time compute, but not for cross-project synthesis.",
            },
            {
                "name": "Predictive AP Push",
                "impact_pct": ((after_push["iq"] - after_dream["iq"]) / baseline["iq"] * 100),
                "uniqueness": 100,
                "effort_days": 2,
                "description": "Push anti-pattern warnings to new projects based on stack match",
                "competitors": "Zero competitors. Nobody does proactive failure prevention.",
            },
            {
                "name": "Code Review",
                "impact_pct": ((after_review["iq"] - after_push["iq"]) / baseline["iq"] * 100),
                "uniqueness": 90,
                "effort_days": 4,
                "description": "Scan git diff against anti-pattern DB before merge",
                "competitors": "Static analyzers exist, but none use institutional memory.",
            },
            {
                "name": "Knowledge Compiler",
                "impact_pct": ((after_compiler["iq"] - after_review["iq"]) / baseline["iq"] * 100),
                "uniqueness": 70,
                "effort_days": 3,
                "description": "Monthly: generate CLAUDE.md from CANON + VALIDATED patterns",
                "competitors": "Notion AI summarizes, but not from validated patterns DB.",
            },
            {
                "name": "Competitive A/B",
                "impact_pct": ((after_ab["iq"] - after_compiler["iq"]) / baseline["iq"] * 100),
                "uniqueness": 100,
                "effort_days": 4,
                "description": "Contradicting patterns compete statistically, winner promoted",
                "competitors": "Nobody does data-driven pattern selection for AI agents.",
            },
            {
                "name": "Failure Cascade",
                "impact_pct": ((after_cascade["iq"] - after_ab["iq"]) / baseline["iq"] * 100),
                "uniqueness": 85,
                "effort_days": 2,
                "description": "Invalidated pattern cascades confidence reduction to dependents",
                "competitors": "Knowledge graphs exist, but none cascade confidence updates.",
            },
            {
                "name": "Agent Routing",
                "impact_pct": ((after_routing["iq"] - after_cascade["iq"]) / baseline["iq"] * 100),
                "uniqueness": 75,
                "effort_days": 3,
                "description": "Route tasks to agent with best domain track record",
                "competitors": "OpenAI has multi-agent, but no expertise-based routing.",
            },
            {
                "name": "Temporal Graph",
                "impact_pct": ((after_temporal["iq"] - after_routing["iq"]) / baseline["iq"] * 100),
                "uniqueness": 90,
                "effort_days": 5,
                "description": "Time-indexed facts: knowledge with validity windows",
                "competitors": "Zep has temporal facts, but not for patterns/decisions.",
            },
            {
                "name": "Memory Inheritance",
                "impact_pct": ((after_inherit["iq"] - after_temporal["iq"]) / baseline["iq"] * 100),
                "uniqueness": 95,
                "effort_days": 2,
                "description": "New projects inherit validated patterns from similar projects",
                "competitors": "Nobody does project-level knowledge inheritance.",
            },
        ]

        # Sort by composite score: impact × uniqueness / effort
        for f in features:
            f["composite"] = abs(f["impact_pct"]) * f["uniqueness"] / max(f["effort_days"], 1)

        features.sort(key=lambda f: -f["composite"])

        print(f"\n  {'Rank':>4s} | {'Feature':<22s} | {'IQ %':>7s} | {'Unique':>6s} | "
              f"{'Days':>4s} | {'Composite':>9s}")
        print(f"  {'─'*4} | {'─'*22} | {'─'*7} | {'─'*6} | {'─'*4} | {'─'*9}")
        for i, f in enumerate(features, 1):
            sign = "+" if f["impact_pct"] >= 0 else ""
            bar = "█" * int(f["composite"] / 50)
            print(
                f"  {i:4d} | {f['name']:<22s} | {sign}{f['impact_pct']:5.1f}% | "
                f"{f['uniqueness']:5d}% | {f['effort_days']:4d} | "
                f"{f['composite']:8.0f}  {bar}"
            )

        # ═══════════════════════════════════════════════════
        # TOP 3 RECOMMENDATION
        # ═══════════════════════════════════════════════════

        print(f"\n{'=' * 80}")
        print("  TOP 3 RECOMMENDATIONS (highest composite score)")
        print("=" * 80)

        for i, f in enumerate(features[:3], 1):
            print(f"\n  #{i}. {f['name']}")
            print(f"      Impact:     {f['impact_pct']:+.1f}% Org IQ")
            print(f"      Uniqueness: {f['uniqueness']}% (vs competitors)")
            print(f"      Effort:     {f['effort_days']} days")
            print(f"      What:       {f['description']}")
            print(f"      Why unique: {f['competitors']}")

        # ═══════════════════════════════════════════════════
        # CUMULATIVE IMPACT
        # ═══════════════════════════════════════════════════

        final_iq = after_inherit["iq"]
        total_improvement = ((final_iq - baseline["iq"]) / baseline["iq"] * 100)

        print(f"\n{'=' * 80}")
        print("  CUMULATIVE: ALL 10 IMPROVEMENTS")
        print(f"  Baseline Org IQ:     {baseline['iq']:6.1f}")
        print(f"  Final Org IQ:        {final_iq:6.1f}")
        print(f"  Total improvement:   {total_improvement:+.1f}%")
        print(f"  Canon memories:      {baseline['canon']} -> {after_inherit['canon']}")
        print(f"  Validated memories:  {baseline['validated']} -> {after_inherit['validated']}")
        print(f"  Avg confidence:      {baseline['avg_confidence']:.3f} -> {after_inherit['avg_confidence']:.3f}")
        print(f"{'=' * 80}")

        # Stats for display
        print("\n  Extra stats:")
        print(f"    Auto-propagated:     {propagated} patterns")
        print(f"    Dream connections:   {connections_created}")
        print(f"    Dream validations:   {dream_validations}")
        print(f"    Push warnings:       {push_warnings} (avoidance: {push_avoidance_rate:.0%})")
        print(f"    Code review catches: {review_catches}/{review_scans} ({review_catch_rate:.0%})")
        print(f"    A/B tests run:       {ab_tests}")
        print(f"    Cascade events:      {cascades}")
        print(f"    Inherited memories:  {inherited}")
        print(f"    Temporal fixes:      {temporal_fixes}")
        print(f"    Stale (before):      {stale_baseline}")
        print(f"    Stale (after dream): {stale_after_dream}")
        print(f"    Propagation:         {avg_spread_baseline:.1f} -> {avg_spread_after:.1f} proj/pattern")

        assert final_iq > baseline["iq"], "Improvements should increase Org IQ"
        assert total_improvement > 50, "Combined improvements should give >50% boost"


def test_feedback_splits_mistake_made_vs_avoided(session, org, tmp_path):
    """Post-task review must branch MISTAKE_MADE (failure) vs MISTAKE_AVOIDED (success).

    Regression for a no-op ternary in feedback.py that classified every
    violation as MISTAKE_AVOIDED, turning the honesty of the impact counters
    inside out.
    """
    from memee.engine.feedback import post_task_review
    from memee.engine.impact import (
        ImpactEvent,
        ImpactType,
        get_impact_summary,
    )

    # Seed: one anti-pattern (eval), one good pattern (timeout).
    project_dir = tmp_path / "feedback-project"
    project_dir.mkdir()
    proj = Project(
        organization_id=org.id, name="FeedbackProj",
        path=str(project_dir),
        stack=["Python"], tags=["python"],
    )
    session.add(proj)

    ap_memory = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        title="Never use eval() on user input",
        content="eval on untrusted input is remote code execution.",
        tags=["python", "security", "eval"],
        confidence_score=0.85,
    )
    session.add(ap_memory)
    session.flush()
    ap = AntiPattern(
        memory_id=ap_memory.id, severity="critical",
        trigger="eval()", consequence="RCE", alternative="ast.literal_eval",
    )
    session.add(ap)

    ok_memory = Memory(
        type=MemoryType.PATTERN.value,
        title="Always use timeout on HTTP requests",
        content="Prevents hung sockets.",
        tags=["python", "http", "timeout"],
        confidence_score=0.9,
        maturity=MaturityLevel.VALIDATED.value,
    )
    session.add(ok_memory)
    session.commit()

    # Run #1: violation + failure → MISTAKE_MADE.
    bad_diff = "+    result = eval(user_input)\n"
    post_task_review(
        session, bad_diff,
        project_path=str(project_dir),
        agent="agent-A", model="claude",
        outcome="failure",
    )

    # Run #2: good usage + success → should NOT emit MISTAKE_MADE.
    good_diff = "+    r = requests.get(url, timeout=10)\n+    import logging\n"
    post_task_review(
        session, good_diff,
        project_path=str(project_dir),
        agent="agent-A", model="claude",
        outcome="success",
    )

    # Verify the raw events split correctly.
    made = (
        session.query(ImpactEvent)
        .filter(ImpactEvent.impact_type == ImpactType.MISTAKE_MADE.value)
        .count()
    )
    avoided = (
        session.query(ImpactEvent)
        .filter(ImpactEvent.impact_type == ImpactType.MISTAKE_AVOIDED.value)
        .count()
    )
    assert made >= 1, "Failure + violation must record MISTAKE_MADE"
    # The aggregate summary exposes the new counter.
    summary = get_impact_summary(session)
    assert summary.get("mistakes_made", 0) >= 1, (
        "get_impact_summary must surface mistakes_made separately"
    )
    # Sanity: the two counters don't collide — MISTAKE_MADE rows should not be
    # counted as avoided.
    assert made != avoided or (made > 0 and avoided == 0), (
        f"mistake_made={made} mistake_avoided={avoided} — split is wrong"
    )
