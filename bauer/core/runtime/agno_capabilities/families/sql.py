"""Sprint 52 — SQL somente leitura por conexões nomeadas (plano 057, sprint "20").

``bauer.sql`` runs queries only through connections an administrator named in
``agno_capabilities.yaml``. Read-only is enforced in **two independent
layers**, so a parser gap alone cannot write:

1. statement guard — one statement, ``SELECT``/``WITH`` only, no mutating or
   session-altering keyword anywhere (comments and string literals stripped
   first), optional schema/table allowlist on every ``FROM``/``JOIN``;
2. driver guard — the connection itself is read-only: SQLite opens the file
   with ``mode=ro`` + ``PRAGMA query_only``; PostgreSQL/MySQL run inside a
   ``READ ONLY`` transaction with a server-side statement timeout, and the
   operator must declare that the credential is a read-only database user.

Dialects without a verifiable read-only mode are refused (plan STOP rule).
Every transaction is rolled back. Queries are logged only as a fingerprint.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .....tools.base import ToolError
from ..bridge import ActionImpl, register_factory
from ..credentials import resolve_credential, valid_credential_name
from ..manifest import ActionPolicy, ToolkitPolicy, register_policy
from ..settings import StrictSettings, register_settings_model

logger = logging.getLogger("bauer.agno_capabilities.sql")

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)?$")
_FORBIDDEN = frozenset(
    {
        "insert", "update", "delete", "merge", "upsert", "replace", "create", "drop", "alter",
        "truncate", "rename", "grant", "revoke", "attach", "detach", "pragma", "vacuum", "analyze",
        "reindex", "copy", "call", "exec", "execute", "do", "lock", "unlock", "set", "reset",
        "into", "load", "import", "export", "begin", "commit", "rollback", "savepoint", "release",
        "prepare", "deallocate", "listen", "notify", "refresh", "cluster", "comment", "security",
        "handler", "outfile", "dumpfile", "shutdown", "kill",
    }
)
_FUNCTION_DENYLIST = frozenset(
    {"pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "lo_import", "lo_export",
     "dblink", "load_extension", "readfile", "writefile", "sleep", "benchmark", "load_file"}
)


class SqlConnectionSettings(StrictSettings):
    dialect: Literal["sqlite", "postgresql", "mysql"]
    #: SQLite only: database file path (no secret).
    path: str | None = None
    #: Server dialects: credential *name* whose value is the SQLAlchemy URL.
    url_credential: str | None = None
    #: Operator attests the credential is a dedicated read-only DB user.
    credential_is_read_only: bool = False
    schemas: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    max_rows: int = Field(default=500, ge=1, le=10_000)
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    max_cell_chars: int = Field(default=2_000, ge=50, le=50_000)

    @field_validator("schemas", "tables")
    @classmethod
    def _identifiers(cls, value: list[str]) -> list[str]:
        for item in value:
            if not _IDENT.match(item):
                raise ValueError("identificador inválido")
        return sorted({item.lower() for item in value})

    @model_validator(mode="after")
    def _connection(self) -> "SqlConnectionSettings":
        if self.dialect == "sqlite":
            if not self.path:
                raise ValueError("sqlite exige path")
        else:
            if not self.url_credential or not valid_credential_name(self.url_credential):
                raise ValueError("dialeto de servidor exige url_credential (nome)")
            if not self.credential_is_read_only:
                raise ValueError(
                    "credential_is_read_only deve ser true: use um usuário de banco somente leitura"
                )
        return self


class SqlSettings(StrictSettings):
    connections: dict[str, SqlConnectionSettings] = Field(min_length=1)

    @field_validator("connections")
    @classmethod
    def _names(cls, value: dict[str, SqlConnectionSettings]) -> dict[str, SqlConnectionSettings]:
        for name in value:
            if not re.match(r"^[a-z][a-z0-9_]{0,40}$", name):
                raise ValueError("nome de conexão deve ser [a-z][a-z0-9_]*")
        return value


# ── statement guard ──────────────────────────────────────────────────────────


def _strip_literals_and_comments(sql: str) -> str:
    out: list[str] = []
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if sql.startswith("--", index) or char == "#":
            end = sql.find("\n", index)
            index = length if end < 0 else end
            out.append(" ")
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                raise ToolError("comentário SQL não fechado")
            index = end + 2
            out.append(" ")
        elif char in {"'", '"', "`"}:
            quote = char
            index += 1
            while index < length:
                if sql[index] == quote:
                    if index + 1 < length and sql[index + 1] == quote:
                        index += 2
                        continue
                    break
                index += 1
            if index >= length:
                raise ToolError("literal SQL não fechado")
            index += 1
            # Quoted identifiers keep a placeholder so FROM "x" is still seen.
            out.append(" __lit__ " if quote == "'" else " __ident__ ")
        elif sql.startswith("$$", index):
            raise ToolError("dollar-quoting não é permitido")
        else:
            out.append(char)
            index += 1
    return "".join(out)


def guard_statement(sql: str, connection: SqlConnectionSettings) -> str:
    """Return the normalized statement or raise ``ToolError`` (fail-closed)."""
    text = str(sql or "").strip()
    if not text:
        raise ToolError("consulta vazia")
    if len(text) > 20_000:
        raise ToolError("consulta grande demais")
    bare = _strip_literals_and_comments(text).strip()
    if bare.endswith(";"):
        bare = bare[:-1].rstrip()
    if ";" in bare:
        raise ToolError("apenas uma instrução por consulta")
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", bare.lower())
    if not tokens or tokens[0] not in {"select", "with"}:
        raise ToolError("somente SELECT/WITH são permitidos")
    forbidden = sorted(set(tokens) & _FORBIDDEN)
    if forbidden:
        raise ToolError(f"palavra-chave não permitida em modo leitura: {forbidden[0]}")
    functions = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", bare.lower()))
    blocked = sorted(functions & _FUNCTION_DENYLIST)
    if blocked:
        raise ToolError(f"função não permitida: {blocked[0]}")
    if connection.tables or connection.schemas:
        _check_tables(bare, connection)
    return text.rstrip().rstrip(";")


def _check_tables(bare: str, connection: SqlConnectionSettings) -> None:
    lowered = bare.lower()
    cte_names = set(
        re.findall(r"(?:with|,)\s*(?:recursive\s+)?([a-z_][a-z0-9_]*)\s*(?:\([^()]*\))?\s*as\s*\(", lowered)
    )
    for match in re.finditer(r"\b(?:from|join)\s+([^\s,()]+)", lowered):
        target = match.group(1)
        if target in {"__ident__"}:
            raise ToolError("identificadores entre aspas não são aceitos com allowlist de tabelas")
        if target in cte_names or target == "(":
            continue
        if not _IDENT.match(target):
            raise ToolError("alvo de FROM/JOIN não reconhecido")
        schema, _, table = target.rpartition(".")
        if connection.schemas and schema and schema not in connection.schemas:
            raise ToolError("schema fora da allowlist")
        if connection.tables and target not in connection.tables and table not in connection.tables:
            raise ToolError("tabela fora da allowlist")
    # Comma joins: FROM a, b
    for match in re.finditer(r"\bfrom\s+([a-z_][a-z0-9_.$]*(?:\s+(?:as\s+)?[a-z_][a-z0-9_]*)?(?:\s*,\s*[a-z_][a-z0-9_.$]*(?:\s+(?:as\s+)?[a-z_][a-z0-9_]*)?)+)", lowered):
        for part in match.group(1).split(",")[1:]:
            name = part.strip().split()[0]
            if name in cte_names:
                continue
            schema, _, table = name.rpartition(".")
            if connection.tables and name not in connection.tables and table not in connection.tables:
                raise ToolError("tabela fora da allowlist")


def fingerprint(sql: str) -> str:
    normalized = re.sub(r"\s+", " ", _strip_literals_and_comments(sql)).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ── driver guard ─────────────────────────────────────────────────────────────


def _engine(connection: SqlConnectionSettings):
    from sqlalchemy import create_engine, event

    if connection.dialect == "sqlite":
        path = Path(str(connection.path)).expanduser().resolve()
        if not path.is_file():
            raise ToolError("arquivo SQLite não encontrado")
        from sqlalchemy.pool import NullPool

        engine = create_engine(
            f"sqlite+pysqlite:///file:{path.as_posix()}?mode=ro&uri=true",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )

        @event.listens_for(engine, "connect")
        def _readonly(dbapi_connection, _record):  # pragma: no cover - exercised via queries
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA query_only = ON")
            cursor.close()

        return engine
    url = resolve_credential(str(connection.url_credential))
    if not url:
        raise ToolError("credencial da conexão SQL não configurada")
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    parsed = make_url(url)
    if not parsed.drivername.startswith(connection.dialect):
        raise ToolError("URL da credencial não corresponde ao dialeto declarado")
    return create_engine(parsed, poolclass=NullPool)


def _run(connection: SqlConnectionSettings, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, SQLAlchemyError

    engine = _engine(connection)
    started = time.monotonic()
    deadline = started + connection.timeout_seconds
    try:
        with engine.connect() as conn:
            raw = conn.connection.dbapi_connection
            if connection.dialect == "sqlite":
                raw.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1_000)
            elif connection.dialect == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(connection.timeout_seconds * 1000)}")
            elif connection.dialect == "mysql":
                conn.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
                conn.exec_driver_sql(f"SET SESSION max_execution_time = {int(connection.timeout_seconds * 1000)}")
            try:
                result = conn.execute(text(sql), params or {})
                columns = list(result.keys()) if result.returns_rows else []
                rows = result.fetchmany(connection.max_rows + 1) if result.returns_rows else []
            finally:
                conn.rollback()
    except DBAPIError as exc:
        elapsed = time.monotonic() - started
        if elapsed >= connection.timeout_seconds or "interrupt" in str(exc.orig).lower():
            raise ToolError(f"consulta cancelada: excedeu {connection.timeout_seconds:g}s") from None
        raise ToolError(f"banco recusou a consulta ({type(exc.orig).__name__})") from None
    except SQLAlchemyError as exc:
        raise ToolError(f"falha SQL ({type(exc).__name__})") from None
    finally:
        engine.dispose()
    truncated = len(rows) > connection.max_rows
    rows = rows[: connection.max_rows]

    def cell(value: Any) -> Any:
        if isinstance(value, (bytes, bytearray)):
            return f"<{len(value)} bytes>"
        if isinstance(value, str) and len(value) > connection.max_cell_chars:
            return value[: connection.max_cell_chars] + "…"
        if value is None or isinstance(value, (int, float, bool, str)):
            return value
        return str(value)

    return {
        "columns": columns,
        "rows": [[cell(value) for value in row] for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }


def _actions(settings: SqlSettings) -> dict[str, Any]:
    def _connection(name: str) -> SqlConnectionSettings:
        connection = settings.connections.get(str(name or ""))
        if connection is None:
            raise ToolError(
                f"conexão desconhecida; disponíveis: {', '.join(sorted(settings.connections))}"
            )
        return connection

    def run_query(connection: str, sql: str) -> dict[str, Any]:
        """Executa uma consulta SELECT somente leitura numa conexão nomeada.

        Args:
            connection: Nome da conexão configurada pelo administrador.
            sql: Uma única instrução SELECT/WITH.
        """
        conn = _connection(connection)
        statement = guard_statement(sql, conn)
        logger.info("sql query connection=%s fingerprint=%s", connection, fingerprint(statement))
        result = _run(conn, statement)
        result["fingerprint"] = fingerprint(statement)
        return result

    def list_tables(connection: str) -> dict[str, Any]:
        """Lista tabelas visíveis (respeitando a allowlist) de uma conexão nomeada.

        Args:
            connection: Nome da conexão configurada pelo administrador.
        """
        conn = _connection(connection)
        if conn.dialect == "sqlite":
            sql = "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
        else:
            sql = (
                "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "ORDER BY 1"
            ) if conn.dialect == "postgresql" else (
                "SELECT CONCAT(table_schema, '.', table_name) FROM information_schema.tables ORDER BY 1"
            )
        names = [row[0] for row in _run(conn, sql)["rows"]]
        if conn.tables or conn.schemas:
            names = [
                name for name in names
                if (not conn.tables or name.lower() in conn.tables or name.lower().rpartition(".")[2] in conn.tables)
                and (not conn.schemas or "." not in name or name.lower().partition(".")[0] in conn.schemas)
            ]
        return {"tables": names}

    def describe_table(connection: str, table: str) -> dict[str, Any]:
        """Descreve colunas de uma tabela permitida.

        Args:
            connection: Nome da conexão configurada pelo administrador.
            table: Nome da tabela (schema.tabela quando aplicável).
        """
        conn = _connection(connection)
        if not _IDENT.match(str(table or "")):
            raise ToolError("nome de tabela inválido")
        guard_statement(f"SELECT * FROM {table}", conn)  # allowlist check
        from sqlalchemy import inspect

        engine = _engine(conn)
        try:
            schema, _, name = str(table).rpartition(".")
            columns = inspect(engine).get_columns(name, schema=schema or None)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"não foi possível descrever a tabela ({type(exc).__name__})") from None
        finally:
            engine.dispose()
        return {"table": table, "columns": [{"name": c["name"], "type": str(c["type"])} for c in columns]}

    return {"run_query": run_query, "list_tables": list_tables, "describe_table": describe_table}


def _factory(resolved, section, context=None) -> dict[str, ActionImpl]:
    from ..bridge import _parameters_for

    if not isinstance(section, SqlSettings):
        raise ToolError("bauer.sql sem configuração")
    actions = _actions(section)
    timeout = int(max(c.timeout_seconds for c in section.connections.values())) + 10
    return {
        action.name: ActionImpl(
            fn=actions[action.name],
            description=(actions[action.name].__doc__ or "").strip().split("\n\n")[0],
            parameters=_parameters_for(actions[action.name]),
            timeout_seconds=timeout,
        )
        for action in resolved.actions
    }


register_settings_model("bauer.sql", SqlSettings)
register_policy(
    ToolkitPolicy(
        id="bauer.sql",
        title="SQL somente leitura",
        category="Dados",
        description="Consultas SELECT em conexões nomeadas pelo administrador, com modo read-only no driver.",
        source="bauer",
        actions=(
            ActionPolicy("run_query", "read", "data.query"),
            ActionPolicy("list_tables", "read", "data.query"),
            ActionPolicy("describe_table", "read", "data.query"),
        ),
        requires=("sqlalchemy",),
        install_hint="uv pip install 'bauer-agent[agno]'",
        settings_section="bauer.sql",
        notes="SQLite: mode=ro + query_only. PostgreSQL/MySQL: transação READ ONLY + timeout no servidor e usuário read-only declarado.",
    )
)
register_factory("bauer.sql", _factory)

for _capability_id, _title in (
    ("agno.sql", "SQLTools"),
    ("agno.postgres", "PostgresTools"),
    ("agno.duckdb", "DuckDbTools"),
    ("agno.redshift", "RedshiftTools"),
    ("agno.neo4j", "Neo4jTools"),
    ("agno.google.bigquery", "BigQuery"),
):
    register_policy(
        ToolkitPolicy(
            id=_capability_id,
            title=_title,
            category="Dados",
            description="Toolkit SQL/consulta do SDK Agno.",
            incompatible=(
                "Executa SQL arbitrário sem modo read-only verificável, allowlist de tabelas "
                "ou cancelamento por timeout. Use bauer.sql (conexões nomeadas, read-only no driver)."
            ),
        )
    )
