"""
Overlay widgets for the voice input application.

TranscriptOverlay — floating dark box near the cursor that displays the
                    live streaming transcript as it arrives.
RecordingBubble   — small dark bubble with a pulsing red circle (🎙)
SpinnerBubble     — same bubble with a spinning arc (⏳)
"""

from __future__ import annotations

import platform

from PyQt6.QtCore import Qt, QTimer, QPoint, QRectF
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QCursor, QBrush, QConicalGradient,
    QFont, QFontMetrics, QTextOption,
)
from PyQt6.QtWidgets import QWidget, QApplication

_IS_MACOS = platform.system() == "Darwin"

# On macOS, use Cocoa to snapshot and re-activate the previously focused
# app after showing an overlay, so our main window never steals focus.
_ns_workspace = None
if _IS_MACOS:
    try:
        from AppKit import NSWorkspace as _NSWorkspace
        _ns_workspace = _NSWorkspace.sharedWorkspace
    except ImportError:
        pass


def _get_frontmost_app():
    """Return the currently frontmost application (macOS only, else None)."""
    if _ns_workspace is not None:
        return _ns_workspace().frontmostApplication()
    return None


def _reactivate_app(app):
    """Re-activate *app* so our Qt app doesn't stay frontmost (macOS)."""
    if app is not None:
        app.activateWithOptions_(0)


# Shared constants
_SIZE = 32           # bubble diameter
_OFFSET = QPoint(20, 20)  # offset from cursor so the bubble doesn't sit on top


class _BaseBubble(QWidget):
    """
    Base class: frameless, always-on-top, translucent dark bubble
    that follows the mouse cursor.

    Must float above ALL apps without stealing focus or bringing
    the main window forward.  On macOS the Tool window type causes
    application activation on show(); we counter that by immediately
    re-focusing the previously active app via Cocoa.
    """

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool            # keeps it out of the taskbar
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # clicks pass through

        # Keep Tool windows visible even when our app is not frontmost.
        if _IS_MACOS:
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)

        self.setFixedSize(_SIZE, _SIZE)

        # Timer to follow the cursor
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(30)
        self._follow_timer.timeout.connect(self._follow_cursor)

    def show_at_cursor(self):
        """Show the bubble and start following the cursor."""
        self._follow_cursor()

        # Snapshot whichever app currently owns focus (e.g. Chrome)
        prev = _get_frontmost_app()
        self.show()
        # Give focus right back so our main window never comes forward
        _reactivate_app(prev)

        self._follow_timer.start()

    def dismiss(self):
        """Hide the bubble and stop the follow timer."""
        self._follow_timer.stop()
        self.hide()

    def _follow_cursor(self):
        pos = QCursor.pos() + _OFFSET
        self.move(pos)


class RecordingBubble(_BaseBubble):
    """
    A small dark bubble with a volume-reactive red dot — indicates that
    the microphone is actively recording.  The red circle grows when
    the user speaks louder and shrinks to a small dot during silence.
    """

    _MIN_RADIUS = 3      # radius when silent
    _MAX_RADIUS = 13     # radius at full volume
    _SMOOTHING_RISE = 0.45   # fast attack
    _SMOOTHING_FALL = 0.12   # slow decay
    # dB range mapped to [0, 1]
    _DB_FLOOR = -60.0
    _DB_CEIL = -10.0

    def __init__(self):
        super().__init__()
        self._volume_t = 0.0       # smoothed volume 0..1
        self._raw_t = 0.0          # latest raw volume 0..1

        # Repaint timer (animation frame rate)
        self._paint_timer = QTimer(self)
        self._paint_timer.setInterval(30)
        self._paint_timer.timeout.connect(self._tick)

    def show_at_cursor(self):
        self._volume_t = 0.0
        self._raw_t = 0.0
        self._paint_timer.start()
        super().show_at_cursor()

    def dismiss(self):
        self._paint_timer.stop()
        super().dismiss()

    def set_volume(self, rms_db: float):
        """Set the current volume level (called from Qt main thread)."""
        clamped = max(self._DB_FLOOR, min(self._DB_CEIL, rms_db))
        self._raw_t = (clamped - self._DB_FLOOR) / (self._DB_CEIL - self._DB_FLOOR)

    def _tick(self):
        # Smooth towards the raw value
        alpha = self._SMOOTHING_RISE if self._raw_t >= self._volume_t else self._SMOOTHING_FALL
        self._volume_t += alpha * (self._raw_t - self._volume_t)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dark semi-transparent background circle
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(30, 30, 30, 200))
        painter.drawEllipse(0, 0, _SIZE, _SIZE)

        # Volume-reactive red circle in the centre
        t = self._volume_t
        radius = int(self._MIN_RADIUS + (self._MAX_RADIUS - self._MIN_RADIUS) * t)
        alpha = int(160 + 95 * t)
        cx, cy = _SIZE // 2, _SIZE // 2
        painter.setBrush(QColor(220, 50, 50, alpha))
        painter.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

        painter.end()


