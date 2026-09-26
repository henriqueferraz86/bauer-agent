# Sprint 22 — Controle web da autonomia contínua

## Objetivo

Permitir que o operador ligue e desligue a autonomia contínua pelo painel do
Server, sem editar `config.yaml` ou reiniciar o processo.

## Escopo

- Persistir `continuous_autonomy.enabled` por uma operação autenticada do
  browser.
- Recarregar a configuração no supervisor em execução.
- Ao ligar, iniciar a observação quando houver alvos habilitados.
- Ao desligar, parar imediatamente a observação e manter a configuração
  desligada.
- Exibir o estado da configuração e mensagens de erro/aviso no painel.

## Fora de escopo

- Criar alvos automaticamente pelo browser.
- Alterar as políticas de recuperação ou a allowlist de ações.
- Iniciar a autonomia sem alvo configurado.

## Critérios de aceite

1. `GET /api/autonomy/status` informa se a autonomia está habilitada na
   configuração.
2. `POST /api/autonomy/enabled` aceita somente `enabled: boolean`, persiste o
   valor e recarrega o supervisor.
3. Desligar pelo endpoint interrompe um supervisor em execução.
4. Ligar sem alvo não derruba o Server: mantém `enabled=true` e retorna um
   aviso explicando que é necessário cadastrar/habilitar um alvo.
5. A tela `/autonomy` possui um controle global de ligar/desligar e continua
   oferecendo o controle operacional de iniciar/parar a observação.
6. Testes Python e TypeScript cobrem o contrato novo.

## Validação

- `uv run pytest tests/test_continuous_autonomy.py tests/test_server_contract.py -q --tb=short`
- `uv run ruff check bauer/ --select E9,F63,F7,F82`
- `npm run test -- --run`
- `npm run build`
