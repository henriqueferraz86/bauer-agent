# SPEC — Sprint 78: /loop resiste a erro passageiro do provider

## Problema

Na Beelink, em 2026-11-11 às 23:20 UTC, o agente do Telegram abriu sozinho
um /loop (sprint 77) para a T0011. O run
`run-5d4fcfa2-8fc6-4ab6-8112-7302a1ad2744` falhou em 2 s, na primeira rodada,
sem nenhuma tool executada, com erro `loop parou: provider_error`.

Reproduzido logo depois, numa cópia isolada do estado e com o mesmo
provider, modelo, histórico e limites, o loop rodou 40 rodadas sem erro.
A falha foi passageira, mas tinha dois agravantes:

1. `run_loop_rounds` tratava qualquer exceção da rodada como fatal. Uma
   falha momentânea do provider matava um trabalho autônomo de horas.
2. O motivo real só ia no relatório enviado ao chat. O run, que é o que o
   painel mostra, e o log do gateway ficavam só com "provider_error".

## Comportamento

- Rodada que falha por erro **transitório** (`RATE_LIMIT`, `SERVER_ERROR`,
  `PROVIDER_DOWN`, `TIMEOUT` em `classify_api_error`) é repetida até 3 vezes,
  após 5 s, 15 s e 45 s. A rodada que falhou não conta no limite de
  rodadas. O contador zera depois de uma rodada bem-sucedida.
- Durante a espera, `should_stop` é consultado a cada segundo:
  `/cancelar`, kill-switch e orçamento continuam valendo.
- Erro permanente (autenticação, cota, modelo inexistente, desconhecido)
  continua parando na hora.
- O detalhe (`Tipo: mensagem`, redigido por `redact_text`, até 500
  caracteres) vai para o relatório, para o log do gateway (warning) e para
  `run.error` (`loop parou: provider_error — <detalhe>`), no /loop do
  Telegram e no da web.

## Fora de escopo

- Descobrir a causa exata da falha das 23:20: ela não ficou registrada em
  lugar nenhum do servidor, e é isso que esta sprint corrige.

## Causa real das falhas da Beelink (achada depois do merge do PR #176)

Um segundo /loop (`run-4e070f33…`, 23:46) falhou do mesmo jeito, então
reproduzi o loop **com as ferramentas reais** numa cópia isolada do estado:

- com `list_dir`, o loop concluiu;
- explorando o projeto, ele morreu na 9ª rodada com
  `FileNotFoundError: …/investimentos-os/.venv/bin/python`.

`glob_files` chamava `stat()` em cada resultado. O `.venv/bin/python` do
projeto é um link para um Python do host, que não existe no container. A
exceção não era `ToolError`, então `run_native_turn` a relançava, o turno
caía e `run_loop_rounds` a rotulava como `provider_error`.

Correções:

- `glob_files` lista link quebrado como `(link quebrado)` e entrada
  inacessível como `(inacessivel)`, em vez de falhar;
- `run_native_turn` converte `OSError` de qualquer tool em resultado de
  erro para o modelo (`[Erro: Tipo: mensagem]`) e segue o turno. Outras
  exceções inesperadas continuam propagando, assim como as de controle
  (cancelamento, governança).

Testes: `tests/test_sprint78_tool_oserror.py`. Os dois primeiros falham no
código anterior.
