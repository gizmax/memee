"""Performance & simulation tests for Memee.

Simulates realistic multi-agent, multi-project workflows:
- 5 virtual companies with 3-8 projects each
- Multiple agents recording patterns, decisions, anti-patterns
- Cross-project knowledge transfer
- Learning from mistakes (anti-pattern avoidance)
- Confidence evolution over time
- Maturity progression from hypothesis to canon
- Autoresearch experiment tracking
- Organizational learning rate measurement

Run with: pytest tests/test_perf_simulation.py -v -s
"""

import random
import time
from datetime import datetime, timedelta, timezone

import pytest

from memee.engine.confidence import update_confidence
from memee.engine.lifecycle import run_aging_cycle
from memee.engine.search import search_anti_patterns, search_memories
from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryConnection,
    MemoryType,
    MemoryValidation,
    Project,
    ProjectMemory,
    Severity,
)


# ── Demo Data Definitions ──

VIRTUAL_PROJECTS = [
    {
        "name": "InvestmentBot",
        "path": "/projects/investmentbot",
        "stack": ["Python", "Flask", "SQLite", "Chart.js"],
        "tags": ["fintech", "ai", "dashboard", "api"],
    },
    {
        "name": "Cardigo",
        "path": "/projects/cardigo",
        "stack": ["Swift", "SwiftUI", "CoreData", "HealthKit"],
        "tags": ["ios", "mobile", "health", "swiftui"],
    },
    {
        "name": "Sandcastle",
        "path": "/projects/sandcastle",
        "stack": ["Python", "FastAPI", "SQLite", "React"],
        "tags": ["evolution", "simulation", "ai", "api"],
    },
    {
        "name": "DataPipeline",
        "path": "/projects/datapipeline",
        "stack": ["Python", "pandas", "SQLite", "Airflow"],
        "tags": ["etl", "data", "pipeline", "automation"],
    },
    {
        "name": "WebDashboard",
        "path": "/projects/webdashboard",
        "stack": ["React", "TypeScript", "Tailwind", "FastAPI"],
        "tags": ["frontend", "dashboard", "api", "visualization"],
    },
]

AGENTS = ["agent-alpha", "agent-beta", "agent-gamma", "agent-delta"]

DEMO_PATTERNS = [
    {
        "title": "Always use timeout on HTTP requests",
        "content": "Use requests.get(url, timeout=10) to prevent hanging connections. "
        "Without timeout, a single slow API can block the entire pipeline.",
        "tags": ["python", "api", "reliability", "http"],
        "applicable_to": ["Python"],
    },
    {
        "title": "SQLite WAL mode for concurrent reads",
        "content": "Enable PRAGMA journal_mode=WAL for better concurrent read performance. "
        "Default rollback journal blocks readers during writes.",
        "tags": ["sqlite", "database", "performance", "concurrency"],
        "applicable_to": ["SQLite"],
    },
    {
        "title": "Use row_factory = sqlite3.Row for dict-like access",
        "content": "Always set conn.row_factory = sqlite3.Row to get dict-like row access "
        "instead of plain tuples. Prevents index-based bugs.",
        "tags": ["sqlite", "python", "database", "best-practice"],
        "applicable_to": ["SQLite", "Python"],
    },
    {
        "title": "React useEffect cleanup to prevent memory leaks",
        "content": "Always return a cleanup function from useEffect when subscribing to events "
        "or timers. Prevents memory leaks on component unmount.",
        "tags": ["react", "frontend", "memory-leak", "hooks"],
        "applicable_to": ["React"],
    },
    {
        "title": "ThreadPoolExecutor for parallel API calls",
        "content": "Use concurrent.futures.ThreadPoolExecutor(max_workers=N) with individual "
        "timeouts when making multiple external API calls. Reduces wall-clock time by N/workers.",
        "tags": ["python", "performance", "concurrency", "api"],
        "applicable_to": ["Python"],
    },
    {
        "title": "Pydantic model_validate over manual dict parsing",
        "content": "Use Pydantic's model_validate() instead of manual dict key access. "
        "Provides validation, type coercion, and clear error messages automatically.",
        "tags": ["python", "pydantic", "validation", "api"],
        "applicable_to": ["Python", "FastAPI"],
    },
    {
        "title": "Tailwind @apply for repeated utility patterns",
        "content": "Extract repeated Tailwind utility combinations into @apply classes "
        "when the same pattern appears 3+ times. Reduces HTML clutter.",
        "tags": ["tailwind", "css", "frontend", "refactoring"],
        "applicable_to": ["Tailwind", "React"],
    },
    {
        "title": "SwiftUI .task modifier for async data loading",
        "content": "Use .task { } modifier instead of .onAppear + Task { } for async data loading. "
        "Automatically cancels on view disappear.",
        "tags": ["swift", "swiftui", "async", "ios"],
        "applicable_to": ["Swift", "SwiftUI"],
    },
    {
        "title": "Index foreign keys in SQLite",
        "content": "Always create indexes on foreign key columns used in WHERE clauses and JOINs. "
        "SQLite doesn't auto-index FKs unlike PostgreSQL.",
        "tags": ["sqlite", "database", "performance", "indexing"],
        "applicable_to": ["SQLite"],
    },
    {
        "title": "FastAPI Depends for DB session injection",
        "content": "Use FastAPI's Depends() for database session lifecycle management. "
        "Ensures proper cleanup even on exceptions.",
        "tags": ["python", "fastapi", "database", "dependency-injection"],
        "applicable_to": ["FastAPI", "Python"],
    },
]

