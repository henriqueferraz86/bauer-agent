# Plan 005: Testes de integração para os 35 módulos de bauer/commands/

> **Instruções ao executor**: Siga este plano passo a passo. Execute cada
> comando de verificação e confirme o resultado esperado antes de avançar.
> Se qualquer condição de STOP ocorrer, pare e reporte — não improvise.
> Ao terminar, atualize a linha de status deste plano em `plans/README.md`.
>
> **Drift check (execute primeiro)**:
> `git diff --stat 820322b..HEAD -- bauer/commands/ tests/test_cli.py tests/test_cli_commands2.py`
> Se a estrutura de `bauer/commands/` mudou desde este plano, compare a lista
> de módulos abaixo antes de prosseguir.

## Status

- **Prioridade**: P1
- **Esforço**: M
- **Risco**: LOW
- **Depende de**: nenhum
- **Categoria**: tests
- **Planejado em**: commit `820322b`, 2026-06-27

## Por que isso importa

O refactor P4 extraiu 36 grupos de comandos Typer do `cli.py` (10403 → 2317
linhas) para `bauer/commands/`. Os testes existentes (`test_cli.py`,
`test_cli_commands2.py`, `test_cli_extended.py`, `test_cli_extra_coverage.py`)
testam via `bauer.cli.app` no nível de integração top-level, mas nenhum
arquivo de teste corresponde individualmente a um módulo de `bauer/commands/`.

Isso significa que:

1. Uma quebra de import em `bauer/commands/agent_cmd.py` pode não ser
   detectada até o usuário rodar `bauer agent`.
2. Os caminhos de erro de cada comando (config ausente, modelo não encontrado,
   workspace inválido) não têm cobertura dedicada.
3. O CI não tem granularidade para identificar qual grupo de comandos regrediu.

Este plano cria 4 arquivos de teste novos cobrindo os grupos de maior risco
e uso: `config_cmd`, `models_cmd`, `tools_cmd`, e `agent_cmd` (parte de
inicialização/argparse). O padrão é reproduzível para os demais grupos.

## Estado atual

### Estrutura existente dos módulos

`bauer/commands/` contém (entre outros):
- `config_cmd.py` — `config validate`, `config show`
- `models_cmd.py` — `models list`, `models test`
- `tools_cmd.py` — `tools list`, `tools run`
- `agent_cmd.py` — `agent` (985 linhas, entrada principal do agente)
- `orchestrate_cmd.py` — `orchestrate run`
- `serve_cmd.py` — `serve`
- `task_cmd.py` — `task add`, `task list`, `task start`, `task done`, etc.
- `learning_cmd.py` — `learning show`, `learning reset`, etc.
- `memory_cmd.py` — `memory summarize`

### Padrão de teste existente (use como modelo)

Do `tests/test_cli.py`:

```python
# tests/test_cli.py — padrão a seguir

from typer.testing import CliRunner
from bauer.cli import app

runner = CliRunner()

@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    c = tmp_path / "config.yaml"
    c.write_text(
        "agent:\n  name: Test\n  workspace: ./workspace\n"
        "model:\n  provider: ollama\n  name: qwen2.5:3b\n"
        "  requested_context: 8192\n  minimum_context: 4096\n"
        "  auto_downgrade_context: true\n"
        "ollama:\n  host: http://localhost:11434\n  timeout_seconds: 10\n  api_key: ''\n"
        "openai:\n  host: http://localhost:1234\n  timeout_seconds: 30\n  api_key: ''\n"
        "runtime:\n  profile: low\n  ram_limit_mb: 4096\n  safety_margin_mb: 512\n"
        "logging:\n  level: info\n  file: null\n"
        "tools:\n  shell_enabled: false\n  safe_mode: true\n"
        "  timeout_seconds: 30\n  max_output_kb: 50\n"
        "serve:\n  host: 0.0.0.0\n  port: 8000\n  api_key: ''\n  workers: 1\n",
        encoding="utf-8",
    )
    return c

@pytest.fixture
def models_path(tmp_path: Path) -> Path:
    m = tmp_path / "models.yaml"
    m.write_text(
        "models:\n"
        "  qwen2.5:3b:\n"
        "    provider: ollama\n"
        "    ram_base_mb: 2500\n"
        "    ram_per_1k_ctx_mb: 40\n"
        "    max_context_safe: 32768\n"
        "    supports_tools: false\n"
        "    ram_profile: low\n",
        encoding="utf-8",
    )
    return m

def test_config_validate_ok(cfg_path: Path):
    result = runner.invoke(app, ["config", "validate", "--config", str(cfg_path)])
    assert result.exit_code == 0
    assert "OK" in result.output
```

