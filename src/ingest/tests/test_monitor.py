"""The monitoring exporter (DD-100): text format, configuration, expected-check states, query isolation."""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from oss911_ingest import monitor
from oss911_ingest.monitor import (Collector, Metrics, MonitorConfigError, check_state, load_config, worst_status)

REPO = Path(__file__).resolve().parents[3]
RELEASE_CONFIG = REPO / "contracts" / "monitoring.yml"
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def item(**kw):
    base = {"id": "x", "kind": "check", "dag_id": "d", "node": "n", "period_minutes": 10, "allowed_delay_minutes": 20}
    base.update(kw)
    return base


class TextFormatTest(unittest.TestCase):
    def test_labels_escaped_and_values(self):
        m = Metrics()
        m.add("a", "help", {"z": 'q"\\\n', "b": "1"}, 3)
        m.add("a", "help", {}, float("inf"))
        m.add("b", "help", {}, None)
        m.add("b", "help", {}, True)
        text = m.render()
        self.assertIn('a{b="1",z="q\\"\\\\\\n"} 3', text)
        self.assertIn("a +Inf", text)
        self.assertIn("b NaN", text)
        self.assertIn("b 1", text)
        self.assertEqual(text.count("# TYPE a gauge"), 1)

    def test_floats_and_large_numbers(self):
        m = Metrics()
        m.add("a", "h", {}, 0.25)
        m.add("a", "h", {}, 1795451649.0)
        self.assertIn("a 0.25", m.render())
        self.assertIn("a 1795451649", m.render())