DEMO_ANTI_PATTERNS = [
    {
        "title": "Don't use pypdf for complex PDFs",
        "severity": "high",
        "trigger": "Processing multi-column or image-heavy PDF files",
        "consequence": "Garbled text, missing content, silent data loss",
        "alternative": "Use pymupdf (fitz) or pdfplumber",
        "tags": ["python", "pdf"],
    },
    {
        "title": "Don't use git reset --hard in automation loops",
        "severity": "critical",
        "trigger": "Automated scripts that need to undo changes",
        "consequence": "Destroys commit history, loses experiment data, agent loses memory",
        "alternative": "Use git revert HEAD --no-edit to preserve history",
        "tags": ["git", "automation", "safety"],
    },
    {
        "title": "Don't store API keys in code",
        "severity": "critical",
        "trigger": "Hardcoding API keys, tokens, or secrets in source files",
        "consequence": "Keys leak via git history, CI logs, or screenshots",
        "alternative": "Use environment variables via os.getenv() or .env files",
        "tags": ["security", "api", "secrets"],
    },
    {
        "title": "Avoid componentDidMount in React",
        "severity": "medium",
        "trigger": "Using class component lifecycle methods",
        "consequence": "Deprecated API, harder to compose, no hooks ecosystem",
        "alternative": "Use useEffect hook in functional components",
        "tags": ["react", "frontend", "deprecated"],
    },
    {
        "title": "Don't use inline styles in React",
        "severity": "low",
        "trigger": "Using style={{}} attributes in JSX",
        "consequence": "Inconsistent styling, harder to maintain, no responsive breakpoints",
        "alternative": "Use Tailwind CSS classes or CSS modules",
        "tags": ["react", "css", "frontend"],
    },
    {
        "title": "Don't use requests without timeout",
        "severity": "high",
        "trigger": "Making HTTP requests with default (infinite) timeout",
        "consequence": "Thread blocks indefinitely on slow/dead endpoints, cascading failures",
        "alternative": "Always requests.get(url, timeout=10)",
        "tags": ["python", "http", "reliability"],
    },
    {
        "title": "SwiftUI DragGesture ghost artifact",
        "severity": "high",
        "trigger": "DragGesture on child view with .offset on parent view",
        "consequence": "Ghost/duplicate artifact during slow continuous drag",
        "alternative": "Gesture and offset MUST be on the SAME view",
        "tags": ["swift", "swiftui", "ui", "gesture"],
    },
]

DEMO_DECISIONS = [
    {
        "title": "Database: SQLite over PostgreSQL",
        "chosen": "SQLite",
        "alternatives": [
            {"name": "PostgreSQL", "reason_rejected": "Too complex for single-user apps"},
            {"name": "MongoDB", "reason_rejected": "No relational needs, overkill"},
        ],
        "criteria": ["simplicity", "zero-config", "single-file", "portability"],
    },
    {
        "title": "Backend framework: FastAPI over Flask",
        "chosen": "FastAPI",
        "alternatives": [
            {"name": "Flask", "reason_rejected": "No native async, manual validation"},
            {"name": "Django", "reason_rejected": "Too heavy for API-only services"},
        ],
        "criteria": ["async", "auto-docs", "pydantic-integration", "performance"],
    },
    {
        "title": "Frontend styling: Tailwind over CSS-in-JS",
        "chosen": "Tailwind CSS",
        "alternatives": [
            {"name": "styled-components", "reason_rejected": "Runtime overhead, SSR issues"},
            {"name": "CSS Modules", "reason_rejected": "Verbose, no utility classes"},
        ],
        "criteria": ["speed", "consistency", "no-runtime", "responsive"],
    },
]


# ── Fixtures ──


@pytest.fixture
def populated_session(session, org):
    """Session with virtual projects, agents, and seed data."""
    projects = {}
    for p_data in VIRTUAL_PROJECTS:
        project = Project(
            organization_id=org.id,
            name=p_data["name"],
            path=p_data["path"],
            stack=p_data["stack"],
            tags=p_data["tags"],
        )
        session.add(project)
        projects[p_data["name"]] = project

    session.commit()
    return session, projects, org


# ── Test 1: Bulk Memory Insertion Performance ──


