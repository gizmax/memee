"""Memee CLI — institutional memory for AI agent companies."""

from __future__ import annotations

from pathlib import Path

import click

from memee.config import settings


@click.group()
@click.option("--org", default=None, help="Organization name override")
@click.pass_context
def cli(ctx, org):
    """Memee — Your agents forget. Memee doesn't."""
    ctx.ensure_object(dict)
    ctx.obj["org"] = org or settings.org_name


# ── Init ──


@cli.command()
@click.argument("mode", required=False, default=None,
                type=click.Choice(["solo", "join", "team", None]))
def setup(mode):
    """Interactive setup wizard with beautiful terminal UI."""
    from memee.installer import run_setup, _setup_solo, _setup_join, _setup_team_lead, _clear

    if mode == "solo":
        _clear()
        from memee.installer import LOGO, TAGLINE
        print(LOGO)
        print(TAGLINE)
        _setup_solo()
    elif mode == "join":
        _clear()
        from memee.installer import LOGO, TAGLINE
        print(LOGO)
        print(TAGLINE)
        _setup_join()
    elif mode == "team":
        _clear()
        from memee.installer import LOGO, TAGLINE
        print(LOGO)
        print(TAGLINE)
        _setup_team_lead()
    else:
        run_setup()


@cli.command()
@click.option("--no-fix", is_flag=True, help="Don't auto-fix issues, just report")
def doctor(no_fix):
    """Health check: scan system, detect AI tools, fix configuration."""
    from memee.doctor import print_doctor_report, run_doctor

    results = run_doctor(auto_fix=not no_fix)
    print_doctor_report(results)


@cli.command()
@click.pass_context
def init(ctx):
    """Initialize Memee database and organization."""
    from memee.storage.database import get_session, init_db
    from memee.storage.models import Organization

    engine = init_db()
    session = get_session(engine)

    org_name = ctx.obj["org"]
    existing = session.query(Organization).filter_by(name=org_name).first()
    if existing:
        click.echo(f"Organization '{org_name}' already exists. DB: {settings.db_path}")
        return

    org = Organization(name=org_name)
    session.add(org)
    session.commit()
    click.echo(f"Memee initialized. Org: {org_name}, DB: {settings.db_path}")


# ── Record ──


@cli.command()
@click.argument(
    "type",
    type=click.Choice(["pattern", "decision", "anti_pattern", "lesson", "observation"]),
)
@click.argument("title")
@click.option("--content", "-c", default="", help="Full content of the memory")
@click.option("--tags", "-t", default="", help="Comma-separated tags")
@click.option("--project", "-p", default="", help="Project path to link")
def record(type, title, content, tags, project):
    """Record a new memory to organizational knowledge base."""
    from memee.engine.quality_gate import merge_duplicate, run_quality_gate
    from memee.storage.database import get_session, init_db
    from memee.storage.models import Memory

    engine = init_db()
    session = get_session(engine)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    actual_content = content or title

    # Quality gate
    gate = run_quality_gate(session, title, actual_content, tag_list, type, source="human")

    if not gate.accepted and gate.merged:
        existing = session.get(Memory, gate.merged_id)
        if existing:
            merge_duplicate(
                session, existing, actual_content, tag_list,
                new_title=title, similarity=gate.dedup_similarity,
            )
            click.echo(f"Merged into existing: {existing.title} (id: {existing.id[:8]}...)")
            return

    if not gate.accepted and gate.flagged and gate.reason == "large_cluster_manual_review":
        click.echo(
            f"Flagged for manual review (large cluster): {'; '.join(gate.issues)}"
        )
        return

    if not gate.accepted:
        click.echo(f"Rejected: {'; '.join(gate.issues)}")
        return

    if gate.flagged:
        click.echo(f"Warning: {'; '.join(gate.issues)}")

    memory = Memory(
        type=type,
        title=title,
        content=actual_content,
        tags=tag_list,
        confidence_score=gate.initial_confidence,
        source_type=gate.source_type,
        quality_score=gate.quality_score,
    )
    session.add(memory)

    if project:
        _link_memory_to_project(session, memory, project)

    session.commit()
    click.echo(f"Recorded [{type}] {title} (id: {memory.id[:8]}...)")
    if tag_list:
        click.echo(f"  Tags: {', '.join(tag_list)}")
    click.echo(f"  Confidence: {gate.initial_confidence:.0%} | Quality: {gate.quality_score:.1f}/5")


# ── Search ──


