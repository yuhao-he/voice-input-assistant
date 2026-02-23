"""
Post-process transcribed text via Gemini.

Authentication uses a Google Cloud API key — the same key used for
Speech-to-Text.  Call ``configure(api_key)`` once before use.

Uses the direct Gemini API (generativelanguage.googleapis.com) rather than
Vertex AI, so no GCP project ID or gcloud credentials are required.
"""

from __future__ import annotations

import httpx
from typing import Optional

from google import genai

_MODEL = "gemini-2.5-flash"

# Lazy-initialised client — recreated whenever configure() is called.
_client: Optional[genai.Client] = None
_http_client: Optional[httpx.Client] = None
_api_key: Optional[str] = None


def configure(api_key: str) -> None:
    """Set the API key used for all subsequent postprocess calls.

    Resets the cached client so the next call creates a fresh one with the
    new key.
    """
    global _client, _api_key, _http_client
    _api_key = api_key.strip() if api_key else ""
    _client = None  # force re-creation on next call
    
    if _http_client is not None:
        try:
            _http_client.close()
        except Exception:
            pass
        _http_client = None


def _get_client() -> genai.Client:
    """Return a Gemini client, creating it on first call."""
    global _client, _http_client
    if _client is None:
        if not _api_key:
            raise RuntimeError(
                "Google Cloud API key not configured. "
                "Open Settings and paste your API key."
            )
            
        # Create a persistent HTTP client to keep the TLS connection alive
        # between rapid, consecutive transcription requests.
        _http_client = httpx.Client(http2=True)
        _client = genai.Client(
            api_key=_api_key,
            http_options={'httpx_client': _http_client}
        )
    return _client


def postprocess(transcript: str, prompt: str) -> str:
    """
    Send *transcript* + *prompt* to Gemini and return the model's response.

    Parameters
    ----------
    transcript : str
        The raw transcription from Speech-to-Text.
    prompt : str
        User-defined instruction (e.g. "Fix grammar and punctuation").

    Returns
    -------
    str
        The post-processed text, or the original transcript on failure.
    """
    prompt = prompt.strip() if prompt else ""
    if not prompt or not transcript:
        return transcript

    full_prompt = (
        f"{prompt}\n\n"
        f"Transcript:\n{transcript}\n\n"
        f"Respond ONLY with the processed text, nothing else."
    )

    try:
        print(f"[Postprocess] Full prompt to Gemini:\n{full_prompt}")
        client = _get_client()
        response = client.models.generate_content(
            model=_MODEL,
            contents=full_prompt,
        )
        result = response.text.strip()
        return result if result else transcript
    except Exception as exc:
        print(f"[Postprocess] Gemini call failed: {exc}")
        return transcript
