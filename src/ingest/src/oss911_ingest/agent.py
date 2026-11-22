"""Agent metrics by observation: sign-in intervals (contract if03b) and day totals (contract if03c) · DD-98 · LD-01 4.1.

Both come from hindex/agent (interface reference p.935-936, p.1035), which takes ccId, vdnId, objectIds and indexIds
and nothing else: there is no date parameter. Until ICP answers Q36 a response is taken to be "today so far", so a run
is one observation of the current values (DD-98 item one) -- no window, no watermark, no backfill. A run:
  1. picks the agents to ask for from raw.icp_reference (ingestion cannot read core, DD-74): every agent ID in the
     site VDN's agents snapshot that was in force when today began (window.day_timezone), and in every trusted
     snapshot taken since -- an agent removed during the day is still asked for that day (DD-98 item three)
  2. asks for them in requests of at most objects.batch_size IDs, one batch each, every batch in its own transaction
     (meta.ingest_batch mode observation: window_from the day, window_to the observation time)
  3. writes, per key, only an observation whose content differs from the key's previous one (contract raw_write):
     the previous one is the latest not in a pending or discarded batch, so a value that changes back is written again
  4. registers, per batch, which agents were asked for and which came back (raw.icp_agent_coverage, DD-98 review
     note). An agent asked for but not returned is registered there, not quarantined: the request includes agents
     removed during the day, which the platform may leave out (Q40). dbt derives src_confirmed_at from it
Sign-in intervals: one raw row per interval, key (id, inTime); an interval belongs to the day of its inTime, so the
observation time does not matter for it. Day totals: one raw row per agent, key (id, date_key) with date_key the day
of the observation time; an observation within midnight_skip_minutes after midnight may still hold the previous
day's values (report batch delay) and is not made at all (DD-98 item two).

Values are not interpreted here beyond the batch checks (DD-02): raw keeps the text as received, "10759.0" included,
and null stays null -- staging turns them into integers.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .contract import SESSION_INDEX, effective_midnight_skip_minutes
from .icp import IcpClient
from .snapshot import TRUSTED_BATCH, record_hash

SOURCE_TIME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$")
INTEGER_TEXT = re.compile(r"^-?[0-9]+(\.0+)?$")      # "n" or "n.0" (DD-98 item four)
AGENT_ID = re.compile(r"^[0-9]+$")                   # objectIds is an array of integers (p.935)
EXAMPLES = 5                                         # how many examples a quarantine reason lists


def _id_order(agent_id: str):
    # Integer IDs in numeric order, anything else after them
    return (0, int(agent_id), "") if AGENT_ID.match(agent_id) else (1, 0, agent_id)


def as_text(value) -> str | None:
    """A received scalar as text, for the raw columns; JSON numbers keep their JSON form. None stays None."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def is_integer_value(value) -> bool:
    """null, an integer, or a string integer ("n.0" counts). Anything else quarantines the batch (DD-98 item four)."""
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    return isinstance(value, str) and INTEGER_TEXT.match(value) is not None


class Problems:
    """Batch check failures, counted per kind with a few examples, so one bad field does not make a reason of
    two thousand lines."""

    def __init__(self):
        self.count: Counter = Counter()
        self.examples: dict[str, list[str]] = {}

    def add(self, kind: str, example: str) -> None:
        self.count[kind] += 1
        ex = self.examples.setdefault(kind, [])
        if len(ex) < EXAMPLES:
            ex.append(example)

    def reasons(self) -> list[str]:
        return [f"{kind} ({n}x, e.g. {', '.join(self.examples[kind])})" for kind, n in self.count.items()]


