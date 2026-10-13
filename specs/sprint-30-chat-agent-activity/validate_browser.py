"""Hermetic built-chat activity validation against a controlled local server.

uv run --no-project --with playwright==1.63.0 python \
    specs/sprint-30-chat-agent-activity/validate_browser.py
"""

from __future__ import annotations

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).parent / "validation"
SID = "controlled-session"
STAMP = "2026-10-13T12:00:00+00:00"
UNAVAILABLE = False
STREAMS = 0
REQUESTS = []


def agent(agent_id, name, task, status="running", tool=None):
    return {"agent_id": agent_id, "name": name, "task": task, "status": status,
            "activity": "Concluído" if status == "completed" else "Estado não informado" if status == "unknown" else "Executando ferramenta" if tool else "Executando tarefa",
            "tool_name": tool, "updated_at": STAMP}


def snapshot(status="running", member_status="running"):
    return {"session_id": SID, "runs": [{
        "run_id": "controlled-run", "task": "Entregar a melhoria com revisão e testes",
        "status": status, "agent_id": "serve.chat", "agent_name": "Bauer",
        "runtime": "agno", "started_at": STAMP, "updated_at": STAMP,
        "agents": [agent("serve.chat", "Bauer", "Coordenar a entrega", status),
                   agent("delegation-1", "Revisor", "Revisar a segurança da mudança", member_status, "read_file"),
                   agent("delegation-2", "Testador", "Validar a suíte e o comportamento no navegador", "completed")],
    }]}


ACTIVITY = snapshot()


def project_context(sid=None):
    return {"session_id": sid, "project": {"id": "project-a", "name": "Projeto Alpha", "path": "/projetos/alpha"},
            "workspace": "/projetos/alpha", "source": "session" if sid else "active",
            "active_project": {"id": "project-a", "name": "Projeto Alpha", "path": "/projetos/alpha"}, "warning": None}


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
        global STREAMS
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/chat/context":
            return self.json_response(project_context(query.get("session_id", [None])[0]))
        if parsed.path == "/chat/activity":
            REQUESTS.append(query.get("session_id", [None])[0])
            if UNAVAILABLE:
                return self.json_response({"detail": "controlled unavailable"}, 503)
            return self.json_response(ACTIVITY)
        if parsed.path == "/stream":
            STREAMS += 1
            body = ("event: context\ndata: " + json.dumps(project_context(SID)) + "\n\n"
                    "event: internal\ndata: " + json.dumps({"kind": "chat.activity", "payload": ACTIVITY}) + "\n\n"
                    "data: Resposta controlada concluída.\n\n"
                    "event: done\ndata: " + SID + "\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        responses = {
            "/auth/state": {"enabled": False, "api_key_required": False, "authenticated": False},
            "/status": {"model": "controlled-model", "provider": "ollama", "context_tokens": 4096,
                        "tools": [], "auth_enabled": False},
            "/health": {"status": "ok"}, "/api/gateway/status": {"running": False},
            "/api/projects": {"active": "project-a", "projects": [{"id": "project-a", "name": "Projeto Alpha", "path": "/projetos/alpha", "active": True}]},
        }
        if parsed.path in responses:
            return self.json_response(responses[parsed.path])
        if parsed.path.startswith("/api/"):
            return self.json_response({})
        return super().do_GET()


