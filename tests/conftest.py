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

from memee.config import Settings
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