class ConfigTest(unittest.TestCase):
    def test_release_config_loads(self):
        cfg = load_config(RELEASE_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(cfg["sources"]["icp_cdr_voice"]["models"][-1], "fact_call_leg")
        self.assertEqual(cfg["sources"]["icp_reference"]["max_consecutive_failures"], 2)
        ids = [c["id"] for c in cfg["expected_checks"]]
        self.assertEqual(len(ids), len(set(ids)))

    def _broken(self, change):
        doc = yaml.safe_load(RELEASE_CONFIG.read_text(encoding="utf-8"))
        change(doc)
        with self.assertRaises(MonitorConfigError):
            load_config(yaml.safe_dump(doc))

    def test_rejects(self):
        self._broken(lambda d: d.update(version=2))
        self._broken(lambda d: d["thresholds"].pop("closing_lag_days"))
        self._broken(lambda d: d["thresholds"].update(closing_lag_days=0))
        self._broken(lambda d: d["sources"].pop("default"))
        self._broken(lambda d: d["sources"]["icp_reference"].update(max_consecutive_failures="2"))
        self._broken(lambda d: d["expected_checks"].append(dict(d["expected_checks"][0])))     # id twice
        self._broken(lambda d: d["expected_checks"][0].update(kind="task"))
        self._broken(lambda d: d["expected_checks"][-1].pop("node"))
        self._broken(lambda d: d["expected_checks"][0].update(deployed_at="yesterday"))


class CheckStateTest(unittest.TestCase):
    def test_states(self):
        fresh = NOW - timedelta(minutes=5)
        self.assertEqual(check_state(item(), {"status": "pass", "checked_at": fresh}, NOW)[0], "normal")
        self.assertEqual(check_state(item(), {"status": "warn", "checked_at": fresh}, NOW)[0], "warned")
        self.assertEqual(check_state(item(), {"status": "fail", "checked_at": fresh}, NOW)[0], "failed")
        self.assertEqual(check_state(item(), {"status": "error", "checked_at": fresh}, NOW)[0], "error")
        self.assertEqual(check_state(item(), {"status": "skipped", "checked_at": fresh}, NOW)[0], "skipped")
        self.assertEqual(check_state(item(), {"status": "odd", "checked_at": fresh}, NOW)[0], "error")
        self.assertEqual(check_state(item(enabled=False), None, NOW)[0], "disabled")

    def test_an_old_pass_is_stale(self):
        state, age, allowed = check_state(item(), {"status": "pass", "checked_at": NOW - timedelta(minutes=31)}, NOW)
        self.assertEqual((state, allowed), ("stale", 1800.0))
        self.assertAlmostEqual(age, 1860.0)

    def test_grace_then_never_run(self):
        self.assertEqual(check_state(item(deployed_at=(NOW - timedelta(minutes=29)).isoformat()), None, NOW)[0],
                         "grace")
        self.assertEqual(check_state(item(deployed_at=(NOW - timedelta(minutes=31)).isoformat()), None, NOW)[0],
                         "never_run")
        self.assertEqual(check_state(item(), None, NOW)[0], "never_run")

    def test_worst_status(self):
        self.assertEqual(worst_status(["pass", "warn", "success"]), "warn")
        self.assertEqual(worst_status(["pass", "fail", "error"]), "error")
        self.assertEqual(worst_status(["success", "skipped"]), "skipped")


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self):
        self.rollbacks = 0

    def cursor(self):
        return _Cursor()

    def commit(self):
        pass

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = Path(self.dir.name)
        (d / "monitoring.yml").write_text(RELEASE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        (d / "dags.json").write_text('{"sources": [{"source_id": "icp_cdr_voice", "enabled": true}]}',
                                     encoding="utf-8")

        class S:
            config_path = str(d / "monitoring.yml")
            manifest_path = str(d / "dags.json")
        self.settings = S
        self.saved = monitor.QUERIES

    def tearDown(self):
        monitor.QUERIES = self.saved
        self.dir.cleanup()

    def test_a_failed_query_serves_nothing_of_its_own(self):
        def good(cur, ctx):
            m = Metrics()
            m.add("oss911_good", "h", {}, 1)
            return m

        def bad(cur, ctx):
            raise RuntimeError("boom")
        monitor.QUERIES = [("good", good), ("bad", bad), ("after", good)]
        conn = _Conn()
        with redirect_stderr(io.StringIO()):          # the exporter logs the failure; not test output
            text = Collector(self.settings, connect=lambda: conn).refresh()
        self.assertIn('oss911_monitor_query_errors{query="bad"} 1', text)
        self.assertIn('oss911_monitor_query_errors{query="good"} 0', text)
        self.assertIn('oss911_monitor_query_errors{query="after"} 0', text)
        self.assertEqual(text.count("oss911_good 1"), 2)
        self.assertEqual(conn.rollbacks, 1)
        self.assertIn("oss911_monitor_config_ok 1", text)

    def test_no_database_flags_every_query(self):
        def fail():
            raise OSError("refused")
        with redirect_stderr(io.StringIO()):
            text = Collector(self.settings, connect=fail).refresh()
        for name, _ in monitor.QUERIES:
            self.assertIn(f'oss911_monitor_query_errors{{query="{name}"}} 1', text)
        self.assertIn('oss911_threshold{signal="closing_lag_days"} 3', text)

    def test_broken_config_keeps_the_previous_one(self):
        monitor.QUERIES = []
        c = Collector(self.settings, connect=_Conn)
        c.refresh()
        Path(self.settings.config_path).write_text("version: 7\n", encoding="utf-8")
        os.utime(self.settings.config_path, (1, 1))
        text = c.refresh()
        self.assertIn("oss911_monitor_config_ok 0", text)
        self.assertIn('oss911_threshold{signal="closing_lag_days"} 3', text)

    def test_cache(self):
        calls = []
        monitor.QUERIES = [("q", lambda cur, ctx: calls.append(1) or Metrics())]
        t = [1000.0]
        c = Collector(self.settings, connect=_Conn, clock=lambda: t[0])
        c.metrics()
        t[0] += 59
        c.metrics()
        self.assertEqual(len(calls), 1)
        t[0] += 2
        c.metrics()
        self.assertEqual(len(calls), 2)


class InstallTest(unittest.TestCase):
    """infra/files/monitoring_install.py: deployed_at kept for known checks, set for new ones"""

    def run_install(self, old_text):
        with tempfile.TemporaryDirectory() as d:
            old = Path(d) / "old.yml"
            if old_text is not None:
                old.write_text(old_text, encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=str(REPO / "src" / "ingest" / "src"))
            r = subprocess.run([sys.executable, str(REPO / "infra" / "files" / "monitoring_install.py"),
                                str(RELEASE_CONFIG), str(old), "2026-11-24T12:00:00+03:00"],
                               capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return yaml.safe_load(r.stdout)

    def test_first_and_second_install(self):
        first = self.run_install(None)
        self.assertTrue(all(c["deployed_at"] == "2026-11-24T12:00:00+03:00" for c in first["expected_checks"]))
        first["expected_checks"][0]["deployed_at"] = "2026-11-01T00:00:00+00:00"
        first["expected_checks"] = first["expected_checks"][:1]
        second = self.run_install(yaml.safe_dump(first))
        self.assertEqual(second["expected_checks"][0]["deployed_at"], "2026-11-01T00:00:00+00:00")
        self.assertEqual(second["expected_checks"][1]["deployed_at"], "2026-11-24T12:00:00+03:00")
        load_config(yaml.safe_dump(second))


if __name__ == "__main__":
    unittest.main()
