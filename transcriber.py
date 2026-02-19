"""
GCP Speech-to-Text **v2** caller using the ``google-cloud-speech`` library.

Supports both batch (``transcribe``) and streaming (``transcribe_streaming``)
recognition.

Authentication uses Application Default Credentials (ADC).
Run ``gcloud auth application-default login`` before starting the app.
"""

from __future__ import annotations

import queue
from typing import Callable, Iterator, Optional

import numpy as np

from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech

# Lazy-initialised clients (gRPC channel reuse across calls)
_client: Optional[SpeechClient] = None          # global endpoint
_client_regional: Optional[SpeechClient] = None  # regional endpoint for Chirp
_project_id: Optional[str] = None

_CHIRP_LOCATION = "us-central1"  # Chirp models require a regional endpoint


def _get_client() -> tuple[SpeechClient, str]:
    """Return a (SpeechClient, project_id) pair using the *global* endpoint."""
    global _client, _project_id
    if _client is None:
        _init_project()
        import google.auth
        credentials, _ = google.auth.default()
        _client = SpeechClient(credentials=credentials)
    return _client, _project_id


def _get_regional_client() -> tuple[SpeechClient, str]:
    """Return a (SpeechClient, project_id) pair using the *regional* endpoint
    required by Chirp models."""
    global _client_regional, _project_id
    if _client_regional is None:
        _init_project()
        import google.auth
        credentials, _ = google.auth.default()
        _client_regional = SpeechClient(
            credentials=credentials,
            client_options={"api_endpoint": f"{_CHIRP_LOCATION}-speech.googleapis.com"},
        )
    return _client_regional, _project_id


def _init_project():
    """Ensure _project_id is set."""
    global _project_id
    if _project_id is None:
        import google.auth
        _, project = google.auth.default()
        if not project:
            raise RuntimeError(
                "Could not determine GCP project. "
                "Set it with:  gcloud config set project YOUR_PROJECT_ID"
            )
        _project_id = project


# ---------------------------------------------------------------------------
# Batch (non-streaming) transcription
# ---------------------------------------------------------------------------

def transcribe(
    audio: np.ndarray,
    language_code: str = "en-US",
    sample_rate: int = 16000,
) -> Optional[str]:
    """
    Send audio to GCP Speech-to-Text v2 and return the transcript.

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
        client, project_id = _get_client()
    except Exception as exc:
        print(f"[Transcriber] Failed to initialise client: {exc}")
        return None

    config = cloud_speech.RecognitionConfig(
        explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
            encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=sample_rate,
            audio_channel_count=1,
        ),
        language_codes=[language_code],
        model="latest_short",
        features=cloud_speech.RecognitionFeatures(
            enable_automatic_punctuation=True,
        ),
    )

    request = cloud_speech.RecognizeRequest(
        recognizer=f"projects/{project_id}/locations/global/recognizers/_",
        config=config,
        content=audio_bytes,
    )

    try:
        response = client.recognize(request=request)
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

def _request_generator(
    recognizer: str,
    config: cloud_speech.RecognitionConfig,
    audio_queue: queue.Queue,
) -> Iterator[cloud_speech.StreamingRecognizeRequest]:
    """
    Yield ``StreamingRecognizeRequest`` messages for the streaming API.

    1. First message: recognizer + streaming config (no audio).
    2. Subsequent messages: raw audio bytes read from *audio_queue*.

    The generator terminates when it reads a *None* sentinel from the queue.
    """
    # --- first request: configuration only ---
    streaming_config = cloud_speech.StreamingRecognitionConfig(
        config=config,
        streaming_features=cloud_speech.StreamingRecognitionFeatures(
            interim_results=True,
        ),
    )
    yield cloud_speech.StreamingRecognizeRequest(
        recognizer=recognizer,
        streaming_config=streaming_config,
    )

    # --- subsequent requests: audio chunks ---
    while True:
        chunk = audio_queue.get()  # blocks until a chunk is available
        if chunk is None:
            # Sentinel — recording stopped; end the stream.
            break
        yield cloud_speech.StreamingRecognizeRequest(audio=chunk)


def transcribe_streaming(
    audio_queue: queue.Queue,
    language_code: str = "en-US",
    sample_rate: int = 16000,
    on_interim: Optional[Callable[[str], None]] = None,
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

    Returns
    -------
    str or None
        The final concatenated transcript, or *None* if nothing was recognised.
    """
    try:
        client, project_id = _get_client()
    except Exception as exc:
        print(f"[Streaming] Failed to initialise client: {exc}")
        return None

    recognizer = f"projects/{project_id}/locations/global/recognizers/_"

    config = cloud_speech.RecognitionConfig(
        explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
            encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=sample_rate,
            audio_channel_count=1,
        ),
        language_codes=[language_code],
        model="latest_long",
        features=cloud_speech.RecognitionFeatures(
            enable_automatic_punctuation=True,
        ),
    )

    requests = _request_generator(recognizer, config, audio_queue)

    final_transcripts: list[str] = []

    try:
        responses = client.streaming_recognize(requests=requests)

        for response in responses:
            # Collect all interim (non-final) pieces from this response
            # so we can show the complete current hypothesis at once.
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

            # After processing every result in this response, push
            # one combined snapshot: all settled finals + all current
            # interim segments joined together.
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
