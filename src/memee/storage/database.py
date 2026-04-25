"""Database initialization, session management, FTS5 setup."""

import logging
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from memee import config
from memee.storage.models import Base

logger = logging.getLogger("memee.storage")


def get_engine(db_path: Path | None = None):
    """Create SQLAlchemy engine with WAL mode and FK enforcement."""
    path = db_path or config.settings.db_path
    # Guard: if the parent exists but is a regular file (user accidentally
    # created ~/.memee as a file), mkdir would raise a raw FileExistsError.
    # Surface a clean, actionable click exception instead.
    if path.parent.exists() and not path.parent.is_dir():
        import click
        raise click.ClickException(
            f"{path.parent} exists but is a file — move or delete it and rerun."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    # `check_same_thread=False` lets FastAPI worker threads share the engine's
    # connection pool. SQLite still serialises writes internally, but without
    # this flag the default sqlite3 binding raises on any cross-thread use.
    # `timeout=30` gives writers 30s to acquire the lock before raising
    # OperationalError — default 5s is too short under contention.
    engine = create_engine(
        f"sqlite:///{path}",
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        # Read back the mode; on network filesystems (NFS, SMB) WAL silently
        # downgrades to rollback journal and concurrent writers will clobber.
        mode_row = cursor.execute("PRAGMA journal_mode").fetchone()
        mode = (mode_row[0] if mode_row else "").lower()
        if mode != "wal":
            logger.warning(
                "SQLite journal mode is %s, not wal. "
                "On a network filesystem, concurrent writes may fail.",
                mode or "unknown",
            )
        cursor.execute("PRAGMA foreign_keys=ON")
        # Mirror the SA-level timeout at the SQLite level so busy_timeout
        # applies even to raw PRAGMA/DDL calls that bypass the engine pool.
        cursor.execute("PRAGMA busy_timeout=30000")
        # R11 concurrency #3: ``synchronous=NORMAL`` (the SQLite docs'
        # recommended setting in WAL mode) drops write p99 by ~34 % vs the
        # default FULL with zero correctness loss in WAL — the WAL log
        # itself still fsyncs at checkpoint, only the per-commit fsync of
        # the WAL header is skipped. Read latency is unchanged because
        # readers never fsync. Validated under macOS APFS + Linux ext4 in
        # the R11 audit.
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine


def _bootstrap_memory_organization_id(engine) -> None:
    """Ensure ``memories.organization_id`` exists and is backfilled.

    Models declare the column, but older DBs created before the multi-tenant
    work predate it. We ALTER TABLE in place if missing, then backfill every
    NULL row — but ONLY if there are NULL rows to fill. Brand-new DBs stay
    untouched so that ``memee init`` can create the org itself with the name
    the user configured (not a hardcoded "default").

    Idempotent; safe to call on every init_db.
    """
    from sqlalchemy.exc import OperationalError

    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(memories)")).fetchall()
        has_org_col = any(c[1] == "organization_id" for c in cols)

        if not has_org_col:
            try:
                conn.execute(
                    text("ALTER TABLE memories ADD COLUMN organization_id VARCHAR(36)")
                )
                conn.commit()
            except OperationalError as e:
                logger.debug("ADD COLUMN organization_id skipped: %s", e)

        # Only backfill if there's actually something to backfill. Keeps
        # brand-new DBs clean so the CLI init flow stays deterministic.
        null_rows = conn.execute(
            text("SELECT COUNT(*) FROM memories WHERE organization_id IS NULL")
        ).scalar() or 0
        if null_rows == 0:
            return

        existing = conn.execute(
            text("SELECT id FROM organizations ORDER BY created_at ASC LIMIT 1")
        ).fetchone()
        if existing is None:
            import uuid
            from datetime import datetime, timezone

            default_id = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO organizations (id, name, created_at) "
                    "VALUES (:id, :name, :ts)"
                ),
                {
                    "id": default_id,
                    "name": "default",
                    "ts": datetime.now(timezone.utc),
                },
            )
            conn.commit()
        else:
            default_id = existing[0]

        conn.execute(
            text(
                "UPDATE memories SET organization_id = :oid "
                "WHERE organization_id IS NULL"
            ),
            {"oid": default_id},
        )
        conn.commit()


