# ARCHITECTURE

Sobrescrever `ChatGPTBackendClient.chat_with_tools` e consumir a tradução e o
parser já existentes em `chat_stream_events`. Agregar deltas e chamadas no
contrato do agente nativo; enviar deltas ao callback opcional, em best-effort.
Não criar outro transporte, parser ou loop de tools. O backend exige streaming
mesmo quando o caller deseja apenas o resultado agregado.

No worker de `/stream`, registrar a exceção capturada com `redact_text` antes
de enviá-la ao logger. Preservar persistência e a mensagem pública genérica.
Testes HTTP usam SSE simulado na fronteira httpx, deixando o loop real executar.
