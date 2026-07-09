# Plano Completo — Bauer Agent → Bauer Agent Runtime → Bauer OS

**Versão:** 1.0  
**Data:** 2026-07-07  
**Objetivo:** transformar o Bauer Agent em uma plataforma de execução, governança e experiência para agentes autônomos, usando o Agno como uma das peças centrais do runtime.

---

## 0. Resumo na lata

O Bauer não precisa virar “mais um framework de agente”.  
O melhor caminho é este:

```text
Bauer OS
└── Bauer Agent Runtime
    ├── Runtime Adapter: Agno
    ├── Policy Engine
    ├── Skill Registry
    ├── Agent Registry
    ├── Event Bus
    ├── Scheduler
    ├── Memory Layer
    ├── Observability
    └── Execution Backends
        ├── Local process
        ├── Docker/container
        ├── Windows skill adapters
        ├── Linux/macOS skill adapters
        └── Cloud/runtime remoto
```

A ideia principal:

```text
Agno executa.
Bauer governa.
Skills dão capacidades.
Policy Engine limita.
Bauer OS vira a experiência.
```

O Bauer Agent atual já tem muita base boa. A virada de chave é separar claramente:

1. **Agent** — quem pensa e executa uma tarefa.
2. **Runtime** — quem mantém agentes rodando, agenda, controla estado, aplica política, observa e registra.
3. **OS/Experience** — interface principal onde o usuário gerencia agentes, skills, permissões, automações e execuções.

---

## 1. Diagnóstico do Bauer atual

Pelo estado público do repositório, o Bauer já tem sinais fortes de runtime:

- CLI com modos `bauer chat`, `bauer agent` e `bauer agent run`.
- Agents especializados definidos em `agents.yaml`.
- Memória persistente e busca em sessões.
- Orquestrador multi-passo com DAG.
- `bauer serve` com API HTTP, Web UI, sessões e streaming.
- Gateway para Telegram, Discord, Slack e WebSocket.
- Skills hub com list/search/install/stats.
- Tools para arquivos, comandos, browser, código, memória, canais, social posting, voz, multimodal e MCP.
- Métricas Prometheus.
- Docker e instalação multiplataforma.
- Safe mode, allowlist, confirmação de comandos e rate limit.

Isso significa que a base já existe.  
O que falta é transformar essas peças em **contratos formais de runtime**.

Hoje o Bauer parece um agente muito completo.  
O próximo passo é ele virar uma plataforma onde qualquer agente, skill ou backend roda sob as mesmas regras.

---

## 2. Arquitetura-alvo

### 2.1 Visão de camadas

```text
┌──────────────────────────────────────────────┐
│ Bauer OS                                     │
│ Interface, dashboard, voice, desktop, CLI     │
├──────────────────────────────────────────────┤
│ Bauer Agent Runtime                          │
│ Policies, skills, agents, events, scheduler   │
├──────────────────────────────────────────────┤
│ Runtime Adapters                             │
│ Agno, native Bauer, future runtimes           │
├──────────────────────────────────────────────┤
│ Execution Backends                           │
│ Process, Docker, Windows, Linux, macOS, cloud │
├──────────────────────────────────────────────┤
│ Tools / Skills / MCP / APIs                  │
│ Browser, shell, filesystem, apps, services    │
└──────────────────────────────────────────────┘
```

### 2.2 Papel do Agno

O Agno entra como **runtime adapter primário**.

Não faça o Bauer depender diretamente do Agno em todos os lugares.  
Crie uma interface interna:

```python
class RuntimeAdapter:
    def create_agent(self, spec): ...
    def run_agent(self, request): ...
    def stream_agent(self, request): ...
    def stop_run(self, run_id): ...
    def get_run(self, run_id): ...
    def list_sessions(self): ...
    def get_trace(self, run_id): ...
```

Depois implemente:

```text
BauerNativeRuntimeAdapter
AgnoRuntimeAdapter
FutureRuntimeAdapter
```

Assim o Bauer não vira refém do Agno.  
O Agno vira o primeiro motor forte.

---

## 3. Princípios arquiteturais

### P1 — Bauer governa, não só executa

O Bauer deve decidir:

- Quem pode executar.
- O que pode executar.
- Com qual modelo.
- Com quais tools.
- Com qual orçamento.
- Com qual nível de autonomia.
- Quando precisa de aprovação humana.
- Como registrar, reverter ou bloquear.

### P2 — Skill é unidade de capacidade e permissão

Nunca dê “permissão total ao agente”.  
Dê permissões para skills.

Exemplo:

```yaml
skill:
  id: windows.control_panel
  capabilities:
    - os.windows.open_control_panel
  permissions:
    - os.open_app
  risk_level: low
```

Outro exemplo:

```yaml
skill:
  id: windows.powershell
  capabilities:
    - os.windows.run_powershell
  permissions:
    - shell.execute
  risk_level: high
  approval_required: true
```

### P3 — Runtime é independente do sistema operacional

O Bauer deve rodar em Windows, Linux e macOS.

A execução pode usar:

- Processo nativo.
- Docker.
- VM.
- Serviço local.
- Agno AgentOS.
- Runtime remoto.
- Adaptador específico por sistema.

Container é útil, mas não deve ser obrigatório.

### P4 — Tudo que executa vira evento

Cada ação precisa gerar evento:

```json
{
  "event_type": "tool.call.requested",
  "agent_id": "dev-agent",
  "skill_id": "windows.powershell",
  "capability": "shell.execute",
  "risk_level": "high",
  "approval_required": true,
  "timestamp": "2026-07-07T23:00:00-03:00"
}
```

