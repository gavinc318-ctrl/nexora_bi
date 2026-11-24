"""oss911-monitor: the monitoring exporter (DD-100 block two · LD-01 10.2).

Reads meta, a few columns of core and raw's coverage records as oss911_monitor (read only, change 0017) and serves
them as Prometheus gauges on /metrics. It reports what is, never whether it should alarm: the rules in Prometheus
compare each value with a threshold the exporter exports too (oss911_threshold), so no business threshold is written
twice. What it reads:

  sources        meta.run_log          consecutive failed runs, age of the last success, per source of the DAG
                                       manifest (LD-01 chapter 10: source down, watermark stalled)
  resweep        meta.ingest_batch     source days from the source's first re-sweep day on, due by now, whose
                                       re-sweep has not reached core -- one missing day holds every later closing
  closing        mart_day_publication  days between today and the latest closed day, per mart table
  backlog        ingest_batch          processable batches a model has not registered, and the oldest one's age
  quarantine     quarantine_batch      batches waiting for a person
  reference      ingest_batch          age of the last synced version per reference scope
  clarification  clarification_queue   open items and the oldest one's age
  unlinked       core (0015/0017)      distinct agent IDs no dimension version covers
  agents         icp_agent_coverage    agents asked for and not returned by the last N requests in a row
  partitions     pg_inherits           days until the last monthly partition of each partitioned table ends
  checks         meta.check_result     per dbt node: latest status and failures, the previous failures; per
                                       expected check (contracts/monitoring.yml): its state and the age of its result
  (no query)     state files           the alert path's timed jobs (oss911-alertpath): when each last ran and whether
                                       it succeeded -- the heartbeat checker is watched through the alert path

Failure of one query leaves out that query's metrics -- never a value from an earlier refresh -- and sets
oss911_monitor_query_errors{query} to 1 (DD-100 review, item two). Results are cached for 60 s: the scrape interval
of the job is 60 s, so the database sees one round of queries a minute however often it is scraped.

The configuration (contracts/monitoring.yml as 55-monitor installs it, with each expected check's deployed_at) and
the DAG manifest are read again when they change. A configuration that does not load keeps the previous one and
sets oss911_monitor_config_ok to 0; with none at all, only the exporter's own metrics are served.

Standard library plus psycopg and PyYAML (the ingestion program's own dependencies): the text format is a few lines,
not worth an eighth-year dependency.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from . import __version__
from .alertpath import states as alertpath_states
from .config import ConfigError, monitor_settings

CACHE_SECONDS = 60
# Thresholds the queries or the rules need; the rules guard each with absent() as well
REQUIRED_THRESHOLDS = ("closing_lag_days", "batch_backlog_seconds", "agent_missing_consecutive",
                       "partition_min_days_ahead", "clarification_oldest_days")
CHECK_STATES = ("normal", "warned", "failed", "error", "skipped", "never_run", "stale", "grace", "disabled")


# ---------------------------------------------------------------------------------------------------------------
# Exposition: the Prometheus text format, version 0.0.4
# ---------------------------------------------------------------------------------------------------------------

def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value) -> str:
    if value is None:
        return "NaN"
    if isinstance(value, bool):
        return "1" if value else "0"
    v = float(value)
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "+Inf" if v > 0 else "-Inf"
    return repr(int(v)) if v.is_integer() and abs(v) < 2**53 else repr(v)


@dataclass
class Family:
    name: str
    help: str
    samples: list = field(default_factory=list)


class Metrics:
    """Gauges by name; families render in the order they were first declared."""

    def __init__(self) -> None:
        self.families: dict[str, Family] = {}

    def declare(self, name: str, help_text: str) -> None:
        if name not in self.families:
            self.families[name] = Family(name, help_text)

    def add(self, name: str, help_text: str, labels: dict, value) -> None:
        self.declare(name, help_text)
        self.families[name].samples.append((dict(labels), value))

    def extend(self, other: "Metrics") -> None:
        for f in other.families.values():
            self.declare(f.name, f.help)
            self.families[f.name].samples.extend(f.samples)

    def render(self) -> str:
        out = []
        for f in self.families.values():
            out.append(f"# HELP {f.name} {f.help}")
            out.append(f"# TYPE {f.name} gauge")
            for labels, value in f.samples:
                lab = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
                out.append(f"{f.name}{{{lab}}} {_number(value)}" if lab else f"{f.name} {_number(value)}")
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------------------------

class MonitorConfigError(Exception):
    pass


def _positive(doc: dict, key: str, where: str) -> float:
    v = doc.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        raise MonitorConfigError(f"{where}.{key} must be a positive number, got {v!r}")
    return v


def load_config(text: str) -> dict:
    """contracts/monitoring.yml as installed: validated, with defaults merged into each source."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise MonitorConfigError("the monitoring configuration must be a mapping")
    if doc.get("version") != 1:
        raise MonitorConfigError(f"unsupported version {doc.get('version')!r} (expected 1)")
    if not isinstance(doc.get("report_timezone"), str):
        raise MonitorConfigError("report_timezone is missing")
    th = doc.get("thresholds")
    if not isinstance(th, dict) or not th:
        raise MonitorConfigError("thresholds must be a non-empty mapping")
    for key in th:
        _positive(th, key, "thresholds")
    missing = [k for k in REQUIRED_THRESHOLDS if k not in th]
    if missing:
        raise MonitorConfigError(f"thresholds lack {', '.join(missing)}")
    src = doc.get("sources")
    if not isinstance(src, dict) or not isinstance(src.get("default"), dict):
        raise MonitorConfigError("sources.default is missing")
    keys = ("max_consecutive_failures", "max_success_age_minutes")
    for key in keys:
        _positive(src["default"], key, "sources.default")
    merged = {}
    for sid, s in src.items():
        if sid == "default":
            continue
        if not isinstance(s, dict):
            raise MonitorConfigError(f"sources.{sid} must be a mapping")
        m = {**src["default"], **s}
        for key in keys:
            _positive(m, key, f"sources.{sid}")
        if "resweep_allowed_delay_hours" in m:
            _positive(m, "resweep_allowed_delay_hours", f"sources.{sid}")
        models = m.get("models", [])
        if not isinstance(models, list) or not all(isinstance(x, str) for x in models):
            raise MonitorConfigError(f"sources.{sid}.models must be a list of model names")
        merged[sid] = m
    checks = doc.get("expected_checks")
    if not isinstance(checks, list) or not checks:
        raise MonitorConfigError("expected_checks must be a non-empty list")
    seen = set()
    for i, c in enumerate(checks):
        where = f"expected_checks[{i}]"
        if not isinstance(c, dict) or not isinstance(c.get("id"), str):
            raise MonitorConfigError(f"{where} needs an id")
        if c["id"] in seen:
            raise MonitorConfigError(f"{where}: id {c['id']} is listed twice")
        seen.add(c["id"])
        if c.get("kind") not in ("dag", "check"):
            raise MonitorConfigError(f"{where} ({c['id']}): kind must be dag or check")
        if not isinstance(c.get("dag_id"), str):
            raise MonitorConfigError(f"{where} ({c['id']}): dag_id is missing")
        if c["kind"] == "check" and not isinstance(c.get("node"), str):
            raise MonitorConfigError(f"{where} ({c['id']}): a check needs its dbt node name")
        _positive(c, "period_minutes", where)
        _positive(c, "allowed_delay_minutes", where)
        if not isinstance(c.get("enabled", True), bool):
            raise MonitorConfigError(f"{where} ({c['id']}): enabled must be true or false")
        if c.get("deployed_at") is not None:
            try:
                datetime.fromisoformat(str(c["deployed_at"]))
            except ValueError:
                raise MonitorConfigError(f"{where} ({c['id']}): deployed_at is not a timestamp") from None
    return {"report_timezone": doc["report_timezone"], "thresholds": th, "default": src["default"],
            "sources": merged, "expected_checks": checks}


