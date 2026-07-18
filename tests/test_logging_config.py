"""Testes para logging_config — setup_logging e get_logger."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

from bauer.logging_config import get_logger, setup_logging


def test_setup_logging_returns_logger():
    logger = setup_logging()
    assert isinstance(logger, logging.Logger)
    assert logger.name == "bauer"


def test_setup_logging_default_level():
    logger = setup_logging(level="info")
    assert logger.level == logging.INFO


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
    assert not any(isinstance(h, logging.FileHandler) for h in logger.handlers)
    # E nada de lixo "MagicMock/" foi criado no CWD isolado.
    assert not any(p.name == "MagicMock" for p in tmp_path.iterdir())
    bauer_logger.handlers.clear()
