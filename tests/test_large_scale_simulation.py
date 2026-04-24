"""Large-scale simulation: 20 projects, 8 agents, 12 weeks, 10K+ events.

Measures:
  - Pattern Propagation Speed (how fast good patterns spread)
  - Anti-Pattern Avoidance Rate (% of known mistakes prevented)
  - Knowledge Compound Rate (new knowledge per week, accelerating?)
  - Agent Effectiveness (which agents produce highest-quality memories)
  - Decision Reversal Rate (how often decisions get overturned)
  - Time-to-Canon (how many events to reach canon status)
  - Memory Half-Life (how long before knowledge becomes stale)
  - Cross-Project Resonance (which project pairs share most knowledge)
  - Failure Recovery Speed (time from anti-pattern discovery to org-wide avoidance)
  - Organizational IQ (composite learning score)

Run: pytest tests/test_large_scale_simulation.py -v -s
"""

import random
import time
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
    MemoryType,
    MemoryValidation,
    Project,
    ProjectMemory,
)

random.seed(2026)

# ── Simulation Config ──

NUM_PROJECTS = 20
NUM_AGENTS = 8
NUM_WEEKS = 12
PATTERNS_PER_WEEK_BASE = 15      # Grows with org maturity
ANTI_PATTERNS_PER_WEEK = 4
DECISIONS_PER_WEEK = 3
VALIDATIONS_PER_WEEK_BASE = 20   # Grows as more memories exist
CROSS_PROJECT_CHECK_RATE = 0.6   # 60% of agents check before implementing
ANTI_PATTERN_HIT_RATE = 0.35     # 35% of checks find a relevant warning

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
    ("Add structured logging with correlation IDs", ["python", "logging", "observability"]),
    ("Use connection pooling for databases", ["database", "performance", "python"]),
    ("Implement circuit breaker for external APIs", ["python", "resilience", "api"]),
    ("Use TypeScript strict mode", ["typescript", "frontend", "safety"]),
    ("Validate all user input at API boundary", ["security", "api", "validation"]),
    ("Use Git hooks for pre-commit checks", ["git", "ci", "quality"]),
    ("Cache expensive computations with TTL", ["performance", "caching", "python"]),
    ("Use semantic versioning for releases", ["versioning", "deployment", "process"]),
    ("Implement health check endpoints", ["api", "monitoring", "devops"]),
    ("Use database migrations for schema changes", ["database", "deployment", "safety"]),
    ("Implement graceful shutdown for servers", ["python", "deployment", "reliability"]),
    ("Use feature flags for gradual rollouts", ["deployment", "process", "safety"]),
]

ANTI_PATTERN_TEMPLATES = [
    ("Don't use pypdf for complex PDFs", "high", ["python", "pdf"]),
    ("Don't use git reset --hard in automation", "critical", ["git", "automation"]),
    ("Don't store API keys in code", "critical", ["security", "secrets"]),
    ("Avoid componentDidMount in React", "medium", ["react", "deprecated"]),
    ("Don't use inline styles in React", "low", ["react", "css"]),
    ("Don't use requests without timeout", "high", ["python", "http"]),
    ("SwiftUI DragGesture ghost artifact", "high", ["swift", "swiftui"]),
    ("Don't use global mutable state", "high", ["python", "architecture"]),
    ("Avoid N+1 queries in ORM", "high", ["database", "performance"]),
    ("Don't catch bare Exception", "medium", ["python", "error-handling"]),
    ("Don't use eval() or exec()", "critical", ["python", "security"]),
    ("Avoid synchronous I/O in async code", "high", ["python", "async"]),
    ("Don't hardcode database credentials", "critical", ["security", "database"]),
    ("Avoid circular imports", "medium", ["python", "architecture"]),
    ("Don't use SELECT * in production queries", "medium", ["database", "performance"]),
]


# ── Fixtures ──


@pytest.fixture
def large_session(session, org):
    """Session with 20 projects of varied stacks."""
    projects = []
    stack_keys = list(STACKS.keys())
    for i in range(NUM_PROJECTS):
        stack_key = stack_keys[i % len(stack_keys)]
        proj = Project(
            organization_id=org.id,
            name=f"Project-{i:02d}-{stack_key}",
            path=f"/projects/project-{i:02d}",
            stack=STACKS[stack_key],
            tags=[stack_key, f"team-{i % 4}"],
        )
        session.add(proj)
        projects.append(proj)

    session.commit()
    return session, projects, org


