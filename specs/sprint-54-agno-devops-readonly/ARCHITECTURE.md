# ARCHITECTURE — Sprint 54

`bauer/core/runtime/agno_capabilities/families/devops.py`

```text
bauer.docker:<acao>(host, container…)
  → host configurado? → container casa allowlist? (inspect via GET)
  → httpx.Client(transport=ReadOnlyTransport(HTTPTransport(uds=socket)))
       ReadOnlyTransport.handle_request: método ≠ GET ⇒ ToolError (sem I/O)
  → campos selecionados / logs demux + redação

bauer.aws:<acao>(account, region, …)
  → conta e região em allowlist → _AWS_SESSION_FACTORY(profile, region)
  → ReadOnlyAwsClient(service): __getattr__ só para operações listadas
  → STS get_caller_identity == account_id (1x por conta/região/sessão)
  → operação de leitura → campos selecionados

bauer.kubernetes:<acao>(context, namespace, …)
  → contexto nomeado (kubeconfig_context explícito) + namespace allowlist
  → _KUBE_API_FACTORY → CoreV1Api/AppsV1Api → list/read
```

Costuras de teste: `_DOCKER_TRANSPORT`, `_AWS_SESSION_FACTORY`,
`_KUBE_API_FACTORY` (nenhum cliente real nos testes).

Todas as ações usam a operação `infra.read` do PolicyEngine (sem regra
padrão ⇒ allow; operador pode exigir aprovação com regra própria).
