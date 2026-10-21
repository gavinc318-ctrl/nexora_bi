"""Tests of the contract rules (oss911_ingest.contract) and of validate-contract's exit codes.

Run from the repository root:  python3 src/ingest/tests/test_contract.py -v
Needs only PyYAML; no database. The --with-overrides path reads the database and is exercised by
infra/playbooks/45-contracts.yml on the real machine.
"""

import copy
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
INGEST = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(INGEST))
sys.path.insert(0, os.path.join(INGEST, "src"))

from oss911_ingest import cli                                     # noqa: E402
from oss911_ingest.contract import (                              # noqa: E402
    ContractError, effective_lookback_minutes, load_contract, validate_contract, validate_overrides)

CDR = os.path.join(REPO, "contracts", "if03a_icp_cdr.yaml")


def cdr() -> dict:
    with open(CDR, encoding="utf-8") as fh:
        return load_contract(fh.read())


class LoadTest(unittest.TestCase):
    def test_duplicate_key_rejected(self):
        # PyYAML's default loader would silently keep 30
        with self.assertRaises(ContractError) as ctx:
            load_contract("window:\n  lookback_minutes: 15\n  lookback_minutes: 30\n")
        self.assertIn("duplicate key 'lookback_minutes'", str(ctx.exception))

    def test_duplicate_top_level_key_rejected(self):
        with self.assertRaises(ContractError):
            load_contract("contract_version: 1\ncontract_version: 2\n")

    def test_same_key_in_different_mappings_is_fine(self):
        doc = load_contract("a: {x: 1}\nb: {x: 2}\n")
        self.assertEqual(doc, {"a": {"x": 1}, "b": {"x": 2}})

    def test_not_a_mapping(self):
        with self.assertRaises(ContractError):
            load_contract("- just\n- a list\n")

    def test_python_tags_refused(self):
        with self.assertRaises(ContractError):
            load_contract("x: !!python/object/apply:os.system ['true']\n")


class ContractRulesTest(unittest.TestCase):
    def test_released_cdr_contract_is_valid(self):
        self.assertEqual(validate_contract(cdr()), [])

    def assertProblem(self, doc, fragment):
        problems = validate_contract(doc)
        self.assertTrue(any(fragment in p for p in problems), f"no problem containing {fragment!r} in {problems}")

    def test_version_must_be_positive_integer(self):
        for bad in (0, -1, "1", 1.5, True, None):
            doc = cdr()
            doc["contract_version"] = bad
            self.assertProblem(doc, "contract_version")

    def test_required_sections(self):
        for key, fragment in (("endpoint", "endpoint"), ("columns", "columns"), ("idempotent_key", "idempotent_key"),
                              ("watermark", "watermark"), ("window", "window"), ("auth", "auth")):
            doc = cdr()
            del doc[key]
            self.assertProblem(doc, fragment)

    def test_credential_ref_required_for_rest_api(self):
        doc = cdr()
        del doc["auth"]["obtain"]["credential_ref"]
        self.assertProblem(doc, "credential_ref")

    def test_auth_may_inherit(self):
        doc = cdr()
        doc["auth"] = {"inherits": "icp_cdr_voice"}
        self.assertEqual(validate_contract(doc), [])

    def test_reference_names_must_be_identifiers(self):
        doc = cdr()
        doc["endpoint"]["host_ref"] = "icp-gateway"      # would not map to an environment variable name
        self.assertProblem(doc, "endpoint.host_ref")

    def test_secret_looking_key_rejected(self):
        doc = cdr()
        doc["auth"]["obtain"]["password"] = "x"
        self.assertProblem(doc, "looks like a secret")

    def test_idempotent_key_must_name_columns(self):
        doc = cdr()
        doc["idempotent_key"] = ["CALLID", "CALL_ID_NUM"]
        self.assertProblem(doc, "['CALL_ID_NUM']")

    def test_duplicate_column(self):
        doc = cdr()
        doc["columns"].append(copy.deepcopy(doc["columns"][0]))
        self.assertProblem(doc, "more than once")

    def test_column_state(self):
        doc = cdr()
        doc["columns"][0]["state"] = "maybe"
        self.assertProblem(doc, "state must be")

    def test_window_ranges(self):
        cases = [("lookback_minutes", 0), ("lookback_minutes", 1441), ("lookback_minutes", None),
                 ("lookback_minutes", "15"), ("max_span_hours", 0), ("min_span_seconds", -5), ("concurrency", 0)]
        for key, bad in cases:
            doc = cdr()
            doc["window"][key] = bad
            self.assertProblem(doc, f"window.{key}")

    def test_min_span_must_be_shorter_than_max(self):
        doc = cdr()
        doc["window"]["min_span_seconds"] = 24 * 3600
        self.assertProblem(doc, "shorter than")

    def test_connector_and_target(self):
        doc = cdr()
        doc["connector"] = "ftp"
        doc["target_raw"] = "core.fact_call"
        self.assertProblem(doc, "connector")
        self.assertProblem(doc, "target_raw")


