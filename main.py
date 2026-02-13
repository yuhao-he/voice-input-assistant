"""
Voice Input Application — Entry Point

Wires together:
  hotkey press  → sound chirp + recording bubble + start recording
  hotkey release → sound chirp + spinner bubble + stop recording
                 → trim silence → transcribe → auto-paste → dismiss bubble

On transcription, the text is pasted into the currently focused input
via a clipboard-swap technique (save → set → paste keystroke → restore).
"""

from __future__ import annotations

import platform
import sys
import threading

from PyQt6.QtCore import QMimeData, QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication

from pynput.keyboard import Controller as KbController, Key

# Determine the correct modifier for paste (Cmd on macOS, Ctrl elsewhere)
_PASTE_MODIFIER = Key.cmd if platform.system() == "Darwin" else Key.ctrl

from recorder import AudioRecorder, trim_silence
from transcriber import transcribe
from postprocess import postprocess
from sounds import play_start, play_stop
from overlay import RecordingBubble, SpinnerBubble
from ui import MainWindow


class AppController(QObject):
    """
    Coordinates recording, trimming, and transcription.
    Runs the transcription pipeline in a background thread to avoid
    blocking the UI.
    """

    transcription_done = pyqtSignal(str)   # emitted when result is ready
    transcription_failed = pyqtSignal(str)  # emitted on error or silence
    volume_update = pyqtSignal(float)       # live volume dB from recording

    def __init__(self, window: MainWindow):
        super().__init__()
        self.window = window
        self.recorder = AudioRecorder(on_volume=self._on_volume_callback)

        self.__kb = None  # created lazily to avoid Quartz/Qt startup race

        # Overlay bubbles
        self._recording_bubble = RecordingBubble()
        self._spinner_bubble = SpinnerBubble()

        # Connect window signals
        self.window.recording_requested.connect(self.on_start_recording)
        self.window.recording_stopped.connect(self.on_stop_recording)

        # Connect result signals back to UI updates
        self.transcription_done.connect(self._on_transcription_done)
        self.transcription_failed.connect(self._on_transcription_failed)

        # Feed live recording volume to the bubble
        self.volume_update.connect(self._recording_bubble.set_volume)

    @property
    def _kb(self):
        """Lazily create the pynput keyboard controller on first use."""
        if self.__kb is None:
            self.__kb = KbController()
        return self.__kb

    def _on_volume_callback(self, rms_db: float):
        """Called from the audio thread — emit a Qt signal to cross threads safely."""
        self.volume_update.emit(rms_db)

    @pyqtSlot()
    def on_start_recording(self):
        play_start()
        self._recording_bubble.show_at_cursor()
        self.window.set_status_recording()
        self.recorder.start()

    @pyqtSlot()
    def on_stop_recording(self):
        audio = self.recorder.stop()
        self._recording_bubble.dismiss()

        play_stop()

        if audio is None or len(audio) == 0:
            self.window.set_status_idle()
            return

        self.window.set_status_transcribing()
        self._spinner_bubble.show_at_cursor()

        # Run trim + transcription in a background thread
        language = self.window.get_language_code()
        prompt = self.window.get_postproc_prompt()

        thread = threading.Thread(
            target=self._transcribe_worker,
            args=(audio, language, prompt),
            daemon=True,
        )
        thread.start()

    _SILENCE_THRESHOLD_DB = -55.0

    def _transcribe_worker(self, audio, language, prompt):
        """Runs in a background thread."""
        # Trim silence
        trimmed = trim_silence(audio, threshold_db=self._SILENCE_THRESHOLD_DB)
        if trimmed is None:
            self.transcription_failed.emit("Audio was entirely silence — skipped API call.")
            return

        duration_sec = len(trimmed) / 16000
        print(f"[Recorder] Trimmed audio: {duration_sec:.1f}s ({len(trimmed)} samples)")

        # Transcribe
        text = transcribe(
            audio=trimmed,
            language_code=language,
        )

        if not text:
            self.transcription_failed.emit("No transcription returned.")
            return

        # Post-process via Gemini if a prompt is configured
        if prompt:
            print(f"[Postprocess] Sending to Gemini… (Transcription: {text})")
            text = postprocess(text, prompt)
            print(f"[Postprocess] Result: {text}")

        self.transcription_done.emit(text)

    # ------------------------------------------------------------------
    # Clipboard-swap auto-paste
    # ------------------------------------------------------------------

    @pyqtSlot(str)
    def _on_transcription_done(self, text: str):
        print(f"\n>>> {text}\n")

        # Dismiss the spinner bubble
        self._spinner_bubble.dismiss()

        clipboard = QApplication.clipboard()

        # 1. Save current clipboard contents
        saved_mime = QMimeData()
        source_mime = clipboard.mimeData()
        if source_mime is not None:
            for fmt in source_mime.formats():
                saved_mime.setData(fmt, source_mime.data(fmt))

        # 2. Put transcription text into clipboard
        clipboard.setText(text)

        # 3. Schedule the paste keystroke via QTimer so the event loop
        #    can process the clipboard ownership change first.
        #    (Using time.sleep here would block the event loop and
        #    prevent Qt from serving clipboard data to the target app.)
        def _do_paste():
            self._kb.press(_PASTE_MODIFIER)
            self._kb.press("v")
            self._kb.release("v")
            self._kb.release(_PASTE_MODIFIER)

        QTimer.singleShot(80, _do_paste)

        # 4. Restore original clipboard after paste has had time to complete
        def _restore():
            clipboard.setMimeData(saved_mime)

        QTimer.singleShot(350, _restore)

        self.window.set_status_idle()

    @pyqtSlot(str)
    def _on_transcription_failed(self, msg: str):
        print(f"[Info] {msg}")
        self._spinner_bubble.dismiss()
        self.window.set_status_idle()


_SETUP_BANNER = """\
╔══════════════════════════════════════════════════════════════════╗
║  Voice Input — GCP Speech-to-Text v2                           ║
║                                                                ║
║  Setup (run once in your terminal):                            ║
║                                                                ║
║    1. Install the gcloud CLI                                   ║
║         https://cloud.google.com/sdk/docs/install              ║
║                                                                ║
║    2. Log in with Application Default Credentials              ║
║         gcloud auth application-default login                  ║
║                                                                ║
║    3. Set your default project                                 ║
║         gcloud config set project YOUR_PROJECT_ID              ║
║                                                                ║
║    4. Enable the Speech-to-Text API                            ║
║         gcloud services enable speech.googleapis.com           ║
╚══════════════════════════════════════════════════════════════════╝
"""


def main():
    print(_SETUP_BANNER)

    app = QApplication(sys.argv)
    app.setOrganizationName("VoiceInput")
    app.setApplicationName("Voice Input")

    window = MainWindow()
    controller = AppController(window)  # noqa: F841 — prevent GC

    window.show()
    window.set_status_idle()

    app.exec()


if __name__ == "__main__":
    main()
