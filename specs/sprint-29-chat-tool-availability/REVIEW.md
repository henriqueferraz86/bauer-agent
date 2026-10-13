# Revisão independente

project_backend revisou dispatch Agno, schemas, autorização de sessão/workspace,
Kernel retries, observer ContextVar e streaming. Dois achados corrigidos:

1. Worker órfão após timeout e executor rejeitado pelo evaluator não podem marcar
   uma pergunta como apresentada. Engine obtém preview; apenas resposta HTTP
   completed e emissão SSE marcam apresentação. Regressões em ambos runtimes.
2. Gerações por sessão poderiam crescer sem limite e pedidos abandonados
   permaneciam após TTL. Tokens únicos eliminam retenção de gerações encerradas;
   capture/resolve limpam pedidos expirados e observers antigos são invalidados.

Sem outros bloqueantes após revisão. Os defaults globais de segurança seguem
intactos; shell é habilitado somente na configuração local já usada pelo serviço.
A autorização permanente é global para o binário perguntado, conforme texto
explícito ao usuário, e depende de leitura verificável do allowed_commands.yaml.
