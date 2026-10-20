"""Tests of CSV parsing, the content hash, the ICP CDR batch checks and window cutting.

Run from the repository root:  python3 ingest/tests/test_batch.py -v
No database and no network. The end-to-end path (fake ICP interface -> raw) is exercised against a database
as described in infra/runbook/10-ingest.md.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "ingest", "src"))

from oss911_ingest.cdr import check_cdr                       # noqa: E402
from oss911_ingest.config import ConfigError                  # noqa: E402
from oss911_ingest.contract import load_contract              # noqa: E402
from oss911_ingest.parse import content_hash, parse_csv       # noqa: E402
from oss911_ingest.runner import cut                          # noqa: E402

with open(os.path.join(REPO, "contracts", "if03a_icp_cdr.yaml"), encoding="utf-8") as fh:
    CONTRACT = load_contract(fh.read())
COLUMNS = [c["src"] for c in CONTRACT["columns"]]


def leg(**over) -> dict:
    row = {c: "" for c in COLUMNS}
    row.update({"CALLID": "1700000000-1", "CALLIDNUM": "1", "CALLERNO": "0500000000", "CALLEENO": "911",
                "WAITBEGIN": "2026-10-14 08:00:00", "WAITEND": "2026-10-14 08:00:10",
                "ACKBEGIN": "2026-10-14 08:00:10", "ACKEND": "2026-10-14 08:00:12",
                "CALLBEGIN": "2026-10-14 08:00:12", "CALLEND": "2026-10-14 08:03:00",
                "DEVICETYPE": "2", "DEVICENO": "101", "CALLTYPE": "0"})
    row.update(over)
    return row


def csv_bytes(rows, header=None) -> bytes:
    header = header or COLUMNS
    lines = [",".join(header)] + [",".join(r.get(c, "") for c in header) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def check(rows):
    return check_cdr(COLUMNS, [[r[c] for c in COLUMNS] for r in rows], CONTRACT)


class ParseTest(unittest.TestCase):
    def test_ok(self):
        p = parse_csv(csv_bytes([leg(), leg(CALLIDNUM="-1")]), COLUMNS)
        self.assertIsNone(p.unparseable)
        self.assertEqual(len(p.rows), 2)
        self.assertEqual(p.header_problems, [])
        self.assertEqual(p.size, len(csv_bytes([leg(), leg(CALLIDNUM="-1")])))

    def test_header_only_is_empty_result(self):
        p = parse_csv(csv_bytes([]), COLUMNS)
        self.assertIsNone(p.unparseable)
        self.assertEqual(p.rows, [])

    def test_quoted_comma(self):
        data = csv_bytes([leg()]).replace(b"0500000000", b'"web:https://x.invalid/a,b"')
        p = parse_csv(data, COLUMNS)
        self.assertIsNone(p.unparseable)
        self.assertEqual(p.rows[0][COLUMNS.index("CALLERNO")], "web:https://x.invalid/a,b")

    def test_unparseable(self):
        cases = {
            "wrong field count": csv_bytes([leg()]) + b"a,b,c\n",
            "unbalanced quote": csv_bytes([leg()]).replace(b"0500000000", b'"0500000000'),
            "not utf-8": csv_bytes([leg()]).replace(b"0500000000", b"\xff\xfe"),
            "empty file": b"",
            "repeated column": csv_bytes([], header=COLUMNS + ["CALLID"]),
        }
        for name, data in cases.items():
            p = parse_csv(data, COLUMNS)
            self.assertIsNotNone(p.unparseable, name)
            self.assertEqual(p.rows, [], name)

    def test_bom_is_a_header_problem(self):
        p = parse_csv(b"\xef\xbb\xbf" + csv_bytes([leg()]), COLUMNS)
        self.assertTrue(any("missing" in x for x in p.header_problems))

    def test_new_column(self):
        p = parse_csv(csv_bytes([leg(NEWCOL="x")], header=COLUMNS + ["NEWCOL"]), COLUMNS)
        self.assertEqual(p.unknown_columns, ["NEWCOL"])
        self.assertEqual(len(p.rows[0]), len(COLUMNS) + 1)

    def test_missing_and_reordered(self):
        p = parse_csv(csv_bytes([leg()], header=COLUMNS[:-1]), COLUMNS)
        self.assertTrue(any("missing" in x for x in p.header_problems))
        p = parse_csv(csv_bytes([leg()], header=list(reversed(COLUMNS))), COLUMNS)
        self.assertTrue(any("order" in x for x in p.header_problems))


class HashTest(unittest.TestCase):
    def test_names_and_order_count(self):
        h = content_hash(["A", "B"], ["1", "2"])
        self.assertEqual(len(h), 32)
        self.assertEqual(h, content_hash(["A", "B"], ["1", "2"]))
        self.assertNotEqual(h, content_hash(["B", "A"], ["2", "1"]))   # order received is part of it
        self.assertNotEqual(h, content_hash(["A", "C"], ["1", "2"]))   # names too
        self.assertNotEqual(h, content_hash(["A", "B", "C"], ["1", "2", ""]))   # a new column, even empty

    def test_no_separator_ambiguity(self):
        self.assertNotEqual(content_hash(["A", "B"], ["1,2", ""]), content_hash(["A", "B"], ["1", "2"]))


class CdrCheckTest(unittest.TestCase):
    def test_clean(self):
        r = check([leg(), leg(CALLIDNUM="-1", DEVICENO="102")])
        self.assertEqual(r.quarantine, [])
        self.assertEqual(r.unknown_values, {})

    def test_call_in_progress_is_fine(self):
        self.assertEqual(check([leg(CALLEND="")]).quarantine, [])   # no -1 leg yet, CALLEND still empty

    def assertQuarantined(self, rows, fragment):
        r = check(rows)
        self.assertTrue(any(fragment in q for q in r.quarantine), f"{fragment!r} not in {r.quarantine}")

    def test_rules(self):
        self.assertQuarantined([leg(WAITBEGIN="")], "WAITBEGIN empty")
        self.assertQuarantined([leg(), leg()], "repeated")
        self.assertQuarantined([leg(CALLIDNUM="-1"), leg(CALLIDNUM="-1", DEVICENO="9")], "repeated")
        self.assertQuarantined([leg(CALLID="")], "CALLID or CALLIDNUM empty")
        self.assertQuarantined([leg(CALLIDNUM="x")], "not an integer")
        self.assertQuarantined([leg(WAITEND="2026-10-14 07:59:59")], "WAITEND < WAITBEGIN")
        self.assertQuarantined([leg(CALLEND="2026-10-14 08:00:00")], "CALLEND < CALLBEGIN")
        self.assertQuarantined([leg(ACKEND="2026-10-14 08:00:09")], "ACKEND < ACKBEGIN")
        self.assertQuarantined([leg(CALLEND="2026-10-14 8:03:00")], "not in the format")

    def test_same_seq_in_different_calls_is_fine(self):
        self.assertEqual(check([leg(), leg(CALLID="1700000000-2")]).quarantine, [])

    def test_unknown_calltype_goes_to_clarification(self):
        r = check([leg(CALLTYPE="9999"), leg(CALLID="2", CALLTYPE="9999"), leg(CALLID="3", CALLTYPE="6")])
        self.assertEqual(r.quarantine, [])                        # not quarantined (contract batch_checks 5)
        self.assertEqual(dict(r.unknown_values["CALLTYPE"]), {"9999": 2})   # 6 is a known internal-call code

    def test_contract_without_value_table(self):
        defn = dict(CONTRACT, enums={})
        r = check_cdr(COLUMNS, [[leg()[c] for c in COLUMNS]], defn)
        self.assertTrue(any("enums.call_type.values" in q for q in r.quarantine))


class CutTest(unittest.TestCase):
    H, S5 = timedelta(hours=24), timedelta(seconds=5)
    T0 = datetime(2026, 10, 1)

    def test_one_slice(self):
        self.assertEqual(cut(self.T0, self.T0 + timedelta(minutes=20), self.H, self.S5),
                         [(self.T0, self.T0 + timedelta(minutes=20))])

    def test_contiguous_slices(self):
        out = cut(self.T0, self.T0 + timedelta(days=2, hours=6), self.H, self.S5)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0][0], self.T0)
        self.assertEqual(out[-1][1], self.T0 + timedelta(days=2, hours=6))
        for (a, b), (c, d) in zip(out, out[1:]):
            self.assertEqual(b, c)                                # no gap, no overlap
        self.assertTrue(all(b - a <= self.H for a, b in out))

    def test_short_last_slice_widened(self):
        out = cut(self.T0, self.T0 + self.H + timedelta(seconds=2), self.H, self.S5)
        self.assertEqual(out[-1][1] - out[-1][0], self.S5)        # never below the interface minimum
        self.assertEqual(out[-1][1], self.T0 + self.H + timedelta(seconds=2))

    def test_too_short(self):
        with self.assertRaises(ConfigError):
            cut(self.T0, self.T0 + timedelta(seconds=3), self.H, self.S5)


if __name__ == "__main__":
    unittest.main()
