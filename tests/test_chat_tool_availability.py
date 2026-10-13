"""Shell approval and full governed tool visibility across web chat paths."""

import json

import pytest
from fastapi.testclient import TestClient

from bauer.config_loader import ToolsSection
from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.server import create_app
from bauer.shell_runner import CommandResult, ShellRunner, load_learned_commands
from bauer.tool_router import ToolRouter
from bauer.tools.base import ToolError


@pytest.mark.parametrize('mode', ['bauer_native', 'agno'])
@pytest.mark.parametrize('transport', ['chat', 'stream'])
@pytest.mark.parametrize('governed', [False, True])
def test_web_chat_asks_then_learns_only_explicit_session_approval(tmp_path, monkeypatch, mode, transport, governed):
    monkeypatch.setenv('BAUER_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr('bauer.server._MEMORY_PREFETCH_ENABLED', False)
    monkeypatch.setattr('bauer.projects_registry._DEFAULT_REGISTRY', tmp_path / 'projects.json')
    workspace = tmp_path / 'project'
    workspace.mkdir()
    config = tmp_path / 'config.yaml'
    config.write_text(
        'model:\n  provider: ollama\n  name: controlled\nkernel:\n  enabled: ' + str(governed).lower() + '\n',
        encoding='utf-8',
    )
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'serve-runtime-mode.json').write_text(json.dumps({'mode': mode}), encoding='utf-8')
    runner = ShellRunner(workspace=workspace)
    router = ToolRouter(workspace=workspace, shell_runner=runner,
                        tool_allowlist=['run_command', 'calculate', 'list_dir'])
    executed = []
    seen = []

    def controlled_run(command, confirm=False):
        args = runner.validate(command, confirm=confirm)
        executed.append(args)
        return CommandResult(args, 0, 'VALIDATED', '', 1)

    monkeypatch.setattr(runner, 'run', controlled_run)

    def invoke(active_router, message):
        seen.append(message)
        try:
            result = active_router.execute_native_call('run_command', {'command': 'fake-cli --version'})
        except ToolError as exc:
            result = str(exc)
        return result

    def native_turn(ctx, active_router, *args, **kwargs):
        message = '\n'.join(str(item.get('content') or '') for item in ctx.get_payload())
        result = invoke(active_router, message)
        ctx.add_assistant('Resposta vaga do modelo')
        return 'Resposta vaga do modelo', [{'tool': 'run_command', 'result': result}]

    def agno_turn(adapter, request):
        assert request['agent_spec']['tools'] == router.available_tools()
        assert 'calculate' in request['agent_spec']['tools']
        result = invoke(adapter._build_tool_router(), request['task'])
        return {'status': 'completed', 'output': 'Resposta vaga do modelo',
                'metadata': {'tools': [{'tool_name': 'run_command', 'result': result}]}}

    monkeypatch.setattr('bauer.agent.run_one_turn', native_turn)
    monkeypatch.setattr(AgnoRuntimeAdapter, 'run_agent', agno_turn)
    app = create_app(model_name='controlled', applied_context=4096, client=object(), router=router,
                     system_prompt='test', sessions_dir=tmp_path / 'sessions', config_path=config,
                     api_key='', web_auth_enabled=False, rate_limit_requests=0)

    def send(client, message, sid=None):
        payload = {'message': message}
        if sid:
            payload['session_id'] = sid
        if transport == 'chat':
            response = client.post('/chat', json=payload)
            assert response.status_code == 200, response.text
            return response.json()['response'], response.json()['session_id']
        response = client.get('/stream', params=payload)
        assert response.status_code == 200, response.text
        return response.text, response.headers['X-Session-ID']

    with TestClient(app) as client:
        question, sid = send(client, 'rode fake-cli --version')
        assert "O comando 'fake-cli' está fora da allowlist" in question
        assert executed == [] and load_learned_commands() == set()
        # A different session's bare yes cannot authorize this pending command.
        _, other_sid = send(client, 'sim')
        assert other_sid != sid and executed == [] and load_learned_commands() == set()
        _, _ = send(client, 'sim', sid)
        assert executed == [['fake-cli', '--version']]
        assert load_learned_commands() == {'fake-cli'}
        assert 'Tarefa original: rode fake-cli --version' in seen[-1]
        # Existing cached runners also learn grants, while validation remains active.
        assert ShellRunner(workspace=workspace).validate('fake-cli --version') == ['fake-cli', '--version']


def test_shell_flag_builds_the_actual_terminal(tmp_path):
    from types import SimpleNamespace
    from bauer.commands._runtime import _build_shell_runner

    assert _build_shell_runner(SimpleNamespace(tools=ToolsSection(shell_enabled=False)), tmp_path) is None
    runner = _build_shell_runner(SimpleNamespace(tools=ToolsSection(shell_enabled=True)), tmp_path)
    router = ToolRouter(workspace=tmp_path, shell_runner=runner)
    assert 'run_command' in router.available_tools()
    assert runner.safe_mode is True


