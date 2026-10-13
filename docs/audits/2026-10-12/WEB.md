# Auditoria do servidor e frontend Bauer — 2026-10-12

Reproduções preservadas: [repro/README.md](repro/README.md). Evidências consolidadas: [REPORT.md](REPORT.md). Os caminhos `/tmp` abaixo identificam a execução original.

Escopo: leitura de `server.py`, `server_chat.py`, `server_openai.py`, `server_streaming.py`, `server_observability.py`, `desktop_api.py`, `session_store.py`, `web_auth.py`, `web/`, fonte `desktop/src/` e testes vizinhos. Nenhum código de produto/config real alterado. Reproduções HTTP com `uv run`, TestClient, mocks de provider e diretórios exclusivos `/tmp`; o root cuida do browser real e suíte completa.

Artefatos executáveis: `/tmp/bauer_web_repro.py` e `/tmp/bauer_web_repro2.py`. Saídas e arquivos em `/tmp/bauer-web-audit-4a29ew6x` e `/tmp/bauer-web-audit2-bcg0ejdt`. `uv sync --frozen --extra dev` foi executado pelo root antes destas reproduções.

## Achados reproduzidos

### WEB-01 — P1 — `session_id` permite leitura/sobrescrita fora do diretório de sessões

- Origem: `bauer/session_store.py:26`, `:34`, `:55`, `:62`; entrada HTTP sem validação em `bauer/server_chat.py:21`, `:208`, `:225` e `bauer/server.py:1573`. Header `X-Hermes-Session-Id` também não validado, `bauer/server_openai.py:83`.
- Prova: criar `tmp/victim.jsonl` com mensagem `outside-original`; POST `/chat` com API key e `session_id: '../victim'`, message `overwrite`. Resultado **200**; `victim.jsonl` fora de `sessions/` fica com outside-original + overwrite + RESPOSTA. IDs absolutos também escapam do diretório por semântica de `Path / absolute`.
- Impacto: bypass de confinamento do workspace e dos gates de filesystem; corrupção/leitura de qualquer `.jsonl` acessível pelo processo, inclusive históricos/eventos. Depende de acesso autenticado, ou servidor sem API key.
- Teste ausente na cobertura inspecionada: traversal, ID absoluto, separadores Windows, symlink, todos endpoints/session headers.
- Correção: validar ID em ponto único usado por SessionStore e ingressos; aceitar formato limitado e rejeitar separadores/absolutos; verificar containment após resolve (incluindo symlink). Testar HTTP antes de qualquer LLM/run e escrita atômica.

### WEB-02 — P1 — SSE de time inicia trabalho via GET sem proteção CSRF

- Origem: `bauer/server.py:982` só considera GET `/stream` mutável; `bauer/desktop_api.py:701` GET `/api/teams/{team_id}/stream` executa `orchestrator.stream` em `:717`.
- Prova: setup autenticado retorna201; mesma sessão, nenhum X-CSRF-Token: GET `/stream`403, GET `/api/teams/fake/stream?task=execute+tools` **200**, `orchestrator.stream.call_count=1`.
- Impacto: navegação externa de primeiro nível pode enviar cookie SameSite=Lax e disparar agents/tools/custo. Política do runtime ainda pode limitar tools, porém a intenção não veio do usuário.
- Teste necessário: mesmo contrato CSRF em todos streams executáveis; browser navegação de outra origem em teste isolado.
- Correção: POST para iniciar run com CSRF e GET apenas para observar eventos; até migração, incluir rota de time explicitamente na exigência CSRF.

### WEB-03 — P1 — troca de modelo permite sair da garantia `--local`

- Origem: `bauer/server.py:1314` `/models/switch` constrói provider sem verificar `profile_set=='local'`; `:2318` callback OAuth também seleciona OpenAI incondicionalmente. CLI promete `--local: nada sai desta maquina`, `bauer/commands/serve_cmd.py:140`.
- Prova: app com config temporária model.provider=ollama, `profile_set='local'`, cliente local; mock do construtor retorna client cloud. POST `/models/switch` `{provider:'openai', model:'fake-cloud'}` **200**, retorno provider openai e `/status.provider='openai'`. Roteamento desativado/default retorna `_state['client']`, `bauer/server.py:875`.
- Impacto: runtime iniciado com promessa explícita de privacidade passa a enviar prompts para cloud após operação na UI/API. Nenhuma chamada real cloud foi feita.
- Teste necessário: --local + switch cloud, callback OAuth, fallback e profile fallback durante execução.
- Correção: preservar flag local explícita, validar todos caminhos que trocam/constroem client antes da mutação; informar rejeição na UI.