def load_manifest(text: str) -> list[dict]:
    doc = json.loads(text)
    sources = doc.get("sources") if isinstance(doc, dict) else None
    if not isinstance(sources, list):
        raise MonitorConfigError("the DAG manifest has no sources list")
    return [s for s in sources if isinstance(s, dict) and isinstance(s.get("source_id"), str)]


def source_settings(cfg: dict, source_id: str) -> dict:
    return cfg["sources"].get(source_id, cfg["default"])


# ---------------------------------------------------------------------------------------------------------------
# Expected checks: the state of each item (DD-100 review note, item three)
# ---------------------------------------------------------------------------------------------------------------

def check_state(item: dict, latest: dict | None, now: datetime) -> tuple[str, float | None, float]:
    """(state, age of the latest result in seconds or None, the allowed age in seconds) of one expected check.

    latest: {"status": dbt status (for a dag: the worst of its latest invocation), "checked_at": datetime} or None.
    Stale wins over the status: an old pass is no pass. An item never run is in its grace period while its
    deployed_at is younger than one period plus the allowed delay; after that it is never_run. Disabled items have
    no state but disabled."""
    allowed = (item["period_minutes"] + item["allowed_delay_minutes"]) * 60.0
    if not item.get("enabled", True):
        return "disabled", None, allowed
    if latest is None:
        dep = item.get("deployed_at")
        if dep is not None:
            dep_at = datetime.fromisoformat(str(dep))
            if dep_at.tzinfo is None:
                dep_at = dep_at.replace(tzinfo=timezone.utc)
            if (now - dep_at).total_seconds() < allowed:
                return "grace", None, allowed
        return "never_run", None, allowed
    age = (now - latest["checked_at"]).total_seconds()
    if age > allowed:
        return "stale", age, allowed
    status = latest["status"]
    state = {"success": "normal", "pass": "normal", "warn": "warned", "fail": "failed", "error": "error",
             "skipped": "skipped"}.get(status, "error")
    return state, age, allowed


