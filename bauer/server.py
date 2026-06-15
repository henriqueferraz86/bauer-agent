"""Bauer Agent — modo servidor HTTP (Fases A2/A3/A4/A5).

Endpoints:
  GET  /health                 — liveness check
  GET  /status                 — modelo, contexto, tools
  GET  /tools                  — lista tools disponíveis
  GET  /metrics                — métricas Prometheus (text/plain)
  GET  /sessions               — lista sessões ativas  [auth]
  DELETE /sessions/{id}        — remove sessão         [auth]
  POST /chat                   — envia mensagem, recebe resposta completa [auth]
  GET  /stream?message=&session_id=  — resposta em tempo real via SSE [auth]

  # OpenAI-compatible (Claw3D / virtual office integration)
  POST /v1/chat/completions    — OpenAI-compat, stream ou batch [auth]
  GET  /v1/models              — lista modelos disponíveis

Auth: header X-API-Key ou Authorization: Bearer <key>
      Ignorado se serve.api_key estiver vazio no config.yaml.

Rate Limiting: configurável via config.yaml → serve.rate_limit
  requests: 60     # máximo de requisições por janela
  window_s: 60     # janela de tempo em segundos
  Retorna 429 Too Many Requests quando excedido.
  Desativado se requests <= 0.

Claw3D / Virtual Office:
  Configure o floor "local-runtime" no Claw3D com:
    url:         http://localhost:7770   (porta padrão do bauer serve)
    adapterType: custom
  O endpoint /v1/chat/completions segue o protocolo OpenAI SSE, compatível com
  hermes-gateway-adapter.js e qualquer cliente OpenAI-compatible.
  Header X-Hermes-Session-Id é honrado para retomada de sessão.
"""

import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional


# ─── Métricas globais em memória (Prometheus-style) ───────────────────────────

class _Metrics:
    """Contadores simples thread-safe via GIL (suficiente para uvicorn single-threaded)."""

    def __init__(self):
        self.requests_total: int = 0
        self.requests_errors: int = 0
        self.chat_requests_total: int = 0
        self.stream_requests_total: int = 0
        self.tool_calls_total: int = 0
        self.rate_limited_total: int = 0
        self._start_time: float = time.time()

    def to_prometheus(self, model: str = "", provider: str = "") -> str:
        """Serializa métricas no formato Prometheus text exposition."""
        uptime = time.time() - self._start_time
        lines = [
            "# HELP bauer_uptime_seconds Tempo em segundos desde o inicio do servidor",
            "# TYPE bauer_uptime_seconds gauge",
            f'bauer_uptime_seconds {uptime:.2f}',
            "",
            "# HELP bauer_requests_total Total de requisicoes HTTP recebidas",
            "# TYPE bauer_requests_total counter",
            f'bauer_requests_total {self.requests_total}',
            "",
            "# HELP bauer_requests_errors_total Total de erros HTTP (5xx)",
            "# TYPE bauer_requests_errors_total counter",
            f'bauer_requests_errors_total {self.requests_errors}',
            "",
            "# HELP bauer_chat_requests_total Total de chamadas ao endpoint /chat",
            "# TYPE bauer_chat_requests_total counter",
            f'bauer_chat_requests_total {self.chat_requests_total}',
            "",
            "# HELP bauer_stream_requests_total Total de chamadas ao endpoint /stream",
            "# TYPE bauer_stream_requests_total counter",
            f'bauer_stream_requests_total {self.stream_requests_total}',
            "",
            "# HELP bauer_tool_calls_total Total de tool calls executadas",
            "# TYPE bauer_tool_calls_total counter",
            f'bauer_tool_calls_total {self.tool_calls_total}',
            "",
            "# HELP bauer_rate_limited_total Total de requisicoes bloqueadas por rate limit",
            "# TYPE bauer_rate_limited_total counter",
            f'bauer_rate_limited_total {self.rate_limited_total}',
        ]
        if model:
            lines += [
                "",
                "# HELP bauer_info Informacoes do servidor (gauge constante = 1)",
                "# TYPE bauer_info gauge",
                f'bauer_info{{model="{model}",provider="{provider}"}} 1',
            ]
        return "\n".join(lines) + "\n"


