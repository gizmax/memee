"""End-to-end check: the agent-facing compact briefing has no injection
shape, with the real ``python-web`` seed pack loaded.

Earlier guards cover this from different angles:

  * ``test_no_imperatives.py`` greps the *source* of emitting modules.
    Layer 0 of the router renders DB-loaded titles verbatim, so source
    grep can't see what the agent will actually receive.
  * ``test_citation_footer.py`` covers only the footer string.
  * ``test_memee_quiet.py`` covers per-channel kill switches but not
    the rendered output as a whole.

v2.2.3 adds this end-to-end check that runs after a real seed pack is
installed. If a future seed-pack contribution reintroduces a "Never X"
critical title, the briefing it produces must still fail this test —
either because the title trips the leading-word guard in
``test_no_imperatives`` or because some declarative phrasing leaks an
imperative the source grep missed.
"""

from __future__ import annotations


from memee.cli import _to_compact
from memee.engine.packs import install_pack, resolve_seed_pack
from memee.engine.router import _count_tokens, smart_briefing


def _install_python_web(session) -> None:
    """Install the bundled ``python-web`` seed pack into the test DB."""
    pack_path = resolve_seed_pack("python-web")
    assert pack_path is not None, (
        "python-web seed pack not bundled; check packs/seed/python-web.memee"
    )
    install_pack(session, pack_path, allow_unsigned=True)


# Banlist mirrored from ``test_no_imperatives.BANLIST`` — applied here to
# the *rendered* briefing rather than the source. Kept in lock-step:
# new entries in the source banlist should be added here too.
_OUTPUT_BANLIST: list[str] = [
    "cite memee",
    "[mem:<",
    "becomes evidence",
    "soft validation",
    "uncontested",
    "within 24h",
    "fair game",
    "<8-char-id>",
]


def _assert_no_injection_shape(text: str) -> None:
    lower = text.lower()
    for needle in _OUTPUT_BANLIST:
        assert needle not in lower, (
            f"Rendered briefing contains injection-shape token {needle!r}.\n"
            f"---\n{text}\n---"
        )


def _assert_no_layer0_cosmetics(text: str) -> None:
    """v2.2.3 stripped the ⚠ glyph and renamed the header."""
    assert "⚠" not in text, (
        f"Rendered briefing still contains ⚠ glyph (v2.2.3 dropped it).\n"
        f"---\n{text}\n---"
    )
    assert "CRITICAL (always):" not in text, (
        f"Pre-v2.2.3 header text still present.\n---\n{text}\n---"
    )


def test_compact_briefing_has_no_injection_shape(session, monkeypatch):
    """Full SessionStart-style render with python-web seed installed."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.delenv("MEMEE_NO_LAYER0", raising=False)
    monkeypatch.delenv("MEMEE_NO_FOOTER", raising=False)
    _install_python_web(session)

    raw = smart_briefing(
        session, task="write a fastapi endpoint", token_budget=300,
    )
    rendered = _to_compact(raw, budget=300, count_tokens=_count_tokens)

    _assert_no_injection_shape(rendered)
    _assert_no_layer0_cosmetics(rendered)

    # Compact format intentionally strips section headers (cli.py:_to_compact)
    # — the agent only sees bullets. Layer 0 contributes the ``•`` bullets,
    # so their presence is the proof Layer 0 still fires.
    assert "•" in rendered, (
        "Layer 0 bullets missing from compact output — Layer 0 fired but its "
        "rows were dropped, or `_to_compact` stripped the new glyph.\n"
        f"---\n{rendered}\n---"
    )

    # The raw (pre-compact) briefing keeps the section label.
    assert "Critical anti-patterns in scope:" in raw, (
        "Layer 0 must still surface under the v2.2.3 header in the raw briefing.\n"
        f"---\n{raw}\n---"
    )

    # Spot-check: at least one of the rewritten declarative titles lands
    # in the output. Catches a regression where the .memee bundle drifts
    # away from the .jsonl source (e.g. seed rebuilder skipped).
    declarative_markers = [
        "eval()/exec() on user-supplied input",
        "API keys in source code",
        ".env / credentials.json in git",
        "subprocess shell=True",
        "SQL string concatenation",
    ]
    assert any(marker in rendered for marker in declarative_markers), (
        "None of the v2.2.3 declarative titles appear in the briefing — "
        "did packs/seed/python-web.memee get rebuilt from the .jsonl?\n"
        f"---\n{rendered}\n---"
    )


def test_memee_no_layer0_suppresses_critical_block(session, monkeypatch):
    """``MEMEE_NO_LAYER0=1`` removes Layer 0 without touching anything else."""
    monkeypatch.delenv("MEMEE_QUIET", raising=False)
    monkeypatch.setenv("MEMEE_NO_LAYER0", "1")
    _install_python_web(session)

    raw = smart_briefing(
        session, task="write a fastapi endpoint", token_budget=300,
    )
    assert "Critical anti-patterns in scope:" not in raw, (
        f"MEMEE_NO_LAYER0=1 should suppress Layer 0.\n---\n{raw}\n---"
    )
    # Layer 1 (search-routed) still emits.
    rendered = _to_compact(raw, budget=300, count_tokens=_count_tokens)
    assert rendered, "Layer 1 should still produce output when only Layer 0 is muted"


def test_memee_quiet_suppresses_layer0(session, monkeypatch):
    """Master ``MEMEE_QUIET=1`` switch reaches Layer 0 too.

    Symmetric with the footer: any channel that fires unconditionally
    must honour the master kill switch. (CLI-level full silencing of
    ``memee brief`` is tested in test_memee_quiet.py:test_quiet_silences_brief_command.)
    """
    monkeypatch.setenv("MEMEE_QUIET", "1")
    _install_python_web(session)

    raw = smart_briefing(
        session, task="write a fastapi endpoint", token_budget=300,
    )
    assert "Critical anti-patterns in scope:" not in raw
