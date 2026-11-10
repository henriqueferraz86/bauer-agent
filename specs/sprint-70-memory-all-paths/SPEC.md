# SPEC — Sprint 70: memória em todos os caminhos de execução (plano 066)

Depende das sprints 65–68 (plano 065). Fecha a limitação registrada nelas:
só as sessões do `bauer agent` tinham proveniência de workspace, e só o
`bauer agent` lia diretivas aprovadas e fatos consolidados — num bloco montado
uma vez na abertura da sessão.

## Problema

- As sessões do `serve`/web (`/chat`, `/stream`, `/loop`), do OpenAI-compat
  (`/v1`) e dos canais (Telegram, Discord) não diziam de qual workspace vinham.
  Ficavam inelegíveis para a consolidação.
- O `serve` grava JSONL (`SessionStore`), não o `sessions.db` que o
  consolidador lê. A importação automática de JSONL do `SqliteSessionStore`
  cria linhas sem proveniência e nunca as atualiza.
- O gateway usa um único `SqliteSessionStore` para todos os projetos. O
  workspace do store era fixado no construtor.
- Diretivas e fatos consolidados não entravam no prompt desses caminhos.
- No `bauer agent`, a diretiva entrava num bloco `system` gravado no
  histórico. Revogar só valia na próxima sessão, e retomar a sessão antiga
  trazia a diretiva revogada de volta.

## Decisões (autoridade delegada ao implementador, registradas aqui)

- **Workspace por caminho:**
  - web (`/chat`, `/stream`, `/loop`): o workspace efetivo do turno
    (`_effective_ws(active_router)`). É resolvido no servidor pelo registro de
    projetos e pelo projeto fixado na sessão, nunca por um caminho enviado
    pelo cliente;
  - `/v1`: o workspace do router padrão do `serve`, o mesmo em que as tools
    desse caminho rodam;
  - canais: só o workspace do projeto **vinculado** pelo operador (sprint 33).
    Sem vínculo (Discord, grupos, Telegram sem `/projetos`), nada é gravado
    nem injetado (fail closed). O workspace padrão do gateway não é usado
    implicitamente.
- **Proveniência por gravação:** `SqliteSessionStore.save(..., workspace=...)`
  substitui o workspace do store naquela gravação. Valem as regras da sprint 65:
  - só o INSERT carimba;
  - uma linha histórica (NULL) continua NULL;
  - trocar de workspace, ou gravar sem workspace, marca conflito.
- **Espelho do `serve`:** `SessionStore.save(..., workspace=...)` grava a cópia
  **redigida** também no `sessions.db` do mesmo diretório, antes do JSONL.
  Gravar antes impede que a importação automática chegue primeiro e carimbe a
  sessão nova como histórica. Sem `workspace`, o comportamento antigo é
  mantido. Apagar a sessão pelo `serve` apaga também o espelho, mas só quando
  o JSONL existia: o endpoint não apaga sessões que só existem no banco.
- **Injeção dinâmica:**
  - `ContextManager.memory_block` é um callable recalculado a cada
    `get_payload()` e anexado ao prompt de sistema;
  - nunca entra em `messages`, então nunca é persistido nem chega ao
    consolidador;
  - revogar ou vencer uma diretiva vale na próxima chamada ao LLM, inclusive
    numa sessão aberta.
- **Fonte única:** `bauer/memory_injection.py`.
  - Os fatos consolidados só entram quando carimbados com o `scope` do
    workspace (`- scope: ws-…`), então um `CONSOLIDATED.md` copiado não vaza.
  - As diretivas já filtravam por `workspace_id` desde a sprint 68.
- **`bauer agent`:**
  - as diretivas saem do `LocalMemoryProvider.system_prompt_block` e passam a
    vir do bloco dinâmico. Os fatos continuam no bloco do provider, como antes;
  - ao retomar uma sessão gravada antes desta sprint, a seção de diretivas
    congelada no histórico é removida (`strip_persisted_directives`).
- **Feedback:** web, `/v1` e canais não têm `/thumbsup`/`/thumbsdown`. Não há
  feedback a carimbar nesses caminhos, e nenhum foi criado (fora de escopo).

## Escopo

1. `bauer/memory_injection.py`: `workspace_memory_block`,
   `attach_workspace_memory` e `strip_persisted_directives`.
2. `ContextManager.memory_block` + `get_payload`.
3. `SqliteSessionStore.save(workspace=...)`.
4. `SessionStore.save(workspace=...)` com espelho de proveniência e `delete`
   coerente.
5. `serve`: `_new_context` liga a memória; `/chat`, `/stream`, `/loop` e `/v1`
   passam o workspace ao gravar.
6. Canais (`AgentBackend`): memória e proveniência pelo projeto vinculado, a
   cada turno (`_turno_cru` e `_turno_agno`).
7. `bauer agent`: bloco dinâmico e limpeza do histórico legado.

## Fora de escopo

- Feedback (👍/👎) na web, no `/v1` e nos canais.
- Vínculo de projeto para Discord e grupos.
- Proveniência retroativa para sessões históricas (continuam inelegíveis).

## Critérios de aceite

- [x] Nenhuma leitura ou escrita cruzada entre workspaces, em cada caminho.
  Há teste com dois workspaces na web, no Telegram e no `/loop`/`/v1`.
- [x] Sessões históricas sem proveniência continuam fora (web e Telegram).
- [x] Revogar uma diretiva tira o efeito no próximo turno da mesma sessão
  aberta (web, `/v1`, Telegram e `bauer agent`).
- [x] Discord e Telegram sem vínculo não gravam proveniência nem recebem
  memória.
- [x] A memória injetada não aparece no histórico gravado.

## Plano de validação

`tests/test_memory_all_paths.py` (hermético, cliente LLM fake) e os gates
completos do plano 066.
