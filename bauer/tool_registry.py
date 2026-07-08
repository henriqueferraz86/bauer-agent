"""Registry pattern para tools externas do Bauer (TOOL-1).

Permite registrar tools fora do tool_router.py via decorador.
O ToolRouter integra automaticamente todas as tools registradas.

Exemplo de uso:
    # Em qualquer arquivo do projeto ou plugin:
    from bauer.tool_registry import registry

    @registry.tool(
        "weather",
        description="Retorna clima atual de uma cidade",
        args={"city": "str — nome da cidade (obrigatorio)"},
        permission="network",
        risk="low",
    )
    def weather_tool(args: dict) -> str:
        city = args.get("city", "")
        return f"Clima em {city}: ensolarado 22°C"

    # O ToolRouter usa automaticamente sem modificação:
    router = ToolRouter(workspace=".")
    result = router.execute({"action": "weather", "args": {"city": "SP"}})

Regras:
- Tools externas têm PRIORIDADE sobre built-ins de mesmo nome (override explícito).
- Erros no fn são propagados como ToolError pelo ToolRouter.
- O singleton é resetável em testes via ToolRegistry.reset().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# ToolDefinition
# ---------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    """Definição completa de uma tool registrada externamente.

    Attributes:
        name: Identificador único (ex: 'weather').
        fn: Função (args: dict) -> str.
        description: Descrição legível.
        args: Schema de argumentos {nome: "tipo — descrição"}.
        permission: Nível de permissão (read/write/execute/network/system).
        risk: Nível de risco (low/medium/high/critical).
        requires_approval: Se True, ToolRouter deve pedir confirmação humana.
        tags: Tags opcionais para categorização/filtragem.
    """

    name: str
    fn: Callable[[dict], str]
    description: str
    args: dict[str, str]
    permission: str = "read"
    risk: str = "low"
    requires_approval: bool = False
    tags: list[str] = field(default_factory=list)

    def to_info(self) -> dict:
        """Retorna dict compatível com ToolRouter.tool_info()."""
        return {
            "description": self.description,
            "args": self.args,
            "permission": self.permission,
            "risk": self.risk,
            "requires_approval": self.requires_approval,
            "tags": self.tags,
        }


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Singleton que mantém tools registradas externamente.

    Não substitui os tools built-in do ToolRouter.
    Em caso de conflito de nome, tools externas têm precedência.
    """

    _instance: "ToolRegistry | None" = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get(cls) -> "ToolRegistry":
        """Retorna a instância singleton global."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reseta o singleton — use APENAS em testes (evita vazamento entre testes)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Registro programático
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        fn: Callable[[dict], str],
        *,
        description: str,
        args: dict[str, str],
        permission: str = "read",
        risk: str = "low",
        requires_approval: bool = False,
        tags: list[str] | None = None,
    ) -> ToolDefinition:
        """Registra uma tool no registry.

        Args:
            name: Identificador único (ex: 'weather').
            fn: Função que recebe dict e retorna str.
            description: Descrição legível.
            args: Schema {nome: "tipo — descrição"}.
            permission: 'read' | 'write' | 'execute' | 'network' | 'system'.
            risk: 'low' | 'medium' | 'high' | 'critical'.
            requires_approval: True para exigir confirmação humana no ToolRouter.
            tags: Tags opcionais.

        Returns:
            ToolDefinition criada.
        """
        if not name or not name.strip():
            raise ValueError("ToolRegistry.register: 'name' nao pode ser vazio.")
        if not callable(fn):
            raise ValueError(f"ToolRegistry.register: 'fn' para '{name}' deve ser callable.")

        td = ToolDefinition(
            name=name,
            fn=fn,
            description=description,
            args=args,
            permission=permission,
            risk=risk,
            requires_approval=requires_approval,
            tags=list(tags) if tags else [],
        )
        self._tools[name] = td
        return td

    # ------------------------------------------------------------------
    # Decorator
    # ------------------------------------------------------------------

    def tool(
        self,
        name: str,
        *,
        description: str,
        args: dict[str, str],
        permission: str = "read",
        risk: str = "low",
        requires_approval: bool = False,
        tags: list[str] | None = None,
    ) -> Callable:
        """Decorator para registrar uma função como tool.

        A função decorada NÃO é modificada — ela retorna o fn original.
        O registro é um side-effect no singleton.

        Exemplo:
            @registry.tool(
                "echo",
                description="Retorna o texto passado",
                args={"text": "str — texto a ecoar"},
            )
            def echo(args: dict) -> str:
                return args.get("text", "")
        """
        def decorator(fn: Callable[[dict], str]) -> Callable[[dict], str]:
            self.register(
                name,
                fn,
                description=description,
                args=args,
                permission=permission,
                risk=risk,
                requires_approval=requires_approval,
                tags=tags,
            )
            return fn

        return decorator

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    def get_tool(self, name: str) -> ToolDefinition | None:
        """Retorna ToolDefinition ou None se não registrada."""
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        """Retorna lista ordenada de nomes de tools registradas."""
        return sorted(self._tools)

    def tool_info(self, name: str) -> dict | None:
        """Retorna dict de info ou None se não encontrada."""
        td = self._tools.get(name)
        return td.to_info() if td else None

    def unregister(self, name: str) -> bool:
        """Remove tool do registry. Retorna True se existia."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def clear(self) -> None:
        """Remove todas as tools registradas (útil em testes)."""
        self._tools.clear()

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        return f"ToolRegistry(tools={self.list_names()})"


# ---------------------------------------------------------------------------
# Singleton global — importe este objeto nos seus plugins/módulos
# ---------------------------------------------------------------------------

registry: ToolRegistry = ToolRegistry.get()