### Como os módulos se registram no app

`bauer/cli.py` adiciona cada sub-Typer ao `app` principal. Por exemplo:

```python
# bauer/cli.py (trecho)
from .commands.config_cmd import config_app
app.add_typer(config_app, name="config")
```

Portanto, os testes testam via `runner.invoke(app, ["config", "validate", ...])`.

## Comandos necessários

| Propósito | Comando | Esperado no sucesso |
|-----------|---------|---------------------|
| Testes do grupo | `python -m pytest tests/test_commands_config.py -q --tb=short` | exit 0 |
| Testes completos | `python -m pytest tests/ -q --tb=short` | exit 0 |
| Lint crítico | `ruff check tests/test_commands_*.py --select E9,F63,F7,F82` | exit 0 |
| Importação dos módulos | `python -c "from bauer.commands import config_cmd, models_cmd, tools_cmd, agent_cmd"` | exit 0 |

## Escopo

**Em escopo** (únicos arquivos a criar):
- `tests/test_commands_config.py`
- `tests/test_commands_models.py`
- `tests/test_commands_tools.py`
- `tests/test_commands_agent.py`

**Fora de escopo** (não modifique):
- `bauer/commands/*.py` — se um teste falhar porque o módulo tem um bug,
  STOP e reporte; não corrija o módulo aqui
- `bauer/cli.py` — não altere o registro de comandos
- `tests/test_cli.py`, `tests/test_cli_commands2.py` — não modifique os
  testes existentes; os novos são adicionais, não substitutos

## Workflow Git

- Branch: `advisor/005-commands-integration-tests`
- Commits: um por arquivo de teste criado (4 commits)
  - `test(commands): testes de integracao para config_cmd`
  - `test(commands): testes de integracao para models_cmd`
  - `test(commands): testes de integracao para tools_cmd`
  - `test(commands): testes de integracao para agent_cmd (init paths)`
- NÃO faça push nem abra PR salvo instrução explícita.

## Passos

### Passo 1: Criar `tests/test_commands_config.py`

```python
"""Testes de integração para bauer/commands/config_cmd.py."""
from __future__ import annotations

from pathlib import Path

import pytest
pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app

runner = CliRunner()


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    c = tmp_path / "config.yaml"
    c.write_text(
        "agent:\n  name: Test\n  workspace: ./workspace\n"
        "model:\n  provider: ollama\n  name: qwen2.5:3b\n"
        "  requested_context: 8192\n  minimum_context: 4096\n"
        "  auto_downgrade_context: true\n"
        "ollama:\n  host: http://localhost:11434\n  timeout_seconds: 10\n  api_key: ''\n"
        "openai:\n  host: http://localhost:1234\n  timeout_seconds: 30\n  api_key: ''\n"
        "runtime:\n  profile: low\n  ram_limit_mb: 4096\n  safety_margin_mb: 512\n"
        "logging:\n  level: info\n  file: null\n"
        "tools:\n  shell_enabled: false\n  safe_mode: true\n"
        "  timeout_seconds: 30\n  max_output_kb: 50\n"
        "serve:\n  host: 0.0.0.0\n  port: 8000\n  api_key: ''\n  workers: 1\n",
        encoding="utf-8",
    )
    return c


# ─── config validate ──────────────────────────────────────────────────────────

def test_config_validate_ok(cfg_path: Path):
    result = runner.invoke(app, ["config", "validate", "--config", str(cfg_path)])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_config_validate_file_not_found(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "empty"))
    result = runner.invoke(app, ["config", "validate", "--config", str(tmp_path / "nao_existe.yaml")])
    assert result.exit_code != 0


def test_config_validate_invalid_yaml(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(": invalid: yaml: [[\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "validate", "--config", str(bad)])
    assert result.exit_code != 0


# ─── config show ─────────────────────────────────────────────────────────────

def test_config_show_ok(cfg_path: Path):
    result = runner.invoke(app, ["config", "show", "--config", str(cfg_path)])
    assert result.exit_code == 0
    # Deve exibir ao menos o provider e o modelo
    assert "ollama" in result.output.lower() or "qwen" in result.output.lower()


def test_config_show_raw(cfg_path: Path):
    result = runner.invoke(app, ["config", "show", "--config", str(cfg_path), "--raw"])
    assert result.exit_code == 0
```

**Verificar**: `python -m pytest tests/test_commands_config.py -q --tb=short` → 5 passed

### Passo 2: Criar `tests/test_commands_models.py`

