"""Hermeticidade global da suíte — nenhum teste faz chamada de LLM real.

Causa-raiz (2026-07-02): o CI foi de ~5min para 2-3.5h a partir do commit
4a7061b, que trocou o config.yaml do REPO de openai/api_key-vazia (falhava
instantâneo) para opencode (provider vivo, OAuth global). Qualquer caminho
best-effort com cfg=None — em especial a compressão semântica do
ContextManager via auxiliary_client._try_load_default_config() — passou a
carregar esse config real (cwd do pytest = raiz do repo), construir um client
de verdade e fazer chamadas HTTP reais DENTRO dos testes, com retry/backoff
de 2s/4s/8s em 429 do free tier. Resultado medido: testes de
test_context_manager*.py levando 40-75s CADA (vs <1s rule-based).

Correção nas DUAS camadas de fallback do load_config:
- BAUER_CONFIG → caminho inexistente (mata o autoload do config.yaml do cwd
  no auxiliary_client);
- BAUER_HOME → diretório temporário vazio (mata o fallback para
  ~/.bauer/config.yaml, que em máquina de dev tem provider real configurado —
  ver test_auxiliary_client.py::test_no_config_no_autoload_returns_none_pair,
  que já documentava essa armadilha por-teste).

Com isso, caminhos cfg=None degradam para (None, None) → fallback rule-based
determinístico, igual ao comportamento em CI limpo. Testes que querem exercitar
a resolução de config passam `cfg` explícito (não são afetados) ou setam os
env vars por conta própria via monkeypatch (override por-teste continua
funcionando normalmente).

os.environ é herdado por subprocessos (testes que spawnam `python -m
bauer.cli`), então a hermeticidade cobre também os testes de CLI.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Módulo-level (não fixture): precisa valer antes de qualquer import/teste,
# e cada worker do pytest-xdist importa este conftest — isolamento por worker.
os.environ["BAUER_CONFIG"] = str(
    Path(tempfile.gettempdir()) / "bauer-tests-no-such-config" / "config.yaml"
)
os.environ["BAUER_HOME"] = tempfile.mkdtemp(prefix="bauer-tests-home-")
# Mesma hermeticidade para delegate_task: sem isto, testes rodando com
# cwd=raiz do repo leriam o agents.yaml REAL do projeto (agents especialistas
# reais poderiam dar match acidental em tasks de teste genéricas como
# "Calcule 2+2").
os.environ["BAUER_AGENTS_FILE"] = str(
    Path(tempfile.gettempdir()) / "bauer-tests-no-such-agents" / "agents.yaml"
)
