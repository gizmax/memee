"""Regression for v2.4.9: `memee bar` hides the host Python from the Dock.

Pre-v2.4.9 pipx installs surfaced the Python launcher as a "Python" entry
in the macOS Dock and Cmd+Tab because rumps without a `.app` bundle has
no `LSUIElement` flag set on its info dict. `_hide_from_dock()` writes
the flag at runtime before `rumps.App` constructs.

The fix is idempotent and degrades silently when AppKit isn't available
(non-macOS, missing pyobjc). These tests verify both branches without
needing a real WindowServer to be reachable.
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest import mock


class HideFromDockTests(unittest.TestCase):
    """Direct tests on the helper, mocking AppKit."""

    def _import_helper(self):
        # Import lazily so the test file doesn't pull rumps/AppKit at
        # collection time (rumps is in the [bar] extra, may not be present).
        from memee.bar.app import _hide_from_dock
        return _hide_from_dock

    def test_sets_lsuielement_when_appkit_available(self):
        helper = self._import_helper()

        info: dict = {}
        fake_bundle = mock.Mock()
        fake_bundle.localizedInfoDictionary.return_value = None
        fake_bundle.infoDictionary.return_value = info

        fake_nsbundle = mock.Mock()
        fake_nsbundle.mainBundle.return_value = fake_bundle

        fake_appkit = types.ModuleType("AppKit")
        fake_appkit.NSBundle = fake_nsbundle  # type: ignore[attr-defined]

        with mock.patch.dict(sys.modules, {"AppKit": fake_appkit}):
            helper()

        self.assertEqual(info.get("LSUIElement"), "1")
        self.assertEqual(info.get("CFBundleName"), "Memee")
        self.assertEqual(info.get("CFBundleDisplayName"), "Memee")

    def test_prefers_localized_info_dict_when_available(self):
        helper = self._import_helper()

        info: dict = {"CFBundleName": "Memee.app already named"}
        fake_bundle = mock.Mock()
        fake_bundle.localizedInfoDictionary.return_value = info
        # infoDictionary should NOT be reached if localized is non-None.
        fake_bundle.infoDictionary.return_value = {"SHOULD_NOT_TOUCH": True}

        fake_nsbundle = mock.Mock()
        fake_nsbundle.mainBundle.return_value = fake_bundle

        fake_appkit = types.ModuleType("AppKit")
        fake_appkit.NSBundle = fake_nsbundle  # type: ignore[attr-defined]

        with mock.patch.dict(sys.modules, {"AppKit": fake_appkit}):
            helper()

        # LSUIElement landed in the localized dict.
        self.assertEqual(info.get("LSUIElement"), "1")
        # setdefault preserved the pre-existing CFBundleName.
        self.assertEqual(info.get("CFBundleName"), "Memee.app already named")

    def test_silent_when_appkit_missing(self):
        """No AppKit available → helper degrades silently, no exception."""
        helper = self._import_helper()

        # Hide AppKit from sys.modules so the inner `from AppKit import …`
        # raises ImportError.
        with mock.patch.dict(sys.modules, {"AppKit": None}):
            try:
                helper()
            except Exception as e:  # noqa: BLE001 — we're proving it doesn't raise
                self.fail(f"_hide_from_dock raised on missing AppKit: {e!r}")

    def test_silent_when_bundle_returns_none(self):
        """NSBundle plumbing returning None for both dicts → no crash."""
        helper = self._import_helper()

        fake_bundle = mock.Mock()
        fake_bundle.localizedInfoDictionary.return_value = None
        fake_bundle.infoDictionary.return_value = None

        fake_nsbundle = mock.Mock()
        fake_nsbundle.mainBundle.return_value = fake_bundle

        fake_appkit = types.ModuleType("AppKit")
        fake_appkit.NSBundle = fake_nsbundle  # type: ignore[attr-defined]

        with mock.patch.dict(sys.modules, {"AppKit": fake_appkit}):
            # No dict to write into — must not raise.
            helper()


if __name__ == "__main__":
    unittest.main()