@cli.command()
@click.argument("query")
@click.option("--type", "-t", "memory_type", default=None, help="Filter by memory type")
@click.option("--tags", default="", help="Comma-separated tags to boost")
@click.option("--limit", "-n", default=10, help="Max results")
def search(query, memory_type, tags, limit):
    """Search organizational memory."""
    from memee.engine.search import search_memories
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    results = search_memories(
        session, query, tags=tag_list, memory_type=memory_type, limit=limit
    )

    if not results:
        click.echo("No memories found.")
        return

    for i, r in enumerate(results, 1):
        m = r["memory"]
        score = r["total_score"]
        mat = m.maturity.upper()[:3]
        conf = f"{m.confidence_score:.0%}"
        tags_str = ", ".join(m.tags) if m.tags else ""
        click.echo(f"  {i}. [{mat}|{conf}] {m.title}")
        click.echo(f"     Type: {m.type} | Score: {score:.3f} | ID: {m.id[:8]}")
        if tags_str:
            click.echo(f"     Tags: {tags_str}")


# ── Suggest ──


@cli.command()
@click.option("--context", "-c", required=True, help="Current task context")
@click.option("--tags", "-t", default="", help="Comma-separated tags")
@click.option("--limit", "-n", default=5, help="Max suggestions")
def suggest(context, tags, limit):
    """Get cross-project suggestions for current context."""
    from memee.engine.search import search_memories
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    results = search_memories(session, context, tags=tag_list, limit=limit)

    if not results:
        click.echo("No suggestions found for this context.")
        return

    click.echo(f"Suggestions for: {context[:60]}...")
    for i, r in enumerate(results, 1):
        m = r["memory"]
        click.echo(
            f"  {i}. [{m.maturity}|{m.confidence_score:.0%}] {m.title}"
        )
        if m.content and m.content != m.title:
            click.echo(f"     {m.content[:100]}")


# ── Decide ──


@cli.command()
@click.argument("chosen")
@click.option("--over", "alternatives", required=True, help="Rejected alternatives, comma-separated")
@click.option("--reason", "-r", default="", help="Why this was chosen")
@click.option("--project", "-p", default="", help="Project path")
def decide(chosen, alternatives, reason, project):
    """Record a technical decision: why X over Y."""
    from memee.storage.database import get_session, init_db
    from memee.storage.models import Decision, Memory, MemoryType

    engine = init_db()
    session = get_session(engine)

    alt_list = [
        {"name": a.strip(), "reason_rejected": ""}
        for a in alternatives.split(",")
        if a.strip()
    ]

    memory = Memory(
        type=MemoryType.DECISION.value,
        title=f"Decision: {chosen} over {alternatives}",
        content=reason or f"Chose {chosen} over {alternatives}",
    )
    session.add(memory)
    session.flush()

    decision = Decision(
        memory_id=memory.id,
        chosen=chosen,
        alternatives=alt_list,
        criteria=[],
    )
    session.add(decision)

    if project:
        _link_memory_to_project(session, memory, project)

    session.commit()
    click.echo(f"Decision recorded: {chosen} (over {alternatives})")
    click.echo(f"  ID: {memory.id[:8]}...")


# ── Warn (Anti-Pattern) ──


@cli.command()
@click.argument("title")
@click.option(
    "--severity", "-s",
    type=click.Choice(["low", "medium", "high", "critical"]),
    default="medium",
)
@click.option("--trigger", required=True, help="When does this problem occur?")
@click.option("--consequence", required=True, help="What goes wrong?")
@click.option("--alternative", "-a", default="", help="What to do instead")
@click.option("--tags", "-t", default="", help="Comma-separated tags")
def warn(title, severity, trigger, consequence, alternative, tags):
    """Record an anti-pattern: what NOT to do."""
    from memee.storage.database import get_session, init_db
    from memee.storage.models import AntiPattern, Memory, MemoryType

    engine = init_db()
    session = get_session(engine)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

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

    icon = {"low": "~", "medium": "!", "high": "!!", "critical": "!!!"}
    click.echo(f"Anti-pattern [{icon.get(severity, '!')}] {title}")
    click.echo(f"  Trigger: {trigger}")
    click.echo(f"  Consequence: {consequence}")
    if alternative:
        click.echo(f"  Alternative: {alternative}")
    click.echo(f"  ID: {memory.id[:8]}...")


# ── Check ──


@cli.command()
@click.argument("context")
@click.option("--tags", "-t", default="", help="Comma-separated tags")
def check(context, tags):
    """Check context against known anti-patterns."""
    from memee.engine.search import search_anti_patterns
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    results = search_anti_patterns(session, context, tags=tag_list)

    if not results:
        click.echo("No matching anti-patterns found. You're clear.")
        return

    click.echo(f"WARNING: {len(results)} anti-pattern(s) match your context:")
    for i, r in enumerate(results, 1):
        m = r["memory"]
        ap = m.anti_pattern
        if ap:
            sev = ap.severity.upper()
            click.echo(f"  {i}. [{sev}] {m.title}")
            click.echo(f"     Trigger: {ap.trigger}")
            click.echo(f"     Consequence: {ap.consequence}")
            if ap.alternative:
                click.echo(f"     Do instead: {ap.alternative}")
        else:
            click.echo(f"  {i}. {m.title}")