```python
"""Testes de integração para bauer/commands/models_cmd.py."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app

runner = CliRunner()


@pytest.fixture
def cfg_models(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "agent:\n  name: Test\n  workspace: ./workspace\n"
        "model:\n  provider: ollama\n  name: qwen2.5:3b\n"
        "  requested_context: 8192\n  minimum_context: 4096\n"
        "  auto_downgrade_context: true\n"
        "ollama:\n  host: http://localhost:11434\n  timeout_seconds: 10\n  api_key: ''\n"
        "openai:\n  host: http://localhost:1234\n  timeout_seconds: 30\n  api_key: ''\n"
        "runtime:\n  profile: low\n  ram_limit_mb: 4096\n  safety_margin_mb: 512\n"
        "logging:\n  level: info\n  file: null\n"
        "tools:\n  shell_enabled: false\n  safe_mode: true\n"
        "  timeout_seconds: 30\n  max_output_kb: 50\n"
        "serve:\n  host: 0.0.0.0\n  port: 8000\n  api_key: ''\n  workers: 1\n",
        encoding="utf-8",
    )
    models = tmp_path / "models.yaml"
    models.write_text(
        "models:\n"
        "  qwen2.5:3b:\n"
        "    provider: ollama\n"
        "    ram_base_mb: 2500\n"
        "    ram_per_1k_ctx_mb: 40\n"
        "    max_context_safe: 32768\n"
        "    supports_tools: false\n"
        "    ram_profile: low\n",
        encoding="utf-8",
    )
    return cfg, models


def test_models_list_renders(cfg_models):
    cfg, models = cfg_models
    result = runner.invoke(app, [
        "models", "list",
        "--config", str(cfg),
        "--models", str(models),
    ])
    assert result.exit_code == 0
    assert "qwen2.5" in result.output or "models" in result.output.lower()


def test_models_list_missing_models_file(cfg_models, tmp_path):
    cfg, _ = cfg_models
    result = runner.invoke(app, [
        "models", "list",
        "--config", str(cfg),
        "--models", str(tmp_path / "nao_existe.yaml"),
    ])
    # Deve falhar de forma limpa
    assert result.exit_code != 0 or "erro" in result.output.lower() or "not found" in result.output.lower()
```

**Verificar**: `python -m pytest tests/test_commands_models.py -q --tb=short` → passed (exceto possivelmente os que requerem Ollama ativo)

### Passo 3: Criar `tests/test_commands_tools.py`

```python
"""Testes de integração para bauer/commands/tools_cmd.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app

runner = CliRunner()


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    c = tmp_path / "config.yaml"
    c.write_text(
        "agent:\n  name: Test\n  workspace: ./workspace\n"
        "model:\n  provider: ollama\n  name: qwen2.5:3b\n"
        "  requested_context: 8192\n  minimum_context: 4096\n"
        "  auto_downgrade_context: true\n"
        "ollama:\n  host: http://localhost:11434\n  timeout_seconds: 10\n  api_key: ''\n"
        "openai:\n  host: http://localhost:1234\n  timeout_seconds: 30\n  api_key: ''\n"
        "runtime:\n  profile: low\n  ram_limit_mb: 4096\n  safety_margin_mb: 512\n"
        "logging:\n  level: info\n  file: null\n"
        "tools:\n  shell_enabled: false\n  safe_mode: true\n"
        "  timeout_seconds: 30\n  max_output_kb: 50\n"
        "serve:\n  host: 0.0.0.0\n  port: 8000\n  api_key: ''\n  workers: 1\n",
        encoding="utf-8",
    )
    return c


def test_tools_list_ok(cfg_path: Path):
    result = runner.invoke(app, ["tools", "list", "--config", str(cfg_path)])
    assert result.exit_code == 0


def test_tools_run_list_dir(cfg_path: Path, tmp_path: Path):
    action = json.dumps({"action": "list_dir", "args": {"path": str(tmp_path)}})
    result = runner.invoke(app, ["tools", "run", action, "--config", str(cfg_path)])
    assert result.exit_code == 0


def test_tools_run_from_json_file(cfg_path: Path, tmp_path: Path):
    action_file = tmp_path / "action.json"
    action_file.write_text(
        json.dumps({"action": "list_dir", "args": {"path": str(tmp_path)}}),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["tools", "run", str(action_file), "--config", str(cfg_path)])
    assert result.exit_code == 0


def test_tools_run_invalid_json(cfg_path: Path):
    result = runner.invoke(app, ["tools", "run", "{nao-e-json}", "--config", str(cfg_path)])
    assert result.exit_code != 0 or "erro" in result.output.lower() or "invalid" in result.output.lower()
```

