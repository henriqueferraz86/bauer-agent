"""WEB-08: logout precisa limpar o storage local do navegador (prova hermética).

Sobe o app real do `bauer serve` (web auth ligada, chave fictícia, client
falso, nenhuma rede além de 127.0.0.1), cria a conta, envia uma mensagem,
abre uma segunda aba, clica em Sair e lê `localStorage` nas duas abas.

    # Playwright é auxiliar, fora do lock do Bauer:
    uv run --with playwright==1.63.0 python docs/audits/2026-10-12/repro/browser_logout_storage.py

`BAUER_CHROMIUM` aponta o executável do Chromium (default: cache Playwright).
O SPA servido é o bundle versionado em `bauer/static/`.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(tempfile.mkdtemp(prefix="bauer-web08-"))
os.environ.update(BAUER_HOME=str(ROOT / "home"), BAUER_CONFIG=str(ROOT / "absent.yaml"),
                  BAUER_AGENTS_FILE=str(ROOT / "absent-agents.yaml"))

BOOTSTRAP = "audit-bootstrap-only"
EMAIL = "audit@example.test"
PASSWORD = "Audit-only-password-2026"


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _servidor(porta: int):
    import uvicorn

    from bauer.server import create_app
    from bauer.tool_router import ToolRouter

    ws = ROOT / "ws"
    ws.mkdir()
    client = MagicMock()
    client.chat_stream.side_effect = lambda *a, **k: iter(["RESPOSTA_SINTETICA_WEB08"])
    client.chat.return_value = "RESPOSTA_SINTETICA_WEB08"
    client._provider = "ollama"
    app = create_app(model_name="fake", applied_context=4096, router=ToolRouter(workspace=ws),
                     client=client, system_prompt="s", sessions_dir=ROOT / "sessions",
                     api_key=BOOTSTRAP, web_auth_enabled=True, rate_limit_requests=0)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return server


async def main() -> int:
    import re

    from playwright.async_api import async_playwright

    porta = _porta_livre()
    server = _servidor(porta)
    base = f"http://127.0.0.1:{porta}"
    chromium = os.environ.get("BAUER_CHROMIUM") or str(
        Path.home() / ".cache/ms-playwright/chromium-1248/chrome-linux64/chrome")
    resultado: dict = {}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=chromium, headless=True,
                                              args=["--no-sandbox"])
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(base + "/#/chat", wait_until="networkidle")
            await page.get_by_label("E-mail").fill(EMAIL)
            await page.get_by_label("Senha", exact=True).fill(PASSWORD)
            await page.get_by_label("Confirmar senha").fill(PASSWORD)
            await page.get_by_label("Chave de bootstrap").fill(BOOTSTRAP)
            await page.get_by_role("button", name=re.compile("Criar conta")).click()
            await page.locator("textarea").wait_for(timeout=15000)
            await page.locator("textarea").fill("mensagem sensível WEB08")
            await page.locator("textarea").press("Enter")
            await page.wait_for_function(
                "(localStorage.getItem('bauer.chatState.v1')||'').includes('RESPOSTA_SINTETICA_WEB08')",
                timeout=15000)
            outra = await ctx.new_page()
            await outra.goto(base + "/#/chat", wait_until="networkidle")
            await outra.locator("textarea").wait_for(timeout=15000)
            resultado["antes_logout_chaves"] = await page.evaluate(
                "Object.keys(localStorage).filter(k => k.startsWith('bauer.'))")

            await page.goto(base + "/#/config", wait_until="networkidle")
            await page.get_by_role("button", name=re.compile("Sair")).first.click()
            await page.get_by_label("E-mail").wait_for(timeout=15000)
            await outra.wait_for_timeout(500)

            resultado["depois_logout_chaves"] = await page.evaluate(
                "Object.keys(localStorage).filter(k => k.startsWith('bauer.'))")
            resultado["chat_state_retido"] = await page.evaluate(
                "localStorage.getItem('bauer.chatState.v1')")
            resultado["sessions_status"] = await page.evaluate(
                "async () => (await fetch('/sessions')).status")
            resultado["outra_aba_mostra_login"] = await outra.get_by_label("E-mail").count() > 0
            resultado["outra_aba_texto_visivel"] = "RESPOSTA_SINTETICA_WEB08" in (
                await outra.locator("body").inner_text())

            # novo login no mesmo navegador: a conversa anterior não reidrata
            await page.get_by_label("E-mail").fill(EMAIL)
            await page.get_by_label("Senha", exact=True).fill(PASSWORD)
            await page.get_by_role("button", name=re.compile("Entrar")).click()
            await page.locator(".sidebar").wait_for(timeout=15000)
            await page.goto(base + "/#/chat", wait_until="networkidle")
            await page.locator("textarea").wait_for(timeout=15000)
            resultado["relogin_reidrata_conversa"] = "RESPOSTA_SINTETICA_WEB08" in (
                await page.locator("body").inner_text())
            await browser.close()
    finally:
        server.should_exit = True
    resultado["WEB08_reproduz"] = bool(
        resultado.get("chat_state_retido") or resultado.get("relogin_reidrata_conversa")
        or resultado.get("outra_aba_texto_visivel"))
    print(json.dumps(resultado, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
