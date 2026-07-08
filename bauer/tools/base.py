"""Tipos e exceções compartilhados pelos mixins de tools.

Mora aqui (e não em tool_router.py) para evitar import circular: os mixins
em bauer/tools/*.py importam destas classes, e tool_router.py as re-exporta
para preservar `from bauer.tool_router import ToolError, SandboxError, DryRunResult`.
"""

from __future__ import annotations


class ToolError(Exception):
    """Erro de execução de tool com mensagem legível."""


class SandboxError(ToolError):
    """Tentativa de acesso fora do workspace."""


class DryRunResult:
    """Retornado quando dry_run=True: descreve o que teria acontecido sem executar."""
    def __init__(self, tool: str, summary: str):
        self.tool = tool
        self.summary = summary

    def __str__(self) -> str:
        return f"[dry_run] {self.tool}: {self.summary}"


# Limites de I/O de arquivo, compartilhados por FsToolsMixin e pelas descrições
# de schema em tool_router.py (re-exportados de lá).

# Limite de chars no OUTPUT de read_file (após paginação + numeração de linha).
# Caracteres são proxy de tokens; ~100K chars ≈ 25-35K tokens.
_MAX_READ_BYTES = 100_000
# Ceiling absoluto de tamanho de arquivo que read_file abre (G17.1).
# Acima disso, mesmo leitura paginada é recusada → use search_text/grep.
_MAX_FILE_BYTES = 5_000_000
# Número de linhas lidas por padrão quando 'limit' não é informado (G17.1).
_DEFAULT_READ_LINES = 2000
# Limite de resultados de busca.
_MAX_SEARCH_RESULTS = 50


def _syntax_check(path, text: str) -> str | None:
    """Valida sintaxe de arquivos .py/.json/.yaml recém-escritos.

    Retorna descrição curta do erro ou None se OK/extensão não suportada.
    Feedback imediato pro modelo: sem isto, um write_file com syntax error
    só era descoberto tool calls depois, ao tentar executar/importar.

    Compartilhado por FsToolsMixin (_write_file) e tool_router (_patch_file).
    """
    suffix = str(getattr(path, "suffix", "")).lower()
    try:
        if suffix == ".py":
            import ast as _ast
            _ast.parse(text)
        elif suffix == ".json":
            import json as _json
            _json.loads(text)
        elif suffix in (".yaml", ".yml"):
            import yaml as _yaml
            _yaml.safe_load(text)
        return None
    except SyntaxError as exc:
        return f"Python syntax error na linha {exc.lineno}: {exc.msg}"
    except Exception as exc:
        kind = suffix.lstrip(".").upper()
        return f"{kind} syntax error: {str(exc)[:200]}"
