# Validação e revisão — 2026-10-13

- `uv sync --frozen --extra dev`: exit 0.
- Regressões `test_chatgpt_backend`, `test_kernel_serve_stream` e
  `test_chat_privacy`: 51 testes, exit 0.
- `uv run pytest tests/ -q --tb=short`: exit 0; skips e avisos existentes
  de depreciação de `on_event` do FastAPI.
- Ambos os gates Ruff 0.17.0 de AGENTS.md: exit 0.
- `git diff --check`: exit 0.

Revisão local: assinatura nativa compatível com o pai, reutilização do parser
Responses, callback best-effort, argumentos vazios normalizados somente ao
emitir a call completa (evita prefixar `{}` aos fragmentos). Uso e chamadas
mantêm o contrato do loop existente. Sem mudanças em credenciais/config/modelo
ou custódia do Kernel. A edição preexistente de CI não faz parte deste fix.

Após conferir ausência de turnos ativos, reiniciado `bauer-serve.service`.
`GET /stream?message=oi` devolveu `Olá! Como posso ajudar?`, seguido de `done`.
Run `run-d8a6b88c-9f53-488c-a8ad-9ac3b69aa3e1`: `completed`, uma chamada LLM,
zero tools. Provider/modelo preservados: `openai` / `gpt-5.6-luna`.

A investigação consultou [OpenAI Docs — Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery).
A causa deste incidente foi determinada pelos registros locais, inspeção do
cliente e comparação entre o endpoint herdado e o transporte Responses real.
Nenhum provider real é usado pelos testes automatizados.
