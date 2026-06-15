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
        # Tenta localizar models.yaml no diretório de instalação do pacote
        _pkg_models = Path(__file__).parent.parent / "models.yaml"
        if _pkg_models.exists():
            p = _pkg_models
        else:
            # Sem models.yaml — registry vazio; preflight auto-detecta via Ollama API
            return ModelRegistry(models={})
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


def auto_detect_from_ollama(client, model_name: str) -> ModelInfo | None:
    """Detecta ModelInfo automaticamente via Ollama API.

    Não bloqueia por RAM (ram_base_mb=0, ram_per_1k_ctx_mb=0) — usa max_context_safe
    como teto. O valor de max_context_safe vem do context_length nativo da arquitetura
    reportado pelo /api/show (ex: gemma3.context_length = 131072).
    """
    try:
        params = client.show_model(model_name)
    except Exception:
        return None

    # Contexto máximo nativo do modelo (da arquitetura GGUF)
    native_ctx = params.context_length or params.num_ctx
    if not native_ctx or native_ctx <= 0:
        native_ctx = 32768  # fallback conservador

    # Tamanho em bytes: tenta show_model first, fallback para list_models_with_sizes
    size_bytes = params.size_bytes or 0
    if not size_bytes:
        for entry in client.list_models_with_sizes():
            if entry["name"] == model_name:
                size_bytes = entry["size_bytes"]
                break

    # ram_base_mb estimado do tamanho do arquivo (modelo carregado ≈ tamanho * 1.1)
    # Usado apenas como informação — ram_per_1k_ctx_mb=0 desativa o bloqueio por RAM.
    ram_base = int(size_bytes / 1024 / 1024 * 1.1) if size_bytes else 0

    return ModelInfo(
        provider="ollama",
        ram_base_mb=ram_base,
        ram_per_1k_ctx_mb=0,      # desativa bloqueio por KV cache — usa max_context_safe
        max_context_safe=native_ctx,
        supports_tools=False,
        ram_profile="medium",
    )


def contexto_seguro(
    info: ModelInfo,
    ram_disponivel_mb: int,
    folga_mb: int = 1024,
) -> int:
    """Calcula contexto máximo seguro para RAM disponível (Decisão 3).

    Retorna 0 se o modelo nem cabe vazio nesta máquina.
    Quando ram_per_1k_ctx_mb=0 (auto-detectado), retorna max_context_safe diretamente
    sem checar RAM — o modelo já está rodando ou o usuário optou por não limitar.
    """
    if info.ram_per_1k_ctx_mb <= 0:
        return info.max_context_safe
    ram_para_contexto = ram_disponivel_mb - info.ram_base_mb - folga_mb
    if ram_para_contexto <= 0:
        return 0
    tokens_seguros = (ram_para_contexto / info.ram_per_1k_ctx_mb) * 1024
    # Arredondar para múltiplo de 256 (mais previsível).
    tokens_seguros = int(tokens_seguros // 256) * 256
    return max(0, min(tokens_seguros, info.max_context_safe))
