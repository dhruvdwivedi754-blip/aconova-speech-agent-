"""ElevenLabs text-to-speech API integration."""

import asyncio
import logging
import os
import re
from io import BytesIO

import requests
from dotenv import load_dotenv

try:
    import edge_tts
    from edge_tts.exceptions import EdgeTTSException
except ImportError:  # pragma: no cover - reported when the free provider is requested.
    edge_tts = None
    EDGE_TTS_EXCEPTIONS = ()
else:
    EDGE_TTS_EXCEPTIONS = (EdgeTTSException,)

try:
    from aiohttp import ClientError as AiohttpClientError
except ImportError:  # pragma: no cover - aiohttp is installed with edge-tts.
    AIOHTTP_EXCEPTIONS = ()
else:
    AIOHTTP_EXCEPTIONS = (AiohttpClientError,)

from services.translation_service import (
    TranslationServiceError,
    detect_speaker_genders,
    translate_text,
)

try:
    from gtts import gTTS
    from gtts.tts import gTTSError
except ImportError:  # pragma: no cover - optional free fallback.
    gTTS = None
    GTTS_EXCEPTIONS = ()
else:
    GTTS_EXCEPTIONS = (gTTSError,)

try:
    from pydub import AudioSegment
except ImportError:  # pragma: no cover - optional in lightweight environments.
    AudioSegment = None

load_dotenv()

logger = logging.getLogger(__name__)

LANGUAGE_VOICES = {
    "en": "ELEVENLABS_VOICE_ID_EN",
    "hi": "ELEVENLABS_VOICE_ID_HI",
    "ta": "ELEVENLABS_VOICE_ID_TA",
    "es": "ELEVENLABS_VOICE_ID_ES",
}
SUPPORTED_LANGUAGES = list(LANGUAGE_VOICES.keys())
ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_OUTPUT_FORMAT = "mp3_44100_128"
MAX_TEXT_LENGTH = 5000

VOICE_AGENTS = {
    "storyteller": {
        "name": "Storyteller AI",
        "description": "Stories, audiobooks, expressive narration, and immersive character work.",
        "voice_env_prefix": "STORYTELLER_VOICE_ID",
        "narration_hint": "Narrate this story with vivid, expressive, cinematic emotion and natural pacing.",
    },
    "professional": {
        "name": "Professional Narrator AI",
        "description": "Educational content, tutorials, presentations, and informative delivery.",
        "voice_env_prefix": "PROFESSIONAL_VOICE_ID",
        "narration_hint": "Deliver this content clearly, professionally, and with confident narration for learning and presentations.",
    },
}

FREE_AGENT_VOICES = {
    "storyteller": {
        "en": "en-US-JennyNeural",
        "hi": "hi-IN-SwaraNeural",
        "ta": "ta-IN-PallaviNeural",
        "es": "es-ES-ElviraNeural",
    },
    "professional": {
        "en": "en-US-GuyNeural",
        "hi": "hi-IN-MadhurNeural",
        "ta": "ta-IN-ValluvarNeural",
        "es": "es-ES-AlvaroNeural",
    },
}


def _safe_log_detail(detail, *sensitive_values):
    """Redact request secrets and content before logging upstream errors."""
    safe_detail = str(detail)
    for value in sensitive_values:
        if value:
            safe_detail = safe_detail.replace(value, "[REDACTED]")
    return " ".join(safe_detail.split())[:500]


