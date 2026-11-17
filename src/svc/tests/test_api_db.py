"""The query API over a real database (OSS911_SVC_TEST_DSN, see conftest.py): the call marts, as the service's role.

Expected values are read from the mart tables directly in each test, so the tests hold on any data the fake ICP
produced. Covers DD-99's rules on the call data: TC-DISC-02 (a supervisor and a call taker get different rows and
fields), the refusals (each with its own code), version selection before filtering, one snapshot per request, and
the audit (who is recorded by sub).
"""

from __future__ import annotations

import psycopg
import pytest

SUP = dict(user="sup1", roles=("supervisor",))
CT = dict(user="ct101", roles=("call_taker",), agent_no="101")


@pytest.fixture()
def db(dsn):
    with psycopg.connect(dsn) as conn:
        yield conn


@pytest.fixture()
def closed_day(db):
    """A day of calls__day__volume closed with version 1, and a range around it."""
    row = db.execute("""SELECT max(date_key) FROM meta.mart_day_publication
                         WHERE mart_table = 'mart.calls__day__volume' AND close_version = 1""").fetchone()
    if row[0] is None:
        pytest.skip("no closed day in the test database")
    k = row[0]
    return f"{k // 10000:04d}-{k // 100 % 100:02d}-{k % 100:02d}", k


def q(client, token, who, **body):
    body.setdefault("range", {"from": "2026-10-01", "to": "2026-12-01"})
    return client.post("/v1/query", json=body, headers={"Authorization": token(**who)})


def next_day(iso):
    import datetime as dt
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=1)).isoformat()


# ---------------------------------------------------------------- results
def test_ratio_is_sum_over_sum(client, token, db, closed_day):
    day, k = closed_day
    r = q(client, token, SUP, metrics=["M-C01", "M-C04"], dimensions=["date"], range={"from": day, "to": next_day(day)})
    assert r.status_code == 200, r.text
    offered, answered = db.execute("""SELECT sum(offered_calls), sum(answered_calls) FROM mart.calls__day__volume
                                       WHERE date_key = %s AND close_version = (SELECT max(close_version)
                                             FROM mart.calls__day__volume WHERE date_key = %s)""", (k, k)).fetchone()
    row = r.json()["rows"][0]
    assert row["M-C01"] == offered and row["M-C04"] == pytest.approx(float(answered) / float(offered))
    meta = r.json()["meta"]
    assert meta["days"][0]["status"] == "published" and meta["publication_seq"] and meta["cache_key"]
    assert meta["metrics"][1]["versions"][0]["metric_version"] == 1


def test_week_sums_the_days(client, token):
    days = q(client, token, SUP, metrics=["M-C01"], dimensions=["date"], range={"from": "2026-11-01", "to": "2026-11-15"})
    weeks = q(client, token, SUP, metrics=["M-C01"], dimensions=["date"], granularity="week",
              range={"from": "2026-11-01", "to": "2026-11-15"})
    assert days.status_code == weeks.status_code == 200
    assert [w["date"] for w in weeks.json()["rows"]] == ["2026-11-01", "2026-11-08"]   # Sundays
    assert sum(w["M-C01"] for w in weeks.json()["rows"]) == sum(d["M-C01"] for d in days.json()["rows"])


def test_supervisor_sees_every_agent_call_taker_only_himself(client, token):        # TC-DISC-02, rows
    sup = q(client, token, SUP, metrics=["M-C09"], dimensions=["agent"])
    ct = q(client, token, CT, metrics=["M-C09"], dimensions=["agent"])
    assert sup.status_code == ct.status_code == 200, ct.text
    assert len(sup.json()["rows"]) > 1
    assert [r["agent"] for r in ct.json()["rows"]] == ["101"]
    assert ct.json()["meta"]["row_scope"] == "self:101"
    mine = [r for r in sup.json()["rows"] if r["agent"] == "101"]
    assert mine == ct.json()["rows"]


def test_agent_names_supervisor_only(client, token):                                   # TC-DISC-02, fields
    path = "/v1/dimensions/agent/members?metric=M-C09&from=2026-11-01&to=2026-11-16&attributes=agent_name_en"
    sup = client.get(path, headers={"Authorization": token(**SUP)})
    assert sup.status_code == 200 and sup.json()["members"][0]["agent_name_en"]
    ct = client.get(path, headers={"Authorization": token(**CT)})
    assert ct.status_code == 403 and ct.json()["error"] == "field_not_permitted"
    ct_plain = client.get(path.split("&attributes")[0], headers={"Authorization": token(**CT)})
    assert [m["member"] for m in ct_plain.json()["members"]] == ["101"]


