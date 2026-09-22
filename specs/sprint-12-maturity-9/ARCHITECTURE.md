# Arquitetura

## Provider unificado

`UnifiedMemory` implementa `MemoryProvider`. O runtime auditável continua
guardando identidade, escopo, confiança e validade; a projeção Markdown é a
representação humana. O provider expõe `recall`, `remember`, `on_turn_start`,
`on_turn_end` e `name`, preservando o contrato usado pelo agente.

Providers externos continuam selecionáveis. Quando o provider local é usado,
ele passa a ser a implementação composta; não há migração automática
destrutiva.

## Diagnóstico de maturidade

O diagnóstico mede cinco dimensões independentes: qualidade estática, testes,
governança, memória e modularidade. Cada dimensão tem evidências locais e
limiar. O resultado inclui os itens que impediram a nota 9, evitando que um
percentual de testes seja confundido com maturidade de código.

## Compatibilidade legada

O caminho legado fica explicitamente nomeado no diagnóstico e na documentação.
Ele pode continuar existindo enquanto consumidores são migrados, mas novas
integrações devem usar `UnifiedMemory`, `routing_runtime` e as entradas do
Kernel.
