"""
Audio recorder using sounddevice with silence trimming.

Records 16-bit PCM mono audio at 16 kHz. After recording stops,
trims leading and trailing silence based on a configurable RMS
threshold (in dB).

The recorder also pushes raw audio chunks to a ``queue.Queue`` so
that a streaming transcription consumer can read them in real time.
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
        recorder = AudioRecorder(on_volume=my_callback)
        recorder.start()
        ...
        audio_data = recorder.stop()   # returns numpy int16 array
        trimmed = trim_silence(audio_data)

    While recording is active, raw PCM chunks (bytes) are also pushed to
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
        with self._lock:
            if self._recording:
                return
            self._frames = []
            # Replace the queue so any old consumer referencing the
            # previous queue is not confused.
            self.audio_queue = queue.Queue()
            self._recording = True

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


class VolumeMonitor:
    """
    Always-on microphone monitor that reports live input volume.

    Opens a lightweight input stream on construction and continuously
    reports RMS dB via the on_volume callback — like an OS
    input level meter.
    """

    def __init__(self, on_volume=None):
        self._on_volume = on_volume
        self._stream: Optional[sd.InputStream] = None

    def start(self):
        """Start monitoring mic input."""
        if self._stream is not None:
            return
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCK_SIZE,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        """Stop monitoring."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(self, indata: np.ndarray, frames: int, time_info, status):
        if self._on_volume is None:
            return
        samples = indata.flatten().astype(np.float64)
        rms = np.sqrt(np.mean(samples ** 2))
        if rms > 0:
            rms_db = 20.0 * np.log10(rms / 32768.0)
        else:
            rms_db = -120.0
        try:
            self._on_volume(rms_db)
        except Exception:
            pass


def trim_silence(
    audio: np.ndarray,
    threshold_db: float = -30.0,
    frame_length_ms: float = 20.0,
    sample_rate: int = SAMPLE_RATE,
    padding_ms: float = 100.0,
) -> Optional[np.ndarray]:
    """
    Trim leading and trailing silence from an int16 audio array.

    Parameters
    ----------
    audio : np.ndarray
        1-D int16 audio samples.
    threshold_db : float
        RMS threshold in dB (relative to int16 full scale).
        Frames quieter than this are considered silence.
    frame_length_ms : float
        Length of analysis frames in milliseconds.
    sample_rate : int
        Audio sample rate.
    padding_ms : float
        Padding to keep around detected speech edges (ms).

    Returns
    -------
    np.ndarray or None
        Trimmed audio, or None if the entire clip is silence.
    """
    if audio is None or len(audio) == 0:
        return None

    frame_length = int(sample_rate * frame_length_ms / 1000.0)
    num_frames = len(audio) // frame_length
    if num_frames == 0:
        return None

    # Compute RMS in dB for each frame
    audio_float = audio.astype(np.float64)
    rms_values = np.zeros(num_frames)
    for i in range(num_frames):
        start = i * frame_length
        end = start + frame_length
        frame = audio_float[start:end]
        rms = np.sqrt(np.mean(frame ** 2))
        if rms > 0:
            rms_values[i] = 20.0 * np.log10(rms / 32768.0)
        else:
            rms_values[i] = -120.0  # silence floor

    # Find first and last frame above threshold
    above = np.where(rms_values > threshold_db)[0]
    if len(above) == 0:
        return None  # Entire clip is silence

    first_frame = above[0]
    last_frame = above[-1]

    # Convert to sample indices with padding
    padding_samples = int(sample_rate * padding_ms / 1000.0)
    start_sample = max(0, first_frame * frame_length - padding_samples)
    end_sample = min(len(audio), (last_frame + 1) * frame_length + padding_samples)

    return audio[start_sample:end_sample]
