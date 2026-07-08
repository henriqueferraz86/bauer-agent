"""Browser automation tools (Playwright): navigate/click/type/snapshot/etc.

Mixin herdado por ToolRouter. Toda browser tool roda na thread dedicada e
persistente de _get_browser_executor (Playwright sync e thread-affine). Os
atributos de instancia (_browser_page/_ctx/_pw/_executor) sao criados no
__init__ do ToolRouter e acessados via self.
"""

from __future__ import annotations

import json

from .base import ToolError


class BrowserToolsMixin:
    """Ferramentas de automacao de navegador via Playwright (thread dedicada)."""

    _BROWSER_CONSOLE_MSGS: list = []  # captura msgs de console entre calls

    def _get_browser_executor(self):
        """Executor de thread única e persistente para as browser tools (G18).

        Playwright sync é thread-affine: a página/contexto criados num
        browser_navigate só podem ser dirigidos pela MESMA thread. Como o
        execute() roda tools com timeout em threads descartáveis, cada call
        de browser caía numa thread diferente → 'cannot switch to a different
        thread'. Aqui mantemos um único worker (max_workers=1) vivo pela
        sessão inteira para que todas as browser tools rodem na mesma thread.
        """
        import concurrent.futures as _cf
        if self._browser_executor is None:
            self._browser_executor = _cf.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="bauer-browser"
            )
        return self._browser_executor

    def close_browser_executor(self) -> None:
        """Encerra a thread dedicada do browser (chamar no fim da sessão)."""
        if self._browser_executor is not None:
            self._browser_executor.shutdown(wait=False)
            self._browser_executor = None

    def _ensure_browser(self) -> object:
        """Garante que o browser Playwright está iniciado. Retorna a Page ativa."""
        if self._browser_page is not None:
            return self._browser_page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ToolError(
                "browser_*: requer Playwright — execute: pip install playwright && playwright install chromium"
            )
        try:
            self._browser_pw = sync_playwright().__enter__()
            self._browser_ctx = self._browser_pw.chromium.launch(headless=True)
            self._browser_page = self._browser_ctx.new_page()
            # Captura mensagens de console
            self._BROWSER_CONSOLE_MSGS = []
            self._browser_page.on(
                "console",
                lambda msg: self._BROWSER_CONSOLE_MSGS.append(
                    f"[{msg.type}] {msg.text}"
                ),
            )
        except Exception as exc:
            raise ToolError(f"browser: falha ao iniciar Playwright — {exc}") from exc
        return self._browser_page

    def _browser_navigate(self, args: dict) -> str:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ToolError("browser_navigate: 'url' deve começar com http:// ou https://")
        wait_until = str(args.get("wait_until", "load"))
        valid_waits = ("load", "domcontentloaded", "networkidle")
        if wait_until not in valid_waits:
            wait_until = "load"
        page = self._ensure_browser()
        try:
            response = page.goto(url, wait_until=wait_until, timeout=30_000)
            status = response.status if response else "?"
            return f"[browser] Navegou para {url} — status HTTP {status} | título: {page.title()}"
        except Exception as exc:
            raise ToolError(f"browser_navigate: {exc}") from exc

    def _browser_snapshot(self, args: dict) -> str:
        page = self._ensure_browser()
        include_hidden = bool(args.get("include_hidden", False))
        try:
            # Retorna texto acessível via innerText em estrutura simplificada
            script = """
            () => {
                function walk(el, depth) {
                    let tag = el.tagName ? el.tagName.toLowerCase() : '';
                    let role = el.getAttribute ? (el.getAttribute('role') || '') : '';
                    let label = (el.getAttribute ? el.getAttribute('aria-label') : '') || el.innerText || el.textContent || '';
                    label = (label || '').replace(/\\s+/g, ' ').trim().slice(0, 120);
                    let hidden = el.hidden || (el.style && el.style.display === 'none') || (el.getAttribute && el.getAttribute('aria-hidden') === 'true');
                    if (hidden && !arguments[1]) return '';
                    let indent = '  '.repeat(depth);
                    let info = indent + (tag || '?');
                    if (role) info += `[role=${role}]`;
                    if (label) info += ` "${label}"`;
                    let children = Array.from(el.children || []).map(c => walk(c, depth+1)).filter(Boolean).join('\\n');
                    return children ? info + '\\n' + children : info;
                }
                return walk(document.body, 0);
            }
            """
            snapshot = page.evaluate(script)
            url = page.url
            title = page.title()
            return f"[browser_snapshot] {title} | {url}\n\n{snapshot[:8000]}"
        except Exception as exc:
            raise ToolError(f"browser_snapshot: {exc}") from exc

    def _browser_click(self, args: dict) -> str:
        selector = str(args.get("selector", "")).strip()
        if not selector:
            raise ToolError("browser_click: 'selector' é obrigatório.")
        by = str(args.get("by", "css")).lower()
        page = self._ensure_browser()
        try:
            if by == "text":
                page.get_by_text(selector).first.click(timeout=10_000)
            elif by == "role":
                page.get_by_role(selector).first.click(timeout=10_000)
            elif by == "xpath":
                page.locator(f"xpath={selector}").first.click(timeout=10_000)
            else:
                page.locator(selector).first.click(timeout=10_000)
            return f"[browser_click] Clicou em '{selector}' (by={by})"
        except Exception as exc:
            raise ToolError(f"browser_click: {exc}") from exc

    def _browser_type(self, args: dict) -> str:
        selector = str(args.get("selector", "")).strip()
        text = str(args.get("text", ""))
        if not selector:
            raise ToolError("browser_type: 'selector' é obrigatório.")
        clear_first = bool(args.get("clear_first", True))
        page = self._ensure_browser()
        try:
            loc = page.locator(selector).first
            if clear_first:
                loc.fill(text, timeout=10_000)
            else:
                loc.type(text, timeout=10_000)
            return f"[browser_type] Digitou {len(text)} chars em '{selector}'"
        except Exception as exc:
            raise ToolError(f"browser_type: {exc}") from exc

    def _browser_scroll(self, args: dict) -> str:
        direction = str(args.get("direction", "down")).lower()
        amount = self._coerce_int(args.get("amount", 500), default=500, minimum=1)
        selector = args.get("selector")
        page = self._ensure_browser()
        try:
            if direction == "top":
                page.evaluate("window.scrollTo(0, 0)")
            elif direction == "bottom":
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            elif direction == "up":
                page.evaluate(f"window.scrollBy(0, -{amount})")
            else:
                page.evaluate(f"window.scrollBy(0, {amount})")
            return f"[browser_scroll] Rolou {direction} ({amount}px)"
        except Exception as exc:
            raise ToolError(f"browser_scroll: {exc}") from exc

    def _browser_back(self, args: dict) -> str:
        page = self._ensure_browser()
        try:
            page.go_back(timeout=10_000)
            return f"[browser_back] Voltou para: {page.url}"
        except Exception as exc:
            raise ToolError(f"browser_back: {exc}") from exc

    def _browser_press(self, args: dict) -> str:
        key = str(args.get("key", "")).strip()
        if not key:
            raise ToolError("browser_press: 'key' é obrigatório (ex: Enter, Tab, Control+A).")
        selector = args.get("selector")
        page = self._ensure_browser()
        try:
            if selector:
                page.locator(str(selector)).first.press(key, timeout=10_000)
            else:
                page.keyboard.press(key)
            return f"[browser_press] Pressionou '{key}'"
        except Exception as exc:
            raise ToolError(f"browser_press: {exc}") from exc

    def _browser_console(self, args: dict) -> str:
        self._ensure_browser()
        max_lines = self._coerce_int(args.get("max_lines", 50), default=50, minimum=1)
        msgs = self._BROWSER_CONSOLE_MSGS[-max_lines:]
        if not msgs:
            return "[browser_console] Sem mensagens de console."
        return "[browser_console]\n" + "\n".join(msgs)

    def _browser_get_images(self, args: dict) -> str:
        include_data = bool(args.get("include_data_urls", False))
        page = self._ensure_browser()
        try:
            images = page.evaluate("""
            () => Array.from(document.images).map(img => ({
                src: img.src, alt: img.alt, width: img.naturalWidth, height: img.naturalHeight
            }))
            """)
            if not include_data:
                images = [i for i in images if not i["src"].startswith("data:")]
            if not images:
                return "[browser_get_images] Nenhuma imagem encontrada."
            lines = [f"[browser_get_images] {len(images)} imagem(ns):"]
            for img in images[:50]:
                lines.append(
                    f"  {img['width']}x{img['height']} | alt='{img['alt'][:60]}' | {img['src'][:120]}"
                )
            return "\n".join(lines)
        except Exception as exc:
            raise ToolError(f"browser_get_images: {exc}") from exc

    def _browser_vision(self, args: dict) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("browser_vision: 'query' é obrigatório.")
        vision_client = self._resolve_vision_client("browser_vision")  # G18.4
        page = self._ensure_browser()
        try:
            screenshot_bytes = page.screenshot(full_page=False)
        except Exception as exc:
            raise ToolError(f"browser_vision: falha ao capturar screenshot — {exc}") from exc

        import base64
        b64 = base64.b64encode(screenshot_bytes).decode()
        data_url = f"data:image/png;base64,{b64}"
        try:
            msg = {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"[screenshot do browser — {page.url}] {query}"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
            return str(self._llm_single_turn(vision_client, [msg]))
        except Exception as exc:
            raise ToolError(f"browser_vision: falha na análise — {exc}") from exc

    def _browser_dialog(self, args: dict) -> str:
        action = str(args.get("action", "accept")).lower()
        prompt_text = str(args.get("prompt_text", ""))
        page = self._ensure_browser()

        dialog_handled = {"done": False, "msg": ""}

        def _handle(dialog):
            if action == "dismiss":
                dialog.dismiss()
                dialog_handled["msg"] = f"[browser_dialog] Descartou diálogo '{dialog.type}': {dialog.message[:80]}"
            else:
                dialog.accept(prompt_text or "")
                dialog_handled["msg"] = f"[browser_dialog] Aceitou diálogo '{dialog.type}': {dialog.message[:80]}"
            dialog_handled["done"] = True

        page.once("dialog", _handle)
        # Aguarda até 5s por um diálogo
        try:
            page.wait_for_timeout(5_000)
        except Exception:
            pass
        if dialog_handled["done"]:
            return dialog_handled["msg"]
        page.remove_listener("dialog", _handle)
        return "[browser_dialog] Nenhum diálogo detectado em 5s."

    def _browser_cdp(self, args: dict) -> str:
        method = str(args.get("method", "")).strip()
        if not method:
            raise ToolError("browser_cdp: 'method' é obrigatório (ex: Page.captureScreenshot).")
        params = args.get("params", {})
        if not isinstance(params, dict):
            params = {}
        page = self._ensure_browser()
        try:
            client = page.context.new_cdp_session(page)
            result = client.send(method, params)
            client.detach()
            result_str = json.dumps(result, ensure_ascii=False)[:2000]
            return f"[browser_cdp] {method} → {result_str}"
        except Exception as exc:
            raise ToolError(f"browser_cdp: {exc}") from exc
