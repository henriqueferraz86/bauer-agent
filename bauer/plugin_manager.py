"""Instalação e estado transacional de plugins gerenciados."""

from __future__ import annotations

import ast
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import plugin_registry_path
from .plugin_manifest import PluginManifest, PluginManifestError


logger = logging.getLogger("bauer.plugin_manager")


class PluginManagerError(RuntimeError):
    """Falha operacional ao instalar, validar ou alterar um plugin."""


@dataclass(frozen=True)
class ManagedPlugin:
    manifest: PluginManifest
    path: Path
    enabled: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.manifest.id,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "path": str(self.path),
            "enabled": self.enabled,
            "permissions": list(self.manifest.permissions),
            "capabilities": list(self.manifest.capabilities),
            "error": self.error,
        }


class PluginManager:
    """Gerencia plugins sob o diretório canônico do usuário."""

    def __init__(self, *, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root is not None else plugin_registry_path().parent
        self.installed_dir = self.root / "installed"
        self.history_dir = self.root / "history"
        self.registry_path = self.root / "registry.json"
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def list_plugins(self) -> list[ManagedPlugin]:
        records = self._read_registry()
        result: list[ManagedPlugin] = []
        for plugin_dir in sorted(path for path in self.installed_dir.iterdir() if path.is_dir()):
            manifest_path = plugin_dir / "plugin.yaml"
            try:
                manifest = PluginManifest.from_file(manifest_path)
                record = records.get(manifest.id, {})
                result.append(
                    ManagedPlugin(
                        manifest=manifest,
                        path=plugin_dir,
                        enabled=bool(record.get("enabled", True)),
                    )
                )
            except (PluginManifestError, OSError) as exc:
                fallback_id = plugin_dir.name
                safe_fallback_id = "".join(
                    char if char.isalnum() or char in "._-" else "_"
                    for char in fallback_id.lower()
                )
                if len(safe_fallback_id) < 2 or not safe_fallback_id[0].isalnum():
                    safe_fallback_id = f"invalid_{safe_fallback_id}"
                safe_fallback_id = safe_fallback_id[:64]
                result.append(
                    ManagedPlugin(
                        manifest=PluginManifest(
                            id=safe_fallback_id or "invalid_plugin",
                            name=fallback_id,
                            version="0.0.0",
                            bauer_min_version="unknown",
                            source=str(manifest_path),
                        ),
                        path=plugin_dir,
                        enabled=False,
                        error=str(exc),
                    )
                )
        return result

    def get(self, plugin_id: str) -> ManagedPlugin:
        normalized = plugin_id.strip().lower()
        for plugin in self.list_plugins():
            if plugin.manifest.id == normalized:
                return plugin
        raise PluginManagerError(f"Plugin não encontrado: {plugin_id}")

    def search(self, query: str) -> list[ManagedPlugin]:
        needle = query.strip().lower()
        return [
            plugin
            for plugin in self.list_plugins()
            if needle in plugin.manifest.id.lower()
            or needle in plugin.manifest.name.lower()
            or needle in " ".join(plugin.manifest.capabilities).lower()
        ]

    def install(self, source: str | Path, *, force: bool = False) -> ManagedPlugin:
        candidate_root = Path(tempfile.mkdtemp(prefix=".candidate-", dir=self.root))
        previous_dir: Path | None = None
        archived_previous: Path | None = None
        active_dir: Path | None = None
        switched = False
        registry_before = self._read_registry()
        registry_existed = self.registry_path.exists()
        try:
            source_path = Path(source).expanduser()
            if source_path.exists():
                self._copy_local(source_path, candidate_root)
            elif str(source).startswith(("http://", "https://", "git@")):
                self._clone_git(str(source), candidate_root)
            else:
                raise PluginManagerError(f"Fonte local/Git não encontrada: {source}")

            manifest = self._validate_candidate(candidate_root)
            active_dir = self.installed_dir / manifest.id
            if active_dir.exists() and not force:
                raise PluginManagerError(f"Plugin '{manifest.id}' já instalado; use --force para substituir")
            if active_dir.exists():
                previous_dir = self.root / f".rollback-{manifest.id}"
                if previous_dir.exists():
                    shutil.rmtree(previous_dir)
                shutil.move(str(active_dir), str(previous_dir))
            shutil.move(str(candidate_root), str(active_dir))
            switched = True
            history = list(registry_before.get(manifest.id, {}).get("history", []))
            if previous_dir is not None and previous_dir.exists():
                archived_previous = self._archive_dir(manifest.id, previous_dir)
                history.append(
                    {
                        "version": self._manifest_version(archived_previous),
                        "path": str(archived_previous),
                    }
                )
            self._write_registry_entry(
                manifest.id,
                {
                    "enabled": True,
                    "version": manifest.version,
                    "path": str(active_dir),
                    "history": history,
                },
            )
            return self.get(manifest.id)
        except Exception as exc:  # noqa: BLE001 - transaction boundary
            if switched and active_dir is not None and active_dir.exists():
                shutil.rmtree(active_dir, ignore_errors=True)
            if active_dir is not None:
                if archived_previous is not None and archived_previous.exists():
                    shutil.move(str(archived_previous), str(active_dir))
                elif previous_dir is not None and previous_dir.exists():
                    shutil.move(str(previous_dir), str(active_dir))
            try:
                if registry_existed:
                    self._write_registry(registry_before)
                elif self.registry_path.exists():
                    self.registry_path.unlink()
            except Exception:
                logger.debug("could not restore plugin registry after failed transaction", exc_info=True)
            if isinstance(exc, PluginManagerError):
                raise
            raise PluginManagerError(f"Instalação do plugin falhou: {exc}") from exc
        finally:
            if candidate_root.exists():
                shutil.rmtree(candidate_root, ignore_errors=True)

    def enable(self, plugin_id: str) -> ManagedPlugin:
        plugin = self.get(plugin_id)
        self._validate_candidate(plugin.path)
        self._write_registry_entry(plugin.manifest.id, {"enabled": True, "version": plugin.manifest.version, "path": str(plugin.path)})
        return self.get(plugin.manifest.id)

    def update(self, source: str | Path, *, force: bool = False) -> ManagedPlugin:
        """Install a newer candidate while preserving the active version."""
        return self.install(source, force=True)

    def history(self, plugin_id: str) -> list[ManagedPlugin]:
        """Return previous validated versions, newest first."""
        normalized = plugin_id.strip().lower()
        root = self.history_dir / normalized
        if not root.exists():
            return []
        result: list[ManagedPlugin] = []
        for path in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                manifest = self._validate_candidate(path)
            except (PluginManifestError, PluginManagerError):
                continue
            result.append(ManagedPlugin(manifest=manifest, path=path, enabled=False))
        return result

    def rollback(self, plugin_id: str) -> ManagedPlugin:
        """Activate the newest previous version and archive the current one."""
        current = self.get(plugin_id)
        previous_versions = self.history(current.manifest.id)
        if not previous_versions:
            raise PluginManagerError(f"Nenhuma versão anterior para rollback: {plugin_id}")
        previous = previous_versions[0]
        old_active = self.root / f".rollback-current-{current.manifest.id}"
        if old_active.exists():
            shutil.rmtree(old_active)
        records = self._read_registry()
        try:
            shutil.move(str(current.path), str(old_active))
            shutil.move(str(previous.path), str(current.path))
            remaining = [
                item for item in records.get(current.manifest.id, {}).get("history", [])
                if Path(str(item.get("path", ""))).resolve() != previous.path.resolve()
            ]
            remaining.append({"version": current.manifest.version, "path": str(old_active)})
            self._write_registry_entry(
                current.manifest.id,
                {
                    "enabled": True,
                    "version": previous.manifest.version,
                    "path": str(current.path),
                    "history": remaining,
                },
            )
            self._archive_dir(current.manifest.id, old_active)
            self._write_registry_entry(
                current.manifest.id,
                {
                    "history": [
                        {"version": item.manifest.version, "path": str(item.path)}
                        for item in self.history(current.manifest.id)
                    ],
                },
            )
            return self.get(current.manifest.id)
        except Exception as exc:  # noqa: BLE001
            if current.path.exists():
                shutil.rmtree(current.path, ignore_errors=True)
            if old_active.exists():
                shutil.move(str(old_active), str(current.path))
            self._write_registry(records)
            raise PluginManagerError(f"Rollback falhou: {exc}") from exc

    def reload(self, plugin_id: str) -> ManagedPlugin:
        """Validate the active plugin and return the version ready to load."""
        plugin = self.get(plugin_id)
        if not plugin.enabled:
            raise PluginManagerError(f"Plugin desabilitado: {plugin_id}")
        self._validate_candidate(plugin.path)
        return self.get(plugin.manifest.id)

    def disable(self, plugin_id: str) -> ManagedPlugin:
        plugin = self.get(plugin_id)
        self._write_registry_entry(plugin.manifest.id, {"enabled": False, "version": plugin.manifest.version, "path": str(plugin.path)})
        return self.get(plugin.manifest.id)

    def uninstall(self, plugin_id: str) -> None:
        plugin = self.get(plugin_id)
        trash = self.root / f".uninstall-{plugin.manifest.id}"
        if trash.exists():
            shutil.rmtree(trash)
        shutil.move(str(plugin.path), str(trash))
        try:
            records = self._read_registry()
            records.pop(plugin.manifest.id, None)
            self._write_registry(records)
            shutil.rmtree(trash)
        except Exception as exc:  # noqa: BLE001
            if trash.exists() and not plugin.path.exists():
                shutil.move(str(trash), str(plugin.path))
            raise PluginManagerError(f"Remoção do plugin falhou: {exc}") from exc

    def _copy_local(self, source: Path, destination: Path) -> None:
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
            return
        if source.suffix.lower() != ".py":
            raise PluginManagerError("Fonte local deve ser um diretório ou arquivo .py")
        shutil.copy2(source, destination / source.name)
        adjacent_manifest = source.with_name("plugin.yaml")
        if adjacent_manifest.exists():
            shutil.copy2(adjacent_manifest, destination / "plugin.yaml")

    @staticmethod
    def _clone_git(source: str, destination: Path) -> None:
        result = subprocess.run(
            ["git", "clone", "--depth=1", source, str(destination)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise PluginManagerError(f"git clone falhou: {result.stderr.strip()[:300]}")

    @staticmethod
    def _validate_candidate(candidate: Path) -> PluginManifest:
        manifest = PluginManifest.from_file(candidate / "plugin.yaml")
        entry = manifest.entry_point or f"{manifest.id}.py"
        entry_path = (candidate / entry).resolve()
        if candidate.resolve() not in entry_path.parents or not entry_path.exists():
            raise PluginManagerError(f"entry_point fora do plugin ou ausente: {entry}")
        try:
            ast.parse(entry_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            raise PluginManagerError(f"entry_point inválido: {exc}") from exc
        return manifest

    def _archive_dir(self, plugin_id: str, source: Path) -> Path:
        target_root = self.history_dir / plugin_id
        target_root.mkdir(parents=True, exist_ok=True)
        version = self._manifest_version(source)
        target = target_root / version
        counter = 1
        while target.exists():
            target = target_root / f"{version}.{counter}"
            counter += 1
        shutil.move(str(source), str(target))
        return target

    @staticmethod
    def _manifest_version(path: Path) -> str:
        return PluginManifest.from_file(path / "plugin.yaml").version

    def _read_registry(self) -> dict[str, dict[str, Any]]:
        if not self.registry_path.exists():
            return {}
        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PluginManagerError(f"Registry inválido: {self.registry_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise PluginManagerError(f"Registry precisa ser um mapa: {self.registry_path}")
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}

    def _write_registry_entry(self, plugin_id: str, entry: dict[str, Any]) -> None:
        records = self._read_registry()
        records[plugin_id] = entry
        self._write_registry(records)

    def _write_registry(self, records: dict[str, dict[str, Any]]) -> None:
        payload = json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_tmp = tempfile.mkstemp(prefix=".registry-", suffix=".tmp", dir=self.registry_path.parent)
        tmp = Path(raw_tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.registry_path)
        finally:
            tmp.unlink(missing_ok=True)
