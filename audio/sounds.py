"""
Cross-platform audio feedback sounds.

Generates short sine-wave chirps using numpy and plays them via
sounddevice.  No bundled sound files needed — works on macOS,
Linux, and Windows.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd

_SAMPLE_RATE = 44100


def _generate_chirp(
    freq_start: float,
    freq_end: float,
    duration: float = 0.12,
    sample_rate: int = _SAMPLE_RATE,
    volume: float = 0.3,
) -> np.ndarray:
    """
    Generate a linear-frequency chirp (rising or falling tone).

    Returns a float32 numpy array suitable for sounddevice.play().
    """
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    # Linear frequency sweep
    freq = np.linspace(freq_start, freq_end, len(t))
    # Instantaneous phase via cumulative integral of frequency
    phase = 2.0 * np.pi * np.cumsum(freq) / sample_rate
    wave = np.sin(phase).astype(np.float32) * volume

    # Apply a short fade-in / fade-out to avoid clicks
    fade_len = int(sample_rate * 0.008)  # 8 ms
    if fade_len > 0 and fade_len < len(wave):
        fade_in = np.linspace(0, 1, fade_len, dtype=np.float32)
        fade_out = np.linspace(1, 0, fade_len, dtype=np.float32)
        wave[:fade_len] *= fade_in
        wave[-fade_len:] *= fade_out

    return wave


# Pre-generate the two chirps at import time
_chirp_start = _generate_chirp(600, 900, duration=0.12)
_chirp_stop = _generate_chirp(900, 600, duration=0.12)


def play_start():
    """Play a short rising chirp to indicate recording has started."""
    try:
        sd.play(_chirp_start, samplerate=_SAMPLE_RATE)
    except Exception:
        pass  # Non-critical — don't crash if audio output fails


def play_stop():
    """Play a short falling chirp to indicate recording has stopped."""
    try:
        sd.play(_chirp_stop, samplerate=_SAMPLE_RATE)
    except Exception:
        pass
