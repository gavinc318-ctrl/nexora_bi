"""Runtime configuration: the files under /etc/oss911 (LD-01 2.5 · DD-84).

    ingest.conf  0644  non-secret settings: where the database is                 written by 30-python.yml
    ingest.env   0600  database login user and password                            written by 40-schema.yml
    icp.env      0600  ICP gateway address and service account                     written by 30-python.yml
                       (development) or by operations (production; the playbook only checks it)
    monitor.conf 0644  the monitoring exporter: database, listen address, files     written by 55-monitor.yml
    monitor.env  0600  its database login user and password                        written by 40-schema.yml
    alertpath.conf 0644  the heartbeat check and the daily digest: delivery side,   written by 56-alerting.yml
                       Prometheus, state directory, heartbeat source and ages

All are KEY=VALUE lines, the format systemd and "set -a; . file" read too. The program reads them itself:
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


@dataclass(frozen=True)
class MonitorSettings:
    db: DbSettings
    listen: str              # address the exporter binds: the host's address on the container network
    port: int
    config_path: str         # the monitoring configuration of the release (contracts/monitoring.yml, installed)
    manifest_path: str       # the DAG manifest (sources, schedules, re-sweep days)
    state_dir: str           # the alert path's state files (oss911-alertpath), served as metrics


def monitor_settings() -> MonitorSettings:
    """The exporter's settings (DD-100): monitor.conf and monitor.env. Its database login uses the transaction pool --
    one short read-only transaction per query, no session state."""
    conf = read_env_file("monitor.conf")
    cred = read_env_file("monitor.env")
    nums = {}
    for key in ("OSS911_DB_PORT", "MONITOR_PORT"):
        value = require(conf, key, "monitor.conf")
        if not value.isdigit():
            raise ConfigError(f"{key} in monitor.conf must be a number")
        nums[key] = int(value)
    db = DbSettings(host=require(conf, "OSS911_DB_HOST", "monitor.conf"), port=nums["OSS911_DB_PORT"],
                    dbname=require(conf, "OSS911_DB_NAME", "monitor.conf"),
                    user=require(cred, "MONITOR_DB_USER", "monitor.env"),
                    password=require(cred, "MONITOR_DB_PASSWORD", "monitor.env"))
    return MonitorSettings(db=db, listen=require(conf, "MONITOR_LISTEN", "monitor.conf"), port=nums["MONITOR_PORT"],
                           config_path=require(conf, "MONITOR_CONFIG", "monitor.conf"),
                           manifest_path=require(conf, "MONITOR_MANIFEST", "monitor.conf"),
                           state_dir=conf.get("MONITOR_STATE_DIR") or "/var/lib/oss911mon/alertpath")


@dataclass(frozen=True)
class AlertPathSettings:
    gateway_url: str         # the delivery side: the fake gateway (development) or the delivery service
    prometheus_url: str
    state_dir: str
    monitor_config: str      # the installed monitoring configuration: the report time zone
    node: str                # this node, named in the messages
    heartbeat_source: str    # whose heartbeat this node checks: its own on one node, the peer's in production
    heartbeat_max_age: int   # seconds without a heartbeat before the path counts as broken
    renotify: int            # seconds between two "path broken" messages while it lasts


def alertpath_settings() -> AlertPathSettings:
    """The heartbeat check's and the digest's settings (DD-100 block three): alertpath.conf, nothing secret"""
    conf = read_env_file("alertpath.conf")
    nums = {}
    for key in ("HEARTBEAT_MAX_AGE_SECONDS", "HEARTBEAT_RENOTIFY_SECONDS"):
        value = require(conf, key, "alertpath.conf")
        if not value.isdigit() or int(value) == 0:
            raise ConfigError(f"{key} in alertpath.conf must be a positive number")
        nums[key] = int(value)
    return AlertPathSettings(
        gateway_url=require(conf, "ALERT_GATEWAY_URL", "alertpath.conf").rstrip("/"),
        prometheus_url=require(conf, "PROMETHEUS_URL", "alertpath.conf").rstrip("/"),
        state_dir=require(conf, "ALERTPATH_STATE_DIR", "alertpath.conf"),
        monitor_config=require(conf, "MONITOR_CONFIG", "alertpath.conf"),
        node=require(conf, "NODE_NAME", "alertpath.conf"),
        heartbeat_source=require(conf, "HEARTBEAT_SOURCE", "alertpath.conf"),
        heartbeat_max_age=nums["HEARTBEAT_MAX_AGE_SECONDS"], renotify=nums["HEARTBEAT_RENOTIFY_SECONDS"])
