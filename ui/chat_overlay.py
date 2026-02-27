"""
Chat history overlay for the voice input application.

ChatHistoryOverlay  — transparent, stays-on-top window that accumulates
                      completed transcript messages as editable bubbles.

MessageItem         — a single bubble: dark rounded-rect with QTextEdit
                      (read-only by default) and a floating _ActionBar
                      sibling that hovers over the top-right corner.
                      The action bar is hidden during "processing" and
                      revealed when the message transitions to "done".

_ActionBar          — small horizontal strip (Insert ↵ · Copy ⧉ · Edit ✎)
                      that floats over its parent MessageItem.

Layout (bottom-anchored, transparent window)
--------------------------------------------

  ┌────────────────────────────────────────┐
  │  [gradient fade — topmost message]     │
  │  ┌──────────────────────────────────┐  │
  │  │ Older message 2 (gray text)      │  │
  │  │                     [↵][⧉][✎]  ←── action bar floats over top-right
  │  └──────────────────────────────────┘  │
  │  ┌──────────────────────────────────┐  │
  │  │ Latest message (white text)      │  │
  │  │                     [↵][⧉][✎]  │  │
  │  └──────────────────────────────────┘  │  ← bottom = anchor_pos
  └────────────────────────────────────────┘
"""

from __future__ import annotations

import platform
from typing import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_IS_MACOS = platform.system() == "Darwin"

_ns_workspace = None
if _IS_MACOS:
    try:
        from AppKit import NSWorkspace as _NSWorkspace
        _ns_workspace = _NSWorkspace.sharedWorkspace
    except ImportError:
        pass


def _reactivate_last_app(app):
    if app is not None:
        try:
            app.activateWithOptions_(0)
        except Exception:
            pass


# ── Visual constants ────────────────────────────────────────────────────────

_BUBBLE_PADDING     = 12          # inner text padding
_BUBBLE_RADIUS      = 10
_BUBBLE_SPACING     = 8           # vertical gap between bubbles
_BUBBLE_BG          = QColor(25, 25, 25, 220)

_BTN_SIZE           = 26          # icon button square
_BTN_RADIUS         = 6
_BTN_ICON_COLOR     = QColor(180, 180, 180, 200)
_BTN_HOVER_BG       = QColor(255, 255, 255, 28)

_ABAR_BG            = QColor(45, 45, 45, 245)   # action bar background
_ABAR_PAD           = 5           # inner padding of action bar
_ABAR_GAP           = 3           # spacing between buttons
_ABAR_RADIUS        = 8
_ABAR_W             = 3 * _BTN_SIZE + 2 * _ABAR_GAP + 2 * _ABAR_PAD
_ABAR_H             = _BTN_SIZE + 2 * _ABAR_PAD
_ABAR_MARGIN        = 6           # distance from bubble corner to action bar

_OVERLAY_MAX_TEXT_W = 420 - 2 * _BUBBLE_PADDING                         # 396 px — match TranscriptOverlay
_OVERLAY_W          = _OVERLAY_MAX_TEXT_W + 2 * _BUBBLE_PADDING         # 420 px

_HISTORY_VISIBLE    = 2.5         # how many prev bubbles to show

_SPIN_CHARS = ['⣾', '⣽', '⣻', '⢿', '⡿', '⣟', '⣯', '⣷']


# ── Icon button ─────────────────────────────────────────────────────────────

class _IconButton(QWidget):
    """Tiny square that paints a Unicode glyph with hover highlight."""

    clicked = pyqtSignal()

    def __init__(self, glyph: str, tooltip: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._glyph   = glyph
        self._hovered = False
        self.setFixedSize(_BTN_SIZE, _BTN_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def enterEvent(self, event):
        self._hovered = True
        self.update()

    def leaveEvent(self, event):
        self._hovered = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hovered:
            p.setBrush(_BTN_HOVER_BG)
            p.setPen(Qt.PenStyle.NoPen)
            path = QPainterPath()
            path.addRoundedRect(
                QRectF(1, 1, _BTN_SIZE - 2, _BTN_SIZE - 2),
                _BTN_RADIUS, _BTN_RADIUS,
            )
            p.drawPath(path)
        p.setPen(_BTN_ICON_COLOR)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._glyph)
        p.end()


# ── Floating action bar ──────────────────────────────────────────────────────

class _ActionBar(QWidget):
    """
    Small horizontal strip (Insert · Copy · Edit) that floats over the
    top-right corner of a MessageItem.  It is a child of MessageItem but
    NOT part of any layout — positioned via move() in resizeEvent.
    """

    insert_clicked = pyqtSignal()
    copy_clicked   = pyqtSignal()
    edit_clicked   = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(_ABAR_PAD, _ABAR_PAD, _ABAR_PAD, _ABAR_PAD)
        layout.setSpacing(_ABAR_GAP)

        self._btn_insert = _IconButton("↵", "Insert", self)
        self._btn_copy   = _IconButton("⧉", "Copy",   self)
        self._btn_edit   = _IconButton("✎", "Edit",   self)

        self._btn_insert.clicked.connect(self.insert_clicked)
        self._btn_copy.clicked.connect(self.copy_clicked)
        self._btn_edit.clicked.connect(self.edit_clicked)

        layout.addWidget(self._btn_insert)
        layout.addWidget(self._btn_copy)
        layout.addWidget(self._btn_edit)

        self.setFixedSize(_ABAR_W, _ABAR_H)

    def set_edit_active(self, active: bool):
        self._btn_edit.setToolTip("Done" if active else "Edit")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), _ABAR_RADIUS, _ABAR_RADIUS)
        p.fillPath(path, _ABAR_BG)
        p.end()


