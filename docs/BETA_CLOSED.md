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
.\.venv\Scripts\activate
python -m pip install -e ".[server,web]"
python -m bauer.cli config check --config config.yaml
python -m bauer.cli runtime list
python -m bauer.cli skills validate
```

Para Agno:

```powershell
python -m pip install agno sqlalchemy
python -m bauer.cli runtime test agno --config config.yaml
```

## Demo Repetivel em 5 Minutos

Use dois terminais.

### Terminal 1: API e dashboard

```powershell
.\.venv\Scripts\activate
python -m bauer.cli serve --config config.yaml --host 127.0.0.1 --port 8000
```

Abrir:

```text
http://127.0.0.1:8000/
```

### Terminal 2: roteiro de operacao

1. Confirmar runtime e adapters.

```powershell
python -m bauer.cli runtime list
python -m bauer.cli runtime test agno --config config.yaml
```

2. Rodar agent via Agno.

```powershell
python -m bauer.cli runtime use agno --config config.yaml
python -m bauer.cli agent run-one "Responda um smoke test curto do Bauer Runtime." --config config.yaml
```

3. Executar skill de arquivo / validar skill registry.

```powershell
python -m bauer.cli skills validate
python -m bauer.cli skills find filesystem.read
python -m bauer.cli skills inspect bauer.coding
```

4. Tentar comando PowerShell sensivel e verificar aprovacao.

```powershell
python -m bauer.cli skills inspect windows.powershell_safe
python -m bauer.cli approvals list
```

No dashboard, abrir Approvals. A acao `shell.execute` deve aparecer como pendente quando solicitada por skill/tool governada.

5. Aprovar.

```powershell
python -m bauer.cli approvals approve <approval_id>
```

6. Ver run, eventos e audit log.

```powershell
python -m bauer.cli runs list
python -m bauer.cli runs show <run_id>
python -m bauer.cli runs events <run_id>
curl http://127.0.0.1:8000/events
curl http://127.0.0.1:8000/audit
```

No dashboard, abrir Runs, Events, Approvals e Observability.

7. Agendar tarefa.

```powershell
python -m bauer.cli schedule add --id beta_smoke --name "Beta smoke" --agent-id default --runtime-adapter bauer_native --cron "* * * * *" --message "Execute um smoke test agendado curto."
python -m bauer.cli schedule list
```

8. Worker executar sozinho.

```powershell
python -m bauer.cli worker start
```

Em outro terminal:

```powershell
python -m bauer.cli worker status
python -m bauer.cli schedule show beta_smoke
python -m bauer.cli runs list
```

9. Mostrar kill switch.

```powershell
python -m bauer.cli runtime kill-switch on
python -m bauer.cli runtime kill-switch status
python -m bauer.cli schedule run beta_smoke
python -m bauer.cli runtime kill-switch off
```

Com o kill switch ligado, novas execucoes devem ser bloqueadas, mas leitura/status e cancelamento continuam permitidos.

10. Limpar demo.

```powershell
python -m bauer.cli schedule delete beta_smoke --yes
python -m bauer.cli runtime use bauer_native --config config.yaml
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
