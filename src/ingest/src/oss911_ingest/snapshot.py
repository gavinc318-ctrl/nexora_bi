"""Version-gated snapshot ingestion: ICP reference data (contract if03d · DD-94 · LD-01 4.6).

The configuration-sync GETs (synwas) return whole datasets from the gateway's own copy, with no paging and no
increments; a version probe says which version of that copy is current. One version covers a scope:
  - the CTI version covers dataset vdns                                    scope "vdns@<ccId>"
  - each VDN's version covers the seven other datasets of that VDN         scope "<dataset>@<ccId>/<vdnId>"
so the version is compared per (dataset, scope), against what meta.ingest_batch says was taken last:
  greater   fetch the dataset and write every record (a snapshot), one batch per dataset
  equal     nothing to do
  smaller   not applied: a clarification item -- a rollback, or a lagging node; a person decides (DD-82 item four)
Every batch counts as taken, quarantined ones too: a held snapshot is not fetched again every 30 minutes.
Only a scope whose sync status is 2 (succeeded) is fetched; in progress or failed is skipped and reported.
Every CTI and VDN the probe lists is taken into raw, but the dimensions are built from the contract's site_vdn only
(DD-94 item seventeen): any other one, or the site's missing from the probe, is a clarification item.

Each batch commits on its own, as in the CDR path: a failure stops the run, and the datasets not yet taken at that
version are fetched by the next run. Nothing here calls the gateway's sync actions (POST syncti / synvdn): they
write, and delete with a VDN (DD-94 item four). If nothing syncs the copy, its endTime stops moving; a copy older
than version_stale_days is reported in the run's message.

Types: the document and its examples disagree (ids typed int, sent as strings; times typed string, sent as epoch
milliseconds; the probe's result typed array, sent as an object -- DD-94 0-i). Both forms are read; raw keeps the
records exactly as received.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import ConfigError
from .contract import effective_stale_days
from .icp import IcpClient, IcpError

SYNC_OK = 2              # WAS synchronization status: 0 initial, 1 in progress, 2 succeeded, 3 failed (p.1031)


@dataclass
class Version:
    cc_id: str
    vdn_id: str | None       # None: the CTI-level version
    version: int
    status: int | None
    end_time: datetime | None

    @property
    def label(self) -> str:
        return f"CTI {self.cc_id}" if self.vdn_id is None else f"VDN {self.cc_id}/{self.vdn_id}"


def _as_int(value, what: str) -> int:
    if isinstance(value, bool):
        raise IcpError(f"version probe: {what} is not a number: {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise IcpError(f"version probe: {what} is not a number: {value!r}") from None


def _as_time(value) -> datetime | None:
    """Epoch milliseconds, as number or as text. Anything else is unknown (None), never guessed."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_versions(result) -> list[Version]:
    """The probe's result -> [CTI version, VDN versions...]. Accepts the object of the example and a list of them."""
    entries = result if isinstance(result, list) else [result]
    out: list[Version] = []
    for e in entries:
        if not isinstance(e, dict):
            raise IcpError(f"version probe: unexpected result {type(e).__name__}")
        cc = str(_as_int(e.get("ccId"), "ccId"))
        out.append(Version(cc, None, _as_int(e.get("version"), "version"),
                           _as_int(e["status"], "status") if e.get("status") is not None else None,
                           _as_time(e.get("endTime"))))
        for v in e.get("vdnsVersion") or []:
            if not isinstance(v, dict):
                raise IcpError("version probe: vdnsVersion holds a non-object")
            out.append(Version(cc, str(_as_int(v.get("vdnId"), "vdnId")), _as_int(v.get("version"), "VDN version"),
                               _as_int(v["status"], "status") if v.get("status") is not None else None,
                               _as_time(v.get("endTime"))))
    if not out:
        raise IcpError("version probe: no version in the result")
    return out


def record_hash(record: dict) -> bytes:
    """SHA-256 over every field received, independent of key order (the gateway gives no order guarantee)."""
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).digest()


