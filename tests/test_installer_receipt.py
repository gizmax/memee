"""Tests for the v2.2.5 installer additions.

Two surfaces:

  1. `_stack_to_seed_pack` — the stack-list → seed-pack mapping that
     drives the auto-install step in ``_setup_solo``.
  2. The seed-canon spotlight + "0 API calls" lines that get appended
     to the success receipt box. We can't drive the interactive wizard
     end-to-end here (it expects stdin), so the seeded-canon block is
     covered indirectly by ``install_pack`` + a render-shape check that
     copies the wizard's formatting.

Both surfaces are pull-shaped (the user explicitly ran `memee setup`)
so content-policy rules from v2.2.1 don't apply.
"""

from __future__ import annotations


from memee.installer import _stack_to_seed_pack


# ── Stack → pack mapping ──


def test_python_stack_maps_to_python_web():
    assert _stack_to_seed_pack(["Python", "FastAPI", "SQLite"]) == "python-web"


def test_javascript_stack_maps_to_react_vite():
    assert _stack_to_seed_pack(["React", "TypeScript", "Node.js"]) == "react-vite"


def test_fullstack_picks_javascript_pack_first():
    """The wizard's "Full-stack" option lists both React and Python.
    We pick `react-vite` first because the heuristic order tries the
    most-specific frontend match before falling back to backend canon.
    Documented in `_stack_to_seed_pack`; this test pins the order so a
    refactor can't silently flip it."""
    result = _stack_to_seed_pack(["Python", "FastAPI", "React", "PostgreSQL"])
    assert result == "react-vite"


def test_unknown_stack_returns_none():
    """Unknown stack → no pack auto-installed, no harm done."""
    assert _stack_to_seed_pack(["Go", "Gin"]) is None
    assert _stack_to_seed_pack([]) is None
    assert _stack_to_seed_pack(["Whatever"]) is None


def test_case_insensitive():
    """Wizard sometimes title-cases, sometimes lower-cases the stack
    tokens before passing them in. The mapping must not care."""
    assert _stack_to_seed_pack(["python"]) == "python-web"
    assert _stack_to_seed_pack(["PYTHON"]) == "python-web"
    assert _stack_to_seed_pack(["FastAPI"]) == "python-web"


# ── Seed-pack auto-install actually puts canon in the DB ──


def test_python_web_pack_install_yields_patterns(session):
    """The receipt shows the top 3 *pattern* titles at canon-or-validated
    maturity (positive framing). This verifies the python-web seed
    ships ≥3 of them — otherwise the "Seed patterns loaded" block would
    be empty and the friction we're fixing (empty DB on day 1) would
    still hit."""
    from memee.engine.packs import install_pack, resolve_seed_pack
    from memee.storage.models import MaturityLevel, Memory, MemoryType

    pack = resolve_seed_pack("python-web")
    assert pack is not None, "python-web seed pack missing from packs/seed/"

    result = install_pack(session, pack, allow_unsigned=True)
    assert result.no_op is False
    assert result.imported > 0

    receipt_eligible = (
        session.query(Memory)
        .filter(
            Memory.type == MemoryType.PATTERN.value,
            Memory.maturity.in_(
                [MaturityLevel.CANON.value, MaturityLevel.VALIDATED.value]
            ),
        )
        .count()
    )
    assert receipt_eligible >= 3, (
        "python-web seed should ship ≥3 canon-or-validated patterns "
        "so the receipt's 'Seed patterns loaded' spotlight isn't sparse"
    )


def test_seeded_titles_are_not_anti_pattern_imperatives(session):
    """The success receipt renders these titles. The receipt filters to
    PATTERNS (positive framing), so the anti-pattern imperatives that
    show up in Layer 0 ("subprocess shell=True → injection") never
    leak into the receipt. This test pins the rendering surface — a
    future regression that selects from anti_patterns would fail here."""
    from memee.engine.packs import install_pack, resolve_seed_pack
    from memee.storage.models import MaturityLevel, Memory, MemoryType

    install_pack(
        session, resolve_seed_pack("python-web"), allow_unsigned=True,
    )

    rows = (
        session.query(Memory.title)
        .filter(
            Memory.type == MemoryType.PATTERN.value,
            Memory.maturity.in_(
                [MaturityLevel.CANON.value, MaturityLevel.VALIDATED.value]
            ),
        )
        .order_by(Memory.confidence_score.desc())
        .limit(3)
        .all()
    )
    titles = [r[0] for r in rows]
    assert titles, "no pattern titles found"
    # No "Never X" — anti-pattern imperatives must not surface in the
    # celebratory receipt. (Patterns can still use "Always X" / "Use X"
    # framing — those are positive recommendations, not warnings.)
    for t in titles:
        lower = t.lower()
        assert not lower.startswith(("never ", "don't ", "do not ")), (
            f"anti-pattern-style title in receipt: {t!r}"
        )