# ── Validate ──


@cli.command()
@click.argument("memory_id")
@click.option("--evidence", "-e", default="", help="Evidence that it worked")
@click.option("--project", "-p", default="", help="Project path")
def validate(memory_id, evidence, project):
    """Validate a memory — confirm it worked in this context."""
    from memee.engine.confidence import update_confidence
    from memee.storage.database import get_session, init_db
    from memee.storage.models import MemoryValidation

    engine = init_db()
    session = get_session(engine)

    # Support partial ID matching
    memory = _find_memory(session, memory_id)
    if not memory:
        click.echo(f"Memory not found: {memory_id}")
        return

    project_id = None
    if project:
        proj = _get_or_create_project(session, project)
        project_id = proj.id

    validation = MemoryValidation(
        memory_id=memory.id,
        project_id=project_id,
        validated=True,
        evidence=evidence,
    )
    session.add(validation)

    old_maturity = memory.maturity
    new_score = update_confidence(memory, validated=True, project_id=project_id)

    session.commit()

    click.echo(f"Validated: {memory.title}")
    click.echo(f"  Confidence: {new_score:.0%} | Maturity: {old_maturity} -> {memory.maturity}")


# ── Status ──


@cli.command()
def status():
    """Show organizational learning dashboard."""
    from sqlalchemy import func

    from memee.storage.database import get_session, init_db
    from memee.storage.models import Memory, Organization, Project

    engine = init_db()
    session = get_session(engine)

    total = session.query(func.count(Memory.id)).scalar() or 0
    if total == 0:
        click.echo("Memee is empty. Start recording memories with 'memee record'.")
        return

    # Maturity distribution
    maturity_counts = dict(
        session.query(Memory.maturity, func.count(Memory.id))
        .group_by(Memory.maturity)
        .all()
    )

    # Type distribution
    type_counts = dict(
        session.query(Memory.type, func.count(Memory.id))
        .group_by(Memory.type)
        .all()
    )

    avg_confidence = session.query(func.avg(Memory.confidence_score)).scalar() or 0
    project_count = session.query(func.count(Project.id)).scalar() or 0
    org_count = session.query(func.count(Organization.id)).scalar() or 0

    click.echo("=== MEMEE LEARNING DASHBOARD ===")
    click.echo(f"Organizations: {org_count} | Projects: {project_count}")
    click.echo(f"Total memories: {total} | Avg confidence: {avg_confidence:.0%}")
    click.echo()

    click.echo("Maturity:")
    for level in ["canon", "validated", "tested", "hypothesis", "deprecated"]:
        count = maturity_counts.get(level, 0)
        bar = "#" * min(count, 40)
        click.echo(f"  {level:12s} {count:4d} {bar}")

    click.echo()
    click.echo("Types:")
    for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        click.echo(f"  {t:15s} {count:4d}")


# ── Research Commands ──


@cli.group()
def research():
    """Autoresearch experiment management."""
    pass


@research.command("start")
@click.argument("goal")
@click.option("--metric", "-m", required=True, help="Metric name (e.g. accuracy, coverage)")
@click.option("--direction", "-d", type=click.Choice(["higher", "lower"]), default="higher")
@click.option("--verify", "-v", required=True, help="Command to measure metric")
@click.option("--guard", "-g", default="", help="Command that must pass (exit 0)")
@click.option("--scope", "-s", default="", help="File globs (comma-separated)")
@click.option("--project", "-p", default="", help="Project path")
@click.option("--baseline", "-b", default=None, type=float, help="Baseline value (auto-measured if omitted)")
def research_start(goal, metric, direction, verify, guard, scope, project, baseline):
    """Start a new autoresearch experiment."""
    from memee.engine.research import create_experiment
    from memee.storage.database import get_session, init_db
    from memee.storage.models import Project as ProjectModel

    engine = init_db()
    session = get_session(engine)

    project_id = None
    if project:
        abs_path = str(Path(project).resolve())
        proj = session.query(ProjectModel).filter_by(path=abs_path).first()
        if proj:
            project_id = proj.id

    if not project_id:
        proj = session.query(ProjectModel).first()
        project_id = proj.id if proj else None

    if not project_id:
        click.echo("No project found. Register one first with 'memee project register'.")
        return

    scope_list = [s.strip() for s in scope.split(",") if s.strip()] if scope else []

    exp = create_experiment(
        session, project_id, goal, metric, direction, verify,
        guard_command=guard, scope_globs=scope_list, baseline_value=baseline,
    )

    click.echo(f"Experiment started: {exp.id[:8]}")
    click.echo(f"  Goal: {goal}")
    click.echo(f"  Metric: {metric} ({direction})")
    click.echo(f"  Baseline: {exp.baseline_value}")
    click.echo(f"  Verify: {verify}")
    if guard:
        click.echo(f"  Guard: {guard}")


