# Plan 006: Agentes Distribuídos MVP — dispatch remoto HTTP entre instâncias bauer serve

> **Executor instructions**: Siga este plano passo a passo. Execute cada
> comando de verificação e confirme o resultado esperado antes de avançar.
> Se qualquer condição STOP ocorrer, pare e reporte — não improvise.
> Ao concluir, atualize a linha de status deste plano em `plans/README.md`.
>
> **Drift check (execute primeiro)**:
> `git diff --stat 1f6292e..HEAD -- bauer/agent_registry.py bauer/orchestrator.py bauer/tools/execution.py`
> Se algum arquivo mudou desde que o plano foi escrito, compare os trechos
> da seção "Estado atual" com o código real antes de prosseguir. Em caso de
> divergência, trate como STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: none (005 já está DONE)
- **Category**: direction / feature
- **Planned at**: commit `1f6292e`, 2026-06-29

## Por que isso importa

Hoje cada instância `bauer serve` é um silo: o orquestrador de DAG executa
todos os passos no mesmo processo, e `delegate_task` lança subprocessos locais.
Dois agentes em máquinas diferentes não conseguem colaborar.

O MVP adiciona um campo `url` ao `AgentDef`. Quando um passo do DAG designa
um agente com `url`, o orquestrador POST para `{url}/chat` em vez de executar
localmente. O mesmo mecanismo está disponível para qualquer agente via a tool
`delegate_task` (novo argumento `agent_name`). O endpoint `/chat` já existe e
está funcionando — nenhuma mudança no servidor é necessária.

Resultado: orquestrador na máquina A pode delegar passos para workers em B e C,
receber as respostas e sintetizar um resultado final.

## Estado atual (leia antes de editar)

### `bauer/agent_registry.py`

**`AgentDef` — dataclass (linhas 612–625 do estado em `1f6292e`):**
```python
@dataclass
class AgentDef:
    name: str
    description: str
    system: str
    tools: list[str] = field(default_factory=lambda: list(DEFAULT_TOOLS))
    capabilities: list[str] = field(default_factory=list)
    lane: str = ""
    max_concurrent: int = 1
    priority_weight: int = 1
    model: str = ""          # vazio = usa config.yaml
    provider: str = ""       # vazio = usa config.yaml
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
```
→ Não existe campo `url` nem `api_key`.

**`AgentDef.to_dict()` (linhas 627–647):** serializa apenas os campos existentes;
campos com valor default são omitidos do YAML para não poluir.

**`AgentDef.from_dict()` (linha 649):** lê do dict; usa `.get()` com default para
campos opcionais.

### `bauer/orchestrator.py`

**`execute_step()` (linhas 356–416):**
```python
def execute_step(self, step: dict, previous_results: list[StepResult]) -> StepResult:
    goal = step.get("goal", "executar tarefa")
    needs_tools = step.get("tools", True)
    agent_name = step.get("agent", "")
    step_id = step.get("id", 0)

    agent_system = self._load_agent_system(agent_name)
    model_name, _route = self.model_router.select_model(goal)
    ...
    # sem qualquer checagem de URL remota — executa sempre local
```

**`_load_agent_system()` (linhas 344–354):** carrega system prompt do `AgentRegistry`.
Podemos usar o mesmo padrão para carregar `url` e `api_key`.

**`OrchestratorConfig` (linhas 134–142):**
```python
@dataclass
class OrchestratorConfig:
    planner_model: str = "qwen3:0.6b"
    synthesizer_model: str = "phi4-mini"
    max_steps: int = MAX_STEPS
    parallel_steps: bool = False
    max_retries: int = 2
    retry_delay_s: float = 3.0
    agents_file: str = "agents.yaml"
```

### `bauer/tools/execution.py`

**`_delegate_task()` (linhas 364–439):** aceita `task`, `context`, `timeout`.
Tenta usar `self._llm_client` diretamente, depois subprocess `bauer agent run-one`.
**Não aceita `agent_name` nem faz dispatch remoto.**

