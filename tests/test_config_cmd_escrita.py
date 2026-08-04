"""`bauer config set` não pode criar config na pasta de onde foi chamado.

Bug visto DUAS VEZES na mesma máquina, pelo mesmo mecanismo:

  1. o atalho de tema gravava com `set_config_value("agent.accent", nome)` e o
     default relativo criava `./config.yaml` (corrigido em `_persistir_acento`);
  2. `bauer config set ui.truecolor sim`, rodado do `$HOME`, criou
     `~/config.yaml` com 21 bytes.

Nos dois casos o arquivo parcial passava a ofuscar o `~/.bauer/config.yaml` e
TODO comando `bauer` naquela pasta morria em "model: Field required" — um
sintoma que não aponta em nada para a causa.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bauer.commands.config_cmd import _config_para_escrita


@pytest.fixture
def home_com_config(tmp_path, monkeypatch):
    """Um `~/.bauer/config.yaml` válido, como numa instalação real."""
    home = tmp_path / "bauer-home"
    home.mkdir()
    cfg = home / "config.yaml"
    cfg.write_text(
        yaml.safe_dump({"model": {"provider": "ollama", "name": "x"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("BAUER_HOME", str(home))
    return cfg


class TestNaoCriaNaPastaAtual:
    def test_pasta_sem_config_escreve_no_home(self, tmp_path, home_com_config, monkeypatch):
        projeto = tmp_path / "projeto-qualquer"
        projeto.mkdir()
        monkeypatch.chdir(projeto)

        alvo = _config_para_escrita(Path("config.yaml"))

        assert alvo.resolve() == home_com_config.resolve(), (
            "deveria escrever no config que o load_config lê, não na pasta atual"
        )
        assert not (projeto / "config.yaml").exists()

    def test_pasta_com_config_usa_o_local(self, tmp_path, home_com_config, monkeypatch):
        """Projeto com config próprio continua mandando — é a precedência que
        o `load_config` já usa."""
        projeto = tmp_path / "projeto-com-config"
        projeto.mkdir()
        local = projeto / "config.yaml"
        local.write_text(
            yaml.safe_dump({"model": {"provider": "ollama", "name": "y"}}),
            encoding="utf-8",
        )
        monkeypatch.chdir(projeto)

        assert _config_para_escrita(Path("config.yaml")).resolve() == local.resolve()

    def test_sem_config_nenhum_cai_no_canonico(self, tmp_path, monkeypatch):
        """Instalação nova: escreve no ~/.bauer, não na pasta de onde o
        comando por acaso foi chamado."""
        home = tmp_path / "home-vazio"
        home.mkdir()
        projeto = tmp_path / "projeto"
        projeto.mkdir()
        monkeypatch.setenv("BAUER_HOME", str(home))
        monkeypatch.chdir(projeto)

        alvo = _config_para_escrita(Path("config.yaml"))

        assert home in alvo.parents or alvo.parent == home
        assert not (projeto / "config.yaml").exists()

    def test_caminho_explicito_e_respeitado(self, tmp_path, home_com_config, monkeypatch):
        """`--config caminho` é intenção do usuário — inclusive a de criar."""
        monkeypatch.chdir(tmp_path)
        pedido = tmp_path / "meu-config.yaml"

        assert _config_para_escrita(pedido) == pedido


class TestFimAFim:
    def test_config_set_nao_suja_a_pasta(self, tmp_path, home_com_config, monkeypatch):
        """O caminho real do bug: `bauer config set` de dentro de um projeto."""
        from bauer.config_admin import set_config_value

        projeto = tmp_path / "financeos-pme"
        projeto.mkdir()
        monkeypatch.chdir(projeto)

        alvo = _config_para_escrita(Path("config.yaml"))
        set_config_value("ui.truecolor", "sim", alvo)

        assert not (projeto / "config.yaml").exists(), (
            "criou config na pasta do projeto — é o bug que ofusca o config "
            "real e derruba todo comando bauer ali"
        )
        salvo = yaml.safe_load(home_com_config.read_text(encoding="utf-8"))
        assert salvo["ui"]["truecolor"] == "sim"
        assert salvo["model"]["name"] == "x", "sobrescreveu o resto do config"