@research.command("log")
@click.argument("experiment_id")
@click.argument("metric_value", type=float)
@click.argument("status", type=click.Choice(["keep", "discard", "crash"]))
@click.option("--desc", "-d", default="", help="Description")
@click.option("--commit", "-c", default="", help="Git commit hash")
def research_log_cmd(experiment_id, metric_value, status, desc, commit):
    """Log an iteration result."""
    from memee.engine.research import log_iteration
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)

    try:
        it = log_iteration(session, experiment_id, metric_value, status, desc, commit)
        exp = it.experiment
        keep_rate = exp.keeps / max(exp.total_iterations, 1)
        click.echo(f"Iteration {it.iteration_number}: [{status.upper()}] "
                    f"value={metric_value} delta={it.delta:+.4f}")
        click.echo(f"  Keep rate: {keep_rate:.0%} ({exp.keeps}/{exp.total_iterations})")
    except ValueError as e:
        click.echo(f"Error: {e}")


@research.command("run")
@click.argument("experiment_id")
@click.option("--desc", "-d", default="", help="Description of this iteration's changes")
@click.option("--commit", "-c", default="", help="Git commit hash")
@click.option("--cwd", default=None, help="Working directory for commands")
def research_run_cmd(experiment_id, desc, commit, cwd):
    """Run one iteration: execute guard → verify → compare → keep/discard."""
    from memee.engine.research import run_iteration
    from memee.storage.database import get_session, init_db
    from memee.storage.models import ResearchExperiment

    engine = init_db()
    session = get_session(engine)

    exp = session.get(ResearchExperiment, experiment_id)
    if not exp:
        # Try partial ID
        exps = session.query(ResearchExperiment).filter(
            ResearchExperiment.id.like(f"{experiment_id}%")
        ).all()
        exp = exps[0] if len(exps) == 1 else None

    if not exp:
        click.echo(f"Experiment not found: {experiment_id}")
        return

    click.echo(f"Running iteration {exp.total_iterations + 1}...")
    it = run_iteration(session, exp, description=desc, commit_hash=commit, cwd=cwd)

    icon = {"keep": "+", "discard": ".", "crash": "X"}[it.status]
    click.echo(f"  [{icon}] {it.status.upper()}: "
               f"value={it.metric_value} delta={it.delta:+.4f}" if it.metric_value else
               f"  [X] CRASH: {it.description}")

    keep_rate = exp.keeps / max(exp.total_iterations, 1)
    click.echo(f"  Best: {exp.best_value} | Keep rate: {keep_rate:.0%}")


@research.command("status")
@click.argument("experiment_id", default="")
def research_status_cmd(experiment_id):
    """Show experiment status (or list all if no ID given)."""
    from memee.engine.research import get_experiment_status, list_experiments
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)

    if not experiment_id:
        experiments = list_experiments(session)
        if not experiments:
            click.echo("No experiments found.")
            return
        click.echo(f"{'ID':>8s} | {'Status':>10s} | {'Goal':<30s} | {'Keep%':>5s} | {'Baseline':>8s} | {'Best':>8s}")
        click.echo(f"{'─'*8} | {'─'*10} | {'─'*30} | {'─'*5} | {'─'*8} | {'─'*8}")
        for e in experiments:
            click.echo(
                f"{e['id'][:8]:>8s} | {e['status']:>10s} | {e['goal'][:30]:<30s} | "
                f"{e['keep_rate']:5.0%} | {e['baseline'] or 0:8.4f} | {e['best'] or 0:8.4f}"
            )
        return

    status = get_experiment_status(session, experiment_id)
    if "error" in status:
        # Try partial ID
        from memee.storage.models import ResearchExperiment
        exps = session.query(ResearchExperiment).filter(
            ResearchExperiment.id.like(f"{experiment_id}%")
        ).all()
        if len(exps) == 1:
            status = get_experiment_status(session, exps[0].id)
        else:
            click.echo(status["error"])
            return

    click.echo(f"Experiment: {status['id'][:8]}")
    click.echo(f"  Goal:       {status['goal']}")
    click.echo(f"  Metric:     {status['metric']} ({status['direction']})")
    click.echo(f"  Status:     {status['status']}")
    click.echo(f"  Baseline:   {status['baseline']}")
    click.echo(f"  Best:       {status['best']}")
    click.echo(f"  Improvement:{status['improvement']:+.4f}")
    click.echo(f"  Iterations: {status['total_iterations']} "
               f"(keep:{status['keeps']} discard:{status['discards']} crash:{status['crashes']})")
    click.echo(f"  Keep rate:  {status['keep_rate']:.0%}")

    if status["trajectory"]:
        click.echo("\n  Trajectory:")
        for t in status["trajectory"]:
            icon = {"keep": "+", "discard": ".", "crash": "X"}.get(t["status"], "?")
            val = f"{t['value']:.4f}" if t["value"] is not None else "N/A"
            delta = f"{t['delta']:+.4f}" if t["delta"] is not None else ""
            bar = "█" * int((t["value"] or 0) * 20) if t["value"] else ""
            click.echo(f"    {t['iteration']:3d} [{icon}] {val} {delta} {bar}")


