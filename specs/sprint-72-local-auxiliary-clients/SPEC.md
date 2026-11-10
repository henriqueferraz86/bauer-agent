# SPEC — Sprint 72: `--local` nos clients auxiliares (plano 066)

Plano: [`plans/066-pendencias-pos-064-065.md`](../../plans/066-pendencias-pos-064-065.md),
sprint 72. Fecha a pendência registrada na sprint 69: numa sessão `--local`,
os clients auxiliares não eram verificados.
Branch `codex/sprint-72-local-auxiliary-clients`, base `origin/master`
`3677d0d`.

## Inventário

Clients de modelo construídos fora do turno principal, encontrados no código
(busca por `get_text_auxiliary_client`, `call_aux_text`,
`_build_client_for_provider`, construtores `OpenAIClient`/`AnthropicClient`/
`ChatGPTBackendClient`/`openai.OpenAI` e `httpx.post` para APIs de modelo):

| Slot / caminho | Consumidor | Roda onde |
|---|---|---|
| `auxiliary.approval_model` | `llm_approval.llm_evaluate_tool` (G4) | turno |
| `auxiliary.compression_model` | `context_manager._compression_client` (sem cfg: autoload de `BAUER_CONFIG`/`config.yaml` do CWD) | turno |
| `auxiliary.compression_model` | `memory_consolidation.default_llm` (sprints 66/67) | agendador/CLI |
| `auxiliary.background_reviewer` | `background_review._run_review` | thread daemon |
| `auxiliary.vision_model` | `_runtime._build_router` → `vision_analyze`/`video_analyze`/`browser_vision` | turno |
| `auxiliary.intent_router` | `os_intent.route_intent` | Command Palette |
| `auxiliary.kanban_decomposer` | `kanban_decompose.decompose_task` | kanban |
| `auxiliary.triage_specifier` | `kanban_specify.specify_task` | kanban |
| `_build_client_for_provider` direto | `server.py`: troca de modelo, restauração do modelo salvo, OAuth | serve |
| Transcrição (STT) | `transcription.transcribe_audio` (`/transcribe`, tool `transcribe_audio`, voz) | turno/serve |
| Síntese de voz (TTS) | `tts.synthesize_speech` (`/listen`, autonomia contínua) | turno/thread |
| `image_generate`, `text_to_speech` (tools) | `tools/media.py`: OpenAI/xAI/OpenRouter | turno |

Não há geração de título ou resumo de sessão por LLM fora da compressão de
contexto.

## Reprodução no código atual (base `3677d0d`)

`tests/test_sprint72_local_auxiliary_clients.py`, rodado antes da correção:
44 casos falham e 27 passam. Os que passam são controles positivos (slot
local, slot vazio caindo no principal, modo default inalterado) e as três
catracas de inventário. Todos os casos "um por slot" falham:

- com `BAUER_LOCAL_ONLY=1` e o slot apontando para `openai`, cada consumidor
  real constrói um `OpenAIClient` e, quando chega a chamar, faz o request;
- `validar_execucao_local` aceita qualquer slot `auxiliary.*` de nuvem;
- a transcrição usa o default OpenRouter e a cadeia `auto` de nuvem;
- o TTS usa OpenAI/Google;
- `image_generate` e `text_to_speech` chegam a `httpx.post`/`openai.OpenAI`;
- `_build_client_for_provider` constrói client de nuvem (inclusive a partir
  do token do `bauer auth`).

## Objetivo

Num processo marcado com `BAUER_LOCAL_ONLY` (gravado por `bauer agent --local`
e `bauer serve --local`), todo client auxiliar é local. Se não for, a criação
é recusada antes de qualquer request. Se o config pede um auxiliar de nuvem,
o `--local` recusa subir.

## Escopo

1. **Camada 1 — boot.** `validar_execucao_local` ganha
   `problemas_auxiliares_locais(cfg)`. São problemas:
   - todo slot de `AuxiliarySection` com `provider` explícito que não é
     local (`provider_e_local`, a mesma regra do modelo principal);
   - `STT_PROVIDER` explícito de nuvem;
   - `TTS_PROVIDER`/`BAUER_TTS_PROVIDER` explícito de nuvem.

   O `agent --local`, o `serve --local` e o rebuild do `/model --local`
   (sprint 69) já chamam essa função. Por isso, os três passam a cobrir os
   auxiliares sem mudança nos comandos.
2. **Camada 2 — criação.** Com a marca ligada:
   - `get_text_auxiliary_client` recusa (log WARNING, `(None, None)`) o slot
     cujo provider resolvido não é local, antes de construir o client;
   - `_build_client_for_provider` levanta `AuxiliaryLocalOnlyRefused`
     (subclasse de `ValueError`) no topo, antes do token do `bauer auth` e de
     qualquer construtor. Isso cobre as chamadas diretas do `server.py`;
   - transcrição e TTS descartam toda tentativa de nuvem e devolvem um erro
     que cita `--local`. `available_stt_provider`/`available_tts_provider`
     só reportam o provider local;
   - `image_generate` e `text_to_speech` (tools) levantam `ToolError` antes
     de qualquer client.

## Decisões

1. **Slot vazio herda o principal, e isso é aceito.** `_resolve_slot` cai em
   `model.provider`/`model.name`. O boot do `--local` já exige
   `model.provider` local. Slot só com `model` (sem `provider`) herda o
   provider principal e também é aceito.
