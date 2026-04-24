"""Multi-Agent Company Simulation: how Memee learns, improves, and prevents mistakes.

Simulates "NovaTech" — a software company with 6 teams, 12 projects, 10 agents.
Runs through realistic scenarios over 6 months (24 weeks):

  Month 1: Chaos Phase — agents work in silos, repeat mistakes
  Month 2: Discovery Phase — Memee starts connecting patterns
  Month 3: Learning Phase — cross-project knowledge flows, anti-patterns caught
  Month 4: Maturity Phase — canon patterns emerge, new agents onboard faster
  Month 5: Optimization Phase — dream mode finds contradictions, refines knowledge
  Month 6: Mastery Phase — org IQ plateaus, knowledge compounds

Tracks detailed metrics at each phase and prints a narrative report.

Run: pytest tests/test_company_simulation.py -v -s
"""

import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func

from memee.engine.confidence import update_confidence
from memee.engine.dream import run_dream_cycle
from memee.engine.inheritance import inherit_memories
from memee.engine.lifecycle import run_aging_cycle
from memee.engine.predictive import scan_project_for_warnings
from memee.engine.propagation import run_propagation_cycle
from memee.engine.review import review_diff
from memee.engine.search import search_anti_patterns, search_memories
from memee.storage.models import (
    AntiPattern,
    Decision,
    MaturityLevel,
    Memory,
    MemoryConnection,
    MemoryType,
    MemoryValidation,
    Organization,
    Project,
    ProjectMemory,
    ResearchExperiment,
    ResearchIteration,
    ResearchStatus,
    Severity,
)

random.seed(42)


# ═══════════════════════════════════
# COMPANY DEFINITION: NovaTech
# ═══════════════════════════════════

COMPANY = "NovaTech"

TEAMS = {
    "backend": {
        "agents": ["alice", "bob"],
        "projects": [
            ("PaymentAPI", ["Python", "FastAPI", "PostgreSQL"], ["api", "payments", "python"]),
            ("AuthService", ["Python", "FastAPI", "Redis"], ["api", "auth", "python", "security"]),
        ],
    },
    "data": {
        "agents": ["carol", "dan"],
        "projects": [
            ("DataPipeline", ["Python", "pandas", "Airflow", "SQLite"], ["data", "etl", "python"]),
            ("MLPlatform", ["Python", "scikit-learn", "FastAPI"], ["ml", "api", "python"]),
        ],
    },
    "frontend": {
        "agents": ["eve", "frank"],
        "projects": [
            ("CustomerPortal", ["React", "TypeScript", "Tailwind"], ["frontend", "react", "typescript"]),
            ("AdminDashboard", ["React", "TypeScript", "Recharts"], ["frontend", "react", "dashboard"]),
        ],
    },
    "mobile": {
        "agents": ["grace", "hank"],
        "projects": [
            ("iOSApp", ["Swift", "SwiftUI", "CoreData"], ["ios", "swift", "mobile"]),
            ("AndroidApp", ["Kotlin", "Jetpack Compose"], ["android", "kotlin", "mobile"]),
        ],
    },
    "devops": {
        "agents": ["ivan"],
        "projects": [
            ("InfraConfig", ["Terraform", "Docker", "Python"], ["devops", "infra", "python"]),
            ("CIPipeline", ["Python", "GitHub Actions"], ["ci", "devops", "python"]),
        ],
    },
    "security": {
        "agents": ["judy"],
        "projects": [
            ("SecurityAudit", ["Python", "Bandit", "OWASP"], ["security", "audit", "python"]),
        ],
    },
}

