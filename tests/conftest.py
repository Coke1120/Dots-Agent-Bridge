import json
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from bridge.app import create_app
from bridge.auth import Denied, Policy, SCOPES
from bridge.callback import Callback
from bridge.store import Store


class FakeCallback(Callback):
    """Only tests can instantiate this in-memory callback; never sends network data."""
    def __init__(self):
        super().__init__(["receiver.example"])
        self.calls = []
        self.status = 200
        self.bad_challenge = False

    def post(self, url, body, headers):
        self.calls.append((url, body, headers))
        data = json.loads(body)
        if data.get("type") == "verification":
            return 200, json.dumps({"challenge": "wrong" if self.bad_challenge else data["challenge"]}).encode()
        return self.status, b"{}"


@pytest.fixture
def env(tmp_path):
    grants = [{"subject": owner + "-subject", "client_id": role, "owner": owner,
               "role": role, "enabled": True, "queue_authorized": True}
              for owner in ("alice", "bob") for role in ("hermes", "dot")]
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"grants": grants}))
    policy = Policy(str(policy_path))
    people = {f"{o}-{r}": policy.resolve(o + "-subject", r, SCOPES[r])
              for o in ("alice", "bob") for r in ("hermes", "dot")}
    now = [time.time()]
    callback = FakeCallback()
    key = Fernet.generate_key()
    db = str(tmp_path / "test.sqlite")
    store = Store(db, key, policy, callback, clock=lambda: now[0])

    def auth(header):
        try:
            return policy.recheck(people[header.removeprefix("Bearer ")])
        except (KeyError, AttributeError):
            raise Denied() from None

    app = create_app(store, auth, "https://bridge.example/mcp", "https://issuer.example", run_worker=False)
    with TestClient(app) as client:
        yield {"store": store, "client": client, "people": people, "callback": callback,
               "now": now, "db": db, "key": key, "policy": policy, "grants": grants,
               "policy_path": policy_path}
    store.db.close()


def rpc(env, method, params=None, who="alice-hermes"):
    return env["client"].post("/mcp", headers={"Authorization": "Bearer " + who},
                              json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def call(env, name, args, who="alice-hermes"):
    result = rpc(env, "tools/call", {"name": name, "arguments": args}, who).json()
    if "error" in result:
        return result
    value = result["result"]
    if value.get("isError"):
        return {"error": value["content"][0]["text"]}
    return json.loads(value["content"][0]["text"])


def submit(env, key="msg1", **extra):
    return call(env, "submit_message", {"content": "Synthetic hello", "idempotency_key": key, **extra})


def subscription(secret=None):
    import base64
    return {"name": "request.created", "arguments": {}, "delivery": {
        "mode": "webhook", "url": "https://receiver.example/callback",
        "secret": secret or "whsec_" + base64.b64encode(b"synthetic-signing-key-32-bytes!!!!").decode()}}
