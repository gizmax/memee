"""Post-task feedback loop: did the agent USE what Memee taught?

Measures teaching effectiveness:
  1. Was agent briefed? (pre-task)
  2. Did agent follow patterns? (during task — check diff)
  3. Did agent violate warnings? (during task — check diff)
  4. Record outcome (post-task — success/failure)

Feedback feeds back into confidence:
  - Pattern used + succeeded → validate (boost confidence)
  - Warning heeded → validate anti-pattern
  - Warning ignored + failed → stronger anti-pattern signal
  - Pattern used + failed → invalidate (maybe it's wrong)
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from memee.engine.confidence import update_confidence
from memee.engine.impact import ImpactType, record_impact
from memee.engine.review import review_diff
from memee.storage.models import (
    Memory,
    MemoryValidation,
    Project,
)


def post_task_review(
    session: Session,
    diff_text: str,
    project_path: str = "",
    agent: str = "",
    model: str = "",
    outcome: str = "success",
) -> dict:
    """Review what happened after a task and extract learnings.

    Args:
        diff_text: git diff of changes made
        project_path: project where task was done
        agent: agent/developer name
        model: AI model used
        outcome: "success" or "failure"

    Returns:
        Teaching effectiveness report
    """
    # Scan diff against memory
    review = review_diff(session, diff_text, project_path)

    warnings_violated = review["warnings"]
    good_patterns_followed = review["confirmations"]
    stats = review.get("stats", {})

    project = None
    if project_path:
        from pathlib import Path
        abs_path = str(Path(project_path).resolve())
        project = session.query(Project).filter_by(path=abs_path).first()

    project_id = project.id if project else None

    # Record impact for each pattern followed
    patterns_recorded = 0
    for pattern in good_patterns_followed:
        mem = session.get(Memory, pattern.get("memory_id"))
        if mem:
            record_impact(
                session, mem.id,
                ImpactType.KNOWLEDGE_REUSED.value,
                agent=agent, model=model, project_id=project_id,
                trigger="Post-task review detected pattern usage",
                memory_shown=mem.title,
                agent_action="Applied pattern in code",
                outcome=outcome,
            )

            # Validate: pattern was used
            if outcome == "success":
                v = MemoryValidation(
                    memory_id=mem.id, project_id=project_id,
                    validated=True, validator_model=model,
                    evidence=f"Auto-detected in diff. Task outcome: {outcome}",
                )
                session.add(v)
                update_confidence(mem, True, project_id, model)

            patterns_recorded += 1

    # Record impact for each warning violated
    violations_recorded = 0
    for warning in warnings_violated:
        mem_id = warning.get("memory_id")
        if mem_id:
            mem = session.get(Memory, mem_id)
            if not mem:
                continue

            # A violation means the agent wrote code that matches a known
            # anti-pattern. If the task succeeded anyway we still credit
            # MISTAKE_AVOIDED (the warning surfaced in review was visible and
            # the agent's work passed). If the task failed — the warning was
            # ignored AND there's a real negative outcome — it's a MISTAKE_MADE.
            impact_kind = (
                ImpactType.MISTAKE_AVOIDED.value
                if outcome == "success"
                else ImpactType.MISTAKE_MADE.value
            )
            record_impact(
                session, mem.id,
                impact_kind,
                agent=agent, model=model, project_id=project_id,
                trigger="Post-task review detected anti-pattern violation",
                memory_shown=mem.title,
                agent_action=f"Violated warning: {warning.get('title', '')}",
                outcome=f"Warning ignored. Task outcome: {outcome}",
                severity_avoided=warning.get("severity", "medium"),
            )

            # If task FAILED and warning was violated → strong signal
            if outcome == "failure":
                v = MemoryValidation(
                    memory_id=mem.id, project_id=project_id,
                    validated=True, validator_model=model,
                    evidence="Warning ignored → task failed. Strong validation.",
                )
                session.add(v)
                update_confidence(mem, True, project_id, model)

            violations_recorded += 1

    session.commit()

    # Teaching effectiveness
    total_taught = len(good_patterns_followed) + len(warnings_violated)
    effectiveness = (
        patterns_recorded / max(total_taught, 1)
        if total_taught > 0 else None
    )

    return {
        "patterns_followed": len(good_patterns_followed),
        "warnings_violated": len(warnings_violated),
        "patterns_details": [
            {"title": p.get("title", ""), "maturity": p.get("maturity", "")}
            for p in good_patterns_followed
        ],
        "violations_details": [
            {"title": w.get("title", ""), "severity": w.get("severity", "")}
            for w in warnings_violated
        ],
        "outcome": outcome,
        "teaching_effectiveness": round(effectiveness, 2) if effectiveness is not None else None,
        "keywords_scanned": stats.get("keywords_extracted", 0),
        "lines_scanned": stats.get("lines_scanned", 0),
    }
