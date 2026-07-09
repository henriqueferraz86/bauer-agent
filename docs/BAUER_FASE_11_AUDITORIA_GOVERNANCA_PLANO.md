# Fase 11 — Auditoria e Governança do Bauer

**Versão:** 1.0  
**Status:** Plano de implementação  
**Objetivo:** transformar a camada de auditoria do Bauer em features reais, operacionais e testáveis.

---

## 1. Visão da Fase 11

Depois que o Bauer já consegue executar agents, skills, policies, approvals, scheduler, workers e runtime adapters, o próximo passo é garantir que ele esteja fazendo tudo do jeito certo.

A Fase 11 cria a camada de:

```text
auditoria
governança
métricas
relatórios
score de qualidade
auditoria arquitetural
benchmark interno
evolução de skills baseada em uso
```

A pergunta principal deixa de ser:

```text
O Bauer consegue executar?
```

E passa a ser:

```text
O Bauer executou corretamente, com segurança, rastreabilidade e qualidade?
```

---

## 2. Resultado esperado da Fase 11

Ao final desta fase, o Bauer deve conseguir:

```text
1. Gerar relatório das últimas runs.
2. Mostrar taxa de sucesso, falhas, custos e aprovações.
3. Auditar uma run específica.
4. Dar nota automática para cada run importante.
5. Identificar gargalos e falhas recorrentes.
6. Verificar se uma mudança respeita a arquitetura.
7. Rodar cenários reais de benchmark.
8. Sugerir novas skills com base em padrões repetidos.
9. Expor tudo isso via CLI e dashboard.
```

---

## 3. Estrutura recomendada

```text
bauer/
└── core/
    ├── audit/
    │   ├── __init__.py
    │   ├── report.py
    │   ├── run_auditor.py
    │   ├── architecture_auditor.py
    │   ├── score.py
    │   ├── benchmark.py
    │   ├── skill_insights.py
    │   └── schemas.py
    ├── observability/
    ├── events/
    ├── runtime/
    ├── policy/
    └── skills/
```

Comandos novos esperados:

```bash
bauer audit report
bauer audit run <run_id>
bauer audit architecture
bauer audit score <run_id>
bauer benchmark run
bauer skills insights
```

---

# Sprint 25 — Comando `bauer audit report`

## Objetivo

Criar o primeiro relatório geral de auditoria do Bauer.

Esse comando deve resumir o estado recente do runtime usando runs, eventos, approvals, policies e skills.

## Comando esperado

```bash
bauer audit report
```

Com opções:

```bash
bauer audit report --last 24h
bauer audit report --last 7d
bauer audit report --format table
bauer audit report --format json
bauer audit report --output reports/audit-report.json
```

## Dados mínimos do relatório

```text
runs_total
runs_completed
runs_failed
runs_waiting_approval
success_rate
average_duration
approvals_pending
policy_denied_total
most_used_skills
most_failed_skills
most_used_agents
runtime_adapters_used
estimated_cost
top_errors
```

## Exemplo de saída

```text
Bauer Audit Report — Últimas 24h

Runs:
- Total: 18
- Completed: 14
- Failed: 3
- Waiting approval: 1
- Success rate: 77.7%

Policy:
- Allow: 31
- Ask: 7
- Deny: 2
- Pending approvals: 1

Skills:
- Mais usadas:
  1. filesystem.read
  2. shell.execute
  3. docker.diagnose

Falhas principais:
- shell timeout: 2
- missing file: 1

Recomendação:
- Revisar skill shell.execute por falhas recorrentes.
```

## Passo a passo

1. Criar `bauer/core/audit/schemas.py`.
2. Criar `bauer/core/audit/report.py`.
3. Ler dados de:
   - RunManager
   - EventStore
   - ApprovalManager
   - SkillRegistry
   - PolicyEngine logs/eventos
4. Agregar métricas básicas.
5. Criar saída em tabela.
6. Criar saída em JSON.
7. Adicionar comando no CLI.
8. Criar testes unitários com dados fake.
9. Criar teste de integração com runs reais, se possível.

## Critérios de aceite

```text
[ ] `bauer audit report` executa sem erro.
[ ] Mostra métricas básicas de runs.
[ ] Mostra approvals pendentes.
[ ] Mostra policies allow/ask/deny.
[ ] Mostra skills mais usadas.
[ ] Suporta formato JSON.
[ ] Testes passam.
```

