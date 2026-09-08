# Plan 054: Elevar o Bauer de beta avançado para pré-produção robusta

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: —
- **Category**: security, performance, frontend reliability, architecture
- **Planned at**: 2026-09-08, após auditoria completa de maturidade
- **Status**: IN PROGRESS

## Objective

Fechar os quatro maiores riscos identificados na auditoria de maturidade do
Bauer, elevando o produto de beta avançado para uma base adequada a
pré-produção controlada:

1. impedir exposição acidental do servidor sem autenticação;
2. controlar o custo de CPU e I/O da indexação semântica no Ollama;
3. tornar o ciclo de microfone, listen loop e wake verificável por testes de
   comportamento;
4. reduzir a concentração de responsabilidade nos módulos centrais e tornar
   explícita a custódia das execuções.

## Evidence

- `bauer/preflight.py:274-283` emite aviso para host externo sem API key, mas
  essa condição não é um erro obrigatório de boot.
- `bauer/commands/serve_cmd.py:141-165` resolve host e chave e cria o servidor
  sem uma barreira equivalente à configuração insegura.
- `bauer/sqlite_session_store.py:575-603` inicia indexação em background a cada
  save e itera as mensagens da sessão.
- `bauer/embeddings.py:291-293` implementa `embed_batch()` como chamadas
  sequenciais a `embed()`, sem batching real no backend Ollama.
- `desktop/src/screens/Chat.tsx:535-621` concentra o ciclo de gravação,
  detecção de silêncio, transcrição, loop e wake.
- `.github/workflows/ci.yml:250-283` executa build e audit do desktop, mas não
  testes de componente ou E2E.
- `bauer/agent.py`, `bauer/server.py` e `bauer/tool_router.py` permanecem como
  módulos centrais grandes, enquanto o Kernel documenta a diferença entre
  `admit()` sem custódia e `execute()`/`continue_governed()` com custódia.

## Scope

### Fase 1 — Barreira de segurança do serve

- Rejeitar no boot qualquer host não local sem `serve.api_key`, salvo uma opção
  de desenvolvimento explicitamente nomeada e visível.
- Preservar o comportamento local sem chave em `127.0.0.1`/`localhost`.
- Garantir que a mesma regra valha para CLI, desktop sidecar e caminhos de
  configuração carregados pelo servidor.
- Manter os detalhes de exceção fora das respostas HTTP de produção; registrar
  diagnóstico completo apenas no logger apropriado.

### Fase 2 — Perfil de baixo consumo para memória semântica

- Adicionar configuração explícita para habilitar/desabilitar indexação
  semântica automática.
- Implementar debounce/coalescência e limite de concorrência para não iniciar
  uma rajada por save ou por turno.
- Evitar revarrer a conversa inteira quando apenas mensagens novas foram
  adicionadas.
- Se o backend suportar, usar lote real; caso contrário, limitar o tamanho do
  lote e o ritmo das chamadas individuais.
- Expor no diagnóstico se a memória está em modo semântico, TF-IDF ou
  desabilitado, incluindo contagem de pendências sem imprimir conteúdo privado.

### Fase 3 — Contrato comportamental do microfone

- Criar testes para os modos `once`, `loop` e `wake`.
- Verificar: início da gravação, detecção de silêncio, envio automático,
  mensagem “Pode falar novamente”, reinício do loop, ativação do wake,
  cancelamento manual, erro de permissão e falha de transcrição.
- Testar que o clique manual continua interrompendo a gravação e não dispara
  um envio duplicado.
- Integrar os testes ao CI do desktop; o build continua obrigatório.

### Fase 4 — Desacoplamento arquitetural incremental

- Mapear responsabilidades e efeitos colaterais de `agent.py`, `server.py` e
  `tool_router.py` antes de mover código.
- Extrair primeiro módulos de baixo acoplamento: ciclo de voz do desktop,
  montagem de contexto, registro de rotas e adaptação de execução.
- Manter compatibilidade das APIs públicas e dos comandos existentes.
- Para cada caminho assíncrono, declarar se ele usa `execute()`,
  `continue_governed()` ou a exceção documentada de `admit()`.
- Não iniciar uma grande reescrita: cada extração deve reduzir uma
  responsabilidade mensurável e preservar os gates existentes.

Fora de escopo: trocar provider de voz, reescrever o Kernel, mudar o default
global de memória, migrar o backend de tarefas, alterar contratos de providers
ou introduzir multi-tenancy completo.

## Required behavior

1. `bauer serve` não sobe silenciosamente em interface externa sem API key.
2. Um servidor local sem API key continua funcionando como hoje.
3. A indexação semântica pode ser desligada sem desabilitar sessões, FTS ou o
   funcionamento normal do agente.
4. Uma sequência de saves rápidos gera no máximo uma rodada coalescida de
   indexação por janela configurada.