### WEB-04 — P1 — dois turnos na mesma sessão perdem mensagens

- Origem: `bauer/server_chat.py:225` carrega snapshot; `:279` reescreve arquivo completo; `bauer/session_store.py:27` `open('w')` sem lock/versionamento. `/stream` tem o mesmo ciclo `bauer/server.py:1640`, `:1730`; `/loop` e `/v1` coexistem.
- Prova determinística com Events e dois POST /chat `session_id='shared'`: A e B entram antes de qualquer save; liberar A e aguardar200, liberar B e aguardar200. Histórico final **somente B + answer-B**, A desaparece.
- Impacto: múltiplas abas, API simultânea ou loop+chat deixam contexto inconsistente apesar de ambos responderem200. Leitura durante truncamento também pode retornar histórico parcial.
- Teste necessário: turnos concorrentes mesma sessão e entre transportes; sessões diferentes devem continuar paralelas.
- Correção: serializar ciclo inteiro load→execute→save por sessão (cross-process se suportado), ou controle de versão com conflito explícito; escrita atômica sozinha evita arquivo parcial mas NÃO resolve perda de atualização.

### WEB-05 — P2 — contrato `/v1` duplica histórico enviado pelo cliente

- Origem: `bauer/server_openai.py:103` carrega histórico persistido e `:104` reaplica todos `req.messages`.
- Prova: request1 `{session_id:'oai-history',messages:[user:first]}`200; request2 mesmo ID, messages `[user:first,assistant:RESPOSTA,user:second]`200. Store contém `[user:first,assistant:RESPOSTA,user:first,assistant:RESPOSTA,user:second]`.
- Impacto: SDKs OpenAI que enviam transcript completo multiplicam contexto e repetem instruções; consumo/custos e truncamento crescem a cada turno.
- Teste necessário: payload full-history com ID persistente/header Hermes; payload só último turno com política claramente definida.
- Correção: definir messages como transcript autoritativo no `/v1` ou reconciliar prefixo exato; manter modo incremental explícito para clientes Hermes quando necessário.

## Achados confirmados por código; reprodução browser/dinâmica ainda recomendada

### WEB-06 — P1 — `/v1` streaming mantém motor de tools/custos divergente

- Origem: `bauer/server_openai.py:162`–`:276`. Caminho stream chama diretamente `turn_client.chat_stream(active_model, ctx.get_payload())` em `:190`, interpreta uma tool bridge via `_try_parse_tool`, chama router.execute, termina com complete_run sem custo. Caminho batch usa `run_one_turn` e `continue_governed` em `:282`–`:312`.
- Impacto concreto de estrutura: tools nativas não são passadas, batch/dedup/fallback/timeouts/guardrails compartilhados não são usados; não instala `cost_sink`, não chama record_turn_budget; complete_run omite cost_estimate. Mesmo request `stream` altera ferramentas e ledger disponíveis. Duplicação de motor de execução real, contrariando consolidação pretendida.
- Teste necessário: mesmo fake native provider/prompt/tools em stream e batch, fallback, erro/timeout, ledger com uso cloud sintético; nenhuma chamada cloud necessária.
- Correção: compartilhar motor de turno e sink de eventos/custo com `/stream`/`/chat`, conservando encoding OpenAI. Registar justificativa de custódia admit somente se limitação de streaming realmente exigir.

### WEB-07 — P2 — campos aceitos no contrato OpenAI são silenciosamente ignorados

- Origem: `bauer/server_openai.py:22` role é string sem enum; `:31`–`:37` aceitam model/max_tokens/temperature; `:105`–`:108` somente user/assistant sobrevivem; `:110` usa modelo global/routing e não req.model.
- Impacto: system/developer fornecido pelo cliente desaparece; modelo pedido e limite de geração/temperatura são ignorados com200, prejudicando compatibilidade e controle de custo. `model` anunciado em resposta pode diferir do solicitado.
- Teste necessário: client spy captura payload, model e kwargs com mensagens system e diferentes parâmetros.
- Correção: implementar campos suportados com validação de capability ou rejeitar explicitamente campos/roles não suportados; documentar subset não é suficiente quando campo é aceito silenciosamente.

