"""`--local`: um pedido explícito de que NADA saia da máquina.

O caso que motivou: o usuário baixou um GGUF, apontou o config para ele, e o
`bauer agent` respondeu com `minimax/minimax-m2.5` no OpenRouter. Não era bug do
modelo — era o roteamento por tier, com os quatro tiers na nuvem.

A saída óbvia seria desligar o roteamento. Mas isso joga fora a parte útil dele:
escolher o modelo certo para cada tipo de tarefa. Daí a flag: mantém o
roteamento LIGADO e só troca para onde aponta (`model.profiles_local`).

**O que torna a flag honesta é a recusa.** Há TRÊS portas para a nuvem, e fechar
só o roteamento faria `--local` mentir:

    profiles         os tiers — a porta óbvia
    model.name       caminhos sem tier vão direto nele
    fallback_models  se o local engasgar, é para onde o Bauer corre

As duas últimas abrem em silêncio: um hiccup do Ollama manda o contexto para
fora sem uma linha na tela. Por isso `--local` recusa a subir em vez de avisar —
"quase local" não é o que ninguém quer dizer ao digitar isso.
"""

from __future__ import annotations

from types import SimpleNamespace as N

import pytest

from bauer.model_router import (
    PROVIDERS_LOCAIS,
    profiles_from_config,
    provider_e_local,
    validar_execucao_local,
)


def _cfg(**kw):
    base = dict(provider="ollama", name="qwen3:14b", profiles={},
                profiles_local={}, fallback_models=[], fallback_models_local=[])
    base.update(kw)
    return N(model=N(**base))


def _fb(provider, name):
    return N(provider=provider, name=name)


# ─── o que conta como local ──────────────────────────────────────────────────


@pytest.mark.parametrize("provider,esperado", [
    ("ollama", True), ("lmstudio", True), ("OLLAMA", True),
    ("openrouter", False), ("openai", False), ("anthropic", False),
    ("", False),
])
def test_providers(provider, esperado):
    assert provider_e_local(provider) is esperado


def test_custom_depende_do_host():
    """`custom` aponta para onde o usuário mandar. Tratá-lo como local por
    padrão transformaria a flag numa promessa que qualquer endpoint externo
    quebraria em silêncio."""
    assert provider_e_local("custom", N(custom=N(host="http://localhost:9000")))
    assert provider_e_local("custom", N(custom=N(host="http://127.0.0.1:8080")))
    assert not provider_e_local("custom", N(custom=N(host="https://api.externa.com")))
    assert not provider_e_local("custom", None), "sem cfg não dá para saber — não assume"


def test_lmstudio_conta():
    """Não é só Ollama. Quem roda LM Studio também está local, e excluí-lo faria
    a flag recusar uma configuração legítima."""
    assert "lmstudio" in PROVIDERS_LOCAIS


# ─── as três portas ──────────────────────────────────────────────────────────


def test_config_totalmente_local_passa():
    cfg = _cfg(profiles_local={"fast": {"provider": "ollama", "model": "x"}})
    assert validar_execucao_local(cfg) == []


def test_porta_1_sem_profiles_local():
    """Sem o bloco não há para onde rotear."""
    p = validar_execucao_local(_cfg())
    assert any("profiles_local" in x for x in p)


def test_porta_2_modelo_padrao_na_nuvem():
    """A traiçoeira nº1: caminhos sem tier (bauer run, run-one) vão direto no
    `model.name`. Fechar só o roteamento deixaria essa porta escancarada."""
    cfg = _cfg(provider="openrouter", name="z-ai/glm-5.2",
               profiles_local={"fast": {"provider": "ollama", "model": "x"}})
    p = validar_execucao_local(cfg)
    assert any("model.name" in x and "openrouter" in x for x in p)


def test_porta_3_fallback_LOCAL_na_nuvem():
    """A traiçoeira nº2, e a pior: um hiccup do Ollama manda o contexto para
    fora SEM uma linha na tela. O usuário nunca saberia."""
    cfg = _cfg(profiles_local={"fast": {"provider": "ollama", "model": "x"}},
               fallback_models_local=[_fb("openrouter", "moonshotai/kimi-k3")])
    p = validar_execucao_local(cfg)
    assert any("fallback_models_local" in x and "kimi-k3" in x for x in p)


def test_fallback_de_NUVEM_nao_bloqueia_o_local():
    """O par existe para isto: `fallback_models` continua podendo apontar para
    fora, porque no modo `--local` ele nem e lido. Sem o par, o usuario teria de
    escolher entre ter `--local` e ter socorro grande no modo de nuvem."""
    cfg = _cfg(profiles_local={"fast": {"provider": "ollama", "model": "x"}},
               fallback_models=[_fb("openrouter", "moonshotai/kimi-k3")],
               fallback_models_local=[_fb("ollama", "qwen2.5:7b")])
    assert validar_execucao_local(cfg) == []


def test_sem_fallback_local_e_seguro():
    """Lista local vazia = sem socorro nenhum no modo local. Seguro por
    construcao: nao ha para onde vazar."""
    cfg = _cfg(profiles_local={"fast": {"provider": "ollama", "model": "x"}},
               fallback_models=[_fb("openrouter", "kimi")])
    assert validar_execucao_local(cfg) == []


def test_reporta_TODOS_os_problemas_de_uma_vez():
    """Um por vez faria o usuário rodar, corrigir, rodar, corrigir. A mensagem
    tem de dizer tudo que falta na primeira tentativa."""
    cfg = _cfg(provider="openrouter", name="glm",
               fallback_models_local=[_fb("openrouter", "kimi")])
    assert len(validar_execucao_local(cfg)) == 3


def test_config_real_do_usuario():
    """Reproduz o config do Beelink que gerou o caso. Os três problemas."""
    cfg = _cfg(provider="ollama", name="hf.co/prism-ml/Bonsai-27B-gguf:Q4_1",
               profiles={"fast": {"provider": "openrouter", "model": "minimax/minimax-m2.5"}},
               fallback_models=[_fb("openrouter", "moonshotai/kimi-k3")])
    p = validar_execucao_local(cfg)

    # `model.name` PASSA — é ollama. Que ele esteja quebrado é outro problema:
    # o `--local` responde "sai da máquina?", não "funciona?".
    assert not any("model.name" in x for x in p)
    assert any("profiles_local" in x for x in p)
    # o fallback de NUVEM nao entra: no modo local ele nem e lido
    assert not any("fallback" in x for x in p)


def test_nao_verifica_se_o_modelo_FUNCIONA():
    """Fronteira explícita. Misturar as duas perguntas deixaria a mensagem
    confusa — o `bauer doctor` diz se o Ollama está no ar; este diz se algo
    sairia da máquina."""
    cfg = _cfg(name="modelo-que-nao-existe-nenhum",
               profiles_local={"fast": {"provider": "ollama", "model": "y"}})
    assert validar_execucao_local(cfg) == []


def test_model_name_do_argumento_vence():
    """`--model` sobrescreve o config, então entra na mesma checagem — senão
    passar um modelo de nuvem por linha de comando furaria a flag."""
    cfg = _cfg(provider="openrouter", name="glm",
               profiles_local={"fast": {"provider": "ollama", "model": "x"}})
    # o provider continua sendo o do config; o nome não muda isso
    assert any("model.name" in x for x in validar_execucao_local(cfg, "qwen3:14b"))


# ─── o conjunto de perfis ────────────────────────────────────────────────────


def test_conjunto_local_le_o_bloco_certo():
    cfg = _cfg(profiles={"fast": {"provider": "openrouter", "model": "nuvem"}},
               profiles_local={"fast": {"provider": "ollama", "model": "meu"}})

    assert profiles_from_config(cfg)["fast"].model == "nuvem"
    assert profiles_from_config(cfg, conjunto="local")["fast"].model == "meu"


def test_default_inalterado():
    """Compatibilidade: quem não usa a flag não pode sentir diferença."""
    cfg = _cfg(profiles={"fast": {"provider": "openrouter", "model": "nuvem"}})
    assert profiles_from_config(cfg)["fast"].model == "nuvem"


def test_conjunto_local_ausente_devolve_vazio():
    """E vazio faz `heuristic_route_kit` devolver (None, None) — o turno cai no
    `model.name`, que a checagem de entrada já garantiu ser local."""
    assert profiles_from_config(_cfg(), conjunto="local") == {}


# ─── as flags existem nos dois lugares ───────────────────────────────────────


@pytest.mark.parametrize("modulo", ["agent_cmd", "serve_cmd"])
def test_flag_registrada(modulo):
    """`serve` também. Sem ele o usuário resolveria o interativo e o SERVIDOR —
    que é o que fica no ar sem ninguém olhando — continuaria indo para a nuvem."""
    import importlib
    import inspect

    m = importlib.import_module(f"bauer.commands.{modulo}")
    fonte = inspect.getsource(m)
    assert '"--local"' in fonte
    assert "validar_execucao_local" in fonte


def test_serve_passa_o_conjunto_ao_app():
    import inspect

    from bauer.server import create_app

    assert "profile_set" in inspect.signature(create_app).parameters
    fonte = inspect.getsource(
        __import__("bauer.commands.serve_cmd", fromlist=["x"]))
    assert 'profile_set="local" if local else "default"' in fonte


# ─── o par de fallback: cada modo com o seu ──────────────────────────────────


def test_construtor_le_a_lista_certa():
    """`build_fallback_clients(conjunto="local")` tem de ler `fallback_models_local`.
    Se lesse a de nuvem, a validacao passaria e o socorro sairia da maquina
    assim mesmo — a flag mentiria no unico momento em que importa."""
    import inspect

    from bauer.commands._runtime import build_fallback_clients
    from bauer.commands.agent_cmd import _build_fallback_clients

    for fn in (build_fallback_clients, _build_fallback_clients):
        assert "conjunto" in inspect.signature(fn).parameters
        assert "fallback_models_local" in inspect.getsource(fn)


def test_call_sites_passam_o_conjunto():
    """Ter o parametro e nao passa-lo seria pior que nao ter: daria a impressao
    de estar coberto."""
    import inspect

    for mod in ("agent_cmd", "serve_cmd"):
        fonte = inspect.getsource(__import__(f"bauer.commands.{mod}", fromlist=["x"]))
        assert 'conjunto="local" if local else "default"' in fonte, mod


def test_schema_aceita_o_campo():
    """`extra="forbid"`: sem o campo no schema, acrescenta-lo ao config.yaml
    quebra o boot com ValidationError. Foi o que aconteceu ao adicionar
    `profiles_local` antes de atualizar o Bauer da maquina."""
    from bauer.config_loader import ModelSection

    assert "fallback_models_local" in ModelSection.model_fields