### Endpoint HTTP disponível em toda instância `bauer serve`

```
POST /chat
Headers: X-API-Key: <key>   (opcional se serve.api_key estiver vazio)
Body:    {"message": "<tarefa>", "session_id": "<str opcional>"}
Resposta 200: {"response": "<texto>", "session_id": "...", "model": "...", "tool_calls": [...]}
```

### Convenções do repositório

- Dataclasses com `@dataclass` e `field(default_factory=...)` para mutáveis.
- Erros de tool: `raise ToolError("mensagem")` (de `.base`).
- Testes: `unittest.mock.MagicMock` + `patch("httpx.post")`.
- Padrão de fixture de orquestrador: ver `tests/test_orchestrator.py::_make_orch()`.
- Padrão de fixture de registry: ver `tests/test_agent_registry.py::_sample_agent()`.

## Comandos necessários

| Propósito | Comando | Esperado no sucesso |
|-----------|---------|---------------------|
| Testes | `python -m pytest tests/ -q --tb=short` | exit 0 |
| Testes filtrados | `python -m pytest tests/test_orchestrator.py tests/test_agent_registry.py tests/test_distributed_agents.py -v` | todos passam |

## Escopo

**Em escopo (únicos arquivos a modificar):**
- `bauer/agent_registry.py` — adicionar `url` e `api_key` ao `AgentDef`
- `bauer/orchestrator.py` — adicionar `_remote_dispatch()` e modificar `execute_step()`
- `bauer/tools/execution.py` — modificar `_delegate_task()` para aceitar `agent_name`
- `tests/test_distributed_agents.py` — criar (novo arquivo de testes)

**Fora de escopo (NÃO tocar):**
- `bauer/server.py` — o endpoint `/chat` já funciona corretamente, sem mudanças.
- `bauer/url_safety.py` — o dispatch remoto vai direto via `httpx`, sem passar pelo
  SSRF guard (que bloqueia localhost). A URL vem do registry configurado pelo
  administrador, não do LLM — é input confiável.
- `bauer/tool_router.py` — só o mixin `execution.py` muda; o registro da tool
  não precisa ser alterado.
- `bauer/commands/agent_cmd.py` — adição de `--url` ao CLI é melhoria futura;
  este plano foca no runtime. Não tocar.

## Workflow git

- Branch: `feature/006-distributed-agents`
- Commits: convencional — `feat(distributed): ...`
- **NÃO** fazer push nem PR até o operador solicitar.

---

## Passos

### Passo 1 — Adicionar `url` e `api_key` ao `AgentDef`

**Arquivo**: `bauer/agent_registry.py`

**1a. No `@dataclass AgentDef`, adicione dois campos após `provider`:**

```python
    model: str = ""          # vazio = usa config.yaml
    provider: str = ""       # vazio = usa config.yaml
    url: str = ""            # endpoint remoto: http://host:port (vazio = local)
    api_key: str = ""        # X-API-Key para o servidor remoto (vazio = sem auth)
    created_at: str = field(...)
```

**1b. Em `to_dict()`, serializar `url` e `api_key` somente quando não-vazios
(mesmo padrão de `model` e `provider` já no código):**

Localize o bloco de condicionais em `to_dict()` (linhas ~643-647):
```python
        if self.model:
            d["model"] = self.model
        if self.provider:
            d["provider"] = self.provider
        return d
```

Substitua por:
```python
        if self.model:
            d["model"] = self.model
        if self.provider:
            d["provider"] = self.provider
        if self.url:
            d["url"] = self.url
        if self.api_key:
            d["api_key"] = self.api_key
        return d
```

**1c. Em `from_dict()`, ler os dois campos novos:**

