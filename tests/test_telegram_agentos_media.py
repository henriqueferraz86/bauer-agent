import httpx
import pytest

from bauer.telegram_agentos_media import AgentOSMediaError, MAX_DOCUMENT_BYTES, extract_text_document


def message(data: bytes, **extra):
    return {"document": {"file_id": "id-1", "file_name": "nota.txt", "mime_type": "text/plain",
                         "file_size": len(data), **extra}}


def extract(data: bytes, *, msg=None, info=None, handler=None):
    request_handler = handler or (lambda request: httpx.Response(200, stream=httpx.ByteStream(data)))
    with httpx.Client(transport=httpx.MockTransport(request_handler)) as client:
        return extract_text_document(
            msg or message(data), get_file=lambda _: info or {"file_path": "documents/nota.txt", "file_size": len(data)},
            http_client=client, bot_token="123:test_bot_token",
        )


def test_text_document_to_inline_text_without_filename_or_token():
    result = extract("Olá mundo".encode(), msg={**message("Olá mundo".encode()), "caption": "Resuma"})
    assert result == "[Documento de texto fornecido pelo usuário]\nLegenda: Resuma\nOlá mundo"
    assert "nota.txt" not in result and "123:test_bot_token" not in result and "documents/" not in result


@pytest.mark.parametrize("name,mime", [
    ("../../segredo.txt", "text/plain"), ("C:\\keys.txt", "text/plain"),
    (".env.txt", "text/plain"), ("file.pdf", "text/plain"), ("file.txt", "application/pdf"),
])
def test_invalid_filename_or_type_never_downloads(name, mime):
    called = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: called.append(request))) as client:
        with pytest.raises(AgentOSMediaError):
            extract_text_document(message(b"x", file_name=name, mime_type=mime),
                                  get_file=lambda _: called.append("get_file"), http_client=client,
                                  bot_token="123:test")
    assert not called


@pytest.mark.parametrize("remote_path", ["../escape", "/etc/passwd", "foo/../secret", "https://host/test"])
def test_path_traversal_or_external_host_is_refused(remote_path):
    with pytest.raises(AgentOSMediaError):
        extract(b"x", info={"file_path": remote_path, "file_size": 1})


def test_rejects_secret_binary_empty_and_oversized():
    for data in (b"\x00binary", b"\xff", b"   ", b"-----BEGIN PRIVATE KEY-----", b"x" * (MAX_DOCUMENT_BYTES + 1)):
        with pytest.raises(AgentOSMediaError):
            extract(data)


def test_rejects_discrepant_or_truncated_file_size():
    with pytest.raises(AgentOSMediaError):
        extract(b"ab", info={"file_path": "documents/file.txt", "file_size": 3})
    with pytest.raises(AgentOSMediaError):
        extract(b"ab", msg=message(b"abc"), info={"file_path": "documents/file.txt", "file_size": 3})


def test_rejects_stream_growing_beyond_declared_limit():
    class LargeResponse(httpx.SyncByteStream):
        def __iter__(self):
            yield b"a"
            yield b"b"

    with pytest.raises(AgentOSMediaError):
        extract(b"a", handler=lambda request: httpx.Response(200, stream=LargeResponse()))


def test_rejects_compressed_response_before_decompression():
    with pytest.raises(AgentOSMediaError):
        extract(b"a", handler=lambda request: httpx.Response(
            200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(b"a"),
        ))


def test_http_failure_is_sanitized_without_bot_token():
    def failure(request):
        raise httpx.ConnectError(f"could not connect to {request.url}", request=request)

    with pytest.raises(AgentOSMediaError) as captured:
        extract(b"hello", handler=failure)
    assert "test_bot_token" not in str(captured.value)
    assert captured.value.__suppress_context__


def test_http_client_logs_do_not_expose_telegram_token(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="httpx")
    extract(b"hello")
    assert "123:test_bot_token" not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_rejects_voice_photo_and_caption_secrets():
    for msg in ({**message(b"x"), "voice": {"file_id": "a"}},
                {**message(b"x"), "photo": [{"file_id": "a"}]},
                {**message(b"x"), "caption": "key=abcdefghijklmnopqrstuvwxyz123456"}):
        with pytest.raises(AgentOSMediaError):
            extract(b"x", msg=msg)


@pytest.mark.parametrize("caption", ["\x00", "\ud800", "a" * 4097])
def test_rejects_invalid_caption(caption):
    with pytest.raises(AgentOSMediaError):
        extract(b"x", msg={**message(b"x"), "caption": caption})
