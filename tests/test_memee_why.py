"""Tests for `memee why "<snippet>"` — the screenshotable demo surface.

Seeds 2-3 anti-patterns + a lesson, runs `memee why "eval(user_input)"`,
asserts the relevant memory ID appears in the output and the
`no canon hit` fallback fires on an empty store.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from memee.cli import cli
from memee.engine.citations import explain, short_hash
from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryType,
)


def _patch_db(tmp_path, monkeypatch):
    db_path = tmp_path / "why.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    return db_path


def _seed_canon(session, org_id):
    """Drop in 3 anti-patterns + 1 lesson covering classic Python footguns."""
    eval_ap = Memory(
        organization_id=org_id,
        type=MemoryType.ANTI_PATTERN.value,
        title="never use eval() on user input",
        content=(
            "Trigger: needing to evaluate a math expression provided by the user.\n"
            "Consequence: arbitrary code execution.\n"
            "Alternative: ast.literal_eval / json.loads."
        ),
        tags=["python", "security", "eval"],
        confidence_score=0.94,
        maturity=MaturityLevel.CANON.value,
    )
    session.add(eval_ap)
    session.flush()
    session.add(
        AntiPattern(
            memory_id=eval_ap.id,
            severity="critical",
            trigger="needing to evaluate a math expression provided by the user",
            consequence="arbitrary code execution",
            alternative="ast.literal_eval / json.loads",
        )
    )

    pickle_ap = Memory(
        organization_id=org_id,
        type=MemoryType.ANTI_PATTERN.value,
        title="never pickle.loads untrusted bytes",
        content="Trigger: deserialising network input with pickle.\n"
        "Consequence: RCE.",
        tags=["python", "security", "pickle"],
        confidence_score=0.88,
        maturity=MaturityLevel.CANON.value,
    )
    session.add(pickle_ap)
    session.flush()
    session.add(
        AntiPattern(
            memory_id=pickle_ap.id,
            severity="critical",
            trigger="deserialising network input with pickle.loads",
            consequence="remote code execution",
            alternative="json or msgpack",
        )
    )

    sql_ap = Memory(
        organization_id=org_id,
        type=MemoryType.ANTI_PATTERN.value,
        title="never f-string SQL queries",
        content="Trigger: building SQL with f-strings.\nConsequence: SQL injection.",
        tags=["python", "security", "sql"],
        confidence_score=0.85,
        maturity=MaturityLevel.CANON.value,
    )
    session.add(sql_ap)
    session.flush()
    session.add(
        AntiPattern(
            memory_id=sql_ap.id,
            severity="high",
            trigger="building SQL queries with f-strings or string concat",
            consequence="SQL injection",
            alternative="parameterised queries",
        )
    )

    lesson = Memory(
        organization_id=org_id,
        type=MemoryType.LESSON.value,
        title="Sanitise inputs before parsing math expressions",
        content="Validate user input is purely numeric/operator chars before parsing.",
        tags=["python", "security", "input-validation"],
        confidence_score=0.7,
        maturity=MaturityLevel.VALIDATED.value,
    )
    session.add(lesson)

    session.commit()
    return {
        "eval": eval_ap,
        "pickle": pickle_ap,
        "sql": sql_ap,
        "lesson": lesson,
    }


# ── Pure engine API ──


def test_explain_returns_eval_antipattern_for_eval_snippet(session, org):
    """`explain` must surface the eval anti-pattern when the snippet uses eval."""
    seeds = _seed_canon(session, org.id)

    hits = explain(session, "eval(user_input)", limit=3)
    assert hits, "expected at least one canon hit"
    titles = [h["memory"].title for h in hits]
    assert any("eval" in t.lower() for t in titles), (
        f"expected the eval anti-pattern in hits, got: {titles}"
    )
    # The eval AP id should be among the hits.
    ids = [h["memory"].id for h in hits]
    assert seeds["eval"].id in ids


def test_explain_empty_db_returns_empty(session):
    """No seeds → no canon hits, no crash."""
    hits = explain(session, "eval(user_input)", limit=3)
    assert hits == []


# ── CLI ──


def test_why_cli_surfaces_eval_anti_pattern(tmp_path, monkeypatch):
    """`memee why "eval(user_input)"` must mention the eval AP id."""
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    # Seed via the same in-memory session the CLI will use.
    from memee.storage.database import get_session, init_db
    from memee.storage.models import Organization

    engine = init_db()
    session = get_session(engine)
    org = session.query(Organization).first()
    seeds = _seed_canon(session, org.id)
    short = short_hash(seeds["eval"].id)
    session.close()

    result = runner.invoke(cli, ["why", "eval(user_input)"])
    assert result.exit_code == 0, result.output
    assert short in result.output, (
        f"expected [mem:{short}] in output, got:\n{result.output}"
    )
    assert "eval" in result.output.lower()
    assert "ast.literal_eval" in result.output


def test_why_cli_no_canon_hit_message(tmp_path, monkeypatch):
    """Empty DB should hit the friendly fallback message."""
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(cli, ["why", "eval(user_input)"])
    assert result.exit_code == 0, result.output
    assert "no canon hit" in result.output.lower()
    assert "memee record" in result.output.lower()


def test_why_cli_json_format(tmp_path, monkeypatch):
    """`--format json` returns parseable JSON with cite tokens."""
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    from memee.storage.database import get_session, init_db
    from memee.storage.models import Organization

    engine = init_db()
    session = get_session(engine)
    org = session.query(Organization).first()
    _seed_canon(session, org.id)
    session.close()

    result = runner.invoke(cli, ["why", "eval(user_input)", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "hits" in payload
    assert payload["hits"], "expected at least one hit"
    first = payload["hits"][0]
    assert first["cite"].startswith("[mem:")
    assert first["cite"].endswith("]")
    assert "title" in first
    assert "severity" in first


def test_why_cli_stdin_works(tmp_path, monkeypatch):
    """Pipe a snippet via --stdin (e.g. from `git diff`)."""
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    from memee.storage.database import get_session, init_db
    from memee.storage.models import Organization

    engine = init_db()
    session = get_session(engine)
    org = session.query(Organization).first()
    seeds = _seed_canon(session, org.id)
    short = short_hash(seeds["eval"].id)
    session.close()

    result = runner.invoke(
        cli, ["why", "--stdin"], input="result = eval(user_payload)\n"
    )
    assert result.exit_code == 0, result.output
    assert short in result.output


def test_why_cli_requires_input(tmp_path, monkeypatch):
    """No snippet, --file, or --stdin → friendly error, no crash."""
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(cli, ["why"])
    # Does not crash; exit 0 (CLI helper, not a hard error).
    assert result.exit_code == 0
    # The error message goes to stderr, not stdout, in CliRunner default.
    assert "pass a snippet" in (result.output + (result.stderr_bytes or b"").decode())