Localize a chamada `cls(...)` dentro de `from_dict()` (linha ~666). O construtor
usa `**kwargs` implicitamente por ser dataclass — apenas certifique-se de que
`from_dict` lê `url` e `api_key` do dict e os passa:

```python
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            system=system,
            tools=tools,
            capabilities=capabilities,
            lane=str(d.get("lane", "") or ""),
            max_concurrent=max_concurrent,
            priority_weight=priority_weight,
            model=str(d.get("model", "") or ""),
            provider=str(d.get("provider", "") or ""),
            url=str(d.get("url", "") or ""),           # NOVO
            api_key=str(d.get("api_key", "") or ""),   # NOVO
            created_at=d.get("created_at", ""),
        )
```

**Verificar**:
```bash
python -c "
from bauer.agent_registry import AgentDef
a = AgentDef(name='worker', description='d', system='s', url='http://localhost:8001', api_key='sk-test')
d = a.to_dict()
assert d['url'] == 'http://localhost:8001'
assert d['api_key'] == 'sk-test'
b = AgentDef.from_dict(d)
assert b.url == 'http://localhost:8001'
assert b.api_key == 'sk-test'
# Agente local sem url não deve ter chave no dict
c = AgentDef(name='local', description='d', system='s')
assert 'url' not in c.to_dict()
print('OK')
"
```
→ deve imprimir `OK`.

---

### Passo 2 — Adicionar `_remote_dispatch()` ao `AgentOrchestrator`

**Arquivo**: `bauer/orchestrator.py`

Adicione o método após `_load_agent_system()` (linha ~354), antes de `execute_step()`.

**Copie exatamente:**
```python
    def _remote_dispatch(
        self,
        url: str,
        api_key: str,
        task: str,
        timeout: float = 120.0,
    ) -> str:
        """Dispatcha tarefa para uma instância remota bauer serve via POST /chat.

        Args:
            url:     Base URL do servidor remoto, ex: 'http://192.168.1.5:8000'.
            api_key: X-API-Key do servidor remoto ('' = sem autenticação).
            task:    Texto completo da tarefa a executar.
            timeout: Timeout HTTP em segundos.

        Returns:
            Texto da resposta do agente remoto.

        Raises:
            RuntimeError: Se o servidor retornar erro HTTP ou timeout.
        """
        import httpx  # importação lazy — não penaliza quem não usa dispatch remoto

        endpoint = url.rstrip("/") + "/chat"
        headers: dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = api_key

        try:
            resp = httpx.post(
                endpoint,
                json={"message": task},
                headers=headers,
                timeout=httpx.Timeout(connect=10.0, read=timeout, write=10.0, pool=5.0),
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            raise RuntimeError(
                f"Timeout ({timeout}s) ao aguardar resposta de {endpoint}."
            )
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Agente remoto {endpoint} retornou HTTP {exc.response.status_code}."
            ) from exc
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Não foi possível conectar a {endpoint}. "
                "Verifique se o bauer serve está rodando e acessível."
            ) from exc

        data = resp.json()
        return data.get("response", "")
```

**Verificar**: o arquivo deve importar sem erros:
```bash
python -c "from bauer.orchestrator import AgentOrchestrator; print('OK')"
```
→ `OK`

---

### Passo 3 — Modificar `execute_step()` para dispatch remoto

**Arquivo**: `bauer/orchestrator.py`

No método `execute_step()`, adicione **antes** do bloco `if needs_tools:` uma
verificação de URL remota. O local exato é após as linhas que montam
`context_text` e `stream_prefix` (~linha 383).

**Estado atual do trecho (confirme que está assim antes de editar):**
```python
        context_text = "\n".join(context_lines)

        stream_prefix = f"[passo {step_id}]"

        # Execução usa self.client (provider principal: Groq, OpenAI, Ollama…).
        if needs_tools:
```

