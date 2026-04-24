"""Tests for the 5 improvement features."""

import pytest

from memee.engine.dream import _boost_connected_memories, run_dream_cycle
from memee.engine.inheritance import compute_stack_similarity, inherit_memories
from memee.engine.predictive import scan_all_projects, scan_project_for_warnings
from memee.engine.propagation import propagate_memory, run_propagation_cycle
from memee.engine.review import review_diff, review_file_content
from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryConnection,
    MemoryType,
    Project,
    ProjectMemory,
)


@pytest.fixture
def multi_project_env(session, org):
    """5 projects with varied stacks."""
    projects = {
        "api": Project(
            organization_id=org.id, name="API-Service",
            path="/proj/api", stack=["Python", "FastAPI", "SQLite"],
            tags=["python", "api"],
        ),
        "web": Project(
            organization_id=org.id, name="Web-Frontend",
            path="/proj/web", stack=["React", "TypeScript", "Tailwind"],
            tags=["react", "frontend"],
        ),
        "data": Project(
            organization_id=org.id, name="Data-Pipeline",
            path="/proj/data", stack=["Python", "pandas", "SQLite"],
            tags=["python", "data"],
        ),
        "ios": Project(
            organization_id=org.id, name="iOS-App",
            path="/proj/ios", stack=["Swift", "SwiftUI"],
            tags=["swift", "ios"],
        ),
        "api2": Project(
            organization_id=org.id, name="API-Service-2",
            path="/proj/api2", stack=["Python", "FastAPI", "PostgreSQL"],
            tags=["python", "api"],
        ),
    }
    for p in projects.values():
        session.add(p)
    session.commit()
    return session, projects, org


# ═══════════════════════════════════
# 1. AUTO-PROPAGATION
# ═══════════════════════════════════


class TestAutoPropagation:

    def test_propagate_single_memory(self, multi_project_env):
        """Pattern with python tag propagates to all Python projects."""
        session, projects, org = multi_project_env

        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Use timeout on requests",
            content="Always set timeout.",
            tags=["python", "http"],
            confidence_score=0.6,
        )
        session.add(m)
        session.flush()

        pm = ProjectMemory(project_id=projects["api"].id, memory_id=m.id)
        session.add(pm)
        session.commit()

        results = propagate_memory(session, m)

        # Should propagate to data and api2 (both have Python)
        propagated_names = {r["project_name"] for r in results}
        assert "Data-Pipeline" in propagated_names
        assert "API-Service-2" in propagated_names
        # Should NOT propagate to Web-Frontend or iOS-App (no Python)
        assert "Web-Frontend" not in propagated_names
        assert "iOS-App" not in propagated_names

    def test_propagation_cycle(self, multi_project_env):
        """Full propagation cycle processes all eligible memories."""
        session, projects, org = multi_project_env

        # Add 5 patterns in API project
        for i in range(5):
            m = Memory(
                type=MemoryType.PATTERN.value,
                title=f"Pattern {i}",
                content=f"Content {i}",
                tags=["python", "api"],
                confidence_score=0.6,
            )
            session.add(m)
            session.flush()
            pm = ProjectMemory(project_id=projects["api"].id, memory_id=m.id)
            session.add(pm)

        session.commit()
        stats = run_propagation_cycle(session, confidence_threshold=0.55)

        assert stats["memories_propagated"] > 0
        assert stats["total_new_links"] > 0
        assert stats["projects_reached"] > 0

    def test_no_duplicate_propagation(self, multi_project_env):
        """Running propagation twice doesn't create duplicates."""
        session, projects, org = multi_project_env

        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Unique pattern",
            content="Content",
            tags=["python"],
            confidence_score=0.7,
        )
        session.add(m)
        session.flush()
        pm = ProjectMemory(project_id=projects["api"].id, memory_id=m.id)
        session.add(pm)
        session.commit()

        r1 = propagate_memory(session, m)
        r2 = propagate_memory(session, m)

        assert len(r2) == 0  # No new propagations


# ═══════════════════════════════════
# 2. PREDICTIVE ANTI-PATTERN PUSH
# ═══════════════════════════════════