class TestBulkPerformance:
    """Test insertion and search performance at scale."""

    def test_insert_1000_memories(self, populated_session):
        """Insert 1000 memories and measure time."""
        session, projects, org = populated_session

        start = time.time()
        for i in range(1000):
            pattern = DEMO_PATTERNS[i % len(DEMO_PATTERNS)]
            memory = Memory(
                type=MemoryType.PATTERN.value,
                title=f"{pattern['title']} (variant {i})",
                content=pattern["content"],
                tags=pattern["tags"],
                source_agent=random.choice(AGENTS),
            )
            session.add(memory)

        session.commit()
        elapsed = time.time() - start

        count = session.query(Memory).count()
        assert count >= 1000
        print(f"\n  INSERT 1000 memories: {elapsed:.3f}s ({1000/elapsed:.0f} ops/s)")
        assert elapsed < 5.0, "Insert should be under 5 seconds"

    def test_search_performance_1000(self, populated_session):
        """Search across 1000 memories and measure time."""
        session, projects, org = populated_session

        # Seed data
        for i in range(1000):
            pattern = DEMO_PATTERNS[i % len(DEMO_PATTERNS)]
            memory = Memory(
                type=MemoryType.PATTERN.value,
                title=f"{pattern['title']} (variant {i})",
                content=pattern["content"],
                tags=pattern["tags"],
            )
            session.add(memory)
        session.commit()

        queries = [
            "timeout API calls HTTP",
            "SQLite performance concurrent",
            "React hooks memory leak",
            "FastAPI dependency injection",
            "Tailwind responsive design",
        ]

        start = time.time()
        total_results = 0
        for query in queries:
            results = search_memories(session, query, limit=10)
            total_results += len(results)

        elapsed = time.time() - start
        print(f"\n  SEARCH 5 queries across 1000 memories: {elapsed:.3f}s")
        print(f"  Total results: {total_results}")
        assert elapsed < 2.0, "5 searches should complete under 2 seconds"
        assert total_results > 0, "Should find at least some results"


# ── Test 2: Cross-Project Knowledge Transfer ──


class TestCrossProjectTransfer:
    """Simulate knowledge flowing between projects."""

    def test_pattern_spreads_across_projects(self, populated_session):
        """A pattern discovered in Project A gets validated in B, C, D."""
        session, projects, org = populated_session

        # Agent Alpha discovers a pattern in InvestmentBot
        memory = Memory(
            type=MemoryType.PATTERN.value,
            title="Always use timeout on HTTP requests",
            content="Use requests.get(url, timeout=10) to prevent hanging.",
            tags=["python", "api", "reliability"],
            source_agent="agent-alpha",
        )
        session.add(memory)
        session.flush()

        # Link to InvestmentBot
        pm = ProjectMemory(
            project_id=projects["InvestmentBot"].id,
            memory_id=memory.id,
        )
        session.add(pm)
        session.commit()

        assert memory.maturity == MaturityLevel.HYPOTHESIS.value
        assert memory.confidence_score == 0.5

        # Agent Beta validates it in Sandcastle (cross-project)
        # Note: update_confidence BEFORE adding ProjectMemory so it detects new project
        v1 = MemoryValidation(
            memory_id=memory.id,
            project_id=projects["Sandcastle"].id,
            validated=True,
            evidence="Applied timeout, prevented a hang in news_scorer.py",
        )
        session.add(v1)
        score1 = update_confidence(
            memory, validated=True, project_id=projects["Sandcastle"].id
        )
        pm2 = ProjectMemory(
            project_id=projects["Sandcastle"].id,
            memory_id=memory.id,
            applied=True,
            outcome="positive",
        )
        session.add(pm2)

        # Agent Gamma validates in DataPipeline (cross-project)
        v2 = MemoryValidation(
            memory_id=memory.id,
            project_id=projects["DataPipeline"].id,
            validated=True,
            evidence="Timeout prevented 30min hang on dead upstream API",
        )
        session.add(v2)
        score2 = update_confidence(
            memory, validated=True, project_id=projects["DataPipeline"].id
        )
        pm3 = ProjectMemory(
            project_id=projects["DataPipeline"].id,
            memory_id=memory.id,
            applied=True,
            outcome="positive",
        )
        session.add(pm3)

        # Agent Delta validates in WebDashboard (cross-project)
        v3 = MemoryValidation(
            memory_id=memory.id,
            project_id=projects["WebDashboard"].id,
            validated=True,
            evidence="Added to all fetch calls in API client",
        )
        session.add(v3)
        score3 = update_confidence(
            memory, validated=True, project_id=projects["WebDashboard"].id
        )
        pm4 = ProjectMemory(
            project_id=projects["WebDashboard"].id,
            memory_id=memory.id,
            applied=True,
            outcome="positive",
        )
        session.add(pm4)

        session.commit()

        print(f"\n  Pattern: '{memory.title}'")
        print(f"  Confidence: 0.50 -> {score1:.2f} -> {score2:.2f} -> {score3:.2f}")
        print(f"  Maturity: {memory.maturity}")
        print(f"  Projects: {memory.project_count}, Validations: {memory.validation_count}")

        # After 3 cross-project validations, should be VALIDATED
        assert memory.confidence_score > 0.65
        assert memory.project_count >= 3
        assert memory.maturity in (
            MaturityLevel.VALIDATED.value,
            MaturityLevel.TESTED.value,
        )

    def test_search_finds_cross_project_patterns(self, populated_session):
        """Agent in new project finds relevant patterns from other projects."""
        session, projects, org = populated_session

        # Seed patterns with different project origins
        patterns_data = [
            ("Use connection pooling", "python,database", "InvestmentBot"),
            ("Always validate user input", "security,api", "Sandcastle"),
            ("Use timeout on HTTP requests", "python,api", "DataPipeline"),
        ]

        for title, tags, proj_name in patterns_data:
            m = Memory(
                type=MemoryType.PATTERN.value,
                title=title,
                content=f"Best practice from {proj_name}",
                tags=tags.split(","),
                confidence_score=0.8,
                maturity=MaturityLevel.VALIDATED.value,
            )
            session.add(m)
            session.flush()
            pm = ProjectMemory(
                project_id=projects[proj_name].id, memory_id=m.id
            )
            session.add(pm)

        session.commit()

        # New agent on WebDashboard searches for API patterns
        results = search_memories(
            session, "HTTP requests API", tags=["python", "api"], limit=5
        )

        print("\n  Search 'HTTP requests API' from WebDashboard context:")
        for r in results:
            print(f"    [{r['total_score']:.3f}] {r['memory'].title}")

        assert len(results) > 0


