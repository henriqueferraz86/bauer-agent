# 028 — Bauer Design System: o terminal que mostra a máquina pensando

> Objetivo: `bauer agent` e `bauer serve` com UMA identidade visual, streaming
> real e uma linguagem de execução que nenhum concorrente tem. Referência de
> qualidade: Claude Code. Referência de *identidade*: o que só o Bauer sabe —
> local vs nuvem, custo em tempo real, Kernel governando o turno.

Status: **F0, F1 e F2 implementados e verdes** (2026-08-03) · F3–F6 pendentes ·
Acento definido: **violeta elétrico `#a855f7`** · Escopo: `bauer/ui.py`,
`bauer/agent.py`, `bauer/ascii_intro.py`, `bauer/indicators.py`,
`bauer/delta_stream.py`, `desktop/src/`

---

## 1. Diagnóstico — o que está errado hoje (medido no código, não na impressão)

| # | Achado | Onde | Consequência |
|---|--------|------|--------------|
| D1 | **Não existe streaming no terminal.** A resposta é coletada inteira (`_collect_with_fallback` → `chat_stream` consumido em `parts = list(...)`) e só então impressa como bloco Markdown. | [agent.py:1146](bauer/agent.py:1146), [agent.py:1198](bauer/agent.py:1198), [agent.py:1477](bauer/agent.py:1477) | O usuário olha um spinner "pensando…" por 5–60s e recebe um bloco de texto. É a diferença sensorial nº 1 para o Claude Code. |
| D2 | **Três paletas no mesmo produto.** CLI minimal teal `#00d4aa` + intro em gradiente teal→azul→roxo + indicators com `PULSE #7c3aed`; web em paleta GitHub (`#0d1117`, accent `#58a6ff`). | [ui.py:17](bauer/ui.py:17), [ascii_intro.py:28](bauer/ascii_intro.py:28), [indicators.py:14](bauer/indicators.py:14), [styles.css:2](desktop/src/styles.css:2) | O produto não tem cara. Cada superfície parece de um projeto diferente. |
| D3 | **O estado só existe enquanto o prompt espera.** A `bottom_toolbar` (modelo, ctx, custo) é renderizada pelo prompt_toolkit e **some no Enter**; o vão é coberto por um spinner genérico de uma linha. | [agent.py:4614](bauer/agent.py:4614), [agent.py:1361](bauer/agent.py:1361) | Justo no momento em que a máquina trabalha, o painel de instrumentos apaga. |
| D4 | **Ação de tool é uma linha morta.** `[✓] write_file  auth.py  90ms` — sem diff, sem saída ao vivo, sem agrupamento por rodada. | [ui.py:84](bauer/ui.py:84) | Não dá para acompanhar *o que* o agente fez, só *que* fez. |
| D5 | **Live e `input()` brigam** — documentado no próprio código como incidente real ("o bug do totodo"). A solução atual é desligar o spinner por lista de tools. | [agent.py:1395-1420](bauer/agent.py:1395) | Qualquer UI mais rica precisa resolver isso de forma estrutural, não por allowlist. |
| D6 | **O `serve` sobe com 6 linhas de texto** e o SPA tem cockpit (16 telas, palette Ctrl+K) que ninguém vê porque o boot não conta que ele existe. | [serve_cmd.py:167-191](bauer/commands/serve_cmd.py:167), [server.py:1280](bauer/server.py:1280) | Recurso caro e invisível. |

**A boa notícia:** o encanamento do streaming **já existe e já é usado** — pelo
gateway. `delta_stream` tem sink com `on_delta` / `on_round` / `on_tool` e um
`StreamDiag` que já calcula `tokens_per_second`. O terminal simplesmente nunca
instalou um sink. Isso muda a fase mais cara do plano de "reescrever o loop"
para "plugar um renderer".

---

## 2. O conceito: **Painel de Voo**

Claude Code é um *log elegante que cresce*. A proposta do Bauer é um
**instrumento**: o turno inteiro acontece dentro de um quadro vivo de três
zonas, e ao terminar ele **colapsa** para uma transcrição limpa que rola.

