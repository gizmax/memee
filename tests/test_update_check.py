"""Tests for v2.0.3 passive update check."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from memee import update_check


@pytest.fixture(autouse=True)
def _redirect_cache(monkeypatch, tmp_path):
    cache = tmp_path / "update_check.json"
    monkeypatch.setattr(update_check, "CACHE_PATH", cache)
    yield


# ── version comparison ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "current,latest,expected",
    [
        ("2.0.2", "2.0.3", True),
        ("2.0.3", "2.0.3", False),
        ("2.0.3", "2.0.2", False),
        ("2.0.3", "2.1.0", True),
        ("2.0.3", "3.0.0", True),
        ("2.0.3.dev1", "2.0.3", False),  # dev suffix → tied → not newer
        ("2.0.3", "2.0.3.dev1", False),
        ("garbage", "2.0.3", False),
        ("2.0.3", "garbage", False),
        (None, "2.0.3", False),
        ("2.0.3", None, False),
    ],
)
def test_is_newer(current, latest, expected):
    assert update_check._is_newer(current, latest) is expected


# ── kill switch ───────────────────────────────────────────────────────────


def test_kill_switch_short_circuits(monkeypatch):
    monkeypatch.setenv("MEMEE_NO_UPDATE_CHECK", "1")
    with patch.object(update_check, "_fetch_latest_from_pypi") as m:
        s = update_check.check()
    assert s.source == "disabled"
    assert s.available is False
    assert s.latest is None
    m.assert_not_called()


def test_kill_switch_force_overrides(monkeypatch):
    """``force=True`` is for ``memee doctor``: bypass cache AND kill switch."""
    monkeypatch.setenv("MEMEE_NO_UPDATE_CHECK", "1")
    with patch.object(update_check, "_fetch_latest_from_pypi", return_value="9.9.9") as m:
        s = update_check.check(force=True)
    m.assert_called_once()
    assert s.source == "network"
    assert s.latest == "9.9.9"


# ── caching ───────────────────────────────────────────────────────────────


def test_fresh_cache_is_used(monkeypatch):
    payload = {
        "current": "2.0.2", "latest": "2.0.3", "checked_at": time.time() - 60
    }
    update_check.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    update_check.CACHE_PATH.write_text(json.dumps(payload))
    with patch.object(update_check, "_fetch_latest_from_pypi") as m:
        s = update_check.check()
    m.assert_not_called()
    assert s.source == "cache"
    assert s.latest == "2.0.3"


def test_stale_cache_triggers_refresh():
    payload = {
        "current": "2.0.2", "latest": "2.0.0",
        "checked_at": time.time() - 48 * 3600,  # 2 days ago
    }
    update_check.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    update_check.CACHE_PATH.write_text(json.dumps(payload))
    with patch.object(update_check, "_fetch_latest_from_pypi", return_value="2.0.5"):
        s = update_check.check()
    assert s.source == "network"
    assert s.latest == "2.0.5"


def test_corrupt_cache_treated_as_miss():
    update_check.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    update_check.CACHE_PATH.write_text("not json")
    with patch.object(update_check, "_fetch_latest_from_pypi", return_value="2.0.4"):
        s = update_check.check()
    assert s.source == "network"


def test_network_failure_returns_unknown_silently():
    with patch.object(update_check, "_fetch_latest_from_pypi", return_value=None):
        s = update_check.check()
    assert s.source == "unknown"
    assert s.available is False
    assert s.latest is None


# ── notice rendering ──────────────────────────────────────────────────────


def test_format_notice_returns_none_when_up_to_date():
    s = update_check.UpdateStatus(
        available=False, current="2.0.3", latest="2.0.3",
        checked_at=0.0, source="cache",
    )
    assert update_check.format_notice(s) is None


def test_format_notice_renders_when_available():
    s = update_check.UpdateStatus(
        available=True, current="2.0.2", latest="2.0.3",
        checked_at=0.0, source="cache",
    )
    msg = update_check.format_notice(s)
    assert msg is not None
    assert "2.0.2" in msg and "2.0.3" in msg
    assert "pipx upgrade memee" in msg


def test_format_notice_with_prefix():
    s = update_check.UpdateStatus(
        available=True, current="2.0.2", latest="2.0.3",
        checked_at=0.0, source="cache",
    )
    msg = update_check.format_notice(s, prefix="> ")
    assert msg is not None
    assert msg.startswith("> ")
