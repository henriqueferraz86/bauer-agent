"""Validate the built chat with controlled local project/session responses.

UV_OFFLINE=1 uv run --no-project --with playwright==1.63.0 python \
    specs/sprint-28-chat-project-context/validate_browser.py
"""

from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).parent / "validation"
PROJECTS = {
    "a": {"id": "a", "name": "Projeto Alpha", "path": "/projetos/alpha"},
    "b": {"id": "b", "name": "Projeto Beta", "path": "/projetos/beta"},
    "broken": {"id": "broken", "name": "Projeto ausente", "path": "/projetos/ausente"},
}
ACTIVE = "a"
SESSIONS: dict[str, str] = {}
CONTEXT_ERROR = False


def project_context(session_id: str | None = None):
    pid = SESSIONS.get(session_id or "", ACTIVE)
    fallback = pid == "broken"
    return {
        "session_id": session_id,
        "project": None if fallback else PROJECTS[pid],
        "workspace": "/workspace/padrao" if fallback else PROJECTS[pid]["path"],
        "source": "session" if session_id in SESSIONS else "active",
        "active_project": PROJECTS[ACTIVE],
        "warning": "Pasta do projeto indisponível. Usando workspace padrão." if fallback else None,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "bauer" / "static"), **kwargs)

    def log_message(self, *_args):
        pass

    def json_response(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/chat/context":
            if CONTEXT_ERROR:
                return self.json_response({"detail": "controlled unavailable"}, 503)
            return self.json_response(project_context(query.get("session_id", [None])[0]))
        if parsed.path == "/stream":
            sid = query.get("session_id", [f"session-{ACTIVE}"])[0]
            SESSIONS.setdefault(sid, ACTIVE)
            ctx = project_context(sid)
            data = (
                "event: context\ndata: " + json.dumps(ctx) + "\n\n"
                "data: Resposta controlada concluída.\n\n"
                "event: done\ndata: " + sid + "\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        responses = {
            "/auth/state": {"enabled": False, "api_key_required": False, "authenticated": False},
            "/status": {"model": "controlled-model", "provider": "ollama", "context_tokens": 4096,
                        "tools": [], "auth_enabled": False},
            "/health": {"status": "ok"},
            "/api/gateway/status": {"running": False},
            "/api/projects": {"active": ACTIVE, "projects": [
                {**project, "active": project["id"] == ACTIVE} for project in PROJECTS.values()
            ]},
        }
        if parsed.path in responses:
            return self.json_response(responses[parsed.path])
        if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/stats"):
            return self.json_response({"sessions": 0, "cost_usd": 0, "total_tokens": 0})
        if parsed.path.startswith("/api/"):
            return self.json_response({})
        return super().do_GET()

    def do_POST(self):
        global ACTIVE
        parts = urlsplit(self.path).path.split("/")
        if len(parts) == 5 and parts[1:3] == ["api", "projects"] and parts[4] == "activate":
            ACTIVE = parts[3]
            return self.json_response({"active": ACTIVE})
        self.send_error(404)


def main():
    global ACTIVE, CONTEXT_ERROR
    OUTPUT.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    errors, external = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path="/home/henrique/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome",
                headless=True,
            )
            context = browser.new_context(viewport={"width": 1440, "height": 900})

            def local_only(route):
                if route.request.url.startswith(origin + "/"):
                    route.continue_()
                else:
                    external.append(route.request.url)
                    route.abort()

            context.route("**/*", local_only)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/#/chat")
            banner = page.get_by_label("Contexto do projeto")
            expect(banner).to_contain_text("Projeto Alpha")
            expect(banner).to_contain_text("/projetos/alpha")
            page.locator("textarea").fill("mensagem controlada")
            page.locator("textarea").press("Enter")
            expect(page.get_by_text("Resposta controlada concluída.", exact=True)).to_be_visible()
            expect(banner).to_contain_text("Projeto Alpha")
            assert '"workspace"' not in page.locator(".msgs").inner_text()

            page.evaluate("location.hash = '#/projects'")
            page.get_by_text("Projeto Beta", exact=True).click()
            page.get_by_role("button", name="Ativar", exact=True).click()
            page.wait_for_function("""async () => (await (await fetch('/api/projects')).json()).active === 'b'""")
            page.evaluate("location.hash = '#/chat'")
            expect(banner).to_contain_text("Projeto Alpha")
            expect(banner).to_contain_text("Projeto Beta")
            expect(banner).to_contain_text("/projetos/alpha")
            page.screenshot(path=str(OUTPUT / "chat-session-project.png"), full_page=True)
            page.reload()
            expect(banner).to_contain_text("Projeto Alpha")
            expect(banner).to_contain_text("Projeto Beta")

            page.get_by_role("button", name="Nova sessão").click()
            expect(banner).to_contain_text("Projeto Beta")
            expect(banner).to_contain_text("/projetos/beta")
            expect(banner).not_to_contain_text("Projeto Alpha")
            page.screenshot(path=str(OUTPUT / "chat-active-project.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            expect(banner).to_contain_text("/projetos/beta")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(OUTPUT / "chat-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})

            ACTIVE = "broken"
            page.get_by_role("button", name="Nova sessão").click()
            page.reload()
            expect(banner).to_contain_text("/workspace/padrao")
            expect(banner).to_contain_text("Pasta do projeto indisponível")
            CONTEXT_ERROR = True
            page.reload()
            expect(banner).to_contain_text("indisponíveis")
            expect(banner).not_to_contain_text("/projetos/beta")
            assert not errors, errors
            assert not external, external
            result = {"passed": True, "browser": browser.version,
                      "checks": ["active project before send", "authoritative SSE context",
                                 "session stays bound after activation", "reload preserves session context",
                                 "new session follows active", "mobile width", "fallback warning",
                                 "unavailable context hides stale workspace"],
                      "page_errors": errors, "external_requests": external}
            (OUTPUT / "browser.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
