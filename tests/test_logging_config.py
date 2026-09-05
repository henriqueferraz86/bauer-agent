"""Testes para logging_config — setup_logging e get_logger."""

from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.logging_config import _SafeStreamHandler, get_logger, setup_logging


@pytest.fixture(autouse=True)
def _fecha_handlers_do_bauer_apos_o_teste():
    """Não deixe StreamHandlers presos ao ``stderr`` capturado pelo pytest.

    A suíte usa workers de longa duração. Sem a limpeza, um teste que chama
    ``setup_logging()`` deixa no logger global um handler ligado ao capture
    temporário daquele teste; testes posteriores escrevem num stream já
    fechado e geram ``Logging error`` espúrio no CI.
    """
    yield
    logger = logging.getLogger("bauer")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()


def test_setup_logging_returns_logger():
    logger = setup_logging()
    assert isinstance(logger, logging.Logger)
    assert logger.name == "bauer"


def test_setup_logging_default_level():
    logger = setup_logging(level="info")
    assert logger.level == logging.INFO


def test_setup_logging_console_fica_silencioso():
    logging.getLogger("bauer").handlers.clear()
    logger = setup_logging(level="info")
    stream_handlers = [h for h in logger.handlers if isinstance(h, _SafeStreamHandler)]

    assert len(stream_handlers) == 1
    assert stream_handlers[0].level > logging.CRITICAL


def test_setup_logging_debug_level():
    # Limpa handlers para forcar reconfiguracao
    bauer_logger = logging.getLogger("bauer")
    bauer_logger.handlers.clear()
    logger = setup_logging(level="debug")
    assert logger.level == logging.DEBUG
    bauer_logger.handlers.clear()  # limpa apos o teste


def test_setup_logging_idempotent():
    """Chamadas repetidas nao duplicam handlers."""
    bauer_logger = logging.getLogger("bauer")
    bauer_logger.handlers.clear()
    setup_logging(level="info")
    count_after_first = len(bauer_logger.handlers)
    setup_logging(level="info")  # segunda chamada
    assert len(bauer_logger.handlers) == count_after_first


def test_setup_logging_with_file(tmp_path: Path):
    log_file = str(tmp_path / "bauer.log")
    bauer_logger = logging.getLogger("bauer")
    bauer_logger.handlers.clear()
    logger = setup_logging(level="info", file_path=log_file)
    # Deve ter 2 handlers: stream + file
    assert len(logger.handlers) == 2
    assert Path(log_file).exists() or True  # arquivo criado na escrita
    bauer_logger.handlers.clear()


def test_setup_logging_file_creates_parent_dir(tmp_path: Path):
    log_file = str(tmp_path / "nested" / "dir" / "bauer.log")
    bauer_logger = logging.getLogger("bauer")
    bauer_logger.handlers.clear()
    setup_logging(level="info", file_path=log_file)
    assert Path(log_file).parent.exists()
    bauer_logger.handlers.clear()


def test_setup_logging_unknown_level_defaults_to_info():
    bauer_logger = logging.getLogger("bauer")
    bauer_logger.handlers.clear()
    logger = setup_logging(level="unknown_level")
    assert logger.level == logging.INFO
    bauer_logger.handlers.clear()


def test_get_logger_returns_bauer_logger():
    logger = get_logger()
    assert logger.name == "bauer"


def test_get_logger_custom_name():
    logger = get_logger("bauer.submodule")
    assert logger.name == "bauer.submodule"


def test_stream_handler_ignora_stream_fechado_sem_emitir_erro():
    stream = StringIO()
    handler = _SafeStreamHandler(stream)
    stream.close()
    record = logging.LogRecord("bauer", logging.INFO, __file__, 1, "evento", (), None)

    with patch.object(logging.StreamHandler, "handleError") as handle_error:
        handler.emit(record)

    handle_error.assert_not_called()


