# SPEC — Orçamento diário em Settings

Problema: chat recebe policy deny daily used9.6945/limit2 sem campo de ajuste
em Settings. Limite efetivo pertence a BudgetManager/AutonomyProfile, não YAML.
Escopo: cartão Settings com limite diário USD, consumo últimas24h/saldo/atingido,
salvar número finito não negativo, confirmado por API. Zero bloqueia execuções.
GET/api/obs/budget existente; PUT mesmo path só daily_budget_usd. Salvar mantém
consumo, modo e outros limites, aplica imediatamente ao Kernel cacheado e
persiste após restart. Não escolhemos um novo limite do usuário automaticamente.
Fora: desativar política, reset consumo, alterar preços/limites de loop, semana/mês.
Skill obrigatória: spec-driven-project-setup.
Subagents: backend (API/regressões), frontend (card/SPA/tests/build), review
(revisão independente/browser), root (specs/contrato/gates/entrega).
Aceite: limite salvo na política real; editar inválidos não altera perfil;
isolamento auth/CSRF existente; erros visíveis/estado carregando; uso permanece;
responsivo390px; nome24h reflete janela móvel; feedback não promete orçamento
positivo se usado>=limite; testes frozen/Ruff/mypy/SPA/browser.
Contrato PUT request {daily_budget_usd:number}; strict sole required field,
finite>=0 rejects strings/bool/null/unknown/negative/nonfinite422.
Resposta igual GET {profile,daily,weekly,monthly,by_agent,by_company}; daily
{limit_usd,used_usd,remaining_usd,exceeded}. Outros campos inalterados.
