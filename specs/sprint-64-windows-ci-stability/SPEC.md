# SPEC — Sprint 64: estabilidade do CI no Windows (plano 064)

## Problema

Os jobs `Tests (Python 3.11/3.12 / windows-latest)` do `master` falham em toda
execução. Levantamento feito nos logs de 9 runs (`gh run list --branch master`,
de `4b799cb` a `415c56a`):

| Teste | Frequência | Assinatura |
|---|---|---|
| `test_channel_execution::test_timeout_keeps_same_context_and_kernel_custody` | **todo job Windows** | `assert entered.wait(timeout=5)` → `assert False` |
| `test_channel_execution::test_cancel_waits_for_real_worker_and_preserves_all_cost` | 1× | mesmo assert |
| `test_server_accounting_producers` (replan `loop`, `test_loop_replan_preserves_tools...`) | 5× | `controlled worker did not reach terminal` (5s) |
| `test_approval_chat_e2e` (agno chat/post/openai, playwright run-enforce) | 6× | `No approval reached the persisted queue` (6s) |
| `test_approval_chat_e2e::test_playwright_...[always-audit]` | 1× | `thread.join(10)` com o thread ainda vivo |
| `test_approval_chat_e2e::test_waiting_for_human_does_not_exhaust...` | 1× | run `running` depois do budget de 8s |
| `test_kernel_serve_stream::test_stream_approval_is_visible...` | 1× | stream sem `event: approval` |
| `test_telegram_loop::test_record_file_has_restrictive_permissions` | até `f5f643e` | `438 == 384` (já resolvido no master por `a720e50`, com skip POSIX) |

## Causas-raiz

1. **O setup do turno governado fazia trabalho quadrático e rodava antes do
   relógio do teste parar.** Fizemos profiling do worker de canal, do
   `process()` até a entrada no turno. A rota de time (`resolve_team_intent`)
   e o catálogo do decisor listam os times, e `TeamRegistry._validate`
   chamava `RuntimeAgentRegistry.get` para cada agent de cada time. Cada
   `get` relia e re-parseava todos os specs YAML com o PyYAML puro: 1 time ×
   9 agents davam 270 parses por turno, cerca de 85% do setup.
   - No Linux isso custava cerca de 0,1s por turno. Com instrumentação, o
     custo caiu de 1,42s para 0,26s em 3 turnos.
   - No runner Windows (I/O e CPU mais lentos, 4 workers xdist), o mesmo
     custo passava do budget de 0,4s do teste de timeout. O gateway cancelava
     a execução antes de o corpo do turno começar, e `entered` nunca era
     setado. Isso explica a falha determinística.
   - Reproduzido localmente: com 0,8s de atraso injetado antes do turno, o
     teste antigo falha com a mesma assinatura e o novo passa.
2. **O boot do servidor não era hermético em relação ao Docker.** Cada
   `create_app` monta o router desktop, que executava `docker ps -a`
   (`sync_discovered_docker_targets`) e gravava os containers do host no
   config do teste. Foram 84 chamadas só em quatro arquivos de teste, cerca
   de 0,9s cada aqui. O runner Windows tem o CLI do Docker, então cada
   chamada custava segundos, em paralelo com os testes sensíveis a tempo.
3. **Os deadlines de polling eram asserções de velocidade.** Valores como 5s,
   6s, 10s e "40×50ms" eram redes de segurança curtas para SDK Agno real,
   subprocessos reais e 4 a 6 turnos de `/loop` com replan. O
   `tests/conftest.py` já documenta a regra (`DEADLINE_ANTI_TRAVAMENTO =
   30`): timeout em teste é contra travamento, não contra lentidão.

