"""Desktop API — endpoints REST/SSE que alimentam o Bauer Agent Desktop (SPA).

O `bauer serve` (server.py) já expõe chat/stream/models/sessions. Este módulo
adiciona um ``APIRouter`` montado em ``/api`` com os grupos que as 8 telas do
desktop precisam — todos *reusando* módulos existentes, sem reescrever lógica:

  Projetos   → projects_registry
  Kanban     → workspace_manager.WorkspaceManager.list_tasks()
  Modelos    → models_dev.catalog_models()
  Gateway    → config_loader + gateway_service.read_process_status()
  Obs        → cost_tracker (cost_history.jsonl) + otel (spans.jsonl)
  Config     → config_admin + config_profiles
  Logs       → tail de logs/*.log

O router recebe `verify_key` (dependency de auth do server) e resolvers de
workspace/config injetáveis — assim os endpoints derivam o projeto ativo ao vivo
e os testes podem apontar para diretórios temporários.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers (puros — testáveis isoladamente)
# ---------------------------------------------------------------------------

_SECRET_RE = re.compile(r"(key|token|secret|password|api_key)", re.IGNORECASE)
_SAFE_LOG_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
logger = logging.getLogger(__name__)


def _mask_secrets(node: Any) -> Any:
    """Mascara recursivamente valores de chaves sensíveis num dict de config."""
    if isinstance(node, dict):
        out: Dict[str, Any] = {}
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                out[k] = _mask_secrets(v)
            elif _SECRET_RE.search(str(k)) and v:
                s = str(v)
                out[k] = (s[:4] + "…") if len(s) > 4 else "•••"
            else:
                out[k] = v
        return out
    if isinstance(node, list):
        return [_mask_secrets(x) for x in node]
    return node


def _day_start(ts: float) -> float:
    """Epoch da meia-noite local do dia de `ts`."""
    lt = time.localtime(ts)
    midnight = (lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)
    return time.mktime(midnight)


def _read_jsonl(path: Path, limit: int = 0) -> List[Dict[str, Any]]:
    """Lê um JSONL defensivamente. `limit` > 0 retorna só as últimas N linhas."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    if limit > 0:
        lines = lines[-limit:]
    out: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                out.append(rec)
        except json.JSONDecodeError:
            continue
    return out


def _percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return round(s[k], 2)


def run_durations_ms(runtime_root: Path) -> List[float]:
    """Duração wall-clock (ms) das runs terminais, de started_at→finished_at.

    Fonte de latência para p50/p95 quando o serve não emite spans de tracing
    (que é o caso: o serve registra runs/eventos, não spans OTel). Cai em
    updated_at quando finished_at não foi gravado. Best-effort — timestamps
    ilegíveis são pulados."""
    from datetime import datetime

    try:
        from .core.runtime.run_manager import TERMINAL_RUN_STATUSES, RunManager
    except Exception:  # noqa: BLE001
        return []

    def _parse(ts: Any) -> Optional[datetime]:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(str(ts))
        except (ValueError, TypeError):
            return None

    out: List[float] = []
    try:
        runs = RunManager(root=runtime_root).list_runs()
    except Exception:  # noqa: BLE001
        return []
    for run in runs:
        if getattr(run, "status", None) not in TERMINAL_RUN_STATUSES:
            continue
        start = _parse(getattr(run, "started_at", None))
        end = _parse(getattr(run, "finished_at", None)) or _parse(getattr(run, "updated_at", None))
        if start is None or end is None:
            continue
        ms = (end - start).total_seconds() * 1000.0
        if ms >= 0:
            out.append(ms)
    return out