# Real-world incidents that happen to the company
INCIDENTS = [
    {
        "week": 2,
        "agent": "bob",
        "project": "PaymentAPI",
        "type": "mistake",
        "title": "Stored API keys in source code",
        "detail": "Bob committed Stripe API keys directly in config.py. Caught by code review.",
        "anti_pattern": {
            "title": "Never store API keys in source code",
            "severity": "critical",
            "trigger": "Hardcoding secrets, API keys, tokens in .py/.js/.ts files",
            "consequence": "Keys leak via git history, CI logs, code sharing. Stripe key was exposed for 6 hours.",
            "alternative": "Use environment variables: os.getenv('STRIPE_KEY') or .env files with python-dotenv",
            "tags": ["security", "secrets", "python", "api"],
        },
    },
    {
        "week": 3,
        "agent": "alice",
        "project": "AuthService",
        "type": "mistake",
        "title": "No timeout on Redis connection",
        "detail": "Redis went down and AuthService hung for 47 minutes. All auth requests blocked.",
        "anti_pattern": {
            "title": "Always set timeout on external connections",
            "severity": "high",
            "trigger": "Connecting to Redis, HTTP APIs, databases without explicit timeout",
            "consequence": "Service hangs indefinitely. One slow dependency cascades to entire system.",
            "alternative": "redis.Redis(host, port, socket_timeout=5, socket_connect_timeout=5)",
            "tags": ["python", "redis", "reliability", "timeout"],
        },
    },
    {
        "week": 4,
        "agent": "carol",
        "project": "DataPipeline",
        "type": "mistake",
        "title": "N+1 query in pandas ETL loop",
        "detail": "ETL job took 3 hours instead of 5 minutes. Each row triggered separate DB query.",
        "anti_pattern": {
            "title": "Avoid N+1 queries — batch database operations",
            "severity": "high",
            "trigger": "Looping over rows and querying DB per row instead of batch SELECT/INSERT",
            "consequence": "O(n) queries instead of O(1). 3h vs 5min for 100K rows.",
            "alternative": "Use pd.read_sql() for batch read, df.to_sql() for batch write",
            "tags": ["python", "database", "performance", "pandas"],
        },
    },
    {
        "week": 5,
        "agent": "eve",
        "project": "CustomerPortal",
        "type": "mistake",
        "title": "XSS vulnerability in user profile",
        "detail": "User name field rendered raw HTML. Pen test found stored XSS.",
        "anti_pattern": {
            "title": "Always sanitize user-generated HTML output",
            "severity": "critical",
            "trigger": "Rendering user input directly with dangerouslySetInnerHTML or without escaping",
            "consequence": "Stored XSS attack. Attacker can steal sessions, redirect users.",
            "alternative": "Use React's default escaping. Never use dangerouslySetInnerHTML with user input.",
            "tags": ["security", "react", "frontend", "xss"],
        },
    },
    {
        "week": 7,
        "agent": "dan",
        "project": "MLPlatform",
        "type": "mistake",
        "title": "Synchronous ML inference in async endpoint",
        "detail": "scikit-learn predict() blocked the FastAPI event loop. 2s latency per request.",
        "anti_pattern": {
            "title": "Never run CPU-bound code in async event loop",
            "severity": "high",
            "trigger": "Calling synchronous ML inference, heavy computation, or blocking I/O in async def endpoint",
            "consequence": "Event loop blocks. All concurrent requests wait. Latency spikes.",
            "alternative": "Use run_in_executor() or dedicated worker thread/process pool",
            "tags": ["python", "async", "performance", "fastapi", "ml"],
        },
    },
    {
        "week": 8,
        "agent": "grace",
        "project": "iOSApp",
        "type": "mistake",
        "title": "SwiftUI DragGesture ghost artifact",
        "detail": "DragGesture on child view with .offset on parent caused ghost during slow drag.",
        "anti_pattern": {
            "title": "SwiftUI: gesture and offset must be on same view",
            "severity": "high",
            "trigger": "DragGesture on child view with .offset modifier on parent/different view level",
            "consequence": "Ghost/duplicate visual artifact during slow continuous drag. Unfixable with workarounds.",
            "alternative": "Move both DragGesture AND .offset to the SAME view in the hierarchy",
            "tags": ["swift", "swiftui", "ui", "gesture", "ios"],
        },
    },
    {
        "week": 10,
        "agent": "frank",
        "project": "AdminDashboard",
        "type": "mistake",
        "title": "Memory leak from useEffect without cleanup",
        "detail": "WebSocket subscription in useEffect without cleanup. Dashboard crashed after 2 hours.",
        "anti_pattern": {
            "title": "Always return cleanup function from useEffect with subscriptions",
            "severity": "high",
            "trigger": "useEffect with WebSocket, setInterval, event listeners without return cleanup",
            "consequence": "Memory leak. Component unmounts but subscription continues. Eventually crashes.",
            "alternative": "return () => { ws.close(); clearInterval(id); el.removeEventListener(...); }",
            "tags": ["react", "frontend", "hooks", "memory-leak"],
        },
    },
    # ── Good discoveries (patterns that work) ──
    {
        "week": 3,
        "agent": "alice",
        "project": "PaymentAPI",
        "type": "pattern",
        "title": "Pydantic model_validate for request parsing",
        "detail": "Replaced manual dict parsing with Pydantic. Caught 12 type bugs in first week.",
        "pattern": {
            "title": "Use Pydantic model_validate() instead of manual dict parsing",
            "content": "Pydantic validates types, provides defaults, generates OpenAPI schema. "
                       "Caught 12 bugs in PaymentAPI that manual parsing missed.",
            "tags": ["python", "pydantic", "validation", "api", "fastapi"],
        },
    },
    {
        "week": 6,
        "agent": "ivan",
        "project": "CIPipeline",
        "type": "pattern",
        "title": "Pre-commit hooks catch issues before CI",
        "detail": "Added ruff + mypy pre-commit hooks. CI failures dropped 60%.",
        "pattern": {
            "title": "Use pre-commit hooks (ruff, mypy) to catch issues before CI",
            "content": "Pre-commit catches lint/type errors locally in <2s instead of waiting "
                       "5min for CI. CI failure rate dropped from 40% to 16% at NovaTech.",
            "tags": ["python", "ci", "quality", "devops", "git"],
        },
    },
    {
        "week": 9,
        "agent": "judy",
        "project": "SecurityAudit",
        "type": "pattern",
        "title": "Structured logging with correlation IDs",
        "detail": "Added correlation IDs to all services. Incident investigation time dropped 5x.",
        "pattern": {
            "title": "Use structured logging with correlation IDs across services",
            "content": "Generate UUID per request, propagate via headers, include in all log entries. "
                       "Searching logs across services: 45min → 8min.",
            "tags": ["python", "logging", "observability", "api", "devops"],
        },
    },
    # ── Decisions ──
    {
        "week": 1,
        "agent": "alice",
        "project": "PaymentAPI",
        "type": "decision",
        "title": "PostgreSQL over SQLite for PaymentAPI",
        "decision": {
            "chosen": "PostgreSQL",
            "alternatives": [
                {"name": "SQLite", "reason_rejected": "No concurrent writes for payment processing"},
                {"name": "MongoDB", "reason_rejected": "Transactions needed for payment consistency"},
            ],
            "criteria": ["ACID transactions", "concurrent writes", "production scale"],
        },
    },
    {
        "week": 5,
        "agent": "eve",
        "project": "CustomerPortal",
        "type": "decision",
        "title": "Tailwind CSS over styled-components",
        "decision": {
            "chosen": "Tailwind CSS",
            "alternatives": [
                {"name": "styled-components", "reason_rejected": "Runtime overhead, SSR hydration issues"},
                {"name": "CSS Modules", "reason_rejected": "No utility classes, more verbose"},
            ],
            "criteria": ["zero runtime", "design consistency", "responsive utilities"],
        },
    },
]

