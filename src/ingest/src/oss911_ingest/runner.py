"""One ingestion run: lock, read the contract, cut the window into batches, fetch, check, write (LD-01 2.5).

Structure of a run (DD-88 · DD-89):
  - one database connection for the whole run, through pgBouncer's session pool; the per-source advisory lock
    is taken on it and dies with it (DD-85). The connection is never swapped while the run lasts
  - the contract (definition, contract_version, enabled) and its hot-tuned values are read in ONE statement at
    the start -- one statement is one snapshot, so a deployment committing in between cannot pair an old contract
    with new values (DD-88 item eighteen). No row lock is taken and none is held while downloading. Both are
    checked with the single set of rules (contract.py) and used, unchanged, for the whole run
  - [start, end] is cut into batches no longer than the interface allows, fetched one after another
  - each batch commits in its own transaction: meta.ingest_batch, the raw rows, a quarantine registration if
    any, clarification items, and -- in incremental mode -- the watermark. A failure rolls back that batch
    only and stops the run; later batches are not attempted, so the watermark never jumps a gap
  - meta.run_log gets one row when the run ends: success, partial or failed

Modes (DD-89):
  incremental  start = watermark - lookback, end = now in the source's time zone; the watermark moves forward
               after each batch. The first run of a source has no watermark; it needs --start-at, given once
  backfill     explicit window; batches are marked backfill; the watermark is not touched
  resweep      the daily re-sweep (LD-01 4.4) of one day, given by the DAG from its logical date (--day): that whole
               day in the source's time zone, extended back by the effective lookback (the lookback is resolved
               here, from the contract and the hot-tuned value, so the DAG passes only the day -- DD-89 note). Like a
               backfill it leaves the watermark untouched; its batches are marked resweep (0005); never from "now",
               so a retry sweeps the same day and a missed day is swept when the DAG catches up. It catches calls
               longer than the lookback, which incremental runs never see end. After it, the calls in the window
               still without a final leg are reported, received and usable (OBSERVE_AFTER_RESWEEP)

Termination: SIGTERM (Airflow's execution timeout, or a task stopped by hand) is turned into an exception, so the
current batch is rolled back and the run ends like any failed run -- run_log written, exit code 1 -- instead of
the process vanishing without a record.
"""

from __future__ import annotations

import json
import signal
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .cdr import check_cdr, open_calls
from .config import ConfigError, db_settings, read_env_file, require
from .contract import effective_lookback_minutes, validate_contract, validate_overrides
from .icp import IcpClient, IcpError
from .parse import content_hash, parse_csv

SOURCE_TIME = "%Y-%m-%d %H:%M:%S"       # how windows and watermarks are written: the source's own notation

# Sources this program can ingest, and the batch checks of each. A source with a contract but no entry here
# cannot run: there is no generic "accept anything" path, because every source's checks are its own.
CHECKS = {"icp_cdr_voice": check_cdr}
# What a re-sweep reports afterwards, per source (LD-01 4.4)
OBSERVE_AFTER_RESWEEP = {"icp_cdr_voice": open_calls}

EXIT_OK, EXIT_FAILED = 0, 1


class RunFailed(Exception):
    """The run cannot go on; ends with exit code 1 and a failed / partial run_log row."""


class Terminated(BaseException):
    """SIGTERM arrived. BaseException, like KeyboardInterrupt: no "except Exception" may swallow it."""


def _on_sigterm(signum, frame):
    raise Terminated("terminated by SIGTERM (Airflow execution timeout, or the task was stopped); "
                     "the batch in progress was rolled back")


@dataclass
class Batch:
    seq: int
    start: datetime
    end: datetime


def fmt(t: datetime) -> str:
    return t.strftime(SOURCE_TIME)


def cut(start: datetime, end: datetime, max_span: timedelta, min_span: timedelta) -> list[tuple[datetime, datetime]]:
    """Cut [start, end) into slices of at most max_span. A last slice shorter than min_span is widened backwards
    into the previous one (the overlap is absorbed by de-duplication); a window shorter than min_span is an error."""
    if end - start < min_span:
        raise ConfigError(f"window {fmt(start)} / {fmt(end)} is shorter than the interface minimum {min_span}")
    out = []
    s = start
    while s < end:
        e = min(s + max_span, end)
        out.append((s, e))
        s = e
    if out[-1][1] - out[-1][0] < min_span:
        out[-1] = (out[-1][1] - min_span, out[-1][1])
    return out


