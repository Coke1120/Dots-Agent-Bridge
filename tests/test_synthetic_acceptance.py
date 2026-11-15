"""Hermes and generic submitter roles through the real JWT/MCP path.

This is an offline acceptance rehearsal, not a live tunnel or dot test.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import ClientSession
from sdk_compat import field, httpx, sdk_transport

from bridge.app import create_app
from bridge.auth import JWTAuth, SCOPES
from conftest import subscription


@pytest.mark.parametrize('submitter', ('hermes', 'agent'))
def test_dual_audience_synthetic_acceptance(env, submitter):
    local = 'http://127.0.0.1:8765/mcp'
    tunnel = 'https://tunnel.example/verified-resource'
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    auth = JWTAuth('https://issuer.example', local, 'https://issuer.example/jwks', env['policy'],
                   allow_loopback=True, tunnel_resource=tunnel)
    auth.keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key()))
    app = create_app(env['store'], auth, local, 'https://issuer.example', run_worker=False)

    def token(role, audience):
        now = int(time.time())
        return jwt.encode(dict(iss='https://issuer.example', sub='alice-subject', client_id=role,
                               aud=audience, iat=now, exp=now + 300, scope=' '.join(SCOPES[role])),
                          private, algorithm='RS256')

    def decoded(result):
        assert field(result, "isError", "is_error") is False
        return json.loads(result.content[0].text)

    async def run():
        # Simulates Authorization forwarding only. It does not implement or emulate Tunnel control plane.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers={'Authorization': 'Bearer ' + token(submitter, local)}) as sender_http, \
                   httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers={'Authorization': 'Bearer ' + token('dot', tunnel)}) as dhttp:
            async with sdk_transport(local, http_client=sender_http) as (hr, hw), \
                       sdk_transport(local, http_client=dhttp) as (dr, dw):
                async with ClientSession(hr, hw) as sender, ClientSession(dr, dw) as dot:
                    await sender.initialize()
                    await dot.initialize()
                    subscribed = await dhttp.post(local, json={'jsonrpc': '2.0', 'id': 1, 'method': 'events/subscribe', 'params': subscription()})
                    assert 'result' in subscribed.json()
                    args = {'content': 'Synthetic test only: reply bridge-ok. Do not use other tools.', 'idempotency_key': 'synthetic-acceptance-001'}
                    request = decoded(await sender.call_tool('submit_message', args))
                    assert decoded(await sender.call_tool('submit_message', args))['id'] == request['id']
                    assert env['store'].deliver_once()
                    pending = decoded(await sender.call_tool('get_request', {'request_id': request['id']}))
                    assert pending['request']['state'] == 'delivered' and pending['latest_responses'] == []
                    waiting = asyncio.create_task(hermes.call_tool('wait_response', {'request_id': request['id'], 'timeout_seconds': 5}))
                    context = decoded(await dot.call_tool('get_request', {'request_id': request['id']}))
                    reply = {'request_id': request['id'], 'content': 'bridge-ok', 'state': 'completed',
                             'expected_revision': context['request']['revision'], 'idempotency_key': 'synthetic-reply-001'}
                    first = decoded(await dot.call_tool('record_response', reply))
                    assert decoded(await dot.call_tool('record_response', reply)) == first
                    waited = decoded(await waiting)
                    assert waited['ready'] and not waited['timed_out']
                    assert waited['responses'][0]['content'] == 'bridge-ok'
                    received = decoded(await sender.call_tool('list_responses', {'request_id': request['id']}))
                    assert len(received['responses']) == 1 and received['responses'][0]['content'] == 'bridge-ok'
                    follow = decoded(await sender.call_tool('submit_message', {
                        'content': 'Synthetic follow-up', 'idempotency_key': 'synthetic-acceptance-002',
                        'conversation_id': request['conversation_id']}))
                    assert follow['sequence'] == 2
                    cancelled = decoded(await sender.call_tool('cancel_request', {'request_id': follow['id']}))
                    assert cancelled['state'] == 'cancelled'
                    await dhttp.post(local, json={'jsonrpc': '2.0', 'id': 2, 'method': 'events/unsubscribe',
                        'params': {'name': 'request.created', 'arguments': {}, 'delivery': {'mode': 'webhook', 'url': subscription()['delivery']['url']}}})
                    assert not env['store'].all('subscription')[0]['active']
    asyncio.run(run())
