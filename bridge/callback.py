"""Signed HTTPS POST with DNS pinning, TLS hostname checks, no redirects/proxies."""
import base64
import hmac
import http.client
import ipaddress
import json
import re
import secrets
import socket
import ssl
import time
import threading

import dns.exception
import dns.resolver
from urllib.parse import urlsplit


class CallbackError(Exception):
    def __init__(self, reason="unreachable"):
        self.reason = reason
        super().__init__(reason)


def signing_key(secret):
    try:
        if not isinstance(secret, str) or not secret.startswith("whsec_"):
            raise ValueError()
        key = base64.b64decode(secret[6:], validate=True)
        if not 24 <= len(key) <= 64:
            raise ValueError()
        return key
    except (ValueError, TypeError):
        raise CallbackError("invalid_secret") from None


def headers(body, event_id, subscription_id, keys, now):
    stamp = str(int(now))
    signed = event_id.encode() + b"." + stamp.encode() + b"." + body
    signatures = ["v1," + base64.b64encode(hmac.digest(signing_key(k), signed, "sha256")).decode() for k in keys]
    return {"Content-Type": "application/json", "webhook-id": event_id,
            "webhook-timestamp": stamp, "webhook-signature": " ".join(signatures),
            "X-MCP-Subscription-Id": subscription_id}


def validate_url(url, allowed_hosts):
    try:
        u = urlsplit(url)
        if (u.scheme != "https" or u.hostname not in allowed_hosts or u.port not in (None, 443)
                or u.username or u.password or u.fragment or len(url) > 2048
                or any(ord(c) < 33 or ord(c) > 126 for c in url)):
            raise ValueError()
        return u
    except (ValueError, TypeError):
        raise CallbackError("invalid_url") from None


def subscription_diagnostic(url, reason, allowed_hosts):
    """Bounded hostname and fixed reason only; never return raw URL/error text."""
    safe_reasons = {"invalid_secret", "invalid_url", "dns_failed", "non_public_address",
                    "payload_too_large", "response_too_large", "unreachable", "challenge_failed"}
    reason = reason if reason in safe_reasons else "validation_failed"
    host = "unavailable"
    try:
        if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in url):
            return host, reason
        parsed = urlsplit(url)
        candidate = parsed.hostname
        if not candidate or len(candidate) > 253:
            return host, reason
        try:
            host = ipaddress.ip_address(candidate).compressed
        except ValueError:
            normalized = candidate.lower().rstrip(".")
            if all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in normalized.split(".")):
                host = normalized
        if reason == "invalid_url" and host != "unavailable" and candidate not in allowed_hosts:
            # Distinguish allowlist rejection only when all other URL checks pass.
            validate_url(url, {candidate})
            reason = "host_not_allowed"
    except (ValueError, TypeError, CallbackError):
        pass
    return host, reason


def public_addresses(host):
    try:
        literal = ipaddress.ip_address(host)
        ips = [literal]
    except ValueError:
        ips = []
        deadline = time.monotonic() + 3
        resolver = dns.resolver.Resolver()
        for kind in ("A", "AAAA"):
            try:
                answer = resolver.resolve(host + ".", kind, search=False,
                                          lifetime=max(0.01, deadline - time.monotonic()))
                ips.extend(ipaddress.ip_address(record.address) for record in answer)
            except dns.resolver.NoAnswer:
                continue
            except dns.exception.DNSException:
                raise CallbackError("dns_failed") from None
    if not ips:
        raise CallbackError("dns_failed")
    addresses = []
    for ip in ips:
        if (not ip.is_global or ip.is_multicast or ip.is_reserved
                or getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None)
                or getattr(ip, "teredo", None)):
            raise CallbackError("non_public_address")
        family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
        addr = (str(ip), 443) if ip.version == 4 else (str(ip), 443, 0, 0)
        addresses.append((family, socket.SOCK_STREAM, 0, "", addr))
    return addresses


class Callback:
    def __init__(self, allowed_hosts):
        if not allowed_hosts:
            raise ValueError("Explicit callback host allowlist required")
        self.allowed_hosts = frozenset(allowed_hosts)

    def post(self, url, body, request_headers):
        u = validate_url(url, self.allowed_hosts)
        if len(body) > 262144:
            raise CallbackError("payload_too_large")
        conn = None
        raw = None
        timer = None
        active_socket = [None]
        try:
            # Resolve exactly once per attempt; connect to numeric validated sockaddr.
            family, kind, proto, _, address = public_addresses(u.hostname)[0]
            raw = socket.socket(family, kind, proto)
            raw.settimeout(10)
            active_socket[0] = raw
            def abort():
                try:
                    active_socket[0].shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            timer = threading.Timer(10, abort)
            timer.daemon = True
            timer.start()
            raw.connect(address)
            tls = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname,
                                                           do_handshake_on_connect=False)
            active_socket[0] = tls
            tls.do_handshake()
            conn = http.client.HTTPSConnection(u.hostname, timeout=10)
            conn.sock = tls
            path = (u.path or "/") + ("?" + u.query if u.query else "")
            conn.request("POST", path, body, request_headers)
            response = conn.getresponse()
            data = response.read(16385)
            if len(data) > 16384:
                raise CallbackError("response_too_large")
            return response.status, data  # 3xx never followed
        except (OSError, http.client.HTTPException):
            raise CallbackError("unreachable") from None
        finally:
            if timer:
                timer.cancel()
            if active_socket[0]:
                active_socket[0].close()
            if conn:
                conn.close()
            elif raw:
                raw.close()

    def verify(self, url, secret, subscription_id, now):
        challenge = secrets.token_urlsafe(32)
        body = json.dumps({"type": "verification", "challenge": challenge}, separators=(",", ":")).encode()
        started = time.monotonic()
        status, data = self.post(url, body, headers(body, "verify_" + secrets.token_hex(16), subscription_id, [secret], now))
        try:
            echoed = json.loads(data).get("challenge")
            if not (200 <= status < 300 and time.monotonic() - started < 15
                    and isinstance(echoed, str) and hmac.compare_digest(challenge, echoed)):
                raise ValueError()
        except (ValueError, AttributeError):
            raise CallbackError("challenge_failed") from None
