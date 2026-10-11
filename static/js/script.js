"use strict";

const MAX_TEXT_LENGTH = 5000;
const textArea = document.querySelector("#speech-text");
const characterCount = document.querySelector("#character-count");
const textFeedback = document.querySelector("#text-feedback");
const generateButton = document.querySelector("#generate-button");
const buttonLabel = document.querySelector(".button-label");
const clearButton = document.querySelector("#clear-button");
const resultPanel = document.querySelector("#result-panel");
const successMessageText = document.querySelector("#success-message-text");
const audioPlayer = document.querySelector("#audio-player");
const downloadButton = document.querySelector("#download-button");
const toast = document.querySelector("#toast");
const toastMessage = document.querySelector("#toast-message");
const toastClose = document.querySelector("#toast-close");
const scriptButton = document.querySelector("#generate-script-button");
const multiScript = document.querySelector("#multi-script");
const cloneForm = document.querySelector("#clone-form");
const voiceSampleInput = document.querySelector("#voice-sample");

let audioUrl = null;
let toastTimer = null;
let isGenerating = false;

function updateCharacterCount() {
  if (!textArea) return;
  characterCount.textContent = textArea.value.length.toLocaleString();
  textFeedback.textContent = "";
}

function showError(message) {
  window.clearTimeout(toastTimer);
  toastMessage.textContent = message;
  toast.hidden = false;
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, 6000);
}

function setGenerating(generating) {
  if (!generateButton) return;
  isGenerating = generating;
  generateButton.disabled = generating;
  textArea.disabled = generating;
  clearButton.disabled = generating;
  document.querySelectorAll('input[name="language"], input[name="agent"]').forEach((input) => {
    input.disabled = generating;
  });
  generateButton.classList.toggle("is-loading", generating);
  generateButton.setAttribute("aria-busy", String(generating));
  buttonLabel.textContent = generating ? "Generating voice..." : "Generate voice";
}

function releaseAudioUrl() {
  if (audioUrl) {
    URL.revokeObjectURL(audioUrl);
    audioUrl = null;
  }
}

function getSelectedLanguage() {
  const selected = document.querySelector('input[name="language"]:checked');
  return selected ? selected.value : "en";
}

function getSelectedAgent() {
  const selected = document.querySelector('input[name="agent"]:checked');
  return selected ? selected.value : "storyteller";
}

async function generateVoice() {
  const text = textArea.value.trim();
  const language = getSelectedLanguage();
  const agent = getSelectedAgent();

  if (!text) {
    textFeedback.textContent = "Please enter some text first.";
    textArea.focus();
    return;
  }
  if (text.length > MAX_TEXT_LENGTH) {
    showError("Text cannot exceed 5000 characters.");
    return;
  }
  if (!["en", "hi", "ta", "es"].includes(language)) {
    showError("Choose a supported language from English, Hindi, Tamil, or Spanish.");
    return;
  }

  setGenerating(true);
  textFeedback.textContent = "";

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "audio/mpeg, application/json" },
      body: JSON.stringify({ text, language, agent }),
    });

    if (!response.ok) {
      let message = "Unable to generate speech right now. Please try again.";
      try {
        const payload = await response.json();
        if (typeof payload.error === "string") message = payload.error;
      } catch (_error) {
        if (response.status === 429) message = "The voice service is busy. Please try again shortly.";
      }
      showError(message);
      return;
    }

    const audioBlob = await response.blob();
    if (!audioBlob.size || !audioBlob.type.startsWith("audio/")) {
      showError("The voice service returned an invalid audio file. Please try again.");
      return;
    }

    releaseAudioUrl();
    audioUrl = URL.createObjectURL(audioBlob);
    audioPlayer.src = audioUrl;
    downloadButton.href = audioUrl;
    downloadButton.download = `vocanova-${language}-${agent}.mp3`;
    if (successMessageText) {
      const provider = response.headers.get("X-Speech-Provider");
      if (provider === "free-edge-tts") {
        successMessageText.textContent = "Generated with the free voice for your selected agent.";
      } else if (provider === "free-gtts") {
        successMessageText.textContent = "Generated with the free Google TTS voice.";
      } else {
        successMessageText.textContent = "Voice generated successfully";
      }
    }
    resultPanel.hidden = false;
    resultPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    audioPlayer.play().catch(() => {});
  } catch (_error) {
    showError("Network error. Please check your connection and try again.");
  } finally {
    setGenerating(false);
  }
}

