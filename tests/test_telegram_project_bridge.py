"""Project-aware Telegram transport with no Telegram/provider network calls."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.channel_projects import ChannelProjectContext, ChannelProjectError
from bauer.telegram_bridge import TelegramBridge, _MEDIA_CONTEXT, extract_outbound_media


class ProjectBackend(AgentBackend):
    def __init__(self, root: Path):
        super().__init__(config_path=root / 'missing.yaml', sessions_dir=root / 'sessions')
        self.contexts = {}
        for name in ('alpha', 'beta'):
            path = root / name
            path.mkdir()
            self.contexts[name] = ChannelProjectContext(name, name, path, f'session-{name}')
        self.current = 'alpha'
        self.selections = []
        self.received = []
        self._provider = 'ollama'
        self._model_name = 'm1'
        self._providers_fetcher = lambda: ['ollama']
        self._models_fetcher = lambda: ['m1', 'm2']

    @property
    def is_ready(self):
        return True

    def project_context(self, msg):
        return msg.project_snapshot if msg.project_snapshot_captured else self.contexts[self.current]

    def list_channel_projects(self, msg):
        if msg.raw['message']['chat']['type'] != 'private':
            raise ChannelProjectError('private required')
        return [{'id': key, 'name': key} for key in self.contexts]

    def select_channel_project(self, msg, pid):
        self.list_channel_projects(msg)
        self.current = pid
        self.selections.append(pid)
        return self.contexts[pid]

    @contextmanager
    def project_scope(self, msg):
        yield self.capture_project_context(msg)

    def process(self, msg, on_delta=None, send_fn=None):
        self.received.append((self.project_context(msg), msg.text))
        return 'ok'


@pytest.fixture
def transport(tmp_path):
    sent = []

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        sent.append((request.url.path.rsplit('/', 1)[-1], body))
        if '/file/bot' in str(request.url):
            return httpx.Response(200, content=b'document')
        result = {'message_id': 71}
        if 'getFile' in str(request.url):
            result = {'file_path': 'docs/report.txt', 'file_size': 8}
        return httpx.Response(200, json={'ok': True, 'result': result})

    bridge = TelegramBridge(token='fake', backend=ProjectBackend(tmp_path), allowed_users=[42, 43],
                            state_dir=tmp_path / 'state')
    bridge._http.close()
    bridge._http = httpx.Client(transport=httpx.MockTransport(handler))
    yield bridge, sent
    bridge._executor.shutdown(wait=True)
    bridge._http.close()


def update(text, *, user=42, chat=42, kind='private', document=None):
    message = {'text': text, 'from': {'id': user}, 'chat': {'id': chat, 'type': kind}}
    if document:
        message['document'] = document
    return {'update_id': 1, 'message': message}


def callback(data, *, user=42, chat=42, message_id=71, kind='private'):
    return {'id': 'cb', 'data': data, 'from': {'id': user},
            'message': {'message_id': message_id, 'chat': {'id': chat, 'type': kind}}}


def picker_token(bridge, sent):
    bridge._handle_update(update('/projetos'))
    keyboard = [body for method, body in sent if method == 'sendMessage'][-1]['reply_markup']
    return keyboard['inline_keyboard'][1][0]['callback_data']


def test_project_picker_opaque_single_use(transport):
    bridge, sent = transport
    data = picker_token(bridge, sent)
    assert 'beta' not in data
    assert len(data.encode()) <= 64
    bridge._handle_callback(callback(data))
    bridge._handle_callback(callback(data))
    assert bridge.backend.selections == ['beta']
    assert any('beta' in body.get('text', '') for method, body in sent if method == 'editMessageText')


@pytest.mark.parametrize('changes', [{'user': 43}, {'chat': 43}, {'message_id': 72}, {'kind': 'group'}])
def test_project_picker_rejects_wrong_identity_message_or_group(transport, changes):
    bridge, sent = transport
    data = picker_token(bridge, sent)
    bridge._handle_callback(callback(data, **changes))
    assert bridge.backend.selections == []
    bridge._handle_callback(callback(data))
    assert bridge.backend.selections == ['beta']


def test_project_picker_expiry_and_restart_invalidates(transport):
    bridge, sent = transport
    data = picker_token(bridge, sent)
    bridge._project_callbacks[data[5:]]['expires_at'] = 0
    bridge._handle_callback(callback(data))
    assert bridge.backend.selections == []
    data = picker_token(bridge, sent)
    bridge._project_callbacks.clear()
    bridge._handle_callback(callback(data))
    assert bridge.backend.selections == []


def test_projects_in_group_does_not_offer_buttons(transport):
    bridge, sent = transport
    bridge._handle_update(update('/projetos', kind='group'))
    assert not bridge._project_callbacks
    assert any('privada' in body.get('text', '') for method, body in sent)


def test_snapshot_captured_before_executor_and_media_download(transport):
    bridge, sent = transport
    pending = []
    bridge._executor.submit = lambda fn, *args: pending.append((fn, args))
    bridge._handle_update(update('', document={'file_id': 'D1', 'file_name': 'report.txt'}))
    bridge.backend.current = 'beta'
    fn, args = pending.pop()
    assert args[0].project_snapshot.project_id == 'alpha'
    fn(*args)
    context, text = bridge.backend.received[-1]
    assert context.project_id == 'alpha'
    assert str(context.workspace) in text
    downloads = list((context.workspace / '.bauer_gateway').rglob('*.txt'))
    assert len(downloads) == 1
    assert not (bridge.backend.contexts['beta'].workspace / '.bauer_gateway').exists()


def test_downloads_use_unique_names(transport):
    bridge, _ = transport
    context = bridge.backend.contexts['alpha']
    token = _MEDIA_CONTEXT.set((context.workspace, context.session_key))
    try:
        one = bridge._download_file('D1', 'report.txt')
        two = bridge._download_file('D1', 'report.txt')
        assert one != two
        assert one.read_bytes() == two.read_bytes() == b'document'
    finally:
        _MEDIA_CONTEXT.reset(token)


def test_model_picker_callback_rejects_project_changed(transport):
    bridge, sent = transport
    bridge._handle_update(update('/model'))
    bridge.backend.current = 'beta'
    bridge._handle_callback(callback('mp:p:0'))
    assert not any(method == 'editMessageText' for method, body in sent)
    assert not bridge.backend._model_overrides


def test_model_picker_callback_rejects_wrong_owner_or_message(transport):
    bridge, sent = transport
    bridge._handle_update(update('/model'))
    bridge._handle_callback(callback('mp:p:0', user=43))
    bridge._handle_callback(callback('mp:p:0', message_id=72))
    assert not any(method == 'editMessageText' for method, body in sent)


def test_outbound_media_resolves_workspace_and_blocks_external_paths(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    inside = project / 'same.png'
    inside.write_bytes(b'allowed')
    outside = tmp_path / 'same.png'
    outside.write_bytes(b'secret')
    monkeypatch.chdir(tmp_path)
    files, _ = extract_outbound_media(f'[media: same.png]\n[media: {outside}]\n[media: ../same.png]',
                                     workspace=project, allowed_root=project)
    assert files == [inside]


def test_outbound_media_blocks_symlink(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    secret = tmp_path / 'secret.txt'
    secret.write_text('secret')
    link = project / 'link.txt'
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks unavailable')
    files, _ = extract_outbound_media('[media: link.txt]', workspace=project, allowed_root=project)
    assert files == []


def test_ingress_rejects_symlink_outside_project(transport, tmp_path):
    bridge, _ = transport
    context = bridge.backend.contexts['alpha']
    outside = tmp_path / 'outside'
    outside.mkdir()
    try:
        (context.workspace / '.bauer_gateway').symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks unavailable')
    token = _MEDIA_CONTEXT.set((context.workspace, context.session_key))
    try:
        assert bridge._download_file('D1', 'secret.txt') is None
    finally:
        _MEDIA_CONTEXT.reset(token)
    assert list(outside.iterdir()) == []


def test_concurrent_project_callbacks_consume_menu_once(transport):
    from concurrent.futures import ThreadPoolExecutor
    bridge, sent = transport
    data = picker_token(bridge, sent)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(bridge._handle_callback, callback(data)) for _ in range(2)]
        for future in futures:
            future.result()
    assert bridge.backend.selections == ['beta']


def test_model_callback_changes_captured_project_session(transport):
    bridge, sent = transport
    bridge._handle_update(update('/model'))
    bridge._handle_callback(callback('mp:p:0'))
    bridge._handle_callback(callback('mp:m:0:1'))
    assert bridge.backend._model_overrides == {'session-alpha': 'm2'}


def test_outbound_transport_uses_captured_workspace(transport):
    bridge, _ = transport
    context = bridge.backend.contexts['alpha']
    inside = context.workspace / 'same.png'
    inside.write_bytes(b'allowed')
    outside = bridge.backend.contexts['beta'].workspace / 'same.png'
    outside.write_bytes(b'secret')
    bridge.backend.process = lambda *args, **kwargs: f'[media: same.png]\n[media: {outside}]'
    delivered = []
    bridge.send_media = lambda chat, path, caption='': delivered.append(path)
    bridge._handle_update(update('draw'))
    bridge._executor.shutdown(wait=True)
    assert delivered == [inside]


def test_transport_failure_does_not_echo_internal_error(transport):
    bridge, sent = transport
    def fail(*args):
        raise ValueError('secret-token=do-not-leak')
    bridge._process_message = fail
    message = update('hi')['message']
    msg = ChannelMessage(channel='telegram', user_id='42', chat_id='42', text='hi')
    bridge._safe_process(msg, message)
    outgoing = [body['text'] for method, body in sent if method == 'sendMessage']
    assert outgoing
    assert all('do-not-leak' not in text for text in outgoing)