class TestPredictiveAPPush:

    def test_push_warnings_to_project(self, multi_project_env):
        """Anti-patterns matching project stack are pushed automatically."""
        session, projects, org = multi_project_env

        # Create a Python anti-pattern
        m = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Don't use requests without timeout",
            content="Causes hanging threads.",
            tags=["python", "http"],
        )
        session.add(m)
        session.flush()
        ap = AntiPattern(
            memory_id=m.id, severity="high",
            trigger="HTTP requests", consequence="Hanging threads",
            alternative="Use timeout=10",
        )
        session.add(ap)
        session.commit()

        # Scan Python project → should find the warning
        warnings = scan_project_for_warnings(session, projects["api"])
        assert len(warnings) >= 1
        assert warnings[0]["severity"] == "high"

        # Scan iOS project → should NOT find it (different stack)
        ios_warnings = scan_project_for_warnings(session, projects["ios"])
        python_warnings = [w for w in ios_warnings if "python" in w.get("matching_tags", [])]
        assert len(python_warnings) == 0

    def test_scan_all_projects(self, multi_project_env):
        """Batch scan finds warnings across all projects."""
        session, projects, org = multi_project_env

        # Add anti-patterns for different stacks
        for title, tags, severity in [
            ("No timeout", ["python", "http"], "high"),
            ("Inline styles", ["react", "css"], "low"),
            ("DragGesture ghost", ["swift", "swiftui"], "high"),
        ]:
            m = Memory(
                type=MemoryType.ANTI_PATTERN.value,
                title=title, content=title, tags=tags,
            )
            session.add(m)
            session.flush()
            ap = AntiPattern(
                memory_id=m.id, severity=severity,
                trigger=title, consequence="Known issue",
            )
            session.add(ap)

        session.commit()
        stats = scan_all_projects(session)

        assert stats["projects_scanned"] == 5
        assert stats["total_warnings"] > 0


# ═══════════════════════════════════
# 3. MEMORY INHERITANCE
# ═══════════════════════════════════


class TestMemoryInheritance:

    def test_stack_similarity(self, multi_project_env):
        """Projects with similar stacks have high similarity scores."""
        _, projects, _ = multi_project_env

        # API and API2 share Python and FastAPI
        sim = compute_stack_similarity(projects["api"], projects["api2"])
        assert sim > 0.4

        # API and iOS share nothing
        sim_diff = compute_stack_similarity(projects["api"], projects["ios"])
        assert sim_diff == 0.0

    def test_inherit_from_similar(self, multi_project_env):
        """New project inherits validated patterns from similar projects."""
        session, projects, org = multi_project_env

        # Add validated patterns to API project
        for i in range(5):
            m = Memory(
                type=MemoryType.PATTERN.value,
                title=f"API Pattern {i}",
                content=f"Validated pattern {i}",
                tags=["python", "fastapi"],
                confidence_score=0.7,
                maturity=MaturityLevel.VALIDATED.value,
                application_count=3,
            )
            session.add(m)
            session.flush()
            pm = ProjectMemory(project_id=projects["api"].id, memory_id=m.id)
            session.add(pm)

        session.commit()

        # API2 inherits from API (both Python/FastAPI)
        stats = inherit_memories(session, projects["api2"])

        assert stats["memories_inherited"] > 0
        assert any(sp["name"] == "API-Service" for sp in stats["similar_projects"])

    def test_inherit_excludes_tested_and_hypothesis(self, multi_project_env):
        """Only VALIDATED + CANON memories should be inherited.

        Regression: inheritance used to pull TESTED memories (one application,
        often confidence 0.5-0.65), muddying onboarding. Now gated to
        VALIDATED/CANON only.
        """
        session, projects, org = multi_project_env

        # Seed a TESTED memory with high confidence — it must NOT be inherited.
        tested = Memory(
            type=MemoryType.PATTERN.value,
            title="Only-tested pattern",
            content="One application, not trusted enough for onboarding.",
            tags=["python", "fastapi"],
            confidence_score=0.75,
            maturity=MaturityLevel.TESTED.value,
            application_count=1,
        )
        # Seed a VALIDATED memory — this one MAY be inherited.
        validated = Memory(
            type=MemoryType.PATTERN.value,
            title="Validated pattern",
            content="Trusted across projects.",
            tags=["python", "fastapi"],
            confidence_score=0.85,
            maturity=MaturityLevel.VALIDATED.value,
            application_count=4,
        )
        # Seed a HYPOTHESIS memory — must not propagate either.
        hypothesis = Memory(
            type=MemoryType.PATTERN.value,
            title="Hypothesis pattern",
            content="Just recorded, not tested.",
            tags=["python", "fastapi"],
            confidence_score=0.5,
            maturity=MaturityLevel.HYPOTHESIS.value,
        )
        for m in (tested, validated, hypothesis):
            session.add(m)
            session.flush()
            session.add(
                ProjectMemory(project_id=projects["api"].id, memory_id=m.id)
            )
        session.commit()

        stats = inherit_memories(session, projects["api2"])
        titles = {im["title"] for im in stats["inherited_memories"]}
        assert "Validated pattern" in titles
        assert "Only-tested pattern" not in titles
        assert "Hypothesis pattern" not in titles

    def test_inherit_does_not_inflate_confidence(self, multi_project_env):
        """Inheritance links memory-to-project but must not bump confidence.

        Regression: dropping update_confidence from inheritance means the
        memory's application_count and confidence_score stay as-is — the new
        project is a delivery, not evidence of validation.
        """
        session, projects, org = multi_project_env

        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Canonical pattern",
            content="Already trusted.",
            tags=["python", "fastapi"],
            confidence_score=0.9,
            maturity=MaturityLevel.VALIDATED.value,
            application_count=5,
        )
        session.add(m)
        session.flush()
        session.add(ProjectMemory(project_id=projects["api"].id, memory_id=m.id))
        session.commit()

        before_conf = m.confidence_score
        before_apps = m.application_count
        inherit_memories(session, projects["api2"])
        session.refresh(m)
        assert m.confidence_score == before_conf, (
            f"inheritance inflated confidence {before_conf} → {m.confidence_score}"
        )
        assert m.application_count == before_apps, (
            f"inheritance inflated application_count {before_apps} → {m.application_count}"
        )

    def test_no_inherit_from_different_stack(self, multi_project_env):
        """iOS project doesn't inherit Python patterns."""
        session, projects, org = multi_project_env

        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Python-only pattern",
            content="Only for Python",
            tags=["python"],
            confidence_score=0.8,
            maturity=MaturityLevel.VALIDATED.value,
            application_count=3,
        )
        session.add(m)
        session.flush()
        pm = ProjectMemory(project_id=projects["api"].id, memory_id=m.id)
        session.add(pm)
        session.commit()

        stats = inherit_memories(session, projects["ios"])
        assert stats["memories_inherited"] == 0


