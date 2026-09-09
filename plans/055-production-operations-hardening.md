# Plan 055: Tornar a operação do Bauer segura, observável e recuperável

> **Instruções ao executor**: siga as etapas na ordem. Rode cada verificação indicada antes de avançar. Não altere contratos públicos sem teste de compatibilidade. Atualize a linha deste plano em `plans/README.md` quando terminar.
>
> **Drift check (primeiro comando)**:
> `git diff --stat 47f5bad..HEAD -- bauer/server.py bauer/config_loader.py bauer/core/runtime bauer/commands/runtime_cmd.py bauer/agent.py tests/`
> Se os trechos descritos em “Estado atual” não corresponderem ao código vivo, pare e reporte o drift em vez de adaptar o plano por suposição.

## Status

- **Prioridade**: P1
- **Esforço**: L
- **Risco**: MED
- **Depende de**: nenhum
- **Categoria**: segurança, confiabilidade, observabilidade, arquitetura
- **Planejado em**: commit `47f5bad`, 2026-09-08
- **Status**: DONE

## Por que isto importa

O Bauer já impede bind externo sem API key e o runtime agora grava seu estado compartilhado em SQLite com WAL. Ainda faltam as proteções que tornam isso operável fora de uma máquina única: uma configuração de implantação inequívoca, sinais úteis para o operador, cópias consistentes e restauração deliberada, e fronteiras de módulo que permitam evoluir o servidor e o agente sem concentrar riscos em dois arquivos muito grandes.

O objetivo não é embutir um serviço de nuvem no Bauer. É definir um contrato seguro para o processo FastAPI atrás de um proxy TLS, diagnosticar uma instância sem ler conteúdo privado, recuperar o estado operacional após falha e reduzir o acoplamento dos módulos centrais.

## Estado atual

- `bauer/config_loader.py:614-631` define `ServeSection`: bind local é o padrão, API key é opcional no uso local, `trusted_proxies` e `cors_origins` já são explícitos, e `enable_access_log` existe mas inicia desligado.
- `bauer/server.py:111-202` mantém `_Metrics` global em memória. Os contadores são reinicializados no boot, portanto não há agregação entre workers nem histograma de latência. `bauer/server.py:1251-1319` concentra middleware, access log e `/metrics` dentro de `create_app`.
- `bauer/server.py:575-2320` ainda monta o app, dependências, middleware e endpoints no mesmo módulo. `bauer/server_streaming.py`, `server_chat.py` e `server_openai.py` são precedentes corretos de extração por domínio.
- `bauer/core/runtime/state_store.py:68-96` cria `runtime_state.sqlite3` com `journal_mode=WAL`, `busy_timeout` e transações `BEGIN IMMEDIATE`; `budget_ledger.py` usa outro banco SQLite no mesmo root. Não há comando comum para snapshot, verificação de integridade ou restauração desses bancos.
- `bauer/agent.py` tem aproximadamente 4.282 linhas. Já delega voz, resposta e loop para `agent_voice.py`, `agent_response.py`, `agent_loop_driver.py` e `agent_loop_support.py`, mas ainda concentra montagem do prompt, protocolo de tools e a orquestração do turno. `run_one_turn()` em torno da linha 2189 é o contrato compartilhado por CLI e servidor.
- O kernel continua sendo a fonte de custódia: `AGENTS.md` determina que novos caminhos executáveis usem `run_governed()`/`continue_governed()` e que exceções a isso sejam registradas em `tests/test_arquitetura_custodia_kernel.py`.

Convenções obrigatórias:

- Configurações Pydantic são estritas (`extra="forbid"`); todo campo novo deve ser adicionado a `ServeSection`, documentado e coberto por configuração inválida.
- Erros em telemetria e auditoria são best-effort: registrar em DEBUG e não derrubar um turno. Segurança, snapshot e restauração, ao contrário, falham fechados e devolvem diagnóstico acionável.
- Testes usam `tmp_path` e `monkeypatch`; não devem tocar o `BAUER_HOME` real, providers reais nem a raiz do repositório.

## Comandos de verificação

| Objetivo | Comando | Resultado esperado |
|---|---|---|
| Ambiente | `uv sync --frozen --extra dev` | saída 0 |
| Testes focados | `uv run pytest tests/test_security_defaults.py tests/test_server_contract.py tests/test_runtime_snapshot.py tests/test_agent_contracts.py -q --tb=short` | todos passam |
| Suíte integral | `uv run pytest tests/ -q --tb=short` | todos passam |
| Tipos | `uv run mypy bauer/` | saída 0, sem ampliar overrides |
| Lint bloqueante | `uv run ruff check bauer/ --select E9,F63,F7,F82` | saída 0 |
| Lint amplo | `uv run ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303` | não piorar o baseline existente |
| Lock | `uv lock --check` | saída 0 |

## Escopo

**Em escopo**

