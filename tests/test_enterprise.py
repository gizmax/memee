"""Enterprise-scale simulation: Fortune 500 software company.

TechCorp — 8 divisions, 50 projects, 30 agents, 52 weeks.
Realistic incident patterns, team dynamics, technology migrations,
quarterly reviews, and measurable learning curves.

Tests organizational learning at scale with full benchmark integration.

Run: pytest tests/test_enterprise.py -v -s
"""

import random
import time
from collections import defaultdict

import pytest
from sqlalchemy import func

from memee.benchmarks.orgmemeval import run_orgmemeval
from memee.engine.confidence import update_confidence
from memee.engine.dream import run_dream_cycle
from memee.engine.inheritance import inherit_memories
from memee.engine.lifecycle import run_aging_cycle
from memee.engine.predictive import scan_project_for_warnings
from memee.engine.propagation import run_propagation_cycle
from memee.engine.research import (
    complete_experiment,
    create_experiment,
    get_meta_learning,
    log_iteration,
)
from memee.engine.review import review_diff
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

# ═══════════════════════════════════════════════
# TECHCORP DEFINITION
# ═══════════════════════════════════════════════

DIVISIONS = {
    "platform": {
        "agents": ["plat-alice", "plat-bob", "plat-carol", "plat-dan"],
        "projects": [
            ("CoreAPI", ["Python", "FastAPI", "PostgreSQL", "Redis"]),
            ("AuthGateway", ["Python", "FastAPI", "JWT", "Redis"]),
            ("EventBus", ["Python", "Kafka", "PostgreSQL"]),
            ("ConfigService", ["Python", "FastAPI", "etcd"]),
            ("ServiceMesh", ["Go", "Envoy", "gRPC"]),
            ("APIGateway", ["Go", "Gin", "Redis"]),
        ],
    },
    "data": {
        "agents": ["data-eve", "data-frank", "data-grace"],
        "projects": [
            ("DataLake", ["Python", "Spark", "Delta Lake", "AWS S3"]),
            ("ETLPipeline", ["Python", "Airflow", "pandas", "SQLite"]),
            ("MLPlatform", ["Python", "scikit-learn", "FastAPI", "MLflow"]),
            ("FeatureStore", ["Python", "Redis", "PostgreSQL"]),
            ("AnalyticsDB", ["Python", "ClickHouse", "Grafana"]),
            ("DataQuality", ["Python", "Great Expectations", "Airflow"]),
        ],
    },
    "frontend": {
        "agents": ["fe-hank", "fe-ivan", "fe-judy", "fe-kate"],
        "projects": [
            ("CustomerPortal", ["React", "TypeScript", "Tailwind", "Next.js"]),
            ("AdminDashboard", ["React", "TypeScript", "Recharts", "MUI"]),
            ("MobileWebApp", ["React", "TypeScript", "PWA"]),
            ("DesignSystem", ["React", "TypeScript", "Storybook"]),
            ("MarketingSite", ["Next.js", "TypeScript", "Tailwind"]),
        ],
    },
    "mobile": {
        "agents": ["mob-leo", "mob-mia", "mob-noah"],
        "projects": [
            ("iOSApp", ["Swift", "SwiftUI", "CoreData", "Combine"]),
            ("AndroidApp", ["Kotlin", "Jetpack Compose", "Room"]),
            ("ReactNativeSDK", ["React Native", "TypeScript"]),
            ("MobileAnalytics", ["Swift", "Kotlin", "Firebase"]),
        ],
    },
    "infra": {
        "agents": ["inf-olivia", "inf-paul"],
        "projects": [
            ("CloudInfra", ["Terraform", "AWS", "Docker"]),
            ("CIPipeline", ["Python", "GitHub Actions", "Docker"]),
            ("MonitoringStack", ["Python", "Prometheus", "Grafana"]),
            ("SecretManager", ["Python", "HashiCorp Vault"]),
        ],
    },
    "security": {
        "agents": ["sec-quinn", "sec-rachel"],
        "projects": [
            ("SecurityScanner", ["Python", "Bandit", "OWASP"]),
            ("PenTestToolkit", ["Python", "Burp Suite"]),
            ("ComplianceEngine", ["Python", "FastAPI", "PostgreSQL"]),
        ],
    },
    "payments": {
        "agents": ["pay-sam", "pay-tina", "pay-uma"],
        "projects": [
            ("PaymentGateway", ["Python", "FastAPI", "PostgreSQL", "Stripe"]),
            ("InvoiceService", ["Python", "FastAPI", "PostgreSQL"]),
            ("FraudDetection", ["Python", "scikit-learn", "Kafka"]),
            ("BillingAPI", ["Python", "FastAPI", "PostgreSQL"]),
        ],
    },
    "ai": {
        "agents": ["ai-victor", "ai-wendy", "ai-xena"],
        "projects": [
            ("RecommendationEngine", ["Python", "PyTorch", "FastAPI"]),
            ("NLPService", ["Python", "Transformers", "FastAPI"]),
            ("SearchRanker", ["Python", "Elasticsearch", "FastAPI"]),
            ("ChatBot", ["Python", "LangChain", "FastAPI", "Redis"]),
        ],
    },
}