class SpinnerBubble(_BaseBubble):
    """
    A small dark bubble with a spinning arc — indicates that
    transcription is in progress.
    """

    def __init__(self):
        super().__init__()
        self._angle = 0

        # Spin animation timer
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(30)
        self._spin_timer.timeout.connect(self._tick_spin)

    def show_at_cursor(self):
        self._angle = 0
        self._spin_timer.start()
        super().show_at_cursor()

    def dismiss(self):
        self._spin_timer.stop()
        super().dismiss()

    def _tick_spin(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dark semi-transparent background circle
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(30, 30, 30, 200))
        painter.drawEllipse(0, 0, _SIZE, _SIZE)

        # Spinning arc
        pen = QPen(QColor(200, 200, 200, 230), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        margin = 7
        rect = self.rect().adjusted(margin, margin, -margin, -margin)
        # drawArc expects 1/16th of a degree
        start_angle = self._angle * 16
        span_angle = 270 * 16
        painter.drawArc(rect, start_angle, span_angle)

        painter.end()


# -----------------------------------------------------------------------
# Transcript overlay — shows live streaming transcript near the cursor
# -----------------------------------------------------------------------

_OVERLAY_PADDING = 12
_OVERLAY_MAX_WIDTH = 420
_OVERLAY_CORNER_RADIUS = 10
_OVERLAY_OFFSET = QPoint(24, 24)   # offset from cursor


class TranscriptOverlay(QWidget):
    """
    A floating dark rounded-rect box that displays the live transcript.

    Call ``show_at_cursor()`` when recording starts, ``set_text()`` to
    update the displayed transcript in real time, and ``dismiss()`` to
    hide.  The overlay auto-sizes to fit the text and stays near the
    mouse cursor.
    """

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        if _IS_MACOS:
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)

        self._text = ""
        self._font = QFont("SF Pro Text" if _IS_MACOS else "Segoe UI", 14)
        self._font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self._metrics = QFontMetrics(self._font)

        # Cursor-follow timer
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(30)
        self._follow_timer.timeout.connect(self._follow_cursor)

        # Start with a minimum size
        self._update_size()

    # -- public API -------------------------------------------------------

    def show_at_cursor(self):
        """Show the overlay near the cursor and start following it."""
        self._follow_cursor()
        prev = _get_frontmost_app()
        self.show()
        _reactivate_app(prev)
        self._follow_timer.start()

    def dismiss(self):
        """Hide the overlay and stop following the cursor."""
        self._follow_timer.stop()
        self.hide()
        self._text = ""
        self._update_size()

    def set_text(self, text: str):
        """Update the displayed transcript text."""
        self._text = text
        self._update_size()
        self.update()

    # -- internals --------------------------------------------------------

    def _follow_cursor(self):
        pos = QCursor.pos() + _OVERLAY_OFFSET

        # Keep the overlay on screen
        screen = QApplication.screenAt(QCursor.pos())
        if screen is not None:
            geo = screen.availableGeometry()
            if pos.x() + self.width() > geo.right():
                pos.setX(geo.right() - self.width())
            if pos.y() + self.height() > geo.bottom():
                pos.setY(QCursor.pos().y() - _OVERLAY_OFFSET.y() - self.height())
            if pos.x() < geo.left():
                pos.setX(geo.left())
            if pos.y() < geo.top():
                pos.setY(geo.top())

        self.move(pos)

    def _update_size(self):
        """Recalculate widget size to fit the current text."""
        p = _OVERLAY_PADDING
        if not self._text:
            # Small pill when no text yet (shows a blinking cursor / placeholder)
            self.setFixedSize(p * 2 + 60, p * 2 + self._metrics.height())
            return

        max_text_w = _OVERLAY_MAX_WIDTH - 2 * p
        bounding = self._metrics.boundingRect(
            0, 0, max_text_w, 10000,
            Qt.TextFlag.TextWordWrap,
            self._text,
        )
        w = min(bounding.width() + 2 * p + 4, _OVERLAY_MAX_WIDTH)
        h = bounding.height() + 2 * p + 4
        self.setFixedSize(max(w, 80), max(h, p * 2 + self._metrics.height()))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dark rounded-rect background
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(25, 25, 25, 220))
        painter.drawRoundedRect(
            self.rect(), _OVERLAY_CORNER_RADIUS, _OVERLAY_CORNER_RADIUS,
        )

        # Text
        painter.setFont(self._font)
        painter.setPen(QColor(255, 255, 255, 240))

        p = _OVERLAY_PADDING
        text_rect = QRectF(p, p, self.width() - 2 * p, self.height() - 2 * p)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        option.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        if self._text:
            painter.drawText(text_rect, self._text, option)
        else:
            painter.setPen(QColor(180, 180, 180, 160))
            painter.drawText(text_rect, "Listening…", option)

        painter.end()
