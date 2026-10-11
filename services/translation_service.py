"""Automatic language detection and translation for speech generation."""

import logging
import os
from dataclasses import dataclass

import requests

try:
    from langdetect import DetectorFactory, LangDetectException, detect_langs
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt.
    LangDetectException = Exception
    detect_langs = None
else:
    DetectorFactory.seed = 0

logger = logging.getLogger(__name__)

OUTPUT_LANGUAGES = {
    "en": {"name": "English"},
    "hi": {"name": "Hindi"},
    "ta": {"name": "Tamil"},
    "es": {"name": "Spanish"},
}
SOURCE_LANGUAGE_NAMES = {
    "ar": "Arabic",
    "bn": "Bengali",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "gu": "Gujarati",
    "hi": "Hindi",
    "it": "Italian",
    "ja": "Japanese",
    "kn": "Kannada",
    "ko": "Korean",
    "ml": "Malayalam",
    "mr": "Marathi",
    "ne": "Nepali",
    "pa": "Punjabi",
    "pt": "Portuguese",
    "ru": "Russian",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
    "zh-cn": "Chinese",
    "zh-tw": "Chinese",
}
MAX_TRANSLATION_LENGTH = 5000
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


class TranslationServiceError(Exception):
    """A safe-to-return error from translation or language validation."""

    def __init__(self, message, status_code=502, code="translation"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class TranslationResult:
    text: str
    source_language: str
    source_language_name: str
    target_language: str
    target_language_name: str
    translated: bool

    def as_dict(self):
        return {
            "text": self.text,
            "source_language": {
                "code": self.source_language,
                "name": self.source_language_name,
            },
            "target_language": {
                "code": self.target_language,
                "name": self.target_language_name,
            },
            "translated": self.translated,
        }


def _detect_source_language(text):
    if detect_langs is None:
        raise TranslationServiceError(
            "Language detection is not installed.",
            status_code=503,
            code="translation",
        )
    try:
        candidates = detect_langs(text)
    except LangDetectException:
        return "und", "Unknown"
    if not candidates:
        return "und", "Unknown"
    language_code = candidates[0].lang.lower()
    if candidates[0].prob < 0.7:
        return "und", "Unknown"
    return language_code, SOURCE_LANGUAGE_NAMES.get(language_code, language_code.upper())


def _detect_short_common_language(text):
    normalized = " ".join(text.casefold().split())
    common_phrases = {
        "en": {"hello", "hello there", "hi", "good morning", "good evening", "thank you"},
        "es": {"hola", "buenos días", "buenas tardes", "buenas noches", "gracias"},
    }
    for language_code, phrases in common_phrases.items():
        if normalized in phrases:
            return language_code, OUTPUT_LANGUAGES[language_code]["name"]
    return None


def _request_gemini(url, api_key, payload):
    return requests.post(
        url,
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=(10, 60),
    )


def _translate_with_provider(text, target_language):
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_gemini_api_key_here":
        raise TranslationServiceError(
            "Gemini translation is not configured. Add GEMINI_API_KEY to the local .env file.",
            status_code=503,
        )

    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    if not model:
        raise TranslationServiceError(
            "Gemini translation model is not configured.",
            status_code=503,
        )

    target_name = OUTPUT_LANGUAGES[target_language]["name"]
    url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    payload = {
        "model": model,
        "system_instruction": (
            f"Translate the user's text into {target_name}. "
            "Preserve its meaning and tone. Return only the translation, "
            "without explanations, labels, or quotation marks."
        ),
        "input": text,
        "generation_config": {"temperature": 0},
        "store": False,
    }

    try:
        response = _request_gemini(url, api_key, payload)
    except requests.Timeout as error:
        raise TranslationServiceError(
            "Gemini translation timed out. Please try again.",
            status_code=504,
        ) from error
    except requests.RequestException as error:
        raise TranslationServiceError(
            "Gemini translation is unavailable. Please try again.",
            status_code=502,
        ) from error

    if response.status_code == 429:
        raise TranslationServiceError(
            "Gemini translation is busy or its quota has been reached. Please try again later.",
            status_code=429,
        )
    if response.status_code in (401, 403):
        raise TranslationServiceError(
            "Gemini rejected the API key. Check GEMINI_API_KEY in the local .env file.",
            status_code=503,
        )
    if response.status_code == 404:
        raise TranslationServiceError(
            "Gemini could not find the configured model. Set GEMINI_MODEL to an available model in the local .env file.",
            status_code=503,
        )
    if response.status_code >= 400:
        raise TranslationServiceError(
            "Gemini could not translate the text. Please check your model and try again.",
            status_code=502,
        )

    try:
        result = response.json()
    except ValueError as error:
        raise TranslationServiceError(
            "Gemini returned an invalid response.",
            status_code=502,
        ) from error

    steps = result.get("steps") if isinstance(result, dict) else None
    if not isinstance(steps, list):
        raise TranslationServiceError(
            "Gemini did not return a translation. Please try again.",
            status_code=502,
        )
    translated = "".join(
        part["text"]
        for step in steps
        if (
            isinstance(step, dict)
            and step.get("type") == "model_output"
            and isinstance(step.get("content"), list)
        )
        for part in step["content"]
        if (
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )
    ).strip()
    if not translated:
        raise TranslationServiceError(
            "Gemini returned an empty translation.",
            status_code=502,
        )
    return translated


def translate_text(text, target_language):
    if not isinstance(text, str) or not text.strip():
        raise TranslationServiceError("Please enter some text first.", status_code=400)
    if len(text) > MAX_TRANSLATION_LENGTH:
        raise TranslationServiceError("Text cannot exceed 5000 characters.", status_code=400)
    if target_language not in OUTPUT_LANGUAGES:
        raise TranslationServiceError("Choose a supported output language.", status_code=400)

    cleaned = text.strip()
    target = OUTPUT_LANGUAGES[target_language]
    detected = _detect_short_common_language(cleaned)
    source_code, source_name = detected or _detect_source_language(cleaned)
    if source_code == target_language:
        return TranslationResult(
            text=cleaned,
            source_language=source_code,
            source_language_name=source_name,
            target_language=target_language,
            target_language_name=target["name"],
            translated=False,
        )

    try:
        translated = _translate_with_provider(cleaned, target_language).strip()
    except TranslationServiceError as error:
        logger.warning(
            "Translation to %s failed (%s).",
            target_language,
            type(error).__name__,
        )
        raise
    if not translated:
        raise TranslationServiceError("Translation service returned empty text.")

    return TranslationResult(
        text=translated,
        source_language=source_code,
        source_language_name=source_name,
        target_language=target_language,
        target_language_name=target["name"],
        translated=True,
    )
