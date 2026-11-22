import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from bridge.auth import DISPATCH_SCOPE, Denied
from bridge.dispatch import ATTEMPT_LEASE, POLL_INTERVAL, TASK_TTL, DispatchQueue
from bridge.openbot import OpenBotError
from bridge.store import DISPATCH_RETENTION, Conflict, Store


@pytest.fixture
def dispatch(env):
    grant = next(g for g in env["grants"] if g["owner"] == "alice" and g["role"] == "dot")
    grant["dispatch_targets"] = ["writer", "reader"]
    env["policy_path"].write_text(json.dumps({"grants": env["grants"]}))
    dot = env["policy"].resolve(grant["subject"], grant["client_id"], [DISPATCH_SCOPE])
    return DispatchQueue(env["store"]), dot, grant


def message(key="first", target="writer"):
    return {"target": target, "content": "Summarize the supplied text", "idempotency_key": key}


@pytest.mark.parametrize("status,answer", [
    ("completed", "Actual answer"), ("completed", None), ("failed", None),
    ("interrupted", None), ("cancelled", None)])
def test_dispatch_persists_receipt_then_polls_terminal_result(env, dispatch, status, answer):
    queue, dot, _ = dispatch
    task = queue.dispatch_task(dot, message())
    ref = {"task_id": task["task_id"]}
    calls = []

    class Adapter:
        def send(self, target, key, content):
            calls.append(("send", target, key, content))
            assert queue.get_task(dot, ref)["state"] == "submitting"
            with pytest.raises(Conflict, match="task_in_flight"):
                queue.cancel_task(dot, ref)
            return "delivery-1"

        def poll(self, target, receipt):
            calls.append(("poll", target, receipt))
            return status, answer

    adapter = Adapter()
    assert queue.work_once(adapter)
    assert queue.get_task(dot, ref)["state"] == "running"
    assert not queue.work_once(adapter)
    env["now"][0] += POLL_INTERVAL
    assert queue.work_once(adapter)
    result = queue.get_task(dot, ref)
    assert result["state"] == status and result["result"] == answer
    assert queue.dispatch_task(dot, message()) == result
    assert not queue.work_once(adapter)
    assert calls == [("send", "writer", task["task_id"], message()["content"]),
                     ("poll", "writer", "delivery-1")]


def test_concurrent_submit_is_unique_and_cancel_is_atomic(env, dispatch):
    queue, dot, _ = dispatch
    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(lambda _: queue.dispatch_task(dot, message()), range(8)))
    assert len({row["task_id"] for row in rows}) == 1
    for changed in ({"target": "reader"}, {"content": "Another message"}):
        with pytest.raises(Conflict, match="idempotency_key_reused"):
            queue.dispatch_task(dot, {**message(), **changed})
    ref = {"task_id": rows[0]["task_id"]}
    cancelled = queue.cancel_task(dot, ref)
    assert cancelled["state"] == "cancelled"
    assert queue.cancel_task(dot, ref) == cancelled
    assert not queue.work_once(None)


def test_ambiguous_post_retries_with_same_key_after_restart_then_only_polls(env, dispatch):
    queue, dot, _ = dispatch
    task = queue.dispatch_task(dot, message())
    sends, polls = [], []

    class Adapter:
        def send(self, target, task_id, content):
            sends.append(task_id)
            if len(sends) == 1:
                raise RuntimeError("simulate crash after OpenBot accepted POST")
            return "receipt"

        def poll(self, target, delivery_id):
            polls.append(delivery_id)
            if len(polls) == 1:
                raise OpenBotError("openbot_unavailable", retryable=True)
            if len(polls) == 2:
                return "starting", None
            return "completed", "Recovered answer"

    adapter = Adapter()
    with pytest.raises(RuntimeError, match="simulate crash"):
        queue.work_once(adapter)
    store = Store(env["db"], env["key"], env["policy"], env["callback"], clock=env["store"].clock)
    try:
        reopened = DispatchQueue(store)
        assert not reopened.work_once(adapter)
        for delay in (ATTEMPT_LEASE, POLL_INTERVAL, 2, POLL_INTERVAL):
            env["now"][0] += delay
            assert reopened.work_once(adapter)
        result = reopened.get_task(dot, {"task_id": task["task_id"]})
        assert result["result"] == "Recovered answer"
        assert sends == [task["task_id"]] * 2 and polls == ["receipt"] * 3
    finally:
        store.db.close()


def test_slow_target_retry_does_not_starve_other_work_and_expiry_does_not_resend(env, dispatch):
    queue, dot, _ = dispatch
    slow = queue.dispatch_task(dot, message())
    other = queue.dispatch_task(dot, message("other", "reader"))
    sent = []

    class Adapter:
        def send(self, target, task_id, content):
            sent.append(target)
            if target == "writer":
                raise OpenBotError("openbot_unavailable", retryable=True)
            return "known-receipt"

    adapter = Adapter()
    assert queue.work_once(adapter)
    assert queue.work_once(adapter)
    assert sent == ["writer", "reader"]
    queued = queue.dispatch_task(dot, message("pending"))
    env["now"][0] += TASK_TTL
    assert not queue.work_once(adapter)
    for task in (slow, other):
        result = queue.get_task(dot, {"task_id": task["task_id"]})
        assert result["state"] == "unknown" and result["result"] is None
    assert queue.get_task(dot, {"task_id": queued["task_id"]})["state"] == "expired"
    assert sent == ["writer", "reader"]


