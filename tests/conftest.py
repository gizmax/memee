"""Shared test fixtures for Memee."""

import os
import tempfile
from pathlib import Path

import pytest

# Use in-memory or temp DB for tests
os.environ["MEMEE_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test_memee.db")

# Force sentence-transformers / HuggingFace into offline mode for tests.
# Without this, the first test that touches embeddings triggers a
# ~80 MB model download (or times out if the network is throttled).
# With offline mode: if the model is cached locally it's used, otherwise
# the loader fails fast and vector search degrades gracefully to BM25+tags.
# Set BEFORE importing memee modules so no background loader is kicked off.
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")

from memee.storage.database import get_engine, get_session, init_db
from memee.storage.models import Organization


@pytest.fixture
def db_engine(tmp_path):
    """Create a fresh test database."""
    db_path = tmp_path / "test.db"
    os.environ["MEMEE_DB_PATH"] = str(db_path)
    engine = init_db(get_engine(db_path))
    return engine


@pytest.fixture
def session(db_engine):
    """Create a DB session with a default organization."""
    session = get_session(db_engine)
    org = Organization(name="test-org")
    session.add(org)
    session.commit()
    yield session
    session.close()


@pytest.fixture
def org(session):
    """Return the test organization."""
    return session.query(Organization).filter_by(name="test-org").first()


# ── Test-time isolation of the pack-install ledger ──────────────────────
#
# The ledger normally lives at ``~/.memee/packs.json`` and is appended to
# every time ``install_pack`` succeeds. Individual pack tests already
# monkeypatch ``LEDGER_PATH``, but a forgotten patch (or a test that
# imports through a side path — REPL, ``python -c``, IDE runner) silently
# pollutes the developer's real ledger. We saw this happen.
#
# The autouse fixture below makes that impossible: every test, in every
# file, gets ``memee.engine.packs.LEDGER_PATH`` repointed at a per-test
# ``tmp_path``. Existing per-test ``monkeypatch.setattr`` calls keep
# working — they just override the redirect with another tmp_path, which
# is also fine.


@pytest.fixture(autouse=True)
def _isolate_pack_ledger(tmp_path, monkeypatch):
    """Redirect ``memee.engine.packs.LEDGER_PATH`` to a per-test tmp file.

    Belt-and-suspenders default isolation. Without this, any test (or
    fixture) that calls ``install_pack`` without an explicit patch would
    write to the developer's real ``~/.memee/packs.json``.
    """
    # Importing the engine module here (lazily) avoids loading SQLAlchemy
    # for tests that never touch packs — keeps suite startup fast.
    try:
        import memee.engine.packs as _packs
    except ImportError:
        # Engine missing in some minimal environments — nothing to patch.
        yield
        return
    fake = tmp_path / "test-ledger" / "packs.json"
    fake.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_packs, "LEDGER_PATH", fake)
    yield


@pytest.fixture(scope="session", autouse=True)
def _ledger_leak_guard():
    """Fail the suite if any test wrote to the real ``~/.memee/packs.json``.

    Snapshots size + mtime at session start; checks again at session end.
    A drift means *something* bypassed the autouse fixture above — that's
    a regression we want loud. The guard skips when no real ledger exists
    yet (fresh checkout, CI sandbox).
    """
    real = Path.home() / ".memee" / "packs.json"
    before: tuple | None = None
    if real.exists():
        st = real.stat()
        before = (st.st_size, st.st_mtime_ns)

    yield

    if before is None:
        # No real ledger before the run. If one appeared, that's a leak.
        if real.exists():
            raise AssertionError(
                f"Test suite created a real ledger at {real}. "
                "Some test bypassed the _isolate_pack_ledger autouse fixture. "
                "Check for direct imports of LEDGER_PATH or for tests that "
                "construct paths relative to Path.home() directly."
            )
        return
    if not real.exists():
        # Existed before, gone now — the suite deleted it. Loud.
        raise AssertionError(
            f"Test suite removed the real ledger at {real}. "
            "Refuse to assume it's safe — restore from backup."
        )
    st = real.stat()
    after = (st.st_size, st.st_mtime_ns)
    if after != before:
        raise AssertionError(
            f"Test suite modified the real ledger at {real} "
            f"(size/mtime changed from {before} to {after}). "
            "A test bypassed the _isolate_pack_ledger autouse fixture."
        )