**Substitua por:**
```python
        context_text = "\n".join(context_lines)

        stream_prefix = f"[passo {step_id}]"

        # ── Dispatch remoto ────────────────────────────────────────────────────
        # Se o agente designado para este passo tiver `url` no registry, envia
        # a tarefa via HTTP para aquela instância bauer serve em vez de executar
        # localmente. Não passa pelo SSRF guard — URL vem do registry confiável.
        if agent_name:
            try:
                from .agent_registry import AgentRegistry
                _reg = AgentRegistry(self.config.agents_file)
                _ag = _reg.get(agent_name)
                if _ag and _ag.url:
                    if self.console:
                        self.console.print(
                            f"[dim][passo {step_id}] → dispatch remoto: {_ag.url}[/dim]"
                        )
                    full_task = (goal + "\n\n" + context_text).strip()
                    _timeout = getattr(self.config, "remote_timeout_s", 120.0)
                    response = self._remote_dispatch(
                        url=_ag.url,
                        api_key=_ag.api_key,
                        task=full_task,
                        timeout=_timeout,
                    )
                    return StepResult(
                        id=step_id,
                        goal=goal,
                        model_used=f"remote:{_ag.url}",
                        response=response,
                        tool_log=[],
                    )
            except Exception as _dispatch_err:
                if self.console:
                    self.console.print(
                        f"[yellow][passo {step_id}] Dispatch remoto falhou "
                        f"({_dispatch_err}); executando localmente.[/yellow]"
                    )
                # fallthrough — executa local normalmente

        # Execução usa self.client (provider principal: Groq, OpenAI, Ollama…).
        if needs_tools:
```

**Nota**: o bloco `except` faz fallthrough para execução local em caso de falha
remota. Isso garante que o DAG não trave se o worker estiver temporariamente
offline. Comportamento correto para MVP.

**Verificar**:
```bash
python -c "from bauer.orchestrator import AgentOrchestrator; print('OK')"
```
→ `OK`

---

### Passo 4 — Modificar `_delegate_task()` para aceitar `agent_name`

**Arquivo**: `bauer/tools/execution.py`

O objetivo é: quando o LLM chamar `delegate_task` com `{"agent_name": "worker-python", "task": "..."}`,
a tool consulta o registry, encontra a URL do agente e despacha via HTTP.

**4a. Atualize o docstring e a extração de args** no início de `_delegate_task()`:

Localize:
```python
    def _delegate_task(self, args: dict) -> str:
        """Delega subtarefa a sub-agente via subprocess bauer CLI.
        ...
        """
        import subprocess
        import sys

        task = args.get("task", "").strip()
        if not task:
            raise ToolError("delegate_task requer 'task'.")

        context = args.get("context", "").strip()
        timeout = int(args.get("timeout", 120))
        timeout = max(10, min(timeout, 600))
```

