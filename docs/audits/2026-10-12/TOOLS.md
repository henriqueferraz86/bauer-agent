# Auditoria de tools — 2026-10-12

Reproduções preservadas: [repro/README.md](repro/README.md). Evidências consolidadas: [REPORT.md](REPORT.md). Os caminhos `/tmp` abaixo identificam a execução original.

Escopo: ToolRouter, 17 mixins, registry, schemas nativos/bridge, dedup, políticas, caminhos e efeitos. Código de produto somente leitura. Reproduções via `uv run python` depois do `uv sync --frozen --extra dev` realizado pelo root. Scripts `/tmp/bauer_audit_tools_probe.py` e `/tmp/bauer_audit_tools_probe_extra.py`: temporários próprios, BAUER_HOME/CONFIG/AGENTS_FILE herméticos, transportes/bridges falsos, sem mensagens reais nem chamadas a providers.

## Inventário e duplicidades

Há 71 tools built-in quando shell e web estão habilitados; 67 sem ambas. 17 mixins não têm nenhum método de mesmo nome que sombreie outro na MRO (inventário AST). Grupos: fs 12, browser 12, code_intel 7, media 6, messaging 5, factory 4, utility 4, execution 3, web 3, mcp 3, skills 3, agent_misc 3, kanban 2, cronjob/memory/session/sqlite 1 cada.

Sobreposições intencionais:

- `search_text` é literal case insensitive, `regex_search` regex com flags: contratos diferentes; compartilhamento do engine já planejado/concluído em 045.
- `code_symbols/find_definition/find_usages/get_imports` dão fallback leve sem processo LSP; `lsp` fornece consulta semântica. Não recomendaria remover sem avaliar capacidade local.
- `channel_send` resolve nome de canal registrado e usa outbox; `send_message` usa chat_id direto e bridge vivo, com fallback outbox. Contratos diferentes, apesar de mesmo domínio.
- Nove `kanban_*` antigos são helpers internos encaminhados pelas duas tools atuais; não são nove tools duplicadas expostas. A migração 043 deixou drift problemático em metadados/dedup (T07).
- `video_generate` só aparece nos metadados/dedup, não tem handler nem schema. Entrada órfã, não uma tool implementada inacessível.

## Achados confirmados

### T01 — P1: cronjob executa comandos fora da allowlist e sem aprovação de shell

Evidência: `bauer/tools/cronjob.py:154` chama `_execute_code` diretamente; `:159` aplica apenas denylist; `:168` usa subprocess.run direto. `bauer/tool_router.py:1603` só executa command guards para run_command/execute_code/process; `:1985` mapeia essas operações para shell.execute, porém não cronjob.

Reprodução: ShellRunner.validate('printf cron-audit') dá BlockedCommandError. Mesmo router com ShellRunner e policy_enabled=True aceita criar cronjob mode=shell e rodá-lo, retornando exit 0/stdout cron-audit. run_command com o mesmo comando dá waiting_approval. Nenhum comando destrutivo utilizado.

Impacto: `tools.shell_enabled:false`, allowlist, safe_mode, command guards e política shell.execute podem ser contornados pela tool cronjob sempre exposta. Modo python também passa por fora do dispatch governado do execute_code, embora mantenha sua denylist interna.

Correção: resolver a operação por ação (cronjob run -> shell.execute), executar shell pelo mesmo ShellRunner validado, e encaminhar o guard determinístico para o comando persistido. Bloquear modo shell quando runner indisponível. Evitar contar chamada interna duas vezes no budget.

Regressão: printf fora allowlist e shell desabilitado não executam; policy ask/deny aplica a job persistido; guards hardline aplicam aos dois modos; job permitido continua funcional.

### T02 — P1: política network.http não é consultada pelas tools HTTP

Evidência: `bauer/tool_router.py:1459` retorna sem avaliação quando `_policy_operation_for_tool` é None; mapa `:1985` não inclui http_request/web_fetch/web_search/mcp_call/delegate_task. PolicyEngine tem deny explícito para network.http contendo segredo em `bauer/core/policy/engine.py:28`.

Reprodução: workspace com `.bauer/policy.yaml` rule `operation: network.http, action: deny`, router policy_enabled=True; http_request realiza ambas requisições fake e retorna HTTP 200. `_policy_operation_for_tool('http_request') == None`. Também enviado contains_secret=True no payload para demonstrar que até a condição prevista não chega ao engine.

Impacto: operador define regra deny para HTTP, porém a tool de HTTP ignora-a integralmente. Classificador/redator de output não protege headers/body de saída.

Correção: mapear explicitamente tools de rede à operação apropriada, agregar URL/método/payload consistente e detectar material secreto de saída em vez de confiar apenas em flag enviada pelo modelo.

Regressão: deny network.http interrompe antes de transporte; allow deixa passar; ask gera waiting_approval. Testar a regra padrão contains_secret com segredo sintético.

### T03 — P1: redirect HTTP entre hosts preserva Authorization e corpo

Evidência: `bauer/tools/web.py:168` envia headers/body inicial; `:176` resolve Location; `:183` reenvia os mesmos headers/json/content para nova origem sem filtrar credenciais e sem ajustar método para 301/302/303.

