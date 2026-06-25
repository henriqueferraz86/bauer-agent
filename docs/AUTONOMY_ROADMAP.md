# Bauer — Roadmap de Autonomia ("rumo ao paperclip")

Comparativo do Bauer contra um **framework de autonomia de agente (L0–L5)**
sintetizado do estado-da-arte (ReAct, Reflexion, Voyager, SWE-agent, AutoGPT),
com **score por tópico** e plano de sub-tasks pra subir de nível.

> Metáfora "paperclip maximizer" = agente que pega um objetivo e executa de ponta
> a ponta, sozinho e confiável. O objetivo aqui **não** é remover o humano, e sim
> subir a autonomia **junto com** trilhos determinísticos (gates, containment).

Última avaliação: **2026-06-25** · Estado global estimado: **~L2.5**

---

## 1. Escala de autonomia (L0–L5)

| Nível | Nome | O que caracteriza |
|---|---|---|
| **L0** | Manual | Humano dirige cada passo; agente é só ferramenta. |
| **L1** | Assistido | Agente faz passos isolados sob pedido; humano encadeia. |
| **L2** | Execução supervisionada | Agente executa tarefa multi-step com humano aprovando/corrigindo (ReAct + tools). |
| **L3** | Entrega de projeto supervisionada | Agente recebe objetivo, planeja, executa N tarefas, recupera de erros; humano revisa marcos (SWE-agent + reflexão). |
| **L4** | Autonomia auto-verificável | Agente planeja, executa **e verifica o próprio produto** (builda/roda/testa), se autocorrige, acumula skills (Voyager). Humano define meta + revisa o final. |
| **L5** | Autônomo aberto | Agente define sub-metas, roda sem supervisão em horizonte longo, melhora sozinho, gerencia o próprio orçamento. (território paperclip) |

---

## 2. Scorecard do Bauer (hoje)

Score 0–10 por dimensão, com nível atual e justificativa ancorada no código real.

| # | Tópico | Score | Nível | Justificativa (estado real) |
|---|--------|:---:|:---:|---|
| 1 | **Planejamento & decomposição** | 7 | L3 | App Factory (Spec-Driven, 7 docs, gates) + Orchestrator (DAG paralelo) + BACKLOG/TASKS. Estrutura forte; qualidade depende do LLM, mas os gates forçam o rito. |
| 2 | **Uso de tools & execução** | 7 | L3 | ~60 tools, tool calling nativo + bridge, execução paralela, dedup. Sólido **após** corrigir ~9 tools que estavam quebradas (run_one_turn). |
| 3 | **Auto-verificação** | 2 | L1 | 🔴 **Maior gargalo.** O Bauer NÃO builda/roda/testa o app que gera. "Pronto" = arquivos presentes (Delivery Score), não "o app funciona". |
| 4 | **Recuperação de erro & resiliência** | 7 | L3 | Retry+backoff, fallback chain (`_fb_idx`), circuit breaker, loop detection, recovery de resposta vazia. Endurecido esta semana. |
| 5 | **Memória & aprendizado** | 5 | L2–L3 | SQLite+vetorial, learning engine, MODEL_EXPERIENCE, skill registry, self-tuner. Tem as peças, mas "aprende" ≈ loga; pouco fecha o loop em mudança de comportamento. |
| 6 | **Coerência de horizonte longo** | 4 | L2 | Context manager (compressão), 150 tool turns, sessão persistente. Mas hard-stop de loop em 5 repetições e desvios reais (não usou `clarify`, escreveu fora da pasta) = coerência frágil. |
| 7 | **Segurança & guardrails** | 7 | L3 | Approval system, HARDLINE blocks, sandbox/anti-traversal, secrets scanner, audit log, gates da App Factory + **containment** (novo), dry_run. Guardrails à frente da autonomia — ordem certa. (Ressalva: "modo autônomo tudo-liberado" afrouxa.) |
| 8 | **Autonomia de metas** | 3 | L2 | Executa metas dadas; não define sub-metas além de decompor tarefa. cronjob/dogfooding são agendados, não auto-dirigidos. (E é aqui que se quer cautela.) |

