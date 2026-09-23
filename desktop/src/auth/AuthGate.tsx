import { ReactNode } from "react";
import { needsAuthScreen } from "../auth";
import { useAuth } from "./AuthContext";
import AuthScreen from "./AuthScreen";

export default function AuthGate({ children }: { children: ReactNode }) {
  const { state, loading, error } = useAuth();
  if (loading) return <div className="auth-page"><div className="auth-loading"><i className="ti ti-loader-2 spin" /> Verificando sessão…</div></div>;
  if (error && !state.enabled) return <div className="auth-page"><div className="auth-error">{error}</div></div>;
  if (!state.enabled && state.api_key_required) return <div className="auth-page"><div className="auth-card"><h1>Login web desabilitado</h1><p>Ative <code>serve.web_auth_enabled</code> ou use a API com o header <code>X-API-Key</code>.</p></div></div>;
  if (needsAuthScreen(state)) return <AuthScreen />;
  return <>{children}</>;
}
