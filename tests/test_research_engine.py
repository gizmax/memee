"""Tests for autoresearch engine: create, run, track, meta-learn."""

import pytest

from memee.engine.research import (
    _extract_metric,
    complete_experiment,
    create_experiment,
    get_experiment_status,
    get_meta_learning,
    list_experiments,
    log_iteration,
)
from memee.storage.models import (
    Memory,
    MemoryType,
    Project,
    ResearchStatus,
)


@pytest.fixture
def research_env(session, org):
    """Session with a project for research experiments."""
    proj = Project(
        organization_id=org.id,
        name="ResearchProject",
        path="/projects/research",
        stack=["Python", "FastAPI"],
        tags=["python", "api"],
    )
    session.add(proj)
    session.commit()
    return session, proj, org


class TestMetricExtraction:
    """Test extracting numeric metrics from command output."""

    def test_key_value_colon(self):
        assert _extract_metric("accuracy: 0.85") == 0.85

    def test_key_value_equals(self):
        assert _extract_metric("score = 0.912") == 0.912

    def test_percentage(self):
        assert _extract_metric("TOTAL coverage: 73%") == 0.73

    def test_coverage_output(self):
        output = """Name          Stmts   Miss  Cover
------------------------------
src/main.py       50     10    80%
TOTAL            100     20    80%"""
        assert _extract_metric(output) == 0.80

    def test_last_number(self):
        assert _extract_metric("some output\n42.5") == 42.5

    def test_empty(self):
        assert _extract_metric("") is None

    def test_no_numbers(self):
        assert _extract_metric("all tests passed") is None


class TestExperimentLifecycle:
    """Test full experiment lifecycle."""

    def test_create_experiment(self, research_env):
        session, proj, org = research_env

        exp = create_experiment(
            session, proj.id,
            goal="Increase accuracy to 90%",
            metric_name="accuracy",
            metric_direction="higher",
            verify_command="echo 'accuracy: 0.75'",
            baseline_value=0.75,
        )

        assert exp.id is not None
        assert exp.goal == "Increase accuracy to 90%"
        assert exp.baseline_value == 0.75
        assert exp.status == ResearchStatus.RUNNING.value

    def test_log_iterations(self, research_env):
        """Log keep/discard/crash iterations."""
        session, proj, org = research_env

        exp = create_experiment(
            session, proj.id, "Test", "metric", "higher",
            "echo 0.5", baseline_value=0.50,
        )

        # Keep: improvement
        it1 = log_iteration(session, exp.id, 0.55, "keep", "Added feature X")
        assert it1.status == "keep"
        assert it1.delta == pytest.approx(0.05)
        assert exp.keeps == 1
        assert exp.best_value == 0.55

        # Discard: regression
        it2 = log_iteration(session, exp.id, 0.52, "discard", "Didn't help")
        assert it2.status == "discard"
        assert exp.discards == 1
        assert exp.best_value == 0.55  # Unchanged

        # Crash
        it3 = log_iteration(session, exp.id, 0.0, "crash", "Tests broke")
        assert it3.status == "crash"
        assert exp.crashes == 1

        assert exp.total_iterations == 3

    def test_keep_rate_improves(self, research_env):
        """Simulate 20 iterations with improving keep rate."""
        session, proj, org = research_env

        exp = create_experiment(
            session, proj.id, "Improve coverage", "coverage", "higher",
            "echo 0.60", baseline_value=0.60,
        )

        results = []
        current = 0.60
        for i in range(20):
            # Simulate: 70% chance of small improvement
            import random
            random.seed(i + 100)
            if random.random() < 0.70:
                new_val = current + random.uniform(0.005, 0.02)
                log_iteration(session, exp.id, new_val, "keep", f"Iter {i+1}")
                current = new_val
                results.append("keep")
            else:
                log_iteration(session, exp.id, current - 0.01, "discard", f"Iter {i+1}")
                results.append("discard")

        keep_rate = exp.keeps / exp.total_iterations
        assert keep_rate >= 0.5
        assert exp.best_value > exp.baseline_value

    def test_complete_creates_memory(self, research_env):
        """Completing a successful experiment creates a lesson memory."""
        session, proj, org = research_env

        exp = create_experiment(
            session, proj.id, "Optimize query speed", "latency", "lower",
            "echo 0.5", baseline_value=0.50,
        )
        log_iteration(session, exp.id, 0.30, "keep", "Added index")

        complete_experiment(session, exp, "completed")

        assert exp.status == "completed"
        assert exp.final_value is not None

        # Should have created a lesson memory
        lesson = (
            session.query(Memory)
            .filter(
                Memory.type == MemoryType.LESSON.value,
                Memory.title.like("%Optimize query speed%"),
            )
            .first()
        )
        assert lesson is not None
        assert "autoresearch" in lesson.tags

    def test_complete_failed_no_memory(self, research_env):
        """Failed experiment doesn't create a memory."""
        session, proj, org = research_env

        exp = create_experiment(
            session, proj.id, "Failed experiment", "metric", "higher",
            "echo 0", baseline_value=0.50,
        )
        log_iteration(session, exp.id, 0.40, "discard")

        before_count = session.query(Memory).filter(
            Memory.type == MemoryType.LESSON.value
        ).count()

        complete_experiment(session, exp, "failed")

        after_count = session.query(Memory).filter(
            Memory.type == MemoryType.LESSON.value
        ).count()

        assert after_count == before_count  # No new memory


