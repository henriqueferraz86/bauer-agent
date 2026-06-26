"""Tools de utilidade pura: calculate, datetime_now, json_query, encode_decode.

Mixin herdado por ToolRouter. Os métodos usam `self._sandbox` (provido pela
classe base) e as exceções de `.base`. Nenhum estado próprio — apenas lógica.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone

from .base import SandboxError, ToolError


class UtilityToolsMixin:
    """Ferramentas determinísticas sem efeito colateral nem rede."""

    def _calculate(self, args: dict) -> str:
        expression = args.get("expression")
        if not expression:
            raise ToolError("calculate requer 'expression'.")

        # Avaliação segura: converte para AST e avalia apenas nós permitidos
        _SAFE_FUNCS = {
            "abs": abs, "round": round, "min": min, "max": max,
            "sum": sum, "int": int, "float": float, "pow": pow,
        }
        try:
            import math
            _SAFE_FUNCS.update({
                "sqrt": math.sqrt, "log": math.log, "log2": math.log2,
                "log10": math.log10, "ceil": math.ceil, "floor": math.floor,
                "pi": math.pi, "e": math.e, "sin": math.sin, "cos": math.cos,
                "tan": math.tan,
            })
        except ImportError:
            pass

        class _SafeEval(ast.NodeVisitor):
            ALLOWED = (
                ast.Expression, ast.BinOp, ast.UnaryOp, ast.Num, ast.Constant,
                ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
                ast.FloorDiv, ast.USub, ast.UAdd, ast.Call, ast.Name,
                ast.Load, ast.List, ast.Tuple,
            )
            def generic_visit(self, node):
                if not isinstance(node, self.ALLOWED):
                    raise ToolError(f"Operacao nao permitida no calculo: {type(node).__name__}")
                return super().generic_visit(node)

        expr = str(expression).strip()
        try:
            tree = ast.parse(expr, mode="eval")
            _SafeEval().visit(tree)
            result = eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, _SAFE_FUNCS)  # noqa: S307
        except ToolError:
            raise
        except ZeroDivisionError:
            raise ToolError("Divisao por zero.")
        except Exception as exc:
            raise ToolError(f"Expressao invalida: {exc}") from exc

        return f"{expr} = {result}"

    def _datetime_now(self, args: dict) -> str:
        fmt = str(args.get("format", "iso")).lower()
        tz_arg = str(args.get("tz", "utc")).lower()

        if tz_arg == "utc":
            now = datetime.now(timezone.utc)
        else:
            now = datetime.now()

        if fmt == "iso":
            return now.isoformat(timespec="seconds")
        elif fmt == "br":
            return now.strftime("%d/%m/%Y %H:%M:%S")
        elif fmt == "us":
            return now.strftime("%Y-%m-%d %H:%M:%S")
        elif fmt == "timestamp":
            return str(int(now.timestamp()))
        else:
            return now.isoformat(timespec="seconds")

    def _json_query(self, args: dict) -> str:
        data_arg = args.get("data")
        query = args.get("query")
        if not data_arg:
            raise ToolError("json_query requer 'data'.")
        if not query:
            raise ToolError("json_query requer 'query'.")

        # Tenta como arquivo primeiro, depois como string JSON
        raw = None
        try:
            p = self._sandbox(str(data_arg))
            if p.exists() and p.is_file():
                raw = json.loads(p.read_text(encoding="utf-8"))
        except (SandboxError, Exception):
            pass

        if raw is None:
            try:
                raw = json.loads(str(data_arg))
            except json.JSONDecodeError as exc:
                raise ToolError(f"JSON inválido: {exc}") from exc

        # Navega pelo path: ".key.sub[0].field"
        query = query.strip()
        if query.startswith("."):
            query = query[1:]

        current = raw
        # Tokeniza: split por '.' respeitando '[n]'
        tokens: list[str] = re.split(r"\.(?![^\[]*\])", query) if query else []
        for token in tokens:
            if not token:
                continue
            # Verifica acesso de lista: nome[0]
            m = re.match(r"^(.*?)\[(\d+)\]$", token)
            if m:
                key, idx = m.group(1), int(m.group(2))
                if key:
                    if not isinstance(current, dict):
                        raise ToolError(f"Esperava objeto JSON em '{key}', encontrou {type(current).__name__}")
                    if key not in current:
                        raise ToolError(f"Chave '{key}' nao encontrada")
                    current = current[key]
                if not isinstance(current, list):
                    raise ToolError(f"Esperava lista para indice [{idx}], encontrou {type(current).__name__}")
                if idx >= len(current):
                    raise ToolError(f"Indice [{idx}] fora do range (len={len(current)})")
                current = current[idx]
            else:
                if isinstance(current, dict):
                    if token not in current:
                        raise ToolError(f"Chave '{token}' nao encontrada. Chaves disponíveis: {list(current.keys())[:10]}")
                    current = current[token]
                else:
                    raise ToolError(f"Esperava objeto JSON para acessar '{token}', encontrou {type(current).__name__}")

        return json.dumps(current, ensure_ascii=False, indent=2) if isinstance(current, (dict, list)) else str(current)

    def _encode_decode(self, args: dict) -> str:
        inp = args.get("input")
        operation = str(args.get("operation", "")).lower().strip()
        if inp is None:
            raise ToolError("encode_decode requer 'input'.")
        if not operation:
            raise ToolError("encode_decode requer 'operation'.")

        import base64
        import urllib.parse

        text = str(inp)
        if operation == "base64_encode":
            return base64.b64encode(text.encode()).decode()
        elif operation == "base64_decode":
            try:
                return base64.b64decode(text.encode()).decode("utf-8", errors="replace")
            except Exception as exc:
                raise ToolError(f"base64_decode falhou: {exc}") from exc
        elif operation == "url_encode":
            return urllib.parse.quote(text, safe="")
        elif operation == "url_decode":
            return urllib.parse.unquote(text)
        elif operation == "hex_encode":
            return text.encode().hex()
        elif operation == "hex_decode":
            try:
                return bytes.fromhex(text).decode("utf-8", errors="replace")
            except Exception as exc:
                raise ToolError(f"hex_decode falhou: {exc}") from exc
        else:
            raise ToolError(
                f"Operacao '{operation}' nao reconhecida. "
                "Use: base64_encode, base64_decode, url_encode, url_decode, hex_encode, hex_decode."
            )
