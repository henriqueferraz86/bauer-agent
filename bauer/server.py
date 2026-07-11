"""Bauer Agent — modo servidor HTTP (Fases A2/A3/A4/A5).

Endpoints:
  GET  /health                 — liveness check
  GET  /status                 — modelo, contexto, tools
  GET  /tools                  — lista tools disponíveis
  GET  /metrics                — métricas Prometheus (text/plain)
  GET  /sessions               — lista sessões ativas  [auth]
  DELETE /sessions/{id}        — remove sessão         [auth]
  POST /chat                   — envia mensagem, recebe resposta completa [auth]
  POST /transcribe             — transcreve áudio (multipart) em texto [auth]
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

import hmac
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

# Timeout de turno (wall-clock) do loop de tool-calling em /stream — sem isso,
# uma sessao que entra num loop de tool calls (ou uma chamada de LLM/tool
# travada) fica pendurada pra sempre: a SSE nunca fecha, a UI mostra
# "gerando..." indefinidamente e nao ha nenhum sinal de erro. Mesma logica do
# BAUER_GATEWAY_TURN_TIMEOUT do gateway (channel_base.py), aplicada aqui pro
# /stream do bauer serve.
#
# 300s (5min), nao 120s: trabalho de dev real (scaffolding, varios arquivos,
# builds) passa de 2min com modelos mais lentos (ex. deepseek via OpenRouter
# fazendo varias rodadas de tool call) — o timeout curto cortava turnos que
# estavam progredindo normalmente, so mais devagar.
_STREAM_TURN_TIMEOUT_SECONDS = int(os.environ.get("BAUER_SERVE_TURN_TIMEOUT", "300"))

# Teto de chars do PROJECT.md auto-injetado por turno (B da "memória por
# projeto"). Cabeçalho, não o arquivo inteiro: é pago a cada turno e prompt
# longo degrada modelos fracos (mesma razão do cap de skill em skill_match).
# O resto fica sob demanda via read_file. Ajustável por env.
_PROJECT_BRIEF_CAP = int(os.environ.get("BAUER_SERVE_PROJECT_BRIEF_CHARS", "1500"))

# Prefetch de memória (decisões + sessões) por turno. Roda SÍNCRONO no handler
# do request, antes do primeiro byte — com busca semântica que pode custar até
# ~2s no pior caso (projeto com decisions.db grande). Ligado por padrão; quem
# priorizar time-to-first-token pode desligar via env.
_MEMORY_PREFETCH_ENABLED = os.environ.get(
    "BAUER_SERVE_MEMORY_PREFETCH", "1"
).strip().lower() not in ("0", "false", "no", "")

# Modo da Policy Engine no serve (B0 — governança). Regras default classificam
# shell.execute/filesystem.delete como "ask"; num serve local de um operador só,
# bloquear todo shell quebraria o uso normal — daí o default "audit":
#   off     — não avalia policy (comportamento pré-B0).
#   audit   — avalia e emite policy.evaluated (popula o audit report); "deny"
#             BLOQUEIA (segurança real, ex.: exfiltração de segredo); "ask" PASSA
#             com registro approval.accepted (auto — operador presente).
#   enforce — "ask" BLOQUEIA e cria approval pendente (para uso desatendido ou
#             quando existir fluxo de aprovar-e-retomar no chat).
_SERVE_POLICY_MODE = os.environ.get("BAUER_SERVE_POLICY", "audit").strip().lower()
if _SERVE_POLICY_MODE not in ("off", "audit", "enforce"):
    _SERVE_POLICY_MODE = "audit"


def _sse(data: str, event: str | None = None) -> str:
    """Codifica um evento SSE preservando quebras de linha.

    O protocolo SSE exige que cada linha do payload tenha seu próprio prefixo
    ``data:`` — um ``\\n`` cru dentro de ``data: {texto}`` corrompe o frame e o
    cliente descarta tudo que vier depois da primeira linha (era assim que o
    /stream colapsava markdown inteiro numa linha só)."""
    lines = "".join(f"data: {ln}\n" for ln in data.split("\n"))
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}{lines}\n"


class _StreamGate:
    """Retém trechos do stream que podem ser um tool-call JSON.

    Modelos bridge (sem tool calling nativo) às vezes narram antes de emitir o
    JSON da action (``Vou verificar…{"action": ...}`` ou dentro de fence
    ```` ```json ````). Sem retenção, o JSON cru vaza para o chat antes de o
    turno terminar e a action ser parseada. A gate segura o texto a partir de
    um candidato (``{`` ou ```` ``` ````); se a janela seguinte não contém
    ``"action"``, era falso alarme e o trecho é liberado."""

    _PROBE = 96  # chars após o marcador para decidir se parece uma action

    def __init__(self) -> None:
        self.pending = ""
        self.sent_any = False

    def _candidate_idx(self) -> int:
        idxs = [i for i in (self.pending.find("{"), self.pending.find("```")) if i != -1]
        return min(idxs) if idxs else -1

    def feed(self, chunk: str) -> str:
        """Acumula um chunk e devolve a parte segura para enviar ao cliente."""
        self.pending += chunk
        out: list[str] = []
        while True:
            idx = self._candidate_idx()
            if idx == -1:
                out.append(self.pending)
                self.pending = ""
                break
            out.append(self.pending[:idx])
            self.pending = self.pending[idx:]
            probe = self.pending[: self._PROBE]
            if '"action"' in probe or len(self.pending) < self._PROBE:
                # Candidato plausível (ou cedo demais para saber) — retém.
                break
            # Falso alarme (ex.: "{{.ServerVersion}}" num comando docker):
            # libera o 1º char do marcador e re-escaneia o restante.
            out.append(self.pending[0])
            self.pending = self.pending[1:]
        text = "".join(out)
        self.sent_any = self.sent_any or bool(text)
        return text


def _strip_action_block(text: str, available: set) -> str:
    """Remove TODOS os JSON de action (e os fences markdown ao redor) do texto.

    Usado no flush da _StreamGate: a narração retida junto com o(s) JSON(s) vai
    para o chat, o JSON não. Modelos que emitem vários tool calls numa única
    resposta (batch, intercalados com prosa) deixariam o 2º, 3º… vazando se
    removêssemos só o primeiro — daí o scan varrer a string inteira."""
    import json as _json
    import re as _re

    def _is_action(obj) -> bool:
        # Envelope de tool call do modelo: {"action": "...", "args": {...}}.
        # Casa tools conhecidas OU qualquer objeto no formato action+args —
        # mostrar JSON de tool call cru no chat nunca é desejável, mesmo que a
        # tool não esteja disponível neste router (o modelo pode alucinar uma).
        if not isinstance(obj, dict) or not isinstance(obj.get("action"), str):
            return False
        return obj["action"] in available or "args" in obj

    decoder = _json.JSONDecoder()
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "{":
            try:
                obj, end = decoder.raw_decode(text, i)
                if _is_action(obj):
                    i = end
                    continue
            except _json.JSONDecodeError:
                pass
        out.append(text[i])
        i += 1
    result = "".join(out)
    # Fences que ficaram vazios após remover o JSON de dentro (```json``` ).
    result = _re.sub(r"```(?:json)?\s*```", "", result)
    # Colapsa as linhas em branco extras deixadas pelos blocos removidos.
    result = _re.sub(r"\n{3,}", "\n\n", result)
    return result


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

    def to_prometheus(self, model: str = "", provider: str = "", runtime: dict | None = None) -> str:
        """Serializa metricas no formato Prometheus text exposition."""
        uptime = time.time() - self._start_time
        runtime = runtime or {}
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
            "",
            "# HELP bauer_runs_total Total de runs registradas",
            "# TYPE bauer_runs_total counter",
            f'bauer_runs_total {int(runtime.get("runs_total", 0))}',
            "",
            "# HELP bauer_runs_active Runs em execucao ou aguardando aprovacao",
            "# TYPE bauer_runs_active gauge",
            f'bauer_runs_active {int(runtime.get("runs_active", 0))}',
            "",
            "# HELP bauer_runs_failed_total Total de runs com falha",
            "# TYPE bauer_runs_failed_total counter",
            f'bauer_runs_failed_total {int(runtime.get("runs_failed_total", 0))}',
            "",
            "# HELP bauer_approvals_pending Aprovacoes pendentes",
            "# TYPE bauer_approvals_pending gauge",
            f'bauer_approvals_pending {int(runtime.get("approvals_pending", 0))}',
            "",
            "# HELP bauer_policy_denied_total Total de decisoes de policy negadas",
            "# TYPE bauer_policy_denied_total counter",
            f'bauer_policy_denied_total {int(runtime.get("policy_denied_total", 0))}',
            "",
            "# HELP bauer_skill_executions_total Total de execucoes de skill",
            "# TYPE bauer_skill_executions_total counter",
            f'bauer_skill_executions_total {int(runtime.get("skill_executions_total", 0))}',
            "",
            "# HELP bauer_agent_runtime_adapter_calls_total Total de chamadas a runtime adapters",
            "# TYPE bauer_agent_runtime_adapter_calls_total counter",
            f'bauer_agent_runtime_adapter_calls_total {int(runtime.get("agent_runtime_adapter_calls_total", 0))}',
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
    config_path: Optional[Path] = None,
    fallback_clients: list | None = None,
):
    """Cria e retorna o app FastAPI configurado."""
    _require_fastapi()

    import json
    import logging

    from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
    from fastapi.responses import FileResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel as PydanticModel

    from .agent import run_one_turn, run_one_turn_with_fallback

    _fallback_clients = fallback_clients or []
    from .context_manager import ContextManager
    from .core.events import EventBus
    from .core.observability import AuditLog, RunTraceStore
    from .core.runtime.run_manager import RunManager
    from .core.runtime.session_manager import SessionManager
    from .session_store import SessionStore

    _access_logger = logging.getLogger("bauer.access")
    _log = logging.getLogger("bauer.server")

    # --- schemas (definidas fora de qualquer função para Pydantic resolver corretamente) ---

    class ChatRequest(PydanticModel):
        message: str
        session_id: Optional[str] = None
        project_id: Optional[str] = None

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
        # Wildcard "*" e allow_credentials=True são incompatíveis pela spec CORS:
        # o navegador rejeita e o Starlette ecoa a origin em vez de "*".
        # Com wildcard, desabilita credentials para retornar "*" corretamente.
        _wildcard = "*" in cors_origins
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=not _wildcard,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    if enable_gzip:
        from fastapi.middleware.gzip import GZipMiddleware
        app.add_middleware(GZipMiddleware, minimum_size=1000)

    store = SessionStore(sessions_dir)
    runtime_root = sessions_dir.parent / "runtime"
    event_bus = EventBus(root=runtime_root)
    run_manager = RunManager(root=runtime_root, event_bus=event_bus)
    session_manager = SessionManager(root=runtime_root)
    from .core.policy import ApprovalManager
    approval_manager = ApprovalManager(root=runtime_root, event_bus=event_bus)
    audit_log = AuditLog(event_bus.store)
    trace_store = RunTraceStore(event_bus.store)
    from .core.runtime.autonomy import BudgetManager
    budget_manager = BudgetManager(root=runtime_root, event_bus=event_bus)

    def _wire_router_to_serve(r) -> None:
        """Aponta o EventBus/policy_root de um ToolRouter para os DESTE serve.

        Sem isto, cada ToolRouter usa um EventBus próprio rooteado em
        `<workspace>.parent/runtime` (ToolRouter.__init__) — e todo tool call
        publica `tool.call.completed` nesse bus (tool_router._publish_tool_event).
        Para o router default o serve já sobrescrevia; para os routers de
        PROJETO (Fase 1) não sobrescrevia, então a atividade de tool dos turnos
        por-projeto ia pra um store diferente e sumia da Observabilidade/`/audit`
        do serve. Aplicar em TODO router (default + projeto) fecha esse buraco."""
        try:
            r._event_bus = event_bus  # type: ignore[attr-defined]
            r._policy_root = runtime_root  # type: ignore[attr-defined]
            # B0: liga a avaliação de policy no serve (allow/ask/deny auditáveis).
            # Modo controla o que "ask" faz — ver _SERVE_POLICY_MODE.
            r._policy_enabled = _SERVE_POLICY_MODE != "off"  # type: ignore[attr-defined]
            r._policy_mode = _SERVE_POLICY_MODE  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            from .logging_config import log_suppressed
            log_suppressed("serve.wire_router", exc)

    _wire_router_to_serve(router)

    # ── Router-por-projeto (Fase 1 do isolamento por projeto) ────────────────
    #
    # v1: todo projeto compartilha o MESMO llm_client/config do serve — só o
    # workspace muda (sandbox/policy/kanban/audit ficam confinados na pasta do
    # projeto). Modelo/policy por-projeto é uma fase futura.
    def _build_project_router(project_path: Path):
        from .commands._runtime import _build_router as _build_scoped_router
        from .config_loader import load_config as _load_cfg
        cfg = None
        if config_path is not None:
            try:
                cfg = _load_cfg(config_path)
            except Exception as exc:  # noqa: BLE001
                _log.debug("project router: falha ao carregar config: %s", exc)
        proj_router = _build_scoped_router(
            cfg, project_path,
            llm_client=getattr(router, "_llm_client", None),
        )
        _wire_router_to_serve(proj_router)  # eventos vão pro store do serve
        return proj_router

    from .project_routers import ProjectRouterCache
    project_router_cache = ProjectRouterCache(router, _build_project_router)

    def _resolve_project_router(sid: "str | None", explicit_project_id: "str | None"):
        """(router, project_id) para este turno.

        Precedência: project_id explícito no request > projeto já fixado
        NESTA sessão (sticky — grava na 1ª vez que resolve, nunca troca
        sozinho depois) > projeto ativo global (registry). `project_id` no
        retorno é None quando o router usado é o default (nada para taggear).
        """
        pid = (explicit_project_id or "").strip() or None
        if not pid and sid:
            try:
                existing = session_manager.get_session(sid)
                pid = (existing.state or {}).get("project_id") if existing else None
            except Exception as exc:  # noqa: BLE001
                _log.debug("project resolve: sessão indisponível: %s", exc)
        if not pid:
            try:
                from . import projects_registry as pr
                pid = pr.get_active()
            except Exception as exc:  # noqa: BLE001
                _log.debug("project resolve: registry indisponível: %s", exc)
        resolved_router = project_router_cache.get(pid)
        active_project_id = pid if resolved_router is not router else None
        return resolved_router, active_project_id

    def _current_system_prompt() -> str:
        """Refresh request-scoped prompt data such as current date/time."""
        try:
            from .agent import _build_system_prompt

            return _build_system_prompt(router)
        except Exception:
            return system_prompt

    def _new_context() -> ContextManager:
        return ContextManager(
            applied_context=applied_context,
            system_prompt=_current_system_prompt(),
        )

    def _active_project_hint(effective_workspace: Path) -> "str | None":
        """Bloco de contexto que direciona o turno para a pasta do projeto ativo.

        Fase 0 (steer suave): o botão "Ativar" da tela Projetos grava o projeto
        ativo no registry; aqui traduzimos isso num aviso efêmero para o modelo
        manter a edição de arquivos DENTRO da subpasta do projeto. NÃO é parede.

        Desde a Fase 1 (router-por-projeto, ver project_routers.py), a maioria
        dos casos nem chega a precisar deste aviso: quando o projeto ativo
        resolve para um ToolRouter próprio, `effective_workspace` JÁ é a pasta
        do projeto — a sandbox real supre a necessidade do nudge, e a função
        devolve None (nada a "direcionar": já se está lá). Este bloco só
        dispara no caminho de FALLBACK: o projeto está ativo mas, por algum
        motivo (pasta sumida, sensível, cache falhou), o turno segue no router
        default — aí o nudge ainda ajuda, mesmo sem ser uma parede real."""
        try:
            from . import projects_registry as pr

            pid = pr.get_active()
            if not pid:
                return None
            entry = pr.get_project(pid)
            if not entry:
                return None
            proj = Path(entry["path"]).resolve()
            try:
                rel = proj.relative_to(effective_workspace.resolve())
            except ValueError:
                return None  # projeto fora do workspace efetivo deste turno
            rel_str = str(rel).replace("\\", "/")
            if rel_str in (".", ""):
                return None  # já é a raiz do workspace efetivo: nada a direcionar
            name = entry.get("name") or proj.name
            return (
                "<projeto-ativo>\n"
                f"[Projeto ativo: '{name}' — pasta `{rel_str}/` dentro do workspace.]\n"
                f"Trabalhe DENTRO dessa pasta: ao criar, ler ou editar arquivos, "
                f"use caminhos começando por `{rel_str}/` "
                f"(ex.: `{rel_str}/src/App.tsx`). Só use outra pasta se o usuário "
                f"pedir explicitamente.\n"
                "</projeto-ativo>"
            )
        except Exception as exc:  # noqa: BLE001 — hint é auxílio; nunca quebra o turno
            _log.debug("active project hint failed: %s", exc)
            return None

    def _project_brief_block(effective_workspace: Path) -> "str | None":
        """(B) Auto-carrega o PROJECT.md do projeto como brief/convenções do turno.

        Cabeçalho com teto (não o arquivo inteiro — é pago a cada turno e
        modelos fracos sofrem com prompt longo; mesma razão do cap de skill).
        Se truncar, aponta o read_file pro resto (barato, está na sandbox).
        Enquadrado como REGRA a seguir (≠ memória, que é referência). Pula o
        placeholder auto-gerado pelo `bauer project init` (sem conteúdo real)."""
        try:
            pf = Path(effective_workspace) / "PROJECT.md"
            if not pf.is_file():
                return None
            text = pf.read_text(encoding="utf-8", errors="replace").strip()
            # Placeholder do `bauer project init` (descrição vazia) → não injeta.
            if not text or len(text) < 40 or "Sem descricao." in text:
                return None
            truncated = len(text) > _PROJECT_BRIEF_CAP
            head = text[:_PROJECT_BRIEF_CAP].rstrip()
            note = (
                "\n\n(PROJECT.md continua — use `read_file` com path 'PROJECT.md' "
                "para ver o restante.)" if truncated else ""
            )
            return (
                "<projeto-brief>\n"
                "[Contexto e convenções deste projeto (PROJECT.md). SIGA-AS ao "
                "trabalhar aqui, salvo instrução explícita em contrário.]\n"
                f"{head}{note}\n"
                "</projeto-brief>"
            )
        except Exception as exc:  # noqa: BLE001 — brief é auxílio; nunca quebra o turno
            _log.debug("project brief injection failed: %s", exc)
            return None

    def _memory_context_block(message: str, effective_workspace: Path) -> "str | None":
        """(A) Prefetch de memória do projeto — paridade com o CLI.

        Busca decisões passadas (decisions.db) + sessões similares, ambas já
        escopadas na pasta do projeto (Fase 1), e devolve o bloco
        <memory-context> pronto. O serve não fazia isso — toda a memória do
        projeto era invisível pro chat web. Desligável via
        BAUER_SERVE_MEMORY_PREFETCH=0 (roda síncrono no request; ver constante)."""
        if not _MEMORY_PREFETCH_ENABLED:
            return None
        try:
            from .memory_context import prefetch_memory_context

            return prefetch_memory_context(message, str(effective_workspace))
        except Exception as exc:  # noqa: BLE001 — memória é auxílio; nunca quebra o turno
            _log.debug("memory prefetch failed: %s", exc)
            return None

    def _resolve_request_context(message: str, effective_workspace: Path) -> dict:
        resolved: dict = {"agent_id": "", "agent": None, "skill": None}
        try:
            from .agent_registry import match_agents, merged_specialist_pool, resolve_user_agents_path

            agent = match_agents(message, merged_specialist_pool(resolve_user_agents_path()))
            if agent is not None:
                resolved["agent_id"] = agent.name
                resolved["agent"] = agent
        except Exception as exc:
            _log.debug("agent match failed: %s", exc)
        try:
            from .skill_match import match_skill

            resolved["skill"] = match_skill(message)
        except Exception as exc:
            _log.debug("skill match failed: %s", exc)
        resolved["project_hint"] = _active_project_hint(effective_workspace)
        resolved["project_brief"] = _project_brief_block(effective_workspace)
        resolved["memory_context"] = _memory_context_block(message, effective_workspace)
        return resolved

    def _apply_request_context(ctx: ContextManager, resolved: dict) -> None:
        # Ordem: brief estável do projeto → especialista → skill → memória
        # (referência) → nudge de pasta (fallback). Os mais "sempre-ligados"
        # e estáveis primeiro.
        project_brief = resolved.get("project_brief")
        if project_brief:
            ctx.add_ephemeral_system(project_brief)
        agent = resolved.get("agent")
        if agent is not None:
            ctx.add_ephemeral_system(
                "<agent-especialista>\n"
                f"[Agent '{agent.name}' selecionado automaticamente para este turno.]\n"
                f"{agent.system}\n"
                "</agent-especialista>"
            )
        skill = resolved.get("skill")
        if skill is not None:
            try:
                from .skill_match import skill_injection_block

                ctx.add_ephemeral_system(skill_injection_block(skill))
            except Exception as exc:
                _log.debug("skill injection failed: %s", exc)
        memory_context = resolved.get("memory_context")
        if memory_context:
            ctx.add_ephemeral_system(memory_context)
        project_hint = resolved.get("project_hint")
        if project_hint:
            ctx.add_ephemeral_system(project_hint)

    def _run_input(message: str, endpoint: str, resolved: dict) -> dict:
        payload = {"message": message, "endpoint": endpoint}
        if resolved.get("agent_id"):
            payload["selected_agent"] = resolved["agent_id"]
        skill = resolved.get("skill")
        if skill is not None:
            payload["selected_skill"] = getattr(skill, "name", "")
            payload["selected_skill_score"] = getattr(skill, "score", None)
        if resolved.get("project_id"):
            # Runs continuam GLOBAIS (não uma lista por projeto) — só marcadas
            # com o project_id pra permitir filtrar depois na Observabilidade.
            payload["project_id"] = resolved["project_id"]
        return payload

    def _publish_selected_skill(run_id: str, session_id: str, agent_id: str, resolved: dict) -> None:
        skill = resolved.get("skill")
        if skill is None:
            return
        event_bus.publish(
            "skill.selected",
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            skill_id=getattr(skill, "name", ""),
            status="selected",
            data={
                "score": getattr(skill, "score", None),
                "source": getattr(skill, "source", ""),
            },
        )

    def _format_server_response(response: str) -> str:
        text = str(response or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
        return text

    class _TurnCostRecorder:
        """Sink do cost_meter para um turno do serve.

        run_one_turn reporta cada LLM call em report_llm_cost; sem um sink
        instalado o serve nunca registrava custo/tokens — a Observabilidade e
        o budget do painel ficavam eternamente em zero. Cada call vira uma
        linha no cost_history.jsonl e o total do turno alimenta o ledger de
        budget (BudgetManager) + cost_estimate da run."""

        def __init__(self, session_id: str):
            self.session_id = session_id
            self.total_usd = 0.0

        def __call__(self, provider: str, model: str, usage: dict, cost_usd: float) -> None:
            self.total_usd += float(cost_usd or 0.0)
            try:
                from .cost_tracker import record_llm_usage
                record_llm_usage(self.session_id, provider, model, usage, cost_usd)
            except Exception:
                pass

    def _record_turn_budget(recorder: "_TurnCostRecorder", run_id: str, agent_id: str) -> None:
        if recorder.total_usd <= 0:
            return
        try:
            budget_manager.record_run_cost(
                run_id=run_id, agent_id=agent_id, cost_usd=recorder.total_usd,
            )
        except Exception as exc:  # noqa: BLE001 — medição nunca derruba o turno
            _log.debug("budget record falhou: %s", exc)

    def _runtime_metrics_snapshot() -> dict:
        runs = run_manager.list_runs()
        events = event_bus.list_events()
        approvals = approval_manager.list()
        return {
            "runs_total": len(runs),
            "runs_active": sum(1 for run in runs if run.status in {"queued", "running", "waiting_approval"}),
            "runs_failed_total": sum(1 for run in runs if run.status == "failed"),
            "approvals_pending": sum(1 for approval in approvals if approval.status == "pending"),
            "policy_denied_total": sum(
                1
                for event in events
                if event.event_type == "policy.evaluated" and event.status == "deny"
            ),
            "skill_executions_total": sum(1 for event in events if event.event_type == "skill.executed"),
            "agent_runtime_adapter_calls_total": sum(1 for event in events if event.event_type == "run.created"),
        }

    # Detecta provider inicial pelo atributo _provider ou, como fallback, pela URL do host.
    def _detect_provider(c) -> str:
        # Só aceita _provider quando é string não-vazia (evita falsos truthy,
        # ex: atributos auto-criados de mocks/proxies).
        p = getattr(c, "_provider", "")
        if isinstance(p, str) and p:
            return p
        host = getattr(c, "host", "")
        host = host.lower() if isinstance(host, str) else ""
        for kw in ("openrouter", "groq", "mistral", "deepseek", "together", "openai",
                   "anthropic", "xai", "github", "opencode", "gemini"):
            if kw in host:
                return kw
        if hasattr(c, "list_models"):  # OllamaClient
            return "ollama"
        return ""

    # Estado mutável do modelo ativo e client (permite troca em runtime via /models/switch)
    _state = {
        "model": model_name,
        "client": client,
        "provider": _detect_provider(client),
    }

    # ── Roteamento por-turno (Fase 12 / Sprint 34c) — opt-in ──────────────────
    # Quando model.router_enabled=True e há profiles, cada turno escolhe o modelo
    # do tier (fast/balanced/coding/heavy) via classify_task heurístico. CONSERVADOR:
    # tier sem profile, provider sem client, ou qualquer falha → cai no modelo
    # primário (_state). Nunca degrada silenciosamente para um modelo fraco.
    _router_enabled = False
    _router_profiles: dict = {}
    _router_cfg = None
    try:
        if config_path is not None:
            from .config_loader import load_config as _load_cfg
            from .model_router import profiles_from_config
            _router_cfg = _load_cfg(config_path)
            _router_enabled = bool(getattr(_router_cfg.model, "router_enabled", False))
            _router_profiles = profiles_from_config(_router_cfg)
    except Exception as exc:  # noqa: BLE001
        _log.debug("router config load failed: %s", exc)
    _profile_clients: dict = {}  # provider → client (cache)

    def _client_for_profile(provider: str):
        """Client para o provider do profile. Reusa o default se mesmo provider;
        senão constrói e cacheia. None em falha (caller cai no default)."""
        if not provider or provider == _state["provider"]:
            return _state["client"]
        if provider in _profile_clients:
            return _profile_clients[provider]
        try:
            from .commands._runtime import _build_client as _bc
            from .config_loader import BauerConfig
            from .env_loader import apply_env_to_config
            raw = _router_cfg.model_dump()
            raw["model"]["provider"] = provider
            vcfg = BauerConfig(**raw)
            apply_env_to_config(vcfg)
            c = _bc(vcfg)
            _profile_clients[provider] = c
            return c
        except Exception as exc:  # noqa: BLE001
            _log.debug("build profile client failed (%s): %s", provider, exc)
            return None

    def _resolve_turn_model(message: str):
        """(client, model, decision). Sem routing / na dúvida → (primário, None)."""
        if not _router_enabled or not _router_profiles:
            return _state["client"], _state["model"], None
        try:
            from .model_router import decide
            d = decide(message, _router_profiles)
        except Exception:  # noqa: BLE001
            return _state["client"], _state["model"], None
        if not d.model:
            return _state["client"], _state["model"], None  # tier sem profile → default
        c = _client_for_profile(d.provider)
        if c is None:
            return _state["client"], _state["model"], None  # sem client → default
        return c, d.model, d

    def _publish_route(run_id: str, sid: str, agent_id: str, decision) -> None:
        if decision is None:
            return
        try:
            event_bus.publish(
                "model.route.selected",
                run_id=run_id, session_id=sid, agent_id=agent_id,
                status=decision.profile, message=decision.reason,
                data={"task_type": decision.task_type, "complexity": decision.complexity,
                      "tier": decision.profile, "provider": decision.provider, "model": decision.model},
            )
        except Exception as exc:  # noqa: BLE001
            from .logging_config import log_suppressed
            log_suppressed("serve.publish_route", exc)

    # Rate limiter (desativado se rate_limit_requests <= 0)
    _limiter = _RateLimiter(
        max_requests=rate_limit_requests,
        window_s=rate_limit_window_s,
    )

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        # A SPA buildada (Vite) referencia seus chunks em /assets/* a partir da
        # raiz — monta esse diretório direto para o index.html servido em "/"
        # encontrar JS/CSS/fontes (sem isso a página fica em branco: 404 nos assets).
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

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
        if not hmac.compare_digest(incoming or "", api_key):
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
    def status(_: None = Depends(_verify_key)):
        return {
            "model": _state["model"],
            "provider": _state["provider"],
            "context_tokens": applied_context,
            "tools": router.available_tools(),
            "auth_enabled": bool(api_key),
        }

    @app.get("/metrics", include_in_schema=False)
    def metrics(_: None = Depends(_verify_key)):
        """Endpoint Prometheus — retorna métricas em text exposition format."""
        from fastapi.responses import PlainTextResponse
        text = _metrics.to_prometheus(
            model=_state["model"],
            provider=_provider_name,
            runtime=_runtime_metrics_snapshot(),
        )
        return PlainTextResponse(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/tools")
    def tools_list(_: None = Depends(_verify_key)):
        return [router.tool_info(name) for name in router.available_tools()]

    @app.get("/models")
    def models_list(_: None = Depends(_verify_key)):
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
        new_provider = (body.get("provider") or "").strip().lower()
        if not new_model:
            raise HTTPException(status_code=400, detail="Campo 'model' obrigatorio.")

        current_provider = _state["provider"]
        # Normaliza: se não veio provider, assume o atual
        if not new_provider:
            new_provider = current_provider

        if new_provider == current_provider:
            # Mesmo provider — valida com has_model() só para Ollama
            if current_provider in ("ollama", "") and not _state["client"].has_model(new_model):
                raise HTTPException(
                    status_code=404,
                    detail=f"Modelo '{new_model}' nao encontrado no {current_provider or 'provider atual'}.",
                )
            _state["model"] = new_model
        else:
            # Provider diferente — tenta reconstruir o client
            new_client = None
            if config_path is not None:
                try:
                    from .auxiliary_client import _build_client_for_provider
                    from .config_loader import load_config
                    cfg = load_config(config_path)
                    new_client = _build_client_for_provider(new_provider, new_model, cfg)
                except Exception as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Nao foi possivel construir client para provider '{new_provider}': {exc}",
                    )
            else:
                # Sem config_path — aceita a troca mas mantém o client atual
                # (o chat vai falhar se o provider for incompatível)
                pass

            _state["model"] = new_model
            _state["provider"] = new_provider
            if new_client is not None:
                _state["client"] = new_client

        return {"active": _state["model"], "provider": _state["provider"]}

    @app.get("/sessions")
    def list_sessions(_: None = Depends(_verify_key)):
        return {"sessions": store.list_sessions()}

    @app.get("/events")
    def list_events(limit: int = Query(100, ge=1, le=1000), _: None = Depends(_verify_key)):
        return {"events": [EventBus.to_dict(event) for event in event_bus.list_events(limit=limit)]}

    @app.get("/runs")
    def list_runs(_: None = Depends(_verify_key)):
        from dataclasses import asdict
        return {"runs": [asdict(run) for run in run_manager.list_runs()]}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, _: None = Depends(_verify_key)):
        from dataclasses import asdict
        run = run_manager.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' nao encontrada.")
        return asdict(run)

    @app.get("/runs/{run_id}/events")
    def list_run_events(run_id: str, _: None = Depends(_verify_key)):
        return {"events": [EventBus.to_dict(event) for event in event_bus.list_events(run_id=run_id)]}

    @app.get("/runs/{run_id}/trace")
    def get_run_trace(run_id: str, _: None = Depends(_verify_key)):
        if run_manager.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' nao encontrada.")
        return trace_store.get_trace(run_id)

    @app.get("/audit")
    def list_audit(
        run_id: str = Query("", description="filtra por run_id"),
        limit: int = Query(100, ge=1, le=1000),
        _: None = Depends(_verify_key),
    ):
        return {
            "audit": [
                AuditLog.to_dict(record)
                for record in audit_log.list_records(run_id=run_id or None, limit=limit)
            ]
        }

    @app.get("/audit/report")
    def audit_report_endpoint(
        last: str = Query("24h", description="janela: 24h, 7d, 2w"),
        _: None = Depends(_verify_key),
    ):
        from dataclasses import asdict
        from datetime import datetime, timedelta
        import re
        from .core.audit import build_report

        match = re.fullmatch(r"(\d+)([mhdw])", last.strip().lower())
        if not match:
            raise HTTPException(status_code=400, detail="Use janela como 24h, 7d ou 2w.")
        amount, unit = int(match.group(1)), match.group(2)
        delta = {
            "m": timedelta(minutes=amount), "h": timedelta(hours=amount),
            "d": timedelta(days=amount), "w": timedelta(weeks=amount),
        }[unit]
        return asdict(build_report(runtime_root, since=datetime.now() - delta, window_label=last))

    @app.get("/audit/runs/{run_id}")
    def audit_run_endpoint(run_id: str, _: None = Depends(_verify_key)):
        from dataclasses import asdict
        from .core.audit import audit_run

        audited = audit_run(runtime_root, run_id, include_events=True, include_tools=True)
        if audited is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' nao encontrada.")
        return asdict(audited)

    @app.get("/audit/runs/{run_id}/score")
    def audit_score_endpoint(run_id: str, _: None = Depends(_verify_key)):
        from dataclasses import asdict
        from .core.audit import score_run_by_id

        score = score_run_by_id(runtime_root, run_id)
        if score is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' nao encontrada.")
        return asdict(score)

    @app.get("/audit/skills/insights")
    def audit_skill_insights_endpoint(
        last: str = Query("7d", description="janela: 24h, 7d, 2w"),
        _: None = Depends(_verify_key),
    ):
        from dataclasses import asdict
        from datetime import datetime, timedelta
        import re
        from .core.audit import build_skill_insights

        match = re.fullmatch(r"(\d+)([mhdw])", last.strip().lower())
        if not match:
            raise HTTPException(status_code=400, detail="Use janela como 24h, 7d ou 2w.")
        amount, unit = int(match.group(1)), match.group(2)
        delta = {
            "m": timedelta(minutes=amount), "h": timedelta(hours=amount),
            "d": timedelta(days=amount), "w": timedelta(weeks=amount),
        }[unit]
        return asdict(build_skill_insights(
            runtime_root,
            since=datetime.now() - delta,
            window_label=last,
            suggest_new=True,
        ))

    @app.get("/approvals")
    def list_approvals(status: str = Query("", description="pending | approved | denied"), _: None = Depends(_verify_key)):
        from dataclasses import asdict
        return {"approvals": [asdict(record) for record in approval_manager.list(status=status or None)]}

    @app.post("/approvals/{approval_id}/approve")
    def approve_request(approval_id: str, _: None = Depends(_verify_key)):
        from dataclasses import asdict
        try:
            return asdict(approval_manager.approve(approval_id))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' nao encontrado.")

    @app.post("/approvals/{approval_id}/deny")
    def deny_request(approval_id: str, _: None = Depends(_verify_key)):
        from dataclasses import asdict
        try:
            return asdict(approval_manager.deny(approval_id))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' nao encontrado.")

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
        active_router, active_project_id = _resolve_project_router(session_id, req.project_id)
        resolved = _resolve_request_context(req.message, Path(active_router.workspace))
        resolved["project_id"] = active_project_id
        request_agent_id = resolved.get("agent_id") or "serve.chat"
        session_state = {"transport": "http", "endpoint": "/chat"}
        if active_project_id:
            session_state["project_id"] = active_project_id  # sticky: fixa nesta sessão
        session_manager.get_or_create_session(
            session_id,
            agent_id=request_agent_id,
            state=session_state,
        )
        run = run_manager.create_run(
            session_id=session_id,
            agent_id=request_agent_id,
            runtime_adapter="bauer_native",
            input=_run_input(req.message, "/chat", resolved),
            status="running",
        )

        ctx = _new_context()
        ctx.messages = store.load(session_id)
        _apply_request_context(ctx, resolved)
        _publish_selected_skill(run.id, session_id, request_agent_id, resolved)
        ctx.add_user(req.message)

        from .cost_meter import cost_sink
        from .tool_router import reset_runtime_ids, set_runtime_ids
        _turn_client, _turn_model, _route = _resolve_turn_model(req.message)
        _publish_route(run.id, session_id, request_agent_id, _route)
        _cost = _TurnCostRecorder(session_id)
        _cost_token = cost_sink.set(_cost)
        _ids_token = set_runtime_ids(session_id, run.id)
        try:
            response, tool_log = run_one_turn_with_fallback(
                ctx, active_router, _turn_client, _turn_model, _fallback_clients,
            )
        except Exception as exc:
            _log.exception("Erro interno em /chat: %s", exc)
            run_manager.fail_run(run.id, str(exc))
            raise HTTPException(status_code=500, detail="Erro interno — consulte os logs do servidor.")
        finally:
            cost_sink.reset(_cost_token)
            reset_runtime_ids(_ids_token)

        _metrics.tool_calls_total += len(tool_log)
        _record_turn_budget(_cost, run.id, request_agent_id)
        response = _format_server_response(response)
        store.save(session_id, ctx.messages)
        session_manager.touch_session(session_id, state={"last_run_id": run.id})
        run_manager.complete_run(
            run.id,
            output={"response": response},
            tool_calls_count=len(tool_log),
            cost_estimate=round(_cost.total_usd, 6),
        )

        return ChatResponse(
            response=response,
            session_id=session_id,
            model=_state["model"],
            tool_calls=[ToolCallLog(**t) for t in tool_log],
        )

    @app.post("/transcribe")
    async def transcribe(file: UploadFile = File(...), _: None = Depends(_verify_key)):
        """Transcreve áudio (gravado no microfone da UI) usando o mesmo pipeline
        STT do gateway (Groq/OpenAI Whisper ou faster-whisper local, conforme
        STT_PROVIDER) — ver bauer/transcription.py."""
        import tempfile

        from .transcription import transcribe_audio

        suffix = Path(file.filename or "audio.webm").suffix or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = Path(tmp.name)
        try:
            result = transcribe_audio(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        if not result["success"]:
            raise HTTPException(status_code=422, detail=result["error"])
        return {"transcript": result["transcript"], "provider": result["provider"]}

    @app.get("/stream")
    def stream(
        message: str = Query(..., description="Mensagem do usuario"),
        session_id: Optional[str] = Query(None, description="ID de sessao existente"),
        project_id: Optional[str] = Query(None, description="Projeto explicito (sobrepoe sessao/ativo global)"),
        _: None = Depends(_verify_key),
    ):
        """Resposta em tempo real via Server-Sent Events (SSE)."""
        _metrics.stream_requests_total += 1
        sid = session_id or store.new_id()
        active_router, active_project_id = _resolve_project_router(sid, project_id)
        resolved = _resolve_request_context(message, Path(active_router.workspace))
        resolved["project_id"] = active_project_id
        request_agent_id = resolved.get("agent_id") or "serve.stream"
        session_state = {"transport": "sse", "endpoint": "/stream"}
        if active_project_id:
            session_state["project_id"] = active_project_id  # sticky: fixa nesta sessão
        session_manager.get_or_create_session(
            sid,
            agent_id=request_agent_id,
            state=session_state,
        )
        run = run_manager.create_run(
            session_id=sid,
            agent_id=request_agent_id,
            runtime_adapter="bauer_native",
            input=_run_input(message, "/stream", resolved),
            status="running",
        )

        ctx = _new_context()
        ctx.messages = store.load(sid)
        _apply_request_context(ctx, resolved)
        _publish_selected_skill(run.id, sid, request_agent_id, resolved)
        ctx.add_user(message)

        # Roteamento por-turno resolvido AQUI (fora da thread): o par
        # (client, model) é capturado pelo worker; a decisão vira evento SSE
        # `route` no gerador + evento de runtime na Observabilidade.
        _turn_client, _turn_model, _route = _resolve_turn_model(message)
        _publish_route(run.id, sid, request_agent_id, _route)

        def _event_stream():
            # O turno roda no MESMO motor do CLI e do POST /chat —
            # run_one_turn_with_fallback: native function calling quando o
            # provider suporta, batch de tool calls, guardrails, dedup, timeout
            # de tool, recovery de resposta vazia, detecção de loop e fallback
            # de provider. Antes o /stream reimplementava um mini-loop próprio
            # (só tool-bridge, 1 tool por rodada, sem nada disso), e o
            # comportamento do agente divergia visivelmente do `bauer agent`.
            #
            # Padrão do gateway (channel_base._execute_turn): o turno roda numa
            # thread daemon com um delta-sink instalado (ContextVar não cruza
            # threads); os eventos chegam por uma fila e viram frames SSE aqui.
            import json as _json
            import queue as _queue
            import threading as _threading

            from .agent import _extract_text_from_pseudo_json, run_one_turn_with_fallback

            events: "_queue.Queue[tuple[str, str]]" = _queue.Queue()
            result: dict = {}

            class _QueueSink:
                def on_delta(self, chunk: str) -> None:
                    events.put(("delta", chunk))

                def on_round(self) -> None:
                    events.put(("round", ""))

                def on_tool(self, name: str) -> None:
                    events.put(("tool", name))

            _cost = _TurnCostRecorder(sid)

            def _worker() -> None:
                from .cost_meter import cost_sink
                from .delta_stream import reset_sink, set_sink
                from .tool_router import reset_runtime_ids, set_runtime_ids
                token = set_sink(_QueueSink())
                cost_token = cost_sink.set(_cost)
                # ContextVar (não atributo de instância): active_router pode ser
                # o router de projeto, reusado por outra sessão/turno concorrente
                # no MESMO projeto — instalar aqui (nesta thread) evita a corrida
                # de duas threads pisando no session/run id uma da outra.
                ids_token = set_runtime_ids(sid, run.id)
                try:
                    resp, tool_log = run_one_turn_with_fallback(
                        ctx, active_router, _turn_client, _turn_model, _fallback_clients,
                    )
                    result["response"] = resp
                    result["tool_log"] = tool_log
                except BaseException as exc:  # noqa: BLE001 — repassa ao gerador
                    result["error"] = exc
                finally:
                    cost_sink.reset(cost_token)
                    reset_sink(token)
                    reset_runtime_ids(ids_token)
                    # Persistência ÚNICA aqui (não no gerador): se o turno estourar
                    # o _STREAM_TURN_TIMEOUT_SECONDS, o gerador já retornou e a SSE
                    # já fechou, mas esta thread (órfã) continua rodando até o fim
                    # do turno — write_file/run_command executam de verdade. Sem
                    # isso, o `store.save` do timeout salvava um ctx.messages
                    # META-DO-CAMINHO (perdendo os tool calls que rodaram DEPOIS do
                    # corte), e a run ficava marcada "failed" mesmo quando o
                    # trabalho terminou com sucesso — daí o usuário via "cancelei"
                    # mas o arquivo já tinha sido criado (só a 2ª mensagem via o
                    # resultado). Persistir aqui, sempre, faz a sessão e a run
                    # convergirem para o estado real assim que o turno de fato
                    # acaba — mesmo com o cliente HTTP já desconectado.
                    try:
                        current = run_manager.get_run(run.id)
                        already_cancelled = current is not None and current.status == "cancelled"
                        store.save(sid, ctx.messages)
                        session_manager.touch_session(sid, state={"last_run_id": run.id})
                        if not already_cancelled:
                            if "error" in result:
                                run_manager.fail_run(run.id, str(result["error"]))
                            else:
                                _record_turn_budget(_cost, run.id, request_agent_id)
                                _tlog = result.get("tool_log") or []
                                _metrics.tool_calls_total += len(_tlog)
                                run_manager.complete_run(
                                    run.id,
                                    output={"response": _format_server_response(str(result.get("response", "")))},
                                    tool_calls_count=len(_tlog),
                                    cost_estimate=round(_cost.total_usd, 6),
                                )
                    except Exception:  # noqa: BLE001 — persistência nunca derruba a thread
                        _log.exception("Falha ao persistir turno /stream (run %s)", run.id)
                    events.put(("end", ""))

            # Sinaliza a skill auto-selecionada para a UI (paridade com a linha
            # "↳ skill 'X' (NN%)" que o CLI imprime). O conteúdo da skill já foi
            # injetado no contexto por _apply_request_context; aqui é só o aviso
            # visível de que ela disparou.
            _selected_skill = resolved.get("skill")
            if _selected_skill is not None:
                yield _sse(
                    _json.dumps({
                        "name": getattr(_selected_skill, "name", ""),
                        "score": getattr(_selected_skill, "score", None),
                    }, ensure_ascii=False),
                    event="skill",
                )

            # Modelo roteado deste turno (S34c) — indicador na UI.
            if _route is not None:
                yield _sse(
                    _json.dumps({"tier": _route.profile, "model": _route.model,
                                 "task_type": _route.task_type}, ensure_ascii=False),
                    event="route",
                )

            worker = _threading.Thread(
                target=_worker, name=f"bauer-stream:{sid[:8]}", daemon=True,
            )
            worker.start()

            available = set(active_router.available_tools())
            gate = _StreamGate()  # retém possíveis tool-call JSON (ver docstring)
            emitted_any = False   # já mandamos texto ao cliente (p/ separador)
            turn_sep = False      # próximo texto abre um bloco novo (pós-tool)
            round_raw: list[str] = []  # deltas crus da rodada corrente do LLM
            deadline = time.monotonic() + _STREAM_TURN_TIMEOUT_SECONDS

            def _flush_gate() -> str:
                leftover = _strip_action_block(gate.pending, available).strip("\n")
                gate.pending = ""
                return leftover

            def _emit_text(text: str):
                nonlocal emitted_any, turn_sep
                if not text.strip():
                    return None
                if turn_sep:
                    text = "\n\n" + text.lstrip("\n")
                    turn_sep = False
                emitted_any = True
                return _sse(text)

            ended = False
            while not ended:
                try:
                    kind, payload = events.get(timeout=1.0)
                except _queue.Empty:
                    # Sem eventos: checa cancelamento e timeout de turno. Em
                    # ambos os casos a thread órfã continua até o fim do turno
                    # (mesma limitação do gateway) — só paramos de streamar.
                    current_run = run_manager.get_run(run.id)
                    if current_run is not None and current_run.status == "cancelled":
                        store.save(sid, ctx.messages)
                        session_manager.touch_session(sid, state={"last_run_id": run.id})
                        yield _sse(sid, event="done")
                        return
                    if time.monotonic() > deadline:
                        yield _sse(
                            "⏱ Essa tarefa passou de "
                            f"{_STREAM_TURN_TIMEOUT_SECONDS}s e a conexão foi "
                            "encerrada, mas o trabalho pode ter continuado (e "
                            "concluído) em segundo plano — ações como criar/editar "
                            "arquivos já executadas não são desfeitas. Confira o "
                            "resultado antes de repetir o pedido, ou dê uma olhada "
                            "no workspace/Kanban."
                        )
                        store.save(sid, ctx.messages)
                        session_manager.touch_session(sid, state={"last_run_id": run.id})
                        run_manager.fail_run(run.id, "stream turn timeout")
                        yield _sse(sid, event="done")
                        return
                    continue

                if kind == "delta":
                    round_raw.append(payload)
                    frame = _emit_text(gate.feed(payload))
                    if frame:
                        yield frame
                elif kind == "round":
                    # Nova chamada ao LLM: o que sobrou da rodada anterior é
                    # narração misturada com JSON de actions — manda a narração.
                    frame = _emit_text(_flush_gate())
                    if frame:
                        yield frame
                    round_raw = []
                    turn_sep = emitted_any
                elif kind == "tool":
                    # Narração pendente sai antes do chip da tool.
                    frame = _emit_text(_flush_gate())
                    if frame:
                        yield frame
                    # Narração de fase (S37): além do nome cru, manda o passo
                    # humano ("Executando comando") + ícone para a UI mostrar.
                    try:
                        from .core.ux import tool_phase
                        _ph = tool_phase(payload)
                        _tool_data = _json.dumps(
                            {"name": payload, "label": _ph.label, "icon": _ph.icon},
                            ensure_ascii=False,
                        )
                    except Exception:  # noqa: BLE001 — fallback para o nome cru
                        _tool_data = _json.dumps({"name": payload})
                    yield _sse(_tool_data, event="tool")
                else:  # "end"
                    ended = True

                if time.monotonic() > deadline and not ended:
                    yield _sse(
                        "⏱ Essa tarefa passou de "
                        f"{_STREAM_TURN_TIMEOUT_SECONDS}s e a conexão foi "
                        "encerrada, mas o trabalho pode ter continuado (e "
                        "concluído) em segundo plano — ações como criar/editar "
                        "arquivos já executadas não são desfeitas. Confira o "
                        "resultado antes de repetir o pedido, ou dê uma olhada "
                        "no workspace/Kanban."
                    )
                    store.save(sid, ctx.messages)
                    session_manager.touch_session(sid, state={"last_run_id": run.id})
                    run_manager.fail_run(run.id, "stream turn timeout")
                    yield _sse(sid, event="done")
                    return

            # Persistência (sessão + status/custo da run) já foi feita no
            # `finally` do _worker acima — é a ÚNICA fonte de verdade, inclusive
            # quando o timeout dispara e este trecho abaixo nunca roda (a thread
            # segue sozinha até o fim e persiste por conta própria). Aqui só
            # resta emitir o texto final ao cliente ainda conectado.
            if "error" in result:
                yield _sse(f"[Erro: {result['error']}]")
                yield _sse(sid, event="done")
                return

            raw_response = str(result.get("response", ""))

            # Cauda: o que ficou retido na gate + resposta que não passou pelo
            # stream (native tool calling não emite deltas de texto; sínteses
            # como "[Loop detectado]" e recovery também chegam só no retorno).
            tail = _flush_gate()
            if raw_response.strip() and raw_response.strip() != "".join(round_raw).strip():
                final_text = _extract_text_from_pseudo_json(raw_response) or raw_response
                final_text = _strip_action_block(final_text, available).strip("\n")
                tail = f"{tail}\n\n{final_text}" if tail.strip() else final_text
            elif not emitted_any and not tail.strip():
                # Rodada única toda retida na gate (ex.: pseudo-JSON de conversa
                # de modelos pequenos) — extrai o texto e manda.
                clean = _extract_text_from_pseudo_json(raw_response)
                if clean:
                    tail = clean
            frame = _emit_text(tail)
            if frame:
                yield frame

            yield _sse(sid, event="done")

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={"X-Session-ID": sid, "X-Bauer-Run-ID": run.id},
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
        last_user_message = next(
            (msg.content for msg in reversed(req.messages) if msg.role == "user"),
            "",
        )
        # /v1 (Claw3D/OpenAI-compat) fica FORA do router-por-projeto (Fase 1) —
        # API externa, sem noção de "projeto ativo" da UI desktop; sempre usa
        # o router default do serve.
        resolved = _resolve_request_context(last_user_message, Path(router.workspace))
        request_agent_id = resolved.get("agent_id") or "serve.openai"
        session_manager.get_or_create_session(
            sid,
            agent_id=request_agent_id,
            state={"transport": "openai", "endpoint": "/v1/chat/completions"},
        )

        ctx = _new_context()
        ctx.messages = store.load(sid)

        # Adiciona todas as mensagens do request ao contexto
        # (ignora mensagens de sistema — já está no system_prompt)
        for msg in req.messages:
            if msg.role == "user":
                ctx.add_user(msg.content)
            elif msg.role == "assistant":
                ctx.add_assistant(msg.content)

        _turn_client, active_model, _v1_route = _resolve_turn_model(last_user_message)
        completion_id = f"chatcmpl-bauer-{_uuid.uuid4().hex[:12]}"
        run = run_manager.create_run(
            session_id=sid,
            agent_id=request_agent_id,
            runtime_adapter="bauer_native",
            input={
                "messages": [msg.model_dump() for msg in req.messages],
                "stream": req.stream,
                "endpoint": "/v1/chat/completions",
                "message": last_user_message,
                "selected_agent": resolved.get("agent_id") or "",
                "selected_skill": getattr(resolved.get("skill"), "name", ""),
            },
            status="running",
        )
        _publish_selected_skill(run.id, sid, request_agent_id, resolved)
        _publish_route(run.id, sid, request_agent_id, _v1_route)
        resp_headers = {"X-Hermes-Session-Id": sid, "X-Bauer-Run-ID": run.id}

        # ── modo streaming ────────────────────────────────────────────────────
        if req.stream:
            _metrics.stream_requests_total += 1

            def _oai_stream():
                from .agent import _try_parse_tool, MAX_TOOL_TURNS
                from .tool_router import reset_runtime_ids, set_runtime_ids

                # ContextVar (não atributo de instância): /v1 usa o router
                # default, que pode rodar concorrente com /chat//stream — mutar
                # a instância vazaria o id de um request pro outro. Instala aqui
                # (na thread que roda o gerador e executa as tools).
                _ids_token = set_runtime_ids(sid, run.id)
                tool_count = 0
                parts: list[str] = []
                try:
                    while True:
                        current_run = run_manager.get_run(run.id)
                        if current_run is not None and current_run.status == "cancelled":
                            store.save(sid, ctx.messages)
                            session_manager.touch_session(sid, state={"last_run_id": run.id})
                            yield "data: [DONE]\n\n"
                            return
                        parts = []
                        try:
                            for chunk in _turn_client.chat_stream(active_model, ctx.get_payload()):
                                current_run = run_manager.get_run(run.id)
                                if current_run is not None and current_run.status == "cancelled":
                                    store.save(sid, ctx.messages)
                                    session_manager.touch_session(sid, state={"last_run_id": run.id})
                                    yield "data: [DONE]\n\n"
                                    return
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
                            session_manager.touch_session(sid, state={"last_run_id": run.id})
                            run_manager.fail_run(run.id, str(exc))
                            yield "data: [DONE]\n\n"
                            return

                        response = _format_server_response("".join(parts))
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
                            session_manager.touch_session(sid, state={"last_run_id": run.id})
                            run_manager.complete_run(
                                run.id,
                                output={"response": response},
                                tool_calls_count=tool_count,
                            )
                            yield "data: [DONE]\n\n"
                            break
                finally:
                    reset_runtime_ids(_ids_token)

            return StreamingResponse(
                _oai_stream(),
                media_type="text/event-stream",
                headers=resp_headers,
            )

        # ── modo não-streaming (resposta completa) ────────────────────────────
        from .cost_meter import cost_sink
        from .tool_router import reset_runtime_ids, set_runtime_ids
        _cost = _TurnCostRecorder(sid)
        _cost_token = cost_sink.set(_cost)
        _ids_token = set_runtime_ids(sid, run.id)
        try:
            response, tool_log = run_one_turn(ctx, router, _turn_client, active_model)
        except Exception as exc:
            _log.exception("Erro interno em /v1/chat/completions: %s", exc)
            run_manager.fail_run(run.id, str(exc))
            raise HTTPException(status_code=500, detail="Erro interno — consulte os logs do servidor.")
        finally:
            cost_sink.reset(_cost_token)
            reset_runtime_ids(_ids_token)

        _metrics.tool_calls_total += len(tool_log)
        _record_turn_budget(_cost, run.id, request_agent_id)
        response = _format_server_response(response)
        store.save(sid, ctx.messages)
        session_manager.touch_session(sid, state={"last_run_id": run.id})
        run_manager.complete_run(
            run.id,
            output={"response": response},
            tool_calls_count=len(tool_log),
            cost_estimate=round(_cost.total_usd, 6),
        )

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

    # --- Desktop API (SPA das 8 telas) ------------------------------------------
    try:
        from .desktop_api import build_desktop_router

        # workspace/config REAIS deste serve — sem isso o router usa defaults
        # relativos ao cwd e o Kanban/Projetos leem um workspace diferente do
        # que as tools kanban_*/write_file do chat escrevem.
        _dsk_workspace = getattr(router, "workspace", None)

        def _kanban_project_workspace(pid: "str | None") -> Path:
            # Mesma resolução do chat (Fase 1): project_id explícito > ativo
            # global > default. Sem sessão aqui (o painel não tem session_id),
            # então passa sid=None — cai direto no ativo global do registry.
            proj_router, _ = _resolve_project_router(None, pid)
            return Path(proj_router.workspace)

        app.include_router(build_desktop_router(
            verify_key=_verify_key,
            runtime_root=runtime_root,
            get_workspace=(lambda: _dsk_workspace) if _dsk_workspace else None,
            get_config_path=(lambda: config_path) if config_path else None,
            resolve_project_workspace=_kanban_project_workspace,
        ))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("bauer.server").warning(
            "Desktop API não montada: %s", exc
        )

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
