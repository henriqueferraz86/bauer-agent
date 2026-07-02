"""Code-intelligence e LSP: find_definition/usages, get_imports, code_symbols,
e as tools lsp_* (hover/definitions/references/diagnostics/completion/etc.).

Mixin herdado por ToolRouter. As tools AST-based usam o modulo
..code_intelligence; as lsp_* falam com ..lsp.manager / ..lsp.servers.
"""

from __future__ import annotations

import json

from .base import ToolError


class CodeIntelToolsMixin:
    """Navegacao de codigo (AST) e ponte LSP."""

    def _code_symbols(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        if not file_rel:
            raise ToolError("code_symbols requer 'file'.")
        from ..code_intelligence import get_python_symbols
        path = self._sandbox(file_rel)
        if not path.exists():
            raise ToolError(f"Arquivo nao encontrado: '{file_rel}'")
        result = get_python_symbols(str(path))
        if "error" in result:
            return f"[Erro de parse] {result['error']}"
        lines: list[str] = []
        for fn in result.get("functions", []):
            async_prefix = "async " if fn.get("is_async") else ""
            args_str = ", ".join(fn.get("args", []))
            lines.append(f"  func  L{fn['line']:4d}  {async_prefix}{fn['name']}({args_str})")
        for cls in result.get("classes", []):
            bases = ", ".join(cls.get("bases", []))
            base_str = f"({bases})" if bases else ""
            lines.append(f"  class L{cls['line']:4d}  {cls['name']}{base_str}")
        for var in result.get("variables", []):
            lines.append(f"  var   L{var['line']:4d}  {var['name']}")
        if not lines:
            return f"Nenhum simbolo encontrado em '{file_rel}'"
        return f"[Simbolos de {file_rel}]\n" + "\n".join(lines)

    def _find_definition(self, args: dict) -> str:
        symbol = str(args.get("symbol", "")).strip()
        workspace = str(args.get("workspace", ".")).strip() or "."
        if not symbol:
            raise ToolError("find_definition requer 'symbol'.")
        from ..code_intelligence import find_symbol_definitions
        root = self._sandbox(workspace)
        results = find_symbol_definitions(symbol, str(root))
        if not results:
            return f"Definicao de '{symbol}' nao encontrada em '{workspace}'"
        lines = [f"  {r['type']:8s} L{r['line']:4d}  {r['file']}\n            {r['signature']}"
                 for r in results]
        return f"[Definicoes de '{symbol}']\n" + "\n".join(lines)

    def _get_imports(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        if not file_rel:
            raise ToolError("get_imports requer 'file'.")
        from ..code_intelligence import get_imports
        path = self._sandbox(file_rel)
        if not path.exists():
            raise ToolError(f"Arquivo nao encontrado: '{file_rel}'")
        imports = get_imports(str(path))
        if not imports:
            return f"Nenhum import encontrado em '{file_rel}'"
        return f"[Imports de {file_rel}]\n" + "\n".join(f"  {imp}" for imp in imports)

    def _find_usages(self, args: dict) -> str:
        symbol = str(args.get("symbol", "")).strip()
        workspace = str(args.get("workspace", ".")).strip() or "."
        file_pattern = str(args.get("file_pattern", "*.py")).strip() or "*.py"
        if not symbol:
            raise ToolError("find_usages requer 'symbol'.")
        from ..code_intelligence import get_call_sites
        root = self._sandbox(workspace)
        results = get_call_sites(symbol, str(root), file_pattern=file_pattern)
        if not results:
            return f"Nenhum uso de '{symbol}' encontrado em '{workspace}'"
        lines = [f"  {r['file']}:{r['line']}: {r['context']}" for r in results]
        return f"[Usos de '{symbol}']\n" + "\n".join(lines)

    def _lsp_call(
        self,
        method: str,
        file_rel: str,
        line: int,
        char: int,
        **kwargs,
    ) -> dict | list | None:
        """Helper: run an LSP async call synchronously using asyncio."""
        import asyncio
        from ..lsp.servers import server_for_file
        from ..lsp.manager import get_or_start

        # workspace_symbols doesn't need a real file path
        if method == "workspace_symbols":
            query = file_rel  # repurpose first positional arg as query
            server_cfg = next(
                (c for c in __import__("bauer.lsp.servers", fromlist=["KNOWN_SERVERS"]).KNOWN_SERVERS.values()
                 if c.lang == "python"),
                None,
            )
        else:
            file_abs = self._sandbox(file_rel)
            server_cfg = server_for_file(str(file_abs))

        if server_cfg is None:
            return None

        workspace = str(self.workspace)

        async def _run():
            mgr = await get_or_start(server_cfg, workspace)
            if mgr is None:
                return None
            client = mgr.client()
            if method == "hover":
                return await client.hover(file_abs.as_uri(), line, char)
            if method == "definitions":
                return await client.definitions(file_abs.as_uri(), line, char)
            if method == "references":
                return await client.references(file_abs.as_uri(), line, char)
            if method == "diagnostics":
                return await client.diagnostics(file_abs.as_uri())
            if method == "workspace_symbols":
                return await client.workspace_symbols(query)
            if method == "completion":
                return await client.completion(file_abs.as_uri(), line, char)
            if method == "code_actions":
                return await client.code_actions(
                    file_abs.as_uri(), line, char,
                    kwargs.get("end_line", line),
                    kwargs.get("end_char", char),
                )
            if method == "format_document":
                return await client.format_document(
                    file_abs.as_uri(),
                    tab_size=kwargs.get("tab_size", 4),
                    insert_spaces=kwargs.get("insert_spaces", True),
                )
            if method == "rename_symbol":
                return await client.rename_symbol(
                    file_abs.as_uri(), line, char,
                    kwargs.get("new_name", ""),
                )
            return None

        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(_run())
        except RuntimeError:
            # Already in async context — can't run_until_complete
            return None
        except Exception:
            return None

    def _lsp_hover(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        line = self._coerce_int(args.get("line", 0), default=0, minimum=0)
        char = self._coerce_int(args.get("character", 0), default=0, minimum=0)
        if not file_rel:
            raise ToolError("lsp_hover requer 'file'.")
        result = self._lsp_call("hover", file_rel, line, char)
        if result is None:
            server_hint = "pyright" if file_rel.endswith(".py") else "typescript-language-server"
            return json.dumps({"error": "LSP server not running", "hint": f"pip/npm install {server_hint}"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _lsp_definitions(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        line = self._coerce_int(args.get("line", 0), default=0, minimum=0)
        char = self._coerce_int(args.get("character", 0), default=0, minimum=0)
        if not file_rel:
            raise ToolError("lsp_definitions requer 'file'.")
        result = self._lsp_call("definitions", file_rel, line, char)
        if result is None:
            return json.dumps({"error": "LSP server not running"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _lsp_references(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        line = self._coerce_int(args.get("line", 0), default=0, minimum=0)
        char = self._coerce_int(args.get("character", 0), default=0, minimum=0)
        if not file_rel:
            raise ToolError("lsp_references requer 'file'.")
        result = self._lsp_call("references", file_rel, line, char)
        if result is None:
            return json.dumps({"error": "LSP server not running"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _lsp_diagnostics(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        if not file_rel:
            raise ToolError("lsp_diagnostics requer 'file'.")
        result = self._lsp_call("diagnostics", file_rel, 0, 0)
        if result is None:
            return json.dumps({"error": "LSP server not running"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _lsp_workspace_symbols(self, args: dict) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("lsp_workspace_symbols requer 'query'.")
        # file_rel is repurposed as query string for workspace_symbols
        result = self._lsp_call("workspace_symbols", query, 0, 0)
        if result is None:
            return json.dumps({"error": "LSP server not running", "hint": "pip install pyright"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _lsp_completion(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        line = self._coerce_int(args.get("line", 0), default=0, minimum=0)
        char = self._coerce_int(args.get("character", 0), default=0, minimum=0)
        if not file_rel:
            raise ToolError("lsp_completion requer 'file'.")
        result = self._lsp_call("completion", file_rel, line, char)
        if result is None:
            return json.dumps({"error": "LSP server not running"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _lsp_code_actions(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        if not file_rel:
            raise ToolError("lsp_code_actions requer 'file'.")
        start_line = self._coerce_int(args.get("start_line", 0), default=0, minimum=0)
        start_char = self._coerce_int(args.get("start_char", 0), default=0, minimum=0)
        end_line = self._coerce_int(args.get("end_line", start_line), default=start_line, minimum=0)
        end_char = self._coerce_int(args.get("end_char", start_char), default=start_char, minimum=0)
        result = self._lsp_call(
            "code_actions", file_rel, start_line, start_char,
            end_line=end_line, end_char=end_char,
        )
        if result is None:
            return json.dumps({"error": "LSP server not running"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _lsp_format(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        if not file_rel:
            raise ToolError("lsp_format requer 'file'.")
        tab_size = self._coerce_int(args.get("tab_size", 4), default=4, minimum=1)
        insert_spaces = bool(args.get("insert_spaces", True))
        result = self._lsp_call(
            "format_document", file_rel, 0, 0,
            tab_size=tab_size, insert_spaces=insert_spaces,
        )
        if result is None:
            server_hint = "pyright" if file_rel.endswith(".py") else "typescript-language-server"
            return json.dumps({"error": "LSP server not running", "hint": f"pip/npm install {server_hint}"})
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _lsp_rename(self, args: dict) -> str:
        file_rel = str(args.get("file", "")).strip()
        new_name = str(args.get("new_name", "")).strip()
        if not file_rel:
            raise ToolError("lsp_rename requer 'file'.")
        if not new_name:
            raise ToolError("lsp_rename requer 'new_name'.")
        line = self._coerce_int(args.get("line", 0), default=0, minimum=0)
        char = self._coerce_int(args.get("character", 0), default=0, minimum=0)
        result = self._lsp_call(
            "rename_symbol", file_rel, line, char,
            new_name=new_name,
        )
        if result is None:
            return json.dumps({"error": "LSP server not running"})
        return json.dumps(result, indent=2, ensure_ascii=False)
