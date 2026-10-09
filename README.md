# VocaNova

Turn your words into natural voice. VocaNova is a bilingual Hindi and English text-to-speech studio powered by the ElevenLabs API. Audio is generated on the server and returned to the browser as an MP3; the API key is never sent to the frontend.

## Features

- Generate real Hindi and English speech with ElevenLabs.
- Configure a separate ElevenLabs voice ID for each language.
- Preview and download generated MP3 audio without reloading the page.
- Validate request language, script contents, and the 5,000-character limit on both client and server.
- Keep API credentials in environment variables and return user-safe API errors.
- Responsive layout, accessible form controls, live character count, and clear loading and error states.

## Tech Stack

- Python and Flask
- ElevenLabs text-to-speech REST API via `requests`
- `python-dotenv`
- HTML, CSS, and vanilla JavaScript

## Architecture

The browser posts the selected language and text to Flask at `POST /api/generate`. The Flask route validates the request and delegates speech generation to `services/elevenlabs_service.py`. That service reads credentials and voice IDs from the server environment, calls ElevenLabs, and returns MP3 bytes to the browser. The browser creates an in-memory audio URL for playback and download; generated audio is not stored on disk.

## Project Structure

```text
.
├── app.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── services/
│   ├── __init__.py
│   └── elevenlabs_service.py
├── templates/
│   └── index.html
├── static/
│   ├── css/style.css
│   └── js/script.js
└── tests/
    └── test_app.py
```

## Prerequisites

- Python 3.10 or newer.
- An ElevenLabs account with API access and at least one voice that supports multilingual speech.
- The voice IDs for the English and Hindi voice you want to use. ElevenLabs voices vary in language support, so verify your chosen voices can speak your intended language.

## Installation

Create and activate a virtual environment from the project directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell prevents activating the environment, use `\.venv\Scripts\python.exe` in place of `python` in the commands below.

## Environment Variables

Copy the example configuration and edit the local `.env` file:

```powershell
Copy-Item .env.example .env
```

Fill in these values in `.env`:

```dotenv
ELEVENLABS_API_KEY=your_real_api_key
ELEVENLABS_VOICE_ID_EN=your_english_voice_id
ELEVENLABS_VOICE_ID_HI=your_hindi_voice_id
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
FLASK_DEBUG=false
```

Get the API key from the API key section of your ElevenLabs account. Copy voice IDs from the voice details in your ElevenLabs account. Configure a suitable voice separately for each language; do not use the placeholder values. `eleven_multilingual_v2` is the default model. The selected voice must support the requested language. Never commit `.env` or paste the key into frontend code.

## Run Locally

```powershell
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). The app binds to localhost by default. Set `PORT` to change the port.

## Try Hindi and English

1. Set both `ELEVENLABS_VOICE_ID_EN` and `ELEVENLABS_VOICE_ID_HI` to real, language-capable voice IDs in `.env`.
2. Start the app and choose **English** or **Hindi** in the language selector.
3. Enter text in that language and select **Generate voice**.
4. Use the audio player to listen, or select **Download** to save the MP3.

The Hindi field submits the language code `hi` and the English field submits `en`; the server only accepts those values.

## API

### `POST /api/generate`

Request JSON:

```json
{
  "text": "Hello, welcome to VocaNova.",
  "language": "en"
}
```

Use `"language": "hi"` for Hindi. On success, the endpoint returns MP3 bytes with an `audio/mpeg` content type. On errors, it returns a JSON object such as `{"error":"Please enter some text first."}` with an appropriate HTTP status.

| Status | Meaning |
| --- | --- |
| `200` | MP3 audio generated |
| `400` | Invalid JSON, empty or overlong text, or unsupported language |
| `401` | ElevenLabs rejected the configured credentials |
| `413` | Request exceeds the Flask request-size limit |
| `429` | ElevenLabs rate limit reached |
| `502` | ElevenLabs API or upstream generation failure |
| `503` | Missing API key or language voice configuration |
| `504` | ElevenLabs request timed out |

## Tests

Run the backend tests with Python's built-in test runner:

```powershell
python -m unittest discover -s tests -v
```

Tests exercise server-side validation, MP3 responses, upstream errors, and safe error messages. End-to-end Hindi and English generation, playback, and downloads require real ElevenLabs credentials and voice IDs.

## Security Notes

- The ElevenLabs key is read only by the Python backend. Do not add secrets to HTML, JavaScript, source control, or client-visible responses.
- `.env` is excluded by `.gitignore`. Keep production secrets in your hosting provider's environment configuration.
- User input is validated on the server; only `en` and `hi` are accepted.
- The app logs technical failures without logging request text, API keys, or upstream response bodies.
- Speech requests are sent to the ElevenLabs API over HTTPS. Audio is streamed from the server and kept in browser memory.
- Add authentication, quotas, or other abuse controls before exposing this public endpoint to untrusted traffic.

## Deployment

Deploy to a Python host that supports Flask/WSGI and outbound HTTPS requests. Install `requirements.txt`, configure `ELEVENLABS_API_KEY`, both language voice IDs, and `ELEVENLABS_MODEL_ID` as hosting environment variables, then start the app with a production WSGI server, for example `gunicorn app:app` (install Gunicorn separately on Linux hosts if needed). Put the service behind HTTPS, configure request and worker timeouts for speech generation, and keep debug mode disabled. `app.run()` is intended for local development, not production.

## Screenshots

_Add application screenshots here._

## Future Improvements

- Offer selectable voices after retrieving supported voices from the server.
- Add request quotas and authentication for public deployments.
- Add automated browser coverage for audio controls and mobile viewports.