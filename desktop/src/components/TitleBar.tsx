import { useEffect, useState } from "react";
import { api } from "../api/client";
import { MODEL_SELECTION_EVENT, ModelSelection, refreshModelSelection } from "../modelSelection";

interface Status {
  model: string;
  provider: string;
  gatewayOn: boolean;
}

export default function TitleBar() {
  const [st, setSt] = useState<Status>({ model: "—", provider: "", gatewayOn: false });

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        await refreshModelSelection();
        const g = await api.get<{ running: boolean }>("/api/gateway/status").catch(() => ({ running: false }));
        if (alive) setSt((status) => ({ ...status, gatewayOn: g.running }));
      } catch {
        /* serve offline */
      }
    };
    const selected = (event: Event) => {
      const selection = (event as CustomEvent<ModelSelection>).detail;
      if (alive) setSt((status) => ({ ...status, ...selection }));
    };
    window.addEventListener(MODEL_SELECTION_EVENT, selected);
    load();
    const t = setInterval(load, 10000);
    return () => {
      alive = false;
      clearInterval(t);
      window.removeEventListener(MODEL_SELECTION_EVENT, selected);
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
        <span className="pill">{st.provider ? `${st.provider} · ` : ""}{st.model}</span>
      </div>
    </div>
  );
}
