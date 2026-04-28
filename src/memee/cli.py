"""Memee CLI — institutional memory for AI agent companies."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from memee.config import settings


def _print_version_and_exit(ctx, param, value):
    """Click ``--version`` callback that prints version + install location +
    multi-install warning when applicable.

    Replaces ``click.version_option`` so we can also surface the path of the
    binary the shell resolves and any *other* memee installs that are
    shadowing or being shadowed by this one — the v2.0.1 patch fix for the
    pipx-vs-Homebrew shadowing bug.
    """
    if not value or ctx.resilient_parsing:
        return

    from memee import __version__
    from memee.doctor import (
        _install_kind_label,
        detect_memee_installs,
    )

    # Resolve where THIS python imported memee from + which binary on PATH
    # is the active one.
    try:
        import memee as _memee_pkg
        installed = os.path.dirname(os.path.abspath(_memee_pkg.__file__))
    except Exception:
        installed = "<unknown>"

    # The binary on PATH may not be the one running RIGHT NOW (e.g. when
    # invoked via ``python -m memee.cli``), so we report it separately and
    # mark which one matches sys.executable.
    installs = detect_memee_installs()

    click.echo(f"memee {__version__}")
    click.echo(f"  installed: {installed}")

    if not installs:
        # Running from source / no shim on PATH. Honest about it.
        click.echo("  binary:    <not on PATH>")
    else:
        active = installs[0]
        kind = _install_kind_label(active["install_kind"])
        click.echo(
            f"  binary:    {active['path']}  ({kind} — active)"
        )
        for alt in installs[1:]:
            alt_ver = alt.get("version") or "?"
            alt_kind = _install_kind_label(alt["install_kind"])
            click.echo(
                f"  alt:       {alt['path']}  v{alt_ver}  "
                f"({alt_kind} — shadowed by the one above)"
            )

        if len(installs) > 1:
            click.echo("")
            click.echo("  Run: memee doctor   for cleanup guidance")

    # Update notice — same passive channel as the hook briefing. Cached for
    # 24h, silent on failure, killable via MEMEE_NO_UPDATE_CHECK=1. Lives
    # below the install info so the version line itself is parseable.
    try:
        from memee.update_check import check, format_notice

        notice = format_notice(check())
        if notice:
            click.echo("")
            click.echo(f"  {notice}")
    except Exception:
        pass

    ctx.exit()


@click.group()
@click.option("--org", default=None, help="Organization name override")
@click.option(
    "--version", is_flag=True, expose_value=False, is_eager=True,
    callback=_print_version_and_exit,
    help="Show version, install location, and any shadowed installs on PATH.",
)
@click.pass_context
def cli(ctx, org):
    """Memee — Your agents forget. Memee doesn't."""
    ctx.ensure_object(dict)
    ctx.obj["org"] = org or settings.org_name


# ── Init ──


@cli.command()
@click.argument("mode", required=False, default=None,
                type=click.Choice(["solo", "join", "team", None]))
@click.option(
    "--no-hooks", is_flag=True,
    help="Wire MCP only — skip the SessionStart/UserPromptSubmit/Stop hooks "
         "that make Memee fully automatic. Default: hooks on.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Show what setup would change without writing any files.",
)
@click.option(
    "--ignore-multi-install", is_flag=True,
    help="Run setup even when multiple memee binaries are on PATH. "
         "Use only if you know which one is active and accept the risk "
         "that hooks will fire whichever the shell resolves first.",
)
def setup(mode, no_hooks, dry_run, ignore_multi_install):
    """Interactive setup wizard with beautiful terminal UI."""
    # Pre-flight: setup writes hooks into ~/.claude/settings.json that
    # invoke ``memee`` on every session. If two binaries are on PATH we
    # have no idea which one will fire — and the most likely one is the
    # *older* shadowing install, which won't have the hook code at all.
    # Refuse cleanly rather than ship a broken wire-up.
    if not ignore_multi_install:
        from memee.doctor import (
            _install_kind_label,
            detect_memee_installs,
        )
        installs = detect_memee_installs()
        if len(installs) > 1:
            click.echo(
                "\033[31m✗\033[0m Setup refused. Multiple `memee` "
                "installations on PATH:"
            )
            for i, inst in enumerate(installs):
                tag = "[active]" if i == 0 else "[shadowed]"
                ver = inst.get("version") or "?"
                kind = _install_kind_label(inst["install_kind"])
                click.echo(f"    {inst['path']}  v{ver}  {kind}  {tag}")
            click.echo("")
            click.echo(
                "  Resolve this first (run \033[36mmemee doctor\033[0m for "
                "specific cleanup commands), then re-run \033[36mmemee "
                "setup\033[0m."
            )
            click.echo(
                "  Or pass \033[2m--ignore-multi-install\033[0m if you "
                "know what you're doing."
            )
            sys.exit(1)

    # The installer reads these via module-level globals so the wizard's
    # rich UI can decide whether to advertise the automatic experience or
    # the MCP-only one.
    from memee import installer as _installer

    _installer.SETUP_FLAGS = {"no_hooks": no_hooks, "dry_run": dry_run}

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
@click.option(
    "--no-hooks", is_flag=True,
    help="Wire MCP only — don't install SessionStart/UserPromptSubmit/Stop hooks.",
)
@click.option(
    "--uninstall-hooks", is_flag=True,
    help="Remove Memee-installed hooks (leaves user's other hooks intact).",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Show what doctor would change without writing any files.",
)
@click.option(
    "--ignore-multi-install", is_flag=True,
    help="Suppress the multi-install warning. Doctor still scans, but no "
         "warning is added to the issues list — for users who genuinely "
         "want two memee binaries side by side.",
)
@click.option(
    "--yes", "-y", "assume_yes", is_flag=True,
    help="Skip the confirmation prompt before destructive auto-fixes "
         "(currently: removing a shadowing memee install). Implied in "
         "non-interactive shells.",
)
def doctor(
    no_fix, no_hooks, uninstall_hooks, dry_run, ignore_multi_install, assume_yes
):
    """Health check: scan system, detect AI tools, fix configuration."""
    from memee.doctor import (
        _can_safely_remove,
        _install_kind_label,
        detect_memee_installs,
        print_doctor_report,
        run_doctor,
    )

    # Pre-flight: when we're about to run a destructive multi-install fix
    # in an interactive TTY, ask once. Non-TTY (CI, piped) skips the prompt
    # — same convention as ``doctor``'s existing MCP-config auto-fix.
    auto_fix = not no_fix
    skip_install_fix = ignore_multi_install
    if auto_fix and not skip_install_fix and not dry_run:
        installs = detect_memee_installs()
        if len(installs) > 1:
            ok, _ = _can_safely_remove(installs[0], installs[1:])
            if ok and sys.stdin.isatty() and not assume_yes:
                active = installs[0]
                target = installs[1]
                click.echo(
                    "\033[33m!\033[0m Two memee installs on PATH. "
                    "Doctor wants to remove the active one:"
                )
                click.echo(
                    f"    \033[1mremove\033[0m: {active['path']}  "
                    f"v{active.get('version') or '?'}  "
                    f"({_install_kind_label(active['install_kind'])})"
                )
                click.echo(
                    f"    \033[1mkeep\033[0m:   {target['path']}  "
                    f"v{target.get('version') or '?'}  "
                    f"({_install_kind_label(target['install_kind'])})"
                )
                if not click.confirm("  Proceed?", default=False):
                    skip_install_fix = True

    results = run_doctor(
        auto_fix=auto_fix,
        install_hooks=not no_hooks and not uninstall_hooks,
        uninstall_hooks=uninstall_hooks,
        dry_run=dry_run,
        skip_install_fix=skip_install_fix,
    )
    if ignore_multi_install:
        results["issues"] = [
            i for i in results.get("issues", [])
            if i.get("type") != "multi_install"
        ]
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
    """Show organizational learning summary."""
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


