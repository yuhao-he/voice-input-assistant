"""
AppController — wires together recording, transcription, and auto-paste.

  hotkey press  → sound chirp + transcript overlay + start streaming
  hotkey release → sound chirp + finish streaming → post-process → auto-paste

While the hotkey is held, audio is streamed to the Speech-to-Text API
and the live transcript is displayed in a floating overlay near the cursor.

On transcription, the text is pasted into the currently focused input
via a clipboard-swap technique (save → set → paste keystroke → restore).
"""

from __future__ import annotations

import os
import platform
import threading

from PyQt6.QtCore import QMimeData, QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication

from pynput.keyboard import Controller as KbController, Key

_PASTE_MODIFIER = Key.cmd if platform.system() == "Darwin" else Key.ctrl
_IS_MACOS = platform.system() == "Darwin"

_ns_workspace = None
if _IS_MACOS:
    try:
        from AppKit import NSWorkspace as _NSWorkspace
        _ns_workspace = _NSWorkspace.sharedWorkspace
    except ImportError:
        pass

from audio.recorder import AudioRecorder
from audio.sounds import play_start, play_stop
import services.transcriber as _transcriber
import services.postprocess as _postprocess
from services.transcriber import transcribe_streaming
from services.postprocess import postprocess
from ui.overlay import TranscriptOverlay
from ui.window import MainWindow


