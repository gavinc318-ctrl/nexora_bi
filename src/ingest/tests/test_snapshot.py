"""Tests of the version-gated snapshot ingestion (oss911_ingest.snapshot) and of contract if03d's rules.

Run from the repository root:  python3 src/ingest/tests/test_snapshot.py -v
No database and no network. The end-to-end path (fake ICP synwas -> raw.icp_reference) is exercised against a
database as described in infra/runbook/14-reference.md.
"""

import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
INGEST = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(INGEST))
sys.path.insert(0, os.path.join(INGEST, "src"))

from oss911_ingest.contract import (                              # noqa: E402
    effective_stale_days, load_contract, validate_contract, validate_overrides)
from oss911_ingest.icp import IcpError                            # noqa: E402
from oss911_ingest.snapshot import check_snapshot, parse_versions, record_hash   # noqa: E402

with open(os.path.join(REPO, "contracts", "if03d_icp_reference.yaml"), encoding="utf-8") as fh:
    REF = load_contract(fh.read())

# The example of the interface reference (p.933): an object, ids as numbers, times as epoch milliseconds
PROBE = {"ccId": 1, "version": 6, "startTime": 1453448050000, "endTime": 1453448054000, "status": 2,
         "clusterId": "1", "clusterName": "c", "vdnsVersion": [
             {"vdnId": 1, "version": 13, "startTime": 1453448050000, "endTime": 1453448054000, "status": 2}]}


class VersionsTest(unittest.TestCase):
    def test_document_example(self):
        cti, vdn = parse_versions(PROBE)
        self.assertEqual((cti.cc_id, cti.vdn_id, cti.version, cti.status), ("1", None, 6, 2))
        self.assertEqual((vdn.cc_id, vdn.vdn_id, vdn.version), ("1", "1", 13))
        self.assertEqual(vdn.end_time.year, 2016)

    def test_typed_as_the_table_says(self):
        # The table types result as an array and the times / status as strings: read either way
        doc = copy.deepcopy(PROBE)
        doc["ccId"], doc["status"], doc["endTime"] = "1", "2", "1453448054000"
        cti, _ = parse_versions([doc])
        self.assertEqual((cti.cc_id, cti.status), ("1", 2))
        self.assertIsNotNone(cti.end_time)

    def test_unreadable_time_is_unknown_not_guessed(self):
        doc = copy.deepcopy(PROBE)
        doc["endTime"] = "2016-01-22 07:34:14"
        self.assertIsNone(parse_versions(doc)[0].end_time)

    def test_bad_version_fails_the_run(self):
        for bad in ({"ccId": 1, "version": "six"}, {"ccId": 1, "version": True}, "x", []):
            with self.assertRaises(IcpError):
                parse_versions(bad)


class BatchCheckTest(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(check_snapshot([{"agentNo": "1"}, {"agentNo": "2"}], "agentNo", 2), ([], 0))

    def test_repeated_key_quarantines(self):
        problems, _ = check_snapshot([{"agentNo": "1"}, {"agentNo": "1"}], "agentNo", None)
        self.assertTrue(problems and "repeated" in problems[0])

    def test_keyless_quarantines(self):
        # DD-94 item fifteen: a snapshot with unidentifiable records is incomplete, all keyless as well
        problems, keyless = check_snapshot([{"agentNo": "1"}, {"agentNo": ""}, {"agentNo": None}, {"x": 1}], "agentNo", None)
        self.assertEqual(keyless, 3)
        self.assertTrue(any("snapshot incomplete: 3 row(s) without agentNo" in p for p in problems))
        self.assertTrue(check_snapshot([{"x": 1}], "agentNo", None)[0])

    def test_empty_after_non_empty_quarantines(self):
        self.assertTrue(check_snapshot([], "agentNo", 60)[0])
        self.assertEqual(check_snapshot([], "agentNo", None), ([], 0))     # first snapshot may be empty
        self.assertEqual(check_snapshot([], "agentNo", 0), ([], 0))

    def test_not_a_list(self):
        self.assertTrue(check_snapshot({"agentNo": "1"}, "agentNo", None)[0])
        self.assertTrue(check_snapshot(["x"], "agentNo", None)[0])

    def test_no_key_dataset(self):
        self.assertEqual(check_snapshot([{"a": 1}, {"a": 1}], None, None), ([], 0))

    def test_hash_ignores_key_order(self):
        self.assertEqual(record_hash({"a": 1, "b": "x"}), record_hash({"b": "x", "a": 1}))
        self.assertNotEqual(record_hash({"a": 1}), record_hash({"a": "1"}))


class ContractTest(unittest.TestCase):
    def problems(self, doc):
        return validate_contract(dict(doc, enabled=True))

    def test_released_contract_is_valid(self):
        self.assertEqual(self.problems(REF), [])

    def test_required(self):
        for section, leaf in (("endpoint", "version_probe"), ("endpoint", "datasets"), ("endpoint", "site_vdn"),
                              ("auth", "inherits"), ("window", "version_stale_days")):
            doc = copy.deepcopy(REF)
            del doc[section][leaf]
            self.assertTrue(any(f"{section}.{leaf}" in p for p in self.problems(doc)), leaf)

    def test_dataset_rules(self):
        cases = [
            ({"name": "nope"}, "name must be one of"),
            ({"scope": "x"}, "scope must be cti or vdn"),
            ({"path": "/cmsgateway/resource/synwas/agents"}, "needs {ccId} and {vdnId}"),
            ({"key": ""}, "key must be"),
        ]
        for change, fragment in cases:
            doc = copy.deepcopy(REF)
            doc["endpoint"]["datasets"][1].update(change)
            self.assertTrue(any(fragment in p for p in self.problems(doc)), change)
        doc = copy.deepcopy(REF)
        doc["endpoint"]["datasets"].append(copy.deepcopy(doc["endpoint"]["datasets"][1]))
        self.assertTrue(any("more than once" in p for p in self.problems(doc)))

    def test_site_vdn(self):
        for bad in ({"cc_id": 1, "vdn_id": "1"}, {"cc_id": "1", "vdn_id": "x"}):
            doc = copy.deepcopy(REF)
            doc["endpoint"]["site_vdn"] = bad
            self.assertTrue(any("endpoint.site_vdn." in p for p in self.problems(doc)), bad)

    def test_stale_days_override(self):
        self.assertEqual(validate_overrides(REF, {"version_stale_days": "3"}), [])     # lower: allowed
        self.assertEqual(validate_overrides(REF, {"version_stale_days": "30"}), [])    # higher: allowed
        self.assertTrue(validate_overrides(REF, {"version_stale_days": "0"}))
        self.assertTrue(validate_overrides(REF, {"version_stale_days": "x"}))
        self.assertTrue(validate_overrides(REF, {"lookback_minutes": "30"}))           # not this contract's setting
        self.assertEqual(effective_stale_days(REF, {}), 7)
        self.assertEqual(effective_stale_days(REF, {"version_stale_days": "3"}), 3)


if __name__ == "__main__":
    unittest.main()
