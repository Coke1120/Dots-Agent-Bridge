import asyncio
import json
import os
from pathlib import Path
import sys
import types

import pytest
from bridge import direct


class Result:
    def __init__(self, data=None, error=False, snake=False):
        self.value = {'is_error' if snake else 'isError': error,
                      'content': [{'type': 'text', 'text': json.dumps(data)}]}
    def model_dump(self, **kwargs):
        assert kwargs == {'mode': 'json', 'by_alias': True}
        return self.value


@pytest.mark.parametrize('snake', [True, False])
def test_decode_aliases_and_errors(snake):
    assert direct.decode(Result({'id': 'test'}, snake=snake)) == {'id': 'test'}
    with pytest.raises(direct.ClientError):
        direct.decode(Result({'token': 'secret'}, error=True, snake=snake))
    with pytest.raises(direct.ClientError):
        direct.decode(Result([]))


def test_receipt_reuse_private_and_input_bound(tmp_path):
    path = tmp_path / 'receipt.json'
    args = {'content': 'private message'}
    first = direct.prepare_receipt(path, tmp_path, 'bridge', args)
    assert direct.prepare_receipt(path, tmp_path, 'bridge', args) == first
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'private message' not in path.read_text()
    with pytest.raises(direct.ClientError):
        direct.prepare_receipt(path, tmp_path, 'bridge', {'content': 'changed'})
    with pytest.raises(direct.ClientError):
        direct.prepare_receipt(path, tmp_path, 'other', args)
    path.chmod(0o644)
    with pytest.raises(direct.ClientError):
        direct.prepare_receipt(path, tmp_path, 'bridge', args)


@pytest.mark.parametrize('module', ['run_agent', 'agent.providers', 'litellm', 'openai', 'anthropic'])
def test_no_inference_guard(module):
    with pytest.raises(direct.ClientError):
        direct.NoInference().find_spec(module)


def test_direct_cli_profile_before_import_and_no_model(tmp_path, monkeypatch, capsys):
    (tmp_path / 'config.yaml').write_text('placeholder')
    calls = []
    shutdown = []
    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if name == 'submit_message':
                assert (tmp_path / 'receipt').exists()
                return Result({'id': 'r1'}, snake=True)
            return Result({'state': 'completed', 'responses': [{'revision': 1, 'content': 'ok'}], 'active_subscriptions': 1})
    async def close(): shutdown.append(True)
    async def connect(name, config):
        return types.SimpleNamespace(session=Session(), shutdown=close)
    modules = {
        'hermes_cli.mcp_config': {'_get_mcp_servers': lambda: {'hermes-dots-bridge': {'url': 'https://127.0.0.1:8765/mcp', 'auth': 'oauth'}}, '_resolve_mcp_server_config': lambda c: c},
        'tools.mcp_tool_discovery': {'_connect_server': connect},
        'tools.mcp_tool_loop': {'_ensure_mcp_loop': lambda: None, '_run_on_mcp_loop': lambda factory, timeout: asyncio.run(factory())},
        'tools.mcp_tool_lifecycle': {'_stop_mcp_loop_if_idle': lambda: None},
    }
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name in modules:
            assert os.environ['HERMES_HOME'] == str(tmp_path)
            assert any(isinstance(x, direct.NoInference) for x in sys.meta_path)
            return types.SimpleNamespace(**modules[name])
        assert name not in ('run_agent', 'openai', 'anthropic', 'litellm')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    monkeypatch.setenv('HERMES_HOME', 'old')
    argv = ['--hermes-home', str(tmp_path), 'send', 'hello', '--receipt', str(tmp_path / 'receipt')]
    assert direct.main(argv) == 0
    first_key = calls[0][1]['idempotency_key']
    assert direct.main(argv) == 0
    assert calls[2][1]['idempotency_key'] == first_key
    assert [x[0] for x in calls] == ['submit_message', 'wait_response'] * 2
    assert len(shutdown) == 2
    assert '"content": "ok"' in capsys.readouterr().out


@pytest.mark.parametrize('failure', ['timeout', 'tool_error', 'pending'])
def test_shutdown_and_bounded_wait(failure, capsys):
    calls = []
    closed = []
    async def call(name, arguments):
        calls.append(name)
        if failure == 'timeout': raise TimeoutError('sensitive URL')
        if failure == 'tool_error': return Result(error=True)
        return Result({'state': 'queued', 'responses': [], 'active_subscriptions': 1})
    async def shutdown(): closed.append(True)
    async def connect(*args): return types.SimpleNamespace(session=types.SimpleNamespace(call_tool=call), shutdown=shutdown)
    args = types.SimpleNamespace(server='bridge', command='wait', request_id='r1', wait_calls=2)
    if failure == 'pending':
        asyncio.run(direct.exchange(connect, {}, args))
        assert calls == ['wait_response'] * 2
        assert 'waiting_stopped' in capsys.readouterr().out
    else:
        with pytest.raises((TimeoutError, direct.ClientError)):
            asyncio.run(direct.exchange(connect, {}, args))
    assert closed == [True]


def test_status_reads_without_submit(capsys):
    calls = []
    async def call(name, arguments):
        calls.append(name)
        return Result({'request': {'state': 'completed'}} if name == 'get_request' else {'responses': [], 'next_after_revision': None})
    async def shutdown(): pass
    async def connect(*args): return types.SimpleNamespace(session=types.SimpleNamespace(call_tool=call), shutdown=shutdown)
    args = types.SimpleNamespace(command='status', server='bridge', request_id='r1')
    asyncio.run(direct.exchange(connect, {}, args))
    assert calls == ['get_request', 'list_responses']


def test_installed_sdk_result_model():
    from mcp.types import CallToolResult, TextContent
    result = CallToolResult(content=[TextContent(type='text', text='{"id":"synthetic"}')])
    assert direct.decode(result) == {'id': 'synthetic'}
