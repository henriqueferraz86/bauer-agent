"""Efeito declarado por tool e por ação — fonte única do ``dry_run`` (T06).

Sprint 61. ``dry_run=True`` simula toda chamada que teria efeito e só deixa
passar leitura. Um efeito é qualquer um destes:

* gravação persistente local (workspace, stores internos, Kanban, LSP);
* execução de processo ou código (shell, Python, MCP, verificação de app);
* comunicação externa ou escrita remota (mensagem, post, HTTP, browser);
* geração paga em provider (imagem, voz, delegação a outro agente).

Consultas que só devolvem informação (busca web, análise de mídia,
transcrição, ``sqlite_query`` read-only) continuam rodando. O ``todo`` também
roda, porque é estado em memória do router e não grava em disco.

Tool desconhecida (built-in nova ainda sem classificação, extensão do
``ToolRegistry`` sem ``side_effects=False``) **conta como efeito**. É o
default seguro. ``tests/test_sprint61_security_execution_boundaries.py``
trava a paridade: toda built-in registrada precisa estar classificada aqui.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Built-ins sem efeito em nenhuma ação.
READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "list_dir", "read_file", "search_text", "glob_files", "regex_search", "diff_files",
    "calculate", "datetime_now", "json_query", "encode_decode", "todo", "clarify",
    "skills_list", "skill_view", "app_factory_status", "app_factory_score",
    "session_search", "kanban_read", "code_symbols", "find_definition", "get_imports",
    "find_usages", "lsp", "sqlite_query", "mcp_list_servers", "channel_list",
    "social_list_channels", "web_search", "vision_analyze", "video_analyze",
    "transcribe_audio", "browser_snapshot", "browser_scroll", "browser_console",
    "browser_get_images", "browser_vision",
})

#: Built-ins híbridas: só estas ações são leitura; qualquer outra (inclusive
#: ação vazia/desconhecida) é efeito.
READ_ACTIONS: Mapping[str, frozenset[str]] = {
    "memory": frozenset({"get", "list"}),
    "cronjob": frozenset({"list"}),
    "process": frozenset({"list", "poll", "log"}),
}

#: Built-ins com efeito em toda ação (documentação + paridade).
EFFECT_TOOLS: frozenset[str] = frozenset({
    "write_file", "append_file", "patch", "delete_file", "move_file", "create_dir",
    "run_command", "execute_code", "verify_app", "skill_manage", "app_factory_init",
    "kanban_write", "lsp_format", "lsp_rename", "mcp_call", "mcp_list_tools",
    "channel_send", "send_message", "social_post", "web_fetch", "http_request",
    "browser_navigate", "browser_click", "browser_type", "browser_press",
    "browser_dialog", "browser_cdp", "browser_back", "image_generate",
    "text_to_speech", "delegate_task", "mixture_of_agents",
})


def call_has_side_effects(
    name: str,
    args: Mapping[str, Any] | None,
    *,
    external: Any = None,
    capability_permission: str | None = None,
) -> bool:
    """``True`` se a chamada precisa ser simulada em ``dry_run``.

    ``external`` é a ``ToolDefinition`` do registry (tem precedência na
    execução). Um override de built-in só fica sem efeito se **as duas**
    definições disserem leitura. ``capability_permission`` vem do manifest
    revisado das capabilities Agno: só ``read`` roda em ``dry_run``.
    """
    if external is not None and getattr(external, "side_effects", True) is not False:
        return True
    if capability_permission is not None:
        return capability_permission != "read"
    if name in READ_ONLY_TOOLS:
        return False
    read_actions = READ_ACTIONS.get(name)
    if read_actions is not None:
        action = str((args or {}).get("action", "")).strip().lower()
        return action not in read_actions
    if external is not None and name not in EFFECT_TOOLS:
        return False  # extensão nova que declarou side_effects=False
    return True