def lock_key(source_id: str) -> str:
    return f"oss911.ingest.{source_id}"


class Runner:
    def __init__(self, source_id: str, mode: str, window: tuple[datetime, datetime] | None,
                 start_at: datetime | None, out=sys.stdout, resweep_day: datetime | None = None):
        self.source_id = source_id
        self.mode = mode
        self.resweep_day = resweep_day
        self.window = window
        self.start_at = start_at
        self.out = out
        self.run_id = uuid.uuid4()
        self.started_at = None
        self.committed = 0
        self.rows_received = 0
        self.wm_from: str | None = None
        self.wm_to: str | None = None

    def say(self, text: str) -> None:
        print(text, file=self.out, flush=True)

    # ------------------------------------------------------------------------------------------------------------
    def run(self) -> int:
        signal.signal(signal.SIGTERM, _on_sigterm)
        settings = db_settings()   # configuration first: a missing file is a configuration error (2)
        import psycopg             # imported here so that usage errors are reported without the driver

        try:
            conn = psycopg.connect(**settings.connect_kwargs())
        except psycopg.Error as exc:
            raise RunFailed(f"cannot connect to the database: {exc}") from None
        with conn:
            self.started_at = conn.execute("SELECT now()").fetchone()[0]
            contract = self._read_contract(conn)
            conn.commit()
            if contract is None:
                raise ConfigError(f"source {self.source_id} has no contract in meta.source_contract "
                                  "(not released, or 45-contracts not run)")
            definition, version, enabled, overrides = contract
            if not enabled:
                self.say(f"source {self.source_id} is disabled (meta.source_contract.enabled); nothing done")
                return EXIT_OK

            got = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                               (lock_key(self.source_id),)).fetchone()[0]
            conn.commit()
            if not got:
                msg = f"another run of {self.source_id} holds the advisory lock (DD-85)"
                self._run_log(conn, version, "failed", msg)
                raise RunFailed(msg)
            try:
                return self._locked(conn, definition, version, overrides)
            finally:
                if not conn.closed:
                    conn.rollback()
                    conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock_key(self.source_id),))
                    conn.commit()

    def _read_contract(self, conn):
        # One statement, one snapshot: the contract and the hot-tuned values belong to the same moment
        return conn.execute(
            """SELECT c.definition, c.contract_version, c.enabled,
                      coalesce((SELECT jsonb_object_agg(p.param_name, p.param_value)
                                  FROM meta.source_runtime_param p WHERE p.source_id = c.source_id), '{}'::jsonb)
                 FROM meta.source_contract c WHERE c.source_id = %s""",
            (self.source_id,)).fetchone()

    # ------------------------------------------------------------------------------------------------------------
    def _locked(self, conn, definition: dict, version: int, overrides: dict) -> int:
        if self.source_id not in CHECKS:
            raise ConfigError(f"this program has no ingestion for source {self.source_id}")
        doc = dict(definition, enabled=True)
        problems = validate_contract(doc) + validate_overrides(doc, overrides)
        if problems:
            raise ConfigError(f"contract {self.source_id} v{version} in the database fails validation: "
                              + "; ".join(problems))
        lookback = effective_lookback_minutes(doc, overrides)
        params = {"lookback_minutes": lookback}

        win = definition["window"]
        try:
            tz = ZoneInfo(win.get("source_timezone") or "")
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f"contract {self.source_id}: window.source_timezone missing or unknown") from None
        max_span = timedelta(hours=win["max_span_hours"])
        min_span = timedelta(seconds=win["min_span_seconds"])

        if self.mode == "incremental":
            row = conn.execute("SELECT watermark_value FROM meta.watermark_state WHERE source_id = %s",
                               (self.source_id,)).fetchone()
            conn.commit()
            if row is None and self.start_at is None:
                raise ConfigError(f"{self.source_id} has no watermark yet; give the first run --start-at")
            if row is not None and self.start_at is not None:
                raise ConfigError(f"{self.source_id} already has a watermark ({row[0]}); --start-at is only "
                                  "for the first run. To fetch an earlier period, use --mode backfill")
            watermark = self.start_at if row is None else datetime.strptime(row[0], SOURCE_TIME)
            self.wm_from = fmt(watermark)
            start = watermark - timedelta(minutes=lookback)
            end = datetime.now(tz).replace(tzinfo=None, microsecond=0)
            if end < watermark:
                # A watermark in the future means a wrong --start-at, a clock that moved back, or the wrong
                # source_timezone; fetching would ask for a window that has not happened
                raise ConfigError(f"watermark {fmt(watermark)} is later than now ({fmt(end)}, {tz.key})")
        elif self.mode == "resweep":
            # The given day in the source's time zone, extended back by the lookback so the legs around its first
            # midnight are fetched once more as well
            start = self.resweep_day - timedelta(minutes=lookback)
            end = self.resweep_day + timedelta(days=1)
            if end > datetime.now(tz).replace(tzinfo=None):
                raise ConfigError(f"--day {self.resweep_day:%Y-%m-%d} has not ended yet in {tz.key}; "
                                  "a re-sweep covers a whole past day")
            self.window = (start, end)
            self.wm_from = None
        else:
            start, end = self.window
            self.wm_from = None

        slices = cut(start, end, max_span, min_span)
        self.say(f"run {self.run_id}: {self.source_id} v{version} {self.mode} "
                 f"{fmt(start)} / {fmt(end)}, {len(slices)} batch(es), lookback {lookback} min")

        client = self._icp_client(definition)
        # ingest_batch.mode: incremental, backfill, resweep (0005) -- the batch records which kind of run made it
        batch_mode = self.mode
        writer = BatchWriter(conn, self.source_id, version, self.run_id, batch_mode, params, definition)
        try:
            for seq, (s, e) in enumerate(slices, 1):
                self._one_batch(conn, client, writer, Batch(seq, s, e))
        except (IcpError, RunFailed, ConfigError, Terminated) as exc:
            outcome = "partial" if self.committed else "failed"
            self._record_failure(conn, version, outcome, f"batch {self.committed + 1}: {exc}")
            raise RunFailed(f"{outcome} after {self.committed} batch(es): {exc}") from None
        except BaseException as exc:
            outcome = "partial" if self.committed else "failed"
            self._record_failure(conn, version, outcome, f"batch {self.committed + 1}: {type(exc).__name__}: {exc}")
            raise
        message = None
        if self.mode == "resweep" and self.source_id in OBSERVE_AFTER_RESWEEP:
            message = OBSERVE_AFTER_RESWEEP[self.source_id](conn, definition, self.window, tz)
            conn.commit()
            self.say(f"  {message}")
        self._run_log(conn, version, "success", message)
        self.say(f"run {self.run_id}: success, {self.committed} batch(es), {self.rows_received} rows received")
        return EXIT_OK

    def _record_failure(self, conn, version: int, outcome: str, message: str) -> None:
        """Roll back the batch in progress and write the run_log row. If the connection itself is broken (e.g. the
        signal arrived inside a database call), say so on stderr; the server rolls the transaction back when the
        connection closes, so no half batch remains either way."""
        try:
            conn.rollback()
            self._run_log(conn, version, outcome, message)
        except Exception as exc:          # noqa: BLE001 -- the original failure is what gets reported
            print(f"could not write run_log ({type(exc).__name__}: {exc}); original failure: {message}",
                  file=sys.stderr)

    def _icp_client(self, definition: dict) -> IcpClient:
        host_ref = definition["endpoint"]["host_ref"]
        auth = definition["auth"]
        cred_ref = auth["obtain"]["credential_ref"]
        env = read_env_file("icp.env")
        prefix = f"OSS911_CRED_{cred_ref.upper()}"
        return IcpClient(
            base_url=require(env, f"OSS911_HOST_{host_ref.upper()}", "icp.env"),
            user=require(env, f"{prefix}_USER", "icp.env"),
            password=require(env, f"{prefix}_PASSWORD", "icp.env"),
            auth_path=auth["obtain"]["path"],
            token_header=auth["header"],
        )

    def _one_batch(self, conn, client: IcpClient, writer: "BatchWriter", b: Batch) -> None:
        ep = writer.definition["endpoint"]
        body = {k: (fmt(b.start) if v == "{{ window_from }}" else fmt(b.end) if v == "{{ window_to }}" else v)
                for k, v in ep["request_body"].items()}
        got = client.download(ep["path"], body)
        columns = [c["src"] for c in writer.definition["columns"]]
        parsed = parse_csv(got.body, columns)
        result = writer.write(b, parsed)
        if self.mode == "incremental":
            writer.advance_watermark(fmt(b.end))
        conn.commit()
        self.wm_to = fmt(b.end)              # how far this run got: the end of its last committed batch
        self.committed += 1
        self.rows_received += len(parsed.rows)
        self.say(f"  batch {b.seq} {fmt(b.start)} / {fmt(b.end)}: received {len(parsed.rows)}, "
                 f"written {result['written']}" + (f", QUARANTINED: {result['quarantine']}" if result["quarantine"] else ""))

    def _run_log(self, conn, version: int, outcome: str, message: str | None) -> None:
        conn.execute(
            """INSERT INTO meta.run_log (run_id, source_id, contract_version, started_at, finished_at,
                                         watermark_from, watermark_to, row_count, outcome, message)
               VALUES (%s, %s, %s, coalesce(%s, now()), clock_timestamp(), %s, %s, %s, %s, %s)""",
            (self.run_id, self.source_id, version, self.started_at,
             self.wm_from if self.mode == "incremental" else fmt(self.window[0]),
             self.wm_to, self.rows_received, outcome, message))
        conn.commit()