### WEB-08 — P2 — logout mantém transcript e session_id no localStorage

- Origem: `desktop/src/screens/Chat.tsx:53`, `:63`, `:145`; `desktop/src/auth/AuthContext.tsx:75`–`:77` somente logout HTTP e refresh. Nenhum cleanup do CHAT_STATE_KEY. Padrões de redação frontend também são mais estreitos que `bauer/privacy.py`.
- Jornada browser: criar chat, `/#/config` botão Sair, ler `localStorage['bauer.chatState.v1']`, entrar novamente e abrir chat. Transcript permanece fora da proteção HttpOnly e reidrata automaticamente.
- Impacto: dispositivo compartilhado mantém conteúdo potencialmente sensível acessível via storage após usuário encerrar sessão; logout não encerra polls/fetch em outras abas por broadcast.
- Teste necessário: logout e expiração limpam memória persistida/streams; alternar usuário/instância e confirmar política escolhida.
- Correção: política explícita de retenção; limpar storage e sessão local ao logout/expiração, ou armazenamento opt-in criptografado/por conta. Não afirmar quebra entre usuários do servidor: backend é single-admin por design.

### WEB-09 — P2 — sair da tela Chat não aborta a requisição SSE

- Origem: `desktop/src/api/client.ts:100` aceita AbortSignal, mas `desktop/src/screens/Chat.tsx:464` não passa signal. Cleanup existente trata áudio e pollers, sem reader/AbortController.
- Jornada browser: iniciar turno lento, navegar para Models/Config e depois voltar; observar request /stream continuar e consumo provider/tools. UI reidrata transcript enquanto callbacks pertencem ao componente desmontado; resposta final/session_id podem não persistir na tela nova.
- Impacto: usuário deixa tela sem interromper execução; mecanismo backend que cancela na desconexão não dispara porque conexão permanece. Nova sessão pode ser criada ao retornar antes do evento done.
- Teste necessário: navegar/reload durante stream lento com provider controlado e confirmar intenção de cancelamento/persistência.
- Correção: gerenciar AbortController por turno e cleanup; decidir explicitamente continuidade em background e exibir run rastreável se essa for a intenção.

### WEB-10 — P2 — limites de upload/json só rodam depois de o parser materializar body

- Origem: `bauer/server.py:1445` UploadFile injetado; `:1463` Content-Length e `:1484` leitura limitada ocorrem DENTRO do endpoint, depois parser multipart do FastAPI/Starlette. Não há middleware de contagem de receive/body. `tests/test_server_body_limits.py:4` reconhece expressamente que raw body é materializado antes de validação.
- Impacto: arquivo acima MAX_AUDIO_BYTES é rejeitado finalmente, mas já foi recebido e spooled em disco pelo parser; Content-Length ausente/mentiroso e multipart com várias partes consomem recursos antes do check/autenticação. JSON de /chat ou /v1 também pode materializar body muito maior que limites Pydantic.
- Teste necessário: ASGI receive sintético conta bytes/chunks até corte, incluindo sem Content-Length; usar volume pequeno com threshold de teste, evitar DoS real.
- Correção: middleware ASGI limita bytes recebidos antes de parse e autenticação antecipada para uploads; limites por rota/categoria.

## Duplicações adicionais

- Approvals repetem construção de Kernel entre `bauer/server_observability.py:242` e `bauer/desktop_api.py:1557`; risco de divergência de configuração/executor em novas correções. Confirmar comportamento com agente de Kernel antes de priorizar.
- API observabilidade `/runs`, `/audit`, `/events`, `/approvals` e `/api/obs/*`, `/api/audit/*` coexistem por clientes CLI/UI. Não tratar mera existência de aliases como bug: avaliar equivalência e centralizar implementação.
- `/new` e `/clear` são aliases explícitos, não duplicação acidental.
- Frontend+backend têm redação de secrets independente: frontend não cobre PEM/URL credentials/gsk/hf/AWS enquanto backend cobre. Evitar desacoplamento de política de retenção em futuro refactor.