def _bootstrap_porter_tokenizer(engine) -> None:
    """R11 native: rebuild the FTS5 index with ``porter unicode61`` if the
    existing index uses plain ``unicode61``. SQLite doesn't allow ALTERing
    a virtual table's tokenizer in place, so we drop + recreate + reindex.

    Idempotent: introspects ``sqlite_master`` for the current CREATE
    statement and skips when the porter tokenizer is already in place.
    The reindex itself replays every memory through the new tokenizer in
    one transaction; cost is O(N) but happens once per DB.
    """
    with engine.connect() as conn:
        try:
            row = conn.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='memories_fts'"
                )
            ).fetchone()
        except Exception:
            return
        if row is None:
            return
        ddl = (row[0] or "").lower()
        if "porter" in ddl:
            return
        # Drop and recreate. Triggers reference the FTS table by name, so we
        # must drop them too and re-create on the way out.
        try:
            conn.execute(text("DROP TRIGGER IF EXISTS memories_fts_ai"))
            conn.execute(text("DROP TRIGGER IF EXISTS memories_fts_au"))
            conn.execute(text("DROP TRIGGER IF EXISTS memories_fts_ad"))
            conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
            conn.execute(text("""
                CREATE VIRTUAL TABLE memories_fts USING fts5(
                    title, content, summary, tags,
                    content='memories',
                    content_rowid='rowid',
                    tokenize='porter unicode61'
                )
            """))
            conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS memories_fts_ai
                AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, title, content, summary, tags)
                    VALUES (new.rowid, new.title, new.content, new.summary,
                            COALESCE(new.tags, '[]'));
                END
            """))
            conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS memories_fts_au
                AFTER UPDATE OF title, content, summary, tags ON memories BEGIN
                    INSERT INTO memories_fts(
                        memories_fts, rowid, title, content, summary, tags
                    )
                    VALUES ('delete', old.rowid, old.title, old.content,
                            old.summary, COALESCE(old.tags, '[]'));
                    INSERT INTO memories_fts(rowid, title, content, summary, tags)
                    VALUES (new.rowid, new.title, new.content, new.summary,
                            COALESCE(new.tags, '[]'));
                END
            """))
            conn.execute(text("""
                CREATE TRIGGER IF NOT EXISTS memories_fts_ad
                AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(
                        memories_fts, rowid, title, content, summary, tags
                    )
                    VALUES ('delete', old.rowid, old.title, old.content,
                            old.summary, COALESCE(old.tags, '[]'));
                END
            """))
            # Replay every memory into the new tokenizer.
            conn.execute(text("""
                INSERT INTO memories_fts(rowid, title, content, summary, tags)
                SELECT rowid, title, content, summary, COALESCE(tags, '[]')
                FROM memories
            """))
            conn.commit()
            logger.info("Migrated memories_fts to porter unicode61 tokenizer")
        except Exception as e:
            logger.warning("Porter tokenizer migration failed: %s", e)


def _bootstrap_r10_indexes(engine) -> None:
    """R10 DB-audit recommendations applied as idempotent ``CREATE INDEX
    IF NOT EXISTS`` migrations on legacy DBs. ``Base.metadata.create_all``
    already creates them on fresh DBs, but legacy DBs miss them.

    Two indexes are also dropped here:
      * ``ix_search_events_accepted`` — superseded by a partial index that
        only covers rows where the LTR mining query actually selects.
      * old non-partial replacement of the same.
    The partial index is then created via raw SQL since SQLAlchemy 2.0
    does not yet emit the WHERE clause through Index() on SQLite cleanly.
    """
    statements = (
        "CREATE INDEX IF NOT EXISTS ix_research_iter_exp_num "
        "  ON research_iterations(experiment_id, iteration_number)",
        "CREATE INDEX IF NOT EXISTS ix_anti_patterns_severity "
        "  ON anti_patterns(severity)",
        "CREATE INDEX IF NOT EXISTS ix_memory_validations_created_at "
        "  ON memory_validations(created_at)",
        "CREATE INDEX IF NOT EXISTS ix_learning_snapshots_date "
        "  ON learning_snapshots(snapshot_date)",
        # Partial: LTR training query is the only consumer; non-partial wastes
        # space on every row where accepted is NULL (the vast majority).
        "CREATE INDEX IF NOT EXISTS ix_search_events_accepted_partial "
        "  ON search_events(accepted_memory_id) "
        "  WHERE accepted_memory_id IS NOT NULL",
        # Partial: only rows with a source_agent set need to participate in
        # the /agents grouping query.
        "CREATE INDEX IF NOT EXISTS ix_memories_source_agent "
        "  ON memories(source_agent) WHERE source_agent IS NOT NULL",
        # Drop the superseded non-partial index; the partial replaces it.
        "DROP INDEX IF EXISTS ix_search_events_accepted",
    )
    with engine.connect() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except Exception as e:
                logger.debug("R10 index migration skipped: %s — %s", stmt[:60], e)
        conn.commit()


def _bootstrap_search_event_ranker_columns(engine) -> None:
    """R9 LTR: add ``ranker_version`` and ``ranker_model_id`` to legacy
    ``search_events`` rows. Idempotent."""
    from sqlalchemy.exc import OperationalError

    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(search_events)")).fetchall()
        names = {c[1] for c in cols}
        if "ranker_version" not in names:
            try:
                conn.execute(
                    text(
                        "ALTER TABLE search_events ADD COLUMN ranker_version VARCHAR(40)"
                        " DEFAULT 'rrf_v1'"
                    )
                )
                conn.commit()
            except OperationalError as e:
                logger.debug("ADD COLUMN ranker_version skipped: %s", e)
        if "ranker_model_id" not in names:
            try:
                conn.execute(
                    text(
                        "ALTER TABLE search_events ADD COLUMN ranker_model_id VARCHAR(36)"
                    )
                )
                conn.commit()
            except OperationalError as e:
                logger.debug("ADD COLUMN ranker_model_id skipped: %s", e)


