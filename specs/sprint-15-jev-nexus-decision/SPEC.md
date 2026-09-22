# SPEC — Jev como camada de decisão do Bauer/Nexus

## Objetivo

Transformar o Jev em uma camada opcional de decisão estruturada para o Bauer/Nexus, mantendo os LLMs e o Agno responsáveis pela execução complexa.

## Escopo

- Retornar distribuição de probabilidades entre alternativas e confiança.
- Selecionar profile, runtime, time e agente a partir do catálogo disponível.
- Recomendar ferramentas, estratégia e plano estruturado sem executar nada diretamente.
- Consultar e registrar decisões na DecisionMemory para aprendizado posterior.
- Integrar a decisão ao Kernel antes de policy, approval e execução.
- Manter fallback heurístico local quando Jev estiver sem chave, indisponível ou abaixo do limiar.
- Manter Jev desligado por padrão.

## Fora de escopo

- Chamar a API Jev sem chave configurada.
- Permitir que a resposta do Jev execute tools ou ignore policy/approval.
- Migrar automaticamente todos os executores legados para Agno Team.
- Treinar um modelo ou alterar pesos locais.

## Critérios de aceite

1. A decisão possui probabilidades normalizadas, alternativa escolhida, confiança, runtime, team, agent, tools, strategy e plan.
2. Resposta inválida ou ausência de Jev cai para decisão heurística segura.
3. Tools recomendadas são limitadas ao catálogo permitido.
4. Kernel registra a decisão antes da execução e a mantém sujeita a policy, approval, budget e gates.
5. DecisionMemory recebe a decisão e permite feedback posterior por sessão.
6. Nenhum caminho faz chamada externa quando Jev está desligado.
7. Testes cobrem parsing, fallback, catálogo, memória e wiring do Kernel.

## Validação

- `uv sync --frozen --extra dev`
- testes específicos da sprint
- `uv run pytest tests/ -q --tb=short`
- gates de Ruff e CI
