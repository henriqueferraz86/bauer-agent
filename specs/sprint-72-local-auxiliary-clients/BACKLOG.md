# BACKLOG — Sprint 72

## Tarefas

### 1. Inventariar os clients auxiliares

Status: concluído

Os sete slots de `AuxiliarySection` e os consumidores de cada um, o uso
direto de `_build_client_for_provider` pelo `server.py`, a transcrição, o
TTS e as tools de mídia de nuvem (tabela no `SPEC.md`). Não existe geração de
título ou resumo de sessão por LLM fora da compressão.

### 2. Reproduzir

Status: concluído

`tests/test_sprint72_local_auxiliary_clients.py` na base `3677d0d`, sem a
correção: 44 casos falham e 27 passam. Os que passam são os controles
positivos e as catracas de inventário. Todo caso "um por slot" falha.

### 3. Camada 1 — boot

Status: concluído

Critérios:
- `validar_execucao_local` recusa slot `auxiliary.*` com provider de nuvem,
  além de `STT_PROVIDER` e `TTS_PROVIDER`/`BAUER_TTS_PROVIDER` explícitos de
  nuvem.
- Slot local, slot vazio e slot só com `model` são aceitos.
- A lista de slots vem do schema.

### 4. Camada 2 — criação

Status: concluído

Critérios:
- Com a marca ligada, `get_text_auxiliary_client` não constrói client de
  nuvem e `_build_client_for_provider` levanta `AuxiliaryLocalOnlyRefused`
  antes do token do `bauer auth`.
- Transcrição e TTS só usam o provider local. `image_generate` e
  `text_to_speech` são recusados antes de qualquer client.
- Sem a marca, nada muda.

### 5. Catracas de inventário

Status: concluído

- Schema × `VALID_SLOTS` × `SLOT_CONSUMERS`.
- Literais de slot no código, com `headless_approval` como única exceção.
- Sites de construção de client por arquivo (`CONSTRUCAO_PERMITIDA`).

### 6. Gates e revisão de segurança

Status: concluído

Gates:
- pytest: exit 0;
- ruff crítico e ruff de estilo: sem achados;
- mypy: 421 arquivos, sem issues.

Revisão do diff:
- as guardas só apertam e nunca desligam nada sem a marca;
- a recusa acontece antes de qualquer construtor, token ou request;
- provider desconhecido ou vazio conta como não local (falha fechado);
- nenhum `except: pass` novo;
- o G4 continua fail-open quando o juiz é recusado (decisão 6 do `SPEC.md`).

## Notas de entrega

- Branch: `codex/sprint-72-local-auxiliary-clients`, base `origin/master`
  `3677d0d`.
- Achado fora do escopo, sem correção nesta sprint: a suíte grava
  `memory/runtime/agno/sessions.db` e `runtime/` na raiz do checkout. O
  `tests/test_channel_agno_turn.py` grava ali, e o estado se acumula entre
  execuções. Num checkout reaproveitado,
  `test_agno_happy_path_completes_simple_turn_through_channel` passou a
  estourar o timeout de 120 s de forma determinística. Com os diretórios
  removidos, voltou a passar, tanto no `origin/master` quanto nesta branch.
  É uma falha de hermeticidade anterior a esta sprint.
- Pendências: os provedores de memória remotos (Mem0, Honcho, HTTP) e o
  `ollama.host` remoto (ver "Fora de escopo" no `SPEC.md`).
