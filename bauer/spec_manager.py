"""Spec-Driven Development — gerenciador de specs YAML.

Cada spec define o CONTRATO de um feature antes de qualquer linha de codigo:
  purpose         o que faz e por que existe
  behavior        regras e invariantes que a implementacao DEVE respeitar
  interface       inputs/outputs tipados
  acceptance_criteria  criterios Given/When/Then verificaveis
  linked_files    arquivos de implementacao e testes associados

Fluxo esperado:
  1. bauer spec new <id>       — escreve o spec (modo entrevista)
  2. Escreve testes alinhados ao spec
  3. Implementa ate os testes passarem
  4. bauer spec show <id>      — exibe contrato como referencia
  5. bauer agent               — le specs automaticamente como contexto
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

SPECS_DIR = Path("specs")

_VALID_STATUSES = {"draft", "review", "approved", "implemented", "deprecated"}


@dataclass
class Spec:
    id: str
    title: str
    version: str = "1.0.0"
    status: str = "draft"     # draft | review | approved | implemented | deprecated
    created: str = field(default_factory=lambda: date.today().isoformat())
    purpose: str = ""
    behavior: list[str] = field(default_factory=list)
    interface: dict[str, Any] = field(default_factory=dict)
    acceptance_criteria: list[str] = field(default_factory=list)
    linked_files: list[str] = field(default_factory=list)
    notes: str = ""

    # ------------------------------------------------------------------
    # serialização
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "version": self.version,
            "status": self.status,
            "created": self.created,
            "purpose": self.purpose,
            "behavior": self.behavior,
            "interface": self.interface,
            "acceptance_criteria": self.acceptance_criteria,
        }
        if self.linked_files:
            d["linked_files"] = self.linked_files
        if self.notes:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Spec":
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            version=str(d.get("version", "1.0.0")),
            status=d.get("status", "draft"),
            created=str(d.get("created", date.today().isoformat())),
            purpose=d.get("purpose", ""),
            behavior=d.get("behavior", []),
            interface=d.get("interface", {}),
            acceptance_criteria=d.get("acceptance_criteria", []),
            linked_files=d.get("linked_files", []),
            notes=d.get("notes", ""),
        )

    # ------------------------------------------------------------------
    # formatação para LLM
    # ------------------------------------------------------------------

    def to_context(self, compact: bool = False) -> str:
        """Formata o spec como texto para injeção em prompts.

        Args:
            compact: Se True, emite apenas id, title, purpose e ACs (1 linha cada).
        """
        lines: list[str] = []

        if compact:
            lines.append(f"[spec:{self.id}] {self.title} — {self.purpose.split(chr(10))[0]}")
            for ac in self.acceptance_criteria:
                lines.append(f"  AC: {ac}")
            return "\n".join(lines)

        lines.append(f"## Spec: {self.id} — {self.title} (v{self.version}, {self.status})")
        if self.purpose:
            lines.append(f"\n**Purpose:** {self.purpose.strip()}")
        if self.behavior:
            lines.append("\n**Behavior (regras que a implementacao DEVE respeitar):**")
            for rule in self.behavior:
                lines.append(f"  - {rule}")
        if self.interface:
            inputs = self.interface.get("inputs", [])
            outputs = self.interface.get("outputs", [])
            if inputs:
                lines.append("\n**Inputs:**")
                for inp in inputs:
                    if isinstance(inp, dict):
                        req = " (required)" if inp.get("required") else ""
                        lines.append(
                            f"  - {inp.get('name', '')}: {inp.get('type', 'any')}{req}"
                            f" — {inp.get('description', '')}"
                        )
                    else:
                        lines.append(f"  - {inp}")
            if outputs:
                lines.append("\n**Outputs:**")
                for out in outputs:
                    if isinstance(out, dict):
                        lines.append(
                            f"  - {out.get('name', '')}: {out.get('type', 'any')}"
                            f" — {out.get('description', '')}"
                        )
                    else:
                        lines.append(f"  - {out}")
        if self.acceptance_criteria:
            lines.append("\n**Acceptance Criteria:**")
            for ac in self.acceptance_criteria:
                lines.append(f"  ✓ {ac}")
        if self.notes:
            lines.append(f"\n**Notes:** {self.notes.strip()}")

        return "\n".join(lines)

    @staticmethod
    def valid_id(spec_id: str) -> bool:
        return bool(re.match(r"^[a-z0-9][a-z0-9_-]{1,50}$", spec_id))


class SpecManagerError(Exception):
    pass


class SpecManager:
    """Lê e salva specs YAML em `specs/<id>.yaml`."""

    def __init__(self, specs_dir: str | Path = SPECS_DIR):
        self.specs_dir = Path(specs_dir)

    def _path(self, spec_id: str) -> Path:
        return self.specs_dir / f"{spec_id}.yaml"

    def list_specs(self) -> list[Spec]:
        """Retorna todos os specs encontrados em specs_dir."""
        if not self.specs_dir.exists():
            return []
        specs = []
        for f in sorted(self.specs_dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(f.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    specs.append(Spec.from_dict(raw))
            except Exception:
                pass
        return specs

    def get(self, spec_id: str) -> Spec | None:
        p = self._path(spec_id)
        if not p.exists():
            return None
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
            return Spec.from_dict(raw) if isinstance(raw, dict) else None
        except Exception:
            return None

    def save(self, spec: Spec) -> Path:
        """Salva spec em YAML. Cria specs_dir se necessário."""
        self.specs_dir.mkdir(parents=True, exist_ok=True)
        p = self._path(spec.id)
        p.write_text(
            yaml.dump(
                spec.to_dict(),
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
                width=88,
            ),
            encoding="utf-8",
        )
        return p

    def delete(self, spec_id: str) -> bool:
        p = self._path(spec_id)
        if p.exists():
            p.unlink()
            return True
        return False

    def find_relevant(self, query: str, max_results: int = 5) -> list[Spec]:
        """Retorna specs cujo id/title/purpose contém termos do query (simples keyword match).

        Prioriza specs com status != 'deprecated'.
        """
        keywords = set(query.lower().split())
        results: list[tuple[int, Spec]] = []
        for spec in self.list_specs():
            if spec.status == "deprecated":
                continue
            behavior_text = " ".join(
                b if isinstance(b, str) else str(b.get("description", "") + " " + str(b.get("name", "")))
                for b in spec.behavior
            )
            haystack = (
                f"{spec.id} {spec.title} {spec.purpose} {behavior_text}"
            ).lower()
            score = sum(1 for kw in keywords if kw in haystack)
            if score > 0:
                results.append((score, spec))
        results.sort(key=lambda t: -t[0])
        return [s for _, s in results[:max_results]]

    def specs_context(self, query: str = "", compact: bool = True) -> str:
        """Retorna texto pronto para injeção em prompts.

        Se query vazio, inclui todos os specs aprovados/implementados.
        Se query fornecido, faz keyword match primeiro.
        """
        if query:
            specs = self.find_relevant(query)
        else:
            specs = [
                s for s in self.list_specs()
                if s.status in ("approved", "implemented")
            ]
        if not specs:
            return ""
        parts = ["# Specs do Projeto (contratos a respeitar)\n"]
        for spec in specs:
            parts.append(spec.to_context(compact=compact))
            parts.append("")
        return "\n".join(parts)
