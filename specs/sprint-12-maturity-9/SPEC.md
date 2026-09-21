# Sprint 12 — Bauer 9/10

## Objetivo

Elevar a maturidade de código do Bauer de 8/10 para 9/10 por meio de gates
mensuráveis: diminuir dívida estática, usar a memória unificada no caminho
automático do agente e tornar explícita a fronteira entre execução governada e
compatibilidade legada.

## Escopo

- Fazer o `UnifiedMemory` implementar o contrato `MemoryProvider`, permitindo
  que o agente use um único backend composto para leitura e escrita de memória.
- Integrar a memória unificada ao provider local padrão sem remover providers
  externos nem alterar configurações existentes.
- Adicionar um diagnóstico de maturidade reproduzível com score por dimensão,
  evidências e limiares para 9/10.
- Reduzir os avisos Ruff informativos do pacote para no máximo 10, corrigindo
  apenas problemas mecânicos e sem mudar comportamento.
- Adicionar testes do provider unificado, do diagnóstico e dos gates de
  compatibilidade.

## Fora de escopo

- Reescrita completa de `agent.py`, `server.py` ou `tool_router.py`.
- Remoção de providers externos, do formato Markdown ou do runtime auditável.
- Ativação obrigatória do Jev ou de qualquer provider remoto.
- Declarar 9/10 sem passar todos os critérios de aceite.

## Critérios de aceite

1. O provider local padrão escreve e busca pela fachada unificada; os hooks
   existentes de início/fim de turno continuam funcionando.
2. `bauer maturity` produz score determinístico, com score global >= 9.0
   apenas quando todos os gates definidos estão verdes.
3. O Ruff informativo em `bauer/` fica com no máximo 10 ocorrências.
4. Testes serial e paralelo, Ruff crítico, mypy e lock continuam verdes.
5. A documentação identifica claramente o que ainda é legado e o caminho de
   migração, sem esconder compatibilidade existente.
