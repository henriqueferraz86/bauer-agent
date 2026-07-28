"""Caminhos que eram relativos ao CWD e mentiam ou se espalhavam.

Duas manifestações da mesma raiz, medidas no Beelink em 2026-07-27:

- `bauer doctor` / `bauer serve` imprimiam o config PEDIDO, não o CARREGADO.
  Rodando de /tmp o doctor anunciava `Config: /tmp/config.yaml` — arquivo que
  não existe — enquanto lia `~/.bauer/config.yaml`. Justamente a confusão que
  aquela linha foi criada para acabar.
- `record_incident` gravava em `./logs/incidents`, então cada pasta de projeto
  onde o `bauer run` rodasse ganhava a sua — telemetria inagregável, e perdida
  em silêncio quando o CWD não é gravável.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.commands._common import _loaded_config_path
from bauer.config_loader import ConfigError, load_config, resolve_config_path
from bauer.incidents import (
    default_incidents_dir,
    list_incidents,
    record_incident,
)


CONFIG_MINIMO = """
model:
  provider: ollama
  name: qwen3-coder:30b
"""


@pytest.fixture
def bauer_home(tmp_path, monkeypatch):
    home = tmp_path / "bauer_home"
    home.mkdir()
    monkeypatch.setenv("BAUER_HOME", str(home))
    return home


# ─── qual config é realmente carregado ───────────────────────────────────────


def test_resolve_cai_para_bauer_home_quando_path_nao_existe(bauer_home, tmp_path, monkeypatch):
    (bauer_home / "config.yaml").write_text(CONFIG_MINIMO, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    resolvido = resolve_config_path("config.yaml")
    assert resolvido == bauer_home / "config.yaml"
    # e é de fato o que o load_config lê
    assert load_config("config.yaml").model.name == "qwen3-coder:30b"


def test_resolve_respeita_path_explicito_existente(bauer_home, tmp_path, monkeypatch):
    (bauer_home / "config.yaml").write_text(CONFIG_MINIMO, encoding="utf-8")
    proprio = tmp_path / "meu.yaml"
    proprio.write_text(CONFIG_MINIMO.replace("qwen3-coder:30b", "qwen2.5:7b"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert resolve_config_path(proprio) == proprio
    assert load_config(proprio).model.name == "qwen2.5:7b"


def test_resolve_sem_nenhum_config_explica_os_dois_caminhos(bauer_home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as exc:
        resolve_config_path("config.yaml")
    msg = str(exc.value)
    assert "config.yaml" in msg
    assert str(bauer_home) in msg


def test_display_mostra_o_arquivo_carregado_nao_o_pedido(bauer_home, tmp_path, monkeypatch):
    """O bug: `Config: /tmp/config.yaml`, path inexistente."""
    (bauer_home / "config.yaml").write_text(CONFIG_MINIMO, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    exibido = _loaded_config_path(Path("config.yaml"))
    assert exibido == (bauer_home / "config.yaml").resolve()
    assert exibido.exists(), "o doctor não pode anunciar um caminho que não existe"


def test_display_degrada_para_o_pedido_quando_nada_existe(bauer_home, tmp_path, monkeypatch):
    """Sem config nenhum, a linha de display não pode explodir.

    Quem reporta o erro de verdade é o load_config.
    """
    monkeypatch.chdir(tmp_path)
    assert _loaded_config_path(Path("config.yaml")) == (tmp_path / "config.yaml").resolve()


# ─── onde os incidentes são gravados ─────────────────────────────────────────


def test_incidente_vai_para_bauer_home_e_nao_para_o_cwd(bauer_home, tmp_path, monkeypatch):
    projeto = tmp_path / "projeto-do-usuario"
    projeto.mkdir()
    monkeypatch.chdir(projeto)

    path = record_incident("empty_response", model="qwen3-coder:30b")

    assert path is not None
    assert path.parent == bauer_home / "logs" / "incidents"
    assert not (projeto / "logs").exists(), "o run sujou a pasta do projeto"


def test_default_incidents_dir_segue_bauer_home_em_tempo_de_chamada(tmp_path, monkeypatch):
    """Resolvido na chamada, não no import — senão $BAUER_HOME tardio é ignorado."""
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "a"))
    primeiro = default_incidents_dir()
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "b"))
    assert default_incidents_dir() != primeiro


def test_list_incidents_ainda_enxerga_o_local_antigo(bauer_home, tmp_path, monkeypatch):
    """Telemetria já gravada em ./logs/incidents não pode sumir do relatório."""
    projeto = tmp_path / "projeto"
    antigo = projeto / "logs" / "incidents"
    antigo.mkdir(parents=True)
    (antigo / "20260101_000000_000_legado.json").write_text(
        '{"kind": "legado", "timestamp": "2026-01-01T00:00:00", "details": {}}',
        encoding="utf-8",
    )
    monkeypatch.chdir(projeto)

    record_incident("novo")
    kinds = [d["kind"] for d in list_incidents()]

    assert "novo" in kinds and "legado" in kinds
    # mais recentes primeiro, ordenando ENTRE os dois diretórios
    assert kinds.index("novo") < kinds.index("legado")


def test_list_incidents_filtra_por_kind(bauer_home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    record_incident("empty_response")
    record_incident("tool_loop")
    assert [d["kind"] for d in list_incidents(kind="tool_loop")] == ["tool_loop"]
