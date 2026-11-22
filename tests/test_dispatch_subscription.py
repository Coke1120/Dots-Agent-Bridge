import json

import pytest

from bridge.auth import DISPATCH_SCOPE, SCOPES
from conftest import rpc, submit, subscription


@pytest.mark.parametrize("legacy_row", [False, True])
def test_revoking_dispatch_preserves_authorized_mailbox_events(env, legacy_row):
    store = env["store"]
    dot_grant = next(g for g in env["grants"] if g["owner"] == "alice" and g["role"] == "dot")
    dot_grant["dispatch_targets"] = ["agent-1"]
    env["policy_path"].write_text(json.dumps({"grants": env["grants"]}))
    dot = env["policy"].resolve(dot_grant["subject"], dot_grant["client_id"], SCOPES["dot"] | {DISPATCH_SCOPE})
    assert DISPATCH_SCOPE in dot.scopes
    env["people"]["alice-dot"] = dot
    sid = rpc(env, "events/subscribe", subscription(), "alice-dot").json()["result"]["id"]
    with store.transaction():
        saved = store.get("subscription", sid, dot.owner)
        assert saved["scopes"] == ["bridge:subscribe"]
        if legacy_row:
            saved["scopes"] = sorted(dot.scopes)
            store.put("subscription", saved)

    del dot_grant["dispatch_targets"]
    env["policy_path"].write_text(json.dumps({"grants": env["grants"]}))
    request = submit(env)
    assert store.deliver_once()
    assert store.get("request", request["id"], dot.owner)["state"] == "delivered"
    assert json.loads(env["callback"].calls[-1][1])["data"]["request_id"] == request["id"]
