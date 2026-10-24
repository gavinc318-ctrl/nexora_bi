"""Ingestion DAGs, one per released source contract (DD-84 · DD-85 · LD-01 2.5 · LD-06 2.4).

Generated from the contracts that deployment copied to /opt/oss911/contracts (45-contracts.yml): the DAGs
follow the release list, they are not edited by hand (LD-06: "DAGs are generated from the contract registry;
the console shows them, it does not edit them"). Airflow only orchestrates -- when to run and how often to
retry. Fetching, checking, writing raw and moving the watermark all happen inside `oss911-ingest`.

Rules carried by every task here; the ingestion program and its exit codes depend on them:
  - skip_on_exit_code=None: BashOperator would otherwise treat exit code 99 as "skipped" (DD-84)
  - a global pool per interface that cannot take concurrent requests (DD-85). The pool is one row in the
    metadata database shared by the whole cluster, so two ETL nodes never download at once; a manual run
    outside Airflow is kept out by the per-source advisory lock inside the program
  - retries are bounded and the same for every exit code (LD-01 2.5): a failed batch moves no watermark and
    raw de-duplicates, so a retry can at worst repeat work
  - env is set explicitly, without the Airflow process environment: the worker's environment holds the
    metadata database connection string, and the program reads its own credentials from /etc/oss911
    (LD-01 2.5: "no password through the DAG, a template or the Airflow parent process")
  - one run at a time per source (max_active_runs=1), no catch-up: an incremental run always starts from the
    watermark, so catching up is the program's job, not the scheduler's (one run after a pause fetches the gap)

The polling interval is the contract's window.schedule. It is a release item, not hot-tunable (LD-06 2.4).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import yaml
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

CONTRACTS_DIR = os.environ.get("OSS911_CONTRACTS_DIR", "/opt/oss911/contracts")
INGEST_BIN = "/opt/oss911/venv-ingest/bin/oss911-ingest"

# Which global pool each source's downloads go through (DD-85; pools are created by 30-python.yml).
# A released source missing here is an error, not a DAG without a pool: concurrency limits must be explicit.
POOLS = {
    "icp_cdr_voice": "icp_cms",
}

DEFAULT_ARGS = {
    "owner": "oss911",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "execution_timeout": timedelta(minutes=30),   # a 24-hour slice downloads in seconds; 30 min means it is stuck
}


def released_contracts(base: str) -> list[dict]:
    with open(os.path.join(base, "release.yml"), encoding="utf-8") as fh:
        files = (yaml.safe_load(fh) or {}).get("release") or []
    out = []
    for name in files:
        with open(os.path.join(base, os.path.basename(name)), encoding="utf-8") as fh:
            out.append(yaml.safe_load(fh))
    return out


def build(contract: dict) -> DAG:
    source = contract["source_id"]
    if source not in POOLS:
        raise ValueError(f"source {source} is released but has no pool in POOLS of oss911_ingest.py (DD-85)")
    with DAG(
        dag_id=f"ingest_{source}",
        description=f"Incremental ingestion of {source} (contract v{contract['contract_version']})",
        schedule=contract["window"]["schedule"],
        start_date=datetime(2026, 10, 1),
        catchup=False,
        max_active_runs=1,
        default_args=DEFAULT_ARGS,
        tags=["ingest", source],
    ) as dag:
        BashOperator(
            task_id="incremental",
            bash_command=f"{INGEST_BIN} run --source {source}",
            pool=POOLS[source],
            skip_on_exit_code=None,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            append_env=False,
        )
    return dag


for _contract in released_contracts(CONTRACTS_DIR):
    globals()[f"ingest_{_contract['source_id']}"] = build(_contract)
