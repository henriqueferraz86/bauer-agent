# Plano 068 — Reutilizar as conexões SQLite do runtime

**Status:** IN PROGRESS

**Prioridade:** P2

**Esforço total estimado:** M (sprint 76)

**Referências:** plano 064, sprint 64 (`specs/sprint-64-windows-ci-stability/`);
commits `33530e1` e `eb65424`; runs de CI 42924245625, 42929970110 e
42935274304 (tentativa 2).

## Problema e objetivo

`SqliteStateStore` abre e fecha uma conexão SQLite a cada leitura, escrita e
evento publicado. O EventBus grava observabilidade a cada evento, e a checagem
de governança por evento dos canais lê o estado várias vezes. Um único turno
abre centenas de conexões.

Em WAL, fechar a última conexão de um arquivo faz checkpoint e apaga o `-wal`
e o `-shm`. A próxima abertura reconstrói os dois, e outra conexão abrindo
nesse intervalo espera o lock do arquivo. No Windows, onde fsync e remoção de
arquivo são lentos, essas esperas se somam até estourar o prazo dos testes de
aprovação. A run fica em `running` sem nenhuma aprovação gravada.

A sprint 64 e os commits `33530e1` e `eb65424` removeram os pragmas
repetidos, que amplificavam o problema. Mesmo assim, a run 42935274304
(tentativa 2) ainda pegou uma thread presa no primeiro comando de uma conexão
nova (`state_store.py` `_connect`, `PRAGMA synchronous`).

O objetivo é eliminar essa rotatividade de conexões no runtime: menos
latência por evento em todas as plataformas e o fim do travamento
intermitente no Windows.

## Sprint 76 — Conexões reutilizadas no store de runtime

- `SqliteStateStore` reutiliza conexões em vez de abrir uma por operação. Pode
  ser uma conexão por thread por arquivo, ou uma conexão âncora mantida
  aberta por processo para que o `-wal` nunca seja apagado; a escolha fica
  registrada na SPEC. O contrato atual se mantém: escrita serializada pelo
  `RLock` por arquivo, `BEGIN IMMEDIATE`, retry em `locked`.
- Fechamento explícito: o store expõe `close()`, e os testes e o shutdown do
  processo liberam as conexões. Isso é necessário no Windows, onde arquivo
  aberto não pode ser apagado e os testes removem diretórios temporários.
- Mesmo cuidado com o banco SQLite do Agno, que já é um engine SQLAlchemy por
  `SqliteDb`, e com o `sessions.db` do `SqliteSessionStore`, se a medição
  mostrar a mesma rotatividade.
- Medir antes e depois: conexões abertas por turno governado e latência de
  `publish` por evento.
- **Aceite:**
  - os testes de aprovação ponta a ponta passam em 5 rodadas seguidas dos dois
    jobs do Windows no mesmo commit;
  - nenhum handle de arquivo fica aberto depois de `close()`;
  - o número de conexões por turno cai em pelo menos uma ordem de grandeza.

## Fora de escopo

- Trocar o SQLite por outro banco.
- Mudar o formato do estado persistido.

## Validação obrigatória

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
```

Mais o CI no Windows conforme o aceite.
