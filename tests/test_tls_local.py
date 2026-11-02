import datetime
import ipaddress
import json
import plistlib
import socket
import ssl
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bridge.cli import SetupError, init_directory, launchd_template, load_config, local_status, tls_options, tunnel_launchd_template


def tls_config(tmp_path):
    path = init_directory(tmp_path / 'private')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic-loopback')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .sign(key, hashes.SHA256()))
    for filename, data in [('server.pem', cert.public_bytes(serialization.Encoding.PEM)),
                           ('server.key', key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))]:
        target = path.parent / filename
        target.write_bytes(data)
        target.chmod(0o600)
    config = json.loads(path.read_text())
    config.update(resource='https://127.0.0.1:8765/mcp', tls_cert_file='server.pem', tls_key_file='server.key')
    path.write_text(json.dumps(config))
    return path


def test_https_config_and_real_tls_handshake(tmp_path):
    path = tls_config(tmp_path)
    config = load_config(path)
    opts = tls_options(config)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(opts['ssl_certfile'], opts['ssl_keyfile'])
    client = ssl.create_default_context(cafile=opts['ssl_certfile'])
    left, right = socket.socketpair()
    errors = []
    def reply():
        try:
            with server.wrap_socket(left, server_side=True) as stream:
                stream.sendall(b'ok')
        except Exception as e:
            errors.append(e)
    thread = threading.Thread(target=reply)
    thread.start()
    with client.wrap_socket(right, server_hostname='127.0.0.1') as stream:
        assert stream.recv(2) == b'ok'
    thread.join(timeout=2)
    assert not thread.is_alive() and not errors
    assert plistlib.loads(launchd_template(path, keep_alive=True))['Disabled'] is True


def test_tls_fail_closed_and_no_status_side_effects(tmp_path):
    path = tls_config(tmp_path)
    config = load_config(path)
    assert local_status(config)['database_exists'] is False
    assert not Path(config['database']).exists()
    config['resource'] = 'http://127.0.0.1:8765/mcp'
    with pytest.raises(SetupError):
        tls_options(config)
    config['resource'] = 'https://127.0.0.1:8765/mcp'
    Path(config['tls_key_file']).chmod(0o644)
    with pytest.raises(SetupError):
        tls_options(config)
    config.pop('tls_key_file')
    with pytest.raises(SetupError):
        tls_options(config)


def test_tunnel_template_is_disabled_and_never_sets_http_exception(tmp_path):
    template = plistlib.loads(tunnel_launchd_template(tmp_path / 'tunnel client', tmp_path / 'profile.yaml', tmp_path / 'ca.pem'))
    assert template['Disabled'] is True and template['KeepAlive'] is True
    assert template['ThrottleInterval'] == 30
    assert '--ca-bundle' in template['ProgramArguments']
    assert not any('plaintext' in x for x in template['ProgramArguments'])