Isso permite auditoria, dashboard, replay, debug e governança.

### P5 — Agente 24/7 só com controle

Rodar 24 horas por dia é possível, mas precisa de:

- Scheduler.
- Heartbeat.
- State store.
- Logs.
- Retry.
- Limite de custo.
- Limite de tool calls.
- Aprovação humana.
- Kill switch.
- Modo seguro.

---

## 4. Estrutura recomendada do repositório

```text
bauer-agent/
├── bauer/
│   ├── core/
│   │   ├── runtime/
│   │   │   ├── adapters/
│   │   │   │   ├── base.py
│   │   │   │   ├── bauer_native.py
│   │   │   │   └── agno_adapter.py
│   │   │   ├── run_manager.py
│   │   │   ├── session_manager.py
│   │   │   ├── state_store.py
│   │   │   └── scheduler.py
│   │   ├── policy/
│   │   │   ├── engine.py
│   │   │   ├── rules.py
│   │   │   ├── approvals.py
│   │   │   └── risk.py
│   │   ├── events/
│   │   │   ├── bus.py
│   │   │   ├── schema.py
│   │   │   └── handlers.py
│   │   ├── skills/
│   │   │   ├── registry.py
│   │   │   ├── manifest.py
│   │   │   ├── executor.py
│   │   │   └── installer.py
│   │   ├── agents/
│   │   │   ├── registry.py
│   │   │   ├── spec.py
│   │   │   └── lifecycle.py
│   │   └── observability/
│   │       ├── traces.py
│   │       ├── metrics.py
│   │       └── audit_log.py
│   ├── os_adapters/
│   │   ├── windows/
│   │   │   ├── apps.py
│   │   │   ├── powershell.py
│   │   │   ├── ui_automation.py
│   │   │   └── manifest.yaml
│   │   ├── linux/
│   │   └── macos/
│   ├── cli/
│   ├── server/
│   └── desktop/
├── skills/
│   ├── bauer.project/
│   ├── bauer.coding/
│   ├── bauer.devops/
│   ├── windows.control_panel/
│   ├── windows.browser/
│   └── windows.powershell/
├── docs/
│   ├── RFC-001-bauer-runtime.md
│   ├── RFC-002-skill-interface.md
│   ├── RFC-003-policy-engine.md
│   ├── RFC-004-agno-adapter.md
│   ├── RFC-005-bauer-os.md
│   └── ROADMAP.md
└── tests/
    ├── runtime/
    ├── policy/
    ├── skills/
    └── integration/
```

---

## 5. Interface padrão das skills

Essa é uma das decisões mais importantes.

### 5.1 Manifesto da skill

Cada skill precisa ter um `skill.yaml`.

```yaml
id: windows.control_panel
name: Windows Control Panel Skill
version: 0.1.0
description: Abre e navega no Painel de Controle do Windows.
author: Bauer

runtime:
  type: python
  entrypoint: skill.py

platforms:
  - windows

capabilities:
  - os.windows.open_control_panel
  - os.windows.open_settings

permissions:
  - os.open_app
  - os.ui_control

risk:
  level: low
  requires_approval: false

inputs:
  type: object
  properties:
    target:
      type: string
      enum:
        - control_panel
        - settings
        - network
        - uninstall_programs
  required:
    - target

outputs:
  type: object
  properties:
    status:
      type: string
    message:
      type: string
```

### 5.2 Classe base da skill

```python
class BauerSkill:
    id: str
    name: str
    version: str

    def describe(self) -> dict:
        ...

    def validate(self, input: dict) -> None:
        ...

    def estimate_risk(self, input: dict) -> dict:
        ...

    def execute(self, input: dict, context: dict) -> dict:
        ...
```

### 5.3 Fluxo correto de execução

```text
User request
↓
Agent interpreta intenção
↓
Skill Registry encontra capacidade
↓
Policy Engine avalia permissão e risco
↓
Approval Manager decide se precisa confirmar
↓
Skill Executor roda
↓
Event Bus registra tudo
↓
Observability gera trace/métrica/audit
↓
Resposta ao usuário
```

---

## 6. Policy Engine

### 6.1 Tipos de permissão

```yaml
permissions:
  filesystem.read:
    default: allow

  filesystem.write:
    default: ask

  filesystem.delete:
    default: deny

  shell.execute:
    default: ask

  network.http:
    default: allow

  os.open_app:
    default: allow

  os.ui_control:
    default: ask

  social.publish:
    default: ask

  payment.spend_money:
    default: deny

  deployment.production:
    default: ask
```

### 6.2 Níveis de risco

```text
G0 — Leitura segura
G1 — Ação reversível
G2 — Escrita local
G3 — Ação externa ou pública
G4 — Irreversível, caro, sensível ou perigoso
```

Exemplo:

```yaml
risk_matrix:
  G0:
    approval_required: false
  G1:
    approval_required: false
  G2:
    approval_required: true_if_untrusted_skill
  G3:
    approval_required: true
  G4:
    approval_required: always
```

### 6.3 Primeiro conjunto de regras

```yaml
rules:
  - id: deny_delete_without_confirmation
    when:
      permission: filesystem.delete
    action: require_approval

  - id: deny_secret_exfiltration
    when:
      output_contains_secret: true
    action: block

  - id: ask_shell_commands
    when:
      permission: shell.execute
    action: require_approval

  - id: block_production_deploy
    when:
      permission: deployment.production
    action: require_approval

  - id: allow_open_apps
    when:
      permission: os.open_app
    action: allow
```

---

## 7. Sprints detalhadas