def check_records(result, requested: set[str], problems: Problems) -> list[tuple[str | None, list, dict]]:
    """The response's records as (agent id as text, idxs, the record as received). A record that cannot be read is
    reported and kept with what can be read (raw writes what it can, the batch is quarantined)."""
    if not isinstance(result, list) or not all(isinstance(r, dict) for r in result):
        problems.add("result is not a list of {id, idxs} records", type(result).__name__)
        return []
    out = []
    for rec in result:
        aid = as_text(rec.get("id"))
        if aid in (None, ""):
            problems.add("record without id", json.dumps(rec, ensure_ascii=False)[:80])
            aid = None
        idxs = rec.get("idxs")
        if not (isinstance(idxs, list) and all(isinstance(x, dict) for x in idxs)):
            problems.add("idxs is not a list of {id, val}", str(aid))
            idxs = []
        out.append((aid, idxs, rec))
    ids = Counter(a for a, _, _ in out if a is not None)
    for aid, n in ids.items():
        if n > 1:
            problems.add("agent repeated in the response", aid)
        if aid not in requested:
            # objectIds and the response do not match: the records cannot be trusted to be the agents asked for
            problems.add("agent not asked for", aid)
    return out


def coverage(records, requested) -> tuple[list[str], list[str]]:
    """(returned, missing): the agent IDs with a record in the response, and those asked for without one, each
    sorted. Missing agents are registered (raw.icp_agent_coverage), not quarantined -- the request includes agents
    removed during the day, which the platform may leave out (DD-98 review note, Q40). An agent returned but not
    asked for is a batch check failure of check_records, not counted here as returned-for-the-request."""
    asked = set(requested)
    returned = sorted({a for a, _, _ in records if a is not None and a in asked}, key=_id_order)
    missing = sorted(asked - set(returned), key=_id_order)
    return returned, missing


def session_rows(records, problems: Problems) -> list[dict]:
    """One row per sign-in interval (contract if03b). Checks: the interval array readable, inTime present in the
    source's notation, outTime empty or in that notation and not before inTime, duration an integer, no interval
    twice in one response."""
    rows, keys = [], Counter()
    for aid, idxs, rec in records:
        entries = [x for x in idxs if x.get("id") == SESSION_INDEX]
        if len(entries) != 1:
            problems.add(f"{SESSION_INDEX} missing or repeated", str(aid))
            continue
        val = entries[0].get("val")
        if val is None:
            continue                                  # no interval that day: nothing to record
        if not (isinstance(val, list) and all(isinstance(iv, dict) for iv in val)):
            problems.add(f"{SESSION_INDEX} value is not an array of intervals", str(aid))
            continue
        for iv in val:
            intime, outtime, duration = iv.get("inTime"), iv.get("outTime"), iv.get("duration")
            if not (isinstance(intime, str) and SOURCE_TIME.match(intime)):
                problems.add("inTime missing or not YYYY-MM-DD HH:MM:SS", f"{aid}: {intime!r}")
            elif outtime not in (None, ""):
                if not (isinstance(outtime, str) and SOURCE_TIME.match(outtime)):
                    problems.add("outTime not YYYY-MM-DD HH:MM:SS", f"{aid}: {outtime!r}")
                elif outtime < intime:                # same fixed-width notation: sorts like the time
                    problems.add("outTime before inTime", f"{aid}: {intime} / {outtime}")
            if duration != "" and not is_integer_value(duration):
                problems.add("duration not an integer", f"{aid}: {duration!r}")
            keys[(aid, as_text(intime))] += 1
            payload = {**iv, "id": rec.get("id")}     # the interval does not carry the agent: added, as received
            rows.append({"id": aid, "intime": as_text(intime), "outtime": as_text(outtime),
                         "duration": as_text(duration), "payload": payload, "content_hash": record_hash(payload)})
    for (aid, intime), n in keys.items():
        if n > 1:
            problems.add("interval repeated in the response (same id and inTime)", f"{aid}: {intime}")
    return rows


def day_rows(records, index_ids: list[str], date_key: int, problems: Problems,
             unknown: set[str]) -> list[dict]:
    """One row per agent (contract if03c). Checks: every index asked for present once, every value null or an
    integer. An index not asked for is kept in raw and registered as unknown (DD-07), not quarantined."""
    asked = set(index_ids)
    rows = []
    for aid, idxs, rec in records:
        by_id: dict = {}
        for x in idxs:
            by_id.setdefault(x.get("id"), []).append(x)
        for i in index_ids:
            if i not in by_id:
                problems.add("index asked for missing", f"{aid}: {i}")
            elif len(by_id[i]) > 1:
                problems.add("index repeated", f"{aid}: {i}")
            elif not is_integer_value(by_id[i][0].get("val")):
                problems.add("value not an integer or null", f"{aid}: {i}={by_id[i][0].get('val')!r}")
        unknown.update(str(k) for k in by_id if k not in asked)
        # The hash covers what was received for the key: id, the day and the values, independent of their order
        canonical = {"id": rec.get("id"), "date_key": date_key,
                     "idxs": sorted(idxs, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))}
        rows.append({"id": aid, "date_key": date_key, "idxs": idxs, "payload": rec,
                     "content_hash": record_hash(canonical)})
    return rows


class AgentObservation:
    """One run of icp_agent_session or icp_agent_day. Used by runner.Runner after the contract has been read and
    validated."""

    def __init__(self, runner, conn, client: IcpClient, definition: dict, version: int, overrides: dict):
        self.r = runner
        self.conn = conn
        self.client = client
        self.d = definition
        self.contract_version = version
        self.kind = "session" if runner.source_id == "icp_agent_session" else "day"
        ep, win = definition["endpoint"], definition["window"]
        self.site = ep["site_vdn"]
        self.index_ids = ep["index_ids"]
        self.batch_size = ep["objects"]["batch_size"]
        self.day_tz = ZoneInfo(win["day_timezone"])
        self.skip = effective_midnight_skip_minutes(definition, overrides) if self.kind == "day" else None
        self.params = {"midnight_skip_minutes": self.skip} if self.kind == "day" else {}
        self.messages: list[str] = []
        self.seq = 0
        self.missing = 0                              # agents asked for but not returned, over the run's batches

    # ------------------------------------------------------------------------------------------------------------
    def run(self) -> str | None:
        now = self._now()
        day = now.astimezone(self.day_tz).date()
        self.r.wm_from = day.isoformat()
        if self.kind == "day" and self._too_early(now):
            self.messages.append(f"observed at {now.astimezone(self.day_tz):%H:%M} {self.day_tz.key}, within "
                                 f"{self.skip} minutes after midnight: not used (DD-98 item two), nothing asked for")
            return "; ".join(self.messages)
        agents = self._agents(day)
        if not agents:
            self.messages.append("no agent in the site VDN's agents snapshot: nothing asked for")
            return "; ".join(self.messages)
        chunks = [agents[i:i + self.batch_size] for i in range(0, len(agents), self.batch_size)]
        self.r.say(f"  {len(agents)} agent(s) of VDN {self.site['cc_id']}/{self.site['vdn_id']}, "
                   f"{len(chunks)} request(s)")
        for chunk in chunks:
            observed_at = self._now()
            if self.kind == "day" and (observed_at.astimezone(self.day_tz).date() != day or self._too_early(observed_at)):
                # The day turned during the run: the rest would be the new day's first minutes, which are not used
                self.messages.append(f"the day changed during the run; {len(agents) - self.seq * self.batch_size} "
                                     "agent(s) not observed")
                break
            self._one_batch(chunk, observed_at)
        self.conn.commit()
        if self.missing:
            self.messages.append(f"{self.missing} agent(s) asked for but not returned, registered in "
                                 "raw.icp_agent_coverage (DD-98 review note, Q40)")
        return "; ".join(self.messages) or None

    def _now(self) -> datetime:
        # The database clock, the same one raw.ingested_at and meta.ingest_batch.committed_at are written with
        return self.conn.execute("SELECT clock_timestamp()").fetchone()[0]

    def _too_early(self, t: datetime) -> bool:
        local = t.astimezone(self.day_tz)
        midnight = datetime.combine(local.date(), time(0), tzinfo=self.day_tz)
        return local - midnight < timedelta(minutes=self.skip)

    def _agents(self, day) -> list[str]:
        """Agent IDs to ask for today (DD-98 item three). Raises RunFailed when there is no trusted snapshot at all:
        without one nothing says who the agents are (run icp_reference first)."""
        from .runner import RunFailed

        scope = f"agents@{self.site['cc_id']}/{self.site['vdn_id']}"
        day_start = datetime.combine(day, time(0), tzinfo=self.day_tz)
        rows = self.conn.execute(
            f"""WITH trusted AS (
                    SELECT b.run_id, b.batch_seq, b.snapshot_version, b.committed_at
                      FROM meta.ingest_batch b
                     WHERE b.source_id = 'icp_reference' AND b.mode = 'snapshot' AND b.snapshot_scope = %s
                       AND {TRUSTED_BATCH}),
                picked AS (
                    (SELECT run_id, batch_seq FROM trusted WHERE committed_at <= %s
                      ORDER BY snapshot_version DESC, committed_at DESC LIMIT 1)
                    UNION
                    (SELECT run_id, batch_seq FROM trusted ORDER BY snapshot_version DESC, committed_at DESC LIMIT 1)
                    UNION
                    SELECT run_id, batch_seq FROM trusted WHERE committed_at > %s)
                SELECT (SELECT count(*) FROM trusted),
                       array(SELECT DISTINCT r.src_natural_key
                               FROM raw.icp_reference r JOIN picked p USING (run_id, batch_seq)
                              WHERE r.dataset = 'agents' AND r.src_natural_key IS NOT NULL)""",
            (scope, day_start, day_start)).fetchone()
        self.conn.commit()
        if rows[0] == 0:
            raise RunFailed(f"no trusted agents snapshot of VDN {self.site['cc_id']}/{self.site['vdn_id']} in "
                            "raw.icp_reference: run icp_reference first (DD-98 item three)")
        ids = rows[1]
        bad = sorted(i for i in ids if not AGENT_ID.match(i))
        for i in bad:
            self._clarify("agentNo (not an integer: hindex/agent cannot be asked for it)", i, 1)
        if bad:
            self.messages.append(f"{len(bad)} agent ID(s) not integers, not asked for: {bad[:EXAMPLES]}")
        return sorted((i for i in ids if AGENT_ID.match(i)), key=int)

    # ------------------------------------------------------------------------------------------------------------
    def _one_batch(self, chunk: list[str], observed_at: datetime) -> None:
        body = {"ccId": self.site["cc_id"], "vdnId": self.site["vdn_id"],
                "objectIds": [int(a) for a in chunk], "indexIds": self.index_ids}
        result, raw_body = self.client.post_json(self.d["endpoint"]["path"], body)
        problems, unknown = Problems(), set()
        records = check_records(result, set(chunk), problems)
        day = observed_at.astimezone(self.day_tz).date()
        if self.kind == "session":
            rows = session_rows(records, problems)
        else:
            rows = day_rows(records, self.index_ids, int(day.strftime("%Y%m%d")), problems, unknown)
        new = self._new_rows(rows)
        quarantine = problems.reasons()
        returned, missing = coverage(records, chunk)

        self.seq += 1
        self.conn.execute(
            """INSERT INTO meta.ingest_batch (run_id, batch_seq, source_id, mode, window_from, window_to,
                   row_count, rows_written, file_sha256, file_bytes, contract_version, effective_params)
               VALUES (%s, %s, %s, 'observation', %s, %s, %s, %s, %s, %s, %s, %s)""",
            (self.r.run_id, self.seq, self.r.source_id, day.isoformat(), observed_at.isoformat(timespec="seconds"),
             len(rows), len(new), hashlib.sha256(raw_body).digest(), len(raw_body), self.contract_version,
             json.dumps(self.params)))
        cc, vdn = self.site["cc_id"], self.site["vdn_id"]
        with self.conn.cursor() as cur:
            if self.kind == "session":
                cur.executemany(
                    """INSERT INTO raw.icp_agent_session (cc_id, vdn_id, id, intime, outtime, duration, payload,
                           observed_at, run_id, batch_seq, contract_version, content_hash)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [(cc, vdn, x["id"], x["intime"], x["outtime"], x["duration"],
                      json.dumps(x["payload"], ensure_ascii=False), observed_at, self.r.run_id, self.seq,
                      self.contract_version, x["content_hash"]) for x in new])
            else:
                cur.executemany(
                    """INSERT INTO raw.icp_agent_day (cc_id, vdn_id, id, date_key, idxs, payload, observed_at,
                           run_id, batch_seq, contract_version, content_hash)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [(cc, vdn, x["id"], x["date_key"], json.dumps(x["idxs"], ensure_ascii=False),
                      json.dumps(x["payload"], ensure_ascii=False), observed_at, self.r.run_id, self.seq,
                      self.contract_version, x["content_hash"]) for x in new])
        self.conn.execute(
            """INSERT INTO raw.icp_agent_coverage (source_id, cc_id, vdn_id, date_key, observed_at, requested,
                   returned, missing_count, run_id, batch_seq, contract_version)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (self.r.source_id, cc, vdn, int(day.strftime("%Y%m%d")), observed_at, sorted(chunk, key=_id_order),
             returned, len(missing), self.r.run_id, self.seq, self.contract_version))
        if quarantine:
            self.conn.execute(
                "INSERT INTO meta.quarantine_batch (run_id, batch_seq, failure_reason, row_count) VALUES (%s, %s, %s, %s)",
                (self.r.run_id, self.seq, "; ".join(quarantine), len(rows)))
        for name in sorted(unknown):
            # An index in the response that was not asked for is a structure drift (DD-07 · LD-01 2.3)
            self.conn.execute(
                """INSERT INTO meta.mapping_registry (source_id, src_column, mapping_state, note)
                   VALUES (%s, %s, 'unknown', %s) ON CONFLICT DO NOTHING""",
                (self.r.source_id, name, f"first seen in run {self.r.run_id} batch {self.seq}, not asked for"))
        self.conn.commit()
        self.r.committed += 1
        self.r.rows_received += len(rows)
        self.r.wm_to = observed_at.isoformat(timespec="seconds")
        self.missing += len(missing)
        self.r.say(f"  batch {self.seq} {len(chunk)} agent(s), observed {observed_at:%Y-%m-%d %H:%M:%S %z}: "
                   f"received {len(rows)}, written {len(new)}"
                   + (f", {len(missing)} agent(s) not returned (registered, not quarantined): {missing[:EXAMPLES]}"
                      if missing else "")
                   + (f", QUARANTINED: {'; '.join(quarantine)}" if quarantine else ""))

    def _new_rows(self, rows: list[dict]) -> list[dict]:
        """The rows whose content differs from the key's previous observation in raw (contract raw_write): the latest
        one not in a pending or discarded batch. Looked up for this batch's keys only, through the key index."""
        if not rows:
            return []
        if self.kind == "session":
            table, k2, cast = "raw.icp_agent_session", "intime", "text"
            keys = [(x["id"], x["intime"]) for x in rows]
        else:
            table, k2, cast = "raw.icp_agent_day", "date_key", "integer"
            keys = [(x["id"], x["date_key"]) for x in rows]
        latest = self.conn.execute(
            f"""SELECT k.a, k.b, (SELECT r.content_hash FROM {table} r
                                   WHERE r.id = k.a AND r.{k2} = k.b
                                     AND NOT EXISTS (SELECT 1 FROM meta.quarantine_batch q
                                                      WHERE q.run_id = r.run_id AND q.batch_seq = r.batch_seq
                                                        AND q.state IN ('pending', 'discarded'))
                                   ORDER BY r.observed_at DESC, r.ingested_at DESC LIMIT 1)
                  FROM unnest(%s::text[], %s::{cast}[]) AS k(a, b)""",
            ([a for a, _ in keys], [b for _, b in keys])).fetchall()
        previous = {(a, b): bytes(h) for a, b, h in latest if h is not None}
        return [x for x, key in zip(rows, keys) if previous.get(key) != x["content_hash"]]

    def _clarify(self, column: str, value: str, n: int) -> None:
        # One open item per value, as for CDR codes (runner.BatchWriter)
        self.conn.execute(
            """INSERT INTO meta.clarification_queue (source_id, src_column, src_value, impacted_rows)
               SELECT %s, %s, %s, %s WHERE NOT EXISTS (
                   SELECT 1 FROM meta.clarification_queue
                    WHERE source_id = %s AND src_column = %s AND src_value = %s AND status IN ('open', 'asked'))""",
            (self.r.source_id, column, value, n, self.r.source_id, column, value))
        self.conn.commit()


def check_mode(mode: str, source_id: str) -> None:
    from .config import ConfigError

    if mode != "incremental":
        raise ConfigError(f"{source_id} is ingested by observation of today so far (hindex/agent has no date "
                          f"parameter, DD-98); --mode {mode} does not apply (run it without --mode)")