- `bauer/config_loader.py`, `bauer/commands/serve_cmd.py`, `bauer/preflight.py` e documentação de implantação para o contrato de produção.
- `bauer/server.py` e novos módulos coesos em `bauer/server_*.py` para middleware, readiness e métricas.
- `bauer/core/runtime/state_store.py`, `budget_ledger.py`, novo módulo de snapshot e `bauer/commands/runtime_cmd.py`.
- `bauer/agent.py` e novos módulos `bauer/agent_*.py`, somente para extrações de responsabilidade já existente.
- Testes Python e atualização pontual de `AGENTS.md`/README se o contrato operacional mudar.

**Fora de escopo**

- Terminação TLS, gestão de certificados, Kubernetes, banco remoto ou serviço de backup externo. O Bauer deve documentar o proxy TLS; não tentar substituir a infraestrutura de borda.
- Backup de `auth.json`, keychain, tokens de provider ou credenciais. Snapshot operacional não pode copiar segredos silenciosamente.
- Reescrever o Kernel, mudar sua semântica de custódia, ou converter o agente em arquitetura de plugins neste plano.
- Alterar comportamento de voz, indexação semântica ou contratos do desktop.

## Fluxo Git

- Branch: `codex/production-operations-hardening`.
- Commits pequenos, no estilo existente: `feat(serve): ...`, `feat(runtime): ...`, `refactor(agent): ...`.
- Não enviar ou integrar na `master` sem a autorização do operador após os gates completos.

## Etapas

### 1. Formalizar o perfil de implantação exposta

Adicione uma opção estrita em `ServeSection` que distinga `local` de `reverse_proxy`. No perfil `reverse_proxy`, valide no boot e no preflight:

- `api_key` obrigatória;
- `cors_origins` não pode conter `*`;
- `trusted_proxies` não pode conter `*` e precisa conter ao menos um IP/CIDR;
- uma URL pública HTTPS explicitamente configurada é exigida para orientar o operador a terminar TLS fora do processo.

Mantenha o perfil `local` idêntico ao comportamento atual: bind em loopback e sem API key continua válido. Não infira ambiente de produção pelo hostname nem por variáveis de CI. Mostre, no painel de boot, o perfil selecionado, se auth está ativa e que TLS é responsabilidade do proxy, sem imprimir a chave.

Acrescente headers defensivos apenas para respostas HTTP do Bauer (por exemplo, `X-Content-Type-Options` e uma política de frame conservadora), tomando cuidado para não emitir HSTS quando o processo não sabe se a conexão externa foi HTTPS.

**Verifique**: crie/estenda `tests/test_security_defaults.py` para cobrir os quatro inválidos acima, um perfil de proxy válido e o perfil local legado. `uv run pytest tests/test_security_defaults.py tests/test_server_contract.py -q --tb=short` deve passar.

### 2. Extrair observabilidade de request e readiness

Crie `bauer/server_observability.py` para conter, por injeção de dependências:

- middleware que gera ou propaga um identificador de requisição validado, devolve-o no header de resposta e o inclui em cada access log;
- logging JSON estruturado opcional com método, rota normalizada, status, duração, request id e tipo de erro — nunca corpo, prompt, resposta, API key, session id ou argumentos de tools;
- métricas Prometheus de contagem por rota/status e histograma de duração, com labels de cardinalidade limitada. Não use modelo, sessão, usuário ou request id como label;
- `/readyz` autenticado, separado de `/health`: deve confirmar que o runtime root é gravável, que os bancos registrados passam `PRAGMA quick_check` e que a configuração foi carregada. Não faça chamada ao LLM/Ollama em readiness.

`server.py` deve ficar apenas com a composição e a passagem explícita das dependências. Preserve `/metrics`, seus nomes atuais e seus testes; acrescente as métricas novas de forma aditiva. Documente que métricas em memória são por processo: para `workers > 1`, o deployment deve expor cada worker ao coletor ou usar uma estratégia de agregação escolhida explicitamente; não prometa soma automática que o processo não realiza.

**Verifique**: em `tests/test_server_contract.py` ou novo `tests/test_server_observability.py`, teste request id gerado/propagado, redação do access log, rótulos limitados, `/health` sem readiness e `/readyz` com banco íntegro e banco indisponível. Rode os testes focados mais `uv run ruff check bauer/server.py bauer/server_observability.py --select E9,F63,F7,F82`.

### 3. Entregar snapshots consistentes do estado operacional

Crie `bauer/core/runtime/snapshot.py` e integre comandos sob o grupo existente `bauer runtime` em `bauer/commands/runtime_cmd.py`:

- `bauer runtime snapshot`: recebe `--root`, cria diretório de destino seguro, usa a API `sqlite3.Connection.backup()` para cópia consistente de `runtime_state.sqlite3` e `budget_ledger.sqlite3`, executa `PRAGMA integrity_check` no snapshot e grava manifesto JSON com versão, horário UTC, bancos incluídos, tamanho e hash SHA-256;
- `bauer runtime verify-snapshot`: verifica manifesto, hashes e `integrity_check`, sem modificar o snapshot;
- `bauer runtime restore`: exige `--from`, confirmação explícita e processo Bauer parado. Verifica o snapshot inteiro antes, copia para arquivos temporários no mesmo volume e só então substitui cada banco. Ao detectar PID ativo, lock ou falha de verificação, recusa sem sobrescrever nada.

