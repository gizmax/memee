"""CI guard: emitting modules must not contain prompt-injection-shaped
strings.

See ``docs/CONTENT_POLICY.md`` for rationale. This test greps the source
of the modules that write text into agent context (hook stdout, MCP tool
descriptions, briefing prepends, citation footer, digests, receipts,
onboarding arc, session ledger) for a banlist of tokens that are
characteristic of prompt injection (imperatives, format demands, reward
language, authority manufacture).

Tests fail fast (one assert per module per banned token) so a CI failure
points exactly at the offender. New emitting code is added to
``EMITTING_MODULES`` below; new injection patterns get appended to
``BANLIST``.

v2.2.3 extension: seed-pack ``*.jsonl`` titles are *also* an emitting
surface — Layer 0 of the router renders critical anti-pattern titles
verbatim into every briefing. Imperative seed titles ("Never X")
therefore inject the same shape of directive the v2.2.1 footer rewrite
banned. ``test_seed_pack_critical_titles_are_declarative`` enforces it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# Modules whose source text reaches the LLM. These are the surfaces
# audited in v2.2.1; new surfaces go here.
EMITTING_MODULES = [
    "src/memee/engine/citations.py",
    "src/memee/engine/router.py",
    "src/memee/engine/briefing.py",
    "src/memee/receipts.py",
    "src/memee/digest.py",
    "src/memee/onboarding.py",
    "src/memee/session_ledger.py",
    "src/memee/mcp_server.py",
    "src/memee/hooks_config.py",
]

# Banlist tokens (case-insensitive). See CONTENT_POLICY.md for the
# OWASP-grounded rationale per token.
#
# Each entry is a dict with:
#   "needle":  the substring (case-insensitive) we forbid in emitting
#              modules
#   "rule":    one-line explanation, surfaced on failure
BANLIST: list[dict[str, str]] = [
    # ─ Imperatives addressed at the agent ─
    {"needle": "cite memee", "rule": "imperative voice (rule 1)"},
    {"needle": "call this first", "rule": "imperative voice (rule 1)"},
    {"needle": "call this before", "rule": "imperative voice (rule 1)"},
    {"needle": "run this periodically", "rule": "imperative voice (rule 1)"},
    # ─ Format demands ─
    {"needle": "[mem:<", "rule": "format demand for [mem:<id>] tokens (rule 2)"},
    {"needle": "<8-char-id>", "rule": "format demand for short-hash tokens (rule 2)"},
    # ─ Reward / penalty language ─
    {"needle": "becomes evidence", "rule": "reward framing (rule 3)"},
    {"needle": "soft validation", "rule": "reward framing (rule 3)"},
    {"needle": "uncontested", "rule": "deadline / reward framing (rule 3)"},
    {"needle": "within 24h", "rule": "deadline pressure (rule 3)"},
    # ─ Authority manufacture ─
    {"needle": "fair game", "rule": "authority manufacture (rule 4)"},
]

# Allowlist: (file_path, needle) tuples where the banned token is
# intentionally present (regression tests, banlist source, the policy
# doc itself). Empty here on first commit; entries accumulate as needed.
ALLOWLIST: set[tuple[str, str]] = set()


def _iter_emitting_files() -> list[Path]:
    """Resolve EMITTING_MODULES paths relative to the repo root."""
    repo_root = Path(__file__).resolve().parent.parent
    paths: list[Path] = []
    for relative in EMITTING_MODULES:
        path = repo_root / relative
        if not path.exists():
            raise AssertionError(
                f"EMITTING_MODULES references missing file: {relative}. "
                f"Update the list when modules move."
            )
        paths.append(path)
    return paths


@pytest.mark.parametrize("entry", BANLIST, ids=lambda e: e["needle"])
def test_no_banned_tokens_in_emitting_modules(entry: dict[str, str]) -> None:
    """Every emitting module must be free of the banned needle.

    Failures show the offending file:line so the writer can rewrite to
    declarative voice or add the surface to ALLOWLIST with a comment
    explaining the intentional reference.
    """
    needle = entry["needle"].lower()
    rule = entry["rule"]

    offences: list[str] = []
    for path in _iter_emitting_files():
        text = path.read_text(encoding="utf-8")
        if (path.as_posix(), needle) in ALLOWLIST:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                # Skip docstring / comment lines that intentionally cite
                # the banned token to document the rule (e.g. v2.2.1
                # rewrite notes that quote the old footer).
                stripped = line.strip()
                if stripped.startswith(("#", "*", '"""', "'''", "//")):
                    continue
                # In-string occurrence inside a comment block.
                if needle in line.lower() and (
                    "v2.2.1" in line.lower() or "v2.2.2" in line.lower()
                ):
                    continue
                offences.append(f"{path.name}:{lineno}: {stripped}")

    assert not offences, (
        f"Banned token {entry['needle']!r} ({rule}) appears in emitting "
        f"modules:\n  " + "\n  ".join(offences) + "\n"
        "See docs/CONTENT_POLICY.md. Rewrite to declarative voice, or "
        "add (file, needle) to ALLOWLIST in tests/test_no_imperatives.py."
    )