O plano abaixo considera sprints de 1 semana.  
Se você trabalha poucas horas por dia, transforme cada sprint em 2 semanas.

---

# Fase 0 — Congelar visão e contratos

## Sprint 0 — RFC do Bauer Runtime

### Objetivo

Definir oficialmente o que é Bauer Agent, Bauer Agent Runtime e Bauer OS.

### Entregáveis

- `docs/RFC-001-bauer-runtime.md`
- `docs/RFC-002-skill-interface.md`
- `docs/RFC-003-policy-engine.md`
- `docs/RFC-004-agno-adapter.md`
- `docs/ROADMAP.md`

### Passo a passo

1. Criar `docs/RFC-001-bauer-runtime.md`.
2. Escrever a definição:
   - Bauer Agent: agente/assistente.
   - Bauer Agent Runtime: infraestrutura que executa e governa agentes.
   - Bauer OS: experiência principal do usuário.
3. Listar responsabilidades do runtime:
   - runs
   - sessions
   - tools
   - skills
   - policies
   - events
   - scheduling
   - observability
4. Criar glossário:
   - Agent
   - Skill
   - Tool
   - Runtime
   - Run
   - Session
   - Capability
   - Permission
   - Approval
   - Event
   - Trace
5. Criar decisão arquitetural:
   - Bauer deve suportar múltiplos runtimes.
   - Agno será o primeiro runtime adapter externo.
6. Criar diagrama em Markdown.
7. Criar checklist do MVP.

### Critérios de aceite

- Existe uma definição clara de Agent, Runtime e OS.
- O Agno aparece como motor, não como identidade do Bauer.
- A interface de skill tem uma primeira versão.
- A interface de runtime adapter tem uma primeira versão.

---

# Fase 1 — Preparar o Bauer para virar runtime

## Sprint 1 — Runtime Adapter Interface

### Objetivo

Criar uma camada interna para o Bauer chamar qualquer runtime sem se acoplar ao Agno.

### Entregáveis

- `bauer/core/runtime/adapters/base.py`
- `bauer/core/runtime/adapters/bauer_native.py`
- Testes unitários do adapter base.
- Registro de adapters.

### Passo a passo

1. Criar pasta:

```text
bauer/core/runtime/adapters/
```

2. Criar `base.py`.

3. Definir interface:

```python
from typing import Protocol, Iterator, Any

class RuntimeAdapter(Protocol):
    name: str

    def create_agent(self, spec: dict) -> dict:
        ...

    def run_agent(self, request: dict) -> dict:
        ...

    def stream_agent(self, request: dict) -> Iterator[dict]:
        ...

    def stop_run(self, run_id: str) -> dict:
        ...

    def get_run(self, run_id: str) -> dict:
        ...

    def list_sessions(self) -> list[dict]:
        ...
```

4. Criar `bauer_native.py` usando a execução atual do Bauer.
5. Criar factory:

```python
def get_runtime_adapter(name: str) -> RuntimeAdapter:
    ...
```

6. Expor config:

```yaml
runtime:
  default_adapter: bauer_native
  adapters:
    bauer_native:
      enabled: true
    agno:
      enabled: false
      base_url: http://localhost:7777
```

7. Fazer `bauer agent` usar o adapter nativo por baixo.
8. Não mudar comportamento externo ainda.

### Critérios de aceite

- `bauer agent` continua funcionando.
- Existe uma interface única para runtime.
- O adapter nativo passa pelos testes.
- Nenhuma feature antiga quebra.

---

## Sprint 2 — Run Manager e Session Manager

### Objetivo

Separar execução de agente em uma entidade formal chamada `Run`.

### Entregáveis

- `Run`
- `Session`
- `RunManager`
- `SessionManager`
- Persistência mínima em SQLite ou JSONL.

### Modelo de dados

```python
class Run:
    id: str
    session_id: str
    agent_id: str
    runtime_adapter: str
    status: str  # queued, running, completed, failed, cancelled
    input: dict
    output: dict | None
    error: str | None
    started_at: str
    finished_at: str | None
    cost_estimate: float | None
    tool_calls_count: int
```

```python
class Session:
    id: str
    user_id: str
    company_id: str | None
    agent_id: str
    created_at: str
    updated_at: str
    state: dict
```

### Passo a passo

1. Criar `bauer/core/runtime/run_manager.py`.
2. Criar `bauer/core/runtime/session_manager.py`.
3. Criar `bauer/core/runtime/state_store.py`.
4. Criar status de run:
   - queued
   - running
   - waiting_approval
   - completed
   - failed
   - cancelled
5. Adaptar `bauer serve` para registrar runs.
6. Adaptar streaming para associar chunks ao `run_id`.
7. Criar comandos:

```bash
bauer runs list
bauer runs show <run_id>
bauer runs cancel <run_id>
bauer sessions list
bauer sessions show <session_id>
```

### Critérios de aceite

- Toda execução tem `run_id`.
- Toda execução está ligada a uma sessão.
- É possível listar runs e sessões.
- Cancelamento funciona em pelo menos execução local.

---

## Sprint 3 — Event Bus mínimo

### Objetivo

Transformar ações em eventos auditáveis.

### Entregáveis

- `EventBus`
- `EventSchema`
- Persistência de eventos.
- Eventos básicos de run/tool/skill.

### Eventos iniciais

```text
run.created
run.started
run.completed
run.failed
run.cancelled
tool.call.requested
tool.call.completed
tool.call.failed
skill.selected
skill.executed
policy.evaluated
approval.requested
approval.accepted
approval.denied
```

### Passo a passo

