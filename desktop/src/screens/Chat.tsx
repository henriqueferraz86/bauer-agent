import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, streamSSE } from "../api/client";
import Markdown from "../components/Markdown";

interface ToolCall { name: string; label?: string; icon?: string; }
interface SkillTag { name: string; score: number | null; }
interface RouteTag { tier: string; model: string; }
interface LoopTag {
  runId: string;
  state: string;              // running | completed | stopped | failed
  rounds: number;
  toolCalls: number;
  costUsd: number;
  activity?: string;          // o que está fazendo agora (feedback ao vivo)
  stopReason?: string | null;
}
interface Message {
  role: "user" | "assistant";
  text: string;
  tools?: ToolCall[];
  skill?: SkillTag;
  route?: RouteTag;
  loop?: LoopTag;
  streaming?: boolean;
}

interface LoopStatusResponse {
  run_id: string;
  state: string;
  rounds: number | null;
  tool_calls: number | null;
  cost_usd: number | null;
  activity?: string;
  stop_reason?: string | null;
  last_text?: string;
}

interface SlashCommand {
  cmd: string;
  desc: string;
  run: (arg: string) => void | Promise<void>;
}

const CHAT_STATE_KEY = "bauer.chatState.v1";

function loadChatState(): { messages: Message[]; sessionId: string } {
  try {
    const raw = localStorage.getItem(CHAT_STATE_KEY);
    if (!raw) return { messages: [], sessionId: "" };
    const parsed = JSON.parse(raw) as { messages?: Message[]; sessionId?: string };
    return {
      messages: Array.isArray(parsed.messages)
        ? parsed.messages.map((m) => ({ ...m, streaming: false }))
        : [],
      sessionId: typeof parsed.sessionId === "string" ? parsed.sessionId : "",
    };
  } catch {
    return { messages: [], sessionId: "" };
  }
}