@research.command("meta")
def research_meta_cmd():
    """Show meta-learning insights across all experiments."""
    from memee.engine.research import get_meta_learning
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)

    meta = get_meta_learning(session)
    if "message" in meta:
        click.echo(meta["message"])
        return

    click.echo("=== AUTORESEARCH META-LEARNING ===")
    click.echo(f"  Total experiments: {meta['total_experiments']}")
    click.echo(f"  Completed: {meta['completed']}")
    click.echo(f"  Total iterations: {meta['total_iterations']}")
    click.echo(f"  Overall keep rate: {meta['overall_keep_rate']:.0%}")
    click.echo(f"  Keeps/Discards/Crashes: {meta['keeps']}/{meta['discards']}/{meta['crashes']}")

    if meta.get("by_metric"):
        click.echo("\n  By Metric:")
        for name, stats in meta["by_metric"].items():
            click.echo(f"    {name}: keep={stats['keep_rate']:.0%} "
                       f"avg_improvement={stats['avg_improvement']:+.4f} "
                       f"({stats['experiments']} experiments)")

    if meta.get("improvement_by_phase"):
        click.echo("\n  Improvement by Phase:")
        for phase, total in meta["improvement_by_phase"].items():
            bar = "█" * int(total * 10)
            click.echo(f"    {phase:15s}: {total:.4f} {bar}")

    if meta.get("insights"):
        click.echo("\n  Insights:")
        for insight in meta["insights"]:
            click.echo(f"    - {insight}")


@research.command("complete")
@click.argument("experiment_id")
@click.option("--status", "-s", type=click.Choice(["completed", "failed", "cancelled"]), default="completed")
def research_complete_cmd(experiment_id, status):
    """Mark an experiment as completed/failed/cancelled."""
    from memee.engine.research import complete_experiment
    from memee.storage.database import get_session, init_db
    from memee.storage.models import ResearchExperiment

    engine = init_db()
    session = get_session(engine)

    exp = session.get(ResearchExperiment, experiment_id)
    if not exp:
        exps = session.query(ResearchExperiment).filter(
            ResearchExperiment.id.like(f"{experiment_id}%")
        ).all()
        exp = exps[0] if len(exps) == 1 else None

    if not exp:
        click.echo(f"Experiment not found: {experiment_id}")
        return

    complete_experiment(session, exp, status)
    click.echo(f"Experiment {exp.id[:8]} marked as {status}.")
    if status == "completed" and exp.keeps > 0:
        click.echo("  Lesson recorded to organizational memory.")


# ── Project Commands ──


@cli.group()
def project():
    """Project management commands."""
    pass


@project.command("register")
@click.argument("path", default=".")
@click.option("--name", "-n", default=None, help="Project name (default: directory name)")
@click.option("--tags", "-t", default="", help="Comma-separated tags")
@click.option("--stack", "-s", default="", help="Comma-separated stack items")
@click.pass_context
def project_register(ctx, path, name, tags, stack):
    """Register a project directory."""
    from memee.storage.database import get_session, init_db
    from memee.storage.models import Organization, Project

    engine = init_db()
    session = get_session(engine)

    abs_path = str(Path(path).resolve())
    proj_name = name or Path(abs_path).name
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    stack_list = [s.strip() for s in stack.split(",") if s.strip()] if stack else []

    org = session.query(Organization).filter_by(name=ctx.obj["org"]).first()
    if not org:
        click.echo(f"Organization '{ctx.obj['org']}' not found. Run 'memee init' first.")
        return

    existing = (
        session.query(Project)
        .filter_by(organization_id=org.id, path=abs_path)
        .first()
    )
    if existing:
        click.echo(f"Project already registered: {proj_name} ({abs_path})")
        return

    proj = Project(
        organization_id=org.id,
        name=proj_name,
        path=abs_path,
        tags=tag_list,
        stack=stack_list,
    )
    session.add(proj)
    session.commit()
    click.echo(f"Registered project: {proj_name} ({abs_path})")


