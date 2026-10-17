import { describe, expect, it, vi } from "vitest";
import {
  loopIsActive, mergeLoopApprovals, ApprovalCard, approvalStatusText, canApproveRun, mergeApproval,
  parseApprovalEvent, restoreApprovals, sanitizeApproval, submitApprovalDecision, updateApprovalRequest,
} from "./approvalState";

const pending: ApprovalCard = {
  id: "approval-1", status: "pending", operation: "shell.execute",
  tool_name: "run_command", run_id: "run-1", session_id: "session-1",
  reason: "Requer decisão humana", risk_level: "high",
};

describe("approval cards", () => {
  it("keeps autonomous polling active while a card awaits a decision", () => {
    const cards = mergeLoopApprovals(undefined, [pending]);
    expect(cards[pending.id].status).toBe("pending");
    expect(loopIsActive("waiting_approval")).toBe(true);
    expect(loopIsActive("queued")).toBe(true);
    expect(loopIsActive("completed")).toBe(false);
    expect(mergeLoopApprovals(cards, [{ id: pending.id, status: "approved" }])[pending.id].status).toBe("approved");
  });
  it("sends and restores an explicit permanent command decision", async () => {
    const post = vi.fn().mockResolvedValue({});
    let cards = mergeApproval(undefined, pending);
    await submitApprovalDecision(pending, "approve", "always", new Set(), post, (id, patch) => {
      cards = updateApprovalRequest(cards, id, patch)!;
    });
    expect(post).toHaveBeenCalledWith("/api/approvals/approval-1/approve", { scope: "always" });
    expect(restoreApprovals(cards)[pending.id].scope).toBe("always");
  });
  it("keeps public summaries distinct through partial events and storage", () => {
    const docker = parseApprovalEvent(JSON.stringify({ ...pending, summary: "Executar Docker Compose: up" }));
    const python = parseApprovalEvent(JSON.stringify({ ...pending, id: "approval-2", summary: "Executar código Python" }));
    const cards = mergeApproval(mergeApproval(undefined, docker), python);
    const resolved = mergeApproval(cards, { id: pending.id, status: "approved" });
    const restored = restoreApprovals(resolved);
    expect(restored[pending.id].summary).toBe("Executar Docker Compose: up");
    expect(restored["approval-2"].summary).toBe("Executar código Python");
    expect(restored[pending.id].reason).toBe(restored["approval-2"].reason);
    expect(mergeApproval(cards, { id: pending.id, summary: null })[pending.id].summary).toBe(docker?.summary);
  });

  it("sanitizes optional summaries without deriving them from private payloads", () => {
    expect(sanitizeApproval({ ...pending, summary: " Executar\nPython\u0000 TOKEN=private-secret " })?.summary)
      .toBe("Executar Python  TOKEN=[REDACTED]");
    expect(sanitizeApproval({ ...pending, summary: "x".repeat(200) })?.summary).toHaveLength(128);
    for (const summary of [undefined, null, 42, {}, [], "", "   "]) {
      expect(sanitizeApproval({ ...pending, summary, payload: { command: "python private/path --token=secret" } })).not.toHaveProperty("summary");
    }
  });

  it("keeps distinct IDs and merges partial SSE decisions without losing metadata", () => {
    const first = mergeApproval(undefined, pending);
    const both = mergeApproval(first, { ...pending, id: "approval-2" });
    const resolved = mergeApproval(both, parseApprovalEvent('{"id":"approval-1","status":"approved"}'));
    expect(Object.keys(resolved)).toEqual(["approval-1", "approval-2"]);
    expect(resolved["approval-1"]).toMatchObject({ ...pending, status: "approved" });
    expect(resolved["approval-2"]).toEqual({ ...pending, id: "approval-2" });
    expect(first["approval-1"].status).toBe("pending");
  });

  it("does not reopen a resolved card when pending events replay", () => {
    const approved = mergeApproval(undefined, { ...pending, status: "approved", scope: "run" });
    expect(mergeApproval(approved, { ...pending, scope: "once" })[pending.id]).toMatchObject({ status: "approved", scope: "run" });
    expect(mergeApproval(approved, { id: pending.id, reason: null, run_id: null })[pending.id]).toMatchObject({
      reason: pending.reason, run_id: pending.run_id,
    });
  });

  it("drops private fields and redacts credentials before state or local storage", () => {
    const source = {
      ...pending, payload: { command: "private command" }, args: { secret: "value" },
      token: "private token", arbitrary: "private data", submitting: true, error: "private error",
      reason: 'TOKEN=example-secret password="two word secret" Bearer abc.def.ghi https://user:password@example.test',
    };
    const card = sanitizeApproval(source)!;
    expect(card).not.toHaveProperty("payload");
    expect(card).not.toHaveProperty("args");
    expect(card).not.toHaveProperty("token");
    expect(card).not.toHaveProperty("arbitrary");
    expect(card).not.toHaveProperty("error");
    expect(card).not.toHaveProperty("submitting");
    expect(card.reason).toBe('TOKEN=[REDACTED] password=[REDACTED] Bearer [REDACTED] https://[REDACTED]@example.test');
    expect(restoreApprovals({ [pending.id]: source })[pending.id]).toMatchObject(card);
  });

  it("migrates a legacy single card without persisting transient request state", () => {
    expect(restoreApprovals(undefined, { ...pending, submitting: true, error: "transport secret" })).toEqual({ [pending.id]: pending });
    expect(restoreApprovals({ [pending.id]: { ...pending, scope: "run", status: "approved" } })[pending.id]).toMatchObject({ scope: "run", status: "approved" });
  });

  it("rejects malformed events and does not invent pending for unknown status", () => {
    for (const data of ["not-json", "null", "[]", "{}", '{"id":12}', '{"id":"../unsafe"}']) {
      expect(parseApprovalEvent(data)).toBeNull();
    }
    expect(parseApprovalEvent('{"id":"approval-1","status":"anything"}')).toEqual({ id: "approval-1" });
    expect(mergeApproval(undefined, { id: "constructor", status: "pending" }).constructor).toMatchObject({ id: "constructor", status: "pending" });
  });

  it("offers task scope only for shell.execute/run_command with run and session", () => {
    expect(canApproveRun(pending)).toBe(true);
    for (const patch of [
      { operation: "runtime.execute" }, { tool_name: "write_file" }, { run_id: undefined },
      { session_id: undefined }, { run_id: " " }, { session_id: "" },
    ]) expect(canApproveRun({ ...pending, ...patch })).toBe(false);
  });

  it("separates confirmed decisions, final done and disconnected streams", () => {
    const card: ApprovalCard = { ...pending, status: "approved" };
    expect(approvalStatusText(card, true)).toContain("aguardando conclusão");
    expect(approvalStatusText(card, false, true)).toContain("turno encerrado");
    expect(approvalStatusText(card, false)).toContain("Consulte a execução");
    expect(approvalStatusText({ ...card, scope: "run" }, false, true)).toContain("Comandos desta tarefa aprovados");
    expect(approvalStatusText({ ...card, status: "denied" }, true)).toBe("Aprovação negada.");
    expect(approvalStatusText({ ...card, status: "expired" }, false)).toBe("Aprovação expirada.");
    expect(approvalStatusText({ ...card, status: "cancelled" }, false)).toBe("Aprovação cancelada.");
    expect(approvalStatusText({ id: card.id }, false)).toContain("não confirmado");
  });
});

