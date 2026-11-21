import asyncio
import json

from bridge.app import delivery_worker
from bridge.callback import CallbackError
from conftest import subscription


def test_worker_drains_due_events_before_first_idle_wait(env):
    store = env["store"]
    store.subscribe(env["people"]["alice-dot"], subscription())
    for i in range(70):
        store.submit(env["people"]["alice-agent"], {
            "content": "Synthetic", "idempotency_key": str(i), "ttl_seconds": 60,
        })

    class StopOnFirstWait(asyncio.Event):
        async def wait(self):
            self.set()
            return True

    asyncio.run(delivery_worker(store, StopOnFirstWait()))
    deliveries = store.all("outbox")
    assert len(deliveries) == 70
    assert all(e["status"] == "delivered" and e["attempts"] == 1 for e in deliveries)


def test_slow_callback_gets_full_backoff_without_holding_other_events(env, monkeypatch):
    store, callback, now = env["store"], env["callback"], env["now"]
    store.subscribe(env["people"]["alice-dot"], subscription())
    start = now[0]
    def submit(key):
        request = store.submit(env["people"]["alice-agent"], {
            "content": "Synthetic", "idempotency_key": key, "ttl_seconds": 60,
        })
        return request["id"]

    slow_id = submit("slow")
    now[0] = start + 1
    fast_id = submit("fast")
    post = callback.post

    def deliver(url, body, headers):
        if json.loads(body)["data"]["request_id"] == slow_id and now[0] == start + 1:
            now[0] += 10
            raise CallbackError("unreachable")
        return post(url, body, headers)

    monkeypatch.setattr(callback, "post", deliver)
    assert store.deliver_once()
    retried = next(item for item in store.all("outbox") if item["request_id"] == slow_id)
    assert retried["attempts"] == 1 and retried["status"] == "pending"
    assert retried["due"] == start + 16
    assert store.deliver_once()
    assert json.loads(callback.calls[-1][1])["data"]["request_id"] == fast_id
    assert not store.deliver_once()
    now[0] = start + 16
    assert store.deliver_once()
    assert json.loads(callback.calls[-1][1])["data"]["request_id"] == slow_id
