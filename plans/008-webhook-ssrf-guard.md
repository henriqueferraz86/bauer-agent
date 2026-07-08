# Plan 008: Aplicar o guard SSRF (`url_safety`) na entrega de webhooks do gateway

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If
> anything in "STOP conditions" occurs, stop and report — do not improvise.
> When done, update this plan's status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 2c9d86f..HEAD -- bauer/gateway_adapters.py bauer/config_loader.py`
> If either file changed, compare "Current state" excerpts against the live
> code before proceeding; on mismatch, treat as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: MED (pode quebrar webhooks para hosts internos legítimos se o
  opt-out não for feito com cuidado — ver Step 2)
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `2c9d86f`, 2026-07-06

## Why this matters

O outbox do Bauer Gateway entrega mensagens (da tool `channel_send` e de
escalations) para URLs de webhook via `_post_json`, que chama
`urllib.request.urlopen()` diretamente **sem nenhuma validação de URL**. O
projeto JÁ tem um módulo `bauer/url_safety.py` com `check_url()` que bloqueia
SSRF (endpoints de metadata como 169.254.169.254, IPs privados, DNS rebinding)
— e ele é usado no `web_fetch`, mas NÃO nesta borda de saída. Isso é uma
inconsistência: um canal de webhook configurado (ou influenciado por um agente
sob prompt-injection) pode fazer o servidor bater em endpoints internos/cloud
metadata. O fix reusa o módulo existente. **Cuidado**: setups self-hosted
legitimamente entregam webhooks para `localhost`/rede interna (o próprio
usuário roda serviços locais), então a proteção precisa de um opt-out
explícito via config — bloquear por padrão, permitir interno quando o operador
declarar.

## Current state

- `bauer/gateway_adapters.py` — adaptadores de entrega outbound do outbox.
  A função de POST está nas linhas 118–134:

```python
# bauer/gateway_adapters.py:118-134
def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
) -> None:
    if not url.strip():
        raise ValueError("gateway target URL is required")
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = int(getattr(resp, "status", getattr(resp, "code", 200)) or 200)
        if status >= 400:
            raise RuntimeError(f"gateway returned HTTP {status}")
```

- `_post_json` é chamado por `_deliver_webhook` (linha ~62) e
  `_deliver_discord`/`_deliver_slack` (linhas ~78–87). O alvo SSRF principal é
  `_deliver_webhook` (URL totalmente arbitrária vinda de metadata do canal);
  discord/slack têm domínios fixos mas passam pela mesma função.

- O módulo de segurança a reusar — `bauer/url_safety.py`:

```python
# bauer/url_safety.py:45,114,143-147
class UrlSafetyError(ValueError): ...

@dataclass
class UrlSafetyConfig:
    ...  # tem o campo block_private_ips: bool (True por padrão)

def check_url(url: str, *, config: UrlSafetyConfig | None = None) -> None:
    """Raise UrlSafetyError if url is unsafe."""
```

  `check_url` levanta `UrlSafetyError` quando a URL é insegura; com um
  `UrlSafetyConfig(block_private_ips=False)` ele permite rede interna.

- A seção de config do gateway em `bauer/config_loader.py`:

```python
# bauer/config_loader.py (perto da linha 802)
class GatewaySection(_StrictSection):
    """Bauer Gateway — runtime unificado de canais + entrega do outbox."""
    outbox_drain_interval_s: int = Field(ge=1, le=3600, default=15)
```

### Convenções do repo a seguir
- Config: seções Pydantic v2 `_StrictSection` (extra proibido). Campos com
  default seguro. Veja `GatewaySection` acima como exemplar — adicione o novo
  campo nela.
- Import tardio para evitar ciclo: `bauer/gateway_adapters.py` NÃO deve importar
  `config_loader` no topo se isso criar ciclo; passe a config como parâmetro
  ou importe `url_safety` (que é leaf, sem ciclo).

## Commands you will need