# Realistic incident timeline — things that actually go wrong at scale
ENTERPRISE_INCIDENTS = [
    # Week 1-4: Classic early mistakes
    (1, "pay-sam", "PaymentGateway", "critical", "Stripe API key committed to git",
     "Never store API keys in source code", ["security", "secrets", "python"],
     "Keys leaked for 4 hours. Rotated immediately. Customer data not affected."),
    (2, "plat-bob", "CoreAPI", "high", "No timeout on database connection pool",
     "Always set connection pool timeout and max connections", ["python", "database", "postgresql"],
     "DB pool exhausted during traffic spike. 23 min outage."),
    (3, "data-eve", "ETLPipeline", "high", "N+1 queries in pandas ETL loop",
     "Batch database operations — never query per row", ["python", "database", "performance", "pandas"],
     "Daily ETL took 6 hours instead of 20 minutes."),
    (4, "fe-hank", "CustomerPortal", "critical", "XSS via dangerouslySetInnerHTML",
     "Never use dangerouslySetInnerHTML with user input", ["react", "security", "xss", "frontend"],
     "Pen test found stored XSS in user profile. Severity: P1."),

    # Week 5-8: Distributed systems issues
    (5, "plat-carol", "EventBus", "high", "Kafka consumer without idempotency",
     "Always implement idempotent message processing", ["python", "kafka", "distributed", "reliability"],
     "Duplicate payment processing during consumer rebalance."),
    (6, "data-frank", "MLPlatform", "high", "Blocking ML inference in async endpoint",
     "Never run CPU-bound code in async event loop", ["python", "async", "performance", "fastapi", "ml"],
     "scikit-learn predict() blocked event loop. 3s latency per request."),
    (7, "mob-leo", "iOSApp", "high", "SwiftUI DragGesture ghost artifact",
     "Gesture and offset must be on same view in SwiftUI", ["swift", "swiftui", "ui", "gesture"],
     "Ghost artifact during drag. No workaround except structural fix."),
    (8, "fe-ivan", "AdminDashboard", "high", "WebSocket memory leak in useEffect",
     "Always return cleanup function from useEffect with subscriptions", ["react", "frontend", "hooks", "memory-leak"],
     "Dashboard crashed after 2 hours of use. WebSocket connections leaked."),

    # Week 9-12: Data and scale issues
    (9, "data-grace", "DataLake", "high", "No schema evolution strategy",
     "Use schema registry for data lake evolution", ["data", "schema", "architecture"],
     "Breaking schema change corrupted 3 days of data. No rollback possible."),
    (10, "plat-dan", "ServiceMesh", "critical", "mTLS cert expired in production",
     "Automate certificate rotation with alerts", ["security", "devops", "tls"],
     "Service-to-service auth failed. 45 min P1 outage."),
    (11, "pay-tina", "FraudDetection", "high", "Model trained on biased sample",
     "Validate training data distribution before model deployment", ["ml", "data", "quality"],
     "Fraud model had 40% false positive rate on international cards."),

    # Week 13-20: Architecture and process issues
    (14, "inf-paul", "CIPipeline", "medium", "CI runs all tests on every PR",
     "Use test impact analysis to run only affected tests", ["ci", "performance", "devops"],
     "CI takes 45 min per PR. Developer productivity dropped 30%."),
    (16, "sec-quinn", "SecurityScanner", "high", "SQL injection in admin endpoint",
     "Use parameterized queries — never string-format SQL", ["python", "security", "database", "sql-injection"],
     "Admin endpoint vulnerable to SQL injection. Found in quarterly audit."),
    (18, "ai-victor", "RecommendationEngine", "medium", "Cold start problem not handled",
     "Implement fallback strategy for new users without history", ["ml", "product", "ux"],
     "New users saw empty recommendations for first 48 hours."),
    (20, "plat-alice", "APIGateway", "high", "No rate limiting on public API",
     "Implement rate limiting on all public-facing endpoints", ["api", "security", "performance"],
     "Scraping bot caused 10x traffic spike. Degraded service for all users."),
]