# ── Test 3: Learning from Mistakes ──


class TestLearningFromMistakes:
    """Simulate agents discovering, recording, and avoiding anti-patterns."""

    def test_anti_pattern_discovery_and_avoidance(self, populated_session):
        """Agent hits a bug, records anti-pattern, other agents avoid it."""
        session, projects, org = populated_session

        # Step 1: Agent Alpha hits pypdf bug in InvestmentBot
        anti_mem = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Don't use pypdf for complex PDFs",
            content="Trigger: complex multi-column PDFs\nConsequence: garbled text",
            tags=["python", "pdf"],
            source_agent="agent-alpha",
        )
        session.add(anti_mem)
        session.flush()

        ap = AntiPattern(
            memory_id=anti_mem.id,
            severity=Severity.HIGH.value,
            trigger="Processing multi-column or image-heavy PDF files",
            consequence="Garbled text, missing content, silent data loss",
            alternative="Use pymupdf (fitz) or pdfplumber",
        )
        session.add(ap)

        pm = ProjectMemory(
            project_id=projects["InvestmentBot"].id,
            memory_id=anti_mem.id,
        )
        session.add(pm)
        session.commit()

        print("\n  Step 1: Agent Alpha records anti-pattern in InvestmentBot")
        print(f"    [{ap.severity.upper()}] {anti_mem.title}")

        # Step 2: Agent Beta in DataPipeline is about to process PDFs
        # Runs antipattern_check BEFORE implementing
        check_results = search_anti_patterns(
            session, "pypdf complex PDF", tags=["python", "pdf"]
        )

        print("\n  Step 2: Agent Beta checks before implementing PDF processing")
        print(f"    Found {len(check_results)} warning(s)")

        assert len(check_results) > 0
        warning = check_results[0]["memory"]
        assert warning.anti_pattern is not None
        print(f"    WARNING: [{warning.anti_pattern.severity.upper()}] {warning.title}")
        print(f"    Alternative: {warning.anti_pattern.alternative}")

        # Step 3: Agent Beta avoids the mistake, validates the anti-pattern
        v = MemoryValidation(
            memory_id=anti_mem.id,
            project_id=projects["DataPipeline"].id,
            validated=True,
            evidence="Checked before implementing. Used pymupdf instead. Success.",
        )
        session.add(v)
        update_confidence(
            anti_mem, validated=True, project_id=projects["DataPipeline"].id
        )

        # Step 4: Agent Gamma in WebDashboard also avoids it
        v2 = MemoryValidation(
            memory_id=anti_mem.id,
            project_id=projects["WebDashboard"].id,
            validated=True,
            evidence="Anti-pattern check prevented pypdf usage.",
        )
        session.add(v2)
        update_confidence(
            anti_mem, validated=True, project_id=projects["WebDashboard"].id
        )
        session.commit()

        print("\n  Step 3-4: Two more agents validate the anti-pattern")
        print(f"    Confidence: {anti_mem.confidence_score:.2f}")
        print(f"    Maturity: {anti_mem.maturity}")
        print(f"    Projects warned: {anti_mem.project_count}")

        assert anti_mem.confidence_score > 0.6
        assert anti_mem.project_count >= 2

    def test_wrong_pattern_gets_deprecated(self, populated_session):
        """A pattern that seemed good but keeps failing gets deprecated."""
        session, projects, org = populated_session

        # Someone records a pattern
        memory = Memory(
            type=MemoryType.PATTERN.value,
            title="Use global SQLite connection for better performance",
            content="Share one connection object across all threads for lower overhead.",
            tags=["sqlite", "python", "performance"],
            source_agent="agent-alpha",
        )
        session.add(memory)
        session.flush()

        pm = ProjectMemory(
            project_id=projects["InvestmentBot"].id,
            memory_id=memory.id,
        )
        session.add(pm)
        session.commit()

        print(f"\n  Initial: '{memory.title}'")
        print(f"    Confidence: {memory.confidence_score:.2f}")

        # It fails in multiple projects
        fail_projects = ["Sandcastle", "DataPipeline", "WebDashboard"]
        for proj_name in fail_projects:
            v = MemoryValidation(
                memory_id=memory.id,
                project_id=projects[proj_name].id,
                validated=False,
                evidence=f"Caused threading errors in {proj_name}",
            )
            session.add(v)
            score = update_confidence(
                memory, validated=False, project_id=projects[proj_name].id
            )
            print(f"    Failed in {proj_name}: confidence -> {score:.2f}")

        session.commit()

        print("\n  After 3 failures:")
        print(f"    Confidence: {memory.confidence_score:.2f}")
        print(f"    Maturity: {memory.maturity}")

        # Should be deprecated after 3+ failures with low confidence
        assert memory.confidence_score < 0.35
        assert memory.maturity in (
            MaturityLevel.DEPRECATED.value,
            MaturityLevel.TESTED.value,
        )


