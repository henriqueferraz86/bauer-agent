"""Testes para SpecManager e wizard_auto_spec."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from bauer.spec_manager import Spec, SpecManager, SpecManagerError


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_manager(tmp_path: Path) -> SpecManager:
    return SpecManager(specs_dir=tmp_path / "specs")


def _sample_spec(spec_id: str = "meu-spec", status: str = "draft") -> Spec:
    return Spec(
        id=spec_id,
        title="Meu Spec",
        purpose="Faz X para resolver Y.",
        behavior=["Nunca retorna None.", "Erros têm mensagem clara."],
        interface={
            "inputs": [{"name": "query", "type": "str", "description": "busca", "required": True}],
            "outputs": [{"name": "resultado", "type": "str", "description": "resposta"}],
        },
        acceptance_criteria=[
            "Given query válida, When busca, Then retorna resultado.",
            "Given query vazia, When busca, Then lança ValueError.",
        ],
        linked_files=["bauer/meu_modulo.py"],
        status=status,
    )


# ─── Spec.valid_id ────────────────────────────────────────────────────────────


def test_valid_id_ok():
    assert Spec.valid_id("meu-spec")
    assert Spec.valid_id("spec123")
    assert Spec.valid_id("a1")
    assert Spec.valid_id("ab")


def test_valid_id_too_short():
    assert not Spec.valid_id("a")
    assert not Spec.valid_id("")


def test_valid_id_uppercase():
    assert not Spec.valid_id("MeuSpec")


def test_valid_id_spaces():
    assert not Spec.valid_id("meu spec")


def test_valid_id_max_length():
    # regex: ^[a-z0-9][a-z0-9_-]{1,50}$ → total 2–51 chars
    assert Spec.valid_id("a" * 51)       # 51 = limite máximo permitido
    assert not Spec.valid_id("a" * 52)   # 52 = excede limite


# ─── Spec serialização ────────────────────────────────────────────────────────


def test_to_dict_roundtrip():
    spec = _sample_spec()
    d = spec.to_dict()
    restored = Spec.from_dict(d)
    assert restored.id == spec.id
    assert restored.title == spec.title
    assert restored.purpose == spec.purpose
    assert restored.behavior == spec.behavior
    assert restored.acceptance_criteria == spec.acceptance_criteria
    assert restored.linked_files == spec.linked_files
    assert restored.status == spec.status


def test_from_dict_defaults():
    """from_dict deve tolerar campos ausentes com defaults seguros."""
    spec = Spec.from_dict({"id": "minimal", "title": "Mínimo"})
    assert spec.purpose == ""
    assert spec.behavior == []
    assert spec.acceptance_criteria == []
    assert spec.linked_files == []
    assert spec.status == "draft"
    assert spec.version == "1.0.0"


def test_to_dict_omits_empty_linked_files():
    spec = _sample_spec()
    spec.linked_files = []
    d = spec.to_dict()
    assert "linked_files" not in d


def test_to_dict_omits_empty_notes():
    spec = _sample_spec()
    spec.notes = ""
    d = spec.to_dict()
    assert "notes" not in d


# ─── Spec.to_context ──────────────────────────────────────────────────────────


def test_to_context_compact():
    spec = _sample_spec()
    text = spec.to_context(compact=True)
    assert spec.id in text
    assert spec.title in text
    for ac in spec.acceptance_criteria:
        assert ac in text


def test_to_context_full():
    spec = _sample_spec()
    text = spec.to_context(compact=False)
    assert "Purpose" in text
    assert "Behavior" in text
    assert "Acceptance Criteria" in text
    assert "Inputs" in text
    assert "Outputs" in text


# ─── SpecManager.save / get ───────────────────────────────────────────────────


def test_save_creates_yaml(tmp_path):
    mgr = _make_manager(tmp_path)
    spec = _sample_spec()
    path = mgr.save(spec)
    assert path.exists()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["id"] == spec.id
    assert raw["title"] == spec.title


def test_save_creates_specs_dir(tmp_path):
    mgr = _make_manager(tmp_path)
    assert not mgr.specs_dir.exists()
    mgr.save(_sample_spec())
    assert mgr.specs_dir.exists()


def test_get_existing(tmp_path):
    mgr = _make_manager(tmp_path)
    spec = _sample_spec()
    mgr.save(spec)
    loaded = mgr.get(spec.id)
    assert loaded is not None
    assert loaded.id == spec.id
    assert loaded.purpose == spec.purpose


def test_get_not_found(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.get("nao-existe") is None


def test_get_corrupted_yaml(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.specs_dir.mkdir(parents=True)
    (mgr.specs_dir / "broken.yaml").write_text(":: não é yaml válido ::", encoding="utf-8")
    assert mgr.get("broken") is None


# ─── SpecManager.list_specs ───────────────────────────────────────────────────


def test_list_specs_empty_dir(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.list_specs() == []


def test_list_specs_no_dir(tmp_path):
    mgr = SpecManager(specs_dir=tmp_path / "inexistente")
    assert mgr.list_specs() == []


def test_list_specs_multiple(tmp_path):
    mgr = _make_manager(tmp_path)
    for sid in ["alpha", "beta", "gamma"]:
        mgr.save(_sample_spec(sid))
    specs = mgr.list_specs()
    ids = {s.id for s in specs}
    assert ids == {"alpha", "beta", "gamma"}


def test_list_specs_skips_corrupted(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.specs_dir.mkdir(parents=True)
    mgr.save(_sample_spec("valido"))
    (mgr.specs_dir / "corrompido.yaml").write_text("not: valid: yaml:", encoding="utf-8")
    specs = mgr.list_specs()
    assert len(specs) == 1
    assert specs[0].id == "valido"


# ─── SpecManager.delete ───────────────────────────────────────────────────────


def test_delete_existing(tmp_path):
    mgr = _make_manager(tmp_path)
    spec = _sample_spec()
    mgr.save(spec)
    assert mgr.delete(spec.id) is True
    assert mgr.get(spec.id) is None


def test_delete_not_found(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.delete("nao-existe") is False


# ─── SpecManager.find_relevant ────────────────────────────────────────────────


def test_find_relevant_by_title(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save(Spec(id="auth-login", title="Login Auth", purpose="Gerencia autenticação."))
    mgr.save(Spec(id="dashboard", title="Dashboard", purpose="Exibe métricas."))
    results = mgr.find_relevant("login")
    assert len(results) == 1
    assert results[0].id == "auth-login"


def test_find_relevant_by_purpose(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save(Spec(id="cache", title="Cache", purpose="Armazena resultados de queries."))
    mgr.save(Spec(id="log", title="Logger", purpose="Registra eventos do sistema."))
    results = mgr.find_relevant("queries armazena")
    assert results[0].id == "cache"


def test_find_relevant_skips_deprecated(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save(Spec(id="old-feat", title="Old Feature", purpose="Faz X", status="deprecated"))
    mgr.save(Spec(id="new-feat", title="New Feature", purpose="Faz X melhorado", status="approved"))
    results = mgr.find_relevant("faz x")
    ids = [r.id for r in results]
    assert "old-feat" not in ids
    assert "new-feat" in ids


def test_find_relevant_max_results(tmp_path):
    mgr = _make_manager(tmp_path)
    for i in range(10):
        mgr.save(Spec(id=f"spec-{i:02d}", title=f"Spec {i}", purpose="faz algo interessante"))
    results = mgr.find_relevant("algo interessante", max_results=3)
    assert len(results) <= 3


def test_find_relevant_no_match(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save(Spec(id="xpto", title="Xpto", purpose="Faz coisas."))
    results = mgr.find_relevant("zzzz nada")
    assert results == []


# ─── SpecManager.specs_context ───────────────────────────────────────────────


def test_specs_context_empty(tmp_path):
    mgr = _make_manager(tmp_path)
    assert mgr.specs_context() == ""


def test_specs_context_only_approved(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save(_sample_spec("draft-spec", status="draft"))
    mgr.save(_sample_spec("approved-spec", status="approved"))
    mgr.save(_sample_spec("impl-spec", status="implemented"))
    ctx = mgr.specs_context()
    assert "approved-spec" in ctx
    assert "impl-spec" in ctx
    assert "draft-spec" not in ctx


def test_specs_context_with_query(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.save(Spec(id="auth", title="Auth", purpose="Gerencia login e tokens."))
    mgr.save(Spec(id="billing", title="Billing", purpose="Cobra assinaturas."))
    ctx = mgr.specs_context(query="login tokens")
    assert "auth" in ctx
    assert "billing" not in ctx


# ─── wizard_auto_spec ────────────────────────────────────────────────────────


def test_wizard_auto_spec_saves_spec(tmp_path):
    """wizard_auto_spec deve salvar spec quando modelo retorna JSON válido."""
    from bauer.spec_wizard import wizard_auto_spec

    mgr = _make_manager(tmp_path)
    mock_json = {
        "purpose": "Gerencia autenticação de usuários.",
        "behavior": ["Tokens expiram em 24h.", "Senhas são hashadas com bcrypt."],
        "acceptance_criteria": [
            "Given credenciais válidas, When login, Then retorna JWT.",
        ],
        "interface": {
            "inputs": [{"name": "email", "type": "str", "description": "email", "required": True}],
            "outputs": [{"name": "token", "type": "str", "description": "JWT"}],
        },
    }

    with (
        patch("bauer.spec_wizard._call_model_for_spec", return_value=mock_json),
        patch("bauer.spec_wizard.Prompt.ask", return_value="salvar"),
        patch("bauer.spec_wizard.console"),
    ):
        result = wizard_auto_spec("User Authentication", "Login e registro", mgr)

    assert result is not None
    assert result.purpose == mock_json["purpose"]
    assert result.behavior == mock_json["behavior"]
    assert mgr.get(result.id) is not None


def test_wizard_auto_spec_cancel(tmp_path):
    """wizard_auto_spec deve retornar None quando usuário cancela."""
    from bauer.spec_wizard import wizard_auto_spec

    mgr = _make_manager(tmp_path)
    mock_json = {"purpose": "x", "behavior": [], "acceptance_criteria": [], "interface": {}}

    with (
        patch("bauer.spec_wizard._call_model_for_spec", return_value=mock_json),
        patch("bauer.spec_wizard.Prompt.ask", return_value="cancelar"),
        patch("bauer.spec_wizard.console"),
    ):
        result = wizard_auto_spec("Titulo", "Desc", mgr)

    assert result is None
    assert mgr.list_specs() == []


def test_wizard_auto_spec_model_failure_fallback(tmp_path):
    """Se modelo falha, wizard_auto_spec cai no wizard manual."""
    from bauer.spec_wizard import wizard_auto_spec

    mgr = _make_manager(tmp_path)
    fallback_spec = _sample_spec("fallback-spec")

    with (
        patch("bauer.spec_wizard._call_model_for_spec", return_value=None),
        patch("bauer.spec_wizard.wizard_create_spec", return_value=fallback_spec) as mock_wizard,
        patch("bauer.spec_wizard.console"),
    ):
        result = wizard_auto_spec("Titulo", "Desc", mgr)

    mock_wizard.assert_called_once()
    assert result is fallback_spec


def test_wizard_auto_spec_resolves_id_conflict(tmp_path):
    """wizard_auto_spec deve gerar ID único se já existir spec com mesmo nome."""
    from bauer.spec_wizard import wizard_auto_spec

    mgr = _make_manager(tmp_path)
    # Salva spec que vai conflitar
    mgr.save(_sample_spec("user-authentication"))

    mock_json = {"purpose": "x", "behavior": [], "acceptance_criteria": [], "interface": {}}

    with (
        patch("bauer.spec_wizard._call_model_for_spec", return_value=mock_json),
        patch("bauer.spec_wizard.Prompt.ask", return_value="salvar"),
        patch("bauer.spec_wizard.console"),
    ):
        result = wizard_auto_spec("User Authentication", "desc", mgr)

    assert result is not None
    assert result.id != "user-authentication"  # ID resolvido


def _patch_model_call(response_text: str):
    """Context manager helper: mocka OllamaClient e load_config para _call_model_for_spec."""
    from contextlib import ExitStack

    mock_instance = MagicMock()
    mock_instance.chat_stream.return_value = iter([response_text])
    mock_class = MagicMock(return_value=mock_instance)
    cfg = {"model": "phi4-mini", "base_url": "http://localhost:11434"}

    stack = ExitStack()
    # Patchamos nos módulos de origem — a função faz "from .xxx import yyy" localmente,
    # que busca o atributo no módulo cached em sys.modules
    stack.enter_context(patch("bauer.ollama_client.OllamaClient", mock_class))
    stack.enter_context(patch("bauer.config_loader.load_config", return_value=cfg))
    return stack


def test_call_model_for_spec_parse_plain_json():
    """_call_model_for_spec deve parsear JSON puro retornado pelo modelo."""
    import json
    from bauer.spec_wizard import _call_model_for_spec

    response_json = json.dumps({
        "purpose": "Faz algo.",
        "behavior": ["Regra 1."],
        "acceptance_criteria": ["AC 1."],
        "interface": {},
    })

    with _patch_model_call(response_json):
        result = _call_model_for_spec("Titulo", "Descricao")

    assert result is not None
    assert result["purpose"] == "Faz algo."
    assert result["behavior"] == ["Regra 1."]


def test_call_model_for_spec_parse_markdown_block():
    """_call_model_for_spec deve extrair JSON de bloco markdown."""
    import json
    from bauer.spec_wizard import _call_model_for_spec

    data = {"purpose": "P.", "behavior": [], "acceptance_criteria": [], "interface": {}}
    response = f"Aqui esta:\n```json\n{json.dumps(data)}\n```\nFim."

    with _patch_model_call(response):
        result = _call_model_for_spec("T", "D")

    assert result is not None
    assert result["purpose"] == "P."