def _bootstrap_memory_connection_expiry(engine) -> None:
    """Add ``expires_at`` to ``memory_connections`` on legacy DBs (R9 graph
    work: time-bounded supersession edges). Idempotent.
    """
    from sqlalchemy.exc import OperationalError

    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(memory_connections)")).fetchall()
        has_expires = any(c[1] == "expires_at" for c in cols)
        if not has_expires:
            try:
                conn.execute(
                    text("ALTER TABLE memory_connections ADD COLUMN expires_at DATETIME")
                )
                conn.commit()
            except OperationalError as e:
                logger.debug("ADD COLUMN expires_at skipped: %s", e)


def init_db(engine=None):
    """Create all tables + FTS5 virtual table with sync triggers."""
    engine = engine or get_engine()
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        # FTS5 virtual table for full-text search on memories.
        # R11 native research: ``porter unicode61`` (Snowball stemmer over
        # the unicode61 word-splitter) measured +3.07 nDCG@10, +3.7
        # Recall@10, +1.1 MRR on the 55-q retrieval_eval harness vs the
        # plain ``unicode61`` default — same index size, same query
        # latency. The stemmer collapses morphological variants
        # (validate / validates / validated, optimize / optimizing /
        # optimization) so agent-task queries hit memories that share an
        # intent but not the exact surface form. Legacy DBs migrate via
        # ``_bootstrap_porter_tokenizer`` below.
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                title, content, summary, tags,
                content='memories',
                content_rowid='rowid',
                tokenize='porter unicode61'
            )
        """))

        # Auto-sync FTS on INSERT
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS memories_fts_ai
            AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, title, content, summary, tags)
                VALUES (new.rowid, new.title, new.content, new.summary,
                        COALESCE(new.tags, '[]'));
            END
        """))

        # Auto-sync FTS on UPDATE. Gate on the text-indexed columns only —
        # confidence_score / last_applied_at / application_count changes are
        # hot paths (router feedback bumps) and must NOT trigger a full
        # delete+reinsert of the FTS row. Without the column filter, at 100K
        # memories FTS rebuilds dominate write time.
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS memories_fts_au
            AFTER UPDATE OF title, content, summary, tags ON memories BEGIN
                INSERT INTO memories_fts(
                    memories_fts, rowid, title, content, summary, tags
                )
                VALUES ('delete', old.rowid, old.title, old.content,
                        old.summary, COALESCE(old.tags, '[]'));
                INSERT INTO memories_fts(rowid, title, content, summary, tags)
                VALUES (new.rowid, new.title, new.content, new.summary,
                        COALESCE(new.tags, '[]'));
            END
        """))

        # Auto-sync FTS on DELETE
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS memories_fts_ad
            AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(
                    memories_fts, rowid, title, content, summary, tags
                )
                VALUES ('delete', old.rowid, old.title, old.content,
                        old.summary, COALESCE(old.tags, '[]'));
            END
        """))

        conn.commit()

    # Backfill multi-tenant column on legacy DBs (idempotent). Must run after
    # Base.metadata.create_all so the table exists, but before alembic stamp.
    _bootstrap_memory_organization_id(engine)
    _bootstrap_memory_connection_expiry(engine)
    _bootstrap_search_event_ranker_columns(engine)
    _bootstrap_r10_indexes(engine)
    _bootstrap_porter_tokenizer(engine)

    # Stamp alembic head if the version table is empty / missing. This keeps
    # both paths (init_db-only and alembic-only) interoperable — without this
    # stamp a later `alembic upgrade head` would fail with "table already exists".
    # Guarded: pipx installs don't ship alembic.ini, and that's fine.
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")
            ).fetchone()
            needs_stamp = row is None
            if not needs_stamp:
                count = conn.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar()
                needs_stamp = (count or 0) == 0

        if needs_stamp:
            from alembic import command
            from alembic.config import Config

            # alembic.ini lives at the project root (two parents up from src/memee/storage/)
            ini_path = Path(__file__).resolve().parents[3] / "alembic.ini"
            if ini_path.exists():
                cfg = Config(str(ini_path))
                command.stamp(cfg, "head")
    except Exception:
        # Best-effort; a missing alembic.ini or any stamp error must not break
        # regular init_db() usage in production installs.
        pass

    return engine


def get_session(engine=None) -> Session:
    """Create a new database session."""
    engine = engine or get_engine()
    factory = sessionmaker(bind=engine)
    return factory()
