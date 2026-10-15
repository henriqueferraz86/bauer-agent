# Validação — 2026-10-15

Ambiente `uv sync --frozen --extra dev` aprovado.31testes unitários novos do
retry e10integrações Responses passaram;3testes extras do controle HTTPloop
passaram. Regressão independente77casos e regressão backend122casos aprovadas.
Dois gates Ruff0.17.0 aprovados; `uv run mypy bauer/` aprovado380arquivos.

A primeira suíte completa encontrou4expectativas antigas em contabilização
/v1: agora existem3chamadasprimárias+fallback, custo parcial por tentativa. Teste
ajustado para contar requisições efetivamente feitas e nenhum uso obsoleto;
24testes desse módulo aprovados depois da atualização, sem sleeps reais.
Suíte completa final `uv run pytest tests/ -q --tb=short` aprovada (exit0).

Nenhum provider real chamado por testes. Nenhum orçamento real alterado.
O diário1000 do usuário é preservado. Falhas permanentes não usam este retry.
O fallback/downgrade de protocolo mantém semântica legada documentada em REVIEW.
Nenhuma execução falhada é reaberta automaticamente.
