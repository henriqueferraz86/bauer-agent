# Reconciliação dos status históricos — 2026-10-13

O pedido de continuar o loop motivou uma verificação final dos critérios
originais da auditoria atual e dos TODOs registrados no índice de planos.
As três frentes confirmaram fechamento dos 17 itens funcionais autorizados.
O plano 023 de julho continha nove TODOs para correções já implementadas.

## Critérios e evidências do plano 023

| Item | Evidência atual | Verificação local |
| --- | --- | --- |
| 19 | AGENTS.md contém contrato/layout/verificação; CLAUDE.md alinha Ruff 0.17.0 | Leitura dos arquivos; AGENTS tem 207 linhas; comando corrigido executado offline |
| DOCS | Diretório docs/Nova pasta ausente; links locais atuais README/CHANGELOG resolvem | Verificação de existência de todos os alvos Markdown relativos encontrados, ignorando URLs e anchors |
| 09 | DecisionMemory persiste embedding e backfilla ao trocar backend | TestPersistedEmbeddings: 3 testes |
| 17 | memory_context mantém cache de stores por caminho com lock | TestStoreInstanceCache: 2 testes |
| 20 | /v1 é def; transcribe_audio roda em anyio.to_thread.run_sync | Leitura das rotas e testes transcribe_success/transcribe_cleans_up_temp_file com STT controlado |
| 16 | Scheduler aplica deadline durante a execução | max_runtime_interrupts_stuck_adapter e fast_adapter_within_deadline_completes |
| 08 | JsonlStateStore é fachada de SQLite transacional | test_state_store, incluindo threads/processos e migração |
| 11 | Budget reserva estimativa em SQLite; vagas admitem em transação | test_kernel_parallel_runs e dois casos budget reserves_once/per_run_cost |
| 18 | Registry formal se chama RuntimeAgentRegistry; legado AgentRegistry separado | Leitura das classes; formal_agent_registry_loads_versioned_specs e run_manager_creates_run_from_agent_registry |

## Comandos e resultados

Todos via ambiente frozen/offline, sem provider real ou credenciais.

```bash
UV_OFFLINE=1 uv sync --frozen --extra dev
UV_OFFLINE=1 uv run --frozen pytest tests/test_dev_env_parity.py -o addopts='' -q --tb=short
UV_OFFLINE=1 uv run --frozen pytest \
  tests/test_decision_memory.py::TestPersistedEmbeddings \
  tests/test_memory_context.py::TestStoreInstanceCache \
  tests/test_state_store.py \
  tests/test_runtime_scheduler.py::test_max_runtime_interrupts_stuck_adapter \
  tests/test_runtime_scheduler.py::test_fast_adapter_within_deadline_completes \
  tests/test_kernel_parallel_runs.py \
  tests/test_runtime_agent_registry.py::test_formal_agent_registry_loads_versioned_specs \
  tests/test_runtime_agent_registry.py::test_run_manager_creates_run_from_agent_registry \
  tests/test_server_extended.py::test_transcribe_success \
  tests/test_server_extended.py::test_transcribe_cleans_up_temp_file \
  -o addopts='-n 2' -q --tb=short
UV_OFFLINE=1 uv run --frozen pytest tests/test_policy_engine.py \
  -k 'reservation or per_run_cost' -o addopts='' -q --tb=short
UV_OFFLINE=1 uv run --frozen pytest \
  tests/test_policy_engine.py::test_runtime_budget_migrates_audit_and_reserves_once \
  -o addopts='' -q --tb=short
```

Paridade dev: 11 passed. Seleção histórica: **27 passed**, 2 avisos existentes
on_event; [log](validation/historical-status.log). Dois casos adicionais de
budget: 1 passed em cada comando, somando **29 regressões** para os status.
Os testes de policy_engine selecionados exercitam somente contabilidade.

Fechamento read-only pelo time: kernel_runtime, 191 testes; tools_catalog,
75 testes e ambos gates Ruff; web_ui, mapping de seis critérios com as
regressões e cinco provas Chromium já aprovadas. Sem novo finding reproduzível.
A suíte global anterior permanece aplicável: 7.982 passed/16 skipped; produção,
frontend, testes e dependências não foram modificados nesta rodada documental.

A revisão encontrou um comando inválido em CLAUDE.md: `uv run ruff check ...`
falhava com `Failed to spawn: ruff`, porque Ruff não está no extra dev. O guia
agora usa `uv run --with ruff==0.17.0 ruff check ...`, em paridade com
AGENTS.md/README/CI. Ambos gates passaram com pacote já disponível no cache,
sem alterar dependências do produto. A nota da matriz CI foi conferida e
corrigida: Ubuntu e Windows/Python 3.11 e 3.12; validação local somente Linux.

## Arquivos desta rodada

- `CLAUDE.md`: comando Ruff alinhado à versão do CI.
- `plans/023-auditoria-completa-2026-07.md`: nove status encerrados com evidência;
  nota distingue histórico e estado atual.
- `plans/README.md`: nota da reconciliação de status.
- Nesta pasta: `SPEC.md`, `ARCHITECTURE.md`, `BACKLOG.md`, `REVIEW.md`,
  `VALIDATION.md` e este novo `STATUS_RECONCILIATION.md` registram o escopo,
  responsabilidades, critérios, revisão e resultados.
- Log local `validation/historical-status.log` contém os 27 casos principais.

## Revisão final

**APROVADA por tools_catalog**, com 11 testes independentes, oito links locais
README/CHANGELOG e diff-check aprovados. Nota de CI e comando CLAUDE/Ruff
corrigidos; nenhum finding restante. A revisão foi concluída antes de encerrar
a tarefa no backlog.

## Limites e pendências

Detalhes históricos do plano permanecem intactos; a tabela de status reflete
a revalidação. Nenhum item de segurança foi executado nem encerrado.
Plano 017 é design bloqueado por decisão de escopo; 057 é expansão de features.
A colisão de numeração dos planos e cutovers de arquitetura não autorizam
reescritas nesta manutenção. Windows, áudio real/providers reais seguem sem
validação adicional. Nenhum commit/push, rede ou configuração sensível.