## Prompt para Codex

```text
Estamos na Fase 11, Sprint 25 do Bauer.

Objetivo: implementar o comando `bauer audit report`.

Crie uma camada em `bauer/core/audit/` capaz de agregar dados de runs, eventos, approvals, policy decisions e skills.

Requisitos:
- Criar `bauer/core/audit/schemas.py`
- Criar `bauer/core/audit/report.py`
- Criar comando CLI `bauer audit report`
- Suportar `--last`, `--format table`, `--format json` e `--output`
- Não alterar a lógica do runtime
- Não alterar execução de agents/skills
- Usar dados já persistidos pelo Bauer
- Criar testes

Critérios:
- O comando mostra total de runs, sucesso, falhas, approvals, policies e skills mais usadas
- Saída JSON funciona
- Testes existentes continuam passando
```

---

# Sprint 26 — Relatório estruturado por run

## Objetivo

Criar auditoria detalhada de uma execução específica.

## Comando esperado

```bash
bauer audit run <run_id>
```

Opções:

```bash
bauer audit run <run_id> --format json
bauer audit run <run_id> --include-events
bauer audit run <run_id> --include-tools
bauer audit run <run_id> --include-policy
```

## Estrutura esperada

```yaml
run_id: run-123
status: completed
agent_id: dev-agent
runtime_adapter: agno
started_at: ...
finished_at: ...

request:
  prompt: ...

plan:
  steps: []

execution:
  skills_used: []
  tools_used: []
  commands_executed: []
  files_changed: []

policy:
  decisions: []

events:
  total: 42
  critical: []

validation:
  tests_ran: true
  tests_passed: true

final_answer:
  summary: ...
```

## Passo a passo

1. Criar `bauer/core/audit/run_auditor.py`.
2. Buscar run por `run_id`.
3. Buscar eventos relacionados.
4. Extrair skills usadas.
5. Extrair tool calls.
6. Extrair decisões de policy.
7. Extrair approvals.
8. Extrair arquivos alterados se os eventos registrarem isso.
9. Gerar relatório estruturado.
10. Expor via CLI.

## Critérios de aceite

```text
[ ] `bauer audit run <run_id>` funciona.
[ ] Mostra status, duração, agent e adapter.
[ ] Mostra skills/tools usadas.
[ ] Mostra policy decisions.
[ ] Mostra eventos principais.
[ ] Suporta JSON.
[ ] Ajuda a entender o que aconteceu na run.
```

## Prompt para Codex

```text
Implemente a Sprint 26 da Fase 11: relatório estruturado por run.

Crie `bauer/core/audit/run_auditor.py` e o comando `bauer audit run <run_id>`.

O comando deve carregar a run, eventos, tool calls, skills usadas, approvals e policy decisions relacionados ao run_id.

Não mude a lógica de execução.
Apenas leia dados existentes e gere relatório.

Adicionar testes com uma run fake e eventos fake.
```

---

# Sprint 27 — Score automático das runs

## Objetivo

Criar uma nota simples para medir qualidade de execução.

## Comando esperado

```bash
bauer audit score <run_id>
```

## Critérios de score

Cada run recebe nota de 0 a 5:

```text
+1 objetivo concluído
+1 plano claro registrado
+1 execução sem erro crítico
+1 validação/testes executados quando aplicável
+1 resumo final claro
```

## Modelo

```python
class RunScore:
    run_id: str
    score: int
    max_score: int = 5
    reasons: list[str]
    warnings: list[str]
```

## Exemplo de saída

```text
Run Score: 4/5

Pontos positivos:
- Objetivo concluído
- Execução completada
- Eventos registrados
- Resumo final encontrado

Ponto perdido:
- Não há evidência de teste/build executado
```

## Passo a passo

1. Criar `bauer/core/audit/score.py`.
2. Implementar heurística inicial.
3. Usar dados do `RunAuditor`.
4. Expor comando CLI.
5. Adicionar score no `audit run`.
6. Salvar score como evento opcional:
   - `audit.score.generated`
7. Criar testes.

## Critérios de aceite

```text
[ ] Toda run pode receber score.
[ ] Score explica os motivos.
[ ] Score não depende de LLM.
[ ] Score aparece no audit run.
[ ] Testes passam.
```

## Prompt para Codex

