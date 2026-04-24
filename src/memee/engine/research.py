"""Autoresearch engine: goal → metric → modify → verify → keep/discard loop.

Karpathy-inspired autonomous improvement:
- Define a goal and a measurable metric
- Each iteration: modify code → run verify command → compare metric
- Keep improvements, discard regressions, log crashes
- Guard command ensures nothing breaks
- Meta-learning: which experiment types succeed across projects?

The engine orchestrates the loop. Claude agents provide the modifications.
"""

from __future__ import annotations

import subprocess
import re
from collections import defaultdict

from sqlalchemy.orm import Session

from memee.storage.models import (
    Memory,
    MemoryType,
    Project,
    ProjectMemory,
    ResearchExperiment,
    ResearchIteration,
    ResearchStatus,
    utcnow,
)


def create_experiment(
    session: Session,
    project_id: str,
    goal: str,
    metric_name: str,
    metric_direction: str,
    verify_command: str,
    guard_command: str = "",
    scope_globs: list[str] | None = None,
    baseline_value: float | None = None,
) -> ResearchExperiment:
    """Create a new autoresearch experiment.

    If baseline_value is None, runs verify_command to measure it.
    """
    experiment = ResearchExperiment(
        project_id=project_id,
        goal=goal,
        metric_name=metric_name,
        metric_direction=metric_direction,
        verify_command=verify_command,
        guard_command=guard_command or "",
        scope_globs=scope_globs or [],
        baseline_value=baseline_value,
    )
    session.add(experiment)
    session.commit()

    # Measure baseline if not provided
    if baseline_value is None:
        measured = run_verify(verify_command)
        if measured is not None:
            experiment.baseline_value = measured
            experiment.best_value = measured
            session.commit()

    return experiment


