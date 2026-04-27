"""Tests for `memee brief --format compact --budget N`.

The hook layer fires `memee brief` on SessionStart and UserPromptSubmit, so
the compact output has to (a) respect the token budget tightly, (b) be
robust to a missing project (fresh clone, hook fires before setup), and
(c) never print the verbose footer/headers that bloat the agent context.
"""

from __future__ import annotations

from click.testing import CliRunner

from memee.cli import _to_compact, cli
from memee.engine.router import _count_tokens
from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryType,
    Project,
)


def _patch_db(tmp_path, monkeypatch):
    db_path = tmp_path / "compact.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    return db_path


def _seed(session, org):
    """Drop in enough patterns + criticals that compact has to truncate."""
    proj = Project(
        organization_id=org.id, name="CompactProj",
        path="/tmp/compact-proj",
        stack=["Python"],
        tags=["python"],
    )
    session.add(proj)
    session.flush()

    for i in range(8):
        m = Memory(
            type=MemoryType.PATTERN.value,
            title=f"Important pattern {i} about Python testing and best practices",
            content=f"This is the body for pattern {i}, long enough to matter.",
            tags=["python", "testing"],
            confidence_score=0.9,
            maturity=MaturityLevel.CANON.value,
        )
        session.add(m)

    for i in range(3):
        m = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            title=f"Critical warning {i} about secrets in code",
            content=f"Warning body {i}",
            tags=["security"],
            confidence_score=0.85,
        )
        session.add(m)
        session.flush()
        ap = AntiPattern(
            memory_id=m.id, severity="critical",
            trigger="hardcoded secret", consequence="leak",
            alternative="use env var",
        )
        session.add(ap)
    session.commit()


# ── Pure trimmer ──


def test_to_compact_drops_footer_and_headers():
    raw = (
        "CRITICAL (always):\n"
        "  ⚠ Never store API keys in code\n"
        "\n"
        'For "write tests":\n'
        "  ✓ Use pytest fixtures (90%)\n"
        "  ✓ Mock external APIs (88%)\n"
        "\n"
        "[123 memories — memee search <query> for more]\n"
        "[~145 tokens / 300 budget]\n"
    )
    out = _to_compact(raw, budget=500, count_tokens=_count_tokens)
    lines = out.splitlines()
    # Footer lines and section headers stripped.
    assert all(not (l.startswith("[") and l.endswith("]")) for l in lines)
    assert "CRITICAL (always):" not in lines
    assert 'For "write tests":' not in lines
    # Bullets survive.
    assert any("Never store API keys" in l for l in lines)
    assert any("pytest fixtures" in l for l in lines)


def test_to_compact_caps_at_seven_bullets():
    """Bullet section caps at 7. The 2-line citation footer (---\\n<text>)
    is appended after the bullets so total lines may be up to 9 — the
    cap is on the bullet count, not on the footer block."""
    raw = "\n".join(f"  ✓ Pattern {i} blah blah" for i in range(20))
    out = _to_compact(raw, budget=2000, count_tokens=_count_tokens)
    bullet_lines = [
        ln for ln in out.splitlines()
        if ln.startswith("✓") or ln.startswith("⚠") or ln.startswith("[")
    ]
    assert len(bullet_lines) <= 7


def test_to_compact_respects_token_budget():
    """A tight budget forces a shorter result regardless of bullet count."""
    raw = "\n".join(
        f"  ✓ Pattern {i} with a fairly long descriptive title to inflate tokens"
        for i in range(7)
    )
    out = _to_compact(raw, budget=20, count_tokens=_count_tokens)
    # Must fit under (approximately) the budget. Token count is a 4-char
    # heuristic so we allow a small slack but verify the trimmer kicked in
    # (i.e. fewer than the original 7 lines).
    assert _count_tokens(out) <= 20 + 5
    assert len(out.splitlines()) < 7


def test_to_compact_handles_empty_input():
    assert _to_compact("", budget=300, count_tokens=_count_tokens) == ""


# ── CLI integration ──


def test_brief_compact_via_cli_respects_budget(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)

    from memee.storage.database import get_session, init_db
    from memee.storage.models import Organization

    engine = init_db()
    session = get_session(engine)
    org = Organization(name="brief-compact-org")
    session.add(org)
    session.flush()
    _seed(session, org)
    session.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["brief", "--task", "write unit tests for python module",
         "--format", "compact", "--budget", "300"],
    )
    assert result.exit_code == 0, result.output
    out = result.output.strip()
    assert out, "compact briefing should produce output"

    tokens = _count_tokens(out)
    # Allow a small slack — token count is a 4-char heuristic.
    assert tokens <= 300 + 25, (
        f"Compact briefing exceeded budget: {tokens} tokens for budget 300\n"
        f"---\n{out}\n---"
    )

    # No verbose footer.
    assert "memories — memee search" not in out
    assert "tokens / " not in out


def test_brief_compact_handles_missing_project(tmp_path, monkeypatch):
    """Hook can fire from a path that's not registered — must not crash."""
    _patch_db(tmp_path, monkeypatch)

    from memee.storage.database import get_session, init_db
    from memee.storage.models import Organization

    engine = init_db()
    session = get_session(engine)
    org = Organization(name="missing-proj-org")
    session.add(org)
    session.commit()
    session.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["brief", "--project", str(tmp_path / "does-not-exist"),
         "--task", "anything", "--format", "compact", "--budget", "200"],
    )
    # Even with no project + no memories the command must succeed.
    assert result.exit_code == 0, result.output


def test_brief_compact_at_budget_200(tmp_path, monkeypatch):
    """The UserPromptSubmit hook uses --budget 200; must hold at that size."""
    _patch_db(tmp_path, monkeypatch)

    from memee.storage.database import get_session, init_db
    from memee.storage.models import Organization

    engine = init_db()
    session = get_session(engine)
    org = Organization(name="budget-200-org")
    session.add(org)
    session.flush()
    _seed(session, org)
    session.close()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["brief", "--task", "fix flaky tests in CI pipeline",
         "--format", "compact", "--budget", "200"],
    )
    assert result.exit_code == 0
    tokens = _count_tokens(result.output.strip())
    assert tokens <= 200 + 25
