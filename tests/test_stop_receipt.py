"""Tests for the v2.1.0 Stop hook receipt sentence (feature A).

The Stop hook's prior surface was a structured metric line:

    memee learn: ok (warnings_violated=1, patterns_followed=2, new_patterns=0)

That is diagnostics, not a product surface. v2.1.0 replaces it with a
one-line English sentence that names a concrete memory and surfaces the
single most-significant thing that happened in this task. Silence is
preserved when nothing notable happened.

Significance order, highest first:
  1. MISTAKE_MADE         (warning ignored AND task failed)
  2. WARNING_INEFFECTIVE  (warning ignored, task succeeded — got lucky)
  3. KNOWLEDGE_REUSED     (pattern was applied)
  4. NEW_PATTERN          (knowledge growth — placeholder)
"""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from memee.cli import _format_stop_receipt, _truncate_title, cli
from memee.engine.impact import ImpactType


# ── Pure renderer tests (no DB) ──────────────────────────────────────────


def test_silent_when_no_significant_memory():
    """No ``most_significant_*`` fields → empty string → caller stays
    silent. Mirrors the prior no-op behaviour."""
    assert _format_stop_receipt({}) == ""
    assert (
        _format_stop_receipt(
            {"patterns_followed": 0, "warnings_violated": 0, "new_patterns": 0}
        )
        == ""
    )


def test_silent_when_kind_present_but_id_missing():
    """A non-null kind without a memory id is honestly nothing — refuse to
    fabricate a citation. Stay silent."""
    out = _format_stop_receipt(
        {
            "most_significant_kind": ImpactType.MISTAKE_MADE.value,
            "most_significant_memory_title": "Stripe API key in source code",
            "most_significant_memory_id": None,
        }
    )
    assert out == ""


def test_mistake_made_sentence():
    """MISTAKE_MADE → ``warning ignored — "..." was hit (mistake_made).``"""
    out = _format_stop_receipt(
        {
            "most_significant_kind": ImpactType.MISTAKE_MADE.value,
            "most_significant_memory_title": "Stripe API key in source code",
            "most_significant_memory_id": "a81f2c9d-e7b4-4a91-bcd1-abcdef012345",
        }
    )
    assert out == (
        'Memee: warning ignored — "Stripe API key in source code" '
        "was hit (mistake_made). [mem:a81f2c9d]"
    )
    assert len(out) <= 120


def test_warning_ineffective_sentence():
    """WARNING_INEFFECTIVE → same shape, kind label flipped."""
    out = _format_stop_receipt(
        {
            "most_significant_kind": ImpactType.WARNING_INEFFECTIVE.value,
            "most_significant_memory_title": "Never use eval() on user input",
            "most_significant_memory_id": "b39d4f2a-1234-4321-9876-abcdef012345",
        }
    )
    assert out == (
        'Memee: warning ignored — "Never use eval() on user input" '
        "was hit (warning_ineffective). [mem:b39d4f2a]"
    )
    assert len(out) <= 120


def test_knowledge_reused_sentence():
    """KNOWLEDGE_REUSED → ``reused "..." (canon).``"""
    out = _format_stop_receipt(
        {
            "most_significant_kind": ImpactType.KNOWLEDGE_REUSED.value,
            "most_significant_memory_title": (
                "React Query keys must include tenant id"
            ),
            "most_significant_memory_id": "c12a7e8f-aaaa-bbbb-cccc-ddddeeeeffff",
        }
    )
    assert out == (
        'Memee: reused "React Query keys must include tenant id" '
        "(canon). [mem:c12a7e8f]"
    )
    assert len(out) <= 120


def test_new_pattern_learned_sentence():
    """Forward-compat: any unknown kind (incl. NEW_PATTERN once added)
    renders as a learning event. The kind enum doesn't include
    NEW_PATTERN today, so we test the fall-through with a sentinel."""
    out = _format_stop_receipt(
        {
            "most_significant_kind": "new_pattern",
            "most_significant_memory_title": (
                "Retry logic on 5xx errors should use jitter"
            ),
            "most_significant_memory_id": "d44ff011-2222-3333-4444-555566667777",
        }
    )
    assert out == (
        'Memee: learned "Retry logic on 5xx errors should use jitter" '
        "as hypothesis. [mem:d44ff011]"
    )
    assert len(out) <= 120


def test_long_title_is_truncated():
    """Memory titles >60 chars get truncated with ``…`` so the line stays
    inside the 120-char terminal cap."""
    very_long = (
        "This is a very long memory title that definitely exceeds the "
        "sixty-character title cap and should be truncated"
    )
    out = _format_stop_receipt(
        {
            "most_significant_kind": ImpactType.MISTAKE_MADE.value,
            "most_significant_memory_title": very_long,
            "most_significant_memory_id": "abcd1234-1111-2222-3333-444455556666",
        }
    )
    assert len(out) <= 120
    # The visible title inside the quotes is at most 60 chars and ends with
    # ``…``. The MISTAKE_MADE template + citation already eats ~67 chars of
    # the 120-char budget, so the inner clamp loop may shrink the title
    # below 60 to fit. Either is correct — the load-bearing invariant is
    # ``len(out) <= 120`` AND the title was visibly truncated.
    title_match = re.search(r'"([^"]+)"', out)
    assert title_match is not None
    visible_title = title_match.group(1)
    assert len(visible_title) <= 60
    assert visible_title.endswith("…")
    # The truncation actually happened (we didn't fit the full 110-char
    # source title somehow).
    assert len(visible_title) < len(very_long)
    # Citation suffix is still there.
    assert out.endswith("[mem:abcd1234]")


