"""Testes do Tool Bridge (Fase 4).

Prioridade: segurança antes de funcionalidade.
Premortem item 4: path traversal, sandbox, JSON inválido, write sem overwrite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.tool_router import SandboxError, ToolError, ToolRouter


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Workspace temporário com alguns arquivos de teste."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello world\nline two", encoding="utf-8")
    (workspace / "subdir").mkdir()
    (workspace / "subdir" / "nested.txt").write_text("nested content", encoding="utf-8")
    return workspace


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws)


# === SEGURANÇA ==============================================================


def test_sandbox_blocks_dotdot(router: ToolRouter):
    """../ fora do workspace deve levantar SandboxError."""
    with pytest.raises(SandboxError, match="Acesso negado"):
        router._sandbox("../etc/passwd")


def test_sandbox_blocks_absolute_outside(router: ToolRouter, tmp_path: Path):
    """Path absoluto fora do workspace deve ser bloqueado."""
    outside = str(tmp_path)  # pai do workspace — fora do sandbox
    with pytest.raises(SandboxError, match="Acesso negado"):
        router._sandbox(outside)


def test_sandbox_allows_dot(router: ToolRouter, ws: Path):
    """'.' deve resolver para a raiz do workspace."""
    resolved = router._sandbox(".")
    assert resolved == ws


def test_sandbox_allows_nested_subdir(router: ToolRouter, ws: Path):
    resolved = router._sandbox("subdir/nested.txt")
    assert resolved == ws / "subdir" / "nested.txt"


def test_sandbox_normalizes_workspace_prefix(router: ToolRouter, ws: Path):
    """/workspace/foo.txt (path absoluto com nome do workspace) → foo.txt relativo."""
    resolved = router._sandbox("/workspace/hello.txt")
    assert resolved == ws / "hello.txt"


def test_sandbox_normalizes_leading_slash(router: ToolRouter, ws: Path):
    """/hello.txt (slash absoluto sem workspace prefix) → hello.txt relativo."""
    resolved = router._sandbox("/hello.txt")
    assert resolved == ws / "hello.txt"


def test_sandbox_normalizes_workspace_subdir(router: ToolRouter, ws: Path):
    """/workspace/subdir/nested.txt → subdir/nested.txt relativo."""
    resolved = router._sandbox("/workspace/subdir/nested.txt")
    assert resolved == ws / "subdir" / "nested.txt"


def test_write_file_blocked_without_overwrite(router: ToolRouter):
    """Sobrescrever arquivo existente sem overwrite=true deve falhar."""
    # Cria arquivo novo
    router.execute({"action": "write_file", "args": {"path": "teste_ow.txt", "content": "primeiro"}})
    # Tenta sobrescrever sem overwrite=true — deve falhar
    with pytest.raises(ToolError, match="overwrite"):
        router.execute({"action": "write_file", "args": {"path": "teste_ow.txt", "content": "segundo"}})


def test_write_file_outside_workspace_blocked(router: ToolRouter):
    with pytest.raises(SandboxError):
        router.execute({"action": "write_file", "args": {"path": "../fora.txt", "content": "x"}})


def test_read_file_outside_workspace_blocked(router: ToolRouter):
    with pytest.raises(SandboxError):
        router.execute({"action": "read_file", "args": {"path": "../../secret"}})


def test_list_dir_outside_workspace_blocked(router: ToolRouter):
    with pytest.raises(SandboxError):
        router.execute({"action": "list_dir", "args": {"path": "../.."}})


def test_search_text_outside_workspace_blocked(router: ToolRouter):
    with pytest.raises(SandboxError):
        router.execute({"action": "search_text", "args": {"path": "../..", "pattern": "x"}})


def test_unknown_tool_raises_error(router: ToolRouter):
    with pytest.raises(ToolError, match="desconhecida"):
        router.execute({"action": "run_command", "args": {"command": "ls"}})


def test_shell_not_available(router: ToolRouter):
    """run_command nunca deve estar disponível na Fase 4."""
    assert "run_command" not in router.available_tools()
    assert "shell" not in router.available_tools()


# === PARSER =================================================================


def test_parse_bare_json_string(router: ToolRouter):
    result = router._parse('{"action": "list_dir", "args": {"path": "."}}')
    assert result["action"] == "list_dir"


def test_parse_markdown_code_block(router: ToolRouter):
    text = '```json\n{"action": "list_dir", "args": {"path": "."}}\n```'
    result = router._parse(text)
    assert result["action"] == "list_dir"


def test_parse_markdown_block_no_lang(router: ToolRouter):
    text = '```\n{"action": "read_file", "args": {"path": "a.txt"}}\n```'
    result = router._parse(text)
    assert result["action"] == "read_file"


def test_parse_dict_passthrough(router: ToolRouter):
    d = {"action": "list_dir", "args": {"path": "."}}
    assert router._parse(d) is d


def test_parse_invalid_json_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="JSON invalido"):
        router._parse("isso nao e json")


def test_parse_missing_action_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="action"):
        router.execute({"args": {"path": "."}})


def test_parse_args_not_dict_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="args"):
        router.execute({"action": "list_dir", "args": "string invalida"})


# === list_dir ===============================================================


def test_list_dir_root(router: ToolRouter):
    result = router.execute({"action": "list_dir", "args": {"path": "."}})
    assert "hello.txt" in result
    assert "subdir" in result


def test_list_dir_subdirectory(router: ToolRouter):
    result = router.execute({"action": "list_dir", "args": {"path": "subdir"}})
    assert "nested.txt" in result


def test_list_dir_default_path(router: ToolRouter):
    result = router.execute({"action": "list_dir", "args": {}})
    assert "hello.txt" in result


def test_list_dir_nonexistent_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="Nao encontrado"):
        router.execute({"action": "list_dir", "args": {"path": "nao_existe"}})


def test_list_dir_on_file_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="nao e um diretorio"):
        router.execute({"action": "list_dir", "args": {"path": "hello.txt"}})


def test_list_dir_empty(router: ToolRouter, ws: Path):
    (ws / "vazio").mkdir()
    result = router.execute({"action": "list_dir", "args": {"path": "vazio"}})
    assert "vazio" in result


# === read_file ==============================================================


def test_read_file_content(router: ToolRouter):
    result = router.execute({"action": "read_file", "args": {"path": "hello.txt"}})
    assert "hello world" in result
    assert "line two" in result


def test_read_file_nested(router: ToolRouter):
    result = router.execute({"action": "read_file", "args": {"path": "subdir/nested.txt"}})
    assert "nested content" in result


def test_read_file_nonexistent_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="nao encontrado"):
        router.execute({"action": "read_file", "args": {"path": "inexistente.txt"}})


def test_read_file_missing_path_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="requer 'path'"):
        router.execute({"action": "read_file", "args": {}})


def test_read_file_on_dir_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="diretorio"):
        router.execute({"action": "read_file", "args": {"path": "subdir"}})


def test_read_file_too_large_raises(router: ToolRouter, ws: Path):
    # G17.1: arquivo acima do ceiling absoluto (5 MB) e recusado de vez.
    huge = ws / "huge.txt"
    huge.write_bytes(b"x" * 6_000_000)
    with pytest.raises(ToolError, match="grande"):
        router.execute({"action": "read_file", "args": {"path": "huge.txt"}})


def test_read_file_output_char_cap_raises(router: ToolRouter, ws: Path):
    # G17.1: arquivo abaixo do ceiling mas cuja JANELA estoura o cap de chars
    # do output (linha unica gigante) → erro pedindo reduzir limit/offset.
    big = ws / "big.txt"
    big.write_bytes(b"x" * 200_000)  # 1 linha, 200K chars > cap de 100K
    with pytest.raises(ToolError, match="produziu"):
        router.execute({"action": "read_file", "args": {"path": "big.txt"}})


def test_read_file_pagination_and_line_numbers(router: ToolRouter, ws: Path):
    # G17.1: offset/limit + numeracao de linha no output.
    (ws / "multi.txt").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    out = router.execute({"action": "read_file", "args": {"path": "multi.txt", "offset": 2, "limit": 2}})
    assert "2\tb" in out
    assert "3\tc" in out
    assert "1\ta" not in out  # offset pulou a linha 1
    assert "linhas 2-3 de 5" in out


def test_read_file_dedup_blocks_reread(router: ToolRouter, ws: Path):
    # G17.1: reler a mesma janela de arquivo inalterado vira stub e depois bloqueia.
    (ws / "stable.txt").write_text("conteudo\n", encoding="utf-8")
    first = router.execute({"action": "read_file", "args": {"path": "stable.txt"}})
    assert "conteudo" in first
    second = router.execute({"action": "read_file", "args": {"path": "stable.txt"}})
    assert "inalterado" in second
    with pytest.raises(ToolError, match="BLOQUEADO"):
        router.execute({"action": "read_file", "args": {"path": "stable.txt"}})


# === write_file =============================================================


def test_write_file_creates_new(router: ToolRouter, ws: Path):
    router.execute({"action": "write_file", "args": {"path": "novo.txt", "content": "conteudo"}})
    assert (ws / "novo.txt").read_text(encoding="utf-8") == "conteudo"


def test_write_file_with_overwrite_true(router: ToolRouter, ws: Path):
    # G17.2: sobrescrever arquivo existente exige leitura previa.
    router.execute({"action": "read_file", "args": {"path": "hello.txt"}})
    router.execute({"action": "write_file", "args": {"path": "hello.txt", "content": "novo", "overwrite": True}})
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "novo"


def test_write_file_overwrite_without_read_raises(router: ToolRouter, ws: Path):
    # G17.2: overwrite as cegas (sem read_file antes) e bloqueado.
    with pytest.raises(ToolError, match="nao foi lido"):
        router.execute({"action": "write_file", "args": {"path": "hello.txt", "content": "x", "overwrite": True}})


def test_write_file_creates_subdirs(router: ToolRouter, ws: Path):
    router.execute({
        "action": "write_file",
        "args": {"path": "pasta/sub/arquivo.txt", "content": "ok"},
    })
    assert (ws / "pasta" / "sub" / "arquivo.txt").exists()


def test_write_file_missing_path_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="requer 'path'"):
        router.execute({"action": "write_file", "args": {"content": "x"}})


def test_write_file_missing_content_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="requer 'content'"):
        router.execute({"action": "write_file", "args": {"path": "a.txt"}})


def test_write_file_invalid_overwrite_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="overwrite"):
        router.execute({"action": "write_file", "args": {"path": "a.txt", "content": "x", "overwrite": "yes"}})


# === search_text ============================================================


def test_search_text_finds_match(router: ToolRouter):
    result = router.execute({"action": "search_text", "args": {"path": ".", "pattern": "hello"}})
    assert "hello.txt" in result
    assert "hello world" in result


def test_search_text_case_insensitive(router: ToolRouter):
    result = router.execute({"action": "search_text", "args": {"path": ".", "pattern": "HELLO"}})
    assert "hello" in result.lower()


def test_search_text_in_subdirs(router: ToolRouter):
    result = router.execute({"action": "search_text", "args": {"path": ".", "pattern": "nested"}})
    assert "nested.txt" in result


def test_search_text_no_results(router: ToolRouter):
    result = router.execute({"action": "search_text", "args": {"path": ".", "pattern": "xyz_nao_existe"}})
    assert "Nenhum resultado" in result


def test_search_text_missing_pattern_raises(router: ToolRouter):
    with pytest.raises(ToolError, match="requer 'pattern'"):
        router.execute({"action": "search_text", "args": {"path": "."}})


def test_search_text_single_file(router: ToolRouter):
    result = router.execute({"action": "search_text", "args": {"path": "hello.txt", "pattern": "line"}})
    assert "line two" in result


# === available_tools ========================================================


def test_available_tools_contains_core(router: ToolRouter):
    tools = router.available_tools()
    # Ferramentas básicas sempre presentes
    assert {"list_dir", "read_file", "write_file", "search_text"}.issubset(set(tools))
    # Novas tools de arquivo e utilidade também presentes
    assert {"create_dir", "append_file", "glob_files", "calculate", "datetime_now"}.issubset(set(tools))
    # Shell e web opcionais NÃO estão sem config
    assert "run_command" not in tools
    assert "web_search" not in tools


def test_tool_info_has_description(router: ToolRouter):
    info = router.tool_info("read_file")
    assert "description" in info
    assert "args" in info
