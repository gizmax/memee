"""Regression tests for R5 concurrency / honesty / security fixes.

One test per fix in issues 15-22 so the bugs cannot come back. Kept in
one file for locality — each test is small and self-contained.
"""

from __future__ import annotations

import os
import threading

import pytest

from memee.engine import research as research_mod
from memee.engine.embeddings import _model_lock, get_model
from memee.engine.evidence import add_evidence, get_evidence
from memee.engine.models import get_model_family
from memee.engine.research import (
    VerifyTimeout,
    log_iteration,
    run_guard,
    run_verify,
)
from memee.engine.telemetry import record_search_event
from memee.engine.tokens import estimate_org_savings, format_savings_report
from memee.storage.models import (
    Memory,
    MemoryType,
    Project,
    ResearchExperiment,
    SearchEvent,
)


# ── Fix 15: research subprocess timeout + non-finite metric ──


class TestResearchTimeoutAndFinite:

    def test_verify_timeout_raises(self):
        """A command that runs past the timeout must raise VerifyTimeout
        rather than hang forever or silently return None."""
        with pytest.raises(VerifyTimeout):
            run_verify("sleep 5", timeout=1)

    def test_guard_timeout_raises(self):
        with pytest.raises(VerifyTimeout):
            run_guard("sleep 5", timeout=1)

    def test_run_iteration_marks_timeout(self, session, org):
        """run_iteration catches VerifyTimeout and logs an iteration with
        status in ('timeout', 'crash'), never 'keep' or 'discard'."""
        proj = Project(organization_id=org.id, name="R5Proj", path="/r5")
        session.add(proj)
        session.commit()
        exp = ResearchExperiment(
            project_id=proj.id,
            goal="timeout test",
            metric_name="m",
            metric_direction="higher",
            verify_command="sleep 5",
            guard_command="",
            baseline_value=0.5,
            best_value=0.5,
        )
        session.add(exp)
        session.commit()

        # Monkey-patch run_verify to raise VerifyTimeout quickly
        original = research_mod.run_verify

        def fake_verify(*a, **k):
            raise VerifyTimeout("simulated 600s timeout")

        research_mod.run_verify = fake_verify
        try:
            it = research_mod.run_iteration(session, exp, description="t")
        finally:
            research_mod.run_verify = original

        assert it.status == "timeout"
        assert it.status not in ("keep", "discard", "completed")
        assert exp.crashes >= 1

    def test_non_finite_metric_rejected(self, session, org):
        """Verify command that yields inf/nan must be rejected, not kept."""
        proj = Project(organization_id=org.id, name="R5NanProj", path="/r5nan")
        session.add(proj)
        session.commit()
        exp = ResearchExperiment(
            project_id=proj.id,
            goal="nan test",
            metric_name="m",
            metric_direction="higher",
            verify_command="echo ignored",
            guard_command="",
            baseline_value=0.5,
            best_value=0.5,
        )
        session.add(exp)
        session.commit()

        original = research_mod.run_verify
        research_mod.run_verify = lambda *a, **k: float("inf")
        try:
            it_inf = research_mod.run_iteration(session, exp)
            assert it_inf.status == "crash"
            assert it_inf.status != "keep"

            research_mod.run_verify = lambda *a, **k: float("nan")
            it_nan = research_mod.run_iteration(session, exp)
            assert it_nan.status == "crash"
        finally:
            research_mod.run_verify = original

        # best_value must be untouched by the rejected iterations
        assert exp.best_value == 0.5


# ── Fix 16: baseline comparison must not short-circuit on 0.0 ──


class TestResearchBaselineZero:

    def test_best_value_zero_not_falsy(self, session, org):
        """When best_value is 0.0 ("zero failing tests"), a later metric of
        1.0 with direction=lower is a REGRESSION and must be discarded."""
        proj = Project(organization_id=org.id, name="R5ZeroProj", path="/r5zero")
        session.add(proj)
        session.commit()
        exp = ResearchExperiment(
            project_id=proj.id,
            goal="keep zero failing tests",
            metric_name="failing_tests",
            metric_direction="lower",
            verify_command="",
            guard_command="",
            baseline_value=10.0,
            best_value=0.0,  # we already reached 0 failures
        )
        session.add(exp)
        session.commit()

        # Regression: 1.0 failing tests is WORSE than 0.0 for direction=lower.
        # Old code: baseline = best_value or baseline_value = 10.0
        # → delta = 1 - 10 = -9 < 0 → is_improvement → KEEP (wrong!)
        # New code: baseline = 0.0 → delta = 1 > 0 → discard
        it = log_iteration(session, exp.id, 1.0, "keep")
        # log_iteration doesn't re-evaluate status (status is passed in),
        # but it should compute delta against 0.0, not 10.0.
        assert it.delta == pytest.approx(1.0)


