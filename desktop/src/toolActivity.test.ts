import { describe, expect, it } from "vitest";
import { parseToolActivityEvent, sanitizeToolActivity } from "./toolActivity";

describe("tool activity events", () => {
  it("accepts only the safe label/name/icon fields from an internal event", () => {
    expect(parseToolActivityEvent(JSON.stringify({
      internal: true,
      name: "read_file",
      label: "Lendo arquivos",
      icon: "file-text",
      args: { path: "private.txt" },
      result: "private content",
    }))).toEqual({ name: "read_file", label: "Lendo arquivos", icon: "file-text" });
  });

  it("ignores malformed, non-internal, or unsafe events", () => {
    expect(parseToolActivityEvent("not-json")).toBeNull();
    expect(parseToolActivityEvent(JSON.stringify({ internal: false, name: "read_file", label: "Lendo" }))).toBeNull();
    expect(parseToolActivityEvent(JSON.stringify({ internal: true, name: "run command", label: "Executando" }))).toBeNull();
    expect(sanitizeToolActivity({ name: "read_file", label: "x".repeat(81) })).toBeNull();
  });

  it("falls back to a safe icon and strips control characters from labels", () => {
    expect(sanitizeToolActivity({ name: "write_file", label: "Escrevendo\narquivo", icon: "x onerror=alert(1)" }))
      .toEqual({ name: "write_file", label: "Escrevendo arquivo", icon: "tool" });
  });
});