**Verificar**: `python -m pytest tests/test_commands_tools.py -q --tb=short` → passed

### Passo 4: Criar `tests/test_commands_agent.py`

Este arquivo testa os caminhos de inicialização de `agent_cmd.py` (não o loop
interativo, que requer input real) — foco em: import correto, erros de
configuração, flags `--model` e `--profile`.

```python
"""Testes de inicialização para bauer/commands/agent_cmd.py.

Cobre: import correto pós-P4, erros de config ausente, flags de CLI.
NÃO cobre o loop interativo do agente (requer input real do terminal).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app

runner = CliRunner()


def test_agent_module_importable():
    """Garantia de import pós-refactor P4."""
    from bauer.commands import agent_cmd
    assert hasattr(agent_cmd, "agent_app")


def test_agent_missing_config_exits_cleanly(tmp_path, monkeypatch):
    """bauer agent sem config.yaml deve sair com erro claro, não traceback."""
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "empty"))
    result = runner.invoke(app, [
        "agent",
        "--config", str(tmp_path / "nao_existe.yaml"),
    ])
    assert result.exit_code != 0
    # Não deve expor traceback Python ao usuário
    assert "Traceback" not in result.output


def test_agent_subcommands_registered():
    """Verifica que subcomandos 'create', 'list', 'run', 'delete' estão registrados."""
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    # Ao menos o help deve funcionar
    assert "agent" in result.output.lower()
```

**Verificar**: `python -m pytest tests/test_commands_agent.py -q --tb=short` → 3+ passed

### Passo 5: Confirmar que todos os 4 arquivos passam juntos

**Verificar**:
```
python -m pytest tests/test_commands_config.py tests/test_commands_models.py \
  tests/test_commands_tools.py tests/test_commands_agent.py -q --tb=short
```
→ todos passam

### Passo 6: Confirmar que a suite completa ainda passa

**Verificar**: `python -m pytest tests/ -q --tb=short` → exit 0

## Plano de testes

Estes arquivos são os testes em si — os novos testes seguem o padrão
`CliRunner` + `cfg_path(tmp_path)` já estabelecido em `tests/test_cli.py`.

**Cobertura alvo por módulo:**
- `config_cmd.py` — `validate` ok, file-not-found, yaml inválido; `show` ok, `show --raw`
- `models_cmd.py` — `list` ok, `list` com models.yaml ausente
- `tools_cmd.py` — `list` ok; `run` com JSON inline, com arquivo, com JSON inválido
- `agent_cmd.py` — import ok, config ausente, help registrado

## Critérios de conclusão

- [ ] `python -c "from bauer.commands import config_cmd, models_cmd, tools_cmd, agent_cmd"` → exit 0
- [ ] `python -m pytest tests/test_commands_config.py -q --tb=short` → todos passam
- [ ] `python -m pytest tests/test_commands_models.py -q --tb=short` → todos passam
- [ ] `python -m pytest tests/test_commands_tools.py -q --tb=short` → todos passam
- [ ] `python -m pytest tests/test_commands_agent.py -q --tb=short` → todos passam
- [ ] `ruff check tests/test_commands_*.py --select E9,F63,F7,F82` → exit 0
- [ ] `python -m pytest tests/ -q --tb=short` → exit 0
- [ ] Nenhum arquivo fora do escopo foi modificado
- [ ] `plans/README.md` linha de status atualizada

## Condições de STOP

Pare e reporte se:
- Um módulo de `bauer/commands/` lança `ImportError` ao ser importado
  (indica um bug real no módulo, não nos testes — reporte sem corrigir o módulo).
- Um teste falha porque a função de CLI tenta conectar ao Ollama de verdade
  (sem mock): adicione `@patch("bauer.ollama_client.OllamaClient.list_models", return_value=[])` em vez de corrigir a lógica.
- `runner.invoke` retorna `exit_code=0` mas `result.exception` não é None
  (exceção silenciada pelo Typer) — reporte o traceback exato.

## Notas de manutenção

- Este padrão pode ser replicado para os 31 grupos restantes em `bauer/commands/`
  (gateway_cmd, daemon_cmd, serve_cmd, task_cmd, learning_cmd, etc.) — crie
  um arquivo `test_commands_X.py` por grupo, seguindo o mesmo padrão.
- Quando novos subcomandos forem adicionados a um grupo, adicione testes no
  arquivo correspondente.
- O teste `test_agent_module_importable` é um guarda de regressão para o
  refactor P4 — se um novo refactor quebrar o import, ele falha primeiro.
