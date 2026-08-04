/**
 * Hud — o mesmo painel de instrumentos do terminal, na web (plano 028 F5).
 *
 * Regra herdada do terminal: NADA DE ENFEITE QUE NÃO SEJA DADO REAL. Por isso
 * este HUD tem menos campos que o do CLI, e a diferença é honesta:
 *
 *   - selo local/nuvem  → derivado do `provider` que o /status devolve
 *   - modelo do turno   → do evento `route` do SSE quando há roteamento; marca
 *                         a divergência com `→`, igual ao terminal
 *   - tok/s ao vivo     → MEDIDO no cliente, contando o que de fato chegou
 *   - custo             → só aparece no /loop, que é quem reporta custo
 *
 * Contexto (o medidor ▰▰▰▱▱) NÃO aparece: o `/status` expõe o TAMANHO da
 * janela, não quanto dela foi usada. Desenhar uma barra a partir disso seria
 * inventar. Quando o serve passar a expor o uso, o campo entra.
 */

export interface HudData {
  provider: string;
  model: string;
  turnModel?: string;      // do evento `route` — pode divergir do configurado
  tokensPerSecond?: number;
  costUsd?: number;
  live?: boolean;
}

/** Providers que rodam na máquina do usuário. */
const LOCAIS = new Set(["ollama", "lmstudio", "llamacpp", "vllm"]);

export default function Hud({ data }: { data: HudData }) {
  const provider = (data.provider || "").trim().toLowerCase();
  const local = LOCAIS.has(provider);
  const modelo = data.turnModel || data.model;
  const divergiu = Boolean(data.turnModel && data.turnModel !== data.model);

  return (
    <div className={"hud" + (data.live ? " live" : "")}>
      {/* Sem provider conhecido o selo NÃO aparece. Cair para "nuvem" quando o
          serve está offline afirmaria que o turno sai da máquina — uma mentira
          sobre soberania de dados, que é justamente o que o selo existe para
          responder. Ausência de dado vira ausência de selo. */}
      {provider && (
        <span className={"hud-seal " + (local ? "local" : "cloud")}>
          <i className={"ti " + (local ? "ti-device-desktop" : "ti-cloud")} />
          {local ? "local" : "nuvem"}
        </span>
      )}

      <span className="hud-model mono" title={divergiu ? `configurado: ${data.model}` : undefined}>
        {divergiu && <span className="hud-diverge">→ </span>}
        {modelo || "—"}
      </span>

      {data.tokensPerSecond != null && data.tokensPerSecond > 0 && (
        <span className="hud-tps mono">{data.tokensPerSecond.toFixed(1)} tok/s</span>
      )}

      {data.costUsd != null && data.costUsd > 0 && (
        <span className="hud-cost mono">${data.costUsd.toFixed(3)}</span>
      )}
    </div>
  );
}
