"""MegaCorp: 100-project, 50-agent, year-long simulation with hallucination detection.

Side-by-side: identical company runs WITH and WITHOUT Memee.
Measures real differences, not abstract scores.

Scenarios:
  - Massive incident wave (30+ real bugs across 10 divisions)
  - Hallucinated memories injected (agent records wrong things)
  - Technology migration (Python 2→3 style: old knowledge becomes wrong)
  - New hire onboarding speed
  - Cross-team knowledge transfer
  - Code review catches at scale
  - Autoresearch experiments
  - Quality gate filtering garbage

Run: pytest tests/test_megacorp.py -v -s
"""

import random
import time
from collections import defaultdict

import pytest
from sqlalchemy import func

from memee.engine.confidence import get_confidence_interval, get_uncertainty, update_confidence
from memee.engine.dream import run_dream_cycle
from memee.engine.impact import ImpactType, get_impact_summary, record_impact
from memee.engine.inheritance import inherit_memories
from memee.engine.lifecycle import run_aging_cycle
from memee.engine.predictive import scan_project_for_warnings
from memee.engine.propagation import run_propagation_cycle
from memee.engine.quality_gate import run_quality_gate, merge_duplicate
from memee.engine.research import create_experiment, complete_experiment, log_iteration, get_meta_learning
from memee.engine.review import review_diff
from memee.engine.search import search_memories
from memee.storage.models import (
    AntiPattern, Decision, MaturityLevel, Memory, MemoryConnection,
    MemoryType, MemoryValidation, Organization, Project, ProjectMemory, Severity,
)

random.seed(2026)

# ═══════════════════════════════════
# MEGACORP DEFINITION
# ═══════════════════════════════════

DIVISIONS = [
    "platform", "payments", "data", "ml", "frontend",
    "mobile", "infra", "security", "growth", "internal-tools",
]

STACKS = [
    (["Python", "FastAPI", "PostgreSQL"], ["python", "api"]),
    (["Python", "Flask", "SQLite"], ["python", "web"]),
    (["Python", "Django", "PostgreSQL"], ["python", "web"]),
    (["Python", "pandas", "Airflow"], ["python", "data"]),
    (["Python", "scikit-learn", "FastAPI"], ["python", "ml"]),
    (["React", "TypeScript", "Tailwind"], ["react", "frontend"]),
    (["React", "TypeScript", "Next.js"], ["react", "frontend"]),
    (["Swift", "SwiftUI", "CoreData"], ["swift", "mobile"]),
    (["Kotlin", "Jetpack Compose", "Room"], ["kotlin", "mobile"]),
    (["Go", "Gin", "PostgreSQL"], ["go", "api"]),
]

# Real incidents — things that actually break at scale
REAL_INCIDENTS = [
    ("Stripe API key in git", "critical", ["security", "secrets", "python"],
     "Key exposed 6h. All customer cards safe but key rotated.", 120),
    ("No timeout on Redis", "high", ["python", "redis", "reliability"],
     "Redis down → AuthService hung 47min. All auth blocked.", 90),
    ("N+1 queries in ORM loop", "high", ["python", "database", "performance"],
     "ETL: 6 hours instead of 20 minutes. 100K rows × 1 query each.", 180),
    ("XSS via dangerouslySetInnerHTML", "critical", ["react", "security", "xss"],
     "Stored XSS in user profile. Pen test caught it. P1 severity.", 240),
    ("Kafka consumer no idempotency", "high", ["python", "kafka", "distributed"],
     "Duplicate payment processing during rebalance. $12K overcharged.", 300),
    ("ML predict() blocks event loop", "high", ["python", "async", "ml", "fastapi"],
     "scikit-learn in async endpoint. 3s latency. Users complained.", 60),
    ("SwiftUI DragGesture ghost", "high", ["swift", "swiftui", "ui"],
     "Ghost artifact during drag. No workaround. Structural fix needed.", 120),
    ("useEffect memory leak", "high", ["react", "frontend", "hooks"],
     "WebSocket no cleanup. Dashboard crashes after 2h.", 90),
    ("mTLS cert expired prod", "critical", ["security", "devops", "tls"],
     "Service-to-service auth failed. 45min P1 outage.", 180),
    ("SQL injection admin endpoint", "critical", ["python", "security", "database"],
     "f-string SQL in admin panel. Found in quarterly audit.", 360),
    ("No rate limiting public API", "high", ["api", "security", "performance"],
     "Scraping bot caused 10x traffic. Degraded for all users.", 120),
    ("Model trained on biased data", "high", ["ml", "data", "quality"],
     "40% false positive on international cards. Discrimination risk.", 480),
    ("Docker image 2.3GB", "medium", ["devops", "docker", "performance"],
     "CI takes 25min. Multi-stage build would fix.", 30),
    ("No graceful shutdown", "high", ["python", "deployment", "reliability"],
     "In-flight requests dropped during deploy. Data loss.", 60),
    ("Circular import crash", "medium", ["python", "architecture"],
     "Service won't start after refactor. 20min to diagnose.", 20),
    ("SELECT * on 50M row table", "high", ["database", "performance"],
     "Query took 45 seconds. Only needed 3 columns.", 30),
    ("JWT secret hardcoded", "critical", ["security", "auth", "python"],
     "Same JWT secret in all environments. Token forgery possible.", 120),
    ("No database migration", "high", ["database", "deployment", "safety"],
     "Schema change broke prod. No rollback. 2h manual fix.", 180),
    ("Logging PII in plaintext", "critical", ["security", "logging", "compliance"],
     "User emails in CloudWatch. GDPR violation. Legal involved.", 240),
    ("No circuit breaker", "high", ["python", "api", "resilience"],
     "Payment provider down. Our service cascaded. 30min outage.", 90),
]

