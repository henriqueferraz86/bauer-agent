# BACKLOG — Sprint 31: aprovação no Chat

| ID | Tarefa | Status | Verificação |
|---|---|---|---|
| A31-01 | Especificação e arquitetura do fluxo | done | arquivos desta pasta |
| A31-02 | Emitir aprovação estruturada e aguardar decisão no `/stream` | done | testes SSE/backend |
| A31-03 | Renderizar cartão inline no Chat | done | build TypeScript |
| A31-04 | Impedir cancelamento indevido enquanto aguarda aprovação | done | teste de ciclo de vida |
| A31-05 | Aguardar aprovações criadas durante tool calling | done | teste de policy + SSE |
| A31-06 | Propagar governança para routers internos do Agno | done | teste adapter + SSE |
| A31-07 | Unificar configuração do ShellRunner Agno com `tools` | done | teste adapter + Docker |
| A31-08 | Revisão de segurança, testes e lint | partial | gates focados, desktop e ressalvas da suíte completa |
| A31-09 | Decisão explícita once/run, idempotência e cancelamento | done | kernel/router + isolamento |
| A31-10 | Cartões por ID e status fiel no chat | done | testes UI e build |
| A31-11 | Preflight de comandos e ambiente Docker/Agno | done | shell + imagem Compose |
| A31-12 | Integração real Agno/API/SSE e revisão adversarial | done | efeito único e validação operacional |
| A31-13 | Card para binário fora da allowlist e autorização limitada à chamada | done | 22 testes E2E/escopo em execução serial; gates Ruff; instância da captura ainda não atualizada |

Validação A31-13 (2026-10-17): `uv sync --frozen --extra dev` e ambos os gates
Ruff passaram. Os testes focados de router, adapter, shell e stream passaram.
Na suíte completa houve sete falhas: cinco esperas/timeouts no E2E de
aprovação (todos passaram ao repetir o arquivo sem concorrência), mais
`test_arquitetura_custodia_kernel` (catraca 6 vs 4) e
`test_stream_timeout_nao_sobrescreve` (tentativa de sobrescrever run terminal).
Esses dois últimos envolvem alterações de Kernel/state store já presentes
no workspace antes desta correção. A suíte global permanece sem aprovação.
O teste novo garante cleanup mesmo quando a espera inicial falha. Não houve
rebuild/deploy da instância exibida pelo usuário.

Integração de publicação (2026-10-17): branch isolada sobre origin/master,
preservando contexto de projeto, streaming persistente, orçamento e CAS do
Kernel atuais. Cards são consumidos por chatStream.ts; navegação mantém
aprovações e os controles de decisão. Frontend: 94 testes e build aprovados.
Backend: testes focados aprovados; suíte global em validação antes do deploy.
