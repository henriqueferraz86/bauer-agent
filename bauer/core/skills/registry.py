"""Formal skill registry."""

from __future__ import annotations

from pathlib import Path

from .manifest import SkillManifest, SkillManifestError


class SkillRegistry:
    def __init__(self, roots: list[str | Path] | None = None):
        default_root = Path(__file__).resolve().parents[2] / "data" / "skills"
        formal_root = Path(__file__).resolve().parents[2] / "data" / "skill_manifests"
        self.roots = [Path(root) for root in (roots or [formal_root, default_root])]

    def list(self) -> list[SkillManifest]:
        manifests: dict[str, SkillManifest] = {}
        for path in self._manifest_paths():
            try:
                manifest = SkillManifest.from_file(path)
            except (OSError, SkillManifestError, ValueError):
                continue
            manifests.setdefault(manifest.id, manifest)
        return sorted(manifests.values(), key=lambda item: item.id)

    def get(self, skill_id: str) -> SkillManifest | None:
        for manifest in self.list():
            if manifest.id == skill_id:
                return manifest
        return None

    def find_by_capability(self, capability: str) -> list[SkillManifest]:
        needle = capability.strip().lower()
        return [
            manifest
            for manifest in self.list()
            if any(capability.lower() == needle for capability in manifest.capabilities)
        ]

    def capabilities(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for manifest in self.list():
            for capability in manifest.capabilities:
                result.setdefault(capability, []).append(manifest.id)
        return dict(sorted(result.items()))

    def validate_all(self) -> tuple[list[SkillManifest], list[str]]:
        valid: list[SkillManifest] = []
        errors: list[str] = []
        for path in self._manifest_paths():
            try:
                valid.append(SkillManifest.from_file(path))
            except (OSError, SkillManifestError, ValueError) as exc:
                errors.append(f"{path}: {exc}")
        return valid, errors

    def _manifest_paths(self) -> list[Path]:
        paths: list[Path] = []
        for root in self.roots:
            if not root.exists():
                continue
            paths.extend(sorted(root.rglob("skill.yaml")))
            paths.extend(sorted(path for path in root.rglob("*.yaml") if path.name != "skill.yaml"))
        return paths