Substitua pelo bloco abaixo (até `full_task = ...`):
```python
    def _delegate_task(self, args: dict) -> str:
        """Delega subtarefa a sub-agente local ou remoto.

        Quando `agent_name` é fornecido e o agente tem `url` no registry,
        despacha via HTTP POST /chat para aquela instância bauer serve.
        Caso contrário, usa LLM client direto ou subprocess local.
        """
        import subprocess
        import sys

        task = args.get("task", "").strip()
        if not task:
            raise ToolError("delegate_task requer 'task'.")

        context = args.get("context", "").strip()
        agent_name = str(args.get("agent_name", "") or "").strip()
        timeout = int(args.get("timeout", 120))
        timeout = max(10, min(timeout, 600))

        full_task = f"{context}\n\n{task}".strip() if context else task
        full_task = full_task.replace("\x00", "").strip()
        if len(full_task) > 4096:
            full_task = full_task[:4096]

        # ── Dispatch remoto via agent registry ────────────────────────────────
        if agent_name:
            try:
                from ..agent_registry import AgentRegistry
                import httpx as _httpx

                _home = getattr(self, "_bauer_home", None)
                _agents_file = str(_home / "agents.yaml") if _home else "agents.yaml"
                _reg = AgentRegistry(_agents_file)
                _ag = _reg.get(agent_name)
                if _ag and _ag.url:
                    endpoint = _ag.url.rstrip("/") + "/chat"
                    _headers: dict[str, str] = {}
                    if _ag.api_key:
                        _headers["X-API-Key"] = _ag.api_key
                    try:
                        resp = _httpx.post(
                            endpoint,
                            json={"message": full_task},
                            headers=_headers,
                            timeout=_httpx.Timeout(connect=10.0, read=float(timeout),
                                                   write=10.0, pool=5.0),
                        )
                        resp.raise_for_status()
                        return f"[agente remoto: {agent_name}]\n{resp.json().get('response', '')}"
                    except _httpx.TimeoutException:
                        raise ToolError(
                            f"delegate_task: timeout ({timeout}s) aguardando {agent_name} "
                            f"em {endpoint}."
                        )
                    except _httpx.HTTPStatusError as exc:
                        raise ToolError(
                            f"delegate_task: agente remoto {agent_name} retornou "
                            f"HTTP {exc.response.status_code}."
                        ) from exc
                    except _httpx.ConnectError:
                        raise ToolError(
                            f"delegate_task: não foi possível conectar a {agent_name} "
                            f"em {endpoint}. Verifique se o bauer serve está rodando."
                        )
            except ToolError:
                raise
            except Exception:
                pass  # registry não disponível — continua com delegate local
```

**Importante**: o bloco acima é inserido **antes** do código existente
`if self._llm_client is not None:`. O resto do método permanece igual.

**Verificar**:
```bash
python -c "from bauer.tools.execution import ExecutionToolsMixin; print('OK')"
```
→ `OK`

---

### Passo 5 — Atualizar descrição da tool no `tool_router.py`

**Arquivo**: `bauer/tool_router.py`

Localize a definição de `delegate_task` (linhas ~607-618). Atualize o `description`
e o dict de `args` para documentar o novo campo `agent_name`:

```python
        self._tools["delegate_task"] = {
            "fn": self._delegate_task,
            "description": (
                "Delega uma subtarefa a um sub-agente e retorna o resultado. "
                "Se 'agent_name' for fornecido e o agente tiver URL configurada no "
                "registry, dispatcha via HTTP para aquela instância bauer serve remota. "
                "Sem agent_name, executa localmente no mesmo processo."
            ),
            "args": {
                "task": "str — descricao completa da tarefa a delegar (obrigatorio)",
                "context": "str — contexto adicional para o sub-agente (opcional)",
                "agent_name": "str — nome do agente no registry (opcional; sem este campo, executa local)",
                "timeout": "int — timeout em segundos (default: 120, max: 600)",
            },
        }
```

**Verificar**:
```bash
python -c "
from unittest.mock import MagicMock
from bauer.tool_router import ToolRouter
r = ToolRouter(workspace='/tmp', llm_client=None)
info = r.tool_info('delegate_task')
assert 'agent_name' in info['args'], info
print('OK')
"
```
→ `OK`

---

### Passo 6 — Escrever os testes

**Arquivo a criar**: `tests/test_distributed_agents.py`

Use como padrão estrutural `tests/test_orchestrator.py` (fixtures `_make_orch`,
mock de `client.chat_stream`) e `tests/test_agent_registry.py` (fixture
`_make_registry`, `_sample_agent`).

