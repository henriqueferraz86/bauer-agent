# Bauer Agent Runtime Closed Beta

Status: beta fechado
Data: 2026-07-08
Versao alvo: 0.9.0b1

## Objetivo

Este beta demonstra que o Bauer ja funciona honestamente como Agent Runtime: ele executa agentes por adapter, registra runs e sessoes, aplica policy antes de acoes sensiveis, gera eventos, persiste historico, mostra observability e opera tarefas agendadas.

## Escopo Incluido

- Adapter nativo `bauer_native`.
- Adapter `agno`.
- Policy Engine e ApprovalManager.
- Skill Registry formal e SkillExecutor.
- Runs e Sessions persistentes.
- Event Bus persistente.
- Scheduler e worker local.
- Dashboard runtime basico.
- Windows Skill Pack MVP.
- Observability: runs, eventos, traces, audit log e metricas.
- Budget, autonomia, kill switch e recovery basicos.
- Skill marketplace local.
- Memoria runtime auditavel.

## Fora do Escopo

- Marketplace remoto de skills.
- Multi-tenant completo.
- Assinatura remota de pacotes.
- Desktop shell empacotado como release publica.
- Garantia de compatibilidade com qualquer runtime externo alem do adapter Agno MVP.

## Preparo

```powershell
cd C:\Users\henri\Documents\PROJETOS\BauerAgent
uv sync --frozen --extra dev
uv run bauer config check --config config.yaml
uv run bauer runtime list
uv run bauer skills validate
```

Para Agno:

```powershell
# Agno e SQLAlchemy nao fazem parte do lock do Bauer. Este e um smoke opt-in
# do adapter externo; `--with` mantem essas dependencias fora do ambiente do CI.
uv run --with agno --with sqlalchemy bauer runtime test agno --config config.yaml
```

## Demo Repetivel em 5 Minutos

Use dois terminais.

### Terminal 1: API e dashboard

```powershell
uv run bauer serve --config config.yaml --host 127.0.0.1 --port 8000
```

Abrir:

```text
http://127.0.0.1:8000/
```

### Terminal 2: roteiro de operacao

1. Confirmar runtime e adapters.

```powershell
uv run bauer runtime list
uv run --with agno --with sqlalchemy bauer runtime test agno --config config.yaml
```

2. Rodar agent via Agno.

```powershell
uv run bauer runtime use agno --config config.yaml
uv run --with agno --with sqlalchemy bauer agent run-one "Responda um smoke test curto do Bauer Runtime." --config config.yaml
```

3. Executar skill de arquivo / validar skill registry.

```powershell
uv run bauer skills validate
uv run bauer skills find filesystem.read
uv run bauer skills inspect bauer.coding
```

4. Tentar comando PowerShell sensivel e verificar aprovacao.

```powershell
uv run bauer skills inspect windows.powershell_safe
uv run bauer approvals list
```

No dashboard, abrir Approvals. A acao `shell.execute` deve aparecer como pendente quando solicitada por skill/tool governada.

5. Aprovar.

```powershell
uv run bauer approvals approve <approval_id>
```

6. Ver run, eventos e audit log.

```powershell
uv run bauer runs list
uv run bauer runs show <run_id>
uv run bauer runs events <run_id>
curl http://127.0.0.1:8000/events
curl http://127.0.0.1:8000/audit
```

No dashboard, abrir Runs, Events, Approvals e Observability.

7. Agendar tarefa.

```powershell
uv run bauer cron create beta_smoke "Execute um smoke test agendado curto." --schedule "cron: * * * * *" --workspace workspace
uv run bauer cron list --workspace workspace
```

8. Subir o runtime supervisionado. Ele inicia dispatcher, cron, outbox e Kanban;
o cron vencido entra na fila e o dispatcher o executa.

```powershell
uv run bauer runtime start --workspace workspace
```

Em outro terminal:

```powershell
uv run bauer runtime status --workspace workspace
uv run bauer cron run beta_smoke --workspace workspace
uv run bauer dispatch status --workspace workspace
uv run bauer runs list
```

9. Mostrar kill switch.

```powershell
uv run bauer runtime kill-switch on
uv run bauer runtime kill-switch status
uv run bauer cron run beta_smoke --workspace workspace
uv run bauer runtime kill-switch off
```

Com o kill switch ligado, novas execucoes devem ser bloqueadas, mas leitura/status e cancelamento continuam permitidos.

10. Limpar demo.

```powershell
uv run bauer cron delete beta_smoke --yes --workspace workspace
uv run bauer runtime stop --workspace workspace
uv run bauer runtime use bauer_native --config config.yaml
```

## Checklist de Release

- README atualizado com runtime beta.
- `docs/ROADMAP.md` atualizado.
- RFC-005 aceito como definicao de Bauer OS.
- Testes passando.
- Instalacao limpa validada.
- Config antiga continua valida por defaults de `RuntimeSection`.
- Demo acima repetivel em ate 5 minutos.

## Criterios de Aceite

- O Bauer pode ser apresentado como Agent Runtime porque possui execucao formal, policy, eventos, skills, scheduler, observability e adapters.
- A demo e repetivel com comandos versionados.
- O usuario entende o valor em 5 minutos: agente roda, acao sensivel pede aprovacao, run fica auditavel, tarefa agenda e kill switch bloqueia execucao nova.
