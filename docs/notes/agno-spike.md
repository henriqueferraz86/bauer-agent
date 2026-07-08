# Spike Agno minimo

## Contexto

Sprint 4 da Fase 2 valida o Agno fora do Bauer antes de criar um adapter real.
O teste usa `agno==2.7.1` instalado na `.venv` do projeto e um `Model`
deterministico local para nao depender de credenciais externas.

## Como subir

```powershell
.\.venv\Scripts\python.exe -m pip install agno sqlalchemy
.\.venv\Scripts\python.exe experiments\agno_minimal_agent.py
```

O script grava sessoes em SQLite:

```text
tmp/agno_spike.db
```

## O que foi validado

- `Agent.run(...)` responde fora do Bauer.
- `session_id` e `run_id` sao emitidos pelo Agno.
- Historico de sessao volta no contexto com `add_history_to_context=True`.
- Streaming funciona com `Agent.run(..., stream=True)`.
- Tool call funciona com uma funcao Python registrada em `tools=[add_numbers]`.
- Persistencia minima de sessao funciona via `agno.db.sqlite.SqliteDb`.

## Porta, endpoints e autenticacao

Modo validado nesta sprint: SDK Python direto.

- Porta: nenhuma.
- Endpoints: nenhum endpoint HTTP e necessario nesse modo.
- Autenticacao: nenhuma no modelo deterministico local; em provider real, a
  autenticacao fica nas variaveis de ambiente do provider escolhido, por
  exemplo `OPENAI_API_KEY` ou `OPENROUTER_API_KEY`.

O pacote instalado tambem possui modulos `agno.os` e `agno.api`, mas o primeiro
modo de integracao do Bauer nao precisa expor AgentOS HTTP. A camada HTTP pode
ser testada depois, quando o adapter Agno ja estiver estavel.

## Como observar execucao

O script imprime:

- bloco `simple` com `run_id`, `session_id` e resposta;
- bloco `session` mostrando uso do historico;
- bloco `tool` com nome e resultado da tool;
- bloco `stream` com chunks emitidos;
- bloco `storage` com o caminho absoluto do SQLite.

## Decisao

Primeiro modo de integracao: SDK Python direto.

Motivos:

- mapeia naturalmente para o `RuntimeAdapter` criado na Fase 1;
- preserva `run_id`, `session_id`, streaming e tools sem criar outro processo;
- evita acoplamento prematuro a endpoints AgentOS;
- permite que o Bauer continue sendo a camada de run/session/event/policy.

Modo secundario futuro: HTTP contra AgentOS Runtime.

Esse modo deve ser tratado como adapter separado quando houver necessidade de
rodar Agno como processo remoto, container ou runtime compartilhado.

## Como o Bauer chamara o Agno

O futuro adapter `agno_sdk` deve:

1. construir um `agno.agent.Agent` a partir da definicao do agente Bauer;
2. injetar `session_id`, `user_id`, `tools` e `db`;
3. chamar `agent.run(input, stream=False)` para execucao normal;
4. chamar `agent.run(input, stream=True)` para streaming;
5. converter eventos/chunks do Agno para eventos do `EventBus` do Bauer;
6. persistir o `run_id` do Bauer como chave principal e guardar o `run_id`
   Agno em metadados do run.

Assim, o Agno executa o agente, enquanto o Bauer continua governando run,
sessao, eventos, politicas, aprovacoes e observabilidade.