def cost_summary(cost_file: Path, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Resumo de custo/tokens do dia + acumulado, a partir do cost_history.jsonl."""
    now = now if now is not None else time.time()
    midnight = _day_start(now)
    recs = _read_jsonl(cost_file)
    today = [r for r in recs if float(r.get("ts", 0)) >= midnight]
    sessions = {r.get("session_id") for r in today if r.get("session_id")}
    return {
        "cost_today_usd": round(sum(float(r.get("cost_usd", 0)) for r in today), 6),
        "tokens_today": sum(int(r.get("total_tokens", 0)) for r in today),
        "calls_today": len(today),
        "sessions_today": len(sessions),
        "cost_total_usd": round(sum(float(r.get("cost_usd", 0)) for r in recs), 6),
    }


def cost_by_model(cost_file: Path) -> List[Dict[str, Any]]:
    """Breakdown de custo agregado por modelo (desc)."""
    recs = _read_jsonl(cost_file)
    agg: Dict[str, Dict[str, Any]] = {}
    for r in recs:
        model = r.get("model") or "unknown"
        a = agg.setdefault(model, {"model": model, "cost_usd": 0.0, "total_tokens": 0, "calls": 0})
        a["cost_usd"] += float(r.get("cost_usd", 0))
        a["total_tokens"] += int(r.get("total_tokens", 0))
        a["calls"] += 1
    for a in agg.values():
        a["cost_usd"] = round(a["cost_usd"], 6)
    return sorted(agg.values(), key=lambda x: x["cost_usd"], reverse=True)


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    import httpx

    try:
        return httpx.get(url, timeout=timeout).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def wait_for_health(
    url: str,
    *,
    timeout: float = 20.0,
    interval: float = 0.3,
    _probe: Optional[Callable[[str], bool]] = None,
) -> bool:
    """Poll ``url`` até responder 200 (ou estourar ``timeout``). Testável via ``_probe``."""
    probe = _probe or _http_ok
    deadline = time.time() + timeout
    while time.time() < deadline:
        if probe(url):
            return True
        time.sleep(interval)
    return False


def tail_log(log_path: Path, lines: int = 200) -> List[str]:
    if not log_path.exists():
        return []
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-max(1, lines):]


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def _default_config_path() -> Path:
    return Path("config.yaml")


def _default_workspace() -> Path:
    try:
        from .config_loader import load_config

        cfg = load_config("config.yaml")
        ws = Path(cfg.agent.workspace)
        return ws if ws.is_absolute() else (Path.cwd() / ws)
    except Exception:  # noqa: BLE001
        return Path.cwd() / "workspace"


def build_desktop_router(
    *,
    verify_key: Optional[Callable] = None,
    get_config_path: Optional[Callable[[], Path]] = None,
    get_workspace: Optional[Callable[[], Path]] = None,
    resolve_project_workspace: Optional[Callable[[Optional[str]], Path]] = None,
    cost_file: Optional[Path] = None,
    spans_file: Optional[Path] = None,
    runtime_root: Optional[Path] = None,
    logs_dir: Optional[Path] = None,
):
    """Monta o APIRouter ``/api`` do desktop. Tudo opcional/injetável p/ testes.

    ``resolve_project_workspace(project_id) -> Path``: quando fornecido, o
    ``/api/kanban`` usa a MESMA resolução de projeto do chat (project_id
    explícito > projeto ativo global > workspace default) para ler o board da
    pasta do projeto — sem isso, o painel lê sempre o TASKS.md do workspace
    raiz do serve, e as tarefas criadas por projeto (Fase 1) não aparecem.
    """
    from fastapi import APIRouter, Body, Depends, HTTPException, Query

    get_config_path = get_config_path or _default_config_path
    get_workspace = get_workspace or _default_workspace
    _cost_file = cost_file or (Path.home() / ".bauer" / "cost_history.jsonl")
    _spans_file = spans_file or (Path.home() / ".bauer" / "traces" / "spans.jsonl")
    _runtime_root = runtime_root or (Path.cwd() / "memory" / "runtime")
    _logs_dir = logs_dir or (Path.cwd() / "logs")

    def _kanban_workspace(project_id: Optional[str]) -> Path:
        """Workspace do board a exibir: projeto resolvido, com fallback seguro
        para o workspace default do serve."""
        if resolve_project_workspace is not None:
            try:
                return resolve_project_workspace(project_id)
            except Exception:  # noqa: BLE001 — nunca derruba o painel
                logger.debug("kanban: resolução de projeto falhou; usando default")
        return get_workspace()

    deps = [Depends(verify_key)] if verify_key else []
    router = APIRouter(prefix="/api", dependencies=deps, tags=["desktop"])

    # ── Projetos ──────────────────────────────────────────────────────────
    from . import projects_registry as pr

    @router.get("/projects")
    def list_projects():
        # Auto-descoberta: pastas de projeto do workspace entram no registro
        # (idempotente) — sem isso a tela fica vazia até adicionar à mão.
        try:
            pr.sync_workspace_projects(get_workspace())
        except Exception as exc:  # noqa: BLE001
            logger.debug("projects sync failed: %s", exc)
        return {"projects": pr.list_projects(), "active": pr.get_active()}

    @router.post("/projects")
    def add_project(body: dict = Body(...)):
        path = (body.get("path") or "").strip()
        if not path:
            raise HTTPException(status_code=400, detail="Campo 'path' obrigatório.")
        try:
            return pr.add_project(path, body.get("name"))
        except (NotADirectoryError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/projects/{pid}/activate")
    def activate_project(pid: str):
        if not pr.set_active(pid):
            raise HTTPException(status_code=404, detail=f"Projeto '{pid}' não encontrado.")
        return {"active": pid}

    @router.delete("/projects/{pid}")
    def delete_project(pid: str):
        if not pr.remove_project(pid):
            raise HTTPException(status_code=404, detail=f"Projeto '{pid}' não encontrado.")
        return {"removed": pid}

    @router.get("/projects/{pid}/stats")
    def project_stats(pid: str):
        return pr.project_stats(pid)

    # ── Kanban ────────────────────────────────────────────────────────────
    @router.get("/kanban")
    def kanban_board(
        project_id: str = Query("", description="projeto explicito (default: ativo global)"),
    ):
        try:
            from .workspace_manager import WorkspaceManager

            wm = WorkspaceManager(_kanban_workspace(project_id or None))
            tasks = wm.list_tasks()
        except Exception:  # noqa: BLE001
            tasks = []
        columns: Dict[str, List[Dict[str, Any]]] = {}
        for t in tasks:
            card = {
                "id": getattr(t, "id", ""),
                "title": getattr(t, "title", ""),
                "status": getattr(t, "status", ""),
                "priority": getattr(t, "priority", "medium"),
                "assignee": getattr(t, "assignee", ""),
            }
            columns.setdefault(card["status"] or "TODO", []).append(card)
        return {"columns": columns, "total": len(tasks)}

    # ── Modelos (catálogo) ────────────────────────────────────────────────
    # Providers primários exibidos no filtro do Desktop (ordem de exibição)
    _PRIMARY_PROVIDERS = [
        "openrouter", "openai", "anthropic", "google", "groq",
        "mistral", "cohere", "deepseek", "nvidia", "azure",
        "github-models", "github-copilot", "huggingface", "togetherai",
        "fireworks-ai", "cerebras", "perplexity", "xai", "ollama-cloud",
        "opencode",
    ]

    @router.get("/models/providers")
    def models_providers():
        try:
            from .models_dev import catalog_models
            all_models = catalog_models()
        except Exception:
            all_models = []
        seen: dict[str, int] = {}
        for m in all_models:
            p = m.get("provider", "")
            if p:
                seen[p] = seen.get(p, 0) + 1
        # Ordena: primários primeiro (na ordem acima), depois demais por contagem desc
        primary = [p for p in _PRIMARY_PROVIDERS if p in seen]
        others = sorted(
            [p for p in seen if p not in _PRIMARY_PROVIDERS],
            key=lambda p: -seen[p],
        )
        providers = primary + others[:20]
        return {"providers": providers, "counts": seen}

    @router.get("/models/catalog")
    def models_catalog(
        q: str = Query("", description="filtro de substring no id"),
        provider: str = Query("", description="filtrar por provider"),
        free: bool = Query(False, description="só modelos sem custo"),
        limit: int = Query(200, ge=1, le=2000),
        offset: int = Query(0, ge=0),
    ):
        try:
            from .models_dev import catalog_models

            models = catalog_models(provider=provider or None)
        except Exception:  # noqa: BLE001
            models = []
        if q:
            ql = q.lower()
            models = [m for m in models if ql in str(m.get("id", "")).lower()]
        if free:
            # Fonte única: o campo is_free do catálogo (models_dev._is_free_model).
            # Não reclassificar por cost==0 aqui — custo zero por token não
            # significa gratuito (modelos de áudio/imagem cobram por request).
            models = [m for m in models if m.get("is_free") is True]
        total = len(models)
        free_count = sum(1 for m in models if m.get("is_free"))
        page = models[offset:offset + limit]
        return {"total": total, "free_count": free_count, "models": page}

    # ── Gateway ───────────────────────────────────────────────────────────
    @router.get("/gateway/status")
    def gateway_status():
        out: Dict[str, Any] = {
            "telegram": False, "discord": False,
            "running": False, "pid": None, "uptime_s": None,
        }
        try:
            from .config_loader import load_config

            cfg = load_config(get_config_path())
            out["telegram"] = bool(getattr(cfg.telegram, "enabled", False))
            out["discord"] = bool(getattr(cfg.discord, "enabled", False))
        except Exception as exc:  # noqa: BLE001
            logger.debug("runtime dashboard config load failed: %s", exc)
        try:
            from .gateway_service import read_process_status

            pid, uptime, _mem = read_process_status(get_config_path().parent)
            out["pid"] = pid
            out["uptime_s"] = uptime
            out["running"] = pid is not None
        except Exception as exc:  # noqa: BLE001
            logger.debug("agents dashboard load failed: %s", exc)
        return out

    @router.post("/gateway/{action}")
    def gateway_control(action: str):
        if action not in ("start", "stop"):
            raise HTTPException(status_code=400, detail="Ação deve ser start ou stop.")
        try:
            from .gateway_service import GatewayServiceManager

            mgr = GatewayServiceManager()
            msg = mgr.start() if action == "start" else mgr.stop()
            return {"action": action, "detail": msg}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Observabilidade ───────────────────────────────────────────────────
    @router.get("/obs/summary")
    def obs_summary():
        summary = cost_summary(_cost_file)
        spans = _read_jsonl(_spans_file, limit=2000)
        durations = [
            float(s["duration_ms"]) for s in spans
            if isinstance(s.get("duration_ms"), (int, float))
        ]
        # O serve não emite spans OTel → sem isto o p95 do painel ficava
        # eternamente "-". Fallback: latência wall-clock das runs terminais.
        if not durations:
            durations = run_durations_ms(_runtime_root)
        summary["p50_ms"] = _percentile(durations, 50)
        summary["p95_ms"] = _percentile(durations, 95)
        return summary

    @router.get("/obs/cost")
    def obs_cost():
        budget = None
        try:
            from .config_loader import load_config

            cfg = load_config(get_config_path())
            budget = getattr(getattr(cfg, "observability", None), "daily_budget_usd", None)
        except Exception:  # noqa: BLE001
            pass
        return {"by_model": cost_by_model(_cost_file), "daily_budget_usd": budget}

    @router.get("/obs/traces")
    def obs_traces(
        session: str = Query("", description="filtra por session/trace id"),
        limit: int = Query(200, ge=1, le=2000),
    ):
        spans = _read_jsonl(_spans_file, limit=limit * 4)
        if session:
            spans = [
                s for s in spans
                if session in (str(s.get("trace_id", "")), str(s.get("session_id", "")))
            ]
        return {"spans": spans[-limit:]}

    @router.get("/obs/runs")
    def obs_runs(limit: int = Query(50, ge=1, le=500)):
        from dataclasses import asdict

        from .core.runtime.run_manager import RunManager

        runs = RunManager(root=_runtime_root).list_runs()
        return {"runs": [asdict(run) for run in runs[-limit:]]}

    @router.get("/obs/runs/{run_id}/trace")
    def obs_run_trace(run_id: str):
        from .core.events import EventBus
        from .core.observability import RunTraceStore

        return RunTraceStore(EventBus(root=_runtime_root).store).get_trace(run_id)

    @router.get("/obs/runs/{run_id}/events")
    def obs_run_events(run_id: str):
        from .core.events import EventBus

        bus = EventBus(root=_runtime_root)
        return {"events": [EventBus.to_dict(event) for event in bus.list_events(run_id=run_id)]}

    @router.get("/obs/approvals")
    def obs_approvals(status: str = Query("pending", description="pending | approved | denied")):
        from dataclasses import asdict

        from .core.policy import ApprovalManager

        return {"approvals": [asdict(record) for record in ApprovalManager(root=_runtime_root).list(status=status or None)]}

    @router.get("/events")
    def runtime_events(limit: int = Query(200, ge=1, le=2000)):
        from .core.events import EventBus

        bus = EventBus(root=_runtime_root)
        return {"events": [EventBus.to_dict(event) for event in bus.list_events(limit=limit)]}

    @router.get("/obs/budget")
    def obs_budget():
        from .core.runtime.autonomy import BudgetManager

        return BudgetManager(root=_runtime_root).status()

    # -- Auditoria e governanca (Fase 11) ---------------------------------
    @router.get("/audit/report")
    def phase11_audit_report(last: str = Query("24h", description="24h | 7d | 2w | 30d")):
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
        return asdict(build_report(_runtime_root, since=datetime.now() - delta, window_label=last))

    @router.get("/audit/runs/{run_id}")
    def phase11_audit_run(run_id: str):
        from dataclasses import asdict
        from .core.audit import audit_run

        audited = audit_run(_runtime_root, run_id, include_events=True, include_tools=True)
        if audited is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' nao encontrada.")
        return asdict(audited)

    @router.get("/audit/runs/{run_id}/score")
    def phase11_audit_score(run_id: str):
        from dataclasses import asdict
        from .core.audit import score_run_by_id

        score = score_run_by_id(_runtime_root, run_id)
        if score is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' nao encontrada.")
        return asdict(score)

    @router.get("/audit/skills")
    @router.get("/audit/skills/insights")
    def phase11_skill_insights(last: str = Query("7d", description="24h | 7d | 2w | 30d")):
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
            _runtime_root,
            since=datetime.now() - delta,
            window_label=last,
            suggest_new=True,
        ))

    @router.post("/os/command")
    def os_command(body: dict = Body(...)):
        from dataclasses import asdict

        from .core.events import EventBus
        from .core.policy import ApprovalManager, PolicyEngine
        from .core.runtime.run_manager import RunManager
        from .core.runtime.session_manager import SessionManager

        text = str(body.get("text") or body.get("command") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Campo 'text' obrigatorio.")

        normalized = text.casefold()
        event_bus = EventBus(root=_runtime_root)

        # Atalhos de navegação só valem para comandos curtos ("mostrar runs").
        # Frases longas ("abre o navegador e pesquisa docs do agno") carregam
        # intenção composta e devem chegar ao roteador de intenção lá embaixo.
        is_short = len(normalized.split()) <= 4

        def navigation(path: str, label: str) -> dict[str, Any]:
            return {
                "kind": "navigate",
                "label": label,
                "path": path,
                "message": f"Abrindo {label}.",
            }

        if is_short and any(term in normalized for term in ("ver runs", "mostrar runs", "listar runs", "runs")):
            return navigation("/runs", "Runs")
        if is_short and any(term in normalized for term in ("aprovar", "aprovacao", "aprova", "pendente")):
            return navigation("/approvals", "Approvals")
        if is_short and any(term in normalized for term in ("skill", "capability", "capabilities")):
            return navigation("/skills", "Skills")
        if any(term in normalized for term in ("pausar agente", "pausar agent", "pause agent", "pause agente")):
            agent_id = "default"
            for marker in ("pausar agente", "pausar agent", "pause agent", "pause agente"):
                if marker in normalized:
                    tail = text[normalized.index(marker) + len(marker):].strip()
                    if tail:
                        agent_id = tail.split()[0].strip(".,:;") or agent_id
                    break
            event_bus.publish(
                "tool.call.requested",
                agent_id=agent_id,
                tool_name="bauer_os.pause_agent",
                status="requested",
                message=f"Pause requested for agent {agent_id}",
                data={"source": "bauer_os_lite", "command": text},
            )
            return {
                "kind": "agent_pause_requested",
                "label": f"Agent {agent_id}",
                "path": "/agents",
                "agent_id": agent_id,
                "message": f"Pausa registrada para agent {agent_id}.",
            }
        if is_short and any(term in normalized for term in ("agent", "agente")) and not any(term in normalized for term in ("rodar", "executar")):
            return navigation("/agents", "Agents")
        if is_short and any(term in normalized for term in ("runtime", "agno", "worker")):
            return navigation("/runtime", "Runtime")
        if is_short and any(term in normalized for term in ("arquivo", "workspace", "pesquisar arquivo")):
            return navigation("/projects", "Workspace")
        if is_short and any(term in normalized for term in ("abrir navegador", "navegador", "browser")):
            return {
                "kind": "open_external",
                "label": "Navegador",
                "url": "about:blank",
                "message": "Abrindo o navegador.",
            }

        if any(term in normalized for term in ("painel de controle", "control panel")):
            decision = PolicyEngine(workspace=get_workspace(), runtime_root=_runtime_root).evaluate(
                "os.ui_control",
                {"command": text, "source": "bauer_os_lite"},
            )
            event_bus.publish(
                "policy.evaluated",
                status=decision.action,
                message=decision.reason,
                data={"operation": "os.ui_control", "risk_level": decision.risk_level, "matched_rules": decision.matched_rules},
            )
            if decision.action == "deny":
                return {
                    "kind": "denied",
                    "label": "Painel de controle",
                    "message": decision.reason,
                    "policy": asdict(decision),
                }
            if decision.action == "ask":
                approval = ApprovalManager(root=_runtime_root, event_bus=event_bus).request(
                    operation="os.ui_control",
                    tool_name="bauer_os.open_control_panel",
                    reason=decision.reason,
                    risk_level=decision.risk_level,
                    payload={"command": text, "target": "control_panel"},
                )
                return {
                    "kind": "approval_required",
                    "label": "Painel de controle",
                    "approval": asdict(approval),
                    "policy": asdict(decision),
                    "message": "Acao sensivel enviada para aprovacao.",
                }
            return {
                "kind": "open_external",
                "label": "Painel de controle",
                "url": "ms-settings:",
                "message": "Abrindo painel de controle.",
                "policy": asdict(decision),
            }

        wants_agent_run = any(term in normalized for term in ("rodar agent", "rodar agente", "executar agent", "executar agente"))
        if wants_agent_run:
            agent_id = "default"
            for marker in ("rodar agent", "rodar agente", "executar agent", "executar agente"):
                if marker in normalized:
                    tail = text[normalized.index(marker) + len(marker):].strip()
                    if tail:
                        agent_id = tail.split()[0].strip(".,:;") or agent_id
                    break
            session = SessionManager(root=_runtime_root).create_session(
                user_id="desktop",
                agent_id=agent_id,
                state={"source": "bauer_os_lite", "command": text},
            )
            run = RunManager(root=_runtime_root, event_bus=event_bus).create_run(
                session_id=session.id,
                agent_id=agent_id,
                runtime_adapter=str(body.get("runtime_adapter") or "bauer_native"),
                input={"message": text, "source": "bauer_os_lite"},
                status="queued",
            )
            return {
                "kind": "run_created",
                "label": f"Agent {agent_id}",
                "path": "/runs",
                "run": asdict(run),
                "session": asdict(session),
                "message": f"Run criada para agent {agent_id}.",
            }

        # Sem atalho determinístico → roteador de intenção (LLM auxiliar).
        # O SkillExecutor cuida de policy → approval → execução → eventos;
        # aqui só interpretamos a intenção e mapeamos o resultado pro palette.
        intent = None
        manifest = None
        try:
            from .core.skills import SkillExecutor, SkillRegistry
            from .os_intent import route_intent

            registry = SkillRegistry()
            intent = route_intent(text, registry.list())
            if intent is not None:
                manifest = registry.get(intent.skill_id)
        except Exception as exc:  # noqa: BLE001 — intent é best-effort.
            logger.debug("os command intent routing failed: %s", exc)
            intent = None
        if intent is not None and manifest is not None:
            result = SkillExecutor(
                workspace=get_workspace(),
                runtime_root=_runtime_root,
                event_bus=event_bus,
            ).execute(manifest, intent.inputs)
            if result.status == "completed":
                return {
                    "kind": "skill_executed",
                    "label": manifest.name,
                    "skill_id": manifest.id,
                    "output": result.output,
                    "message": intent.reason or f"{manifest.name} executada.",
                }
            if result.status == "waiting_approval":
                return {
                    "kind": "approval_required",
                    "label": manifest.name,
                    "approval": result.output.get("approval"),
                    "policy": result.output.get("decision"),
                    "message": "Acao sensivel enviada para aprovacao.",
                }
            if result.status == "denied":
                decision_data = result.output.get("decision") or {}
                return {
                    "kind": "denied",
                    "label": manifest.name,
                    "message": decision_data.get("reason") or "Acao negada pela policy.",
                    "policy": decision_data,
                }
            return {
                "kind": "skill_failed",
                "label": manifest.name,
                "skill_id": manifest.id,
                "message": str(result.output.get("error") or "Falha ao executar a skill."),
            }

        return {
            "kind": "unknown",
            "label": "Bauer Command",
            "message": "Nao reconheci esse comando ainda.",
            "suggestions": [
                "mostrar runs",
                "aprovar acao pendente",
                "rodar agent code",
                "abrir navegador",
                "abrir painel de controle",
                "pesquisar arquivo",
            ],
        }

    @router.get("/os/home")
    def os_home():
        """Painel unificado do Bauer OS: agentes ativos, aprovacoes pendentes,
        tarefas agendadas com falha, budget do dia e ultimas execucoes.

        Cada fonte e isolada em try/except: uma fonte indisponivel degrada o
        cartao correspondente sem derrubar o painel inteiro.
        """
        from dataclasses import asdict

        home: Dict[str, Any] = {}

        # Runs: ativos (nao-terminais) + ultimas execucoes.
        active_agents = 0
        recent_runs: List[Dict[str, Any]] = []
        try:
            from .core.runtime.run_manager import TERMINAL_RUN_STATUSES, RunManager

            runs = RunManager(root=_runtime_root).list_runs()
            active_agents = sum(1 for r in runs if r.status not in TERMINAL_RUN_STATUSES)
            recent_runs = [
                {
                    "id": r.id,
                    "agent_id": r.agent_id,
                    "status": r.status,
                    "runtime_adapter": getattr(r, "runtime_adapter", ""),
                    "started_at": getattr(r, "started_at", ""),
                    "updated_at": getattr(r, "updated_at", ""),
                }
                for r in runs[-6:][::-1]
            ]
        except Exception as exc:  # noqa: BLE001
            logger.debug("os home runs load failed: %s", exc)

        # Aprovacoes pendentes.
        pending_approvals: List[Dict[str, Any]] = []
        try:
            from .core.policy import ApprovalManager

            pending_approvals = [
                asdict(record)
                for record in ApprovalManager(root=_runtime_root).list(status="pending")
            ]
        except Exception as exc:  # noqa: BLE001
            logger.debug("os home approvals load failed: %s", exc)

        # Tarefas agendadas com falha na ultima execucao.
        failed_scheduled: List[Dict[str, Any]] = []
        try:
            from .core.runtime.scheduler import Scheduler

            for task in Scheduler(root=_runtime_root).list_tasks():
                if task.last_error:
                    failed_scheduled.append({
                        "id": task.id,
                        "name": getattr(task, "name", task.id),
                        "last_error": task.last_error,
                        "last_run_id": task.last_run_id,
                        "next_run_at": task.next_run_at,
                    })
        except Exception as exc:  # noqa: BLE001
            logger.debug("os home scheduler load failed: %s", exc)

        # Budget do dia: limite/uso vem do autonomy profile; custo do dia do ledger.
        budget: Dict[str, Any] = {}
        try:
            from .core.runtime.autonomy import BudgetManager

            daily = BudgetManager(root=_runtime_root).status().get("daily", {})
            budget = {
                "used_usd": daily.get("used_usd"),
                "limit_usd": daily.get("limit_usd"),
                "remaining_usd": daily.get("remaining_usd"),
                "exceeded": daily.get("exceeded"),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("os home budget load failed: %s", exc)
        try:
            summary = cost_summary(_cost_file)
            budget["cost_today_usd"] = summary.get("cost_today_usd")
            budget["calls_today"] = summary.get("calls_today")
        except Exception as exc:  # noqa: BLE001
            logger.debug("os home cost summary failed: %s", exc)

        home["active_agents"] = active_agents
        home["pending_approvals_count"] = len(pending_approvals)
        home["pending_approvals"] = pending_approvals[:5]
        home["failed_scheduled_count"] = len(failed_scheduled)
        home["failed_scheduled"] = failed_scheduled[:5]
        home["budget"] = budget
        home["recent_runs"] = recent_runs
        return home

    @router.get("/runtime/dashboard")
    def runtime_dashboard():
        from .core.runtime.adapters import list_runtime_adapters
        from .core.runtime.resilience import RuntimeControl, WorkerRegistry

        default_adapter = "bauer_native"
        configured: Dict[str, Any] = {}
        try:
            from .config_loader import load_config

            cfg = load_config(get_config_path())
            runtime = getattr(cfg, "runtime", None)
            default_adapter = getattr(runtime, "default_adapter", default_adapter)
            configured = getattr(runtime, "adapters", {}) or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("runtime dashboard config load failed: %s", exc)

        registered = list_runtime_adapters()
        adapters: List[Dict[str, Any]] = []
        for name in sorted(set(registered) | set(configured)):
            cfg = configured.get(name, {}) if isinstance(configured, dict) else {}
            adapters.append({
                "name": name,
                "registered": name in registered,
                "enabled": bool(cfg.get("enabled", name == "bauer_native")) if isinstance(cfg, dict) else name == "bauer_native",
                "default": name == default_adapter,
                "mode": cfg.get("mode", "sdk" if name == "agno" else "local") if isinstance(cfg, dict) else "-",
                "base_url": cfg.get("base_url", "") if isinstance(cfg, dict) else "",
            })

        return {
            "default_adapter": default_adapter,
            "adapters": adapters,
            "workers": WorkerRegistry(root=_runtime_root).list(),
            "kill_switch": RuntimeControl(root=_runtime_root).kill_switch_enabled(),
        }

    @router.get("/agents")
    def agents_dashboard():
        agents: List[Dict[str, Any]] = []
        try:
            from .agent_registry import AgentRegistry, list_builtin_specialists

            agents.extend({**agent.to_dict(), "source": "agents.yaml"} for agent in AgentRegistry(Path("agents.yaml")).list_agents())
            agents.extend({**agent.to_dict(), "source": "builtin"} for agent in list_builtin_specialists())
        except Exception as exc:  # noqa: BLE001
            logger.debug("agents dashboard load failed: %s", exc)
        seen: Dict[str, Dict[str, Any]] = {}
        for agent in agents:
            seen.setdefault(str(agent.get("name") or ""), agent)
        return {"agents": sorted(seen.values(), key=lambda item: str(item.get("name", "")))}

    @router.get("/skills")
    def skills_dashboard():
        from .core.skills import SkillRegistry

        return {"skills": [manifest.to_dict() for manifest in SkillRegistry().list()]}

    @router.post("/approvals/{approval_id}/approve")
    def approve_policy_request(approval_id: str):
        from dataclasses import asdict

        from .core.policy import ApprovalManager

        try:
            return asdict(ApprovalManager(root=_runtime_root).approve(approval_id))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' nao encontrado.")

    @router.post("/approvals/{approval_id}/deny")
    def deny_policy_request(approval_id: str):
        from dataclasses import asdict

        from .core.policy import ApprovalManager

        try:
            return asdict(ApprovalManager(root=_runtime_root).deny(approval_id))
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' nao encontrado.")

    # ── Config ────────────────────────────────────────────────────────────
    from . import config_admin as ca
    from . import config_profiles as cp

    @router.get("/config")
    def get_config():
        raw = ca._read_raw_yaml(get_config_path())
        return {"config": _mask_secrets(raw)}

    @router.put("/config")
    def put_config(body: dict = Body(...)):
        key = (body.get("key") or "").strip()
        if not key:
            raise HTTPException(status_code=400, detail="Campo 'key' obrigatório.")
        value = body.get("value", "")
        try:
            dest, path = ca.set_config_value(
                key, str(value), config_path=str(get_config_path())
            )
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"Chave inválida: {exc}")
        return {"saved": key, "dest": dest, "path": str(path)}

    @router.get("/config/profiles")
    def get_profiles():
        return {
            "profiles": cp.list_profiles(get_config_path()),
            "active": cp.get_active_profile(),
        }

    @router.post("/config/profiles/{name}/use")
    def use_profile(name: str):
        cp.set_active_profile(name)
        return {"active": name}

    # ── Logs ──────────────────────────────────────────────────────────────
    @router.get("/logs/{name}/tail")
    def logs_tail(name: str, lines: int = Query(200, ge=1, le=5000)):
        if not _SAFE_LOG_NAME.match(name):
            raise HTTPException(status_code=400, detail="Nome de log inválido.")
        fname = name if name.endswith(".log") else f"{name}.log"
        raw = tail_log(_logs_dir / fname, lines)
        # Logs do gateway carregam tokens de bot em URLs — redige antes de expor à UI.
        try:
            from .secrets_scanner import redact

            scrubbed = [redact(line) for line in raw]
        except Exception:  # noqa: BLE001
            scrubbed = raw
        return {"name": fname, "lines": scrubbed}

    return router
