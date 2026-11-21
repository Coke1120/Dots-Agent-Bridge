import json

import pytest

from conftest import rpc, submit, subscription


def register(env, endpoint, **options):
    args = subscription()
    args["delivery"]["url"] += "/" + endpoint
    return rpc(env, "events/subscribe", {**args, **options}, "alice-dot").json()


@pytest.mark.parametrize("retirement", ["unsubscribed", "expired", "gone"])
def test_retired_subscription_replaced_only_after_successful_verification(env, retirement):
    store = env["store"]
    first = register(env, "original", ttlMs=1000)["result"]
    if retirement == "gone":
        # Queue only for the original subscriber so the 410 targets the intended row.
        submit(env, key="old")
    for name in ("two", "three", "four"):
        assert "result" in register(env, name)
    assert register(env, "original", ttlMs=1000)["result"]["id"] == first["id"]
    assert register(env, "replacement")["error"]["message"] == "subscription_capacity"

    if retirement == "unsubscribed":
        args = subscription()
        del args["delivery"]["secret"]
        args["delivery"]["url"] += "/original"
        assert rpc(env, "events/unsubscribe", args, "alice-dot").json()["result"] == {}
    elif retirement == "expired":
        env["now"][0] += 2
    else:
        env["callback"].status = 410
        assert store.deliver_once()
    before = store.all("subscription", "alice")
    env["callback"].bad_challenge = True
    assert register(env, "replacement")["error"]["data"]["reason"] == "challenge_failed"
    assert store.all("subscription", "alice") == before

    env["callback"].bad_challenge = False
    assert "result" in register(env, "replacement")
    retained = store.all("subscription", "alice")
    assert len(retained) == 4
    assert all(s["active"] and s["id"] != first["id"] for s in retained)
    assert register(env, "original")["error"]["message"] == "subscription_capacity"


@pytest.mark.parametrize("retirement", ["expired", "gone"])
def test_callback_replacement_cannot_revive_old_pending_events(env, retirement):
    first = register(env, "original", ttlMs=1000)["result"]
    for key in ("old-a", "old-b"):
        submit(env, key=key)
    if retirement == "expired":
        env["now"][0] += 2
    else:
        env["callback"].status = 410
        assert env["store"].deliver_once()
        env["callback"].status = 200

    assert register(env, "original")["result"]["id"] == first["id"]
    events = env["store"].all("outbox", "alice")
    assert sorted(e["status"] for e in events) == (
        ["stopped", "stopped"] if retirement == "expired" else ["failed", "stopped"])
    fresh = submit(env, key="new")
    assert register(env, "original")["result"]["id"] == first["id"]
    assert env["store"].deliver_once()
    assert json.loads(env["callback"].calls[-1][1])["data"]["request_id"] == fresh["id"]
    assert not env["store"].deliver_once()
