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


#: models.yaml curado que viaja DENTRO do pacote. Antes ele morava na raiz do
#: repo e o `package-data` do pyproject só cobre `bauer/**`, então nenhuma
#: instalação pip/pipx recebia o arquivo: `load_registry()` devolvia registry
#: vazio e todo o guardrail de RAM (ram_base_mb/max_context_safe) virava letra
#: morta fora de um checkout git.
_PACKAGED_MODELS = Path(__file__).parent / "data" / "models.yaml"

#: Local antigo (raiz do repo). Mantido só para não quebrar quem tem um
#: models.yaml lá de um checkout anterior.
_LEGACY_MODELS = Path(__file__).parent.parent / "models.yaml"


def _read_models_file(p: Path) -> dict[str, ModelInfo]:
    """Lê UM models.yaml e devolve o dict de modelos validado."""
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ModelRegistryError(f"YAML inválido em {p}: {exc}") from exc
    if not isinstance(raw, dict) or "models" not in raw:
        raise ModelRegistryError(
            f"{p} precisa ter uma chave 'models:' no topo."
        )
    try:
        return ModelRegistry(**raw).models
    except ValidationError as exc:
        problems = "\n".join(
            f"  - {'/'.join(str(x) for x in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        )
        raise ModelRegistryError(f"{p} inválido:\n{problems}") from exc


def registry_sources(path: str | Path = "models.yaml") -> list[Path]:
    """Arquivos que compõem o registry, do mais fraco ao mais forte.

    Ordem de precedência (o último vence por modelo):
      1. `bauer/data/models.yaml` — curado, viaja no pacote
      2. `$BAUER_HOME/models.yaml` — curadoria do usuário
      3. o `path` explícito, quando existe (flag --models / cwd)

    A camada 2 é a que faltava: `load_config` já caía para `$BAUER_HOME`, o
    registry não — então o models.yaml que o usuário escrevia em ~/.bauer/
    nunca era lido por nada além do `bauer run`.
    """
    sources: list[Path] = []
    if _PACKAGED_MODELS.exists():
        sources.append(_PACKAGED_MODELS)
    elif _LEGACY_MODELS.exists():
        sources.append(_LEGACY_MODELS)

    try:
        from .paths import get_bauer_home

        home_models = get_bauer_home() / "models.yaml"
        if home_models.exists() and home_models not in sources:
            sources.append(home_models)
    except Exception:
        pass

    p = Path(path)
    if p.exists() and p.resolve() not in [s.resolve() for s in sources]:
        sources.append(p)
    return sources


def load_registry(path: str | Path = "models.yaml") -> ModelRegistry:
    """Carrega o registry mesclando as camadas de `registry_sources()`.

    Merge por NOME de modelo (não substituição do arquivo inteiro): um
    ~/.bauer/models.yaml com uma única entrada afina esse modelo e mantém os
    demais curados, em vez de apagar o registry.
    """
    merged: dict[str, ModelInfo] = {}
    for src in registry_sources(path):
        merged.update(_read_models_file(src))
    return ModelRegistry(models=merged)


def auto_detect_from_ollama(client, model_name: str) -> ModelInfo | None:
    """Detecta ModelInfo automaticamente via Ollama API.

    Não bloqueia por RAM (ram_base_mb=0, ram_per_1k_ctx_mb=0) — usa max_context_safe
    como teto. O valor de max_context_safe vem do context_length nativo da arquitetura
    reportado pelo /api/show (ex: gemma3.context_length = 131072).

    `supports_tools` sai do campo `capabilities` do /api/show (o Ollama devolve
    `["completion", "tools"]` para quem suporta function calling). Antes era
    chumbado em False, então qualquer modelo fora do models.yaml era rotulado
    "sem tools" mesmo suportando.
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

    caps = {str(c).lower() for c in (params.capabilities or [])}

    return ModelInfo(
        provider="ollama",
        ram_base_mb=ram_base,
        ram_per_1k_ctx_mb=0,      # desativa bloqueio por KV cache — usa max_context_safe
        max_context_safe=native_ctx,
        supports_tools="tools" in caps,
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
