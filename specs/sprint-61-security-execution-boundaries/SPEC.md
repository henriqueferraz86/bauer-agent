# SPEC — Sprint 61: fronteiras de execução

Plano: [`plans/064-seguranca-auditoria-2026-10-12.md`](../../plans/064-seguranca-auditoria-2026-10-12.md),
lote 2 (parte de tools) e metadados de segurança do lote 6 de
[`docs/audits/2026-10-12/CORRECTIONS.md`](../../docs/audits/2026-10-12/CORRECTIONS.md).
Branch `codex/sprint-61-security-execution-boundaries`, empilhada sobre a
sprint 59 (`codex/sprint-59-security-confinement`).

## Problema

Reprodução no código atual (sprint 59 aplicada), com
`bauer_audit_tools_probe_extra.py` antes da correção:

| ID | Achado | Prova antes |
|---|---|---|
| T01 | O cronjob de shell executa um comando que o `ShellRunner` bloqueia, sem a aprovação que `run_command` exige. Também roda sem shell (`shell_enabled=false`). | `SHELL_PRINTF_DENIED BlockedCommandError`, `CRON_SHELL_RESULT … exit: 0 … cron-audit`, `POLICY_SHELL_DENIED waiting_approval` para o mesmo comando via `run_command`. Com router sem `ShellRunner`, testes antigos (`test_gap_tools`, `test_scanner_fixes`) rodavam `echo`/`python -c` pelo cron. **Reproduz.** |
| T06 | `dry_run=True` grava memória e cria tarefa/arquivos reais no Kanban | `DRY_MEMORY_FILE True`, `DRY_KANBAN_RESULT Tarefa criada`, `DRY_KANBAN_FILES ['.bauer_dispatch','TASKS.md','PROJECT.md', …]`. **Reproduz.** |
| T08 | Extensão com `requires_approval=True` executa sem consultar o aprovador | `EXTERNAL_WITH_LLM ran`, `EXTERNAL_APPROVER_CALL_COUNT 0`, `EXTERNAL_SECURITY {'permission': 'read', 'risk': 'low', 'approval': False}` com `tool_info … requires_approval True`. **Reproduz.** |
| Lote 6 (seg.) | `kanban_write` reportado como `read/low` no catálogo e `permission/risk=None` nos eventos; 9 entradas `kanban_*` e `video_generate` órfãs; `verify_app` com `permission="shell"` (fora de `_PERMISSION_LEVELS`) | `KANBAN_SECURITY {'permission': 'read', 'risk': 'low', …}`, `SECURITY_MISSING ['kanban_read','kanban_write']`, `SECURITY_ORPHANS [kanban_block, …, video_generate]`. **Reproduz.** |

## Objetivo

Toda execução de comando passa pelos mesmos gates de `run_command` e
`execute_code`. `dry_run` simula toda ação com efeito antes do handler.
Extensões do `ToolRegistry` e overrides de built-in usam a definição de
segurança efetiva no catálogo, nos eventos e no dispatcher.

## Escopo

1. **T01 (cronjob run).** O comando persistido roda por
   `execute_governed(router, "run_command" | "execute_code", …)`, que chama o
   próprio `ToolRouter.execute`. Isso aplica:
   - allowlist de contexto da tool interna (`tool denied: 'run_command'`);
   - HARDLINE e denylist (o pré-check de denylist do cron foi mantido como
     defesa em profundidade);
   - policy `shell.execute` (ask/deny/espera de aprovação, `approval_id`
     repassado);
   - command guards, dry-run e aprovação por LLM;
   - `ShellRunner` (allowlist, denylist, safe_mode), timeout e auditoria.

   A chamada interna não conta de novo em `max_tool_calls`
   (`INNER_GOVERNED_CALL`). O modo `shell` é recusado em `create` e em `run`
   quando não há shell (`run_command` não registrado = `shell_enabled: false`).
   O `run_count`/`last_run` só muda depois de uma execução autorizada.
2. **T06 (dry-run).** `bauer/tool_effects.py` declara o efeito por tool e por
   ação:
   - leitura pura, tools híbridas (`memory` get/list, `cronjob` list,
     `process` list/poll/log) e efeito;
   - **default seguro**: built-in sem classificação, extensão sem
     `side_effects=False` e capability com `permission != read` contam como
     efeito;
   - override de built-in só sai do dry-run se as duas definições forem
     leitura.

   O `ToolRouter.execute` consulta essa função antes do handler e antes do
   gate do App Factory, que dependia de "não dry_run".
3. **T08 (aprovação do registry).** `_effective_security(name)` é a fonte
   única de segurança. Uma extensão usa a própria declaração. Um override de
   built-in usa o **maior** nível de permissão e risco e a aprovação de
   qualquer um dos dois, então nunca rebaixa. A função é usada por:
   - `tool_security` e `tool_info`;
   - `_publish_tool_event`;
   - o gate de aprovação por LLM;
   - o resumo do `DryRunResult`.

   Com policy habilitada, uma extensão que exige aprovação passa pela
   operação `tool.approval` (regra default `ask`). Ela pede aprovação humana
   pelo `ApprovalManager`, como `shell.execute`.
4. **Metadados de segurança (lote 6).**
   - `kanban_read` = read/low e `kanban_write` = write/low.
   - Removidas as 9 entradas `kanban_*` legadas e `video_generate`.
   - `verify_app` = execute.
   - `memory` = write (set/delete gravam).
   - `cronjob` = execute/high (o gate real é o da tool interna).

   Um teste trava a paridade "inventário de built-ins ↔ `_TOOL_SECURITY`".

