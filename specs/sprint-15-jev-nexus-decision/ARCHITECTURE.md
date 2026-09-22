# ARCHITECTURE — Jev como camada de decisão do Bauer/Nexus

O `decision_router` passa a produzir um `RouteDecision` completo. A decisão é uma recomendação serializável: probabilidades por profile, alternativas, runtime, team, agent, tools, strategy, plan e referências de memória. Ela não possui acesso a ToolRouter e não executa efeitos.

O catálogo é resolvido pelo Bauer a partir de `RuntimeAgentRegistry` e `TeamRegistry`. O Jev recebe somente nomes, capacidades e ferramentas já permitidas para o contexto. O retorno é validado e normalizado; ferramentas desconhecidas são descartadas.

O Kernel pode solicitar uma decisão no estágio `planning`. Ele publica `decision.selected`, grava a decisão no input auditável do run e registra o sinal em `DecisionMemory`. A decisão não substitui `PolicyEngine`, `ApprovalManager`, `BudgetManager` ou os gates. O executor recebe apenas metadados selecionados e continua sob custódia.

Sem Jev, o mesmo contrato é preenchido por heurística local. Com Jev ativo e sem chave, o fallback continua local. Com uma chave, o Jev pode comparar alternativas e devolver a distribuição completa; o Bauer ainda aplica o limiar de confiança e o catálogo local.