```
  ╭─ transcrição (rola, permanente) ─────────────────────────────────────────╮
  │  ❯ refatora o auth pra usar o pool de credenciais                        │
  │                                                                          │
  │  ▏bauer                                                                  │
  │  Vou ler o módulo atual e ver como o pool expõe as chaves.               │
  ╰──────────────────────────────────────────────────────────────────────────╯
  ┃ trilho de execução (vivo, colapsa ao fim)
  ┃  ✓ read_file      bauer/auth.py                                     41ms
  ┃  ✓ grep           "credential_pool"  · 3 arquivos                   88ms
  ┃  ◐ write_file     bauer/auth.py                                     1.2s
  ┃     ▏  +  from .credential_pool import CredentialPool
  ┃     ▏  -  token = os.environ["BAUER_TOKEN"]
  ┃     ▏  +  token = pool.acquire("bauer")
 ─────────────────────────────────────────────────────────────────────────────
  ◆ local  qwen3-coder:30b   ctx ▰▰▰▱▱▱▱▱ 34%   $0.00   18.4 tok/s   ⟐⟐⟐◦ verify
```

Quatro coisas nessa tela **não existem em nenhum concorrente**, e todas são
dados que o Bauer já tem:

1. **`◆ local` / `☁ nuvem` como selo de soberania**, mudando o acento do tema no
   turno. O Bauer é o runtime que roda na sua máquina — isso tem que ser
   *visível*, não uma flag. (`--local`, `validar_execucao_local`, roteamento.)
2. **tok/s ao vivo** — `StreamDiag.tokens_per_second()` já calcula. É o que
   torna a tela "ultrarealista": você vê a GPU trabalhando, não uma animação.
3. **Custo subindo em tempo real** dentro do turno (cost_meter). Um agente que
   te mostra a fatura enquanto gasta muda o comportamento de quem usa.
4. **A esteira do Kernel** `⟐⟐⟐◦` — os estados (admit → plan → execute →
   verify) acendendo. O diferencial arquitetural do Bauer virando pixel.

> Regra de ouro do conceito: **nada de enfeite que não seja um dado real**.
> "Ultrarealista" aqui significa *o painel expõe o que a máquina de fato está
> fazendo* — não que tem mais gradiente.

---

## 3. Identidade visual — uma paleta, duas superfícies

Recomendação: **manter o teal `#00d4aa` como acento único** (já é o Bauer) e
matar a divergência trazendo o web para ele. Grafite mais profundo que o
GitHub dark, para o teal brilhar.

```
  --bauer-void     #0a0c10   fundo
  --bauer-surface  #12151b   painel
  --bauer-line     #1e232c   moldura
  --bauer-accent   #00d4aa   AÇÃO / local / vivo        ← o único neon
  --bauer-cloud    #7aa2f7   nuvem (troca o acento quando o turno sai da máquina)
  --bauer-text     #e5e7eb / --dim #6b7280 / --faint #4b5563
  --bauer-ok       #22c55e   --warn #f59e0b   --bad #ef4444
```

**Fonte da verdade única:** `bauer/ui/theme.py` exporta os tokens e um
`export_css_vars()` que **gera** `desktop/src/tokens.css`. Um teste falha se o
CSS gerado divergir do commitado. Sem isso, a divergência D2 volta em três
meses — foi exatamente assim que ela apareceu.

Brilho sem circo (o "brilha os olhos" que sobrevive ao uso diário):
- **glow** só no elemento vivo (tool em execução, caret de streaming);
- **shimmer** no gauge só quando >85% (é aviso, não decoração);
- gradiente **exclusivo da marca** — logo e boot. Nunca em conteúdo.

---

## 4. Fases

### F0 — Fundação de tokens (0,5 dia) · destrava tudo
- `bauer/ui/theme.py`: paleta, glifos, `Fallback` ASCII, `export_css_vars()`.
- `bauer/ui.py`, `ascii_intro.py`, `indicators.py` passam a importar dali.
- Gera `desktop/src/tokens.css`; teste de divergência Python↔CSS.
- Chave de escape desde o primeiro commit: `BAUER_UI=plain|rich`, respeito a
  `NO_COLOR`, degradação ASCII automática fora de TTY (já há `unicode_utils`).

### F1 — Streaming real no terminal · **maior ganho por linha**

> **Correção de escopo (feita durante a execução).** O parágrafo abaixo dizia
> que bastava plugar um sink, porque o `delta_stream` já streamava. Errado: ele
> só cobre o modo **bridge**. O modo **nativo** — o padrão dos modelos capazes,
> por onde passa quase todo turno — chama `chat_with_tools`, que era
> `"stream": False` nos DOIS clients. Não havia streaming a plugar; foi preciso
> criar a capacidade nos clients (`on_delta` em `chat_with_tools`) e remontar
> as tool calls fatiadas pelo SSE (`bauer/stream_tools.py`), onde três dialetos
> de provider discordam. Sem essa descoberta, F1 teria "funcionado" só no
> caminho que o usuário quase nunca usa.

