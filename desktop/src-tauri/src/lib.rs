//! Bauer Agent Desktop — shell nativo (Tauri v2).
//!
//! O app não embute o backend: ele spawna o `bauer serve` do Python do sistema
//! (mesmo modelo do comando `bauer desktop`), aguarda `/health` e então navega a
//! janela para `http://127.0.0.1:<porta>/`, onde o próprio serve já serve a SPA.
//! Ao sair, mata o processo do serve para não deixar órfão.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Emitter, Manager};

/// Guarda o processo do serve para encerrá-lo no exit.
struct ServeProcess(Mutex<Option<Child>>);

/// Diretório home do usuário, sem depender do crate `dirs`.
fn home_dir() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
}

/// Extrai o caminho do projeto ativo do conteúdo de `~/.bauer/projects.json`.
/// Prefere o projeto cujo `id == active`; senão o primeiro da lista.
fn parse_active_path(json: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(json).ok()?;
    let active = v.get("active").and_then(|x| x.as_str());
    let arr = v.get("projects")?.as_array()?;
    let entry = arr
        .iter()
        .find(|p| p.get("id").and_then(|i| i.as_str()) == active)
        .or_else(|| arr.first())?;
    entry
        .get("path")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string())
}

/// Diretório onde rodar o serve: projeto ativo do registro, senão o home.
fn active_project_dir() -> PathBuf {
    if let Some(home) = home_dir() {
        let reg = home.join(".bauer").join("projects.json");
        if let Ok(txt) = std::fs::read_to_string(&reg) {
            if let Some(path) = parse_active_path(&txt) {
                let pb = PathBuf::from(path);
                if pb.is_dir() {
                    return pb;
                }
            }
        }
        return home;
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// Candidatos de executável Python por plataforma.
fn python_candidates() -> Vec<String> {
    if let Ok(p) = std::env::var("BAUER_PYTHON") {
        if !p.trim().is_empty() {
            return vec![p];
        }
    }
    if cfg!(windows) {
        vec!["py".into(), "python".into(), "python3".into()]
    } else {
        vec!["python3".into(), "python".into()]
    }
}

/// Escolhe o Python que tem o pacote `bauer` instalado; senão o primeiro que roda.
fn find_python() -> String {
    let candidates = python_candidates();
    for c in &candidates {
        let has_bauer = Command::new(c)
            .args(["-c", "import bauer"])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false);
        if has_bauer {
            return c.clone();
        }
    }
    candidates.into_iter().next().unwrap_or_else(|| "python".into())
}

/// Porta TCP livre em 127.0.0.1 (fallback 8799 se o bind falhar).
fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8799)
}

/// Spawna `python -m bauer.cli serve` com cwd no diretório do projeto.
fn spawn_serve(python: &str, cwd: &Path, port: u16) -> std::io::Result<Child> {
    let mut cmd = Command::new(python);
    cmd.args([
        "-m",
        "bauer.cli",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        &port.to_string(),
    ])
    .current_dir(cwd);

    // Evita um console flash no Windows (CREATE_NO_WINDOW).
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000);
    }

    cmd.spawn()
}

/// Um GET HTTP/1.0 mínimo em `127.0.0.1:<port>/health` via TcpStream puro
/// (sem TLS/deps nativas — só falamos com o loopback). True se a resposta for 200.
fn health_ok(port: u16) -> bool {
    let addr: SocketAddr = match format!("127.0.0.1:{port}").parse() {
        Ok(a) => a,
        Err(_) => return false,
    };
    let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_secs(2)) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let req = format!(
        "GET /health HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = Vec::with_capacity(256);
    let _ = stream.take(512).read_to_end(&mut buf);
    let head = String::from_utf8_lossy(&buf);
    let first = head.lines().next().unwrap_or("");
    first.contains(" 200")
}

/// Faz poll de `/health` até 200 OK ou estourar o timeout.
fn wait_health(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if health_ok(port) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(400));
    }
    false
}

/// Inicia o app Tauri.
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            app.manage(ServeProcess(Mutex::new(None)));
            let handle = app.handle().clone();

            // Boot do serve numa thread para não bloquear a janela (mostra o splash).
            std::thread::spawn(move || {
                let port = free_port();
                let proj = active_project_dir();
                let python = find_python();

                let _ = handle.emit(
                    "bauer://status",
                    format!("Iniciando serve em 127.0.0.1:{port}…"),
                );

                match spawn_serve(&python, &proj, port) {
                    Ok(child) => {
                        if let Some(state) = handle.try_state::<ServeProcess>() {
                            *state.0.lock().unwrap() = Some(child);
                        }
                        if wait_health(port, Duration::from_secs(30)) {
                            if let Some(win) = handle.get_webview_window("main") {
                                let url = format!("http://127.0.0.1:{port}/");
                                if let Ok(parsed) = url.parse() {
                                    let _ = win.navigate(parsed);
                                }
                            }
                        } else {
                            let _ = handle.emit(
                                "bauer://error",
                                "O servidor não respondeu a tempo (/health).".to_string(),
                            );
                        }
                    }
                    Err(e) => {
                        let _ = handle.emit(
                            "bauer://error",
                            format!("Falha ao iniciar o Python ('{python}'): {e}"),
                        );
                    }
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("erro ao iniciar o app Tauri")
        .run(|app_handle, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(state) = app_handle.try_state::<ServeProcess>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_active_prefers_active_id() {
        let json = r#"{"active":"b","projects":[
            {"id":"a","path":"C:/a"},{"id":"b","path":"C:/b"}]}"#;
        assert_eq!(parse_active_path(json).as_deref(), Some("C:/b"));
    }

    #[test]
    fn parse_active_falls_back_to_first() {
        let json = r#"{"active":null,"projects":[{"id":"a","path":"/x"}]}"#;
        assert_eq!(parse_active_path(json).as_deref(), Some("/x"));
    }

    #[test]
    fn parse_active_empty_or_bad() {
        assert_eq!(parse_active_path("{}"), None);
        assert_eq!(parse_active_path("not json"), None);
        assert_eq!(parse_active_path(r#"{"projects":[]}"#), None);
    }

    #[test]
    fn free_port_nonzero() {
        assert!(free_port() > 0);
    }

    #[test]
    fn python_candidates_nonempty() {
        assert!(!python_candidates().is_empty());
    }

    #[test]
    fn bauer_python_env_overrides() {
        std::env::set_var("BAUER_PYTHON", "/custom/python");
        assert_eq!(python_candidates(), vec!["/custom/python".to_string()]);
        std::env::remove_var("BAUER_PYTHON");
    }
}