_STATUS_RANK = {"error": 5, "fail": 4, "skipped": 3, "warn": 2, "pass": 1, "success": 1}


def worst_status(statuses) -> str:
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 6))


# ---------------------------------------------------------------------------------------------------------------
# Queries. Each takes a cursor and the context, and returns Metrics; it raises on any error.
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class Context:
    cfg: dict
    manifest: list[dict]
    now: datetime

    def enabled_sources(self) -> list[dict]:
        return [s for s in self.manifest if s.get("enabled", True)]


def q_sources(cur, ctx: Context) -> Metrics:
    m = Metrics()
    ids = [s["source_id"] for s in ctx.enabled_sources()]
    cur.execute("""
        with r as (
            select source_id, started_at, outcome,
                   row_number() over (partition by source_id order by started_at desc) as rn
              from meta.run_log
             where source_id = any(%(ids)s) and finished_at is not null
               and started_at > now() - interval '30 days'
        )
        select s.source_id,
               extract(epoch from now() - (select max(l.started_at) from meta.run_log l
                                            where l.source_id = s.source_id and l.outcome = 'success')),
               coalesce((select min(rn) - 1 from r where r.source_id = s.source_id and r.outcome <> 'failed'),
                        (select count(*) from r where r.source_id = s.source_id))
          from unnest(%(ids)s::text[]) as s(source_id)
    """, {"ids": ids})
    for sid, age, fails in cur.fetchall():
        lab = {"source_id": sid}
        m.add("oss911_source_last_success_age_seconds",
              "Seconds since the last successful run of the source (+Inf: none in the log)", lab,
              math.inf if age is None else age)
        m.add("oss911_source_consecutive_failures",
              "Failed runs of the source in a row, newest first, up to the last run that did not fail", lab, fails)
    return m


