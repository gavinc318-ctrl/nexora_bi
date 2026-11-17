"""Shared test pieces: a signing key standing in for the realm's, tokens shaped like Keycloak's, and the application
wired to a test database.

The database tests need OSS911_SVC_TEST_DSN: a libpq connection string of a database built by the structural
changes and the dbt project, logging in as a user that is a member of oss911_query_svc -- the same privileges as
the service in production, so a test cannot pass on privileges the service will not have. They are skipped
without it. The audit tests also need OSS911_SVC_TEST_AUDIT_DSN, a login that may read audit.access_log (the
service's role may only insert into it).
"""

from __future__ import annotations

import os
import time

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from oss911_svc.app import create_app
from oss911_svc.auth import TokenVerifier

ISSUER = "http://keycloak.test/realms/oss911"
AUDIENCE = "oss911-svc"


@pytest.fixture(scope="session")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def verifier(key):
    return TokenVerifier(ISSUER, AUDIENCE, lambda token: key.public_key())


@pytest.fixture(scope="session")
def token(key):
    def make(user="sup1", roles=("supervisor",), aud=AUDIENCE, iss=ISSUER, ttl=300, signing_key=None, sub="",
             **claims):
        """sub: default "id-<user>"; None leaves it out."""
        now = int(time.time())
        payload = {"iss": iss, "aud": aud, "preferred_username": user, "azp": "oss911-portal",
                   "iat": now, "exp": now + ttl, "realm_access": {"roles": list(roles)}, **claims}
        if sub is not None:
            payload["sub"] = sub or f"id-{user}"
        return "Bearer " + jwt.encode(payload, signing_key or key, algorithm="RS256")
    return make


@pytest.fixture(scope="session")
def dsn():
    value = os.environ.get("OSS911_SVC_TEST_DSN")
    if not value:
        pytest.skip("OSS911_SVC_TEST_DSN is not set: no test database")
    return value


@pytest.fixture()
def client(dsn, verifier):
    from fastapi.testclient import TestClient

    def connect():
        return psycopg.connect(dsn, prepare_threshold=None)
    return TestClient(create_app(verifier=verifier, connect_analytic=connect, connect_audit=connect))
