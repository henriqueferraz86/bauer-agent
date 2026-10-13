# Revisão independente — ferramentas

Executei via `uv run` os scripts `/tmp/bauer_audit_tools_probe.py` e `/tmp/bauer_audit_tools_probe_extra.py`, depois de sync informado pelo root. Também rodei `/tmp/bauer-tools-independent-review.py` para testar ausência de ShellRunner e contrastar cronjob/kanban em dry_run. Nenhum produto foi modificado. Saídas adicionais: `/tmp/bauer-tools-independent-extra.txt`, `/tmp/bauer-tools-independent-review.txt`.

## Decisão

| Achado | Parecer | Ressalva |
|---|---|---|
| T01 cronjob shell | Manter P1 | Bypass de allowlist, disponibilidade shell e runtime policy em modo enforce confirmado; denylist interna continua aplicada. |
| T02 policy network.http | Manter P1 | Confirmado para http_request; não tratar delegate_task/mcp_call como prova automática da mesma operação sem contrato de mapeamento definido. |
| T03 auth em redirect | Manter P1 | Transporte fake confirma segunda origem recebendo Authorization; sem afirmação de exfiltração real executada. |
| T04 upload mídia fora workspace | Manter P1 | Bridge fake recebe absoluto externo; não houve envio real. |
| T05 symlink memória | Manter P1 | Sandbox rejeita symlink, handler memory set efetivamente grava destino externo. Skills/cron têm mesmo padrão identificado em leitura. |
| T06 dry_run | Manter P1 | Memory e Kanban efeitos reais reproduzidos. Remover associação indevida com gate App Factory. |
| T07 Kanban metadados/dedup | Consolidar em P2 | Dedup replay e leitura stale reais. Metadados incorretos não provam bypass do gate por contexto. |
| T08 aprovação registry | Manter P1 | Com llm_client presente, extensão requires_approval=True executa, spy de approver tem 0 chamadas. |
| T09 schemas registry | Manter P2 | Tool disponível/executável ausente no schema; override mantém contrato antigo. |
| T10 tipos/required schema | Manter P2 | Social.channels string rejeitada; Kanban exige globalmente campos próprios de ações distintas. |

## Esclarecimentos necessários

### Cronjob

`ToolRouter.execute` aplica runtime policy somente se `policy_enabled=True`; `cronjob` não possui mapeamento em `_policy_operation_for_tool` e por isso é liberado mesmo com policy habilitada. No probe, o router está em `_policy_mode='enforce'` (default). Comparação com run_command bloqueado em waiting_approval é válida. Quando `policy_enabled=False`, a ausência de aprovação de runtime policy seria esperada por design, porém o desvio da allowlist do ShellRunner persiste.

Reprodução extra sem runner: `NO_SHELL_RUNNER=True`, cronjob shell printf executa e retorna exit 0. `_build_shell_runner` em `commands/_runtime.py:634` retorna None quando shell_enabled=False; esse cenário confirma a quebra do gate de disponibilidade.

Não chamar o achado de bypass de denylist: cronjob.py:160 explicitamente verifica `_DENYLIST`. Também não é prova de shell injection por operadores, já que subprocess usa argv shlex com shell=False. O problema é ter um caminho de execução paralelo aos gates centrais.

Cronjob está incluído em `_DRY_RUN_SIDE_EFFECTS` e portanto é simulado em dry_run. Teste suplementar: workspace permanece vazio após cronjob create em dry_run. Não associar T01 a bypass de dry_run.

### Dry run

Baseline do router novo dry_run era diretório vazio. `kanban_write create` criou `.bauer_dispatch`, `TASKS.md`, `PROJECT.md`; não são apenas artefatos criados pelo construtor. `memory set` também gravou estado real.

A frase de T06 ligando `:1644` à desativação de gate App Factory para tools omitidas é imprecisa: `_AF_GUARDED` contém write_file/append_file/patch/create_dir/move_file, e todas elas já estão no conjunto que retorna DryRunResult antes dessa parte. Manter o achado pela lista incompleta de efeitos; retirar essa causa secundária.

Efeitos externos de send_message/social_post/process são hipótese sustentada pelo dispatcher e devem ficar explicitamente marcados como não executados neste teste (o relatório já o faz). A escrita real é prova suficiente da quebra de contrato.

### Kanban

Metadados públicos `tool_security`/`tool_info` retornam read/low para kanban_write por fallback. O `_publish_tool_event` (tool_router.py:1863) usa `_TOOL_SECURITY.get(name, {})`, portanto o evento recebe `permission=None/risk=None`, e não read/low. Corrigir frase sobre auditoria para refletir a diferença entre catálogo e evento.

O gate por contexto em `KanbanToolsMixin._kanban_write` continua ativo. A migração não deu autorização de complete/heartbeat ao chat. Falha é drift dos metadados e do conjunto MUTATING_TOOLS: escrita nova não invalida cache e pode ser suprimida por replay; não demonstramos bypass contextual. P2 é severidade consistente com esse efeito funcional confirmado.

## Cobertura

Não rejeito nenhum dos dez achados centrais; rebaixo a ambiguidade P1/P2 de T07 para P2 e rejeito apenas as interpretações ampliadas indicadas acima. Nenhum teste real de rede/pessoa/provider foi necessário para esta revisão.