def test_versions_chosen_before_filtering(client, token, db):
    row = db.execute("""SELECT date_key FROM meta.mart_day_publication
                         WHERE mart_table = 'mart.calls__day__repeat_failed' AND close_version = 2
                         ORDER BY date_key LIMIT 1""").fetchone()
    if row is None:
        pytest.skip("no corrected day")
    k = row[0]
    day = f"{k // 10000:04d}-{k // 100 % 100:02d}-{k % 100:02d}"
    rng = {"from": day, "to": next_day(day)}
    expect = {v: db.execute("""SELECT sum(repeat_failed_calls) FROM mart.calls__day__repeat_failed
                                WHERE date_key = %s AND close_version = %s""", (k, v)).fetchone()[0] for v in (1, 2)}
    latest = q(client, token, SUP, metrics=["M-C15"], dimensions=["date"], range=rng)
    closed = q(client, token, SUP, metrics=["M-C15"], dimensions=["date"], range=rng, data_version="as_closed")
    absent = q(client, token, SUP, metrics=["M-C15"], dimensions=["date"], range=rng, data_version=9)
    assert latest.json()["rows"][0]["M-C15"] == expect[2]
    assert closed.json()["rows"][0]["M-C15"] == expect[1]
    assert absent.json()["rows"] == [] and absent.json()["meta"]["days"][0]["status"] == "version_not_found"
    assert absent.json()["meta"]["empty_reason"] == "no_data"


def test_as_closed_lists_unclosed_days(client, token):
    r = q(client, token, SUP, metrics=["M-C01"], dimensions=["date"], data_version="as_closed",
          range={"from": "2026-11-10", "to": "2027-01-01"})
    assert r.status_code == 200
    meta = r.json()["meta"]
    assert meta["unclosed_dates"] or any(d["status"] == "not_built" for d in meta["days"])
    assert all(row["date"] not in meta["unclosed_dates"] for row in r.json()["rows"])


def test_meta_in_progress_and_unlinked(client, token):
    r = q(client, token, SUP, metrics=["M-C01"], dimensions=["date"], range={"from": "2026-10-01", "to": "2027-01-01"})
    assert isinstance(r.json()["meta"]["in_progress_calls"], int)
    r = q(client, token, CT, metrics=["M-C09"], dimensions=["agent"])
    assert r.json()["meta"]["has_unlinked"] is False      # the call taker's own rows only


# ---------------------------------------------------------------- refusals, each with its code
@pytest.mark.parametrize("who,body,status,code", [
    (CT, {"metrics": ["M-C01"]}, 403, "table_not_permitted"),
    (dict(user="disp1", roles=("dispatcher",)), {"metrics": ["M-C09"]}, 403, "table_not_permitted"),
    (dict(user="ct_noagent", roles=("call_taker",)), {"metrics": ["M-C09"]}, 403, "identity_unmapped"),
    (dict(user="ct_unknown", roles=("call_taker",), agent_no="999"), {"metrics": ["M-C09"]}, 403, "identity_unmapped"),
    (SUP, {"metrics": ["M-Z99"]}, 404, "metric_unknown"),
    (SUP, {"metrics": ["M-C08"]}, 404, "metric_not_implemented"),
    (SUP, {"metrics": [{"code": "M-C01", "metric_version": 5}]}, 404, "metric_version_unknown"),
    (SUP, {"metrics": ["M-C01", "M-C09"]}, 400, "incompatible_grain"),
    (SUP, {"metrics": ["M-C11"]}, 400, "dimension_invalid"),                       # released_by is required
    (SUP, {"metrics": ["M-C01"], "dimensions": ["queue"]}, 400, "dimension_invalid"),
    (SUP, {"metrics": ["M-C09"], "filters": {"agent_name_en": ["x"]}}, 400, "dimension_invalid"),
    (SUP, {"metrics": ["M-C01"], "granularity": "hour"}, 400, "request_invalid"),
])
def test_refusals(client, token, who, body, status, code):
    r = q(client, token, who, **body)
    assert (r.status_code, r.json()["error"]) == (status, code), r.text


def test_unauthenticated(client):
    r = client.post("/v1/query", json={"metrics": ["M-C01"], "range": {"from": "2026-11-01", "to": "2026-11-02"}})
    assert r.status_code == 401 and r.json()["error"] == "unauthenticated"


def test_call_taker_cannot_filter_on_another_agent(client, token):
    r = q(client, token, CT, metrics=["M-C09"], dimensions=["agent"], filters={"agent": ["102"]})
    assert r.status_code == 200 and r.json()["rows"] == []        # the self filter still applies: none of 102's rows


