"""
PyQt6 main window: credential inputs, hotkey configuration,
silence threshold slider, and status bar.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
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
    QVBoxLayout,
    QWidget,
)

from hotkey import HotkeyCombo, HotkeyListener, key_to_str, _MODIFIER_MAP


# Common language codes for the dropdown
LANGUAGES = [
    ("English (US)", "en-US"),
    ("English (UK)", "en-GB"),
    ("Chinese (Mandarin)", "zh"),
    ("Spanish", "es-ES"),
    ("French", "fr-FR"),
    ("German", "de-DE"),
    ("Japanese", "ja-JP"),
    ("Korean", "ko-KR"),
    ("Portuguese (BR)", "pt-BR"),
    ("Hindi", "hi-IN"),
]


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

        # --- Credentials group ---
        creds_group = QGroupBox("GCP Credentials")
        creds_layout = QVBoxLayout(creds_group)

        # API Key
        api_key_row = QHBoxLayout()
        api_key_row.addWidget(QLabel("API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("Enter your Google Cloud API key")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        api_key_row.addWidget(self.api_key_input)
        self.api_key_toggle = QPushButton("Show")
        self.api_key_toggle.setFixedWidth(60)
        self.api_key_toggle.setCheckable(True)
        self.api_key_toggle.toggled.connect(self._toggle_api_key_visibility)
        api_key_row.addWidget(self.api_key_toggle)
        creds_layout.addLayout(api_key_row)

        # Project ID
        project_row = QHBoxLayout()
        project_row.addWidget(QLabel("Project ID:"))
        self.project_id_input = QLineEdit()
        self.project_id_input.setPlaceholderText("(optional) GCP project ID")
        project_row.addWidget(self.project_id_input)
        creds_layout.addLayout(project_row)

        # Language
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Language:"))
        self.language_combo = QComboBox()
        for display, code in LANGUAGES:
            self.language_combo.addItem(f"{display} ({code})", code)
        lang_row.addWidget(self.language_combo)
        creds_layout.addLayout(lang_row)

        layout.addWidget(creds_group)

        # --- Hotkey group ---
        hotkey_group = QGroupBox("Hotkey (Push-to-Talk)")
        hotkey_layout = QHBoxLayout(hotkey_group)

        self.hotkey_label = QLabel("None")
        self.hotkey_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        hotkey_layout.addWidget(self.hotkey_label)

        self.hotkey_btn = QPushButton("Set Hotkey")
        self.hotkey_btn.clicked.connect(self._start_hotkey_capture)
        hotkey_layout.addWidget(self.hotkey_btn)

        layout.addWidget(hotkey_group)

        # --- Silence threshold group ---
        threshold_group = QGroupBox("Silence Threshold")
        threshold_layout = QHBoxLayout(threshold_group)

        threshold_layout.addWidget(QLabel("Sensitive"))
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setMinimum(-60)
        self.threshold_slider.setMaximum(-10)
        self.threshold_slider.setValue(-30)
        self.threshold_slider.setTickInterval(5)
        self.threshold_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.threshold_slider.valueChanged.connect(self._update_threshold_label)
        threshold_layout.addWidget(self.threshold_slider)
        threshold_layout.addWidget(QLabel("Aggressive"))

        self.threshold_value_label = QLabel("-30 dB")
        self.threshold_value_label.setFixedWidth(60)
        threshold_layout.addWidget(self.threshold_value_label)

        layout.addWidget(threshold_group)

        # --- Status bar ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._set_status("Idle")

        # --- Connect hotkey listener signals ---
        self._hotkey_listener.signals.hotkey_pressed.connect(self._on_hotkey_pressed)
        self._hotkey_listener.signals.hotkey_released.connect(self._on_hotkey_released)
        self._hotkey_listener.signals.key_event.connect(self._on_capture_key_event)

        # Start the global listener
        self._hotkey_listener.start()

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_api_key(self) -> str:
        return self.api_key_input.text().strip()

    def get_project_id(self) -> str:
        return self.project_id_input.text().strip()

    def get_language_code(self) -> str:
        return self.language_combo.currentData()

    def get_threshold_db(self) -> float:
        return float(self.threshold_slider.value())

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
    # API key visibility toggle
    # ------------------------------------------------------------------

    @pyqtSlot(bool)
    def _toggle_api_key_visibility(self, checked: bool):
        if checked:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.api_key_toggle.setText("Hide")
        else:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.api_key_toggle.setText("Show")

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