# Good patterns discovered
ENTERPRISE_PATTERNS = [
    (2, "plat-alice", "CoreAPI", "Use Pydantic model_validate for all request parsing",
     ["python", "pydantic", "api", "validation"]),
    (4, "inf-olivia", "CIPipeline", "Pre-commit hooks (ruff + mypy) catch 60% of CI failures locally",
     ["python", "ci", "quality", "devops"]),
    (6, "sec-rachel", "SecurityScanner", "Structured logging with correlation IDs across all services",
     ["python", "logging", "observability", "distributed"]),
    (8, "data-eve", "DataLake", "Use Apache Iceberg for time-travel queries on data lake",
     ["data", "architecture", "lakehouse"]),
    (10, "pay-uma", "BillingAPI", "Circuit breaker pattern for payment provider failover",
     ["python", "resilience", "payments", "api"]),
    (12, "fe-kate", "DesignSystem", "Design tokens as single source of truth for all UIs",
     ["frontend", "design", "consistency"]),
    (15, "ai-wendy", "NLPService", "Model versioning with A/B traffic splitting",
     ["ml", "deployment", "experimentation"]),
    (18, "plat-dan", "ConfigService", "Feature flags with gradual rollout percentages",
     ["deployment", "safety", "configuration"]),
    (22, "data-frank", "FeatureStore", "Feature computation with point-in-time correctness",
     ["ml", "data", "quality", "feature-engineering"]),
    (25, "mob-mia", "AndroidApp", "Offline-first architecture with conflict resolution",
     ["mobile", "architecture", "offline", "sync"]),
]

# Technology decisions
ENTERPRISE_DECISIONS = [
    (1, "PostgreSQL over MySQL", "ACID compliance, JSON support, extensions",
     ["MySQL: limited JSON, no custom types", "MongoDB: no strong consistency"]),
    (3, "React over Vue", "Larger ecosystem, TypeScript integration, hiring pool",
     ["Vue: smaller community", "Angular: too opinionated for our scale"]),
    (5, "Kafka over RabbitMQ", "Throughput at scale, replay capability, partitioning",
     ["RabbitMQ: simpler but no replay", "SQS: vendor lock-in"]),
    (8, "FastAPI over Django", "Async native, auto-docs, Pydantic integration",
     ["Django: too heavy for microservices", "Flask: no native async"]),
]