# ── Research Commands — REMOVED in v2.0.0 ──
# The autoresearch engine (create/log/run/status/meta/complete) was an
# orthogonal Karpathy-style harness, not institutional memory. It bloated
# every agent's MCP tool list and shipped a ~1.4 KLOC schema for nobody.
# Use a dedicated tool (e.g. wandb, optuna) for experiment tracking.


# ── LTR / hard-negative mining commands ──


@cli.group()
def ranker():
    """LTR ranker management (R9 #3 + #4)."""
    pass


@ranker.command("status")
def ranker_status():
    """Show registered LTR models and their status."""
    from memee.storage.database import get_session, init_db
    from memee.storage.models import LTRModel, SearchEvent
    from sqlalchemy import func

    engine = init_db()
    session = get_session(engine)
    rows = session.query(LTRModel).order_by(LTRModel.created_at.desc()).all()
    if not rows:
        click.echo("No LTR models registered.")
    else:
        for r in rows:
            click.echo(
                f"  {r.id[:8]}  {r.version:<20} {r.status:<11} "
                f"nDCG@10={r.eval_ndcg_at_10}  events={r.training_event_count}"
            )
    total_events = session.query(func.count(SearchEvent.id)).scalar() or 0
    accepted = (
        session.query(func.count(SearchEvent.id))
        .filter(SearchEvent.accepted_memory_id.isnot(None))
        .scalar()
        or 0
    )
    click.echo(f"\nSearchEvent total: {total_events}, accepted: {accepted}")
    click.echo("  (≥500 accepted recommended before training)")


@ranker.command("train")
@click.option("--version", "-v", required=True, help="Model version label (e.g. ltr_v1)")
@click.option(
    "--output-dir",
    "-o",
    default=None,
    help="Directory for model file (default: ~/.memee/models)",
)
def ranker_train(version, output_dir):
    """Train an LTR ranker on the SearchEvent + snapshot history."""
    from memee.engine import ltr
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)
    out = Path(output_dir) if output_dir else Path.home() / ".memee" / "models"
    model_id = ltr.train_and_register(session, out, version=version)
    if model_id is None:
        click.echo(
            "Training skipped — install `memee[ltr]` and ensure ≥30 "
            "SearchEvent accepted rows."
        )
        return
    click.echo(f"Trained ranker {version} → id {model_id[:8]} (status: candidate)")
    click.echo("Promote with: memee ranker promote " + model_id[:8])


@ranker.command("promote")
@click.argument("model_id")
def ranker_promote(model_id):
    """Promote a candidate ranker to production."""
    from memee.engine import ltr
    from memee.storage.database import get_session, init_db
    from memee.storage.models import LTRModel

    engine = init_db()
    session = get_session(engine)
    target = session.get(LTRModel, model_id)
    if target is None:
        # try prefix lookup
        rows = (
            session.query(LTRModel)
            .filter(LTRModel.id.like(f"{model_id}%"))
            .all()
        )
        if len(rows) != 1:
            click.echo(f"No unique model matching '{model_id}'")
            return
        target = rows[0]
    if ltr.promote(session, target.id):
        click.echo(f"Promoted {target.version} ({target.id[:8]}) to production.")
    else:
        click.echo("Promotion failed.")


@ranker.command("mine-negatives")
@click.option("--since-days", "-d", default=None, type=int, help="Limit window (days)")
@click.option(
    "--output",
    "-o",
    default=None,
    help="JSONL output path (default: ~/.memee/hard_negatives.jsonl)",
)
def ranker_mine_negatives(since_days, output):
    """Mine (rejected_top, accepted_lower) pairs to JSONL for retraining."""
    from memee.engine.hard_negatives import export_hard_negatives_jsonl
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)
    out = (
        Path(output)
        if output
        else Path.home() / ".memee" / "hard_negatives.jsonl"
    )
    n = export_hard_negatives_jsonl(session, out, since_days=since_days)
    click.echo(f"Exported {n} pairs to {out}")


