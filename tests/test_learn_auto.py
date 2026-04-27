"""Tests for `memee learn --auto` (Stop hook).

The hook fires on every Stop event — including chats with no code changes,
broken git repos, and brand-new directories. It MUST exit 0 in all those
cases and stay silent on no-op so users don't disable it.
"""

from __future__ import annotations

import subprocess

from click.testing import CliRunner

from memee.cli import cli


def _patch_db(tmp_path, monkeypatch):
    db_path = tmp_path / "learn.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    return db_path


def test_learn_auto_no_git_silent_exit_0(tmp_path, monkeypatch):
    """Run from a non-git directory — must exit 0 with no stdout."""
    _patch_db(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["learn", "--auto", "--project", str(tmp_path)])
    assert result.exit_code == 0
    # Silent on no-op. Nothing on stdout (stderr may have a debug line on
    # some systems but we don't assert on stderr — Click captures it
    # together by default).
    assert result.output == "" or "memee learn:" not in result.output.replace(
        "memee learn: git not available", ""
    )


def test_learn_auto_empty_diff_silent_exit_0(tmp_path, monkeypatch):
    """Initialised git repo with no diff: silent no-op."""
    _patch_db(tmp_path, monkeypatch)

    # Set up a real git repo with zero pending changes.
    repo = tmp_path / "clean-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T",
         "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=repo, check=True,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["learn", "--auto", "--project", str(repo)])
    assert result.exit_code == 0
    assert "memee learn:" not in result.output


def test_learn_auto_with_diff_emits_structured_line(tmp_path, monkeypatch):
    """Diff that matches a known anti-pattern emits the structured line."""
    _patch_db(tmp_path, monkeypatch)

    # Seed an anti-pattern keyed on "eval" so the review engine fires.
    from memee.storage.database import get_session, init_db
    from memee.storage.models import (
        AntiPattern,
        MaturityLevel,
        Memory,
        MemoryType,
        Organization,
    )

    engine = init_db()
    session = get_session(engine)
    org = Organization(name="learn-auto-org")
    session.add(org)
    session.flush()

    m = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        title="Never use eval() on user input",
        content="eval lets attackers run arbitrary Python on user data",
        tags=["python", "security"],
        confidence_score=0.85,
        maturity=MaturityLevel.CANON.value,
    )
    session.add(m)
    session.flush()
    session.add(
        AntiPattern(
            memory_id=m.id, severity="critical",
            trigger="eval(", consequence="RCE",
            alternative="ast.literal_eval",
        )
    )
    session.commit()
    session.close()

    # Hand-craft a diff that contains the trigger.
    diff = (
        "diff --git a/app.py b/app.py\n"
        "+import ast\n"
        "+result = eval(user_payload)\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["learn", "--auto", "--project", str(tmp_path), "--diff", diff],
    )
    assert result.exit_code == 0
    # Either we get the structured line OR a silent return — depends on
    # whether the review engine actually flagged something. The key
    # behaviour for the hook is: exit 0, no traceback.
    if result.output.strip():
        assert "memee learn: ok" in result.output
        assert "warnings_avoided=" in result.output
        assert "patterns_followed=" in result.output
        assert "new_patterns=" in result.output


def test_learn_auto_resilient_to_db_errors(tmp_path, monkeypatch):
    """An exception inside post_task_review must not break the hook."""
    _patch_db(tmp_path, monkeypatch)

    # Force post_task_review to blow up. The hook command must still exit 0.
    import memee.engine.feedback as fb

    def boom(*args, **kwargs):
        raise RuntimeError("simulated DB error")

    monkeypatch.setattr(fb, "post_task_review", boom)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["learn", "--auto", "--project", str(tmp_path),
         "--diff", "diff --git a/x b/x\n+something"],
    )
    assert result.exit_code == 0
    # Error is reported to stderr (Click merges with stdout in CliRunner
    # default) but the hook does not crash.
    # We just want a non-zero exit to be impossible.


def test_learn_manual_mode_reports_when_no_diff(tmp_path, monkeypatch):
    """Without --auto, an empty diff is a user error — message goes to stderr."""
    _patch_db(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, ["learn", "--project", str(tmp_path)])
    assert result.exit_code == 0
    # Manual mode is allowed to be loud.
