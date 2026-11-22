import json
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from bridge.openbot import OpenBot, OpenBotError


@contextmanager
def listener(replies):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append((self.command, self.path, self.headers.get("Authorization"),
                          json.loads(data) if data else None))
            status, result = replies.pop(0)
            if callable(result):
                result(self)
                return
            body = json.dumps(result).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = handle_request

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        worker.start()
        try:
            yield server.server_port, calls
        finally:
            server.shutdown()
            worker.join()


TARGET = "agent-one"
TASK = "bridge-task-1"
DELIVERY = "delivery:001"


def receipt(target=TARGET, delivery=DELIVERY):
    return {"messageId": "msg-1", "deliveries": [
        {"recipientAgentId": target, "id": delivery, "status": "queued", "position": 1}]}


class OpenBotAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "automation"
        self.directory.mkdir(mode=0o700)
        self.bot = OpenBot(self.directory)

    def set_connection(self, port, token="synthetic-token"):
        for name, text in (("url", f"http://127.0.0.1:{port}"), ("token", token)):
            file = self.directory / name
            file.write_text(text)
            file.chmod(0o600)

    def test_send_and_read_actual_answer_across_restart(self):
        content = "測試 message"
        with listener([(202, receipt()), (401, {"error": "rotating token"})]) as (port, calls):
            self.set_connection(port)
            self.assertEqual(self.bot.send(TARGET, TASK, content), DELIVERY)
            with self.assertRaises(OpenBotError) as err:
                self.bot.poll(TARGET, DELIVERY)
            self.assertTrue(err.exception.retryable)
            self.assertEqual(calls[0], ("POST", "/v1/agents/agent-one/messages", "Bearer synthetic-token",
                                        {"text": content, "clientMessageId": TASK}))
        statuses = ["queued", "starting", "running", "failed", "interrupted", "cancelled", "completed"]
        replies = [(202, receipt())] + [
            (200, {"id": DELIVERY, "status": status, "answer": "完整答案"}) for status in statuses]
        replies.append((200, {"id": DELIVERY, "status": "completed", "answer": None}))
        with listener(replies) as (port, calls):
            self.set_connection(port, "replacement-token")
            self.assertEqual(self.bot.send(TARGET, TASK, content), DELIVERY)
            self.assertEqual(calls[0][3]["clientMessageId"], TASK)
            for status in statuses:
                self.assertEqual(self.bot.poll(TARGET, DELIVERY), (status, "完整答案" if status == "completed" else None))
            self.assertEqual(self.bot.poll(TARGET, DELIVERY), ("completed", None))
            self.assertTrue(all(c[2] == "Bearer replacement-token" for c in calls))
            self.assertTrue(all(c[:2] == ("GET", "/v1/agents/agent-one/deliveries/delivery%3A001") for c in calls[1:]))

    def test_correlates_receipts_and_results(self):
        invalid_receipts = [receipt("different"), receipt(delivery="../unknown"),
                            {"messageId": "", "deliveries": []},
                            {"messageId": "msg-1", "deliveries": receipt()["deliveries"] * 2}]
        invalid_polls = [{"id": "different", "status": "completed", "answer": "private"},
                         {"id": DELIVERY, "recipientAgentId": "other", "status": "completed", "answer": "private"},
                         {"id": DELIVERY, "status": "unsupported"}, {"id": DELIVERY, "status": "completed"},
                         {"id": DELIVERY, "status": "completed", "answer": {"raw": "private"}},
                         {"id": DELIVERY, "status": "completed", "answer": "\ud800"}]
        with listener([(202, r) for r in invalid_receipts] + [(200, r) for r in invalid_polls]) as (port, _):
            self.set_connection(port)
            for _ in invalid_receipts:
                with self.assertRaisesRegex(OpenBotError, "^openbot_invalid_response$"):
                    self.bot.send(TARGET, TASK, "test")
            for _ in invalid_polls:
                with self.assertRaisesRegex(OpenBotError, "^openbot_invalid_response$"):
                    self.bot.poll(TARGET, DELIVERY)

    def test_http_failures_and_redirects(self):
        def redirect(handler):
            handler.send_response(302)
            handler.send_header("Location", "/redirect-target")
            handler.end_headers()

        statuses = [302, 400, 401, 403, 404, 408, 409, 413, 429, 503]
        replies = [(s, redirect if s == 302 else {"error": "private upstream detail"}) for s in statuses]
        with listener(replies) as (port, calls):
            self.set_connection(port)
            for status in statuses:
                with self.assertRaises(OpenBotError) as err:
                    self.bot.send(TARGET, TASK, "private message")
                self.assertEqual(err.exception.retryable, status in (401, 408, 409, 429, 503))
                self.assertNotIn("private", str(err.exception))
            self.assertEqual(len(calls), len(statuses))
            self.assertTrue(all(c[1] == "/v1/agents/agent-one/messages" for c in calls))

    def test_private_files_and_literal_loopback_only(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            OpenBot("relative/automation")
        with self.assertRaises(OpenBotError) as err:
            self.bot.poll(TARGET, DELIVERY)
        self.assertTrue(err.exception.retryable)
        self.set_connection(12345)
        for url in ("http://localhost:12345", "http://127.0.0.2:12345", "http://127.0.0.1:12345/",
                    "http://127.0.0.1:12345?x", "http://127.0.0.1:12345@other", "https://127.0.0.1:12345"):
            (self.directory / "url").write_text(url)
            with self.assertRaisesRegex(OpenBotError, "^openbot_invalid_credentials$"):
                self.bot.poll(TARGET, DELIVERY)
        self.set_connection(12345)
        for item, correct in ((self.directory, 0o700), (self.directory / "url", 0o600),
                              (self.directory / "token", 0o600)):
            item.chmod(correct | 0o040)
            with self.assertRaisesRegex(OpenBotError, "^openbot_invalid_credentials$"):
                self.bot.poll(TARGET, DELIVERY)
            item.chmod(correct)
        token = self.directory / "token"
        token.rename(token.with_suffix(".real"))
        token.symlink_to(token.with_suffix(".real"))
        with self.assertRaisesRegex(OpenBotError, "^openbot_invalid_credentials$"):
            self.bot.poll(TARGET, DELIVERY)
        other = Path(self.temp.name) / "link"
        other.symlink_to(self.directory)
        with self.assertRaisesRegex(OpenBotError, "^openbot_invalid_credentials$"):
            OpenBot(other).poll(TARGET, DELIVERY)

    def test_ids_and_body_limits(self):
        for invalid in (".", "..", "../next", "x%2fsettings", "x?query", "x#fragment", "x" * 129):
            with self.assertRaisesRegex(OpenBotError, "^openbot_invalid_request$"):
                self.bot.poll(TARGET, invalid)
            with self.assertRaisesRegex(OpenBotError, "^openbot_invalid_request$"):
                self.bot.send(invalid, TASK, "test")
        with self.assertRaisesRegex(OpenBotError, "^openbot_message_too_large$"):
            self.bot.send(TARGET, TASK, "\x01" * 8000)
        with listener([(200, {"id": DELIVERY, "status": "completed", "answer": "x" * 64})]) as (port, _):
            self.set_connection(port)
            with patch("bridge.openbot.MAX_RESPONSE_BYTES", 64):
                with self.assertRaisesRegex(OpenBotError, "^openbot_response_too_large$"):
                    self.bot.poll(TARGET, DELIVERY)

    def test_slow_read_has_a_total_deadline(self):
        def slow(server):
            server.send_response(200)
            server.send_header("Content-Length", "1000")
            server.end_headers()
            for _ in range(30):
                try:
                    server.wfile.write(b" ")
                except OSError:
                    break
                time.sleep(.04)

        with listener([(200, slow)]) as (port, _):
            self.set_connection(port)
            with patch("bridge.openbot.TIMEOUT", .2):
                started = time.monotonic()
                with self.assertRaises(OpenBotError) as err:
                    self.bot.poll(TARGET, DELIVERY)
                self.assertTrue(err.exception.retryable)
                self.assertLess(time.monotonic() - started, 1.0)
