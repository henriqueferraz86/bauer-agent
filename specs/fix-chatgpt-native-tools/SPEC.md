# SPEC — correção do chat ChatGPT com tools nativas

## Problema e escopo

A sessão `173cbae0` falhou em 2026-10-13 com HTTP 403. O cliente ChatGPT
declara suporte a tools nativas, mas herda `OpenAIClient.chat_with_tools`,
que usa `/v1/chat/completions` em vez do `/responses` desse backend.
A chamada direta ao endpoint Responses com o modelo ativo respondeu HTTP 200.

Corrigir o contrato nativo no cliente existente e registrar a causa sanitizada
das falhas de execução de `/stream`, mantendo detalhes técnicos fora do chat.

## Fora de escopo

Trocar modelo/provider, alterar credenciais, fluxo OAuth ou governança do Kernel.
Não há nova feature/sprint. Skill aplicada à investigação: OpenAI Docs.

## Aceite e validação

- Com e sem callback, tools nativas usam exclusivamente `/responses` e retornam
  `content`/`tool_calls`, preservando argumentos, resultados de tools e usage.
- Callback de renderização com falha não interrompe a chamada.
- `/stream` entrega a resposta pelo motor real do agente com cliente ChatGPT.
- Falhas ficam registradas com run, tipo e causa sanitizada; o SSE não expõe
  exceções nem segredos.
- Regressões herméticas, suíte Python frozen e ambos os gates Ruff do AGENTS.
