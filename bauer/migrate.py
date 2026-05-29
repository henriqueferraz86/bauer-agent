"""migrate.py — Importa configurações e dados de outros agents para o Bauer.

Suporta:
  - Hermes Agent  (~/.hermes/config.yaml  +  clawd3d-history.json)
  - OpenClaw      (~/.openclaw/claw3d/settings.json)

Uso programático:
    result = HermesMigrator().migrate(dry_run=False)
    result = OpenClawMigrator().migrate(dry_run=False)

Cada migrador retorna um MigrationResult com listas de ações realizadas,
avisos e erros.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Resultado de migração ─────────────────────────────────────────────────────

@dataclass
class MigrationResult:
    source: str
    dry_run: bool
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def add(self, msg: str) -> None:
        self.actions.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)


# ── Helpers de config.yaml ────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise RuntimeError(f"Erro ao ler {path}: {exc}") from exc


def _save_yaml(path: Path, data: dict) -> None:
    import yaml
    path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _merge_config(config_path: Path, updates: dict, dry_run: bool) -> list[str]:
    """Aplica `updates` no config.yaml sem sobrescrever valores já definidos.

    Retorna lista de campos alterados.
    """
    if not config_path.exists():
        changed: list[str] = []

        def _collect_keys(d: dict, prefix: str = "") -> None:
            for k, v in d.items():
                kp = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    _collect_keys(v, kp)
                else:
                    changed.append(kp)

        _collect_keys(updates)
        if not dry_run:
            _save_yaml(config_path, updates)
        return changed

    current = _load_yaml(config_path)
    changed: list[str] = []

    def _apply(dst: dict, src: dict, prefix: str = "") -> None:
        for k, v in src.items():
            key_path = f"{prefix}.{k}" if prefix else k
            if k not in dst or dst[k] in (None, "", 0, False):
                dst[k] = v
                changed.append(key_path)
            elif isinstance(v, dict) and isinstance(dst.get(k), dict):
                _apply(dst[k], v, key_path)

    _apply(current, updates)

    if changed and not dry_run:
        _save_yaml(config_path, current)

    return changed


# ── Mapeamento de provider Hermes → Bauer ────────────────────────────────────

_HERMES_PROVIDER_MAP: dict[str, str] = {
    "ollama-launch": "ollama",
    "ollama":        "ollama",
    "openai":        "openai",
    "openai-api":    "openai-api",
    "anthropic":     "anthropic",
    "groq":          "groq",
    "mistral":       "mistral",
    "together":      "together",
    "deepseek":      "deepseek",
    "gemini":        "gemini",
    "azure":         "azure",
    "openrouter":    "openrouter",
    "xai":           "xai",
    "github":        "github",
    "copilot":       "copilot",
    # nomes genéricos
    "local":         "ollama",
    "remote":        "openai",
}

_HERMES_TOOLSET_MAP: dict[str, list[str]] = {
    "hermes-cli":  ["list_dir", "read_file", "write_file", "run_command"],
    "web":         ["web_search", "web_fetch"],
    "code":        ["read_file", "write_file", "run_command"],
    "files":       ["list_dir", "read_file", "write_file", "append_file",
                    "create_dir", "delete_file", "move_file"],
}


# ── HermesMigrator ────────────────────────────────────────────────────────────

class HermesMigrator:
    """Migra configurações e histórico do Hermes Agent para o Bauer."""

    DEFAULT_HERMES_DIR = Path.home() / ".hermes"

    def __init__(
        self,
        hermes_dir: str | Path | None = None,
        bauer_config: str | Path = "config.yaml",
        bauer_memory: str | Path = "memory",
        bauer_agents: str | Path = "agents.yaml",
    ):
        self.hermes_dir = Path(hermes_dir) if hermes_dir else self.DEFAULT_HERMES_DIR
        self.config_path = Path(bauer_config)
        self.memory_dir = Path(bauer_memory)
        self.agents_path = Path(bauer_agents)

    # --- detecção ---------------------------------------------------------------

    def detect(self) -> bool:
        """Retorna True se uma instalação Hermes foi encontrada."""
        return (self.hermes_dir / "config.yaml").exists()

    def source_summary(self) -> dict:
        """Retorna resumo do que foi encontrado na instalação Hermes."""
        summary: dict[str, Any] = {"dir": str(self.hermes_dir), "found": self.detect()}
        if not summary["found"]:
            return summary

        cfg_file = self.hermes_dir / "config.yaml"
        try:
            cfg = _load_yaml(cfg_file)
            summary["provider"] = cfg.get("model", {}).get("provider", "?")
            summary["model"] = cfg.get("model", {}).get("default", "?")
            summary["toolsets"] = cfg.get("toolsets", [])
            prov = cfg.get("providers", {})
            summary["provider_count"] = len(prov)
        except Exception:
            pass

        hist_file = self.hermes_dir / "clawd3d-history.json"
        if hist_file.exists():
            try:
                hist = json.loads(hist_file.read_text(encoding="utf-8"))
                if isinstance(hist, dict):
                    # formato {session_id: [messages]}
                    summary["session_count"] = len(hist)
                    summary["total_messages"] = sum(
                        len(v) for v in hist.values() if isinstance(v, list)
                    )
                elif isinstance(hist, list):
                    summary["session_count"] = 1
                    summary["total_messages"] = len(hist)
                else:
                    summary["session_count"] = 0
            except Exception:
                summary["session_count"] = 0
        else:
            summary["session_count"] = 0

        return summary

    # --- migração principal ------------------------------------------------------

    def migrate(
        self,
        dry_run: bool = False,
        import_config: bool = True,
        import_history: bool = True,
        import_agents: bool = True,
    ) -> MigrationResult:
        result = MigrationResult(source="hermes", dry_run=dry_run)

        if not self.detect():
            result.error(
                f"Instalação Hermes não encontrada em {self.hermes_dir}. "
                f"Verifique o caminho com --hermes-dir."
            )
            return result

        cfg_file = self.hermes_dir / "config.yaml"
        try:
            hermes_cfg = _load_yaml(cfg_file)
        except RuntimeError as exc:
            result.error(str(exc))
            return result

        # ── 1. Config ─────────────────────────────────────────────────────────
        if import_config:
            self._migrate_config(hermes_cfg, result, dry_run)

        # ── 2. Histórico de conversas ─────────────────────────────────────────
        if import_history:
            self._migrate_history(result, dry_run)

        # ── 3. Toolsets → agents.yaml ─────────────────────────────────────────
        if import_agents:
            self._migrate_toolsets(hermes_cfg, result, dry_run)

        return result

    def _migrate_config(
        self, hermes_cfg: dict, result: MigrationResult, dry_run: bool
    ) -> None:
        model_cfg = hermes_cfg.get("model", {})
        hermes_provider = model_cfg.get("provider", "ollama")
        bauer_provider = _HERMES_PROVIDER_MAP.get(hermes_provider, "ollama")
        model_name = model_cfg.get("default", "")
        base_url = model_cfg.get("base_url", "")

        updates: dict[str, Any] = {"model": {"provider": bauer_provider}}

        if model_name:
            updates["model"]["name"] = model_name

        # Remove o sufixo /v1 do host Ollama
        if bauer_provider == "ollama" and base_url:
            host = re.sub(r"/v1/?$", "", base_url)
            updates["ollama"] = {"host": host}
        elif bauer_provider in ("openai", "openai-api") and base_url:
            updates["openai"] = {"host": base_url}

        # Providers adicionais do Hermes
        for prov_id, prov_data in hermes_cfg.get("providers", {}).items():
            mapped = _HERMES_PROVIDER_MAP.get(prov_id, prov_id)
            prov_host = prov_data.get("api", "")
            if mapped == "ollama" and prov_host:
                host = re.sub(r"/v1/?$", "", prov_host)
                if "ollama" not in updates:
                    updates["ollama"] = {}
                updates["ollama"].setdefault("host", host)

        try:
            changed = _merge_config(self.config_path, updates, dry_run)
            if changed:
                prefix = "[dry-run] " if dry_run else ""
                for c in changed:
                    result.add(f"{prefix}config.yaml ← {c}")
            else:
                result.add("config.yaml já estava atualizado — sem alterações")
        except Exception as exc:
            result.error(f"Erro ao atualizar config.yaml: {exc}")

    def _migrate_history(self, result: MigrationResult, dry_run: bool) -> None:
        hist_file = self.hermes_dir / "clawd3d-history.json"
        if not hist_file.exists():
            result.warn("clawd3d-history.json não encontrado — histórico ignorado")
            return

        try:
            raw = hist_file.read_text(encoding="utf-8").strip()
            if not raw or raw in ("{}", "[]", "null"):
                result.add("Histórico Hermes está vazio — nada a importar")
                return

            hist = json.loads(raw)
        except Exception as exc:
            result.error(f"Erro ao ler histórico: {exc}")
            return

        # Normaliza para {session_id: [messages]}
        sessions: dict[str, list[dict]] = {}
        if isinstance(hist, list):
            sessions["hermes-import"] = hist
        elif isinstance(hist, dict):
            for sid, msgs in hist.items():
                if isinstance(msgs, list) and msgs:
                    sessions[sid] = msgs

        if not sessions:
            result.add("Histórico Hermes vazio — nada a importar")
            return

        sessions_dir = self.memory_dir / "sessions"
        if not dry_run:
            sessions_dir.mkdir(parents=True, exist_ok=True)

        for sid, messages in sessions.items():
            # Converte para formato Bauer {role, content} por linha
            jsonl_lines: list[str] = []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", msg.get("type", "user"))
                # Normaliza nomes de role
                if role in ("human", "user", "usuario"):
                    role = "user"
                elif role in ("ai", "assistant", "bot", "agent"):
                    role = "assistant"
                else:
                    role = "user"
                content = msg.get("content", msg.get("text", msg.get("message", "")))
                if content:
                    jsonl_lines.append(json.dumps({"role": role, "content": str(content)}, ensure_ascii=False))

            if not jsonl_lines:
                continue

            new_id = f"hermes-{sid[:8]}-{uuid.uuid4().hex[:6]}"
            out_file = sessions_dir / f"{new_id}.jsonl"
            prefix = "[dry-run] " if dry_run else ""
            if not dry_run:
                out_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")
            result.add(f"{prefix}sessão importada: {out_file.name} ({len(jsonl_lines)} mensagens)")

    def _migrate_toolsets(
        self, hermes_cfg: dict, result: MigrationResult, dry_run: bool
    ) -> None:
        toolsets = hermes_cfg.get("toolsets", [])
        if not toolsets:
            return

        # Monta lista de tools combinando os toolsets
        tools: list[str] = []
        for ts in toolsets:
            tools.extend(_HERMES_TOOLSET_MAP.get(ts, []))
        tools = list(dict.fromkeys(tools))  # deduplicar mantendo ordem

        if not tools:
            result.warn(f"Toolsets {toolsets} não mapeados — sem tools para importar")
            return

        # Cria agent "hermes-default" no agents.yaml
        try:
            import yaml
            from .agent_registry import AgentRegistry, AgentDef

            registry = AgentRegistry(self.agents_path)
            existing_names = {ag.name for ag in registry.list_agents()}

            if "hermes-default" in existing_names:
                result.warn("Agent 'hermes-default' ja existe -- pulado")
                return

            agent = AgentDef(
                name="hermes-default",
                description="Importado do Hermes Agent",
                system=(
                    "Você é um assistente especializado importado do Hermes Agent. "
                    "Responda de forma direta e use as tools disponíveis quando necessário."
                ),
                tools=tools,
            )
            prefix = "[dry-run] " if dry_run else ""
            if not dry_run:
                registry.save(agent)
            result.add(f"{prefix}agent criado: hermes-default (tools: {', '.join(tools)})")
        except Exception as exc:
            result.warn(f"Não foi possível criar agent hermes-default: {exc}")


# ── OpenClawMigrator ──────────────────────────────────────────────────────────

class OpenClawMigrator:
    """Migra perfis de conexão e tasks do OpenClaw para o Bauer."""

    DEFAULT_SETTINGS = Path.home() / ".openclaw" / "claw3d" / "settings.json"

    def __init__(
        self,
        settings_path: str | Path | None = None,
        bauer_config: str | Path = "config.yaml",
        bauer_workspace: str | Path = "workspace",
    ):
        self.settings_path = Path(settings_path) if settings_path else self.DEFAULT_SETTINGS
        self.config_path = Path(bauer_config)
        self.workspace_dir = Path(bauer_workspace)

    # --- detecção ---------------------------------------------------------------

    def detect(self) -> bool:
        return self.settings_path.exists()

    def source_summary(self) -> dict:
        summary: dict[str, Any] = {
            "file": str(self.settings_path),
            "found": self.detect(),
        }
        if not summary["found"]:
            return summary

        try:
            cfg = json.loads(self.settings_path.read_text(encoding="utf-8"))
            gateway = cfg.get("gateway", {})
            profiles = gateway.get("profiles", {})
            summary["profiles"] = list(profiles.keys())
            summary["profile_count"] = len(profiles)
            summary["active_adapter"] = gateway.get("adapterType", "?")
            summary["active_floor"] = cfg.get("activeFloorId", "?")

            # Task board
            task_boards = cfg.get("taskBoard", {})
            total_cards = sum(
                len(v.get("cards", [])) for v in task_boards.values()
                if isinstance(v, dict)
            )
            summary["task_card_count"] = total_cards
        except Exception:
            pass

        return summary

    # --- migração principal ------------------------------------------------------

    def migrate(
        self,
        dry_run: bool = False,
        import_config: bool = True,
        import_auth: bool = True,
        import_tasks: bool = True,
    ) -> MigrationResult:
        result = MigrationResult(source="openclaw", dry_run=dry_run)

        if not self.detect():
            result.error(
                f"settings.json do OpenClaw não encontrado em {self.settings_path}. "
                f"Verifique o caminho com --settings."
            )
            return result

        try:
            raw = self.settings_path.read_text(encoding="utf-8")
            cfg = json.loads(raw)
        except Exception as exc:
            result.error(f"Erro ao ler settings.json: {exc}")
            return result

        # ── 1. Provider ativo → config.yaml ────────────────────────────────────
        if import_config:
            self._migrate_active_provider(cfg, result, dry_run)

        # ── 2. Gateway profiles → auth tokens ──────────────────────────────────
        if import_auth:
            self._migrate_profiles(cfg, result, dry_run)

        # ── 3. Task board → TASKS.md ───────────────────────────────────────────
        if import_tasks:
            self._migrate_tasks(cfg, result, dry_run)

        return result

    def _migrate_active_provider(
        self, cfg: dict, result: MigrationResult, dry_run: bool
    ) -> None:
        gateway = cfg.get("gateway", {})
        adapter = gateway.get("adapterType", "")
        active_floor_id = cfg.get("activeFloorId", "")
        floors = cfg.get("officeFloors", {})
        active_floor = floors.get(active_floor_id, {})
        floor_provider = active_floor.get("provider", "")
        floor_url = active_floor.get("gatewayUrl", "")

        # Determina provider Bauer a partir do floor/adapter ativo
        provider_map = {"hermes": "ollama", "openai": "openai", "local": "ollama"}
        bauer_provider = provider_map.get(floor_provider or adapter, "ollama")

        updates: dict[str, Any] = {"model": {"provider": bauer_provider}}

        # Se a URL é HTTP local, usa como host Ollama
        if floor_url and ("localhost" in floor_url or "127.0.0.1" in floor_url):
            url = floor_url.replace("ws://", "http://").replace("wss://", "https://")
            url = re.sub(r"/v1/?$", "", url)
            if bauer_provider == "ollama":
                updates["ollama"] = {"host": url}

        try:
            changed = _merge_config(self.config_path, updates, dry_run)
            prefix = "[dry-run] " if dry_run else ""
            for c in changed:
                result.add(f"{prefix}config.yaml ← {c}")
        except Exception as exc:
            result.error(f"Erro ao atualizar config.yaml: {exc}")

    def _migrate_profiles(
        self, cfg: dict, result: MigrationResult, dry_run: bool
    ) -> None:
        gateway = cfg.get("gateway", {})
        profiles: dict[str, dict] = gateway.get("profiles", {})

        if not profiles:
            result.warn("Nenhum gateway profile encontrado no OpenClaw")
            return

        try:
            from .auth import AuthToken, TokenStore
            store = TokenStore()
        except Exception as exc:
            result.warn(f"TokenStore não disponível — tokens não serão salvos: {exc}")
            return

        for profile_name, profile_data in profiles.items():
            url = profile_data.get("url", "")
            token = profile_data.get("token", "")

            if not url:
                continue

            # Determina provider pelo nome/URL
            if "openclaw" in profile_name or "openclaw" in url:
                provider = "openclaw"
            elif "hermes" in profile_name or "hermes" in url:
                provider = "hermes"
            elif "localhost" in url or "127.0.0.1" in url:
                provider = f"local-{profile_name}"
            else:
                provider = profile_name

            # Normaliza URL (ws → http)
            api_base = url.replace("ws://", "http://").replace("wss://", "https://")

            prefix = "[dry-run] " if dry_run else ""
            if token:
                auth_token = AuthToken(
                    provider=provider,
                    access_token=token,
                    api_base=api_base,
                    token_type="Bearer",
                )
                if not dry_run:
                    try:
                        store.save(auth_token)
                        result.add(f"{prefix}token salvo: provider={provider} url={api_base}")
                    except Exception as exc:
                        result.error(f"Erro ao salvar token {provider}: {exc}")
                else:
                    result.add(f"{prefix}token seria salvo: provider={provider} url={api_base}")
            else:
                result.add(f"{prefix}perfil sem token: {profile_name} ({api_base}) — apenas URL registrada")

    def _migrate_tasks(
        self, cfg: dict, result: MigrationResult, dry_run: bool
    ) -> None:
        task_boards: dict[str, Any] = cfg.get("taskBoard", {})
        all_cards: list[dict] = []

        for board_url, board_data in task_boards.items():
            if isinstance(board_data, dict):
                cards = board_data.get("cards", [])
                if isinstance(cards, list):
                    all_cards.extend(cards)

        if not all_cards:
            result.add("Task board OpenClaw está vazio — sem tasks para importar")
            return

        try:
            from .workspace_manager import WorkspaceManager
            wm = WorkspaceManager(self.workspace_dir)
            if not wm.tasks_file.exists() and not dry_run:
                wm.init_project("Projeto")
        except Exception as exc:
            result.error(f"WorkspaceManager não disponível: {exc}")
            return

        imported = 0
        prefix = "[dry-run] " if dry_run else ""
        for card in all_cards:
            title = (
                card.get("title") or
                card.get("name") or
                card.get("label") or
                card.get("text") or ""
            ).strip()
            if not title:
                continue

            description = card.get("description") or card.get("body") or ""
            status_raw = (card.get("status") or card.get("state") or "todo").lower()
            # Mapeia status OpenClaw → Bauer
            status_map = {
                "todo": "TODO", "backlog": "TODO", "open": "TODO", "new": "TODO",
                "in_progress": "IN_PROGRESS", "doing": "IN_PROGRESS",
                "in progress": "IN_PROGRESS", "wip": "IN_PROGRESS",
                "done": "DONE", "closed": "DONE", "complete": "DONE",
                "completed": "DONE", "finished": "DONE",
                "blocked": "BLOCKED", "hold": "BLOCKED", "paused": "BLOCKED",
            }
            bauer_status = status_map.get(status_raw, "TODO")

            if not dry_run:
                try:
                    task = wm.add_task(title, description=str(description))
                    if bauer_status != "TODO":
                        wm.update_task_status(task.id, bauer_status)
                    result.add(f"{prefix}task importada [{bauer_status}]: {title[:60]}")
                    imported += 1
                except Exception as exc:
                    result.warn(f"Erro ao importar task '{title[:40]}': {exc}")
            else:
                result.add(f"{prefix}task seria importada [{bauer_status}]: {title[:60]}")
                imported += 1

        if imported:
            result.add(f"{prefix}total: {imported} task(s) do OpenClaw importadas para workspace/TASKS.md")
