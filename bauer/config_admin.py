"""Administração de config — leitura/escrita de config.yaml e .env pela CLI.

Coração do ``bauer config`` (paridade com ``herm config`` do Hermes). Tudo
aqui é puro e testável sem tocar na CLI: roteamento env-vs-yaml, escrita
incremental do .env (preserva o resto do arquivo), navegação por chave
pontilhada (``model.name``, ``mcp.servers.0.url``) e redaction de segredos.

Roteamento de chave (``is_env_key``):
- segredos e endpoints (``*_API_KEY``, ``*_TOKEN``, ``*_ENDPOINT``, ``*_HOST``,
  e a allowlist conhecida) vão para o ``.env`` — nunca para o config.yaml,
  que pode ir para o git.
- todo o resto é chave de config.yaml (aninhada por ``.``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

# Env vars que são segredos/host mesmo sem sufixo óbvio
_KNOWN_ENV_KEYS = {
    "GITHUB_TOKEN", "COPILOT_TOKEN", "OLLAMA_HOST", "OLLAMA_API_KEY",
    "GOOGLE_API_KEY", "AZURE_OPENAI_ENDPOINT", "BAUER_SERVE_API_KEY",
    "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN",
}

_ENV_SUFFIXES = ("_API_KEY", "_TOKEN", "_KEY", "_SECRET", "_ENDPOINT", "_HOST", "_PASSWORD")


def get_config_path(config: str | Path = "config.yaml") -> Path:
    return Path(config).resolve()


def get_env_path(env: str | Path = ".env") -> Path:
    return Path(env).resolve()


# ─────────────────────────────────────────────────────────────────────────────
# Redaction
# ─────────────────────────────────────────────────────────────────────────────


def redact_secret(value: str | None) -> str:
    """Mostra um segredo sem vazá-lo: ``sk-pr…9f2c`` ou ``(não definido)``."""
    if not value:
        return "(não definido)"
    v = str(value)
    if len(v) <= 8:
        return "•" * len(v)
    return f"{v[:4]}…{v[-4:]}"


# ─────────────────────────────────────────────────────────────────────────────
# Roteamento env vs yaml + coerção de tipos
# ─────────────────────────────────────────────────────────────────────────────


def is_env_key(key: str) -> bool:
    """True se a chave deve ir para o .env (segredo/endpoint), não o yaml."""
    k = key.upper()
    if "." in key:
        return False  # chave pontilhada é sempre config.yaml
    if k in _KNOWN_ENV_KEYS:
        return True
    return any(k.endswith(suf) for suf in _ENV_SUFFIXES)


def coerce_value(value: str) -> Any:
    """Converte string da CLI para bool/int/float/str (igual Hermes)."""
    low = value.strip().lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    if low in {"null", "none", "~"}:
        return None
    # int (com sinal)
    if re.fullmatch(r"-?\d+", value.strip()):
        return int(value)
    # float
    if re.fullmatch(r"-?\d*\.\d+", value.strip()):
        return float(value)
    return value


# ─────────────────────────────────────────────────────────────────────────────
# Navegação por chave pontilhada (com índice de lista)
# ─────────────────────────────────────────────────────────────────────────────


def _coerce_segment(node: Any, seg: str) -> Any:
    """Resolve um segmento (chave de dict ou índice de lista) ou None."""
    if isinstance(node, list):
        if re.fullmatch(r"\d+", seg):
            idx = int(seg)
            if 0 <= idx < len(node):
                return node[idx]
        return None
    if isinstance(node, dict):
        return node.get(seg)
    return None


def get_nested(data: dict, dotted_key: str) -> Any:
    """Lê ``a.b.0.c``. Retorna None se qualquer segmento faltar."""
    node: Any = data
    for seg in dotted_key.split("."):
        node = _coerce_segment(node, seg)
        if node is None:
            return None
    return node


def set_nested(data: dict, dotted_key: str, value: Any) -> None:
    """Escreve ``a.b.c`` criando dicts no caminho. Suporta índice de lista
    existente (``servers.0.url``) mas não estende listas."""
    segs = dotted_key.split(".")
    node: Any = data
    for seg in segs[:-1]:
        if isinstance(node, list):
            if not re.fullmatch(r"\d+", seg):
                raise KeyError(f"'{seg}' não é índice de lista em '{dotted_key}'")
            idx = int(seg)
            if not (0 <= idx < len(node)):
                raise KeyError(f"índice {idx} fora da lista em '{dotted_key}'")
            node = node[idx]
        else:
            if seg not in node or not isinstance(node[seg], (dict, list)):
                node[seg] = {}
            node = node[seg]
    last = segs[-1]
    if isinstance(node, list):
        if not re.fullmatch(r"\d+", last):
            raise KeyError(f"'{last}' não é índice de lista em '{dotted_key}'")
        idx = int(last)
        if not (0 <= idx < len(node)):
            raise KeyError(f"índice {idx} fora da lista em '{dotted_key}'")
        node[idx] = value
    else:
        node[last] = value


# ─────────────────────────────────────────────────────────────────────────────
# .env — escrita incremental preservando o resto do arquivo
# ─────────────────────────────────────────────────────────────────────────────


def save_env_value(key: str, value: str, env_path: str | Path = ".env") -> Path:
    """Cria/atualiza ``KEY=value`` no .env. Preserva comentários e demais linhas."""
    path = Path(env_path)
    key = key.strip()
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^\s*(export\s+)?{re.escape(key)}\s*=")
    new_line = f"{key}={value}"
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def remove_env_value(key: str, env_path: str | Path = ".env") -> bool:
    """Remove a linha ``KEY=...`` do .env. Retorna True se removeu algo."""
    path = Path(env_path)
    if not path.exists():
        return False
    key = key.strip()
    pattern = re.compile(rf"^\s*(export\s+)?{re.escape(key)}\s*=")
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines if not pattern.match(ln)]
    if len(kept) == len(lines):
        return False
    path.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    return True


def read_env_value(key: str, env_path: str | Path = ".env") -> str | None:
    """Lê o valor de KEY direto do arquivo .env (sem tocar no os.environ)."""
    path = Path(env_path)
    if not path.exists():
        return None
    pattern = re.compile(rf"^\s*(export\s+)?{re.escape(key)}\s*=(.*)$")
    for line in path.read_text(encoding="utf-8").splitlines():
        m = pattern.match(line)
        if m:
            val = m.group(2).strip()
            if len(val) >= 2 and val[0] in ("'", '"') and val[-1] == val[0]:
                val = val[1:-1]
            return val
    return None


# ─────────────────────────────────────────────────────────────────────────────
# config.yaml — escrita preservando só o user-config (sem dumpar defaults)
# ─────────────────────────────────────────────────────────────────────────────


def _read_raw_yaml(config_path: str | Path) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}


def set_config_value(
    key: str, value: str,
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
) -> tuple[str, Path]:
    """Define um valor. Roteia para .env (segredos) ou config.yaml (resto).

    Retorna ``(destino, caminho)`` onde destino ∈ {"env", "config"}.
    Levanta KeyError se a chave pontilhada for inválida.
    """
    if is_env_key(key):
        path = save_env_value(key.upper(), value, env_path)
        return "env", path

    raw = _read_raw_yaml(config_path)
    set_nested(raw, key, coerce_value(value))
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return "config", path


def get_config_value(
    key: str,
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
) -> Any:
    """Lê um valor de config.yaml (chave pontilhada) ou .env (chave de env)."""
    if is_env_key(key):
        import os
        return os.environ.get(key.upper()) or read_env_value(key.upper(), env_path)
    return get_nested(_read_raw_yaml(config_path), key)


def unset_config_value(
    key: str,
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
) -> tuple[str, bool]:
    """Remove um valor. Retorna ``(destino, removeu?)``."""
    if is_env_key(key):
        return "env", remove_env_value(key.upper(), env_path)
    raw = _read_raw_yaml(config_path)
    segs = key.split(".")
    node: Any = raw
    for seg in segs[:-1]:
        node = _coerce_segment(node, seg)
        if not isinstance(node, dict):
            return "config", False
    if isinstance(node, dict) and segs[-1] in node:
        del node[segs[-1]]
        Path(config_path).write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        return "config", True
    return "config", False


# ─────────────────────────────────────────────────────────────────────────────
# check — status de env vars (delega ao provider_profile)
# ─────────────────────────────────────────────────────────────────────────────


def env_status_rows() -> list[dict]:
    """Linhas {provider, display_name, env_var, set, auth_type} de todos os
    providers — base do ``bauer config check``."""
    try:
        from .provider_profile import env_var_status
        return env_var_status()
    except Exception:  # noqa: BLE001
        return []


def find_editor() -> str | None:
    """Editor do usuário: $EDITOR/$VISUAL, senão candidato por plataforma."""
    import os
    import shutil
    import sys

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if editor:
        return editor
    candidates = (
        ["notepad", "code", "vim", "nano"]
        if sys.platform == "win32"
        else ["nano", "vim", "vi", "code", "notepad"]
    )
    for cmd in candidates:
        if shutil.which(cmd):
            return cmd
    return None
