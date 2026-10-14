"""Validate budget Settings against a controlled local HTTP server.

uv run --no-project --with playwright==1.63.0 python \
    specs/sprint-31-settings-daily-budget/validate_browser.py
"""

from __future__ import annotations

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).parent / "validation"
LIMIT = 2.0
USED = 9.6945
SAVE_ERROR = False
LOAD_ERROR = False
PUTS = []


def budget_status():
    return {
        "profile": {"mode": "supervised", "daily_budget_usd": LIMIT, "weekly_budget_usd": 50.0,
                    "monthly_budget_usd": 100.0, "max_tool_calls_per_run": 100, "max_runtime_s_per_run": 600,
                    "max_parallel_runs": 3, "agent_budgets_usd": {}, "company_budgets_usd": {},
                    "max_cost_usd_per_run": None, "updated_at": "2026-10-14T00:00:00+00:00"},
        "daily": {"limit_usd": LIMIT, "used_usd": USED, "remaining_usd": max(0, LIMIT - USED), "exceeded": USED >= LIMIT},
        "weekly": {"limit_usd": 50.0, "used_usd": USED, "remaining_usd": 50 - USED, "exceeded": False},
        "monthly": {"limit_usd": 100.0, "used_usd": USED, "remaining_usd": 100 - USED, "exceeded": False},
        "by_agent": {}, "by_company": {},
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
        path = urlsplit(self.path).path
        if path == "/api/obs/budget":
            if LOAD_ERROR:
                return self.json_response({"detail": "Orçamento temporariamente indisponível."}, 503)
            return self.json_response(budget_status())
        responses = {
            "/auth/state": {"enabled": False, "api_key_required": False, "authenticated": False},
            "/status": {"model": "controlled-model", "provider": "ollama", "context_tokens": 4096,
                        "tools": [], "auth_enabled": False},
            "/health": {"status": "ok"}, "/api/gateway/status": {"running": False},
            "/api/config": {"config": {}}, "/api/config/profiles": {"profiles": [], "active": None},
            "/api/auth/openai/status": {"connected": False},
        }
        if path in responses:
            return self.json_response(responses[path])
        if path.startswith("/api/"):
            return self.json_response({})
        return super().do_GET()

    def do_PUT(self):
        global LIMIT
        if urlsplit(self.path).path != "/api/obs/budget":
            self.send_error(404)
            return
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        PUTS.append(payload)
        if SAVE_ERROR:
            return self.json_response({"detail": "Não foi possível salvar o limite. Tente novamente."}, 503)
        assert set(payload) == {"daily_budget_usd"}
        LIMIT = payload["daily_budget_usd"]
        return self.json_response(budget_status())


def main():
    global SAVE_ERROR, LOAD_ERROR
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
            page.on("console", lambda message: stderr.append(message.text) if message.type == "error" else None)
            page.goto(origin + "/#/settings")
            card = page.get_by_label("Orçamento de execução", exact=True)
            field = page.get_by_label("Limite diário (US$)", exact=True)
            save = card.get_by_role("button", name="Salvar limite diário", exact=True)
            expect(field).to_have_value("2")
            expect(card).to_contain_text("24")
            expect(card).to_contain_text("9,6945")
            expect(card).to_contain_text("Orçamento diário atingido")
            page.screenshot(path=str(OUTPUT / "settings-budget-exceeded-desktop.png"), full_page=True)
            initial = len(PUTS)
            for invalid in ("", "-1", "1e309"):
                field.fill(invalid)
                save.click()
                expect(card.get_by_role("alert")).to_be_visible()
            assert len(PUTS) == initial
            field.fill("20")
            save.click()
            expect(card.get_by_role("status")).to_be_visible()
            expect(card).to_contain_text("10,3055")
            assert PUTS[-1] == {"daily_budget_usd": 20}
            assert LIMIT == 20 and USED == 9.6945
            page.reload()
            expect(field).to_have_value("20")
            expect(card).to_contain_text("10,3055")
            page.screenshot(path=str(OUTPUT / "settings-budget-saved-desktop.png"), full_page=True)
            field.fill("0")
            save.click()
            expect(field).to_have_value("0")
            expect(card.get_by_role("status")).to_contain_text("Novas execuções ficam bloqueadas")
            assert PUTS[-1] == {"daily_budget_usd": 0}
            SAVE_ERROR = True
            field.fill("30")
            save.click()
            expect(card.get_by_role("alert")).to_be_visible()
            expect(field).to_have_value("30")
            assert LIMIT == 0 and USED == 9.6945
            page.screenshot(path=str(OUTPUT / "settings-budget-save-error.png"), full_page=True)
            count = len(PUTS)
            card.get_by_role("button", name="Consultar orçamento", exact=True).click()
            expect(card.get_by_role("alert")).not_to_be_visible()
            expect(field).to_have_value("30")
            expect(card).to_contain_text("Limite em vigor: US$ 0,00")
            assert len(PUTS) == count
            SAVE_ERROR = False
            save.click()
            expect(card.get_by_role("alert")).not_to_be_visible()
            expect(card.get_by_role("status")).to_be_visible()
            expect(card).to_contain_text("20,3055")
            page.set_viewport_size({"width": 390, "height": 844})
            card.scroll_into_view_if_needed()
            box = card.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390
            assert card.evaluate("el => el.scrollWidth <= el.clientWidth")
            expect(field).to_be_visible()
            expect(save).to_be_visible()
            page.screenshot(path=str(OUTPUT / "settings-budget-mobile.png"), full_page=True)
            LOAD_ERROR = True
            page.reload()
            expect(card.get_by_role("alert")).to_be_visible()
            retry = card.get_by_role("button", name="Tentar novamente", exact=True)
            expect(retry).to_be_visible()
            LOAD_ERROR = False
            retry.click()
            expect(field).to_have_value("30")
            assert not errors, errors
            assert not external, external
            result = {"passed": True, "browser": browser.version,
                      "checks": ["initial exceeded rolling24h", "invalid input no PUT", "raise20 remaining10.3055 preserves usage",
                                 "reload persisted policy", "zero visibly blocks", "save503 preserves draft and effective limit",
                                 "consult after save503 preserves draft without PUT", "retry successful save", "390px card no overflow", "load503 retry"],
                      "requests": PUTS, "page_errors": errors, "console_errors": stderr, "external_requests": external}
            (OUTPUT / "browser.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
