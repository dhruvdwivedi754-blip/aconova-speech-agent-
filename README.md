# aconova-speech-agent-
aconova is a ai text to speech model 
# 🎙️ Text-to-Speech Model

A web-based **Text-to-Speech (TTS) application** that converts written text into spoken audio using the ElevenLabs API.

The project provides a simple web interface where users can enter text, generate speech, and listen to the resulting audio.

## ✨ Features

* 📝 Convert written text into speech.
* 🗣️ Generate natural-sounding audio using the ElevenLabs API.
* 🌐 Simple and user-friendly web interface.
* 🎨 Custom frontend using HTML, CSS, and JavaScript.
* ⚙️ Flask-based Python backend.
* 🔐 Secure API key configuration using environment variables.
* 🧪 Basic application tests.
* 📁 Modular project structure for easier maintenance.

## 🛠️ Tech Stack

| Technology     | Purpose                       |
| -------------- | ----------------------------- |
| Python         | Backend development           |
| Flask          | Web application framework     |
| ElevenLabs API | Text-to-speech generation     |
| HTML           | Web page structure            |
| CSS            | Styling and responsive design |
| JavaScript     | Frontend interactions         |
| pytest         | Application testing           |

## 📂 Project Structure

```text
text-to-speech-model/
│
├── app.py
├── requirements.txt
├── requests.http
├── README.md
├── .env.example
├── .gitignore
│
├── services/
│   ├── __init__.py
│   └── elevenlabs_service.py
│
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── script.js
│
├── templates/
│   └── index.html
│
└── tests/
    └── test_a_
```

