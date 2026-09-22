import { describe, expect, it, vi } from "vitest";
import {
  extractWakeCommand,
  isVoiceStop,
  requestMicrophone,
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
