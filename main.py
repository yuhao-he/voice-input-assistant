"""
Voice Input Application — Entry Point

Wires together:
  hotkey press → start recording
  hotkey release → stop recording → trim silence → transcribe → print
"""

from __future__ import annotations

import sys
import threading

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication

from recorder import AudioRecorder, trim_silence
from transcriber import transcribe
from ui import MainWindow


class AppController(QObject):
    """
    Coordinates recording, trimming, and transcription.
    Runs the transcription pipeline in a background thread to avoid
    blocking the UI.
    """

    transcription_done = pyqtSignal(str)   # emitted when result is ready
    transcription_failed = pyqtSignal(str)  # emitted on error or silence

    def __init__(self, window: MainWindow):
        super().__init__()
        self.window = window
        self.recorder = AudioRecorder()

        # Connect window signals
        self.window.recording_requested.connect(self.on_start_recording)
        self.window.recording_stopped.connect(self.on_stop_recording)

        # Connect result signals back to UI updates
        self.transcription_done.connect(self._on_transcription_done)
        self.transcription_failed.connect(self._on_transcription_failed)

    @pyqtSlot()
    def on_start_recording(self):
        api_key = self.window.get_api_key()
        if not api_key:
            self.window._set_status("⚠️  No API key set — cannot record")
            return

        self.window.set_status_recording()
        self.recorder.start()

    @pyqtSlot()
    def on_stop_recording(self):
        audio = self.recorder.stop()
        if audio is None or len(audio) == 0:
            self.window.set_status_idle()
            return

        self.window.set_status_transcribing()

        # Run trim + transcription in a background thread
        threshold_db = self.window.get_threshold_db()
        api_key = self.window.get_api_key()
        language = self.window.get_language_code()

        thread = threading.Thread(
            target=self._transcribe_worker,
            args=(audio, threshold_db, api_key, language),
            daemon=True,
        )
        thread.start()

    def _transcribe_worker(self, audio, threshold_db, api_key, language):
        """Runs in a background thread."""
        # Trim silence
        trimmed = trim_silence(audio, threshold_db=threshold_db)
        if trimmed is None:
            self.transcription_failed.emit("Audio was entirely silence — skipped API call.")
            return

        duration_sec = len(trimmed) / 16000
        print(f"[Recorder] Trimmed audio: {duration_sec:.1f}s ({len(trimmed)} samples)")

        # Transcribe
        text = transcribe(
            audio=trimmed,
            api_key=api_key,
            language_code=language,
        )

        if text:
            self.transcription_done.emit(text)
        else:
            self.transcription_failed.emit("No transcription returned.")

    @pyqtSlot(str)
    def _on_transcription_done(self, text: str):
        print(f"\n>>> {text}\n")
        self.window.set_status_idle()

    @pyqtSlot(str)
    def _on_transcription_failed(self, msg: str):
        print(f"[Info] {msg}")
        self.window.set_status_idle()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Voice Input")

    window = MainWindow()
    controller = AppController(window)  # noqa: F841 — prevent GC

    window.show()
    window.set_status_idle()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

