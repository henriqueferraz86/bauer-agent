import { describe, expect, it, vi } from "vitest";
import {
  extractWakeCommand,
  isVoiceStop,
  requestMicrophone,
  speakBrowserFallback,
  shouldStopRecording,
  VOICE_MAX_RECORDING_MS,
  VOICE_NO_SPEECH_TIMEOUT_MS,
  VOICE_SILENCE_MS,
} from "./voice";

describe("microphone access", () => {
  it("reports when the browser hides mediaDevices", async () => {
    const originalNavigator = globalThis.navigator;
    Object.defineProperty(globalThis, "navigator", {
      configurable: true,
      value: {},
    });
    try {
      expect(() => requestMicrophone()).toThrow("não disponibilizou acesso");
    } finally {
      Object.defineProperty(globalThis, "navigator", {
        configurable: true,
        value: originalNavigator,
      });
    }
  });

  it("delegates to the browser microphone API", async () => {
    const getUserMedia = vi.fn().mockResolvedValue("stream");
    const originalNavigator = globalThis.navigator;
    Object.defineProperty(globalThis, "navigator", {
      configurable: true,
      value: { mediaDevices: { getUserMedia } },
    });
    try {
      await expect(requestMicrophone()).resolves.toBe("stream");
      expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    } finally {
      Object.defineProperty(globalThis, "navigator", {
        configurable: true,
        value: originalNavigator,
      });
    }
  });
});

describe("browser speech fallback", () => {
  it("speaks in Brazilian Portuguese and cancels previous speech", () => {
    const speak = vi.fn();
    const cancel = vi.fn();
    const voice = { lang: "pt-BR", name: "Português" } as SpeechSynthesisVoice;
    const synthesis = { speak, cancel, getVoices: () => [voice] } as unknown as SpeechSynthesis;
    class FakeUtterance {
      lang = "";
      voice?: SpeechSynthesisVoice;
      constructor(public text: string) {}
    }
    const originalSynthesis = globalThis.speechSynthesis;
    const originalUtterance = globalThis.SpeechSynthesisUtterance;
    Object.defineProperty(globalThis, "speechSynthesis", { configurable: true, value: synthesis });
    Object.defineProperty(globalThis, "SpeechSynthesisUtterance", { configurable: true, value: FakeUtterance });
    try {
      expect(speakBrowserFallback("Olá, Bauer")).toBe(true);
      expect(cancel).toHaveBeenCalledOnce();
      expect(speak).toHaveBeenCalledOnce();
      expect(speak.mock.calls[0][0]).toMatchObject({ text: "Olá, Bauer", lang: "pt-BR", voice });
    } finally {
      Object.defineProperty(globalThis, "speechSynthesis", { configurable: true, value: originalSynthesis });
      Object.defineProperty(globalThis, "SpeechSynthesisUtterance", { configurable: true, value: originalUtterance });
    }
  });

  it("returns false when the browser has no speech API", () => {
    const originalSynthesis = globalThis.speechSynthesis;
    const originalUtterance = globalThis.SpeechSynthesisUtterance;
    Object.defineProperty(globalThis, "speechSynthesis", { configurable: true, value: undefined });
    Object.defineProperty(globalThis, "SpeechSynthesisUtterance", { configurable: true, value: undefined });
    try {
      expect(speakBrowserFallback("teste")).toBe(false);
    } finally {
      Object.defineProperty(globalThis, "speechSynthesis", { configurable: true, value: originalSynthesis });
      Object.defineProperty(globalThis, "SpeechSynthesisUtterance", { configurable: true, value: originalUtterance });
    }
  });
});

describe("voice command parsing", () => {
  it("accepts stop commands independent of accents and case", () => {
    expect(isVoiceStop("PÁRE!")).toBe(true);
    expect(isVoiceStop(" cancelar ")).toBe(true);
    expect(isVoiceStop("pare agora")).toBe(false);
  });

  it("extracts a command after the wake word", () => {
    expect(extractWakeCommand("Bauer, abra o projeto")).toBe("abra o projeto");
    expect(extractWakeCommand("pode BAUER: mostrar status")).toBe("mostrar status");
    expect(extractWakeCommand("bauerman mostrar status")).toBeNull();
    expect(extractWakeCommand("mostrar status")).toBeNull();
  });
});

describe("automatic recording stop", () => {
  it("stops after speech followed by silence", () => {
    expect(shouldStopRecording({
      heardSpeech: true,
      silentForMs: VOICE_SILENCE_MS,
      noSpeechForMs: 4000,
    })).toBe(true);
  });

  it("keeps recording while speech continues", () => {
    expect(shouldStopRecording({
      heardSpeech: true,
      silentForMs: VOICE_SILENCE_MS - 1,
      noSpeechForMs: 4000,
    })).toBe(false);
  });

  it("stops an empty recording after the no-speech timeout", () => {
    expect(shouldStopRecording({
      heardSpeech: false,
      silentForMs: 0,
      noSpeechForMs: VOICE_NO_SPEECH_TIMEOUT_MS,
    })).toBe(true);
  });

  it("enforces the maximum recording duration", () => {
    expect(shouldStopRecording({
      heardSpeech: true,
      silentForMs: 10,
      noSpeechForMs: VOICE_MAX_RECORDING_MS,
    })).toBe(true);
  });
});
