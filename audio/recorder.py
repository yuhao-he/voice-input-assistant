"""
Audio recorder using sounddevice.

Records 16-bit PCM mono audio at 16 kHz and pushes raw chunks to a
``queue.Queue`` for real-time streaming transcription.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional

import numpy as np
import sounddevice as sd

# Audio format constants
SAMPLE_RATE = 16000  # Hz
CHANNELS = 1
DTYPE = "int16"
BLOCK_SIZE = 1024  # frames per callback

# Sentinel pushed to the audio queue when recording stops.
_AUDIO_QUEUE_SENTINEL = None


class AudioRecorder:
    """
    Records audio from the default input device.

    Usage:
        recorder = AudioRecorder()
        recorder.start()
        ...
        recorder.finalize(recorder.audio_queue)

    While recording is active, raw PCM chunks (bytes) are pushed to
    ``recorder.audio_queue`` so that a streaming consumer can read them
    in real time.  A *None* sentinel is pushed when recording stops.
    """

    def __init__(self, on_volume=None):
        self._stream: Optional[sd.InputStream] = None
        self._frames: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._recording = False
        self._on_volume = on_volume

        # Queue for real-time streaming.  Each item is a ``bytes`` object
        # containing raw LINEAR16 PCM, or *None* to signal end-of-stream.
        self.audio_queue: queue.Queue[Optional[bytes]] = queue.Queue()

    def start(self):
        """Start recording audio."""
        stream_to_close = None
        old_queue = None

        with self._lock:
            if self._recording:
                # Forcefully end the previous recording session
                self._recording = False
                stream_to_close = self._stream
                old_queue = self.audio_queue

            self._frames = []
            # Replace the queue so any old consumer referencing the
            # previous queue is not confused.
            self.audio_queue = queue.Queue()
            self._recording = True

        if stream_to_close is not None:
            old_queue.put(_AUDIO_QUEUE_SENTINEL)
            stream_to_close.stop()
            stream_to_close.close()

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCK_SIZE,
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop(self) -> Optional[np.ndarray]:
        """
        Stop recording and return the captured audio as a numpy int16 array.
        Returns None if no audio was captured.
        """
        with self._lock:
            if not self._recording:
                return None
            self._recording = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # Signal end-of-stream to the streaming consumer.
        self.audio_queue.put(_AUDIO_QUEUE_SENTINEL)

        with self._lock:
            if not self._frames:
                return None
            audio = np.concatenate(self._frames, axis=0).flatten()
            self._frames = []
            return audio

    def finalize(self, queue_ref: "queue.Queue") -> None:
        """
        End the recording associated with *queue_ref*.

        If this recorder is still actively writing to *queue_ref* (i.e. no
        new recording has started since the caller captured the reference),
        stop the physical audio stream.  Either way, push the end-of-stream
        sentinel into *queue_ref* so that any streaming consumer that is
        blocked on it can terminate.

        This method is used to implement a short *tail-recording* delay:
        the caller captures ``recorder.audio_queue`` at hotkey-release time,
        waits N ms, then calls ``recorder.finalize(captured_queue)`` so the
        final fraction of audio is included before the stream closes.
        """
        should_stop_stream = False
        with self._lock:
            if self._recording and self.audio_queue is queue_ref:
                self._recording = False
                should_stop_stream = True

        if should_stop_stream and self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # Always push the sentinel so the streaming consumer for this
        # specific recording can exit cleanly.
        queue_ref.put(_AUDIO_QUEUE_SENTINEL)

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """sounddevice callback — runs in audio thread."""
        if status:
            pass  # Ignore xruns silently

        chunk = indata.copy()

        with self._lock:
            if self._recording:
                self._frames.append(chunk)

        # Push raw bytes to the streaming queue (non-blocking).
        try:
            self.audio_queue.put_nowait(chunk.tobytes())
        except queue.Full:
            pass  # drop chunk if queue is somehow full

        # Report live volume to the callback (if set)
        if self._on_volume is not None:
            samples = indata.flatten().astype(np.float64)
            rms = np.sqrt(np.mean(samples ** 2))
            rms_db = 20.0 * np.log10(rms / 32768.0) if rms > 0 else -120.0
            try:
                self._on_volume(rms_db)
            except Exception:
                pass
