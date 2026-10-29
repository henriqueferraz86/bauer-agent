# ARCHITECTURE — Inicialização confiável do Gateway

## Visão geral

`start.sh` continua responsável por iniciar canais habilitados. A checagem de
idempotência passa a validar a linha de comando do PID salvo usando um helper
comum, em vez de confiar somente em `pid_exists`.

## Fluxo

```text
PID file existe → comando é Gateway? → sim: não duplica
                                  └→ não: remove PID obsoleto e inicia Gateway
```

## Componentes

- `bauer/gateway_process.py`: identifica a linha de comando do runtime.
- `bauer/commands/gateway_cmd.py`: início em background.
- `bauer/gateway_service.py`: início pelo gerenciador de serviço.
- `start.sh`: inicialização dos canais habilitados no boot Docker.

## Risco

Em plataformas que escondem a linha de comando de outro usuário, o helper
considera o PID não confiável e permite novo início. No container Bauer os
processos rodam com o mesmo usuário.