@project.command("list")
@click.pass_context
def project_list(ctx):
    """List registered projects."""
    from memee.storage.database import get_session, init_db
    from memee.storage.models import Organization, Project

    engine = init_db()
    session = get_session(engine)

    org = session.query(Organization).filter_by(name=ctx.obj["org"]).first()
    if not org:
        click.echo("No organization found. Run 'memee init' first.")
        return

    projects = session.query(Project).filter_by(organization_id=org.id).all()
    if not projects:
        click.echo("No projects registered. Use 'memee project register <path>'.")
        return

    for p in projects:
        stack = ", ".join(p.stack) if p.stack else "-"
        click.echo(f"  {p.name:20s} {p.path}")
        click.echo(f"  {'':20s} Stack: {stack}")


@project.command("sync")
@click.argument("path", default=".")
def project_sync(path):
    """Sync CLAUDE.md from project into Memee memories."""
    from memee.sync.claudemd import sync_claudemd

    abs_path = str(Path(path).resolve())
    stats = sync_claudemd(abs_path)
    click.echo(f"Synced from {abs_path}/CLAUDE.md:")
    for key, count in stats.items():
        if count > 0:
            click.echo(f"  {key}: {count}")


# ── Propagate ──


@cli.command()
@click.option("--threshold", "-t", default=0.55, help="Min confidence to propagate")
@click.option("--max", "-m", "max_prop", default=500, help="Max propagations")
def propagate(threshold, max_prop):
    """Auto-propagate validated patterns to matching-stack projects."""
    from memee.engine.propagation import run_propagation_cycle
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)

    stats = run_propagation_cycle(session, threshold, max_propagations=max_prop)
    click.echo("Auto-Propagation complete:")
    click.echo(f"  Checked:    {stats['memories_checked']} memories")
    click.echo(f"  Propagated: {stats['memories_propagated']} memories")
    click.echo(f"  New links:  {stats['total_new_links']}")
    click.echo(f"  Projects:   {stats['projects_reached']} reached")


# ── Dream ──


@cli.command()
def dream():
    """Run Dream Mode: nightly knowledge processing cycle."""
    from memee.engine.dream import run_dream_cycle
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)

    stats = run_dream_cycle(session)
    click.echo("Dream Mode complete:")
    click.echo(f"  Connections:     {stats['connections_created']} new")
    click.echo(f"  Contradictions:  {stats['contradictions_found']}")
    click.echo(f"  Confidence boosts: {stats['confidence_boosts']}")
    click.echo(f"  Promotions:      {stats['promotions_applied']}/{stats['promotions_proposed']}")

    if stats.get("meta_patterns"):
        click.echo("  Meta-patterns:")
        for mp in stats["meta_patterns"]:
            click.echo(f"    - {mp}")

    if stats.get("digest"):
        click.echo("  Digest:")
        for d in stats["digest"][:5]:
            click.echo(f"    - {d}")


# ── Review ──


@cli.command()
@click.argument("diff_source", default="-")
def review(diff_source):
    """Review git diff against institutional memory.

    Pass a diff file path, or use - for stdin (pipe from git diff).
    """
    import sys

    from memee.engine.review import review_diff
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)

    if diff_source == "-":
        diff_text = sys.stdin.read()
    else:
        diff_text = Path(diff_source).read_text()

    result = review_diff(session, diff_text)

    if result["warnings"]:
        click.echo(f"WARNINGS ({len(result['warnings'])}):")
        for w in result["warnings"]:
            sev = w["severity"].upper()
            click.echo(f"  [{sev}] {w['title']}")
            if w.get("alternative"):
                click.echo(f"         Fix: {w['alternative']}")
            click.echo(f"         Matched: {', '.join(w['matched_keywords'])}")

    if result["confirmations"]:
        click.echo(f"\nGOOD PATTERNS ({len(result['confirmations'])}):")
        for c in result["confirmations"]:
            click.echo(f"  [OK] {c['title']} ({c['maturity']})")

    if result["suggestions"]:
        click.echo(f"\nSUGGESTIONS ({len(result['suggestions'])}):")
        for s in result["suggestions"]:
            click.echo(f"  -> {s['title']} (conf: {s['confidence']:.0%})")

    if not result["warnings"] and not result["confirmations"]:
        click.echo("No warnings or pattern matches found.")

    stats = result.get("stats", {})
    click.echo(f"\nScanned {stats.get('lines_scanned', 0)} lines, "
               f"extracted {stats.get('keywords_extracted', 0)} keywords")


# ── Serve (MCP) ──