1. Criar `bauer/core/events/schema.py`.
2. Criar `bauer/core/events/bus.py`.
3. Implementar publish/subscribe simples em memória.
4. Persistir eventos em JSONL ou SQLite.
5. Integrar EventBus no RunManager.
6. Integrar EventBus nas tools principais.
7. Adicionar endpoint:

```text
GET /events
GET /runs/{run_id}/events
```

8. Adicionar CLI:

```bash
bauer events tail
bauer runs events <run_id>
```

### Critérios de aceite

- Toda run gera eventos.
- Toda tool call importante gera evento.
- É possível fazer tail dos eventos.
- O histórico é persistente.

---

# Fase 2 — Agno como motor de runtime

## Sprint 4 — Spike técnico com Agno

### Objetivo

Rodar um agente simples via Agno fora do Bauer para validar instalação, API, sessões e streaming.

### Entregáveis

- `experiments/agno_minimal_agent.py`
- `docs/notes/agno-spike.md`
- Decisão: API local, SDK ou ambos.

### Passo a passo

1. Instalar Agno em ambiente isolado.
2. Criar um agente mínimo.
3. Rodar uma chamada simples.
4. Testar sessão.
5. Testar streaming, se disponível.
6. Testar ferramenta simples.
7. Testar memory/session.
8. Documentar:
   - como sobe
   - qual porta usa
   - quais endpoints existem
   - como autentica
   - como observar execução
9. Definir o primeiro modo de integração:
   - SDK Python direto
   - HTTP contra AgentOS Runtime
   - ambos

### Critérios de aceite

- Um agente Agno responde fora do Bauer.
- O fluxo está documentado.
- Você sabe exatamente como o Bauer chamará o Agno.

---

## Sprint 5 — AgnoRuntimeAdapter MVP

### Objetivo

Conectar Bauer ao Agno por meio de adapter.

### Entregáveis

- `bauer/core/runtime/adapters/agno_adapter.py`
- Config `runtime.adapters.agno`
- Teste de integração básico.

### Passo a passo

1. Criar `agno_adapter.py`.
2. Implementar:
   - `create_agent`
   - `run_agent`
   - `stream_agent`, se possível
   - `get_run`
   - `list_sessions`
3. Mapear AgentSpec do Bauer para AgentSpec do Agno.
4. Mapear Tool/Skill do Bauer para tools suportadas pelo Agno.
5. Criar config:

```yaml
runtime:
  default_adapter: agno
  adapters:
    agno:
      enabled: true
      mode: http
      base_url: http://localhost:7777
      timeout_s: 120
```

6. Criar comando:

```bash
bauer runtime list
bauer runtime test agno
bauer runtime use agno
```

7. Fazer um agent simples do Bauer rodar via Agno.

### Critérios de aceite

- `bauer runtime test agno` passa.
- Um agent simples roda via Agno.
- Se Agno cair, Bauer mostra erro limpo.
- É possível voltar para `bauer_native`.

---

## Sprint 6 — Compatibilidade Bauer Agent + Agno

### Objetivo

Permitir que agents existentes do Bauer rodem em Agno sem refatoração pesada.

### Entregáveis

- Conversor `agents.yaml` → Agno agent spec.
- Suporte inicial a tools compatíveis.
- Fallback para tool nativa do Bauer quando Agno não suportar.

### Passo a passo

1. Criar `AgentSpec` interno:

```python
class AgentSpec:
    id: str
    name: str
    description: str
    model: str
    provider: str
    instructions: str
    tools: list[str]
    skills: list[str]
    memory: dict
    policies: list[str]
```

2. Criar parser de `agents.yaml`.
3. Criar mapper para Agno.
4. Definir lista de tools MVP:
   - read_file
   - write_file
   - list_dir
   - run_command com aprovação
   - web_search
   - memory
5. Criar testes com 2 agents:
   - code-agent
   - research-agent
6. Criar fallback:
   - Se Agno não conseguir executar a tool, Bauer executa via SkillExecutor, mas policy passa antes.

### Critérios de aceite

- Pelo menos 2 agents existentes rodam via Agno.
- Tools críticas passam pelo Policy Engine.
- O usuário não precisa saber se rodou nativo ou Agno.

---

# Fase 3 — Governança real

## Sprint 7 — Policy Engine MVP

### Objetivo

Toda execução sensível passa por avaliação de permissão.

### Entregáveis

- `PolicyEngine`
- `RiskClassifier`
- `ApprovalManager`
- Primeiras regras YAML.

### Passo a passo

1. Criar `bauer/core/policy/engine.py`.
2. Criar `bauer/core/policy/risk.py`.
3. Criar `bauer/core/policy/approvals.py`.
4. Definir objeto:

```python
class PolicyDecision:
    action: str  # allow, deny, ask
    reason: str
    risk_level: str
    matched_rules: list[str]
```

5. Criar regras iniciais:
   - shell.execute → ask
   - filesystem.delete → ask
   - social.publish → ask
   - os.ui_control → ask
   - filesystem.read → allow
   - filesystem.write → ask se fora do workspace
6. Integrar com ToolExecutor.
7. Integrar com SkillExecutor.
8. Criar CLI:

```bash
bauer approvals list
bauer approvals approve <id>
bauer approvals deny <id>
```

9. Criar endpoint:

```text
GET /approvals
POST /approvals/{id}/approve
POST /approvals/{id}/deny
```

### Critérios de aceite

- `run_command` não executa comando sensível sem policy.
- Ação negada gera evento.
- Ação que exige aprovação entra em `waiting_approval`.
- Aprovação continua a execução.

---

## Sprint 8 — Skill Registry formal

