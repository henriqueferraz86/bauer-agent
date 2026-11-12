# SPEC — Sprint 83: rebuild Docker pelo agente

## Pedido

2026-11-12: `run-77c15e97` encerrou com "o agente encerrou sem cumprir o
objetivo". A fase 011 (T0011) do investimentosOS estava com os testes verdes,
mas a Decisão 013 do projeto exige `docker compose -f compose.yml build`
antes de fechar a tarefa, e o agente não tinha como rodar isso. O usuário
autorizou liberar `docker`.

## Causas

1. `docker` fora de `tools.extra_allowed_commands` (config do operador).
2. A imagem tem `docker-cli`, mas não o plugin `compose`: `docker compose`
   respondia "'compose' is not a docker command".
3. `tools.timeout_seconds` tinha teto de 300 s (padrão 30 s); o build leva
   34 s com cache e bem mais sem.

## Comportamento

- A imagem instala `docker-compose` e `docker-buildx` (Debian trixie).
- `tools.timeout_seconds` aceita até 3600. O padrão continua 30.
- Liberar `docker` continua sendo decisão do operador, no `config.yaml`.
  Com o socket do Docker montado, isso equivale a root no host.
