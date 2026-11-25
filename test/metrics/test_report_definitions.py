"""Checks of report definitions in infra/files/metrics_to_json.py (DD-102), against the repository's metrics.yaml and
marts.yaml. Each case changes one thing in a valid definition and expects the problem that names it.

Run: /opt/oss911/venv-ingest/bin/python -m unittest discover -s test/metrics
"""

import copy
import importlib.util
import os
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_spec = importlib.util.spec_from_file_location("metrics_to_json", os.path.join(ROOT, "infra/files/metrics_to_json.py"))
mtj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mtj)


def _load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class ReportDefinitionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dbt_vars = _load("src/dbt/dbt_project.yml").get("vars") or {}
        metrics, problems = mtj.convert(_load("metrics/metrics.yaml"), dbt_vars)
        assert not problems, problems
        cls.metrics = {m["code"]: m for m in metrics}
        cls.marts, problems = mtj.convert_marts(_load("metrics/marts.yaml"))
        assert not problems, problems
        cls.r04 = _load("reports/R-04.yaml")

    def check(self, doc, file_id="R-04"):
        return mtj.convert_report(doc, file_id, self.metrics, self.marts)

    def assertProblem(self, doc, fragment, file_id="R-04"):
        _, problems = self.check(doc, file_id)
        self.assertTrue(any(fragment in x for x in problems), f"no problem with {fragment!r} in {problems}")

    def changed(self, fn):
        doc = copy.deepcopy(self.r04)
        fn(doc)
        return doc

    def test_repository_reports_are_valid(self):
        out, problems = mtj.convert_reports(os.path.join(ROOT, "reports"), list(self.metrics.values()), self.marts)
        self.assertEqual(problems, [])
        self.assertIn("R-04", out)
        self.assertEqual(len(out["R-04"]["definition_sha256"]), 64)

    def test_id_must_be_the_file_name(self):
        self.assertProblem(self.r04, "id must be the file name", file_id="R-05")

    def test_role_beyond_the_table_is_refused(self):
        self.assertProblem(self.changed(lambda d: d["roles"].append("dispatcher")), "who may not read")

    def test_hour_table_is_not_for_call_takers(self):
        r21 = _load("reports/R-21.yaml")
        r21["roles"].insert(0, "call_taker")
        self.assertProblem(r21, "call_taker, who may not read mart.agents__day_hour__on_duty", file_id="R-21")

    def test_required_dimension_must_be_output(self):
        r05 = _load("reports/R-05.yaml")
        r05["queries"]["share"]["dimensions"] = []
        self.assertProblem(r05, "must be queried with released_by", file_id="R-05")

    def test_metrics_of_two_tables_in_one_query(self):
        self.assertProblem(self.changed(lambda d: d["queries"]["handling"]["metrics"].append("M-R10")),
                           "one query reads one mart table")

    def test_unpublished_or_unimplemented_metric(self):
        self.assertProblem(self.changed(lambda d: d["queries"]["handling"]["metrics"].append("M-R03")),
                           "M-R03 is not published")
        self.assertProblem(self.changed(lambda d: d["queries"]["handling"]["metrics"].append("M-R05")),
                           "M-R05 is not implemented")
        self.assertProblem(self.changed(lambda d: d["queries"]["handling"]["metrics"].append("M-Z99")),
                           "M-Z99 is not in metrics.yaml")

    def test_dimension_not_in_table(self):
        self.assertProblem(self.changed(lambda d: d["queries"]["handling"].update(dimensions=["queue"])),
                           "has no dimension(s) queue")

    def test_filter_roles_within_report(self):
        self.assertProblem(self.changed(lambda d: d["filters"][0].update(roles=["dispatcher"])),
                           "roles must be some of the report's roles")

    def test_chart_axes_must_be_the_query_dimensions(self):
        self.assertProblem(self.changed(lambda d: d["views"][1].update(x="date")), "must be exactly the query's")

    def test_chart_series_and_sort_from_the_query(self):
        self.assertProblem(self.changed(lambda d: d["views"][1].update(series=["M-C01"])), "series must be metrics")
        self.assertProblem(self.changed(lambda d: d["views"][1].update(sort={"by": "M-C01", "order": "desc"})),
                           "sort must be")

    def test_stack_only_for_bar(self):
        self.assertProblem(self.changed(lambda d: d["views"][1].update(chart="line")), "only for bar")

    def test_unused_query_and_unknown_key(self):
        self.assertProblem(self.changed(lambda d: d["queries"].update(extra={"metrics": ["M-C09"]})),
                           "query extra is not shown")
        self.assertProblem(self.changed(lambda d: d.update(colour="red")), "unknown key(s) colour")

    def test_table_queries_must_share_dimensions(self):
        r01 = _load("reports/R-01.yaml")
        r01["queries"]["wait"]["dimensions"] = []
        self.assertProblem(r01, "same dimensions and granularity", file_id="R-01")

    def test_interval_refresh_needs_latest(self):
        d06 = _load("reports/D-06.yaml")
        d06["defaults"]["data_version"] = "as_closed"
        self.assertProblem(d06, "interval refresh reads the provisional rows", file_id="D-06")

    def test_arabic_title_missing_only_as_draft(self):
        self.assertProblem(self.changed(lambda d: d.update(title_ar_state="confirmed")), "draft while title.ar is null")

    def test_format_for_a_metric_not_read(self):
        self.assertProblem(self.changed(lambda d: d["formats"].update({"M-C01": "integer"})), "which no query reads")


if __name__ == "__main__":
    unittest.main()
