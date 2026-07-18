# Plan 026: Aviso claro quando um modelo cai no bridge por não estar no registry (+ detecção opcional via Ollama)

> **Executor instructions**: Follow step by step; run every verification and
> confirm before proceeding. On any STOP condition, stop and report. Update the
> status row in `plans/README.md` when done.
>
> **Drift check (run first)**: `git diff --stat ced7dc2..HEAD -- bauer/preflight.py`
> Mismatch vs "Current state" → STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug / dx
- **Planned at**: commit `ced7dc2`, 2026-07-18

## Why this matters

O tool mode (`native` vs `bridge`) é decidido só pelo `models.yaml`: se `info.supports_tools is True` → `native`, senão `bridge`. O problema: quando o modelo **não está no `models.yaml`** (`info is None`), ele cai em `bridge` **silenciosamente** — foi exatamente o que degradou um deploy real (o modelo emitia tool calls como texto e ninguém sabia por quê, porque o modelo nem estava no registry). Este plano torna essa degradação **visível** (aviso acionável no doctor/findings) e, num segundo passo opcional, detecta a capability real consultando o Ollama, deixando de depender só do YAML.

## Current state

Excerto (`bauer/preflight.py:226-234`):

```python
    # --- tool mode ---------------------------------------------------------------
    if is_cloud:
        tool_mode = "bridge"
        findings.append("Tool mode: bridge (provider cloud)")
    elif info is not None and info.supports_tools is True:
        tool_mode = "native"
    else:
        tool_mode = "bridge"  # padrão conservador; Fase 4 implementa de fato
        findings.append(f"Tool mode planejado: {tool_mode}")
```

`info` vem do `ModelRegistry` (carregado do `models.yaml`); é `None` quando o modelo ativo não tem entrada no registry. `findings` é uma lista de strings legíveis exibidas pelo `bauer doctor`. O `configured_model`/nome do modelo ativo está disponível na função (procure a variável do nome do modelo no corpo de `run_doctor`/preflight, ex.: `model_name` ou `configured_model`).

O Ollama expõe `POST /api/show` com `{"name": "<model>"}`; a resposta inclui um campo `capabilities` (lista) que contém `"tools"` para modelos com function calling nativo. O host do Ollama está em `config.ollama.host`.

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Sintaxe | `python -c "import ast; ast.parse(open('bauer/preflight.py',encoding='utf-8').read())"` | sem erro |
| Testes  | `python -m pytest tests/test_preflight_toolmode.py -q` | passam |
| Lint    | `python -m ruff check bauer/preflight.py` | `All checks passed!` |

## Scope

**In scope**: `bauer/preflight.py` (o bloco de tool mode), `tests/test_preflight_toolmode.py` (criar).

**Out of scope**: `models.yaml` (o registry em si — não editar aqui); o consumo de `tool_mode` a jusante (agent/serve).

## Git workflow

- Branch: `advisor/026-tool-capability-detection`
- Conventional commit (ex.: `fix(preflight): avisa quando modelo cai no bridge por não estar no registry`)

## Steps

### Step 1: Aviso acionável quando `info is None`

Separe o ramo "modelo desconhecido" do ramo "modelo conhecido mas sem tools", com mensagens distintas e acionáveis. Alvo:

```python
    elif info is not None and info.supports_tools is True:
        tool_mode = "native"
    elif info is None:
        tool_mode = "bridge"
        findings.append(
            f"Tool mode: bridge — o modelo '{model_name}' não está no models.yaml. "
            "Se ele suporta function calling nativo (ex.: qwen2.5/qwen3), adicione a "
            "entrada com supports_tools: true para tool calling confiável."
        )
    else:
        tool_mode = "bridge"
        findings.append(
            f"Tool mode: bridge — models.yaml marca '{model_name}' como supports_tools != true."
        )
```

Use o nome real da variável do modelo (confirme no corpo da função — provavelmente `model_name` ou `state`/`configured_model`).

**Verify**: `python -c "import ast; ast.parse(open('bauer/preflight.py',encoding='utf-8').read())"` → sem erro

### Step 2 (opcional, mas recomendado): detecção via Ollama `/api/show`

Quando o provider é Ollama e `info` não confirma tools, tente detectar a capability real antes de decidir bridge. Adicione um helper best-effort:

```python
def _ollama_supports_tools(host: str, model: str) -> "bool | None":
    """True/False se /api/show declarar capabilities; None se indisponível."""
    try:
        import httpx
        r = httpx.post(f"{host.rstrip('/')}/api/show", json={"name": model}, timeout=3.0)
        caps = r.json().get("capabilities") or []
        return "tools" in caps
    except Exception:
        return None
```

No bloco de tool mode, quando `info is None` e o provider é Ollama, consulte `_ollama_supports_tools`; se retornar `True`, use `native` e emita um finding "detectado via Ollama /api/show". Mantenha o aviso do Step 1 quando a detecção for inconclusiva (`None`) ou `False`.

**Verify**: `python -c "from bauer.preflight import _ollama_supports_tools; print(_ollama_supports_tools('http://localhost:1','x'))"` → imprime `None` (host inacessível), sem exceção

### Step 3: Testes

Crie `tests/test_preflight_toolmode.py`. Como `run_doctor`/preflight tem muitas dependências, teste preferencialmente as **peças isoláveis**:
- `_ollama_supports_tools` com `httpx.post` mockado retornando `{"capabilities": ["tools"]}` → `True`; sem `capabilities` → `False`; exceção → `None`.
- Se for viável chamar o bloco de decisão isolado (extraia-o para uma função pura `_decide_tool_mode(is_cloud, info, ...)` se ajudar a testar — mudança pequena e vale a pena), teste: `info=None` → bridge + finding com "não está no models.yaml".

Padrão: `tests/test_server_warmup.py` (mock de `httpx.post`, `threading.Event`).

**Verify**: `python -m pytest tests/test_preflight_toolmode.py -q` → passam

## Test plan

- Testes em `tests/test_preflight_toolmode.py` (acima), cobrindo: detecção via Ollama (3 casos) e o aviso de modelo ausente.
- Verificação: `python -m pytest tests/test_preflight_toolmode.py -q`.

## Done criteria

- [ ] `info is None` gera finding distinto e acionável (menciona models.yaml + supports_tools)
- [ ] (se Step 2) `_ollama_supports_tools` existe e é best-effort (None em falha)
- [ ] `python -m pytest tests/test_preflight_toolmode.py -q` passa
- [ ] `python -m ruff check bauer/preflight.py` → `All checks passed!`
- [ ] `git status` sem arquivos fora de escopo
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

- A variável do nome do modelo não existir no escopo do bloco (confirme antes de usar `model_name`).
- O bloco de tool mode divergir do excerto (drift).
- `/api/show` não retornar `capabilities` na versão do Ollama alvo — nesse caso, mantenha só o Step 1 e anote no plano.

## Maintenance notes

- Se o Step 2 for adotado, o `models.yaml` deixa de ser a única fonte de verdade — documente que a detecção via Ollama tem precedência quando disponível.
- Revisor: garanta que a detecção via HTTP é best-effort e nunca trava o boot (timeout curto, try/except).