- `ConsoleSink` implementando o protocolo de `delta_stream` (`on_delta`,
  `on_round`, `on_tool`), instalado no turno interativo via `set_sink`.
- `_collect_with_fallback` passa a emitir delta a delta em vez de `list(...)`.
- **Markdown incremental**: buffer por bloco — o parágrafo em curso sai como
  texto vivo; ao fechar bloco (`\n\n`, fim de fence) reimprime só ele como
  Markdown. Reparsear o documento inteiro por token é o que trava o terminal.
- Caret de streaming `▍` no acento.
- Métrica de aceitação: **tempo até o primeiro caractere visível** cai de
  "duração do turno" para < 1s no local.

### F2 — HUD persistente + trilho (≈2 dias) · resolve D3 e D5

**O que o usuário passa a ver.** A barra de estado para de sumir. Hoje ela é
`bottom_toolbar` do prompt_toolkit: existe enquanto o prompt espera e apaga no
Enter — justamente quando a máquina começa a trabalhar. Depois do F2 o rodapé
fica: selo `◆ local` / `☁ nuvem`, modelo **do turno**, medidor de contexto,
custo acumulado, **tok/s ao vivo** e a esteira do Kernel acendendo.

**Arquitetura — o ponto que decide o resto.** Um `Live` **por turno**, não por
bloco. O F1 abre um `Live` transitório por bloco de texto; isso funciona para
streaming mas não sustenta um rodapé fixo. O F2 inverte:

    ┌ histórico ─────────────┐  ← console.print() normal; rola por cima
    │ ...                    │
    ├ região viva (Live) ────┤  ← trilho de tools + HUD; sempre redesenhada
    └────────────────────────┘

O Rich já suporta `console.print()` **enquanto** um `Live` está ativo — a linha
impressa sobe e a região viva permanece colada embaixo. O `ConsoleStreamRenderer`
do F1 muda pouco: continua selando bloco por `console.print`, mas a prévia crua
passa a morar na região viva compartilhada em vez de abrir `Live` próprio.

**Arquivos:**
- `bauer/ui_hud.py` (novo) — `HudState` (dataclass pura: selo, modelo, ctx_pct,
  custo, tok/s, estado do Kernel, rodada) + `render_hud(state)`. Puro e testável
  como o resto do kit.
- `bauer/ui_frame.py` (novo) — `TurnFrame`: dono do único `Live`, expõe
  `print_above()`, `set_hud()`, `set_rail()` e o `suspend()` abaixo.
- `bauer/agent.py` — a closure `_bottom_toolbar` (hoje monta o HTML na mão,
  [agent.py:4614](bauer/agent.py:4614)) passa a consumir o **mesmo** `HudState`.
  Dois transportes (HTML do prompt_toolkit e `Text` do Rich), um estado só.

**`ui.suspend()` — o contrato que mata o D5 na raiz.** Context manager que para
o `Live`, devolve o terminal, roda o `input()` e volta. Com ele caem as
allowlists `_INTERACTIVE_TOOLS` / `_CONFIRM_CAPABLE_TOOLS` /
`_CONFIRM_EXEC_ACTIVE` ([agent.py:1401-1408](bauer/agent.py:1401)), que hoje
existem só porque não havia como suspender o display — a lista precisa ser
mantida à mão a cada tool interativa nova, e esquecer uma reintroduz o bug do
"totodo" (texto digitado sumindo).

**Esteira do Kernel — dado real, não enfeite.** A máquina de estados existe:
`created → planning → policy_check → queued → running → evaluating → completed`
([states.py:18](bauer/core/kernel/states.py:18)), e o Kernel publica
`run.state.changed` no EventBus para os estados novos
([states.py:36](bauer/core/kernel/states.py:36)). O HUD assina esse tópico via
`event_bus.subscribe` ([event_bus.py:222](bauer/event_bus.py:222)). Quando o
Kernel está desligado, a esteira some — não inventa estado.

**Riscos:** `Live` + `input()` (precedente real neste repo); prompt_toolkit e
Rich disputando o terminal — a regra é que nunca coexistem (prompt só com o
`Live` parado); console legado do Windows.

