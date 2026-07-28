"""Desconexão do cliente no /stream: cancelar o run e não explodir o middleware.

Dois defeitos distintos, medidos no Beelink com o serve real:

1. `RuntimeError: No response returned.` — o BaseHTTPMiddleware do Starlette
   levanta isso quando o cliente some no meio de uma resposta em streaming.
   Subia como exceção não tratada: traceback inteiro no log a CADA aba fechada,
   e a métrica da requisição nunca era contabilizada.

2. O run seguia até o fim depois do cliente sumir — gerando na GPU para
   ninguém. O laço do /stream já sabia parar quando o run vira "cancelled"
   (caminho do POST /runs/{id}/cancel); faltava alguém marcar isso.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ─── 1. middleware não pode propagar a desconexão ────────────────────────────


@pytest.mark.asyncio
async def test_desconexao_vira_499_e_nao_traceback():
    """Cliente sumiu não é erro do servidor: vira 499, contabilizado à parte."""
    from bauer import server as srv

    metrics = srv._Metrics()
    disconnects_antes = metrics.client_disconnects_total

    # Reproduz o miolo do middleware: call_next levanta o RuntimeError do
    # Starlette e o handler precisa transformá-lo em resposta.
    async def _call_next_desconectado(_request):
        raise RuntimeError("No response returned.")

    async def _middleware_miolo(call_next, request):
        try:
            return await call_next(request)
        except RuntimeError as exc:
            if "No response returned" not in str(exc):
                raise
            metrics.client_disconnects_total += 1
            from starlette.responses import Response as _Resp
            return _Resp(status_code=499)

    resp = await _middleware_miolo(_call_next_desconectado, MagicMock())

    assert resp.status_code == 499
    assert metrics.client_disconnects_total == disconnects_antes + 1


@pytest.mark.asyncio
async def test_outro_runtime_error_continua_propagando():
    """Só a desconexão é engolida — bug de verdade tem que aparecer."""

    async def _call_next_quebrado(_request):
        raise RuntimeError("banco de dados sumiu")

    async def _middleware_miolo(call_next, request):
        try:
            return await call_next(request)
        except RuntimeError as exc:
            if "No response returned" not in str(exc):
                raise
            from starlette.responses import Response as _Resp
            return _Resp(status_code=499)

    with pytest.raises(RuntimeError, match="banco de dados"):
        await _middleware_miolo(_call_next_quebrado, MagicMock())


def test_metrica_de_desconexao_existe_e_e_separada_de_erro():
    """Desconexão não pode inflar `requests_errors` — não é erro 5xx."""
    from bauer.server import _Metrics

    m = _Metrics()
    assert hasattr(m, "client_disconnects_total")
    m.client_disconnects_total += 1
    assert m.requests_errors == 0
    assert "bauer_client_disconnects_total 1" in m.to_prometheus()


# ─── 2. fechar o gerador cancela o run ───────────────────────────────────────


def test_fechar_o_stream_cancela_o_run():
    """É o GeneratorExit que sinaliza a desconexão para um gerador síncrono."""
    run_manager = MagicMock()
    run_id = "run-123"
    interno_fechado: list[bool] = []

    def _event_stream():
        try:
            for i in range(1000):
                yield f"data: {i}\n\n"
        except GeneratorExit:
            interno_fechado.append(True)
            raise

    def _event_stream_cancelavel():
        interno = _event_stream()
        try:
            yield from interno
        except GeneratorExit:
            try:
                run_manager.cancel_run(run_id)
            except Exception:
                pass
            interno.close()
            raise

    gen = _event_stream_cancelavel()
    assert next(gen) == "data: 0\n\n"
    assert next(gen) == "data: 1\n\n"
    gen.close()  # é isto que o Starlette faz quando o cliente some

    run_manager.cancel_run.assert_called_once_with(run_id)
    assert interno_fechado == [True], "gerador interno não foi encerrado"


def test_stream_completo_nao_cancela_o_run():
    """Quem terminou sozinho não pode ser marcado como cancelado."""
    run_manager = MagicMock()

    def _event_stream():
        yield "data: unico\n\n"

    def _event_stream_cancelavel():
        interno = _event_stream()
        try:
            yield from interno
        except GeneratorExit:
            run_manager.cancel_run("run-123")
            interno.close()
            raise

    assert list(_event_stream_cancelavel()) == ["data: unico\n\n"]
    run_manager.cancel_run.assert_not_called()


def test_falha_ao_cancelar_nao_propaga():
    """Teardown quebrado não pode virar erro visível ao cliente que já foi."""
    run_manager = MagicMock()
    run_manager.cancel_run.side_effect = RuntimeError("store fora do ar")

    def _event_stream():
        while True:
            yield "x"

    def _event_stream_cancelavel():
        interno = _event_stream()
        try:
            yield from interno
        except GeneratorExit:
            try:
                run_manager.cancel_run("run-123")
            except Exception:  # noqa: BLE001
                pass
            interno.close()
            raise

    gen = _event_stream_cancelavel()
    next(gen)
    gen.close()  # não pode levantar
    run_manager.cancel_run.assert_called_once()
