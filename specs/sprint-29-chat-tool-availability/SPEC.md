# SPEC — Ferramentas disponíveis no chat

## Problema e objetivo
O serviço usa shell_enabled=false, ocultando run_command. Agno restringe o
chat a sete ferramentas apesar de outras estarem autorizadas pelo ToolRouter.
Disponibilizar ferramentas registradas e autorizadas no workspace efetivo,
com pedido explícito ao usuário quando um comando está fora da allowlist.

## Escopo
- Habilitar shell na configuração local do serviço, mantendo safe_mode.
- Expor schemas Bauer autorizados no Agno sem limitar artificialmente a sete.
- Reutilizar execução governada do ToolRouter; preservar sandbox/policies.
- Verificar pergunta de allowlist em /chat e /stream com Agno e native.
- Consumir autorização/recusa explícita em sessão e workspace vinculados,
  persistindo permissão verificada sem executar comandos pelo helper.
- Corrigir prompt que afirma terminal disponível quando shell está desligado.

## Fora de escopo
Desativar política/denylist/safe_mode; habilitar toolkits arbitrários Agno;
liberar comandos automaticamente; mudar defaults globais ou providers reais nos testes.

## Skills obrigatórias
- spec-driven-project-setup (já aplicada nesta entrega).

## Sub-agents
- project_backend: bridge genérica Agno e testes do adapter.
- project_diagnosis: diagnóstico/revisão da autorização de comandos.
- root: servidor, configuração local, integração, gates e entrega.

## Critérios de aceite
1. run_command disponível após habilitar shell no serviço.
2. Ferramentas autorizadas adicionais têm nome, descrição e parâmetros corretos no Agno.
3. Tools não registradas ou não autorizadas não são anunciadas e não executam.
4. Comando fora allowlist gera pergunta explícita; não executa antes de autorização.
5. Ambos engines/transportes preservam comportamento e logs governados.
6. Testes herméticos, gates Python e mypy passam.
7. Confirmação solta, citada, expirada ou de outra sessão/workspace não libera
   comandos; recusa e falha de persistência não anunciam sucesso.

## Plano de validação
uv sync frozen/dev; regressões adapter/chat/router/shell; suíte completa;
Ruff 0.17.0 bloqueante e estilo; mypy; verificação local de schemas sem LLM.
