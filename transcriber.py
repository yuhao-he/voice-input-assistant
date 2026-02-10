"""
GCP Speech-to-Text REST API caller.

Sends base64-encoded LINEAR16 audio to the synchronous recognize endpoint
using a plain API key (no service account needed).
"""

from __future__ import annotations

import base64
from typing import Optional

import numpy as np
import requests

RECOGNIZE_URL = "https://speech.googleapis.com/v1/speech:recognize"


def transcribe(
    audio: np.ndarray,
    api_key: str,
    language_code: str = "en-US",
    sample_rate: int = 16000,
) -> Optional[str]:
    """
    Send audio to GCP Speech-to-Text and return the transcript.

    Parameters
    ----------
    audio : np.ndarray
        1-D int16 PCM audio samples.
    api_key : str
        Google Cloud API key with Speech-to-Text enabled.
    language_code : str
        BCP-47 language code, e.g. "en-US".
    sample_rate : int
        Sample rate of the audio.

    Returns
    -------
    str or None
        The transcribed text, or None on failure / empty result.
    """
    if audio is None or len(audio) == 0:
        return None

    # Encode audio to base64
    audio_bytes = audio.astype(np.int16).tobytes()
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

    payload = {
        "config": {
            "encoding": "LINEAR16",
            "sampleRateHertz": sample_rate,
            "languageCode": language_code,
            "enableAutomaticPunctuation": True,
        },
        "audio": {
            "content": audio_b64,
        },
    }

    url = f"{RECOGNIZE_URL}?key={api_key}"

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[Transcriber] Request failed: {exc}")
        if hasattr(exc, "response") and exc.response is not None:
            print(f"[Transcriber] Response body: {exc.response.text}")
        return None

    data = response.json()
    results = data.get("results", [])
    if not results:
        return None

    # Concatenate all result transcripts
    transcripts = []
    for result in results:
        alternatives = result.get("alternatives", [])
        if alternatives:
            transcripts.append(alternatives[0].get("transcript", ""))

    full_text = " ".join(transcripts).strip()
    return full_text if full_text else None

