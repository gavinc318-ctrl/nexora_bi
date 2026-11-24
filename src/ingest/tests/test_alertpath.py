"""The alert path's timed jobs (DD-100 block three): heartbeat check and daily digest."""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib import parse
from zoneinfo import ZoneInfo

from oss911_ingest import alertpath
from oss911_ingest.alertpath import Digest, Prometheus, episodes, heartbeat_check, read_state, states

T0 = datetime(2026, 11, 24, 5, 0, tzinfo=timezone.utc)
RIYADH = ZoneInfo("Asia/Riyadh")


class FakeHttp:
    """Answers GET /heartbeat from .last, records POSTs; .down makes every call fail"""

    def __init__(self, last=None):
        self.last = last
        self.posts = []
        self.down = False
        self.post_down = False

    def get_json(self, url):
        if self.down:
            raise OSError("connection refused")
        return {"source": "dev01", "last_received": self.last, "gateway_started_at": "2026-11-24T00:00:00+00:00"}

    def post_json(self, url, doc):
        if self.down or self.post_down:
            raise OSError("connection refused")
        self.posts.append((url, doc))
        return {"deliveries": 1}


class Settings:
    def __init__(self, d):
        self.gateway_url = "http://gw"
        self.prometheus_url = "http://prom"
        self.state_dir = d
        self.monitor_config = str(Path(d) / "monitoring.yml")
        self.node = "dev01"
        self.heartbeat_source = "dev01"
        self.heartbeat_max_age = 300
        self.renotify = 4 * 3600
        Path(self.monitor_config).write_text("report_timezone: Asia/Riyadh\n", encoding="utf-8")


class HeartbeatTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.s = Settings(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def at(self, minutes):
        return T0 + timedelta(minutes=minutes)

    def test_fresh_heartbeat_is_quiet(self):
        http = FakeHttp(last=(T0 - timedelta(seconds=60)).isoformat())
        self.assertEqual(heartbeat_check(self.s, http, T0), 0)
        self.assertEqual(http.posts, [])
        st = read_state(self.s.state_dir, "heartbeat_check")
        self.assertEqual((st["status"], st["ok"], st["heartbeat_age_seconds"]), ("ok", 1, 60))

    def test_broken_path_pages_once_then_again_after_renotify_then_recovers(self):
        http = FakeHttp(last=T0.isoformat())
        heartbeat_check(self.s, http, self.at(4))
        self.assertEqual(http.posts, [])
        heartbeat_check(self.s, http, self.at(6))                 # 6 min > 300 s: broken
        self.assertEqual([p[1]["kind"] for p in http.posts], ["path_failure"])
        self.assertEqual(http.posts[0][0], "http://gw/direct")
        self.assertEqual(http.posts[0][1]["level"], "page")
        heartbeat_check(self.s, http, self.at(7))                 # still broken: not again yet
        heartbeat_check(self.s, http, self.at(60))
        self.assertEqual(len(http.posts), 1)
        heartbeat_check(self.s, http, self.at(6 + 240))           # renotify interval
        self.assertEqual([p[1]["kind"] for p in http.posts], ["path_failure", "path_failure"])
        self.assertEqual(read_state(self.s.state_dir, "heartbeat_check")["since"], T0.isoformat())
        http.last = self.at(250).isoformat()
        heartbeat_check(self.s, http, self.at(251))
        self.assertEqual(http.posts[-1][1]["kind"], "path_recovered")
        heartbeat_check(self.s, http, self.at(252))
        self.assertEqual(len(http.posts), 3)

    def test_never_received_counts_from_the_gateway_start(self):
        http = FakeHttp(last=None)
        heartbeat_check(self.s, http, datetime(2026, 11, 24, 0, 3, tzinfo=timezone.utc))
        self.assertEqual(http.posts, [])
        heartbeat_check(self.s, http, datetime(2026, 11, 24, 0, 6, tzinfo=timezone.utc))
        self.assertIn("never", http.posts[0][1]["body"])

    def test_gateway_down_fails_and_says_so(self):
        http = FakeHttp()
        http.down = True
        self.assertEqual(heartbeat_check(self.s, http, T0), 1)
        st = read_state(self.s.state_dir, "heartbeat_check")
        self.assertEqual(st["ok"], 0)
        self.assertIn("not reachable", st["error"])
        self.assertEqual(states(self.s.state_dir)["heartbeat_check"]["ok"], 0)

    def test_failed_notification_is_retried_next_run(self):
        http = FakeHttp(last=T0.isoformat())
        http.post_down = True
        self.assertEqual(heartbeat_check(self.s, http, self.at(6)), 1)
        http.post_down = False
        self.assertEqual(heartbeat_check(self.s, http, self.at(7)), 0)
        self.assertEqual([p[1]["kind"] for p in http.posts], ["path_failure"])


class FakePrometheus:
    """Answers the digest's queries from a table: expr -> result"""

    def __init__(self, instant=None, ranges=None, fail=(), served=("checks", "clarification", "agents")):
        self.instant = dict(instant or {})
        for q in served:
            self.instant.setdefault(f'oss911_monitor_query_errors{{query="{q}"}} == 0', [({"query": q}, 0.0)])
        self.ranges = ranges or {}
        self.fail = fail

    def query(self, expr, at):
        if expr in self.fail:
            raise ValueError("Prometheus: bad_data")
        r = self.instant.get(expr, [])
        return r(at) if callable(r) else r

    def query_range(self, expr, start, end, step):
        if expr in self.fail:
            raise ValueError("Prometheus: bad_data")
        return self.ranges.get(expr, [])


DAY = date(2026, 11, 23)
START = datetime(2026, 11, 23, tzinfo=RIYADH).timestamp()
END = START + 86400


def minutes(a, b):
    return [(START + 60 * m, 1.0) for m in range(a, b + 1)]


class DigestTest(unittest.TestCase):
    def build(self, prom):
        return Digest(prom, RIYADH, DAY, datetime(2026, 11, 24, 5, 0, tzinfo=timezone.utc)).build()

    def test_episodes(self):
        self.assertEqual(episodes([(0, 1), (60, 1), (120, 1), (600, 1)], 60), [(0, 120), (600, 600)])

    def test_alerts_recovered_and_still_firing(self):
        alerts = [
            ({"alertname": "OSS911SourceFailing", "severity": "page", "source_id": "icp_cdr_voice",
              "alertstate": "firing", "job": "oss911_monitor", "instance": "x", "prometheus": "dev01"},
             minutes(295, 329)),
            ({"alertname": "OSS911QuarantineWaiting", "severity": "notify", "source_id": "icp_agent_day",
              "alertstate": "firing"}, minutes(1300, 1440)),
        ]
        msg = self.build(FakePrometheus(ranges={'ALERTS{alertstate="firing",severity!="heartbeat"}': alerts,
                                                'up{job="oss911_monitor"}': [({}, minutes(0, 1440))]}))
        self.assertEqual(msg["subject"], "[OSS911 DIGEST] 2026-11-23: 1 page, 1 notify alert(s), 1 still firing")
        body = msg["body"]
        self.assertIn("PAGE   OSS911SourceFailing  source_id=icp_cdr_voice", body)
        self.assertIn("04:55-05:29 (35 min in 1 episode(s)); recovered", body)
        self.assertIn("STILL FIRING", body)
        self.assertNotIn("dev01", body)                      # no host or instance labels
        self.assertIn("100.0% of the day", body)
        self.assertLess(body.index("OSS911SourceFailing"), body.index("OSS911QuarantineWaiting"))

    def test_quiet_day_without_data_is_not_good_news(self):
        msg = self.build(FakePrometheus(ranges={'up{job="oss911_monitor"}': [({}, minutes(0, 600))]}))
        self.assertIn("No alert fired.", msg["body"])
        self.assertIn("41.7% of the day (601 of 1441 minutes)", msg["body"])
        self.assertIn("cannot vouch", msg["body"])

    def test_warn_changed_and_unchanged(self):
        expr = 'oss911_test_failures{severity="warn"}'
        a = {"dag_id": "build_icp_cdr_voice", "node": "fact_call_leg_without_dimension", "severity": "warn"}
        b = {"dag_id": "build_icp_cdr_voice", "node": "fact_call_leg_without_first_leg", "severity": "warn"}
        c = {"dag_id": "maintenance_checks", "node": "agent_day_reconciles_with_legs", "severity": "warn"}
        lookback_start = END - 14 * 86400
        hist = [(a, [(lookback_start + 3600 * h, 1.0) for h in range(0, 337)]),
                (b, [(lookback_start + 3600 * h, 1.0 if h < 300 else 3.0) for h in range(0, 337)])]

        def at(t):
            return [(a, 1.0), (b, 3.0), (c, 0.0)] if t.timestamp() >= END - 1 else [(a, 1.0), (b, 1.0), (c, 2.0)]
        msg = self.build(FakePrometheus(instant={expr: at}, ranges={expr: hist}))
        body = msg["body"]
        self.assertIn("build_icp_cdr_voice / fact_call_leg_without_first_leg: 1 -> 3 row(s)", body)
        self.assertIn("maintenance_checks / agent_day_reconciles_with_legs: 2 -> 0 row(s)", body)
        self.assertIn("fact_call_leg_without_dimension: 1 row(s), unchanged for 14+ days", body)

    def test_daily_only_items(self):
        inst = {
            'oss911_threshold{signal="clarification_oldest_days"}': [({}, 7.0)],
            "oss911_clarification_open": [({"source_id": "icp_cdr_voice"}, 2.0)],
            "oss911_clarification_oldest_age_seconds": [({"source_id": "icp_cdr_voice"}, 9.5 * 86400)],
            'oss911_agents_not_returned{roster="removed"} > 0': [({"source_id": "icp_agent_day"}, 3.0)],
            'oss911_test_status{node="agent_day_reconciles_with_legs"} == 1':
                [({"dag_id": "maintenance_checks", "status": "warn"}, 1.0)],
            'oss911_test_failures{node="agent_day_reconciles_with_legs"}': [({"dag_id": "maintenance_checks"}, 2.0)],
            'oss911_expected_check_state{state!~"normal|disabled"} == 1':
                [({"check": "maintenance_checks", "state": "stale"}, 1.0)],
        }
        body = self.build(FakePrometheus(instant=inst))["body"]
        self.assertIn("Clarification icp_cdr_voice: 2 open, the oldest 9.5 days  OVER 7 DAYS", body)
        self.assertIn("Removed agents not returned by icp_agent_day: 3", body)
        self.assertIn("Day-total reconciliation (maintenance_checks): warn, 2 agent-day(s) differ", body)
        self.assertIn("  maintenance_checks: stale", body)

    def test_no_exporter_data_is_no_good_news(self):
        body = self.build(FakePrometheus(served=()))["body"]
        self.assertNotIn("All normal", body)
        self.assertNotIn("no open item", body)
        self.assertNotIn("Removed agents not returned: none", body)
        self.assertEqual(body.count("NO DATA"), 4)

    def test_a_failed_section_marks_the_digest_incomplete(self):
        msg = self.build(FakePrometheus(fail=('ALERTS{alertstate="firing",severity!="heartbeat"}',)))
        self.assertTrue(msg["subject"].endswith("-- INCOMPLETE"))
        self.assertIn("NOT AVAILABLE: ValueError", msg["body"])
        self.assertIn("Expected checks not normal", msg["body"])      # the other sections still there


class DigestRunTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.s = Settings(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_sent_once_per_day_and_state_written(self):
        http = FakeHttp()
        prom_calls = []

        def fake_get(url):
            prom_calls.append(url)
            return {"status": "success", "data": {"result": []}}
        http.get_json = fake_get
        now = datetime(2026, 11, 24, 5, 0, tzinfo=timezone.utc)          # 08:00 Riyadh
        self.assertEqual(alertpath.digest(self.s, http=http, now=now), 0)
        self.assertEqual(len(http.posts), 1)
        sent = http.posts[0][1]
        self.assertEqual((sent["kind"], sent["level"], sent["day"]), ("digest", "digest", "2026-11-23"))
        q = parse.parse_qs(parse.urlparse(prom_calls[0]).query)
        self.assertEqual(float(q["start"][0]), datetime(2026, 11, 23, tzinfo=RIYADH).timestamp())
        self.assertEqual(alertpath.digest(self.s, http=http, now=now), 0)   # already sent
        self.assertEqual(len(http.posts), 1)
        self.assertEqual(alertpath.digest(self.s, http=http, now=now, force=True), 0)
        self.assertEqual(len(http.posts), 2)
        st = json.loads((Path(self.dir.name) / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual((st["ok"], st["last_day"]), (1, "2026-11-23"))

    def test_gateway_down_is_a_failed_run(self):
        http = FakeHttp()
        http.get_json = lambda url: {"status": "success", "data": {"result": []}}
        http.post_down = True
        self.assertEqual(alertpath.digest(self.s, http=http, now=T0), 1)
        self.assertEqual(states(self.s.state_dir)["digest"]["ok"], 0)
        self.assertIsNone(read_state(self.s.state_dir, "digest")["last_day"])

    def test_prometheus_client_raises_on_error_status(self):
        http = FakeHttp()
        http.get_json = lambda url: {"status": "error", "errorType": "bad_data", "error": "parse error"}
        with self.assertRaises(ValueError):
            Prometheus("http://p", http).query("x", T0)


if __name__ == "__main__":
    unittest.main()
