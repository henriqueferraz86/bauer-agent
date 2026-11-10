# SPEC — Sprint 75: conectores de escrita sob aprovação (plano 067)

Plano: [`plans/067-agno-conectores-fase-2.md`](../../plans/067-agno-conectores-fase-2.md),
sprint 75. Segue o padrão da sprint 57
([`specs/sprint-57-agno-write-connector/`](../sprint-57-agno-write-connector/))
e encaixa nos contratos das sprints 61 (efeitos e `dry_run`), 62 (rede,
redirect) e 69 (operação de rede, `--local`). Branch
`codex/sprint-75-agno-write-connectors`, base `origin/master` `7298f0d`.

## Problema

Depois do piloto `bauer.github_issues`, o catálogo continuava sem nenhum
outro caminho de escrita. Slack, Linear e GitLab apareciam só como toolkits
do SDK Agno em `blocked_by_policy`, porque misturam leitura e escrita sem
escopo e sem aprovação da chamada exata.

## Escopo

Três capabilities **Bauer-native** (httpx direto, sem SDK novo e sem mexer
em `pyproject.toml`/`uv.lock`), cada uma com uma única ação de escrita:

| Capability | Ação | Alvo permitido | API |
|---|---|---|---|
| `bauer.slack_messages` | `post_message(channel, text, thread_ts?)` | IDs de canal (`C…`/`G…`) | `slack.com/api/chat.postMessage` |
| `bauer.linear_issues` | `create_issue(team, title, description?, project?, labels?)` | chaves de time → UUID; projetos e labels por nome → UUID | `api.linear.app/graphql` (`issueCreate`) |
| `bauer.gitlab_issues` | `create_issue(project, title, description?, labels?)` | caminhos `grupo/projeto`; labels permitidas | `<base_url>/api/v4/projects/:id/issues` |

Para cada uma:

1. **Catálogo.** Entrada própria (`source: bauer`, risco `write`), sem
   `default_actions`. Fica `needs_configuration` até o operador escrever a
   seção em `$BAUER_HOME/agno_capabilities.yaml`; depois `ready`, e `enabled`
   no agente que a declara com a ação explícita. Os toolkits do SDK
   (`agno.slack`, `agno.linear`, `agno.gitlab`) continuam `blocked_by_policy`.
2. **Policy.** Operação `integration.write` (`ALWAYS_ASK_OPERATIONS`): piso
   `ask` que nenhum `policy.yaml` rebaixa; o modo `audit` não auto-aprova; com
   policy desligada a ponte recusa.
3. **Aprovação.** `ApprovalManager` existente: vinculada a run, sessão, tool e
   digest dos argumentos exatos; uso único; negada/expirada não autoriza.
4. **Preflight antes da aprovação** (sem rede, sem pedido de aprovação):
   `--local`, alvo fora da allowlist, label/projeto fora do mapa, argumento
   desconhecido, limites de tamanho, menção de broadcast (Slack), material
   secreto nos argumentos e credencial ausente.
5. **Resumo de aprovação seguro.** Uma linha, truncada e passada por
   `redact_text`: alvo, efeito, trecho do título/texto e o **nome** da
   credencial. Nunca o token.
6. **Idempotência por run.** Ledger com reserva exclusiva por chave e
   marcador remoto (ver ARCHITECTURE). Retry do agente, do Kernel ou após
   timeout nunca duplica.
7. **`dry_run`.** Regra da sprint 61: capability com `permission != read` é
   simulada. Nada sai, e o ledger não é tocado.
8. **Eventos.** Os do router: só nomes de argumentos, erro só com o tipo para
   capabilities. Nenhum valor e nenhum token.
9. **Erros.** `ToolError` com texto fixo + status HTTP ou código de erro da
   API validado (`^[a-z_]+$`). Nunca texto de exceção, corpo de resposta ou
   header.
10. **Timeouts.** `timeout_seconds` por seção (padrão 15 s, máximo 60); o
    timeout da tool cobre a escrita e a verificação.

## Decisões

