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
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers (puros — testáveis isoladamente)
# ---------------------------------------------------------------------------

_SECRET_RE = re.compile(r"(key|token|secret|password|api_key)", re.IGNORECASE)
_SAFE_LOG_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


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
    cost_file: Optional[Path] = None,
    spans_file: Optional[Path] = None,
    logs_dir: Optional[Path] = None,
):
    """Monta o APIRouter ``/api`` do desktop. Tudo opcional/injetável p/ testes."""
    from fastapi import APIRouter, Body, Depends, HTTPException, Query

    get_config_path = get_config_path or _default_config_path
    get_workspace = get_workspace or _default_workspace
    _cost_file = cost_file or (Path.home() / ".bauer" / "cost_history.jsonl")
    _spans_file = spans_file or (Path.home() / ".bauer" / "traces" / "spans.jsonl")
    _logs_dir = logs_dir or (Path.cwd() / "logs")

    deps = [Depends(verify_key)] if verify_key else []
    router = APIRouter(prefix="/api", dependencies=deps, tags=["desktop"])

    # ── Projetos ──────────────────────────────────────────────────────────
    from . import projects_registry as pr

    @router.get("/projects")
    def list_projects():
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
    def kanban_board():
        try:
            from .workspace_manager import WorkspaceManager

            wm = WorkspaceManager(get_workspace())
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
            models = [
                m for m in models
                if m.get("is_free") is True or (
                    "is_free" not in m and m.get("cost_in") == 0
                )
            ]
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
        except Exception:  # noqa: BLE001
            pass
        try:
            from .gateway_service import read_process_status

            pid, uptime, _mem = read_process_status(get_config_path().parent)
            out["pid"] = pid
            out["uptime_s"] = uptime
            out["running"] = pid is not None
        except Exception:  # noqa: BLE001
            pass
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
