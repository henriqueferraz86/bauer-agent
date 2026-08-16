"""Testes para compressão imediata de tool results grandes (_ctx_result_for_context).

Cobre:
1. Resultados pequenos passam sem modificação
2. Resultados > threshold são comprimidos
3. Compressão de listagem (list_dir, glob_files)
4. Compressão de conteúdo (read_file, execute_code)
5. Compressão genérica
6. Output nunca ultrapassa _TOOL_RESULT_COMPRESSED_PREVIEW
7. Flag was_compressed retornada corretamente
"""
from __future__ import annotations

import pytest

from bauer.agent import (
    _TOOL_RESULT_COMPRESS_THRESHOLD,
    _TOOL_RESULT_COMPRESSED_PREVIEW,
    _compress_tool_result_inline,
    _ctx_result_for_context,
)


# ─── _ctx_result_for_context — casos básicos ─────────────────────────────────


def test_small_result_unchanged():
    result = "arquivo.py\noutro.py"
    ctx, compressed = _ctx_result_for_context("list_dir", result)
    assert ctx == result
    assert compressed is False


def test_small_result_at_threshold_unchanged():
    # Exatamente no limite — não comprime
    result = "x" * _TOOL_RESULT_COMPRESS_THRESHOLD
    ctx, compressed = _ctx_result_for_context("read_file", result)
    assert compressed is False


def test_large_result_compressed():
    result = "linha\n" * 500  # bem acima do threshold
    ctx, compressed = _ctx_result_for_context("list_dir", result)
    assert compressed is True
    assert len(ctx) < len(result)


def test_large_result_within_preview_limit():
    result = "x" * 10_000
    ctx, compressed = _ctx_result_for_context("execute_code", result)
    assert compressed is True
    assert len(ctx) <= _TOOL_RESULT_COMPRESSED_PREVIEW


def test_was_compressed_false_for_small():
    _, compressed = _ctx_result_for_context("read_file", "pequeno resultado")
    assert compressed is False


def test_was_compressed_true_for_large():
    big = "linha de resultado\n" * 200
    _, compressed = _ctx_result_for_context("read_file", big)
    assert compressed is True


# ─── _compress_tool_result_inline — listagem ─────────────────────────────────


def test_list_dir_shows_item_count():
    items = [f"arquivo_{i}.py" for i in range(50)]
    result = "\n".join(items)
    compressed = _compress_tool_result_inline("list_dir", result)
    assert "50" in compressed or "itens" in compressed


def test_list_dir_shows_first_items():
    items = [f"file_{i}.py" for i in range(20)]
    result = "\n".join(items)
    compressed = _compress_tool_result_inline("list_dir", result)
    assert "file_0.py" in compressed


def test_glob_files_compressed():
    files = [f"src/module_{i}.py" for i in range(30)]
    result = "\n".join(files)
    compressed = _compress_tool_result_inline("glob_files", result)
    assert len(compressed) <= _TOOL_RESULT_COMPRESSED_PREVIEW
    assert "src/module_0.py" in compressed


def test_list_dir_shows_extra_count():
    items = [f"item_{i}" for i in range(20)]
    result = "\n".join(items)
    compressed = _compress_tool_result_inline("list_dir", result)
    # Deve indicar que há mais itens além dos mostrados
    assert "+" in compressed or "mais" in compressed


# ─── _compress_tool_result_inline — conteúdo ─────────────────────────────────


def test_read_file_shows_line_count():
    lines = [f"    código da linha {i}" for i in range(100)]
    result = "\n".join(lines)
    compressed = _compress_tool_result_inline("read_file", result)
    assert "100" in compressed or "linhas" in compressed


def test_read_file_shows_first_lines():
    lines = ["def minha_funcao():", "    return 42"] + ["# mais código"] * 50
    result = "\n".join(lines)
    compressed = _compress_tool_result_inline("read_file", result)
    assert "minha_funcao" in compressed


def test_execute_code_shows_output_start():
    output = "=== Resultado da Execução ===\n" + "linha de saída\n" * 100
    compressed = _compress_tool_result_inline("execute_code", output)
    assert "Resultado" in compressed or "Execu" in compressed


def test_content_tool_within_preview_limit():
    content = "linha importante\n" * 200
    compressed = _compress_tool_result_inline("read_file", content)
    assert len(compressed) <= _TOOL_RESULT_COMPRESSED_PREVIEW


# ─── _compress_tool_result_inline — genérico ─────────────────────────────────


def test_unknown_tool_generic_compression():
    result = "dado qualquer\n" * 100
    compressed = _compress_tool_result_inline("tool_desconhecida", result)
    assert len(compressed) <= _TOOL_RESULT_COMPRESSED_PREVIEW


def test_generic_shows_size_info():
    result = "x" * 5000
    compressed = _compress_tool_result_inline("custom_tool", result)
    assert "5000" in compressed or "chars" in compressed


def test_empty_result_not_crashes():
    compressed = _compress_tool_result_inline("list_dir", "")
    assert isinstance(compressed, str)


def test_single_line_result():
    result = "apenas uma linha de resultado"
    ctx, compressed = _ctx_result_for_context("read_file", result)
    assert ctx == result
    assert compressed is False


# ─── Propriedades invariantes ─────────────────────────────────────────────────