@ranker.command("rerank-status")
def ranker_rerank_status():
    """Show cross-encoder rerank state (R14): active model, top-K, cache."""
    from memee.engine.reranker import (
        CrossEncoderReranker,
        DEFAULT_RERANK_MODEL,
        DEFAULT_RERANK_TOP_K,
    )

    rr = CrossEncoderReranker()
    state = rr.cache_state()
    if not rr.is_enabled():
        click.echo(
            "Cross-encoder rerank: OFF\n"
            "  Set MEMEE_RERANK_MODEL to enable, e.g.:\n"
            f"    export MEMEE_RERANK_MODEL=ms-marco-MiniLM-L-6-v2\n"
            f"  Default model: {DEFAULT_RERANK_MODEL}\n"
            f"  Default top-K: {DEFAULT_RERANK_TOP_K}\n"
            "  Optional dep: pip install memee[rerank]"
        )
        if state["load_failed"]:
            click.echo("  (last load attempt failed — see logs)")
        return
    click.echo("Cross-encoder rerank: ON")
    click.echo(f"  Model:   {state['model_name']}")
    click.echo(f"  Top-K:   {state['top_k']}")
    if state["loaded"]:
        click.echo(f"  Cache:   loaded ({state['cached_model_name']})")
    else:
        click.echo("  Cache:   not yet loaded (first search will warm it)")
    if state["load_failed"]:
        click.echo("  Status:  load_failed (rerank disabled this run)")


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
@click.option(
    "--format", "fmt",
    type=click.Choice(["default", "compact"]),
    default="default",
    help="Output format. 'compact' strips headers and emits 5-7 short bullet "
         "lines; designed for hook injection where every token counts.",
)
def brief(project, task, budget, full, fmt):
    """Smart briefing: only relevant knowledge, token-budgeted.

    Wraps the smart router (default) or the full briefing engine (``--full``).
    The hook layer calls this on SessionStart and UserPromptSubmit with
    ``--format compact --budget 200..300``; humans usually want the default.
    """
    from memee.storage.database import get_session, init_db

    session = get_session(init_db())
    # Project may not exist locally yet (e.g. fresh git worktree the agent
    # opens before any setup). Path.resolve(strict=False) handles that.
    try:
        abs_path = str(Path(project).resolve(strict=False))
    except OSError:
        abs_path = project

    try:
        if full:
            from memee.engine.briefing import briefing
            result = briefing(
                session, abs_path,
                task_description=task,
                compact=(fmt == "compact"),
            )
        elif fmt == "compact":
            # Compact is the hook format: render via smart_briefing then
            # post-trim to 5-7 short lines (no decoration, no footer noise)
            # and re-check the budget. The router already enforces the
            # budget; we just strip and tighten.
            from memee.engine.router import _count_tokens, smart_briefing
            raw = smart_briefing(
                session, abs_path, task=task, token_budget=budget
            )
            result = _to_compact(raw, budget=budget, count_tokens=_count_tokens)
        else:
            from memee.engine.router import smart_briefing
            result = smart_briefing(
                session, abs_path, task=task, token_budget=budget
            )
    except Exception as e:
        # A briefing failure must never break the agent's session. Hooks run
        # this command on every prompt; if a corrupt DB or a search error
        # bubbled out, every keystroke would error out. Log to stderr and
        # exit 0 so the harness moves on.
        click.echo(f"memee brief: {e}", err=True)
        return

    # Receipt chain: build the prepend stack so Memee tells the user what
    # it did, in their own conversation, where they already are. Order
    # matters — most "this week" first (reads like context), then "last
    # session" (yesterday's evidence), then update notice (housekeeping),
    # then the briefing body itself. Each piece is independently silent
    # when it has nothing to say. All exceptions are swallowed: a broken
    # receipt must never break a briefing.
    prepends: list[str] = []
    try:
        from memee.digest import format_digest_notice

        digest = format_digest_notice()
        if digest:
            prepends.append(digest)
    except Exception:
        pass

    try:
        from memee.session_ledger import format_session_summary

        summary = format_session_summary()
        if summary:
            prepends.append(summary)
    except Exception:
        pass

    try:
        from memee.update_check import check, format_notice

        notice = format_notice(check(), prefix="> ")
        if notice:
            prepends.append(notice)
    except Exception:
        pass

    if prepends:
        prefix = "\n\n".join(prepends)
        result = f"{prefix}\n\n{result}" if result else prefix

    click.echo(result)


