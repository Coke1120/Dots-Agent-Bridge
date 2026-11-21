import asyncio
import json

import pytest

from bridge.auth import Denied
from bridge.protocol import Protocol
from bridge.waiting import Waiter
from conftest import call, submit, subscription


def test_wait_wire_bounds_roles_and_no_false_completion(env):
    r = submit(env)
    snap = call(env, 'wait_response', {'request_id': r['id'], 'timeout_seconds': 0})
    assert snap['timed_out'] and not snap['ready'] and snap['state'] == 'queued'
    assert snap['active_subscriptions'] == 0
    assert 'error' in call(env, 'wait_response', {'request_id': r['id'], 'timeout_seconds': 31})
    assert 'error' in call(env, 'wait_response', {'request_id': r['id'], 'timeout_seconds': 0}, 'alice-dot')
    assert 'error' in call(env, 'wait_response', {'request_id': r['id'], 'timeout_seconds': 0}, 'bob-hermes')
    env['store'].subscribe(env['people']['alice-dot'], subscription())
    assert call(env, 'wait_response', {'request_id': r['id'], 'timeout_seconds': 0})['active_subscriptions'] == 1
    assert not env['store'].all('response')


@pytest.mark.parametrize('outcome', ['reply', 'cancel', 'expiry', 'revoke', 'disconnect', 'timeout', 'token_expired'])
def test_wait_releases_lock_rechecks_auth_and_bounds(env, outcome):
    r = submit(env)
    p = env['people']['alice-hermes']
    ticks = [0]
    class Request:
        headers = {'authorization': 'Bearer synthetic'}
        async def is_disconnected(self):
            return outcome == 'disconnect'
    def auth(header):
        if outcome == 'token_expired' and ticks[0]:
            raise Denied()
        return env['policy'].recheck(p)
    async def sleep(seconds):
        ticks[0] += seconds
        if outcome == 'reply':
            env['store'].respond(env['people']['alice-dot'], {'request_id': r['id'], 'content': 'synthetic-dot',
                'idempotency_key': 'reply', 'expected_revision': 0, 'state': 'completed'})
        elif outcome == 'cancel':
            env['store'].cancel(p, {'request_id': r['id']})
        elif outcome == 'expiry':
            env['now'][0] += 86401
        elif outcome == 'revoke':
            for grant in env['grants']:
                grant['enabled'] = False
            env['policy_path'].write_text(json.dumps({'grants': env['grants']}))
    waiter = Waiter(clock=lambda: ticks[0], sleep=sleep)
    async def run():
        return await waiter.run(Request(), auth, p, Protocol(env['store']), {'name': 'wait_response',
            'arguments': {'request_id': r['id'], 'timeout_seconds': 2}})
    if outcome in ('revoke', 'token_expired'):
        with pytest.raises(Denied):
            asyncio.run(run())
    elif outcome == 'disconnect':
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    else:
        result = json.loads(asyncio.run(run())['content'][0]['text'])
        assert result['timed_out'] == (outcome == 'timeout')
        assert result['state'] == {'reply': 'completed', 'cancel': 'cancelled', 'expiry': 'expired', 'timeout': 'queued'}[outcome]
    assert waiter.active == 0
    assert ticks[0] <= 2


def test_wait_capacity_does_not_create_work(env):
    waiter = Waiter(limit=0)
    result = asyncio.run(waiter.run(None, None, None, None, {}))
    assert result['isError'] and 'capacity' in result['content'][0]['text']
    assert env['store'].all('request') == []


def test_wait_and_list_read_indexed_pages_for_the_requested_mailbox_entry(env):
    store = env['store']
    target = submit(env)
    other = submit(env, key='unrelated')
    for request in (target, other):
        for revision in range(1, 13):
            store.respond(env['people']['alice-dot'], {
                'request_id': request['id'], 'state': 'processing', 'content': 'Update',
                'expected_revision': revision - 1, 'idempotency_key': f'update-{revision}'})

    sender = env['people']['alice-hermes']
    statements = []
    store.db.set_trace_callback(statements.append)
    try:
        for method in (store.wait_snapshot, store.list_responses):
            first = method(sender, {'request_id': target['id']})
            assert [row['revision'] for row in first['responses']] == list(range(1, 11))
            assert first['next_after_revision'] == 10
            second = method(sender, {'request_id': target['id'], 'after_revision': 10})
            assert [row['revision'] for row in second['responses']] == [11, 12]
            assert second['next_after_revision'] is None
            assert all(row['request_id'] == target['id'] for row in first['responses'] + second['responses'])
            assert method(sender, {'request_id': target['id'], 'after_revision': 10**30})['responses'] == []
    finally:
        store.db.set_trace_callback(None)
    queries = [sql for sql in statements if sql.startswith('SELECT') and "kind='response'" in sql]
    assert queries
    for sql in queries:
        plan = store.db.execute('EXPLAIN QUERY PLAN ' + sql).fetchall()
        assert any('SEARCH' in row[3] and 'response_request_revision' in row[3] for row in plan)


def test_wait_snapshot_does_not_count_remapped_subscription(env):
    r = submit(env)
    env['store'].subscribe(env['people']['alice-dot'], subscription())
    for grant in env['grants']:
        if grant['subject'] == 'alice-subject' and grant['client_id'] == 'dot':
            grant['owner'] = 'bob'
    env['policy_path'].write_text(json.dumps({'grants': env['grants']}))
    snap = call(env, 'wait_response', {'request_id': r['id'], 'timeout_seconds': 0})
    assert snap['active_subscriptions'] == 0


def test_wait_authentication_expiry_returns_http_challenge_without_losing_request(env):
    from fastapi.testclient import TestClient
    from bridge.app import create_app
    request = submit(env)
    calls = []
    def expiring_auth(header):
        calls.append(header)
        if len(calls) > 1:
            raise Denied()
        return env['people']['alice-hermes']
    app = create_app(env['store'], expiring_auth, 'https://bridge.example/mcp',
                     'https://issuer.example', run_worker=False)
    with TestClient(app) as client:
        response = client.post('/mcp', headers={'Authorization': 'Bearer synthetic-expiring'},
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                  'params': {'name': 'wait_response', 'arguments': {'request_id': request['id'], 'timeout_seconds': 0}}})
    assert response.status_code == 401
    assert 'resource_metadata="https://bridge.example/.well-known/oauth-protected-resource"' in response.headers['www-authenticate']
    assert 'synthetic-expiring' not in response.text
    assert call(env, 'wait_response', {'request_id': request['id'], 'timeout_seconds': 0})['state'] == 'queued'
