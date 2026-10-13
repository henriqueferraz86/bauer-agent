# Validação — 2026-10-13

- uv sync --frozen --extra dev: exit 0.
- Suíte completa tests/ passou duas vezes; segunda após correções da revisão.
- 13 regressões integração /chat e /stream, native e Agno, Kernel on/off,
  confirmação entre sessões, timeout órfão e pergunta rejeitada pelo evaluator.
- 37 regressões do helper: consentimento exato, recusa, sessão/workspace,
  expiração, persistência, revalidação, pergunta não entregue, tokens e limpeza.
- Regressões adapter com SDK Agno real + provider stub: schemas adicionais,
  kwargs/execução, negação/unknown, logs de erro/parciais e deduplicação.
- Ambos gates Ruff 0.17.0: exit 0. Mypy: 378 módulos, exit 0.
- git diff --check: exit 0.

Serviço local tinha zero runs ativos antes do restart. Depois: GET /tools
HTTP 200, 29 ferramentas e run_command presente (antes 28, sem run_command).
Schemas das 29 ferramentas autorizadas mapeiam no Agno (antes filtro artificial7).
GET /chat/context mantém investimentosOS em /home/henrique/projetos/investimentosOS.
ShellRunner configurado executou apenas python -c print('BAUER_SHELL_OK'):
exit 0, safe_mode=True, workspace correto. Sem chamada real de LLM.
Config local ignorada pelo Git usa shell_enabled=True; defaults versionados
seguem opt-in. Alteração pré-existente de CI permaneceu fora da entrega.

Pending só sobrevive na memória do serviço: após restart pedir novamente o
comando para gerar nova pergunta. A autorização permanente salva/verificada
sobrevive e runners em cache reconhecem a mudança. Resposta afirmativa deve
ser exata: sim, autorizo, permito ou sim permanentemente. Texto composto é
tratado como outro pedido e não concede permissão.
