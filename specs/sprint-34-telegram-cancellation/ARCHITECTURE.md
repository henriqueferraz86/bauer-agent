# ARCHITECTURE — Sprint 34

ExecutionControl instalado via ContextVar contém identidade de run/session e
workspace, Event de parada e callback para consultar governança. checkpoint
levanta ProviderRetryInterrupted (não ToolError, para não virar resposta de
ferramenta que permita continuar). Threads recebem copy_context.

Registro de execução no backend liga identidade do operador ao worker e mantém
seu estado vivo. Toda run_governed executa no worker, mantendo custo e estado
sob custódia; timeout apenas retorna aviso e sinaliza. Lock da sessão só é
liberado na saída real. Não criar escrita terminal fora do Kernel.

ToolRouter verifica controle mesmo com policy desligada e novamente antes do
dispatch efetivo, inclusive workers com timeout/browser e depois da aprovação.
Agent verifica limites nas fronteiras native/bridge/fallback/recovery e
compressão. Não engolir a interrupção tipada em catches best-effort.

Processos sob controle usam Popen com grupo próprio e callbacks de término;
sem controle, preservar caminho subprocess.run. Unix encerra grupo; Windows
usa identificação do grupo/árvore e taskkill sem shell. Ferramentas de processo
guardam owner e não permitem que uma run controle processos de outra.

Limites por run e saldo diário são consultados sem alterar o perfil persistido.
Operação externa não interrompível permanece em cancelamento solicitado até a
saída; nenhuma etapa posterior é iniciada.