def q_resweep(cur, ctx: Context) -> Metrics:
    m = Metrics()
    for s in ctx.enabled_sources():
        if not s.get("resweep") or not s.get("resweep_from"):
            continue
        st = source_settings(ctx.cfg, s["source_id"])
        core_model = (st.get("models") or [None])[-1]
        if core_model is None:
            raise MonitorConfigError(f"sources.{s['source_id']}.models: the core model is needed for re-sweeps")
        cur.execute("""
            with runs as (
                select b.run_id, (max(b.window_to)::timestamp - interval '1 day')::date as source_day,
                       bool_and(exists (select 1 from meta.staging_batch_state t
                                         where t.model = %(model)s and t.run_id = b.run_id and t.batch_seq = b.batch_seq)
                                or exists (select 1 from meta.quarantine_batch q
                                            where q.run_id = b.run_id and q.batch_seq = b.batch_seq
                                              and q.state = 'discarded')) as in_core
                  from meta.ingest_batch b
                  join meta.run_log r on r.run_id = b.run_id and r.outcome = 'success'
                 where b.source_id = %(sid)s and b.mode = 'resweep'
                 group by b.run_id
            ),
            due as (
                select d::date as day
                  from generate_series(%(first)s::date,
                                       ((now() at time zone %(tz)s) - %(delay)s * interval '1 hour')::date - 1,
                                       interval '1 day') d
            )
            select count(*), min(day)
              from due where day not in (select source_day from runs where in_core)
        """, {"model": core_model, "sid": s["source_id"], "first": s["resweep_from"], "tz": s.get("timezone") or "UTC",
              "delay": float(st.get("resweep_allowed_delay_hours", 6))})
        missing, first_missing = cur.fetchone()
        m.add("oss911_resweep_missing_days",
              "Source days due for re-sweep (from the first re-sweep day on) whose re-sweep has not reached core",
              {"source_id": s["source_id"]}, missing)
        if first_missing is not None:
            m.add("oss911_resweep_first_missing_day",
                  "The earliest such day, as YYYYMMDD (absent when none is missing)",
                  {"source_id": s["source_id"]}, int(first_missing.strftime("%Y%m%d")))
    return m


def q_closing(cur, ctx: Context) -> Metrics:
    m = Metrics()
    cur.execute("""
        select mart_table,
               (now() at time zone %(tz)s)::date
                 - to_date((max(date_key) filter (where close_version >= 1))::text, 'YYYYMMDD')
          from meta.mart_day_publication group by mart_table
    """, {"tz": ctx.cfg["report_timezone"]})
    for table, lag in cur.fetchall():
        m.add("oss911_mart_closed_lag_days",
              "Days from today (report time zone) back to the latest closed day of the mart table (+Inf: none closed)",
              {"mart_table": table}, math.inf if lag is None else lag)
    return m


def q_backlog(cur, ctx: Context) -> Metrics:
    m = Metrics()
    pairs = [(s["source_id"], model) for s in ctx.enabled_sources()
             for model in source_settings(ctx.cfg, s["source_id"]).get("models", [])]
    if not pairs:
        return m
    cur.execute("""
        select p.source_id, p.model, count(b.run_id), extract(epoch from now() - min(b.committed_at))
          from unnest(%(sids)s::text[], %(models)s::text[]) as p(source_id, model)
          left join meta.ingest_batch b
            on b.source_id = p.source_id
           and b.committed_at > now() - interval '14 days'
           and not exists (select 1 from meta.quarantine_batch q
                            where q.run_id = b.run_id and q.batch_seq = b.batch_seq
                              and q.state in ('pending', 'discarded'))
           and not exists (select 1 from meta.staging_batch_state t
                            where t.model = p.model and t.run_id = b.run_id and t.batch_seq = b.batch_seq)
         group by p.source_id, p.model
    """, {"sids": [p[0] for p in pairs], "models": [p[1] for p in pairs]})
    for sid, model, n, age in cur.fetchall():
        lab = {"source_id": sid, "model": model}
        m.add("oss911_unprocessed_batches",
              "Processable batches of the last 14 days the model has not registered (not taken in yet)", lab, n)
        m.add("oss911_unprocessed_oldest_age_seconds",
              "Age of the oldest of them since its commit (0 when there is none)", lab, age or 0)
    return m


