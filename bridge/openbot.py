"""Same-user OpenBot Local scripts adapter for sending messages and reading their deliveries."""
import errno
import http.client
import json
import os
import re
import socket
import stat
import threading
import time
from pathlib import Path
from urllib.parse import quote


TIMEOUT = 5
MAX_RESPONSE_BYTES = 1024 * 1024
DELIVERY_STATES = {"queued", "starting", "running", "completed", "failed", "interrupted", "cancelled"}


class OpenBotError(Exception):
    def __init__(self, reason, retryable=False):
        self.reason = reason
        self.retryable = retryable
        super().__init__(reason)


def valid_id(value):
    return (isinstance(value, str) and value not in (".", "..")
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None)


def _credential(directory_fd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > 1024):
            raise OpenBotError("openbot_invalid_credentials")
        data = stream.read(1025)
        if len(data) > 1024:
            raise OpenBotError("openbot_invalid_credentials")
        return data.decode("ascii").removesuffix("\n").removesuffix("\r")


def _connection_details(directory):
    # Pin both reads to the same owned directory and validate each opened descriptor.
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise OpenBotError("openbot_invalid_credentials")
            url = _credential(fd, "url")
            token = _credential(fd, "token")
        finally:
            os.close(fd)
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ESTALE, errno.EINTR):
            raise OpenBotError("openbot_unavailable", True) from None
        raise OpenBotError("openbot_invalid_credentials") from None
    except UnicodeError:
        raise OpenBotError("openbot_invalid_credentials") from None
    port = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", url)
    if (not port or not 1024 <= int(port[1]) <= 65535
            or not re.fullmatch(r"[A-Za-z0-9_~-][A-Za-z0-9._~+/=-]{0,511}", token)):
        raise OpenBotError("openbot_invalid_credentials")
    return int(port[1]), token


def _raise_status(status):
    if status == 401 or status in (408, 409, 425, 429) or 500 <= status <= 599:
        raise OpenBotError("openbot_unavailable", True)
    reason = {400: "openbot_invalid_request", 403: "openbot_forbidden",
              404: "openbot_not_found", 413: "openbot_message_too_large",
              415: "openbot_invalid_request", 422: "openbot_invalid_request"}
    raise OpenBotError(reason.get(status, "openbot_invalid_response"))


class OpenBot:
    def __init__(self, directory):
        self.directory = Path(directory)
        if not self.directory.is_absolute():
            raise ValueError("OpenBot automation directory must be absolute")

    def _request(self, method, path, expected_status, body=None):
        port, token = _connection_details(self.directory)
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
        deadline = time.monotonic() + TIMEOUT
        watchdog = None
        try:
            connection.connect()
            active_socket = connection.sock

            def abort():
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            # HTTPConnection's timeout alone restarts whenever another byte arrives.
            watchdog = threading.Timer(max(0, deadline - time.monotonic()), abort)
            watchdog.daemon = True
            watchdog.start()
            connection.request(method, path, body=body, headers=headers)
            with connection.getresponse() as response:
                if response.status != expected_status:
                    _raise_status(response.status)
                expected_length = response.length
                if expected_length is not None and expected_length > MAX_RESPONSE_BYTES:
                    raise OpenBotError("openbot_response_too_large")
                data = response.read(MAX_RESPONSE_BYTES + 1)
            if time.monotonic() >= deadline or (expected_length is not None and len(data) < expected_length):
                raise OpenBotError("openbot_unavailable", True)
            if len(data) > MAX_RESPONSE_BYTES:
                raise OpenBotError("openbot_response_too_large")
            result = json.loads(data.decode("utf-8"))
            if not isinstance(result, dict):
                raise OpenBotError("openbot_invalid_response")
            return result
        except (OSError, http.client.HTTPException):
            raise OpenBotError("openbot_unavailable", True) from None
        except (ValueError, RecursionError):
            raise OpenBotError("openbot_invalid_response") from None
        finally:
            if watchdog is not None:
                watchdog.cancel()
            connection.close()

    def send(self, target, task_id, content):
        if not valid_id(target) or not valid_id(task_id) or not isinstance(content, str) or not content.strip():
            raise OpenBotError("openbot_invalid_request")
        try:
            body = json.dumps({"text": content, "clientMessageId": task_id},
                              ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except UnicodeError:
            raise OpenBotError("openbot_invalid_request") from None
        if len(body) > 32 * 1024:
            raise OpenBotError("openbot_message_too_large")
        result = self._request("POST", f"/v1/agents/{quote(target, safe='')}/messages", 202, body)
        deliveries = result.get("deliveries")
        if not valid_id(result.get("messageId")) or not isinstance(deliveries, list):
            raise OpenBotError("openbot_invalid_response")
        matches = [d for d in deliveries if isinstance(d, dict) and d.get("recipientAgentId") == target]
        if len(matches) != 1 or not valid_id(matches[0].get("id")):
            raise OpenBotError("openbot_invalid_response")
        return matches[0]["id"]

    def poll(self, target, delivery_id):
        if not valid_id(target) or not valid_id(delivery_id):
            raise OpenBotError("openbot_invalid_request")
        path = f"/v1/agents/{quote(target, safe='')}/deliveries/{quote(delivery_id, safe='')}"
        result = self._request("GET", path, 200)
        status = result.get("status")
        if (result.get("id") != delivery_id or result.get("recipientAgentId", target) != target
                or not isinstance(status, str) or status not in DELIVERY_STATES):
            raise OpenBotError("openbot_invalid_response")
        if status != "completed":
            return status, None
        answer = result.get("answer")
        if "answer" not in result or (answer is not None and not isinstance(answer, str)):
            raise OpenBotError("openbot_invalid_response")
        if answer is not None:
            try:
                answer.encode("utf-8")  # JSON escapes can represent unpaired surrogates.
            except UnicodeError:
                raise OpenBotError("openbot_invalid_response") from None
        return status, answer