class OverridesTest(unittest.TestCase):
    def test_raise_lookback_ok(self):
        self.assertEqual(validate_overrides(cdr(), {"lookback_minutes": "30"}), [])
        self.assertEqual(validate_overrides(cdr(), {"lookback_minutes": "15"}), [])   # equal to the default

    def test_lower_lookback_refused(self):
        problems = validate_overrides(cdr(), {"lookback_minutes": "10"})
        self.assertEqual(len(problems), 1)
        self.assertIn("only be raised", problems[0])

    def test_new_default_above_existing_override(self):
        # A release raises the default to 45 while 30 is hot-tuned: the override now lowers it
        doc = cdr()
        doc["window"]["lookback_minutes"] = 45
        self.assertIn("only be raised", validate_overrides(doc, {"lookback_minutes": "30"})[0])

    def test_contract_without_the_setting(self):
        doc = cdr()
        del doc["window"]["lookback_minutes"]
        self.assertIn("no valid window.lookback_minutes", validate_overrides(doc, {"lookback_minutes": "30"})[0])

    def test_null_setting_in_contract(self):
        # jsonb null is not SQL NULL; here it is just "not an integer"
        doc = cdr()
        doc["window"]["lookback_minutes"] = None
        self.assertIn("no valid window.lookback_minutes", validate_overrides(doc, {"lookback_minutes": "30"})[0])

    def test_bad_values(self):
        for bad in ("0", "1441", "15m", "", " 30", "-30", "30.0"):
            self.assertTrue(validate_overrides(cdr(), {"lookback_minutes": bad}), bad)

    def test_unknown_parameter(self):
        for name in ("enabled", "interval_minutes", "batch_size"):
            self.assertIn("not a hot-tunable", validate_overrides(cdr(), {name: "1"})[0])

    def test_effective_lookback(self):
        self.assertEqual(effective_lookback_minutes(cdr(), {}), 15)
        self.assertEqual(effective_lookback_minutes(cdr(), {"lookback_minutes": "30"}), 30)


class CliTest(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:      # argparse usage errors
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_valid(self):
        code, out, _ = self.run_cli("validate-contract", CDR)
        self.assertEqual(code, 0)
        self.assertIn('"source_id": "icp_cdr_voice"', out)

    def test_overrides_json(self):
        self.assertEqual(self.run_cli("validate-contract", CDR, "--overrides", '{"lookback_minutes": "20"}')[0], 0)
        self.assertEqual(self.run_cli("validate-contract", CDR, "--overrides", '{"lookback_minutes": "5"}')[0], 1)

    def test_invalid_contract_exits_1(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
            fh.write("source_id: x\nsource_id: y\n")
        try:
            code, _, err = self.run_cli("validate-contract", fh.name)
        finally:
            os.unlink(fh.name)
        self.assertEqual(code, 1)
        self.assertIn("duplicate key", err)

    def test_usage_errors_exit_2(self):
        self.assertEqual(self.run_cli("validate-contract", CDR, "--source", "icp_cdr_voice")[0], 2)  # no --with-overrides
        self.assertEqual(self.run_cli("validate-contract", CDR, "--overrides", "{not json")[0], 2)
        self.assertEqual(self.run_cli("validate-contract", CDR, "--overrides", '{"lookback_minutes": 30}')[0], 2)
        self.assertEqual(self.run_cli("validate-contract", "/nonexistent.yaml")[0], 2)

    def test_run_usage_errors_exit_2(self):
        self.assertEqual(self.run_cli("run", "--source", "x", "--mode", "backfill")[0], 2)            # no --window
        self.assertEqual(self.run_cli("run", "--source", "x", "--window",
                                      "2026-10-01T00:00:00/2026-10-02T00:00:00")[0], 2)            # window is for backfill
        self.assertEqual(self.run_cli("run", "--source", "x", "--mode", "backfill", "--start-at", "2026-10-01T00:00:00",
                                      "--window", "2026-10-01T00:00:00/2026-10-02T00:00:00")[0], 2)
        self.assertEqual(self.run_cli("run", "--source", "x", "--start-at", "2026-10-01 00:00")[0], 2)
        self.assertEqual(self.run_cli("run", "--source", "x", "--mode", "backfill",
                                      "--window", "2026-10-02T00:00:00/2026-10-01T00:00:00")[0], 2)   # reversed

    def test_run_missing_config_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["OSS911_ETC"] = d
            try:
                code, _, err = self.run_cli("run", "--source", "icp_cdr_voice")
            finally:
                del os.environ["OSS911_ETC"]
        self.assertEqual(code, 2)
        self.assertIn("ingest.conf does not exist", err)

    def test_with_overrides_wrong_source(self):
        code, _, err = self.run_cli("validate-contract", CDR, "--with-overrides", "--source", "other")
        self.assertEqual(code, 1)
        self.assertIn("does not match", err)

    def test_with_overrides_missing_config_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["OSS911_ETC"] = d
            try:
                code, _, err = self.run_cli("validate-contract", CDR, "--with-overrides", "--source", "icp_cdr_voice")
            finally:
                del os.environ["OSS911_ETC"]
        self.assertEqual(code, 2)
        self.assertIn("ingest.conf does not exist", err)


if __name__ == "__main__":
    unittest.main()
