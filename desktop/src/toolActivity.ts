export interface ToolActivity {
  name: string;
  label: string;
  icon: string;
}

const SAFE_TOOL_NAME = /^[a-z][a-z0-9_]{0,63}$/;
const SAFE_ICON_NAME = /^[a-z0-9-]{1,40}$/;

export function sanitizeToolActivity(value: unknown): ToolActivity | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const name = typeof candidate.name === "string" ? candidate.name : "";
  const label = typeof candidate.label === "string"
    ? candidate.label.replace(/[\u0000-\u001f\u007f]/g, " ").trim()
    : "";
  const icon = typeof candidate.icon === "string" && SAFE_ICON_NAME.test(candidate.icon)
    ? candidate.icon
    : "tool";

  if (!SAFE_TOOL_NAME.test(name) || !label || label.length > 80) return null;
  return { name, label, icon };
}

export function parseToolActivityEvent(data: string): ToolActivity | null {
  try {
    const payload: unknown = JSON.parse(data);
    if (!payload || typeof payload !== "object") return null;
    const candidate = payload as Record<string, unknown>;
    if (candidate.internal !== true) return null;
    return sanitizeToolActivity(candidate);
  } catch {
    return null;
  }
}