| Purpose   | Command                                                        | Expected |
|-----------|----------------------------------------------------------------|----------|
| Testes    | `.venv/Scripts/python.exe -m pytest tests/ -k "gateway or adapter or url_safety" -q` | all pass |
| Import    | `.venv/Scripts/python.exe -c "import bauer.gateway_adapters"`  | exit 0   |
| Config    | `.venv/Scripts/python.exe -c "from bauer.config_loader import BauerConfig"` | exit 0 |

## Scope

**In scope**:
- `bauer/gateway_adapters.py`
- `bauer/config_loader.py` (adicionar 1 campo em `GatewaySection`)
- `tests/test_gateway_adapters_ssrf.py` (criar)

**Out of scope** (NÃO tocar):
- `bauer/url_safety.py` — reuse como está, não altere a lógica de SSRF.
- `bauer/postiz_client.py` — é outro cliente HTTP (redes sociais), fora deste
  escopo; NÃO adicione SSRF check nele (posts sociais vão para APIs públicas
  conhecidas e o self-hosted Postiz roda em localhost de propósito).
- A lógica de retry/outbox durável — só a validação de URL entra.

## Git workflow

- Branch: `advisor/008-webhook-ssrf-guard`
- Commit style: conventional commits. Ex.: `fix(security): valida URL de webhook contra SSRF (url_safety) no outbox`
- NÃO faça push nem PR sem instrução.

## Steps

### Step 1: Adicionar toggle de opt-out na config do gateway

Em `bauer/config_loader.py`, na `GatewaySection`, adicione um campo booleano
para permitir rede interna quando o operador declarar explicitamente:

```python
class GatewaySection(_StrictSection):
    """Bauer Gateway — runtime unificado de canais + entrega do outbox."""
    outbox_drain_interval_s: int = Field(ge=1, le=3600, default=15)
    webhook_allow_internal: bool = False  # True = permite webhook p/ localhost/rede interna (self-hosted)
```

**Verify**: `.venv/Scripts/python.exe -c "from bauer.config_loader import BauerConfig; print(BauerConfig.model_fields['gateway'])"` → exit 0.

### Step 2: Validar a URL em `_post_json` antes do `urlopen`

Em `bauer/gateway_adapters.py`, adicione a validação SSRF no início de
`_post_json`, controlada por um parâmetro `allow_internal` (default `False`)
que os callers repassam a partir da config. A `_post_json` recebe o novo
kwarg; os callers (`_deliver_webhook` etc.) o repassam.

Forma-alvo do início de `_post_json`:

```python
def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
    allow_internal: bool = False,
) -> None:
    if not url.strip():
        raise ValueError("gateway target URL is required")
    # SSRF guard: bloqueia metadata endpoints / IPs privados, salvo opt-in.
    from .url_safety import check_url, UrlSafetyConfig, UrlSafetyError
    try:
        cfg = UrlSafetyConfig(block_private_ips=not allow_internal)
        check_url(url, config=cfg)
    except UrlSafetyError as exc:
        raise RuntimeError(f"webhook URL bloqueada por seguranca (SSRF): {exc}") from exc
    ...  # resto igual
```

**Importante**: confirme a assinatura real de `UrlSafetyConfig` — o campo pode
ter nome diferente de `block_private_ips`. Rode
`grep -n "block_private_ips\|class UrlSafetyConfig" bauer/url_safety.py` e use
o nome exato. Se o campo não existir, use `check_url(url)` sem config (bloqueia
tudo interno) e, para o opt-in, envolva a chamada em
`if not allow_internal:` — ou seja, só valida quando interno NÃO é permitido.
Prefira essa segunda forma se a config de opt-out não for óbvia:

```python
    if not allow_internal:
        from .url_safety import check_url, UrlSafetyError
        try:
            check_url(url)
        except UrlSafetyError as exc:
            raise RuntimeError(f"webhook URL bloqueada por seguranca (SSRF): {exc}") from exc
```

### Step 3: Repassar `allow_internal` dos callers