def test_banlist_is_lowercase() -> None:
    """Sanity check: needles must be lowercase since we compare
    case-insensitively. Catches accidental capitalised entries."""
    for entry in BANLIST:
        assert entry["needle"] == entry["needle"].lower(), (
            f"Banlist needle {entry['needle']!r} must be lowercase."
        )


def test_emitting_modules_list_is_complete() -> None:
    """Spot-check: certain surfaces MUST be in EMITTING_MODULES.

    If someone refactors and forgets to update the list, this test
    points to the gap. Add entries here as new surfaces appear.
    """
    required = {
        "engine/citations.py",
        "mcp_server.py",
        "hooks_config.py",
        "receipts.py",
    }
    listed = {Path(m).as_posix().split("src/memee/", 1)[-1] for m in EMITTING_MODULES}
    missing = required - listed
    assert not missing, (
        f"EMITTING_MODULES is missing required surfaces: {missing}. "
        f"Update tests/test_no_imperatives.py."
    )


# ── Seed-pack title scan (v2.2.3) ──
#
# Layer 0 in ``src/memee/engine/router.py`` always prepends the top 3
# critical anti-pattern titles to every briefing. The title text is
# loaded from the DB — usually from a seed pack installed via ``memee
# pack install <name>`` — so the source-grep above can't catch it.
# This scan walks ``packs/seed/*.jsonl`` directly.
#
# Scope: only ``type == "anti_pattern"`` AND ``severity == "critical"``.
# Layer 0 is the unconditional surface and the only one where the user
# is guaranteed to see the title regardless of search relevance.
#
# Banned leading tokens (case-insensitive). Imperatives that start an
# AP title turn the bullet into a directive aimed at the agent rather
# than a state label. The rewrite pattern is: imperative → noun-phrase
# trigger ("Never use eval() on user input" → "eval() on user input →
# code execution risk").
_TITLE_IMPERATIVE_LEADS = re.compile(
    r"^\s*(never|don['’]t|do not|avoid|always|pause|treat|use this|run this|stop)\b",
    re.IGNORECASE,
)


def _iter_seed_packs() -> list[Path]:
    repo_root = Path(__file__).resolve().parent.parent
    seed_dir = repo_root / "packs" / "seed"
    if not seed_dir.is_dir():
        return []
    return sorted(seed_dir.glob("*.jsonl"))


def test_seed_pack_critical_titles_are_declarative() -> None:
    """Critical AP titles in every seed pack must be declarative.

    Layer 0 (`router.py:smart_briefing`) renders these verbatim into the
    SessionStart/UserPromptSubmit briefing. An imperative title there is
    indistinguishable from a directive injected at the agent — the same
    failure mode the v2.2.1 citation-footer rewrite fixed.

    To rewrite a flagged title, follow the pattern from
    `docs/CONTENT_POLICY.md` rule 1: replace the leading imperative with
    a noun-phrase that names the trigger or hazard. The body
    (Trigger/Consequence/Alternative) stays as-is — it's already
    declarative.
    """
    offences: list[str] = []
    for pack in _iter_seed_packs():
        for lineno, line in enumerate(pack.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                offences.append(f"{pack.name}:{lineno}: invalid JSON ({exc})")
                continue
            if record.get("type") != "anti_pattern":
                continue
            if record.get("severity") != "critical":
                continue
            title = record.get("title", "")
            if _TITLE_IMPERATIVE_LEADS.match(title):
                offences.append(f"{pack.name}:{lineno}: {title!r}")

    assert not offences, (
        "Imperative leading word in critical-AP title (will render into "
        "Layer 0 verbatim):\n  " + "\n  ".join(offences) + "\n"
        "Rewrite to a declarative trigger label. See docs/CONTENT_POLICY.md "
        "rule 1."
    )


def test_seed_packs_round_trip_json() -> None:
    """Every line in every seed pack must parse as JSON.

    Cheap regression catch for hand-edits — a stray quote in a title
    rewrite would otherwise surface only when the pack is installed.
    """
    failures: list[str] = []
    for pack in _iter_seed_packs():
        for lineno, line in enumerate(pack.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
            except json.JSONDecodeError as exc:
                failures.append(f"{pack.name}:{lineno}: {exc}")
    assert not failures, "Seed pack JSON parse errors:\n  " + "\n  ".join(failures)