5. O modo semântico não excede o limite de workers configurado e não bloqueia
   a resposta principal do chat.
6. Os três modos de voz têm testes automatizados para o ciclo completo e não
   exigem pressionar Enter para enviar uma fala encerrada por silêncio.
7. O CI falha se o fluxo de voz quebrar, mesmo quando o TypeScript e o Vite
   continuarem compilando.
8. A extração arquitetural não altera a custódia, os estados persistidos,
   eventos, aprovações, budgets ou contratos de ferramentas.

## Verification

### Segurança

- Testes de configuração local, host externo com chave e host externo sem chave.
- Teste de boot do CLI e do sidecar desktop.
- `uv run pytest tests/test_security_defaults.py tests/test_server_contract.py tests/test_cli_desktop.py -q --tb=short`
- `uv run ruff check bauer/ --select E9,F63,F7,F82`

### Memória e desempenho

- Testes de debounce, limite de concorrência, retomada após erro e reindexação
  incremental.
- Medir chamadas ao Ollama, tempo de resposta do chat, vetores gerados e uso
  de CPU em uma sessão com saves rápidos.
- Comparar modo semântico, TF-IDF e desabilitado.
- `uv run pytest tests/test_embeddings.py tests/test_vector_store.py tests/test_sqlite_session_store.py tests/test_memory_context.py -q --tb=short`

### Desktop e voz

- Adicionar o runner escolhido pelo projeto e executar testes de componente ou
  browser em ambiente CI reproduzível.
- Cobrir microfone normal, listen loop, wake, silêncio, cancelamento, permissão
  negada, erro de transcrição e dupla submissão.
- `npm ci` e `npm run build` em `desktop/`.
- `npm audit --audit-level=high` em `desktop/`.

### Arquitetura e regressão

- Atualizar o mapa de custódia e verificar que novos call sites não burlam o
  Kernel sem justificativa registrada.
- `uv run pytest tests/ -q --tb=short`
- `uv run mypy bauer/`
- `uv run ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303`
  — o baseline amplo só pode permanecer se não piorar.
- `uv lock --check`
- `git diff --check`

## Done criteria

- Host externo sem chave falha de forma clara e testada; bind local continua
  seguro e funcional.
- Indexação semântica tem chave de desligamento, coalescência, limite de
  concorrência e medição antes/depois.
- Os fluxos de voz estão protegidos por testes automatizados no CI.
- Pelo menos uma responsabilidade relevante foi extraída de cada módulo
  central, sem aumentar o acoplamento nem quebrar a custódia.
- A documentação do desktop e do serve descreve o comportamento real.
- Todas as verificações obrigatórias passam e nenhum segredo entra no diff.

## Dependency ordering

1. Fase 1, segurança, pode ser feita isoladamente e deve vir primeiro.
2. Fase 2, desempenho, depende dos limites de configuração definidos na Fase 1
   apenas quando compartilhar validação de boot; funcionalmente é independente.
3. Fase 3, testes de voz, deve preceder qualquer extração do ciclo de voz.
4. Fase 4, arquitetura, só começa depois de existirem testes de contrato para
   os caminhos que serão movidos.

## STOP conditions

- A correção de segurança quebrar o uso local padrão ou exigir segredo gravado
  no repositório.
- A otimização de embeddings alterar a dimensão do índice existente,
  corromper dados ou bloquear o chat principal.
- O runner de UI exigir serviço externo, credencial real ou ambiente não
  reproduzível no CI; nesse caso, registrar a lacuna e manter um teste de
  contrato determinístico.
- Uma extração exigir reescrever o Kernel, mudar contratos públicos ou remover
  a trilha de auditoria para reduzir linhas.

## Suggested implementation commits

1. `fix(serve): block unauthenticated external binds`
2. `perf(memory): coalesce and throttle semantic indexing`
3. `test(desktop): cover automatic microphone turn lifecycle`
4. `refactor(runtime): extract execution and voice boundaries`

## Execution record

- Fases 1 e 2 implementadas nesta branch: bind externo sem chave é recusado,
  exceções internas não são devolvidas em respostas HTTP sensíveis, e a
  indexação semântica tem configuração estrita, fila incremental, debounce,
  lote limitado e desligamento independente do FTS.
- Fase 3 implementada: as regras de silêncio, parada e wake foram extraídas
  para `desktop/src/voice.ts`, cobertas por Vitest e incluídas no CI do desktop.
- A documentação foi alinhada de 8 para 16 telas e passou a documentar os
  controles de memória semântica.
- Fase 4 fica deliberadamente incremental: a extração segura do ciclo de voz
  foi feita; a divisão ampla de `agent.py`, `server.py` e `tool_router.py`
  permanece como próxima etapa, condicionada a testes de contrato de custódia.
