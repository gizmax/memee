"""Regression for the `memee bar` Dock-hiding fix.

Pre-v2.4.9 pipx installs surfaced the Python launcher as a "Python" entry
in the macOS Dock and Cmd+Tab because rumps without a `.app` bundle has no
menu-bar-only flag set.

v2.4.9 tried to mutate the `NSBundle` `LSUIElement` info-dict key, but that
key is only read from a bundle's Info.plist at launch — mutating the
running interpreter's dict is a no-op for the Dock. v2.4.12 switches to the
documented runtime path: `NSApplication.setActivationPolicy_(1)`
(NSApplicationActivationPolicyAccessory).

These tests verify the call happens (and degrades silently without AppKit)
without needing a real WindowServer.
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

    def test_sets_accessory_activation_policy(self):
        helper = self._import_helper()

        fake_app = mock.Mock()
        fake_nsapplication = mock.Mock()
        fake_nsapplication.sharedApplication.return_value = fake_app

        fake_appkit = types.ModuleType("AppKit")
        fake_appkit.NSApplication = fake_nsapplication  # type: ignore[attr-defined]

        with mock.patch.dict(sys.modules, {"AppKit": fake_appkit}):
            helper()

        # Accessory policy == 1.
        fake_app.setActivationPolicy_.assert_called_once_with(1)

    def test_silent_when_appkit_missing(self):
        """No AppKit available → helper degrades silently, no exception."""
        helper = self._import_helper()

        # Hide AppKit from sys.modules so the inner `from AppKit import …`
        # raises ImportError.
        with mock.patch.dict(sys.modules, {"AppKit": None}):
            try:
                helper()
            except Exception as e:  # noqa: BLE001 — proving it doesn't raise
                self.fail(f"_hide_from_dock raised on missing AppKit: {e!r}")

    def test_silent_when_setpolicy_raises(self):
        """A WindowServer / pyobjc failure must not propagate."""
        helper = self._import_helper()

        fake_app = mock.Mock()
        fake_app.setActivationPolicy_.side_effect = RuntimeError("no window server")
        fake_nsapplication = mock.Mock()
        fake_nsapplication.sharedApplication.return_value = fake_app

        fake_appkit = types.ModuleType("AppKit")
        fake_appkit.NSApplication = fake_nsapplication  # type: ignore[attr-defined]

        with mock.patch.dict(sys.modules, {"AppKit": fake_appkit}):
            # Must not raise.
            helper()


if __name__ == "__main__":
    unittest.main()