# ── Test 4: Confidence Evolution Over Time ──


class TestConfidenceEvolution:
    """Track how confidence scores evolve with mixed validation signals."""

    def test_confidence_trajectory(self, populated_session):
        """Plot confidence evolution: validations + invalidations over 20 events."""
        session, projects, org = populated_session

        memory = Memory(
            type=MemoryType.PATTERN.value,
            title="Use TypeScript strict mode",
            content="Enable strict: true in tsconfig for better type safety.",
            tags=["typescript", "frontend", "safety"],
        )
        session.add(memory)
        session.flush()

        # Simulate 20 validation events with realistic mixed signals
        # 70% positive, 30% negative — should trend upward
        events = [
            # (validated, project_name)
            (True, "WebDashboard"),
            (True, "Sandcastle"),
            (True, "WebDashboard"),
            (False, "DataPipeline"),  # Didn't help in Python project
            (True, "WebDashboard"),
            (True, "InvestmentBot"),
            (False, "Cardigo"),  # Not applicable to Swift
            (True, "WebDashboard"),
            (True, "Sandcastle"),
            (True, "WebDashboard"),
            (True, "DataPipeline"),
            (False, "Cardigo"),
            (True, "WebDashboard"),
            (True, "Sandcastle"),
            (True, "InvestmentBot"),
            (True, "WebDashboard"),
            (False, "Cardigo"),
            (True, "Sandcastle"),
            (True, "WebDashboard"),
            (True, "InvestmentBot"),
        ]

        trajectory = [memory.confidence_score]
        maturity_changes = []
        linked_projects = set()

        for validated, proj_name in events:
            old_mat = memory.maturity
            v = MemoryValidation(
                memory_id=memory.id,
                project_id=projects[proj_name].id,
                validated=validated,
                evidence=f"{'Worked' if validated else 'Not applicable'} in {proj_name}",
            )
            session.add(v)

            # update_confidence BEFORE adding ProjectMemory for cross-project detection
            update_confidence(memory, validated, projects[proj_name].id)

            if proj_name not in linked_projects:
                pm = ProjectMemory(
                    project_id=projects[proj_name].id,
                    memory_id=memory.id,
                )
                session.add(pm)
                session.flush()
                linked_projects.add(proj_name)
            trajectory.append(memory.confidence_score)

            if memory.maturity != old_mat:
                maturity_changes.append(
                    (len(trajectory) - 1, old_mat, memory.maturity)
                )

        session.commit()

        # Print trajectory
        print(f"\n  Confidence trajectory for '{memory.title}':")
        print(f"  {'Step':>4s} | {'Score':>6s} | {'Bar'}")
        print(f"  {'─'*4:s} | {'─'*6:s} | {'─'*40:s}")
        for i, score in enumerate(trajectory):
            bar = "█" * int(score * 40)
            marker = ""
            for step, old, new in maturity_changes:
                if step == i:
                    marker = f"  ← {old} → {new}"
            print(f"  {i:4d} | {score:6.3f} | {bar}{marker}")

        print(f"\n  Final: confidence={memory.confidence_score:.3f}, "
              f"maturity={memory.maturity}")
        print(f"  Validations: {memory.validation_count}, "
              f"Invalidations: {memory.invalidation_count}")
        print(f"  Projects: {memory.project_count}")

        # With 70% positive signals, confidence should be well above 0.5
        assert memory.confidence_score > 0.6
        assert memory.validation_count > memory.invalidation_count

    def test_diminishing_returns_same_project(self, populated_session):
        """Same-project validations give diminishing returns."""
        session, projects, org = populated_session

        memory = Memory(
            type=MemoryType.PATTERN.value,
            title="Test diminishing returns",
            content="Test content",
        )
        session.add(memory)
        session.flush()

        pm = ProjectMemory(
            project_id=projects["InvestmentBot"].id,
            memory_id=memory.id,
        )
        session.add(pm)
        session.commit()

        deltas = []
        for i in range(10):
            old = memory.confidence_score
            update_confidence(
                memory, validated=True,
                project_id=projects["InvestmentBot"].id,
            )
            delta = memory.confidence_score - old
            deltas.append(delta)

        print("\n  Diminishing returns (same project, 10 validations):")
        for i, d in enumerate(deltas):
            bar = "█" * int(d * 500)
            print(f"    Validation {i+1:2d}: +{d:.4f} {bar}")

        # Each successive validation should give less
        assert deltas[0] > deltas[-1]
        # First validation should give at least 2x the 10th
        assert deltas[0] > deltas[9] * 2


