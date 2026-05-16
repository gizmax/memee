"""Tests for ``MEMEE_QUIET=1`` — the master cross-context shield (v2.2.1).

Memee hooks fire on every UserPromptSubmit / SessionStart / Stop in every
Claude Code session, regardless of whether the user's prompt has anything
to do with Memee. ``MEMEE_QUIET=1`` is the user's "not now" channel — one
env var that suppresses every prepend channel at once.

This test sweeps the modules that emit text into the agent's context and
verifies each one returns ``None`` / empty when ``MEMEE_QUIET`` is set.
Per-channel kill switches (``MEMEE_NO_DIGEST``, ``MEMEE_NO_RECEIPT``,
etc.) keep working — ``MEMEE_QUIET`` is the OR-of-all, not a replacement.
"""

from __future__ import annotations

from click.testing import CliRunner


def _patch_db(tmp_path, monkeypatch):
    db_path = tmp_path / "quiet.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    return db_path


def test_quiet_suppresses_citation_footer(monkeypatch):
    monkeypatch.setenv("MEMEE_QUIET", "1")
    from memee.engine.citations import get_citation_footer
    assert get_citation_footer() is None


def test_quiet_suppresses_session_receipt(monkeypatch):
    monkeypatch.setenv("MEMEE_QUIET", "1")
    from datetime import datetime, timezone
    from memee.receipts import format_session_receipt
    # Even with a real session this returns None on QUIET.
    assert format_session_receipt(
        session=None,
        since=datetime.now(timezone.utc),
        until=datetime.now(timezone.utc),
    ) is None


def test_quiet_suppresses_session_summary(monkeypatch):
    monkeypatch.setenv("MEMEE_QUIET", "1")
    from memee.session_ledger import format_session_summary
    assert format_session_summary() is None


def test_quiet_suppresses_onboarding(monkeypatch):
    monkeypatch.setenv("MEMEE_QUIET", "1")
    from memee.onboarding import format_onboarding_notice, is_onboarding_active
    assert format_onboarding_notice() is None
    assert is_onboarding_active() is False


def test_quiet_suppresses_digest(monkeypatch):
    monkeypatch.setenv("MEMEE_QUIET", "1")
    from memee.digest import format_digest_notice
    assert format_digest_notice() is None


def test_quiet_silences_brief_command(tmp_path, monkeypatch):
    """``memee brief`` (called from UserPromptSubmit hook) prints nothing."""
    monkeypatch.setenv("MEMEE_QUIET", "1")
    _patch_db(tmp_path, monkeypatch)

    from memee.cli import cli
    runner = CliRunner()
    runner.invoke(cli, ["init"])
    result = runner.invoke(cli, ["brief", "--task", "anything"])
    assert result.exit_code == 0
    assert result.output == ""


def test_quiet_silences_learn_auto(tmp_path, monkeypatch):
    """``memee learn --auto`` (Stop hook) prints nothing."""
    monkeypatch.setenv("MEMEE_QUIET", "1")
    _patch_db(tmp_path, monkeypatch)

    from memee.cli import cli
    runner = CliRunner()
    runner.invoke(cli, ["init"])
    result = runner.invoke(cli, ["learn", "--auto"])
    assert result.exit_code == 0
    assert result.output == ""


def test_quiet_does_not_break_human_commands(tmp_path, monkeypatch):
    """``MEMEE_QUIET`` must not silence user-invoked commands. ``memee status``
    is explicit user intent — they want to see output."""
    monkeypatch.setenv("MEMEE_QUIET", "1")
    _patch_db(tmp_path, monkeypatch)

    from memee.cli import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["init"])
    # Explicit user invocation still produces output.
    assert result.exit_code == 0
    assert len(result.output) > 0


def test_per_channel_kill_switches_still_work(monkeypatch):
    """Setting ``MEMEE_NO_DIGEST`` alone still suppresses just the digest."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.setenv("MEMEE_NO_DIGEST", "1")
    from memee.digest import format_digest_notice
    assert format_digest_notice() is None
    # Footer (different channel) still active.
    from memee.engine.citations import get_citation_footer
    assert get_citation_footer() is not None
