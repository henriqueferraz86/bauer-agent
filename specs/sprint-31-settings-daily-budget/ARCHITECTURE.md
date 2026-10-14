# ARCHITECTURE

PUT no desktop router com verify_key/CSRF de sempre e mesmo runtime_root do
Kernel. Validação antes de BudgetManager.set_profile(daily_budget_usd=value).
GET/PUT retornam status real; sem YAML/config/env. Endpoint não executa LLM.
Novo componente SettingsBudgetCard com API helper puro/testável budgetSettings,
loading/erro/retry/input invalid/saving/saved e uso real. Preservar rascunho ao
salvar com erro; não sobrescrever por polling. Input number min0 step0.01;
backend aplica independentemente de validação navegador.

Zero exige bloqueio explícito em ensure_can_start antes de reservar vaga: a
estimativa desconhecida0 poderia passar no ledger mesmo com orçamento0. O guard
corrige esse caso e preserva comportamento dos outros limites e ledger.
UI descreve consumo/saldo real; não promete autorização de run por orçamento
positivo, pois outros gates permanecem. Perfil salvo preserva gasto/outroscampos.
