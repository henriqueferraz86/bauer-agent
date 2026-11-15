# ARCHITECTURE — Telegram via AgentOS HTTP

## Fluxo

```text
Telegram → Bridge (allowlist, mídia, entrega) → AgentBackend + Kernel
  → executor agentos_http → AgentOS autenticado → agente/time e tools governadas
  ← estados, deltas e resultado normalizados ← execução remota auditada
```

`agno` continua designando SDK local; `agentos_http` é modo separado e opt-in. A governança de ingresso do Kernel não substitui policy e aprovação de cada ferramenta no processo remoto. O contrato concreto depende de `/openapi.json` da instalação e não deve ser inferido só de documentação ou do adapter existente.

## Componentes e decisões

- Configuração específica de Telegram e credencial por variável de ambiente; sem sobrescrever config inteira ou alterar defaults dos demais consumidores.
- Cliente `httpx` sem dependência de SDK: auth, timeouts, limites, erros tipados, JSON/SSE, descoberta e operações de run conforme contrato observado. Não repetir POST incerto.
- Executor de canal preserva os locks e o `ChannelExecutionRegistry` existentes na base de produção. Escolhe agente/time do catálogo validado e traduz eventos para a interface do Telegram. Não usar substituto silencioso para ID ausente.
- Store transacional mantém ownership, identidade de sessão e geração, chave de idempotência, IDs locais/remotos e estados. Após timeout ou restart, reconciliar sem executar novamente uma ação incerta.
- Política de tools no AgentOS ou Tool Gateway autenticado deve validar identidade e escopo confiáveis; ferramenta de escrita exige aprovação remota verificável e execução única.
- Sessão remota deriva de usuário, chat, projeto e geração persistida; histórico remoto é fonte definida explicitamente. Mídia usa upload ou ingestão compatível, nunca caminho local como anexo remoto.
- Métricas, cancelamento e HITL são reconciliados com o estado remoto; valores ausentes permanecem desconhecidos. Segredos e conteúdo sensível não entram em logs.

## Implantação e rollback

Fase 0 identifica commit, versões, configuração sem segredos, catálogo, auth, rede e backups. Não implantar fluxo HTTP em produção com autenticação ausente, catálogo vazio ou contrato divergente. Fases subsequentes seguem os gates do backlog. Rollback: parar admissões HTTP, reconciliar runs e estados, alterar somente o toggle de Telegram e validar duas conversas; nunca transferir uma run em andamento ou repetir efeito.

## Riscos e observabilidade

A worktree da sprint está baseada no commit de produção `675aedd`; nela existem `telegram_default_adapter`, `_turno_agno()` e contexto de projeto. A worktree original estava 173 commits atrás e suas mudanças exigem revisão antes de portabilidade.

Timeout após envio deixa admissão incerta; rede autenticada não demonstra governança das tools; payload de identidade não é prova de autorização; SSE pode terminar truncado; `202` ou cancelamento aceito não é sucesso terminal. Registrar IDs correlacionáveis, estado, latência e custo conhecido/desconhecido, sem tokens nem conteúdo sensível. Gate de corte depende de evidência, não da mera existência de código.
