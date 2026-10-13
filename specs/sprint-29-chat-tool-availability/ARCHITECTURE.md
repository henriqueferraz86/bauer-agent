# ARCHITECTURE

A fonte de disponibilidade e parâmetros é ToolRouter.get_tool_schemas().
Servidor envia somente as ferramentas que o router efetivo autoriza.
O adapter preserva as sete wrappers existentes e cria Function Agno com schema
Bauer para demais ferramentas registradas; entrypoint chama execute_native_call.
Sem acesso direto a shell/filesystem/toolkit externo que contorne o router.
Nomes desconhecidos continuam erro de adapter. Erros de shell permanecem na
trilha e convertidos para pergunta explícita por server_chat.
Config local shell_enabled=true é opção do operador, mantendo safe_mode=true.
Prompt deve refletir disponibilidade real, evitando anunciar terminal ausente.

ShellApprovalManager captura somente bloqueios reais da allowlist mediante
observer ContextVar instalado no ShellRunner.validate depois da denylist.
O pedido fica associado à sessão, workspace e tarefa/comando originais,
expira em dez minutos e é descartado ao mudar workspace ou iniciar outra tarefa.
Respostas exatas de autorização persistem o binário derivado dos argumentos
parseados; a leitura do arquivo verifica sucesso antes de informar autorização.
Recusa não concede permissão. A continuação retorna ao motor governado e
reaplica policy, denylist e safe_mode; o helper nunca executa subprocessos.
Runners já existentes recarregam comandos aprendidos apenas quando o comando
ainda não está autorizado na instância.
Ao aceitar, verificar também que run_command ou process continuam autorizados
no router efetivo. Pedidos pendentes são apenas memória do processo: após
reinício o comando deve ser solicitado/bloqueado novamente antes de aceitar
uma confirmação; autorizações permanentes verificadas sobrevivem ao reinício.
Aceitar confirmação exige que prompt() já tenha apresentado a pergunta daquela
pendência. Observers de execução expirada/terminada ou de uma geração anterior
da sessão não podem criar/substituir pedidos depois de um turno novo.
Workers consultam prompt(presented=False) para obter texto sem conceder direito
de confirmação. O transporte marca presented=True apenas ao entregar a pergunta
final aprovada pelo Kernel: pergunta interna após timeout ou rejeitada por gate
não pode ser aprovada por um "sim" posterior. Tokens de geração são removidos
ao terminar a captura ou iniciar outro turno, sem reter sessões já finalizadas.
Capturas e mensagens novas podam pendências expiradas de sessões abandonadas.
