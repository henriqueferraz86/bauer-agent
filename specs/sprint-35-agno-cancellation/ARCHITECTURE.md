# ARCHITECTURE — Sprint 35: spike de cancelamento no Agno

## Visão geral

O spike observa a propagação do `ContextVar` no caminho sync já usado por
`AgnoRuntimeAdapter`, sem alterar runtime ou dependências. O adapter envolve a
iteração de `agent.run(..., stream=True)` e `team.run(..., stream=True)` com
`_in_agno_session`; as ferramentas Agno chamam o `ToolRouter` via
`_bauer_tool_callable`.

## Componentes examinados

- `bauer/core/runtime/adapters/agno_adapter.py`: wrapper do stream e callable
  das tools.
- `bauer/execution_control.py`: `current_control()` e checkpoints usados pelo
  `ToolRouter`.
- Agno instalado conforme `uv.lock`: dispatch sync de Agent e Team e chamadas
  de ferramentas.
- Probe hermético do sprint: fakes/substitutos no ponto público de execução,
  sem provider, rede ou configuração real.

## Fluxo de evidência

```text
ContextVar ativo no adapter → Agent.run/Team.run sync → evento/tool callable
                           → ToolRouter lê current_control()
```

## Decisões técnicas

- A prova deve exercitar a mesma API sync chamada pelo adapter, não apenas
  `copy_context()` numa thread criada pelo próprio teste.
- Leitura estática do SDK complementa o probe, mas não substitui o resultado
  dinâmico no ponto de chamada da tool.
- Nenhuma API privada do Agno será monkeypatched em produção para obter
  propagação de contexto.

## STOP

Se o caminho usado pelo adapter trocar para thread sem propagação de contexto,
ou se checkpoints seguros entre eventos exigirem monkeypatch do SDK, parar e
seguir a contingência documentada em `plans/062-agno-executor-padrao.md`.

## Observabilidade

O resultado é documentado no `BACKLOG.md` com versão Agno, comando do probe,
seam exercitado e limites. O probe não adiciona telemetria nem logs de runtime.

## Resultado do spike — Agno 3.0.10

- O probe dinâmico no Agent real, com modelo fake, confirmou que a tool recebe
  o `ExecutionControl` ativo e o `session_id` do contexto do adapter.
- A inspeção de `agno.agent._run.run_dispatch` e
  `agno.team._run.run_dispatch` confirmou que `run(stream=True)` encaminha aos
  streams sync (`_run_stream`); tools chamadas nesses streams executam
  síncronas no contexto corrente. O Team documenta processamento sequencial
  dos membros ao fazer streaming.
- Os caminhos `arun()` e tarefas de background (memória/aprendizado) não são
  cobertos por essa evidência e não são chamados pelo adapter atual.
- Decisão: **avançar**. Não há fronteira de thread/task no caminho sync atual;
  a propagação observada usa APIs públicas e não requer monkeypatch.
- Custo/uso parcial: o Kernel acumula campos que o executor informa, mas o
  adapter Agno ainda não traduz as métricas do SDK para `TurnMetrics`. A
  comparação e preservação ponta a ponta ficam na Sprint 36; nenhum canal
  pode migrar antes desse aceite.