```python
"""Testes para dispatch remoto entre instâncias bauer serve."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.agent_registry import AgentDef, AgentRegistry
from bauer.orchestrator import AgentOrchestrator, OrchestratorConfig, StepResult


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(path=str(tmp_path / "agents.yaml"))


def _make_orch(tmp_path: Path, agents_file: str = "agents.yaml") -> AgentOrchestrator:
    client = MagicMock()
    client.chat_stream.return_value = iter(["resposta local"])
    tool_router = MagicMock()
    model_router = MagicMock()
    model_router.select_model.return_value = ("phi4-mini", MagicMock())
    cfg = OrchestratorConfig(agents_file=agents_file)
    orch = AgentOrchestrator(client, tool_router, model_router, cfg)

    def _patched(task: str) -> Path:
        import hashlib
        h = hashlib.md5(task.encode()).hexdigest()[:10]
        return tmp_path / h

    orch._progress_path = _patched  # type: ignore[method-assign]
    return orch


# ─── AgentDef: novos campos url / api_key ────────────────────────────────────


def test_agentdef_url_round_trip(tmp_path: Path):
    """url e api_key devem serializar para YAML e deserializar corretamente."""
    reg = _make_registry(tmp_path)
    ag = AgentDef(
        name="worker-py",
        description="Worker Python remoto",
        system="Você é um especialista Python.",
        url="http://192.168.1.10:8000",
        api_key="secret-key-abc",
    )
    reg.save(ag)

    loaded = reg.get("worker-py")
    assert loaded is not None
    assert loaded.url == "http://192.168.1.10:8000"
    assert loaded.api_key == "secret-key-abc"


def test_agentdef_without_url_omits_key(tmp_path: Path):
    """Agente local (sem url) não deve ter 'url' no dict serializado."""
    ag = AgentDef(name="local", description="d", system="s")
    d = ag.to_dict()
    assert "url" not in d
    assert "api_key" not in d


def test_agentdef_from_dict_handles_missing_url():
    """from_dict sem url/api_key deve retornar strings vazias."""
    ag = AgentDef.from_dict({
        "name": "agente",
        "description": "d",
        "system": "s",
    })
    assert ag.url == ""
    assert ag.api_key == ""


# ─── AgentOrchestrator._remote_dispatch ──────────────────────────────────────


def test_remote_dispatch_posts_to_chat_endpoint(tmp_path: Path):
    """_remote_dispatch deve POST para {url}/chat e retornar 'response'."""
    orch = _make_orch(tmp_path)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "resultado remoto", "session_id": "s1"}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = orch._remote_dispatch(
            url="http://192.168.1.10:8000",
            api_key="key123",
            task="analise o arquivo",
            timeout=30.0,
        )

    assert result == "resultado remoto"
    call_args = mock_post.call_args
    assert call_args[0][0] == "http://192.168.1.10:8000/chat"
    assert call_args[1]["json"] == {"message": "analise o arquivo"}
    assert call_args[1]["headers"]["X-API-Key"] == "key123"


def test_remote_dispatch_no_api_key_omits_header(tmp_path: Path):
    """Sem api_key, o header X-API-Key não deve ser enviado."""
    orch = _make_orch(tmp_path)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "ok"}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        orch._remote_dispatch(url="http://localhost:8000", api_key="", task="t")

    headers = mock_post.call_args[1]["headers"]
    assert "X-API-Key" not in headers


def test_remote_dispatch_timeout_raises_runtime_error(tmp_path: Path):
    """Timeout deve levantar RuntimeError com mensagem clara."""
    import httpx
    orch = _make_orch(tmp_path)
    with patch("httpx.post", side_effect=httpx.TimeoutException("t")):
        with pytest.raises(RuntimeError, match="Timeout"):
            orch._remote_dispatch(url="http://localhost:8000", api_key="", task="t")


def test_remote_dispatch_connect_error_raises(tmp_path: Path):
    """ConnectError deve levantar RuntimeError com mensagem clara."""
    import httpx
    orch = _make_orch(tmp_path)
    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(RuntimeError, match="conectar"):
            orch._remote_dispatch(url="http://localhost:8000", api_key="", task="t")


# ─── AgentOrchestrator.execute_step com agente remoto ────────────────────────


def test_execute_step_dispatches_remotely_when_url_set(tmp_path: Path):
    """execute_step deve usar _remote_dispatch quando o agente tem url."""
    agents_yaml = tmp_path / "agents.yaml"
    reg = AgentRegistry(path=str(agents_yaml))
    reg.save(AgentDef(
        name="worker-remoto",
        description="Worker em outra máquina",
        system="s",
        url="http://worker-host:8001",
        api_key="abc",
    ))

    orch = _make_orch(tmp_path, agents_file=str(agents_yaml))

    step = {"id": 1, "goal": "processar dados", "tools": False,
            "depends_on": [], "agent": "worker-remoto"}

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "dados processados"}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp):
        result = orch.execute_step(step, [])

    assert result.response == "dados processados"
    assert "remote:" in result.model_used


def test_execute_step_falls_back_to_local_on_remote_failure(tmp_path: Path):
    """Falha no dispatch remoto deve fazer fallback para execução local."""
    import httpx
    agents_yaml = tmp_path / "agents.yaml"
    reg = AgentRegistry(path=str(agents_yaml))
    reg.save(AgentDef(
        name="worker-falho",
        description="Worker que vai falhar",
        system="s",
        url="http://worker-host:8001",
    ))

    orch = _make_orch(tmp_path, agents_file=str(agents_yaml))
    # client local retorna "resposta local"
    orch.client.chat_stream.return_value = iter(["resposta local"])

    step = {"id": 1, "goal": "tarefa", "tools": False,
            "depends_on": [], "agent": "worker-falho"}

    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        result = orch.execute_step(step, [])

    # Deve ter executado local — model_used não deve conter "remote:"
    assert "remote:" not in result.model_used


def test_execute_step_local_agent_unchanged(tmp_path: Path):
    """Agente sem url deve continuar executando localmente (sem httpx)."""
    agents_yaml = tmp_path / "agents.yaml"
    reg = AgentRegistry(path=str(agents_yaml))
    reg.save(AgentDef(
        name="local-agent",
        description="Agente local",
        system="s",
        # sem url
    ))

    orch = _make_orch(tmp_path, agents_file=str(agents_yaml))
    orch.client.chat_stream.return_value = iter(["resposta local"])

    step = {"id": 1, "goal": "tarefa local", "tools": False,
            "depends_on": [], "agent": "local-agent"}

    with patch("httpx.post") as mock_post:
        result = orch.execute_step(step, [])

    mock_post.assert_not_called()
    assert result.response == "resposta local"


# ─── delegate_task com agent_name remoto ─────────────────────────────────────


def test_delegate_task_dispatches_to_remote_agent(tmp_path: Path):
    """_delegate_task com agent_name remoto deve usar httpx.post."""
    from bauer.tool_router import ToolRouter
    from bauer.agent_registry import AgentRegistry, AgentDef

    agents_yaml = tmp_path / "agents.yaml"
    reg = AgentRegistry(path=str(agents_yaml))
    reg.save(AgentDef(
        name="worker-api",
        description="Worker API",
        system="s",
        url="http://worker:9000",
        api_key="key-xyz",
    ))

    router = ToolRouter(workspace=str(tmp_path), llm_client=None)
    router._bauer_home = tmp_path  # type: ignore[attr-defined]

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "resultado da API"}
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp):
        result = router.execute({
            "action": "delegate_task",
            "args": {
                "task": "fazer algo",
                "agent_name": "worker-api",
            },
        })

    assert "resultado da API" in result
    assert "worker-api" in result
```

