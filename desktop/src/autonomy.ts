export const CONTINUOUS_STATES = ["off", "starting", "running", "stopping", "stopped", "error"] as const;
export type ContinuousState = typeof CONTINUOUS_STATES[number];

export function autonomyStateLabel(state: string): string {
  return ({ off: "Desligada", starting: "Iniciando", running: "Rodando", stopping: "Parando", stopped: "Parada", error: "Erro" } as Record<string, string>)[state] || state;
}

export function canStopAutonomy(state: string): boolean {
  return state === "starting" || state === "running" || state === "stopping";
}
