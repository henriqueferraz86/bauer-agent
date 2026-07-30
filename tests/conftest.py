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

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

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
# Mesma hermeticidade para MCP: `MCP_SERVER_<NOME>` no ambiente VENCE o
# config.yaml na descoberta de servidores, então um dev com MCP configurado
# via env via servidores a mais do que o teste montou.
# test_mcp_discovery::test_lista_servidores_do_config falhava só na máquina do
# dev ("3 servidor(es)" em vez de 2, por causa de MCP_SERVER_GITMCP) e passava
# no CI — parecia flake de plataforma, era ambiente vazando para dentro do teste.
for _k in [k for k in os.environ if k.startswith("MCP_SERVER_")]:
    del os.environ[_k]


@pytest.fixture(autouse=True)
def _isolate_kanban_board(request, monkeypatch):
    """Board kanban_db próprio por teste.

    O backend SQLite guarda tarefas num board GLOBAL sob BAUER_HOME — que este
    conftest define uma vez por sessão. Com o backend markdown isso nunca doeu
    (cada teste tinha seu tmp_path), mas ao medir/rodar com
    `agent.task_backend=sqlite` todos os testes passariam a dividir
    `boards/default/kanban.db` e o estado vazaria entre eles (testes de "lista
    vazia" quebram por tarefas criadas por OUTRO teste).

    Damos a cada teste um nome de board único. Testes que passam `board=`
    explicitamente continuam mandando — isto só troca o ponteiro default.
    """
    # md5 do nodeid: determinístico entre runs (hash() varia com
    # PYTHONHASHSEED) e sempre seguro como nome de diretório.
    digest = hashlib.md5(request.node.nodeid.encode("utf-8")).hexdigest()[:12]
    monkeypatch.setenv("BAUER_KANBAN_BOARD", f"t-{digest}")


# ── Deadline anti-travamento para testes assíncronos ────────────────────────
#
# Flake real (CI do master, Windows/3.12, 2026-07-25):
# `test_start_shuts_down_when_budget_exhausted` estourou um
# `asyncio.wait_for(..., timeout=3.0)`. O watchdog que o teste espera roda a
# cada 50ms — margem de 60x — mas o job levou ~7min contra 5m44s dos anteriores:
# runner carregado. Localmente o mesmo teste passa 25/25.
#
# A lição, que vale para TODO deadline de wall-clock em teste:
#
#   O timeout aqui é REDE DE SEGURANÇA CONTRA TRAVAMENTO, não uma assertiva de
#   velocidade. No caminho feliz ele nunca é alcançado — o teste termina em
#   dezenas de ms. No caminho quebrado, ele só decide QUANTO você espera antes
#   de ver a falha. Apertá-lo não torna o código mais rápido nem o teste mais
#   rigoroso: só transforma lentidão do runner em vermelho falso.
#
# Por isso o valor é generoso. Se um teste precisa mesmo afirmar "isto é
# rápido", meça o que o código faz (iterações, chamadas) — não o relógio de
# parede de uma VM compartilhada.
DEADLINE_ANTI_TRAVAMENTO = 30.0


# ── tolerância de tempo sob execução paralela ────────────────────────────────
#
# A suíte roda com `-n auto` (pyproject). Num runner de CI carregado o worker é
# desescalonado, e asserções do tipo `elapsed < X` medem escalonamento, não o
# comportamento que pretendem verificar. Aconteceu de verdade: um deadline de
# 200ms levou 3.97s de wall clock com o mecanismo funcionando perfeitamente.
#
# Onde dá, prefira asserção DETERMINÍSTICA (um threading.Event provando que a
# chamada voltou enquanto o outro lado ainda rodava). Onde o tempo é mesmo o
# objeto do teste, multiplique o teto por este fator: ele preserva a intenção
# ("voltou MUITO antes do sleep de N s") sem transformar carga de máquina em
# falha de produto.
import os as _os

FATOR_TEMPO_CI: float = 6.0 if _os.environ.get("CI") else 3.0


@pytest.fixture
def limite():
    """Teto de tempo tolerante a escalonamento. Use com `elapsed < limite(0.2)`."""
    def _limite(segundos: float) -> float:
        return segundos * FATOR_TEMPO_CI
    return _limite