def _to_compact(raw: str, budget: int, count_tokens) -> str:
    """Trim a smart-briefing into a compact 5-7 line bullet list.

    Drops the verbose footer (the ``[N memories — memee search ...]`` and
    the ``[~X tokens / Y budget]`` lines) and any blank separators so the
    result fits comfortably inside the agent's context. Bullet markers
    from the router (``⚠``, ``✓``, ``[SEV]``) survive — they're already
    short and signal severity at a glance.

    Citation footer (``---\\nCite Memee canon …``) is appended after
    trimming so the agent always sees the cite contract; the footer is
    capped to ≤200 tokens by spec, well under any realistic budget.

    If the trimmed output still exceeds the budget, lines are dropped from
    the tail until it fits or only one line remains. The citation footer
    is preserved at the cost of bullets — it's the load-bearing
    instruction; bullets without a cite contract are decoration.
    """
    if not raw:
        return ""
    from memee.engine.citations import get_citation_footer

    bullets: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip footer lines and section headers — bullets only.
        if stripped.startswith("[") and stripped.endswith("]"):
            continue
        if stripped.endswith(":") and not stripped.startswith(("⚠", "✓", "[")):
            continue
        bullets.append(stripped)
        if len(bullets) >= 7:
            break

    footer = get_citation_footer()
    footer_tokens = count_tokens(footer)
    # The hook layer ships at budget=200..300 where the footer is well
    # under the ceiling. If the caller passed a budget so small the
    # footer alone would blow it, drop the footer rather than the bullets
    # (callers running at budget≪footer aren't shipping to a session
    # hook — they're tests or ad-hoc trims).
    include_footer = budget >= footer_tokens
    if include_footer:
        while bullets and count_tokens("\n".join(bullets)) + footer_tokens > budget:
            if len(bullets) == 1:
                break
            bullets.pop()
    else:
        while len(bullets) > 1 and count_tokens("\n".join(bullets)) > budget:
            bullets.pop()

    pieces = bullets[:]
    if include_footer:
        pieces.append(footer)
    return "\n".join(pieces)


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
@click.option(
    "--auto", is_flag=True,
    help="Run in hook mode: infer project + diff from CWD, exit 0 on no-op, "
         "write nothing to stdout unless something was learned.",
)
@click.option(
    "--project", "-p", default="",
    help="Project path (default: current directory)",
)
@click.option(
    "--diff", "diff_text", default="",
    help="git diff text to review. Default: read from `git diff` in --project.",
)
@click.option(
    "--outcome", default="success",
    type=click.Choice(["success", "failure"]),
    help="Outcome of the task being reviewed.",
)
@click.option("--agent", default="", help="Agent / developer name")
@click.option("--model", default="", help="AI model used (claude-opus-4, gpt-4o, ...)")
@click.option(
    "--json", "as_json", is_flag=True,
    help="Print the full structured review payload as JSON instead of the "
         "one-line English sentence. Useful for scripts and for debugging "
         "the Stop hook receipt.",
)
def learn(auto, project, diff_text, outcome, agent, model, as_json):
    """Post-task review: scan the latest diff, validate patterns, log impact.

    The Stop hook fires this with ``--auto`` so the loop closes without the
    agent having to remember. In auto mode:

      * The current directory is treated as the project.
      * ``git diff`` (working tree) is captured and fed to the review engine.
      * On a no-op (empty diff, no recent activity), exit 0 silently.
      * On a successful review, print exactly one structured line.
      * Any error goes to stderr and we still exit 0 — a hook MUST NOT
        fail the agent's session.

    Manual mode (without ``--auto``) is for humans / scripts that want to
    explicitly drive the review against a chosen diff.
    """
    import os as _os
    import subprocess as _sub

    try:
        # Resolve the project path. In --auto mode the hook is fired from
        # whatever CWD Claude Code launched in; that's almost always the
        # project root.
        if not project:
            project = _os.environ.get("CLAUDE_PROJECT_DIR") or _os.getcwd()
        try:
            abs_path = str(Path(project).resolve(strict=False))
        except OSError:
            abs_path = project

        # Snapshot the session's citations BEFORE anything else in the
        # auto path. The Stop hook fires this command on every Stop —
        # including chats with no diff — so this is the only reliable
        # session-end boundary we have to advance ``last_ended_at``.
        # The next SessionStart briefing reads the snapshot and prepends
        # a one-line "Last session: applied N memories" receipt.
        # Best-effort: every error is swallowed inside record_session_end;
        # we wrap again here so a failure to even open the DB doesn't
        # break the hook.
        if auto:
            try:
                from memee.session_ledger import record_session_end
                from memee.storage.database import (
                    get_session as _get_session,
                    init_db as _init_db,
                )

                _ledger_session = _get_session(_init_db())
                try:
                    record_session_end(_ledger_session)
                finally:
                    _ledger_session.close()
            except Exception:
                # Hook safety: never break the session.
                pass

        # Source the diff. Caller-provided wins; otherwise, in a git repo,
        # take the working-tree diff. If the directory isn't a git repo the
        # subprocess returns non-zero — treat as no-op in --auto mode.
        if not diff_text:
            try:
                proc = _sub.run(
                    ["git", "diff", "--no-color", "--unified=0"],
                    cwd=abs_path,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                if proc.returncode == 0:
                    diff_text = proc.stdout
            except (FileNotFoundError, _sub.TimeoutExpired, OSError):
                # No git, or hung — silent no-op in auto, surface in manual.
                if not auto:
                    click.echo("memee learn: git not available or timed out", err=True)

        if not diff_text.strip():
            if auto:
                # Silent no-op: hook fires on every Stop, including chats
                # with no code changes. Nothing to learn.
                return
            click.echo("memee learn: no diff to review", err=True)
            return

        from memee.engine.feedback import post_task_review
        from memee.storage.database import get_session, init_db

        session = get_session(init_db())
        result = post_task_review(
            session,
            diff_text=diff_text,
            project_path=abs_path,
            agent=agent,
            model=model,
            outcome=outcome,
        )
    except Exception as e:
        # Hook safety: never break the session. Errors go to stderr,
        # exit code stays 0.
        click.echo(f"memee learn: {e}", err=True)
        return

    patterns_followed = result.get("patterns_followed", 0)
    warnings_violated = result.get("warnings_violated", 0)
    new_patterns = result.get("new_patterns", 0)

    # ``--json`` short-circuits all rendering. Useful for scripts and for
    # debugging the Stop hook from a terminal.
    if as_json:
        import json as _json

        click.echo(_json.dumps(result, default=str, sort_keys=True))
        return

    if auto:
        sentence = _format_stop_receipt(result)
        # Silent unless we built a sentence. A noisy hook gets disabled.
        if not sentence:
            return
        click.echo(sentence)
    else:
        click.echo(
            f"Patterns followed: {patterns_followed}\n"
            f"Warnings violated: {warnings_violated}\n"
            f"New patterns: {new_patterns}\n"
            f"Outcome: {result.get('outcome', outcome)}"
        )


# ── Stop hook receipt ────────────────────────────────────────────────────
#
# Render a single ≤120-char English sentence describing the most-significant
# thing that happened in this Stop. Returns ``""`` when nothing notable
# happened — the caller stays silent in that case.


def _format_stop_receipt(result: dict) -> str:
    """Build the one-line Stop receipt from a ``post_task_review`` result.

    Significance order, highest first:

      1. MISTAKE_MADE         (warning ignored AND task failed)
      2. WARNING_INEFFECTIVE  (warning ignored, task succeeded — got lucky)
      3. KNOWLEDGE_REUSED     (agent applied a known canon)
      4. (future) NEW_PATTERN (Memee learned something)

    Format:
      ``Memee: warning ignored — "<title>" was hit (<kind>). [mem:xxxxxxxx]``
      ``Memee: reused "<title>" (canon). [mem:xxxxxxxx]``
      ``Memee: learned "<title>" as hypothesis. [mem:xxxxxxxx]``

    The title is truncated to 60 chars + ``…`` if longer. The whole
    sentence is capped at 120 chars; if a freak combination would push it
    over we trim the title harder until it fits.
    """
    kind = result.get("most_significant_kind")
    title = result.get("most_significant_memory_title") or ""
    mem_id = result.get("most_significant_memory_id") or ""

    if not kind or not mem_id:
        # Nothing notable — silent no-op. Even if the structured counts
        # are non-zero, without a concrete memory row we can't honestly
        # name what happened, so we stay quiet rather than fabricate.
        return ""

    short_id = mem_id[:8]
    truncated_title = _truncate_title(title, 60)

    # Match against ImpactType *string values*. We import here so the CLI
    # doesn't pay the engine import cost on commands that don't need it.
    from memee.engine.impact import ImpactType

    if kind == ImpactType.MISTAKE_MADE.value:
        sentence = (
            f'Memee: warning ignored — "{truncated_title}" was hit '
            f"(mistake_made). [mem:{short_id}]"
        )
    elif kind == ImpactType.WARNING_INEFFECTIVE.value:
        sentence = (
            f'Memee: warning ignored — "{truncated_title}" was hit '
            f"(warning_ineffective). [mem:{short_id}]"
        )
    elif kind == ImpactType.KNOWLEDGE_REUSED.value:
        sentence = f'Memee: reused "{truncated_title}" (canon). [mem:{short_id}]'
    else:
        # Forward-compat: any other kind (incl. future NEW_PATTERN) reads
        # as a learning event. Keeps the renderer total — never raises,
        # never returns ``None`` for a known result shape.
        sentence = (
            f'Memee: learned "{truncated_title}" as hypothesis. '
            f"[mem:{short_id}]"
        )

    # Hard cap — the receipt is read in a single terminal line. If a long
    # title pushed us over 120 chars, shrink the title further until we fit.
    while len(sentence) > 120 and len(truncated_title) > 8:
        truncated_title = _truncate_title(truncated_title, len(truncated_title) - 8)
        sentence = sentence.replace(
            sentence.split('"', 2)[1], truncated_title
        ) if '"' in sentence else sentence
        # Defensive: rebuild from scratch in case the in-place replace
        # didn't shorten it (e.g. title contained quotes).
        if len(sentence) > 120:
            if kind == ImpactType.MISTAKE_MADE.value:
                sentence = (
                    f'Memee: warning ignored — "{truncated_title}" '
                    f"was hit (mistake_made). [mem:{short_id}]"
                )
            elif kind == ImpactType.WARNING_INEFFECTIVE.value:
                sentence = (
                    f'Memee: warning ignored — "{truncated_title}" '
                    f"was hit (warning_ineffective). [mem:{short_id}]"
                )
            elif kind == ImpactType.KNOWLEDGE_REUSED.value:
                sentence = (
                    f'Memee: reused "{truncated_title}" (canon). '
                    f"[mem:{short_id}]"
                )
            else:
                sentence = (
                    f'Memee: learned "{truncated_title}" as hypothesis. '
                    f"[mem:{short_id}]"
                )

    return sentence


def _truncate_title(title: str, max_chars: int) -> str:
    """Truncate a memory title to ``max_chars`` characters.

    Adds an ellipsis (``…``) when truncated. The ellipsis is one char so
    the visible result is exactly ``max_chars`` characters long. Empty /
    short titles pass through unchanged.
    """
    if len(title) <= max_chars:
        return title
    if max_chars <= 1:
        return "…"
    return title[: max_chars - 1] + "…"


@cli.command()
def serve():
    """Start Memee as MCP stdio server for Claude Code."""
    from memee.mcp_server import mcp

    # MCP stdio means stdout is the wire protocol — never log there. Stderr
    # is harness-visible in most clients (Claude Code surfaces it in
    # `/mcp servers`). Best-effort, swallow all errors.
    try:
        from memee.update_check import check, format_notice

        notice = format_notice(check())
        if notice:
            sys.stderr.write(f"[memee] {notice}\n")
    except Exception:
        pass

    mcp.run(transport="stdio")


# ── Demo ──


@cli.command()
@click.option("--weeks", "-w", default=52, help="Weeks to simulate")
def demo(weeks):
    """Generate enterprise-scale demo data."""
    from memee.demo import generate_demo_data

    click.echo("Generating demo data...")
    generate_demo_data(weeks=weeks)
    click.echo("Done! Run 'memee status' for a summary or 'memee benchmark' to score.")


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


# ── Pack format (.memee) ──


@cli.group()
def pack():
    """Build, install, verify ``.memee`` knowledge packs.

    A ``.memee`` pack is a portable, optionally-signed bundle of validated
    memories. See ``docs/pack-format.md`` for the full spec.
    """


def _resolve_signing_key(key_arg: str | None) -> bytes | None:
    """Locate a private signing key.

    Order: explicit ``--key`` arg → ``MEMEE_PACK_KEY`` env var → none.
    Returns the PEM bytes or ``None`` if no key is configured.
    """
    import os as _os

    candidate = key_arg or _os.environ.get("MEMEE_PACK_KEY")
    if not candidate:
        return None
    p = Path(candidate)
    if not p.exists():
        raise click.ClickException(f"signing key not found: {p}")
    return p.read_bytes()


@pack.command("export")
@click.option("--name", default=None, help="Pack name (default: parent dir basename)")
@click.option("--pack-version", "version", default="0.1.0", help="Pack version (semver)")
@click.option("--title", default=None, help="Pack title (default: derived from name)")
@click.option("--description", default="", help="Pack description")
@click.option("--author", default="", help="Pack author")
@click.option("--license", "license_", default="MIT", help="SPDX licence id")
@click.option("--confidence-cap", default=0.6, type=float,
              help="Cap imported confidences at this value")
@click.option("--stack", default="", help="Comma-separated stack tags")
@click.option("--canon-only", is_flag=True,
              help="Only export memories at maturity=canon (else canon+validated)")
@click.option("--out", "out_path", default=None,
              help="Output file (default: <name>.memee in cwd; '-' for stdout)")
@click.option("--key", "key_path", default=None,
              help="Path to ed25519 private key (PEM). Falls back to MEMEE_PACK_KEY.")
def pack_export(
    name, version, title, description, author, license_,
    confidence_cap, stack, canon_only, out_path, key_path,
):
    """Export validated/canon memories as a ``.memee`` pack."""
    from memee.engine.packs import export_pack, export_pack_to_stream
    from memee.storage.database import get_session, init_db

    if not name:
        name = Path.cwd().name
    if not title:
        title = f"{name} canon"
    stack_list = [s.strip() for s in stack.split(",") if s.strip()] if stack else []

    private_key = _resolve_signing_key(key_path)

    engine = init_db()
    session = get_session(engine)

    if out_path == "-":
        # Streaming to stdout — emit no human-readable line, only the bytes.
        result = export_pack_to_stream(
            session,
            name=name, version=version, title=title,
            stream=sys.stdout.buffer,
            description=description,
            confidence_cap=confidence_cap,
            stack=stack_list,
            canon_only=canon_only,
            private_key_pem=private_key,
        )
        click.echo(
            f"pack exported: <stdout> ({result.memories} memories, "
            f"signed={'Y' if result.signed else 'N'})",
            err=True,
        )
        return

    result = export_pack(
        session,
        name=name, version=version, title=title,
        out=out_path,
        description=description,
        author=author,
        license=license_,
        confidence_cap=confidence_cap,
        stack=stack_list,
        canon_only=canon_only,
        private_key_pem=private_key,
    )
    size_kb = max(1, result.size_bytes // 1024)
    click.echo(
        f"pack exported: {result.out_path} ({result.memories} memories, "
        f"signed={'Y' if result.signed else 'N'}, size={size_kb} KB)"
    )


@pack.command("install")
@click.argument("source", required=False)
@click.option("--from-url", "from_url", default=None,
              help="HTTPS URL of a .memee pack to download and install")
@click.option("--unsigned", is_flag=True,
              help="Allow installing an unsigned or invalidly-signed pack")
@click.option("--upgrade", is_flag=True,
              help="If a different version of this pack is installed, install alongside")
def pack_install(source, from_url, unsigned, upgrade):
    """Install a ``.memee`` pack into the local DB.

    ``SOURCE`` accepts either a local file path or a bundled seed-pack name
    (``agent-discipline``, ``python-web``, …). Names are resolved against
    the seed packs shipped inside the wheel.
    """
    from memee.engine.packs import install_pack, list_seed_packs, resolve_seed_pack
    from memee.storage.database import get_session, init_db

    if not source and not from_url:
        raise click.UsageError("provide a FILE argument or --from-url URL")
    if source and from_url:
        raise click.UsageError("FILE and --from-url are mutually exclusive")

    target = from_url or source
    pack_filename = Path(source).name if source else None

    # Bare-name resolution: when SOURCE is neither an existing file path
    # nor a URL, treat it as a seed-pack name and look it up in the bundle.
    # Falls back to the original error path when the name is unknown so the
    # user still sees a helpful message instead of "file not found".
    if source and not from_url:
        looks_like_path = "/" in source or source.endswith(".memee")
        is_existing_file = Path(source).is_file()
        if not looks_like_path and not is_existing_file:
            seed_path = resolve_seed_pack(source)
            if seed_path is not None:
                target = str(seed_path)
                pack_filename = seed_path.name
            else:
                names = list_seed_packs()
                hint = (
                    f"available seed packs: {', '.join(names)}"
                    if names else
                    "no seed packs are bundled in this install"
                )
                raise click.ClickException(
                    f"unknown seed pack '{source}'. {hint}. "
                    f"To install from a file, pass a path; to install from a URL, "
                    f"use --from-url."
                )

    engine = init_db()
    session = get_session(engine)

    try:
        result = install_pack(
            session,
            target,
            allow_unsigned=unsigned,
            overwrite_version=upgrade,
            pack_filename=pack_filename,
        )
    except ValueError as e:
        raise click.ClickException(str(e))
    except FileNotFoundError as e:
        raise click.ClickException(str(e))

    if not result.signed and not unsigned:
        # Reachable when allow_unsigned was True due to nothing being signed
        # — never warn unless the pack actually claimed a sig.
        pass

    if result.no_op:
        click.echo(
            f"pack already installed: {result.name} v{result.version} (no-op)"
        )
        return

    if not result.signed:
        click.secho(
            f"WARNING: pack {result.name} v{result.version} is unsigned. "
            f"Trust depends on where you got it.",
            fg="yellow",
        )

    click.echo(
        f"pack installed: {result.name} v{result.version} "
        f"(imported={result.imported}, merged={result.merged}, "
        f"skipped={result.skipped}, signed={'Y' if result.signed else 'N'})"
    )
    if result.rejected:
        click.echo(f"  {result.rejected} rows rejected by quality gate")


@pack.command("list")
def pack_list():
    """List installed packs from ``~/.memee/packs.json``."""
    from memee.engine.packs import list_installed

    rows = list_installed()
    if not rows:
        click.echo("No packs installed.")
        return

    click.echo(f"{'NAME':<24s} {'VERSION':<10s} {'INSTALLED':<26s} {'SIGNED':<7s} COUNT")
    click.echo("-" * 80)
    for r in rows:
        installed = (r.get("installed_at") or "")[:19].replace("T", " ")
        signed = "Y" if r.get("signed") else "N"
        count = int(r.get("imported", 0)) + int(r.get("merged", 0))
        click.echo(
            f"{(r.get('name') or '')[:24]:<24s} "
            f"{(r.get('version') or '')[:10]:<10s} "
            f"{installed:<26s} {signed:<7s} {count}"
        )


@pack.command("verify")
@click.argument("source")
def pack_verify(source):
    """Verify a ``.memee`` pack's signature without installing.

    Exit 0 on a valid signature OR an unsigned pack with no signature claim.
    Exit 1 on tamper, signature mismatch, or unverifiable claim.
    """
    from memee.engine.packs import verify_file

    try:
        result = verify_file(source)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e))

    click.echo(f"pack: {result.name} v{result.version}")
    click.echo(f"  memories: {result.memories}")
    if result.counts:
        click.echo(f"  manifest counts: {dict(result.counts)}")
    click.echo(f"  signed: {'Y' if result.signed else 'N'}")
    click.echo(f"  signature: {result.reason}")

    if not result.valid:
        sys.exit(1)


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
    ended up using. Memee uses this signal to compute hit@1 / hit@3
    retrieval health metrics (surfaced via ``/api/v1/retrieval``).
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


