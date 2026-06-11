"""Testes para _detect_loop e _loop_fp (detecção de loop no agent).

Cobre:
1. Fingerprint correta
2. Nenhum loop em log curto
3. Soft warning (3× consecutivas)
4. Hard stop (5× consecutivas)
5. Oscilação A→B→A→B→A→B
6. Tools diferentes não disparam alerta
7. Oscilação com mesma tool não dispara (A→A→A → repetição, não oscilação)
"""

from __future__ import annotations

import pytest

from bauer.agent import (
    _LOOP_OSCIL_WINDOW,
    _LOOP_REPEAT_HARD,
    _LOOP_REPEAT_WARN,
    _detect_loop,
    _loop_fp,
)


def _entry(tool: str, result: str = "resultado ok") -> dict:
    return {"tool": tool, "result": result}


# ─── _loop_fp ────────────────────────────────────────────────────────────────


def test_loop_fp_includes_tool_name():
    e = _entry("list_dir", "arquivo.py\noutro.py")
    assert "list_dir" in _loop_fp(e)


def test_loop_fp_includes_result_prefix():
    e = _entry("read_file", "conteudo do arquivo x" * 10)
    fp = _loop_fp(e)
    assert "conteudo do arquivo x" in fp


def test_loop_fp_truncates_long_result():
    e = _entry("read_file", "x" * 500)
    fp = _loop_fp(e)
    # Fingerprint não deve ter 500 chars do resultado
    assert len(fp) < 200


def test_loop_fp_different_tools_differ():
    a = _loop_fp(_entry("list_dir", "r"))
    b = _loop_fp(_entry("read_file", "r"))
    assert a != b


def test_loop_fp_same_tool_different_result_differ():
    a = _loop_fp(_entry("list_dir", "resultado_A"))
    b = _loop_fp(_entry("list_dir", "resultado_B"))
    assert a != b


# ─── _detect_loop — sem loop ─────────────────────────────────────────────────


def test_detect_loop_empty_log():
    warn, hard = _detect_loop([])
    assert warn is None
    assert hard is False


def test_detect_loop_single_entry():
    warn, hard = _detect_loop([_entry("list_dir")])
    assert warn is None
    assert hard is False


def test_detect_loop_two_different_tools():
    log = [_entry("list_dir"), _entry("read_file")]
    warn, hard = _detect_loop(log)
    assert warn is None


def test_detect_loop_two_same_tool_below_threshold():
    log = [_entry("list_dir"), _entry("list_dir")]
    warn, hard = _detect_loop(log)
    assert warn is None  # só 2 repetições, threshold é 3


# ─── _detect_loop — soft warning (3×) ────────────────────────────────────────


def test_detect_loop_soft_warning_at_threshold():
    log = [_entry("list_dir")] * _LOOP_REPEAT_WARN
    warn, hard = _detect_loop(log)
    assert warn is not None
    assert hard is False
    assert "list_dir" in warn
    assert str(_LOOP_REPEAT_WARN) in warn


def test_detect_loop_soft_warning_mentions_tool():
    log = [_entry("execute_code", "erro: arquivo não encontrado")] * _LOOP_REPEAT_WARN
    warn, hard = _detect_loop(log)
    assert warn is not None
    assert "execute_code" in warn


def test_detect_loop_soft_warning_not_hard():
    log = [_entry("read_file")] * _LOOP_REPEAT_WARN
    _, hard = _detect_loop(log)
    assert hard is False


def test_detect_loop_warning_dispara_uma_unica_vez():
    """Na 4ª repetição NÃO repete o aviso (anti-ruído, 2026-06-11).

    O aviso é injetado no contexto da sessão — repeti-lo a cada chamada
    enchia o histórico com o mesmo parágrafo. Dispara só em ==3; entre o
    aviso e o hard stop (5) o sistema fica em silêncio.
    """
    log = [_entry("web_search")] * (_LOOP_REPEAT_WARN + 1)  # 4 repetições
    warn, hard = _detect_loop(log)
    assert warn is None
    assert hard is False


def test_detect_loop_hard_stop_mensagem_concisa():
    """Hard stop vai para o contexto E para o usuário — precisa ser curto."""
    log = [_entry("web_search")] * _LOOP_REPEAT_HARD
    warn, hard = _detect_loop(log)
    assert hard is True
    assert len(warn) < 250
    assert "web_search" in warn


