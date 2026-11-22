"""Durable dot-initiated work for an explicitly allowed local OpenBot agent."""
import sqlite3
import uuid

from .auth import DISPATCH_SCOPE, Denied, Principal
from .openbot import OpenBotError, build_message_body
from .store import Conflict


TASK_TTL = 3600
POLL_INTERVAL = 3
ATTEMPT_LEASE = 30  # Longer than the adapter's bounded request timeout.
PUBLIC_FIELDS = ("task_id", "target", "state", "created", "expires", "result", "reason")
DEFINITE_REJECTIONS = {"openbot_forbidden", "openbot_not_found", "openbot_invalid_request",
                       "openbot_message_too_large", "openbot_invalid_credentials"}


class DispatchQueue:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        with store.transaction():
            self.db.execute("""CREATE TABLE IF NOT EXISTS dispatch_tasks (
                task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, subject TEXT NOT NULL, client TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, target TEXT NOT NULL, content TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, due REAL,
                delivery_id TEXT, generation INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0, result TEXT, reason TEXT,
                UNIQUE(owner, subject, client, idempotency_key))""")
            self.db.execute("""CREATE INDEX IF NOT EXISTS dispatch_due ON dispatch_tasks(due)
                               WHERE due IS NOT NULL""")
            self.db.execute("CREATE INDEX IF NOT EXISTS dispatch_created ON dispatch_tasks(created)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS dispatch_target_owners (
                target TEXT PRIMARY KEY, owner TEXT NOT NULL)""")
        store.has_dispatch_table = True

    def _one(self, sql, params):
        cursor = self.db.cursor()
        cursor.row_factory = sqlite3.Row
        return cursor.execute(sql, params).fetchone()

    def _targets(self, principal):
        self.store.authorize(principal, DISPATCH_SCOPE)
        if principal.role != "dot":
            raise Denied()
        targets = self.store.policy.dispatch_targets(principal)
        return [target for target in targets if not self.db.execute(
            "SELECT 1 FROM dispatch_target_owners WHERE target=? AND owner<>?",
            (target, principal.owner)).fetchone()]

    def list_dispatch_targets(self, p, a):
        with self.store.transaction():
            return {"targets": self._targets(p)}

    @staticmethod
    def _public(task):
        return {key: task[key] for key in PUBLIC_FIELDS}

    def _owned(self, principal, task_id):
        targets = self._targets(principal)
        task = self._one("""SELECT * FROM dispatch_tasks
                            WHERE task_id=? AND owner=? AND subject=? AND client=?""",
                         (task_id, principal.owner, principal.subject, principal.client))
        if task is None:
            raise Conflict("not_found")
        if task["target"] not in targets:
            raise Denied()
        return task

    def dispatch_task(self, p, a):
        with self.store.transaction():
            if a["target"] not in self._targets(p):
                raise Denied()
            now = self.store.clock()
            self.store.maintain_dispatch(now)
            identity = (p.owner, p.subject, p.client, a["idempotency_key"])
            existing = self._one("""SELECT * FROM dispatch_tasks
                WHERE owner=? AND subject=? AND client=? AND idempotency_key=?""", identity)
            if existing:
                if existing["target"] != a["target"] or existing["content"] != a["content"]:
                    raise Conflict("idempotency_key_reused")
                return self._public(existing)
            task_id = str(uuid.uuid4())
            try:
                build_message_body(task_id, a["content"])
            except OpenBotError as error:
                raise Conflict(error.reason) from None
            count = self.db.execute("SELECT count(*) FROM dispatch_tasks WHERE owner=?", (p.owner,)).fetchone()[0]
            if count >= self.store.limit:
                raise Conflict("queue_capacity")
            # OpenBot retains the target's own chat history beyond our task retention.
            self.db.execute("INSERT OR IGNORE INTO dispatch_target_owners(target,owner) VALUES (?,?)",
                            (a["target"], p.owner))
            self.db.execute("""INSERT INTO dispatch_tasks
                (task_id, owner, subject, client, idempotency_key, target, content, state, created, expires, due)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)""",
                (task_id, *identity, a["target"], a["content"], now, now + TASK_TTL, now))
            return self._public(self._one("SELECT * FROM dispatch_tasks WHERE task_id=?", (task_id,)))

    def get_task(self, p, a):
        with self.store.transaction():
            self.store.maintain_dispatch(self.store.clock())
            return self._public(self._owned(p, a["task_id"]))

    def cancel_task(self, p, a):
        with self.store.transaction():
            self.store.maintain_dispatch(self.store.clock())
            task = self._owned(p, a["task_id"])
            if task["state"] in ("submitting", "running"):
                raise Conflict("task_in_flight")
            result = self._public(task)
            if task["state"] == "queued":
                self.db.execute("UPDATE dispatch_tasks SET state='cancelled', due=NULL WHERE task_id=?",
                                (task["task_id"],))
                result["state"] = "cancelled"
            return result

    def work_once(self, adapter):
        with self.store.transaction():
            now = self.store.clock()
            self.store.maintain_dispatch(now)
            task = self._one("""SELECT * FROM dispatch_tasks WHERE due <= ?
                                ORDER BY due, rowid LIMIT 1""", (now,))
            if task is None:
                return False
            task_id = task["task_id"]
            principal = Principal(task["subject"], task["client"], task["owner"], "dot",
                                  frozenset({DISPATCH_SCOPE}))
            try:
                if task["target"] not in self._targets(principal):
                    raise Denied()
            except Denied:
                state = "cancelled" if task["state"] == "queued" else "unknown"
                self.db.execute("""UPDATE dispatch_tasks SET state=?, reason='authorization_revoked',
                                   due=NULL WHERE task_id=?""", (state, task_id))
                return True
            generation = task["generation"] + 1
            state = "running" if task["delivery_id"] else "submitting"
            self.db.execute("UPDATE dispatch_tasks SET state=?, generation=?, due=? WHERE task_id=?",
                            (state, generation, now + ATTEMPT_LEASE, task_id))

        # A crash here leaves a durable claim. Recovery sends the same task UUID,
        # or polls the saved delivery ID. No SQLite lock is held during host I/O.
        delivery_id = task["delivery_id"]
        result = reason = next_delay = None
        failures = 0
        try:
            if delivery_id is None:
                delivery_id = adapter.send(task["target"], task_id, task["content"])
                state, next_delay = "running", POLL_INTERVAL
            else:
                status, answer = adapter.poll(task["target"], delivery_id)
                if status in ("queued", "starting", "running"):
                    state, next_delay = "running", POLL_INTERVAL
                elif status in ("completed", "failed", "interrupted", "cancelled"):
                    state = status
                    result = answer if status == "completed" else None
                else:
                    state, reason = "unknown", "openbot_invalid_response"
        except OpenBotError as error:
            reason = error.reason
            if error.retryable:
                failures = task["failures"] + 1
                next_delay = min(60, 2 ** min(6, failures))
            else:
                # After an earlier ambiguous POST, a later rejection cannot prove
                # that OpenBot never started the original delivery.
                rejected = task["state"] == "queued" and error.reason in DEFINITE_REJECTIONS
                state = "failed" if rejected else "unknown"

        with self.store.transaction():
            now = self.store.clock()
            due = None if next_delay is None else now + next_delay
            # An expired lease can be taken by another worker. Its outcome wins.
            self.db.execute("""UPDATE dispatch_tasks SET state=?, delivery_id=?, result=?, reason=?,
                               due=?, failures=? WHERE task_id=? AND generation=? AND due IS NOT NULL""",
                            (state, delivery_id, result, reason, due, failures, task_id, generation))
            self.store.maintain_dispatch(now)
        return True
