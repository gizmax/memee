"""Tests for multi-model memory: cross-model validation, family detection, bonuses."""

import pytest

from memee.engine.confidence import update_confidence
from memee.engine.models import (
    detect_current_model,
    get_model_family,
    get_unique_model_families,
    is_different_family,
)
from memee.storage.models import (
    MaturityLevel,
    Memory,
    MemoryType,
    MemoryValidation,
    Organization,
    Project,
    ProjectMemory,
)


# ── Model Family Detection ──


class TestModelFamilyDetection:

    def test_anthropic_models(self):
        assert get_model_family("claude-opus-4") == "anthropic"
        assert get_model_family("claude-sonnet-4-20250514") == "anthropic"
        assert get_model_family("claude-haiku-4-5") == "anthropic"

    def test_openai_models(self):
        assert get_model_family("gpt-4o") == "openai"
        assert get_model_family("gpt-4-turbo") == "openai"
        assert get_model_family("o1-preview") == "openai"
        assert get_model_family("o3-mini") == "openai"

    def test_google_models(self):
        assert get_model_family("gemini-2.0-flash") == "google"
        assert get_model_family("gemini-1.5-pro") == "google"

    def test_meta_models(self):
        assert get_model_family("llama-3.1-70b") == "meta"
        assert get_model_family("codellama-34b") == "meta"

    def test_local_models(self):
        assert get_model_family("ollama-server") == "local"
        assert get_model_family("mlx-community/phi-3") == "local"
        assert get_model_family("llamacpp-q4") == "local"

    def test_mistral_models(self):
        assert get_model_family("mistral-large") == "mistral"
        assert get_model_family("mixtral-8x7b") == "mistral"

    def test_unknown(self):
        assert get_model_family("custom-model-v1") == "unknown"
        assert get_model_family(None) == "unknown"
        assert get_model_family("") == "unknown"

    def test_is_different_family(self):
        assert is_different_family("claude-opus-4", "gpt-4o") is True
        assert is_different_family("claude-opus-4", "claude-sonnet-4") is False
        assert is_different_family("gpt-4o", "gpt-4-turbo") is False
        assert is_different_family("claude-opus-4", "gemini-2.0") is True
        assert is_different_family(None, "gpt-4o") is False
        assert is_different_family("claude-opus-4", None) is False

    def test_unique_families(self):
        models = ["claude-opus-4", "gpt-4o", "gemini-2.0", "claude-sonnet-4", "gpt-4-turbo"]
        families = get_unique_model_families(models)
        assert families == {"anthropic", "openai", "google"}

    def test_detect_from_env(self, monkeypatch):
        monkeypatch.setenv("MEMEE_MODEL", "gpt-4o")
        assert detect_current_model() == "gpt-4o"

        monkeypatch.delenv("MEMEE_MODEL")
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4")
        assert detect_current_model() == "claude-opus-4"


# ── Cross-Model Confidence ──