### Objetivo

Transformar skills em unidades formais de capacidade, instalação e permissão.

### Entregáveis

- `SkillManifest`
- `SkillRegistry`
- `SkillExecutor`
- Validação de `skill.yaml`.
- 3 skills internas migradas para o novo formato.

### Passo a passo

1. Criar `bauer/core/skills/manifest.py`.
2. Criar esquema obrigatório:
   - id
   - name
   - version
   - description
   - capabilities
   - permissions
   - risk
   - platforms
   - inputs
   - outputs
3. Criar `SkillRegistry`.
4. Criar `SkillExecutor`.
5. Migrar 3 skills existentes:
   - bauer.project
   - bauer.coding
   - bauer.devops
6. Criar comando:

```bash
bauer skills validate
bauer skills inspect <skill_id>
bauer skills capabilities
```

7. Criar busca por capability:

```bash
bauer skills find os.windows.open_control_panel
```

### Critérios de aceite

- Toda skill instalada tem manifesto válido.
- Bauer consegue achar skill por capability.
- Policy Engine lê permissões do manifesto.
- Skills antigas continuam funcionando por compatibilidade.

---

## Sprint 9 — Observability e auditoria

### Objetivo

Dar visibilidade completa ao que o runtime está fazendo.

### Entregáveis

- Traces por run.
- Audit log.
- Métricas novas.
- Dashboard simples em Web UI.

### Passo a passo

1. Criar `bauer/core/observability/traces.py`.
2. Criar `bauer/core/observability/audit_log.py`.
3. Adicionar métricas:
   - bauer_runs_total
   - bauer_runs_active
   - bauer_runs_failed_total
   - bauer_approvals_pending
   - bauer_policy_denied_total
   - bauer_skill_executions_total
   - bauer_agent_runtime_adapter_calls_total
4. Criar endpoint:

```text
GET /runs
GET /runs/{id}
GET /runs/{id}/trace
GET /audit
```

5. Atualizar Web UI:
   - lista de runs
   - detalhes da run
   - eventos da run
   - aprovações pendentes

### Critérios de aceite

- Você consegue responder “o que o agente fez?”.
- Você consegue responder “por que foi bloqueado?”.
- Você consegue responder “quanto rodou?”.
- Dá para debugar uma execução inteira.

---

# Fase 4 — Runtime 24/7

## Sprint 10 — Scheduler e tarefas persistentes

### Objetivo

Permitir agentes que rodam por horário, gatilho ou condição.

### Entregáveis

- `Scheduler`
- `TaskDefinition`
- Tarefas agendadas persistentes.
- Primeiro worker local.

### Modelo de tarefa

```yaml
id: daily_project_review
name: Revisão diária dos projetos
agent_id: productivity
runtime_adapter: agno
schedule:
  type: cron
  expression: "0 9 * * *"
input:
  message: "Revise o Kanban e gere plano do dia."
policy:
  max_cost_usd: 0.50
  max_runtime_s: 300
  approval_required: false
```

### Passo a passo

1. Criar `bauer/core/runtime/scheduler.py`.
2. Criar persistência de tarefas.
3. Criar CLI:

```bash
bauer schedule add
bauer schedule list
bauer schedule run <id>
bauer schedule pause <id>
bauer schedule resume <id>
bauer schedule delete <id>
```

4. Criar worker local:

```bash
bauer worker start
```

5. Integrar worker com RunManager.
6. Criar eventos:
   - schedule.triggered
   - schedule.skipped
   - schedule.failed

### Critérios de aceite

- Tarefa agenda e executa sozinha.
- Reiniciar o Bauer não perde agendamentos.
- O worker registra eventos.
- Erros não derrubam o processo principal.

---

## Sprint 11 — Heartbeat, retry e recovery

### Objetivo

Dar resistência ao runtime.

### Entregáveis

- Heartbeat de workers.
- Retry configurável.
- Recovery de runs travadas.
- Kill switch.

### Passo a passo

1. Criar tabela/arquivo de workers ativos.
2. Worker envia heartbeat a cada N segundos.
3. Criar detecção de run travada.
4. Criar política:
   - retry_count
   - retry_backoff
   - max_runtime_s
5. Criar comando:

```bash
bauer worker status
bauer runtime recover
bauer runtime kill-switch on
bauer runtime kill-switch off
```

6. Se kill switch estiver ativo:
   - permitir leitura/status
   - bloquear execução nova
   - permitir cancelar runs

### Critérios de aceite

- Worker morto aparece como offline.
- Run travada pode ser marcada como failed.
- Retry funciona.
- Kill switch bloqueia novas execuções.

---

## Sprint 12 — Budget e limites de autonomia

### Objetivo

Impedir agente 24/7 de gastar ou agir sem controle.

### Entregáveis

- Budget diário/semanal/mensal.
- Limite por agent.
- Limite por company.
- Limite por run.
- Modo autonomia.

### Modos de autonomia

```text
manual        — sempre pede confirmação para ações sensíveis
supervised    — executa baixo risco, pede alto risco
autonomous    — executa dentro de budget e policy
locked        — não executa nada, só responde
```

### Config exemplo

```yaml
autonomy:
  mode: supervised
  daily_budget_usd: 2.00
  max_tool_calls_per_run: 100
  max_runtime_s_per_run: 600
  max_parallel_runs: 3
```

### Passo a passo

1. Criar `AutonomyProfile`.
2. Integrar budget no PolicyEngine.
3. Criar tracking de custo por run.
4. Criar CLI:

```bash
bauer budget status
bauer budget set daily 2.00
bauer autonomy set supervised
```

