"""
PyQt6 main window: language selection, hotkey configuration,
post-transcription editing, and status bar.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QSize, QRect, QSettings, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from hotkey import HotkeyCombo, HotkeyListener, key_to_str, _MODIFIER_MAP


# Common language codes for the dropdown: (display_name, description, code)
LANGUAGES = [
    ("English (US)", "General American English", "en-US"),
    ("English (UK)", "British English", "en-GB"),
    ("Chinese (Mandarin)", "普通话 – 简体", "cmn-Hans-CN"),
    ("Spanish", "Español – España", "es-ES"),
    ("French", "Français – France", "fr-FR"),
    ("German", "Deutsch – Deutschland", "de-DE"),
    ("Japanese", "日本語", "ja-JP"),
    ("Korean", "한국어", "ko-KR"),
    ("Portuguese (BR)", "Português – Brasil", "pt-BR"),
    ("Hindi", "हिन्दी – भारत", "hi-IN"),
]


class _TwoLineDelegate(QStyledItemDelegate):
    """
    Combo-box item delegate that draws a bold title on the first line
    and a smaller grey description on the second, like the Cursor
    privacy-mode dropdown.
    """

    _PADDING = 6
    _LINE_SPACING = 2
    _DESC_SCALE = 0.85

    @staticmethod
    def _make_smaller_font(base: QFont, scale: float) -> QFont:
        """Return a copy of *base* scaled down, handling both point and pixel sizes."""
        font = QFont(base)
        pt = base.pointSizeF()
        if pt > 0:
            font.setPointSizeF(pt * scale)
        else:
            px = base.pixelSize()
            font.setPixelSize(max(1, int(px * scale)))
        return font

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        self.initStyleOption(option, index)

        # Draw hover / selection background as light grey instead of system blue
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        from PyQt6.QtWidgets import QStyle
        if option.state & QStyle.StateFlag.State_Selected or option.state & QStyle.StateFlag.State_MouseOver:
            painter.setBrush(QColor("#444444"))
            painter.setPen(QPen(Qt.PenStyle.NoPen))
            bg_rect = option.rect.adjusted(2, 1, -2, -1)
            painter.drawRoundedRect(bg_rect, 4, 4)
        painter.restore()

        rect: QRect = option.rect.adjusted(self._PADDING, self._PADDING,
                                            -self._PADDING, -self._PADDING)

        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        description = index.data(Qt.ItemDataRole.UserRole + 1) or ""

        # Title (bold)
        title_font = QFont(option.font)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(option.palette.color(option.palette.ColorRole.Text))
        title_rect = QRect(rect.x(), rect.y(), rect.width(), painter.fontMetrics().height())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)

        # Description (smaller, grey)
        desc_font = self._make_smaller_font(option.font, self._DESC_SCALE)
        painter.setFont(desc_font)
        painter.setPen(QColor("#999999"))
        desc_y = title_rect.bottom() + self._LINE_SPACING
        desc_rect = QRect(rect.x(), desc_y, rect.width(), painter.fontMetrics().height())
        painter.drawText(desc_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, description)

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        self.initStyleOption(option, index)
        title_font = QFont(option.font)
        title_font.setBold(True)
        desc_font = self._make_smaller_font(option.font, self._DESC_SCALE)

        from PyQt6.QtGui import QFontMetrics
        title_h = QFontMetrics(title_font).height()
        desc_h = QFontMetrics(desc_font).height()
        total = title_h + self._LINE_SPACING + desc_h + self._PADDING * 2
        return QSize(option.rect.width(), total)

# Default hotkey: F3
DEFAULT_HOTKEY = HotkeyCombo(modifiers=set(), main_key="f3")


class MainWindow(QMainWindow):
    """Application main window."""

    # Signals emitted to the controller
    recording_requested = pyqtSignal()    # hotkey pressed
    recording_stopped = pyqtSignal()      # hotkey released

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Input — GCP Speech-to-Text")
        self.setMinimumWidth(480)

        # Hotkey listener
        self._hotkey_listener = HotkeyListener()
        self._current_combo: HotkeyCombo | None = None
        self._capturing_hotkey = False
        self._capture_modifiers: set[str] = set()

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(12)

        # --- Settings group ---
        settings_group = QGroupBox("Settings")
        settings_layout = QVBoxLayout(settings_group)

        # Language (two-line delegate: bold title + grey description)
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Language:"))
        self.language_combo = QComboBox()
        self.language_combo.setItemDelegate(_TwoLineDelegate(self.language_combo))
        self.language_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #555;
                border-radius: 10px;
                padding: 4px 14px;
                background: transparent;
                color: #ccc;
                font-size: 13px;
            }
            QComboBox:hover {
                border-color: #888;
            }
            QComboBox::drop-down {
                border: none;
                width: 0px;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
            QComboBox QAbstractItemView {
                background: #2a2a2a;
                border: 1px solid #555;
                border-radius: 6px;
                padding: 4px;
                selection-background-color: #444;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                border-radius: 4px;
                padding: 2px;
            }
            QComboBox QAbstractItemView::item:hover {
                background: #444;
            }
        """)
        for display, description, code in LANGUAGES:
            self.language_combo.addItem(display, code)
            idx = self.language_combo.count() - 1
            self.language_combo.setItemData(idx, description, Qt.ItemDataRole.UserRole + 1)
        lang_row.addWidget(self.language_combo)
        settings_layout.addLayout(lang_row)

        layout.addWidget(settings_group)

        # --- Hotkey group ---
        hotkey_group = QGroupBox("Hotkey (Push-to-Talk)")
        hotkey_layout = QHBoxLayout(hotkey_group)

        self.hotkey_label = QLabel(str(DEFAULT_HOTKEY))
        self.hotkey_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        hotkey_layout.addWidget(self.hotkey_label)

        self.hotkey_btn = QPushButton("Set Hotkey")
        self.hotkey_btn.clicked.connect(self._start_hotkey_capture)
        hotkey_layout.addWidget(self.hotkey_btn)

        layout.addWidget(hotkey_group)

        # --- Post transcription editing group ---
        postproc_group = QGroupBox("Post Transcription Editing")
        postproc_layout = QVBoxLayout(postproc_group)
        postproc_layout.addWidget(QLabel(
            "If non-empty, the transcript is sent to Gemini with this prompt before pasting."
        ))
        self.postproc_prompt = QPlainTextEdit()
        self.postproc_prompt.setPlaceholderText(
            "e.g.  Fix grammar and punctuation, keep the original meaning."
        )
        self.postproc_prompt.setMaximumHeight(80)
        postproc_layout.addWidget(self.postproc_prompt)
        layout.addWidget(postproc_group)

        # --- Status bar ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._set_status("Idle")

        # --- Connect hotkey listener signals ---
        self._hotkey_listener.signals.hotkey_pressed.connect(self._on_hotkey_pressed)
        self._hotkey_listener.signals.hotkey_released.connect(self._on_hotkey_released)
        self._hotkey_listener.signals.key_event.connect(self._on_capture_key_event)

        # --- Restore saved settings (or fall back to defaults) ---
        self._settings = QSettings()
        self._restore_settings()

        # Start the global listener with the (possibly restored) hotkey
        self._hotkey_listener.set_hotkey(self._current_combo)
        self._hotkey_listener.start()

        # --- Auto-save on change ---
        self.language_combo.currentIndexChanged.connect(self._save_settings)
        self.postproc_prompt.textChanged.connect(self._save_settings)

    # ------------------------------------------------------------------
    # Focus: click anywhere outside a text field to clear focus
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        """Clear focus from text inputs when clicking elsewhere."""
        focused = QApplication.focusWidget()
        if isinstance(focused, QLineEdit):
            focused.clearFocus()
        super().mousePressEvent(event)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_language_code(self) -> str:
        return self.language_combo.currentData()

    def get_postproc_prompt(self) -> str:
        """Return the post-processing prompt (empty string means disabled)."""
        return self.postproc_prompt.toPlainText().strip()

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _set_status(self, text: str):
        self.status_bar.showMessage(text)

    def set_status_idle(self):
        self._set_status("Idle — press hotkey to record")

    def set_status_recording(self):
        self._set_status("🎙️  Recording…")

    def set_status_transcribing(self):
        self._set_status("⏳  Transcribing…")

    # ------------------------------------------------------------------
    # Hotkey capture
    # ------------------------------------------------------------------

    def _start_hotkey_capture(self):
        """Enter hotkey capture mode."""
        self._capturing_hotkey = True
        self._capture_modifiers = set()
        self.hotkey_btn.setText("Press keys…")
        self.hotkey_btn.setEnabled(False)
        self.hotkey_label.setText("Listening…")
        self._hotkey_listener.set_capture_mode(True)

    @pyqtSlot(object, bool)
    def _on_capture_key_event(self, key, is_press: bool):
        """Handle key events during hotkey capture."""
        if not self._capturing_hotkey:
            return

        if is_press:
            if key in _MODIFIER_MAP:
                self._capture_modifiers.add(_MODIFIER_MAP[key])
            else:
                # Non-modifier key pressed — finalize the combo
                main_key = key_to_str(key)
                combo = HotkeyCombo(
                    modifiers=set(self._capture_modifiers),
                    main_key=main_key,
                )
                self._finish_hotkey_capture(combo)

    def _finish_hotkey_capture(self, combo: HotkeyCombo):
        """Finish capturing and apply the new hotkey."""
        self._capturing_hotkey = False
        self._hotkey_listener.set_capture_mode(False)
        self._current_combo = combo
        self._hotkey_listener.set_hotkey(combo)
        self.hotkey_label.setText(str(combo))
        self.hotkey_btn.setText("Set Hotkey")
        self.hotkey_btn.setEnabled(True)
        self._save_settings()

    # ------------------------------------------------------------------
    # Hotkey press / release (forwarded as signals)
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _on_hotkey_pressed(self):
        self.recording_requested.emit()

    @pyqtSlot()
    def _on_hotkey_released(self):
        self.recording_stopped.emit()

    # ------------------------------------------------------------------
    # Settings persistence (QSettings — macOS plist / Windows registry)
    # ------------------------------------------------------------------

    def _restore_settings(self):
        """Load saved settings or apply defaults."""
        # Language
        saved_lang = self._settings.value("language", None)
        if saved_lang is not None:
            for i in range(self.language_combo.count()):
                if self.language_combo.itemData(i) == saved_lang:
                    self.language_combo.setCurrentIndex(i)
                    break

        # Post-processing prompt
        saved_prompt = self._settings.value("postproc_prompt", "")
        self.postproc_prompt.setPlainText(saved_prompt or "")

        # Hotkey
        saved_modifiers = self._settings.value("hotkey/modifiers", None)
        saved_main_key = self._settings.value("hotkey/main_key", None)
        if saved_main_key is not None:
            mods = set(saved_modifiers) if saved_modifiers else set()
            combo = HotkeyCombo(modifiers=mods, main_key=saved_main_key)
        else:
            combo = DEFAULT_HOTKEY
        self._current_combo = combo
        self.hotkey_label.setText(str(combo))

    def _save_settings(self):
        """Persist current settings."""
        self._settings.setValue("language", self.language_combo.currentData())
        self._settings.setValue("postproc_prompt", self.postproc_prompt.toPlainText())
        if self._current_combo is not None:
            self._settings.setValue("hotkey/modifiers", list(self._current_combo.modifiers))
            self._settings.setValue("hotkey/main_key", self._current_combo.main_key)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self._save_settings()
        self._hotkey_listener.stop()
        super().closeEvent(event)
