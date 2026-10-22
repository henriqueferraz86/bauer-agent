# ARCHITECTURE — Sprint 33

`TelegramBridge` valida identidade/allowlist e transporta comandos/botões.
`AgentBackend` mantém vínculo e resolve um snapshot de projeto por mensagem;
router e prompt pertencem a esse snapshot, nunca a uma mutação global por chat.
SQLite sob o estado do gateway guarda vínculos. Callbacks de seleção de uso
limitado ficam em memória; após restart são recusados e o usuário abre um novo
picker. Identidades opacas incluem owner/chat e validação da mensagem.

Reutilizar `projects_registry` e a construção canônica `_build_router`; validar
diretório no momento do uso. Não aceitar paths vindos do callback nem carregar
config de projeto que amplie privilégios do config do gateway. Seleção não muda
o projeto ativo global da interface web. Uma sessão inclui identidade do projeto,
mantendo os identificadores legados para conversas ainda sem seleção.

Propagar snapshot e ContextVars para a thread do turno. O arquivo de mídia é
resolvido no workspace capturado antes do download; validar outbound e symlinks.
Falha no vínculo ou na persistência é erro explícito e não concede acesso.

Kernel continua dono da execução e do estado terminal. Usar o mesmo root do
web (`sessions_dir.parent / "runtime"`), também nos routers e eventos, para que
o budget diário permaneça no store atual e não seja reconfigurado. Propagar IDs
e custo real do turno às métricas do Kernel; falha de telemetria é best-effort,
mas erro de wiring de governança não autoriza execução sem política.