# ── Calibration (R12 P1) ──


@cli.group()
def calibration():
    """Confidence calibration tools."""
    pass


@calibration.command("eval")
def calibration_eval():
    """Run the synthetic calibration harness and print Brier / ECE / MCE."""
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "tests.calibration_eval"],
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    sys.exit(result.returncode)


@calibration.command("status")
def calibration_status():
    """Show whether calibration is enabled and which curves are loaded."""
    from memee.engine.calibration import is_enabled, load_curves
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)
    enabled = is_enabled()
    registry = load_curves(session)
    click.echo(f"MEMEE_CALIBRATED_CONFIDENCE: {'on' if enabled else 'off'}")
    if registry is None:
        click.echo("No calibration curves loaded.")
        click.echo("Run `memee calibration fit` to fit + persist.")
        return
    click.echo(f"Global curve: {registry.global_curve.n_train} training points")
    click.echo(f"Per-slice curves: {len(registry.by_slice)}")
    for key, curve in sorted(registry.by_slice.items()):
        click.echo(f"  {key}: {curve.n_train} points, {len(curve.xs)} breakpoints")


@calibration.command("fit")
def calibration_fit():
    """Fit isotonic curves from MemoryValidation history and persist."""
    from memee.engine.calibration import fit_curves, save_curves
    from memee.storage.database import get_session, init_db
    from memee.storage.models import Memory, MemoryValidation

    engine = init_db()
    session = get_session(engine)

    # Build training records: every MemoryValidation row becomes a sample
    # whose ``prediction`` is the memory's confidence at the time of the
    # validation (we approximate with current confidence — full fidelity
    # would require historical snapshots which we don't yet log).
    rows = (
        session.query(MemoryValidation, Memory)
        .join(Memory, Memory.id == MemoryValidation.memory_id)
        .all()
    )
    if not rows:
        click.echo("No MemoryValidation rows yet — record some validations first.")
        return
    records = []
    for v, m in rows:
        records.append(
            {
                "prediction": float(m.confidence_score or 0.0),
                "outcome": 1 if v.validated else 0,
                "memory_type": m.type,
                "scope": m.scope,
                "source_type": m.source_type,
            }
        )
    registry = fit_curves(records)
    save_curves(session, registry)
    click.echo(
        f"Fit + saved: global={registry.global_curve.n_train} points, "
        f"slices={len(registry.by_slice)}"
    )