**Testes:** golden de `render_hud` por estado; `suspend()` devolve e restaura;
regressão do "totodo" — aprovação dentro do frame não pode comer caractere;
teste que garante que nenhuma allowlist de tool é mais necessária.

---

### F3 — Blocos de execução ricos (≈2 dias) · resolve D4

**O que o usuário passa a ver.** A ação deixa de ser uma linha morta
(`[✓] write_file  auth.py`). Em execução, o bloco mostra as últimas 3 linhas da
saída em `dim` (um `docker build` para de parecer travado); ao terminar,
**colapsa** para uma linha com resumo e tempo. Edição de arquivo mostra o diff.

**Duas descobertas que mudam o trabalho:**

1. **O diff já existe.** `edit_file` monta um `unified_diff` e o embute no
   resultado da tool ([fs.py:462-473](bauer/tools/fs.py:462)). O F3 não precisa
   calcular diff nenhum — precisa **parsear e colorir** o que já vem pronto.
   Trabalho muito menor e mais fiel (é o diff que a tool de fato aplicou, não
   uma recomputação que pode divergir).
2. **O tempo nunca foi medido.** `tool_line` aceita `elapsed_ms`
   ([ui.py:84](bauer/ui.py:84)), mas **nenhum** dos dois call sites o passa
   ([agent.py:2036](bauer/agent.py:2036),
   [agent.py:3874](bauer/agent.py:3874)) — o componente sabe exibir uma duração
   que o agente nunca cronometrou. O F3 precisa instrumentar a execução da tool,
   não só mudar o render. (Os "41ms" de qualquer mockup são, hoje, ficção.)

**Arquivos:**
- `bauer/ui_diff.py` (novo) — parser de unified diff → `Text` colorido
  (`+` ok, `-` bad, `@@` faint), com teto de linhas e queda para ASCII/sem-cor.
- `bauer/ui.py` — `tool_block(name, args, *, status, elapsed_ms, corpo)`.
- `bauer/agent.py` — cronômetro em volta de `router.execute_native_call` e do
  caminho bridge; alimenta `elapsed_ms` e o corpo do bloco.
- Card de aprovação: o `Panel` amarelo cru de
  [agent.py:1423](bauer/agent.py:1423) vira componente do tema, usando o
  `suspend()` do F2, com destaque no `a` — a opção que **ensina** o allowlist.

**Riscos:** saída de tool pode ser enorme (teto de linhas obrigatório) ou conter
sequência ANSI vinda de subprocesso (sanitizar antes de renderizar, senão o
terminal do usuário vira refém do output).

**Testes:** golden do colorizador para diff real de `edit_file`; teto de linhas;
colapso; `elapsed_ms` medido de fato (e não zero); ANSI de subprocesso neutralizado.

---

### F4 — Boot ultrarealista (≈1 dia) · resolve D6

**O que o usuário passa a ver.** Hoje: arte estática, depois a sessão. Depois do
F4: as checagens **que já acontecem** aparecendo uma a uma conforme resolvem —
RAM disponível, contexto aplicado, provider, modo de tool calling, tools
carregadas, local ou nuvem. O logo entra **no fim**, quando a máquina está
pronta. É o mesmo tempo de boot mostrando o que sempre fez em silêncio.

**Fonte de dados:** `preflight.run_doctor()` → `DoctorReport`
([preflight.py:121](bauer/preflight.py:121)) e
`runtime_capability.modo_de_tool_calling`. Nada de checagem nova.

**`bauer serve`** ganha a mesma sequência e — o que falta hoje — **anuncia o
cockpit**: o SPA é servido em `/`
([server.py:1280-1293](bauer/server.py:1280)) e o boot imprime seis linhas de
texto sem mencioná-lo ([serve_cmd.py:167-191](bauer/commands/serve_cmd.py:167)).
Recurso caro e invisível.

**Risco que manda na fase:** não regredir o tempo de startup. A memória do
projeto registra 23s → 2s conquistados com SSL compartilhado e `AuthManager`
preguiçoso; um boot "bonito" que force checagem antecipada joga isso fora. O
teste de aceitação é medir o startup antes e depois — se subir, a fase falhou.

**Testes:** render do boot a partir de um `DoctorReport` falso; assert de que
**nenhuma** chamada HTTP nova é feita pelo boot; medição de tempo.

