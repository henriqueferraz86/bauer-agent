import { Navigate, Route, Routes } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import TitleBar from "./components/TitleBar";
import CommandPalette from "./components/CommandPalette";
import Chat from "./screens/Chat";
import Projects from "./screens/Projects";
import Kanban from "./screens/Kanban";
import Models from "./screens/Models";
import Gateway from "./screens/Gateway";
import Observability from "./screens/Observability";
import Logs from "./screens/Logs";
import Config from "./screens/Config";
import Runs from "./screens/Runs";
import Approvals from "./screens/Approvals";
import Agents from "./screens/Agents";
import Skills from "./screens/Skills";
import Runtime from "./screens/Runtime";
import Settings from "./screens/Settings";

export default function App() {
  return (
    <div className="app">
      <TitleBar />
      <CommandPalette />
      <div className="body">
        <Sidebar />
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/kanban" element={<Kanban />} />
          <Route path="/models" element={<Models />} />
          <Route path="/gateway" element={<Gateway />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/runtime" element={<Runtime />} />
          <Route path="/observability" element={<Observability />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/config" element={<Config />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </div>
    </div>
  );
}
