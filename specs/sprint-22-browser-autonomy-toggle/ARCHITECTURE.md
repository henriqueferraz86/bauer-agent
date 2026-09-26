# Arquitetura — controle web da autonomia contínua

O endpoint novo fica no mesmo router autenticado de `desktop_api.py`, porque
ele já possui a instância viva de `ContinuousAutonomy` e a função que resolve o
`config.yaml` efetivo. A escrita usa o parser YAML existente, recarrega a
seção validada e atualiza a instância sem perder os estados dos alvos.

O status passa a expor `config_enabled`, separado do estado operacional
(`off`, `running`, etc.). Assim o painel consegue diferenciar “configuração
ligada, mas sem alvo” de “configuração desligada”.

`POST /api/autonomy/enabled` tem semântica idempotente:

- `enabled=false`: grava a configuração, recarrega o manager e solicita
  parada do worker.
- `enabled=true`: grava a configuração, recarrega o manager e tenta iniciar;
  se não houver alvo habilitado, retorna 200 com `warning` e o estado off.

O browser usa esse endpoint para o switch global. Os botões existentes de
“Iniciar observação” e “Parar imediatamente” continuam sendo o controle
operacional do worker, enquanto o switch define a permissão persistente para
que a autonomia seja iniciada.