class BatchWriter:
    """Writes one batch inside the caller's transaction. The caller commits."""

    def __init__(self, conn, source_id: str, version: int, run_id, mode: str, params: dict, definition: dict):
        self.conn = conn
        self.source_id = source_id
        self.version = version
        self.run_id = run_id
        self.mode = mode
        self.params = params
        self.definition = definition
        self.target = definition["target_raw"]
        self.columns = [c["src"] for c in definition["columns"]]
        self.check = CHECKS[source_id]
        wm_col = definition["watermark"]["column"]
        self.wm_index_name = wm_col
        # raw column names are the contract's source column names in lower case (change 0004)
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS ingest_incoming ("
            + ", ".join(f"c{i} text" for i in range(len(self.columns)))
            + ", payload text NOT NULL, src_watermark text, content_hash text NOT NULL) ON COMMIT DELETE ROWS")
        conn.commit()

    def write(self, b: Batch, parsed) -> dict:
        from psycopg import sql

        quarantine = list(parsed.header_problems)
        if parsed.unparseable:
            quarantine.insert(0, f"unparseable: {parsed.unparseable}")
        clar = {}
        if not parsed.unparseable:
            res = self.check(parsed.header, parsed.rows, self.definition)
            quarantine += res.quarantine
            clar = res.unknown_values

        # 1. stage the received rows
        idx = {name: i for i, name in enumerate(parsed.header)}
        wm_i = idx.get(self.wm_index_name)
        with self.conn.cursor().copy(sql.SQL("COPY ingest_incoming ({}) FROM STDIN").format(
                sql.SQL(", ").join(sql.Identifier(n) for n in
                                   [f"c{i}" for i in range(len(self.columns))]
                                   + ["payload", "src_watermark", "content_hash"]))) as cp:
            for row in parsed.rows:
                values = [row[idx[c]] if c in idx else None for c in self.columns]
                payload = json.dumps(dict(zip(parsed.header, row)), ensure_ascii=False)
                cp.write_row(values + [payload, row[wm_i] if wm_i is not None else None,
                                       content_hash(parsed.header, row).hex()])

        # 2. what is new: not already in raw with the same key and hash, ignoring rows of pending or discarded
        #    batches (DD-82 item two). Counted first because ingest_batch is written before the raw rows (foreign
        #    key) and ingest cannot update it afterwards
        key = [c for c in self.definition["idempotent_key"]]
        key_i = [self.columns.index(k) for k in key]
        raw_cols = [c.lower() for c in self.columns]
        tbl = sql.SQL(".").join(sql.Identifier(p) for p in self.target.split("."))
        match = sql.SQL(" AND ").join(
            sql.SQL("r.{} = i.{}").format(sql.Identifier(raw_cols[k]), sql.Identifier(f"c{k}")) for k in key_i)
        new_rows = sql.SQL(
            "SELECT i.* FROM ingest_incoming i WHERE NOT EXISTS ("
            " SELECT 1 FROM {tbl} r WHERE {match} AND r.content_hash = decode(i.content_hash, 'hex')"
            " AND NOT EXISTS (SELECT 1 FROM meta.quarantine_batch q WHERE q.run_id = r.run_id"
            " AND q.batch_seq = r.batch_seq AND q.state IN ('pending', 'discarded')))").format(tbl=tbl, match=match)
        self.conn.execute(sql.SQL("CREATE TEMP TABLE ingest_new ON COMMIT DROP AS {}").format(new_rows))
        written = self.conn.execute("SELECT count(*) FROM ingest_new").fetchone()[0]

        # 3. the batch row, then the raw rows (they reference it)
        self.conn.execute(
            """INSERT INTO meta.ingest_batch (run_id, batch_seq, source_id, mode, window_from, window_to,
                   row_count, rows_written, received_header, file_sha256, file_bytes, archive_key,
                   contract_version, effective_params)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s)""",
            (self.run_id, b.seq, self.source_id, self.mode, fmt(b.start), fmt(b.end), len(parsed.rows), written,
             parsed.header or None, parsed.sha256, parsed.size, self.version, json.dumps(self.params)))
        self.conn.execute(sql.SQL(
            "INSERT INTO {tbl} ({cols}, payload, run_id, batch_seq, contract_version, src_watermark, content_hash) "
            "SELECT {src}, payload::jsonb, %s, %s, %s, src_watermark, decode(content_hash, 'hex') FROM ingest_new"
        ).format(tbl=tbl,
                 cols=sql.SQL(", ").join(sql.Identifier(c) for c in raw_cols),
                 src=sql.SQL(", ").join(sql.Identifier(f"c{i}") for i in range(len(self.columns)))),
            (self.run_id, b.seq, self.version))

        # 4. quarantine, new columns, clarification items
        if quarantine:
            self.conn.execute(
                "INSERT INTO meta.quarantine_batch (run_id, batch_seq, failure_reason, row_count) VALUES (%s, %s, %s, %s)",
                (self.run_id, b.seq, "; ".join(quarantine), len(parsed.rows)))
        for name in parsed.unknown_columns:
            # A new column is registered as unknown (DD-07 · LD-01 2.3); one row per column, ever
            self.conn.execute(
                """INSERT INTO meta.mapping_registry (source_id, src_column, mapping_state, note)
                   VALUES (%s, %s, 'unknown', %s) ON CONFLICT DO NOTHING""",
                (self.source_id, name, f"first seen in run {self.run_id} batch {b.seq}"))
        for column, counts in clar.items():
            for value, n in counts.items():
                # One open item per value: the queue is sorted by impact and is useless if it repeats every 5 minutes.
                # impacted_rows is the count when first raised (ingest cannot update the item)
                self.conn.execute(
                    """INSERT INTO meta.clarification_queue (source_id, src_column, src_value, impacted_rows)
                       SELECT %s, %s, %s, %s WHERE NOT EXISTS (
                           SELECT 1 FROM meta.clarification_queue
                            WHERE source_id = %s AND src_column = %s AND src_value = %s
                              AND status IN ('open', 'asked'))""",
                    (self.source_id, column, value, n, self.source_id, column, value))
        return {"written": written, "quarantine": "; ".join(quarantine)}

    def advance_watermark(self, value: str) -> None:
        # Forward only: GREATEST on the fixed-width text form, which sorts like the time it writes
        self.conn.execute(
            """INSERT INTO meta.watermark_state (source_id, watermark_value, watermark_type, last_success_at, updated_at)
               VALUES (%s, %s, %s, now(), now())
               ON CONFLICT (source_id) DO UPDATE
                  SET watermark_value = GREATEST(meta.watermark_state.watermark_value, EXCLUDED.watermark_value),
                      last_success_at = now(), updated_at = now()""",
            (self.source_id, value, self.definition["watermark"]["type"]))
