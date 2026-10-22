"""Cancellation remains responsive when every model worker is busy."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bauer.telegram_bridge import TelegramBridge


def update(text="/cancelar", *, owner=42, private=True):
    return {"message": {"text": text, "chat": {"id": 42, "type": "private" if private else "group"},
                        "from": {"id": owner}}}


def bridge(tmp_path):
    backend = SimpleNamespace(cancel_channel_execution=Mock(return_value="Cancelamento solicitado"))
    result = TelegramBridge(token="fake", backend=backend, allowed_users=[42], state_dir=tmp_path)
    result.send_text = Mock()
    result._executor.submit = Mock(side_effect=AssertionError("A cancellation cannot wait for a model worker"))
    return result


@pytest.mark.parametrize("text,run_id", [("/cancelar", None), ("/cancelar run-123", "run-123"),
                                          ("/cancelar@BauerBot run-123", "run-123")])
def test_cancel_bypasses_busy_worker_queue(tmp_path, text, run_id):
    service = bridge(tmp_path)
    try:
        service._handle_update(update(text))
        msg, actual_id = service.backend.cancel_channel_execution.call_args.args
        assert actual_id == run_id
        assert msg.user_id == msg.chat_id == "42"
        service.send_text.assert_called_once_with("42", "Cancelamento solicitado")
        service._executor.submit.assert_not_called()
    finally:
        service.stop()


@pytest.mark.parametrize("payload", [update(owner=99), update(private=False), update("/cancelar a b")])
def test_cancel_rejects_unauthorized_group_and_malformed_command(tmp_path, payload):
    service = bridge(tmp_path)
    try:
        service._handle_update(payload)
        service.backend.cancel_channel_execution.assert_not_called()
    finally:
        service.stop()


def test_cancel_error_does_not_echo_internal_detail(tmp_path):
    service = bridge(tmp_path)
    service.backend.cancel_channel_execution.side_effect = RuntimeError("PRIVATE_SECRET")
    try:
        service._handle_update(update())
        assert "PRIVATE_SECRET" not in service.send_text.call_args.args[1]
    finally:
        service.stop()