def check_snapshot(records, key: str | None, previous_rows: int | None) -> tuple[list[str], int]:
    """Batch checks (contract if03d quality.batch_checks). Returns (quarantine reasons, records without a key)."""
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        return ["result is not a list of records"], 0
    problems = []
    keyless = 0
    if key is not None:
        seen: dict[str, int] = {}
        for r in records:
            k = r.get(key)
            if k is None or k == "":
                keyless += 1
                continue
            seen[str(k)] = seen.get(str(k), 0) + 1
        dups = sorted(k for k, n in seen.items() if n > 1)
        if dups:
            # A snapshot is the whole set: a repeated id means it is not one consistent copy
            problems.append(f"{key} repeated within the snapshot: {dups[:10]}" + (" ..." if len(dups) > 10 else ""))
        if keyless:
            # Nor is one with records that cannot be identified: applied, every entity among them would look gone
            # (DD-94 item fifteen)
            problems.append(f"snapshot incomplete: {keyless} row(s) without {key}")
    if not records and previous_rows:
        # Applied as it is, an empty snapshot would close every agent or queue in core
        problems.append(f"empty snapshot, while the last one taken for this scope had {previous_rows} record(s)")
    return problems, keyless


class SnapshotIngest:
    """One run of a version-gated source. Used by runner.Runner after the contract has been read and validated."""

    def __init__(self, runner, conn, client: IcpClient, definition: dict, version: int, overrides: dict):
        self.r = runner
        self.conn = conn
        self.client = client
        self.d = definition
        self.contract_version = version
        self.stale_days = effective_stale_days(definition, overrides)
        self.params = {"version_stale_days": self.stale_days}
        self.messages: list[str] = []
        self.seq = 0

    def run(self) -> str | None:
        ep = self.d["endpoint"]
        result, _ = self.client.get_json(ep["version_probe"]["path"])
        versions = parse_versions(result)
        vdn_versions = [v for v in versions if v.vdn_id is not None]
        self.r.wm_to = "; ".join(f"{v.label} v{v.version}" for v in versions)

        # AS-30: one VDN, the contract's site_vdn. Everything is still taken -- raw records what the source has --
        # but only the site's snapshots become dimensions, so someone must know about any other (DD-94 item seventeen)
        site = ep["site_vdn"]
        for v in versions:
            if v.cc_id != site["cc_id"] or (v.vdn_id is not None and v.vdn_id != site["vdn_id"]):
                self._clarify("vdnsVersion", f"{v.label} is not the site's (ccId {site['cc_id']}, vdnId "
                              f"{site['vdn_id']}): taken into raw, not into the dimensions", 1)
        if not any(v.cc_id == site["cc_id"] and v.vdn_id == site["vdn_id"] for v in vdn_versions):
            label = f"VDN {site['cc_id']}/{site['vdn_id']}"
            self._clarify("site_vdn", f"{label} is not in the version probe", 1)
            self.messages.append(f"{label} (site_vdn) is not in the version probe: the dimensions cannot change")

        for v in versions:
            datasets = [ds for ds in ep["datasets"] if ds["scope"] == ("cti" if v.vdn_id is None else "vdn")]
            if v.status != SYNC_OK:
                self.messages.append(f"{v.label}: sync status {v.status}, not fetched")
                continue
            taken = {ds["name"]: self._taken(self._scope(ds, v)) for ds in datasets}
            newest = max((t[0] for t in taken.values() if t[0] is not None), default=None)
            if newest is not None and v.version < newest:
                # One version covers all the datasets of its scope, so a step back is one item, not one per dataset
                self._clarify("snapshot_version", f"{v.label}: version {v.version} is older than {newest}, "
                              "already taken", 1)
                self.messages.append(f"{v.label}: version went back from {newest} to {v.version}, not applied")
                self._stale(v)
                continue
            for ds in datasets:
                self._one_dataset(ds, v, *taken[ds["name"]])
            self._stale(v)
        self.conn.commit()
        return "; ".join(self.messages) or None

    # ------------------------------------------------------------------------------------------------------------
    def _scope(self, ds: dict, v: Version) -> str:
        return f"{ds['name']}@{v.cc_id}" if v.vdn_id is None else f"{ds['name']}@{v.cc_id}/{v.vdn_id}"

    def _taken(self, scope: str) -> tuple[int | None, int | None]:
        """The newest version taken for the scope, and how many records that snapshot had."""
        row = self.conn.execute(
            """SELECT snapshot_version, row_count FROM meta.ingest_batch
                WHERE source_id = %s AND mode = 'snapshot' AND snapshot_scope = %s
                ORDER BY snapshot_version DESC, committed_at DESC LIMIT 1""",
            (self.r.source_id, scope)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def _one_dataset(self, ds: dict, v: Version, taken: int | None, taken_rows: int | None) -> None:
        scope = self._scope(ds, v)
        if taken == v.version:
            return
        path = ds["path"].replace("{ccId}", v.cc_id).replace("{vdnId}", v.vdn_id or "")
        records, body = self.client.get_json(path)
        quarantine, keyless = check_snapshot(records, ds.get("key"), taken_rows)
        rows = records if isinstance(records, list) and all(isinstance(x, dict) for x in records) else []
        self.seq += 1
        self.conn.execute(
            """INSERT INTO meta.ingest_batch (run_id, batch_seq, source_id, mode, window_from, window_to,
                   row_count, rows_written, file_sha256, file_bytes, contract_version, effective_params,
                   snapshot_scope, snapshot_version, snapshot_synced_at)
               VALUES (%s, %s, %s, 'snapshot', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (self.r.run_id, self.seq, self.r.source_id, "" if taken is None else str(taken), str(v.version),
             len(rows), len(rows), hashlib.sha256(body).digest(), len(body), self.contract_version,
             json.dumps(self.params), scope, v.version, v.end_time))
        key = ds.get("key")
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO raw.icp_reference (dataset, cc_id, vdn_id, src_natural_key, snapshot_version, payload,
                                                  run_id, batch_seq, contract_version, content_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [(ds["name"], v.cc_id, v.vdn_id,
                  None if key is None or rec.get(key) in (None, "") else str(rec.get(key)),
                  v.version, json.dumps(rec, ensure_ascii=False), self.r.run_id, self.seq, self.contract_version,
                  record_hash(rec)) for rec in rows])
        if quarantine:
            self.conn.execute(
                "INSERT INTO meta.quarantine_batch (run_id, batch_seq, failure_reason, row_count) VALUES (%s, %s, %s, %s)",
                (self.r.run_id, self.seq, "; ".join(quarantine), len(rows)))
        self.conn.commit()
        self.r.committed += 1
        self.r.rows_received += len(rows)
        self.r.say(f"  batch {self.seq} {scope} v{v.version} (was {taken}): {len(rows)} record(s)"
                   + (f", {keyless} without {key}" if keyless else "")
                   + (f", QUARANTINED: {'; '.join(quarantine)}" if quarantine else ""))

    def _stale(self, v: Version) -> None:
        if v.end_time is None:
            self.messages.append(f"{v.label}: no readable endTime, staleness unknown")
            return
        age = datetime.now(timezone.utc) - v.end_time
        if age > timedelta(days=self.stale_days):
            # Nothing has synced the gateway's copy (Q33): the data may be old without any error
            self.messages.append(f"STALE: {v.label} last synced {v.end_time:%Y-%m-%d %H:%M} UTC, "
                                 f"{age.days} days ago (limit {self.stale_days})")

    def _clarify(self, column: str, value: str, n: int) -> None:
        # One open item per value, as for CDR codes (runner.BatchWriter)
        self.conn.execute(
            """INSERT INTO meta.clarification_queue (source_id, src_column, src_value, impacted_rows)
               SELECT %s, %s, %s, %s WHERE NOT EXISTS (
                   SELECT 1 FROM meta.clarification_queue
                    WHERE source_id = %s AND src_column = %s AND src_value = %s AND status IN ('open', 'asked'))""",
            (self.r.source_id, column, value, n, self.r.source_id, column, value))


def check_mode(mode: str, source_id: str) -> None:
    if mode != "incremental":
        raise ConfigError(f"{source_id} is ingested by version-gated snapshots; it has no time window, so "
                          f"--mode {mode} does not apply (run it without --mode)")
