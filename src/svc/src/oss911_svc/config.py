"""Runtime configuration: the files under /etc/oss911 (DD-99 · same scheme as the ingestion program, LD-01 2.5).

    svc.conf  0644  non-secret settings, written by 30-python.yml:
                      SVC_DSN_ANALYTIC_READ   connection string of the analytic reads (host, port, dbname; no user or
                                              password). Production: the analytic replica's read-only pool; the
                                              development machine: pgBouncer's oss911_txn
                      SVC_DSN_AUDIT_WRITE     the same for the audit writes, always the primary (DD-99 review item three)
                      SVC_OIDC_ISSUER         the realm's issuer, e.g. http://127.0.0.1:8080/realms/oss911
                      SVC_OIDC_AUDIENCE       the client the tokens must name in aud (oss911-svc)
                      SVC_OIDC_JWKS_URL       optional; default <issuer>/protocol/openid-connect/certs
    svc.env   0600  database login user and password (SVC_DB_USER, SVC_DB_PASSWORD), written by 40-schema.yml

OSS911_ETC overrides the directory, for tests only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_ETC = "/etc/oss911"

REPORT_TIMEZONE = "Asia/Riyadh"   # date_key is the day in this zone (LD-02 chapter 6); weeks start on Sunday
QUERY_TIMEOUT = "10s"             # one query (DD-99)
MAX_ROWS = 10_000                 # result limit; more is an error, never a truncated result (DD-99)


class ConfigError(Exception):
    pass


def _read(name: str) -> dict[str, str]:
    path = os.path.join(os.environ.get("OSS911_ETC", DEFAULT_ETC), name)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        raise ConfigError(f"{path} does not exist") from None
    except PermissionError:
        raise ConfigError(f"{path} is not readable by this user") from None
    values = {}
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            raise ConfigError(f"{path}:{n}: expected KEY=VALUE")   # the line is not echoed: it may hold a password
        values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class Settings:
    analytic_dsn: str
    audit_dsn: str
    db_user: str
    db_password: str
    oidc_issuer: str
    oidc_audience: str
    jwks_url: str

    @classmethod
    def load(cls) -> "Settings":
        conf, env = _read("svc.conf"), _read("svc.env")

        def need(values, key, source):
            if not values.get(key):
                raise ConfigError(f"{key} is missing or empty in {source}")
            return values[key]

        issuer = need(conf, "SVC_OIDC_ISSUER", "svc.conf").rstrip("/")
        return cls(
            analytic_dsn=need(conf, "SVC_DSN_ANALYTIC_READ", "svc.conf"),
            audit_dsn=need(conf, "SVC_DSN_AUDIT_WRITE", "svc.conf"),
            db_user=need(env, "SVC_DB_USER", "svc.env"),
            db_password=need(env, "SVC_DB_PASSWORD", "svc.env"),
            oidc_issuer=issuer,
            oidc_audience=need(conf, "SVC_OIDC_AUDIENCE", "svc.conf"),
            jwks_url=conf.get("SVC_OIDC_JWKS_URL") or f"{issuer}/protocol/openid-connect/certs",
        )
