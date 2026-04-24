"""Database initialization, session management, FTS5 setup."""

from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from memee import config
from memee.storage.models import Base


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
    engine = create_engine(f"sqlite:///{path}", echo=False)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine=None):
    """Create all tables + FTS5 virtual table with sync triggers."""
    engine = engine or get_engine()
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        # FTS5 virtual table for full-text search on memories
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                title, content, summary, tags,
                content='memories',
                content_rowid='rowid'
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

        # Auto-sync FTS on UPDATE
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS memories_fts_au
            AFTER UPDATE ON memories BEGIN
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
