"""Performance stress test — measures hotspots under load.

Run: pytest tests/test_perf_stress.py -v -s

300 projects, 8000 memories, 16000 validations, propagation + predictive scan.
Reports ops/sec per hotspot.
"""

import random
import time

import pytest
from sqlalchemy import func

from memee.engine.confidence import update_confidence
from memee.engine.predictive import scan_project_for_warnings
from memee.engine.propagation import run_propagation_cycle
from memee.engine.search import search_memories
from memee.storage.models import (
    AntiPattern, Memory, MemoryType,
    Project,
)

random.seed(42)


@pytest.fixture
def stress_env(session, org):
    """300 projects with varied stacks."""
    stacks = [
        (["Python", "FastAPI", "PostgreSQL"], ["python", "api"]),
        (["React", "TypeScript", "Tailwind"], ["react", "frontend"]),
        (["Swift", "SwiftUI", "CoreData"], ["swift", "mobile"]),
        (["Go", "Gin", "PostgreSQL"], ["go", "api"]),
    ]
    projects = []
    for i in range(300):
        stack, tags = stacks[i % len(stacks)]
        proj = Project(
            organization_id=org.id,
            name=f"Stress-{i:03d}",
            path=f"/stress/p-{i:03d}",
            stack=stack, tags=tags,
        )
        session.add(proj)
        projects.append(proj)
    session.commit()
    return session, projects, org


class TestStressPerf:

    def test_hotspots(self, stress_env):
        """Measure each hotspot separately."""
        session, projects, org = stress_env
        results = {}

        # ── Stage 1: Bulk insert 8000 memories ──
        t = time.time()
        titles = [
            "Always use timeout on HTTP requests",
            "Use connection pooling",
            "Pre-commit hooks reduce CI failures",
            "Circuit breaker for external APIs",
            "Structured logging with correlation IDs",
            "Pydantic model_validate for parsing",
            "Async/await for I/O operations",
            "Index foreign keys in PostgreSQL",
        ]
        all_tags = [
            ["python", "http", "reliability"],
            ["python", "database", "performance"],
            ["python", "ci", "quality"],
            ["python", "resilience", "api"],
            ["python", "logging", "observability"],
            ["react", "frontend", "hooks"],
            ["swift", "swiftui", "mobile"],
            ["go", "api", "backend"],
        ]
        memories = []
        for i in range(8000):
            title = f"{random.choice(titles)} v{i}"
            tags = random.choice(all_tags)
            m = Memory(
                type=MemoryType.PATTERN.value,
                title=title, content=f"Content for {title}",
                tags=tags, confidence_score=0.5,
                source_type="llm",
            )
            memories.append(m)
        session.bulk_save_objects(memories)
        session.commit()
        memories = session.query(Memory).all()  # Reload with ids
        results["bulk_insert_8000"] = time.time() - t

        # ── Stage 2: 16000 validations ──
        t = time.time()
        for _ in range(16000):
            m = random.choice(memories)
            proj = random.choice(projects)
            update_confidence(m, random.random() > 0.3, proj.id)
        session.commit()
        results["validations_16000"] = time.time() - t

        # ── Stage 3: Search (600 queries) ──
        t = time.time()
        for _ in range(600):
            q = random.choice(["timeout", "database", "logging", "api", "async"])
            search_memories(session, q, limit=5, use_vectors=False)
        results["search_600"] = time.time() - t

        # ── Stage 4: Propagation ──
        t = time.time()
        prop_stats = run_propagation_cycle(
            session, confidence_threshold=0.5, max_propagations=5000
        )
        results["propagation"] = time.time() - t
        results["propagation_links"] = prop_stats["total_new_links"]

        # ── Stage 5: Predictive scan (60 projects) ──
        # Add some anti-patterns first
        for i in range(50):
            ap_m = Memory(
                type=MemoryType.ANTI_PATTERN.value,
                title=f"Never do bad thing {i}", content=f"Bad thing {i}",
                tags=random.choice(all_tags),
                confidence_score=0.7,
            )
            session.add(ap_m)
            session.flush()
            ap = AntiPattern(
                memory_id=ap_m.id,
                severity=random.choice(["critical", "high", "medium"]),
                trigger=f"Bad thing {i}", consequence="Failure",
            )
            session.add(ap)
        session.commit()

        t = time.time()
        total_warnings = 0
        for proj in projects[:60]:
            w = scan_project_for_warnings(session, proj)
            total_warnings += len(w)
        results["predictive_60_projects"] = time.time() - t
        results["warnings_emitted"] = total_warnings

        # ── Report ──
        print("\n" + "=" * 70)
        print("  STRESS TEST: 300 projects, 8k memories, 16k validations")
        print("=" * 70)
        total = session.query(func.count(Memory.id)).scalar()
        print(f"\n  Total memories at end: {total}")
        print("\n  Hotspot timings:")
        print(f"    Bulk insert 8000:     {results['bulk_insert_8000']:6.2f}s "
              f"({8000 / results['bulk_insert_8000']:.0f}/s)")
        print(f"    Validations 16000:    {results['validations_16000']:6.2f}s "
              f"({16000 / results['validations_16000']:.0f}/s)")
        print(f"    Search 600 queries:   {results['search_600']:6.2f}s "
              f"({600 / results['search_600']:.0f}/s, "
              f"{results['search_600'] / 600 * 1000:.1f}ms avg)")
        print(f"    Propagation:          {results['propagation']:6.2f}s "
              f"({results['propagation_links']} links)")
        print(f"    Predictive 60 proj:   {results['predictive_60_projects']:6.2f}s "
              f"({results['warnings_emitted']} warnings, "
              f"{results['warnings_emitted'] / max(1, results['predictive_60_projects']):.0f}/s)")

        # Sanity assertions
        assert results["validations_16000"] < 60
        assert results["search_600"] < 10
        assert results["predictive_60_projects"] < 15
