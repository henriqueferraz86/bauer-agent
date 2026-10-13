# Revisão independente WEB01–05 — 2026-10-12

Executados `uv run python /tmp/bauer_web_repro.py` e `/tmp/bauer_web_repro2.py`, ambos exit0. Código referenciado revisado. Sem alteração de produto ou configuração real; somente arquivos tmp. Transportes/provider simulados.

| Achado | Resultado independente | Severidade proposta |
|---|---|---|
| WEB01 | traversal `../victim` HTTP200; conteúdo externo original é carregado, recebe usuário e resposta e é salvo novamente | P1 |
| WEB02 | mesma sessão cookie: `/stream`403 sem CSRF; `/api/teams/fake/stream`200 e chama orchestrator.stream uma vez | P1 |
| WEB03 | `profile_set='local'`, cliente Ollama, switch OpenAI HTTP200, status provider OpenAI | P1 |
| WEB04 | dois turnos sobrepostos, ambos200, arquivo final só B/answer-B; também confirmado com kernel explicitamente ligado | P1 (P2 se convenção prioriza só exposição de segurança em P1) |
| WEB05 | dois requests full transcript mesmo session_id duplicam user/assistant do primeiro turno na entrada do motor e no store | P2 |

## Reforços e limitações

- Original repro.py cria app antes de patch das funções de turno. `create_app` captura run_one_turn em OpenAIRouteDependencies, portanto patch posterior não substitui execução de /v1. Cliente é MagicMock e continua hermético, mas falta assistant final na saída original; não usar esse detalhe como prova de outro bug.
- `/tmp/bauer_web_crossreview_history.py` corrigiu ordem do patch, usa config explícita kernel.enabled:true e replies distintas. WEB01 resultou200 answer-overwrite. WEB05 respondeu answer-first/answer-second, contexto do segundo turno contém first,answer-first,first,answer-first,second; store final acrescenta answer-second. Bug não depende de MagicMock de retorno do motor nem do Kernel desligado.
- `/tmp/bauer_web_crossreview_kernel.py` é clone de repro2 com kernel.enabled:true e config_path explícito na app concorrente. Mesmo resultado de perda silenciosa de A; nenhuma serialização por sessão no caminho governado.
- WEB01 restringe alvo a nome terminado `.jsonl` e permissões do processo; não é escrita arbitrária de bytes em qualquer extensão. API autenticada ou sem auth é pré-condição. Não afirmar bypass de autenticação.
- WEB02 prova ausência de gate CSRF e início de trabalho no endpoint. Exploração por navegador de outra origem não executada nesta revisão. Cookies são SameSite=Lax; navegação GET de primeiro nível é cenário coerente. CORS não impede envio de navegação. Fake team verifica dispatcher, não execução de tools reais.
- WEB03 usa constructor cloud fake; demonstra troca de estado/client pela API que usa `--local` via profile_set, sem enviar prompt a cloud real. `_resolve_turn_model` retorna esse client quando roteamento está desligado/default. OAuth é caminho adicional confirmado por leitura, não por teste de callback aqui.
- WEB04 perda só provada entre dois /chat; `/stream` e `/loop` têm o mesmo load/save pelo código, mas simultaneidade entre transportes ainda é critério para regressão, não prova já executada.
- WEB05 requer ID estável (session_id ou header Hermes); requests OpenAI sem ID recebem sessões novas e não sofrem a duplicação entre chamadas. Historizar em modo incremental é extensão válida, porém full transcript + ID estável precisa reconciliação/contrato explícito.

Nenhum dos cinco achados merece descarte. Relatório deve distinguir os resultados dinâmicos acima das extrapolações por leitura e das jornadas browser pendentes.
