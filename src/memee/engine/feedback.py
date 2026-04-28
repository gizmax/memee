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
            # anti-pattern. We map outcome to one of three honest states:
            #   - failure → MISTAKE_MADE     (warning ignored, real damage)
            #   - success → WARNING_INEFFECTIVE (warning ignored, got lucky)
            # We deliberately do NOT credit MISTAKE_AVOIDED here — that
            # would be a metric that lies about what happened, and bad
            # numbers ruin the trust users have to put in this dashboard
            # for it to be worth running.
            if outcome == "success":
                impact_kind = ImpactType.WARNING_INEFFECTIVE.value
                outcome_text = (
                    "Warning ignored; task succeeded anyway. The warning "
                    "did not change the agent's behaviour."
                )
            else:
                impact_kind = ImpactType.MISTAKE_MADE.value
                outcome_text = (
                    f"Warning ignored. Task outcome: {outcome}"
                )
            record_impact(
                session, mem.id,
                impact_kind,
                agent=agent, model=model, project_id=project_id,
                trigger="Post-task review detected anti-pattern violation",
                memory_shown=mem.title,
                agent_action=f"Violated warning: {warning.get('title', '')}",
                outcome=outcome_text,
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

    # ── Pick the single most-significant memory for the Stop receipt ──
    #
    # The Stop hook surfaces *one* sentence — the highest-impact thing that
    # happened. Significance order, highest first:
    #
    #   1. MISTAKE_MADE         (warning ignored AND task failed)
    #   2. WARNING_INEFFECTIVE  (warning ignored, task succeeded — got lucky)
    #   3. patterns_followed    (agent applied a known canon)
    #   4. new_patterns         (Memee learned something — currently 0)
    #
    # We map the chosen memory back to a row + ImpactType string so the
    # caller can render it without re-querying.
    most_sig_id: str | None = None
    most_sig_title: str | None = None
    most_sig_kind: str | None = None
    most_sig_maturity: str | None = None

    if warnings_violated:
        # Highest-severity violation wins. ``warnings`` from review.py is
        # already severity-sorted (critical → low), so element 0 is fine.
        top = warnings_violated[0]
        most_sig_id = top.get("memory_id") or None
        most_sig_title = top.get("title") or None
        most_sig_kind = (
            ImpactType.MISTAKE_MADE.value
            if outcome == "failure"
            else ImpactType.WARNING_INEFFECTIVE.value
        )
        most_sig_maturity = top.get("maturity") or None
    elif good_patterns_followed:
        # No violations — surface the strongest pattern reuse. v2.1.1
        # added a strict maturity × confidence sort in review.py so
        # ``[0]`` is the canon-est, most-confident reuse — not just the
        # first DB row.
        top = good_patterns_followed[0]
        most_sig_id = top.get("memory_id") or None
        most_sig_title = top.get("title") or None
        most_sig_kind = ImpactType.KNOWLEDGE_REUSED.value
        most_sig_maturity = top.get("maturity") or None
    # No ``new_patterns`` branch yet — placeholder. When feedback starts
    # auto-recording candidate patterns from the diff we'll fill it in.

    return {
        "patterns_followed": len(good_patterns_followed),
        "warnings_violated": len(warnings_violated),
        "new_patterns": 0,
        "patterns_details": [
            {"title": p.get("title", ""), "maturity": p.get("maturity", "")}
            for p in good_patterns_followed
        ],
        "violations_details": [
            {"title": w.get("title", ""), "severity": w.get("severity", "")}
            for w in warnings_violated
        ],
        "most_significant_memory_id": most_sig_id,
        "most_significant_memory_title": most_sig_title,
        "most_significant_kind": most_sig_kind,
        "most_significant_memory_maturity": most_sig_maturity,
        "outcome": outcome,
        "teaching_effectiveness": round(effectiveness, 2) if effectiveness is not None else None,
        "keywords_scanned": stats.get("keywords_extracted", 0),
        "lines_scanned": stats.get("lines_scanned", 0),
    }
