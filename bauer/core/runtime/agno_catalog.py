"""Safe, import-light catalog of Agno capabilities known to Bauer.

The SDK namespace is inspected as filesystem metadata only. Optional toolkits
are never imported or instantiated while showing the catalog.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.machinery import PathFinder
from typing import Any, Literal

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

class AgnoCapabilityCatalog:
    """Catalog Bauer tool adapters plus every toolkit of the installed Agno SDK.

    SDK toolkits come from ``agno_capabilities`` (AST discovery + reviewed
    manifest + operator settings). Nothing is imported or instantiated here.
    """

    def list(self, *, agent_tools: list[str] | None = None) -> list[dict[str, Any]]:
        from .agno_capabilities.catalog import catalog_entries

        enabled = {str(item).strip() for item in (agent_tools or [])}
        agno_installed = self._agno_installed()
        entries = [
            capability.to_public_dict(
                enabled_for_agent=capability.id in enabled or capability.id.removeprefix("bauer.") in enabled,
                installed=capability.package is None or agno_installed,
            )
            for capability in _BAUER_CAPABILITIES
        ]
        known = {entry["id"] for entry in entries}
        entries.extend(
            entry for entry in catalog_entries(agent_tools=list(enabled)) if entry["id"] not in known
        )
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
        """Bauer tool adapter by id/legacy name (SDK toolkits: ``agno_capabilities``)."""
        return cls.resolve_bauer_tool(capability_id)

    @staticmethod
    def _agno_installed() -> bool:
        try:
            return PathFinder.find_spec("agno") is not None
        except (ImportError, AttributeError, ValueError):
            return False
