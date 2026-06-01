"""Sistema de plugin hooks para o Bauer Agent.

Permite que plugins externos registrem callbacks para eventos do ciclo de vida
do agente sem modificar o código-fonte. Plugins são arquivos .py em ~/.bauer/plugins/.

Hooks disponíveis:
  pre_tool_call(action, args)                   → antes de executar uma tool
  post_tool_call(action, args, result, error)   → após executar uma tool
  pre_llm_call(model, messages)                 → antes de chamar o LLM
  post_llm_call(model, messages, response)      → após chamar o LLM
  session_start(session_id, model)              → início de sessão
  session_end(session_id, model)                → fim de sessão

Uso (em um plugin ~/.bauer/plugins/meu_plugin.py):

    from bauer.plugin_hooks import hooks

    @hooks.on("pre_tool_call")
    def meu_hook(action, args):
        print(f"Executando tool: {action}")

    @hooks.on("post_llm_call")
    def log_resposta(model, messages, response):
        with open("llm_log.txt", "a") as f:
            f.write(f"{model}: {response[:100]}\\n")

Thread-safety: hooks são chamados na thread que dispara o evento.
Erros em hooks são silenciados — nunca bloqueiam a execução do agente.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# Hooks válidos — nomes fora deste set são silenciosamente ignorados
VALID_HOOKS = frozenset({
    "pre_tool_call",
    "post_tool_call",
    "pre_llm_call",
    "post_llm_call",
    "session_start",
    "session_end",
})


class HookRegistry:
    """Registro central de hooks. Singleton acessível via `hooks`."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)
        self._plugins_loaded: bool = False

    # ── Registro de handlers ──────────────────────────────────────────────────

    def on(self, event: str) -> Callable:
        """Decorador para registrar um handler em um evento.

        Exemplo:
            @hooks.on("pre_tool_call")
            def meu_hook(action, args):
                ...
        """
        def _decorator(fn: Callable) -> Callable:
            self.register(event, fn)
            return fn
        return _decorator

    def register(self, event: str, fn: Callable) -> None:
        """Registra um handler para um evento."""
        if event not in VALID_HOOKS:
            log.warning("plugin_hooks: evento desconhecido '%s' ignorado.", event)
            return
        self._handlers[event].append(fn)

    def unregister(self, event: str, fn: Callable) -> None:
        """Remove um handler de um evento."""
        try:
            self._handlers[event].remove(fn)
        except ValueError:
            pass

    def clear(self, event: str | None = None) -> None:
        """Remove todos os handlers de um evento (ou todos os eventos)."""
        if event is None:
            self._handlers.clear()
        else:
            self._handlers[event].clear()

    # ── Disparo de eventos ────────────────────────────────────────────────────

    def emit(self, event: str, **kwargs: Any) -> None:
        """Dispara um evento chamando todos os handlers registrados.

        Erros em handlers são logados e silenciados — nunca propagados.
        """
        for fn in list(self._handlers.get(event, [])):
            try:
                fn(**kwargs)
            except Exception as exc:
                log.warning(
                    "plugin_hooks: erro em handler '%s' para evento '%s': %s",
                    getattr(fn, "__name__", "?"),
                    event,
                    exc,
                )

    # ── Carregamento automático de plugins ────────────────────────────────────

    def load_plugins(self, plugin_dir: Path | None = None) -> list[str]:
        """Carrega plugins de ~/.bauer/plugins/*.py.

        Args:
            plugin_dir: Diretório de plugins. Padrão: ~/.bauer/plugins/

        Returns:
            Lista de nomes de plugins carregados com sucesso.
        """
        if plugin_dir is None:
            plugin_dir = Path.home() / ".bauer" / "plugins"

        if not plugin_dir.exists():
            return []

        loaded: list[str] = []
        for plugin_path in sorted(plugin_dir.glob("*.py")):
            plugin_name = plugin_path.stem
            try:
                spec = importlib.util.spec_from_file_location(
                    f"bauer_plugin_{plugin_name}", plugin_path
                )
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[f"bauer_plugin_{plugin_name}"] = module
                spec.loader.exec_module(module)  # type: ignore[union-attr]
                loaded.append(plugin_name)
                log.info("plugin_hooks: plugin '%s' carregado.", plugin_name)
            except Exception as exc:
                log.warning(
                    "plugin_hooks: falha ao carregar plugin '%s': %s",
                    plugin_name,
                    exc,
                )

        self._plugins_loaded = True
        return loaded

    def ensure_plugins_loaded(self, plugin_dir: Path | None = None) -> None:
        """Carrega plugins na primeira chamada; no-op nas chamadas seguintes."""
        if not self._plugins_loaded:
            self.load_plugins(plugin_dir)


# Singleton global — importado por qualquer módulo que queira disparar eventos
hooks = HookRegistry()
