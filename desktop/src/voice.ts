export const VOICE_SILENCE_MS = 1800;
export const VOICE_NO_SPEECH_TIMEOUT_MS = 15000;
export const VOICE_MAX_RECORDING_MS = 120000;
export const VOICE_LEVEL_THRESHOLD = 0.035;

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