export default function Chat() {
  const initialState = useRef(loadChatState());
  const [messages, setMessages] = useState<Message[]>(initialState.current.messages);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string>(initialState.current.sessionId);
  const [palIdx, setPalIdx] = useState(0);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const scroll = () => requestAnimationFrame(() => endRef.current?.scrollIntoView({ behavior: "smooth" }));

  useEffect(() => {
    const cleanMessages = messages.map((m) => ({ ...m, streaming: false }));
    localStorage.setItem(CHAT_STATE_KEY, JSON.stringify({ messages: cleanMessages, sessionId }));
  }, [messages, sessionId]);

  useEffect(() => {
    scroll();
  }, []);

  function appendInfo(text: string) {
    setMessages((m) => [...m, { role: "assistant", text }]);
    scroll();
  }
  function resetSession() {
    setMessages([]);
    setSessionId("");
    localStorage.removeItem(CHAT_STATE_KEY);
  }

  // ── Modo autônomo (/loop no serve) ────────────────────────────────────────
  // POST /loop dispara o laço em background no servidor; aqui só acompanhamos
  // por polling (GET /loop/{run_id}) e atualizamos o card da mensagem.
  const loopPollers = useRef<Map<string, number>>(new Map());

  function updateLoopMessage(runId: string, patch: (m: Message) => void) {
    setMessages((msgs) => {
      const copy = [...msgs];
      for (let i = copy.length - 1; i >= 0; i--) {
        if (copy[i].loop?.runId === runId) {
          const clone = { ...copy[i], loop: { ...copy[i].loop! } };
          patch(clone);
          copy[i] = clone;
          break;
        }
      }
      return copy;
    });
  }

  function stopPolling(runId: string) {
    const t = loopPollers.current.get(runId);
    if (t !== undefined) { window.clearInterval(t); loopPollers.current.delete(runId); }
  }

  function pollLoop(runId: string) {
    if (loopPollers.current.has(runId)) return;
    const timer = window.setInterval(async () => {
      try {
        const s = await api.get<LoopStatusResponse>(`/loop/${runId}`);
        updateLoopMessage(runId, (m) => {
          m.loop!.state = s.state;
          m.loop!.rounds = s.rounds ?? m.loop!.rounds;
          m.loop!.toolCalls = s.tool_calls ?? m.loop!.toolCalls;
          m.loop!.costUsd = s.cost_usd ?? m.loop!.costUsd;
          m.loop!.activity = s.activity ?? "";
          m.loop!.stopReason = s.stop_reason ?? null;
          // texto parcial ao vivo (feedback durante a rodada) + final no fim
          if (s.last_text) m.text = s.last_text;
          if (s.state !== "running") m.streaming = false;
        });
        if (s.state !== "running") stopPolling(runId);
      } catch {
        /* serve fora do ar? tenta de novo no próximo tick */
      }
    }, 2000);
    loopPollers.current.set(runId, timer);
  }

  async function startLoop(goal: string) {
    if (!goal.trim()) {
      appendInfo("Uso: /loop OBJETIVO — ex: /loop construa o site conforme o SPEC, não pare até concluir");
      return;
    }
    setMessages((m) => [...m, { role: "user", text: `/loop ${goal}` }]);
    try {
      const r = await api.post<{ run_id: string; session_id: string; limits: Record<string, number> }>(
        "/loop", { message: goal, ...(sessionId ? { session_id: sessionId } : {}) },
      );
      setSessionId(r.session_id);
      setMessages((m) => [...m, {
        role: "assistant", text: "", streaming: true,
        loop: { runId: r.run_id, state: "running", rounds: 0, toolCalls: 0, costUsd: 0 },
      }]);
      scroll();
      pollLoop(r.run_id);
    } catch (e) {
      appendInfo(`[Erro ao iniciar o loop: ${e}]`);
    }
  }

  async function stopLoop(runId: string) {
    try {
      await api.post(`/loop/${runId}/stop`);
      updateLoopMessage(runId, (m) => { m.loop!.state = "stopping"; });
    } catch (e) {
      appendInfo(`[Erro ao parar o loop: ${e}]`);
    }
  }

  // Retoma o acompanhamento de loops que ficaram rodando (reload da página):
  // o GET /loop/{id} responde mesmo pós-restart (cai no Run persistido).
  useEffect(() => {
    for (const m of initialState.current.messages) {
      if (m.loop && (m.loop.state === "running" || m.loop.state === "stopping")) {
        pollLoop(m.loop.runId);
      }
    }
    const pollers = loopPollers.current;
    return () => { pollers.forEach((t) => window.clearInterval(t)); pollers.clear(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Comandos de barra (paridade com o menu "/" do Telegram) ───────────────
  const COMMANDS: SlashCommand[] = [
    { cmd: "/start", desc: "Menu inicial", run: () => appendInfo(helpText()) },
    { cmd: "/help", desc: "Ajuda e comandos disponíveis", run: () => appendInfo(helpText()) },
    {
      cmd: "/status",
      desc: "Modelo, contexto e sessão atual",
      run: async () => {
        try {
          const s = await api.get<{ model: string; provider: string; context_tokens: number; tools: string[]; auth_enabled: boolean }>("/status");
          const g = await api.get<{ running: boolean; telegram: boolean }>("/api/gateway/status").catch(() => null);
          appendInfo(
            `📊 Status\n` +
            `• provider: ${s.provider || "(default)"}\n` +
            `• modelo: ${s.model}\n` +
            `• contexto: ${s.context_tokens} tokens\n` +
            `• tools: ${s.tools.length}\n` +
            `• auth: ${s.auth_enabled ? "habilitada" : "desabilitada"}\n` +
            `• gateway: ${g?.running ? "online" : "offline"}\n` +
            `• sessão: ${sessionId ? sessionId.slice(0, 8) : "(nova)"}`
          );
        } catch (e) {
          appendInfo(`[Erro ao obter status: ${e}]`);
        }
      },
    },
    {
      cmd: "/model",
      desc: "Trocar modelo (ex: /model gpt-4o ou /model openai gpt-4o)",
      run: async (arg) => {
        if (!arg) { navigate("/models"); return; }
        // Suporta "/model PROVIDER MODEL_ID" para trocar provider junto
        const parts = arg.trim().split(/\s+/);
        const provider = parts.length >= 2 ? parts[0] : "";
        const model = parts.length >= 2 ? parts.slice(1).join(" ") : parts[0];
        try {
          await api.post("/models/switch", { model, ...(provider ? { provider } : {}) });
          appendInfo(`✅ Modelo trocado para \`${model}\`${provider ? ` (${provider})` : ""}.`);
        } catch (e) {
          appendInfo(`[Erro ao trocar modelo: ${e}]`);
        }
      },
    },
    {
      cmd: "/provider",
      desc: "Ver providers disponíveis ou filtrar modelos por provider",
      run: async (arg) => {
        if (arg) { navigate(`/models?provider=${encodeURIComponent(arg)}`); return; }
        try {
          const r = await api.get<{ providers: string[] }>("/api/models/providers");
          appendInfo(`Providers disponíveis:\n${r.providers.join(", ")}\n\nUse /provider NOME para ver modelos, ou /model PROVIDER MODEL para trocar.`);
        } catch (e) {
          appendInfo(`[Erro: ${e}]`);
        }
      },
    },
    { cmd: "/tasks", desc: "Tarefas do kanban do workspace", run: () => navigate("/kanban") },
    {
      cmd: "/loop",
      desc: "Modo autônomo: trabalha sozinho até concluir (ex: /loop construa o site do SPEC)",
      run: (arg) => startLoop(arg),
    },
    { cmd: "/new", desc: "Conversa nova (apaga o histórico)", run: () => resetSession() },
    { cmd: "/clear", desc: "O mesmo que /new", run: () => resetSession() },
  ];

  function helpText(): string {
    return "Comandos disponíveis:\n" + COMMANDS.map((c) => `${c.cmd} — ${c.desc}`).join("\n");
  }

  // Paleta visível quando a mensagem começa com "/". Filtra pelo que foi digitado.
  const typed = input.startsWith("/") ? input.slice(1).split(" ")[0].toLowerCase() : null;
  const matches = typed !== null ? COMMANDS.filter((c) => c.cmd.slice(1).startsWith(typed)) : [];
  const showPalette = typed !== null && matches.length > 0 && !busy;
  const selIdx = Math.min(palIdx, matches.length - 1);

  function execCommand(c: SlashCommand) {
    const arg = input.slice(c.cmd.length).trim();
    setInput("");
    setPalIdx(0);
    void c.run(arg);
  }

  async function send(overrideText?: string) {
    const text = (overrideText ?? input).trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text }]);
    setMessages((m) => [...m, { role: "assistant", text: "", tools: [], streaming: true }]);
    scroll();

    const qs = new URLSearchParams({ message: text });
    if (sessionId) qs.set("session_id", sessionId);

    try {
      await streamSSE(`/stream?${qs.toString()}`, (e) => {
        setMessages((m) => {
          const copy = [...m];
          const last = copy[copy.length - 1];
          if (e.event === "skill") {
            try {
              const s = JSON.parse(e.data) as { name: string; score: number | null };
              if (s.name) last.skill = { name: s.name, score: s.score ?? null };
            } catch { /* ignora payload malformado */ }
          } else if (e.event === "route") {
            try {
              const r = JSON.parse(e.data) as { tier: string; model: string };
              if (r.model) last.route = { tier: r.tier, model: r.model };
            } catch { /* ignora payload malformado */ }
          } else if (e.event === "tool") {
            let tc: ToolCall = { name: e.data };
            try {
              const parsed = JSON.parse(e.data);
              if (parsed && parsed.name) tc = { name: parsed.name, label: parsed.label, icon: parsed.icon };
            } catch { /* payload legado = nome cru */ }
            last.tools = [...(last.tools || []), tc];
          } else if (e.event === "done") {
            setSessionId(e.data);
            last.streaming = false;
          } else {
            last.text += e.data;
          }
          return copy;
        });
        scroll();
      });
    } catch (err) {
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1].text += `\n[Erro: ${err}]`;
        copy[copy.length - 1].streaming = false;
        return copy;
      });
    } finally {
      setMessages((m) => {
        const copy = [...m];
        if (copy.length) copy[copy.length - 1].streaming = false;
        return copy;
      });
      setBusy(false);
    }
  }

  // ── Microfone: grava, transcreve (STT default do gateway) e envia ─────────
  async function toggleRecording() {
    if (recording) {
      recorderRef.current?.stop();
      return;
    }
    if (busy || transcribing) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: mime || "audio/webm" });
        if (blob.size === 0) return;
        setTranscribing(true);
        try {
          const r = await api.upload<{ transcript: string; provider: string }>("/transcribe", blob, "voice.webm");
          if (r.transcript?.trim()) await send(r.transcript);
        } catch (e) {
          appendInfo(`[Erro na transcrição: ${e}]`);
        } finally {
          setTranscribing(false);
        }
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch (e) {
      appendInfo(`[Não consegui acessar o microfone: ${e}]`);
    }
  }

  function onKey(e: React.KeyboardEvent) {
    if (showPalette) {
      if (e.key === "ArrowDown") { e.preventDefault(); setPalIdx((i) => (Math.min(i, matches.length - 1) + 1) % matches.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setPalIdx((i) => (Math.min(i, matches.length - 1) + matches.length - 1) % matches.length); return; }
      if (e.key === "Tab") { e.preventDefault(); setInput(matches[selIdx].cmd + " "); setPalIdx(0); return; }
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); execCommand(matches[selIdx]); return; }
      if (e.key === "Escape") { e.preventDefault(); setInput(""); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-message-2 head-icon" />
        <span className="title">Chat</span>
        {sessionId && <span className="sub mono">· {sessionId.slice(0, 8)}</span>}
        <div className="spacer" />
        <button className="btn" onClick={resetSession}>
          <i className="ti ti-plus" /> Nova sessão
        </button>
      </div>

      <div className="content">
        {messages.length === 0 && (
          <div className="empty">Comece uma conversa com o Bauer. Digite <span className="mono">/</span> para ver os comandos.</div>
        )}
        <div className="msgs">
          {messages.map((m, i) => (
            <div className="msg" key={i}>
              <div className={"avatar " + (m.role === "user" ? "user" : "bot")}>
                {m.role === "user" ? "HF" : <i className={"ti ti-bolt" + (m.streaming ? " spin" : "")} />}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="row" style={{ marginBottom: 4 }}>
                  <span className="who">{m.role === "user" ? "Henrique" : "Bauer"}</span>
                  {m.streaming && <span className="when blink" style={{ color: "var(--accent)" }}>gerando…</span>}
                </div>
                {m.route && (
                  <div className="routecall" title={`Roteado por tarefa: tier ${m.route.tier}`}>
                    <i className="ti ti-arrows-shuffle" style={{ color: "var(--accent)" }} />
                    <span className="rname">
                      <strong>{m.route.tier}</strong> · <span className="mono">{m.route.model}</span>
                    </span>
                  </div>
                )}
                {m.loop && (
                  <div className={"loopcall" + (m.loop.state === "running" ? " running" : "")}>
                    <i className={"ti ti-refresh" + (m.loop.state === "running" || m.loop.state === "stopping" ? " spin" : "")}
                       style={{ color: "var(--accent)" }} />
                    <span className="lname">
                      <strong>
                        {m.loop.state === "running" ? "Modo autônomo"
                          : m.loop.state === "stopping" ? "Parando…"
                          : m.loop.state === "completed" ? "Concluído"
                          : m.loop.state === "stopped" ? "Parado"
                          : "Falhou"}
                      </strong>
                      {" · "}rodada {m.loop.rounds} · {m.loop.toolCalls} tools · ${m.loop.costUsd.toFixed(3)}
                      {m.loop.state === "running" && m.loop.activity && (
                        <span className="lactivity"> · {m.loop.activity}…</span>
                      )}
                      {m.loop.stopReason && m.loop.state !== "completed" && (
                        <span className="mono"> · {m.loop.stopReason}</span>
                      )}
                    </span>
                    {(m.loop.state === "running") && (
                      <button className="loop-stop" onClick={() => stopLoop(m.loop!.runId)}
                              title="Parar o loop (a rodada corrente termina)">
                        <i className="ti ti-player-stop" /> parar
                      </button>
                    )}
                  </div>
                )}
                {m.skill && (
                  <div className="skillcall">
                    <i className="ti ti-sparkles" style={{ color: "var(--accent)" }} />
                    <span className="sname">
                      skill <strong>{m.skill.name}</strong>
                      {m.skill.score != null && ` · ${Math.round(m.skill.score * 100)}%`}
                    </span>
                  </div>
                )}
                {m.tools?.map((t, j) => (
                  <div className="toolcall" key={j}>
                    <i className={`ti ti-${t.icon || "tool"}`} style={{ color: "var(--green)" }} />
                    <span className="tlabel">{t.label || t.name}</span>
                    {t.label && <span className="tname">{t.name}</span>}
                  </div>
                ))}
                {m.role === "user" ? (
                  <div className="text" style={{ whiteSpace: "pre-wrap" }}>{m.text}</div>
                ) : (
                  <div className="text">
                    <Markdown text={m.text} />
                    {m.streaming && <span className="blink">▍</span>}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
        <div ref={endRef} />
      </div>

      <div className="composer" style={{ position: "relative" }}>
        {showPalette && (
          <div className="slash-palette">
            {matches.map((c, i) => (
              <div
                key={c.cmd}
                className={"slash-item" + (i === selIdx ? " active" : "")}
                onMouseEnter={() => setPalIdx(i)}
                onMouseDown={(e) => { e.preventDefault(); execCommand(c); }}
              >
                <span className="slash-cmd mono">{c.cmd}</span>
                <span className="slash-desc">{c.desc}</span>
              </div>
            ))}
          </div>
        )}
        <div className="box">
          <textarea
            placeholder={
              recording
                ? "Gravando… clique no microfone de novo para enviar"
                : "Mensagem para Bauer… (digite / para comandos · Enter envia · Shift+Enter quebra linha)"
            }
            value={input}
            rows={1}
            onChange={(e) => { setInput(e.target.value); setPalIdx(0); }}
            onKeyDown={onKey}
            disabled={busy || recording || transcribing}
          />
          <div
            className={"send-btn" + (recording ? " recording" : "")}
            onClick={toggleRecording}
            title={recording ? "Parar e enviar" : "Gravar áudio"}
          >
            <i className={"ti " + (transcribing ? "ti-loader-2 spin" : recording ? "ti-player-stop" : "ti-microphone")} />
          </div>
          <div className="send-btn" onClick={() => send()}>
            <i className={"ti " + (busy ? "ti-loader-2 spin" : "ti-arrow-up")} />
          </div>
        </div>
      </div>
    </div>
  );
}
