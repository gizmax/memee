"""v2.4.10 — every briefing bullet carries a verifiable evidence prefix.

A bare ``• Never do X`` bullet reads as an unsigned imperative — the exact
shape a prompt-injection guard refuses. Prefixing each bullet with its own
``[mem:<8hex> · <maturity> <conf>]`` handle turns the line into *attributed
canon state*: resolvable with ``memee cite``, scored, and provenanced. This
is attribution, not instruction (the v2.2.1 footer mistake), so it never
asks the model to emit a token.
"""

import re

import pytest

from memee.engine.citations import resolve, short_hash
from memee.engine.router import _bullet, _evidence_prefix, smart_briefing
from memee.storage.models import (
    AntiPattern,
    MaturityLevel,
    Memory,
    MemoryType,
    Project,
)

# Matches `[mem:8hex · <maturity> <conf>%]` anywhere in a bullet line.
PREFIX_RE = re.compile(r"\[mem:[0-9a-f]{8} · \w+ \d{1,3}%\]")


class _FakeMem:
    def __init__(self, id_, conf, maturity, title="t"):
        self.id = id_
        self.confidence_score = conf
        self.maturity = maturity
        self.title = title


def test_prefix_shape():
    m = _FakeMem("a1b2c3d4-0000-0000-0000-000000000000", 0.91, "canon")
    assert _evidence_prefix(m) == "[mem:a1b2c3d4 · canon 91%]"


def test_prefix_handle_resolves(session, org):
    """The 8-char handle in the prefix resolves back to the same row."""
    m = Memory(
        type=MemoryType.PATTERN.value,
        title="Use connection pooling",
        content="Use connection pooling in production",
        tags=["database"],
        confidence_score=0.8,
        maturity=MaturityLevel.CANON.value,
    )
    session.add(m)
    session.flush()
    prefix = _evidence_prefix(m)
    handle = prefix[len("[mem:") : len("[mem:") + 8]
    assert handle == short_hash(m.id)
    assert resolve(session, handle) is m


def test_prefix_empty_without_id():
    """Defensive: a row with no id yields no prefix (bare bullet fallback)."""
    m = _FakeMem("", 0.5, "hypothesis")
    assert _evidence_prefix(m) == ""


def test_prefix_handles_none_confidence():
    m = _FakeMem("dead-beef", None, None)
    # No crash; missing maturity renders as '?', missing conf as 0%.
    assert _evidence_prefix(m) == "[mem:deadbeef · ? 0%]"


def test_bullet_places_prefix_between_glyph_and_title():
    m = _FakeMem("a1b2c3d4-x", 0.9, "canon", title="Never store secrets in code")
    line = _bullet("•", m)
    assert line == "  • [mem:a1b2c3d4 · canon 90%] Never store secrets in code"


def test_bullet_falls_back_to_bare_when_no_prefix():
    m = _FakeMem("", 0.9, "canon", title="x")
    assert _bullet("•", m) == "  • x"


def test_bullet_custom_title_override():
    m = _FakeMem("a1b2c3d4-x", 0.9, "canon", title="orig")
    assert _bullet("[HIGH]", m, title="override").endswith("override")


@pytest.fixture
def env(session, org):
    proj = Project(
        organization_id=org.id,
        name="APIProject",
        path="/tmp/evidence-project",
        stack=["Python", "FastAPI"],
        tags=["python", "api"],
    )
    session.add(proj)
    session.flush()

    # A canon pattern that search should surface.
    p = Memory(
        type=MemoryType.PATTERN.value,
        title="Always use timeout on HTTP requests",
        content="Set timeout=10 to prevent hanging connections",
        tags=["api", "http", "python"],
        confidence_score=0.92,
        maturity=MaturityLevel.CANON.value,
    )
    session.add(p)

    # A critical anti-pattern → Layer 0.
    ap_mem = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        title="Never store secrets in source code",
        content="Secrets in source leak via git history",
        tags=["security", "python"],
        confidence_score=0.88,
        maturity=MaturityLevel.CANON.value,
    )
    session.add(ap_mem)
    session.flush()
    session.add(
        AntiPattern(
            memory_id=ap_mem.id,
            severity="critical",
            trigger="committing code",
            consequence="credential leak",
            alternative="use env vars",
        )
    )
    session.flush()
    return proj


def test_every_bullet_in_briefing_has_a_prefix(env, session):
    out = smart_briefing(
        session, project_path="/tmp/evidence-project",
        task="call an external API", token_budget=500,
    )
    bullet_lines = [
        ln for ln in out.splitlines()
        if ln.strip().startswith(("•", "→", "?", "✓", "["))
        and not (ln.strip().startswith("[") and ln.strip().endswith("]"))
    ]
    assert bullet_lines, "expected at least one bullet"
    for ln in bullet_lines:
        assert PREFIX_RE.search(ln), f"bullet without evidence prefix: {ln!r}"


def test_briefing_handles_resolve_back_to_rows(env, session):
    """Every handle Memee prints can be inspected with `memee cite`."""
    out = smart_briefing(
        session, project_path="/tmp/evidence-project",
        task="call an external API", token_budget=500,
    )
    handles = re.findall(r"\[mem:([0-9a-f]{8})", out)
    assert handles
    for h in handles:
        assert resolve(session, h) is not None, f"dangling handle: {h}"