class AppController(QObject):
    """
    Coordinates recording, trimming, and transcription.
    Runs the transcription pipeline in a background thread to avoid
    blocking the UI.
    """

    transcription_done = pyqtSignal(str, int, int)   # (text, seg_id, generation)
    transcription_failed = pyqtSignal(str, int, int) # (msg, seg_id, generation)
    interim_transcript = pyqtSignal(str)             # emitted with live transcript text

    def __init__(self, window: MainWindow):
        super().__init__()
        self.window = window
        self.recorder = AudioRecorder()

        self.__kb = None  # created lazily to avoid Quartz/Qt startup race

        # Transcript overlay
        self._transcript_overlay = TranscriptOverlay()

        # Streaming state — per-job containers, keyed by segment id
        self._active_job: dict | None = None
        self._is_recording = False
        self._generation = 0
        self._generation_lock = threading.Lock()
        self._pending_timers: list[QTimer] = []
        self._last_external_app = None

        # Apply any API key that was saved in a previous session.
        saved_key = self.window.get_api_key()
        if saved_key:
            _transcriber.configure(saved_key)
            _postprocess.configure(saved_key)

        # Connect window signals
        self.window.recording_requested.connect(self.on_start_recording)
        self.window.recording_stopped.connect(self.on_stop_recording)
        self.window.cancel_requested.connect(self.on_cancel_all)

        # Connect result signals back to UI updates
        self.transcription_done.connect(self._on_transcription_done)
        self.transcription_failed.connect(self._on_transcription_failed)

        # Live transcript updates → overlay
        self.interim_transcript.connect(self._transcript_overlay.set_text)

        # Keep a best-effort pointer to the last non-self foreground app so
        # pressing hotkey while this window is focused can hand focus back.
        self._focus_probe_timer = QTimer(self)
        self._focus_probe_timer.setInterval(250)
        self._focus_probe_timer.timeout.connect(self._capture_frontmost_external_app)
        self._focus_probe_timer.start()

    @property
    def _kb(self):
        """Lazily create the pynput keyboard controller on first use."""
        if self.__kb is None:
            self.__kb = KbController()
        return self.__kb

    def _on_interim_callback(self, text: str):
        """Called from the streaming thread — emit a Qt signal to cross threads safely."""
        self.interim_transcript.emit(text)

    def _capture_frontmost_external_app(self):
        if _ns_workspace is None:
            return
        try:
            app = _ns_workspace().frontmostApplication()
            if app is not None and app.processIdentifier() != os.getpid():
                self._last_external_app = app
        except Exception:
            pass

    def _release_focus_to_input_app(self) -> bool:
        """Try to hand focus to a non-VoiceInput app. Returns True on success."""
        focused = QApplication.focusWidget()
        if focused is not None:
            focused.clearFocus()
        self.window.clearFocus()

        if _ns_workspace is None:
            return False
        try:
            front = _ns_workspace().frontmostApplication()
            if front is not None and front.processIdentifier() != os.getpid():
                return True
            if (
                front is not None
                and front.processIdentifier() == os.getpid()
                and self._last_external_app is not None
            ):
                self._last_external_app.activateWithOptions_(0)
                return True
        except Exception:
            return False
        return False

    def _current_generation(self) -> int:
        with self._generation_lock:
            return self._generation

    def _bump_generation(self) -> int:
        with self._generation_lock:
            self._generation += 1
            return self._generation

    def _schedule_timer(self, delay_ms: int, callback):
        """Track UI timers so Escape can cancel pending paste/restore actions."""
        timer = QTimer(self)
        timer.setSingleShot(True)

        def _run():
            if timer in self._pending_timers:
                self._pending_timers.remove(timer)
            callback()
            timer.deleteLater()

        timer.timeout.connect(_run)
        self._pending_timers.append(timer)
        timer.start(delay_ms)

    def _cancel_pending_timers(self):
        for timer in self._pending_timers:
            timer.stop()
            timer.deleteLater()
        self._pending_timers.clear()

    @pyqtSlot()
    def on_start_recording(self):
        if self._is_recording:
            return

        api_key = self.window.get_api_key()
        if not api_key:
            self._transcript_overlay.show_error_at_cursor(
                "⚠  API key missing — open Settings and paste your Google Cloud API key"
            )
            self.window.show_window()
            return

        if api_key != getattr(self, "_current_api_key", None):
            _transcriber.configure(api_key)
            _postprocess.configure(api_key)
            self._current_api_key = api_key

        handoff_ok = self._release_focus_to_input_app()
        if not handoff_ok and self.window.isActiveWindow():
            self.window.showMinimized()

        play_start()
        self._transcript_overlay.show_at_cursor()
        QTimer.singleShot(0, self._release_focus_to_input_app)
        QTimer.singleShot(900, self._release_focus_to_input_app)
        self.window.set_status_recording()
        self.recorder.start()
        self._is_recording = True

        language = self.window.get_language_code()
        boost_words = self.window.get_boost_words()
        boost_value = self.window.get_boost_value()
        result_box: list[str | None] = [None]
        thread = threading.Thread(
            target=self._streaming_worker,
            args=(self.recorder.audio_queue, language, boost_words, boost_value, result_box),
            daemon=True,
        )
        self._active_job = {"thread": thread, "result_box": result_box}
        thread.start()

    @pyqtSlot()
    def on_stop_recording(self):
        if not self._is_recording:
            return
        self._is_recording = False

        # Capture the active queue *before* the tail delay so the finalizer
        # always sends the sentinel to the correct recording even if a new
        # session starts within the 200 ms window.
        captured_queue = self.recorder.audio_queue

        # Keep recording for 200 ms after the hotkey is released so the
        # trailing edge of the user's speech is captured.
        QTimer.singleShot(200, lambda: self.recorder.finalize(captured_queue))

        play_stop()

        seg_id = self._transcript_overlay.freeze_active_segment()

        job = self._active_job
        thread_ref = job["thread"] if job else None
        result_box = job["result_box"] if job else [None]

        self.window.set_status_transcribing()

        prompt = self.window.get_postproc_prompt()
        generation = self._current_generation()
        threading.Thread(
            target=self._wait_for_streaming,
            args=(thread_ref, result_box, prompt, seg_id, generation),
            daemon=True,
        ).start()

    @pyqtSlot()
    def on_cancel_all(self):
        self._bump_generation()
        self._is_recording = False
        self._active_job = None

        self.recorder.stop()
        self._transcript_overlay.dismiss()
        self._cancel_pending_timers()
        self.window.set_status_idle()

    def _streaming_worker(self, audio_queue, language, boost_words, boost_value, result_box: list):
        """Runs in a background thread. Streams audio to the API and stores the result."""
        text = transcribe_streaming(
            audio_queue=audio_queue,
            language_code=language,
            on_interim=self._on_interim_callback,
            boost_words=boost_words if boost_words else None,
            boost_value=boost_value,
        )
        result_box[0] = text

    def _wait_for_streaming(
        self,
        thread: threading.Thread | None,
        result_box: list,
        prompt: str,
        seg_id: int,
        generation: int,
    ):
        """
        Runs in a background thread. Waits for the streaming thread to finish,
        applies post-processing, and emits the result paired with *seg_id*.
        """
        if thread is not None:
            thread.join()

        if generation != self._current_generation():
            return

        text = result_box[0]

        if not text:
            self.transcription_failed.emit("No transcription returned.", seg_id, generation)
            return

        if prompt:
            print(f"[Postprocess] Sending to Gemini… (Transcription: {text})")
            text = postprocess(text, prompt)
            print(f"[Postprocess] Result: {text}")

        if generation != self._current_generation():
            return

        self.transcription_done.emit(text, seg_id, generation)

    # ------------------------------------------------------------------
    # Clipboard-swap auto-paste
    # ------------------------------------------------------------------

    @pyqtSlot(str, int, int)
    def _on_transcription_done(self, text: str, seg_id: int, generation: int):
        if generation != self._current_generation():
            return

        print(f"\n>>> {text}\n")

        self._transcript_overlay.complete_segment(seg_id)

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
        def _do_paste():
            if generation != self._current_generation():
                return

            paste_mod_str = "cmd" if _IS_MACOS else "ctrl"
            active_mods = self.window._hotkey_listener._active_modifiers
            needs_modifier = paste_mod_str not in active_mods

            if needs_modifier:
                self._kb.press(_PASTE_MODIFIER)

            self._kb.press("v")
            self._kb.release("v")

            if needs_modifier:
                self._kb.release(_PASTE_MODIFIER)

        self._schedule_timer(80, _do_paste)

        # 4. Restore original clipboard after paste has had time to complete
        def _restore():
            if generation != self._current_generation():
                return
            clipboard.setMimeData(saved_mime)

        self._schedule_timer(350, _restore)

        if not self._transcript_overlay.isVisible():
            self.window.set_status_idle()

    @pyqtSlot(str, int, int)
    def _on_transcription_failed(self, msg: str, seg_id: int, generation: int):
        if generation != self._current_generation():
            return

        print(f"[Info] {msg}")
        self._transcript_overlay.complete_segment(seg_id)
        if not self._transcript_overlay.isVisible():
            self.window.set_status_idle()