Reprodução transport fake: `https://source.example/start` responde 302 Location `https://other.example/target`; segunda request recebe `Authorization: Bearer audit-token`. Sem DNS, sem token real, sem rede.

Impacto: endpoint que redireciona para outra origem pode exfiltrar Authorization/Cookie/custom API key e corpo. SSRF valida o novo host, mas não autenticação por origem. POST 303 mantém POST, contrariando semântica comum de redirect.

Correção: usar Client com redirect seguro e validação de cada hop, ou remover headers sensíveis e corpo quando origem muda; implementar regra correta de método para status; nunca permitir downgrade HTTPS com credenciais.

Regressão: redirect same-origin preserva auth; foreign-origin não; 303 muda método para GET e retira corpo; hop interno é barrado antes de request.

### T04 — P1: envio/upload de mídia lê caminhos fora do workspace

Evidência: `bauer/tools/messaging.py:106` passa media_path cru para bridge.send_media; `:115` persiste path cru no outbox; `:221` passa cada media_paths cru a PostizClient.upload. Nenhum `_sandbox`.

Reprodução: fake bridge recebe caminho absoluto de arquivo privado criado no pai do workspace, e send_message retorna sucesso. `_sandbox` do mesmo caminho bloqueia. Não houve envio a pessoa real.

Impacto: um modelo que não pode ler um arquivo com read_file pode pedir upload/envio dele por outra tool. O caminho relativo do outbox pode ainda depender do CWD do pump e enviar arquivo diferente do esperado.

Correção: resolver/validar arquivo no workspace antes de qualquer envio; passar absoluto validado tanto para bridge quanto outbox e Postiz. Se escopo extra for necessário, usar allowlist explícita de leitura.

Regressão: absoluto externo, traversal e symlink externo rejeitados antes de envio/enqueue/upload; arquivo interno e mídia sem texto funcionam.

### T05 — P1: arquivos internos de memory/skills/cronjob seguem symlinks externos

Evidência: `bauer/tools/memory.py:17` constrói workspace / '.bauer_memory.json'; `:29` grava sem `_sandbox`; `bauer/tools/skills.py:25` e `bauer/tools/cronjob.py:32` seguem o mesmo padrão.

Reprodução: `.bauer_memory.json` é symlink para `{tmp}/outside-memory.json`. router._sandbox('.bauer_memory.json') lança SandboxError, mas router.execute(memory set) grava valor 'wrote-outside' no JSON externo. Só arquivo temporário próprio foi alterado.

Impacto: projeto com symlink pré-existente (ou criado via processo) pode fazer tools normalmente consideradas read/low lerem/sobrescreverem arquivos externos. read_file e write_file corretos não bastam para a garantia de sandbox.

Correção: aplicar resolução/containment aos arquivos internos e gravar atomicamente com semântica que não siga symlink inesperado. Aplicar em reads e writes.

Regressão: symlink externo rejeitado em get/list/set/delete, skills create/update e cronjob create/run; estado interno normal persiste.

### T06 — P1: dry_run executa efeitos persistentes e externos

Evidência: `bauer/tool_router.py:1615` enumera side effects incompletamente; faltam memory, todo, kanban_write, skill_manage, app_factory_init, process, verify_app, mcp_call, channel_send/send_message/social_post e outros. `:1623` usa apenas pertença a essa lista;

Reprodução router dry_run=True: memory set grava `.bauer_memory.json`; kanban_write create cria card, TASKS.md, PROJECT.md e `.bauer_dispatch`. Retorna sucesso real, não DryRunResult.

Impacto: modo que promete simular sem efeitos realiza escrita de workspace; pelo mesmo dispatch, pode lançar processo, enfileirar mensagem ou publicar post com transport/provider presentes. Publicação externa não foi executada neste teste.

Correção: centralizar efeito por tool/ação com default seguro para extensões. Toda ação mutante em dry_run retorna DryRunResult antes do handler e antes de omitir gates; ações de leitura podem continuar.

Regressão: matriz de todas as tools/actions mutantes sem transporte/arquivo/processo acionado; registry externo com efeito declarado também simulado.

### T07 — P2: kanban_write usa metadados antigos e cache de leitura

Evidência: `bauer/tool_router.py:902` registra kanban_write, mas `_TOOL_SECURITY` contém apenas os nove nomes antigos; `:1385` cai no fallback permission=read/risk=low. `bauer/tool_dedup.py:29` lista antigos e omite kanban_write; `:91` permite replay; `:115` não invalida leituras. O TestsGate importa esse mesmo conjunto em `bauer/core/kernel/gates/tests.py:38`.

Reprodução: kanban_write create registrada no deduper -> check dos mesmos args retorna replay; depois de cached kanban_read list e novo create, check de kanban_read retorna lista antiga. tool_security('kanban_write') retorna read/low.

