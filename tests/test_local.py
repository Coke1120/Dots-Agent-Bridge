import json
import os
import plistlib
import time
from pathlib import Path
from unittest.mock import Mock

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from bridge.app import create_app
from bridge.auth import Denied, JWTAuth, validate_local_resource
from bridge.cli import SetupError, hermes_snippet, init_directory, launchd_template, load_config, main, serve


@pytest.fixture
def local_config(tmp_path, env):
    path = init_directory(tmp_path / 'Application Support' / 'Bridge & Test')
    config = json.loads(path.read_text())
    config.update(issuer="https://issuer.example", jwks_url="https://issuer.example/jwks",
                  client_claim="client_id", callback_hosts=["receiver.example"])
    path.write_text(json.dumps(config))
    (path.parent / 'policy.json').write_text(json.dumps({"grants": env["grants"][:2]}))
    key = path.parent / 'signing-secrets.key'
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    return path


def test_init_no_overwrite_or_secret_generation(tmp_path):
    path = init_directory(tmp_path / 'new')
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert sorted(p.name for p in path.parent.iterdir()) == ['config.json', 'policy.json']
    policy = json.loads((path.parent / 'policy.json').read_text())
    assert all(not g['enabled'] and not g['queue_authorized'] for g in policy['grants'])
    with pytest.raises(SetupError):
        init_directory(path.parent)
    assert main(['check', '--config', str(path)]) == 2


def test_local_check_and_templates_are_passive(local_config, capsys):
    config = load_config(local_config, ready=True)
    assert main(['check', '--config', str(local_config)]) == 0
    output = capsys.readouterr().out
    assert 'No network' in output and 'not ready' in output
    assert not Path(config['database']).exists()
    assert 'http://127.0.0.1:8765/mcp' in hermes_snippet(config)
    assert 'auth: oauth' in hermes_snippet(config)
    assert 'bridge:submit bridge:read offline_access' in hermes_snippet(config)
    assert 'bridge:respond' not in hermes_snippet(config)
    plist = plistlib.loads(launchd_template(local_config))
    assert plist['ProgramArguments'][-1] == str(local_config)
    assert plist['KeepAlive'] is False
    assert not any('signing-secrets.key' in x for x in plist['ProgramArguments'])
    assert main(['print-launchd', '--config', str(local_config)]) == 0
    assert '<key>ProgramArguments</key>' in capsys.readouterr().out


@pytest.mark.parametrize('field', ['config.json', 'policy.json', 'signing-secrets.key'])
def test_private_permissions_required(local_config, field):
    (local_config.parent / field).chmod(0o644)
    with pytest.raises(SetupError):
        load_config(local_config, ready=True)


def test_symlink_key_and_disabled_grant_fail_closed(local_config):
    key = local_config.parent / 'signing-secrets.key'
    real = key.with_suffix('.real')
    key.rename(real)
    key.symlink_to(real)
    with pytest.raises(SetupError):
        load_config(local_config, ready=True)
    key.unlink()
    real.rename(key)
    policy_path = local_config.parent / 'policy.json'
    policy = json.loads(policy_path.read_text())
    policy['grants'][1]['queue_authorized'] = False
    policy_path.write_text(json.dumps(policy))
    with pytest.raises(SetupError):
        load_config(local_config, ready=True)


@pytest.mark.parametrize('url', ['http://0.0.0.0:8765/mcp', 'http://192.168.1.2:8765/mcp',
                                'http://localhost:8765/mcp', 'http://127.0.0.1:80/mcp',
                                'http://127.0.0.1:8765/mcp?x=1', 'http://u:p@127.0.0.1:8765/mcp'])
def test_local_listener_cannot_expand_to_lan(url):
    with pytest.raises(ValueError):
        validate_local_resource(url)