# ── Dashboard — REMOVED in v2.0.0 ──
# The web dashboard at port 7878 was deleted. Memee's pitch ends with
# "no dashboards, no copilots, no magic." The CLI is the human surface;
# agents talk to Memee via MCP. The JSON ``memee.api`` app still exists
# (under the optional ``[api]`` extra) for integrations.


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



@cli.command("why")
@click.argument("snippet", required=False, default="")
@click.option(
    "--file", "-f", "file_path", default="",
    help="Read snippet from a file instead of the positional argument.",
)
@click.option(
    "--stdin", "use_stdin", is_flag=True,
    help="Read snippet from stdin (e.g. `git diff | memee why --stdin`).",
)
@click.option(
    "--limit", "-n", default=3, type=int,
    help="Top N canon entries to surface (default: 3).",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format. JSON is for tooling; text is the screenshotable view.",
)
def why_cmd(snippet, file_path, use_stdin, limit, fmt):
    """Explain a snippet against canon: which lesson would have prevented it.

    Pass code or a free-form question as the positional argument, or
    pipe a diff via ``--stdin``, or read from a file via ``--file``.
    Returns the canon entries (anti-patterns + lessons) that match —
    formatted as one screenshotable block per hit.
    """
    import json as _json
    import sys as _sys

    from memee.engine.citations import cite_token, explain
    from memee.storage.database import get_session, init_db

    # Resolve input: CLI arg > --file > --stdin (in that priority).
    text_in = snippet or ""
    if not text_in and file_path:
        try:
            text_in = Path(file_path).read_text(encoding="utf-8")
        except OSError as e:
            click.echo(f"memee why: cannot read {file_path}: {e}", err=True)
            return
    if not text_in and use_stdin:
        text_in = _sys.stdin.read()

    if not text_in.strip():
        click.echo(
            "memee why: pass a snippet, --file <path>, or --stdin", err=True
        )
        return

    engine = init_db()
    session = get_session(engine)

    hits = explain(session, text_in, limit=limit)

    if fmt == "json":
        payload = []
        for h in hits:
            m = h["memory"]
            ap = m.anti_pattern
            payload.append({
                "id": m.id,
                "cite": cite_token(m.id),
                "title": m.title,
                "type": m.type,
                "maturity": m.maturity,
                "confidence": m.confidence_score,
                "severity": ap.severity if ap else None,
                "trigger": ap.trigger if ap else None,
                "consequence": ap.consequence if ap else None,
                "alternative": ap.alternative if ap else None,
                "score": h.get("score", 0.0),
            })
        click.echo(_json.dumps({"hits": payload}, indent=2))
        return

    if not hits:
        click.echo(
            "no canon hit. either you're safe, or this is a new lesson — "
            "record it with: memee record …"
        )
        return

    for i, h in enumerate(hits):
        m = h["memory"]
        ap = m.anti_pattern
        cite = cite_token(m.id)
        conf = f"conf={m.confidence_score:.2f}"
        click.echo(
            f"{cite}   {m.title}          ({m.maturity}, {conf})"
        )
        if ap:
            if ap.trigger:
                click.echo(f"                 Trigger: {ap.trigger}")
            if ap.consequence:
                click.echo(f"                 Consequence: {ap.consequence}")
            if ap.alternative:
                click.echo(f"                 Alternative: {ap.alternative}")
            click.echo(f"                 Severity: {ap.severity}")
        else:
            # Lesson rendering — show the body directly.
            body = (m.content or "").strip()
            if body and body != m.title:
                # Take the first ~3 short lines so the screenshot fits.
                for line in body.splitlines()[:3]:
                    click.echo(f"                 {line}")
        if i < len(hits) - 1:
            click.echo("")


