"""OAuth resource server only: tokens are issued by an external provider."""
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import jwt

SCOPES = {"hermes": {"bridge:submit", "bridge:read"},
          "dot": {"bridge:read", "bridge:respond", "bridge:subscribe"}}


class Denied(Exception):
    pass


class AuthenticationRequired(Denied):
    """Credentials stopped authenticating an already accepted bounded wait."""


class InsufficientScope(Denied):
    def __init__(self, scopes):
        self.scopes = tuple(sorted(scopes))


@dataclass(frozen=True)
class Principal:
    subject: str
    client: str
    owner: str
    role: str
    scopes: frozenset[str]


class Policy:
    """Reload local operator grants for every call and every event attempt.

    Operator edits are atomic file replacements. No remotely callable grant tool.
    """
    def __init__(self, path: str):
        self.path = Path(path)

    def resolve(self, subject: str, client: str, scopes) -> Principal:
        try:
            entries = json.loads(self.path.read_text())["grants"]
            matches = [g for g in entries if g["subject"] == subject and g["client_id"] == client]
            if len(matches) != 1:
                raise Denied()
            g = matches[0]
            if g.get("enabled") is not True or g.get("queue_authorized") is not True:
                raise Denied()
            if g["role"] not in SCOPES or not isinstance(g["owner"], str) or not g["owner"]:
                raise Denied()
            return Principal(subject, client, g["owner"], g["role"], frozenset(scopes) & SCOPES[g["role"]])
        except (OSError, ValueError, KeyError, TypeError):
            raise Denied() from None

    def recheck(self, p: Principal):
        current = self.resolve(p.subject, p.client, p.scopes)
        if current != p:
            raise Denied()
        return current


def validate_https(url):
    if not isinstance(url, str):
        raise ValueError("URL must be a string")
    u = urlsplit(url)
    if (u.scheme != "https" or not u.hostname or u.username or u.password
            or u.fragment or u.query or any(ord(c) <= 32 for c in url)):
        raise ValueError("Expected an explicit HTTPS URL without credentials/query/fragment")


def validate_local_resource(url):
    if not isinstance(url, str):
        raise ValueError("Local resource must be a string")
    u = urlsplit(url)
    if (u.scheme not in ("http", "https") or u.hostname != "127.0.0.1" or u.port is None
            or not 1024 <= u.port <= 65535 or u.path != "/mcp"
            or u.username or u.password or u.query or u.fragment):
        raise ValueError("Local resource must be http(s)://127.0.0.1:PORT/mcp, port 1024-65535")
    return u.port


class JWTAuth:
    def __init__(self, issuer, audience, jwks_url, policy, client_claim="client_id", *, allow_loopback=False, tunnel_resource=None):
        if client_claim not in ("client_id", "azp"):
            raise ValueError("Unsupported client claim")
        self.client_claim = client_claim
        for url in (issuer, jwks_url):
            validate_https(url)
        if allow_loopback:
            validate_local_resource(audience)
        else:
            validate_https(audience)
        audiences = [audience]
        if tunnel_resource:
            validate_https(tunnel_resource)
            audiences.append(tunnel_resource)
        self.issuer, self.audience, self.policy = issuer, audiences, policy
        # Pinned operator URL, never a token-supplied jku/x5u URL. Short bounded cache.
        self.keys = jwt.PyJWKClient(jwks_url, lifespan=300, timeout=5)

    def __call__(self, authorization):
        try:
            if not authorization or not authorization.startswith("Bearer "):
                raise Denied()
            token = authorization[7:]
            if len(token) > 16384:
                raise Denied()
            key = self.keys.get_signing_key_from_jwt(token).key
            c = jwt.decode(token, key, algorithms=["RS256"], issuer=self.issuer,
                           audience=self.audience, options={"require": ["exp", "iat", "sub", "iss", "aud"]})
            if not isinstance(c["sub"], str) or not isinstance(c.get("scope"), str):
                raise Denied()
            # Configure provider's access-token client_id claim; never infer role from requested scopes.
            client = c.get(self.client_claim)
            if not isinstance(client, str) or not client:
                raise Denied()
            return self.policy.resolve(c["sub"], client, c["scope"].split())
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise Denied() from None