_metrics = _Metrics()


class _RateLimiter:
    """Rate limiter em-memória baseado em sliding window por chave (IP ou API key).

    Thread-safe para uso com uvicorn (single-threaded async).
    Cada chave tem uma deque de timestamps das últimas requisições.
    """

    def __init__(self, max_requests: int = 60, window_s: float = 60.0):
        self.max_requests = max_requests
        self.window_s = window_s
        self._windows: dict[str, deque] = defaultdict(deque)

    def is_allowed(self, key: str) -> bool:
        """Verifica se a chave está dentro do limite. Registra a requisição se permitida."""
        if self.max_requests <= 0:
            return True  # desativado
        now = time.monotonic()
        window = self._windows[key]
        cutoff = now - self.window_s
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.max_requests:
            return False
        window.append(now)
        return True

    def retry_after(self, key: str) -> float:
        """Segundos até a próxima requisição ser permitida para a chave."""
        window = self._windows.get(key)
        if not window:
            return 0.0
        oldest = window[0]
        return max(0.0, (oldest + self.window_s) - time.monotonic())


def _require_fastapi():
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        raise ImportError(
            "FastAPI/uvicorn nao instalados.\n"
            "Instale com: pip install 'bauer-agent[server]'\n"
            "Ou: pip install fastapi uvicorn[standard]"
        )


