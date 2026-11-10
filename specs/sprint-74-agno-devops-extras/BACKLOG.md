# BACKLOG — Sprint 74: DevOps e GitHub instaláveis

- [x] Versões/licenças/idade de boto3, kubernetes, PyGithub e transitivos.
- [x] Extra `agno-devops` + `uv.lock` (nenhum pacote existente alterado).
- [x] Dicas de instalação para o extra.
- [x] Provas AWS (Stubber), Kubernetes (pool falso), GitHub (requests falso).
- [x] Contrato do extra no lock (sempre roda).
- [ ] Step do CI (pendente, ver BACKLOG da sprint 73).
- [x] Gates: lock, sync, pytest (dev; dev+agno-devops+agno-research), ruff,
      mypy.

## Resultado medido (Agno 3.0.10, `--coverage`)

| Ambiente | ready | needs_configuration | dependency_missing | blocked_by_policy |
|---|---|---|---|---|
| antes (master) | 6 | 17 | 18 | 53 |
| só `agno-devops` | 6 | 22 | 14 | 52 |
| `agno-research` + `agno-devops` | 10 | 26 | 6 | 52 |

Restam `dependency_missing`: `agno.bravesearch` (sem versão compatível),
`agno.baidusearch`, `agno.google.maps`, `agno.openbb`, `agno.valyu`,
`agno.seltz` (fora do plano 067).

## Versões travadas

| Pacote | Versão | Upload | Licença |
|---|---|---|---|
| boto3 | 1.43.119 | 2026-10-26 | Apache-2.0 |
| botocore | 1.43.119 | 2026-10-26 | Apache-2.0 |
| s3transfer | 0.19.3 | 2026-10-19 | Apache-2.0 |
| jmespath | 1.1.0 | 2026-01-22 | MIT |
| kubernetes | 36.0.3 | 2026-07-13 | Apache-2.0 |
| durationpy | 0.11 | 2026-08-26 | MIT |
| websocket-client | 1.9.2 | 2026-08-31 | Apache-2.0 |
| requests-oauthlib | 2.0.0 | 2024-03-22 | ISC |
| oauthlib | 4.0.0 | 2026-09-28 | BSD-3-Clause |
| PyGithub | 2.10.0 | 2026-08-20 | LGPL-3.0 |
| pyjwt | 2.15.1 | 2026-09-28 | MIT |
| pynacl | 1.6.2 | 2026-01-01 | Apache-2.0 |

## Riscos

- `ReadOnlyAwsClient` guarda o cliente bruto em `_client`; só código Bauer
  o alcança (o modelo chama ações, não atributos).
- Perfil AWS existente mas sem credencial pode levar o botocore a tentar
  IMDS; o operador deve usar perfil com credencial explícita.
