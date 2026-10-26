"""Tests of the DAG manifest (oss911_ingest.manifest) and of the run modes' usage rules.

Run from the repository root:  python3 src/ingest/tests/test_manifest.py -v
No database: build() is given the managed sources directly. The lock and the database read are exercised by
infra/playbooks/45-contracts.yml.
"""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
INGEST = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(INGEST, "src"))

from oss911_ingest import cli                                          # noqa: E402
from oss911_ingest.manifest import ManifestError, build, serialize, write_atomic   # noqa: E402

CDR_DEF = {"window": {"schedule": "*/5 * * * *", "max_span_hours": 24, "lookback_minutes": 15, "source_timezone": "UTC"}}
DAY_DEF = {"window": {"schedule": "0 * * * *", "grain": "day"}}


def managed(**over):
    m = {"icp_cdr_voice": {"contract_version": 3, "enabled": True, "definition": CDR_DEF},
         "icp_agent_day": {"contract_version": 1, "enabled": False, "definition": DAY_DEF}}
    m.update(over)
    return m


POOLS = {"icp_cdr_voice": "icp_cms", "icp_agent_day": "icp_cms"}


class BuildTest(unittest.TestCase):
    def test_all_managed_sources_listed_disabled_too(self):
        doc = build(managed(), POOLS)
        ids = [s["source_id"] for s in doc["sources"]]
        self.assertEqual(ids, ["icp_agent_day", "icp_cdr_voice"])     # sorted, deterministic
        day = doc["sources"][0]
        self.assertFalse(day["enabled"])                              # still listed: its DAG must stay
        self.assertEqual(day["pool"], "icp_cms")

    def test_fields(self):
        cdr = build(managed(), POOLS, {"icp_cdr_voice": "2026-10-25"})["sources"][1]
        self.assertEqual(cdr, {"source_id": "icp_cdr_voice", "contract_version": 3, "enabled": True,
                               "schedule": "*/5 * * * *", "pool": "icp_cms", "resweep": True,
                               "resweep_from": "2026-10-25", "timezone": "UTC"})

    def test_resweep_needs_time_window_and_date(self):
        doc = build(managed(), POOLS, {"icp_agent_day": "2026-10-25"})["sources"]
        self.assertFalse(doc[0]["resweep"])          # icp_agent_day: date given, but no time window
        self.assertFalse(doc[1]["resweep"])          # icp_cdr_voice: time window, but no date -- not live yet
        self.assertIsNone(doc[1]["resweep_from"])
        with self.assertRaises(ManifestError):
            build(managed(), POOLS, {"icp_cdr_voice": "25/10/2026"})

    def test_missing_pool_fails(self):
        with self.assertRaises(ManifestError) as ctx:
            build(managed(), {"icp_cdr_voice": "icp_cms"})
        self.assertIn("icp_agent_day: no pool", str(ctx.exception))

    def test_missing_schedule_fails(self):
        with self.assertRaises(ManifestError):
            build(managed(icp_cdr_voice={"contract_version": 3, "enabled": True, "definition": {"window": {}}}), POOLS)

    def test_extra_pool_entries_are_fine(self):
        build(managed(), dict(POOLS, retired_source="x"))

    def test_serialize_is_deterministic(self):
        self.assertEqual(serialize(build(managed(), POOLS)), serialize(build(dict(reversed(managed().items())), POOLS)))


class WriteTest(unittest.TestCase):
    def test_write_then_unchanged_then_changed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "dags.json")
            data = serialize(build(managed(), POOLS))
            self.assertTrue(write_atomic(path, data))
            self.assertFalse(write_atomic(path, data))                # same content: not rewritten
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)
            self.assertTrue(write_atomic(path, serialize({"sources": []})))
            self.assertEqual(json.loads(open(path, encoding="utf-8").read()), {"sources": []})
            self.assertEqual([f for f in os.listdir(d)], ["dags.json"])   # no temporary file left behind

    def test_failed_write_leaves_old_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "dags.json")
            write_atomic(path, b"old")
            os.chmod(d, 0o500)                                         # directory not writable: mkstemp fails
            try:
                if os.geteuid() != 0:
                    with self.assertRaises(OSError):
                        write_atomic(path, b"new")
            finally:
                os.chmod(d, 0o700)
            self.assertEqual(open(path, "rb").read(), b"old")


class RunUsageTest(unittest.TestCase):
    def code(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                return cli.main(list(argv))
            except SystemExit as exc:
                return exc.code

    def test_resweep_usage(self):
        self.assertEqual(self.code("run", "--source", "x", "--mode", "resweep"), 2)                      # no --day
        self.assertEqual(self.code("run", "--source", "x", "--mode", "resweep", "--day", "2026-10-01",
                                   "--window", "2026-10-01T00:00:00/2026-10-02T00:00:00"), 2)
        self.assertEqual(self.code("run", "--source", "x", "--mode", "resweep", "--day", "2026-10-01",
                                   "--start-at", "2026-10-01T00:00:00"), 2)
        self.assertEqual(self.code("run", "--source", "x", "--mode", "resweep", "--day", "2026-10-32"), 2)
        self.assertEqual(self.code("run", "--source", "x", "--mode", "resweep", "--day", "26-10-01"), 2)
        self.assertEqual(self.code("run", "--source", "x", "--day", "2026-10-01"), 2)               # --day only for resweep

    def test_manifest_bad_pools_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "pools.yml")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("pools:\n  a: x\n  a: y\n")                   # duplicate key
            self.assertEqual(self.code("dag-manifest", "--pools", p, "--out", os.path.join(d, "o.json")), 1)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("pools: [a, b]\n")
            self.assertEqual(self.code("dag-manifest", "--pools", p, "--out", os.path.join(d, "o.json")), 1)
            self.assertEqual(self.code("dag-manifest", "--pools", os.path.join(d, "none.yml"),
                                       "--out", os.path.join(d, "o.json")), 2)


if __name__ == "__main__":
    unittest.main()
