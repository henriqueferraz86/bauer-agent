"""Config profiles — suporte a múltiplos perfis (dev/staging/prod).

Cada perfil é um arquivo config.<profile>.yaml no mesmo diretório que config.yaml.
O perfil ativo é rastreado em ~/.bauer/active_profile.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional


_ACTIVE_PROFILE_FILE = Path.home() / ".bauer" / "active_profile"


def _config_dir(config_path: Optional[Path] = None) -> Path:
    base = config_path or Path("config.yaml")
    return base.parent


def profile_path(profile: str, config_path: Optional[Path] = None) -> Path:
    """Retorna o caminho do arquivo de perfil."""
    return _config_dir(config_path) / f"config.{profile}.yaml"


def list_profiles(config_path: Optional[Path] = None) -> List[str]:
    """Lista todos os perfis disponíveis (arquivos config.<profile>.yaml)."""
    d = _config_dir(config_path)
    profiles = []
    for f in sorted(d.glob("config.*.yaml")):
        name = f.stem.replace("config.", "", 1)
        if name and name != "yaml":
            profiles.append(name)
    return profiles


def create_profile(
    profile: str,
    source_config: Optional[Path] = None,
    overwrite: bool = False,
) -> Path:
    """Cria um perfil copiando config.yaml (ou outro arquivo fonte).

    Retorna o caminho do perfil criado.
    """
    src = source_config or Path("config.yaml")
    dst = profile_path(profile, src)

    if dst.exists() and not overwrite:
        raise FileExistsError(f"Perfil '{profile}' já existe em {dst}. Use overwrite=True para sobrescrever.")

    if src.exists():
        shutil.copy2(src, dst)
    else:
        dst.write_text("# Perfil: " + profile + "\n")

    return dst


def delete_profile(profile: str, config_path: Optional[Path] = None) -> bool:
    """Remove um perfil. Retorna True se removido, False se não existia."""
    p = profile_path(profile, config_path)
    if p.exists():
        p.unlink()
        return True
    return False


def get_active_profile() -> Optional[str]:
    """Retorna o perfil ativo (lido de ~/.bauer/active_profile)."""
    if _ACTIVE_PROFILE_FILE.exists():
        name = _ACTIVE_PROFILE_FILE.read_text().strip()
        return name if name else None
    return None


def set_active_profile(profile: Optional[str]) -> None:
    """Define o perfil ativo. None = sem perfil (usa config.yaml padrão)."""
    _ACTIVE_PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if profile is None:
        _ACTIVE_PROFILE_FILE.write_text("")
    else:
        _ACTIVE_PROFILE_FILE.write_text(profile)


def effective_config_path(config_path: Optional[Path] = None) -> Path:
    """Retorna o caminho de config a usar: perfil ativo ou config.yaml padrão."""
    base = config_path or Path("config.yaml")
    active = get_active_profile()
    if active:
        p = profile_path(active, base)
        if p.exists():
            return p
    return base


# ---------------------------------------------------------------------------
# Config diff
# ---------------------------------------------------------------------------

def config_diff(path_a: Path, path_b: Path) -> List[str]:
    """Retorna as diferenças entre dois arquivos de config no formato unified diff.

    Retorna lista de linhas (com prefixos +/-/space).
    """
    import difflib

    text_a = path_a.read_text(encoding="utf-8").splitlines(keepends=True) if path_a.exists() else []
    text_b = path_b.read_text(encoding="utf-8").splitlines(keepends=True) if path_b.exists() else []

    diff = list(difflib.unified_diff(
        text_a, text_b,
        fromfile=str(path_a),
        tofile=str(path_b),
        lineterm="",
    ))
    return diff


# ---------------------------------------------------------------------------
# Config validate (JSON Schema via Pydantic)
# ---------------------------------------------------------------------------

def validate_config(config_path: Optional[Path] = None) -> List[str]:
    """Valida o arquivo de config. Retorna lista de erros (vazia = válido)."""
    import yaml

    fp = config_path or Path("config.yaml")
    if not fp.exists():
        return [f"Arquivo não encontrado: {fp}"]

    try:
        with open(fp, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        return [f"YAML inválido: {exc}"]

    if data is None:
        return ["Arquivo de config está vazio"]

    errors = []
    # Validações básicas estruturais
    if not isinstance(data, dict):
        errors.append("Raiz do config deve ser um mapeamento YAML")
        return errors

    # Tenta validar com Pydantic se disponível
    try:
        from .config_loader import load_config
        load_config(str(fp))
    except Exception as exc:
        err_msg = str(exc)
        # Filtra erros de campo inválido para dar mensagem clara
        if "extra inputs are not permitted" in err_msg or "validation error" in err_msg.lower():
            errors.append(f"Erro de validação Pydantic: {err_msg[:200]}")
        else:
            errors.append(f"Erro ao carregar config: {err_msg[:200]}")

    return errors


# ---------------------------------------------------------------------------
# Config migrate
# ---------------------------------------------------------------------------

_MIGRATIONS: Dict[str, Any] = {
    "0.1→0.2": {
        "description": "Renomeia 'model' para 'default_model' em cada section de provider",
        "renames": {},
        "adds": {},
    },
}


def list_migrations() -> List[Dict[str, str]]:
    """Lista migrações disponíveis."""
    return [{"key": k, "description": v["description"]} for k, v in _MIGRATIONS.items()]


def run_migration(
    migration_key: str,
    config_path: Optional[Path] = None,
    dry_run: bool = True,
) -> List[str]:
    """Executa uma migração de config.

    Retorna lista de mudanças feitas (ou que seriam feitas em dry_run=True).
    """
    import yaml

    fp = config_path or Path("config.yaml")
    if not fp.exists():
        return [f"Arquivo não encontrado: {fp}"]

    if migration_key not in _MIGRATIONS:
        return [f"Migração '{migration_key}' não encontrada. Disponíveis: {list(_MIGRATIONS.keys())}"]

    migration = _MIGRATIONS[migration_key]
    try:
        with open(fp, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        return [f"Erro ao ler config: {exc}"]

    changes = []

    # Aplica renames
    for old_key, new_key in migration.get("renames", {}).items():
        if old_key in data:
            changes.append(f"Renomeia '{old_key}' → '{new_key}'")
            if not dry_run:
                data[new_key] = data.pop(old_key)

    # Aplica adições
    for key, default_val in migration.get("adds", {}).items():
        if key not in data:
            changes.append(f"Adiciona '{key}' = {default_val!r}")
            if not dry_run:
                data[key] = default_val

    if not dry_run and changes:
        with open(fp, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

    return changes if changes else ["Nenhuma alteração necessária"]
