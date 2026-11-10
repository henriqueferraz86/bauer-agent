# Plano 066 — Pendências remanescentes dos planos 064 e 065

**Status:** DONE (sprints 69–72; PRs #168, #169 e #170)

**Prioridade:** P1 (sprint 69) / P2 (sprints 70–71)

**Esforço total estimado:** M, dividido em sprints 69–72

**Referências:** notas "Left open" e "Pendências" dos BACKLOGs das sprints
59–68 (`specs/sprint-59-*` a `specs/sprint-68-*`) e `plans/README.md`.

## Problema e objetivo

As sprints dos planos 064 (segurança) e 065 (consolidação de memória)
fecharam o escopo prometido, mas registraram lacunas vizinhas que ficaram
fora. Algumas são fronteiras de segurança no mesmo padrão dos achados
corrigidos. Outras limitam o alcance da memória nova a um só caminho de
execução. Este plano fecha essas lacunas sem abrir funcionalidade nova.

## Sprints

### Sprint 69 — Fronteiras de rede e aprovação restantes (P1) — DONE (PR #168)

- `web_search`, `mcp_call` e `delegate_task` ganham contrato de operação de
  rede na política, como `http_request` e `web_fetch` ganharam na sprint 62.
  Com `deny`, nada chega ao transporte. Com `ask`, fica um approval pendente.
- `lsp_rename` exige aprovação, mas não tem operação de política. Hoje só o
  aprovador LLM o vê. Ele passa a ter uma operação de política real.
- O `/model` do `bauer agent --local` interativo reescreve o `config.yaml` sem
  a guarda `--local`. A mesma garantia da sprint 62 passa a valer aqui.
- **Aceite:** um teste de regressão por item, falhando sem a correção.

### Sprint 70 — Memória em todos os caminhos de execução (P2) — DONE (PR #169)

- Sessões criadas por serve/web, OpenAI-compat e canais (Telegram, Discord)
  registram a origem de workspace, como já fazem as sessões do
  `bauer agent`. O mesmo vale para o feedback desses caminhos, quando houver.
- Diretivas aprovadas e fatos consolidados entram também no prompt desses
  caminhos, respeitando o workspace de cada um.
- Revogar uma diretiva tira o efeito dela já no próximo turno, inclusive numa
  sessão aberta.
- **Aceite:** sem leitura nem escrita cruzada entre workspaces. Sessões
  antigas sem origem continuam fora. Testes herméticos cobrem cada caminho.

### Sprint 71 — Higiene (P2) — DONE (PR #169)

- O prompt de Kanban em `agent.py` ainda usa os nomes antigos `kanban_*`.
  Ele passa a usar `kanban_read` e `kanban_write`.
- `SqliteSessionStore.delete` remove vetores pelo prefixo `"{sid}:"`, o que
  pode atingir outra sessão cujo ID comece igual. A remoção passa a ser por
  identidade exata.
- O documento do plano 049, que nunca chegou ao `master`, é reconciliado na
  tabela de `plans/README.md`. Ou traz o documento da branch
  `codex/cli-visual-2`, ou registra que ele não existe mais.

### Sprint 72 — `--local` nos clients auxiliares (P1) — DONE (PR #170)

A sprint 69 fechou o `--local` na delegação. Ficou aberto o resto: numa
sessão `--local`, os clients auxiliares não são verificados. São eles o juiz
de aprovação (`auxiliary.approval_model`), a compressão de contexto, a
consolidação de memória, a transcrição e qualquer outro slot `auxiliary.*`.
A checagem de início não cobre esses clients.

- Todo client auxiliar criado num processo marcado com `BAUER_LOCAL_ONLY`
  precisa ser local. Se não for, a criação é recusada antes de qualquer
  request.
- Se um slot auxiliar apontar para cloud, o `--local` recusa subir, como já
  faz com o modelo principal.
- **Aceite:** um teste por slot auxiliar, falhando sem a correção, e um teste
  que trava o inventário dos slots, para que um slot novo não escape.

## Fora de escopo

- Passar um handle de arquivo pela API do bridge para eliminar a janela
  residual do T04. Isso exige mudar o contrato do bridge.
- Reconferir o budget entre tentativas. A sprint 60 deixou de fora de
  propósito, porque a reserva é idempotente por run.

## Validação obrigatória por sprint

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
```