Encontre os callers de `_post_json` (`grep -n "_post_json(" bauer/gateway_adapters.py`)
e, onde a config do gateway estiver acessível, passe
`allow_internal=<gateway.webhook_allow_internal>`. Se a config NÃO estiver
disponível dentro de `_deliver_*` (verifique como `GatewayDeliveryAdapter`
recebe config), então: adicione um atributo `self.webhook_allow_internal` ao
adapter, populado a partir da config no ponto de construção, e use-o. Se isso
exigir tocar o construtor e o ponto de construção estiver fora do in-scope,
**pare e reporte** (STOP condition) — não expanda o escopo silenciosamente.

**Verify**: `.venv/Scripts/python.exe -c "import bauer.gateway_adapters"` → exit 0.

### Step 4: Testes

Crie `tests/test_gateway_adapters_ssrf.py` (ver Test plan).

**Verify**: `.venv/Scripts/python.exe -m pytest tests/test_gateway_adapters_ssrf.py -q` → all pass.

## Test plan

- Novo arquivo `tests/test_gateway_adapters_ssrf.py`. Modele por
  `tests/test_gateway_adapters*.py` se existir (`ls tests/ | grep gateway`);
  senão por qualquer teste que chame funções de `gateway_adapters`.
- Casos:
  1. **Bloqueio por padrão**: `_post_json("http://169.254.169.254/latest/meta-data/", {...})`
     levanta `RuntimeError` contendo "SSRF" (ou o texto do fix). Não precisa de
     rede — `check_url` bloqueia por hostname/IP antes de qualquer socket.
  2. **Bloqueio de IP privado**: `_post_json("http://10.0.0.1/x", {...})`
     levanta `RuntimeError`.
  3. **Opt-in permite interno**: `_post_json("http://127.0.0.1:9/x", {...},
     allow_internal=True)` NÃO levanta `UrlSafetyError`/SSRF (pode falhar por
     conexão recusada — isso é OK; o teste só verifica que NÃO foi bloqueado
     pelo guard; use `pytest.raises` esperando um erro de conexão OU mocke
     `urllib.request.urlopen`).
  4. **URL pública passa**: com `urllib.request.urlopen` mockado, uma URL
     `https://hooks.example.com/x` chega ao `urlopen` (não é bloqueada).
- Verificação: `.venv/Scripts/python.exe -m pytest tests/test_gateway_adapters_ssrf.py -q`
  → all pass (≥4 testes).

## Done criteria

TODAS devem valer:

- [ ] `.venv/Scripts/python.exe -c "import bauer.gateway_adapters"` sai 0
- [ ] `.venv/Scripts/python.exe -m pytest tests/test_gateway_adapters_ssrf.py -q` passa
- [ ] `.venv/Scripts/python.exe -m pytest tests/ -k "gateway or url_safety" -q` continua passando
- [ ] `grep -n "check_url" bauer/gateway_adapters.py` retorna ≥1 ocorrência
- [ ] `grep -n "webhook_allow_internal" bauer/config_loader.py` retorna 1 ocorrência
- [ ] Nenhum arquivo fora do in-scope modificado (`git status`)
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

Pare e reporte se:

- O excerpt de `_post_json` não bater com o código atual (drift).
- `UrlSafetyConfig`/`check_url` tiverem assinatura diferente da documentada e
  você não conseguir determinar o nome do campo de opt-out — nesse caso use a
  forma `if not allow_internal: check_url(url)` do Step 2 e reporte a diferença.
- Repassar `allow_internal` exigir modificar o construtor de
  `GatewayDeliveryAdapter` e o ponto de construção estiver fora do in-scope.
- Algum teste existente de gateway quebrar por causa da validação (indica um
  webhook interno legítimo nos fixtures — reporte em vez de afrouxar o guard).

## Maintenance notes

- Se no futuro os canais de webhook passarem a aceitar URL vinda de input de
  usuário (não só config/CLI), este guard vira crítico (hoje é MED porque o
  alvo é config-controlled).
- O reviewer deve conferir que discord/slack (domínios fixos) continuam
  passando — eles usam `discord.com`/`slack.com`, públicos, não afetados.
- Follow-up deferido: rate-limit / circuit-breaker por alvo de webhook lento
  (achado P7 separado, não incluído aqui).