def q_quarantine(cur, ctx: Context) -> Metrics:
    m = Metrics()
    cur.execute("""
        select s.source_id,
               (select count(*) from meta.quarantine_batch q
                  join meta.ingest_batch b on b.run_id = q.run_id and b.batch_seq = q.batch_seq
                 where b.source_id = s.source_id and q.state = 'pending')
          from unnest(%(ids)s::text[]) as s(source_id)
    """, {"ids": [s["source_id"] for s in ctx.enabled_sources()]})
    for sid, n in cur.fetchall():
        m.add("oss911_quarantine_pending", "Quarantined batches waiting for a decision in M-03", {"source_id": sid}, n)
    return m


def q_reference(cur, ctx: Context) -> Metrics:
    m = Metrics()
    cur.execute("""
        select source_id, split_part(snapshot_scope, '@', 1), extract(epoch from now() - max(snapshot_synced_at))
          from meta.ingest_batch
         where snapshot_scope is not null and snapshot_synced_at is not null
         group by 1, 2
    """)
    for sid, scope, age in cur.fetchall():
        m.add("oss911_reference_sync_age_seconds",
              "Seconds since the platform last synced the newest version of the reference scope (its endTime)",
              {"source_id": sid, "scope": scope}, age)
    cur.execute("""
        select c.source_id,
               coalesce((select p.param_value::integer from meta.source_runtime_param p
                          where p.source_id = c.source_id and p.param_name = 'version_stale_days'),
                        (c.definition #>> '{window,version_stale_days}')::integer)
          from meta.source_contract c
         where c.definition #> '{window,version_stale_days}' is not null
    """)
    for sid, days in cur.fetchall():
        m.add("oss911_threshold", "Thresholds of the alert rules, from the release or the database (one authority)",
              {"signal": "reference_sync_age_seconds", "source_id": sid}, days * 86400)
    return m


def q_clarification(cur, ctx: Context) -> Metrics:
    m = Metrics()
    cur.execute("""
        select source_id, count(*), extract(epoch from now() - min(raised_at))
          from meta.clarification_queue where status in ('open', 'asked') group by source_id
    """)
    for sid, n, age in cur.fetchall():
        lab = {"source_id": sid}
        m.add("oss911_clarification_open", "Clarification items open or asked", lab, n)
        m.add("oss911_clarification_oldest_age_seconds", "Age of the oldest of them", lab, age)
    return m


def q_unlinked(cur, ctx: Context) -> Metrics:
    m = Metrics()
    for table, sql in (
            ("core.fact_call_leg", "select count(distinct device_no) from core.fact_call_leg "
                                   "where device_type_code = 2 and agent_sk is null"),
            ("core.fact_agent_session", "select count(distinct agent_no) from core.fact_agent_session "
                                        "where agent_sk is null"),
            ("core.fact_agent_day", "select count(distinct agent_no) from core.fact_agent_day where agent_sk is null")):
        cur.execute(sql)
        m.add("oss911_unlinked_agents", "Distinct agent IDs no version of dim_agent covers", {"table": table},
              cur.fetchone()[0])
    return m


