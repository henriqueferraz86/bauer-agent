export interface AuthUser {
  email: string;
  display_name: string;
  has_password: boolean;
  google_linked: boolean;
}

export interface AuthState {
  enabled: boolean;
  api_key_required: boolean;
  setup_required: boolean;
  authenticated: boolean;
  google_enabled: boolean;
  google_client_id: string;
  user: AuthUser | null;
}

export const EMPTY_AUTH_STATE: AuthState = {
  enabled: false,
  api_key_required: false,
  setup_required: false,
  authenticated: false,
  google_enabled: false,
  google_client_id: "",
  user: null,
};

export function needsAuthScreen(state: AuthState): boolean {
  return state.enabled && !state.authenticated;
}

export function authMode(state: AuthState): "disabled" | "setup" | "login" | "authenticated" {
  if (!state.enabled) return "disabled";
  if (state.authenticated) return "authenticated";
  return state.setup_required ? "setup" : "login";
}
