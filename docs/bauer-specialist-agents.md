# Agentes especialistas do Bauer

O `bauer.software_team` reúne nove agentes sob coordenação de Bauer Product.
Agno é o adapter de execução; o Kernel continua responsável por política,
aprovação, auditoria e ciclo de vida da execução.

| ID | Responsabilidade | Ferramentas específicas |
|---|---|---|
| `bauer.product` | Coordenar escopo e delegação | herdadas da definição atual |
| `bauer.dev` | Desenvolver e manter software | herdadas da definição atual |
| `bauer.qa` | Testes, qualidade e critérios de aceite | herdadas da definição atual |
| `bauer.devops` | Operação, ambiente e runtime | herdadas da definição atual |
| `bauer.architect` | Arquitetura, dependências, impactos e planos | leitura, busca no workspace e web |
| `bauer.security` | Revisão de segurança e recomendações | leitura, busca no workspace e web |
| `bauer.research` | Pesquisar fontes e comparar alternativas | web e leitura do workspace |
| `bauer.docs` | Documentação técnica | leitura, busca e escrita governada |
| `bauer.data` | Esquemas, qualidade e fluxos de dados | leitura, busca no workspace e web |

O fallback local seleciona um especialista quando a solicitação contém sinais
claros, por exemplo: revisão de segurança, pesquisa, documentação, análise de
dados ou avaliação arquitetural. Uma tarefa que mencione explicitamente o ID
do agente também pode ser roteada para ele. Jev pode substituir essa decisão
quando estiver ativado, mas não é necessário para o catálogo e o fallback
funcionarem.

Bauer Security não recebe ferramentas de escrita nem execução de comandos. Seu
papel é produzir achados com evidências e recomendações. Bauer Docs pode propor
e gravar documentação usando `write_file`, sempre sujeito às políticas e aos
gates do ToolRouter. Bauer Data começa sem ferramentas mutáveis ou shell.

Para consultar o catálogo e o time no runtime:

```bash
bauer runtime agents list
bauer runtime teams list
```