4. **Corrida entre admissão e aprovador rápido (bug de produto).**
   `BauerKernel._preflight` persistia o pedido de aprovação e só depois relia
   o run para montar o resultado da admissão. Se a aprovação chegava nessa
   janela (aba Approvals, Telegram ou o teste, que aprova assim que vê o
   pedido), o run já estava `queued`. O `/stream` via `queued`, respondia
   "⛔ aprovação humana necessária" e abandonava um run aprovado. No Windows
   o I/O do `approval.requested` alarga a janela; o diagnóstico adicionado ao
   teste mostrou o run terminando em `queued` com a aprovação já concedida.
   Com aprovação instantânea forçada, o teste
   `test_admission_reports_waiting_approval_even_if_approved_before_reread`
   reproduz a falha de forma determinística. A correção está em
   `_waiting_result`: uma admissão que pediu aprovação informa
   `waiting_approval`, e o fluxo de aprovação lê o estado vivo e inicia o
   worker. Negação ou cancelamento concorrente continua terminal.

5. **Um worker de memória devolvia um slot que não era dele (bug de
   produto).** O `_start_memory_worker` relia o global
   `_MEMORY_WORKER_SLOTS` na hora de liberar. Um worker que sobrevivia a uma
   troca do pool liberava um slot do pool novo e "dessaturava" a capacidade.
   No CI Windows, um worker de um teste anterior terminava dentro do
   `test_prefetch_saturation...`, e o teste via `decision: 2`. Agora o worker
   libera o semáforo que adquiriu. A regressão é
   `test_memory_worker_releases_the_slot_it_acquired`.

Nenhuma das falhas era semântica POSIX, e nenhuma ganhou skip. O alvo do
monkeypatch (`bauer.agent.run_one_turn_with_fallback`) funciona no Windows:
o teste de cancelamento, que usa o mesmo helper sem budget curto, passava.

## Escopo

- Produto:
  - `TeamRegistry.list` valida contra um único snapshot de agents.
  - Os specs YAML são parseados com cache por hash de conteúdo (`CSafeLoader`
    quando disponível) e devolvidos como cópia.
  - `discover_docker_containers` respeita `BAUER_DOCKER_DISCOVERY=0` quando
    não há `runner` explícito. Nesse caso devolve `None`, o mesmo contrato de
    daemon indisponível, que não remove alvos já cadastrados.
- Testes:
  - `conftest.py` desliga o inventário Docker.
  - Os testes de timeout do gateway cobram o relógio a partir da entrada no
    turno.
  - Os deadlines de polling viram anti-travamento de 30s.
  - O budget do teste "espera humana não esgota o timeout" sobe de 8s para
    15s, e a espera continua maior que o budget.

## Fora de escopo

- Testes lentos que não falham, como os de retry/backoff real com 10 a 49s
  em `test_turn_partial_accounting` e `test_server_extended`. Ficam
  registrados como oportunidade.
- Mudar `ci.yml`.

## Decisões

- **Cache por conteúdo, não por mtime.** No NTFS e em FS de rede, a
  resolução de mtime faria um spec reescrito no mesmo tick parecer
  inalterado. Ler os bytes custa pouco. O custo estava no parse.
- **O gate do relógio no teste é deliberado.** `charge_generation_from`
  embrulha o `ExecutionControl.wait_for_generation` real: espera o turno
  entrar (até 30s) e então chama o original com o budget configurado. A
  semântica testada (timeout durante o turno, pausa durante a espera humana)
  é a do produto, sem atalho. Só o setup anterior ao turno fica fora da
  conta, porque não é objeto do teste.
- **Toggle por variável de ambiente para o Docker.** Segue o padrão da suíte
  com `BAUER_OLLAMA_URL`. Em produção nada muda: sem a variável, o
  inventário roda como antes.

## Critérios de aceite

- [x] Uma listagem de times parseia cada spec no máximo uma vez. Listagens
  repetidas sem mudança não re-parseiam. Uma edição de spec vale na
  listagem seguinte.
- [x] Com `BAUER_DOCKER_DISCOVERY=0`, `create_app` não executa `docker`
  (confirmado com um spy de `subprocess.Popen` na suíte inteira: zero
  chamadas).
- [x] A falha determinística do teste de timeout é reproduzida localmente com
  setup lento e corrigida.
- [ ] Os dois jobs Windows ficam verdes em duas execuções consecutivas na
  branch (IDs no `BACKLOG.md`).
