import base64
import json
import socket
import time
from types import SimpleNamespace
from unittest.mock import Mock

import dns.resolver
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from bridge.auth import Denied, JWTAuth, SCOPES
from bridge.callback import Callback, CallbackError, public_addresses, signing_key, validate_url
from conftest import call, rpc, submit, subscription


@pytest.fixture
def jwt_auth(env):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    auth = JWTAuth("https://issuer.example", "https://bridge.example/mcp", "https://issuer.example/jwks", env["policy"])
    auth.keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key()))
    claims = {"iss": "https://issuer.example", "aud": "https://bridge.example/mcp", "sub": "alice-subject",
              "exp": int(time.time()) + 3600, "iat": int(time.time()), "client_id": "hermes",
              "scope": " ".join(SCOPES["hermes"])}
    return auth, private, claims


def test_signed_jwt_auth_and_role_not_claim_controlled(jwt_auth):
    auth, private, claims = jwt_auth
    claims["scope"] += " bridge:respond bridge:subscribe"
    claims["owner_id"] = "bob"
    claims["role"] = "dot"
    p = auth("Bearer " + jwt.encode(claims, private, algorithm="RS256"))
    assert p.owner == "alice" and p.role == "hermes" and p.scopes == frozenset(SCOPES["hermes"])


@pytest.mark.parametrize("change", [{"iss": "https://evil.example"}, {"aud": "another-resource"},
                                  {"exp": 1}, {"iat": 9999999999}, {"nbf": 9999999999},
                                  {"sub": "unknown"}, {"client_id": "unregistered"}, {"scope": []}])
def test_jwt_rejects_invalid_claims(jwt_auth, change):
    auth, private, claims = jwt_auth
    with pytest.raises(Denied):
        auth("Bearer " + jwt.encode({**claims, **change}, private, algorithm="RS256"))


def test_jwt_missing_claim_bad_signature_algorithm(jwt_auth):
    auth, private, claims = jwt_auth
    for missing in ("exp", "iat", "iss", "aud", "sub", "scope", "client_id"):
        c = {k: v for k, v in claims.items() if k != missing}
        with pytest.raises(Denied):
            auth("Bearer " + jwt.encode(c, private, algorithm="RS256"))
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(Denied):
        auth("Bearer " + jwt.encode(claims, other, algorithm="RS256"))
    with pytest.raises(Denied):
        auth("Bearer " + jwt.encode(claims, "synthetic-secret" * 4, algorithm="HS256"))


@pytest.mark.parametrize("url", ["http://receiver.example/hook", "https://receiver.example:8443/", "https://receiver.example.evil/", "https://u:p@receiver.example/", "https://receiver.example/#fragment", "https://receiver.example/\r\nInjected:yes", "https://127.0.0.1/", "https://receiver.example/é"])
def test_callback_url_rejection(url):
    with pytest.raises(CallbackError):
        validate_url(url, {"receiver.example"})


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.0.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:8.8.8.8", "2002:0808:0808::1", "ff02::1"])
def test_private_and_transition_addresses_blocked(ip):
    with pytest.raises(CallbackError):
        public_addresses(ip)


def test_mixed_dns_and_rebinding_blocked(monkeypatch):
    addresses = ["8.8.8.8"]
    class Resolver:
        def resolve(self, host, kind, **kwargs):
            assert host == "receiver.example." and kwargs["search"] is False
            assert 0 < kwargs["lifetime"] <= 3
            return [SimpleNamespace(address=x) for x in addresses] if kind == "A" else []
    monkeypatch.setattr("bridge.callback.dns.resolver.Resolver", Resolver)
    assert public_addresses("receiver.example")[0][4] == ("8.8.8.8", 443)
    addresses.append("127.0.0.1")
    with pytest.raises(CallbackError):
        public_addresses("receiver.example")
    addresses[:] = ["10.0.0.1"]
    with pytest.raises(CallbackError):
        public_addresses("receiver.example")