5. Criar eventos:
   - budget.warning
   - budget.exceeded
   - autonomy.changed

### Critérios de aceite

- Runtime bloqueia execução ao estourar budget.
- Autonomia pode ser alterada por config/CLI.
- Dashboard mostra status do budget.
- Cada run mostra custo estimado.

---

# Fase 5 — Bauer OS como experiência

## Sprint 13 — Definir Bauer OS oficialmente

### Objetivo

Transformar Bauer OS em experiência, não em sistema operacional tradicional.

### Entregáveis

- `docs/RFC-005-bauer-os.md`
- Wireframe simples.
- Definição dos módulos do Bauer OS.

### Definição

Bauer OS é a camada principal de experiência do usuário para operar agentes, skills, automações, permissões e execução contínua.

Ele roda em cima de Windows, Linux ou macOS.

### Módulos do Bauer OS

```text
Home
Agents
Skills
Runs
Approvals
Scheduler
Memory
Files/Workspace
OS Control
Settings
Observability
```

### Critérios de aceite

- Está claro que Bauer OS não é um kernel.
- Está claro que Bauer OS é uma shell/experience.
- Está claro que o runtime fica por baixo.
- Está claro que o Agno fica invisível ao usuário final.

---

## Sprint 14 — Dashboard Runtime MVP

### Objetivo

Criar uma interface visual para operar o runtime.

### Entregáveis

- Página Runs.
- Página Approvals.
- Página Agents.
- Página Skills.
- Página Settings.

### Passo a passo

1. Usar a Web UI atual como base.
2. Criar tela de runs:
   - status
   - agent
   - runtime adapter
   - started_at
   - duration
   - cost
3. Criar tela de detalhes da run:
   - input
   - output
   - events
   - tools
   - policy decisions
4. Criar tela de aprovações:
   - ação
   - risco
   - agent
   - skill
   - botão aprovar/negar
5. Criar tela de skills:
   - instaladas
   - capabilities
   - permissões
   - risco
6. Criar tela de runtime:
   - adapter ativo
   - Agno status
   - Bauer native status
   - workers

### Critérios de aceite

- Dá para operar uma run pelo dashboard.
- Dá para aprovar/negar ações.
- Dá para ver skills e permissões.
- Dá para ver se Agno está conectado.

---

## Sprint 15 — Desktop shell / Bauer OS Lite

### Objetivo

Criar a primeira sensação de “Bauer OS”.

### Entregáveis

- App desktop ou web app empacotado.
- Command palette.
- Voice input opcional.
- Launcher de agentes/skills.

### Funcionalidades MVP

```text
Ctrl+Space abre Bauer Command Palette
Digite ou fale:
- abrir navegador
- rodar agent code
- ver runs
- aprovar ação pendente
- abrir painel de controle
- pesquisar arquivo
```

### Passo a passo

1. Definir se o desktop será:
   - Tauri
   - Electron
   - PWA
   - Web local primeiro
2. Criar Command Palette.
3. Criar endpoint:

```text
POST /os/command
```

4. O endpoint transforma intenção em:
   - agent run
   - skill execution
   - dashboard action
5. Integrar voice input existente.
6. Criar primeiro fluxo:
   - “abrir navegador”
   - “abrir painel de controle”
   - “mostrar runs”
   - “pausar agente X”

### Critérios de aceite

- Usuário consegue comandar o Bauer por interface central.
- O runtime executa por trás.
- Toda ação sensível passa por policy.
- A experiência já parece um “mini OS”.

---

# Fase 6 — Skills de sistema operacional

## Sprint 16 — Windows Skill Pack MVP

### Objetivo

Criar o primeiro pacote de skills para controlar o Windows com segurança.

### Entregáveis

- `skills/windows.open_app`
- `skills/windows.browser`
- `skills/windows.control_panel`
- `skills/windows.powershell_safe`
- Manifestos completos.
- Policy integrada.

### Skills iniciais

#### 1. `windows.open_app`

Capacidades:

```text
os.windows.open_app
```

Permissões:

```text
os.open_app
```

Risco: G1

#### 2. `windows.browser`

Capacidades:

```text
os.windows.open_browser
browser.navigate
```

Permissões:

```text
os.open_app
network.http
```

Risco: G1/G2

#### 3. `windows.control_panel`

Capacidades:

```text
os.windows.open_control_panel
os.windows.open_settings
```

Permissões:

```text
os.open_app
os.ui_control
```

Risco: G2

#### 4. `windows.powershell_safe`

Capacidades:

```text
os.windows.run_powershell
```

Permissões:

```text
shell.execute
```

Risco: G3/G4 dependendo do comando

### Implementação recomendada

Use adaptadores por baixo:

- `subprocess` para abrir apps e comandos simples.
- PowerShell para automação de sistema.
- `pywin32` para APIs nativas quando necessário.
- `pywinauto` ou UI Automation para UI.
- Playwright para navegador.

### Critérios de aceite

- Bauer abre apps simples.
- Bauer abre navegador.
- Bauer abre configurações/painel.
- PowerShell pede aprovação.
- Tudo gera eventos e audit log.

---

## Sprint 17 — Linux/macOS Skill Pack MVP

### Objetivo

Provar que a interface de skills é multiplataforma.

### Entregáveis

- `linux.open_app`
- `linux.shell_safe`
- `macos.open_app`
- `macos.shell_safe`

### Passo a passo

1. Implementar manifesto igual ao Windows.
2. Trocar apenas backend.
3. Criar capability genérica:

```text
os.open_app
```