# ---------------------------------------------------------------- audit
@pytest.fixture()
def audit_db():
    """The service's role may only INSERT into the audit log (0002); reading it back needs another login:
    OSS911_SVC_TEST_AUDIT_DSN."""
    import os
    value = os.environ.get("OSS911_SVC_TEST_AUDIT_DSN")
    if not value:
        pytest.skip("OSS911_SVC_TEST_AUDIT_DSN is not set: cannot read the audit log back")
    with psycopg.connect(value) as conn:
        yield conn


def test_service_role_cannot_read_the_audit_log(db):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("SELECT 1 FROM audit.access_log LIMIT 1")


def test_every_request_is_audited(client, token, audit_db):
    db = audit_db
    def count():
        return db.execute("SELECT count(*) FROM audit.access_log WHERE user_principal = 'id-audit_probe'").fetchone()[0]
    before = count()
    ok = q(client, token, dict(user="audit_probe", roles=("supervisor",)), metrics=["M-C01"])
    refused = q(client, token, dict(user="audit_probe", roles=("call_taker",), agent_no="101"), metrics=["M-C01"])
    assert ok.status_code == 200 and refused.status_code == 403
    assert count() == before + 2
    row = db.execute("""SELECT user_display, role_code, metric_codes, row_count, client_kind, request_digest IS NOT NULL
                          FROM audit.access_log WHERE user_principal = 'id-audit_probe' AND role_code = 'supervisor'
                         ORDER BY access_id DESC LIMIT 1""").fetchone()
    assert row == ("audit_probe", "supervisor", ["M-C01"], len(ok.json()["rows"]), "portal", True)


def test_sensitive_fields_recorded_by_name(client, token, audit_db):
    db = audit_db
    client.get("/v1/dimensions/agent/members?metric=M-C09&attributes=agent_name_en",
               headers={"Authorization": token(user="audit_probe2", roles=("supervisor",))})
    row = db.execute("""SELECT sensitive_fields FROM audit.access_log WHERE user_principal = 'id-audit_probe2'
                         ORDER BY access_id DESC LIMIT 1""").fetchone()
    assert row == (["agent_name_en"],)


def last_audit(db, subject):
    return db.execute("""SELECT user_display, role_code, object_ref, metric_codes, row_count, request_digest
                           FROM audit.access_log WHERE user_principal = %s ORDER BY access_id DESC LIMIT 1""",
                      (subject,)).fetchone()


@pytest.mark.parametrize("roles,code", [(("offline_access",), "role_missing"),
                                        (("call_taker", "dispatcher"), "role_ambiguous")])
def test_role_refusals_are_audited_with_role_none(client, token, audit_db, roles, code):     # review item four
    r = q(client, token, dict(user="audit_role", roles=roles), metrics=["M-C01"])
    assert (r.status_code, r.json()["error"]) == (403, code)
    assert last_audit(audit_db, "id-audit_role")[:4] == ("audit_role", "none", "/v1/query", ["M-C01"])


def test_malformed_request_is_audited_without_its_values(client, token, audit_db):
    r = client.post("/v1/query", json={"metrics": ["M-C01"], "filters": {"agent": "secret-value"}},
                    headers={"Authorization": token(user="audit_bad", roles=("supervisor",))})
    assert (r.status_code, r.json()["error"]) == (400, "request_invalid")
    assert "secret-value" not in r.text
    assert last_audit(audit_db, "id-audit_bad") == ("audit_bad", "supervisor", "/v1/query", None, None, None)


def test_invalid_token_is_not_audited(client, token, audit_db):
    before = audit_db.execute("SELECT count(*) FROM audit.access_log WHERE user_principal = 'id-audit_forged'").fetchone()
    r = q(client, token, dict(user="audit_forged", roles=("supervisor",), aud="another-client"), metrics=["M-C01"])
    assert r.status_code == 401
    after = audit_db.execute("SELECT count(*) FROM audit.access_log WHERE user_principal = 'id-audit_forged'").fetchone()
    assert after == before


def test_department_map_is_matched_by_subject(client, token, audit_db):              # DD-99 note 2026-11-17
    """A row keyed by the user's subject applies; a row whose user name matches but whose subject is someone
    else's does not (a re-used user name inherits nothing). No current agency type exists in the test data, so a
    matched department shows as "not a current agency type code" -- which proves it was found."""
    audit_db.execute("""INSERT INTO meta.user_department_map (subject, username, agency_type_code, changed_by)
                        VALUES ('id-dept_probe', 'dept_probe', 'POL', 'test'),
                               ('id-former-holder', 'renamed_probe', 'POL', 'test')""")
    audit_db.commit()
    try:
        def dept_problems(user):
            me = client.get("/v1/me", headers={"Authorization": token(user=user, roles=("dispatcher",))}).json()
            return [p for p in me["problems"] if "department" in p]
        assert dept_problems("dept_probe") == ["department POL is not a current agency type code"]
        assert dept_problems("renamed_probe") == ["no department in the token and none mapped in "
                                                  "meta.user_department_map"]
    finally:
        audit_db.execute("DELETE FROM meta.user_department_map WHERE changed_by = 'test'")
        audit_db.commit()