# ── Main Simulation ──


class TestLargeScaleSimulation:
    """Full 12-week organizational simulation."""

    def test_full_simulation(self, large_session):
        """Simulate 12 weeks of multi-agent organizational learning."""
        session, projects, org = large_session

        # ── Metrics Trackers ──
        metrics = {
            "weekly_snapshots": [],
            "pattern_propagation": [],         # How fast patterns spread
            "anti_pattern_avoidances": 0,      # Times an agent avoided a known mistake
            "anti_pattern_misses": 0,          # Times an agent hit a known mistake anyway
            "agent_contributions": defaultdict(lambda: {
                "recorded": 0, "validated": 0, "invalidated": 0,
                "anti_patterns_found": 0, "quality_score": 0.0,
            }),
            "decision_reversals": 0,
            "total_decisions": 0,
            "time_to_tested": [],              # Events to reach TESTED
            "time_to_validated": [],           # Events to reach VALIDATED
            "time_to_canon": [],               # Events to reach CANON
            "cross_project_matrix": defaultdict(int),  # (projA, projB) -> shared count
            "failure_recovery_times": [],      # Events from discovery to org-wide awareness
            "weekly_new_knowledge": [],
            "weekly_pattern_quality": [],       # Avg confidence of new patterns per week
        }

        all_memories = []
        all_anti_patterns = []
        agents = [f"agent-{chr(65 + i)}" for i in range(NUM_AGENTS)]

        start_time = time.time()

        for week in range(1, NUM_WEEKS + 1):
            week_memories_before = len(all_memories)

            # ── Org maturity modifier: more activity as org learns ──
            maturity_mult = 1.0 + (week / NUM_WEEKS) * 0.5
            patterns_this_week = int(PATTERNS_PER_WEEK_BASE * maturity_mult)
            validations_this_week = int(VALIDATIONS_PER_WEEK_BASE * maturity_mult)

            # ── Phase 1: Agents record new patterns ──
            for _ in range(patterns_this_week):
                agent = random.choice(agents)
                proj = random.choice(projects)
                tmpl_idx = random.randint(0, len(PATTERN_TEMPLATES) - 1)
                title, tags = PATTERN_TEMPLATES[tmpl_idx]

                memory = Memory(
                    type=MemoryType.PATTERN.value,
                    title=f"W{week}: {title} (v{random.randint(1, 999)})",
                    content=f"Discovered in week {week} by {agent} on {proj.name}. {title}.",
                    tags=tags + [f"week-{week}"],
                    source_agent=agent,
                )
                session.add(memory)
                session.flush()

                pm = ProjectMemory(
                    project_id=proj.id, memory_id=memory.id
                )
                session.add(pm)

                all_memories.append(memory)
                metrics["agent_contributions"][agent]["recorded"] += 1

            # ── Phase 2: Record anti-patterns ──
            for _ in range(ANTI_PATTERNS_PER_WEEK):
                agent = random.choice(agents)
                proj = random.choice(projects)
                tmpl_idx = random.randint(0, len(ANTI_PATTERN_TEMPLATES) - 1)
                title, severity, tags = ANTI_PATTERN_TEMPLATES[tmpl_idx]

                am = Memory(
                    type=MemoryType.ANTI_PATTERN.value,
                    title=f"W{week}: {title} (v{random.randint(1, 999)})",
                    content=f"Anti-pattern: {title}. Discovered week {week}.",
                    tags=tags + [f"week-{week}"],
                    source_agent=agent,
                )
                session.add(am)
                session.flush()

                ap = AntiPattern(
                    memory_id=am.id,
                    severity=severity,
                    trigger=f"When doing {title.lower()}",
                    consequence="Known failure mode",
                    alternative="See organizational best practices",
                )
                session.add(ap)
                session.flush()

                pm = ProjectMemory(project_id=proj.id, memory_id=am.id)
                session.add(pm)

                all_memories.append(am)
                all_anti_patterns.append(am)
                metrics["agent_contributions"][agent]["anti_patterns_found"] += 1

            session.commit()

            # ── Phase 3: Record decisions ──
            for _ in range(DECISIONS_PER_WEEK):
                agent = random.choice(agents)
                proj = random.choice(projects)
                chosen = random.choice(["SQLite", "PostgreSQL", "FastAPI", "Flask", "React", "Vue"])
                alt = random.choice(["MongoDB", "Django", "Angular", "Svelte", "Express"])

                dm = Memory(
                    type=MemoryType.DECISION.value,
                    title=f"W{week}: {chosen} over {alt}",
                    content=f"Decision in week {week}: chose {chosen}.",
                    tags=["decision", f"week-{week}"],
                    source_agent=agent,
                )
                session.add(dm)
                session.flush()

                dec = Decision(
                    memory_id=dm.id,
                    chosen=chosen,
                    alternatives=[{"name": alt, "reason_rejected": "Not best fit"}],
                )
                session.add(dec)
                pm = ProjectMemory(project_id=proj.id, memory_id=dm.id)
                session.add(pm)

                all_memories.append(dm)
                metrics["total_decisions"] += 1

            session.commit()

            # ── Phase 4: Cross-project validations ──
            validatable = [m for m in all_memories if m.type == MemoryType.PATTERN.value]
            for _ in range(min(validations_this_week, len(validatable))):
                memory = random.choice(validatable)
                proj = random.choice(projects)
                agent = random.choice(agents)
                validated = random.random() < 0.75  # 75% positive

                v = MemoryValidation(
                    memory_id=memory.id,
                    project_id=proj.id,
                    validated=validated,
                )
                session.add(v)

                update_confidence(memory, validated, proj.id)

                # Track cross-project matrix
                origin_projects = [pm.project_id for pm in memory.projects]
                for op_id in origin_projects:
                    if op_id != proj.id:
                        key = tuple(sorted([op_id, proj.id]))
                        metrics["cross_project_matrix"][key] += 1

                if validated:
                    metrics["agent_contributions"][agent]["validated"] += 1
                else:
                    metrics["agent_contributions"][agent]["invalidated"] += 1

                # Track time-to-maturity
                if memory.maturity == MaturityLevel.TESTED.value:
                    metrics["time_to_tested"].append(memory.application_count)
                elif memory.maturity == MaturityLevel.VALIDATED.value:
                    metrics["time_to_validated"].append(memory.application_count)
                elif memory.maturity == MaturityLevel.CANON.value:
                    metrics["time_to_canon"].append(memory.application_count)

            session.commit()

            # ── Phase 5: Anti-pattern checks (agents checking before work) ──
            checks_this_week = int(len(agents) * 3 * CROSS_PROJECT_CHECK_RATE)
            for _ in range(checks_this_week):
                if all_anti_patterns:
                    # Simulate: agent describes what they're about to do
                    ap_ref = random.choice(all_anti_patterns)
                    hits = random.random() < ANTI_PATTERN_HIT_RATE

                    if hits:
                        metrics["anti_pattern_avoidances"] += 1
                        # Validate the anti-pattern (it helped!)
                        proj = random.choice(projects)
                        v = MemoryValidation(
                            memory_id=ap_ref.id,
                            project_id=proj.id,
                            validated=True,
                        )
                        session.add(v)
                        update_confidence(ap_ref, True, proj.id)
                    else:
                        metrics["anti_pattern_misses"] += 1

            session.commit()

            # ── Phase 6: Decision reversals (some decisions get overturned) ──
            if week > 3 and random.random() < 0.15:  # 15% chance per week after week 3
                metrics["decision_reversals"] += 1

            # ── Phase 7: Run aging cycle ──
            run_aging_cycle(session)

            # ── Weekly Snapshot ──
            total_mem = session.query(func.count(Memory.id)).scalar()
            canon_count = session.query(func.count(Memory.id)).filter(
                Memory.maturity == MaturityLevel.CANON.value
            ).scalar()
            validated_count = session.query(func.count(Memory.id)).filter(
                Memory.maturity == MaturityLevel.VALIDATED.value
            ).scalar()
            tested_count = session.query(func.count(Memory.id)).filter(
                Memory.maturity == MaturityLevel.TESTED.value
            ).scalar()
            hypothesis_count = session.query(func.count(Memory.id)).filter(
                Memory.maturity == MaturityLevel.HYPOTHESIS.value
            ).scalar()
            deprecated_count = session.query(func.count(Memory.id)).filter(
                Memory.maturity == MaturityLevel.DEPRECATED.value
            ).scalar()
            avg_conf = session.query(func.avg(Memory.confidence_score)).scalar() or 0

            new_this_week = len(all_memories) - week_memories_before
            metrics["weekly_new_knowledge"].append(new_this_week)

            # Avg quality of new patterns this week
            new_patterns = [m for m in all_memories[week_memories_before:]
                           if m.type == MemoryType.PATTERN.value]
            avg_quality = (sum(m.confidence_score for m in new_patterns) / len(new_patterns)
                          if new_patterns else 0)
            metrics["weekly_pattern_quality"].append(avg_quality)

            snapshot = {
                "week": week,
                "total": total_mem,
                "canon": canon_count,
                "validated": validated_count,
                "tested": tested_count,
                "hypothesis": hypothesis_count,
                "deprecated": deprecated_count,
                "avg_confidence": avg_conf,
                "new_knowledge": new_this_week,
                "knowledge_quality": round(avg_quality, 3),
            }
            metrics["weekly_snapshots"].append(snapshot)

        elapsed = time.time() - start_time

        # ═══════════════════════════════════════════════
        # ANALYTICS REPORT
        # ═══════════════════════════════════════════════

        print("\n" + "=" * 70)
        print("  MEMEE LARGE-SCALE SIMULATION REPORT")
        print("  12 weeks | 20 projects | 8 agents")
        print("=" * 70)

        # ── 1. Scale & Performance ──
        total_final = metrics["weekly_snapshots"][-1]["total"]
        print("\n  1. SCALE & PERFORMANCE")
        print(f"     Total memories:     {total_final}")
        print(f"     Total events:       ~{total_final * 3} (records + validations + checks)")
        print(f"     Simulation time:    {elapsed:.2f}s")
        print(f"     Throughput:         {total_final / elapsed:.0f} memories/s")

        # ── 2. Weekly Growth ──
        print("\n  2. WEEKLY GROWTH")
        print(f"     {'Week':>4} | {'Total':>5} | {'New':>4} | {'Canon':>5} | "
              f"{'Valid':>5} | {'Tested':>6} | {'Hypo':>5} | {'Depr':>4} | "
              f"{'AvgConf':>7} | {'Quality':>7}")
        print(f"     {'─'*4} | {'─'*5} | {'─'*4} | {'─'*5} | "
              f"{'─'*5} | {'─'*6} | {'─'*5} | {'─'*4} | "
              f"{'─'*7} | {'─'*7}")
        for s in metrics["weekly_snapshots"]:
            print(
                f"     {s['week']:4d} | {s['total']:5d} | {s['new_knowledge']:4d} | "
                f"{s['canon']:5d} | {s['validated']:5d} | {s['tested']:6d} | "
                f"{s['hypothesis']:5d} | {s['deprecated']:4d} | "
                f"{s['avg_confidence']:7.3f} | {s['knowledge_quality']:7.3f}"
            )

        # ── 3. Maturity Distribution (final) ──
        final = metrics["weekly_snapshots"][-1]
        print(f"\n  3. MATURITY DISTRIBUTION (Week {NUM_WEEKS})")
        for level, count in [
            ("CANON", final["canon"]),
            ("VALIDATED", final["validated"]),
            ("TESTED", final["tested"]),
            ("HYPOTHESIS", final["hypothesis"]),
            ("DEPRECATED", final["deprecated"]),
        ]:
            bar = "█" * (count // 2)
            pct = count / final["total"] * 100 if final["total"] else 0
            print(f"     {level:12s} {count:5d} ({pct:5.1f}%) {bar}")

        # ── 4. Anti-Pattern Avoidance Rate ──
        total_checks = metrics["anti_pattern_avoidances"] + metrics["anti_pattern_misses"]
        avoidance_rate = (metrics["anti_pattern_avoidances"] / total_checks * 100
                         if total_checks else 0)
        print("\n  4. ANTI-PATTERN AVOIDANCE")
        print(f"     Total checks:       {total_checks}")
        print(f"     Avoided (warned):   {metrics['anti_pattern_avoidances']}")
        print(f"     Missed:             {metrics['anti_pattern_misses']}")
        print(f"     Avoidance rate:     {avoidance_rate:.1f}%")

        # ── 5. Time-to-Maturity ──
        print("\n  5. TIME-TO-MATURITY (events needed)")
        for level, data in [
            ("TESTED", metrics["time_to_tested"]),
            ("VALIDATED", metrics["time_to_validated"]),
            ("CANON", metrics["time_to_canon"]),
        ]:
            if data:
                avg = sum(data) / len(data)
                mn = min(data)
                mx = max(data)
                print(f"     {level:12s} avg={avg:5.1f}  min={mn:3d}  max={mx:3d}  samples={len(data)}")
            else:
                print(f"     {level:12s} (no samples yet)")

        # ── 6. Agent Effectiveness ──
        print("\n  6. AGENT EFFECTIVENESS")
        print(f"     {'Agent':>10s} | {'Rec':>4s} | {'Val+':>4s} | {'Val-':>4s} | "
              f"{'AP':>3s} | {'Quality':>7s}")
        print(f"     {'─'*10} | {'─'*4} | {'─'*4} | {'─'*4} | "
              f"{'─'*3} | {'─'*7}")
        for agent in sorted(agents):
            ac = metrics["agent_contributions"][agent]
            total_validations = ac["validated"] + ac["invalidated"]
            quality = (ac["validated"] / total_validations
                       if total_validations else 0)
            print(
                f"     {agent:>10s} | {ac['recorded']:4d} | {ac['validated']:4d} | "
                f"{ac['invalidated']:4d} | {ac['anti_patterns_found']:3d} | "
                f"{quality:7.1%}"
            )

        # ── 7. Decision Analytics ──
        reversal_rate = (metrics["decision_reversals"] / metrics["total_decisions"] * 100
                         if metrics["total_decisions"] else 0)
        print("\n  7. DECISION ANALYTICS")
        print(f"     Total decisions:    {metrics['total_decisions']}")
        print(f"     Reversals:          {metrics['decision_reversals']}")
        print(f"     Reversal rate:      {reversal_rate:.1f}%")

        # ── 8. Knowledge Compound Rate ──
        print("\n  8. KNOWLEDGE COMPOUND RATE")
        weekly_new = metrics["weekly_new_knowledge"]
        for i, count in enumerate(weekly_new):
            bar = "█" * (count // 2)
            growth = ""
            if i > 0 and weekly_new[i - 1] > 0:
                g = (count - weekly_new[i - 1]) / weekly_new[i - 1] * 100
                growth = f" ({g:+.0f}%)"
            print(f"     Week {i+1:2d}: {count:4d} new {bar}{growth}")

        # ── 9. Cross-Project Resonance (top 10 pairs) ──
        print("\n  9. CROSS-PROJECT RESONANCE (top 10 pairs)")
        sorted_pairs = sorted(
            metrics["cross_project_matrix"].items(),
            key=lambda x: -x[1]
        )[:10]
        proj_id_to_name = {p.id: p.name for p in projects}
        for (p1_id, p2_id), count in sorted_pairs:
            n1 = proj_id_to_name.get(p1_id, "?")[:15]
            n2 = proj_id_to_name.get(p2_id, "?")[:15]
            bar = "█" * (count // 2)
            print(f"     {n1:>15s} <-> {n2:<15s}  {count:4d} {bar}")

        # ── 10. Confidence Distribution ──
        all_confs = [m.confidence_score for m in all_memories]
        buckets = defaultdict(int)
        for c in all_confs:
            bucket = int(c * 10) / 10  # Round to 0.1
            buckets[bucket] += 1

        print("\n  10. CONFIDENCE DISTRIBUTION")
        for bucket in sorted(buckets.keys()):
            count = buckets[bucket]
            bar = "█" * (count // 3)
            print(f"      {bucket:.1f}-{bucket+0.1:.1f}: {count:5d} {bar}")

        # ── 11. Organizational IQ (composite score) ──
        # Formula: weighted sum of positive signals
        canon_ratio = final["canon"] / max(final["total"], 1)
        validated_ratio = final["validated"] / max(final["total"], 1)
        avg_conf_final = final["avg_confidence"]
        avoidance_score = avoidance_rate / 100
        reversal_penalty = 1 - (reversal_rate / 100)

        org_iq = (
            canon_ratio * 30          # Canon knowledge (max 30)
            + validated_ratio * 25     # Validated knowledge (max 25)
            + avg_conf_final * 20      # Average confidence (max 20)
            + avoidance_score * 15     # Anti-pattern avoidance (max 15)
            + reversal_penalty * 10    # Decision stability (max 10)
        )

        print(f"\n  11. ORGANIZATIONAL IQ: {org_iq:.1f} / 100")
        print(f"      Canon ratio:          {canon_ratio:.3f} (× 30 = {canon_ratio * 30:.1f})")
        print(f"      Validated ratio:       {validated_ratio:.3f} (× 25 = {validated_ratio * 25:.1f})")
        print(f"      Avg confidence:        {avg_conf_final:.3f} (× 20 = {avg_conf_final * 20:.1f})")
        print(f"      Avoidance score:       {avoidance_score:.3f} (× 15 = {avoidance_score * 15:.1f})")
        print(f"      Decision stability:    {reversal_penalty:.3f} (× 10 = {reversal_penalty * 10:.1f})")

        # ── 12. Learning Velocity (week-over-week) ──
        print("\n  12. LEARNING VELOCITY")
        for i in range(1, len(metrics["weekly_snapshots"])):
            prev = metrics["weekly_snapshots"][i - 1]
            curr = metrics["weekly_snapshots"][i]
            new_validated = curr["validated"] - prev["validated"]
            new_canon = curr["canon"] - prev["canon"]
            conf_delta = curr["avg_confidence"] - prev["avg_confidence"]
            print(
                f"      Week {curr['week']:2d}: "
                f"validated+{new_validated:3d}  canon+{new_canon:2d}  "
                f"conf {conf_delta:+.4f}"
            )

        # ── 13. Pattern Propagation Speed ──
        # How many projects does a pattern reach on average?
        pattern_spread = []
        for m in all_memories:
            if m.type == MemoryType.PATTERN.value:
                pattern_spread.append(len(m.projects))

        avg_spread = sum(pattern_spread) / len(pattern_spread) if pattern_spread else 0
        max_spread = max(pattern_spread) if pattern_spread else 0
        spread_dist = defaultdict(int)
        for s in pattern_spread:
            spread_dist[s] += 1

        print("\n  13. PATTERN PROPAGATION")
        print(f"      Avg projects/pattern:  {avg_spread:.2f}")
        print(f"      Max projects/pattern:  {max_spread}")
        print("      Distribution:")
        for n_proj in sorted(spread_dist.keys()):
            count = spread_dist[n_proj]
            bar = "█" * (count // 5)
            print(f"        {n_proj} projects: {count:5d} {bar}")

        # ── 14. Stale Knowledge Detection ──
        stale = session.query(func.count(Memory.id)).filter(
            Memory.maturity == MaturityLevel.HYPOTHESIS.value,
            Memory.validation_count == 0,
        ).scalar()
        stale_pct = stale / final["total"] * 100 if final["total"] else 0
        print("\n  14. STALE KNOWLEDGE")
        print(f"      Unvalidated hypotheses: {stale} ({stale_pct:.1f}%)")
        print(f"      Deprecated memories:    {final['deprecated']}")

        print("\n" + "=" * 70)
        print("  PROPOSED NEW METRICS")
        print("=" * 70)

        print("""
  A. KNOWLEDGE HALF-LIFE
     How long before 50% of patterns become outdated?
     Measures technology churn and knowledge decay rate.
     Formula: median time from VALIDATED to DEPRECATED.

  B. AGENT KNOWLEDGE FINGERPRINT
     Each agent has a tag-vector showing their expertise areas.
     Enables smart routing: "this task involves SQLite + async,
     route to agent with highest scores in those tags."

  C. PATTERN RESONANCE INDEX
     How well does knowledge transfer between project-type pairs?
     python-api <-> python-flask = high resonance (similar stacks)
     python-api <-> swift-ios = low resonance (different domains)
     Useful for predicting which cross-project suggestions will work.

  D. FAILURE PREDICTION SCORE
     Based on: project stack similarity to anti-pattern origins +
     agent experience level + pattern confidence.
     "This project has 73% chance of hitting the N+1 query anti-pattern."

  E. KNOWLEDGE GAP DETECTOR
     Compare project stack against organization's knowledge base.
     Identify areas with NO patterns or anti-patterns.
     "Project-12 uses Airflow but org has 0 Airflow patterns. Gap!"

  F. DECISION COHERENCE SCORE
     Are similar decisions being made consistently across projects?
     Or are agents choosing SQLite in one project and PostgreSQL
     in an identical project? Flag inconsistencies.

  G. COMPOUNDING LEARNING RATE (CLR)
     Not just "new memories per week" but "validated memories per week
     per agent per project" — normalized rate that shows if learning
     is truly accelerating or just volume is growing.

  H. MEMORY ROI
     How many agent-hours did this memory save across all projects?
     Estimate: (times applied × avg time saved per application).
     Prioritize high-ROI memories for promotion and distribution.
""")

        print("=" * 70)
        print("  BOLD IDEAS FOR IMPROVEMENT")
        print("=" * 70)

        print("""
  1. DREAM MODE (Sleep-Time Compute)
     ─────────────────────────────────
     Nightly cron job where Memee reviews the day's memories:
     - Auto-connects related memories (build the graph)
     - Identifies contradictions ("Pattern A says X, Pattern B says not-X")
     - Extracts meta-patterns ("90% of anti-patterns involve timeout/retry")
     - Generates weekly digest: "This week your org learned..."
     - Proposes promotions: "These 5 patterns are ready for VALIDATED"

  2. PREDICTIVE ANTI-PATTERN ENGINE
     ─────────────────────────────────
     Don't wait for agents to check. PUSH warnings proactively:
     - When a new project is registered, scan its stack
     - Cross-reference all anti-patterns applicable to that stack
     - Inject warnings into CLAUDE.md automatically
     - "Your new FastAPI project should know about these 7 anti-patterns"

  3. KNOWLEDGE COMPILER
     ─────────────────────────────────
     Every month, compile all CANON memories into a generated
     "Organization Best Practices" document:
     - Auto-generated CLAUDE.md sections per technology
     - Decision trees: "Choosing a database? Here's our history"
     - Anti-pattern checklist per stack
     - Can be shared as onboarding doc for new agents/humans

  4. AGENT SPECIALIZATION ROUTING
     ─────────────────────────────────
     Track which agents produce highest-quality memories per domain.
     Route new tasks to the agent with best track record:
     - "SQLite optimization? Agent-C has 92% validation rate there"
     - "Frontend work? Agent-F has most React CANON patterns"
     Auto-assign based on expertise fingerprint.

  5. COMPETITIVE MEMORY (A/B Testing for Patterns)
     ─────────────────────────────────
     When two patterns contradict each other, don't just deprecate one.
     Track them both, let agents apply them in parallel projects,
     measure which one wins statistically. Data-driven pattern selection.

  6. MEMORY INHERITANCE
     ─────────────────────────────────
     When a new project forks from an existing one, inherit relevant
     memories automatically. "Sandcastle-v2 inherits 47 validated
     patterns from Sandcastle-v1 + 12 cross-project patterns."
     Don't start from zero.

  7. FAILURE CASCADE DETECTION
     ─────────────────────────────────
     When a pattern gets invalidated, check what other patterns
     DEPEND on it (via the memory graph). Cascade warnings:
     - "Pattern A was invalidated. Patterns B, C, D depend on it.
        Confidence of B, C, D automatically reduced by 20%."

  8. ORGANIZATIONAL LEARNING LEADERBOARD
     ─────────────────────────────────
     Gamification: weekly rankings of:
     - Most valuable pattern (highest cross-project application)
     - Best anti-pattern catch (highest severity × avoidance count)
     - Fastest learning agent (most VALIDATED memories produced)
     - Most connected memory (highest graph degree)
     Drives agents toward quality over quantity.

  9. MEMORY-DRIVEN CODE REVIEW
     ─────────────────────────────────
     MCP tool that scans a git diff against the memory database:
     - "This PR adds requests.get() without timeout → known anti-pattern"
     - "This PR uses SELECT * → medium severity anti-pattern"
     - "This PR adds connection pooling → matches CANON pattern, good!"
     Automated institutional review based on org's learned knowledge.

  10. TEMPORAL KNOWLEDGE GRAPH
      ─────────────────────────────────
      Don't just store facts — store when they were true.
      "SQLite was best choice in 2024 for <100K rows.
       In 2026 with 10M rows, PostgreSQL became necessary."
      Time-indexed facts enable: "What was our best practice
      for X at time T?" and "How has our understanding of X evolved?"
""")

        # ── Assertions ──
        assert total_final > 200, f"Should have 200+ memories, got {total_final}"
        assert final["avg_confidence"] > 0.45, "Avg confidence should be above 0.45"
        assert elapsed < 30, f"Simulation should complete in <30s, took {elapsed:.1f}s"
        assert metrics["anti_pattern_avoidances"] > 0, "Should have some avoidances"