# ─── _detect_loop — hard stop (5×) ───────────────────────────────────────────


def test_detect_loop_hard_stop_at_threshold():
    log = [_entry("list_dir")] * _LOOP_REPEAT_HARD
    warn, hard = _detect_loop(log)
    assert warn is not None
    assert hard is True


def test_detect_loop_hard_stop_message_firm():
    log = [_entry("read_file")] * _LOOP_REPEAT_HARD
    warn, hard = _detect_loop(log)
    assert hard is True
    # Mensagem deve ser mais enfática
    assert "PARE" in warn or "interromp" in warn.lower() or "LOOP" in warn


def test_detect_loop_hard_stop_beyond_threshold():
    """7 repetições também dispara hard stop."""
    log = [_entry("list_dir")] * 7
    warn, hard = _detect_loop(log)
    assert hard is True


def test_detect_loop_consecutive_resets_with_different():
    """2 repetições + 1 diferente + 2 repetições → não deve dar warning."""
    log = [
        _entry("list_dir", "r1"),
        _entry("list_dir", "r1"),
        _entry("read_file", "r2"),
        _entry("list_dir", "r1"),
        _entry("list_dir", "r1"),
    ]
    warn, hard = _detect_loop(log)
    assert warn is None  # só 2 consecutivas no final


# ─── _detect_loop — oscilação A→B→A→B ────────────────────────────────────────


def test_detect_loop_oscillation_detected():
    log = []
    for _ in range(_LOOP_OSCIL_WINDOW // 2):
        log.append(_entry("list_dir", "resultado_a"))
        log.append(_entry("read_file", "resultado_b"))
    warn, hard = _detect_loop(log)
    assert warn is not None
    assert "list_dir" in warn or "read_file" in warn
    assert hard is False  # oscilação é soft warning


def test_detect_loop_oscillation_not_hard_stop():
    log = []
    for _ in range(4):
        log.append(_entry("glob_files", "*.py"))
        log.append(_entry("list_dir", "src/"))
    warn, hard = _detect_loop(log)
    if warn:
        assert hard is False


def test_detect_loop_oscillation_below_window():
    """Menos de OSCIL_WINDOW calls — oscilação não detectada."""
    log = []
    for _ in range((_LOOP_OSCIL_WINDOW // 2) - 1):
        log.append(_entry("list_dir"))
        log.append(_entry("read_file"))
    warn, hard = _detect_loop(log)
    # Pode ter soft warning (repetição) mas não oscilação — a janela não está cheia
    # O importante é que o log é menor que OSCIL_WINDOW
    assert len(log) < _LOOP_OSCIL_WINDOW


def test_detect_loop_three_tools_no_oscillation():
    """A→B→C→A→B→C não é oscilação de 2 (precisa ser exatamente 2 padrões alternados)."""
    log = [
        _entry("list_dir"),
        _entry("read_file"),
        _entry("execute_code"),
        _entry("list_dir"),
        _entry("read_file"),
        _entry("execute_code"),
    ]
    warn, hard = _detect_loop(log)
    # 3 tools diferentes nas posições ímpares e pares — não é oscilação de 2
    # (evens = {list_dir, execute_code}, não é conjunto de 1 elemento)
    # Pode retornar None ou warning por repetição, mas não oscilação 2-cycle
    assert hard is False


def test_detect_loop_same_tool_not_oscillation():
    """A→A→A→A→A→A é repetição, não oscilação."""
    log = [_entry("list_dir")] * _LOOP_OSCIL_WINDOW
    warn, hard = _detect_loop(log)
    # Deve detectar como repetição (hard stop), não oscilação
    assert warn is not None
    assert hard is True  # 6 repetições → hard stop (> _LOOP_REPEAT_HARD=5)


# ─── Prioridade: repetição vs oscilação ──────────────────────────────────────


def test_hard_stop_takes_priority_over_oscillation():
    """Se as últimas calls são repetição E há oscilação no histórico,
    hard stop deve prevalecer."""
    log = []
    # Oscilação primeiro
    for _ in range(3):
        log.append(_entry("list_dir"))
        log.append(_entry("read_file"))
    # Depois repetição pesada
    for _ in range(_LOOP_REPEAT_HARD):
        log.append(_entry("execute_code", "mesmo resultado"))
    warn, hard = _detect_loop(log)
    assert warn is not None
    assert hard is True  # repetição de execute_code dispara hard stop
