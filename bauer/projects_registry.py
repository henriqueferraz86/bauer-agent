"""Projects registry — registro de múltiplos workspaces/projetos do Bauer.

A CLI sempre operou um único workspace (o diretório corrente). O Bauer Desktop
precisa alternar entre vários projetos — cada um com seu config.yaml, profile
ativo, modelo e gateway. Este módulo persiste essa lista em
``~/.bauer/projects.json`` e oferece CRUD + enriquecimento ao vivo (modelo,
gateway on/off) lido do config de cada projeto.

Estrutura do JSON::

    {
      "active": "<id>",
      "projects": [
        {"id": "ab12cd34ef56", "name": "BauerAgent",
         "path": "C:/.../BauerAgent", "added_at": 1750000000.0}
      ]
    }

O ``id`` é derivado do caminho absoluto (sha1[:12]) — estável entre execuções,
sem colisão para caminhos distintos.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_REGISTRY = Path.home() / ".bauer" / "projects.json"


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def _registry_path(registry_path: Optional[Path] = None) -> Path:
    return registry_path or _DEFAULT_REGISTRY


def project_id(path: str | Path) -> str:
    """ID estável derivado do caminho absoluto normalizado."""
    abspath = str(Path(path).expanduser().resolve())
    return hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:12]


def load_registry(registry_path: Optional[Path] = None) -> Dict[str, Any]:
    """Carrega o registro. Retorna estrutura vazia se não existir/corrompido."""
    p = _registry_path(registry_path)
    if not p.exists():
        return {"active": None, "projects": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"active": None, "projects": []}
    if not isinstance(data, dict):
        return {"active": None, "projects": []}
    data.setdefault("active", None)
    data.setdefault("projects", [])
    if not isinstance(data["projects"], list):
        data["projects"] = []
    return data


def save_registry(reg: Dict[str, Any], registry_path: Optional[Path] = None) -> None:
    p = _registry_path(registry_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_project(
    path: str | Path,
    name: Optional[str] = None,
    *,
    registry_path: Optional[Path] = None,
    require_config: bool = False,
) -> Dict[str, Any]:
    """Registra um workspace. Idempotente (mesmo path = mesmo id, atualiza nome).

    Valida que o diretório existe. Se ``require_config``, exige config.yaml.
    O primeiro projeto registrado vira o ativo automaticamente.
    """
    proj_dir = Path(path).expanduser().resolve()
    if not proj_dir.is_dir():
        raise NotADirectoryError(f"Diretório não existe: {proj_dir}")
    if require_config and not (proj_dir / "config.yaml").exists():
        raise FileNotFoundError(f"config.yaml não encontrado em {proj_dir}")

    reg = load_registry(registry_path)
    pid = project_id(proj_dir)
    entry = {
        "id": pid,
        "name": name or proj_dir.name,
        "path": str(proj_dir),
        "added_at": time.time(),
    }

    existing = next((p for p in reg["projects"] if p.get("id") == pid), None)
    if existing is not None:
        existing["name"] = entry["name"]
        existing["path"] = entry["path"]
    else:
        reg["projects"].append(entry)

    if reg.get("active") is None:
        reg["active"] = pid

    save_registry(reg, registry_path)
    return entry


def remove_project(pid: str, *, registry_path: Optional[Path] = None) -> bool:
    """Remove do registro (não apaga arquivos). Reajusta o ativo se necessário."""
    reg = load_registry(registry_path)
    before = len(reg["projects"])
    reg["projects"] = [p for p in reg["projects"] if p.get("id") != pid]
    removed = len(reg["projects"]) < before
    if reg.get("active") == pid:
        reg["active"] = reg["projects"][0]["id"] if reg["projects"] else None
    if removed:
        save_registry(reg, registry_path)
    return removed


def get_project(pid: str, *, registry_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    reg = load_registry(registry_path)
    return next((p for p in reg["projects"] if p.get("id") == pid), None)


def find_project_for_cwd(
    cwd: str | Path,
    *,
    registry_path: Optional[Path] = None,
) -> Optional[str]:
    """project_id se ``cwd`` (ou um ancestral) já é um projeto registrado.

    Sobe de ``cwd`` até a raiz do FS; o primeiro diretório cujo ``project_id``
    consta no registro vence. Deixa `bauer agent` reconhecer a pasta do projeto
    automaticamente quando o usuário roda de dentro dela (ou de uma subpasta).
    Retorna None se nada casar."""
    try:
        cur = Path(cwd).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return None
    ids = {p.get("id") for p in load_registry(registry_path)["projects"]}
    if not ids:
        return None
    for d in [cur, *cur.parents]:
        if project_id(d) in ids:
            return project_id(d)
    return None


def is_sensitive_dir(path: str | Path) -> bool:
    """True para diretórios que NUNCA devem virar workspace por auto-detecção.

    Guard da adoção automática: raiz de disco/FS, a home do usuário e o
    ``~/.bauer`` (home do Bauer). Adotar qualquer um desses daria à sandbox
    acesso de escrita a praticamente tudo — a confirmação nem deve ser
    oferecida. Um path que nem resolve é tratado como sensível (falha seguro)."""
    try:
        p = Path(path).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return True
    if p == p.parent:  # raiz de disco (C:\) ou do FS (/)
        return True
    try:
        if p == Path.home().resolve():
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from .paths import get_bauer_home
        if p == get_bauer_home().resolve():
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def get_active(registry_path: Optional[Path] = None) -> Optional[str]:
    return load_registry(registry_path).get("active")


def set_active(pid: str, *, registry_path: Optional[Path] = None) -> bool:
    """Marca um projeto como ativo. Retorna False se o id não existe."""
    reg = load_registry(registry_path)
    if not any(p.get("id") == pid for p in reg["projects"]):
        return False
    reg["active"] = pid
    save_registry(reg, registry_path)
    return True


# Pastas do workspace que não são projetos (infra do próprio Bauer / lixo).
_SYNC_SKIP_DIRS = {
    "__pycache__", "node_modules", "memory", "logs", "sessions",
    ".bauer_serve", ".bauer_worktrees", "venv", ".venv",
}


def sync_workspace_projects(
    workspace: str | Path,
    *,
    registry_path: Optional[Path] = None,
) -> int:
    """Semeia o registro com as pastas de projeto do workspace (idempotente).

    O agente cria projetos como subpastas do workspace (barbearia-site,
    bauerinvest…), mas o registro era só manual — a tela Projetos ficava
    vazia até o usuário adicionar cada pasta à mão. Retorna quantas pastas
    foram sincronizadas. Nunca levanta exceção."""
    count = 0
    try:
        ws = Path(workspace).expanduser().resolve()
        if not ws.is_dir():
            return 0
        for child in sorted(ws.iterdir()):
            if (
                not child.is_dir()
                or child.name.startswith(".")
                or child.name in _SYNC_SKIP_DIRS
            ):
                continue
            try:
                add_project(child, registry_path=registry_path)
                count += 1
            except Exception:  # noqa: BLE001 — uma pasta ruim não derruba o sync
                continue
    except Exception:  # noqa: BLE001
        return count
    return count


# ---------------------------------------------------------------------------
# Enriquecimento (modelo / gateway / profile) — lido ao vivo do projeto
# ---------------------------------------------------------------------------

def _enrich(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Adiciona model/provider/gateway/telegram a partir do config do projeto.

    Defensivo: qualquer falha de leitura mantém os campos como None/False.
    """
    out = dict(entry)
    out.setdefault("model", None)
    out.setdefault("provider", None)
    out.setdefault("telegram", False)
    out.setdefault("gateway_running", False)

    cfg_path = Path(entry["path"]) / "config.yaml"
    try:
        from .config_loader import load_config

        cfg = load_config(cfg_path)
        out["model"] = getattr(cfg.model, "name", None)
        out["provider"] = getattr(cfg.model, "provider", None)
        out["telegram"] = bool(getattr(cfg.telegram, "enabled", False))
    except Exception:  # noqa: BLE001
        pass

    try:
        from .gateway_service import read_process_status

        pid, _uptime, _mem = read_process_status(Path(entry["path"]))
        out["gateway_running"] = pid is not None
    except Exception:  # noqa: BLE001
        pass

    return out


