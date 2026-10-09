"""Focused tests for request validation and safe TTS API responses."""

import unittest
from unittest.mock import patch

import requests

from app import app
from services.elevenlabs_service import SpeechServiceError, generate_speech


class GenerateEndpointTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def post(self, payload):
        return self.client.post("/api/generate", json=payload)

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
        with patch("app.generate_speech", return_value=b"mp3-audio") as generate_speech:
            response = self.post({"text": "  Hello there.  ", "language": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.mimetype.startswith("audio/"))
        self.assertEqual(response.data, b"mp3-audio")
        generate_speech.assert_called_once_with("Hello there.", "en")

    def test_hides_missing_credential_details(self):
        with patch("app.generate_speech", side_effect=SpeechServiceError("missing key", 503)):
            response = self.post({"text": "नमस्ते", "language": "hi"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.json["error"])
        self.assertNotIn("missing key", response.json["error"])

    def test_preserves_rate_limit_status(self):
        with patch("app.generate_speech", side_effect=SpeechServiceError("rate limited", 429)):
            response = self.post({"text": "Hello", "language": "en"})
        self.assertEqual(response.status_code, 429)

    def test_hides_unexpected_service_failure(self):
        with patch("app.generate_speech", side_effect=RuntimeError("private details")):
            response = self.post({"text": "Hello", "language": "en"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private details", response.json["error"])


class ElevenLabsServiceTests(unittest.TestCase):
    @patch.dict("os.environ", {
        "ELEVENLABS_API_KEY": "test-key",
        "ELEVENLABS_VOICE_ID_EN": "voice-en",
        "ELEVENLABS_VOICE_ID_HI": "voice-hi",
    }, clear=True)
    @patch("services.elevenlabs_service.requests.post")
    def test_sends_hindi_voice_and_mp3_format(self, post):
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