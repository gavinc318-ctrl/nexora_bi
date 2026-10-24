"""The DAG manifest: what Airflow schedules, derived from the contracts in the database (LD-01 2.1 · DD-84).

The DAGs read only this file, never the contract YAML: the contracts have one runtime reading point, the
database. Deployment (45-contracts.yml) writes the manifest as its last step, after the contract sync has
committed. Rules:

  - every source managed in meta.source_contract is listed, enabled = false included: taking a contract out of
    release.yml does not stop its source, and its DAG stays (start / stop is meta.source_contract.enabled only)
  - each source's pool comes from contracts/pools.yml; a managed source without a pool fails, a concurrency
    limit is never implied
  - written atomically (temporary file in the same directory, then rename), and only when the content changed,
    so a repeated deployment reports "unchanged"
  - written under the same advisory lock as the contract sync (oss911.contract_sync), held from reading the
    database to the rename: two deployments run one after the other, and the file always reflects the database
    as of the moment its writer held the lock -- an earlier deployment can never write an older manifest last
  - committing the sync and writing the manifest are two operations, not one. A failure in between leaves the
    release unfinished: deployment reports it, and a rerun regenerates the manifest even if every contract is
    unchanged

Before the sync, deployment builds a candidate manifest (the database plus the released YAML laid over it,
i.e. what the database will hold after the sync) and parses the DAGs with it.
"""

from __future__ import annotations

import json
import os
import tempfile

LOCK_KEY = "oss911.contract_sync"     # the key contract_sync.sql locks with pg_advisory_xact_lock(hashtext(...))


class ManifestError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__(problems)
        self.problems = problems

    def __str__(self) -> str:
        return "; ".join(self.problems)


def has_time_window(definition: dict) -> bool:
    """Incremental by time window with a lookback: such a source gets the daily re-sweep (LD-01 4.4)."""
    win = definition.get("window") if isinstance(definition.get("window"), dict) else {}
    return "max_span_hours" in win and "lookback_minutes" in win


def build(managed: dict[str, dict], pools: dict[str, str]) -> dict:
    """managed: source_id -> {"contract_version", "enabled", "definition"}. Returns the manifest document."""
    problems, sources = [], []
    for sid in sorted(managed):
        m = managed[sid]
        schedule = ((m["definition"].get("window") or {}).get("schedule")) if isinstance(m["definition"], dict) else None
        if not isinstance(schedule, str) or not schedule.strip():
            problems.append(f"{sid}: contract v{m['contract_version']} has no window.schedule")
        if sid not in pools:
            problems.append(f"{sid}: no pool in contracts/pools.yml (DD-85)")
        sources.append({
            "source_id": sid,
            "contract_version": m["contract_version"],
            "enabled": m["enabled"],
            "schedule": schedule,
            "pool": pools.get(sid),
            "resweep": has_time_window(m["definition"]),
        })
    if problems:
        raise ManifestError(problems)
    return {"sources": sources}


def serialize(doc: dict) -> bytes:
    # Deterministic: same database state, same bytes -- so "unchanged" can be detected
    return (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_atomic(path: str, data: bytes) -> bool:
    """Write data to path through a temporary file and rename. Returns False when the file already holds it."""
    try:
        with open(path, "rb") as fh:
            if fh.read() == data:
                return False
    except FileNotFoundError:
        pass
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".dags.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)                  # the DAG processor reads it; it holds no secret
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return True
