# ARCHITECTURE — AgentOS na Beelink

`bauer/agentos_app.py` constrói um `agno.os.AgentOS` a partir do config Bauer,
do `RuntimeAgentRegistry` e do `TeamRegistry`. O `AgnoRuntimeAdapter` continua
sendo a única camada que traduz `AgentSpec`/`TeamSpec` para objetos Agno.

O processo `bauer-agentos.service` executa `python -m bauer.agentos_app` em
`0.0.0.0:7777`. O processo Bauer existente continua em `0.0.0.0:7770`.

Essa fronteira é deliberada: o AgentOS fornece a API/inspeção do Agno; runs
governados pelo Bauer continuam passando pelo Cockpit/Kernel em `7770`.
