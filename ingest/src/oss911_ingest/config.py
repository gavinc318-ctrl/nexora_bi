"""Runtime configuration: the files under /etc/oss911 (LD-01 2.5 · DD-84).

    ingest.conf  0644  non-secret settings: where the database is                 written by 30-python.yml
    ingest.env   0600  database login user and password                            written by 40-schema.yml
    icp.env      0600  ICP gateway address and service account                     written by 30-python.yml
                       (development) or by operations (production; the playbook only checks it)

All three are KEY=VALUE lines, the format systemd and "set -a; . file" read too. The program reads them itself:
no password passes through the DAG, a template or the Airflow parent process (LD-01 2.5).

Contract references map to variable names (LD-01 2.5):
    host_ref: X        -> OSS911_HOST_<X>
    credential_ref: Y  -> OSS911_CRED_<Y>_USER and OSS911_CRED_<Y>_PASSWORD        (X, Y upper-cased)

OSS911_ETC overrides the directory, for tests only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_ETC = "/etc/oss911"


class ConfigError(Exception):
    """A configuration file or setting is missing or malformed; ends with exit code 2."""


def etc_dir() -> str:
    return os.environ.get("OSS911_ETC", DEFAULT_ETC)


def read_env_file(name: str) -> dict[str, str]:
    path = os.path.join(etc_dir(), name)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        raise ConfigError(f"{path} does not exist") from None
    except PermissionError:
        raise ConfigError(f"{path} is not readable by this user") from None
    values: dict[str, str] = {}
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            raise ConfigError(f"{path}:{n}: expected KEY=VALUE")   # the line itself is not echoed: it may hold a password
        values[key.strip()] = value.strip()
    return values


def require(values: dict[str, str], key: str, source: str) -> str:
    value = values.get(key, "")
    if not value:
        raise ConfigError(f"{key} is missing or empty in {source}")
    return value


@dataclass(frozen=True)
class DbSettings:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    def connect_kwargs(self) -> dict:
        # Keyword arguments, not a connection string: nothing to quote, and the password never appears in a string
        # that could end up in an error message
        return {"host": self.host, "port": self.port, "dbname": self.dbname, "user": self.user,
                "password": self.password, "application_name": "oss911-ingest", "connect_timeout": 10}


def db_settings() -> DbSettings:
    conf = read_env_file("ingest.conf")
    cred = read_env_file("ingest.env")
    port = require(conf, "OSS911_DB_PORT", "ingest.conf")
    if not port.isdigit():
        raise ConfigError("OSS911_DB_PORT in ingest.conf must be a number")
    return DbSettings(
        host=require(conf, "OSS911_DB_HOST", "ingest.conf"),
        port=int(port),
        # Must be pgBouncer's session pool: the advisory lock lives in the session (DD-85)
        dbname=require(conf, "OSS911_DB_NAME", "ingest.conf"),
        user=require(cred, "INGEST_DB_USER", "ingest.env"),
        password=require(cred, "INGEST_DB_PASSWORD", "ingest.env"),
    )
