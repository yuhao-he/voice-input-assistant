"""
GCP Speech-to-Text **v1** caller.

Authentication uses a Google Cloud API key — no gcloud CLI or Application
Default Credentials required.  Call ``configure(api_key)`` once before use.

Speech-to-Text v1 is used (instead of v2) because it does not require a
GCP project ID in the recognizer path, which keeps the setup to a single
copy-paste API key.
"""

from __future__ import annotations

import queue
from typing import Callable, Iterator, Optional

import numpy as np

from google.cloud import speech

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_client: Optional[speech.SpeechClient] = None
_api_key: Optional[str] = None


def configure(api_key: str) -> None:
    """Set the API key used for all subsequent transcription calls.

    Resets the cached client so the next call creates a fresh one with the
    new key.
    """
    global _client, _api_key
    _api_key = api_key.strip() if api_key else ""
    _client = None  # force re-creation on next call


def _get_client() -> speech.SpeechClient:
    global _client
    if _client is None:
        if not _api_key:
            raise RuntimeError(
                "Google Cloud API key not configured. "
                "Open Settings and paste your API key."
            )
        _client = speech.SpeechClient(
            client_options={"api_key": _api_key}
        )
    return _client


# ---------------------------------------------------------------------------
# Batch (non-streaming) transcription
# ---------------------------------------------------------------------------

def transcribe(
    audio: np.ndarray,
    language_code: str = "en-US",
    sample_rate: int = 16000,
) -> Optional[str]:
    """
    Send audio to GCP Speech-to-Text v1 and return the transcript.

    Parameters
    ----------
    audio : np.ndarray
        1-D int16 PCM audio samples.
    language_code : str
        BCP-47 language code, e.g. "en-US".
    sample_rate : int
        Sample rate of the audio.

    Returns
    -------
    str or None
        The transcribed text, or *None* on failure / empty result.
    """
    if audio is None or len(audio) == 0:
        return None

    audio_bytes = audio.astype(np.int16).tobytes()

    try:
        client = _get_client()
    except Exception as exc:
        print(f"[Transcriber] Failed to initialise client: {exc}")
        return None

    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=sample_rate,
        language_code=language_code,
        model="latest_short",
        use_enhanced=True,
        enable_automatic_punctuation=True,
    )

    audio_obj = speech.RecognitionAudio(content=audio_bytes)

    try:
        response = client.recognize(config=config, audio=audio_obj)
    except Exception as exc:
        print(f"[Transcriber] API call failed: {exc}")
        return None

    transcripts = []
    for result in response.results:
        if result.alternatives:
            transcripts.append(result.alternatives[0].transcript)

    full_text = " ".join(transcripts).strip()
    return full_text if full_text else None


# ---------------------------------------------------------------------------
# Streaming transcription
# ---------------------------------------------------------------------------

def _audio_generator(
    audio_queue: queue.Queue,
) -> Iterator[speech.StreamingRecognizeRequest]:
    """
    Yield audio-only ``StreamingRecognizeRequest`` messages.

    In the v1 helper API the ``StreamingRecognitionConfig`` is passed as a
    separate first argument to ``streaming_recognize``; requests therefore
    carry *only* audio content.

    The generator terminates when it reads a *None* sentinel from the queue.
    """
    while True:
        chunk = audio_queue.get()  # blocks until a chunk is available
        if chunk is None:
            break
        yield speech.StreamingRecognizeRequest(audio_content=chunk)


def transcribe_streaming(
    audio_queue: queue.Queue,
    language_code: str = "en-US",
    sample_rate: int = 16000,
    on_interim: Optional[Callable[[str], None]] = None,
    boost_words: Optional[list[str]] = None,
    boost_value: float = 10.0,
) -> Optional[str]:
    """
    Perform **streaming** speech recognition, consuming audio chunks from
    *audio_queue* in real time.

    Parameters
    ----------
    audio_queue : queue.Queue
        A queue that yields ``bytes`` (raw LINEAR16 PCM) while the user is
        speaking.  A ``None`` sentinel signals end-of-stream.
    language_code : str
        BCP-47 language code.
    sample_rate : int
        Sample rate of the audio.
    on_interim : callable, optional
        Called with the latest interim transcript string whenever a new
        streaming response arrives.
    boost_words : list of str, optional
        Words or phrases to bias the recogniser towards.
    boost_value : float
        Strength of the phrase boost (0 – 20).  Default is 10.0.

    Returns
    -------
    str or None
        The final concatenated transcript, or *None* if nothing was recognised.
    """
    try:
        client = _get_client()
    except Exception as exc:
        print(f"[Streaming] Failed to initialise client: {exc}")
        return None

    speech_contexts = (
        [speech.SpeechContext(phrases=boost_words, boost=boost_value)]
        if boost_words
        else []
    )

    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=sample_rate,
        language_code=language_code,
        model="latest_long",
        use_enhanced=True,
        enable_automatic_punctuation=True,
        speech_contexts=speech_contexts,
    )

    streaming_config = speech.StreamingRecognitionConfig(
        config=config,
        interim_results=True,
    )

    # v1 helper: config is the first positional arg; requests carry audio only.
    requests = _audio_generator(audio_queue)

    final_transcripts: list[str] = []

    try:
        responses = client.streaming_recognize(streaming_config, requests)

        for response in responses:
            interim_parts: list[str] = []

            for result in response.results:
                if not result.alternatives:
                    continue

                transcript = result.alternatives[0].transcript

                if result.is_final:
                    print(f"[Streaming]   FINAL : {transcript}")
                    final_transcripts.append(transcript)
                else:
                    interim_parts.append(transcript)

            if on_interim is not None:
                current = "".join(final_transcripts + interim_parts).strip()
                try:
                    on_interim(current)
                except Exception:
                    pass

    except Exception as exc:
        print(f"[Streaming] API error: {exc}")

    full_text = "".join(final_transcripts).strip()
    return full_text if full_text else None