def run_verify(command: str, timeout: int = 300, cwd: str | None = None) -> float | None:
    """Run verify command and extract metric value from output.

    Looks for patterns like:
    - "metric: 0.85"
    - "coverage: 73%"
    - "accuracy = 0.912"
    - Last number on last non-empty line
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        output = result.stdout + result.stderr
        return _extract_metric(output)
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def run_guard(command: str, timeout: int = 300, cwd: str | None = None) -> bool:
    """Run guard command. Returns True if exit code is 0."""
    if not command:
        return True
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return result.returncode == 0
    except Exception:
        return False


def run_iteration(
    session: Session,
    experiment: ResearchExperiment,
    description: str = "",
    commit_hash: str = "",
    cwd: str | None = None,
) -> ResearchIteration:
    """Run a single iteration: verify metric → compare → keep/discard.

    Returns the iteration with status set.
    """
    experiment.total_iterations += 1
    iteration_num = experiment.total_iterations

    # Run guard first
    guard_passed = run_guard(experiment.guard_command, cwd=cwd)
    if not guard_passed:
        iteration = ResearchIteration(
            experiment_id=experiment.id,
            iteration_number=iteration_num,
            commit_hash=commit_hash,
            metric_value=None,
            delta=None,
            guard_passed=False,
            status="crash",
            description=description or "Guard command failed",
        )
        session.add(iteration)
        experiment.crashes += 1
        session.commit()
        return iteration

    # Run verify
    metric_value = run_verify(experiment.verify_command, cwd=cwd)
    if metric_value is None:
        iteration = ResearchIteration(
            experiment_id=experiment.id,
            iteration_number=iteration_num,
            commit_hash=commit_hash,
            metric_value=None,
            delta=None,
            guard_passed=True,
            status="crash",
            description=description or "Verify command failed to produce metric",
        )
        session.add(iteration)
        experiment.crashes += 1
        session.commit()
        return iteration

    # Compare
    baseline = experiment.best_value or experiment.baseline_value or 0
    delta = metric_value - baseline

    is_improvement = (
        (delta > 0 and experiment.metric_direction == "higher")
        or (delta < 0 and experiment.metric_direction == "lower")
    )

    if is_improvement:
        status = "keep"
        experiment.keeps += 1
        experiment.best_value = metric_value
    else:
        status = "discard"
        experiment.discards += 1

    iteration = ResearchIteration(
        experiment_id=experiment.id,
        iteration_number=iteration_num,
        commit_hash=commit_hash,
        metric_value=metric_value,
        delta=delta,
        guard_passed=True,
        status=status,
        description=description,
    )
    session.add(iteration)
    session.commit()
    return iteration


def log_iteration(
    session: Session,
    experiment_id: str,
    metric_value: float,
    status: str,
    description: str = "",
    commit_hash: str = "",
) -> ResearchIteration:
    """Manually log an iteration (for agents that run verify themselves)."""
    experiment = session.get(ResearchExperiment, experiment_id)
    if not experiment:
        raise ValueError(f"Experiment not found: {experiment_id}")

    experiment.total_iterations += 1
    baseline = experiment.best_value or experiment.baseline_value or 0
    delta = metric_value - baseline

    if status == "keep":
        experiment.keeps += 1
        if experiment.best_value is None or (
            (experiment.metric_direction == "higher" and metric_value > experiment.best_value)
            or (experiment.metric_direction == "lower" and metric_value < experiment.best_value)
        ):
            experiment.best_value = metric_value
    elif status == "discard":
        experiment.discards += 1
    elif status == "crash":
        experiment.crashes += 1

    iteration = ResearchIteration(
        experiment_id=experiment.id,
        iteration_number=experiment.total_iterations,
        commit_hash=commit_hash,
        metric_value=metric_value,
        delta=delta,
        guard_passed=status != "crash",
        status=status,
        description=description,
    )
    session.add(iteration)
    session.commit()
    return iteration


def complete_experiment(
    session: Session,
    experiment: ResearchExperiment,
    status: str = "completed",
) -> ResearchExperiment:
    """Mark an experiment as completed/failed/cancelled."""
    experiment.status = status
    experiment.final_value = experiment.best_value
    experiment.completed_at = utcnow()

    # Record as a memory if successful
    if status == "completed" and experiment.keeps > 0:
        improvement = (experiment.best_value or 0) - (experiment.baseline_value or 0)
        keep_rate = experiment.keeps / max(experiment.total_iterations, 1)

        memory = Memory(
            type=MemoryType.LESSON.value,
            title=f"Autoresearch: {experiment.goal}",
            content=(
                f"Goal: {experiment.goal}\n"
                f"Metric: {experiment.metric_name} ({experiment.metric_direction})\n"
                f"Baseline: {experiment.baseline_value} → Final: {experiment.best_value}\n"
                f"Improvement: {improvement:+.4f}\n"
                f"Iterations: {experiment.total_iterations} "
                f"(keep: {experiment.keeps}, discard: {experiment.discards}, crash: {experiment.crashes})\n"
                f"Keep rate: {keep_rate:.0%}\n"
                f"Verify: {experiment.verify_command}"
            ),
            tags=["autoresearch", experiment.metric_name],
        )
        session.add(memory)
        session.flush()

        if experiment.project_id:
            pm = ProjectMemory(
                project_id=experiment.project_id, memory_id=memory.id
            )
            session.add(pm)

    session.commit()
    return experiment


def get_experiment_status(
    session: Session,
    experiment_id: str,
) -> dict:
    """Get full experiment status with iteration history."""
    exp = session.get(ResearchExperiment, experiment_id)
    if not exp:
        return {"error": f"Not found: {experiment_id}"}

    iterations = (
        session.query(ResearchIteration)
        .filter(ResearchIteration.experiment_id == experiment_id)
        .order_by(ResearchIteration.iteration_number)
        .all()
    )

    keep_rate = exp.keeps / max(exp.total_iterations, 1)
    improvement = (exp.best_value or 0) - (exp.baseline_value or 0)

    return {
        "id": exp.id,
        "goal": exp.goal,
        "metric": exp.metric_name,
        "direction": exp.metric_direction,
        "status": exp.status,
        "baseline": exp.baseline_value,
        "best": exp.best_value,
        "final": exp.final_value,
        "improvement": round(improvement, 4),
        "total_iterations": exp.total_iterations,
        "keeps": exp.keeps,
        "discards": exp.discards,
        "crashes": exp.crashes,
        "keep_rate": round(keep_rate, 3),
        "trajectory": [
            {
                "iteration": it.iteration_number,
                "value": it.metric_value,
                "delta": it.delta,
                "status": it.status,
                "description": it.description,
            }
            for it in iterations
        ],
    }


def list_experiments(
    session: Session,
    project_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """List all experiments with summary stats."""
    q = session.query(ResearchExperiment)
    if project_id:
        q = q.filter(ResearchExperiment.project_id == project_id)
    if status:
        q = q.filter(ResearchExperiment.status == status)

    experiments = q.order_by(ResearchExperiment.started_at.desc()).all()

    return [
        {
            "id": e.id,
            "goal": e.goal,
            "metric": e.metric_name,
            "status": e.status,
            "baseline": e.baseline_value,
            "best": e.best_value,
            "improvement": round((e.best_value or 0) - (e.baseline_value or 0), 4),
            "iterations": e.total_iterations,
            "keep_rate": round(e.keeps / max(e.total_iterations, 1), 3),
            "project_id": e.project_id,
        }
        for e in experiments
    ]


# ── Meta-Learning ──


def get_meta_learning(session: Session) -> dict:
    """Analyze autoresearch experiments across all projects.

    Returns insights about which types of experiments succeed,
    optimal iteration counts, and cross-project patterns.
    """
    experiments = session.query(ResearchExperiment).all()
    if not experiments:
        return {"message": "No experiments yet."}

    completed = [e for e in experiments if e.status == ResearchStatus.COMPLETED.value]
    all_count = len(experiments)
    completed_count = len(completed)

    # Overall stats
    total_iterations = sum(e.total_iterations for e in experiments)
    total_keeps = sum(e.keeps for e in experiments)
    total_discards = sum(e.discards for e in experiments)
    total_crashes = sum(e.crashes for e in experiments)
    overall_keep_rate = total_keeps / max(total_iterations, 1)

    # Keep rate by metric type
    metric_stats = defaultdict(lambda: {"experiments": 0, "keeps": 0, "total": 0, "improvements": []})
    for e in experiments:
        m = metric_stats[e.metric_name]
        m["experiments"] += 1
        m["keeps"] += e.keeps
        m["total"] += e.total_iterations
        if e.best_value and e.baseline_value:
            m["improvements"].append(e.best_value - e.baseline_value)

    metric_analysis = {}
    for name, stats in metric_stats.items():
        kr = stats["keeps"] / max(stats["total"], 1)
        avg_imp = (sum(stats["improvements"]) / len(stats["improvements"])
                   if stats["improvements"] else 0)
        metric_analysis[name] = {
            "experiments": stats["experiments"],
            "keep_rate": round(kr, 3),
            "avg_improvement": round(avg_imp, 4),
            "total_iterations": stats["total"],
        }

    # Optimal iteration count (where do most improvements happen?)
    iteration_value = defaultdict(list)
    for e in experiments:
        iterations = (
            session.query(ResearchIteration)
            .filter(ResearchIteration.experiment_id == e.id)
            .order_by(ResearchIteration.iteration_number)
            .all()
        )
        for it in iterations:
            if it.status == "keep" and it.delta:
                iteration_value[it.iteration_number].append(abs(it.delta))

    # Find where improvements concentrate
    improvement_by_phase = {"early (1-10)": 0, "mid (11-30)": 0, "late (31+)": 0}
    for it_num, deltas in iteration_value.items():
        total_delta = sum(deltas)
        if it_num <= 10:
            improvement_by_phase["early (1-10)"] += total_delta
        elif it_num <= 30:
            improvement_by_phase["mid (11-30)"] += total_delta
        else:
            improvement_by_phase["late (31+)"] += total_delta

    # Cross-project patterns: which projects have best research outcomes?
    project_stats = defaultdict(lambda: {"experiments": 0, "avg_keep_rate": []})
    for e in experiments:
        ps = project_stats[e.project_id]
        ps["experiments"] += 1
        ps["avg_keep_rate"].append(e.keeps / max(e.total_iterations, 1))

    project_analysis = {}
    for pid, stats in project_stats.items():
        proj = session.get(Project, pid) if pid else None
        name = proj.name if proj else "Unknown"
        avg_kr = sum(stats["avg_keep_rate"]) / len(stats["avg_keep_rate"])
        project_analysis[name] = {
            "experiments": stats["experiments"],
            "avg_keep_rate": round(avg_kr, 3),
        }

    # Insights
    insights = []
    if overall_keep_rate > 0.5:
        insights.append(f"Strong keep rate ({overall_keep_rate:.0%}). Experiments are well-targeted.")
    elif overall_keep_rate < 0.3:
        insights.append(f"Low keep rate ({overall_keep_rate:.0%}). Consider narrower scope or different strategies.")

    best_metric = max(metric_analysis.items(), key=lambda x: x[1]["keep_rate"], default=None)
    if best_metric:
        insights.append(
            f"Best performing metric: '{best_metric[0]}' "
            f"(keep rate: {best_metric[1]['keep_rate']:.0%})"
        )

    if improvement_by_phase.get("early (1-10)", 0) > improvement_by_phase.get("late (31+)", 0) * 2:
        insights.append("Most improvements happen in first 10 iterations. Consider shorter runs.")

    if total_crashes > total_iterations * 0.15:
        insights.append(f"High crash rate ({total_crashes/max(total_iterations,1):.0%}). Check guard commands.")

    return {
        "total_experiments": all_count,
        "completed": completed_count,
        "total_iterations": total_iterations,
        "overall_keep_rate": round(overall_keep_rate, 3),
        "keeps": total_keeps,
        "discards": total_discards,
        "crashes": total_crashes,
        "by_metric": metric_analysis,
        "improvement_by_phase": improvement_by_phase,
        "by_project": project_analysis,
        "insights": insights,
    }


def _extract_metric(output: str) -> float | None:
    """Extract a numeric metric from command output.

    Tries patterns in order:
    1. "metric_name: 0.85" or "metric_name = 0.85"
    2. "TOTAL ... 73%" (coverage style)
    3. Last number on last non-empty line
    """
    if not output:
        return None

    # Pattern 1: key: value% or key = value% (percentage with label)
    match = re.search(r"(?:metric|score|accuracy|coverage|result|value)\s*[:=]\s*([\d.]+)\s*%", output, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1)) / 100
        except ValueError:
            pass

    # Pattern 2: key: value or key = value (plain number with label)
    match = re.search(r"(?:metric|score|accuracy|coverage|result|value)\s*[:=]\s*([\d.]+)", output, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    # Pattern 3: standalone percentage
    match = re.search(r"([\d.]+)\s*%", output)
    if match:
        try:
            return float(match.group(1)) / 100
        except ValueError:
            pass

    # Pattern 3: last number
    lines = [l.strip() for l in output.strip().split("\n") if l.strip()]
    if lines:
        numbers = re.findall(r"([\d.]+)", lines[-1])
        if numbers:
            try:
                return float(numbers[-1])
            except ValueError:
                pass

    return None