**Média ≈ 5.25/10 → nível global ~L2.5** (execução supervisionada, encostando em entrega de projeto supervisionada).

**Teto atual:** a dimensão #3 (auto-verificação) trava tudo. Sem provar que o
produto funciona, não dá pra confiar em rodadas não supervisionadas → autonomia
real fica capada em L2–L3 por mais peças que existam.

```
Planejamento      ████████░░ 7  L3
Tools/execução    ████████░░ 7  L3
Auto-verificação  ██░░░░░░░░ 2  L1  ← gargalo
Recuperação       ████████░░ 7  L3
Memória/aprend.   █████░░░░░ 5  L2-3
Coerência longa   ████░░░░░░ 4  L2
Segurança         ████████░░ 7  L3
Autonomia metas   ███░░░░░░░ 3  L2
```

---

## 3. Plano — sub-tasks por fase (ordenado por alavancagem)

### Fase P1 — Auto-verificação (destrava L3.5; **prioridade máxima**)
Ataca a dimensão #3, o teto de tudo.
- **P1.1** `verify_app`: após implementação, detectar a stack (npm/pip/docker/go), buildar o app gerado e capturar erros.
- **P1.2** Smoke run: subir o app (ou rodar a suíte de testes DELE) e bater num healthcheck/endpoint/saída esperada.
- **P1.3** Loop de autocorreção (Reflexion): em falha de build/run, devolver o erro ao agente para uma tentativa de fix **limitada** (N tentativas), não infinita.
- **P1.4** Delivery Score com sinal "roda de verdade" (não só presença de arquivo).
- **P1.5** Testes que exercitam o caminho REAL (anti-padrão "mockar a camada errada" — apareceu 3× esta semana).

### Fase P2 — Coerência de horizonte longo (L2→L3)
Ataca #6.
- **P2.1** Trocar o hard-stop de loop por **rastreio de progresso** (o agente avançou o TASKS.md / kanban?).
- **P2.2** Ledger de tarefas: agente trabalha contra TASKS.md, marca feito, pega a próxima — coerência ancorada no doc, não no chat.
- **P2.3** Re-grounding periódico em runs longas ("onde estou vs a SPEC?").

### Fase P3 — Fechar o loop de aprendizado (L3→L4)
Ataca #5.
- **P3.1** Skill library que é **reusada** (Voyager): padrões de tarefa bem-sucedidos viram skills chamáveis.
- **P3.2** Post-mortem de falha → mudança concreta de comportamento (não só log).
- **P3.3** Self-tuner escolhe modelo/params a partir de dados de outcome reais.

### Fase P4 — Metas & horizonte não supervisionado (L4→L5, **com freios**)
Ataca #8 — só depois de #3 sólido.
- **P4.1** Marcos + dependências entre tarefas (tasks #44/#50 pendentes) — autonomia por milestone.
- **P4.2** Runs não supervisionadas limitadas por **orçamento** (USD/tempo já existem; cablear à autonomia).
- **P4.3** Regra de ouro: cada nova capacidade autônoma ganha um **gate determinístico** antes (como o containment).

### Fase P5 — Score como artefato vivo
- **P5.1** `bauer autonomy score`: encodar o rubric L0–L5 + dimensões como comando objetivo (igual ao Delivery Score).
- **P5.2** Rastrear o score ao longo do tempo (regressão: autonomia não pode cair sem aviso).

---

## 4. Critério de "pronto" por nível

- **Chegar a L3** (entrega supervisionada confiável): P1 + P2 concluídos — o Bauer entrega um app que **comprovadamente roda**, e se mantém coerente numa tarefa de muitos passos.
- **Chegar a L4** (auto-verificável): + P3 — aprende com o que deu certo/errado e reusa.
- **Chegar a L5** (aberto, com freios): + P4 — só com auto-verificação madura e um gate por capacidade.

**Próximo passo concreto recomendado:** começar por **P1.1 + P1.2** (build + smoke do app gerado). É o maior salto de confiabilidade por unidade de esforço e o que mais aproxima o Bauer do "paperclip".