```text
Implemente a Sprint 27 da Fase 11: score automático das runs.

Criar `bauer/core/audit/score.py` com uma heurística simples de 0 a 5.

O score deve considerar:
- status completed
- existência de plano
- ausência de erro crítico
- evidência de teste/build quando aplicável
- resumo final claro

Adicionar comando `bauer audit score <run_id>` e integrar o score ao `bauer audit run`.

Não usar LLM nesta versão.
Criar testes.
```

---

# Sprint 28 — Dashboard de métricas de auditoria

## Objetivo

Levar os dados de auditoria para o frontend do `bauer serve`.

## Telas mínimas

```text
/audit
/audit/runs
/audit/runs/:run_id
/audit/approvals
/audit/skills
```

## Widgets da tela `/audit`

```text
Runs hoje
Taxa de sucesso
Runs com falha
Aprovações pendentes
Policies negadas
Skills mais usadas
Custo estimado
Últimos erros
```

## Endpoints esperados

```text
GET /audit/report
GET /audit/runs/{run_id}
GET /audit/runs/{run_id}/score
GET /audit/skills/insights
```

## Passo a passo

1. Criar endpoints no server.
2. Reutilizar `AuditReport`.
3. Reutilizar `RunAuditor`.
4. Reutilizar `RunScore`.
5. Criar página `/audit`.
6. Criar detalhes da run.
7. Separar visualmente:
   - resposta final
   - eventos
   - tool calls
   - policy decisions
   - score
8. Corrigir renderização de Markdown se ainda estiver ruim.
9. Adicionar testes ou snapshots, se existir estrutura.

## Critérios de aceite

```text
[ ] Dashboard mostra métricas principais.
[ ] É possível abrir uma run específica.
[ ] Score aparece visualmente.
[ ] Eventos não ficam misturados com resposta final.
[ ] Approvals aparecem com clareza.
[ ] Não altera execução do runtime.
```

## Prompt para Codex

```text
Implemente a Sprint 28 da Fase 11: dashboard de métricas de auditoria.

Criar endpoints:
- GET /audit/report
- GET /audit/runs/{run_id}
- GET /audit/runs/{run_id}/score

Criar tela de auditoria no frontend do bauer serve.

Objetivos:
- Mostrar métricas gerais
- Mostrar lista de runs
- Mostrar detalhes de uma run
- Separar resposta final, eventos, tools e policies
- Melhorar renderização de Markdown/blocos de código se necessário

Não alterar runtime, agents, skills ou policies.
```

---

# Sprint 29 — Auditoria arquitetural automática

## Objetivo

Criar uma feature que avalia se mudanças recentes respeitam a arquitetura do Bauer.

## Comando esperado

```bash
bauer audit architecture
```

Opções:

```bash
bauer audit architecture --since main
bauer audit architecture --changed-files
bauer audit architecture --format json
```

## Checagens iniciais

```text
1. Agno não pode ser chamado fora do RuntimeAdapter.
2. Frontend não deve conter lógica de runtime.
3. Skills precisam ter manifesto.
4. Tools sensíveis devem passar pelo Policy Engine.
5. Execuções devem gerar eventos.
6. Novos módulos precisam ter testes.
7. Core não deve depender de skill específica.
8. Skill não deve alterar policy diretamente.
```

## Saída esperada

```text
Bauer Architecture Audit

Status: approved_with_warnings

Warnings:
- Arquivo X chama adapter Agno diretamente.
- Skill Y não possui manifesto completo.

Critical:
- Nenhum

Recomendações:
- Mover chamada Agno para AgnoRuntimeAdapter.
- Validar manifesto no SkillRegistry.
```

## Passo a passo

1. Criar `bauer/core/audit/architecture_auditor.py`.
2. Implementar análise estática simples por padrões.
3. Usar `git diff --name-only` quando disponível.
4. Criar regras iniciais.
5. Expor comando CLI.
6. Gerar saída table/json.
7. Criar testes com arquivos fake.

## Critérios de aceite

```text
[ ] Comando roda localmente.
[ ] Detecta chamadas indevidas ao Agno.
[ ] Detecta skills sem manifesto.
[ ] Detecta possíveis bypasses de policy.
[ ] Não bloqueia release ainda, apenas alerta.
[ ] Suporta JSON.
```

## Prompt para Codex

