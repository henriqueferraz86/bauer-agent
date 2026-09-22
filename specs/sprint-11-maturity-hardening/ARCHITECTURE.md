# Arquitetura

## Memória

`UnifiedMemory` é uma fachada de compatibilidade. O runtime auditável continua
sendo a fonte de identidade, escopo, confiança e validade; o Markdown continua
sendo a representação humana que o agente lê. Uma gravação durável escreve no
runtime e, quando solicitada, projeta uma nota no Markdown. Falhas na projeção
humana são registradas e não invalidam a gravação auditável.

As implementações antigas continuam disponíveis para compatibilidade. A
fachada não tenta inferir equivalência entre arquivos antigos e registros
runtime; ela apenas adiciona um caminho explícito para novas gravações e uma
busca combinada.

## Roteamento

`routing_runtime.py` concentra a chamada ao decisor com fallback e a forma do
evento `model.route.selected`. Os consumidores continuam responsáveis por
publicar o evento no barramento correto, mas deixam de reconstruir o payload e
as regras de fallback individualmente.

## Servidor local

O Kanban usa `ThreadingHTTPServer` com threads daemonizadas. Cada requisição
pode progredir sem bloquear as demais e erros de conexão causados pelo cliente
fechar a aba são tratados como desconexões normais.
