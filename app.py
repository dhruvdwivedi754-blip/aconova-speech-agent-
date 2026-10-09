"""Flask application for the VocaNova bilingual text-to-speech studio."""

import logging
import os
import re

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from io import BytesIO

from services.elevenlabs_service import LANGUAGE_VOICES, MAX_TEXT_LENGTH, SpeechServiceError, generate_speech

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/generate")
def generate():
    if not request.is_json:
        return jsonify(error="Please send a JSON request."), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Invalid request."), 400

    text = payload.get("text")
    language = payload.get("language")
    if not isinstance(text, str) or not text.strip():
        return jsonify(error="Please enter some text first."), 400
    if len(text) > MAX_TEXT_LENGTH:
        return jsonify(error="Text cannot exceed 5000 characters."), 400
    if not isinstance(language, str) or language not in LANGUAGE_VOICES:
        return jsonify(error="Choose Hindi or English."), 400

    try:
        audio = generate_speech(text.strip(), language)
    except SpeechServiceError as error:
        if error.status_code == 503:
            return jsonify(error="Voice service is not configured. Please contact the administrator."), 503
        if error.status_code == 401:
            return jsonify(error="Voice service credentials are invalid. Please contact the administrator."), 401
        if error.status_code == 429:
            return jsonify(error="The voice service is busy. Please try again shortly."), 429
        if error.status_code == 504:
            return jsonify(error="Speech generation timed out. Please try again."), 504
        if error.status_code == 400:
            return jsonify(error="Choose Hindi or English."), 400
        logger.error("Speech generation failed with status %s.", error.status_code)
        return jsonify(error="Unable to generate speech right now. Please try again."), error.status_code
    except Exception:
        logger.exception("Unexpected error while generating speech.")
        return jsonify(error="Unable to generate speech right now. Please try again."), 500

    safe_language = re.sub(r"[^a-z]", "", language)
    return send_file(
        BytesIO(audio),
        mimetype="audio/mpeg",
        as_attachment=False,
        download_name=f"vocanova-{safe_language}.mp3",
        max_age=0,
    )


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="Request is too large."), 413


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )