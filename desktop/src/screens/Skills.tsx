import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Skill {
  id: string;
  name: string;
  description: string;
  capabilities: string[];
  permissions: string[];
  risk: string;
  legacy: boolean;
}

export default function Skills() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [query, setQuery] = useState("");

  useEffect(() => {
    api.get<{ skills: Skill[] }>("/api/skills").then((r) => setSkills(r.skills)).catch(() => {});
  }, []);

  const filtered = skills.filter((skill) => {
    const hay = `${skill.id} ${skill.name} ${skill.description} ${skill.capabilities.join(" ")}`.toLowerCase();
    return hay.includes(query.toLowerCase());
  });

  return (
    <div className="main">
      <div className="page-head">
        <i className="ti ti-puzzle head-icon" />
        <span className="title">Skills</span>
        <span className="sub">{filtered.length}/{skills.length}</span>
        <div className="spacer" />
        <input className="in" style={{ width: 260 }} placeholder="Buscar capability, permissão, nome" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      <div className="content">
        {filtered.length === 0 ? <div className="empty">Nenhuma skill encontrada.</div> : filtered.map((skill) => (
          <div className="list-item" key={skill.id} style={{ cursor: "default", alignItems: "flex-start" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="row" style={{ marginBottom: 4 }}>
                <span className="mono" style={{ color: "#e6edf3" }}>{skill.id}</span>
                <span className="tag">{skill.risk}</span>
                {skill.legacy && <span className="tag">legacy</span>}
              </div>
              <div className="muted" style={{ marginBottom: 8 }}>{skill.description}</div>
              <div className="row" style={{ flexWrap: "wrap", marginBottom: 5 }}>{skill.capabilities.slice(0, 8).map((cap) => <span className="tag accent" key={cap}>{cap}</span>)}</div>
              <div className="row" style={{ flexWrap: "wrap" }}>{skill.permissions.map((perm) => <span className="tag" key={perm}>{perm}</span>)}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