def create_app(
    model_name: str,
    applied_context: int,
    router,
    client,
    system_prompt: str,
    sessions_dir: Path,
    api_key: str = "",
    rate_limit_requests: int = 60,
    rate_limit_window_s: float = 60.0,
    rate_limit_per_key: bool = False,
    cors_origins: list[str] | None = None,
    enable_gzip: bool = True,
    enable_access_log: bool = False,
):
    """Cria e retorna o app FastAPI configurado."""
    _require_fastapi()

    import json
    import logging

    from fastapi import Depends, FastAPI, HTTPException, Query, Request
    from fastapi.responses import FileResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel as PydanticModel

    from .agent import _build_system_prompt, run_one_turn
    from .context_manager import ContextManager
    from .session_store import SessionStore

    _access_logger = logging.getLogger("bauer.access")

    # --- schemas (definidas fora de qualquer função para Pydantic resolver corretamente) ---

    class ChatRequest(PydanticModel):
        message: str
        session_id: Optional[str] = None

    class ToolCallLog(PydanticModel):
        tool: str
        result: str

    class ChatResponse(PydanticModel):
        response: str
        session_id: str
        model: str
        tool_calls: list[ToolCallLog] = []

    app = FastAPI(
        title="Bauer Agent Server",
        version="0.1.0",
        description="Bauer Agent como API REST. Modelos locais via Ollama.",
    )

    # --- middleware setup (ordem importa: outer-first em FastAPI) ----------------

    if cors_origins:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    if enable_gzip:
        from fastapi.middleware.gzip import GZipMiddleware
        app.add_middleware(GZipMiddleware, minimum_size=1000)

    store = SessionStore(sessions_dir)

    # Estado mutável do modelo ativo (permite troca em runtime via /models/switch)
    _state = {"model": model_name}

    # Rate limiter (desativado se rate_limit_requests <= 0)
    _limiter = _RateLimiter(
        max_requests=rate_limit_requests,
        window_s=rate_limit_window_s,
    )

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/", include_in_schema=False)
        def web_ui():
            return FileResponse(static_dir / "index.html")

    # --- auth + rate limit -------------------------------------------------------

    def _get_client_ip(request: Request) -> str:
        """Extrai IP real do cliente, respeitando proxies."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _extract_incoming_key(request: Request) -> str:
        return (
            request.headers.get("X-API-Key")
            or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        )

    def _rate_limit_key(request: Request) -> str:
        if rate_limit_per_key:
            k = _extract_incoming_key(request)
            return f"key:{k}" if k else _get_client_ip(request)
        return _get_client_ip(request)

    def _check_rate_limit(request: Request) -> None:
        key = _rate_limit_key(request)
        if not _limiter.is_allowed(key):
            retry = _limiter.retry_after(key)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit excedido. Tente novamente em {retry:.0f}s.",
                headers={"Retry-After": str(int(retry) + 1)},
            )

    def _verify_key(request: Request) -> None:
        if not api_key:
            return
        incoming = _extract_incoming_key(request)
        if incoming != api_key:
            raise HTTPException(status_code=401, detail="API key invalida ou ausente.")

    # --- endpoints --------------------------------------------------------------

    # Provider name for metrics labels
    _provider_name = getattr(client, "_provider", None) or (
        "ollama" if hasattr(client, "host") and "ollama" in getattr(client, "host", "").lower()
        else "openai"
    )

    # Reset metrics on app start
    _metrics.__init__()

    @app.middleware("http")
    async def _metrics_middleware(request, call_next):
        _metrics.requests_total += 1

        # Global rate limit (applies to every route, even /health)
        if _limiter.max_requests > 0:
            from fastapi.responses import JSONResponse
            key = _rate_limit_key(request)
            if not _limiter.is_allowed(key):
                retry = _limiter.retry_after(key)
                _metrics.rate_limited_total += 1
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Rate limit excedido. Tente novamente em {retry:.0f}s."},
                    headers={"Retry-After": str(int(retry) + 1)},
                )

        t0 = time.monotonic()
        response = await call_next(request)
        if response.status_code >= 500:
            _metrics.requests_errors += 1
        if response.status_code == 429:
            _metrics.rate_limited_total += 1
        if enable_access_log:
            elapsed_ms = (time.monotonic() - t0) * 1000
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(elapsed_ms, 1),
                "client_ip": _get_client_ip(request),
                "user_agent": request.headers.get("User-Agent", ""),
            }
            _access_logger.info(json.dumps(record))
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "model": _state["model"]}

    @app.get("/status")
    def status():
        return {
            "model": _state["model"],
            "context_tokens": applied_context,
            "tools": router.available_tools(),
            "auth_enabled": bool(api_key),
        }

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        """Endpoint Prometheus — retorna métricas em text exposition format."""
        from fastapi.responses import PlainTextResponse
        text = _metrics.to_prometheus(model=_state["model"], provider=_provider_name)
        return PlainTextResponse(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/tools")
    def tools_list():
        return [router.tool_info(name) for name in router.available_tools()]

    @app.get("/models")
    def models_list():
        try:
            installed = client.list_models()
        except Exception:
            installed = []
        return {
            "active": _state["model"],
            "installed": installed,
        }

    @app.post("/models/switch")
    def models_switch(body: dict, _: None = Depends(_verify_key)):
        new_model = (body.get("model") or "").strip()
        if not new_model:
            raise HTTPException(status_code=400, detail="Campo 'model' obrigatorio.")
        if not client.has_model(new_model):
            raise HTTPException(status_code=404, detail=f"Modelo '{new_model}' nao encontrado no Ollama.")
        _state["model"] = new_model
        return {"active": new_model}

    @app.get("/sessions")
    def list_sessions(_: None = Depends(_verify_key)):
        return {"sessions": store.list_sessions()}

    @app.delete("/sessions/{session_id}")
    def delete_session(session_id: str, _: None = Depends(_verify_key)):
        deleted = store.delete(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Sessao '{session_id}' nao encontrada.")
        return {"deleted": session_id}

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, _: None = Depends(_verify_key)):
        _metrics.chat_requests_total += 1
        session_id = req.session_id or store.new_id()

        ctx = ContextManager(applied_context=applied_context, system_prompt=system_prompt)
        ctx.messages = store.load(session_id)
        ctx.add_user(req.message)

        try:
            response, tool_log = run_one_turn(ctx, router, client, _state["model"])
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        _metrics.tool_calls_total += len(tool_log)
        store.save(session_id, ctx.messages)

        return ChatResponse(
            response=response,
            session_id=session_id,
            model=_state["model"],
            tool_calls=[ToolCallLog(**t) for t in tool_log],
        )

    @app.get("/stream")
    def stream(
        message: str = Query(..., description="Mensagem do usuario"),
        session_id: Optional[str] = Query(None, description="ID de sessao existente"),
        _: None = Depends(_verify_key),
    ):
        """Resposta em tempo real via Server-Sent Events (SSE)."""
        _metrics.stream_requests_total += 1
        sid = session_id or store.new_id()

        ctx = ContextManager(applied_context=applied_context, system_prompt=system_prompt)
        ctx.messages = store.load(sid)
        ctx.add_user(message)

        def _event_stream():
            from .tool_router import SandboxError, ToolError
            from .agent import _try_parse_tool, _extract_text_from_pseudo_json, MAX_TOOL_TURNS

            tool_count = 0

            while True:
                parts: list[str] = []
                streaming = False   # True quando ja comecamos a enviar chunks ao cliente
                buffering = False   # True quando response pode ser JSON (aguarda completar)

                try:
                    for chunk in client.chat_stream(_state["model"], ctx.get_payload()):
                        parts.append(chunk)

                        if streaming:
                            yield f"data: {chunk}\n\n"
                        elif not buffering:
                            preview = "".join(parts).lstrip()
                            if preview.startswith("{") or preview.startswith("```"):
                                buffering = True
                            else:
                                for p in parts:
                                    yield f"data: {p}\n\n"
                                streaming = True
                except Exception as exc:
                    yield f"data: [Erro: {exc}]\n\n"
                    store.save(sid, ctx.messages)
                    yield f"event: done\ndata: {sid}\n\n"
                    return

                response = "".join(parts)
                ctx.add_assistant(response)

                action_dict = _try_parse_tool(response, router)
                if action_dict and tool_count < MAX_TOOL_TURNS:
                    action_name = action_dict.get("action", "?")
                    try:
                        tool_result = router.execute(action_dict)
                    except (ToolError, SandboxError) as exc:
                        tool_result = f"[Erro: {exc}]"

                    yield f"event: tool\ndata: {action_name}\n\n"
                    ctx.add_user(f"[Resultado de {action_name}]\n{tool_result}")
                    tool_count += 1
                    _metrics.tool_calls_total += 1
                else:
                    # Se estava bufferizando JSON de conversa, envia o texto extraído
                    if buffering:
                        clean = _extract_text_from_pseudo_json(response) or response
                        yield f"data: {clean}\n\n"
                    store.save(sid, ctx.messages)
                    yield f"event: done\ndata: {sid}\n\n"
                    break

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={"X-Session-ID": sid},
        )

    # ── OpenAI-compatible endpoint (Claw3D / virtual office) ─────────────────
    #
    # Protocolo idêntico ao usado pelo hermes-gateway-adapter.js:
    #   POST /v1/chat/completions
    #   Header X-Hermes-Session-Id  →  retomada de sessão
    #   stream: true  →  SSE: data: {"choices":[{"delta":{"content":"..."}}]}
    #                    finalizando com:  data: [DONE]
    #   stream: false →  JSON: {"id":..., "choices":[{"message":...}], "usage":...}

    class OAIMessage(PydanticModel):
        role: str
        content: str

    class OAICompletionRequest(PydanticModel):
        model: Optional[str] = None
        messages: list[OAIMessage]
        stream: bool = False
        session_id: Optional[str] = None    # campo body (ignorado em favor do header)
        max_tokens: Optional[int] = None
        temperature: Optional[float] = None

    @app.post("/v1/chat/completions")
    async def oai_chat_completions(
        req: OAICompletionRequest,
        request: Request,
        _: None = Depends(_verify_key),
    ):
        """Endpoint OpenAI-compatible para integração com Claw3D e outros clientes."""
        import json as _json
        import uuid as _uuid
        import time as _time

        _metrics.chat_requests_total += 1

        # Sessão: header tem prioridade sobre body
        sid = (
            request.headers.get("X-Hermes-Session-Id")
            or req.session_id
            or store.new_id()
        )

        ctx = ContextManager(applied_context=applied_context, system_prompt=system_prompt)
        ctx.messages = store.load(sid)

        # Adiciona todas as mensagens do request ao contexto
        # (ignora mensagens de sistema — já está no system_prompt)
        for msg in req.messages:
            if msg.role == "user":
                ctx.add_user(msg.content)
            elif msg.role == "assistant":
                ctx.add_assistant(msg.content)

        active_model = _state["model"]
        completion_id = f"chatcmpl-bauer-{_uuid.uuid4().hex[:12]}"
        resp_headers = {"X-Hermes-Session-Id": sid}

        # ── modo streaming ────────────────────────────────────────────────────
        if req.stream:
            _metrics.stream_requests_total += 1

            def _oai_stream():
                from .agent import _try_parse_tool, MAX_TOOL_TURNS
                tool_count = 0
                parts: list[str] = []

                while True:
                    parts = []
                    try:
                        for chunk in client.chat_stream(active_model, ctx.get_payload()):
                            parts.append(chunk)
                            # Emite chunk no formato OpenAI delta
                            delta = _json.dumps({
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": int(_time.time()),
                                "model": active_model,
                                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                            }, ensure_ascii=False)
                            yield f"data: {delta}\n\n"
                    except Exception as exc:
                        err = _json.dumps({"error": {"message": str(exc), "type": "server_error"}})
                        yield f"data: {err}\n\n"
                        store.save(sid, ctx.messages)
                        yield "data: [DONE]\n\n"
                        return

                    response = "".join(parts)
                    ctx.add_assistant(response)

                    action_dict = _try_parse_tool(response, router)
                    if action_dict and tool_count < MAX_TOOL_TURNS:
                        action_name = action_dict.get("action", "tool")
                        try:
                            tool_result = router.execute(action_dict)
                        except Exception as exc:
                            tool_result = f"[Erro: {exc}]"
                        # Emite evento de progresso de tool (formato Hermes)
                        tool_evt = _json.dumps({"tool": action_name, "label": action_name})
                        yield f"event: hermes.tool.progress\ndata: {tool_evt}\n\n"
                        ctx.add_user(f"[Resultado de {action_name}]\n{tool_result}")
                        tool_count += 1
                        _metrics.tool_calls_total += 1
                    else:
                        # Chunk final com finish_reason
                        final_delta = _json.dumps({
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": int(_time.time()),
                            "model": active_model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        })
                        yield f"data: {final_delta}\n\n"
                        store.save(sid, ctx.messages)
                        yield "data: [DONE]\n\n"
                        break

            return StreamingResponse(
                _oai_stream(),
                media_type="text/event-stream",
                headers=resp_headers,
            )

        # ── modo não-streaming (resposta completa) ────────────────────────────
        try:
            response, tool_log = run_one_turn(ctx, router, client, active_model)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        _metrics.tool_calls_total += len(tool_log)
        store.save(sid, ctx.messages)

        # Estima tokens (sem tokenizer real)
        prompt_tokens = sum(len(m.get("content", "")) // 4 for m in ctx.messages[:-1])
        completion_tokens = len(response) // 4

        oai_response = {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(_time.time()),
            "model": active_model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": response},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        from fastapi.responses import JSONResponse
        return JSONResponse(content=oai_response, headers=resp_headers)

    @app.get("/v1/models")
    def oai_models(_: None = Depends(_verify_key)):
        """Lista modelos disponíveis — formato OpenAI (para compatibilidade com clientes OAI)."""
        import time as _time
        return {
            "object": "list",
            "data": [
                {
                    "id": _state["model"],
                    "object": "model",
                    "created": int(_time.time()),
                    "owned_by": "bauer-agent",
                }
            ],
        }

    return app


def run_server(
    app,
    host: str = "0.0.0.0",
    port: int = 8000,
    pid_file: "Path | None" = None,
) -> None:
    import os
    from pathlib import Path as _Path
    _require_fastapi()
    import uvicorn
    if pid_file is not None:
        _Path(pid_file).parent.mkdir(parents=True, exist_ok=True)
        _Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")
    try:
        uvicorn.run(app, host=host, port=port)
    finally:
        if pid_file is not None:
            try:
                _Path(pid_file).unlink(missing_ok=True)
            except OSError:
                pass
