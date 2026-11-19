"""Tests of the agent metrics' batch checks (oss911_ingest.agent) and of contracts if03b / if03c's rules.

Run from the repository root:  python3 src/ingest/tests/test_agent.py -v
No database and no network. The end-to-end path (fake ICP hindex/agent -> raw.icp_agent_session / raw.icp_agent_day)
is exercised against a database as described in infra/runbook/20-agent-metrics.md.
"""

import copy
import os
import sys
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
INGEST = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(INGEST))
sys.path.insert(0, os.path.join(INGEST, "src"))

from oss911_ingest.agent import (                                 # noqa: E402
    AgentObservation, Problems, check_records, day_rows, is_integer_value, session_rows)
from oss911_ingest.contract import (                              # noqa: E402
    effective_midnight_skip_minutes, load_contract, validate_contract, validate_overrides)


def contract(name: str) -> dict:
    with open(os.path.join(REPO, "contracts", name), encoding="utf-8") as fh:
        return load_contract(fh.read())


SESSION = contract("if03b_icp_agent_session.yaml")
DAY = contract("if03c_icp_agent_day.yaml")
DAY_INDEXES = DAY["endpoint"]["index_ids"]

# The examples of the interface reference (p.936, p.1035): ids and values as strings, null for an unknown object
SESSION_EXAMPLE = [{"id": "291", "idxs": [{"id": "IDX_04_03_001", "val": [
    {"inTime": "2016-01-28 02:27:18", "outTime": "2016-01-28 02:27:18", "duration": "14399"}]}]}]


def day_record(aid="291", **values):
    vals = {i: "0" for i in DAY_INDEXES}
    vals.update(values)
    return {"id": aid, "idxs": [{"id": i, "val": v} for i, v in vals.items()]}


def check(result, requested=("291",)):
    problems = Problems()
    return check_records(result, set(requested), problems), problems


class ContractTest(unittest.TestCase):
    def test_released_contracts_are_valid(self):
        self.assertEqual(validate_contract(SESSION), [])
        self.assertEqual(validate_contract(DAY), [])

    def test_day_indexes_and_columns_are_one_set(self):
        doc = copy.deepcopy(DAY)
        doc["endpoint"]["index_ids"] = DAY_INDEXES[:-1]
        self.assertTrue(any("same set" in p for p in validate_contract(doc)))

    def test_session_asks_for_the_interval_index_only(self):
        doc = copy.deepcopy(SESSION)
        doc["endpoint"]["index_ids"] = ["IDX_04_03_001", "IDX_04_02_001"]
        self.assertTrue(any("IDX_04_03_001" in p for p in validate_contract(doc)))

    def test_agent_ids_come_from_the_reference_snapshots(self):
        doc = copy.deepcopy(DAY)
        doc["endpoint"]["objects"]["from"] = "core.dim_agent"
        doc["endpoint"]["objects"]["batch_size"] = 20001
        problems = validate_contract(doc)
        self.assertTrue(any("objects.from" in p for p in problems))
        self.assertTrue(any("batch_size" in p for p in problems))

    def test_midnight_skip_can_only_be_raised(self):
        self.assertEqual(validate_overrides(DAY, {"midnight_skip_minutes": "45"}), [])
        self.assertEqual(effective_midnight_skip_minutes(DAY, {"midnight_skip_minutes": "45"}), 45)
        self.assertTrue(validate_overrides(DAY, {"midnight_skip_minutes": "10"}))
        self.assertTrue(validate_overrides(SESSION, {"midnight_skip_minutes": "45"}))   # the contract has none

    def test_missing_day_timezone(self):
        doc = copy.deepcopy(DAY)
        del doc["window"]["day_timezone"]
        self.assertIn("window.day_timezone is required for icp_agent_day", validate_contract(doc))


class ValueTest(unittest.TestCase):
    def test_integers(self):
        for good in (None, "0", "10771", "10759.0", "-3", 7, 7.0):
            self.assertTrue(is_integer_value(good), good)
        for bad in ("10759.5", "", "1e3", "abc", True, 7.5, [], {}):
            self.assertFalse(is_integer_value(bad), bad)


class RecordsTest(unittest.TestCase):
    def test_document_example_is_clean(self):
        records, problems = check(SESSION_EXAMPLE)
        self.assertEqual(problems.reasons(), [])
        self.assertEqual(records[0][0], "291")

    def test_not_a_list(self):
        _, problems = check({"id": "291"})
        self.assertTrue(problems.reasons())

    def test_agent_not_asked_for_and_repeated(self):
        _, problems = check(SESSION_EXAMPLE * 2 + [{"id": 999, "idxs": []}])
        reasons = " ".join(problems.reasons())
        self.assertIn("repeated", reasons)
        self.assertIn("not asked for (1x, e.g. 999)", reasons)