# ── Fix 17: evidence.py — thread-safe add_evidence ──


class TestEvidenceConcurrency:

    def test_concurrent_appends_preserve_all(self, db_engine):
        """Five threads each append one entry. After, the chain contains
        all five distinct entries — no write is silently lost."""
        from memee.storage.database import get_session
        from memee.storage.models import Memory as MemoryModel
        from memee.storage.models import MemoryType, Organization

        s0 = get_session(db_engine)
        # Ensure an org exists (some DBs expect it); ignore if already present.
        if not s0.query(Organization).first():
            s0.add(Organization(name="r5-test-org"))
            s0.commit()
        m = MemoryModel(
            type=MemoryType.PATTERN.value,
            title="Evidence race target",
            content="Target for concurrent add_evidence calls.",
            evidence_chain=[],
        )
        s0.add(m)
        s0.commit()
        mem_id = m.id
        s0.close()

        errors: list[Exception] = []

        def worker(tag: str):
            s = get_session(db_engine)
            try:
                add_evidence(
                    s, mem_id,
                    evidence_type="validation",
                    reference=f"ref-{tag}",
                    agent=f"agent-{tag}",
                    outcome="confirmed",
                )
            except Exception as e:  # pragma: no cover — surfaces in assertion
                errors.append(e)
            finally:
                s.close()

        threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Any hard errors would mean the fix broke the happy path.
        assert not errors, f"add_evidence raised under contention: {errors}"

        s2 = get_session(db_engine)
        try:
            chain = get_evidence(s2, mem_id)
            refs = {e["ref"] for e in chain}
            assert len(chain) == 5, (
                f"expected 5 evidence entries after concurrent adds, got "
                f"{len(chain)} — a write was overwritten"
            )
            assert refs == {f"ref-{i}" for i in range(5)}
        finally:
            s2.close()


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


# ── Fix 20: tokens exposes assumptions ──


class TestTokenAssumptions:

    def test_assumptions_populated(self):
        s = estimate_org_savings(
            agents=42,
            lookups_per_agent_per_day=9,
            working_days_per_year=251,
            incidents_per_year=77,
            model="gpt-4o",
        )
        a = s.assumptions
        assert a["agents"] == 42
        assert a["lookups_per_agent_per_day"] == 9
        assert a["working_days_per_year"] == 251
        assert a["incidents_per_year"] == 77
        assert a["model"] == "gpt-4o"
        # Pricing used must be a snapshot, not a reference to the module dict.
        assert "input" in a["pricing_used"] and "output" in a["pricing_used"]

    def test_report_prints_assumptions(self):
        s = estimate_org_savings(agents=42, lookups_per_agent_per_day=9)
        report = format_savings_report(s, agents=42)
        # The caller must be able to see methodology next to numbers.
        assert "ASSUMPTIONS" in report
        assert "42" in report  # agent count
        assert "Lookups" in report


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


# ── Fix 22: dashboard escapes user-controlled HTML ──


class TestDashboardXSSEscape:

    def test_memory_title_script_tag_escaped(self, session):
        """Dashboard must NOT reflect a raw <script> tag from a memory's title.

        The dashboard HTML is static (only JS is interpolated), so we instead
        exercise the /api/v1/memories endpoint that feeds the dashboard. The
        dashboard JS is the last mile; the unit test proves the escapeHTML
        helper is present and the template uses it on title/tag.
        """
        from fastapi.testclient import TestClient

        from memee.api.app import app
        from memee.api.routes.api_v1 import get_db

        # Seed a malicious memory via the ORM.
        m = Memory(
            type=MemoryType.PATTERN.value,
            title="<script>alert(1)</script>",
            content="malicious",
            tags=["<img src=x onerror=alert(1)>"],
        )
        session.add(m)
        session.commit()

        app.dependency_overrides[get_db] = lambda: session
        try:
            client = TestClient(app)
            # The API returns raw strings (JSON is safe by encoding). The
            # dashboard HTML must include the escapeHTML helper and use it
            # in the memory-list / anti-pattern / project renderers.
            r = client.get("/")
            assert r.status_code == 200
            html = r.text
            assert "function escapeHTML" in html, (
                "escapeHTML helper missing — user content renders raw"
            )
            # Raw title template must go through escapeHTML — check the
            # interpolations we care about are wrapped.
            assert "${escapeHTML(m.title)}" in html
            assert "${escapeHTML(a.title)}" in html
            assert "${escapeHTML(p.name)}" in html
            # The raw attack payload must NOT leak into the HTML source
            # (title lives in JSON-delivered payload, not the page source).
            assert "<script>alert(1)</script>" not in html.replace(
                "function escapeHTML", ""  # don't match the helper itself
            )
        finally:
            app.dependency_overrides.pop(get_db, None)
