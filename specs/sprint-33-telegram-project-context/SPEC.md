# SPEC — Sprint 33: contexto de projeto no Telegram

Plano: `plans/061-telegram-project-control.md`, etapas 0 e 1.
Status: em implementação; base `e22e1aa`, 2026-10-21.

## Objetivo e escopo

Selecionar explicitamente projeto em conversa privada autorizada do Telegram;
persistir o vínculo por operador/chat e manter histórico, modelo, ferramentas,
prompt, tarefas e mídia no workspace efetivo. Conversas sem vínculo continuam
com o comportamento legado até a seleção explícita. Vínculo inválido bloqueia
novas ações e nunca usa fallback silencioso para outro projeto.

Comandos: `/projetos`, `/projeto`, `/tarefas` (alias `/tasks`), e projeto no
`/status`. Botões usam referências opacas vinculadas a usuário/chat/mensagem,
expiram e são revalidados. Listar somente projetos válidos e acessíveis ao
operador autorizado pela política atual do canal; nenhum path arbitrário remoto.
Sem projetos cadastrados, oferecer o workspace configurado como opção explícita.
Os novos controles privilegiados são recusados em grupos.

Fora do escopo desta sprint: cancelamento, atividade, aprovação remota, `/loop`,
nova identidade multicanal, ativação do bot e mudança de orçamento/política.

## Skills e responsabilidades

Skill: `.agents/skills/spec-driven-project-setup/SKILL.md`; regras `AGENTS.md`.
Time: `telegram_projects` implementa contexto/backend; root integra bridge e
contratos; `telegram_review` revisa isolamento/testes; `telegram_runtime` audita
compatibilidade com custódia e futuras etapas, sem implementá-las nesta sprint.

## Aceite e validação

- Dois projetos com arquivos homônimos usam o workspace correto em paralelo.
- Trocar projeto separa histórico/modelo e não redireciona trabalho admitido.
- Restart mantém vínculo; remoção/mudança do projeto bloqueia execução.
- Usuário/chat errado e callback expirado não alteram vínculos.
- Mídia ingressa e sai do workspace efetivo; sem CWD global e sem vazamento
  por symlink/path externo na seleção de mídia de projeto.
- Comandos existentes e canais Discord/Slack preservados.
- Testes herméticos, full pytest, Ruff crítico/amplo, mypy, lock e diff-check
  conforme AGENTS antes da próxima sprint. Sem chamadas a provider real.
