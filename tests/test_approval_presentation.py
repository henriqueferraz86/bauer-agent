from types import SimpleNamespace

import pytest

from bauer.approval_presentation import approval_summary


@pytest.mark.parametrize("command,label", [
    ("docker compose up --password secret", "Executar Docker Compose: up"),
    ("docker compose -f private.yml up", "Executar Docker Compose"),
    ("docker logs private-container", "Executar Docker: logs"),
    ('python -c "print(\'sensitive\')"', "Executar código Python"),
    ("unknown sensitive", "Executar comando"),
    ("docker 'secret up'", "Executar Docker"),
    ('docker "', "Executar comando"),
    (None, "Executar comando"),
])
def test_summary_uses_fixed_labels_never_arguments(command, label):
    assert approval_summary(SimpleNamespace(tool_name="run_command", payload={"command": command})) == label
