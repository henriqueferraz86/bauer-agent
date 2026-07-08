import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";

interface OsCommandResult {
  kind: string;
  label?: string;
  message: string;
  path?: string;
  url?: string;
  approval?: { id: string; status: string; risk_level: string };
  run?: { id: string; status: string; agent_id: string };
  skill_id?: string;
  output?: Record<string, unknown>;
  suggestions?: string[];
}

interface CommandSeed {
  text: string;
  desc: string;
  icon: string;
}

const COMMANDS: CommandSeed[] = [
  { text: "mostrar runs", desc: "Abrir execucoes do runtime", icon: "ti-player-play" },
  { text: "aprovar acao pendente", desc: "Abrir fila de approvals", icon: "ti-shield-check" },
  { text: "rodar agent code", desc: "Criar uma run para o agente code", icon: "ti-robot" },
  { text: "abrir navegador", desc: "Abrir navegador do sistema", icon: "ti-world" },
  { text: "abrir painel de controle", desc: "Solicitar controle de OS via policy", icon: "ti-settings" },
  { text: "pesquisar arquivo", desc: "Abrir workspace/projetos", icon: "ti-file-search" },
  { text: "pausar agente code", desc: "Registrar pausa de um agente", icon: "ti-player-pause" },
  { text: "ver skills", desc: "Listar skills e permissoes", icon: "ti-puzzle" },
  { text: "status do runtime", desc: "Ver adapter ativo e workers", icon: "ti-plug-connected" },
];

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [idx, setIdx] = useState(0);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [recording, setRecording] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const navigate = useNavigate();

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return COMMANDS;
    return COMMANDS.filter((item) => `${item.text} ${item.desc}`.toLowerCase().includes(q));
  }, [query]);
  const selected = matches[Math.min(idx, Math.max(matches.length - 1, 0))];

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.ctrlKey && event.code === "Space") {
        event.preventDefault();
        setOpen((value) => !value);
      }
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!open) return;
    setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  useEffect(() => setIdx(0), [query]);

  function close() {
    setOpen(false);
    setQuery("");
    setStatus("");
  }

  function applyResult(result: OsCommandResult) {
    setStatus(result.message);
    if (result.kind === "navigate" && result.path) {
      navigate(result.path);
      close();
      return;
    }
    if ((result.kind === "run_created" || result.kind === "agent_pause_requested") && result.path) {
      navigate(result.path);
      setStatus(`${result.message} ${result.run?.id || ""}`.trim());
      return;
    }
    if (result.kind === "approval_required") {
      navigate("/approvals");
      setStatus(`${result.message} ${result.approval?.id || ""}`.trim());
      return;
    }
    if (result.kind === "open_external" && result.url) {
      window.open(result.url, "_blank", "noopener,noreferrer");
      close();
      return;
    }
    if (result.kind === "skill_executed") {
      setStatus(`✓ ${result.label || result.skill_id || "Skill"}: ${result.message}`);
      return;
    }
    if (result.kind === "skill_failed" || result.kind === "denied") {
      setStatus(`✗ ${result.label || "Comando"}: ${result.message}`);
    }
  }

  async function execute(text?: string) {
    const command = (text || query || selected?.text || "").trim();
    if (!command || busy) return;
    setBusy(true);
    try {
      const result = await api.post<OsCommandResult>("/api/os/command", { text: command });
      applyResult(result);
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  }

  async function toggleVoice() {
    if (recording) {
      recorderRef.current?.stop();
      return;
    }
    if (busy) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: mime || "audio/webm" });
        if (blob.size === 0) return;
        setBusy(true);
        try {
          const response = await api.upload<{ transcript: string }>("/transcribe", blob, "voice.webm");
          setQuery(response.transcript || "");
          if (response.transcript?.trim()) {
            const result = await api.post<OsCommandResult>("/api/os/command", { text: response.transcript });
            applyResult(result);
          }
        } catch (error) {
          setStatus(String(error));
        } finally {
          setBusy(false);
        }
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
      setStatus("Ouvindo...");
    } catch (error) {
      setStatus(String(error));
    }
  }

  if (!open) return null;

  return (
    <div className="cmd-overlay" onMouseDown={close}>
      <div className="cmd-panel" onMouseDown={(event) => event.stopPropagation()}>
        <div className="cmd-input-row">
          <i className={"ti " + (busy ? "ti-loader-2 spin" : "ti-command")} />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setIdx((value) => (value + 1) % Math.max(matches.length, 1));
              }
              if (event.key === "ArrowUp") {
                event.preventDefault();
                setIdx((value) => (value + Math.max(matches.length, 1) - 1) % Math.max(matches.length, 1));
              }
              if (event.key === "Enter") {
                event.preventDefault();
                void execute(query || selected?.text);
              }
            }}
            placeholder="Digite ou fale um comando do Bauer OS..."
          />
          <button className={"cmd-icon-btn" + (recording ? " recording" : "")} onClick={toggleVoice} title="Falar comando">
            <i className={"ti " + (recording ? "ti-player-stop" : "ti-microphone")} />
          </button>
        </div>
        <div className="cmd-list">
          {matches.map((item, itemIdx) => (
            <button
              className={"cmd-item" + (itemIdx === idx ? " active" : "")}
              key={item.text}
              onMouseEnter={() => setIdx(itemIdx)}
              onClick={() => execute(item.text)}
            >
              <i className={"ti " + item.icon} />
              <span>
                <strong>{item.text}</strong>
                <small>{item.desc}</small>
              </span>
            </button>
          ))}
          {matches.length === 0 && (
            <button className="cmd-item active" onClick={() => execute()}>
              <i className="ti ti-sparkles" />
              <span>
                <strong>{query}</strong>
                <small>Executar como comando livre</small>
              </span>
            </button>
          )}
        </div>
        <div className="cmd-footer">
          <span>Ctrl+Space abre</span>
          <span>Enter executa</span>
          <span>Esc fecha</span>
          {status && <strong>{status}</strong>}
        </div>
      </div>
    </div>
  );
}
