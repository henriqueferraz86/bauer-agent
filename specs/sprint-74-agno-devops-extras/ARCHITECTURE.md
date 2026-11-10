# ARCHITECTURE — Sprint 74

| Peça | Arquivo |
|---|---|
| Extra `agno-devops` | `pyproject.toml`, `uv.lock` |
| Dicas de instalação | `families/devops.py`, `families/github.py` |
| Contrato do extra (sempre roda) | `tests/test_agno_devops_extras.py` |
| Guards com libs reais | `tests/test_agno_devops_extras_installed.py` |
| CI (pendente) | `.github/workflows/ci.yml` (`install-check`) |

```text
AWS:  _AWS_SESSION_FACTORY → cliente boto3 REAL (+Stubber)
        → ReadOnlyAwsClient.__getattr__ (allowlist) → STS == account_id
        → operação de leitura → Stubber (nenhuma resposta extra pendente)

K8s:  _default_kube_api REAL (kubeconfig_path + kubeconfig_context)
        → CoreV1Api/AppsV1Api → RESTClientObject → urllib3.PoolManager.request (falso)

GitHub: _sdk_factory → GithubTools REAL → _repo_guard → PyGithub Requester
        → requests.HTTPAdapter.send (falso)
```

Em todos os testes `socket.connect`/`create_connection` falham, e o boto3
lê arquivos AWS temporários com IMDS desligado.
