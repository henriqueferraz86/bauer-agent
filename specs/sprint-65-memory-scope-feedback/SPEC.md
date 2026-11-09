# SPEC — Sprint 65: contrato de escopo e feedback enriquecido (plano 065, fatia 0)

## Origem e decisões

Fatia 0 (`enrich_feedback_and_scope_contract`) do design
`docs/architecture/memory-consolidation-loop.yaml`. O spike 017 parou porque as
sessões não tinham identidade de escopo. O coordenador decidiu, por delegação
do usuário:

- **Escopo `workspace_only`.** Toda leitura/escrita de consolidação e
  feedback acontece dentro de um workspace, identificado por
  `ws-` + `sha256(normcase(caminho resolvido))[:16]`. O escopo `company` fica
  rejeitado até existir contrato de identidade. O escopo `user` fica adiado,
  porque as sessões não têm `user_id`.
- **Fail closed para o histórico.** Uma sessão sem proveniência verificável
  nunca é elegível.
- **`machine_id` não particiona.** Continua só como metadado.
- **Redação antes de persistir.** O feedback usa `privacy.redact_text` e
  descarta (`redaction=dropped`) o trecho que o scanner ainda detectar.

## Problema

- O `/thumbsup`/`/thumbsdown` grava só nota, modelo, `machine_id` e
  pergunta/resposta truncadas, **sem redação**, no `FEEDBACK.md`. Não grava
  sessão, turno, tools, digest nem escopo.
- O `sessions.db` é compartilhado por qualquer workspace que use o mesmo
  `--sessions-dir` e não diz de qual workspace veio cada sessão.

## Escopo

1. `bauer/memory_scope.py`: `WorkspaceScope`, `resolve_workspace_scope` (com
   o mesmo guard de tipo de `_safe_workspace`), `validate_scope_kind`
   (`company`/`user` → `ScopeViolationError`), `sanitize_text` (redige, corta
   depois e marca o que ainda é sensível) e `text_digest`.
2. `SqliteSessionStore(workspace=...)`:
   - migração idempotente das colunas `workspace_id`, `workspace_path` e
     `workspace_conflict`;
   - só o INSERT grava o workspace na sessão. Uma linha histórica (NULL)
     nunca ganha proveniência depois;
   - uma sessão com workspace gravado que é salva por outro workspace, ou por
     um store sem workspace, é marcada em conflito;
   - `list_sessions_for_workspace`, `get_provenance` e `provenance_summary`.
3. `bauer agent` (os dois pontos de criação do store em `agent_cmd.py`) passa
   `workspace=router.workspace`, o mesmo workspace cuja memória o
   `MemoryProvider` injeta.
4. `bauer/memory_feedback.py`: `FeedbackStore` (SQLite em
   `<workspace>/memory/consolidation/feedback.sqlite3`). Grava `workspace_id`,
   `scope`, `rating`, `session_id` validado, `turn_index`, digests do texto
   redigido, trechos redigidos, motivo, tools do turno, modelo, `machine_id`,
   decisão de redação e `created_at`. Só lê linhas do próprio `workspace_id`.
5. Handler do agente: `record_turn_feedback(...)` best-effort.
   `/thumbsup <motivo>` e `/thumbsdown <motivo>` aceitam motivo (os aliases
   curtos continuam exatos). O `FEEDBACK.md` legado passa a ser redigido.
6. `bauer memory feedback list` para inspeção.

## Fora de escopo

- Proveniência para canais (Telegram/Discord), `serve` e web. As sessões
  desses caminhos continuam sem workspace e, portanto, inelegíveis.
- Fingerprint e diretivas (sprint 68). Consolidação (sprints 66–67).

## Critérios de aceite

- [x] Nenhuma leitura de sessão ou feedback atravessa workspaces, mesmo com o
  banco copiado de um workspace para outro.
- [x] Sessão histórica ou migrada fica sem proveniência e inelegível, inclusive
  depois de ser retomada.
- [x] Sessão retomada por outro workspace fica em conflito e inelegível.
- [x] Segredo do turno não aparece no banco de feedback nem no `FEEDBACK.md`.
- [x] `company`/`user` são recusados com o motivo da decisão.
- [x] Feedback com workspace inválido (ex.: `MagicMock`) não escreve nada.

## Plano de validação

`tests/test_memory_scope_feedback.py` (unitários + loop do agente com cliente
fake) e os gates completos do plano 065.
