"""Focused tests for request validation and safe TTS API responses."""

import json
import unittest
from unittest.mock import AsyncMock, patch

import requests

from app import app
from services.elevenlabs_service import (
    FREE_AGENT_VOICES,
    SpeechServiceError,
    _generate_free_agent_audio,
    generate_speech,
    generate_script_audio,
    resolve_voice_for_language,
)
from services.translation_service import (
    TranslationServiceError,
    _request_gemini,
    detect_speaker_genders,
    translate_text,
)


class GenerateEndpointTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def post(self, payload):
        return self.client.post("/api/generate", json=payload)

    def test_studio_has_no_translation_preview_control(self):
        with patch(
            "app.get_current_user",
            return_value={"id": 1, "name": "Test User", "email": "test@example.com"},
        ):
            response = self.client.get("/studio")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Translate &amp; Preview", response.data)
        self.assertNotIn(b"Translated text (editable)", response.data)
        self.assertNotIn(b"Cinematic Voice AI", response.data)
        self.assertIn(b"Microsoft Edge TTS", response.data)
        self.assertEqual(
            self.client.post(
                "/api/translate",
                json={"text": "नमस्ते", "language": "ta"},
            ).status_code,
            404,
        )
        self.assertNotIn(b"Gemini translation setup", response.data)
        self.assertEqual(
            self.client.post("/api/settings/gemini", json={"api_key": "test"}).status_code,
            404,
        )

    def test_root_always_starts_at_login_and_clears_existing_session(self):
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = 1

        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        with self.client.session_transaction() as current_session:
            self.assertNotIn("user_id", current_session)

    def test_studio_redirects_to_login_without_authenticated_user(self):
        response = self.client.get("/studio")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")

    def test_voice_config_offers_only_agents_with_supplied_voice_sets(self):
        response = self.client.get("/api/voice-config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json["agents"]),
            {"storyteller", "professional"},
        )

    def test_rejects_empty_text(self):
        response = self.post({"text": "  ", "language": "en"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Please enter", response.json["error"])

    def test_rejects_text_over_limit(self):
        response = self.post({"text": "x" * 5001, "language": "en"})
        self.assertEqual(response.status_code, 400)

    def test_rejects_unsupported_language_without_calling_service(self):
        with patch("app.generate_speech") as generate_speech:
            response = self.post({"text": "Hello", "language": "fr"})
        self.assertEqual(response.status_code, 400)
        generate_speech.assert_not_called()

    def test_rejects_non_json_request(self):
        response = self.client.post("/api/generate", data="hello")
        self.assertEqual(response.status_code, 400)

    def test_returns_generated_mp3(self):
        with patch("app.generate_speech", return_value=(b"mp3-audio", "elevenlabs")) as generate_speech:
            response = self.post({"text": "  Hello there.  ", "language": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.mimetype.startswith("audio/"))
        self.assertEqual(response.data, b"mp3-audio")
        self.assertEqual(response.headers["X-Speech-Provider"], "elevenlabs")
        generate_speech.assert_called_once_with(
            "Hello there.",
            "en",
            voice_id=None,
            agent_name=None,
            return_provider=True,
        )

    def test_hides_missing_credential_details(self):
        with patch("app.generate_speech", side_effect=SpeechServiceError("missing key", 503)):
            response = self.post({"text": "नमस्ते", "language": "hi"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.json["error"])
        self.assertNotIn("missing key", response.json["error"])

    def test_reports_translation_failure_instead_of_returning_wrong_language_audio(self):
        message = "Gemini could not find the configured model. Set GEMINI_MODEL to an available model in the local .env file."
        translation_error = SpeechServiceError(message, 503, code="translation")
        with patch("app.generate_speech", side_effect=translation_error):
            response = self.post({"text": "नमस्ते", "language": "ta"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["error"], message)

    def test_reports_plan_restricted_agent_voice_without_substituting_another_voice(self):
        voice_error = SpeechServiceError(
            "ElevenLabs does not allow this agent voice on the current plan.",
            402,
        )
        with patch("app.generate_speech", side_effect=voice_error):
            response = self.post({
                "text": "Hello",
                "language": "en",
                "agent": "storyteller",
            })

        self.assertEqual(response.status_code, 402)
        self.assertIn("does not allow this agent voice", response.json["error"])

    def test_generate_automatically_translates_original_text(self):
        original_text = "नमस्ते"
        with patch("app.generate_speech", return_value=(b"mp3-audio", "elevenlabs")) as service:
            response = self.post({
                "text": original_text,
                "language": "ta",
                "agent": "professional",
            })

        self.assertEqual(response.status_code, 200)
        service.assert_called_once_with(
            original_text,
            "ta",
            voice_id=None,
            agent_name="professional",
            return_provider=True,
        )

    def test_preserves_rate_limit_status(self):
        with patch("app.generate_speech", side_effect=SpeechServiceError("rate limited", 429)):
            response = self.post({"text": "Hello", "language": "en"})
        self.assertEqual(response.status_code, 429)

    def test_returns_safe_speech_provider_error(self):
        message = "Could not reach ElevenLabs. Check your internet connection and try again."
        with patch("app.generate_speech", side_effect=SpeechServiceError(message, 502)):
            response = self.post({"text": "Hello", "language": "en"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["error"], message)

    def test_hides_unexpected_service_failure(self):
        with patch("app.generate_speech", side_effect=RuntimeError("private details")):
            response = self.post({"text": "Hello", "language": "en"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private details", response.json["error"])


class ElevenLabsServiceTests(unittest.TestCase):
    def setUp(self):
        self.api_key_patch = patch.dict(
            "os.environ",
            {"GEMINI_API_KEY": "test-gemini-key", "GEMINI_MODEL": "test-model"},
        )
        self.api_key_patch.start()
        self.addCleanup(self.api_key_patch.stop)
        self.translation_request_patch = patch("services.translation_service._request_gemini")
        self.translation_request = self.translation_request_patch.start()
        self.addCleanup(self.translation_request_patch.stop)
        self.detection_patch = patch("services.translation_service._detect_source_language")
        self.detect_source = self.detection_patch.start()
        self.addCleanup(self.detection_patch.stop)
        self.detect_source.return_value = ("hi", "Hindi")
        self.translation_request.return_value.status_code = 200
        self.translation_request.return_value.json.return_value = {
            "steps": [{
                "type": "model_output",
                "content": [{"type": "text", "text": "Gemini translated text"}],
            }]
        }

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "STORYTELLER_VOICE_ID_EN": "story-en",
        "STORYTELLER_VOICE_ID_HI": "story-hi",
        "STORYTELLER_VOICE_ID_TA": "story-ta",
        "STORYTELLER_VOICE_ID_ES": "story-es",
        "PROFESSIONAL_VOICE_ID_EN": "professional-en",
        "PROFESSIONAL_VOICE_ID_HI": "professional-hi",
        "PROFESSIONAL_VOICE_ID_TA": "professional-ta",
        "PROFESSIONAL_VOICE_ID_ES": "professional-es",
    }, clear=True)
    @patch("services.elevenlabs_service.requests.post")
    def test_each_agent_uses_its_own_voice_for_every_language(self, post):
        post.return_value.status_code = 200
        post.return_value.ok = True
        post.return_value.content = b"mp3-audio"
        expected_voices = {
            ("storyteller", "en"): "story-en",
            ("storyteller", "hi"): "story-hi",
            ("storyteller", "ta"): "story-ta",
            ("storyteller", "es"): "story-es",
            ("professional", "en"): "professional-en",
            ("professional", "hi"): "professional-hi",
            ("professional", "ta"): "professional-ta",
            ("professional", "es"): "professional-es",
        }

        for (agent_name, language), voice_id in expected_voices.items():
            with self.subTest(agent=agent_name, language=language):
                post.reset_mock()
                generate_speech("Hello", language, agent_name=agent_name)
                self.assertTrue(post.call_args.args[0].endswith(f"/{voice_id}"))

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "STORYTELLER_VOICE_ID_EN": "shared-voice",
        "PROFESSIONAL_VOICE_ID_EN": "shared-voice",
        "PROFESSIONAL_VOICE_ID_TA": "professional-ta",
    }, clear=True)
    def test_rejects_duplicate_agent_voice_configuration(self):
        with self.assertRaises(SpeechServiceError) as context:
            resolve_voice_for_language("en", agent_name="storyteller")

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.code, "voice_agent_config")
        self.assertIn("different ElevenLabs voice ID", str(context.exception))

    def test_rejects_removed_cinematic_agent(self):
        with self.assertRaises(SpeechServiceError) as context:
            resolve_voice_for_language("en", agent_name="cinematic")

        self.assertEqual(context.exception.status_code, 400)

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "ELEVENLABS_VOICE_ID_EN": "voice-en",
        "ELEVENLABS_VOICE_ID_HI": "voice-hi",
    }, clear=True)
    @patch("services.elevenlabs_service.requests.post")
    def test_sends_hindi_voice_and_mp3_format(self, post):
        self.detect_source.return_value = ("hi", "Hindi")
        post.return_value.status_code = 200
        post.return_value.ok = True
        post.return_value.content = b"mp3-audio"

        audio = generate_speech("नमस्ते", "hi")

        self.assertEqual(audio, b"mp3-audio")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.elevenlabs.io/v1/text-to-speech/voice-hi")
        self.assertEqual(kwargs["headers"]["xi-api-key"], "test-key")
        self.assertEqual(kwargs["json"]["model_id"], "eleven_multilingual_v2")
        self.assertNotIn("language_code", kwargs["json"])
        self.assertNotIn("output_format", kwargs["json"])
        self.assertEqual(kwargs["params"], {"output_format": "mp3_44100_128"})

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "ELEVENLABS_VOICE_ID_TA": "voice-ta",
    }, clear=True)
    @patch("services.elevenlabs_service.requests.post")
    def test_translates_hindi_input_to_tamil_before_elevenlabs(self, post):
        tamil_text = "வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்?"
        self.translation_request.return_value.json.return_value["steps"][0]["content"][0]["text"] = tamil_text
        post.return_value.status_code = 200
        post.return_value.ok = True
        post.return_value.content = b"mp3-audio"

        audio = generate_speech("नमस्ते, आप कैसे हैं?", "ta")

        self.assertEqual(audio, b"mp3-audio")
        self.detect_source.assert_called_once_with("नमस्ते, आप कैसे हैं?")
        translation_args = self.translation_request.call_args
        self.assertEqual(
            translation_args.args[0],
            "https://generativelanguage.googleapis.com/v1beta/interactions",
        )
        self.assertEqual(translation_args.args[1], "test-gemini-key")
        request_payload = translation_args.args[2]
        self.assertEqual(request_payload["model"], "test-model")
        self.assertIn("Tamil", request_payload["system_instruction"])
        self.assertEqual(request_payload["input"], "नमस्ते, आप कैसे हैं?")
        self.assertFalse(request_payload["store"])
        self.assertEqual(post.call_args.kwargs["json"]["text"], tamil_text)
        self.assertIn("/voice-ta", post.call_args.args[0])

    def test_translation_uses_each_selected_speech_language_as_target(self):
        samples = [
            ("Hello friends, welcome to my studio.", "en", "hi"),
            ("नमस्ते, आप कैसे हैं?", "hi", "en"),
            ("नमस्ते, आप कैसे हैं?", "hi", "ta"),
            ("नमस्ते, आप कैसे हैं?", "hi", "es"),
            ("Hello friends, welcome to my studio.", "en", "ta"),
            ("Hola, ¿cómo estás?", "es", "hi"),
            ("வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்?", "ta", "es"),
        ]
        for text, source, target in samples:
            with self.subTest(source=source, target=target):
                self.translation_request.reset_mock()
                self.translation_request.return_value.json.return_value["steps"][0][
                    "content"
                ][0]["text"] = f"translated-{target}"
                self.detect_source.return_value = (source, source.upper())
                result = translate_text(text, target)

                self.assertEqual(result.text, f"translated-{target}")
                self.assertEqual(result.source_language, source)
                self.assertEqual(result.target_language, target)
                request_payload = self.translation_request.call_args.args[2]
                self.assertIn(
                    {"en": "English", "hi": "Hindi", "ta": "Tamil", "es": "Spanish"}[
                        target
                    ],
                    request_payload["system_instruction"],
                )
                self.assertEqual(request_payload["input"], text)

    def test_same_language_input_skips_translation_provider(self):
        self.detect_source.return_value = ("es", "Spanish")

        result = translate_text("Hola, ¿cómo estás?", "es")

        self.assertEqual(result.text, "Hola, ¿cómo estás?")
        self.assertFalse(result.translated)
        self.translation_request.assert_not_called()

    def test_short_common_same_language_input_skips_translation_provider(self):
        result = translate_text("Hola", "es")

        self.assertEqual(result.source_language, "es")
        self.assertEqual(result.text, "Hola")
        self.assertFalse(result.translated)
        self.translation_request.assert_not_called()
        self.detect_source.assert_not_called()

    def test_english_already_in_target_is_not_sent_for_translation(self):
        self.detect_source.return_value = ("en", "English")
        text = "Hello friends, welcome to my AI Voice Studio."

        result = translate_text(text, "en")

        self.assertEqual(result.text, text)
        self.assertFalse(result.translated)
        self.translation_request.assert_not_called()

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "ELEVENLABS_VOICE_ID_TA": "voice-ta",
    }, clear=True)
    @patch("services.elevenlabs_service.gTTS")
    @patch("services.elevenlabs_service.requests.post")
    def test_free_fallback_speaks_already_translated_tamil(self, post, tts_factory):
        tamil_text = "வணக்கம்"
        self.translation_request.return_value.json.return_value["steps"][0]["content"][0]["text"] = tamil_text
        tts_factory.return_value.write_to_fp.side_effect = lambda buffer: buffer.write(b"free-audio")
        post.return_value.status_code = 402
        post.return_value.ok = False
        post.return_value.text = "Paid plan required"

        audio, provider = generate_speech("नमस्ते", "ta", return_provider=True)

        self.assertEqual(audio, b"free-audio")
        self.assertEqual(provider, "free-gtts")
        tts_factory.assert_called_once_with(text=tamil_text, lang="ta", slow=False)
        self.assertEqual(
            self.translation_request.call_args.args[2]["input"],
            "नमस्ते",
        )

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "ELEVENLABS_VOICE_ID_EN": "voice-en",
    }, clear=True)
    @patch("services.elevenlabs_service.gTTS")
    @patch("services.elevenlabs_service.requests.post")
    def test_google_tts_fallback_failure_is_reported_safely(self, post, tts_factory):
        post.return_value.status_code = 402
        post.return_value.ok = False
        post.return_value.text = "Paid plan required"
        tts_factory.return_value.write_to_fp.side_effect = requests.ConnectionError(
            "private provider details"
        )

        with self.assertRaises(SpeechServiceError) as context:
            generate_speech("Hello", "en")

        self.assertEqual(context.exception.status_code, 502)
        self.assertIn("Free speech generation is temporarily unavailable", str(context.exception))
        self.assertNotIn("private provider details", str(context.exception))

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "STORYTELLER_VOICE_ID_EN": "story-voice",
    }, clear=True)
    @patch("services.elevenlabs_service._generate_free_agent_audio", return_value=b"edge-audio")
    @patch("services.elevenlabs_service.requests.post")
    def test_plan_restricted_agent_uses_the_selected_free_agent_voice(
        self,
        post,
        free_tts,
    ):
        post.return_value.status_code = 402
        post.return_value.ok = False
        post.return_value.text = "Paid plan required"

        audio, provider = generate_speech(
            "Hello",
            "en",
            agent_name="storyteller",
            return_provider=True,
        )

        self.assertEqual(audio, b"edge-audio")
        self.assertEqual(provider, "free-edge-tts")
        free_tts.assert_called_once_with("Hello", "en", "storyteller")

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "STORYTELLER_VOICE_ID_EN": "story-voice",
    }, clear=True)
    @patch("services.elevenlabs_service._generate_free_agent_audio", return_value=b"edge-audio")
    @patch("services.elevenlabs_service.requests.post")
    def test_agent_uses_free_voice_when_elevenlabs_is_unreachable(
        self,
        post,
        free_tts,
    ):
        for error in (requests.ConnectionError("offline"), requests.Timeout("timed out")):
            with self.subTest(error=type(error).__name__):
                post.side_effect = error

                audio, provider = generate_speech(
                    "Hello",
                    "en",
                    agent_name="storyteller",
                    return_provider=True,
                )

                self.assertEqual((audio, provider), (b"edge-audio", "free-edge-tts"))
                free_tts.assert_called_with("Hello", "en", "storyteller")
                post.reset_mock()

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "STORYTELLER_VOICE_ID_EN": "story-voice",
    }, clear=True)
    @patch("services.elevenlabs_service._generate_free_agent_audio", return_value=b"edge-audio")
    @patch("services.elevenlabs_service.requests.post")
    def test_agent_uses_free_voice_when_elevenlabs_is_busy_or_unavailable(
        self,
        post,
        free_tts,
    ):
        for status_code in (429, 500, 503):
            with self.subTest(status_code=status_code):
                post.side_effect = None
                post.return_value.status_code = status_code
                post.return_value.ok = False
                post.return_value.text = "provider unavailable"

                audio, provider = generate_speech(
                    "Hello",
                    "en",
                    agent_name="storyteller",
                    return_provider=True,
                )

                self.assertEqual((audio, provider), (b"edge-audio", "free-edge-tts"))
                free_tts.assert_called_with("Hello", "en", "storyteller")

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "STORYTELLER_VOICE_ID_HI": "story-hi",
    }, clear=True)
    @patch("services.elevenlabs_service._generate_free_agent_audio", return_value=b"edge-audio")
    @patch("services.elevenlabs_service.requests.post")
    def test_agent_voice_language_rejection_uses_selected_free_voice(self, post, free_tts):
        post.return_value.status_code = 400
        post.return_value.ok = False
        post.return_value.text = "Voice does not support this language"

        audio, provider = generate_speech(
            "कल मैं स्कूल जाऊंगा",
            "hi",
            agent_name="storyteller",
            return_provider=True,
        )

        self.assertEqual(audio, b"edge-audio")
        self.assertEqual(provider, "free-edge-tts")
        free_tts.assert_called_once_with("कल मैं स्कूल जाऊंगा", "hi", "storyteller")

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
    }, clear=True)
    @patch("services.elevenlabs_service._generate_free_agent_audio", return_value=b"edge-audio")
    @patch("services.elevenlabs_service.requests.post")
    def test_agent_uses_free_voice_when_elevenlabs_is_not_configured(self, post, free_tts):
        audio, provider = generate_speech(
            "Hello",
            "en",
            agent_name="professional",
            return_provider=True,
        )

        self.assertEqual(audio, b"edge-audio")
        self.assertEqual(provider, "free-edge-tts")
        free_tts.assert_called_once_with("Hello", "en", "professional")
        post.assert_not_called()

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "STORYTELLER_VOICE_ID_EN": "story-en",
    }, clear=True)
    @patch("services.elevenlabs_service.detect_speaker_genders", return_value={"Narrator": "female"})
    @patch("services.elevenlabs_service._generate_free_agent_audio", return_value=b"edge-audio")
    @patch("services.elevenlabs_service.requests.post")
    def test_multispeaker_narrator_uses_free_agent_after_plan_rejection(self, post, free_tts, _detect):
        post.return_value.status_code = 402
        post.return_value.ok = False
        post.return_value.text = "Paid plan required"

        audio = generate_script_audio(
            "kal mai school jaunga",
            language="en",
            agent_name="storyteller",
        )

        self.assertEqual(audio, b"edge-audio")
        free_tts.assert_called_once_with(
            "Gemini translated text",
            "en",
            "storyteller",
        )

    @patch("services.elevenlabs_service.detect_speaker_genders", return_value={
        "Narrator": "male",
        "Rahul": "female",
    })
    @patch("services.elevenlabs_service.combine_audio_segments", side_effect=lambda segments: segments)
    @patch("services.elevenlabs_service.generate_speech", side_effect=[b"narrator", b"rahul", b"narrator-again"])
    def test_multispeaker_uses_female_narrator_and_male_rahul_voices(self, generate_speech, _combine, detect):
        audio = generate_script_audio(
            "Narrator: Once upon a time.\nRahul: Hello there.\nNarrator: The end.",
            language="hi",
            agent_name="storyteller",
        )

        self.assertEqual(audio, [b"narrator", b"rahul", b"narrator-again"])
        calls = generate_speech.call_args_list
        self.assertEqual([call.kwargs["agent_name"] for call in calls], [
            "storyteller",
            "professional",
            "storyteller",
        ])
        self.assertEqual([call.kwargs["voice_id"] for call in calls], [None, None, None])
        detect.assert_called_once_with(
            "Narrator: Once upon a time.\nRahul: Hello there.\nNarrator: The end.",
            ["Narrator", "Rahul"],
        )
        _combine.assert_called_once_with([b"narrator", b"rahul", b"narrator-again"])

    @patch("services.elevenlabs_service.detect_speaker_genders", return_value={
        "Narrator": "female",
        "Friend": "male",
    })
    @patch("services.elevenlabs_service.combine_audio_segments", side_effect=lambda segments: segments)
    @patch("services.elevenlabs_service.generate_speech", side_effect=[b"narrator", b"rahul"])
    def test_multispeaker_uses_gemini_gender_for_unmapped_speaker(self, generate_speech, _combine, _detect):
        audio = generate_script_audio(
            "Narrator: Once upon a time.\nFriend: Hello there.",
            language="hi",
            agent_name="storyteller",
        )

        self.assertEqual(audio, [b"narrator", b"rahul"])
        self.assertEqual(
            [call.kwargs["agent_name"] for call in generate_speech.call_args_list],
            ["storyteller", "professional"],
        )
        _combine.assert_called_once_with([b"narrator", b"rahul"])

    @patch("services.elevenlabs_service.detect_speaker_genders", return_value={
        "Narrator": "female",
        "Rahul": "male",
    })
    @patch("services.elevenlabs_service.combine_audio_segments", side_effect=lambda segments: segments)
    @patch("services.elevenlabs_service.generate_speech", side_effect=[b"narrator", b"rahul"])
    def test_multispeaker_supports_inline_speaker_labels(self, generate_speech, _combine, _detect):
        audio = generate_script_audio(
            "Narrator isme ladki hi bolni chahiye Rahul: isme ladka hi bolna chahiye",
            language="hi",
            agent_name="storyteller",
        )

        self.assertEqual(audio, [b"narrator", b"rahul"])
        self.assertEqual(
            [call.kwargs["agent_name"] for call in generate_speech.call_args_list],
            ["storyteller", "professional"],
        )
        self.assertEqual(
            [call.args[0] for call in generate_speech.call_args_list],
            ["isme ladki hi bolni chahiye", "isme ladka hi bolna chahiye"],
        )
        _combine.assert_called_once_with([b"narrator", b"rahul"])

    @patch("services.elevenlabs_service.detect_speaker_genders", return_value={
        "Narrator": "female",
        "Rahul": "male",
    })
    @patch("services.elevenlabs_service.combine_audio_segments", side_effect=lambda segments: segments)
    @patch("services.elevenlabs_service.generate_speech", side_effect=[b"narrator", b"rahul"])
    def test_multispeaker_honors_each_explicit_voice_assignment(self, generate_speech, _combine, _detect):
        generate_script_audio(
            "Narrator: Once upon a time.\nRahul: Hello there.",
            speaker_assignments={"Narrator": "custom-narrator", "Rahul": "custom-rahul"},
            language="en",
            agent_name="storyteller",
        )

        self.assertEqual(
            [call.kwargs["voice_id"] for call in generate_speech.call_args_list],
            ["custom-narrator", "custom-rahul"],
        )
        _combine.assert_called_once_with([b"narrator", b"rahul"])

    @patch("app.generate_script_audio", return_value=b"mp3-audio")
    def test_multispeaker_endpoint_returns_audio(self, generate_script_audio):
        app.config["TESTING"] = True
        response = app.test_client().post(
            "/api/multispeaker",
            json={
                "script": "Narrator: Hello there.",
                "language": "en",
                "agent": "storyteller",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"mp3-audio")
        generate_script_audio.assert_called_once_with(
            "Narrator: Hello there.",
            speaker_assignments={},
            language="en",
            agent_name="storyteller",
        )

    def test_free_agent_voices_are_distinct_for_all_supported_languages(self):
        with patch(
            "services.elevenlabs_service._stream_edge_tts_audio",
            new_callable=AsyncMock,
            return_value=b"edge-audio",
        ) as stream_audio:
            for agent_name, language_voices in FREE_AGENT_VOICES.items():
                for language, voice in language_voices.items():
                    with self.subTest(agent=agent_name, language=language):
                        stream_audio.reset_mock()
                        audio = _generate_free_agent_audio("Hello", language, agent_name)
                        self.assertEqual(audio, b"edge-audio")
                        stream_audio.assert_awaited_once_with("Hello", voice)

        for language in ("en", "hi", "ta", "es"):
            self.assertNotEqual(
                FREE_AGENT_VOICES["storyteller"][language],
                FREE_AGENT_VOICES["professional"][language],
            )

    def test_missing_edge_tts_does_not_prevent_app_startup(self):
        with patch("services.elevenlabs_service.edge_tts", None):
            with self.assertRaises(SpeechServiceError) as context:
                _generate_free_agent_audio("Hello", "en", "storyteller")

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.code, "speech_dependency")
        self.assertIn("pip install -r requirements.txt", str(context.exception))

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "test-gemini-key",
        "GEMINI_MODEL": "test-model",
        "ELEVENLABS_API_KEY": "test-key",
        "PROFESSIONAL_VOICE_ID_TA": "professional-voice",
    }, clear=True)
    @patch("services.elevenlabs_service.requests.post")
    def test_automatically_translates_before_using_selected_agent_voice(self, post):
        post.return_value.status_code = 200
        post.return_value.ok = True
        post.return_value.content = b"mp3-audio"
        original_text = "नमस्ते, आप कैसे हैं?"
        translated_text = "வணக்கம், நலமா?"
        self.detect_source.return_value = ("hi", "Hindi")
        self.translation_request.return_value.json.return_value["steps"][0]["content"][0][
            "text"
        ] = translated_text

        audio = generate_speech(
            original_text,
            "ta",
            agent_name="professional",
        )

        self.assertEqual(audio, b"mp3-audio")
        self.assertIn("/professional-voice", post.call_args.args[0])
        self.assertEqual(
            self.translation_request.call_args.args[2]["input"],
            original_text,
        )
        self.assertEqual(post.call_args.kwargs["json"]["text"], translated_text)

    def test_translation_failure_does_not_return_original_text(self):
        self.translation_request.side_effect = requests.ConnectionError("translation unavailable")

        with self.assertRaises(SpeechServiceError) as context:
            generate_speech("नमस्ते", "ta")

        self.assertEqual(context.exception.status_code, 502)
        self.assertEqual(context.exception.code, "translation")

    def test_translation_timeout_has_gateway_timeout_status(self):
        self.translation_request.side_effect = requests.Timeout("Gemini timeout")

        with self.assertRaises(TranslationServiceError) as context:
            translate_text("नमस्ते", "ta")

        self.assertEqual(context.exception.status_code, 504)

    def test_gemini_returns_translation(self):
        self.translation_request.return_value.json.return_value["steps"][0]["content"][0]["text"] = "வணக்கம்"
        result = translate_text("नमस्ते", "ta")

        self.assertEqual(result.text, "வணக்கம்")
        self.assertEqual(
            self.translation_request.call_args.args[1],
            "test-gemini-key",
        )

    def test_gemini_classifies_speaker_voice_gender(self):
        self.translation_request.return_value.json.return_value["steps"][0]["content"][0]["text"] = (
            '{"Narrator": "female", "Rahul": "male"}'
        )

        result = detect_speaker_genders(
            "Narrator: Once upon a time.\nRahul: Hello there.",
            ["Narrator", "Rahul"],
        )

        self.assertEqual(result, {"Narrator": "female", "Rahul": "male"})
        request_payload = self.translation_request.call_args.args[2]
        self.assertIn("voice gender", request_payload["system_instruction"])
        self.assertEqual(
            json.loads(request_payload["input"]),
            {
                "script": "Narrator: Once upon a time.\nRahul: Hello there.",
                "speakers": ["Narrator", "Rahul"],
            },
        )

    def test_uses_configured_gemini_translation_model(self):
        self.translation_request.return_value.json.return_value["steps"][0]["content"][0]["text"] = "வணக்கம்"

        translated = translate_text("नमस्ते", "ta")

        self.assertEqual(translated.text, "வணக்கம்")
        self.translation_request.assert_called_once_with(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            "test-gemini-key",
            self.translation_request.call_args.args[2],
        )
        self.assertFalse(self.translation_request.call_args.args[2]["store"])

    def test_defaults_to_free_tier_gemini_flash_lite_model(self):
        self.translation_request.return_value.json.return_value["steps"][0]["content"][0]["text"] = "வணக்கம்"
        with patch.dict("os.environ", {
            "GEMINI_API_KEY": "test-gemini-key",
        }, clear=True):
            translated = translate_text("नमस्ते", "ta")

        self.assertEqual(translated.text, "வணக்கம்")
        payload = self.translation_request.call_args.args[2]
        self.assertEqual(payload["model"], "gemini-2.5-flash")

    def test_reports_unavailable_gemini_model(self):
        self.translation_request.return_value.status_code = 404

        with self.assertRaises(TranslationServiceError) as context:
            translate_text("नमस्ते", "ta")

        self.assertEqual(context.exception.status_code, 503)
        self.assertIn("configured model", str(context.exception))

    def test_requires_gemini_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(TranslationServiceError) as context:
                translate_text("नमस्ते", "ta")

        self.assertEqual(context.exception.status_code, 503)
        self.translation_request.assert_not_called()

    def test_maps_gemini_quota_response_to_rate_limit(self):
        self.translation_request.return_value.status_code = 429

        with self.assertRaises(TranslationServiceError) as context:
            translate_text("नमस्ते", "ta")

        self.assertEqual(context.exception.status_code, 429)

    def test_maps_rejected_gemini_api_key_to_configuration_error(self):
        self.translation_request.return_value.status_code = 403

        with self.assertRaises(TranslationServiceError) as context:
            translate_text("नमस्ते", "ta")

        self.assertEqual(context.exception.status_code, 503)

    def test_rejects_gemini_response_without_text_output(self):
        self.translation_request.return_value.json.return_value = {
            "steps": [
                {"type": "thought", "signature": "internal"},
                {"type": "model_output", "content": [{"type": "image"}]},
            ]
        }

        with self.assertRaises(TranslationServiceError) as context:
            translate_text("नमस्ते", "ta")

        self.assertEqual(context.exception.status_code, 502)

    @patch("services.translation_service.requests.post")
    def test_sends_api_key_in_header_and_keeps_it_out_of_url(self, request_post):
        request_post.return_value = object()
        payload = {"model": "test-model", "input": "नमस्ते"}

        _request_gemini(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            "test-gemini-key",
            payload,
        )

        request_post.assert_called_once_with(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            headers={
                "x-goog-api-key": "test-gemini-key",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(10, 60),
        )

    def test_spanish_target_uses_auto_detect_for_non_spanish_input(self):
        self.translation_request.return_value.json.return_value["steps"][0]["content"][0]["text"] = "Hola"
        self.detect_source.return_value = ("ta", "Tamil")

        translated = translate_text("வணக்கம்", "es")

        self.assertEqual(translated.text, "Hola")
        self.assertIn(
            "Spanish",
            self.translation_request.call_args.args[2]["system_instruction"],
        )

    @patch.dict("os.environ", {
        "ELEVENLABS_API_KEY": "test-key",
        "ELEVENLABS_VOICE_ID_EN": "voice-en",
        "ELEVENLABS_VOICE_ID_HI": "voice-hi",
    }, clear=True)
    @patch("services.elevenlabs_service.requests.post")
    def test_selects_english_voice_without_unsupported_language_code(self, post):
        post.return_value.status_code = 200
        post.return_value.ok = True
        post.return_value.content = b"mp3-audio"

        generate_speech("Hello", "en")

        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.elevenlabs.io/v1/text-to-speech/voice-en")
        self.assertEqual(kwargs["json"]["model_id"], "eleven_multilingual_v2")
        self.assertNotIn("language_code", kwargs["json"])

    @patch.dict("os.environ", {
        "ELEVENLABS_API_KEY": "test-key",
        "ELEVENLABS_VOICE_ID_EN": "voice-en",
    }, clear=True)
    @patch("services.elevenlabs_service.requests.post")
    def test_maps_invalid_api_credentials(self, post):
        post.return_value.status_code = 401
        post.return_value.ok = False
        post.return_value.text = "Unauthorized test-key voice-en Hello"

        with self.assertLogs("services.elevenlabs_service", level="WARNING") as logs:
            with self.assertRaises(SpeechServiceError) as context:
                generate_speech("Hello", "en")

        self.assertEqual(context.exception.status_code, 401)
        logged_error = " ".join(logs.output)
        self.assertNotIn("test-key", logged_error)
        self.assertNotIn("voice-en", logged_error)
        self.assertNotIn("Hello", logged_error)

    @patch.dict("os.environ", {
        "ELEVENLABS_API_KEY": "test-key",
        "ELEVENLABS_VOICE_ID_EN": "voice-en",
    }, clear=True)
    @patch("services.elevenlabs_service.requests.post")
    def test_rejects_missing_hindi_voice_without_calling_api(self, post):
        with self.assertRaises(SpeechServiceError) as context:
            generate_speech("नमस्ते", "hi")

        self.assertEqual(context.exception.status_code, 503)
        post.assert_not_called()

    @patch.dict("os.environ", {
        "ELEVENLABS_API_KEY": "test-key",
        "ELEVENLABS_VOICE_ID_EN": "voice-en",
    }, clear=True)
    @patch("services.elevenlabs_service.requests.post", side_effect=requests.ConnectionError)
    def test_maps_network_failure(self, post):
        with self.assertRaises(SpeechServiceError) as context:
            generate_speech("Hello", "en")

        self.assertEqual(context.exception.status_code, 502)
        post.assert_called_once()

    @patch.dict("os.environ", {}, clear=True)
    @patch("services.elevenlabs_service.requests.post")
    def test_rejects_missing_key_without_calling_api(self, post):
        with self.assertRaises(SpeechServiceError) as context:
            generate_speech("Hello", "en")

        self.assertEqual(context.exception.status_code, 503)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()