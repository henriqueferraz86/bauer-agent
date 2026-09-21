# Sprint 11 — Maturidade do runtime

## Objetivo

Reduzir os pontos de instabilidade identificados na revisão do Bauer: tornar a
memória acessível por uma única fachada, retirar a montagem de decisões de
rota de caminhos duplicados e garantir que o servidor local permaneça
responsivo quando a suíte roda em paralelo no Windows.

## Escopo

- Criar uma fachada de memória que coordene a memória Markdown legível pelo
  agente e a memória runtime auditável, sem apagar nem migrar dados antigos.
- Usar a fachada nos comandos de memória para que novas escritas tenham
  sincronização explícita e resultados de busca mostrem a origem.
- Extrair a normalização de decisão e evento de rota para um módulo pequeno e
  compartilhado por servidor, agente e execução autônoma.
- Tornar o servidor Kanban concorrente e tolerante a clientes que fecham a
  conexão durante uma resposta.
- Documentar a arquitetura e validar com a suíte completa e os gates do CI.

## Fora de escopo

- Reescrever `agent.py` ou `server.py` por completo.
- Remover os formatos Markdown, JSONL ou `.bauer_memory.json` existentes.
- Fazer migração destrutiva de memórias ou alterar o contrato público dos
  comandos atuais.
- Alterar o comportamento de providers reais.

## Critérios de aceite

1. `bauer memory` mantém os comandos existentes e oferece uma operação
   sincronizada que grava uma memória auditável e uma nota legível.
2. A busca unificada retorna resultados de ambos os backends sem duplicação
   evidente e tolera falha acessória do Markdown.
3. Servidor e agente usam a mesma função para construir a decisão de rota e o
   payload do evento.
4. O endpoint `/api/ops` passa repetidamente sob `pytest-xdist` no Windows.
5. Os testes, Ruff crítico, mypy e `uv lock --check` passam no ambiente
   definido pelo repositório.