**Verificar**:
```bash
python -m pytest tests/test_distributed_agents.py -v --tb=short
```
→ todos os testes do arquivo devem passar.

---

### Passo 7 — Suite completa

```bash
python -m pytest tests/ -q --tb=short
```
→ exit 0, sem regressões.

---

## Plano de testes

| Teste | Arquivo | O que cobre |
|-------|---------|-------------|
| `test_agentdef_url_round_trip` | test_distributed_agents.py | serialização/desserialização de url+api_key |
| `test_agentdef_without_url_omits_key` | test_distributed_agents.py | agente local não polui YAML |
| `test_agentdef_from_dict_handles_missing_url` | test_distributed_agents.py | compatibilidade retroativa |
| `test_remote_dispatch_posts_to_chat_endpoint` | test_distributed_agents.py | happy path HTTP |
| `test_remote_dispatch_no_api_key_omits_header` | test_distributed_agents.py | sem auth |
| `test_remote_dispatch_timeout_raises_runtime_error` | test_distributed_agents.py | timeout |
| `test_remote_dispatch_connect_error_raises` | test_distributed_agents.py | servidor offline |
| `test_execute_step_dispatches_remotely_when_url_set` | test_distributed_agents.py | orquestrador → remote |
| `test_execute_step_falls_back_to_local_on_remote_failure` | test_distributed_agents.py | fallback |
| `test_execute_step_local_agent_unchanged` | test_distributed_agents.py | regressão local |
| `test_delegate_task_dispatches_to_remote_agent` | test_distributed_agents.py | tool bridge → remote |

