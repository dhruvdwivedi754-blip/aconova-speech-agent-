"""Flask application for the VocaNova AI voice studio."""

import logging
import os
import re
import secrets
import sqlite3
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from services.elevenlabs_service import (
    LANGUAGE_VOICES,
    MAX_TEXT_LENGTH,
    SUPPORTED_LANGUAGES,
    VOICE_AGENTS,
    SpeechServiceError,
    generate_script_audio,
    generate_speech,
    parse_script_lines,
)
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "voca_nova.db"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV", "development").lower() == "production"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_db_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"
        )
        connection.commit()


init_db()


@app.before_request
def ensure_csrf_token():
    session.setdefault("csrf_token", secrets.token_urlsafe(32))


@app.get("/")
def index():
    session.clear()
    return redirect(url_for("login_page"))


@app.get("/studio")
def studio():
    user = get_current_user()
    if not user:
        return redirect(url_for("login_page"))
    return render_template("index.html", user=user)


@app.get("/login")
def login_page():
    return render_template("login.html", user=get_current_user())


@app.get("/signup")
def signup_page():
    return render_template("signup.html", user=get_current_user())


@app.get("/dashboard")
def dashboard():
    user = get_current_user()
    if not user:
        return redirect(url_for("login_page"))
    return render_template("dashboard.html", user=user)


@app.post("/logout")
def logout():
    if not app.config.get("TESTING"):
        csrf_token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
        if csrf_token != session.get("csrf_token"):
            return jsonify(error="Invalid request."), 403
    session.clear()
    return redirect(url_for("login_page"))


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, name, email FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if row is None:
        session.clear()
        return None
    return {"id": row["id"], "name": row["name"], "email": row["email"]}


def normalise_email(value):
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def validate_csrf_or_skip():
    if app.config.get("TESTING"):
        return
    csrf_token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    if csrf_token != session.get("csrf_token"):
        raise PermissionError("Invalid CSRF token.")


@app.post("/api/auth/signup")
def api_signup():
    try:
        validate_csrf_or_skip()
    except PermissionError:
        if request.is_json:
            return jsonify(error="Invalid request."), 403
        return redirect(url_for("login_page"))

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = request.form.to_dict()

    name = (payload.get("name") or "").strip()
    email = normalise_email(payload.get("email"))
    password = payload.get("password") or ""
    confirm_password = payload.get("confirm_password") or ""

    if not name or len(name) < 2:
        if request.is_json:
            return jsonify(error="Please enter your full name."), 400
        return render_template("signup.html", user=None, error="Please enter your full name."), 400
    if not email or "@" not in email:
        if request.is_json:
            return jsonify(error="Please enter a valid email address."), 400
        return render_template("signup.html", user=None, error="Please enter a valid email address."), 400
    if len(password) < 8:
        if request.is_json:
            return jsonify(error="Password must be at least 8 characters long."), 400
        return render_template("signup.html", user=None, error="Password must be at least 8 characters long."), 400
    if password != confirm_password:
        if request.is_json:
            return jsonify(error="Passwords do not match."), 400
        return render_template("signup.html", user=None, error="Passwords do not match."), 400

    with get_db_connection() as connection:
        existing = connection.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            if request.is_json:
                return jsonify(error="An account with this email already exists."), 409
            return render_template("signup.html", user=None, error="An account with this email already exists."), 409
        try:
            connection.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                (name, email, generate_password_hash(password)),
            )
        except sqlite3.Error:
            if request.is_json:
                return jsonify(error="Unable to create the account right now."), 500
            return render_template("signup.html", user=None, error="Unable to create the account right now."), 500

    if request.is_json:
        return jsonify(message="Account created successfully. Please log in."), 201
    return redirect(url_for("login_page"))