def q_agents(cur, ctx: Context) -> Metrics:
    m = Metrics()
    n = int(ctx.cfg["thresholds"]["agent_missing_consecutive"])
    cur.execute("""
        with recent as (
            select c.source_id, c.observed_at, r.agent_no, r.agent_no = any(c.returned) as got
              from raw.icp_agent_coverage c
             cross join lateral unnest(c.requested) as r(agent_no)
             where c.observed_at > now() - interval '2 days'
        ),
        ranked as (
            select *, row_number() over (partition by source_id, agent_no order by observed_at desc) as rn from recent
        ),
        streak as (
            select source_id, agent_no from ranked where rn <= %(n)s
             group by source_id, agent_no having count(*) = %(n)s and not bool_or(got)
        ),
        sources as (select distinct source_id from raw.icp_agent_coverage
                     where observed_at > now() - interval '2 days')
        select so.source_id, k.roster,
               count(st.agent_no) filter (where (k.roster = 'current') = exists (
                   select 1 from core.dim_agent d where d.src_natural_key = st.agent_no and d.is_current))
          from sources so
         cross join (values ('current'), ('removed')) as k(roster)
          left join streak st on st.source_id = so.source_id
         group by so.source_id, k.roster
    """, {"n": n})
    for sid, roster, cnt in cur.fetchall():
        m.add("oss911_agents_not_returned",
              "Agents asked for and missing from each of the last N responses (N: threshold agent_missing_consecutive);"
              " roster current: still in the configuration, removed: no longer (daily digest only)",
              {"source_id": sid, "roster": roster}, cnt)
    return m


def q_partitions(cur, ctx: Context) -> Metrics:
    m = Metrics()
    cur.execute("""
        select n.nspname || '.' || p.relname,
               (max(to_date(right(c.relname, 6), 'YYYYMM')) + interval '1 month')::date - current_date
          from pg_partitioned_table pt
          join pg_class p on p.oid = pt.partrelid
          join pg_namespace n on n.oid = p.relnamespace
          left join pg_inherits i on i.inhparent = p.oid
          left join pg_class c on c.oid = i.inhrelid and c.relname ~ '_p[0-9]{6}$'
         where n.nspname in ('core', 'audit', 'raw', 'meta')
         group by 1
    """)
    for table, days in cur.fetchall():
        m.add("oss911_partition_days_ahead",
              "Days until the last monthly partition of the table ends (-Inf: it has none); writes after that fail",
              {"table": table}, -math.inf if days is None else days)
    return m


def q_checks(cur, ctx: Context) -> Metrics:
    m = Metrics()
    # Every test node with a result in the last two days, per DAG: its latest result and the one before (WARN that
    # grows, DD-100 review: compare adjacent results)
    cur.execute("""
        select dag_id, node_name, severity, status, failures, prev_failures, checked_at
          from (select dag_id, node_name, severity, status, failures, checked_at,
                       lead(failures) over w as prev_failures,
                       row_number() over w as rn
                  from meta.check_result
                 where resource_type = 'test' and dag_id is not null and checked_at > now() - interval '2 days'
                window w as (partition by dag_id, unique_id order by checked_at desc)) x
         where rn = 1
    """)
    for dag, node, sev, status, failures, prev, _ in cur.fetchall():
        lab = {"dag_id": dag, "node": node, "severity": sev or ""}
        m.add("oss911_test_status", "Latest result of a dbt test in a DAG: 1 for its status", {**lab, "status": status},
              1)
        if failures is not None:
            m.add("oss911_test_failures", "Rows the test returned in its latest result", lab, failures)
        if prev is not None:
            m.add("oss911_test_failures_previous", "Rows the test returned in the result before", lab, prev)
    # The expected checks
    cur.execute("""
        select dag_id, node_name, status, checked_at
          from (select dag_id, node_name, status, checked_at,
                       row_number() over (partition by dag_id, node_name order by checked_at desc) as rn
                  from meta.check_result
                 where dag_id = any(%(dags)s) and checked_at > now() - interval '8 days') x
         where rn = 1
    """, {"dags": sorted({c["dag_id"] for c in ctx.cfg["expected_checks"]})})
    nodes = {(d, n): {"status": s, "checked_at": t} for d, n, s, t in cur.fetchall()}
    cur.execute("""
        select dag_id, array_agg(status), max(checked_at)
          from meta.check_result r
         where dag_id = any(%(dags)s) and checked_at > now() - interval '8 days'
           and invocation_id = (select r2.invocation_id from meta.check_result r2
                                 where r2.dag_id = r.dag_id and r2.checked_at > now() - interval '8 days'
                                 order by r2.checked_at desc limit 1)
         group by dag_id
    """, {"dags": sorted({c["dag_id"] for c in ctx.cfg["expected_checks"] if c["kind"] == "dag"})})
    dags = {d: {"status": worst_status(st), "checked_at": t} for d, st, t in cur.fetchall()}
    for item in ctx.cfg["expected_checks"]:
        latest = dags.get(item["dag_id"]) if item["kind"] == "dag" else nodes.get((item["dag_id"], item["node"]))
        state, age, allowed = check_state(item, latest, ctx.now)
        lab = {"check": item["id"], "dag_id": item["dag_id"], "kind": item["kind"]}
        for s in CHECK_STATES:
            m.add("oss911_expected_check_state",
                  "State of an expected check (contracts/monitoring.yml): 1 for the current state, 0 for the others",
                  {**lab, "state": s}, s == state)
        m.add("oss911_expected_check_age_seconds",
              "Age of the check's latest result (+Inf: never run)", lab, math.inf if age is None else age)
        m.add("oss911_expected_check_allowed_age_seconds", "One period plus the allowed delay", lab, allowed)
    return m