@cli.command()
@click.option("--project", "-p", default=".", help="Project path")
@click.option("--task", "-t", default="", help="What you're about to do")
@click.option("--budget", "-b", default=500, help="Max tokens for briefing")
@click.option("--full", is_flag=True, help="Full briefing (no token limit)")
def brief(project, task, budget, full):
    """Smart briefing: only relevant knowledge, token-budgeted."""
    from memee.storage.database import get_session, init_db

    session = get_session(init_db())
    abs_path = str(Path(project).resolve())

    if full:
        from memee.engine.briefing import briefing
        result = briefing(session, abs_path, task_description=task)
    else:
        from memee.engine.router import smart_briefing
        result = smart_briefing(session, abs_path, task=task, token_budget=budget)

    click.echo(result)


@cli.command()
@click.option("--project", "-p", default=".", help="Project path")
def inject(project):
    """Inject organizational knowledge into project's CLAUDE.md."""
    from memee.engine.briefing import inject_claudemd

    abs_path = str(Path(project).resolve())
    result = inject_claudemd(abs_path)
    click.echo(f"Knowledge injected into {result['path']}")
    click.echo(f"  Action: {result['action']}")
    click.echo(f"  Section: {result['section_lines']} lines")
    click.echo(f"  Total CLAUDE.md: {result['total_lines']} lines")


@cli.command()
@click.option("--days", "-d", default=7, help="Number of days to look back")
def changelog(days):
    """Show what the organization learned recently."""
    from memee.engine.changelog import format_changelog, generate_changelog
    from memee.storage.database import get_session, init_db

    session = get_session(init_db())
    data = generate_changelog(session, days=days)
    click.echo(format_changelog(data))


@cli.command("benchmark")
@click.option("--scenario", "-s", default=None, help="Run specific scenario only")
@click.option("--seed", default=42, help="Random seed for reproducibility")
def benchmark_cmd(scenario, seed):
    """Run OrgMemEval — organizational memory benchmark."""
    from memee.benchmarks.orgmemeval import format_report, run_orgmemeval

    scenarios = [scenario] if scenario else None
    click.echo("Running OrgMemEval benchmark...")
    results = run_orgmemeval(scenarios=scenarios, seed=seed)
    click.echo(format_report(results))


@cli.command()
def embed():
    """Generate vector embeddings for all memories (requires fastembed)."""
    from memee.engine.search import embed_all_memories
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)

    click.echo("Generating embeddings...")
    count = embed_all_memories(session)
    if count == 0:
        click.echo("No memories to embed (or fastembed not installed).")
        click.echo("Install with: pip install memee[vectors]")
    else:
        click.echo(f"Embedded {count} memories. Hybrid search is now active.")


@cli.command()
def serve():
    """Start Memee as MCP stdio server for Claude Code."""
    from memee.mcp_server import mcp

    mcp.run(transport="stdio")


# ── Demo ──


@cli.command()
@click.option("--weeks", "-w", default=52, help="Weeks to simulate")
def demo(weeks):
    """Generate enterprise-scale demo data for the dashboard."""
    from memee.demo import generate_demo_data

    click.echo("Generating demo data...")
    generate_demo_data(weeks=weeks)
    click.echo("Done! Run 'memee dashboard' to view.")


# ── CMAM (Claude Managed Agents Memory) ──


@cli.group()
def cmam():
    """Sync Memee's canon to a Claude Managed Agents Memory store.

    Memee stays the intelligence layer (confidence, quality, routing, multi-model).
    CMAM is the Claude-native delivery mechanism: a filesystem-like mount at
    /mnt/memory/ that agents see via the memory tool.
    """


def _build_cmam_config(store_id, backend, local_root, api_base):
    """Resolve a CMAMConfig from CLI flags + settings + env."""
    import os as _os
    from memee.adapters.cmam import CMAMConfig

    cfg = CMAMConfig(
        store_id=store_id or settings.cmam_store_id,
        backend=backend or settings.cmam_backend,
        local_root=Path(local_root) if local_root else settings.cmam_local_root,
        api_base=api_base or settings.cmam_api_base,
        api_key=_os.environ.get("ANTHROPIC_API_KEY"),
        redact=settings.cmam_redact,
    )
    return cfg


@cmam.command("sync")
@click.option("--store-id", default=None, help="CMAM store id (default: MEMEE_CMAM_STORE_ID)")
@click.option("--backend", default=None, type=click.Choice(["fs", "api"]))
@click.option("--local-root", default=None, help="FS backend: output directory")
@click.option("--api-base", default=None, help="API backend: base URL override")
@click.option("--dry-run", is_flag=True, help="Show what would sync without writing")
def cmam_sync(store_id, backend, local_root, api_base, dry_run):
    """Push CANON memories + critical anti-patterns to a CMAM store."""
    from memee.adapters.cmam import sync_to_cmam
    from memee.storage.database import get_session, init_db

    cfg = _build_cmam_config(store_id, backend, local_root, api_base)
    init_db()
    session = get_session()

    result = sync_to_cmam(session, cfg, dry_run=dry_run)

    click.echo(f"CMAM store: {cfg.store_id} ({cfg.backend})")
    if cfg.backend == "fs":
        root = cfg.local_root or (Path.home() / ".memee" / "cmam" / cfg.store_id)
        click.echo(f"Root: {root}")
    click.echo(f"Pushed:   {result.pushed}")
    click.echo(f"Updated:  {result.updated}")
    click.echo(f"Rejected: {len(result.rejected)}")
    click.echo(f"Store:    {result.store_count} memories, {result.store_bytes:,} bytes")
    if result.warnings:
        click.echo("\nWarnings:")
        for w in result.warnings:
            click.echo(f"  - {w}")
    if result.rejected:
        click.echo("\nRejected:")
        for r in result.rejected[:10]:
            click.echo(f"  - {r['path']}: {r['reason']}")
    if dry_run:
        click.echo("\n(dry run — no changes written)")


@cmam.command("status")
@click.option("--store-id", default=None)
@click.option("--backend", default=None, type=click.Choice(["fs", "api"]))
@click.option("--local-root", default=None)
@click.option("--api-base", default=None)
def cmam_status(store_id, backend, local_root, api_base):
    """Inspect a CMAM store: size, file count, limit headroom."""
    from memee.adapters.cmam import verify_store

    cfg = _build_cmam_config(store_id, backend, local_root, api_base)
    info = verify_store(cfg)

    click.echo(f"Store:    {info['store_id']} ({info['backend']})")
    click.echo(f"Memories: {info['memories']} ({info['count_pct_of_limit']}% of 2000)")
    click.echo(f"Bytes:    {info['bytes']:,} ({info['bytes_pct_of_limit']}% of 100 MB)")
    if info['paths']:
        click.echo("\nPaths:")
        for p in info['paths'][:20]:
            click.echo(f"  {p}")
        if len(info['paths']) > 20:
            click.echo(f"  ... +{len(info['paths']) - 20} more")


# ── Retrieval feedback ──


@cli.command()
@click.argument("event_id")
@click.argument("memory_id")
@click.option(
    "--position", "-p", default=-1, type=int,
    help="0-based rank the memory had in the results (-1 = unknown)",
)
def feedback(event_id, memory_id, position):
    """Mark which memory was actually used from a prior search.

    EVENT_ID is the ``query_event_id`` printed by ``memee search`` (or
    returned by the MCP ``memory_search`` tool). MEMORY_ID is the memory you
    ended up using. Memee uses this signal to compute hit@1 / hit@3 for the
    dashboard Retrieval Health panel.
    """
    from memee.engine.telemetry import mark_event_accepted
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)
    pos = None if position < 0 else position
    ok = mark_event_accepted(session, event_id, memory_id, position=pos)
    if ok:
        click.echo(
            f"Recorded: event {event_id[:8]}... -> memory {memory_id[:8]}..."
            + (f" (position {pos})" if pos is not None else "")
        )
    else:
        click.echo(f"Event {event_id[:8]}... not found or write failed", err=True)


# ── Dashboard ──


@cli.command()
@click.option("--port", "-p", default=7878, help="Port number")
@click.option("--open/--no-open", default=True, help="Open browser automatically")
def dashboard(port, open):
    """Start the web dashboard."""
    import uvicorn

    click.echo(f"Starting Memee dashboard at http://127.0.0.1:{port}")
    if open:
        import threading
        import webbrowser

        def _open():
            import time
            time.sleep(1)
            webbrowser.open(f"http://127.0.0.1:{port}")

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run("memee.api.app:app", host="127.0.0.1", port=port, log_level="warning")


# ── Helpers ──


def _find_memory(session, memory_id: str):
    """Find memory by full or partial ID."""
    from memee.storage.models import Memory

    memory = session.get(Memory, memory_id)
    if memory:
        return memory

    # Partial ID match
    results = (
        session.query(Memory).filter(Memory.id.like(f"{memory_id}%")).all()
    )
    if len(results) == 1:
        return results[0]
    return None


def _link_memory_to_project(session, memory, project_path: str):
    """Link a memory to a project by path."""
    from memee.storage.models import Project, ProjectMemory

    abs_path = str(Path(project_path).resolve())
    proj = session.query(Project).filter_by(path=abs_path).first()
    if proj:
        pm = ProjectMemory(project_id=proj.id, memory_id=memory.id)
        session.add(pm)


def _get_or_create_project(session, project_path: str):
    """Get project by path, or return None if not registered."""
    from memee.storage.models import Project

    abs_path = str(Path(project_path).resolve())
    return session.query(Project).filter_by(path=abs_path).first()
