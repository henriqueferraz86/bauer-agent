# Revisão independente

Integração em `tests/test_chatgpt_loop_recovery.py` usa o
`ChatGPTBackendClient` concreto, parser Responses SSE e execução nativa real.
Somente o transporte HTTP e a espera são controlados; `append_file` escreve
um marcador em `tmp_path`, permitindo detectar repetição de efeitos.
Nenhum provider, orçamento ou workspace real é acessado.

## Evidências

- Sobrecarga na chamada seguinte a uma ferramenta: somente a requisição é
  repetida, com `input` idêntico e `function_call_output` preservado. Proposta
  de ferramenta recebida em stream incompleto não é executada.
- Tentativas têm contagem própria; uso ausente na falha não reaproveita tokens
  da chamada anterior. Custos das chamadas bem-sucedidas são somados uma vez.
- HTTP 401/402 e erro SSE de contexto propagam imediatamente, mantendo o log
  da ferramenta concluída. Sobrecarga persistente termina após dois retries.
- Fallback configurado continua disponível após exaustão; o log parcial da
  ferramenta anterior aparece uma vez no resultado agregado.
- `/loop` governado conclui após a recuperação, com uma ferramenta, cinco
  chamadas LLM (incluindo confirmação final), custo USD 0,00355 e
  `usage_known=false` pela tentativa sem informação de uso. Ledger durável e
  sessão mantêm a contabilização e o resultado da ferramenta.
- Cancelamento HTTP e kill-switch durante a espera impedem nova requisição;
  o Kernel termina `cancelled`, preservando ferramenta e custo anteriores.
- O limite de custo é sincronizado antes da chamada seguinte, impedindo nova
  requisição quando o custo da primeira já consumiu o teto.

## Limites de escopo preservados

O reconhecimento legado de native tools considera HTTP 400 como protocolo
não suportado e pode mudar para bridge. Esta sprint não altera esse downgrade;
o teste de contexto permanente do novo retry usa HTTP 200 com envelope SSE
`error`. Não se afirma que todo HTTP 400 bruto termina sem chamada bridge.

A cascata de fallback mantém a restauração de contexto anterior ao turno,
conforme o contrato existente. A preservação integral de contexto nesta sprint
aplica-se às tentativas da mesma requisição nativa, antes da cascata.

## Achados durante a revisão

- O helper inicialmente importava um classificador inexistente; corrigido para
  `classify_api_error` pelo responsável pelo backend.
- Erros de ferramentas não devem entrar na recuperação do provider. Marcador
  no limite de `client.chat_with_tools` e tipos explícitos de erro garantem
  essa origem, preservando exceções de programação e execução de ferramentas.
- O feedback de espera precisa voltar a "pensando" na retomada. A emissão de
  `on_round` antes da nova requisição remove a mensagem de cooldown anterior;
  teste unitário cobre inclusive um sink de retry que falha.
- A interrupção por custo antes de uma resposta textual inicialmente deixava
  saída vazia e o gate `non_empty_output` reprovava o run. Teste de integração
  mantém essa condição coberta. Corrigido no motor de rodadas com diagnóstico
  factual de limite, preservando a avaliação e a custódia do Kernel.

## Validação executada

`uv run pytest tests/test_chatgpt_loop_recovery.py -q --tb=short`: 10 casos
passaram. Ruff 0.17.0 do novo arquivo também passou. A regressão ampliada de
native retry, controle de `/loop`, SSE e motor de rodadas passou com 77 casos:

```bash
uv run pytest tests/test_chatgpt_loop_recovery.py tests/test_kernel_serve_stream.py \
  tests/test_native_provider_retry.py tests/test_loop_provider_retry_control.py \
  tests/test_serve_loop.py -q --tb=short
```
