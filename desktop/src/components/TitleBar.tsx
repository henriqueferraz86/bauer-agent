import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Status {
  model: string;
  gatewayOn: boolean;
}

export default function TitleBar() {
  const [st, setSt] = useState<Status>({ model: "—", gatewayOn: false });

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const s = await api.get<{ model: string }>("/status");
        const g = await api.get<{ running: boolean }>("/api/gateway/status").catch(() => ({ running: false }));
        if (alive) setSt({ model: s.model, gatewayOn: g.running });
      } catch {
        /* serve offline */
      }
    };
    load();
    const t = setInterval(load, 10000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return (
    <div className="titlebar">
      <div className="tdots">
        <div className="tdot r" /><div className="tdot y" /><div className="tdot g" />
      </div>
      <div className="tbrand">⚡ Bauer Agent</div>
      <div className="tstatus">
        <span className="row" style={{ fontSize: 11, color: st.gatewayOn ? "var(--green)" : "var(--text-4)" }}>
          <span className={"dot " + (st.gatewayOn ? "on" : "off")} /> Gateway
        </span>
        <span className="pill">{st.model}</span>
      </div>
    </div>
  );
}