# ---------------------------------------------------------------- one snapshot per request (review item one)
def test_one_snapshot_across_an_intraday_rewrite(client, token, audit_db, monkeypatch):
    """Between choosing the publications and reading the rows, the hourly publication rewrites the provisional
    day (delete, insert under a new publication_seq). The request still answers from the one it chose: never
    "published" and empty. Leaves the day as an hourly run would."""
    db = audit_db
    row = db.execute("""SELECT p.date_key FROM meta.mart_day_publication p
                         WHERE p.mart_table = 'mart.calls__day__volume' AND p.close_version = 0 AND p.row_count > 0
                           AND NOT EXISTS (SELECT 1 FROM meta.mart_day_publication c WHERE c.mart_table = p.mart_table
                                             AND c.date_key = p.date_key AND c.close_version >= 1)
                         ORDER BY 1 DESC LIMIT 1""").fetchone()
    if row is None:
        pytest.skip("no provisional day with rows")
    k = row[0]
    day = f"{k // 10000:04d}-{k // 100 % 100:02d}-{k % 100:02d}"

    from oss911_svc import query
    original = query.choose_publications

    def rewrite_after_choosing(conn, rule, req):
        out = original(conn, rule, req)
        seq = db.execute("""INSERT INTO meta.data_publication (kind, date_from, date_to, run_id)
                            VALUES ('intraday', %s, %s, 'snapshot-test') RETURNING publication_seq""",
                         (k, k)).fetchone()[0]
        db.execute("""CREATE TEMP TABLE rewrite ON COMMIT DROP AS
                      SELECT * FROM mart.calls__day__volume WHERE date_key = %s AND close_version = 0""", (k,))
        db.execute("DELETE FROM mart.calls__day__volume WHERE date_key = %s AND close_version = 0", (k,))
        db.execute("UPDATE rewrite SET publication_seq = %s", (seq,))
        db.execute("INSERT INTO mart.calls__day__volume SELECT * FROM rewrite")
        db.execute("""INSERT INTO meta.mart_day_publication (mart_table, date_key, close_version, publication_seq,
                                                             row_count)
                      SELECT 'mart.calls__day__volume', %s, 0, %s, count(*) FROM rewrite""", (k, seq))
        db.commit()
        return out
    monkeypatch.setattr(query, "choose_publications", rewrite_after_choosing)
    r = q(client, token, SUP, metrics=["M-C01"], dimensions=["date"], range={"from": day, "to": next_day(day)})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["days"][0]["status"] == "published"
    assert r.json()["rows"] and r.json()["rows"][0]["M-C01"] is not None


def test_audit_failure_returns_nothing(dsn, verifier, token):
    from fastapi.testclient import TestClient

    from oss911_svc.app import create_app

    def broken():
        raise psycopg.OperationalError("audit database unreachable")
    app = create_app(verifier=verifier, connect_analytic=lambda: psycopg.connect(dsn, prepare_threshold=None),
                     connect_audit=broken)
    r = TestClient(app).post("/v1/query", json={"metrics": ["M-C01"], "range": {"from": "2026-11-01", "to": "2026-11-02"}},
                             headers={"Authorization": token(**SUP)})
    assert r.status_code == 500 and r.json()["error"] == "audit_unavailable" and "rows" not in r.json()


# ---------------------------------------------------------------- dictionary and me
def test_metrics_and_me(client, token):
    m = client.get("/v1/metrics?locale=ar", headers={"Authorization": token(**CT)}).json()["metrics"]
    by = {x["code"]: x for x in m}
    assert by["M-C09"]["readable"] and not by["M-C01"]["readable"] and not by["M-C08"]["readable"]
    assert by["M-C15"]["versions"][-1]["implemented"]
    me = client.get("/v1/me", headers={"Authorization": token(**CT)}).json()
    assert (me["user"], me["subject"], me["role"], me["agent_no"]) == ("ct101", "id-ct101", "call_taker", "101")
    legs = next(t for t in me["tables"] if t["table"] == "mart.legs__day_agent__handling")
    assert legs["readable"] and legs["row_scope"] == "self:101" and legs["visible_sensitive_fields"] == []
    me2 = client.get("/v1/me", headers={"Authorization": token(user="ct_noagent", roles=("call_taker",))}).json()
    assert me2["agent_no"] is None and me2["problems"]
