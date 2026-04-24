"""Tests for quality gate: validation, dedup, source classification."""

import pytest

from memee.engine.quality_gate import (
    _assess_quality,
    _find_duplicate,
    _validate_basic,
    merge_duplicate,
    run_quality_gate,
)
from memee.storage.models import Memory, MemoryType


class TestBasicValidation:

    def test_valid_memory(self):
        issues = _validate_basic(
            "Always use timeout on HTTP requests",
            "Set timeout=10 to prevent hanging connections",
            ["python", "http"], "pattern"
        )
        assert issues == []

    def test_title_too_short(self):
        issues = _validate_basic("short", "Valid content here enough", ["tag"], "pattern")
        assert any("10 characters" in i for i in issues)

    def test_title_too_long(self):
        issues = _validate_basic("x" * 501, "Valid content here enough", ["tag"], "pattern")
        assert any("500" in i for i in issues)

    def test_content_too_short(self):
        issues = _validate_basic("Valid title for memory", "short", ["tag"], "pattern")
        assert any("15 characters" in i for i in issues)

    def test_invalid_type(self):
        issues = _validate_basic("Valid title here ok", "Valid content here", ["tag"], "garbage")
        assert any("Invalid type" in i for i in issues)

    def test_empty_title(self):
        issues = _validate_basic("", "Valid content", ["tag"], "pattern")
        assert len(issues) > 0


class TestDeduplication:

    def test_exact_duplicate(self, session):
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Always use timeout on HTTP requests",
            content="Set timeout=10", tags=["python", "http"],
        )
        session.add(m)
        session.commit()

        # Same type + same tags → exact fingerprint match
        dup, score = _find_duplicate(
            session, "Always use timeout on HTTP requests", "",
            memory_type="pattern", tags=["python", "http"],
        )
        assert dup is not None
        assert score == 1.0

    def test_near_duplicate(self, session):
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Always use timeout on HTTP requests",
            content="Content", tags=["python", "http"],
        )
        session.add(m)
        session.commit()

        # Similar title + same tag cluster → fuzzy match
        dup, score = _find_duplicate(
            session, "Set timeout on HTTP requests", "",
            memory_type="pattern", tags=["python", "http"],
        )
        assert dup is not None
        assert score > 0.7

    def test_no_duplicate(self, session):
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Always use timeout on HTTP requests",
            content="Content", tags=["python"],
        )
        session.add(m)
        session.commit()

        dup, score = _find_duplicate(session, "SwiftUI DragGesture ghost artifact", "")
        assert dup is None

    def test_merge_duplicate(self, session):
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Use timeout", content="Original content",
            tags=["python"],
        )
        session.add(m)
        session.commit()

        merged = merge_duplicate(session, m, "Extra detail", ["http", "api"])
        assert "Extra detail" in merged.content
        assert "http" in merged.tags
        assert "python" in merged.tags  # Original preserved


class TestQualityScoring:

    def test_excellent_quality(self):
        score = _assess_quality(
            "Always use timeout on HTTP requests to prevent hanging",
            "Set requests.get(url, timeout=10) to prevent connections from hanging "
            "indefinitely. Without timeout, one slow API can block the entire pipeline.",
            ["python", "http", "reliability"],
        )
        assert score >= 4.0

    def test_poor_quality(self):
        score = _assess_quality(
            "timeout",
            "timeout",
            [],
        )
        assert score <= 2.5

    def test_medium_quality(self):
        score = _assess_quality(
            "Use timeout on requests",
            "Set timeout parameter",
            ["python"],
        )
        assert 2.5 <= score <= 4.0


