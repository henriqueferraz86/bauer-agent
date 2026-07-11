"""Narração de progresso — traduz nome cru de tool em passo humano (Fase 12/S37).

O stream já mostra os chips de tool ao vivo; aqui damos a eles um rótulo
amigável ("run_command" → "Executando comando…") para o usuário ver PASSOS, não
nomes técnicos. Fonte única, reusada pelo serve e pela CLI. Puro e testável.

`icon` é um nome de ícone Tabler (o frontend usa `ti ti-<icon>`); a CLI pode
ignorá-lo. Rótulos em PT-BR, no gerúndio, curtos."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolPhase:
    label: str   # "Executando comando"
    icon: str    # nome do ícone Tabler, ex.: "terminal-2"


_DEFAULT = ToolPhase("Trabalhando", "tool")

# Match exato por tool.
_EXACT: dict[str, ToolPhase] = {
    "run_command": ToolPhase("Executando comando", "terminal-2"),
    "execute_code": ToolPhase("Executando código", "terminal-2"),
    "process": ToolPhase("Executando processo", "terminal-2"),
    "read_file": ToolPhase("Lendo arquivos", "file-text"),
    "list_dir": ToolPhase("Explorando arquivos", "folder"),
    "glob_files": ToolPhase("Procurando arquivos", "search"),
    "search_text": ToolPhase("Procurando no código", "search"),
    "regex_search": ToolPhase("Procurando no código", "search"),
    "code_symbols": ToolPhase("Analisando o código", "code"),
    "get_imports": ToolPhase("Analisando o código", "code"),
    "write_file": ToolPhase("Escrevendo arquivo", "file-pencil"),
    "append_file": ToolPhase("Escrevendo arquivo", "file-pencil"),
    "create_dir": ToolPhase("Criando pasta", "folder-plus"),
    "patch": ToolPhase("Editando arquivo", "edit"),
    "move_file": ToolPhase("Movendo arquivo", "file-symlink"),
    "copy_file": ToolPhase("Copiando arquivo", "copy"),
    "delete_file": ToolPhase("Removendo arquivo", "trash"),
    "diff_files": ToolPhase("Comparando arquivos", "git-compare"),
    "web_search": ToolPhase("Pesquisando na web", "world-search"),
    "web_fetch": ToolPhase("Lendo página web", "world"),
    "image_generate": ToolPhase("Gerando imagem", "photo"),
    "text_to_speech": ToolPhase("Gerando áudio", "volume"),
    "transcribe_audio": ToolPhase("Transcrevendo áudio", "microphone"),
    "vision_analyze": ToolPhase("Analisando imagem", "eye"),
    "video_analyze": ToolPhase("Analisando vídeo", "video"),
    "delegate_task": ToolPhase("Delegando a especialista", "users"),
    "mixture_of_agents": ToolPhase("Consultando especialistas", "users"),
    "calculate": ToolPhase("Calculando", "calculator"),
    "datetime_now": ToolPhase("Verificando data/hora", "clock"),
    "memory": ToolPhase("Consultando memória", "brain"),
    "todo": ToolPhase("Organizando tarefas", "checklist"),
    "json_query": ToolPhase("Processando dados", "braces"),
    "encode_decode": ToolPhase("Processando dados", "braces"),
    "clarify": ToolPhase("Pedindo esclarecimento", "help"),
    "verify_app": ToolPhase("Verificando a aplicação", "checkup-list"),
    "mcp_call": ToolPhase("Chamando ferramenta externa", "plug"),
}

# Match por prefixo (ordem importa; primeiro que casar vence).
_PREFIX: tuple[tuple[str, ToolPhase], ...] = (
    ("browser_", ToolPhase("Navegando na web", "world")),
    ("kanban_", ToolPhase("Atualizando o Kanban", "layout-kanban")),
    ("lsp_", ToolPhase("Analisando o código", "code")),
    ("find_", ToolPhase("Analisando o código", "code")),
    ("social_", ToolPhase("Publicando nas redes", "share")),
    ("app_factory_", ToolPhase("Estruturando o projeto", "layout-grid")),
    ("skill", ToolPhase("Consultando skills", "sparkles")),
    ("channel_", ToolPhase("Enviando mensagem", "message")),
    ("session_", ToolPhase("Buscando no histórico", "history")),
)


def tool_phase(tool_name: str) -> ToolPhase:
    """Passo humano para uma tool. Match exato → prefixo → default 'Trabalhando'."""
    name = (tool_name or "").strip()
    if not name:
        return _DEFAULT
    exact = _EXACT.get(name)
    if exact is not None:
        return exact
    for prefix, phase in _PREFIX:
        if name.startswith(prefix):
            return phase
    return _DEFAULT