@cli.command("cite")
@click.argument("hash_or_id")
@click.option(
    "--confirm", is_flag=True,
    help="Mark the citation as applied: bump application_count and append "
         "an evidence_chain entry of kind 'citation'.",
)
@click.option(
    "--note", default="",
    help="Optional note attached to the citation evidence entry.",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
)
def cite_cmd(hash_or_id, confirm, note, fmt):
    """Resolve a `[mem:abc12345]` citation to its full lineage.

    Accepts an 8-char short hash, a dashed UUID prefix, or the full
    UUID. Use ``--confirm`` to record that the agent actually applied
    this memory (a soft validation, see docs).
    """
    import json as _json

    from memee.engine.citations import (
        cite_token,
        confirm_citation,
        lineage,
        resolve,
    )
    from memee.storage.database import get_session, init_db

    engine = init_db()
    session = get_session(engine)

    memory = resolve(session, hash_or_id)
    if memory is None:
        click.echo(
            f"memee cite: no unique memory matches '{hash_or_id}' "
            "(unknown or ambiguous prefix)",
            err=True,
        )
        sys.exit(1)
        return

    confirm_result = None
    if confirm:
        confirm_result = confirm_citation(session, memory, note=note)
        # Re-fetch the lineage AFTER the confirm so the new entry shows up.

    lin = lineage(session, memory)

    if fmt == "json":
        ap = memory.anti_pattern
        payload = {
            "id": memory.id,
            "cite": cite_token(memory.id),
            "title": memory.title,
            "type": memory.type,
            "maturity": memory.maturity,
            "confidence": memory.confidence_score,
            "severity": ap.severity if ap else None,
            "source_url": memory.source_url,
            "lineage": lin,
        }
        if confirm_result is not None:
            payload["confirmed"] = confirm_result
        click.echo(_json.dumps(payload, indent=2))
        return

    cite = cite_token(memory.id)
    click.echo(f"{cite}   {memory.title}")
    parts = [f"Type: {memory.type}"]
    ap = memory.anti_pattern
    if ap:
        parts.append(f"severity={ap.severity}")
    parts.append(f"maturity={memory.maturity}")
    parts.append(f"conf={memory.confidence_score:.2f}")
    click.echo(", ".join(parts))
    click.echo("")
    if lin:
        click.echo("Lineage:")
        for entry in lin:
            ts = entry.get("ts", "")[:10]  # YYYY-MM-DD
            kind = entry.get("kind", "?")
            note_txt = entry.get("note", "")
            click.echo(f"  {ts}  {note_txt}" if note_txt else f"  {ts}  {kind}")
        click.echo("")
    if memory.source_url:
        click.echo("Source:")
        click.echo(f"  {memory.source_url}")
    if confirm_result is not None:
        click.echo("")
        click.echo(
            f"Confirmed citation — application_count is now "
            f"{confirm_result['application_count']}."
        )



def main() -> None:
    """Console-script entry point with clean top-level error handling.

    Wraps ``cli()`` so an uncaught engine exception surfaces as
    ``memee: <error>`` + exit 1 instead of a raw Python traceback.
    Set ``MEMEE_DEBUG=1`` to re-raise the original exception for debugging.

    Click's own errors (``ClickException``, ``Abort``, ``SystemExit``) are
    passed through unchanged so Click's usage/help/exit-code logic still
    works, and tests using ``CliRunner`` (which invokes ``cli`` directly)
    are unaffected.
    """
    import os
    import sys

    try:
        cli()  # Click handles its own exit via SystemExit
    except click.ClickException:
        raise
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        click.echo(f"memee: {e}", err=True)
        if os.environ.get("MEMEE_DEBUG"):
            raise
        sys.exit(1)