```text
Implemente a Sprint 29 da Fase 11: auditoria arquitetural automática.

Criar `bauer/core/audit/architecture_auditor.py` e comando `bauer audit architecture`.

O comando deve fazer checagens estáticas simples:
- Agno só pode aparecer dentro do adapter Agno ou docs/testes
- frontend não deve importar runtime core diretamente
- skills devem ter manifesto
- tools sensíveis devem passar por policy
- novos módulos devem ter testes quando aplicável

Não precisa ser perfeito.
A versão inicial deve alertar, não bloquear.
Criar testes.
```

---

# Sprint 30 — Benchmark interno do Bauer

## Objetivo

Criar uma suíte de cenários reais para testar o Bauer de ponta a ponta.

## Comando esperado

```bash
bauer benchmark run
```

Opções:

```bash
bauer benchmark run --scenario docker-diagnosis
bauer benchmark run --scenario mini-api
bauer benchmark run --scenario windows-control
bauer benchmark run --all
bauer benchmark report
```

## Cenários iniciais

### 1. Diagnóstico Docker

Valida:

```text
diagnóstico técnico
uso de shell
policy
eventos
resumo final
```

### 2. Criar mini API

Valida:

```text
criação de projeto
edição de arquivos
testes
README
resumo final
```

### 3. Controlar Windows com aprovação

Valida:

```text
OS skill
approval
policy
audit log
```

### 4. Site de barbearia

Valida:

```text
leitura de spec
frontend generation
workspace isolation
output formatado
```

## Estrutura recomendada

```text
benchmarks/
├── docker-diagnosis.yaml
├── mini-api.yaml
├── windows-control.yaml
└── site-barbearia.yaml
```

## Exemplo de benchmark

```yaml
id: mini-api
name: Criar mini API FastAPI
prompt: |
  Crie uma mini API FastAPI com endpoint /health, README e testes.
expected:
  files:
    - app/main.py
    - README.md
  commands:
    - pytest
  min_score: 4
```

## Passo a passo

1. Criar `bauer/core/audit/benchmark.py`.
2. Criar diretório `benchmarks/`.
3. Criar schema de benchmark.
4. Implementar runner.
5. Cada cenário cria uma run.
6. Ao final, usa `RunScore`.
7. Gerar relatório.
8. Criar comando CLI.

## Critérios de aceite

```text
[ ] `bauer benchmark run --all` funciona.
[ ] Cada benchmark gera run_id.
[ ] Cada benchmark gera score.
[ ] Relatório mostra aprovado/reprovado.
[ ] Dá para repetir os cenários.
```

## Prompt para Codex

```text
Implemente a Sprint 30 da Fase 11: benchmark interno do Bauer.

Criar comando `bauer benchmark run`.

Criar suporte a cenários YAML em `benchmarks/`.

Cada cenário deve definir:
- id
- name
- prompt
- expected files
- expected commands ou expected events, quando aplicável
- min_score

O runner deve executar o prompt via runtime atual, capturar run_id, gerar score e produzir relatório.

Comece com cenários:
- mini-api
- site-barbearia
- docker-diagnosis
- windows-control, se estiver em Windows

Criar testes.
```

---

# Sprint 31 — Skill Insights

## Objetivo

Detectar padrões de uso e sugerir melhorias ou novas skills.

## Comando esperado

```bash
bauer skills insights
```

Opções:

```bash
bauer skills insights --last 7d
bauer skills insights --suggest-new
bauer skills insights --format json
```

## Insights esperados

```text
skills mais usadas
skills com mais falha
skills lentas
skills nunca usadas
sequências repetidas de tools
candidatas a virar skill
```

## Exemplo de saída

```text
Skill Insights — Últimos 7 dias

Mais usadas:
1. shell.execute — 42 usos
2. filesystem.write — 31 usos
3. docker.logs — 18 usos

Maior taxa de falha:
1. docker.diagnose — 22%
2. browser.navigate — 18%

Sugestão:
Criar skill docker.diagnose_compose porque a sequência:
- docker ps
- docker compose logs
- verificar portas
- verificar healthcheck
apareceu 5 vezes em runs concluídas.
```

## Passo a passo

1. Criar `bauer/core/audit/skill_insights.py`.
2. Ler eventos de skills e tools.
3. Agregar frequência e falhas.
4. Detectar sequências repetidas simples.
5. Gerar sugestões.
6. Expor comando CLI.
7. Expor endpoint para dashboard, opcional.
8. Criar testes com eventos fake.

## Critérios de aceite

