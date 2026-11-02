import logging

import pytest

from bridge.callback import CallbackError, subscription_diagnostic
from conftest import rpc, subscription


def test_unlisted_callback_logs_host_only_and_stays_rejected(env, caplog):
    args = subscription()
    args['delivery']['url'] = 'https://NEW.Events.Example/sensitive-path?token=sensitive-query'
    with caplog.at_level(logging.WARNING, logger='bridge.app'):
        response = rpc(env, 'events/subscribe', args, 'alice-dot')
    assert response.json()['error']['data']['reason'] == 'invalid_url'
    records = [r.getMessage() for r in caplog.records if r.name == 'bridge.app']
    assert records == ['mcp_subscription_rejected hostname=new.events.example reason=host_not_allowed']
    for secret in ('sensitive-path', 'sensitive-query', args['delivery']['secret'], 'alice', 'Bearer', 'https://'):
        assert secret not in '\n'.join(records)
    assert env['store'].all('subscription') == []
    assert env['store'].all('outbox') == []
    assert env['callback'].calls == []
    assert env['callback'].allowed_hosts == frozenset({'receiver.example'})


@pytest.mark.parametrize('url,host', [
    ('https://secret-user:secret-pass@Receiver.Example/private?key=secret-query#secret-fragment', 'receiver.example'),
    ('https://[broken/private?key=secret-query', 'unavailable'),
    ('https://bad%0ahost.example/private', 'unavailable'),
    ('https://bad\nhost.example/private', 'unavailable'),
    ('https://receiver.example:bad-port/private', 'receiver.example'),
    (None, 'unavailable'), (42, 'unavailable'), ({'secret': 'never-log'}, 'unavailable'),
])
def test_malformed_and_sensitive_url_components_are_never_returned(url, host):
    assert subscription_diagnostic(url, 'invalid_url', {'receiver.example'}) == (host, 'invalid_url')


def test_userinfo_and_arbitrary_exception_text_not_logged(env, caplog):
    args = subscription()
    args['delivery']['url'] = 'https://secret-user:secret-pass@receiver.example/private?key=secret-query#secret-fragment'
    rpc(env, 'events/subscribe', args, 'alice-dot')
    assert 'hostname=receiver.example reason=invalid_url' in caplog.text
    assert all(x not in caplog.text for x in ('secret-user', 'secret-pass', 'secret-query', 'secret-fragment', '/private'))
    caplog.clear()
    def fail(*args):
        raise CallbackError('secret-exception-text')
    env['callback'].verify = fail
    rpc(env, 'events/subscribe', subscription(), 'alice-dot')
    assert 'hostname=receiver.example reason=validation_failed' in caplog.text
    assert 'secret-exception-text' not in caplog.text
    assert not env['store'].all('subscription')
