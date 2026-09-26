# Jev no Bauer

O Bauer usa Jev como uma camada de decisão estruturada opcional. Jev compara
alternativas com probabilidades normalizadas, escolhe o tier (`fast`,
`balanced`, `coding` ou `heavy`), runtime, time, agente, ferramentas,
estratégia e plano. A execução continua passando pelo Kernel, policy,
approval, allowlist, orçamento e gates do Bauer.

O resultado é uma recomendação auditável. Jev não executa ferramentas e não
pode conceder permissões. O Kernel grava a decisão no run e publica o evento
`decision.selected` antes da execução. Se o runtime escolhido for `agno` e
houver `team_id`, o adapter resolve o time pelo `TeamRegistry` e materializa
um `agno.team.Team` com os agentes registrados. Sem time, usa o agente
individual selecionado.

As perguntas enviadas usam apenas tipos aceitos pela API System One
(`choice` e `noul`). Cada ferramenta disponível é avaliada com uma pergunta
sim/não (`noul`); as probabilidades vêm da resposta `choice` do tier. O plano
curto é montado localmente a partir da estratégia escolhida, sem pedir à API
uma resposta em lista, formato que o contrato da TypeSafe não oferece.

Jev fica desligado por padrão. Para habilitar no perfil atual:

```bash
bauer config set TYPESAFE_API_KEY sua-chave
bauer config set decision.jev_enabled true
bauer config set decision.fallback_enabled true
bauer config set model.router_enabled true
```

Os tiers precisam estar configurados em `model.profiles`, por exemplo:

```yaml
model:
  router_enabled: true
  profiles:
    fast: {provider: ollama, model: qwen3:0.6b}
    balanced: {provider: ollama, model: qwen3:8b}
    coding: {provider: ollama, model: qwen3-coder:30b}
    heavy: {provider: openrouter, model: deepseek/deepseek-r1}

decision:
  jev_enabled: true
  fallback_enabled: true
  memory_enabled: true
  timeout_seconds: 2.0
  min_confidence: 0.65
```

O catálogo enviado ao Jev contém somente IDs de times e agentes registrados no
Bauer e ferramentas disponíveis para o contexto. Decisões anteriores com
resultado positivo são consultadas na `DecisionMemory`; cada nova decisão é
registrada com `memory_decision_id` para receber feedback posterior da sessão.

Quando Jev está desligado ou sem chave, o fallback local mantém o mesmo
contrato: produz probabilidades heurísticas, escolhe o time Agno formal para
tarefas com múltiplas etapas, seleciona um agente por tipo de tarefa, sugere
ferramentas permitidas e cria um plano curto. Assim a integração pode ser
testada antes da ativação da API.

Para desligar Jev e voltar imediatamente ao classificador local:

```bash
bauer config set decision.jev_enabled false
```

Se Jev estiver habilitado, mas houver timeout, erro HTTP, resposta inválida ou
confiança abaixo do limite, `fallback_enabled: true` usa o classificador
heurístico local. Para operar sem qualquer chamada externa de decisão, deixe
`decision.jev_enabled: false`. A chave é armazenada no `.env` pelo comando de
configuração e não aparece no painel nem nos eventos.
# Agentes especialistas

O catálogo padrão também contém Bauer Architect, Security, Research, Docs e
Data. O fallback local pode escolhê-los por sinais da solicitação sem exigir
Jev. Os papéis, ferramentas e limites estão em
[`bauer-specialist-agents.md`](bauer-specialist-agents.md).
