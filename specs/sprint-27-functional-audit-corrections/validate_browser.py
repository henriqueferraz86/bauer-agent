"""UI functional smoke: built SPA, controlled loopback API, no provider/auth.

Run with the cached auxiliary environment:
UV_OFFLINE=1 uv run --no-project --with playwright==1.63.0 python specs/sprint-27-functional-audit-corrections/validate_browser.py
"""

import json
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).parent / "validation"
RELEASE = Event()
MODEL = {"model": "controlled-old", "provider": "ollama"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "bauer" / "static"), **kwargs)

    def log_message(self, *_args):
        pass

    def json_response(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write("data: Resposta parcial\n\n".encode())
            self.wfile.flush()
            if not RELEASE.wait(20):
                return
            self.wfile.write("data:  concluída\n\nevent: done\ndata: controlled-session\n\n".encode())
            self.wfile.flush()
            return
        responses = {
            "/auth/state": {"enabled": False, "api_key_required": False, "authenticated": False},
            "/status": {**MODEL, "context_tokens": 4096, "tools": [], "auth_enabled": False},
            "/models": {"active": MODEL["model"], "models": []},
            "/health": {"status": "ok"},
            "/api/gateway/status": {"running": False},
            "/api/models/providers": {"providers": ["ollama"]},
            "/api/models/catalog": {"total": 1, "free_count": 1, "models": [{
                "id": "controlled-new", "provider": "ollama", "is_free": True,
            }]},
        }
        if path in responses:
            return self.json_response(responses[path])
        if path.startswith("/api/"):
            return self.json_response({})
        return super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path != "/models/switch":
            self.send_error(404)
            return
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        MODEL.update(model=data["model"], provider=data.get("provider") or "ollama")
        self.json_response({"active": MODEL["model"], "provider": MODEL["provider"]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-path", help="Use an already installed Chromium; never download one")
    options = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    errors = []
    blocked = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=options.browser_path)
            context = browser.new_context()

            def local_only(route):
                if route.request.url.startswith(origin + "/"):
                    route.continue_()
                else:
                    blocked.append(route.request.url)
                    route.abort()

            context.route("**/*", local_only)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/#/chat")
            expect(page.locator(".titlebar .pill")).to_contain_text("controlled-old")
            page.locator("textarea").fill("mensagem controlada")
            page.locator("textarea").press("Enter")
            expect(page.get_by_text("Resposta parcial", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Nova sessão")).to_be_disabled()
            page.evaluate("location.hash = '#/models'")
            expect(page.get_by_placeholder("Buscar modelos…")).to_be_visible()
            RELEASE.set()
            page.wait_for_function("""() => Object.values(localStorage).some(value =>
                value.includes('controlled-session') && value.includes('concluída'))""")
            page.get_by_text("controlled-new", exact=True).click()
            expect(page.locator(".titlebar .pill")).to_contain_text("controlled-new")
            page.evaluate("location.hash = '#/chat'")
            expect(page.get_by_text("Resposta parcial concluída", exact=True)).to_be_visible()
            expect(page.locator(".hud-model")).to_contain_text("controlled-new")
            expect(page.get_by_role("button", name="Nova sessão")).to_be_enabled()
            page.reload()
            expect(page.get_by_text("Resposta parcial concluída", exact=True)).to_be_visible()
            expect(page.locator(".titlebar .pill")).to_contain_text("controlled-new")
            expect(page.locator(".hud-model")).to_contain_text("controlled-new")
            assert not errors, errors
            assert not blocked, blocked
            page.screenshot(path=str(OUTPUT / "browser.png"), full_page=True)

            # Storage failures must not break the React effect outside the
            # detached turn owner. Inject before mounting, then exercise a
            # complete turn and reset with both write/remove unavailable.
            quota_context = browser.new_context()
            quota_context.route("**/*", local_only)
            quota_page = quota_context.new_page()
            quota_page.on("pageerror", lambda error: errors.append(str(error)))
            quota_page.add_init_script("""(() => {
                const originalSet = Storage.prototype.setItem;
                const originalRemove = Storage.prototype.removeItem;
                window.controlledQuotaWrites = 0;
                window.controlledQuotaRemoves = 0;
                Storage.prototype.setItem = function(key, value) {
                    if (key === 'bauer.chatState.v1') {
                        window.controlledQuotaWrites++;
                        throw new DOMException('controlled storage full', 'QuotaExceededError');
                    }
                    return originalSet.call(this, key, value);
                };
                Storage.prototype.removeItem = function(key) {
                    if (key === 'bauer.chatState.v1') {
                        window.controlledQuotaRemoves++;
                        throw new DOMException('controlled storage unavailable', 'QuotaExceededError');
                    }
                    return originalRemove.call(this, key);
                };
            })();""")
            quota_page.goto(origin + "/#/chat")
            expect(quota_page.locator("textarea")).to_be_visible()
            quota_page.locator("textarea").fill("mensagem com storage indisponível")
            quota_page.locator("textarea").press("Enter")
            expect(quota_page.get_by_text("Resposta parcial concluída", exact=True)).to_be_visible()
            expect(quota_page.locator("textarea")).to_be_enabled()
            expect(quota_page.get_by_role("button", name="Nova sessão")).to_be_enabled()
            quota_page.get_by_role("button", name="Nova sessão").click()
            expect(quota_page.get_by_text("Resposta parcial concluída", exact=True)).to_have_count(0)
            expect(quota_page.locator("textarea")).to_be_visible()
            quota_page.locator("textarea").fill("novo turno após reset sem storage")
            quota_page.locator("textarea").press("Enter")
            expect(quota_page.get_by_text("Resposta parcial concluída", exact=True)).to_be_visible()
            expect(quota_page.get_by_role("button", name="Nova sessão")).to_be_enabled()
            storage_attempts = quota_page.evaluate("({writes: controlledQuotaWrites, removes: controlledQuotaRemoves})")
            assert storage_attempts["writes"] > 0 and storage_attempts["removes"] > 0, storage_attempts
            assert not errors, errors
            assert not blocked, blocked
            quota_page.screenshot(path=str(OUTPUT / "browser-quota.png"), full_page=True)
            result = {"passed": True, "browser": browser.version, "provider": "controlled fixture",
                      "checks": ["stream survives navigation", "session and response persist after reload",
                                 "new session disabled during turn", "model switch updates titlebar and chat HUD",
                                 "storage quota preserves mounted chat, completed turn and reset"],
                      "blocked_storage_attempts": storage_attempts,
                      "external_requests": blocked, "page_errors": errors}
            (OUTPUT / "browser.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result))
            browser.close()
    finally:
        RELEASE.set()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
