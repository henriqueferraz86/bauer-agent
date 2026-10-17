from bauer.server import _is_text_approval_confirmation


def test_only_unambiguous_confirmation_tokens_are_decisions():
    assert _is_text_approval_confirmation("confirmo")
    assert _is_text_approval_confirmation("  SIM, APROVO  ")
    assert _is_text_approval_confirmation("pode executar")
    assert not _is_text_approval_confirmation("sim")
    assert not _is_text_approval_confirmation("confirmo, mas verifique a porta")
    assert not _is_text_approval_confirmation("faça o deploy")
