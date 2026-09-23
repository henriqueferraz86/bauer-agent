"""Safe, import-light catalog of Agno capabilities known to Bauer.

The SDK namespace is inspected as filesystem metadata only. Optional toolkits
are never imported or instantiated while showing the catalog.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.machinery import PathFinder
from pathlib import Path
from typing import Any, List, Literal

CapabilityRisk = Literal["read", "write", "execute", "external"]


@dataclass(frozen=True, slots=True)
class AgnoCapability:
    id: str
    name: str
    category: str
    description: str
    source: Literal["bauer", "agno"]
    actions: tuple[str, ...] = ()
    risk: CapabilityRisk = "read"
    factory_registered: bool = False
    package: str | None = "agno"
    module_family: bool = False
    required_config: tuple[str, ...] = ()

    def to_public_dict(self, *, enabled_for_agent: bool = False, installed: bool = True) -> dict[str, Any]:
        """Return only non-sensitive metadata suitable for API/UI responses."""
        state = "disabled"
        if not installed:
            state = "dependency_missing"
        elif not self.factory_registered:
            state = "adapter_required"
        elif self.required_config:
            state = "needs_configuration"
        elif enabled_for_agent:
            state = "enabled"

        result = asdict(self)
        result["actions"] = list(self.actions)
        result["required_config"] = list(self.required_config)
        result["state"] = state
        result["enabled_for_agent"] = enabled_for_agent
        result["installed"] = installed
        return result


_BAUER_CAPABILITIES: tuple[AgnoCapability, ...] = (
    AgnoCapability("bauer.read_file", "Ler arquivo", "workspace", "Lê arquivo no workspace autorizado.", "bauer", ("read",), "read", True, package=None),
    AgnoCapability("bauer.list_dir", "Listar diretório", "workspace", "Lista conteúdo de diretório autorizado.", "bauer", ("read",), "read", True, package=None),
    AgnoCapability("bauer.search_text", "Buscar texto", "workspace", "Busca texto no workspace autorizado.", "bauer", ("read",), "read", True, package=None),
    AgnoCapability("bauer.write_file", "Escrever arquivo", "workspace", "Escreve arquivo sob as políticas Bauer.", "bauer", ("write",), "write", True, package=None),
    AgnoCapability("bauer.run_command", "Executar comando", "execution", "Executa comando sob shell runner e policy Bauer.", "bauer", ("execute",), "execute", True, package=None),
    AgnoCapability("bauer.web_search", "Busca web", "research", "Pesquisa web através do ToolRouter.", "bauer", ("search",), "external", True, package=None),
    AgnoCapability("bauer.memory", "Memória", "memory", "Consulta ou atualiza memória via ToolRouter.", "bauer", ("read", "write"), "write", True, package=None),
)

_FAMILY_LABELS = {
    "code": "Desenvolvimento",
    "file": "Arquivos",
    "finance": "Finanças",
    "google": "Google",
    "knowledge": "Knowledge",
    "mcp": "MCP",
    "streamlit": "Streamlit",
}
_IGNORED_FAMILIES = {"models"}


class AgnoCapabilityCatalog:
    """Catalog static Bauer adapters and discoverable Agno SDK tool families."""

    def list(self, *, agent_tools: list[str] | None = None) -> list[dict[str, Any]]:
        enabled = {str(item).strip() for item in (agent_tools or [])}
        agno_installed = self._agno_installed()
        entries = [
            capability.to_public_dict(
                enabled_for_agent=capability.id in enabled or capability.id.removeprefix("bauer.") in enabled,
                installed=capability.package is None or agno_installed,
            )
            for capability in _BAUER_CAPABILITIES
        ]
        for family in self._discover_sdk_families():
            entries.append(family.to_public_dict(installed=True))
        return sorted(entries, key=lambda item: (str(item["category"]), str(item["name"])))

    @staticmethod
    def resolve_bauer_tool(tool_name: str) -> AgnoCapability | None:
        normalized = tool_name.strip()
        return next(
            (
                capability
                for capability in _BAUER_CAPABILITIES
                if normalized in {capability.id, capability.id.removeprefix("bauer.")}
            ),
            None,
        )

    @classmethod
    def find(cls, capability_id: str) -> AgnoCapability | None:
        normalized = capability_id.strip()
        known = next(
            (
                item
                for item in _BAUER_CAPABILITIES
                if normalized in {item.id, item.id.removeprefix("bauer.")}
            ),
            None,
        )
        if known is not None:
            return known
        return next((item for item in cls._discover_sdk_families() if item.id == normalized), None)

    @staticmethod
    def _agno_installed() -> bool:
        try:
            return PathFinder.find_spec("agno") is not None
        except (ImportError, AttributeError, ValueError):
            return False

    @classmethod
    def _discover_sdk_families(cls) -> List[AgnoCapability]:
        """Enumerate top-level ``agno.tools`` folders without importing them."""
        try:
            agno_spec = PathFinder.find_spec("agno")
            if agno_spec is None or not agno_spec.submodule_search_locations:
                return []
            tools_spec = PathFinder.find_spec("agno.tools", list(agno_spec.submodule_search_locations))
            if tools_spec is None or not tools_spec.submodule_search_locations:
                return []
            families: set[str] = set()
            for root in tools_spec.submodule_search_locations:
                path = Path(root)
                if not path.is_dir():
                    continue
                families.update(
                    child.name
                    for child in path.iterdir()
                    if child.is_dir()
                    and not child.name.startswith("_")
                    and child.name not in _IGNORED_FAMILIES
                )
            return [
                AgnoCapability(
                    id=f"agno.family.{family}",
                    name=_FAMILY_LABELS.get(family, family.replace("_", " ").title()),
                    category="Agno SDK",
                    description="Família detectada no SDK instalado; requer registro e revisão de adapter antes de habilitar.",
                    source="agno",
                    risk="external",
                    package="agno",
                    module_family=True,
                )
                for family in sorted(families)
            ]
        except (OSError, ImportError, AttributeError, ValueError):
            return []
