import os
from .base import OSIntegration

try:
    import AppKit
except ImportError:
    AppKit = None

from pynput.keyboard import Controller, Key

class MacOSIntegration(OSIntegration):
    def __init__(self):
        self._kb = Controller()

    def get_frontmost_app(self):
        if AppKit is None:
            return None
        try:
            return AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        except Exception:
            return None

    def activate_app(self, app_ref):
        if app_ref is None or AppKit is None:
            return False
        try:
            # Activate the app, ignoring other apps (forces it to the front)
            app_ref.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
            return True
        except Exception:
            return False

    def simulate_paste(self):
        self._kb.press(Key.cmd)
        self._kb.press('v')
        self._kb.release('v')
        self._kb.release(Key.cmd)