class TestFullPipeline:

    def test_accept_good_memory(self, session):
        result = run_quality_gate(
            session,
            title="Always use timeout on HTTP requests",
            content="Set timeout=10 to prevent hanging connections",
            tags=["python", "http"],
            memory_type="pattern",
        )
        assert result.accepted is True
        assert result.merged is False

    def test_reject_too_short(self, session):
        result = run_quality_gate(
            session,
            title="bad",
            content="x",
            tags=["tag"],
            memory_type="pattern",
        )
        assert result.accepted is False
        assert len(result.issues) > 0

    def test_merge_duplicate(self, session):
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Always use timeout on HTTP requests",
            content="Original", tags=["python"],
        )
        session.add(m)
        session.commit()

        result = run_quality_gate(
            session,
            title="Always use timeout on HTTP requests",
            content="New info about timeout",
            tags=["python"],
            memory_type="pattern",
        )
        assert result.merged is True
        assert result.merged_id == m.id

    def test_source_multiplier_human(self, session):
        result = run_quality_gate(
            session,
            title="Human-recorded pattern here",
            content="Detailed human observation about code quality",
            tags=["quality"],
            source="human",
        )
        assert result.initial_confidence == pytest.approx(0.6, abs=0.01)  # 0.5 * 1.2

    def test_source_multiplier_llm(self, session):
        result = run_quality_gate(
            session,
            title="LLM-generated pattern here ok",
            content="Auto-detected by language model analysis",
            tags=["auto"],
            source="llm",
        )
        assert result.initial_confidence == pytest.approx(0.4, abs=0.01)  # 0.5 * 0.8

    def test_team_scope_quality_flag(self, session):
        result = run_quality_gate(
            session,
            title="vague thing about code",
            content="not much here really but enough chars",
            tags=["test"],
            scope="team",
        )
        # Low quality → flagged for team scope
        assert result.quality_score is not None

    def test_personal_scope_basic_quality_check(self, session):
        result = run_quality_gate(
            session,
            title="My personal note about something",
            content="Just a quick note for myself about this topic",
            tags=["personal"],
            scope="personal",
        )
        assert result.quality_score is not None  # Quality checked for all scopes


class TestScopeAwareDedup:
    """Dedup must be aggressive for solo users but conservative at team/org.

    At 240-project / 120-dev scale we saw a single 0.88 threshold collapse
    ~6500 candidate patterns into 12 memories. Similar titles in different
    projects usually encode different rules — don't merge them silently.
    """

    def _seed(self, session, title, tags):
        m = Memory(
            type=MemoryType.PATTERN.value,
            title=title,
            content="Original content explaining the pattern and when to use it",
            tags=tags,
        )
        session.add(m)
        session.commit()
        return m

    def test_team_scope_rejects_merge_at_0_90_similarity(self, session):
        """Titles that would merge at personal scope (~0.90) must NOT merge at team."""
        self._seed(session, "Always use timeout on HTTP requests", ["python", "http"])

        # Slightly different wording: same tags, ~0.90 similarity, different project meaning
        result = run_quality_gate(
            session,
            title="Always use a timeout on every HTTP request",
            content="Use a timeout on every outbound HTTP call to avoid hangs",
            tags=["python", "http"],
            memory_type="pattern",
            scope="team",
        )
        # Team scope (threshold 0.92) must NOT collapse this into the seed.
        assert result.merged is False, (
            f"Team scope should not merge near-duplicate (similarity "
            f"{result.dedup_similarity:.3f}); got merged={result.merged}"
        )

    def test_personal_scope_merges_same_pair(self, session):
        """The same title pair at personal scope SHOULD merge (0.88 threshold)."""
        seed = self._seed(
            session, "Always use timeout on HTTP requests", ["python", "http"]
        )

        result = run_quality_gate(
            session,
            title="Always use a timeout on every HTTP request",
            content="Use a timeout on every outbound HTTP call to avoid hangs",
            tags=["python", "http"],
            memory_type="pattern",
            scope="personal",
        )
        assert result.merged is True
        assert result.merged_id == seed.id

    def test_org_scope_requires_0_95(self, session):
        """Org scope is even stricter than team — only near-identical titles merge."""
        self._seed(session, "Always use timeout on HTTP requests", ["python", "http"])

        # Deliberately close but not identical (~0.90)
        result = run_quality_gate(
            session,
            title="Always use a timeout on every HTTP request",
            content="Use a timeout on every outbound HTTP call to avoid hangs",
            tags=["python", "http"],
            memory_type="pattern",
            scope="org",
        )
        assert result.merged is False


