"""Access tokens: verify, then read who is asking (DD-99 · LD-07 chapter 6).

The service only verifies tokens Keycloak issued -- signature (the realm's keys, fetched and cached), audience,
issuer, expiry -- it never logs anyone in. Claims read (realm oss911, 23-keycloak-realm.yml):
    realm_access.roles   one or more of the four roles
    agent_no             the agent number, for the "only my own rows" filter (editable by administrators only)
    agency_type_code     the department, when the directory provides it (otherwise meta.user_department_map)
    preferred_username   who, for the audit (sub when absent)
    azp                  the client that obtained the token, recorded as the audit's client_kind
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import jwt

from .errors import RoleMissing, Unauthenticated

# A user holding several roles acts with the broadest; their scopes are nested (admin > supervisor > dispatcher >
# call_taker in what they may read), so this never shows more than one of the user's own roles allows.
ROLE_ORDER = ["admin", "supervisor", "dispatcher", "call_taker"]


@dataclass(frozen=True)
class Principal:
    user: str
    role: str
    agent_no: str | None
    department_claim: str | None
    client: str | None


class TokenVerifier:
    """key_for(token) returns the verification key; production uses the realm's JWKS, tests a fixed key."""

    def __init__(self, issuer: str, audience: str, key_for: Callable[[str], object]):
        self.issuer, self.audience, self.key_for = issuer, audience, key_for

    @classmethod
    def from_jwks(cls, issuer: str, audience: str, jwks_url: str) -> "TokenVerifier":
        client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)
        return cls(issuer, audience, lambda token: client.get_signing_key_from_jwt(token).key)

    def principal(self, authorization: str | None) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise Unauthenticated("an access token is required (Authorization: Bearer ...)")
        token = authorization[7:].strip()
        try:
            claims = jwt.decode(token, self.key_for(token), algorithms=["RS256"], audience=self.audience,
                                issuer=self.issuer, options={"require": ["exp", "iat", "iss", "aud"]})
        except jwt.PyJWKClientError as exc:
            raise Unauthenticated(f"the token's signing key could not be obtained: {exc}") from None
        except jwt.InvalidTokenError as exc:
            raise Unauthenticated(f"the token is not valid: {exc}") from None
        roles = set((claims.get("realm_access") or {}).get("roles") or [])
        role = next((r for r in ROLE_ORDER if r in roles), None)
        user = claims.get("preferred_username") or claims.get("sub")
        if not user:
            raise Unauthenticated("the token names no user")
        if role is None:
            raise RoleMissing(f"user {user} has none of the roles {ROLE_ORDER}")

        def text(name):
            value = claims.get(name)
            return value.strip() if isinstance(value, str) and value.strip() else None

        return Principal(user=user, role=role, agent_no=text("agent_no"),
                         department_claim=text("agency_type_code"), client=text("azp"))
