"""Tests for v2.2.5 doctor additions: smoke probe + dep manifest.

Both surfaces are pull-shaped (the user runs `memee doctor` or
`memee doctor --smoke`) so the content policy from v2.2.1 doesn't
apply — these are *user-invoked* outputs, not cross-context prepends.
What they have to do is **be correct**: a green report that lies is
worse than no report. These tests exercise the probe against a real
in-memory pipeline and assert each step's `ok` flag against ground
truth, plus the failure path through the same surface.
"""

from __future__ import annotations


from memee.doctor import get_dep_manifest, run_smoke_probe
from memee.storage.models import Memory


# ── Smoke probe — happy path ──


def test_smoke_probe_runs_all_four_steps(session, monkeypatch):
    """A fresh DB (just the session fixture's empty schema) should still
    let every probe step pass. Embeddings happen to be unavailable
    inside the test conftest (offline HF), so search falls back to BM25
    + tag matching — the probe must work in that mode too."""
    # The smoke probe opens its own session via init_db; point it at the
    # same DB the test fixture is using.
    import memee.config as cfg
    monkeypatch.setattr(cfg.settings, "db_path", cfg.settings.db_path)

    result = run_smoke_probe()
    assert result["ok"] is True, result
    step_names = [s["name"] for s in result["steps"]]
    assert step_names == ["open_db", "record", "search", "brief"]
    for step in result["steps"]:
        assert step["ok"] is True, step


def test_smoke_probe_cleans_up_its_own_row():
    """The probe writes one memory and deletes it. After a successful
    run the DB must contain zero rows tagged ``__smoke__``. Catches the
    failure mode where the cleanup branch silently regresses."""
    from memee.storage.database import get_session, init_db

    run_smoke_probe()

    s = get_session(init_db())
    leftover = (
        s.query(Memory)
        .filter(Memory.title.like("doctor smoke probe%"))
        .count()
    )
    s.close()
    assert leftover == 0, "smoke probe leaked its own row"


# ── Smoke probe — failure paths ──


def test_smoke_probe_reports_open_db_failure(monkeypatch):
    """If the first step (open_db) blows up, the probe must return
    ``ok=False`` with a real error string on that step rather than
    silently claiming success or hiding the cause. Mimics the "FTS5
    missing", "DB is locked", "permission denied on ~/.memee/" classes
    of failures that surface on competitor tools' first-run."""
    import memee.storage.database as db_mod

    def _boom():
        raise RuntimeError("simulated init_db failure")

    monkeypatch.setattr(db_mod, "init_db", _boom)

    result = run_smoke_probe()
    assert result["ok"] is False
    # The open_db step records the real error message so a user can
    # action it. A "green report that lies" would be ok=True or a
    # generic message.
    open_step = result["steps"][0]
    assert open_step["name"] == "open_db"
    assert open_step["ok"] is False
    assert "simulated init_db failure" in (open_step["error"] or "")
    # And no further steps ran — the probe short-circuits on failure
    # so the operator sees the first thing that broke, not a cascade.
    assert len(result["steps"]) == 1


# ── Dependency manifest ──


def test_dep_manifest_lists_required_deps():
    """sqlite + fts5 are required. They must always appear and be
    marked ``present: True`` on any machine that can run the tests
    (because the test fixtures rely on the same primitives)."""
    m = get_dep_manifest()
    for required in ("sqlite", "fts5"):
        assert required in m, f"manifest missing required dep {required!r}"
        assert m[required]["present"] is True
        assert m[required].get("optional") is False


def test_dep_manifest_marks_optional_correctly():
    """numpy, embeddings, reranker are all optional. The manifest must
    flag them as such so the doctor report can render them with a
    neutral dim glyph instead of a red X when missing."""
    m = get_dep_manifest()
    for opt in ("numpy", "embeddings", "reranker"):
        assert opt in m
        assert m[opt].get("optional") is True


def test_dep_manifest_sqlite_includes_version():
    m = get_dep_manifest()
    # SQLite is bundled with Python; the version is always reportable.
    assert isinstance(m["sqlite"].get("version"), str)
    assert m["sqlite"]["version"]  # non-empty
