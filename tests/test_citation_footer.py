"""Tests for the citation footer in compact briefings.

Spec:
  * Footer goes ONLY into the compact format the SessionStart hook ships.
  * Verbose / markdown formats stay clean.
  * Footer is ≤200 tokens (measured against the existing budget).
"""

from __future__ import annotations

from click.testing import CliRunner

from memee.cli import _to_compact, cli
from memee.engine.citations import CITATION_FOOTER, get_citation_footer
from memee.engine.router import _count_tokens


def _patch_db(tmp_path, monkeypatch):
    db_path = tmp_path / "footer.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    return db_path


# ── Footer string itself ──


def test_footer_is_under_200_tokens():
    """Spec: ≤200 tokens."""
    assert _count_tokens(CITATION_FOOTER) <= 200, (
        f"footer has {_count_tokens(CITATION_FOOTER)} tokens, must be ≤200"
    )


def test_footer_mentions_cite_token_format():
    """The screenshotable instruction must spell out the [mem:<id>] format."""
    f = get_citation_footer()
    assert "[mem:" in f
    assert "memee cite" in f


# ── Compact emission ──


def test_compact_includes_footer():
    """A normal compact render must end with the cite contract."""
    raw = "\n".join(f"  ✓ Pattern {i} matters" for i in range(5))
    out = _to_compact(raw, budget=300, count_tokens=_count_tokens)
    assert "[mem:" in out
    assert out.rstrip().endswith(CITATION_FOOTER.rstrip())


def test_compact_footer_under_200_tokens_on_render():
    """Even with bullets the footer block stays under the cap."""
    raw = "\n".join(f"  ✓ Pattern {i}" for i in range(3))
    out = _to_compact(raw, budget=300, count_tokens=_count_tokens)
    # Isolate the footer (the part after the last `---`).
    parts = out.split("---", 1)
    assert len(parts) == 2
    footer_only = "---" + parts[1]
    assert _count_tokens(footer_only) <= 200


def test_compact_drops_footer_when_budget_too_tight():
    """Tiny budgets (smoke tests) skip the footer to honor the budget."""
    raw = "\n".join(
        f"  ✓ Pattern {i} with a fairly long descriptive title to inflate tokens"
        for i in range(7)
    )
    # Budget=20 is well under the footer's ~58-token weight.
    out = _to_compact(raw, budget=20, count_tokens=_count_tokens)
    assert "[mem:<8-char-id>]" not in out, (
        "footer should be dropped when budget cannot fit it"
    )
    assert _count_tokens(out) <= 20 + 5


def test_compact_at_budget_300_includes_footer_and_bullets():
    """Realistic hook budget — must hold both the footer and ≥1 bullet."""
    raw = "\n".join(f"  ✓ Pattern {i} body" for i in range(5))
    out = _to_compact(raw, budget=300, count_tokens=_count_tokens)
    lines = out.splitlines()
    # Footer marker present.
    assert any("---" == ln for ln in lines)
    # At least one bullet present.
    assert any(ln.startswith("✓") for ln in lines)
    # Within budget.
    assert _count_tokens(out) <= 300 + 25


# ── Verbose / markdown formats stay clean ──


def test_default_format_does_not_get_footer(tmp_path, monkeypatch):
    """`memee brief` (default format) must NOT contain the citation footer.

    Only the compact format ships the footer — the markdown/verbose path
    is for humans and the CLAUDE.md inject already has its own context.
    """
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(cli, ["brief", "--task", "anything"])
    assert result.exit_code == 0
    assert "Cite Memee canon" not in result.output


def test_full_format_does_not_get_footer(tmp_path, monkeypatch):
    """`memee brief --full` should not append the citation footer either."""
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(cli, ["brief", "--full", "--task", "x"])
    assert result.exit_code == 0
    assert "Cite Memee canon" not in result.output


def test_compact_format_via_cli_includes_footer(tmp_path, monkeypatch):
    """End-to-end: SessionStart-style invocation gets the footer."""
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(
        cli,
        ["brief", "--task", "write tests",
         "--format", "compact", "--budget", "300"],
    )
    assert result.exit_code == 0
    assert "[mem:" in result.output
    assert "memee cite" in result.output