2. **Default de nuvem embutido é recusado em runtime, sem derrubar o boot.**
   Os slots `auxiliary.*` não têm default de nuvem: o default é o principal.
   A transcrição tem: sem `STT_PROVIDER`, usa OpenRouter. Sob a marca, esse
   default é recusado. A transcrição só roda localmente e só com
   `STT_PROVIDER=local` ou `auto`. Sem a variável, nem o local é escolhido
   em silêncio: o erro pede `STT_PROVIDER=local`. O boot não recusa a
   variável ausente. Se recusasse, todo `--local` sem voz configurada
   deixaria de subir por um recurso que talvez nunca seja usado. Valor
   explícito de nuvem (`STT_PROVIDER=groq`, `TTS_PROVIDER=openai`, …) é
   configuração que contradiz o `--local` e recusa o boot, como um slot.
3. **O TTS segue a mesma regra.** `kokoro` e `local`/`coqui`/`xtts` passam.
   `auto` fica restrito ao local. `openai`/`google` são recusados.
4. **`image_generate` e `text_to_speech` (tools) são recusados por inteiro.**
   Os três providers de imagem são de nuvem. O TTS da tool cai em
   `api.openai.com` quando o client não tem `.audio`. Não há variante local
   para preservar. A recusa diz para rodar sem `--local`.
5. **Recusa = indisponível, não troca de destino.** O slot recusado devolve
   `(None, None)`, o mesmo contrato de "slot não disponível" que todo
   consumidor já trata:
   - a compressão cai no client da sessão (local) ou no rule-based;
   - a consolidação levanta `ConsolidationError`;
   - kanban devolve `auxiliary_unavailable`;
   - o `os_intent` devolve `None`;
   - a revisão em background não registra nada.

   O slot recusado não é redirecionado ao modelo principal. Num `--local`, um
   slot de nuvem é erro de configuração, e o boot já o recusa. A camada 2
   existe para config relido ou carregado fora do boot.
6. **Juiz de aprovação recusado não bloqueia a tool.** O G4 já é fail-open
   quando não há juiz disponível. As camadas determinísticas (allowlist,
   limiar de risco, política, aprovação humana) continuam valendo. Tornar
   o G4 fail-closed só sob `--local` mudaria a semântica do gate por um
   motivo que não é de segurança: nada sai da máquina em nenhum dos dois
   casos.
7. **Localidade é a de `provider_e_local`.** É a mesma regra do principal
   (`ollama`, `lmstudio`; `custom` só com host local). Uma regra diferente
   para os auxiliares faria o boot e a criação discordarem.
8. **Threads e cron no mesmo processo.** A marca está em `os.environ`. A
   revisão em background (thread daemon), o agendador de consolidação e o
   Whisper/TTS em thread leem a mesma marca. O teste do `background_reviewer`
   roda o consumidor numa thread.
9. **`headless_approval` fica como está.** O modo `smart` pede o slot
   `headless_approval`, que não existe no schema. `get_text_auxiliary_client`
   devolve `(None, None)` sem construir client, e o modo cai no `threshold`.
   Não é porta para a nuvem. A catraca registra o nome como o único slot fora
   do schema.
10. **Isolamento de testes.** `tests/conftest.py` remove `STT_PROVIDER`,
    `TTS_PROVIDER` e `BAUER_TTS_PROVIDER` por teste. O boot do `--local`
    passou a lê-las, e um valor no ambiente de quem roda a suíte não pode
    decidir resultado.

## Catracas

- `VALID_SLOTS == AuxiliarySection.model_fields`. Todo slot do schema tem um
  consumidor real em `SLOT_CONSUMERS` do teste. Os testes de boot e de
  criação são parametrizados pelo schema: um slot novo entra neles sozinho.
- Os literais de slot usados em `get_text_auxiliary_client`/`call_aux_text`
  no código são `VALID_SLOTS` mais `SLOTS_FORA_DO_SCHEMA`
  (`{"headless_approval"}`), exatamente.
- `CONSTRUCAO_PERMITIDA` conta, por arquivo, os sites que constroem um client
  de modelo que pode ser de nuvem, com o motivo de cada um. Site novo ou
  contagem diferente falha o teste.

## Fora de escopo (registrado)

- **Provedores de memória remotos** (`Mem0Provider`, `HonchoProvider`,
  `HttpMemoryProvider` em `memory_provider.py`). Mandam conversa para APIs
  externas, mas não são clients de modelo. Não foram cobertos aqui. São o
  próximo candidato para o `--local`.
- **`ollama.host` remoto.** `provider_e_local("ollama")` é sempre verdadeiro,
  mesmo com host fora da máquina (Ollama Cloud com `api_key`). Vale para o
  principal e para os auxiliares. Mudar a regra afeta o boot inteiro e fica
  para outra sprint.
- **Modelo do turno Agno** (`agno_adapter._build_model`) e **`/model` dos
  canais** (`channel_base`, processo do gateway, sem `--local`). São o turno
  principal, não auxiliares.

## Critérios de aceite

- [x] Um teste por slot `auxiliary.*`, pelo consumidor real, falha sem a
  correção: com a marca, nenhum client de nuvem é construído nem chamado.
- [x] `validar_execucao_local` recusa slot de nuvem e voz explícita de nuvem.
  Aceita slot local, slot vazio e slot só com `model`.
- [x] `_build_client_for_provider` recusa nuvem sob a marca, inclusive pelo
  token do `bauer auth`.
- [x] Transcrição, TTS, `image_generate` e `text_to_speech` não fazem request
  de nuvem sob a marca. O caminho local continua funcionando.
- [x] Sem a marca, nada muda (controles positivos).
- [x] Catracas de inventário (schema, literais, sites de construção).

## Plano de validação

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
```

Regressões: `tests/test_sprint72_local_auxiliary_clients.py`.