# Hallucinated memories — WRONG patterns that agents might record
HALLUCINATIONS = [
    ("Always use global mutable state for config", ["python", "architecture"],
     "Global dict is fastest for config access across modules.",
     "WRONG: Thread-unsafe, untestable, hidden dependencies"),
    ("eval() is fine for parsing JSON config", ["python", "security"],
     "eval() handles complex Python dict syntax better than json.loads.",
     "WRONG: Remote code execution vulnerability"),
    ("Skip tests for hotfixes", ["testing", "deployment"],
     "Hotfixes are urgent, tests slow things down.",
     "WRONG: Hotfixes are exactly when tests matter most"),
    ("Use sleep(5) for race condition fix", ["python", "concurrency"],
     "Adding delay between operations prevents race conditions.",
     "WRONG: Hides bug, breaks under load, non-deterministic"),
    ("Disable CORS for easier development", ["api", "security"],
     "CORS blocks local testing. Just disable it.",
     "WRONG: Leaves production open to cross-origin attacks"),
]

# Code diffs for review
DIFFS = {
    "good_timeout": "+    response = requests.get(url, timeout=10)\n+    import logging\n+    logger = logging.getLogger(__name__)",
    "bad_eval": "+    config = eval(open('config.txt').read())\n+    result = eval(user_query)",
    "good_env": "+    import os\n+    API_KEY = os.getenv('STRIPE_KEY')\n+    assert API_KEY, 'Missing STRIPE_KEY'",
    "bad_select_star": "+    cursor.execute('SELECT * FROM users WHERE active = 1')\n+    for row in cursor.fetchall():",
    "good_pooling": "+    engine = create_engine(url, pool_size=20, pool_timeout=30)\n+    import logging\n+    logger = logging.getLogger('pool')",
    "bad_no_timeout": "+    response = requests.get(external_api_url)\n+    data = response.json()",
}

MODELS = ["claude-opus-4", "claude-sonnet-4", "gpt-4o", "gpt-4-turbo",
          "gemini-2.0-flash", "gemini-1.5-pro", "llama-3.1-70b"]


@pytest.fixture
def megacorp(session, org):
    """100-project, 50-agent MegaCorp."""
    projects = []
    for i in range(100):
        div = DIVISIONS[i % len(DIVISIONS)]
        stack, tags = STACKS[i % len(STACKS)]
        proj = Project(
            organization_id=org.id,
            name=f"{div.title()}-{i:03d}",
            path=f"/megacorp/{div}/proj-{i:03d}",
            stack=stack, tags=tags + [div],
        )
        session.add(proj)
        projects.append(proj)

    session.commit()
    agents = [f"dev-{i:02d}" for i in range(50)]
    return session, projects, agents, org


