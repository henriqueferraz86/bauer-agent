# Revisão independente

project_diagnosis revisou projeção, associação sessão/run, delegação e integração
Agno. Identificou tools de membros SDK descartadas pelo sink do chat; root
corrigiu encaminhamento somente para membros explicitamente identificados,
com tool_name e sem argumentos/resultados. Regressão distingue tool da raiz.
Nenhum bloqueador restante. RunManager continua declarando o estado do pai;
evento de tool concluída e encerramento SSE não concluem a execução.

Inspeção visual desktop/móvel e validação hermética no navegador confirmam
compositor visível, atividades reais, tarefas distintas e estados desconhecidos.

Revisão da correção: o evento activity recém-introduzido era desconhecido para
uma aba com bundle anterior; seu handler genérico anexava o JSON. O canal
internal já era ignorado nessas versões. A correção reutiliza esse canal,
restringe append textual ao evento message e não elimina JSON de outra
sessão/tarefa nem fenced code do histórico. Execução ativa não é interrompida.
