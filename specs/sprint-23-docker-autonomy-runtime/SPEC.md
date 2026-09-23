# Sprint 23 — Runtime Docker para autonomia contínua

## Objetivo

Fazer o monitoramento e a recuperação de containers funcionarem quando o
`bauer serve` roda dentro do Docker Desktop.

## Problema

O supervisor chama `docker ps`, `docker inspect`, `docker start` e `docker
restart`. A imagem do Server não contém o Docker CLI e o serviço não recebe o
socket do daemon; por isso os incidentes mostram `[Errno 2] No such file or
directory: 'docker'`.

## Escopo

- Instalar o cliente Docker na imagem do Server.
- Montar `/var/run/docker.sock` somente no serviço `bauer`.
- Manter a descoberta e as ações existentes, com os mesmos limites e
  allowlist.
- Validar o caminho em Docker Desktop no Windows.

## Fora de escopo

- Alterar permissões do daemon Docker.
- Expor o socket ou uma API Docker para o navegador.
- Permitir comandos Docker arbitrários fora das receitas de autonomia.

## Critérios de aceite

1. `docker ps` executa dentro de `bauer-agent`.
2. O Server consegue descobrir os containers e cadastrá-los como alvos.
3. Um alvo Docker ativo pode ser sondado com `docker inspect` sem gerar o erro
   de executável ausente.
4. Uma falha persistida após reinício do Server volta a tentar a receita de
   autocorreção, respeitando cooldown e limite de tentativas.
5. O socket não é montado no `agentos` nem no frontend.

## Validação

- `docker compose config`
- `docker compose build bauer`
- `docker exec bauer-agent docker ps`
- `uv run pytest tests/test_continuous_autonomy.py -q --tb=short`
- `uv run ruff check bauer/ --select E9,F63,F7,F82`
