# SPEC — Recuperação de sobrecarga no caminho nativo

Erro real: rodada11 encerra /loop provider_error com ChatGPT stream overloaded.
Classificador já reconhece erro temporário. Caminho native não possui retry de
requisição; fallback externo recomeça turno e pode perder resultados no contexto.
Escopo: native run_one_turn repete somente chamada LLM que falhou antes do
dispatch de novas tools, até2 retries consecutivos com backoff5/10s+jittercap,
reseta contador após sucesso. Preserva ctx e logs/tools realizadas anteriormente;
cada tentativa usa budgetLLM e custo do executor de sempre. Espera interrompível
por stop/kill-switch/budgettempo, respeita maxLLM/tool/custo. Erros auth/model/
quota/contexto/unsupported e programação não entram neste retry. Exaustão mantém
falha/fallback normal e errofinal real. Evento seguro de retry no sink permite
atividade ao vivo /loop; não inserir texto de controle na resposta do modelo.
Não aumentar limite diário1000 já definido, não executar provider/tarefa real,
não alterar custódiaKernel/fallback configurado. Não replay turno inteiro nem
alterar retry bridge/Agno/CLIinteractive nesta tarefa. Não reexecutar runfalhado.
Skill: spec-driven-project-setup (já aplicada).
Subagents: backend (agent retry/helper+unit), diagnosis (revisão e Responses
mocked integração), root (server/serve_loop/cancel/specs/fullgates/deploy).
Aceite: overload->retry->sucesso com ferramenta antiga executada1x;
falhas consecutivas limitadas e tentativas/custos contados; 401/402/400context
semretry; cooldowncancel/kill/budgetencerra semnovacall; /looplive explica espera;
regressõesfrozen/Ruff/mypy e integração sem provider real.