@pytest.mark.parametrize('mode', ['bauer_native', 'agno'])
def test_stream_timeout_does_not_make_hidden_worker_question_approvable(tmp_path, monkeypatch, mode):
    from threading import Event

    monkeypatch.setenv('BAUER_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr('bauer.server._MEMORY_PREFETCH_ENABLED', False)
    monkeypatch.setattr('bauer.projects_registry._DEFAULT_REGISTRY', tmp_path / 'projects.json')
    monkeypatch.setattr('bauer.server._STREAM_TURN_TIMEOUT_SECONDS', 0.02)
    workspace = tmp_path / 'project'
    workspace.mkdir()
    config = tmp_path / 'config.yaml'
    config.write_text('model:\n  provider: ollama\n  name: controlled\nkernel:\n  enabled: true\n', encoding='utf-8')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'serve-runtime-mode.json').write_text(json.dumps({'mode': mode}), encoding='utf-8')
    runner = ShellRunner(workspace=workspace)
    router = ToolRouter(workspace=workspace, shell_runner=runner, tool_allowlist=['run_command'])
    entered, release = Event(), Event()
    invocations = []

    def controlled_result(active_router):
        invocations.append(True)
        if len(invocations) == 1:
            entered.set()
            assert release.wait(5), 'test did not release background turn'
            try:
                active_router.execute_native_call('run_command', {'command': 'hidden-customtool --version'})
            except ToolError as exc:
                return str(exc)
            pytest.fail('unapproved background command was executed')
        return 'resposta ao sim depois do timeout'

    def native_turn(ctx, active_router, *args, **kwargs):
        result = controlled_result(active_router)
        ctx.add_assistant('resposta controlada')
        return 'resposta controlada', [{'tool': 'run_command', 'result': result}] if len(invocations) == 1 else []

    def agno_turn(adapter, request):
        result = controlled_result(adapter._build_tool_router())
        return {'status': 'completed', 'output': 'resposta controlada', 'metadata': {
            'tools': [{'tool_name': 'run_command', 'result': result}] if len(invocations) == 1 else [],
        }}

    monkeypatch.setattr('bauer.agent.run_one_turn', native_turn)
    monkeypatch.setattr(AgnoRuntimeAdapter, 'run_agent', agno_turn)
    app = create_app(model_name='controlled', applied_context=4096, client=object(), router=router,
                     system_prompt='test', sessions_dir=tmp_path / 'sessions', config_path=config,
                     api_key='', web_auth_enabled=False, rate_limit_requests=0)
    with TestClient(app) as client:
        try:
            response = client.get('/stream', params={'message': 'rode hidden-customtool --version', 'session_id': 'same'})
            assert entered.is_set()
            assert 'conexão foi' in response.text
            assert 'Você autoriza' not in response.text
        finally:
            release.set()
        # The session lock makes this wait for background settlement, without
        # polling/sleep. Its late question was never shown over the closed SSE.
        reply = client.post('/chat', json={'message': 'sim', 'session_id': 'same'})
        assert reply.status_code == 200, reply.text
    assert len(invocations) == 2
    assert load_learned_commands() == set()
    assert 'hidden-customtool' not in runner._runtime_allowed


@pytest.mark.parametrize('mode', ['bauer_native', 'agno'])
def test_kernel_rejection_does_not_present_an_undelivered_shell_question(tmp_path, monkeypatch, mode):
    from bauer.core.kernel.evaluator import Evaluator, Verdict

    monkeypatch.setenv('BAUER_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr('bauer.server._MEMORY_PREFETCH_ENABLED', False)
    monkeypatch.setattr('bauer.projects_registry._DEFAULT_REGISTRY', tmp_path / 'projects.json')
    config = tmp_path / 'config.yaml'
    config.write_text('model:\n  provider: ollama\n  name: controlled\nkernel:\n  enabled: true\n', encoding='utf-8')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'serve-runtime-mode.json').write_text(json.dumps({'mode': mode}), encoding='utf-8')
    runner = ShellRunner(workspace=tmp_path)
    router = ToolRouter(workspace=tmp_path, shell_runner=runner, tool_allowlist=['run_command'])
    reject = [True]
    monkeypatch.setattr(Evaluator, 'evaluate', lambda *a, **k: Verdict(not reject[0], reason='controlled rejection'))

    def controlled_result(active_router):
        if reject[0]:
            with pytest.raises(ToolError):
                active_router.execute_native_call('run_command', {'command': 'hidden-http-cli --version'})
        return 'resposta controlada'

    def native_turn(ctx, active_router, *args, **kwargs):
        result = controlled_result(active_router)
        ctx.add_assistant(result)
        return result, []

    def agno_turn(adapter, request):
        return {'status': 'completed', 'output': controlled_result(adapter._build_tool_router()), 'metadata': {'tools': []}}

    monkeypatch.setattr('bauer.agent.run_one_turn', native_turn)
    monkeypatch.setattr(AgnoRuntimeAdapter, 'run_agent', agno_turn)
    app = create_app(model_name='controlled', applied_context=4096, client=object(), router=router,
                     system_prompt='test', sessions_dir=tmp_path / 'sessions', config_path=config,
                     api_key='', web_auth_enabled=False, rate_limit_requests=0)
    with TestClient(app) as client:
        failed = client.post('/chat', json={'message': 'rode hidden-http-cli --version', 'session_id': 'same'})
        assert failed.status_code == 500
        assert 'Você autoriza' not in failed.text
        reject[0] = False
        reply = client.post('/chat', json={'message': 'sim', 'session_id': 'same'})
        assert reply.status_code == 200, reply.text
    assert load_learned_commands() == set()
    assert 'hidden-http-cli' not in runner._runtime_allowed
