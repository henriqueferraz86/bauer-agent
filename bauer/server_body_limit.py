"""Limite de corpo HTTP aplicado ANTES de qualquer parser (WEB-10).

O FastAPI/Starlette materializa o corpo antes de o endpoint rodar: JSON vira
``bytes`` inteiros em memória e multipart é "spooled" para disco pelo parser.
Um limite checado dentro do endpoint (como o de ``/transcribe``) só rejeita
DEPOIS de o servidor já ter recebido tudo, e um ``Content-Length`` ausente
(chunked) ou mentiroso não é barrado por nada.

Este middleware ASGI puro envolve o app inteiro e conta os bytes que o servidor
entrega ao app em ``receive``:

- ``Content-Length`` declarado acima do limite → 413 sem ler um byte do corpo;
- ``Content-Length`` malformado → 400;
- corpo sem ``Content-Length`` (chunked) ou maior que o declarado → o
  ``receive`` contado interrompe o app no primeiro chunk que ultrapassa o
  limite, a resposta que o app tentar mandar é descartada e o cliente recebe
  413. Nenhum parser vê mais do que ``limite`` bytes.

O limite é por categoria de rota (``limite_para``), e não um teto único,
para não reprovar requests válidos: o ``/v1`` aceita até 200 mensagens de
200 mil caracteres pelo próprio schema; o ``/transcribe`` aceita o limite de
áudio dos providers mais o envelope multipart.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MIB = 1024 * 1024
#: teto padrão: muito acima do maior payload válido de /chat (100 mil
#: caracteres) e dos formulários da UI, e ainda limitado.
DEFAULT_MAX_BODY_BYTES = 4 * MIB
#: /v1/chat/completions: 200 mensagens x 200 mil caracteres (schema) cabem
#: em ~40 MB ASCII; o teto acompanha o contrato, não o reduz.
OPENAI_MAX_BODY_BYTES = 48 * MIB
#: envelope multipart (boundary, headers da parte) acima do áudio em si
MULTIPART_OVERHEAD_BYTES = 1 * MIB

_METODOS_COM_CORPO = {"POST", "PUT", "PATCH", "DELETE"}


def limite_para(path: str) -> int:
    """Limite em bytes do corpo bruto para ``path``."""
    if path == "/transcribe":
        from .transcription import MAX_AUDIO_BYTES
        return int(MAX_AUDIO_BYTES) + MULTIPART_OVERHEAD_BYTES
    if path == "/v1" or path.startswith("/v1/"):
        return OPENAI_MAX_BODY_BYTES
    return DEFAULT_MAX_BODY_BYTES


class _CorpoExcedido(Exception):
    """Interrompe o app quando o corpo ultrapassa o limite."""


async def _responder(send: Send, status: int, detalhe: str) -> None:
    import json

    corpo = json.dumps({"detail": detalhe}, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(corpo)).encode("ascii")),
            (b"connection", b"close"),
        ],
    })
    await send({"type": "http.response.body", "body": corpo, "more_body": False})


class BodySizeLimitMiddleware:
    """Middleware ASGI: corta o corpo da requisição no limite, antes do parser."""

    def __init__(self, app: ASGIApp, *,
                 limite_fn: Callable[[str], int] | None = None) -> None:
        self.app = app
        self.limite_fn = limite_fn or limite_para

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("method") not in _METODOS_COM_CORPO:
            await self.app(scope, receive, send)
            return

        limite = int(self.limite_fn(str(scope.get("path") or "")))
        declarado: str | None = None
        for nome, valor in scope.get("headers") or []:
            if nome.lower() == b"content-length":
                declarado = valor.decode("latin-1").strip()
                break
        if declarado is not None:
            if not declarado.isdigit():
                await _responder(send, 400, "Content-Length inválido.")
                return
            if int(declarado) > limite:
                await _responder(send, 413, _mensagem(limite))
                return

        recebido = 0
        excedido = False
        resposta_iniciada = False

        async def receive_contado() -> Message:
            nonlocal recebido, excedido
            if excedido:
                raise _CorpoExcedido()
            message = await receive()
            if message.get("type") == "http.request":
                recebido += len(message.get("body") or b"")
                if recebido > limite:
                    excedido = True
                    raise _CorpoExcedido()
            return message

        async def send_guardado(message: Message) -> None:
            nonlocal resposta_iniciada
            if excedido:
                return  # a resposta de erro do app é substituída pelo 413
            if message.get("type") == "http.response.start":
                resposta_iniciada = True
            await send(message)

        try:
            await self.app(scope, receive_contado, send_guardado)
        except BaseException:
            # _CorpoExcedido pode chegar embrulhado (ExceptionGroup do
            # BaseHTTPMiddleware) ou já convertido em 500 pelo
            # ServerErrorMiddleware, que re-levanta. O que decide é o flag.
            if not excedido:
                raise
        if excedido and not resposta_iniciada:
            await _responder(send, 413, _mensagem(limite))


def _mensagem(limite: int) -> str:
    if limite >= MIB:
        return f"Corpo da requisição excede o limite de {limite // MIB}MB."
    return f"Corpo da requisição excede o limite de {limite} bytes."
