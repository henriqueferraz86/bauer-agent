import { useEffect, useState } from "react";
import { api } from "../api/client";
import { autonomyStateLabel, canStopAutonomy } from "../autonomy";

type Target = {
  id: string; name: string; url: string; state: string; enabled: boolean;
  type?: string; auto_recover?: boolean; recovery_action?: string; auto_discovered?: boolean;
  status_code?: number | null; latency_ms?: number | null;
  last_checked_at?: string | null; last_error?: string | null;
};
type AutonomyStatus = {
  state: {
    state: string; message: string; last_check_at?: string | null;
    incidents: number; recommendations: number; alert_level: string;
    voice_enabled: boolean; error?: string | null;
  };
  configured_targets: number;
  targets: Target[];
  incidents: { id: string; target: string; message: string; created_at: string }[];
  recommendations: { id: string; message: string; safe_action: string; created_at: string }[];
  delegations: { id: string; kind: string; run_id: string; branch?: string; status: string }[];
};

export default function Autonomy() {
  const [data, setData] = useState<AutonomyStatus | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [kind, setKind] = useState("application");
  const [targetName, setTargetName] = useState("");
  const [targetUrl, setTargetUrl] = useState("");
  const [targetInterval, setTargetInterval] = useState("60");
  const [targetTimeout, setTargetTimeout] = useState("5");
  const [targetExpected, setTargetExpected] = useState("200");
  const [targetType, setTargetType] = useState("docker_container");
  const [targetContainer, setTargetContainer] = useState("");
  const [targetProcess, setTargetProcess] = useState("");
  const [targetCommand, setTargetCommand] = useState("");
  const [targetAutoRecover, setTargetAutoRecover] = useState(true);

  const load = () => api.get<AutonomyStatus>("/api/autonomy/status")
    .then((value) => { setData(value); setError(""); })
    .catch((err) => setError(String(err)));

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, []);

  async function start() {
    try { await api.post("/api/autonomy/start", {}); await load(); }
    catch (err) { setError(String(err)); }
  }
  async function stop() {
    try { await api.post("/api/autonomy/stop"); await load(); }
    catch (err) { setError(String(err)); }
  }
  async function configureAlerts(voice_enabled: boolean, alert_level: string) {
    try { await api.post("/api/autonomy/alerts", { voice_enabled, alert_level }); await load(); }
    catch (err) { setError(String(err)); }
  }
  async function delegate() {
    if (!message.trim()) return;
    try {
      await api.post("/api/autonomy/delegate", { kind, message });
      setMessage(""); await load();
    } catch (err) { setError(String(err)); }
  }

  async function registerTarget() {
    const ready = targetName.trim() && (targetType === "process"
      ? targetProcess.trim() && targetCommand.trim()
      : (targetType === "docker_container" ? targetContainer.trim() : targetUrl.trim()));
    if (!ready) return;
    try {
      await api.post("/api/autonomy/targets", {
        name: targetName.trim(), type: targetType, url: targetUrl.trim(),
        interval_s: Number(targetInterval), timeout_s: Number(targetTimeout),
        expected_status: Number(targetExpected), enabled: true,
        auto_recover: targetType === "docker_container" || (targetType === "process" && targetAutoRecover),
        recovery_action: targetType === "docker_container" ? "docker_recover" : targetType === "process" ? "process_restart" : null,
        container_name: targetContainer.trim() || null,
        process_name: targetProcess.trim() || null,
        process_command: targetCommand.trim() ? targetCommand.trim().split(/\s+/) : [],
      });
      setTargetName(""); setTargetUrl("");
      setTargetInterval("60"); setTargetTimeout("5"); setTargetExpected("200");
      setTargetContainer(""); setTargetProcess(""); setTargetCommand("");
      await load();
    } catch (err) { setError(String(err)); }
  }

  async function setTargetEnabled(target: Target) {
    try {
      await api.post(`/api/autonomy/targets/${target.id}/enabled`, { enabled: !target.enabled });
      await load();
    } catch (err) { setError(String(err)); }
  }

  async function setAutoRecover(target: Target) {
    try {
      await api.post(`/api/autonomy/targets/${target.id}/auto-recover`, { enabled: !target.auto_recover });
      await load();
    } catch (err) { setError(String(err)); }
  }

  async function deleteTarget(target: Target) {
    if (!window.confirm(`Excluir o alvo "${target.name}"?`)) return;
    try {
      await api.del(`/api/autonomy/targets/${target.id}`);
      await load();
    } catch (err) { setError(String(err)); }
  }

  const state = data?.state.state || "off";
  const targetReady = targetName.trim() && (targetType === "process"
    ? targetProcess.trim() && targetCommand.trim()
      : (targetType === "docker_container" ? targetContainer.trim() : targetUrl.trim()));
  const formatWhen = (value?: string) => value ? new Date(value).toLocaleString() : "data não disponível";
  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-eye head-icon" />
        <span className="title">Autonomia contínua</span>
        <span className="tag">{autonomyStateLabel(state)}</span>
        <div className="spacer" />
        {canStopAutonomy(state) ?
          <button className="btn" onClick={stop}><i className="ti ti-player-stop" /> Parar imediatamente</button> :
          <button className="btn primary" onClick={start}><i className="ti ti-player-play" /> Iniciar observação</button>}
      </div>
      <div className="content">
        {error && <div className="card" style={{ color: "var(--bauer-bad)", marginBottom: 12 }}>{error}</div>}
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div><div className="section-label">Modo seguro</div><div className="muted">{data?.state.message || "Carregando estado."}</div></div>
            <div className="row">
              <select className="in" style={{ width: 130 }} value={data?.state.alert_level || "important"}
                onChange={(e) => configureAlerts(data?.state.voice_enabled || false, e.target.value)}>
                <option value="off">alertas off</option><option value="important">importantes</option><option value="all">todos</option>
              </select>
              <button className={"btn" + (data?.state.voice_enabled ? " primary" : "")} onClick={() => configureAlerts(!(data?.state.voice_enabled || false), data?.state.alert_level || "important")}>
                <i className="ti ti-volume" /> Voz {data?.state.voice_enabled ? "on" : "off"}
              </button>
            </div>
          </div>
          <div className="muted" style={{ marginTop: 10 }}>Alvos marcados para recuperação podem ser diagnosticados e reiniciados conforme a receita cadastrada. Sem alvo configurado, nenhuma rede é sondada.</div>
        </div>
        <div className="section-label">Cadastrar alvo monitorado</div>
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <select className="in" style={{ width: 170 }} value={targetType} onChange={(e) => setTargetType(e.target.value)}>
              <option value="docker_container">Container Docker</option>
              <option value="process">Processo</option>
              <option value="http_health">Somente HTTP</option>
            </select>
            <input className="in" style={{ flex: "1 1 180px" }} value={targetName}
              onChange={(e) => setTargetName(e.target.value)} placeholder="Nome (ex.: mt5_dashboard)" />
            <input className="in" style={{ flex: "2 1 280px" }} value={targetUrl}
              onChange={(e) => setTargetUrl(e.target.value)} placeholder={targetType === "docker_container" ? "URL opcional de saúde" : "URL de saúde (ex.: http://127.0.0.1:8010/)"} />
            <input className="in" style={{ width: 90 }} type="number" min="1" value={targetInterval}
              onChange={(e) => setTargetInterval(e.target.value)} title="Intervalo em segundos" placeholder="Intervalo" />
            <input className="in" style={{ width: 80 }} type="number" min="0.1" step="0.1" value={targetTimeout}
              onChange={(e) => setTargetTimeout(e.target.value)} title="Timeout em segundos" placeholder="Timeout" />
            <input className="in" style={{ width: 80 }} type="number" min="100" max="599" value={targetExpected}
              onChange={(e) => setTargetExpected(e.target.value)} title="Status HTTP esperado" placeholder="HTTP" />
            <button className="btn primary" onClick={registerTarget}
              disabled={!targetReady}>
              <i className="ti ti-plus" /> Cadastrar
            </button>
          </div>
          {targetType === "docker_container" && <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
            <input className="in" style={{ flex: "1 1 260px" }} value={targetContainer}
              onChange={(e) => setTargetContainer(e.target.value)} placeholder="Nome do container (ex.: nautilus-mt5-dashboard)" />
            <span className="muted">autocorreção sempre ativa</span>
          </div>}
          {targetType === "process" && <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
            <input className="in" style={{ flex: "1 1 180px" }} value={targetProcess}
              onChange={(e) => setTargetProcess(e.target.value)} placeholder="Processo (ex.: terminal.exe)" />
            <input className="in" style={{ flex: "2 1 280px" }} value={targetCommand}
              onChange={(e) => setTargetCommand(e.target.value)} placeholder="Comando de inicialização" />
            <label className="muted"><input type="checkbox" checked={targetAutoRecover} onChange={(e) => setTargetAutoRecover(e.target.checked)} /> recuperação automática</label>
          </div>}
          <div className="muted" style={{ marginTop: 8, fontSize: 11 }}>
            Containers podem ser monitorados pelo estado do Docker; a URL é opcional. A autocorreção pode ser desligada por alvo.
          </div>
        </div>
        <div className="section-label">Alvos configurados ({data?.configured_targets || 0})</div>
        {data?.targets.length ? data.targets.map((target) => (
          <div className="list-item" key={target.id}>
            <div style={{ flex: 1 }}><div className="mono" style={{ color: "#e6edf3" }}>{target.name}</div><div className="muted" style={{ fontSize: 11 }}>{target.url} · {target.last_checked_at || "ainda não verificado"}</div></div>
            <span className={"tag" + (target.state === "healthy" ? " green" : target.state === "failed" ? " red" : "")}>{target.enabled ? target.state : "pausado"}</span>
            <span className={"tag " + (target.auto_recover ? "green" : "red")}>autocorreção {target.auto_recover ? "ativa" : "desligada"}</span>
            {target.latency_ms != null && <span className="tag">{target.latency_ms} ms</span>}
            {target.type !== "http_health" && <button
              className={"btn" + (target.auto_recover ? "" : " danger")}
              onClick={() => setAutoRecover(target)}
            >
              <i className={target.auto_recover ? "ti ti-shield-check" : "ti ti-shield-off"} />
              {target.auto_recover ? "Desligar autocorreção" : "Ligar autocorreção"}
            </button>}
            <button className="btn danger" onClick={() => deleteTarget(target)}>
              <i className="ti ti-trash" /> Excluir
            </button>
            <button className="btn" onClick={() => setTargetEnabled(target)}>
              <i className={target.enabled ? "ti ti-player-pause" : "ti ti-player-play"} /> {target.enabled ? "Pausar" : "Retomar"}
            </button>
          </div>
        )) : <div className="empty">Nenhum alvo. Adicione `continuous_autonomy.targets` ao config.yaml.</div>}

        <div className="section-label" style={{ marginTop: 18 }}>Delegar trabalho autônomo em workspace isolado</div>
        <div className="card">
          <div className="row"><select className="in" style={{ width: 180 }} value={kind} onChange={(e) => setKind(e.target.value)}><option value="application">criar aplicação</option><option value="bauer_improvement">melhorar o Bauer</option></select><input className="in" value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Descreva o objetivo" onKeyDown={(e) => e.key === "Enter" && delegate()} /><button className="btn primary" onClick={delegate} disabled={!message.trim()}>Delegar</button></div>
          <div className="muted" style={{ marginTop: 8, fontSize: 11 }}>A execução usa o /loop real, RunManager, Kernel, budget e kill-switch. O branch fica aguardando aprovação para integrar.</div>
        </div>
        <div className="section-label" style={{ marginTop: 18 }}>Incidentes e recomendações</div>
        {!data?.incidents.length && !data?.recommendations.length ? <div className="empty">Nenhum incidente.</div> : <>
          {data?.incidents.slice(-5).reverse().map((incident) => <div className="list-item" key={incident.id}><div style={{ flex: 1 }}><div className="mono">{incident.target}</div><div className="muted">{formatWhen(incident.created_at)} · {incident.message}</div></div><span className="tag red">incidente</span></div>)}
          {data?.recommendations.slice(-5).reverse().map((item) => <div className="list-item" key={item.id}><div style={{ flex: 1 }}><div>{item.message}</div><div className="muted">{formatWhen(item.created_at)} · ação segura: {item.safe_action}</div></div><span className="tag accent">revisar</span></div>)}
        </>}
      </div>
    </div>
  );
}
