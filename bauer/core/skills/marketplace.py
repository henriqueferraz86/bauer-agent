"""Local skill marketplace package/install support."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...paths import get_bauer_home
from .manifest import SkillManifest, SkillManifestError


class SkillMarketplaceError(ValueError):
    pass


@dataclass(slots=True)
class SkillPackageInfo:
    id: str
    version: str
    name: str
    permissions: list[str]
    risk: str
    package_hash: str
    source_path: str
    installed_path: str | None = None
    installed_at: str | None = None
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillMarketplace:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else get_bauer_home() / "skill_marketplace"
        self.installed_root = self.root / "installed"
        self.index_path = self.root / "index.json"

    def package(self, package_dir: str | Path) -> SkillPackageInfo:
        source = Path(package_dir)
        manifest = self._read_manifest(source)
        return SkillPackageInfo(
            id=manifest.id,
            version=manifest.version,
            name=manifest.name,
            permissions=list(manifest.permissions),
            risk=manifest.risk,
            package_hash=self.hash_package(source),
            source_path=str(source),
            files=[path.as_posix() for path in self._relative_files(source)],
        )

    def install(self, package_dir: str | Path, *, yes: bool = False, force: bool = False) -> SkillPackageInfo:
        source = Path(package_dir)
        info = self.package(source)
        if not yes:
            raise SkillMarketplaceError("installation requires explicit approval")
        dest = self.installed_root / _safe_skill_dir(info.id)
        if dest.exists():
            if not force:
                raise SkillMarketplaceError(f"skill already installed: {info.id}")
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
        info.installed_path = str(dest)
        info.installed_at = datetime.now(UTC).isoformat()
        self._write_index(info)
        return info

    def uninstall(self, skill_id: str) -> SkillPackageInfo:
        index = self.index()
        record = index.get(skill_id)
        if record is None:
            raise SkillMarketplaceError(f"skill not installed: {skill_id}")
        dest = Path(str(record.get("installed_path") or ""))
        if dest.exists():
            shutil.rmtree(dest)
        index.pop(skill_id, None)
        self._save_index(index)
        return SkillPackageInfo(**record)

    def index(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}

    def installed_manifest_root(self) -> Path:
        self.installed_root.mkdir(parents=True, exist_ok=True)
        return self.installed_root

    def hash_package(self, package_dir: str | Path) -> str:
        source = Path(package_dir)
        if not source.is_dir():
            raise SkillMarketplaceError(f"package directory not found: {source}")
        digest = hashlib.sha256()
        for rel in self._relative_files(source):
            path = source / rel
            digest.update(rel.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return f"sha256:{digest.hexdigest()}"

    def _read_manifest(self, source: Path) -> SkillManifest:
        if not source.is_dir():
            raise SkillMarketplaceError(f"package directory not found: {source}")
        manifest_path = source / "skill.yaml"
        if not manifest_path.exists():
            raise SkillMarketplaceError("skill package must contain skill.yaml")
        try:
            return SkillManifest.from_file(manifest_path)
        except SkillManifestError:
            raise
        except Exception as exc:
            raise SkillMarketplaceError(str(exc)) from exc

    def _write_index(self, info: SkillPackageInfo) -> None:
        index = self.index()
        index[info.id] = info.to_dict()
        self._save_index(index)

    def _save_index(self, index: dict[str, dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _relative_files(self, source: Path) -> list[Path]:
        ignored_parts = {"__pycache__", ".git"}
        files = [
            path.relative_to(source)
            for path in source.rglob("*")
            if path.is_file() and not any(part in ignored_parts for part in path.relative_to(source).parts)
        ]
        return sorted(files, key=lambda item: item.as_posix())


def _safe_skill_dir(skill_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in skill_id)
