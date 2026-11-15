"""Official 2026 wire fixtures, synthetic auth only; no live request body captured."""
import base64
import json

import pytest

from bridge.wire import MODERN, PREFIX, SUPPORTED
from conftest import subscription


def modern(env, method='server/discover', params=None, who='alice-dot', headers=None, meta=None):
    params = {**(params or {}), '_meta': meta if meta is not None else {
        PREFIX + 'protocolVersion': MODERN, PREFIX + 'clientCapabilities': {},
        PREFIX + 'clientInfo': {'name': 'synthetic-client', 'version': '1'}}}
    h = {'Authorization': 'Bearer ' + who, 'MCP-Protocol-Version': MODERN,
         'Mcp-Method': method, 'Accept': 'application/json, text/event-stream'}
    if method == 'tools/call':
        h['Mcp-Name'] = params['name']
    h.update(headers or {})
    return env['client'].post('/mcp', headers=h, json={
        'jsonrpc': '2.0', 'id': 'openai-mcp-discover' if method == 'server/discover' else 'synthetic',
        'method': method, 'params': params})


def test_discovery_role_scoped_without_initialize(env):
    for role in ('hermes', 'dot', 'agent'):
        r = modern(env, who='alice-' + role)
        assert r.status_code == 200
        assert r.headers['cache-control'] == 'no-store'
        result = r.json()['result']
        assert r.json()['id'] == 'openai-mcp-discover'
        assert result['resultType'] == 'complete'
        assert result['supportedVersions'] == SUPPORTED
        assert result['_meta'][PREFIX + 'serverInfo']['name'] == 'hermes-dots-bridge'
        assert ('events' in result['capabilities']) == (role == 'dot')
        assert result['ttlMs'] == 0
        assert 'protocolVersion' not in result  # not an initialize alias


@pytest.mark.parametrize('headers,code', [
    ({'Mcp-Method': 'tools/list'}, -32020),
    ({'MCP-Protocol-Version': '2025-11-25'}, -32020),
    ({'Mcp-Method': ''}, -32020),
])
def test_mirrored_headers(env, headers, code):
    r = modern(env, headers=headers)
    assert r.status_code == 400
    assert r.json()['error']['code'] == code


@pytest.mark.parametrize('meta', [{}, {PREFIX + 'protocolVersion': MODERN},
    {PREFIX + 'protocolVersion': MODERN, PREFIX + 'clientCapabilities': []}])
def test_missing_metadata(env, meta):
    r = modern(env, meta=meta)
    assert r.status_code == 400
    assert r.json()['error']['code'] == -32602


def test_unknown_version_and_method(env):
    r = modern(env, meta={PREFIX + 'protocolVersion': '2099-01-01', PREFIX + 'clientCapabilities': {}},
               headers={'MCP-Protocol-Version': '2099-01-01'})
    assert r.status_code == 400
    assert r.json()['error']['code'] == -32022
    assert r.json()['error']['data']['supported'] == SUPPORTED
    r = modern(env, 'unknown/method')
    assert r.status_code == 404
    assert r.json()['error']['code'] == -32601


def test_modern_roles_owner_and_events(env):
    def tool(name, args, who):
        return modern(env, 'tools/call', {'name': name, 'arguments': args}, who=who).json()
    queued = tool('submit_message', {'content': 'synthetic', 'idempotency_key': 'modern'}, 'alice-hermes')
    request = json.loads(queued['result']['content'][0]['text'])
    assert queued['result']['resultType'] == 'complete'
    assert tool('get_request', {'request_id': request['id']}, 'bob-dot')['result']['isError']
    assert 'error' in tool('submit_message', {'content': 'x', 'idempotency_key': 'x'}, 'alice-dot')
    assert 'error' in modern(env, 'events/list', who='alice-hermes').json()
    assert 'error' in modern(env, 'events/list', who='alice-agent').json()
    assert modern(env, 'events/list').json()['result']['events'][0]['name'] == 'request.created'
    assert modern(env, 'events/subscribe', subscription()).json()['result']['resultType'] == 'complete'
    assert tool('get_request', {'request_id': request['id']}, 'alice-dot')['result']['resultType'] == 'complete'
    reply = tool('record_response', {'request_id': request['id'], 'content': 'bridge-ok',
                 'idempotency_key': 'reply', 'expected_revision': 0, 'state': 'completed'}, 'alice-dot')
    assert not reply['result']['isError']
    assert tool('list_responses', {'request_id': request['id']}, 'alice-hermes')['result']['resultType'] == 'complete'


def test_discovery_still_requires_auth_and_live_grant(env):
    assert modern(env, headers={'Authorization': ''}).status_code == 401
    assert modern(env, headers={'Origin': 'https://attacker.example'}).status_code == 403
    assert modern(env).status_code == 200
    for g in env['grants']:
        if g['client_id'] == 'dot':
            g['enabled'] = False
    env['policy_path'].write_text(json.dumps({'grants': env['grants']}))
    assert modern(env).status_code == 401


def test_name_encoding_and_spoofed_identity(env):
    params = {'name': 'list_requests', 'arguments': {}}
    encoded = '=?base64?' + base64.b64encode(b'list_requests').decode() + '?='
    assert modern(env, 'tools/call', params, headers={'Mcp-Name': encoded}).status_code == 200
    r = modern(env, 'tools/call', params, headers={'Mcp-Name': 'record_response'})
    assert r.status_code == 400 and r.json()['error']['code'] == -32020
    meta = {PREFIX + 'protocolVersion': MODERN, PREFIX + 'clientCapabilities': {},
            PREFIX + 'clientInfo': {'name': 'dot', 'version': '1'}, 'owner_id': 'bob', 'role': 'dot'}
    r = modern(env, 'tools/list', who='alice-hermes', meta=meta)
    assert 'record_response' not in {t['name'] for t in r.json()['result']['tools']}


def test_missing_header_and_private_diagnostics(env, caplog):
    payload = {'jsonrpc': '2.0', 'id': 8, 'method': 'server/discover', 'params': {'_meta': {
        PREFIX + 'protocolVersion': MODERN, PREFIX + 'clientCapabilities': {},
        'private': 'never-log-this'}}}
    r = env['client'].post('/mcp', headers={'Authorization': 'Bearer alice-dot',
        'MCP-Protocol-Version': MODERN}, json=payload)
    assert r.status_code == 400 and r.json()['error']['code'] == -32020
    assert 'mcp_wire_rejected code=-32020' in caplog.text
    assert 'never-log-this' not in caplog.text and 'alice-dot' not in caplog.text


def test_unknown_extra_discover_args_rejected(env):
    r = modern(env, params={'owner_id': 'bob'})
    assert r.status_code == 400 and r.json()['error']['code'] == -32602
