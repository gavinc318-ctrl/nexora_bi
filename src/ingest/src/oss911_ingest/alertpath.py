"""oss911-alertpath: the two timed jobs of the alert path (DD-100 block three and its review note, items three to five).

  heartbeat-check   every minute (oss911-heartbeat-check.timer). Asks the delivery side when the heartbeat alert
                    (OSS911Heartbeat, always firing) last arrived through Prometheus and Alertmanager. Older than the
                    allowed age: the path is broken, and the people on level page are told directly through the
                    delivery side -- not through Alertmanager, which is what may be broken. Told again every
                    renotify interval while it lasts, and once when it recovers.
  digest            daily at 08:00 report time (oss911-digest.timer). The previous calendar day in the report time
                    zone: every alert that fired (recovered ones too), the WARN tests (changed ones listed, unchanged
                    ones on one line each with the time they last changed), the items that only the digest reports
                    (the oldest clarification item, removed agents not returned, the day-total reconciliation),
                    expected checks not normal at the end of the day, and how much of the day the exporter was
                    scraped -- a quiet digest from a day without data is no good news. Read from Prometheus; sent
                    through the delivery side, never through Alertmanager's repeat notifications.

Both write a state file (<state dir>/<task>.json: last run, whether it succeeded); the exporter serves it, and alert
rules report a job that stopped or fails -- the checker of the alert path is watched through the alert path, and the
alert path by the checker.

The delivery side is the fake gateway on the development machine (test/fake_gateway) and the delivery service in
production; both offer POST /direct and GET /heartbeat. Single node: when the whole machine is down nothing runs to
notice it -- a known limitation of the development machine, removed in production by two nodes checking each other.

Standard library plus PyYAML, like the exporter.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib import error as urlerror
from urllib import parse, request
from zoneinfo import ZoneInfo

import yaml

from . import __version__
from .config import ConfigError, alertpath_settings

TASKS = ("heartbeat_check", "digest")
HIDDEN_LABELS = {"__name__", "alertname", "alertstate", "severity", "job", "instance", "env", "site", "prometheus"}
STEP = 60                       # seconds between the points of the day's range queries
WARN_LOOKBACK_DAYS = 14         # how far back "unchanged since" looks (Prometheus keeps 15 days on development)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value) -> datetime | None:
    if not value:
        return None
    t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------------------------------
# HTTP and state
# ---------------------------------------------------------------------------------------------------------------

class Http:
    timeout = 20

    def get_json(self, url: str) -> dict:
        with request.urlopen(url, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def post_json(self, url: str, doc: dict) -> dict:
        req = request.Request(url, data=json.dumps(doc).encode("utf-8"), method="POST",
                              headers={"Content-Type": "application/json"})
        with request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read() or b"{}")


def read_state(state_dir: str, task: str) -> dict:
    try:
        with open(os.path.join(state_dir, f"{task}.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(state_dir: str, task: str, doc: dict) -> None:
    path = os.path.join(state_dir, f"{task}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------------------------------------------
# Heartbeat check
# ---------------------------------------------------------------------------------------------------------------

def heartbeat_check(s, http: Http | None = None, now: datetime | None = None) -> int:
    http = http or Http()
    now = now or utcnow()
    prev = read_state(s.state_dir, "heartbeat_check")
    state = {"task": "heartbeat_check", "last_run": iso(now), "source": s.heartbeat_source,
             "status": prev.get("status", "ok"), "since": prev.get("since"), "notified_at": prev.get("notified_at")}
    try:
        hb = http.get_json(f"{s.gateway_url}/heartbeat?source={parse.quote(s.heartbeat_source)}")
        last = parse_time(hb.get("last_received"))
        ref = last or parse_time(hb.get("gateway_started_at")) or now
    except (OSError, ValueError, urlerror.URLError) as exc:
        # Nobody can be told through a delivery side that does not answer: the state says so, the exporter serves
        # it, and the rule OSS911AlertPathCheckFailing fires -- reaching people only if the rest of the path works
        state.update(ok=0, error=f"delivery side not reachable: {exc}")
        write_state(s.state_dir, "heartbeat_check", state)
        print(f"oss911-alertpath: heartbeat-check: {state['error']}", file=sys.stderr)
        return 1
    age = (now - ref).total_seconds()
    stale = age > s.heartbeat_max_age
    state.update(heartbeat_age_seconds=round(age), last_received=iso(last) if last else None)
    msg = None
    if stale:
        since = (parse_time(state["since"]) if state["status"] == "stale" else None) or ref
        notified = parse_time(state["notified_at"])
        if state["status"] != "stale" or notified is None or (now - notified).total_seconds() >= s.renotify:
            minutes = round(age / 60)
            msg = {"kind": "path_failure", "level": "page", "source": s.node,
                   "subject": f"[OSS911 PAGE] Alert path broken: no heartbeat from {s.heartbeat_source}"
                              f" for {minutes} min",
                   "body": (f"The heartbeat alert of {s.heartbeat_source} last reached the delivery side at "
                            f"{iso(last) if last else 'never (since the delivery side started)'}; allowed "
                            f"{s.heartbeat_max_age} s. Alerts through Prometheus and Alertmanager are not being "
                            f"delivered: check prometheus.service, alertmanager.service and the delivery side "
                            f"(runbook 21, section three). Sent by the heartbeat check on {s.node}, not through "
                            f"Alertmanager; repeated every {round(s.renotify / 3600, 1)} h while it lasts.")}
        state.update(status="stale", since=iso(since))
    elif state["status"] == "stale":
        msg = {"kind": "path_recovered", "level": "page", "source": s.node,
               "subject": f"[OSS911 PAGE] Alert path recovered: heartbeat from {s.heartbeat_source} arrives again",
               "body": (f"The heartbeat arrived at {iso(ref)} after a gap since {state['since']}. Alerts raised "
                        f"while the path was broken are delivered by Alertmanager now if they still fire; the daily "
                        f"digest lists all of them.")}
        state.update(status="ok", since=None, notified_at=None)
    if msg is not None:
        try:
            http.post_json(f"{s.gateway_url}/direct", msg)
            if msg["kind"] == "path_failure":
                state["notified_at"] = iso(now)
        except (OSError, ValueError, urlerror.URLError) as exc:
            state.update(ok=0, error=f"could not notify through the delivery side: {exc}")
            if msg["kind"] == "path_recovered":   # tell it next minute
                state.update(status="stale", since=prev.get("since"), notified_at=prev.get("notified_at"))
            write_state(s.state_dir, "heartbeat_check", state)
            print(f"oss911-alertpath: heartbeat-check: {state['error']}", file=sys.stderr)
            return 1
        print(f"oss911-alertpath: heartbeat-check: sent {msg['kind']}", flush=True)
    state.update(ok=1, error=None)
    write_state(s.state_dir, "heartbeat_check", state)
    return 0


# ---------------------------------------------------------------------------------------------------------------
# Daily digest
# ---------------------------------------------------------------------------------------------------------------

class Prometheus:
    def __init__(self, url: str, http: Http) -> None:
        self.url = url.rstrip("/")
        self.http = http

    def _call(self, path: str, params: dict) -> list:
        doc = self.http.get_json(f"{self.url}{path}?{parse.urlencode(params)}")
        if doc.get("status") != "success":
            raise ValueError(f"Prometheus: {doc.get('errorType')}: {doc.get('error')}")
        return doc["data"]["result"]

    def query(self, expr: str, at: datetime) -> list[tuple[dict, float]]:
        return [(r["metric"], float(r["value"][1])) for r in self._call("/api/v1/query",
                                                                         {"query": expr, "time": at.timestamp()})]

    def query_range(self, expr: str, start: datetime, end: datetime, step: int) -> list[tuple[dict, list]]:
        res = self._call("/api/v1/query_range", {"query": expr, "start": start.timestamp(), "end": end.timestamp(),
                                                 "step": step})
        return [(r["metric"], [(float(t), float(v)) for t, v in r["values"]]) for r in res]


def where(labels: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(labels.items()) if k not in HIDDEN_LABELS)


def episodes(points: list[tuple[float, float]], step: int) -> list[tuple[float, float]]:
    """Consecutive firing points -> [(first, last)]; a gap of more than two steps starts a new episode"""
    out: list[list[float]] = []
    for t, _ in points:
        if out and t - out[-1][1] <= 2 * step:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(a, b) for a, b in out]


def fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


class Digest:
    """Builds the text of one day's digest; each section records its own failure and the others still run"""

    def __init__(self, prom: Prometheus, tz: ZoneInfo, day: date, now: datetime) -> None:
        self.prom, self.tz, self.day, self.now = prom, tz, day, now
        self.start = datetime(day.year, day.month, day.day, tzinfo=tz)
        self.end = min(self.start + timedelta(days=1), now)
        self.errors: list[str] = []
        self.counts = {"page": 0, "notify": 0, "still": 0}

    def served(self, query: str) -> bool:
        """Whether the exporter's query behind a section ran without error at the end of the day: without it, an
        empty answer is no data, not "nothing to report" """
        return bool(self.prom.query(f'oss911_monitor_query_errors{{query="{query}"}} == 0', self.end))

    def no_data(self, query: str) -> str:
        return (f"  NO DATA: the exporter's query {query} did not run without error at the end of the day"
                f" -- nothing here can be read as good news")

    def hm(self, ts: float) -> str:
        t = datetime.fromtimestamp(ts, self.tz)
        return t.strftime("%H:%M") if t.date() == self.day else t.strftime("%m-%d %H:%M")

    def section(self, title: str, fn) -> list[str]:
        try:
            lines = fn()
        except Exception as exc:   # noqa: BLE001 -- one section's failure must not lose the digest
            self.errors.append(f"{title}: {type(exc).__name__}: {exc}")
            lines = [f"  NOT AVAILABLE: {type(exc).__name__}: {exc}"]
        return [title, *lines, ""]

    def alerts(self) -> list[str]:
        series = self.prom.query_range('ALERTS{alertstate="firing",severity!="heartbeat"}', self.start, self.end, STEP)
        rows = []
        for labels, points in series:
            eps = episodes(points, STEP)
            sev = labels.get("severity", "?")
            still = points[-1][0] >= self.end.timestamp() - 2 * STEP
            if sev in ("page", "notify"):
                self.counts[sev] += 1
            self.counts["still"] += still
            minutes = sum((b - a) / 60 + 1 for a, b in eps)
            spans = ", ".join(f"{self.hm(a)}-{self.hm(b)}" for a, b in eps[:6]) + (" ..." if len(eps) > 6 else "")
            rows.append(((0 if sev == "page" else 1), points[0][0],
                         f"  {sev.upper():6} {labels.get('alertname', '?')}  {where(labels)}\n"
                         f"         {spans} ({round(minutes)} min in {len(eps)} episode(s)); "
                         f"{'STILL FIRING at the end of the day' if still else 'recovered'}"))
        return [r[2] for r in sorted(rows)] or ["  No alert fired."]

    def warns(self) -> list[str]:
        if not self.served("checks"):
            return [self.no_data("checks")]
        expr = 'oss911_test_failures{severity="warn"}'
        key = lambda lab: (lab.get("dag_id", ""), lab.get("node", ""))   # noqa: E731
        at_end = {key(l): v for l, v in self.prom.query(expr, self.end)}
        at_start = {key(l): v for l, v in self.prom.query(expr, self.start)}
        lookback = self.end - timedelta(days=WARN_LOOKBACK_DAYS)
        hist = {key(l): pts for l, pts in self.prom.query_range(expr, lookback, self.end, 3600)}
        changed, unchanged = [], []
        for k in sorted(set(at_end) | set(at_start)):
            v, v0 = at_end.get(k), at_start.get(k)
            name = f"{k[0]} / {k[1]}"
            if v != v0:
                if (v or 0) > 0 or (v0 or 0) > 0:
                    changed.append(f"  {name}: {fmt_num(v0) if v0 is not None else 'none'} -> "
                                   f"{fmt_num(v) if v is not None else 'none'} row(s)")
            elif v and v > 0:
                pts = hist.get(k, [])
                since = None
                for t, x in reversed(pts):
                    if x != v:
                        break
                    since = t
                if pts and since == pts[0][0] and pts[0][0] <= lookback.timestamp() + 3600:
                    when = f"for {WARN_LOOKBACK_DAYS}+ days"
                elif since is not None:
                    when = f"since {datetime.fromtimestamp(since, self.tz):%Y-%m-%d %H:00}"
                else:
                    when = "all day"
                unchanged.append(f"  {name}: {fmt_num(v)} row(s), unchanged {when}")
        out = ["  Changed during the day:", *(changed or ["  (none)"])]
        out += ["  Unchanged (reported once a day only):", *(unchanged or ["  (none)"])]
        return out

    def daily_only(self) -> list[str]:
        out = []
        if not self.served("clarification"):
            out.append(self.no_data("clarification"))
        th = self.prom.query('oss911_threshold{signal="clarification_oldest_days"}', self.end)
        limit = th[0][1] if th else None
        opened = {l.get("source_id"): v for l, v in self.prom.query("oss911_clarification_open", self.end)}
        for lab, age in sorted(self.prom.query("oss911_clarification_oldest_age_seconds", self.end),
                               key=lambda x: x[0].get("source_id", "")):
            days = age / 86400
            flag = (f"  OVER {fmt_num(limit)} DAYS" if limit is not None and days > limit else "")
            out.append(f"  Clarification {lab.get('source_id')}: {fmt_num(opened.get(lab.get('source_id'), 0))} "
                       f"open, the oldest {days:.1f} days{flag}")
        if not opened and self.served("clarification"):
            out.append("  Clarification: no open item")
        if limit is None:
            out.append("  Clarification: threshold clarification_oldest_days not exported")
        removed = self.prom.query('oss911_agents_not_returned{roster="removed"} > 0', self.end)
        out += [f"  Removed agents not returned by {l.get('source_id')}: {fmt_num(v)}" for l, v in removed] or \
               (["  Removed agents not returned: none"] if self.served("agents") else [self.no_data("agents")])
        rec = self.prom.query('oss911_test_status{node="agent_day_reconciles_with_legs"} == 1', self.end)
        fails = {l.get("dag_id"): v for l, v in
                 self.prom.query('oss911_test_failures{node="agent_day_reconciles_with_legs"}', self.end)}
        out += [f"  Day-total reconciliation ({l.get('dag_id')}): {l.get('status')}"
                + (f", {fmt_num(fails[l.get('dag_id')])} agent-day(s) differ" if fails.get(l.get("dag_id")) else "")
                for l, _ in rec] or ["  Day-total reconciliation: no result"]
        return out

    def checks(self) -> list[str]:
        if not self.served("checks"):
            return [self.no_data("checks")]
        res = self.prom.query('oss911_expected_check_state{state!~"normal|disabled"} == 1', self.end)
        return [f"  {l.get('check')}: {l.get('state')}" for l, _ in sorted(res, key=lambda x: x[0].get("check", ""))] \
            or ["  All normal."]

    def coverage(self) -> list[str]:
        series = self.prom.query_range('up{job="oss911_monitor"}', self.start, self.end, STEP)
        expected = int((self.end - self.start).total_seconds() // STEP) + 1
        up = sum(1 for _, pts in series for _, v in pts if v == 1)
        pct = 100.0 * up / expected if expected else 0.0
        line = f"  Exporter scraped and up for {pct:.1f}% of the day ({up} of {expected} minutes)"
        if pct < 99:
            line += ": signals are missing for the rest -- this digest cannot vouch for those hours"
        return [line]

    def build(self) -> dict:
        tzname = self.tz.key
        body = [f"OSS911 BI daily digest for {self.day} (00:00-24:00 {tzname}), "
                f"generated {self.now.astimezone(self.tz):%Y-%m-%d %H:%M %Z}", ""]
        body += self.section("1. Alerts during the day (the heartbeat left out)", self.alerts)
        body += self.section("2. WARN tests, latest result at the end of the day", self.warns)
        body += self.section("3. Reported in the digest only", self.daily_only)
        body += self.section("4. Expected checks not normal at the end of the day", self.checks)
        body += self.section("5. Coverage", self.coverage)
        if self.errors:
            body += ["INCOMPLETE: some sections could not be read from Prometheus. Silence here is not good news."]
        subject = (f"[OSS911 DIGEST] {self.day}: {self.counts['page']} page, {self.counts['notify']} notify alert(s)"
                   f", {self.counts['still']} still firing" + (" -- INCOMPLETE" if self.errors else ""))
        return {"kind": "digest", "level": "digest", "subject": subject, "body": "\n".join(body)}


def report_timezone(path: str) -> ZoneInfo:
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    return ZoneInfo(doc["report_timezone"])


def digest(s, day: date | None = None, force: bool = False, show: bool = False, http: Http | None = None,
           now: datetime | None = None) -> int:
    http = http or Http()
    now = now or utcnow()
    state = read_state(s.state_dir, "digest")
    try:
        tz = report_timezone(s.monitor_config)
    except (OSError, KeyError, ValueError, yaml.YAMLError) as exc:
        if not show:
            write_state(s.state_dir, "digest", {**state, "task": "digest", "last_run": iso(now), "ok": 0,
                                                "error": f"report time zone: {exc}"})
        print(f"oss911-alertpath: digest: cannot read report_timezone from {s.monitor_config}: {exc}",
              file=sys.stderr)
        return 1
    day = day or (now.astimezone(tz).date() - timedelta(days=1))
    if not force and not show and state.get("last_day") == day.isoformat() and state.get("ok"):
        print(f"oss911-alertpath: digest for {day} already sent (--force sends it again)", flush=True)
        return 0
    msg = Digest(Prometheus(s.prometheus_url, http), tz, day, now).build()
    msg["source"] = s.node
    msg["day"] = day.isoformat()
    if show:
        print(msg["subject"])
        print()
        print(msg["body"])
        return 0
    new = {"task": "digest", "last_run": iso(now), "day_tried": day.isoformat(), "last_day": state.get("last_day")}
    try:
        http.post_json(f"{s.gateway_url}/direct", msg)
    except (OSError, ValueError, urlerror.URLError) as exc:
        write_state(s.state_dir, "digest", {**new, "ok": 0, "error": f"delivery side: {exc}"})
        print(f"oss911-alertpath: digest: could not send through the delivery side: {exc}", file=sys.stderr)
        return 1
    write_state(s.state_dir, "digest", {**new, "ok": 1, "error": None, "last_day": day.isoformat(),
                                        "subject": msg["subject"]})
    print(f"oss911-alertpath: digest sent: {msg['subject']}", flush=True)
    return 0


# ---------------------------------------------------------------------------------------------------------------
# State for the exporter
# ---------------------------------------------------------------------------------------------------------------

def states(state_dir: str) -> dict[str, dict]:
    """The state of each task that has a state file (the exporter serves them)"""
    out = {}
    for task in TASKS:
        st = read_state(state_dir, task)
        t = parse_time(st.get("last_run")) if st else None
        if t is not None:
            out[task] = {"last_run": t.timestamp(), "ok": 1 if st.get("ok") else 0}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="oss911-alertpath", description="OSS911 alert path: heartbeat check, digest")
    ap.add_argument("--version", action="version", version=f"oss911-alertpath {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("heartbeat-check", help="check that the heartbeat alert keeps arriving at the delivery side")
    d = sub.add_parser("digest", help="send the daily digest of the previous day (report time zone)")
    d.add_argument("--day", type=date.fromisoformat, help="the day to report, YYYY-MM-DD (default: yesterday)")
    d.add_argument("--force", action="store_true", help="send again a day already sent")
    d.add_argument("--print", dest="show", action="store_true", help="print the digest instead of sending it")
    args = ap.parse_args(argv)
    try:
        s = alertpath_settings()
    except ConfigError as exc:
        print(f"oss911-alertpath: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "heartbeat-check":
        return heartbeat_check(s)
    return digest(s, day=args.day, force=args.force, show=args.show)


if __name__ == "__main__":
    sys.exit(main())
