# VocaNova

VocaNova is a Flask-based multilingual text-to-speech studio with local accounts, three voice agents, character narration, and voice-cloning setup guidance.

## Features

- English, Hindi, Tamil, and Spanish speech output.
- Automatic source-language detection and translation to the selected output language.
- Speech generation uses the translated text for the selected output language.
- Storyteller AI, Professional Narrator AI, and Cinematic Voice AI.
- MP3 playback and download.
- Local SQLite authentication.

## Requirements

- Python 3.10+
- Flask, Requests, and python-dotenv
- `langdetect` for local source-language detection
- A Gemini API key for translation
- Optional ElevenLabs API key and voice IDs for ElevenLabs speech generation; selected agents use free Edge TTS voices when ElevenLabs is not configured or the voice is blocked by a plan

## Install on Windows

```powershell
cd "C:\path\to\text to speech model"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell reports `No module named 'edge_tts'`, install dependencies into the same Python environment used to run the app:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

## Configure

Set your own values in the local `.env` file in the project root. Keep this file local; never paste real API keys into Python source files or commit them:

```dotenv
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash
ELEVENLABS_API_KEY=your_elevenlabs_api_key
ELEVENLABS_VOICE_ID_EN=your_english_voice_id
ELEVENLABS_VOICE_ID_HI=your_hindi_voice_id
ELEVENLABS_VOICE_ID_TA=your_tamil_voice_id
ELEVENLABS_VOICE_ID_ES=your_spanish_voice_id
STORYTELLER_VOICE_ID_EN=your_storyteller_english_voice_id
STORYTELLER_VOICE_ID_HI=your_storyteller_hindi_voice_id
STORYTELLER_VOICE_ID_TA=your_storyteller_tamil_voice_id
STORYTELLER_VOICE_ID_ES=your_storyteller_spanish_voice_id
PROFESSIONAL_VOICE_ID_EN=your_professional_english_voice_id
PROFESSIONAL_VOICE_ID_HI=your_professional_hindi_voice_id
PROFESSIONAL_VOICE_ID_TA=your_professional_tamil_voice_id
PROFESSIONAL_VOICE_ID_ES=your_professional_spanish_voice_id
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
FLASK_SECRET_KEY=replace_with_a_random_secret
FLASK_ENV=development
FLASK_DEBUG=false
ELEVENLABS_CLONING_ENABLED=false
```

Configure a different ElevenLabs voice ID per agent and language using the `STORYTELLER_VOICE_ID_<LANG>` and `PROFESSIONAL_VOICE_ID_<LANG>` variables. For character narration, configure Storyteller with female voices and Professional with male voices: `Narrator` is assigned female, `Rahul` male, and Gemini classifies other speakers from the script. The studio offers Storyteller and Professional; Cinematic is not available because no voice IDs were supplied for it. If ElevenLabs has no API key configured or rejects an agent voice with HTTP 402, the app uses separate Microsoft Edge online TTS voices for each agent and English, Hindi, Tamil, and Spanish. Edge TTS does not require an API key or charge per character, but it requires an internet connection and its online service may be rate-limited or change availability. Other ElevenLabs errors are reported rather than silently switching providers. Speech translation and speaker classification require `GEMINI_API_KEY`; it defaults to Google's `gemini-2.5-flash` and can be changed with `GEMINI_MODEL`. Gemini API free-tier access is subject to Google's model availability, quota, and rate limits.

## Run locally

```powershell
.\.venv\Scripts\python.exe app.py
```

Open <http://127.0.0.1:5000>. The site starts on the login page; after login, continue to the dashboard and open the studio from there.

## Generate speech

1. Add your Gemini API key to `GEMINI_API_KEY` in the local `.env` file, then restart the app.
2. Enter your script in **Your script**. It can be written in any source language the translation provider supports.
3. Select English, Hindi, Tamil, or Spanish as the desired output language.
4. Select Storyteller or Professional; each uses its own voice ID for the selected language.
5. Click **Generate voice**. The app detects the source language, translates the script into the selected language, and sends the translated text to ElevenLabs or the selected agent's free Edge TTS voice when ElevenLabs is unavailable on the current plan.
6. Play the MP3 in the audio player or click **Download**.

For **Character narration**, put each speaker on a separate `Name: dialogue` line and reuse the same name for that character. New speaker names alternate between the selected agent voice and the other available voice so each character keeps a consistent, distinct voice.

## Translation setup and limitations

The app sends the script to Gemini using the server-side `GEMINI_API_KEY` and asks for a translation in the selected output language. Source language detection runs locally with `langdetect`; when the input already matches the selected language, translation is skipped. Translation happens automatically during speech generation. If the key is missing, Gemini quota is exhausted, or translation fails, the app displays an error and does not generate speech from the untranslated original. Store the key only in the local `.env` file, not in Python source or browser code.

Text is limited to 5,000 characters per request.

## Test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Routes

- `/` — Login entry point
- `/studio` — Authenticated voice studio
- `/login`, `/signup`, `/dashboard`, `/logout` — account flow
- `/api/generate` — validated single-speaker MP3 generation
- `/api/multispeaker` — character narration
- `/api/voice-clone` — provider-gated voice sample flow

## Troubleshooting

- **Translation failed or timed out:** check `GEMINI_API_KEY`, `GEMINI_MODEL`, internet connectivity, and Gemini quota; translation stops rather than speaking the wrong-language source.
- **Voice does not support the selected language:** choose a compatible configured agent voice or update its environment variable. The app reports provider rejection instead of silently switching voices.
- **Voice service is not configured:** agent voice generation can use Edge TTS without an ElevenLabs key, but non-agent requests require a valid ElevenLabs key and voice ID.
- **Agent voices sound the same or fail on a free plan:** make sure the `STORYTELLER_VOICE_ID_<LANG>` and `PROFESSIONAL_VOICE_ID_<LANG>` values differ for each language and are enabled for API use by your ElevenLabs account. If ElevenLabs rejects an agent voice because of the plan, the app switches to distinct Edge TTS voices for the selected agent and language; the success message identifies this free provider.
