"""Company Manager do Bauer Agent — suporte multi-empresa.

Cada empresa tem namespace isolado:
  companies/<slug>/
    company.yaml        — metadados e contexto da empresa
    agents.yaml         — agents específicos desta empresa
    workspace/          — PROJECT.md, TASKS.md (isolado)
    memory/             — SKILLS_LEARNED.md, MODEL_EXPERIENCE.md (isolado)

Empresa ativa é salva em .bauer_active_company (arquivo local, ignorado pelo git).

Padrão de arquitetura: Hub-Spoke com Namespace Isolation.
  - Infraestrutura compartilhada (CLI, motor de tools, modelo)
  - Dados isolados por empresa (config, agents, workspace, memória, logs)

Resolução de agents em ordem:
  1. Agents da empresa ativa (companies/<slug>/agents.yaml)
  2. Agents globais (agents.yaml na raiz)
  3. PERSONAS embutidas (agent_registry.PERSONAS)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


_ACTIVE_FILE = Path(".bauer_active_company")

_TASKS_TEMPLATE = """\
# TASKS.md — Tarefas de {name}

Status validos: TODO | READY | IN_PROGRESS | DONE | BLOCKED | FAILED

---
"""

_PROJECT_TEMPLATE = """\
# Projeto: {name}

criado: {created_at}

## Descrição

{name} — {industry}

## Objetivos

<!-- Liste os objetivos do projeto aqui -->

## Stack

<!-- Tecnologias e ferramentas utilizadas -->

---
"""

_SKILLS_TEMPLATE = """\
# SKILLS_LEARNED.md — {name}

## Habilidades e Aprendizados do Time

<!-- Registre aqui skills adquiridas, padrões adotados e lições aprendidas -->

---
"""

_COMPANY_MEMORY_TEMPLATE = """\
# Memória da Empresa — {name}

## Contexto Estratégico
- Setor: {industry}
- Criada em: {created_at}

## Decisões Importantes
<!-- Data | Decisão | Responsável | Impacto -->

## Projetos em Andamento
<!-- Projeto | Status | Responsável | Prazo -->

## OKRs Ativos
<!-- Objetivo | Key Results | Prazo -->

## Última Atualização
- Data: (atualizada automaticamente pelos agents)
"""

_COMPANY_TEMPLATE = """\
# company.yaml — {name}
# Edite livremente. Campos opcionais podem ser omitidos.

id: {id}
name: "{name}"

# Idioma padrão das respostas dos agents (pt | en | es | ...)
language: pt

# Sobrescreve model/provider do config.yaml para esta empresa (opcional)
# model: phi4-mini
# provider: ollama

# Contexto injetado automaticamente em TODOS os system prompts desta empresa.
# Descreva: setor, tamanho, ferramentas usadas, tom, siglas internas, etc.
context: |
  {name} é uma empresa do setor {industry}.
  Responda sempre em português brasileiro.
  Seja preciso, objetivo e profissional.

# Departamentos ativos — usados para sugerir personas relevantes
departments:
  - technology
  - finance
  - marketing
  - sales
  - hr
  - legal
  - operations
  - customer_support
  - data_analytics
  - product

# Restringe tools disponíveis para TODOS os agents desta empresa (opcional)
# tools_allowed:
#   - list_dir
#   - read_file
#   - write_file
#   - search_text
#   - web_search
#   - web_fetch

# Prefixo automático nos nomes dos agents desta empresa (opcional)
# agent_prefix: "{id}"

