"""Registry de modelos conhecidos (models.yaml).

Decisão 3: cada modelo carrega ram_base_mb e ram_per_1k_ctx_mb,
permitindo calcular o contexto seguro sem chutar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError


class ModelRegistryError(Exception):
    pass


class ModelInfo(BaseModel):
    provider: Literal["ollama", "openai", "external"] = "ollama"
    ram_base_mb: int = Field(ge=0)
    ram_per_1k_ctx_mb: float = Field(ge=0.0)
    max_context_safe: int = Field(ge=512)
    supports_tools: bool | Literal["partial"] = False
    ram_profile: Literal["low", "medium", "high", "external"] = "medium"
    recommended_for: list[str] = []


class ModelRegistry(BaseModel):
    models: dict[str, ModelInfo]

    def get(self, name: str) -> ModelInfo | None:
        return self.models.get(name)

    def names(self) -> list[str]:
        return sorted(self.models.keys())


def load_registry(path: str | Path = "models.yaml") -> ModelRegistry:
    p = Path(path)
    if not p.exists():
        raise ModelRegistryError(
            f"Arquivo models.yaml não encontrado: {p}\n"
            f"Crie um models.yaml na raiz do projeto."
        )
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ModelRegistryError(f"YAML inválido em {p}: {exc}") from exc
    if not isinstance(raw, dict) or "models" not in raw:
        raise ModelRegistryError(
            f"{p} precisa ter uma chave 'models:' no topo."
        )
    try:
        return ModelRegistry(**raw)
    except ValidationError as exc:
        problems = "\n".join(
            f"  - {'/'.join(str(x) for x in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        )
        raise ModelRegistryError(f"models.yaml inválido:\n{problems}") from exc


def contexto_seguro(
    info: ModelInfo,
    ram_disponivel_mb: int,
    folga_mb: int = 1024,
) -> int:
    """Calcula contexto máximo seguro para RAM disponível (Decisão 3).

    Retorna 0 se o modelo nem cabe vazio nesta máquina.
    """
    ram_para_contexto = ram_disponivel_mb - info.ram_base_mb - folga_mb
    if ram_para_contexto <= 0:
        return 0
    if info.ram_per_1k_ctx_mb <= 0:
        return info.max_context_safe
    tokens_seguros = (ram_para_contexto / info.ram_per_1k_ctx_mb) * 1024
    # Arredondar para múltiplo de 256 (mais previsível).
    tokens_seguros = int(tokens_seguros // 256) * 256
    return max(0, min(tokens_seguros, info.max_context_safe))