## Browser: rotas, seletores e isolamento

- SPA usa **HashRouter** (`desktop/src/main.tsx:3`): `/#/chat`, `/#/models`, `/#/projects`, `/#/teams`, `/#/runs`, `/#/approvals`, `/#/runtime`, `/#/autonomy`, `/#/observability`, `/#/audit`, `/#/config`, `/#/settings`.
- Auth: getByLabel('E-mail'); getByLabel('Senha',{exact:true}); 'Confirmar senha'; 'Chave de bootstrap'. Botões 'Criar conta', 'Entrar', 'Esqueci minha senha'. Setup exige `api_key` não-vazia E `web_auth_enabled=True`; bootstrap deve igualar API key. Sem API key web auth fica inerte.
- Chat: textarea placeholder começa 'Mensagem para Bauer…'; Enter envia. Envio/microfone são divs sem role/aria-label (acessibilidade e seletores fracos). Botão 'Nova conversa' consultar `Chat.tsx:712`; loop parar `getByTitle('Parar o loop (a rodada corrente termina)')`.
- Jornadas: auth setup/login/logout; texto simples SSE+reload; tools escrever/ler arquivo no workspace tmp; /new; /model; /loop+stop+reload; navegação enquanto gerando; logout storage; validação app local tentando modelo cloud.
- Isolamento: setar BAUER_HOME/BAUER_CONFIG/BAUER_AGENTS_FILE exclusivos tmp antes de imports; config_path/workspace/sessions_dir explícitos. Runtime root do servidor = `sessions_dir.parent / 'runtime'`. Root executou esse isolamento e browser.

## Atualização: prova focal dinâmica WEB-06 e WEB-07

**WEB-06 e WEB-07 promovidos a reproduzidos.** Script `/tmp/bauer_web_openai_parity.py`; evidência integral (chamadas/payloads/tools/runs/ledger) `/tmp/bauer-oai-parity-m0fi7wv1/proof.json`. Usa subclasse verdadeira `OpenAIClient` com métodos provider sobrescritos; chama o motor real `run_one_turn` e a tool real `read_file`. Três funções httpx (get/post/stream) proibidas durante requisição para garantir ausência de rede. Todos arquivos e configuração isolados em `/tmp`. Uso cloud é **sintético**, fornecido pelo spy: 1.000 prompt tokens +100 completion tokens por call, preço model gpt-4o.

| Kernel | Request stream | HTTP | Método provider | Schemas | Tools executadas | Custo run | Ledger |
|---|---|---|---|---|---|---|---|
| desativado | false | 200 | chat_with_tools 2 vezes |67 por call | read_file,1 |0.007 | settled,0.007 |
| desativado | true |200 | chat_stream 1 vez |0 |0 |null |vazio |
| ativado | false |200 |chat_with_tools 2 vezes |67 por call |read_file,1 |0.007 |settled,0.007 |
| ativado | true |200 |chat_stream 1 vez |0 |0 |null |reserved,0.0, apesar de run completed |

A segunda chamada de ambos batch contém mensagem role=tool com `AUDIT_MARKER_READ_ONLY`, lido de `workspace/marker.txt` real. Em stream esse resultado não existe. A diferença independe de capacidades/limitações de um LLM pequeno: ambos caminhos usam a mesma classe com capability nativa verdadeira.

Em TODOS os quatro casos: request `model='CLIENT_REQUESTED_MODEL'` resulta em call/model resposta `gpt-4o`; mensagem system com `CLIENT_SYSTEM_SENTINEL` não chega ao provider; `max_tokens=7` e `temperature=0.123` não chegam (kwargs{}). Body200 aceita todos esses campos sem indicar que foram ignorados.

Observação secundária: batch devolve usage aproximada por comprimento, não `last_usage` fornecido pelo provider (response prompt20/completion3 no caso enquanto calls somam prompt2000/completion200). Registrar como limitação contratual adicional na correção `/v1`.

A evidência browser do root em que qwen2.5-coder:3b repete `AUDITORIA_OK` ao pedido read_file foi interpretada como limitação do modelo na jornada, **não** como achado adicional. Esta prova focal separa a ausência do schema no código /v1 stream desse comportamento do modelo real.
