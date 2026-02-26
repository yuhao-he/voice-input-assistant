"""
Voice Input — entry point.

Initialises the Qt application, creates the main window and controller,
then starts the event loop.
"""

from __future__ import annotations

import platform
import sys

from PyQt6.QtWidgets import QApplication

from controller import AppController
from ui.window import MainWindow

_IS_MACOS = platform.system() == "Darwin"

_SETUP_BANNER = """\
╔══════════════════════════════════════════════════════════════════╗
║  Speedh Input                                                    ║
║                                                                  ║
║  First-time setup (one-time, no gcloud CLI required):            ║
║                                                                  ║
║    1. Go to console.cloud.google.com                             ║
║                                                                  ║
║    2. Select or create a project with billing enabled            ║
║                                                                  ║
║    3. Create an API key (APIs & Services → Credentials)          ║
║                                                                  ║
║    4. Restrict the key to required APIs (Library):               ║
║         • Cloud Speech-to-Text API                               ║
║         • Generative Language API                                ║
╚══════════════════════════════════════════════════════════════════╝
"""


def main():
    print(_SETUP_BANNER)

    app = QApplication(sys.argv)
    app.setOrganizationName("SpeechIput")
    app.setApplicationName("Speech Input")
    # Keep the app alive when the main window is hidden (tray-only mode).
    app.setQuitOnLastWindowClosed(False)

    # macOS: run as a pure menu-bar agent (no Dock icon, no app-switcher entry)
    if _IS_MACOS:
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory
            )
        except ImportError:
            pass

    window = MainWindow()
    controller = AppController(window)  # noqa: F841 — prevent GC

    window.set_status_idle()

    if not window.get_api_key():
        window.show_window()

    app.exec()


if __name__ == "__main__":
    main()
