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


class JWTAuth:
    def __init__(self, issuer, audience, jwks_url, policy, client_claim="client_id"):
        if client_claim not in ("client_id", "azp"):
            raise ValueError("Unsupported client claim")
        self.client_claim = client_claim
        for url in (issuer, audience, jwks_url):
            if urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
                raise ValueError("OAuth issuer, resource and JWKS must be HTTPS")
        self.issuer, self.audience, self.policy = issuer, audience, policy
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
