"""Configuração de logging para o Bauer Agent.

Logs vão para arquivo; o chat/CLI permanece silencioso por padrão.
Premortem item 9: todo erro precisa ter causa, valor configurado, valor detectado
e ação sugerida. Logs aqui são o canal pra isso.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

# O chat tem uma saída própria para conversa, progresso e erros acionáveis.
# Logs técnicos continuam disponíveis no arquivo configurado, mas não devem
# interromper a leitura da conversa com INFO/WARNING/DEBUG/ERROR.
_CONSOLE_SILENT_LEVEL = logging.CRITICAL + 1


class _SafeStreamHandler(logging.StreamHandler):
    """Stream handler que tolera a captura de stderr já encerrada.

    Threads best-effort podem terminar depois que um runner de teste (ou um
    host embutido) troca/fecha seu stream temporário. O logging padrão captura
    o ``ValueError`` mas imprime um segundo ``Logging error`` no stderr, que
    encobre o evento original. Em stream fechado não há destino possível;
    descartamos somente esse caso, preservando os demais erros de logging.
    """

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        exc = sys.exc_info()[1]
        if isinstance(exc, (OSError, ValueError)) and getattr(self.stream, "closed", False):
            return
        super().handleError(record)


def setup_logging(level: str = "info", file_path: str | None = None) -> logging.Logger:
    """Configura o logger raiz do Bauer. Idempotente."""
    logger = logging.getLogger("bauer")
    logger.setLevel(_LEVELS.get(level.lower(), logging.INFO))
    logger.propagate = False

    # Evita handlers duplicados em chamadas repetidas (testes, REPL).
    if logger.handlers:
        # O CLI permanece silencioso; o logger continua aceitando os níveis
        # configurados para o arquivo de log e para diagnóstico quando necessário.
        for handler in logger.handlers:
            if isinstance(handler, _SafeStreamHandler):
                handler.setLevel(_CONSOLE_SILENT_LEVEL)
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    stream = _SafeStreamHandler(sys.stderr)
    # Mensagens de conversa/progresso usam console.print; nenhum log técnico
    # deve aparecer no terminal do chat. O FileHandler abaixo continua
    # recebendo os registros para diagnóstico, quando configurado.
    stream.setLevel(_CONSOLE_SILENT_LEVEL)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    # Guard de tipo: só trata file_path como caminho se for str/bytes/Path
    # (tipos CONCRETOS, não o protocolo os.PathLike — um MagicMock satisfaz
    # isinstance(_, os.PathLike) por implementar __fspath__ automaticamente).
    # Um objeto truthy não-caminho (ex.: cfg.logging.file vindo de um
    # MagicMock em teste, ou um config malformado em produção) faria
    # Path(obj) criar diretórios de lixo em local arbitrário (ex.:
    # "MagicMock/mock.logging.file/<id>"). Nesse caso, pula o log em arquivo
    # (o log de console segue funcionando) em vez de escrever onde não deve.
    if file_path and isinstance(file_path, (str, bytes, Path)):
        file_handler: logging.FileHandler | None = _make_file_handler(Path(file_path), fmt)
        # Fallback para um caminho garantidamente do usuário quando o
        # configurado não é gravável. Bug real (Beelink): logging.file default
        # é "./logs/bauer.log" — RELATIVO ao cwd — e ali o `./logs` pertencia
        # ao root, então FileHandler estourava PermissionError e derrubava
        # `bauer doctor` inteiro com traceback. Log é caminho ACESSÓRIO: nunca
        # deve quebrar o comando (ver AGENTS.md).
        if file_handler is None:
            from .paths import get_bauer_home
            fallback = get_bauer_home() / "logs" / "bauer.log"
            if fallback != Path(file_path):
                file_handler = _make_file_handler(fallback, fmt)
        if file_handler is not None:
            logger.addHandler(file_handler)

    return logger


def _make_file_handler(
    path: Path, fmt: logging.Formatter
) -> "logging.FileHandler | None":
    """Cria um FileHandler ou devolve None se o caminho não for gravável.

    `FileHandler.__init__` ABRE o arquivo (mkdir do pai + open), então tanto o
    mkdir quanto a abertura podem levantar OSError/PermissionError. Sem este
    guard, qualquer caminho de log não-gravável derrubava o processo inteiro —
    e log é acessório, deve degradar para só-console, não crashar.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(fmt)
        return handler
    except OSError as exc:
        # stderr direto, não via logger: o logger ainda está sendo montado.
        print(
            f"[bauer] aviso: nao foi possivel escrever log em '{path}' ({exc}). "
            "Seguindo apenas com log de console.",
            file=sys.stderr,
        )
        return None


def get_logger(name: str = "bauer") -> logging.Logger:
    return logging.getLogger(name)


def log_suppressed(context: str, exc: BaseException, *, logger_name: str = "bauer") -> None:
    """Loga uma excecao suprimida em DEBUG para diagnosabilidade.

    Use em lugar de `except Exception: pass` quando a supressao e intencional
    mas voce quer rastro em modo debug. O chamador nao e interrompido.

    Exemplo:
        except Exception as exc:
            log_suppressed("learning_engine.append_entry", exc)
    """
    log = logging.getLogger(logger_name)
    log.debug("[suprimido] %s: %s(%s)", context, type(exc).__name__, exc)