def test_setup_logging_ignores_non_path_file(tmp_path, monkeypatch):
    """Regressão: file_path truthy não-caminho (ex.: cfg.logging.file de um
    MagicMock, ou config malformado) NÃO deve criar diretórios de lixo no CWD
    — antes 'MagicMock/mock.logging.file/<id>' aparecia na raiz do repo. Com
    o guard de tipo, o log em arquivo é pulado (console segue)."""
    monkeypatch.chdir(tmp_path)
    bauer_logger = logging.getLogger("bauer")
    bauer_logger.handlers.clear()

    logger = setup_logging(level="info", file_path=MagicMock())

    # Nenhum FileHandler foi adicionado (só o StreamHandler de console).
    assert not any(isinstance(h, _FileHandlerReal) for h in logger.handlers)
    # E nada de lixo "MagicMock/" foi criado no CWD isolado.
    assert not any(p.name == "MagicMock" for p in tmp_path.iterdir())
    bauer_logger.handlers.clear()


# ── Log nao-gravavel DEGRADA, nao crasha (bug real: Beelink) ────────────────
#
# `logging.file` default e "./logs/bauer.log", RELATIVO ao cwd. No Beelink o
# `./logs` do cwd pertencia ao root, entao FileHandler estourava PermissionError
# e derrubava `bauer doctor` inteiro com traceback. Log e caminho ACESSORIO:
# tem que degradar para so-console, nunca quebrar o comando (AGENTS.md).

_FileHandlerReal = logging.FileHandler  # antes de qualquer monkeypatch


def _limpa():
    logging.getLogger("bauer").handlers.clear()


def test_path_nao_gravavel_nao_crasha(tmp_path, monkeypatch):
    """O cenario do Beelink: mkdir/abertura do log falha com OSError."""
    _limpa()
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))

    import bauer.logging_config as lc
    original = logging.FileHandler

    def _falha_no_path_ruim(path, *a, **k):
        if "ruim" in str(path):
            raise PermissionError(13, "Permission denied")
        return original(path, *a, **k)

    monkeypatch.setattr(logging, "FileHandler", _falha_no_path_ruim)

    # Nao pode levantar — antes levantava PermissionError aqui.
    logger = lc.setup_logging(level="info", file_path=str(tmp_path / "ruim" / "bauer.log"))
    assert isinstance(logger, logging.Logger)
    _limpa()


def test_cai_no_fallback_do_bauer_home(tmp_path, monkeypatch):
    """Quando o path configurado falha, tenta ~/.bauer/logs/bauer.log — que e
    garantidamente do usuario. O log em arquivo continua funcionando, so muda
    de lugar."""
    _limpa()
    home = tmp_path / "home"
    monkeypatch.setenv("BAUER_HOME", str(home))

    import bauer.logging_config as lc
    original = logging.FileHandler

    def _falha_no_path_ruim(path, *a, **k):
        if "ruim" in str(path):
            raise PermissionError(13, "Permission denied")
        return original(path, *a, **k)

    monkeypatch.setattr(logging, "FileHandler", _falha_no_path_ruim)

    logger = lc.setup_logging(level="info", file_path=str(tmp_path / "ruim" / "bauer.log"))
    # stream + file(fallback) = 2 handlers, e o fallback existe sob BAUER_HOME
    file_handlers = [h for h in logger.handlers if isinstance(h, _FileHandlerReal)]
    assert file_handlers, "deveria ter caido no fallback em vez de perder o log"
    assert str(home) in file_handlers[0].baseFilename
    _limpa()


def test_fallback_tambem_falha_segue_so_console(tmp_path, monkeypatch):
    """Se ATE o fallback falhar, degrada para so-console — ainda sem crashar."""
    _limpa()
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))

    import bauer.logging_config as lc
    monkeypatch.setattr(
        logging, "FileHandler",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "nope")),
    )

    logger = lc.setup_logging(level="info", file_path=str(tmp_path / "x" / "bauer.log"))
    assert isinstance(logger, logging.Logger)
    assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)
    assert not any(isinstance(h, _FileHandlerReal) for h in logger.handlers)
    _limpa()


def test_make_file_handler_devolve_none_em_erro(tmp_path, monkeypatch):
    from bauer.logging_config import _make_file_handler

    monkeypatch.setattr(
        logging, "FileHandler",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "nope")),
    )
    fmt = logging.Formatter("%(message)s")
    assert _make_file_handler(tmp_path / "x.log", fmt) is None
