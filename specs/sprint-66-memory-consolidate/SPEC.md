# SPEC — Sprint 66: consolidador manual (plano 065, fatia 1)

Depende da sprint 65 (proveniência de workspace nas sessões).

## Problema

Conhecimento durável só vira memória quando o modelo se lembra de escrever. O
`memory-nudge` (`memory_provider.py`) só lembra o modelo de salvar; não
consolida nada. As sessões episódicas acumulam no `SqliteSessionStore` sem
nunca virar memória semântica.

## Objetivo

`bauer memory consolidate` destila sessões encerradas **deste workspace** em
`<workspace>/memory/CONSOLIDATED.md`, com proveniência, de forma
reexecutável e auditável. A execução é manual e exige opt-in.

## Decisões

- **Opt-in por workspace, desligado por padrão**
  (`memory/consolidation/settings.json`, via `bauer memory consolidate
  enable|disable`). `--dry-run` funciona sem opt-in porque não chama o LLM e
  não escreve nada.
- **Destino: `CONSOLIDATED.md`**, que entra em `MEMORY_FILES`. Ele é listado,
  buscado e mostrado pelo `bauer memory md` (`show consolidated`) e editável à
  mão. As entradas ainda válidas entram no prefetch como "referência, não
  instrução". As vencidas (`valid_until`) somem do prefetch e da busca.
- **"Sem ativação automática de prompt"** significa que o consolidador nunca
  cria diretiva de comportamento. Só registros `fact`/`preference`/`lesson`
  passam, e qualquer outro `kind` (ex.: `directive`) é descartado. Diretivas
  são da sprint 68, com aprovação humana.
- **LLM:** `auxiliary.compression_model`, que cai para o modelo principal. Não
  há campo novo de config. Nos testes, o LLM é sempre injetado (fake).
- **A execução manual não passa pelo Kernel.** É um comando do usuário, como
  os demais de `bauer memory`. A execução agendada (sprint 67) passa pelo
  Kernel.

## Escopo

1. `bauer/memory_consolidation.py`: `ConsolidationSettings`,
   `ConsolidationLedger` (SQLite com chave `(session_id, scope_id)`, mais uma
   tabela `runs` com as métricas), `MemoryConsolidator`, `parse_records`,
   `write_records` e `active_consolidated_block`.
2. Seleção: sessões com proveniência única do workspace, mais antigas que
   `min_session_age_hours`, ainda não consolidadas e abaixo de
   `max_attempts`. O limite é `--max-sessions`. A proveniência é
   reverificada na leitura.
3. Payload: só mensagens públicas `user`/`assistant`, redigidas e cortadas
   (1.500 caracteres por mensagem, 12.000 por sessão). Uma mensagem que
   continua sensível é descartada. A transcrição vai entre `<transcript>`,
   marcada como dado.
4. Budget: `--max-tokens` (padrão: `daily_token_budget` do workspace) com
   estimativa de ~4 caracteres por token. Quando o budget acaba, a sessão
   fica para a próxima execução, sem chamar o LLM.
5. Escrita: o `CONSOLIDATED.md` é reescrito atomicamente
   (`confined_files.write_text_atomic`, que recusa symlink) com `record_id`
   determinístico. Só depois o ledger marca a sessão. Uma falha por sessão
   vira `failed` com `attempts+1` e é re-tentada na próxima execução.
6. CLI: `bauer memory consolidate [--dry-run] [--max-sessions]
   [--max-tokens] [--min-age-hours] [--json]`, mais `enable`, `disable` e
   `status`. A saída de revisão lista cada registro gravado e onde
   editá-lo/removê-lo.

## Fora de escopo

- Agendamento (sprint 67), diretivas (sprint 68), escrita no
  `RuntimeMemoryManager` e deduplicação semântica entre sessões.

## Critérios de aceite

- [x] Sem opt-in, a execução real falha; o `--dry-run` não chama o LLM nem
  escreve.
- [x] Sem leitura nem escrita entre workspaces.
- [x] Sessões históricas sem proveniência são puladas e contadas.
- [x] Segredos (inclusive de tool output) nunca chegam ao payload nem ao
  arquivo.
- [x] Retry idempotente, inclusive com crash entre a escrita e o ledger.
- [x] Budget e `max_attempts` respeitados; sessões episódicas intactas.
- [x] Fatos vencidos não são injetados nem encontrados pela busca.
