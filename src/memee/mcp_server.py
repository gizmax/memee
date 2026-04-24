"""Memee MCP Server — institutional memory tools for Claude Code agents."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "memee",
    version="0.1.0",
    description=(
        "Institutional memory for AI agent teams. "
        "Record, search, and share knowledge across projects. "
        "Your agents forget. Memee doesn't."
    ),
)


def _get_session():
    from memee.storage.database import get_session, init_db

    init_db()
    return get_session()


def _parse_tags(tags: str) -> list[str]:
    return [t.strip() for t in tags.split(",") if t.strip()] if tags else []


def _detect_model(model: str = "") -> str | None:
    """Detect model name from param or environment."""
    if model:
        return model
    from memee.engine.models import detect_current_model
    return detect_current_model()


def _memory_to_dict(memory) -> dict:
    return {
        "id": memory.id,
        "type": memory.type,
        "maturity": memory.maturity,
        "title": memory.title,
        "content": memory.content,
        "tags": memory.tags or [],
        "confidence_score": memory.confidence_score,
        "validation_count": memory.validation_count,
        "project_count": memory.project_count,
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
    }


# ── Memory CRUD ──


@mcp.tool()
async def memory_record(
    type: str,
    title: str,
    content: str,
    tags: str = "",
    project_path: str = "",
    context: str = "{}",
    model: str = "",
) -> str:
    """Record a new memory to organizational knowledge base.

    Types: pattern, decision, anti_pattern, lesson, observation.
    Tags are comma-separated. Context is JSON string with extra metadata.
    Model is auto-detected if not provided.
    Use this after learning something that could help future projects.
    """
    from memee.engine.quality_gate import merge_duplicate, run_quality_gate
    from memee.storage.models import Memory, Project, ProjectMemory

    session = _get_session()

    tag_list = _parse_tags(tags)
    ctx = json.loads(context) if context else {}
    model_name = _detect_model(model)

    # Quality gate
    gate = run_quality_gate(session, title, content, tag_list, type, source="llm")

    if not gate.accepted and gate.merged:
        existing = session.get(Memory, gate.merged_id)
        if existing:
            merge_duplicate(
                session, existing, content, tag_list,
                new_title=title, similarity=gate.dedup_similarity,
            )
            return json.dumps({"status": "merged", "existing_id": existing.id,
                               "title": existing.title})

    if not gate.accepted and gate.flagged and gate.reason == "large_cluster_manual_review":
        return json.dumps({
            "status": "flagged",
            "reason": gate.reason,
            "candidate_id": gate.merged_id,
            "similarity": gate.dedup_similarity,
            "issues": gate.issues,
        })

    if not gate.accepted:
        return json.dumps({"status": "rejected", "issues": gate.issues})

    memory = Memory(
        type=type,
        title=title,
        content=content,
        tags=tag_list,
        context=ctx,
        source_model=model_name,
        confidence_score=gate.initial_confidence,
        source_type=gate.source_type,
        quality_score=gate.quality_score,
    )
    session.add(memory)

    if project_path:
        abs_path = str(Path(project_path).resolve())
        proj = session.query(Project).filter_by(path=abs_path).first()
        if proj:
            pm = ProjectMemory(project_id=proj.id, memory_id=memory.id)
            session.add(pm)

    session.commit()

    return json.dumps({
        "status": "recorded",
        "memory": _memory_to_dict(memory),
    })


@mcp.tool()
async def memory_search(
    query: str,
    type: str = "",
    tags: str = "",
    limit: int = 10,
) -> str:
    """Search organizational memory using natural language.

    Returns memories ranked by relevance (BM25 + tag overlap + confidence).
    Use this to find existing knowledge before solving a problem.

    The response includes a ``query_event_id`` — pass it to
    ``search_feedback`` once you pick a result so Memee can track hit@k.
    """
    from memee.engine.search import search_memories
    from memee.storage.models import SearchEvent

    session = _get_session()
    tag_list = _parse_tags(tags) or None

    results = search_memories(
        session,
        query,
        tags=tag_list,
        memory_type=type or None,
        limit=limit,
    )

    # Grab the most recent SearchEvent row for this session — telemetry just
    # wrote it. This is a cheap way to expose the id to the caller without
    # changing search_memories' signature.
    latest_event = (
        session.query(SearchEvent).order_by(SearchEvent.created_at.desc()).first()
    )

    return json.dumps({
        "count": len(results),
        "query_event_id": latest_event.id if latest_event else None,
        "results": [
            {
                **_memory_to_dict(r["memory"]),
                "score": r["total_score"],
            }
            for r in results
        ],
    })


@mcp.tool()
async def search_feedback(
    query_event_id: str,
    accepted_memory_id: str,
    position: int = -1,
) -> str:
    """Mark which memory the agent actually used after a memory_search call.

    Pass the ``query_event_id`` you received from memory_search and the id of
    the memory that solved your task. ``position`` is the 0-based rank the
    memory had in the results (use -1 if unknown — hit@3 won't count that
    event but accepted_rate still will).

    Memee needs this signal to compute retrieval quality (hit@1, hit@3).
    Without feedback we can't tell good results from silent rejections.
    """
    from memee.engine.telemetry import mark_event_accepted

    session = _get_session()
    ok = mark_event_accepted(
        session,
        event_id=query_event_id,
        memory_id=accepted_memory_id,
        position=None if position is None or position < 0 else int(position),
    )
    return json.dumps({"ok": ok, "event_id": query_event_id})


@mcp.tool()
async def memory_suggest(
    context: str,
    project_path: str = "",
    tags: str = "",
    limit: int = 5,
) -> str:
    """Get cross-project suggestions for current task context.

    Describe what you're working on and get relevant memories from other projects.
    Pattern Resonance finds matches by text relevance + tag overlap + maturity.
    """
    from memee.engine.search import search_memories

    session = _get_session()
    tag_list = _parse_tags(tags) or None

    results = search_memories(session, context, tags=tag_list, limit=limit)

    suggestions = []
    for r in results:
        m = r["memory"]
        suggestions.append({
            **_memory_to_dict(m),
            "resonance_score": r["total_score"],
        })

    return json.dumps({
        "context": context[:100],
        "suggestion_count": len(suggestions),
        "suggestions": suggestions,
    })


@mcp.tool()
async def memory_validate(
    memory_id: str,
    evidence: str = "",
    project_path: str = "",
    model: str = "",
) -> str:
    """Confirm a memory worked in this context.

    Increases confidence score and may promote maturity level.
    Cross-project validations get 1.5x bonus.
    Cross-model validations get 2.0x bonus (AI peer review).
    Combined cross-project + cross-model = 3.0x maximum bonus.
    """
    from memee.engine.confidence import update_confidence
    from memee.storage.models import Memory, MemoryValidation, Project

    session = _get_session()
    memory = session.get(Memory, memory_id)
    if not memory:
        return json.dumps({"error": f"Memory not found: {memory_id}"})

    model_name = _detect_model(model)
    project_id = None
    if project_path:
        abs_path = str(Path(project_path).resolve())
        proj = session.query(Project).filter_by(path=abs_path).first()
        if proj:
            project_id = proj.id

    validation = MemoryValidation(
        memory_id=memory.id,
        project_id=project_id,
        validated=True,
        evidence=evidence,
        validator_model=model_name,
    )
    session.add(validation)

    old_maturity = memory.maturity
    new_score = update_confidence(
        memory, validated=True, project_id=project_id, model_name=model_name
    )
    memory.last_validated_at = datetime.now(timezone.utc)

    session.commit()

    return json.dumps({
        "status": "validated",
        "memory_id": memory.id,
        "confidence": new_score,
        "maturity_change": f"{old_maturity} -> {memory.maturity}",
    })


@mcp.tool()
async def memory_invalidate(
    memory_id: str,
    reason: str,
    project_path: str = "",
) -> str:
    """Report that a memory didn't hold in this context.

    Decreases confidence score. If invalidated enough times, gets deprecated.
    """
    from memee.engine.confidence import update_confidence
    from memee.storage.models import Memory, MemoryValidation, Project

    session = _get_session()
    memory = session.get(Memory, memory_id)
    if not memory:
        return json.dumps({"error": f"Memory not found: {memory_id}"})

    project_id = None
    if project_path:
        abs_path = str(Path(project_path).resolve())
        proj = session.query(Project).filter_by(path=abs_path).first()
        if proj:
            project_id = proj.id

    validation = MemoryValidation(
        memory_id=memory.id,
        project_id=project_id,
        validated=False,
        evidence=reason,
    )
    session.add(validation)

    old_maturity = memory.maturity
    new_score = update_confidence(memory, validated=False, project_id=project_id)

    session.commit()

    return json.dumps({
        "status": "invalidated",
        "memory_id": memory.id,
        "confidence": new_score,
        "maturity_change": f"{old_maturity} -> {memory.maturity}",
    })


# ── Decisions ──


@mcp.tool()
async def decision_record(
    chosen: str,
    title: str,
    alternatives: str = "[]",
    criteria: str = "[]",
    project_path: str = "",
    reversible: bool = True,
) -> str:
    """Record a technical decision: why X over Y.

    Decision archaeology: when an agent encounters the same choice in a new project,
    it sees the full history of what was chosen before and why.
    Alternatives is JSON array: [{"name": "X", "reason_rejected": "..."}]
    """
    from memee.engine.quality_gate import run_quality_gate
    from memee.storage.models import Decision, Memory, MemoryType, Project, ProjectMemory

    session = _get_session()

    alt_list = json.loads(alternatives) if alternatives else []
    crit_list = json.loads(criteria) if criteria else []
    content = f"Chose {chosen}. Alternatives: {alternatives}"

    gate = run_quality_gate(session, title, content, ["decision"], "decision", source="llm")
    if not gate.accepted and not gate.merged:
        return json.dumps({"status": "rejected", "issues": gate.issues})

    memory = Memory(
        type=MemoryType.DECISION.value,
        title=title,
        content=content,
        confidence_score=gate.initial_confidence,
        source_type=gate.source_type,
        quality_score=gate.quality_score,
    )
    session.add(memory)
    session.flush()

    decision = Decision(
        memory_id=memory.id,
        chosen=chosen,
        alternatives=alt_list,
        criteria=crit_list,
        reversible=reversible,
    )
    session.add(decision)

    if project_path:
        abs_path = str(Path(project_path).resolve())
        proj = session.query(Project).filter_by(path=abs_path).first()
        if proj:
            pm = ProjectMemory(project_id=proj.id, memory_id=memory.id)
            session.add(pm)

    session.commit()

    return json.dumps({
        "status": "recorded",
        "decision": {
            "memory_id": memory.id,
            "chosen": chosen,
            "alternatives": alt_list,
        },
    })


# ── Anti-Patterns ──


@mcp.tool()
async def antipattern_record(
    title: str,
    trigger: str,
    consequence: str,
    severity: str = "medium",
    alternative: str = "",
    tags: str = "",
) -> str:
    """Record an anti-pattern: what NOT to do, and why.

    Severity: low, medium, high, critical.
    Other agents will be warned when their approach matches this anti-pattern.
    """
    from memee.engine.quality_gate import run_quality_gate
    from memee.storage.models import AntiPattern, Memory, MemoryType

    session = _get_session()
    tag_list = _parse_tags(tags)
    content = f"Trigger: {trigger}\nConsequence: {consequence}\nAlternative: {alternative}"

    gate = run_quality_gate(session, title, content, tag_list, "anti_pattern", source="llm")
    if not gate.accepted and not gate.merged:
        return json.dumps({"status": "rejected", "issues": gate.issues})

    memory = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        title=title,
        content=f"Trigger: {trigger}\nConsequence: {consequence}\nAlternative: {alternative}",
        tags=tag_list,
    )
    session.add(memory)
    session.flush()

    ap = AntiPattern(
        memory_id=memory.id,
        severity=severity,
        trigger=trigger,
        consequence=consequence,
        alternative=alternative,
    )
    session.add(ap)
    session.commit()

    return json.dumps({
        "status": "recorded",
        "anti_pattern": {
            "memory_id": memory.id,
            "title": title,
            "severity": severity,
        },
    })


@mcp.tool()
async def antipattern_check(
    context: str,
    tags: str = "",
) -> str:
    """Check current approach against known anti-patterns.

    Call this BEFORE implementing to avoid repeating known mistakes.
    Describe what you're about to do and get warnings if it matches anti-patterns.
    """
    from memee.engine.search import search_anti_patterns

    session = _get_session()
    tag_list = _parse_tags(tags) or None

    results = search_anti_patterns(session, context, tags=tag_list)

    if not results:
        return json.dumps({"status": "clear", "message": "No matching anti-patterns."})

    warnings = []
    for r in results:
        m = r["memory"]
        ap = m.anti_pattern
        if ap:
            warnings.append({
                "memory_id": m.id,
                "title": m.title,
                "severity": ap.severity,
                "trigger": ap.trigger,
                "consequence": ap.consequence,
                "alternative": ap.alternative or "",
                "confidence": m.confidence_score,
            })

    return json.dumps({
        "status": "warning",
        "count": len(warnings),
        "warnings": warnings,
    })


# ── Autoresearch ──


@mcp.tool()
async def research_create(
    goal: str,
    metric_name: str,
    verify_command: str,
    metric_direction: str = "higher",
    guard_command: str = "",
    scope: str = "",
    project_path: str = "",
    baseline: float = -1,
) -> str:
    """Create a new autoresearch experiment.

    Define a goal, a metric to optimize, and commands to measure it.
    The verify_command should print the metric value. The guard_command
    must exit 0 (e.g., test suite) to ensure nothing breaks.

    Example: research_create(
        goal="Increase test coverage to 90%",
        metric_name="coverage",
        verify_command="pytest --cov --cov-branch | grep TOTAL | awk '{print $4}'",
        guard_command="pytest tests/ -q --tb=no",
        project_path="/my/project"
    )
    """
    from memee.engine.research import create_experiment
    from memee.storage.models import Project

    session = _get_session()

    project_id = None
    if project_path:
        abs_path = str(Path(project_path).resolve())
        proj = session.query(Project).filter_by(path=abs_path).first()
        if proj:
            project_id = proj.id

    if not project_id:
        proj = session.query(Project).first()
        project_id = proj.id if proj else None

    if not project_id:
        return json.dumps({"error": "No project found."})

    scope_list = [s.strip() for s in scope.split(",") if s.strip()] if scope else []
    baseline_val = baseline if baseline >= 0 else None

    exp = create_experiment(
        session, project_id, goal, metric_name, metric_direction,
        verify_command, guard_command, scope_list, baseline_val,
    )

    return json.dumps({
        "status": "created",
        "experiment_id": exp.id,
        "goal": goal,
        "metric": metric_name,
        "baseline": exp.baseline_value,
    })


@mcp.tool()
async def research_log(
    experiment_id: str,
    metric_value: float,
    status: str,
    description: str = "",
    commit_hash: str = "",
) -> str:
    """Log an autoresearch iteration result.

    Status: keep (improvement kept), discard (no improvement), crash (error).
    Call this after each modification attempt.
    """
    from memee.engine.research import log_iteration

    session = _get_session()

    try:
        it = log_iteration(session, experiment_id, metric_value, status, description, commit_hash)
        exp = it.experiment
        return json.dumps({
            "status": "logged",
            "iteration": it.iteration_number,
            "result": it.status,
            "delta": it.delta,
            "keep_rate": round(exp.keeps / max(exp.total_iterations, 1), 3),
            "best": exp.best_value,
        })
    except ValueError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
async def research_status(experiment_id: str = "") -> str:
    """Get experiment status or list all experiments.

    Pass experiment_id for detailed status with trajectory.
    Omit for a summary list of all experiments.
    """
    from memee.engine.research import get_experiment_status, list_experiments

    session = _get_session()

    if experiment_id:
        return json.dumps(get_experiment_status(session, experiment_id))
    return json.dumps(list_experiments(session))


@mcp.tool()
async def research_meta() -> str:
    """Meta-learning: analyze patterns across all autoresearch experiments.

    Returns insights about which types of experiments succeed,
    optimal iteration counts, and cross-project patterns.
    """
    from memee.engine.research import get_meta_learning

    session = _get_session()
    return json.dumps(get_meta_learning(session))


@mcp.tool()
async def research_complete(
    experiment_id: str,
    status: str = "completed",
) -> str:
    """Mark an experiment as completed/failed/cancelled.

    Completed experiments with improvements are recorded as organizational lessons.
    """
    from memee.engine.research import complete_experiment
    from memee.storage.models import ResearchExperiment

    session = _get_session()
    exp = session.get(ResearchExperiment, experiment_id)
    if not exp:
        return json.dumps({"error": f"Not found: {experiment_id}"})

    complete_experiment(session, exp, status)
    return json.dumps({
        "status": status,
        "experiment_id": exp.id,
        "final_value": exp.final_value,
        "improvement": round((exp.best_value or 0) - (exp.baseline_value or 0), 4),
    })


# ── Briefing (PUSH knowledge to agents) ──


@mcp.tool()
async def get_briefing(
    project_path: str = "",
    task: str = "",
    token_budget: int = 500,
) -> str:
    """Get a smart knowledge briefing BEFORE starting a task.

    CALL THIS FIRST when starting work on a project.
    Describe your task and get ONLY relevant knowledge — not everything.

    Example: get_briefing(task="write unit tests for auth module")
    → Returns testing + security patterns only (~300 tokens, not 14,000)

    Token-budgeted: max 500 tokens by default. Adjust with token_budget.
    """
    from memee.engine.router import smart_briefing

    session = _get_session()
    abs_path = str(Path(project_path).resolve()) if project_path else None
    return smart_briefing(session, abs_path, task=task, token_budget=token_budget)


@mcp.tool()
async def post_task_feedback(
    diff_text: str,
    project_path: str = "",
    outcome: str = "success",
    model: str = "",
) -> str:
    """Report what happened AFTER a task. Closes the feedback loop.

    Pass the git diff and outcome. Memee checks:
    - Did you follow recommended patterns? (validates them)
    - Did you violate any warnings? (records incident)
    - Teaching effectiveness score.
    """
    from memee.engine.feedback import post_task_review

    session = _get_session()
    model_name = _detect_model(model)
    result = post_task_review(
        session, diff_text, project_path,
        model=model_name or "", outcome=outcome,
    )
    return json.dumps(result)


# ── Analytics ──


@mcp.tool()
async def learning_status() -> str:
    """Organizational learning dashboard: memory stats, maturity distribution, learning rate.

    Use this to understand how the organization's knowledge is growing.
    """
    from sqlalchemy import func

    from memee.storage.models import Memory, Organization, Project

    session = _get_session()

    total = session.query(func.count(Memory.id)).scalar() or 0
    if total == 0:
        return json.dumps({"status": "empty", "message": "No memories recorded yet."})

    maturity_counts = dict(
        session.query(Memory.maturity, func.count(Memory.id))
        .group_by(Memory.maturity)
        .all()
    )

    type_counts = dict(
        session.query(Memory.type, func.count(Memory.id))
        .group_by(Memory.type)
        .all()
    )

    avg_confidence = session.query(func.avg(Memory.confidence_score)).scalar() or 0
    project_count = session.query(func.count(Project.id)).scalar() or 0

    return json.dumps({
        "total_memories": total,
        "projects": project_count,
        "avg_confidence": round(avg_confidence, 3),
        "maturity_distribution": maturity_counts,
        "type_distribution": type_counts,
    })


@mcp.tool()
async def canon_list(category: str = "") -> str:
    """List all canonical best practices — the organizational truth.

    These are memories that have been validated across 5+ projects
    with 85%+ confidence. Filter by tag/category.
    """
    from memee.storage.models import MaturityLevel, Memory

    session = _get_session()

    q = session.query(Memory).filter(Memory.maturity == MaturityLevel.CANON.value)

    if category:
        q = q.filter(Memory.tags.contains(category))

    canons = q.all()

    return json.dumps({
        "count": len(canons),
        "canon": [_memory_to_dict(m) for m in canons],
    })


# ── Auto-Propagation ──


@mcp.tool()
async def propagate_patterns(
    confidence_threshold: float = 0.55,
    max_propagations: int = 500,
) -> str:
    """Auto-propagate validated patterns to projects with matching stacks.

    Finds memories above the confidence threshold and pushes them to all
    projects whose stack/tags overlap. This is how knowledge spreads
    across the organization automatically.
    """
    from memee.engine.propagation import run_propagation_cycle

    session = _get_session()
    stats = run_propagation_cycle(
        session, confidence_threshold, max_propagations=max_propagations
    )

    return json.dumps({
        "status": "completed",
        "memories_checked": stats["memories_checked"],
        "memories_propagated": stats["memories_propagated"],
        "new_links": stats["total_new_links"],
        "projects_reached": stats["projects_reached"],
    })


# ── Predictive Anti-Pattern Push ──


@mcp.tool()
async def predict_warnings(
    project_path: str = "",
) -> str:
    """Scan a project's stack against all known anti-patterns.

    PROACTIVELY pushes relevant warnings — don't wait for the agent to check.
    Call this when starting work on a project to get all applicable warnings upfront.
    """
    from memee.engine.predictive import scan_project_for_warnings
    from memee.storage.models import Project

    session = _get_session()

    if project_path:
        abs_path = str(Path(project_path).resolve())
        project = session.query(Project).filter_by(path=abs_path).first()
    else:
        project = session.query(Project).first()

    if not project:
        return json.dumps({"error": "Project not found. Register it first."})

    warnings = scan_project_for_warnings(session, project)

    return json.dumps({
        "project": project.name,
        "warning_count": len(warnings),
        "warnings": warnings,
    })


# ── Memory Inheritance ──


@mcp.tool()
async def inherit_knowledge(
    project_path: str,
    min_confidence: float = 0.6,
    max_inherit: int = 50,
) -> str:
    """Inherit validated patterns from similar-stack projects.

    When starting a new project, call this to get a head start with
    proven patterns from projects with overlapping technology stacks.
    Don't start from zero.
    """
    from memee.engine.inheritance import inherit_memories
    from memee.storage.models import Project

    session = _get_session()
    abs_path = str(Path(project_path).resolve())
    project = session.query(Project).filter_by(path=abs_path).first()

    if not project:
        return json.dumps({"error": f"Project not found at {project_path}"})

    stats = inherit_memories(
        session, project,
        min_memory_confidence=min_confidence,
        max_inherit=max_inherit,
    )

    return json.dumps({
        "project": project.name,
        "similar_projects": stats["similar_projects"],
        "memories_inherited": stats["memories_inherited"],
        "by_type": stats["by_type"],
    })


# ── Dream Mode ──


@mcp.tool()
async def run_dream() -> str:
    """Run Dream Mode: nightly knowledge processing cycle.

    Auto-connects related memories, finds contradictions, boosts
    well-connected memories, proposes promotions, and extracts meta-patterns.
    Run this periodically (nightly or weekly) to keep knowledge healthy.
    """
    from memee.engine.dream import run_dream_cycle

    session = _get_session()
    stats = run_dream_cycle(session)

    return json.dumps({
        "status": "completed",
        "connections_created": stats["connections_created"],
        "contradictions_found": stats["contradictions_found"],
        "confidence_boosts": stats["confidence_boosts"],
        "promotions": f"{stats['promotions_applied']}/{stats['promotions_proposed']}",
        "meta_patterns": stats["meta_patterns"],
        "aging": stats.get("aging", {}),
    })


# ── Code Review ──


@mcp.tool()
async def review_code(
    diff_text: str,
    project_path: str = "",
) -> str:
    """Review a git diff against organizational memory.

    Paste or pipe a git diff and get:
    - WARNINGS: code matches known anti-patterns
    - CONFIRMATIONS: code follows validated best practices
    - SUGGESTIONS: related patterns that might help

    Use before merging to catch institutional knowledge violations.
    """
    from memee.engine.review import review_diff

    session = _get_session()
    result = review_diff(session, diff_text, project_path)

    return json.dumps({
        "warnings": result["warnings"],
        "confirmations": result["confirmations"],
        "suggestions": result["suggestions"],
        "stats": result.get("stats", {}),
    })


# ── CMAM (Claude Managed Agents Memory) sync ──


@mcp.tool()
async def sync_to_cmam(
    store_id: str = "",
    backend: str = "fs",
    local_root: str = "",
    dry_run: bool = False,
) -> str:
    """Push Memee's CANON memories + critical anti-patterns to a CMAM store.

    Canon is what Memee has confirmed across ≥5 projects with ≥10 validations.
    Critical anti-patterns propagate regardless. Once synced, a Claude agent
    running with managed memory sees this knowledge in /mnt/memory/ from the
    very first turn — no MCP call needed for baseline org context.

    backend="fs" writes to a local directory (mount into a container);
    backend="api" calls Anthropic's managed memory API (needs ANTHROPIC_API_KEY).
    """
    from memee.adapters.cmam import CMAMConfig, sync_to_cmam as _sync
    from memee.config import settings
    import os as _os
    from pathlib import Path as _Path

    cfg = CMAMConfig(
        store_id=store_id or settings.cmam_store_id,
        backend=backend or settings.cmam_backend,
        local_root=_Path(local_root) if local_root else settings.cmam_local_root,
        api_base=settings.cmam_api_base,
        api_key=_os.environ.get("ANTHROPIC_API_KEY"),
        redact=settings.cmam_redact,
    )
    session = _get_session()
    result = _sync(session, cfg, dry_run=dry_run)

    return json.dumps({
        "store_id": cfg.store_id,
        "backend": cfg.backend,
        "pushed": result.pushed,
        "updated": result.updated,
        "rejected": len(result.rejected),
        "store_count": result.store_count,
        "store_bytes": result.store_bytes,
        "warnings": result.warnings,
        "dry_run": dry_run,
    })
