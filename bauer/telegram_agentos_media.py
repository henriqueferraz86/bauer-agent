"""Bounded local text ingestion for the experimental AgentOS channel.

Only explicit UTF-8 ``.txt`` documents are converted to prompt text. Binary
media is refused; no local path, Telegram bot token, or filename is included
in the resulting message. Persist validated metadata before acknowledging the
Telegram update and persist resulting text before submitting the remote run.
This module does not send data to AgentOS itself.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

import httpx

from .secrets_scanner import has_secrets

MAX_DOCUMENT_BYTES = 96 * 1024
MAX_CAPTION_BYTES = 4096
_SAFE_REMOTE_PATH = re.compile(r"[A-Za-z0-9_./-]+\Z")
_TELEGRAM_TOKEN_PATH = re.compile(r"((?:/file)?/bot)[^/\s'\"]+/")


class _TelegramLogFilter(logging.Filter):
    """Avoid HTTP client logs exposing the credential embedded in a URL."""

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        safe = _TELEGRAM_TOKEN_PATH.sub(r"\1[REDACTED]/", rendered)
        if safe != rendered:
            record.msg, record.args = safe, ()
        return True


_LOG_FILTER = _TelegramLogFilter()


def _protect_http_logs() -> None:
    # httpx logs the request URL at INFO, including the Telegram token. The
    # filter is attached at the emitting logger, not at a parent logger.
    logging.getLogger("httpx").addFilter(_LOG_FILTER)
    for name in ("httpcore", "httpcore.http11", "httpcore.http2", "httpcore.connection", "httpcore.proxy"):
        logging.getLogger(name).addFilter(_LOG_FILTER)


class AgentOSMediaError(ValueError):
    """A document cannot be safely ingested; message contains no secrets."""


class AgentOSMediaTransient(AgentOSMediaError):
    """Temporary download failure; retrying the GET is safe."""


def validate_text_document(message: dict[str, Any]) -> dict[str, Any]:
    """Validate and minimize the metadata persisted before Telegram ACK."""
    if not isinstance(message, dict) or any(message.get(k) for k in ("photo", "audio", "voice", "video")):
        raise AgentOSMediaError("Somente documento de texto é aceito neste modo.")
    doc = message.get("document")
    if not isinstance(doc, dict):
        raise AgentOSMediaError("Documento ausente ou inválido.")
    name, file_id = doc.get("file_name"), doc.get("file_id")
    mime = doc.get("mime_type") or "text/plain"
    if (not isinstance(name, str) or not name or "/" in name or "\\" in name
            or not name.lower().endswith(".txt") or name.startswith(".") or len(name) > 200
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name) or has_secrets(name)
            or not isinstance(file_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", file_id)
            or not isinstance(mime, str) or mime.split(";", 1)[0].strip().lower() != "text/plain"):
        raise AgentOSMediaError("Somente arquivos .txt de texto são aceitos.")
    declared = doc.get("file_size")
    if isinstance(declared, bool) or not isinstance(declared, int) or not 0 < declared <= MAX_DOCUMENT_BYTES:
        raise AgentOSMediaError("O documento excede o limite ou não informa tamanho válido.")
    caption = message.get("caption") or ""
    if not isinstance(caption, str):
        raise AgentOSMediaError("Legenda inválida ou grande demais.")
    try:
        caption_size = len(caption.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise AgentOSMediaError("Legenda inválida ou grande demais.") from None
    if caption_size > MAX_CAPTION_BYTES or any(
        (ord(c) < 32 and c not in "\r\n\t") or 127 <= ord(c) <= 159 for c in caption
    ):
        raise AgentOSMediaError("Legenda inválida ou grande demais.")
    if has_secrets(caption):
        raise AgentOSMediaError("Legenda contém possível segredo; não foi enviada.")
    return {"document": {"file_id": file_id, "file_name": name, "file_size": declared,
                         "mime_type": "text/plain"}, "caption": caption}


def extract_text_document(message: dict[str, Any], *, get_file: Callable[[str], Any],
                          http_client: httpx.Client, bot_token: str) -> str:
    """Download a small text document in bounded chunks and reject secrets.

    Metadata and response size are both enforced. This scanner is heuristic,
    not proof that the content has no sensitive information.
    """
    clean = validate_text_document(message)
    file_id, declared = clean["document"]["file_id"], clean["document"]["file_size"]
    caption = clean["caption"]
    if not isinstance(bot_token, str) or not re.fullmatch(r"[A-Za-z0-9_:-]+", bot_token):
        raise AgentOSMediaError("Não foi possível obter o documento.")
    _protect_http_logs()
    try:
        info = get_file(file_id)
        if not isinstance(info, dict):
            raise AgentOSMediaError("Metadados do documento inválidos.")
        path, expected = info.get("file_path"), info.get("file_size")
        if (not isinstance(path, str) or not _SAFE_REMOTE_PATH.fullmatch(path)
                or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/"))
                or isinstance(expected, bool) or not isinstance(expected, int)
                or expected != declared or expected > MAX_DOCUMENT_BYTES):
            raise AgentOSMediaError("Metadados do documento inválidos.")
        url = f"https://api.telegram.org/file/bot{bot_token}/{path}"
        pieces: list[bytes] = []
        size = 0
        with http_client.stream("GET", url, follow_redirects=False, timeout=120,
                                headers={"Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise AgentOSMediaError("Codificação de documento não suportada.")
            for chunk in response.iter_raw(chunk_size=16 * 1024):
                size += len(chunk)
                if size > MAX_DOCUMENT_BYTES or size > declared:
                    raise AgentOSMediaError("O documento excede o tamanho informado.")
                pieces.append(chunk)
        if size != declared:
            raise AgentOSMediaTransient("O documento foi recebido incompleto; download será repetido.")
        content = b"".join(pieces).decode("utf-8-sig", errors="strict")
    except AgentOSMediaError:
        raise
    except UnicodeDecodeError:
        raise AgentOSMediaError("Documento não é UTF-8 válido.") from None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 429 and exc.response.status_code < 500:
            raise AgentOSMediaError("Não foi possível ler o documento de texto.") from None
        raise AgentOSMediaTransient("Download temporariamente indisponível.") from None
    except Exception as exc:  # noqa: BLE001 — never expose Telegram URL containing the bot token
        code = getattr(exc, "error_code", None)
        if isinstance(code, int) and not isinstance(code, bool) and 400 <= code < 500 and code != 429:
            raise AgentOSMediaError("Telegram recusou o documento; não foi enviado.") from None
        raise AgentOSMediaTransient("Download temporariamente indisponível.") from None
    if (not content.strip() or any((ord(c) < 32 and c not in "\r\n\t") or 127 <= ord(c) <= 159 for c in content)
            or has_secrets(content) or has_secrets(caption)):
        raise AgentOSMediaError("Documento vazio, inválido ou com possível segredo; não foi enviado.")
    caption_line = f"Legenda: {caption.strip()}\n" if caption.strip() else ""
    return f"[Documento de texto fornecido pelo usuário]\n{caption_line}{content}"
