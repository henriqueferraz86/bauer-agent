# Bauer Agent Runtime Roadmap

## Status Atual

O Bauer esta no beta fechado do Agent Runtime. A base atual cobre execucao governada, adapters, policy, skills, scheduler, observability, dashboard basico, skill packs por sistema operacional, teams, marketplace local e memoria auditavel.

## Marcos Entregues

### Fase 1: Runtime Foundation

- Runtime Adapter Interface.
- RunManager e SessionManager.
- Event Bus minimo.
- Spike Agno.
- AgnoRuntimeAdapter MVP.
- Compatibilidade Bauer Agent + Agno.

### Fase 3: Governanca e Observability

- Policy Engine MVP.
- Skill Registry formal.
- Observability e auditoria.

### Fase 4: Execucao Continua

- Scheduler e tarefas persistentes.
- Heartbeat, retry e recovery.
- Budget e limites de autonomia.

### Fase 5: Bauer OS

- RFC-005 Bauer OS.
- Dashboard Runtime MVP.
- Desktop shell / Bauer OS Lite.

### Fase 6: Skill Packs

- Windows Skill Pack MVP.
- Linux/macOS Skill Pack MVP.

### Fase 7: Agents Governados

- Agent Registry formal.
- Teams e delegacao governada.

### Fase 8: Runtime Safety e Ecosystem

- Testes de seguranca de skills/tools.
- Marketplace local de skills.
- Memoria runtime auditavel.

### Fase 9: Closed Beta

- Release beta coerente.
- README e docs de demo.
- Validacao de instalacao limpa.
- Checklist de upgrade/config.
- Demo repetivel em 5 minutos.

## Beta Fechado: Escopo

- Adapter nativo.
- Adapter Agno.
- Policy Engine.
- Skill Registry.
- Runs e Sessions.
- Event Bus.
- Scheduler.
- Dashboard basico.
- Windows Skill Pack MVP.
- Observability.

## Proximos Marcos

### Beta 2

- Melhorar UX do dashboard de approvals e run trace.
- Adicionar export de audit log por run.
- Tornar a demo `beta_smoke` automatizavel por script.
- Adicionar health check especifico do Agno adapter.
- Melhorar mensagens de erro quando Agno estiver fora do ar.

### Release Candidate

- Congelar schemas publicos de `Run`, `Session`, `Event`, `SkillManifest` e `AgentSpec`.
- Criar migracoes explicitas para stores JSONL/SQLite.
- Assinar skill packages locais com chave do usuario.
- Publicar guia de operacao Windows.
- Rodar matriz de instalacao limpa em Windows, Linux e macOS.

### Public Beta

- Marketplace remoto experimental.
- Desktop Bauer OS Lite empacotado.
- Templates oficiais de agents e teams.
- Runbooks de producao local.
- Politicas prontas para ambientes pessoais, dev e empresa.

## Definicao de Pronto para Public Beta

- Instalacao limpa reproduzivel.
- Upgrade sem quebrar `config.yaml` antigo.
- Suite de testes verde.
- Demo de 5 minutos gravavel.
- Dashboard permite responder: o que rodou, por que bloqueou, quanto custou e como aprovar/cancelar.
- Toda acao sensivel passa por policy e gera evento/audit log.