# ── Test 5: Maturity Progression Pipeline ──


class TestMaturityProgression:
    """Test the full lifecycle: hypothesis → tested → validated → canon."""

    def test_full_lifecycle_to_canon(self, populated_session):
        """Push a memory all the way from hypothesis to canon."""
        session, projects, org = populated_session

        memory = Memory(
            type=MemoryType.PATTERN.value,
            title="Always sanitize HTML output to prevent XSS",
            content="Use framework auto-escaping or explicit sanitization.",
            tags=["security", "frontend", "xss"],
        )
        session.add(memory)
        session.commit()

        print(f"\n  Lifecycle progression: '{memory.title}'")
        print(f"  Start: maturity={memory.maturity}, confidence={memory.confidence_score}")

        all_projects = list(projects.values())

        # Phase 1: First application → TESTED
        pm = ProjectMemory(
            project_id=all_projects[0].id,
            memory_id=memory.id,
            applied=True,
            outcome="positive",
        )
        session.add(pm)
        v = MemoryValidation(
            memory_id=memory.id,
            project_id=all_projects[0].id,
            validated=True,
        )
        session.add(v)
        update_confidence(memory, True, all_projects[0].id)
        session.commit()
        print(f"  After 1 project: maturity={memory.maturity}, "
              f"confidence={memory.confidence_score:.3f}")
        assert memory.maturity == MaturityLevel.TESTED.value

        # Phase 2: Validate across 4 more projects → VALIDATED
        for proj in all_projects[1:]:
            pm = ProjectMemory(
                project_id=proj.id,
                memory_id=memory.id,
                applied=True,
                outcome="positive",
            )
            session.add(pm)
            for _ in range(2):  # 2 validations per project
                v = MemoryValidation(
                    memory_id=memory.id,
                    project_id=proj.id,
                    validated=True,
                )
                session.add(v)
                update_confidence(memory, True, proj.id)

        session.commit()
        print(f"  After 5 projects: maturity={memory.maturity}, "
              f"confidence={memory.confidence_score:.3f}")
        print(f"    validations={memory.validation_count}, "
              f"project_count={memory.project_count}")

        # Should be at least VALIDATED, possibly CANON
        assert memory.maturity in (
            MaturityLevel.VALIDATED.value,
            MaturityLevel.CANON.value,
        )
        assert memory.confidence_score > 0.8

    def test_aging_cycle_batch(self, populated_session):
        """Aging cycle correctly processes a batch of memories."""
        session, projects, org = populated_session
        now = datetime.now(timezone.utc)

        memories = {
            "should_expire": Memory(
                type=MemoryType.OBSERVATION.value,
                title="Untested old observation",
                content="Something noticed 100 days ago",
                created_at=now - timedelta(days=100),
                validation_count=0,
            ),
            "should_keep": Memory(
                type=MemoryType.PATTERN.value,
                title="Valid pattern",
                content="Works well",
                confidence_score=0.75,
                application_count=3,
                project_count=3,
                validation_count=3,
                maturity=MaturityLevel.TESTED.value,
            ),
            "should_deprecate": Memory(
                type=MemoryType.PATTERN.value,
                title="Bad pattern",
                content="Keeps failing",
                confidence_score=0.15,
                application_count=5,
            ),
            "fresh_hypothesis": Memory(
                type=MemoryType.OBSERVATION.value,
                title="New idea",
                content="Just thought of this",
                created_at=now - timedelta(days=5),
                validation_count=0,
            ),
        }

        for m in memories.values():
            session.add(m)
        session.commit()

        stats = run_aging_cycle(session)

        print("\n  Aging cycle results:")
        print(f"    Expired: {stats['expired']}")
        print(f"    Deprecated: {stats['deprecated']}")
        print(f"    Promoted: {stats['promoted']}")
        print(f"    Unchanged: {stats['unchanged']}")

        for name, m in memories.items():
            print(f"    {name:20s} -> {m.maturity}")

        assert memories["should_expire"].maturity == MaturityLevel.DEPRECATED.value
        assert memories["should_keep"].maturity == MaturityLevel.VALIDATED.value
        assert memories["should_deprecate"].maturity == MaturityLevel.DEPRECATED.value
        assert memories["fresh_hypothesis"].maturity == MaturityLevel.HYPOTHESIS.value


# ── Test 6: Autoresearch Simulation — REMOVED in v2.0.0 ──
# The research engine and its ResearchExperiment / ResearchIteration models
# were deleted with the rest of the autoresearch surface.


# ── Test 7: Multi-Agent Collaboration ──