def list_projects(
    *,
    registry_path: Optional[Path] = None,
    enrich: bool = True,
) -> List[Dict[str, Any]]:
    """Lista projetos registrados. ``enrich`` adiciona model/gateway ao vivo."""
    reg = load_registry(registry_path)
    active = reg.get("active")
    out = []
    for entry in reg["projects"]:
        item = _enrich(entry) if enrich else dict(entry)
        item["active"] = entry.get("id") == active
        out.append(item)
    return out


def project_stats(pid: str, *, registry_path: Optional[Path] = None) -> Dict[str, Any]:
    """Estatísticas do projeto: sessões, custo total, tokens.

    Custo/tokens vêm do histórico global do cost_tracker (~/.bauer/cost_history.jsonl);
    sessões do diretório de sessões do workspace.
    """
    entry = get_project(pid, registry_path=registry_path)
    stats = {"sessions": 0, "cost_usd": 0.0, "total_tokens": 0}
    if entry is None:
        return stats

    # Sessões: conta arquivos no diretório de sessões do workspace
    try:
        from .config_loader import load_config

        cfg = load_config(Path(entry["path"]) / "config.yaml")
        ws = Path(cfg.agent.workspace)
        if not ws.is_absolute():
            ws = Path(entry["path"]) / ws
        sessions_dir = ws / "sessions"
        if sessions_dir.is_dir():
            stats["sessions"] = sum(1 for _ in sessions_dir.glob("*.json"))
    except Exception:  # noqa: BLE001
        pass

    # Custo/tokens: agrega o histórico global do cost_tracker
    try:
        cost_file = Path.home() / ".bauer" / "cost_history.jsonl"
        if cost_file.exists():
            for line in cost_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stats["cost_usd"] += float(rec.get("cost_usd", 0.0))
                stats["total_tokens"] += int(rec.get("total_tokens", 0))
    except Exception:  # noqa: BLE001
        pass

    stats["cost_usd"] = round(stats["cost_usd"], 6)
    return stats
