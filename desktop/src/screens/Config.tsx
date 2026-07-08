import { useEffect, useState } from "react";
import { api, getApiKey, setApiKey } from "../api/client";

type Json = Record<string, unknown>;

function flatten(obj: Json, prefix = ""): [string, string][] {
  const rows: [string, string][] = [];
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      rows.push(...flatten(v as Json, key));
    } else {
      rows.push([key, Array.isArray(v) ? JSON.stringify(v) : String(v)]);
    }
  }
  return rows;
}

// Chaves de provider mais comuns — o backend roteia qualquer *_API_KEY /
// *_TOKEN para o .env (nunca pro config.yaml, que é versionado).
const ENV_KEY_SUGGESTIONS = [
  "OPENROUTER_API_KEY",
  "OPENAI_API_KEY",
  "ANTHROPIC_API_KEY",
  "GROQ_API_KEY",
  "MISTRAL_API_KEY",
  "DEEPSEEK_API_KEY",
  "XAI_API_KEY",
  "TOGETHER_API_KEY",
  "GOOGLE_API_KEY",
  "TELEGRAM_BOT_TOKEN",
  "DISCORD_BOT_TOKEN",
];

export default function Config() {
  const [config, setConfig] = useState<Json>({});
  const [profiles, setProfiles] = useState<string[]>([]);
  const [activeProfile, setActiveProfile] = useState<string | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState(getApiKey());
  const [editing, setEditing] = useState<{ key: string; value: string } | null>(null);
  const [msg, setMsg] = useState("");
  const [secretKey, setSecretKey] = useState("OPENROUTER_API_KEY");
  const [secretValue, setSecretValue] = useState("");
  const [secretMsg, setSecretMsg] = useState("");

  async function saveSecret() {
    const key = secretKey.trim().toUpperCase();
    const value = secretValue.trim();
    if (!key || !value) return;
    setSecretMsg("");
    try {
      const r = await api.put<{ saved: string; dest: string }>("/api/config", { key, value });
      setSecretValue("");
      setSecretMsg(
        r.dest === "env"
          ? `${r.saved} salva no .env — reinicie o serve para aplicar.`
          : `${r.saved} salva (${r.dest}).`
      );
    } catch (e) {
      setSecretMsg(String(e));
    }
  }

  async function load() {
    api.get<{ config: Json }>("/api/config").then((r) => setConfig(r.config)).catch(() => {});
    api.get<{ profiles: string[]; active: string | null }>("/api/config/profiles")
      .then((r) => { setProfiles(r.profiles); setActiveProfile(r.active); }).catch(() => {});
  }
  useEffect(() => { load(); }, []);

  async function save() {
    if (!editing) return;
    setMsg("");
    try {
      await api.put("/api/config", { key: editing.key, value: editing.value });
      setEditing(null);
      await load();
      setMsg("Salvo.");
    } catch (e) { setMsg(String(e)); }
  }
  async function useProfile(p: string) {
    await api.post(`/api/config/profiles/${p}/use`);
    setActiveProfile(p);
  }

  const rows = flatten(config);

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-settings head-icon" />
        <span className="title">Config</span>
        <div className="spacer" />
        {msg && <span className="sub">{msg}</span>}
      </div>
      <div className="content">
        {/* API key local */}
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>API KEY (deste cliente — guardada no navegador)</div>
          <div className="row">
            <input className="in" type="password" placeholder="X-API-Key do serve (se houver auth)"
              value={apiKeyInput} onChange={(e) => setApiKeyInput(e.target.value)} />
            <button className="btn primary" onClick={() => { setApiKey(apiKeyInput); setMsg("API key salva."); }}>Salvar</button>
          </div>
        </div>

        {/* Segredos de provider (.env) */}
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>
            SEGREDOS DE PROVIDER (gravados no .env do projeto, nunca no config.yaml)
          </div>
          <div className="row" style={{ gap: 8 }}>
            <input
              className="in mono"
              style={{ width: 220 }}
              list="env-key-suggestions"
              value={secretKey}
              onChange={(e) => setSecretKey(e.target.value)}
              placeholder="OPENROUTER_API_KEY"
            />
            <datalist id="env-key-suggestions">
              {ENV_KEY_SUGGESTIONS.map((k) => <option key={k} value={k} />)}
            </datalist>
            <input
              className="in"
              type="password"
              style={{ flex: 1 }}
              value={secretValue}
              onChange={(e) => setSecretValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && saveSecret()}
              placeholder="cole a chave aqui"
            />
            <button className="btn primary" onClick={saveSecret} disabled={!secretValue.trim()}>
              Salvar
            </button>
          </div>
          {secretMsg && (
            <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>{secretMsg}</div>
          )}
        </div>

        {/* Profiles */}
        <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}><i className="ti ti-layers-subtract" /> PROFILES</div>
        <div className="row" style={{ gap: 6, marginBottom: 16, flexWrap: "wrap" }}>
          {profiles.length === 0 && <span className="muted">Nenhum profile (usando config.yaml padrão).</span>}
          {profiles.map((p) => (
            <button key={p} className={"btn" + (p === activeProfile ? " primary" : "")} onClick={() => useProfile(p)}>
              {p === activeProfile && <span className="dot on" />} {p}
            </button>
          ))}
        </div>

        {/* Config key/value */}
        <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}><i className="ti ti-file-settings" /> CONFIG.YAML</div>
        {rows.length === 0 ? <div className="empty">config.yaml vazio ou ausente.</div> : (
          <div className="card" style={{ padding: 0 }}>
            {rows.map(([k, v]) => (
              <div className="row" key={k} style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)", gap: 10 }}>
                <span className="mono" style={{ color: "var(--text-2)", flex: 1, fontSize: 12 }}>{k}</span>
                {editing?.key === k ? (
                  <>
                    <input className="in" style={{ width: 220 }} value={editing.value} autoFocus
                      onChange={(e) => setEditing({ key: k, value: e.target.value })}
                      onKeyDown={(e) => e.key === "Enter" && save()} />
                    <button className="btn primary" onClick={save}>OK</button>
                    <button className="btn" onClick={() => setEditing(null)}>×</button>
                  </>
                ) : (
                  <>
                    <span className="mono" style={{ fontSize: 12 }}>{v}</span>
                    <button className="btn" onClick={() => setEditing({ key: k, value: v })}><i className="ti ti-edit" /></button>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
