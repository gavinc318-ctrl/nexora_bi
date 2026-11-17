"""Access tokens: verify, then read who is asking (DD-99 and its note of 2026-11-17 · LD-07 chapter 6).

Two steps, kept apart because they are audited differently (review item four):
  identify(authorization) -> Identity   the token is Keycloak's and valid: signature (the realm's keys, fetched and
                                        cached), audience, issuer, expiry, and it names a subject. A token that fails
                                        here is only logged -- nobody is known to audit
  Identity.principal()     -> Principal the user has exactly one business role. No role (role_missing) or several
                                        (role_ambiguous) is refused, and that refusal is audited: the identity is
                                        confirmed
Claims read (realm oss911, 23-keycloak-realm.yml):
    sub                  who: the subject, stable under the one trusted issuer (svc.conf SVC_OIDC_ISSUER). The key
                         of meta.user_department_map and the audit's user_principal; a user name can change or be
                         given to someone else, a sub cannot
    preferred_username   for display only (the audit's user_display, /v1/me)
    realm_access.roles   exactly one of the four business roles. Their scopes are not nested -- a call taker sees
                         their own agent rows, a dispatcher does not -- so there is no "broadest" to pick
    agent_no             the agent number, for the "only my own rows" filter (editable by administrators only)
    agency_type_code     the department, when the directory provides it (otherwise meta.user_department_map)
    azp                  the client that obtained the token, recorded as the audit's client_kind
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import jwt

from .errors import RoleAmbiguous, RoleMissing, Unauthenticated

BUSINESS_ROLES = ("admin", "supervisor", "dispatcher", "call_taker")


@dataclass(frozen=True)
class Principal:
    subject: str
    display: str
    role: str
    agent_no: str | None
    department_claim: str | None
    client: str | None


@dataclass(frozen=True)
class Identity:
    """A verified token's user, before their role is settled."""
    subject: str
    display: str
    roles: frozenset[str]           # the business roles in the token, others ignored
    agent_no: str | None
    department_claim: str | None
    client: str | None

    def principal(self) -> Principal:
        if not self.roles:
            raise RoleMissing(f"user {self.display} has none of the roles {list(BUSINESS_ROLES)}")
        if len(self.roles) > 1:
            raise RoleAmbiguous(f"user {self.display} holds {len(self.roles)} business roles "
                                f"({', '.join(sorted(self.roles))}); a user may hold only one",
                                roles=sorted(self.roles))
        return Principal(subject=self.subject, display=self.display, role=next(iter(self.roles)),
                         agent_no=self.agent_no, department_claim=self.department_claim, client=self.client)


class TokenVerifier:
    """key_for(token) returns the verification key; production uses the realm's JWKS, tests a fixed key."""

    def __init__(self, issuer: str, audience: str, key_for: Callable[[str], object]):
        self.issuer, self.audience, self.key_for = issuer, audience, key_for

    @classmethod
    def from_jwks(cls, issuer: str, audience: str, jwks_url: str) -> "TokenVerifier":
        client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)
        return cls(issuer, audience, lambda token: client.get_signing_key_from_jwt(token).key)

    def identify(self, authorization: str | None) -> Identity:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise Unauthenticated("an access token is required (Authorization: Bearer ...)")
        token = authorization[7:].strip()
        try:
            claims = jwt.decode(token, self.key_for(token), algorithms=["RS256"], audience=self.audience,
                                issuer=self.issuer, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except jwt.PyJWKClientError as exc:
            raise Unauthenticated(f"the token's signing key could not be obtained: {exc}") from None
        except jwt.InvalidTokenError as exc:
            raise Unauthenticated(f"the token is not valid: {exc}") from None

        def text(name):
            value = claims.get(name)
            return value.strip() if isinstance(value, str) and value.strip() else None

        subject = text("sub")
        if subject is None:
            raise Unauthenticated("the token names no subject (sub)")
        roles = (claims.get("realm_access") or {}).get("roles") or []
        return Identity(subject=subject, display=text("preferred_username") or subject,
                        roles=frozenset(r for r in roles if r in BUSINESS_ROLES), agent_no=text("agent_no"),
                        department_claim=text("agency_type_code"), client=text("azp"))

    def principal(self, authorization: str | None) -> Principal:
        """Both steps at once, for callers that do not audit in between."""
        return self.identify(authorization).principal()