4. O SkillRegistry resolve para:
   - windows.open_app
   - linux.open_app
   - macos.open_app

### Critérios de aceite

- Mesma intenção funciona em mais de um OS.
- O agente não sabe qual OS está por baixo.
- SkillRegistry escolhe a skill certa.

---

# Fase 7 — Multi-agent e organização

## Sprint 18 — Agent Registry formal

### Objetivo

Transformar agents em entidades registradas, versionadas e governadas.

### Entregáveis

- `AgentRegistry`
- `AgentSpec`
- Versionamento de agent.
- Políticas por agent.

### AgentSpec exemplo

```yaml
id: bauer.dev
name: Bauer Dev Agent
version: 0.1.0
description: Agente de desenvolvimento de software.
runtime_adapter: agno
model:
  provider: openrouter
  name: auto
skills:
  - bauer.coding
  - bauer.project
permissions:
  - filesystem.read
  - filesystem.write
  - shell.execute
autonomy:
  mode: supervised
limits:
  max_runtime_s: 900
  max_tool_calls: 300
```

### Critérios de aceite

- Agents têm spec formal.
- Agents têm versionamento.
- Agents têm permissões próprias.
- Runtime pode listar/rodar agents pelo registry.

---

## Sprint 19 — Teams e delegação governada

### Objetivo

Permitir times de agentes com regras claras.

### Entregáveis

- `TeamSpec`
- Delegação com eventos.
- Policy para delegação.

### TeamSpec exemplo

```yaml
id: bauer.software_team
name: Bauer Software Team
agents:
  - bauer.product
  - bauer.dev
  - bauer.qa
  - bauer.devops
coordination:
  mode: supervisor
  supervisor: bauer.product
limits:
  max_parallel_runs: 3
  max_daily_budget_usd: 3.00
```

### Critérios de aceite

- Um agent pode delegar para outro.
- Delegação vira evento.
- Team tem limite de custo.
- Team não executa fora das policies.

---

# Fase 8 — Qualidade, segurança e maturidade

## Sprint 20 — Testes de segurança de skills/tools

### Objetivo

Evitar que skills virem buraco de segurança.

### Entregáveis

- Testes de policy.
- Testes de comandos proibidos.
- Testes de path traversal.
- Testes de secrets.

### Checklist

- Não permitir deletar fora do workspace sem aprovação.
- Não permitir ler secrets e enviar para fora.
- Não permitir comando shell destrutivo sem aprovação.
- Não permitir publicação social sem aprovação.
- Não permitir deploy produção sem aprovação.
- Não permitir skill sem manifesto válido.

### Critérios de aceite

- Suite de segurança roda no CI.
- Comandos perigosos são bloqueados.
- Policy decisions são testadas.

---

## Sprint 21 — Marketplace local de skills

### Objetivo

Preparar o Bauer para ecossistema de skills.

### Entregáveis

- Skill package format.
- Instalação local.
- Assinatura/hash.
- Índice de skills.

### Passo a passo

1. Definir estrutura:

```text
skill-package/
├── skill.yaml
├── skill.py
├── README.md
├── tests/
└── examples/
```

2. Criar comando:

```bash
bauer skills package
bauer skills install ./skill-package
bauer skills uninstall <id>
```

3. Calcular hash do pacote.
4. Mostrar permissões antes de instalar.
5. Bloquear instalação de skill sem manifesto.

### Critérios de aceite

- Instalação mostra permissões.
- Usuário aprova antes de instalar.
- Skill inválida é rejeitada.
- Skill instalada aparece no dashboard.

---

## Sprint 22 — Memória auditável

### Objetivo

Melhorar memória para runtime, não só conversa.

### Entregáveis

- MemoryRecord com origem, validade e confiança.
- Busca por memória.
- Expiração/revisão.
- Eventos de memória.

### Modelo

```python
class MemoryRecord:
    id: str
    scope: str  # user, company, project, agent, skill
    content: str
    source: str
    confidence: float
    valid_until: str | None
    created_at: str
    updated_at: str
```

### Critérios de aceite

- Memória tem origem.
- Memória pode expirar.
- Memória pode ser revisada.
- Toda escrita de memória gera evento.

---

# Fase 9 — Release do Bauer Agent Runtime

## Sprint 23 — Beta fechado

### Objetivo

Lançar primeira versão coerente do Bauer Agent Runtime.

### Escopo do beta

- Adapter nativo.
- Adapter Agno.
- Policy Engine.
- Skill Registry.
- Runs e Sessions.
- Event Bus.
- Scheduler.
- Dashboard básico.
- Windows Skill Pack MVP.
- Observability.

### Checklist de release

- README atualizado.
- `docs/ROADMAP.md` atualizado.
- `docs/RFC-*` completos.
- Testes passando.
- Instalação limpa.
- Upgrade não quebra config antiga.
- Demo gravável em 5 minutos.

### Demo obrigatória

```text
1. Iniciar Bauer Runtime.
2. Rodar agent via Agno.
3. Executar skill de arquivo.
4. Tentar comando PowerShell sensível.
5. Bauer pedir aprovação.
6. Aprovar.
7. Ver run, eventos e audit log no dashboard.
8. Agendar tarefa.
9. Worker executar sozinho.
10. Mostrar kill switch.
```

### Critérios de aceite

- O Bauer já pode ser chamado honestamente de Agent Runtime.
- A demo é repetível.
- O usuário entende o valor em 5 minutos.

---

# Fase 10 — Bauer OS

## Sprint 24 — Bauer OS Alpha

### Objetivo

Criar a primeira versão do Bauer OS como experiência central.

### Entregáveis