@pytest.mark.parametrize("action", [
    "list_dir", "glob_files", "read_file", "execute_code",
    "http_request", "delegate_task", "session_search",
])
def test_compressed_always_within_limit(action):
    """Para qualquer action, o resultado comprimido nunca ultrapassa o limite."""
    big_result = f"linha de {action}\n" * 300
    compressed = _compress_tool_result_inline(action, big_result)
    assert len(compressed) <= _TOOL_RESULT_COMPRESSED_PREVIEW, (
        f"{action}: {len(compressed)} > {_TOOL_RESULT_COMPRESSED_PREVIEW}"
    )


@pytest.mark.parametrize("action", [
    "list_dir", "read_file", "execute_code",
])
def test_compressed_contains_meaningful_info(action):
    """Resultado comprimido deve conter alguma informação útil (não só metadados)."""
    lines = [f"conteudo_relevante_{i}" for i in range(50)]
    result = "\n".join(lines)
    compressed = _compress_tool_result_inline(action, result)
    # Deve ter pelo menos a primeira linha ou contagem
    has_content = "conteudo_relevante_0" in compressed or "50" in compressed
    assert has_content, f"Compressão de {action} não preservou info útil: {compressed}"


# ─── Preservação do FIM do resultado (regressão 2026-08-16) ───────────────────
#
# A compressão cortava só pela cabeça. Como exceção Python, mensagem de erro de
# comando e tally do pytest ficam nas ÚLTIMAS linhas, o modelo recebia o
# preâmbulo e nunca a causa da falha. Medido antes do fix: uma run de pytest com
# 2 falhas chegava como 350 chars de "bringing up nodes" + dots, cortada em
# "FAI" — 0 de 5 sinais (FAILED/assert/KeyError/tally/short test summary).


@pytest.mark.parametrize("action", ["run_command", "execute_code", "read_file"])
def test_preserva_ultimas_linhas(action):
    """O fim do resultado — onde mora o erro — nunca pode ser descartado."""
    result = "linha de progresso irrelevante\n" * 200 + "ERRO_FINAL: causa raiz aqui"
    compressed = _compress_tool_result_inline(action, result)
    assert "ERRO_FINAL: causa raiz aqui" in compressed


def test_traceback_preserva_excecao():
    """Script que imprime muito e quebra no fim: a exceção deve chegar."""
    saida = "".join(f"processando registro {i:04d} ... ok\n" for i in range(120))
    saida += 'Traceback (most recent call last):\n  File "<string>", line 3\n'
    saida += "RuntimeError: schema divergente na tabela pedidos"
    compressed = _compress_tool_result_inline("execute_code", saida)
    assert "RuntimeError" in compressed
    assert "schema divergente" in compressed


def test_pytest_preserva_tally_e_failed():
    """Saída de pytest: FAILED e o tally final precisam sobreviver."""
    saida = "bringing up nodes...\n" + "." * 72 + " [ 51%]\n"
    saida += "=" * 30 + " FAILURES " + "=" * 30 + "\n"
    saida += "detalhe do traceback\n" * 60
    saida += "=" * 25 + " short test summary info " + "=" * 25 + "\n"
    saida += "FAILED tests/test_x.py::test_um - KeyError: 'ausente'\n"
    saida += "2 failed, 138 passed in 40.20s"
    compressed = _compress_tool_result_inline("run_command", saida)
    assert "FAILED" in compressed
    assert "2 failed" in compressed


def test_preserva_inicio_tambem():
    """Início não pode ser sacrificado ao preservar o fim."""
    result = "PRIMEIRA_LINHA: contexto\n" + "meio\n" * 300 + "ULTIMA_LINHA: erro"
    compressed = _compress_tool_result_inline("run_command", result)
    assert "PRIMEIRA_LINHA" in compressed
    assert "ULTIMA_LINHA" in compressed


def test_linha_unica_gigante_preserva_as_duas_pontas():
    """Sem quebras de linha, o corte é por char — mas ainda pelas duas pontas."""
    result = "INICIO_DO_JSON" + "x" * 5000 + "FIM_DO_JSON"
    compressed = _compress_tool_result_inline("http_request", result)
    assert len(compressed) <= _TOOL_RESULT_COMPRESSED_PREVIEW
    assert "FIM_DO_JSON" in compressed


def test_listagem_continua_priorizando_o_inicio():
    """Listagens são homogêneas: o fim não carrega sinal, não deve regredir."""
    result = "\n".join(f"arquivo_{i}.py" for i in range(200))
    compressed = _compress_tool_result_inline("list_dir", result)
    assert "arquivo_0.py" in compressed
    assert len(compressed) <= _TOOL_RESULT_COMPRESSED_PREVIEW


def test_run_command_nao_cai_no_ramo_generico():
    """run_command precisa de tratamento por linha, não preview de 300 chars."""
    from bauer.agent import _CONTENT_TOOLS
    assert "run_command" in _CONTENT_TOOLS


@pytest.mark.parametrize("tamanho", [2001, 3001, 10_000, 200_000])
def test_acima_do_threshold_sempre_comprime(tamanho):
    """Caminho único acima do threshold — não existe rota de truncamento cru.

    Havia um segundo ramo em _ctx_result_for_context truncando pela cabeça em
    3000 chars; era inalcançável e foi removido. Este teste trava a invariante:
    qualquer resultado acima do threshold sai comprimido e dentro do preview.
    """
    result = "linha com conteudo\n" * (tamanho // 19 + 1)
    ctx, compressed = _ctx_result_for_context("run_command", result)
    assert compressed is True
    assert len(ctx) <= _TOOL_RESULT_COMPRESSED_PREVIEW
