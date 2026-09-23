import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, AUTH_REQUIRED_EVENT, clearLegacyApiKey } from "../api/client";
import { AuthState, EMPTY_AUTH_STATE } from "../auth";

interface AuthContextValue {
  state: AuthState;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  setup: (email: string, password: string, bootstrapKey: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  loginGoogle: (credential: string, bootstrapKey?: string) => Promise<void>;
  linkGoogle: (credential: string) => Promise<void>;
  recover: (email: string, password: string, bootstrapKey: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(EMPTY_AUTH_STATE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const next = await api.get<AuthState>("/auth/state");
      setState(next);
      if (next.authenticated) clearLegacyApiKey();
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    const onRequired = () => setState((current) => ({ ...current, authenticated: false, user: null }));
    window.addEventListener(AUTH_REQUIRED_EVENT, onRequired);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onRequired);
  }, []);

  const afterAuth = useCallback(async () => {
    clearLegacyApiKey();
    await refresh();
  }, [refresh]);

  const value = useMemo<AuthContextValue>(() => ({
    state,
    loading,
    error,
    refresh,
    setup: async (email, password, bootstrapKey) => {
      await api.post("/auth/setup", { email, password, bootstrap_key: bootstrapKey });
      await afterAuth();
    },
    login: async (email, password) => {
      await api.post("/auth/login", { email, password });
      await afterAuth();
    },
    loginGoogle: async (credential, bootstrapKey = "") => {
      await api.post("/auth/google", { credential, bootstrap_key: bootstrapKey, link: false });
      await afterAuth();
    },
    linkGoogle: async (credential) => {
      await api.post("/auth/google", { credential, link: true });
      await afterAuth();
    },
    recover: async (email, password, bootstrapKey) => {
      await api.post("/auth/recover", { email, new_password: password, bootstrap_key: bootstrapKey });
      await afterAuth();
    },
    logout: async () => {
      await api.post("/auth/logout");
      await refresh();
    },
  }), [afterAuth, error, loading, refresh, state]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth precisa estar dentro de AuthProvider");
  return value;
}
