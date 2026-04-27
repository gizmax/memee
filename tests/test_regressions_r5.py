"""Regression tests for R5 concurrency / honesty / security fixes.

One test per fix in issues 15-22 so the bugs cannot come back. Kept in
one file for locality — each test is small and self-contained.
"""

from __future__ import annotations

import os
import threading

import pytest

from memee.engine.embeddings import _model_lock, get_model
from memee.engine.models import get_model_family
from memee.engine.telemetry import record_search_event
from memee.storage.models import (
    Memory,
    MemoryType,
    SearchEvent,
)


# ── Fix 15-16 (research timeout/baseline) — research engine removed in v2.0.0 ──
# Tests covering ``research_mod.run_verify``, ``run_guard``, ``run_iteration``,
# and ``log_iteration`` were deleted with ``src/memee/engine/research.py``.


# ── Fix 18: telemetry survives parent rollback ──


class TestTelemetrySurvivesRollback:

    def test_record_then_rollback_keeps_event(self, session):
        """Previously: flush-only telemetry rows were lost if the caller
        rolled back. Now written on an independent short-lived session —
        the row survives outer rollback.

        Models the common FastAPI pattern: search runs (read-only during
        the handler), telemetry is recorded, then the handler raises and
        the per-request session rolls back.
        """
        os.environ["MEMEE_TELEMETRY"] = "1"
        session.query(SearchEvent).delete()
        session.commit()

        # Seed a memory in its own committed txn, to reference as top result.
        m = Memory(type=MemoryType.PATTERN.value, title="rollback probe", content="x")
        session.add(m)
        session.commit()

        # Caller starts a new read-heavy txn (FastAPI GET handler style) —
        # issues a query (implicit BEGIN) then records telemetry, then an
        # exception leads to rollback.
        _ = session.query(Memory).all()
        record_search_event(
            session, "probe", [{"memory": m, "total_score": 1.0}], latency_ms=3.0
        )
        session.rollback()

        # After rollback, expire to bust the session's identity cache so
        # the next query reads fresh from disk.
        session.expire_all()
        events = session.query(SearchEvent).all()
        assert len(events) == 1, (
            f"telemetry row did not survive outer rollback (got {len(events)} events)"
        )
        assert events[0].query_text == "probe"


# ── Fix 19: embeddings thread-safe init ──


class TestEmbeddingsThreadSafeInit:

    def test_lock_is_module_level(self):
        """Smoke test: the module exposes a threading.Lock so we know the
        double-checked locking is wired up, not just commented about."""
        assert isinstance(_model_lock, type(threading.Lock()))

    def test_concurrent_get_model_no_crash(self):
        """Ten concurrent get_model() calls must not crash. If the model
        can't load in offline CI that's fine — we just assert no exception
        leaks out of get_model() under contention."""
        results: list[object] = []
        errors: list[Exception] = []

        def worker():
            try:
                results.append(get_model())
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"get_model raised under contention: {errors}"
        # All ten threads must observe the SAME instance (or consistently None).
        assert len(set(id(r) for r in results)) == 1


# ── Fix 21: model family detection — token-based, no substring misfires ──


class TestModelFamilyTokens:

    @pytest.mark.parametrize("name,expected", [
        # Original happy paths must still work.
        ("claude-opus-4", "anthropic"),
        ("claude-sonnet-4-20250514", "anthropic"),
        ("gpt-4o", "openai"),
        ("gpt-5", "openai"),                  # novel OpenAI family — must work
        ("o1-preview", "openai"),
        ("o3-mini", "openai"),
        ("o5-mini", "openai"),                # future o-series
        ("gemini-2.0-flash", "google"),
        ("llama-3.1-70b", "meta"),
        ("llama-4-405b", "meta"),             # novel meta size
        ("mistral-large", "mistral"),
        ("mixtral-8x7b", "mistral"),
        ("ollama-server", "local"),
        ("deepseek-v3", "deepseek"),
        ("qwen-72b", "alibaba"),
        ("grok-2", "xai"),
        # Adversarial: substring match would classify these wrong.
        ("sonnet-transformers", "unknown"),   # NOT anthropic (hf library-ish)
        ("opusoft-local-tool", "unknown"),    # "opus" as substring of product
        ("custom-model-v1", "unknown"),
        (None, "unknown"),
        ("", "unknown"),
    ])
    def test_family_classification(self, name, expected):
        assert get_model_family(name) == expected


# ── Fix 22 (dashboard XSS escape) — web dashboard removed in v2.0.0 ──
# The dashboard HTML template (and its escapeHTML helper) lived in
# ``src/memee/api/routes/dashboard.py``, which was deleted with the rest of
# the dashboard surface. The /api/v1 JSON layer encodes user content safely
# by default, so there is no remaining HTML-rendering path to regress.
