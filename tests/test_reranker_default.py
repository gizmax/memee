"""Tests for the default-on cross-encoder rerank auto-detect (v2.0.0).

Until v2.0.0 the cross-encoder reranker was opt-in via ``MEMEE_RERANK_MODEL``
and stayed off even when the 80 MB weights were already on disk in
``~/.cache/huggingface/``. R14 measured macro nDCG@10 +0.0355 when on, so
v2.0.0 flips the default: probe the HF hub cache and enable rerank
automatically when the model is cached. Three escape hatches stay covered
by tests:

    * ``MEMEE_RERANK=0`` — kill switch, off even when cached
    * ``MEMEE_RERANK_MODEL=...`` — explicit override stays sovereign
    * cache absent — rerank stays off (no surprise downloads)

We don't load the actual cross-encoder here — we mock the HF cache
directory layout so the test runs in <50 ms and doesn't depend on
sentence-transformers being importable.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Each test starts with a clean rerank env so leakage between cases
    can't make a passing test pass for the wrong reason."""
    monkeypatch.delenv("MEMEE_RERANK", raising=False)
    monkeypatch.delenv("MEMEE_RERANK_MODEL", raising=False)
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HF_HOME", raising=False)


def _reload_reranker():
    """Reimport the module so module-level state (lock, caches) starts
    clean. ``reset_for_tests`` covers the in-process model cache; this
    is for ``DEFAULT_RERANK_MODEL`` and friends."""
    from memee.engine import reranker
    importlib.reload(reranker)
    return reranker


def _make_cache(tmp_path, model_id: str):
    """Create the ``models--<a>--<b>`` directory layout HF uses."""
    hub = tmp_path / "huggingface" / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    (hub / f"models--{model_id.replace('/', '--')}").mkdir(parents=True, exist_ok=True)
    return hub.parent  # HF_HOME points at .../huggingface


def test_no_cache_no_env_returns_none(monkeypatch, tmp_path):
    """Cold install: no env vars, no cache → rerank off, no surprise download."""
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty"))  # never created
    rr = _reload_reranker()

    assert rr._model_name_from_env() is None
    status = rr.rerank_status()
    assert status["enabled"] is False
    assert status["source"] == "no_cache"
    assert status["cached"] is False


def test_cached_model_auto_detects(monkeypatch, tmp_path):
    """Warm cache + no env vars → rerank auto-enables with the default model."""
    rr = _reload_reranker()
    hf_home = _make_cache(tmp_path, rr.DEFAULT_RERANK_MODEL)
    monkeypatch.setenv("HF_HOME", str(hf_home))

    rr = _reload_reranker()  # re-resolve cache path under new HF_HOME

    assert rr._model_name_from_env() == rr.DEFAULT_RERANK_MODEL
    status = rr.rerank_status()
    assert status["enabled"] is True
    assert status["source"] == "auto_cached"
    assert status["model"] == rr.DEFAULT_RERANK_MODEL
    assert status["cached"] is True


def test_kill_switch_overrides_cache(monkeypatch, tmp_path):
    """Even with the cache warm, ``MEMEE_RERANK=0`` keeps rerank off."""
    rr = _reload_reranker()
    hf_home = _make_cache(tmp_path, rr.DEFAULT_RERANK_MODEL)
    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.setenv("MEMEE_RERANK", "0")

    rr = _reload_reranker()

    assert rr._model_name_from_env() is None
    status = rr.rerank_status()
    assert status["enabled"] is False
    assert status["source"] == "kill_switch"
    # cache still reported as present so the user knows why "0" is needed
    assert status["cached"] is True


@pytest.mark.parametrize("kill_value", ["0", "off", "false", "FALSE", "No"])
def test_kill_switch_accepts_truthy_aliases(monkeypatch, tmp_path, kill_value):
    """The kill switch is forgiving about case and the usual falsy spellings."""
    rr = _reload_reranker()
    hf_home = _make_cache(tmp_path, rr.DEFAULT_RERANK_MODEL)
    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.setenv("MEMEE_RERANK", kill_value)

    rr = _reload_reranker()
    assert rr._model_name_from_env() is None


def test_explicit_model_overrides_cache_probe(monkeypatch, tmp_path):
    """``MEMEE_RERANK_MODEL`` always wins over the auto-detect path."""
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty"))  # cache absent
    monkeypatch.setenv("MEMEE_RERANK_MODEL", "my-org/custom-rerank")

    rr = _reload_reranker()
    assert rr._model_name_from_env() == "my-org/custom-rerank"
    status = rr.rerank_status()
    assert status["enabled"] is True
    assert status["source"] == "env_explicit"
    assert status["model"] == "my-org/custom-rerank"


def test_explicit_shorthand_gets_cross_encoder_prefix(monkeypatch, tmp_path):
    """Bare model names get the ``cross-encoder/`` prefix, like before."""
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty"))
    monkeypatch.setenv("MEMEE_RERANK_MODEL", "ms-marco-MiniLM-L-6-v2")

    rr = _reload_reranker()
    assert rr._model_name_from_env() == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_hf_hub_cache_env_takes_precedence(monkeypatch, tmp_path):
    """``HF_HUB_CACHE`` points directly at the hub dir; if set, we use it
    instead of HF_HOME / ~/.cache. Mirrors huggingface_hub's own resolution
    so the probe agrees with where the loader will look."""
    rr = _reload_reranker()
    hub = tmp_path / "explicit-hub"
    hub.mkdir(parents=True)
    (hub / f"models--{rr.DEFAULT_RERANK_MODEL.replace('/', '--')}").mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    # HF_HOME pointed elsewhere should NOT be consulted when HF_HUB_CACHE is set.
    monkeypatch.setenv("HF_HOME", str(tmp_path / "wrong"))

    rr = _reload_reranker()
    assert rr._model_name_from_env() == rr.DEFAULT_RERANK_MODEL


def test_doctor_reports_rerank_status(monkeypatch, tmp_path):
    """``run_doctor`` carries the rerank snapshot through to the report dict."""
    rr = _reload_reranker()
    hf_home = _make_cache(tmp_path, rr.DEFAULT_RERANK_MODEL)
    monkeypatch.setenv("HF_HOME", str(hf_home))

    # Reload doctor *after* setting env so its captured rerank import sees
    # the right cache root on first call.
    from memee import doctor
    importlib.reload(doctor)

    health = doctor.get_rerank_health()
    assert health["enabled"] is True
    assert health["source"] == "auto_cached"
    assert health["model"] == rr.DEFAULT_RERANK_MODEL


def test_doctor_no_cache_reports_actionable_hint(monkeypatch, tmp_path, capsys):
    """When rerank is off the printed hint must point at install + warm-up."""
    monkeypatch.setenv("HF_HOME", str(tmp_path / "nope"))

    from memee import doctor
    importlib.reload(doctor)

    # Synthetic results dict — we only exercise the print branch we own,
    # not the full doctor (which touches DB + tools and is integration-y).
    results = {
        "tools": [],
        "database": {"exists": False, "memories": 0, "embedded": 0,
                     "fts_healthy": False, "size_mb": 0, "path": "x"},
        "knowledge": {"empty": True},
        "rerank": doctor.get_rerank_health(),
        "issues": [],
        "fixed": [],
    }
    doctor.print_doctor_report(results)
    out = capsys.readouterr().out
    assert "rerank: disabled" in out
    assert "memee[rerank]" in out
    assert "memee embed --download-rerank" in out
