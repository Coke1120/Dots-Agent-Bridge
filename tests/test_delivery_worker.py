import asyncio

from bridge.app import delivery_worker
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
