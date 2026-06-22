import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

const LOGS = ["gateway", "dispatcher", "cron", "outbox"];

function lineClass(line: string): string {
  if (/\bERROR\b|\[Erro/i.test(line)) return "log-err";
  if (/\bWARN/i.test(line)) return "log-warn";
  if (/\bno ar\b|200 OK|iniciad/i.test(line)) return "log-ok";
  return "log-info";
}

export default function Logs() {
  const [name, setName] = useState("gateway");
  const [lines, setLines] = useState<string[]>([]);
  const [live, setLive] = useState(true);
  const boxRef = useRef<HTMLDivElement>(null);

  async function load() {
    try {
      const r = await api.get<{ lines: string[] }>(`/api/logs/${name}/tail?lines=300`);
      setLines(r.lines);
      requestAnimationFrame(() => { if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight; });
    } catch { setLines([]); }
  }
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [name]);
  useEffect(() => {
    if (!live) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
    /* eslint-disable-next-line */
  }, [live, name]);

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-terminal-2 head-icon" />
        <span className="title">Logs</span>
        <div className="spacer" />
        <select className="in" style={{ width: 140 }} value={name} onChange={(e) => setName(e.target.value)}>
          {LOGS.map((l) => <option key={l} value={l}>{l}.log</option>)}
        </select>
        <button className={"btn" + (live ? " primary" : "")} onClick={() => setLive((v) => !v)}>
          <span className={"dot " + (live ? "on" : "off")} /> ao vivo
        </button>
      </div>
      <div className="content" ref={boxRef}>
        {lines.length === 0 ? (
          <div className="empty">Sem conteúdo em {name}.log</div>
        ) : (
          <div className="logbox">
            {lines.map((l, i) => <div key={i} className={lineClass(l)}>{l}</div>)}
          </div>
        )}
      </div>
    </div>
  );
}