def test_dispatch_isolation_and_policy_revocation(env, dispatch):
    queue, dot, grant = dispatch
    task = queue.dispatch_task(dot, message())
    ref = {"task_id": task["task_id"]}
    with pytest.raises(Denied):
        queue.dispatch_task(dot, message("unauthorized", "other"))
    with pytest.raises(Denied):
        queue.dispatch_task(env["people"]["alice-agent"], message())

    # A second Dot sharing the owner does not inherit the initiating Dot's tasks.
    env["grants"].append({**grant, "client_id": "second"})
    bob_grant = next(g for g in env["grants"] if g["owner"] == "bob" and g["role"] == "dot")
    bob_grant["dispatch_targets"] = ["bob-writer"]
    env["policy_path"].write_text(json.dumps({"grants": env["grants"]}))
    other_dot = env["policy"].resolve(dot.subject, "second", [DISPATCH_SCOPE])
    other_owner = env["policy"].resolve(bob_grant["subject"], bob_grant["client_id"], [DISPATCH_SCOPE])
    for other in (other_dot, other_owner):
        with pytest.raises(Conflict, match="not_found"):
            queue.get_task(other, ref)
        with pytest.raises(Conflict, match="not_found"):
            queue.cancel_task(other, ref)
    grant["dispatch_targets"] = ["reader"]
    env["policy_path"].write_text(json.dumps({"grants": env["grants"]}))
    with pytest.raises(Denied):
        queue.get_task(dot, ref)
    assert queue.work_once(None)  # Revocation stops the task without contacting OpenBot.
    grant["dispatch_targets"] = ["writer"]
    env["policy_path"].write_text(json.dumps({"grants": env["grants"]}))
    assert queue.get_task(dot, ref)["reason"] == "authorization_revoked"


def test_capacity_retention_and_rejection_after_uncertain_attempt(env, dispatch):
    queue, dot, _ = dispatch
    env["store"].limit = 1
    task = queue.dispatch_task(dot, message())
    with pytest.raises(Conflict, match="queue_capacity"):
        queue.dispatch_task(dot, message("second"))
    sends = []

    class Adapter:
        def send(self, target, task_id, content):
            sends.append(task_id)
            if len(sends) == 1:
                raise OpenBotError("openbot_unavailable", retryable=True)
            raise OpenBotError("openbot_not_found")

    assert queue.work_once(Adapter())
    env["now"][0] += 2
    assert queue.work_once(Adapter())
    assert queue.get_task(dot, {"task_id": task["task_id"]})["state"] == "unknown"
    env["now"][0] += DISPATCH_RETENTION
    assert not queue.work_once(None)
    with pytest.raises(Conflict, match="not_found"):
        queue.get_task(dot, {"task_id": task["task_id"]})
    assert queue.dispatch_task(dot, message("second"))["state"] == "queued"


def test_disabled_restart_purges_tasks_but_preserves_target_history_ownership(env, dispatch):
    queue, dot, grant = dispatch
    assert queue.list_dispatch_targets(dot, {}) == {"targets": ["reader", "writer"]}
    task = queue.dispatch_task(dot, message())
    restarted = Store(env["db"], env["key"], env["policy"], env["callback"], clock=env["store"].clock)
    try:
        # Only the normal mailbox worker is active after this restart.
        env["now"][0] += TASK_TTL
        assert not restarted.deliver_once()
        assert restarted.db.execute("SELECT state FROM dispatch_tasks WHERE task_id=?",
                                    (task["task_id"],)).fetchone() == ("expired",)
        env["now"][0] += DISPATCH_RETENTION
        assert not restarted.deliver_once()
        assert restarted.db.execute("SELECT count(*) FROM dispatch_tasks").fetchone() == (0,)
        # An operator later reallocates the name after all original tasks are gone.
        grant["dispatch_targets"] = ["reader"]
        bob_grant = next(g for g in env["grants"] if g["owner"] == "bob" and g["role"] == "dot")
        bob_grant["dispatch_targets"] = ["writer", "fresh"]
        env["policy_path"].write_text(json.dumps({"grants": env["grants"]}))
        bob = env["policy"].resolve(bob_grant["subject"], bob_grant["client_id"], [DISPATCH_SCOPE])
        enabled = DispatchQueue(restarted)
        assert enabled.list_dispatch_targets(bob, {}) == {"targets": ["fresh"]}
        with pytest.raises(Denied):
            enabled.dispatch_task(bob, message())
        assert enabled.dispatch_task(bob, message(target="fresh"))["state"] == "queued"
    finally:
        restarted.db.close()


@pytest.mark.parametrize("content,reason", [
    ("  \t\n", "openbot_invalid_request"),
    ("report " + "\u001f" * 7993, "openbot_message_too_large"),
])
def test_dispatch_rejects_unsendable_message_without_creating_a_task(dispatch, content, reason):
    queue, dot, _ = dispatch
    with pytest.raises(Conflict, match=f"^{reason}$"):
        queue.dispatch_task(dot, {**message(), "content": content})
    assert queue.db.execute("SELECT count(*) FROM dispatch_tasks").fetchone()[0] == 0
    assert queue.db.execute("SELECT count(*) FROM dispatch_target_owners").fetchone()[0] == 0
    accepted = queue.dispatch_task(dot, {**message(), "content": "🙂" * 8000})
    assert accepted["state"] == "queued"
