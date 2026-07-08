"""Loop-skills: templates reutilizáveis de `/loop` autônomo.

Uma loop-skill é um arquivo YAML instalado manualmente em
`~/.bauer/loop_skills/` que define: um gatilho (regex), uma tarefa
pré-configurada pro `/loop`, orçamento/aprovação, e opcionalmente um gate
de verificação obrigatório no final.

Instalar um loop-skill é um ato de confiança explícito do usuário: a partir
da instalação, o padrão em `trigger_pattern` passa a autorizar o disparo
AUTOMÁTICO de um `/loop` autônomo sem confirmação — ver
`LoopSkillRegistry.match` e o wiring em `bauer/agent.py::run_agent_session`.
Diretório vazio = recurso é um no-op completo; nada vem pré-instalado.

Uso::

    from bauer.loop_skills import LoopSkillRegistry

    registry = LoopSkillRegistry()
    matched = registry.match(user_input)
    if matched:
        skill, m = matched
        task = skill.render_task(m)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


class LoopSkillError(Exception):
    """Erro base de loop-skills."""


class LoopSkillNotFound(LoopSkillError):
    """Loop-skill com o nome pedido não está instalada."""


class LoopSkillValidationError(LoopSkillError):
    """YAML de loop-skill inválido ou faltando campo obrigatório."""


@dataclass
class LoopSkill:
    """Um template de `/loop` pré-configurado, carregado de YAML.

    Attributes
    ----------
    name: Identificador único (nome do arquivo, sem extensão, por convenção).
    trigger_pattern: Regex — casa contra `user_input` via `re.search`.
    task_template: Texto da tarefa passado ao `/loop`. Pode referenciar
        grupos de `trigger_pattern` via `{0}`/`{1}`/... (posicionais) ou
        `{nome}` (grupos nomeados `(?P<nome>...)`).
    max_minutes/max_tool_calls/max_cost_usd/approval_mode/
        approval_risk_threshold: mesmos campos e defaults de
        `bauer.config_loader.LoopSection` — o orçamento de segurança do
        `/loop` disparado por esta skill.
    verify_command: comando shell opcional rodado no fim do loop pra
        confirmar que a tarefa realmente terminou (não só que o modelo
        *disse* que terminou). Vazio = sem verificação custom.
    verify_auto: quando True (e `verify_command` vazio), usa
        `bauer.app_verify.verify_project` — detecção automática de stack.
    tags: livre, só pra `/loop-skill list` e tags da memória gravada.
    source: path do YAML de origem (debug/list).
    """

    name: str
    trigger_pattern: str
    task_template: str
    description: str = ""
    max_minutes: int = 30
    max_tool_calls: int = 120
    max_cost_usd: float = 2.0
    approval_mode: Literal["threshold", "deny_all", "yolo"] = "threshold"
    approval_risk_threshold: float = 0.4
    verify_command: str = ""
    verify_auto: bool = False
    tags: list[str] = field(default_factory=list)
    source: str = ""

    def render_task(self, match: "re.Match[str]") -> str:
        """Preenche `task_template` com os grupos do match (posicionais e nomeados)."""
        try:
            return self.task_template.format(*match.groups(), **match.groupdict())
        except (IndexError, KeyError) as exc:
            raise LoopSkillError(
                f"loop-skill '{self.name}': task_template referencia grupo "
                f"inexistente do trigger_pattern: {exc}"
            ) from exc


_VALID_APPROVAL_MODES = ("threshold", "deny_all", "yolo")


def loop_skill_from_dict(d: dict[str, Any], *, source: str = "") -> LoopSkill:
    """Constrói um `LoopSkill` a partir de um dict já parseado (YAML/JSON).

    Levanta `LoopSkillValidationError` para campos obrigatórios ausentes ou
    inválidos (regex que não compila, approval_mode fora do enum).
    """
    name = str(d.get("name") or "").strip()
    if not name:
        raise LoopSkillValidationError("loop-skill YAML sem campo obrigatório 'name'.")

    trigger_pattern = str(d.get("trigger_pattern") or "").strip()
    if not trigger_pattern:
        raise LoopSkillValidationError(
            f"loop-skill '{name}' sem campo obrigatório 'trigger_pattern'."
        )
    try:
        re.compile(trigger_pattern)
    except re.error as exc:
        raise LoopSkillValidationError(
            f"loop-skill '{name}': trigger_pattern regex inválida: {exc}"
        ) from exc

    task_template = str(d.get("task_template") or "").strip()
    if not task_template:
        raise LoopSkillValidationError(
            f"loop-skill '{name}' sem campo obrigatório 'task_template'."
        )

    approval_mode = str(d.get("approval_mode") or "threshold")
    if approval_mode not in _VALID_APPROVAL_MODES:
        raise LoopSkillValidationError(
            f"loop-skill '{name}': approval_mode inválido {approval_mode!r} "
            f"(use um de: {', '.join(_VALID_APPROVAL_MODES)})."
        )

    tags = d.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    try:
        return LoopSkill(
            name=name,
            trigger_pattern=trigger_pattern,
            task_template=task_template,
            description=str(d.get("description") or ""),
            max_minutes=int(d.get("max_minutes", 30)),
            max_tool_calls=int(d.get("max_tool_calls", 120)),
            max_cost_usd=float(d.get("max_cost_usd", 2.0)),
            approval_mode=approval_mode,
            approval_risk_threshold=float(d.get("approval_risk_threshold", 0.4)),
            verify_command=str(d.get("verify_command") or ""),
            verify_auto=bool(d.get("verify_auto", False)),
            tags=list(tags),
            source=source,
        )
    except (TypeError, ValueError) as exc:
        raise LoopSkillValidationError(
            f"loop-skill '{name}': campo numérico inválido: {exc}"
        ) from exc


def loop_skill_from_yaml(text: str, *, source: str = "") -> LoopSkill:
    """Parseia um YAML de loop-skill. Levanta `LoopSkillValidationError`."""
    try:
        d = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise LoopSkillValidationError(f"Erro de parse YAML: {exc}") from exc
    if not isinstance(d, dict):
        raise LoopSkillValidationError("loop-skill YAML precisa ser um mapa no nível raiz.")
    return loop_skill_from_dict(d, source=source)


class LoopSkillRegistry:
    """Carrega loop-skills de `~/.bauer/loop_skills/*.yaml` e casa
    `user_input` contra os `trigger_pattern` — primeiro match vence (ordem
    alfabética de arquivo, determinístico).

    Recarrega do disco a cada chamada (`list`/`match`/`get`) — o número de
    loop-skills instaladas é pequeno, poucos arquivos; sem necessidade de
    cache/invalidação. Isso também significa que editar o YAML manualmente
    tem efeito imediato, sem reiniciar a sessão.
    """

    def __init__(self, loop_skills_dir: "Path | str | None" = None) -> None:
        if loop_skills_dir is None:
            from .paths import loop_skills_dir as _lsd
            loop_skills_dir = _lsd()
        self._dir = Path(loop_skills_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[LoopSkill]:
        """Todas as loop-skills instaladas. YAML inválido é ignorado (não derruba o resto)."""
        skills: list[LoopSkill] = []
        for p in sorted(self._dir.glob("*.yaml")):
            try:
                skills.append(loop_skill_from_yaml(p.read_text(encoding="utf-8"), source=str(p)))
            except LoopSkillError:
                continue
        return skills

    def match(self, user_input: str) -> "tuple[LoopSkill, re.Match[str]] | None":
        """Primeira loop-skill cujo trigger_pattern casa `user_input`. `None` se nenhuma."""
        for skill in self.list():
            m = re.search(skill.trigger_pattern, user_input)
            if m:
                return skill, m
        return None

    def get(self, name: str) -> LoopSkill:
        for skill in self.list():
            if skill.name == name:
                return skill
        raise LoopSkillNotFound(f"loop-skill '{name}' não instalada. Veja: /loop-skill list")