def main():
    global ACTIVITY, UNAVAILABLE
    OUTPUT.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    errors, stderr, external = [], [], []
    try:
        with sync_playwright() as playwright:
            executable = os.environ.get("BAUER_BROWSER_EXECUTABLE")
            if not executable:
                candidates = sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
                executable = str(candidates[-1]) if candidates else None
            browser = playwright.chromium.launch(executable_path=executable, headless=True)
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
            page.on("console", lambda msg: stderr.append(msg.text) if msg.type == "error" else None)
            page.goto(origin + "/#/chat")
            panel = page.get_by_label("Atividade dos agentes", exact=True)
            expect(panel).to_contain_text("Aguardando uma tarefa")
            page.locator("textarea").fill("Executar a tarefa controlada")
            page.locator("textarea").press("Enter")
            expect(page.get_by_text("Resposta controlada concluída.", exact=True)).to_be_visible()
            reviewer = panel.locator('[data-agent-id="delegation-1"]')
            tester = panel.locator('[data-agent-id="delegation-2"]')
            expect(reviewer).to_contain_text("Revisar a segurança")
            expect(reviewer).to_contain_text("Trabalhando")
            expect(reviewer).to_contain_text("read_file")
            expect(tester).to_contain_text("Validar a suíte")
            expect(tester).to_contain_text("Concluída")
            assert '"runs"' not in page.locator(".msgs").inner_text()
            assert panel.locator(".spin").count() >= 2
            page.screenshot(path=str(OUTPUT / "chat-agents-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            expect(page.locator("textarea")).to_be_visible()
            page.screenshot(path=str(OUTPUT / "chat-agents-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.reload()
            expect(reviewer).to_contain_text("Trabalhando")
            page.evaluate("location.hash = '#/projects'")
            expect(page.get_by_text("Projetos", exact=True)).to_be_visible()
            page.evaluate("location.hash = '#/chat'")
            expect(reviewer).to_contain_text("Revisar a segurança")
            for status, label in (("waiting_approval", "Aguardando aprovação"), ("paused", "Pausada"), ("retrying", "Tentando novamente")):
                ACTIVITY = snapshot(status, status)
                page.evaluate("dispatchEvent(new Event('focus'))")
                expect(reviewer).to_contain_text(label)
            ACTIVITY = snapshot("failed", "unknown")
            page.evaluate("dispatchEvent(new Event('focus'))")
            expect(panel.locator('[data-agent-id="serve.chat"]')).to_contain_text("Falhou")
            expect(reviewer).to_contain_text("Desconhecido")
            expect(tester).to_contain_text("Concluída")
            assert panel.locator(".spin").count() == 0
            UNAVAILABLE = True
            page.evaluate("dispatchEvent(new Event('focus'))")
            expect(panel).to_contain_text("Atualização indisponível")
            expect(tester).to_contain_text("Desconhecido")
            assert panel.locator(".spin").count() == 0
            page.screenshot(path=str(OUTPUT / "chat-agents-unavailable.png"), full_page=True)
            UNAVAILABLE = False
            ACTIVITY = snapshot("completed", "completed")
            page.evaluate("dispatchEvent(new Event('focus'))")
            expect(panel).not_to_contain_text("indisponível")
            expect(reviewer).to_contain_text("Concluída")
            page.get_by_role("button", name="Nova sessão").click()
            expect(panel).to_contain_text("Aguardando uma tarefa")
            assert panel.locator("[data-run-id]").count() == 0
            leaked = snapshot()
            stored = {
                "sessionId": SID, "messages": [
                    {"role": "user", "text": leaked["runs"][0]["task"]},
                    {"role": "assistant", "text": json.dumps(leaked) + "Resumo preservado após atualização."},
                ],
            }
            # Seed before React mounts; a pending reset effect in the old
            # page must not overwrite the migration fixture during reload.
            page.close()
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("localStorage.setItem('bauer.chatState.v1', JSON.stringify(" + json.dumps(stored) + "))")
            page.goto(origin + "/#/chat")
            panel = page.get_by_label("Atividade dos agentes", exact=True)
            expect(page.get_by_text("Resumo preservado após atualização.", exact=True)).to_be_visible()
            assert '"runs"' not in page.locator(".msgs").inner_text()
            expect(panel).to_contain_text("Concluída")
            page.screenshot(path=str(OUTPUT / "chat-formatting-fixed.png"), full_page=True)
            assert not errors, errors
            assert not external, external
            assert REQUESTS and set(REQUESTS) == {SID}, REQUESTS
            result = {"passed": True, "browser": browser.version, "streams": STREAMS,
                      "checks": ["actual member task and tool", "SSE done preserves running runtime", "mobile no overflow and composer visible",
                                 "reload and navigation preserve session", "approval paused retry", "terminal unknown child preserves finished sibling",
                                 "unavailable suppresses stale spinner", "poll recovery", "reset clears agents", "activity metadata excluded from answer",
                                 "legacy metadata prefix repaired without removing answer"],
                      "page_errors": errors, "console_errors": stderr, "external_requests": external}
            (OUTPUT / "browser.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
