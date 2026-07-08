import { NavLink } from "react-router-dom";

const NAV = [
  { to: "/", icon: "ti-home", label: "Bauer OS" },
  { to: "/chat", icon: "ti-message-2", label: "Chat" },
  { to: "/agents", icon: "ti-users", label: "Agents" },
  { to: "/skills", icon: "ti-puzzle", label: "Skills" },
  { to: "/runs", icon: "ti-player-play", label: "Runs" },
  { to: "/approvals", icon: "ti-shield-question", label: "Approvals" },
  { to: "/runtime", icon: "ti-server-2", label: "Runtime" },
  { to: "/observability", icon: "ti-chart-bar", label: "Observabilidade" },
  { to: "/projects", icon: "ti-folders", label: "Projetos" },
  { to: "/kanban", icon: "ti-layout-kanban", label: "Kanban" },
  { to: "/models", icon: "ti-cpu", label: "Modelos" },
  { to: "/gateway", icon: "ti-router", label: "Gateway" },
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
          end={n.to === "/"}
          title={n.label}
          className={({ isActive }) => "sb-item" + (isActive ? " active" : "")}
        >
          <i className={"ti " + n.icon} />
        </NavLink>
      ))}
      <div className="sb-spacer" />
      <NavLink to="/settings" title="Settings" className={({ isActive }) => "sb-item" + (isActive ? " active" : "")}>
        <i className="ti ti-settings" />
      </NavLink>
      <div className="sb-avatar">HF</div>
    </div>
  );
}