1. **Adapters Bauer em vez dos toolkits do SDK.** O padrão exige escopo por
   alvo, preflight, resumo, idempotência e erros sem vazamento. Os toolkits
   Agno não têm nenhum desses ganchos, e o plano pede httpx direto. Os ids
   `agno.*` ficam bloqueados; os novos seguem o nome de `bauer.github_issues`.
2. **Credenciais: `bauer credential` + ambiente, pelo nome.** É o
   `resolve_credential` das capabilities (env primeiro, depois o
   `CredentialPool`). `bauer auth` guarda credencial de *provider de modelo*
   (e OAuth de provider) e não é consultado: um token de bot do Slack não é
   provider. Nomes padrão: `SLACK_BOT_TOKEN`, `LINEAR_API_KEY`,
   `GITLAB_TOKEN`, configuráveis.
3. **Ledger com reserva exclusiva e estado `pending`.** Cada chave é um
   arquivo criado com `O_CREAT|O_EXCL`, então duas chamadas idênticas
   (threads ou processos) nunca chegam juntas à API. A falha é classificada:
   - **certeza de que nada foi criado** (conexão recusada, 401/403/404/422/429,
     `ok:false` do Slack, redirect): a reserva é liberada e um retry envia;
   - **efeito incerto** (timeout depois do envio, 5xx, 408, resposta
     malformada, exceção inesperada): a reserva fica `pending`. O retry
     consulta o marcador remoto; achou ⇒ devolve o mesmo resultado
     (`idempotent_replay`).
4. **Incerto sem marcador: Slack e GitLab não reenviam.** Sem dedupe do lado
   do servidor, reenviar arriscaria duplicata. O erro pede verificação
   manual. É o preço de "retry nunca duplica"; a chave inclui o run, então um
   run novo pode repetir a ação conscientemente.
5. **Linear deduplica no servidor.** O id da issue é um UUID derivado da chave
   e enviado em `IssueCreateInput.id`. Um reenvio usa o mesmo id; o Linear
   recusa a segunda criação e a busca por id devolve a issue existente. Por
   isso o Linear pode reenviar depois de um incerto sem marcador.
6. **Marcadores.** Slack: `metadata` da mensagem
   (`event_type: bauer_idempotency`), consultada em `conversations.history`
   (ou `conversations.replies` na thread) — exige `channels:history`; sem o
   escopo, a verificação falha fechada. GitLab: comentário HTML
   `<!-- bauer-idempotency: … -->` na descrição, buscado com
   `search=<chave>&in=description`. Diferente do GitHub, a busca só acontece
   quando há tentativa incerta: o ledger exclusivo já cobre o caso normal e
   evita depender de índice de busca eventual.
7. **Preflight mais estrito que o piloto.** Credencial ausente e material
   secreto são recusados *antes* da aprovação: o aprovador nunca vê um pedido
   que não pode rodar, e o card/store de aprovação nunca recebe segredo. O
   `bauer.github_issues` não foi alterado.
8. **`approval_id` como metadado de transporte na ponte.** Para operações
   `ALWAYS_ASK`, a ponte tira `approval_id` dos argumentos antes do preflight
   e da função; o router continua verificando o id contra os demais
   argumentos (o digest já ignorava `approval_id`). Isso habilita o fluxo
   "aprovar na UI/no modo audit e repetir com o id", que antes falhava em
   `argumentos desconhecidos`/`TypeError`. Não cria atalho: sem id válido
   para os argumentos exatos, a chamada continua em `waiting_approval`.
9. **`--local` (`BAUER_LOCAL_ONLY`).** Slack e Linear são nuvem: sempre
   recusados. GitLab só é aceito com `base_url` nesta máquina — o mesmo
   conjunto de hosts da sprint 62/69 (`localhost`, `127.0.0.1`, `::1`,
   `0.0.0.0`, `host.docker.internal`). Um GitLab em rede privada (`10.x`) não
   é "esta máquina" e é recusado. A recusa acontece no preflight e de novo na
   execução, antes de qualquer aprovação ou request.
10. **`base_url` do GitLab é configuração do operador.** O modelo nunca escolhe
    host. Validação ao carregar: `https` (`http` só para esta máquina), sem
    user-info, query ou fragmento, sem `.`/`..` no caminho, e nunca host de
    metadata, link-local, multicast, não especificado ou reservado. Rede
    privada é aceita: é onde um GitLab self-hosted costuma estar, e quem
    escreveu o endereço foi o operador. Não há resolução DNS no momento da
    chamada (o host é do operador; ver pendências).