# Code review diffs
ENTERPRISE_DIFFS = [
    (15, "pay-sam", "PaymentGateway",
     "Sam's PR — learned from API key incident (week 1)",
     "+import os\n+STRIPE_KEY = os.getenv('STRIPE_KEY')\n+assert STRIPE_KEY, 'STRIPE_KEY not set'",
     False),
    (20, "data-frank", "MLPlatform",
     "Frank's PR — async inference fix",
     "+    loop = asyncio.get_event_loop()\n+    result = await loop.run_in_executor(None, model.predict, features)\n+    import logging\n+    logger = logging.getLogger(__name__)",
     False),
    (25, "fe-judy", "CustomerPortal",
     "Judy's PR — eval() in template engine (BAD)",
     "+    rendered = eval(template_string.format(**context))\n+    response = requests.get(api_url)",
     True),
    (30, "plat-bob", "CoreAPI",
     "Bob's PR — connection pooling with timeout (learned from week 2)",
     "+    pool = create_engine(url, pool_size=20, max_overflow=10, pool_timeout=30)\n+    import logging\n+    logger = logging.getLogger('sqlalchemy.pool')",
     False),
]

# Autoresearch experiments
ENTERPRISE_EXPERIMENTS = [
    ("CoreAPI", "Reduce P95 API latency below 100ms", "latency_p95", "lower", 0.250),
    ("ETLPipeline", "Increase ETL throughput to 1M rows/min", "throughput", "higher", 0.4),
    ("CustomerPortal", "Improve Lighthouse performance score to 95", "lighthouse", "higher", 0.72),
    ("FraudDetection", "Reduce false positive rate below 5%", "false_positive_rate", "lower", 0.40),
    ("SearchRanker", "Improve NDCG@10 to 0.85", "ndcg", "higher", 0.65),
    ("CIPipeline", "Reduce CI time below 10 minutes", "ci_time_minutes", "lower", 45.0),
]

MODELS = ["claude-opus-4", "claude-sonnet-4", "gpt-4o", "gpt-4-turbo",
          "gemini-2.0-flash", "gemini-1.5-pro", "llama-3.1-70b"]


@pytest.fixture
def enterprise(session, org):
    """Set up TechCorp with all divisions, projects, agents."""
    projects = {}
    agents = {}
    all_agent_names = []

    for div_name, div in DIVISIONS.items():
        for agent_name in div["agents"]:
            agents[agent_name] = {"division": div_name, "created": 0, "mistakes": 0, "saves": 0}
            all_agent_names.append(agent_name)

        for proj_name, stack in div["projects"]:
            tags = [div_name, stack[0].lower()]
            proj = Project(
                organization_id=org.id,
                name=proj_name,
                path=f"/techcorp/{div_name}/{proj_name.lower()}",
                stack=stack,
                tags=tags,
            )
            session.add(proj)
            projects[proj_name] = proj

    session.commit()
    return session, projects, agents, all_agent_names, org


