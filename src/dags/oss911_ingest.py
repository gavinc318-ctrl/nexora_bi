"""Ingestion DAGs, generated from the DAG manifest (DD-84 · DD-85 · LD-01 2.1 · 2.5 · 4.4 · LD-06 2.4).

The DAGs read only the manifest /opt/oss911/release/dags.json, never the contract YAML: the contracts have one
runtime reading point, the database, and deployment writes the manifest from the database after the contract
sync has committed (`oss911-ingest dag-manifest`, 45-contracts.yml). Reading the YAML here would make a second
reading point, with a window in which the two disagree -- and a failed release would already have changed what
is scheduled. A missing or unreadable manifest is an import error: nothing is scheduled from a guess.

Per source in the manifest (every source managed in the database, disabled ones too -- start / stop is
meta.source_contract.enabled; a disabled source's run does nothing and ends with 0):
  ingest_<source>    every <schedule>: oss911-ingest run --source <source>                  (incremental)
  resweep_<source>   daily at 02:00 UTC: oss911-ingest run --source <source> --mode resweep  (sources with a
                     time window and lookback only; catches calls longer than the lookback, LD-01 4.4)
Both go through the source's pool, so they never download at the same time; the advisory lock in the program
covers manual runs outside Airflow.

Rules carried by every task here; the ingestion program and its exit codes depend on them:
  - skip_on_exit_code=None: BashOperator would otherwise treat exit code 99 as "skipped" (DD-84)
  - retries are bounded and the same for every exit code (LD-01 2.5): a failed batch moves no watermark and
    raw de-duplicates, so a retry can at worst repeat work
  - execution_timeout 30 min, a first value: batches commit one by one, so a timeout (SIGTERM, rolled back and
    logged by the program) loses only the batch in progress, and every run makes net progress
  - env is set explicitly, without the Airflow process environment: the worker's environment holds the
    metadata database connection string, and the program reads its own credentials from /etc/oss911
  - one run at a time per DAG, no catch-up: an incremental run always starts from the watermark, so catching up
    is the program's job, not the scheduler's
New DAGs start paused; enabling them is the first-go-live acceptance step (runbook/11), and redeployment never
changes the pause state.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

MANIFEST = os.environ.get("OSS911_DAG_MANIFEST", "/opt/oss911/release/dags.json")
INGEST_BIN = "/opt/oss911/venv-ingest/bin/oss911-ingest"
# The re-sweep runs in UTC whatever the scheduler's default time zone: it follows the source's day (the ICP
# contract's source_timezone is UTC), not the reporting day
RESWEEP_SCHEDULE = "0 2 * * *"
RESWEEP_TZ = pendulum.timezone("UTC")
RESWEEP_DAYS = 1                       # first value (DD-84 note); range to be set after ICP questions Q19 / Q20

DEFAULT_ARGS = {
    "owner": "oss911",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "execution_timeout": timedelta(minutes=30),
}
TASK_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}


def _dag(dag_id: str, description: str, schedule, tags: list[str]) -> DAG:
    return DAG(dag_id=dag_id, description=description, schedule=schedule,
               start_date=datetime(2026, 10, 1, tzinfo=RESWEEP_TZ), catchup=False, max_active_runs=1,
               default_args=DEFAULT_ARGS, tags=tags)


def _task(task_id: str, command: str, pool: str) -> BashOperator:
    return BashOperator(task_id=task_id, bash_command=command, pool=pool,
                        skip_on_exit_code=None, env=TASK_ENV, append_env=False)


with open(MANIFEST, encoding="utf-8") as _fh:
    _sources = json.load(_fh)["sources"]

for _s in _sources:
    _sid, _pool = _s["source_id"], _s["pool"]
    with _dag(f"ingest_{_sid}", f"Incremental ingestion of {_sid} (contract v{_s['contract_version']})",
              _s["schedule"], ["ingest", _sid]) as _d:
        _task("incremental", f"{INGEST_BIN} run --source {_sid}", _pool)
    globals()[_d.dag_id] = _d

    if _s["resweep"]:
        from airflow.timetables.trigger import CronTriggerTimetable

        with _dag(f"resweep_{_sid}", f"Daily re-sweep of {_sid}: the last {RESWEEP_DAYS} day(s) plus the lookback",
                  CronTriggerTimetable(RESWEEP_SCHEDULE, timezone=RESWEEP_TZ), ["ingest", "resweep", _sid]) as _d:
            _task("resweep", f"{INGEST_BIN} run --source {_sid} --mode resweep --days {RESWEEP_DAYS}", _pool)
        globals()[_d.dag_id] = _d