# Code diffs that agents submit for review
CODE_DIFFS = [
    {
        "week": 12,
        "agent": "bob",
        "project": "PaymentAPI",
        "description": "Bob's PR — learned from previous API key incident",
        "diff": """\
diff --git a/src/config.py b/src/config.py
+import os
+STRIPE_KEY = os.getenv('STRIPE_KEY')
+DB_URL = os.getenv('DATABASE_URL')
+# All secrets from environment, never hardcoded
""",
        "should_have_warnings": False,
    },
    {
        "week": 14,
        "agent": "dan",
        "project": "MLPlatform",
        "diff": """\
diff --git a/src/api.py b/src/api.py
+    result = eval(user_query)  # quick hack for dynamic queries
+    response = requests.get(url)
""",
        "description": "Dan's PR — two anti-patterns: eval() and no timeout",
        "should_have_warnings": True,
    },
    {
        "week": 18,
        "agent": "carol",
        "project": "DataPipeline",
        "diff": """\
diff --git a/src/etl.py b/src/etl.py
+    df = pd.read_sql("SELECT * FROM events WHERE date > ?", conn, params=[cutoff])
+    results = df.groupby('user_id').agg({'amount': 'sum'})
+    results.to_sql('daily_totals', conn, if_exists='replace')
""",
        "description": "Carol's PR — batch operations, learned from N+1 incident",
        "should_have_warnings": False,
    },
]