describe("approval decisions", () => {
  it("does not approve optimistically and blocks duplicate/mixed clicks until REST resolves", async () => {
    let resolve!: (value: unknown) => void;
    const post = vi.fn(() => new Promise((done) => { resolve = done; }));
    const update = vi.fn();
    const requests = new Set<string>();
    const first = submitApprovalDecision(pending, "approve", "once", requests, post, update);
    expect(update.mock.calls).toEqual([[pending.id, { submitting: true, error: undefined }]]);
    await submitApprovalDecision(pending, "approve", "run", requests, post, update);
    await submitApprovalDecision(pending, "deny", "once", requests, post, update);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/api/approvals/approval-1/approve", undefined);
    resolve({ status: "queued" });
    await first;
    expect(update).toHaveBeenLastCalledWith(pending.id, { status: "approved", scope: "once", submitting: false, error: undefined });
    expect(requests.size).toBe(0);
  });

  it("sends run scope only on the explicit eligible action", async () => {
    const post = vi.fn().mockResolvedValue({});
    const update = vi.fn();
    await submitApprovalDecision(pending, "approve", "run", new Set(), post, update);
    expect(post).toHaveBeenCalledWith("/api/approvals/approval-1/approve", { scope: "run" });
    expect(update).toHaveBeenLastCalledWith(pending.id, expect.objectContaining({ status: "approved", scope: "run" }));
    post.mockClear();
    await submitApprovalDecision({ ...pending, session_id: undefined }, "approve", "run", new Set(), post, update);
    await submitApprovalDecision(pending, "deny", "run", new Set(), post, update);
    await submitApprovalDecision({ ...pending, status: "approved" }, "approve", "once", new Set(), post, update);
    expect(post).not.toHaveBeenCalled();
  });

  it("keeps failures retryable and never copies an exception into card/chat data", async () => {
    const post = vi.fn().mockRejectedValueOnce(new Error("500: TOKEN=private-secret")).mockResolvedValueOnce({});
    const update = vi.fn();
    const requests = new Set<string>();
    await submitApprovalDecision(pending, "approve", "once", requests, post, update);
    expect(update).toHaveBeenLastCalledWith(pending.id, { submitting: false, error: expect.stringContaining("Tente novamente neste cartão") });
    expect(JSON.stringify(update.mock.calls)).not.toContain("private-secret");
    expect(update.mock.calls.some(([, patch]) => patch.status)).toBe(false);
    expect(requests.size).toBe(0);
    await submitApprovalDecision(pending, "approve", "once", requests, post, update);
    expect(post).toHaveBeenCalledTimes(2);
    expect(update).toHaveBeenLastCalledWith(pending.id, expect.objectContaining({ status: "approved" }));
  });

  it("does not block decisions on a different approval ID", async () => {
    const post = vi.fn().mockResolvedValue({});
    const update = vi.fn();
    await submitApprovalDecision({ ...pending, id: "approval-2" }, "deny", "once", new Set([pending.id]), post, update);
    expect(post).toHaveBeenCalledWith("/api/approvals/approval-2/deny", undefined);
    expect(update).toHaveBeenLastCalledWith("approval-2", expect.objectContaining({ status: "denied" }));
  });

  it("preserves an SSE decision that arrives while REST is outstanding", async () => {
    let reject!: (reason: unknown) => void;
    const post = () => new Promise((_, fail) => { reject = fail; });
    let state = mergeApproval(undefined, pending);
    const request = submitApprovalDecision(pending, "approve", "once", new Set(), post, (id, patch) => {
      state = updateApprovalRequest(state, id, patch)!;
    });
    expect(state[pending.id]).toMatchObject({ status: "pending", submitting: true });
    state = mergeApproval(state, { id: pending.id, status: "denied" });
    reject(new Error("network failure"));
    await request;
    expect(state[pending.id]).toMatchObject({ status: "denied", submitting: false });
    expect(state[pending.id].error).toBeUndefined();
  });

  it("keeps request errors on their own card and clears them on retry", async () => {
    let state = mergeApproval(mergeApproval(undefined, pending), { ...pending, id: "approval-2" });
    const second = state["approval-2"];
    const update = (id: string, patch: Partial<ApprovalCard>) => { state = updateApprovalRequest(state, id, patch)!; };
    const post = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({});
    await submitApprovalDecision(state[pending.id], "approve", "once", new Set(), post, update);
    expect(state[pending.id].status).toBe("pending");
    expect(state[pending.id].error).toContain("Tente novamente");
    expect(state["approval-2"]).toBe(second);
    await submitApprovalDecision(state[pending.id], "approve", "once", new Set(), post, update);
    expect(state[pending.id]).toMatchObject({ status: "approved", submitting: false });
    expect(state[pending.id].error).toBeUndefined();
    expect(updateApprovalRequest(state, "missing", { submitting: true })).toBe(state);
  });
});
