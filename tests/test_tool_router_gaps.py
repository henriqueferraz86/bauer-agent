"""Testes extras para ToolRouter — cobre linhas 121, 177-178, 216, 241-244, 290-291, 328, 338-378, 381-421."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.tool_router import SandboxError, ToolError, ToolRouter


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "texto.txt").write_text("hello world\nsegunda linha", encoding="utf-8")
    return workspace


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws)


# ─── tool_allowlist (toolset enxuto) ──────────────────────────────────────────


def test_tool_allowlist_restringe_available_tools(ws: Path):
    """Com allowlist, available_tools() expõe SÓ as tools listadas."""
    r = ToolRouter(workspace=ws, tool_allowlist=["read_file", "list_dir", "calculate"])
    tools = set(r.available_tools())
    assert tools == {"read_file", "list_dir", "calculate"}
    assert "web_search" not in tools
    assert "delegate_task" not in tools


def test_tool_allowlist_encolhe_schemas(ws: Path):
    """get_tool_schemas() (schema OpenAI native) respeita o allowlist."""
    r = ToolRouter(workspace=ws, tool_allowlist=["read_file", "list_dir"])
    names = {s["function"]["name"] for s in r.get_tool_schemas()}
    assert names == {"read_file", "list_dir"}


def test_tool_allowlist_bloqueia_execucao_fora_da_lista(ws: Path):
    """Tool fora do allowlist não executa (denied), mesmo se o modelo tentar."""
    r = ToolRouter(workspace=ws, tool_allowlist=["list_dir"])
    with pytest.raises(ToolError, match="denied|nao permitido|não permitido"):
        r.execute('{"action": "read_file", "args": {"path": "texto.txt"}}')


def test_tool_allowlist_vazio_mantem_todas(ws: Path):
    """Allowlist vazio (default) = todas as tools disponíveis (sem regressão)."""
    r_none = ToolRouter(workspace=ws)
    r_empty = ToolRouter(workspace=ws, tool_allowlist=[])
    assert "web_fetch" not in r_none.available_tools()  # web_enabled=False por padrão
    assert len(r_empty.available_tools()) == len(r_none.available_tools())
    assert "list_dir" in r_empty.available_tools()


# ─── tool_info ────────────────────────────────────────────────────────────────


def test_tool_info_known_tool(router: ToolRouter):
    info = router.tool_info("list_dir")
    assert info["name"] == "list_dir"
    assert "description" in info
    assert "args" in info


def test_tool_info_unknown_tool_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="desconhecida"):
        router.tool_info("nao_existe")


# ─── _parse: JSON não-dict ────────────────────────────────────────────────────


def test_parse_json_list_raises(router: ToolRouter):
    """JSON que é lista (não dict) deve levantar ToolError."""
    import json
    with pytest.raises(ToolError):
        router.execute(json.dumps([1, 2, 3]))


def test_parse_markdown_block(router: ToolRouter):
    """JSON em bloco markdown deve ser extraído e executado."""
    payload = '```json\n{"action": "list_dir", "args": {"path": "."}}\n```'
    result = router.execute(payload)
    assert isinstance(result, str)
    assert "texto.txt" in result


# ─── _sandbox: path traversal ────────────────────────────────────────────────


def test_sandbox_traversal_blocked(router: ToolRouter):
    with pytest.raises(SandboxError):
        router._sandbox("../../etc/passwd")


def test_sandbox_absolute_workspace_prefix(router: ToolRouter, ws: Path):
    """Path absoluto com nome do workspace é normalizado."""
    ws_name = ws.name
    resolved = router._sandbox(f"{ws_name}/texto.txt")
    assert resolved.name == "texto.txt"


# ─── execute: action vazia e args não-dict ────────────────────────────────────


def test_execute_empty_action_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="ausente"):
        router.execute({"action": "", "args": {}})


def test_execute_args_not_dict_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="args"):
        router.execute({"action": "list_dir", "args": "nao-dict"})


# ─── _read_file: binário ─────────────────────────────────────────────────────


def test_read_file_binary_raises(router: ToolRouter, ws: Path):
    (ws / "binario.bin").write_bytes(bytes(range(256)))
    with pytest.raises(ToolError, match="binario"):
        router.execute({"action": "read_file", "args": {"path": "binario.bin"}})


def test_read_file_directory_raises(router: ToolRouter, ws: Path):
    (ws / "subdir").mkdir(exist_ok=True)
    with pytest.raises(ToolError, match="diretorio"):
        router.execute({"action": "read_file", "args": {"path": "subdir"}})


# ─── _write_file: erros ───────────────────────────────────────────────────────


def test_write_file_no_path_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="path"):
        router.execute({"action": "write_file", "args": {"content": "x"}})


def test_write_file_no_content_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="content"):
        router.execute({"action": "write_file", "args": {"path": "x.txt"}})


def test_write_file_invalid_overwrite_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="overwrite"):
        router.execute({"action": "write_file", "args": {"path": "x.txt", "content": "y", "overwrite": "yes"}})


def test_write_file_no_overwrite_existing_raises(router: ToolRouter, ws: Path):
    (ws / "exists.txt").write_text("original", encoding="utf-8")
    with pytest.raises(ToolError, match="ja existe"):
        router.execute({"action": "write_file", "args": {"path": "exists.txt", "content": "novo", "overwrite": False}})


# ─── _search_text: erros e casos borda ──────────────────────────────────────


def test_search_text_no_pattern_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="pattern"):
        router.execute({"action": "search_text", "args": {"path": "."}})


def test_search_text_relative_path(router: ToolRouter, ws: Path):
    # Resultado usando caminho relativo ao arquivo
    result = router.execute({"action": "search_text", "args": {"path": "texto.txt", "pattern": "hello"}})
    assert "hello" in result


# ─── run_command ──────────────────────────────────────────────────────────────


def test_run_command_no_command_raises(ws: Path):
    shell = MagicMock()
    router = ToolRouter(workspace=ws, shell_runner=shell)
    with pytest.raises(ToolError, match="command"):
        router.execute({"action": "run_command", "args": {}})


def test_run_command_invalid_confirm_raises(ws: Path):
    shell = MagicMock()
    router = ToolRouter(workspace=ws, shell_runner=shell)
    with pytest.raises(ToolError, match="confirm"):
        router.execute({"action": "run_command", "args": {"command": "ls", "confirm": "sim"}})


def test_run_command_shell_error(ws: Path):
    from bauer.shell_runner import ShellError
    shell = MagicMock()
    shell.run.side_effect = ShellError("falha")
    router = ToolRouter(workspace=ws, shell_runner=shell)
    with pytest.raises(ToolError, match="falha"):
        router.execute({"action": "run_command", "args": {"command": "ls", "confirm": True}})


def test_run_command_success_with_stdout(ws: Path):
    shell = MagicMock()
    result_mock = MagicMock()
    result_mock.command = ["ls", "-la"]
    result_mock.returncode = 0
    result_mock.elapsed_ms = 42
    result_mock.stdout = "arquivo.txt"
    result_mock.stderr = ""
    result_mock.truncated = False
    shell.run.return_value = result_mock

    router = ToolRouter(workspace=ws, shell_runner=shell)
    result = router.execute({"action": "run_command", "args": {"command": "ls", "confirm": True}})
    assert "arquivo.txt" in result
    assert "exit: 0" in result


def test_run_command_truncated_output(ws: Path):
    shell = MagicMock()
    result_mock = MagicMock()
    result_mock.command = ["echo", "x"]
    result_mock.returncode = 0
    result_mock.elapsed_ms = 1
    result_mock.stdout = "x"
    result_mock.stderr = "err"
    result_mock.truncated = True
    shell.max_output_bytes = 1024
    shell.run.return_value = result_mock

    router = ToolRouter(workspace=ws, shell_runner=shell)
    result = router.execute({"action": "run_command", "args": {"command": "echo x", "confirm": True}})
    assert "truncada" in result or "truncado" in result


# ─── web tools ────────────────────────────────────────────────────────────────


def test_web_search_no_query_raises(ws: Path):
    router = ToolRouter(workspace=ws, web_enabled=True)
    with pytest.raises(ToolError, match="query"):
        router.execute({"action": "web_search", "args": {}})


def test_web_search_malformed_max_results_does_not_crash(ws: Path):
    """Regressão de bug real reportado pelo usuário: um modelo fraco (free tier)
    emitiu args mal-formados misturando sintaxe de outro protocolo de tool call
    (`<parameter=10>\\nmax_results`) — o `int()` sem guarda em max_results
    estourava ValueError não-capturado, derrubando a sessão inteira do
    `bauer agent` com "Erro inesperado". Confirma que valores não-numéricos
    caem no default (5) via _coerce_int em vez de propagar a exceção, e que
    o max_results NUMÉRICO correto (5) chega até o backend de busca."""
    router = ToolRouter(workspace=ws, web_enabled=True)
    garbled = "10>\n</parameter\n<parameter=10>\nmax_results"
    with patch.object(router._web, "search_as_text", return_value="ok") as mock_search:
        result = router._web_search({"query": "Brazil next match", "max_results": garbled})
    assert result == "ok"
    mock_search.assert_called_once_with("Brazil next match", max_results=5)


def test_web_fetch_malformed_max_chars_does_not_crash(ws: Path):
    router = ToolRouter(workspace=ws, web_enabled=True)
    from bauer.web.dispatcher import WebError
    with patch.object(router._web, "extract", side_effect=WebError("mock")):
        with pytest.raises(ToolError):
            # Levanta ToolError (do WebError mockado), não ValueError do int().
            router._web_fetch({"url": "https://example.com", "max_chars": "abc"})


def test_web_search_sem_ddgs_cai_em_wikipedia(ws: Path):
    """Novo contrato (G18.3): sem ddgs/brave/searxng, web_search NAO falha —
    cai no fallback open-source Wikipedia (zero setup, sem chave)."""
    from unittest.mock import MagicMock
    router = ToolRouter(workspace=ws, web_enabled=True)
    import os
    old_brave = os.environ.pop("BRAVE_API_KEY", None)
    old_searxng = os.environ.pop("SEARXNG_URL", None)

    mock_response = MagicMock()
    mock_response.json.return_value = {"query": {"search": [
        {"title": "Python (programming language)", "snippet": "linguagem"}
    ]}}
    mock_response.raise_for_status = MagicMock()
    try:
        with patch("bauer.web.dispatcher._package_available", return_value=False), \
             patch("httpx.Client.get", return_value=mock_response):
            out = router._web_search({"query": "python", "max_results": 3})
        assert "wikipedia" in out.lower()
        assert "Python" in out
    finally:
        if old_brave is not None:
            os.environ["BRAVE_API_KEY"] = old_brave
        if old_searxng is not None:
            os.environ["SEARXNG_URL"] = old_searxng


def test_web_fetch_no_url_raises(ws: Path):
    router = ToolRouter(workspace=ws, web_enabled=True)
    with pytest.raises(ToolError, match="url"):
        router.execute({"action": "web_fetch", "args": {}})


def test_web_fetch_invalid_scheme_raises(ws: Path):
    router = ToolRouter(workspace=ws, web_enabled=True)
    with pytest.raises(ToolError, match="http"):
        router.execute({"action": "web_fetch", "args": {"url": "ftp://example.com"}})


def test_web_fetch_timeout(ws: Path):
    import httpx
    router = ToolRouter(workspace=ws, web_enabled=True)
    with patch("httpx.Client.get", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(ToolError, match="Timeout"):
            router._web_fetch({"url": "https://example.com"})


def test_web_fetch_http_status_error(ws: Path):
    import httpx
    router = ToolRouter(workspace=ws, web_enabled=True)
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("httpx.Client.get", side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=mock_resp)):
        with pytest.raises(ToolError, match="404"):
            router._web_fetch({"url": "https://example.com"})


def test_web_fetch_generic_exception(ws: Path):
    router = ToolRouter(workspace=ws, web_enabled=True)
    with patch("httpx.Client.get", side_effect=RuntimeError("rede falhou")):
        with pytest.raises(ToolError, match="rede falhou"):
            router._web_fetch({"url": "https://example.com"})


def test_web_fetch_binary_content_type(ws: Path):
    router = ToolRouter(workspace=ws, web_enabled=True)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.headers = {"content-type": "application/octet-stream"}
    with patch("httpx.Client.get", return_value=mock_resp):
        result = router._web_fetch({"url": "https://example.com"})
    assert "content-type" in result.lower() or "bin" in result.lower() or "ignorado" in result.lower()


def test_web_fetch_returns_text(ws: Path):
    router = ToolRouter(workspace=ws, web_enabled=True)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.headers = {"content-type": "text/html"}
    mock_resp.text = "Hello World linha 1\nHello World linha 2"
    # Simula falha ao importar bs4 dentro do _web_fetch
    with patch("httpx.Client.get", return_value=mock_resp):
        # Faz o except Exception do BeautifulSoup cair no fallback resp.text
        with patch("bauer.tool_router.ToolRouter._web_fetch", wraps=router._web_fetch) as _:
            # Injeta exceção no import de bs4 via builtins
            import builtins
            real_import = builtins.__import__
            def mock_import(name, *args, **kwargs):
                if name == "bs4":
                    raise ImportError("bs4 not found")
                return real_import(name, *args, **kwargs)
            with patch("builtins.__import__", side_effect=mock_import):
                result = router._web_fetch({"url": "https://example.com"})
    assert isinstance(result, str)


def test_web_fetch_truncates_long_content(ws: Path):
    router = ToolRouter(workspace=ws, web_enabled=True)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.headers = {"content-type": "text/plain"}
    mock_resp.text = "x" * 10000
    with patch("httpx.Client.get", return_value=mock_resp):
        result = router._web_fetch({"url": "https://example.com", "max_chars": 100})
    assert "truncado" in result