Padrão estrutural: `tests/test_orchestrator.py` (fixture `_make_orch`) e
`tests/test_agent_registry.py` (fixture `_make_registry`).

---

## Critérios de conclusão

- [ ] `python -m pytest tests/ -q --tb=short` → exit 0
- [ ] `python -m pytest tests/test_distributed_agents.py -v` → todos os 11 testes passam
- [ ] `python -c "from bauer.agent_registry import AgentDef; a = AgentDef(name='x', description='d', system='s', url='http://h:8000'); assert a.to_dict()['url'] == 'http://h:8000'; print('OK')"` → `OK`
- [ ] `python -c "from bauer.orchestrator import AgentOrchestrator; print('OK')"` → `OK`
- [ ] `git diff --name-only` mostra apenas os arquivos em escopo (+ o novo teste)
- [ ] `plans/README.md` linha de status atualizada para `DONE`

---

## Condições STOP

Pare e reporte sem improvisar se:

- O `AgentDef` em `bauer/agent_registry.py` já tem campo `url` (alguém implementou
  parcialmente — reconcilie antes de avançar).
- `execute_step()` não tem mais o trecho `agent_system = self._load_agent_system(agent_name)`
  (estrutura mudou — reanalise antes de inserir o bloco de dispatch).
- `_delegate_task` em `bauer/tools/execution.py` não começa com `task = args.get("task", "").strip()`
  (implementação divergiu — reanalise a inserção do bloco de dispatch remoto).
- Qualquer step falha duas vezes após tentativa razoável de correção.
- A correção requer tocar `bauer/server.py` ou `bauer/url_safety.py`.

---

## Notas de manutenção

**Para o responsável pelo código após este plano:**

- **Autenticação mTLS futura**: o campo `api_key` é texto em `agents.yaml`. Para
  produção, considere integração com OS keyring (padrão do plan 004 — auth.py).
  Candidato para próxima sprint de segurança.
- **Session-ID entre passos**: hoje cada passo cria uma nova sessão no worker remoto.
  Se a aplicação precisar de contexto persistente entre passos no mesmo worker,
  adicione `session_id` ao `OrchestratorConfig` e passe no `_remote_dispatch`.
- **Timeout configurável globalmente**: `remote_timeout_s` lido via
  `getattr(self.config, "remote_timeout_s", 120.0)` — pode ser adicionado ao
  `OrchestratorConfig` sem quebrar código existente.
- **`bauer agent create --url`**: flag CLI não está neste plano. Para criar agentes
  remotos via CLI, adicionar `--url` e `--agent-api-key` ao comando `agent create`
  em `bauer/commands/agent_cmd.py`.
- **Descoberta automática**: workers ainda precisam ser registrados manualmente em
  `agents.yaml`. Um mecanismo de service-discovery (mDNS, consul) é o próximo passo
  natural após validar o MVP.
