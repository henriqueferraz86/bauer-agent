import { describe, expect, it } from "vitest";
import { autonomyStateLabel, canStopAutonomy, CONTINUOUS_STATES } from "./autonomy";

describe("continuous autonomy safety UI", () => {
  it("exposes every persisted lifecycle state", () => {
    expect(CONTINUOUS_STATES).toEqual(["off", "starting", "running", "stopping", "stopped", "error"]);
  });
  it("only offers immediate stop while the controller is active", () => {
    expect(canStopAutonomy("running")).toBe(true);
    expect(canStopAutonomy("stopping")).toBe(true);
    expect(canStopAutonomy("stopped")).toBe(false);
    expect(autonomyStateLabel("error")).toBe("Erro");
  });
});
