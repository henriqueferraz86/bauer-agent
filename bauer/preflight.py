"""Preflight do Bauer (bauer doctor).

Premissa central (premortem item 9): erro precisa ter causa, valor configurado,
valor detectado e ação sugerida. Esta camada coleta tudo isso e produz um
RuntimeState completo + uma lista de "notas" legíveis.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config_loader import BauerConfig
from .machine_id import machine_summary
from .model_registry import ModelInfo, ModelRegistry, contexto_seguro
from .ollama_client import OllamaClient, OllamaError
from .runtime_state import ContextState, RuntimeState


@dataclass
class DoctorReport:
    state: RuntimeState
    findings: list[str]   # mensagens legíveis (mesmas das notes em state)


def _detect_env_num_ctx() -> int | None:
    """Lê OLLAMA_CONTEXT_LENGTH do ambiente do processo atual.

    Nota: idealmente leríamos do processo do servidor Ollama via /proc/<pid>/environ
    no Linux. Como o doctor pode rodar em qualquer máquina, ficamos com o ambiente
    visível ao Bauer. É uma aproximação útil — não a verdade absoluta.
    """
    v = os.environ.get("OLLAMA_CONTEXT_LENGTH")
    if v and v.isdigit():
        return int(v)
    return None


def _resolve_context(
    requested: int,
    minimum: int,
    auto_downgrade: bool,
    modelfile_num_ctx: int | None,
    env_num_ctx: int | None,
    info: ModelInfo | None,
    ram_available_mb: int,
    safety_margin_mb: int,
    on_gpu: bool = False,
) -> tuple[int, str, list[str]]:
    """Aplica a regra: contexto_final = min(requested, modelfile, env, ram_seguro).

    ``on_gpu`` desliga o teto de RAM: a fórmula do `contexto_seguro` assume
    inferência na CPU (pesos + KV cache saindo da RAM do sistema). Com o modelo
    residente na GPU quem paga a conta é a VRAM, e medir a RAM livre do host
    passa a reprovar máquinas que estão rodando o modelo perfeitamente bem —
    no Beelink (iGPU Arc com 46 GB de VRAM) isso dava `applied=0` e
    `status=blocked` com o modelo carregado 100% em GPU.

    Retorna (applied, reason, notes).
    """
    notes: list[str] = []
    candidatos: dict[str, int] = {"requested": requested}

    if modelfile_num_ctx:
        candidatos["modelfile_num_ctx"] = modelfile_num_ctx
    if env_num_ctx:
        candidatos["env_OLLAMA_CONTEXT_LENGTH"] = env_num_ctx
    if info is not None:
        if on_gpu:
            candidatos["gpu_max_context"] = info.max_context_safe
            notes.append(
                "Modelo residente na GPU — teto de RAM do sistema não se aplica "
                f"(mantido o max_context_safe de {info.max_context_safe} tokens)."
            )
        else:
            seguro = contexto_seguro(info, ram_available_mb, safety_margin_mb)
            candidatos["ram_safe"] = seguro
            if seguro == 0:
                notes.append(
                    f"Modelo não cabe na RAM disponível ({ram_available_mb} MB). "
                    f"Reduza requested_context, use modelo menor ou aumente RAM."
                )

    applied = min(candidatos.values())
    # quem venceu?
    vencedor = next(k for k, v in candidatos.items() if v == applied)
    reason = f"limited_by={vencedor}"

    if applied < minimum:
        if auto_downgrade:
            notes.append(
                f"Contexto aplicado ({applied}) ficou abaixo de minimum_context "
                f"({minimum}). Auto-downgrade ativo — Bauer segue com {applied}."
            )
        else:
            notes.append(
                f"Contexto aplicado ({applied}) abaixo de minimum_context ({minimum}) "
                f"e auto_downgrade_context=false. Bauer não deve iniciar nesse estado."
            )

    if requested != applied:
        notes.append(
            f"Contexto ajustado: requested={requested} → applied={applied} ({reason})."
        )

    return applied, reason, notes


# Contextos padrão por provider — FONTE ÚNICA em provider_profile.py.
# Estes aliases existem para compatibilidade com importadores antigos (cli.py,
# testes); novos código deve usar provider_profile.get_default_context().
from .provider_profile import (  # noqa: E402
    _DEFAULT_CONTEXT_FALLBACK as _CLOUD_CONTEXT_FALLBACK,
    default_context_map as _default_context_map,
)

_CLOUD_CONTEXT_DEFAULTS: dict[str, int] = _default_context_map()


def run_doctor(
    config: BauerConfig,
    registry: ModelRegistry,
    state_file: str | Path = ".runtime_state.json",
) -> DoctorReport:
    """Executa todas as checagens da Fase 1 e produz RuntimeState + relatório."""
    findings: list[str] = []

    # Qualquer provider que não seja "ollama" é tratado como remoto/cloud:
    # Ollama não é verificado, modelo é assumido disponível.
    is_cloud = config.model.provider != "ollama"

    # --- máquina ----------------------------------------------------------------
    machine = machine_summary()
    ram_available = int(machine["ram_available_mb"])
    ram_total = int(machine["ram_total_mb"])
    mid = str(machine["machine_id"])
    findings.append(
        f"Máquina: id={mid} | RAM disponível={ram_available} MB / total={ram_total} MB"
    )

    # --- ollama (apenas para provider local) -----------------------------------
    alive = False
    client = None
    if not is_cloud:
        client = OllamaClient(config.ollama.host, config.ollama.timeout_seconds)
        alive, motivo = client.is_alive()
        if alive:
            findings.append(f"Ollama: ATIVO em {config.ollama.host}")
        else:
            findings.append(f"Ollama: OFFLINE — {motivo}")
    else:
        alive = True  # provider remoto: assume conectividade OK
        findings.append(f"Provider: {config.model.provider} (Ollama não necessário)")

    # --- modelo ------------------------------------------------------------------
    model_name = config.model.name
    info = registry.get(model_name)
    if info is None and not is_cloud:
        # Tenta auto-detectar via Ollama API quando modelo não está no models.yaml
        if alive and client:
            from .model_registry import auto_detect_from_ollama
            info = auto_detect_from_ollama(client, model_name)
            if info:
                findings.append(
                    f"Modelo '{model_name}' auto-detectado: "
                    f"contexto nativo={info.max_context_safe} tokens "
                    f"(ram_base≈{info.ram_base_mb} MB)."
                )
            else:
                findings.append(
                    f"Modelo '{model_name}' não está no models.yaml e não foi possível "
                    f"auto-detectar. Adicione um entry com ram_base_mb e ram_per_1k_ctx_mb."
                )
        else:
            findings.append(
                f"Modelo '{model_name}' não está no models.yaml. "
                f"Adicione um entry com ram_base_mb e ram_per_1k_ctx_mb."
            )

    model_available = False
    modelfile_num_ctx: int | None = None
    on_gpu = False
    if is_cloud:
        # Para providers cloud, assume modelo disponível
        model_available = True
        findings.append(f"Modelo '{model_name}' configurado para provider cloud.")
    elif alive and client:
        if client.has_model(model_name):
            model_available = True
            findings.append(f"Modelo '{model_name}' está disponível no Ollama.")
            on_gpu = client.loaded_on_gpu(model_name)
            if on_gpu:
                findings.append(
                    f"Modelo '{model_name}' residente em VRAM — o teto de contexto "
                    f"por RAM do sistema não se aplica."
                )
            try:
                params = client.show_model(model_name)
                modelfile_num_ctx = params.num_ctx
                if modelfile_num_ctx:
                    findings.append(
                        f"Modelfile do '{model_name}': num_ctx={modelfile_num_ctx}"
                    )
            except OllamaError as exc:
                findings.append(f"Aviso ao consultar Modelfile: {exc}")
        else:
            findings.append(
                f"Modelo '{model_name}' NÃO encontrado no Ollama. "
                f"Rode: `ollama pull {model_name}`"
            )

    # --- contexto ----------------------------------------------------------------
    env_num_ctx = _detect_env_num_ctx() if not is_cloud else None
    if env_num_ctx:
        findings.append(f"OLLAMA_CONTEXT_LENGTH no ambiente: {env_num_ctx}")

    # Para providers cloud, requested_context costuma ser baixo porque foi definido
    # sob restrição de RAM do Ollama. Usamos o máximo entre o valor configurado e o
    # padrão do provider — o usuário pode sempre configurar explicitamente um valor
    # maior para override.
    if is_cloud:
        cloud_default = _CLOUD_CONTEXT_DEFAULTS.get(
            config.model.provider, _CLOUD_CONTEXT_FALLBACK
        )
        effective_requested = max(config.model.requested_context, cloud_default)
        if effective_requested != config.model.requested_context:
            findings.append(
                f"requested_context={config.model.requested_context} < padrão cloud "
                f"para {config.model.provider} ({cloud_default}) — "
                f"contexto ajustado para {effective_requested}."
            )
    else:
        effective_requested = config.model.requested_context

    applied, reason, ctx_notes = _resolve_context(
        requested=effective_requested,
        minimum=config.model.minimum_context,
        auto_downgrade=config.model.auto_downgrade_context,
        modelfile_num_ctx=modelfile_num_ctx,
        env_num_ctx=env_num_ctx,
        info=info,
        ram_available_mb=ram_available,
        safety_margin_mb=config.runtime.safety_margin_mb,
        on_gpu=on_gpu,
    )
    findings.extend(ctx_notes)

    # --- tool mode ---------------------------------------------------------------
    # Esta linha precisa bater com o gate real do agent
    # (`_client_supports_native_tools`), senão o doctor vira decoração: durante
    # meses ele imprimiu "native" para modelos Ollama enquanto o runtime rodava
    # sempre no bridge, e "bridge" para providers cloud que rodavam native.
    if is_cloud:
        # Todo provider OpenAI-compat aceita `tools=` (OpenAIClient.
        # supports_native_tools é True incondicionalmente).
        tool_mode = "native"
        findings.append("Tool mode: native (provider OpenAI-compat)")
    elif info is not None and info.supports_tools is True:
        tool_mode = "native"
        findings.append("Tool mode: native (modelo anuncia capability 'tools')")
    else:
        tool_mode = "bridge"
        findings.append(
            "Tool mode: bridge — o modelo não declara suporte a tools nativas. "
            "Modelos menores seguem mal o bridge por prompt em tarefas "
            "multi-passo; prefira um modelo com capability 'tools'."
        )

    # --- segurança do serve -------------------------------------------------------
    _serve_host = config.serve.host
    _serve_key = config.serve.api_key or ""
    if _serve_host not in ("127.0.0.1", "localhost") and not _serve_key:
        findings.append(
            "[AVISO DE SEGURANÇA] serve.host está exposto na rede "
            f"({_serve_host}) mas serve.api_key está vazio — qualquer host na rede "
            "pode acessar a API sem autenticação. "
            "Configure serve.api_key ou altere serve.host para 127.0.0.1."
        )

    # --- status final ------------------------------------------------------------
    if is_cloud:
        # Cloud sempre pode rodar (desde que contexto > 0)
        status = "ok" if applied > 0 else "blocked"
    else:
        blocked = (
            not alive
            or not model_available
            or applied <= 0
            or (applied < config.model.minimum_context and not config.model.auto_downgrade_context)
        )
        if blocked:
            status = "blocked"
        elif applied != effective_requested or ctx_notes:
            status = "ok_with_adjustments"
        else:
            status = "ok"

    # --- montar e salvar state ---------------------------------------------------
    state = RuntimeState(
        configured_model=model_name,
        configured_provider=config.model.provider,
        active_model=model_name if model_available else None,
        model_available=model_available,
        ollama_alive=alive,
        ollama_host=config.ollama.host if not is_cloud else "",
        context=ContextState(
            requested=effective_requested,
            modelfile_num_ctx=modelfile_num_ctx,
            env_OLLAMA_CONTEXT_LENGTH=env_num_ctx,
            applied=applied,
            empirical_probe=None,  # Camada B (--deep), fora do escopo da Fase 1
            reason=reason,
        ),
        tool_mode=tool_mode,
        profile=config.runtime.profile,
        ram_available_mb=ram_available,
        ram_total_mb=ram_total,
        machine_id=mid,
        status=status,
        notes=list(findings),
    )

    return DoctorReport(state=state, findings=findings)