class TestClusterSizeGate:
    """A memory that has already swallowed many near-dupes is suspect."""

    def test_rejects_7th_merge_into_saturated_cluster(self, session):
        # Seed a memory that already looks like a grab-bag (merge_count=5)
        seed = Memory(
            type=MemoryType.PATTERN.value,
            title="Always use timeout on HTTP requests",
            content="Original content",
            tags=["python", "http"],
            merge_count=5,
        )
        session.add(seed)
        session.commit()

        # Any close-enough candidate at personal scope would normally merge.
        # With cluster-size cap it must be flagged instead.
        result = run_quality_gate(
            session,
            title="Always use timeout on HTTP requests",
            content="New candidate that would ordinarily merge",
            tags=["python", "http"],
            memory_type="pattern",
            scope="personal",
        )
        assert result.merged is False
        assert result.flagged is True
        assert result.reason == "large_cluster_manual_review"
        assert result.merged_id == seed.id
        assert result.accepted is False

    def test_allows_merge_below_cluster_limit(self, session):
        seed = Memory(
            type=MemoryType.PATTERN.value,
            title="Always use timeout on HTTP requests",
            content="Original content",
            tags=["python", "http"],
            merge_count=2,   # well under limit
        )
        session.add(seed)
        session.commit()

        result = run_quality_gate(
            session,
            title="Always use timeout on HTTP requests",
            content="New candidate that would ordinarily merge",
            tags=["python", "http"],
            memory_type="pattern",
            scope="personal",
        )
        assert result.merged is True
        assert result.flagged is False
        assert result.reason is None


class TestMergeEvidenceChain:
    """merge_duplicate must leave an audit trail operators can replay."""

    def test_merge_appends_evidence_entry(self, session):
        seed = Memory(
            type=MemoryType.PATTERN.value,
            title="Always use timeout on HTTP requests",
            content="Original",
            tags=["python"],
            evidence_chain=[],
        )
        session.add(seed)
        session.commit()

        merge_duplicate(
            session, seed,
            new_content="Extra info about retries too",
            new_tags=["http"],
            new_title="Set timeout on HTTP requests",
            similarity=0.94,
        )

        assert isinstance(seed.evidence_chain, list)
        assert len(seed.evidence_chain) == 1
        entry = seed.evidence_chain[0]
        assert entry["type"] == "dedup_merge"
        assert entry["from_title"] == "Set timeout on HTTP requests"
        assert entry["similarity"] == pytest.approx(0.94, abs=0.001)
        assert "ts" in entry and entry["ts"]  # non-empty ISO timestamp
        # Cluster accounting bumped
        assert seed.merge_count == 1

    def test_merge_accumulates_multiple_entries(self, session):
        seed = Memory(
            type=MemoryType.PATTERN.value,
            title="Always use timeout on HTTP requests",
            content="Original",
            tags=["python"],
            evidence_chain=[],
        )
        session.add(seed)
        session.commit()

        merge_duplicate(session, seed, "first extra info here", new_title="first", similarity=0.93)
        merge_duplicate(session, seed, "second extra info here", new_title="second", similarity=0.91)

        assert len(seed.evidence_chain) == 2
        assert [e["from_title"] for e in seed.evidence_chain] == ["first", "second"]
        assert seed.merge_count == 2

    def test_merge_resyncs_memory_tag_index(self, session):
        """After a merge adds new tags, the MemoryTag index must reflect them.

        Regression: merge used to update only the JSON column, leaving the
        normalized MemoryTag table stale; tag-indexed lookups (propagation,
        predictive) would silently miss the merged tags.
        """
        from memee.engine.tag_index import sync_memory_tags
        from memee.storage.models import MemoryTag

        seed = Memory(
            type=MemoryType.PATTERN.value,
            title="Always use timeout on HTTP requests",
            content="Original",
            tags=["python"],
        )
        session.add(seed)
        session.commit()
        # Initial index state
        sync_memory_tags(session, seed)
        session.commit()
        assert {t.tag for t in session.query(MemoryTag).filter_by(memory_id=seed.id)} == {"python"}

        merge_duplicate(
            session, seed,
            new_content="Also applies to outbound HTTP",
            new_tags=["fastapi"],
            new_title="Always use timeout (fastapi)",
            similarity=0.95,
        )

        # JSON column has both tags AND the index was re-synced.
        assert set(seed.tags) == {"python", "fastapi"}
        indexed = {t.tag for t in session.query(MemoryTag).filter_by(memory_id=seed.id)}
        assert indexed == {"python", "fastapi"}