criado_em: {created_at}
"""


class CompanyManagerError(Exception):
    pass


@dataclass
class CompanyDef:
    id: str                          # slug: acme-corp
    name: str                        # "Acme Corp"
    language: str = "pt"
    model: str = ""                  # sobrescreve config.yaml
    provider: str = ""               # sobrescreve config.yaml
    context: str = ""                # injetado em todos os system prompts
    departments: list[str] = field(default_factory=list)
    tools_allowed: list[str] = field(default_factory=list)
    agent_prefix: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @staticmethod
    def valid_id(slug: str) -> bool:
        return bool(re.match(r"^[a-z0-9][a-z0-9_-]{1,39}$", slug))

    def base_dir(self, companies_root: Path) -> Path:
        return companies_root / self.id

    def agents_file(self, companies_root: Path) -> Path:
        return self.base_dir(companies_root) / "agents.yaml"

    def workspace_dir(self, companies_root: Path) -> Path:
        return self.base_dir(companies_root) / "workspace"

    def memory_dir(self, companies_root: Path) -> Path:
        return self.base_dir(companies_root) / "memory"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "language": self.language,
            "context": self.context,
            "created_at": self.created_at,
        }
        if self.model:
            d["model"] = self.model
        if self.provider:
            d["provider"] = self.provider
        if self.departments:
            d["departments"] = self.departments
        if self.tools_allowed:
            d["tools_allowed"] = self.tools_allowed
        if self.agent_prefix:
            d["agent_prefix"] = self.agent_prefix
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CompanyDef":
        raw_ts = d.get("criado_em", d.get("created_at", ""))
        # YAML pode parsear datas ISO como datetime — converte para str
        if hasattr(raw_ts, "isoformat"):
            created_at_str = raw_ts.isoformat()
        else:
            created_at_str = str(raw_ts) if raw_ts else ""
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            language=d.get("language", "pt"),
            model=d.get("model", ""),
            provider=d.get("provider", ""),
            context=d.get("context", ""),
            departments=d.get("departments", []),
            tools_allowed=d.get("tools_allowed", []),
            agent_prefix=d.get("agent_prefix", ""),
            created_at=created_at_str,
        )


class CompanyManager:
    """Gerencia empresas em companies/<slug>/.

    Usage:
        cm = CompanyManager()
        cm.create("acme-corp", "Acme Corp", industry="fintech")
        cm.set_active("acme-corp")
        company = cm.get_active()
    """

    def __init__(self, companies_root: str | Path = "companies"):
        self.root = Path(companies_root).resolve()

    # --- CRUD ----------------------------------------------------------------

    def create(
        self,
        slug: str,
        name: str,
        industry: str = "tecnologia",
        language: str = "pt",
    ) -> CompanyDef:
        """Cria estrutura de diretórios e company.yaml para uma nova empresa."""
        if not CompanyDef.valid_id(slug):
            raise CompanyManagerError(
                f"ID inválido: '{slug}'. Use letras minúsculas, números e hífens (ex: acme-corp)."
            )
        base = self.root / slug
        if base.exists():
            raise CompanyManagerError(f"Empresa '{slug}' já existe em {base}.")

        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # Cria estrutura completa
        base.mkdir(parents=True, exist_ok=True)
        ws = base / "workspace"
        mem = base / "memory"
        ws.mkdir(exist_ok=True)
        mem.mkdir(exist_ok=True)

        # Subdiretórios do workspace
        (ws / "agents").mkdir(exist_ok=True)
        (ws / "specs").mkdir(exist_ok=True)
        (ws / "planos").mkdir(exist_ok=True)

        # company.yaml com template rico
        (base / "company.yaml").write_text(
            _COMPANY_TEMPLATE.format(
                id=slug, name=name, industry=industry, created_at=created_at,
            ),
            encoding="utf-8",
        )

        # agents.yaml da empresa (agents específicos + herdam do global)
        (base / "agents.yaml").write_text(
            f"# agents.yaml — {name}\n"
            f"# Agents específicos desta empresa.\n"
            f"# Para criar um agent: bauer agent create --agents <este arquivo>\n"
            f"agents: []\n",
            encoding="utf-8",
        )

        # workspace/PROJECT.md
        (ws / "PROJECT.md").write_text(
            _PROJECT_TEMPLATE.format(name=name, industry=industry, created_at=created_at),
            encoding="utf-8",
        )

        # workspace/TASKS.md
        (ws / "TASKS.md").write_text(
            _TASKS_TEMPLATE.format(name=name),
            encoding="utf-8",
        )

        # memory/MEMORY.md — memória estratégica da empresa
        (mem / "MEMORY.md").write_text(
            _COMPANY_MEMORY_TEMPLATE.format(
                name=name, industry=industry, created_at=created_at,
            ),
            encoding="utf-8",
        )

        # memory/SKILLS_LEARNED.md
        (mem / "SKILLS_LEARNED.md").write_text(
            _SKILLS_TEMPLATE.format(name=name),
            encoding="utf-8",
        )

        return CompanyDef(id=slug, name=name, language=language, created_at=created_at)

    def scaffold_workspace(self, slug: str) -> list[Path]:
        """Garante estrutura completa para uma empresa existente. Não sobrescreve arquivos.

        Returns:
            Lista de arquivos/pastas criados.
        """
        base = self.root / slug
        if not base.exists():
            raise CompanyManagerError(f"Empresa '{slug}' não encontrada em {base}.")

        company = self.get(slug)
        if not company:
            raise CompanyManagerError(f"Falha ao carregar '{slug}'.")

        name = company.name
        created_at = company.created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

        created: list[Path] = []

        def _mkdir(p: Path) -> None:
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                created.append(p)

        def _write(p: Path, content: str) -> None:
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                created.append(p)

        ws = base / "workspace"
        mem = base / "memory"

        _mkdir(ws)
        _mkdir(mem)
        _mkdir(ws / "agents")
        _mkdir(ws / "specs")
        _mkdir(ws / "planos")

        _write(ws / "PROJECT.md",
               _PROJECT_TEMPLATE.format(name=name, industry="tecnologia", created_at=created_at))
        _write(ws / "TASKS.md",
               _TASKS_TEMPLATE.format(name=name))
        _write(mem / "MEMORY.md",
               _COMPANY_MEMORY_TEMPLATE.format(name=name, industry="tecnologia", created_at=created_at))
        _write(mem / "SKILLS_LEARNED.md",
               _SKILLS_TEMPLATE.format(name=name))

        # Garante agents.yaml na raiz da empresa
        _write(base / "agents.yaml",
               f"# agents.yaml — {name}\n"
               f"# Agents específicos desta empresa.\n"
               f"agents: []\n")

        return created

    def get(self, slug: str) -> CompanyDef | None:
        """Carrega CompanyDef pelo ID.

        Suporta dois formatos de company.yaml:
          • Padrão Bauer: campos top-level id, name, language, context, ...
          • Formato personalizado: company: { name: "...", description: "...", vision: "...", ... }
        No formato personalizado, 'id' é inferido do nome do diretório.
        """
        p = self.root / slug / "company.yaml"
        if not p.exists():
            return None
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None

            # Formato personalizado: chave raiz é "company"
            if "company" in raw and isinstance(raw["company"], dict) and "id" not in raw:
                inner = raw["company"]
                # Constrói contexto rico a partir dos campos disponíveis
                ctx_parts: list[str] = []
                if inner.get("description"):
                    ctx_parts.append(inner["description"])
                if inner.get("vision"):
                    ctx_parts.append(f"Visão: {inner['vision']}")
                if inner.get("mission"):
                    ctx_parts.append(f"Missão: {inner['mission']}")
                if inner.get("values") and isinstance(inner["values"], list):
                    ctx_parts.append("Valores:\n" + "\n".join(f"  - {v}" for v in inner["values"]))
                if inner.get("sector"):
                    ctx_parts.append(f"Setor: {inner['sector']}")
                normalized: dict[str, Any] = {
                    "id": slug,
                    "name": inner.get("name", slug),
                    "language": inner.get("language", "pt"),
                    "context": "\n\n".join(ctx_parts),
                    "departments": [
                        d["name"] if isinstance(d, dict) else str(d)
                        for d in inner.get("departments", [])
                    ],
                }
                return CompanyDef.from_dict(normalized)

            # Formato padrão — injeta id do nome do diretório se ausente
            if "id" not in raw:
                raw["id"] = slug
            return CompanyDef.from_dict(raw)
        except (yaml.YAMLError, KeyError):
            return None

    def list_companies(self) -> list[CompanyDef]:
        """Lista todas as empresas em companies/."""
        if not self.root.exists():
            return []
        companies = []
        for d in sorted(self.root.iterdir()):
            if d.is_dir() and (d / "company.yaml").exists():
                c = self.get(d.name)
                if c:
                    companies.append(c)
        return companies

    def update(self, company: CompanyDef) -> None:
        """Salva company.yaml de uma empresa existente."""
        base = self.root / company.id
        if not base.exists():
            raise CompanyManagerError(f"Empresa '{company.id}' não encontrada.")
        p = base / "company.yaml"
        # Preserva o template comentado — apenas reescreve os campos de dados
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
        raw.update(company.to_dict())
        p.write_text(
            yaml.dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )

    def delete(self, slug: str) -> bool:
        """Remove uma empresa (pasta inteira). Use com cuidado."""
        import shutil
        base = self.root / slug
        if not base.exists():
            return False
        shutil.rmtree(base)
        return True

    # --- Empresa ativa -------------------------------------------------------

    def set_active(self, slug: str) -> None:
        """Define a empresa ativa para a sessão atual."""
        if not (self.root / slug / "company.yaml").exists():
            raise CompanyManagerError(
                f"Empresa '{slug}' não encontrada. Crie com: bauer company create {slug}"
            )
        _ACTIVE_FILE.write_text(slug, encoding="utf-8")

    def clear_active(self) -> None:
        """Remove seleção de empresa ativa."""
        if _ACTIVE_FILE.exists():
            _ACTIVE_FILE.unlink()

    def get_active_id(self) -> str | None:
        """Retorna o ID da empresa ativa, ou None."""
        if not _ACTIVE_FILE.exists():
            return None
        slug = _ACTIVE_FILE.read_text(encoding="utf-8").strip()
        return slug if slug else None

    def get_active(self) -> CompanyDef | None:
        """Retorna a CompanyDef da empresa ativa, ou None."""
        slug = self.get_active_id()
        if not slug:
            return None
        return self.get(slug)

    # --- Injeção de contexto -------------------------------------------------

    def build_system_prompt_prefix(self, company: CompanyDef) -> str:
        """Constrói o bloco de contexto a ser pré-fixado no system prompt do agent."""
        parts = [f"# CONTEXTO DA EMPRESA: {company.name}"]
        if company.context.strip():
            parts.append(company.context.strip())
        if company.language and company.language != "pt":
            parts.append(f"Idioma de resposta: {company.language}.")
        parts.append("")  # linha em branco de separação
        return "\n".join(parts)

    def inject_context(self, system_prompt: str, company: CompanyDef) -> str:
        """Injeta contexto da empresa no início do system prompt do agent."""
        prefix = self.build_system_prompt_prefix(company)
        return prefix + system_prompt

    # --- Registry de agents da empresa --------------------------------------

    def get_agent_registry(self, slug: str):
        """Retorna um AgentRegistry apontando para o agents.yaml desta empresa."""
        from .agent_registry import AgentRegistry
        return AgentRegistry(path=self.root / slug / "agents.yaml")

    def get_active_registry(self):
        """Retorna AgentRegistry da empresa ativa, ou None."""
        slug = self.get_active_id()
        if not slug:
            return None
        return self.get_agent_registry(slug)
