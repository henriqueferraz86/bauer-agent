# Arquitetura — confiabilidade Telegram

1. No caminho nativo, quando não houver `tool_calls`, inspecionar o texto com
   parser estrito para formatos de pseudo chamada conhecidos (`name` +
   `arguments`, ou `function.name` + `function.arguments`). Só normalizar nomes
   presentes nos schemas atuais do router e argumentos JSON objeto. Converter
   para o formato nativo existente para reaproveitar guardrail, dedupe, timeout,
   eventos e métricas.
2. Não interpretar texto comum como comando. Nome desconhecido ou argumentos
   inválidos permanecem texto normal, mas pseudo chamada explícita não deve ser
   contada como evidência de trabalho concluído.
3. O bridge Telegram garante resposta não vazia; retorno vazio vira aviso claro.
4. Pedidos de equipe: resolver apenas team ids formais do TeamRegistry, passar
   execução por `AgnoTeamOrchestrator` com o Kernel do workspace ativo e ligar a
   run ao contexto de execução Telegram. Se o backend Agno não estiver
   configurado, responder claramente sem alegar execução.
5. Não mudar workspace nem contornar a custódia do Kernel. Não duplicar runs
   governadas: integrar equipe ao executor governado existente ou encaminhar a
   entrada antes da criação de uma run individual.