```text
[ ] Mostra skills mais usadas.
[ ] Mostra skills com maior falha.
[ ] Mostra skills lentas, se houver duração.
[ ] Sugere pelo menos um padrão repetido.
[ ] Não cria skill automaticamente.
[ ] Exige aprovação humana para qualquer criação futura.
```

## Prompt para Codex

```text
Implemente a Sprint 31 da Fase 11: Skill Insights.

Criar `bauer/core/audit/skill_insights.py` e comando `bauer skills insights`.

A feature deve analisar eventos de runs, skills e tools para mostrar:
- skills mais usadas
- skills com mais falhas
- skills lentas
- sequências repetidas de tools
- candidatas a virar novas skills

Importante:
- Não criar skills automaticamente
- Apenas sugerir
- Criar testes com eventos fake
```

---

# Sprint 32 — Revisão semanal automática

## Objetivo

Criar um relatório semanal que resume evolução, falhas e recomendações.

## Comando esperado

```bash
bauer audit weekly
```

Opções:

```bash
bauer audit weekly --last 7d
bauer audit weekly --output reports/weekly.md
```

## Conteúdo esperado

```text
Resumo da semana
Runs importantes
Taxa de sucesso
Falhas recorrentes
Skills mais usadas
Skills candidatas
Aprovações pendentes
Custo estimado
Riscos
Próximas ações recomendadas
```

## Passo a passo

1. Criar gerador Markdown.
2. Reutilizar `AuditReport`.
3. Reutilizar `SkillInsights`.
4. Reutilizar `RunScore`.
5. Gerar arquivo em `reports/`.
6. Opcional: agendar via scheduler semanal.

## Critérios de aceite

```text
[ ] `bauer audit weekly` gera Markdown.
[ ] Relatório é legível.
[ ] Inclui recomendações.
[ ] Pode ser salvo em arquivo.
[ ] Pode ser rodado manualmente.
```

## Prompt para Codex

```text
Implemente a Sprint 32 da Fase 11: revisão semanal automática.

Criar comando `bauer audit weekly`.

O comando deve gerar um relatório Markdown com:
- resumo das runs
- taxa de sucesso
- principais falhas
- approvals pendentes
- skills mais usadas
- skills candidatas
- custos estimados
- riscos
- próximas ações recomendadas

Salvar opcionalmente com `--output`.

Reutilizar as camadas criadas nas sprints anteriores.
Criar testes.
```

---

# 4. Ordem recomendada de implementação

Não pule direto para dashboard.

A ordem ideal é:

```text
Sprint 25 — audit report
Sprint 26 — audit run
Sprint 27 — run score
Sprint 29 — architecture audit
Sprint 30 — benchmark
Sprint 31 — skill insights
Sprint 32 — weekly report
Sprint 28 — dashboard
```

Motivo:

```text
Primeiro cria dados e comandos confiáveis.
Depois leva para interface.
```

Se fizer frontend antes, ele vira enfeite.

---

# 5. MVP mínimo da Fase 11

Se quiser uma versão enxuta, faça só:

```text
[ ] bauer audit report
[ ] bauer audit run <run_id>
[ ] bauer audit score <run_id>
[ ] bauer audit architecture
[ ] bauer benchmark run --all
```

Com isso, você já tem auditoria operacional real.

---

# 6. Critério para considerar Fase 11 concluída

A Fase 11 está concluída quando:

```text
[ ] É possível auditar uma run individual.
[ ] É possível gerar relatório geral.
[ ] É possível calcular score de runs.
[ ] É possível detectar falhas recorrentes.
[ ] É possível verificar riscos arquiteturais.
[ ] É possível rodar benchmark interno.
[ ] É possível sugerir novas skills com base em uso.
[ ] O dashboard mostra pelo menos parte desses dados.
[ ] Tudo tem testes.
```

---

# 7. Release sugerida

Ao finalizar:

```bash
git checkout main
git merge feature/fase-11-auditoria-governanca
git tag v0.2.0-audit-governance
git push origin main --tags
```

---

# 8. Decisão estratégica

A Fase 11 é o que transforma o Bauer de:

```text
plataforma que executa tarefas
```

para:

```text
plataforma que executa, explica, mede, audita e melhora.
```

Esse é o salto de confiabilidade.

Sem essa fase, o Bauer pode parecer poderoso, mas difícil de confiar.

Com essa fase, o Bauer começa a parecer uma plataforma séria.
