"use strict";

const MAX_TEXT_LENGTH = 5000;
const textArea = document.querySelector("#speech-text");
const characterCount = document.querySelector("#character-count");
const textFeedback = document.querySelector("#text-feedback");
const generateButton = document.querySelector("#generate-button");
const buttonLabel = document.querySelector(".button-label");
const clearButton = document.querySelector("#clear-button");
const resultPanel = document.querySelector("#result-panel");
const audioPlayer = document.querySelector("#audio-player");
const downloadButton = document.querySelector("#download-button");
const toast = document.querySelector("#toast");
const toastMessage = document.querySelector("#toast-message");
const toastClose = document.querySelector("#toast-close");

let audioUrl = null;
let toastTimer = null;

function updateCharacterCount() {
  characterCount.textContent = textArea.value.length.toLocaleString();
  textFeedback.textContent = "";
}

function showError(message) {
  window.clearTimeout(toastTimer);
  toastMessage.textContent = message;
  toast.hidden = false;
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, 6000);
}

function setGenerating(isGenerating) {
  generateButton.disabled = isGenerating;
  generateButton.classList.toggle("is-loading", isGenerating);
  generateButton.setAttribute("aria-busy", String(isGenerating));
  buttonLabel.textContent = isGenerating ? "Generating voice..." : "Generate voice";
}

function releaseAudioUrl() {
  if (audioUrl) {
    URL.revokeObjectURL(audioUrl);
    audioUrl = null;
  }
}

async function generateVoice() {
  const text = textArea.value.trim();
  const language = document.querySelector('input[name="language"]:checked')?.value;

  if (!text) {
    textFeedback.textContent = "Please enter some text first.";
    textArea.focus();
    return;
  }
  if (text.length > MAX_TEXT_LENGTH) {
    showError("Text cannot exceed 5000 characters.");
    return;
  }
  if (!["en", "hi"].includes(language)) {
    showError("Choose Hindi or English.");
    return;
  }

  setGenerating(true);
  textFeedback.textContent = "";

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "audio/mpeg, application/json" },
      body: JSON.stringify({ text, language }),
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
    downloadButton.download = `vocanova-${language}.mp3`;
    resultPanel.hidden = false;
    resultPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    audioPlayer.play().catch(() => {});
  } catch (_error) {
    showError("Network error. Please check your connection and try again.");
  } finally {
    setGenerating(false);
  }
}

document.querySelectorAll('input[name="language"]').forEach((input) => {
  input.addEventListener("change", () => {
    document.querySelectorAll(".language-option").forEach((option) => {
      option.classList.toggle("is-selected", option.querySelector("input").checked);
    });
  });
});

textArea.addEventListener("input", updateCharacterCount);
generateButton.addEventListener("click", generateVoice);
clearButton.addEventListener("click", () => {
  textArea.value = "";
  updateCharacterCount();
  textArea.focus();
});
toastClose.addEventListener("click", () => { toast.hidden = true; });
window.addEventListener("pagehide", releaseAudioUrl);