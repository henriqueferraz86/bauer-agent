# SPEC — Sprint 68: diretivas de feedback com aprovação humana (plano 065, fatia 3)

Depende da sprint 65 (feedback estruturado com escopo). As sprints 66 e 67
também estão na pilha, mas esta fatia não usa o LLM nem o consolidador.

## Problema

O 👍/👎 é gravado, mas nada muda: dez 👎 seguidos no mesmo padrão não
alteram o comportamento do agente. Ativar regras automaticamente a partir de
feedback seria perigoso, porque um 👎 dado por frustração com o provider
envenenaria o prompt.

## Decisões

- **Regra de promoção:**
  - ≥3 sinais do mesmo tipo, em ≥2 sessões distintas, com o mesmo
    fingerprint, no mesmo workspace;
  - só contam sinais dos últimos 30 dias (decay);
  - 👎 gera `avoid` e 👍 gera `prefer`.
- **Fingerprint v1, determinístico e sem LLM:**
  - com motivo (`/thumbsdown <motivo>`, sprint 65), o padrão são as
    palavras-chave normalizadas do motivo, em qualquer ordem;
  - sem motivo, são as tools do turno mais 3 palavras-chave do pedido.
- **O rascunho é texto determinístico** ("Evite: <motivo>"), editável na
  aprovação com `--text`. O texto é sanitizado, e um texto com dado sensível
  é recusado.
- **Aprovação individual e explícita:**
  - `approve <id>`, uma por comando, com confirmação (`--yes` para pular);
  - expiração obrigatória (`--ttl-days`, padrão 90, até 365);
  - `reject` e `revoke` disponíveis.
  - Nada é ativado automaticamente: `propose` só cria e atualiza rascunhos.
- **Recusada/revogada fica no histórico.** O mesmo padrão só volta como
  rascunho novo com ≥3 sinais **posteriores** à recusa/revogação.
- **Injeção:** `LocalMemoryProvider.system_prompt_block` (o bloco de memória
  do `bauer agent`) lê, a cada montagem, só as diretivas `approved` e não
  vencidas (até 10, no máximo 1.200 caracteres). Revogar ou vencer tira a
  diretiva da próxima montagem. Uma sessão já aberta mantém o bloco montado
  na abertura.
- **Proveniência:** cada diretiva guarda os `supporting_feedback_ids` e as
  sessões; `show` lista os sinais.

## Escopo

1. `bauer/memory_directives.py`: `fingerprint`, `Directive`, `DirectiveStore`
   (`<ws>/memory/consolidation/directives.json`, gravação atômica confinada,
   filtro por `workspace_id`), `propose`, `approve`, `reject`, `revoke`,
   `active` e `directives_prompt_block`.
2. Injeção no `LocalMemoryProvider`.
3. CLI `bauer memory directives propose|list|show|approve|reject|revoke`.

## Fora de escopo

- Fingerprint semântico (embeddings/LLM) e diretivas em canais/web: o
  `serve` e o Telegram não montam o `LocalMemoryProvider`.
- Proposta automática agendada.

## Critérios de aceite

- [x] A regra de promoção vale: 3 sinais, 2 sessões, 30 dias.
- [x] Diretivas nunca são injetadas antes da aprovação.
- [x] Revogadas e vencidas somem do prompt.
- [x] Aprovação é uma por vez, com confirmação, TTL validado e texto
  sanitizado.
- [x] Nenhuma diretiva ou feedback atravessa workspaces, nem com arquivo
  copiado.
