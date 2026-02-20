"""
PyQt6 main window: language selection, hotkey configuration,
post-transcription editing, and status bar.
"""

from __future__ import annotations

import platform

from PyQt6.QtCore import Qt, QSize, QRect, QSettings, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from hotkey import HotkeyCombo, HotkeyListener, key_to_str, _MODIFIER_MAP


# ── macOS native status-bar support (AppKit / PyObjC) ─────────────────────────
# We bypass QSystemTrayIcon on macOS because it has a known timing issue with
# NSApplicationActivationPolicyAccessory that prevents the icon from appearing.
# AppKit (pyobjc-framework-Cocoa) is already a dependency of this project.
_APPKIT_AVAILABLE = False
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
        """Objective-C action target that forwards menu clicks to the Python window."""

        def init(self):
            self = _objc.super(_MacOSMenuTarget, self).init()
            if self is None:
                return None
            self._vi_window = None
            return self

        def toggleWindow_(self, sender):   # noqa: N802
            if self._vi_window is not None:
                self._vi_window._toggle_window()

        def quitApp_(self, sender):        # noqa: N802
            if self._vi_window is not None:
                self._vi_window._quit_app()

    _APPKIT_AVAILABLE = True
except Exception:
    pass
# ──────────────────────────────────────────────────────────────────────────────


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
    cancel_requested = pyqtSignal()       # escape pressed

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

        # --- Google Cloud API Key group (top — required before anything else) ---
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
        layout.addWidget(creds_group)

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

        # --- Boost Words group ---
        boost_group = QGroupBox("Boost Words")
        boost_layout = QVBoxLayout(boost_group)
        boost_layout.addWidget(QLabel(
            "Comma-separated words/phrases to boost in speech recognition (e.g. proper nouns, jargon)."
        ))
        boost_row = QHBoxLayout()
        self.boost_words_input = QLineEdit()
        self.boost_words_input.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.boost_words_input.setPlaceholderText(
            "e.g.  TensorFlow, Kubernetes, gRPC"
        )
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
        layout.addWidget(boost_group)

        # Internal list of active boost words (populated by _on_boost_update)
        self._boost_words: list[str] = []

        # --- Post transcription editing group ---
        postproc_group = QGroupBox("Post Transcription Editing")
        postproc_layout = QVBoxLayout(postproc_group)
        postproc_layout.addWidget(QLabel(
            "If non-empty, the transcript is sent to Gemini with this prompt before pasting."
        ))
        self.postproc_prompt = QPlainTextEdit()
        self.postproc_prompt.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
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
        self._hotkey_listener.signals.hotkey_double_pressed.connect(self.show_window)
        self._hotkey_listener.signals.cancel_requested.connect(self._on_cancel_requested)
        self._hotkey_listener.signals.key_event.connect(self._on_capture_key_event)

        # --- Restore saved settings (or fall back to defaults) ---
        self._settings = QSettings()
        self._restore_settings()

        # Start the global listener with the (possibly restored) hotkey
        self._hotkey_listener.set_hotkey(self._current_combo)
        self._hotkey_listener.start()

        # --- Auto-save on change ---
        self.api_key_input.textChanged.connect(self._save_settings)
        self.language_combo.currentIndexChanged.connect(self._save_settings)
        self.postproc_prompt.textChanged.connect(self._save_settings)

        # Start with no editor focus so typing doesn't land in the prompt box.
        QTimer.singleShot(0, self._clear_initial_focus)

        # System tray / menu-bar icon — keeps the app alive when the window is hidden.
        self._tray_notified = False
        self._tray_icon: QSystemTrayIcon | None = None   # used on Linux
        self._macos_status_item = None                   # used on macOS
        self._macos_menu_delegate = None                 # strong ref to ObjC delegate
        if platform.system() == "Darwin" and _APPKIT_AVAILABLE:
            self._setup_macos_native_tray()
        else:
            self._tray_icon = self._setup_tray_icon()

    # ------------------------------------------------------------------
    # System tray
    # ------------------------------------------------------------------

    def _make_mic_icon(self) -> QIcon:
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

        # ── Capsule (rounded rect) ────────────────────────────────────
        cap_w, cap_h, cap_r = 16, 24, 8
        painter.drawRoundedRect(cx - cap_w // 2, 6, cap_w, cap_h, cap_r, cap_r)

        # ── Stand arc — U-shape embracing the bottom of the capsule ──
        painter.setBrush(Qt.BrushStyle.NoBrush)
        stand_r = 14
        arc_cy = 6 + cap_h  # y-centre of the arc = bottom edge of capsule = 30
        # Arc rect centred at (cx, arc_cy)
        painter.drawArc(
            cx - stand_r, arc_cy - stand_r,
            2 * stand_r, 2 * stand_r,
            0,           # start at 3-o'clock (right side)
            -180 * 16,   # clockwise 180° → left side, passing through the bottom
        )

        # ── Stem ──────────────────────────────────────────────────────
        stem_top = arc_cy + stand_r  # bottom of the arc circle
        stem_bot = 54
        painter.drawLine(cx, stem_top, cx, stem_bot)

        # ── Base ──────────────────────────────────────────────────────
        painter.drawLine(cx - 10, stem_bot, cx + 10, stem_bot)

        painter.end()
        return QIcon(pixmap)

    def _setup_tray_icon(self) -> QSystemTrayIcon:
        """Create and return a QSystemTrayIcon (used on Linux / Windows)."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("[Tray] System tray not available on this desktop environment.")

        tray = QSystemTrayIcon(self._make_mic_icon(), parent=self)
        tray.setToolTip("Voice Input — GCP Speech-to-Text")

        menu = QMenu()
        show_action = menu.addAction("Show / Hide Settings")
        show_action.triggered.connect(self._toggle_window)
        menu.addSeparator()
        quit_action = menu.addAction("Quit Voice Input")
        quit_action.triggered.connect(self._quit_app)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _setup_macos_native_tray(self):
        """Create a native NSStatusItem in the macOS menu bar.

        QSystemTrayIcon has a timing issue with NSApplicationActivationPolicyAccessory
        that prevents the Qt-managed NSStatusItem from appearing reliably.  Using
        AppKit directly sidesteps that entirely.
        """
        status_bar = _NSStatusBar.systemStatusBar()
        status_item = status_bar.statusItemWithLength_(_NSVariableStatusItemLength)

        # Use the mic SF-symbol-style emoji as the button title.
        # (Setting an NSImage from the Qt pixmap is possible but adds complexity.)
        status_item.button().setTitle_("🎙")
        status_item.button().setToolTip_("Voice Input — GCP Speech-to-Text")

        # Build the drop-down menu.
        menu = _NSMenu.new()

        delegate = _MacOSMenuTarget.alloc().init()
        delegate._vi_window = self
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
        self._macos_status_bar = status_bar   # keep reference so bar isn't GC'd

    def show_window(self):
        """Show the main window and bring it to the foreground."""
        # On macOS the process may be an Accessory agent (no Dock icon) and
        # won't be considered the "active" app by the window server.  We must
        # explicitly activate it so the window actually lands in the foreground.
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

    @pyqtSlot(QSystemTrayIcon.ActivationReason)
    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason):
        """Toggle window visibility when the tray icon is clicked.

        On macOS, clicking the menu-bar icon always produces a ``Context``
        activation (the context menu pops up); ``Trigger`` is used on
        Windows/Linux for a plain left-click.  We handle both so the icon
        is tappable on every platform, while still letting the context menu
        appear normally.
        """
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_window()

    def _quit_app(self):
        """Save settings, stop the hotkey listener, and exit cleanly."""
        self._save_settings()
        self._hotkey_listener.stop()
        # Remove the native macOS status item so it disappears immediately on exit.
        if self._macos_status_item is not None:
            try:
                _NSStatusBar.systemStatusBar().removeStatusItem_(self._macos_status_item)
            except Exception:
                pass
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
    # Public accessors
    # ------------------------------------------------------------------

    def get_api_key(self) -> str:
        """Return the Google Cloud API key (stripped)."""
        return self.api_key_input.text().strip()

    @pyqtSlot(bool)
    def _on_show_key_toggled(self, checked: bool):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.api_key_input.setEchoMode(mode)

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

    @pyqtSlot()
    def _on_cancel_requested(self):
        self.cancel_requested.emit()

    @pyqtSlot()
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

    def _clear_initial_focus(self):
        focused = QApplication.focusWidget()
        if focused is not None:
            focused.clearFocus()

    # ------------------------------------------------------------------
    # Settings persistence (QSettings — macOS plist / Windows registry)
    # ------------------------------------------------------------------

    def _restore_settings(self):
        """Load saved settings or apply defaults."""
        # API key
        saved_key = self._settings.value("api_key", "")
        if saved_key:
            self.api_key_input.setText(saved_key)

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

        # Boost words
        saved_boost = self._settings.value("boost_words", "")
        if saved_boost:
            self.boost_words_input.setText(saved_boost)
            self._boost_words = [w.strip() for w in saved_boost.split(",") if w.strip()]

        # Boost value
        saved_boost_value = self._settings.value("boost_value", None)
        if saved_boost_value is not None:
            try:
                self.boost_value_spin.setValue(float(saved_boost_value))
            except (ValueError, TypeError):
                pass

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
        self._settings.setValue("api_key", self.api_key_input.text().strip())
        self._settings.setValue("language", self.language_combo.currentData())
        self._settings.setValue("postproc_prompt", self.postproc_prompt.toPlainText())
        self._settings.setValue("boost_words", self.boost_words_input.text())
        self._settings.setValue("boost_value", self.boost_value_spin.value())
        if self._current_combo is not None:
            self._settings.setValue("hotkey/modifiers", list(self._current_combo.modifiers))
            self._settings.setValue("hotkey/main_key", self._current_combo.main_key)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        """Hide to the system tray instead of quitting.

        The app only truly exits when the user selects "Quit Voice Input"
        from the tray context menu (which calls _quit_app).
        """
        event.ignore()
        self.hide()
        # Show a one-time balloon so the user knows where to find the app.
        # (Only available via QSystemTrayIcon on Linux; skip silently on macOS.)
        if not self._tray_notified:
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
