import json
import time
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from bridge.app import create_app
from bridge.auth import DISPATCH_SCOPE, JWTAuth
from test_openbot_adapter import listener, receipt


def test_authorized_dot_dispatches_through_native_openbot_and_reads_answer(env, tmp_path, monkeypatch):
    resource = "https://bridge.example/mcp"
    issuer = "https://issuer.example"
    target = "agent-one"
    grant = next(g for g in env["grants"] if g["owner"] == "alice" and g["role"] == "dot")
    grant["dispatch_targets"] = [target]
    env["policy_path"].write_text(json.dumps({"grants": env["grants"]}))
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    auth = JWTAuth(issuer, resource, issuer + "/jwks", env["policy"])
    auth.keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key()))

    def headers(role, scope):
        now = int(time.time())
        token = jwt.encode({"iss": issuer, "aud": resource, "sub": "alice-subject", "client_id": role,
                            "scope": scope, "iat": now, "exp": now + 300}, private, algorithm="RS256")
        return {"Authorization": "Bearer " + token}

    def rpc(client, auth_headers, method, params):
        return client.post("/mcp", headers=auth_headers, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                                                "params": params})

    def names(client, auth_headers):
        return {t["name"] for t in rpc(client, auth_headers, "tools/list", {}).json()["result"]["tools"]}

    def call(client, auth_headers, name, args):
        response = rpc(client, auth_headers, "tools/call", {"name": name, "arguments": args})
        response.raise_for_status()
        return json.loads(response.json()["result"]["content"][0]["text"])

    dot = headers("dot", DISPATCH_SCOPE)
    with TestClient(create_app(env["store"], auth, resource, issuer, run_worker=False)) as disabled:
        assert "dispatch_task" not in names(disabled, dot)
        assert DISPATCH_SCOPE not in disabled.get("/.well-known/oauth-protected-resource").json()["scopes_supported"]

    answer = "Synthetic OpenBot answer"
    directory = tmp_path / "openbot-automation"
    directory.mkdir(mode=0o700)
    replies = [(202, receipt(target, "delivery-1")), (200, {"id": "delivery-1", "status": "completed", "answer": answer})]
    with listener(replies) as (port, calls):
        for name, value in (("url", f"http://127.0.0.1:{port}"), ("token", "synthetic-local-token")):
            file = directory / name
            file.write_text(value)
            file.chmod(0o600)
        monkeypatch.setattr("bridge.dispatch.POLL_INTERVAL", 0)
        app = create_app(env["store"], auth, resource, issuer, openbot_directory=directory)
        with TestClient(app) as client:
            assert DISPATCH_SCOPE in client.get("/.well-known/oauth-protected-resource").json()["scopes_supported"]
            assert names(client, dot) == {"list_dispatch_targets", "dispatch_task", "get_task", "cancel_task"}
            assert "dispatch_task" not in names(client, headers("dot", "bridge:read"))
            submitter = headers("agent", "bridge:submit " + DISPATCH_SCOPE)
            assert "dispatch_task" not in names(client, submitter)
            args = {"target": target, "content": "Reply with a synthetic answer.", "idempotency_key": "user-task-1"}
            assert rpc(client, submitter, "tools/call", {"name": "dispatch_task", "arguments": args}).status_code == 403
            assert call(client, dot, "list_dispatch_targets", {}) == {"targets": [target]}
            assert rpc(client, dot, "tools/call", {"name": "dispatch_task", "arguments": {**args, "target": "other"}}).status_code == 403
            queued = call(client, dot, "dispatch_task", args)
            assert queued["state"] == "queued" and queued["result"] is None
            task_id = queued["task_id"]
            assert call(client, dot, "dispatch_task", args)["task_id"] == task_id
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = call(client, dot, "get_task", {"task_id": task_id})
                if status["state"] == "completed":
                    break
                time.sleep(0.05)
            assert status["state"] == "completed" and status["result"] == answer
        assert [(m, p) for m, p, _, _ in calls] == [
            ("POST", "/v1/agents/agent-one/messages"), ("GET", "/v1/agents/agent-one/deliveries/delivery-1")]
        assert calls[0][3] == {"text": args["content"], "clientMessageId": task_id}
