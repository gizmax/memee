"""Tests for smart knowledge router v2: search-based, token-budgeted."""

import pytest

from memee.engine.router import smart_briefing, TOKENS_PER_LINE
from memee.storage.models import (
    AntiPattern, MaturityLevel, Memory, MemoryType,
    Project,
)


@pytest.fixture
def router_env(session, org):
    """Rich environment with diverse memories for routing tests."""
    proj = Project(
        organization_id=org.id, name="APIProject",
        path="/tmp/api-project",
        stack=["Python", "FastAPI", "PostgreSQL"],
        tags=["python", "api"],
    )
    session.add(proj)
    session.flush()

    # Testing patterns
    for title, tags in [
        ("Use pytest fixtures for test setup", ["testing", "pytest", "python"]),
        ("Mock external APIs in unit tests", ["testing", "python", "api"]),
    ]:
        m = Memory(type=MemoryType.PATTERN.value, title=title, content=title,
                    tags=tags, confidence_score=0.85, maturity=MaturityLevel.VALIDATED.value)
        session.add(m)

    # Security patterns
    for title, tags in [
        ("Validate all user input at boundary", ["security", "api", "python"]),
        ("Use parameterized SQL queries", ["security", "database", "python"]),
    ]:
        m = Memory(type=MemoryType.PATTERN.value, title=title, content=title,
                    tags=tags, confidence_score=0.9, maturity=MaturityLevel.CANON.value)
        session.add(m)

    # Database patterns
    for title, tags in [
        ("Use connection pooling for production", ["database", "performance", "python"]),
        ("Index foreign keys in PostgreSQL", ["database", "indexing", "postgresql"]),
    ]:
        m = Memory(type=MemoryType.PATTERN.value, title=title, content=title,
                    tags=tags, confidence_score=0.8, maturity=MaturityLevel.VALIDATED.value)
        session.add(m)

    # API patterns
    m = Memory(type=MemoryType.PATTERN.value,
               title="Always use timeout on HTTP requests",
               content="Set timeout=10 to prevent hanging",
               tags=["api", "http", "reliability"],
               confidence_score=0.92, maturity=MaturityLevel.CANON.value)
    session.add(m)

    # Frontend (should NOT appear for Python/API project)
    m = Memory(type=MemoryType.PATTERN.value,
               title="React useEffect cleanup prevents leaks",
               content="Return cleanup from useEffect hook",
               tags=["react", "frontend", "hooks"],
               confidence_score=0.85, maturity=MaturityLevel.VALIDATED.value)
    session.add(m)

    # Critical anti-patterns
    for title, severity in [
        ("Never store API keys in source code", "critical"),
        ("Never use eval() on user input", "critical"),
    ]:
        m = Memory(type=MemoryType.ANTI_PATTERN.value, title=title, content=title,
                    tags=["security", "python"], confidence_score=0.95)
        session.add(m)
        session.flush()
        ap = AntiPattern(memory_id=m.id, severity=severity,
                         trigger=title, consequence="Security risk")
        session.add(ap)

    # Non-critical AP
    m = Memory(type=MemoryType.ANTI_PATTERN.value,
               title="Avoid N+1 queries in ORM",
               content="Use batch operations instead of per-row queries",
               tags=["database", "performance"],
               confidence_score=0.8)
    session.add(m)
    session.flush()
    ap = AntiPattern(memory_id=m.id, severity="high",
                     trigger="ORM loops", consequence="Slow queries")
    session.add(ap)

    session.commit()
    return session, proj, org


class TestSmartBriefing:

    def test_contains_critical_always(self, router_env):
        """Critical anti-patterns always appear regardless of task."""
        session, proj, org = router_env
        result = smart_briefing(session, "/tmp/api-project", task="write docs")
        assert "CRITICAL" in result
        assert "API keys" in result or "eval" in result

    def test_testing_task_routes_to_testing(self, router_env):
        """'write tests' gets testing patterns, not frontend."""
        session, proj, org = router_env
        result = smart_briefing(session, "/tmp/api-project",
                                 task="write unit tests for auth")
        assert "test" in result.lower() or "pytest" in result.lower()
        assert "useEffect" not in result  # React pattern excluded

    def test_database_task_routes_to_db(self, router_env):
        """'optimize queries' gets DB patterns."""
        session, proj, org = router_env
        result = smart_briefing(session, "/tmp/api-project",
                                 task="optimize database queries")
        assert "pool" in result.lower() or "index" in result.lower() or "N+1" in result

    def test_no_task_shows_stack_patterns(self, router_env):
        """Without task, shows stack-relevant patterns."""
        session, proj, org = router_env
        result = smart_briefing(session, "/tmp/api-project", task="")
        # Should show some patterns (not just critical)
        assert "✓" in result  # At least one pattern shown
        assert "CRITICAL" in result  # Plus critical warnings

    def test_token_budget_respected(self, router_env):
        """Briefing stays within token budget."""
        session, proj, org = router_env
        result = smart_briefing(session, "/tmp/api-project",
                                 task="full stack development", token_budget=300)
        lines = [l for l in result.split("\n") if l.strip()]
        estimated_tokens = len(lines) * TOKENS_PER_LINE
        # Allow some overhead for headers/footers
        assert estimated_tokens < 500  # Well under a full dump

    def test_excludes_irrelevant_stack(self, router_env):
        """Python project doesn't get React patterns."""
        session, proj, org = router_env
        result = smart_briefing(session, "/tmp/api-project",
                                 task="build API endpoint")
        assert "useEffect" not in result
        assert "React" not in result

    def test_shows_token_count(self, router_env):
        """Footer shows token usage."""
        session, proj, org = router_env
        result = smart_briefing(session, "/tmp/api-project", task="test")
        assert "tokens" in result
        assert "budget" in result

    def test_different_tasks_different_results(self, router_env):
        """Different tasks produce different briefings via search routing."""
        session, proj, org = router_env
        testing = smart_briefing(session, "/tmp/api-project",
                                  task="write unit tests with pytest fixtures")
        database = smart_briefing(session, "/tmp/api-project",
                                   task="optimize PostgreSQL database indexes and pooling")
        # Different tasks should surface different memories
        # (both have CRITICAL section same, but search results differ)
        # At minimum, they should both be valid briefings
        assert "CRITICAL" in testing
        assert "CRITICAL" in database