# ═══════════════════════════════════
# 4. DREAM MODE
# ═══════════════════════════════════


class TestDreamMode:

    def test_auto_connect(self, multi_project_env):
        """Dream mode connects memories with shared tags."""
        session, projects, org = multi_project_env

        # Create memories with overlapping tags
        m1 = Memory(
            type=MemoryType.PATTERN.value,
            title="Timeout on HTTP", content="timeout", tags=["python", "http", "reliability"],
        )
        m2 = Memory(
            type=MemoryType.PATTERN.value,
            title="Retry on 5xx", content="retry", tags=["python", "http", "reliability"],
        )
        m3 = Memory(
            type=MemoryType.PATTERN.value,
            title="SwiftUI modifier", content="modifier", tags=["swift", "swiftui"],
        )
        session.add_all([m1, m2, m3])
        session.commit()

        stats = run_dream_cycle(session)

        # m1 and m2 share 3 tags → should be connected
        assert stats["connections_created"] >= 1
        # m3 has no overlap with m1/m2 → no extra connection

    def test_find_contradictions(self, multi_project_env):
        """Dream mode finds pattern vs anti-pattern contradictions."""
        session, projects, org = multi_project_env

        m_pattern = Memory(
            type=MemoryType.PATTERN.value,
            title="Use requests library", content="requests is great",
            tags=["python", "http"],
        )
        m_anti = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Don't use requests without timeout", content="bad without timeout",
            tags=["python", "http"],
        )
        session.add_all([m_pattern, m_anti])
        session.flush()
        ap = AntiPattern(
            memory_id=m_anti.id, severity="high",
            trigger="No timeout", consequence="Hanging",
        )
        session.add(ap)
        session.commit()

        stats = run_dream_cycle(session)
        assert stats["contradictions_found"] >= 1

    def test_promotions(self, multi_project_env):
        """Dream mode promotes eligible memories."""
        session, projects, org = multi_project_env

        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Ready for promotion",
            content="Should be promoted",
            confidence_score=0.75,
            application_count=5,
            project_count=3,
            validation_count=5,
            maturity=MaturityLevel.TESTED.value,
        )
        session.add(m)
        session.commit()

        stats = run_dream_cycle(session)
        assert m.maturity == MaturityLevel.VALIDATED.value