class TestCrossModelConfidence:

    def test_same_model_no_bonus(self, session):
        """Same model validation = no cross-model bonus."""
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Test", content="Test",
            source_model="claude-opus-4",
        )
        session.add(m)
        session.commit()

        base_score = m.confidence_score
        new_score = update_confidence(m, True, model_name="claude-sonnet-4")
        same_model_delta = new_score - base_score

        # Same family (anthropic) = no cross-model bonus
        assert same_model_delta == pytest.approx(0.08 * 0.5, abs=0.01)

    def test_cross_model_bonus(self, session):
        """Different model family = 2.0x cross-model bonus."""
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Test", content="Test",
            source_model="claude-opus-4",
        )
        session.add(m)
        session.commit()

        base_score = m.confidence_score
        new_score = update_confidence(m, True, model_name="gpt-4o")
        cross_model_delta = new_score - base_score

        # Cross-model (anthropic → openai) = 1.3x diversity bonus
        # Base weight 0.08 × 1.3 = 0.104 applied to (1-0.5) = 0.052
        assert cross_model_delta > 0.045  # More than base 0.04

    def test_cross_model_plus_cross_project(self, session, org):
        """Different model + different project = 3.0x maximum bonus."""
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Test", content="Test",
            source_model="claude-opus-4",
        )
        session.add(m)

        p1 = Project(organization_id=org.id, name="P1", path="/p1")
        p2 = Project(organization_id=org.id, name="P2", path="/p2")
        session.add_all([p1, p2])
        session.flush()

        # Link to P1
        pm = ProjectMemory(project_id=p1.id, memory_id=m.id)
        session.add(pm)
        session.commit()

        base_score = m.confidence_score

        # Validate from P2 (cross-project) + gpt-4o (cross-model)
        new_score = update_confidence(
            m, True, project_id=p2.id, model_name="gpt-4o"
        )
        max_bonus_delta = new_score - base_score

        # Cross-project 1.5 × model diversity 1.3 = 1.95x
        assert max_bonus_delta > 0.07

    def test_model_count_tracking(self, session):
        """model_count tracks unique model families (source + validators)."""
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Test", content="Test",
            source_model="claude-opus-4",
            model_count=0,
        )
        session.add(m)
        session.commit()

        # gpt-4o validates: families = {anthropic, openai} = 2
        v1 = MemoryValidation(memory_id=m.id, validated=True, validator_model="gpt-4o")
        session.add(v1)
        session.commit()
        update_confidence(m, True, model_name="gpt-4o")
        assert m.model_count == 2

        # gemini validates: families = {anthropic, openai, google} = 3
        v2 = MemoryValidation(memory_id=m.id, validated=True, validator_model="gemini-2.0")
        session.add(v2)
        session.commit()
        session.refresh(m)  # Refresh relationship
        update_confidence(m, True, model_name="gemini-2.0")
        assert m.model_count == 3

        # gpt-4-turbo: same family as gpt-4o, no new family
        v3 = MemoryValidation(memory_id=m.id, validated=True, validator_model="gpt-4-turbo")
        session.add(v3)
        session.commit()
        session.refresh(m)
        update_confidence(m, True, model_name="gpt-4-turbo")
        assert m.model_count == 3

    def test_confidence_trajectory_multi_model(self, session, org):
        """Simulate 3 models validating the same pattern across projects."""
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Always use timeout on HTTP requests",
            content="Prevents hanging connections.",
            source_model="claude-opus-4",
            tags=["python", "http"],
        )
        session.add(m)

        projects = []
        for i in range(5):
            p = Project(organization_id=org.id, name=f"P{i}", path=f"/p{i}")
            session.add(p)
            projects.append(p)
        session.flush()

        pm = ProjectMemory(project_id=projects[0].id, memory_id=m.id)
        session.add(pm)
        session.commit()

        trajectory = [m.confidence_score]

        # Claude validates in P0 (same model, same project — base)
        update_confidence(m, True, projects[0].id, "claude-sonnet-4")
        trajectory.append(m.confidence_score)

        # GPT-4 validates in P1 (cross-model + cross-project = 3.0x)
        update_confidence(m, True, projects[1].id, "gpt-4o")
        trajectory.append(m.confidence_score)

        # Gemini validates in P2 (cross-model + cross-project = 3.0x)
        update_confidence(m, True, projects[2].id, "gemini-2.0-flash")
        trajectory.append(m.confidence_score)

        # GPT-4 validates in P3 (same model family, but cross-project = 1.5x)
        update_confidence(m, True, projects[3].id, "gpt-4-turbo")
        trajectory.append(m.confidence_score)

        print(f"\n  Multi-model confidence trajectory:")
        models = ["(start)", "claude-sonnet", "gpt-4o", "gemini-2.0", "gpt-4-turbo"]
        for i, (model, score) in enumerate(zip(models, trajectory)):
            bar = "█" * int(score * 40)
            delta = f"+{score - trajectory[i-1]:.3f}" if i > 0 else ""
            print(f"    {model:16s} {score:.3f} {delta:>7s} {bar}")

        print(f"\n  Model count: {m.model_count}")
        print(f"  Maturity: {m.maturity}")

        # After 3 cross-model validations, confidence should be high
        assert m.confidence_score > 0.7
        assert m.model_count >= 2
        assert m.maturity in (MaturityLevel.TESTED.value, MaturityLevel.VALIDATED.value)

    def test_invalidation_unaffected_by_model(self, session):
        """Invalidation doesn't get cross-model bonus (failures are failures)."""
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="Test", content="Test",
            source_model="claude-opus-4",
        )
        session.add(m)
        session.commit()

        base = m.confidence_score
        update_confidence(m, False, model_name="gpt-4o")
        delta = base - m.confidence_score

        # Invalidation weight is 0.12 × 0.5 = 0.06 regardless of model
        assert delta == pytest.approx(0.06, abs=0.01)
