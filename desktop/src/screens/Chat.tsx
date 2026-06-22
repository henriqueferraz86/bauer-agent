import { useRef, useState } from "react";
import { streamSSE } from "../api/client";

interface ToolCall { name: string; }
interface Message {
  role: "user" | "assistant";
  text: string;
  tools?: ToolCall[];
  streaming?: boolean;
}

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string>("");
  const endRef = useRef<HTMLDivElement>(null);

  const scroll = () => requestAnimationFrame(() => endRef.current?.scrollIntoView({ behavior: "smooth" }));

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
        <button className="btn" onClick={() => { setMessages([]); setSessionId(""); }}>
          <i className="ti ti-plus" /> Nova sessão
        </button>
      </div>

      <div className="content">
        {messages.length === 0 && <div className="empty">Comece uma conversa com o Bauer.</div>}
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
                <div className="text">{m.text}{m.streaming && <span className="blink">▍</span>}</div>
              </div>
            </div>
          ))}
        </div>
        <div ref={endRef} />
      </div>

      <div className="composer">
        <div className="box">
          <textarea
            placeholder="Mensagem para Bauer… (Enter envia, Shift+Enter quebra linha)"
            value={input}
            rows={1}
            onChange={(e) => setInput(e.target.value)}
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