class TestMultiAgentCollaboration:
    """Simulate multiple agents working on different projects simultaneously."""

    def test_four_agents_concurrent_learning(self, populated_session):
        """4 agents work on 4 projects, share knowledge via Memee."""
        session, projects, org = populated_session

        # Each agent works on a different project
        agent_assignments = {
            "agent-alpha": "InvestmentBot",
            "agent-beta": "Sandcastle",
            "agent-gamma": "DataPipeline",
            "agent-delta": "WebDashboard",
        }

        shared_memories = []
        agent_stats = {a: {"recorded": 0, "found": 0, "avoided": 0} for a in AGENTS}

        # Round 1: Each agent records learnings from their project
        for agent, proj_name in agent_assignments.items():
            # Record 3 patterns each
            for i in range(3):
                pattern = DEMO_PATTERNS[(hash(agent) + i) % len(DEMO_PATTERNS)]
                m = Memory(
                    type=MemoryType.PATTERN.value,
                    title=f"{pattern['title']}",
                    content=pattern["content"],
                    tags=pattern["tags"],
                    source_agent=agent,
                )
                session.add(m)
                session.flush()

                pm = ProjectMemory(
                    project_id=projects[proj_name].id,
                    memory_id=m.id,
                )
                session.add(pm)
                shared_memories.append(m)
                agent_stats[agent]["recorded"] += 1

        # Agent Alpha records an anti-pattern
        anti_mem = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Don't use requests without timeout",
            content="Trigger: HTTP requests\nConsequence: hanging threads",
            tags=["python", "http"],
            source_agent="agent-alpha",
        )
        session.add(anti_mem)
        session.flush()
        ap = AntiPattern(
            memory_id=anti_mem.id,
            severity=Severity.HIGH.value,
            trigger="HTTP requests without timeout parameter",
            consequence="Thread blocks indefinitely, cascading failures",
            alternative="requests.get(url, timeout=10)",
        )
        session.add(ap)
        session.commit()

        # Round 2: Each agent searches for relevant knowledge
        for agent, proj_name in agent_assignments.items():
            project = projects[proj_name]
            stack_str = " ".join(project.stack)
            results = search_memories(session, stack_str, limit=5)
            agent_stats[agent]["found"] = len(results)

        # Round 3: Agents check for anti-patterns before work
        for agent in ["agent-beta", "agent-gamma", "agent-delta"]:
            warnings = search_anti_patterns(session, "HTTP API requests python")
            if warnings:
                agent_stats[agent]["avoided"] = len(warnings)

        # Round 4: Agents validate each other's patterns
        for m in shared_memories[:6]:
            random_proj = random.choice(list(projects.values()))
            v = MemoryValidation(
                memory_id=m.id,
                project_id=random_proj.id,
                validated=True,
            )
            session.add(v)
            update_confidence(m, True, random_proj.id)

        session.commit()

        print("\n  Multi-Agent Collaboration Summary:")
        print(f"  {'Agent':>15s} | {'Recorded':>8s} | {'Found':>5s} | {'Avoided':>7s}")
        print(f"  {'─'*15:s} | {'─'*8:s} | {'─'*5:s} | {'─'*7:s}")
        for agent, stats in agent_stats.items():
            print(
                f"  {agent:>15s} | {stats['recorded']:>8d} | "
                f"{stats['found']:>5d} | {stats['avoided']:>7d}"
            )

        total_memories = session.query(Memory).count()
        avg_conf = sum(m.confidence_score for m in shared_memories) / len(shared_memories)
        print(f"\n  Total memories: {total_memories}")
        print(f"  Avg confidence of shared patterns: {avg_conf:.3f}")

        assert total_memories >= 13  # 12 patterns + 1 anti-pattern
        assert all(s["recorded"] > 0 for s in agent_stats.values())


# ── Test 8: Organizational Learning Rate ──