# ── Single message bubble ────────────────────────────────────────────────────

class MessageItem(QWidget):
    """
    One transcript bubble.

    States:
      "processing" — text + spinner, action bar hidden, semi-white text.
      "done"       — final text, action bar visible.

    Signals
    -------
    insert_requested(str)  — Insert button pressed; caller handles paste.
    dismiss_requested()    — Copy button pressed; overlay should dismiss.
    edit_started()         — Editing began; parent grays out other items.
    edit_ended()           — Editing ended; parent restores normal coloring.
    """

    insert_requested  = pyqtSignal(str)
    dismiss_requested = pyqtSignal()
    edit_started      = pyqtSignal()
    edit_ended        = pyqtSignal()
    activated         = pyqtSignal()
    content_resized   = pyqtSignal()

    # ── animated opacity property ────────────────────────────────────────────

    def _get_opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, value: float):
        self._opacity = value
        children_vis = value > 0.05
        self._text_edit.setVisible(children_vis)
        if self._state == "done":
            self._action_bar.setVisible(children_vis)
        self.update()

    bubble_opacity = pyqtProperty(float, fget=_get_opacity, fset=_set_opacity)

    # ── construction ─────────────────────────────────────────────────────────

    def __init__(self, text: str, is_latest: bool = True,
                 state: str = "done",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._opacity   = 1.0
        self._is_latest = is_latest
        self._editing   = False
        self._state     = state   # "processing" or "done"
        self._msg_id    = -1      # set by ChatHistoryOverlay
        self._spin_frame = 0

        font_name = "SF Pro Text" if _IS_MACOS else "Segoe UI"
        self._font = QFont(font_name, 14)
        self._font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)

        self._text_edit = QTextEdit(self)
        self._text_edit.setReadOnly(True)
        self._text_edit.setFrameShape(QTextEdit.Shape.NoFrame)
        self._text_edit.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text_edit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._text_edit.setFont(self._font)
        self._text_edit.setStyleSheet("background: transparent; border: none;")
        self._text_edit.document().contentsChanged.connect(self._on_text_changed)
        self._text_edit.focusInEvent  = self._text_focus_in
        self._text_edit.focusOutEvent = self._text_focus_out

        self._raw_text = text
        self._apply_display()

        self._action_bar = _ActionBar(self)
        self._action_bar.insert_clicked.connect(self._on_insert)
        self._action_bar.copy_clicked.connect(self._on_copy)
        self._action_bar.edit_clicked.connect(self._on_edit)
        self._action_bar.raise_()

        if state == "processing":
            self._action_bar.hide()

        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Minimum)
        self._update_height()

    # ── display helpers ──────────────────────────────────────────────────────

    def _apply_display(self):
        """Update text content and color based on state."""
        if self._state == "processing":
            spinner = _SPIN_CHARS[self._spin_frame]
            display = self._raw_text if self._raw_text else "…"
            self._text_edit.setPlainText(f"{display} {spinner}")
            color = "rgba(255,255,255,130)"
        else:
            self._text_edit.setPlainText(self._raw_text)
            color = self._resolve_color()
        self._text_edit.setStyleSheet(
            f"background: transparent; border: none; color: {color};"
        )

    def _resolve_color(self) -> str:
        if self._is_latest:
            return "rgba(255,255,255,240)"
        return "rgba(200,200,200,160)"

    def _apply_text_color(self):
        if self._state == "processing":
            color = "rgba(255,255,255,130)"
        else:
            color = self._resolve_color()
        self._text_edit.setStyleSheet(
            f"background: transparent; border: none; color: {color};"
        )

    def _update_height(self):
        doc = self._text_edit.document()
        tw = _OVERLAY_MAX_TEXT_W
        doc.setTextWidth(tw)
        doc_h = int(doc.size().height())
        new_h = max(doc_h + _BUBBLE_PADDING * 2, 44)
        if new_h != self.height():
            self.setFixedHeight(new_h)
            self.content_resized.emit()

    def _place_children(self):
        w, h = self.width(), self.height()
        self._text_edit.setGeometry(
            _BUBBLE_PADDING, _BUBBLE_PADDING,
            max(0, w - 2 * _BUBBLE_PADDING),
            max(0, h - 2 * _BUBBLE_PADDING),
        )
        bar = self._action_bar
        bar.move(
            w - bar.width() - _ABAR_MARGIN,
            _ABAR_MARGIN,
        )
        bar.raise_()

    # ── event overrides ──────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_children()
        self._update_height()

    def _on_text_changed(self):
        self._update_height()

    def mousePressEvent(self, event):
        self.activated.emit()
        super().mousePressEvent(event)

    def _text_focus_in(self, event):
        self.activated.emit()
        QTextEdit.focusInEvent(self._text_edit, event)

    def _text_focus_out(self, event):
        if self._editing and self._text_edit.isReadOnly():
            self._editing = False
            self._apply_text_color()
            self.edit_ended.emit()
        QTextEdit.focusOutEvent(self._text_edit, event)

    # ── button handlers ──────────────────────────────────────────────────────

    def _on_insert(self):
        self.activated.emit()
        self.insert_requested.emit(self._text_edit.toPlainText())

    def _on_copy(self):
        self.activated.emit()
        QApplication.clipboard().setText(self._text_edit.toPlainText())
        self.dismiss_requested.emit()

    def _on_edit(self):
        self.activated.emit()
        if self._text_edit.isReadOnly():
            self._text_edit.setReadOnly(False)
            self._editing = True
            self._apply_text_color()
            self._text_edit.setFocus()
            cursor = self._text_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self._text_edit.setTextCursor(cursor)
            self._action_bar.set_edit_active(True)
            self.edit_started.emit()
        else:
            self._text_edit.setReadOnly(True)
            self._editing = False
            self._apply_text_color()
            self._action_bar.set_edit_active(False)
            self.edit_ended.emit()

    # ── public API ───────────────────────────────────────────────────────────

    def set_is_latest(self, value: bool):
        self._is_latest = value
        self._apply_text_color()

    def text(self) -> str:
        return self._text_edit.toPlainText()

    def tick_spinner(self, frame: int):
        """Advance the spinner to *frame* (called by parent timer)."""
        if self._state != "processing":
            return
        self._spin_frame = frame
        spinner = _SPIN_CHARS[frame]
        display = self._raw_text if self._raw_text else "…"
        self._text_edit.blockSignals(True)
        self._text_edit.setPlainText(f"{display} {spinner}")
        self._text_edit.blockSignals(False)
        self.update()

    def complete(self, final_text: str):
        """Transition from processing to done."""
        self._state = "done"
        self._raw_text = final_text
        self._text_edit.blockSignals(True)
        self._text_edit.setPlainText(final_text)
        self._text_edit.blockSignals(False)
        self._apply_text_color()
        self._action_bar.show()
        self._place_children()
        self._update_height()

    # ── painting ─────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._opacity)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), _BUBBLE_RADIUS, _BUBBLE_RADIUS)
        p.fillPath(path, _BUBBLE_BG)
        p.end()


