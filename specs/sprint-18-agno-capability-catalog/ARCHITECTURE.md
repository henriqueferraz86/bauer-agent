# Arquitetura — Sprint 18

## Visão geral

Adicionar um catálogo Bauer explícito e local entre AgentSpec e
`AgnoRuntimeAdapter`. O catálogo descreve integração e ações sem instanciar
toolkits. Para integrações implementadas, uma factory registrada em código
materializa wrappers durante construção do agente; para demais entradas, o
estado explica dependências/configuração ou incompatibilidade. O adapter aceita
somente IDs declarados e resolve factories pelo catálogo.

## Componentes

- `AgnoCapabilityCatalog`: manifesto tipado, estados e resolução de capability.
- `AgnoRuntimeAdapter`: valida ferramentas por agente, constrói wrappers e
  mantém a fronteira de execução Agno.
- `ToolRouter`: autoridade de execução para ferramentas Bauer; policies,
  workspace e allowlist não são reimplementados no catálogo.
- `/api/agno/catalog`: leitura do catálogo e seu status, filtrado para campos
  públicos.
- Desktop: mostra categoria, estado e requisitos para orientar configuração.

## Fluxo

```text
AgentSpec (IDs declarados)
    → validação contra catálogo local (sem imports/network)
    → verificação de enabled, ações, deps e configuração
    → factory registrada no código Bauer
    → wrapper tipado
    → ToolRouter / policy / workspace / approval
    → evento redigido no EventBus do Kernel
```

## Decisões técnicas

- IDs do catálogo são estáveis e semânticos (`bauer.read_file`, `agno.github`).
- Manifestos descrevem risco e ações; configuração do usuário só escolhe IDs e
  ações — nunca `import_path`, código Python ou callable.
- Discovery consulta somente metadata estática. Extras opcionais são consultados
  via `importlib.util.find_spec` apenas para detectar disponibilidade; nunca
  importados pela tela de descoberta.
- Factory map é allowlist fixa em código. Um plugin futuro exige integração
  formal e revisão; entry points arbitrários não são executados implicitamente.
- Tools desconhecidas deixam de ser ignoradas silenciosamente. Um agente com
  declaração inválida falha na admissão/configuração, antes de chamar o provider.
- ToolRouter continua dono de permissões e execução; wrappers Agno não duplicam
  regras de segurança.
- API reusa guard de autenticação/autorização já aplicado às rotas desktop.
- Status da capability é calculado sem gravar na config e sem revelar se um
  segredo específico tem determinado valor; revela somente “configurado” ou
  “faltando”.

## Alternativas consideradas

- **Importar e enumerar todas as classes Python:** rejeitado; pode carregar
  extras pesados, executar código de import e acoplar boot ao catálogo inteiro.
- **Aceitar module path/factory vindo do YAML:** rejeitado; permite execução
  arbitrária por configuração e contorna revisão de código.
- **Manter descarte silencioso de nome desconhecido:** rejeitado; mascara
  configuração que parece ativa mas não entrega a tool esperada.
- **Dar permissões ao Team em bloco:** rejeitado; perde princípio de menor
  privilégio por agente.

## Observabilidade e privacidade

Registrar tool/capability ID, agente, run, ação, estado e motivo categórico de
negação. Não registrar inputs, outputs, prompts, API keys ou texto de exceção
que possa incluí-los. Erros ao descobrir pacote devem conter só nome público do
extra e orientação de configuração.

## Riscos técnicos

- Assinaturas de funções Agno variam entre toolkits; esta sprint só adapta
  wrappers Bauer conhecidos e mantém o restante como não implementado/sem
  factory.
- `find_spec` pode falhar para namespaces dinâmicos; tratar como indisponível,
  sem importar o pacote como fallback.
- Catálogo dinâmico vindo de versões futuras pode introduzir ações novas; um
  upgrade do SDK não deve habilitá-las sem atualização revisada do manifesto.