QUERIES = [("sources", q_sources), ("resweep", q_resweep), ("closing", q_closing), ("backlog", q_backlog),
           ("quarantine", q_quarantine), ("reference", q_reference), ("clarification", q_clarification),
           ("unlinked", q_unlinked), ("agents", q_agents), ("partitions", q_partitions), ("checks", q_checks)]


def thresholds(ctx: Context) -> Metrics:
    """The release's thresholds (contracts/monitoring.yml), global and per source"""
    m = Metrics()
    help_text = "Thresholds of the alert rules, from the release or the database (one authority)"
    for key, value in ctx.cfg["thresholds"].items():
        m.add("oss911_threshold", help_text, {"signal": key}, value)
    for s in ctx.enabled_sources():
        st = source_settings(ctx.cfg, s["source_id"])
        m.add("oss911_threshold", help_text, {"signal": "source_consecutive_failures", "source_id": s["source_id"]},
              st["max_consecutive_failures"])
        m.add("oss911_threshold", help_text, {"signal": "source_last_success_age_seconds", "source_id": s["source_id"]},
              st["max_success_age_minutes"] * 60)
    return m


# ---------------------------------------------------------------------------------------------------------------
# The collector: config reloads, the cache, the database round
# ---------------------------------------------------------------------------------------------------------------

