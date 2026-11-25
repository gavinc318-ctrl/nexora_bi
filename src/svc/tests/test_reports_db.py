"""GET /v1/reports and GET /v1/reports/{id} over a real database (OSS911_SVC_TEST_DSN, see conftest.py) holding the
report definitions of reports/ (46-metrics, change 0018). DD-102: visibility decided by the service layer, the same
403 for "not for you" and "no such report", audited; a report listed for a role runs for that role.
"""

from __future__ import annotations

import datetime as dt
import os

import psycopg
import pytest

SUP = dict(user="sup1", roles=("supervisor",))
CT = dict(user="ct101", roles=("call_taker",), agent_no="101")
DISP = dict(user="disp1", roles=("dispatcher",))
ALL_REPORTS = ["D-06", "R-01", "R-04", "R-05", "R-09", "R-21", "R-26"]


def get(client, token, who, path):
    return client.get(path, headers={"Authorization": token(**who)})


@pytest.fixture()
def audit_db():
    value = os.environ.get("OSS911_SVC_TEST_AUDIT_DSN")
    if not value:
        pytest.skip("OSS911_SVC_TEST_AUDIT_DSN is not set: cannot read the audit log back")
    with psycopg.connect(value) as conn:
        yield conn


def test_catalogue_by_role(client, token):
    ids = {name: [r["id"] for r in get(client, token, who, "/v1/reports").json()["reports"]]
           for name, who in (("sup", SUP), ("ct", CT), ("disp", DISP))}
    assert ids["sup"] == ALL_REPORTS
    assert ids["ct"] == ["R-04", "R-26"]
    assert ids["disp"] == []                       # an empty catalogue, not a refusal


def test_definition_carries_names_formats_and_params(client, token):
    r = get(client, token, SUP, "/v1/reports/R-09")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == "Service Level" and d["title_ar_state"] == "draft"
    assert d["queries"]["service"] == {"metrics": ["M-C05", "M-C06"], "dimensions": ["date", "queue"],
                                       "granularity": "day"}
    assert d["formats"] == {"M-C05": "percent", "M-C06": "duration"}     # one given, one by aggregation
    assert d["metrics"]["M-C05"]["params"] == {"x_seconds": 20}
    assert d["filters"] == ["queue"]


def test_role_limited_filter_is_left_out(client, token):
    assert get(client, token, SUP, "/v1/reports/R-04").json()["filters"] == ["agent"]
    assert get(client, token, CT, "/v1/reports/R-04").json()["filters"] == []


def test_arabic_falls_back_to_english_while_draft(client, token):
    en = get(client, token, SUP, "/v1/reports/R-26?locale=en").json()
    ar = get(client, token, SUP, "/v1/reports/R-26?locale=ar").json()
    assert ar["title"] == en["title"] and ar["source_note"] == en["source_note"]
    assert ar["metrics"]["M-R04"]["name"] != en["metrics"]["M-R04"]["name"]     # metric names come in Arabic


@pytest.mark.parametrize("who,path", [(CT, "/v1/reports/R-21"), (CT, "/v1/reports/R-77"), (DISP, "/v1/reports/R-04")])
def test_not_permitted_and_missing_answer_alike(client, token, who, path):
    r = get(client, token, who, path)
    assert r.status_code == 403
    assert r.json() == {"error": "report_not_permitted", "message": "no such report for this role"}


def test_refusal_is_audited(client, token, audit_db):
    who = dict(user="audit_report", roles=("call_taker",), agent_no="101")
    get(client, token, who, "/v1/reports/R-21")
    row = audit_db.execute("""SELECT role_code, object_ref, row_count FROM audit.access_log
                               WHERE user_principal = 'id-audit_report' ORDER BY access_id DESC LIMIT 1""").fetchone()
    assert row == ("call_taker", "/v1/reports/R-21", None)
    get(client, token, who, "/v1/reports/R-26")
    row = audit_db.execute("""SELECT object_ref, metric_codes, row_count FROM audit.access_log
                               WHERE user_principal = 'id-audit_report' ORDER BY access_id DESC LIMIT 1""").fetchone()
    assert row[0] == "/v1/reports/R-26" and "M-R04" in row[1] and row[2] == 1


@pytest.mark.parametrize("who", [SUP, CT, dict(user="adm1", roles=("admin",))])
def test_every_listed_report_runs_for_its_role(client, token, who):
    """The catalogue promise (DD-102 note): what a role sees listed, it can query -- every query of every report,
    with its dimensions, answers 200 (the row filter still narrows what comes back)."""
    end = dt.date.today() + dt.timedelta(days=1)
    rng = {"from": (end - dt.timedelta(days=7)).isoformat(), "to": end.isoformat()}
    for item in get(client, token, who, "/v1/reports").json()["reports"]:
        d = get(client, token, who, f"/v1/reports/{item['id']}").json()
        for name, q in d["queries"].items():
            r = client.post("/v1/query", json={"metrics": q["metrics"], "dimensions": q["dimensions"],
                                               "granularity": q["granularity"], "range": rng,
                                               "data_version": d["defaults"]["data_version"]},
                            headers={"Authorization": token(**who)})
            assert r.status_code == 200, f"{item['id']} {name}: {r.text}"
