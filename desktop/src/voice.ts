export const VOICE_SILENCE_MS = 1800;
export const VOICE_NO_SPEECH_TIMEOUT_MS = 15000;
export const VOICE_MAX_RECORDING_MS = 120000;
export const VOICE_LEVEL_THRESHOLD = 0.035;

export function requestMicrophone(): Promise<MediaStream> {
  const mediaDevices = globalThis.navigator?.mediaDevices;
  if (!mediaDevices || typeof mediaDevices.getUserMedia !== "function") {
    const insecureContext = typeof window !== "undefined" && !window.isSecureContext;
    throw new Error(
      insecureContext
        ? "O microfone exige um contexto seguro. Abra o Bauer em https:// ou em http://localhost."
        : "Este navegador não disponibilizou acesso ao microfone para o Bauer.",
    );
  }
  return mediaDevices.getUserMedia({ audio: true });
}

/**
 * Usa a fala nativa do navegador quando o servidor não tem provider TTS.
 * Retorna false sem lançar quando a Web Speech API não está disponível.
 */
export function speakBrowserFallback(text: string): boolean {
  const value = text.trim();
  const synthesis = globalThis.speechSynthesis;
  const Utterance = globalThis.SpeechSynthesisUtterance;
  if (!value || !synthesis || typeof synthesis.speak !== "function" || typeof Utterance !== "function") {
    return false;
  }
  try {
    synthesis.cancel();
    const utterance = new Utterance(value);
    utterance.lang = "pt-BR";
    const portugueseVoice = synthesis.getVoices().find((voice) => voice.lang.toLowerCase().startsWith("pt"));
    if (portugueseVoice) utterance.voice = portugueseVoice;
    synthesis.speak(utterance);
    return true;
  } catch {
    return false;
  }
}

export interface RecordingStopSample {
  heardSpeech: boolean;
  silentForMs: number;
  noSpeechForMs: number;
}

export function normalizeVoiceText(text: string): string {
  return text.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase().replace(/[^\w\s]/g, " ").replace(/\s+/g, " ").trim();
}

export function isVoiceStop(text: string): boolean {
  return new Set(["parar", "pare", "cancelar"]).has(normalizeVoiceText(text));
}

export function extractWakeCommand(text: string): string | null {
  const folded = text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const index = folded.indexOf("bauer");
  if (index < 0) return null;
  const before = folded[index - 1];
  const after = folded[index + 5];
  if ((before && /[\w]/.test(before)) || (after && /[\w]/.test(after))) return null;
  return text.slice(index + 5).replace(/^[\s,;:!?-]+/, "").trim();
}

export function shouldStopRecording({
  heardSpeech,
  silentForMs,
  noSpeechForMs,
}: RecordingStopSample): boolean {
  return (
    (heardSpeech && silentForMs >= VOICE_SILENCE_MS) ||
    (!heardSpeech && noSpeechForMs >= VOICE_NO_SPEECH_TIMEOUT_MS) ||
    noSpeechForMs >= VOICE_MAX_RECORDING_MS
  );
}
