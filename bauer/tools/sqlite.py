"""Tool sqlite_query: consulta SOMENTE LEITURA a arquivos SQLite.

Existe porque `sqlite3` vem na biblioteca padrao do Python — degrau 3 da escada
de codigo minimo do proprio Bauer. A alternativa que eu havia recomendado antes
era um servidor MCP de 295 MB (binario Go), que no Windows com Smart App Control
nem executa: `[WinError 4551] politica de Controle de Aplicativo bloqueou`.
Servidor MCP de banco continua fazendo sentido para Postgres/MySQL, onde um
driver e inevitavel. Para SQLite era so nao ter subido a escada.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from .base import SandboxError, ToolError

#: Teto de linhas devolvidas quando o chamador nao passa `limit`.
_DEFAULT_ROWS = 50
#: Teto absoluto — resultado de query vai inteiro para o contexto do modelo.
_MAX_ROWS = 500
#: Teto de chars da resposta formatada, pelo mesmo motivo.
_MAX_CHARS = 20_000


class SqliteToolsMixin:

    if TYPE_CHECKING:
        # Vem do host (ToolRouter/FsToolsMixin). Declarado aqui para o mixin
        # fechar no mypy sozinho — `bauer.tools.sqlite` NAO entra no registro de
        # divida de tipagem do pyproject, que so pode encolher.
        workspace: Path

        @staticmethod
        def _coerce_int(value: object, default: int, minimum: int) -> int: ...

    def _sqlite_query(self, args: dict) -> str:
        """Roda SELECT em um arquivo SQLite, em conexao read-only.

        Duas travas, porque nenhuma sozinha basta:

        1. `file:...?mode=ro` — o proprio SQLite recusa escrita
           ("attempt to write a readonly database"), entao nao depende de o
           modelo se comportar nem de parsear SQL para achar UPDATE disfarçado.
        2. Containment de path — workspace ou BAUER_HOME, nada mais. Sem isto a
           tool viraria um jeito de ler QUALQUER arquivo .db da maquina
           (historico e cookies de navegador sao SQLite), contornando o
           containment que o read_file impoe.
        """
        path = str(args.get("path", "") or "").strip()
        sql = str(args.get("sql", "") or "").strip()
        if not path:
            raise ToolError("sqlite_query requer 'path' (arquivo .db/.sqlite).")
        if not sql:
            raise ToolError("sqlite_query requer 'sql' (uma consulta SELECT).")

        params = args.get("params", [])
        if isinstance(params, (str, int, float)):
            params = [params]
        if not isinstance(params, (list, tuple)):
            raise ToolError("sqlite_query: 'params' deve ser uma lista.")

        limit = self._coerce_int(args.get("limit", _DEFAULT_ROWS), default=_DEFAULT_ROWS, minimum=1)
        limit = min(limit, _MAX_ROWS)

        db = self._sqlite_safe_path(path)

        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
        except sqlite3.OperationalError as exc:
            raise ToolError(
                f"sqlite_query: nao foi possivel abrir '{db}' para leitura: {exc}"
            ) from exc

        try:
            con.row_factory = sqlite3.Row
            try:
                cur = con.execute(sql, tuple(params))
            except sqlite3.OperationalError as exc:
                if "readonly" in str(exc).lower():
                    raise ToolError(
                        "sqlite_query e SOMENTE LEITURA (conexao mode=ro). "
                        "Para escrever, use run_command com o sqlite3 do sistema — "
                        "que passa pela aprovacao de tool de execucao."
                    ) from exc
                raise ToolError(f"sqlite_query: erro de SQL: {exc}") from exc
            except sqlite3.Error as exc:
                raise ToolError(f"sqlite_query: erro de SQL: {exc}") from exc

            rows = cur.fetchmany(limit)
            if cur.description is None:
                return "[sqlite_query] Comando executado sem resultado (nenhuma coluna devolvida)."
            cols = [d[0] for d in cur.description]
        finally:
            con.close()

        if not rows:
            return f"[sqlite_query] {db.name}: nenhuma linha."

        linhas = [" | ".join(cols), "-" * min(len(" | ".join(cols)), 80)]
        for row in rows:
            linhas.append(" | ".join("" if v is None else str(v) for v in tuple(row)))

        out = "\n".join(linhas)
        truncado = ""
        if len(out) > _MAX_CHARS:
            out = out[:_MAX_CHARS]
            truncado = f"\n... (resposta cortada em {_MAX_CHARS} chars)"
        if len(rows) == limit:
            truncado += f"\n... (parou em {limit} linhas; use 'limit' ou refine o SELECT)"
        return f"[sqlite_query] {db.name} — {len(rows)} linha(s)\n{out}{truncado}"

    def _sqlite_safe_path(self, path: str) -> Path:
        """Resolve o caminho do banco, aceitando workspace OU BAUER_HOME.

        BAUER_HOME entra porque o caso de uso mais imediato sao os bancos do
        proprio Bauer (sessions, event_bus, kanban, decisions), que ficam fora
        do workspace. Qualquer outro lugar e recusado.
        """
        try:
            alvo = Path(path).expanduser()
            alvo = alvo.resolve() if alvo.is_absolute() else (self.workspace / alvo).resolve()
        except Exception as exc:
            raise SandboxError(f"sqlite_query: path invalido '{path}': {exc}") from exc

        permitidos = [self.workspace.resolve()]
        try:
            from ..paths import get_bauer_home
            permitidos.append(Path(get_bauer_home()).resolve())
        except Exception as exc:
            from ..logging_config import log_suppressed

            # Sem BAUER_HOME resolvivel, sobra o workspace — restringe, nao afrouxa.
            log_suppressed("sqlite_query.bauer_home", exc)

        for raiz in permitidos:
            try:
                alvo.relative_to(raiz)
                break
            except ValueError:
                continue
        else:
            raise SandboxError(
                f"sqlite_query: acesso negado a '{alvo}'.\n"
                f"Permitido apenas dentro do workspace ({permitidos[0]}) "
                f"ou do diretorio do Bauer"
                + (f" ({permitidos[1]})" if len(permitidos) > 1 else "")
                + ".\nEssa restricao existe porque muito programa guarda dados "
                "sensiveis em SQLite (historico e cookies de navegador, por exemplo)."
            )

        if not alvo.exists():
            raise ToolError(f"sqlite_query: arquivo nao encontrado: {alvo}")
        if not alvo.is_file():
            raise ToolError(f"sqlite_query: '{alvo}' nao e um arquivo.")
        return alvo
