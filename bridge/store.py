"""SQLite mailbox and durable outbox. One process/worker per database."""
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from cryptography.fernet import Fernet

from .auth import Denied, InsufficientScope, Principal
from .callback import CallbackError, headers, signing_key, validate_url

TERMINAL = {"completed", "failed", "cancelled", "expired"}
MAX_RESPONSES = 32
REQUEST_RESPONSES = """FROM records WHERE kind='response' AND owner=?
                       AND json_extract(body, '$.request_id')=?"""


class Conflict(Exception):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, path, key, policy, callback, clock=time.time, limit=1000):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, owner TEXT, body TEXT, PRIMARY KEY(kind,id))")
        self.db.execute("CREATE INDEX IF NOT EXISTS records_owner ON records(kind,owner)")
        self.db.execute("""CREATE INDEX IF NOT EXISTS response_request_revision ON records(
            owner, json_extract(body, '$.request_id'), json_extract(body, '$.revision')
        ) WHERE kind='response'""")
        self.lock = threading.RLock()
        self.cipher, self.policy, self.callback = Fernet(key), policy, callback
        self.clock, self.limit = clock, limit

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def get(self, kind, id, owner):
        row = self.db.execute("SELECT body FROM records WHERE kind=? AND id=? AND owner=?", (kind, id, owner)).fetchone()
        if row is None:
            raise Conflict("not_found")
        return json.loads(row[0])

    def all(self, kind, owner=None):
        query = "SELECT body FROM records WHERE kind=?"
        args = [kind]
        if owner is not None:
            query += " AND owner=?"
            args.append(owner)
        return [json.loads(r[0]) for r in self.db.execute(query, args)]

    def put(self, kind, item):
        self.db.execute("INSERT INTO records VALUES (?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
                        (kind, item["id"], item["owner"], canonical(item)))

    def delete(self, kind, item):
        self.db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, item["id"]))

    def authorize(self, p, *scopes):
        self.policy.recheck(p)
        if not set(scopes) <= p.scopes:
            raise InsufficientScope(scopes)

    def maintain(self, now=None):
        if now is None:
            now = self.clock()
        for r in self.all("request"):
            if r["created"] + 7 * 86400 <= now:
                ids = self.db.execute("SELECT id " + REQUEST_RESPONSES, (r["owner"], r["id"])).fetchall()
                self.delete("request", r)
                for (response_id,) in ids:
                    self.delete("response", {"id": response_id})
                continue
            if r["expires"] <= now and r["state"] not in TERMINAL:
                r["state"] = "expired"
                r["revision"] += 1
                self.put("request", r)
        active_subscriptions = set()
        for sub in self.all("subscription"):
            if sub["expires"] + 86400 <= now:
                self.delete("subscription", sub)
            elif sub["active"] and sub["expires"] > now:
                active_subscriptions.add(sub["id"])
        for event in self.all("outbox"):
            if event["created"] + 7 * 86400 <= now:
                self.delete("outbox", event)
            elif event["status"] == "pending" and event["subscription_id"] not in active_subscriptions:
                # A later registration can reuse this ID, but cannot revive its old deliveries.
                event["status"] = "stopped"
                self.put("outbox", event)
        active_conversations = {r["conversation_id"] for r in self.all("request")}
        for c in self.all("conversation"):
            if c["id"] not in active_conversations:
                self.delete("conversation", c)

    def submit(self, p, a):
        with self.transaction():
            self.authorize(p, "bridge:submit")
            self.maintain()
            digest = hashlib.sha256(canonical(a).encode()).hexdigest()
            for r in self.all("request", p.owner):
                if r["idempotency_key"] == a["idempotency_key"]:
                    if r["digest"] != digest:
                        raise Conflict("idempotency_key_reused")
                    return r
            if len(self.all("request", p.owner)) >= self.limit:
                raise Conflict("queue_capacity")
            cid = a.get("conversation_id")
            if cid:
                c = self.get("conversation", cid, p.owner)
            else:
                c = {"id": str(uuid.uuid4()), "owner": p.owner, "sequence": 0}
            # Serialize each conversation, so a later reply cannot race earlier work.
            if any(r["conversation_id"] == c["id"] and r["state"] not in TERMINAL | {"needs_input"}
                   for r in self.all("request", p.owner)):
                raise Conflict("conversation_busy")
            c["sequence"] += 1
            self.put("conversation", c)
            now = self.clock()
            r = {"id": str(uuid.uuid4()), "owner": p.owner, "conversation_id": c["id"],
                 "sequence": c["sequence"], "content": a["content"], "state": "queued", "revision": 0,
                 "created": now, "expires": now + a.get("ttl_seconds", 86400),
                 "idempotency_key": a["idempotency_key"], "digest": digest}
            # A follow-up supersedes the request waiting for input, retaining its response in context.
            for old in self.all("request", p.owner):
                if old["conversation_id"] == c["id"] and old["state"] == "needs_input":
                    old["state"] = "cancelled"
                    old["reason"] = "superseded_by_followup"
                    old["revision"] += 1
                    self.put("request", old)
            self.put("request", r)
            for s in self.all("subscription", p.owner):
                if s["active"] and s["expires"] > now:
                    self.enqueue(r, s)
            return r

    def enqueue(self, r, s):
        event_id = "evt_" + hashlib.sha256((r["id"] + s["id"]).encode()).hexdigest()
        event = {"eventId": event_id, "name": "request.created", "timestamp": iso(r["created"]),
                 "data": {"request_id": r["id"]}, "cursor": None}
        self.put("outbox", {"id": event_id, "owner": r["owner"], "request_id": r["id"],
                            "subscription_id": s["id"], "event": event, "created": r["created"],
                            "attempts": 0, "due": self.clock(), "status": "pending"})

    def read(self, p, a):
        with self.transaction():
            self.authorize(p, "bridge:read")
            self.maintain()
            r = self.get("request", a["request_id"], p.owner)
            context = sorted([x for x in self.all("request", p.owner) if x["conversation_id"] == r["conversation_id"]
                              and x["sequence"] <= r["sequence"]], key=lambda x: x["sequence"])
            # Bounded pages: explicit earlier cursor avoids silent context loss.
            before = a.get("before_sequence", r["sequence"] + 1)
            context = [x for x in context if x["sequence"] < before][-10:]
            latest = []
            for entry in context:
                row = self.db.execute("SELECT body " + REQUEST_RESPONSES +
                                      " ORDER BY json_extract(body, '$.revision') DESC LIMIT 1",
                                      (p.owner, entry["id"])).fetchone()
                if row:
                    latest.append(json.loads(row[0]))
            deliveries = [{k: e.get(k) for k in ("id", "status", "attempts", "last_http_status")}
                          for e in self.all("outbox", p.owner) if e["request_id"] == r["id"]]
            return {"request": r, "context": context, "latest_responses": latest, "deliveries": deliveries,
                    "next_before_sequence": context[0]["sequence"] if context and context[0]["sequence"] > 1 else None}

    def list_requests(self, p, a):
        with self.transaction():
            self.authorize(p, "bridge:read")
            self.maintain()
            rows = sorted(self.all("request", p.owner), key=lambda r: r["id"])
            rows = [r for r in rows if r["id"] > a.get("after", "")]
            page = rows[:20]
            return {"requests": [{k: r[k] for k in ("id", "conversation_id", "sequence", "state", "revision")} for r in page],
                    "next_after": page[-1]["id"] if len(rows) > 20 else None}

    def _response_page(self, request, after_revision):
        responses = []
        if after_revision < request["revision"]:
            rows = self.db.execute("SELECT body " + REQUEST_RESPONSES +
                                   " AND json_extract(body, '$.revision') > ?"
                                   " ORDER BY json_extract(body, '$.revision') LIMIT 11",
                                   (request["owner"], request["id"], after_revision))
            responses = [json.loads(row[0]) for row in rows]
        return {"responses": responses[:10],
                "next_after_revision": responses[9]["revision"] if len(responses) > 10 else None}

    def list_responses(self, p, a):
        with self.transaction():
            self.authorize(p, "bridge:read")
            self.maintain()
            request = self.get("request", a["request_id"], p.owner)
            return self._response_page(request, a.get("after_revision", 0))

    def wait_snapshot(self, p, a):
        """Short transaction only; HTTP layer sleeps without holding SQLite lock."""
        with self.transaction():
            self.authorize(p, "bridge:submit", "bridge:read")
            self.maintain()
            r = self.get("request", a["request_id"], p.owner)
            page = self._response_page(r, a.get("after_revision", 0))
            subscriptions = 0
            for sub in self.all("subscription", p.owner):
                if not sub["active"] or sub["expires"] <= self.clock():
                    continue
                try:
                    principal = self.policy.resolve(sub["subject"], sub["client"], sub["scopes"])
                    self.authorize(principal, "bridge:subscribe")
                    if principal.owner == sub["owner"] and principal.role == "dot":
                        subscriptions += 1
                except Denied:
                    pass
            return {"request_id": r["id"], "conversation_id": r["conversation_id"],
                    "state": r["state"], "revision": r["revision"], **page,
                    "ready": bool(page["responses"]) or r["state"] in TERMINAL | {"needs_input"},
                    "timed_out": False, "active_subscriptions": subscriptions}

    def respond(self, p, a):
        with self.transaction():
            self.authorize(p, "bridge:respond")
            self.maintain()
            r = self.get("request", a["request_id"], p.owner)
            digest = hashlib.sha256(canonical(a).encode()).hexdigest()
            row = self.db.execute("SELECT body " + REQUEST_RESPONSES +
                                  " AND json_extract(body, '$.idempotency_key')=?",
                                  (p.owner, r["id"], a["idempotency_key"])).fetchone()
            if row:
                old = json.loads(row[0])
                if old["digest"] != digest:
                    raise Conflict("idempotency_key_reused")
                return old
            if r["state"] in TERMINAL | {"needs_input"} or r["revision"] != a["expected_revision"]:
                raise Conflict("stale_or_terminal_request")
            # On an open request each revision is a response. Older stores could
            # already have 32 progress replies; allow their single closing reply too.
            if a["state"] == "processing" and r["revision"] >= MAX_RESPONSES - 1:
                raise Conflict("response_capacity")
            r["state"], r["revision"] = a["state"], r["revision"] + 1
            response = {"id": str(uuid.uuid4()), "owner": p.owner, "request_id": r["id"],
                        "content": a["content"], "state": r["state"], "revision": r["revision"],
                        "created": self.clock(), "idempotency_key": a["idempotency_key"], "digest": digest}
            self.put("response", response)
            self.put("request", r)
            return response

    def cancel(self, p, a):
        with self.transaction():
            self.authorize(p, "bridge:submit")
            self.maintain()
            r = self.get("request", a["request_id"], p.owner)
            if r["state"] not in TERMINAL:
                r["state"], r["revision"] = "cancelled", r["revision"] + 1
                self.put("request", r)
            return r

    def subscription_id(self, p, a):
        return "sub_" + hashlib.sha256(canonical([p.subject, p.client, p.owner, a["name"], a["arguments"], a["delivery"]["url"]]).encode()).hexdigest()

    def subscribe(self, p, a):
        with self.transaction():
            self.authorize(p, "bridge:subscribe")
            now = self.clock()
            self.maintain(now)
            secret, url = a["delivery"]["secret"], a["delivery"]["url"]
            signing_key(secret)
            validate_url(url, self.callback.allowed_hosts)
            sid = self.subscription_id(p, a)
            subscriptions = self.all("subscription", p.owner)
            old = next((s for s in subscriptions if s["id"] == sid), None)
            active = [s for s in subscriptions if s["active"] and s["expires"] > now]
            if old not in active and len(active) >= 4:
                raise Conflict("subscription_capacity")
            if old is None and len(subscriptions) >= 4:
                retired = (s for s in subscriptions if s not in active)
                self.delete("subscription", min(retired, key=lambda s: (s["expires"], s["id"])))
            if not old or old["verified_until"] <= now or not old["active"]:
                self.callback.verify(url, secret, sid, now)
                verified_until = now + 300
            else:
                verified_until = old["verified_until"]
            ttl = min(a.get("ttlMs") or 86400000, 86400000) / 1000
            encrypted = self.cipher.encrypt(secret.encode()).decode()
            sub = {"id": sid, "owner": p.owner, "subject": p.subject, "client": p.client,
                   "scopes": sorted(p.scopes), "url": url, "secret": encrypted, "expires": now + ttl,
                   "verified_until": verified_until, "active": True, "previous_secret": None, "rotate_until": 0}
            if old:
                previous = self.cipher.decrypt(old["secret"].encode()).decode()
                if previous != secret:
                    sub["previous_secret"], sub["rotate_until"] = old["secret"], now + 300
                elif old["rotate_until"] > now:
                    sub["previous_secret"], sub["rotate_until"] = old["previous_secret"], old["rotate_until"]
            self.put("subscription", sub)
            return {"id": sid, "refreshBefore": iso(sub["expires"]), "cursor": None, "truncated": False}

    def unsubscribe(self, p, a):
        with self.transaction():
            self.authorize(p, "bridge:subscribe")
            sid = self.subscription_id(p, a)
            for s in self.all("subscription", p.owner):
                if s["id"] == sid:
                    s["active"] = False
                    self.put("subscription", s)
                    for e in self.all("outbox", p.owner):
                        if e["subscription_id"] == sid and e["status"] == "pending":
                            e["status"] = "stopped"
                            self.put("outbox", e)
            return {}

    def deliver_once(self):
        # Transaction spans the bounded POST: single worker, no duplicate concurrent sends.
        # Crash after POST rolls back attempt; stable eventId makes the replay detectable.
        with self.transaction():
            self.maintain()
            now = self.clock()
            due = sorted([e for e in self.all("outbox") if e["status"] == "pending" and e["due"] <= now], key=lambda e: e["due"])
            if not due:
                return False
            e = due[0]
            try:
                s = self.get("subscription", e["subscription_id"], e["owner"])
                r = self.get("request", e["request_id"], e["owner"])
                p = Principal(s["subject"], s["client"], s["owner"], "dot", frozenset(s["scopes"]))
                self.authorize(p, "bridge:subscribe")
                if not s["active"] or s["expires"] <= now or r["state"] in TERMINAL | {"needs_input"}:
                    raise Conflict("inactive")
            except (Conflict, Denied):
                e["status"] = "stopped"
                self.put("outbox", e)
                return True
            body = canonical(e["event"]).encode()
            keys = [self.cipher.decrypt(s["secret"].encode()).decode()]
            if s["previous_secret"] and s["rotate_until"] > now:
                keys.append(self.cipher.decrypt(s["previous_secret"].encode()).decode())
            e["attempts"] += 1
            try:
                status, _ = self.callback.post(s["url"], body, headers(body, e["id"], s["id"], keys, now))
            except CallbackError:
                status = 0
            if 200 <= status < 300:
                e["status"] = "delivered"
                if r["state"] == "queued":
                    r["state"] = "delivered"  # Not completion, and does not change response revision.
                    self.put("request", r)
            elif status in (410, 413) or (400 <= status < 500 and status not in (408, 429)) or e["attempts"] >= 6:
                e["status"] = "failed"
                if status == 410:
                    s["active"] = False
                    self.put("subscription", s)
            else:
                # Start retry backoff after the callback finishes, not before the POST.
                e["due"] = self.clock() + min(3600, 5 * 2 ** (e["attempts"] - 1))
            e["last_http_status"] = status
            self.put("outbox", e)
            return True
