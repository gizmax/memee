"""Demo data generator — populates Memee with realistic enterprise-scale data.

Creates a virtual company with 30 projects, 20 agents, 52 weeks of data.
Then opens the dashboard to visualize learning.
"""

from __future__ import annotations

import random
import time

from sqlalchemy import func

from memee.engine.confidence import update_confidence
from memee.engine.dream import run_dream_cycle
from memee.engine.inheritance import inherit_memories
from memee.engine.lifecycle import run_aging_cycle
from memee.engine.propagation import run_propagation_cycle
from memee.storage.database import get_session, init_db
from memee.storage.models import (
    AntiPattern,
    Decision,
    LearningSnapshot,
    MaturityLevel,
    Memory,
    MemoryType,
    MemoryValidation,
    Organization,
    Project,
    ProjectMemory,
)

random.seed(2026)

STACKS = [
    ("Python", "FastAPI", "PostgreSQL"),
    ("Python", "FastAPI", "SQLite"),
    ("Python", "Flask", "SQLite"),
    ("Python", "pandas", "Airflow"),
    ("Python", "Django", "PostgreSQL"),
    ("React", "TypeScript", "Tailwind"),
    ("React", "TypeScript", "Recharts"),
    ("Next.js", "TypeScript", "Prisma"),
    ("Swift", "SwiftUI", "CoreData"),
    ("Kotlin", "Jetpack Compose", "Room"),
    ("Go", "Gin", "PostgreSQL"),
    ("Rust", "Actix", "SQLite"),
]

PATTERNS = [
    ("Always use timeout on HTTP requests", ["python", "http", "reliability"]),
    ("SQLite WAL mode for concurrent reads", ["sqlite", "database", "performance"]),
    ("Use connection pooling", ["database", "performance", "python"]),
    ("Pydantic model_validate over manual parsing", ["python", "pydantic", "api"]),
    ("Pre-commit hooks catch issues before CI", ["python", "ci", "quality"]),
    ("Structured logging with correlation IDs", ["python", "logging", "observability"]),
    ("Circuit breaker for external APIs", ["python", "resilience", "api"]),
    ("Validate all user input at API boundary", ["security", "api", "validation"]),
    ("Use TypeScript strict mode", ["typescript", "frontend", "safety"]),
    ("React useEffect cleanup prevents leaks", ["react", "frontend", "hooks"]),
    ("Tailwind @apply for repeated patterns", ["tailwind", "css", "frontend"]),
    ("SwiftUI .task for async data loading", ["swift", "swiftui", "async"]),
    ("Index foreign keys in SQLite", ["sqlite", "database", "indexing"]),
    ("FastAPI Depends for session injection", ["python", "fastapi", "di"]),
    ("Use async/await for I/O-bound work", ["python", "async", "performance"]),
    ("Retry logic for 5xx errors", ["python", "http", "reliability"]),
    ("Cache expensive computations with TTL", ["performance", "caching", "python"]),
    ("Database migrations for schema changes", ["database", "deployment", "safety"]),
    ("Graceful shutdown for servers", ["python", "deployment", "reliability"]),
    ("Feature flags for gradual rollouts", ["deployment", "process", "safety"]),
    ("Use semantic versioning", ["versioning", "deployment", "process"]),
    ("Health check endpoints", ["api", "monitoring", "devops"]),
    ("Rate limiting on public APIs", ["api", "security", "performance"]),
    ("CORS configuration for frontend origins", ["api", "security", "frontend"]),
    ("Use environment variables for secrets", ["security", "config", "deployment"]),
]

ANTI_PATTERNS = [
    ("Never store API keys in source code", "critical", ["security", "secrets", "python"]),
    ("Don't use requests without timeout", "high", ["python", "http", "reliability"]),
    ("Avoid N+1 queries in ORM", "high", ["database", "performance", "python"]),
    ("Never use eval() on user input", "critical", ["python", "security"]),
    ("Don't catch bare Exception", "medium", ["python", "error-handling"]),
    ("Avoid synchronous I/O in async code", "high", ["python", "async", "performance"]),
    ("Never use dangerouslySetInnerHTML with user input", "critical", ["react", "security", "xss"]),
    ("Don't use componentDidMount", "medium", ["react", "deprecated", "frontend"]),
    ("Don't use inline styles in React", "low", ["react", "css", "frontend"]),
    ("SwiftUI gesture and offset must be on same view", "high", ["swift", "swiftui", "ui"]),
    ("Don't use SELECT * in production", "medium", ["database", "performance"]),
    ("Don't hardcode database credentials", "critical", ["security", "database"]),
    ("Avoid circular imports", "medium", ["python", "architecture"]),
    ("Don't use git reset --hard in automation", "critical", ["git", "automation"]),
    ("Never run migrations without backup", "high", ["database", "deployment", "safety"]),
]