class TestOrganizationalLearning:
    """Measure organizational learning over time."""

    def test_learning_rate_improves(self, populated_session):
        """Simulate 4 weeks of organizational learning and measure improvement."""
        session, projects, org = populated_session
        snapshots = []

        for week in range(1, 5):
            # Each week: agents record new patterns and validate existing ones
            for i in range(week * 3):  # More patterns as org matures
                pattern = DEMO_PATTERNS[i % len(DEMO_PATTERNS)]
                m = Memory(
                    type=MemoryType.PATTERN.value,
                    title=f"Week {week}: {pattern['title']} (v{i})",
                    content=pattern["content"],
                    tags=pattern["tags"],
                    source_agent=random.choice(AGENTS),
                )
                session.add(m)

            # Record some anti-patterns
            if week <= len(DEMO_ANTI_PATTERNS):
                ap_data = DEMO_ANTI_PATTERNS[week - 1]
                am = Memory(
                    type=MemoryType.ANTI_PATTERN.value,
                    title=f"Week {week}: {ap_data['title']}",
                    content=f"Trigger: {ap_data['trigger']}",
                    tags=ap_data.get("tags", []),
                )
                session.add(am)
                session.flush()
                ap = AntiPattern(
                    memory_id=am.id,
                    severity=ap_data["severity"],
                    trigger=ap_data["trigger"],
                    consequence=ap_data["consequence"],
                    alternative=ap_data.get("alternative", ""),
                )
                session.add(ap)

            session.commit()

            # Validate some existing patterns
            existing = session.query(Memory).filter(
                Memory.type == MemoryType.PATTERN.value
            ).limit(week * 2).all()

            for m in existing:
                proj = random.choice(list(projects.values()))
                v = MemoryValidation(
                    memory_id=m.id,
                    project_id=proj.id,
                    validated=True,
                )
                session.add(v)
                update_confidence(m, True, proj.id)

            session.commit()

            # Run aging cycle
            run_aging_cycle(session)

            # Take snapshot
            from sqlalchemy import func

            total = session.query(func.count(Memory.id)).scalar()
            canon = (
                session.query(func.count(Memory.id))
                .filter(Memory.maturity == MaturityLevel.CANON.value)
                .scalar()
            )
            validated = (
                session.query(func.count(Memory.id))
                .filter(Memory.maturity == MaturityLevel.VALIDATED.value)
                .scalar()
            )
            hypothesis = (
                session.query(func.count(Memory.id))
                .filter(Memory.maturity == MaturityLevel.HYPOTHESIS.value)
                .scalar()
            )
            deprecated = (
                session.query(func.count(Memory.id))
                .filter(Memory.maturity == MaturityLevel.DEPRECATED.value)
                .scalar()
            )
            avg_conf = session.query(func.avg(Memory.confidence_score)).scalar() or 0

            # LearningSnapshot persistence dropped in v2.0.0 — keep only
            # the in-memory list the rest of the test asserts on.

            snapshots.append({
                "week": week,
                "total": total,
                "canon": canon,
                "validated": validated,
                "hypothesis": hypothesis,
                "deprecated": deprecated,
                "avg_confidence": avg_conf,
                "learning_rate": validated / max(total, 1),
            })

        # Print learning progression
        print("\n  Organizational Learning Over 4 Weeks:")
        print(f"  {'Week':>4s} | {'Total':>5s} | {'Canon':>5s} | {'Valid':>5s} | "
              f"{'Hypo':>5s} | {'Depr':>5s} | {'AvgConf':>7s} | {'LRate':>5s}")
        print(f"  {'─'*4} | {'─'*5} | {'─'*5} | {'─'*5} | "
              f"{'─'*5} | {'─'*5} | {'─'*7} | {'─'*5}")
        for s in snapshots:
            print(
                f"  {s['week']:4d} | {s['total']:5d} | {s['canon']:5d} | "
                f"{s['validated']:5d} | {s['hypothesis']:5d} | {s['deprecated']:5d} | "
                f"{s['avg_confidence']:7.3f} | {s['learning_rate']:5.2f}"
            )

        # Learning rate should improve (more validated/canon over time)
        assert snapshots[-1]["total"] > snapshots[0]["total"]
        assert snapshots[-1]["avg_confidence"] >= snapshots[0]["avg_confidence"]


# ── Test 9: Memory Graph Connections ──


class TestMemoryGraph:
    """Test semantic connections between memories."""

    def test_build_memory_graph(self, populated_session):
        """Create and traverse memory connections."""
        session, projects, org = populated_session

        # Create connected memories
        m_timeout = Memory(
            type=MemoryType.PATTERN.value,
            title="Use timeout on HTTP requests",
            content="requests.get(url, timeout=10)",
            tags=["python", "http"],
        )
        m_pooling = Memory(
            type=MemoryType.PATTERN.value,
            title="Use connection pooling for repeated requests",
            content="requests.Session() for connection reuse",
            tags=["python", "http", "performance"],
        )
        m_retry = Memory(
            type=MemoryType.PATTERN.value,
            title="Add retry logic for 5xx errors",
            content="Use urllib3 Retry adapter for transient failures",
            tags=["python", "http", "reliability"],
        )
        m_no_timeout = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Don't use requests without timeout",
            content="Causes hanging threads",
            tags=["python", "http"],
        )

        session.add_all([m_timeout, m_pooling, m_retry, m_no_timeout])
        session.flush()

        # Create connections
        connections = [
            MemoryConnection(
                source_id=m_timeout.id,
                target_id=m_pooling.id,
                relationship_type="related_to",
                strength=0.7,
            ),
            MemoryConnection(
                source_id=m_timeout.id,
                target_id=m_retry.id,
                relationship_type="prerequisite_of",
                strength=0.8,
            ),
            MemoryConnection(
                source_id=m_timeout.id,
                target_id=m_no_timeout.id,
                relationship_type="contradicts",
                strength=0.95,
            ),
            MemoryConnection(
                source_id=m_pooling.id,
                target_id=m_retry.id,
                relationship_type="supports",
                strength=0.6,
            ),
        ]
        session.add_all(connections)
        session.commit()

        # Query connections
        conns = (
            session.query(MemoryConnection)
            .filter(MemoryConnection.source_id == m_timeout.id)
            .all()
        )

        print(f"\n  Memory Graph around '{m_timeout.title}':")
        for c in conns:
            target = session.get(Memory, c.target_id)
            print(f"    --[{c.relationship_type} ({c.strength})]-> {target.title}")

        assert len(conns) == 3
        contradicts = [c for c in conns if c.relationship_type == "contradicts"]
        assert len(contradicts) == 1
        assert contradicts[0].strength == 0.95
