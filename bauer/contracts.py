"""CONTRACT-001 — Schemas Pydantic para contratos inter-agent.

Define as estruturas de dados canônicas trocadas entre:
  - Planner   → Executor   (PlannerOutput)
  - Executor  → Tool       (ToolCallSchema / ToolResultSchema)
  - Executor  → Critic     (ExecutionSummary)
  - Agent     → Agent      (AgentMessage)

Benefícios:
  - Validação determinística sem LLM
  - Serialização/deserialização confiável (JSON ↔ Pydantic)
  - Documentação como código (schema = contrato)
  - Facilita testes e mocking
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ─── Enums ───────────────────────────────────────────────────────────────────


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PermissionLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    SYSTEM = "system"


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


# ─── Tool contracts ───────────────────────────────────────────────────────────


class ToolCallSchema(BaseModel):
    """Chamada de tool emitida pelo agente para o ToolRouter.

    Exemplo:
        ToolCallSchema(action="write_file", args={"path": "out.py", "content": "..."})
    """

    action: str = Field(min_length=1, description="Nome da tool a executar")
    args: dict[str, Any] = Field(default_factory=dict, description="Argumentos da tool")
    call_id: str | None = Field(default=None, description="ID único desta chamada (para rastreamento)")
    timestamp: float = Field(default_factory=time.time)

    @field_validator("action")
    @classmethod
    def action_must_be_identifier(cls, v: str) -> str:
        if not v.replace("_", "").isalnum():
            raise ValueError(f"action '{v}' contém caracteres inválidos.")
        return v

    def to_json_dict(self) -> dict:
        return {"action": self.action, "args": self.args}


class ToolResultSchema(BaseModel):
    """Resultado de execução de tool retornado pelo ToolRouter.

    Captura sucesso ou falha com metadados para auditoria.
    """

    action: str
    call_id: str | None = None
    success: bool
    result: str = Field(description="Saída textual da tool (ou mensagem de erro)")
    error_type: str | None = Field(default=None, description="Tipo de exceção em caso de falha")
    elapsed_ms: int = Field(default=0, ge=0)
    dry_run: bool = Field(default=False)
    timestamp: float = Field(default_factory=time.time)


# ─── Planner contracts ────────────────────────────────────────────────────────


class PlanStep(BaseModel):
    """Um passo do plano gerado pelo Planner."""

    id: int = Field(ge=1)
    goal: str = Field(min_length=1, description="Objetivo deste passo em linguagem natural")
    tools: bool = Field(default=True, description="Se este passo usa tools")
    depends_on: list[int] = Field(default_factory=list, description="IDs de passos que devem completar antes")
    model_hint: str | None = Field(default=None, description="Modelo recomendado para este passo")
    timeout_seconds: int = Field(default=120, ge=5, le=3600)
    max_retries: int = Field(default=2, ge=0, le=10)

    @field_validator("depends_on")
    @classmethod
    def no_self_dependency(cls, v: list[int], info) -> list[int]:
        if hasattr(info, "data") and "id" in info.data:
            if info.data["id"] in v:
                raise ValueError("Um passo não pode depender de si mesmo.")
        return v


class PlannerOutput(BaseModel):
    """Saída completa do Planner: objetivo + lista de passos com dependências.

    Validada antes de ser passada ao Executor.
    """

    objective: str = Field(min_length=1, description="Objetivo principal da tarefa")
    steps: list[PlanStep] = Field(min_length=1, description="Passos do plano")
    estimated_steps: int = Field(default=0, ge=0)
    planner_model: str | None = None
    created_at: float = Field(default_factory=time.time)

    def model_post_init(self, __context: Any) -> None:
        if self.estimated_steps == 0:
            self.estimated_steps = len(self.steps)

    @field_validator("steps")
    @classmethod
    def ids_must_be_unique(cls, v: list[PlanStep]) -> list[PlanStep]:
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("IDs dos passos devem ser únicos.")
        return v

    def step_ids(self) -> set[int]:
        return {s.id for s in self.steps}

    def to_legacy_dict(self) -> dict:
        """Converte para formato legado usado pelo orchestrator (list of dicts)."""
        return {
            "objective": self.objective,
            "steps": [
                {
                    "id": s.id,
                    "goal": s.goal,
                    "tools": s.tools,
                    "depends_on": s.depends_on,
                }
                for s in self.steps
            ],
        }

    @classmethod
    def from_legacy_dict(cls, data: dict) -> "PlannerOutput":
        """Cria PlannerOutput a partir do formato legado do orchestrator."""
        return cls(
            objective=data.get("objective", ""),
            steps=[PlanStep(**s) for s in data.get("steps", [])],
        )


# ─── Execution contracts ──────────────────────────────────────────────────────


class StepResult(BaseModel):
    """Resultado de execução de um passo do plano.

    Compatível com orchestrator.StepResult (dataclass legacy),
    mas com validação Pydantic e serialização garantida.
    """

    id: int = Field(ge=1)
    goal: str
    model_used: str = ""
    response: str = ""
    tool_log: list[dict] = Field(default_factory=list)
    status: StepStatus = StepStatus.DONE
    error: str | None = None
    elapsed_ms: int = Field(default=0, ge=0)
    timestamp: float = Field(default_factory=time.time)

    def is_success(self) -> bool:
        return self.status == StepStatus.DONE

    def summary(self, max_chars: int = 200) -> str:
        if self.error:
            return f"[FALHA] {self.error[:max_chars]}"
        return self.response[:max_chars]


class ExecutionSummary(BaseModel):
    """Resumo completo de uma execução multi-passo — passado ao Critic/Synthesizer."""

    task: str = Field(description="Tarefa original do usuário")
    total_steps: int = Field(ge=0)
    completed_steps: int = Field(ge=0)
    failed_steps: int = Field(ge=0)
    results: list[StepResult] = Field(default_factory=list)
    final_output: str = ""
    total_elapsed_ms: int = Field(default=0, ge=0)
    created_at: float = Field(default_factory=time.time)

    @property
    def success_rate(self) -> float:
        if self.total_steps == 0:
            return 0.0
        return self.completed_steps / self.total_steps

    def failed_result_summaries(self) -> list[str]:
        return [r.summary() for r in self.results if not r.is_success()]


# ─── Agent message contract ───────────────────────────────────────────────────


class AgentMessage(BaseModel):
    """Mensagem trocada entre agents no protocolo multi-agent.

    Usado em delegate_task, mixture_of_agents e qualquer
    comunicação agent-to-agent.
    """

    role: MessageRole
    content: str | list[dict]  # str para texto simples; list para multimodal
    agent_id: str | None = None
    task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)

    def to_openai_dict(self) -> dict:
        """Converte para formato OpenAI chat message."""
        return {
            "role": self.role.value,
            "content": self.content,
        }


class AgentHandoff(BaseModel):
    """Payload de handoff entre agents (delegate_task result, kanban_complete payload)."""

    from_agent: str
    to_agent: str | None = None
    task_id: str | None = None
    result: str
    artifacts: list[str] = Field(default_factory=list, description="Caminhos de arquivos gerados")
    next_steps: list[str] = Field(default_factory=list, description="Sugestões para próximo agent")
    success: bool = True
    timestamp: float = Field(default_factory=time.time)


# ─── Utilitários ─────────────────────────────────────────────────────────────


def validate_tool_call(action: str, args: dict) -> ToolCallSchema:
    """Valida e retorna um ToolCallSchema. Levanta ValidationError se inválido."""
    return ToolCallSchema(action=action, args=args)


def validate_planner_output(data: dict) -> PlannerOutput:
    """Valida output do planner. Levanta ValidationError se inválido."""
    if "steps" not in data:
        raise ValueError("PlannerOutput deve conter 'steps'.")
    if "objective" not in data:
        raise ValueError("PlannerOutput deve conter 'objective'.")
    return PlannerOutput.model_validate(data)