class SessionTest(unittest.TestCase):
    def rows(self, result):
        records, problems = check(result)
        return session_rows(records, problems), problems.reasons()

    def test_document_example(self):
        rows, reasons = self.rows(SESSION_EXAMPLE)
        self.assertEqual(reasons, [])
        self.assertEqual((rows[0]["id"], rows[0]["intime"], rows[0]["duration"]), ("291", "2016-01-28 02:27:18", "14399"))
        self.assertEqual(rows[0]["payload"]["id"], "291")         # the agent is added to the interval

    def test_null_is_no_interval(self):
        rows, reasons = self.rows([{"id": "291", "idxs": [{"id": "IDX_04_03_001", "val": None}]}])
        self.assertEqual((rows, reasons), ([], []))

    def test_open_interval(self):
        result = copy.deepcopy(SESSION_EXAMPLE)
        result[0]["idxs"][0]["val"][0].update(outTime="", duration="600")
        rows, reasons = self.rows(result)
        self.assertEqual(reasons, [])
        self.assertEqual(rows[0]["outtime"], "")

    def test_bad_intervals(self):
        for field, value, fragment in (("outTime", "2016-01-28 01:00:00", "before inTime"),
                                       ("inTime", None, "inTime missing"),
                                       ("inTime", "2016-01-28T02:27:18", "inTime missing"),
                                       ("duration", "12.5", "duration not an integer")):
            result = copy.deepcopy(SESSION_EXAMPLE)
            result[0]["idxs"][0]["val"][0][field] = value
            _, reasons = self.rows(result)
            self.assertTrue(any(fragment in r for r in reasons), (field, value, reasons))

    def test_same_interval_twice(self):
        result = copy.deepcopy(SESSION_EXAMPLE)
        result[0]["idxs"][0]["val"].append(dict(result[0]["idxs"][0]["val"][0], duration="1"))
        _, reasons = self.rows(result)
        self.assertTrue(any("interval repeated" in r for r in reasons))

    def test_hash_follows_content(self):
        a, _ = self.rows(SESSION_EXAMPLE)
        result = copy.deepcopy(SESSION_EXAMPLE)
        result[0]["idxs"][0]["val"][0]["duration"] = "14400"
        b, _ = self.rows(result)
        self.assertNotEqual(a[0]["content_hash"], b[0]["content_hash"])


class DayTest(unittest.TestCase):
    def rows(self, result):
        records, problems = check(result)
        unknown = set()
        return day_rows(records, DAY_INDEXES, 20261119, problems, unknown), problems.reasons(), unknown

    def test_clean_with_null_and_n_point_zero(self):
        rows, reasons, unknown = self.rows([day_record(IDX_04_02_022="10759.0", IDX_04_02_003=None)])
        self.assertEqual((reasons, unknown), ([], set()))
        self.assertEqual(rows[0]["date_key"], 20261119)

    def test_bad_value_and_missing_index(self):
        rec = day_record(IDX_04_02_002="12.5")
        rec["idxs"] = [x for x in rec["idxs"] if x["id"] != "IDX_04_02_024"]
        _, reasons, _ = self.rows([rec])
        self.assertTrue(any("not an integer" in r for r in reasons))
        self.assertTrue(any("missing" in r and "IDX_04_02_024" in r for r in reasons))

    def test_index_not_asked_for_is_registered_not_quarantined(self):
        rec = day_record()
        rec["idxs"].append({"id": "IDX_04_02_005", "val": "4"})
        _, reasons, unknown = self.rows([rec])
        self.assertEqual((reasons, unknown), ([], {"IDX_04_02_005"}))

    def test_hash_ignores_index_order_not_values(self):
        a, _, _ = self.rows([day_record()])
        rec = day_record()
        rec["idxs"].reverse()
        b, _, _ = self.rows([rec])
        c, _, _ = self.rows([day_record(IDX_04_02_001="1")])
        self.assertEqual(a[0]["content_hash"], b[0]["content_hash"])
        self.assertNotEqual(a[0]["content_hash"], c[0]["content_hash"])


class MidnightTest(unittest.TestCase):
    """DD-98 item two: a day-total observation within midnight_skip_minutes after midnight in the day's zone is not
    made. The database clock cannot be moved for an end-to-end check, so the rule itself is tested here."""

    def obs(self, skip=30):
        o = object.__new__(AgentObservation)
        o.day_tz, o.skip = ZoneInfo("Asia/Riyadh"), skip
        return o

    def test_window_is_in_the_day_zone(self):
        o = self.obs()
        utc = timezone.utc
        self.assertTrue(o._too_early(datetime(2026, 11, 18, 21, 0, 0, tzinfo=utc)))     # 00:00 Riyadh
        self.assertTrue(o._too_early(datetime(2026, 11, 18, 21, 29, 59, tzinfo=utc)))   # 00:29:59
        self.assertFalse(o._too_early(datetime(2026, 11, 18, 21, 30, 0, tzinfo=utc)))   # 00:30
        self.assertFalse(o._too_early(datetime(2026, 11, 19, 0, 10, 0, tzinfo=utc)))    # 03:10, though 00:10 UTC
        self.assertTrue(self.obs(45)._too_early(datetime(2026, 11, 18, 21, 40, 0, tzinfo=utc)))


if __name__ == "__main__":
    unittest.main()