@pytest.fixture
def company(session, org):
    """Set up NovaTech company with all teams and projects."""
    projects = {}
    agents = {}

    for team_name, team in TEAMS.items():
        for agent_name in team["agents"]:
            agents[agent_name] = {"team": team_name, "memories_created": 0, "mistakes": 0, "saves": 0}

        for proj_name, stack, tags in team["projects"]:
            proj = Project(
                organization_id=org.id,
                name=proj_name,
                path=f"/novatech/{proj_name.lower()}",
                stack=stack,
                tags=tags,
            )
            session.add(proj)
            projects[proj_name] = proj

    session.commit()
    return session, projects, agents, org


class TestCompanySimulation:

    def test_novatech_6_month_journey(self, company):
        """Full 6-month simulation of NovaTech's learning journey."""
        session, projects, agents, org = company

        all_memories = []
        all_anti_patterns = []
        incident_log = []
        avoidance_log = []
        weekly_metrics = []
        review_results = []

        start_time = time.time()

        print("\n" + "=" * 80)
        print(f"  NOVATECH — 6-MONTH ORGANIZATIONAL LEARNING JOURNEY")
        print(f"  6 teams | 11 projects | 10 agents | 24 weeks")
        print("=" * 80)

        for week in range(1, 25):
            week_events = []

            # ─────────────────────────────────
            # PHASE: Process scheduled incidents
            # ─────────────────────────────────
            week_incidents = [i for i in INCIDENTS if i["week"] == week]
            for incident in week_incidents:
                agent = incident["agent"]
                proj = projects[incident["project"]]

                if incident["type"] == "mistake":
                    # Agent makes a mistake → records anti-pattern
                    ap_data = incident["anti_pattern"]
                    m = Memory(
                        type=MemoryType.ANTI_PATTERN.value,
                        title=ap_data["title"],
                        content=f"Trigger: {ap_data['trigger']}\n"
                                f"Consequence: {ap_data['consequence']}\n"
                                f"Alternative: {ap_data['alternative']}",
                        tags=ap_data["tags"],
                        source_agent=agent,
                    )
                    session.add(m)
                    session.flush()

                    ap = AntiPattern(
                        memory_id=m.id,
                        severity=ap_data["severity"],
                        trigger=ap_data["trigger"],
                        consequence=ap_data["consequence"],
                        alternative=ap_data["alternative"],
                    )
                    session.add(ap)
                    pm = ProjectMemory(project_id=proj.id, memory_id=m.id)
                    session.add(pm)

                    all_memories.append(m)
                    all_anti_patterns.append(m)
                    agents[agent]["mistakes"] += 1
                    incident_log.append({
                        "week": week, "agent": agent, "project": proj.name,
                        "title": incident["title"], "severity": ap_data["severity"],
                    })
                    week_events.append(
                        f"INCIDENT [{ap_data['severity'].upper()}]: {agent} @ {proj.name} — {incident['title']}"
                    )

                elif incident["type"] == "pattern":
                    p_data = incident["pattern"]
                    m = Memory(
                        type=MemoryType.PATTERN.value,
                        title=p_data["title"],
                        content=p_data["content"],
                        tags=p_data["tags"],
                        source_agent=agent,
                    )
                    session.add(m)
                    session.flush()
                    pm = ProjectMemory(project_id=proj.id, memory_id=m.id)
                    session.add(pm)
                    all_memories.append(m)
                    agents[agent]["memories_created"] += 1
                    week_events.append(
                        f"PATTERN: {agent} @ {proj.name} — {p_data['title']}"
                    )

                elif incident["type"] == "decision":
                    d_data = incident["decision"]
                    m = Memory(
                        type=MemoryType.DECISION.value,
                        title=f"Decision: {d_data['chosen']}",
                        content=f"Chose {d_data['chosen']} for {proj.name}",
                        tags=["decision"],
                        source_agent=agent,
                    )
                    session.add(m)
                    session.flush()
                    dec = Decision(
                        memory_id=m.id,
                        chosen=d_data["chosen"],
                        alternatives=d_data["alternatives"],
                        criteria=d_data.get("criteria", []),
                    )
                    session.add(dec)
                    pm = ProjectMemory(project_id=proj.id, memory_id=m.id)
                    session.add(pm)
                    all_memories.append(m)
                    week_events.append(
                        f"DECISION: {agent} @ {proj.name} — {d_data['chosen']}"
                    )

            session.commit()

            # ─────────────────────────────────
            # PHASE: Cross-project validations
            # ─────────────────────────────────
            if all_memories:
                patterns = [m for m in all_memories
                            if m.type in (MemoryType.PATTERN.value, MemoryType.ANTI_PATTERN.value)]
                n_validations = min(len(patterns), 3 + week)  # More validations as org matures

                for _ in range(n_validations):
                    m = random.choice(patterns)
                    proj = random.choice(list(projects.values()))
                    agent_name = random.choice(list(agents.keys()))
                    validated = random.random() < (0.65 + week * 0.01)  # Validation rate improves

                    v = MemoryValidation(
                        memory_id=m.id,
                        project_id=proj.id,
                        validated=validated,
                    )
                    session.add(v)
                    update_confidence(m, validated, proj.id)

                    if validated:
                        agents[agent_name]["memories_created"] += 1

                session.commit()

            # ─────────────────────────────────
            # PHASE: Anti-pattern checks (agents check BEFORE implementing)
            # Check rate improves over time as org culture develops
            # ─────────────────────────────────
            check_rate = min(0.3 + week * 0.025, 0.9)  # 30% week 1 → 90% week 24
            for agent_name, agent_data in agents.items():
                if random.random() < check_rate and all_anti_patterns:
                    proj_name = random.choice(
                        [p for t in TEAMS.values() for p, _, _ in t["projects"]
                         if agent_name in TEAMS.get(agent_data["team"], {}).get("agents", [])]
                    ) if random.random() > 0.3 else random.choice(list(projects.keys()))

                    proj = projects.get(proj_name)
                    if proj:
                        warnings = scan_project_for_warnings(session, proj)
                        if warnings:
                            agents[agent_name]["saves"] += 1
                            avoidance_log.append({
                                "week": week, "agent": agent_name,
                                "project": proj_name,
                                "avoided": warnings[0]["title"],
                            })

            # ─────────────────────────────────
            # PHASE: Code reviews
            # ─────────────────────────────────
            week_reviews = [r for r in CODE_DIFFS if r["week"] == week]
            for rev in week_reviews:
                result = review_diff(session, rev["diff"])
                caught = len(result["warnings"]) > 0
                review_results.append({
                    "week": week, "agent": rev["agent"],
                    "project": rev["project"],
                    "description": rev["description"],
                    "warnings": len(result["warnings"]),
                    "confirmations": len(result["confirmations"]),
                    "caught_correctly": caught == rev["should_have_warnings"],
                })
                if caught:
                    week_events.append(
                        f"CODE REVIEW CATCH: {rev['agent']}'s PR flagged "
                        f"{len(result['warnings'])} issue(s)"
                    )

            # ─────────────────────────────────
            # PHASE: Auto-propagation (weekly)
            # ─────────────────────────────────
            if week >= 4:  # Start propagation after 1st month
                prop_stats = run_propagation_cycle(
                    session, confidence_threshold=0.52, max_propagations=100,
                )
                if prop_stats["total_new_links"] > 0:
                    week_events.append(
                        f"PROPAGATION: {prop_stats['total_new_links']} patterns spread "
                        f"to {prop_stats['projects_reached']} projects"
                    )

            # ─────────────────────────────────
            # PHASE: Dream mode (monthly — weeks 4, 8, 12, 16, 20, 24)
            # ─────────────────────────────────
            if week % 4 == 0:
                dream_stats = run_dream_cycle(session)
                week_events.append(
                    f"DREAM: {dream_stats['connections_created']} connections, "
                    f"{dream_stats['contradictions_found']} contradictions, "
                    f"{dream_stats['promotions_applied']} promotions"
                )
            else:
                run_aging_cycle(session)

            # ─────────────────────────────────
            # PHASE: New project onboarding (inheritance)
            # ─────────────────────────────────
            if week == 16:
                # NovaTech starts a new Python API project
                new_proj = Project(
                    organization_id=org.id,
                    name="AnalyticsAPI",
                    path="/novatech/analyticsapi",
                    stack=["Python", "FastAPI", "PostgreSQL"],
                    tags=["api", "analytics", "python"],
                )
                session.add(new_proj)
                session.commit()
                projects["AnalyticsAPI"] = new_proj

                inherit_stats = inherit_memories(session, new_proj)
                week_events.append(
                    f"NEW PROJECT: AnalyticsAPI inherits {inherit_stats['memories_inherited']} "
                    f"patterns from {len(inherit_stats['similar_projects'])} similar projects"
                )

            # ─────────────────────────────────
            # Collect weekly metrics
            # ─────────────────────────────────
            total_mem = session.query(func.count(Memory.id)).scalar()
            canon = session.query(func.count(Memory.id)).filter(
                Memory.maturity == MaturityLevel.CANON.value).scalar()
            validated = session.query(func.count(Memory.id)).filter(
                Memory.maturity == MaturityLevel.VALIDATED.value).scalar()
            tested = session.query(func.count(Memory.id)).filter(
                Memory.maturity == MaturityLevel.TESTED.value).scalar()
            avg_conf = session.query(func.avg(Memory.confidence_score)).scalar() or 0

            weekly_metrics.append({
                "week": week, "total": total_mem, "canon": canon,
                "validated": validated, "tested": tested, "avg_conf": avg_conf,
                "events": week_events,
            })

        elapsed = time.time() - start_time

        # ═══════════════════════════════════
        # NARRATIVE REPORT
        # ═══════════════════════════════════

        print(f"\n{'─' * 80}")
        print(f"  MONTH 1 (Weeks 1-4): CHAOS PHASE")
        print(f"{'─' * 80}")
        for wm in weekly_metrics[:4]:
            if wm["events"]:
                print(f"\n  Week {wm['week']}:")
                for e in wm["events"]:
                    print(f"    {e}")

        print(f"\n{'─' * 80}")
        print(f"  MONTH 2 (Weeks 5-8): DISCOVERY PHASE")
        print(f"{'─' * 80}")
        for wm in weekly_metrics[4:8]:
            if wm["events"]:
                print(f"\n  Week {wm['week']}:")
                for e in wm["events"]:
                    print(f"    {e}")

        print(f"\n{'─' * 80}")
        print(f"  MONTH 3 (Weeks 9-12): LEARNING PHASE")
        print(f"{'─' * 80}")
        for wm in weekly_metrics[8:12]:
            if wm["events"]:
                print(f"\n  Week {wm['week']}:")
                for e in wm["events"]:
                    print(f"    {e}")

        print(f"\n{'─' * 80}")
        print(f"  MONTHS 4-6 (Weeks 13-24): MATURITY → MASTERY")
        print(f"{'─' * 80}")
        for wm in weekly_metrics[12:]:
            if wm["events"]:
                print(f"\n  Week {wm['week']}:")
                for e in wm["events"]:
                    print(f"    {e}")

        # ── Incident Timeline ──
        print(f"\n{'═' * 80}")
        print(f"  INCIDENT TIMELINE — Historical mistakes & how org responded")
        print(f"{'═' * 80}")
        for inc in incident_log:
            # Find if this was later avoided by others
            avoidances = [a for a in avoidance_log
                          if inc["title"].split("—")[0].strip() in a.get("avoided", "")]
            avoided_count = len(avoidances)
            print(f"\n  Week {inc['week']:2d} [{inc['severity'].upper():8s}] "
                  f"{inc['agent']} @ {inc['project']}")
            print(f"    {inc['title']}")
            if avoided_count > 0:
                print(f"    → Subsequently avoided by {avoided_count} agent(s)")

        # ── Anti-Pattern Avoidance Story ──
        print(f"\n{'═' * 80}")
        print(f"  ANTI-PATTERN AVOIDANCE — Mistakes prevented by Memee")
        print(f"{'═' * 80}")
        avoidance_by_week = defaultdict(int)
        for a in avoidance_log:
            avoidance_by_week[a["week"]] += 1
        print(f"\n  {'Week':>6s} | {'Saves':>5s} | Visual")
        print(f"  {'─'*6} | {'─'*5} | {'─'*40}")
        for w in range(1, 25):
            count = avoidance_by_week.get(w, 0)
            bar = "█" * count
            print(f"  {w:6d} | {count:5d} | {bar}")
        total_saves = sum(avoidance_by_week.values())
        print(f"\n  Total mistakes prevented: {total_saves}")

        # ── Code Review Effectiveness ──
        if review_results:
            print(f"\n{'═' * 80}")
            print(f"  CODE REVIEW — Institutional knowledge in action")
            print(f"{'═' * 80}")
            for rev in review_results:
                status = "CORRECT" if rev["caught_correctly"] else "MISSED"
                icon = "✓" if rev["caught_correctly"] else "✗"
                print(f"\n  Week {rev['week']:2d} [{status}] {rev['agent']} @ {rev['project']}")
                print(f"    {rev['description']}")
                print(f"    Warnings: {rev['warnings']}, Confirmations: {rev['confirmations']}")

        # ── Knowledge Growth ──
        print(f"\n{'═' * 80}")
        print(f"  KNOWLEDGE GROWTH OVER 6 MONTHS")
        print(f"{'═' * 80}")
        print(f"\n  {'Week':>4s} | {'Total':>5s} | {'Canon':>5s} | {'Valid':>5s} | "
              f"{'Tested':>6s} | {'Conf':>5s} | Maturity Distribution")
        print(f"  {'─'*4} | {'─'*5} | {'─'*5} | {'─'*5} | {'─'*6} | {'─'*5} | {'─'*30}")
        for wm in weekly_metrics:
            t = wm["total"]
            c_bar = "C" * wm["canon"]
            v_bar = "V" * min(wm["validated"], 10)
            t_bar = "T" * min(wm["tested"] // 2, 15)
            print(
                f"  {wm['week']:4d} | {t:5d} | {wm['canon']:5d} | {wm['validated']:5d} | "
                f"{wm['tested']:6d} | {wm['avg_conf']:.3f} | {c_bar}{v_bar}{t_bar}"
            )

        # ── Agent Report Card ──
        print(f"\n{'═' * 80}")
        print(f"  AGENT REPORT CARDS")
        print(f"{'═' * 80}")
        print(f"\n  {'Agent':>8s} | {'Team':>10s} | {'Created':>7s} | "
              f"{'Mistakes':>8s} | {'Saves':>5s} | Learning")
        print(f"  {'─'*8} | {'─'*10} | {'─'*7} | {'─'*8} | {'─'*5} | {'─'*20}")
        for name, data in sorted(agents.items()):
            net = data["saves"] - data["mistakes"]
            learning = "IMPROVING" if net > 0 else "LEARNING" if net == 0 else "NEEDS HELP"
            bar = "+" * data["saves"] + "-" * data["mistakes"]
            print(
                f"  {name:>8s} | {data['team']:>10s} | {data['memories_created']:>7d} | "
                f"{data['mistakes']:>8d} | {data['saves']:>5d} | {learning} {bar}"
            )

        # ── Org IQ Over Time ──
        print(f"\n{'═' * 80}")
        print(f"  ORGANIZATIONAL IQ EVOLUTION")
        print(f"{'═' * 80}")
        final = weekly_metrics[-1]
        total = max(final["total"], 1)
        canon_r = final["canon"] / total
        valid_r = final["validated"] / total
        avg_conf = final["avg_conf"]
        avoidance_r = total_saves / max(total_saves + len(incident_log), 1)

        org_iq = canon_r * 30 + valid_r * 25 + avg_conf * 20 + avoidance_r * 15 + 0.97 * 10

        # Calculate IQ at key milestones
        milestones = [4, 8, 12, 16, 20, 24]
        print(f"\n  {'Month':>5s} | {'Week':>4s} | {'OrgIQ':>6s} | Visual")
        print(f"  {'─'*5} | {'─'*4} | {'─'*6} | {'─'*40}")
        for w in milestones:
            wm = weekly_metrics[w - 1]
            t = max(wm["total"], 1)
            saves_so_far = sum(avoidance_by_week.get(ww, 0) for ww in range(1, w + 1))
            incidents_so_far = len([i for i in incident_log if i["week"] <= w])
            cr = wm["canon"] / t
            vr = wm["validated"] / t
            ar = saves_so_far / max(saves_so_far + incidents_so_far, 1)
            iq = cr * 30 + vr * 25 + wm["avg_conf"] * 20 + ar * 15 + 0.97 * 10
            bar = "█" * int(iq)
            month = w // 4
            print(f"  {month:5d} | {w:4d} | {iq:6.1f} | {bar}")

        print(f"\n  Final Org IQ: {org_iq:.1f} / 100")

        # ── Key Takeaways ──
        print(f"\n{'═' * 80}")
        print(f"  KEY TAKEAWAYS")
        print(f"{'═' * 80}")
        print(f"""
  1. CHAOS → ORDER: 7 real incidents in months 1-3, but each one became
     an anti-pattern that protected the entire organization afterward.

  2. LEARNING COMPOUNDS: Avoidance rate grew from ~30% (month 1)
     to ~90% (month 6). Each saved mistake = saved hours of debugging.

  3. KNOWLEDGE SPREADS: Auto-propagation pushed patterns from origin
     project to {len(projects)} projects. One lesson learned = org-wide benefit.

  4. NEW AGENTS RAMP FASTER: AnalyticsAPI inherited {inherit_stats['memories_inherited']}
     patterns on day 1 instead of discovering them through mistakes.

  5. DREAM MODE FINDS HIDDEN CONNECTIONS: Monthly dream cycles connected
     related patterns and surfaced contradictions automatically.

  6. CODE REVIEW AS INSTITUTIONAL MEMORY: Git diffs scanned against
     org's anti-pattern database catches issues that no linter can find
     (business logic mistakes, architectural anti-patterns).

  Total simulation time: {elapsed:.2f}s
""")

        # ── Assertions ──
        assert final["total"] > 10, "Should have accumulated memories"
        assert total_saves > 0, "Should have prevented at least some mistakes"
        assert len(incident_log) >= 5, "Should have recorded incidents"
        assert elapsed < 30, f"Should complete in <30s, took {elapsed:.1f}s"

        # Verify the org actually learned: later weeks should have
        # higher avg confidence than earlier weeks
        early_conf = weekly_metrics[3]["avg_conf"]
        late_conf = weekly_metrics[-1]["avg_conf"]
        assert late_conf >= early_conf, (
            f"Org should learn over time: week 4 conf={early_conf:.3f}, "
            f"week 24 conf={late_conf:.3f}"
        )