---

### F5 — Cockpit web com a mesma língua (≈2–3 dias)

**O que o usuário passa a ver.** O SPA para de parecer outro produto. Mesmo
grafite, mesmo violeta, mesmos glifos; o chat vira timeline com o mesmo trilho
do terminal e um HUD no topo com selo, contexto, custo e tok/s.

**O caminho é mais curto do que parece.** O `styles.css` já usa `var(--…)` em
**163** pontos, e o `tokens.css` gerado no F0 traz uma camada de alias
(`--bg`, `--accent`, `--text`…) apontando para os tokens do Bauer. Importar o
arquivo já vira quase tudo de uma vez. Sobram ~30 hex literais (o mais comum é
`#e6edf3`, 10 ocorrências) para converter à mão.

**Eventos já existem:** o `/stream` emite `delta`, `tool` e
`hermes.tool.progress` ([server.py:2595](bauer/server.py:2595)) e o front já os
consome ([Chat.tsx:301](desktop/src/screens/Chat.tsx:301)). O trabalho é de
**apresentação**, não de protocolo.

**Detalhes operacionais que mordem:**
- O build do Vite emite **direto** em `bauer/static` com `emptyOutDir`
  ([vite.config.ts](desktop/vite.config.ts)) — os assets versionados são
  gerados; mexer no SPA exige `npm run build` e commit dos assets.
- O proxy de dev aponta para `127.0.0.1:**5174**` enquanto o comentário do
  próprio arquivo fala em `:8000`, que é o default real do serve
  ([config_loader.py:602](bauer/config_loader.py:602)). Conferir ao mexer — hoje
  `npm run dev` só fala com um serve subido em 5174.

**Micro-animações (as únicas):** caret de streaming, glow no bloco vivo, shimmer
no medidor acima de 85%. Todas presas a um dado real; nenhuma decorativa.

**Testes:** verificação no browser (console sem erro, rede, render claro/escuro,
responsivo) — é o que o preview do harness cobre de fato.

---

### F6 — Blindagem (≈0,5–1 dia)

**Matriz de terminais**, cada um com um modo de falha próprio já visto neste
repo: Windows Terminal (utf-8) · **cmd legado (cp1252 → ASCII)** · Git Bash (sem
pty em alguns casos, ver [agent.py:229](bauer/agent.py:229)) · CI sem TTY ·
`NO_COLOR` · `BAUER_UI=plain` · largura 60 colunas.

**Teste de custo de render** — o que impede a volta do comportamento
quadrático: alimentar N tokens e afirmar que o trabalho de render cresce
linearmente, não com N². É a única defesa real contra "ficou lindo e travou em
resposta longa".

**A régua da §5 medida de fato**, não estimada: tempo até o primeiro caractere,
% do turno com feedback específico, nº de paletas, ações com corpo.

---

**Total restante: ~7–9 dias** (F2 2 · F3 2 · F4 1 · F5 2–3 · F6 0,5–1).
F0+F1, já entregues, custaram menos que o previsto no render e mais no
provider — ver a correção de escopo no F1.

---

## 4b. O que já está de pé (2026-08-01)

**F0 — fundação de tokens.** `bauer/theme.py` é a fonte única: paleta, glifos
com par ASCII, detecção de cor/Unicode e `export_css_vars()`, que **gera**
`desktop/src/tokens.css` (teste falha se divergir). `ui.py`, `ascii_intro.py` e
`indicators.py` importam dali; um teste proíbe hex literal nesses três. A
detecção de glifos é acionada no boot da sessão — sem isso o mecanismo existiria
sem nunca rodar, e o cmd legado quebraria no primeiro `❯`.

**F1 — streaming.** Três peças novas:
- `bauer/stream_tools.py` — remonta tool calls fatiadas; concilia os três
  dialetos (OpenAI manda o nome uma vez; alguns repetem inteiro; outros fatiam).
- `chat_with_tools(..., on_delta=...)` nos dois clients — mesmo dict de retorno,
  texto entregue no caminho. O turno nativo do agent não mudou de contrato.
- `bauer/ui_stream.py` — render por bloco: o bloco em curso vive num `Live`
  transitório como texto cru; ao fechar, some e reaparece formatado. Uma
  parseada por trecho, texto no primeiro token.

