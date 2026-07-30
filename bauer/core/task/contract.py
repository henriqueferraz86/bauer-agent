"""TaskContract — o que a tarefa PODE tocar e o que precisa provar.

O gargalo que o S11 revelou. O gate de testes valida o **projeto**; a tarefa tem
**escopo**. Sem um contrato, foram necessários dois contornos:

- o gate de testes virou ratchet (reprova só falha nova), porque não sabia quais
  testes importavam para *aquela* tarefa;
- o gate de escopo foi descartado, porque não havia com o que comparar os
  arquivos alterados.

O contrato fecha os dois. Ele não substitui o Planner (``autonomous_planner.py``,
completo) nem os schemas inter-agente (``bauer/contracts.py``, CONTRACT-001) —
acrescenta a única coisa que faltava: **escopo, critérios de aceite e como
validar**, por tarefa.

Formato YAML, carregável de ``.bauer/task.yaml`` no workspace ou montado em
memória. Deliberadamente simples: sem contrato, tudo segue como hoje.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class Scope(BaseModel):
    """O perímetro da tarefa — o que ela pode e não pode alterar.

    Caminhos são relativos ao workspace. ``allowed`` vazio significa "sem
    restrição declarada" (o sandbox do tool_router continua valendo), NÃO
    "nada permitido" — um contrato pela metade não pode paralisar o agente.
    """

    allowed: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)

    def viola(self, rel: str) -> "str | None":
        """Motivo da violação, ou None. ``rel`` é relativo ao workspace."""
        alvo = rel.replace("\\", "/").lstrip("./")
        for padrao in self.forbidden:
            if _casa(alvo, padrao):
                return f"'{rel}' está em scope.forbidden ('{padrao}')"
        if not self.allowed:
            return None
        for padrao in self.allowed:
            if _casa(alvo, padrao):
                return None
        return f"'{rel}' está fora de scope.allowed ({', '.join(self.allowed)})"


def _casa(caminho: str, padrao: str) -> bool:
    """Prefixo de diretório ou arquivo exato; ``*`` simples via fnmatch."""
    from fnmatch import fnmatch

    bruto = padrao.replace("\\", "/").strip()
    # `.`, `./`, `*` e `**` são a forma natural de escrever "o projeto inteiro".
    # Sem este caso, `allowed: ["."]` não casava com NADA — o normalizador comia
    # o ponto e sobrava string vazia, então o contrato mais permissivo possível
    # virava o mais restritivo. Inverso exato da intenção de quem escreveu.
    if bruto in (".", "./", "*", "**", "/"):
        return True
    p = bruto.lstrip("./").rstrip("/")
    if not p:
        return False
    return caminho == p or caminho.startswith(p + "/") or fnmatch(caminho, p)


class Validation(BaseModel):
    """Como provar que a tarefa foi feita.

    ``timeout_seconds`` e ``selection`` não são enfeite: um Validator sem teto
    transforma o laço autônomo em refém da suíte, e rodar a suíte inteira quando
    a tarefa mexeu num arquivo é desperdício que o usuário paga em tempo real.
    """

    commands: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(ge=1, default=600)
    #: "related" = só o que a tarefa toca | "full" = suíte inteira
    selection: str = "related"

    @field_validator("selection")
    @classmethod
    def _sel(cls, v: str) -> str:
        return v if v in ("related", "full") else "related"


class TaskContract(BaseModel):
    """Contrato de uma tarefa autônoma."""

    task_id: str = ""
    objective: str = ""
    scope: Scope = Field(default_factory=Scope)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    validation: Validation = Field(default_factory=Validation)
    #: none | worktree | container — liga o contrato ao isolamento (S12)
    isolation: str = "none"
    risk_level: str = "medium"
    requires_approval: bool = False

    # ── carga ────────────────────────────────────────────────────────────────

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "TaskContract":
        return cls.model_validate(data or {})

    @classmethod
    def from_yaml(cls, path: "str | Path") -> "TaskContract":
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.from_mapping(raw if isinstance(raw, dict) else {})

    @classmethod
    def descobrir(cls, workspace: "str | Path") -> "Optional[TaskContract]":
        """Contrato do workspace, se houver. None = sem contrato, tudo como hoje.

        Nunca levanta: contrato ilegível é ausência de contrato, não erro — do
        contrário um YAML mal editado paralisaria toda execução.
        """
        alvo = Path(workspace) / ".bauer" / "task.yaml"
        if not alvo.is_file():
            return None
        try:
            return cls.from_yaml(alvo)
        except Exception as exc:  # noqa: BLE001
            from ...logging_config import log_suppressed
            log_suppressed("task_contract.descobrir", exc)
            return None

    # ── uso ──────────────────────────────────────────────────────────────────

    def violacoes_de_escopo(self, alterados: "list[str]") -> list[str]:
        return [m for m in (self.scope.viola(a) for a in alterados) if m]

    @property
    def tem_escopo(self) -> bool:
        return bool(self.scope.allowed or self.scope.forbidden)
