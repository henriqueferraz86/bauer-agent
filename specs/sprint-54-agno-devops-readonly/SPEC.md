# SPEC — Sprint 54: DevOps — Docker e AWS/EKS em leitura (plano 057, sprint "22")

Depende da sprint 50. Executada como 54.

## Problema

O Agno 3.0.10 traz `DockerTools` (24 funções, leitura e escrita misturadas,
logs/env sem redação), `AWSLambdaTools` (invoke executa código) e
`AWSSESTool` (envia e-mail). Não há toolkit Kubernetes/EKS no SDK. `docker`,
`boto3` e `kubernetes` não estão no lock.

## Escopo

Três capabilities Bauer-native, somente leitura, em que a mutação é
**estruturalmente impossível** (condição STOP do plano):

1. `bauer.docker` — Docker Engine API pelo socket configurado, com
   `ReadOnlyTransport` que recusa qualquer método ≠ GET **antes da rede**.
   Allowlist de containers por padrão de nome; `inspect` devolve só nomes de
   variáveis de ambiente e destinos de mounts; logs com tail limitado,
   demultiplexados e redigidos (scanner de segredos + `redact_text`).
2. `bauer.aws` — boto3 opcional atrás de `ReadOnlyAwsClient`, que expõe
   apenas operações listadas (STS `get_caller_identity`; EKS
   `list/describe_cluster`, `list/describe_nodegroup`; EC2
   `describe_instances`). Conta, região e cluster EKS em allowlist; a conta
   real da credencial é verificada por STS antes de qualquer outra chamada
   (conta diferente ⇒ recusa).
3. `bauer.kubernetes` — cliente kubernetes opcional para **contexto
   kubeconfig nomeado explicitamente** e namespaces em allowlist: pods,
   deployments e logs redigidos.
4. Sem contexto/perfil/região padrão implícito: tudo vem nomeado da config.
5. `exec`, `apply`, `delete`, deploy, scale e IAM não são classificados em
   lugar nenhum. `agno.docker`, `agno.aws_lambda`, `agno.aws_ses`
   `incompatible` com motivo.
6. Timeouts e limites de itens/linhas/caracteres configuráveis; erros do
   provedor viram `ToolError` só com o tipo (ARNs/request ids não vazam).

## Fora de escopo

- Qualquer ação mutante; instalar `boto3`/`kubernetes`; Docker via named
  pipe no Windows (socket unix apenas; documentado).

## Decisões (sem operador — opção mais segura)

- Docker pela API HTTP com `httpx` (já no lock) em vez do SDK `docker`:
  permite a garantia testável "só GET".
- AWS com perfil nomeado (credencial do operador, de menor privilégio) e
  verificação de conta por STS; sem credencial ⇒ chamada falha com
  `ToolError`, sem fallback para outra fonte.
- Capabilities sem dependência instalada aparecem `dependency_missing` com a
  dica de instalação; nunca simuladas como prontas.

## Critérios de aceite

- [x] Inventário só acessa hosts/contas/regiões/clusters/namespaces
  configurados.
- [x] Escrita/`exec` negadas antes da rede (transporte e cliente read-only;
  ações não classificadas).
- [x] Saída sensível redigida (env, mounts, tokens em logs).
- [x] Credencial de outra conta recusada.
- [x] Suíte e Ruff verdes.
