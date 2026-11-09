# BACKLOG — Sprint 62

## Tarefas

### 1. Reproduzir T02, T03, WEB-02 e WEB-03 no código atual

Status: concluído

Os quatro reproduzem na base `ddd608c` (tabela no `SPEC.md`). Os scripts
`bauer_web_repro2.py` e `bauer_web_crossreview_kernel.py` foram consertados
para rodar até o fim. A espera por B virou uma observação
(`CONCURRENT_B_OVERLAPPED_A`), porque o WEB-04 serializa a sessão.

### 2. T02 — `http_request`/`web_fetch` sob `network.http`

Status: concluído

Critérios:
- Com `deny`, nada chega ao transporte nem ao `WebDispatcher.extract`.
- Com `allow`, a chamada passa. Com `ask`, fica um approval pendente e nenhum
  request sai.
- Um segredo detectado pelo runtime aciona a regra padrão sem a flag do
  modelo. `Authorization` legítimo passa.

### 3. T03 — redirect sem vazar credencial

Status: concluído

Critérios:
- Na mudança de host, de scheme ou de porta, os headers de credencial são
  removidos. Na mesma origem, são preservados.
- O 303 vira GET sem corpo. O 307 preserva método e corpo na mesma origem e
  não é seguido para outra origem.
- O hop interno é bloqueado antes do request.

### 4. WEB-02 — iniciar time só com POST+CSRF

Status: concluído

Critérios:
- O GET devolve 405 sem chamar o orquestrador. O POST sem CSRF devolve 403.
  O POST com CSRF transmite os eventos.
- O SPA não chamava a rota, então não houve mudança de UI nem rebuild do
  bundle.

### 5. WEB-03 — `--local` depois do boot

Status: concluído

Critérios:
- Com `--local`, nenhuma destas portas leva a um client cloud: switch,
  seleção salva, OAuth, fallback e roteamento por tier.
- O boot recusa `profiles_local` com tier remoto.
- O switch local→local e o modo default não mudam de comportamento.

### 6. Gates e revisão de segurança

Status: concluído

Todos os gates passaram: pytest (exit 0), ruff crítico, ruff estilo e mypy.
Na revisão do diff, nenhum caminho novo amplia privilégio e o payload
enriquecido não altera o digest de aprovação.

## Notas de entrega

- Branch: `codex/sprint-62-security-network-web`, base `origin/master`
  `ddd608c`.
- Pendências fora do escopo:
  - `web_search`, `mcp_call` e `delegate_task` ainda não têm contrato de
    operação de rede;
  - o `/model` do `bauer agent --local` interativo reescreve o `config.yaml`
    sem a guarda `--local`;
  - o `bauer_audit_tools_probe_extra.py` (que lê de `/tmp`) é compartilhado
    com a sprint 59/61 e não foi editado aqui. As provas de T02/T03 estão nos
    testes de regressão.
