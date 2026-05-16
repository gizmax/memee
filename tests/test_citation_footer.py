"""Tests for the citation footer in compact briefings.

v2.2.1 spec (rewritten from v2.1.x):
  * Footer is **declarative state**, not directive: describes that Memee
    context is above and points at the ``memee cite`` CLI for inspection.
    No imperative voice toward the agent, no demand for a specific output
    token format, no reward language.
  * Footer goes ONLY into the compact format the SessionStart hook ships.
    Verbose / markdown formats stay clean.
  * Footer is ≤200 tokens (well under in practice).
  * Footer suppressed when:
      - ``MEMEE_QUIET=1`` (master kill switch)
      - ``MEMEE_NO_FOOTER=1`` (per-channel kill switch)
      - the compact render produced no bullets (footer has nothing to
        point at)
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


def test_footer_is_declarative_not_imperative():
    """The v2.2.1 footer must not instruct the agent.

    Specifically it must NOT contain:
      * imperatives toward the agent ("Cite", "Run `memee cite`")
      * format demands ("[mem:<8-char-id>]")
      * reward language ("becomes evidence", "soft validation")
      * deadline language ("within 24h", "uncontested")
    These are textbook prompt-injection signals (OWASP LLM01) and trip
    Anthropic-trained models' injection detection.
    """
    f = get_citation_footer()
    assert f is not None
    banned = [
        "Cite Memee",
        "[mem:<",
        "becomes evidence",
        "soft validation",
        "within 24h",
        "fair game",
        "uncontested",
    ]
    for token in banned:
        assert token not in f, f"footer must not contain injection signal {token!r}"


def test_footer_points_at_cite_command():
    """Footer should mention ``memee cite`` so a curious user knows the entry
    point — but as information, not as a directive aimed at the agent."""
    f = get_citation_footer()
    assert f is not None
    assert "memee cite" in f


# ── Kill switches (v2.2.1) ──


def test_memee_quiet_suppresses_footer(monkeypatch):
    """``MEMEE_QUIET=1`` returns ``None`` from get_citation_footer."""
    monkeypatch.setenv("MEMEE_QUIET", "1")
    assert get_citation_footer() is None


def test_memee_no_footer_suppresses_footer(monkeypatch):
    """``MEMEE_NO_FOOTER=1`` returns ``None`` from get_citation_footer."""
    monkeypatch.setenv("MEMEE_NO_FOOTER", "1")
    assert get_citation_footer() is None


def test_kill_switches_are_off_by_default(monkeypatch):
    """Default install: footer is enabled."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.delenv("MEMEE_NO_FOOTER", raising=False)
    assert get_citation_footer() == CITATION_FOOTER


# ── Compact emission ──


def test_compact_includes_footer():
    """A normal compact render ends with the (declarative) footer."""
    raw = "\n".join(f"  ✓ Pattern {i} matters" for i in range(5))
    out = _to_compact(raw, budget=300, count_tokens=_count_tokens)
    assert "memee cite" in out
    assert out.rstrip().endswith(CITATION_FOOTER.rstrip())


def test_compact_omits_footer_when_no_bullets():
    """v2.2.1: with zero bullets the footer has nothing to point at and
    must be suppressed (the empty-context contamination case)."""
    out = _to_compact("", budget=300, count_tokens=_count_tokens)
    assert out == ""


def test_compact_omits_footer_when_quiet(monkeypatch):
    """``MEMEE_QUIET=1`` keeps the bullets but suppresses the footer."""
    monkeypatch.setenv("MEMEE_QUIET", "1")
    raw = "\n".join(f"  ✓ Pattern {i} matters" for i in range(5))
    out = _to_compact(raw, budget=300, count_tokens=_count_tokens)
    assert "memee cite" not in out
    assert "Pattern 0" in out


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
    # Pick a budget strictly smaller than the footer itself.
    footer_tokens = _count_tokens(CITATION_FOOTER)
    tiny_budget = max(footer_tokens - 1, 1)
    out = _to_compact(raw, budget=tiny_budget, count_tokens=_count_tokens)
    assert "memee cite" not in out, (
        "footer should be dropped when budget cannot fit it"
    )
    assert _count_tokens(out) <= tiny_budget + 5


def test_compact_at_budget_300_includes_footer_and_bullets():
    """Realistic hook budget — must hold both the footer and ≥1 bullet."""
    raw = "\n".join(f"  ✓ Pattern {i} body" for i in range(5))
    out = _to_compact(raw, budget=300, count_tokens=_count_tokens)
    lines = out.splitlines()
    # Footer separator present.
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
    assert "Memee context above" not in result.output


def test_full_format_does_not_get_footer(tmp_path, monkeypatch):
    """`memee brief --full` should not append the citation footer either."""
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(cli, ["brief", "--full", "--task", "x"])
    assert result.exit_code == 0
    assert "Memee context above" not in result.output


def test_compact_format_via_cli_omits_footer_on_empty_db(tmp_path, monkeypatch):
    """End-to-end: SessionStart-style invocation against an empty DB
    produces no bullets, therefore no footer either (v2.2.1 spec)."""
    _patch_db(tmp_path, monkeypatch)

    runner = CliRunner()
    runner.invoke(cli, ["init"])

    result = runner.invoke(
        cli,
        ["brief", "--task", "write tests",
         "--format", "compact", "--budget", "300"],
    )
    assert result.exit_code == 0
    # Empty DB → no bullets → no footer.
    assert "memee cite" not in result.output
