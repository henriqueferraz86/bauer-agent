# Arquitetura — Sprint 16

As definições dos cinco agentes ficam em `bauer/data/agent_specs/<id>/agent.yaml`
e são descobertas pelo `RuntimeAgentRegistry`. `bauer.software_team` referencia
os nove IDs e mantém `bauer.product` como supervisor. O `AgnoRuntimeAdapter`
materializa os membros a partir do registro formal; cada agente recebe somente
as ferramentas declaradas na própria especificação.

O fallback local continua dentro do `decision_router`: sinais claros de
segurança, pesquisa, documentação, dados e arquitetura escolhem o agente
especialista antes dos mapeamentos genéricos por tipo de tarefa. O agente
selecionado e o runtime `agno` seguem no snapshot da decisão governada pelo
Kernel.

`search_text` passa a ser materializada como ferramenta Agno e entra na
allowlist interna do ToolRouter usada pelo adapter. A função envia o argumento
`pattern` exigido pela tool original e mantém o isolamento e a política do
ToolRouter.

Bauer Security declara somente `read_file`, `list_dir`, `search_text` e
`web_search`; não recebe ferramentas de escrita, shell ou delegação. Bauer Docs
recebe `write_file`, mas a escrita continua sujeita aos gates e às políticas
globais do ToolRouter. O Bauer Data começa sem ferramentas mutáveis ou shell.
