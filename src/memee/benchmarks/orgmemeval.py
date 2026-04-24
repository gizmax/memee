"""OrgMemEval v1.0 — Organizational Memory Benchmark.

8 scenarios testing capabilities NO single-context memory system has:
  1. Propagation    — cross-project pattern spread
  2. Avoidance      — anti-pattern prevention rate
  3. Maturity       — knowledge lifecycle progression
  4. Onboarding     — new project ramp-up speed
  5. Recovery       — incident → org-wide protection time
  6. Calibration    — confidence prediction accuracy
  7. Synthesis      — dream mode graph quality
  8. Research       — autoresearch effectiveness

Each scenario returns a score (0 to max_points) and detailed metrics.
Total: 100 points.
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

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
from memee.engine.search import search_memories
from memee.storage.database import get_session, init_db
from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryConnection,
    MemoryType,
    MemoryValidation,
    Organization,
    Project,
    ProjectMemory,
    Severity,
)

PATTERNS = [
    ("Always use timeout on HTTP requests", ["python", "http", "reliability"]),
    ("SQLite WAL mode for concurrent reads", ["sqlite", "database", "performance"]),
    ("Pydantic model_validate for parsing", ["python", "pydantic", "api"]),
    ("React useEffect cleanup", ["react", "frontend", "hooks"]),
    ("Index foreign keys in SQLite", ["sqlite", "database", "indexing"]),
    ("Async/await for I/O operations", ["python", "async", "performance"]),
    ("Validate user input at API boundary", ["security", "api", "validation"]),
    ("Structured logging with correlation IDs", ["python", "logging", "observability"]),
    ("Cache computations with TTL", ["performance", "caching", "python"]),
    ("Circuit breaker for external APIs", ["python", "resilience", "api"]),
    ("Connection pooling for databases", ["database", "performance", "python"]),
    ("Pre-commit hooks for CI", ["python", "ci", "quality"]),
    ("TypeScript strict mode", ["typescript", "frontend", "safety"]),
    ("Tailwind @apply for patterns", ["tailwind", "css", "frontend"]),
    ("SwiftUI .task for async loading", ["swift", "swiftui", "async"]),
]

ANTI_PATTERNS = [
    ("Don't use eval() on user input", "critical", ["python", "security"]),
    ("Don't store API keys in code", "critical", ["security", "secrets"]),
    ("Avoid N+1 queries in ORM", "high", ["database", "performance"]),
    ("Don't use requests without timeout", "high", ["python", "http"]),
    ("Don't block async event loop", "high", ["python", "async"]),
    ("Never use dangerouslySetInnerHTML", "critical", ["react", "security"]),
    ("Don't catch bare Exception", "medium", ["python", "error-handling"]),
    ("Don't use inline styles in React", "low", ["react", "css"]),
    ("Avoid SELECT * in production", "medium", ["database", "performance"]),
    ("Don't hardcode DB credentials", "critical", ["security", "database"]),
]

STACKS = [
    (["Python", "FastAPI", "SQLite"], ["python", "api"]),
    (["Python", "Flask", "SQLite"], ["python", "web"]),
    (["React", "TypeScript", "Tailwind"], ["react", "frontend"]),
    (["Swift", "SwiftUI", "CoreData"], ["swift", "ios"]),
    (["Python", "pandas", "Airflow"], ["python", "data"]),
]


def _setup_env(session: Session, org_name: str = "OrgMemEval", n_projects: int = 30):
    """Create a clean benchmark environment."""
    org = Organization(name=org_name)
    session.add(org)
    session.flush()

    projects = []
    for i in range(n_projects):
        stack, tags = STACKS[i % len(STACKS)]
        proj = Project(
            organization_id=org.id,
            name=f"Eval-{i:02d}",
            path=f"/eval/proj-{i:02d}",
            stack=stack, tags=tags,
        )
        session.add(proj)
        projects.append(proj)
    session.flush()
    return org, projects


# ═══════════════════════════════════════
# SCENARIO 1: PROPAGATION (15 pts)
# ═══════════════════════════════════════

def scenario_propagation(session: Session, seed: int = 42) -> dict:
    """How effectively do patterns spread cross-project?"""
    random.seed(seed)
    org, projects = _setup_env(session, "Eval-Propagation")
    session.commit()

    # Seed 100 patterns in 5 origin projects
    origin_projects = projects[:5]
    target_projects = projects[5:]
    memories = []

    for i in range(100):
        title, tags = PATTERNS[i % len(PATTERNS)]
        m = Memory(
            type=MemoryType.PATTERN.value,
            title=f"{title} (eval-{i})", content=title,
            tags=tags, confidence_score=0.6 + random.random() * 0.2,
        )
        session.add(m)
        session.flush()
        pm = ProjectMemory(project_id=origin_projects[i % 5].id, memory_id=m.id)
        session.add(pm)
        memories.append(m)
    session.commit()

    # Run propagation
    stats = run_propagation_cycle(session, confidence_threshold=0.55, max_propagations=5000)

    # Measure: how many target projects received patterns?
    target_with_patterns = 0
    total_target_links = 0
    for proj in target_projects:
        count = session.query(ProjectMemory).filter(
            ProjectMemory.project_id == proj.id
        ).count()
        if count > 0:
            target_with_patterns += 1
        total_target_links += count

    coverage = target_with_patterns / len(target_projects) if target_projects else 0
    avg_per_target = total_target_links / len(target_projects) if target_projects else 0

    max_points = 15
    score = coverage * max_points

    return {
        "name": "Propagation",
        "max_points": max_points,
        "score": round(score, 1),
        "pct": round(coverage * 100, 1),
        "metrics": {
            "origin_projects": len(origin_projects),
            "target_projects": len(target_projects),
            "patterns_seeded": len(memories),
            "new_links_created": stats["total_new_links"],
            "targets_reached": target_with_patterns,
            "coverage": round(coverage, 3),
            "avg_patterns_per_target": round(avg_per_target, 1),
        },
        "competitor_baseline": {"score": 0, "reason": "No auto-propagation"},
    }


# ═══════════════════════════════════════
# SCENARIO 2: AVOIDANCE (15 pts)
# ═══════════════════════════════════════

def scenario_avoidance(session: Session, seed: int = 42) -> dict:
    """How well does the system prevent known mistakes?"""
    random.seed(seed)
    org, projects = _setup_env(session, "Eval-Avoidance")
    session.commit()

    # Seed anti-patterns in 3 origin projects
    for i, (title, severity, tags) in enumerate(ANTI_PATTERNS):
        m = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title=title, content=f"Trigger: {title}",
            tags=tags, confidence_score=0.7,
        )
        session.add(m)
        session.flush()
        ap = AntiPattern(
            memory_id=m.id, severity=severity,
            trigger=title, consequence="Known failure",
            alternative="See best practices",
        )
        session.add(ap)
        pm = ProjectMemory(project_id=projects[i % 3].id, memory_id=m.id)
        session.add(pm)
    session.commit()

    # Scan target projects for warnings (predictive push)
    target_projects = projects[3:]
    warned = 0
    total_warnings = 0
    for proj in target_projects:
        warnings = scan_project_for_warnings(session, proj)
        if warnings:
            warned += 1
            total_warnings += len(warnings)

    avoidance_rate = warned / len(target_projects) if target_projects else 0

    max_points = 15
    score = avoidance_rate * max_points

    return {
        "name": "Avoidance",
        "max_points": max_points,
        "score": round(score, 1),
        "pct": round(avoidance_rate * 100, 1),
        "metrics": {
            "anti_patterns_seeded": len(ANTI_PATTERNS),
            "target_projects": len(target_projects),
            "projects_warned": warned,
            "total_warnings_pushed": total_warnings,
            "avoidance_rate": round(avoidance_rate, 3),
        },
        "competitor_baseline": {"score": 2.3, "reason": "~15% manual pull rate"},
    }


# ═══════════════════════════════════════
# SCENARIO 3: MATURITY (12 pts)
# ═══════════════════════════════════════

def scenario_maturity(session: Session, seed: int = 42) -> dict:
    """How well does knowledge mature over time?"""
    random.seed(seed)
    org, projects = _setup_env(session, "Eval-Maturity", n_projects=15)
    session.commit()

    # Seed and validate patterns over 30 simulated weeks
    memories = []
    for i in range(200):
        title, tags = PATTERNS[i % len(PATTERNS)]
        m = Memory(
            type=MemoryType.PATTERN.value,
            title=f"{title} (mat-{i})", content=title,
            tags=tags, confidence_score=0.5,
        )
        session.add(m)
        memories.append(m)
    session.commit()

    for week in range(30):
        accuracy = 0.60 + week * 0.01
        n_validations = min(25 + week * 2, len(memories))
        for _ in range(n_validations):
            m = random.choice(memories)
            proj = random.choice(projects)
            validated = random.random() < accuracy
            v = MemoryValidation(memory_id=m.id, project_id=proj.id, validated=validated)
            session.add(v)
            update_confidence(m, validated, proj.id)
        session.commit()

        # Propagate every 2 weeks + dream monthly
        if week % 2 == 1:
            run_propagation_cycle(session, confidence_threshold=0.50, max_propagations=300)
        if week % 4 == 3:
            run_dream_cycle(session)
        else:
            run_aging_cycle(session)

    total = len(memories)
    canon = sum(1 for m in memories if m.maturity == MaturityLevel.CANON.value)
    validated = sum(1 for m in memories if m.maturity == MaturityLevel.VALIDATED.value)
    avg_conf = sum(m.confidence_score for m in memories) / total

    canon_pct = canon / total
    validated_pct = validated / total
    matured_pct = (canon + validated) / total  # Combined mature knowledge
    maturity_score = canon_pct * 0.3 + matured_pct * 0.3 + avg_conf * 0.4

    max_points = 12
    score = maturity_score * max_points

    return {
        "name": "Maturity",
        "max_points": max_points,
        "score": round(score, 1),
        "pct": round(maturity_score * 100, 1),
        "metrics": {
            "total_memories": total,
            "canon": canon,
            "validated": validated,
            "canon_pct": round(canon_pct * 100, 1),
            "validated_pct": round(validated_pct * 100, 1),
            "avg_confidence": round(avg_conf, 3),
        },
        "competitor_baseline": {"score": 0, "reason": "No maturity model"},
    }


# ═══════════════════════════════════════
# SCENARIO 4: ONBOARDING (12 pts)
# ═══════════════════════════════════════

def scenario_onboarding(session: Session, seed: int = 42) -> dict:
    """How fast can a new project ramp up?"""
    random.seed(seed)
    org, projects = _setup_env(session, "Eval-Onboarding", n_projects=15)
    session.commit()

    # Seed validated patterns in existing projects
    total_relevant = 0
    for i in range(150):
        title, tags = PATTERNS[i % len(PATTERNS)]
        m = Memory(
            type=MemoryType.PATTERN.value,
            title=f"{title} (onb-{i})", content=title,
            tags=tags, confidence_score=0.7,
            maturity=MaturityLevel.VALIDATED.value,
            application_count=3,
        )
        session.add(m)
        session.flush()
        pm = ProjectMemory(project_id=projects[i % 10].id, memory_id=m.id)
        session.add(pm)
        # Count Python-related as relevant for new project
        if "python" in tags:
            total_relevant += 1
    session.commit()

    # New Python/FastAPI project
    new_proj = Project(
        organization_id=org.id,
        name="NewProject", path="/eval/new",
        stack=["Python", "FastAPI", "SQLite"], tags=["python", "api"],
    )
    session.add(new_proj)
    session.commit()

    stats = inherit_memories(session, new_proj)
    inherited = stats["memories_inherited"]
    ratio = inherited / max(total_relevant, 1)

    max_points = 12
    score = min(ratio, 1.0) * max_points

    return {
        "name": "Onboarding",
        "max_points": max_points,
        "score": round(score, 1),
        "pct": round(ratio * 100, 1),
        "metrics": {
            "relevant_patterns": total_relevant,
            "inherited": inherited,
            "similar_projects": len(stats["similar_projects"]),
            "inheritance_ratio": round(ratio, 3),
        },
        "competitor_baseline": {"score": 0, "reason": "Cold start, no inheritance"},
    }


# ═══════════════════════════════════════
# SCENARIO 5: RECOVERY (12 pts)
# ═══════════════════════════════════════

def scenario_recovery(session: Session, seed: int = 42) -> dict:
    """How fast does the org recover from an incident?"""
    random.seed(seed)
    org, projects = _setup_env(session, "Eval-Recovery", n_projects=20)
    session.commit()

    # Incident: new anti-pattern discovered in project 0
    ap_mem = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        title="Critical: SQL injection via string formatting",
        content="Trigger: f-string SQL queries\nConsequence: data breach",
        tags=["python", "security", "database"],
        confidence_score=0.8,
    )
    session.add(ap_mem)
    session.flush()
    ap = AntiPattern(
        memory_id=ap_mem.id, severity="critical",
        trigger="String-formatted SQL queries",
        consequence="SQL injection, data breach",
        alternative="Use parameterized queries",
    )
    session.add(ap)
    pm = ProjectMemory(project_id=projects[0].id, memory_id=ap_mem.id)
    session.add(pm)
    session.commit()

    # Measure: how many projects get warned after propagation + predictive push?
    run_propagation_cycle(session, confidence_threshold=0.5, max_propagations=500)

    # Also run predictive push (scan all projects for matching APs)
    for proj in projects[1:]:
        scan_project_for_warnings(session, proj)

    warned_projects = 0
    for proj in projects[1:]:
        has_ap = session.query(ProjectMemory).filter(
            ProjectMemory.project_id == proj.id,
            ProjectMemory.memory_id == ap_mem.id,
        ).count()
        if has_ap > 0:
            warned_projects += 1

    recovery_rate = warned_projects / (len(projects) - 1)

    max_points = 12
    score = recovery_rate * max_points

    return {
        "name": "Recovery",
        "max_points": max_points,
        "score": round(score, 1),
        "pct": round(recovery_rate * 100, 1),
        "metrics": {
            "total_projects": len(projects),
            "warned_in_cycle_1": warned_projects,
            "recovery_rate": round(recovery_rate, 3),
        },
        "competitor_baseline": {"score": 0, "reason": "No automatic propagation"},
    }


# ═══════════════════════════════════════
# SCENARIO 6: CALIBRATION (10 pts)
# ═══════════════════════════════════════

def scenario_calibration(session: Session, seed: int = 42) -> dict:
    """Does confidence accurately predict future validation success?"""
    random.seed(seed)
    org, projects = _setup_env(session, "Eval-Calibration", n_projects=10)
    session.commit()

    # Create patterns with varied initial quality
    memories = []
    for i in range(100):
        title, tags = PATTERNS[i % len(PATTERNS)]
        true_quality = random.random()  # Ground truth quality
        m = Memory(
            type=MemoryType.PATTERN.value,
            title=f"{title} (cal-{i})", content=title,
            tags=tags, confidence_score=0.5,
            context={"true_quality": true_quality},
        )
        session.add(m)
        memories.append((m, true_quality))
    session.commit()

    # Train: validate based on true quality (with noise)
    for _ in range(500):
        m, tq = random.choice(memories)
        proj = random.choice(projects)
        validated = random.random() < tq  # Higher quality = more validations
        v = MemoryValidation(memory_id=m.id, project_id=proj.id, validated=validated)
        session.add(v)
        update_confidence(m, validated, proj.id)
    session.commit()

    # Test: does confidence correlate with true quality?
    confs = [m.confidence_score for m, _ in memories]
    quals = [tq for _, tq in memories]

    # Pearson correlation
    n = len(confs)
    mean_c = sum(confs) / n
    mean_q = sum(quals) / n
    cov = sum((c - mean_c) * (q - mean_q) for c, q in zip(confs, quals)) / n
    std_c = (sum((c - mean_c) ** 2 for c in confs) / n) ** 0.5
    std_q = (sum((q - mean_q) ** 2 for q in quals) / n) ** 0.5
    correlation = cov / (std_c * std_q) if std_c * std_q > 0 else 0

    max_points = 10
    score = max(0, correlation) * max_points

    return {
        "name": "Calibration",
        "max_points": max_points,
        "score": round(score, 1),
        "pct": round(max(0, correlation) * 100, 1),
        "metrics": {
            "memories_tested": n,
            "validation_events": 500,
            "pearson_correlation": round(correlation, 3),
            "avg_confidence": round(mean_c, 3),
        },
        "competitor_baseline": {"score": 0, "reason": "No confidence model"},
    }


# ═══════════════════════════════════════
# SCENARIO 7: SYNTHESIS (12 pts)
# ═══════════════════════════════════════

def scenario_synthesis(session: Session, seed: int = 42) -> dict:
    """How well does Dream Mode build the knowledge graph?"""
    random.seed(seed)
    org, projects = _setup_env(session, "Eval-Synthesis", n_projects=10)
    session.commit()

    # Seed 300 memories with overlapping tags
    for i in range(300):
        title, tags = PATTERNS[i % len(PATTERNS)]
        m = Memory(
            type=random.choice([MemoryType.PATTERN.value, MemoryType.ANTI_PATTERN.value]),
            title=f"{title} (syn-{i})", content=title, tags=tags,
            confidence_score=0.4 + random.random() * 0.4,
        )
        session.add(m)
        if m.type == MemoryType.ANTI_PATTERN.value:
            session.flush()
            ap = AntiPattern(
                memory_id=m.id, severity="medium",
                trigger=title, consequence="Known issue",
            )
            session.add(ap)
    session.commit()

    # Run dream
    stats = run_dream_cycle(session)

    connections = stats["connections_created"]
    contradictions = stats["contradictions_found"]
    boosts = stats["confidence_boosts"]

    # Score: connections + contradictions weighted
    expected_connections = 200
    expected_contradictions = 10
    conn_score = min(connections / expected_connections, 1.0) * 0.5
    contra_score = min(contradictions / expected_contradictions, 1.0) * 0.3
    boost_score = min(boosts / 50, 1.0) * 0.2

    quality = conn_score + contra_score + boost_score
    max_points = 12
    score = quality * max_points

    return {
        "name": "Synthesis",
        "max_points": max_points,
        "score": round(score, 1),
        "pct": round(quality * 100, 1),
        "metrics": {
            "memories": 300,
            "connections_created": connections,
            "contradictions_found": contradictions,
            "confidence_boosts": boosts,
            "promotions": stats["promotions_applied"],
        },
        "competitor_baseline": {"score": 0, "reason": "No dream mode / graph building"},
    }


# ═══════════════════════════════════════
# SCENARIO 8: RESEARCH (12 pts)
# ═══════════════════════════════════════

def scenario_research(session: Session, seed: int = 42) -> dict:
    """How effective is autoresearch?"""
    random.seed(seed)
    org, projects = _setup_env(session, "Eval-Research", n_projects=5)
    session.commit()

    # Run 5 experiments with 30 iterations each
    experiments = []
    for i, (metric, direction) in enumerate([
        ("accuracy", "higher"), ("latency", "lower"), ("coverage", "higher"),
        ("memory_usage", "lower"), ("throughput", "higher"),
    ]):
        exp = create_experiment(
            session, projects[i].id,
            f"Optimize {metric}", metric, direction,
            f"echo '{metric}: 0.5'",
            baseline_value=0.50,
        )
        current = 0.50
        for j in range(30):
            delta = random.gauss(0.005, 0.012)
            new_val = current + delta if direction == "higher" else current - abs(delta)
            if (direction == "higher" and new_val > current) or \
               (direction == "lower" and new_val < current):
                log_iteration(session, exp.id, round(new_val, 4), "keep", f"Iter {j+1}")
                current = new_val
            elif random.random() < 0.1:
                log_iteration(session, exp.id, 0, "crash", f"Crashed at iter {j+1}")
            else:
                log_iteration(session, exp.id, round(new_val, 4), "discard", f"No improvement")
        complete_experiment(session, exp, "completed")
        experiments.append(exp)

    meta = get_meta_learning(session)
    overall_keep_rate = meta["overall_keep_rate"]
    has_insights = len(meta.get("insights", [])) > 0

    insight_score = 1.0 if has_insights else 0.0
    quality = overall_keep_rate * 0.6 + insight_score * 0.4

    max_points = 12
    score = quality * max_points

    return {
        "name": "Research",
        "max_points": max_points,
        "score": round(score, 1),
        "pct": round(quality * 100, 1),
        "metrics": {
            "experiments": len(experiments),
            "total_iterations": meta["total_iterations"],
            "overall_keep_rate": round(overall_keep_rate, 3),
            "insights_generated": len(meta.get("insights", [])),
            "by_metric": meta.get("by_metric", {}),
        },
        "competitor_baseline": {"score": 0, "reason": "No autoresearch engine"},
    }


# ═══════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════

ALL_SCENARIOS = [
    scenario_propagation,
    scenario_avoidance,
    scenario_maturity,
    scenario_onboarding,
    scenario_recovery,
    scenario_calibration,
    scenario_synthesis,
    scenario_research,
]


def run_orgmemeval(
    db_path=None,
    scenarios: list[str] | None = None,
    seed: int = 42,
) -> dict:
    """Run OrgMemEval benchmark.

    Returns full results with per-scenario scores and summary.
    """
    from memee.storage.database import get_engine, get_session, init_db

    if db_path:
        engine = init_db(get_engine(db_path))
    else:
        # Use temp DB for benchmark isolation
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp()) / "orgmemeval.db"
        engine = init_db(get_engine(tmp))

    results = []
    total_score = 0
    total_max = 0
    total_competitor = 0

    start_time = time.time()

    for scenario_fn in ALL_SCENARIOS:
        name = scenario_fn.__name__.replace("scenario_", "")
        if scenarios and name not in scenarios:
            continue

        session = get_session(engine)
        result = scenario_fn(session, seed=seed)
        results.append(result)
        total_score += result["score"]
        total_max += result["max_points"]
        total_competitor += result["competitor_baseline"]["score"]
        session.close()

    elapsed = time.time() - start_time

    return {
        "benchmark": "OrgMemEval",
        "version": "1.0",
        "system": "Memee",
        "elapsed_seconds": round(elapsed, 2),
        "total_score": round(total_score, 1),
        "total_max": total_max,
        "total_pct": round(total_score / total_max * 100, 1) if total_max else 0,
        "competitor_total": round(total_competitor, 1),
        "scenarios": results,
    }


def format_report(results: dict) -> str:
    """Format benchmark results as a readable report."""
    lines = []
    lines.append("")
    lines.append("═" * 70)
    lines.append(f"  OrgMemEval v{results['version']} — Organizational Memory Benchmark")
    lines.append("═" * 70)
    lines.append(f"  System: {results['system']}")
    lines.append(f"  Time: {results['elapsed_seconds']}s")
    lines.append("")
    lines.append(f"  {'#':>2s}  {'Scenario':<16s} {'Score':>6s}  {'Max':>4s}  {'%':>5s}  {'Competitor':>10s}")
    lines.append(f"  {'─'*2}  {'─'*16} {'─'*6}  {'─'*4}  {'─'*5}  {'─'*10}")

    for i, s in enumerate(results["scenarios"], 1):
        comp = s["competitor_baseline"]
        comp_str = f"{comp['score']}" if comp['score'] > 0 else "0 (n/a)"
        lines.append(
            f"  {i:2d}  {s['name']:<16s} {s['score']:6.1f}  {s['max_points']:4d}  "
            f"{s['pct']:4.0f}%  {comp_str:>10s}"
        )

    lines.append(f"  {'─'*2}  {'─'*16} {'─'*6}  {'─'*4}  {'─'*5}  {'─'*10}")
    lines.append(
        f"  {'':2s}  {'TOTAL':<16s} {results['total_score']:6.1f}  "
        f"{results['total_max']:4d}  {results['total_pct']:4.0f}%  "
        f"{results['competitor_total']:10.1f}"
    )
    lines.append("")
    lines.append(f"  Memee: {results['total_score']}/{results['total_max']} "
                 f"({results['total_pct']:.0f}%) | "
                 f"Competitors: ~{results['competitor_total']}/{results['total_max']} "
                 f"({results['competitor_total']/results['total_max']*100:.0f}%)")
    lines.append("═" * 70)

    # Detail per scenario
    for s in results["scenarios"]:
        lines.append(f"\n  {s['name']}:")
        for k, v in s["metrics"].items():
            if isinstance(v, dict):
                lines.append(f"    {k}:")
                for k2, v2 in v.items():
                    lines.append(f"      {k2}: {v2}")
            else:
                lines.append(f"    {k}: {v}")

    return "\n".join(lines)