**Consertado de passagem:** o ramo de sink não tinha retry nenhum — quem
instalava sink (o gateway) trocava, sem saber, o backoff de 429/5xx por falha
seca. Agora tem retry **a frio** (`_stream_to_sink`): depois do primeiro token
a falha sobe, porque retentar reimprimiria a resposta.

**Verificação:** 147 testes novos, verdes; a fatia `-k "agent or client or
stream or ollama or openai or config"` da suíte (~1400 testes) passa sem
regressão. Falta o teste de campo: rodar contra modelo vivo num terminal real.

---

## 4c. F2 entregue (2026-08-03)

**`bauer/ui_hud.py`** — `HudState` (imutável) + dois renders sobre o MESMO
estado: `render_hud()` (Rich, durante o turno) e `render_hud_html()`
(prompt_toolkit, enquanto o prompt espera). Dois transportes porque são dois
mecanismos de desenho, não duas fontes de verdade.

**`bauer/ui_frame.py`** — `TurnFrame` (o único `Live` do turno: histórico rola
por cima, HUD colado embaixo, trilho no meio) + o registro `register()`/
`suspend()` que substitui a allowlist, + `current_frame()` (ContextVar) para o
código fundo do loop achar o quadro sem recebê-lo por parâmetro em três níveis.

**Três achados durante a execução:**

1. **A `bottom_toolbar` escapou inteira do F0.** Ela montava o HTML na mão com
   `#00d4aa`/`#3b82f6` hardcoded — o teste que proíbe hex literal cobre
   `ui.py`/`ascii_intro.py`/`indicators.py`, não `agent.py`. Só apareceu ao
   unificar as duas superfícies no mesmo `HudState`.
2. **`ThreadPoolExecutor` não herda `ContextVar`.** No batch paralelo do bridge,
   o `suspend()` de uma tool rodando na worker não enxergava o display
   registrado na thread principal — `suspend()` virava no-op silencioso e o bug
   do "totodo" voltaria pelo caminho paralelo. Corrigido com `copy_context()`
   **por submit** (um `Context` não pode rodar em duas threads ao mesmo tempo:
   `.run()` levanta "already entered" — verificado na prática).
3. **O primeiro teste dessa correção não testava nada.** Ele afirmava "o
   `input()` saiu limpo", o que passa com ou sem o fix (com input mockado nada
   colide de verdade). Reescrito para afirmar que o display foi **pausado** —
   e validado por mutação: falha sem o fix, passa com ele.

**Pendente do F2:** a esteira do Kernel não está ligada ao EventBus. O
`HudState` já a suporta e `kernel_estado=None` a mantém oculta — coerente com a
regra "não inventa estado".

---

## 5. Como saber se funcionou (a régua antes do trabalho)

Memória do projeto: *quando um número não sobe, o defeito estava na régua 4 de
4 vezes*. Então a régua vem primeiro:

| Indicador | Hoje | Meta |
|---|---|---|
| Tempo até o 1º caractere visível da resposta | = duração do turno | < 1s (local) |
| % do turno com feedback específico (não spinner genérico) | ~0% | > 90% |
| Nº de paletas no produto | 3 | 1 (com teste de divergência) |
| Ações de tool com corpo (diff/saída) | 0% | 100% de write/edit/run |
| Superfícies com HUD durante execução | 0 | 2 (CLI + web) |

---

## 6. Riscos conhecidos (todos com precedente neste repo)

1. **Live + `input()`** — incidente já ocorrido. Mitigado por `ui.suspend()` em
   F2; sem ele, F3 reintroduz o bug.
2. **Custo de render por token** — Markdown reparseado por delta trava. Mitigado
   pelo buffer por bloco em F1.
3. **Windows** — blocos e emoji já exigiram `reconfigure(utf-8)` e
   `legacy_windows=False`. Fallback ASCII é requisito de F0, não polimento final.
4. **Divergência voltar** — mitigada pela geração do CSS a partir do Python.
5. **Regressão de suíte** — `ui.py` é testado por `render_str`; qualquer
   componente novo nasce puro e testado igual.

---

## 7. Decisões em aberto (do usuário)

1. **Acento**: manter teal `#00d4aa` (recomendado — já é o Bauer) ou repaginar?
2. **Escopo do primeiro corte**: F0+F1 (streaming, 2 dias, impacto máximo) ou o
   pacote CLI completo F0–F4 (~7 dias) antes de tocar no web?
3. **Trilho de execução**: sempre visível ou só quando há mais de uma tool no
   turno?
