# Arquitetura — acesso Docker do supervisor

O serviço `bauer` recebe o cliente `docker.io` na imagem Debian e o socket
Unix do daemon como bind mount. O código de autonomia continua usando
subprocessos com comandos fixos; nenhum endpoint aceita uma linha de comando
Docker fornecida pelo usuário.

O mount fica restrito ao serviço que executa `ContinuousAutonomy`. O AgentOS e
o `agent-ui` não precisam do socket e não recebem essa capacidade.
