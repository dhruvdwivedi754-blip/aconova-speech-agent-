"""ElevenLabs text-to-speech API integration."""

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

LANGUAGE_VOICES = {
    "en": "ELEVENLABS_VOICE_ID_EN",
    "hi": "ELEVENLABS_VOICE_ID_HI",
}
ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_OUTPUT_FORMAT = "mp3_44100_128"
MAX_TEXT_LENGTH = 5000


def _safe_log_detail(detail, *sensitive_values):
    """Redact request secrets and content before logging upstream errors."""
    safe_detail = str(detail)
    for value in sensitive_values:
        if value:
            safe_detail = safe_detail.replace(value, "[REDACTED]")
    return " ".join(safe_detail.split())[:500]


class SpeechServiceError(Exception):
    """A safe-to-handle error raised while configuring or calling ElevenLabs."""

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def generate_speech(text, language):
    """Generate MP3 audio bytes for validated text and a supported language."""
    if language not in LANGUAGE_VOICES:
        raise SpeechServiceError("Unsupported language.", status_code=400)

    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key or api_key == "your_elevenlabs_api_key_here":
        logger.error("Missing ElevenLabs configuration: ELEVENLABS_API_KEY")
        raise SpeechServiceError("Speech service is not configured.", status_code=503)

    voice_id = os.getenv(LANGUAGE_VOICES[language], "").strip()
    if not voice_id or voice_id.endswith("_voice_id_here"):
        logger.error("Missing ElevenLabs configuration: %s", LANGUAGE_VOICES[language])
        raise SpeechServiceError("Speech service is not configured.", status_code=503)

    payload = {
        "text": text,
        "model_id": os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip()
        or "eleven_multilingual_v2",
    }
    try:
        response = requests.post(
            ELEVENLABS_API_URL.format(voice_id=voice_id),
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json=payload,
            params={"output_format": ELEVENLABS_OUTPUT_FORMAT},
            timeout=(10, 90),
        )
    except requests.Timeout as error:
        detail = _safe_log_detail(error, api_key, voice_id, text)
        logger.warning("ElevenLabs request timed out: %s", detail or type(error).__name__)
        raise SpeechServiceError("Speech generation timed out.", status_code=504) from error
    except requests.RequestException as error:
        detail = _safe_log_detail(error, api_key, voice_id, text)
        logger.warning("ElevenLabs request failed (%s): %s", type(error).__name__, detail)
        raise SpeechServiceError("Speech generation failed.", status_code=502) from error

    if response.status_code in (401, 403):
        detail = _safe_log_detail(response.text, api_key, voice_id, text)
        logger.warning("ElevenLabs rejected credentials (HTTP %s): %s", response.status_code, detail)
        raise SpeechServiceError("Speech service credentials are invalid.", status_code=401)
    if response.status_code == 429:
        detail = _safe_log_detail(response.text, api_key, voice_id, text)
        logger.warning("ElevenLabs rate limit reached (HTTP 429): %s", detail)
        raise SpeechServiceError("Speech service is busy. Please try again shortly.", status_code=429)
    if not response.ok:
        detail = _safe_log_detail(response.text, api_key, voice_id, text)
        logger.warning("ElevenLabs returned HTTP %s: %s", response.status_code, detail)
        raise SpeechServiceError("Speech generation failed.", status_code=502)
    if not response.content:
        logger.warning("ElevenLabs returned an empty audio response.")
        raise SpeechServiceError("Speech generation failed.", status_code=502)

    return response.content