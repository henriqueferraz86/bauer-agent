# Sprint 18 — Catálogo Agno e capabilities governadas

## Problema

O `AgnoRuntimeAdapter` só materializa sete nomes de tools Bauer, descartando
silenciosamente nomes desconhecidos. O catálogo Agno inclui muitos toolkits
opcionais; eles não estão todos instalados com `agno` e não podem ser
carregados/importados indiscriminadamente no boot do Bauer.

## Objetivo

Criar a primeira camada de catálogo para que as integrações Agno compatíveis
sejam enumeráveis e configuráveis por agente, sem executar imports de terceiros
durante a descoberta, conceder permissões implícitas ou duplicar policy. O
resultado desta sprint é a fundação extensível e a exibição do estado; cobertura
de todos os toolkits segue nas sprints do Plano 057.

## Escopo

- Registro tipado e central de toolkits/capabilities, com ID estável, descrição,
  ações explícitas, perfil de risco, módulos/dependências opcionais e requisitos
  de configuração/credencial sem valores secretos.
- Estados de catálogo: desabilitada, pronta, precisa configuração, dependência
  ausente e bloqueada por policy.
- Materializar somente factories registradas em código Bauer. Configuração
  seleciona IDs/ações; nunca fornece module path, import dinâmico ou callable.
- Validar tools declaradas por agente; nome/ação desconhecido gera erro claro,
  em vez de ser descartado silenciosamente.
- Adaptar as tools Bauer existentes via ToolRouter e policy Bauer, sem criar
  chamadas paralelas para executar a mesma ação.
- Endpoint de leitura para catálogo/status, sem retornar secrets ou parâmetros
  privados, e apresentação no Desktop.
- Atualizar docs e avaliação do harness com os contratos do catálogo.

## Fora de escopo

- Completar nesta sprint adapters para todos os toolkits do Agno.
- Instalar dependências, descobrir MCP servers, importar módulos arbitrários ou
  efetuar chamadas externas durante listagem do catálogo.
- Ativar capabilities automaticamente ou concedê-las a todos os agentes.
- Habilitar operações mutantes/externas sem approval; nenhuma escrita nova será
  adicionada nesta sprint.
- Substituir ToolRouter, Kernel, política de aprovação ou stores de memória.
- Alterar providers, credenciais, áudio/TTS, allowlists existentes ou config de
  usuário durante upgrade.

## Requisitos funcionais

1. Cada entrada tem ID estável, categoria, descrição, ações, risco, origem,
   dependências opcionais, configuração necessária e factory Bauer registrada
   ou estado `unsupported`.
2. Descobrir/listar entradas é determinístico, local, read-only e não importa os
   módulos dos toolkits opcionais.
3. Uma capacidade fica indisponível se não estiver habilitada para o agente,
   faltar dependência/configuração ou policy negar a ação.
4. Agente recebe apenas IDs e ações declarados; tool ou ação desconhecida causa
   erro descritivo antes de montar/executar o Agno Agent/Team.
5. Tools Bauer aceitas são wrappers de assinatura tipada que passam pelo
   `ToolRouter.execute_native_call`; argumentos/respostas continuam sujeitos às
   políticas já existentes.
6. API/UI mostram estado e requisitos não sensíveis; não expõem key, endpoint
   privado, args/results, prompts ou conteúdo de usuário.
7. Dependência ausente não impede o Bauer/servidor de iniciar; a UI explica que
   capability não pode ser ativada até instalação/configuração explícita.
8. Configuração anterior sem seção de catálogo mantém o mesmo comportamento.

## Requisitos não funcionais e segurança

- Default deny e fail-closed para identificadores/ações não catalogados.
- Sem imports reflexivos, execução de código, network calls ou auto-instalação
  durante discovery.
- Não alterar o escopo de workspace/usuário, allowlist efetiva ou custódia do
  Kernel.
- Telemetria identifica capability/ferramenta e estado, sem argumentos,
  resultados ou credenciais.
- Sem regressão na inicialização quando Agno ou qualquer extra opcional não
  estiver instalado.
- A instalação/update preserva integralmente configuração e serviços já
  funcionais; novos campos são opt-in e migração não destrutiva.

## Skills obrigatórias

- `spec-driven-project-setup`
- `security-review`
- `test-strategy`

## Sub-agents recomendados

- `spec-architect`: conferir contratos e limites do catálogo.
- `backend-implementer`: implementar registro, adapter e endpoint.
- `security-reviewer`: verificar default deny, imports, policy e redaction.
- `test-engineer`: testes herméticos de configuração ausente e negativa.
- `code-reviewer`: verificar a fronteira Kernel/Agno e compatibilidade.

## Critérios de aceite

- [ ] Listagem do catálogo não importa toolkits opcionais nem toca rede.
- [ ] Tool desconhecida/ação não declarada falha antes de iniciar execução.
- [ ] Wrappers Bauer passam pelo ToolRouter e sua política de contexto.
- [ ] Dois agentes com allowlists distintas recebem capabilities distintas.
- [ ] Pacote opcional ausente aparece no status e não impede boot.
- [ ] Endpoint/UI não apresentam credenciais, argumentos ou conteúdo de tool.
- [ ] Config antiga produz o mesmo comportamento e o update preserva dados.
- [ ] Cenários de chamadas permitidas/negadas, erro, redaction e dependência
  ausente têm testes herméticos.
- [ ] Suíte, Ruff, mypy e build Desktop aplicáveis passam.

## Riscos e mitigação

- **Catálogo upstream grande e variável:** separar metadados de factories e
  versionar a cobertura em manifestos; não deduzir segurança pelo nome.
- **Toolkit Agno com ações mistas:** classificar cada ação individualmente;
  capability sem classificação não é carregada.
- **Dois caminhos para a mesma ferramenta:** reutilizar ToolRouter e rejeitar
  wrapper Agno que execute fora dele.
- **Compatibilidade entre versões:** testar contra versão do lock e relatar
  explicitamente toolkit indisponível/incompatível.
- **Config local sensível:** manter discovery read-only e não escrever config
  no fluxo de atualização.

## Plano de validação

- Testes unitários do registro: ordenação estável, estados, action allowlist e
  import lazy (sentinela que falha se o módulo opcional for importado).
- Testes do adapter: rejeição de ferramenta não mapeada e chamada pelo
  ToolRouter mockado com nome/args corretos.
- Testes API/Desktop herméticos: status e campos públicos, sem secrets.
- Teste de paridade com config antiga sem bloco Agno catalog.
- `uv sync --frozen --extra dev`; suíte completa; Ruff crítico e estilo; mypy;
  `npm run build` em `desktop/`.
- Cobrir Windows para paths/config; opcional Agno apenas em teste de integração
  isolado, sem provider real.
