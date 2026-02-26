"""
PyQt6 main window: tabbed settings UI for Voice Input.

Settings tab  — API key, hotkey, language selection.
Advanced tab  — boost words and AI post-processing prompt.
"""

from __future__ import annotations

import json
import platform

from PyQt6.QtCore import Qt, QSize, QRect, QSettings, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.hotkey import HotkeyCombo, HotkeyListener, key_to_str, _MODIFIER_MAP
from ui.tray import TrayManager


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
    ("Swedish", "Svenska – Sverige", "sv-SE"),
    ("Hindi", "हिन्दी – भारत", "hi-IN"),
]


class _TwoLineDelegate(QStyledItemDelegate):
    """
    Combo-box item delegate that draws a bold title on the first line
    and a smaller grey description on the second.
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
    cancel_requested = pyqtSignal()       # escape pressed

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Input — GCP Speech-to-Text")
        self.setFixedWidth(620)

        # Hotkey listener
        self._hotkey_listener = HotkeyListener()
        self._current_combo: HotkeyCombo | None = None
        self._capturing_hotkey = False
        self._capture_modifiers: set[str] = set()

        # Central widget with tab container
        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        tabs = QTabWidget()
        outer_layout.addWidget(tabs)

        # ── Settings tab ───────────────────────────────────────────────
        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        settings_layout.setSpacing(12)
        settings_layout.setContentsMargins(12, 12, 12, 12)

        # Google Cloud API Key group
        creds_group = QGroupBox("Google Cloud API Key")
        creds_layout = QVBoxLayout(creds_group)
        creds_layout.addWidget(QLabel(
            "Required for Speech-to-Text and Gemini post-processing.\n"
            "Create a key at console.cloud.google.com → APIs & Services → Credentials."
        ))

        key_row = QHBoxLayout()
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.api_key_input.setPlaceholderText("Paste your Google Cloud API key here…")
        key_row.addWidget(self.api_key_input)

        self._show_key_cb = QCheckBox("Show")
        self._show_key_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._show_key_cb.toggled.connect(self._on_show_key_toggled)
        key_row.addWidget(self._show_key_cb)
        creds_layout.addLayout(key_row)
        settings_layout.addWidget(creds_group)

        # Language group
        lang_group = QGroupBox("Settings")
        lang_group_layout = QVBoxLayout(lang_group)
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
        lang_group_layout.addLayout(lang_row)
        settings_layout.addWidget(lang_group)

        # Hotkey group
        hotkey_group = QGroupBox("Hotkey (Push-to-Talk)")
        hotkey_layout = QHBoxLayout(hotkey_group)
        self.hotkey_label = QLabel(str(DEFAULT_HOTKEY))
        self.hotkey_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        hotkey_layout.addWidget(self.hotkey_label)
        self.hotkey_btn = QPushButton("Set Hotkey")
        self.hotkey_btn.clicked.connect(self._start_hotkey_capture)
        hotkey_layout.addWidget(self.hotkey_btn)
        settings_layout.addWidget(hotkey_group)

        settings_layout.addStretch()
        tabs.addTab(settings_widget, "Settings")

        # ── Advanced tab ───────────────────────────────────────────────
        advanced_widget = QWidget()
        advanced_layout = QVBoxLayout(advanced_widget)
        advanced_layout.setSpacing(12)
        advanced_layout.setContentsMargins(12, 12, 12, 12)

        # Recording mode group
        rec_mode_group = QGroupBox("Recording Mode")
        rec_mode_layout = QVBoxLayout(rec_mode_group)
        self._rec_mode_group = QButtonGroup(self)
        self.radio_push_to_talk = QRadioButton("Push to talk — hold to record, release to stop")
        self.radio_tap = QRadioButton("Press once to start, press again to stop")
        self.radio_push_to_talk.setChecked(True)
        self._rec_mode_group.addButton(self.radio_push_to_talk, 0)
        self._rec_mode_group.addButton(self.radio_tap, 1)
        rec_mode_layout.addWidget(self.radio_push_to_talk)
        rec_mode_layout.addWidget(self.radio_tap)
        self._rec_mode_group.idToggled.connect(lambda _id, checked: self._save_settings() if checked else None)
        advanced_layout.addWidget(rec_mode_group)

        # Boost Words group
        boost_group = QGroupBox("Boost Words")
        boost_layout = QVBoxLayout(boost_group)
        boost_layout.addWidget(QLabel(
            "Comma-separated words/phrases to boost in speech recognition (e.g. proper nouns, jargon)."
        ))
        boost_row = QHBoxLayout()
        self.boost_words_input = QLineEdit()
        self.boost_words_input.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.boost_words_input.setPlaceholderText("e.g.  TensorFlow, Kubernetes, gRPC")
        boost_row.addWidget(self.boost_words_input)

        boost_row.addWidget(QLabel("Boost:"))
        self.boost_value_spin = QDoubleSpinBox()
        self.boost_value_spin.setRange(0.0, 20.0)
        self.boost_value_spin.setSingleStep(0.5)
        self.boost_value_spin.setValue(10.0)
        self.boost_value_spin.setDecimals(1)
        self.boost_value_spin.setFixedWidth(68)
        self.boost_value_spin.setToolTip(
            "How strongly to bias the recogniser toward the listed words (0 – 20)."
        )
        boost_row.addWidget(self.boost_value_spin)

        self.boost_update_btn = QPushButton("Update")
        self.boost_update_btn.setFixedWidth(80)
        self.boost_update_btn.clicked.connect(self._on_boost_update)
        boost_row.addWidget(self.boost_update_btn)
        boost_layout.addLayout(boost_row)
        advanced_layout.addWidget(boost_group)

        # Internal list of active boost words (populated by _on_boost_update)
        self._boost_words: list[str] = []

        # Word Replacements group
        replacements_group = QGroupBox("Word Replacements")
        replacements_layout = QVBoxLayout(replacements_group)
        replacements_layout.addWidget(QLabel(
            "After transcription, these exact phrases are replaced before pasting."
        ))

        self.replacements_table = QTableWidget(0, 2)
        self.replacements_table.setHorizontalHeaderLabels(["Find", "Replace With"])
        self.replacements_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.replacements_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.replacements_table.verticalHeader().setVisible(False)
        self.replacements_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.replacements_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked |
            QTableWidget.EditTrigger.AnyKeyPressed
        )
        self.replacements_table.setMinimumHeight(140)
        self.replacements_table.itemChanged.connect(self._save_settings)
        replacements_layout.addWidget(self.replacements_table)

        repl_btn_row = QHBoxLayout()
        repl_btn_row.addStretch()
        self.repl_new_btn = QPushButton("New")
        self.repl_new_btn.setFixedWidth(72)
        self.repl_new_btn.clicked.connect(self._on_replacement_new)
        self.repl_delete_btn = QPushButton("Delete")
        self.repl_delete_btn.setFixedWidth(72)
        self.repl_delete_btn.clicked.connect(self._on_replacement_delete)
        repl_btn_row.addWidget(self.repl_new_btn)
        repl_btn_row.addWidget(self.repl_delete_btn)
        replacements_layout.addLayout(repl_btn_row)
        advanced_layout.addWidget(replacements_group)

        # AI Post-processing group
        postproc_group = QGroupBox("AI Post Transcription Editing")
        postproc_layout = QVBoxLayout(postproc_group)
        self.postproc_prompt = QPlainTextEdit()
        self.postproc_prompt.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.postproc_prompt.setPlaceholderText(
            "e.g.  Fix grammar and repetition, and keep the original words as much as possible."
        )
        self.postproc_prompt.setMinimumHeight(240)
        postproc_layout.addWidget(self.postproc_prompt)
        advanced_layout.addWidget(postproc_group)

        advanced_layout.addStretch()

        advanced_scroll = QScrollArea()
        advanced_scroll.setWidget(advanced_widget)
        advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        tabs.addTab(advanced_scroll, "Advanced")


        # ── Hotkey listener signals ────────────────────────────────────
        self._hotkey_listener.signals.hotkey_pressed.connect(self._on_hotkey_pressed)
        self._hotkey_listener.signals.hotkey_released.connect(self._on_hotkey_released)
        self._hotkey_listener.signals.toggle_settings_requested.connect(self._toggle_window)
        self._hotkey_listener.signals.cancel_requested.connect(self._on_cancel_requested)
        self._hotkey_listener.signals.key_event.connect(self._on_capture_key_event)

        # ── Restore saved settings ─────────────────────────────────────
        self._settings = QSettings()
        self._restore_settings()

        # Start the global listener with the (possibly restored) hotkey
        self._hotkey_listener.set_hotkey(self._current_combo)
        self._hotkey_listener.start()

        # ── Auto-save on change ────────────────────────────────────────
        self.api_key_input.textChanged.connect(self._save_settings)
        self.language_combo.currentIndexChanged.connect(self._save_settings)
        self.postproc_prompt.textChanged.connect(self._save_settings)

        # Start with no editor focus so typing doesn't land in the prompt box.
        QTimer.singleShot(0, self._clear_initial_focus)
        self.setMaximumHeight(680)

        # ── System tray / menu-bar icon ────────────────────────────────
        self._tray = TrayManager(
            parent_widget=self,
            on_toggle=self._toggle_window,
            on_quit=self._quit_app,
        )

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def show_window(self):
        """Show the main window and bring it to the foreground."""
        if platform.system() == "Darwin":
            try:
                from AppKit import NSApplication
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except ImportError:
                pass
        self.show()
        self.raise_()
        self.activateWindow()

    def _toggle_window(self):
        """Show the main window if hidden; hide it if visible."""
        if self.isVisible():
            self.hide()
        else:
            self.show_window()

    def _quit_app(self):
        """Save settings, stop the hotkey listener, and exit cleanly."""
        self._save_settings()
        self._hotkey_listener.stop()
        self._tray.cleanup()
        QApplication.instance().quit()

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
    # Public accessors (used by AppController)
    # ------------------------------------------------------------------

    def get_api_key(self) -> str:
        """Return the Google Cloud API key (stripped)."""
        return self.api_key_input.text().strip()

    def get_language_code(self) -> str:
        return self.language_combo.currentData()

    def get_postproc_prompt(self) -> str:
        """Return the post-processing prompt (empty string means disabled)."""
        return self.postproc_prompt.toPlainText().strip()

    def get_boost_words(self) -> list[str]:
        """Return the current list of active boost words/phrases."""
        return list(self._boost_words)

    def get_boost_value(self) -> float:
        """Return the current boost strength."""
        return self.boost_value_spin.value()

    def get_tap_to_record(self) -> bool:
        """Return True if tap-to-record mode is enabled."""
        return self.radio_tap.isChecked()

    def get_replacements(self) -> list[tuple[str, str]]:
        """Return the list of (find, replace) word-replacement pairs.

        Rows where either cell is empty are skipped.
        """
        pairs = []
        for row in range(self.replacements_table.rowCount()):
            find_item = self.replacements_table.item(row, 0)
            repl_item = self.replacements_table.item(row, 1)
            find = find_item.text().strip() if find_item else ""
            repl = repl_item.text().strip() if repl_item else ""
            if find and repl:
                pairs.append((find, repl))
        return pairs

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def set_status_idle(self):
        pass

    def set_status_recording(self):
        pass

    def set_status_transcribing(self):
        pass

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
    # Hotkey press / release (forwarded as signals to the controller)
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _on_hotkey_pressed(self):
        self.recording_requested.emit()

    @pyqtSlot()
    def _on_hotkey_released(self):
        self.recording_stopped.emit()

    @pyqtSlot()
    def _on_cancel_requested(self):
        self.cancel_requested.emit()

    @pyqtSlot()
    def _on_replacement_new(self):
        """Add a blank row, save immediately, then start editing the Find cell."""
        self.replacements_table.blockSignals(True)
        row = self.replacements_table.rowCount()
        self.replacements_table.insertRow(row)
        self.replacements_table.setItem(row, 0, QTableWidgetItem(""))
        self.replacements_table.setItem(row, 1, QTableWidgetItem(""))
        self.replacements_table.blockSignals(False)
        self._save_settings()
        print("saving")
        self.replacements_table.setCurrentCell(row, 0)
        self.replacements_table.editItem(self.replacements_table.item(row, 0))

    def _on_replacement_delete(self):
        """Delete all selected rows."""
        rows = sorted(
            {idx.row() for idx in self.replacements_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self.replacements_table.removeRow(row)
        self._save_settings()

    def _on_boost_update(self):
        """Parse the boost-words input and update the active word list."""
        raw = self.boost_words_input.text()
        words = [w.strip() for w in raw.split(",") if w.strip()]
        boost = self.boost_value_spin.value()
        self._boost_words = words
        self._save_settings()
        # Brief visual confirmation on the button
        self.boost_update_btn.setText("✓")
        QTimer.singleShot(1200, lambda: self.boost_update_btn.setText("Update"))
        if words:
            print(
                f"[BoostWords] Injected {len(words)} phrase(s) into Cloud Speech-to-Text "
                f"(boost={boost}): {words}"
            )
        else:
            print("[BoostWords] Cleared — no boost phrases will be sent to the API.")

    @pyqtSlot(bool)
    def _on_show_key_toggled(self, checked: bool):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.api_key_input.setEchoMode(mode)

    def _clear_initial_focus(self):
        focused = QApplication.focusWidget()
        if focused is not None:
            focused.clearFocus()


    # ------------------------------------------------------------------
    # Settings persistence (QSettings — macOS plist / Windows registry)
    # ------------------------------------------------------------------

    def _restore_settings(self):
        """Load saved settings or apply defaults."""
        # Block every widget that has an auto-save signal connected, so that
        # restoring one value cannot trigger _save_settings and overwrite the
        # not-yet-restored values (e.g. the radio button fires idToggled which
        # would save an empty replacements table before it is populated).
        self._rec_mode_group.blockSignals(True)
        self.api_key_input.blockSignals(True)
        self.language_combo.blockSignals(True)
        self.postproc_prompt.blockSignals(True)
        self.replacements_table.blockSignals(True)

        try:
            self._restore_settings_inner()
        finally:
            self._rec_mode_group.blockSignals(False)
            self.api_key_input.blockSignals(False)
            self.language_combo.blockSignals(False)
            self.postproc_prompt.blockSignals(False)
            self.replacements_table.blockSignals(False)

    def _restore_settings_inner(self):
        saved_key = self._settings.value("api_key", "")
        if saved_key:
            self.api_key_input.setText(saved_key)

        saved_lang = self._settings.value("language", None)
        if saved_lang is not None:
            for i in range(self.language_combo.count()):
                if self.language_combo.itemData(i) == saved_lang:
                    self.language_combo.setCurrentIndex(i)
                    break

        saved_prompt = self._settings.value("postproc_prompt", "")
        self.postproc_prompt.setPlainText(saved_prompt or "")

        saved_boost = self._settings.value("boost_words", "")
        if saved_boost:
            self.boost_words_input.setText(saved_boost)
            self._boost_words = [w.strip() for w in saved_boost.split(",") if w.strip()]

        saved_boost_value = self._settings.value("boost_value", None)
        if saved_boost_value is not None:
            try:
                self.boost_value_spin.setValue(float(saved_boost_value))
            except (ValueError, TypeError):
                pass

        saved_modifiers = self._settings.value("hotkey/modifiers", None)
        saved_main_key = self._settings.value("hotkey/main_key", None)
        if saved_main_key is not None:
            mods = set(saved_modifiers) if saved_modifiers else set()
            combo = HotkeyCombo(modifiers=mods, main_key=saved_main_key)
        else:
            combo = DEFAULT_HOTKEY
        self._current_combo = combo
        self.hotkey_label.setText(str(combo))

        tap_to_record = self._settings.value("tap_to_record", False)
        if tap_to_record in (True, "true"):
            self.radio_tap.setChecked(True)
        else:
            self.radio_push_to_talk.setChecked(True)

        try:
            pairs = json.loads(self._settings.value("replacements", "[]") or "[]")
        except (ValueError, TypeError):
            pairs = []
        for find, repl in pairs:
            row = self.replacements_table.rowCount()
            self.replacements_table.insertRow(row)
            self.replacements_table.setItem(row, 0, QTableWidgetItem(str(find)))
            self.replacements_table.setItem(row, 1, QTableWidgetItem(str(repl)))

    def _save_settings(self):
        """Persist current settings."""
        self._settings.setValue("api_key", self.api_key_input.text().strip())
        self._settings.setValue("language", self.language_combo.currentData())
        self._settings.setValue("postproc_prompt", self.postproc_prompt.toPlainText())
        self._settings.setValue("boost_words", self.boost_words_input.text())
        self._settings.setValue("boost_value", self.boost_value_spin.value())
        self._settings.setValue("tap_to_record", self.radio_tap.isChecked())
        print(f"saving replacements: {self.get_replacements()}")
        self._settings.setValue("replacements", json.dumps(self.get_replacements()))
        self._settings.sync()
        if self._current_combo is not None:
            self._settings.setValue("hotkey/modifiers", list(self._current_combo.modifiers))
            self._settings.setValue("hotkey/main_key", self._current_combo.main_key)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        """Hide to the system tray instead of quitting."""
        event.ignore()
        self._save_settings()
        self.hide()
        self._tray.notify_first_close()