class TestExperimentQueries:
    """Test listing and status queries."""

    def test_get_status(self, research_env):
        session, proj, org = research_env

        exp = create_experiment(
            session, proj.id, "Test goal", "acc", "higher",
            "echo 0.5", baseline_value=0.50,
        )
        log_iteration(session, exp.id, 0.55, "keep")
        log_iteration(session, exp.id, 0.53, "discard")

        status = get_experiment_status(session, exp.id)
        assert status["goal"] == "Test goal"
        assert status["keeps"] == 1
        assert status["discards"] == 1
        assert len(status["trajectory"]) == 2

    def test_list_experiments(self, research_env):
        session, proj, org = research_env

        create_experiment(session, proj.id, "Exp 1", "m1", "higher", "echo 1", baseline_value=1.0)
        create_experiment(session, proj.id, "Exp 2", "m2", "lower", "echo 2", baseline_value=2.0)

        exps = list_experiments(session)
        assert len(exps) == 2

    def test_list_by_status(self, research_env):
        session, proj, org = research_env

        exp1 = create_experiment(session, proj.id, "Running", "m", "higher", "echo 1", baseline_value=1.0)
        exp2 = create_experiment(session, proj.id, "Done", "m", "higher", "echo 1", baseline_value=1.0)
        complete_experiment(session, exp2, "completed")

        running = list_experiments(session, status="running")
        assert len(running) == 1
        assert running[0]["goal"] == "Running"


class TestMetaLearning:
    """Test meta-learning across experiments."""

    def test_meta_learning_insights(self, research_env):
        """Meta-learning produces insights from multiple experiments."""
        session, proj, org = research_env
        import random
        random.seed(999)

        # Create experiments with different metrics
        for metric, keep_rate_target in [
            ("accuracy", 0.7),
            ("accuracy", 0.65),
            ("coverage", 0.5),
            ("coverage", 0.45),
            ("latency", 0.3),
        ]:
            exp = create_experiment(
                session, proj.id, f"Improve {metric}", metric,
                "higher" if metric != "latency" else "lower",
                "echo 0.5", baseline_value=0.50,
            )
            for i in range(10):
                if random.random() < keep_rate_target:
                    log_iteration(session, exp.id, 0.5 + i * 0.01, "keep")
                else:
                    log_iteration(session, exp.id, 0.5, "discard")
            complete_experiment(session, exp, "completed")

        meta = get_meta_learning(session)

        assert meta["total_experiments"] == 5
        assert meta["completed"] == 5
        assert "by_metric" in meta
        assert "accuracy" in meta["by_metric"]
        assert "coverage" in meta["by_metric"]
        assert meta["by_metric"]["accuracy"]["keep_rate"] > meta["by_metric"]["latency"]["keep_rate"]
        assert len(meta["insights"]) > 0

    def test_empty_meta_learning(self, research_env):
        session, proj, org = research_env
        meta = get_meta_learning(session)
        assert "message" in meta