class TestEnterpriseSimulation:

    def test_techcorp_annual_journey(self, enterprise):
        """Full 52-week simulation of TechCorp's organizational learning."""
        session, projects, agents, all_agents, org = enterprise

        all_memories = []
        all_anti_patterns = []
        incident_log = []
        avoidance_log = []
        review_log = []
        experiment_results = []
        quarterly_metrics = []

        start_time = time.time()

        print("\n" + "=" * 80)
        print("  TECHCORP — ANNUAL ORGANIZATIONAL LEARNING REPORT")
        print(f"  8 divisions | {len(projects)} projects | {len(all_agents)} agents | 52 weeks")
        print("=" * 80)

        for week in range(1, 53):
            week_events = []

            # ─── Incidents ───
            week_incidents = [i for i in ENTERPRISE_INCIDENTS if i[0] == week]
            for _, agent, proj_name, severity, title, ap_title, tags, detail in week_incidents:
                proj = projects[proj_name]
                m = Memory(
                    type=MemoryType.ANTI_PATTERN.value,
                    title=ap_title, content=f"Trigger: {title}\nDetail: {detail}",
                    tags=tags, source_agent=agent,
                    source_model=random.choice(MODELS[:3]),
                )
                session.add(m)
                session.flush()
                ap = AntiPattern(
                    memory_id=m.id, severity=severity,
                    trigger=title, consequence=detail,
                    alternative="See organizational best practices",
                )
                session.add(ap)
                pm = ProjectMemory(project_id=proj.id, memory_id=m.id)
                session.add(pm)
                all_memories.append(m)
                all_anti_patterns.append(m)
                agents[agent]["mistakes"] += 1
                incident_log.append({"week": week, "severity": severity, "agent": agent,
                                     "project": proj_name, "title": title})
                week_events.append(f"[{severity.upper():8s}] {agent} @ {proj_name}: {title}")

            # ─── Good patterns ───
            week_patterns = [p for p in ENTERPRISE_PATTERNS if p[0] == week]
            for _, agent, proj_name, title, tags in week_patterns:
                proj = projects[proj_name]
                m = Memory(
                    type=MemoryType.PATTERN.value,
                    title=title, content=title,
                    tags=tags, source_agent=agent,
                    source_model=random.choice(MODELS),
                )
                session.add(m)
                session.flush()
                pm = ProjectMemory(project_id=proj.id, memory_id=m.id)
                session.add(pm)
                all_memories.append(m)
                agents[agent]["created"] += 1

            # ─── Decisions ───
            week_decisions = [d for d in ENTERPRISE_DECISIONS if d[0] == week]
            for _, chosen, reason, alts in week_decisions:
                m = Memory(
                    type=MemoryType.DECISION.value,
                    title=f"Decision: {chosen}", content=reason,
                    tags=["decision"], source_agent=random.choice(all_agents),
                )
                session.add(m)
                session.flush()
                dec = Decision(
                    memory_id=m.id, chosen=chosen,
                    alternatives=[{"name": a.split(":")[0], "reason_rejected": a} for a in alts],
                )
                session.add(dec)
                all_memories.append(m)

            session.commit()

            # ─── Cross-project + cross-model validations ───
            if all_memories:
                patterns = [m for m in all_memories
                            if m.type in (MemoryType.PATTERN.value, MemoryType.ANTI_PATTERN.value)]
                n_val = min(5 + week, len(patterns))
                accuracy = min(0.60 + week * 0.006, 0.90)

                for _ in range(n_val):
                    m = random.choice(patterns)
                    proj = random.choice(list(projects.values()))
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
            check_rate = min(0.25 + week * 0.012, 0.85)
            for agent_name in all_agents:
                if random.random() < check_rate and all_anti_patterns:
                    proj = random.choice(list(projects.values()))
                    warnings = scan_project_for_warnings(session, proj)
                    if warnings:
                        agents[agent_name]["saves"] += 1
                        avoidance_log.append({"week": week, "agent": agent_name})

            # ─── Code reviews ───
            week_diffs = [d for d in ENTERPRISE_DIFFS if d[0] == week]
            for _, agent, proj_name, desc, diff, should_warn in week_diffs:
                result = review_diff(session, f"+{diff}")
                caught = len(result["warnings"]) > 0
                review_log.append({
                    "week": week, "agent": agent, "project": proj_name,
                    "desc": desc, "warnings": len(result["warnings"]),
                    "correct": caught == should_warn,
                })

            # ─── Propagation (bi-weekly) ───
            if week >= 3 and week % 2 == 0:
                run_propagation_cycle(session, confidence_threshold=0.50, max_propagations=200)

            # ─── Dream mode (monthly) ───
            if week % 4 == 0:
                run_dream_cycle(session)
            elif week % 2 == 0:
                run_aging_cycle(session)

            # ─── New project onboarding ───
            if week == 20:
                new_proj = Project(
                    organization_id=org.id, name="PaymentV2",
                    path="/techcorp/payments/paymentv2",
                    stack=["Python", "FastAPI", "PostgreSQL", "Stripe"],
                    tags=["payments", "python"],
                )
                session.add(new_proj)
                session.commit()
                projects["PaymentV2"] = new_proj
                inherit_stats = inherit_memories(session, new_proj)
                week_events.append(
                    f"NEW PROJECT: PaymentV2 inherits {inherit_stats['memories_inherited']} patterns"
                )

            if week == 35:
                new_proj2 = Project(
                    organization_id=org.id, name="MLOps",
                    path="/techcorp/data/mlops",
                    stack=["Python", "Kubernetes", "MLflow", "Docker"],
                    tags=["data", "devops", "python"],
                )
                session.add(new_proj2)
                session.commit()
                projects["MLOps"] = new_proj2
                inherit_stats2 = inherit_memories(session, new_proj2)
                week_events.append(
                    f"NEW PROJECT: MLOps inherits {inherit_stats2['memories_inherited']} patterns"
                )

            # ─── Autoresearch experiments (start at specific weeks) ───
            if week == 10:
                for proj_name, goal, metric, direction, baseline in ENTERPRISE_EXPERIMENTS[:3]:
                    proj = projects[proj_name]
                    exp = create_experiment(
                        session, proj.id, goal, metric, direction,
                        f"echo '{metric}: {baseline}'", baseline_value=baseline,
                    )
                    # Run 20 iterations
                    current = baseline
                    for i in range(20):
                        delta = random.gauss(0.01, 0.025) * (1 if direction == "higher" else -1)
                        new_val = current + delta
                        improves = (direction == "higher" and new_val > current) or \
                                   (direction == "lower" and new_val < current)
                        if improves:
                            log_iteration(session, exp.id, round(new_val, 4), "keep")
                            current = new_val
                        elif random.random() < 0.08:
                            log_iteration(session, exp.id, 0, "crash")
                        else:
                            log_iteration(session, exp.id, round(new_val, 4), "discard")
                    complete_experiment(session, exp, "completed")
                    experiment_results.append({
                        "project": proj_name, "goal": goal,
                        "baseline": baseline, "final": exp.best_value,
                        "keep_rate": exp.keeps / max(exp.total_iterations, 1),
                    })

            if week == 30:
                for proj_name, goal, metric, direction, baseline in ENTERPRISE_EXPERIMENTS[3:]:
                    proj = projects[proj_name]
                    exp = create_experiment(
                        session, proj.id, goal, metric, direction,
                        f"echo '{metric}: {baseline}'", baseline_value=baseline,
                    )
                    current = baseline
                    for i in range(20):
                        delta = random.gauss(0.01, 0.025) * (1 if direction == "higher" else -1)
                        new_val = current + delta
                        improves = (direction == "higher" and new_val > current) or \
                                   (direction == "lower" and new_val < current)
                        if improves:
                            log_iteration(session, exp.id, round(new_val, 4), "keep")
                            current = new_val
                        elif random.random() < 0.08:
                            log_iteration(session, exp.id, 0, "crash")
                        else:
                            log_iteration(session, exp.id, round(new_val, 4), "discard")
                    complete_experiment(session, exp, "completed")
                    experiment_results.append({
                        "project": proj_name, "goal": goal,
                        "baseline": baseline, "final": exp.best_value,
                        "keep_rate": exp.keeps / max(exp.total_iterations, 1),
                    })

            # ─── Quarterly snapshot ───
            if week % 13 == 0:
                total = session.query(func.count(Memory.id)).scalar()
                canon = session.query(func.count(Memory.id)).filter(
                    Memory.maturity == MaturityLevel.CANON.value).scalar()
                validated = session.query(func.count(Memory.id)).filter(
                    Memory.maturity == MaturityLevel.VALIDATED.value).scalar()
                tested = session.query(func.count(Memory.id)).filter(
                    Memory.maturity == MaturityLevel.TESTED.value).scalar()
                avg_conf = session.query(func.avg(Memory.confidence_score)).scalar() or 0
                connections = session.query(func.count(MemoryConnection.source_id)).scalar()

                # Model diversity
                models_used = session.query(Memory.source_model).filter(
                    Memory.source_model.isnot(None)
                ).distinct().count()

                quarter = week // 13
                avoidances_q = sum(1 for a in avoidance_log
                                   if (quarter - 1) * 13 < a["week"] <= quarter * 13)
                incidents_q = sum(1 for i in incident_log
                                  if (quarter - 1) * 13 < i["week"] <= quarter * 13)

                quarterly_metrics.append({
                    "quarter": f"Q{quarter}", "week": week,
                    "total": total, "canon": canon, "validated": validated,
                    "tested": tested, "avg_conf": avg_conf,
                    "connections": connections, "models": models_used,
                    "incidents": incidents_q, "avoidances": avoidances_q,
                })

        elapsed = time.time() - start_time

        # ═══════════════════════════════════════════════
        # ANNUAL REPORT
        # ═══════════════════════════════════════════════

        # Quarterly table
        print(f"\n{'═' * 80}")
        print("  QUARTERLY PERFORMANCE")
        print(f"{'═' * 80}")
        print(f"  {'Q':>3s} | {'Memories':>8s} | {'Canon':>5s} | {'Valid':>5s} | "
              f"{'Conf':>5s} | {'Graph':>5s} | {'Models':>6s} | {'Incidents':>9s} | {'Saves':>5s}")
        print(f"  {'─'*3} | {'─'*8} | {'─'*5} | {'─'*5} | "
              f"{'─'*5} | {'─'*5} | {'─'*6} | {'─'*9} | {'─'*5}")
        for q in quarterly_metrics:
            print(f"  {q['quarter']:>3s} | {q['total']:8d} | {q['canon']:5d} | "
                  f"{q['validated']:5d} | {q['avg_conf']:5.3f} | {q['connections']:5d} | "
                  f"{q['models']:6d} | {q['incidents']:9d} | {q['avoidances']:5d}")

        # Incident timeline
        print(f"\n{'═' * 80}")
        print(f"  INCIDENT TIMELINE ({len(incident_log)} total)")
        print(f"{'═' * 80}")
        severity_counts = defaultdict(int)
        for inc in incident_log:
            severity_counts[inc["severity"]] += 1
            div = agents[inc["agent"]]["division"]
            print(f"  W{inc['week']:2d} [{inc['severity']:8s}] {inc['agent']:12s} "
                  f"({div:8s}) @ {inc['project']}")
            print(f"       {inc['title']}")

        print("\n  By severity: " +
              " | ".join(f"{s}: {c}" for s, c in sorted(severity_counts.items())))

        # Avoidance over time
        print(f"\n{'═' * 80}")
        print(f"  MISTAKE PREVENTION ({len(avoidance_log)} saves)")
        print(f"{'═' * 80}")
        saves_by_quarter = defaultdict(int)
        for a in avoidance_log:
            q = (a["week"] - 1) // 13 + 1
            saves_by_quarter[q] += 1
        for q in range(1, 5):
            bar = "█" * (saves_by_quarter[q] // 5)
            print(f"  Q{q}: {saves_by_quarter[q]:4d} saves {bar}")
        print(f"  Total saves: {len(avoidance_log)}")

        # Code review
        if review_log:
            print(f"\n{'═' * 80}")
            print("  CODE REVIEW EFFECTIVENESS")
            print(f"{'═' * 80}")
            correct = sum(1 for r in review_log if r["correct"])
            total_reviews = len(review_log)
            for r in review_log:
                status = "CORRECT" if r["correct"] else "MISSED"
                print(f"  W{r['week']:2d} [{status:7s}] {r['agent']:12s} @ {r['project']}")
                print(f"       {r['desc']}")
                print(f"       Warnings: {r['warnings']}")
            print(f"\n  Accuracy: {correct}/{total_reviews} ({correct/total_reviews*100:.0f}%)")

        # Autoresearch
        if experiment_results:
            print(f"\n{'═' * 80}")
            print(f"  AUTORESEARCH EXPERIMENTS ({len(experiment_results)})")
            print(f"{'═' * 80}")
            for exp in experiment_results:
                improvement = exp["final"] - exp["baseline"] if exp["final"] else 0
                sign = "+" if improvement > 0 else ""
                print(f"  {exp['project']:25s} {exp['goal'][:40]}")
                print(f"    Baseline: {exp['baseline']:.4f} → Final: {exp['final']:.4f} "
                      f"({sign}{improvement:.4f}) Keep: {exp['keep_rate']:.0%}")

            meta = get_meta_learning(session)
            if meta.get("insights"):
                print("\n  Meta-learning insights:")
                for insight in meta["insights"]:
                    print(f"    - {insight}")

        # Agent leaderboard
        print(f"\n{'═' * 80}")
        print("  AGENT LEADERBOARD")
        print(f"{'═' * 80}")
        sorted_agents = sorted(agents.items(),
                                key=lambda x: x[1]["saves"] - x[1]["mistakes"], reverse=True)
        print(f"  {'Agent':>12s} | {'Division':>10s} | {'Created':>7s} | "
              f"{'Mistakes':>8s} | {'Saves':>5s} | {'Net':>4s} | Status")
        print(f"  {'─'*12} | {'─'*10} | {'─'*7} | {'─'*8} | {'─'*5} | {'─'*4} | {'─'*12}")
        for name, data in sorted_agents[:15]:
            net = data["saves"] - data["mistakes"]
            status = "STAR" if net > 5 else "SOLID" if net > 0 else "LEARNING" if net == 0 else "NEEDS HELP"
            print(f"  {name:>12s} | {data['division']:>10s} | {data['created']:>7d} | "
                  f"{data['mistakes']:>8d} | {data['saves']:>5d} | {net:>+4d} | {status}")

        # Model diversity
        print(f"\n{'═' * 80}")
        print("  MULTI-MODEL VALIDATION")
        print(f"{'═' * 80}")
        model_val_counts = defaultdict(int)
        for v in session.query(MemoryValidation).filter(
            MemoryValidation.validator_model.isnot(None)
        ).all():
            from memee.engine.models import get_model_family
            family = get_model_family(v.validator_model)
            model_val_counts[family] += 1
        for family, count in sorted(model_val_counts.items(), key=lambda x: -x[1]):
            bar = "█" * (count // 10)
            print(f"  {family:>12s}: {count:5d} validations {bar}")

        multi_model = session.query(Memory).filter(Memory.model_count >= 2).count()
        total_mem = session.query(func.count(Memory.id)).scalar()
        print(f"\n  Memories validated by 2+ model families: {multi_model}/{total_mem} "
              f"({multi_model/max(total_mem,1)*100:.0f}%)")

        # Final stats
        final = quarterly_metrics[-1] if quarterly_metrics else {}
        print(f"\n{'═' * 80}")
        print("  ANNUAL SUMMARY")
        print(f"{'═' * 80}")
        print(f"  Total memories:        {final.get('total', 0)}")
        print(f"  Canon (org truth):     {final.get('canon', 0)}")
        print(f"  Avg confidence:        {final.get('avg_conf', 0):.3f}")
        print(f"  Graph connections:     {final.get('connections', 0)}")
        print(f"  Incidents:             {len(incident_log)}")
        print(f"  Mistakes prevented:    {len(avoidance_log)}")
        print(f"  Prevention ratio:      {len(avoidance_log)}:{len(incident_log)} "
              f"({len(avoidance_log)/max(len(incident_log),1):.0f}x)")
        print(f"  Code review accuracy:  {sum(1 for r in review_log if r['correct'])}/{len(review_log)}")
        print(f"  Experiments completed: {len(experiment_results)}")
        print(f"  Simulation time:       {elapsed:.1f}s")

        # ─── Run OrgMemEval benchmark on this data ───
        print(f"\n{'═' * 80}")
        print("  ORGMEMEVAL BENCHMARK")
        print(f"{'═' * 80}")
        bench_results = run_orgmemeval(seed=2026)
        for s in bench_results["scenarios"]:
            bar = "█" * int(s["pct"] / 5)
            print(f"  {s['name']:16s} {s['score']:5.1f}/{s['max_points']:2d} "
                  f"({s['pct']:3.0f}%) {bar}")
        print(f"  {'TOTAL':16s} {bench_results['total_score']:5.1f}/{bench_results['total_max']:3d} "
              f"({bench_results['total_pct']:.0f}%)")

        print(f"\n{'═' * 80}")

        # Assertions
        assert final.get("total", 0) > 30
        assert len(avoidance_log) > len(incident_log)
        assert elapsed < 60
        if quarterly_metrics:
            assert quarterly_metrics[-1]["avg_conf"] > quarterly_metrics[0]["avg_conf"]
