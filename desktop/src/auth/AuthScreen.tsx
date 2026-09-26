import { FormEvent, useState } from "react";
import { authMode } from "../auth";
import { useAuth } from "./AuthContext";
import GoogleButton from "./GoogleButton";

export default function AuthScreen() {
  const auth = useAuth();
  const mode = authMode(auth.state);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [bootstrapKey, setBootstrapKey] = useState("");
  const [recovering, setRecovering] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const isSetup = mode === "setup";

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if ((isSetup || recovering) && password !== confirmPassword) {
      setMessage("As senhas não coincidem.");
      return;
    }
    if (isSetup) await run(() => auth.setup(email, password, bootstrapKey));
    else if (recovering) await run(() => auth.recover(email, password, bootstrapKey));
    else await run(() => auth.login(email, password));
  }

  async function google(credential: string) {
    if (isSetup && !bootstrapKey.trim()) {
      setMessage("Informe a chave de bootstrap antes de continuar com Google.");
      return;
    }
    await run(() => auth.loginGoogle(credential, isSetup ? bootstrapKey : ""));
  }

  const title = isSetup ? "Criar administrador" : recovering ? "Recuperar acesso" : "Entrar no Bauer";
  const description = isSetup
    ? "Este cadastro acontece uma única vez. A chave atual confirma que você controla esta instância."
    : recovering
      ? "Use a chave da instância para definir uma nova senha."
      : "Use seu e-mail e senha ou a conta Google vinculada.";

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="auth-brand"><i className="ti ti-bolt" /><span>Bauer Agent</span></div>
        <h1>{title}</h1>
        <p>{description}</p>

        <form onSubmit={submit} className="auth-form">
          <label>E-mail<input className="in" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
          <label>{recovering ? "Nova senha" : "Senha"}<input className="in" type="password" minLength={isSetup || recovering ? 12 : 1} autoComplete={isSetup ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
          {(isSetup || recovering) && <label>Confirmar senha<input className="in" type="password" minLength={12} autoComplete="new-password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required /></label>}
          {(isSetup || recovering) && <label>Chave de bootstrap<input className="in mono" type="password" autoComplete="off" value={bootstrapKey} onChange={(e) => setBootstrapKey(e.target.value)} required /></label>}
          <button className="btn primary auth-submit" disabled={busy}>{busy ? "Aguarde…" : isSetup ? "Criar conta" : recovering ? "Trocar senha" : "Entrar"}</button>
        </form>

        {auth.state.google_enabled && !recovering && (
          <>
            <div className="auth-divider"><span>ou</span></div>
            <GoogleButton clientId={auth.state.google_client_id} onCredential={google} onError={setMessage} />
          </>
        )}

        {!isSetup && (
          <button className="auth-link" onClick={() => { setRecovering(!recovering); setMessage(""); }}>
            {recovering ? "Voltar ao login" : "Esqueci minha senha"}
          </button>
        )}
        {message && <div className="auth-error">{message}</div>}
      </section>
    </main>
  );
}