- Desktop/Web Shell.
- Command Palette.
- Voice Command.
- Agent Launcher.
- Skill Launcher.
- Approvals Center.
- Runtime Monitor.

### Experiência desejada

O usuário abre o Bauer OS e vê:

```text
Hoje
- 3 agentes ativos
- 2 aprovações pendentes
- 1 tarefa agendada falhou
- budget usado: R$ 1,20 / R$ 5,00
- últimas execuções
```

Ele aperta `Ctrl+Space` e fala/digita:

```text
abre o navegador e pesquisa docs do Agno
```

O Bauer:

1. Identifica intenção.
2. Seleciona skill.
3. Avalia policy.
4. Executa.
5. Registra evento.
6. Mostra resultado.

### Critérios de aceite

- Parece uma experiência central, não uma tela de debug.
- O usuário não vê “Agno” salvo em telas técnicas.
- O usuário vê Bauer.
- Agno fica nos bastidores.

---

## 8. Ordem exata para começar amanhã

Se você quiser começar do jeito mais inteligente:

### Dia 1

1. Criar branch:

```bash
git checkout -b runtime-agno-foundation
```

2. Criar docs:

```bash
mkdir -p docs
touch docs/RFC-001-bauer-runtime.md
touch docs/RFC-002-skill-interface.md
touch docs/RFC-003-policy-engine.md
touch docs/RFC-004-agno-adapter.md
touch docs/ROADMAP.md
```

3. Colar este plano em:

```text
docs/ROADMAP.md
```

4. Criar estrutura:

```bash
mkdir -p bauer/core/runtime/adapters
mkdir -p bauer/core/policy
mkdir -p bauer/core/events
mkdir -p bauer/core/skills
mkdir -p bauer/core/agents
mkdir -p bauer/core/observability
```

### Dia 2

1. Criar `RuntimeAdapter`.
2. Criar `BauerNativeRuntimeAdapter`.
3. Criar teste simples.
4. Fazer `bauer agent` continuar funcionando.

### Dia 3

1. Criar `RunManager`.
2. Toda execução ganha `run_id`.
3. Expor `bauer runs list`.

### Dia 4

1. Criar EventBus.
2. Gerar eventos para run.
3. Criar `bauer events tail`.

### Dia 5

1. Fazer spike com Agno fora do Bauer.
2. Documentar como rodar.
3. Definir SDK ou HTTP.

### Dia 6–7

1. Criar `AgnoRuntimeAdapter`.
2. Rodar agent simples via Agno.
3. Garantir fallback para nativo.

---

## 9. MVP mínimo para dizer “Bauer Agent Runtime”

Você pode chamar de Bauer Agent Runtime quando tiver:

```text
[ ] RuntimeAdapter
[ ] RunManager
[ ] SessionManager
[ ] EventBus
[ ] PolicyEngine
[ ] SkillRegistry formal
[ ] AgnoRuntimeAdapter
[ ] Scheduler
[ ] Worker
[ ] Observability
[ ] Dashboard básico
[ ] Kill switch
```

Antes disso, é Bauer Agent avançado.  
Depois disso, é runtime.

---

## 10. MVP mínimo para dizer “Bauer OS”

Você pode chamar de Bauer OS quando tiver:

```text
[ ] Dashboard central
[ ] Command Palette
[ ] Voice input
[ ] Agent launcher
[ ] Skill launcher
[ ] Approvals center
[ ] Runtime monitor
[ ] OS skill pack inicial
[ ] Experiência unificada
```

Antes disso, é dashboard.  
Depois disso, é Bauer OS.

---

## 11. Riscos principais

### Risco 1 — Acoplar demais no Agno

Mitigação:

```text
Agno sempre atrás de RuntimeAdapter.
```

### Risco 2 — Skill virar permissão total

Mitigação:

```text
Skill sempre declara capabilities, permissions e risk.
```

### Risco 3 — Runtime 24/7 gastar demais

Mitigação:

```text
Budget + autonomy mode + max_runtime + max_tool_calls.
```

### Risco 4 — Dashboard virar enfeite

Mitigação:

```text
Dashboard deve operar approvals, runs, skills e workers de verdade.
```

### Risco 5 — Bauer OS virar sonho grande demais

Mitigação:

```text
Bauer OS começa como Command Palette + Dashboard + Skills.
Não começa como sistema operacional real.
```

---

## 12. Decisão estratégica final

O caminho certo é:

```text
Agora:
Bauer Agent avançado

Próximo:
Bauer Agent Runtime com Agno

Depois:
Bauer OS como experiência central

Futuro:
Ecossistema de skills e agentes
```

O Bauer já tem muita coisa de runtime.  
O trabalho agora é **organizar, padronizar e governar**.

A virada de chave não é adicionar mais tools.  
É criar o núcleo que controla todas elas.

---

## 13. Referências usadas

- Agno Docs — https://docs.agno.com/
- Agno AgentOS Introduction — https://docs.agno.com/agent-os/introduction
- Agno GitHub — https://github.com/agno-agi/agno
- Bauer Agent GitHub — https://github.com/henriqueferraz86/bauer-agent

---

## 14. Próximo documento recomendado

Depois deste plano, crie estes arquivos:

```text
docs/RFC-001-bauer-runtime.md
docs/RFC-002-skill-interface.md
docs/RFC-003-policy-engine.md
docs/RFC-004-agno-adapter.md
docs/RFC-005-bauer-os.md
docs/ROADMAP.md
```

O primeiro a escrever é:

```text
docs/RFC-002-skill-interface.md
```

Porque a interface de skill é o coração do Bauer no longo prazo.