Impacto: segunda criação/comentário/heartbeat idêntico pode ser suprimido; listas ficam obsoletas após qualquer escrita no board; metadados públicos reportam read/low e eventos têm permission/risk ausentes. Fora de git, TestsGate não reconhece kanban_write como mudança. Contradiz etapa 043 que explicitamente requeria metadados novos (`plans/043-multiplex-kanban-tools.md:285`).

Correção: atualizar metadados e conjunto mutating aos nomes expostos; adicionar contrato de paridade inventário/security/dedup para evitar drift na próxima consolidação. Considerar metadados por ação para separar leitura/escrita de tools híbridas.

Regressão: kanban_write nunca cacheado; cada write invalida kanban_read; metadados write; TestsGate reconhece mudança fora de git.

### T08 — P1: ToolRegistry declara aprovação, mas dispatcher ignora

Evidência: `bauer/tool_router.py:1348` lê definição externa em tool_info; `:1564` resolve fn externo; `:1678` busca segurança apenas `_TOOL_SECURITY`, nunca `_ext_def`; `:1385` também ignora registry. Declaração no ToolDefinition afirma requires_approval deve ser aplicado.

Reprodução: registrar approval_audit(permission=system,risk=critical,requires_approval=True), router com `_llm_client=object()`, patch em llm_evaluate_tool; execução retorna 'ran', spy aprover 0 chamadas. tool_info requires_approval=True, tool_security read/low/approval=False.

Impacto: extensão parece crítica/exige aprovação no catálogo, porém executa sem o gate e logs/security discordam. Override de built-in ainda usa segurança do antigo handler, podendo elevar privilégios com metadados incorretos.

Correção: fonte única de definição efetiva incluindo fn, schema, segurança e operação de policy; usar metadata do registry para tudo. Decidir política de override explícito documentada.

Regressão: aprovação negada de extensão impede fn; metadata/eventos refletem extensão; override com risco aumentado utiliza segurança nova.

### T09 — P2: ToolRegistry ausente ou schema antigo em function calling nativo

Evidência: `bauer/tool_router.py:1335` faz união built-in + registry; tool_info e execute priorizam registry; `:1408` get_tool_schemas itera somente self._tools.

Reprodução: audit_extension aparece em available_tools e execute retorna 'done', mas não há schema nativo. Override de read_file mantém args/descrição original no schema, apesar de executar outro fn.

Impacto: extensão disponível ao bridge/CLI não pode ser descoberta pelo modelo nativo; overrides usam contrato de handler diferente, causando erros imprevisíveis por provider.

Correção: gerar schemas a partir de available_tools + tool_info efetivo. Sanitizar uma vez depois da união e verificar nomes únicos.

Regressão: cada nome disponível corresponde a um schema; deny/allowlist filtram ambos; override atualiza schema; nomes únicos.

### T10 — P2: schemas nativos contradizem argumentos aceitos

Evidência: `bauer/tool_router.py:1418` infere int/bool/dict e todo resto string; list[str] de social_post channels/media_paths/media_urls vira string (`:1145`). `:1428` qualquer 'obrigatorio' na descrição vira required global, inclusive 'obrigatorio para action=...'. `bauer/tools/messaging.py:208` exige channels list.

Reprodução: schema social_post.channels.type é string, router.execute social_post channels='dummy-id' rejeita antes de acessar provider. kanban_write.schema.required é ['action','task_id','child_id','reason','progress','comment'], inclusive para create. memory/todo/process/cronjob têm o mesmo erro de required condicionado. skill_manage.tags string vira uma única tag literal se modelo obedece schema.

Impacto: function calling nativo produz argumentos válidos no schema que a tool rejeita, ou inventa campos sem relação com a ação. Bridge recebe descrição original e tem contrato diferente.

Correção: schemas JSON explícitos como fonte de verdade, pelo menos tipos array/items e required realmente global. Condições por ação podem ficar na descrição quando providers não aceitam oneOf; handler continua defensivo, conforme decisão de plans/README.md sobre não impor validação rígida em execute.

Regressão: chamada mínima por cada ação válida contra schema e handler; channels e tags arrays; opcionais condicionais não globais; provider sanitization preserva tipos.

## Achados não classificados como bug neste relatório

- Interpretadores permitidos podem tocar outros paths: já rejeitado como by-design em plans/README.md; não reabri como vulnerabilidade nova.
- execute_code replayável mesmo escrevendo arquivos é decisão documentada e coberta por tests/test_tool_dedup.py, diferente do drift kanban_write.
- browser_navigate não usa url_safety e alcança localhost: browser local é necessário para testar apps; recomendar política configurável e roteamento seguro, mas não afirmar bug só por permitir loopback.
- close_browser_executor só encerra pool e não fecha Page/Browser/Playwright; nenhum caller encontrado. Candidato lifecycle, ainda sem reprodução real neste escopo.

## Ordem proposta

1. Contenção/policy e dry_run (T01–T06, T08), com regressões herméticas.
2. Drift kanban (T07), schemas/registry (T09–T10).
3. Gate de paridade catálogo → handler → segurança → efeito → policy → schema para consolidar listas divergentes.
4. Browser E2E real feito pelo root valida web/loop, mas não substitui testes negativos de tools isoladas.