async function generateScriptNarration() {
  const script = multiScript.value.trim();
  if (!script) {
    showError("Please provide a valid script before generating narration.");
    return;
  }

  try {
    const response = await fetch("/api/multispeaker", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ script, language: getSelectedLanguage(), agent: getSelectedAgent() }),
    });

    if (!response.ok) {
      const payload = await response.json().catch(() => ({ error: "Unable to generate the multi-character narration right now." }));
      showError(payload.error || "Unable to generate the multi-character narration right now.");
      return;
    }

    const audioBlob = await response.blob();
    releaseAudioUrl();
    audioUrl = URL.createObjectURL(audioBlob);
    audioPlayer.src = audioUrl;
    downloadButton.href = audioUrl;
    downloadButton.download = "vocanova-multispeaker.mp3";
    resultPanel.hidden = false;
    resultPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    audioPlayer.play().catch(() => {});
  } catch (_error) {
    showError("The multi-character narration could not be generated. Please try again.");
  }
}

async function uploadVoiceSample(event) {
  event.preventDefault();
  const file = voiceSampleInput.files[0];
  if (!file) {
    showError("Please select a voice sample to upload.");
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  try {
    const response = await fetch("/api/voice-clone", { method: "POST", body: formData });
    const payload = await response.json().catch(() => ({ error: "Unable to process the voice sample." }));
    if (!response.ok) {
      showError(payload.error || "Unable to process the voice sample.");
      return;
    }
    showError(payload.message || "Voice sample accepted.");
  } catch (_error) {
    showError("Voice cloning is currently unavailable. Please try again later.");
  }
}

document.querySelectorAll('[data-panel-toggle]').forEach((button) => {
  button.addEventListener('click', () => {
    const targetPanel = button.dataset.panelToggle;
    document.querySelectorAll('.page-switch').forEach((switchButton) => {
      const isActive = switchButton === button;
      switchButton.classList.toggle('is-active', isActive);
      switchButton.setAttribute('aria-pressed', String(isActive));
    });

    document.querySelectorAll('.studio-panel').forEach((panel) => {
      const isSelected = panel.dataset.panel === targetPanel;
      panel.hidden = !isSelected;
      panel.classList.toggle('is-active', isSelected);
    });
  });
});

document.querySelectorAll('input[name="language"]').forEach((input) => {
  input.addEventListener("change", () => {
    document.querySelectorAll(".language-option").forEach((option) => {
      option.classList.toggle("is-selected", option.querySelector("input").checked);
    });
  });
});

document.querySelectorAll('input[name="agent"]').forEach((input) => {
  input.addEventListener("change", () => {
    document.querySelectorAll(".agent-card").forEach((card) => {
      card.classList.toggle("selected", card.dataset.agent === input.value);
    });
  });
});

if (textArea) {
  textArea.addEventListener("input", () => {
    updateCharacterCount();
  });
}
if (generateButton) {
  generateButton.addEventListener("click", generateVoice);
}
if (clearButton) {
  clearButton.addEventListener("click", () => {
    textArea.value = "";
    updateCharacterCount();
    textArea.focus();
  });
}
if (scriptButton) {
  scriptButton.addEventListener("click", generateScriptNarration);
}
if (cloneForm) {
  cloneForm.addEventListener("submit", uploadVoiceSample);
}
if (toastClose) {
  toastClose.addEventListener("click", () => { toast.hidden = true; });
}

document.querySelectorAll(".password-toggle").forEach((toggleButton) => {
  toggleButton.addEventListener("click", () => {
    const passwordInput = toggleButton.parentElement.querySelector(".password-input");
    if (!passwordInput) return;

    const isHidden = passwordInput.type === "password";
    passwordInput.type = isHidden ? "text" : "password";
    toggleButton.textContent = isHidden ? "Hide" : "Show";
    toggleButton.setAttribute("aria-label", isHidden ? "Hide password" : "Show password");
  });
});

const languageLabels = {
  en: {
    introStart: "Turn text into",
    introEnd: "natural voice.",
    copy: "Create polished multilingual speech, narration, and AI voice experiences for your audience.",
    generate: "Generate voice",
    script: "Your script",
    language: "Choose a language",
    voice: "VOICE GENERATOR",
  },
  hi: {
    introStart: "लिखे हुए शब्दों को",
    introEnd: "प्राकृतिक आवाज़ में बदलें।",
    copy: "अपने दर्शकों के लिए सुंदर बहुभाषी आवाज़, कथन और AI वॉयस अनुभव बनाएं।",
    generate: "आवाज़ बनाएं",
    script: "आपका स्क्रिप्ट",
    language: "भाषा चुनें",
    voice: "वॉयस जनरेटर",
  },
  ta: {
    introStart: "உரையை",
    introEnd: "இயற்கையான குரலாக மாற்றுங்கள்.",
    copy: "உங்கள் பார்வையாளர்களுக்காக அழகான பலமொழி பேச்சு, கதை மற்றும் AI குரல் அனுபவங்களை உருவாக்குங்கள்.",
    generate: "குரல் உருவாக்கு",
    script: "உங்கள் ஸ்கிரிப்ட்",
    language: "மொழியைத் தேர்ந்தெடுக்கவும்",
    voice: "குரல் ஜெனரேட்டர்",
  },
  es: {
    introStart: "Convierte el texto en",
    introEnd: "voz natural.",
    copy: "Crea discursos, narraciones y experiencias de voz con IA en varios idiomas para tu audiencia.",
    generate: "Generar voz",
    script: "Tu guion",
    language: "Elige un idioma",
    voice: "GENERADOR DE VOZ",
  },
};

const updateLanguageUI = () => {
  const selectedLanguage = document.querySelector('input[name="language"]:checked')?.value || "en";
  const labels = languageLabels[selectedLanguage] || languageLabels.en;
  const headingStart = document.querySelector("#page-title .hero-title-start");
  const headingEnd = document.querySelector("#page-title .hero-title");
  const introCopy = document.querySelector(".intro-copy");
  const generateButtonLabel = document.querySelector(".button-label");
  const scriptLabel = document.querySelector(".text-label-row label");
  const languageLegend = document.querySelector(".language-picker legend");
  const voiceKicker = document.querySelector(".section-kicker");

  if (headingStart) headingStart.textContent = labels.introStart;
  if (headingEnd) headingEnd.textContent = labels.introEnd;
  if (introCopy) introCopy.textContent = labels.copy;
  if (generateButtonLabel) generateButtonLabel.textContent = labels.generate;
  if (scriptLabel) scriptLabel.textContent = labels.script;
  if (languageLegend) languageLegend.textContent = labels.language;
  if (voiceKicker) voiceKicker.textContent = labels.voice;
};

document.querySelectorAll('input[name="language"]').forEach((input) => {
  input.addEventListener("change", () => {
    updateLanguageUI();
    document.querySelectorAll(".language-option").forEach((option) => {
      option.classList.toggle("is-selected", option.querySelector("input").checked);
    });
  });
});

if (document.querySelector('input[name="language"]')) {
  updateLanguageUI();
}

window.addEventListener("pagehide", releaseAudioUrl);