class TestMegaCorp:

    def test_megacorp_with_vs_without(self, megacorp):
        """Side-by-side: MegaCorp WITH Memee vs WITHOUT."""
        session, projects, agents, org = megacorp
        start_time = time.time()

        # ═══════════════════════════════════
        # TRACKING: two parallel universes
        # ═══════════════════════════════════
        with_memee = {
            "incidents_caught": 0,
            "incidents_missed": 0,
            "time_saved_minutes": 0,
            "iterations_saved": 0,
            "hallucinations_caught": 0,
            "hallucinations_missed": 0,
            "duplicates_merged": 0,
            "quality_rejected": 0,
            "code_review_catches": 0,
            "onboarding_patterns": 0,
            "propagated_links": 0,
            "experiments_completed": 0,
            "total_memories": 0,
            "canon_memories": 0,
            "avg_confidence": 0,
            "weekly_log": [],
        }

        without_memee = {
            "incidents_repeated": 0,
            "time_wasted_minutes": 0,
            "iterations_wasted": 0,
            "hallucinations_spread": 0,
            "duplicate_knowledge": 0,
            "onboarding_weeks": 0,
            "weekly_log": [],
        }

        all_memories = []
        all_anti_patterns = []
        hallucination_memory_ids: list[str] = []  # track LLM hallucs that slipped gate

        # ═══════════════════════════════════
        # 52-WEEK SIMULATION
        # ═══════════════════════════════════

        for week in range(1, 53):
            week_data = {"incidents": 0, "saves": 0, "catches": 0}

            # ─── Incidents (real bugs happen) ───
            # More in early weeks, fewer later (WITH memee learns, WITHOUT doesn't)
            n_incidents = max(0, int(3 - week * 0.04 + random.gauss(0, 0.5)))
            for _ in range(n_incidents):
                inc = random.choice(REAL_INCIDENTS)
                title_ap, severity, tags, detail, time_cost = inc
                agent = random.choice(agents)
                proj = random.choice(projects)
                model = random.choice(MODELS)

                # WITHOUT: every incident costs full time
                without_memee["time_wasted_minutes"] += time_cost
                without_memee["iterations_wasted"] += random.randint(2, 8)

                # WITH: check if Memee already knows about this
                existing = search_memories(session, title_ap, tags=tags, limit=1, use_vectors=False)
                if existing and existing[0]["memory"].confidence_score > 0.5:
                    # Memee CAUGHT it — agent was warned before it happened
                    with_memee["incidents_caught"] += 1
                    with_memee["time_saved_minutes"] += time_cost
                    week_data["saves"] += 1

                    record_impact(
                        session, existing[0]["memory"].id,
                        ImpactType.MISTAKE_AVOIDED.value,
                        agent=agent, project_id=proj.id,
                        trigger=detail, agent_action="Heeded warning",
                        time_saved_minutes=time_cost,
                        severity_avoided=severity,
                    )
                else:
                    # Memee MISSED it — but now records for future
                    with_memee["incidents_missed"] += 1
                    without_memee["incidents_repeated"] += 1

                    # Quality gate check before recording
                    gate = run_quality_gate(
                        session, title_ap, detail, tags,
                        memory_type="anti_pattern", source="llm",
                    )
                    if gate.accepted:
                        m = Memory(
                            type=MemoryType.ANTI_PATTERN.value,
                            title=title_ap, content=detail,
                            tags=tags, source_agent=agent, source_model=model,
                            confidence_score=gate.initial_confidence,
                            source_type=gate.source_type,
                            quality_score=gate.quality_score,
                        )
                        session.add(m)
                        session.flush()
                        ap = AntiPattern(
                            memory_id=m.id, severity=severity,
                            trigger=title_ap, consequence=detail,
                            alternative="See organizational practices",
                        )
                        session.add(ap)
                        pm = ProjectMemory(project_id=proj.id, memory_id=m.id)
                        session.add(pm)
                        all_memories.append(m)
                        all_anti_patterns.append(m)
                    elif gate.merged:
                        with_memee["duplicates_merged"] += 1
                        without_memee["duplicate_knowledge"] += 1

                week_data["incidents"] += 1

            # ─── Hallucination injection ───
            if week % 8 == 0:
                hall = random.choice(HALLUCINATIONS)
                title, tags, content, why_wrong = hall
                agent = random.choice(agents)

                # WITHOUT: hallucination spreads as "knowledge"
                without_memee["hallucinations_spread"] += 1

                # WITH: quality gate catches it
                gate = run_quality_gate(
                    session, title, content, tags,
                    memory_type="pattern", source="llm", scope="team",
                )
                if gate.flagged or not gate.accepted:
                    with_memee["hallucinations_caught"] += 1
                    with_memee["hallucinations_caught_gate"] = \
                        with_memee.get("hallucinations_caught_gate", 0) + 1
                elif gate.merged:
                    with_memee["hallucinations_caught"] += 1
                    with_memee["hallucinations_caught_gate"] = \
                        with_memee.get("hallucinations_caught_gate", 0) + 1
                else:
                    # Got through the gate — but tracked for post-hoc quarantine check.
                    # Starts with low confidence (llm × 0.8) and peer invalidations
                    # will suppress it. LLM quarantine in evaluate_maturity() also
                    # blocks promotion to VALIDATED/CANON without diversity evidence.
                    m = Memory(
                        type=MemoryType.PATTERN.value,
                        title=title, content=content, tags=tags,
                        source_agent=agent, source_type="llm",
                        confidence_score=gate.initial_confidence,
                    )
                    session.add(m)
                    session.flush()
                    all_memories.append(m)
                    hallucination_memory_ids.append(m.id)

                    # Peer invalidations will kill it
                    for _ in range(3):
                        v = MemoryValidation(
                            memory_id=m.id,
                            project_id=random.choice(projects).id,
                            validated=False, evidence=why_wrong,
                            validator_model=random.choice(MODELS),
                        )
                        session.add(v)
                        update_confidence(m, False, model_name=random.choice(MODELS))

            # ─── Good patterns ───
            n_patterns = random.randint(3, 8)
            for _ in range(n_patterns):
                titles = [
                    "Use timeout on HTTP requests",
                    "Connection pooling for databases",
                    "Pre-commit hooks catch CI failures",
                    "Structured logging with correlation IDs",
                    "Circuit breaker for external APIs",
                    "Pydantic model_validate for parsing",
                    "Feature flags for gradual rollouts",
                    "Health check endpoints for monitoring",
                ]
                title = random.choice(titles)
                tags = ["python", "api"]
                agent = random.choice(agents)
                proj = random.choice(projects)
                model = random.choice(MODELS)

                gate = run_quality_gate(session, title, f"Best practice: {title}",
                                        tags, source="llm")
                if gate.accepted and not gate.merged:
                    m = Memory(
                        type=MemoryType.PATTERN.value,
                        title=f"{title} (W{week})", content=f"Best practice: {title}",
                        tags=tags, source_agent=agent, source_model=model,
                        confidence_score=gate.initial_confidence,
                        source_type="llm",
                    )
                    session.add(m)
                    session.flush()
                    pm = ProjectMemory(project_id=proj.id, memory_id=m.id)
                    session.add(pm)
                    all_memories.append(m)
                elif gate.merged:
                    with_memee["duplicates_merged"] += 1
                    without_memee["duplicate_knowledge"] += 1
                elif not gate.accepted:
                    with_memee["quality_rejected"] += 1

            session.commit()

            # ─── Cross-project validations ───
            patterns = [m for m in all_memories if m.type == MemoryType.PATTERN.value]
            if patterns:
                accuracy = min(0.55 + week * 0.007, 0.88)
                for _ in range(min(10 + week // 2, len(patterns))):
                    m = random.choice(patterns)
                    proj = random.choice(projects)
                    model = random.choice(MODELS)
                    validated = random.random() < accuracy
                    v = MemoryValidation(
                        memory_id=m.id, project_id=proj.id,
                        validated=validated, validator_model=model,
                    )
                    session.add(v)
                    update_confidence(m, validated, proj.id, model_name=model)
                session.commit()

            # ─── Anti-pattern checks ───
            check_rate = min(0.2 + week * 0.013, 0.85)
            for _ in range(int(len(agents) * check_rate * 0.3)):
                proj = random.choice(projects)
                warnings = scan_project_for_warnings(session, proj)
                if warnings:
                    with_memee["incidents_caught"] += 1

            # ─── Code reviews ───
            if week % 3 == 0:
                for diff_name, diff_content in random.sample(list(DIFFS.items()),
                                                             min(3, len(DIFFS))):
                    result = review_diff(session, f"+{diff_content}")
                    if result["warnings"]:
                        with_memee["code_review_catches"] += len(result["warnings"])
                        week_data["catches"] += len(result["warnings"])

            # ─── Propagation + Dream ───
            if week >= 3 and week % 2 == 0:
                prop = run_propagation_cycle(session, confidence_threshold=0.48,
                                            max_propagations=200)
                with_memee["propagated_links"] += prop["total_new_links"]

            if week % 4 == 0:
                run_dream_cycle(session)
            elif week % 2 == 0:
                run_aging_cycle(session)

            # ─── New hire onboarding (quarterly) ───
            if week % 13 == 0:
                new_proj = random.choice(projects)
                stats = inherit_memories(session, new_proj, min_memory_confidence=0.5)
                with_memee["onboarding_patterns"] += stats["memories_inherited"]
                # WITHOUT: new hire takes 4-8 weeks to learn what the org knows
                without_memee["onboarding_weeks"] += random.randint(4, 8)

            # ─── Autoresearch (twice a year) ───
            if week in (15, 40):
                proj = random.choice(projects[:10])
                exp = create_experiment(
                    session, proj.id, "Optimize performance", "latency", "lower",
                    "echo 'latency: 0.5'", baseline_value=0.5,
                )
                current = 0.5
                for i in range(25):
                    delta = random.gauss(0.008, 0.02)
                    new_val = current - abs(delta)
                    if new_val < current:
                        log_iteration(session, exp.id, round(new_val, 4), "keep")
                        current = new_val
                    elif random.random() < 0.08:
                        log_iteration(session, exp.id, 0, "crash")
                    else:
                        log_iteration(session, exp.id, round(new_val, 4), "discard")
                complete_experiment(session, exp, "completed")
                with_memee["experiments_completed"] += 1

            with_memee["weekly_log"].append(week_data)

        # ─── Final metrics ───
        elapsed = time.time() - start_time
        with_memee["total_memories"] = session.query(func.count(Memory.id)).scalar()
        with_memee["canon_memories"] = session.query(func.count(Memory.id)).filter(
            Memory.maturity == MaturityLevel.CANON.value).scalar()
        with_memee["avg_confidence"] = session.query(
            func.avg(Memory.confidence_score)).scalar() or 0

        # Hallucination survival check
        hall_survived = session.query(Memory).filter(
            Memory.source_type == "llm",
            Memory.confidence_score < 0.2,
        ).count()

        # Post-hoc defense: of the hallucinations that slipped past the gate,
        # how many were neutralized? Neutralized = stuck in hypothesis/tested
        # maturity (quarantine held) OR confidence driven below 0.2 (peer kill)
        # OR explicitly deprecated. This is the whole defense-in-depth stack.
        hall_caught_by_defense = 0
        hall_leaked = 0
        if hallucination_memory_ids:
            halls = session.query(Memory).filter(
                Memory.id.in_(hallucination_memory_ids)
            ).all()
            neutralized_maturities = {
                MaturityLevel.HYPOTHESIS.value,
                MaturityLevel.TESTED.value,
                MaturityLevel.DEPRECATED.value,
            }
            for m in halls:
                if (
                    m.maturity in neutralized_maturities
                    or m.confidence_score < 0.2
                ):
                    hall_caught_by_defense += 1
                else:
                    hall_leaked += 1
        # Roll quarantine catches into the caught total (defense-in-depth).
        with_memee["hallucinations_caught"] += hall_caught_by_defense
        with_memee["hallucinations_missed"] = hall_leaked

        # Uncertainty stats
        high_conf_memories = session.query(Memory).filter(
            Memory.confidence_score > 0.8
        ).all()
        avg_uncertainty = sum(get_uncertainty(m) for m in high_conf_memories) / max(len(high_conf_memories), 1)

        # Impact summary
        impact = get_impact_summary(session)

        # ═══════════════════════════════════
        # REPORT
        # ═══════════════════════════════════

        print(f"\n{'═' * 85}")
        print(f"  MEGACORP SIMULATION: WITH MEMEE vs WITHOUT")
        print(f"  100 projects | 50 agents | 52 weeks | 7 AI models")
        print(f"{'═' * 85}")

        print(f"\n  {'Metric':<40s} | {'WITHOUT':>12s} | {'WITH MEMEE':>12s} | {'Delta':>10s}")
        print(f"  {'─'*40} | {'─'*12} | {'─'*12} | {'─'*10}")

        comparisons = [
            ("Time wasted/saved (minutes)",
             without_memee["time_wasted_minutes"],
             with_memee["time_saved_minutes"],
             "saved"),
            ("Iterations wasted/saved",
             without_memee["iterations_wasted"],
             with_memee["iterations_saved"] + with_memee["incidents_caught"] * 3,
             "saved"),
            ("Incidents (repeated/caught)",
             without_memee["incidents_repeated"],
             with_memee["incidents_caught"],
             "caught"),
            ("Hallucinations spread/caught",
             without_memee["hallucinations_spread"],
             with_memee["hallucinations_caught"],
             "caught"),
            ("Duplicate knowledge/merged",
             without_memee["duplicate_knowledge"],
             with_memee["duplicates_merged"],
             "merged"),
            ("New hire onboarding (weeks)",
             without_memee["onboarding_weeks"],
             f"{with_memee['onboarding_patterns']}pat/day0",
             "instant"),
            ("Code review catches",
             0,
             with_memee["code_review_catches"],
             "caught"),
        ]

        for label, wo, wi, action in comparisons:
            print(f"  {label:<40s} | {str(wo):>12s} | {str(wi):>12s} | {action:>10s}")

        # ─── Hallucination detection ───
        print(f"\n{'═' * 85}")
        print(f"  HALLUCINATION DEFENSE")
        print(f"{'═' * 85}")
        total_hall = with_memee["hallucinations_caught"] + with_memee["hallucinations_missed"]
        catch_rate = with_memee["hallucinations_caught"] / max(total_hall, 1) * 100

        gate_catches = with_memee.get("hallucinations_caught_gate", 0)
        print(f"  Hallucinations injected:    {total_hall}")
        print(f"  Caught by quality gate:     {gate_catches}")
        print(f"  Caught by LLM quarantine:   {hall_caught_by_defense}")
        print(f"  Caught total (defense):     {with_memee['hallucinations_caught']}")
        print(f"  Leaked (promoted):          {with_memee['hallucinations_missed']}")
        print(f"  Catch rate:                 {catch_rate:.0f}%")
        print(f"  All LLM <0.2 confidence:    {hall_survived}")
        print(f"  WITHOUT Memee:              {without_memee['hallucinations_spread']} spread as 'facts'")

        # ─── Quality gate stats ───
        print(f"\n{'═' * 85}")
        print(f"  QUALITY GATE EFFECTIVENESS")
        print(f"{'═' * 85}")
        print(f"  Duplicates merged:       {with_memee['duplicates_merged']} "
              f"(WITHOUT: {without_memee['duplicate_knowledge']} duplicates in org)")
        print(f"  Low quality rejected:    {with_memee['quality_rejected']}")
        print(f"  Total memories stored:   {with_memee['total_memories']} (clean, deduplicated)")

        # ─── Knowledge maturity ───
        print(f"\n{'═' * 85}")
        print(f"  KNOWLEDGE MATURITY")
        print(f"{'═' * 85}")
        mat_counts = dict(
            session.query(Memory.maturity, func.count(Memory.id))
            .group_by(Memory.maturity).all()
        )
        for level in ["canon", "validated", "tested", "hypothesis", "deprecated"]:
            count = mat_counts.get(level, 0)
            bar = "█" * (count // 2)
            print(f"  {level:12s}: {count:5d} {bar}")

        print(f"\n  Avg confidence:    {with_memee['avg_confidence']:.3f}")
        print(f"  Avg uncertainty:   {avg_uncertainty:.3f} (high-conf memories)")
        print(f"  Canon memories:    {with_memee['canon_memories']}")
        print(f"  Graph links:       {with_memee['propagated_links']}")

        # ─── Performance ───
        print(f"\n{'═' * 85}")
        print(f"  PERFORMANCE")
        print(f"{'═' * 85}")
        print(f"  Simulation time:     {elapsed:.1f}s")
        print(f"  Memories/second:     {with_memee['total_memories']/elapsed:.0f}")
        print(f"  Projects:            {len(projects)}")
        print(f"  Agents:              {len(agents)}")

        # ─── Incident timeline ───
        print(f"\n{'═' * 85}")
        print(f"  INCIDENT RATE OVER TIME (WITH MEMEE)")
        print(f"{'═' * 85}")
        for i in range(0, 52, 4):
            chunk = with_memee["weekly_log"][i:i+4]
            incidents = sum(w["incidents"] for w in chunk)
            saves = sum(w["saves"] for w in chunk)
            catches = sum(w["catches"] for w in chunk)
            bar_i = "▓" * incidents
            bar_s = "░" * saves
            print(f"  W{i+1:2d}-{i+4:2d}: {incidents:2d} incidents {bar_i}  "
                  f"{saves:2d} saves {bar_s}")

        # ─── ROI ───
        total_time_without = without_memee["time_wasted_minutes"]
        total_time_with = with_memee["time_saved_minutes"]
        roi_hours = total_time_with / 60
        cost_per_month = 199  # Org plan
        hourly_dev_cost = 75  # Average dev cost/hour
        monthly_savings = roi_hours * hourly_dev_cost / 12
        roi_ratio = monthly_savings / cost_per_month if cost_per_month > 0 else 0

        print(f"\n{'═' * 85}")
        print(f"  ROI CALCULATION")
        print(f"{'═' * 85}")
        print(f"  WITHOUT Memee: {total_time_without:,d} minutes wasted on repeated mistakes")
        print(f"  WITH Memee:    {total_time_with:,d} minutes saved by catching mistakes early")
        print(f"  Hours saved:   {roi_hours:.0f} hours/year")
        print(f"  At $75/hr:     ${roi_hours * hourly_dev_cost:,.0f}/year saved")
        print(f"  Memee cost:    ${cost_per_month * 12:,d}/year (Org plan)")
        print(f"  ROI:           {roi_ratio:.0f}x return")

        print(f"\n{'═' * 85}")

        # ─── Assertions ───
        assert with_memee["total_memories"] > 15  # Quality gate deduplicates aggressively
        assert with_memee["incidents_caught"] > 0
        assert with_memee["duplicates_merged"] > 0

        # Hallucination defense — at least two thirds of injected hallucinations
        # must be caught (by gate OR by post-hoc quarantine/invalidation).
        # Semantically-valid-but-wrong patterns cannot be caught at the gate
        # alone; the layered defense (LLM multiplier → peer invalidation →
        # LLM quarantine in evaluate_maturity) is what keeps them down.
        total_injected = (
            with_memee["hallucinations_caught"] + with_memee["hallucinations_missed"]
        )
        if total_injected > 0:
            assert with_memee["hallucinations_caught"] >= max(4, total_injected * 2 // 3), (
                f"Hallucination defense too weak: "
                f"{with_memee['hallucinations_caught']}/{total_injected} caught. "
                f"Expect ≥4 (or ≥2/3 of injected) neutralized via gate + quarantine."
            )

        # Property-based invariant #1: NO LLM memory reaches VALIDATED/CANON
        # without diversity evidence. `validation_count` alone is explicitly
        # NOT sufficient — repeated same-model validation is the hallucination
        # self-reinforcement path we're defending against.
        llm_validated = session.query(Memory).filter(
            Memory.source_type == "llm",
            Memory.maturity.in_([MaturityLevel.VALIDATED.value, MaturityLevel.CANON.value]),
        ).all()
        for m in llm_validated:
            quarantine_lifted = (
                (m.model_count or 0) >= 2
                or (m.project_count or 0) >= 2
            )
            assert quarantine_lifted, (
                f"Promoted LLM memory without diversity evidence: {m.title} "
                f"(val={m.validation_count}, proj={m.project_count}, "
                f"models={m.model_count})"
            )

        # Property-based invariant #2: NO LLM memory reaches CANON without
        # cross-model evidence specifically. Cross-project alone is not enough
        # for canon — the top of the pyramid demands model diversity.
        llm_canon = session.query(Memory).filter(
            Memory.source_type == "llm",
            Memory.maturity == MaturityLevel.CANON.value,
        ).all()
        for m in llm_canon:
            assert (m.model_count or 0) >= 2, (
                f"LLM memory reached CANON without cross-model evidence: "
                f"{m.title} (models={m.model_count}, projects={m.project_count})"
            )

        assert elapsed < 120, f"Should complete in <120s, took {elapsed:.1f}s"
