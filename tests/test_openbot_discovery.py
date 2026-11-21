import os

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from bridge.app import production_app


def test_public_factory_advertises_reachable_oauth_metadata(tmp_path, monkeypatch):
    origin = "https://bridge.example"
    resource = origin + "/mcp"
    issuer = "https://issuer.example/"
    policy = tmp_path / "policy.json"
    policy.write_text('{"grants": []}')
    settings = {
        "BRIDGE_RESOURCE": resource,
        "OAUTH_ISSUER": issuer,
        "OAUTH_JWKS_URL": issuer + "jwks",
        "OAUTH_CLIENT_CLAIM": "azp",
        "BRIDGE_POLICY": str(policy),
        "BRIDGE_DB": ":memory:",
        "BRIDGE_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "BRIDGE_CALLBACK_HOSTS": "receiver.example",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)

    previous_mask = os.umask(0o077)
    try:
        app = production_app()
    finally:
        os.umask(previous_mask)

    metadata_url = origin + "/.well-known/oauth-protected-resource"
    with TestClient(app, base_url=origin) as client:
        challenge = client.get(resource)
        assert challenge.status_code == 401
        assert challenge.headers["www-authenticate"] == f'Bearer resource_metadata="{metadata_url}"'
        for url in (metadata_url, metadata_url + "/mcp"):
            response = client.get(url)
            assert response.status_code == 200
            assert response.json()["resource"] == resource
            assert response.json()["authorization_servers"] == [issuer]
