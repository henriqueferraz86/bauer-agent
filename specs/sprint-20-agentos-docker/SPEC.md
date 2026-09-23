# Sprint 20 — Stack Docker do Bauer, AgentOS e Agent UI

## Objetivo

Quando o instalador do Bauer encontrar Docker Compose, provisionar o stack
completo do Bauer: Ollama, Bauer HTTP, AgentOS e Agent UI oficial do Agno.
Também deve ser possível executar o mesmo stack manualmente com `docker compose`.

## Escopo

- adicionar o serviço AgentOS ao Compose existente;
- construir o Agent UI oficial (`agno-agi/agent-ui`) em uma imagem própria;
- persistir modelos Ollama, workspace, memória e logs em volumes nomeados;
- fazer o instalador detectar Docker Compose e subir o stack automaticamente;
- permitir `--no-docker` e `--docker` para controlar o comportamento automático;
- documentar portas, configuração do endpoint e limitações de segurança;
- documentar instalação manual no Windows + Docker Desktop;
- adicionar testes estáticos para o contrato do Compose e do instalador.

## Fora de escopo

- substituir o servidor Bauer/Kernels por AgentOS;
- publicar uma imagem no Docker Hub;
- instalar Docker no sistema operacional;
- incluir credenciais ou expor Ollama diretamente;
- prometer alta disponibilidade ou produção multi-host.

## Critérios de aceite

1. `docker compose config` valida o arquivo sem interpolação quebrada.
2. O Compose define `bauer`, `ollama`, `ollama-init`, `agentos` e `agent-ui`.
3. AgentOS publica a porta 7777 e Agent UI publica a porta 3000.
4. AgentOS usa o mesmo catálogo formal do Bauer e a mesma rede/volumes de runtime.
5. A imagem do Agent UI usa o repositório oficial e uma revisão parametrizada.
6. Instalação sem Docker continua possível; `--no-docker` nunca chama Docker.
7. `--docker` falha claramente quando Docker Compose não está disponível.
8. Atualizações preservam `config.yaml` e não removem volumes Docker.
9. Os testes novos não acessam Docker nem a rede.
10. O runbook documenta instalação, atualização, volumes, acesso remoto e
    diagnóstico para Linux/macOS e Windows.

## Validação

- `uv sync --frozen --extra dev`
- `uv run pytest tests/test_docker_stack.py tests/test_install_update.py -q`
- `uv run ruff check bauer/ --select E9,F63,F7,F82`
- `docker compose config` em máquina com Compose disponível
- smoke test das portas 8000, 7777 e 3000 após `docker compose up -d --build`