# ── Gradient mask ────────────────────────────────────────────────────────────

class _GradientMask(QWidget):
    """
    Painted over the top of the scroll area to fade out the partially-visible
    topmost message and signal there is more content above.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._fade_h = 0

    def set_fade_height(self, h: int):
        self._fade_h = h
        self.update()

    def paintEvent(self, event):
        if self._fade_h <= 0:
            return
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        grad = QLinearGradient(0, 0, 0, self._fade_h)
        grad.setColorAt(0.0, QColor(0, 0, 0, 255))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRect(0, 0, self.width(), self._fade_h), grad)
        p.end()


# ── Main overlay window ──────────────────────────────────────────────────────

class ChatHistoryOverlay(QWidget):
    """
    Transparent, bottom-anchored window that accumulates transcript bubbles.

    Public API
    ----------
    add_processing_message(text, anchor_rect, on_insert) -> int
        Append a processing bubble, re-anchor, return msg_id.
    complete_processing(msg_id, final_text)
        Transition a processing message to done; fade in history.
    hide_keep_state()
        Hide the window but keep all messages in memory.
    dismiss()
        Clear everything and hide.
    """

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if _IS_MACOS:
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)

        self._items: list[MessageItem] = []
        self._anchor = QPoint(0, 0)
        self._on_insert_cb: Callable[[str], None] | None = None
        self._animations: list[QPropertyAnimation] = []
        self._next_msg_id: int = 0

        # Spinner shared across all processing items
        self._spin_frame: int = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(80)
        self._spin_timer.timeout.connect(self._tick_spin)

        # Scroll area
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setStyleSheet(
            "* { background: transparent; border: none; }"
            "QScrollBar:vertical {"
            "  background: transparent; width: 5px; margin: 0;"
            "}"
            "QScrollBar::handle:vertical {"
            "  background: rgba(255,255,255,55);"
            "  border-radius: 2px; min-height: 20px;"
            "}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
            "  height: 0px;"
            "}"
        )

        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(_BUBBLE_SPACING)
        self._container_layout.addStretch()   # top stretch pushes bubbles to the bottom
        self._scroll.setWidget(self._container)

        # Gradient mask (mouse-transparent, drawn on top of scroll area)
        self._gradient = _GradientMask(self)
        self._gradient.raise_()
        self._target_fade_h = 0

        self._scroll.verticalScrollBar().valueChanged.connect(self._update_gradient)

        self.setFixedWidth(_OVERLAY_W)

    # ── spinner ──────────────────────────────────────────────────────────────

    def _tick_spin(self):
        self._spin_frame = (self._spin_frame + 1) % len(_SPIN_CHARS)
        for item in self._items:
            item.tick_spinner(self._spin_frame)

    def _ensure_spinner(self):
        has_processing = any(it._state == "processing" for it in self._items)
        if has_processing and not self._spin_timer.isActive():
            self._spin_timer.start()
        elif not has_processing and self._spin_timer.isActive():
            self._spin_timer.stop()

    # ── activation coordination ─────────────────────────────────────────────

    def _on_item_activated(self):
        """Highlight the clicked item, gray all others, end stale edits."""
        sender = self.sender()
        for item in self._items:
            if item is not sender and item._editing:
                item._text_edit.setReadOnly(True)
                item._editing = False
                item._action_bar.set_edit_active(False)
            item.set_is_latest(item is sender)

    def _on_item_resized(self):
        """Grow/shrink the overlay and scroll so the edited item stays visible."""
        sender = self.sender()
        self._reposition(history_visible=True)
        if sender is not None:
            QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(sender, 0, 0))

    # ── sizing helpers ───────────────────────────────────────────────────────

    def _avg_prev_height(self) -> int:
        if len(self._items) <= 1:
            return 80
        heights = [item.height() for item in self._items[:-1]]
        return int(sum(heights) / len(heights))

    def _desired_window_height(self, history_visible: bool = True) -> int:
        if not self._items:
            return 80
        latest_h = self._items[-1].height()
        if len(self._items) == 1 or not history_visible:
            return latest_h
        prev_h = int(_HISTORY_VISIBLE * self._avg_prev_height())
        return latest_h + prev_h + _BUBBLE_SPACING

    def _reposition(self, history_visible: bool = True):
        h = self._desired_window_height(history_visible)
        screen = QApplication.screenAt(self._anchor)
        if screen is not None:
            geo = screen.availableGeometry()
            h = min(h, max(80, self._anchor.y() - geo.top()))

        self.setFixedHeight(h)
        self._scroll.setGeometry(0, 0, _OVERLAY_W, h)
        self._gradient.setGeometry(0, 0, _OVERLAY_W, h)

        x, y = self._anchor.x(), self._anchor.y() - h
        if screen is not None:
            geo = screen.availableGeometry()
            x = max(geo.left(), min(x, geo.right() - _OVERLAY_W))
            y = max(geo.top(), y)

        self.move(x, y)

        done_count = sum(1 for it in self._items if it._state == "done")
        if done_count >= 3 and history_visible:
            self._target_fade_h = self._avg_prev_height() // 2
        else:
            self._target_fade_h = 0

        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _update_gradient(self):
        """Show the fade gradient only when content is scrolled above the viewport."""
        sb = self._scroll.verticalScrollBar()
        if self._target_fade_h <= 0 or sb.value() <= 0:
            self._gradient.set_fade_height(0)
            return
        progress = min(1.0, sb.value() / self._target_fade_h)
        self._gradient.set_fade_height(int(self._target_fade_h * progress))

    # ── fade-in animation ────────────────────────────────────────────────────

    def _fade_in_item(self, item: MessageItem):
        item.bubble_opacity = 0.0
        anim = QPropertyAnimation(item, b"bubble_opacity", self)
        anim.setDuration(320)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animations.append(anim)
        anim.finished.connect(
            lambda: self._animations.remove(anim) if anim in self._animations else None
        )
        anim.start()

    # ── public API ───────────────────────────────────────────────────────────

    def add_processing_message(
        self,
        text: str,
        anchor_rect: QRect,
        on_insert: Callable[[str], None] | None = None,
    ) -> int:
        """
        Append a processing bubble at the bottom and show the overlay.

        anchor_rect — screen rect of the locked TranscriptOverlay; the new
                      bubble is positioned at the exact same screen coords.
        Returns a msg_id for use with complete_processing().
        """
        if on_insert is not None:
            self._on_insert_cb = on_insert

        self._anchor = QPoint(anchor_rect.x(), anchor_rect.y() + anchor_rect.height())

        msg_id = self._next_msg_id
        self._next_msg_id += 1

        for existing in self._items:
            existing.set_is_latest(False)

        item = MessageItem(text, is_latest=True, state="processing")
        item._msg_id = msg_id
        item.activated.connect(self._on_item_activated)
        item.content_resized.connect(self._on_item_resized)
        item.insert_requested.connect(self._handle_insert)
        item.dismiss_requested.connect(self.dismiss)
        self._items.append(item)

        self._container_layout.addWidget(item)

        # Hide all previous items initially — they fade in after processing
        for existing in self._items[:-1]:
            existing.bubble_opacity = 0.0
            existing.setVisible(False)

        self._ensure_spinner()
        self._reposition(history_visible=False)

        if not self.isVisible():
            prev_app = None
            if _ns_workspace is not None:
                try:
                    prev_app = _ns_workspace().frontmostApplication()
                except Exception:
                    pass
            self.show()
            _reactivate_last_app(prev_app)

        return msg_id

    def complete_processing(self, msg_id: int, final_text: str):
        """
        Transition the message with *msg_id* from processing to done.
        If it is the latest message, show action bar and fade in history.
        """
        target: MessageItem | None = None
        for item in self._items:
            if item._msg_id == msg_id:
                target = item
                break
        if target is None:
            return

        target.complete(final_text)
        self._ensure_spinner()

        is_latest = (self._items and self._items[-1] is target)

        if is_latest:
            # Reveal and fade in all previous done items
            for existing in self._items[:-1]:
                if existing._state == "done":
                    existing.setVisible(True)
                    self._fade_in_item(existing)

            self._reposition(history_visible=True)

    def hide_keep_state(self):
        """Hide the window but keep all messages in memory."""
        for anim in self._animations:
            anim.stop()
        self._animations.clear()
        self._spin_timer.stop()
        self.hide()

    def cancel_processing(self):
        """Remove all processing bubbles but keep completed messages; then hide."""
        for anim in self._animations:
            anim.stop()
        self._animations.clear()
        self._spin_timer.stop()

        to_remove = [item for item in self._items if item._state == "processing"]
        for item in to_remove:
            self._items.remove(item)
            self._container_layout.removeWidget(item)
            item.deleteLater()

        if self._items:
            for item in self._items:
                item.set_is_latest(False)
            self._items[-1].set_is_latest(True)

        self.hide()

    def remove_message(self, msg_id: int):
        """Silently remove a message by ID (e.g. failed transcription)."""
        target = None
        for item in self._items:
            if item._msg_id == msg_id:
                target = item
                break
        if target is None:
            return

        self._items.remove(target)
        self._container_layout.removeWidget(target)
        target.deleteLater()
        self._ensure_spinner()

        if not self._items:
            self.hide()
            return

        for item in self._items:
            item.set_is_latest(False)
        self._items[-1].set_is_latest(True)

        for item in self._items:
            if item._state == "done":
                item.setVisible(True)
                item.bubble_opacity = 1.0

        self._reposition(history_visible=True)

    def dismiss(self):
        """Clear all bubbles and hide."""
        for anim in self._animations:
            anim.stop()
        self._animations.clear()
        self._spin_timer.stop()
        for item in self._items:
            self._container_layout.removeWidget(item)
            item.deleteLater()
        self._items.clear()
        self.hide()

    # ── insert handler ───────────────────────────────────────────────────────

    def _handle_insert(self, text: str):
        if self._on_insert_cb is not None:
            self._on_insert_cb(text)

    # ── transparent background ───────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.end()
