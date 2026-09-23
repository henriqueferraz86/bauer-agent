import { describe, expect, it } from "vitest";
import { authMode, EMPTY_AUTH_STATE, needsAuthScreen } from "./auth";
import { cookieValue } from "./api/client";

describe("serve web auth state", () => {
  it("keeps legacy open servers outside the auth gate", () => {
    expect(authMode(EMPTY_AUTH_STATE)).toBe("disabled");
    expect(needsAuthScreen(EMPTY_AUTH_STATE)).toBe(false);
  });

  it("distinguishes setup, login and authenticated modes", () => {
    const enabled = { ...EMPTY_AUTH_STATE, enabled: true };
    expect(authMode({ ...enabled, setup_required: true })).toBe("setup");
    expect(authMode(enabled)).toBe("login");
    expect(authMode({ ...enabled, authenticated: true })).toBe("authenticated");
  });

  it("reads the csrf double-submit cookie", () => {
    expect(cookieValue("a=1; bauer_csrf=abc%20123; theme=dark", "bauer_csrf")).toBe("abc 123");
    expect(cookieValue("a=1", "bauer_csrf")).toBe("");
  });
});
