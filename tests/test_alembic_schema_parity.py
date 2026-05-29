"""v2.4.15 — `alembic upgrade head` produces the same schema as metadata.

Without this guard, new tables/columns landed in ``memee.storage.models``
(or ``engine/impact.py``) drift away from the Alembic chain — the fresh
install path then silently misses `impact_events`, `search_ranking_snapshots`,
`ltr_models`, `search_events` ranker columns, `memory_connections.expires_at`
etc. The OSS ``init_db`` bootstrap catches the drift via idempotent
``create_all`` + per-column ALTERs, but the Alembic path doesn't, so a
clean upgrade leaves the DB in a broken state.

Two regressions in one file:

  * ``test_alembic_head_matches_metadata`` — temp DB, ``alembic upgrade head``,
    diff against ``Base.metadata``: 0 missing tables, 0 missing columns.
  * ``test_alembic_downgrade_round_trips`` — upgrade head → downgrade base →
    upgrade head again, schema converges to metadata. Catches a missing
    ``downgrade()`` that would silently fail in production.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _engine_with_head(tmp_path: Path, monkeypatch) -> tuple[Config, str]:
    """Spin a temp sqlite DB, run ``alembic upgrade head``, return its URL.

    ``alembic/env.py:_get_url()`` reads ``memee.config.settings.db_path``
    — it ignores the alembic config's ``sqlalchemy.url`` option entirely
    — so the test has to redirect the settings singleton to the tmp DB,
    not just set the alembic URL.
    """
    db = tmp_path / "memee.db"
    url = f"sqlite:///{db}"
    monkeypatch.setenv("MEMEE_DB_PATH", str(db))
    from memee import config as _cfg
    monkeypatch.setattr(_cfg.settings, "db_path", db)

    repo_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    command.upgrade(cfg, "head")
    return cfg, url


def _full_metadata():
    """Aggregate metadata from every module that registers SQLAlchemy tables.

    Some tables (e.g. ``impact_events``) live in non-storage modules and only
    attach to ``Base.metadata`` once their module is imported. The regression
    is precisely that the schema-parity check forgets one of these imports —
    so we import the known offenders explicitly.
    """
    # NB: importing impact registers ImpactEvent on Base.metadata.
    importlib.import_module("memee.engine.impact")
    from memee.storage.models import Base
    return Base.metadata


def test_alembic_head_matches_metadata(tmp_path, monkeypatch):
    """No missing table, no missing column. FTS5 virtual tables and the
    alembic_version bookkeeping table are excluded — they're either
    SQLite-only or Alembic-internal, not in SQLAlchemy metadata."""
    _, url = _engine_with_head(tmp_path, monkeypatch)
    engine = create_engine(url)
    insp = inspect(engine)
    meta = _full_metadata()

    db_tables = set(insp.get_table_names())
    meta_tables = set(meta.tables.keys())
    extras_to_ignore = {
        "alembic_version",
        "memories_fts",
        "memories_fts_config",
        "memories_fts_data",
        "memories_fts_docsize",
        "memories_fts_idx",
        # memee-team extends Base.metadata with its own User/Team tables
        # but ships no alembic chain — those tables are materialised via
        # ``init_db``'s ``create_all``. Importing memee-team for test
        # collection makes them appear here, so exclude them from the
        # OSS alembic parity check (they're memee-team's responsibility).
        "users",
        "teams",
    }

    missing_tables = meta_tables - db_tables - extras_to_ignore
    assert not missing_tables, (
        f"Alembic head missing tables present in SQLAlchemy metadata: "
        f"{sorted(missing_tables)}. Add a migration that creates them."
    )

    missing_cols: dict[str, list[str]] = {}
    for t in sorted(meta_tables & db_tables):
        meta_cols = {c.name for c in meta.tables[t].columns}
        db_cols = {c["name"] for c in insp.get_columns(t)}
        gap = sorted(meta_cols - db_cols)
        if gap:
            missing_cols[t] = gap
    assert not missing_cols, (
        f"Alembic head missing columns: {missing_cols}. "
        "Add an ALTER TABLE in a new migration."
    )


def test_alembic_downgrade_round_trips(tmp_path, monkeypatch):
    """upgrade head → downgrade base → upgrade head leaves a clean schema.

    Forces the latest migration's ``downgrade()`` to actually work; a
    NotImplementedError or stale-inspector bug would surface here, not in
    production when somebody runs ``alembic downgrade -1``."""
    cfg, url = _engine_with_head(tmp_path, monkeypatch)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    insp = inspect(engine)
    db_tables = set(insp.get_table_names())
    # Sentinel tables that the parity migration owns — they must come back.
    for required in ("impact_events", "ltr_models", "search_ranking_snapshots"):
        assert required in db_tables, (
            f"Round-trip lost table {required!r} — downgrade/upgrade is "
            "not idempotent for the parity migration."
        )


@pytest.mark.parametrize("col", ["ranker_version", "ranker_model_id"])
def test_search_events_has_ranker_columns(tmp_path, monkeypatch, col):
    _, url = _engine_with_head(tmp_path, monkeypatch)
    engine = create_engine(url)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("search_events")}
    assert col in cols, f"search_events.{col} missing after upgrade head"


def test_memory_connections_has_expires_at(tmp_path, monkeypatch):
    _, url = _engine_with_head(tmp_path, monkeypatch)
    engine = create_engine(url)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("memory_connections")}
    assert "expires_at" in cols
