"""Plano 057 / sprint 52 — Bauer Data local e SQL somente leitura.

Herméticos: arquivos e banco SQLite em ``tmp_path``; nenhum servidor de
banco real. PostgreSQL/MySQL são validados só na configuração (sem rede).
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.core.runtime.agno_capabilities.catalog import catalog_entries
from bauer.core.runtime.agno_capabilities.families import sql as sql_family
from bauer.core.runtime.agno_capabilities.settings import CapabilitySettingsError, parse_settings
from bauer.tools.base import ToolError

pytest.importorskip("agno")


def _adapter(tmp_path: Path) -> AgnoRuntimeAdapter:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return AgnoRuntimeAdapter(
        adapter_config={"workspace": str(workspace), "db_file": str(tmp_path / "agno.db")},
        runtime_policy_root=tmp_path / "runtime",
    )


def _tools(adapter: AgnoRuntimeAdapter, ref: str) -> dict:
    return {fn.name: fn for fn in adapter._map_tools([ref])}


def _settings(tmp_path: Path, monkeypatch, body: str) -> None:
    target = tmp_path / "agno_capabilities.yaml"
    target.write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(target))


# ── Bauer Data ───────────────────────────────────────────────────────────────


@pytest.fixture()
def sales(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "sales.csv"
    source.write_text(
        "region,product,amount,paid\nsul,a,10,true\nsul,b,5.5,false\nnorte,a,7,true\nnorte,a,,true\n",
        encoding="utf-8",
    )
    return source


def test_data_describe_and_aggregate_local_csv(tmp_path, sales):
    tools = _tools(_adapter(tmp_path), "bauer.data")
    assert set(tools) == {"bauer_data__describe_file", "bauer_data__query_file"}  # default = read only

    described = json.loads(tools["bauer_data__describe_file"].entrypoint(path="sales.csv"))
    columns = {c["name"]: c for c in described["columns"]}
    assert described["rows_scanned"] == 4
    assert columns["amount"]["types"] == ["float", "int"] and columns["amount"]["nulls"] == 1

    grouped = json.loads(tools["bauer_data__query_file"].entrypoint(
        path="sales.csv", group_by=["region"], aggregate={"amount": "sum"}, order_by="region",
    ))
    assert grouped["rows"] == [{"region": "norte", "sum_amount": 7}, {"region": "sul", "sum_amount": 15.5}]

    filtered = json.loads(tools["bauer_data__query_file"].entrypoint(
        path="sales.csv", where=[{"column": "paid", "op": "eq", "value": True}], columns=["product"],
    ))
    assert filtered["returned"] == 3


def test_data_rejects_paths_outside_workspace_and_unknown_types(tmp_path, sales):
    (tmp_path / "secret.csv").write_text("a\n1\n", encoding="utf-8")
    (sales.parent / "notes.txt").write_text("x", encoding="utf-8")
    describe = _tools(_adapter(tmp_path), "bauer.data:describe_file")["bauer_data__describe_file"]

    with pytest.raises(ToolError, match="fora do workspace"):
        describe.entrypoint(path="../secret.csv")
    with pytest.raises(ToolError, match="fora do workspace"):
        describe.entrypoint(path=str(tmp_path / "secret.csv"))
    with pytest.raises(ToolError, match="extensão"):
        describe.entrypoint(path="notes.txt")


def test_data_budgets_truncate_scan_and_results(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "big.jsonl").write_text("".join(json.dumps({"i": i}) + "\n" for i in range(500)), encoding="utf-8")
    _settings(tmp_path, monkeypatch, """
        capabilities:
          bauer.data:
            max_rows_scanned: 100
            max_result_rows: 10
    """)
    query = _tools(_adapter(tmp_path), "bauer.data:query_file")["bauer_data__query_file"]

    result = json.loads(query.entrypoint(path="big.jsonl", order_by="i", limit=1000))
    assert result["scan_truncated"] is True
    assert result["returned"] == 10 and result["result_truncated"] is True


def test_data_artifacts_stay_in_isolated_dir_and_source_is_untouched(tmp_path, sales):
    before = sales.read_bytes()
    tools = _tools(_adapter(tmp_path), "bauer.data:save_report,save_chart")

    report = json.loads(tools["bauer_data__save_report"].entrypoint(name="../../Vendas Q1", content="# ok"))
    chart = json.loads(tools["bauer_data__save_chart"].entrypoint(name="vendas", labels=["sul", "<x>"], values=[1, 2]))

    assert report["artifact"] == ".bauer/artifacts/data/vendas-q1.md"
    svg = (sales.parent / chart["artifact"]).read_text(encoding="utf-8")
    assert "&lt;x&gt;" in svg and "<x>" not in svg
    assert sales.read_bytes() == before
    assert not (tmp_path / "Vendas Q1.md").exists()


def test_sdk_data_toolkits_are_incompatible():
    rows = {row["id"]: row for row in catalog_entries()}
    for capability_id in ("agno.pandas", "agno.csv_toolkit", "agno.visualization", "agno.sql", "agno.postgres", "agno.duckdb"):
        assert rows[capability_id]["state"] == "incompatible"


# ── SQL read-only ────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "analytics.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE orders(id INTEGER PRIMARY KEY, total REAL, customer TEXT);"
        "CREATE TABLE secrets(id INTEGER, value TEXT);"
        "INSERT INTO secrets VALUES (1, 'top');"
    )
    conn.executemany("INSERT INTO orders(total, customer) VALUES (?, ?)", [(i * 1.5, f"c{i}") for i in range(50)])
    conn.commit()
    conn.close()
    _settings(tmp_path, monkeypatch, f"""
        capabilities:
          bauer.sql:
            connections:
              analytics:
                dialect: sqlite
                path: {json.dumps(str(path))}
                tables: [orders]
                max_rows: 10
                timeout_seconds: 0.5
    """)
    return path


def test_sql_read_query_works_and_is_truncated(tmp_path, db):
    run = _tools(_adapter(tmp_path), "bauer.sql:run_query")["bauer_sql__run_query"]

    result = json.loads(run.entrypoint(connection="analytics", sql="SELECT id, total FROM orders ORDER BY id;"))
    assert result["columns"] == ["id", "total"]
    assert result["row_count"] == 10 and result["truncated"] is True
    assert len(result["fingerprint"]) == 16


@pytest.mark.parametrize(
    "statement, message",
    [
        ("DELETE FROM orders", "SELECT/WITH"),
        ("SELECT 1; DROP TABLE orders", "uma instrução"),
        ("WITH x AS (SELECT 1) INSERT INTO orders(total) SELECT 1", "insert"),
        ("SELECT * FROM secrets", "fora da allowlist"),
        ("SELECT * FROM orders JOIN secrets ON 1=1", "fora da allowlist"),
        ("SELECT * FROM orders, secrets", "fora da allowlist"),
        ("SELECT load_extension('x') FROM orders", "load_extension"),
        ("PRAGMA writable_schema=1", "SELECT/WITH"),
        ("SELECT * FROM orders /* unterminated", "não fechado"),
        ("ATTACH DATABASE 'x.db' AS x", "SELECT/WITH"),
    ],
)
def test_sql_guard_blocks_writes_ddl_and_out_of_scope(tmp_path, db, statement, message):
    run = _tools(_adapter(tmp_path), "bauer.sql:run_query")["bauer_sql__run_query"]
    with pytest.raises(ToolError, match=message):
        run.entrypoint(connection="analytics", sql=statement)


def test_sql_string_literals_cannot_hide_keywords(tmp_path, db):
    run = _tools(_adapter(tmp_path), "bauer.sql:run_query")["bauer_sql__run_query"]
    result = json.loads(run.entrypoint(connection="analytics", sql="SELECT 'DROP TABLE orders; delete' AS t FROM orders LIMIT 1"))
    assert result["rows"] == [["DROP TABLE orders; delete"]]


def test_sqlite_driver_is_read_only_even_if_guard_is_bypassed(db):
    connection = sql_family.SqlConnectionSettings(dialect="sqlite", path=str(db))
    with pytest.raises(ToolError):
        sql_family._run(connection, "INSERT INTO orders(total, customer) VALUES (1, 'x')")
    with pytest.raises(ToolError):
        sql_family._run(connection, "DROP TABLE orders")
    assert sqlite3.connect(db).execute("SELECT count(*) FROM orders").fetchone()[0] == 50


def test_sql_timeout_cancels_long_query(tmp_path, db):
    run = _tools(_adapter(tmp_path), "bauer.sql:run_query")["bauer_sql__run_query"]
    heavy = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 100000000) "
        "SELECT count(*) FROM n"
    )
    with pytest.raises(ToolError, match="cancelada"):
        run.entrypoint(connection="analytics", sql=heavy)


def test_sql_list_and_describe_respect_allowlist(tmp_path, db):
    tools = _tools(_adapter(tmp_path), "bauer.sql")
    assert json.loads(tools["bauer_sql__list_tables"].entrypoint(connection="analytics")) == {"tables": ["orders"]}
    described = json.loads(tools["bauer_sql__describe_table"].entrypoint(connection="analytics", table="orders"))
    assert [c["name"] for c in described["columns"]] == ["id", "total", "customer"]
    with pytest.raises(ToolError, match="fora da allowlist"):
        tools["bauer_sql__describe_table"].entrypoint(connection="analytics", table="secrets")
    with pytest.raises(ToolError, match="conexão desconhecida"):
        tools["bauer_sql__run_query"].entrypoint(connection="prod", sql="SELECT 1")


def test_server_dialects_require_named_read_only_credential():
    base = {"dialect": "postgresql", "url_credential": "ANALYTICS_RO_URL"}
    with pytest.raises(CapabilitySettingsError, match="campos"):
        parse_settings({"capabilities": {"bauer.sql": {"connections": {"pg": base}}}})
    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"bauer.sql": {"connections": {"pg": {**base, "url": "postgres://u:p@h/db",
                                                                               "credential_is_read_only": True}}}}})
    ok = parse_settings({"capabilities": {"bauer.sql": {"connections": {"pg": {**base, "credential_is_read_only": True}}}}})
    assert ok.section("bauer.sql").connections["pg"].dialect == "postgresql"
