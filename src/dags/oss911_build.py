"""Model build DAGs (dbt) and daily maintenance (DD-84 · DD-85 · DD-89 · DD-91 items 10, 11).

Like the ingestion DAGs, generated from the DAG manifest /opt/oss911/release/dags.json -- never from the contracts.

  build_<source>           every <schedule> of the source: dbt build of the source's staging and core models,
                           with their tests. A separate DAG from ingest_<source>, not a second task behind it: one DAG
                           has max_active_runs=1, so a dbt failure and its retries would hold up ingestion (DD-91 item
                           10). The two never hand over through files -- staging takes what raw holds, by batch
                           (meta.staging_batch_state, DD-89), so build and ingestion need no ordering
  maintenance_partitions   daily: monthly partitions for this month and the next three, every partitioned table in
                           core, audit and raw (DD-77). A missing partition makes every write for that month fail
  maintenance_checks       daily: the call-level consistency tests over every call (var full_check). The builds run
                           them over the calls of the last day only (macros/fact_call.sql, calls_to_check): over all
                           calls their time grows with the data, and a build runs every few minutes. Also
                           batches_reach_core: CDR batches older than a day that never reached core.fact_call_leg; and
                           mart_closing_not_behind: a mart table whose last closed day is more than 3 days back
  publish_marts_intraday   hourly: the days not closed yet, as provisional rows (close_version 0), and their tests
  publish_marts_nightly    daily: fact_call brought up to date with the legs, then the days whose re-sweep has reached
                           core (every source day from the source's first re-sweep day on, var resweep_from) are
                           closed (close_version 1) and the last 7 closed days corrected where they changed
                           (macros/mart.sql · DD-97 items six to nine). A day whose re-sweep is late is simply closed
                           by a later night: whether a day is final is read from the database, not from the clock

All of them use the pool dbt_build (one slot for the whole cluster, DD-85): dbt invocations never overlap, which the
core models rely on (macros/fact_call_leg.sql, register_core_batches) and which keeps dbt's threads within the
pgBouncer session pool.

Each command reads dbt's database login from /etc/oss911/dbt.env itself; the task environment carries nothing from
the Airflow process (the metadata database connection string lives there). dbt's target and log directories are
under the airflow home: the project directory /opt/oss911/dbt is installed read-only from the release.
"""

from __future__ import annotations

import json
import os
import shlex
from datetime import datetime, timedelta

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

MANIFEST = os.environ.get("OSS911_DAG_MANIFEST", "/opt/oss911/release/dags.json")
DBT = (
    "set -a; . /etc/oss911/dbt.env; set +a; "
    "exec /opt/oss911/venv-dbt/bin/dbt {command} "
    "--project-dir /opt/oss911/dbt --profiles-dir /var/lib/airflow "
    "--target-path /var/lib/airflow/dbt/target --log-path /var/lib/airflow/dbt/logs"
)
POOL = "dbt_build"
UTC = pendulum.timezone("UTC")

# Which dbt models each source builds (staging, then core). A source in the manifest without an entry has no build
# DAG yet: its data stays in raw until its models are written. Listed explicitly, like the pools of the ingestion DAGs.
BUILDS = {
    "icp_cdr_voice": "stg_icp_cdr_voice fact_call_leg fact_call",
    # The dimension models also relink the call legs already in core (macros/reference.sql), which changes the calls'
    # agent and first queue: fact_call follows them. The shared pool keeps them from running at the same time as
    # build_icp_cdr_voice
    "icp_reference": "stg_icp_reference dim_agent dim_queue reference_value fact_call",
    # Agent metrics (DD-98): dim_agent relinks these two as well when its versions change (macros/agent_facts.sql)
    "icp_agent_session": "stg_icp_agent_session fact_agent_session",
    "icp_agent_day": "stg_icp_agent_day fact_agent_day",
}

DEFAULT_ARGS = {
    "owner": "oss911",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "execution_timeout": timedelta(minutes=30),
}
TASK_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",   # offline: never try to reach the internet
    "DO_NOT_TRACK": "1",
}


def _task(task_id: str, command: str) -> BashOperator:
    return BashOperator(task_id=task_id, bash_command=DBT.format(command=command), pool=POOL,
                        skip_on_exit_code=None, env=TASK_ENV, append_env=False)


with open(MANIFEST, encoding="utf-8") as _fh:
    _sources = json.load(_fh)["sources"]

for _s in _sources:
    _sid = _s["source_id"]
    if _sid not in BUILDS:
        continue
    with DAG(dag_id=f"build_{_sid}", description=f"dbt build of {_sid}: {BUILDS[_sid]}",
             schedule=_s["schedule"], start_date=datetime(2026, 10, 1, tzinfo=UTC), catchup=False,
             max_active_runs=1, default_args=DEFAULT_ARGS, tags=["build", _sid]) as _d:
        _task("dbt_build", f"build --select {BUILDS[_sid]}")
    globals()[_d.dag_id] = _d

with DAG(dag_id="maintenance_partitions", description="Monthly partitions for this month and the next three (DD-77)",
         schedule="30 1 * * *", start_date=datetime(2026, 10, 1, tzinfo=UTC), catchup=False, max_active_runs=1,
         default_args=DEFAULT_ARGS, tags=["maintenance"]) as maintenance_partitions:
    _task("ensure_partitions", "run-operation ensure_partitions_ahead --args '{months: 3}'")

# Mart publication (macros/mart.sql). Nightly at 04:00 UTC: after the 02:00 re-sweep of a source in UTC (the fake
# ICP) and 01:00 for one in Asia/Riyadh, with the builds every few minutes in between; a re-sweep not yet in core
# only delays that day's closing to the next night
with DAG(dag_id="publish_marts_intraday", description="Provisional mart rows of the days not closed yet (hourly)",
         schedule="15 * * * *", start_date=datetime(2026, 10, 1, tzinfo=UTC), catchup=False, max_active_runs=1,
         default_args=DEFAULT_ARGS, tags=["publish"]) as publish_marts_intraday:
    _task("publish", "build --select mart --vars '{mart_kind: intraday}'")

with DAG(dag_id="publish_marts_nightly", description="Close the final days of mart, correct the last 7 closed days",
         schedule="0 4 * * *", start_date=datetime(2026, 10, 1, tzinfo=UTC), catchup=False, max_active_runs=1,
         default_args=DEFAULT_ARGS, tags=["publish"]) as publish_marts_nightly:
    # Closing needs each source's first re-sweep day (contracts/resweep.yml, carried by the manifest): source days
    # before it are history, from it on each must have been re-swept (macros/mart.sql, mart_final_days)
    _nightly_vars = {"mart_kind": "nightly",
                     "resweep_from": {s["source_id"]: s["resweep_from"] for s in _sources if s.get("resweep_from")}}
    _task("publish", f"build --select fact_call mart --vars {shlex.quote(json.dumps(_nightly_vars))}")

# After the nightly publication
with DAG(dag_id="maintenance_checks", description="Call-level consistency tests over every call (full_check)",
         schedule="30 4 * * *", start_date=datetime(2026, 10, 1, tzinfo=UTC), catchup=False, max_active_runs=1,
         default_args=DEFAULT_ARGS, tags=["maintenance"]) as maintenance_checks:
    _task("full_checks", "test --select fact_call_recomputes_from_legs fact_call_for_every_call batches_reach_core "
                         "mart_closing_not_behind agent_day_columns_match_contract agent_day_totals_decrease "
                         "--vars '{full_check: true}'")
