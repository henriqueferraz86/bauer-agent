# SPEC — Sprint 28: projeto e workspace visíveis no chat

## Problema e objetivo

A tela Projetos marca investimentosOS ativo, mas o chat não mostra seu vínculo.
Mesmo uma conversa nova resumiu um workspace quase vazio. Mostrar o projeto e
workspace reais utilizados pelo chat, mantendo contexto, ferramentas e interface
consistentes. Identificar e corrigir a causa reproduzível da divergência.

## Escopo

- Consulta read-only do contexto efetivo do chat, com precedência existente:
  projeto explícito > vínculo da sessão > projeto ativo > workspace padrão.
- Evento SSE público de contexto antes da resposta; nomes/caminhos do workspace
  são informação útil ao operador e não incluem argumentos/resultados de tools.
- Cabeçalho/faixa do chat com nome e pasta efetivos, vínculo de sessão e aviso
  quando projeto ativo global difere ou resolução cai no workspace padrão.
- Nova sessão consulta o projeto ativo; vínculo existente permanece explícito.
- Contexto do agente usa o router/workspace do turno; correção do fallback real
  encontrado pela investigação, se houver.
- Correção confirmada: no modo Agno, reutilizar o ToolRouter do projeto na
  construção do adapter, incluindo workspace, policy e eventos do turno.
- Testes herméticos e revisão em equipe; build e aplicação no serviço local.

## Fora de escopo

Copiar/espelhar arquivos, trocar credenciais/modelos, sincronizar os dois backends
de memória, migrar sessões silenciosamente, alterar governança ou publicar master
sem pedido específico para esta feature. A mudança preexistente de CI fica fora.

## Skills e equipe

- Obrigatória: `.agents/skills/spec-driven-project-setup/SKILL.md`.
- `project_diagnosis`: causa do workspace divergente, sem editar código.
- `project_backend`: resolução/contexto HTTP/SSE e regressões Python.
- `project_frontend`: indicador de contexto no chat, parsing SSE e testes TS.
- Coordenador: specs, integração, validação e revisão cruzada dos dois lados.
  Um dos agentes faz revisão independente após a implementação.

## Aceite

1. Antes do primeiro envio, chat mostra nome e caminho efetivos ou estado claro
   de carregamento/indisponibilidade; não apresenta informação antiga como atual.
2. Sessão vinculada ao projeto A continua indicando A se o ativo mudar para B.
   O aviso explica que Nova sessão começa em B, sem trocar histórico de pasta.
3. Nova sessão usa e exibe B; evento SSE autoritativo confirma contexto resolvido.
4. Nome/pasta/prompt/brief/tools correspondem ao mesmo workspace real do turno.
5. Projeto inválido/pasta ausente/falha no builder sinalizam fallback real.
6. Navegar/recarregar não perde contexto; metadados SSE não viram texto do Bauer.
7. Testes da resolução/turno real controlado, testes frontend, build, suíte Python,
   gates Ruff e revisão passam. Testes nunca usam configuração/provider reais.

## Validação

Ambiente `uv sync --frozen --extra dev`; testes direcionados com tmp_path;
`uv run pytest tests/ -q --tb=short`; ambos os gates Ruff 0.17.0 de AGENTS;
`npm test` e `npm run build` em desktop; diff-check. Verificar tela e metadados
do serviço local sem escrita nos arquivos dos projetos nem providers nos testes.
