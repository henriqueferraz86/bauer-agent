import { NavLink } from "react-router-dom";

const NAV = [
  { to: "/projects", icon: "ti-folders", label: "Projetos" },
  { to: "/chat", icon: "ti-message-2", label: "Chat" },
  { to: "/kanban", icon: "ti-layout-kanban", label: "Kanban" },
  { to: "/models", icon: "ti-cpu", label: "Modelos" },
  { to: "/gateway", icon: "ti-router", label: "Gateway" },
  { to: "/observability", icon: "ti-chart-bar", label: "Observabilidade" },
  { to: "/logs", icon: "ti-terminal-2", label: "Logs" },
];

export default function Sidebar() {
  return (
    <div className="sidebar">
      <div className="sb-logo"><i className="ti ti-bolt" /></div>
      {NAV.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          title={n.label}
          className={({ isActive }) => "sb-item" + (isActive ? " active" : "")}
        >
          <i className={"ti " + n.icon} />
        </NavLink>
      ))}
      <div className="sb-spacer" />
      <NavLink to="/config" title="Config" className={({ isActive }) => "sb-item" + (isActive ? " active" : "")}>
        <i className="ti ti-settings" />
      </NavLink>
      <div className="sb-avatar">HF</div>
    </div>
  );
}