11. **Redirect nunca é seguido** (`follow_redirects=False` e 3xx = erro, nos
    três). Assim nenhum header de credencial chega a outra origem, que é a
    regra da sprint 62 levada ao limite: nesta integração não existe redirect
    legítimo.
12. **Slack.** Só IDs de canal público/privado (`C…`/`G…`); DMs ficam fora
    (dados pessoais, plano 067). Menções de broadcast (`<!channel>`,
    `<!here>`, `<!everyone>`, grupos) são recusadas por padrão
    (`allow_broadcast_mentions: false`). `unfurl_links`/`unfurl_media`
    desligados.
13. **Linear.** O operador mapeia nomes para UUIDs (`teams`, `projects`,
    `labels`); o modelo só cita chaves desses mapas. `auth_scheme: api_key`
    (padrão, `Authorization: <chave>`) ou `bearer` (OAuth).
14. **Falha do scanner de segredo fecha o portão.** Em `bridge.py`, o
    comentário dizia "não abrir em silêncio", mas o código tratava exceção do
    scanner como "sem segredo". Agora recusa (encontrado na revisão de
    segurança; teste próprio).
15. **Snapshot do SDK não muda.** `sdk_snapshot.json` só lista toolkits do
    SDK; a matriz de cobertura ganha as três linhas `bauer.*` sozinha.
    `catalog_coverage.py` não foi tocado (evita conflito com as sprints
    73/74).

## Fora de escopo

- Outros conectores de escrita; leitura no Slack/Linear/GitLab.
- Ativar qualquer conector por padrão.
- Gmail, Drive, Calendar, WhatsApp e mídia paga (continuam bloqueados).

## Critérios de aceite

- [x] Sem aprovação nenhum request (enforce negado, audit, policy custom
  `allow`, policy off).
- [x] Aprovação para A não autoriza B; uso único; retry não duplica (ledger,
  marcador, id do Linear).
- [x] Fora de escopo, argumento desconhecido, segredo e credencial ausente
  recusados antes de pedir aprovação, sem rede.
- [x] `dry_run` não envia nada.
- [x] Token nunca em eventos, cards de aprovação ou erros.
- [x] `--local` recusa nuvem e aceita GitLab nesta máquina.
- [x] `base_url` perigosa recusada; redirect nunca seguido.
- [x] Catálogo: `needs_configuration` → `ready` → `enabled`.
- [x] Gates do `AGENTS.md`.

## Configuração de exemplo (`$BAUER_HOME/agno_capabilities.yaml`)

```yaml
capabilities:
  bauer.slack_messages:
    channels: [C0123ABCD]
    credential: SLACK_BOT_TOKEN
  bauer.linear_issues:
    teams: {ENG: 6f1c1b54-6f1a-4c3e-9a51-2f1f6d0b7a10}
    labels: {bug: a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d}
    credential: LINEAR_API_KEY
  bauer.gitlab_issues:
    base_url: https://gitlab.example.com
    projects: [acme/app]
    allowed_labels: [bug]
    credential: GITLAB_TOKEN
```

Agente: `tools: [bauer.slack_messages:post_message]` (a ação é obrigatória).

## Plano de validação

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
```

Regressões: `tests/test_sprint75_agno_write_connectors.py`.

## Pendências

- O catálogo mostra `ready` para Slack/Linear numa sessão `--local`; a
  recusa vem na chamada (antes da aprovação). Refletir `--local` no estado do
  catálogo exige um campo de localidade por capability.
- O texto de bloqueio de `agno.slack`/`agno.linear`/`agno.gitlab` em
  `catalog_coverage.py` ainda cita o STOP do piloto; ajustar junto com a
  próxima revisão daquele arquivo.
- `base_url` do GitLab não é re-checado por DNS na chamada; um nome do
  operador que resolva para metadata não é detectado.
- O catálogo não reflete credencial ausente (o nome é configurável por
  seção, como no piloto); a recusa é no preflight.
