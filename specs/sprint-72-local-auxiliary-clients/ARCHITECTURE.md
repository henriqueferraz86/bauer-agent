# ARCHITECTURE — Sprint 72

Não há camada nova. A correção reaproveita a marca de processo
`BAUER_LOCAL_ONLY` e `local_only_active` (sprint 69), além de
`provider_e_local` e `validar_execucao_local` (sprint 62).

## Camada 1 — boot

```text
bauer agent --local | bauer serve --local | /model --local (rebuild)
  └─ validar_execucao_local(cfg)                         (model_router.py)
       ├─ profiles_local / model.name / fallback_models_local   (antes)
       └─ problemas_auxiliares_locais(cfg)                (novo)
            ├─ para cada slot em AuxiliarySection.model_fields:
            │    provider explícito e não provider_e_local → problema
            │    provider vazio → herda model.provider (já verificado)
            ├─ STT_PROVIDER ∉ STT_LOCAIS → problema
            └─ BAUER_TTS_PROVIDER | TTS_PROVIDER ∉ TTS_LOCAIS → problema
     problemas → exit 1 antes de mark_local_only()
```

A lista de slots vem do schema Pydantic, e não de uma lista à mão. Um slot
novo é verificado no boot sem edição aqui.

## Camada 2 — criação (marca ligada)

```text
get_text_auxiliary_client(slot, cfg)                   (auxiliary_client.py)
  ├─ _resolve_slot → (provider, model)
  ├─ _local_only_refusal(provider, cfg) → WARNING, (None, None)   ← novo
  └─ _build_client_for_provider(provider, model, cfg)
       ├─ _local_only_refusal → raise AuxiliaryLocalOnlyRefused   ← novo
       ├─ token do bauer auth
       └─ construtores

server.py (troca de modelo, restauração, OAuth)
  ├─ _provider_allowed (sprint 62/69)
  └─ _build_client_for_provider → recusa (defesa em profundidade)

transcription.transcribe_audio
  attempts (default/auto/explícito) → filtra "local" → vazio: erro "--local"
tts.synthesize_speech
  attempts → filtra {"local","kokoro"} → vazio: erro "--local"
tools/media: _image_generate, _text_to_speech
  _refuse_cloud_media_under_local → ToolError antes de client/request
```

Consumidores sem mudança, porque todos passam por `get_text_auxiliary_client`
(direto ou via `call_aux_text`/`get_compression_client`): `llm_approval`,
`context_manager`, `memory_consolidation`, `background_review` (thread),
`_runtime._build_router` (visão), `os_intent`, `kanban_decompose` e
`kanban_specify`.

## Arquivos

- `bauer/model_router.py`: `problemas_auxiliares_locais`, `STT_LOCAIS`,
  `TTS_LOCAIS` e a chamada em `validar_execucao_local`.
- `bauer/auxiliary_client.py`: `AuxiliaryLocalOnlyRefused`,
  `_local_only_refusal` e as duas guardas.
- `bauer/transcription.py`: `_local_only`, `_local_only_message`, o filtro
  em `transcribe_audio` e a restrição em `available_stt_provider` e
  `stt_unavailable_message`.
- `bauer/tts.py`: `_local_only`, o filtro em `synthesize_speech` e a
  restrição em `available_tts_provider`.
- `bauer/tools/media.py`: `_refuse_cloud_media_under_local`.
- `tests/conftest.py`: isola `STT_PROVIDER`, `TTS_PROVIDER` e
  `BAUER_TTS_PROVIDER`.
- `tests/test_sprint72_local_auxiliary_clients.py`: regressões e catracas.
