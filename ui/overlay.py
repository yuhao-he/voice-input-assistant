"""
Floating transcript overlay for the voice input application.

TranscriptOverlay — floating dark box near the cursor that displays the
                    live streaming transcript as it arrives, supporting
                    multiple concurrent segments.
"""

from __future__ import annotations

import html
import platform

from PyQt6.QtCore import Qt, QTimer, QPoint, QRectF
from PyQt6.QtGui import (
    QColor, QPainter, QCursor,
    QFont, QFontMetrics, QTextOption, QTextDocument,
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


# -----------------------------------------------------------------------
# Transcript overlay — shows live streaming transcript near the cursor
# -----------------------------------------------------------------------

_OVERLAY_PADDING = 12
_OVERLAY_MAX_WIDTH = 420
_OVERLAY_CORNER_RADIUS = 10
_OVERLAY_OFFSET = QPoint(24, 24)   # offset from cursor

# Braille spinner frames cycled by the spin timer
_SPIN_CHARS = ['⣾', '⣽', '⣻', '⢿', '⡿', '⣟', '⣯', '⣷']


class TranscriptOverlay(QWidget):
    """
    A floating dark rounded-rect box that displays the live transcript,
    supporting multiple concurrent segments:

    - **active** segment  — the one currently being transcribed (bright white).
    - **processing** segment — sent to Gemini, awaiting result (semi-white +
      spinning braille char appended inline).

    Public API
    ----------
    show_at_cursor()
        Append a new empty *active* segment; show the widget if hidden.
    set_text(text)
        Update the last *active* segment's text.
    freeze_active_segment() -> int
        Mark the last active segment as *processing* (semi-white + spinner).
        Returns the segment's unique id so the caller can later call
        ``complete_segment(seg_id)``.
    complete_segment(seg_id)
        Remove the segment with *seg_id* from the list.  Auto-hides when
        the list becomes empty.
    dismiss()
        Unconditionally clear all segments and hide.
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

        # Segment list — each entry: {"id": int, "text": str, "state": str}
        # state is "active" or "processing"
        self._segments: list[dict] = []
        self._next_id: int = 0

        # Shared spinner animation state
        self._spin_frame: int = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(80)
        self._spin_timer.timeout.connect(self._tick_spin)

        font_name = "SF Pro Text" if _IS_MACOS else "Segoe UI"
        self._font = QFont(font_name, 14)
        self._font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self._metrics = QFontMetrics(self._font)

        # Cursor-follow timer
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(30)
        self._follow_timer.timeout.connect(self._follow_cursor)

        self._update_size()

    # -- public API -------------------------------------------------------

    def show_error_at_cursor(self, msg: str, duration_ms: int = 4000):
        """Show a transient error message near the cursor in light red.

        The message auto-dismisses after *duration_ms* milliseconds.
        """
        seg_id = self._next_id
        self._segments.append({"id": seg_id, "text": msg, "state": "error"})
        self._next_id += 1

        if not self.isVisible():
            self._follow_cursor()
            prev = _get_frontmost_app()
            self.show()
            _reactivate_app(prev)
            self._follow_timer.start()

        self._update_size()
        self.update()

        # Auto-dismiss this error segment after the given delay.
        QTimer.singleShot(duration_ms, lambda: self.complete_segment(seg_id))

    def show_at_cursor(self):
        """Append a new active segment; show + start following the cursor."""
        self._segments.append({"id": self._next_id, "text": "", "state": "active"})
        self._next_id += 1

        if not self.isVisible():
            self._follow_cursor()
            prev = _get_frontmost_app()
            self.show()
            _reactivate_app(prev)
            self._follow_timer.start()

        self._update_size()
        self.update()

    def set_text(self, text: str):
        """Update the last active segment's text (called with live interim transcript)."""
        if self._segments and self._segments[-1]["state"] == "active":
            self._segments[-1]["text"] = text
        self._update_size()
        self.update()

    def freeze_active_segment(self) -> int:
        """
        Mark the last active segment as *processing* (semi-white + spinner).
        Starts the spinner animation if not already running.
        Returns the segment id.
        """
        seg_id = -1
        for seg in reversed(self._segments):
            if seg["state"] == "active":
                seg["state"] = "processing"
                seg_id = seg["id"]
                break

        if not self._spin_timer.isActive():
            self._spin_timer.start()

        self._update_size()
        self.update()
        return seg_id

    def complete_segment(self, seg_id: int):
        """
        Remove the segment with *seg_id* from the display.
        Auto-hides (and stops timers) when no segments remain.
        """
        self._segments = [s for s in self._segments if s["id"] != seg_id]

        # Stop spinner if nothing left to spin
        has_processing = any(s["state"] == "processing" for s in self._segments)
        if not has_processing:
            self._spin_timer.stop()

        if not self._segments:
            self._hide_all()
        else:
            self._update_size()
            self.update()

    def dismiss(self):
        """Unconditionally clear all segments and hide."""
        self._segments.clear()
        self._hide_all()

    # -- internals --------------------------------------------------------

    def _hide_all(self):
        self._spin_timer.stop()
        self._follow_timer.stop()
        self.hide()
        self._update_size()

    def _tick_spin(self):
        self._spin_frame = (self._spin_frame + 1) % len(_SPIN_CHARS)
        self.update()

    def _follow_cursor(self):
        pos = QCursor.pos() + _OVERLAY_OFFSET

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

    def _build_html(self) -> str:
        """
        Build an HTML string representing all segments.

        - Processing segments: semi-white text + spinner char.
        - Active segment: bright white text (or placeholder if empty).
        """
        spinner = _SPIN_CHARS[self._spin_frame]
        parts: list[str] = []

        for seg in self._segments:
            escaped = html.escape(seg["text"])
            if seg["state"] == "error":
                # Light red — signals a configuration / API error
                parts.append(
                    f'<span style="color:rgba(255,110,110,240);">'
                    f'{escaped}'
                    f'</span>'
                )
            elif seg["state"] == "processing":
                # Semi-white + spinner appended inline
                text_part = escaped if escaped else "…"
                parts.append(
                    f'<span style="color:rgba(255,255,255,130);">'
                    f'{text_part}&nbsp;{spinner}'
                    f'</span>'
                )
            else:
                # Active — bright white
                if escaped:
                    parts.append(
                        f'<span style="color:rgba(255,255,255,240);">'
                        f'{escaped}'
                        f'</span>'
                    )
                else:
                    # Placeholder when nothing transcribed yet
                    parts.append(
                        f'<span style="color:rgba(180,180,180,160);">Listening…</span>'
                    )

        # Join segments with a visible separator space
        return '<span style="color:rgba(255,255,255,80);">&nbsp; </span>'.join(parts)

    def _make_doc(self, max_text_w: float) -> QTextDocument:
        """Create a QTextDocument sized to *max_text_w* with current HTML."""
        doc = QTextDocument()
        doc.setDefaultFont(self._font)
        doc.setTextWidth(max_text_w)
        doc.setHtml(self._build_html())
        return doc

    def _update_size(self):
        """Recalculate widget size to fit the current segments."""
        p = _OVERLAY_PADDING
        if not self._segments:
            self.setFixedSize(p * 2 + 60, p * 2 + self._metrics.height())
            return

        max_text_w = _OVERLAY_MAX_WIDTH - 2 * p
        doc = self._make_doc(max_text_w)
        doc_size = doc.size()
        w = min(int(doc_size.width()) + 2 * p + 4, _OVERLAY_MAX_WIDTH)
        h = int(doc_size.height()) + 2 * p + 4
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

        p = _OVERLAY_PADDING
        max_text_w = self.width() - 2 * p

        if self._segments:
            doc = self._make_doc(max_text_w)
            painter.translate(p, p)
            doc.drawContents(painter)
        else:
            painter.setFont(self._font)
            painter.setPen(QColor(180, 180, 180, 160))
            text_rect = QRectF(p, p, max_text_w, self.height() - 2 * p)
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.WordWrap)
            painter.drawText(text_rect, "Listening…", option)

        painter.end()
