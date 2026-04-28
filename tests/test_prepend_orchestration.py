"""Tests for v2.2.0 M6 prepend orchestration.

The chain has six potential channels (onboarding, digest, aggregate
session receipt, last-session summary, update notice, citation footer).
The architecture's #1 risk is "receipt fatigue" — too many channels
firing at once turn ambient into noisy. M6 enforces:

1. **Onboarding suppresses digest.** A new user with an empty DB does
   not want a "0 applied" digest stacked under their stage-1 onboarding
   line.
2. **Hard cap of 2 prepend channels per call.** The list is in priority
   order; anything past slot 2 is dropped.

The citation footer is appended later by the briefing engine and is NOT
subject to this cap — it's load-bearing contract, not optional receipt.
"""

from __future__ import annotations

from unittest.mock import patch

from memee.cli import _gather_prepends


def test_max_two_channels_per_call():
    """Even when every receipt has something to say, only the top two
    survive the hard cap."""
    fake_strs = ["A onboarding", "B digest", "C session-receipt", "D summary", "E update"]
    with patch("memee.onboarding.format_onboarding_notice", return_value=fake_strs[0]), \
         patch("memee.onboarding.is_onboarding_active", return_value=False), \
         patch("memee.digest.format_digest_notice", return_value=fake_strs[1]), \
         patch("memee.receipts.format_session_receipt", return_value=fake_strs[2]), \
         patch("memee.session_ledger.format_session_summary", return_value=fake_strs[3]), \
         patch("memee.update_check.check"), \
         patch("memee.update_check.format_notice", return_value=fake_strs[4]):
        out = _gather_prepends()
    assert len(out) == 2, f"expected ≤2 channels, got {len(out)}: {out}"
    # Priority order: onboarding wins. With onboarding active OFF here,
    # digest comes first; with the rest also firing, slot 2 is the
    # aggregate session receipt.
    assert out[0] == "A onboarding"
    assert out[1] == "B digest"


def test_onboarding_suppresses_digest():
    """When is_onboarding_active() is True the digest must be skipped
    entirely so a new user doesn't get "0 applied this week" stacked
    on top of their stage-1 onboarding receipt."""
    digest_called = False

    def digest_spy():
        nonlocal digest_called
        digest_called = True
        return "B digest"

    with patch("memee.onboarding.format_onboarding_notice",
               return_value="A onboarding"), \
         patch("memee.onboarding.is_onboarding_active", return_value=True), \
         patch("memee.digest.format_digest_notice", side_effect=digest_spy), \
         patch("memee.receipts.format_session_receipt", return_value="C session"), \
         patch("memee.session_ledger.format_session_summary", return_value=None), \
         patch("memee.update_check.check"), \
         patch("memee.update_check.format_notice", return_value=None):
        out = _gather_prepends()
    # Digest must NOT have been called when onboarding is active.
    assert not digest_called, "digest leaked through onboarding suppression"
    # Output: onboarding + the next non-suppressed channel (session receipt).
    assert out[0] == "A onboarding"
    assert "B digest" not in out


def test_returns_empty_when_all_silent():
    """Every channel returns None → orchestrator returns empty list."""
    with patch("memee.onboarding.format_onboarding_notice", return_value=None), \
         patch("memee.onboarding.is_onboarding_active", return_value=False), \
         patch("memee.digest.format_digest_notice", return_value=None), \
         patch("memee.receipts.format_session_receipt", return_value=None), \
         patch("memee.session_ledger.format_session_summary", return_value=None), \
         patch("memee.update_check.check"), \
         patch("memee.update_check.format_notice", return_value=None):
        out = _gather_prepends()
    assert out == []


def test_broken_receipt_does_not_break_briefing():
    """If one receipt raises, the others still return. Hook safety is
    load-bearing — a corrupt module can never break a session."""
    with patch("memee.onboarding.format_onboarding_notice",
               side_effect=RuntimeError("kaboom")), \
         patch("memee.onboarding.is_onboarding_active", return_value=False), \
         patch("memee.digest.format_digest_notice", return_value="B digest"), \
         patch("memee.receipts.format_session_receipt", return_value=None), \
         patch("memee.session_ledger.format_session_summary", return_value=None), \
         patch("memee.update_check.check"), \
         patch("memee.update_check.format_notice", return_value=None):
        out = _gather_prepends()
    assert out == ["B digest"]


def test_priority_order_when_only_some_fire():
    """Lower-priority channels still slot in correctly when higher ones
    are quiet. With only digest + session-summary firing, both make it."""
    with patch("memee.onboarding.format_onboarding_notice", return_value=None), \
         patch("memee.onboarding.is_onboarding_active", return_value=False), \
         patch("memee.digest.format_digest_notice", return_value="DIGEST"), \
         patch("memee.receipts.format_session_receipt", return_value=None), \
         patch("memee.session_ledger.format_session_summary", return_value="SUMMARY"), \
         patch("memee.update_check.check"), \
         patch("memee.update_check.format_notice", return_value="UPDATE"):
        out = _gather_prepends()
    assert out == ["DIGEST", "SUMMARY"]