def test_truncate_title_helper_passes_short_through():
    """Titles ≤ max are unchanged, no ellipsis added."""
    assert _truncate_title("Short title", 60) == "Short title"
    assert _truncate_title("", 60) == ""


def test_truncate_title_helper_adds_ellipsis():
    """Titles > max are truncated to exactly ``max`` chars with a trailing
    ellipsis (one char wide)."""
    out = _truncate_title("a" * 100, 60)
    assert len(out) == 60
    assert out.endswith("…")
    assert out[:59] == "a" * 59


# ── End-to-end CLI tests ─────────────────────────────────────────────────


def _patch_db(tmp_path, monkeypatch):
    db_path = tmp_path / "stop_receipt.db"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db_path))
    from memee import config

    config.settings = config.Settings(db_path=db_path)
    return db_path


def _seed_eval_anti_pattern(severity="critical"):
    """Plant a CANON anti-pattern keyed on ``eval`` so a diff that calls
    ``eval(...)`` fires in the review engine. Returns the memory id."""
    from memee.storage.database import get_session, init_db
    from memee.storage.models import (
        AntiPattern,
        MaturityLevel,
        Memory,
        MemoryType,
    )

    engine = init_db()
    session = get_session(engine)
    mem = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        title="Never use eval() on user input",
        content="eval lets attackers run arbitrary Python on user data",
        tags=["python", "security"],
        confidence_score=0.85,
        maturity=MaturityLevel.CANON.value,
    )
    session.add(mem)
    session.flush()
    session.add(
        AntiPattern(
            memory_id=mem.id,
            severity=severity,
            trigger="eval(",
            consequence="RCE",
            alternative="ast.literal_eval",
        )
    )
    session.commit()
    mem_id = mem.id
    session.close()
    return mem_id


def test_cli_no_op_silent(tmp_path, monkeypatch):
    """Empty diff → silent exit 0. Preserves the v2.0.5 behaviour: the
    Stop hook fires on every Stop event, including chats with no code."""
    _patch_db(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["learn", "--auto", "--project", str(tmp_path), "--diff", ""],
    )
    assert result.exit_code == 0
    assert "Memee:" not in result.output


def test_cli_emits_warning_ineffective_sentence(tmp_path, monkeypatch):
    """Violated warning + success outcome → ``warning_ineffective`` line."""
    _patch_db(tmp_path, monkeypatch)
    mem_id = _seed_eval_anti_pattern()
    diff = (
        "diff --git a/app.py b/app.py\n"
        "+import ast\n"
        "+result = eval(user_payload)\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "learn", "--auto", "--project", str(tmp_path),
            "--diff", diff, "--outcome", "success",
        ],
    )
    assert result.exit_code == 0
    assert "Memee: warning ignored" in result.output
    assert '"Never use eval() on user input"' in result.output
    assert "(warning_ineffective)" in result.output
    assert f"[mem:{mem_id[:8]}]" in result.output


def test_cli_emits_mistake_made_on_failure(tmp_path, monkeypatch):
    """Same violation but task FAILED → ``mistake_made``."""
    _patch_db(tmp_path, monkeypatch)
    mem_id = _seed_eval_anti_pattern()
    diff = (
        "diff --git a/app.py b/app.py\n"
        "+result = eval(user_payload)\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "learn", "--auto", "--project", str(tmp_path),
            "--diff", diff, "--outcome", "failure",
        ],
    )
    assert result.exit_code == 0
    assert "(mistake_made)" in result.output
    assert f"[mem:{mem_id[:8]}]" in result.output


def test_cli_json_flag_returns_structured_payload(tmp_path, monkeypatch):
    """``--json`` short-circuits the sentence renderer and prints the raw
    review dict — useful for scripts and debugging."""
    _patch_db(tmp_path, monkeypatch)
    _seed_eval_anti_pattern()
    diff = (
        "diff --git a/app.py b/app.py\n"
        "+result = eval(user_payload)\n"
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "learn", "--auto", "--project", str(tmp_path),
            "--diff", diff, "--json",
        ],
    )
    assert result.exit_code == 0

    import json as _json

    payload = _json.loads(result.output.strip())
    # Sentence is suppressed; the structured numbers are intact.
    assert payload.get("warnings_violated") == 1
    assert "most_significant_memory_id" in payload
    assert payload["most_significant_kind"] == ImpactType.WARNING_INEFFECTIVE.value


# ── Regression: the receipt does NOT use the old structured-line format ──


def test_no_legacy_structured_line(tmp_path, monkeypatch):
    """The pre-v2.1 line ``memee learn: ok (warnings_violated=...)`` is
    gone. Anyone parsing it should switch to ``--json``."""
    _patch_db(tmp_path, monkeypatch)
    _seed_eval_anti_pattern()
    diff = "diff --git a/app.py b/app.py\n+result = eval(payload)\n"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["learn", "--auto", "--project", str(tmp_path), "--diff", diff],
    )
    assert result.exit_code == 0
    assert "memee learn: ok" not in result.output


# ── Sanity: pytest discovery doesn't trip on the helpers ─────────────────


@pytest.mark.parametrize(
    "kind",
    [
        ImpactType.MISTAKE_MADE.value,
        ImpactType.WARNING_INEFFECTIVE.value,
        ImpactType.KNOWLEDGE_REUSED.value,
    ],
)
def test_all_kinds_under_120_chars(kind):
    """All three primary kinds, with a worst-case-length title, stay ≤120
    chars. The hard cap is the load-bearing invariant for the receipt."""
    out = _format_stop_receipt(
        {
            "most_significant_kind": kind,
            "most_significant_memory_title": "X" * 200,
            "most_significant_memory_id": "deadbeef-cafe-babe-1234-567890abcdef",
        }
    )
    assert len(out) <= 120
    assert out.endswith("[mem:deadbeef]")