Não faça descoberta por glob. Mantenha uma lista versionada de stores incluídos e exiba claramente os excluídos, em especial sessões, memória de decisão e credenciais. Permissões devem ser restritivas quando a plataforma suportar; em plataformas sem semântica POSIX, documente o requisito de ACL do diretório de destino em vez de alegar proteção inexistente.

**Verifique**: crie `tests/test_runtime_snapshot.py` com: snapshot de banco em WAL enquanto há escrita, checksum alterado, banco corrompido, restore sem confirmação, restore com processo/lock ativo e restauração atômica de dois bancos. Rode `uv run pytest tests/test_runtime_snapshot.py tests/test_state_store.py tests/test_runtime_managers.py -q --tb=short`.

### 4. Congelar contratos antes do próximo corte arquitetural

Adicione testes de caracterização antes de mover código:

- `tests/test_agent_contracts.py` deve cobrir o modo bridge e native da montagem de prompt, parsing de uma tool call válida/inválida e a preservação de `run_one_turn` como API importável;
- `tests/test_server_contract.py` deve fixar imports/reexports de streaming, chat e OpenAI, além de contratos HTTP já públicos;
- mantenha `tests/test_arquitetura_custodia_kernel.py` como catraca: nenhum módulo novo pode chamar conclusão/falha fora dos permitidos sem justificativa.

**Verifique**: `uv run pytest tests/test_agent_contracts.py tests/test_server_contract.py tests/test_arquitetura_custodia_kernel.py -q --tb=short` deve passar antes de qualquer movimentação.

### 5. Extrair limites coesos de `agent.py` e `server.py`

Faça duas extrações pequenas, com reexports temporários onde callers internos dependem de nomes privados:

1. Mova de `agent.py` para `agent_tool_protocol.py` apenas as funções de parse/normalização do protocolo bridge e a compactação/formatação de resultado de tool. Elas são o bloco em torno de `_try_parse_tool`, `_try_parse_tools_batch`, `_head_tail`, `_compress_tool_result_inline` e `_format_tool_display`. `run_one_turn` permanece a fachada que escolhe bridge/native e executa tools.
2. Após a etapa 2, deixe métricas, limiter, parsing de proxy e middleware em módulos `server_*.py`; `create_app` apenas os configura. Não mova endpoints funcionais já isolados em `server_chat.py`, `server_openai.py` ou `server_streaming.py` de novo.

Não altere o formato do prompt, os limites de tool turn, eventos, orçamento, aprovação ou a diferença Kernel `execute()` versus `admit()`. Meça o resultado: reduzir `agent.py` abaixo de 3.500 linhas e `server.py` abaixo de 1.700 linhas sem aumentar duplicação é meta de aceitação, não justificativa para cortes de comportamento.

**Verifique**: rode os testes de caracterização a cada extração, depois `uv run pytest tests/test_runtime_event_bus.py tests/test_kernel.py tests/test_kernel_serve_stream.py -q --tb=short`.

### 6. Validar o fluxo completo e publicar o guia operacional

Atualize README ou documentação operacional com:

- exemplo seguro de `serve.deployment_mode: reverse_proxy`, sem segredo;
- checklist do proxy TLS, CORS e CIDR de proxy confiável;
- significado de `/health`, `/readyz` e `/metrics`;
- cronograma sugerido de snapshot, como verificar e como fazer restore com o servidor parado; e os stores propositalmente excluídos.

Execute todos os comandos da tabela “Comandos de verificação”, além de `git diff --check`. Confira manualmente que nenhum segredo, banco SQLite, snapshot ou arquivo de runtime entrou no diff.

## Critérios de pronto

- Configuração exposta insegura falha no boot com mensagem clara; local continua simples e compatível.
- Cada request observável tem identificador, duração e resultado sem vazar conteúdo sensível; readiness mede apenas dependências locais determinísticas.
- O operador consegue criar, verificar e restaurar um snapshot consistente dos dois bancos de runtime sem sobrescrever estado em condição insegura.
- `agent.py` e `server.py` perdem responsabilidade coesa, preservando contratos e regras de custódia cobertas por testes.
- Suíte, mypy, lock e lint bloqueante passam; o lint amplo não piora seu baseline.

## Condições de parada

- O perfil de proxy exigir inferir TLS ou topologia de rede que o Bauer não consegue observar. Pare e peça que o contrato de deployment seja definido, em vez de inventar detecção insegura.
- Snapshot precisar copiar credenciais, ou a restauração não puder garantir que o processo escritor está parado. Não implemente um backup aparentemente completo, porém inseguro.
- Uma extração exigir alterar o Kernel, o contrato `/stream`, ou regras de aprovação/budget. Pare e proponha um plano separado de migração de custódia.
