"""Tests for v2.4.4 bar menubar — impact summary + update notice.

Two earned-visibility extensions to the menubar miniapp:

  1. **"How Memee helped (7d)"** — 7-day rolling impact summary
     (mistakes avoided, patterns applied, canon growth). Hidden when
     all counters are zero (no signal, no row).
  2. **Update notice** — surfaces "↑ vX.Y.Z available — pipx upgrade
     memee" at the top of the popover when a newer release is
     published on PyPI. Hidden when up to date or the check failed.

Both channels are **pull-only** (the bar fetches; nothing is pushed
into the agent prompt). Both honor the existing kill switches
(``MEMEE_QUIET``, ``MEMEE_NO_FOOTER``) via the underlying hook write
path — when the hooks are quieted, the state file doesn't update and
the bar's existing "active · last Xs ago" timestamp naturally reflects
the silence.

Tests pin the **rendering invariants**: empty state collapses to no
row, populated state produces the expected strings, partial state
(only some impact counters set) renders only the non-zero parts.
"""

from __future__ import annotations



from memee.bar.app import render_menu_strings


# ── Empty / no-signal state ──


def test_empty_state_hides_impact_and_update_rows():
    out = render_menu_strings({})
    assert out["impact"] == ""
    assert out["update"] == ""


def test_impact_block_present_in_dict():
    """The rendered dict always has both keys, even when empty —
    callers should hide the row via empty-string check, not by
    KeyError-catching."""
    out = render_menu_strings({})
    assert "impact" in out
    assert "update" in out


# ── Impact summary rendering ──


def test_impact_renders_all_three_counters_when_set():
    state = {
        "impact": {
            "mistakes_avoided": 3,
            "patterns_applied": 42,
            "canon_growth_7d": 2,
        }
    }
    out = render_menu_strings(state)
    assert out["impact"] == (
        "How Memee helped (7d): 3 mistakes avoided · 42 patterns applied · "
        "+2 canon this week"
    )


def test_impact_skips_zero_counters():
    """Zero counters drop out — only positives surface. Earned silence
    on the per-counter level too."""
    state = {
        "impact": {
            "mistakes_avoided": 0,
            "patterns_applied": 17,
            "canon_growth_7d": 0,
        }
    }
    out = render_menu_strings(state)
    assert out["impact"] == "How Memee helped (7d): 17 patterns applied"


def test_impact_pluralisation():
    """1 mistake avoided, not 1 mistakes avoided. Same for patterns."""
    state = {
        "impact": {
            "mistakes_avoided": 1,
            "patterns_applied": 1,
            "canon_growth_7d": 0,
        }
    }
    out = render_menu_strings(state)
    assert "1 mistake avoided" in out["impact"]
    assert "1 pattern applied" in out["impact"]


def test_impact_all_zero_collapses_to_empty():
    state = {"impact": {"mistakes_avoided": 0, "patterns_applied": 0}}
    out = render_menu_strings(state)
    assert out["impact"] == ""


def test_impact_missing_field_safe():
    """Missing impact dict (older Memee installed) → empty string,
    not KeyError."""
    state = {"impact": None}
    out = render_menu_strings(state)
    assert out["impact"] == ""


# ── Update notice rendering ──


def test_update_renders_when_available():
    state = {
        "update": {
            "available": True,
            "current": "2.4.3",
            "latest": "2.4.4",
        }
    }
    out = render_menu_strings(state)
    assert out["update"] == "↑ v2.4.4 available — pipx upgrade memee"


def test_update_hidden_when_not_available():
    state = {
        "update": {
            "available": False,
            "current": "2.4.4",
            "latest": "2.4.4",
        }
    }
    out = render_menu_strings(state)
    assert out["update"] == ""


def test_update_hidden_when_latest_missing():
    """Network failure or PyPI unreachable → ``latest`` is None/empty.
    Don't render a useless "↑ v available" row in that case."""
    state = {"update": {"available": True, "latest": "", "current": "2.4.3"}}
    out = render_menu_strings(state)
    assert out["update"] == ""


def test_update_hidden_when_latest_equals_current():
    """Defensive: if `available` is somehow True but the version
    numbers match, hide the row anyway. Drift between
    update_check.py's `_is_newer` and the cached state file is a real
    failure mode we'd rather hide than surface as a useless click."""
    state = {
        "update": {
            "available": True,
            "current": "2.4.3",
            "latest": "2.4.3",
        }
    }
    out = render_menu_strings(state)
    assert out["update"] == ""


def test_update_missing_field_safe():
    """Pre-v2.4.4 Memee writes no `update` field — bar must not
    KeyError."""
    out = render_menu_strings({"last_brief": {"ts": "x"}})
    assert out["update"] == ""


# ── record_brief wiring ──


def test_record_brief_persists_impact_and_update(tmp_path, monkeypatch):
    """The ``record_brief`` hook helper must accept and persist the
    new ``impact`` and ``update`` payloads so the bar's renderer can
    read them."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.state import read_state, record_brief

    record_brief(
        project="/p",
        task="ship features",
        bullets=7,
        tokens=420,
        totals={"memories": 50, "canon": 12, "validated": 35, "critical_warnings": 2},
        impact={"mistakes_avoided": 4, "patterns_applied": 18, "canon_growth_7d": 1},
        update={"available": True, "current": "2.4.3", "latest": "2.4.4"},
    )
    state = read_state()
    assert state["impact"]["mistakes_avoided"] == 4
    assert state["update"]["latest"] == "2.4.4"


def test_record_brief_optional_payloads_default_to_absent(tmp_path, monkeypatch):
    """Omitting ``impact`` / ``update`` must NOT plant empty dicts on
    state.json. The renderer's earned-silence logic depends on
    missing-vs-empty distinguishability."""
    monkeypatch.setenv("MEMEE_HOME", str(tmp_path))
    from memee.bar.state import read_state, record_brief

    record_brief(
        project="/p", task="x", bullets=1, tokens=10,
        totals={"memories": 1},
    )
    state = read_state()
    assert "impact" not in state
    assert "update" not in state
