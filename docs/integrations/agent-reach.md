# Integração: Agent Reach

Repo: https://github.com/Panniantong/agent-reach (MIT)

## O que é (confirmado no código-fonte, não só no README)

Agent Reach **não** é um wrapper nem expõe MCP próprio. Citação direta de
`agent_reach/core.py` e `CLAUDE.md` do repositório: *"Positioning: installer +
doctor + config tool. NOT a wrapper — after install, agents call upstream
tools directly."*

Ele tem exatamente 11 subcomandos: `setup, install, configure, doctor,
uninstall, skill, format, transcribe, check-update, watch, version` — nenhum
deles busca conteúdo. Quem busca conteúdo são as ferramentas upstream que ele
instala e configura (`gh`, `yt-dlp`, `bili`, `twitter-cli`, `opencli`, `rdt`,
`curl`, `mcporter`), cada uma com sua própria sintaxe.

**Por isso a integração no Bauer é uma skill de conteúdo, não uma tool nova.**
O que faltava não era capacidade de execução (o Bauer já tem `run_command`) —
era o modelo saber qual comando upstream chamar por plataforma. Ver
`bauer/data/skills/web/agent-reach.yaml`.

## Setup (fora do Bauer, uma vez)

```bash
pipx install https://github.com/Panniantong/agent-reach/archive/main.zip
agent-reach install --env=auto
agent-reach doctor --json   # confere o que ficou disponível
```

Requer Python >= 3.10. Várias plataformas (Reddit, XiaoHongShu, Facebook,
Instagram) usam por padrão sessão de browser (`--from-browser chrome`) — em
servidor headless, configure credenciais manualmente via
`agent-reach configure <plataforma>-cookies "..."`.

## Execução

Via `run_command`, que o Bauer já tem — os comandos upstream passam pelo
MESMO pipeline de aprovação/guardrails existente (inclusive
`HeadlessApprovalEngine` se estiver rodando dentro de um `/loop`). Nenhuma
mudança em `bauer/tool_router.py` foi necessária.

## Não incluído nesta rodada

Uma tool Python dedicada (`bauer/tools/social_search.py`) que traduza "busca
no Twitter" automaticamente no comando certo. Mais ergonômico, mas não é
necessário pro ganho principal — o modelo já monta o `run_command` certo
tendo a skill como referência. Avaliar depois se o uso via skill se mostrar
insuficiente.

## Como re-verificar

```bash
agent-reach doctor --json
gh --version && yt-dlp --version   # confirma que as ferramentas upstream instalaram
```

Se `agent-reach doctor --json` mudar de schema numa versão nova, ou se algum
comando upstream documentado na skill mudar de sintaxe, atualize
`bauer/data/skills/web/agent-reach.yaml` — a fonte da verdade upstream é
`agent_reach/skill/SKILL_en.md` + `agent_reach/skill/references/*.md` no
repositório original.

## Versão verificada

`v1.5.0`, tag em 2026-06-11, commit `f65526cbaaad3879473acc1ba6dbefd195caf2be`.
Ver `bauer/data/external_integrations.yaml`.