# ═══════════════════════════════════
# 5. CODE REVIEW
# ═══════════════════════════════════


class TestCodeReview:

    def test_detect_anti_patterns_in_diff(self, multi_project_env):
        """Code review catches known anti-patterns in a diff."""
        session, projects, org = multi_project_env

        # Add anti-pattern
        m = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Don't use eval()",
            content="Security risk",
            tags=["python", "security"],
        )
        session.add(m)
        session.flush()
        ap = AntiPattern(
            memory_id=m.id, severity="critical",
            trigger="Using eval() on user input",
            consequence="Remote code execution",
            alternative="Use ast.literal_eval() or json.loads()",
        )
        session.add(ap)
        session.commit()

        diff = """
diff --git a/src/parser.py b/src/parser.py
--- a/src/parser.py
+++ b/src/parser.py
@@ -10,6 +10,8 @@
 def parse_config(raw):
-    return json.loads(raw)
+    # Quick fix for complex expressions
+    result = eval(raw)
+    return result
"""
        result = review_diff(session, diff)

        assert len(result["warnings"]) >= 1
        assert any(w["severity"] == "critical" for w in result["warnings"])

    def test_detect_good_patterns(self, multi_project_env):
        """Code review confirms good patterns in a diff."""
        session, projects, org = multi_project_env

        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Use timeout on HTTP requests",
            content="Always timeout",
            tags=["python", "http", "timeout", "reliability"],
            maturity=MaturityLevel.VALIDATED.value,
            confidence_score=0.8,
        )
        session.add(m)
        session.commit()

        diff = """
diff --git a/src/api.py b/src/api.py
+    response = requests.get(url, timeout=10)
+    import logging
+    logger = logging.getLogger(__name__)
"""
        result = review_diff(session, diff)
        # Should find the timeout + http pattern
        assert result["stats"]["keywords_extracted"] > 0

    def test_empty_diff(self, multi_project_env):
        """Empty diff returns no results."""
        session, _, _ = multi_project_env
        result = review_diff(session, "")
        assert result["warnings"] == []
        assert result["confirmations"] == []

    def test_rejects_huge_diff(self, multi_project_env):
        """Diffs above MAX_DIFF_BYTES raise DiffTooLargeError (DoS guard)."""
        from memee.engine.review import DiffTooLargeError, MAX_DIFF_BYTES

        session, _, _ = multi_project_env
        # 6 MB of "a\n" lines — would OOM the regex engine otherwise.
        huge = ("a\n" * (MAX_DIFF_BYTES // 2 + 1))
        with pytest.raises(DiffTooLargeError):
            review_diff(session, huge)

    def test_skips_binary_hunks(self, multi_project_env):
        """Binary-file markers are not scanned as code."""
        session, projects, org = multi_project_env

        # Seed an AP that would match "eval" if we scanned everything.
        m = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Don't use eval()", content="rce",
            tags=["python", "security", "eval"],
        )
        session.add(m)
        session.flush()
        session.add(AntiPattern(
            memory_id=m.id, severity="critical",
            trigger="eval", consequence="RCE",
        ))
        session.commit()

        # The binary hunk "contains" the string eval, but via a binary marker.
        diff = (
            "diff --git a/logo.png b/logo.png\n"
            "Binary files a/logo.png and b/logo.png differ\n"
            "+eval(user_input)  # would match if scanned\n"  # must be skipped
            "diff --git a/ok.py b/ok.py\n"
            "--- a/ok.py\n"
            "+++ b/ok.py\n"
            "+print('hello')\n"
        )
        result = review_diff(session, diff)
        # No eval warning should appear because the only `eval(` occurrence
        # lived inside a binary hunk and was skipped.
        eval_hits = [w for w in result["warnings"] if "eval" in w["title"].lower()]
        assert eval_hits == []

    def test_secrets_regex_requires_quoted_string(self, multi_project_env):
        """Variable names that happen to contain `token` do not false-positive."""
        from memee.engine.review import _extract_keywords

        # Plain identifier — must NOT yield "secrets".
        keywords = _extract_keywords([
            "user_token_field = get_field('auth')",
            "password_policy = load_policy()",
        ])
        assert "secrets" not in keywords

        # Literal secret assignment — SHOULD yield "secrets".
        keywords = _extract_keywords([
            'API_KEY = "abc123xyz789"',
        ])
        assert "secrets" in keywords

    def test_http_detector_covers_session_and_verbs(self, multi_project_env):
        """Detector catches session.get + patch/head/options, not just requests.get."""
        from memee.engine.review import _extract_keywords

        keywords = _extract_keywords(["r = session.patch(url, json=body)"])
        assert "http" in keywords
        keywords = _extract_keywords(["client.options(url)"])
        assert "http" in keywords

    def test_env_detector_positive_only(self, multi_project_env):
        """os.environ alone is fine; only indexed access / getenv should flag."""
        from memee.engine.review import _extract_keywords

        # Plain reference — must NOT yield "env-config".
        keywords = _extract_keywords(["e = os.environ"])
        assert "env-config" not in keywords

        # Indexed access — SHOULD yield.
        keywords = _extract_keywords(['x = os.environ["DB_URL"]'])
        assert "env-config" in keywords

        keywords = _extract_keywords(['x = os.getenv("DB_URL")'])
        assert "env-config" in keywords

    def test_unicode_diff_does_not_crash(self, multi_project_env):
        """A diff with Czech/Cyrillic filenames and text scans cleanly."""
        session, _, _ = multi_project_env
        diff = (
            "diff --git a/docs/příručka.md b/docs/příručka.md\n"
            "--- a/docs/příručka.md\n"
            "+++ b/docs/příručka.md\n"
            "@@ -1,1 +1,2 @@\n"
            "-Старое содержимое\n"
            "+Новое содержимое: timeout=10\n"
        )
        # Should not raise; must return a sane stats payload.
        result = review_diff(session, diff)
        assert "stats" in result
        assert isinstance(result["warnings"], list)

    def test_review_file_content(self, multi_project_env):
        """Review file content directly (not a diff)."""
        session, projects, org = multi_project_env

        m = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title="Don't use SELECT *",
            content="Performance issue",
            tags=["database", "performance"],
        )
        session.add(m)
        session.flush()
        ap = AntiPattern(
            memory_id=m.id, severity="medium",
            trigger="SELECT * in production",
            consequence="Fetches unnecessary data",
            alternative="Select specific columns",
        )
        session.add(ap)
        session.commit()

        code = """
import sqlite3
conn = sqlite3.connect('test.db')
cursor = conn.execute("SELECT * FROM users WHERE active = 1")
for row in cursor.fetchall():
    process(row)
"""
        result = review_file_content(session, code, "query.py")
        assert result["file"] == "query.py"