class Collector:
    def __init__(self, settings, connect=None, clock=time.time) -> None:
        self.settings = settings
        self._connect = connect or self._psycopg_connect
        self._clock = clock
        self._lock = threading.Lock()
        self._text = ""
        self._at = -math.inf
        self.cfg = None
        self.manifest: list[dict] = []
        self._stamps: dict[str, float] = {}
        self.config_error = ""

    def _psycopg_connect(self):
        import psycopg
        kw = self.settings.db.connect_kwargs()
        kw["application_name"] = "oss911-monitor"
        conn = psycopg.connect(**kw, autocommit=False)
        conn.read_only = True
        return conn

    def _reload(self) -> None:
        errors = []
        for path, kind in ((self.settings.config_path, "config"), (self.settings.manifest_path, "manifest")):
            try:
                stamp = os.stat(path).st_mtime
                if self._stamps.get(path) == stamp and (self.cfg is not None or kind == "manifest"):
                    continue
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                if kind == "config":
                    self.cfg = load_config(text)
                else:
                    self.manifest = load_manifest(text)
                self._stamps[path] = stamp
            except (OSError, ValueError, MonitorConfigError, yaml.YAMLError) as exc:
                errors.append(f"{path}: {exc}")
        self.config_error = "; ".join(errors)

    def refresh(self) -> str:
        started = self._clock()
        self._reload()
        out = Metrics()
        out.add("oss911_monitor_info", "The exporter's version", {"version": __version__}, 1)
        out.add("oss911_monitor_config_ok",
                "1 when the monitoring configuration and the DAG manifest last loaded without error", {},
                not self.config_error)
        errors = {name: 1 for name, _ in QUERIES}
        if self.cfg is not None:
            ctx = Context(self.cfg, self.manifest, datetime.now(timezone.utc))
            out.extend(thresholds(ctx))
            try:
                conn = self._connect()
            except Exception as exc:   # noqa: BLE001 -- any failure to connect is the same signal
                print(f"oss911-monitor: cannot connect: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                conn = None
            if conn is not None:
                try:
                    for name, query in QUERIES:
                        try:
                            with conn.cursor() as cur:
                                part = query(cur, ctx)
                            conn.commit()
                            out.extend(part)
                            errors[name] = 0
                        except Exception as exc:   # noqa: BLE001 -- one query's failure must not hide the others
                            print(f"oss911-monitor: query {name} failed: {type(exc).__name__}: {exc}",
                                  file=sys.stderr, flush=True)
                            try:
                                conn.rollback()
                            except Exception:   # noqa: BLE001
                                break
                finally:
                    try:
                        conn.close()
                    except Exception:   # noqa: BLE001
                        pass
        for name, flag in errors.items():
            out.add("oss911_monitor_query_errors",
                    "1 when the query failed in the last refresh: its metrics are left out, never served stale",
                    {"query": name}, flag)
        for task, st in alertpath_states(self.settings.state_dir).items():
            out.add("oss911_alertpath_last_run_timestamp_seconds",
                    "Unix time the alert path's timed job last ran (heartbeat_check, digest)", {"task": task},
                    st["last_run"])
            out.add("oss911_alertpath_last_run_ok", "1 when that run succeeded", {"task": task}, st["ok"])
        out.add("oss911_monitor_refresh_duration_seconds", "Duration of the last refresh", {},
                round(self._clock() - started, 3))
        out.add("oss911_monitor_refresh_timestamp_seconds", "Unix time of the last refresh", {}, round(started, 3))
        return out.render()

    def metrics(self) -> str:
        with self._lock:
            if self._clock() - self._at >= CACHE_SECONDS:
                self._text = self.refresh()
                self._at = self._clock()
            return self._text


# ---------------------------------------------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------------------------------------------

class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # The address is the host's on the container network: it may come up after this service (podman creates
        # the bridge with the first container). IP_FREEBIND lets the bind succeed before it exists.
        freebind = getattr(socket, "IP_FREEBIND", 15)
        self.socket.setsockopt(socket.SOL_IP, freebind, 1)
        super().server_bind()


def make_handler(collector: Collector):
    class Handler(BaseHTTPRequestHandler):
        server_version = "oss911-monitor"
        sys_version = ""

        def do_GET(self):   # noqa: N802 -- http.server's naming
            if self.path.split("?")[0] == "/metrics":
                body = collector.metrics().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            elif self.path == "/":
                body = b"oss911-monitor: see /metrics\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
            else:
                body = b"not found\n"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):   # scrapes every minute: no access log in the journal
            pass

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="oss911-monitor", description="OSS911 BI monitoring exporter (DD-100)")
    ap.add_argument("--version", action="version", version=f"oss911-monitor {__version__}")
    ap.add_argument("--once", action="store_true", help="print one refresh to stdout and exit (verification)")
    args = ap.parse_args(argv)
    try:
        settings = monitor_settings()
    except ConfigError as exc:
        print(f"oss911-monitor: {exc}", file=sys.stderr)
        return 2
    collector = Collector(settings)
    if args.once:
        text = collector.refresh()
        sys.stdout.write(text)
        bad = [ln for ln in text.splitlines()
               if (ln.startswith("oss911_monitor_query_errors") and not ln.endswith(" 0"))
               or ln == "oss911_monitor_config_ok 0"]
        if collector.config_error:
            print(f"oss911-monitor: {collector.config_error}", file=sys.stderr)
        return 1 if bad else 0
    server = _Server((settings.listen, settings.port), make_handler(collector))
    print(f"oss911-monitor {__version__} listening on {settings.listen}:{settings.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
