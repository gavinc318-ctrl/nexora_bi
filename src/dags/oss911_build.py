"""Model build DAGs (dbt) and daily maintenance (DD-84 · DD-85 · DD-89 · DD-91 items 10, 11).

Like the ingestion DAGs, generated from the DAG manifest /opt/oss911/release/dags.json -- never from the contracts.

  build_<source>           every <schedule> of the source: dbt build of the source's staging and core models,
                           with their tests. A separate DAG from ingest_<source>, not a second task behind it: one DAG
                           has max_active_runs=1, so a dbt failure and its retries would hold up ingestion (DD-91 item
                           10). The two never hand over through files -- staging takes what raw holds, by batch
                           (meta.staging_batch_state, DD-89), so build and ingestion need no ordering
  maintenance_partitions   daily: monthly partitions for this month and the next three, every partitioned table in
                           core, audit and raw (DD-77). A missing partition makes every write for that month fail

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