## Decisões registradas (condição de STOP do plano)

- **Reinterpretação de `dry_run` (decisão explícita).** O contrato antigo era
  uma lista de 19 nomes, e o resto rodava de verdade. O novo contrato:
  `dry_run` simula toda chamada com efeito, ou seja:
  - gravação local persistente;
  - processo ou código;
  - comunicação ou escrita externa;
  - geração paga em provider (`image_generate`, `text_to_speech`,
    `delegate_task`, `mixture_of_agents`).

  Consultas que só devolvem informação continuam rodando: busca web, análise
  de mídia, transcrição, `sqlite_query` read-only, `todo` (estado em memória)
  e leituras do browser sobre uma página que, em dry-run, nunca foi aberta.
  Mudanças visíveis:
  - `cronjob list` agora roda em dry-run (antes era simulado). O mesmo vale
    para as outras ações de leitura listadas acima.
  - `memory set/delete`, `kanban_write`, `skill_manage`, `app_factory_init`,
    `process start/kill/write`, `verify_app`, `mcp_call`, `mcp_list_tools`
    (inicia o servidor), `channel_send`, `send_message`, `social_post`,
    `lsp_format/rename`, `browser_back` e `mixture_of_agents` passam a ser
    simulados.
- **Extensões: default "tem efeito".** `ToolDefinition.side_effects` nasce
  `True`. A `permission` declarada não serve de base, porque o default
  histórico do registry é `"read"`, e derivar o efeito dela tornaria inseguro
  quem não declara nada.
- **Override de built-in nunca rebaixa a segurança.** Uma extensão pode
  elevar risco, permissão ou aprovação de um nome built-in, mas não reduzir.
  A política de override fica documentada aqui, como pede T08.
- **`tool.approval` pede aprovação quando nenhuma regra casa**
  (`ASK_WHEN_UNMATCHED_OPERATIONS`). Um `policy.yaml` escrito antes da
  operação existir (inclusive o `config/policy.yaml` do repo, atualizado
  junto) não pode derrubar em silêncio a aprovação declarada pela extensão.
  Uma regra explícita (allow, ask ou deny) do operador continua valendo. Não
  entrou em `ALWAYS_ASK_OPERATIONS`, para preservar a autonomia do operador e
  o modo `audit`, com o mesmo comportamento de `shell.execute`.
- **Sem policy habilitada**, a aprovação da extensão é a mesma das built-ins
  com `approval=True`: o aprovador por LLM, quando há `llm_client`. Sem
  `llm_client`, a extensão roda, como `delete_file` e `run_command` nesse
  modo. Isso é paridade, e não um privilégio novo.
- **Nenhuma tool ganhou privilégio.** O cron perdeu o caminho paralelo. Os
  testes antigos que rodavam cron shell sem `ShellRunner`
  (`test_gap_tools::test_run_shell_job/test_create_shell_mode` e
  `test_scanner_fixes::TestCronjobShellDenylist`) passaram a usar um router
  com `ShellRunner`. Nenhuma guarda foi silenciada.

## Fora de escopo

- T02/T03/WEB-02/WEB-03 (track C) e K07/K08/WEB-08/WEB-10 (track B). A edição
  em `tool_router.py` não toca `_policy_operation_for_tool`, que é a área de
  T02: a operação da extensão é resolvida numa função à parte.
- `lsp_rename` declara `approval=True` mas não tem operação de policy, então
  só passa pelo aprovador por LLM. Mapear built-ins para `tool.approval` muda
  o contrato de built-ins e ficou registrado no BACKLOG.
- O prompt de worker Kanban em `agent.py` ainda cita os nomes `kanban_*`
  antigos. É um drift funcional (T07), não de segurança.

## Critérios de aceite

- T01: um comando fora da allowlist fica em `waiting_approval`, com shell
  desabilitado é recusado, e policy deny vale para o job persistido.
  HARDLINE e denylist valem nos dois modos. Um job aprovado roda uma vez com
  `approval_id` e conta uma vez no budget.
- T06: toda ação mutante da matriz devolve `[dry_run]` sem arquivo,
  processo, bridge ou Postiz. As ações de leitura continuam rodando. A
  extensão é simulada por padrão.
- T08: aprovação negada impede o `fn`, aprovação dada roda uma vez, e com
  policy a extensão gera `waiting_approval` em `tool.approval`.
  Metadados e eventos refletem a extensão, e o override não rebaixa.
- Lote 6: `kanban_write` aparece como write no catálogo e nos eventos, sem
  órfãos, e todo nível de permissão e risco é válido.

## Plano de validação

- `tests/test_sprint61_security_execution_boundaries.py`: 47 casos. 33 falham
  sem a correção. Os 14 restantes travam ações autorizadas e leituras que
  devem continuar funcionando.
- `bauer_audit_tools_probe_extra.py`:
  - `CRON_SHELL_DENIED waiting_approval … shell.execute`;
  - `DRY_MEMORY_FILE False`, `DRY_KANBAN_FILES []`;
  - `EXTERNAL_SECURITY {'permission': 'execute', 'risk': 'critical', 'approval': True}`
    e `EXTERNAL_APPROVER_CALL_COUNT 1`;
  - `KANBAN_SECURITY` write e `SECURITY_MISSING []`.

  Os órfãos restantes do probe (`http_request`, `run_command`, `web_fetch`,
  `web_search`) só existem porque o router do probe não tem shell nem web.
- Gates do `AGENTS.md`.
