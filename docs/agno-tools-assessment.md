# Integração de ferramentas Agno no Bauer

**Referência consultada:** [catálogo oficial de exemplos de tools do Agno](https://docs.agno.com/examples/tools/overview) e [documentação oficial do SDK](https://docs.agno.com/llms.txt).

## Situação observada no Bauer

O Agno documenta mais de 100 integrações e exemplos, cobrindo busca e
extração web, GitHub, browser, Python, dados/SQL, armazenamento, mensageria,
voz e geração de mídia. O adapter Agno do Bauer mapeia hoje um conjunto curto
de ferramentas próprias: leitura/escrita/listagem/busca de arquivos, comando,
busca web e memória. Portanto, as integrações nativas do SDK não ficam
disponíveis automaticamente aos agentes.

## Prioridade recomendada

| Prioridade | Área | Candidatos | Aplicação no Bauer |
|---|---|---|---|
| P0 | Ferramentas próprias do Bauer | Workspace, busca web, memória e browser via ToolRouter | Reutilizar as políticas, o workspace isolado, aprovações e auditoria existentes |
| P1 | Desenvolvimento e pesquisa | GitHub em leitura, busca/extrator web, calculadora e MCP com servidores configurados | Bauer Architect, Dev, QA e Research |
| P1 | Dados | CSV, Pandas e visualização; SQL read-only com credencial dedicada | Bauer Data, com limite de escopo e execução sem escrita por padrão |
| P2 | Browser e testes | Agno browser/Playwright ou os adaptadores Playwright existentes no Bauer | Usar uma única sessão controlada e registrar início/fim/navegação sem conteúdo privado |
| P2 | Infraestrutura | Docker/AWS/EKS com operações de leitura e inventário primeiro | Bauer DevOps; mutações exigem policy e aprovação do Bauer |
| P3 | Integrações de escrita | Slack, email, Jira/Linear, calendário, SMS e publicação social | Habilitar por conexão explícita, escopo mínimo e aprovação antes de efeitos externos |

## Regra de integração

Não adicionar todos os toolkits ao `Team` por padrão. Cada ferramenta deve ser
declarada na especificação do agente, resolver credenciais fora do código,
passar pelo caminho de policy/aprovação do Bauer quando produz efeitos e emitir
eventos de tool com nome, agente, run e estado. Argumentos e resultados ficam
fora da telemetria por padrão. Toolkits opcionais não devem impedir o Bauer de
iniciar quando a dependência ou credencial não estiver instalada.

O catálogo do Agno demonstra integrações de terceiros, mas isso não equivale a
uma permissão operacional segura. Há ferramentas de leitura junto com outras
que podem escrever, apagar, enviar mensagens, executar código ou transferir
fundos; cada uma precisa de análise e limites próprios antes de ser exposta.

## Próxima etapa sugerida

Após o painel de atividade, implementar um registro configurável de ferramentas
Agno em incrementos pequenos: primeiro ferramentas locais já cobertas pelo
ToolRouter, em seguida leitura GitHub/web/MCP e ferramentas de dados read-only.
Medir cobertura, custo, falhas e aprovações antes de integrar ações externas.
