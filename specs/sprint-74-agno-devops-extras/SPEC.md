# SPEC — Sprint 74: DevOps e GitHub instaláveis (plano 067)

Depende da sprint 73 (mesmo padrão de extra) e dos guards das sprints 51
(`agno.github`) e 54 (`bauer.aws`, `bauer.kubernetes`).

## Problema

`bauer.aws`, `bauer.kubernetes` e `agno.github` ficavam
`dependency_missing`: os guards de leitura só tinham sido provados com
clientes falsos, nunca com `boto3`, `kubernetes` e PyGithub reais.

## Escopo

1. Extra opcional `agno-devops` (`boto3`, `kubernetes`, `PyGithub`),
   travado no `uv.lock`, mesmas regras da sprint 73 (≥14 dias, licença,
   install padrão idêntico).
2. Dicas de instalação apontam para o extra.
3. Provas com as bibliotecas reais e transporte falso
   (`tests/test_agno_devops_extras_installed.py`):
   - **AWS** (clientes boto3 reais + `botocore.stub.Stubber`): para cada
     serviço da allowlist, toda operação do *service model real* fora da
     allowlist (e `meta`, `get_paginator`, `generate_presigned_url`,
     `_make_api_call`…) é recusada pelo `ReadOnlyAwsClient` sem chegar ao
     botocore; a allowlist existe no modelo real e só tem
     `get_/list_/describe_`; STS de outra conta recusa antes de qualquer
     leitura; perfil nomeado ausente **não** cai para credencial do
     ambiente; mutações não são concedíveis.
   - **Kubernetes** (cliente real montado de kubeconfig temporário,
     `urllib3.PoolManager.request` falso): o contexto nomeado vence o
     `current-context`; só `GET` sob `/api/v1/namespaces/<ns permitido>/`;
     namespace/contexto fora da allowlist e nome de pod inválido são
     recusados antes de qualquer requisição; logs redigidos.
   - **GitHub** (PyGithub real, `requests` falso): só `GET` em
     `api.github.com`; repositório fora da allowlist é negado antes da rede;
     as 12 ações revisadas existem no SDK real; escritas não concedíveis.
4. CI (**pendente**, como na sprint 73): o step proposto no `install-check`
   já lista também este arquivo; push de workflow exige token com escopo
   `workflow`.

## Fora de escopo

- Novas ações; escrita em qualquer um dos três.
- `kubernetes` 37.x (ver decisões).

## Decisões

- **`kubernetes` 36.0.3, não 37.0.1.** A 37 (de 2026-10-09, idade ok) exige
  `certifi>=2026.7.22`, `urllib3>=2.8` e `aiohttp` — mudaria pacotes do
  install padrão. O resolvedor manteve as versões travadas e escolheu a
  36.0.3; nenhum pacote existente do lock mudou.
- **PyGithub é LGPL-3.0.** Aceito: dependência opcional, importada sem
  modificação, fora do install padrão; o Bauer continua MIT. Registrado para
  quem distribuir binários com o extra.
- Corte de idade aplicado como na sprint 73 (`--exclude-newer-package` no
  lock e regeneração sem flags; `uv lock --check` limpo). `boto3`/`botocore`
  1.43.119 (2026-10-26) em vez de 1.43.127 (2026-11-09).
- Nenhuma mudança de código nos guards: as provas passaram com as libs
  reais. Isso confirma que a costura de teste da sprint 54 não escondia
  diferença de comportamento.

## Critérios de aceite

- [x] Extra instalável, versões ≥14 dias, install padrão idêntico.
- [x] Com o extra, os três ficam `needs_configuration` (antes
  `dependency_missing`).
- [x] Guards provados com as bibliotecas reais e rede bloqueada.
- [x] Gates verdes.
