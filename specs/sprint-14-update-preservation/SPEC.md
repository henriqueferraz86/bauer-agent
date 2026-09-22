# SPEC — atualização sem perda de ambiente

## Objetivo

Garantir que `bauer update` atualize o código sem remover ou sobrescrever configurações, credenciais, memória, modelos, extras ativos e estado operacional que estejam funcionando.

## Escopo

- Usar `uv sync --frozen` com os extras declarados para reproduzir o ambiente.
- Capturar e restaurar arquivos de configuração e memória antes de trocar o código.
- Preservar os extras de voz por padrão (`gateway`, `voice`, `voice-kokoro`).
- Validar importação e arquivos preservados depois da atualização.
- Fazer rollback do commit quando uma etapa posterior falhar.
- Registrar a regra na memória persistente do Bauer.

## Fora de escopo

- Backup de pesos grandes de modelos ou workspace de projetos.
- Migração automática de valores de configuração incompatíveis com uma versão nova.
- Atualização de credenciais ou segredos.

## Critérios de aceite

1. Config, `.env`, modelos, agentes, estado e Markdown de memória permanecem byte a byte iguais.
2. O comando não usa `pip install -e` para atualizar o ambiente gerenciado por `uv`.
3. O ambiente preserva os extras de voz e aceita extras explicitamente informados.
4. Falha em dependências ou smoke test restaura o commit anterior e os arquivos preservados.
5. Testes unitários cobrem sucesso, preservação e rollback.

## Validação

- `uv run pytest tests/test_update_cmd.py -q`
- `uv run ruff check bauer/commands/update_cmd.py tests/test_update_cmd.py`
- Suíte completa e CI.
