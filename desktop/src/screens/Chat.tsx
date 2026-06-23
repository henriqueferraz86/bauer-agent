import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, streamSSE } from "../api/client";

interface ToolCall { name: string; }
interface Message {
  role: "user" | "assistant";
  text: string;
  tools?: ToolCall[];
  streaming?: boolean;
}

interface SlashCommand {
  cmd: string;
  desc: string;
  run: (arg: string) => void | Promise<void>;
}

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string>("");
  const [palIdx, setPalIdx] = useState(0);
  const endRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const scroll = () => requestAnimationFrame(() => endRef.current?.scrollIntoView({ behavior: "smooth" }));

  function appendInfo(text: string) {
    setMessages((m) => [...m, { role: "assistant", text }]);
    scroll();
  }
  function resetSession() {
    setMessages([]);
    setSessionId("");
  }

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

  async function send() {
    const text = input.trim();
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
          if (e.event === "tool") {
            last.tools = [...(last.tools || []), { name: e.data }];
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
                {m.tools?.map((t, j) => (
                  <div className="toolcall" key={j}>
                    <i className="ti ti-tool" style={{ color: "var(--green)" }} />
                    <span className="tname">{t.name}</span>
                  </div>
                ))}
                <div className="text" style={{ whiteSpace: "pre-wrap" }}>{m.text}{m.streaming && <span className="blink">▍</span>}</div>
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
            placeholder="Mensagem para Bauer… (digite / para comandos · Enter envia · Shift+Enter quebra linha)"
            value={input}
            rows={1}
            onChange={(e) => { setInput(e.target.value); setPalIdx(0); }}
            onKeyDown={onKey}
            disabled={busy}
          />
          <div className="send-btn" onClick={send}>
            <i className={"ti " + (busy ? "ti-loader-2 spin" : "ti-arrow-up")} />
          </div>
        </div>
      </div>
    </div>
  );
}
