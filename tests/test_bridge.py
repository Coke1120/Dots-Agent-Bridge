import base64
import hmac
import json

import pytest

from bridge.app import production_app
from bridge.store import Store
from conftest import call, rpc, submit, subscription


def subscribe(env, **kwargs):
    return rpc(env, "events/subscribe", {**subscription(), **kwargs}, "alice-dot").json()


def test_discovery_and_role_tool_lists(env):
    init = rpc(env, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}, "alice-dot").json()["result"]
    assert init["capabilities"] == {"tools": {}, "events": {}}
    for who, expected in [("alice-hermes", {"submit_message", "get_request", "list_requests", "list_responses", "cancel_request", "wait_response"}),
                          ("alice-dot", {"get_request", "list_requests", "list_responses", "record_response"})]:
        assert {t["name"] for t in rpc(env, "tools/list", who=who).json()["result"]["tools"]} == expected
    assert rpc(env, "events/list", who="alice-dot").json()["result"]["events"][0]["name"] == "request.created"
    assert "error" in rpc(env, "events/list").json()


def test_auth_metadata_and_http_errors(env):
    client = env["client"]
    response = client.post("/mcp", json={})
    assert response.status_code == 401
    assert 'resource_metadata="https://bridge.example/.well-known/oauth-protected-resource"' in response.headers["www-authenticate"]
    assert client.get("/.well-known/oauth-protected-resource").json()["resource"] == "https://bridge.example/mcp"
    auth = {"Authorization": "Bearer alice-hermes"}
    assert client.get("/mcp", headers=auth).status_code == 405
    assert client.post("/mcp", headers={**auth, "Origin": "https://evil.example"}, json={}).status_code == 403
    assert client.post("/mcp", headers={**auth, "MCP-Protocol-Version": "2026-07-28"}, json={}).status_code == 400
    assert client.post("/mcp", headers=auth, content="x").status_code == 415
    assert client.post("/mcp", headers={**auth, "Content-Type": "application/json"}, content="{").json()["error"]["code"] == -32700
    assert rpc(env, "unknown").json()["error"]["code"] == -32601
    assert client.post("/mcp", headers=auth, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    assert client.post("/mcp", headers=auth, json={"jsonrpc": "2.0", "method": "tools/call"}).json()["error"]["code"] == -32600


def test_offline_round_trip_and_no_false_completion(env):
    subscribe(env)
    request = submit(env)
    assert request["state"] == "queued"
    assert env["store"].deliver_once()
    got = call(env, "get_request", {"request_id": request["id"]})
    assert got["request"]["state"] == "delivered"
    assert got["latest_responses"] == []
    assert got["deliveries"][0]["status"] == "delivered"
    response = call(env, "record_response", {"request_id": request["id"], "content": "Synthetic response",
                     "state": "completed", "expected_revision": 0, "idempotency_key": "r1"}, "alice-dot")
    assert response["revision"] == 1
    assert call(env, "list_responses", {"request_id": request["id"]})["responses"] == [response]
    assert call(env, "get_request", {"request_id": request["id"]})["request"]["state"] == "completed"


def test_owner_and_role_isolation(env):
    r = submit(env)
    assert "error" in call(env, "get_request", {"request_id": r["id"]}, "bob-dot")
    assert "error" in call(env, "cancel_request", {"request_id": r["id"]}, "bob-hermes")
    assert "error" in call(env, "submit_message", {"content": "x", "idempotency_key": "x"}, "alice-dot")
    assert "error" in call(env, "record_response", {"request_id": r["id"], "content": "x", "state": "completed", "expected_revision": 0, "idempotency_key": "r"})
    assert call(env, "list_requests", {}, "bob-dot")["requests"] == []
    assert "error" in submit(env, key="injected", owner_id="bob")
    assert "error" in submit(env, key="cross", conversation_id="missing")


def test_submit_and_response_idempotency_and_stale_order(env):
    r = submit(env)
    assert submit(env) == r
    assert "error" in submit(env, content="different")
    args = {"request_id": r["id"], "content": "Working", "state": "processing", "expected_revision": 0, "idempotency_key": "r1"}
    first = call(env, "record_response", args, "alice-dot")
    assert call(env, "record_response", args, "alice-dot") == first
    assert "error" in call(env, "record_response", {**args, "content": "changed"}, "alice-dot")
    assert "error" in call(env, "record_response", {**args, "idempotency_key": "r2", "state": "completed"}, "alice-dot")
    done = call(env, "record_response", {**args, "idempotency_key": "r2", "state": "completed", "expected_revision": 1}, "alice-dot")
    assert done["revision"] == 2
    assert "error" in call(env, "record_response", {**args, "idempotency_key": "r3", "expected_revision": 2}, "alice-dot")


def test_conversation_followup_and_context(env):
    r = submit(env)
    assert "error" in submit(env, key="next", conversation_id=r["conversation_id"])
    call(env, "record_response", {"request_id": r["id"], "content": "Which option?", "state": "needs_input", "expected_revision": 0, "idempotency_key": "ask"}, "alice-dot")
    follow = submit(env, key="next", conversation_id=r["conversation_id"])
    assert follow["sequence"] == 2
    context = call(env, "get_request", {"request_id": follow["id"]}, "alice-dot")
    assert len(context["context"]) == 2
    assert context["context"][0]["state"] == "cancelled"
    assert context["latest_responses"][0]["state"] == "needs_input"


def test_subscription_identity_cache_rotation_and_secret_encryption(env):
    first = subscribe(env)["result"]
    assert subscribe(env)["result"] == first
    assert len(env["callback"].calls) == 1
    secret = "whsec_" + base64.b64encode(b"a" * 32).decode()
    changed = subscription(secret)
    assert rpc(env, "events/subscribe", changed, "alice-dot").json()["result"]["id"] == first["id"]
    stored = env["store"].all("subscription")[0]
    assert secret not in json.dumps(stored)
    assert stored["previous_secret"]
    submit(env)
    env["store"].deliver_once()
    _, body, headers = env["callback"].calls[-1]
    assert len(headers["webhook-signature"].split()) == 2
    event = json.loads(body)
    assert event["data"].keys() == {"request_id"}
    assert event["eventId"] == headers["webhook-id"]
    signing_input = f'{headers["webhook-id"]}.{headers["webhook-timestamp"]}.'.encode() + body
    expected = "v1," + base64.b64encode(hmac.digest(b"a" * 32, signing_input, "sha256")).decode()
    assert headers["webhook-signature"].split()[0] == expected


def test_bad_challenge_not_persisted(env):
    env["callback"].bad_challenge = True
    result = subscribe(env)
    assert result["error"]["code"] == -32015
    assert result["error"]["data"]["reason"] == "challenge_failed"
    assert env["store"].all("subscription") == []


@pytest.mark.parametrize("status", [500, 429, 408, 0])
def test_callback_retries_survive_restart(env, status):
    subscribe(env)
    r = submit(env)
    env["callback"].status = status
    assert env["store"].deliver_once()
    first = env["callback"].calls[-1]
    assert not env["store"].deliver_once()
    store2 = Store(env["db"], env["key"], env["policy"], env["callback"], clock=lambda: env["now"][0])
    env["now"][0] += 6
    env["callback"].status = 200
    assert store2.deliver_once()
    second = env["callback"].calls[-1]
    assert first[1] == second[1]
    assert first[2]["webhook-id"] == second[2]["webhook-id"]
    assert first[2]["webhook-signature"] != second[2]["webhook-signature"]
    assert store2.read(env["people"]["alice-hermes"], {"request_id": r["id"]})["request"]["state"] == "delivered"
    store2.db.close()


@pytest.mark.parametrize("status", [410, 413, 403])
def test_permanent_callback_failure_no_retry_or_false_completion(env, status):
    subscribe(env)
    r = submit(env)
    env["callback"].status = status
    env["store"].deliver_once()
    env["now"][0] += 100
    assert not env["store"].deliver_once()
    got = call(env, "get_request", {"request_id": r["id"]})
    assert got["deliveries"][0]["status"] == "failed"
    assert got["request"]["state"] == "queued"


def test_expiry_cancellation_and_retention(env):
    subscribe(env)
    r = submit(env, ttl_seconds=60)
    env["now"][0] += 61
    env["store"].deliver_once()
    assert len(env["callback"].calls) == 1
    assert call(env, "get_request", {"request_id": r["id"]})["request"]["state"] == "expired"
    other = submit(env, key="cancel")
    assert call(env, "cancel_request", {"request_id": other["id"]})["state"] == "cancelled"
    assert call(env, "cancel_request", {"request_id": other["id"]})["revision"] == 1
    env["store"].deliver_once()
    assert len(env["callback"].calls) == 1
    env["now"][0] += 7 * 86400
    env["store"].deliver_once()
    assert env["store"].all("request") == []
    assert env["store"].all("conversation") == []


def test_expired_subscription_and_unsubscribe(env):
    subscribe(env, ttlMs=1000)
    r = submit(env)
    env["now"][0] += 2
    env["store"].deliver_once()
    assert len(env["callback"].calls) == 1
    assert call(env, "get_request", {"request_id": r["id"]})["request"]["state"] == "queued"
    subscribe(env)
    sub = subscription()
    del sub["delivery"]["secret"]
    for _ in range(2):
        assert rpc(env, "events/unsubscribe", sub, "alice-dot").json()["result"] == {}
    assert env["store"].all("subscription")[0]["active"] is False


def test_revocation_stops_delivery_and_reads(env):
    subscribe(env)
    submit(env)
    env["policy_path"].write_text('{"grants": []}')
    env["store"].deliver_once()
    assert len(env["callback"].calls) == 1
    assert rpc(env, "tools/list").status_code == 401


def test_size_queue_and_schema_bounds(env):
    assert "error" in submit(env, content="x" * 8001)
    assert "error" in submit(env, ttl_seconds=True)
    assert "error" in subscribe(env, cursor="unsupported-replay")
    env["store"].limit = 1
    submit(env)
    assert "error" in submit(env, key="full")
    assert env["client"].post("/mcp", headers={"Authorization": "Bearer alice-hermes", "Content-Type": "application/json"}, content=b"x" * 65537).status_code == 413


def test_production_has_no_fake_auth_default(monkeypatch):
    monkeypatch.delenv("BRIDGE_RESOURCE", raising=False)
    with pytest.raises(KeyError):
        production_app()


def test_bounded_attempts_and_missing_subscription_no_replay(env):
    subscribe(env)
    r = submit(env)
    env["callback"].status = 500
    for _ in range(6):
        env["store"].deliver_once()
        env["now"][0] += 1000
    assert env["store"].all("outbox")[0]["attempts"] == 6
    assert env["store"].all("outbox")[0]["status"] == "failed"
    assert not env["store"].deliver_once()


def test_crash_replay_uses_same_event_and_response_deduplicates(env):
    subscribe(env)
    r = submit(env)
    env["store"].deliver_once()
    original = env["callback"].calls[-1]
    args = {"request_id": r["id"], "content": "Still working", "state": "processing", "expected_revision": 0, "idempotency_key": "stable-reply"}
    first = call(env, "record_response", args, "alice-dot")
    # Simulate a crash after receiver ACK but before committing delivery bookkeeping.
    with env["store"].transaction():
        e = env["store"].all("outbox")[0]
        e["status"] = "pending"
        env["store"].put("outbox", e)
    env["store"].deliver_once()
    assert env["callback"].calls[-1][1] == original[1]
    assert call(env, "record_response", args, "alice-dot") == first
    assert len(call(env, "list_responses", {"request_id": r["id"]})["responses"]) == 1


def test_concurrent_same_submission_is_atomic(env):
    from concurrent.futures import ThreadPoolExecutor
    args = {"content": "Synthetic concurrent message", "idempotency_key": "concurrent"}
    p = env["people"]["alice-hermes"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: env["store"].submit(p, args), range(8)))
    assert len({r["id"] for r in results}) == 1
    assert len(env["store"].all("request")) == 1