def test_transport_pins_ip_checks_tls_hostname_and_does_not_redirect(monkeypatch):
    seen = []
    raw = Mock()
    tls = Mock()
    context = Mock()
    context.wrap_socket.return_value = tls
    conn = Mock()
    response = Mock(status=302)
    response.read.return_value = b"{}"
    conn.getresponse.return_value = response
    def addresses(host):
        seen.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))]
    monkeypatch.setattr("bridge.callback.public_addresses", addresses)
    monkeypatch.setattr("bridge.callback.socket.socket", lambda *args: raw)
    monkeypatch.setattr("bridge.callback.ssl.create_default_context", lambda: context)
    monkeypatch.setattr("bridge.callback.http.client.HTTPSConnection", lambda *args, **kwargs: conn)
    transport = Callback(["receiver.example"])
    assert transport.post("https://receiver.example/hook?x=1", b"{}", {})[0] == 302
    raw.connect.assert_called_once_with(("8.8.8.8", 443))
    context.wrap_socket.assert_called_once_with(raw, server_hostname="receiver.example", do_handshake_on_connect=False)
    tls.do_handshake.assert_called_once()
    conn.request.assert_called_once_with("POST", "/hook?x=1", b"{}", {})
    assert seen == ["receiver.example"]
    response.read.return_value = b"x" * 16385
    with pytest.raises(CallbackError, match="response_too_large"):
        transport.post("https://receiver.example/hook", b"{}", {})
    with pytest.raises(CallbackError, match="payload_too_large"):
        transport.post("https://receiver.example/hook", b"x" * 262145, {})


@pytest.mark.parametrize("secret", ["plain", "whsec_!", "whsec_" + base64.b64encode(b"x" * 23).decode(), "whsec_" + base64.b64encode(b"x" * 65).decode()])
def test_secret_bounds(secret):
    with pytest.raises(CallbackError):
        signing_key(secret)


def test_policy_corruption_and_explicit_grant_required(env):
    for content in ('{}', '{"grants": []}', 'bad json'):
        env["policy_path"].write_text(content)
        assert rpc(env, "tools/list").status_code == 401
    grant = env["grants"][0].copy()
    grant["queue_authorized"] = False
    env["policy_path"].write_text(json.dumps({"grants": [grant]}))
    assert rpc(env, "tools/list").status_code == 401


def test_configured_azp_provider_claim(jwt_auth):
    auth, private, claims = jwt_auth
    auth.client_claim = "azp"
    claims["azp"] = claims.pop("client_id")
    assert auth("Bearer " + jwt.encode(claims, private, algorithm="RS256")).role == "hermes"


def test_network_exception_is_retried(env):
    from conftest import subscription
    rpc(env, "events/subscribe", subscription(), "alice-dot")
    submit(env)
    env["callback"].post = Mock(side_effect=CallbackError("unreachable"))
    env["store"].deliver_once()
    e = env["store"].all("outbox")[0]
    assert e["status"] == "pending" and e["attempts"] == 1


def test_insufficient_scope_returns_http_challenge_and_keeps_role_boundary(env):
    response = rpc(env, 'tools/call', {'name': 'record_response', 'arguments': {}}, 'alice-hermes')
    assert response.status_code == 403
    assert 'error="insufficient_scope"' in response.headers['www-authenticate']
    assert 'scope="bridge:respond"' in response.headers['www-authenticate']
    assert env['store'].all('response') == []


def test_wait_discovery_requires_both_scopes(env):
    from bridge.protocol import Protocol
    from bridge.auth import Principal
    principal = env['people']['alice-hermes']
    limited = Principal(principal.subject, principal.client, principal.owner, principal.role,
                        frozenset({'bridge:submit'}))
    tools = Protocol(env['store']).dispatch(limited, 'tools/list', {})['tools']
    assert 'wait_response' not in {tool['name'] for tool in tools}


@pytest.mark.parametrize('body', [b'[]', b'null', b'not-json', b'\xff',
                                  json.dumps({'challenge': 'invalid-\u00e9'}).encode()])
def test_malformed_challenge_is_classified_without_internal_error(body):
    callback = Callback(['receiver.example'])
    callback.post = Mock(return_value=(200, body))
    from conftest import subscription
    secret = subscription()['delivery']['secret']
    with pytest.raises(CallbackError, match='challenge_failed'):
        callback.verify('https://receiver.example/callback', secret, 'sub_synthetic', time.time())
