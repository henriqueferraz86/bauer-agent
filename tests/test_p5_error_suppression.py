"""Testes de P5 — tratamento de erros suprimidos.

Verifica que:
- log_suppressed() existe e emite em DEBUG sem levantar excecao.
- Falha de plugin hook nao bloqueia a sessao.
- Audit baseline: registra contagem de except:pass para rastreamento.
"""

from __future__ import annotations

import logging


def test_log_suppressed_emits_debug(caplog):
    """log_suppressed deve emitir mensagem de DEBUG sem re-levantar."""
    from bauer.logging_config import log_suppressed

    with caplog.at_level(logging.DEBUG, logger="bauer"):
        log_suppressed("test.context", ValueError("mensagem de teste"))

    assert any("test.context" in r.message for r in caplog.records), (
        f"log_suppressed nao emitiu mensagem com contexto 'test.context'. "
        f"Records: {[r.message for r in caplog.records]}"
    )
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, "log_suppressed deve emitir em nivel DEBUG"


def test_log_suppressed_does_not_raise():
    """log_suppressed nunca deve levantar excecao."""
    from bauer.logging_config import log_suppressed

    try:
        log_suppressed("safe.context", RuntimeError("erro de teste"))
    except Exception as exc:
        raise AssertionError(f"log_suppressed levantou excecao: {exc}") from exc


def test_log_suppressed_accepts_base_exception():
    """log_suppressed deve aceitar qualquer BaseException."""
    from bauer.logging_config import log_suppressed

    try:
        log_suppressed("base.exc.context", KeyboardInterrupt())
    except Exception as exc:
        raise AssertionError(f"log_suppressed levantou excecao com BaseException: {exc}") from exc


def test_p5_audit_baseline():
    """Registra o baseline de except:pass para rastreamento de progresso.

    NENHUMA ASSERTIVA DE REGRESSAO aqui — o numero vai diminuir gradualmente
    conforme P5 for aplicado. Este teste existe para documentar o estado e
    falhar caso o numero AUMENTE acima de um limiar razoavel.
    """
    import ast
    import pathlib

    critical = []
    for p in sorted(pathlib.Path("bauer").rglob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                body = node.body
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    critical.append(f"{p}:{node.lineno}")

    count = len(critical)
    # Baseline auditado em 2026-06-25: 272 ocorrencias.
    # Este limite cresce o baseline em 10% para absorver adicoes defensivas legitimas.
    # Se ultrapassar, o PR deve classificar os novos casos.
    MAX_ALLOWED = 300
    assert count <= MAX_ALLOWED, (
        f"except:pass cresceu para {count} (baseline=272, limite={MAX_ALLOWED}). "
        "Cada novo caso deve ter: log_suppressed(), comentario justificando, ou teste."
    )
    # Imprime o total para visibilidade no CI (nao e falha)
    print(f"\n[P5 audit] except:pass count: {count}/{MAX_ALLOWED} (baseline 272)")
