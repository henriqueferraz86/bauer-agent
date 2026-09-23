import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import "@tabler/icons-webfont/dist/tabler-icons.min.css";
import "./styles.css";
import App from "./App";
import AuthGate from "./auth/AuthGate";
import { AuthProvider } from "./auth/AuthContext";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HashRouter>
      <AuthProvider>
        <AuthGate><App /></AuthGate>
      </AuthProvider>
    </HashRouter>
  </React.StrictMode>
);