@app.post("/api/auth/login")
def api_login():
    try:
        validate_csrf_or_skip()
    except PermissionError:
        if request.is_json:
            return jsonify(error="Invalid request."), 403
        return redirect(url_for("login_page"))

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = request.form.to_dict()

    email = normalise_email(payload.get("email"))
    password = payload.get("password") or ""
    if not email or not password:
        if request.is_json:
            return jsonify(error="Email and password are required."), 400
        return render_template("login.html", user=None, error="Email and password are required."), 400

    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, name, email, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()

    if row is None or not check_password_hash(row["password_hash"], password):
        if request.is_json:
            return jsonify(error="Invalid email or password."), 401
        return render_template("login.html", user=None, error="Invalid email or password."), 401

    session.clear()
    session["user_id"] = row["id"]
    session["user_name"] = row["name"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    if request.is_json:
        return jsonify(message="Login successful.", redirect="/dashboard"), 200
    return redirect(url_for("dashboard"))


@app.post("/api/auth/logout")
def api_logout():
    try:
        validate_csrf_or_skip()
    except PermissionError:
        return jsonify(error="Invalid request."), 403

    session.clear()
    return jsonify(message="Logged out."), 200


@app.get("/api/voice-config")
def voice_config():
    return jsonify({
        "languages": [
            {"code": code, "name": {"en": "English", "hi": "Hindi", "ta": "Tamil", "es": "Spanish"}[code]}
            for code in SUPPORTED_LANGUAGES
        ],
        "agents": {agent: details for agent, details in VOICE_AGENTS.items()},
    })


@app.post("/api/generate")
def generate():
    if request.is_json:
        payload = request.get_json(silent=True)
    else:
        payload = request.form.to_dict()

    if not isinstance(payload, dict):
        return jsonify(error="Invalid request."), 400

    text = payload.get("text")
    language = payload.get("language")
    agent_name = payload.get("agent")
    voice_id = payload.get("voice_id")

    if not isinstance(text, str) or not text.strip():
        return jsonify(error="Please enter some text first."), 400
    if len(text) > MAX_TEXT_LENGTH:
        return jsonify(error="Text cannot exceed 5000 characters."), 400
    if not isinstance(language, str) or not language.strip():
        return jsonify(error="Choose a supported language."), 400
    language = language.strip().lower()
    if language not in LANGUAGE_VOICES:
        return jsonify(error="Choose a supported language from English, Hindi, Tamil, or Spanish."), 400
    try:
        audio, speech_provider = generate_speech(
            text.strip(),
            language,
            voice_id=voice_id,
            agent_name=agent_name,
            return_provider=True,
        )
    except SpeechServiceError as error:
        if error.code == "voice_agent_config":
            return jsonify(error=str(error)), 503
        if error.code == "voice_language":
            return jsonify(error=str(error)), 422
        if error.code == "translation":
            return jsonify(
                error=(
                    "Could not translate the text into the selected language. "
                    "Add or check GEMINI_API_KEY in the local .env file, then check your internet connection and Gemini quota."
                )
            ), error.status_code
        if error.status_code == 503:
            return jsonify(error="Voice service is not configured. Please contact the administrator."), 503
        if error.status_code == 401:
            return jsonify(error="Voice service credentials are invalid. Please contact the administrator."), 401
        if error.status_code == 402:
            return jsonify(error=str(error)), 402
        if error.status_code == 429:
            return jsonify(error="The voice service is busy. Please try again shortly."), 429
        if error.status_code == 504:
            return jsonify(error="Speech generation timed out. Please try again."), 504
        if error.status_code == 400:
            return jsonify(error="Choose a supported language from English, Hindi, Tamil, or Spanish."), 400
        logger.error("Speech generation failed with status %s.", error.status_code)
        return jsonify(error="Unable to generate speech right now. Please try again."), error.status_code
    except Exception:
        logger.exception("Unexpected error while generating speech.")
        return jsonify(error="Unable to generate speech right now. Please try again."), 500

    safe_language = re.sub(r"[^a-z]", "", language)
    response = send_file(
        BytesIO(audio),
        mimetype="audio/mpeg",
        as_attachment=False,
        download_name=f"vocanova-{safe_language}.mp3",
        max_age=0,
    )
    response.headers["X-Speech-Provider"] = speech_provider
    return response


@app.post("/api/multispeaker")
def multispeaker_generation():
    if request.is_json:
        payload = request.get_json(silent=True)
    else:
        payload = request.form.to_dict()
    if not isinstance(payload, dict):
        return jsonify(error="Invalid request."), 400

    script = payload.get("script")
    if not isinstance(script, str) or not script.strip():
        return jsonify(error="Please provide a valid script before generating narration."), 400

    speaker_assignments = payload.get("speaker_assignments") or {}
    if not isinstance(speaker_assignments, dict):
        return jsonify(error="Speaker assignments must be provided as a dictionary."), 400

    language = str(payload.get("language") or "en").strip().lower()
    if language not in LANGUAGE_VOICES:
        return jsonify(error="Choose a supported language from English, Hindi, Tamil, or Spanish."), 400

    agent_name = str(payload.get("agent") or "storyteller").strip().lower()

    try:
        audio = generate_script_audio(script, speaker_assignments=speaker_assignments, language=language, agent_name=agent_name)
    except SpeechServiceError as error:
        if error.code == "translation":
            return jsonify(
                error="Could not translate the script into the selected language. Check your internet connection and try again."
            ), error.status_code
        if error.status_code == 400:
            return jsonify(error=str(error)), 400
        return jsonify(error="Unable to generate the multi-character narration right now."), error.status_code

    return send_file(
        BytesIO(audio),
        mimetype="audio/mpeg",
        as_attachment=False,
        download_name="vocanova-multispeaker.mp3",
        max_age=0,
    )


@app.post("/api/voice-clone")
def voice_clone():
    if "file" not in request.files:
        return jsonify(error="Please upload a voice sample."), 400

    uploaded = request.files["file"]
    if uploaded.filename == "":
        return jsonify(error="Please select a valid audio file."), 400

    filename = uploaded.filename.lower()
    if not filename.endswith((".wav", ".mp3", ".m4a", ".ogg")):
        return jsonify(error="Unsupported format. Use WAV, MP3, M4A, or OGG."), 400

    # Keep this feature intentionally server-side and provider-gated. The app should not
    # claim a successful clone unless the configured provider confirms the operation.
    if not os.getenv("ELEVENLABS_API_KEY"):
        return jsonify(error="Voice cloning is not available because the ElevenLabs API key is missing."), 503
    if os.getenv("ELEVENLABS_CLONING_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        return jsonify(error="Voice cloning is not enabled for this account. Configure a supported ElevenLabs voice-cloning plan and set ELEVENLABS_CLONING_ENABLED=true."), 501

    return jsonify({
        "message": "A valid voice sample was received. Provider-side cloning will only complete if the configured account supports voice cloning and the account owner has granted consent.",
        "status": "queued",
    })


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="Request is too large."), 413


@app.errorhandler(404)
def not_found(_error):
    return jsonify(error="Page not found."), 404


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )