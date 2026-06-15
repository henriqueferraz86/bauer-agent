"""Tests for interactive wizards: agent_wizard, spec_wizard, model_switcher."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from bauer.agent_wizard import (
    _collect_multiline,
    _pick_multi,
    _pick_numbered,
    wizard_create_agent,
    wizard_create_task,
    wizard_orchestrate,
)
from bauer.model_switcher import (
    _ask_api_key,
    _patch_config,
    _pick_from_list,
    _pick_ollama_model,
    _read_env,
    _write_env_key,
    run_model_switcher,
)
from bauer.spec_wizard import (
    _call_model_for_spec,
    _slugify,
    wizard_auto_spec,
    wizard_create_spec,
)


# ══════════════════════════════════════════════════════════════════════════════
# agent_wizard — _pick_numbered
# ══════════════════════════════════════════════════════════════════════════════


class TestPickNumbered:
    ITEMS = [("a", "Item A"), ("b", "Item B"), ("c", "Item C")]

    def test_pick_first(self):
        with patch("rich.prompt.Prompt.ask", return_value="1"):
            assert _pick_numbered(self.ITEMS, "T") == "a"

    def test_pick_second(self):
        with patch("rich.prompt.Prompt.ask", return_value="2"):
            assert _pick_numbered(self.ITEMS, "T") == "b"

    def test_pick_by_name(self):
        with patch("rich.prompt.Prompt.ask", return_value="c"):
            assert _pick_numbered(self.ITEMS, "T") == "c"

    def test_empty_allow_empty_returns_none(self):
        with patch("rich.prompt.Prompt.ask", return_value=""):
            assert _pick_numbered(self.ITEMS, "T", allow_empty=True) is None

    def test_out_of_range_returns_raw(self):
        with patch("rich.prompt.Prompt.ask", return_value="99"):
            assert _pick_numbered(self.ITEMS, "T") == "99"

    def test_invalid_non_numeric_returns_raw(self):
        with patch("rich.prompt.Prompt.ask", return_value="xyz"):
            assert _pick_numbered(self.ITEMS, "T") == "xyz"


# ══════════════════════════════════════════════════════════════════════════════
# agent_wizard — _pick_multi
# ══════════════════════════════════════════════════════════════════════════════


class TestPickMulti:
    def test_confirm_immediately(self):
        with patch("rich.prompt.Prompt.ask", return_value=""):
            result = _pick_multi(["t1", "t2"], ["t1"], "Tools")
        assert result == ["t1"]

    def test_toggle_adds_item(self):
        with patch("rich.prompt.Prompt.ask", side_effect=["2", ""]):
            result = _pick_multi(["t1", "t2"], ["t1"], "Tools")
        assert "t2" in result

    def test_toggle_removes_item(self):
        with patch("rich.prompt.Prompt.ask", side_effect=["1", ""]):
            result = _pick_multi(["t1", "t2"], ["t1"], "Tools")
        assert "t1" not in result

    def test_invalid_input_loops(self):
        with patch("rich.prompt.Prompt.ask", side_effect=["abc", ""]):
            result = _pick_multi(["t1"], [], "Tools")
        assert result == []


# ══════════════════════════════════════════════════════════════════════════════
# agent_wizard — _collect_multiline
# ══════════════════════════════════════════════════════════════════════════════


class TestCollectMultiline:
    def test_two_lines(self):
        with patch("builtins.input", side_effect=["Line one", "Line two", ""]):
            result = _collect_multiline()
        assert "Line one" in result and "Line two" in result

    def test_single_line(self):
        with patch("builtins.input", side_effect=["Single line here", ""]):
            result = _collect_multiline()
        assert result == "Single line here"


# ══════════════════════════════════════════════════════════════════════════════
# agent_wizard — wizard_create_agent
# ══════════════════════════════════════════════════════════════════════════════


def _make_registry(tmp_path: Path):
    from bauer.agent_registry import AgentRegistry
    f = tmp_path / "agents.yaml"
    f.write_text("{}", encoding="utf-8")
    return AgentRegistry(path=f)


class TestWizardCreateAgent:
    def test_create_with_persona(self, tmp_path):
        registry = _make_registry(tmp_path)
        # Prompt: name, pick persona "1", description, _pick_multi confirm ""
        # Confirm: use persona system, use default model, create
        with patch("rich.prompt.Prompt.ask", side_effect=["test-agent", "1", "My Agent", ""]), \
             patch("rich.prompt.Confirm.ask", side_effect=[True, True, True]):
            agent = wizard_create_agent(registry, config_model="phi4")
        assert agent is not None
        assert agent.name == "test-agent"

    def test_cancel_at_confirm(self, tmp_path):
        registry = _make_registry(tmp_path)
        with patch("rich.prompt.Prompt.ask", side_effect=["test-agent", "1", "My Agent", ""]), \
             patch("rich.prompt.Confirm.ask", side_effect=[True, True, False]):
            agent = wizard_create_agent(registry)
        assert agent is None

    def test_no_persona_uses_multiline(self, tmp_path):
        registry = _make_registry(tmp_path)
        # "" skips persona (allow_empty=True), then description, then _pick_multi confirm
        with patch("rich.prompt.Prompt.ask", side_effect=["agent-two", "", "Desc Two", ""]), \
             patch("rich.prompt.Confirm.ask", side_effect=[True, True]), \
             patch("builtins.input", side_effect=["You are a helpful assistant.", ""]):
            agent = wizard_create_agent(registry)
        assert agent is not None
        assert agent.name == "agent-two"

    def test_custom_model(self, tmp_path):
        registry = _make_registry(tmp_path)
        # Prompt: name, persona, desc, model name, provider (empty), _pick_multi confirm
        with patch("rich.prompt.Prompt.ask", side_effect=["agent3", "1", "Desc3", "my-llm", "", ""]), \
             patch("rich.prompt.Confirm.ask", side_effect=[True, False, True]):
            # Confirm: use persona system=True, use_default_model=False, create=True
            agent = wizard_create_agent(registry, config_model="phi4")
        assert agent is not None
        assert agent.model == "my-llm"

    def test_overwrite_existing(self, tmp_path):
        from bauer.agent_registry import AgentDef
        registry = _make_registry(tmp_path)
        registry.save(AgentDef(name="old-agent", description="Old", system="Old sys"))
        # First ask name "old-agent" → exists → Confirm overwrite=True
        # Then persona "1", desc, _pick_multi confirm ""
        with patch("rich.prompt.Prompt.ask", side_effect=["old-agent", "1", "New Desc", ""]), \
             patch("rich.prompt.Confirm.ask", side_effect=[True, True, True, True]):
            agent = wizard_create_agent(registry)
        assert agent is not None

    def test_invalid_name_retries(self, tmp_path):
        registry = _make_registry(tmp_path)
        # First name is invalid (too short "x"), second is valid
        with patch("rich.prompt.Prompt.ask", side_effect=["x", "valid-name", "1", "Desc", ""]), \
             patch("rich.prompt.Confirm.ask", side_effect=[True, True, True]):
            agent = wizard_create_agent(registry)
        assert agent is not None
        assert agent.name == "valid-name"


# ══════════════════════════════════════════════════════════════════════════════
# agent_wizard — wizard_create_task
# ══════════════════════════════════════════════════════════════════════════════


class TestWizardCreateTask:
    def _patch_deps(self):
        """Patch SpecManager and AgentRegistry to return empty lists."""
        mock_sm = MagicMock()
        mock_sm.list_specs.return_value = []
        sm_patch = patch("bauer.spec_manager.SpecManager", return_value=mock_sm)

        mock_reg = MagicMock()
        mock_reg.list_agents.return_value = []
        reg_patch = patch("bauer.agent_wizard.AgentRegistry", return_value=mock_reg)

        auto_patch = patch("bauer.spec_wizard.wizard_auto_spec", return_value=None)
        return sm_patch, reg_patch, auto_patch

    def test_basic_create(self):
        sm_p, reg_p, auto_p = self._patch_deps()
        with sm_p, reg_p, auto_p:
            with patch("rich.prompt.Prompt.ask", side_effect=["My Task", "", "1"]), \
                 patch("rich.prompt.Confirm.ask", return_value=True):
                result = wizard_create_task()
        assert result is not None
        assert result["title"] == "My Task"
        assert result["priority"] == "alta"

    def test_empty_title_cancels(self):
        with patch("rich.prompt.Prompt.ask", return_value=""):
            result = wizard_create_task()
        assert result is None

    def test_cancel_at_confirm(self):
        sm_p, reg_p, auto_p = self._patch_deps()
        with sm_p, reg_p, auto_p:
            with patch("rich.prompt.Prompt.ask", side_effect=["Task B", "Some desc", "2"]), \
                 patch("rich.prompt.Confirm.ask", return_value=False):
                result = wizard_create_task()
        assert result is None

    def test_picks_existing_spec(self):
        mock_spec = MagicMock()
        mock_spec.id = "spec-id-1"
        mock_spec.status = "approved"
        mock_spec.purpose = "Does things"

        mock_sm = MagicMock()
        mock_sm.list_specs.return_value = [mock_spec]
        sm_p = patch("bauer.spec_manager.SpecManager", return_value=mock_sm)

        mock_reg = MagicMock()
        mock_reg.list_agents.return_value = []
        reg_p = patch("bauer.agent_wizard.AgentRegistry", return_value=mock_reg)

        # Prompt: title, desc, priority "1", spec pick "1" (first spec)
        with sm_p, reg_p:
            with patch("rich.prompt.Prompt.ask", side_effect=["My Task", "", "1", "1"]), \
                 patch("rich.prompt.Confirm.ask", return_value=True):
                result = wizard_create_task()

        assert result is not None
        assert result["spec_id"] == "spec-id-1"


# ══════════════════════════════════════════════════════════════════════════════
# agent_wizard — wizard_orchestrate
# ══════════════════════════════════════════════════════════════════════════════


class TestWizardOrchestrate:
    def _reg_patch(self):
        mock_reg = MagicMock()
        mock_reg.list_agents.return_value = []
        return patch("bauer.agent_wizard.AgentRegistry", return_value=mock_reg)

    def test_automatic_mode(self):
        with self._reg_patch():
            with patch("rich.prompt.Prompt.ask", side_effect=["Do something", "1"]), \
                 patch("rich.prompt.Confirm.ask", side_effect=[False, True]):
                result = wizard_orchestrate()
        assert result is not None
        assert result["task"] == "Do something"
        assert not result["interactive"]
        assert not result["resume"]

    def test_interactive_mode(self):
        with self._reg_patch():
            with patch("rich.prompt.Prompt.ask", side_effect=["Do Y", "2"]), \
                 patch("rich.prompt.Confirm.ask", side_effect=[False, True]):
                result = wizard_orchestrate()
        assert result is not None
        assert result["interactive"] is True

    def test_cancel_at_execute(self):
        with self._reg_patch():
            with patch("rich.prompt.Prompt.ask", side_effect=["Do Z", "1"]), \
                 patch("rich.prompt.Confirm.ask", side_effect=[False, False]):
                result = wizard_orchestrate()
        assert result is None

    def test_empty_task_cancels(self):
        with patch("rich.prompt.Prompt.ask", return_value=""):
            result = wizard_orchestrate()
        assert result is None

    def test_resume_true(self):
        with self._reg_patch():
            with patch("rich.prompt.Prompt.ask", side_effect=["Big task", "1"]), \
                 patch("rich.prompt.Confirm.ask", side_effect=[True, True]):
                result = wizard_orchestrate()
        assert result is not None
        assert result["resume"] is True


# ══════════════════════════════════════════════════════════════════════════════
# spec_wizard — _slugify
# ══════════════════════════════════════════════════════════════════════════════


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert _slugify("Feature #1!") == "feature-1"

    def test_empty_returns_default(self):
        assert _slugify("") == "meu-spec"

    def test_truncates_at_40(self):
        assert len(_slugify("a" * 60)) <= 40

    def test_already_slug(self):
        assert _slugify("my-slug-123") == "my-slug-123"


# ══════════════════════════════════════════════════════════════════════════════
# spec_wizard — wizard_create_spec
# ══════════════════════════════════════════════════════════════════════════════


class TestWizardCreateSpec:
    def test_full_flow_no_interface(self, tmp_path):
        from bauer.spec_manager import SpecManager
        mgr = SpecManager(specs_dir=tmp_path / "specs")

        # Prompt: ID, title, purpose, beh1, "", ac1, "", link"", status"1"
        prompts = ["my-spec", "My Title", "Purpose here",
                   "Rule one", "",
                   "AC one", "",
                   "",
                   "1"]
        # Confirm: inputs=No, outputs=No, save=Yes, task=No
        confirms = [False, False, True, False]

        with patch("rich.prompt.Prompt.ask", side_effect=prompts), \
             patch("rich.prompt.Confirm.ask", side_effect=confirms):
            spec = wizard_create_spec(mgr)

        assert spec is not None
        assert spec.id == "my-spec"
        assert spec.title == "My Title"
        assert "Rule one" in spec.behavior
        assert "AC one" in spec.acceptance_criteria

    def test_cancel_at_save(self, tmp_path):
        from bauer.spec_manager import SpecManager
        mgr = SpecManager(specs_dir=tmp_path / "specs")

        # ID, title, purpose, behavior"", ACs"", linked"", status
        prompts = ["cancel-spec", "Title", "Purpose", "", "", "", "1"]
        confirms = [False, False, False]  # skip inputs/outputs, CANCEL save

        with patch("rich.prompt.Prompt.ask", side_effect=prompts), \
             patch("rich.prompt.Confirm.ask", side_effect=confirms):
            spec = wizard_create_spec(mgr)

        assert spec is None

    def test_with_inputs_and_outputs(self, tmp_path):
        from bauer.spec_manager import SpecManager
        mgr = SpecManager(specs_dir=tmp_path / "specs")

        prompts = [
            "iface-spec", "Iface Spec", "Interface test",
            "Rule A", "",           # behavior
            "param1", "str", "First param",  # input fields
            "",                     # end inputs loop
            "result", "dict", "Output result",  # output fields
            "",                     # end outputs loop
            "",                     # ACs end
            "",                     # linked end
            "1",                    # status
        ]
        # Confirm: inputs=Yes, required=Yes, outputs=Yes, save=Yes, task=No
        confirms = [True, True, True, True, False]

        with patch("rich.prompt.Prompt.ask", side_effect=prompts), \
             patch("rich.prompt.Confirm.ask", side_effect=confirms):
            spec = wizard_create_spec(mgr)

        assert spec is not None
        assert "inputs" in spec.interface
        assert "outputs" in spec.interface

    def test_overwrite_existing_id(self, tmp_path):
        from bauer.spec_manager import Spec, SpecManager
        mgr = SpecManager(specs_dir=tmp_path / "specs")
        mgr.save(Spec(id="exists", title="Old"))

        prompts = ["exists", "New Title", "New purpose", "", "", "", "2"]
        # Confirm: overwrite=Yes, inputs=No, outputs=No, save=Yes, task=No
        confirms = [True, False, False, True, False]

        with patch("rich.prompt.Prompt.ask", side_effect=prompts), \
             patch("rich.prompt.Confirm.ask", side_effect=confirms):
            spec = wizard_create_spec(mgr)

        assert spec is not None
        assert spec.title == "New Title"

    def test_invalid_id_retries(self, tmp_path):
        from bauer.spec_manager import SpecManager
        mgr = SpecManager(specs_dir=tmp_path / "specs")

        # "x" is invalid (too short), then "valid-spec" is ok
        prompts = ["x", "valid-spec", "Title", "Purpose", "", "", "", "1"]
        confirms = [False, False, True, False]

        with patch("rich.prompt.Prompt.ask", side_effect=prompts), \
             patch("rich.prompt.Confirm.ask", side_effect=confirms):
            spec = wizard_create_spec(mgr)

        assert spec is not None
        assert spec.id == "valid-spec"


# ══════════════════════════════════════════════════════════════════════════════
# spec_wizard — wizard_auto_spec
# ══════════════════════════════════════════════════════════════════════════════


FAKE_SPEC_DATA = {
    "purpose": "Does something useful",
    "behavior": ["Rule 1", "Rule 2"],
    "acceptance_criteria": ["Given X, When Y, Then Z"],
    "interface": {
        "inputs": [{"name": "x", "type": "str", "description": "input", "required": True}],
        "outputs": [{"name": "y", "type": "str", "description": "output"}],
    },
}


class TestWizardAutoSpec:
    def test_save_action(self, tmp_path):
        from bauer.spec_manager import SpecManager
        mgr = SpecManager(specs_dir=tmp_path / "specs")

        with patch("bauer.spec_wizard._call_model_for_spec", return_value=FAKE_SPEC_DATA), \
             patch("rich.prompt.Prompt.ask", return_value="salvar"):
            spec = wizard_auto_spec("Test Feature", "Does testing", mgr)

        assert spec is not None
        assert spec.title == "Test Feature"
        assert spec.purpose == "Does something useful"

    def test_cancel_action(self, tmp_path):
        from bauer.spec_manager import SpecManager
        mgr = SpecManager(specs_dir=tmp_path / "specs")

        with patch("bauer.spec_wizard._call_model_for_spec", return_value=FAKE_SPEC_DATA), \
             patch("rich.prompt.Prompt.ask", return_value="cancelar"):
            spec = wizard_auto_spec("My Title", "", mgr)

        assert spec is None

    def test_edit_action_opens_manual(self, tmp_path):
        from bauer.spec_manager import SpecManager
        mgr = SpecManager(specs_dir=tmp_path / "specs")
        manual_spec = MagicMock()
        manual_spec.id = "manual-id"

        with patch("bauer.spec_wizard._call_model_for_spec", return_value=FAKE_SPEC_DATA), \
             patch("rich.prompt.Prompt.ask", return_value="editar"), \
             patch("bauer.spec_wizard.wizard_create_spec", return_value=manual_spec):
            spec = wizard_auto_spec("My Title", "", mgr)

        assert spec is manual_spec

    def test_model_none_falls_back_to_manual(self, tmp_path):
        from bauer.spec_manager import SpecManager
        mgr = SpecManager(specs_dir=tmp_path / "specs")
        manual_spec = MagicMock()

        with patch("bauer.spec_wizard._call_model_for_spec", return_value=None), \
             patch("bauer.spec_wizard.wizard_create_spec", return_value=manual_spec):
            spec = wizard_auto_spec("My Title", "desc", mgr)

        assert spec is manual_spec

    def test_duplicate_id_gets_suffix(self, tmp_path):
        from bauer.spec_manager import Spec, SpecManager
        mgr = SpecManager(specs_dir=tmp_path / "specs")
        mgr.save(Spec(id="test-feature", title="Old"))

        with patch("bauer.spec_wizard._call_model_for_spec", return_value=FAKE_SPEC_DATA), \
             patch("rich.prompt.Prompt.ask", return_value="salvar"):
            spec = wizard_auto_spec("Test Feature", "", mgr)

        assert spec is not None
        assert spec.id != "test-feature"
        assert "test-feature" in spec.id  # e.g. test-feature-2


# ══════════════════════════════════════════════════════════════════════════════
# spec_wizard — _call_model_for_spec
# ══════════════════════════════════════════════════════════════════════════════


class TestCallModelForSpec:
    def test_valid_json_response(self):
        fake_json = json.dumps({
            "purpose": "P",
            "behavior": ["B1"],
            "acceptance_criteria": ["AC1"],
            "interface": {}
        })
        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter([fake_json])

        with patch("bauer.config_loader.load_config", return_value={"base_url": "http://localhost:11434", "model": "phi4"}), \
             patch("bauer.ollama_client.OllamaClient", return_value=mock_client):
            result = _call_model_for_spec("Title", "Desc")

        assert result is not None
        assert result["purpose"] == "P"

    def test_json_in_markdown_fences(self):
        fake_json = json.dumps({"purpose": "P", "behavior": [], "acceptance_criteria": [], "interface": {}})
        wrapped = f"```json\n{fake_json}\n```"
        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter([wrapped])

        with patch("bauer.config_loader.load_config", return_value={"base_url": "http://localhost:11434", "model": "phi4"}), \
             patch("bauer.ollama_client.OllamaClient", return_value=mock_client):
            result = _call_model_for_spec("Title", "Desc")

        assert result is not None

    def test_invalid_json_returns_none(self):
        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter(["not valid json !!!"])

        with patch("bauer.config_loader.load_config", return_value={"base_url": "http://localhost:11434", "model": "phi4"}), \
             patch("bauer.ollama_client.OllamaClient", return_value=mock_client):
            result = _call_model_for_spec("Title", "Desc")

        assert result is None

    def test_exception_returns_none(self):
        with patch("bauer.config_loader.load_config", side_effect=Exception("no config")):
            result = _call_model_for_spec("Title", "Desc")
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# model_switcher — utilities
# ══════════════════════════════════════════════════════════════════════════════


class TestReadEnv:
    def test_missing_file(self, tmp_path):
        assert _read_env(tmp_path / ".env") == {}

    def test_basic_values(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("K1=v1\nK2=v2\n", encoding="utf-8")
        r = _read_env(f)
        assert r["K1"] == "v1" and r["K2"] == "v2"

    def test_quoted_values(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text('KEY="quoted"\n', encoding="utf-8")
        assert _read_env(f)["KEY"] == "quoted"

    def test_comments_ignored(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("# comment\nK=val\n", encoding="utf-8")
        r = _read_env(f)
        assert r.get("K") == "val"
        assert len(r) == 1


class TestWriteEnvKey:
    def test_create_new(self, tmp_path):
        f = tmp_path / ".env"
        _write_env_key(f, "MY_KEY", "my_val")
        assert "MY_KEY=my_val" in f.read_text(encoding="utf-8")

    def test_update_existing(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("MY_KEY=old\n", encoding="utf-8")
        _write_env_key(f, "MY_KEY", "new")
        content = f.read_text(encoding="utf-8")
        assert "MY_KEY=new" in content
        assert "old" not in content

    def test_preserves_other_keys(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("OTHER=kept\n", encoding="utf-8")
        _write_env_key(f, "NEW_KEY", "nv")
        content = f.read_text(encoding="utf-8")
        assert "OTHER=kept" in content
        assert "NEW_KEY=nv" in content


class TestPatchConfig:
    def test_update_provider_model(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  provider: ollama\n  name: old\n", encoding="utf-8")
        _patch_config(cfg, "opencode", "new-model")
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["model"]["provider"] == "opencode"
        assert raw["model"]["name"] == "new-model"

    def test_extra_config_section(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("{}\n", encoding="utf-8")
        _patch_config(cfg, "openai", "gpt-4o", extra={"openai": {"host": "https://api.openai.com"}})
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["openai"]["host"] == "https://api.openai.com"


class TestPickFromList:
    ITEMS = [("ollama", "Ollama desc"), ("opencode", "OC desc"), ("custom", "Custom desc")]

    def test_pick_by_number(self):
        with patch("rich.prompt.Prompt.ask", return_value="1"):
            assert _pick_from_list(self.ITEMS, "T") == "ollama"

    def test_pick_by_name(self):
        with patch("rich.prompt.Prompt.ask", return_value="opencode"):
            assert _pick_from_list(self.ITEMS, "T") == "opencode"

    def test_cancel_empty(self):
        with patch("rich.prompt.Prompt.ask", return_value=""):
            assert _pick_from_list(self.ITEMS, "T") is None

    def test_out_of_range_returns_raw(self):
        with patch("rich.prompt.Prompt.ask", return_value="999"):
            assert _pick_from_list(self.ITEMS, "T") == "999"


class TestAskApiKey:
    def test_existing_key_no_prompt(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("MY_KEY=exists\n", encoding="utf-8")
        env_vars, _ = _ask_api_key(f, "MY_KEY", "My Key", "https://example.com")
        assert env_vars == {}

    def test_new_key_saved(self, tmp_path):
        f = tmp_path / ".env"
        with patch("rich.prompt.Prompt.ask", return_value="sk-new123"):
            env_vars, _ = _ask_api_key(f, "MY_KEY", "My Key", "https://example.com")
        assert env_vars.get("MY_KEY") == "sk-new123"

    def test_skip_key_empty(self, tmp_path):
        f = tmp_path / ".env"
        with patch("rich.prompt.Prompt.ask", return_value=""):
            env_vars, _ = _ask_api_key(f, "MY_KEY", "My Key", "https://example.com")
        assert env_vars == {}


class TestPickOllamaModel:
    def test_offline_falls_back_to_manual(self):
        with patch("httpx.get", side_effect=Exception("refused")), \
             patch("rich.prompt.Prompt.ask", return_value="my-local-model"):
            result = _pick_ollama_model("http://localhost:11434")
        assert result == "my-local-model"

    def test_online_with_models(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [
            {"name": "phi4-mini", "size": 2_000_000_000},
            {"name": "llama3", "size": 4_000_000_000},
        ]}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp), \
             patch("rich.prompt.Prompt.ask", return_value="1"):
            result = _pick_ollama_model("http://localhost:11434")
        assert result == "phi4-mini"

    def test_online_no_models_manual(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp), \
             patch("rich.prompt.Prompt.ask", return_value="custom-model"):
            result = _pick_ollama_model("http://localhost:11434")
        assert result == "custom-model"


# ══════════════════════════════════════════════════════════════════════════════
# model_switcher — run_model_switcher (full flows)
# ══════════════════════════════════════════════════════════════════════════════


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  provider: ollama\n  name: phi4-mini\n", encoding="utf-8")
    return cfg


class TestRunModelSwitcher:
    def test_cancel_no_provider(self, tmp_path):
        cfg = _write_config(tmp_path)
        original = cfg.read_text(encoding="utf-8")
        with patch("rich.prompt.Prompt.ask", return_value=""):
            run_model_switcher(cfg)
        assert cfg.read_text(encoding="utf-8") == original

    def test_opencode_path(self, tmp_path):
        cfg = _write_config(tmp_path)
        # provider "2"=opencode, model "1"=deepseek-v4-flash-free
        with patch("rich.prompt.Prompt.ask", side_effect=["2", "1"]):
            run_model_switcher(cfg)
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["model"]["provider"] == "opencode"
        assert raw["model"]["name"] == "deepseek-v4-flash-free"

    def test_opencode_cancel_model(self, tmp_path):
        """Cancelar modelo volta à lista de providers; cancelar de novo sai."""
        cfg = _write_config(tmp_path)
        original = cfg.read_text(encoding="utf-8")
        # opencode → "" (volta à lista) → "" (sai); config inalterado
        with patch("rich.prompt.Prompt.ask", side_effect=["opencode", "", ""]):
            run_model_switcher(cfg)
        assert cfg.read_text(encoding="utf-8") == original

    def test_custom_no_key(self, tmp_path):
        cfg = _write_config(tmp_path)
        # Seleciona por NOME ("custom") — robusto a reordenação do menu.
        with patch("rich.prompt.Prompt.ask", side_effect=["custom", "http://localhost:1234", "local-llm"]), \
             patch("rich.prompt.Confirm.ask", return_value=False):
            run_model_switcher(cfg)
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["model"]["name"] == "local-llm"

    def test_custom_with_key(self, tmp_path):
        cfg = _write_config(tmp_path)
        with patch("rich.prompt.Prompt.ask", side_effect=["custom", "http://srv:8080", "custom-llm", "secret-key"]), \
             patch("rich.prompt.Confirm.ask", return_value=True):
            run_model_switcher(cfg)
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["model"]["name"] == "custom-llm"
        env_file = cfg.parent / ".env"
        assert "secret-key" in env_file.read_text(encoding="utf-8")

    def test_custom_empty_model_cancels(self, tmp_path):
        cfg = _write_config(tmp_path)
        original = cfg.read_text(encoding="utf-8")
        # custom → host → modelo vazio (volta à lista) → "" (sai); inalterado
        with patch("rich.prompt.Prompt.ask", side_effect=["custom", "http://localhost:1234", "", ""]), \
             patch("rich.prompt.Confirm.ask", return_value=False):
            run_model_switcher(cfg)
        assert cfg.read_text(encoding="utf-8") == original

    def test_ollama_offline_manual_input(self, tmp_path):
        cfg = _write_config(tmp_path)
        # provider "1"=ollama, Ollama offline → manual entry
        with patch("rich.prompt.Prompt.ask", side_effect=["1", "qwen2.5:14b"]), \
             patch("httpx.get", side_effect=Exception("offline")):
            run_model_switcher(cfg)
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["model"]["name"] == "qwen2.5:14b"

    def test_openrouter_with_existing_key(self, tmp_path):
        cfg = _write_config(tmp_path)
        env_file = cfg.parent / ".env"
        env_file.write_text("OPENROUTER_API_KEY=sk-or-test\n", encoding="utf-8")
        # Seleciona por NOME ("openrouter"), model "1"=openai/gpt-4o-mini
        with patch("rich.prompt.Prompt.ask", side_effect=["openrouter", "1"]):
            run_model_switcher(cfg)
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["model"]["provider"] == "openrouter"

    def test_openai_with_existing_key(self, tmp_path):
        cfg = _write_config(tmp_path)
        env_file = cfg.parent / ".env"
        env_file.write_text("OPENAI_API_KEY=sk-existing\n", encoding="utf-8")
        # "openai-api" (fluxo de API key) mapeia para provider interno "openai"
        with patch("rich.prompt.Prompt.ask", side_effect=["openai-api", "1"]):
            run_model_switcher(cfg)
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["model"]["provider"] == "openai"

    def test_groq_with_existing_key(self, tmp_path):
        cfg = _write_config(tmp_path)
        env_file = cfg.parent / ".env"
        env_file.write_text("GROQ_API_KEY=groq-key\n", encoding="utf-8")
        # Seleciona por NOME ("groq"), model "1"=llama-3.3-70b-versatile
        with patch("rich.prompt.Prompt.ask", side_effect=["groq", "1"]):
            run_model_switcher(cfg)
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert raw["model"]["provider"] == "groq"
        assert raw["model"]["name"] == "llama-3.3-70b-versatile"

    def test_openrouter_cancel_model(self, tmp_path):
        cfg = _write_config(tmp_path)
        original = cfg.read_text(encoding="utf-8")
        # openrouter → "" (volta à lista) → "" (sai); config inalterado
        with patch("rich.prompt.Prompt.ask", side_effect=["openrouter", "", ""]):
            run_model_switcher(cfg)
        assert cfg.read_text(encoding="utf-8") == original
