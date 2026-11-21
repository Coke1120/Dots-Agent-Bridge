import hashlib

import pytest

from bridge.store import Conflict, Store, canonical
from conftest import submit


def reply(request_id, revision, state="processing"):
    return {"request_id": request_id, "expected_revision": revision - 1,
            "idempotency_key": f"step-{revision}", "content": f"Update {revision}", "state": state}


@pytest.mark.parametrize("closing_state", ["completed", "failed", "needs_input"])
def test_response_capacity_reserves_a_closing_reply(env, closing_state):
    store, dot = env["store"], env["people"]["alice-dot"]
    request = submit(env)
    for revision in range(1, 32):
        progress = store.respond(dot, reply(request["id"], revision))
    with pytest.raises(Conflict, match="response_capacity"):
        store.respond(dot, reply(request["id"], 32))
    assert store.respond(dot, reply(request["id"], 31)) == progress
    closing = reply(request["id"], 32, closing_state)
    result = store.respond(dot, closing)
    assert result["state"] == closing_state and result["revision"] == 32
    assert store.respond(dot, closing) == result
    with pytest.raises(Conflict, match="idempotency_key_reused"):
        store.respond(dot, {**closing, "content": "Changed"})
    followup = submit(env, key="next", conversation_id=request["conversation_id"])
    assert followup["sequence"] == 2


def test_upgrade_indexes_legacy_rows_and_allows_one_closing_reply(env):
    store, dot, sender = env["store"], env["people"]["alice-dot"], env["people"]["alice-hermes"]
    request = submit(env)
    with store.transaction():
        store.db.execute("DROP INDEX response_request_revision")
        for revision in range(1, 33):
            args = reply(request["id"], revision)
            old = {"id": f"old-{revision}", "owner": sender.owner, "request_id": request["id"],
                   "revision": revision, "state": "processing", "content": args["content"],
                   "created": env["now"][0], "idempotency_key": args["idempotency_key"],
                   "digest": hashlib.sha256(canonical(args).encode()).hexdigest()}
            store.put("response", old)
        store.put("request", {**request, "revision": 32, "state": "processing"})

    upgraded = Store(env["db"], env["key"], env["policy"], env["callback"], clock=store.clock)
    try:
        assert upgraded.respond(dot, args) == old
        with pytest.raises(Conflict, match="response_capacity"):
            upgraded.respond(dot, reply(request["id"], 33))
        closing = reply(request["id"], 33, "completed")
        result = upgraded.respond(dot, closing)
        assert upgraded.respond(dot, closing) == result
        with pytest.raises(Conflict, match="stale_or_terminal_request"):
            upgraded.respond(dot, reply(request["id"], 34, "completed"))
        page = upgraded.list_responses(sender, {"request_id": request["id"], "after_revision": 30})
        assert [response["revision"] for response in page["responses"]] == [31, 32, 33]
        assert page["responses"][-1] == result
    finally:
        upgraded.db.close()
