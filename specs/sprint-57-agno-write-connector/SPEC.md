# SPEC — Sprint 57: primeiro conector com escrita e aprovação (plano 057, sprint "25")

Depende das sprints 50 (ponte) e 51 (GitHub leitura). Executada como 57.

## Conector escolhido

**Criar issue no GitHub** (`bauer.github_issues:create_issue`): caso de uso
real (agente de pesquisa/dev reporta um problema), baixo impacto
(reversível: issue pode ser fechada), sem dependência nova (`httpx`).

## Escopo

1. Capability **separada** da leitura (`bauer.github` não ganha ação de
   escrita). Começa `needs_configuration`: exige seção própria com allowlist
   de repositórios, nome de credencial e labels permitidas; nunca entra no
   grant padrão (`default_actions` vazio).
2. Operação `integration.write` com **piso de aprovação** no PolicyEngine
   (`ALWAYS_ASK_OPERATIONS`): nenhuma regra/arquivo de policy rebaixa para
   `allow` (ainda pode `deny`). O modo `audit` do router (default do serve
   local) **não** auto-aprova essas operações. Com policy desligada, a ponte
   recusa a escrita.
3. Aprovação mostra alvo, efeito e identidade: resumo
   `Criar issue em <repo>: "<título>" (identidade: <nome da credencial>)`
   via `approval_summary` registrado por instância no router.
4. Aprovação vinculada (ApprovalManager existente) a run, sessão, tool e
   **argumentos exatos** (digest), uso único; negada/expirada nunca autoriza;
   argumento alterado exige nova aprovação.
5. Validação local antes da aprovação (`preflight`): repo fora da allowlist,
   label não permitida, argumento desconhecido ou limites ⇒ erro sem pedido
   de aprovação e sem rede.
6. Idempotência: chave = hash(run, repo, título, corpo, labels); ledger local
   em `<policy_root>/agno_capabilities/github_issues_ledger.json` e marcador
   `<!-- bauer-idempotency: … -->` no corpo, pesquisado antes do POST ⇒
   retries (do agente, do Kernel ou após timeout) não duplicam.
7. Falha remota ⇒ `ToolError` explícito (HTTP, timeout com "efeito
   incerto", JSON malformado); o run não vira `completed` falso.
8. Eventos: `approval.requested/accepted/denied` e `tool.call.*` já
   existentes, sem argumentos/resultados (capability) e sem token.

## STOP respeitado

Nenhum outro conector de escrita foi adicionado; a sprint 58 cataloga os
demais conectores com escrita como `blocked_by_policy` até revisão deste
piloto em ambiente de teste.

## Critérios de aceite

- [x] Sem aprovação nenhuma escrita remota (enforce, audit, policy custom
  permissiva, policy off).
- [x] Aprovação não reaproveitável para outro alvo/argumento; uso único.
- [x] Retries não duplicam efeito (ledger e marcador remoto).
- [x] Falhas externas explícitas; run `failed`.
- [x] Suíte e Ruff verdes.
