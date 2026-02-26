"""
System tray / menu-bar icon management.

On macOS, uses a native NSStatusItem via AppKit to avoid a known timing
issue with QSystemTrayIcon and NSApplicationActivationPolicyAccessory.
On other platforms, falls back to QSystemTrayIcon.
"""

from __future__ import annotations

import platform
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

# ---------------------------------------------------------------------------
# Optional AppKit imports (macOS only)
# ---------------------------------------------------------------------------

_APPKIT_AVAILABLE = False
_NSStatusBar = None
_NSVariableStatusItemLength = None
_NSMenu = None
_NSMenuItem = None

try:
    import objc as _objc
    from AppKit import (
        NSObject as _NSObject,
        NSStatusBar as _NSStatusBar,
        NSVariableStatusItemLength as _NSVariableStatusItemLength,
        NSMenu as _NSMenu,
        NSMenuItem as _NSMenuItem,
    )

    class _MacOSMenuTarget(_NSObject):
        """Objective-C action target that forwards menu clicks to Python callbacks."""

        def init(self):
            self = _objc.super(_MacOSMenuTarget, self).init()
            if self is None:
                return None
            self._on_toggle = None
            self._on_quit = None
            return self

        def toggleWindow_(self, sender):   # noqa: N802
            if self._on_toggle is not None:
                self._on_toggle()

        def quitApp_(self, sender):        # noqa: N802
            if self._on_quit is not None:
                self._on_quit()

    _APPKIT_AVAILABLE = True

except Exception:
    _MacOSMenuTarget = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Mic icon
# ---------------------------------------------------------------------------


def _make_mic_icon() -> QIcon:
    """Draw a simple microphone icon programmatically (no image file needed)."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    color = QColor(220, 220, 220)  # light grey — legible on both dark and light trays
    pen = QPen(color)
    pen.setWidth(3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(color)

    cx = size // 2  # 32

    # Capsule (rounded rect)
    cap_w, cap_h, cap_r = 16, 24, 8
    painter.drawRoundedRect(cx - cap_w // 2, 6, cap_w, cap_h, cap_r, cap_r)

    # Stand arc — U-shape embracing the bottom of the capsule
    painter.setBrush(Qt.BrushStyle.NoBrush)
    stand_r = 14
    arc_cy = 6 + cap_h  # y-centre of the arc = bottom edge of capsule = 30
    painter.drawArc(
        cx - stand_r, arc_cy - stand_r,
        2 * stand_r, 2 * stand_r,
        0,           # start at 3-o'clock (right side)
        -180 * 16,   # clockwise 180° → left side, passing through the bottom
    )

    # Stem
    stem_top = arc_cy + stand_r  # bottom of the arc circle
    stem_bot = 54
    painter.drawLine(cx, stem_top, cx, stem_bot)

    # Base
    painter.drawLine(cx - 10, stem_bot, cx + 10, stem_bot)

    painter.end()
    return QIcon(pixmap)


# ---------------------------------------------------------------------------
# TrayManager
# ---------------------------------------------------------------------------


class TrayManager:
    """
    Manages the system tray icon (Linux/Windows) or macOS native menu-bar item.

    Parameters
    ----------
    parent_widget : QWidget
        Parent widget — used as the QSystemTrayIcon parent on non-macOS.
    on_toggle : callable
        Called when the user clicks "Show / Hide Settings".
    on_quit : callable
        Called when the user clicks "Quit Voice Input".
    """

    def __init__(
        self,
        parent_widget: QWidget,
        on_toggle: Callable,
        on_quit: Callable,
    ):
        self._parent = parent_widget
        self._on_toggle = on_toggle
        self._on_quit = on_quit

        self._tray_icon: Optional[QSystemTrayIcon] = None
        self._macos_status_item = None
        self._macos_status_bar = None
        self._macos_menu_delegate = None
        self._tray_notified = False

        if platform.system() == "Darwin" and _APPKIT_AVAILABLE:
            self._setup_macos_native_tray()
        else:
            self._tray_icon = self._setup_qt_tray()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_qt_tray(self) -> QSystemTrayIcon:
        """Create and return a QSystemTrayIcon (used on Linux / Windows)."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("[Tray] System tray not available on this desktop environment.")

        tray = QSystemTrayIcon(_make_mic_icon(), parent=self._parent)
        tray.setToolTip("Voice Input — GCP Speech-to-Text")

        menu = QMenu()
        show_action = menu.addAction("Show / Hide Settings")
        show_action.triggered.connect(self._on_toggle)
        menu.addSeparator()
        quit_action = menu.addAction("Quit Voice Input")
        quit_action.triggered.connect(self._on_quit)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _setup_macos_native_tray(self):
        """Create a native NSStatusItem in the macOS menu bar.

        QSystemTrayIcon has a timing issue with NSApplicationActivationPolicyAccessory
        that prevents the icon from appearing reliably.  Using AppKit directly
        sidesteps that entirely.
        """
        status_bar = _NSStatusBar.systemStatusBar()
        status_item = status_bar.statusItemWithLength_(_NSVariableStatusItemLength)

        status_item.button().setTitle_("🎙")
        status_item.button().setToolTip_("Voice Input — GCP Speech-to-Text")

        menu = _NSMenu.new()

        delegate = _MacOSMenuTarget.alloc().init()
        delegate._on_toggle = self._on_toggle
        delegate._on_quit = self._on_quit
        self._macos_menu_delegate = delegate  # keep a strong Python reference

        show_item = _NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Show / Hide Settings", "toggleWindow:", ""
        )
        show_item.setTarget_(delegate)
        menu.addItem_(show_item)

        menu.addItem_(_NSMenuItem.separatorItem())

        quit_item = _NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Voice Input", "quitApp:", ""
        )
        quit_item.setTarget_(delegate)
        menu.addItem_(quit_item)

        status_item.setMenu_(menu)
        self._macos_status_item = status_item
        self._macos_status_bar = status_bar  # keep reference so bar isn't GC'd

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason):
        """Toggle window visibility when the tray icon is clicked (Qt tray only)."""
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_toggle()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def notify_first_close(self):
        """Show a one-time balloon on the first window close (Qt tray only)."""
        if self._tray_notified:
            return
        self._tray_notified = True
        if (
            self._tray_icon is not None
            and self._tray_icon.supportsMessages()
        ):
            self._tray_icon.showMessage(
                "Voice Input",
                "Still running in the background — click the tray icon to reopen settings.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def cleanup(self):
        """Remove the macOS status item so it disappears immediately on exit."""
        if self._macos_status_item is not None and _NSStatusBar is not None:
            try:
                _NSStatusBar.systemStatusBar().removeStatusItem_(self._macos_status_item)
            except Exception:
                pass
