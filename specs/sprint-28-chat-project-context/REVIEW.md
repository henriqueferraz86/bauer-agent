# Revisão — Sprint 28

Revisão independente por `project_diagnosis`, com integração pelo coordenador.

## Achado corrigido

O hint legado de projeto ativo global podia orientar uma sessão vinculada ao
projeto pai A a trabalhar no filho B recém-ativado. Removido: prompt, brief,
tools e indicador agora compartilham o workspace efetivo. Regressão cobre pai
fixo na sessão com filho ativo global.

Também ajustado o aviso da interface: fallback para workspace padrão não sugere
que Nova sessão solucionará uma pasta ausente. Metadados inválidos/indisponíveis
não mantêm caminho antigo apresentado como confirmado.

## Resultado

Sem achados bloqueantes após correções. Agno reutiliza a identidade do router,
mantendo sandbox, policy, EventBus e IDs de execução. Consulta de contexto é
protegida e não cria sessão/run/provider. SSE confirma contexto antes do texto,
inclusive na admissão bloqueada. Sessões preservam seu projeto; consultas HTTP
atrasadas não sobrescrevem SSE ou Nova sessão. Frontend preserva metadados na
navegação e mostra carregamento antes de confirmar contexto após reload.

O mapa explícito de contrato HTTP inclui a operação nova e seus query params.
A mudança preexistente de `.github/workflows/ci.yml` não faz parte da feature.