def test_local_and_explicit_tunnel_audience_verification(env):
    local = 'http://127.0.0.1:8765/mcp'
    tunnel = 'https://api.openai.com/mcp/EXAMPLE_TUNNEL_RESOURCE'
    with pytest.raises(ValueError):
        JWTAuth('https://issuer.example', local, 'https://issuer.example/jwks', env['policy'])
    auth = JWTAuth('https://issuer.example', local, 'https://issuer.example/jwks', env['policy'],
                   allow_loopback=True, tunnel_resource=tunnel)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    auth.keys = Mock()
    auth.keys.get_signing_key_from_jwt.return_value.key = private.public_key()
    claims = dict(iss='https://issuer.example', sub='alice-subject', client_id='hermes',
                  iat=int(time.time()), exp=int(time.time()) + 60, scope='bridge:read')
    for audience in (local, tunnel):
        token = jwt.encode({**claims, 'aud': audience}, private, algorithm='RS256')
        assert auth('Bearer ' + token).owner == 'alice'
    with pytest.raises(Denied):
        auth('Bearer ' + jwt.encode({**claims, 'aud': tunnel + '-other'}, private, algorithm='RS256'))
    app = create_app(env['store'], auth, local, 'https://issuer.example', run_worker=False)
    with TestClient(app) as client:
        challenge = client.post('/mcp').headers['www-authenticate']
        assert 'http://127.0.0.1:8765/.well-known/oauth-protected-resource' in challenge
        assert client.get('/.well-known/oauth-protected-resource').json()['resource'] == local


def test_serve_binds_loopback_and_holds_single_process_lock(local_config, monkeypatch):
    config = load_config(local_config, ready=True)
    observed = []
    def fake_run(app, **kwargs):
        observed.append(kwargs)
        with pytest.raises(SetupError, match='Another bridge'):
            serve(config)
    monkeypatch.setattr('uvicorn.run', fake_run)
    previous = os.umask(0o077)
    try:
        serve(config)
    finally:
        os.umask(previous)
    assert observed == [dict(host='127.0.0.1', port=8765, workers=1, access_log=False,
                             proxy_headers=False, limit_concurrency=16)]
    assert Path(config['database']).exists()
    assert load_config(local_config, ready=True)


def test_malformed_local_settings_fail_without_traceback_or_values(local_config, capsys):
    config = json.loads(local_config.read_text())
    config["issuer"] = 42
    local_config.write_text(json.dumps(config))
    assert main(["check", "--config", str(local_config)]) == 2
    assert "42" not in capsys.readouterr().err
    config["issuer"] = "https://issuer.example"
    local_config.write_text(json.dumps(config))
    (local_config.parent / "policy.json").write_text("[]")
    assert main(["check", "--config", str(local_config)]) == 2


def test_discovery_bootstrap_exposes_no_tools_or_writes(local_config, monkeypatch):
    from bridge.app import create_discovery_app
    key = local_config.parent / 'signing-secrets.key'
    key.unlink()
    (local_config.parent / 'policy.json').unlink()
    runs = []
    def fake_run(app, **kwargs):
        runs.append(kwargs)
        with TestClient(app) as client:
            for path in ('/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/mcp'):
                assert client.get(path).json()['authorization_servers'] == ['https://issuer.example']
            for method in ('initialize', 'tools/list', 'tools/call', 'events/subscribe'):
                response = client.post('/mcp', headers={'Authorization': 'Bearer synthetic'}, json={
                    'jsonrpc': '2.0', 'id': 1, 'method': method})
                assert response.status_code == 401 and not response.content
            assert client.post('/mcp', headers={'Origin': 'https://other.example'}).status_code == 403
    monkeypatch.setattr('uvicorn.run', fake_run)
    assert main(['serve-discovery', '--config', str(local_config)]) == 0
    assert runs[0]['host'] == '127.0.0.1'
    assert not (local_config.parent / 'bridge.sqlite').exists()
    assert not key.exists()


def test_tunnel_readiness_requires_pinned_audience(local_config):
    assert main(['check', '--require-tunnel', '--config', str(local_config)]) == 2
    config = json.loads(local_config.read_text())
    config['tunnel_resource'] = 'https://tunnel.example/exact-resource'
    local_config.write_text(json.dumps(config))
    assert main(['check', '--require-tunnel', '--config', str(local_config)]) == 0