def generate_demo_data(weeks: int = 52, org_name: str = "NovaTech-Enterprise"):
    """Generate enterprise-scale demo data."""
    engine = init_db()
    session = get_session(engine)

    # Clean existing data
    existing = session.query(Organization).filter_by(name=org_name).first()
    if existing:
        print(f"  Organization '{org_name}' already exists. Skipping generation.")
        return

    print(f"  Generating {weeks}-week enterprise simulation...")
    start = time.time()

    # Create org
    org = Organization(name=org_name)
    session.add(org)
    session.flush()

    # Create 30 projects
    projects = []
    team_names = ["backend", "frontend", "data", "mobile", "devops", "security",
                  "platform", "integrations", "analytics", "infra"]
    for i in range(30):
        stack = list(random.choice(STACKS))
        team = team_names[i % len(team_names)]
        proj = Project(
            organization_id=org.id,
            name=f"{team.title()}-{i:02d}",
            path=f"/enterprise/{team}-{i:02d}",
            stack=stack,
            tags=[team, stack[0].lower()],
        )
        session.add(proj)
        projects.append(proj)

    session.flush()

    # Create 20 agents
    agent_names = [f"agent-{chr(65+i)}{chr(65+j)}"
                   for i in range(4) for j in range(5)]

    all_memories = []
    all_ap_memories = []

    for week in range(1, weeks + 1):
        maturity_mult = 1.0 + (week / weeks) * 0.8
        validation_accuracy = min(0.60 + week * 0.007, 0.92)

        # Record patterns
        n_patterns = int(8 * maturity_mult)
        for _ in range(n_patterns):
            title, tags = random.choice(PATTERNS)
            proj = random.choice(projects)
            agent = random.choice(agent_names)
            m = Memory(
                type=MemoryType.PATTERN.value,
                title=f"{title} (W{week}-{random.randint(1,9999)})",
                content=f"Week {week}: {title}. Discovered by {agent} on {proj.name}.",
                tags=tags,
                source_agent=agent,
            )
            session.add(m)
            session.flush()
            pm = ProjectMemory(project_id=proj.id, memory_id=m.id)
            session.add(pm)
            all_memories.append(m)

        # Record anti-patterns (more in early weeks — incidents happen early)
        n_aps = max(1, int(4 * (1.5 - week / weeks)))
        for _ in range(n_aps):
            title, severity, tags = random.choice(ANTI_PATTERNS)
            proj = random.choice(projects)
            agent = random.choice(agent_names)
            am = Memory(
                type=MemoryType.ANTI_PATTERN.value,
                title=f"{title} (W{week}-{random.randint(1,9999)})",
                content=f"Anti-pattern: {title}",
                tags=tags,
                source_agent=agent,
            )
            session.add(am)
            session.flush()
            ap = AntiPattern(
                memory_id=am.id, severity=severity,
                trigger=f"When {title.lower()}", consequence="Known failure",
                alternative="See organizational best practices",
            )
            session.add(ap)
            session.flush()
            pm = ProjectMemory(project_id=proj.id, memory_id=am.id)
            session.add(pm)
            all_memories.append(am)
            all_ap_memories.append(am)

        # Decisions
        for _ in range(2):
            chosen = random.choice(["PostgreSQL", "SQLite", "FastAPI", "React", "Tailwind"])
            proj = random.choice(projects)
            agent = random.choice(agent_names)
            dm = Memory(
                type=MemoryType.DECISION.value,
                title=f"Decision: {chosen} (W{week})",
                content=f"Chose {chosen}",
                tags=["decision"],
                source_agent=agent,
            )
            session.add(dm)
            session.flush()
            dec = Decision(memory_id=dm.id, chosen=chosen, alternatives=[])
            session.add(dec)
            pm = ProjectMemory(project_id=proj.id, memory_id=dm.id)
            session.add(pm)
            all_memories.append(dm)

        session.commit()

        # Cross-project validations
        n_validations = int(12 * maturity_mult)
        patterns = [m for m in all_memories if m.type == MemoryType.PATTERN.value]
        for _ in range(min(n_validations, len(patterns))):
            m = random.choice(patterns)
            proj = random.choice(projects)
            validated = random.random() < validation_accuracy
            v = MemoryValidation(
                memory_id=m.id, project_id=proj.id, validated=validated,
            )
            session.add(v)
            update_confidence(m, validated, proj.id)

        session.commit()

        # Auto-propagation (start week 4)
        if week >= 4 and week % 2 == 0:
            run_propagation_cycle(session, confidence_threshold=0.52, max_propagations=50)

        # Dream mode (monthly)
        if week % 4 == 0:
            run_dream_cycle(session)
        elif week % 2 == 0:
            run_aging_cycle(session)

        # New project onboarding with inheritance (every 8 weeks)
        if week % 8 == 0 and week < weeks:
            new_proj = random.choice(projects)
            inherit_memories(session, new_proj, min_memory_confidence=0.55)

        # Take snapshot
        total = session.query(func.count(Memory.id)).scalar()
        canon = session.query(func.count(Memory.id)).filter(
            Memory.maturity == MaturityLevel.CANON.value).scalar()
        hypo = session.query(func.count(Memory.id)).filter(
            Memory.maturity == MaturityLevel.HYPOTHESIS.value).scalar()
        depr = session.query(func.count(Memory.id)).filter(
            Memory.maturity == MaturityLevel.DEPRECATED.value).scalar()
        avg_conf = session.query(func.avg(Memory.confidence_score)).scalar() or 0
        validated_count = session.query(func.count(Memory.id)).filter(
            Memory.maturity == MaturityLevel.VALIDATED.value).scalar()

        snap = LearningSnapshot(
            total_memories=total,
            canon_memories=canon,
            hypothesis_memories=hypo,
            deprecated_memories=depr,
            avg_confidence=avg_conf,
            learning_rate=validated_count / max(total, 1),
        )
        session.add(snap)
        session.commit()

        if week % 10 == 0 or week == 1:
            print(f"    Week {week:3d}/{weeks}: {total} memories, "
                  f"conf={avg_conf:.3f}, canon={canon}")

    elapsed = time.time() - start
    total = session.query(func.count(Memory.id)).scalar()
    print(f"  Done! {total} memories generated in {elapsed:.1f}s")
    session.close()