def test_dream_boost_bounded_with_many_weak_neighbors(session):
    """20 weak neighbors must not inflate confidence past a reasonable cap.

    Before the fix, `boost = 0.02 * avg_signal * len(neighbor_confs)` scaled
    linearly in the neighbor count: a TESTED memory at 0.6 with 20 neighbors
    at conf 0.46 and strength 0.4 gained ~0.074 per dream pass, i.e. ~0.37
    across 5 cycles, reaching ~0.97 with no new validation evidence.
    """
    target = Memory(
        type=MemoryType.PATTERN.value,
        title="Dense-cluster hypothesis",
        content="A target memory living inside a dense tag cluster.",
        tags=["cluster"],
        confidence_score=0.6,
        maturity=MaturityLevel.TESTED.value,
    )
    session.add(target)
    session.flush()

    neighbors = []
    for i in range(20):
        n = Memory(
            type=MemoryType.PATTERN.value,
            title=f"Weak neighbor {i}",
            content=f"Neighbor memory number {i}.",
            tags=["cluster"],
            confidence_score=0.46,
            maturity=MaturityLevel.VALIDATED.value,
        )
        session.add(n)
        neighbors.append(n)
    session.flush()

    for n in neighbors:
        session.add(
            MemoryConnection(
                source_id=target.id,
                target_id=n.id,
                relationship_type="related_to",
                strength=0.4,
            )
        )
    session.commit()

    for _ in range(5):
        _boost_connected_memories(session)
    session.commit()

    session.refresh(target)
    # With len_factor capped at 5, per-pass boost ≈ 0.02 * 0.184 * 5 ≈ 0.018,
    # so after 5 passes ≈ 0.69. Assert we stayed well under the pre-fix runaway.
    assert target.confidence_score < 0.75, (
        f"Dream mode inflated confidence past bound: got {target.confidence_score:.3f}"
    )
    # Sanity: boost did still apply (some movement), just bounded.
    assert target.confidence_score > 0.6
