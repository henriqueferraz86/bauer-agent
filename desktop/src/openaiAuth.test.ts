import { describe, expect, it, vi } from "vitest";
import {
  startOpenAIBrowserAuth,
  validateOpenAIAuthorizationUrl,
  waitForOpenAIAuth,
  type OpenAIAuthStatus,
} from "./openaiAuth";

const disconnected: OpenAIAuthStatus = {
  experimental: true,
  connected: false,
  status: "pending",
};

describe("OpenAI browser auth", () => {
  it("aceita somente auth.openai.com em HTTPS", () => {
    expect(validateOpenAIAuthorizationUrl("https://auth.openai.com/oauth/authorize?x=1"))
      .toContain("auth.openai.com");
    expect(() => validateOpenAIAuthorizationUrl("https://evil.example/oauth"))
      .toThrow("inválida");
  });

  it("acompanha até o backend confirmar a conexão", async () => {
    const connected = { ...disconnected, connected: true, status: "connected" as const };
    const read = vi.fn()
      .mockResolvedValueOnce(disconnected)
      .mockResolvedValueOnce(connected);
    const result = await waitForOpenAIAuth(read, {
      attempts: 2,
      delay: async () => undefined,
    });
    expect(result.connected).toBe(true);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("abre o popup antes de aguardar o endpoint start", async () => {
    const calls: string[] = [];
    const popup = { location: { href: "" }, close: vi.fn() };
    const result = await startOpenAIBrowserAuth(
      async () => {
        calls.push("start");
        return {
          authorization_url: "https://auth.openai.com/oauth/authorize?state=safe",
          expires_at: 123,
        };
      },
      async () => ({ ...disconnected, connected: true, status: "connected" }),
      () => {
        calls.push("popup");
        return popup;
      },
      async (read) => read(),
    );
    expect(calls).toEqual(["popup", "start"]);
    expect(popup.location.href).toContain("auth.openai.com");
    expect(result.connected).toBe(true);
  });
});
