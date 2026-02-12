"""
PyQt6 main window: credential inputs, hotkey configuration,
combined volume meter + silence threshold, and status bar.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QSize, QRect, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSlider,
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
    ("Chinese (Mandarin)", "普通话", "zh"),
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

# Default hotkey: Ctrl + '
DEFAULT_HOTKEY = HotkeyCombo(modifiers={"ctrl"}, main_key="'")

# Number of capsules in the level meter
NUM_CAPSULES = 20


class CapsuleMeter(QWidget):
    """
    A discrete-capsule volume level meter, modelled after an OS
    "Input level" indicator.

    Draws NUM_CAPSULES thin rounded rectangles side by side.
    - Capsules at or above the threshold and below the current level
      are lit green.
    - Capsules below the threshold are always grey (even if the level
      reaches them).
    - Unlit capsules are dark grey.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = 0              # 0..NUM_CAPSULES  (current volume)
        self._threshold_idx = 0      # 0..NUM_CAPSULES  (silence threshold)
        self._lit_color = QColor("#7a9a7c")    # muted grey-green
        self._below_color = QColor("#6e6e6e")  # grey for below-threshold lit capsules
        self._dim_color = QColor("#3a3a3a")    # dark grey unlit
        self.setMinimumHeight(20)
        self.setMaximumHeight(20)

    def set_level(self, count: int):
        """Set how many capsules should be lit (0..NUM_CAPSULES)."""
        count = max(0, min(NUM_CAPSULES, count))
        if count != self._level:
            self._level = count
            self.update()

    def set_threshold(self, idx: int):
        """Set the threshold capsule index (0..NUM_CAPSULES)."""
        idx = max(0, min(NUM_CAPSULES, idx))
        if idx != self._threshold_idx:
            self._threshold_idx = idx
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(300, 20)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(Qt.PenStyle.NoPen))

        w = self.width()
        h = self.height()
        gap = 4
        total_gaps = gap * (NUM_CAPSULES - 1)
        capsule_w = max(1, (w - total_gaps) / NUM_CAPSULES)
        radius = min(capsule_w / 2, h / 2, 3)

        for i in range(NUM_CAPSULES):
            x = i * (capsule_w + gap)
            if i < self._level:
                # Lit — green if at/above threshold, grey if below
                if i >= self._threshold_idx:
                    painter.setBrush(self._lit_color)
                else:
                    painter.setBrush(self._below_color)
            else:
                painter.setBrush(self._dim_color)
            painter.drawRoundedRect(int(x), 0, int(capsule_w), h, radius, radius)

        painter.end()


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

        # --- Volume group (capsule meter + silence threshold) ---
        volume_group = QGroupBox("Volume")
        volume_layout = QVBoxLayout(volume_group)

        # Shared label width so the meter and slider left-align
        _label_w = 115

        # Capsule input-level meter
        meter_row = QHBoxLayout()
        meter_label = QLabel("Input Level")
        meter_label.setFixedWidth(_label_w)
        meter_row.addWidget(meter_label)
        self.capsule_meter = CapsuleMeter()
        meter_row.addWidget(self.capsule_meter)
        volume_layout.addLayout(meter_row)

        # Silence threshold slider
        threshold_row = QHBoxLayout()
        threshold_label = QLabel("Silence Threshold")
        threshold_label.setFixedWidth(_label_w)
        threshold_row.addWidget(threshold_label)
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setMinimum(-60)
        self.threshold_slider.setMaximum(-10)
        self.threshold_slider.setValue(-50)
        self.threshold_slider.setTickInterval(5)
        self.threshold_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.threshold_slider.valueChanged.connect(self._update_threshold_label)
        threshold_row.addWidget(self.threshold_slider)
        self.threshold_value_label = QLabel("-50 dB")
        self.threshold_value_label.setFixedWidth(70)
        self.threshold_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        threshold_row.addWidget(self.threshold_value_label)
        volume_layout.addLayout(threshold_row)

        layout.addWidget(volume_group)

        # --- Status bar ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._set_status("Idle")

        # --- Connect hotkey listener signals ---
        self._hotkey_listener.signals.hotkey_pressed.connect(self._on_hotkey_pressed)
        self._hotkey_listener.signals.hotkey_released.connect(self._on_hotkey_released)
        self._hotkey_listener.signals.key_event.connect(self._on_capture_key_event)

        # Apply default hotkey and start the global listener
        self._current_combo = DEFAULT_HOTKEY
        self._hotkey_listener.set_hotkey(DEFAULT_HOTKEY)
        self._hotkey_listener.start()

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

    def get_threshold_db(self) -> float:
        return float(self.threshold_slider.value())

    # ------------------------------------------------------------------
    # Live volume meter
    # ------------------------------------------------------------------

    _SMOOTHING_RISE = 0.35   # EMA factor when level is rising (fast attack)
    _SMOOTHING_FALL = 0.08   # EMA factor when level is falling (slow decay)

    @pyqtSlot(float)
    def update_volume(self, rms_db: float):
        """Update the capsule meter with the current dB level (smoothed)."""
        # Map dB range [-80, 0] → capsule count [0, NUM_CAPSULES]
        clamped = max(-80.0, min(0.0, rms_db))
        raw = (clamped + 80.0) / 80.0 * NUM_CAPSULES

        # Exponential moving average: fast attack, slow decay
        prev = getattr(self, "_smooth_level", 0.0)
        alpha = self._SMOOTHING_RISE if raw >= prev else self._SMOOTHING_FALL
        smoothed = prev + alpha * (raw - prev)
        self._smooth_level = smoothed

        self.capsule_meter.set_level(int(round(smoothed)))

        # Map threshold slider dB → capsule index
        threshold_db = float(self.threshold_slider.value())
        t_clamped = max(-80.0, min(0.0, threshold_db))
        t_idx = int((t_clamped + 80.0) / 80.0 * NUM_CAPSULES)
        self.capsule_meter.set_threshold(t_idx)


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
    # Threshold slider
    # ------------------------------------------------------------------

    @pyqtSlot(int)
    def _update_threshold_label(self, value: int):
        self.threshold_value_label.setText(f"{value} dB")

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
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self._hotkey_listener.stop()
        super().closeEvent(event)