class SpeechServiceError(Exception):
    """A safe-to-handle error raised while configuring or calling ElevenLabs."""

    def __init__(self, message, status_code=502, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _validate_voice_id(voice_id):
    return bool(voice_id and re.fullmatch(r"[A-Za-z0-9_-]+", voice_id))


def get_agent_config(agent_name=None):
    agent_name = (agent_name or "storyteller").strip().lower()
    config = VOICE_AGENTS.get(agent_name)
    if not config:
        raise SpeechServiceError("Unsupported voice agent.", status_code=400)
    return config


def resolve_voice_for_language(language, voice_id=None, agent_name=None):
    if voice_id:
        if not _validate_voice_id(voice_id):
            raise SpeechServiceError("Invalid voice configuration.", status_code=400)
        return voice_id

    if agent_name:
        config = get_agent_config(agent_name)
        language_suffix = language.upper()
        env_var = f"{config['voice_env_prefix']}_{language_suffix}"
        configured = os.getenv(env_var, "").strip()
        if not configured or configured.endswith("_voice_id_here"):
            raise SpeechServiceError(
                f"The {config['name']} voice is not configured for {language_suffix}.",
                status_code=503,
                code="voice_agent_config",
            )
        for other_name, other_config in VOICE_AGENTS.items():
            if other_name == agent_name:
                continue
            other_env_var = f"{other_config['voice_env_prefix']}_{language_suffix}"
            other_voice_id = os.getenv(other_env_var, "").strip()
            if other_voice_id == configured:
                raise SpeechServiceError(
                    "Each voice agent must use a different ElevenLabs voice ID. "
                    f"Update {env_var} and {other_env_var}.",
                    status_code=503,
                    code="voice_agent_config",
                )
        return configured

    env_var = LANGUAGE_VOICES.get(language)
    if not env_var:
        raise SpeechServiceError("Unsupported language.", status_code=400)
    configured = os.getenv(env_var, "").strip()
    if not configured or configured.endswith("_voice_id_here"):
        raise SpeechServiceError("Speech service is not configured.", status_code=503)
    return configured


def translate_text_for_language(text, language):
    """Preserve the legacy text-only helper while using the shared translator."""
    try:
        return translate_text(text, language).text
    except TranslationServiceError as error:
        raise SpeechServiceError(
            str(error),
            status_code=error.status_code,
            code=error.code,
        ) from error


def _generate_free_tts_audio(text, language):
    """Fallback to Google Translate TTS so the app still works on free plans."""
    if gTTS is None:
        raise SpeechServiceError("Speech service is not configured.", status_code=503)

    lang_map = {"en": "en", "hi": "hi", "ta": "ta", "es": "es"}
    lang = lang_map.get(language, "en")
    buffer = BytesIO()
    try:
        tts = gTTS(text=text, lang=lang, slow=False)
        tts.write_to_fp(buffer)
    except (requests.RequestException, OSError) + GTTS_EXCEPTIONS as error:
        logger.warning(
            "Free Google TTS failed for %s (%s).",
            language,
            type(error).__name__,
        )
        raise SpeechServiceError(
            "Free speech generation is temporarily unavailable. "
            "Check your internet connection and try again.",
            status_code=502,
        ) from error
    audio = buffer.getvalue()
    if not audio:
        raise SpeechServiceError("Speech service is not configured.", status_code=503)
    logger.warning("ElevenLabs is unavailable for this account; using the free Google TTS fallback for %s.", language)
    return audio


async def _stream_edge_tts_audio(text, voice):
    if edge_tts is None:
        raise SpeechServiceError(
            "The free voice provider is not installed. Run "
            "`python -m pip install -r requirements.txt`.",
            status_code=503,
            code="speech_dependency",
        )
    audio_buffer = BytesIO()
    communicate = edge_tts.Communicate(text, voice)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_buffer.write(chunk["data"])
    return audio_buffer.getvalue()


def _generate_free_agent_audio(text, language, agent_name, return_provider=False):
    agent_name = agent_name.strip().lower()
    provider = "free-edge-tts"
    if edge_tts is None:
        logger.warning("Edge TTS is not installed; using Google TTS for %s/%s.", agent_name, language)
        audio = _generate_free_tts_audio(text, language)
        provider = "free-gtts"
    else:
        voice = FREE_AGENT_VOICES[agent_name][language]
        try:
            audio = asyncio.run(_stream_edge_tts_audio(text, voice))
        except (OSError, asyncio.TimeoutError) + EDGE_TTS_EXCEPTIONS + AIOHTTP_EXCEPTIONS as error:
            logger.warning(
                "Free Edge TTS failed for %s/%s (%s); trying Google TTS.",
                agent_name,
                language,
                type(error).__name__,
            )
            audio = _generate_free_tts_audio(text, language)
            provider = "free-gtts"
        else:
            if not audio:
                logger.warning(
                    "Free Edge TTS returned no audio for %s/%s; trying Google TTS.",
                    agent_name,
                    language,
                )
                audio = _generate_free_tts_audio(text, language)
                provider = "free-gtts"
            else:
                logger.info("Generated speech with the free Edge TTS %s voice for %s.", agent_name, language)

    return (audio, provider) if return_provider else audio


def _generate_agent_fallback(text, language, agent_name, return_provider):
    return _generate_free_agent_audio(
        text,
        language,
        agent_name.strip().lower(),
        return_provider=return_provider,
    )


def generate_speech(
    text,
    language,
    voice_id=None,
    agent_name=None,
    return_provider=False,
):
    """Generate MP3 audio bytes for validated text and a supported language."""
    if not isinstance(text, str) or not text.strip():
        raise SpeechServiceError("Please enter some text first.", status_code=400)

    language = (language or "en").strip().lower()
    if language not in LANGUAGE_VOICES:
        raise SpeechServiceError("Unsupported language.", status_code=400)

    translated_text = translate_text_for_language(text, language)

    agent = get_agent_config(agent_name) if agent_name else None
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key or api_key == "your_elevenlabs_api_key_here":
        if agent and not voice_id:
            return _generate_agent_fallback(translated_text, language, agent_name, return_provider)
        logger.error("Missing ElevenLabs configuration: ELEVENLABS_API_KEY")
        raise SpeechServiceError("Speech service is not configured.", status_code=503)

    resolved_voice_id = resolve_voice_for_language(language, voice_id=voice_id, agent_name=agent_name)
    payload = {
        "text": translated_text,
        "model_id": os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip() or "eleven_multilingual_v2",
    }
    if agent and agent.get("narration_hint"):
        payload["voice_settings"] = {"stability": 0.45, "similarity_boost": 0.8}
        payload["instructions"] = agent["narration_hint"]

    try:
        response = requests.post(
            ELEVENLABS_API_URL.format(voice_id=resolved_voice_id),
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
        detail = _safe_log_detail(error, api_key, resolved_voice_id, text)
        logger.warning("ElevenLabs request timed out: %s", detail or type(error).__name__)
        if agent and not voice_id:
            return _generate_agent_fallback(translated_text, language, agent_name, return_provider)
        raise SpeechServiceError("Speech generation timed out.", status_code=504) from error
    except requests.RequestException as error:
        detail = _safe_log_detail(error, api_key, resolved_voice_id, text)
        logger.warning("ElevenLabs request failed (%s): %s", type(error).__name__, detail)
        if agent and not voice_id:
            return _generate_agent_fallback(translated_text, language, agent_name, return_provider)
        raise SpeechServiceError(
            "Could not reach ElevenLabs. Check your internet connection and try again.",
            status_code=502,
        ) from error

    if response.status_code in (401, 403):
        detail = _safe_log_detail(response.text, api_key, resolved_voice_id, text)
        logger.warning("ElevenLabs rejected credentials (HTTP %s): %s", response.status_code, detail)
        raise SpeechServiceError("Speech service credentials are invalid.", status_code=401)
    if response.status_code == 402:
        detail = _safe_log_detail(response.text, api_key, resolved_voice_id, text)
        logger.warning("ElevenLabs plan upgrade required (HTTP 402): %s", detail)
        if agent_name:
            if voice_id:
                raise SpeechServiceError(
                    "ElevenLabs does not allow the selected custom voice on the current plan.",
                    status_code=402,
                )
            return _generate_agent_fallback(translated_text, language, agent_name, return_provider)
        audio = _generate_free_tts_audio(translated_text, language)
        return (audio, "free-gtts") if return_provider else audio
    if response.status_code == 400:
        detail = _safe_log_detail(response.text, api_key, resolved_voice_id, text)
        logger.warning("ElevenLabs rejected the selected voice and language (HTTP 400): %s", detail)
        if agent and not voice_id:
            return _generate_agent_fallback(translated_text, language, agent_name, return_provider)
        raise SpeechServiceError(
            "The selected voice does not support this language. Choose a compatible voice or agent.",
            status_code=422,
            code="voice_language",
        )
    if response.status_code == 429:
        detail = _safe_log_detail(response.text, api_key, resolved_voice_id, text)
        logger.warning("ElevenLabs rate limit reached (HTTP 429): %s", detail)
        if agent and not voice_id:
            return _generate_agent_fallback(translated_text, language, agent_name, return_provider)
        raise SpeechServiceError("Speech service is busy. Please try again shortly.", status_code=429)
    if not response.ok:
        detail = _safe_log_detail(response.text, api_key, resolved_voice_id, text)
        logger.warning("ElevenLabs returned HTTP %s: %s", response.status_code, detail)
        if response.status_code >= 500 and agent and not voice_id:
            return _generate_agent_fallback(translated_text, language, agent_name, return_provider)
        raise SpeechServiceError(
            f"ElevenLabs could not generate speech (HTTP {response.status_code}). "
            "Please try again shortly.",
            status_code=502,
        )
    if not response.content:
        logger.warning("ElevenLabs returned an empty audio response.")
        raise SpeechServiceError(
            "ElevenLabs returned an empty audio file. Please try again.",
            status_code=502,
        )

    return (response.content, "elevenlabs") if return_provider else response.content


def parse_script_lines(script_text):
    """Parse multi-character dialogue lines into ordered speaker/text entries."""
    if not isinstance(script_text, str):
        raise SpeechServiceError("Script must be a text string.", status_code=400)

    script_text = script_text.strip()
    if not script_text:
        raise SpeechServiceError("Script cannot be empty.", status_code=400)

    segments = []
    speaker_name = r"[A-Za-zÀ-ÖØ-öø-ÿ0-9\u0900-\u097F][A-Za-zÀ-ÖØ-öø-ÿ0-9.'-\u0900-\u097F]*(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ0-9\u0900-\u097F][A-Za-zÀ-ÖØ-öø-ÿ0-9.'-\u0900-\u097F]*){0,1}"
    speaker_pattern = rf"(?P<speaker>{speaker_name})\s*:\s*"

    def looks_like_speaker(label):
        parts = [part for part in re.split(r"\s+", label.strip()) if part]
        if not parts:
            return False
        cleaned_parts = []
        for part in parts:
            cleaned = re.sub(r"^[^A-Za-zÀ-ÖØ-öø-ÿ0-9\u0900-\u097F]+|[^A-Za-zÀ-ÖØ-öø-ÿ0-9\u0900-\u097F]+$", "", part)
            if cleaned:
                cleaned_parts.append(cleaned)
        if not cleaned_parts:
            return False
        if len(cleaned_parts) == 1:
            return True
        first = cleaned_parts[0]
        if first.isascii() and first and not first[0].isupper():
            return False
        for part in cleaned_parts[1:]:
            if part.lower() in {"sir", "ji", "didi", "bhai", "madam", "maam", "amma", "appa", "mama", "baba"}:
                continue
            if part.isascii() and part and part[0].isupper():
                continue
            return False
        return True

    for line_number, raw_line in enumerate(script_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        if line.count(":") == 1:
            colon_index = line.index(":")
            left = line[:colon_index].strip()
            right = line[colon_index + 1:].strip()
            if left and right and not looks_like_speaker(left):
                prefix_part = left.rsplit(None, 1)
                last_token = prefix_part[-1] if prefix_part else ""
                if last_token and looks_like_speaker(last_token):
                    prefix = left[: left.rfind(last_token)].strip()
                    if prefix:
                        prefix_tokens = re.split(r"\s+", prefix)
                        first_token = prefix_tokens[0].strip(" :;,-") if prefix_tokens else ""
                        if first_token and looks_like_speaker(first_token):
                            leading_text = " ".join(prefix_tokens[1:]).strip()
                            if leading_text:
                                segments.append({"speaker": first_token, "text": leading_text, "line_number": line_number})
                            else:
                                segments.append({"speaker": "Narrator", "text": prefix, "line_number": line_number})
                        else:
                            segments.append({"speaker": "Narrator", "text": prefix, "line_number": line_number})
                    segments.append({"speaker": last_token, "text": right, "line_number": line_number})
                    continue

        matches = [match for match in re.finditer(speaker_pattern, line) if looks_like_speaker(match.group("speaker"))]
        if matches:
            for index, match in enumerate(matches):
                speaker = match.group("speaker").strip()
                text_start = match.end()
                text_end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                text = line[text_start:text_end].strip()
                if not speaker or not text:
                    raise SpeechServiceError(
                        f"Malformed dialogue line {line_number}: speaker and text are required.",
                        status_code=400,
                    )
                segments.append({"speaker": speaker, "text": text, "line_number": line_number})
            continue

        segments.append({"speaker": "Narrator", "text": line, "line_number": line_number})

    if not segments:
        raise SpeechServiceError("No valid dialogue lines were found in the script.", status_code=400)
    return segments


def combine_audio_segments(audio_segments):
    """Combine MP3 segments into a single audio stream when possible."""
    if not audio_segments:
        return b""
    if AudioSegment is not None:
        try:
            segments = [AudioSegment.from_file(BytesIO(chunk), format="mp3") for chunk in audio_segments if chunk]
            if not segments:
                raise ValueError("No parsable audio segments.")
            combined = segments[0]
            for segment in segments[1:]:
                combined += AudioSegment.silent(duration=120)
                combined += segment
            buffer = BytesIO()
            combined.export(buffer, format="mp3")
            return buffer.getvalue()
        except Exception:  # pragma: no cover - falls back cleanly when audio libs are unavailable.
            logger.warning("Falling back to simple MP3 concatenation for combined narration.")
    return b"".join(audio_segments)


def generate_script_audio(script_text, speaker_assignments=None, language="en", agent_name="storyteller"):
    """Choose a voice for each speaker using Gemini's gender classification."""
    segments = parse_script_lines(script_text)
    assignments = speaker_assignments or {}
    if not isinstance(assignments, dict):
        raise SpeechServiceError("Speaker assignments must be provided as an object.", status_code=400)

    primary_agent = (agent_name or "storyteller").strip().lower()
    get_agent_config(primary_agent)
    speakers = list(dict.fromkeys(segment["speaker"] for segment in segments))
    try:
        speaker_genders = detect_speaker_genders(script_text, speakers)
    except TranslationServiceError as error:
        raise SpeechServiceError(
            str(error),
            status_code=error.status_code,
            code="speaker_detection",
        ) from error

    generated = []
    for segment in segments:
        speaker = segment["speaker"]
        normalized_speaker = speaker.casefold()
        gender = speaker_genders[speaker]
        if normalized_speaker == "narrator":
            gender = "female"
        elif normalized_speaker == "rahul":
            gender = "male"
        speaker_agent = {
            "female": "storyteller",
            "male": "professional",
        }.get(gender, primary_agent)
        voice_id = assignments.get(speaker) or assignments.get(speaker.lower())
        generated.append(
            generate_speech(
                segment["text"],
                language,
                voice_id=voice_id,
                agent_name=speaker_agent,
            )
        )

    return combine_audio_segments(generated)